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

DEFAULT_DB_PATH = REPO_ROOT / ".state" / "state.db"


# Return codes for :func:`complete_on_merge`. Stable strings so callers
# (pr_watch's merge-watch loop, tests) can branch without parsing logs.
RESULT_MERGED = "merged"        # PR was merged this call; DB updated.
RESULT_ALREADY = "already"      # PR was merged previously; DB already terminal.
RESULT_NOT_YET = "not_yet"      # PR is open / draft; nothing written.
RESULT_NO_RUN = "no_run"        # No matching run row; nothing written.


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
        row = conn.execute(
            "SELECT task_id FROM runs WHERE branch = ? LIMIT 1", (head_ref,)
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
    db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
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
    from tools.state_db.writer import StateWriter

    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_db = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new_db:
            apply_schema(conn)

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
            "SELECT r.task_id, r.status, r.pr_state, p.slug AS project_slug "
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
        if (run_row["status"] == "completed"
                and run_row["pr_state"] == "merged"
                and already_event is not None):
            return RESULT_ALREADY

        project_slug = run_row["project_slug"]

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
            w.update_run_status(
                resolved_task_id,
                "completed",
                completed_at=merged_at,
            )
            w.append_event(
                kind="pr_merged",
                actor="run_complete_on_merge",
                payload={
                    "pr": pr,
                    "repo": repo,
                    "pr_url": pr_url,
                    "merge_commit": commit_full,
                    "merged_at": merged_at,
                },
                run_task_id=resolved_task_id,
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
    # not_yet is not a failure — the caller polls; exit 0 in all "no-op"
    # cases keeps shell wrappers simple.
    return 0


if __name__ == "__main__":
    sys.exit(main())
