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
        self.assertIsNone(record.threshold_from_env({}))
        self.assertIsNone(record.threshold_from_env({record.THRESHOLD_ENV: "x"}))
        root = Path("/org")
        self.assertEqual(record.role_for("/org/.dispatcher", root), "dispatcher")
        self.assertEqual(record.role_for("/org", root), "secretary")
        # The curator and worker worktrees read the root settings too: not recorded.
        self.assertIsNone(record.role_for("/org/.curator", root))
        self.assertIsNone(record.role_for("/org/.worktrees/t1", root))
        self.assertIsNone(record.role_for("/elsewhere", root))

    def test_resume_line_is_remembered_for_the_watcher(self):
        rec = {"turn_open": 3, "turn_closed": 3}
        for prompt in ("/secretary-resume auto op=20261026T010203Z",
                       "<command-name>/secretary-resume</command-name>\n"
                       "<command-args>auto op=20261026T010203Z</command-args>"):
            out = record.update(rec, {"hook_event_name": "UserPromptSubmit", "prompt": prompt}, {}, "T")
            self.assertEqual((out["resume_op"], out["resume_turn"]), ("20261026T010203Z", 4))
        out = record.update(rec, {"hook_event_name": "UserPromptSubmit",
                                  "prompt": "<channel>worker done</channel>"}, {}, "T")
        self.assertNotIn("resume_op", out)

    def test_compaction_from_another_session_is_dropped(self):
        rec = {"session_id": "s1", "pane": {"backend": "renga", "id": "3"}}
        compact = {"hook_event_name": "SessionStart", "session_id": "s9", "source": "compact"}
        self.assertTrue(record.foreign(rec, compact, {"RENGA_PANE_ID": "3"}))
        self.assertFalse(record.foreign(rec, {**compact, "session_id": "s1"}, {"RENGA_PANE_ID": "3"}))

    def test_another_pane_takes_the_record_only_when_renga_names_it_for_the_role(self):
        rec = {"session_id": "s1", "pane": {"backend": "renga", "id": "3"}}
        start = {"hook_event_name": "SessionStart", "session_id": "s2", "source": "startup"}
        env = {"RENGA_PANE_ID": "8"}
        self.assertTrue(record.foreign(rec, start, env, "secretary", lambda e: "my-own-claude"))
        self.assertFalse(record.foreign(rec, start, env, "secretary", lambda e: "secretary"))
        clear = {**start, "source": "clear"}
        self.assertFalse(record.foreign(rec, clear, env, "secretary", lambda e: "secretary"))
        self.assertTrue(record.foreign(rec, clear, env, "secretary", lambda e: None))

    def test_events_from_another_session_are_dropped(self):
        rec = {"session_id": "s1", "pane": {"backend": "renga", "id": "3"},
               "turn_open": 2, "turn_closed": 2}
        # A secretary whose shell sits in .dispatcher/ must not touch dispatcher.json.
        self.assertTrue(record.foreign(rec, {"hook_event_name": "Stop", "session_id": "s9"}, {}))
        self.assertFalse(record.foreign(rec, {"hook_event_name": "Stop", "session_id": "s1"}, {}))
        clear = {"hook_event_name": "SessionStart", "session_id": "s2", "source": "clear"}
        self.assertTrue(record.foreign(rec, clear, {"RENGA_PANE_ID": "8"}))
        self.assertFalse(record.foreign(rec, clear, {"RENGA_PANE_ID": "3"}))
        start = {"hook_event_name": "SessionStart", "session_id": "s2", "source": "startup"}
        self.assertFalse(record.foreign(rec, start, {"RENGA_PANE_ID": "8"}))

    def test_idle_prompt_goes_to_its_own_file(self):
        import io
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            ctx = Path(td)
            root = str(record.REPO_ROOT)
            for kind in ("permission_prompt", "idle_prompt"):
                payload = {"hook_event_name": "Notification", "session_id": "s", "cwd": root,
                           "notification_type": kind}
                with mock.patch.object(record, "context_dir", lambda: ctx), \
                        mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
                    record.main()
                if kind == "permission_prompt":
                    self.assertFalse((ctx / "secretary.idle.json").exists())
            idle = json.loads((ctx / "secretary.idle.json").read_text())
            self.assertEqual(idle["idle_session_id"], "s")
            self.assertFalse((ctx / "secretary.json").exists())


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

    def test_dispatcher_skips_empty_enqueue_of_a_broker_push(self):
        lines = [_line(type="queue-operation", operation="enqueue"),
                 _line(type="user", origin={"kind": "channel"}, message={"content": "push body"})]
        self.assertEqual(watch.extract_late_inputs(lines, "OP", "dispatcher"), ["push body"])

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

    def __init__(self, tc, org, role="dispatcher"):
        self.tc, self.org, self.role = tc, org, role
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
        cur = json.loads(self.org.record_path(self.role).read_text()) if self.org.record_path(self.role).exists() else {}
        cur.update(data or {})
        cur.update(changes)
        self.org.write_json(self.org.record_path(self.role), cur)
        return cur

    def on_submit(self, text):
        cur = self.rec()
        if text.startswith(f"/{self.role}-handover"):
            with open(self.transcript, "a", encoding="utf-8") as fh:
                fh.write(_line(type="queue-operation", operation="enqueue",
                               content="<channel>late report</channel>") + "\n")
            self.org.handover_path(self.role).write_text("worker-t1 in flight", encoding="utf-8")
            self.rec(turn_closed=cur["turn_closed"] + 1, turn_open=cur["turn_closed"] + 1,
                     stop_at=self.iso())
        elif text == "/clear":
            self.rec(session_id="s2", source="clear", cleared_at=self.iso(),
                     transcript_path=str(self.org.state_dir / "new.jsonl"))
        elif text.startswith(f"/{self.role}-resume"):
            op = text.split("op=")[1]
            self.org.append_event(f"{self.role}_resumed", {"op": op, "loop": self.resume_loop})
            late = self.org.context_dir / f"{self.role}-late-input-{op}.md"
            if late.exists():
                late.rename(late.with_name(late.stem + ".consumed.md"))
            if self.drop_task_on_resume:
                conn = connect(self.org.db_path)
                conn.execute("DELETE FROM runs WHERE task_id = ?", (self.drop_task_on_resume,))
                conn.commit()
                conn.close()
            self.rec(stop_session_id="s2", stop_at=self.iso(5))
            relay_scan.write_heartbeat(self.org.db_path, recipient=relay_scan.DEFAULT_RECIPIENT,
                                       surfaced=0, scanned_at=self.iso(10))


class _WatcherCase(unittest.TestCase):
    role = "dispatcher"

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
        self.sim = Sim(self, self.org, self.role)

        def sleep(sec):
            self.sim.now += sec

        self.pane_name = self.role
        self.w = watch.Watcher(self.org, backend_factory=lambda pane: FakeBackend(self.sim),
                               pane_namer=lambda pane: self.pane_name,
                               clock=lambda: self.sim.now, sleep=sleep)

    def tearDown(self):
        self._td.cleanup()

    def tick(self):
        """The watcher's result for this role ("<role>:<why>" in an idle tick)."""
        out = self.w.tick()
        for part in out.split():
            if part.startswith(f"{self.role}:"):
                return part.split(":", 1)[1]
        return out

    def events(self, kind):
        conn = connect(self.org.db_path)
        try:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM events WHERE kind = ?", (kind,))]
        finally:
            conn.close()

    def op(self):
        return json.loads(self.org.op_path.read_text())


class TestWatcher(_WatcherCase):
    def test_full_handover(self):
        self.assertTrue(self.tick().startswith("ran:done"))
        op = self.op()["op"]
        self.assertEqual(self.sim.enters, [f"/dispatcher-handover auto op={op}", "/clear",
                                           f"/dispatcher-resume auto op={op}"])
        late = self.org.context_dir / f"dispatcher-late-input-{op}.consumed.md"
        self.assertIn("late report", late.read_text())
        self.assertEqual(len(self.events("auto_handover_done")), 1)
        self.assertEqual(self.notes, [])

    def test_under_threshold_or_busy_does_nothing(self):
        self.sim.rec(threshold_tokens=10_000)
        self.assertEqual(self.tick(), "under_threshold")
        self.sim.rec(threshold_tokens=500, turn_open=6)
        self.assertEqual(self.tick(), "turn_open")
        self.assertEqual(self.sim.enters, [])

    def test_threshold_unset_skips_and_tells_secretary_once(self):
        cur = json.loads(self.org.record_path(self.role).read_text())
        cur.pop("threshold_tokens")
        self.org.write_json(self.org.record_path(self.role), cur)
        self.assertEqual(self.tick(), "threshold_unset")
        self.assertEqual(self.tick(), "threshold_unset")
        self.assertEqual(self.sim.enters, [])
        self.assertEqual(len(self.notes), 1)
        self.assertIn("threshold_unset", self.notes[0])
        self.assertEqual(len(self.events("auto_handover_skipped")), 1)

    def test_composer_not_empty_stops_without_typing(self):
        self.sim.composer = "half-typed"
        self.assertEqual(self.tick(), "ran:failed")
        self.assertEqual(self.sim.enters, [])
        self.assertEqual(self.sim.composer, "half-typed")
        self.assertEqual(self.op()["reason"], "composer_not_empty")
        self.assertTrue(self.notes[0].startswith("DISPATCHER_AUTO_HANDOVER_FAILED"))
        self.assertEqual(self.tick(), "backoff")

    def test_readback_mismatch_sends_no_enter_and_erases_nothing(self):
        self.sim.mangle = lambda t: t + "x"
        self.assertEqual(self.tick(), "ran:failed")
        self.assertEqual(self.sim.enters, [])
        self.assertTrue(self.sim.composer.endswith("x"))
        self.assertEqual(self.op()["reason"], "readback_mismatch")

    def test_handover_must_name_live_workers(self):
        orig = self.sim.on_submit

        def no_ids(text):
            orig(text)
            if text.startswith("/dispatcher-handover"):
                self.org.handover_path(self.role).write_text("nothing", encoding="utf-8")

        self.sim.on_submit = no_ids
        self.tick()
        self.assertTrue(self.op()["reason"].startswith("handover_missing_task_ids"))
        self.assertNotIn("/clear", self.sim.enters)

    def test_duplicate_monitoring_loop_fails(self):
        orig = self.sim.on_submit

        def two_loops(text):
            orig(text)
            if text.startswith("/dispatcher-resume"):
                self.sim.rec(session_crons=[{"prompt": MONITOR_PROMPT}, {"prompt": MONITOR_PROMPT}])

        self.sim.on_submit = two_loops
        self.tick()
        self.assertEqual(self.op()["reason"], "monitoring_loop_not_single: crons=2")
        self.assertIn("/dispatcher-resume", self.notes[0])

    def test_worker_finishing_during_verification_is_not_a_failure(self):
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: (orig(t), t.startswith("/dispatcher-resume")
                                        and self.sim.rec(session_crons=[]))
        answers = iter([True, True])  # monitored when verification starts, idle after
        self.org.monitored = lambda: next(answers, False)
        self.assertTrue(self.tick().startswith("ran:done"))
        self.assertEqual(self.notes, [])

    def test_idle_dispatcher_needs_no_loop(self):
        conn = connect(self.org.db_path)
        conn.execute("UPDATE runs SET status = 'completed'")
        conn.commit()
        conn.close()
        self.sim.on_submit = (lambda orig: lambda t: (orig(t), t.startswith("/dispatcher-resume")
                              and self.sim.rec(session_crons=[])))(self.sim.on_submit)
        self.sim.resume_loop = "idle"
        self.assertTrue(self.tick().startswith("ran:done"))

    def test_loss_is_reported(self):
        self.sim.drop_task_on_resume = "t1"
        conn = connect(self.org.db_path)
        conn.execute("INSERT INTO runs (task_id, project_id, pattern, title, status) "
                     "VALUES ('t2', 1, 'B', 't2', 'in_use')")
        conn.commit()
        conn.close()
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: (orig(t), t.startswith("/dispatcher-handover") and
                                        self.org.handover_path(self.role).write_text("t1 t2"))
        self.tick()
        self.assertEqual(self.events("auto_handover_loss_suspected")[0]["reason"], "t1")
        self.assertTrue(any("LOSS_SUSPECTED" in n for n in self.notes))

    def test_restart_before_clear_starts_over(self):
        self.org.write_json(self.org.op_path, {"op": "old", "stage": "clearing",
                                               "old_session_id": "s1", "pane": {"id": "3"}})
        self.assertTrue(self.tick().startswith("ran:done"))
        self.assertNotEqual(self.op()["op"], "old")

    def test_restart_after_clear_continues_without_redoing_handover(self):
        self.sim.rec(session_id="s2", source="clear", cleared_at=self.sim.iso())
        self.org.write_json(self.org.op_path, {
            "op": "OPX", "stage": "clearing", "old_session_id": "s1", "pane": {"id": "3"},
            "old_transcript": str(self.sim.transcript), "p0": 0, "snapshot": {}})
        self.assertEqual(self.tick(), "continued:clearing")
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
        self.assertEqual(self.tick(), "continued:resuming")
        self.assertEqual(self.op()["stage"], "done")

    def test_restart_after_suspend_start_does_not_type_into_old_pane(self):
        self.sim.rec(session_id="s7", source="startup", pane={"backend": "renga", "id": "9"})
        self._resuming_op()
        self.assertEqual(self.tick(), "failed")
        self.assertEqual(self.op()["reason"], "restart_session_replaced")
        self.assertEqual(self.sim.enters, [])

    def test_restart_with_unrelated_session_change_fails(self):
        self.sim.rec(session_id="s9", source="startup")
        self.org.write_json(self.org.op_path, {"op": "OPY", "stage": "handover",
                                               "old_session_id": "s1", "pane": {"id": "3"}})
        self.assertEqual(self.tick(), "failed")
        self.assertEqual(self.op()["reason"], "restart_state_indeterminate")

    def test_resume_resent_once_then_fails(self):
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: None if t.startswith("/dispatcher-resume") else orig(t)
        self.tick()
        resumes = [e for e in self.sim.enters if e.startswith("/dispatcher-resume")]
        self.assertEqual(len(resumes), 2)
        self.assertEqual(self.op()["reason"], "resume_not_observed")


class TestSecretary(_WatcherCase):
    """§3.3 / §4.2 / §6.1 for the secretary (renga, attention watcher running)."""

    role = "secretary"

    def setUp(self):
        super().setUp()
        self.org.transport = lambda: "renga"
        self.org.write_json(self.org.attention_path, {"pane_id": 9})
        self.sim.rec(session_crons=[], prompt_at=self.sim.iso(-120), stop_at=self.sim.iso(-60),
                     idle_at=self.sim.iso(-1), idle_session_id="s1")

    def awaiting(self, task_id, ago, gate="worker_completed"):
        from tools.state_db.writer import StateWriter

        conn = connect(self.org.db_path)
        StateWriter(conn).append_event(kind="notify_sent", payload={
            "kind": "awaiting_user", "task_id": task_id, "gate": gate},
            occurred_at=self.sim.iso(-ago))
        conn.commit()
        conn.close()

    def decision(self, status, **extra):
        self.org.write_json(self.org.state_dir / "pending_decisions.json", [
            {"task_id": "t1", "received_at": "2026-10-26T00:00:00Z", "raw_message": "?",
             "status": status, **extra}])

    def test_full_handover(self):
        self.assertTrue(self.tick().startswith("ran:done"))
        op = self.op()["op"]
        self.assertEqual(self.op()["role"], "secretary")
        self.assertEqual(self.sim.enters, [f"/secretary-handover auto op={op}", "/clear",
                                           f"/secretary-resume auto op={op}"])
        late = self.org.context_dir / f"secretary-late-input-{op}.consumed.md"
        self.assertIn("late report", late.read_text())
        self.assertEqual(self.events("auto_handover_done")[0]["role"], "secretary")
        self.assertEqual(self.notes, [])

    def test_disabled_without_attention_watcher_or_on_broker(self):
        self.org.attention_path.unlink()
        self.assertEqual(self.tick(), "attention_watcher_absent")
        self.org.write_json(self.org.attention_path, {"pane_id": 9})
        self.org.transport = lambda: "broker"
        self.assertEqual(self.tick(), "secretary_broker_unsupported")
        self.assertEqual(self.sim.enters, [])

    def test_open_decisions_hold_including_replied_not_forwarded(self):
        self.decision("pending")
        self.assertEqual(self.tick(), "pending_decision")
        self.decision("escalated", user_replied_at="2026-10-26T00:05:00Z")
        self.assertEqual(self.tick(), "pending_decision")
        self.decision("resolved", resolution_kind="to_worker")
        self.assertTrue(self.tick().startswith("ran:done"))

    def test_recent_question_to_a_person_holds_for_30_minutes(self):
        self.awaiting("t9", ago=60)
        self.assertEqual(self.tick(), "awaiting_user")
        self.assertEqual(self.sim.enters, [])

    def test_reply_forward_and_own_failure_lines_are_not_waiting(self):
        self.awaiting("t1", ago=60, gate="escalation_reply_forward")
        self.awaiting("auto-handover-x", ago=60, gate="auto_handover_failed")
        self.assertTrue(self.tick().startswith("ran:done"))

    def test_old_question_must_be_named_in_the_handover(self):
        self.awaiting("t9", ago=3600)
        self.assertEqual(watch.must_name(self.org, "secretary", self.sim.now), ["t1", "t9"])
        self.assertEqual(watch.must_name(self.org, "dispatcher", self.sim.now), ["t1"])
        self.tick()
        self.assertEqual(self.op()["reason"], "handover_missing_task_ids: t9")
        self.assertNotIn("/clear", self.sim.enters)

    def test_not_idle_until_idle_prompt_follows_the_last_stop(self):
        self.sim.rec(idle_at=self.sim.iso(-90))  # before the last Stop
        self.assertEqual(self.tick(), "not_idle_prompt")
        self.sim.rec(idle_at=self.sim.iso(-1), idle_session_id="old")
        self.assertEqual(self.tick(), "not_idle_prompt")
        self.assertEqual(self.sim.enters, [])

    def test_crons_hold(self):
        self.sim.rec(session_crons=[{"prompt": "/loop 5m something"}])
        self.assertEqual(self.tick(), "session_crons")

    def test_person_typing_before_start_only_waits(self):
        self.sim.composer = "half-typed by a person"
        self.assertEqual(self.tick(), "composer_not_empty")
        self.assertEqual(self.sim.enters, [])
        self.assertEqual(self.sim.composer, "half-typed by a person")
        self.assertEqual(self.notes, [])
        self.assertFalse(self.org.op_path.exists())

    def test_person_typing_before_clear_stops_and_beeps(self):
        orig = self.sim.on_submit

        def types(text):
            orig(text)
            if text.startswith("/secretary-handover"):
                self.sim.composer = "half-typed by a person"

        self.sim.on_submit = types
        self.assertEqual(self.tick(), "ran:failed")
        self.assertNotIn("/clear", self.sim.enters)
        self.assertEqual(self.sim.composer, "half-typed by a person")
        self.assertTrue(self.notes[0].startswith("SECRETARY_AUTO_HANDOVER_FAILED"))
        beep = [e for e in self.events("notify_sent") if e.get("gate") == "auto_handover_failed"]
        self.assertEqual(beep[0]["kind"], "awaiting_user")

    def test_question_asked_during_handover_turn_stops_before_clear(self):
        orig = self.sim.on_submit

        def asks(text):
            orig(text)
            if text.startswith("/secretary-handover"):
                self.awaiting("t1", ago=0)

        self.sim.on_submit = asks
        self.tick()
        self.assertEqual(self.op()["reason"], "not_quiet_before_clear: awaiting_user")
        self.assertNotIn("/clear", self.sim.enters)

    def test_person_writing_during_handover_turn_stops_before_clear(self):
        orig = self.sim.on_submit

        def human(text):
            orig(text)
            if text.startswith("/secretary-handover"):
                with open(self.sim.transcript, "a", encoding="utf-8") as fh:
                    fh.write(_line(type="attachment", attachment={
                        "type": "queued_command", "prompt": "PR 出していいよ"}) + "\n")

        self.sim.on_submit = human
        self.assertEqual(self.tick(), "ran:failed")
        self.assertEqual(self.op()["reason"], "person_input_during_handover")
        self.assertNotIn("/clear", self.sim.enters)

    def test_failure_after_clear_names_the_op_in_the_hint(self):
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: None if t.startswith("/secretary-resume") else orig(t)
        self.tick()
        op = self.op()["op"]
        self.assertIn(f"/secretary-resume auto op={op}", self.notes[-1])

    def test_slow_resume_is_waited_for_not_typed_again(self):
        orig = self.sim.on_submit
        state = {"resume_at": None}

        def slow(text):
            if text.startswith("/secretary-resume"):
                cur = self.sim.rec()
                # the turn started (the hook saw this op's resume line), still running
                self.sim.rec(turn_open=cur["turn_closed"] + 1, resume_op=text.split("op=")[1],
                             resume_turn=cur["turn_closed"] + 1)
                state["resume_at"] = self.sim.now
                state["text"] = text
            else:
                orig(text)

        self.sim.on_submit = slow
        real_sleep = self.w.sleep

        def sleep(sec):
            real_sleep(sec)
            if state["resume_at"] and self.sim.now - state["resume_at"] > 200:
                state["resume_at"] = None
                orig(state["text"])

        self.w.sleep = sleep
        self.assertTrue(self.tick().startswith("ran:done"))
        self.assertEqual(len([e for e in self.sim.enters if e.startswith("/secretary-resume")]), 1)

    def test_unread_late_input_is_reported(self):
        orig = self.sim.on_submit

        def keeps_late(text):
            if text.startswith("/secretary-resume"):
                op = text.split("op=")[1]
                self.org.append_event("secretary_resumed", {"op": op})
                self.sim.rec(stop_session_id="s2", stop_at=self.sim.iso(5))
            else:
                orig(text)

        self.sim.on_submit = keeps_late
        self.assertTrue(self.tick().startswith("ran:done"))
        self.assertIn("late_input_unread", self.events("auto_handover_loss_suspected")[0]["reason"])

    def test_pane_not_named_secretary_is_never_typed_into(self):
        self.pane_name = None
        self.assertEqual(self.tick(), "pane_name_unknown")
        self.pane_name = "my-own-claude"
        self.assertEqual(self.tick(), "pane_not_secretary=my-own-claude")
        self.assertEqual(self.sim.enters, [])
        self.assertFalse(self.org.op_path.exists())

    def test_pane_lookup_failure_fails_the_op_with_backoff(self):
        from tools.self_edit_approval import BackendFailure

        def boom(pane):
            raise BackendFailure("renga inspect timed out")

        self.w.pane_namer = lambda pane: watch.renga_pane_name(pane)
        import tools.self_edit_approval as sea
        orig = sea._run
        sea._run = lambda *a, **k: boom(None)
        try:
            self.assertEqual(self.tick(), "pane_name_unknown")
            self.w.pane_namer = lambda pane: "secretary"
            orig_type = self.w.type_line
            calls = {"n": 0}

            def fail_second(op, text):
                calls["n"] += 1
                if calls["n"] == 2:
                    self.w.pane_namer = lambda pane: watch.renga_pane_name(pane)
                return orig_type(op, text)

            self.w.type_line = fail_second
            self.assertEqual(self.tick(), "ran:failed")
            self.assertEqual(self.op()["reason"], "pane_name_unknown")
            self.assertIn("secretary", json.loads(self.org.backoff_path.read_text()))
        finally:
            sea._run = orig

    def test_secretary_on_a_tmux_pane_is_never_typed_into(self):
        self.sim.rec(pane={"backend": "tmux", "id": "%5"})
        self.assertEqual(self.tick(), "pane_not_renga")

    def test_peer_hand_back_is_not_a_person(self):
        line = _line(type="user", origin={"kind": "peer"}, message={
            "content": "Another Claude session sent a message:\n<agent-message>x</agent-message>"})
        self.assertEqual(watch.person_inputs([line], "OP"), [])

    def test_person_paste_or_image_during_handover_stops_before_clear(self):
        for entry in (
                _line(type="user", origin={"kind": "human"}, message={"content": [
                    {"type": "image", "source": {}}]}),
                _line(type="queue-operation", operation="enqueue"),
                _line(type="user", origin={"kind": "human"},
                      message={"content": "\n\n<pasted_content id=x>log</pasted_content>"})):
            with self.subTest(entry=entry):
                self.assertEqual(watch.person_inputs([entry], "OP"), watch.person_inputs([entry], "OP")[:1])
                self.assertEqual(len(watch.person_inputs([entry], "OP")), 1)
        machine = [_line(type="queue-operation", operation="enqueue",
                         content='<channel source="renga-peers">x</channel>'),
                   _line(type="queue-operation", operation="enqueue",
                         content="<task-notification>done</task-notification>")]
        self.assertEqual(watch.person_inputs(machine, "OP"), [])

    def test_channel_turn_after_clear_is_not_taken_for_the_resume(self):
        orig = self.sim.on_submit

        def channel_turn(text):
            orig(text)
            if text == "/clear":  # a worker report opens a turn before the resume is typed
                cur = self.sim.rec()
                self.sim.rec(turn_open=cur["turn_closed"] + 1)

        self.sim.on_submit = channel_turn
        self.assertTrue(self.tick().startswith("ran:done"))
        self.assertEqual(len([e for e in self.sim.enters if e.startswith("/secretary-resume")]), 1)

    def test_task_id_mentioned_only_inside_another_word_does_not_count(self):
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: (orig(t), t.startswith("/secretary-handover") and
                                        self.org.handover_path("secretary").write_text(
                                            "hot1 worker-t1x"))
        self.tick()
        self.assertEqual(self.op()["reason"], "handover_missing_task_ids: t1")

    def test_worker_prefixed_and_japanese_mentions_count(self):
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: (orig(t), t.startswith("/secretary-handover") and
                                        self.org.handover_path("secretary").write_text(
                                            "worker-t1を確認中"))
        self.assertTrue(self.tick().startswith("ran:done"))

    def test_task_id_that_is_only_a_prefix_does_not_count(self):
        conn = connect(self.org.db_path)
        conn.execute("INSERT INTO runs (task_id, project_id, pattern, title, status) "
                     "VALUES ('t1-2', 1, 'B', 't1-2', 'in_use')")
        conn.execute("UPDATE runs SET task_id = 'fix-ci' WHERE task_id = 't1'")
        conn.commit()
        conn.close()
        orig = self.sim.on_submit
        self.sim.on_submit = lambda t: (orig(t), t.startswith("/secretary-handover") and
                                        self.org.handover_path("secretary").write_text("fix-ci-2 t1-2"))
        self.tick()
        self.assertEqual(self.op()["reason"], "handover_missing_task_ids: fix-ci")

    def test_one_role_at_a_time(self):
        other = watch.Org(self.org.state_dir)
        disp = dict(json.loads(self.org.record_path("secretary").read_text()))
        other.write_json(self.org.record_path("dispatcher"), {
            **disp, "session_crons": [], "pane": {"backend": "renga", "id": "4"}})
        self.w.pane_namer = lambda pane: "dispatcher" if pane["id"] == "4" else "secretary"
        self.assertTrue(self.w.tick().startswith("ran:"))
        handovers = [e for e in self.sim.enters if e.endswith(f"op={self.op()['op']}")
                     and "-handover" in e]
        self.assertEqual(len(handovers), 1)
        self.assertEqual(self.op()["role"], "dispatcher")  # dispatcher first; secretary next tick


class TestStatus(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.org = watch.Org(Path(self._td.name))
        self._orig = watch._identity

    def tearDown(self):
        watch._identity = self._orig

    def test_status_without_sidecar(self):
        self.assertEqual(watch.run_status(self.org), 1)

    def test_status_stale_sidecar(self):
        self.org.write_json(self.org.pid_path, {"pid": 99999999, "cwd": str(Path.cwd())})
        watch._identity = lambda pid: False
        self.assertEqual(watch.run_status(self.org), 1)

    def test_status_running_needs_same_cwd(self):
        watch._identity = lambda pid: True
        self.org.write_json(self.org.pid_path, {"pid": 42, "cwd": str(Path.cwd())})
        self.assertEqual(watch.run_status(self.org), 0)
        self.org.write_json(self.org.pid_path, {"pid": 42, "cwd": "/elsewhere"})
        self.assertEqual(watch.run_status(self.org), 1)

    def test_daemon_refuses_when_running(self):
        watch._identity = lambda pid: True
        self.org.write_json(self.org.pid_path, {"pid": 42, "cwd": str(Path.cwd())})
        self.assertEqual(watch.run_daemon(self.org), watch.EXIT_NOT_STARTED)


if __name__ == "__main__":
    unittest.main()
