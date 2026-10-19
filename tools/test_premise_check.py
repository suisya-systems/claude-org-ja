"""Unit tests for tools.premise_check (Issue #1192)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import premise_check  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

FIRES = [
    "閾値は 256K の窓を前提に決めるべきでは",
    "ディスパッチャーは 9 分で止まるはず",
    "使用率 80% で引き継げばいいよね",
    "ORG_TRANSPORT=broker にしてあるよ",
    "timeout: 900 のままだと足りない",
    "既定は renga だよね",
    "それって仕様でしょ",
]
QUIET = [
    "進めて",
    "OK、マージして",
    "1 番でお願い",
    "次の仕事候補を出して",
    "ありがとう",
    "PR #1193 の続きをやって",
    '<channel source="renga-peers" from_id="3">824 passed、9 分で完了</channel>',
    "<task-notification>\n<status>completed</status>\n"
    "<summary>Agent finished: 824 passed in 9 分</summary>\n</task-notification>",
]


class HasPremiseTest(unittest.TestCase):
    def test_fires(self) -> None:
        for prompt in FIRES:
            with self.subTest(prompt=prompt):
                self.assertTrue(premise_check.has_premise(prompt))

    def test_quiet(self) -> None:
        for prompt in QUIET:
            with self.subTest(prompt=prompt):
                self.assertFalse(premise_check.has_premise(prompt))


class HookTest(unittest.TestCase):
    def _run(self, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(REPO_ROOT / ".hooks" / "remind-verify-premise.sh")],
            input=stdin, capture_output=True, text=True, encoding="utf-8")

    def test_prints_reminder(self) -> None:
        r = self._run(json.dumps({"prompt": FIRES[0]}, ensure_ascii=False))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), premise_check.REMINDER)

    def test_silent_and_never_blocks(self) -> None:
        for stdin in (json.dumps({"prompt": "進めて"}, ensure_ascii=False),
                      "", "not json", "[]", '{"prompt": 1}'):
            with self.subTest(stdin=stdin):
                r = self._run(stdin)
                self.assertEqual(r.returncode, 0)
                self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
