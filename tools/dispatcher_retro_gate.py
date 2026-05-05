#!/usr/bin/env python3
"""Polling gate for the dispatcher's retro completion-report ack.

Issue #285. Replaces the natural-language polling prose in
``.dispatcher/CLAUDE.md`` (around the "完了報告ゲート" section) with a
deterministic CLI so the dispatcher Claude no longer has to re-derive
cadence and ack-judgement on every retro.

Design (option B from the issue brief): this CLI never calls renga-peers
itself. The dispatcher Claude stays the MCP boundary — it issues
``mcp__renga-peers__send_message`` once up front and then
``mcp__renga-peers__check_messages`` per poll, piping each result into
this script's stdin. The script owns:

  * cadence (sleep between attempts, max attempt count)
  * ack judgement (regex against secretary-origin messages)
  * structured JSON output for the dispatcher to switch on

Wire protocol on stdout / stdin (newline-delimited JSON):

  CLI -> stdout: {"action": "send_initial", "to_id": "<secretary>",
                  "message": "<task_id> の完了報告は届いていますか？"}
  CLI -> stdout: {"action": "check_messages", "attempt": <n>}
  stdin -> CLI:  {"messages": [<msg>, ...]}
  ... (repeats up to --timeout-attempts)
  CLI -> stdout: {"status": "acked"|"timeout"|"error", ...}

Exit codes: 0=acked, 1=timeout, 2=error.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from datetime import datetime, timezone

# Ensure UTF-8 stdio on Windows (default cp932 mangles JP prompts).
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and getattr(_stream, "encoding", "").lower() != "utf-8":
        try:
            _stream.reconfigure(encoding="utf-8")  # py>=3.7
        except (AttributeError, io.UnsupportedOperation):
            pass
if getattr(sys.stdin, "encoding", "").lower() != "utf-8":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation):
        pass

DEFAULT_ACK_PATTERN = r"(届い|受領|受け取|ack|received|got it)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(obj: dict, stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stream.flush()


def _is_secretary_message(msg: dict, secretary: str) -> bool:
    """Return True if the message looks like it came from the secretary pane.

    renga-peers shapes vary across SDK versions, so accept either id or name
    matching, and treat messages with no sender attribution as candidates
    (the dispatcher already filters by recipient = self)."""
    from_id = msg.get("from_id")
    from_name = msg.get("from_name")
    if from_id is None and from_name is None:
        return True
    return from_id == secretary or from_name == secretary


def _extract_body(msg: dict) -> str:
    return msg.get("message") or msg.get("text") or msg.get("body") or ""


def run_gate(args: argparse.Namespace, *, sleep=time.sleep,
             stdin=None, stdout=None, stderr=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    pattern = re.compile(args.ack_pattern)

    _emit({
        "action": "send_initial",
        "to_id": args.secretary,
        "message": f"{args.task_id} の完了報告は届いていますか？",
    }, stdout)

    for attempt in range(1, args.timeout_attempts + 1):
        if attempt > 1 and args.interval_seconds > 0:
            sleep(args.interval_seconds)

        _emit({"action": "check_messages", "attempt": attempt}, stdout)

        line = stdin.readline()
        if not line:
            _emit({
                "status": "error",
                "reason": "stdin_closed",
                "attempts": attempt,
                "received_at": None,
                "raw": None,
            }, stdout)
            return 2

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({
                "status": "error",
                "reason": f"invalid_json: {exc}",
                "attempts": attempt,
                "received_at": None,
                "raw": None,
            }, stdout)
            return 2

        messages = payload.get("messages") or []
        for msg in messages:
            if not _is_secretary_message(msg, args.secretary):
                continue
            body = _extract_body(msg)
            if pattern.search(body):
                _emit({
                    "status": "acked",
                    "received_at": _now_iso(),
                    "raw": body,
                    "attempts": attempt,
                }, stdout)
                return 0

    _emit({
        "status": "timeout",
        "received_at": None,
        "raw": None,
        "attempts": args.timeout_attempts,
    }, stdout)
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Poll for the secretary's completion-report ack before "
                    "running the dispatcher retro (Issue #285).",
    )
    p.add_argument("--task-id", required=True,
                   help="Task id used in the prompt sent to the secretary.")
    p.add_argument("--secretary", default="secretary",
                   help="Secretary pane name / id (default: secretary).")
    p.add_argument("--timeout-attempts", type=int, default=10,
                   help="Maximum number of check_messages polls (default: 10).")
    p.add_argument("--interval-seconds", type=float, default=30.0,
                   help="Seconds to sleep between polls (default: 30).")
    p.add_argument("--ack-pattern", default=DEFAULT_ACK_PATTERN,
                   help="Regex applied to incoming message bodies to decide ack.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_gate(args)


if __name__ == "__main__":
    sys.exit(main())
