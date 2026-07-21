#!/usr/bin/env python3
"""Runtime version drift check for /org-start (Issue #472).

Compares the installed ``claude-org-runtime`` version against the
latest release on PyPI that still satisfies ja's pin window (declared
in ``pyproject.toml`` dependencies), and reports the outcome through a
small **exit-code contract** so a sandboxed or offline run can no
longer hide a stale pin behind a silent skip.

That silent skip was the #119 phantom-dispatch root cause: /org-start's
drift check ran inside the Claude Code Bash sandbox, PyPI was
unreachable, the script printed nothing and exited 0, and the operator
read the silence as "up to date" -- then delegated a runtime bug that
had already been fixed upstream (0.1.36) while the venv sat on 0.1.34.

Outcome contract:

* stdout carries the single ``[runtime drift] ...`` line **only** on
  the drift outcome, so /org-start can keep splicing stdout verbatim
  into its readiness report.
* Every "couldn't verify" and "not installed" outcome prints a human
  diagnostic to **stderr** (never stdout) so the reason is visible
  without polluting the spliceable stdout line.
* The process exit code distinguishes the outcomes:

    0  EXIT_OK             installed is the pin-window latest (or a
                           newer preview build); PyPI was reached.
    1  EXIT_DRIFT          installed != latest-in-window; stdout has
                           the ``[runtime drift]`` line.
    2  EXIT_UNVERIFIED     drift could not be verified -- either the
                           latest is undeterminable (offline / PyPI
                           error / JSON parse / no in-window release /
                           packaging missing / pin parse failure), or
                           the installed came from a local source
                           (file:// path / VCS / editable per PEP 610
                           direct_url.json) whose version need not match
                           any PyPI release; stderr has a reason
                           diagnostic.
    3  EXIT_NOT_INSTALLED  package not importable from this Python;
                           stderr note.

The script still never auto-upgrades. No version is hard-coded:
installed comes from ``importlib.metadata``, latest from the PyPI JSON
API, and the pin spec from a regex over ``pyproject.toml`` at read
time.

Used by ``.claude/skills/org-start/SKILL.md`` Block C2, which runs this
outside the sandbox (``dangerouslyDisableSandbox: true``) so exit 2 is
the rare degraded case, not the norm.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

PACKAGE = "claude-org-runtime"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
# Widened from 3.0s: a slow-but-reachable network must not be misread
# as offline. Offline is now surfaced (exit 2 + stderr) rather than
# silently skipped, so a longer wait before giving up is worthwhile.
TIMEOUT_SEC = 8.0
PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"

# Exit codes -- the outcome contract described in the module docstring.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_UNVERIFIED = 2
EXIT_NOT_INSTALLED = 3

# Sentinel so callers can pass ``pin=None`` to *opt out* of the pin
# window, while leaving the default unspecified branch free to
# auto-discover the pin from pyproject.toml.
_AUTO_PIN = object()

# Reason codes for the EXIT_UNVERIFIED outcome. They let /org-start
# tell "offline -- retry on a networked host" apart from "the check
# itself is degraded" (e.g. packaging missing), and each maps to a
# human diagnostic below.
REASON_OFFLINE = "offline"
REASON_PYPI_ERROR = "pypi_error"
REASON_NO_IN_WINDOW_RELEASE = "no_in_window_release"
REASON_PACKAGING_MISSING = "packaging_missing"
REASON_PIN_PARSE_FAILED = "pin_parse_failed"

_REASON_DIAGNOSTICS = {
    REASON_OFFLINE: (
        "PyPI ({url}) に到達できませんでした（オフライン / sandbox 内実行の"
        "可能性）。ネットワーク到達可能なホストで再実行するまで drift は"
        "未確認です。"
    ),
    REASON_PYPI_ERROR: (
        "PyPI ({url}) の応答を解釈できませんでした（HTTP エラー / 不正な"
        "JSON）。drift は未確認です。"
    ),
    REASON_NO_IN_WINDOW_RELEASE: (
        "PyPI に pin 窓 ({pin}) を満たす stable release が見つかりません"
        "でした。drift は未確認です（pin 窓が upstream の実リリースと"
        "乖離している可能性）。"
    ),
    REASON_PACKAGING_MISSING: (
        "packaging が未インストールのため pin 窓 ({pin}) を適用した latest"
        "解決ができませんでした。drift は未確認です。"
    ),
    REASON_PIN_PARSE_FAILED: (
        "pyproject.toml の pin 指定 ({pin}) を解釈できませんでした。窓外"
        " upgrade を促さないため drift は未確認扱いにします。"
    ),
}


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


def _direct_url_local_reason() -> str | None:
    """Return a short human label when the installed distribution came
    from a **local source** -- a ``file://`` path, a VCS checkout, or an
    editable install -- as recorded in PEP 610 ``direct_url.json``. Such
    an install's ``version`` string can't be trusted to correspond to a
    PyPI release (a local build may advertise any version), so drift
    against PyPI is meaningless.

    Returns None for a normal PyPI/index install: pip writes no
    ``direct_url.json`` for those, and a direct URL that isn't a local
    source (e.g. a plain https archive) is left to the usual PyPI path.

    Read via ``importlib.metadata.Distribution.read_text`` rather than
    globbing dist-info so the resolution matches whichever distribution
    this Python actually imports."""
    try:
        from importlib.metadata import PackageNotFoundError, distribution
    except ImportError:
        return None
    try:
        dist = distribution(PACKAGE)
    except PackageNotFoundError:
        return None
    except Exception:
        return None
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        raw = None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    url = data.get("url")
    url_label = url if isinstance(url, str) and url else "(url 不明)"
    # VCS install (git/hg/...): version reflects the checked-out ref,
    # not a released artifact.
    vcs_info = data.get("vcs_info")
    if isinstance(vcs_info, dict):
        vcs = vcs_info.get("vcs")
        vcs = vcs if isinstance(vcs, str) and vcs else "vcs"
        return f"VCS install ({vcs}: {url_label})"
    # Editable install: an in-place checkout; its version is whatever the
    # working tree declares.
    dir_info = data.get("dir_info")
    if isinstance(dir_info, dict) and dir_info.get("editable"):
        return f"editable install ({url_label})"
    # Plain local path / local archive install (file:// scheme).
    if isinstance(url, str) and url.startswith("file:"):
        return f"file:// install ({url_label})"
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


def _fetch_pypi_payload() -> tuple[dict | None, str | None]:
    """Return ``(payload, reason)``. On success ``reason`` is None. On
    failure ``payload`` is None and ``reason`` distinguishes a network
    reach failure (``REASON_OFFLINE``) from a reached-but-bad response
    (``REASON_PYPI_ERROR`` -- HTTP error status or undecodable JSON) so
    the caller can print an accurate diagnostic instead of skipping
    silently."""
    try:
        req = urllib.request.Request(
            PYPI_JSON_URL,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError:
        # Reached PyPI but got a non-2xx status: a server-side problem,
        # not an offline host.
        return None, REASON_PYPI_ERROR
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None, REASON_OFFLINE
    except (json.JSONDecodeError, ValueError):
        return None, REASON_PYPI_ERROR
    except Exception:
        return None, REASON_PYPI_ERROR
    if not isinstance(payload, dict):
        return None, REASON_PYPI_ERROR
    return payload, None


def _latest_version_with_reason(pin=_AUTO_PIN) -> tuple[str | None, str | None]:
    """Return ``(version, reason)`` for the newest stable release of
    PACKAGE on PyPI that satisfies ``pin``. On success ``reason`` is
    None; on any failure ``version`` is None and ``reason`` is one of
    the ``REASON_*`` codes. Pass ``pin=None`` to disable the pin
    window; omit the argument to auto-discover the pin from
    pyproject.toml."""
    payload, reason = _fetch_pypi_payload()
    if payload is None:
        return None, reason

    if pin is _AUTO_PIN:
        pin = _read_pin_spec()

    releases = payload.get("releases")
    if isinstance(releases, dict) and releases:
        try:
            from packaging.specifiers import InvalidSpecifier, SpecifierSet
            from packaging.version import InvalidVersion, Version
        except ImportError:
            fallback = _fallback_info_version(payload, pin)
            if fallback is not None:
                return fallback, None
            return None, REASON_PACKAGING_MISSING
        if pin:
            try:
                spec: SpecifierSet | None = SpecifierSet(pin)
            except InvalidSpecifier:
                # Pin parse failure: prefer an explicit "unverified"
                # over recommending an out-of-window upgrade.
                return None, REASON_PIN_PARSE_FAILED
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
            return str(max(candidates)), None
        return None, REASON_NO_IN_WINDOW_RELEASE

    fallback = _fallback_info_version(payload, pin)
    if fallback is not None:
        return fallback, None
    # No releases dict and no usable info.version. With a pin we can't
    # enforce the window (treat as no in-window release); without one
    # the payload is simply malformed.
    if pin:
        return None, REASON_NO_IN_WINDOW_RELEASE
    return None, REASON_PYPI_ERROR


def _latest_version(pin=_AUTO_PIN) -> str | None:
    """Backward-compatible thin wrapper returning just the resolved
    version (or None). ``main()`` uses ``_latest_version_with_reason``
    to also surface *why* resolution failed."""
    return _latest_version_with_reason(pin)[0]


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
    -- returns None otherwise to avoid recommending an out-of-window
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


def _emit_diagnostic(reason: str | None, pin: str | None) -> None:
    """Print the human-readable reason for an EXIT_UNVERIFIED outcome
    to stderr (never stdout, which is reserved for the drift line)."""
    template = _REASON_DIAGNOSTICS.get(
        reason, "latest を判定できませんでした（reason={reason}）。drift は未確認です。"
    )
    message = template.format(
        url=PYPI_JSON_URL,
        pin=pin if pin else "(pin 未指定)",
        reason=reason,
    )
    print(f"[runtime drift-check] {PACKAGE}: {message}", file=sys.stderr)


def main() -> int:
    installed = _installed_version()
    if installed is None:
        print(
            f"[runtime drift-check] {PACKAGE} をこの Python から import できません"
            "（未インストール / 別 venv）。installed 版を確認できないため drift は"
            "未確認です。",
            file=sys.stderr,
        )
        return EXIT_NOT_INSTALLED
    # A local install (file:// path / VCS checkout / editable) advertises
    # a version that need not match any PyPI release, so comparing it
    # against latest is meaningless -- surface it as "unverified" rather
    # than let a locally-built version read as up-to-date (Issue #747:
    # a file:// install was mis-reported as "最新・drift なし"). Checked
    # before the PyPI fetch since the verdict doesn't depend on the
    # network.
    local_reason = _direct_url_local_reason()
    if local_reason is not None:
        print(
            f"[runtime drift-check] {PACKAGE}: local install -- PyPI 照合不能"
            f"（installed={installed} は {local_reason}。installed の実体が"
            "リリース版とは限らないため drift を判定しません。PyPI 由来の"
            "インストールで再確認してください）。",
            file=sys.stderr,
        )
        return EXIT_UNVERIFIED
    pin = _read_pin_spec()
    latest, reason = _latest_version_with_reason(pin)
    if latest is None:
        _emit_diagnostic(reason, pin)
        return EXIT_UNVERIFIED
    if installed == latest:
        return EXIT_OK
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
    return EXIT_DRIFT


if __name__ == "__main__":
    sys.exit(main())
