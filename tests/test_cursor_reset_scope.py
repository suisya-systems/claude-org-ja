"""Stale / keyless unsent rows must not be re-sent; repeated calls dedupe (Refs #1233)."""
import json, subprocess, sys, tempfile, unittest, sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "cursor_reset.py"
KEYS = ["source=event_cursor", "kind=stale_event_cursor", "confidence=n/a"]


class ScopeTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        self.tmp = Path(td.name); self.db = self.tmp / "s.db"
        self.cursor = self.tmp / "c.txt"; self.idle = self.tmp / "i.json"
        self.idle.write_text(json.dumps({"w": {"placement": "background_tab"}}))

    def anomaly(self, sk, dk):
        subprocess.run([sys.executable, str(REPO / "tools" / "journal_append.py"), "anomaly_observed",
                        *KEYS, f"session_key={sk}",
                        f"discarded_session_key={dk}", "note=session_mismatch", "--db-path", str(self.db)], check=True)

    def call(self):
        r = subprocess.run([sys.executable, str(TOOL), "--db-path", str(self.db), "--cursor-file", str(self.cursor),
                            "--session-key", "cur", "--session-key-source", "server_info.session_id",
                            "--idle-state", str(self.idle)], capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def test_old_session_and_none_rows_not_resent(self):
        self.anomaly("none", "none"); self.anomaly("oldsess", "older")
        self.cursor.write_text(json.dumps({"version": 1, "session_key": "cur",
                                           "session_key_source": "server_info.session_id", "next_since": 1}))
        self.assertEqual(self.call()["notices"], [])

    def test_repeat_call_records_one_anomaly(self):
        self.cursor.write_text(json.dumps({"version": 1, "session_key": "old",
                                           "session_key_source": "server_info.session_id", "next_since": 1}))
        for _ in range(3):
            self.assertEqual(len(self.call()["notices"]), 1)
        n = sqlite3.connect(self.db).execute("select count(*) from events where kind='anomaly_observed'").fetchone()[0]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
