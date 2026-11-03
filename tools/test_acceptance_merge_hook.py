"""Tests for the secretary's pre-merge hook (Issue #1167): command parsing, the
CI-head binding (#1228), and the whole path (fake gh, a file:// remote, the real
gate) through the bash wrapper.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import acceptance_merge_hook as hook  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402
from tools.test_acceptance_gate import BUGGY, FIXED, TEST, TRAILER, Repo  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40
WRAPPER = Path(__file__).resolve().parent.parent / ".hooks" / "gate-acceptance-before-merge.sh"
VERB = "mer" + "ge"  # keep the literal command out of this file's own tool calls


def merge_cmd(n="7", repo="o/r", method="--squash", sha=SHA, tail=""):
    return f"gh pr {VERB} {n} --repo {repo} {method} --match-head-commit {sha}{tail}"


def record_ci(db: Path, pr: int, status: str, head_oid: str | None, repo: str = "o/r") -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    new = not db.exists()
    conn = connect(db)
    if new:
        apply_schema(conn)
    payload = {"pr": pr, "repo": repo, "status": status, "duration_sec": 1,
               "head": (head_oid or OTHER)[:7]}
    if head_oid:
        payload["head_oid"] = head_oid
    w = StateWriter(conn)
    w.append_event(kind="ci_completed", actor="pr_watch", payload=payload)
    w.commit()
    conn.close()


class ParseTest(unittest.TestCase):
    def test_not_a_merge(self):
        for cmd in (f"git {VERB} main", "gh pr view 3", "gh pr checks 3", f"echo {VERB}",
                    "git log --merges", f"grep -n {VERB} /x/y.md", f"git {VERB}-base a b"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(hook.parse(cmd))

    def test_issue_1229_cases_pass(self):
        """The six commands the hook stopped on 2026-11-03 (Issue #1229), each typed on its own."""
        for cmd in (
            "gh pr view 1227 --repo o/r --json state,mergedAt",
            f'gh pr create --repo o/r --title "fix the {VERB} gate" --body-file .worker-scratch/body.md',
            "gh pr view 1227 --repo o/r --json mergedAt,body",
            'codex exec "Review tools/acceptance_merge_hook.py and .hooks/gate-acceptance-before-merge.sh"',
            "grep -n DENY $CLAUDE_ORG_PATH/tools/acceptance_merge_hook.py",
            "head -40 /r/.hooks/gate-acceptance-before-merge.sh",
            # Multi-line: judged by the broad rule, so it passes while the body has no gh / pull / $ / backtick.
            "cat >> .state/workers/worker-x.md <<'EOF'\n- merge_approval_mode: acceptance\nEOF",
        ):
            with self.subTest(cmd=cmd):
                self.assertIsNone(hook.parse(cmd))

    def test_more_allow_listed_commands_pass(self):
        for cmd in (f'gh issue create --title "{VERB} queue" --body x', f"gh pr list --search {VERB}",
                    f'gh pr comment 3 --body "ready to {VERB}"', "gh pr view 3 --json mergeable,autoMergeRequest",
                    "gh -R o/r pr view 3 --json mergedAt", f"gh search prs {VERB} --repo o/r",
                    "gh pr view $PR --repo o/r --json mergedAt", "gh pr checks 3 --repo o/r 2>/dev/null",
                    "sed -n 10,40p tools/acceptance_merge_hook.py", "tail -n 5 $LOG/pr-merge.log"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(hook.parse(cmd))

    def test_word_inside_a_longer_name_is_not_judged(self):
        """A file name or JSON field that contains the word cannot be the subcommand, whatever surrounds it."""
        m = VERB
        for cmd in (f"grep -n gh /r/acceptance_{m}_hook.py | head", f"gh pr view 3 --json {m}dAt; echo $X",
                    f"cat >> f.md <<'EOF'\n{m}_approval_mode: $MODE\nEOF",
                    f"gh pr view 3 --json {m}dAt # note", f"gh pr view 3 \\\n --json {m}dAt",
                    f"gh pr view `echo 3` --json {m}dAt", f"git log --{m}s; echo $?",
                    f"bash .hooks/gate-acceptance-before-{m}.sh < $F"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(hook.parse(cmd))

    def test_not_allow_listed_falls_back_to_the_broad_deny(self):
        """Issue #1229 option (c): chaining, substitution or an unknown program is denied as before."""
        m = VERB
        for cmd in (f'gh pr create --title "{m} gate" --body "$(cat body.md)"',
                    f"gh pr view 3 --json mergedAt && gh pr {m} 3",
                    f"cat f <(gh pr {m} 3)", f"cat <<EOF\n$(gh pr {m} 3)\nEOF",
                    f"cat <<'EOF'\nx\nEOF\ngh pr {m} 3", f"sed -n '1e gh pr {m} 3' f",
                    f"cat <<'EOF'\nnotes\nEOF\ngh pr {m} 7 --repo o/r --squash\ncat <<'EOF'\nnotes\nEOF",
                    # No heredoc exception, no `#`: multi-line and commented commands use the broad rule.
                    f'cat /dev/null # <<"EOF"\ngh pr {m} 7 --repo o/r --squash\nEOF',
                    f"gh pr create --title {m} --body-file - <<'EOF'\nbody\nEOF",
                    f"sed -n -e 1p -e '1e gh pr {m} 7' f", f"sed -n 1p f -e '1e gh pr {m} 7'",
                    f"sed -n 1p -f s.sed f # gh {m}",
                    f"less '+!gh pr {m} 3' f", f"rg --pre $X {m} f", f"grep x f 2>&1 | gh pr {m} 3",
                    f'codex exec "review $CLAUDE_ORG_PATH/{m}.py"'):
            with self.subTest(cmd=cmd), self.assertRaises(hook.Deny):
                hook.parse(cmd)

    def test_the_allowed_form(self):
        for method in ("squash", VERB, "rebase"):
            self.assertEqual(hook.parse(merge_cmd(n="3", method=f"--{method}")), ("3", "o/r", SHA))
        self.assertEqual(hook.parse(merge_cmd(n="12", repo="suisya-systems/claude-org-ja",
                                              tail=" --delete-branch\n")),
                         ("12", "suisya-systems/claude-org-ja", SHA))

    def test_every_other_merge_is_denied(self):
        m = VERB
        for cmd in (f"gh pr {m} 3 --repo o/r --squash",  # without --match-head-commit (#1228)
                    merge_cmd(sha="abc1234"), merge_cmd(sha=SHA.upper()),
                    f"gh pr {m} 3", f"gh pr {m} 3 --squash", f"gh pr {m} --repo o/r 3 --squash",
                    f"gh pr {m} 3 --squash --repo o/r", merge_cmd(tail=" --auto"),
                    merge_cmd(tail=" --disable-auto"), f"gh pr {m} 3 -R o/r --squash",
                    f"gh pr {m} 3 --repo=o/r --squash", "gh  " + merge_cmd()[3:],
                    merge_cmd() + " && git pull", "git pull; " + merge_cmd(),
                    merge_cmd() + "\n--disable-auto", f"gh pr \\\n {m} 3 --repo o/r --squash",
                    f'bash -c "{merge_cmd()}"', f"gh pr {m} $PR --repo o/r --squash",
                    "gh pr m'erge' 3", f"gh api -X PUT repos/o/r/pulls/3/{m}",
                    f"curl -X PUT https://api.github.com/repos/o/r/pulls/3/{m}",
                    "gh api graphql -f query='mutation{mergePullRequest(input:{})}'",
                    "gh api graphql -f query='mutation{enablePullRequestAutoMerge(input:{})}'",
                    f"gh pr {m.upper()} 3", f"x={m}; gh pr $x 3", merge_cmd(tail=" --body x"),
                    # Issue #1229 narrowing must not open these.
                    f"gh pr view 7\ngh pr {m} 7 --repo o/r --squash",
                    f'gh pr create --title t && gh pr {m} 3', f"g=gh; $g pr {m} 3",
                    f"$(echo gh) pr {m} 3", f"`echo gh` pr {m} 3", f'c="x {m}"; eval "$c"',
                    f"python3 -c \"import subprocess; subprocess.run(['gh','pr','{m}','3'])\"",
                    f"gh $p {m} 3", f"gh alias set pm 'pr {m}'", f"/usr/bin/gh pr {m} 3",
                    f"gh -R o/r pr {m} 3 --squash", f"gh.exe pr {m} 7 --repo o/r --squash",
                    f"C:\\Program Files\\GitHub CLI\\gh.exe pr {m} 7", f"/c/tools/gh.exe pr {m} 7",
                    "gh pr mer\\\nge 7 --repo o/r --squash", f"gh pr ${{ACTION:-{m}}} 7 --repo o/r --squash",
                    f"gh ${{G:-pr}} {m} 7", f"${{GH:-gh}} pr {m} 7",
                    f"gh pr ${{ACTION-{m}}} 7 --repo o/r --squash", f"gh pr ${{A-${{B-{m}}}}} 7",
                    f"gh pr ${{A-{m}",
                    f'env "$GH" pr {m} 7 --repo o/r --squash', f'command "$GH" pr {m} 7',
                    f"sudo $GH pr {m} 7", f"$GH $P {m} 7", f"xargs -I{{}} $GH pr {m} {{}}",
                    f"env GH=1 $GH pr ${{X:-{m}}} 7"):
            with self.subTest(cmd=cmd), self.assertRaises(hook.Deny):
                hook.parse(cmd)


class CiHeadTest(unittest.TestCase):
    """The latest ci_completed row for the PR decides (Issue #1228)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "state.db"
        self._env = mock.patch.dict(os.environ, {"STATE_DB_PATH": str(self.db)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_no_db_or_no_row(self):
        self.assertIsNone(hook.ci_passed_head("o/r", 7))
        record_ci(self.db, 8, "passed", SHA)
        record_ci(self.db, 7, "passed", SHA, repo="o/other")
        self.assertIsNone(hook.ci_passed_head("o/r", 7))

    def test_latest_row_wins(self):
        record_ci(self.db, 7, "passed", SHA)
        self.assertEqual(hook.ci_passed_head("O/R", 7), SHA)
        record_ci(self.db, 7, "passed", OTHER)  # pushed after CI, new head passed
        self.assertEqual(hook.ci_passed_head("o/r", 7), OTHER)
        record_ci(self.db, 7, "failed", SHA)
        self.assertIsNone(hook.ci_passed_head("o/r", 7))

    def test_row_without_full_head(self):
        record_ci(self.db, 7, "passed", None)
        self.assertIsNone(hook.ci_passed_head("o/r", 7))

    def test_decide_checks_head_and_ci_before_fetching(self):
        calls = []

        def fake_run(cmd, cwd, deadline, limit):
            calls.append(cmd[:2])
            if cmd[:2] == ["gh", "pr"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(
                    {"number": 7, "url": "https://github.com/o/r/pull/7", "baseRefName": "main",
                     "headRefOid": self.head}), "")
            raise AssertionError(f"ran {cmd[:2]} before the CI binding")

        with mock.patch.object(hook, "run", side_effect=fake_run):
            self.head = OTHER  # pushed after the secretary read the SHA
            record_ci(self.db, 7, "passed", SHA)
            with self.assertRaisesRegex(hook.Deny, "PR head is"):
                hook.decide(merge_cmd(), Path("."))
            self.head = SHA
            record_ci(self.db, 7, "passed", OTHER)  # CI saw a later head
            with self.assertRaisesRegex(hook.Deny, "no passed ci_completed"):
                hook.decide(merge_cmd(), Path("."))
        self.assertEqual(calls, [["gh", "pr"], ["gh", "pr"]])

    def test_script_runs_without_pythonpath(self):
        """The wrapper runs the file as a script: `tools` must import from any cwd."""
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": merge_cmd()}})
        res = subprocess.run([sys.executable, str(Path(hook.__file__))], input=payload,
                             capture_output=True, text=True, env=dict(env, PATH="/nonexistent"),
                             cwd=self._tmp.name, timeout=60)
        self.assertNotIn("ModuleNotFoundError", res.stdout + res.stderr)
        self.assertTrue(res.stdout.startswith("DENY:"), res.stdout)  # no gh on PATH


@unittest.skipUnless(shutil.which("bwrap") and shutil.which("jq"), "needs bwrap and jq")
class HookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.origin = Repo(tmp / "origin")
        self.base = self.origin.commit("base", {"calc.py": BUGGY, "pyproject.toml": "[project]\nname = 'x'\n"})
        self.project = tmp / "project"
        subprocess.run(["git", "clone", "-q", str(self.origin.root), str(self.project)], check=True)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.db = tmp / "state.db"
        self.env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                        CLAUDE_PROJECT_DIR=str(self.project), STATE_DB_PATH=str(self.db))

    def tearDown(self):
        self._tmp.cleanup()

    def open_pr(self, *commits: tuple[str, dict], ci: bool = True) -> str:
        """Commit on a branch of origin, publish it as refs/pull/7/head, fake gh for it."""
        self.origin.git("checkout", "-q", "-B", "feature", self.base)
        for msg, files in commits:
            head = self.origin.commit(msg, files)
        self.origin.git("update-ref", "refs/pull/7/head", head)
        self.origin.git("checkout", "-q", "main")
        view = {"number": 7, "url": f"file://{self.origin.root}/pull/7",
                "baseRefName": "main", "headRefOid": head}
        gh = self.bin / "gh"
        gh.write_text(f"#!/bin/sh\necho '{json.dumps(view)}'\n")
        gh.chmod(0o755)
        if ci:
            record_ci(self.db, 7, "passed", head)
        return head

    def run_hook(self, command: str) -> tuple[int, str]:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        res = subprocess.run(["bash", str(WRAPPER)], input=payload, capture_output=True,
                             text=True, env=self.env, cwd=self.project, timeout=300)
        return res.returncode, res.stderr

    def test_passing_pr_allowed(self):
        head = self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}))
        self.assertEqual(self.run_hook(merge_cmd(sha=head)), (0, ""))

    def test_rejected_pr_denied(self):
        head = self.open_pr(("test" + TRAILER, {"tests/test_calc.py": TEST}), ("fix", {"calc.py": FIXED}),
                            ("weaken", {"tests/test_calc.py": "def test_add():\n    pass\n"}))
        rc, err = self.run_hook(merge_cmd(sha=head))
        self.assertEqual(rc, 2)
        self.assertIn("ACCEPTANCE_GATE rejected", err)

    def test_no_manifest_allowed(self):
        head = self.open_pr(("fix", {"calc.py": FIXED}))
        self.assertEqual(self.run_hook(merge_cmd(sha=head))[0], 0)

    def test_push_after_ci_denied(self):
        """Issue #1228: CI passed on the first head, then a commit was pushed."""
        first = self.open_pr(("fix", {"calc.py": FIXED}))
        head = self.open_pr(("fix", {"calc.py": FIXED}), ("more", {"x.py": "x = 1\n"}), ci=False)
        record_ci(self.db, 7, "passed", first)
        for sha in (first, head):
            rc, err = self.run_hook(merge_cmd(sha=sha))
            self.assertEqual(rc, 2)
        self.assertIn("no passed ci_completed", err)

    def test_other_merge_form_is_denied_without_running_the_gate(self):
        (self.bin / "gh").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "gh").chmod(0o755)
        for cmd in (f"gh pr {VERB} 7 --squash", f"gh pr {VERB.upper()} 7 --repo o/r --squash"):
            rc, err = self.run_hook(cmd)
            self.assertEqual(rc, 2)
            self.assertIn("merge with exactly", err)

    def test_gh_failure_denied(self):
        (self.bin / "gh").write_text("#!/bin/sh\necho 'no auth' >&2\nexit 1\n")
        (self.bin / "gh").chmod(0o755)
        rc, err = self.run_hook(merge_cmd())
        self.assertEqual(rc, 2)
        self.assertIn("gh pr view failed", err)

    def test_allow_line_from_a_crashed_python_is_denied(self):
        py = self.bin / "python3"
        py.write_text("#!/bin/sh\necho ALLOW\nexit 1\n")
        py.chmod(0o755)
        self.env["PATH"] = f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"  # no py on PATH
        self.assertEqual(self.run_hook(merge_cmd())[0], 2)

    def test_unrelated_command_skips_python(self):
        (self.bin / "python3").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "python3").chmod(0o755)
        self.assertEqual(self.run_hook("git status"), (0, ""))

    def test_issue_1229_read_passes_the_wrapper(self):
        self.assertEqual(self.run_hook("gh pr view 7 --repo o/r --json state,mergedAt"), (0, ""))


if __name__ == "__main__":
    unittest.main()
