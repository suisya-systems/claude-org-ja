"""Acceptance test for tools/cursor_reset.py (Refs #1233, #1226).

The tool owns steps 1-6 of .dispatcher/references/cursor-reset.md: cursor
binding check, anomaly record, de-dup, send gate, notify_sent record and the
unsent-retry. The dispatcher only sends the returned message and then calls
``--sent``. Everything runs against a temp DB, never .state/state.db.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "cursor_reset.py"


class CursorResetTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.db = self.tmp / "state.db"
        self.cursor = self.tmp / "cursor.txt"
        self.idle = self.tmp / "idle.json"
        self.cursor.write_text(json.dumps({
            "version": 1, "session_key": "old-session",
            "session_key_source": "server_info.session_id", "next_since": 64,
        }), encoding="utf-8")

    def run_tool(self, *args):
        r = subprocess.run([sys.executable, str(TOOL), "--db-path", str(self.db), *args],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def check(self, session_key="new-session"):
        return self.run_tool(
            "--cursor-file", str(self.cursor), "--session-key", session_key,
            "--session-key-source", "server_info.session_id",
            "--idle-state", str(self.idle))

    def sent(self, session_key="new-session", discarded="old-session"):
        return self.run_tool("--sent", "--session-key", session_key,
                             "--discarded-session-key", discarded)

    def events(self, kind):
        if not self.db.exists():
            return []
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute("SELECT payload_json FROM events WHERE kind=? ORDER BY id",
                                (kind,)).fetchall()
        finally:
            conn.close()
        return [json.loads(r[0]) for r in rows]

    def set_workers(self, placement):
        self.idle.write_text(json.dumps({"w1": {"placement": placement}}), encoding="utf-8")

    def test_no_background_worker_is_suppressed_and_recorded(self):
        self.set_workers("same_tab")
        out = self.check()
        self.assertTrue(out["discard"])
        self.assertEqual(out["notices"], [])
        self.assertEqual(len(self.events("anomaly_observed")), 1)
        sent = self.events("notify_sent")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["delivery"], "suppressed")
        self.assertEqual(sent[0]["reason"], "no_background_worker")
        # next cycle: nothing pending, nothing re-recorded
        self.assertEqual(self.check()["notices"], [])
        self.assertEqual(len(self.events("notify_sent")), 1)

    def test_background_worker_sends_then_sent_records_once(self):
        self.set_workers("background_tab")
        out = self.check()
        self.assertEqual(len(out["notices"]), 1)
        notice = out["notices"][0]
        self.assertTrue(notice["message"].startswith("EVENT_CURSOR_RESET:"))
        self.assertEqual(self.events("notify_sent"), [])  # not before the send
        self.sent()
        sent = self.events("notify_sent")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["delivery"], "sent")
        self.assertEqual(sent[0]["session_key"], "new-session")
        self.assertEqual(sent[0]["discarded_session_key"], "old-session")

    def test_missing_sent_means_resend_next_call(self):
        self.set_workers("background_tab")
        self.check()  # dispatcher "forgot" --sent (send failed)
        # the dispatcher has re-seated the cursor, so the binding now matches
        self.cursor.write_text(json.dumps({
            "version": 1, "session_key": "new-session",
            "session_key_source": "server_info.session_id", "next_since": 3,
        }), encoding="utf-8")
        out = self.check()
        self.assertFalse(out["discard"])
        self.assertEqual(len(out["notices"]), 1)
        self.assertEqual(len(self.events("anomaly_observed")), 1)  # not re-recorded
        self.sent()
        self.assertEqual(self.check()["notices"], [])


if __name__ == "__main__":
    unittest.main()
