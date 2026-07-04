"""Test-only guard: scrub live broker/renga transport env vars (Issue #683).

Background
----------
The runtime launcher injects ``ORG_BROKER_STATE_DIR`` / ``ORG_TRANSPORT``
into pane envs so the *real* CLI helpers reach the live broker daemon
(paired contract, claude-org-runtime #122/#127). That is correct for
production. It is dangerous for tests: several tool tests drive
``pr_watch.main`` / ``notify_peer`` end-to-end — either in-process or by
spawning ``python tools/pr_watch.py`` with ``env={**os.environ}`` — so a
suite run inside a live org session leaks fixture peer messages (observed:
``x`` and ``CI_COMPLETED: PR #4242 ... repo octo/repo``) onto the live
secretary channel.

The pre-existing guard lived in ``tools/conftest.py`` as a *pytest-only*
autouse fixture, but CI and workers run the suite with
``python -m unittest discover`` — where conftest never fires — and it only
scrubbed the renga vars, so the broker path (``ORG_TRANSPORT=broker`` +
``ORG_BROKER_STATE_DIR``) leaked regardless. Per-file ``setUpModule``
hooks (e.g. ``tools/test_pr_watch.py``) papered over one module at a time
and were renga-only. This module is the runner-agnostic replacement.

Design
------
* :func:`running_under_test` returns True only when the interpreter was
  started as a test runner (``python -m unittest``, ``pytest``, or a
  directly executed ``test_*.py``). Production tool invocations
  (``python tools/pr_watch.py`` etc.) return False, so live peer delivery
  is never disturbed.
* :func:`scrub_live_transport_env` removes the transport-routing vars from
  ``os.environ`` process-wide. Doing it once, early, protects both the
  in-process path *and* any child spawned with an inherited environment.

``tools/__init__.py`` calls the pair at package-import time (gated), which
under ``unittest discover -s tools`` runs before any test module is even
imported. ``tests/__init__.py`` scrubs unconditionally (that package is
imported only by the test runner).
"""
from __future__ import annotations

import os
import sys

# Env vars that route peer_notify / the broker CLI at a *live* daemon.
# Scrubbing all three forces the hermetic no-op path: peer_notify falls
# back to the renga branch (no ORG_TRANSPORT=broker) which then
# short-circuits because RENGA_SOCKET is gone too.
LIVE_TRANSPORT_ENV = ("ORG_BROKER_STATE_DIR", "ORG_TRANSPORT", "RENGA_SOCKET")


def scrub_live_transport_env() -> None:
    """Remove live broker/renga transport env vars from ``os.environ``.

    Idempotent and process-wide: children spawned afterwards with an
    inherited environment start clean too.
    """
    for name in LIVE_TRANSPORT_ENV:
        os.environ.pop(name, None)


def running_under_test() -> bool:
    """True iff this interpreter was launched as a test runner.

    Detection is deliberately conservative so it never fires for a
    production tool invocation:

    * ``python -m unittest ...`` -> ``sys.argv[0]`` is the unittest
      ``__main__.py`` path (contains ``"unittest"``).
    * ``pytest`` / ``python -m pytest`` -> ``sys.argv[0]`` contains
      ``"pytest"``, or pytest exports ``PYTEST_CURRENT_TEST`` /
      ``PYTEST_VERSION``.
    * ``python tools/test_foo.py`` -> the script basename starts with
      ``test``.

    Real tools (``pr_watch.py``, ``journal_append.py``,
    ``check_role_configs.py``, ...) match none of these, so the guard is a
    no-op for them.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if "unittest" in argv0 or "pytest" in argv0:
        return True
    base = os.path.basename(argv0)
    if base.startswith("test"):
        return True
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION"):
        return True
    return False
