"""Unit tests for tools/run_complete_on_merge.py (Issue #317).

The gh CLI is mocked via subprocess.run side_effect; the DB writes go
to a temp state.db built from schema.sql.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_complete_on_merge  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402


REPO = "octo/repo"
PR = 317
PR_URL = f"https://github.com/{REPO}/pull/{PR}"
BRANCH = "feat/issue-317-pr-merge-helper"
TASK_ID = "issue-317-pr-merge-helper"
MERGED_AT = "2026-05-06T03:21:00Z"
MERGE_OID = "abcdef0123456789abcdef0123456789abcdef01"


def _seed_run(db_path: Path, *, pr_url: str = PR_URL, branch: str = BRANCH,
              pattern: str = "A") -> None:
    """Create a fresh DB with one in-progress run row pointing at the PR.

    Default pattern is 'A' so the helper performs the full status
    transition; pattern-B tests opt in explicitly via ``pattern='B'``.
    """
    apply_schema(connect(db_path))
    conn = connect(db_path)
    try:
        with StateWriter(conn).transaction() as w:
            w.upsert_run(
                task_id=TASK_ID,
                project_slug="claude-org",
                pattern=pattern,
                title="PR-merge auto-completion helper",
                status="review",
                branch=branch,
                pr_url=pr_url,
                pr_state="open",
            )
    finally:
        conn.close()


def _seed_pattern_c_run(db_path: Path, *, worker_dir: str, pattern: str = "C",
                        task_id: str = TASK_ID) -> None:
    """Seed a run linked to a worker_dir, for the Issue #478 cleanup tests.

    ``worker_dir`` is registered first so ``upsert_run`` can resolve it to a
    ``worker_dir_id`` within the same transaction.
    """
    apply_schema(connect(db_path))
    conn = connect(db_path)
    try:
        with StateWriter(conn).transaction() as w:
            w.register_worker_dir(abs_path=worker_dir, is_git_repo=True)
            w.upsert_run(
                task_id=task_id,
                project_slug="claude-org-ja",
                pattern=pattern,
                title="pattern C self-edit",
                status="review",
                worker_dir_abs_path=worker_dir,
            )
    finally:
        conn.close()


def _make_pr_view(*, merged_at=MERGED_AT, merge_oid=MERGE_OID) -> dict:
    return {
        "number": PR,
        "url": PR_URL,
        "state": "MERGED" if merged_at else "OPEN",
        "mergedAt": merged_at,
        "mergeCommit": {"oid": merge_oid} if merge_oid else None,
        "headRefName": BRANCH,
    }


def _fake_subprocess_run(view_payload):
    """Build a subprocess.run stub that responds to gh pr view calls."""
    def fake(cmd, *args, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return mock.Mock(
                returncode=0,
                stdout=json.dumps(view_payload),
                stderr="",
            )
        raise AssertionError(f"unexpected cmd: {cmd}")
    return fake


def _events_of_kind(db: Path, kind: str) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row) for row in conn.execute(
                "SELECT id, kind, payload_json, run_id FROM events "
                "WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        ]
    finally:
        conn.close()


def _run_row(db: Path, task_id: str = TASK_ID) -> dict:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM runs WHERE task_id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row is not None else {}
    finally:
        conn.close()


class TempDB:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name) / "state.db"

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


class CompleteOnMergeTests(unittest.TestCase):
    def test_records_merge_metadata_without_flipping_status(self) -> None:
        """Codex round-3 Blocker: helper never flips runs.status itself.

        It records pr_state='merged', commit_short/full, pr_url,
        completed_at, and appends one pr_merged event. The status
        transition to 'completed' is the secretary's manual step
        once worktree / pane / worker_dir cleanup is done.
        """
        with TempDB() as db:
            _seed_run(db)
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(result, run_complete_on_merge.RESULT_MERGED)
            row = _run_row(db)
            # status must NOT be flipped — secretary owns the transition.
            self.assertEqual(row["status"], "review")
            self.assertEqual(row["pr_state"], "merged")
            self.assertEqual(row["pr_url"], PR_URL)
            self.assertEqual(row["commit_full"], MERGE_OID)
            self.assertEqual(row["commit_short"], MERGE_OID[:7])
            self.assertEqual(row["completed_at"], MERGED_AT)

            evts = _events_of_kind(db, "pr_merged")
            self.assertEqual(len(evts), 1)
            payload = json.loads(evts[0]["payload_json"])
            self.assertEqual(payload["task"], TASK_ID)
            self.assertEqual(payload["pr"], PR)
            self.assertEqual(payload["repo"], REPO)
            self.assertEqual(payload["merge_commit"], MERGE_OID)
            self.assertEqual(payload["merged_at"], MERGED_AT)
            self.assertFalse(payload["auto_completed"])

    def test_pattern_b_records_metadata_without_status_flip(self) -> None:
        """Pattern B / C / D path is identical to Pattern A: status untouched."""
        with TempDB() as db:
            _seed_run(db, pattern="B")
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(result, run_complete_on_merge.RESULT_MERGED)
            row = _run_row(db)
            self.assertEqual(row["status"], "review")
            self.assertEqual(row["pr_state"], "merged")
            self.assertEqual(row["completed_at"], MERGED_AT)
            payload = json.loads(
                _events_of_kind(db, "pr_merged")[0]["payload_json"]
            )
            self.assertEqual(payload["pattern"], "B")
            self.assertFalse(payload["auto_completed"])

    def test_idempotent_second_call_is_noop(self) -> None:
        with TempDB() as db:
            _seed_run(db)
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                first = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
                # Simulate the secretary completing the manual close
                # between calls — the helper must still detect that the
                # event was already recorded and no-op.
                conn = connect(db)
                try:
                    with StateWriter(conn).transaction() as w:
                        w.update_run_status(TASK_ID, "completed")
                finally:
                    conn.close()
                second = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(first, run_complete_on_merge.RESULT_MERGED)
            self.assertEqual(second, run_complete_on_merge.RESULT_ALREADY)
            # No double event row.
            self.assertEqual(len(_events_of_kind(db, "pr_merged")), 1)

    def test_not_merged_is_no_op(self) -> None:
        with TempDB() as db:
            _seed_run(db)
            view = _make_pr_view(merged_at=None, merge_oid=None)
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(view),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(result, run_complete_on_merge.RESULT_NOT_YET)
            self.assertEqual(_run_row(db)["status"], "review")
            self.assertEqual(_events_of_kind(db, "pr_merged"), [])

    def test_branch_fallback_skips_terminal_runs(self) -> None:
        """Codex Major: branch lookup must ignore completed/failed/abandoned runs.

        A historical completed run with the same branch as the live PR
        must not be re-completed and have a stray pr_merged event
        appended to it.
        """
        with TempDB() as db:
            _seed_run(db, pr_url="", branch=BRANCH, pattern="A")
            # Flip the seeded row to completed so it should be skipped
            # by the branch fallback.
            conn = connect(db)
            try:
                with StateWriter(conn).transaction() as w:
                    w.update_run_status(TASK_ID, "completed")
            finally:
                conn.close()
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            # No active run row matched (the only row is terminal).
            self.assertEqual(result, run_complete_on_merge.RESULT_NO_RUN)
            self.assertEqual(_events_of_kind(db, "pr_merged"), [])

    def test_resolves_task_id_via_branch_when_pr_url_absent(self) -> None:
        """If the seeded run has no pr_url, fall back to branch lookup."""
        with TempDB() as db:
            _seed_run(db, pr_url="")
            self.assertIn(_run_row(db)["pr_url"], (None, ""))
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(result, run_complete_on_merge.RESULT_MERGED)
            row = _run_row(db)
            self.assertEqual(row["pr_state"], "merged")
            # status remains 'review' — secretary owns the flip.
            self.assertEqual(row["status"], "review")

    def test_no_matching_run_returns_no_run(self) -> None:
        with TempDB() as db:
            apply_schema(connect(db))  # empty DB, no runs
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(result, run_complete_on_merge.RESULT_NO_RUN)
            self.assertEqual(_events_of_kind(db, "pr_merged"), [])

    def test_explicit_task_id_skips_resolution(self) -> None:
        with TempDB() as db:
            _seed_run(db, pr_url="", branch="other-branch")
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db, task_id=TASK_ID,
                )
            self.assertEqual(result, run_complete_on_merge.RESULT_MERGED)


class CLITests(unittest.TestCase):
    def test_cli_invokes_helper(self) -> None:
        with TempDB() as db:
            _seed_run(db)
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ), mock.patch.object(
                run_complete_on_merge.shutil, "which", return_value="/usr/bin/gh",
            ):
                rc = run_complete_on_merge.main([
                    "--pr", str(PR),
                    "--repo", REPO,
                    "--db-path", str(db),
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(_run_row(db)["pr_state"], "merged")

    def test_cli_no_run_exits_nonzero(self) -> None:
        """Codex round-3 Major: CLI must surface no_run as a failure exit."""
        with TempDB() as db:
            apply_schema(connect(db))  # empty DB, no runs match
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ), mock.patch.object(
                run_complete_on_merge.shutil, "which", return_value="/usr/bin/gh",
            ):
                rc = run_complete_on_merge.main([
                    "--pr", str(PR),
                    "--repo", REPO,
                    "--db-path", str(db),
                ])
            self.assertEqual(rc, 3)


class CleanupPatternCTests(unittest.TestCase):
    """Issue #478: Pattern C gitignored_repo_root CLAUDE.local.md cleanup."""

    def _brief_path(self, root: Path) -> Path:
        return root / run_complete_on_merge.PATTERN_C_BRIEF_FILENAME

    def test_pattern_c_at_root_removes_brief_and_records_event(self) -> None:
        """Case 1: Pattern C + worker_dir == claude_org_root → delete + event."""
        with TempDB() as db:
            root = db.parent
            brief = self._brief_path(root)
            brief.write_text("worker brief\n", encoding="utf-8")
            _seed_pattern_c_run(db, worker_dir=str(root.resolve()))
            conn = connect(db)
            try:
                result = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id=TASK_ID, claude_org_root=root,
                )
            finally:
                conn.close()
            self.assertEqual(result, run_complete_on_merge.CLEANUP_REMOVED)
            self.assertFalse(brief.exists())
            evts = _events_of_kind(db, "pattern_c_cleanup")
            self.assertEqual(len(evts), 1)
            payload = json.loads(evts[0]["payload_json"])
            self.assertEqual(payload["task"], TASK_ID)
            self.assertEqual(payload["mode"], "auto")
            self.assertEqual(
                payload["removed_path"],
                str(root.resolve() / run_complete_on_merge.PATTERN_C_BRIEF_FILENAME),
            )

    def test_pattern_c_ephemeral_does_nothing(self) -> None:
        """Case 2: Pattern C ephemeral (worker_dir != root) → no-op, no event."""
        with TempDB() as db:
            root = db.parent
            ephemeral = root / "ephemeral_worker"
            ephemeral.mkdir()
            brief = self._brief_path(root)
            brief.write_text("untouched\n", encoding="utf-8")
            _seed_pattern_c_run(db, worker_dir=str(ephemeral.resolve()))
            conn = connect(db)
            try:
                result = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id=TASK_ID, claude_org_root=root,
                )
            finally:
                conn.close()
            self.assertEqual(
                result, run_complete_on_merge.CLEANUP_NOT_APPLICABLE
            )
            self.assertTrue(brief.exists())
            self.assertEqual(_events_of_kind(db, "pattern_c_cleanup"), [])

    def test_pattern_a_and_b_do_nothing(self) -> None:
        """Case 3: even with worker_dir == root, non-C patterns are no-ops."""
        for pattern in ("A", "B"):
            with self.subTest(pattern=pattern), TempDB() as db:
                root = db.parent
                brief = self._brief_path(root)
                brief.write_text("untouched\n", encoding="utf-8")
                _seed_pattern_c_run(
                    db, worker_dir=str(root.resolve()), pattern=pattern,
                )
                conn = connect(db)
                try:
                    result = run_complete_on_merge.cleanup_pattern_c_local_md(
                        conn, task_id=TASK_ID, claude_org_root=root,
                    )
                finally:
                    conn.close()
                self.assertEqual(
                    result, run_complete_on_merge.CLEANUP_NOT_APPLICABLE
                )
                self.assertTrue(brief.exists())
                self.assertEqual(_events_of_kind(db, "pattern_c_cleanup"), [])

    def test_idempotent_second_call_when_absent(self) -> None:
        """Case 4: re-call after the brief is gone does not raise."""
        with TempDB() as db:
            root = db.parent
            brief = self._brief_path(root)
            brief.write_text("worker brief\n", encoding="utf-8")
            _seed_pattern_c_run(db, worker_dir=str(root.resolve()))
            conn = connect(db)
            try:
                first = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id=TASK_ID, claude_org_root=root,
                )
                second = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id=TASK_ID, claude_org_root=root,
                )
            finally:
                conn.close()
            self.assertEqual(first, run_complete_on_merge.CLEANUP_REMOVED)
            self.assertEqual(second, run_complete_on_merge.CLEANUP_ABSENT)
            self.assertFalse(brief.exists())
            modes = [
                json.loads(e["payload_json"])["mode"]
                for e in _events_of_kind(db, "pattern_c_cleanup")
            ]
            self.assertEqual(modes, ["auto", "skip"])

    def test_no_run_row_is_not_applicable(self) -> None:
        """A task_id with no run row resolves cleanly to not_applicable."""
        with TempDB() as db:
            apply_schema(connect(db))
            conn = connect(db)
            try:
                result = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id="ghost-task", claude_org_root=db.parent,
                )
            finally:
                conn.close()
            self.assertEqual(
                result, run_complete_on_merge.CLEANUP_NOT_APPLICABLE
            )


class CleanupPatternCCloseOrderTests(unittest.TestCase):
    """Issue #486: cleanup must fire even after remove_worker_dir().

    The org-pull-request close-phase StateWriter block runs
    ``remove_worker_dir()`` (DELETE on worker_dirs), and ``runs.worker_dir_id``
    is ``ON DELETE SET NULL``. A cleanup that resolves the worker_dir via the
    live join therefore sees ``abs_path = NULL`` and no-ops, leaving the
    Pattern C gitignored_repo_root ``CLAUDE.local.md`` behind. The fix is the
    explicit ``worker_dir_abs`` argument, which survives the row removal.
    """

    def _brief_path(self, root: Path) -> Path:
        return root / run_complete_on_merge.PATTERN_C_BRIEF_FILENAME

    def _close_remove_worker_dir(self, db: Path, abs_path: str) -> None:
        """Mimic the SKILL StateWriter close block: status flip + row delete."""
        conn = connect(db)
        try:
            with StateWriter(conn).transaction() as w:
                w.update_run_status(TASK_ID, "completed")
                w.remove_worker_dir(abs_path)
        finally:
            conn.close()

    def test_cleanup_after_remove_with_worker_dir_abs_fires(self) -> None:
        """The fix: passing worker_dir_abs keeps cleanup firing post-removal."""
        with TempDB() as db:
            root = db.parent
            abs_path = str(root.resolve())
            brief = self._brief_path(root)
            brief.write_text("worker brief\n", encoding="utf-8")
            _seed_pattern_c_run(db, worker_dir=abs_path)

            # Close-phase ordering: worker_dirs row is gone before cleanup.
            self._close_remove_worker_dir(db, abs_path)

            conn = connect(db)
            try:
                # Sanity: the join no longer resolves the worker_dir.
                row = conn.execute(
                    "SELECT d.abs_path FROM runs r "
                    "LEFT JOIN worker_dirs d ON d.id = r.worker_dir_id "
                    "WHERE r.task_id = ?",
                    (TASK_ID,),
                ).fetchone()
                self.assertIsNone(row["abs_path"])

                result = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id=TASK_ID, claude_org_root=root,
                    worker_dir_abs=abs_path,
                )
            finally:
                conn.close()

            self.assertEqual(result, run_complete_on_merge.CLEANUP_REMOVED)
            self.assertFalse(brief.exists())
            evts = _events_of_kind(db, "pattern_c_cleanup")
            self.assertEqual(len(evts), 1)
            self.assertEqual(json.loads(evts[0]["payload_json"])["mode"], "auto")

    def test_cleanup_after_remove_without_abs_regresses_to_noop(self) -> None:
        """Root cause: the join-only path no-ops once the row is deleted.

        Without ``worker_dir_abs`` the helper falls back to the live join,
        which returns NULL after remove_worker_dir() — exactly the Issue #486
        bug this test pins. The brief is (incorrectly) left behind.
        """
        with TempDB() as db:
            root = db.parent
            abs_path = str(root.resolve())
            brief = self._brief_path(root)
            brief.write_text("worker brief\n", encoding="utf-8")
            _seed_pattern_c_run(db, worker_dir=abs_path)

            self._close_remove_worker_dir(db, abs_path)

            conn = connect(db)
            try:
                result = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id=TASK_ID, claude_org_root=root,
                )
            finally:
                conn.close()

            self.assertEqual(
                result, run_complete_on_merge.CLEANUP_NOT_APPLICABLE
            )
            self.assertTrue(brief.exists())
            self.assertEqual(_events_of_kind(db, "pattern_c_cleanup"), [])

    def test_worker_dir_abs_ephemeral_stays_noop(self) -> None:
        """worker_dir_abs for an ephemeral C run (≠ root) must not delete."""
        with TempDB() as db:
            root = db.parent
            ephemeral = root / "ephemeral_worker"
            ephemeral.mkdir()
            eph_abs = str(ephemeral.resolve())
            brief = self._brief_path(root)
            brief.write_text("untouched\n", encoding="utf-8")
            _seed_pattern_c_run(db, worker_dir=eph_abs)

            self._close_remove_worker_dir(db, eph_abs)

            conn = connect(db)
            try:
                result = run_complete_on_merge.cleanup_pattern_c_local_md(
                    conn, task_id=TASK_ID, claude_org_root=root,
                    worker_dir_abs=eph_abs,
                )
            finally:
                conn.close()

            self.assertEqual(
                result, run_complete_on_merge.CLEANUP_NOT_APPLICABLE
            )
            self.assertTrue(brief.exists())
            self.assertEqual(_events_of_kind(db, "pattern_c_cleanup"), [])


if __name__ == "__main__":
    unittest.main()
