"""The acceptance gate judges from git and CI records only (Issue #1231).

It runs no test itself, so any language works. Before-fix failure is the CI
failure recorded at the test-only commit M; the evidence is the same CI job
failing at M and succeeding at head, both runs on the same base.
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
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.test_acceptance_gate import Repo  # noqa: E402
from tools.test_acceptance_merge_hook import record_ci  # noqa: E402

BUGGY = "export const add = (a, b) => a - b;\n"
FIXED = "export const add = (a, b) => a + b;\n"
TEST = "import { add } from '../calc.js';\ntest('add', () => expect(add(2, 2)).toBe(4));\n"
TRAILER = "\n\nAcceptance-Test: tests/calc.test.js"
TEST_JOB = (15368, "test")  # (app id, check name); 15368 is GitHub Actions
BASE_SHA = "c" * 40


class CiGateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.repo = Repo(tmp / "repo")
        self.base = self.repo.commit("base", {"calc.js": BUGGY, "package.json": "{}\n"})
        self.db = tmp / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.close()
        self.runs: dict[str, dict] = {}
        self.bases: dict[str, set] = {}

    def tearDown(self):
        self._tmp.cleanup()

    def land(self) -> tuple[str, str]:
        m = self.repo.commit("test: failing test" + TRAILER, {"tests/calc.test.js": TEST})
        head = self.repo.commit("fix", {"calc.js": FIXED})
        return m, head

    def ci(self, sha: str, runs: dict, bases=frozenset({BASE_SHA}), failed_event: bool = True):
        self.runs[sha] = runs
        self.bases[sha] = set(bases)
        if failed_event:
            record_ci(self.db, 7, "failed", sha)

    def run_gate(self, *extra: str, pr: bool = True) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(gate, "check_runs", create=True,
                               side_effect=lambda repo, sha: self.runs.get(sha, {})), \
                mock.patch.object(gate, "run_bases", create=True,
                                  side_effect=lambda repo, sha: self.bases.get(sha, set())), \
                redirect_stdout(out), redirect_stderr(err):
            rc = gate.main(["--repo", str(self.repo.root), "--base", self.base, "--head", "HEAD",
                            "--db-path", str(self.db), *(["--pr", "o/r#7"] if pr else []), *extra])
        return rc, out.getvalue()

    def assert_verdict(self, verdict: str, needle: str = "", **kw) -> str:
        rc, out = self.run_gate(**kw)
        self.assertEqual(out.strip().splitlines()[-1], f"ACCEPTANCE_GATE: {verdict}", out)
        self.assertIn(needle, out)
        return out

    def test_any_language_passes_on_ci_evidence(self):
        m, head = self.land()
        self.ci(m, {TEST_JOB: "failure"})
        self.ci(head, {TEST_JOB: "success"}, failed_event=False)
        self.assert_verdict("passed", "test")

    def test_no_failed_ci_record_at_the_test_commit_rejected(self):
        m, head = self.land()
        self.ci(m, {TEST_JOB: "failure"}, failed_event=False)
        self.ci(head, {TEST_JOB: "success"}, failed_event=False)
        self.assert_verdict("rejected", "no failed CI run recorded at the test commit")

    def test_cancelled_job_is_not_a_failure(self):
        m, head = self.land()
        self.ci(m, {TEST_JOB: "cancelled"})
        self.ci(head, {TEST_JOB: "success"}, failed_event=False)
        self.assert_verdict("rejected", "no CI job failed at the test commit and succeeded at head")

    def test_job_still_failing_at_head_rejected(self):
        m, head = self.land()
        self.ci(m, {TEST_JOB: "failure"})
        self.ci(head, {TEST_JOB: "failure"}, failed_event=False)
        self.assert_verdict("rejected", "no CI job failed at the test commit and succeeded at head")

    def test_same_name_from_another_app_does_not_match(self):
        m, head = self.land()
        self.ci(m, {(1, "test"): "failure"})
        self.ci(head, {(2, "test"): "success"}, failed_event=False)
        self.assert_verdict("rejected", "no CI job failed at the test commit and succeeded at head")

    def test_base_moved_between_the_two_runs_rejected(self):
        m, head = self.land()
        self.ci(m, {TEST_JOB: "failure"})
        self.ci(head, {TEST_JOB: "success"}, bases={"d" * 40}, failed_event=False)
        self.assert_verdict("rejected", "different bases")

    def test_unknown_base_rejected(self):
        m, head = self.land()
        self.ci(m, {TEST_JOB: "failure"}, bases=set())
        self.ci(head, {TEST_JOB: "success"}, failed_event=False)
        self.assert_verdict("rejected", "different bases")

    def test_unreadable_check_runs_rejected(self):
        m, head = self.land()
        record_ci(self.db, 7, "failed", m)
        with mock.patch.object(gate, "check_runs", create=True,
                               side_effect=gate.Reject("cannot read the check runs")):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                gate.main(["--repo", str(self.repo.root), "--base", self.base, "--head", "HEAD",
                           "--db-path", str(self.db), "--pr", "o/r#7"])
        self.assertIn("cannot read the check runs", out.getvalue())
        self.assertTrue(out.getvalue().strip().endswith("ACCEPTANCE_GATE: rejected"))

    def test_test_commit_must_be_the_first_commit_of_the_pr(self):
        """base -> break the code -> test commit -> restore: the test never failed on base."""
        self.repo.commit("break", {"calc.js": "export const add = () => 0;\n"})
        m = self.repo.commit("test" + TRAILER, {"tests/calc.test.js": TEST})
        head = self.repo.commit("restore", {"calc.js": BUGGY.replace("a - b", "a + b")})
        self.ci(m, {TEST_JOB: "failure"})
        self.ci(head, {TEST_JOB: "success"}, failed_event=False)
        self.assert_verdict("rejected", "first commit")

    def test_without_pr_there_is_no_ci_evidence(self):
        m, head = self.land()
        self.ci(m, {TEST_JOB: "failure"})
        self.ci(head, {TEST_JOB: "success"}, failed_event=False)
        self.assert_verdict("rejected", "--pr", pr=False)


if __name__ == "__main__":
    unittest.main()
