"""Unit tests for tools.state_db.writer (M2 write switch, Issue #267)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.state_db import apply_schema, connect
from tools.state_db.writer import StateWriter


def _fresh_db():
    td = tempfile.TemporaryDirectory()
    db = Path(td.name) / "writer.db"
    conn = connect(db)
    apply_schema(conn)
    return td, conn


class TestSessionSingleton(unittest.TestCase):
    def test_writer_seeds_singleton_on_init(self):
        td, conn = _fresh_db()
        try:
            StateWriter(conn)
            row = conn.execute(
                "SELECT id, status FROM org_sessions WHERE id = 1"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "IDLE")
        finally:
            conn.close()
            td.cleanup()

    def test_update_session_patches_listed_fields_only(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.update_session(status="ACTIVE", objective="ship M2",
                             dispatcher_pane_id="2",
                             dispatcher_peer_id="2")
            sess = w.get_session()
            self.assertEqual(sess["status"], "ACTIVE")
            self.assertEqual(sess["objective"], "ship M2")
            self.assertEqual(sess["dispatcher_pane_id"], "2")
            self.assertIsNone(sess["curator_pane_id"])
            # Second call mutates only what's passed.
            w.update_session(status="SUSPENDED", suspended_at="2026-05-04")
            sess = w.get_session()
            self.assertEqual(sess["status"], "SUSPENDED")
            self.assertEqual(sess["objective"], "ship M2")  # untouched
            self.assertEqual(sess["suspended_at"], "2026-05-04")
        finally:
            conn.close()
            td.cleanup()

    def test_update_session_rejects_unknown_field(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            with self.assertRaises(ValueError):
                w.update_session(status="ACTIVE", bogus="x")
        finally:
            conn.close()
            td.cleanup()

    def test_singleton_check_blocks_second_row(self):
        td, conn = _fresh_db()
        try:
            StateWriter(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO org_sessions (id, status) VALUES (2, 'ACTIVE')"
                )
        finally:
            conn.close()
            td.cleanup()


class TestWorkerDirs(unittest.TestCase):
    def test_register_is_idempotent(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            id1 = w.register_worker_dir(abs_path="/x/dir-a", layout="flat")
            id2 = w.register_worker_dir(abs_path="/x/dir-a", layout="flat",
                                         current_branch="main")
            self.assertEqual(id1, id2)
            row = conn.execute(
                "SELECT current_branch, lifecycle FROM worker_dirs "
                "WHERE abs_path = '/x/dir-a'"
            ).fetchone()
            self.assertEqual(row["current_branch"], "main")
            self.assertEqual(row["lifecycle"], "active")
        finally:
            conn.close()
            td.cleanup()

    def test_update_lifecycle_and_remove(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.register_worker_dir(abs_path="/x/dir-b", layout="flat")
            w.update_worker_dir_lifecycle("/x/dir-b", "archived")
            row = conn.execute(
                "SELECT lifecycle, archived FROM worker_dirs "
                "WHERE abs_path = '/x/dir-b'"
            ).fetchone()
            self.assertEqual(row["lifecycle"], "archived")
            self.assertEqual(row["archived"], 1)
            w.remove_worker_dir("/x/dir-b")
            row = conn.execute(
                "SELECT 1 FROM worker_dirs WHERE abs_path = '/x/dir-b'"
            ).fetchone()
            self.assertIsNone(row)
        finally:
            conn.close()
            td.cleanup()


class TestRuns(unittest.TestCase):
    def test_upsert_run_creates_then_updates(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.register_worker_dir(abs_path="/x/wd1")
            run_id = w.upsert_run(
                task_id="t1", project_slug="proj-a", pattern="B",
                title="task one", status="in_use",
                worker_dir_abs_path="/x/wd1",
                issue_refs=["#100", "#101"],
            )
            self.assertGreater(run_id, 0)
            row = conn.execute(
                "SELECT status, title, issue_refs, worker_dir_id "
                "FROM runs WHERE task_id = 't1'"
            ).fetchone()
            self.assertEqual(row["status"], "in_use")
            self.assertEqual(row["title"], "task one")
            self.assertIn("#100", row["issue_refs"])

            # Update path: status change shouldn't null pr_url.
            w.upsert_run(task_id="t1", project_slug="proj-a", pattern="B",
                          status="review", pr_url="https://x/pr/1")
            row = conn.execute(
                "SELECT status, pr_url FROM runs WHERE task_id = 't1'"
            ).fetchone()
            self.assertEqual(row["status"], "review")
            self.assertEqual(row["pr_url"], "https://x/pr/1")

            # Subsequent update with no pr_url must not null the existing one.
            w.upsert_run(task_id="t1", project_slug="proj-a", pattern="B",
                          status="completed")
            row = conn.execute(
                "SELECT status, pr_url FROM runs WHERE task_id = 't1'"
            ).fetchone()
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["pr_url"], "https://x/pr/1")
        finally:
            conn.close()
            td.cleanup()

    def test_update_run_status(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.upsert_run(task_id="t2", project_slug="proj-b", pattern="C")
            w.update_run_status("t2", "completed",
                                 completed_at="2026-05-04T00:00:00.000Z",
                                 outcome_note="ok")
            row = conn.execute(
                "SELECT status, completed_at, outcome_note FROM runs "
                "WHERE task_id = 't2'"
            ).fetchone()
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["completed_at"], "2026-05-04T00:00:00.000Z")
            self.assertEqual(row["outcome_note"], "ok")
        finally:
            conn.close()
            td.cleanup()


class TestEvents(unittest.TestCase):
    def test_append_event_resolves_run_fk(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.upsert_run(task_id="t3", project_slug="proj-c", pattern="A")
            evt_id = w.append_event(
                kind="dispatch", actor="dispatcher",
                payload={"task": "t3", "n": 1},
                run_task_id="t3",
            )
            self.assertGreater(evt_id, 0)
            row = conn.execute(
                "SELECT kind, actor, run_id, project_id, payload_json "
                "FROM events WHERE id = ?", (evt_id,)
            ).fetchone()
            self.assertEqual(row["kind"], "dispatch")
            self.assertEqual(row["actor"], "dispatcher")
            self.assertIsNotNone(row["run_id"])
            self.assertIsNotNone(row["project_id"])
            self.assertIn("\"n\": 1", row["payload_json"])
        finally:
            conn.close()
            td.cleanup()

    def test_append_event_payload_check(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            # Empty payload defaults to '{}' which is valid JSON.
            w.append_event(kind="ping", payload=None)
            row = conn.execute(
                "SELECT payload_json FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(row["payload_json"], "{}")
        finally:
            conn.close()
            td.cleanup()

    def test_append_event_with_explicit_occurred_at(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.append_event(kind="x", occurred_at="2026-05-04T00:00:00.000Z")
            row = conn.execute(
                "SELECT occurred_at FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(row["occurred_at"], "2026-05-04T00:00:00.000Z")
        finally:
            conn.close()
            td.cleanup()


class TestTransactionBoundary(unittest.TestCase):
    def test_rollback_undoes_uncommitted_writes(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.commit()  # flush the auto-seeded singleton
            w.begin()
            w.register_worker_dir(abs_path="/x/uncommitted")
            w.rollback()
            row = conn.execute(
                "SELECT 1 FROM worker_dirs WHERE abs_path = '/x/uncommitted'"
            ).fetchone()
            self.assertIsNone(row)
        finally:
            conn.close()
            td.cleanup()

    def test_commit_persists(self):
        td, conn = _fresh_db()
        try:
            w = StateWriter(conn)
            w.register_worker_dir(abs_path="/x/persist")
            w.commit()
            # Re-open to verify on-disk state.
            db_path = Path([h for h in conn.execute(
                "PRAGMA database_list").fetchall()][0]["file"])
            conn.close()
            conn2 = connect(db_path)
            try:
                row = conn2.execute(
                    "SELECT 1 FROM worker_dirs WHERE abs_path = '/x/persist'"
                ).fetchone()
                self.assertIsNotNone(row)
            finally:
                conn2.close()
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
