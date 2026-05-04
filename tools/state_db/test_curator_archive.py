"""Unit tests for tools.state_db.curator_archive (M3, Issue #267)."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.state_db import apply_schema, connect
from tools.state_db import curator_archive as ca


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_db(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    apply_schema(conn)
    return conn


def _insert_dir(conn: sqlite3.Connection, abs_path: str, lifecycle: str = "active") -> None:
    conn.execute(
        "INSERT INTO worker_dirs (abs_path, layout, lifecycle) VALUES (?, 'project_workstream', ?)",
        (abs_path, lifecycle),
    )
    conn.commit()


def _set_mtime(path: Path, days_ago: int) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# Quarter / target derivation
# ---------------------------------------------------------------------------


def test_archive_quarter():
    assert ca.archive_quarter(datetime(2026, 5, 4, tzinfo=timezone.utc)) == "2026-Q2"
    assert ca.archive_quarter(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-Q1"
    assert ca.archive_quarter(datetime(2026, 12, 31, tzinfo=timezone.utc)) == "2026-Q4"


def test_derive_archive_target_three_tier(tmp_path):
    root = tmp_path / "workers"
    src = root / "claude-org-ja" / "_runs" / "public-release" / "open-flip-plan"
    target = ca.derive_archive_target(str(src.as_posix()), root, "2026-Q2")
    assert target.endswith("/_archive/2026-Q2/claude-org-ja/public-release/open-flip-plan")


def test_derive_archive_target_flat_fallback(tmp_path):
    root = tmp_path / "workers"
    src = root / "legacy-flat-dir"
    target = ca.derive_archive_target(str(src.as_posix()), root, "2026-Q2")
    assert target.endswith("/_archive/2026-Q2/legacy-flat-dir")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_active_under_90d_is_noop(tmp_path):
    root = tmp_path / "workers"; root.mkdir()
    (root / "claude-org-ja" / "_runs" / "ws" / "fresh").mkdir(parents=True)
    p = root / "claude-org-ja" / "_runs" / "ws" / "fresh"
    _set_mtime(p, days_ago=10)
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(p.as_posix()))
    cands = ca.select_archive_candidates(conn, root)
    assert cands == []


def test_active_over_90d_is_candidate(tmp_path):
    root = tmp_path / "workers"; root.mkdir()
    p = root / "claude-org-ja" / "_runs" / "ws" / "stale"
    p.mkdir(parents=True)
    _set_mtime(p, days_ago=120)
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(p.as_posix()))
    cands = ca.select_archive_candidates(conn, root)
    assert len(cands) == 1
    assert cands[0].target.endswith("_archive/" + ca.archive_quarter() + "/claude-org-ja/ws/stale")


def test_apply_archive_moves_dir_and_updates_db(tmp_path):
    root = tmp_path / "workers"; root.mkdir()
    p = root / "claude-org-ja" / "_runs" / "ws" / "stale"
    p.mkdir(parents=True)
    (p / "marker.txt").write_text("hi", encoding="utf-8")
    _set_mtime(p, days_ago=200)
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(p.as_posix()))

    cands = ca.select_archive_candidates(conn, root)
    n = ca.apply_archive(conn, cands)
    assert n == 1
    assert not p.exists()
    new_path = Path(cands[0].target)
    assert (new_path / "marker.txt").read_text(encoding="utf-8") == "hi"
    row = conn.execute(
        "SELECT abs_path, lifecycle FROM worker_dirs"
    ).fetchone()
    assert row["lifecycle"] == "archived"
    assert row["abs_path"] == cands[0].target


def test_purge_deletes_delete_pending(tmp_path):
    root = tmp_path / "workers"; root.mkdir()
    p = root / "trash-dir"; p.mkdir()
    (p / "junk").write_text("x", encoding="utf-8")
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(p.as_posix()), lifecycle="delete_pending")
    cands = ca.select_purge_candidates(conn)
    assert len(cands) == 1
    n = ca.apply_purge(conn, cands)
    assert n == 1
    assert not p.exists()
    assert conn.execute("SELECT COUNT(*) FROM worker_dirs").fetchone()[0] == 0


def test_apply_archive_skips_active_under_age(tmp_path):
    root = tmp_path / "workers"; root.mkdir()
    fresh = root / "claude-org-ja" / "_runs" / "ws" / "fresh"; fresh.mkdir(parents=True)
    stale = root / "claude-org-ja" / "_runs" / "ws" / "stale"; stale.mkdir(parents=True)
    _set_mtime(fresh, days_ago=10)
    _set_mtime(stale, days_ago=100)
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(fresh.as_posix()))
    _insert_dir(conn, str(stale.as_posix()))

    cands = ca.select_archive_candidates(conn, root)
    assert len(cands) == 1
    ca.apply_archive(conn, cands)
    rows = {r["abs_path"]: r["lifecycle"] for r in conn.execute("SELECT abs_path, lifecycle FROM worker_dirs")}
    assert rows[str(fresh.as_posix())] == "active"
    archived = [path for path, lc in rows.items() if lc == "archived"]
    assert len(archived) == 1


def test_age_days_threshold_override(tmp_path):
    root = tmp_path / "workers"; root.mkdir()
    p = root / "claude-org-ja" / "_runs" / "ws" / "twoweeks"; p.mkdir(parents=True)
    _set_mtime(p, days_ago=15)
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(p.as_posix()))
    assert ca.select_archive_candidates(conn, root, age_days=90) == []
    assert len(ca.select_archive_candidates(conn, root, age_days=10)) == 1


def test_exdev_fallback_uses_copytree(tmp_path, monkeypatch):
    """When os.rename raises EXDEV, the archive must fall back to copytree+rmtree
    (not shutil.move, which would silently nest under an existing target)."""
    import errno as _errno
    root = tmp_path / "workers"; root.mkdir()
    p = root / "claude-org-ja" / "_runs" / "ws" / "stale"; p.mkdir(parents=True)
    (p / "marker.txt").write_text("hi", encoding="utf-8")
    _set_mtime(p, days_ago=200)
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(p.as_posix()))

    real_rename = os.rename
    calls = {"n": 0}

    def fake_rename(src, dst):
        # Fail the forward archive rename once with EXDEV; let any subsequent
        # rollback rename succeed normally.
        calls["n"] += 1
        if calls["n"] == 1:
            err = OSError("synthetic cross-device")
            err.errno = _errno.EXDEV
            raise err
        return real_rename(src, dst)

    monkeypatch.setattr(ca.os, "rename", fake_rename)
    cands = ca.select_archive_candidates(conn, root)
    n = ca.apply_archive(conn, cands)
    assert n == 1
    assert not p.exists()
    assert (Path(cands[0].target) / "marker.txt").read_text(encoding="utf-8") == "hi"


def test_non_exdev_oserror_propagates(tmp_path, monkeypatch):
    """Non-EXDEV OSError must NOT be swallowed by the cross-device fallback."""
    import errno as _errno
    root = tmp_path / "workers"; root.mkdir()
    p = root / "claude-org-ja" / "_runs" / "ws" / "stale"; p.mkdir(parents=True)
    _set_mtime(p, days_ago=200)
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str(p.as_posix()))

    def fake_rename(src, dst):
        err = OSError("permission denied")
        err.errno = _errno.EACCES
        raise err

    monkeypatch.setattr(ca.os, "rename", fake_rename)
    cands = ca.select_archive_candidates(conn, root)
    with pytest.raises(OSError):
        ca.apply_archive(conn, cands)


def test_missing_dir_silently_skipped(tmp_path):
    """Row points to a non-existent path → not a candidate (no exception)."""
    root = tmp_path / "workers"; root.mkdir()
    conn = _seed_db(tmp_path / "state.db")
    _insert_dir(conn, str((root / "vanished").as_posix()))
    assert ca.select_archive_candidates(conn, root) == []
