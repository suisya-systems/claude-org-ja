"""Unit tests for tools.relay_scan (outbox relay, Refs #653 #658)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import relay_scan
from tools.state_db import apply_schema, connect
from tools.state_db.writer import StateWriter


def _db_with_events(events):
    """events: list of (kind, payload_dict). Returns (tempdir, db_path)."""
    td = tempfile.TemporaryDirectory()
    db = Path(td.name) / "state.db"
    conn = connect(db)
    apply_schema(conn)
    for kind, payload in events:
        conn.execute(
            "INSERT INTO events (kind, payload_json) VALUES (?, ?)",
            (kind, json.dumps(payload)),
        )
    conn.commit()
    conn.close()
    return td, db


class TestComposeMessage(unittest.TestCase):
    def test_known_kinds_render_expected_forms(self):
        self.assertEqual(
            relay_scan.compose_message(
                "ci_completed", {"pr": 73, "status": "passed", "head": "abc1234"}),
            "CI_COMPLETED: PR #73 (status=passed, head=abc1234) [relay]")
        self.assertEqual(
            relay_scan.compose_message("pr_merged", {"pr": 5, "head": "d"}),
            "PR_MERGED: PR #5 (head=d) [relay]")
        self.assertIn(
            "last CI-confirmed head=base",
            relay_scan.compose_message(
                "pr_merged_head_unconfirmed",
                {"pr": 5, "head": "h", "baseline_head": "base"}))
        self.assertIn(
            "PR_WATCH_ABORTED",
            relay_scan.compose_message("pr_watch_aborted",
                                       {"pr": 5, "error": "boom"}))
        self.assertIn(
            "NOTIFY_FAILED",
            relay_scan.compose_message(
                "notify_failed",
                {"pr": 5, "failed_kind": "ci_completed", "transport": "broker"}))

    def test_unknown_kind_degrades_not_raises(self):
        msg = relay_scan.compose_message("brand_new_kind", {"pr": 9})
        self.assertIn("BRAND_NEW_KIND", msg)
        self.assertIn("PR #9", msg)

    def test_missing_pr_is_tolerated(self):
        msg = relay_scan.compose_message("ci_completed", {})
        self.assertIn("PR #?", msg)


class TestRelayScanCli(unittest.TestCase):
    def _run(self, db, *args):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = relay_scan._main(["--db", str(db), *args])
        return rc, buf.getvalue()

    def test_list_surfaces_terminal_excludes_nonterminal(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
            ("worker_reported", {"pr": 1}),
            ("pr_merged", {"pr": 1, "head": "a"}),
        ])
        try:
            rc, out = self._run(db, "--recipient", "secretary", "--list")
            self.assertEqual(rc, 0)
            items = json.loads(out)
            kinds = {i["kind"] for i in items}
            self.assertEqual(kinds, {"ci_completed", "pr_merged"})
            self.assertTrue(all(i["attempt"] == 1 for i in items))
        finally:
            td.cleanup()

    def test_delivered_excluded_at_least_once_idempotent(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            rc, out = self._run(db, "--list")
            eid = json.loads(out)[0]["source_event_id"]
            # A second --list before delivery re-surfaces it (still pending)
            # and increments the attempt counter (at-least-once tracking).
            rc, out = self._run(db, "--list")
            self.assertEqual(json.loads(out)[0]["attempt"], 2)
            # Mark delivered -> excluded thereafter.
            rc, out = self._run(db, "--mark-delivered",
                                "--source-event-id", str(eid))
            self.assertEqual(rc, 0)
            rc, out = self._run(db, "--list")
            self.assertEqual(json.loads(out), [])
            # Re-marking delivered is idempotent (no error, still empty).
            rc, _ = self._run(db, "--mark-delivered",
                              "--source-event-id", str(eid))
            self.assertEqual(rc, 0)
        finally:
            td.cleanup()

    def test_mark_failed_is_retryable(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            rc, out = self._run(db, "--list")
            eid = json.loads(out)[0]["source_event_id"]
            rc, _ = self._run(db, "--mark-failed", "--source-event-id",
                              str(eid), "--error", "broker down")
            self.assertEqual(rc, 0)
            # failed does NOT exclude — still surfaced for retry.
            rc, out = self._run(db, "--list")
            self.assertEqual(json.loads(out)[0]["source_event_id"], eid)
        finally:
            td.cleanup()

    def test_since_bound_excludes_ancient(self):
        td = tempfile.TemporaryDirectory()
        db = Path(td.name) / "state.db"
        conn = connect(db)
        apply_schema(conn)
        conn.execute(
            "INSERT INTO events (kind, occurred_at, payload_json) "
            "VALUES ('ci_completed','2000-01-01T00:00:00.000Z','{\"pr\":1}')")
        conn.execute(
            "INSERT INTO events (kind, occurred_at, payload_json) "
            "VALUES ('ci_completed','2099-01-01T00:00:00.000Z','{\"pr\":2}')")
        conn.commit(); conn.close()
        try:
            rc, out = self._run(db, "--list", "--since-hours", "72")
            prs = {i["payload"]["pr"] for i in json.loads(out)}
            self.assertEqual(prs, {2})  # ancient row excluded by since bound
            # Disabling the bound (<=0) surfaces both.
            rc, out = self._run(db, "--mark-delivered",
                                "--source-event-id", "2")
            rc, out = self._run(db, "--list", "--since-hours", "0")
            prs = {i["payload"]["pr"] for i in json.loads(out)}
            self.assertEqual(prs, {1})
        finally:
            td.cleanup()

    def test_missing_db_is_empty_not_error(self):
        td = tempfile.TemporaryDirectory()
        db = Path(td.name) / "nope" / "state.db"
        try:
            rc, out = self._run(db, "--list")
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "[]")
        finally:
            td.cleanup()

    def test_mark_requires_source_event_id(self):
        td, db = _db_with_events([("ci_completed", {"pr": 1})])
        try:
            rc, _ = self._run(db, "--mark-delivered")
            self.assertEqual(rc, 2)
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
