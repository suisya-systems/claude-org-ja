#!/usr/bin/env python3
"""claude-org-ja journal append wrapper (Step D shim).

Thin CLI around :class:`core_harness.audit.Journal` with the org-specific
journal path (``.state/journal.jsonl``) baked in. Use this when payload
values need types beyond string (numbers, booleans, nested objects) or
when keys fall outside the bash helper's
``[A-Za-z_][A-Za-z0-9_]*`` constraint; otherwise prefer
``tools/journal_append.sh``.

Usage::

    py -3 tools/journal_append.py <event> --json '{"k": 1, "nested": {...}}'
    py -3 tools/journal_append.py <event> k=v k2=v2

The first form takes a JSON object on the command line and merges it
into the payload. The second form is the same string-typed key=value
shape as the bash helper. Both forms can be combined; explicit
``--json`` payload wins on key conflicts.

The journal path is fixed at ``<repo_root>/.state/journal.jsonl``
where ``<repo_root>`` is the directory one level above this script
(``tools/..``). This keeps writes anchored to the org journal
regardless of caller cwd (e.g. the dispatcher pane runs with
cwd=.dispatcher/).

Audit boundary (refs cross-review M3): the legacy ``--path`` CLI
argument and ``JOURNAL_PATH`` environment variable are rejected at
this ja boundary so off-canon writes can't be silently redirected. A
stderr warning is emitted if either is set; the canonical path is
used regardless.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core_harness.audit import Journal


def _parse_kv(pair: str) -> "tuple[str, str]":
    if "=" not in pair:
        raise argparse.ArgumentTypeError(
            f"malformed field {pair!r} (want key=value)"
        )
    key, _, val = pair.partition("=")
    if not key:
        raise argparse.ArgumentTypeError(f"empty key in {pair!r}")
    return key, val


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/journal_append.py",
        description="Append a JSON-Lines event to .state/journal.jsonl.",
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
    parser.add_argument(
        "--path",
        default=None,
        help=argparse.SUPPRESS,
    )
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
    args.path = str(canonical_path)

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

    Journal(Path(args.path)).append(args.event, **payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
