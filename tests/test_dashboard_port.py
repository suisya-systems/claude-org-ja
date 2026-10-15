"""Tests for the dashboard port record and ``server.py --status`` (Issue #1135).

Each case runs real servers from throwaway "clones" (a temp dir whose
dashboard/server.py is a symlink to the real one), so BASE_DIR / .state differ
per org exactly as two checkouts on one host would.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "dashboard" / "server.py"
sys.path.insert(0, str(REPO))

from dashboard import server  # noqa: E402
from dashboard.server import PORTS  # noqa: E402


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("localhost", port))
        except OSError:
            return False
    return True


@unittest.skipIf(os.name == "nt", "uses symlinks and SIGTERM")
@unittest.skipUnless(all(_port_free(p) for p in PORTS), "dashboard ports in use on this host")
class DashboardPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.procs: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
        self._td.cleanup()

    def _clone(self, name: str) -> Path:
        root = self.tmp / name
        (root / "dashboard").mkdir(parents=True)
        (root / "dashboard" / "server.py").symlink_to(SERVER)
        return root

    def _start(self, root: Path) -> subprocess.Popen:
        p = subprocess.Popen(
            [sys.executable, str(root / "dashboard" / "server.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.procs.append(p)
        return p

    def _status(self, root: Path, wait: float = 0) -> tuple[int, str]:
        r = subprocess.run(
            [sys.executable, str(root / "dashboard" / "server.py"),
             "--status", "--wait", str(wait)],
            capture_output=True, text=True, timeout=wait + 30,
        )
        return r.returncode, (r.stdout + r.stderr).strip()

    def _stop(self, p: subprocess.Popen) -> None:
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)

    def test_two_orgs_stop_reuse_and_all_busy(self) -> None:
        a, b, c, d = (self._clone(n) for n in "abcd")
        self.assertEqual(self._status(a)[0], 1)

        pa = self._start(a)
        code, url_a = self._status(a, wait=15)
        self.assertEqual((code, url_a), (0, f"http://localhost:{PORTS[0]}"))

        pb = self._start(b)
        code, url_b = self._status(b, wait=15)
        self.assertEqual((code, url_b), (0, f"http://localhost:{PORTS[1]}"))
        rec = json.loads((b / ".state" / "dashboard.json").read_text())
        self.assertEqual(rec["pid"], pb.pid)
        self.assertEqual(rec["org_root"], str(b.resolve()))

        # all candidate ports busy: no record, failure exit, status says stopped
        self._start(c)
        self._status(c, wait=15)
        pd = self._start(d)
        self.assertEqual(pd.wait(timeout=15), 1)
        self.assertFalse((d / ".state" / "dashboard.json").exists())
        self.assertFalse((d / ".state" / "dashboard.pid").exists())
        self.assertEqual(self._status(d)[0], 1)

        # SIGTERM cleans the record; the freed port is reused by the next start
        self._stop(pa)
        self.assertFalse((a / ".state" / "dashboard.json").exists())
        self.assertFalse((a / ".state" / "dashboard.pid").exists())
        self.assertEqual(self._status(a)[0], 1)
        self._start(d)
        self.assertEqual(self._status(d, wait=15), (0, f"http://localhost:{PORTS[0]}"))
        self._stop(pb)

    def test_record_pointing_at_another_org_is_rejected(self) -> None:
        a, b = self._clone("a"), self._clone("b")
        self._start(a)
        self.assertEqual(self._status(a, wait=15)[0], 0)
        # b's stale record names a live pid but a's port: must not report a's URL
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.procs.append(sleeper)
        (b / ".state").mkdir()
        (b / ".state" / "dashboard.json").write_text(
            json.dumps({"pid": sleeper.pid, "port": PORTS[0]})
        )
        code, msg = self._status(b)
        self.assertEqual(code, 2, msg)
        (b / ".state" / "dashboard.json").write_text("{broken")
        self.assertEqual(self._status(b)[0], 2)

    def test_legacy_server_without_record(self) -> None:
        a = self._clone("a")
        legacy = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", "dashboard/server.py"]
        )
        self.procs.append(legacy)
        (a / ".state").mkdir()
        (a / ".state" / "dashboard.pid").write_text(str(legacy.pid))
        code, msg = self._status(a)
        self.assertEqual(code, 3, msg)
        self.assertIn("restart", msg)


class WindowsStatusTests(unittest.TestCase):
    def test_never_probes_pid_on_windows(self) -> None:
        # os.kill(pid, 0) terminates the process on Windows.
        with TemporaryDirectory() as td:
            rec = Path(td) / "dashboard.json"
            with socket.socket() as s:
                s.bind(("localhost", 0))
                port = s.getsockname()[1]  # closed again: nothing answers
            rec.write_text(json.dumps({"pid": os.getpid(), "port": port}))
            with mock.patch.object(server, "RECORD_FILE", rec), \
                    mock.patch.object(server, "pid_alive", side_effect=AssertionError), \
                    mock.patch.object(server.os, "name", "nt"):
                code, _ = server.check_status()
        self.assertEqual(code, 1)

    def test_legacy_pid_file_on_windows_is_not_stopped(self) -> None:
        with TemporaryDirectory() as td:
            pf = Path(td) / "dashboard.pid"
            pf.write_text("12345")
            with mock.patch.object(server, "RECORD_FILE", Path(td) / "dashboard.json"), \
                    mock.patch.object(server, "PID_FILE", pf), \
                    mock.patch.object(server, "pid_alive", side_effect=AssertionError), \
                    mock.patch.object(server.os, "name", "nt"):
                code, _ = server.check_status()
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
