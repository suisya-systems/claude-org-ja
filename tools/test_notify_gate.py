"""Tests for tools.notify_gate (Refs #1226)."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import notify_gate as g  # noqa: E402


class CursorResetTest(unittest.TestCase):
    def test_absent_file_suppresses(self):
        self.assertEqual(g.decide_event_cursor_reset(None), (False, "no_background_worker"))

    def test_empty_and_same_tab_only_suppress(self):
        self.assertFalse(g.decide_event_cursor_reset({})[0])
        st = {"worker-a": {"placement": "same_tab"}, "worker-b": {"idle_streak_cycles": 1}}
        self.assertFalse(g.decide_event_cursor_reset(st)[0])

    def test_one_background_worker_sends(self):
        st = {"worker-a": {"placement": "same_tab"}, "worker-b": {"placement": "background_tab"}}
        self.assertEqual(g.decide_event_cursor_reset(st), (True, "background_worker_present"))

    def test_unknown_placement_and_garbage_send(self):
        self.assertEqual(g.decide_event_cursor_reset({"w": {"placement": "x"}}), (True, "unknown_placement"))
        self.assertEqual(g.decide_event_cursor_reset({"w": 3}), (True, "unreadable_state"))
        self.assertEqual(g.decide_event_cursor_reset([]), (True, "unreadable_state"))


class CuratorTest(unittest.TestCase):
    def test_table(self):
        self.assertFalse(g.decide_curator_vanished("done")[0])
        self.assertFalse(g.decide_curator_vanished("skipped")[0])
        for v in ("null", None, "error", "weird"):
            self.assertTrue(g.decide_curator_vanished(v)[0], v)


class CliTest(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = g.main(argv)
        return rc, json.loads(out.getvalue())

    def test_cli_cursor_reset(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            rc, r = self._run(["--kind", "event_cursor_reset", "--idle-state", str(p)])
            self.assertEqual((rc, r["send"]), (0, False))  # absent
            p.write_text(json.dumps({"w": {"placement": "background_tab"}}), encoding="utf-8")
            self.assertTrue(self._run(["--kind", "event_cursor_reset", "--idle-state", str(p)])[1]["send"])
            p.write_text("null", encoding="utf-8")
            self.assertTrue(self._run(["--kind", "event_cursor_reset", "--idle-state", str(p)])[1]["send"])
            p.write_text("{not json", encoding="utf-8")
            r = self._run(["--kind", "event_cursor_reset", "--idle-state", str(p)])[1]
            self.assertEqual((r["send"], r["reason"]), (True, "unreadable_state"))

    def test_cli_curator(self):
        self.assertFalse(self._run(["--kind", "curator_vanished", "--curate-result", "done"])[1]["send"])
        self.assertTrue(self._run(["--kind", "curator_vanished"])[1]["send"])


if __name__ == "__main__":
    unittest.main()
