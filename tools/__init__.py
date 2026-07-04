"""``tools`` package marker.

Kept intentionally minimal. Its only behaviour is a *test-only* env guard
(Issue #683): when the ``tools`` package is imported by a test runner
(``unittest`` / ``pytest`` / a directly executed ``test_*.py``), any live
broker/renga transport env vars inherited from a real org session are
scrubbed from ``os.environ`` *before any test runs*, so neither in-process
peer emits nor subprocess-spawning tests can reach the live daemon. Under
normal production imports (``python tools/pr_watch.py`` and friends) the
guard is a no-op, so real peer delivery is unaffected.

Under ``python -m unittest discover -s tools`` this module is imported at
the very start of discovery, so the scrub lands before the first test
module is loaded. See ``tools/_hermetic_env.py`` for the full rationale.
"""
from __future__ import annotations

from ._hermetic_env import running_under_test, scrub_live_transport_env

if running_under_test():
    scrub_live_transport_env()
