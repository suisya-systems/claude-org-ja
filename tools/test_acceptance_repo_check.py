"""Both kinds of test-first evidence through a stubbed gh (Issue #1274).

1. The red run: a failed CI run recorded at the test commit, and the same job
   failing there and passing at head.
2. The repository's own acceptance check: the base names a check in
   ``.github/acceptance-check`` (paddock's ``acceptance`` job proves inside the
   PR that the declared test fails at the test commit and passes at head);
   that check passing at head stands in for the red run.

Only ``gh`` is stubbed; git runs for real.
"""
from __future__ import annotations

import io
import json
import subprocess
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
CONFIG = ".github/acceptance-check"
ACTIONS = 15368  # GitHub Actions app id
BASE_SHA = "c" * 40
T1 = "2026-11-12T01:00:00Z"
_REAL_RUN = subprocess.run


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        gate.pr_runs.cache_clear()
        self.addCleanup(gate.pr_runs.cache_clear)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.repo = Repo(tmp / "repo")
        self.db = tmp / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.close()
        self.checks: dict[str, list[tuple[str, str]]] = {}  # sha -> [(check name, conclusion)]
        self.gh_calls: list[list[str]] = []

    def start(self, config: str | None) -> None:
        files = {"calc.js": BUGGY, "package.json": "{}\n"}
        if config is not None:
            files[CONFIG] = config
        self.base = self.repo.commit("base", files)

    def land(self) -> tuple[str, str]:
        m = self.repo.commit("test: failing test" + TRAILER, {"tests/calc.test.js": TEST})
        head = self.repo.commit("fix", {"calc.js": FIXED})
        return m, head

    def gh(self, cmd, *args, **kw):
        """Answer ``gh api --paginate <path> --jq <jq>`` with the jq-shaped lines."""
        if cmd[0] != "gh":
            return _REAL_RUN(cmd, *args, **kw)
        self.gh_calls.append(cmd)
        path = cmd[3]
        if "/actions/runs?" in path:
            sha = path.split("head_sha=", 1)[1].split("&", 1)[0]
            rows = [{"wf": 1, "suite": 10, "created": T1, "bases": [BASE_SHA]}] if sha in self.checks else []
        else:
            sha = path.split("/commits/", 1)[1].split("/", 1)[0]
            rows = [{"app": ACTIONS, "name": name, "conclusion": c, "completed_at": T1, "suite": 10}
                    for name, c in self.checks.get(sha, [])]
        return subprocess.CompletedProcess(cmd, 0, "".join(json.dumps(r) + "\n" for r in rows), "")

    def run_gate(self) -> str:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(gate.subprocess, "run", side_effect=self.gh), \
                redirect_stdout(out), redirect_stderr(err):
            rc = gate.main(["--repo", str(self.repo.root), "--base", self.base, "--head", "HEAD",
                            "--db-path", str(self.db), "--pr", "o/r#7"])
        text = out.getvalue()
        verdict = text.strip().splitlines()[-1].removeprefix("ACCEPTANCE_GATE: ")
        self.assertEqual(rc, gate.EXIT[verdict], text)
        return text

    def assert_verdict(self, verdict: str, needle: str = "") -> str:
        out = self.run_gate()
        self.assertEqual(out.strip().splitlines()[-1], f"ACCEPTANCE_GATE: {verdict}", out)
        self.assertIn(needle, out)
        return out

    # --- evidence 1: the red run at the test commit ------------------------
    def test_red_run_at_the_test_commit_passes(self):
        self.start(config=None)
        m, head = self.land()
        record_ci(self.db, 7, "failed", m)
        self.checks[m] = [("test", "failure")]
        self.checks[head] = [("test", "success")]
        self.assert_verdict("passed", "failed at the test commit")

    def test_red_run_still_counts_when_the_repo_names_a_check(self):
        self.start(config="acceptance\n")
        m, head = self.land()
        record_ci(self.db, 7, "failed", m)
        self.checks[m] = [("test", "failure"), ("acceptance", "failure")]
        self.checks[head] = [("test", "success"), ("acceptance", "failure")]
        self.assert_verdict("passed", "failed at the test commit")

    # --- evidence 2: the repository's own acceptance check -----------------
    def test_repo_acceptance_check_passing_at_head_passes(self):
        """paddock #250: test commit and fix pushed together, no red run, acceptance green."""
        self.start(config="acceptance\n")
        m, head = self.land()
        self.checks[head] = [("test", "success"), ("acceptance", "success")]
        out = self.assert_verdict("passed", "acceptance")
        self.assertIn(head[:12], out)
        self.assertFalse(any(f"/commits/{m}/" in c[3] for c in self.gh_calls),
                         "the test commit's CI is not needed with the repository's evidence")

    def test_repo_acceptance_check_failing_at_head_without_red_run_rejected(self):
        self.start(config="acceptance\n")
        _, head = self.land()
        self.checks[head] = [("test", "success"), ("acceptance", "failure")]
        self.assert_verdict("rejected", "no failed CI run recorded at the test commit")

    def test_repo_acceptance_check_missing_at_head_rejected(self):
        self.start(config="acceptance\n")
        _, head = self.land()
        self.checks[head] = [("test", "success")]
        self.assert_verdict("rejected", "no failed CI run recorded at the test commit")

    def test_check_named_only_by_the_pr_does_not_count(self):
        """The config is read from the base: a PR cannot name a check that is green anyway."""
        self.start(config=None)
        m = self.repo.commit("test: failing test" + TRAILER, {"tests/calc.test.js": TEST})
        head = self.repo.commit("fix", {"calc.js": FIXED, CONFIG: "test\n"})
        self.checks[head] = [("test", "success")]
        self.assert_verdict("rejected", "no failed CI run recorded at the test commit")
        self.assertTrue(m)

    def test_other_refusals_still_apply_with_the_repo_check(self):
        self.start(config="acceptance\n")
        self.land()
        head = self.repo.commit("weaken", {"tests/calc.test.js": "test('add', () => {});\n"})
        self.checks[head] = [("acceptance", "success")]
        self.assert_verdict("rejected", "protected paths changed")

    def test_malformed_config_rejected(self):
        self.start(config="acceptance\ntest\n")
        _, head = self.land()
        self.checks[head] = [("acceptance", "success"), ("test", "success")]
        self.assert_verdict("rejected", CONFIG)


if __name__ == "__main__":
    unittest.main()
