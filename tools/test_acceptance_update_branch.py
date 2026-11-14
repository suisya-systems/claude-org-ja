"""A PR brought up to date by merging the base after the test commit (Issue #1278).

When the target repository requires up-to-date branches and force push is not
allowed, the base is merged into the PR branch. The fork point then moves to
the new base, but the test commit is still the PR's first own commit and the
only commits after it that are not the PR's own came in from the base.

Real git repos; the CI evidence is faked to pass as in test_acceptance_gate.py.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import acceptance_gate as gate  # noqa: E402
from tools.test_acceptance_gate import BUGGY, FIXED, TEST, TRAILER, Repo  # noqa: E402

JOB = (15368, "test")


class UpdateBranchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Repo(Path(self._tmp.name) / "repo")
        self.repo.commit("base", {
            "calc.py": BUGGY,
            "pyproject.toml": "[project]\nname = 'x'\n",
            ".github/workflows/ci.yml": "on: push\n",
        })
        self.repo.git("checkout", "-q", "-b", "pr")
        head = lambda: self.repo.git("rev-parse", "HEAD")  # noqa: E731
        for name, fake in (
            ("ci_failed_at", lambda db, repo, number, sha: True),
            ("check_runs", lambda repo, sha: {JOB: "success" if sha == head() else "failure"}),
            ("run_bases", lambda repo, sha: {"c" * 40}),
        ):
            patcher = mock.patch.object(gate, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def move_base(self, files=None) -> None:
        """main moves on (also touching a protected path, as a real base does)."""
        self.repo.git("checkout", "-q", "main")
        self.repo.commit("base moves", files or {
            "README": "x\n", "pyproject.toml": "[project]\nname = 'x'\nversion = '2'\n"})
        self.repo.git("checkout", "-q", "pr")

    def merge_base(self, *flags: str) -> None:
        self.repo.git("merge", "-q", "--no-edit", *flags, "main")

    def land(self) -> None:
        self.repo.commit("test: failing test" + TRAILER, {"tests/test_calc.py": TEST})
        self.repo.commit("fix", {"calc.py": FIXED})

    def assert_verdict(self, verdict: str, needle: str = "") -> None:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = gate.main(["--repo", str(self.repo.root), "--base", "main", "--head", "pr",
                            "--pr", "o/r#1", "--no-record"])
        self.assertEqual(out.getvalue().strip().splitlines()[-1], f"ACCEPTANCE_GATE: {verdict}",
                         out.getvalue())
        self.assertEqual(rc, gate.EXIT[verdict])
        self.assertIn(needle, out.getvalue())

    def test_base_merged_after_the_test_commit_passes(self):
        self.land()
        self.move_base()
        self.merge_base()
        self.assert_verdict("passed")

    def test_base_merged_twice_after_the_test_commit_passes(self):
        self.land()
        for n in (1, 2):
            self.move_base({"README": f"{n}\n"})
            self.merge_base()
        self.assert_verdict("passed")

    def test_base_merged_before_the_test_commit_rejected(self):
        self.move_base()
        self.merge_base("--no-ff")
        self.land()
        self.assert_verdict("rejected", "must be the first commit")

    def test_own_commit_before_the_test_commit_rejected(self):
        self.repo.commit("own work first", {"other.py": "x = 1\n"})
        self.land()
        self.move_base()
        self.merge_base()
        self.assert_verdict("rejected", "must be the first commit")

    def test_other_branch_merged_after_the_test_commit_rejected(self):
        """Only the base may be merged in: another branch's commits are the PR's own."""
        self.land()
        self.repo.git("checkout", "-q", "-b", "side", "main")
        self.repo.commit("side work", {"side.py": "x = 1\n"})
        self.repo.git("checkout", "-q", "pr")
        self.repo.git("merge", "-q", "--no-edit", "side")
        self.assert_verdict("rejected", "must be the first commit")

    def test_protected_change_by_the_pr_after_the_merge_rejected(self):
        self.land()
        self.move_base()
        self.merge_base()
        self.repo.commit("weaken", {"pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-k x'\n"})
        self.assert_verdict("rejected", "pyproject.toml")

    def test_test_changed_after_the_merge_rejected(self):
        self.land()
        self.move_base()
        self.merge_base()
        self.repo.commit("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"})
        self.assert_verdict("rejected", "tests/test_calc.py")


if __name__ == "__main__":
    unittest.main()
