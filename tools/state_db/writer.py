"""Thin DB direct-write API for the M2 write switch (Issue #267).

In M2 the SQLite DB at `.state/state.db` becomes the SoT for state writes.
Secretary / dispatcher / worker code is expected to call into a
``StateWriter`` instead of editing `.state/org-state.md` or appending to
`.state/journal.jsonl` by hand. The markdown / jsonl files are regenerated
post-commit by :mod:`tools.state_db.snapshotter` (one-way dump).

Design boundary (per CLAUDE.local.md / migration-strategy.md §M2):
- writer mutates DB rows only.
- writer never imports snapshotter; markdown regeneration is the caller's
  responsibility (typically via ``post_commit_regenerate``). This keeps the
  failure surface of writer narrow and avoids a circular dependency.
- writer wraps every public mutation in an explicit transaction. Callers
  may compose multiple mutations inside ``begin()`` / ``commit()`` instead.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Optional


class StateWriter:
    """Connection-bound writer with explicit transaction control.

    Construct with an open ``sqlite3.Connection`` (preferably one returned
    by :func:`tools.state_db.connect` so that ``foreign_keys`` and
    ``busy_timeout`` PRAGMAs are already set). The writer re-asserts those
    PRAGMAs defensively because callers sometimes hand in a bare connection.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        # Defensive: ensure project-wide PRAGMAs even when caller passed a
        # bare sqlite3.connect() handle.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        # Auto-create the org_sessions singleton if missing so update_session
        # can run on a freshly imported DB without a separate seed step.
        conn.execute(
            "INSERT OR IGNORE INTO org_sessions (id, status, last_writer_at) "
            "VALUES (1, 'IDLE', strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        )

    # ------------------------------------------------------------------
    # Transaction control
    # ------------------------------------------------------------------

    def begin(self) -> None:
        """Open an explicit transaction.

        sqlite3's default isolation level is "deferred" (= a transaction
        opens implicitly on the first DML); calling ``BEGIN`` directly
        would raise ``OperationalError: cannot start a transaction within
        a transaction`` if one is already open. We swallow that case so
        callers can defensively bracket their writes.
        """
        try:
            self.conn.execute("BEGIN")
        except sqlite3.OperationalError:
            pass

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # ------------------------------------------------------------------
    # org session (singleton)
    # ------------------------------------------------------------------

    _SESSION_FIELDS: tuple[str, ...] = (
        "status", "started_at", "updated_at", "suspended_at", "resumed_at",
        "objective", "resume_instructions",
        "dispatcher_pane_id", "dispatcher_peer_id",
        "curator_pane_id", "curator_peer_id",
    )

    def update_session(self, **fields: Any) -> None:
        """Patch the org_sessions singleton with the given keyword fields.

        Unknown keys raise ``ValueError`` to catch typos at write time.
        Pass ``status=None`` (or any other field set to None) explicitly
        to clear that column. Omitted fields are left untouched.
        """
        unknown = set(fields) - set(self._SESSION_FIELDS)
        if unknown:
            raise ValueError(
                f"unknown org_sessions field(s): {sorted(unknown)}; "
                f"expected one of {list(self._SESSION_FIELDS)}"
            )
        if not fields:
            # No-op but still bump last_writer_at so dashboards see a write.
            self.conn.execute(
                "UPDATE org_sessions SET "
                "last_writer_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id = 1"
            )
            return
        assigns = ", ".join(f"{k} = ?" for k in fields)
        values = [fields[k] for k in fields]
        self.conn.execute(
            f"UPDATE org_sessions SET {assigns}, "
            "last_writer_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = 1",
            values,
        )

    def get_session(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM org_sessions WHERE id = 1").fetchone()
        if row is None:
            return {}
        return {k: row[k] for k in row.keys()}

    # ------------------------------------------------------------------
    # worker_dirs
    # ------------------------------------------------------------------

    def register_worker_dir(
        self,
        *,
        abs_path: str,
        layout: str = "flat",
        is_git_repo: bool = False,
        is_worktree: bool = False,
        origin_url: Optional[str] = None,
        current_branch: Optional[str] = None,
        size_mb: Optional[float] = None,
        lifecycle: str = "active",
    ) -> int:
        """INSERT (or UPSERT) a worker_dirs row keyed by abs_path. Returns id.

        UPSERT keeps re-registration idempotent: the dispatcher / sweeper
        often re-announces dirs on startup and we don't want duplicates.
        """
        cur = self.conn.execute(
            "INSERT INTO worker_dirs ("
            "abs_path, layout, is_git_repo, is_worktree, origin_url, "
            "current_branch, size_mb, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(abs_path) DO UPDATE SET "
            "  layout = excluded.layout, "
            "  is_git_repo = excluded.is_git_repo, "
            "  is_worktree = excluded.is_worktree, "
            "  origin_url = COALESCE(excluded.origin_url, worker_dirs.origin_url), "
            "  current_branch = COALESCE(excluded.current_branch, worker_dirs.current_branch), "
            "  size_mb = COALESCE(excluded.size_mb, worker_dirs.size_mb), "
            "  lifecycle = excluded.lifecycle, "
            "  last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (abs_path, layout, int(is_git_repo), int(is_worktree),
             origin_url, current_branch, size_mb, lifecycle),
        )
        # cur.lastrowid is 0 on UPDATE-only path; resolve via SELECT.
        row = self.conn.execute(
            "SELECT id FROM worker_dirs WHERE abs_path = ?", (abs_path,)
        ).fetchone()
        return int(row["id"]) if row else cur.lastrowid

    def update_worker_dir_lifecycle(self, abs_path: str, lifecycle: str) -> None:
        """Move a worker dir to a different lifecycle bucket (active/archived/etc)."""
        self.conn.execute(
            "UPDATE worker_dirs SET lifecycle = ?, "
            "last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE abs_path = ?",
            (lifecycle, abs_path),
        )

    def remove_worker_dir(self, abs_path: str) -> None:
        """Physically delete a worker_dirs row. Reserved for curator batch.

        runs.worker_dir_id is ON DELETE SET NULL so existing run history is
        preserved; only the FS-level metadata disappears.
        """
        self.conn.execute("DELETE FROM worker_dirs WHERE abs_path = ?", (abs_path,))

    # ------------------------------------------------------------------
    # projects / workstreams (lookup helpers — no creation outside importer)
    # ------------------------------------------------------------------

    def ensure_project(self, slug: str, *, display_name: Optional[str] = None,
                       origin_url: Optional[str] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO projects (slug, display_name, origin_url) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET "
            "  display_name = COALESCE(excluded.display_name, projects.display_name), "
            "  origin_url = COALESCE(excluded.origin_url, projects.origin_url)",
            (slug, display_name or slug, origin_url),
        )
        row = self.conn.execute(
            "SELECT id FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
        return int(row["id"]) if row else cur.lastrowid

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------

    def upsert_run(
        self,
        *,
        task_id: str,
        project_slug: str,
        pattern: str,
        title: Optional[str] = None,
        status: str = "in_use",
        branch: Optional[str] = None,
        pr_url: Optional[str] = None,
        pr_state: Optional[str] = None,
        issue_refs: Optional[Iterable[str]] = None,
        verification: str = "standard",
        worker_dir_abs_path: Optional[str] = None,
        commit_short: Optional[str] = None,
        commit_full: Optional[str] = None,
        outcome_note: Optional[str] = None,
        workstream_slug: Optional[str] = None,
    ) -> int:
        """Insert or update a run row keyed by ``task_id``. Returns runs.id."""
        project_id = self.ensure_project(project_slug)
        workstream_id: Optional[int] = None
        if workstream_slug:
            ws = self.conn.execute(
                "SELECT id FROM workstreams WHERE project_id = ? AND slug = ?",
                (project_id, workstream_slug),
            ).fetchone()
            workstream_id = int(ws["id"]) if ws else None
        worker_dir_id: Optional[int] = None
        if worker_dir_abs_path:
            wd = self.conn.execute(
                "SELECT id FROM worker_dirs WHERE abs_path = ?",
                (worker_dir_abs_path,),
            ).fetchone()
            worker_dir_id = int(wd["id"]) if wd else None

        issue_refs_json = json.dumps(list(issue_refs)) if issue_refs else None
        title = title or task_id

        existing = self.conn.execute(
            "SELECT id FROM runs WHERE task_id = ?", (task_id,)
        ).fetchone()
        if existing is None:
            cur = self.conn.execute(
                "INSERT INTO runs ("
                "task_id, project_id, workstream_id, pattern, title, status, "
                "branch, pr_url, pr_state, issue_refs, verification, "
                "worker_dir_id, commit_short, commit_full, outcome_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, project_id, workstream_id, pattern, title, status,
                 branch, pr_url, pr_state, issue_refs_json, verification,
                 worker_dir_id, commit_short, commit_full, outcome_note),
            )
            return cur.lastrowid
        # Update path: only overwrite columns the caller explicitly passed;
        # use COALESCE on optional fields so e.g. updating status doesn't
        # null out a previously-set pr_url.
        self.conn.execute(
            "UPDATE runs SET "
            "  project_id = ?, workstream_id = ?, pattern = ?, title = ?, "
            "  status = ?, "
            "  branch = COALESCE(?, branch), "
            "  pr_url = COALESCE(?, pr_url), "
            "  pr_state = COALESCE(?, pr_state), "
            "  issue_refs = COALESCE(?, issue_refs), "
            "  verification = ?, "
            "  worker_dir_id = COALESCE(?, worker_dir_id), "
            "  commit_short = COALESCE(?, commit_short), "
            "  commit_full = COALESCE(?, commit_full), "
            "  outcome_note = COALESCE(?, outcome_note) "
            "WHERE task_id = ?",
            (project_id, workstream_id, pattern, title, status,
             branch, pr_url, pr_state, issue_refs_json, verification,
             worker_dir_id, commit_short, commit_full, outcome_note,
             task_id),
        )
        return int(existing["id"])

    def update_run_status(
        self,
        task_id: str,
        status: str,
        *,
        completed_at: Optional[str] = None,
        outcome_note: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, "
            "  completed_at = COALESCE(?, completed_at), "
            "  outcome_note = COALESCE(?, outcome_note) "
            "WHERE task_id = ?",
            (status, completed_at, outcome_note, task_id),
        )

    # ------------------------------------------------------------------
    # events (append-only journal)
    # ------------------------------------------------------------------

    def append_event(
        self,
        *,
        kind: str,
        actor: Optional[str] = None,
        payload: Optional[dict] = None,
        occurred_at: Optional[str] = None,
        run_task_id: Optional[str] = None,
        project_slug: Optional[str] = None,
        workstream_slug: Optional[str] = None,
    ) -> int:
        """Insert one event row. Returns events.id.

        ``payload`` is JSON-encoded; if you need to round-trip the original
        kind/ts/actor of a legacy line, include them inside payload as well.
        """
        run_id: Optional[int] = None
        project_id: Optional[int] = None
        workstream_id: Optional[int] = None
        if run_task_id:
            row = self.conn.execute(
                "SELECT id, project_id, workstream_id FROM runs WHERE task_id = ?",
                (run_task_id,),
            ).fetchone()
            if row is not None:
                run_id = int(row["id"])
                project_id = int(row["project_id"])
                if row["workstream_id"] is not None:
                    workstream_id = int(row["workstream_id"])
        if project_slug and project_id is None:
            row = self.conn.execute(
                "SELECT id FROM projects WHERE slug = ?", (project_slug,)
            ).fetchone()
            if row is not None:
                project_id = int(row["id"])
        if workstream_slug and workstream_id is None and project_id is not None:
            row = self.conn.execute(
                "SELECT id FROM workstreams WHERE project_id = ? AND slug = ?",
                (project_id, workstream_slug),
            ).fetchone()
            if row is not None:
                workstream_id = int(row["id"])

        payload_json = json.dumps(
            payload or {}, ensure_ascii=False, sort_keys=True
        )
        if occurred_at:
            cur = self.conn.execute(
                "INSERT INTO events ("
                "occurred_at, actor, kind, run_id, workstream_id, project_id, "
                "payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (occurred_at, actor, kind, run_id, workstream_id, project_id,
                 payload_json),
            )
        else:
            cur = self.conn.execute(
                "INSERT INTO events ("
                "actor, kind, run_id, workstream_id, project_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (actor, kind, run_id, workstream_id, project_id, payload_json),
            )
        return cur.lastrowid


__all__ = ["StateWriter"]
