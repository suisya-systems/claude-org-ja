"""Tests for tools/work_discovery_dedup.py (unchanged candidate set suppression)."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import work_discovery_dedup as dd  # noqa: E402

SCAN = {
    "status": "candidates_found",
    "repo_resolution": {"repos": ["o/home"]},
    "candidates": [
        {"repo": "o/b", "issue": 2},
        {"repo": None, "issue": 7},
        {"repo": "o/a", "issue": 9},
    ],
}
REFS = "o/a#9,o/b#2,o/home#7"


class DedupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "state.db"
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}')"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, **payload):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO events (kind, payload_json) VALUES ('work_discovery_scanned', ?)",
            (json.dumps(payload),),
        )
        conn.commit()
        conn.close()

    def run_main(self, scan, db=None):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(scan) if isinstance(scan, dict) else scan)), \
                mock.patch("sys.stdout", out):
            code = dd.main(["--db", str(db or self.db)])
        return code, json.loads(out.getvalue())

    def test_refs_sorted_with_repo_fallback(self):
        self.assertEqual(dd.candidate_refs(SCAN), REFS)

    def test_no_prior_record_forwards(self):
        self.assertEqual(self.run_main(SCAN), (0, {"candidate_refs": REFS, "unchanged": False}))

    def test_same_set_as_last_worker_close_is_unchanged(self):
        self.record(trigger="worker_close", candidate_refs=REFS)
        self.assertEqual(self.run_main(SCAN)[0], 3)

    def test_changed_set_forwards(self):
        self.record(trigger="worker_close", candidate_refs="o/a#9")
        self.assertEqual(self.run_main(SCAN)[0], 0)

    def test_only_the_latest_record_counts(self):
        self.record(trigger="worker_close", candidate_refs=REFS)
        self.record(trigger="worker_close", candidate_count="0", candidate_refs="")
        self.assertEqual(self.run_main(SCAN)[0], 0)

    def test_error_or_legacy_record_without_refs_forwards(self):
        self.record(trigger="worker_close", candidate_refs=REFS)
        self.record(trigger="worker_close", outcome="error")
        self.assertEqual(self.run_main(SCAN)[0], 0)

    def test_other_trigger_is_ignored(self):
        self.record(trigger="worker_close", candidate_refs=REFS)
        self.record(trigger="post_merge", candidate_refs="o/x#1")
        self.assertEqual(self.run_main(SCAN)[0], 3)

    def test_unreadable_db_forwards(self):
        self.assertEqual(self.run_main(SCAN, db=Path(self.tmp.name) / "missing.db")[0], 0)

    def test_default_db_follows_state_db_path_env(self):
        self.record(trigger="worker_close", candidate_refs=REFS)
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"STATE_DB_PATH": str(self.db)}), \
                mock.patch("sys.stdin", io.StringIO(json.dumps(SCAN))), \
                mock.patch("sys.stdout", out):
            self.assertEqual(dd.main([]), 3)

    def test_bad_json_is_error(self):
        self.assertEqual(self.run_main("not json")[0], 2)


if __name__ == "__main__":
    unittest.main()
