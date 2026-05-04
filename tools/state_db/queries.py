"""Read-only query layer over the M0 state DB.

Thin convenience wrappers around `sqlite3.Connection` that return plain
``dict`` rows. Keeping these as standalone functions (no ORM, no class) so
short-lived dashboard / SKILL invocations can open a connection, fetch what
they need, and close — WAL allows many concurrent readers.

The DB is a derived view in M1 (markdown remains SoT); see
migration-strategy.md §M1. Callers should keep a markdown fallback path on
disk and treat any query failure here as a soft signal to reach for it.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional


_ACTIVE_STATUSES: tuple[str, ...] = ("in_use", "review")


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
    """Return runs whose status is in_use or review (Worker Directory Registry)."""
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


def get_org_state_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate snapshot used by the dashboard.

    Shape:
        {
          "active_runs": [...],            # in_use / review
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
        "active_runs": list_active_runs(conn),
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


__all__ = [
    "list_active_runs",
    "get_run_by_task_id",
    "list_worker_dirs",
    "list_recent_events",
    "get_org_state_summary",
    "get_resume_briefing",
]
