"""Curator batch: hot → cold archive of stale worker dirs (M3, Issue #267).

CLI:
    python -m tools.state_db.curator_archive --dry-run [--db <path>] [--workers-root <path>]
    python -m tools.state_db.curator_archive --apply   [--db <path>] [--workers-root <path>]
    python -m tools.state_db.curator_archive --purge   [--db <path>]

Behavior (directory-layout.md §2 N2 / §5 H5):
  - lifecycle='active' rows whose dir mtime > 90 days are moved to
    `<workers_root>/_archive/<YYYY-Qx>/<original_project>/<workstream>/<run>/`
    and updated to lifecycle='archived', with abs_path rewritten in the DB.
  - lifecycle='delete_pending' rows are physically removed by `--purge`
    (filesystem rmtree + DB row delete).

The archive target is derived from each row's *current* abs_path, which
in M3 is already the 3-tier `<project>/_runs/<workstream>/<run>/` shape.
For pre-M3 flat dirs the helper degrades to a single-tier archive
(`_archive/<YYYY-Qx>/<dirname>/`).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

from tools.state_db import connect


HOT_TO_COLD_AGE_DAYS = 90


# ---------------------------------------------------------------------------
# Quarter / target helpers
# ---------------------------------------------------------------------------


def archive_quarter(now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    q = (n.month - 1) // 3 + 1
    return f"{n.year}-Q{q}"


def derive_archive_target(
    abs_path: str,
    workers_root: Path,
    quarter: str,
) -> str:
    """Compute the target `_archive/<YYYY-Qx>/...` path for `abs_path`.

    If `abs_path` is `<workers_root>/<project>/_runs/<workstream>/<run>/`,
    the target preserves `<project>/<workstream>/<run>/` under `_archive/`.
    Otherwise (flat / unrecognised), the basename is used as the only segment.
    """
    src = PurePosixPath(Path(abs_path).as_posix())
    root = PurePosixPath(workers_root.as_posix())
    archive_root = root / "_archive" / quarter
    try:
        rel = src.relative_to(root)
    except ValueError:
        return str(archive_root / src.name)
    parts = rel.parts
    # Expected shape: <project>/_runs/<workstream>/<run>/
    if len(parts) >= 4 and parts[1] == "_runs":
        project, _runs, workstream, run = parts[0], parts[1], parts[2], parts[3]
        return str(archive_root / project / workstream / run)
    return str(archive_root / src.name)


def _dir_mtime(path: Path) -> Optional[datetime]:
    try:
        ts = path.stat().st_mtime
    except FileNotFoundError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Row selection
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    abs_path: str
    lifecycle: str
    target: str
    age_days: float


def select_archive_candidates(
    conn: sqlite3.Connection,
    workers_root: Path,
    *,
    now: Optional[datetime] = None,
    age_days: int = HOT_TO_COLD_AGE_DAYS,
) -> list[Candidate]:
    """active rows whose on-disk mtime is older than `age_days`."""
    n = now or datetime.now(timezone.utc)
    cutoff = n - timedelta(days=age_days)
    quarter = archive_quarter(n)
    rows = conn.execute(
        "SELECT abs_path FROM worker_dirs WHERE lifecycle = 'active'"
    ).fetchall()
    out: list[Candidate] = []
    for r in rows:
        path = Path(r["abs_path"]) if isinstance(r, sqlite3.Row) else Path(r[0])
        m = _dir_mtime(path)
        if m is None or m >= cutoff:
            continue
        target = derive_archive_target(str(path).replace("\\", "/"), workers_root, quarter)
        out.append(Candidate(
            abs_path=str(path).replace("\\", "/"),
            lifecycle="active",
            target=target,
            age_days=(n - m).total_seconds() / 86400,
        ))
    return out


def select_purge_candidates(conn: sqlite3.Connection) -> list[Candidate]:
    rows = conn.execute(
        "SELECT abs_path FROM worker_dirs WHERE lifecycle = 'delete_pending'"
    ).fetchall()
    out: list[Candidate] = []
    for r in rows:
        ap = r["abs_path"] if isinstance(r, sqlite3.Row) else r[0]
        out.append(Candidate(abs_path=ap, lifecycle="delete_pending", target="", age_days=0))
    return out


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_archive(conn: sqlite3.Connection, candidates: list[Candidate]) -> int:
    """Move dirs and update DB. Each candidate is its own transaction so a
    failed mv mid-batch leaves prior successes committed and the failing row
    untouched."""
    moved = 0
    for c in candidates:
        src = Path(c.abs_path)
        dst = Path(c.target)
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError:
            # cross-device fallback
            shutil.move(str(src), str(dst))
        try:
            conn.execute(
                "UPDATE worker_dirs SET abs_path = ?, lifecycle = 'archived', "
                "last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE abs_path = ?",
                (str(PurePosixPath(dst.as_posix())), c.abs_path),
            )
            conn.commit()
        except sqlite3.Error:
            # rollback FS to keep DB↔FS in sync
            os.rename(dst, src)
            conn.rollback()
            raise
        moved += 1
    return moved


def apply_purge(conn: sqlite3.Connection, candidates: list[Candidate]) -> int:
    purged = 0
    for c in candidates:
        p = Path(c.abs_path)
        if p.exists():
            shutil.rmtree(p, ignore_errors=False)
        conn.execute("DELETE FROM worker_dirs WHERE abs_path = ?", (c.abs_path,))
        conn.commit()
        purged += 1
    return purged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="curator_archive", description=__doc__.splitlines()[0])
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true", help="list candidates only")
    grp.add_argument("--apply", action="store_true", help="archive active rows older than 90d")
    grp.add_argument("--purge", action="store_true", help="physically delete delete_pending rows")
    p.add_argument("--db", type=Path, default=Path(".state/state.db"), help="state DB path")
    p.add_argument("--workers-root", type=Path,
                   default=Path("..").resolve(), help="../workers/ root")
    p.add_argument("--age-days", type=int, default=HOT_TO_COLD_AGE_DAYS)
    p.add_argument("--json", action="store_true", help="emit JSON to stdout (dry-run)")
    args = p.parse_args(argv)

    conn = connect(args.db)
    try:
        if args.purge:
            cands = select_purge_candidates(conn)
            if args.dry_run:  # unreachable due to mutex, here for readability
                pass
            n = apply_purge(conn, cands)
            print(f"purged {n} delete_pending rows")
            return 0

        cands = select_archive_candidates(
            conn, args.workers_root, age_days=args.age_days,
        )

        if args.dry_run:
            if args.json:
                print(json.dumps([c.__dict__ for c in cands], indent=2, ensure_ascii=False))
            else:
                print(f"# archive candidates (age > {args.age_days}d): {len(cands)}")
                for c in cands:
                    print(f"  {c.abs_path}  → {c.target}  (age={c.age_days:.0f}d)")
            return 0

        if args.apply:
            n = apply_archive(conn, cands)
            print(f"archived {n}/{len(cands)} dirs")
            return 0
    finally:
        conn.close()

    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
