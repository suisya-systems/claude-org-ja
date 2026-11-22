"""close_run keeps trusting a ``merged`` that its own record backs (Issue 1298).

The stale-``merged`` check (tests/test_second_pr_state.py) must not make
close_run ask GitHub when a ``pr_merged`` event names this very PR. Uses the
fixture of tests/test_close_run.py, loaded by path so the ``tests`` package
__init__ (which scrubs the transport env for the whole process) does not run.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
_spec = importlib.util.spec_from_file_location("_close_fixture", _ROOT / "tests" / "test_close_run.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


class MergedRecordTests(base.CloseRunTests):
    def test_merged_backed_by_its_own_record_needs_no_question(self):
        wdir = self.run_("own-record", pr_url="https://github.com/o/r/pull/3", pr_state="merged")
        self.ev("worker_spawned", "own-record", dir=str(wdir))
        self.ev("pr_merged", "own-record", pr=2, pr_url="https://github.com/o/r/pull/2")
        self.ev("pr_merged", "own-record", pr=3, pr_url="https://github.com/o/r/pull/3")
        self.ev("pane_closed", "own-record", pane_id="%3")
        fake = self.tmp / "bin"
        fake.mkdir(exist_ok=True)
        (fake / "gh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")  # asking would fail
        (fake / "gh").chmod(0o755)
        self.assertClosed(self.close("--task", "own-record", path_prefix=fake), "own-record", wdir)


for _name in [n for n in dir(base.CloseRunTests) if n.startswith("test_")]:
    if _name not in MergedRecordTests.__dict__:
        setattr(MergedRecordTests, _name, None)

if __name__ == "__main__":
    unittest.main()
