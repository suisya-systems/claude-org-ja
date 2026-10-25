"""Tests for tools/spawn_watch_pane.py (Issue #1216).

The renga MCP bridge is replaced by a fake that keeps a pane table, so the
suite never touches a live renga server.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import spawn_watch_pane as swp  # noqa: E402

ALL_TABS = """Panes in every tab (yours first):

- id=1 name=secretary role=secretary (focused) [your tab]
  geometry: x=0 y=0 width=200 height=60
  cwd: /ja
- id=2 name=dispatcher role=dispatcher [your tab]
  geometry: x=200 y=0 width=80 height=60
- id=9 name=pr-watch-7 role=watcher [tab 2 "監視"]
  geometry: x=0 y=0 width=200 height=60
- id=5 name=worker-x role=worker [tab 1 "workers"]
  geometry: x=0 y=0 width=200 height=60
"""

OWN_TAB = """Panes in this tab:

- id=1 name=secretary role=secretary (focused)
  geometry: x=0 y=0 width=200 height=60
- id=2 name=dispatcher role=dispatcher
  geometry: x=200 y=0 width=120 height=60
"""


class FakeRenga:
    def __init__(self, all_tabs: str = ALL_TABS, next_id: int = 40) -> None:
        self.all_tabs = all_tabs
        self.next_id = next_id
        self.calls: "list[tuple[str, dict]]" = []

    def __call__(self, tool: str, args: dict) -> "tuple[bool, str]":
        self.calls.append((tool, args))
        if tool == "list_panes":
            return True, self.all_tabs if args.get("tab") == {"all": True} else OWN_TAB
        if tool == "spawn_pane":
            self.next_id += 1
            return True, f"Spawned pane id={self.next_id}."
        return True, "ok"

    def spawns(self) -> "list[dict]":
        return [a for t, a in self.calls if t == "spawn_pane"]


class SpawnWatchPaneTests(unittest.TestCase):
    def _run(self, fake: FakeRenga, delivered: "bool | None") -> int:
        with mock.patch.dict(os.environ, {"RENGA_PANE_ID": "1"}), \
             mock.patch.object(swp, "_call", fake), \
             mock.patch.object(swp, "_max_event_id", return_value=0), \
             mock.patch.object(swp, "wait_started", return_value=delivered):
            return swp.run(123, "octo/repo", True, Path("unused.db"))

    def test_parse_pane_list_reads_tabs_and_geometry(self) -> None:
        panes = {p["id"]: p for p in swp.parse_pane_list(ALL_TABS)}
        self.assertTrue(panes[1]["same_tab"])
        self.assertEqual(panes[9]["tab_label"], "監視")
        self.assertFalse(panes[9]["same_tab"])
        self.assertEqual((panes[2]["x"], panes[2]["width"]), (200, 80))

    def test_splits_existing_watch_tab_and_notifies_by_numeric_id(self) -> None:
        fake = FakeRenga()
        self.assertEqual(self._run(fake, True), 0)
        [spawn] = fake.spawns()
        self.assertEqual(spawn["tab"], {"pane_id": 9})
        self.assertEqual(spawn["target"], "9")
        self.assertIn("export ORG_NOTIFY_TO=1;", spawn["command"])
        self.assertIn('renga close --id "$RENGA_PANE_ID"', spawn["command"])
        self.assertNotIn("close_pane", [t for t, _ in fake.calls])

    def test_opens_background_tab_when_none_exists(self) -> None:
        fake = FakeRenga(all_tabs=OWN_TAB.replace("this tab", "every tab"))
        self.assertEqual(self._run(fake, True), 0)
        [spawn] = fake.spawns()
        self.assertEqual(spawn["tab"], {"new": {"name": "監視"}})
        self.assertNotIn("target", spawn)

    def test_undelivered_ping_closes_and_falls_back_to_own_tab(self) -> None:
        fake = FakeRenga()
        self.assertEqual(self._run(fake, False), 1)
        first, second = fake.spawns()
        self.assertIn(("close_pane", {"target": "41"}), fake.calls)
        self.assertNotIn("tab", second)
        self.assertNotIn("ORG_NOTIFY_TO", second["command"])
        self.assertIn(second["target"], {"1", "2"})

    def test_same_pr_already_running_is_not_spawned_twice(self) -> None:
        fake = FakeRenga(all_tabs=ALL_TABS.replace("pr-watch-7", "pr-watch-123"))
        self.assertEqual(self._run(fake, True), 0)
        self.assertEqual(fake.spawns(), [])

    def test_other_orgs_watch_tab_is_ignored(self) -> None:
        fake = FakeRenga(all_tabs=ALL_TABS.replace(
            '[tab 2 "監視"]\n  geometry: x=0 y=0 width=200 height=60',
            '[tab 2 "監視"]\n  geometry: x=0 y=0 width=200 height=60\n  cwd: /other-org'))
        self.assertEqual(self._run(fake, True), 0)
        [spawn] = fake.spawns()
        self.assertEqual(spawn["tab"], {"new": {"name": "監視"}})

    def test_no_all_tab_list_stays_in_own_tab(self) -> None:
        fake = FakeRenga()
        inner = fake.__call__

        def call(tool: str, args: dict) -> "tuple[bool, str]":
            if tool == "list_panes" and args.get("tab"):
                fake.calls.append((tool, args))
                return False, "[server_too_old] cross_tab_list"
            return inner(tool, args)

        with mock.patch.dict(os.environ, {"RENGA_PANE_ID": "1"}), \
             mock.patch.object(swp, "_call", call):
            self.assertEqual(swp.run(123, "octo/repo", True, Path("unused.db")), 1)
        [spawn] = fake.spawns()
        self.assertNotIn("tab", spawn)
        self.assertNotIn("ORG_NOTIFY_TO", spawn["command"])

    def test_broker_does_nothing(self) -> None:
        fake = FakeRenga()
        with mock.patch("tools.transport.resolve", return_value="broker"), \
             mock.patch.object(swp, "_call", fake):
            self.assertEqual(swp.run(123, "octo/repo", True, Path("unused.db")), 3)
        self.assertEqual(fake.calls, [])

    def test_wait_started_reads_the_watcher_event(self) -> None:
        from tools.state_db import apply_schema, connect
        from tools.state_db.writer import StateWriter

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            conn = connect(db)
            apply_schema(conn)
            writer = StateWriter(conn)
            writer.append_event(kind="pr_watch_started", actor="pr_watch",
                                payload={"pr": 99, "delivered": True})
            writer.commit()
            conn.close()
            after = swp._max_event_id(db)
            conn = connect(db)
            writer = StateWriter(conn)
            writer.append_event(kind="pr_watch_started", actor="pr_watch",
                                payload={"pr": 123, "delivered": False})
            writer.commit()
            conn.close()
            self.assertIs(swp.wait_started(db, 123, 0, timeout=0), False)
            self.assertIsNone(swp.wait_started(db, 99, after, timeout=0))


if __name__ == "__main__":
    unittest.main()
