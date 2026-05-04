"""DB → markdown drift check (M4, Issue #267).

After M4 the DB is the SoT and ``.state/org-state.md`` is a fully
generator-owned dump (no passthrough escape hatch — free-form sections
live under ``notes/`` instead). ``drift_check`` answers: **does the
snapshotter, fed the current DB, produce the markdown file already on
disk byte-for-byte?** A non-empty diff means either someone hand-edited
the dump out of band, or the snapshotter has a bug.

* ``exit 0`` — no drift.
* ``exit 1`` — drift detected (unified diff printed to stdout).
* ``exit 2`` — tool failure (DB missing, IO error).
"""
from __future__ import annotations

import argparse
import difflib
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from . import connect
from .snapshotter import render_structured_markdown


def compute_diff(
    conn: sqlite3.Connection,
    actual_md_path: Path,
) -> str:
    """Return a unified diff (str). Empty string ⇒ no drift.

    M4 (Issue #267): the comparison is whole-file. There is no
    passthrough exclusion — every byte of ``.state/org-state.md`` must
    come from the DB.
    """
    actual_md_path = Path(actual_md_path)
    expected = render_structured_markdown(conn)
    actual = (
        actual_md_path.read_text(encoding="utf-8")
        if actual_md_path.exists() else ""
    )
    # Tolerate a single trailing-newline mismatch only — atomic writes
    # already produce stable trailing whitespace, so a difference here
    # would be a real divergence; this normalisation just prevents a
    # benign \n-or-no-\n at EOF from masquerading as drift.
    if expected.rstrip("\n") + "\n" == actual.rstrip("\n") + "\n":
        return ""
    diff = difflib.unified_diff(
        actual.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=str(actual_md_path),
        tofile="<expected from DB>",
        n=3,
    )
    return "".join(diff)


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tools.state_db.drift_check",
        description="Diff DB-derived org-state.md against the file on disk.",
    )
    p.add_argument("--db", required=True, type=Path,
                   help="Path to .state/state.db")
    p.add_argument("--markdown", required=True, type=Path,
                   help="Path to .state/org-state.md")
    args = p.parse_args(argv)
    if not args.db.exists():
        print(f"error: DB not found: {args.db}", file=sys.stderr)
        return 2
    conn = connect(args.db)
    try:
        diff = compute_diff(conn, args.markdown)
    finally:
        conn.close()
    if not diff:
        print("drift_check: no drift")
        return 0
    sys.stdout.write(diff)
    if not diff.endswith("\n"):
        sys.stdout.write("\n")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
