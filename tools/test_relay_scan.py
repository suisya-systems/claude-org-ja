"""Unit tests for tools.relay_scan (outbox relay, Refs #653 #658)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make ``tools.*`` importable when this file is executed directly
# (``python3 tools/test_relay_scan.py``) and not only via a discovery
# runner, so the __main__ block at the bottom is actually usable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import relay_scan  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402


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
        # Issue #946: relayed so a pane with NO transport configured
        # (which records no notify_failed by design) still surfaces the
        # conflict; the wording follows the two conflict shapes.
        self.assertEqual(
            relay_scan.compose_message(
                "pr_conflict_detected",
                {"pr": 248, "head": "abc1234", "merge_state_status": "DIRTY"}),
            "PR_CONFLICT: PR #248 (head=abc1234, mergeStateStatus=DIRTY) - "
            "conflict のため CI が発火しません [relay]")
        self.assertIn(
            "CI 判定は出ています",
            relay_scan.compose_message(
                "pr_conflict_detected",
                {"pr": 248, "head": "abc1234", "merge_state_status": "DIRTY",
                 "ci_settled": True}))
        self.assertIn("pr_conflict_detected", relay_scan.TERMINAL_KINDS)
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

    def test_missing_head_is_marked_unverifiable(self):
        # Issue #954: a headless terminal event (the secretary's hand-written
        # pr_merged row carries no head) used to render `head=unknown`, which
        # the freshness gate could only read as "mismatched" -- so it silently
        # skipped the watcher close and left a zombie pane. The gap must be
        # rendered as not-comparable, without breaking the message shape the
        # secretary skill parses.
        msg = relay_scan.compose_message("pr_merged", {"pr": 256})
        self.assertEqual(
            msg, "PR_MERGED: PR #256 (head=<missing>) [head-unverifiable] [relay]")
        self.assertNotIn("unknown", msg)
        # A head that IS present keeps the plain tail.
        self.assertNotIn(
            "head-unverifiable",
            relay_scan.compose_message("pr_merged", {"pr": 256, "head": "abc1234"}))
        # For pr_merged_head_unconfirmed the gate compares the baseline, so
        # that is the field whose absence marks the event unverifiable.
        self.assertIn(
            "last CI-confirmed head=<missing>) [head-unverifiable] [relay]",
            relay_scan.compose_message(
                "pr_merged_head_unconfirmed", {"pr": 5, "head": "h"}))
        self.assertNotIn(
            "head-unverifiable",
            relay_scan.compose_message(
                "pr_merged_head_unconfirmed",
                {"pr": 5, "head": "h", "baseline_head": "base"}))
        # The watcher writes the literal "unknown" when it cannot resolve a
        # merged head (tools/pr_watch.py `head_tag = merged_head or "unknown"`), so a
        # truthiness test alone would let that through with a plain tail.
        self.assertEqual(
            relay_scan.compose_message("pr_merged_no_run", {"pr": 7, "head": "unknown"}),
            "PR_MERGED_NO_RUN: PR #7 (head=<missing>) [head-unverifiable] [relay]")
        self.assertIn(
            "head-unverifiable",
            relay_scan.compose_message("pr_merged", {"pr": 7, "head": "  "}))
        self.assertIn(
            "last CI-confirmed head=<missing>) [head-unverifiable] [relay]",
            relay_scan.compose_message(
                "pr_merged_head_unconfirmed",
                {"pr": 7, "head": "h", "baseline_head": "unknown"}))


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

    def test_ledger_epoch_default_survives_long_outage(self):
        """Codex P2: the DEFAULT scan floor is the ledger epoch (a FIXED
        instant), not a moving now-N h window. A terminal event emitted
        after the ledger existed must stay relay-eligible no matter how
        long the dispatcher was down — even years — while pre-ledger
        history stays excluded (anti-flood)."""
        td = tempfile.TemporaryDirectory()
        db = Path(td.name) / "state.db"
        conn = connect(db)
        apply_schema(conn)
        # Pin the ledger epoch to a fixed past instant so we can place
        # events unambiguously before/after it AND far in the past
        # relative to "now" (simulating a multi-year outage).
        conn.execute(
            "UPDATE schema_migrations SET applied_at = "
            "'2010-01-01T00:00:00.000Z' WHERE version = 3")
        # post-epoch but ancient relative to now (the outage case):
        conn.execute(
            "INSERT INTO events (kind, occurred_at, payload_json) "
            "VALUES ('ci_completed','2015-06-01T00:00:00.000Z','{\"pr\":1}')")
        # pre-epoch history: must never be relayed.
        conn.execute(
            "INSERT INTO events (kind, occurred_at, payload_json) "
            "VALUES ('ci_completed','2005-01-01T00:00:00.000Z','{\"pr\":2}')")
        conn.commit()
        self.assertEqual(relay_scan._ledger_epoch(conn),
                         "2010-01-01T00:00:00.000Z")
        conn.close()
        try:
            # DEFAULT (no --since-hours): epoch floor. pr=1 (post-epoch,
            # decade-old relative to "now") surfaces despite its age —
            # this is the outage case; pr=2 (pre-epoch) stays excluded.
            rc, out = self._run(db, "--list")
            prs = {i["payload"]["pr"] for i in json.loads(out)}
            self.assertEqual(prs, {1})
            # Explicit unbounded backfill surfaces pre-epoch history too.
            rc, out = self._run(db, "--list", "--since-hours", "0")
            prs = {i["payload"]["pr"] for i in json.loads(out)}
            self.assertEqual(prs, {1, 2})
        finally:
            td.cleanup()

    def test_wall_clock_override_ages_out_unattempted(self):
        """A wall-clock --since-hours N>0 (operator override) DOES age out
        a never-attempted event older than the window — the behavior the
        epoch default deliberately avoids. Contrast with the epoch default
        test above."""
        td = tempfile.TemporaryDirectory()
        db = Path(td.name) / "state.db"
        conn = connect(db)
        apply_schema(conn)
        conn.execute(
            "INSERT INTO events (kind, occurred_at, payload_json) "
            "VALUES ('ci_completed','2005-01-01T00:00:00.000Z','{\"pr\":9}')")
        conn.commit(); conn.close()
        try:
            rc, out = self._run(db, "--list", "--since-hours", "72")
            self.assertEqual(json.loads(out), [])
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




class TestExecutionTraceAudit(unittest.TestCase):
    """--audit: detect that the relay scan itself stopped running (#941).

    The outage this guards against ran 20 days undetected because a
    silent no-op and a clean scan leave the same evidence (nothing). The
    heartbeat is what separates them, so these tests pin both directions:
    a scan that ran is `fresh`, and no scan at all is `never_scanned`
    even when the ledger looks perfectly healthy.
    """

    def _run(self, db, *args):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = relay_scan._main(["--db", str(db), *args])
        return rc, buf.getvalue()

    def test_audit_before_any_scan_is_never_scanned(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertEqual(rc, 10)
            self.assertEqual(report["status"], "never_scanned")
            self.assertIsNone(report["last_scan_at"])
            # The backlog is reported alongside, which is the number that
            # made the real outage legible once someone finally looked.
            self.assertEqual(report["pending_now"], 1)
        finally:
            td.cleanup()

    def test_audit_after_list_is_fresh(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertEqual(report["status"], "fresh")
            self.assertIsNotNone(report["last_scan_at"])
        finally:
            td.cleanup()

    def test_empty_scan_still_stamps_the_heartbeat(self):
        """The crux: a scan with nothing pending must still prove it ran.

        This is the case the ledger cannot record -- no pending events
        means no ledger rows -- so without the heartbeat it is
        indistinguishable from the command never executing.
        """
        td, db = _db_with_events([("worker_reported", {"pr": 1})])
        try:
            rc, out = self._run(db, "--list")
            self.assertEqual(json.loads(out), [])
            rc, out = self._run(db, "--audit")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["status"], "fresh")
        finally:
            td.cleanup()

    def test_stale_when_heartbeat_is_older_than_threshold(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            hb = relay_scan._heartbeat_path(db)
            data = json.loads(hb.read_text(encoding="utf-8"))
            data["secretary"]["last_scan_at"] = "2026-07-30T20:02:13.418Z"
            hb.write_text(json.dumps(data), encoding="utf-8")
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertEqual(rc, 10)
            self.assertEqual(report["status"], "stale")
            self.assertTrue(report["finding"])
            self.assertGreater(report["age_min"], 15)
        finally:
            td.cleanup()

    def test_corrupt_heartbeat_reads_as_never_scanned(self):
        """A trace we cannot read is a trace we do not have (fail-loud)."""
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            hb = relay_scan._heartbeat_path(db)
            hb.write_text("{not json", encoding="utf-8")
            rc, out = self._run(db, "--audit")
            self.assertEqual(rc, 10)
            self.assertEqual(json.loads(out)["status"], "never_scanned")
        finally:
            td.cleanup()

    def test_structurally_invalid_entry_reads_as_never_scanned(self):
        """Valid JSON but a non-object entry must not break the contract.

        `--audit` promises 0/10/2; an AttributeError escaping as exit 1
        would break the runbook's branch table precisely when the
        heartbeat is corrupt.
        """
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            hb = relay_scan._heartbeat_path(db)
            hb.write_text(json.dumps({"secretary": "corrupt"}),
                          encoding="utf-8")
            rc, out = self._run(db, "--audit")
            self.assertEqual(rc, 10)
            self.assertEqual(json.loads(out)["status"], "never_scanned")
        finally:
            td.cleanup()

    def test_non_string_timestamp_reads_as_never_scanned(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            hb = relay_scan._heartbeat_path(db)
            hb.write_text(json.dumps({"secretary": {"last_scan_at": 12345}}),
                          encoding="utf-8")
            rc, out = self._run(db, "--audit")
            self.assertEqual(rc, 10)
            self.assertEqual(json.loads(out)["status"], "never_scanned")
        finally:
            td.cleanup()

    def test_heartbeat_is_per_recipient(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--recipient", "secretary", "--list")
            rc, out = self._run(db, "--recipient", "secretary", "--audit")
            self.assertEqual(json.loads(out)["status"], "fresh")
            rc, out = self._run(db, "--recipient", "other", "--audit")
            self.assertEqual(rc, 10)
            self.assertEqual(json.loads(out)["status"], "never_scanned")
        finally:
            td.cleanup()

    def test_stale_with_empty_backlog_is_not_a_finding(self):
        """The monitoring loop is designed to stop when idle.

        Step 7 halts it once no worker panes remain and the relay set is
        empty, so the first audit of the next session legitimately sees
        an old heartbeat. Reporting that would fire RELAY_SCAN_STALE on
        nearly every org start and train the reader to ignore it.
        """
        td, db = _db_with_events([("worker_reported", {"pr": 1})])
        try:
            self._run(db, "--list")
            hb = relay_scan._heartbeat_path(db)
            data = json.loads(hb.read_text(encoding="utf-8"))
            data["secretary"]["last_scan_at"] = "2026-07-30T20:02:13.418Z"
            hb.write_text(json.dumps(data), encoding="utf-8")
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertEqual(report["status"], "stale")
            self.assertFalse(report["finding"])
            self.assertEqual(rc, 0)
        finally:
            td.cleanup()

    def test_stale_with_backlog_is_a_finding(self):
        """The real outage signature: nothing draining, events piling up."""
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertEqual(report["status"], "never_scanned")
            self.assertTrue(report["finding"])
            self.assertEqual(rc, 10)
        finally:
            td.cleanup()

    def test_never_scanned_on_a_quiet_org_is_not_a_finding(self):
        """Freshly deployed tool, nothing pending -> no day-one alarm."""
        td, db = _db_with_events([("worker_reported", {"pr": 1})])
        try:
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertEqual(report["status"], "never_scanned")
            self.assertFalse(report["finding"])
            self.assertEqual(rc, 0)
        finally:
            td.cleanup()

    def test_audit_reports_db_errors_as_exit_2(self):
        """A corrupt/incompatible DB must hit the documented error path.

        The runbook only branches on 0/10/2; an uncaught traceback with
        exit 1 has no handler, which is the failure mode this whole tool
        exists to eliminate.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            # A file that exists but is not a usable schema.
            conn = connect(db)
            conn.execute("CREATE TABLE unrelated (x INTEGER)")
            conn.commit()
            conn.close()
            rc, out = self._run(db, "--audit")
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(out)["status"], "error")

    def test_audit_without_db_is_not_a_finding(self):
        """A plain checkout has no relay to run; do not cry outage."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._run(Path(tmp) / "absent.db", "--audit")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["status"], "no_db")

    def test_limit_does_not_distort_the_reported_backlog(self):
        """`surfaced` is per-scan; `pending_now` is the real backlog."""
        td, db = _db_with_events([
            ("ci_completed", {"pr": i, "status": "passed", "head": "a"})
            for i in range(5)
        ])
        try:
            self._run(db, "--list", "--limit", "2")
            rc, out = self._run(db, "--audit")
            self.assertEqual(json.loads(out)["pending_now"], 5)
        finally:
            td.cleanup()


class TestCycle(unittest.TestCase):
    """--cycle: the one command the monitoring cycle has to run (#955).

    The relay stopped for 44.5 minutes on 2026-09-03 with two events
    behind it even though the loop was armed with the canonical /loop 3m
    directive. The cause was structural, not prose compliance: the cycle
    issued about one shell command while the runbook asked for two, and
    the two were self-referential (the read-only audit never causes a
    scan; the scan was reached only "when the audit says stale"). These
    tests pin the collapsed form -- both halves in one invocation, the
    audit half evaluated BEFORE the scan half stamps the heartbeat.
    """

    def _run(self, db, *args):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = relay_scan._main(["--db", str(db), *args])
        return rc, buf.getvalue()

    def _age_heartbeat(self, db, *, recipient="secretary",
                       when="2026-07-30T20:02:13.418Z"):
        hb = relay_scan._heartbeat_path(db)
        data = json.loads(hb.read_text(encoding="utf-8"))
        data[recipient]["last_scan_at"] = when
        hb.write_text(json.dumps(data), encoding="utf-8")

    def test_cycle_returns_both_halves_in_one_json(self):
        td, db = _db_with_events([
            ("ci_completed", {"pr": 7, "status": "passed", "head": "abc1234"}),
        ])
        try:
            rc, out = self._run(db, "--cycle")
            report = json.loads(out)
            # Shape: exactly the two halves, audit first.
            self.assertEqual(set(report), {"audit", "items"})
            self.assertEqual(report["audit"]["status"], "never_scanned")
            self.assertEqual(report["audit"]["pending_now"], 1)
            self.assertEqual(len(report["items"]), 1)
            self.assertEqual(
                report["items"][0]["message"],
                "CI_COMPLETED: PR #7 (status=passed, head=abc1234) [relay]")
            # Exit code follows the audit contract, unchanged.
            self.assertEqual(rc, 10)
        finally:
            td.cleanup()

    def test_cycle_scans_even_when_the_audit_is_clean(self):
        """exit 0 means "no corrective action", never "skip the scan"."""
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            self._run(db, "--mark-delivered", "--source-event-id", "1")
            rc, out = self._run(db, "--cycle")
            report = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertEqual(report["audit"]["status"], "fresh")
            self.assertEqual(report["items"], [])
            # ...and it still stamped a fresh trace of this cycle.
            entry = relay_scan._read_heartbeat(db)["secretary"]
            self.assertGreaterEqual(entry["last_scan_at"],
                                    report["audit"]["last_scan_at"])
        finally:
            td.cleanup()

    def test_cycle_audits_before_it_scans(self):
        """Order is load-bearing: scanning first would report `fresh` forever.

        The scan writes the heartbeat the audit reads, so an audit placed
        after it can never observe a gap -- which would silently destroy
        the detection --audit exists for.
        """
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            self._age_heartbeat(db)
            rc, out = self._run(db, "--cycle")
            report = json.loads(out)
            self.assertEqual(report["audit"]["status"], "stale")
            self.assertTrue(report["audit"]["finding"])
            self.assertEqual(rc, 10)
            # The scan half ran anyway (report and recover in one cycle).
            self.assertEqual(len(report["items"]), 1)
        finally:
            td.cleanup()

    def test_cycle_without_db_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._run(Path(tmp) / "absent.db", "--cycle")
            report = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertEqual(report["audit"]["status"], "no_db")
            self.assertEqual(report["items"], [])

    def test_cycle_reports_db_errors_as_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            conn = connect(db)
            conn.execute("CREATE TABLE unrelated (x INTEGER)")
            conn.commit()
            conn.close()
            rc, out = self._run(db, "--cycle")
            report = json.loads(out)
            self.assertEqual(rc, 2)
            self.assertEqual(report["audit"]["status"], "error")


class TestHaltedAt(unittest.TestCase):
    """--mark-halted: tell an intentional Step 7 stop from a dead relay (#955).

    Step 7 halts the loop once no worker panes remain and the relay set is
    empty. Without a marker, an event landing after that halt makes the
    next session's first audit fire RELAY_SCAN_STALE on an org that is
    behaving exactly as designed -- and an alarm that fires when nothing
    is wrong is one its reader learns to ignore.
    """

    def _run(self, db, *args):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = relay_scan._main(["--db", str(db), *args])
        return rc, buf.getvalue()

    def _age_last_scan(self, db, *, recipient="secretary",
                       when="2026-07-30T20:02:13.418Z"):
        hb = relay_scan._heartbeat_path(db)
        data = json.loads(hb.read_text(encoding="utf-8"))
        data[recipient]["last_scan_at"] = when
        hb.write_text(json.dumps(data), encoding="utf-8")

    def _halted_db(self):
        """A DB whose relay ran, then halted, then had an event land."""
        td, db = _db_with_events([("worker_reported", {"pr": 1})])
        self._run(db, "--list")          # empty scan, stamps the trace
        self._run(db, "--mark-halted")   # Step 7 stops the loop
        self._age_last_scan(db)          # the org sat idle overnight
        conn = connect(db)
        conn.execute(
            "INSERT INTO events (kind, payload_json) VALUES (?, ?)",
            ("ci_completed", json.dumps({"pr": 2, "status": "passed",
                                         "head": "a"})))
        conn.commit()
        conn.close()
        return td, db

    def test_mark_halted_preserves_the_scan_trace(self):
        td, db = _db_with_events([("worker_reported", {"pr": 1})])
        try:
            self._run(db, "--list")
            before = relay_scan._read_heartbeat(db)["secretary"]["last_scan_at"]
            rc, out = self._run(db, "--mark-halted")
            self.assertEqual(rc, 0)
            entry = relay_scan._read_heartbeat(db)["secretary"]
            # The halt annotates the trace; it does not forge one.
            self.assertEqual(entry["last_scan_at"], before)
            self.assertIn("halted_at", entry)
        finally:
            td.cleanup()

    def test_halted_stop_is_not_reported_as_an_outage(self):
        td, db = self._halted_db()
        try:
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            # Descriptive status is unchanged (diagnosis stays honest);
            # only the actionable verdict is suppressed.
            self.assertEqual(report["status"], "stale")
            self.assertEqual(report["pending_now"], 1)
            self.assertTrue(report["halted"])
            self.assertFalse(report["finding"])
            self.assertEqual(rc, 0)
        finally:
            td.cleanup()

    def test_halt_suppression_is_one_shot(self):
        """The resume cycle clears it, so a relay that then dies is caught."""
        td, db = self._halted_db()
        try:
            rc, out = self._run(db, "--cycle")   # first cycle after resume
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(out)["audit"]["finding"])
            entry = relay_scan._read_heartbeat(db)["secretary"]
            self.assertNotIn("halted_at", entry)
            # The relay dies again after the resume: now it IS a finding.
            self._age_last_scan(db)
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertFalse(report["halted"])
            self.assertTrue(report["finding"])
            self.assertEqual(rc, 10)
        finally:
            td.cleanup()

    def test_halt_exemption_is_spent_by_the_audit_that_uses_it(self):
        """One-shot must not depend on a scan following (Codex P1).

        A caller stuck on the compat --audit -- the audit-only failure
        this monitor exists to catch -- would otherwise keep the marker
        forever and suppress the stale-with-backlog finding for good.
        """
        td, db = self._halted_db()
        try:
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertFalse(report["finding"])
            self.assertTrue(report["halt_consumed"])
            self.assertEqual(rc, 0)
            self.assertNotIn("halted_at",
                             relay_scan._read_heartbeat(db)["secretary"])
            # No scan ran in between; the next audit is judged normally.
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertFalse(report["halted"])
            self.assertTrue(report["finding"])
            self.assertEqual(rc, 10)
        finally:
            td.cleanup()

    def test_unspendable_exemption_is_not_granted(self):
        """A marker that cannot be cleared must not silence the monitor.

        Suppressing while the clear fails would leave `halted_at` on disk
        forever and kill every later stale-with-backlog finding. One
        false alert on a host whose telemetry is already broken is the
        safe direction (Codex round 2 P2).
        """
        import unittest.mock as mock
        td, db = self._halted_db()
        try:
            with mock.patch.object(relay_scan, "_write_heartbeat_file",
                                   return_value=False):
                rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertTrue(report["halted"])
            self.assertFalse(report["halt_consumed"])
            self.assertTrue(report["finding"])
            self.assertEqual(rc, 10)
        finally:
            td.cleanup()

    def test_mark_halted_refuses_while_the_relay_is_nonempty(self):
        """Step 7's precondition, enforced at the moment of the halt.

        An event landing between the final --cycle and the halt would
        otherwise be recorded as designed downtime, and the marker would
        excuse the audit that should have surfaced it (Codex round 3).
        """
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            rc, out = self._run(db, "--mark-halted")
            self.assertEqual(rc, 10)
            self.assertIn("halt refused", out)
            self.assertEqual(relay_scan._read_heartbeat(db), {})
        finally:
            td.cleanup()

    def test_mark_halted_reports_db_errors_as_exit_2(self):
        """Step 7 branches on 0/10/2 only; a traceback has no handler."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            conn = connect(db)
            conn.execute("CREATE TABLE unrelated (x INTEGER)")
            conn.commit()
            conn.close()
            rc, _ = self._run(db, "--mark-halted")
            self.assertEqual(rc, 2)

    def test_mark_halted_fails_loudly_when_it_cannot_persist(self):
        """The marker IS the command; a silent failure is a false alert later."""
        import unittest.mock as mock
        td, db = _db_with_events([("worker_reported", {"pr": 1})])
        try:
            with mock.patch.object(relay_scan, "_write_heartbeat_file",
                                   return_value=False):
                rc, _ = self._run(db, "--mark-halted")
            self.assertEqual(rc, 2)
        finally:
            td.cleanup()

    def test_halt_older_than_the_last_scan_does_not_excuse_it(self):
        """A stale marker must not excuse a gap it did not open."""
        td, db = self._halted_db()
        try:
            hb = relay_scan._heartbeat_path(db)
            data = json.loads(hb.read_text(encoding="utf-8"))
            data["secretary"]["halted_at"] = "2026-01-01T00:00:00.000Z"
            hb.write_text(json.dumps(data), encoding="utf-8")
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertFalse(report["halted"])
            self.assertTrue(report["finding"])
            self.assertEqual(rc, 10)
        finally:
            td.cleanup()

    def test_unhalted_death_is_still_a_finding(self):
        """The outage this tool exists for is unaffected by the marker."""
        td, db = _db_with_events([
            ("ci_completed", {"pr": 1, "status": "passed", "head": "a"}),
        ])
        try:
            self._run(db, "--list")
            self._age_last_scan(db)
            rc, out = self._run(db, "--audit")
            report = json.loads(out)
            self.assertIsNone(report["halted_at"])
            self.assertTrue(report["finding"])
            self.assertEqual(rc, 10)
        finally:
            td.cleanup()

    def test_mark_halted_without_db_is_quiet_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _ = self._run(Path(tmp) / "absent.db", "--mark-halted")
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
