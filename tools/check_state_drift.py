#!/usr/bin/env python3
"""Detect drift between ``state.db`` runs and on-disk worker state files.

Closes #356 (Epic #357). Implements the detection-only side of the recovery
paths called for by the parent epic. **Warn-only by default**: this command
NEVER mutates state.db, NEVER moves worker-state files, NEVER spawns or
closes panes. The contract (``docs/contracts/state-semantics-contract.md``,
in particular invariants I3 / I7 / I8 and § 4 transition ownership) is
deliberate that classification of run outcomes is a Secretary act; this tool
only surfaces evidence so an operator can decide.

Distinct from ``tools.state_db.drift_check`` (DB → ``.state/org-state.md``
markdown round-trip checker, Issue #267). This tool checks **DB ↔
worker-state-file** drift, a different surface.

Drift classes
-------------

D1. ``queued_stale``
    ``runs.status='queued'`` whose ``dispatched_at`` is older than
    ``--queued-stale-seconds`` (default 300s). Per § 2 / I8 a row that
    lingers in ``queued`` for more than a few seconds is itself a signal of
    a failed T2 transition (e.g., ``SPLIT_CAPACITY_EXCEEDED`` without
    Secretary cleanup). Recovery: Secretary investigates dispatcher state
    and applies T8 (``update_run_status('<task>', 'abandoned')``) once the
    contract write path lands. Today this is operator-confirmed.

D2. ``live_run_missing_worker_file``
    ``runs.status IN ('in_use','review')`` (active execution / human
    review) but ``.state/workers/worker-{task_id}.md`` is absent.
    Steady-state breach of I3. The ``/org-suspend`` exception in I3 does
    not erase the worker-state file (suspend graceful-closes the pane only;
    the .md persists), so a missing file genuinely signals drift even when
    ``org_sessions.status='SUSPENDED'``. Recovery: Secretary confirms with
    Dispatcher whether ``WORKER_PANE_EXITED`` was missed; on confirmation,
    the prescribed T7 transition (``→ abandoned``) is the recovery.
    Operator confirmation required.

D3. ``completed_run_worker_file_present``
    ``runs.status='completed'`` but ``.state/workers/worker-{task_id}.md``
    is still in the live workers directory (not under ``archive/``). The
    post-commit hook at ``tools/state_db/writer.py:597`` should have moved
    it; presence after a completed transition implies the hook didn't fire
    (e.g., direct SQL UPDATE bypassed it, or a transient IO failure).
    Recovery: re-run the archive move manually (``mv
    .state/workers/worker-{task_id}.md .state/workers/archive/``); no
    state.db write is needed because the run row is already terminal.

D4. ``terminal_nonarchived_worker_file`` *(future-covered)*
    ``runs.status IN ('failed','abandoned')`` with the .md still in the
    live workers directory. Per § 4 T7 / T8 / T9 the ``failed`` /
    ``abandoned`` write paths are prescribed but **not yet implemented**;
    no production callsite emits them today. Detection is included so the
    tool covers the contract surface, but in practice this class will
    remain empty until the prescribed transitions activate.

What this tool does NOT classify
--------------------------------

- **Worker-file orphans (no DB row at all)**: handled by
  ``tools/sweep_stale_workers.py``. That tool already classifies orphans
  against ``.state/org-state.md`` and is the canonical sweep. Surfacing
  the same files here would create two tools with disagreeing opinions
  on the same evidence; we explicitly defer.
- **Markdown ↔ DB drift**: handled by ``tools/state_db/drift_check.py``.

Exit codes
----------

* ``0`` — no drift detected.
* ``1`` — drift detected (records printed). This is the warn-only signal;
  no remediation is applied automatically.
* ``2`` — tool failure (DB missing, IO error, malformed schema).

Usage
-----

::

    py -3 tools/check_state_drift.py
    py -3 tools/check_state_drift.py --json
    py -3 tools/check_state_drift.py --queued-stale-seconds 120
    py -3 tools/check_state_drift.py --repo-root /path/to/checkout
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open ``db_path`` strictly read-only so detection cannot mutate state.

    The package-level ``tools.state_db.connect`` issues
    ``PRAGMA journal_mode = WAL`` on first open, which materially writes
    to the DB (flips the journal mode and creates ``-wal``/``-shm``
    siblings). For a *warn-only* detector that contract-claims "no
    mutation", that side-effect is itself drift. We open via the SQLite
    URI ``mode=ro`` so the connection has no write capability at all —
    journal-mode changes silently no-op rather than touching the file.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# Run-status groupings reused from the canonical contract (see
# state-semantics-contract.md § 3.5). Kept inline rather than imported from
# queries.py because that module's _ACTIVE_STATUSES is the user-visible
# projection, which differs from what we need here (we want active execution
# only, plus an explicit terminal predicate for D3 / D4).
_ACTIVE_EXECUTION_STATUSES = ("in_use", "review")
_TERMINAL_NON_COMPLETED = ("failed", "abandoned")
_DEFAULT_QUEUED_STALE_SECONDS = 300


@dataclass(frozen=True)
class DriftRecord:
    """One detected drift incident.

    ``operator_action`` names the documented recovery (see
    ``docs/operations/state-drift-recovery.md``); ``ambiguous`` is True when
    the class requires operator confirmation rather than mechanical fix.
    """
    klass: str
    task_id: str
    detail: str
    operator_action: str
    ambiguous: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _parse_iso_utc(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp produced by SQLite ``strftime`` / Python.

    Accepts both ``Z`` and ``+00:00`` suffixes; returns ``None`` on parse
    failure (treat as "unknown age" rather than crash — a malformed
    timestamp is itself drift but not detection-loop-fatal).
    """
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _detect_queued_stale(
    conn,
    *,
    now: datetime,
    queued_stale_seconds: int,
) -> list[DriftRecord]:
    rows = conn.execute(
        "SELECT task_id, dispatched_at FROM runs WHERE status = 'queued' "
        "ORDER BY dispatched_at"
    ).fetchall()
    out: list[DriftRecord] = []
    for r in rows:
        task_id = r["task_id"]
        dispatched = _parse_iso_utc(r["dispatched_at"])
        if dispatched is None:
            out.append(DriftRecord(
                klass="queued_stale",
                task_id=task_id,
                detail=(
                    f"queued with unparseable dispatched_at "
                    f"({r['dispatched_at']!r})"
                ),
                operator_action=(
                    "investigate row directly; treat as stale until "
                    "dispatched_at is corrected"
                ),
                ambiguous=True,
            ))
            continue
        age = (now - dispatched).total_seconds()
        if age > queued_stale_seconds:
            out.append(DriftRecord(
                klass="queued_stale",
                task_id=task_id,
                detail=(
                    f"queued for {age:.0f}s "
                    f"(threshold={queued_stale_seconds}s); "
                    f"likely failed T2 (e.g., SPLIT_CAPACITY_EXCEEDED)"
                ),
                operator_action=(
                    "Secretary: confirm dispatcher state and apply T8 "
                    "(abandoned) once the prescribed write path is live"
                ),
                ambiguous=True,
            ))
    return out


def _detect_live_missing_worker_file(
    conn,
    *,
    workers_dir: Path,
) -> list[DriftRecord]:
    placeholders = ", ".join("?" for _ in _ACTIVE_EXECUTION_STATUSES)
    rows = conn.execute(
        f"SELECT task_id, status FROM runs WHERE status IN ({placeholders})",
        _ACTIVE_EXECUTION_STATUSES,
    ).fetchall()
    out: list[DriftRecord] = []
    for r in rows:
        task_id = r["task_id"]
        md = workers_dir / f"worker-{task_id}.md"
        if md.exists():
            continue
        status = r["status"]
        # Per state-semantics-contract.md § 4: in_use's prescribed
        # recovery on missing pane is T7 (abandoned), but review's
        # normal exits are T5 (completed) and T6 (in_use). Pushing
        # T7 for review would discard already-reported completion
        # work. Tailor the guidance per status.
        if status == "review":
            action = (
                "Secretary: review state means a completion report is "
                "already on file. Restore the worker .md from "
                "Progress Log / archive if recoverable, then proceed "
                "with normal T5 (completed) or T6 (in_use review-"
                "feedback). Do NOT apply T7 — it would discard "
                "reported work."
            )
        else:
            action = (
                "Secretary: confirm with Dispatcher whether "
                "WORKER_PANE_EXITED was missed; on confirmation, "
                "apply T7 (abandoned) once the prescribed write path "
                "is live."
            )
        out.append(DriftRecord(
            klass="live_run_missing_worker_file",
            task_id=task_id,
            detail=(
                f"runs.status='{status}' but {md.name} is missing "
                f"from {workers_dir}"
            ),
            operator_action=action,
            ambiguous=True,
        ))
    return out


def _detect_completed_with_live_file(
    conn,
    *,
    workers_dir: Path,
) -> list[DriftRecord]:
    rows = conn.execute(
        "SELECT task_id FROM runs WHERE status = 'completed'"
    ).fetchall()
    out: list[DriftRecord] = []
    for r in rows:
        task_id = r["task_id"]
        md = workers_dir / f"worker-{task_id}.md"
        if not md.exists():
            continue
        out.append(DriftRecord(
            klass="completed_run_worker_file_present",
            task_id=task_id,
            detail=(
                f"runs.status='completed' but {md.name} still in "
                f"{workers_dir} (post-commit archive hook did not fire)"
            ),
            operator_action=(
                "move the file: "
                f"mv {md} {workers_dir / 'archive' / md.name}"
            ),
            ambiguous=False,
        ))
    return out


def _detect_terminal_nonarchived(
    conn,
    *,
    workers_dir: Path,
) -> list[DriftRecord]:
    placeholders = ", ".join("?" for _ in _TERMINAL_NON_COMPLETED)
    rows = conn.execute(
        f"SELECT task_id, status FROM runs WHERE status IN ({placeholders})",
        _TERMINAL_NON_COMPLETED,
    ).fetchall()
    out: list[DriftRecord] = []
    for r in rows:
        task_id = r["task_id"]
        md = workers_dir / f"worker-{task_id}.md"
        if not md.exists():
            continue
        out.append(DriftRecord(
            klass="terminal_nonarchived_worker_file",
            task_id=task_id,
            detail=(
                f"runs.status='{r['status']}' but {md.name} still in "
                f"{workers_dir} (no archive hook for {r['status']} today)"
            ),
            operator_action=(
                "move the file: "
                f"mv {md} {workers_dir / 'archive' / md.name}"
            ),
            ambiguous=False,
        ))
    return out


def detect_drift(
    db_path: Path,
    workers_dir: Path,
    *,
    queued_stale_seconds: int = _DEFAULT_QUEUED_STALE_SECONDS,
    now: Optional[datetime] = None,
) -> list[DriftRecord]:
    """Run every detector and return the merged drift list.

    Pure read-only; safe to run against a live DB while WAL writers
    are active. ``now`` is injectable so tests can pin a deterministic
    clock.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    conn = _open_readonly(db_path)
    try:
        records: list[DriftRecord] = []
        records.extend(_detect_queued_stale(
            conn,
            now=now,
            queued_stale_seconds=queued_stale_seconds,
        ))
        records.extend(_detect_live_missing_worker_file(
            conn, workers_dir=workers_dir,
        ))
        records.extend(_detect_completed_with_live_file(
            conn, workers_dir=workers_dir,
        ))
        records.extend(_detect_terminal_nonarchived(
            conn, workers_dir=workers_dir,
        ))
    finally:
        conn.close()
    return records


def _format_text(records: list[DriftRecord]) -> str:
    if not records:
        return "check_state_drift: no drift\n"
    lines = [f"check_state_drift: {len(records)} drift record(s)\n"]
    by_class: dict[str, list[DriftRecord]] = {}
    for rec in records:
        by_class.setdefault(rec.klass, []).append(rec)
    for klass in sorted(by_class):
        lines.append(f"\n[{klass}]")
        for rec in by_class[klass]:
            tag = " (operator-ambiguous)" if rec.ambiguous else ""
            lines.append(f"  - {rec.task_id}{tag}: {rec.detail}")
            lines.append(f"    action: {rec.operator_action}")
    lines.append("")
    return "\n".join(lines)


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python tools/check_state_drift.py",
        description=(
            "Detect drift between state.db runs and worker-state files. "
            "Warn-only — no mutation, no auto-heal."
        ),
    )
    p.add_argument(
        "--repo-root", type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repo root (defaults to checkout containing this script).",
    )
    p.add_argument(
        "--db", type=Path, default=None,
        help="Path to state.db (default: <repo-root>/.state/state.db).",
    )
    p.add_argument(
        "--workers-dir", type=Path, default=None,
        help=(
            "Path to .state/workers/ "
            "(default: <repo-root>/.state/workers)."
        ),
    )
    p.add_argument(
        "--queued-stale-seconds", type=int,
        default=_DEFAULT_QUEUED_STALE_SECONDS,
        help=(
            "Threshold for D1 (queued_stale). Default 300s. "
            "Lower values catch failed T2 sooner but raise false-positive "
            "rate on slow spawn paths (Windows base-clone fan-out)."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit JSON list of drift records to stdout instead of text.",
    )
    args = p.parse_args(argv)

    db_path = args.db or (args.repo_root / ".state" / "state.db")
    workers_dir = (
        args.workers_dir or (args.repo_root / ".state" / "workers")
    )

    if not db_path.exists():
        print(
            f"check_state_drift: error: DB not found: {db_path}",
            file=sys.stderr,
        )
        return 2
    if not workers_dir.exists():
        # A missing workers dir is itself a degenerate state; surface it
        # as a tool error rather than silently treating every active run
        # as drift.
        print(
            f"check_state_drift: error: workers dir not found: "
            f"{workers_dir}",
            file=sys.stderr,
        )
        return 2

    try:
        records = detect_drift(
            db_path,
            workers_dir,
            queued_stale_seconds=args.queued_stale_seconds,
        )
    except Exception as exc:  # pragma: no cover - defensive shell guard
        print(f"check_state_drift: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        sys.stdout.write(json.dumps(
            [r.as_dict() for r in records], indent=2,
        ))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_format_text(records))

    return 1 if records else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
