"""Best-effort renga-peers message dispatch from CLI subprocesses.

Issue #326. ``mcp__renga-peers__send_message`` is only reachable from
inside a Claude Code session — a CLI helper like ``tools/pr_watch.py``
cannot call MCP tools directly. The renga binary, however, ships an
``mcp-peer`` subcommand that runs the same MCP server over stdio, so
spawning it as a subprocess and driving a one-shot JSON-RPC handshake
is the simplest reliable bridge from a Python CLI back into the
peer-message channel.

When ``RENGA_SOCKET`` is unset (plain shell, CI, etc.), this helper is a
silent no-op so the calling tool keeps working in non-renga
environments. All failures (binary missing, handshake error, timeout,
peer not found) are swallowed — peer notification is decoration on top
of the canonical event row, never a precondition.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

_RENGA_BIN = "renga"
_HANDSHAKE_TIMEOUT_SEC = 5.0


def notify_peer(
    to_id: str,
    message: str,
    *,
    timeout: float = _HANDSHAKE_TIMEOUT_SEC,
    renga_bin: str = _RENGA_BIN,
) -> bool:
    """Send a peer message via ``renga mcp-peer``. Best-effort.

    Returns ``True`` only on confirmed (non-error) delivery from the MCP
    server. Returns ``False`` for every other outcome — RENGA_SOCKET
    unset, renga binary missing, subprocess crash, JSON-RPC error,
    timeout. Never raises.
    """
    if not os.environ.get("RENGA_SOCKET"):
        return False
    if shutil.which(renga_bin) is None:
        return False
    try:
        proc = subprocess.Popen(
            [renga_bin, "mcp-peer"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=os.environ.copy(),
        )
    except (OSError, ValueError):
        return False
    try:
        return _drive_send(proc, to_id, message)
    except Exception:  # noqa: BLE001 — best-effort, swallow everything
        return False
    finally:
        _shutdown(proc, timeout)


def _drive_send(proc: subprocess.Popen, to_id: str, message: str) -> bool:
    def write(req: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()

    def read_line() -> Optional[str]:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        return line if line else None

    write({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pr_watch", "version": "0.1"},
        },
    })
    init = read_line()
    if init is None:
        return False
    try:
        json.loads(init)
    except json.JSONDecodeError:
        return False

    write({"jsonrpc": "2.0", "method": "notifications/initialized"})
    write({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "send_message",
            "arguments": {"to_id": to_id, "message": message},
        },
    })
    resp = read_line()
    if resp is None:
        return False
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        return False
    result = data.get("result")
    if not isinstance(result, dict):
        return False
    return result.get("isError") is False


def _shutdown(proc: subprocess.Popen, timeout: float) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
