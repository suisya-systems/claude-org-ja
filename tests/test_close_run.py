"""Acceptance tests: closing a finished run is one command (Issue 1298).

After a PR lands the secretary used to hand-write the same shell every time:
wait for the worker pane to close, ``git worktree remove --force``, mark the
run completed and drop its worker_dirs row in one StateWriter transaction, then
``run_complete_on_merge.py --check-closed``. ``tools/close_run.py --task T``
does all of it in that order, reading only recorded state, and stops before
removing anything while the pane is still open or the PR has not merged.

The four shapes closed by hand on 2026-11-21/22 are covered: a merged run, a
run parked (pane closed while waiting) whose PR then merged, a run with no PR
(a design-only run handed to a build run), and a PR that has not merged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.state_db import apply_schema, connect  # noqa: E402

TOOL = REPO / "tools" / "close_run.py"


class CloseRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / ".state").mkdir()
        self.db = self.tmp / ".state" / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (1, 'p', 'p')")
        conn.commit()
        conn.close()
        self.base = self.tmp / "workers" / "p"
        self.base.mkdir(parents=True)
        self.git(self.base, "init", "-q", "-b", "main")
        self.git(self.base, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                 "--allow-empty", "-m", "base")
        self._at = 0

    # -- fixtures ---------------------------------------------------------

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout

    def sql(self, query: str, args: tuple = ()) -> list:
        conn = connect(self.db)
        try:
            rows = conn.execute(query, args).fetchall()
            conn.commit()
            return [tuple(r) for r in rows]
        finally:
            conn.close()

    def run_(self, task: str, *, pr_url: str | None = None, pr_state: str | None = None,
             status: str = "review") -> Path:
        wdir = self.base / ".worktrees" / task
        self.git(self.base, "worktree", "add", "-q", "-b", f"feat/{task}", str(wdir), "main")
        (wdir / "send_plan.json").write_text("{}")  # untracked, as in real worktrees
        self.sql("INSERT INTO worker_dirs (abs_path, layout, is_git_repo, is_worktree) "
                 "VALUES (?, 'project', 1, 1)", (str(wdir),))
        (wid,) = self.sql("SELECT id FROM worker_dirs WHERE abs_path = ?", (str(wdir),))[0]
        self.sql("INSERT INTO runs (task_id, project_id, pattern, title, status, worker_dir_id, "
                 "branch, pr_url, pr_state) VALUES (?, 1, 'B', ?, ?, ?, ?, ?, ?)",
                 (task, task, status, wid, f"feat/{task}", pr_url, pr_state))
        return wdir

    def ev(self, kind: str, task: str, **extra) -> None:
        self._at += 1
        payload = {"task": task, "worker": f"worker-{task}", **extra}
        self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES (?, 'x', ?, ?)",
                 (f"2026-11-22T10:{self._at:02d}:00.000Z", kind, json.dumps(payload)))

    def close(self, *args: str, path_prefix: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if path_prefix is not None:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            [sys.executable, str(TOOL), "--db-path", str(self.db), *args],
            capture_output=True, text=True, cwd=str(self.tmp), env=env, timeout=120,
        )

    def status(self, task: str) -> str:
        return self.sql("SELECT status FROM runs WHERE task_id = ?", (task,))[0][0]

    def dir_rows(self, wdir: Path) -> int:
        return self.sql("SELECT COUNT(*) FROM worker_dirs WHERE abs_path = ?", (str(wdir),))[0][0]

    def last_line(self, proc: subprocess.CompletedProcess) -> str:
        lines = proc.stdout.strip().splitlines()
        return lines[-1] if lines else ""

    def assertClosed(self, proc, task: str, wdir: Path) -> None:
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.status(task), "completed")
        self.assertEqual(self.dir_rows(wdir), 0, "worker_dirs row left behind")
        self.assertFalse(wdir.exists(), "worktree left behind")
        self.assertNotIn(task, self.git(self.base, "worktree", "list"))
        self.assertIn(f"CLOSE_RUN: {task} closed", proc.stdout)

    def assertUntouched(self, task: str, wdir: Path, status: str = "review") -> None:
        self.assertEqual(self.status(task), status)
        self.assertEqual(self.dir_rows(wdir), 1)
        self.assertTrue(wdir.exists(), "worktree removed although the close was refused")

    # -- the four shapes --------------------------------------------------

    def test_merged_run_with_closed_pane_is_closed_in_one_call(self):
        wdir = self.run_("t1", pr_url="https://github.com/o/r/pull/1", pr_state="merged")
        self.ev("worker_spawned", "t1", dir=str(wdir))
        self.ev("pane_closed", "t1", pane_id="%1")
        self.assertClosed(self.close("--task", "t1"), "t1", wdir)

    def test_open_pane_blocks_and_removes_nothing(self):
        wdir = self.run_("t2", pr_url="https://github.com/o/r/pull/2", pr_state="merged")
        self.ev("worker_spawned", "t2", dir=str(wdir))
        proc = self.close("--task", "t2")
        self.assertEqual(proc.returncode, 5, proc.stdout + proc.stderr)
        self.assertIn("CLOSE_RUN: t2 blocked", self.last_line(proc))
        self.assertUntouched("t2", wdir)

    def test_parked_run_whose_pr_merged_is_closed(self):
        wdir = self.run_("t3", pr_url="https://github.com/o/r/pull/3", pr_state="merged")
        self.ev("worker_spawned", "t3", dir=str(wdir))
        self.ev("worker_parked", "t3", reason="merge")
        self.ev("pane_closed", "t3", pane_id="%3")
        self.assertClosed(self.close("--task", "t3"), "t3", wdir)

    def test_runs_without_pr_close_with_no_pr_in_one_call(self):
        # A design-only run handed to a build run, and a run that ended without a PR.
        design = self.run_("t4-design")
        self.ev("worker_spawned", "t4-design", dir=str(design))
        self.ev("pane_closed", "t4-design", pane_id="%4")
        audit = self.run_("t5-audit")
        self.ev("worker_spawned", "t5-audit", dir=str(audit))
        self.ev("pane_closed", "t5-audit", pane_id="%5")
        proc = self.close("--task", "t4-design", "--task", "t5-audit", "--no-pr")
        self.assertClosed(proc, "t4-design", design)
        self.assertClosed(proc, "t5-audit", audit)

    def test_run_without_pr_is_not_closed_without_no_pr(self):
        wdir = self.run_("t6")
        self.ev("worker_spawned", "t6", dir=str(wdir))
        self.ev("pane_closed", "t6", pane_id="%6")
        proc = self.close("--task", "t6")
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertIn("CLOSE_RUN: t6 blocked", self.last_line(proc))
        self.assertUntouched("t6", wdir)

    def test_pr_not_merged_blocks_and_removes_nothing(self):
        wdir = self.run_("t7", pr_url="https://github.com/o/r/pull/7", pr_state="open")
        self.ev("worker_spawned", "t7", dir=str(wdir))
        self.ev("pane_closed", "t7", pane_id="%7")
        fake = self.tmp / "bin"
        fake.mkdir()
        gh = fake / "gh"
        gh.write_text("#!/bin/sh\necho '" + json.dumps({
            "url": "https://github.com/o/r/pull/7", "state": "OPEN", "mergedAt": None,
            "mergeCommit": None, "headRefName": "feat/t7", "headRefOid": "a" * 40,
            "title": "t7"}) + "'\n")
        gh.chmod(0o755)
        proc = self.close("--task", "t7", path_prefix=fake)
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertIn("CLOSE_RUN: t7 blocked", self.last_line(proc))
        self.assertUntouched("t7", wdir)

    def test_second_call_is_a_no_op(self):
        wdir = self.run_("t8", pr_url="https://github.com/o/r/pull/8", pr_state="merged")
        self.ev("worker_spawned", "t8", dir=str(wdir))
        self.ev("pane_closed", "t8", pane_id="%8")
        self.assertClosed(self.close("--task", "t8"), "t8", wdir)
        again = self.close("--task", "t8")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("CLOSE_RUN: t8 closed", again.stdout)


if __name__ == "__main__":
    unittest.main()
