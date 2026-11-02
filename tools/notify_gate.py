#!/usr/bin/env python3
"""Send/suppress gate for dispatcher -> secretary notifications (Refs #1226).

A message that reaches the secretary costs it one turn, so the dispatcher
only sends the ones the secretary can act on. The decision lives here, in
deterministic code; the dispatcher prose branches on this tool's JSON
output instead of judging in prose.

    python3 ../tools/notify_gate.py --kind event_cursor_reset \
        --idle-state ../.state/dispatcher/worker-idle-state.json
    python3 ../tools/notify_gate.py --kind curator_vanished --curate-result done

stdout: one JSON line ``{"kind", "send", "reason"}``. Exit 0 on a decision,
2 on a usage error. When the state cannot be judged the answer is
``send: true`` (fail loud): a suppressed message must be provably pointless.
Callers that get a non-zero exit or unparseable output must also send.

Only reads. Recording the suppression is the caller's journal_append line.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

KINDS = ("event_cursor_reset", "curator_vanished")


def decide_event_cursor_reset(idle_state: Any) -> tuple[bool, str]:
    """EVENT_CURSOR_RESET only matters for background-tab workers.

    ``idle_state`` is the parsed worker-idle-state.json (``None`` = file
    absent, i.e. no worker records at all).
    """
    if idle_state is None:
        return False, "no_background_worker"
    if not isinstance(idle_state, dict):
        return True, "unreadable_state"
    unknown = False
    for rec in idle_state.values():
        if not isinstance(rec, dict):
            return True, "unreadable_state"
        placement = rec.get("placement")
        if placement == "background_tab":
            return True, "background_worker_present"
        if placement not in (None, "same_tab"):
            unknown = True
    return (True, "unknown_placement") if unknown else (False, "no_background_worker")


def decide_curator_vanished(curate_result: Optional[str]) -> tuple[bool, str]:
    """A vanished curator only needs the secretary if no CURATE_* arrived."""
    if curate_result in ("done", "skipped"):
        return False, "result_already_received"
    return True, "no_result_received"


def _load_idle_state(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else "unreadable"  # JSON null != absent
    except (OSError, ValueError):
        return "unreadable"  # not a dict -> decide_* answers send


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--idle-state", help="worker-idle-state.json (event_cursor_reset)")
    ap.add_argument("--curate-result", default="null",
                    help="curate_result of the inflight record: done|skipped|error|null")
    args = ap.parse_args(argv)
    if args.kind == "event_cursor_reset":
        if not args.idle_state:
            ap.error("--idle-state is required for event_cursor_reset")
        send, reason = decide_event_cursor_reset(_load_idle_state(Path(args.idle_state)))
    else:
        send, reason = decide_curator_vanished(args.curate_result)
    print(json.dumps({"kind": args.kind, "send": send, "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
