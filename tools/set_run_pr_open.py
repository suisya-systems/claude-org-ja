#!/usr/bin/env python3
"""PR-open back-fill helper (Issue #323).

Closes the auto-completion gap that surfaced on PR #321: when Secretary
runs ``gh pr create`` immediately after a worker reports done, the
freshly-created PR's ``url`` is not yet recorded on ``runs``. The
``-MergeWatch`` path of ``pr-watch.ps1`` later calls
``run_complete_on_merge --pr <N>``, which resolves ``task_id`` by
matching ``runs.pr_url`` (or ``runs.branch``); without a back-fill the
helper returns ``no_run`` and Secretary has to recover by hand.

This helper is the canonical action Secretary runs right after
``gh pr create`` succeeds. It:

1. shells ``gh pr view <PR> --json url,headRefName`` to get the PR URL
   and head branch (authoritative — what GitHub actually recorded);
2. opens a ``StateWriter.transaction()`` and calls
   :meth:`StateWriter.set_run_pr` to update ``runs.pr_url`` (and
   ``runs.branch`` re-asserted from gh's ``headRefName``).

Idempotent: a second invocation rewrites the same values and adds no
journal entry — back-fill is metadata, not a new lifecycle event.

Usage::

    python tools/set_run_pr_open.py --task-id <id> --pr <N> \\
        [--repo OWNER/REPO] [--db-path <path>]

Exit codes: 0 on success, 2 on gh / DB failure, 3 when the run row for
``task_id`` is missing (so Secretary sees the misalignment instead of a
silent no-op), 127 when the ``gh`` CLI is not installed.
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


def _ensure_gh_installed() -> None:
    if shutil.which("gh") is None:
        sys.stderr.write(
            "tools/set_run_pr_open.py: error: GitHub CLI (gh) not found "
            "in PATH.\n"
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
            "tools/set_run_pr_open.py: error: failed to auto-detect repo "
            f"via `gh repo view`: {exc.stderr.strip() or exc}\n"
        )
        sys.exit(2)
    try:
        return json.loads(result.stdout)["nameWithOwner"]
    except (json.JSONDecodeError, KeyError) as exc:
        sys.stderr.write(
            "tools/set_run_pr_open.py: error: unexpected `gh repo view` "
            f"output: {exc}\n"
        )
        sys.exit(2)


def fetch_pr_view(pr: int, repo: str) -> dict:
    """Return the parsed ``gh pr view`` payload (url + headRefName)."""
    proc = subprocess.run(
        [
            "gh", "pr", "view", str(pr),
            "--repo", repo,
            "--json", "url,headRefName",
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


# Stable result strings — callers (tests, future automation) can branch
# without parsing log lines.
RESULT_OK = "ok"
RESULT_NO_RUN = "no_run"


def set_run_pr_open(
    *,
    task_id: str,
    pr: int,
    repo: str,
    db_path: Optional[Path] = None,
    pr_view: Optional[dict] = None,
) -> str:
    """Back-fill ``runs.pr_url`` / ``runs.branch`` for ``task_id``.

    Returns :data:`RESULT_OK` on success or :data:`RESULT_NO_RUN` if the
    run row is missing (caller decides whether to abort).

    ``pr_view`` may be supplied by callers that already fetched the
    payload; otherwise it is fetched via :func:`fetch_pr_view`.
    """
    db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if pr_view is None:
        pr_view = fetch_pr_view(pr, repo)

    pr_url = (pr_view.get("url") or "").strip()
    head_ref = pr_view.get("headRefName") or None
    if not pr_url:
        raise RuntimeError(
            f"gh pr view {pr} returned no url field; refusing to write "
            "an empty pr_url."
        )

    from tools.state_db import apply_schema, connect
    from tools.state_db.writer import StateWriter

    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_db = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new_db:
            apply_schema(conn)

        row = conn.execute(
            "SELECT 1 FROM runs WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            sys.stderr.write(
                "tools/set_run_pr_open.py: warning: no run row for "
                f"task_id={task_id!r}; skipping back-fill.\n"
            )
            return RESULT_NO_RUN

        with StateWriter(conn).transaction() as w:
            w.set_run_pr(task_id, pr_url=pr_url, branch=head_ref)
        return RESULT_OK
    finally:
        conn.close()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/set_run_pr_open.py",
        description=(
            "Back-fill runs.pr_url (and runs.branch) right after Secretary "
            "creates the PR, so run_complete_on_merge can auto-resolve "
            "task_id on merge."
        ),
    )
    parser.add_argument("--task-id", required=True,
                        help="task_id of the run to back-fill")
    parser.add_argument("--pr", type=int, required=True,
                        help="pull request number")
    parser.add_argument("--repo", default=None,
                        help=("OWNER/REPO for cross-repo PRs (Issue #331); "
                              "auto-detected via gh repo view when omitted"))
    parser.add_argument("--db-path", default=None,
                        help=f"path to state.db (default: {DEFAULT_DB_PATH})")
    args = parser.parse_args(argv)

    if args.pr <= 0:
        parser.error("--pr must be a positive integer")
    if not args.task_id.strip():
        parser.error("--task-id must be non-empty")

    _ensure_gh_installed()
    repo = args.repo or _resolve_repo()

    try:
        result = set_run_pr_open(
            task_id=args.task_id,
            pr=args.pr,
            repo=repo,
            db_path=Path(args.db_path) if args.db_path else None,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"tools/set_run_pr_open.py: error: {exc}\n")
        return 2

    sys.stdout.write(
        f"set_run_pr_open: task_id={args.task_id} PR #{args.pr} {result}\n"
    )
    if result == RESULT_NO_RUN:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
