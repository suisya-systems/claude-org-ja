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
    def test_marks_run_completed_on_first_merge(self) -> None:
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
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["pr_state"], "merged")
            self.assertEqual(row["pr_url"], PR_URL)
            self.assertEqual(row["commit_full"], MERGE_OID)
            self.assertEqual(row["commit_short"], MERGE_OID[:7])
            self.assertEqual(row["completed_at"], MERGED_AT)

            evts = _events_of_kind(db, "pr_merged")
            self.assertEqual(len(evts), 1)
            payload = json.loads(evts[0]["payload_json"])
            self.assertEqual(payload["pr"], PR)
            self.assertEqual(payload["repo"], REPO)
            self.assertEqual(payload["merge_commit"], MERGE_OID)
            self.assertEqual(payload["merged_at"], MERGED_AT)

    def test_pattern_b_stops_at_pending_cleanup(self) -> None:
        """Pattern B / C / D leaves runs.status untouched; only metadata + event."""
        with TempDB() as db:
            _seed_run(db, pattern="B")
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(
                result, run_complete_on_merge.RESULT_MERGED_PENDING_CLEANUP
            )
            row = _run_row(db)
            # status must NOT be flipped — secretary still has to run
            # worktree / CLOSE_PANE / remove_worker_dir.
            self.assertEqual(row["status"], "review")
            self.assertEqual(row["pr_state"], "merged")
            self.assertEqual(row["commit_full"], MERGE_OID)
            evts = _events_of_kind(db, "pr_merged")
            self.assertEqual(len(evts), 1)
            payload = json.loads(evts[0]["payload_json"])
            self.assertEqual(payload["task"], TASK_ID)
            self.assertFalse(payload["auto_completed"])
            self.assertEqual(payload["pattern"], "B")

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
            _seed_run(db, pr_url="")  # empty pr_url
            # Confirm pr_url really is NULL/empty so the lookup path is forced.
            self.assertIn(_run_row(db)["pr_url"], (None, ""))
            with mock.patch.object(
                run_complete_on_merge.subprocess, "run",
                side_effect=_fake_subprocess_run(_make_pr_view()),
            ):
                result = run_complete_on_merge.complete_on_merge(
                    pr=PR, repo=REPO, db_path=db,
                )
            self.assertEqual(result, run_complete_on_merge.RESULT_MERGED)
            self.assertEqual(_run_row(db)["status"], "completed")

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
            self.assertEqual(_run_row(db)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
