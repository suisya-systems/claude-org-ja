"""Tests for tools.check_state_drift (Issue #356, Epic #357).

Covers each drift class declared in the module docstring:

* D1 ``queued_stale`` — fresh queued is clean; aged queued reports drift.
* D2 ``live_run_missing_worker_file`` — in_use / review without .md.
* D3 ``completed_run_worker_file_present`` — completed with live .md.
* D4 ``terminal_nonarchived_worker_file`` — failed / abandoned with live .md.

Plus the warn-only guarantee: detect_drift returns records but never
mutates state.db or the workers directory.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.check_state_drift import (
    DriftRecord,
    _DEFAULT_QUEUED_STALE_SECONDS,
    _main,
    detect_drift,
)
from tools.state_db import apply_schema, connect


def _seed_project(conn: sqlite3.Connection, slug: str = "demo") -> int:
    cur = conn.execute(
        "INSERT INTO projects (slug, display_name) VALUES (?, ?)",
        (slug, slug),
    )
    return int(cur.lastrowid)


def _insert_run(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    project_id: int,
    status: str,
    dispatched_at: str,
) -> None:
    conn.execute(
        "INSERT INTO runs "
        "(task_id, project_id, pattern, title, status, dispatched_at) "
        "VALUES (?, ?, 'B', ?, ?, ?)",
        (task_id, project_id, task_id, status, dispatched_at),
    )


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DriftDetectorTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "state.db"
        self.workers = self.root / "workers"
        (self.workers / "archive").mkdir(parents=True)

        conn = connect(self.db_path)
        try:
            apply_schema(conn)
            self.project_id = _seed_project(conn)
            conn.commit()
        finally:
            conn.close()

        # Pin a deterministic clock; D1 thresholding compares `now` to
        # dispatched_at, so all queued tests reference this fixed instant.
        self.now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _write(self, **runs: tuple) -> None:
        """``runs[task_id] = (status, dispatched_at_iso)``."""
        conn = connect(self.db_path)
        try:
            for task_id, (status, dispatched) in runs.items():
                _insert_run(
                    conn,
                    task_id=task_id,
                    project_id=self.project_id,
                    status=status,
                    dispatched_at=dispatched,
                )
            conn.commit()
        finally:
            conn.close()

    def _touch_worker(
        self, task_id: str, *, archived: bool = False,
    ) -> Path:
        target_dir = self.workers / "archive" if archived else self.workers
        path = target_dir / f"worker-{task_id}.md"
        path.write_text(f"Task: {task_id}\nStatus: active\n", encoding="utf-8")
        return path

    def _detect(
        self,
        *,
        queued_stale_seconds: int = _DEFAULT_QUEUED_STALE_SECONDS,
    ) -> list[DriftRecord]:
        return detect_drift(
            self.db_path,
            self.workers,
            queued_stale_seconds=queued_stale_seconds,
            now=self.now,
        )

    # ------------------------------------------------------------------
    # D1 queued_stale
    # ------------------------------------------------------------------

    def test_fresh_queued_run_is_clean(self):
        recent = self.now - timedelta(seconds=10)
        self._write(t_fresh=("queued", _iso(recent)))
        records = self._detect()
        self.assertEqual(records, [])

    def test_aged_queued_run_is_drift(self):
        old = self.now - timedelta(seconds=900)
        self._write(t_old=("queued", _iso(old)))
        records = self._detect()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.klass, "queued_stale")
        self.assertEqual(rec.task_id, "t_old")
        self.assertTrue(rec.ambiguous)
        self.assertIn("900", rec.detail)

    def test_queued_threshold_respects_cli_override(self):
        old = self.now - timedelta(seconds=120)
        self._write(t=("queued", _iso(old)))
        # Default threshold (300s) — clean.
        self.assertEqual(self._detect(), [])
        # Tightened threshold — drift fires.
        records = self._detect(queued_stale_seconds=60)
        self.assertEqual([r.klass for r in records], ["queued_stale"])

    def test_unparseable_dispatched_at_is_drift(self):
        self._write(t_bad=("queued", "not-a-date"))
        records = self._detect()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].klass, "queued_stale")
        self.assertIn("unparseable", records[0].detail)

    # ------------------------------------------------------------------
    # D2 live_run_missing_worker_file
    # ------------------------------------------------------------------

    def test_in_use_run_with_worker_file_is_clean(self):
        ts = _iso(self.now)
        self._write(t_live=("in_use", ts))
        self._touch_worker("t_live")
        self.assertEqual(self._detect(), [])

    def test_in_use_run_without_worker_file_is_drift(self):
        ts = _iso(self.now)
        self._write(t_live=("in_use", ts))
        records = self._detect()
        self.assertEqual([r.klass for r in records],
                         ["live_run_missing_worker_file"])
        self.assertEqual(records[0].task_id, "t_live")
        self.assertTrue(records[0].ambiguous)

    def test_review_run_without_worker_file_is_drift(self):
        ts = _iso(self.now)
        self._write(t_review=("review", ts))
        records = self._detect()
        self.assertEqual([r.klass for r in records],
                         ["live_run_missing_worker_file"])

    def test_review_recovery_action_does_not_recommend_T7(self):
        # Codex review caught: Set F § 4 review's normal exits are T5/T6;
        # pushing T7 (abandoned) would discard a worker that already
        # submitted a completion report.
        ts = _iso(self.now)
        self._write(t_review=("review", ts))
        records = self._detect()
        self.assertEqual(len(records), 1)
        action = records[0].operator_action
        self.assertIn("T5", action)
        self.assertIn("T6", action)
        # The negation phrasing must be present so the Secretary cannot
        # accidentally read T7 as the recommended path.
        self.assertIn("Do NOT apply T7", action)

    def test_in_use_recovery_action_does_recommend_T7(self):
        ts = _iso(self.now)
        self._write(t_live=("in_use", ts))
        records = self._detect()
        self.assertEqual(len(records), 1)
        self.assertIn("T7", records[0].operator_action)

    # ------------------------------------------------------------------
    # D3 completed_run_worker_file_present
    # ------------------------------------------------------------------

    def test_completed_run_archived_file_is_clean(self):
        ts = _iso(self.now)
        self._write(t_done=("completed", ts))
        self._touch_worker("t_done", archived=True)
        self.assertEqual(self._detect(), [])

    def test_completed_run_with_live_file_is_drift(self):
        ts = _iso(self.now)
        self._write(t_done=("completed", ts))
        self._touch_worker("t_done")
        records = self._detect()
        self.assertEqual([r.klass for r in records],
                         ["completed_run_worker_file_present"])
        self.assertFalse(records[0].ambiguous)

    # ------------------------------------------------------------------
    # D4 terminal_nonarchived_worker_file (future-covered)
    # ------------------------------------------------------------------

    def test_failed_run_with_live_file_is_drift(self):
        ts = _iso(self.now)
        self._write(t_fail=("failed", ts))
        self._touch_worker("t_fail")
        records = self._detect()
        self.assertEqual([r.klass for r in records],
                         ["terminal_nonarchived_worker_file"])

    def test_abandoned_run_with_live_file_is_drift(self):
        ts = _iso(self.now)
        self._write(t_abandon=("abandoned", ts))
        self._touch_worker("t_abandon")
        records = self._detect()
        self.assertEqual([r.klass for r in records],
                         ["terminal_nonarchived_worker_file"])

    # ------------------------------------------------------------------
    # warn-only guarantee
    # ------------------------------------------------------------------

    def test_detect_does_not_mutate_journal_mode(self):
        # Codex review caught: opening via tools.state_db.connect issues
        # PRAGMA journal_mode=WAL, which physically writes to the DB
        # (creating -wal/-shm siblings, flipping the mode). A warn-only
        # detector must NOT do that. Build a fresh delete-mode DB so the
        # mutation, if it happened, would visibly flip the mode.
        fresh_db = self.root / "ro_guard.db"
        c = sqlite3.connect(fresh_db)
        try:
            apply_schema(c)
            mode_before = c.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0].lower()
            c.commit()
        finally:
            c.close()
        self.assertEqual(mode_before, "delete")
        before_mtime = fresh_db.stat().st_mtime_ns

        detect_drift(
            fresh_db,
            self.workers,
            queued_stale_seconds=_DEFAULT_QUEUED_STALE_SECONDS,
            now=self.now,
        )

        c = sqlite3.connect(fresh_db)
        try:
            mode_after = c.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0].lower()
        finally:
            c.close()
        self.assertEqual(mode_after, "delete")
        # No -wal / -shm siblings on a delete-mode DB.
        for sibling in ("ro_guard.db-wal", "ro_guard.db-shm"):
            self.assertFalse(
                (self.root / sibling).exists(),
                f"unexpected sibling: {sibling}",
            )
        self.assertEqual(fresh_db.stat().st_mtime_ns, before_mtime)

    def test_detect_does_not_mutate_db_or_files(self):
        ts = _iso(self.now - timedelta(seconds=900))
        self._write(t_q=("queued", ts))
        self._write(t_done=("completed", _iso(self.now)))
        live_md = self._touch_worker("t_done")
        archived_md = self._touch_worker("done2", archived=True)

        before_db = self.db_path.read_bytes()
        before_live = live_md.read_bytes()
        before_archive_listing = sorted(
            p.name for p in (self.workers / "archive").iterdir()
        )
        before_root_listing = sorted(
            p.name for p in self.workers.iterdir() if p.is_file()
        )

        records = self._detect()
        self.assertGreaterEqual(len(records), 1)

        # DB byte-identical (warn-only contract).
        self.assertEqual(self.db_path.read_bytes(), before_db)
        # Worker files untouched in place.
        self.assertEqual(live_md.read_bytes(), before_live)
        self.assertTrue(archived_md.exists())
        self.assertEqual(
            sorted(p.name for p in (self.workers / "archive").iterdir()),
            before_archive_listing,
        )
        self.assertEqual(
            sorted(
                p.name for p in self.workers.iterdir() if p.is_file()
            ),
            before_root_listing,
        )


class CliMainTests(unittest.TestCase):
    """Smoke-test the argparse entrypoint and exit-code contract.

    Exit-code contract is part of the operator interface — runbooks gate
    on ``rc==1`` to know "warn fired".
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "state.db"
        self.workers = self.root / "workers"
        (self.workers / "archive").mkdir(parents=True)
        conn = connect(self.db_path)
        try:
            apply_schema(conn)
            project_id = _seed_project(conn)
            self.project_id = project_id
            conn.commit()
        finally:
            conn.close()

    def _argv(self, *extra: str) -> list[str]:
        return [
            "--db", str(self.db_path),
            "--workers-dir", str(self.workers),
            *extra,
        ]

    def test_exit_code_zero_when_clean(self):
        rc = _main(self._argv())
        self.assertEqual(rc, 0)

    def test_exit_code_one_when_drift(self):
        # Insert a stale queued run with dispatched_at well past threshold.
        old = "2020-01-01T00:00:00.000000Z"
        conn = connect(self.db_path)
        try:
            _insert_run(
                conn,
                task_id="ancient",
                project_id=self.project_id,
                status="queued",
                dispatched_at=old,
            )
            conn.commit()
        finally:
            conn.close()
        rc = _main(self._argv())
        self.assertEqual(rc, 1)

    def test_exit_code_two_when_db_missing(self):
        rc = _main([
            "--db", str(self.root / "nonexistent.db"),
            "--workers-dir", str(self.workers),
        ])
        self.assertEqual(rc, 2)

    def test_exit_code_two_when_workers_dir_missing(self):
        rc = _main([
            "--db", str(self.db_path),
            "--workers-dir", str(self.root / "nonexistent_workers"),
        ])
        self.assertEqual(rc, 2)

    def test_json_output_is_parseable(self):
        old = "2020-01-01T00:00:00.000000Z"
        conn = connect(self.db_path)
        try:
            _insert_run(
                conn,
                task_id="ancient",
                project_id=self.project_id,
                status="queued",
                dispatched_at=old,
            )
            conn.commit()
        finally:
            conn.close()

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _main(self._argv("--json"))
        self.assertEqual(rc, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["klass"], "queued_stale")
        self.assertEqual(payload[0]["task_id"], "ancient")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
