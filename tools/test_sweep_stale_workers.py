"""Tests for tools/sweep_stale_workers.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sweep_stale_workers import parse_live_task_ids, task_id_from_worker_md


class TestParseLiveTaskIds(unittest.TestCase):

    def test_picks_only_non_terminal_statuses(self):
        text = (
            "## Active Work Items\n"
            "- live-a: live one [IN_PROGRESS]\n"
            "- live-b: also live [REVIEW_PENDING]\n"
            "- done: finished [COMPLETED]\n"
            "- gone: abandoned [ABANDONED]\n"
            "- review: in review [REVIEW]\n"
        )
        out = self._parse_text(text)
        self.assertEqual(out, {"live-a", "live-b"})

    def test_missing_file(self):
        self.assertEqual(parse_live_task_ids(Path("/no/such/file.md")), set())

    def _parse_text(self, text: str) -> set[str]:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = Path(f.name)
        try:
            return parse_live_task_ids(p)
        finally:
            p.unlink()


class TestTaskIdFromWorkerMd(unittest.TestCase):

    def test_extracts_task_field(self):
        text = "# Worker\nTask: my-task\nPane ID: pane-1\n"
        self.assertEqual(task_id_from_worker_md(text, "fallback"), "my-task")

    def test_falls_back_to_stem(self):
        self.assertEqual(task_id_from_worker_md("no task field here\n", "stem-id"), "stem-id")


if __name__ == "__main__":
    unittest.main()
