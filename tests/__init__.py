"""``tests`` package marker + Issue #683 hermetic env guard.

This package is imported only by the test runner, so the live
broker/renga transport env vars are scrubbed unconditionally here (no
runner detection needed). Keeps tests under ``tests/`` from reaching a
live daemon, mirroring the gated guard in ``tools/__init__.py``. See
``tools/_hermetic_env.py`` for the full rationale.
"""
import os

for _name in ("ORG_BROKER_STATE_DIR", "ORG_TRANSPORT", "RENGA_SOCKET"):
    os.environ.pop(_name, None)
del _name
