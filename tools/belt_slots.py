#!/usr/bin/env python3
"""Count conveyor slots by work actually being done (Issue 1295).

A run that is only waiting on a human does not take a slot:

* ``merge``    -- ``review`` with an open PR whose current head CI passed
                  (``tools.watcher_restart_guard.evaluate`` verdict
                  ``completed``; a late green for an older head does not count).
* ``decision`` -- ``review`` or ``in_use`` with an escalated pending decision
                  the human has not answered (``.state/pending_decisions.json``).
* ``approval`` -- ``review`` without a PR, after the secretary emitted
                  ``notify_sent kind=awaiting_user gate=worker_completed``.

Everything else that is ``queued`` / ``in_use`` / ``review`` is busy. A
waiting ``review`` run whose pane is still open is reported as *parkable*.
Mid-task decisions (``in_use``) are waiting but never parkable.

This tool only counts and checks. Closing a parked pane and re-spawning a
worker into the same worktree (``gen_delegate_payload.py revive``) are not
implemented yet, and the lifecycle contract does not allow them yet; both
arrive together in the follow-up change for Issue 1295. Until then no skill
calls ``park``, and its output is advisory.

Subcommands (all print one JSON object or one line):

  count       busy / waiting / free_slots / parkable
  park        re-check one run and record ``worker_parked`` (advisory; the
              caller that closes the pane comes with the revive path)
  hold        record ``belt_hold`` (why free slots are deliberately left empty)
  idle-check  for the dispatcher loop: exit 10 + one line when free slots and
              candidates have both existed for 20+ minutes with no hold
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import pending_decisions as pd  # noqa: E402
from tools.watcher_restart_guard import (  # noqa: E402
    _split_pr_url,
    evaluate,
    head_corroborates,
)

ACTIVE = ("queued", "in_use", "review")
IDLE_MINUTES = 20
IDLE_EXIT = 10


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _events(conn: sqlite3.Connection, task: str, kinds: tuple[str, ...]) -> list[tuple[str, str, dict]]:
    """(occurred_at, kind, payload) rows about ``task``, oldest first."""
    marks = ",".join("?" for _ in kinds)
    rows = conn.execute(
        f"SELECT occurred_at, kind, payload_json FROM events WHERE kind IN ({marks}) "
        "ORDER BY occurred_at, id",
        kinds,
    ).fetchall()
    out = []
    for at, kind, raw in rows:
        try:
            p = json.loads(raw or "{}")
        except ValueError:
            continue
        if isinstance(p, dict) and (
            task in (p.get("task"), p.get("task_id")) or p.get("worker") == f"worker-{task}"
        ):
            out.append((at, kind, p))
    return out


def _last(rows, kinds) -> Optional[tuple[str, str, dict]]:
    hits = [r for r in rows if r[1] in kinds]
    return hits[-1] if hits else None


def _pane(conn, task) -> dict:
    rows = _events(conn, task, ("worker_spawned", "worker_spawn_verified", "pane_closed", "worker_closed"))
    spawned = _last(rows, ("worker_spawned",))
    closed = _last(rows, ("pane_closed", "worker_closed"))
    verified = _last(rows, ("worker_spawn_verified",))
    # Only the latest spawn's own verification names the pane; an older one
    # may point at a pane id that has since been reused.
    current = verified is not None and spawned is not None and verified[0] >= spawned[0]
    return {
        "open": spawned is not None and (closed is None or closed[0] < spawned[0]),
        "dir": spawned[2].get("dir") if spawned else None,
        "pane_id": verified[2].get("pane_id") if current else None,
    }


def _waiting_decision(task, decisions) -> bool:
    return any(
        d.task_id == task and d.status == "escalated" and not d.user_replied_at
        for d in decisions
    )


def _classify(conn, task, status, pr_url, decisions) -> tuple[Optional[str], Optional[dict]]:
    """Return (reason, ci_terminal) for a waiting run, (None, None) if busy."""
    if _waiting_decision(task, decisions):
        return "decision", None
    if status != "review":
        return None, None
    if pr_url:
        repo, pr = _split_pr_url(pr_url)
        if pr is None:
            return None, None
        v = evaluate(conn, pr, repo, task_id=task)
        term = v.terminal or {}
        if v.verdict == "completed" and (
            term.get("status") == "passed" or str(term.get("kind", "")).startswith("pr_merged")
        ):
            return "merge", term
        return None, None
    rows = _events(conn, task, ("worker_completed", "delegate_resume", "notify_sent"))
    notice = _last([r for r in rows if r[2].get("kind") == "awaiting_user"
                    and r[2].get("gate") == "worker_completed"], ("notify_sent",))
    work = _last(rows, ("worker_completed", "delegate_resume"))
    if notice and (work is None or work[0] <= notice[0]):
        return "approval", None
    return None, None


def count(conn: sqlite3.Connection, *, max_parallel: Optional[int], decisions_path: Path) -> dict:
    decisions = pd._load(Path(decisions_path))
    busy, waiting = [], []
    for task, status, pr_url in conn.execute(
        f"SELECT task_id, status, pr_url FROM runs WHERE status IN ({','.join('?' * len(ACTIVE))}) "
        "ORDER BY id",
        ACTIVE,
    ).fetchall():
        reason, _ = _classify(conn, task, status, pr_url, decisions)
        if reason is None:
            busy.append(task)
            continue
        pane = _pane(conn, task)
        waiting.append({"task": task, "status": status, "reason": reason, "pane_open": pane["open"]})
    out = {
        "max": max_parallel,
        "busy": busy,
        "waiting": waiting,
        "parkable": [w["task"] for w in waiting if w["status"] == "review" and w["pane_open"]],
    }
    if max_parallel is None:  # unlimited
        out["free_slots"], out["over_capacity"] = None, 0
    else:
        out["free_slots"] = max(0, max_parallel - len(busy))
        out["over_capacity"] = max(0, len(busy) - max_parallel)
    return out


def _git(wdir: str, *args: str) -> Optional[str]:
    try:
        return subprocess.run(["git", "-C", wdir, *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def park(conn: sqlite3.Connection, task: str, *, decisions_path: Path) -> dict:
    row = conn.execute("SELECT status, pr_url FROM runs WHERE task_id = ?", (task,)).fetchone()
    if row is None:
        return {"ok": False, "task": task, "why": "no such run"}
    status, pr_url = row[0], row[1]
    reason, term = _classify(conn, task, status, pr_url, pd._load(Path(decisions_path)))
    if reason is None or status != "review":
        return {"ok": False, "task": task, "why": f"not a waiting review run (status={status}, reason={reason})"}
    pane = _pane(conn, task)
    if not pane["open"]:
        return {"ok": False, "task": task, "why": "pane already closed"}
    if not pane["pane_id"]:
        return {"ok": False, "task": task, "why": "latest spawn has no worker_spawn_verified pane id"}
    wdir = pane["dir"]
    if not wdir or not Path(wdir).is_dir() or _git(wdir, "rev-parse", "HEAD") is None:
        return {"ok": False, "task": task, "why": f"worktree not found: {wdir!r}"}
    if _git(wdir, "status", "--porcelain", "--untracked-files=no") != "":
        return {"ok": False, "task": task, "why": "worktree has uncommitted changes"}
    head = _git(wdir, "rev-parse", "HEAD")
    if reason == "merge" and not (term and term.get("head")):
        # evaluate() tolerates a verdict without a head; parking does not.
        return {"ok": False, "task": task, "why": "CI verdict carries no head to compare with the worktree"}
    if term and term.get("head") and not head_corroborates(head, term["head"]):
        return {"ok": False, "task": task, "why": f"local HEAD {head[:12]} is not the CI-green head {term['head']}"}
    payload = {"task": task, "worker": f"worker-{task}", "reason": reason,
               "pane_id": pane["pane_id"], "head": head, "pr_url": pr_url}
    conn.execute("INSERT INTO events (actor, kind, payload_json) VALUES ('secretary', 'worker_parked', ?)",
                 (json.dumps(payload),))
    conn.commit()
    return {"ok": True, **payload}


def hold(conn: sqlite3.Connection, reason: str, *, minutes: int = 60, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    payload = {"reason": reason, "expires_at": _iso(now + timedelta(minutes=minutes))}
    cur = conn.execute("INSERT INTO events (occurred_at, actor, kind, payload_json) "
                       "VALUES (?, 'secretary', 'belt_hold', ?)", (_iso(now), json.dumps(payload)))
    conn.commit()
    return int(cur.lastrowid)


def read_contract(path: Path) -> tuple[Optional[str], Optional[int]]:
    """(belt_state, max_parallel) from the scope contract; (None, None) if absent."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None, None
    state = re.search(r"^\s*-\s*belt_state:\s*(\w+)", text, re.M)
    maxp = re.search(r"^\s*-\s*max_parallel:\s*(\d+)", text, re.M)
    # A contract written before belt_state existed is read as running.
    return (state.group(1) if state else "running"), (int(maxp.group(1)) if maxp else None)


def _candidates(conn) -> int:
    row = conn.execute("SELECT payload_json FROM events WHERE kind = 'work_discovery_scanned' "
                       "ORDER BY occurred_at DESC, id DESC LIMIT 1").fetchone()
    if row is None:
        return 0
    try:
        return int(json.loads(row[0]).get("candidate_count") or 0)
    except (ValueError, TypeError):
        return 0


def _hold_active(conn, now: datetime) -> bool:
    for (raw,) in conn.execute("SELECT payload_json FROM events WHERE kind = 'belt_hold'").fetchall():
        try:
            if _parse(json.loads(raw)["expires_at"]) > now:
                return True
        except (ValueError, KeyError, TypeError):
            continue
    return False


def idle_check(conn, *, contract_path: Path, decisions_path: Path, idle_state_path: Path,
               now: Optional[datetime] = None, minutes: int = IDLE_MINUTES) -> tuple[int, str]:
    now = now or datetime.now(timezone.utc)
    idle_state_path = Path(idle_state_path)
    state, maxp = read_contract(contract_path)
    free = count(conn, max_parallel=maxp, decisions_path=decisions_path)["free_slots"] if state == "running" and maxp else 0
    cands = _candidates(conn)
    if not free or cands == 0:
        idle_state_path.unlink(missing_ok=True)
        return 0, ""
    try:
        prev = json.loads(idle_state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None
    # A dispatch (delegate_sent) or a filled slot means the belt is moving:
    # start a new episode. The slot count alone can stay flat when a worker
    # finishes and another is dispatched between two checks.
    sent = conn.execute("SELECT MAX(id) FROM events WHERE kind = 'delegate_sent'").fetchone()[0]
    if prev is None or free < prev.get("free", 0) or sent != prev.get("last_sent"):
        prev = {"since": _iso(now), "free": free, "notified": False}
    prev["free"], prev["last_sent"] = free, sent
    msg, code = "", 0
    if (not prev["notified"] and not _hold_active(conn, now)
            and now - _parse(prev["since"]) >= timedelta(minutes=minutes)):
        prev["notified"] = True
        code = IDLE_EXIT
        msg = (f"BELT_IDLE_SUSPECTED: free={free} since={prev['since']} candidates={cands} - "
               "dispatch the next candidate, or record why not with "
               "`python3 tools/belt_slots.py hold --reason ...`")
    idle_state_path.parent.mkdir(parents=True, exist_ok=True)
    idle_state_path.write_text(json.dumps(prev), encoding="utf-8")
    return code, msg


def _max_from_config(root: Path) -> Optional[int]:
    try:
        text = (root / "registry" / "org-config.md").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^max_concurrent_workers:\s*(\S+)", text, re.M)
    return int(m.group(1)) if m and m.group(1).isdigit() else None


def main(argv: Optional[list] = None) -> int:
    from tools.state_db import connect
    from tools.state_db.discover import discover_repo_root, resolve_state_db_path

    ap = argparse.ArgumentParser(description="Count conveyor slots by work actually being done.")
    ap.add_argument("--db-path", help="state.db (default: --db-path > $STATE_DB_PATH > discovery)")
    ap.add_argument("--root", help="org root holding .state/ and registry/ (default: discovered)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count")
    c.add_argument("--max", type=int, help="slot limit (default: contract max_parallel, else max_concurrent_workers)")
    p = sub.add_parser("park", help="check one run and record worker_parked (advisory; no pane is closed)")
    p.add_argument("--task", required=True)
    h = sub.add_parser("hold")
    h.add_argument("--reason", required=True)
    h.add_argument("--minutes", type=int, default=60)
    i = sub.add_parser("idle-check")
    i.add_argument("--minutes", type=int, default=IDLE_MINUTES)
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else discover_repo_root()
    contract = root / ".state" / "conveyor" / "scope-contract.md"
    decisions = root / ".state" / "pending_decisions.json"
    conn = connect(resolve_state_db_path(Path(args.db_path) if args.db_path else None))
    try:
        if args.cmd == "count":
            maxp = args.max if args.max is not None else (read_contract(contract)[1] or _max_from_config(root))
            print(json.dumps(count(conn, max_parallel=maxp, decisions_path=decisions), ensure_ascii=False))
            return 0
        if args.cmd == "park":
            r = park(conn, args.task, decisions_path=decisions)
            print(json.dumps(r, ensure_ascii=False))
            return 0 if r["ok"] else 1
        if args.cmd == "hold":
            hold(conn, args.reason, minutes=args.minutes)
            return 0
        code, msg = idle_check(conn, contract_path=contract, decisions_path=decisions,
                               idle_state_path=root / ".state" / "dispatcher" / "belt-idle.json",
                               minutes=args.minutes)
        if msg:
            print(msg)
        return code
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
