#!/usr/bin/env python3
"""PR-merge auto-completion helper (Issue #317).

Given a PR number, resolves the merge metadata via ``gh pr view`` and,
if the PR is merged, runs the canonical ``StateWriter.transaction()``
to drive the run row to its terminal state:

* ``runs.status = 'completed'``
* ``runs.pr_state = 'merged'``
* ``runs.pr_url`` and ``runs.commit_short`` / ``runs.commit_full`` set
  from the PR view payload
* ``runs.completed_at`` set to the PR's ``mergedAt``
* one ``pr_merged`` event appended to the events table

The helper replaces the inline ``python -c`` block that used to live
in ``.claude/skills/org-delegate/SKILL.md`` Step 5 2b-ii. The legacy
hand-rolled snippet is preserved verbatim under
``docs/legacy/pr-merge-completion-manual.md`` for archaeology only.

Usage::

    python tools/run_complete_on_merge.py --pr <N> \\
        [--repo OWNER/REPO] [--task-id <id>] [--db-path <path>]

The helper is **idempotent**: running it twice for the same PR is a
no-op on the second call (no double event row, no status flip).

Also exported as :func:`complete_on_merge` for in-process callers
(e.g. ``tools/pr_watch.py``'s merge-watch loop).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.state_db.discover import resolve_state_db_path  # noqa: E402

# Issue #398: discovery-based default so worktree-cwd invocations target
# the main checkout's state.db rather than an empty `.worktrees/<task>/.state/`.
DEFAULT_DB_PATH = resolve_state_db_path()


# Return codes for :func:`complete_on_merge`. Stable strings so callers
# (pr_watch's merge-watch loop, tests) can branch without parsing logs.
RESULT_MERGED = "merged"        # PR was merged this call; PR metadata recorded.
RESULT_ALREADY = "already"      # PR was merged previously; DB already has metadata + event.
RESULT_NOT_YET = "not_yet"      # PR is open / draft; nothing written.
RESULT_NO_RUN = "no_run"        # No matching run row; nothing written.
# Codex review (rounds 1-3): the helper records the merge fact
# (pr_state='merged', commit, completed_at, pr_merged event) but never
# flips runs.status itself. The status transition is gated on the
# secretary running worktree remove / CLOSE_PANE / remove_worker_dir
# and on the dispatcher closing the pane / writing the worker-state
# final update (delegation-lifecycle-contract §T5, state-schema-contract
# §3.1). Owning all of that from a one-shot subprocess is out of scope.
# RESULT_MERGED_PENDING_CLEANUP is kept as an alias for
# back-compat with callers that imported the symbol.
RESULT_MERGED_PENDING_CLEANUP = RESULT_MERGED


# --- Pattern C (gitignored_repo_root) post-completion cleanup (Issue #478) --
# The brief filename the secretary writes into a claude-org self-edit worker's
# dir (per .claude/skills/org-delegate/references/claude-org-self-edit.md §2).
# For Pattern B (live_repo_worktree) the worktree removal at close reclaims it;
# for Pattern C gitignored_repo_root the worker_dir *is* the claude-org repo
# root, so there is no directory to remove and the brief must be deleted
# file-by-file or it lingers and overwrites the secretary's role identity on
# the next /org-start.
PATTERN_C_BRIEF_FILENAME = "CLAUDE.local.md"

# Return codes for :func:`cleanup_pattern_c_local_md`.
CLEANUP_REMOVED = "removed"          # file existed and was deleted; event mode=auto.
CLEANUP_ABSENT = "absent"            # Pattern C @ root but file already gone; event mode=skip.
CLEANUP_NOT_APPLICABLE = "not_applicable"  # not Pattern C, or worker_dir != root; no event.


def cleanup_pattern_c_local_md(
    conn,
    *,
    task_id: str,
    claude_org_root: Path,
    worker_dir_abs: Optional[str] = None,
) -> str:
    """Remove a leftover ``CLAUDE.local.md`` from the claude-org repo root
    for a Pattern C ``gitignored_repo_root`` self-edit run (Issue #478).

    Detection (design judgment 1 of Issue #478): ``runs.pattern == 'C'`` AND
    the run's worker_dir equals ``claude_org_root``. This needs no schema
    change — Pattern C *ephemeral* has ``worker_dir`` at
    ``{workers_dir}/{task_id}`` (≠ root), so it is reclaimed by ordinary dir
    removal and naturally falls through here as ``not_applicable``.

    The worker_dir is taken from the explicit ``worker_dir_abs`` argument when
    provided, otherwise from the live ``runs.worker_dir_id`` → ``worker_dirs``
    join. Issue #486: the close-phase runbook calls
    ``StateWriter.remove_worker_dir()`` which DELETEs the ``worker_dirs`` row,
    and ``runs.worker_dir_id`` is ``ON DELETE SET NULL`` (schema.sql:84), so a
    cleanup invoked *after* the removal would see ``abs_path = NULL`` through
    the join and no-op even for a genuine gitignored_repo_root run. Callers
    that are about to (or just did) drop the worker_dirs row must pass the
    path they hold via ``worker_dir_abs`` so detection stays order-independent.
    The in-process PR-merge path (:func:`complete_on_merge`) leaves the row in
    place and so relies on the join fallback.

    Idempotent: ``Path.unlink(missing_ok=True)`` never raises on a missing
    file, and a re-call after the brief is gone returns
    :data:`CLEANUP_ABSENT`. An audit row (``kind='pattern_c_cleanup'``) is
    appended whenever the run qualifies — ``mode='auto'`` when the file was
    actually removed, ``mode='skip'`` when it was already absent — so the
    hook firing is observable in the events table. Non-qualifying runs
    (Pattern A/B, or Pattern C ephemeral) write nothing.

    Returns one of :data:`CLEANUP_REMOVED`, :data:`CLEANUP_ABSENT`,
    :data:`CLEANUP_NOT_APPLICABLE`.
    """
    row = conn.execute(
        "SELECT r.pattern, d.abs_path "
        "FROM runs r LEFT JOIN worker_dirs d ON d.id = r.worker_dir_id "
        "WHERE r.task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return CLEANUP_NOT_APPLICABLE
    pattern = (row["pattern"] or "").upper()
    # Prefer the caller-supplied path: it survives a prior remove_worker_dir()
    # that NULLs the join. Fall back to the live join when omitted.
    worker_dir = worker_dir_abs if worker_dir_abs is not None else row["abs_path"]
    if pattern != "C" or not worker_dir:
        return CLEANUP_NOT_APPLICABLE

    root = Path(claude_org_root).resolve()
    if Path(worker_dir).resolve() != root:
        # Pattern C ephemeral: worker_dir is {workers_dir}/{task_id}, a
        # disposable dir whose CLAUDE.md is reclaimed by dir removal.
        return CLEANUP_NOT_APPLICABLE

    target = root / PATTERN_C_BRIEF_FILENAME
    existed = target.exists()
    target.unlink(missing_ok=True)  # OS-level idempotent

    from tools.state_db.writer import StateWriter
    with StateWriter(conn).transaction() as w:
        w.append_event(
            kind="pattern_c_cleanup",
            actor="run_complete_on_merge",
            payload={
                "task": task_id,
                "removed_path": str(target),
                "mode": "auto" if existed else "skip",
            },
            run_task_id=task_id,
        )
    return CLEANUP_REMOVED if existed else CLEANUP_ABSENT


def _ensure_gh_installed() -> None:
    if shutil.which("gh") is None:
        sys.stderr.write(
            "tools/run_complete_on_merge.py: error: GitHub CLI (gh) not "
            "found in PATH.\n"
        )
        sys.exit(127)


def _resolve_repo() -> str:
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(
            "tools/run_complete_on_merge.py: error: failed to auto-detect "
            f"repo via `gh repo view`: {exc.stderr.strip() or exc}\n"
        )
        sys.exit(2)
    try:
        return json.loads(result.stdout)["nameWithOwner"]
    except (json.JSONDecodeError, KeyError) as exc:
        sys.stderr.write(
            "tools/run_complete_on_merge.py: error: unexpected `gh repo "
            f"view` output: {exc}\n"
        )
        sys.exit(2)


def fetch_pr_view(pr: int, repo: str) -> dict:
    """Return the parsed `gh pr view` payload for the given PR.

    Fields requested: ``number,url,state,mergedAt,mergeCommit,headRefName``.
    Raises ``RuntimeError`` if gh fails or the JSON is unparseable —
    callers (CLI / merge-watch) are expected to surface that and retry
    or exit.
    """
    proc = subprocess.run(
        [
            "gh", "pr", "view", str(pr),
            "--repo", repo,
            "--json", "number,url,state,mergedAt,mergeCommit,headRefName",
        ],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh pr view {pr} (repo={repo}) failed: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"gh pr view {pr} returned unparseable JSON: {exc}"
        ) from None
    if not isinstance(data, dict):
        raise RuntimeError(
            f"gh pr view {pr} returned non-object JSON: {type(data).__name__}"
        )
    return data


def _resolve_task_id(conn, *, pr: int, repo: str, pr_url: str,
                     head_ref: Optional[str]) -> Optional[str]:
    """Look up the task_id for a PR. Tries ``runs.pr_url`` then ``runs.branch``.

    Returns None if no candidate row exists; the caller should warn and
    exit with :data:`RESULT_NO_RUN`. We do not invent a row — the helper's
    contract is to drive an *existing* run to terminal, not to retroactively
    create state.
    """
    if pr_url:
        row = conn.execute(
            "SELECT task_id FROM runs WHERE pr_url = ? LIMIT 1", (pr_url,)
        ).fetchone()
        if row is not None:
            return row["task_id"]
        # gh sometimes returns a URL with trailing slash variations; try
        # a substring match on the canonical /pull/<n> tail.
        tail = f"/{repo}/pull/{pr}"
        row = conn.execute(
            "SELECT task_id FROM runs WHERE pr_url LIKE ? LIMIT 1",
            (f"%{tail}",),
        ).fetchone()
        if row is not None:
            return row["task_id"]
    if head_ref:
        # Codex Major: scope branch fallback to non-terminal runs so a
        # past completed run with the same branch can't be mistakenly
        # re-completed and have a stray pr_merged event appended.
        row = conn.execute(
            "SELECT task_id FROM runs WHERE branch = ? "
            "AND status NOT IN ('completed','failed','abandoned') "
            "ORDER BY id DESC LIMIT 1",
            (head_ref,),
        ).fetchone()
        if row is not None:
            return row["task_id"]
    return None


def _short_sha(full: Optional[str]) -> Optional[str]:
    if not full:
        return None
    return full[:7]


def complete_on_merge(
    *,
    pr: int,
    repo: str,
    task_id: Optional[str] = None,
    db_path: Optional[Path] = None,
    pr_view: Optional[dict] = None,
) -> str:
    """Drive the run for ``pr`` to its terminal merged state.

    Returns one of :data:`RESULT_MERGED`, :data:`RESULT_ALREADY`,
    :data:`RESULT_NOT_YET`, :data:`RESULT_NO_RUN`.

    ``pr_view`` may be supplied by callers that already fetched the
    payload (e.g. pr_watch's merge-watch loop); when omitted the helper
    fetches it via :func:`fetch_pr_view`.
    """
    db_path = (
        resolve_state_db_path(Path(db_path)) if db_path is not None
        else resolve_state_db_path()
    )
    if pr_view is None:
        pr_view = fetch_pr_view(pr, repo)

    merged_at = pr_view.get("mergedAt")
    if not merged_at:
        return RESULT_NOT_YET

    pr_url = pr_view.get("url") or ""
    head_ref = pr_view.get("headRefName")
    merge_commit = pr_view.get("mergeCommit") or {}
    commit_full = (merge_commit.get("oid") if isinstance(merge_commit, dict)
                   else None)
    commit_short = _short_sha(commit_full)

    from tools.state_db import apply_schema, connect
    from tools.state_db.discover import verify_or_exit
    from tools.state_db.writer import StateWriter

    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_db = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new_db:
            apply_schema(conn)
        else:
            verify_or_exit(
                db_path, conn=conn, prog="tools/run_complete_on_merge.py",
            )

        resolved_task_id = task_id or _resolve_task_id(
            conn, pr=pr, repo=repo, pr_url=pr_url, head_ref=head_ref,
        )
        if resolved_task_id is None:
            sys.stderr.write(
                "tools/run_complete_on_merge.py: warning: no run row "
                f"matches PR #{pr} (pr_url={pr_url!r}, head_ref="
                f"{head_ref!r}); skipping.\n"
            )
            return RESULT_NO_RUN

        run_row = conn.execute(
            "SELECT r.task_id, r.status, r.pr_state, r.pattern, "
            "p.slug AS project_slug "
            "FROM runs r JOIN projects p ON p.id = r.project_id "
            "WHERE r.task_id = ?",
            (resolved_task_id,),
        ).fetchone()
        if run_row is None:
            sys.stderr.write(
                "tools/run_complete_on_merge.py: warning: task_id "
                f"{resolved_task_id!r} resolved but row vanished; skipping.\n"
            )
            return RESULT_NO_RUN

        already_event = conn.execute(
            "SELECT 1 FROM events e JOIN runs r ON r.id = e.run_id "
            "WHERE r.task_id = ? AND e.kind = 'pr_merged' LIMIT 1",
            (resolved_task_id,),
        ).fetchone()
        if (run_row["pr_state"] == "merged"
                and already_event is not None):
            # Either the run is fully completed (Pattern A auto-close
            # path) or it has been left in 'review' awaiting secretary
            # cleanup (Pattern B/C/D). Either way, we already wrote
            # the PR metadata and the event row; do not double-write.
            return RESULT_ALREADY

        project_slug = run_row["project_slug"]
        pattern = (run_row["pattern"] or "B").upper()
        # Codex round 3 Blocker: even Pattern A close requires
        # dispatcher-side pane close / worker_closed / worker-state
        # final update (delegation-lifecycle-contract §T5). A
        # subprocess helper cannot orchestrate those, so the helper
        # records the merge fact (pr_state, commit, completed_at,
        # pr_merged event) and leaves the runs.status flip to the
        # secretary's manual cleanup step in 2b-ii.

        # claude_org_root=None lets StateWriter auto-detect from the
        # connection's database file (`<root>/.state/state.db`). That
        # keeps the post-commit snapshot regen pointed at the same
        # repo as the DB we just wrote to — important when pr_watch
        # invokes us from a worker dir, and important for tests that
        # use a temp state.db path.
        with StateWriter(conn).transaction() as w:
            w.upsert_run(
                task_id=resolved_task_id,
                project_slug=project_slug,
                pr_url=pr_url or None,
                pr_state="merged",
                commit_short=commit_short,
                commit_full=commit_full,
            )
            # Always record completed_at without flipping runs.status —
            # secretary owns the status transition (see comment above).
            w.conn.execute(
                "UPDATE runs SET completed_at = COALESCE(completed_at, ?) "
                "WHERE task_id = ?",
                (merged_at, resolved_task_id),
            )
            w.append_event(
                kind="pr_merged",
                actor="run_complete_on_merge",
                payload={
                    # `task` matches docs/journal-events.md §PR / push.
                    "task": resolved_task_id,
                    "pr": pr,
                    "repo": repo,
                    "pr_url": pr_url,
                    "merge_commit": commit_full,
                    "merged_at": merged_at,
                    "pattern": pattern,
                    "auto_completed": False,
                },
                run_task_id=resolved_task_id,
            )
        sys.stderr.write(
            "tools/run_complete_on_merge.py: notice: PR merged for "
            f"task {resolved_task_id} (pattern {pattern}); pr_state set "
            "to 'merged' and completed_at recorded, but runs.status "
            "left untouched — secretary must complete worktree remove / "
            "CLOSE_PANE / remove_worker_dir and call "
            "update_run_status('<task>', 'completed') (Step 5 2b-ii / "
            "delegation-lifecycle-contract §T5).\n"
        )
        # Issue #478: a Pattern C gitignored_repo_root self-edit run keeps
        # its CLAUDE.local.md inside the claude-org repo root (worker_dir ==
        # root), so the worktree-remove path the secretary runs at close has
        # no directory to reclaim it. Delete the brief here while we have the
        # connection. No-op for every other pattern/variant. The non-PR
        # close path (gitignored tasks rarely produce a merged PR) must call
        # this helper from the runbook too — see org-pull-request 2b-ii.
        cleanup_pattern_c_local_md(
            conn,
            task_id=resolved_task_id,
            claude_org_root=db_path.parent.parent,
        )
        return RESULT_MERGED
    finally:
        conn.close()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/run_complete_on_merge.py",
        description=(
            "Mark a run completed when its PR has been merged on GitHub."
        ),
    )
    parser.add_argument("--pr", type=int, required=True,
                        help="pull request number")
    parser.add_argument("--repo", default=None,
                        help="OWNER/REPO; auto-detected via gh repo view")
    parser.add_argument("--task-id", default=None,
                        help="task_id of the run to complete; auto-resolved "
                             "from runs.pr_url / runs.branch when omitted")
    parser.add_argument("--db-path", default=None,
                        help=f"path to state.db (default: {DEFAULT_DB_PATH})")
    args = parser.parse_args(argv)

    if args.pr <= 0:
        parser.error("--pr must be a positive integer")

    _ensure_gh_installed()
    repo = args.repo or _resolve_repo()

    try:
        result = complete_on_merge(
            pr=args.pr,
            repo=repo,
            task_id=args.task_id,
            db_path=Path(args.db_path) if args.db_path else None,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"tools/run_complete_on_merge.py: error: {exc}\n")
        return 2

    sys.stdout.write(f"run_complete_on_merge: PR #{args.pr} {result}\n")
    # not_yet is not a failure — the caller polls. Codex round-3 Major:
    # no_run IS a failure (PR merged but no run row matched), so the
    # secretary's manual invocation must see a non-zero exit code.
    if result == RESULT_NO_RUN:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
