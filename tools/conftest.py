"""pytest fixtures shared across tools/ test modules.

Issue #398 follow-up / Issue #590 / Issue #683: several tool tests
exercise ``pr_watch.main()`` end-to-end, which calls ``_notify_peer`` ->
``tools.peer_notify.notify_peer``. Inside a live Claude Code / renga
session ``RENGA_SOCKET`` is set, and inside a live broker session
``ORG_TRANSPORT=broker`` + ``ORG_BROKER_STATE_DIR`` are set; either lets a
test run leak fake CI / merge messages onto the production peer channel.
CI runners have none of these so they never noticed.

This autouse fixture is the pytest arm of the guard. The runner-agnostic
arm — which also covers ``python -m unittest discover`` (the CI /worker
path, where conftest never fires) and directly executed ``test_*.py`` —
lives in ``tools/__init__.py`` + ``tools/_hermetic_env.py`` and scrubs at
package-import time. Both scrub the same var set
(``tools._hermetic_env.LIVE_TRANSPORT_ENV``). Tests that explicitly want
to exercise peer-emit behaviour should mock
``tools.peer_notify.notify_peer`` (or set the env vars themselves).
"""
from __future__ import annotations

import pytest

from tools._hermetic_env import LIVE_TRANSPORT_ENV


@pytest.fixture(autouse=True)
def _scrub_live_transport_env(monkeypatch):
    """Disable peer-emit fall-through (both transports) for the test.

    ``peer_notify.notify_peer`` short-circuits to a no-op when
    ``ORG_TRANSPORT`` is not ``broker`` and ``RENGA_SOCKET`` is unset;
    scrubbing ``ORG_BROKER_STATE_DIR`` additionally keeps a stray
    ``--state-dir`` from pointing the broker CLI at a live daemon. Using
    ``monkeypatch`` restores any real values after the test.
    """
    for name in LIVE_TRANSPORT_ENV:
        monkeypatch.delenv(name, raising=False)
    yield
