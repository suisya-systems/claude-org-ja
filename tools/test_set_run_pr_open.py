"""Unit + integration tests for tools/set_run_pr_open.py (Issue #323).

The integration test simulates the full PR-merge auto-completion chain:

    gen_delegate_payload.apply_delegate_plan
        → set_run_pr_open                   (mock `gh pr view`, PR open)
        → run_complete_on_merge.complete_on_merge
                                            (mock `gh pr view`, PR merged)

The chain ends with a single ``runs`` row that has ``pr_state='merged'``,
populated ``commit_short`` / ``commit_full`` / ``pr_url`` / ``completed_at``,
and one ``pr_merged`` event — i.e. the no-extra-flags MergeWatch path
that PR #321 surfaced as broken.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import set_run_pr_open  # noqa: E402  (tools/set_run_pr_open.py)
import run_complete_on_merge  # noqa: E402  (tools/run_complete_on_merge.py)
from tools import gen_delegate_payload as gdp  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402


REPO = "octo/repo"
PR = 323
PR_URL = f"https://github.com/{REPO}/pull/{PR}"
TASK_ID = "issue-323-pr-watch-task-id"
BRANCH = f"feat/{TASK_ID}"
MERGED_AT = "2026-05-06T05:00:00Z"
MERGE_OID = "abc1234567890abcdef0123456789abcdef01234"


# ---------------------------------------------------------------------------
# Unit tests for set_run_pr_open / StateWriter.set_run_pr
# ---------------------------------------------------------------------------


class TestStateWriterSetRunPr(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.close()

    def tearDown(self) -> None:
        self._td.cleanup()

    def _seed(self, *, branch=BRANCH, pr_url=None) -> None:
        conn = connect(self.db)
        try:
            with StateWriter(conn).transaction() as w:
                w.upsert_run(
                    task_id=TASK_ID,
                    project_slug="claude-org",
                    pattern="B",
                    title=TASK_ID,
                    status="review",
                    branch=branch,
                    pr_url=pr_url,
                )
        finally:
            conn.close()

    def _row(self) -> dict:
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT task_id, branch, pr_url FROM runs WHERE task_id = ?",
                (TASK_ID,),
            ).fetchone()
            return dict(row) if row is not None else {}
        finally:
            conn.close()

    def test_set_run_pr_writes_pr_url_and_branch(self):
        self._seed(branch=None, pr_url=None)
        conn = connect(self.db)
        try:
            with StateWriter(conn).transaction() as w:
                w.set_run_pr(TASK_ID, pr_url=PR_URL, branch=BRANCH)
        finally:
            conn.close()
        self.assertEqual(self._row()["pr_url"], PR_URL)
        self.assertEqual(self._row()["branch"], BRANCH)

    def test_set_run_pr_idempotent(self):
        self._seed()
        conn = connect(self.db)
        try:
            with StateWriter(conn).transaction() as w:
                w.set_run_pr(TASK_ID, pr_url=PR_URL, branch=BRANCH)
                w.set_run_pr(TASK_ID, pr_url=PR_URL, branch=BRANCH)
        finally:
            conn.close()
        self.assertEqual(self._row()["pr_url"], PR_URL)

    def test_set_run_pr_preserves_branch_when_omitted(self):
        self._seed()  # branch=BRANCH already
        conn = connect(self.db)
        try:
            with StateWriter(conn).transaction() as w:
                w.set_run_pr(TASK_ID, pr_url=PR_URL)
        finally:
            conn.close()
        row = self._row()
        self.assertEqual(row["pr_url"], PR_URL)
        self.assertEqual(row["branch"], BRANCH)

    def test_set_run_pr_rejects_empty_inputs(self):
        self._seed()
        conn = connect(self.db)
        try:
            w = StateWriter(conn)
            with self.assertRaises(ValueError):
                w.set_run_pr("", pr_url=PR_URL)
            with self.assertRaises(ValueError):
                w.set_run_pr(TASK_ID, pr_url="")
        finally:
            conn.close()


class TestSetRunPrOpenHelper(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        with StateWriter(conn).transaction() as w:
            w.upsert_run(
                task_id=TASK_ID,
                project_slug="claude-org",
                pattern="B",
                title=TASK_ID,
                status="review",
                branch=BRANCH,
            )
        conn.close()

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_returns_ok_and_back_fills(self):
        view = {"url": PR_URL, "headRefName": BRANCH}
        result = set_run_pr_open.set_run_pr_open(
            task_id=TASK_ID, pr=PR, repo=REPO,
            db_path=self.db, pr_view=view,
        )
        self.assertEqual(result, set_run_pr_open.RESULT_OK)
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT pr_url, branch FROM runs WHERE task_id = ?",
                (TASK_ID,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["pr_url"], PR_URL)
        self.assertEqual(row["branch"], BRANCH)

    def test_returns_no_run_when_task_id_missing(self):
        view = {"url": PR_URL, "headRefName": BRANCH}
        result = set_run_pr_open.set_run_pr_open(
            task_id="ghost-task", pr=PR, repo=REPO,
            db_path=self.db, pr_view=view,
        )
        self.assertEqual(result, set_run_pr_open.RESULT_NO_RUN)

    def test_raises_when_view_has_no_url(self):
        with self.assertRaises(RuntimeError):
            set_run_pr_open.set_run_pr_open(
                task_id=TASK_ID, pr=PR, repo=REPO,
                db_path=self.db, pr_view={"url": "", "headRefName": BRANCH},
            )


# ---------------------------------------------------------------------------
# Integration: gen_delegate_payload.apply → set_run_pr_open → run_complete_on_merge
# ---------------------------------------------------------------------------


class _Sandbox:
    """Same shape as tests/test_gen_delegate_payload.py::_Sandbox.

    Duplicated locally so this file stays self-contained — pulling it
    in via cross-test import would couple two test modules.
    """

    def __init__(self, td: Path):
        self.root = td
        self.workers = td / "workers"
        self.workers.mkdir()
        self.claude_org_root = td / "claude-org"
        (self.claude_org_root / ".state").mkdir(parents=True)
        (self.claude_org_root / "registry").mkdir()
        (self.claude_org_root / "registry" / "org-config.md").write_text(
            "## Permission Mode\ndefault_permission_mode: auto\n"
            "## Workers Directory\nworkers_dir: ../workers\n",
            encoding="utf-8",
        )
        (self.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| 時計アプリ | clock-app | - | Web 時計 | デザイン |\n",
            encoding="utf-8",
        )
        self.db_path = self.claude_org_root / ".state" / "state.db"
        conn = connect(self.db_path)
        apply_schema(conn)
        conn.close()


def _pr_view_open() -> dict:
    return {"url": PR_URL, "headRefName": BRANCH}


def _pr_view_merged() -> dict:
    return {
        "number": PR,
        "url": PR_URL,
        "state": "MERGED",
        "mergedAt": MERGED_AT,
        "mergeCommit": {"oid": MERGE_OID},
        "headRefName": BRANCH,
    }


class TestPrMergeAutoCompletionChain(unittest.TestCase):
    """Acceptance test for Issue #323: ``pr-watch.ps1 <PR> -MergeWatch`` with
    no manual --task-id drives the run to merged-state success.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _row(self) -> dict:
        conn = sqlite3.connect(str(self.sb.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT task_id, status, pr_state, pr_url, branch, "
                "commit_short, commit_full, completed_at "
                "FROM runs WHERE task_id = ?",
                (TASK_ID,),
            ).fetchone()
            return dict(row) if row is not None else {}
        finally:
            conn.close()

    def _events(self, kind: str) -> list[dict]:
        conn = sqlite3.connect(str(self.sb.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT events.id AS id, events.kind AS kind, "
                "events.payload_json AS payload_json "
                "FROM events JOIN runs ON runs.id = events.run_id "
                "WHERE events.kind = ? AND runs.task_id = ?",
                (kind, TASK_ID),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def test_full_chain_ends_in_merged_state(self):
        # Step 1: Secretary's apply reserves a queued row
        # (gen_delegate_payload.apply_delegate_plan).
        plan = gdp.build_delegate_plan(
            task_id=TASK_ID,
            project_slug="clock-app",
            description="back-fill runs.pr_url at PR-open",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        row_after_apply = self._row()
        self.assertEqual(row_after_apply["status"], "queued")
        # branch is populated from planned_branch but pr_url is not yet
        self.assertIsNone(row_after_apply["pr_url"])

        # Step 2: Secretary creates the PR and immediately runs
        # set_run_pr_open. Mocked `gh pr view` returns the open PR.
        result_open = set_run_pr_open.set_run_pr_open(
            task_id=TASK_ID, pr=PR, repo=REPO,
            db_path=self.sb.db_path, pr_view=_pr_view_open(),
        )
        self.assertEqual(result_open, set_run_pr_open.RESULT_OK)
        row_after_open = self._row()
        self.assertEqual(row_after_open["pr_url"], PR_URL)
        self.assertEqual(row_after_open["branch"], BRANCH)

        # Step 3: pr-watch's MergeWatch loop calls
        # run_complete_on_merge.complete_on_merge with NO --task-id.
        # The auto-resolver hits runs.pr_url first, so the call must
        # succeed without a manual override.
        result_merge = run_complete_on_merge.complete_on_merge(
            pr=PR, repo=REPO,
            db_path=self.sb.db_path,
            pr_view=_pr_view_merged(),
        )
        self.assertEqual(result_merge, run_complete_on_merge.RESULT_MERGED)

        # Final assertions: pr_state='merged', commit_short, pr_url,
        # completed_at all populated; exactly one pr_merged event.
        final = self._row()
        self.assertEqual(final["pr_state"], "merged")
        self.assertEqual(final["pr_url"], PR_URL)
        self.assertEqual(final["commit_full"], MERGE_OID)
        self.assertEqual(final["commit_short"], MERGE_OID[:7])
        self.assertEqual(final["completed_at"], MERGED_AT)
        # runs.status is intentionally NOT flipped here — that's the
        # secretary's manual step (delegation-lifecycle-contract §T5).
        self.assertEqual(final["status"], "queued")

        events = self._events("pr_merged")
        self.assertEqual(len(events), 1)
        payload = json.loads(events[0]["payload_json"])
        self.assertEqual(payload["task"], TASK_ID)
        self.assertEqual(payload["pr"], PR)
        self.assertEqual(payload["merge_commit"], MERGE_OID)

        # Step 4: idempotency — a re-run of run_complete_on_merge must
        # not duplicate the event row.
        result_again = run_complete_on_merge.complete_on_merge(
            pr=PR, repo=REPO,
            db_path=self.sb.db_path,
            pr_view=_pr_view_merged(),
        )
        self.assertEqual(result_again, run_complete_on_merge.RESULT_ALREADY)
        self.assertEqual(len(self._events("pr_merged")), 1)


if __name__ == "__main__":
    unittest.main()
