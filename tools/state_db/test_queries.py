"""Unit tests for the M1 read-only query layer.

Run with:
    python -m unittest discover -s tools/state_db -p 'test_*.py'

Strategy: most tests use the M0 importer with the same synthetic fixture
that test_importer.py uses, so we exercise the real markdown → DB pipeline
that dashboard / org-* skills will read from in production. A couple of
tests bypass the importer to seed exact rows and pin down edge cases
(suspend lookup, lifecycle filter, null workstream).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.state_db import apply_schema, connect
from tools.state_db.importer import import_full_rebuild
from tools.state_db.queries import (
    get_org_state_summary,
    get_resume_briefing,
    get_run_by_task_id,
    list_active_runs,
    list_recent_events,
    list_worker_dirs,
)
from tools.state_db.test_importer import _seed_claude_org_root


# ---------------------------------------------------------------------------
# Empty DB behaviour
# ---------------------------------------------------------------------------


class TestEmptyDB(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "empty.db"
        self.conn = connect(self.db_path)
        apply_schema(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_list_active_runs_empty(self):
        self.assertEqual(list_active_runs(self.conn), [])

    def test_list_worker_dirs_empty(self):
        self.assertEqual(list_worker_dirs(self.conn), [])
        self.assertEqual(list_worker_dirs(self.conn, lifecycle="active"), [])

    def test_list_recent_events_empty(self):
        self.assertEqual(list_recent_events(self.conn), [])

    def test_get_run_by_task_id_missing(self):
        self.assertIsNone(get_run_by_task_id(self.conn, "no-such-task"))

    def test_get_org_state_summary_empty(self):
        s = get_org_state_summary(self.conn)
        self.assertEqual(s["active_runs"], [])
        self.assertEqual(s["active_worker_dirs"], [])
        self.assertEqual(s["recent_events"], [])
        self.assertEqual(s["run_status_counts"], {})
        self.assertEqual(s["totals"]["runs"], 0)
        self.assertEqual(s["totals"]["projects"], 0)
        self.assertEqual(s["totals"]["worker_dirs"], 0)

    def test_get_resume_briefing_empty(self):
        b = get_resume_briefing(self.conn)
        self.assertEqual(b["active_runs"], [])
        self.assertIsNone(b["last_event_at"])
        self.assertIsNone(b["last_suspend_at"])


# ---------------------------------------------------------------------------
# Imported-fixture behaviour
# ---------------------------------------------------------------------------


class TestSeededDB(unittest.TestCase):
    """End-to-end check: importer fixture → queries return expected shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name) / "claude-org"
        cls.db_path = Path(cls._td.name) / "state.db"
        _seed_claude_org_root(root)
        cls.summary = import_full_rebuild(cls.db_path, root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def setUp(self) -> None:
        self.conn = connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()

    def test_active_runs_match_in_use_and_review(self):
        # Fixture: sample-task-1 → completed (merged), sample-task-2 → review.
        active = list_active_runs(self.conn)
        task_ids = {r["task_id"] for r in active}
        self.assertIn("sample-task-2", task_ids)
        self.assertNotIn("sample-task-1", task_ids)
        # Joined project info populated.
        row = next(r for r in active if r["task_id"] == "sample-task-2")
        self.assertEqual(row["project_slug"], "renga")
        self.assertEqual(row["status"], "review")

    def test_get_run_by_task_id_returns_completed_too(self):
        # Lookup is not status-filtered.
        row = get_run_by_task_id(self.conn, "sample-task-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["project_slug"], "clock-app")

    def test_recent_events_ordered_desc_with_limit(self):
        all_events = list_recent_events(self.conn, limit=100)
        self.assertGreater(len(all_events), 0)
        ids = [e["id"] for e in all_events]
        self.assertEqual(ids, sorted(ids, reverse=True))

        capped = list_recent_events(self.conn, limit=2)
        self.assertEqual(len(capped), 2)
        # Same prefix as the un-capped result.
        self.assertEqual([e["id"] for e in capped], ids[:2])

    def test_summary_aggregates(self):
        s = get_org_state_summary(self.conn)
        self.assertGreater(s["totals"]["runs"], 0)
        self.assertGreater(s["totals"]["projects"], 0)
        self.assertGreater(len(s["recent_events"]), 0)
        # Status counts should sum to total runs.
        self.assertEqual(
            sum(s["run_status_counts"].values()), s["totals"]["runs"]
        )

    def test_resume_briefing_has_last_event(self):
        b = get_resume_briefing(self.conn)
        self.assertIsNotNone(b["last_event_at"])
        self.assertIsNotNone(b["last_event_kind"])


# ---------------------------------------------------------------------------
# Targeted edge cases (hand-seeded rows)
# ---------------------------------------------------------------------------


class TestEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "edge.db"
        self.conn = connect(self.db_path)
        apply_schema(self.conn)
        # One project, two worker_dirs (active + archived), one run with NULL
        # workstream_id, plus a 'suspend' event.
        self.conn.execute(
            "INSERT INTO projects (id, slug, display_name) VALUES (1, 'pj', 'pj')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (abs_path, layout, lifecycle) "
            "VALUES ('/w/active', 'flat', 'active')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (abs_path, layout, lifecycle) "
            "VALUES ('/w/old', 'flat', 'archived')"
        )
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status) "
            "VALUES ('t-null-ws', 1, 'B', 't-null-ws', 'in_use')"
        )
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES ('2026-04-01T00:00:00Z', 'secretary', 'suspend', "
            "'{\"reason\":\"end of day\"}')"
        )
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES ('2026-04-02T00:00:00Z', 'secretary', 'resume', '{}')"
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_lifecycle_filter(self):
        active = list_worker_dirs(self.conn, lifecycle="active")
        archived = list_worker_dirs(self.conn, lifecycle="archived")
        self.assertEqual([w["abs_path"] for w in active], ["/w/active"])
        self.assertEqual([w["abs_path"] for w in archived], ["/w/old"])
        self.assertEqual(len(list_worker_dirs(self.conn)), 2)

    def test_active_run_with_null_workstream(self):
        rows = list_active_runs(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_id"], "t-null-ws")
        self.assertIsNone(rows[0]["workstream_slug"])

    def test_resume_briefing_picks_latest_suspend(self):
        b = get_resume_briefing(self.conn)
        self.assertEqual(b["last_suspend_at"], "2026-04-01T00:00:00Z")
        self.assertEqual(b["last_suspend_actor"], "secretary")
        # Most recent event overall is the resume, not the suspend.
        self.assertEqual(b["last_event_kind"], "resume")
        # Payload survives as raw JSON text (caller decodes).
        self.assertEqual(json.loads(b["last_suspend_payload"])["reason"],
                         "end of day")

    def test_recent_events_negative_limit_is_safe(self):
        # Defensive: a caller passing limit=-1 should not blow up.
        self.assertEqual(list_recent_events(self.conn, limit=-1), [])


if __name__ == "__main__":
    unittest.main()
