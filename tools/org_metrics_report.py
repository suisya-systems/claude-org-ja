#!/usr/bin/env python3
"""Read-only team metrics report for the claude-org state DB (Issue #551).

This CLI aggregates run / event / PR / CI activity from
``<repo_root>/.state/state.db`` over a chosen period and renders the result
as Markdown (default) or JSON. It is meant for team-adoption reporting: a
human runs it to see "what did the org actually do this week".

Design guarantees (kept deliberately narrow so this stays a pure reader):

1. **Read-only DB access.** The connection is opened with the sqlite
   ``mode=ro`` URI plus ``PRAGMA query_only=ON``. We deliberately do NOT use
   ``tools.state_db.connect()`` because that applies ``journal_mode=WAL`` and
   would happily run forward migrations -- both are writes/side effects we
   must never trigger from a report tool.

2. **Period inclusion rule.** A run is counted when its ``dispatched_at``
   falls inside the period (i.e. the run *started* within the window).
   Events and CI events are counted by ``occurred_at``. The bound check is an
   inclusive lexicographic compare against ISO-8601 ``...Z`` timestamps, which
   is well-defined because every timestamp the writers emit shares that fixed
   width-and-zone format. See ``_in_period``.

3. **CI<->run join is a 3-stage fallback** (never a silent drop):
   ``events.run_id`` first, then a normalized ``(owner/repo, pr-number)``
   match against ``runs.pr_url``, then anything left over is surfaced in an
   explicit ``unmatched`` bucket.

4. **Frozen event vocabulary.** Redispatch = ``delegate_resume`` /
   ``delegate_resume_r2``; pane-close = ``worker_closed`` / ``pane_closed``.
   ``WORKER_PANE_EXITED`` is a peer-message only signal and never lands in the
   events table, so it is intentionally not counted.

5. **runs.status is a closed enum.** ``suspended`` is a reserved value that
   production does not currently emit; it is reported on its own line and is
   never folded into the live or terminal aggregates.

6. **Pending-decisions count** is read from ``.state/pending_decisions.json``
   (path overridable). A missing file reports as "not available", which is
   kept distinct from a present-but-empty register (0 pending).

All emitted strings use ASCII hyphens only (no em-dash) so ``--help`` and the
report body stay safe on a cp932 (Windows) console.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _fmt_ts(dt: datetime) -> str:
    """Format ``dt`` exactly like the DB writers do: ISO-8601 with 3-digit
    milliseconds and a ``Z`` suffix (SQLite ``strftime('%Y-%m-%dT%H:%M:%fZ')``
    yields millisecond precision). Producing the same width matters because
    the period filter compares timestamps *lexicographically*: a 6-digit
    microsecond bound would sort after a 3-digit DB value sharing the same
    instant and wrongly exclude an on-boundary row.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"

# --- frozen event-kind vocabulary (design note 4) ------------------------
REDISPATCH_KINDS: tuple[str, ...] = ("delegate_resume", "delegate_resume_r2")
PANE_CLOSE_KINDS: tuple[str, ...] = ("worker_closed", "pane_closed")

# --- runs.status classification (design note 5) --------------------------
# suspended is a reserved enum value and is excluded from both buckets.
LIVE_STATUSES: tuple[str, ...] = ("queued", "in_use", "review")
TERMINAL_STATUSES: tuple[str, ...] = ("completed", "failed", "abandoned")
RESERVED_STATUSES: tuple[str, ...] = ("suspended",)

# github.com/<owner>/<repo>/pull/<n> -> ("owner/repo", n). Case-insensitive;
# the owner/repo half is lowercased by callers before comparison.
_PR_URL_RE = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)", re.IGNORECASE)


# =========================================================================
# DB access (read-only)
# =========================================================================
def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open ``db_path`` strictly read-only.

    Uses the ``file:...?mode=ro`` URI (fails fast if the DB is missing rather
    than creating an empty one) and pins ``query_only=ON`` as a belt-and-
    suspenders guard. Never applies WAL or runs migrations.
    """
    # ``Path.as_uri()`` percent-encodes the path (spaces, ``?``, ``#``) and
    # normalizes separators cross-platform, so a path containing a literal
    # ``?`` is not mis-parsed as the start of the URI query string.
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def resolve_db_path(cli_override: Optional[str]) -> Path:
    """Resolve the state DB path.

    Precedence: ``--db-path`` > ``$STATE_DB_PATH`` > ``<repo_root>/.state/
    state.db``. Mirrors tools/state_db/discover.py's precedence without
    importing it (keeps this tool free of any write-capable code path).
    """
    if cli_override:
        return Path(cli_override).expanduser()
    env = os.environ.get("STATE_DB_PATH")
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / ".state" / "state.db"


# =========================================================================
# Period handling (design note 2)
# =========================================================================
def _normalize_since(value: str) -> str:
    """Lower bound: a bare ``YYYY-MM-DD`` is already a valid prefix-style
    inclusive start-of-day bound under lexicographic compare, so pass it
    through verbatim."""
    return value.strip()


def _normalize_until(value: str) -> str:
    """Upper bound: a bare ``YYYY-MM-DD`` must cover the *whole* day, so
    expand it to an end-of-day timestamp. A value that already carries a time
    component is used as-is."""
    v = value.strip()
    if len(v) == 10 and v.count("-") == 2:
        # End-of-day at the DB's 3-digit millisecond precision so a row stamped
        # at the day's last writable instant (``...T23:59:59.999Z``) still
        # sorts <= this bound under lexicographic compare.
        return v + "T23:59:59.999Z"
    return v


def compute_bounds(
    *,
    since: Optional[str],
    until: Optional[str],
    last_days: Optional[int],
    now: datetime,
) -> tuple[Optional[str], Optional[str]]:
    """Return the ``(lo, hi)`` ISO bounds for the period.

    ``--last-days N`` sets ``lo = now - N days`` and, unless ``--until`` is
    given, ``hi = now``. ``--last-days`` cannot be combined with ``--since``
    (the caller validates this and raises argparse error upstream).
    """
    if last_days is not None:
        lo = _fmt_ts(now - timedelta(days=last_days))
        hi = _normalize_until(until) if until else _fmt_ts(now)
        return lo, hi
    lo = _normalize_since(since) if since else None
    hi = _normalize_until(until) if until else None
    return lo, hi


def _in_period(ts: Optional[str], lo: Optional[str], hi: Optional[str]) -> bool:
    """Inclusive lexicographic membership test. ``None`` ts is never in any
    bounded period; ``None`` bounds are open-ended on that side."""
    if ts is None:
        return False
    if lo is not None and ts < lo:
        return False
    if hi is not None and ts > hi:
        return False
    return True


# =========================================================================
# Tolerant payload reader (design note: fixtures carry payload gaps/extras)
# =========================================================================
def _load_payload(raw: Any) -> dict[str, Any]:
    """Decode ``events.payload_json`` defensively.

    The schema CHECK guarantees valid JSON, but a row could still hold a JSON
    scalar/array, or be missing keys we want. Anything that is not a JSON
    object decodes to ``{}`` so callers can ``.get()`` without guarding.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


# =========================================================================
# Pending decisions (design note 6)
# =========================================================================
def load_pending_decisions(path: Path) -> Optional[dict[str, Any]]:
    """Return pending-decision counts, or ``None`` if the file is absent.

    ``None`` (file missing) is intentionally distinct from a present register
    with zero pending entries. A malformed file is reported as an error bucket
    rather than crashing the whole report.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except (OSError, ValueError) as exc:
        return {"error": f"could not read register: {exc}", "by_status": {}}
    if not isinstance(data, list):
        return {"error": "register top-level is not a list", "by_status": {}}
    by_status: dict[str, int] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status", "pending"))
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "error": None,
        "total": sum(by_status.values()),
        "pending": by_status.get("pending", 0),
        "by_status": by_status,
    }


# =========================================================================
# Aggregation
# =========================================================================
def _parse_pr_url(pr_url: Optional[str]) -> Optional[tuple[str, int]]:
    if not pr_url:
        return None
    m = _PR_URL_RE.search(pr_url)
    if not m:
        return None
    return (m.group(1).lower(), int(m.group(2)))


def gather_pr_index(conn: sqlite3.Connection) -> dict[tuple[str, int], str]:
    """Index ``(owner/repo, pr-number) -> task_id`` over **all** runs.

    Deliberately not period-scoped: a CI event inside the window can belong to
    a run dispatched before it, and the 3-stage join's PR fallback must still
    resolve that rather than mislabel it unmatched (design note 3).
    """
    index: dict[tuple[str, int], str] = {}
    for r in conn.execute("SELECT task_id, pr_url FROM runs").fetchall():
        parsed = _parse_pr_url(r["pr_url"])
        if parsed is not None:
            index[parsed] = r["task_id"]
    return index


def gather_runs(conn: sqlite3.Connection, lo, hi) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT r.task_id, r.status, r.pattern, r.pr_url, r.pr_state, "
        "r.dispatched_at, r.completed_at, p.slug AS project_slug "
        "FROM runs r JOIN projects p ON p.id = r.project_id"
    ).fetchall()

    by_status: dict[str, int] = {}
    by_project: dict[str, int] = {}
    by_pattern: dict[str, int] = {}
    by_pr_state: dict[str, int] = {}
    total = 0
    completed = 0
    with_pr = 0

    for r in rows:
        if not _in_period(r["dispatched_at"], lo, hi):
            continue
        total += 1
        status = r["status"]
        by_status[status] = by_status.get(status, 0) + 1
        by_project[r["project_slug"]] = by_project.get(r["project_slug"], 0) + 1
        by_pattern[r["pattern"]] = by_pattern.get(r["pattern"], 0) + 1
        if status == "completed":
            completed += 1
        if r["pr_url"]:
            with_pr += 1
            state = r["pr_state"] or "unknown"
            by_pr_state[state] = by_pr_state.get(state, 0) + 1

    live = sum(by_status.get(s, 0) for s in LIVE_STATUSES)
    terminal = sum(by_status.get(s, 0) for s in TERMINAL_STATUSES)
    reserved = sum(by_status.get(s, 0) for s in RESERVED_STATUSES)
    completion_rate = (completed / total) if total else None

    return {
        "total": total,
        "by_status": by_status,
        "live": live,
        "terminal": terminal,
        "reserved": reserved,
        "completed": completed,
        "completion_rate": completion_rate,
        "by_project": by_project,
        "by_pattern": by_pattern,
        "with_pr": with_pr,
        "by_pr_state": by_pr_state,
    }


def gather_events_and_ci(
    conn: sqlite3.Connection, lo, hi, pr_index: dict[tuple[str, int], str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = conn.execute(
        "SELECT e.id, e.kind, e.occurred_at, e.run_id, e.payload_json, "
        "r.task_id AS run_task_id "
        "FROM events e LEFT JOIN runs r ON r.id = e.run_id"
    ).fetchall()

    kind_counts: dict[str, int] = {}
    total_events = 0
    redispatch = 0
    pane_close = 0
    worker_completed = 0

    ci_total = 0
    ci_by_status: dict[str, int] = {}
    ci_matched_run_id = 0
    ci_matched_pr = 0
    ci_unmatched: list[dict[str, Any]] = []
    ci_durations: list[int] = []

    for e in rows:
        if not _in_period(e["occurred_at"], lo, hi):
            continue
        total_events += 1
        kind = e["kind"]
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if kind in REDISPATCH_KINDS:
            redispatch += 1
        if kind in PANE_CLOSE_KINDS:
            pane_close += 1
        if kind == "worker_completed":
            worker_completed += 1

        if kind == "ci_completed":
            ci_total += 1
            payload = _load_payload(e["payload_json"])
            status = str(payload.get("status", "unknown"))
            ci_by_status[status] = ci_by_status.get(status, 0) + 1
            dur = payload.get("duration_sec")
            if isinstance(dur, (int, float)):
                ci_durations.append(int(dur))

            # 3-stage join (design note 3): run_id -> normalized PR -> unmatched
            if e["run_task_id"]:
                ci_matched_run_id += 1
                continue
            repo = payload.get("repo")
            pr = payload.get("pr")
            key = None
            if isinstance(repo, str) and isinstance(pr, (int, str)):
                try:
                    key = (repo.lower(), int(pr))
                except (TypeError, ValueError):
                    key = None
            if key is not None and key in pr_index:
                ci_matched_pr += 1
                continue
            ci_unmatched.append(
                {
                    "event_id": e["id"],
                    "occurred_at": e["occurred_at"],
                    "repo": repo,
                    "pr": pr,
                    "status": status,
                    "reason": (
                        "no run_id and no PR match"
                        if key is not None
                        else "no run_id and unparseable repo/pr payload"
                    ),
                }
            )

    avg_duration = (
        round(sum(ci_durations) / len(ci_durations), 1) if ci_durations else None
    )

    events = {
        "total": total_events,
        "by_kind": kind_counts,
        "redispatch": redispatch,
        "pane_close": pane_close,
        "worker_completed": worker_completed,
    }
    ci = {
        "total": ci_total,
        "by_status": ci_by_status,
        "matched_by_run_id": ci_matched_run_id,
        "matched_by_pr": ci_matched_pr,
        "unmatched_count": len(ci_unmatched),
        "unmatched": ci_unmatched,
        "avg_duration_sec": avg_duration,
    }
    return events, ci


def build_report(
    conn: sqlite3.Connection,
    *,
    lo: Optional[str],
    hi: Optional[str],
    pending_path: Path,
    generated_at: str,
    db_path: Path,
) -> dict[str, Any]:
    runs = gather_runs(conn, lo, hi)
    pr_index = gather_pr_index(conn)
    events, ci = gather_events_and_ci(conn, lo, hi, pr_index)
    pending = load_pending_decisions(pending_path)
    return {
        "generated_at": generated_at,
        "db_path": str(db_path),
        "period": {
            "since": lo,
            "until": hi,
            "inclusion_rule": "runs counted by dispatched_at; "
            "events/CI by occurred_at; bounds inclusive",
        },
        "runs": runs,
        "events": events,
        "ci": ci,
        "pending_decisions": pending,
    }


# =========================================================================
# Rendering
# =========================================================================
def _ascii(s: str) -> str:
    """Coerce a string to pure ASCII so it is safe on a cp932 console.

    DB-derived values (project slugs, PR repos, payload fragments) can hold
    arbitrary Unicode -- emoji, CJK, en/em dashes. The JSON renderer escapes
    these via ``ensure_ascii=True``; the Markdown renderer routes its final
    output through here so a stray non-cp932 character becomes a visible
    ``\\u....`` escape instead of crashing ``print`` with UnicodeEncodeError.
    """
    return s.encode("ascii", "backslashreplace").decode("ascii")


def _fmt_pct(rate: Optional[float]) -> str:
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def _fmt_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "  (none)"
    return "\n".join(
        f"  - {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )


def render_markdown(report: dict[str, Any]) -> str:
    p = report["period"]
    runs = report["runs"]
    events = report["events"]
    ci = report["ci"]
    pending = report["pending_decisions"]

    since = p["since"] or "(open)"
    until = p["until"] or "(open)"

    lines: list[str] = []
    lines.append("# Org metrics report")
    lines.append("")
    lines.append(f"- Generated at: {report['generated_at']}")
    lines.append(f"- DB: {report['db_path']}")
    lines.append(f"- Period: {since} .. {until}")
    lines.append(f"- Inclusion: {p['inclusion_rule']}")
    lines.append("")

    # Runs
    lines.append("## Runs")
    lines.append("")
    lines.append(f"- Dispatched in period: {runs['total']}")
    lines.append(
        f"- Live (queued/in_use/review): {runs['live']}  |  "
        f"Terminal (completed/failed/abandoned): {runs['terminal']}"
    )
    if runs["reserved"]:
        lines.append(
            f"- Reserved status rows (suspended, not classified): "
            f"{runs['reserved']}"
        )
    lines.append(
        f"- Completed: {runs['completed']}  "
        f"(completion rate: {_fmt_pct(runs['completion_rate'])})"
    )
    lines.append("- By status:")
    lines.append(_fmt_counts(runs["by_status"]))
    lines.append("- By project:")
    lines.append(_fmt_counts(runs["by_project"]))
    lines.append("- By pattern:")
    lines.append(_fmt_counts(runs["by_pattern"]))
    lines.append(f"- Runs with a PR: {runs['with_pr']}")
    lines.append("- By PR state:")
    lines.append(_fmt_counts(runs["by_pr_state"]))
    lines.append("")

    # Events
    lines.append("## Events")
    lines.append("")
    lines.append(f"- Total events: {events['total']}")
    lines.append(
        f"- Redispatch (delegate_resume/_r2): {events['redispatch']}"
    )
    lines.append(
        f"- Pane close (worker_closed/pane_closed): {events['pane_close']}"
    )
    lines.append(f"- Worker completed: {events['worker_completed']}")
    lines.append("- By kind:")
    lines.append(_fmt_counts(events["by_kind"]))
    lines.append("")

    # CI
    lines.append("## CI")
    lines.append("")
    lines.append(f"- ci_completed events: {ci['total']}")
    lines.append("- By status:")
    lines.append(_fmt_counts(ci["by_status"]))
    avg = ci["avg_duration_sec"]
    lines.append(
        f"- Avg duration: {'n/a' if avg is None else str(avg) + 's'}"
    )
    lines.append(
        f"- Joined to a run by run_id: {ci['matched_by_run_id']}  |  "
        f"by PR url/number: {ci['matched_by_pr']}  |  "
        f"unmatched: {ci['unmatched_count']}"
    )
    if ci["unmatched"]:
        lines.append("- Unmatched CI events (not dropped):")
        for u in ci["unmatched"]:
            lines.append(
                f"  - event {u['event_id']} @ {u['occurred_at']}: "
                f"repo={u['repo']!r} pr={u['pr']!r} status={u['status']} "
                f"({u['reason']})"
            )
    lines.append("")

    # Pending decisions
    lines.append("## Pending decisions")
    lines.append("")
    if pending is None:
        lines.append("- Register not available (file absent).")
    elif pending.get("error"):
        lines.append(f"- Register error: {pending['error']}")
    else:
        lines.append(f"- Pending: {pending['pending']}")
        lines.append(f"- Total entries: {pending['total']}")
        lines.append("- By status:")
        lines.append(_fmt_counts(pending["by_status"]))
    lines.append("")

    return _ascii("\n".join(lines))


def render_json(report: dict[str, Any]) -> str:
    # ensure_ascii=True keeps the output cp932-safe (non-ASCII project names
    # etc. are \u-escaped rather than emitted raw).
    return json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True)


# =========================================================================
# CLI
# =========================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/org_metrics_report.py",
        description=(
            "Read-only team metrics report from .state/state.db. Aggregates "
            "run / event / PR / CI activity over a period and prints Markdown "
            "or JSON. Runs are counted by dispatched_at (a run that STARTED "
            "in the window); events and CI by occurred_at. Bounds are "
            "inclusive. This tool never writes to the DB."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to state.db (default: $STATE_DB_PATH or "
        "<repo_root>/.state/state.db).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Lower bound (inclusive), ISO-8601. A bare YYYY-MM-DD means "
        "start of that day. Cannot be combined with --last-days.",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="Upper bound (inclusive), ISO-8601. A bare YYYY-MM-DD covers "
        "the whole day.",
    )
    parser.add_argument(
        "--last-days",
        type=int,
        default=None,
        metavar="N",
        help="Shortcut for since = now - N days (until defaults to now). "
        "Cannot be combined with --since.",
    )
    parser.add_argument(
        "--pending-decisions-path",
        default=None,
        help="Path to pending_decisions.json (default: sibling of the DB, "
        "i.e. <db dir>/pending_decisions.json). A missing file reports as "
        "'not available', kept distinct from 0 pending.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    return parser


def run(argv: Optional[list[str]] = None, *, now: Optional[datetime] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.last_days is not None and args.since is not None:
        parser.error("--last-days cannot be combined with --since")
    if args.last_days is not None and args.last_days < 0:
        parser.error("--last-days must be a non-negative integer")

    now = now or datetime.now(timezone.utc)
    generated_at = _fmt_ts(now)

    db_path = resolve_db_path(args.db_path)
    if not db_path.exists():
        sys.stderr.write(
            f"tools/org_metrics_report.py: error: state DB not found at "
            f"{db_path}. Pass --db-path or set STATE_DB_PATH.\n"
        )
        return 2

    if args.pending_decisions_path:
        pending_path = Path(args.pending_decisions_path).expanduser()
    else:
        pending_path = db_path.parent / "pending_decisions.json"

    lo, hi = compute_bounds(
        since=args.since,
        until=args.until,
        last_days=args.last_days,
        now=now,
    )

    conn = open_readonly(db_path)
    try:
        report = build_report(
            conn,
            lo=lo,
            hi=hi,
            pending_path=pending_path,
            generated_at=generated_at,
            db_path=db_path,
        )
    finally:
        conn.close()

    if args.format == "json":
        out = render_json(report)
    else:
        out = render_markdown(report)
    sys.stdout.write(out + "\n")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
