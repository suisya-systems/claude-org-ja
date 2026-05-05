"""Tests for tools/dispatcher_retro_gate.py (Issue #285).

The CLI is a stdin/stdout JSON co-routine: it emits "action" prompts on
stdout, the dispatcher pipes ``check_messages`` results back on stdin,
and the final "status" line on stdout decides retro continuation.

These tests exercise the script end-to-end via subprocess with
``--interval-seconds 0`` so the polling cadence does not slow CI.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "dispatcher_retro_gate.py"


def _run(stdin_payloads: list[dict], extra_args: list[str] | None = None,
         task_id: str = "issue-285-test") -> subprocess.CompletedProcess:
    args = [
        sys.executable, str(SCRIPT),
        "--task-id", task_id,
        "--interval-seconds", "0",
        "--timeout-attempts", "10",
    ]
    if extra_args:
        args.extend(extra_args)
    stdin_text = "".join(json.dumps(p, ensure_ascii=False) + "\n"
                         for p in stdin_payloads)
    return subprocess.run(
        args, input=stdin_text, capture_output=True, text=True,
        timeout=15, encoding="utf-8",
    )


def _parse(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


class DispatcherRetroGateTests(unittest.TestCase):

    def test_ack_on_first_poll(self) -> None:
        stdin = [{"messages": [
            {"from_id": "secretary", "message": "完了報告は届いています"},
        ]}]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        events = _parse(proc.stdout)
        # Order: send_initial, check_messages(attempt=1), status=acked.
        self.assertEqual(events[0]["action"], "send_initial")
        self.assertEqual(events[0]["to_id"], "secretary")
        self.assertIn("issue-285-test", events[0]["message"])
        self.assertEqual(events[1], {"action": "check_messages", "attempt": 1})
        self.assertEqual(events[-1]["status"], "acked")
        self.assertEqual(events[-1]["attempts"], 1)
        self.assertIn("届いて", events[-1]["raw"])
        self.assertIsNotNone(events[-1]["received_at"])

    def test_ack_on_third_poll(self) -> None:
        stdin = [
            {"messages": []},
            {"messages": [{"from_id": "other", "message": "noise"}]},
            {"messages": [{"from_id": "secretary", "message": "ack received"}]},
        ]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        events = _parse(proc.stdout)
        check_msgs = [e for e in events if e.get("action") == "check_messages"]
        self.assertEqual([e["attempt"] for e in check_msgs], [1, 2, 3])
        self.assertEqual(events[-1]["status"], "acked")
        self.assertEqual(events[-1]["attempts"], 3)
        self.assertEqual(events[-1]["raw"], "ack received")

    def test_timeout_after_max_attempts(self) -> None:
        stdin = [{"messages": []} for _ in range(10)]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 1, msg=proc.stderr)
        events = _parse(proc.stdout)
        check_msgs = [e for e in events if e.get("action") == "check_messages"]
        self.assertEqual(len(check_msgs), 10)
        self.assertEqual(events[-1]["status"], "timeout")
        self.assertEqual(events[-1]["attempts"], 10)
        self.assertIsNone(events[-1]["received_at"])
        self.assertIsNone(events[-1]["raw"])

    def test_non_secretary_messages_do_not_ack(self) -> None:
        # Secretary-pattern body but from a different sender — must not ack.
        stdin = [
            {"messages": [{"from_id": "worker-x", "message": "届いてます"}]},
        ] + [{"messages": []} for _ in range(9)]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 1, msg=proc.stderr)
        events = _parse(proc.stdout)
        self.assertEqual(events[-1]["status"], "timeout")

    def test_invalid_ack_pattern_returns_error(self) -> None:
        # No stdin needed — the failure happens before the first poll.
        proc = _run([], extra_args=["--ack-pattern", "("])
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)
        events = _parse(proc.stdout)
        self.assertEqual(events[-1]["status"], "error")
        self.assertIn("invalid_ack_pattern", events[-1]["reason"])

    def test_malformed_messages_payload_returns_error(self) -> None:
        # 'messages' is a string instead of a list.
        proc = _run([{"messages": "oops"}])
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)
        events = _parse(proc.stdout)
        self.assertEqual(events[-1]["status"], "error")
        self.assertIn("invalid_schema", events[-1]["reason"])

    def test_non_dict_message_entries_are_skipped(self) -> None:
        # Mixed list: a stray int followed by a real secretary ack must
        # neither crash nor mask the ack.
        stdin = [{"messages": [42, {"from_id": "secretary",
                                    "message": "届きました"}]}]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        events = _parse(proc.stdout)
        self.assertEqual(events[-1]["status"], "acked")

    def test_anonymous_message_does_not_ack(self) -> None:
        # Body matches the ack regex but no sender attribution.
        stdin = [{"messages": [{"message": "届いてます"}]}] + \
                [{"messages": []} for _ in range(9)]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 1, msg=proc.stderr)
        events = _parse(proc.stdout)
        self.assertEqual(events[-1]["status"], "timeout")

    def test_secretary_replies_without_ack_regex(self) -> None:
        # Secretary replied across attempts, but no body matched the ack
        # regex — must surface as replied_no_ack (exit 3), not timeout, so
        # the dispatcher does not jump to the secretary_unreachable fallback.
        stdin = [
            {"messages": [{"from_id": "secretary", "message": "確認します"}]},
            {"messages": []},
            {"messages": [{"from_id": "secretary",
                            "message": "見当たりません"}]},
        ] + [{"messages": []} for _ in range(7)]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 3, msg=proc.stderr)
        events = _parse(proc.stdout)
        final = events[-1]
        self.assertEqual(final["status"], "replied_no_ack")
        # Reports the LAST secretary body seen + the attempt it arrived on.
        self.assertEqual(final["raw"], "見当たりません")
        self.assertEqual(final["attempts"], 3)

    def test_non_string_body_does_not_crash(self) -> None:
        # message field is an int; pattern.search would TypeError if the
        # body wasn't coerced. The script must skip the bad body and
        # continue to the next message rather than die with a traceback.
        stdin = [{"messages": [
            {"from_id": "secretary", "message": 42},
            {"from_id": "secretary", "message": "ack"},
        ]}]
        proc = _run(stdin)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        events = _parse(proc.stdout)
        self.assertEqual(events[-1]["status"], "acked")
        self.assertEqual(events[-1]["raw"], "ack")

    def test_custom_ack_pattern(self) -> None:
        stdin = [{"messages": [
            {"from_id": "secretary", "message": "CONFIRMED-285"},
        ]}]
        proc = _run(stdin, extra_args=["--ack-pattern", r"CONFIRMED-\d+"])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        events = _parse(proc.stdout)
        self.assertEqual(events[-1]["status"], "acked")
        self.assertEqual(events[-1]["raw"], "CONFIRMED-285")


if __name__ == "__main__":
    unittest.main()
