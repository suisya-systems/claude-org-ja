"""Tests for tools/defense_report.py and the defense register (Issue #1191)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import defense_report as dr  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# .hooks scripts that are not failure-stopping defenses.
NOT_DEFENSES = {"context-record", "fixture-always-block"}


class RegisterTest(unittest.TestCase):
    def test_every_hook_is_registered(self):
        ids = dr.hook_ids(dr.load_register(dr.REGISTER_PATH))
        hooks = {p.stem for p in (ROOT / ".hooks").glob("*.sh")} - NOT_DEFENSES
        self.assertEqual(sorted(hooks - ids), [],
                         "add the hook to docs/operations/defense-register.md")
        self.assertEqual(sorted(ids - hooks), [], "register lists a hook that is gone")


class RegisterPathTest(unittest.TestCase):
    def test_register_is_read_from_this_checkout_not_the_shared_one(self):
        self.assertEqual(dr.REGISTER_PATH, ROOT / dr.REGISTER)
        self.assertTrue(dr.REGISTER_PATH.is_file())

    def test_non_hook_rows_do_not_count_as_missing_hooks(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.md"
            f.write_text("| id | 種別 | パス | 止める失敗 |\n|---|---|---|---|\n"
                         "| t1 | test | `tests/x.py` | x |\n", encoding="utf-8")
            rows = dr.load_register(f)
        self.assertEqual(dr.hook_ids(rows), set())


class ReportTest(unittest.TestCase):
    def test_counts_never_and_overblock(self):
        reg = [{"id": i, "type": "hook", "path": "", "failure": ""} for i in "abc"]
        t = datetime(2026, 10, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            conn = connect(Path(d) / "s.db")
            apply_schema(conn)
            rows = [("a", t), ("a", t + timedelta(minutes=2)),
                    ("a", t + timedelta(minutes=4)), ("b", t)]
            for did, at in rows:
                conn.execute(
                    "INSERT INTO events (occurred_at, actor, kind, payload_json) "
                    "VALUES (?, 'hook', ?, ?)",
                    (at.strftime("%Y-%m-%dT%H:%M:%SZ"), dr.DENY_KIND,
                     json.dumps({dr.DEFENSE_FIELD: did + ".sh"})))
            conn.commit()
            stops = dr.collect(conn, t - timedelta(days=1))
            conn.close()
        out = dr.render(reg, stops, 30)
        self.assertIn("| a | 3 | 2 |", out)
        self.assertIn("| b | 1 | 0 |", out)
        self.assertIn("一度も止めていない: c", out)
        self.assertIn("止めすぎの疑い（近似。正しい回避かすり抜けかは人が見る）: a", out)


if __name__ == "__main__":
    unittest.main()
