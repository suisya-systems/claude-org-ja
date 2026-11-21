"""Acceptance tests: design-ahead runs wait on their dependency (Issue 1295).

The conveyor may take a blocked Issue up to the design stage. The secretary
records that with ``belt_slots.design_ahead``. When such a run reports its
design (``review``), it waits on the dependency: reason ``dependency``,
parkable while its pane is open, not a busy slot. ``design_ready`` reads a
scan JSON and lists the design-ahead runs whose Issue is no longer blocked,
which the conveyor revives with ``--reason dependency_landed``.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.state_db import apply_schema, connect  # noqa: E402

JA = "suisya-systems/claude-org-ja"


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.bs = importlib.import_module("tools.belt_slots")
        for name in ("design_ahead", "design_ready"):
            if not hasattr(self.bs, name):
                self.fail(f"belt_slots.{name} is missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        self.decisions = d / "pending_decisions.json"
        self.conn = connect(d / "state.db")
        self.addCleanup(self.conn.close)
        apply_schema(self.conn)
        self.conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (1, 'ja', 'ja')")
        self.conn.commit()

    def run_(self, task, status):
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status) VALUES (?, 1, 'B', ?, ?)",
            (task, task, status))
        self.conn.commit()

    def spawned(self, task):
        for kind, extra in (("worker_spawned", {"dir": "/x"}), ("worker_spawn_verified", {"pane_id": "%3"})):
            self.conn.execute(
                "INSERT INTO events (actor, kind, payload_json) VALUES ('x', ?, ?)",
                (kind, json.dumps({"task": task, "worker": f"worker-{task}", **extra})))
        self.conn.commit()

    def count(self):
        return self.bs.count(self.conn, max_parallel=3, decisions_path=self.decisions)

    def test_design_ahead_records_an_event(self):
        self.run_("d", "in_use")
        self.bs.design_ahead(self.conn, "d", issue_ref=f"{JA}#61", blocking_refs=[f"{JA}#60"])
        row = self.conn.execute("SELECT payload_json FROM events WHERE kind = 'design_ahead'").fetchone()
        self.assertIsNotNone(row)
        p = json.loads(row[0])
        self.assertEqual((p["task"], p["issue_ref"], p["blocking_refs"]), ("d", f"{JA}#61", [f"{JA}#60"]))

    def test_design_run_in_progress_is_busy(self):
        self.run_("d", "in_use")
        self.bs.design_ahead(self.conn, "d", issue_ref=f"{JA}#61", blocking_refs=[f"{JA}#60"])
        self.assertIn("d", self.count()["busy"])

    def test_reported_design_waits_on_the_dependency(self):
        self.run_("d", "review")
        self.spawned("d")
        self.bs.design_ahead(self.conn, "d", issue_ref=f"{JA}#61", blocking_refs=[f"{JA}#60"])
        r = self.count()
        self.assertNotIn("d", r["busy"])
        w = {x["task"]: x for x in r["waiting"]}
        self.assertEqual(w["d"]["reason"], "dependency")
        self.assertEqual(r["parkable"], ["d"])

    def test_design_ready_lists_runs_whose_issue_is_unblocked(self):
        for t, n in (("d1", 61), ("d2", 63)):
            self.run_(t, "review")
            self.bs.design_ahead(self.conn, t, issue_ref=f"{JA}#{n}", blocking_refs=[f"{JA}#60"])
        scan = {
            "status": "candidates_found",
            "candidates": [{"repo": JA, "issue": 61}],
            "excluded_blocked": [{"repo": JA, "issue": 63, "blocking_refs": [f"{JA}#60"]}],
        }
        self.assertEqual(self.bs.design_ready(self.conn, scan), ["d1"])

    def test_design_ready_reads_single_repo_scan_form(self):
        self.run_("d1", "review")
        self.bs.design_ahead(self.conn, "d1", issue_ref=f"{JA}#61", blocking_refs=[f"{JA}#60"])
        scan = {"candidates": [], "excluded_blocked": [], "repo_resolution": {"repos": [JA]}}
        # repo null = the single scanned repo; #61 is neither blocked nor listed -> ready
        self.assertEqual(self.bs.design_ready(self.conn, scan), ["d1"])

    def test_design_ready_ignores_runs_not_waiting(self):
        self.run_("d1", "completed")
        self.bs.design_ahead(self.conn, "d1", issue_ref=f"{JA}#61", blocking_refs=[f"{JA}#60"])
        self.assertEqual(self.bs.design_ready(self.conn, {"candidates": [], "excluded_blocked": []}), [])

    def test_revive_accepts_dependency_landed(self):
        gdp = importlib.import_module("tools.gen_delegate_payload")
        self.assertIn("dependency_landed", gdp.REVIVE_REASONS)


if __name__ == "__main__":
    unittest.main()
