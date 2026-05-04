"""DB read-path tests (Issue #267, updated for M4).

M4 (Issue #267) decommissions the markdown / jsonl fallback paths in
``dashboard/server.py`` and ``dashboard/org_state_converter.py``. The DB
is the only source. These tests cover the two remaining branches:

* DB present → DB drives the dashboard.
* DB missing → ``build_state`` returns a guidance payload (status IDLE,
  objective points to the importer command); ``convert`` returns False.

Run:  python -m unittest tests.test_state_db_fallback
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.state_db.importer import import_full_rebuild


_MARKDOWN_FIXTURE = """\
# Org State

Status: ACTIVE
Updated: 2026-05-04T00:00:00Z
Current Objective: M4 freeze tests

## Worker Directory Registry

| Task ID | Pattern | Directory | Project | Status |
|---|---|---|---|---|
| task-1 | B | /tmp/w/task-1 | demo-project | review |
"""

_PROJECTS_FIXTURE = """\
| 通称 | プロジェクト名 | パス | 説明 | 例 |
|---|---|---|---|---|
| demo | demo-project | https://example.invalid/d | demo | x |
"""


def _seed_state_dir(state_dir: Path, registry_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "org-state.md").write_text(_MARKDOWN_FIXTURE, encoding="utf-8")
    (registry_dir / "projects.md").write_text(_PROJECTS_FIXTURE, encoding="utf-8")


class TestServerDbRead(unittest.TestCase):
    """build_state must use DB when present and degrade gracefully when absent."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.state_dir = self.root / ".state"
        self.registry_dir = self.root / "registry"
        _seed_state_dir(self.state_dir, self.registry_dir)
        self.db_path = self.state_dir / "state.db"

        # Fresh import the server module bound to our tempdir.
        if "dashboard.server" in sys.modules:
            del sys.modules["dashboard.server"]
        import dashboard.server as server  # noqa: E402
        self.server = server
        server.BASE_DIR = self.root
        server.STATE_DB_PATH = self.db_path

    def test_db_missing_returns_idle_guidance(self):
        """No .state/state.db → build_state returns IDLE with importer guidance."""
        self.assertFalse(self.db_path.exists())
        state = self.server.build_state()
        self.assertEqual(state["status"], "IDLE")
        self.assertIn("importer", (state["objective"] or "").lower())
        self.assertEqual(state["workItems"], [])
        self.assertEqual(state["activity"], [])

    def test_db_present_drives_state(self):
        """state.db exists → status / workItems flow from DB."""
        import_full_rebuild(self.db_path, self.root)
        state = self.server.build_state()
        self.assertEqual(state["status"], "ACTIVE")
        ids = [w["id"] for w in state["workItems"]]
        self.assertIn("task-1", ids)


class TestConverterDbOnly(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.state_dir = self.root / ".state"
        self.registry_dir = self.root / "registry"
        _seed_state_dir(self.state_dir, self.registry_dir)
        self.db_path = self.state_dir / "state.db"
        self.json_path = self.state_dir / "org-state.json"

        if "dashboard.org_state_converter" in sys.modules:
            importlib.reload(sys.modules["dashboard.org_state_converter"])
        from dashboard import org_state_converter  # noqa: E402
        self.converter = org_state_converter

    def _read_json(self) -> dict:
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    def test_missing_db_returns_false(self):
        ok = self.converter.convert(json_path=self.json_path,
                                     db_path=self.db_path)
        self.assertFalse(ok)
        self.assertFalse(self.json_path.exists())

    def test_db_present_writes_json(self):
        import_full_rebuild(self.db_path, self.root)
        ok = self.converter.convert(json_path=self.json_path,
                                     db_path=self.db_path)
        self.assertTrue(ok)
        data = self._read_json()
        self.assertEqual(data.get("_source"), "db")
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["currentObjective"], "M4 freeze tests")


if __name__ == "__main__":
    unittest.main()
