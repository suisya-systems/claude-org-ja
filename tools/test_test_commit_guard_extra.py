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


class GuardReviewFixesTests(base.TestCommitGuardTests):
    """Forms from the Codex review of the guard."""

    def setUp(self) -> None:
        super().setUp()
        self.commit(self.wdir, "fix", "fix.txt")

    def test_pull_follows_the_rebase_setting(self):
        self.assertAllowed("git pull origin main")
        self.assertDenied("git -c pull.rebase=true pull origin main")
        self.git(self.wdir, "config", "pull.rebase", "true")
        self.assertDenied("git pull origin main")
        self.assertAllowed("git pull --no-rebase origin main")

    def test_reset_with_a_bare_separator_is_a_branch_reset(self):
        self.assertDenied("git reset --soft origin/main --")
        self.assertAllowed("git reset origin/main -- fix.txt")

    def test_update_ref_of_head_moves_the_branch(self):
        self.assertDenied("git update-ref HEAD origin/main")

    def test_two_history_changes_in_one_call(self):
        self.assertDenied("git reset --soft HEAD~1 && git commit --amend --no-edit")
        self.assertAllowed("git add -u && git commit -m more")
        self.assertAllowed("git fetch origin && git merge origin/main")


    def test_a_subdirectory_is_the_same_worktree(self):
        (self.wdir / "sub").mkdir()
        self.assertDenied("git reset --soft HEAD~1 && cd sub && git commit --amend --no-edit")

    def test_quoted_git_is_still_git(self):
        self.assertDenied('g"it" rebase origin/main')

    def test_git_dir_and_work_tree_selectors(self):
        here = self.root
        self.assertDenied(f"git --git-dir={self.wdir}/.git --work-tree={self.wdir} rebase origin/main", cwd=here)
        self.assertDenied(f"git --work-tree {self.wdir} --git-dir {self.wdir}/.git reset --hard origin/main",
                          cwd=here)
        self.assertDenied(f"GIT_WORK_TREE={self.wdir} GIT_DIR={self.wdir}/.git git rebase origin/main", cwd=here)
        self.assertDenied(f"git --git-dir={self.wdir}/.git rebase origin/main", cwd=here)
        self.assertAllowed(f"git --git-dir={self.wdir}/.git log --oneline -1", cwd=here)


    def test_nested_shell_behind_an_assignment(self):
        self.assertDenied("GIT_SEQUENCE_EDITOR=true bash -c 'git rebase origin/main'")
        self.assertDenied("env X=1 bash -c 'git rebase origin/main'")

    def test_attached_force_create_names(self):
        self.assertDenied("git checkout -Bfeat/t origin/main")
        self.assertDenied("git switch -Cfeat/t origin/main")
        self.assertAllowed("git checkout -Bother origin/main")


    def test_update_ref_with_a_reflog_message(self):
        self.assertDenied("git update-ref -m cleanup HEAD origin/main")
        self.assertDenied("git update-ref --stdin")

    def test_nested_shell_then_cd(self):
        self.assertDenied("bash -c 'git rebase origin/main'; cd /tmp")


for _name in [n for n in dir(base.TestCommitGuardTests) if n.startswith("test_")]:
    setattr(GuardReviewFixesTests, _name, None)
    setattr(GuardFormsTests, _name, None)

if __name__ == "__main__":
    unittest.main()
