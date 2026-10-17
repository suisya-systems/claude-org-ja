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

WRAPPER = Path(__file__).resolve().parent.parent / ".hooks" / "gate-acceptance-before-merge.sh"


class ParseTest(unittest.TestCase):
    def test_not_a_merge(self):
        for cmd in ("git merge main", "gh pr view 3", "gh pr checks 3",
                    'gh issue comment 3 --body "will pr merge later"', "echo merge"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(hook.parse(cmd))

    def test_merge_must_stand_alone(self):
        for cmd in ("gh pr merge 3 && git pull", "git pull; gh pr merge 3", "gh pr merge 3 | cat",
                    'bash -c "gh pr merge 3"', "gh pr merge $PR", "gh pr merge `cat n`",
                    "env X=1 gh pr merge 3", "gh pr merge 3 4", 'gh pr merge "3',
                    "gh pr merge 7 --squash\n--disable-auto", "git pull\ngh pr merge 3"):
            with self.subTest(cmd=cmd):
                with self.assertRaises(hook.Deny):
                    hook.parse(cmd)

    def test_line_continuations_are_joined(self):
        self.assertEqual(hook.parse("gh pr \\\n  merge 7 --squash")["sel"], "7")
        self.assertEqual(hook.parse("gh pr mer\\\nge 7")["sel"], "7")

    def test_api_merge_denied(self):
        with self.assertRaisesRegex(hook.Deny, "API"):
            hook.parse("gh api -X PUT repos/o/r/pulls/3/merge")

    def test_flags(self):
        args = hook.parse("gh pr merge 3 --squash -d -R o/r --auto --subject 'x y' --body=z")
        self.assertEqual(args, {"sel": "3", "repo": "o/r", "auto": True, "disable_auto": False})
        self.assertEqual(hook.parse("gh pr merge --repo=o/r")["repo"], "o/r")
        self.assertEqual(hook.parse("gh pr merge -Ro/r 3")["repo"], "o/r")
        self.assertTrue(hook.parse("gh pr merge 3 --disable-auto")["disable_auto"])
        for flag in ("--disable-auto=false", "--auto=true"):
            with self.subTest(flag=flag), self.assertRaises(hook.Deny):
                hook.parse(f"gh pr merge 3 {flag}")


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

    def run_hook(self, command: str, cwd: Path | None = None) -> tuple[int, str]:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                              **({"cwd": str(cwd)} if cwd else {})})
        res = subprocess.run(["bash", str(WRAPPER)], input=payload, capture_output=True,
                             text=True, env=self.env, cwd=self.project, timeout=300)
        return res.returncode, res.stderr

    def test_passing_pr_allowed(self):
        self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}))
        self.assertEqual(self.run_hook("gh pr merge 7 --squash"), (0, ""))

    def test_rejected_pr_denied(self):
        self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}),
                     ("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"}))
        rc, err = self.run_hook("gh pr merge 7 --squash")
        self.assertEqual(rc, 2)
        self.assertIn("ACCEPTANCE_GATE rejected", err)

    def test_no_manifest_allowed_even_with_auto(self):
        self.open_pr(("fix", {"calc.py": FIXED}))
        self.assertEqual(self.run_hook("gh pr merge 7 --auto --squash")[0], 0)

    def test_auto_merge_of_gated_pr_denied(self):
        self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}))
        rc, err = self.run_hook("gh pr merge 7 --auto --squash")
        self.assertEqual(rc, 2)
        self.assertIn("--auto", err)

    def test_pr_is_resolved_from_the_bash_cwd(self):
        # gh resolves a bare PR number from the directory the Bash call runs in.
        self.open_pr(("fix", {"calc.py": FIXED}))
        other = Path(self._tmp.name) / "other"
        other.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!/bin/sh\n[ \"$PWD\" = '{other}' ] || {{ echo wrong cwd >&2; exit 1; }}\n"
                      + gh.read_text().split("\n", 1)[1])
        self.assertEqual(self.run_hook("gh pr merge 7", cwd=other)[0], 0)
        self.assertEqual(self.run_hook("gh pr merge 7")[0], 2)

    def test_continued_merge_line_is_gated(self):
        self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}),
                     ("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"}))
        self.assertEqual(self.run_hook("gh pr \\\n  merge 7 --squash")[0], 2)

    def test_newline_split_merge_is_denied(self):
        self.open_pr(("fix", {"calc.py": FIXED}))
        rc, err = self.run_hook("gh pr merge 7 --squash\n--disable-auto")
        self.assertEqual(rc, 2)
        self.assertIn("on one line", err)

    def test_gh_failure_denied(self):
        (self.bin / "gh").write_text("#!/bin/sh\necho 'no auth' >&2\nexit 1\n")
        (self.bin / "gh").chmod(0o755)
        rc, err = self.run_hook("gh pr merge 7")
        self.assertEqual(rc, 2)
        self.assertIn("gh pr view failed", err)

    def test_allow_line_from_a_crashed_python_is_denied(self):
        py = self.bin / "python3"
        py.write_text("#!/bin/sh\necho ALLOW\nexit 1\n")
        py.chmod(0o755)
        self.env["PATH"] = f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"  # no py on PATH
        self.assertEqual(self.run_hook("gh pr merge 7")[0], 2)

    def test_unrelated_command_skips_python(self):
        (self.bin / "python3").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "python3").chmod(0o755)
        self.assertEqual(self.run_hook("git status"), (0, ""))


if __name__ == "__main__":
    unittest.main()
