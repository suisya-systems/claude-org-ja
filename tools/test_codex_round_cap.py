"""Unit tests for tools.codex_round_cap (Codex review round cap)."""
from __future__ import annotations

import io
import json
import multiprocessing
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import codex_round_cap as cap  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402

ORG = "/org"
CANON = "/org/tools/codex_review_gate.sh"


def verdict(command: str) -> str:
    return cap.classify(command, CANON, ORG)[0]


class ClassifyTest(unittest.TestCase):
    def test_direct_codex_denied(self):
        for cmd in [
            "codex exec review --base x",
            "codex review",
            "/usr/bin/codex exec",
            '"cod"ex exec',
            "env X=1 codex exec",
            "env -S 'codex exec'",
            "FOO=1 codex",
            "exec codex",
            "command codex",
            "nohup codex exec &",
            "time codex",
            "nice -n 5 codex",
            "stdbuf -oL codex",
            "sudo -u x codex",
            "timeout -k 1 5 codex",
            "xargs -n1 codex",
            "npx -y @openai/codex exec",
            "npx -c 'codex exec'",
            "pnpm dlx @openai/codex",
            "bunx @openai/codex",
            'bash -c "codex exec"',
            'bash -lc "codex exec"',
            "sh -c 'codex review'",
            "eval codex exec",
            "echo $(codex exec)",
            "echo `codex exec`",
            "cat <(codex exec)",
            "ls; codex exec",
            "ls && codex exec",
            "ls | codex exec",
            "ls\ncodex exec",
            "(codex exec)",
            "{ codex exec; }",
            "if true; then codex exec; fi",
            "for i in 1; do codex exec; done",
            "! codex exec",
            "2>/dev/null codex exec",
            "> out codex exec",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "deny")

    def test_canonical_gate(self):
        for cmd in [
            "bash /org/tools/codex_review_gate.sh origin/main",
            'bash "$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" origin/main',
            "bash ${CLAUDE_ORG_PATH}/tools/codex_review_gate.sh HEAD~1",
            "bash /org/tools/../tools/codex_review_gate.sh origin/modernize/prompts-2026-10",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "gate")

    def test_non_canonical_gate_denied(self):
        for cmd in [
            "bash tools/codex_review_gate.sh base",
            "bash ./tools/codex_review_gate.sh base",
            "bash /work/tools/codex_review_gate.sh base",
            "sh /org/tools/codex_review_gate.sh base",
            "/org/tools/codex_review_gate.sh base",
            "bash /org/tools/codex_review_gate.sh",
            "bash /org/tools/codex_review_gate.sh a b",
            "bash /org/tools/codex_review_gate.sh base; true",
            "true && bash /org/tools/codex_review_gate.sh base",
            "bash /org/tools/codex_review_gate.sh base | tee log",
            "bash /org/tools/codex_review_gate.sh base > log",
            "X=1 bash /org/tools/codex_review_gate.sh base",
            "env bash /org/tools/codex_review_gate.sh base",
            "bash -x /org/tools/codex_review_gate.sh base",
            'bash -c "bash /org/tools/codex_review_gate.sh base"',
            'bash /org/tools/codex_review_gate.sh "$(rm -rf x)"',
            "bash /org/tools/codex_review_gate.sh base & bash /org/tools/codex_review_gate.sh base",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "deny")

    def test_unrelated_commands_allowed(self):
        for cmd in [
            "grep -n codex file",
            "git commit -m 'codex exec を塞ぐ'",
            'git commit -m "codex exec を塞ぐ"',
            "bash tests/test-codex-review-gate.sh",
            "python3 -m pytest tools/test_codex_round_cap.py",
            "ls",
            "echo 'a\nb codex'",
            "cat .codex-home/config.toml",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "allow")

    def test_unparsable_with_codex_fails_closed(self):
        self.assertEqual(verdict('echo "unbalanced codex'), "deny")
        self.assertEqual(verdict('echo "unbalanced'), "allow")


def _make_org(td: str, worker_dir: str = "/w/one", task: str = "t1") -> Path:
    db = Path(td) / ".state" / "state.db"
    db.parent.mkdir(parents=True)
    conn = connect(db)
    apply_schema(conn)
    w = StateWriter(conn)
    w.append_event(kind="delegate_sent", payload={"task": task, "dir": worker_dir})
    w.commit()
    conn.close()
    return db


def _hook(org: str, worker_dir: str, command: str) -> str:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return cap.run_hook(payload, {"WORKER_DIR": worker_dir, "CLAUDE_ORG_PATH": org})


def _consume(args):
    db, wdir = args
    return cap.consume_round(Path(db), wdir)[0]


class StateTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.org = self._td.name
        self.db = _make_org(self.org)
        self.gate = f"bash {self.org}/tools/codex_review_gate.sh origin/main"

    def tearDown(self):
        self._td.cleanup()

    def _cli(self, *argv) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cap.main([*argv, "--db-path", str(self.db)])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def test_default_cap_and_set(self):
        for _ in range(3):
            self.assertEqual(_hook(self.org, "/w/one/", self.gate), "ALLOW")
        out = _hook(self.org, "/w/one", self.gate)
        self.assertTrue(out.startswith("DENY: CODEX_ROUND_DENIED"), out)
        self.assertEqual(self._cli("status", "--task-id", "t1"),
                         {"task": "t1", "used": 3, "allowed": 3, "denied": 1})
        st = self._cli("set", "--task-id", "t1", "--allowed", "4", "--reason", "human ok")
        self.assertEqual(st["allowed"], 4)
        self.assertEqual(_hook(self.org, "/w/one", self.gate), "ALLOW")
        self.assertTrue(_hook(self.org, "/w/one", self.gate).startswith("DENY"))
        self._cli("set", "--task-id", "t1", "--allowed", "unlimited", "--reason", "x")
        self.assertEqual(_hook(self.org, "/w/one", self.gate), "ALLOW")

    def test_set_rejects_bad_allowed(self):
        with self.assertRaises(SystemExit):
            cap.main(["set", "--task-id", "t1", "--allowed", "-1", "--reason", "x",
                      "--db-path", str(self.db)])

    def test_cli_never_creates_db(self):
        missing = Path(self.org) / "nope" / "state.db"
        self.assertEqual(cap.main(["status", "--task-id", "t1", "--db-path", str(missing)]), 1)
        self.assertFalse(missing.exists())

    def test_hook_fail_closed(self):
        self.assertTrue(_hook(self.org, "/w/other", self.gate).startswith("DENY"))
        self.assertTrue(_hook(self.org + "/missing", "/w/one",
                              f"bash {self.org}/missing/tools/codex_review_gate.sh b")
                        .startswith("DENY"))
        self.assertTrue(cap.run_hook("not json", {}).startswith("DENY"))
        self.assertTrue(_hook("", "/w/one", self.gate).startswith("DENY"))

    def test_newest_delegate_sent_wins(self):
        conn = connect(self.db)
        w = StateWriter(conn)
        w.append_event(kind="delegate_sent", payload={"task": "t2", "dir": "/w/one"})
        w.commit()
        conn.close()
        self.assertEqual(_hook(self.org, "/w/one", self.gate), "ALLOW")
        self.assertEqual(self._cli("status", "--task-id", "t2")["used"], 1)
        self.assertEqual(self._cli("status", "--task-id", "t1")["used"], 0)

    def test_concurrent_consumers_at_allowance_one(self):
        self._cli("set", "--task-id", "t1", "--allowed", "1", "--reason", "x")
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(4) as pool:
            results = pool.map(_consume, [(str(self.db), "/w/one")] * 4)
        self.assertEqual(results.count(True), 1, results)
        st = self._cli("status", "--task-id", "t1")
        self.assertEqual((st["used"], st["denied"]), (1, 3))


if __name__ == "__main__":
    unittest.main()
