"""Unit tests for tools.dispatcher_loop_watch (Issue #1168)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import dispatcher_loop_watch as watch  # noqa: E402
from tools import relay_scan  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ago(minutes: float) -> str:
    conn = connect(":memory:")
    try:
        return conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now', ?)",
            (f"-{minutes} minutes",)).fetchone()[0]
    finally:
        conn.close()


class TestCheck(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.execute("INSERT INTO projects (slug, display_name) VALUES ('p', 'P')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self._td.cleanup()

    def _run(self, task, status, dispatched_min_ago):
        conn = connect(self.db)
        conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status, "
            "dispatched_at) VALUES (?, 1, 'B', ?, ?, ?)",
            (task, task, status, _ago(dispatched_min_ago)))
        conn.commit()
        conn.close()

    def _scan(self, minutes_ago):
        relay_scan.write_heartbeat(self.db, recipient="secretary", surfaced=0,
                                   scanned_at=_ago(minutes_ago))

    def test_no_live_workers_is_silent_even_with_stale_heartbeat(self):
        self._run("done", "completed", 600)
        self._scan(600)
        self.assertIsNone(watch.check(self.db))

    def test_live_worker_with_fresh_scan_is_silent(self):
        self._run("t1", "in_use", 120)
        self._scan(2)
        self.assertIsNone(watch.check(self.db))

    def test_live_worker_with_stale_scan_reports(self):
        self._run("t1", "in_use", 120)
        self._run("t2", "review", 60)
        self._scan(30)
        line = watch.check(self.db)
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("DISPATCHER_LOOP_UNARMED:"))
        self.assertIn("t1, t2", line)

    def test_never_scanned_gets_a_grace_window_from_first_dispatch(self):
        self._run("t1", "in_use", 3)
        self.assertIsNone(watch.check(self.db))

    def test_never_scanned_past_grace_reports(self):
        self._run("t1", "in_use", 20)
        line = watch.check(self.db)
        self.assertIn("last_scan_at=never", line)

    def test_missing_db_is_silent_and_not_created(self):
        missing = Path(self._td.name) / "nope" / "state.db"
        self.assertIsNone(watch.check(missing))
        self.assertFalse(missing.exists())

    def test_same_unarmed_state_is_reported_once(self):
        self._run("t1", "in_use", 120)
        self._scan(30)
        self.assertIsNotNone(watch.report_once(self.db))
        self.assertIsNone(watch.report_once(self.db))
        # the heartbeat moved but is still stale: a new state, report again
        self._scan(20)
        self.assertIsNotNone(watch.report_once(self.db))

    def test_report_rearms_after_the_state_clears(self):
        self._run("t1", "in_use", 20)
        self.assertIsNotNone(watch.report_once(self.db))
        conn = connect(self.db)
        conn.execute("UPDATE runs SET status = 'completed'")
        conn.commit()
        conn.close()
        self.assertIsNone(watch.report_once(self.db))
        self._run("t2", "in_use", 20)  # never scanned again: same key "never"
        self.assertIsNotNone(watch.report_once(self.db))

    def test_hook_never_blocks(self):
        hook = REPO_ROOT / ".hooks" / "check-dispatcher-loop.sh"
        self._run("t1", "in_use", 20)
        res = subprocess.run(
            ["bash", str(hook)], input="{}", capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "STATE_DB_PATH": str(self.db)})
        self.assertEqual(res.returncode, 0)
        self.assertIn("DISPATCHER_LOOP_UNARMED", res.stdout)


if __name__ == "__main__":
    unittest.main()
