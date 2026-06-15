"""pytest fixtures shared across tools/ test modules.

Issue #398 follow-up: several tool tests exercise `pr_watch.main()` end-to-end,
which calls `_notify_peer` -> `tools.peer_notify.notify_peer` -> spawns
`renga mcp-peer` when ``RENGA_SOCKET`` is set. Inside a live Claude Code /
renga session that environment variable is set, so test runs with a real
`renga` binary on PATH leak fake CI / merge messages onto the production
peer channel. The existing tests didn't notice this because CI runners
have neither the env var nor the binary.

Issue #590 makes ``notify_peer`` transport-neutral: with
``ORG_TRANSPORT=broker`` it instead shells out to
``claude-org-runtime broker send``. A test run inside a live broker
session would leak the same fake messages onto the broker channel, so we
scrub ``ORG_TRANSPORT`` too — forcing the renga branch, which then
no-ops immediately because ``RENGA_SOCKET`` is also scrubbed.

This autouse fixture scrubs ``RENGA_SOCKET`` and ``ORG_TRANSPORT`` for
the duration of every test in ``tools/``. Tests that explicitly want to
test peer-emit behaviour should mock ``tools.peer_notify.notify_peer``
(or set the env vars themselves) instead.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _scrub_renga_socket(monkeypatch):
    """Disable peer-emit fall-through (both transports) for the test.

    `peer_notify.notify_peer` returns False immediately when
    ``RENGA_SOCKET`` is unset, which short-circuits the renga subprocess
    handshake. Scrubbing ``ORG_TRANSPORT`` keeps the dispatch on the
    renga branch so a live broker session can't shell out to
    ``claude-org-runtime broker send``. Together that is exactly the
    hermetic behaviour we want for tests not asserting on the peer
    channel.
    """
    monkeypatch.delenv("RENGA_SOCKET", raising=False)
    monkeypatch.delenv("ORG_TRANSPORT", raising=False)
    yield
