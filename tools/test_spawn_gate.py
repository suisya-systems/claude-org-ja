"""Unit tests for tools/spawn_gate.py (dispatcher spawn-completion gate).

The gate exists because the spawn ceremony
(``.dispatcher/references/spawn-flow.md`` 3-3b approval Enter / 3-4
``list_peers`` wait / 3-5 instruction send) left no artifact, so a
dispatcher that skipped it produced a `.state/` trace identical to one
that performed it. The tests below pin the two properties that fix
depends on:

* ``verify`` refuses to emit a ``DELEGATE_COMPLETE`` body unless the
  supplied ``list_peers`` observation matches the worker dir the
  *secretary* recorded at T1 — the one input the dispatcher cannot
  produce by assertion.
* ``audit`` finds a ``worker_spawned`` with no ``worker_spawn_verified``,
  which is exactly the trace both 2026-08-18 incidents left behind.
"""

from __future__ import annotations

import json
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawn_gate  # noqa: E402

WORKER_DIR = "/tmp/org/workers/login-fix"
#: Seeded spawn time for VerifyTests. Must be in the past relative to the
#: real clock, because `verify` compares the verification it writes (stamped
#: `now` by SQLite) against the latest `worker_spawned`.
_PAST = "2000-01-01T00:00:00.000Z"
TASK = "login-fix"


def _make_db(path: Path) -> None:
    """A state.db with just enough schema for the gate's reads/writes."""
    from tools.state_db import apply_schema, connect

    conn = connect(path)
    try:
        apply_schema(conn)
    finally:
        conn.close()


def _seed_run(db: Path, task: str = TASK, worker_dir: str = WORKER_DIR) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO projects (slug, display_name) VALUES (?, ?)",
            ("proj", "Proj"),
        )
        project_id = conn.execute(
            "SELECT id FROM projects WHERE slug = 'proj'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO worker_dirs (abs_path, layout) VALUES (?, 'flat')",
            (worker_dir,),
        )
        dir_id = conn.execute(
            "SELECT id FROM worker_dirs WHERE abs_path = ?", (worker_dir,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status, "
            "worker_dir_id) VALUES (?, ?, 'A', ?, 'queued', ?)",
            (task, project_id, task, dir_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_event(db: Path, kind: str, payload: dict, occurred_at: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES (?, 'dispatcher', ?, ?)",
            (occurred_at, kind, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def _run(argv: "list[str]") -> "tuple[int, dict]":
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = spawn_gate.main(argv)
    return code, json.loads(buf.getvalue())


class VerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "state.db"
        _make_db(self.db)
        _seed_run(self.db)
        _seed_event(
            self.db,
            "worker_spawned",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            _PAST,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, **over) -> "list[str]":
        args = {
            "--task": TASK,
            "--pane-id": "5",
            "--peer-id": "5",
            "--peer-name": f"worker-{TASK}",
            "--peer-cwd": WORKER_DIR,
            "--approval": "sent",
            "--instruction": "send_message",
        }
        args.update(over)
        argv = ["--db-path", str(self.db), "verify"]
        for key, val in args.items():
            argv += [key, val]
        return argv

    def test_happy_path_records_event_and_emits_report(self) -> None:
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "verified")
        self.assertIn("DELEGATE_COMPLETE", out["delegate_complete"])
        self.assertIn("id=5", out["delegate_complete"])

        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM events WHERE kind = ?",
                (spawn_gate.VERIFIED_EVENT,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][0])
        self.assertEqual(payload["peer_id"], 5)
        self.assertEqual(payload["peer_cwd"], WORKER_DIR)

    def test_transport_comes_from_the_shared_resolver(self) -> None:
        """Not a literal: Epic #586 flipped DEFAULT_TRANSPORT to broker."""
        from tools.transport import resolve as _resolve

        _, out = _run(self._argv())
        self.assertEqual(out["recorded"]["transport"], str(_resolve()))

    def test_explicit_transport_wins(self) -> None:
        _, out = _run(self._argv(**{"--transport": "renga"}))
        self.assertEqual(out["recorded"]["transport"], "renga")

    def test_limitations_travel_with_the_evidence(self) -> None:
        """The attested/verified boundary must not live only in prose."""
        _, out = _run(self._argv())
        self.assertIn("機械検証", out["limitations"])

    def test_cwd_mismatch_blocks_the_report(self) -> None:
        """The one check the dispatcher cannot satisfy by asserting it."""
        code, out = _run(self._argv(**{"--peer-cwd": "/tmp/org/workers/other"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["status"], "gate_failed")
        self.assertIn("peer_cwd", out["failures"])
        self.assertNotIn("delegate_complete", out)

    def test_trailing_separator_is_not_a_mismatch(self) -> None:
        code, _ = _run(self._argv(**{"--peer-cwd": WORKER_DIR + "/"}))
        self.assertEqual(code, spawn_gate.EXIT_OK)

    def test_background_tab_requires_peer_id_to_equal_pane_id(self) -> None:
        """3-4b opens the background gate only on exact bound_pane_id match."""
        code, out = _run(
            self._argv(**{"--placement": "background_tab", "--peer-id": "9"})
        )
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("bound_pane_id", out["failures"])

    def test_background_tab_accepts_the_bound_pane_id(self) -> None:
        code, out = _run(self._argv(**{"--placement": "background_tab"}))
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["recorded"]["placement"], "background_tab")

    def test_same_tab_does_not_impose_id_equality(self) -> None:
        """broker peer ids need not equal pane ids on the default path."""
        code, _ = _run(self._argv(**{"--peer-id": "9"}))
        self.assertEqual(code, spawn_gate.EXIT_OK)

    def test_wrong_peer_name_blocks_the_report(self) -> None:
        code, out = _run(self._argv(**{"--peer-name": "worker-other"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("peer_name", out["failures"])

    def test_non_numeric_peer_id_blocks_the_report(self) -> None:
        """`list_peers` not yet showing the worker is the failure to catch."""
        code, out = _run(self._argv(**{"--peer-id": "unknown"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("peer_id", out["failures"])
        self.assertTrue(any("3-3b" in r for r in out["remedy"]))

    def test_gate_cannot_run_before_the_spawn_it_gates(self) -> None:
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("DELETE FROM events WHERE kind = 'worker_spawned'")
            conn.commit()
        finally:
            conn.close()
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("spawned_event", out["failures"])

    def test_unknown_task_is_blocked(self) -> None:
        code, out = _run(self._argv(**{"--task": "no-such-task"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("run_row", out["failures"])

    def test_runs_row_is_the_fallback_when_delegate_sent_is_absent(self) -> None:
        """No delegate_sent (older run): runs.worker_dir_id still serves."""
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["checks"]["expected_worker_dir_source"], "runs")

    def test_delegate_sent_is_preferred_over_the_mutable_runs_row(self) -> None:
        """Step 4's upsert_run can move runs.worker_dir_id; events cannot."""
        _seed_event(
            self.db,
            "delegate_sent",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            _PAST,
        )
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(
            out["checks"]["expected_worker_dir_source"], "delegate_sent"
        )

    def test_runs_row_moved_away_from_the_t1_value_is_a_failure(self) -> None:
        """The gate must not certify once its independent reference is gone."""
        _seed_event(
            self.db,
            "delegate_sent",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            _PAST,
        )
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO worker_dirs (abs_path, layout) VALUES (?, 'flat')",
                ("/tmp/org/workers/moved",),
            )
            conn.execute(
                "UPDATE runs SET worker_dir_id = "
                "(SELECT id FROM worker_dirs WHERE abs_path = ?)",
                ("/tmp/org/workers/moved",),
            )
            conn.commit()
        finally:
            conn.close()
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("worker_dir_divergence", out["failures"])

    def test_degraded_send_delivery_evidence_is_accepted(self) -> None:
        """spawn-flow 3-4 縮退: enumeration discarded, delivery is the probe."""
        code, out = _run(
            [
                "--db-path",
                str(self.db),
                "verify",
                "--task",
                TASK,
                "--pane-id",
                "5",
                "--evidence",
                "send_delivery",
                "--approval",
                "sent",
                "--instruction",
                "send_message",
            ]
        )
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertIn("DELEGATE_COMPLETE", out["delegate_complete"])
        self.assertIn("縮退", out["delegate_complete"])
        self.assertEqual(out["recorded"]["evidence"], "send_delivery")
        self.assertIsNone(out["recorded"]["peer_id"])

    def test_degraded_mode_rejects_a_quoted_peer_record(self) -> None:
        """A discarded enumeration must not ride in under the weaker mode."""
        code, out = _run(self._argv(**{"--evidence": "send_delivery"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("evidence_mismatch", out["failures"])

    def test_redispatch_appends_a_second_verification(self) -> None:
        """A stale verification must not silently cover a later respawn."""
        self.assertEqual(_run(self._argv())[0], spawn_gate.EXIT_OK)
        _seed_event(
            self.db,
            "worker_spawned",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            "2099-01-01T00:00:00.000Z",
        )
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "verified")

        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?",
                (spawn_gate.VERIFIED_EVENT,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2)

    def test_rerun_is_idempotent(self) -> None:
        self.assertEqual(_run(self._argv())[0], spawn_gate.EXIT_OK)
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "already_verified")
        self.assertIn("DELEGATE_COMPLETE", out["delegate_complete"])

        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?",
                (spawn_gate.VERIFIED_EVENT,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "state.db"
        _make_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _audit(self, *extra: str) -> "tuple[int, dict]":
        # Tests seed fixed timestamps around GATE_EPOCH, so the default cutoff
        # would filter some of them out; disable it unless a case sets it.
        argv = ["--db-path", str(self.db), "audit", *extra]
        if "--since" not in extra:
            argv += ["--since", ""]
        return _run(argv)

    def test_clean_when_every_spawn_is_verified(self) -> None:
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T00:00:00.000Z"
        )
        _seed_event(
            self.db,
            spawn_gate.VERIFIED_EVENT,
            {"task": TASK},
            "2026-08-18T00:00:30.000Z",
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "clean")

    def test_the_2026_08_18_trace_is_a_finding(self) -> None:
        """delegate_sent -> worker_spawned -> nothing: exactly the incident."""
        _seed_event(
            self.db, "delegate_sent", {"task": TASK}, "2026-08-17T17:05:43.236Z"
        )
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-17T17:07:33.126Z"
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["finding_count"], 1)
        self.assertEqual(out["findings"][0]["task"], TASK)
        self.assertIsNone(out["findings"][0]["last_verified_at"])

    def test_grace_window_suppresses_an_in_flight_ceremony(self) -> None:
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        _seed_event(
            self.db,
            "worker_spawned",
            {"task": TASK},
            now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        self.assertEqual(self._audit("--older-than-min", "5")[0], spawn_gate.EXIT_OK)
        self.assertEqual(self._audit("--older-than-min", "0")[0], spawn_gate.EXIT_FIRE)

    def test_redispatch_needs_a_fresh_verification(self) -> None:
        """A stale verification must not cover a later re-spawn."""
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T00:00:00.000Z"
        )
        _seed_event(
            self.db,
            spawn_gate.VERIFIED_EVENT,
            {"task": TASK},
            "2026-08-18T00:00:30.000Z",
        )
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T02:00:00.000Z"
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["findings"][0]["last_verified_at"], "2026-08-18T00:00:30.000Z")

    def test_gate_epoch_excludes_spawns_that_predate_the_gate(self) -> None:
        """383 pre-deployment spawns must not drown the live signal."""
        _seed_event(
            self.db, "worker_spawned", {"task": "ancient"}, "2026-05-11T07:40:30.206Z"
        )
        code, out = self._audit(
            "--older-than-min", "0", "--since", spawn_gate.GATE_EPOCH
        )
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["skipped"]["before_gate_epoch"], 1)

    def test_an_old_unverified_spawn_is_never_forgotten(self) -> None:
        """A rolling window would drop this once it aged past 24h."""
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-19T00:00:00.000Z"
        )
        code, out = self._audit("--older-than-min", "0", "--since", spawn_gate.GATE_EPOCH)
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["finding_count"], 1)

    def test_completed_run_is_not_a_finding(self) -> None:
        """A run that finished is no longer actionable, and is counted."""
        _seed_run(self.db)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE runs SET status = 'completed'")
            conn.commit()
        finally:
            conn.close()
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T00:00:00.000Z"
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["skipped"]["terminal_run"], 1)


if __name__ == "__main__":
    unittest.main()
