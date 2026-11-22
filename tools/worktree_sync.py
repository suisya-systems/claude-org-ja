#!/usr/bin/env python3
"""Move a worker's worktree from outside the sandbox (Issue 1298).

Usage::

    python3 tools/worktree_sync.py --task T --new-branch B [--from REF]   # default REF: origin/main
    python3 tools/worktree_sync.py --task T --merge REF

The secretary runs this for the two moves a worker cannot finish inside its
sandbox (a checkout or merge stops halfway on sandbox-protected paths such as
``.claude/skills``), and that were done by hand:

* ``--new-branch``: a task's next PR. ``git fetch origin``, then
  ``git switch -c B REF`` in the worker dir, and in the same step the run's
  record moves to the new branch: ``runs.branch = B``, ``runs.pr_url`` and
  ``runs.pr_state`` cleared (the earlier PR stays in the events). Without this
  the record kept the earlier PR's branch and ``merged`` state.
* ``--merge``: catch up with a ref (``git fetch origin``, ``git merge
  --no-edit REF``). A conflict is undone (``merge --abort``) and its files are
  listed; nothing is left half-merged. A tree without a git identity merges as
  ``claude-org worktree_sync``.

Both refuse a worker dir with uncommitted changes to tracked files and leave
it untouched. Each move writes one ``worktree_synced`` event (task, action,
branch / ref, before, after).

``ff_only`` (used by ``tools/refresh_open_prs.py``) brings the worktree up to
``origin/<branch>`` with ``merge --ff-only``, or says why not (``dirty`` /
``diverged`` / ``error``).

Output: ``WORKTREE_SYNC: <task> synced|dirty|conflict|error ...``; exit 0 when
synced, 1 otherwise, 2 for usage / no run.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.state_db import connect  # noqa: E402
from tools.state_db.discover import resolve_state_db_path  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402

_FALLBACK_ID = {"GIT_AUTHOR_NAME": "claude-org worktree_sync", "GIT_AUTHOR_EMAIL": "worktree-sync@claude-org.invalid",
                "GIT_COMMITTER_NAME": "claude-org worktree_sync",
                "GIT_COMMITTER_EMAIL": "worktree-sync@claude-org.invalid"}


def git(wdir: str, *args: str, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", wdir, *args], capture_output=True, text=True, env=env)


def dirty(wdir: str) -> bool:
    return bool(git(wdir, "status", "--porcelain", "--untracked-files=no").stdout.strip())


def head(wdir: str) -> str:
    return git(wdir, "rev-parse", "HEAD").stdout.strip()


def ff_only(wdir: str, branch: str) -> str:
    """Bring the worktree to origin/<branch> by fast-forward; return synced / dirty / diverged / error."""
    if not wdir or not Path(wdir).is_dir():
        return "error"
    fetch = git(wdir, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    if fetch.returncode != 0:
        return "error"
    if dirty(wdir):
        return "dirty"
    return "synced" if git(wdir, "merge", "-q", "--ff-only", f"origin/{branch}").returncode == 0 else "diverged"


def _run(conn, task: str) -> tuple[str, Optional[str]]:
    row = conn.execute(
        "SELECT w.abs_path, r.branch FROM runs r LEFT JOIN worker_dirs w ON w.id = r.worker_dir_id"
        " WHERE r.task_id = ?", (task,)).fetchone()
    if row is None or not row[0] or not Path(row[0]).is_dir():
        raise LookupError(f"no run with a worker dir for {task!r}")
    return row[0], row[1]


def _record(conn, task: str, payload: dict, branch: Optional[str] = None) -> None:
    with StateWriter(conn).transaction() as w:
        if branch is not None:
            w.conn.execute("UPDATE runs SET branch = ?, pr_url = NULL, pr_state = NULL WHERE task_id = ?",
                           (branch, task))
        w.append_event(kind="worktree_synced", actor="secretary", payload={"task": task, **payload},
                       run_task_id=task)


def new_branch(conn, task: str, branch: str, from_ref: str) -> tuple[int, str]:
    wdir, _ = _run(conn, task)
    if dirty(wdir):
        return 1, "dirty (uncommitted changes to tracked files; nothing done)"
    fetch = git(wdir, "fetch", "-q", "origin")
    if fetch.returncode != 0:
        return 1, f"error (git fetch origin failed, nothing done: {fetch.stderr.strip()[:200]})"
    before = head(wdir)
    proc = git(wdir, "switch", "-q", "-c", branch, from_ref)
    if proc.returncode != 0:
        return 1, f"error (git switch -c {branch} {from_ref}: {proc.stderr.strip()})"
    after = head(wdir)
    _record(conn, task, {"action": "new_branch", "branch": branch, "from": from_ref, "before": before,
                         "after": after}, branch=branch)
    return 0, f"synced (new branch {branch} at {after[:12]} from {from_ref}; runs.branch updated, PR record cleared)"


def merge(conn, task: str, ref: str) -> tuple[int, str]:
    wdir, _ = _run(conn, task)
    if dirty(wdir):
        return 1, "dirty (uncommitted changes to tracked files; nothing done)"
    fetch = git(wdir, "fetch", "-q", "origin")
    if fetch.returncode != 0:
        return 1, f"error (git fetch origin failed, nothing done: {fetch.stderr.strip()[:200]})"
    before = head(wdir)
    env = None
    if not git(wdir, "config", "user.name").stdout.strip():
        env = {**os.environ, **_FALLBACK_ID}
    proc = git(wdir, "merge", "-q", "--no-edit", ref, env=env)
    if proc.returncode != 0:
        files = git(wdir, "diff", "--name-only", "--diff-filter=U").stdout.split()
        git(wdir, "merge", "--abort")
        if files:
            return 1, f"conflict (merge of {ref} undone; conflicting files: {', '.join(files)})"
        return 1, f"error (git merge {ref}: {proc.stderr.strip()})"
    after = head(wdir)
    _record(conn, task, {"action": "merge", "ref": ref, "before": before, "after": after})
    return 0, f"synced (merged {ref}: {before[:12]} -> {after[:12]})"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/worktree_sync.py",
                                 description="Cut a new branch or merge a ref in a worker's worktree, outside the sandbox.")
    ap.add_argument("--root", type=Path, help="org root; its .state/state.db is used (default: discovery)")
    ap.add_argument("--db-path", type=Path, help="state.db override (default: discovery)")
    ap.add_argument("--task", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--new-branch", metavar="BRANCH")
    g.add_argument("--merge", metavar="REF")
    ap.add_argument("--from", dest="from_ref", default="origin/main", help="start of --new-branch")
    args = ap.parse_args(argv)
    db = args.db_path or (args.root / ".state" / "state.db" if args.root else None)
    conn = connect(resolve_state_db_path(db))
    try:
        if args.new_branch:
            code, line = new_branch(conn, args.task, args.new_branch, args.from_ref)
        else:
            code, line = merge(conn, args.task, args.merge)
    except LookupError as exc:
        code, line = 2, f"error ({exc})"
    finally:
        conn.close()
    print(f"WORKTREE_SYNC: {args.task} {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
