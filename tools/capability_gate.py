#!/usr/bin/env python3
"""Look up whether a renga 2.0 capability gate has already been passed.

Backing the operational gate of the backend contract's T-Sec ratification
node (`docs/contracts/backend-interface-contract.md`, "Three gates apply").
The harness-side procedure that consumes this tool is
`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`.

Why a script and not an inline `python3 -c`:

1. **cwd independence.** The dispatcher runs with cwd `.dispatcher/`, so an
   inline `from tools.state_db import connect` raises ModuleNotFoundError
   there - the repo root is not on `sys.path`. This file anchors the repo
   root off ``__file__`` (same pattern as ``tools/journal_append.py``), so
   every role gets the same answer from whatever cwd it runs in.
2. **Exact-match lookup.** The lookup MUST match ``$.kind`` exactly. A
   substring test over the whole payload (``payload_json LIKE '%<gate>%'``)
   also matches a *different* event whose free-text ``note`` merely mentions
   the gate name, which silently marks the gate passed forever.

Gate names (the ``kind=`` value recorded inside a ``notify_sent`` event):

Messaging gates (contract T-Sec ratification node, "Three gates apply").
These keep their historical ``capability_``-prefixed recorded kinds and a
stripped short name.

  first_drive             ``capability_first_drive``
      Operational gate. The first drive of a capability-advertising backend
      was reported to a human and confirmed.
  first_drive_pending     ``capability_first_drive_pending``
      Not an approval. Records that a non-interactive call site *observed* a
      capability-advertising enumeration and filed the mandatory report; it
      exists so that report is filed once rather than every cycle.
  production_activation   ``capability_production_activation``
      Production-activation gate. A 2.0-server x 2.0-mcp-peer dogfood was run
      and a human confirmed leaving the capability branch enabled.

Pane-control ladder rungs (contract T-Sec ratification-pc). These six names are
fixed by the contract as the record identifiers themselves, so the short name and
the recorded ``kind`` are identical. A messaging gate record MUST NOT be read as
evidence about pane control, and vice versa: the two families are held apart here
for that reason. None of these rungs has been walked, so each reports
``not_recorded`` - which is the truthful, fail-closed answer, not permission.

  pane_control_canary_a_ro            read-only cross-tab inspect canary
  pane_control_canary_a_mut           state-changing cross-tab keystroke canary
  pane_control_canary_a_ui            cross-tab focus canary; its expected
      discharge is a recorded *exclusion* rather than an exercise
  pane_control_canary_b               set_pane_identity then close_pane, on two
      separate disposable panes
  pane_control_production_activation  leaving cross-tab pane control enabled
      unattended
  placement_production_activation     relief from the same-tab placement rule;
      additionally requires all four operational-readiness conditions of the
      contract's T-Sec 4.2-place, whose record shows one of the four satisfied

Each rung records only the exercise its own line names, and nothing wider. A
canary rung records that one hazard class was driven once under supervision; it
does **not** record that the class may run unattended, and it does not record
anything about the classes above or below it. The two ``production_activation``
rungs are held apart for the same reason: the pane-control one records nothing
about placement, and the placement one records nothing about the addressing
canaries, which stay unwalked either way.

Rung 1 of that ladder - ratification of the contract text - is deliberately NOT a
gate here. It is a decision about the document, and its canonical expression is
the amendment section's own ``Status:`` line. The journal does carry a record that
the decision was taken, under a name that is not one of the six; adding it to this
table would manufacture a queryable "gate" that a harness could misread as
clearing a rung, which the ladder's ordering rule forbids.

Exit codes (same shape as ``tools/check_renga_compat.py``):

  0 - recorded      the gate has a matching event; it has been passed
  1 - not_recorded  the DB was queried and holds no matching event
  2 - undetermined  the DB could not be queried (missing / unreadable)

``not_recorded`` and ``undetermined`` are both fail-safe branches - the
caller must treat each as "gate not passed" - but they are reported
distinguishably on purpose: only the first is evidence about the
organisation's history, and only the second is a reason to re-probe. This
mirrors the contract's own rule that a determined-empty result and an
undetermined result MUST NOT be conflated.

Usage:
  python3 tools/capability_gate.py                        # all gates, exit 0
  python3 tools/capability_gate.py --gate first_drive     # one gate + status exit
  python3 tools/capability_gate.py --gate first_drive --json
  python3 tools/capability_gate.py --gate placement_production_activation
  python3 ../tools/capability_gate.py --gate first_drive  # from .dispatcher/
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Make `tools.state_db.*` importable when this script is invoked directly
# (`python3 tools/capability_gate.py`, or `python3 ../tools/capability_gate.py`
# from the dispatcher's cwd) without a prior `pip install -e .`. The repo root
# is the directory above this file. Same anchor as tools/journal_append.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.state_db.discover import resolve_state_db_path  # noqa: E402

# Gate short name -> the `kind=` value carried inside the notify_sent payload.
# The three messaging gates keep their historical `capability_`-prefixed kinds and a
# stripped short name. The six pane-control ladder rungs are identity-mapped (short
# name == recorded kind) because the contract fixes those strings as the record
# identifiers themselves - see the docstring above.
GATES: "dict[str, str]" = {
    # Messaging gates (contract T-Sec ratification node, "Three gates apply").
    "first_drive": "capability_first_drive",
    "first_drive_pending": "capability_first_drive_pending",
    "production_activation": "capability_production_activation",
    # Pane-control activation ladder (contract T-Sec ratification-pc). None walked.
    "pane_control_canary_a_ro": "pane_control_canary_a_ro",
    "pane_control_canary_a_mut": "pane_control_canary_a_mut",
    "pane_control_canary_a_ui": "pane_control_canary_a_ui",
    "pane_control_canary_b": "pane_control_canary_b",
    "pane_control_production_activation": "pane_control_production_activation",
    "placement_production_activation": "placement_production_activation",
}

# Status tokens printed on stdout, and their exit codes.
RECORDED = "recorded"
NOT_RECORDED = "not_recorded"
UNDETERMINED = "undetermined"

EXIT_CODES = {RECORDED: 0, NOT_RECORDED: 1, UNDETERMINED: 2}

# The recorded event is always a `notify_sent` whose payload carries the gate
# name under `kind`. `json_extract` (JSON1) is guaranteed available: the
# events table already declares CHECK (json_valid(payload_json)) in
# tools/state_db/schema.sql, so a build without JSON1 could not create it.
_QUERY = (
    "SELECT 1 FROM events "
    "WHERE kind = 'notify_sent' "
    "AND json_extract(payload_json, '$.kind') = ? "
    "LIMIT 1"
)


def lookup(payload_kind: str, db_path: "str | Path | None" = None) -> "tuple[str, str]":
    """Return ``(status, detail)`` for one gate.

    ``status`` is one of RECORDED / NOT_RECORDED / UNDETERMINED. ``detail`` is
    a short ASCII reason, non-empty only when the answer is not a clean
    determination.

    Every failure path resolves to UNDETERMINED rather than raising, because
    the caller is a fail-safe gate: an exception escaping here would either
    crash an operating role or - worse, if swallowed by the caller - read as
    "no record found, carry on".
    """
    try:
        resolved = Path(resolve_state_db_path(db_path))
    except Exception as exc:  # pragma: no cover - discovery is defensive
        return UNDETERMINED, f"path resolution failed: {exc}"

    if not resolved.exists():
        # Deliberately NOT reported as not_recorded: an absent DB is a broken
        # environment, not evidence that the gate was never passed.
        return UNDETERMINED, f"state db not found: {resolved}"

    # Two open attempts, in this order:
    #
    # 1. read-only URI. Cleanest: it can never materialise a file, so a
    #    mis-resolved path stays an error instead of becoming an empty DB
    #    that truthfully answers "no rows".
    # 2. plain read-write open, only because we already established the file
    #    EXISTS above. This is the fallback that matters in practice: the DB
    #    is in WAL mode, and a read-only open of a WAL database has to create
    #    the `-shm` sidecar. Org roles run Bash inside a sandbox, where that
    #    write is denied and step 1 fails with "unable to open database
    #    file" even though the caller can read the DB perfectly well.
    #
    # `?immutable=1` would dodge the sidecar without any write, but it is the
    # wrong tool here: it tells SQLite to ignore the `-wal` file, so a gate
    # recorded moments ago and not yet checkpointed would read as absent.
    last_error: "str | None" = None
    for opener in (
        lambda: sqlite3.connect(f"file:{resolved}?mode=ro", uri=True),
        lambda: sqlite3.connect(str(resolved)),
    ):
        try:
            conn = opener()
        except sqlite3.Error as exc:
            last_error = f"cannot open {resolved}: {exc}"
            continue
        try:
            row = conn.execute(_QUERY, (payload_kind,)).fetchone()
        except sqlite3.OperationalError as exc:
            # A missing `events` table is a schema problem, not an access
            # problem - retrying with wider permissions cannot fix it.
            if "no such table" in str(exc):
                return UNDETERMINED, f"query failed: {exc}"
            last_error = f"query failed: {exc}"
            continue
        except sqlite3.Error as exc:
            last_error = f"query failed: {exc}"
            continue
        finally:
            conn.close()
        return (RECORDED, "") if row else (NOT_RECORDED, "")

    return UNDETERMINED, last_error or "unreadable"


def _build_parser() -> argparse.ArgumentParser:
    # NOTE: help strings stay ASCII-only. A cp932 console raises
    # UnicodeEncodeError on --help when they are not, and pytest's
    # redirect_stdout does not reproduce it.
    parser = argparse.ArgumentParser(
        description=(
            "Report whether a renga 2.0 capability gate has already been "
            "passed, by looking for its notify_sent record in the events table."
        ),
    )
    parser.add_argument(
        "--gate",
        choices=sorted(GATES),
        help=(
            "Gate to look up. Prints one status token and exits "
            "0=recorded / 1=not_recorded / 2=undetermined. Omit to print all "
            "gates and exit 0."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Explicit state.db path (overrides $STATE_DB_PATH and discovery).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of plain text.",
    )
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.gate:
        status, detail = lookup(GATES[args.gate], args.db_path)
        if args.json:
            print(json.dumps(
                {"gate": args.gate, "kind": GATES[args.gate],
                 "status": status, "detail": detail},
                ensure_ascii=False,
            ))
        else:
            print(status)
            if detail:
                print(detail, file=sys.stderr)
        return EXIT_CODES[status]

    results = {
        name: dict(zip(("status", "detail"), lookup(kind, args.db_path)))
        for name, kind in GATES.items()
    }
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for name in sorted(results):
            entry = results[name]
            line = f"{name}: {entry['status']}"
            if entry["detail"]:
                line += f" ({entry['detail']})"
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
