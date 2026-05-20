#!/usr/bin/env python3
"""Runtime version drift check for /org-start (Issue #472).

Compares the installed ``claude-org-runtime`` version against the latest
release on PyPI. If they differ, prints a single warning line to stdout
so /org-start can splice it into its Step 4 readiness report. In all
other cases (versions match, package not installed, PyPI unreachable,
parse failure) the script stays silent.

The script never auto-upgrades and never exits non-zero — drift is
informational, and skipping silently is the correct behavior when the
machine is offline or in a sandboxed CI lane.

Used by ``.claude/skills/org-start/SKILL.md`` Block C2.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

# On Windows the default console codepage is cp932 / cp1252, which can't
# encode the JP message body. Force UTF-8 on stdout so /org-start (which
# reads this script's stdout via the Bash tool) gets a clean string.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

PACKAGE = "claude-org-runtime"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
TIMEOUT_SEC = 3.0


def _installed_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        return None
    try:
        return version(PACKAGE)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def _latest_version() -> str | None:
    try:
        req = urllib.request.Request(
            PYPI_JSON_URL,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None
    except (json.JSONDecodeError, ValueError):
        return None
    except Exception:
        return None
    info = payload.get("info") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        return None
    latest = info.get("version")
    if not isinstance(latest, str) or not latest:
        return None
    return latest


def main() -> int:
    installed = _installed_version()
    if installed is None:
        return 0
    latest = _latest_version()
    if latest is None:
        return 0
    if installed == latest:
        return 0
    print(
        f"[runtime drift] {PACKAGE}: installed={installed} latest={latest} "
        f"-- `pip install --upgrade {PACKAGE}` で最新化できます"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
