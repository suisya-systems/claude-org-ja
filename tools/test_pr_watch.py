"""Unit tests for tools/pr_watch.py (Issue #204, Issue #224).

Mocks the gh CLI subprocess via monkey-patching so the suite stays
hermetic. Verifies the journal payload shape matches the contract in
CLAUDE.local.md.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_watch  # noqa: E402


class ClassifyTests(unittest.TestCase):
    """Fallback classifier (used only when JSON probe is unavailable).

    Per `gh help exit-codes`, exit 8 is "Checks pending" — NOT failure.
    Issue #224 corrects the prior mapping.
    """

    def test_zero_is_passed(self) -> None:
        self.assertEqual(pr_watch._classify(0), "passed")

    def test_eight_is_incomplete(self) -> None:
        # gh exit 8 = "Checks pending", per gh's help text.
        self.assertEqual(pr_watch._classify(8), "incomplete")

    def test_two_is_canceled(self) -> None:
        self.assertEqual(pr_watch._classify(2), "canceled")

    def test_other_nonzero_is_incomplete(self) -> None:
        # Conservative: treat unknown gh errors as incomplete rather
        # than libelling the PR as failed.
        self.assertEqual(pr_watch._classify(1), "incomplete")
        self.assertEqual(pr_watch._classify(127), "incomplete")


def _make_fake_run(
    watch_exit: int = 0,
    checks_json: "list[dict] | None" = None,
    checks_raises: "Exception | None" = None,
):
    """Build a `subprocess.run` stub matching the call sites in pr_watch.

    Recognized commands:

    * ``gh pr view ... --json number`` → success (PR exists probe)
    * ``gh pr checks <pr> --json ...`` → ``checks_json`` (or raise)
    * ``gh pr checks <pr> --watch ...`` → returncode = ``watch_exit``
    """
    if checks_json is None:
        checks_json = [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}]

    def fake_run(cmd, *args, **kwargs):
        if "view" in cmd and "--json" in cmd and "number" in cmd:
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        if "checks" in cmd and "--json" in cmd:
            if checks_raises is not None:
                raise checks_raises
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(checks_json),
                stderr="",
            )
        # The watched run.
        return mock.Mock(returncode=watch_exit)

    return fake_run


class ArgFormTests(unittest.TestCase):
    """Both `--pr <n>` and the legacy positional form must parse identically."""

    def _run(self, argv: "list[str]") -> "tuple[int, dict]":
        fake_run = _make_fake_run(watch_exit=0)

        with TempDir() as tmp:
            journal = tmp / ".state" / "journal.jsonl"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                rc = pr_watch.main(argv)
            rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            return rc, rec

    def test_long_form(self) -> None:
        rc, rec = self._run(["--pr", "42", "--repo", "octo/repo"])
        self.assertEqual(rc, 0)
        self.assertEqual(rec["pr"], 42)

    def test_positional_form(self) -> None:
        rc, rec = self._run(["42", "--repo", "octo/repo"])
        self.assertEqual(rc, 0)
        self.assertEqual(rec["pr"], 42)

    def test_both_forms_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            pr_watch.main(["7", "--pr", "9", "--repo", "octo/repo"])


class JournalEmitTests(unittest.TestCase):
    def _run(
        self,
        tmp_journal: Path,
        gh_exit: int,
        checks_json: "list[dict] | None" = None,
        checks_raises: "Exception | None" = None,
    ) -> int:
        fake_run = _make_fake_run(
            watch_exit=gh_exit,
            checks_json=checks_json,
            checks_raises=checks_raises,
        )
        with mock.patch.object(pr_watch, "JOURNAL_PATH", tmp_journal), \
             mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(pr_watch.time, "monotonic", side_effect=[100.0, 142.0]):
            return pr_watch.main(["--pr", "205", "--repo", "octo/repo", "--interval", "5"])

    def test_passed_emits_ci_completed(self) -> None:
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

    def test_failed_status_from_check_bucket(self) -> None:
        """A `fail` bucket in the JSON probe → status=failed."""
        with TempDir() as tmp:
            journal = tmp / ".state" / "journal.jsonl"
            self._run(
                journal,
                gh_exit=8,
                checks_json=[
                    {"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                    {"name": "test", "state": "COMPLETED", "bucket": "fail"},
                ],
            )
            rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["status"], "failed")

    def test_transient_gh_error_is_not_failed(self) -> None:
        """Issue #224: gh exit 1 with all checks `pass` must classify as passed.

        Regression: the old code conflated any non-zero exit with CI
        failure, so a transient error in `gh pr checks --watch` (e.g.
        a brief network blip) would be journaled as ``status=failed``
        even when CI itself was green.
        """
        with TempDir() as tmp:
            journal = tmp / ".state" / "journal.jsonl"
            self._run(
                journal,
                gh_exit=1,
                checks_json=[
                    {"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                    {"name": "test", "state": "COMPLETED", "bucket": "pass"},
                ],
            )
            rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["status"], "passed")

    def test_pending_check_is_incomplete(self) -> None:
        """A still-running check → status=incomplete (new in #224)."""
        with TempDir() as tmp:
            journal = tmp / ".state" / "journal.jsonl"
            self._run(
                journal,
                gh_exit=0,
                checks_json=[
                    {"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                    {"name": "deploy", "state": "IN_PROGRESS", "bucket": "pending"},
                ],
            )
            rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["status"], "incomplete")

    def test_empty_checks_is_incomplete(self) -> None:
        with TempDir() as tmp:
            journal = tmp / ".state" / "journal.jsonl"
            self._run(journal, gh_exit=0, checks_json=[])
            rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(rec["status"], "incomplete")

    def test_json_probe_failure_falls_back_to_exit_code(self) -> None:
        """If `gh pr checks --json` itself fails, use exit-code mapping."""
        with TempDir() as tmp:
            journal = tmp / ".state" / "journal.jsonl"
            self._run(
                journal,
                gh_exit=0,
                checks_raises=FileNotFoundError("gh missing"),
            )
            rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
            # exit 0 → passed via _classify fallback.
            self.assertEqual(rec["status"], "passed")


class ClassifyFromChecksTests(unittest.TestCase):
    def test_all_pass(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "pass"}]
            ),
            "passed",
        )

    def test_skipping_counts_as_passed(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "skipping"}]
            ),
            "passed",
        )

    def test_fail_bucket_is_failed(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "fail"}]
            ),
            "failed",
        )

    def test_cancel_bucket_is_failed(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "cancel"}]
            ),
            "failed",
        )

    def test_pending_is_incomplete(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "pending"}]
            ),
            "incomplete",
        )

    def test_empty_is_incomplete(self) -> None:
        self.assertEqual(pr_watch._classify_from_checks([]), "incomplete")

    def test_unknown_bucket_is_incomplete(self) -> None:
        # Conservative: unrecognized bucket → don't claim "passed".
        self.assertEqual(
            pr_watch._classify_from_checks([{"bucket": "mystery"}]),
            "incomplete",
        )

    def test_case_insensitive_bucket(self) -> None:
        # gh emits lowercase, but be defensive.
        self.assertEqual(
            pr_watch._classify_from_checks([{"bucket": "PASS"}]),
            "passed",
        )


class PowerShellInterpreterProbeTests(unittest.TestCase):
    """Issue #224 (b): pr-watch.ps1 must reject Pythons that lack core_harness.

    These tests exercise the actual PowerShell script via pwsh; they are
    skipped on hosts where pwsh isn't available (e.g. minimal CI images).
    The probe logic itself is small and self-contained, so we extract just
    the Test-Interpreter function and call it with synthetic shim
    interpreters.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not cls.pwsh:
            raise unittest.SkipTest("pwsh/powershell not available")
        cls.script = (
            Path(__file__).resolve().parent / "pr-watch.ps1"
        )
        if not cls.script.exists():
            raise unittest.SkipTest("pr-watch.ps1 not found")

    def _run_probe(self, shim_body: str) -> bool:
        """Write a fake interpreter shim, source Test-Interpreter, return result."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            if os.name == "nt":
                shim = tdp / "fakepy.cmd"
                shim.write_text(shim_body, encoding="ascii")
                exe_arg = str(shim)
            else:
                shim = tdp / "fakepy"
                shim.write_text("#!/usr/bin/env bash\n" + shim_body, encoding="ascii")
                shim.chmod(0o755)
                exe_arg = str(shim)
            # We can't dot-source pr-watch.ps1 directly (it has a mandatory
            # -PR param that would error out), so extract just the
            # Test-Interpreter function block via regex and Invoke-Expression.
            extract = (
                "$src = Get-Content -Raw '" + str(self.script).replace("'", "''") + "'; "
                "if ($src -match '(?ms)function Test-Interpreter \\{.*?^\\}') "
                "{ Invoke-Expression $Matches[0] } "
                "else { Write-Error 'function not found'; exit 99 }; "
                "if (Test-Interpreter -Exe '" + exe_arg.replace("'", "''") + "' -Prefix @()) "
                "{ exit 0 } else { exit 1 }"
            )
            res = subprocess.run(
                [self.pwsh, "-NoProfile", "-Command", extract],
                capture_output=True,
                text=True,
            )
            return res.returncode == 0

    def test_version_ok_import_fail_is_rejected(self) -> None:
        if os.name == "nt":
            shim_body = (
                "@echo off\r\n"
                "if \"%~1\"==\"--version\" ( echo Python 3.10.0 & exit /b 0 )\r\n"
                "if \"%~1\"==\"-c\" ( echo ImportError 1>&2 & exit /b 1 )\r\n"
                "exit /b 2\r\n"
            )
        else:
            shim_body = (
                "if [ \"$1\" = \"--version\" ]; then echo 'Python 3.10.0'; exit 0; fi\n"
                "if [ \"$1\" = \"-c\" ]; then echo 'ImportError' 1>&2; exit 1; fi\n"
                "exit 2\n"
            )
        self.assertFalse(
            self._run_probe(shim_body),
            "Test-Interpreter should reject a Python that fails the import check",
        )

    def test_version_ok_import_ok_is_accepted(self) -> None:
        if os.name == "nt":
            shim_body = (
                "@echo off\r\n"
                "if \"%~1\"==\"--version\" ( echo Python 3.10.0 & exit /b 0 )\r\n"
                "if \"%~1\"==\"-c\" ( exit /b 0 )\r\n"
                "exit /b 2\r\n"
            )
        else:
            shim_body = (
                "if [ \"$1\" = \"--version\" ]; then echo 'Python 3.10.0'; exit 0; fi\n"
                "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
                "exit 2\n"
            )
        self.assertTrue(
            self._run_probe(shim_body),
            "Test-Interpreter should accept a Python that passes both probes",
        )


class TempDir:
    def __enter__(self) -> Path:
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        return Path(self._dir.name)

    def __exit__(self, *exc) -> None:
        self._dir.cleanup()


if __name__ == "__main__":
    unittest.main()
