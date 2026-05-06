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
peer not found, backend unreachable) are swallowed — peer notification
is decoration on top of the canonical event row, never a precondition.

Failure handling notes:
* ``stdout.readline()`` would block indefinitely on a renga binary that
  starts but never replies. The reader runs in a background thread so
  the caller-supplied ``timeout`` is actually enforced.
* Renga currently returns the backend-unreachable case as ok-text
  ``"(message dropped — renga not reachable: <reason>)"`` rather than
  a JSON-RPC error (transitional shim per
  ``docs/contracts/backend-interface-contract.md`` §2.1 / Issue #242).
  This helper inspects the result text and rejects that shape so a
  silent backend failure isn't reported as confirmed delivery.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from typing import Optional

_RENGA_BIN = "renga"
_HANDSHAKE_TIMEOUT_SEC = 5.0
_DROPPED_PREFIX = "(message dropped"


def notify_peer(
    to_id: str,
    message: str,
    *,
    timeout: float = _HANDSHAKE_TIMEOUT_SEC,
    renga_bin: str = _RENGA_BIN,
) -> bool:
    """Send a peer message via ``renga mcp-peer``. Best-effort.

    Returns ``True`` only on confirmed (non-error, non-dropped) delivery
    from the MCP server. Returns ``False`` for every other outcome —
    RENGA_SOCKET unset, renga binary missing, subprocess crash, JSON-RPC
    error, read timeout, ``(message dropped — ...)`` backend-unreachable
    shim. Never raises.
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

    line_q: "queue.Queue[Optional[str]]" = queue.Queue()

    def reader() -> None:
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line_q.put(raw)
        except Exception:  # noqa: BLE001
            pass
        finally:
            line_q.put(None)  # EOF sentinel

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        return _drive_send(proc, line_q, to_id, message, timeout)
    except Exception:  # noqa: BLE001 — best-effort, swallow everything
        return False
    finally:
        _shutdown(proc, timeout)


def _read_response(
    line_q: "queue.Queue[Optional[str]]",
    target_id: int,
    timeout: float,
) -> Optional[dict]:
    """Drain lines until one matches ``id == target_id``, or timeout.

    Notifications and lines for other ids are skipped. Returns the
    parsed dict on match, ``None`` on timeout / EOF / parse error.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while True:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            return None
        try:
            raw = line_q.get(timeout=remaining)
        except queue.Empty:
            return None
        if raw is None:
            return None  # EOF
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("id") == target_id:
            return msg


def _drive_send(
    proc: subprocess.Popen,
    line_q: "queue.Queue[Optional[str]]",
    to_id: str,
    message: str,
    timeout: float,
) -> bool:
    def write(req: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()

    write({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "pr_watch", "version": "0.1"},
        },
    })
    init = _read_response(line_q, target_id=1, timeout=timeout)
    if init is None or "result" not in init:
        return False

    write({"jsonrpc": "2.0", "method": "notifications/initialized"})
    write({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "send_message",
            "arguments": {"to_id": to_id, "message": message},
        },
    })
    resp = _read_response(line_q, target_id=2, timeout=timeout)
    if resp is None:
        return False

    result = resp.get("result")
    if not isinstance(result, dict):
        return False
    if result.get("isError"):
        return False
    # Renga's backend-unreachable shim returns ok-text rather than a
    # JSON-RPC error (Issue #242). Reject "(message dropped — ..." so
    # a silent backend failure isn't reported as success.
    for chunk in result.get("content", []) or []:
        if isinstance(chunk, dict):
            text = chunk.get("text") or ""
            if isinstance(text, str) and text.lstrip().startswith(_DROPPED_PREFIX):
                return False
    return True


def _shutdown(proc: subprocess.Popen, timeout: float) -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
