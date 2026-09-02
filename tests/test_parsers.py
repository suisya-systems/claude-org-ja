"""Smoke tests for dashboard/server.py parser functions.

M4 (Issue #267) decommissions the markdown / jsonl parsers
(``_parse_org_state``, ``_parse_journal``) — the dashboard reads
state from ``.state/state.db`` only. The remaining helpers are file-
system-only utilities (workers list, projects table, knowledge index)
and continue to be exercised here.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Add project root so we can import from dashboard.server
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.server import (
    _parse_projects,
    _parse_workers,
    _parse_knowledge,
    build_state,
)
from tools.state_db import connect
from tools.state_db.importer import import_full_rebuild

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
    """``_parse_workers`` renders detail for ids the DB already admitted.

    The eligibility set is supplied by the caller (``build_state`` derives
    it from ``runs.status = 'in_use'``); these tests pass it explicitly, so
    they cover md parsing / directory scoping only.
    """

    def test_happy_path(self):
        workers = _parse_workers(FIXTURES / "workers", {"abc12345"})

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
        workers = _parse_workers(FIXTURES / "nonexistent", {"abc12345"})
        self.assertEqual(workers, [])

    def test_empty_eligibility_returns_nothing(self):
        """An md file on disk is never sufficient on its own."""
        self.assertEqual(_parse_workers(FIXTURES / "workers", set()), [])

    def test_archive_subdir_excluded(self):
        """Issue #264: workers under .state/workers/archive/ must not appear as live.

        Still enforced by the non-recursive glob, independently of the DB
        eligibility gate: even an ``in_use`` id whose md has been archived
        stays out of the panel.
        """
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
            workers = _parse_workers(wdir, {"live", "old"})
            self.assertEqual([w["task"] for w in workers], ["live-task"])


class TestBuildStateLiveWorkers(unittest.TestCase):
    """Issue #264 regression, re-gated on the DB.

    Two predicates must both hold for a worker card: the run is in the
    Set F §3.3 user-visible projection (in_use / review) AND its md file
    still sits directly under .state/workers/. Workers in REVIEW stay
    visible — the pane is still open, awaiting human approval — while an
    md moved to archive/ disappears. The DB gate is the addition: md
    presence alone used to be the whole predicate, so completed /
    abandoned / suspended runs kept rendering as active.
    """

    def _seed(self, base, statuses):
        """Import a minimal org into base/.state/state.db and set statuses."""
        (base / "registry").mkdir(exist_ok=True)
        (base / "registry" / "projects.md").write_text(
            "| 通称 | プロジェクト名 | パス | 説明 | 例 |\n"
            "|---|---|---|---|---|\n"
            "| demo | demo-project | https://example.invalid/d | demo | x |\n",
            encoding="utf-8",
        )
        (base / ".state" / "org-state.md").write_text(
            "# Org State\n\nStatus: ACTIVE\nCurrent Objective: t\n",
            encoding="utf-8",
        )
        db_path = base / ".state" / "state.db"
        import_full_rebuild(db_path, base)
        conn = connect(db_path)
        try:
            pid = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
            for task_id, status in statuses.items():
                conn.execute(
                    "INSERT INTO runs (task_id, project_id, pattern, title, "
                    "status) VALUES (?, ?, 'A', ?, ?)",
                    (task_id, pid, task_id, status),
                )
            conn.commit()
        finally:
            conn.close()
        return db_path

    def test_review_workers_stay_visible_and_archived_disappear(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / ".state" / "workers" / "archive").mkdir(parents=True)
            (base / ".state" / "workers" / "worker-active.md").write_text(
                "Task: active-task\nPane ID: pane-1\nStarted: now\n",
                encoding="utf-8",
            )
            (base / ".state" / "workers" / "worker-review.md").write_text(
                "Task: review-task\nPane ID: pane-2\nStarted: now\n",
                encoding="utf-8",
            )
            (base / ".state" / "workers" / "worker-old.md").write_text(
                "Task: done-task\nPane ID: pane-4\nStarted: now\n",
                encoding="utf-8",
            )
            (base / ".state" / "workers" / "archive" / "worker-arch.md").write_text(
                "Task: archived-task\nPane ID: pane-3\nStarted: ages ago\n",
                encoding="utf-8",
            )
            db_path = self._seed(base, {
                "active": "in_use",
                "review": "review",
                # Still in_use in the DB, but its md lives in archive/ —
                # the #264 directory rule keeps it off the panel.
                "arch": "in_use",
                # md sits in the live directory, but the run is finished —
                # the DB gate keeps it off the panel.
                "old": "completed",
            })

            with patch("dashboard.server.BASE_DIR", base), \
                    patch("dashboard.server.STATE_DB_PATH", db_path):
                state = build_state()

            tasks = sorted(w["task"] for w in state["workers"])
            self.assertEqual(tasks, ["active-task", "review-task"])

    def test_no_db_yields_no_workers(self):
        """Fail-safe: without state.db the panel is empty, not "everything"."""
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / ".state" / "workers" / "archive").mkdir(parents=True)
            (base / ".state" / "workers" / "worker-active.md").write_text(
                "Task: active-task\nPane ID: pane-1\nStarted: now\n",
                encoding="utf-8",
            )
            (base / ".state" / "workers" / "archive" / "worker-old.md").write_text(
                "Task: old-task\nPane ID: pane-3\nStarted: ages ago\n",
                encoding="utf-8",
            )
            (base / "registry").mkdir()
            (base / "registry" / "projects.md").write_text("", encoding="utf-8")

            with patch("dashboard.server.BASE_DIR", base), \
                    patch("dashboard.server.STATE_DB_PATH",
                          base / ".state" / "state.db"):
                state = build_state()

            self.assertEqual(state["workers"], [])


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
