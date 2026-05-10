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
ja pins ``claude-org-runtime`` to a narrow window (see
``RUNTIME_PIN_LOWER_INCLUSIVE`` / ``RUNTIME_PIN_UPPER_EXCLUSIVE``).
When the installed runtime is *outside* that window, the **byte check
skips with a warning** rather than failing — the bundled schema is by
definition allowed to evolve ahead of ja's pin window, and a hard
failure here would just block unrelated PRs. Inside the window the
byte check treats any structural difference as a hard failure.

The ``--semantic`` check, in contrast, runs unconditionally when
explicitly requested: an operator invoking it against a preview
runtime is asking "does my evaluator-shape golden still match?", and
silently skipping would defeat the point of the flag. The same
unconditional semantics apply to the matching pytest test.

Two drift dimensions
--------------------
The byte-identical schema check is necessary but not sufficient. Two
schemas can be byte-identical and still produce divergent rendered
sandbox suppression metadata if the runtime's
``render_role_with_metadata()`` evaluator changes shape (e.g. a new
suppression reason wording, a new placeholder substituted, a different
``sandbox_read_roots`` order). The "byte-identical" framing alone is
therefore incomplete after the Phase 1 sandbox-intent work landed.

The ``--semantic`` flag adds a complementary semantic golden drift
check: small in-repo fixtures under
``tests/fixtures/runtime_schema_drift/sandbox_intent/`` carry both an
input schema fragment and the expected explain JSON
(``SandboxMetadata.to_jsonable()``). The check renders each fixture
through ``render_role_with_metadata()`` with a fixture-supplied fake
``realpath`` shim (so platform-dependent paths don't pollute the
diff) and asserts the rendered explain JSON matches byte-for-byte.

Exit codes: 0 = OK or skipped (out-of-window), 1 = drift detected.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any, Callable

# Mirror of the ``claude-org-runtime`` pin in pyproject.toml /
# requirements.txt. Keep this constant in sync when widening the pin
# (Phase 5e+ scope per CLAUDE.local.md). Stored as tuples for ordered
# comparison; only major.minor.micro are honoured.
RUNTIME_PIN_LOWER_INCLUSIVE = (0, 1, 5)
RUNTIME_PIN_UPPER_EXCLUSIVE = (0, 2, 0)

REPO_ROOT = Path(__file__).resolve().parent.parent
JA_SCHEMA = REPO_ROOT / "tools" / "org_extension_schema.json"
SEMANTIC_FIXTURE_DIR = (
    REPO_ROOT / "tests" / "fixtures" / "runtime_schema_drift" / "sandbox_intent"
)


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


def _build_realpath_fn(
    rules: list[dict[str, str]],
) -> Callable[[str], str]:
    """Build a deterministic ``realpath`` shim from fixture rules.

    Each rule is a ``{"prefix", "replacement"}`` pair. For an input path
    ``p`` the first matching rule wins: a rule matches when ``p`` equals
    ``prefix`` or starts with ``prefix + "/"``; the matched prefix is
    swapped for ``replacement``. Paths with no matching rule pass
    through unchanged. The behaviour mirrors the fake-realpath stubs
    used by the runtime's own settings-generator unit tests.
    """
    compiled = list(rules or [])

    def _realpath(p: str) -> str:
        for rule in compiled:
            prefix = rule["prefix"]
            replacement = rule["replacement"]
            if p == prefix:
                return replacement
            if p.startswith(prefix + "/"):
                return replacement + p[len(prefix):]
        return p

    return _realpath


def _render_fixture_explain(fixture: dict[str, Any]) -> dict[str, Any]:
    """Render one fixture and return the canonical explain JSON.

    Imports the runtime lazily so a missing install surfaces the same
    way the byte check does.
    """
    from claude_org_runtime.settings.generator import (  # noqa: PLC0415
        render_role_with_metadata,
    )

    inputs = fixture["inputs"]
    realpath_fn = _build_realpath_fn(inputs.get("realpath_map", []))
    wsl_detected = bool(inputs.get("wsl_detected", False))
    result = render_role_with_metadata(
        inputs["schema_fragment"],
        role=inputs["role"],
        worker_dir=inputs["worker_dir"],
        claude_org_path=inputs["claude_org_path"],
        role_kind=inputs.get("role_kind", "worker"),
        base_clone=inputs.get("base_clone"),
        task_id=inputs.get("task_id"),
        branch_ref=inputs.get("branch_ref"),
        pattern=inputs.get("pattern"),
        realpath_fn=realpath_fn,
        wsl_detector=lambda: wsl_detected,
    )
    return result.sandbox.to_jsonable()


def _format_explain_diff(
    fixture_path: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> str:
    expected_text = json.dumps(expected, indent=2, sort_keys=True).splitlines(
        keepends=True
    )
    actual_text = json.dumps(actual, indent=2, sort_keys=True).splitlines(
        keepends=True
    )
    diff = difflib.unified_diff(
        expected_text,
        actual_text,
        fromfile=f"expected ({fixture_path.name})",
        tofile=f"actual ({fixture_path.name})",
    )
    return "".join(diff)


def _check_byte_drift(installed_str: str) -> int:
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


_FIXTURE_OUT_OF_SCOPE_FIELDS = ("verification_depth",)


def _find_forbidden_keys(
    obj: Any, forbidden: tuple[str, ...]
) -> list[tuple[str, str]]:
    """Walk ``obj`` and return ``(json_pointer, key)`` for every
    forbidden key found at any depth.

    Recurses through dicts and lists; ignores everything else. The
    ``json_pointer`` is a slash-joined path so violations point at
    the offending location even when the forbidden key is nested
    inside ``inputs.schema_fragment.worker_roles.<role>.…``.
    """
    hits: list[tuple[str, str]] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child_path = f"{path}/{key}" if path else key
                if key in forbidden:
                    hits.append((child_path, key))
                _walk(value, child_path)
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                _walk(value, f"{path}/{idx}")

    _walk(obj, "")
    return hits


def _validate_fixture_policy(
    fixture_path: Path, fixture: dict[str, Any]
) -> list[str]:
    """Return a list of policy violations for one fixture.

    Currently enforces only that the out-of-scope fields listed in
    ``_FIXTURE_OUT_OF_SCOPE_FIELDS`` (notably ``verification_depth``,
    which is a delegate-payload convention rather than a sandbox
    enforcement dimension) do not appear *anywhere* in the fixture —
    not just at the top level of ``inputs`` / ``expected_explain``,
    but also nested inside ``schema_fragment``. A shallow check would
    let a fixture sneak ``verification_depth`` into the schema body
    and silently establish a precedent the rest of the codebase has
    to maintain. Mirrors the policy check in
    ``tests/test_runtime_schema_drift_semantic.py`` so the manual CLI
    run gives the same answer as the unittest suite.
    """
    violations: list[str] = []
    hits = _find_forbidden_keys(fixture, _FIXTURE_OUT_OF_SCOPE_FIELDS)
    for path, key in hits:
        violations.append(
            f"{fixture_path.name}: {key!r} must not appear in fixture "
            f"(found at {path!r}; out-of-scope for sandbox semantic "
            "contract; see fixture-dir README)."
        )
    return violations


def _check_semantic_drift(installed_str: str) -> int:
    if not SEMANTIC_FIXTURE_DIR.is_dir():
        print(
            f"check_runtime_schema_drift: semantic fixture dir not found at "
            f"{SEMANTIC_FIXTURE_DIR}",
            file=sys.stderr,
        )
        return 1
    fixture_paths = sorted(SEMANTIC_FIXTURE_DIR.glob("*.json"))
    if not fixture_paths:
        print(
            f"check_runtime_schema_drift: no semantic fixtures found in "
            f"{SEMANTIC_FIXTURE_DIR}",
            file=sys.stderr,
        )
        return 1
    drift_seen = False
    policy_violations: list[str] = []
    for fixture_path in fixture_paths:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        policy_violations.extend(_validate_fixture_policy(fixture_path, fixture))
        expected = fixture["expected_explain"]
        actual = _render_fixture_explain(fixture)
        if expected == actual:
            continue
        drift_seen = True
        print(
            "check_runtime_schema_drift: SEMANTIC DRIFT — "
            f"{fixture_path.relative_to(REPO_ROOT)} explain JSON differs "
            f"from claude-org-runtime {installed_str} render output.",
            file=sys.stderr,
        )
        diff = _format_explain_diff(fixture_path, expected, actual)
        if diff:
            print(diff, file=sys.stderr)
    if policy_violations:
        for v in policy_violations:
            print(
                f"check_runtime_schema_drift: FIXTURE POLICY — {v}",
                file=sys.stderr,
            )
    if drift_seen:
        print(
            "  Update the affected fixture(s) under "
            f"{SEMANTIC_FIXTURE_DIR.relative_to(REPO_ROOT)}/ if the runtime "
            "behaviour change is intended, or fix the runtime if it is not.",
            file=sys.stderr,
        )
        return 1
    if policy_violations:
        return 1
    print(
        f"check_runtime_schema_drift: semantic OK (claude-org-runtime "
        f"{installed_str}, {len(fixture_paths)} fixture(s))"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Drift check between ja's tools/org_extension_schema.json and "
            "the bundled claude-org-runtime schema. With no flags runs the "
            "byte-identical check; --semantic switches to the render-output "
            "golden diff against fixture explain JSON; pass --byte "
            "--semantic together to run both."
        ),
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help=(
            "Run the semantic golden drift check against fixtures under "
            "tests/fixtures/runtime_schema_drift/sandbox_intent/. "
            "Without --byte this replaces the byte check; combine with "
            "--byte to run both."
        ),
    )
    parser.add_argument(
        "--byte",
        action="store_true",
        help=(
            "Run the byte-identical schema check (the default when no "
            "flags are passed). Combine with --semantic to run both."
        ),
    )
    args = parser.parse_args(argv)

    run_byte = args.byte or not args.semantic
    run_semantic = args.semantic

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
    in_window = _runtime_in_pin_window(installed)

    rc = 0
    if run_byte:
        # Byte check honours the pin window: a runtime preview release
        # is allowed to ship a schema ahead of ja's pin, so a hard
        # failure here would just block unrelated PRs. Skip with a
        # warning when out-of-window.
        if not in_window:
            print(
                f"check_runtime_schema_drift: WARN — installed "
                f"claude-org-runtime {installed_str} is outside ja's pin "
                f"window "
                f">={'.'.join(map(str, RUNTIME_PIN_LOWER_INCLUSIVE))},"
                f"<{'.'.join(map(str, RUNTIME_PIN_UPPER_EXCLUSIVE[:2]))}; "
                "skipping byte drift check (runtime is allowed to ship a "
                "new minor before ja widens the pin)."
            )
        else:
            rc = _check_byte_drift(installed_str) or rc
    if run_semantic:
        # Semantic check runs unconditionally when explicitly
        # requested. Operators invoking `--semantic` against a
        # 0.2-preview runtime want exactly this answer ("does the
        # explain JSON still match my goldens against the new
        # evaluator?"), and silently skipping would defeat the point
        # of the flag. The matching pytest test is the same shape:
        # it always runs against whatever runtime is importable.
        rc = _check_semantic_drift(installed_str) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
