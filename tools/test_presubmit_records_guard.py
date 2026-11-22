"""The submit tools write no record when the step fails (Issue 1298).

Kept outside the declared test (tests/test_presubmit_records.py) because these
already held before the change: they guard that recording on success did not
start recording on failure. Uses the fixture of tests/test_presubmit.py,
loaded by path so the ``tests`` package __init__ does not run here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
_spec = importlib.util.spec_from_file_location("_presubmit_fixture4", _ROOT / "tests" / "test_presubmit.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


@unittest.skipIf(os.name == "nt", "the fake gh is a shell script (POSIX only)")
class NoRecordOnFailureTests(base.PresubmitTests):
    def events(self, kind: str, task: str) -> list[dict]:
        rows = self.sql("SELECT payload_json FROM events WHERE kind = ? ORDER BY id", (kind,))
        return [p for p in (json.loads(r[0]) for r in rows) if p.get("task") == task]

    def test_a_refused_push_writes_no_record(self):
        self.registry("false")
        wdir = self.worker("r1")
        self.commit(wdir, "work")
        self.tool("run", "--task", "r1")
        proc = self.tool("push", "--task", "r1")
        self.assertIn("PRESUBMIT_PUSH: refused", self.last(proc))
        self.assertEqual(self.events("fix_pushed", "r1"), [])

    def test_a_failed_pr_create_writes_nothing(self):
        fake = self.tmp / "bin"
        fake.mkdir(exist_ok=True)
        (fake / "gh").write_text("#!/bin/sh\necho 'GraphQL: could not create' >&2\nexit 1\n", encoding="utf-8")
        (fake / "gh").chmod(0o755)
        wdir = self.worker("r5")
        self.commit(wdir, "work")
        proc = self.tool("pr", "--task", "r5", "--create", "--title", "work",
                         "--body-file", str(self.body("Refs #1\n")))
        self.assertIn("PRESUBMIT_PR: refused", self.last(proc))
        self.assertEqual(self.events("pr_opened", "r5"), [])
        self.assertIsNone(self.sql("SELECT pr_url FROM runs WHERE task_id = 'r5'")[0][0])


    def test_a_push_still_reminds_to_restart_the_watcher(self):
        self.registry("true")
        wdir = self.worker("r6")
        self.sql("UPDATE runs SET pr_url = 'https://github.com/o/p/pull/40', pr_state = 'open' WHERE task_id = 'r6'")
        self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES "
                 "('2026-11-22T10:30:00.000Z', 'secretary', 'pr_watch_pane_started', ?)",
                 (json.dumps({"task": "r6", "pr": 40, "repo": "o/p", "pane_id": "%1"}),))
        self.commit(wdir, "work")
        self.tool("run", "--task", "r6")
        proc = self.tool("push", "--task", "r6")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # The check ran on the new record (its verdict depends on the clock of the fixture rows).
        self.assertIn("[watcher-guard] PR #40", proc.stderr)


for _name in [n for n in dir(base.PresubmitTests) if n.startswith("test_")]:
    if _name not in NoRecordOnFailureTests.__dict__:
        setattr(NoRecordOnFailureTests, _name, None)

if __name__ == "__main__":
    unittest.main()
