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


@contextmanager
def with_db(db_path: PathLike) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


__all__ = ["connect", "apply_schema", "with_db", "SCHEMA_PATH"]
