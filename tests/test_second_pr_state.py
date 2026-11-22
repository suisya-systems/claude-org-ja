"""Acceptance tests: a task's second PR does not inherit the first PR's record (Issue 1298).

Seen on 2026-11-22 when one task opened a second PR on a new branch:

* ``runs.branch`` still named the first PR's branch, so ``presubmit.py push``
  was about to send to it;
* ``runs.pr_state`` stayed ``merged`` from the first PR (``set_run_pr_open``
  only rewrote ``pr_url`` and ``branch``), and ``close_run`` reads ``merged``
  as "landed" and would remove the worktree of a PR still open;
* two pre-submit checks at once failed a load-sensitive test, and a check was
  run again on a head that had already passed.

So: ``set_run_pr_open`` records the PR's state too; ``close_run`` trusts
``merged`` only when a ``pr_merged`` event names that very PR, and otherwise
asks GitHub; ``presubmit.py push`` refuses when ``runs.branch`` is not the
worker dir's branch; ``presubmit.py run`` takes a lock (one check at a time)
and does not run again for a head that passed with the same settings.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: presubmit.py has no run lock there
    fcntl = None

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import test_close_run as close_base  # noqa: E402  (modules, so their tests are not collected twice)
from tests import test_presubmit as pre_base  # noqa: E402
from tools.state_db import connect  # noqa: E402

OPEN_VIEW = {"url": "https://github.com/o/r/pull/2", "headRefName": "feat/t-2", "state": "OPEN",
             "isDraft": False, "title": "second"}


class SetRunPrOpenStateTests(close_base.CloseRunTests):
    def test_the_second_pr_replaces_the_first_prs_state(self):
        self.run_("t", pr_url="https://github.com/o/r/pull/1", pr_state="merged")
        from tools.set_run_pr_open import set_run_pr_open
        set_run_pr_open(task_id="t", pr=2, repo="o/r", db_path=self.db, pr_view=dict(OPEN_VIEW))
        row = self.sql("SELECT pr_url, pr_state, branch FROM runs WHERE task_id = 't'")[0]
        self.assertEqual(row, ("https://github.com/o/r/pull/2", "open", "feat/t-2"))


class CloseRunStaleMergedTests(close_base.CloseRunTests):
    def gh_says(self, state: str) -> Path:
        fake = self.tmp / "bin"
        fake.mkdir(exist_ok=True)
        gh = fake / "gh"
        view = {"url": "https://github.com/o/r/pull/2", "state": state, "headRefName": "feat/t",
                "headRefOid": "a" * 40, "title": "second",
                "mergedAt": "2026-11-22T12:00:00Z" if state == "MERGED" else None,
                "mergeCommit": {"oid": "b" * 40} if state == "MERGED" else None}
        gh.write_text("#!/bin/sh\necho '" + json.dumps(view) + "'\n", encoding="utf-8")
        gh.chmod(0o755)
        return fake

    def test_a_stale_merged_is_checked_with_github(self):
        # The first PR's `merged` is left on the row of the second, still open, PR.
        wdir = self.run_("t", pr_url="https://github.com/o/r/pull/2", pr_state="merged")
        self.ev("worker_spawned", "t", dir=str(wdir))
        self.ev("pr_merged", "t", pr=1, pr_url="https://github.com/o/r/pull/1")
        self.ev("pane_closed", "t", pane_id="%1")
        proc = self.close("--task", "t", path_prefix=self.gh_says("OPEN"))
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertUntouched("t", wdir)


class PresubmitSecondPrTests(pre_base.PresubmitTests):
    def test_push_refuses_when_the_recorded_branch_is_not_the_worktrees(self):
        self.registry("true")
        wdir = self.worker("p1")
        self.git(wdir, "checkout", "-q", "-b", "feat/p1-2")
        self.commit(wdir, "work")
        self.tool("run", "--task", "p1")
        proc = self.tool("push", "--task", "p1")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PRESUBMIT_PUSH: refused", self.last(proc))
        self.assertIn("feat/p1-2", self.last(proc))
        self.assertEqual(self.remote_head("feat/p1"), "")
        self.assertEqual(self.remote_head("feat/p1-2"), "")

    def test_a_head_that_passed_is_not_checked_again(self):
        counter = self.tmp / "runs.txt"
        self.registry(f"echo ran >> {counter}")
        wdir = self.worker("p2")
        self.commit(wdir, "work")
        first = self.tool("run", "--task", "p2")
        second = self.tool("run", "--task", "p2")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("already", self.last(second))
        self.assertEqual(counter.read_text(encoding="utf-8").count("ran"), 1)
        # Other settings: checked again.
        self.registry(f"echo ran >> {counter}; true")
        third = self.tool("run", "--task", "p2")
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertEqual(counter.read_text(encoding="utf-8").count("ran"), 2)

    @unittest.skipIf(fcntl is None, "no fcntl (Windows): presubmit.py has no run lock there")
    def test_one_check_at_a_time(self):
        self.registry("true")
        wdir = self.worker("p3")
        self.commit(wdir, "work")
        lock = self.root / ".state" / "presubmit" / "run.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            proc = self.tool("run", "--task", "p3", "--wait", "2")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("waiting", proc.stdout)
        self.assertEqual(self.checked("p3"), [])


def _only_own(cls, base):
    for name in [n for n in dir(base) if n.startswith("test_")]:
        if name not in cls.__dict__:
            setattr(cls, name, None)


_only_own(SetRunPrOpenStateTests, close_base.CloseRunTests)
_only_own(CloseRunStaleMergedTests, close_base.CloseRunTests)
_only_own(PresubmitSecondPrTests, pre_base.PresubmitTests)

if __name__ == "__main__":
    unittest.main()
