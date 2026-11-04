"""Acceptance tests: quiet judgment with the resident background work seen on a real broker cockpit."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUEUE_WATCHER = {"id": "b1", "type": "shell", "status": "running",
                 "description": "secretary queue watcher",
                 "command": "python3 tools/secretary_queue_watcher.py --state-dir .state"}
LIVE_UPDATES = {"id": "m1", "type": "monitor",
                "description": "live updates for artifact https://claude.ai/artifact/abc"}
OTHER_SHELL = {"id": "b2", "type": "shell", "status": "running",
               "description": "dev server", "command": "python3 -m http.server 8000"}
OTHER_MONITOR = {"id": "m2", "type": "monitor", "description": "tail build log"}


def _base():
    return {"session_id": "s", "stop_session_id": "s", "pane": {"backend": "tmux", "id": "%1"},
            "turn_open": 2, "turn_closed": 2, "stop_at": "2026-10-18T00:09:00Z",
            "background_tasks": []}


class TestQuiet(unittest.TestCase):
    def setUp(self):
        from tools import context_handover_watch as watch
        self.watch = watch
        self.now = watch.parse_iso("2026-10-18T00:10:00Z")

    def reason(self, **changes):
        return self.watch.not_quiet_reason({**_base(), **changes}, self.now)

    def test_resident_background_work_is_quiet(self):
        self.assertIsNone(self.reason(background_tasks=[QUEUE_WATCHER, LIVE_UPDATES]))

    def test_other_background_work_is_not_quiet(self):
        for extra in (OTHER_SHELL, OTHER_MONITOR):
            self.assertEqual(self.reason(background_tasks=[QUEUE_WATCHER, LIVE_UPDATES, extra]),
                             "background_tasks")

    def test_idle_after_stop_is_not_stale(self):
        late = {"stop_at": "2026-10-17T23:00:00Z", "idle_session_id": "s",
                "idle_at": "2026-10-17T23:05:00Z"}
        self.assertIsNone(self.reason(**late))
        self.assertEqual(self.reason(stop_at=late["stop_at"]), "stop_stale")


if __name__ == "__main__":
    unittest.main()
