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

from tools.state_db import connect
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

    def test_db_missing_returns_uninitialized_guidance(self):
        """No .state/state.db → build_state surfaces UNINITIALIZED so
        the dashboard can render an actionable message instead of
        masquerading as a normal IDLE org (Codex r3 m-1)."""
        self.assertFalse(self.db_path.exists())
        state = self.server.build_state()
        self.assertEqual(state["status"], "UNINITIALIZED")
        obj = (state["objective"] or "")
        self.assertIn("importer", obj.lower())
        # M4: the guidance must be runnable verbatim.
        self.assertIn("--rebuild", obj)
        self.assertIn(".state/state.db", obj)
        self.assertEqual(state["workItems"], [])
        self.assertEqual(state["activity"], [])

    def test_db_present_drives_state(self):
        """state.db exists → status / workItems flow from DB."""
        import_full_rebuild(self.db_path, self.root)
        state = self.server.build_state()
        self.assertEqual(state["status"], "ACTIVE")
        ids = [w["id"] for w in state["workItems"]]
        self.assertIn("task-1", ids)

    def test_set_f_phase_groups_split_correctly(self):
        """Set F §3 — queued lands in reservedItems (RESERVED), in_use
        in workItems (IN_PROGRESS), terminal rows in neither.

        Regression for Issue #352: dashboard /api/state must keep the
        active-reservation, user-visible, and terminal phases separated
        so I8 (queued invisible to user-visible projection) and the
        4-phase UI distinguishability acceptance criterion both hold.
        """
        import_full_rebuild(self.db_path, self.root)
        # Seed one of each phase against the imported project.
        conn = connect(self.db_path)
        try:
            project_row = conn.execute(
                "SELECT id FROM projects LIMIT 1"
            ).fetchone()
            project_id = project_row[0]
            conn.execute(
                "INSERT INTO runs (task_id, project_id, pattern, title, status) "
                "VALUES ('t-queued', ?, 'A', 't-queued', 'queued')",
                (project_id,),
            )
            conn.execute(
                "INSERT INTO runs (task_id, project_id, pattern, title, status) "
                "VALUES ('t-running', ?, 'A', 't-running', 'in_use')",
                (project_id,),
            )
            conn.execute(
                "INSERT INTO runs (task_id, project_id, pattern, title, status) "
                "VALUES ('t-done', ?, 'A', 't-done', 'completed')",
                (project_id,),
            )
            conn.commit()
        finally:
            conn.close()

        state = self.server.build_state()
        work_ids = {w["id"]: w["status"] for w in state["workItems"]}
        reserved_ids = {w["id"]: w["status"] for w in state["reservedItems"]}
        # Reserved bucket only contains queued (§3.1 \\ §3.3).
        self.assertEqual(reserved_ids.get("t-queued"), "RESERVED")
        self.assertNotIn("t-queued", work_ids)
        # Active bucket contains in_use / review only (§3.3).
        self.assertEqual(work_ids.get("t-running"), "IN_PROGRESS")
        self.assertNotIn("t-running", reserved_ids)
        # Terminal rows are filtered out of both surfaces (§3.4).
        self.assertNotIn("t-done", work_ids)
        self.assertNotIn("t-done", reserved_ids)


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

    def test_json_emits_reserved_items_alongside_work_items(self):
        """Set F §3 — queued runs surface in org-state.json as
        reservedItems, parallel to /api/state. Regression for the
        derived-layer drift Codex flagged on the Issue #352 review:
        without this, the JSON projection silently dropped queued rows
        while the HTTP projection rendered them.
        """
        import_full_rebuild(self.db_path, self.root)
        conn = connect(self.db_path)
        try:
            pid = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO runs (task_id, project_id, pattern, title, status) "
                "VALUES ('t-q', ?, 'A', 't-q', 'queued')",
                (pid,),
            )
            conn.commit()
        finally:
            conn.close()
        ok = self.converter.convert(json_path=self.json_path,
                                     db_path=self.db_path)
        self.assertTrue(ok)
        data = self._read_json()
        reserved = {w["id"]: w["status"] for w in data.get("reservedItems", [])}
        work = {w["id"] for w in data.get("workItems", [])}
        self.assertEqual(reserved.get("t-q"), "RESERVED")
        self.assertNotIn("t-q", work)


if __name__ == "__main__":
    unittest.main()
