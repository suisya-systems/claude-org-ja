#!/usr/bin/env python3
"""Outbox relay scanner for the dispatcher's /loop 3m monitoring cycle.

CI-watch zero-miss (Refs #653 #658). ``events`` is the source of truth
for terminal signals (ci_completed / pr_merged / merge timeout / no-run /
head-unconfirmed / watcher abort / notify_failed). ``pr_watch`` writes
those rows locally; the low-latency peer push from the pr-watch pane is
best-effort and CAN silently no-op (the observed PR #73 failure: the pane
had no ORG_TRANSPORT/broker env, so the push never reached the queue and
the secretary sat idle). This tool is the reliable backstop: the
dispatcher (which holds a broker token) scans for terminal events that
have not yet been relayed to a recipient and hands each one back so the
dispatcher can ``send_message`` it, then records the delivery in the
``event_deliveries`` outbox ledger.

De-dup / exactly-once-relay is the ledger's ``UNIQUE (source_event_id,
recipient)`` idempotency key — NOT a send-side marker — so a lost push
can never leave a terminal event silently undelivered, and a redundant
relay (dispatcher crash between send and mark) is at worst a benign
duplicate the secretary handles idempotently (at-least-once).

Usage (driven from the dispatcher monitoring loop; see
``.dispatcher/references/worker-monitoring.md``):

    # 1. list undelivered terminal events (records a relay attempt each):
    python -m tools.relay_scan --recipient secretary --list

    # 2. for each item, dispatcher sends the `message` via send_message,
    #    then confirms the delivery so it is never relayed again:
    python -m tools.relay_scan --recipient secretary \
        --mark-delivered --source-event-id <id>

    # on a send failure, record it (retried next cycle, stays visible):
    python -m tools.relay_scan --recipient secretary \
        --mark-failed --source-event-id <id> --error "<reason>"

All CLI output strings use ASCII only so ``--help`` never crashes a
cp932 console (project Windows constraint).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Make ``tools.state_db.*`` importable when running this script directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.state_db import connect  # noqa: E402
from tools.state_db.discover import resolve_state_db_path  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402

# Terminal event kinds relayed to the secretary. These are the pr-watch
# terminal signals whose only prior delivery path was the best-effort
# peer push. NOT included: worker_escalation (relayed by the dispatcher's
# Step 5.1 SECRETARY_RELAY_GAP path, a separate owner — including it here
# would double-relay). notify_failed IS included: a failed push is itself
# a delivery gap the secretary must learn about (fail-loud end-to-end).
TERMINAL_KINDS = (
    "ci_completed",
    "pr_merged",
    "pr_merge_watch_timeout",
    "pr_merged_no_run",
    "pr_merged_head_unconfirmed",
    "pr_watch_aborted",
    "notify_failed",
)

# Default recipient for relays.
DEFAULT_RECIPIENT = "secretary"

# Default lookback window (hours). Bounds the scan cost and, critically,
# stops the FIRST post-deploy cycle from relaying the entire historical
# backlog of terminal events (all of which look "undelivered" before the
# ledger existed). Terminal events are acted on well within this window;
# a merge-watch itself is bounded at 24h.
DEFAULT_SINCE_HOURS = 72


def _iso_since(hours: float) -> Optional[str]:
    """Return an ISO-8601 UTC cutoff `hours` in the past, or None.

    ``hours <= 0`` disables the bound (unbounded scan). Computed in SQL
    (``strftime`` on ``now``) rather than Python so the cutoff matches
    the ``occurred_at`` format exactly and the tool stays free of the
    project's ``Date.now()`` concerns.
    """
    if hours <= 0:
        return None
    # Deterministic-format cutoff via a throwaway in-memory computation.
    conn = connect(":memory:")
    try:
        row = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now', ?) AS cutoff",
            (f"-{float(hours)} hours",),
        ).fetchone()
        return row["cutoff"] if row else None
    finally:
        conn.close()


def _payload(row) -> dict:
    try:
        data = json.loads(row["payload_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def compose_message(kind: str, payload: dict) -> str:
    """Build the relay text for a terminal event.

    Mirrors the peer-message forms the secretary's CI-detection step
    already recognizes (``CI_COMPLETED: PR #<n> ...`` etc.), with a
    trailing ``[relay]`` marker so a relayed copy is distinguishable from
    a direct push in the transcript. Unknown kinds degrade to a generic
    line rather than raising, so a new terminal kind is never dropped.
    """
    pr = payload.get("pr")
    pr_tag = f"PR #{pr}" if pr is not None else "PR #?"
    head = payload.get("head") or "unknown"
    if kind == "ci_completed":
        status = payload.get("status", "unknown")
        return f"CI_COMPLETED: {pr_tag} (status={status}, head={head}) [relay]"
    if kind == "pr_merged":
        return f"PR_MERGED: {pr_tag} (head={head}) [relay]"
    if kind == "pr_merge_watch_timeout":
        return f"PR_MERGE_WATCH_TIMEOUT: {pr_tag} (head={head}) [relay]"
    if kind == "pr_merged_no_run":
        return f"PR_MERGED_NO_RUN: {pr_tag} (head={head}) [relay]"
    if kind == "pr_merged_head_unconfirmed":
        baseline = payload.get("baseline_head") or "unknown"
        return (
            f"PR_MERGED_HEAD_UNCONFIRMED: {pr_tag} (head={head}, "
            f"last CI-confirmed head={baseline}) [relay]"
        )
    if kind == "pr_watch_aborted":
        err = payload.get("error", "unknown error")
        return f"PR_WATCH_ABORTED: {pr_tag} ({err}) [relay]"
    if kind == "notify_failed":
        orig = payload.get("failed_kind", "unknown")
        transport = payload.get("transport", "unknown")
        return (
            f"NOTIFY_FAILED: push of {orig} for {pr_tag} to "
            f"{payload.get('target', 'secretary')} failed "
            f"(transport={transport}) [relay]"
        )
    # Unknown terminal kind: relay a generic, non-lossy line.
    return f"{kind.upper()}: {pr_tag} [relay]"


def cmd_list(writer: StateWriter, conn, *, recipient: str,
             kinds: tuple[str, ...], since: Optional[str],
             limit: Optional[int]) -> list[dict]:
    """List undelivered terminal events and record a relay attempt each.

    Records ``begin_delivery_attempt`` for every surfaced event so the
    ledger tracks how many cycles it has been pending (observability) and
    an attempt is durable even if the dispatcher dies before sending.
    The matching ``mark-delivered`` (only after a confirmed send) is what
    makes delivery terminal — this ordering is what yields at-least-once.
    """
    rows = writer.pending_deliveries(
        recipient=recipient, kinds=list(kinds), since=since, limit=limit)
    out: list[dict] = []
    for row in rows:
        payload = _payload(row)
        attempt = writer.begin_delivery_attempt(
            source_event_id=int(row["id"]), recipient=recipient)
        out.append({
            "source_event_id": int(row["id"]),
            "kind": row["kind"],
            "occurred_at": row["occurred_at"],
            "recipient": recipient,
            "attempt": attempt,
            "message": compose_message(row["kind"], payload),
            "payload": payload,
        })
    conn.commit()
    return out


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tools.relay_scan",
        description=(
            "Scan the events table for terminal signals not yet relayed "
            "to a recipient and record deliveries in the event_deliveries "
            "outbox ledger (CI-watch zero-miss, Refs #653 #658)."
        ),
    )
    p.add_argument("--db", type=Path, default=None,
                   help="path to .state/state.db (default: auto-resolve)")
    p.add_argument("--recipient", default=DEFAULT_RECIPIENT,
                   help="delivery recipient id (default: secretary)")
    p.add_argument("--kinds", default=None,
                   help="comma-separated terminal kinds to scan "
                        "(default: the built-in terminal set)")
    p.add_argument("--since-hours", type=float, default=DEFAULT_SINCE_HOURS,
                   help="lookback window in hours; <=0 disables the bound "
                        f"(default: {DEFAULT_SINCE_HOURS})")
    p.add_argument("--limit", type=int, default=None,
                   help="cap the number of events returned by --list")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true",
                        help="list undelivered terminal events as JSON "
                             "(records a relay attempt for each)")
    action.add_argument("--mark-delivered", action="store_true",
                        help="mark --source-event-id delivered to --recipient")
    action.add_argument("--mark-failed", action="store_true",
                        help="record a retryable delivery failure")
    p.add_argument("--source-event-id", type=int, default=None,
                   help="events.id for --mark-delivered / --mark-failed")
    p.add_argument("--error", default="",
                   help="failure reason for --mark-failed")
    args = p.parse_args(argv)

    if (args.mark_delivered or args.mark_failed) and args.source_event_id is None:
        print("error: --source-event-id is required for "
              "--mark-delivered / --mark-failed", file=sys.stderr)
        return 2

    db_path = resolve_state_db_path(args.db)
    if not Path(db_path).exists():
        # No DB yet means nothing has ever been recorded — an empty relay
        # set, not an error (the dispatcher should proceed quietly).
        if args.list:
            print("[]")
        return 0

    kinds = (tuple(k.strip() for k in args.kinds.split(",") if k.strip())
             if args.kinds else TERMINAL_KINDS)
    since = _iso_since(args.since_hours)

    conn = connect(db_path)
    try:
        writer = StateWriter(conn)
        if args.list:
            items = cmd_list(writer, conn, recipient=args.recipient,
                             kinds=kinds, since=since, limit=args.limit)
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        if args.mark_delivered:
            writer.mark_delivered(source_event_id=args.source_event_id,
                                  recipient=args.recipient)
            conn.commit()
            print(f"delivered: event {args.source_event_id} -> "
                  f"{args.recipient}")
            return 0
        if args.mark_failed:
            writer.mark_delivery_failed(
                source_event_id=args.source_event_id,
                recipient=args.recipient,
                error=args.error or "unspecified")
            conn.commit()
            print(f"failed: event {args.source_event_id} -> "
                  f"{args.recipient} ({args.error or 'unspecified'})")
            return 0
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
