"""Direct unit tests for tools/peer_notify.py (Issue #326 / #590).

``NotifyPeerTests`` exercise the renga JSON-RPC handshake, timeout, and
the ok-text ``(message dropped — …)`` rejection without invoking the
real renga binary. The helper spawns ``renga mcp-peer`` with
subprocess.Popen, so we substitute a fake Popen that wires its
stdin/stdout to in-memory streams scripted by each test.

``NotifyPeerBrokerTests`` cover the ``ORG_TRANSPORT=broker`` branch,
which shells out to the frozen ``claude-org-runtime broker send`` CLI.
These stub ``subprocess.run`` so they verify the argv, the
``returncode == 0`` success mapping, and the best-effort ``False`` for
non-zero exit / CLI-absent (FileNotFoundError) / timeout — all without a
live broker daemon or an installed runtime.
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
        "result": {"protocolVersion": "2025-03-26",
                   "capabilities": {}, "serverInfo": {"name": "fake"}},
    }


class NotifyPeerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pretend renga is on PATH and RENGA_SOCKET is set so the
        # short-circuit guards don't fire. Pin ORG_TRANSPORT=renga so a
        # real broker session env (ORG_TRANSPORT=broker) can't misroute
        # these renga-path tests through the broker branch.
        self._env = mock.patch.dict(
            os.environ,
            {"RENGA_SOCKET": r"\\.\pipe\fake", "ORG_TRANSPORT": "renga"},
            clear=False,
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

    def test_initialize_request_uses_modern_protocol_version(self) -> None:
        """Regression guard against drifting back to 2024-11-05.

        Should match the version sent by tools/check_renga_compat.py so a
        future renga that tightens version negotiation doesn't silently
        downgrade peer_notify to a no-op.
        """
        seen: list[dict] = []

        def server(req: dict) -> Optional[dict]:
            seen.append(req)
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

        init_calls = [r for r in seen if r.get("method") == "initialize"]
        self.assertEqual(len(init_calls), 1)
        self.assertEqual(
            init_calls[0]["params"]["protocolVersion"], "2025-03-26",
        )

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


class _FakeCompleted:
    """Minimal subprocess.CompletedProcess stand-in."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


class NotifyPeerBrokerTests(unittest.TestCase):
    """ORG_TRANSPORT=broker branch — shells out to the runtime CLI."""

    def setUp(self) -> None:
        # Pin ORG_TRANSPORT=broker and drop any ORG_BROKER_STATE_DIR
        # leaking in from a real broker session env so the frozen-argv
        # tests exercise the default (no --state-dir) command shape.
        env = {k: v for k, v in os.environ.items()
               if k != "ORG_BROKER_STATE_DIR"}
        env["ORG_TRANSPORT"] = "broker"
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def _patch_run(self, **kwargs):
        return mock.patch.object(peer_notify.subprocess, "run", **kwargs)

    def test_returncode_zero_is_delivered(self) -> None:
        with self._patch_run(return_value=_FakeCompleted(0)) as run:
            self.assertTrue(peer_notify.notify_peer("secretary", "hi"))
        run.assert_called_once()

    def test_nonzero_returncode_is_false(self) -> None:
        with self._patch_run(return_value=_FakeCompleted(1)):
            self.assertFalse(peer_notify.notify_peer("secretary", "hi"))

    def test_cli_absent_filenotfound_is_false(self) -> None:
        """Until runtime#93 ships the CLI is not on PATH; the subprocess
        raises FileNotFoundError, which must map to a graceful no-op."""
        with self._patch_run(side_effect=FileNotFoundError("no runtime")):
            self.assertFalse(peer_notify.notify_peer("secretary", "hi"))

    def test_timeout_is_false(self) -> None:
        exc = peer_notify.subprocess.TimeoutExpired(cmd="x", timeout=0.1)
        with self._patch_run(side_effect=exc):
            self.assertFalse(peer_notify.notify_peer("secretary", "hi"))

    def test_generic_exception_is_false_not_raised(self) -> None:
        with self._patch_run(side_effect=RuntimeError("boom")):
            self.assertFalse(peer_notify.notify_peer("secretary", "hi"))

    def test_argv_matches_frozen_contract(self) -> None:
        """`claude-org-runtime broker send --to <id> --message <text>`."""
        with self._patch_run(return_value=_FakeCompleted(0)) as run:
            peer_notify.notify_peer("secretary", "a message")
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            ["claude-org-runtime", "broker", "send",
             "--to", "secretary", "--message", "a message"],
        )

    def test_state_dir_env_appends_flag(self) -> None:
        """ORG_BROKER_STATE_DIR set + non-empty → `--state-dir <value>`
        is appended so the CLI reaches a daemon on a non-default state
        dir (paired contract, claude-org-runtime #122)."""
        with mock.patch.dict(
            os.environ,
            {"ORG_BROKER_STATE_DIR": "/abs/.state/broker-herdr-dogfood-33"},
            clear=False,
        ), self._patch_run(return_value=_FakeCompleted(0)) as run:
            self.assertTrue(peer_notify.notify_peer("secretary", "hi"))
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            ["claude-org-runtime", "broker", "send",
             "--to", "secretary", "--message", "hi",
             "--state-dir", "/abs/.state/broker-herdr-dogfood-33"],
        )

    def test_state_dir_env_unset_keeps_legacy_argv(self) -> None:
        """No ORG_BROKER_STATE_DIR → historical argv, no --state-dir
        (backward compatible with runtimes that predate the flag)."""
        with self._patch_run(return_value=_FakeCompleted(0)) as run:
            self.assertTrue(peer_notify.notify_peer("secretary", "hi"))
        argv = run.call_args.args[0]
        self.assertNotIn("--state-dir", argv)

    def test_state_dir_env_empty_keeps_legacy_argv(self) -> None:
        """Empty ORG_BROKER_STATE_DIR is treated as unset."""
        with mock.patch.dict(
            os.environ, {"ORG_BROKER_STATE_DIR": ""}, clear=False,
        ), self._patch_run(return_value=_FakeCompleted(0)) as run:
            self.assertTrue(peer_notify.notify_peer("secretary", "hi"))
        argv = run.call_args.args[0]
        self.assertNotIn("--state-dir", argv)

    def test_broker_branch_does_not_spawn_renga(self) -> None:
        """ORG_TRANSPORT=broker must never reach the renga Popen path,
        even if RENGA_SOCKET happens to be set in the environment."""
        with mock.patch.dict(os.environ, {"RENGA_SOCKET": "x"}, clear=False), \
                mock.patch.object(peer_notify.subprocess, "Popen") as popen, \
                self._patch_run(return_value=_FakeCompleted(0)):
            self.assertTrue(peer_notify.notify_peer("secretary", "hi"))
        popen.assert_not_called()


class NotifyPeerDispatchTests(unittest.TestCase):
    """ORG_TRANSPORT routing: only ``broker`` takes the broker branch."""

    def _assert_renga_path(self, transport_env: dict) -> None:
        """With a non-broker transport the broker CLI is never called and
        the renga branch runs (and no-ops False when RENGA_SOCKET unset)."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("RENGA_SOCKET", "ORG_TRANSPORT")}
        env.update(transport_env)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(peer_notify.subprocess, "run") as run:
            self.assertFalse(peer_notify.notify_peer("secretary", "hi"))
        run.assert_not_called()

    def test_unset_transport_uses_renga(self) -> None:
        self._assert_renga_path({})

    def test_renga_transport_uses_renga(self) -> None:
        self._assert_renga_path({"ORG_TRANSPORT": "renga"})

    def test_unknown_transport_falls_back_to_renga(self) -> None:
        self._assert_renga_path({"ORG_TRANSPORT": "something-else"})


if __name__ == "__main__":
    unittest.main()
