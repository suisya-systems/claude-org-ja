"""Acceptance tests: bringing open PRs up to date, and syncing a worker's worktree (Issue 1298).

Each time a PR landed, the secretary brought the remaining PRs up to date by
hand (``gh pr update-branch``), re-armed their CI watchers, and later found
that the worker's worktree and the push record still named the old head
(``belt_slots.py park`` refused: "not the CI-green head"; a watcher started
before the push record looked stale). Cutting a new branch for a task's next
PR, or merging main into a worktree whose update touches sandbox-protected
paths, was also done by hand outside the sandbox.

``tools/refresh_open_prs.py`` does, per open PR and in this order: update the
branch on GitHub, read the new head, record ``fix_pushed`` for it, bring the
worktree up with ``merge --ff-only`` (not when it has uncommitted changes),
and say whether the watcher must be re-armed. Conflicts are listed.

``tools/worktree_sync.py`` cuts a new branch (and points the run's record at
it: ``runs.branch``, no PR yet) or merges a ref into the worktree, from
outside the sandbox; a conflicting merge is undone and its files listed.
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

REFRESH = REPO / "tools" / "refresh_open_prs.py"
SYNC = REPO / "tools" / "worktree_sync.py"
ID = ["-c", "user.name=t", "-c", "user.email=t@t"]

# A gh for the tests: `pr view` reads the bare remote, `pr update-branch` merges the
# base into the PR branch there (as GitHub does), refusing on a conflict.
FAKE_GH = r'''
import json, os, subprocess, sys, tempfile
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
if args[:2] == ["pr", "view"]:
    pr = state["prs"][args[2]]
    head = git("--git-dir", state["remote"], "rev-parse", "refs/heads/" + pr["branch"]).stdout.strip()
    print(json.dumps({"headRefOid": head, "headRefName": pr["branch"], "state": pr.get("state", "OPEN"),
                      "url": "https://github.com/o/r/pull/" + args[2], "baseRefName": "main"}))
elif args[:2] == ["pr", "update-branch"]:
    pr = state["prs"][args[2]]
    d = tempfile.mkdtemp()
    git("clone", "-q", state["remote"], d)
    git("checkout", "-q", pr["branch"], cwd=d)
    r = git("-c", "user.name=gh", "-c", "user.email=gh@x", "merge", "--no-edit", "origin/main", cwd=d)
    if r.returncode != 0:
        print("merge conflict between base and head", file=sys.stderr)
        sys.exit(1)
    git("push", "-q", "origin", pr["branch"], cwd=d)
    print("PR branch updated")
else:
    print("unexpected gh call: " + " ".join(args), file=sys.stderr)
    sys.exit(2)
'''


@unittest.skipIf(os.name == "nt", "the fake gh is a shebang script (POSIX only)")
class RefreshSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "org"
        (self.root / ".state").mkdir(parents=True)
        self.db = self.root / ".state" / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (1, 'p', 'p')")
        conn.commit()
        conn.close()
        self.remote = self.tmp / "remote.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.base = self.tmp / "workers" / "p"
        self.base.mkdir(parents=True)
        self.git(self.base, "init", "-q", "-b", "main")
        self.commit(self.base, "base", "base.txt")
        self.git(self.base, "remote", "add", "origin", str(self.remote))
        self.git(self.base, "push", "-q", "origin", "main")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!{sys.executable}\n" + FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.state_file = self.tmp / "gh-state.json"
        self.gh_state = {"remote": str(self.remote), "prs": {}}
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                    "FAKE_GH_STATE": str(self.state_file), "GIT_TERMINAL_PROMPT": "0"}
        self._at = 0

    # -- fixtures ---------------------------------------------------------

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, cwd: Path, message: str, name: str, text: str | None = None) -> str:
        (cwd / name).write_text(text if text is not None else message, encoding="utf-8")
        self.git(cwd, "add", name)
        self.git(cwd, *ID, "commit", "-q", "-m", message)
        return self.git(cwd, "rev-parse", "HEAD")

    def sql(self, query: str, args: tuple = ()) -> list:
        conn = connect(self.db)
        try:
            rows = [tuple(r) for r in conn.execute(query, args).fetchall()]
            conn.commit()
            return rows
        finally:
            conn.close()

    def ev(self, kind: str, payload: dict) -> None:
        self._at += 1
        self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES (?, 'x', ?, ?)",
                 (f"2026-11-22T10:{self._at:02d}:00.000Z", kind, json.dumps(payload)))

    def pr_task(self, task: str, pr: int) -> Path:
        """A worker worktree with one commit, pushed, with an open PR and a watcher."""
        wdir = self.base / ".worktrees" / task
        self.git(self.base, "worktree", "add", "-q", "-b", f"feat/{task}", str(wdir), "main")
        self.sql("INSERT INTO worker_dirs (abs_path, layout, is_git_repo, is_worktree) "
                 "VALUES (?, 'project', 1, 1)", (str(wdir),))
        (wid,) = self.sql("SELECT id FROM worker_dirs WHERE abs_path = ?", (str(wdir),))[0]
        self.sql("INSERT INTO runs (task_id, project_id, pattern, title, status, worker_dir_id, branch, "
                 "pr_url, pr_state) VALUES (?, 1, 'B', ?, 'review', ?, ?, ?, 'open')",
                 (task, task, wid, f"feat/{task}", f"https://github.com/o/r/pull/{pr}"))
        self.ev("delegate_sent", {"task": task, "dir": str(wdir), "base_ref": "origin/main"})
        head = self.commit(wdir, f"work of {task}", f"{task}.txt")
        self.git(wdir, "push", "-q", "origin", f"feat/{task}")
        self.git(self.base, "fetch", "-q", "origin")
        self.ev("fix_pushed", {"task": task, "pr": pr, "repo": "o/r", "head": head})
        self.ev("pr_watch_pane_started", {"task": task, "pr": pr, "repo": "o/r", "pane_id": "%9"})
        self.gh_state["prs"][str(pr)] = {"branch": f"feat/{task}"}
        self.state_file.write_text(json.dumps(self.gh_state), encoding="utf-8")
        return wdir

    def main_moves(self, name: str = "main2.txt", text: str | None = None) -> None:
        self.git(self.base, "checkout", "-q", "main")
        self.commit(self.base, "main moves", name, text)
        self.git(self.base, "push", "-q", "origin", "main")
        self.git(self.base, "fetch", "-q", "origin")

    def remote_head(self, branch: str) -> str:
        return self.git(self.tmp, "--git-dir", str(self.remote), "rev-parse", f"refs/heads/{branch}")

    def run_tool(self, tool: Path, *args: str):
        return subprocess.run([sys.executable, str(tool), "--root", str(self.root), "--db-path", str(self.db),
                               *args], capture_output=True, text=True, env=self.env, timeout=120)

    def line(self, proc, task: str) -> str:
        lines = [l for l in proc.stdout.splitlines() if l.startswith(f"REFRESH: {task} ")]
        self.assertTrue(lines, f"no REFRESH line for {task}: {proc.stdout + proc.stderr}")
        return lines[-1]

    def pushed_heads(self, task: str) -> list[str]:
        rows = self.sql("SELECT payload_json FROM events WHERE kind = 'fix_pushed' ORDER BY id")
        return [p["head"] for p in (json.loads(r[0]) for r in rows) if p.get("task") == task]

    # -- refresh ------------------------------------------------------------

    def test_refresh_updates_records_syncs_and_asks_for_a_new_watcher(self):
        wdir = self.pr_task("a", 7)
        old = self.remote_head("feat/a")
        self.main_moves()
        proc = self.run_tool(REFRESH, "--task", "a")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        new = self.remote_head("feat/a")
        self.assertNotEqual(new, old)
        self.assertEqual(self.pushed_heads("a")[-1], new)          # the record follows GitHub
        self.assertEqual(self.git(wdir, "rev-parse", "HEAD"), new)  # the worktree too
        line = self.line(proc, 'a')
        self.assertIn("updated", line)
        self.assertIn("watcher=needs_rearm", line)
        self.assertIn("worktree=synced", line)

    def test_an_up_to_date_pr_is_left_alone(self):
        self.pr_task("b", 8)
        before = self.pushed_heads("b")
        proc = self.run_tool(REFRESH, "--task", "b")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("up_to_date", self.line(proc, 'b'))
        self.assertEqual(self.pushed_heads("b"), before)

    def test_a_conflict_is_listed_and_nothing_is_recorded(self):
        wdir = self.pr_task("c", 9)
        self.commit(wdir, "edit shared", "shared.txt", "branch side\n")
        self.git(wdir, "push", "-q", "origin", "feat/c")
        before = self.pushed_heads("c")
        self.main_moves("shared.txt", "main side\n")
        proc = self.run_tool(REFRESH, "--task", "c")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("conflict", self.line(proc, 'c'))
        self.assertEqual(self.pushed_heads("c"), before)

    def test_a_dirty_worktree_is_not_synced(self):
        wdir = self.pr_task("d", 10)
        (wdir / "d.txt").write_text("uncommitted", encoding="utf-8")
        old_local = self.git(wdir, "rev-parse", "HEAD")
        self.main_moves()
        proc = self.run_tool(REFRESH, "--task", "d")
        line = self.line(proc, 'd')
        self.assertIn("worktree=dirty", line)
        self.assertEqual(self.git(wdir, "rev-parse", "HEAD"), old_local)
        self.assertEqual(self.pushed_heads("d")[-1], self.remote_head("feat/d"))

    def test_several_prs_in_one_call(self):
        self.pr_task("e", 11)
        self.pr_task("f", 12)
        self.main_moves()
        proc = self.run_tool(REFRESH)  # every open PR of an open run
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for task in ("e", "f"):
            self.assertIn("updated", self.line(proc, task))

    # -- worktree_sync ------------------------------------------------------

    def test_a_new_branch_points_the_record_at_it(self):
        wdir = self.pr_task("g", 13)
        self.sql("UPDATE runs SET pr_state = 'merged' WHERE task_id = 'g'")
        proc = self.run_tool(SYNC, "--task", "g", "--new-branch", "feat/g-2", "--from", "origin/main")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.git(wdir, "symbolic-ref", "--short", "HEAD"), "feat/g-2")
        self.assertEqual(self.git(wdir, "rev-parse", "HEAD"), self.git(self.base, "rev-parse", "origin/main"))
        row = self.sql("SELECT branch, pr_url, pr_state FROM runs WHERE task_id = 'g'")[0]
        self.assertEqual(row, ("feat/g-2", None, None))

    def test_a_merge_into_the_worktree(self):
        wdir = self.pr_task("h", 14)
        self.main_moves()
        proc = self.run_tool(SYNC, "--task", "h", "--merge", "origin/main")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(subprocess.run(["git", "-C", str(wdir), "merge-base", "--is-ancestor", "origin/main",
                                         "HEAD"]).returncode, 0)
        self.assertEqual(self.git(wdir, "status", "--porcelain", "--untracked-files=no"), "")

    def test_a_conflicting_merge_is_undone_and_listed(self):
        wdir = self.pr_task("i", 15)
        before = self.commit(wdir, "edit shared", "shared.txt", "branch side\n")
        self.main_moves("shared.txt", "main side\n")
        proc = self.run_tool(SYNC, "--task", "i", "--merge", "origin/main")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("shared.txt", proc.stdout)
        self.assertEqual(self.git(wdir, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git(wdir, "status", "--porcelain", "--untracked-files=no"), "")

    def test_sync_refuses_a_dirty_worktree(self):
        wdir = self.pr_task("j", 16)
        (wdir / "j.txt").write_text("uncommitted", encoding="utf-8")
        proc = self.run_tool(SYNC, "--task", "j", "--new-branch", "feat/j-2", "--from", "origin/main")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("WORKTREE_SYNC: j dirty", proc.stdout)
        self.assertEqual(self.git(wdir, "symbolic-ref", "--short", "HEAD"), "feat/j")
        self.assertEqual(self.sql("SELECT branch FROM runs WHERE task_id = 'j'")[0][0], "feat/j")


if __name__ == "__main__":
    unittest.main()
