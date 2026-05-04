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

# Standalone DDL for the M2-introduced singleton table.
#
# **Must stay in sync with the org_sessions block in schema.sql.**
# schema.sql is the SoT for fresh-DB construction; this constant is the
# only path used to add the table to an *existing* M0 / M1 DB without
# wiping the rest of its contents. Column list, types, CHECKs and DEFAULT
# clauses must match exactly. ``test_writer.test_ddl_sync_with_schema_sql``
# asserts the column shape stays in lockstep so an accidental drift
# breaks tests instead of silently producing two divergent schemas.
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


def _conn_in_transaction(conn: sqlite3.Connection) -> bool:
    return bool(getattr(conn, "in_transaction", False))


def ensure_m2_schema(conn: sqlite3.Connection) -> bool:
    """Idempotently bring an M0 / M1 DB up to the M2 shape.

    Adds the ``org_sessions`` singleton table + a v2 ``schema_migrations``
    row when missing, then seeds the singleton row. Safe to call repeatedly
    and on freshly-applied schema (everything is ``CREATE TABLE IF NOT
    EXISTS`` / ``INSERT OR IGNORE``).

    **Must be called outside an open transaction** (cross-review N1). The
    Python ``sqlite3`` module's ``Connection.executescript`` issues an
    implicit ``COMMIT`` before running its statements, which would silently
    commit any uncommitted INSERT/UPDATE the caller is in the middle of —
    the subsequent ROLLBACK would then be a no-op and that work would
    survive against the caller's expectation. We fail fast with
    ``RuntimeError`` instead of warning (warnings are easy to miss and the
    side-effect is data-shaped, not lint-shaped).

    Returns True if a migration step actually ran, False if the DB was
    already at M2 shape.
    """
    if _conn_in_transaction(conn):
        raise RuntimeError(
            "ensure_m2_schema must be called outside an active "
            "transaction; got conn.in_transaction=True. "
            "sqlite3.Connection.executescript() issues an implicit "
            "COMMIT before running, which would silently commit any "
            "pending writes from the caller. COMMIT or ROLLBACK first, "
            "or construct the StateWriter before opening the transaction."
        )

    changed = False
    had_table = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='org_sessions'"
    ).fetchone() is not None
    if not had_table:
        conn.executescript(_M2_ORG_SESSIONS_DDL)
        changed = True
    cur = conn.execute(
        "INSERT OR IGNORE INTO org_sessions "
        "(id, status, last_writer_at) "
        "VALUES (1, 'IDLE', strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
    )
    if cur.rowcount > 0:
        changed = True
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO schema_migrations "
            "(version, description) "
            "VALUES (2, 'M2: org_sessions singleton (Issue #267)')"
        )
        if cur.rowcount > 0:
            changed = True
    except sqlite3.OperationalError:
        # schema_migrations table absent (very old / corrupt DB).
        pass
    return changed


@contextmanager
def with_db(db_path: PathLike) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Cross-review Nit 1: ``__all__`` lives at module bottom so every name it
# references is already bound at evaluation time — easier to scan than
# the previous "declared mid-file, with_db defined below" arrangement.
__all__ = [
    "SCHEMA_PATH",
    "apply_schema",
    "connect",
    "ensure_m2_schema",
    "with_db",
]
