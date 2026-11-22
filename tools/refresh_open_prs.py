#!/usr/bin/env python3
"""Bring the open PRs up to date with their base, in one call (Issue 1298).

Usage::

    python3 tools/refresh_open_prs.py [--task T ...]

The secretary runs this after a PR lands (the remaining PRs fall behind). With
no ``--task``, every open run (``queued`` / ``in_use`` / ``review``) whose
``runs.pr_url`` is set and whose ``pr_state`` is not ``merged`` / ``closed``.
For each PR, in this order (the order matters: a push record behind GitHub's
head made ``belt_slots.py park`` refuse, and recording it after the watcher
started made the watcher look stale):

1. read the head (``gh pr view``); a PR that is not open is skipped;
2. ``gh pr update-branch`` (GitHub merges the base into the PR branch; the
   test commit stays the first commit). A refusal mentioning a conflict is
   ``conflict``: listed, nothing recorded, the worker is asked to merge;
3. read the new head; unchanged is ``up_to_date`` (nothing recorded);
4. record ``fix_pushed`` (task, pr, repo, head) for the new head;
5. bring the worker's worktree to it with ``merge --ff-only``
   (``tools/worktree_sync.ff_only``; a worktree with uncommitted changes is
   left alone and reported ``dirty``);
6. say that the CI watcher must be re-armed: by ``tools/watcher_restart_guard.py``
   a watcher is current only when it started after the newest ``fix_pushed``,
   and this call just recorded one. Starting a watcher is an MCP operation
   (``/pr-watch-pane``), so this only says so.

One line per PR: ``REFRESH: <task> PR #<n> updated <old> -> <new>
watcher=needs_rearm worktree=synced|dirty|diverged|error`` (or
``up_to_date`` / ``conflict`` / ``skipped`` / ``error``), then
``REFRESH_DONE: ...``. Exit 0 when nothing conflicted or failed, else 1.
"""

from __future__ import annotations

import argparse
import json
import re
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
from tools.worktree_sync import ff_only  # noqa: E402

_PR_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")


def _view(pr: str, repo: str) -> dict:
    proc = subprocess.run(["gh", "pr", "view", pr, "--repo", repo, "--json", "headRefOid,state,headRefName"],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr view {pr}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "{}")
    if not isinstance(data, dict):
        raise RuntimeError(f"gh pr view {pr}: not an object")
    return data


def refresh_one(conn, task: str, pr_url: str, wdir: Optional[str], branch: Optional[str]) -> tuple[str, str]:
    m = _PR_URL.search(pr_url or "")
    if not m:
        return "error", f"cannot read the PR from {pr_url!r}"
    repo, pr = m.group(1), m.group(2)
    try:
        before = _view(pr, repo)
        if str(before.get("state", "")).upper() != "OPEN":
            return "skipped", f"PR #{pr} is {before.get('state')}"
        old = before.get("headRefOid") or ""
        upd = subprocess.run(["gh", "pr", "update-branch", pr, "--repo", repo],
                             capture_output=True, text=True, encoding="utf-8")
        if upd.returncode != 0:
            text = (upd.stderr + upd.stdout).strip()
            if "conflict" in text.lower():
                return "conflict", f"PR #{pr} conflicts with its base; ask the worker to merge the base ({text[:200]})"
            return "error", f"PR #{pr}: gh pr update-branch failed: {text[:300]}"
        new = _view(pr, repo).get("headRefOid") or ""
    except (RuntimeError, ValueError, OSError) as exc:
        return "error", str(exc)
    if new and new == old and _last_push(conn, task) not in (None, new):
        # An earlier update-branch moved the head but its record was never written
        # (the head read after it failed): write it now.
        return _record_push(conn, task, pr, repo, old, new, wdir, branch, before, repaired=True)
    if not new or new == old:
        # An earlier refresh may have left the worktree behind (dirty, or a failed fetch).
        if wdir and branch and subprocess.run(["git", "-C", wdir, "merge-base", "--is-ancestor", old, "HEAD"],
                                              capture_output=True).returncode != 0:
            worktree = ff_only(wdir, branch)
            return ("error" if worktree == "error" else "up_to_date"), f"PR #{pr} at {old[:12]}; worktree={worktree}"
        return "up_to_date", f"PR #{pr} at {old[:12]}"
    return _record_push(conn, task, pr, repo, old, new, wdir, branch, before)


def _last_push(conn, task: str) -> Optional[str]:
    head = None
    for (raw,) in conn.execute("SELECT payload_json FROM events WHERE kind = 'fix_pushed' ORDER BY id"):
        try:
            p = json.loads(raw or "{}")
        except ValueError:
            continue
        if isinstance(p, dict) and p.get("task") == task:
            head = p.get("head") or p.get("commit") or p.get("sha") or head
    return head


def _record_push(conn, task, pr, repo, old, new, wdir, branch, before, repaired=False):
    with StateWriter(conn).transaction() as w:
        w.append_event(kind="fix_pushed", actor="secretary",
                       payload={"task": task, "pr": int(pr), "repo": repo, "head": new,
                                "source": "refresh_open_prs"}, run_task_id=task)
    worktree = ff_only(wdir, branch or before.get("headRefName") or "") if wdir else "error"
    # The record and the watcher note stand either way; a failed local sync still fails the call.
    result = "error" if worktree == "error" else "updated"
    if repaired:
        return result, (f"PR #{pr} at {new[:12]} (the record of an earlier update was missing; written now)"
                         f" watcher=needs_rearm worktree={worktree}")
    # A watcher started before this push is stale by the org's rule (tools/watcher_restart_guard.py:
    # the newest watcher must start after the newest fix_pushed), and none can have started after it.
    return result, f"PR #{pr} updated {old[:12]} -> {new[:12]} watcher=needs_rearm worktree={worktree}"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/refresh_open_prs.py",
                                 description="Update open PRs with their base, record the new heads, sync worktrees.")
    ap.add_argument("--root", type=Path, help="org root; its .state/state.db is used (default: discovery)")
    ap.add_argument("--db-path", type=Path, help="state.db override (default: discovery)")
    ap.add_argument("--task", action="append", help="only these tasks (repeatable; default: every open PR)")
    args = ap.parse_args(argv)
    db = args.db_path or (args.root / ".state" / "state.db" if args.root else None)
    conn = connect(resolve_state_db_path(db))
    counts: dict[str, list[str]] = {}
    try:
        query = ("SELECT r.task_id, r.pr_url, w.abs_path, r.branch FROM runs r "
                 "LEFT JOIN worker_dirs w ON w.id = r.worker_dir_id WHERE r.pr_url IS NOT NULL AND r.pr_url != '' "
                 "AND r.status IN ('queued', 'in_use', 'review') "
                 "AND COALESCE(r.pr_state, '') NOT IN ('merged', 'closed')")
        rows = [tuple(r) for r in conn.execute(query)]
        if args.task:
            rows = [r for r in rows if r[0] in args.task]
            for missing in sorted(set(args.task) - {r[0] for r in rows}):
                print(f"REFRESH: {missing} error (no open run with an open PR)")
                counts.setdefault("error", []).append(missing)
        for task, pr_url, wdir, branch in rows:
            result, detail = refresh_one(conn, task, pr_url, wdir, branch)
            print(f"REFRESH: {task} {result} ({detail})" if result != "updated" else f"REFRESH: {task} {detail}",
                  flush=True)
            counts.setdefault(result, []).append(task)
    finally:
        conn.close()
    print("REFRESH_DONE: " + (", ".join(f"{k} {len(v)} ({' '.join(v)})" for k, v in sorted(counts.items()))
                              or "no open PR"))
    return 1 if counts.get("conflict") or counts.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
