#!/usr/bin/env python3
"""Per-attempt ack judge for the dispatcher's retro completion-report gate.

Issue #285. Replaces the natural-language polling prose in
``.dispatcher/CLAUDE.md`` (around the "完了報告ゲート" section) with a
deterministic CLI so the dispatcher Claude no longer has to re-derive
ack-judgement on every retro.

Design: this CLI never calls renga-peers itself, and it does not run a
long-lived polling loop — Claude Code's Bash tool is one-shot, so a
co-routine that demands interactive stdin/stdout would not be runnable
in practice. Instead, the dispatcher Claude runs this script ONCE per
poll attempt, piping the latest ``check_messages`` result into stdin
along with the gate state from the previous attempt. The script returns
either a terminal verdict (``acked`` / ``replied_no_ack`` / ``timeout``
/ ``error``) or a ``polling`` verdict that carries the updated state
back so the dispatcher can sleep, fetch again, and re-invoke.

Invocation per attempt::

    python tools/dispatcher_retro_gate.py \
        --task-id <id> --secretary secretary \
        --attempt <n> --max-attempts 10 [--ack-pattern <regex>]

stdin (single JSON object on one or more lines)::

    {
      "messages": [<renga-peers message dict>, ...],
      "state":    {"last_secretary_attempt": <int>,
                   "last_secretary_body":    <str|null>}   // optional
    }

stdout (single JSON object — terminal or progress)::

    {"status": "acked",          "received_at": ..., "raw": ..., "attempts": <n>}
    {"status": "replied_no_ack", "received_at": ..., "raw": ..., "attempts": <n>}
    {"status": "timeout",        "received_at": null, "raw": null, "attempts": <max>}
    {"status": "polling",        "attempts": <n>,  "state": {...}}
    {"status": "error",          "reason":   ..., "attempts": <n>, ...}

Exit codes::

    0 = acked
    1 = timeout
    2 = error
    3 = replied_no_ack
    4 = polling (caller should sleep and re-invoke)

The ``--print-initial-prompt`` mode prints the task-templated initial
question (no stdin needed, no exit code semantics) so the dispatcher
can pipe it into ``mcp__renga-peers__send_message`` once before the
first attempt.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

# Ensure UTF-8 stdio on Windows (default cp932 mangles JP prompts/ack
# bodies). Reconfigure is best-effort — on streams that don't support
# it we leave the default in place.
for _stream_name in ("stdout", "stderr", "stdin"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is None:
        continue
    if getattr(_stream, "encoding", "").lower() == "utf-8":
        continue
    try:
        _stream.reconfigure(encoding="utf-8")  # py>=3.7
    except (AttributeError, io.UnsupportedOperation):
        pass

DEFAULT_ACK_PATTERN = r"(届[いきけくこ]|受領|受け取|ack|received|got it)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(obj: dict, stream) -> None:
    stream.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stream.flush()


def _is_secretary_message(msg: dict, secretary: str) -> bool:
    """Return True only if the message explicitly identifies the secretary
    as sender. Messages with no sender attribution are NOT treated as
    secretary-origin — accepting them would let an unrelated message
    whose body happens to contain "届い" trigger a false ack."""
    return msg.get("from_id") == secretary or msg.get("from_name") == secretary


def _extract_body(msg: dict) -> str:
    """Pull the body text out of a renga-peers message dict. Non-string
    values in any of the candidate fields are coerced to empty so the
    caller's regex never sees a non-str (which would TypeError)."""
    for key in ("message", "text", "body"):
        value = msg.get(key)
        if isinstance(value, str):
            return value
    return ""


def _read_stdin_payload(stdin) -> dict[str, Any]:
    raw = stdin.read()
    if not raw or not raw.strip():
        return {}
    return json.loads(raw)


def run_gate(args: argparse.Namespace, *, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    if args.print_initial_prompt:
        # Plain text on stdout — the dispatcher pipes this into
        # mcp__renga-peers__send_message before the first attempt.
        stdout.write(f"{args.task_id} の完了報告は届いていますか？\n")
        stdout.flush()
        return 0

    if args.attempt < 1 or args.attempt > args.max_attempts:
        _emit({
            "status": "error",
            "reason": (f"attempt {args.attempt} out of range "
                       f"[1, {args.max_attempts}]"),
            "attempts": args.attempt,
            "received_at": None,
            "raw": None,
        }, stdout)
        return 2

    try:
        pattern = re.compile(args.ack_pattern)
    except re.error as exc:
        _emit({
            "status": "error",
            "reason": f"invalid_ack_pattern: {exc}",
            "attempts": args.attempt,
            "received_at": None,
            "raw": None,
        }, stdout)
        return 2

    try:
        payload = _read_stdin_payload(stdin)
    except json.JSONDecodeError as exc:
        _emit({
            "status": "error",
            "reason": f"invalid_json: {exc}",
            "attempts": args.attempt,
            "received_at": None,
            "raw": None,
        }, stdout)
        return 2

    if not isinstance(payload, dict):
        _emit({
            "status": "error",
            "reason": "invalid_schema: payload must be a JSON object",
            "attempts": args.attempt,
            "received_at": None,
            "raw": None,
        }, stdout)
        return 2

    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        _emit({
            "status": "error",
            "reason": "invalid_schema: 'messages' must be a list",
            "attempts": args.attempt,
            "received_at": None,
            "raw": None,
        }, stdout)
        return 2

    state = payload.get("state") or {}
    if not isinstance(state, dict):
        state = {}
    last_secretary_body = state.get("last_secretary_body")
    last_secretary_attempt = state.get("last_secretary_attempt") or 0
    if not isinstance(last_secretary_attempt, int):
        last_secretary_attempt = 0
    if last_secretary_body is not None and not isinstance(last_secretary_body, str):
        last_secretary_body = None

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if not _is_secretary_message(msg, args.secretary):
            continue
        body = _extract_body(msg)
        last_secretary_body = body
        last_secretary_attempt = args.attempt
        if pattern.search(body):
            _emit({
                "status": "acked",
                "received_at": _now_iso(),
                "raw": body,
                "attempts": args.attempt,
            }, stdout)
            return 0

    if args.attempt >= args.max_attempts:
        if last_secretary_body is not None:
            _emit({
                "status": "replied_no_ack",
                "received_at": _now_iso(),
                "raw": last_secretary_body,
                "attempts": last_secretary_attempt,
            }, stdout)
            return 3
        _emit({
            "status": "timeout",
            "received_at": None,
            "raw": None,
            "attempts": args.max_attempts,
        }, stdout)
        return 1

    # More attempts to go — return updated state for the next invocation.
    _emit({
        "status": "polling",
        "attempts": args.attempt,
        "state": {
            "last_secretary_attempt": last_secretary_attempt,
            "last_secretary_body": last_secretary_body,
        },
    }, stdout)
    return 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Per-attempt ack judge for the dispatcher's retro "
                    "completion-report gate (Issue #285).",
    )
    p.add_argument("--task-id", required=True,
                   help="Task id used in the prompt sent to the secretary.")
    p.add_argument("--secretary", default="secretary",
                   help="Secretary pane name / id (default: secretary).")
    p.add_argument("--attempt", type=int, default=1,
                   help="Current poll attempt number, 1-indexed.")
    p.add_argument("--max-attempts", type=int, default=10,
                   help="Total number of poll attempts (default: 10).")
    p.add_argument("--ack-pattern", default=DEFAULT_ACK_PATTERN,
                   help="Regex applied to incoming message bodies to decide ack.")
    p.add_argument("--print-initial-prompt", action="store_true",
                   help="Print the templated initial prompt and exit. "
                        "Use this once before --attempt 1 to feed the "
                        "secretary message via mcp__renga-peers__send_message.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_gate(args)


if __name__ == "__main__":
    sys.exit(main())
