"""Issue #1244: a dispatch states whether it carries an acceptance test.

When ``merge_approval_mode`` is ``acceptance``, ``preview`` / ``apply``
without ``--acceptance-test`` stop before writing anything, so a forgotten
declaration is caught by the tool rather than by the secretary's memory.
"""
from __future__ import annotations

import re
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import tests.test_gen_delegate_payload as base

gdp = base.gdp


class TestIssue1244AcceptanceFlag(unittest.TestCase):
    setUp = base.TestIssue928DelegateSentEvent.setUp
    tearDown = base.TestIssue928DelegateSentEvent.tearDown
    _git = base.TestIssue928DelegateSentEvent._git
    _out = base.TestIssue928DelegateSentEvent._out

    def _set_mode(self, mode: str) -> None:
        cfg = self.sb.claude_org_root / "registry" / "org-config.md"
        cfg.write_text(
            cfg.read_text(encoding="utf-8")
            + f"## Merge Approval\nmerge_approval_mode: {mode}\n",
            encoding="utf-8",
        )

    def _main(self, cmd, task_id, *extra):
        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main([
                cmd,
                "--task-id", task_id,
                "--project-slug", "claude-org-ja",
                "--description", "do the thing",
                "--impl-guidance", "keep it small",
                "--claude-org-root", str(self.sb.claude_org_root),
                "--state-db-path", str(self.sb.db_path),
                *(["--skip-settings"] if cmd == "apply" else []),
                *extra,
            ])
        return rc, buf.getvalue()

    def _ok(self, cmd, task_id, *extra):
        """Run a command that must succeed; a refusal is an assertion failure."""
        try:
            rc, out = self._main(cmd, task_id, *extra)
        except SystemExit as e:
            self.fail(f"{cmd} refused: {e}")
        self.assertEqual(rc, 0)
        return out

    def _brief(self, out: str) -> str:
        path = Path(re.search(r"^brief: (.+)$", out, re.M).group(1))
        return path.read_text(encoding="utf-8")

    def _runs(self, task_id):
        return [r for r in self.sb.list_runs() if r["task_id"] == task_id]

    def test_acceptance_mode_without_flag_stops_and_writes_nothing(self):
        self._set_mode("acceptance")
        for cmd in ("preview", "apply"):
            with self.assertRaises(SystemExit) as cm:
                self._main(cmd, "acc-missing")
            msg = str(cm.exception)
            self.assertIn("--acceptance-test yes", msg)
            self.assertIn("--acceptance-test no", msg)
            # The default answer is shown first.
            self.assertLess(msg.index("--acceptance-test yes"),
                            msg.index("--acceptance-test no"))
        self.assertEqual(self._runs("acc-missing"), [])
        worker_dir = self.sb.claude_org_root / ".worktrees" / "acc-missing"
        self.assertFalse(worker_dir.exists())

    def test_minimal_depth_is_exempt(self):
        self._set_mode("acceptance")
        self._ok("apply", "acc-minimal", "--verification-depth", "minimal")

    def test_yes_renders_the_declaration_into_the_brief(self):
        self._set_mode("acceptance")
        out = self._ok("apply", "acc-yes", "--acceptance-test", "yes")
        self.assertIn("受け入れテスト: あり", self._brief(out))

    def test_no_with_reason_keeps_the_reason_in_brief_and_body(self):
        self._set_mode("acceptance")
        reason = "文書だけの変更"
        out = self._ok("preview", "acc-no-pv",
                       "--acceptance-test", "no",
                       "--acceptance-reason", reason)
        self.assertIn(reason, out.split("--- Artifacts")[0])
        out = self._ok("apply", "acc-no",
                       "--acceptance-test", "no",
                       "--acceptance-reason", reason)
        brief = self._brief(out)
        self.assertIn(reason, brief)
        # The brief template itself quotes 「受け入れテスト: あり」, so only
        # the implementation-guidance section is checked.
        guidance = brief.split("### 実装ガイダンス")[1].split("\n## ")[0]
        self.assertIn(reason, guidance)
        self.assertNotIn("受け入れテスト: あり", guidance)

    def test_no_without_reason_is_refused(self):
        self._set_mode("acceptance")
        with self.assertRaises(SystemExit) as cm:
            self._main("apply", "acc-no-reason", "--acceptance-test", "no")
        self.assertIn("--acceptance-reason", str(cm.exception.code))
        self.assertEqual(self._runs("acc-no-reason"), [])


if __name__ == "__main__":
    unittest.main()
