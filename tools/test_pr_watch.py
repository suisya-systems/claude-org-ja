"""Unit tests for tools/pr_watch.py (Issue #204).

Mocks the gh CLI subprocess via monkey-patching so the suite stays
hermetic. Verifies the journal payload shape matches the contract in
CLAUDE.local.md.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_watch  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_zero_is_passed(self) -> None:
        self.assertEqual(pr_watch._classify(0), "passed")

    def test_eight_is_failed(self) -> None:
        self.assertEqual(pr_watch._classify(8), "failed")

    def test_two_is_canceled(self) -> None:
        self.assertEqual(pr_watch._classify(2), "canceled")

    def test_other_nonzero_is_failed(self) -> None:
        self.assertEqual(pr_watch._classify(1), "failed")
        self.assertEqual(pr_watch._classify(127), "failed")


class JournalEmitTests(unittest.TestCase):
    def _run(self, tmp_journal: Path, gh_exit: int) -> int:
        completed = mock.Mock(returncode=gh_exit)

        def fake_run(cmd, *args, **kwargs):
            # _pr_exists() probe path
            if "view" in cmd and "--json" in cmd and "number" in cmd:
                return mock.Mock(returncode=0, stdout="{}", stderr="")
            # the watched run
            return completed

        with mock.patch.object(pr_watch, "JOURNAL_PATH", tmp_journal), \
             mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(pr_watch.time, "monotonic", side_effect=[100.0, 142.0]):
            return pr_watch.main(["--pr", "205", "--repo", "octo/repo", "--interval", "5"])

    def test_passed_emits_ci_completed(self) -> None:
        with self.subTest("passed"):
            with TempDir() as tmp:
                journal = tmp / ".state" / "journal.jsonl"
                self._run(journal, gh_exit=0)
                lines = journal.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                rec = json.loads(lines[0])
                self.assertEqual(rec["event"], "ci_completed")
                self.assertEqual(rec["pr"], 205)
                self.assertEqual(rec["repo"], "octo/repo")
                self.assertEqual(rec["status"], "passed")
                self.assertEqual(rec["duration_sec"], 42)
                self.assertIn("ts", rec)

    def test_failed_status(self) -> None:
        with TempDir() as tmp:
            journal = tmp / ".state" / "journal.jsonl"
            self._run(journal, gh_exit=8)
            rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["status"], "failed")


class TempDir:
    def __enter__(self) -> Path:
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        return Path(self._dir.name)

    def __exit__(self, *exc) -> None:
        self._dir.cleanup()


if __name__ == "__main__":
    unittest.main()
