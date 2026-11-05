"""Tests for tools.acceptance_gate (Issue #1167, #1231): real git repos, faked CI records.

Most cases are holes a gate that only checks "green" would miss: weakening,
renaming or replacing the test, rewriting the manifest, and swapping the test
infrastructure.
"""
from __future__ import annotations

import io
import json
import sqlite3
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

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"
TRAILER = "\n\nAcceptance-Test: tests/test_calc.py"


class Repo:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.root, input=stdin, capture_output=True, text=True, check=True,
        ).stdout.strip()

    def commit(self, msg: str, files: dict[str, str | None] | None = None) -> str:
        for path, content in (files or {}).items():
            dest = self.root / path
            if content is None:
                dest.unlink()
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")


JOB = (15368, "test")


class GateTest(unittest.TestCase):
    """git checks and the pin. The CI evidence is faked to pass: the job fails at
    every commit but head, the failed run is recorded and both runs share a base
    (its own checks live in test_acceptance_ci_gate.py)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name) / "repo")
        self.base = self.repo.commit("base", {
            "calc.py": BUGGY,
            "pyproject.toml": "[project]\nname = 'x'\n",
            ".github/workflows/ci.yml": "on: push\n",
            "tests/other.py": "def test_other():\n    assert True\n",
        })
        head = lambda: self.repo.git("rev-parse", "HEAD")  # noqa: E731
        for name, fake in (
            ("ci_failed_at", lambda db, repo, number, sha: True),
            ("check_runs", lambda repo, sha: {JOB: "success" if sha == head() else "failure"}),
            ("run_bases", lambda repo, sha: {"c" * 40}),
        ):
            patcher = mock.patch.object(gate, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def run_gate(self, *extra: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = gate.main(["--repo", str(self.repo.root), "--base", self.base, "--head", "HEAD",
                            *extra]
                           + ([] if "--pr" in extra else ["--pr", "o/r#1"])
                           + ([] if "--db-path" in extra else ["--no-record"]))
        return rc, out.getvalue()

    def assert_verdict(self, verdict: str, needle: str = "") -> str:
        rc, out = self.run_gate()
        self.assertEqual(out.strip().splitlines()[-1], f"ACCEPTANCE_GATE: {verdict}", out)
        self.assertEqual(rc, gate.EXIT[verdict])
        self.assertIn(needle, out)
        return out

    def land(self, test: str = TEST, trailer: str = TRAILER, extra=None):
        self.repo.commit("test: failing test" + trailer, {"tests/test_calc.py": test, **(extra or {})})
        self.repo.commit("fix", {"calc.py": FIXED})

    # --- passes -----------------------------------------------------------
    def test_test_first_passes(self):
        self.land()
        self.assert_verdict("passed", "failed at the test commit")

    def test_change_then_revert_passes(self):
        self.land()
        self.repo.commit("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"})
        self.repo.commit("revert", {"tests/test_calc.py": TEST})
        self.assert_verdict("passed")

    def test_no_trailer_is_no_manifest(self):
        self.repo.commit("fix", {"calc.py": FIXED})
        self.assert_verdict("no-manifest")

    # --- (a) protected paths ----------------------------------------------
    def test_weakened_test_rejected(self):
        self.land()
        self.repo.commit("weaken", {"tests/test_calc.py": "def test_add():\n    assert True\n"})
        self.assert_verdict("rejected", "tests/test_calc.py")

    def test_renamed_test_rejected(self):
        self.land()
        self.repo.git("mv", "tests/test_calc.py", "tests/test_calc2.py")
        self.repo.commit("rename")
        self.assert_verdict("rejected", "protected paths changed")

    def test_deleted_test_rejected(self):
        self.land()
        self.repo.commit("delete", {"tests/test_calc.py": None})
        self.assert_verdict("rejected", "protected paths changed")

    def test_other_file_renamed_onto_test_rejected(self):
        self.land()
        self.repo.commit("drop", {"tests/test_calc.py": None})
        self.repo.git("mv", "tests/other.py", "tests/test_calc.py")
        self.repo.commit("replace")
        self.assert_verdict("rejected", "protected paths changed")

    def test_second_manifest_rejected(self):
        self.land()
        self.repo.commit("new manifest\n\nAcceptance-Test: tests/other.py")
        self.assert_verdict("rejected", "the manifest was rewritten")

    def test_infrastructure_swap_after_test_rejected(self):
        swaps = {
            "tests/conftest.py": "import pytest\n",
            "pytest.ini": "[pytest]\naddopts = -k nothing\n",
            "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-k nothing'\n",
            ".github/workflows/ci.yml": "on: workflow_dispatch\n",
            "sub/setup.cfg": "[tool:pytest]\n",
            "web/vitest.config.ts": "export default {}\n",
            "web/package.json": "{}\n",
        }
        self.land()
        for path, content in swaps.items():
            with self.subTest(path=path):
                self.repo.commit("swap", {path: content})
                self.assert_verdict("rejected", path)
                self.repo.git("reset", "-q", "--hard", "HEAD~1")

    def test_file_added_to_manifest_directory_rejected(self):
        self.land(trailer="\n\nAcceptance-Test: tests/")
        self.repo.commit("add", {"tests/test_extra.py": "def test_x():\n    pass\n"})
        self.assert_verdict("rejected", "tests/test_extra.py")

    def test_undeclared_change_in_test_commit_rejected(self):
        self.land(extra={"calc.py": FIXED})
        self.assert_verdict("rejected", "undeclared paths ['calc.py']")

    def test_missing_and_escaping_paths_rejected(self):
        for trailer, needle in (("tests/nope.py", "does not exist"), ("../x.py", "invalid manifest path")):
            with self.subTest(trailer=trailer):
                self.repo.commit("t\n\nAcceptance-Test: " + trailer)
                self.assert_verdict("rejected", needle)
                self.repo.git("reset", "-q", "--hard", self.base)

    def test_symlinked_test_rejected(self):
        blob = self.repo.git("hash-object", "-w", "--stdin", stdin="other.py")
        self.repo.git("update-index", "--add", "--cacheinfo", f"120000,{blob},tests/test_calc.py")
        self.repo.git("commit", "-q", "-m", "link" + TRAILER)
        self.assert_verdict("rejected", "symlink")

    # --- events -----------------------------------------------------------
    def fresh_db(self) -> Path:
        db = Path(self._tmp.name) / "state.db"
        conn = connect(db)
        apply_schema(conn)
        conn.close()
        return db

    def test_event_recorded(self):
        self.land()
        db = self.fresh_db()
        rc, _ = self.run_gate("--db-path", str(db), "--pr", "o/r#12", "--task", "t1")
        self.assertEqual(rc, 0)
        row = sqlite3.connect(db).execute(
            "SELECT payload_json FROM events WHERE kind = 'acceptance_gate'").fetchone()
        payload = json.loads(row[0])
        self.assertEqual((payload["result"], payload["pr"], payload["task"]), ("passed", "o/r#12", "t1"))
        self.assertEqual(payload["paths"], ["tests/test_calc.py"])

    def test_rejected_event_keeps_manifest(self):
        self.land()
        self.repo.commit("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"})
        db = self.fresh_db()
        self.assertEqual(self.run_gate("--db-path", str(db))[0], 1)
        payload = json.loads(sqlite3.connect(db).execute(
            "SELECT payload_json FROM events WHERE kind = 'acceptance_gate'").fetchone()[0])
        self.assertEqual((payload["result"], payload["paths"]), ("rejected", ["tests/test_calc.py"]))

    # --- pin (force push) ----------------------------------------------
    def rewrite_test(self, test: str) -> None:
        self.repo.git("reset", "-q", "--hard", self.base)
        self.land(test=test)

    def test_rewritten_test_commit_rejected_after_first_sight(self):
        db = self.fresh_db()
        self.land(test=TEST.replace("add(2, 2) == 4", "add(2, 2) == 5"))
        self.run_gate("--db-path", str(db), "--pr", "o/r#1")
        self.rewrite_test(TEST)  # force push with a different test
        rc, out = self.run_gate("--db-path", str(db), "--pr", "o/r#1")
        self.assertEqual(rc, 1)
        self.assertIn("rewritten since the gate first saw it", out)
        self.assertEqual(self.run_gate("--db-path", str(db), "--pr", "o/r#2")[0], 0)  # other PR

    def test_whitespace_only_rewrite_is_still_a_rewrite(self):
        db = self.fresh_db()
        self.land(test=TEST.replace("add(2, 2) == 4", "add(2, 2) == 5"))
        self.run_gate("--db-path", str(db), "--pr", "o/r#1")
        self.rewrite_test(TEST.replace("add(2, 2) == 5", "add(2,2) == 5"))
        self.assertIn("rewritten", self.run_gate("--db-path", str(db), "--pr", "o/r#1")[1])

    def test_rebase_keeps_the_pin(self):
        db = self.fresh_db()
        self.land()
        self.assertEqual(self.run_gate("--db-path", str(db), "--pr", "o/r#1")[0], 0)
        old_base = self.base
        self.repo.git("checkout", "-q", "-b", "newbase", old_base)
        self.base = self.repo.commit("base moves", {"README": "x\n"})
        self.repo.git("checkout", "-q", "main")
        self.repo.git("rebase", "-q", "newbase")
        self.assertEqual(self.run_gate("--db-path", str(db), "--pr", "o/r#1")[0], 0)

    def test_removed_manifest_rejected_after_first_sight(self):
        db = self.fresh_db()
        self.land()
        self.run_gate("--db-path", str(db), "--pr", "o/r#1")
        self.repo.git("reset", "-q", "--hard", self.base)
        self.repo.commit("fix", {"calc.py": FIXED})
        rc, out = self.run_gate("--db-path", str(db), "--pr", "o/r#1")
        self.assertEqual(rc, 1)
        self.assertIn("removed", out)

    def test_record_failure_with_pr_rejects(self):
        self.land()
        db = Path(self._tmp.name) / "bad.db"
        sqlite3.connect(db).execute("CREATE TABLE unrelated (x)").connection.commit()
        rc, out = self.run_gate("--db-path", str(db), "--pr", "o/r#1")
        self.assertEqual(rc, 1)
        self.assertIn("ACCEPTANCE_GATE: rejected", out)


class GithubReadTest(unittest.TestCase):
    """check_runs / run_bases read gh; gh failing is a Reject (fail closed)."""

    def gh(self, stdout: str = "", rc: int = 0):
        return mock.patch.object(gate.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], rc, stdout, "boom"))

    def test_latest_run_per_app_and_name(self):
        lines = "\n".join(json.dumps(r) for r in (
            {"app": 1, "name": "test", "conclusion": "failure", "completed_at": "2026-11-05T01:00:00Z"},
            {"app": 1, "name": "test", "conclusion": "success", "completed_at": "2026-11-05T02:00:00Z"},
            {"app": 2, "name": "test", "conclusion": "failure", "completed_at": "2026-11-05T03:00:00Z"},
            {"app": 1, "name": "lint", "conclusion": None, "completed_at": None},
        ))
        with self.gh(lines):
            self.assertEqual(gate.check_runs("o/r", "a" * 40),
                             {(1, "test"): "success", (2, "test"): "failure"})

    def test_bases(self):
        with self.gh('"%s"\n"%s"\n' % ("c" * 40, "c" * 40)):
            self.assertEqual(gate.run_bases("o/r", "a" * 40), {"c" * 40})

    def test_gh_failure_rejects(self):
        with self.gh(rc=1), self.assertRaisesRegex(gate.Reject, "cannot read"):
            gate.check_runs("o/r", "a" * 40)

if __name__ == "__main__":
    unittest.main()
