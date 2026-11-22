"""Acceptance tests: a worker cannot rewrite a reported test commit (Issue 1298).

Twice a worker rebased after reporting the SHA of its declared test commit
(the commit with the ``Acceptance-Test`` trailer). The secretary pushes that
commit alone first so CI records its failure; a new SHA throws that record
away. The brief even told workers to rebase before reporting.

``tools/test_commit_guard.py hook`` (PreToolUse payload on stdin; the worker
hook ``.hooks/block-test-commit-rewrite.sh`` runs it) decides from state, not
from the worker's word. M is guarded once any of these holds: it is on the
remote branch, it is in a ``presubmit_checked`` record, or the worker's own
commits sit on top of it (the implementation has started). While guarded, a
git command that would drop M from the
branch (a rebase onto a base without M, ``commit --amend`` at M, a reset or a
forced branch move to a commit without M, filter-branch) is refused, and
``git merge`` of the base is the way to catch up. While M is still HEAD and
neither pushed nor recorded, the test can be rewritten freely; a mistake found
after the implementation is on top is fixed on a new branch (cut from the base,
corrected test first, implementation cherry-picked), which passes.
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

TOOL = REPO / "tools" / "test_commit_guard.py"
ID = ["-c", "user.name=t", "-c", "user.email=t@t"]
TRAILER = "\n\nAcceptance-Test: tests/test_x.py\n"


class TestCommitGuardTests(unittest.TestCase):
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
        self.git(self.tmp, "init", "-q", "--bare", str(self.remote))
        self.base = self.tmp / "workers" / "p"
        self.base.mkdir(parents=True)
        self.git(self.base, "init", "-q", "-b", "main")
        self.commit(self.base, "base", "base.txt")
        self.git(self.base, "remote", "add", "origin", str(self.remote))
        self.git(self.base, "push", "-q", "origin", "main")
        # main moves on after the task is dispatched.
        self.wdir = self.base / ".worktrees" / "t"
        self.git(self.base, "worktree", "add", "-q", "-b", "feat/t", str(self.wdir), "main")
        self.commit(self.base, "main moves", "main2.txt")
        self.git(self.base, "push", "-q", "origin", "main")
        self.git(self.base, "fetch", "-q", "origin")
        self.sql("INSERT INTO worker_dirs (abs_path, layout, is_git_repo, is_worktree) "
                 "VALUES (?, 'project', 1, 1)", (str(self.wdir),))
        self.sql("INSERT INTO runs (task_id, project_id, pattern, title, status, worker_dir_id, branch) "
                 "VALUES ('t', 1, 'B', 't', 'in_use', 1, 'feat/t')")
        self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES "
                 "('2026-11-22T10:00:00.000Z', 'secretary', 'delegate_sent', ?)",
                 (json.dumps({"task": "t", "worker": "worker-t", "dir": str(self.wdir),
                              "base_ref": "origin/main"}),))
        self.m = self.commit(self.wdir, "test: failing test" + TRAILER, "tests_x.txt")

    # -- fixtures ---------------------------------------------------------

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, cwd: Path, message: str, name: str) -> str:
        (cwd / name).write_text(message, encoding="utf-8")
        self.git(cwd, "add", name)
        self.git(cwd, *ID, "commit", "-q", "-m", message)
        return self.git(cwd, "rev-parse", "HEAD")

    def sql(self, query: str, args: tuple = ()) -> None:
        conn = connect(self.db)
        try:
            conn.execute(query, args)
            conn.commit()
        finally:
            conn.close()

    def push_m(self) -> None:
        """The secretary pushes the test commit alone (refs shared with the worktree)."""
        self.git(self.base, "push", "-q", "origin", f"{self.m}:refs/heads/feat/t")
        self.git(self.base, "fetch", "-q", "origin")

    def record_m(self) -> None:
        self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES "
                 "('2026-11-22T11:00:00.000Z', 'secretary', 'presubmit_checked', ?)",
                 (json.dumps({"task": "t", "head": self.m, "test_commit": self.m, "result": "passed"}),))

    def hook(self, command: str, cwd: Path | None = None) -> str:
        payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd or self.wdir),
                              "tool_input": {"command": command}})
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--root", str(self.root), "--db-path", str(self.db), "hook"],
            input=payload, capture_output=True, text=True, timeout=60,
            env={**os.environ, "WORKER_DIR": str(self.wdir), "CLAUDE_ORG_PATH": str(self.root)},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertTrue(lines, "the hook printed nothing")
        return lines[0]

    def assertDenied(self, command: str, cwd: Path | None = None) -> None:
        self.assertTrue(self.hook(command, cwd).startswith("DENY"), command)

    def assertAllowed(self, command: str, cwd: Path | None = None) -> None:
        self.assertEqual(self.hook(command, cwd), "ALLOW", command)

    # -- while the test commit is HEAD and not reported ----------------------

    def test_the_test_commit_can_be_rewritten_while_it_is_head(self):
        self.assertAllowed("git commit --amend --no-edit")
        self.assertAllowed("git rebase origin/main")
        self.assertAllowed("git reset --hard origin/main")

    # -- once the implementation sits on top ---------------------------------

    def test_work_on_top_guards_the_test_commit(self):
        self.commit(self.wdir, "fix", "fix.txt")  # neither pushed nor recorded
        self.assertDenied("git rebase origin/main")
        self.assertDenied("git reset --hard origin/main")
        self.assertDenied("git rebase -i HEAD~2")
        self.assertAllowed("git merge origin/main")
        self.assertAllowed("git commit --amend --no-edit")  # HEAD is the fix, M stays

    def test_rebuilding_on_a_new_branch_passes(self):
        fix = self.commit(self.wdir, "fix", "fix.txt")
        self.assertAllowed("git switch -c feat/t-2 origin/main")
        self.assertAllowed("git checkout -b feat/t-2 origin/main")
        self.assertAllowed(f"git cherry-pick {fix}")

    # -- once it is reported -------------------------------------------------

    def test_a_pushed_test_commit_is_not_rebased_away(self):
        self.commit(self.wdir, "fix", "fix.txt")
        self.push_m()
        self.assertDenied("git rebase origin/main")
        self.assertDenied("git rebase -i origin/main")
        self.assertDenied("env GIT_EDITOR=true git rebase origin/main")
        self.assertDenied(f"git -C {self.wdir} rebase origin/main", cwd=self.root)
        self.assertAllowed("git merge origin/main")
        self.assertAllowed("git merge --no-edit origin/main")
        self.assertAllowed("git rebase --abort")

    def test_a_recorded_test_commit_is_guarded_too(self):
        self.record_m()  # M is HEAD: only the record guards it
        self.assertDenied("git rebase origin/main")
        self.assertDenied("git pull --rebase origin main")
        self.assertDenied("git filter-branch --msg-filter cat HEAD")

    def test_amend_is_refused_only_at_the_test_commit(self):
        self.push_m()
        self.assertDenied("git commit --amend --no-edit")  # HEAD is M
        self.commit(self.wdir, "fix", "fix.txt")
        self.assertAllowed("git commit --amend --no-edit")  # HEAD is the fix

    def test_resets_that_keep_the_test_commit_pass(self):
        self.commit(self.wdir, "fix", "fix.txt")
        self.push_m()
        self.assertAllowed("git reset --soft HEAD~1")  # back to M: M stays
        self.assertAllowed("git reset tests_x.txt")    # a path, HEAD does not move
        self.assertDenied("git reset --hard origin/main")
        self.assertDenied("git reset --hard HEAD~2")
        self.assertDenied("git branch -f feat/t origin/main")
        self.assertDenied("git reset --hard $B")

    def test_other_commands_pass(self):
        self.push_m()
        for command in ("git status", "git log --oneline -3", "git fetch origin",
                        "git add -u", "git commit -m more", "python3 -m pytest tests"):
            self.assertAllowed(command)


if __name__ == "__main__":
    unittest.main()
