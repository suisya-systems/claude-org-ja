"""More cases for refresh_open_prs.py and worktree_sync.py (Issue 1298).

Uses the fixture of tests/test_refresh_sync.py, loaded by path so the ``tests``
package __init__ does not run here.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
_spec = importlib.util.spec_from_file_location("_refresh_fixture", _ROOT / "tests" / "test_refresh_sync.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


@unittest.skipIf(os.name == "nt", "the fake gh is a shebang script (POSIX only)")
class MoreTests(base.RefreshSyncTests):
    def test_a_retry_syncs_a_worktree_left_behind(self):
        wdir = self.pr_task("k", 20)
        (wdir / "k.txt").write_text("uncommitted", encoding="utf-8")
        self.main_moves()
        first = self.run_tool(base.REFRESH, "--task", "k")
        self.assertIn("worktree=dirty", self.line(first, "k"))
        pushes = self.pushed_heads("k")
        self.git(wdir, "checkout", "--", "k.txt")  # the worker's change is gone
        second = self.run_tool(base.REFRESH, "--task", "k")
        self.assertIn("worktree=synced", self.line(second, "k"))
        self.assertEqual(self.git(wdir, "rev-parse", "HEAD"), self.remote_head("feat/k"))
        self.assertEqual(self.pushed_heads("k"), pushes)  # no second record for the same head

    def test_a_failed_fetch_changes_nothing(self):
        wdir = self.pr_task("l", 21)
        self.git(wdir, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        proc = self.run_tool(base.SYNC, "--task", "l", "--new-branch", "feat/l-2", "--from", "origin/main")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("fetch", proc.stdout)
        self.assertEqual(self.git(wdir, "symbolic-ref", "--short", "HEAD"), "feat/l")
        self.assertEqual(self.sql("SELECT branch, pr_state FROM runs WHERE task_id = 'l'")[0], ("feat/l", "open"))
        merge = self.run_tool(base.SYNC, "--task", "l", "--merge", "origin/main")
        self.assertNotEqual(merge.returncode, 0, merge.stdout + merge.stderr)


    def test_a_missing_push_record_is_repaired(self):
        wdir = self.pr_task("n", 23)
        # An earlier update moved the branch on GitHub, but its record was never written.
        self.commit(wdir, "moved on GitHub", "n2.txt")
        self.git(wdir, "push", "-q", "origin", "feat/n")
        proc = self.run_tool(base.REFRESH, "--task", "n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = self.line(proc, "n")
        self.assertIn("watcher=needs_rearm", line)
        self.assertEqual(self.pushed_heads("n")[-1], self.remote_head("feat/n"))

    def test_a_failed_local_sync_fails_the_call(self):
        wdir = self.pr_task("o", 24)
        self.main_moves()
        # The worktree shares the clone's config: from here its fetch fails (GitHub, faked, still works).
        self.git(wdir, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        proc = self.run_tool(base.REFRESH, "--task", "o")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = self.line(proc, "o")
        self.assertIn("worktree=error", line)
        self.assertIn("watcher=needs_rearm", line)
        self.assertEqual(self.pushed_heads("o")[-1], self.remote_head("feat/o"))  # the record still written

    def test_root_alone_selects_its_database(self):
        self.pr_task("m", 22)
        import subprocess
        proc = subprocess.run([sys.executable, str(base.SYNC), "--root", str(self.root), "--task", "m",
                               "--new-branch", "feat/m-2", "--from", "origin/main"],
                              capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.sql("SELECT branch FROM runs WHERE task_id = 'm'")[0][0], "feat/m-2")


for _name in [n for n in dir(base.RefreshSyncTests) if n.startswith("test_")]:
    if _name not in MoreTests.__dict__:
        setattr(MoreTests, _name, None)

if __name__ == "__main__":
    unittest.main()
