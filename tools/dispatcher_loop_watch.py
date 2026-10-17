#!/usr/bin/env python3
"""Detect, from outside the dispatcher, that its /loop 3m monitoring is unarmed.

Issue #1168. The dispatcher's monitoring loop can sit unarmed while workers
keep running: it announces "/loop 3m で監視します" on the first
DELEGATE_COMPLETE and then falls idle, and the only guard was a written
step in /org-start ("起動後の裏取り") that the secretary has to remember to
perform. ``relay_scan.py --audit`` cannot catch this either, because it
runs *inside* the very cycle that is not running.

The trace already exists: every cycle runs ``relay_scan.py --cycle``, which
stamps ``last_scan_at`` into ``.state/dispatcher/relay-scan-heartbeat.json``
unconditionally (:func:`tools.relay_scan.write_heartbeat`). This tool only
reads it from the other side. It is run by the secretary's UserPromptSubmit
hook (``.hooks/check-dispatcher-loop.sh``), so the check happens on every
secretary turn whether or not anyone follows the runbook.

Finding: at least one worker is live (``runs.status`` in ``in_use`` /
``review`` -- the workers whose panes keep the loop obliged to run, see
worker-monitoring Step 7) **and** the newer of (last scan, oldest live
worker's ``dispatched_at``) is older than ``--stale-min`` (default 9 min =
three /loop 3m cycles). Measuring from the oldest dispatch gives a freshly
started org one grace window to arm the loop before it is reported.

Output: one line on stdout when there is a finding, nothing otherwise, and
the same unarmed state is reported only once: the warned ``last_scan_at`` is
kept in ``.state/dispatcher/loop-watch-warned.json`` and the line comes back
only when the heartbeat moves or the state clears (the hook runs every turn,
so repeating it would just pile up context). Always exits 0, opens
``state.db`` read-only and never creates it: it is advisory context for the
secretary and must never block a prompt.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.relay_scan import DEFAULT_RECIPIENT, _read_heartbeat  # noqa: E402
from tools.state_db.discover import resolve_state_db_path  # noqa: E402
from tools.state_db.queries import USER_VISIBLE_STATUSES  # noqa: E402

DEFAULT_STALE_MIN = 9.0
_WARNED_FILENAME = "loop-watch-warned.json"


def _last_scan(db_path: Path):
    entry = _read_heartbeat(db_path).get(DEFAULT_RECIPIENT)
    last = entry.get("last_scan_at") if isinstance(entry, dict) else None
    return last if isinstance(last, str) else None


def _finding(db_path: Path, stale_min: float):
    """Return ``(line, key)`` for a finding, else None.

    ``key`` names the unarmed state for :func:`report_once`: the last scan
    plus the live task ids, so a new set of workers is a new state even if
    no prompt saw the previous one clear.
    """
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        marks = ",".join("?" * len(USER_VISIBLE_STATUSES))
        live = conn.execute(
            f"SELECT task_id, dispatched_at FROM runs WHERE status IN ({marks})"
            " ORDER BY dispatched_at", USER_VISIBLE_STATUSES).fetchall()
        if not live:
            return None
        last = _last_scan(db_path)
        # Same ISO-8601 Z format on both sides, so max() is chronological.
        ref = max(last or "", live[0][1])
        age = conn.execute(
            "SELECT (julianday('now') - julianday(?)) * 1440.0", (ref,)
        ).fetchone()[0]
    finally:
        conn.close()
    if age is None or age <= stale_min:
        return None
    tasks = ", ".join(t for t, _ in live)
    line = (
        f"DISPATCHER_LOOP_UNARMED: live workers {len(live)} ({tasks}), "
        f"dispatcher monitoring cycle (relay_scan --cycle) silent for "
        f"{age:.0f} min (last_scan_at={last or 'never'}, "
        f"threshold={stale_min:g} min). The dispatcher /loop 3m is likely "
        f"unarmed or dead: inspect_pane the dispatcher and re-arm it with the "
        f"canonical directive (org-start 「起動後の裏取り」 / "
        f"dispatcher-resume Step 5)."
    )
    return line, f"{last or 'never'}|{','.join(sorted(t for t, _ in live))}"


def check(db_path: Path, stale_min: float = DEFAULT_STALE_MIN):
    """Return the warning line, or None when there is nothing to report."""
    found = _finding(db_path, stale_min)
    return found[0] if found else None


def _warned_path(db_path: Path) -> Path:
    return db_path.parent / "dispatcher" / _WARNED_FILENAME


def report_once(db_path: Path, stale_min: float = DEFAULT_STALE_MIN):
    """:func:`check`, minus a repeat of the state already reported."""
    found = _finding(db_path, stale_min)
    marker = _warned_path(db_path)
    if found is None:
        marker.unlink(missing_ok=True)
        return None
    line, key = found
    try:
        if json.loads(marker.read_text(encoding="utf-8")).get("key") == key:
            return None
    except (OSError, ValueError, AttributeError):
        pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"key": key}), encoding="utf-8")
    except OSError:
        pass  # unrecorded: the warning repeats next turn (fail-loud side)
    return line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 tools/dispatcher_loop_watch.py",
        description="Warn when workers are live but the dispatcher loop is silent.")
    ap.add_argument("--db-path", type=Path, default=None)
    ap.add_argument("--stale-min", type=float, default=DEFAULT_STALE_MIN)
    args = ap.parse_args(argv)
    try:
        line = report_once(resolve_state_db_path(args.db_path), args.stale_min)
    except Exception:  # noqa: BLE001 -- advisory; a broken DB must not block a prompt
        return 0
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
