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

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Loaded by path, not as ``tests.test_presubmit``: importing the ``tests``
# package runs its __init__, which scrubs the transport env for the whole
# process and changes what later tools/ tests see. Kept as a module object so
# its test class is not collected here.
_spec = importlib.util.spec_from_file_location("_presubmit_fixture", _ROOT / "tests" / "test_presubmit.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


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


    def test_cd_into_the_worker_dir_is_followed(self):
        wdir = self.worker("s3")
        self.assertTrue(self.hook(f"cd {wdir} && git push origin HEAD", self.root).startswith("DENY"))
        self.assertTrue(self.hook(f"cd {wdir}; gh pr create --title x --body y", self.root)
                        .startswith("DENY"))
        self.assertTrue(self.hook("cd $W && git push origin HEAD", self.root).startswith("DENY"))
        self.assertEqual(self.hook(f"cd {self.root} && git push origin main", wdir), "ALLOW")

    def test_pr_edit_matches_the_repository_too(self):
        self.worker("s4")
        self.sql("UPDATE runs SET pr_url = 'https://github.com/o/p/pull/12' WHERE task_id = 's4'")
        self.assertTrue(self.hook("gh pr edit 12 --repo o/p --body-file b.md", self.root).startswith("DENY"))
        self.assertTrue(self.hook("gh pr edit https://github.com/o/p/pull/12 --body-file b.md", self.root)
                        .startswith("DENY"))
        self.assertEqual(self.hook("gh pr edit 12 --repo other/repo --body-file b.md", self.root), "ALLOW")


    def test_prefixed_pushes_are_still_read(self):
        wdir = self.worker("s5")
        for command in ("env GIT_TERMINAL_PROMPT=0 git push origin HEAD",
                        "command git push origin HEAD",
                        "GIT_TERMINAL_PROMPT=0 git push origin HEAD",
                        "env -i PATH=/usr/bin git push origin HEAD",
                        "GH_DEBUG=1 gh pr create --title x --body y"):
            with self.subTest(command=command):
                self.assertTrue(self.hook(command, wdir).startswith("DENY"))


    def test_attached_dash_c_and_subshell_cd(self):
        wdir = self.worker("s6")
        self.assertTrue(self.hook(f"git -C{wdir} push origin HEAD", self.root).startswith("DENY"))
        self.assertTrue(self.hook("(cd /tmp); git push origin HEAD", wdir).startswith("DENY"))


class ToolFixesTests(base.PresubmitTests):
    def test_closing_reference_on_the_dispatch_base_counts_for_another_pr_base(self):
        self.fake_gh()
        # The task is dispatched from develop, which has a commit main does not.
        self.git(self.base, "checkout", "-q", "-b", "develop", "main")
        self.commit(self.base, "Fixes #9 on develop", "dev.txt")
        self.git(self.base, "push", "-q", "origin", "develop")
        self.git(self.base, "fetch", "-q", "origin")
        self.git(self.base, "checkout", "-q", "main")
        wdir = self.worker("u5")
        self.sql("UPDATE events SET payload_json = json_set(payload_json, '$.base_ref', 'origin/develop')"
                 " WHERE kind = 'delegate_sent'")
        self.git(wdir, "reset", "-q", "--hard", "origin/develop")
        self.commit(wdir, "work")
        ok = self.tool("pr", "--task", "u5", "--create", "--title", "work",
                       "--body-file", str(self.body("Refs #1\n")), "--base", "develop")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        refused = self.tool("pr", "--task", "u5", "--create", "--title", "work",
                            "--body-file", str(self.body("Refs #1\n")), "--base", "main")
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("#9", refused.stdout)

    def test_closing_reference_in_a_commit_brought_by_a_merge_is_refused(self):
        self.fake_gh()
        wdir = self.worker("u4")
        self.commit(wdir, "work")
        self.git(wdir, "checkout", "-q", "-b", "side", "main")
        self.commit(wdir, "Fixes #8 on the side", "side.txt")
        self.git(wdir, "checkout", "-q", "feat/u4")
        self.git(wdir, *base.ID, "merge", "-q", "--no-ff", "--no-edit", "side")
        proc = self.tool("pr", "--task", "u4", "--create", "--title", "work",
                         "--body-file", str(self.body("Refs #1\n")))
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("#8", proc.stdout)

    def test_run_refuses_uncommitted_tracked_changes(self):
        self.registry("true")
        wdir = self.worker("u1")
        self.commit(wdir, "work")
        (wdir / "f.txt").write_text("not committed", encoding="utf-8")
        proc = self.tool("run", "--task", "u1")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.checked("u1"), [])

    def test_force_replaces_the_branch_after_a_rebase(self):
        self.registry("true")
        wdir = self.worker("u2")
        self.commit(wdir, "work")
        self.tool("run", "--task", "u2")
        self.assertEqual(self.tool("push", "--task", "u2").returncode, 0)
        self.git(wdir, *base.ID, "commit", "-q", "--amend", "-m", "work, reworded")
        new = self.git(wdir, "rev-parse", "HEAD")
        self.tool("run", "--task", "u2")
        self.assertNotEqual(self.tool("push", "--task", "u2").returncode, 0)  # not a fast-forward
        proc = self.tool("push", "--task", "u2", "--force")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.remote_head("feat/u2"), new)

    def test_no_setting_warns_and_presubmit_required_refuses(self):
        wdir = self.worker("u3")
        self.commit(wdir, "work")
        self.tool("run", "--task", "u3")
        ok = self.tool("push", "--task", "u3")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("WARNING: no pre-submit check", self.last(ok))
        self.config("\n## Presubmit\npresubmit_required: yes\n")
        self.commit(wdir, "more")
        self.tool("run", "--task", "u3")
        refused = self.tool("push", "--task", "u3")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("presubmit_required", self.last(refused))


# Only the classes above run here; the inherited test_* methods belong to tests/test_presubmit.py.
for _name in [n for n in dir(base.PresubmitTests) if n.startswith("test_")]:
    setattr(HookPassThroughTests, _name, None)
    setattr(ToolFixesTests, _name, None)

if __name__ == "__main__":
    unittest.main()
