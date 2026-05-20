#!/usr/bin/env python3
"""Runtime version drift check for /org-start (Issue #472).

Compares the installed ``claude-org-runtime`` version against the
latest release on PyPI that still satisfies ja's pin window
(declared in ``pyproject.toml`` dependencies). If they differ,
prints a single warning line to stdout so /org-start can splice it
into its Step 4 readiness report. In all other cases — versions
match, package not installed, PyPI unreachable, parse failure, no
pin-compatible release found, ``packaging`` import failure — the
script stays silent.

The pin window matters because /org-start's warning must not steer
users into an upgrade that breaks ja's compatibility contract: when
ja's upper bound is exclusive and PyPI ships a release past it, we
still want to bring users up to the latest in-window release but
never recommend the out-of-window one. No specific version is
hard-coded anywhere — installed comes from ``importlib.metadata``,
latest from the PyPI JSON API, and the pin spec from a regex over
``pyproject.toml`` at read time.

The script never auto-upgrades and never exits non-zero — drift is
informational, and skipping silently is the correct behavior when the
machine is offline or in a sandboxed CI lane.

Used by ``.claude/skills/org-start/SKILL.md`` Block C2.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

PACKAGE = "claude-org-runtime"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
TIMEOUT_SEC = 3.0
PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"

# Sentinel so callers can pass ``pin=None`` to *opt out* of the pin
# window, while leaving the default unspecified branch free to
# auto-discover the pin from pyproject.toml.
_AUTO_PIN = object()


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


def _read_pin_spec() -> str | None:
    """Return the version specifier string for PACKAGE from
    pyproject.toml, or None when it can't be located. We grep with a
    regex rather than parse TOML so the script keeps its
    zero-runtime-dependency contract (Python 3.10 has no stdlib
    tomllib)."""
    try:
        text = PYPROJECT_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(
        rf'["\']{re.escape(PACKAGE)}\s*([^"\']*?)["\']',
        text,
    )
    if not m:
        return None
    spec = m.group(1).strip()
    return spec or None


def _fetch_pypi_payload() -> dict | None:
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
    return payload if isinstance(payload, dict) else None


def _latest_version(pin=_AUTO_PIN) -> str | None:
    """Return the newest stable release of PACKAGE on PyPI that
    satisfies ``pin``. Pass ``pin=None`` to disable the pin window;
    omit the argument to auto-discover the pin from pyproject.toml.
    Returns None on any failure path so the caller stays silent."""
    payload = _fetch_pypi_payload()
    if payload is None:
        return None

    if pin is _AUTO_PIN:
        pin = _read_pin_spec()

    releases = payload.get("releases")
    if isinstance(releases, dict) and releases:
        try:
            from packaging.specifiers import InvalidSpecifier, SpecifierSet
            from packaging.version import InvalidVersion, Version
        except ImportError:
            return _fallback_info_version(payload, pin)
        if pin:
            try:
                spec: SpecifierSet | None = SpecifierSet(pin)
            except InvalidSpecifier:
                # Pin parse failure: prefer silence over recommending an
                # out-of-window upgrade (the docstring guarantees silent
                # skip on parse failure).
                return None
        else:
            spec = None
        candidates: list[Version] = []
        for raw, files in releases.items():
            if _release_is_yanked(files):
                continue
            try:
                ver = Version(raw)
            except InvalidVersion:
                continue
            if ver.is_prerelease or ver.is_devrelease:
                continue
            if spec is not None and ver not in spec:
                continue
            candidates.append(ver)
        if candidates:
            return str(max(candidates))
        return None

    return _fallback_info_version(payload, pin)


def _release_is_yanked(files) -> bool:
    """A PyPI release version is considered yanked when every uploaded
    file under it is marked yanked. Conservatively treat an empty file
    list as "not yanked" so we don't drop legitimate releases that
    simply lack file metadata in the JSON snapshot."""
    if not isinstance(files, list) or not files:
        return False
    for entry in files:
        if not isinstance(entry, dict):
            return False
        if not entry.get("yanked"):
            return False
    return True


def _fallback_info_version(payload: dict, pin: str | None) -> str | None:
    """Last-resort path when releases dict is missing or packaging is
    unavailable. Trusts info.version only when no pin window applies
    — silent skip otherwise to avoid recommending an out-of-window
    upgrade."""
    info = payload.get("info") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        return None
    latest = info.get("version")
    if not isinstance(latest, str) or not latest:
        return None
    if pin:
        return None
    return latest


def main() -> int:
    installed = _installed_version()
    if installed is None:
        return 0
    pin = _read_pin_spec()
    latest = _latest_version(pin)
    if latest is None:
        return 0
    if installed == latest:
        return 0
    # Bare ``pip install --upgrade claude-org-runtime`` would fetch the
    # PyPI maximum, which may be out of ja's pin window. Bake the pin
    # spec into the recommended command so the user never gets steered
    # to an unsupported version.
    spec_suffix = pin if pin else ""
    upgrade_target = f"{PACKAGE}{spec_suffix}"
    print(
        f"[runtime drift] {PACKAGE}: installed={installed} latest={latest} "
        f"-- `python -m pip install --upgrade '{upgrade_target}'` で更新できます"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
