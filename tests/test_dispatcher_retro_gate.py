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

    # --- Issue #584: completion-phrasing acks in DEFAULT_ACK_PATTERN ----

    def test_secretary_merged_phrase_acks(self) -> None:
        # "マージ済み" from the secretary must trip the default pattern so
        # the gate passes without a per-invocation --ack-pattern override.
        proc = _run({"messages": [
            {"from_id": "secretary", "message": "PR #584 マージ済みです"},
        ]}, attempt=1)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "acked")
        self.assertIn("マージ済み", final["raw"])

    def test_secretary_kanryo_phrase_acks(self) -> None:
        # "完了" from the secretary (without any 届い/受領 token) acks.
        proc = _run({"messages": [
            {"from_id": "secretary", "message": "対応完了しました"},
        ]}, attempt=1)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        final = _final(proc)
        self.assertEqual(final["status"], "acked")
        self.assertIn("完了", final["raw"])

    def test_non_secretary_completion_phrase_does_not_ack(self) -> None:
        # The same completion wording from a non-secretary sender must
        # still be gated out by _is_secretary_message (no false positive).
        proc = _run({"messages": [
            {"from_id": "worker-x", "message": "マージ済み、完了しました"},
        ]}, attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_kanryo_houkoku_negative_reply_does_not_ack(self) -> None:
        # Regression: the secretary's negative reply "完了報告は見当たり
        # ません" contains 完了 (inside the 完了報告 noun) but is NOT an ack.
        # A bare 完了 in the pattern would falsely pass the gate here; the
        # guarded form must keep it polling at a non-final attempt.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "完了報告は見当たりません"}]},
                    attempt=1, max_attempts=3)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_negated_completion_verb_does_not_ack(self) -> None:
        # "まだ完了していません" — secretary says it is NOT done. The 完了
        # token is directly negated and must not ack.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "まだ完了していません"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_mikanryo_does_not_ack(self) -> None:
        # "未完了です" — 完了 preceded by 未 (incomplete) must not ack.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "タスクは未完了です"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_completion_question_does_not_ack(self) -> None:
        # "作業は完了しましたか？" — a question, not an assertion, must
        # not pass the gate (trailing 疑問助詞 か is excluded).
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "作業は完了しましたか？"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_merged_affirmative_acks(self) -> None:
        # "マージ済みです" — affirmative merged assertion acks.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "PR はマージ済みです"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "acked")

    def test_merged_negation_does_not_ack(self) -> None:
        # "マージ済みではありません" — negation of merged must not ack.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "PR はまだマージ済みではありません"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_colloquial_and_polite_negation_question_do_not_ack(self) -> None:
        # The clause-scoped guard must reject colloquial negation and
        # polite question forms, not just the literal ではありません/〜か？.
        for body in (
            "マージ済みじゃありません",          # 口語否定
            "作業は完了しましたでしょうか？",      # 丁寧疑問
            "PR はマージ済みでしょうか？",         # 丁寧疑問 (merged)
            "マージ済みか確認します",             # 確認中（疑問助詞 か）
        ):
            with self.subTest(body=body):
                proc = _run({"messages": [{"from_id": "secretary",
                                            "message": body}]}, attempt=1)
                self.assertEqual(proc.returncode, 4, msg=proc.stderr)
                self.assertEqual(_final(proc)["status"], "polling")

    def test_cross_clause_negation_does_not_ack(self) -> None:
        # "対応は完了しましたが、マージ済みではありません" — the 完了 verb is
        # affirmative but the SAME sentence (past the read-point 、) negates
        # the merge. The shared clause-scoped guard spans 、 so neither
        # 完了 nor マージ済み may ack here.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "対応は完了しましたが、マージ済みではありません"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 4, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "polling")

    def test_affirmative_with_trailing_sentence_still_acks(self) -> None:
        # The clause-scoped か/negation guard stops at the sentence
        # terminator, so an affirmative ack followed by an unrelated
        # question in the NEXT sentence still acks.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "完了しました。次は何をしますか？"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "acked")

    # --- Issue #591: ございません false-positive / 何か false-negative ----

    def test_polite_negation_gozaimasen_does_not_ack(self) -> None:
        # Regression (Issue #591 BUG-FP): the polite negation ございません
        # was NOT caught by the old ありませ stem, so an affirmative token
        # followed by ございません wrongly acked. The negation stem is now
        # ませ — the shared substring of the whole ません family — so these
        # negative replies keep polling at a non-final attempt.
        for body in (
            "マージ済みではございません",            # merged-negation, polite
            "完了済みですが報告はございません",        # 完了済 token then ございません
            "完了報告はございません",                # 完了報告 noun + polite negation
        ):
            with self.subTest(body=body):
                proc = _run({"messages": [{"from_id": "secretary",
                                            "message": body}]},
                            attempt=1, max_attempts=3)
                self.assertEqual(proc.returncode, 4, msg=proc.stderr)
                self.assertEqual(_final(proc)["status"], "polling")

    def test_affirmative_with_incidental_nanika_acks(self) -> None:
        # Regression (Issue #591 BUG-FN): an affirmative completion that
        # merely carries the indefinite 何か ("something") mid-clause was
        # wrongly suppressed by the blanket か rejection. The (?<!何)か
        # carve-out lets the real ack through.
        proc = _run({"messages": [{"from_id": "secretary",
                                    "message": "マージ済みですが何か問題あれば連絡します"}]},
                    attempt=1)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(_final(proc)["status"], "acked")

    def test_kano_negation_and_terminal_nanika_question_do_not_ack(self) -> None:
        # The 何か carve-out must NOT open a hole: a clause-terminal 何か
        # question stays suppressed, and a polite-negation 〜できかねます
        # (whose mid-word か the (?<!何)か rule still rejects) does not ack.
        for body in (
            "マージ済みは何か？",                    # terminal 何か question
            "マージ済みは何か ？",                   # terminal 何か with spacing
            "マージ済みは何か　？",                  # terminal 何か, full-width space
            "マージ済みにはできかねます",             # polite negation できかね
            "完了しましたとかいう話です",             # hearsay とか, not own completion
        ):
            with self.subTest(body=body):
                proc = _run({"messages": [{"from_id": "secretary",
                                            "message": body}]},
                            attempt=1, max_attempts=3)
                self.assertEqual(proc.returncode, 4, msg=proc.stderr)
                self.assertEqual(_final(proc)["status"], "polling")

    # --- Issue #594: _NEG_Q guard on the bare 届い/受領/受け取 tokens ------

    def test_received_token_negation_does_not_ack(self) -> None:
        # Regression (Issue #594): the receipt tokens 届い/受領/受け取 were
        # bare (no _NEG_Q guard), so a negative reply that contains one of
        # them in a negation clause wrongly acked — falsely passing the
        # gate while the completion report is actually NOT yet received.
        # The guarded form must keep polling at a non-final attempt.
        for body in (
            "完了報告はまだ届いておりません",        # 届い + polite negation
            "完了報告はまだ届いていません",          # 届い + plain polite negation
            "まだ受領しておりません",                # 受領 + polite negation
            "まだ受領していません",                  # 受領 + plain polite negation
            "完了報告を受け取っていません",          # 受け取 + negation
            "完了報告は届いていますか？",            # 届い + question 助詞 か
        ):
            with self.subTest(body=body):
                proc = _run({"messages": [{"from_id": "secretary",
                                            "message": body}]},
                            attempt=1, max_attempts=3)
                self.assertEqual(proc.returncode, 4, msg=proc.stderr)
                self.assertEqual(_final(proc)["status"], "polling")

    def test_received_token_affirmative_still_acks(self) -> None:
        # The _NEG_Q guard must not regress the affirmative receipt acks:
        # plain "届きました" / "受領しました" / "受け取りました" still pass.
        for body in (
            "完了報告が届きました",                  # 届き affirmative
            "完了報告は届いています",                # 届い affirmative
            "完了報告を受領しました",                # 受領 affirmative
            "完了報告を受け取りました",              # 受け取 affirmative
        ):
            with self.subTest(body=body):
                proc = _run({"messages": [{"from_id": "secretary",
                                            "message": body}]}, attempt=1)
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                self.assertEqual(_final(proc)["status"], "acked")

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
