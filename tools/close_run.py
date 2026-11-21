#!/usr/bin/env python3
"""Close a finished run in one call (Issue 1298).

Usage::

    python3 tools/close_run.py --task <task_id> [--task <task_id> ...] [--no-pr] [--wait-pane SEC]

The secretary runs this after asking the dispatcher to close the worker pane
(``CLOSE_PANE``). For each task, in this order, reading recorded state only:

1. PR: a run with ``pr_url`` must be merged. ``runs.pr_state='merged'`` is
   enough (``pr_watch --merge-watch`` records it); otherwise
   ``run_complete_on_merge.complete_on_merge`` asks GitHub once and records the
   merge. Not merged -> blocked (exit 4). A run without ``pr_url`` (a
   design-only run handed to a build run, an audit, a task the human closed)
   needs ``--no-pr``; without it -> blocked (exit 4).
2. Pane: the latest ``worker_spawned`` must be followed by ``pane_closed`` /
   ``worker_closed`` (a parked run's pane was closed when it was parked).
   Still open -> blocked (exit 5), after waiting up to ``--wait-pane``
   seconds (default 0).
3. Worktree: when the worker dir is a linked git worktree, ``git worktree
   remove --force`` from its main checkout (``--force`` because of the
   untracked ``send_plan.json``). The branch is kept. A project dir (pattern A)
   or an ephemeral dir (pattern C) is kept.
4. One StateWriter transaction: ``runs.status='completed'`` (archives the
   worker-state file) and the ``worker_dirs`` row removed; then the pattern C
   ``CLAUDE.local.md`` cleanup.
5. Check: the run is not among ``run_complete_on_merge.merged_runs_left_open``.

Nothing is removed before 1 and 2 pass. A step that fails stops that task
(exit 6) with the steps already done; running the command again continues,
and a closed run reports ``closed`` again (exit 0).

Output: per task a last line ``CLOSE_RUN: <task> closed (...)`` or
``CLOSE_RUN: <task> blocked: <reason>``. Exit: 0 all closed, 2 usage / no
run, 4 PR not merged / no PR without ``--no-pr``, 5 pane open, 6 a step
failed. With several tasks the highest code wins.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.belt_slots import _pane  # noqa: E402
from tools.run_complete_on_merge import (  # noqa: E402
    RESULT_ALREADY,
    RESULT_MERGED,
    cleanup_pattern_c_local_md,
    complete_on_merge,
    merged_runs_left_open,
)
from tools.state_db import connect  # noqa: E402
from tools.state_db.discover import resolve_state_db_path  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402

_PR_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")


class Blocked(Exception):
    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code


def _git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", "-C", cwd, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _check_pr(db_path: Path, task: str, pr_url: Optional[str], pr_state: Optional[str],
              no_pr: bool) -> str:
    if not pr_url:
        if not no_pr:
            raise Blocked(4, "the run has no PR; pass --no-pr to close it without one")
        return "no PR"
    if pr_state == "merged":
        return "PR merged"
    m = _PR_URL.search(pr_url)
    if not m:
        raise Blocked(4, f"cannot read the repository from pr_url {pr_url!r}")
    try:
        result = complete_on_merge(pr=int(m.group(2)), repo=m.group(1), task_id=task,
                                   db_path=db_path)
    except (OSError, subprocess.SubprocessError, ValueError, SystemExit) as exc:
        raise Blocked(4, f"could not ask GitHub whether PR #{m.group(2)} merged: {exc}")
    if result not in (RESULT_MERGED, RESULT_ALREADY):
        raise Blocked(4, f"PR #{m.group(2)} is not merged ({result})")
    return "PR merged"


def _wait_pane(conn, task: str, wait: float) -> None:
    deadline = time.monotonic() + wait
    while _pane(conn, task)["open"]:
        if time.monotonic() >= deadline:
            raise Blocked(5, "the worker pane is not recorded as closed (no pane_closed after the"
                             " latest worker_spawned); send CLOSE_PANE and run this again")
        time.sleep(2)


def _remove_worktree(wdir: Optional[str]) -> str:
    if not wdir or not Path(wdir).is_dir():
        return "no worktree to remove"
    try:
        git_dir = _git(wdir, "rev-parse", "--path-format=absolute", "--git-dir")
        common = _git(wdir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except subprocess.CalledProcessError:
        return "dir kept (not a git checkout)"
    if Path(git_dir) == Path(common):
        return "dir kept (not a linked worktree)"
    main_checkout = str(Path(common).parent)
    try:
        _git(main_checkout, "worktree", "remove", "--force", wdir)
    except subprocess.CalledProcessError as exc:
        raise Blocked(6, f"git worktree remove failed: {exc.stderr.strip()}")
    return "worktree removed"


def close_one(db_path: Path, task: str, *, no_pr: bool, wait: float) -> tuple[int, str]:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT r.status, r.pr_url, r.pr_state, w.abs_path FROM runs r "
            "LEFT JOIN worker_dirs w ON w.id = r.worker_dir_id WHERE r.task_id = ?",
            (task,),
        ).fetchone()
        if row is None:
            return 2, f"blocked: no run {task!r}"
        status, pr_url, pr_state, wdir = row[0], row[1], row[2], row[3]
        if status == "completed" and wdir is None:
            return 0, "closed (already)"
        done = [_check_pr(db_path, task, pr_url, pr_state, no_pr)]
        _wait_pane(conn, task, wait)
        done.append("pane closed")
        done.append(_remove_worktree(wdir))
        with StateWriter(conn).transaction() as w:
            w.update_run_status(task, "completed")
            if wdir:
                w.remove_worker_dir(wdir)
        done.append("run completed")
        if wdir:
            # The org root is the directory holding .state/state.db.
            cleanup_pattern_c_local_md(conn, task_id=task,
                                       claude_org_root=db_path.resolve().parent.parent,
                                       worker_dir_abs=wdir)
    except Blocked as exc:
        return exc.code, f"blocked: {exc}"
    finally:
        conn.close()
    if any(t == task for t, _ in merged_runs_left_open(db_path)):
        return 6, f"blocked: still listed as a merged run left open after: {', '.join(done)}"
    return 0, f"closed ({', '.join(done)})"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools/close_run.py",
        description="Close finished runs: check merge and pane, remove the worktree,"
                    " mark the run completed.",
    )
    ap.add_argument("--task", action="append", required=True, help="task id (repeatable)")
    ap.add_argument("--no-pr", action="store_true",
                    help="close runs that have no PR (design-only, audit, closed by the human)")
    ap.add_argument("--wait-pane", type=float, default=0.0,
                    help="seconds to wait for pane_closed (default 0)")
    ap.add_argument("--db-path", type=Path, help="state.db override (default: discovery)")
    args = ap.parse_args(argv)

    db_path = resolve_state_db_path(args.db_path)
    worst = 0
    for task in args.task:
        code, line = close_one(db_path, task, no_pr=args.no_pr, wait=args.wait_pane)
        print(f"CLOSE_RUN: {task} {line}", flush=True)
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    sys.exit(main())
