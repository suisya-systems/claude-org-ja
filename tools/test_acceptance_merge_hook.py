"""Tests for the secretary's pre-merge hook (Issue #1167): command parsing, and
the whole path (fake gh, a file:// remote, the real gate) through the bash wrapper.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import acceptance_merge_hook as hook  # noqa: E402
from tools.test_acceptance_gate import BUGGY, FIXED, TEST, TRAILER, Repo  # noqa: E402

MERGE7 = "gh pr merge 7 --repo o/r --squash"
WRAPPER = Path(__file__).resolve().parent.parent / ".hooks" / "gate-acceptance-before-merge.sh"


class ParseTest(unittest.TestCase):
    def test_not_a_merge(self):
        for cmd in ("git merge main", "gh pr view 3", "gh pr checks 3", "echo merge",
                    "git log --merges", "grep -n merge /x/y.md"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(hook.parse(cmd))

    def test_the_allowed_form(self):
        for method in ("squash", "merge", "rebase"):
            self.assertEqual(hook.parse(f"gh pr merge 3 --repo o/r --{method}"), ("3", "o/r"))
        self.assertEqual(hook.parse("gh pr merge 12 --repo suisya-systems/claude-org-ja --squash"
                                    " --delete-branch\n"), ("12", "suisya-systems/claude-org-ja"))

    def test_every_other_merge_is_denied(self):
        for cmd in ("gh pr merge 3", "gh pr merge 3 --squash", "gh pr merge --repo o/r 3 --squash",
                    "gh pr merge 3 --squash --repo o/r", "gh pr merge 3 --repo o/r --squash --auto",
                    "gh pr merge 3 --repo o/r --squash --disable-auto", "gh pr merge 3 -R o/r --squash",
                    "gh pr merge 3 --repo=o/r --squash", "gh  pr merge 3 --repo o/r --squash",
                    "gh pr merge 3 --repo o/r --squash && git pull", "git pull; gh pr merge 3 --repo o/r --squash",
                    "gh pr merge 3 --repo o/r --squash\n--disable-auto", "gh pr \\\n merge 3 --repo o/r --squash",
                    'bash -c "gh pr merge 3 --repo o/r --squash"', "gh pr merge $PR --repo o/r --squash",
                    "gh pr m'erge' 3", "gh api -X PUT repos/o/r/pulls/3/merge",
                    "curl -X PUT https://api.github.com/repos/o/r/pulls/3/merge",
                    "gh api graphql -f query='mutation{mergePullRequest(input:{})}'",
                    "gh pr MERGE 3", "x=merge; gh pr $x 3", "gh pr merge 3 --repo o/r --squash --body x"):
            with self.subTest(cmd=cmd), self.assertRaises(hook.Deny):
                hook.parse(cmd)


@unittest.skipUnless(shutil.which("bwrap") and shutil.which("jq"), "needs bwrap and jq")
class HookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.origin = Repo(tmp / "origin")
        self.base = self.origin.commit("base", {"calc.py": BUGGY, "pyproject.toml": "[project]\nname = 'x'\n"})
        self.project = tmp / "project"
        subprocess.run(["git", "clone", "-q", str(self.origin.root), str(self.project)], check=True)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                        CLAUDE_PROJECT_DIR=str(self.project), STATE_DB_PATH=str(tmp / "state.db"))

    def tearDown(self):
        self._tmp.cleanup()

    def open_pr(self, *commits: tuple[str, dict]) -> None:
        """Commit on a branch of origin, publish it as refs/pull/7/head, fake gh for it."""
        self.origin.git("checkout", "-q", "-B", "feature", self.base)
        for msg, files in commits:
            head = self.origin.commit(msg, files)
        self.origin.git("update-ref", "refs/pull/7/head", head)
        self.origin.git("checkout", "-q", "main")
        view = {"number": 7, "url": f"file://{self.origin.root}/pull/7",
                "baseRefName": "main", "headRefOid": head}
        gh = self.bin / "gh"
        gh.write_text(f"#!/bin/sh\necho '{json.dumps(view)}'\n")
        gh.chmod(0o755)

    def run_hook(self, command: str) -> tuple[int, str]:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        res = subprocess.run(["bash", str(WRAPPER)], input=payload, capture_output=True,
                             text=True, env=self.env, cwd=self.project, timeout=300)
        return res.returncode, res.stderr

    def test_passing_pr_allowed(self):
        self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}))
        self.assertEqual(self.run_hook(MERGE7), (0, ""))

    def test_rejected_pr_denied(self):
        self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}),
                     ("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"}))
        rc, err = self.run_hook(MERGE7)
        self.assertEqual(rc, 2)
        self.assertIn("ACCEPTANCE_GATE rejected", err)

    def test_no_manifest_allowed(self):
        self.open_pr(("fix", {"calc.py": FIXED}))
        self.assertEqual(self.run_hook(MERGE7)[0], 0)

    def test_other_merge_form_is_denied_without_running_the_gate(self):
        (self.bin / "gh").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "gh").chmod(0o755)
        for cmd in ("gh pr merge 7 --squash", "gh pr MERGE 7 --repo o/r --squash"):
            rc, err = self.run_hook(cmd)
            self.assertEqual(rc, 2)
            self.assertIn("merge with exactly", err)

    def test_gh_failure_denied(self):
        (self.bin / "gh").write_text("#!/bin/sh\necho 'no auth' >&2\nexit 1\n")
        (self.bin / "gh").chmod(0o755)
        rc, err = self.run_hook(MERGE7)
        self.assertEqual(rc, 2)
        self.assertIn("gh pr view failed", err)

    def test_allow_line_from_a_crashed_python_is_denied(self):
        py = self.bin / "python3"
        py.write_text("#!/bin/sh\necho ALLOW\nexit 1\n")
        py.chmod(0o755)
        self.env["PATH"] = f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"  # no py on PATH
        self.assertEqual(self.run_hook(MERGE7)[0], 2)

    def test_unrelated_command_skips_python(self):
        (self.bin / "python3").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "python3").chmod(0o755)
        self.assertEqual(self.run_hook("git status"), (0, ""))


if __name__ == "__main__":
    unittest.main()
