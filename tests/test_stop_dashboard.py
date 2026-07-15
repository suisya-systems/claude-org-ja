"""Tests for tools/stop_dashboard.py identity-checked dashboard stop.

Lock the mis-kill-prevention invariants for the stale-pid-safe dashboard stop
used by /org-down: kill ONLY when the live pid's argv still names
dashboard/server.py; on a dead / recycled / non-matching pid, remove the stale
pid file WITHOUT killing.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import stop_dashboard as sd  # noqa: E402


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


class StopDashboardTests(unittest.TestCase):
    def test_absent_pidfile_is_noop(self) -> None:
        with TemporaryDirectory() as td:
            self.assertEqual(sd.run_stop(Path(td) / "dashboard.pid"), 0)

    def test_non_int_pidfile_stale_deleted(self) -> None:
        with TemporaryDirectory() as td:
            pf = Path(td) / "dashboard.pid"
            pf.write_text("not-a-pid", encoding="utf-8")
            self.assertEqual(sd.run_stop(pf), 0)
            self.assertFalse(pf.exists())

    def test_dead_pid_stale_deleted_not_killed(self) -> None:
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        if sd.pid_alive(dead.pid):
            self.skipTest("pid recycled before assertion")
        with TemporaryDirectory() as td:
            pf = Path(td) / "dashboard.pid"
            pf.write_text(str(dead.pid), encoding="utf-8")
            self.assertEqual(sd.run_stop(pf), 0)
            self.assertFalse(pf.exists())

    def test_identity_mismatch_never_kills_live_process(self) -> None:
        # A live non-dashboard process (recycled pid) must survive; only the
        # stale pid file is removed.
        sleeper = _spawn_sleeper()
        try:
            with TemporaryDirectory() as td:
                pf = Path(td) / "dashboard.pid"
                pf.write_text(str(sleeper.pid), encoding="utf-8")
                self.assertEqual(sd.run_stop(pf), 0)
                self.assertFalse(pf.exists())
                time.sleep(0.2)
                self.assertIsNone(
                    sleeper.poll(), "a non-dashboard process must NOT be killed"
                )
        finally:
            sleeper.terminate()
            sleeper.wait()

    def test_verified_identity_is_stopped(self) -> None:
        # A live process whose argv is made to look like dashboard/server.py is
        # SIGTERM'd and its pid file cleared.
        sleeper = _spawn_sleeper()
        try:
            with TemporaryDirectory() as td:
                pf = Path(td) / "dashboard.pid"
                pf.write_text(str(sleeper.pid), encoding="utf-8")
                with mock.patch.object(
                    sd, "live_cmdline", return_value=["python", "dashboard/server.py"]
                ):
                    self.assertEqual(sd.run_stop(pf), 0)
                self.assertEqual(sleeper.wait(timeout=5), -signal.SIGTERM)
                self.assertFalse(pf.exists())
        finally:
            if sleeper.poll() is None:
                sleeper.kill()
                sleeper.wait()

    def test_unreadable_identity_keeps_pidfile(self) -> None:
        # Simulate the Windows-native path (no /proc, no ps): argv unresolvable →
        # exit 2, no kill, pid file preserved for the PowerShell fallback.
        sleeper = _spawn_sleeper()
        try:
            with TemporaryDirectory() as td:
                pf = Path(td) / "dashboard.pid"
                pf.write_text(str(sleeper.pid), encoding="utf-8")
                with mock.patch.object(sd, "live_cmdline", return_value=None):
                    self.assertEqual(sd.run_stop(pf), 2)
                self.assertTrue(pf.exists(), "pid file must be kept when identity is unconfirmed")
                self.assertIsNone(sleeper.poll())
        finally:
            sleeper.terminate()
            sleeper.wait()


if __name__ == "__main__":
    unittest.main()
