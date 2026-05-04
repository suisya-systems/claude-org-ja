#!/usr/bin/env python3
"""claude-org-ja journal append wrapper (M4 DB-only, Issue #267).

CLI shape kept compatible with pre-M4 wrappers so existing hooks /
SKILL.md references keep working::

    py -3 tools/journal_append.py <event> --json '{"k": 1, "nested": {...}}'
    py -3 tools/journal_append.py <event> k=v k2=v2

M4 (Issue #267): the canonical (and only) write target is the SQLite DB
at ``<repo_root>/.state/state.db`` via
:class:`tools.state_db.writer.StateWriter`. ``.state/journal.jsonl``
is decommissioned — the ``events`` table is the SoT for events. The DB
schema is auto-created if the file is absent so a fresh clone or CI run
without a prior importer pass still works. Legacy ``--path`` /
``$JOURNAL_PATH`` overrides remain rejected at this ja boundary.

Failure handling: any exception from the DB write path propagates to
the caller as exit-code 1 with a stderr message. There is no
file-append fallback in M4 — silently shadowing failures with a legacy
jsonl write would let writes diverge from the SoT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make `tools.state_db.*` importable when this script is invoked
# directly (e.g. `py -3 tools/journal_append.py …`) without a prior
# `pip install -e .`. The repo root is the directory above this file.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _parse_kv(pair: str) -> "tuple[str, str]":
    if "=" not in pair:
        raise argparse.ArgumentTypeError(
            f"malformed field {pair!r} (want key=value)"
        )
    key, _, val = pair.partition("=")
    if not key:
        raise argparse.ArgumentTypeError(f"empty key in {pair!r}")
    return key, val


def _db_append(repo_root: Path, event: str, payload: dict) -> None:
    """M4 canonical path: DB write only (no jsonl side-output)."""
    from tools.state_db import apply_schema, connect
    from tools.state_db.writer import StateWriter

    db_path = repo_root / ".state" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_db = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new_db:
            apply_schema(conn)
        writer = StateWriter(conn)
        actor = None
        if isinstance(payload, dict) and isinstance(payload.get("actor"), str):
            actor = payload["actor"]
        writer.append_event(kind=event, actor=actor, payload=payload)
        writer.commit()
    finally:
        conn.close()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/journal_append.py",
        description="Append an event to .state/state.db (M4: DB-only).",
    )
    parser.add_argument("event", help="event name (free-form string)")
    parser.add_argument(
        "fields",
        nargs="*",
        type=_parse_kv,
        help="zero or more key=value pairs (string-typed)",
    )
    parser.add_argument(
        "--json",
        dest="json_payload",
        default=None,
        help="JSON object to merge into the payload (typed values).",
    )
    repo_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--path", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.path is not None:
        sys.stderr.write(
            "tools/journal_append.py: warning: --path override rejected at "
            "ja boundary; M4 writes go to .state/state.db only.\n"
        )
    if "JOURNAL_PATH" in os.environ:
        sys.stderr.write(
            "tools/journal_append.py: warning: $JOURNAL_PATH override "
            "rejected at ja boundary; M4 writes go to .state/state.db only.\n"
        )

    payload: "dict[str, object]" = {}
    for key, val in args.fields:
        payload[key] = val
    if args.json_payload is not None:
        try:
            extra = json.loads(args.json_payload)
        except json.JSONDecodeError as exc:
            parser.error(f"--json must be valid JSON: {exc}")
        if not isinstance(extra, dict):
            parser.error("--json must encode a JSON object")
        payload.update(extra)

    try:
        _db_append(repo_root, args.event, payload)
        return 0
    except Exception as exc:
        sys.stderr.write(
            "tools/journal_append.py: DB write failed "
            f"({type(exc).__name__}: {exc}); event NOT recorded.\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
