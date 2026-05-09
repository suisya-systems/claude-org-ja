"""pytest fixtures shared across tools/ test modules.

Issue #398 follow-up: several tool tests exercise `pr_watch.main()` end-to-end,
which calls `_notify_peer` -> `tools.peer_notify.notify_peer` -> spawns
`renga mcp-peer` when ``RENGA_SOCKET`` is set. Inside a live Claude Code /
renga session that environment variable is set, so test runs with a real
`renga` binary on PATH leak fake CI / merge messages onto the production
peer channel. The existing tests didn't notice this because CI runners
have neither the env var nor the binary.

This autouse fixture scrubs ``RENGA_SOCKET`` for the duration of every
test in ``tools/``. Tests that explicitly want to test peer-emit behaviour
should mock ``tools.peer_notify.notify_peer`` directly instead.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _scrub_renga_socket(monkeypatch):
    """Disable renga peer-emit fall-through for the test's duration.

    `peer_notify.notify_peer` returns False immediately when
    ``RENGA_SOCKET`` is unset, which short-circuits the whole subprocess
    handshake. That is exactly the behaviour we want for hermetic tests
    that aren't asserting on the peer channel.
    """
    monkeypatch.delenv("RENGA_SOCKET", raising=False)
    yield
