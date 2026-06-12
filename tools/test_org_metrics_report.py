"""Unit tests for tools/org_metrics_report.py (Issue #551).

The fixture DB is built with the real ``tools/state_db`` schema/writer so the
report runs against production-shaped rows, including payload gaps and
unknown keys (the reader must tolerate both). Period semantics, the
status-classification rule, the 3-stage CI<->run join, the event vocabulary,
and the pending-decisions not-available distinction are each asserted.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import org_metrics_report as omr  # noqa: E402
from tools.state_db import apply_schema  # noqa: E402

REPO = "octo/repo"


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _seed(db_path: Path) -> None:
    """Build a fixture DB covering the cases the report must handle."""
    conn = _conn(db_path)
    apply_schema(conn)
    # one project
    conn.execute(
        "INSERT INTO projects (id, slug, display_name) VALUES "
        "(1, 'claude-org', 'claude-org')"
    )

    # runs: explicit dispatched_at so period filtering is deterministic.
    # (task_id, status, pattern, pr_url, pr_state, dispatched_at)
    runs = [
        # in-period (2026-06-10..) runs
        ("r-completed", "completed", "A",
         f"https://github.com/{REPO}/pull/101", "merged", "2026-06-10T01:00:00.000Z"),
        ("r-inuse", "in_use", "B", None, None, "2026-06-11T02:00:00.000Z"),
        ("r-review", "review", "A",
         f"https://github.com/{REPO}/pull/102", "open", "2026-06-12T03:00:00.000Z"),
        ("r-failed", "failed", "C", None, None, "2026-06-12T04:00:00.000Z"),
        ("r-suspended", "suspended", "A", None, None, "2026-06-11T05:00:00.000Z"),
        # out-of-period (too old) run -- must be excluded
        ("r-old", "completed", "A", None, None, "2026-05-01T00:00:00.000Z"),
    ]
    for task_id, status, pattern, pr_url, pr_state, dispatched in runs:
        conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status, "
            "pr_url, pr_state, dispatched_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (task_id, pattern, f"title {task_id}", status, pr_url, pr_state,
             dispatched),
        )

    run_id_of = {
        r["task_id"]: r["id"]
        for r in conn.execute("SELECT id, task_id FROM runs").fetchall()
    }

    # events: occurred_at drives event-period membership.
    def ev(kind, occurred, payload=None, run_id=None):
        conn.execute(
            "INSERT INTO events (kind, occurred_at, run_id, payload_json) "
            "VALUES (?, ?, ?, ?)",
            (kind, occurred, run_id, json.dumps(payload or {})),
        )

    # vocabulary events (in period)
    ev("delegate_resume", "2026-06-10T06:00:00.000Z")
    ev("delegate_resume_r2", "2026-06-10T06:30:00.000Z")
    ev("worker_closed", "2026-06-11T07:00:00.000Z")
    ev("pane_closed", "2026-06-11T07:30:00.000Z")
    ev("worker_completed", "2026-06-12T08:00:00.000Z")
    # an event with an unknown/extra payload key -- tolerant reader
    ev("worker_completed", "2026-06-12T08:30:00.000Z",
       payload={"unexpected_key": "x", "nested": {"a": 1}})

    # CI events exercising the 3-stage join:
    #  (a) matched by run_id
    ev("ci_completed", "2026-06-10T09:00:00.000Z",
       payload={"pr": 101, "repo": REPO, "status": "success", "duration_sec": 120},
       run_id=run_id_of["r-completed"])
    #  (b) matched by normalized PR (no run_id; repo case differs)
    ev("ci_completed", "2026-06-12T09:30:00.000Z",
       payload={"pr": 102, "repo": "Octo/Repo", "status": "failure",
                "duration_sec": 60})
    #  (c) unmatched -- PR that no run owns
    ev("ci_completed", "2026-06-12T10:00:00.000Z",
       payload={"pr": 999, "repo": REPO, "status": "success"})
    #  (d) unmatched -- payload missing repo/pr entirely
    ev("ci_completed", "2026-06-12T10:30:00.000Z",
       payload={"status": "incomplete"})
    # an out-of-period CI event -- excluded
    ev("ci_completed", "2026-05-01T00:00:00.000Z",
       payload={"pr": 101, "repo": REPO, "status": "success"})

    conn.commit()
    conn.close()


class MetricsReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state.db"
        _seed(self.db_path)
        self.now = datetime(2026, 6, 13, 0, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _report(self, lo="2026-06-10", hi="2026-06-12",
                pending_path=None) -> dict:
        conn = omr.open_readonly(self.db_path)
        try:
            return omr.build_report(
                conn,
                lo=omr._normalize_since(lo) if lo else None,
                hi=omr._normalize_until(hi) if hi else None,
                pending_path=pending_path or (self.db_path.parent /
                                              "pending_decisions.json"),
                generated_at="2026-06-13T00:00:00.000Z",
                db_path=self.db_path,
            )
        finally:
            conn.close()

    # --- read-only guarantee --------------------------------------------
    def test_connection_is_read_only(self) -> None:
        conn = omr.open_readonly(self.db_path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO projects (slug, display_name) "
                    "VALUES ('x', 'x')"
                )
        finally:
            conn.close()

    # --- period inclusion (dispatched_at basis) -------------------------
    def test_runs_filtered_by_dispatched_at(self) -> None:
        rep = self._report()
        # 5 in-period runs; the 2026-05-01 run is excluded.
        self.assertEqual(rep["runs"]["total"], 5)
        self.assertNotIn("r-old", rep["runs"]["by_status"])

    def test_open_ended_period_includes_old_run(self) -> None:
        rep = self._report(lo=None, hi=None)
        self.assertEqual(rep["runs"]["total"], 6)

    # --- status classification (suspended reserved) ---------------------
    def test_status_classification_excludes_suspended(self) -> None:
        runs = self._report()["runs"]
        # live = in_use + review = 2 ; terminal = completed + failed = 2
        self.assertEqual(runs["live"], 2)
        self.assertEqual(runs["terminal"], 2)
        self.assertEqual(runs["reserved"], 1)  # suspended counted on its own
        # suspended is NOT in live or terminal
        self.assertEqual(runs["live"] + runs["terminal"], 4)
        self.assertEqual(runs["by_status"].get("suspended"), 1)

    def test_completion_rate(self) -> None:
        runs = self._report()["runs"]
        self.assertEqual(runs["completed"], 1)
        self.assertAlmostEqual(runs["completion_rate"], 1 / 5)

    def test_pr_state_breakdown(self) -> None:
        runs = self._report()["runs"]
        self.assertEqual(runs["with_pr"], 2)
        self.assertEqual(runs["by_pr_state"], {"merged": 1, "open": 1})

    # --- event vocabulary -----------------------------------------------
    def test_event_vocabulary_counts(self) -> None:
        events = self._report()["events"]
        self.assertEqual(events["redispatch"], 2)  # delegate_resume + _r2
        self.assertEqual(events["pane_close"], 2)   # worker_closed + pane_closed
        self.assertEqual(events["worker_completed"], 2)

    # --- CI 3-stage join -------------------------------------------------
    def test_ci_join_three_stage(self) -> None:
        ci = self._report()["ci"]
        self.assertEqual(ci["total"], 4)  # excludes the 2026-05-01 CI event
        self.assertEqual(ci["matched_by_run_id"], 1)
        self.assertEqual(ci["matched_by_pr"], 1)
        self.assertEqual(ci["unmatched_count"], 2)
        # unmatched are surfaced, not dropped
        reasons = {u["reason"] for u in ci["unmatched"]}
        self.assertIn("no run_id and no PR match", reasons)
        self.assertIn("no run_id and unparseable repo/pr payload", reasons)

    def test_ci_avg_duration(self) -> None:
        ci = self._report()["ci"]
        # durations present: 120 and 60 -> avg 90
        self.assertEqual(ci["avg_duration_sec"], 90.0)

    # --- pending decisions: not-available vs zero -----------------------
    def test_pending_not_available_when_missing(self) -> None:
        rep = self._report(pending_path=self.db_path.parent / "nope.json")
        self.assertIsNone(rep["pending_decisions"])

    def test_pending_zero_when_present_empty(self) -> None:
        path = self.db_path.parent / "pending_decisions.json"
        path.write_text("[]", encoding="utf-8")
        rep = self._report(pending_path=path)
        pd = rep["pending_decisions"]
        self.assertIsNotNone(pd)
        self.assertEqual(pd["pending"], 0)
        self.assertEqual(pd["total"], 0)

    def test_pending_counts_by_status(self) -> None:
        path = self.db_path.parent / "pending_decisions.json"
        path.write_text(json.dumps([
            {"task_id": "a", "received_at": "2026-06-10T00:00:00Z",
             "raw_message": "m", "status": "pending"},
            {"task_id": "b", "received_at": "2026-06-10T00:00:00Z",
             "raw_message": "m", "status": "escalated"},
        ]), encoding="utf-8")
        rep = self._report(pending_path=path)
        pd = rep["pending_decisions"]
        self.assertEqual(pd["pending"], 1)
        self.assertEqual(pd["total"], 2)
        self.assertEqual(pd["by_status"], {"pending": 1, "escalated": 1})

    # --- bounds helpers --------------------------------------------------
    def test_until_date_only_covers_whole_day(self) -> None:
        # event at 2026-06-12T10:30 must be inside an until of 2026-06-12
        self.assertTrue(
            omr._in_period(
                "2026-06-12T10:30:00.000Z", "2026-06-10",
                omr._normalize_until("2026-06-12"))
        )

    def test_last_days_bounds(self) -> None:
        lo, hi = omr.compute_bounds(
            since=None, until=None, last_days=3, now=self.now)
        self.assertEqual(lo, "2026-06-10T00:00:00.000000Z")
        self.assertEqual(hi, "2026-06-13T00:00:00.000000Z")

    # --- format smoke ----------------------------------------------------
    def test_markdown_render_ascii_safe(self) -> None:
        md = omr.render_markdown(self._report())
        self.assertIn("# Org metrics report", md)
        self.assertIn("## Runs", md)
        self.assertIn("Unmatched CI events", md)
        # cp932 safety: no em-dash / en-dash in output
        self.assertNotIn("—", md)
        self.assertNotIn("–", md)
        md.encode("cp932")  # must not raise

    def test_json_render_roundtrips(self) -> None:
        js = omr.render_json(self._report())
        data = json.loads(js)
        self.assertEqual(data["runs"]["total"], 5)
        self.assertNotIn("_pr_index", data["runs"])  # internal key stripped
        js.encode("cp932")  # ASCII-only, cp932 safe


class CliArgvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state.db"
        _seed(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_json_end_to_end(self) -> None:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = omr.run([
                "--db-path", str(self.db_path),
                "--since", "2026-06-10",
                "--until", "2026-06-12",
                "--format", "json",
            ], now=datetime(2026, 6, 13, tzinfo=timezone.utc))
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["runs"]["total"], 5)

    def test_missing_db_returns_error(self) -> None:
        rc = omr.run(["--db-path", str(self.db_path.parent / "absent.db")])
        self.assertEqual(rc, 2)

    def test_last_days_conflicts_with_since(self) -> None:
        with self.assertRaises(SystemExit):
            omr.run(["--db-path", str(self.db_path),
                     "--since", "2026-06-10", "--last-days", "3"])


if __name__ == "__main__":
    unittest.main()
