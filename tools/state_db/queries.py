"""Read-only query layer over the M0 state DB.

Thin convenience wrappers around `sqlite3.Connection` that return plain
``dict`` rows. Keeping these as standalone functions (no ORM, no class) so
short-lived dashboard / SKILL invocations can open a connection, fetch what
they need, and close — WAL allows many concurrent readers.

In M2 the DB is the canonical write target (migration-strategy.md §M2):
the M1 markdown overlay is gone, ``org_sessions`` carries Status / Updated /
Suspended / Resumed / Current Objective / Dispatcher / Curator /
Resume Instructions, and ``.state/org-state.md`` is regenerated from this
DB by :mod:`tools.state_db.snapshotter`.

State semantics (Set F — ``docs/contracts/state-semantics-contract.md``):
the contract pins four orthogonal predicates over ``runs.status`` (§3.5).
This module exposes those predicates as the named tuples below so resolver,
snapshotter, and dashboard share one definition. The ``queued`` ⊂
active-reservation but ⊄ user-visible asymmetry (§3 final paragraph, I8) is
the central correctness fact:

* ``ACTIVE_RESERVATION_STATUSES`` — the resolver's pattern-selection set
  (§3.1). A queued reservation already occupies the project's base-clone
  slot, so a concurrent delegation MUST switch to Pattern B.
* ``USER_VISIBLE_STATUSES`` — the dashboard / org-state.md Active Work Items
  set (§3.3). ``queued`` is intentionally excluded so a sub-second
  reservation does not flicker into the operator UI; lingering queued is an
  anomaly the dispatcher should surface, not a normal Active Work Item (I8).
* ``TERMINAL_STATUSES`` — runs that cannot transition further without
  operator override (§3.4).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional


# Set F §3.5 predicate table. Names match the contract's concept labels so
# `git grep` from the contract lands on the implementation.
ACTIVE_RESERVATION_STATUSES: tuple[str, ...] = ("queued", "in_use", "review")
USER_VISIBLE_STATUSES: tuple[str, ...] = ("in_use", "review")
ACTIVE_EXECUTION_STATUSES: tuple[str, ...] = ("in_use",)
TERMINAL_STATUSES: tuple[str, ...] = ("completed", "failed", "abandoned")

# Backwards-compatible alias retained for in-tree callers that still import
# the pre-Set-F name. Points at the user-visible projection (the prior
# meaning) rather than the broader active-reservation set.
_ACTIVE_STATUSES: tuple[str, ...] = USER_VISIBLE_STATUSES


def _row_to_dict(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]


# ---------------------------------------------------------------------------
# Run-level queries
# ---------------------------------------------------------------------------


def list_active_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return runs in the user-visible projection (Set F §3.3).

    Predicate: ``runs.status IN ('in_use', 'review')`` —
    ``USER_VISIBLE_STATUSES``. ``queued`` is intentionally excluded per
    contract §3.3 / I8: a fresh T1 reservation has not yet produced a pane,
    and surfacing it in Active Work Items would flicker the operator UI for
    sub-second reservations. Use :func:`list_reserved_runs` to see queued
    rows separately; the resolver's pattern-selection set (§3.1, the
    union of queued + in_use + review) is exposed as the
    ``ACTIVE_RESERVATION_STATUSES`` constant for callers that need to
    build their own predicate.

    Used by the dashboard's Active Work Items list and the snapshotter's
    ``## Active Work Items`` rendering.
    """
    rows = conn.execute(
        """
        SELECT
            r.id            AS run_id,
            r.task_id       AS task_id,
            r.title         AS title,
            r.pattern       AS pattern,
            r.status        AS status,
            r.branch        AS branch,
            r.pr_url        AS pr_url,
            r.pr_state      AS pr_state,
            r.issue_refs    AS issue_refs,
            r.verification  AS verification,
            r.dispatched_at AS dispatched_at,
            r.completed_at  AS completed_at,
            r.commit_short  AS commit_short,
            r.outcome_note  AS outcome_note,
            p.slug          AS project_slug,
            p.display_name  AS project_name,
            w.slug          AS workstream_slug,
            w.display_name  AS workstream_name,
            d.abs_path      AS worker_dir,
            d.current_branch AS worker_dir_branch,
            d.lifecycle     AS worker_dir_lifecycle
        FROM runs r
        JOIN projects p             ON p.id = r.project_id
        LEFT JOIN workstreams w     ON w.id = r.workstream_id
        LEFT JOIN worker_dirs d     ON d.id = r.worker_dir_id
        WHERE r.status IN ('in_use', 'review')
        ORDER BY r.dispatched_at DESC, r.id DESC
        """
    ).fetchall()
    return _rows_to_dicts(rows)


def list_reserved_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return runs in the active-reservation-only projection (Set F §3.1 \\ §3.3).

    Predicate: ``runs.status = 'queued'``. These rows occupy a project's
    base-clone slot (so the resolver must consider them for Pattern B
    selection — §3.1) but are not yet user-visible in Active Work Items
    (§3.3). The dashboard surfaces them as a separate "reserved" group so
    operators can spot a stuck T1→T2 transition (I8): a row that lingers
    here for more than a few seconds is itself a signal of a failed
    ``spawn_claude_pane`` step that never flipped the row to ``in_use``.
    """
    rows = conn.execute(
        """
        SELECT
            r.id            AS run_id,
            r.task_id       AS task_id,
            r.title         AS title,
            r.pattern       AS pattern,
            r.status        AS status,
            r.branch        AS branch,
            r.dispatched_at AS dispatched_at,
            p.slug          AS project_slug,
            p.display_name  AS project_name,
            d.abs_path      AS worker_dir,
            d.lifecycle     AS worker_dir_lifecycle
        FROM runs r
        JOIN projects p         ON p.id = r.project_id
        LEFT JOIN worker_dirs d ON d.id = r.worker_dir_id
        WHERE r.status = 'queued'
        ORDER BY r.dispatched_at DESC, r.id DESC
        """
    ).fetchall()
    return _rows_to_dicts(rows)


def list_runs_with_dirs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every run that has a worker_dir attached, regardless of status.

    Used by ``parse_org_state_db`` and the markdown snapshotter to render the
    Worker Directory Registry, which historically lists *all* runs with a
    directory (active + completed + failed + abandoned), not just the live ones.
    """
    rows = conn.execute(
        """
        SELECT
            r.task_id, r.pattern, r.status, r.title, r.outcome_note,
            r.dispatched_at,
            p.slug          AS project_slug,
            d.abs_path      AS worker_dir
        FROM runs r
        JOIN projects p         ON p.id = r.project_id
        LEFT JOIN worker_dirs d ON d.id = r.worker_dir_id
        WHERE d.abs_path IS NOT NULL
        ORDER BY r.dispatched_at, r.id
        """
    ).fetchall()
    return _rows_to_dicts(rows)


def get_run_by_task_id(
    conn: sqlite3.Connection, task_id: str
) -> Optional[dict[str, Any]]:
    """Return one run row keyed by its natural task_id, or None."""
    row = conn.execute(
        """
        SELECT r.*, p.slug AS project_slug, p.display_name AS project_name,
               d.abs_path AS worker_dir, d.lifecycle AS worker_dir_lifecycle
        FROM runs r
        JOIN projects p         ON p.id = r.project_id
        LEFT JOIN worker_dirs d ON d.id = r.worker_dir_id
        WHERE r.task_id = ?
        """,
        (task_id,),
    ).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Worker directories
# ---------------------------------------------------------------------------


def list_worker_dirs(
    conn: sqlite3.Connection, lifecycle: Optional[str] = None
) -> list[dict[str, Any]]:
    """Return worker_dirs rows, optionally filtered to a single lifecycle."""
    if lifecycle is not None:
        rows = conn.execute(
            "SELECT * FROM worker_dirs WHERE lifecycle = ? ORDER BY abs_path",
            (lifecycle,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM worker_dirs ORDER BY abs_path"
        ).fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def list_recent_events(
    conn: sqlite3.Connection, limit: int = 50
) -> list[dict[str, Any]]:
    """Return the most recent events ordered by id DESC."""
    if limit < 0:
        limit = 0
    rows = conn.execute(
        """
        SELECT id, occurred_at, actor, kind, run_id, workstream_id,
               project_id, payload_json
        FROM events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Aggregate dashboards / briefings
# ---------------------------------------------------------------------------


def _run_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM runs GROUP BY status"
    ).fetchall()
    return {r["status"]: r["c"] for r in rows}


def get_session(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    """Return the singleton ``org_sessions`` row, or ``None`` if missing.

    M2 (Issue #267) replaced the M1 markdown overlay; consumers now read
    Status / Updated / Suspended / Resumed / Current Objective / Dispatcher
    / Curator / Resume Instructions straight from this row.

    Pre-M2 DB handling: when the ``org_sessions`` table is absent we try
    to forward-migrate via ``ensure_m2_schema``, but only if the caller's
    connection is **not** in an open transaction. Migrating inside a
    caller-controlled tx would force an implicit COMMIT (executescript
    behaviour — see N1 in cross-review), silently confirming any pending
    writes the caller meant to roll back. So an in-tx caller gets
    ``None`` and is expected to handle that as "session unknown" rather
    than as a positive read result.

    Returning ``None`` here doesn't trigger a markdown fallback in the
    M2 dashboard / converter (the M1 overlay is gone); callers will see
    Status default to IDLE and the role / objective / resume_instructions
    fields go unset for that render. Run the importer once to install
    the M2 table out-of-band if you need a stable read.
    """
    try:
        row = conn.execute(
            "SELECT * FROM org_sessions WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        # Pre-M2 DB. Don't migrate inside an open caller tx — see docstring
        # for the rationale. Skip silently and let the caller see a
        # missing-row signal.
        if getattr(conn, "in_transaction", False):
            return None
        try:
            from tools.state_db import ensure_m2_schema
            ensure_m2_schema(conn)
            conn.commit()
            row = conn.execute(
                "SELECT * FROM org_sessions WHERE id = 1"
            ).fetchone()
        except (sqlite3.OperationalError, RuntimeError):
            return None
    return _row_to_dict(row)


def get_org_state_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate snapshot used by the dashboard.

    Shape:
        {
          "session": {... org_sessions row ...} | None,
          "active_runs": [...],            # Set F §3.3 user-visible (in_use / review)
          "reserved_runs": [...],          # Set F §3.1 \\ §3.3 (queued only)
          "active_worker_dirs": [...],     # lifecycle='active'
          "recent_events": [...],          # 20 newest
          "run_status_counts": {status: count},
          "totals": {"projects": int, "runs": int, "worker_dirs": int}
        }
    """
    totals_row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM projects)     AS projects,
            (SELECT COUNT(*) FROM runs)         AS runs,
            (SELECT COUNT(*) FROM worker_dirs)  AS worker_dirs
        """
    ).fetchone()
    return {
        "session": get_session(conn),
        "active_runs": list_active_runs(conn),
        "reserved_runs": list_reserved_runs(conn),
        "active_worker_dirs": list_worker_dirs(conn, lifecycle="active"),
        "recent_events": list_recent_events(conn, limit=20),
        "run_status_counts": _run_status_counts(conn),
        "totals": _row_to_dict(totals_row) or {},
    }


def get_resume_briefing(conn: sqlite3.Connection) -> dict[str, Any]:
    """Phase 1-2 briefing payload for org-resume.

    Reflects the state needed to greet the human after a SUSPEND: which runs
    are still live, when the org was last suspended, and what happened in the
    journal recently.

    .. note::
       Heavyweight by design — returns raw ``payload_json`` and the full
       ``resume_instructions`` body. /org-resume Phase 1 should call
       :func:`get_resume_briefing_light` instead so a fresh secretary
       session does not pay 6–8k tokens of context just to render the
       opening summary (Issue #412). This API is retained for callers
       that genuinely need the raw blobs (rare).
    """
    suspend_row = conn.execute(
        """
        SELECT occurred_at, actor, payload_json
        FROM events
        WHERE kind = 'suspend'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    last_row = conn.execute(
        "SELECT occurred_at, kind FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "session": get_session(conn),
        "active_runs": list_active_runs(conn),
        "active_worker_dirs": list_worker_dirs(conn, lifecycle="active"),
        "recent_events": list_recent_events(conn, limit=30),
        "run_status_counts": _run_status_counts(conn),
        "last_event_at": last_row["occurred_at"] if last_row else None,
        "last_event_kind": last_row["kind"] if last_row else None,
        "last_suspend_at": suspend_row["occurred_at"] if suspend_row else None,
        "last_suspend_actor": suspend_row["actor"] if suspend_row else None,
        "last_suspend_payload": (
            suspend_row["payload_json"] if suspend_row else None
        ),
    }


# ---------------------------------------------------------------------------
# Lightweight briefing API for /org-resume Phase 1 (Issue #412)
# ---------------------------------------------------------------------------
#
# The heavyweight :func:`get_resume_briefing` returns raw ``payload_json``
# blobs, the full ``resume_instructions`` body, and a 30-event tail. On a
# busy org that bill comes due *every secretary session* during the Phase 1
# briefing — empirically 6–8k tokens of context just to render the opening
# summary. The light API trims that bill to roughly constant by:
#
# * compressing each event to a per-kind allowlist of fields (or a
#   kind/actor/occurred_at fallback for unknown kinds — see
#   ``docs/journal-events.md`` for the typical-fields catalog),
# * dropping ``resume_instructions`` raw text in favour of a short
#   ``resume_summary``,
# * scoping briefing worker_dirs to ``runs.status`` membership rather
#   than ``worker_dirs.lifecycle``: state-semantics-contract I7 pins
#   that the two predicates can disagree (an ``active`` lifecycle dir
#   may correspond to a completed run), and the briefing wants the
#   runs-current set,
# * returning a compressed ``last_suspend_summary`` (counts and the
#   reason string) rather than the raw suspend payload, which can carry
#   tens of KB of pending_items / active_workers for a busy session.
#
# The dashboard / converter path (``get_org_state_summary``) is
# deliberately untouched — its callers depend on the existing shape.

# Briefing-only event-stream noise list. These kinds get emitted often
# enough to crowd out signal in a 5-event window without ever helping the
# secretary's opening summary. Anything not on this list is considered
# signal and may appear in :func:`list_recent_events_for_briefing`.
BRIEFING_EVENT_KINDS_NOISE: frozenset[str] = frozenset({
    # Observability churn from the dispatcher rate-limiter.
    "events_dropped",
    # /org-start re-asserts secretary identity on every cold boot; not
    # interesting in a "what happened recently" briefing.
    "secretary_identity_restored",
})


# Per-kind allowlist for ``event_summary`` payload extraction. The keys
# match the event names in ``docs/journal-events.md``; the value tuple is
# the subset of payload fields worth keeping in a one-line briefing
# summary. Long-form fields (``summary``, ``note``) are intentionally
# omitted — the briefing answers "what kind of thing happened" not "what
# did the worker say verbatim". Unknown kinds fall back to
# ``{kind, actor, occurred_at}``.
_BRIEFING_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    # worker lifecycle
    "worker_spawned":          ("worker", "task"),
    "worker_completed":        ("worker", "task"),
    "worker_reported":         ("worker", "task"),
    "worker_closed":           ("worker", "pane_id"),
    "worker_review":           ("worker", "task", "outcome"),
    "worker_report_forwarded": ("worker", "task", "recipient"),
    "worktree_removed":        ("path", "task"),
    "retro_deferred":          ("worker", "reason"),
    # delegate flow
    "delegate_sent":           ("task", "worker"),
    "delegate_resume":         ("task", "worker"),
    "delegate_resume_r2":      ("task", "worker", "round"),
    # plan / design
    "plan_delivered":          ("task", "worker"),
    "plan_approved":           ("task",),
    "plan_approved_and_prep_dispatched": ("task", "prep_worker"),
    "prep_delivered":          ("task", "worker"),
    "design_approved":         ("task", "pr"),
    "drift_reaudit":           ("task", "reason"),
    # PR / push
    "fix_pushed":              ("task", "branch", "commit"),
    "pr_opened":               ("task", "pr"),
    "prs_opened":              ("count",),
    "pr_merged":               ("pr", "task"),
    "prs_merged":              ("count",),
    "prs_pushed":              ("count",),
    # history / phase
    "pre_history_reset_snapshot": ("path",),
    "phase_d_snapshot":        ("path",),
    "phase_d_complete":        ("task",),
    "phase_d_force_push":      ("branch",),
    "pane_closed":             ("pane_id", "worker"),
    # issues
    "issue_filed":             ("issue", "title"),
    "issues_filed":            ("count",),
    "issues_swept":            ("count",),
    "issue_closed":            ("issue",),
    # observability
    "anomaly_observed":        ("worker", "kind", "confidence"),
    "notify_sent":             ("recipient", "kind"),
    # CI
    "ci_completed":            ("pr", "status"),
    # session lifecycle
    "suspend":                 ("reason",),
    "resume":                  (),
    "task_completed":          ("task",),
}

# Cap any extracted string value at this length to keep a runaway field
# (an unexpectedly long worker id, a multi-line title) from re-inflating
# the briefing. Picked to fit a one-line terminal render.
_BRIEFING_FIELD_MAX_LEN = 120


def _briefing_value(value: Any) -> Any:
    """Coerce a payload value to the scalar subset the briefing accepts.

    The allowlist only enumerates *which keys* survive into the briefing
    — it doesn't say anything about the value shape, and
    ``tools/journal_append.py --json '{...}'`` cheerfully accepts nested
    objects. So a single allowlisted ``summary`` field that happens to
    carry a 30-KB list would re-inflate the briefing past the
    Issue #412 budget. Restrict to JSON scalars; downgrade list/dict to
    a length-only marker so the operator still sees that *something*
    was there without the bytes coming along.
    """
    if isinstance(value, str):
        if len(value) > _BRIEFING_FIELD_MAX_LEN:
            return value[:_BRIEFING_FIELD_MAX_LEN] + "…"
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return {"_type": "list", "_len": len(value)}
    if isinstance(value, dict):
        return {"_type": "dict", "_keys": len(value)}
    # Fallback: a JSON-decodable but unexpected scalar type. Stringify
    # the type name; never echo ``repr(value)`` since that would inline
    # the bytes the caller is trying to suppress.
    return {"_type": type(value).__name__}


def _extract_briefing_payload(
    kind: str, payload_json: Optional[str]
) -> dict[str, Any]:
    fields = _BRIEFING_PAYLOAD_FIELDS.get(kind)
    if fields is None or not payload_json:
        return {}
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {f: _briefing_value(payload[f]) for f in fields if f in payload}


def list_recent_events_for_briefing(
    conn: sqlite3.Connection, limit: int = 5
) -> list[dict[str, Any]]:
    """Briefing-only recent events — short, allowlisted, noise-filtered.

    Differs from :func:`list_recent_events` on three axes:

    * default ``limit`` is 5 (vs. 50): the briefing renders a short
      headline list, not the full activity tail.
    * ``BRIEFING_EVENT_KINDS_NOISE`` rows are excluded *in the SQL
      WHERE clause* so the LIMIT counts only signal rows. A previous
      Python-side filter could return < ``limit`` rows when a busy
      tail of dispatcher noise crowded out the older signal.
    * each row is the ``event_summary`` shape — ``id, occurred_at, actor,
      kind, fields`` — where ``fields`` is the per-kind payload allowlist
      extraction (see ``_BRIEFING_PAYLOAD_FIELDS``). Raw ``payload_json``
      is never returned. Unknown kinds get an empty ``fields`` map and
      fall back to ``{kind, actor, occurred_at}`` for identification.
    """
    if limit <= 0:
        return []
    # Build the NOT IN clause from the noise constant rather than
    # hard-coding it, so the SQL stays in lockstep with
    # ``BRIEFING_EVENT_KINDS_NOISE`` if the set grows.
    noise = tuple(sorted(BRIEFING_EVENT_KINDS_NOISE))
    if noise:
        placeholders = ",".join("?" * len(noise))
        rows = conn.execute(
            f"""
            SELECT id, occurred_at, actor, kind, payload_json
            FROM events
            WHERE kind NOT IN ({placeholders})
            ORDER BY id DESC
            LIMIT ?
            """,
            (*noise, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, occurred_at, actor, kind, payload_json
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "occurred_at": r["occurred_at"],
            "actor": r["actor"],
            "kind": r["kind"],
            "fields": _extract_briefing_payload(r["kind"], r["payload_json"]),
        }
        for r in rows
    ]


def format_session_brief(
    session: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Compress an ``org_sessions`` row into the briefing-safe subset.

    Drops ``resume_instructions`` raw body (often multi-KB after a long
    suspend) in favour of a short ``resume_summary`` excerpt, and drops
    pane/peer ids the briefing doesn't surface. Returning ``None`` for an
    absent row mirrors :func:`get_session`.
    """
    if not session:
        return None
    raw = session.get("resume_instructions") or ""
    if raw:
        flat = raw.strip().splitlines()[0] if raw.strip() else ""
        if len(flat) > _BRIEFING_FIELD_MAX_LEN:
            resume_summary: Optional[str] = (
                flat[:_BRIEFING_FIELD_MAX_LEN] + "…"
            )
        else:
            resume_summary = flat or None
    else:
        resume_summary = None
    return {
        "status": session.get("status"),
        "objective": session.get("objective"),
        "started_at": session.get("started_at"),
        "updated_at": session.get("updated_at"),
        "suspended_at": session.get("suspended_at"),
        "resumed_at": session.get("resumed_at"),
        "resume_summary": resume_summary,
        "has_resume_instructions": bool(raw),
    }


def list_briefing_worker_dirs(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Worker dirs scoped to live runs, not to ``worker_dirs.lifecycle``.

    State-semantics-contract I7 pins that ``worker_dirs.lifecycle`` and
    ``runs.status`` are independent predicates: an ``active`` lifecycle
    directory may belong to a completed run (the inventory scrubber has
    not yet flipped it to ``archived``), and a stale ``archived`` row
    might still be referenced by a live ``in_use`` run if a previous
    cleanup raced. The briefing wants the *runs-current* set — every
    worker_dir reachable from a queued / in_use / review run — so it
    joins through ``runs.worker_dir_id`` rather than filtering on
    ``lifecycle = 'active'``.

    The result is named ``active_inventory_dirs`` in
    :func:`get_resume_briefing_light` so callers don't conflate it with
    the dashboard's ``active_worker_dirs`` (lifecycle-scoped, used by
    the inventory UI).
    """
    rows = conn.execute(
        f"""
        SELECT DISTINCT
            d.id, d.abs_path, d.layout, d.lifecycle, d.current_branch
        FROM worker_dirs d
        JOIN runs r ON r.worker_dir_id = d.id
        WHERE r.status IN ({','.join('?' * len(ACTIVE_RESERVATION_STATUSES))})
        ORDER BY d.abs_path
        """,
        ACTIVE_RESERVATION_STATUSES,
    ).fetchall()
    return _rows_to_dicts(rows)


def format_last_suspend_summary(
    suspend_row: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Compress the latest ``suspend`` event row into briefing-safe shape.

    The raw ``suspend`` payload can carry tens of KB
    (``active_workers[]``, ``pending_items[]``) on a busy org. The
    briefing only needs scalar headlines (when, who, why, and the two
    list lengths); a caller that needs the full payload should query the
    events table directly or use the heavyweight
    :func:`get_resume_briefing`.
    """
    if not suspend_row:
        return None
    raw = suspend_row.get("payload_json")
    payload: dict[str, Any] = {}
    if raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                payload = decoded
        except (TypeError, ValueError):
            payload = {}

    def _count(name: str) -> Optional[int]:
        v = payload.get(name)
        return len(v) if isinstance(v, list) else None

    reason = payload.get("reason")
    return {
        "occurred_at": suspend_row.get("occurred_at"),
        "actor": suspend_row.get("actor"),
        "reason": _briefing_value(reason) if reason is not None else None,
        "active_workers_count": _count("active_workers"),
        "pending_items_count": _count("pending_items"),
    }


def get_resume_briefing_light(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Lightweight Phase-1 briefing payload (Issue #412).

    Sister to :func:`get_resume_briefing`, but explicitly bounded:

    * ``session`` is replaced by :func:`format_session_brief` (no raw
      ``resume_instructions`` body).
    * ``recent_events`` is the 5-row event-summary stream from
      :func:`list_recent_events_for_briefing` (no raw ``payload_json``,
      noise kinds filtered out).
    * ``active_inventory_dirs`` is :func:`list_briefing_worker_dirs`'
      runs-scoped view (not ``worker_dirs.lifecycle='active'``).
    * the latest ``suspend`` is reduced to :func:`format_last_suspend_summary`
      (no raw payload).

    The /org-resume Phase 1 query path uses this API so a fresh secretary
    session pays roughly constant context cost regardless of how busy the
    org has been.
    """
    last_row = conn.execute(
        "SELECT occurred_at, kind FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    suspend_row = conn.execute(
        """
        SELECT occurred_at, actor, payload_json
        FROM events
        WHERE kind = 'suspend'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "session": format_session_brief(get_session(conn)),
        "active_runs": list_active_runs(conn),
        "reserved_runs": list_reserved_runs(conn),
        "active_inventory_dirs": list_briefing_worker_dirs(conn),
        "recent_events": list_recent_events_for_briefing(conn, limit=5),
        "run_status_counts": _run_status_counts(conn),
        "last_event_at": last_row["occurred_at"] if last_row else None,
        "last_event_kind": last_row["kind"] if last_row else None,
        "last_suspend_summary": format_last_suspend_summary(
            _row_to_dict(suspend_row)
        ),
    }


__all__ = [
    "ACTIVE_RESERVATION_STATUSES",
    "USER_VISIBLE_STATUSES",
    "ACTIVE_EXECUTION_STATUSES",
    "TERMINAL_STATUSES",
    "BRIEFING_EVENT_KINDS_NOISE",
    "list_active_runs",
    "list_reserved_runs",
    "list_runs_with_dirs",
    "get_run_by_task_id",
    "list_worker_dirs",
    "list_recent_events",
    "list_recent_events_for_briefing",
    "list_briefing_worker_dirs",
    "format_session_brief",
    "format_last_suspend_summary",
    "get_session",
    "get_org_state_summary",
    "get_resume_briefing",
    "get_resume_briefing_light",
]
