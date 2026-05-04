"""DB → markdown drift check (M2, Issue #267).

After M2 the DB is the SoT and ``.state/org-state.md`` is a regenerated
dump. ``drift_check`` answers a single question: **does the snapshotter,
fed the current DB, produce the markdown file already on disk?** A
non-empty diff means either someone hand-edited the markdown out of band
or the snapshotter has a bug.

Important behavioural notes:

* Only the **structured** sections are diffed. Free-form ``## …`` sections
  (which the snapshotter passes through verbatim) are excluded so a
  curated note doesn't masquerade as drift.
* ``exit 0`` on no diff, ``exit 1`` on diff. Anything else is a tool
  failure (missing DB, IO error). Designed to be wired into CI later;
  not a blocking gate while the notes/ split (M4) is still pending.
"""
from __future__ import annotations

import argparse
import difflib
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from . import connect
from .snapshotter import (
    extract_unknown_sections,
    render_structured_markdown,
)


def _strip_passthrough(markdown_text: str) -> str:
    """Return the text minus the unknown ``## …`` sections.

    Equivalent to: take everything up to the first structured ``## …``
    heading, then keep only structured sections in order. We implement it
    by subtracting the set of passthrough sections from the original text.
    """
    passthrough = extract_unknown_sections(markdown_text)
    if not passthrough:
        return markdown_text
    # Remove each passthrough section block (one at a time, in order). The
    # passthrough block always begins on a ``## …`` line that exists
    # verbatim in markdown_text, so a literal substring split is exact.
    result = markdown_text
    # Split passthrough back into its constituent sections so we don't
    # accidentally remove an unrelated identical run of bytes.
    blocks: list[str] = []
    cur: list[str] = []
    for line in passthrough.splitlines(keepends=True):
        if line.startswith("## ") and cur:
            blocks.append("".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("".join(cur))
    for block in blocks:
        idx = result.find(block)
        if idx >= 0:
            result = result[:idx] + result[idx + len(block):]
    return result


def compute_diff(
    conn: sqlite3.Connection,
    actual_md_path: Path,
) -> str:
    """Return a unified diff (str). Empty string ⇒ no drift."""
    actual_md_path = Path(actual_md_path)
    expected = render_structured_markdown(conn)
    actual_full = (
        actual_md_path.read_text(encoding="utf-8")
        if actual_md_path.exists() else ""
    )
    actual_structured = _strip_passthrough(actual_full)
    # Removing a passthrough block from the middle of the file can leave
    # an extra blank line behind; normalise trailing whitespace before
    # comparing so a benign free-form append doesn't masquerade as drift.
    if expected.rstrip() + "\n" == actual_structured.rstrip() + "\n":
        return ""
    diff = difflib.unified_diff(
        actual_structured.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=str(actual_md_path) + " (structured slice)",
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
