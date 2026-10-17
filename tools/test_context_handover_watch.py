"""Unit tests for tools.context_record and tools.context_handover_watch (Issue #1175)."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import context_handover_watch as watch  # noqa: E402
from tools import context_record as record  # noqa: E402
from tools import relay_scan  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402

MONITOR_PROMPT = ("references/worker-monitoring.md（ディスパッチャー cwd .dispatcher/ 基準）の"
                  "「監視ループ 1 サイクル」を 1 回だけ実行する")


class TestRecord(unittest.TestCase):
    def test_turns_open_and_close(self):
        rec = record.update({}, {"hook_event_name": "SessionStart", "session_id": "s1",
                                 "transcript_path": "/t1", "source": "startup"},
                            {"RENGA_PANE_ID": "7"}, "T0")
        self.assertEqual(rec["pane"], {"backend": "renga", "id": "7"})
        rec = record.update(rec, {"hook_event_name": "UserPromptSubmit"}, {}, "T1")
        self.assertNotEqual(rec["turn_open"], rec["turn_closed"])
        rec = record.update(rec, {"hook_event_name": "Stop", "session_id": "s1",
                                  "background_tasks": [], "session_crons": [{"id": "c"}]}, {}, "T2")
        self.assertEqual((rec["turn_open"], rec["turn_closed"]), (1, 1))
        self.assertEqual(rec["session_crons"], [{"id": "c"}])

    def test_stop_without_prompt_still_closes(self):
        rec = record.update({}, {"hook_event_name": "Stop", "session_id": "s"}, {}, "T")
        self.assertEqual(rec["turn_open"], rec["turn_closed"])

    def test_clear_marks_cleared_and_closes_open_turn(self):
        rec = {"turn_open": 4, "turn_closed": 3}
        rec = record.update(rec, {"hook_event_name": "SessionStart", "session_id": "s2",
                                  "source": "clear"}, {"TMUX_PANE": "%5"}, "T")
        self.assertEqual(rec["cleared_at"], "T")
        self.assertEqual(rec["turn_open"], 3)
        self.assertEqual(rec["pane"], {"backend": "tmux", "id": "%5"})

    def test_threshold_and_role(self):
        self.assertEqual(record.threshold_from_env({record.THRESHOLD_ENV: "5000"}), 5000)
        self.assertEqual(record.threshold_from_env({record.WINDOW_ENV: "1000000"}), 600000)
        self.assertEqual(record.threshold_from_env({}), 120000)
        self.assertEqual(record.role_for("/org/.dispatcher"), "dispatcher")
        self.assertEqual(record.role_for("/org"), "secretary")


def _line(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


class TestPure(unittest.TestCase):
    def test_last_usage_skips_sidechain_and_reads_input_side(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            usage = {"input_tokens": 1, "cache_creation_input_tokens": 10,
                     "cache_read_input_tokens": 100, "output_tokens": 999}
            p.write_text("\n".join([
                _line(type="assistant", message={"usage": usage}),
                _line(type="assistant", isSidechain=True, message={"usage": {"input_tokens": 5}}),
                _line(type="user", message={"content": "x"}),
            ]), encoding="utf-8")
            self.assertEqual(watch.last_usage_tokens(p), 111)
            p.write_text(_line(type="user", message={"content": "x"}), encoding="utf-8")
            self.assertIsNone(watch.last_usage_tokens(p))

    def test_extract_late_inputs_three_forms_dedup_and_own_commands(self):
        ch = '<channel source="renga-peers" from_id="5">done</channel>'
        lines = [
            _line(type="user", origin={"kind": "human"},
                  message={"content": "<command-message>dispatcher-handover</command-message>\n"
                                      "<command-name>/dispatcher-handover</command-name>\n"
                                      "<command-args>auto op=OP1</command-args>"}),
            _line(type="queue-operation", operation="enqueue", content=ch),
            _line(type="attachment", attachment={"type": "queued_command", "prompt": ch}),
            _line(type="queue-operation", operation="enqueue", content="second"),
            _line(type="user", origin={"kind": "channel"}, message={"content": "second"}),
            _line(type="user", message={"content": [{"type": "tool_result"}]}),
            _line(type="user", isMeta=True, message={"content": "skill body"}),
            _line(type="user", origin={"kind": "human"}, message={"content": "typed by a person"}),
        ]
        self.assertEqual(watch.extract_late_inputs(lines, "OP1"),
                         [ch, "second", "typed by a person"])

    def test_not_quiet_reasons(self):
        now = watch.parse_iso("2026-10-18T00:10:00Z")
        base = {"session_id": "s", "stop_session_id": "s", "pane": {"backend": "renga", "id": "1"},
                "turn_open": 2, "turn_closed": 2, "stop_at": "2026-10-18T00:09:00Z",
                "background_tasks": []}
        self.assertIsNone(watch.not_quiet_reason(base, now))
        for change, reason in [({"turn_open": 3}, "turn_open"),
                               ({"stop_session_id": "old"}, "no_stop_in_current_session"),
                               ({"stop_at": "2026-10-17T23:00:00Z"}, "stop_stale"),
                               ({"background_tasks": [{"id": "b"}]}, "background_tasks"),
                               ({"pane": None}, "pane_unknown")]:
            self.assertEqual(watch.not_quiet_reason({**base, **change}, now), reason)


class FakeBackend:
    """A Claude composer: typed text sits there until Enter, then the session acts."""

    def __init__(self, sim):
        self.sim = sim

    def inspect(self, lines):
        rule = "─" * 20
        return [{"row": 0, "text": rule}, {"row": 1, "text": "❯ " + self.sim.composer},
                {"row": 2, "text": rule}]

    def send_text(self, text):
        self.sim.composer += self.sim.mangle(text)

    def send_enter(self):
        text, self.sim.composer = self.sim.composer, ""
        self.sim.enters.append(text)
        self.sim.on_submit(text)


class Sim:
    """Plays the dispatcher session and its hooks against a temp org."""

    def __init__(self, tc, org):
        self.tc, self.org = tc, org
        self.composer = ""
        self.enters = []
        self.mangle = lambda t: t
        self.resume_loop = "kept"
        self.drop_task_on_resume = None
        self.now = time.time()  # handover file mtimes are wall-clock
        self.transcript = org.state_dir / "old.jsonl"
        self.transcript.write_text(_line(type="assistant", message={"usage": {
            "input_tokens": 900}}) + "\n", encoding="utf-8")
        self.rec({"session_id": "s1", "stop_session_id": "s1", "transcript_path": str(self.transcript),
                  "pane": {"backend": "renga", "id": "3"}, "turn_open": 5, "turn_closed": 5,
                  "stop_at": self.iso(), "background_tasks": [], "threshold_tokens": 500,
                  "source": "startup", "session_crons": [{"prompt": MONITOR_PROMPT}]})

    def iso(self, offset=0.0):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.now + offset, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")

    def rec(self, data=None, **changes):
        cur = json.loads(self.org.record_path.read_text()) if self.org.record_path.exists() else {}
        cur.update(data or {})
        cur.update(changes)
        self.org.write_json(self.org.record_path, cur)
        return cur

    def on_submit(self, text):
        cur = self.rec()
        if text.startswith("/dispatcher-handover"):
            with open(self.transcript, "a", encoding="utf-8") as fh:
                fh.write(_line(type="queue-operation", operation="enqueue",
                               content="<channel>late report</channel>") + "\n")
            self.org.handover_path.write_text("worker-t1 in flight", encoding="utf-8")
            self.rec(turn_closed=cur["turn_closed"] + 1, turn_open=cur["turn_closed"] + 1,
                     stop_at=self.iso())
        elif text == "/clear":
            self.rec(session_id="s2", source="clear", cleared_at=self.iso(),
                     transcript_path=str(self.org.state_dir / "new.jsonl"))
        elif text.startswith("/dispatcher-resume"):
            op = text.split("op=")[1]
            self.org.append_event("dispatcher_resumed", {"op": op, "loop": self.resume_loop})
            if self.drop_task_on_resume:
                conn = connect(self.org.db_path)
                conn.execute("DELETE FROM runs WHERE task_id = ?", (self.drop_task_on_resume,))
                conn.commit()
                conn.close()
            self.rec(stop_session_id="s2", stop_at=self.iso(5))
            relay_scan.write_heartbeat(self.org.db_path, recipient=relay_scan.DEFAULT_RECIPIENT,
                                       surfaced=0, scanned_at=self.iso(10))


class TestWatcher(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        state = Path(self._td.name)
        conn = connect(state / "state.db")
        apply_schema(conn)
        conn.execute("INSERT INTO projects (slug, display_name) VALUES ('p', 'P')")
        conn.execute("INSERT INTO runs (task_id, project_id, pattern, title, status) "
                     "VALUES ('t1', 1, 'B', 't1', 'in_use')")
        conn.commit()
        conn.close()
        self.org = watch.Org(state)
        self.notes = []
        self.org.notify_secretary = lambda m: self.notes.append(m) or True
        self.sim = Sim(self, self.org)

        def sleep(sec):
            self.sim.now += sec

        self.w = watch.Watcher(self.org, backend_factory=lambda pane: FakeBackend(self.sim),
                               clock=lambda: self.sim.now, sleep=sleep)

    def tearDown(self):
        self._td.cleanup()

    def events(self, kind):
        conn = connect(self.org.db_path)
        try:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM events WHERE kind = ?", (kind,))]
        finally:
            conn.close()

    def op(self):
        return json.loads(self.org.op_path.read_text())

    def test_full_handover(self):
        self.assertTrue(self.w.tick().startswith("ran:done"))
        op = self.op()["op"]
        self.assertEqual(self.sim.enters, [f"/dispatcher-handover auto op={op}", "/clear",
                                           f"/dispatcher-resume auto op={op}"])
        late = self.org.context_dir / f"dispatcher-late-input-{op}.md"
        self.assertIn("late report", late.read_text())
        self.assertEqual(len(self.events("auto_handover_done")), 1)
        self.assertEqual(self.notes, [])

    def test_under_threshold_or_busy_does_nothing(self):
        self.sim.rec(threshold_tokens=10_000)
        self.assertEqual(self.w.tick(), "under_threshold")
        self.sim.rec(threshold_tokens=500, turn_open=6)
        self.assertEqual(self.w.tick(), "turn_open")
        self.assertEqual(self.sim.enters, [])

    def test_composer_not_empty_stops_without_typing(self):
        self.sim.composer = "half-typed"
        self.assertEqual(self.w.tick(), "ran:failed")
        self.assertEqual(self.sim.enters, [])
        self.assertEqual(self.sim.composer, "half-typed")
        self.assertEqual(self.op()["reason"], "composer_not_empty")
        self.assertTrue(self.notes[0].startswith("DISPATCHER_AUTO_HANDOVER_FAILED"))
        self.assertEqual(self.w.tick(), "backoff")

    def test_readback_mismatch_sends_no_enter_and_erases_nothing(self):
        self.sim.mangle = lambda t: t + "x"
        self.assertEqual(self.w.tick(), "ran:failed")
        self.assertEqual(self.sim.enters, [])
        self.assertTrue(self.sim.composer.endswith("x"))
        self.assertEqual(self.op()["reason"], "readback_mismatch")

    def test_handover_must_name_live_workers(self):
        orig = self.sim.on_submit

        def no_ids(text):
            orig(text)
            if text.startswith("/dispatcher-handover"):
                self.org.handover_path.write_text("nothing", encoding="utf-8")

        self.sim.on_submit = no_ids
        self.w.tick()
        self.assertTrue(self.op()["reason"].startswith("handover_missing_task_ids"))
        self.assertNotIn("/clear", self.sim.enters)

    def test_duplicate_monitoring_loop_fails(self):
        orig = self.sim.on_submit

        def two_loops(text):
            orig(text)
            if text.startswith("/dispatcher-resume"):
                self.sim.rec(session_crons=[{"prompt": MONITOR_PROMPT}, {"prompt": MONITOR_PROMPT}])

        self.sim.on_submit = two_loops
        self.w.tick()
        self.assertEqual(self.op()["reason"], "monitoring_loop_not_single: crons=2")
        self.assertIn("/dispatcher-resume", self.notes[0])

    def test_idle_dispatcher_needs_no_loop(self):
        conn = connect(self.org.db_path)
        conn.execute("UPDATE runs SET status = 'completed'")
        conn.commit()
        conn.close()
        self.sim.on_submit = (lambda orig: lambda t: (orig(t), t.startswith("/dispatcher-resume")
                              and self.sim.rec(session_crons=[])))(self.sim.on_submit)
        self.sim.resume_loop = "idle"
        self.assertTrue(self.w.tick().startswith("ran:done"))

    def test_loss_is_reported(self):
        self.sim.drop_task_on_resume = "t1"
        conn = connect(self.org.db_path)
        conn.execute("INSERT INTO runs (task_id, project_id, pattern, title, status) "
                     "VALUES ('t2', 1, 'B', 't2', 'in_use')")
        conn.commit()
        conn.close()
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: (orig(t), t.startswith("/dispatcher-handover") and
                                        self.org.handover_path.write_text("t1 t2"))
        self.w.tick()
        self.assertEqual(self.events("auto_handover_loss_suspected")[0]["reason"], "t1")
        self.assertTrue(any("LOSS_SUSPECTED" in n for n in self.notes))

    def test_restart_before_clear_starts_over(self):
        self.org.write_json(self.org.op_path, {"op": "old", "stage": "clearing",
                                               "old_session_id": "s1", "pane": {"id": "3"}})
        self.assertTrue(self.w.tick().startswith("ran:done"))
        self.assertNotEqual(self.op()["op"], "old")

    def test_restart_after_clear_continues_without_redoing_handover(self):
        self.sim.rec(session_id="s2", source="clear", cleared_at=self.sim.iso())
        self.org.write_json(self.org.op_path, {
            "op": "OPX", "stage": "clearing", "old_session_id": "s1", "pane": {"id": "3"},
            "old_transcript": str(self.sim.transcript), "p0": 0, "snapshot": {}})
        self.assertEqual(self.w.tick(), "continued:clearing")
        self.assertEqual(self.sim.enters, ["/dispatcher-resume auto op=OPX"])
        self.assertEqual(self.op()["stage"], "done")

    def _resuming_op(self, **changes):
        op = {"op": "OPZ", "stage": "resuming", "old_session_id": "s1", "new_session_id": "s2",
              "pane": {"backend": "renga", "id": "3"}, "snapshot": {}}
        op.update(changes)
        self.org.write_json(self.org.op_path, op)

    def test_restart_after_resume_typed_continues_in_same_session(self):
        self.sim.rec(session_id="s2", source="clear", cleared_at=self.sim.iso())
        self._resuming_op()
        self.assertEqual(self.w.tick(), "continued:resuming")
        self.assertEqual(self.op()["stage"], "done")

    def test_restart_after_suspend_start_does_not_type_into_old_pane(self):
        self.sim.rec(session_id="s7", source="startup", pane={"backend": "renga", "id": "9"})
        self._resuming_op()
        self.assertEqual(self.w.tick(), "failed")
        self.assertEqual(self.op()["reason"], "restart_session_replaced")
        self.assertEqual(self.sim.enters, [])

    def test_restart_with_unrelated_session_change_fails(self):
        self.sim.rec(session_id="s9", source="startup")
        self.org.write_json(self.org.op_path, {"op": "OPY", "stage": "handover",
                                               "old_session_id": "s1", "pane": {"id": "3"}})
        self.assertEqual(self.w.tick(), "failed")
        self.assertEqual(self.op()["reason"], "restart_state_indeterminate")

    def test_resume_resent_once_then_fails(self):
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: None if t.startswith("/dispatcher-resume") else orig(t)
        self.w.tick()
        resumes = [e for e in self.sim.enters if e.startswith("/dispatcher-resume")]
        self.assertEqual(len(resumes), 2)
        self.assertEqual(self.op()["reason"], "resume_not_observed")


if __name__ == "__main__":
    unittest.main()
