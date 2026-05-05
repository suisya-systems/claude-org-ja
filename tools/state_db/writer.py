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

import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


class _ClearSentinel:
    """Singleton marker that means "explicitly NULL this column".

    Used by :meth:`StateWriter.update_session` to distinguish "caller
    omitted this kwarg / passed None as a meaningless default" from
    "caller really wants to clear this column".
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return "StateWriter.CLEAR"


class StateWriter:
    """Connection-bound writer with explicit transaction control.

    Construct with an open ``sqlite3.Connection`` (preferably one returned
    by :func:`tools.state_db.connect` so that ``foreign_keys`` and
    ``busy_timeout`` PRAGMAs are already set). The writer re-asserts those
    PRAGMAs defensively because callers sometimes hand in a bare connection.
    """

    CLEAR = _ClearSentinel()

    def __init__(self, conn: sqlite3.Connection,
                 *, claude_org_root: Optional[Path] = None):
        self.conn = conn
        # Defensive: ensure project-wide PRAGMAs and row_factory even
        # when caller passed a bare sqlite3.connect() handle. The
        # ``sqlite3.Row`` row_factory is required by ``_detect_claude_org_root``
        # below (which addresses ``row["file"]``); a default tuple
        # row_factory would raise ``TypeError: tuple indices must be
        # integers``. Setting it here is idempotent for connections
        # opened via ``tools.state_db.connect``.
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        # Forward-migrate pre-M2 DBs in place: org_sessions may be missing
        # if this writer attaches to an M0 / M1 DB that was created before
        # the schema bump. ensure_m2_schema is idempotent and seeds the
        # singleton row when it's absent.
        from tools.state_db import ensure_m2_schema
        ensure_m2_schema(conn)
        # claude_org_root is the repo root that contains `.state/`. When
        # set, ``transaction()`` regenerates `.state/org-state.md` and
        # `.state/journal.jsonl` from the DB after each successful commit
        # (M2.1 post-commit hook). When None we attempt to auto-detect
        # from the connection's file path; failure is non-fatal — the
        # transaction still commits and the caller can still call
        # snapshotter.post_commit_regenerate manually.
        if claude_org_root is None:
            claude_org_root = _detect_claude_org_root(conn)
        self._claude_org_root: Optional[Path] = (
            Path(claude_org_root) if claude_org_root is not None else None
        )
        # Issue #284: task_ids whose run was just transitioned to
        # 'completed'. Drained on commit so we never move the worker
        # state file before the DB row is durable on disk.
        self._pending_worker_archives: list[str] = []

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
        # Issue #284: a rollback voids any 'completed' transitions made
        # during the aborted transaction, so drop their archive intents
        # too — otherwise the next commit on this writer would archive
        # files for runs that are not actually completed.
        self._pending_worker_archives.clear()

    # ------------------------------------------------------------------
    # Post-commit hooks (Issue #284)
    # ------------------------------------------------------------------

    def _regenerate_json_snapshot(self) -> None:
        """Regenerate ``.state/org-state.json`` from the DB after commit.

        Soft-fail: any exception is logged to stderr and swallowed so a
        converter bug never blocks a write that is already durable in
        the DB.
        """
        if self._claude_org_root is None:
            return
        try:
            # Lazy import: dashboard isn't a tools.state_db dependency at
            # module load time, and import-time failures (e.g. missing
            # optional package) must not poison the writer.
            from dashboard.org_state_converter import convert
            json_path = self._claude_org_root / ".state" / "org-state.json"
            db_path = self._claude_org_root / ".state" / "state.db"
            convert(json_path=json_path, db_path=db_path)
        except Exception as exc:
            sys.stderr.write(
                "tools.state_db.writer: post-commit JSON snapshot "
                f"regenerate failed ({type(exc).__name__}: {exc}); "
                "DB is committed, dashboard JSON will catch up on the "
                "next regenerate.\n"
            )

    def _drain_pending_worker_archives(self) -> None:
        """Move queued worker-state files into ``.state/workers/archive/``.

        Called after commit. Idempotent: missing source file is a no-op
        (re-completion of an already-archived run). The destination
        directory is lazily created. Uses ``os.replace`` so the move is
        atomic on Windows as well as POSIX.
        """
        if not self._pending_worker_archives:
            return
        pending, self._pending_worker_archives = (
            self._pending_worker_archives, []
        )
        if self._claude_org_root is None:
            return
        workers_dir = self._claude_org_root / ".state" / "workers"
        archive_dir = workers_dir / "archive"
        for task_id in pending:
            src = workers_dir / f"worker-{task_id}.md"
            if not src.exists():
                continue
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                dst = archive_dir / src.name
                os.replace(src, dst)
            except Exception as exc:
                sys.stderr.write(
                    "tools.state_db.writer: failed to archive "
                    f"{src} → {archive_dir}/ "
                    f"({type(exc).__name__}: {exc}); leaving in place.\n"
                )

    @contextlib.contextmanager
    def transaction(self) -> Iterator["StateWriter"]:
        """Context-manager wrapper: BEGIN → yield → COMMIT (+ post-commit hook).

        On normal exit the transaction is committed and, when
        ``claude_org_root`` is known, ``post_commit_regenerate`` is
        invoked to refresh `.state/org-state.md` + `.state/journal.jsonl`
        from the DB. Regenerate failures are logged to stderr and
        swallowed — the DB is the SoT, so the markdown / jsonl dump is
        best-effort. The caller's ``with`` block still completes
        normally.

        On exception the transaction is rolled back and the exception
        propagates; no regenerate is attempted.
        """
        self.begin()
        try:
            yield self
        except BaseException:
            self.rollback()
            raise
        self.commit()
        if self._claude_org_root is not None:
            try:
                # Imported lazily to keep writer ↔ snapshotter import
                # graph cycle-free at module load time.
                from tools.state_db.snapshotter import post_commit_regenerate
                post_commit_regenerate(self.conn, self._claude_org_root)
            except Exception as exc:
                sys.stderr.write(
                    "tools.state_db.writer: post-commit regenerate failed "
                    f"({type(exc).__name__}: {exc}); "
                    "DB is committed, markdown/jsonl will catch up on "
                    "the next regenerate.\n"
                )
            # Issue #284: regenerate the dashboard JSON snapshot
            # (.state/org-state.json) after the markdown dump. Sequential
            # (not parallel) and after markdown so the JSON observes the
            # same DB state. Soft-fail like markdown — converter bugs
            # must not block the already-committed write.
            self._regenerate_json_snapshot()
            # Issue #284: archive worker-state files for runs that this
            # transaction marked completed. Deferred to post-commit so we
            # never move the file before the row update is durable.
            self._drain_pending_worker_archives()

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
        Cross-review m2: pass ``status=None`` is treated as "caller did
        not supply this field" and is **skipped**, not written as NULL.
        This protects callers that pile up Optional[…] kwargs from
        accidentally NULL-clearing every column when none of them got
        a real value. To explicitly clear a column, use the dedicated
        sentinel :attr:`StateWriter.CLEAR` (``writer.update_session(
        status=StateWriter.CLEAR)``) or write raw SQL.
        Omitted kwargs are also untouched.
        """
        unknown = set(fields) - set(self._SESSION_FIELDS)
        if unknown:
            raise ValueError(
                f"unknown org_sessions field(s): {sorted(unknown)}; "
                f"expected one of {list(self._SESSION_FIELDS)}"
            )
        # Drop implicit-None entries; only explicit CLEAR or a real value
        # results in a column write.
        actual: dict[str, Any] = {}
        for k, v in fields.items():
            if v is None:
                continue
            actual[k] = None if v is self.CLEAR else v
        if not actual:
            # No-op but still bump last_writer_at so dashboards see a write.
            self.conn.execute(
                "UPDATE org_sessions SET "
                "last_writer_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id = 1"
            )
            return
        assigns = ", ".join(f"{k} = ?" for k in actual)
        values = [actual[k] for k in actual]
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
        layout: Optional[str] = None,
        is_git_repo: Optional[bool] = None,
        is_worktree: Optional[bool] = None,
        origin_url: Optional[str] = None,
        current_branch: Optional[str] = None,
        size_mb: Optional[float] = None,
        lifecycle: Optional[str] = None,
    ) -> int:
        """INSERT (or differential-UPDATE) a worker_dirs row by abs_path.

        Idempotent re-registration: the dispatcher / sweeper often
        re-announces dirs on startup. Only fields the caller explicitly
        passes are written on the UPDATE path so a status ping that omits
        ``is_git_repo`` doesn't silently flip it back to False.
        """
        existing = self.conn.execute(
            "SELECT id FROM worker_dirs WHERE abs_path = ?", (abs_path,)
        ).fetchone()
        if existing is None:
            cur = self.conn.execute(
                "INSERT INTO worker_dirs ("
                "abs_path, layout, is_git_repo, is_worktree, origin_url, "
                "current_branch, size_mb, lifecycle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (abs_path, layout or "flat",
                 int(bool(is_git_repo)) if is_git_repo is not None else 0,
                 int(bool(is_worktree)) if is_worktree is not None else 0,
                 origin_url, current_branch, size_mb,
                 lifecycle or "active"),
            )
            return cur.lastrowid

        sets: list[str] = []
        values: list = []

        def _maybe(col: str, val):
            if val is not None:
                sets.append(f"{col} = ?")
                values.append(val)

        _maybe("layout", layout)
        if is_git_repo is not None:
            sets.append("is_git_repo = ?")
            values.append(int(bool(is_git_repo)))
        if is_worktree is not None:
            sets.append("is_worktree = ?")
            values.append(int(bool(is_worktree)))
        _maybe("origin_url", origin_url)
        _maybe("current_branch", current_branch)
        _maybe("size_mb", size_mb)
        _maybe("lifecycle", lifecycle)
        sets.append("last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        values.append(abs_path)
        self.conn.execute(
            f"UPDATE worker_dirs SET {', '.join(sets)} WHERE abs_path = ?",
            values,
        )
        return int(existing["id"])

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

    # Sentinel — distinguishes "caller didn't pass this kwarg" from
    # "caller explicitly passed None" in upsert_run's differential update.
    _UNSET = object()

    def upsert_run(
        self,
        *,
        task_id: str,
        project_slug: str,
        pattern: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[str] = None,
        branch: Optional[str] = None,
        pr_url: Optional[str] = None,
        pr_state: Optional[str] = None,
        issue_refs: Optional[Iterable[str]] = None,
        verification: Optional[str] = None,
        worker_dir_abs_path: Optional[str] = None,
        commit_short: Optional[str] = None,
        commit_full: Optional[str] = None,
        outcome_note: Optional[str] = None,
        workstream_slug: Optional[str] = None,
    ) -> int:
        """Insert or update a run row keyed by ``task_id``. Returns runs.id.

        Update semantics: only fields the caller explicitly passes are
        overwritten. Omitted kwargs preserve the existing row's value;
        passing ``None`` is treated as "caller didn't supply" and also
        preserves the existing value. (Use raw SQL if you need to
        explicitly NULL a field — by design, this API never silently
        clears columns.)
        """
        project_id = self.ensure_project(project_slug)
        existing = self.conn.execute(
            "SELECT id FROM runs WHERE task_id = ?", (task_id,)
        ).fetchone()

        worker_dir_id = self._UNSET
        if worker_dir_abs_path is not None:
            wd = self.conn.execute(
                "SELECT id FROM worker_dirs WHERE abs_path = ?",
                (worker_dir_abs_path,),
            ).fetchone()
            worker_dir_id = int(wd["id"]) if wd else None

        workstream_id = self._UNSET
        if workstream_slug is not None:
            ws = self.conn.execute(
                "SELECT id FROM workstreams WHERE project_id = ? AND slug = ?",
                (project_id, workstream_slug),
            ).fetchone()
            workstream_id = int(ws["id"]) if ws else None

        issue_refs_json: object = self._UNSET
        if issue_refs is not None:
            issue_refs_json = json.dumps(list(issue_refs))

        if existing is None:
            cur = self.conn.execute(
                "INSERT INTO runs ("
                "task_id, project_id, workstream_id, pattern, title, status, "
                "branch, pr_url, pr_state, issue_refs, verification, "
                "worker_dir_id, commit_short, commit_full, outcome_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, project_id,
                 None if workstream_id is self._UNSET else workstream_id,
                 pattern or "B",
                 title or task_id,
                 status or "in_use",
                 branch, pr_url, pr_state,
                 None if issue_refs_json is self._UNSET else issue_refs_json,
                 verification or "standard",
                 None if worker_dir_id is self._UNSET else worker_dir_id,
                 commit_short, commit_full, outcome_note),
            )
            return cur.lastrowid

        # Update path: build the SET clause from supplied kwargs only so
        # omitted fields preserve their existing value.
        sets: list[str] = []
        values: list = []

        def _maybe(col: str, val):
            if val is not None and val is not self._UNSET:
                sets.append(f"{col} = ?")
                values.append(val)

        # project_id is always known (we just resolved it from project_slug)
        # and therefore safe to write.
        _maybe("project_id", project_id)
        _maybe("pattern", pattern)
        _maybe("title", title)
        _maybe("status", status)
        _maybe("branch", branch)
        _maybe("pr_url", pr_url)
        _maybe("pr_state", pr_state)
        _maybe("verification", verification)
        _maybe("commit_short", commit_short)
        _maybe("commit_full", commit_full)
        _maybe("outcome_note", outcome_note)
        if issue_refs_json is not self._UNSET:
            sets.append("issue_refs = ?")
            values.append(issue_refs_json)
        if worker_dir_id is not self._UNSET:
            sets.append("worker_dir_id = ?")
            values.append(worker_dir_id)
        if workstream_id is not self._UNSET:
            sets.append("workstream_id = ?")
            values.append(workstream_id)

        if sets:
            values.append(task_id)
            self.conn.execute(
                f"UPDATE runs SET {', '.join(sets)} WHERE task_id = ?",
                values,
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
        # Issue #284: when a run goes to 'completed', schedule its
        # worker-state file for archival. The actual move happens after
        # commit (see _drain_pending_worker_archives) so that a rollback
        # leaves the file in place. Idempotent: a second completed
        # transition just queues a second move attempt that no-ops when
        # the source file is already gone.
        if status == "completed":
            self._pending_worker_archives.append(task_id)

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


def _detect_claude_org_root(conn: sqlite3.Connection) -> Optional[Path]:
    """Best-effort: derive the claude-org repo root from a sqlite Connection.

    The convention is ``<root>/.state/state.db``; we walk up two levels
    from the connection's main database file. Returns None for
    ``:memory:`` connections, for connections opened against a path that
    is not under a ``.state/`` directory (most test fixtures), or when
    the PRAGMA query fails for any reason. A None return makes
    ``transaction()`` skip the post-commit hook silently — callers that
    care can pass ``claude_org_root`` explicitly.
    """
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    # ``sqlite3.Row`` exposes ``keys()``; tuple rows do not. ``StateWriter``
    # forces row_factory = sqlite3.Row defensively, so this guard mostly
    # protects callers that invoke ``_detect_claude_org_root`` against a
    # bare connection without going through ``StateWriter``.
    try:
        file_path = row["file"]
    except (IndexError, TypeError):
        return None
    if not file_path:
        return None
    p = Path(file_path).resolve()
    if p.parent.name == ".state":
        return p.parent.parent
    return None


__all__ = ["StateWriter"]
