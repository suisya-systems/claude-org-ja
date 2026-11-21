"""The presubmit hook leaves the secretary's syncing commands alone (Issue 1298).

The hook (``tools/presubmit.py hook``) refuses only a raw ``git push`` /
``gh pr create`` / ``gh pr edit`` of an open run's work. Pulling main into the
org checkout, fetching in a project's base clone and bringing a worker's
worktree up to date with ``merge --ff-only`` must still pass, even when they
name the worker dir or an open run's branch.

Uses the fixture of ``tests/test_presubmit.py`` (the declared acceptance test)
without re-running its tests.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import test_presubmit as base  # noqa: E402  (module, so its tests are not collected here)


class HookPassThroughTests(base.PresubmitTests):
    def test_syncing_commands_are_allowed(self):
        wdir = self.worker("s1")
        for command, cwd in (
            ("git pull", self.root),
            ("git pull --ff-only origin main", self.root),
            ("git fetch -q origin", self.base),
            (f"git -C {self.base} fetch origin feat/s1", self.root),
            ("git merge --ff-only origin/feat/s1", wdir),
            (f"git -C {wdir} fetch -q origin && git -C {wdir} merge --ff-only origin/feat/s1", self.root),
            (f"git -C {wdir} log --oneline -1 origin/feat/s1", self.root),
            ("gh pr view 12 --repo o/p --json headRefOid", self.root),
            ("gh pr checks 12 --repo o/p", self.root),
        ):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command, cwd), "ALLOW")

    def test_a_push_hidden_in_bash_c_is_still_refused(self):
        wdir = self.worker("s2")
        self.assertTrue(self.hook(f"bash -c 'git -C {wdir} push origin HEAD'", self.root)
                        .startswith("DENY"))
        self.assertTrue(self.hook('git -C "$W" push origin HEAD', self.root).startswith("DENY"))


# Only the class above runs here; the inherited test_* methods belong to tests/test_presubmit.py.
for _name in [n for n in dir(base.PresubmitTests) if n.startswith("test_")]:
    setattr(HookPassThroughTests, _name, None)

if __name__ == "__main__":
    unittest.main()
