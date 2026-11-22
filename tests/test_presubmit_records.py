"""Acceptance tests: the submit tools write their own records (Issue 1298).

On 2026-11-22 the secretary ran ``presubmit.py run`` -> ``presubmit.py push``
-> ``journal_append.sh fix_pushed`` in one go; the run failed, the push was
refused, and the hand-written ``fix_pushed`` was recorded anyway (park and the
CI watcher read that record). A record typed by hand after a step does not see
whether the step succeeded.

So ``presubmit.py push`` writes ``fix_pushed`` itself, in the same call and
only after the push succeeded, with the SHA it actually sent; and
``presubmit.py pr --create`` writes ``pr_opened`` and the run's PR record
(``runs.pr_url`` / ``pr_state`` / ``branch``, what ``set_run_pr_open`` did) once
gh has created the PR. Nothing is written when the step fails.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import test_presubmit as base  # noqa: E402  (a module, so its tests are not collected twice)

TRAILER = "\n\nAcceptance-Test: tests/test_x.py\n"


@unittest.skipIf(os.name == "nt", "the fake gh is a shell script (POSIX only)")
class RecordTests(base.PresubmitTests):
    def events(self, kind: str, task: str) -> list[dict]:
        rows = self.sql("SELECT payload_json FROM events WHERE kind = ? ORDER BY id", (kind,))
        return [p for p in (json.loads(r[0]) for r in rows) if p.get("task") == task]

    def test_a_push_records_the_sha_it_sent(self):
        self.registry("true")
        wdir = self.worker("r2")
        self.commit(wdir, "work")
        self.tool("run", "--task", "r2")
        proc = self.tool("push", "--task", "r2")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        (ev,) = self.events("fix_pushed", "r2") or [None]
        self.assertIsNotNone(ev, "no fix_pushed after a successful push")
        self.assertEqual(ev["head"], self.remote_head("feat/r2"))
        self.assertEqual(ev["branch"], "feat/r2")
        self.assertEqual(ev["source"], "presubmit_push")

    def test_the_test_commit_push_records_the_test_commit(self):
        self.registry("true")
        wdir = self.worker("r3")
        m = self.commit(wdir, "test: failing test" + TRAILER, "tests_x.txt")
        self.commit(wdir, "fix")
        self.tool("run", "--task", "r3")
        proc = self.tool("push", "--task", "r3", "--test-commit")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        heads = [e["head"] for e in self.events("fix_pushed", "r3")]
        self.assertEqual(heads, [m])

    def test_creating_the_pr_records_it(self):
        self.fake_gh()
        wdir = self.worker("r4")
        self.sql("UPDATE runs SET pr_url = 'https://github.com/o/p/pull/1', pr_state = 'merged' WHERE task_id = 'r4'")
        self.commit(wdir, "work")
        proc = self.tool("pr", "--task", "r4", "--create", "--title", "work",
                         "--body-file", str(self.body("Refs #1\n")))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        row = self.sql("SELECT pr_url, pr_state, branch FROM runs WHERE task_id = 'r4'")[0]
        self.assertEqual(row, ("https://github.com/o/p/pull/12", "open", "feat/r4"))
        opened = self.events("pr_opened", "r4")
        self.assertEqual([(e["pr"], e["url"]) for e in opened], [(12, "https://github.com/o/p/pull/12")])



for _name in [n for n in dir(base.PresubmitTests) if n.startswith("test_")]:
    if _name not in RecordTests.__dict__:
        setattr(RecordTests, _name, None)

if __name__ == "__main__":
    unittest.main()
