"""Tests for tools/dispatcher_retro_gate.py (Issue #285).

The CLI is a one-shot per-attempt ack judge: it reads
``{"messages": [...], "state": {...}}`` from stdin, applies the ack
regex, and prints either a terminal verdict (acked / replied_no_ack /
timeout / error) or a ``polling`` verdict carrying state for the next
invocation. Tests drive it via ``subprocess.run`` so the CLI contract
is exercised end-to-end.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "dispatcher_retro_gate.py"


def _run(stdin_payload: dict | None, *, attempt: int = 1, max_attempts: int = 10,
         extra_args: list[str] | None = None,
         task_id: str = "issue-285-test") -> subprocess.CompletedProcess:
    args = [
        sys.executable, str(SCRIPT),
        "--task-id", task_id,
        "--attempt", str(attempt),
        "--max-attempts", str(max_attempts),
    ]
    if extra_args:
        args.extend(extra_args)
    stdin_text = ""
    if stdin_payload is not None:
        stdin_text = json.dumps(stdin_payload, ensure_ascii=False) + "\n"
    return subprocess.run(
        args, input=stdin_text, capture_output=True, text=True,
        timeout=15, encoding="utf-8",
    )


def _final(proc: subprocess.CompletedProcess) -> dict:
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"no stdout from CLI; stderr={proc.stderr!r}"
    return json.loads(lines[-1])


class DispatcherRetroGateTests(unittest.TestCase):

    # --- Stage 2 baseline cases mandated by the issue brief ------------

    def test_ack_on_first_poll(self) -> None:
        proc = _run(
            {"messages": [
                {"from_id": "secretary", "message": "完了報告は届いています"},
            ]},
            attempt=1,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "acked")
        self.assertEqual(final["attempts"], 1)
        self.assertIn("届い", final["raw"])

    def test_ack_on_third_poll(self) -> None:
        # Driver loops three invocations, threading state through.
        state = None

        # Attempt 1: empty.
        proc = _run({"messages": [], "state": state}, attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        f1 = _final(proc)
        self.assertEqual(f1["status"], "polling")
        state = f1["state"]

        # Attempt 2: noise from non-secretary.
        proc = _run({"messages": [{"from_id": "other", "message": "noise"}],
                      "state": state}, attempt=2)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        state = _final(proc)["state"]

        # Attempt 3: real ack.
        proc = _run({"messages": [{"from_id": "secretary",
                                   "message": "ack received"}],
                     "state": state}, attempt=3)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "acked")
        self.assertEqual(final["attempts"], 3)
        self.assertEqual(final["raw"], "ack received")

    def test_timeout_after_max_attempts(self) -> None:
        # All 10 attempts return empty messages and no secretary reply.
        state = None
        for n in range(1, 10):
            proc = _run({"messages": [], "state": state}, attempt=n)
            self.assertEqual(proc.returncode, 4, msg=proc.stderr)
            state = _final(proc)["state"]
        proc = _run({"messages": [], "state": state}, attempt=10)
        self.assertEqual(proc.returncode, 1, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "timeout")
        self.assertEqual(final["attempts"], 10)
        self.assertIsNone(final["received_at"])
        self.assertIsNone(final["raw"])

    # --- Hardening cases from Codex review rounds ----------------------

    def test_anonymous_message_does_not_ack(self) -> None:
        # Body matches the regex but no sender attribution → not ack.
        proc = _run({"messages": [{"message": "届いてます"}]}, attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_non_secretary_messages_do_not_ack(self) -> None:
        proc = _run({"messages": [{"from_id": "worker-x",
                                    "message": "届いてます"}]}, attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)

    def test_invalid_ack_pattern_returns_error(self) -> None:
        proc = _run(None, extra_args=["--ack-pattern", "("])
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "error")
        self.assertIn("invalid_ack_pattern", final["reason"])

    def test_malformed_messages_payload_returns_error(self) -> None:
        proc = _run({"messages": "oops"}, attempt=1)
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "error")
        self.assertIn("invalid_schema", final["reason"])

    def test_payload_must_be_object(self) -> None:
        # JSON list at top level instead of object.
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--task-id", "x",
             "--attempt", "1", "--max-attempts", "10"],
            input="[1,2,3]\n", capture_output=True, text=True,
            timeout=15, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)
        self.assertIn("invalid_schema", _final(proc)["reason"])

    def test_non_dict_message_entries_are_skipped(self) -> None:
        # Mixed list: stray int, then real secretary ack.
        proc = _run({"messages": [42, {"from_id": "secretary",
                                        "message": "届きました"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "acked")

    def test_non_string_body_does_not_crash(self) -> None:
        proc = _run({"messages": [
            {"from_id": "secretary", "message": 42},
            {"from_id": "secretary", "message": "ack"},
        ]}, attempt=1)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(_final(proc)["raw"], "ack")

    def test_secretary_replies_without_ack_regex(self) -> None:
        # Secretary replies but bodies never match — at the final
        # attempt the verdict must be replied_no_ack (exit 3), not
        # timeout, so the dispatcher does not jump to the
        # secretary_unreachable fallback.
        state = None
        # Attempt 1: secretary says "確認します"
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "確認します"}],
                     "state": state}, attempt=1, max_attempts=3)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        state = _final(proc)["state"]
        self.assertEqual(state["last_secretary_body"], "確認します")
        self.assertEqual(state["last_secretary_attempt"], 1)

        # Attempt 2: empty.
        proc = _run({"messages": [], "state": state},
                    attempt=2, max_attempts=3)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        state = _final(proc)["state"]

        # Attempt 3 (final): secretary says "見当たりません"
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "見当たりません"}],
                     "state": state}, attempt=3, max_attempts=3)
        self.assertEqual(proc.returncode, 3, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "replied_no_ack")
        self.assertEqual(final["raw"], "見当たりません")
        self.assertEqual(final["attempts"], 3)

    def test_custom_ack_pattern(self) -> None:
        proc = _run({"messages": [
            {"from_id": "secretary", "message": "CONFIRMED-285"},
        ]}, attempt=1, extra_args=["--ack-pattern", r"CONFIRMED-\d+"])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(_final(proc)["raw"], "CONFIRMED-285")

    def test_attempt_out_of_range_returns_error(self) -> None:
        proc = _run({"messages": []}, attempt=11, max_attempts=10)
        self.assertEqual(proc.returncode, 2, msg=proc.stderr)
        self.assertIn("out of range", _final(proc)["reason"])

    # --- --print-initial-prompt mode -----------------------------------

    def test_print_initial_prompt(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--task-id", "issue-285-test",
             "--print-initial-prompt"],
            capture_output=True, text=True, timeout=15, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("issue-285-test", proc.stdout)
        self.assertIn("完了報告", proc.stdout)


if __name__ == "__main__":
    unittest.main()
