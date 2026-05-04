"""claude-org state DB (M0 shadow mirror).

Public helpers:
- `connect(db_path)` opens a sqlite3.Connection with project PRAGMAs applied.
- `apply_schema(conn)` executes schema.sql against an empty DB.
- `with_db(db_path)` context manager wrapping the two for short-lived scripts.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

PathLike = Union[str, Path]

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: PathLike) -> sqlite3.Connection:
    """Open a connection with project-wide PRAGMAs applied.

    PRAGMA choices follow schema-proposal.md and migration-strategy.md:
    foreign_keys=ON (FK enforcement), journal_mode=WAL (concurrent read/write
    for dispatcher + dashboard), busy_timeout=5000ms (avoid spurious failures
    under WAL contention).
    """
    db_path = str(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    # journal_mode=WAL is a no-op on :memory:; ignore the resulting mode there.
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Execute schema.sql against `conn`."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Forward migration for pre-M2 DBs (Issue #267)
# ---------------------------------------------------------------------------

# Standalone DDL for the M2-introduced singleton table. Must stay in sync
# with the org_sessions block in schema.sql; on a fresh DB schema.sql wins,
# on an existing M0/M1 DB this script is what creates the table in place.
_M2_ORG_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS org_sessions (
  id                   INTEGER PRIMARY KEY CHECK (id = 1),
  status               TEXT NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE','SUSPENDED','IDLE')),
  started_at           TEXT,
  updated_at           TEXT,
  suspended_at         TEXT,
  resumed_at           TEXT,
  objective            TEXT,
  resume_instructions  TEXT,
  dispatcher_pane_id   TEXT,
  dispatcher_peer_id   TEXT,
  curator_pane_id      TEXT,
  curator_peer_id      TEXT,
  last_writer_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


def ensure_m2_schema(conn: sqlite3.Connection) -> bool:
    """Idempotently bring an M0 / M1 DB up to the M2 shape.

    Adds the ``org_sessions`` singleton table + a v2 ``schema_migrations``
    row when missing, then seeds the singleton row. Safe to call repeatedly
    and on freshly-applied schema (everything is ``CREATE TABLE IF NOT
    EXISTS`` / ``INSERT OR IGNORE``).

    Returns True if a migration step actually ran (table or migration row
    or singleton was missing), False if the DB was already at M2 shape.
    Callers can use the return value to decide whether to commit or to
    log a one-time "migrated" message.
    """
    changed = False
    had_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='org_sessions'"
    ).fetchone() is not None
    if not had_table:
        conn.executescript(_M2_ORG_SESSIONS_DDL)
        changed = True
    cur = conn.execute(
        "INSERT OR IGNORE INTO org_sessions (id, status, last_writer_at) "
        "VALUES (1, 'IDLE', strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
    )
    if cur.rowcount > 0:
        changed = True
    # Make sure schema_migrations is consistent. The table itself shipped
    # in M0, so it must already exist; tolerate a freshly applied schema
    # where v2 is already in place.
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, description) "
            "VALUES (2, 'M2: org_sessions singleton (Issue #267)')"
        )
        if cur.rowcount > 0:
            changed = True
    except sqlite3.OperationalError:
        # schema_migrations table absent (very old / corrupt DB) — leave it.
        pass
    return changed


__all__ = [
    "connect", "apply_schema", "with_db", "ensure_m2_schema", "SCHEMA_PATH",
]


@contextmanager
def with_db(db_path: PathLike) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


