"""Unit tests for tools.state_db.curator_archive (M3, Issue #267)."""
from __future__ import annotations

import errno as _errno
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tools.state_db import apply_schema, connect
from tools.state_db import curator_archive as ca


def _seed_db(db_path):
    conn = connect(db_path)
    apply_schema(conn)
    return conn


def _insert_dir(conn, abs_path, lifecycle="active"):
    conn.execute(
        "INSERT INTO worker_dirs (abs_path, layout, lifecycle) VALUES (?, 'project_workstream', ?)",
        (abs_path, lifecycle),
    )
    conn.commit()


def _set_mtime(path, days_ago):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


class _TempCase(unittest.TestCase):
    """Test base providing tmp / root / db plumbing."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "workers"
        self.root.mkdir()
        self.conn = _seed_db(self.tmp / "state.db")

    def tearDown(self):
        self.conn.close()
        self._td.cleanup()


# ---------------------------------------------------------------------------
# Quarter / target derivation
# ---------------------------------------------------------------------------


class TestQuarterAndTarget(unittest.TestCase):

    def test_archive_quarter(self):
        self.assertEqual(ca.archive_quarter(datetime(2026, 5, 4, tzinfo=timezone.utc)), "2026-Q2")
        self.assertEqual(ca.archive_quarter(datetime(2026, 1, 1, tzinfo=timezone.utc)), "2026-Q1")
        self.assertEqual(ca.archive_quarter(datetime(2026, 12, 31, tzinfo=timezone.utc)), "2026-Q4")

    def test_derive_archive_target_three_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workers"
            src = root / "claude-org-ja" / "_runs" / "public-release" / "open-flip-plan"
            target = ca.derive_archive_target(str(src.as_posix()), root, "2026-Q2")
            self.assertTrue(target.endswith(
                "/_archive/2026-Q2/claude-org-ja/public-release/open-flip-plan"))

    def test_derive_archive_target_flat_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workers"
            src = root / "legacy-flat-dir"
            target = ca.derive_archive_target(str(src.as_posix()), root, "2026-Q2")
            self.assertTrue(target.endswith("/_archive/2026-Q2/legacy-flat-dir"))


# ---------------------------------------------------------------------------
# Selection / Apply / Purge
# ---------------------------------------------------------------------------


class TestArchive(_TempCase):

    def test_active_under_90d_is_noop(self):
        p = self.root / "claude-org-ja" / "_runs" / "ws" / "fresh"
        p.mkdir(parents=True)
        _set_mtime(p, days_ago=10)
        _insert_dir(self.conn, str(p.as_posix()))
        self.assertEqual(ca.select_archive_candidates(self.conn, self.root), [])

    def test_active_over_90d_is_candidate(self):
        p = self.root / "claude-org-ja" / "_runs" / "ws" / "stale"
        p.mkdir(parents=True)
        _set_mtime(p, days_ago=120)
        _insert_dir(self.conn, str(p.as_posix()))
        cands = ca.select_archive_candidates(self.conn, self.root)
        self.assertEqual(len(cands), 1)
        self.assertTrue(cands[0].target.endswith(
            "_archive/" + ca.archive_quarter() + "/claude-org-ja/ws/stale"))

    def test_apply_archive_moves_dir_and_updates_db(self):
        p = self.root / "claude-org-ja" / "_runs" / "ws" / "stale"
        p.mkdir(parents=True)
        (p / "marker.txt").write_text("hi", encoding="utf-8")
        _set_mtime(p, days_ago=200)
        _insert_dir(self.conn, str(p.as_posix()))
        cands = ca.select_archive_candidates(self.conn, self.root)
        n = ca.apply_archive(self.conn, cands)
        self.assertEqual(n, 1)
        self.assertFalse(p.exists())
        new_path = Path(cands[0].target)
        self.assertEqual((new_path / "marker.txt").read_text(encoding="utf-8"), "hi")
        row = self.conn.execute("SELECT abs_path, lifecycle FROM worker_dirs").fetchone()
        self.assertEqual(row["lifecycle"], "archived")
        self.assertEqual(row["abs_path"], cands[0].target)

    def test_purge_deletes_delete_pending(self):
        p = self.root / "trash-dir"; p.mkdir()
        (p / "junk").write_text("x", encoding="utf-8")
        _insert_dir(self.conn, str(p.as_posix()), lifecycle="delete_pending")
        cands = ca.select_purge_candidates(self.conn)
        self.assertEqual(len(cands), 1)
        n = ca.apply_purge(self.conn, cands)
        self.assertEqual(n, 1)
        self.assertFalse(p.exists())
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM worker_dirs").fetchone()[0], 0)

    def test_apply_archive_skips_active_under_age(self):
        fresh = self.root / "claude-org-ja" / "_runs" / "ws" / "fresh"; fresh.mkdir(parents=True)
        stale = self.root / "claude-org-ja" / "_runs" / "ws" / "stale"; stale.mkdir(parents=True)
        _set_mtime(fresh, days_ago=10)
        _set_mtime(stale, days_ago=100)
        _insert_dir(self.conn, str(fresh.as_posix()))
        _insert_dir(self.conn, str(stale.as_posix()))
        cands = ca.select_archive_candidates(self.conn, self.root)
        self.assertEqual(len(cands), 1)
        ca.apply_archive(self.conn, cands)
        rows = {r["abs_path"]: r["lifecycle"]
                for r in self.conn.execute("SELECT abs_path, lifecycle FROM worker_dirs")}
        self.assertEqual(rows[str(fresh.as_posix())], "active")
        archived = [path for path, lc in rows.items() if lc == "archived"]
        self.assertEqual(len(archived), 1)

    def test_age_days_threshold_override(self):
        p = self.root / "claude-org-ja" / "_runs" / "ws" / "twoweeks"; p.mkdir(parents=True)
        _set_mtime(p, days_ago=15)
        _insert_dir(self.conn, str(p.as_posix()))
        self.assertEqual(ca.select_archive_candidates(self.conn, self.root, age_days=90), [])
        self.assertEqual(
            len(ca.select_archive_candidates(self.conn, self.root, age_days=10)), 1)

    def test_exdev_fallback_uses_copytree(self):
        """EXDEV from os.rename → copytree+rmtree (NOT shutil.move which would
        silently nest under an existing target)."""
        p = self.root / "claude-org-ja" / "_runs" / "ws" / "stale"
        p.mkdir(parents=True)
        (p / "marker.txt").write_text("hi", encoding="utf-8")
        _set_mtime(p, days_ago=200)
        _insert_dir(self.conn, str(p.as_posix()))

        real_rename = os.rename
        calls = {"n": 0}

        def fake_rename(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                err = OSError("synthetic cross-device")
                err.errno = _errno.EXDEV
                raise err
            return real_rename(src, dst)

        with mock.patch.object(ca.os, "rename", side_effect=fake_rename):
            cands = ca.select_archive_candidates(self.conn, self.root)
            n = ca.apply_archive(self.conn, cands)
        self.assertEqual(n, 1)
        self.assertFalse(p.exists())
        self.assertEqual(
            (Path(cands[0].target) / "marker.txt").read_text(encoding="utf-8"), "hi")

    def test_non_exdev_oserror_propagates(self):
        p = self.root / "claude-org-ja" / "_runs" / "ws" / "stale"
        p.mkdir(parents=True)
        _set_mtime(p, days_ago=200)
        _insert_dir(self.conn, str(p.as_posix()))

        def fake_rename(src, dst):
            err = OSError("permission denied")
            err.errno = _errno.EACCES
            raise err

        with mock.patch.object(ca.os, "rename", side_effect=fake_rename):
            cands = ca.select_archive_candidates(self.conn, self.root)
            with self.assertRaises(OSError):
                ca.apply_archive(self.conn, cands)

    def test_missing_dir_silently_skipped(self):
        """Row points to a non-existent path → not a candidate (no exception)."""
        _insert_dir(self.conn, str((self.root / "vanished").as_posix()))
        self.assertEqual(ca.select_archive_candidates(self.conn, self.root), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
