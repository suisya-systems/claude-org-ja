"""Tests for tools/secretary_queue_watcher.py PID-file + --stop stop path.

These lock the mis-kill-prevention invariants of the identity-checked stop
(design constraints #1/#2 of the org-down-suspend-teardown brief):

* the PID file records pid/cwd/cmdline/started_at/broker_state_dir and round-trips;
* ``run_stop`` on an absent / corrupt / bad-pid sidecar is a safe no-op or a
  stale delete (never a kill);
* ownership mismatch (a different broker's ``ORG_BROKER_STATE_DIR``) never kills —
  a live unrelated process keeps running and only the stale sidecar is removed;
* identity mismatch (pid recycled to a non-watcher process) never kills;
* a genuinely-matching live watcher IS stopped and its sidecar removed.

Identity checks read ``/proc/<pid>/cmdline`` so the kill-path assertions are
Linux-only; ownership-short-circuit and file-handling tests run everywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import secretary_queue_watcher as sqw  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "secretary_queue_watcher.py"

_LINUX_PROC = sys.platform.startswith("linux") and Path("/proc").is_dir()


def _args(owner: str = "secretary") -> argparse.Namespace:
    return argparse.Namespace(owner=owner, stale_sec=120, poll_sec=30)


def _spawn_sleeper() -> subprocess.Popen:
    """A live process whose argv does NOT contain the watcher script name."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )


def _spawn_watcher(state_dir: Path, pid_file: Path) -> subprocess.Popen:
    """Launch the real watcher (argv contains the script name → identity matches)."""
    env = os.environ.copy()
    env["ORG_BROKER_STATE_DIR"] = str(state_dir)
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), "--pid-file", str(pid_file), "--poll-sec", "0.2"],
        env=env,
        cwd=str(REPO_ROOT),
    )


def _await_pidfile(pid_file: Path, tries: int = 50) -> bool:
    for _ in range(tries):
        if pid_file.exists():
            return True
        time.sleep(0.1)
    return pid_file.exists()


class PidFileRoundTripTests(unittest.TestCase):
    def test_write_records_identity_fields(self) -> None:
        with TemporaryDirectory() as td:
            pid_file = Path(td) / ".state" / "secretary_queue_watcher.json"
            sqw.write_pid_file(pid_file, state_dir=str(Path(td) / "broker"), args=_args())
            rec = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual(rec["pid"], os.getpid())
            self.assertEqual(rec["owner"], "secretary")
            # broker_state_dir is stored resolved/absolute (constraint #2)
            self.assertEqual(rec["broker_state_dir"], sqw._real(Path(td) / "broker"))
            for key in ("cwd", "cmdline", "started_at", "broker_state_dir"):
                self.assertIn(key, rec)

    def test_remove_only_when_pid_matches(self) -> None:
        with TemporaryDirectory() as td:
            pid_file = Path(td) / "qw.json"
            sqw.write_pid_file(pid_file, state_dir=td, args=_args())
            # A newer watcher overwrote the sidecar with a different pid:
            # our finally must NOT delete it.
            other = json.loads(pid_file.read_text(encoding="utf-8"))
            other["pid"] = os.getpid() + 1
            pid_file.write_text(json.dumps(other), encoding="utf-8")
            sqw.remove_pid_file(pid_file, expected_pid=os.getpid())
            self.assertTrue(pid_file.exists(), "sidecar of a different pid must survive")
            # Matching pid removes it.
            sqw.remove_pid_file(pid_file, expected_pid=os.getpid() + 1)
            self.assertFalse(pid_file.exists())


class OwnershipTests(unittest.TestCase):
    def test_broker_state_dir_match(self) -> None:
        rec = {"broker_state_dir": "/a/b/broker", "cwd": "/repo"}
        ok, reason = sqw._ownership_ok(rec, "/a/b/broker")
        self.assertTrue(ok)
        self.assertEqual(reason, "broker_state_dir_match")

    def test_broker_state_dir_mismatch(self) -> None:
        rec = {"broker_state_dir": "/a/b/broker", "cwd": "/repo"}
        ok, reason = sqw._ownership_ok(rec, "/other/broker")
        self.assertFalse(ok)
        self.assertEqual(reason, "broker_state_dir_mismatch")

    def test_env_unset_falls_back_to_cwd(self) -> None:
        rec = {"broker_state_dir": "/a/b/broker", "cwd": sqw._real(Path.cwd())}
        ok, reason = sqw._ownership_ok(rec, None)
        self.assertTrue(ok)
        self.assertEqual(reason, "cwd_match_env_unset")

    def test_env_unset_cwd_mismatch(self) -> None:
        rec = {"broker_state_dir": "/a/b/broker", "cwd": "/somewhere/else/entirely"}
        ok, _ = sqw._ownership_ok(rec, None)
        self.assertFalse(ok)


class LiveCmdlineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ps"), "ps not available")
    def test_ps_fallback_used_when_proc_missing(self) -> None:
        # Force the /proc fast path to miss so _live_cmdline falls back to `ps`
        # (the macOS / BSD path). On Linux CI `ps` is present, so this exercises
        # the fallback cross-platform.
        with mock.patch.object(sqw, "_proc_cmdline", return_value=None):
            cmd = sqw._live_cmdline(os.getpid())
        self.assertIsNotNone(cmd, "ps fallback should resolve the running argv")
        self.assertTrue(
            any("python" in part.lower() for part in cmd),
            f"ps output should name the interpreter: {cmd}",
        )

    def test_ps_fallback_none_for_dead_pid(self) -> None:
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        if sqw._pid_alive(dead.pid):
            self.skipTest("pid was recycled before assertion")
        with mock.patch.object(sqw, "_proc_cmdline", return_value=None):
            self.assertIsNone(sqw._live_cmdline(dead.pid))


class RunStopSafeCasesTests(unittest.TestCase):
    def test_absent_pidfile_is_noop(self) -> None:
        with TemporaryDirectory() as td:
            pid_file = Path(td) / "qw.json"
            self.assertEqual(sqw.run_stop(pid_file), 0)

    def test_corrupt_pidfile_stale_deleted(self) -> None:
        with TemporaryDirectory() as td:
            pid_file = Path(td) / "qw.json"
            pid_file.write_text("{ not json", encoding="utf-8")
            self.assertEqual(sqw.run_stop(pid_file), 0)
            self.assertFalse(pid_file.exists())

    def test_bad_pid_field_stale_deleted(self) -> None:
        with TemporaryDirectory() as td:
            pid_file = Path(td) / "qw.json"
            pid_file.write_text(json.dumps({"pid": "not-an-int"}), encoding="utf-8")
            self.assertEqual(sqw.run_stop(pid_file), 0)
            self.assertFalse(pid_file.exists())

    def test_dead_pid_stale_deleted_not_killed(self) -> None:
        # A reaped child's pid is dead; stop must stale-delete, never kill.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        if sqw._pid_alive(dead.pid):
            self.skipTest("pid was recycled before assertion")
        with TemporaryDirectory() as td:
            pid_file = Path(td) / "qw.json"
            pid_file.write_text(
                json.dumps({"pid": dead.pid, "broker_state_dir": td, "cwd": sqw._real(Path.cwd())}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"ORG_BROKER_STATE_DIR": td}):
                self.assertEqual(sqw.run_stop(pid_file), 0)
            self.assertFalse(pid_file.exists())

    def test_ownership_mismatch_never_kills_live_process(self) -> None:
        # Ownership check short-circuits BEFORE any kill: a live process owned by
        # a different broker survives, only the stale sidecar is removed.
        sleeper = _spawn_sleeper()
        try:
            with TemporaryDirectory() as td:
                pid_file = Path(td) / "qw.json"
                pid_file.write_text(
                    json.dumps({"pid": sleeper.pid, "broker_state_dir": "/some/OTHER/broker"}),
                    encoding="utf-8",
                )
                with mock.patch.dict(os.environ, {"ORG_BROKER_STATE_DIR": td}):
                    self.assertEqual(sqw.run_stop(pid_file), 0)
                self.assertFalse(pid_file.exists())
                time.sleep(0.2)
                self.assertIsNone(sleeper.poll(), "unrelated process must NOT be killed")
        finally:
            sleeper.terminate()
            sleeper.wait()


@unittest.skipUnless(_LINUX_PROC, "identity check needs /proc (Linux)")
class RunStopIdentityTests(unittest.TestCase):
    def test_identity_mismatch_never_kills_recycled_pid(self) -> None:
        # Ownership OK but the pid now points at a non-watcher process (recycle):
        # identity mismatch → stale delete, no kill.
        sleeper = _spawn_sleeper()
        try:
            with TemporaryDirectory() as td:
                pid_file = Path(td) / "qw.json"
                pid_file.write_text(
                    json.dumps({"pid": sleeper.pid, "broker_state_dir": sqw._real(td)}),
                    encoding="utf-8",
                )
                with mock.patch.dict(os.environ, {"ORG_BROKER_STATE_DIR": sqw._real(td)}):
                    self.assertEqual(sqw.run_stop(pid_file), 0)
                self.assertFalse(pid_file.exists())
                time.sleep(0.2)
                self.assertIsNone(sleeper.poll(), "recycled unrelated pid must NOT be killed")
        finally:
            sleeper.terminate()
            sleeper.wait()

    def test_ownership_mismatch_spares_real_watcher(self) -> None:
        # Constraint #2, exercised properly: a REAL watcher (identity WOULD match
        # via /proc) belonging to a DIFFERENT broker must survive --stop, because
        # the ownership (broker_state_dir) check short-circuits the kill BEFORE the
        # identity check. Unlike test_ownership_mismatch_never_kills_live_process,
        # here identity would pass — so a regression that ignored the ownership
        # result while keeping identity+kill WOULD kill this process and fail here.
        with TemporaryDirectory() as td:
            own_dir = Path(td) / "brokerX"  # the watcher's OWN broker state dir
            own_dir.mkdir()
            pid_file = Path(td) / "qw.json"
            proc = _spawn_watcher(own_dir, pid_file)
            try:
                self.assertTrue(_await_pidfile(pid_file), "watcher never wrote its PID file")
                other_dir = Path(td) / "brokerY"  # a DIFFERENT broker runs --stop
                with mock.patch.dict(os.environ, {"ORG_BROKER_STATE_DIR": str(other_dir)}):
                    self.assertEqual(sqw.run_stop(pid_file), 0)
                time.sleep(0.3)
                self.assertIsNone(
                    proc.poll(), "a different broker's live watcher must NOT be killed"
                )
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()

    def test_stops_real_watcher_and_clears_sidecar(self) -> None:
        with TemporaryDirectory() as td:
            state_dir = Path(td) / "broker"
            state_dir.mkdir()
            pid_file = Path(td) / "qw.json"
            env = os.environ.copy()
            env["ORG_BROKER_STATE_DIR"] = str(state_dir)
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), "--pid-file", str(pid_file), "--poll-sec", "0.2"],
                env=env,
                cwd=str(REPO_ROOT),
            )
            try:
                # Wait for the watcher to write its sidecar.
                for _ in range(50):
                    if pid_file.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(pid_file.exists(), "watcher never wrote its PID file")
                rec = json.loads(pid_file.read_text(encoding="utf-8"))
                self.assertEqual(rec["pid"], proc.pid)

                with mock.patch.dict(os.environ, {"ORG_BROKER_STATE_DIR": str(state_dir)}):
                    self.assertEqual(sqw.run_stop(pid_file), 0)

                self.assertEqual(proc.wait(timeout=5), 0)
                self.assertFalse(pid_file.exists(), "sidecar must be cleared after stop")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


if __name__ == "__main__":
    unittest.main()
