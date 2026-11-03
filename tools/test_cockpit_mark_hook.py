"""PostToolUse(Bash) hook marks the home tab when the secretary records awaiting_user.

journal_append runs inside the Bash sandbox (tmux unreachable), so the mark is
applied by a hook, which runs outside it. Fake adapter; no real tmux.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from test_cockpit_state import FakeAdapter  # noqa: E402

BROKER = {"ORG_TRANSPORT": "broker", "TMUX_PANE": "%1"}
CMD = 'bash tools/journal_append.sh notify_sent kind=awaiting_user task_id=T gate={gate} note="PR 1"'


def payload(command, tool="Bash"):
    return json.dumps({"tool_name": tool, "tool_input": {"command": command},
                       "tool_response": {"stdout": "", "stderr": ""}})


def mark(stdin, env=BROKER):
    from tools import cockpit_state as cs
    a = FakeAdapter()
    return cs.mark_for_tool(stdin, adapter=a, env=env), a


class MarkForToolTest(unittest.TestCase):
    def test_awaiting_user_command_marks(self):
        ok, a = mark(payload(CMD.format(gate="ci_green_merge_gate")))
        self.assertTrue(ok)
        self.assertEqual(a.state, "needs-decision")

    def test_excluded_gates_do_not_mark(self):
        for gate in ("escalation_reply_forward", "auto_handover_failed"):
            ok, a = mark(payload(CMD.format(gate=gate)))
            self.assertFalse(ok, gate)
            self.assertFalse(a.wrote(), gate)

    def test_other_commands_do_not_mark(self):
        for cmd in ("ls -la", "bash tools/journal_append.sh worker_spawned task=T",
                    "bash tools/journal_append.sh notify_sent kind=other task_id=T",
                    "echo kind=awaiting_user"):
            ok, a = mark(payload(cmd))
            self.assertFalse(ok, cmd)
            self.assertFalse(a.wrote(), cmd)

    def test_non_bash_tool_does_not_mark(self):
        ok, _ = mark(payload(CMD.format(gate="x"), tool="Write"))
        self.assertFalse(ok)

    def test_noop_off_broker_or_off_home_pane(self):
        cmd = payload(CMD.format(gate="ci_green_merge_gate"))
        for env in ({**BROKER, "ORG_TRANSPORT": "renga"}, {**BROKER, "TMUX_PANE": "%9"},
                    {"ORG_TRANSPORT": "broker"}):
            ok, a = mark(cmd, env=env)
            self.assertFalse(ok, env)
            self.assertFalse(a.wrote(), env)

    def test_garbage_input_is_noop(self):
        for s in ("", "garbage", "[]", json.dumps({"tool_input": 1})):
            self.assertFalse(mark(s)[0], s)


class HookScriptTest(unittest.TestCase):
    def test_exit_zero_and_silent(self):
        e = {**os.environ, "ORG_TRANSPORT": "renga"}
        for stdin in ("", "garbage", payload(CMD.format(gate="x"))):
            r = subprocess.run(["bash", str(ROOT / ".hooks" / "mark-needs-decision.sh")],
                               input=stdin, capture_output=True, text=True, env=e, timeout=30)
            self.assertEqual((r.returncode, r.stdout), (0, ""), stdin)
            self.assertEqual(r.stderr, "", stdin)


if __name__ == "__main__":
    unittest.main()
