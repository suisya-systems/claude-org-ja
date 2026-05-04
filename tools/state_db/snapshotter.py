"""Post-commit DB → markdown / jsonl regenerator (M2, Issue #267).

The M2 design (migration-strategy.md §M2) inverts the SoT: writes land in
SQLite first; ``.state/org-state.md`` and ``.state/journal.jsonl`` become
DB-derived dumps refreshed after each commit. This module is the dumper.

Two design choices worth knowing about:

1. **Passthrough merge for org-state.md.** The schema only models a known
   set of structured fields (Status / Updated / Dispatcher / Curator /
   Worker Directory Registry / Active Work Items / Resume Instructions /
   …). The real `.state/org-state.md` carries hundreds of lines of
   free-form session notes that the schema deliberately doesn't model.
   To avoid wiping that human-curated history when secretary first calls
   the snapshotter, we render the structured prefix from the DB *and then
   pass through unknown ``## …`` sections from the existing markdown in
   their original order*. The notes/ split (M4) will eventually retire
   the passthrough; M2 just protects the data.

2. **Atomic-rename writes.** Each output is written to a sibling
   ``<file>.tmp`` and ``os.replace()``-d into place after fsync. A crashed
   regenerate run leaves the previous good copy in place; the next call
   reproduces the same bytes from the same DB state (idempotency).
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Heading taxonomy
# ---------------------------------------------------------------------------

# ``## …`` headings (lower-cased, exact-match) that the snapshotter owns.
# Anything else is treated as free-form and passed through.
#
# Exact match — not substring — on purpose: substring would silently
# absorb headings like "## Dispatcher Notes" or "## Curator メモ" into the
# DB-owned set and the snapshotter would drop them on regenerate (and
# drift_check would not detect the loss because the same predicate gates
# both sides). See cross-review M1.
_STRUCTURED_HEADINGS: frozenset[str] = frozenset({
    "dispatcher",
    "curator",
    "worker directory registry",
    "resume instructions",
})
# Cross-review M2.1 (Issue #272): ``Active Work Items`` was previously
# DB-owned but the importer routes legacy free-form bullets like
# ``- task-id: COMPLETED (PR #N merged, …)`` to ``events`` (kind
# ``legacy_active_item``), not to ``runs``. After M2.1 wires the
# post-commit regenerate, treating the heading as structured silently
# erases every COMPLETED / ABANDONED entry the operator curated by hand
# on the next call. We therefore demote the heading to passthrough so
# the existing free-form list survives. The DB still tracks live runs
# via the ``runs`` table — dashboards read it directly via queries.py;
# they do not parse the markdown — so demoting this heading does not
# change live-state visibility.


def _is_structured_heading(heading_text: str) -> bool:
    return heading_text.strip().lower() in _STRUCTURED_HEADINGS


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, body: str) -> None:
    """Write `body` to `path` via tmp → fsync → rename.

    The tmp file is created in the same directory so ``os.replace`` is a
    same-volume rename (atomic on Windows + POSIX). A crash mid-write
    leaves the previous good copy in place.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync isn't supported on every FS (e.g. some CI tmpfs);
                # the rename is still atomic, so degrade silently.
                pass
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Structured-section rendering
# ---------------------------------------------------------------------------


def _fetch_session(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM org_sessions WHERE id = 1").fetchone()
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def _fetch_runs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            r.task_id, r.pattern, r.status, r.title, r.pr_url, r.pr_state,
            r.outcome_note, r.dispatched_at, r.completed_at,
            p.slug AS project_slug,
            d.abs_path AS worker_dir
        FROM runs r
        JOIN projects p             ON p.id = r.project_id
        LEFT JOIN worker_dirs d     ON d.id = r.worker_dir_id
        ORDER BY r.dispatched_at, r.id
        """
    ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def _render_header(session: dict) -> str:
    """Top-of-file ``# Org State`` block + bare key:value lines."""
    out = io.StringIO()
    out.write("# Org State\n\n")
    status = session.get("status") or "IDLE"
    out.write(f"Status: {status}\n")
    for label, key in (
        ("Started", "started_at"),
        ("Updated", "updated_at"),
        ("Suspended", "suspended_at"),
        ("Resumed", "resumed_at"),
        ("Current Objective", "objective"),
    ):
        v = session.get(key)
        if v:
            out.write(f"{label}: {v}\n")
    out.write("\n")
    return out.getvalue()


def _render_role_section(label: str, peer_id, pane_id) -> str:
    if not peer_id and not pane_id:
        return ""
    out = io.StringIO()
    out.write(f"## {label}\n")
    if peer_id:
        out.write(f"- Peer ID: {peer_id}\n")
    if pane_id:
        out.write(f"- Pane ID: {pane_id}\n")
    out.write("\n")
    return out.getvalue()


def _render_worker_directory_registry(runs: Iterable[dict]) -> str:
    runs = [r for r in runs if r.get("worker_dir")]
    if not runs:
        return ""
    out = io.StringIO()
    out.write("## Worker Directory Registry\n\n")
    out.write("| Task ID | Pattern | Directory | Project | Status |\n")
    out.write("|---|---|---|---|---|\n")
    for r in runs:
        status_cell = r.get("outcome_note") or r.get("status") or ""
        # Pipe characters in cells would corrupt the table; quote them.
        status_cell = str(status_cell).replace("|", "\\|").replace("\n", " ")
        title_or_dir = r.get("worker_dir") or ""
        out.write(
            f"| {r['task_id']} | {r['pattern']} | {title_or_dir} | "
            f"{r.get('project_slug') or ''} | {status_cell} |\n"
        )
    out.write("\n")
    return out.getvalue()


_DB_STATUS_TO_MD_LABEL = {
    "in_use": "IN_PROGRESS",
    "review": "REVIEW",
    "queued": "PENDING",
    "completed": "COMPLETED",
    "failed": "BLOCKED",
    "suspended": "PENDING",
    "abandoned": "ABANDONED",
}


def _render_active_work_items(runs: Iterable[dict]) -> str:
    active = [r for r in runs if (r.get("status") or "") in ("in_use", "review")]
    if not active:
        return ""
    out = io.StringIO()
    out.write("## Active Work Items\n\n")
    for r in active:
        # Match the legacy "- task-id: title [STATUS]" convention parsed by
        # dashboard/server.py and dashboard/org_state_converter.py. Use the
        # frontend's status vocabulary (IN_PROGRESS / REVIEW / …) so a regen
        # → markdown-fallback cycle doesn't render as `?` in the UI.
        raw = (r.get("status") or "").lower()
        status_label = _DB_STATUS_TO_MD_LABEL.get(raw, raw.upper())
        title = r.get("title") or r["task_id"]
        out.write(f"- {r['task_id']}: {title} [{status_label}]\n")
    out.write("\n")
    return out.getvalue()


def _render_resume_instructions(session: dict) -> str:
    body = (session.get("resume_instructions") or "").strip()
    if not body:
        return ""
    out = io.StringIO()
    out.write("## Resume Instructions\n\n")
    out.write(body)
    if not body.endswith("\n"):
        out.write("\n")
    out.write("\n")
    return out.getvalue()


def render_structured_markdown(conn: sqlite3.Connection) -> str:
    """Build the DB-owned portion of org-state.md.

    Order matches the legacy file: header keys → Dispatcher → Curator →
    Worker Directory Registry → Active Work Items → Resume Instructions.
    Empty sections are omitted entirely.
    """
    session = _fetch_session(conn)
    runs = _fetch_runs(conn)
    parts = [
        _render_header(session),
        _render_role_section(
            "Dispatcher",
            session.get("dispatcher_peer_id"),
            session.get("dispatcher_pane_id"),
        ),
        _render_role_section(
            "Curator",
            session.get("curator_peer_id"),
            session.get("curator_pane_id"),
        ),
        _render_worker_directory_registry(runs),
        # ``## Active Work Items`` is intentionally NOT rendered here;
        # see the passthrough rationale next to ``_STRUCTURED_HEADINGS``.
        # The helper ``_render_active_work_items`` is kept for callers
        # that want a DB-only view (none in tree at the moment).
        _render_resume_instructions(session),
    ]
    return "".join(parts)


# ---------------------------------------------------------------------------
# Passthrough merge
# ---------------------------------------------------------------------------


def extract_unknown_sections(markdown_text: str) -> str:
    """Return the concatenated text of every ``## …`` block whose heading
    does NOT match the structured taxonomy.

    Each block runs from its ``## …`` heading line through (but not
    including) the next ``## …`` heading or end-of-file. The leading
    ``# Org State`` and the bare key:value lines above the first ``##`` are
    discarded — those are owned by the structured renderer.
    """
    out_parts: list[str] = []
    lines = markdown_text.splitlines(keepends=True)
    cur_section: Optional[list[str]] = None
    cur_keep = False
    for line in lines:
        if line.startswith("## "):
            if cur_section is not None and cur_keep:
                out_parts.append("".join(cur_section))
            heading = line[3:].strip()
            cur_keep = not _is_structured_heading(heading)
            cur_section = [line]
            continue
        if cur_section is not None:
            cur_section.append(line)
    if cur_section is not None and cur_keep:
        out_parts.append("".join(cur_section))
    return "".join(out_parts)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def render_org_state_md(
    conn: sqlite3.Connection, *, source_md: Optional[Path] = None
) -> str:
    """Build the full org-state.md body (structured + passthrough merge).

    `source_md` is read for passthrough preservation; if it's missing or
    None, the output is structured-only.
    """
    body = render_structured_markdown(conn)
    if source_md is not None:
        try:
            source_text = Path(source_md).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            source_text = ""
        passthrough = extract_unknown_sections(source_text)
        if passthrough:
            if not body.endswith("\n\n"):
                if body.endswith("\n"):
                    body += "\n"
                else:
                    body += "\n\n"
            body += passthrough
    return body


def regenerate_org_state_md(
    conn: sqlite3.Connection, out_path: Path,
    *, source_md: Optional[Path] = None,
) -> None:
    """Write a fresh org-state.md to `out_path` (atomic rename).

    Defaults to using `out_path` itself as the passthrough source — we
    overwrite the file in place but read its old content first to capture
    any free-form sections.
    """
    out_path = Path(out_path)
    if source_md is None and out_path.exists():
        source_md = out_path
    body = render_org_state_md(conn, source_md=source_md)
    _atomic_write_text(out_path, body)


def render_journal_jsonl(conn: sqlite3.Connection) -> str:
    """Build journal.jsonl from the events table, ts ascending."""
    rows = conn.execute(
        "SELECT occurred_at, kind, actor, payload_json "
        "FROM events ORDER BY occurred_at, id"
    ).fetchall()
    lines: list[str] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"_raw_payload": payload}
        out: dict = {}
        if r["occurred_at"]:
            out["ts"] = r["occurred_at"]
        out["event"] = r["kind"]
        if r["actor"]:
            out["actor"] = r["actor"]
        for k, v in payload.items():
            # Don't let payload override the canonical ts/event/actor keys.
            if k in ("ts", "event", "actor") and k in out:
                continue
            out[k] = v
        lines.append(json.dumps(out, ensure_ascii=False, sort_keys=True))
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def regenerate_journal_jsonl(
    conn: sqlite3.Connection, out_path: Path
) -> None:
    body = render_journal_jsonl(conn)
    _atomic_write_text(out_path, body)


def post_commit_regenerate(
    conn: sqlite3.Connection, claude_org_root: Path
) -> None:
    """Convenience wrapper: regenerate both files at the canonical paths.

    Failures are not silently swallowed — the caller should handle them
    (typically by logging and continuing; the DB COMMIT already happened).
    """
    state_dir = Path(claude_org_root) / ".state"
    regenerate_org_state_md(conn, state_dir / "org-state.md")
    regenerate_journal_jsonl(conn, state_dir / "journal.jsonl")


__all__ = [
    "render_structured_markdown",
    "render_org_state_md",
    "render_journal_jsonl",
    "regenerate_org_state_md",
    "regenerate_journal_jsonl",
    "post_commit_regenerate",
    "extract_unknown_sections",
]
