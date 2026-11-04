"""Acceptance tests: the secretary's quiet is judged by the watcher, not by idle_prompt.

The record shape is the one seen on the real broker cockpit: no ``idle_at``,
only ``stop_at`` and ``prompt_at`` (a resident background shell keeps Claude
Code from ever sending idle_prompt).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.test_context_handover_watch import _WatcherCase  # noqa: E402


class TestOwnIdle(_WatcherCase):
    role = "secretary"

    def setUp(self):
        super().setUp()
        self.org.transport = lambda: "renga"
        self.org.write_json(self.org.attention_path, {"pane_id": 9})
        self.sim.rec(session_crons=[])  # no idle_at / idle_session_id on purpose

    def test_quiet_without_idle_prompt_after_60s(self):
        self.sim.rec(prompt_at=self.sim.iso(-200), stop_at=self.sim.iso(-90))
        self.assertTrue(self.tick().startswith("ran:done"))

    def test_prompt_after_stop_is_not_quiet(self):
        self.sim.rec(stop_at=self.sim.iso(-200), prompt_at=self.sim.iso(-90))
        self.assertFalse(self.tick().startswith("ran:"))
        self.assertEqual(self.sim.enters, [])

    def test_under_60s_is_not_quiet(self):
        self.sim.rec(prompt_at=self.sim.iso(-200), stop_at=self.sim.iso(-30))
        self.assertFalse(self.tick().startswith("ran:"))
        self.assertEqual(self.sim.enters, [])


if __name__ == "__main__":
    unittest.main()
