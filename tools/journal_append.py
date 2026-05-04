#!/usr/bin/env python3
"""claude-org-ja journal append wrapper (M2 DB-write switch, Issue #267).

CLI shape kept compatible with the pre-M2 wrapper so existing hooks /
SKILL.md references keep working::

    py -3 tools/journal_append.py <event> --json '{"k": 1, "nested": {...}}'
    py -3 tools/journal_append.py <event> k=v k2=v2

Behaviour change in M2: the canonical write target is the SQLite DB at
``<repo_root>/.state/state.db`` (via :class:`tools.state_db.writer.StateWriter`);
``.state/journal.jsonl`` is then regenerated from the events table by
:mod:`tools.state_db.snapshotter`. The DB schema is auto-created if the
file is absent so a fresh clone or CI run without a prior importer pass
still works. Legacy ``--path`` / ``$JOURNAL_PATH`` overrides remain
rejected at this ja boundary.

Fallback: if the DB write path raises (e.g. sqlite3 unavailable, the
schema can't be created), we degrade to the legacy file-append via
``core_harness.audit.Journal`` so downstream automation stays unblocked
and the failure surfaces as a stderr warning instead of a hook abort.
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


def _legacy_append(journal_path: Path, event: str, payload: dict) -> None:
    """Pre-M2 file-append path. Used as a last-resort fallback only."""
    from core_harness.audit import Journal
    Journal(journal_path).append(event, **payload)


def _db_append(repo_root: Path, journal_path: Path, event: str,
                payload: dict) -> None:
    """M2 canonical path: DB write → jsonl regenerate."""
    from tools.state_db import apply_schema, connect
    from tools.state_db.snapshotter import regenerate_journal_jsonl
    from tools.state_db.writer import StateWriter

    db_path = repo_root / ".state" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_db = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new_db:
            apply_schema(conn)
        writer = StateWriter(conn)
        # ``actor`` is conventionally an explicit payload field on the
        # legacy wire format; promote it so the events table has the
        # column populated even without per-call API change.
        actor = None
        if isinstance(payload, dict) and isinstance(payload.get("actor"),
                                                     str):
            actor = payload["actor"]
        writer.append_event(kind=event, actor=actor, payload=payload)
        writer.commit()
        regenerate_journal_jsonl(conn, journal_path)
    finally:
        conn.close()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/journal_append.py",
        description="Append a JSON-Lines event (M2: DB-first, jsonl regenerated).",
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
    canonical_path = repo_root / ".state" / "journal.jsonl"
    parser.add_argument("--path", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.path is not None:
        sys.stderr.write(
            "tools/journal_append.py: warning: --path override rejected at "
            "ja boundary; writing to canonical "
            f"{canonical_path}\n"
        )
    if "JOURNAL_PATH" in os.environ:
        sys.stderr.write(
            "tools/journal_append.py: warning: $JOURNAL_PATH override "
            "rejected at ja boundary; writing to canonical "
            f"{canonical_path}\n"
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
        _db_append(repo_root, canonical_path, args.event, payload)
        return 0
    except Exception as exc:
        # Don't let a DB-side hiccup take down the dispatcher hook —
        # warn loudly and degrade to the legacy file append.
        sys.stderr.write(
            "tools/journal_append.py: DB-write path failed "
            f"({type(exc).__name__}: {exc}); falling back to file append.\n"
        )
        try:
            _legacy_append(canonical_path, args.event, payload)
            return 0
        except Exception as exc2:
            sys.stderr.write(
                "tools/journal_append.py: legacy fallback also failed: "
                f"{type(exc2).__name__}: {exc2}\n"
            )
            return 1


if __name__ == "__main__":
    sys.exit(main())
