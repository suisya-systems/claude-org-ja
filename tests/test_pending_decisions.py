"""Tests for tools/pending_decisions.py (Issue #297)."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import pending_decisions as pd  # noqa: E402


class PendingDecisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Path(self._tmpdir.name) / "pending_decisions.json"

    def _read_raw(self) -> list[dict]:
        return json.loads(self.store.read_text(encoding="utf-8"))

    # (a) ----------------------------------------------------------------
    def test_append_to_empty_store_creates_file_with_pending_entry(self) -> None:
        self.assertFalse(self.store.exists())
        entry = pd.append("t1", "judgment please", store_path=self.store)
        self.assertTrue(self.store.exists())
        self.assertEqual(entry.task_id, "t1")
        self.assertEqual(entry.status, "pending")
        self.assertEqual(entry.raw_message, "judgment please")
        self.assertIsNone(entry.resolved_at)
        self.assertIsNone(entry.resolution_kind)
        raw = self._read_raw()
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["task_id"], "t1")

    # (b) ----------------------------------------------------------------
    def test_append_same_task_id_is_idempotent(self) -> None:
        first = pd.append("t1", "first", store_path=self.store)
        second = pd.append("t1", "second body ignored", store_path=self.store)
        self.assertEqual(first.received_at, second.received_at)
        self.assertEqual(second.raw_message, "first")  # original kept
        raw = self._read_raw()
        self.assertEqual(len(raw), 1)

    # (c) ----------------------------------------------------------------
    def test_resolve_to_user_marks_escalated(self) -> None:
        pd.append("t1", "ask", store_path=self.store)
        out = pd.resolve("t1", "to_user", store_path=self.store)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.status, "escalated")
        self.assertEqual(out.resolution_kind, "to_user")
        self.assertIsNotNone(out.resolved_at)

    # (d) ----------------------------------------------------------------
    def test_resolve_to_worker_marks_resolved(self) -> None:
        pd.append("t1", "ask", store_path=self.store)
        out = pd.resolve("t1", "to_worker", store_path=self.store)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.status, "resolved")
        self.assertEqual(out.resolution_kind, "to_worker")

    # (e) ----------------------------------------------------------------
    def test_resolve_unknown_task_id_returns_none(self) -> None:
        pd.append("t1", "ask", store_path=self.store)
        self.assertIsNone(pd.resolve("nonexistent", "to_user", store_path=self.store))
        # Original pending entry untouched
        pending = pd.list_pending(store_path=self.store)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].status, "pending")

    # (f) ----------------------------------------------------------------
    def test_list_pending_older_than_filters_by_received_at(self) -> None:
        old_ts = "2026-05-05T00:00:00Z"
        new_ts = "2026-05-05T05:50:00Z"
        seed = [
            {
                "task_id": "old",
                "received_at": old_ts,
                "raw_message": "old",
                "status": "pending",
            },
            {
                "task_id": "new",
                "received_at": new_ts,
                "raw_message": "new",
                "status": "pending",
            },
            {
                "task_id": "done",
                "received_at": old_ts,
                "raw_message": "done",
                "status": "resolved",
                "resolved_at": old_ts,
                "resolution_kind": "to_worker",
            },
        ]
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(seed), encoding="utf-8")

        now = datetime(2026, 5, 5, 6, 0, 0, tzinfo=timezone.utc)
        result = pd.list_pending_older_than(15, store_path=self.store, now=now)
        self.assertEqual([e.task_id for e in result], ["old"])

    # (g) ----------------------------------------------------------------
    def test_atomic_write_leaves_no_tmp_file(self) -> None:
        pd.append("t1", "ask", store_path=self.store)
        pd.resolve("t1", "to_user", store_path=self.store)
        siblings = list(self.store.parent.iterdir())
        names = sorted(p.name for p in siblings)
        self.assertEqual(names, [self.store.name])
        # No leftover tmp file
        self.assertFalse(any(p.name.endswith(".tmp") for p in siblings))

    # (h) ----------------------------------------------------------------
    def test_malformed_json_raises_value_error(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            pd.list_pending(store_path=self.store)

    # extras -------------------------------------------------------------
    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(pd.list_pending(store_path=self.store), [])
        self.assertEqual(
            pd.list_pending_older_than(15, store_path=self.store), []
        )

    def test_resolve_picks_oldest_pending_when_multiple(self) -> None:
        # Direct-seed two pending entries for the same task_id (the
        # public append() de-dups, so we bypass it to construct the
        # rare "multiple pending" state described in the spec).
        seed = [
            {
                "task_id": "t1",
                "received_at": "2026-05-05T05:00:00Z",
                "raw_message": "newer",
                "status": "pending",
            },
            {
                "task_id": "t1",
                "received_at": "2026-05-05T04:00:00Z",
                "raw_message": "older",
                "status": "pending",
            },
        ]
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(seed), encoding="utf-8")

        out = pd.resolve("t1", "to_worker", store_path=self.store)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.raw_message, "older")
        # The newer one stays pending.
        remaining = pd.list_pending(store_path=self.store)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].raw_message, "newer")

    def test_resolve_unknown_kind_raises(self) -> None:
        pd.append("t1", "ask", store_path=self.store)
        with self.assertRaises(ValueError):
            pd.resolve("t1", "to_nowhere", store_path=self.store)  # type: ignore[arg-type]

    def test_append_after_resolve_creates_new_pending(self) -> None:
        pd.append("t1", "first", store_path=self.store)
        pd.resolve("t1", "to_worker", store_path=self.store)
        # A new judgment-request for the same task should re-open.
        new_entry = pd.append("t1", "second round", store_path=self.store)
        self.assertEqual(new_entry.status, "pending")
        self.assertEqual(new_entry.raw_message, "second round")
        raw = self._read_raw()
        self.assertEqual(len(raw), 2)

    def test_cli_append_and_list(self) -> None:
        rc = pd.main(
            [
                "--store",
                str(self.store),
                "append",
                "--task-id",
                "tcli",
                "--message",
                "hi",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(pd.list_pending(store_path=self.store)), 1)


if __name__ == "__main__":
    unittest.main()
