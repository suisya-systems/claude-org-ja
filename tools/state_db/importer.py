"""Full-rebuild importer for the M0 shadow DB.

Reads existing markdown / JSONL state and rebuilds `.state/state.db` from
scratch. The importer is intentionally idempotent: any second invocation
against the same inputs drops every table, re-applies the schema, and
re-inserts identical rows — `dump_signature()` (sha256 over a deterministic
text rendering) is therefore reproducible.

The "no row dropped" invariant from migration-strategy.md §M0 DoD is upheld
by routing every input line we cannot parse into the `unparsed_legacy`
table instead of raising.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

from . import apply_schema, connect


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class ImportSummary:
    projects_inserted: int = 0
    workstreams_inserted: int = 0
    worker_dirs_inserted: int = 0
    runs_inserted: int = 0
    events_inserted: int = 0
    unparsed_inserted: int = 0
    input_lines_total: int = 0
    dump_sha256: str = ""
    inputs_found: list[str] = field(default_factory=list)
    inputs_missing: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return (
            self.projects_inserted
            + self.workstreams_inserted
            + self.worker_dirs_inserted
            + self.runs_inserted
            + self.events_inserted
            + self.unparsed_inserted
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_rows"] = self.total_rows
        return d


# ---------------------------------------------------------------------------
# Status keyword mapping (org-state.md → runs.status enum)
# ---------------------------------------------------------------------------

# Keys are checked as lowercased substrings of the raw status cell.
_STATUS_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("merged", "completed"),
    ("closed", "completed"),
    ("completed", "completed"),
    ("review", "review"),
    ("draft", "review"),
    ("open", "in_use"),
    ("in_use", "in_use"),
    ("in use", "in_use"),
    ("available", "in_use"),
    ("queued", "queued"),
    ("pending", "queued"),
    ("suspended", "suspended"),
    ("paused", "suspended"),
    ("failed", "failed"),
    ("abandoned", "abandoned"),
)


def _lifecycle_for_inventory_tier(tier: Optional[str]) -> str:
    """Map inventory.json `proposed_classification.tier` → worker_dirs.lifecycle.

    SoT: directory-layout.md §5 H5 lifecycle table. archive_candidate must
    flow into delete_pending (not active) so curator --purge can pick it
    up; archived stays archived; scratch stays scratch; everything else
    (run / project / unknown) defaults to active.
    """
    return {
        "scratch": "scratch",
        "archive_candidate": "delete_pending",
        "archive": "archived",
        "archived": "archived",
    }.get(tier or "", "active")


def _map_status(raw: str) -> str:
    s = raw.lower()
    for kw, mapped in _STATUS_KEYWORDS:
        if kw in s:
            return mapped
    return "in_use"


def _extract_pr_state(raw: str) -> Optional[str]:
    s = raw.lower()
    if "merged" in s:
        return "merged"
    if "closed" in s:
        return "closed"
    if "draft" in s:
        return "draft"
    if "open" in s or "review" in s:
        return "open"
    return None


# ---------------------------------------------------------------------------
# Importer core
# ---------------------------------------------------------------------------


class _Importer:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.summary = ImportSummary()
        # caches keyed by natural id
        self._project_id: dict[str, int] = {}
        self._worker_dir_id: dict[str, int] = {}

    # -- projects ---------------------------------------------------------

    def _ensure_project(self, slug: str, *, display_name: Optional[str] = None,
                         origin_url: Optional[str] = None,
                         notes: Optional[str] = None,
                         status: str = "active") -> int:
        slug = slug.strip() or "_unknown"
        if slug in self._project_id:
            return self._project_id[slug]
        cur = self.conn.execute(
            "INSERT INTO projects (slug, display_name, origin_url, status, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (slug, display_name or slug, origin_url, status, notes),
        )
        pid = cur.lastrowid
        self._project_id[slug] = pid
        self.summary.projects_inserted += 1
        return pid

    # -- worker_dirs ------------------------------------------------------

    def _ensure_worker_dir(self, abs_path: str, *, layout: str = "flat",
                            is_git_repo: int = 0, is_worktree: int = 0,
                            origin_url: Optional[str] = None,
                            current_branch: Optional[str] = None,
                            size_mb: Optional[float] = None,
                            lifecycle: str = "active") -> int:
        abs_path = abs_path.strip().rstrip("/")
        if abs_path in self._worker_dir_id:
            return self._worker_dir_id[abs_path]
        cur = self.conn.execute(
            "INSERT INTO worker_dirs (abs_path, layout, is_git_repo, is_worktree, "
            "origin_url, current_branch, size_mb, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (abs_path, layout, is_git_repo, is_worktree, origin_url,
             current_branch, size_mb, lifecycle),
        )
        wid = cur.lastrowid
        self._worker_dir_id[abs_path] = wid
        self.summary.worker_dirs_inserted += 1
        return wid

    # -- unparsed legacy --------------------------------------------------

    def _record_unparsed(self, source: str, line_no: Optional[int], raw: str,
                          reason: str) -> None:
        self.conn.execute(
            "INSERT INTO unparsed_legacy (source, source_line, raw, reason) "
            "VALUES (?, ?, ?, ?)",
            (source, line_no, raw, reason),
        )
        self.summary.unparsed_inserted += 1

    # -- registry/projects.md --------------------------------------------

    _PROJ_ROW_RE = re.compile(r"^\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]*)\|\s*$")

    def import_projects_md(self, path: Path) -> None:
        if not path or not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        in_table = False
        for i, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.rstrip()
            if not line.startswith("|"):
                in_table = False
                continue
            self.summary.input_lines_total += 1
            # separator row → record so the line is accounted for.
            if re.match(r"^\|\s*-+", line):
                in_table = True
                self._record_unparsed("projects.md", i, raw_line,
                                       "table separator")
                continue
            m = self._PROJ_ROW_RE.match(line)
            if not m:
                self._record_unparsed("projects.md", i, raw_line,
                                       "row regex mismatch")
                continue
            cells = [c.strip() for c in m.groups()]
            common, project_slug, src, desc, _examples = cells
            if not in_table or project_slug.lower() in ("プロジェクト名", "name", "project"):
                # header row — keep counted but mark as schema row
                self._record_unparsed("projects.md", i, raw_line, "header row")
                continue
            origin_url = src if src.startswith("http") else None
            self._ensure_project(
                project_slug,
                display_name=common or project_slug,
                origin_url=origin_url,
                notes=desc or None,
            )

    # -- inventory.json ---------------------------------------------------

    def import_inventory_json(self, path: Path) -> None:
        if not path or not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data:
            self.summary.input_lines_total += 1
            abs_path = entry.get("abs_path") or entry.get("path")
            if not abs_path:
                self._record_unparsed("inventory.json", None,
                                       json.dumps(entry, ensure_ascii=False),
                                       "missing abs_path")
                continue
            git = entry.get("git") or {}
            self._ensure_worker_dir(
                abs_path,
                layout="flat",
                is_git_repo=1 if git.get("is_repo") else 0,
                is_worktree=1 if git.get("is_worktree") else 0,
                origin_url=git.get("origin_url"),
                current_branch=git.get("current_branch"),
                size_mb=entry.get("size_mb"),
                lifecycle=_lifecycle_for_inventory_tier(
                    (entry.get("proposed_classification") or {}).get("tier")
                ),
            )

    # -- org-state.md / Worker Directory Registry ------------------------

    _WDR_ROW_RE = re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*([ABCD])\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$"
    )

    # M2: org_sessions singleton overlay fields. The importer extracts these
    # from the markdown header / Dispatcher / Curator / Resume Instructions
    # sections so a freshly rebuilt DB reproduces the M1 markdown overlay
    # values without going through the markdown layer at read time.
    def _import_org_session_from_md(self, text: str) -> None:
        status: Optional[str] = None
        started_at: Optional[str] = None
        updated_at: Optional[str] = None
        suspended_at: Optional[str] = None
        resumed_at: Optional[str] = None
        objective: Optional[str] = None
        dispatcher_pane_id: Optional[str] = None
        dispatcher_peer_id: Optional[str] = None
        curator_pane_id: Optional[str] = None
        curator_peer_id: Optional[str] = None
        resume_instructions: Optional[str] = None

        section: Optional[str] = None
        current_role: Optional[str] = None
        resume_lines: list[str] = []

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if line.startswith("## "):
                heading = line[3:].strip().lower()
                if section == "resume instructions":
                    body = "\n".join(resume_lines).strip()
                    if body:
                        resume_instructions = body
                    resume_lines = []
                section = heading
                current_role = None
                if "dispatcher" in heading:
                    current_role = "dispatcher"
                elif "curator" in heading:
                    current_role = "curator"
                continue

            if section == "resume instructions":
                resume_lines.append(line)
                continue

            if line.startswith("Status:") and status is None:
                v = line[len("Status:"):].strip()
                if v:
                    status = v.upper().split()[0]
                continue
            if line.startswith("Started:") and started_at is None:
                v = line[len("Started:"):].strip()
                if v:
                    started_at = v
                continue
            if line.startswith("Updated:") and updated_at is None:
                v = line[len("Updated:"):].strip()
                if v:
                    updated_at = v
                continue
            if line.startswith("Suspended:") and suspended_at is None:
                v = line[len("Suspended:"):].strip()
                if v:
                    suspended_at = v
                continue
            if line.startswith("Resumed:") and resumed_at is None:
                v = line[len("Resumed:"):].strip()
                if v:
                    resumed_at = v
                continue
            if line.startswith("Current Objective:") and objective is None:
                v = line[len("Current Objective:"):].strip()
                if v:
                    objective = v
                continue

            if current_role:
                m = re.match(r"^-?\s*Peer\s+ID:\s*(\S+)", line, re.IGNORECASE)
                if m:
                    if current_role == "dispatcher":
                        dispatcher_peer_id = m.group(1)
                    else:
                        curator_peer_id = m.group(1)
                    continue
                m = re.match(r"^-?\s*Pane\s+ID:\s*(\S+)", line, re.IGNORECASE)
                if m:
                    if current_role == "dispatcher":
                        dispatcher_pane_id = m.group(1)
                    else:
                        curator_pane_id = m.group(1)
                    continue

        if section == "resume instructions":
            body = "\n".join(resume_lines).strip()
            if body:
                resume_instructions = body

        # Always insert a singleton row so post-M2 code can rely on its
        # existence even when the source markdown had no Status header.
        self.conn.execute(
            "INSERT INTO org_sessions ("
            "id, status, started_at, updated_at, suspended_at, resumed_at, "
            "objective, resume_instructions, dispatcher_pane_id, "
            "dispatcher_peer_id, curator_pane_id, curator_peer_id, "
            "last_writer_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (status or "IDLE"),
                started_at, updated_at, suspended_at, resumed_at,
                objective, resume_instructions,
                dispatcher_pane_id, dispatcher_peer_id,
                curator_pane_id, curator_peer_id,
                # Sentinel timestamp keeps dump_signature reproducible.
                "2000-01-01T00:00:00.000Z",
            ),
        )

    def import_org_state_md(self, path: Path) -> None:
        if not path or not path.exists():
            # Even with no markdown, install a default singleton so callers
            # never have to handle a missing org_sessions row.
            self.conn.execute(
                "INSERT INTO org_sessions (id, status, last_writer_at) "
                "VALUES (1, 'IDLE', '2000-01-01T00:00:00.000Z')"
            )
            return
        text = path.read_text(encoding="utf-8")
        self._import_org_session_from_md(text)
        section: Optional[str] = None
        for i, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.rstrip()
            if line.startswith("## "):
                heading = line[3:].strip()
                if "Worker Directory Registry" in heading:
                    section = "wdr"
                elif "Active Work Items" in heading:
                    section = "active"
                elif "直近完了" in heading:
                    section = "recent"
                else:
                    section = None
                continue
            if section is None:
                continue
            if not line.strip():
                continue

            if section == "wdr":
                self.summary.input_lines_total += 1
                if re.match(r"^\|\s*-+", line):
                    self._record_unparsed("org-state.md:wdr", i, raw_line,
                                           "table separator")
                    continue
                m = self._WDR_ROW_RE.match(line)
                if not m:
                    # Header or non-conforming row.
                    if "Task ID" in line:
                        # header row — counted but expected
                        self._record_unparsed("org-state.md:wdr", i, raw_line,
                                               "header row")
                    else:
                        self._record_unparsed("org-state.md:wdr", i, raw_line,
                                               "row regex mismatch")
                    continue
                task_id, pattern, directory, project, status_raw = (
                    g.strip() for g in m.groups()
                )
                project_slug = project if project and project != "-" else "_unknown"
                pid = self._ensure_project(project_slug)
                wdir_id = self._ensure_worker_dir(directory, layout="flat")
                run_status = _map_status(status_raw)
                pr_state = _extract_pr_state(status_raw)
                try:
                    self.conn.execute(
                        "INSERT INTO runs (task_id, project_id, pattern, title, "
                        "status, pr_state, worker_dir_id, outcome_note) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (task_id, pid, pattern, task_id, run_status, pr_state,
                         wdir_id, status_raw),
                    )
                    self.summary.runs_inserted += 1
                except sqlite3.IntegrityError as exc:
                    self._record_unparsed("org-state.md:wdr", i, raw_line,
                                           f"insert failed: {exc}")
            elif section in ("active", "recent"):
                if not line.lstrip().startswith("-"):
                    continue
                self.summary.input_lines_total += 1
                # Active / 直近完了 entries are free-form; record as events for
                # auditability rather than trying to map to runs in M0.
                payload = json.dumps({"section": section, "raw": line,
                                       "source_line": i},
                                      ensure_ascii=False)
                # Deterministic sentinel — these legacy bullets carry no real
                # timestamp; using NOW() would break dump_signature idempotency.
                self.conn.execute(
                    "INSERT INTO events (occurred_at, kind, actor, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    ("2000-01-01T00:00:00.000Z",
                     f"legacy_{section}_item", "importer", payload),
                )
                self.summary.events_inserted += 1

    # -- journal.jsonl ----------------------------------------------------

    def import_journal_jsonl(self, path: Path) -> None:
        if not path or not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for i, raw in enumerate(fh, start=1):
                raw = raw.rstrip("\n")
                if not raw.strip():
                    continue
                self.summary.input_lines_total += 1
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as exc:
                    self._record_unparsed("journal.jsonl", i, raw,
                                           f"json decode: {exc}")
                    continue
                kind = obj.get("event") or obj.get("kind") or "unknown"
                ts = obj.get("ts") or obj.get("occurred_at")
                actor = obj.get("actor")
                payload = json.dumps(obj, ensure_ascii=False, sort_keys=True)
                # Sentinel when the source line carries no timestamp — letting
                # SQLite's strftime('now') default fire would break dump_signature
                # idempotency on rebuild.
                occurred_at = ts if ts else "2000-01-01T00:00:00.000Z"
                self.conn.execute(
                    "INSERT INTO events (occurred_at, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (occurred_at, actor, kind, payload),
                )
                self.summary.events_inserted += 1


# ---------------------------------------------------------------------------
# Schema reset
# ---------------------------------------------------------------------------

_DROP_ORDER = (
    "tag_assignments", "tags", "events", "runs", "worker_dirs",
    "workstreams", "projects", "unparsed_legacy", "org_sessions",
    "schema_migrations",
)


def _reset_schema(conn: sqlite3.Connection) -> None:
    """Drop every project table (if present) and re-apply schema.sql."""
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in _DROP_ORDER:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    # Reset AUTOINCREMENT counters so rebuild is bit-identical.
    # sqlite_sequence is internal — row-clear, don't DROP.
    try:
        conn.execute("DELETE FROM sqlite_sequence")
    except sqlite3.OperationalError:
        pass  # table absent on a fresh DB
    conn.commit()
    apply_schema(conn)
    conn.execute("PRAGMA foreign_keys = ON")


# ---------------------------------------------------------------------------
# Deterministic dump → sha256
# ---------------------------------------------------------------------------

# (table, ORDER BY) pairs. Keep stable so dump_signature is reproducible.
_DUMP_TABLES: tuple[tuple[str, str], ...] = (
    ("projects", "slug"),
    ("workstreams", "project_id, slug"),
    ("worker_dirs", "abs_path"),
    ("runs", "task_id"),
    (
        "events",
        "COALESCE(occurred_at,''), kind, COALESCE(actor,''), payload_json, id",
    ),
    ("unparsed_legacy", "source, COALESCE(source_line,0), raw, id"),
    ("org_sessions", "id"),
)


def _dump_text(conn: sqlite3.Connection) -> str:
    """Render the DB to a deterministic text form for hashing."""
    out: list[str] = []
    for table, order in _DUMP_TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        # Skip volatile timestamp columns that change between runs.
        volatile = {"created_at", "applied_at", "opened_at", "dispatched_at",
                     "last_seen_at", "last_writer_at"}
        kept = [c for c in cols if c not in volatile]
        col_list = ", ".join(kept)
        out.append(f"# {table}")
        rows = conn.execute(f"SELECT {col_list} FROM {table} ORDER BY {order}")
        for row in rows:
            payload = {kept[i]: row[i] for i in range(len(kept))}
            out.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(out) + "\n"


def dump_signature(conn: sqlite3.Connection) -> str:
    """Return sha256 hex of the deterministic dump."""
    return hashlib.sha256(_dump_text(conn).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


class MissingInputsError(RuntimeError):
    """Raised when strict=True and one or more expected inputs are missing."""

    def __init__(self, missing: list[str]):
        super().__init__(
            "expected importer inputs missing: " + ", ".join(missing)
        )
        self.missing = missing


def import_full_rebuild(
    db_path: Path,
    claude_org_root: Path,
    *,
    inventory_json: Optional[Path] = None,
    strict: bool = True,
) -> ImportSummary:
    """Drop everything, re-apply schema, re-import from markdown / JSONL.

    Inputs are resolved relative to `claude_org_root`:
      - `registry/projects.md`
      - `.state/org-state.md`
      - `.state/journal.jsonl`
    `inventory_json` is read independently (defaults to None — tests may
    supply the design-phase fixture).

    With `strict=True` (default), a missing standard input raises
    `MissingInputsError` before any DB work is done. The shadow-DB DoD
    (migration-strategy.md §M0) requires that "no input row is silently
    dropped"; that contract cannot be honoured if the importer cannot
    distinguish "0 lines because empty" from "0 lines because path typo".
    Tests that intentionally exercise a partial fixture set should pass
    `strict=False`.
    """
    db_path = Path(db_path)
    claude_org_root = Path(claude_org_root)

    inputs = {
        "registry/projects.md": claude_org_root / "registry" / "projects.md",
        ".state/org-state.md": claude_org_root / ".state" / "org-state.md",
        ".state/journal.jsonl": claude_org_root / ".state" / "journal.jsonl",
    }
    if inventory_json is not None:
        inputs["inventory.json"] = Path(inventory_json)

    found, missing = [], []
    for label, path in inputs.items():
        # is_file() (not exists()) so a directory at the expected path is
        # treated as missing — defensive against typos / botched restores.
        (found if path.is_file() else missing).append(label)
    if strict and missing:
        raise MissingInputsError(missing)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        _reset_schema(conn)
        importer = _Importer(conn)
        importer.summary.inputs_found = found
        importer.summary.inputs_missing = missing
        # Order matters: projects first so runs FK resolves; worker_dirs
        # before runs so worker_dir_id resolves; events last.
        importer.import_projects_md(inputs["registry/projects.md"])
        if "inventory.json" in inputs:
            importer.import_inventory_json(inputs["inventory.json"])
        importer.import_org_state_md(inputs[".state/org-state.md"])
        importer.import_journal_jsonl(inputs[".state/journal.jsonl"])
        conn.commit()
        importer.summary.dump_sha256 = dump_signature(conn)
        return importer.summary
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.state_db.importer")
    p.add_argument("--db", required=True, type=Path,
                   help="Output SQLite path (e.g. .state/state.db)")
    p.add_argument("--root", type=Path, default=Path.cwd(),
                   help="claude-org repo root (defaults to CWD)")
    p.add_argument("--inventory", type=Path, default=None,
                   help="Optional inventory.json fixture")
    p.add_argument("--rebuild", action="store_true",
                   help="Required acknowledgement that the DB will be wiped")
    p.add_argument("--strict", dest="strict", action="store_true",
                   default=True,
                   help="Fail (exit != 0) if any expected input is missing (default)")
    p.add_argument("--no-strict", dest="strict", action="store_false",
                   help="Tolerate missing inputs (e.g. fixture-only smoke runs)")
    args = p.parse_args(argv)
    if not args.rebuild:
        print("error: --rebuild flag is required (this drops every table).",
              file=sys.stderr)
        return 2
    try:
        summary = import_full_rebuild(args.db, args.root,
                                       inventory_json=args.inventory,
                                       strict=args.strict)
    except MissingInputsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
