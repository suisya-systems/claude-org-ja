"""
Smoke tests for org-state JSON snapshot (Issue #20).

Covers:
1. parse_org_state_md() — Markdown -> dict conversion (all fields)
2. convert() — file I/O with atomic write
3. Dashboard _load_org_state_from_json() — JSON -> dashboard format
4. Staleness fallback — JSON older than MD returns None

Run:  python tests/test_org_state_converter.py
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Allow imports from project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

from dashboard.org_state_converter import parse_org_state_md, convert, SCHEMA_VERSION

# ---------------------------------------------------------------------------
# Sample org-state.md content for testing
# ---------------------------------------------------------------------------

SAMPLE_ORG_STATE_MD = """\
# Organization State

Status: ACTIVE
Updated: 2026-04-10T18:00:00+09:00
Current Objective: Issue #20 org-state JSON snapshot

## Active Work Items

- org-state-json: org-state JSONスナップショット追加 [IN_PROGRESS]
  - 結果: converter実装完了、テスト作成中
  - ワーカー: abc12345xyz

- blog-redesign: ブログリデザイン [COMPLETED]
  - 結果: 全ページ対応完了

## Worker Directory Registry

| Task ID | Pattern | Directory | Project | Status |
|---|---|---|---|---|
| org-state-json | B | C:\\Users\\iwama\\work\\sandbox\\workers\\aainc-wezterm\\.worktrees\\org-state-json | aainc-wezterm | in_use |
| blog-redesign | A | C:\\Users\\iwama\\work\\sandbox\\blog | blog | available |

## Foreman

- Peer ID: peer-foreman-001
- Pane ID: pane-42

## Curator

- Peer ID: peer-curator-002
- Pane ID: pane-43

## Resume Instructions

前回の作業を引き継ぐこと。
org-state-json タスクのテストを完了させる。
"""


class TestParseOrgStateMd(unittest.TestCase):
    """Test parse_org_state_md() extracts all fields correctly."""

    def setUp(self):
        self.result = parse_org_state_md(SAMPLE_ORG_STATE_MD)

    def test_version(self):
        self.assertEqual(self.result["version"], SCHEMA_VERSION)

    def test_status(self):
        self.assertEqual(self.result["status"], "ACTIVE")

    def test_updated(self):
        self.assertEqual(self.result["updated"], "2026-04-10T18:00:00+09:00")

    def test_current_objective(self):
        self.assertEqual(self.result["currentObjective"], "Issue #20 org-state JSON snapshot")

    def test_work_items_count(self):
        self.assertEqual(len(self.result["workItems"]), 2)

    def test_work_item_in_progress(self):
        wi = self.result["workItems"][0]
        self.assertEqual(wi["id"], "org-state-json")
        self.assertEqual(wi["title"], "org-state JSONスナップショット追加")
        self.assertEqual(wi["status"], "IN_PROGRESS")
        self.assertEqual(wi["progress"], "converter実装完了、テスト作成中")
        self.assertEqual(wi["worker"], "abc12345xyz")

    def test_work_item_completed(self):
        wi = self.result["workItems"][1]
        self.assertEqual(wi["id"], "blog-redesign")
        self.assertEqual(wi["status"], "COMPLETED")
        self.assertEqual(wi["progress"], "全ページ対応完了")
        self.assertIsNone(wi["worker"])

    def test_worker_directory_registry(self):
        reg = self.result["workerDirectoryRegistry"]
        self.assertEqual(len(reg), 2)
        self.assertEqual(reg[0]["taskId"], "org-state-json")
        self.assertEqual(reg[0]["pattern"], "B")
        self.assertEqual(reg[0]["status"], "in_use")
        self.assertEqual(reg[1]["taskId"], "blog-redesign")
        self.assertEqual(reg[1]["status"], "available")

    def test_foreman(self):
        self.assertIsNotNone(self.result["foreman"])
        self.assertEqual(self.result["foreman"]["peerId"], "peer-foreman-001")
        self.assertEqual(self.result["foreman"]["paneId"], "pane-42")

    def test_curator(self):
        self.assertIsNotNone(self.result["curator"])
        self.assertEqual(self.result["curator"]["peerId"], "peer-curator-002")
        self.assertEqual(self.result["curator"]["paneId"], "pane-43")

    def test_resume_instructions(self):
        ri = self.result["resumeInstructions"]
        self.assertIsNotNone(ri)
        self.assertIn("前回の作業を引き継ぐ", ri)
        self.assertIn("テストを完了させる", ri)


class TestParseEmptyMd(unittest.TestCase):
    """Test parse_org_state_md() with empty/minimal input."""

    def test_empty_string(self):
        result = parse_org_state_md("")
        self.assertEqual(result["status"], "IDLE")
        self.assertIsNone(result["currentObjective"])
        self.assertEqual(result["workItems"], [])
        self.assertIsNone(result["foreman"])
        self.assertIsNone(result["curator"])
        self.assertIsNone(result["resumeInstructions"])

    def test_status_only(self):
        result = parse_org_state_md("Status: SUSPENDED\n")
        self.assertEqual(result["status"], "SUSPENDED")


class TestConvertFileIO(unittest.TestCase):
    """Test convert() writes valid JSON atomically."""

    def test_convert_creates_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "org-state.md"
            json_path = Path(tmpdir) / "org-state.json"
            md_path.write_text(SAMPLE_ORG_STATE_MD, encoding="utf-8")

            ok = convert(md_path=md_path, json_path=json_path)
            self.assertTrue(ok)
            self.assertTrue(json_path.exists())

            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], SCHEMA_VERSION)
            self.assertEqual(data["status"], "ACTIVE")
            self.assertEqual(len(data["workItems"]), 2)

    def test_convert_missing_md_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "nonexistent.md"
            json_path = Path(tmpdir) / "org-state.json"
            ok = convert(md_path=md_path, json_path=json_path)
            self.assertFalse(ok)
            self.assertFalse(json_path.exists())


class TestDashboardJsonLoad(unittest.TestCase):
    """Test dashboard server's _load_org_state_from_json() reads JSON correctly."""

    def setUp(self):
        # Import the dashboard function
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "server", str(ROOT / "dashboard" / "server.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._load = mod._load_org_state_from_json

    def test_load_from_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            md_path = state_dir / "org-state.md"
            json_path = state_dir / "org-state.json"

            # Write both MD and JSON (JSON newer)
            md_path.write_text(SAMPLE_ORG_STATE_MD, encoding="utf-8")
            time.sleep(0.05)  # ensure JSON mtime > MD mtime
            data = parse_org_state_md(SAMPLE_ORG_STATE_MD)
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            result = self._load(state_dir)
            self.assertIsNotNone(result)
            status, objective, work_items = result
            self.assertEqual(status, "ACTIVE")
            self.assertEqual(objective, "Issue #20 org-state JSON snapshot")
            self.assertEqual(len(work_items), 2)
            self.assertEqual(work_items[0]["id"], "org-state-json")

    def test_stale_json_returns_none(self):
        """When JSON is older than MD, fallback to None (MD parsing)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            md_path = state_dir / "org-state.md"
            json_path = state_dir / "org-state.json"

            # Write JSON first, then MD (MD newer)
            json_path.write_text("{}", encoding="utf-8")
            time.sleep(0.05)
            md_path.write_text(SAMPLE_ORG_STATE_MD, encoding="utf-8")

            result = self._load(state_dir)
            self.assertIsNone(result)

    def test_missing_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            md_path = state_dir / "org-state.md"
            md_path.write_text(SAMPLE_ORG_STATE_MD, encoding="utf-8")

            result = self._load(state_dir)
            self.assertIsNone(result)

    def test_invalid_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            md_path = state_dir / "org-state.md"
            json_path = state_dir / "org-state.json"

            md_path.write_text(SAMPLE_ORG_STATE_MD, encoding="utf-8")
            time.sleep(0.05)  # ensure invalid JSON is newer than MD
            json_path.write_text("{invalid", encoding="utf-8")

            result = self._load(state_dir)
            self.assertIsNone(result)

    def test_invalid_work_item_shape_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            md_path = state_dir / "org-state.md"
            json_path = state_dir / "org-state.json"

            md_path.write_text(SAMPLE_ORG_STATE_MD, encoding="utf-8")
            time.sleep(0.05)
            json_path.write_text(
                json.dumps(
                    {
                        "status": "ACTIVE",
                        "currentObjective": "broken payload",
                        "workItems": [{"id": "task-without-title"}],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = self._load(state_dir)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
