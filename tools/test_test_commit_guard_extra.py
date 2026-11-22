"""More forms for the test commit guard (Issue 1298).

Uses the fixture of ``tests/test_test_commit_guard.py`` (the declared
acceptance test), loaded by path so the ``tests`` package __init__ (which
scrubs the transport env for the whole process) does not run here.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location("_guard_fixture", _ROOT / "tests" / "test_test_commit_guard.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


class GuardFormsTests(base.TestCommitGuardTests):
    def setUp(self) -> None:
        super().setUp()
        self.commit(self.wdir, "fix", "fix.txt")
        self.push_m()

    def test_rebase_targets(self):
        self.assertAllowed("git rebase HEAD~1")             # the upstream is M itself
        self.assertDenied("git rebase")                      # no upstream configured
        self.assertDenied("git rebase --onto origin/main HEAD~1")
        self.assertDenied("git rebase $BASE")

    def test_moving_the_current_branch(self):
        self.assertDenied("git checkout -B feat/t origin/main")
        self.assertDenied("git switch -C feat/t origin/main")
        self.assertDenied("git update-ref refs/heads/feat/t origin/main")
        self.assertAllowed("git checkout -B other origin/main")
        self.assertAllowed("git branch -f other origin/main")

    def test_directory_forms(self):
        self.assertAllowed("cd /tmp && git status")
        self.assertDenied("cd $W && git rebase origin/main")
        self.assertDenied(f"cd {self.wdir} && git rebase origin/main", cwd=self.root)
        self.assertDenied(f"git -C{self.wdir} rebase origin/main", cwd=self.root)

    def test_pull_without_rebase_passes(self):
        self.assertAllowed("git pull --no-rebase origin main")
        self.assertAllowed("git pull --ff-only origin main")


for _name in [n for n in dir(base.TestCommitGuardTests) if n.startswith("test_")]:
    setattr(GuardFormsTests, _name, None)

if __name__ == "__main__":
    unittest.main()
