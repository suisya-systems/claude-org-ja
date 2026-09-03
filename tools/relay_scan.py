#!/usr/bin/env python3
"""Outbox relay scanner for the dispatcher's /loop 3m monitoring cycle.

CI-watch zero-miss (Refs #653 #658). ``events`` is the source of truth
for terminal signals (ci_completed / pr_merged / merge timeout / no-run /
head-unconfirmed / watcher abort / notify_failed) plus the one
non-terminal signal that needs the same guarantee, ``pr_conflict_detected``
(Issue #946). ``pr_watch`` writes
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

    # 1. ONE command per monitoring cycle: self-check + scan together.
    #    Prints {"audit": {...}, "items": [...]}; exit code is the audit
    #    contract (0 healthy / 10 finding / 2 tool error).
    python -m tools.relay_scan --recipient secretary --cycle

    # 1'. the same two halves, still available separately (compat):
    python -m tools.relay_scan --recipient secretary --list

    # 2. for each item, dispatcher sends the `message` via send_message,
    #    then confirms the delivery so it is never relayed again:
    python -m tools.relay_scan --recipient secretary \
        --mark-delivered --source-event-id <id>

    # on a send failure, record it (retried next cycle, stays visible):
    python -m tools.relay_scan --recipient secretary \
        --mark-failed --source-event-id <id> --error "<reason>"

    # 3. machine-verify that step 1 is actually being run (Issue #941):
    python -m tools.relay_scan --recipient secretary --audit

    # 4. when the loop stops on purpose (Step 7), record that it did, so
    #    the first audit after resume does not report the gap as an outage:
    python -m tools.relay_scan --recipient secretary --mark-halted

Why ``--cycle`` exists
----------------------
``--audit`` and ``--list`` were two commands the runbook asked the
dispatcher to run every cycle, and it ran neither reliably: an observed
monitoring cycle issues roughly one shell command, and the two were
mutually self-referential -- ``--audit`` is read-only and never causes a
scan, while ``--list`` was reached only "when the audit says stale". On
2026-09-03 the loop was armed with the canonical ``/loop 3m`` directive
and the scan still stopped for 44.5 minutes with two undelivered events
behind it (Issues #955 / #971). That is a structural defect, not a prose
compliance one: the fix is to make the mandatory cycle-start command
exactly one, so running the cycle at all runs the scan. ``--cycle`` runs
the audit **first** (the heartbeat must be read before ``--list`` stamps
it) and then always runs the scan, whatever the audit said.

Why ``--audit`` exists
----------------------
The relay is only a zero-miss guarantee while the dispatcher actually
runs it. Between 2026-07-30 and 2026-08-19 it did not: the runbook spelled
the command ``python ...`` and the host has only ``python3``, so every
cycle died on ``command not found`` and produced no output, no ledger row,
and no error anyone read. 134 terminal events accumulated undelivered and
the gap was found only because a human eventually queried the ledger by
hand.

The structural problem is that a *silent no-op is indistinguishable from
a clean scan*: both leave nothing behind. ``--list`` writes ledger rows
only when something is pending, so "no rows" legitimately means "nothing
to relay" — it cannot also be made to mean "the scan ran". ``--audit``
closes that by giving the scan an unconditional trace (a heartbeat
written on every ``--list``, whether or not anything was pending) and a
one-command staleness check over it. A relay that stops running now
reports itself within one ``--stale-min`` window instead of after 20 days.

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
    # Issue #946. The only non-terminal kind here, and deliberately so:
    # a conflicting head means GitHub cannot build the merge ref, so no
    # workflow fires and the PR sits at zero checks until a human
    # rebases. It needs the same zero-miss guarantee as the terminal
    # signals, and the `notify_failed` backstop does NOT cover it — a
    # pane with no transport configured at all (the PR #73 env-injection
    # failure) records no `notify_failed` by design, which would leave
    # this canonical row as the only trace of the conflict and nothing
    # to relay it. Delivery is still once-only: the ledger dedups by
    # event id, and pr_watch emits at most one row per head.
    "pr_conflict_detected",
)

# Default recipient for relays.
DEFAULT_RECIPIENT = "secretary"

# Schema version whose migration timestamp defines the "ledger epoch"
# (when the outbox ledger first existed in this DB). See _ledger_epoch.
_LEDGER_SCHEMA_VERSION = 3


def _ledger_epoch(conn) -> Optional[str]:
    """Return the ISO-8601 UTC instant the outbox ledger came into being.

    The ``event_deliveries`` ledger landed as ``schema_migrations`` row
    ``version = 3``; that row's ``applied_at`` is stamped when the table
    was created in THIS DB (fresh ``apply_schema`` at DB birth, or the
    in-place ``ensure_event_deliveries_schema`` migration). It cleanly
    separates **pre-ledger history** (events emitted before the ledger,
    ``occurred_at < epoch`` — never to be relayed, the anti-flood floor)
    from **post-ledger events** (``occurred_at >= epoch`` — relay-eligible
    until delivered, and crucially NEVER aged out by wall-clock: an event
    emitted after the epoch stays eligible even if the dispatcher was down
    for weeks, closing the outage gap a moving ``now - N h`` window would
    open). Returns None if the migration row is absent (very old/corrupt
    DB) — the caller then falls back to an unbounded scan.
    """
    try:
        row = conn.execute(
            "SELECT applied_at FROM schema_migrations WHERE version = ?",
            (_LEDGER_SCHEMA_VERSION,),
        ).fetchone()
    except Exception:  # noqa: BLE001 — schema_migrations absent on a corrupt DB
        return None
    return row["applied_at"] if row and row["applied_at"] else None


def _iso_since(hours: float) -> Optional[str]:
    """Return an ISO-8601 UTC cutoff `hours` in the past, or None.

    ``hours <= 0`` disables the bound (unbounded scan). Computed in SQL
    (``strftime`` on ``now``) rather than Python so the cutoff matches
    the ``occurred_at`` format exactly and the tool stays free of the
    project's ``Date.now()`` concerns. This is the operator's explicit
    wall-clock override; the DEFAULT floor is the ledger epoch, not a
    moving window (see :func:`_ledger_epoch`).
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


# Rendered in place of a head the event does not carry, together with
# ``UNVERIFIABLE_RELAY_TAIL``. Issue #954: filling the gap with "unknown"
# produced a value that merely *mismatched* the watcher head, which the
# secretary's freshness gate is required to treat as a superseded event
# (skip the close) -- so a headless event silently disabled the watcher
# cleanup instead of reporting that it could not be matched at all.
HEAD_MISSING = "<missing>"
RELAY_TAIL = "[relay]"
UNVERIFIABLE_RELAY_TAIL = "[head-unverifiable] [relay]"

# Values that are present in the payload but carry no comparable SHA. The
# watcher itself writes the literal "unknown" when it cannot resolve a
# merged head (``tools/pr_watch.py``: ``head_tag = merged_head or "unknown"``
# on the pr_merged_no_run / pr_merged_head_unconfirmed path, and the same
# placeholder on pr_conflict_detected), so a truthiness test alone would
# let those through with a plain tail and land them back in the
# silent-mismatch branch this marker exists to avoid.
HEAD_PLACEHOLDERS = frozenset({"unknown", "none", "null", "-"})


def _gate_head(value: object) -> tuple:
    """Return ``(rendered, verifiable)`` for a head-ish payload field.

    ``verifiable`` is False when the field is absent, empty, or one of the
    known placeholders -- i.e. whenever the secretary's freshness gate has
    nothing it can legitimately compare against.
    """
    if not isinstance(value, str) or not value.strip():
        return HEAD_MISSING, False
    text = value.strip()
    if text.lower() in HEAD_PLACEHOLDERS:
        return HEAD_MISSING, False
    return text, True


def compose_message(kind: str, payload: dict) -> str:
    """Build the relay text for a terminal event.

    Mirrors the peer-message forms the secretary's CI-detection step
    already recognizes (``CI_COMPLETED: PR #<n> ...`` etc.), with a
    trailing ``[relay]`` marker so a relayed copy is distinguishable from
    a direct push in the transcript. Unknown kinds degrade to a generic
    line rather than raising, so a new terminal kind is never dropped.

    When the event carries no head to match the watcher against -- absent,
    empty, or a placeholder such as the watcher's own literal ``"unknown"``
    -- the head field renders as ``<missing>`` and the line gains a leading
    ``[head-unverifiable]`` marker ahead of ``[relay]`` (Issue #954).
    """
    pr = payload.get("pr")
    pr_tag = f"PR #{pr}" if pr is not None else "PR #?"
    # Issue #954: a missing head used to be filled in with "unknown".
    # The secretary's freshness gate matches the relayed head against the
    # head its live watcher instance is tracking (see the "freshness gate"
    # bullet in .claude/skills/org-pull-request/SKILL.md), so "unknown" is
    # structurally guaranteed to mismatch and the gate then silently
    # skipped the cleanup close, leaving a zombie watcher pane on the
    # herdr / wezterm backends (the Issue #751 re-entry path). Render an
    # explicit sentinel plus a trailing marker instead, so "this head
    # cannot be compared at all" is distinguishable from "the heads
    # differ" without changing the CI_COMPLETED / PR_MERGED message shape
    # the secretary skill parses.
    head, head_ok = _gate_head(payload.get("head"))
    tail = RELAY_TAIL if head_ok else UNVERIFIABLE_RELAY_TAIL
    if kind == "ci_completed":
        status = payload.get("status", "unknown")
        return f"CI_COMPLETED: {pr_tag} (status={status}, head={head}) {tail}"
    if kind == "pr_merged":
        return f"PR_MERGED: {pr_tag} (head={head}) {tail}"
    if kind == "pr_merge_watch_timeout":
        return f"PR_MERGE_WATCH_TIMEOUT: {pr_tag} (head={head}) {tail}"
    if kind == "pr_merged_no_run":
        return f"PR_MERGED_NO_RUN: {pr_tag} (head={head}) {tail}"
    if kind == "pr_merged_head_unconfirmed":
        # For this kind the gate compares the *baseline* (last CI-confirmed)
        # head, not `head` -- so that is the field whose absence makes the
        # event unmatchable.
        baseline, baseline_ok = _gate_head(payload.get("baseline_head"))
        baseline_tail = RELAY_TAIL if baseline_ok else UNVERIFIABLE_RELAY_TAIL
        return (
            f"PR_MERGED_HEAD_UNCONFIRMED: {pr_tag} (head={head}, "
            f"last CI-confirmed head={baseline}) {baseline_tail}"
        )
    if kind == "pr_conflict_detected":
        state = payload.get("merge_state_status") or "unknown"
        advice = ("CI 判定は出ていますが head が conflict のためマージ不可"
                  if payload.get("ci_settled")
                  else "conflict のため CI が発火しません")
        return (
            f"PR_CONFLICT: {pr_tag} (head={head}, mergeStateStatus={state})"
            f" - {advice} {tail}"
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


# ---------------------------------------------------------------------
# Execution-trace heartbeat (Issue #941)
# ---------------------------------------------------------------------
#
# Written on every --list, unconditionally. This is deliberately NOT an
# ``events`` row: the journal is an append-only record of things that
# happened to the org, and a /loop 3m cadence would add ~480 rows a day
# of pure liveness noise (the whole table held ~4.3k rows after months).
# A heartbeat only ever needs its latest value, so last-write-wins JSON
# next to the other dispatcher-local state files is the right shape.
_HEARTBEAT_FILENAME = "relay-scan-heartbeat.json"

# Default staleness bound for --audit. The dispatcher loop is /loop 3m,
# so 15 minutes is five missed cycles: long enough that a slow cycle or a
# transient never trips it, short enough that a dead relay surfaces the
# same working day rather than three weeks later.
DEFAULT_STALE_MIN = 15.0


def _heartbeat_path(db_path) -> Path:
    """Location of the heartbeat file (beside state.db, under dispatcher/)."""
    return Path(db_path).parent / "dispatcher" / _HEARTBEAT_FILENAME


def _now_iso(conn) -> str:
    """Current UTC instant in the same format as ``events.occurred_at``."""
    row = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now') AS now"
    ).fetchone()
    return row["now"]


def _read_heartbeat(db_path) -> dict:
    """Return the heartbeat map, or ``{}`` if absent / unreadable.

    An unreadable heartbeat is reported as "never scanned" rather than as
    an error: the audit's job is to notice a missing trace, and a corrupt
    file is a missing trace.
    """
    try:
        with open(_heartbeat_path(db_path), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — absent, unreadable, or malformed
        return {}


def write_heartbeat(db_path, *, recipient: str, surfaced: int,
                    scanned_at: str) -> bool:
    """Record that a scan ran. Best-effort; returns success.

    Keyed by recipient so scans for different recipients do not overwrite
    each other's trace. Failure to write is swallowed: the relay itself
    must never fail because its telemetry could not be persisted. The
    consequence of a swallowed failure is that ``--audit`` reports stale,
    which is the fail-loud direction.

    ``surfaced`` is how many events that scan returned, which is NOT the
    backlog size when ``--limit`` is in play. The authoritative backlog
    number is ``pending_now``, which ``--audit`` recomputes unlimited;
    this field is only a breadcrumb about the scan itself.
    """
    data = _read_heartbeat(db_path)
    # Whole-entry replacement, deliberately: it drops any ``halted_at``
    # left by a previous Step 7 stop. A scan that ran ends the halt.
    # (The audit that spends the exemption also clears it, so the
    # one-shot property does not depend on a scan following -- see
    # clear_halted.)
    data[recipient] = {"last_scan_at": scanned_at, "surfaced": surfaced}
    return _write_heartbeat_file(db_path, data)


def write_halted(db_path, *, recipient: str, halted_at: str) -> bool:
    """Record that the monitoring loop stopped on purpose (Issue #955).

    Step 7 of the dispatcher runbook halts the loop once no worker panes
    remain and the relay set is empty. Without a marker, that designed
    stop is indistinguishable from the loop having died: the next
    session's first ``--audit`` sees an hours-old heartbeat, and if any
    terminal event landed while the org was down it reports
    ``RELAY_SCAN_STALE`` on essentially every org start -- an alarm that
    fires when nothing is wrong is an alarm its reader learns to ignore.

    The marker preserves ``last_scan_at`` (the trace itself stays
    truthful) and only adds ``halted_at``. It suppresses exactly one
    audit, from both ends: the audit that uses it consumes it
    (:func:`clear_halted`) and a completed scan replaces the whole entry
    anyway, so the cycle after resume is judged normally again. A loop
    that dies *without* being marked halted is unaffected -- that is
    still the outage this tool exists to catch.
    """
    data = _read_heartbeat(db_path)
    entry = data.get(recipient)
    if not isinstance(entry, dict):
        entry = {}
    entry["halted_at"] = halted_at
    data[recipient] = entry
    return _write_heartbeat_file(db_path, data)


def clear_halted(db_path, *, recipient: str) -> bool:
    """Drop the halt marker, leaving the scan trace intact.

    Called by the audit that spends the exemption (Codex P1). Clearing it
    on the *next scan* alone was not enough to make the exemption
    one-shot: a caller that keeps running the compat ``--audit`` without
    ever completing a ``--list`` -- which is exactly the audit-only
    failure this monitor exists to catch -- would have kept the marker,
    and with it a permanent suppression of the stale-with-backlog
    finding. Consuming it where it is used bounds the exemption to one
    audit no matter which command path is in play.
    """
    data = _read_heartbeat(db_path)
    entry = data.get(recipient)
    if not isinstance(entry, dict) or "halted_at" not in entry:
        return True
    entry.pop("halted_at", None)
    data[recipient] = entry
    return _write_heartbeat_file(db_path, data)


def _write_heartbeat_file(db_path, data: dict) -> bool:
    """Atomically persist the heartbeat map. Best-effort; returns success."""
    path = _heartbeat_path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001 — read-only FS, permissions, etc.
        return False


def cmd_audit(writer: StateWriter, conn, db_path, *, recipient: str,
              kinds: tuple, since: Optional[str],
              stale_min: float) -> tuple:
    """Report whether the relay scan is actually running. Returns (dict, exit).

    Exit codes mirror ``tools/spawn_gate.py audit``, the dispatcher's
    other cycle-start self-check, so both read the same way from the
    runbook: 0 = healthy, 10 = finding to act on, 2 = tool error.

    ``pending_now`` is reported alongside the heartbeat because the two
    answer different questions and the incident needed both: the
    heartbeat says whether the scan is running, ``pending_now`` says how
    much is stuck behind it. A stale heartbeat with a large backlog is
    the exact signature of the 2026-08-19 outage.

    **A stale heartbeat alone is not a finding.** The monitoring loop is
    *designed* to stop: Step 7 halts it once no worker panes remain and
    the relay set is empty. After an idle evening the first ``--audit``
    of the next session necessarily sees an hours-old heartbeat, and
    reporting that as an outage would fire a false ``RELAY_SCAN_STALE``
    on essentially every org start — which is how a monitor teaches its
    reader to ignore it. So exit 10 requires **both** a missing/stale
    trace **and** an actual undelivered backlog (``pending_now > 0``):
    that pair means events are piling up while nothing is draining them,
    which is the failure itself rather than a proxy for it. A broken
    relay with an empty queue is inert by construction — and stops being
    inert the moment a terminal event lands, at which point the very
    next audit fires. The 2026-08-19 outage had 132+ pending, so this
    gate does not weaken the detection it was built for.

    ``status`` stays descriptive (``fresh`` / ``stale`` /
    ``never_scanned``) for diagnosis; ``finding`` is the actionable bit
    the runbook branches on.
    """
    pending_now = len(writer.pending_deliveries(
        recipient=recipient, kinds=list(kinds), since=since, limit=None))
    now = _now_iso(conn)
    entry = _read_heartbeat(db_path).get(recipient)
    if not isinstance(entry, dict):
        # A structurally wrong entry (e.g. {"secretary": "corrupt"}) is
        # corruption, not a trace. Coerce rather than raise so --audit
        # keeps its 0/10/2 exit contract exactly when the heartbeat is
        # malformed -- the moment the runbook most needs a usable answer.
        entry = {}
    last = entry.get("last_scan_at")
    if not isinstance(last, str):
        last = None
    halted_at = entry.get("halted_at")
    if not isinstance(halted_at, str):
        halted_at = None

    out = {
        "recipient": recipient,
        "checked_at": now,
        "last_scan_at": last,
        "halted_at": halted_at,
        # A halt marker only excuses the gap it opened, so it must post-date
        # the last scan. Both timestamps come from the same
        # ``strftime('%Y-%m-%dT%H:%M:%fZ')`` writer, so the lexicographic
        # compare is a chronological one. (A --list always replaces the whole
        # entry and drops halted_at, so the ordered case is the only one that
        # occurs normally; the guard covers a hand-edited/corrupt file.)
        "halted": bool(halted_at and (last is None or halted_at >= last)),
        "stale_min": stale_min,
        "pending_now": pending_now,
    }
    if not last:
        out["status"] = "never_scanned"
        out["age_min"] = None
        return out, _verdict(out, db_path)

    age_min = _age_minutes(conn, last)
    out["age_min"] = age_min
    if age_min is None:
        # Unparseable timestamp: treat as no usable trace (fail-safe).
        out["status"] = "never_scanned"
        return out, _verdict(out, db_path)
    out["status"] = "stale" if age_min > stale_min else "fresh"
    return out, _verdict(out, db_path)


def _verdict(out: dict, db_path) -> int:
    """Set ``out["finding"]`` and return the exit code.

    A finding needs a broken trace AND a real backlog behind it; see
    :func:`cmd_audit` for why a stale heartbeat alone is expected during
    the monitoring loop's designed downtime.

    A trace marked halted (Issue #955) is excused as well: Step 7 stopped
    the loop on purpose, so events that landed during the stop are not
    evidence that anything is broken -- the resume cycle drains them.
    Only the intentional stop is excused; a loop that simply died is
    still a finding.

    The exemption is spent here: the marker is cleared as soon as an
    audit relies on it, so the *next* audit is judged normally whether or
    not a scan followed (see :func:`clear_halted`). **An exemption that
    cannot be spent is not granted**: if that write fails (read-only FS,
    full disk, a failed ``replace()``), suppressing the finding anyway
    would leave ``halted_at`` on disk forever and silence every later
    stale-with-backlog finding. So a failed consume falls back to the
    normal verdict -- at worst one false ``RELAY_SCAN_STALE`` on a host
    whose telemetry is already broken, which is the same fail-loud
    direction a failed heartbeat write takes. ``halt_consumed`` records
    which way it went.
    """
    halted = bool(out.get("halted"))
    if halted:
        consumed = clear_halted(db_path, recipient=out["recipient"])
        out["halt_consumed"] = consumed
        halted = consumed
    out["finding"] = (out["status"] != "fresh"
                      and out["pending_now"] > 0
                      and not halted)
    return 10 if out["finding"] else 0


def _age_minutes(conn, iso_ts: str) -> Optional[float]:
    """Minutes between ``iso_ts`` and now, or None if it does not parse.

    Computed in SQLite so the arithmetic matches the format the rest of
    this tool writes and reads, without introducing a Python clock.
    """
    try:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(?)) * 1440.0 AS age",
            (iso_ts,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None or row["age"] is None:
        return None
    return round(float(row["age"]), 2)


def cmd_list(writer: StateWriter, conn, *, recipient: str,
             kinds: tuple[str, ...], since: Optional[str],
             limit: Optional[int], db_path=None) -> list[dict]:
    """List undelivered terminal events and record a relay attempt each.

    Records ``begin_delivery_attempt`` for every surfaced event so the
    ledger tracks how many cycles it has been pending (observability) and
    an attempt is durable even if the dispatcher dies before sending.
    The matching ``mark-delivered`` (only after a confirmed send) is what
    makes delivery terminal — this ordering is what yields at-least-once.

    Also stamps the execution-trace heartbeat (Issue #941) when
    ``db_path`` is supplied. The stamp is unconditional — a scan that
    found nothing is still a scan that ran, and distinguishing those two
    from "the command never executed" is the entire point.
    """
    rows = writer.pending_deliveries(
        recipient=recipient, kinds=list(kinds), since=since, limit=limit)
    if db_path is not None:
        write_heartbeat(db_path, recipient=recipient, surfaced=len(rows),
                        scanned_at=_now_iso(conn))
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


def _safe_audit(writer: StateWriter, conn, db_path, *, recipient: str,
                kinds: tuple, since: Optional[str],
                stale_min: float) -> tuple:
    """``cmd_audit`` with the 0/10/2 contract enforced at the boundary.

    The runbook only branches on 0/10/2, so an unexpected failure (corrupt
    DB, missing tables, a schema older than the ledger) must arrive as
    exit 2 "tool error" -- not as an uncaught traceback and exit 1, which
    the dispatcher has no branch for. A monitor whose own failure mode is
    unhandled is the thing this tool exists to stop.
    """
    try:
        result = cmd_audit(writer, conn, db_path, recipient=recipient,
                           kinds=kinds, since=since, stale_min=stale_min)
        conn.commit()
        return result
    except Exception as exc:  # noqa: BLE001 -- contract boundary
        return ({"status": "error", "recipient": recipient,
                 "error": f"{type(exc).__name__}: {exc}"}, 2)


def cmd_cycle(writer: StateWriter, conn, db_path, *, recipient: str,
              kinds: tuple, since: Optional[str], limit: Optional[int],
              stale_min: float) -> tuple:
    """One monitoring cycle: self-check then scan. Returns (dict, exit).

    Order is load-bearing: the audit reads the heartbeat that ``cmd_list``
    is about to stamp, so auditing second would report ``fresh`` forever
    and destroy the detection ``--audit`` exists for.

    The scan runs regardless of the audit verdict -- exit 10 means "report
    this", never "skip the scan"; the usual cause of a finding is the scan
    not running, so reporting and recovery belong in the same cycle. A
    failure of the scan half is reported as exit 2 with the audit half
    still rendered, since the audit result is exactly what diagnoses it.
    """
    report, code = _safe_audit(writer, conn, db_path, recipient=recipient,
                               kinds=kinds, since=since, stale_min=stale_min)
    out = {"audit": report, "items": []}
    try:
        out["items"] = cmd_list(writer, conn, recipient=recipient,
                                kinds=kinds, since=since, limit=limit,
                                db_path=db_path)
    except Exception as exc:  # noqa: BLE001 -- contract boundary
        out["items"] = None
        out["error"] = f"list: {type(exc).__name__}: {exc}"
        return out, 2
    return out, code


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
    p.add_argument("--since-hours", type=float, default=None,
                   help="scan floor override. Omitted (default) uses the "
                        "ledger epoch (relay every post-ledger event until "
                        "delivered, never aged out by wall-clock). 0 = "
                        "unbounded (also relays pre-ledger history; manual "
                        "backfill). N>0 = wall-clock floor now-N hours.")
    p.add_argument("--limit", type=int, default=None,
                   help="cap the number of events returned by --list")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--cycle", action="store_true",
                        help="one monitoring cycle: audit then list, in "
                             "one invocation. Prints a single JSON object "
                             "{audit, items}; exit code follows --audit "
                             "(0 / 10 / 2). The scan runs whatever the "
                             "audit says.")
    action.add_argument("--list", action="store_true",
                        help="list undelivered terminal events as JSON "
                             "(records a relay attempt for each)")
    action.add_argument("--mark-delivered", action="store_true",
                        help="mark --source-event-id delivered to --recipient")
    action.add_argument("--mark-failed", action="store_true",
                        help="record a retryable delivery failure")
    action.add_argument("--audit", action="store_true",
                        help="check that --list is actually being run: "
                             "exit 0 fresh / 10 stale or never scanned / "
                             "2 error. Prints a JSON report.")
    action.add_argument("--mark-halted", action="store_true",
                        help="record that the monitoring loop stopped on "
                             "purpose (runbook Step 7), so the first audit "
                             "after resume does not report the gap as an "
                             "outage. Cleared by the next scan.")
    p.add_argument("--stale-min", type=float, default=DEFAULT_STALE_MIN,
                   help=f"minutes before a scan counts as stale for "
                        f"--audit (default: {DEFAULT_STALE_MIN:g})")
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
        # A checkout with no state DB has no relay to run, so a missing
        # heartbeat is not a finding. Exit 0 so a plain clone / CI does
        # not report a false outage.
        no_db = {"status": "no_db", "recipient": args.recipient,
                 "db": str(db_path)}
        if args.audit:
            print(json.dumps(no_db, ensure_ascii=False, indent=2))
        if args.cycle:
            print(json.dumps({"audit": no_db, "items": []},
                             ensure_ascii=False, indent=2))
        if args.mark_halted:
            # Nothing to halt and nowhere to write it; stay quiet and
            # successful so an org with no DB yet is not a failure path.
            print(f"halted: no state db at {db_path} (nothing recorded)")
        return 0

    kinds = (tuple(k.strip() for k in args.kinds.split(",") if k.strip())
             if args.kinds else TERMINAL_KINDS)

    conn = connect(db_path)
    try:
        writer = StateWriter(conn)
        # Resolve the scan floor (only meaningful for --list):
        #   omitted        -> ledger epoch (safe default: post-ledger events
        #                     stay eligible until delivered, immune to
        #                     dispatcher outages of any length)
        #   --since-hours 0 -> unbounded (relays pre-ledger history too)
        #   --since-hours N -> wall-clock floor now-N h (operator override)
        if args.since_hours is None:
            since = _ledger_epoch(conn)
        else:
            since = _iso_since(args.since_hours)
        if args.list:
            items = cmd_list(writer, conn, recipient=args.recipient,
                             kinds=kinds, since=since, limit=args.limit,
                             db_path=db_path)
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        if args.cycle:
            report, code = cmd_cycle(
                writer, conn, db_path, recipient=args.recipient,
                kinds=kinds, since=since, limit=args.limit,
                stale_min=args.stale_min)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return code
        if args.audit:
            report, code = _safe_audit(
                writer, conn, db_path, recipient=args.recipient,
                kinds=kinds, since=since, stale_min=args.stale_min)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return code
        if args.mark_halted:
            # Re-check the backlog at the moment of the halt, not at the
            # moment of the last scan (Codex round 3 P2). A terminal event
            # landing between the final --cycle and this call would
            # otherwise be recorded as "nothing to relay, stopping on
            # purpose", and the marker would then excuse the very audit
            # that should have surfaced it. Step 7's own precondition is
            # "relay set empty", so refusing here just enforces it in the
            # tool instead of trusting the runbook to re-read it.
            try:
                pending_now = len(writer.pending_deliveries(
                    recipient=args.recipient, kinds=list(kinds),
                    since=since, limit=None))
            except Exception as exc:  # noqa: BLE001 -- contract boundary
                # Same 0/10/2 discipline as the audit path: a corrupt or
                # partially migrated DB must not leave Step 7 with a
                # traceback and exit 1, which the runbook has no branch
                # for (Codex round 4).
                print(f"error: halt precheck failed for {args.recipient}: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                return 2
            if pending_now:
                print(f"halt refused: {pending_now} undelivered event(s) "
                      f"for {args.recipient}; keep the loop running and "
                      f"relay them first")
                return 10
            halted_at = _now_iso(conn)
            ok = write_halted(db_path, recipient=args.recipient,
                              halted_at=halted_at)
            if not ok:
                # The marker IS the whole command; a swallowed failure
                # would let Step 7 stop believing the halt was recorded
                # and hand the next session the false RELAY_SCAN_STALE
                # this flag exists to prevent. Fail loud on the 0/10/2
                # contract instead (Codex P2).
                print(f"error: could not record the halt for "
                      f"{args.recipient} at {_heartbeat_path(db_path)}",
                      file=sys.stderr)
                return 2
            print(f"halted: {args.recipient} at {halted_at}")
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
