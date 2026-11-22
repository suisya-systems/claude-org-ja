"""Acceptance tests: the pre-submit check runs from settings and gates the push (Issue 1298).

On every completion report the secretary built the full test command by hand
and ran it outside the sandbox (cargo tests, pytest twice, the project's
acceptance check). Once, a helper file changed after the test commit and only
CI noticed (a rebuilt PR). ``tools/presubmit.py``:

* ``run --task T`` reads the project's check from settings (the ``presubmit``
  column of ``registry/projects.md``; ``home_presubmit:`` in
  ``registry/org-config.md`` for the org repository itself), runs each step in
  the worker dir, writes a log and one ``presubmit_checked`` event, and ends
  with ``PRESUBMIT: passed|failed|not-configured``;
* ``push --task T [--test-commit]`` pushes only a head (or the recorded test
  commit) that has a passed check;
* ``pr --task T (--create | --edit N) --title X --body-file F [--closes N]``
  creates or edits the PR with gh, after refusing every GitHub closing
  reference (close/fix/resolve forms before ``#N``, ``owner/repo#N`` or an
  Issue URL) in the title, the body and the branch's commit messages that is
  not listed with ``--closes``. GitHub does not read negation, so neither does
  the check (makiba PR 266 closed Issue 50 with "It does not close #50");
* ``hook`` (PreToolUse payload on stdin) refuses a raw ``git push``,
  ``gh pr create`` or ``gh pr edit`` of an open run's work, so they go
  through ``push`` and ``pr``.
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

TOOL = REPO / "tools" / "presubmit.py"
ID = ["-c", "user.name=t", "-c", "user.email=t@t"]
TRAILER = "\n\nAcceptance-Test: tests/test_x.py\n"


class PresubmitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # The org root: a git repo holding .state/ and registry/.
        self.root = self.tmp / "org"
        (self.root / ".state").mkdir(parents=True)
        (self.root / "registry").mkdir()
        self.git(self.root, "init", "-q", "-b", "main")
        self.git(self.root, *ID, "commit", "-q", "--allow-empty", "-m", "org")
        self.db = self.root / ".state" / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (1, 'p', 'p')")
        conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (2, 'ja', 'ja')")
        conn.commit()
        conn.close()
        self.config("")
        self.registry("")
        # The project's clone, its remote, and a worker worktree.
        self.remote = self.tmp / "remote.git"
        self.git(self.tmp, "init", "-q", "--bare", str(self.remote))
        self.base = self.tmp / "workers" / "p"
        self.base.mkdir(parents=True)
        self.git(self.base, "init", "-q", "-b", "main")
        self.git(self.base, *ID, "commit", "-q", "--allow-empty", "-m", "base")
        self.git(self.base, "remote", "add", "origin", str(self.remote))
        self.git(self.base, "push", "-q", "origin", "main")
        self.git(self.base, "fetch", "-q", "origin")

    # -- fixtures ---------------------------------------------------------

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def config(self, extra: str) -> None:
        (self.root / "registry" / "org-config.md").write_text(
            "# Org Config\n\n## Merge Approval\nmerge_approval_mode: acceptance\n" + extra,
            encoding="utf-8")

    def registry(self, presubmit: str) -> None:
        (self.root / "registry" / "projects.md").write_text(
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage | base_branch | presubmit |\n"
            "|---|---|---|---|---|---|---|---|\n"
            f"| p | p | https://github.com/o/p | sample | - | | | {presubmit} |\n",
            encoding="utf-8")

    def sql(self, query: str, args: tuple = ()) -> list:
        conn = connect(self.db)
        try:
            rows = [tuple(r) for r in conn.execute(query, args).fetchall()]
            conn.commit()
            return rows
        finally:
            conn.close()

    def worker(self, task: str, *, base: Path | None = None, project_id: int = 1,
               status: str = "review") -> Path:
        base = base or self.base
        wdir = base / ".worktrees" / task
        self.git(base, "worktree", "add", "-q", "-b", f"feat/{task}", str(wdir), "main")
        self.sql("INSERT INTO worker_dirs (abs_path, layout, is_git_repo, is_worktree) "
                 "VALUES (?, 'project', 1, 1)", (str(wdir),))
        (wid,) = self.sql("SELECT id FROM worker_dirs WHERE abs_path = ?", (str(wdir),))[0]
        self.sql("INSERT INTO runs (task_id, project_id, pattern, title, status, worker_dir_id, branch) "
                 "VALUES (?, ?, 'B', ?, ?, ?, ?)", (task, project_id, task, status, wid, f"feat/{task}"))
        self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES "
                 "('2026-11-22T10:00:00.000Z', 'secretary', 'delegate_sent', ?)",
                 (json.dumps({"task": task, "worker": f"worker-{task}", "dir": str(wdir),
                              "base_ref": "origin/main"}),))
        return wdir

    def commit(self, wdir: Path, message: str, name: str = "f.txt") -> str:
        (wdir / name).write_text(message, encoding="utf-8")
        self.git(wdir, "add", name)
        self.git(wdir, *ID, "commit", "-q", "-m", message)
        return self.git(wdir, "rev-parse", "HEAD")

    def tool(self, *args: str, stdin: str | None = None, cwd: Path | None = None):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        fake = self.tmp / "bin"
        if fake.is_dir():
            env["PATH"] = f"{fake}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            [sys.executable, str(TOOL), "--root", str(self.root), "--db-path", str(self.db), *args],
            capture_output=True, text=True, input=stdin, cwd=str(cwd or self.tmp), timeout=120,
            env=env,
        )

    def fake_gh(self) -> Path:
        """A gh that records its arguments (one per line) and prints a PR URL."""
        fake = self.tmp / "bin"
        fake.mkdir(exist_ok=True)
        calls = self.tmp / "gh-calls.txt"
        gh = fake / "gh"
        gh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{calls}"\n'
                      'echo https://github.com/o/p/pull/12\n', encoding="utf-8")
        gh.chmod(0o755)
        return calls

    def body(self, text: str) -> Path:
        path = self.tmp / "body.md"
        path.write_text(text, encoding="utf-8")
        return path

    def last(self, proc) -> str:
        lines = proc.stdout.strip().splitlines()
        return lines[-1] if lines else ""

    def checked(self, task: str) -> list[dict]:
        rows = self.sql("SELECT payload_json FROM events WHERE kind = 'presubmit_checked' ORDER BY id")
        return [p for p in (json.loads(r[0]) for r in rows) if p.get("task") == task]

    def one(self, task: str) -> dict:
        events = self.checked(task)
        self.assertEqual(len(events), 1, f"expected one presubmit_checked event for {task}")
        return events[0]

    def remote_head(self, branch: str) -> str:
        out = subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "--verify", "-q",
                              f"refs/heads/{branch}"], capture_output=True, text=True)
        return out.stdout.strip()

    # -- run ----------------------------------------------------------------

    def test_passing_steps_are_recorded_at_head(self):
        self.registry("true; echo second-step")
        wdir = self.worker("t1")
        head = self.commit(wdir, "work")
        proc = self.tool("run", "--task", "t1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.last(proc), "PRESUBMIT: passed")
        ev = self.one("t1")
        self.assertEqual(ev["head"], head)
        self.assertEqual(ev["result"], "passed")
        self.assertEqual([s["rc"] for s in ev["steps"]], [0, 0])
        self.assertTrue(Path(ev["log"]).is_file())
        self.assertIn("second-step", Path(ev["log"]).read_text(encoding="utf-8"))

    def test_a_failing_step_fails_the_check(self):
        self.registry("true; false; true")
        wdir = self.worker("t2")
        self.commit(wdir, "work")
        proc = self.tool("run", "--task", "t2")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(self.last(proc), "PRESUBMIT: failed")
        ev = self.one("t2")
        self.assertEqual(ev["result"], "failed")
        self.assertEqual(ev["steps"][1]["rc"], 1)

    def test_base_and_head_are_filled_in(self):
        self.registry('test "{head}" = "$(git rev-parse HEAD)"; '
                      'test "{base}" = "$(git rev-parse origin/main)"')
        wdir = self.worker("t3")
        self.commit(wdir, "work")
        proc = self.tool("run", "--task", "t3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_declared_test_commit_is_recorded(self):
        self.registry("true")
        wdir = self.worker("t4")
        m = self.commit(wdir, "test: failing test" + TRAILER, "tests_x.txt")
        self.commit(wdir, "fix")
        self.tool("run", "--task", "t4")
        ev = self.one("t4")
        self.assertEqual(ev["test_commit"], m)

    def test_no_setting_is_not_configured(self):
        wdir = self.worker("t5")
        self.commit(wdir, "work")
        proc = self.tool("run", "--task", "t5")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(self.last(proc), "PRESUBMIT: not-configured")

    def test_the_org_repository_uses_home_presubmit(self):
        self.config("\n## Presubmit\nhome_presubmit: echo home-check\n")
        wdir = self.worker("t6", base=self.root, project_id=2)
        self.commit(wdir, "work")
        proc = self.tool("run", "--task", "t6")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ev = self.one("t6")
        self.assertIn("home-check", Path(ev["log"]).read_text(encoding="utf-8"))

    # -- push ---------------------------------------------------------------

    def test_push_needs_a_passed_check_at_head(self):
        self.registry("true")
        wdir = self.worker("t7")
        head = self.commit(wdir, "work")
        refused = self.tool("push", "--task", "t7")
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("PRESUBMIT_PUSH: refused", self.last(refused))
        self.assertEqual(self.remote_head("feat/t7"), "")
        self.tool("run", "--task", "t7")
        ok = self.tool("push", "--task", "t7")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(self.remote_head("feat/t7"), head)

    def test_push_refuses_a_head_that_moved_after_the_check(self):
        self.registry("true")
        wdir = self.worker("t8")
        self.commit(wdir, "work")
        self.tool("run", "--task", "t8")
        self.commit(wdir, "more work")
        proc = self.tool("push", "--task", "t8")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PRESUBMIT_PUSH: refused", self.last(proc))
        self.assertEqual(self.remote_head("feat/t8"), "")

    def test_push_refuses_after_a_failed_check(self):
        self.registry("false")
        wdir = self.worker("t9")
        self.commit(wdir, "work")
        self.tool("run", "--task", "t9")
        proc = self.tool("push", "--task", "t9")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PRESUBMIT_PUSH: refused", self.last(proc))
        self.assertEqual(self.remote_head("feat/t9"), "")

    def test_push_of_the_test_commit_alone(self):
        self.registry("true")
        wdir = self.worker("t10")
        m = self.commit(wdir, "test: failing test" + TRAILER, "tests_x.txt")
        self.commit(wdir, "fix")
        self.tool("run", "--task", "t10")
        proc = self.tool("push", "--task", "t10", "--test-commit")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.remote_head("feat/t10"), m)

    # -- pr (closing references) --------------------------------------------

    # The start of the sentence in makiba PR 266 that closed Issue 50 on merge (2026-11-22),
    # as quoted by the secretary.
    PR266 = "It does not close #50: that Issue is closed by a person\n"

    def test_pr_refuses_a_negated_closing_reference(self):
        calls = self.fake_gh()
        wdir = self.worker("t14")
        self.commit(wdir, "work")
        proc = self.tool("pr", "--task", "t14", "--create", "--title", "Issue 266 work",
                         "--body-file", str(self.body(self.PR266)), "--closes", "266")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("close #50", proc.stdout)
        self.assertIn("PRESUBMIT_PR: refused", self.last(proc))
        self.assertFalse(calls.exists(), "gh was called although the body closes #50")

    def test_pr_refuses_references_in_commits_and_other_forms(self):
        calls = self.fake_gh()
        wdir = self.worker("t15")
        self.commit(wdir, "Fixes o/other#7 by the way")
        for body in ("Resolved: https://github.com/o/p/issues/9\n", "CLOSES   #3\n", ""):
            proc = self.tool("pr", "--task", "t15", "--create", "--title", "work",
                             "--body-file", str(self.body(body)))
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("o/other#7", proc.stdout)
        self.assertFalse(calls.exists())

    def test_pr_creates_with_the_listed_closing_reference(self):
        calls = self.fake_gh()
        wdir = self.worker("t16")
        self.commit(wdir, "work")
        proc = self.tool("pr", "--task", "t16", "--create", "--title", "Closes #4 work",
                         "--body-file", str(self.body("Closes #4\nRefs #5\n")), "--closes", "4")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        args = calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(args[:2], ["pr", "create"])
        for flag, value in (("--repo", "o/p"), ("--head", "feat/t16"), ("--title", "Closes #4 work")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--body-file", args)

    def test_pr_edit_is_checked_too(self):
        calls = self.fake_gh()
        wdir = self.worker("t17")
        self.commit(wdir, "work")
        refused = self.tool("pr", "--task", "t17", "--edit", "12", "--title", "work",
                            "--body-file", str(self.body(self.PR266)))
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(calls.exists())
        ok = self.tool("pr", "--task", "t17", "--edit", "12", "--title", "work",
                       "--body-file", str(self.body("Refs #50\n")))
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(calls.read_text(encoding="utf-8").splitlines()[:3], ["pr", "edit", "12"])

    # -- hook ---------------------------------------------------------------

    def hook(self, command: str, cwd: Path) -> str:
        payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}})
        proc = self.tool("hook", stdin=payload, cwd=cwd)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip().splitlines()[0]

    def test_hook_refuses_a_raw_push_of_an_open_run(self):
        wdir = self.worker("t11")
        self.assertTrue(self.hook("git push origin feat/t11", wdir).startswith("DENY"))
        self.assertTrue(self.hook(f"git -C {wdir} push origin HEAD", self.root).startswith("DENY"))
        # The same branch pushed from another checkout of the same repository.
        self.assertTrue(self.hook("git push origin feat/t11", self.base).startswith("DENY"))
        self.assertTrue(self.hook("git push origin abc123:refs/heads/feat/t11", self.base)
                        .startswith("DENY"))

    def test_hook_refuses_raw_pr_create_and_edit_of_an_open_run(self):
        wdir = self.worker("t18")
        self.sql("UPDATE runs SET pr_url = 'https://github.com/o/p/pull/31' WHERE task_id = 't18'")
        self.assertTrue(self.hook("gh pr create --repo o/p --head feat/t18 --title x --body y",
                                  self.root).startswith("DENY"))
        self.assertTrue(self.hook("gh pr create --title x --body y", wdir).startswith("DENY"))
        self.assertTrue(self.hook("gh pr edit 31 --repo o/p --body-file b.md", self.root)
                        .startswith("DENY"))
        self.assertEqual(self.hook("gh pr view 31 --repo o/p", self.root), "ALLOW")
        self.assertEqual(self.hook("python3 tools/presubmit.py pr --task t18 --edit 31 --title x"
                                   " --body-file b.md", self.root), "ALLOW")

    def test_hook_allows_the_tool_and_other_pushes(self):
        self.worker("t12")
        self.worker("t13", status="completed")
        self.assertEqual(self.hook("python3 tools/presubmit.py push --task t12", self.root), "ALLOW")
        self.assertEqual(self.hook("git push origin main", self.root), "ALLOW")
        self.assertEqual(self.hook("git push origin feat/t13", self.base), "ALLOW")
        self.assertEqual(self.hook("git status", self.base), "ALLOW")


if __name__ == "__main__":
    unittest.main()
