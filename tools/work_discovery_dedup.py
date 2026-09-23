#!/usr/bin/env python3
"""Decide whether a worker_close triage scan result is worth forwarding.

Reads ``work_discovery_scan.py`` stdout JSON (exit 10 shape) on stdin,
builds the candidate ref set (``owner/repo#N``, sorted, comma-joined) and
compares it with ``candidate_refs`` of the last ``work_discovery_scanned``
event recorded with ``trigger=worker_close``.

Exit codes (the dispatcher branches on these, see
``.dispatcher/references/pane-close.md`` 6-2):
  0  changed   - forward to the secretary
  3  unchanged - same set as the last record; do not forward
  2  error     - stdin is not a scan JSON

stdout is one JSON line: ``{"candidate_refs": "...", "unchanged": bool}``.
An unreadable DB or a last record without ``candidate_refs`` counts as
changed: never suppress on uncertainty.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent.parent / ".state" / "state.db"


def candidate_refs(scan: dict) -> str:
    repos = (scan.get("repo_resolution") or {}).get("repos") or []
    fallback = repos[0] if repos else ""
    refs = {
        f"{c.get('repo') or fallback}#{c['issue']}"
        for c in scan.get("candidates") or []
    }
    return ",".join(sorted(refs))


def last_refs(db: Path) -> "str | None":
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT json_extract(payload_json, '$.candidate_refs') FROM events"
                " WHERE kind = 'work_discovery_scanned'"
                " AND json_extract(payload_json, '$.trigger') = 'worker_close'"
                " ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=_DEFAULT_DB)
    args = ap.parse_args(argv)
    try:
        scan = json.load(sys.stdin)
        refs = candidate_refs(scan)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"error": f"bad scan JSON: {exc}"}))
        return 2
    unchanged = bool(refs) and last_refs(args.db) == refs
    print(json.dumps({"candidate_refs": refs, "unchanged": unchanged}))
    return 3 if unchanged else 0


if __name__ == "__main__":
    sys.exit(main())
