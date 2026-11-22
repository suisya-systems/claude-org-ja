"""When ``presubmit.py run`` reuses an earlier pass, and when it does not (Issue 1298).

Uses the fixture of tests/test_presubmit.py, loaded by path so the ``tests``
package __init__ (which scrubs the transport env for the whole process) does
not run here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: presubmit.py runs without the lock there
    fcntl = None

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
_spec = importlib.util.spec_from_file_location("_presubmit_fixture2", _ROOT / "tests" / "test_presubmit.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


class RerunTests(base.PresubmitTests):
    def test_a_moved_base_runs_the_check_again(self):
        counter = self.tmp / "runs.txt"
        self.registry(f"echo ran >> {counter}")
        wdir = self.worker("r1")
        self.commit(wdir, "work")
        self.assertEqual(self.tool("run", "--task", "r1").returncode, 0)
        self.commit(self.base, "main moves", "main2.txt")
        self.git(self.base, "push", "-q", "origin", "main")
        self.git(self.base, "fetch", "-q", "origin")
        self.assertEqual(self.tool("run", "--task", "r1").returncode, 0)
        self.assertEqual(counter.read_text(encoding="utf-8").count("ran"), 2)

    @unittest.skipIf(fcntl is None, "no fcntl (Windows): presubmit.py has no run lock there")
    def test_a_waiting_run_reuses_the_pass_it_waited_for(self):
        counter = self.tmp / "runs.txt"
        self.registry(f"echo ran >> {counter}")
        wdir = self.worker("r2")
        head = self.commit(wdir, "work")
        lock = self.root / ".state" / "presubmit" / "run.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            proc = subprocess.Popen(
                [sys.executable, str(base.TOOL), "--root", str(self.root), "--db-path", str(self.db),
                 "run", "--task", "r2", "--wait", "60"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            self.assertIn("waiting", proc.stdout.readline())
            # Meanwhile the run that held the lock records a pass for the same head.
            main = self.git(wdir, "rev-parse", "origin/main")
            self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES "
                     "('2026-11-22T12:00:00.000Z', 'secretary', 'presubmit_checked', ?)",
                     (json.dumps({"task": "r2", "head": head, "base": main, "result": "passed",
                                  "config": f"echo ran >> {counter}", "log": "x"}),))
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("already", out)
        self.assertFalse(counter.exists(), "the waiting run ran the steps again")


    @unittest.skipIf(fcntl is None, "no fcntl (Windows): presubmit.py has no run lock there")
    def test_a_head_that_moved_during_the_wait_is_checked(self):
        counter = self.tmp / "runs.txt"
        self.registry(f"echo ran >> {counter}")
        wdir = self.worker("r3")
        old = self.commit(wdir, "work")
        lock = self.root / ".state" / "presubmit" / "run.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            proc = subprocess.Popen(
                [sys.executable, str(base.TOOL), "--root", str(self.root), "--db-path", str(self.db),
                 "run", "--task", "r3", "--wait", "60"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            self.assertIn("waiting", proc.stdout.readline())
            main = self.git(wdir, "rev-parse", "origin/main")
            self.sql("INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES "
                     "('2026-11-22T12:00:00.000Z', 'secretary', 'presubmit_checked', ?)",
                     (json.dumps({"task": "r3", "head": old, "base": main, "result": "passed",
                                  "config": f"echo ran >> {counter}", "log": "x"}),))
            new = self.commit(wdir, "more work")
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("already", out)
        self.assertEqual(counter.read_text(encoding="utf-8").count("ran"), 1)
        self.assertEqual(self.checked("r3")[-1]["head"], new)


for _name in [n for n in dir(base.PresubmitTests) if n.startswith("test_")]:
    if _name not in RerunTests.__dict__:
        setattr(RerunTests, _name, None)

if __name__ == "__main__":
    unittest.main()
