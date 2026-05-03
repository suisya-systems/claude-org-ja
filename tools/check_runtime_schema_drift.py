#!/usr/bin/env python3
"""Drift check: ja's ``tools/org_extension_schema.json`` vs the
``role_configs_schema.json`` bundled inside the ``claude-org-runtime``
package (Phase 5c, Issue #130).

The runtime ships its own copy of the role-configs schema (used by
``claude_org_runtime.settings.generator``). Historically the two copies
have been kept in lockstep by hand. This tool fails CI when they
diverge so the next contributor doesn't unknowingly publish a
runtime-incompatible schema change.

Pin-window tolerance
--------------------
ja pins ``claude-org-runtime>=0.1.1,<0.2``. When the installed runtime
is *outside* that window (e.g. a contributor previewed 0.2.0 locally
before ja widened its pin), this check **skips with a warning** rather
than failing — the bundled schema is by definition allowed to evolve
ahead of ja's pin window, and a hard failure here would just block
unrelated PRs.

When the installed runtime is *inside* the window, any difference is a
hard failure: the two schemas are supposed to be byte-identical.

Exit codes: 0 = OK or skipped (out-of-window), 1 = drift detected.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

# Mirror of the ``claude-org-runtime`` pin in pyproject.toml /
# requirements.txt. Keep this constant in sync when widening the pin
# (Phase 5e+ scope per CLAUDE.local.md). Stored as tuples for ordered
# comparison; only major.minor.micro are honoured.
RUNTIME_PIN_LOWER_INCLUSIVE = (0, 1, 1)
RUNTIME_PIN_UPPER_EXCLUSIVE = (0, 2, 0)

REPO_ROOT = Path(__file__).resolve().parent.parent
JA_SCHEMA = REPO_ROOT / "tools" / "org_extension_schema.json"


def _parse_version(ver: str) -> tuple[int, ...]:
    """Parse a PEP 440 release segment to a (major, minor, micro) tuple.

    Pre-release / dev / local segments are stripped — drift tolerance
    is decided on the release segment alone, matching how the pin
    specifier ``>=0.1.1,<0.2`` is interpreted by pip.
    """
    head = ver.split("+", 1)[0]
    for marker in ("a", "b", "rc", ".dev", ".post"):
        idx = head.find(marker)
        if idx != -1:
            head = head[:idx]
    parts: list[int] = []
    for chunk in head.split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _runtime_in_pin_window(installed: tuple[int, ...]) -> bool:
    return RUNTIME_PIN_LOWER_INCLUSIVE <= installed < RUNTIME_PIN_UPPER_EXCLUSIVE


def _bundled_schema_path() -> Path:
    """Return the on-disk path to the runtime's bundled
    ``role_configs_schema.json``.

    Imports lazily so the script can fail with a helpful message when
    ``claude-org-runtime`` is not installed, rather than dying at
    import time.
    """
    from importlib.resources import files

    resource = files("claude_org_runtime.settings").joinpath(
        "role_configs_schema.json"
    )
    return Path(str(resource))


def _normalise(obj: object) -> object:
    """Strip cosmetic-only keys before comparison.

    ``$comment`` entries are documentation aids and may legitimately
    differ between the two checked-in copies (e.g. ja adds a comment
    pointing at the projection script). Everything else must match.
    """
    if isinstance(obj, dict):
        return {
            k: _normalise(v) for k, v in obj.items() if not k.startswith("$comment")
        }
    if isinstance(obj, list):
        return [_normalise(x) for x in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    del argv  # currently no flags; kept for signature consistency
    try:
        installed_str = _pkg_version("claude-org-runtime")
    except PackageNotFoundError:
        print(
            "check_runtime_schema_drift: claude-org-runtime is not installed; "
            "run `pip install -e .` first.",
            file=sys.stderr,
        )
        return 1
    installed = _parse_version(installed_str)
    if not _runtime_in_pin_window(installed):
        print(
            f"check_runtime_schema_drift: WARN — installed claude-org-runtime "
            f"{installed_str} is outside ja's pin window "
            f">={'.'.join(map(str, RUNTIME_PIN_LOWER_INCLUSIVE))},"
            f"<{'.'.join(map(str, RUNTIME_PIN_UPPER_EXCLUSIVE[:2]))}; "
            "skipping strict drift check (runtime is allowed to ship a new "
            "minor before ja widens the pin)."
        )
        return 0

    bundled_path = _bundled_schema_path()
    if not bundled_path.is_file():
        print(
            f"check_runtime_schema_drift: bundled schema not found at "
            f"{bundled_path}",
            file=sys.stderr,
        )
        return 1
    if not JA_SCHEMA.is_file():
        print(
            f"check_runtime_schema_drift: ja schema not found at {JA_SCHEMA}",
            file=sys.stderr,
        )
        return 1

    bundled = json.loads(bundled_path.read_text(encoding="utf-8"))
    ja = json.loads(JA_SCHEMA.read_text(encoding="utf-8"))

    if _normalise(bundled) == _normalise(ja):
        print(
            f"check_runtime_schema_drift: OK (claude-org-runtime "
            f"{installed_str} bundled schema matches {JA_SCHEMA.name})"
        )
        return 0

    print(
        "check_runtime_schema_drift: DRIFT — ja's "
        f"{JA_SCHEMA.name} differs from the schema bundled with "
        f"claude-org-runtime {installed_str} ({bundled_path}).",
        file=sys.stderr,
    )
    print(
        "  Either update tools/org_extension_schema.json to match the "
        "runtime, or release a new runtime that matches ja's schema, "
        "then re-pin in pyproject.toml + requirements.txt.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
