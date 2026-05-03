"""Smoke tests for dashboard/server.py parser functions."""

import sys
import unittest
from pathlib import Path

# Add project root so we can import from dashboard.server
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.server import (
    _parse_org_state,
    _parse_journal,
    _parse_projects,
    _parse_workers,
    _parse_knowledge,
    build_state,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParseOrgState(unittest.TestCase):

    def test_happy_path(self):
        text = (FIXTURES / "org-state-sample.md").read_text(encoding="utf-8")
        status, objective, work_items = _parse_org_state(text)

        self.assertEqual(status, "ACTIVE")
        self.assertEqual(objective, "ダッシュボードの改善")
        self.assertEqual(len(work_items), 2)

        wi0 = work_items[0]
        self.assertEqual(wi0["id"], "task-1")
        self.assertEqual(wi0["title"], "サンプルタスク")
        self.assertEqual(wi0["status"], "IN_PROGRESS")
        self.assertEqual(wi0["progress"], "50%完了")
        self.assertEqual(wi0["worker"], "abc12345")

        wi1 = work_items[1]
        self.assertEqual(wi1["id"], "task-2")
        self.assertEqual(wi1["status"], "COMPLETED")
        self.assertIsNone(wi1["progress"])
        self.assertIsNone(wi1["worker"])

    def test_empty_input(self):
        status, objective, work_items = _parse_org_state("")
        self.assertEqual(status, "IDLE")
        self.assertIsNone(objective)
        self.assertEqual(work_items, [])


class TestParseJournal(unittest.TestCase):

    def test_happy_path(self):
        text = (FIXTURES / "journal-sample.jsonl").read_text(encoding="utf-8")
        result = _parse_journal(text)

        # 3 valid JSON lines (malformed and blank skipped), reversed
        self.assertEqual(len(result), 3)

        # Reversed order: last valid event first
        self.assertEqual(result[0]["event"], "resume")
        self.assertEqual(result[0]["summary"], "組織を再開")

        self.assertEqual(result[1]["event"], "worker_closed")
        self.assertIn("ワーカー終了", result[1]["summary"])

        self.assertEqual(result[2]["event"], "worker_spawned")
        self.assertIn("ワーカー派遣", result[2]["summary"])
        # Worker ID truncated to 8 chars
        self.assertIn("abc12345", result[2]["summary"])
        self.assertNotIn("abc12345-long-id", result[2]["summary"])

    def test_empty_input(self):
        result = _parse_journal("")
        self.assertEqual(result, [])


class TestParseProjects(unittest.TestCase):

    def test_happy_path(self):
        text = (FIXTURES / "projects-sample.md").read_text(encoding="utf-8")
        projects = _parse_projects(text)

        self.assertEqual(len(projects), 2)

        self.assertEqual(projects[0]["name"], "時計アプリ")
        self.assertEqual(projects[0]["path"], "apps/clock")
        self.assertEqual(projects[0]["description"], "Webブラウザで動くデジタル時計")
        self.assertEqual(projects[0]["tasks"], ["デザイン変更", "機能追加"])

        self.assertEqual(projects[1]["name"], "ブログ")
        self.assertEqual(projects[1]["tasks"], ["記事追加"])

    def test_empty_input(self):
        projects = _parse_projects("")
        self.assertEqual(projects, [])


class TestParseWorkers(unittest.TestCase):

    def test_happy_path(self):
        workers = _parse_workers(FIXTURES / "workers")

        self.assertEqual(len(workers), 1)
        w = workers[0]
        self.assertEqual(w["id"], "abc12345")
        self.assertEqual(w["shortId"], "abc12345"[:8])
        self.assertEqual(w["task"], "task-1")
        self.assertEqual(w["paneId"], "worker-task-1")
        self.assertEqual(w["started"], "2026-04-10T10:00:00Z")
        self.assertEqual(w["lastProgress"], "実装完了、テスト中")
        self.assertEqual(w["lastProgressTs"], "2026-04-10T10:30:00Z")

    def test_nonexistent_dir(self):
        workers = _parse_workers(FIXTURES / "nonexistent")
        self.assertEqual(workers, [])

    def test_archive_subdir_excluded(self):
        """Issue #264: workers under .state/workers/archive/ must not appear as live."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            (wdir / "worker-live.md").write_text(
                "Task: live-task\nPane ID: pane-1\nStarted: now\n",
                encoding="utf-8",
            )
            (wdir / "archive").mkdir()
            (wdir / "archive" / "worker-old.md").write_text(
                "Task: old-task\nPane ID: pane-9\nStarted: long ago\n",
                encoding="utf-8",
            )
            workers = _parse_workers(wdir)
            self.assertEqual([w["task"] for w in workers], ["live-task"])


class TestBuildStateLiveWorkers(unittest.TestCase):
    """Issue #264 regression: live worker list = files in .state/workers/ root only.

    Workers in REVIEW must remain visible (pane is still open, awaiting human approval).
    Workers whose md file has been moved to archive/ must NOT appear as live, regardless
    of whether their task id still appears in org-state.md Active Work Items.
    """

    def test_review_workers_stay_visible_and_archived_disappear(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / ".state" / "workers" / "archive").mkdir(parents=True)
            (base / ".state" / "workers" / "worker-active.md").write_text(
                "Task: active-task\nPane ID: pane-1\nStarted: now\n", encoding="utf-8",
            )
            (base / ".state" / "workers" / "worker-review.md").write_text(
                "Task: review-task\nPane ID: pane-2\nStarted: now\n", encoding="utf-8",
            )
            (base / ".state" / "workers" / "archive" / "worker-old.md").write_text(
                "Task: old-task\nPane ID: pane-3\nStarted: ages ago\n", encoding="utf-8",
            )
            (base / ".state" / "org-state.md").write_text(
                "## Active Work Items\n"
                "- active-task: 作業中 [IN_PROGRESS]\n"
                "- review-task: レビュー中 [REVIEW]\n",
                encoding="utf-8",
            )
            (base / "registry").mkdir()
            (base / "registry" / "projects.md").write_text("", encoding="utf-8")

            with patch("dashboard.server.BASE_DIR", base):
                state = build_state()

            tasks = sorted(w["task"] for w in state["workers"])
            self.assertEqual(tasks, ["active-task", "review-task"])


class TestParseKnowledge(unittest.TestCase):

    def test_happy_path(self):
        result = _parse_knowledge(FIXTURES / "curated")

        # .gitkeep should be skipped
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["theme"], "sample topic")
        self.assertEqual(result[0]["count"], 3)

    def test_nonexistent_dir(self):
        result = _parse_knowledge(FIXTURES / "nonexistent")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
