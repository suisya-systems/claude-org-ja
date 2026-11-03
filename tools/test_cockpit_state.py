"""Home-tab needs-decision marker (cockpit-home-needs-decision).

A fake tmux adapter stands in for runtime's TmuxAdapter; nothing here touches
a real tmux server.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BROKER = {"ORG_TRANSPORT": "broker"}


class FakeAdapter:
    """secretary_pane_id / set_agent_options / _tmux, enough for cockpit_state."""

    def __init__(self, home="%1", state=""):
        self.home, self.state, self.calls = home, state, []

    def secretary_pane_id(self):
        return self.home

    def set_agent_options(self, pane_id, **opts):
        self.calls.append(("set", pane_id, opts))
        self.state = opts["state"]

    def _tmux(self, *args, check=True):
        self.calls.append(("tmux", args))
        if args[0] == "show-option":
            return SimpleNamespace(returncode=0, stdout=self.state + "\n")
        if args[0] == "set-option" and "-wu" in args:
            self.state = ""
        return SimpleNamespace(returncode=0, stdout="")

    def wrote(self):
        return any(c[0] == "set" or (c[0] == "tmux" and c[1][0] == "set-option") for c in self.calls)


class MarkTest(unittest.TestCase):
    def test_awaiting_user_marks_home_tab(self):
        from tools import cockpit_state as cs
        a = FakeAdapter()
        self.assertTrue(cs.mark_needs_decision(adapter=a, env=BROKER))
        self.assertEqual(a.state, "needs-decision")
        self.assertEqual(a.calls[0], ("set", "%1", {"state": "needs-decision"}))

    def test_renga_transport_is_noop(self):
        from tools import cockpit_state as cs
        a = FakeAdapter()
        self.assertFalse(cs.mark_needs_decision(adapter=a, env={"ORG_TRANSPORT": "renga"}))
        self.assertEqual(a.calls, [])

    def test_no_home_pane_is_noop(self):
        from tools import cockpit_state as cs
        a = FakeAdapter(home=None)
        self.assertFalse(cs.mark_needs_decision(adapter=a, env=BROKER))
        self.assertFalse(a.wrote())

    def test_no_tmux_is_silent(self):
        from tools import cockpit_state as cs
        with mock.patch.object(cs, "_default_adapter", side_effect=FileNotFoundError("tmux")):
            self.assertFalse(cs.mark_needs_decision(env=BROKER))

    def test_tmux_error_is_silent(self):
        from tools import cockpit_state as cs
        a = FakeAdapter()
        a.set_agent_options = mock.Mock(side_effect=RuntimeError("tmux failed"))
        self.assertFalse(cs.mark_needs_decision(adapter=a, env=BROKER))

    def test_event_filter(self):
        from tools import cockpit_state as cs
        a = FakeAdapter()
        for ev, payload in [("worker_spawned", {}),
                            ("notify_sent", {"kind": "other"}),
                            ("notify_sent", {"kind": "awaiting_user", "gate": "escalation_reply_forward"}),
                            ("notify_sent", {"kind": "awaiting_user", "gate": "auto_handover_failed"})]:
            self.assertFalse(cs.mark_for_event(ev, payload, adapter=a, env=BROKER), (ev, payload))
        self.assertEqual(a.calls, [])
        self.assertTrue(cs.mark_for_event(
            "notify_sent", {"kind": "awaiting_user", "gate": "worker_completed"}, adapter=a, env=BROKER))


class ClearTest(unittest.TestCase):
    def prompt(self, text="進めて"):
        return json.dumps({"prompt": text})

    def test_user_input_clears_needs_decision(self):
        from tools import cockpit_state as cs
        a = FakeAdapter(state="needs-decision")
        env = dict(BROKER, TMUX_PANE="%1")
        self.assertTrue(cs.clear_for_prompt(self.prompt(), adapter=a, env=env))
        self.assertEqual(a.state, "")

    def test_other_states_untouched(self):
        from tools import cockpit_state as cs
        for st in ("waiting", "done", "working", ""):
            a = FakeAdapter(state=st)
            env = dict(BROKER, TMUX_PANE="%1")
            self.assertFalse(cs.clear_for_prompt(self.prompt(), adapter=a, env=env), st)
            self.assertEqual(a.state, st)
            self.assertFalse(a.wrote())

    def test_other_pane_does_not_clear(self):
        from tools import cockpit_state as cs
        a = FakeAdapter(state="needs-decision")
        for pane in ("%9", None):
            env = dict(BROKER)
            if pane:
                env["TMUX_PANE"] = pane
            self.assertFalse(cs.clear_for_prompt(self.prompt(), adapter=a, env=env))
        self.assertEqual(a.state, "needs-decision")

    def test_machine_prompts_do_not_clear(self):
        from tools import cockpit_state as cs
        a = FakeAdapter(state="needs-decision")
        env = dict(BROKER, TMUX_PANE="%1")
        for p in ('<channel source="org-broker-channel">x</channel>', "<task-notification>x"):
            self.assertFalse(cs.clear_for_prompt(self.prompt(p), adapter=a, env=env))
        for raw in ("", "not json", json.dumps({"prompt": 3})):
            self.assertFalse(cs.clear_for_prompt(raw, adapter=a, env=env))
        self.assertEqual(a.state, "needs-decision")

    def test_non_broker_is_noop(self):
        from tools import cockpit_state as cs
        a = FakeAdapter(state="needs-decision")
        env = {"ORG_TRANSPORT": "renga", "TMUX_PANE": "%1"}
        self.assertFalse(cs.clear_for_prompt(self.prompt(), adapter=a, env=env))
        self.assertEqual(a.calls, [])

    def test_ask_reply_forward_sequence(self):
        """ask marks, the user's prompt clears, forwarding the answer does not re-mark."""
        from tools import cockpit_state as cs
        a = FakeAdapter()
        env = dict(BROKER, TMUX_PANE="%1")
        cs.mark_for_event("notify_sent", {"kind": "awaiting_user", "gate": "escalation_to_user"}, adapter=a, env=env)
        self.assertEqual(a.state, "needs-decision")
        cs.clear_for_prompt(self.prompt(), adapter=a, env=env)
        cs.mark_for_event("notify_sent", {"kind": "awaiting_user", "gate": "escalation_reply_forward"}, adapter=a, env=env)
        self.assertEqual(a.state, "")


class JournalAppendTest(unittest.TestCase):
    def append(self, event, **fields):
        from tools import cockpit_state as cs, journal_append as ja
        a = FakeAdapter()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, BROKER), \
                mock.patch.object(cs, "_default_adapter", return_value=a):
            ja._db_append(Path(d) / "state.db", event, fields)
        return a

    def test_recording_awaiting_user_marks(self):
        a = self.append("notify_sent", kind="awaiting_user", task_id="t", gate="ci_green_merge_gate")
        self.assertEqual(a.state, "needs-decision")

    def test_other_events_do_not_mark(self):
        self.assertEqual(self.append("worker_spawned", task="t").calls, [])

    def test_marker_failure_never_fails_the_write(self):
        from tools import cockpit_state as cs, journal_append as ja
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, BROKER), \
                mock.patch.object(cs, "_default_adapter", side_effect=RuntimeError("boom")):
            ja._db_append(Path(d) / "state.db", "notify_sent", {"kind": "awaiting_user"})


class HookScriptTest(unittest.TestCase):
    def run_hook(self, stdin, env=None):
        e = {**os.environ, "ORG_TRANSPORT": "renga", **(env or {})}
        return subprocess.run(["bash", str(ROOT / ".hooks" / "clear-needs-decision.sh")],
                              input=stdin, capture_output=True, text=True, env=e, timeout=30)

    def test_always_exit_zero_and_silent(self):
        for stdin in ("", "garbage", json.dumps({"prompt": "進めて"})):
            r = self.run_hook(stdin)
            self.assertEqual((r.returncode, r.stdout), (0, ""), stdin)


if __name__ == "__main__":
    unittest.main()
