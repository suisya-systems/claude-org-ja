"""The pre-merge hook follows the merge approval mode for a PR without an
acceptance test (Issue #1236): ``acceptance`` denies ``no-manifest``,
``human`` records it and lets it through, ``passed`` is allowed in both.

The gate run and gh / git are faked; the state DB is a temp file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import acceptance_merge_hook as hook  # noqa: E402
from tools.test_acceptance_merge_hook import SHA, merge_cmd, record_ci  # noqa: E402


class ModeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        db = self.root / "state.db"
        self._env = mock.patch.dict(os.environ, {"STATE_DB_PATH": str(db)})
        self._env.start()
        record_ci(db, 7, "passed", SHA)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def decide(self, mode: str | None, verdict: str, diff: str = "tools/x.py\n") -> str:
        config = self.root / "registry" / "org-config.md"
        config.unlink(missing_ok=True)
        if mode is not None:
            config.parent.mkdir(exist_ok=True)
            config.write_text(f"# config\n\nmerge_approval_mode: {mode}\n", encoding="utf-8")
        rc = {"passed": 0, "rejected": 1, "no-manifest": 3}[verdict]

        def fake_run(cmd, cwd, deadline, limit):
            if cmd[:2] == ["gh", "pr"]:
                out = json.dumps({"number": 7, "url": "https://github.com/o/r/pull/7",
                                  "baseRefName": "main", "headRefOid": SHA})
                return subprocess.CompletedProcess(cmd, 0, out, "")
            if cmd[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(cmd, 0, SHA + "\n", "")
            if cmd[:2] == ["git", "diff"]:
                return subprocess.CompletedProcess(cmd, 0, diff, "")
            return subprocess.CompletedProcess(cmd, rc, f"reason\nACCEPTANCE_GATE: {verdict}\n", "")

        with mock.patch.object(hook, "run", side_effect=fake_run):
            return hook.decide(merge_cmd(), self.root)

    def test_acceptance_denies_no_manifest(self):
        with self.assertRaises(hook.Deny) as cm:
            self.decide("acceptance", "no-manifest")
        msg = str(cm.exception)
        self.assertIn("no-manifest", msg)
        self.assertIn("GitHub", msg)
        self.assertIn("human", msg)

    def outcome(self, mode: str, verdict: str, diff: str) -> str:
        try:
            return self.decide(mode, verdict, diff)
        except hook.Deny as exc:
            return f"DENY: {exc}"

    def test_acceptance_allows_docs_only_no_manifest(self):
        self.assertEqual(self.outcome("acceptance", "no-manifest", "README.md\ndocs/a/b.txt\n"), "ALLOW")

    def test_acceptance_denies_mixed_diff_no_manifest(self):
        self.assertIn("no-manifest", self.outcome("acceptance", "no-manifest", "README.md\ntools/x.py\n"))

    def test_acceptance_denies_empty_diff_no_manifest(self):
        self.assertIn("no-manifest", self.outcome("acceptance", "no-manifest", ""))

    def test_human_allows_no_manifest(self):
        self.assertEqual(self.decide("human", "no-manifest"), "ALLOW")
        self.assertEqual(self.decide(None, "no-manifest"), "ALLOW")  # no config = human

    def test_passed_is_allowed(self):
        self.assertEqual(self.decide("acceptance", "passed"), "ALLOW")
        self.assertEqual(self.decide("human", "passed"), "ALLOW")

    def test_rejected_is_denied(self):
        for mode in ("acceptance", "human"):
            with self.assertRaisesRegex(hook.Deny, "rejected"):
                self.decide(mode, "rejected")


if __name__ == "__main__":
    unittest.main()
