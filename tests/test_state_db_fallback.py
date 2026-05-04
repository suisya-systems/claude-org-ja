"""M1 read-path fallback tests (Issue #267 review minor #2).

Covers the DB-missing / DB-corrupt / DB-stale branches of:
- dashboard/server.py:_load_state_from_db + _db_is_fresh + build_state
- dashboard/org_state_converter.py:convert(source="auto")

Strategy: monkey-patch the module-level paths to point at a tempdir so the
test suite never touches the real .state/ tree, then assert that each branch
falls through to the markdown reader rather than raising.

Run:  python -m unittest tests.test_state_db_fallback
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.state_db.importer import import_full_rebuild


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_MARKDOWN_FIXTURE = """\
Status: ACTIVE
Updated: 2026-05-04T00:00:00Z
Current Objective: M1 read switch tests

- task-1: Markdown title [IN_PROGRESS]
  - 結果: midway
  - ワーカー: abc12345

## Worker Directory Registry

| Task ID | Pattern | Directory | Project | Status |
|---|---|---|---|---|
| task-1 | B | /tmp/w/task-1 | demo-project | review |

## Active Work Items

- task-1: in progress
"""

_PROJECTS_FIXTURE = """\
| 通称 | プロジェクト名 | パス | 説明 | 例 |
|---|---|---|---|---|
| demo | demo-project | https://example.invalid/d | demo | x |
"""

_JOURNAL_FIXTURE = '{"ts":"2026-05-04T00:00:00Z","event":"resume"}\n'


def _seed_state_dir(state_dir: Path, registry_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "org-state.md").write_text(_MARKDOWN_FIXTURE, encoding="utf-8")
    (state_dir / "journal.jsonl").write_text(_JOURNAL_FIXTURE, encoding="utf-8")
    (registry_dir / "projects.md").write_text(_PROJECTS_FIXTURE, encoding="utf-8")


# ---------------------------------------------------------------------------
# server.build_state fallback tests
# ---------------------------------------------------------------------------


class TestServerFallback(unittest.TestCase):
    """build_state must always succeed; DB issues degrade to markdown."""

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
        import dashboard.server as server  # type: ignore  # noqa: E402
        self.server = server
        # Redirect module-level paths at the tempdir.
        server.BASE_DIR = self.root
        server.STATE_DB_PATH = self.db_path
        server._DB_STALE_WARN_LOGGED = False

    def test_db_missing_falls_back_to_markdown(self):
        """No .state/state.db → build_state still returns markdown-derived items."""
        self.assertFalse(self.db_path.exists())
        state = self.server.build_state()
        ids = [w["id"] for w in state["workItems"]]
        self.assertIn("task-1", ids)
        # markdown-only field flowed through
        self.assertEqual(state["status"], "ACTIVE")
        # activity came from journal.jsonl (markdown-side reader)
        self.assertTrue(any(e.get("event") == "resume" for e in state["activity"]))

    def test_db_corrupt_falls_back_to_markdown(self):
        """Garbage bytes in state.db → connect/query raises → fallback engages."""
        self.db_path.write_bytes(b"this is not a sqlite database")
        # Touch to be newer than markdown so freshness check passes and the
        # DB read attempt actually fires (and then fails inside sqlite3).
        future = time.time() + 60
        import os
        os.utime(self.db_path, (future, future))
        state = self.server.build_state()
        # build_state did not raise; markdown items present.
        self.assertEqual(state["status"], "ACTIVE")
        self.assertGreaterEqual(len(state["workItems"]), 1)
        self.assertEqual(state["workItems"][0]["id"], "task-1")

    def test_db_stale_falls_back_with_warning(self):
        """DB older than markdown → fresh check fails, no DB read, markdown wins."""
        # Build a real DB, then bump markdown mtime so DB is older.
        import_full_rebuild(self.db_path, self.root)
        future = time.time() + 120
        import os
        for p in (self.state_dir / "org-state.md",
                  self.state_dir / "journal.jsonl",
                  self.registry_dir / "projects.md"):
            os.utime(p, (future, future))

        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stderr(buf):
            state = self.server.build_state()
        self.assertEqual(state["status"], "ACTIVE")
        warn = buf.getvalue().lower()
        # Warning must point at the rebuild path so the operator can act on it.
        self.assertIn("older", warn)
        self.assertIn("rebuild", warn)
        # Second invocation should not duplicate the warning (latch).
        buf2 = StringIO()
        with contextlib.redirect_stderr(buf2):
            self.server.build_state()
        self.assertEqual(buf2.getvalue(), "")


# ---------------------------------------------------------------------------
# org_state_converter.convert(source="auto") fallback tests
# ---------------------------------------------------------------------------


class TestConverterAutoFallback(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.state_dir = self.root / ".state"
        self.registry_dir = self.root / "registry"
        _seed_state_dir(self.state_dir, self.registry_dir)
        self.md_path = self.state_dir / "org-state.md"
        self.db_path = self.state_dir / "state.db"
        self.json_path = self.state_dir / "org-state.json"

        if "dashboard.org_state_converter" in sys.modules:
            importlib.reload(sys.modules["dashboard.org_state_converter"])
        from dashboard import org_state_converter  # noqa: E402
        self.converter = org_state_converter

    def _read_json(self) -> dict:
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    def test_auto_no_db_uses_markdown(self):
        ok = self.converter.convert(md_path=self.md_path,
                                     json_path=self.json_path,
                                     source="auto", db_path=self.db_path)
        self.assertTrue(ok)
        data = self._read_json()
        self.assertNotEqual(data.get("_source"), "db")
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["currentObjective"], "M1 read switch tests")

    def test_auto_stale_db_uses_markdown(self):
        import_full_rebuild(self.db_path, self.root)
        # Make markdown newer than DB.
        future = time.time() + 120
        import os
        os.utime(self.md_path, (future, future))
        ok = self.converter.convert(md_path=self.md_path,
                                     json_path=self.json_path,
                                     source="auto", db_path=self.db_path)
        self.assertTrue(ok)
        data = self._read_json()
        self.assertNotEqual(data.get("_source"), "db")

    def test_auto_fresh_db_uses_db(self):
        import_full_rebuild(self.db_path, self.root)
        # Make DB newer than markdown.
        future = time.time() + 120
        import os
        os.utime(self.db_path, (future, future))
        ok = self.converter.convert(md_path=self.md_path,
                                     json_path=self.json_path,
                                     source="auto", db_path=self.db_path)
        self.assertTrue(ok)
        data = self._read_json()
        self.assertEqual(data.get("_source"), "db")
        # Markdown overlay still preserves the markdown-only fields.
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["currentObjective"], "M1 read switch tests")

    def test_explicit_markdown_default_does_not_consult_db(self):
        # Even if DB is fresh, default source='markdown' must produce the
        # markdown shape (non-lossy, includes Resume Instructions etc.).
        import_full_rebuild(self.db_path, self.root)
        ok = self.converter.convert(md_path=self.md_path,
                                     json_path=self.json_path,
                                     db_path=self.db_path)  # default source
        self.assertTrue(ok)
        data = self._read_json()
        self.assertNotIn("_source", data)


if __name__ == "__main__":
    unittest.main()
