"""Tests for tools.acceptance_gate (Issue #1167): real git repos, real pytest runs.

Most cases are holes a gate that only checks "green" would miss: weakening,
renaming or replacing the test, rewriting the manifest, and swapping the test
infrastructure.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
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


@unittest.skipUnless(shutil.which("bwrap"), "the gate runs PR tests under bubblewrap")
class GateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name) / "repo")
        self.base = self.repo.commit("base", {
            "calc.py": BUGGY,
            "pyproject.toml": "[project]\nname = 'x'\n",
            ".github/workflows/ci.yml": "on: push\n",
            "tests/other.py": "def test_other():\n    assert True\n",
        })

    def tearDown(self):
        self._tmp.cleanup()

    def run_gate(self, *extra: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = gate.main(["--repo", str(self.repo.root), "--base", self.base,
                            "--head", "HEAD", "--timeout", "120", *extra]
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
        self.assert_verdict("passed", "1 test(s) fail on base and pass on head")

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

    # --- (b) base must fail -----------------------------------------------
    def test_test_passing_on_base_rejected(self):
        self.land(test="from calc import add\n\n\ndef test_add():\n    assert add(2, 0) == 2\n")
        self.assert_verdict("rejected", "does not fail on base")

    def test_conftest_fixture_in_test_commit_cannot_fake_base_failure(self):
        # The fixture patches the bug away. Base runs with it too, so base passes.
        conftest = ("import calc, pytest\n\n\n@pytest.fixture(autouse=True)\n"
                    "def _fix(monkeypatch):\n    monkeypatch.setattr(calc, 'add', lambda a, b: a + b)\n")
        self.repo.commit("t" + TRAILER, {"tests/test_calc.py": "import calc\n\n\ndef test_add():\n"
                                         "    assert calc.add(2, 2) == 4\n",
                                         "tests/conftest.py": conftest})
        self.assert_verdict("rejected", "does not fail on base")

    def test_fixture_error_on_base_does_not_count_as_failure(self):
        test = ("import calc, pytest\n\n\n@pytest.fixture\ndef fixed():\n"
                "    if calc.add(1, 1) != 2:\n        raise RuntimeError('setup')\n\n\n"
                "def test_add(fixed):\n    assert calc.add(2, 2) == 4\n")
        self.land(test=test)
        self.assert_verdict("rejected", "does not fail on base")

    def test_collection_error_on_base_does_not_count(self):
        test = "from calc import sub\n\n\ndef test_sub():\n    assert sub(2, 2) == 0\n"
        self.repo.commit("t" + TRAILER, {"tests/test_calc.py": test})
        self.repo.commit("feat", {"calc.py": BUGGY + "\n\ndef sub(a, b):\n    return a - b\n"})
        self.assert_verdict("rejected", "error during collection")

    def test_interrupted_head_run_rejected(self):
        # pytest.exit(returncode=1) after a real failure: the report looks like a
        # failing run but the suite never finished.
        test = TEST + "\n\ndef test_stop():\n    import pytest\n    pytest.exit('stop', returncode=1)\n"
        self.land(test=test)
        self.assert_verdict("rejected", "pytest was interrupted")

    def test_interrupted_base_run_rejected(self):
        test = TEST + "\n\ndef test_stop():\n    import pytest\n    pytest.exit('stop', returncode=0)\n"
        self.land(test=test)
        self.assert_verdict("rejected", "pytest was interrupted in base")

    def test_head_exit_zero_is_rejected_wherever_it_happens(self):
        # The fix exits 0 only when the code under test is imported on head:
        # at collection, inside a test after the declared one passed, and in a
        # session teardown. Each would look like a clean run by its exit code.
        cases = {
            "collection": FIXED + "\nimport pytest\npytest.exit('x', returncode=0)\n",
            "call": FIXED + "\n\ndef stop():\n    import pytest\n    pytest.exit('x', returncode=0)\n",
            "teardown": FIXED + "\n\ndef stop():\n    import pytest\n    pytest.exit('x', returncode=0)\n",
        }
        tests = {
            "collection": TEST,
            "call": TEST + "\n\ndef test_more():\n    import calc\n    getattr(calc, 'stop', lambda: None)()\n",
            "teardown": "import calc, pytest\nfrom calc import add\n\n\n@pytest.fixture(scope='session')\n"
                        "def fin():\n    yield\n    getattr(calc, 'stop', lambda: None)()\n\n\n"
                        "def test_add(fin):\n    assert add(2, 2) == 4\n",
        }
        for where, fixed in cases.items():
            with self.subTest(where=where):
                self.repo.commit("t" + TRAILER, {"tests/test_calc.py": tests[where]})
                self.repo.commit("fix", {"calc.py": fixed})
                self.assert_verdict("rejected", "pytest was interrupted in head")
                self.repo.git("reset", "-q", "--hard", self.base)

    # --- (c) head must pass -----------------------------------------------
    def test_head_still_failing_rejected(self):
        self.repo.commit("t" + TRAILER, {"tests/test_calc.py": TEST})
        self.assert_verdict("rejected", "does not pass on head")

    def test_skipped_test_on_head_rejected(self):
        test = TEST + "\n\ndef test_later():\n    import pytest\n    pytest.skip('later')\n"
        self.land(test=test)
        self.assert_verdict("rejected", "does not pass on head")

    def test_manifest_without_pytest_file_rejected(self):
        self.repo.commit("t\n\nAcceptance-Test: tests/other.py\nAcceptance-Test: web/a.test.ts",
                         {"tests/other.py": "x = 1\n", "web/a.test.ts": "it()\n"})
        self.assert_verdict("rejected", "not supported")

    # --- events -----------------------------------------------------------
    def test_event_recorded(self):
        self.land()
        db = Path(self._tmp.name) / "state.db"
        conn = connect(db)
        apply_schema(conn)
        conn.close()
        rc, _ = self.run_gate("--db-path", str(db), "--pr", "12", "--task", "t1")
        self.assertEqual(rc, 0)
        row = sqlite3.connect(db).execute(
            "SELECT payload_json FROM events WHERE kind = 'acceptance_gate'").fetchone()
        payload = json.loads(row[0])
        self.assertEqual((payload["result"], payload["pr"], payload["task"]), ("passed", "12", "t1"))
        self.assertEqual(payload["paths"], ["tests/test_calc.py"])

    def test_rejected_event_keeps_manifest(self):
        self.land()
        self.repo.commit("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"})
        db = Path(self._tmp.name) / "state.db"
        conn = connect(db)
        apply_schema(conn)
        conn.close()
        self.assertEqual(self.run_gate("--db-path", str(db))[0], 1)
        payload = json.loads(sqlite3.connect(db).execute(
            "SELECT payload_json FROM events WHERE kind = 'acceptance_gate'").fetchone()[0])
        self.assertEqual((payload["result"], payload["paths"]), ("rejected", ["tests/test_calc.py"]))

    # --- isolation ------------------------------------------------------
    def test_pr_code_cannot_reach_state_credentials_or_network(self):
        # The PR's test runs inside the gate's sandbox and asserts what it can
        # see; every probe target is real and reachable from the gate itself.
        secret = Path(self._tmp.name) / "outside" / "state.db"
        secret.parent.mkdir()
        secret.write_text("x")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        self.addCleanup(listener.close)
        gh = Path.home() / ".config" / "gh"
        probes = {
            "outside_file": repr(str(secret)),
            "gate_repo": repr(str(Path(gate.__file__).resolve().parent.parent / ".state")),
            "gate_tools": repr(gate.__file__),
            "gh_config": repr(str(gh)),
        }
        test = ("import os, socket\n\n\ndef test_isolated():\n"
                + "".join(f"    assert not os.path.exists({v}), {k!r}\n" for k, v in probes.items())
                + "    assert 'GH_TOKEN' not in os.environ\n"
                f"    for addr in (('127.0.0.1', {listener.getsockname()[1]}), ('1.1.1.1', 53)):\n"
                "        try:\n            socket.create_connection(addr, timeout=2).close()\n"
                "        except OSError:\n            continue\n"
                "        raise AssertionError(f'reached {addr}')\n")
        root = Path(self._tmp.name) / "run" / "head"
        root.mkdir(parents=True)
        (root / "test_iso.py").write_text(test, encoding="utf-8")
        with mock.patch.dict(os.environ, {"GH_TOKEN": "secret"}):
            rc, outcomes = gate.run_pytest(root, ["test_iso.py"], 60)
        self.assertEqual((rc, list(outcomes.values())), (0, ["passed"]))
        self.assertTrue(secret.exists())  # the probe path is real outside the sandbox

    def test_no_bwrap_rejects(self):
        self.land()
        with mock.patch.object(gate.shutil, "which", return_value=None):
            self.assert_verdict("rejected", "bubblewrap (bwrap) is required")

    # --- pin (force push) ----------------------------------------------
    def fresh_db(self) -> Path:
        db = Path(self._tmp.name) / "state.db"
        conn = connect(db)
        apply_schema(conn)
        conn.close()
        return db

    def rewrite_test(self, test: str) -> None:
        self.repo.git("reset", "-q", "--hard", self.base)
        self.land(test=test)

    def test_rewritten_test_commit_rejected_after_first_sight(self):
        db = self.fresh_db()
        self.land(test=TEST.replace("add(2, 2) == 4", "add(2, 2) == 5"))  # wrong test: rejected
        self.assertEqual(self.run_gate("--db-path", str(db), "--pr", "o/r#1")[0], 1)
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

    def test_record_failure_keeps_verdict(self):
        self.land()
        db = Path(self._tmp.name) / "bad.db"
        sqlite3.connect(db).execute("CREATE TABLE unrelated (x)").connection.commit()
        rc, out = self.run_gate("--db-path", str(db))
        self.assertEqual((rc, out.strip().splitlines()[-1]), (0, "ACCEPTANCE_GATE: passed"))


if __name__ == "__main__":
    unittest.main()
