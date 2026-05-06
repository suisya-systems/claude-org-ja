"""Direct unit tests for tools/peer_notify.py (Issue #326).

These exercise the JSON-RPC handshake, timeout, and the ok-text
``(message dropped — …)`` rejection without invoking the real renga
binary. The helper spawns ``renga mcp-peer`` with subprocess.Popen,
so we substitute a fake Popen that wires its stdin/stdout to in-memory
streams scripted by each test.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Callable, List, Optional
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import peer_notify  # noqa: E402


class _FakeProc:
    """Minimal subprocess.Popen stand-in driven by a server callback."""

    def __init__(self, server: Callable[[dict], "Optional[dict | List[dict]]"],
                 stall: bool = False) -> None:
        self._server = server
        self._stall = stall
        self._in_r, self._in_w = os.pipe()
        self._out_r, self._out_w = os.pipe()
        self.stdin = os.fdopen(self._in_w, "w", encoding="utf-8")
        self.stdout = os.fdopen(self._out_r, "r", encoding="utf-8")
        self.stderr = io.StringIO()
        self._reader_in = os.fdopen(self._in_r, "r", encoding="utf-8")
        self._writer_out = os.fdopen(self._out_w, "w", encoding="utf-8")
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()
        self.returncode: Optional[int] = None

    def _serve(self) -> None:
        try:
            for line in self._reader_in:
                if not line.strip():
                    continue
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if self._stall:
                    # Simulate an unresponsive server: read but never reply.
                    continue
                resp = self._server(req)
                if resp is None:
                    continue
                payloads = resp if isinstance(resp, list) else [resp]
                for p in payloads:
                    self._writer_out.write(json.dumps(p) + "\n")
                    self._writer_out.flush()
        except Exception:
            pass
        finally:
            try:
                self._writer_out.close()
            except Exception:
                pass

    def wait(self, timeout: Optional[float] = None) -> int:
        self._t.join(timeout=timeout)
        self.returncode = 0
        return 0

    def kill(self) -> None:
        try:
            self._writer_out.close()
        except Exception:
            pass
        try:
            self._reader_in.close()
        except Exception:
            pass


def _ok_init(req: dict) -> dict:
    return {
        "jsonrpc": "2.0", "id": req["id"],
        "result": {"protocolVersion": "2024-11-05",
                   "capabilities": {}, "serverInfo": {"name": "fake"}},
    }


class NotifyPeerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pretend renga is on PATH and RENGA_SOCKET is set so the
        # short-circuit guards don't fire.
        self._env = mock.patch.dict(
            os.environ, {"RENGA_SOCKET": r"\\.\pipe\fake"}, clear=False,
        )
        self._env.start()
        self._which = mock.patch.object(
            peer_notify.shutil, "which", return_value="/fake/renga",
        )
        self._which.start()

    def tearDown(self) -> None:
        self._which.stop()
        self._env.stop()

    def _patch_popen(self, server) -> mock._patch:
        def _popen(*_a, **_kw):
            return _FakeProc(server)
        return mock.patch.object(peer_notify.subprocess, "Popen",
                                 side_effect=_popen)

    def test_no_socket_returns_false(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "RENGA_SOCKET"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(peer_notify.notify_peer("secretary", "x"))

    def test_renga_missing_returns_false(self) -> None:
        with mock.patch.object(peer_notify.shutil, "which", return_value=None):
            self.assertFalse(peer_notify.notify_peer("secretary", "x"))

    def test_successful_delivery(self) -> None:
        def server(req: dict) -> Optional[dict]:
            if req.get("method") == "initialize":
                return _ok_init(req)
            if req.get("method") == "tools/call":
                return {
                    "jsonrpc": "2.0", "id": req["id"],
                    "result": {"isError": False,
                               "content": [{"type": "text",
                                            "text": "Delivered to 1."}]},
                }
            return None

        with self._patch_popen(server):
            self.assertTrue(peer_notify.notify_peer("secretary", "hi"))

    def test_message_dropped_ok_text_is_rejected(self) -> None:
        """Renga's backend-unreachable shim returns ok-text rather
        than a JSON-RPC error. notify_peer must NOT report success."""
        def server(req: dict) -> Optional[dict]:
            if req.get("method") == "initialize":
                return _ok_init(req)
            if req.get("method") == "tools/call":
                return {
                    "jsonrpc": "2.0", "id": req["id"],
                    "result": {"isError": False,
                               "content": [{"type": "text",
                                            "text": "(message dropped — renga not reachable: foo)"}]},
                }
            return None

        with self._patch_popen(server):
            self.assertFalse(peer_notify.notify_peer("secretary", "hi"))

    def test_is_error_true_returns_false(self) -> None:
        def server(req: dict) -> Optional[dict]:
            if req.get("method") == "initialize":
                return _ok_init(req)
            if req.get("method") == "tools/call":
                return {
                    "jsonrpc": "2.0", "id": req["id"],
                    "result": {"isError": True,
                               "content": [{"type": "text", "text": "boom"}]},
                }
            return None

        with self._patch_popen(server):
            self.assertFalse(peer_notify.notify_peer("secretary", "hi"))

    def test_unrelated_lines_are_skipped(self) -> None:
        """Notifications and unrelated id lines must not fool the reader."""
        def server(req: dict) -> Optional[list]:
            if req.get("method") == "initialize":
                return [
                    {"jsonrpc": "2.0", "method": "notifications/log",
                     "params": {"msg": "noise"}},
                    {"jsonrpc": "2.0", "id": 999,
                     "result": {"unrelated": True}},
                    _ok_init(req),
                ]
            if req.get("method") == "tools/call":
                return [
                    {"jsonrpc": "2.0", "method": "notifications/progress"},
                    {"jsonrpc": "2.0", "id": req["id"],
                     "result": {"isError": False,
                                "content": [{"type": "text",
                                             "text": "Delivered to 1."}]}},
                ]
            return None

        with self._patch_popen(server):
            self.assertTrue(peer_notify.notify_peer("secretary", "hi"))

    def test_unresponsive_server_times_out(self) -> None:
        """An unresponsive `renga mcp-peer` must not hang the caller."""
        def _popen(*_a, **_kw):
            return _FakeProc(server=lambda r: None, stall=True)

        with mock.patch.object(peer_notify.subprocess, "Popen",
                               side_effect=_popen):
            t0 = time.monotonic()
            ok = peer_notify.notify_peer("secretary", "hi", timeout=0.3)
            elapsed = time.monotonic() - t0
        self.assertFalse(ok)
        self.assertLess(elapsed, 5.0,
                        f"timeout not enforced; elapsed={elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
