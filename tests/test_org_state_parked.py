"""Acceptance tests: the org-state summaries list parked runs (Issue 1295).

A parked run stays ``review`` with no pane. Resume / handover skills compare
``active_runs`` against live panes, so the summaries expose ``parked_runs``
(task ids) to keep a parked run from being reported as a vanished worker.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db import queries  # noqa: E402


class ParkedRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.addCleanup(self.conn.close)
        apply_schema(self.conn)
        self.conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (1, 'ja', 'ja')")

    def run_(self, task, status="review"):
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status) VALUES (?, 1, 'B', ?, ?)",
            (task, task, status),
        )

    def ev(self, kind, task, at):
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES (?, 'x', ?, ?)",
            (at, kind, json.dumps({"task": task, "worker": f"worker-{task}"})),
        )

    def parked(self, summary):
        self.assertIn("parked_runs", summary, "summary has no parked_runs")
        return summary["parked_runs"]

    def test_parked_review_run_is_listed(self):
        self.run_("a")
        self.ev("worker_spawned", "a", "2026-11-22T10:00:00Z")
        self.ev("worker_parked", "a", "2026-11-22T10:10:00Z")
        self.run_("b")  # review, not parked
        self.ev("worker_spawned", "b", "2026-11-22T10:00:00Z")
        for fn in (queries.get_org_state_summary, queries.get_resume_briefing_light):
            self.assertEqual(self.parked(fn(self.conn)), ["a"], fn.__name__)

    def test_revived_run_is_no_longer_parked(self):
        self.run_("a", status="in_use")
        self.ev("worker_spawned", "a", "2026-11-22T10:00:00Z")
        self.ev("worker_parked", "a", "2026-11-22T10:10:00Z")
        self.ev("worker_spawned", "a", "2026-11-22T11:00:00Z")
        self.assertEqual(self.parked(queries.get_org_state_summary(self.conn)), [])

    def test_completed_run_is_not_parked(self):
        self.run_("a", status="completed")
        self.ev("worker_spawned", "a", "2026-11-22T10:00:00Z")
        self.ev("worker_parked", "a", "2026-11-22T10:10:00Z")
        self.assertEqual(self.parked(queries.get_org_state_summary(self.conn)), [])


if __name__ == "__main__":
    unittest.main()
