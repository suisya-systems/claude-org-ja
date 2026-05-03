"""Tests for tools/sweep_stale_workers.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sweep_stale_workers import (
    classify,
    parse_work_item_statuses,
    task_id_from_worker_md,
)


class TestParseWorkItemStatuses(unittest.TestCase):

    def test_collects_id_to_status(self):
        import tempfile
        text = (
            "## Active Work Items\n"
            "- ip-task: 作業中 [IN_PROGRESS]\n"
            "- rv-task: レビュー [REVIEW]\n"
            "- done-task: 完了 [COMPLETED]\n"
            "- gone-task: 中止 [ABANDONED]\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = Path(f.name)
        try:
            out = parse_work_item_statuses(p)
        finally:
            p.unlink()
        self.assertEqual(out, {
            "ip-task": "IN_PROGRESS",
            "rv-task": "REVIEW",
            "done-task": "COMPLETED",
            "gone-task": "ABANDONED",
        })

    def test_missing_file(self):
        self.assertEqual(parse_work_item_statuses(Path("/no/such/file.md")), {})


class TestClassify(unittest.TestCase):
    """REVIEW must stay live; only COMPLETED/ABANDONED archive by default; orphans
    only archive when caller opts in (handled in main, not classify)."""

    def test_completed_archives(self):
        a, _ = classify("t", {"t": "COMPLETED"}, age_days=1, min_age=7)
        self.assertEqual(a, "archive")

    def test_abandoned_archives(self):
        a, _ = classify("t", {"t": "ABANDONED"}, age_days=1, min_age=7)
        self.assertEqual(a, "archive")

    def test_review_keeps(self):
        a, reason = classify("t", {"t": "REVIEW"}, age_days=1, min_age=7)
        self.assertEqual(a, "keep")
        self.assertIn("REVIEW", reason)

    def test_in_progress_keeps(self):
        a, _ = classify("t", {"t": "IN_PROGRESS"}, age_days=1, min_age=7)
        self.assertEqual(a, "keep")

    def test_orphan_recent_keeps(self):
        a, _ = classify("t", {}, age_days=1, min_age=7)
        self.assertEqual(a, "keep")

    def test_orphan_old_archive_orphan(self):
        a, _ = classify("t", {}, age_days=30, min_age=7)
        self.assertEqual(a, "archive-orphan")


class TestTaskIdFromWorkerMd(unittest.TestCase):

    def test_extracts_task_field(self):
        text = "# Worker\nTask: my-task\nPane ID: pane-1\n"
        self.assertEqual(task_id_from_worker_md(text, "fallback"), "my-task")

    def test_falls_back_to_stem(self):
        self.assertEqual(task_id_from_worker_md("no task field here\n", "stem-id"), "stem-id")


if __name__ == "__main__":
    unittest.main()
