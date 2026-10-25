#!/usr/bin/env python3
"""Spawn the PR CI watcher pane in a background renga tab (Issue #1216).

Usage::

    python3 tools/spawn_watch_pane.py --pr <PR> --repo OWNER/REPO [--no-merge-watch]

renga only. Under broker (no tabs) it does nothing and exits 3, and the
caller keeps the existing ``/pr-watch-pane`` route.

What one run does:

1. Refuses a second watcher for the same PR (``pr-watch-<PR>`` in any tab).
2. Splits a watcher pane inside an existing background tab labelled
   ``監視``, or opens a new one (``spawn_pane(tab={"new": ...})`` opens it in
   the background, so the user's visible tab does not change).
3. Hands the watcher the secretary's numeric pane id via ``ORG_NOTIFY_TO``:
   renga resolves a peer *name* only inside the sender's tab, while numeric
   ids reach every tab. ``RENGA_PANE_ID`` of the caller is that id, since
   the secretary runs this script.
4. Waits for the watcher's ``pr_watch_started`` event (written by
   ``tools/pr_watch.py`` after its startup ping). Delivered → done.
5. Otherwise closes the new pane by numeric id, re-spawns the watcher by
   splitting the secretary's own tab (the pre-#1216 layout, notified by
   name), and exits 1 so the caller tells the human.

The pane closes itself when the watch ends (``renga close --id
$RENGA_PANE_ID``); renga closes the tab with its last pane.

Output: one ``SPAWN_WATCH: <status> ...`` line. Exit codes: 0 spawned in
the watch tab / already running, 1 fell back to the secretary's tab,
2 could not spawn at all, 3 not renga.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WATCH_TAB = "監視"
CONFIRM_TIMEOUT_SEC = 60.0
CONFIRM_POLL_SEC = 1.0

_PANE_RE = re.compile(
    r'^- id=(?P<id>\d+)(?: name=(?P<name>\S+))?(?: role=(?P<role>\S+))?'
    r'(?: \(focused\))?(?: (?P<mine>\[your tab\])| \[tab (?P<tab>\d+)(?: "(?P<label>.*)")?\])?$'
)
_GEOM_RE = re.compile(r"x=(\d+) y=(\d+) width=(\d+) height=(\d+)")
_SPAWNED_RE = re.compile(r"Spawned pane id=(\d+)")


def parse_pane_list(text: str) -> "list[dict]":
    """Parse renga's ``list_panes`` text into pane dicts."""
    panes: "list[dict]" = []
    for line in text.splitlines():
        m = _PANE_RE.match(line)
        if m:
            panes.append({
                "id": int(m["id"]), "name": m["name"], "role": m["role"],
                "same_tab": bool(m["mine"]) or m["tab"] is None,
                "tab_label": m["label"],
                "x": 0, "y": 0, "width": 0, "height": 0, "cwd": None,
            })
            continue
        if panes and line.startswith("  cwd: "):
            panes[-1]["cwd"] = line[len("  cwd: "):]
            continue
        g = _GEOM_RE.search(line)
        if g and panes and "geometry:" in line:
            panes[-1].update(x=int(g[1]), y=int(g[2]),
                             width=int(g[3]), height=int(g[4]))
    return panes


def build_command(pr: int, repo: str, merge_watch: bool,
                  notify_to: "str | None") -> str:
    """The shell line typed into the watcher pane.

    Same shape as the ``/pr-watch-pane`` broker command: ``nocorrect`` first
    (zsh correctall would stall the line), then the caller's transport env
    mirrored into the pane, then the watcher, then a renga self-close.
    """
    parts = ["nocorrect true 2>/dev/null || true"]
    for name in ("ORG_TRANSPORT", "ORG_BROKER_STATE_DIR", "PATH"):
        value = os.environ.get(name)
        if value:
            parts.append(f"export {name}={shlex.quote(value)}")
    if notify_to:
        parts.append(f"export ORG_NOTIFY_TO={shlex.quote(notify_to)}")
    watch = f"bash tools/pr-watch.sh {pr} --repo {shlex.quote(repo)}"
    if merge_watch:
        watch += " --merge-watch"
    parts.append("mkdir -p .state")
    parts.append(f"{watch} --no-detach 2>&1 | tee -a .state/pr-watch-{pr}.log")
    parts.append('renga close --id "$RENGA_PANE_ID" 2>/dev/null || true')
    return "; ".join(parts)


def _call(tool: str, args: dict) -> "tuple[bool, str]":
    from tools.peer_notify import renga_tool_call

    return renga_tool_call(tool, args, timeout=15.0)


def _anchors(panes: "list[dict]") -> "list[dict]":
    """Ranked split candidates via tools/pick_watcher_anchor.py."""
    from tools.pick_watcher_anchor import build_result, parse_panes

    result = build_result(parse_panes({"panes": panes}), transport="renga")
    return result["candidates"]


def _spawn(base: dict, candidates: "list[dict]", tab: "dict | None") -> "int | None":
    """Try each candidate in rank order; return the new pane id."""
    for cand in candidates:
        args = dict(base, target=str(cand["target_id"]), direction=cand["direction"])
        if tab:
            args["tab"] = tab
        ok, text = _call("spawn_pane", args)
        m = _SPAWNED_RE.search(text)
        if ok and m:
            return int(m[1])
    return None


def _max_event_id(db: Path) -> int:
    if not db.exists():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])
    finally:
        conn.close()


def wait_started(db: Path, pr: int, after_id: int,
                 timeout: float = CONFIRM_TIMEOUT_SEC) -> "bool | None":
    """``delivered`` of the first ``pr_watch_started`` for ``pr`` past
    ``after_id``; ``None`` when none appears within ``timeout``."""
    deadline = time.monotonic() + timeout
    while True:
        if db.exists():
            conn = sqlite3.connect(str(db))
            try:
                rows = conn.execute(
                    "SELECT payload_json FROM events WHERE kind = 'pr_watch_started' "
                    "AND id > ? ORDER BY id", (after_id,)).fetchall()
            finally:
                conn.close()
            for (raw,) in rows:
                payload = json.loads(raw or "{}")
                if payload.get("pr") == pr:
                    return bool(payload.get("delivered"))
        if time.monotonic() >= deadline:
            return None
        time.sleep(CONFIRM_POLL_SEC)


def _spawn_in_watch_tab(pr: int, repo: str, merge_watch: bool, db: Path,
                        base: dict, sec_id: str, every: "list[dict]") -> "str | None":
    """Spawn in the watch tab and confirm the ping. ``None`` on success,
    otherwise why the caller has to fall back (the pane is already closed)."""
    command = build_command(pr, repo, merge_watch, sec_id)
    after = _max_event_id(db)
    watch = [p for p in every if p["tab_label"] == WATCH_TAB and not p["same_tab"]]
    new_id = None
    if watch:
        new_id = _spawn(dict(base, command=command), _anchors(watch),
                        {"pane_id": watch[0]["id"]})
    if new_id is None:
        ok, text = _call("spawn_pane", dict(base, command=command,
                                            tab={"new": {"name": WATCH_TAB}}))
        m = _SPAWNED_RE.search(text)
        if not (ok and m):
            return f"spawn in the {WATCH_TAB} tab failed ({text.strip() or 'no reply'})"
        new_id = int(m[1])
    delivered = wait_started(db, pr, after)
    if delivered:
        print(f"SPAWN_WATCH: spawned id={new_id} tab={WATCH_TAB} notify_to={sec_id}")
        return None
    _call("close_pane", {"target": str(new_id)})
    return "ping not delivered" if delivered is False else "no pr_watch_started event"


def run(pr: int, repo: str, merge_watch: bool, db: Path) -> int:
    from tools.transport import resolve

    if resolve() != "renga":
        print("SPAWN_WATCH: skip - not renga; use the /pr-watch-pane broker route")
        return 3
    sec_id = os.environ.get("RENGA_PANE_ID", "").strip()
    if not sec_id.isdigit():
        print("SPAWN_WATCH: failed - RENGA_PANE_ID is not set; run this from the secretary pane")
        return 2

    name = f"pr-watch-{pr}"
    base = {"role": "watcher", "name": name, "cwd": str(REPO_ROOT)}
    ok, text = _call("list_panes", {"tab": {"all": True}})
    if ok:
        # Another organisation in another tab reuses the same names and tab
        # label; ``cwd`` is what tells its panes apart (L-§1.5).
        every = [p for p in parse_pane_list(text)
                 if p["same_tab"] or p["cwd"] in (None, str(REPO_ROOT))]
        running = [p for p in every if p["name"] == name]
        if running:
            print(f"SPAWN_WATCH: already-running id={running[0]['id']}")
            return 0
        why = _spawn_in_watch_tab(pr, repo, merge_watch, db, base, sec_id, every)
        if why is None:
            return 0
    else:
        # No all-tab list (renga without cross_tab_list, or unreachable):
        # nothing in the watch tab can be found, so stay in our own tab.
        why = f"all-tab list_panes unavailable ({text.strip() or 'no reply'})"

    ok, text = _call("list_panes", {})
    own = parse_pane_list(text) if ok else []
    running = [p for p in own if p["name"] == name]
    if running:
        print(f"SPAWN_WATCH: already-running id={running[0]['id']}")
        return 0
    fallback = _spawn(dict(base, command=build_command(pr, repo, merge_watch, None)),
                      _anchors(own), None)
    if fallback is None:
        print(f"SPAWN_WATCH: failed - {why}; splitting the secretary tab failed too")
        return 2
    print(f"SPAWN_WATCH: fell-back id={fallback} tab=secretary reason={why}")
    return 1


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/spawn_watch_pane.py",
        description="Spawn the PR CI watcher in a background renga tab.")
    parser.add_argument("--pr", type=int, required=True, help="pull request number")
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--no-merge-watch", action="store_true",
                        help="stop at the CI verdict instead of waiting for the merge")
    args = parser.parse_args(argv)
    from tools.state_db.discover import resolve_state_db_path

    return run(args.pr, args.repo, not args.no_merge_watch, resolve_state_db_path())


if __name__ == "__main__":
    sys.exit(main())
