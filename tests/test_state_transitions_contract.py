"""Contract tests for canonical state semantics (Issue #355 / Epic #357).

These tests lock the operational contract documented in
``docs/contracts/state-semantics-contract.md`` (Set F). They are written
contract-style: each test case is anchored to a numbered section /
invariant in the contract document, and a regression in the run-status
lifecycle, the read-side projections, or the post-commit hooks must
fail one of these tests fast.

Scope (per Issue #355):
- (1) ``queued -> in_use -> review -> completed`` lifecycle coverage
- (2) ``failed`` / ``abandoned`` terminal states accepted by the writer
- (3) post-commit worker-state-file archive move on T5 close
- (4) post-commit derived-artifact regen failure is treated as
       soft-failure (DB commit survives, writer does not raise)
- (5) DB state vs. read / query / dashboard summary path coherence

The tests deliberately re-anchor on the *contract* rather than on
implementation internals; they use only the public ``StateWriter``
mutation entry points (``upsert_run`` / ``update_run_status``) so that a
future re-implementation of the writer keeps the contract intact.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.queries import (  # noqa: E402
    get_org_state_summary,
    get_run_by_task_id,
    list_active_runs,
    list_runs_with_dirs,
)
from tools.state_db.writer import StateWriter  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Closed run-status enumeration per Set F § 2 (mirrors schema.sql CHECK).
CONTRACT_STATUS_VOCAB: frozenset[str] = frozenset(
    {"queued", "in_use", "review", "completed", "failed", "suspended", "abandoned"}
)

# Predicates pinned by Set F § 3.5.
ACTIVE_RESERVATION = frozenset({"queued", "in_use", "review"})
ACTIVE_EXECUTION = frozenset({"in_use"})
USER_VISIBLE = frozenset({"in_use", "review"})
TERMINAL = frozenset({"completed", "failed", "abandoned"})


def _make_root_with_db():
    """Create a temp ``<root>/.state/state.db`` with ``workers/`` seeded.

    Returns ``(tempdir_handle, root_path, db_path, sqlite3_connection)``.
    The caller must close the connection and call ``cleanup()`` on the
    tempdir handle. The connection is opened via ``tools.state_db.connect``
    so PRAGMAs (foreign_keys, busy_timeout) are applied; the schema is
    applied so the run-status CHECK constraint is in force.
    """
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / ".state" / "workers").mkdir(parents=True)
    db = root / ".state" / "state.db"
    conn = connect(db)
    apply_schema(conn)
    return td, root, db, conn


def _seed_worker_file(root: Path, task_id: str) -> Path:
    f = root / ".state" / "workers" / f"worker-{task_id}.md"
    f.write_text(
        f"# Worker: worker-{task_id}\nStatus: active\n", encoding="utf-8"
    )
    return f


def _statuses(rows) -> set[str]:
    return {r.get("status") for r in rows}


def _task_ids(rows) -> set[str]:
    return {r.get("task_id") for r in rows}


# ---------------------------------------------------------------------------
# § 2 — Closed run-status vocabulary (I1)
# ---------------------------------------------------------------------------


class TestRunStatusVocabulary(unittest.TestCase):
    """Set F § 2 / I1: ``runs.status`` is restricted to a closed seven-value
    enumeration. Any drift (added/removed/renamed status) must trip the
    schema CHECK constraint at write time."""

    def test_schema_enumeration_matches_contract(self):
        """Codex round-1 Major: assert *exact* set equality. A drift
        that adds a new status to the CHECK clause (e.g. 'blocked')
        must fail this test even though every contract value is still
        listed."""
        import re
        td, _root, _db, conn = _make_root_with_db()
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'runs'"
            ).fetchone()
            self.assertIsNotNone(row)
            sql = row["sql"]
            # Extract the status CHECK clause's IN (...) member list and
            # parse out the literals between single quotes. This lets
            # the assertion fail on *any* difference — added values,
            # removed values, or renamed values.
            m = re.search(
                r"status\s+TEXT[^,]*?CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)",
                sql, re.IGNORECASE,
            )
            self.assertIsNotNone(
                m, "could not locate runs.status CHECK clause in DDL"
            )
            literals = set(re.findall(r"'([^']*)'", m.group(1)))
            self.assertEqual(
                literals, set(CONTRACT_STATUS_VOCAB),
                "runs.status CHECK clause MUST equal the contract vocabulary "
                f"exactly; drift={literals ^ set(CONTRACT_STATUS_VOCAB)!r}"
            )
        finally:
            conn.close()
            td.cleanup()

    def test_writer_rejects_status_outside_contract_vocabulary(self):
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            with w.transaction() as tx:
                tx.upsert_run(task_id="t-vocab", project_slug="p", pattern="B")
            with self.assertRaises(sqlite3.IntegrityError):
                with w.transaction() as tx:
                    tx.update_run_status("t-vocab", "in-progress")  # hyphen
        finally:
            conn.close()
            td.cleanup()


# ---------------------------------------------------------------------------
# § 4 / § 6 — Lifecycle coverage
# ---------------------------------------------------------------------------


class TestPrimaryLifecycle(unittest.TestCase):
    """Set F § 4 / § 6: primary success path is
    ``(none) -> queued -> in_use -> review -> completed`` driven by
    secretary (T1, T4, T5) + dispatcher (T2). T3 is an in_use→in_use
    no-op for progress reports.

    These tests walk the lifecycle through the sanctioned writer entry
    points, asserting that intermediate read-side projections agree with
    the contract predicates at each step (§ 3)."""

    def setUp(self):
        self._td, self._root, self._db, self._conn = _make_root_with_db()
        self._writer = StateWriter(self._conn, claude_org_root=self._root)

    def tearDown(self):
        self._conn.close()
        self._td.cleanup()

    def test_full_success_path(self):
        # T1 — secretary reservation; row inserted with status='queued'.
        with self._writer.transaction() as tx:
            tx.upsert_run(
                task_id="t-life", project_slug="p", pattern="B",
                status="queued",
            )
        run = get_run_by_task_id(self._conn, "t-life")
        self.assertEqual(run["status"], "queued")

        # § 3.1 active reservation: queued is included.
        self.assertIn(run["status"], ACTIVE_RESERVATION)
        # § 3.3 user-visible: queued is excluded (I8). list_active_runs is
        # the canonical user-visible query.
        self.assertEqual(_task_ids(list_active_runs(self._conn)), set())

        # T2 — dispatcher promotion to in_use (upsert with new status).
        with self._writer.transaction() as tx:
            tx.upsert_run(
                task_id="t-life", project_slug="p", pattern="B",
                status="in_use",
            )
        run = get_run_by_task_id(self._conn, "t-life")
        self.assertEqual(run["status"], "in_use")
        self.assertIn(run["status"], ACTIVE_EXECUTION)
        self.assertIn("t-life", _task_ids(list_active_runs(self._conn)))

        # T3 — progress no-op: writer sends update_run_status(in_use)
        # again and runs.status remains in_use without raising.
        with self._writer.transaction() as tx:
            tx.update_run_status("t-life", "in_use")
        self.assertEqual(
            get_run_by_task_id(self._conn, "t-life")["status"], "in_use"
        )

        # T4 — secretary flips to review on worker completion report.
        with self._writer.transaction() as tx:
            tx.update_run_status("t-life", "review")
        run = get_run_by_task_id(self._conn, "t-life")
        self.assertEqual(run["status"], "review")
        self.assertIn(run["status"], USER_VISIBLE)
        self.assertNotIn(run["status"], ACTIVE_EXECUTION,
                         "review excludes active-execution per § 3.2")

        # T5 — secretary close-condition met.
        with self._writer.transaction() as tx:
            tx.update_run_status("t-life", "completed")
        run = get_run_by_task_id(self._conn, "t-life")
        self.assertEqual(run["status"], "completed")
        self.assertIn(run["status"], TERMINAL)
        # Terminal rows MUST drop out of the user-visible projection.
        self.assertNotIn("t-life", _task_ids(list_active_runs(self._conn)))

    def test_review_to_in_use_review_feedback(self):
        """T6 — secretary may bounce the run back from review to in_use
        when delivering review feedback (Set F § 4 row T6)."""
        with self._writer.transaction() as tx:
            tx.upsert_run(
                task_id="t-t6", project_slug="p", pattern="B",
                status="in_use",
            )
            tx.update_run_status("t-t6", "review")
        with self._writer.transaction() as tx:
            tx.update_run_status("t-t6", "in_use")
        self.assertEqual(
            get_run_by_task_id(self._conn, "t-t6")["status"], "in_use"
        )
        # After T6, the run is again user-visible and active-execution.
        self.assertIn("t-t6", _task_ids(list_active_runs(self._conn)))

    def test_progress_log_writes_do_not_change_status(self):
        """Set F § 4 T3: progress reports MUST NOT change runs.status.
        Repeated upsert_run calls with the same status field and other
        metadata must be a status no-op."""
        with self._writer.transaction() as tx:
            tx.upsert_run(
                task_id="t-prog", project_slug="p", pattern="B",
                status="in_use", title="initial",
            )
        with self._writer.transaction() as tx:
            tx.upsert_run(
                task_id="t-prog", project_slug="p", title="progressing",
            )  # no status passed
        run = get_run_by_task_id(self._conn, "t-prog")
        self.assertEqual(run["status"], "in_use")
        self.assertEqual(run["title"], "progressing")


# ---------------------------------------------------------------------------
# § 4 — Terminal states (failed / abandoned)
# ---------------------------------------------------------------------------


class TestTerminalStates(unittest.TestCase):
    """Set F § 4 T7-T9 / I5: ``failed`` and ``abandoned`` are accepted
    terminal values. The contract notes T7/T8/T9 production callsites
    are pending — these tests verify that *when* a writer drives the
    transition, the resulting state is stable, terminal, and excluded
    from active read-side projections."""

    def _setup(self, task_id: str, *, status_path: list[str]):
        td, root, _db, conn = _make_root_with_db()
        w = StateWriter(conn, claude_org_root=root)
        with w.transaction() as tx:
            tx.upsert_run(
                task_id=task_id, project_slug="p", pattern="B",
                status=status_path[0],
            )
            for s in status_path[1:]:
                tx.update_run_status(task_id, s)
        return td, root, conn, w

    def test_in_use_to_failed_terminal(self):
        td, _root, conn, _w = self._setup(
            "t-fail", status_path=["in_use", "failed"]
        )
        try:
            run = get_run_by_task_id(conn, "t-fail")
            self.assertEqual(run["status"], "failed")
            self.assertIn(run["status"], TERMINAL)
            self.assertNotIn("t-fail", _task_ids(list_active_runs(conn)))
        finally:
            conn.close()
            td.cleanup()

    def test_in_use_to_abandoned_terminal(self):
        td, _root, conn, _w = self._setup(
            "t-aban", status_path=["in_use", "abandoned"]
        )
        try:
            run = get_run_by_task_id(conn, "t-aban")
            self.assertEqual(run["status"], "abandoned")
            self.assertIn(run["status"], TERMINAL)
            self.assertNotIn("t-aban", _task_ids(list_active_runs(conn)))
        finally:
            conn.close()
            td.cleanup()

    def test_queued_to_abandoned_capacity_path(self):
        """Set F § 6 row ``queued``: T8 is the only legal exit from
        queued without a successful T2 promotion; queued → completed and
        queued → review are forbidden by the contract transition table.
        We verify the legal path here; the forbidden ones are negative-
        checked in :class:`TestForbiddenTransitions`."""
        td, _root, conn, _w = self._setup(
            "t-q8", status_path=["queued", "abandoned"]
        )
        try:
            run = get_run_by_task_id(conn, "t-q8")
            self.assertEqual(run["status"], "abandoned")
            self.assertIn(run["status"], TERMINAL)
        finally:
            conn.close()
            td.cleanup()


class TestForbiddenTransitions(unittest.TestCase):
    """Set F § 6: the contract transition table is an allow-list.

    The writer's row-level ``UPDATE`` does not enforce *table-level*
    transitions today (I5 is operator-level per the contract); attempts
    to write a forbidden cell of the table (e.g. ``queued → completed``)
    succeed at the SQL layer and are caught by code-review / journal
    audit, not by SQL CHECK. We therefore lock two complementary
    guarantees here:

    1. **Read-side projection** — once a row commits to a terminal
       status, every user-visible projection MUST exclude it,
       regardless of how it got there.
    2. **Operator audit surface** — the run row's *committed* status
       remains exactly what the writer wrote, so retros / journal
       audits can detect and roll back an out-of-allow-list transition
       (the only mechanism that currently enforces the allow-list).

    A future Issue may add a CHECK trigger to enforce I5 in code; when
    that lands, additional tests should assert that a forbidden write
    raises rather than commits."""

    def test_terminal_rows_excluded_from_user_visible_projection(self):
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            with w.transaction() as tx:
                tx.upsert_run(
                    task_id="t-comp", project_slug="p", pattern="B",
                    status="in_use",
                )
                tx.update_run_status("t-comp", "completed")
                tx.upsert_run(
                    task_id="t-fai", project_slug="p", pattern="B",
                    status="in_use",
                )
                tx.update_run_status("t-fai", "failed")
                tx.upsert_run(
                    task_id="t-aba", project_slug="p", pattern="B",
                    status="in_use",
                )
                tx.update_run_status("t-aba", "abandoned")
                tx.upsert_run(
                    task_id="t-live", project_slug="p", pattern="B",
                    status="in_use",
                )
            visible = _task_ids(list_active_runs(conn))
            self.assertEqual(
                visible, {"t-live"},
                "user-visible projection MUST equal status IN ('in_use','review')"
            )
        finally:
            conn.close()
            td.cleanup()

    def test_forbidden_queued_jumps_commit_audit_surface(self):
        """Set F § 6: ``queued → completed`` and ``queued → review`` are
        forbidden allow-list cells (the only legal exit from queued
        without T2 promotion is T8 → abandoned). The writer does NOT
        block these today — pin the audit surface that will detect
        them: the row's *committed* status equals exactly what the
        writer wrote, no silent normalization, so journal audits can
        trip on the violation. If a future commit adds CHECK-trigger
        enforcement, this test should be tightened to assert the
        write raises instead."""
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            with w.transaction() as tx:
                tx.upsert_run(
                    task_id="t-jump-c", project_slug="p", pattern="B",
                    status="queued",
                )
                tx.upsert_run(
                    task_id="t-jump-r", project_slug="p", pattern="B",
                    status="queued",
                )
            with w.transaction() as tx:
                tx.update_run_status("t-jump-c", "completed")
                tx.update_run_status("t-jump-r", "review")
            self.assertEqual(
                get_run_by_task_id(conn, "t-jump-c")["status"], "completed",
                "writer MUST commit the literal status the caller wrote"
            )
            self.assertEqual(
                get_run_by_task_id(conn, "t-jump-r")["status"], "review",
                "writer MUST commit the literal status the caller wrote"
            )
        finally:
            conn.close()
            td.cleanup()

    def test_queued_excluded_from_user_visible_but_in_active_reservation(self):
        """Set F § 3.3 / I8: queued rows must NOT appear on the dashboard
        / org-state.md Active Work Items list, but Set F § 3.1 says
        they DO occupy the project's base-clone slot — confirmed by
        ``list_runs_with_dirs`` which feeds the resolver."""
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            with w.transaction() as tx:
                tx.register_worker_dir(
                    abs_path="/tmp/x", layout="flat", lifecycle="active"
                )
                tx.upsert_run(
                    task_id="t-q", project_slug="p", pattern="B",
                    status="queued", worker_dir_abs_path="/tmp/x",
                )
            # User-visible: excluded.
            self.assertNotIn("t-q", _task_ids(list_active_runs(conn)))
            # Active-reservation projection (resolver feed): present.
            with_dirs = list_runs_with_dirs(conn)
            t_q = next((r for r in with_dirs if r["task_id"] == "t-q"), None)
            self.assertIsNotNone(t_q)
            self.assertEqual(t_q["status"], "queued")
            self.assertIn(t_q["status"], ACTIVE_RESERVATION)
        finally:
            conn.close()
            td.cleanup()


# ---------------------------------------------------------------------------
# Worker archive move (post-commit hook on T5)
# ---------------------------------------------------------------------------


class TestWorkerArchivePostCommit(unittest.TestCase):
    """Set F § 1.1 / Issue #284: ``update_run_status('<task>', 'completed')``
    must atomically move ``.state/workers/worker-<task>.md`` to
    ``.state/workers/archive/`` after the DB commit succeeds.

    This test re-anchors the existing TestPostCommitWorkerArchive cases
    in ``tools/state_db/test_writer.py`` against the *contract* —
    failing here points the operator at the contract document, not the
    implementation file."""

    def test_t5_completion_archives_worker_state_file(self):
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            src = _seed_worker_file(root, "tarc")
            with w.transaction() as tx:
                tx.upsert_run(
                    task_id="tarc", project_slug="p", pattern="B",
                    status="in_use",
                )
                tx.update_run_status("tarc", "review")
            self.assertTrue(src.exists(), "review must NOT archive")

            with w.transaction() as tx:
                tx.update_run_status("tarc", "completed")
            self.assertFalse(src.exists(),
                             "T5 close MUST move worker file out of .state/workers/")
            archived = root / ".state" / "workers" / "archive" / "worker-tarc.md"
            self.assertTrue(archived.exists(),
                            "worker file MUST land under .state/workers/archive/")
        finally:
            conn.close()
            td.cleanup()

    def test_non_completed_terminal_does_not_archive(self):
        """Per Set F § 1.1 the post-commit hook fires on the
        ``completed`` transition specifically. ``failed`` and
        ``abandoned`` are terminal but the worker-state file lifecycle
        is governed independently (the secretary's manual / curator
        sweep paths handle those cases). The writer-level hook MUST NOT
        archive on ``failed`` / ``abandoned``."""
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            src_f = _seed_worker_file(root, "tfail")
            src_a = _seed_worker_file(root, "taba")
            with w.transaction() as tx:
                tx.upsert_run(
                    task_id="tfail", project_slug="p", pattern="B",
                    status="in_use",
                )
                tx.upsert_run(
                    task_id="taba", project_slug="p", pattern="B",
                    status="in_use",
                )
                tx.update_run_status("tfail", "failed")
                tx.update_run_status("taba", "abandoned")
            self.assertTrue(src_f.exists())
            self.assertTrue(src_a.exists())
            self.assertFalse(
                (root / ".state" / "workers" / "archive").exists(),
                "archive dir must not be created for failed/abandoned"
            )
        finally:
            conn.close()
            td.cleanup()


# ---------------------------------------------------------------------------
# Post-commit derived-artifact regen soft-failure
# ---------------------------------------------------------------------------


class TestDerivedArtifactSoftFailure(unittest.TestCase):
    """Set F § 1.2 / Issue #284: derived artifacts (``.state/org-state.md``,
    ``.state/org-state.json``) are regenerated from state.db as a
    post-commit hook. Their regeneration is best-effort — a regen
    failure MUST NOT roll back the already-committed DB write and MUST
    NOT propagate as an exception out of ``transaction()``.

    This is a contract-level requirement because the DB is the single
    source of truth (§ 1.3): a derived-layer regen failure must never
    block the authoritative write."""

    def test_markdown_regen_failure_is_swallowed_and_db_committed(self):
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            from tools.state_db import snapshotter as _snap
            orig = _snap.post_commit_regenerate

            def boom(*_a, **_kw):
                raise RuntimeError("contract-test: simulated markdown regen failure")

            _snap.post_commit_regenerate = boom
            try:
                # MUST NOT raise even though the post-commit hook explodes.
                with w.transaction() as tx:
                    tx.upsert_run(
                        task_id="t-soft-md", project_slug="p", pattern="B",
                        status="in_use",
                    )
                    tx.update_run_status("t-soft-md", "review")
            finally:
                _snap.post_commit_regenerate = orig

            # DB write survived.
            run = get_run_by_task_id(conn, "t-soft-md")
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "review")
        finally:
            conn.close()
            td.cleanup()

    def test_json_regen_failure_is_swallowed_and_db_committed(self):
        """Codex round-1 Major: a JSON regen failure must NOT
        short-circuit the post-commit worker-archive hook that runs
        after it. Seed a real worker file and assert it lands in
        archive/ even when the JSON converter explodes mid-hook."""
        td, root, _db, conn = _make_root_with_db()
        try:
            w = StateWriter(conn, claude_org_root=root)
            src = _seed_worker_file(root, "t-soft-json")
            from dashboard import org_state_converter as _conv
            orig = _conv.convert

            def boom(*_a, **_kw):
                raise RuntimeError("contract-test: simulated json regen failure")

            _conv.convert = boom
            try:
                with w.transaction() as tx:
                    tx.upsert_run(
                        task_id="t-soft-json", project_slug="p", pattern="B",
                        status="in_use",
                    )
                    tx.update_run_status("t-soft-json", "completed")
            finally:
                _conv.convert = orig

            run = get_run_by_task_id(conn, "t-soft-json")
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "completed")
            # JSON regen blew up; subsequent worker-archive hook MUST
            # still run. Source file moved → archive populated.
            self.assertFalse(
                src.exists(),
                "JSON regen failure must NOT short-circuit the worker "
                "archive post-commit step"
            )
            archived = (
                root / ".state" / "workers" / "archive" / "worker-t-soft-json.md"
            )
            self.assertTrue(
                archived.exists(),
                "worker file MUST land in archive/ even when an earlier "
                "post-commit hook raised"
            )
        finally:
            conn.close()
            td.cleanup()


# ---------------------------------------------------------------------------
# DB / read-side coherence (queries + dashboard summary)
# ---------------------------------------------------------------------------


class TestReadSideCoherence(unittest.TestCase):
    """Set F § 3.5: the four contract predicates pin the read-side
    projections to explicit ``runs.status IN …`` clauses. These tests
    seed a mixed population covering all seven status values and assert
    each projection returns exactly the rows the contract prescribes."""

    def setUp(self):
        self._td, self._root, self._db, self._conn = _make_root_with_db()
        w = StateWriter(self._conn, claude_org_root=self._root)
        # Seed every status in the closed vocabulary so any conflation
        # between predicates surfaces as a wrong row count.
        with w.transaction() as tx:
            tx.register_worker_dir(
                abs_path="/tmp/cohpath", layout="flat", lifecycle="active"
            )
            tx.upsert_run(
                task_id="rq", project_slug="p", pattern="B",
                status="queued", worker_dir_abs_path="/tmp/cohpath",
            )
            tx.upsert_run(
                task_id="ri", project_slug="p", pattern="B",
                status="in_use", worker_dir_abs_path="/tmp/cohpath",
            )
            tx.upsert_run(
                task_id="rr", project_slug="p", pattern="B",
                status="in_use", worker_dir_abs_path="/tmp/cohpath",
            )
            tx.update_run_status("rr", "review")
            tx.upsert_run(
                task_id="rc", project_slug="p", pattern="B",
                status="in_use", worker_dir_abs_path="/tmp/cohpath",
            )
            tx.update_run_status("rc", "completed")
            tx.upsert_run(
                task_id="rf", project_slug="p", pattern="B",
                status="in_use", worker_dir_abs_path="/tmp/cohpath",
            )
            tx.update_run_status("rf", "failed")
            # 'suspended' has no sanctioned writer today — issue a raw
            # UPDATE so we can verify the read-side projections still
            # treat it correctly when a future writer activates it.
            tx.upsert_run(
                task_id="rs", project_slug="p", pattern="B",
                status="in_use", worker_dir_abs_path="/tmp/cohpath",
            )
            self._conn.execute(
                "UPDATE runs SET status = 'suspended' WHERE task_id = ?",
                ("rs",),
            )
            tx.upsert_run(
                task_id="ra", project_slug="p", pattern="B",
                status="in_use", worker_dir_abs_path="/tmp/cohpath",
            )
            tx.update_run_status("ra", "abandoned")

    def tearDown(self):
        self._conn.close()
        self._td.cleanup()

    def test_user_visible_projection_matches_contract(self):
        """§ 3.3 / § 3.5: ``list_active_runs`` returns exactly
        ``status IN ('in_use','review')``."""
        rows = list_active_runs(self._conn)
        statuses = _statuses(rows)
        self.assertEqual(statuses, USER_VISIBLE)
        self.assertEqual(
            _task_ids(rows), {"ri", "rr"},
            "user-visible projection MUST exclude queued / suspended / terminal rows"
        )

    def test_active_reservation_projection_includes_queued(self):
        """§ 3.1 / § 3.5: the resolver-feed projection includes queued
        because T1 reservations occupy the base-clone slot before T2
        spawns the pane."""
        rows = list_runs_with_dirs(self._conn)
        active_reservation_rows = [
            r for r in rows if r["status"] in ACTIVE_RESERVATION
        ]
        self.assertEqual(
            _task_ids(active_reservation_rows), {"rq", "ri", "rr"},
            "active-reservation projection MUST be exactly status IN "
            "('queued','in_use','review')"
        )

    def test_terminal_projection_matches_contract(self):
        """§ 3.4 / § 3.5: the terminal predicate is
        ``status IN ('completed','failed','abandoned')``. ``suspended``
        is reserved-for-future and is NOT terminal under this contract."""
        rows = list_runs_with_dirs(self._conn)
        terminal_rows = [r for r in rows if r["status"] in TERMINAL]
        self.assertEqual(_task_ids(terminal_rows), {"rc", "rf", "ra"})
        # suspended is explicitly excluded from terminal.
        self.assertNotIn(
            "suspended", {r["status"] for r in terminal_rows},
            "Set F § 2: 'suspended' is reserved, not terminal"
        )

    def test_dashboard_summary_active_runs_matches_user_visible(self):
        """Set F § 1.2 / dashboard payload (``/api/state``) reads from
        state.db via ``get_org_state_summary``. Its ``active_runs`` slot
        MUST agree with the user-visible predicate (§ 3.3)."""
        summary = get_org_state_summary(self._conn)
        active = summary["active_runs"]
        self.assertEqual(
            _task_ids(active), {"ri", "rr"},
            "dashboard summary active_runs MUST equal user-visible predicate"
        )
        # Run-status counts surface every status value from the closed
        # vocabulary that has rows; this is the operator-debug surface
        # and MUST NOT be filtered.
        counts = summary["run_status_counts"]
        for status in {"queued", "in_use", "review", "completed",
                       "failed", "suspended", "abandoned"}:
            self.assertEqual(
                counts.get(status, 0), 1,
                f"run_status_counts MUST surface every seeded status, missing {status!r}"
            )

    def test_dashboard_status_remap_covers_full_vocabulary(self):
        """``dashboard.server._DB_STATUS_TO_UI`` is the contract-level
        DB-status → frontend-status remap (Set F § 2 last paragraph).
        Its domain MUST cover every value in the closed vocabulary so
        the UI never falls back to ``?`` for a legal status."""
        from dashboard.server import _DB_STATUS_TO_UI
        self.assertEqual(
            set(_DB_STATUS_TO_UI.keys()), CONTRACT_STATUS_VOCAB,
            "_DB_STATUS_TO_UI domain MUST equal the closed run-status vocabulary"
        )


if __name__ == "__main__":
    unittest.main()
