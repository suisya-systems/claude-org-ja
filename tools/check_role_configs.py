#!/usr/bin/env python3
# Phase 5 shim audit: confirmed minimal as of 2026-05-04 (#130)
"""Role-based settings.local.json integrity checker (Step B shim).

The validation engine now lives in ``core_harness.validator``. This
module is a thin CLI shim that:

* Loads the org-extension data (``tools/org_extension_schema.json``)
  and merges it with the framework JSON Schema retrieved from the
  pinned ``core_harness`` package via
  ``core_harness.schema.load_framework_schema()``.
* Re-exports the public engine symbols (``Finding``,
  ``validate_config``, ``validate_schema_integrity``,
  ``check_worker_settings``) so existing callers — including the test
  suite under ``tests/test_check_role_configs.py`` — keep using
  ``check_role_configs`` as the import surface unchanged.
* Overrides ``extract_role_blocks`` with a bilingual wrapper that
  accepts either the canonical ja heading from
  ``org_extension_schema.json`` (``docs_section``) or its English
  counterpart (Issue #340, Option A — "make the parser bilingual").
  The ja repo's ``permissions.md`` ships ja headings; the en mirror
  translates them to English. Mapping each ja heading to the canonical
  role key and accepting the en alias as an additional substring
  marker lets the same parser project roles out of either localisation
  without forcing the en mirror to keep ja anchors. Options (B) and
  (C) from Issue #340 were considered: (B) requires every translation
  to preserve hidden ja anchors (brittle); (C) — schema-driven
  ``permissions.md`` — is the long-term clean path but a much bigger
  refactor. (A) is the least-invasive fix that unblocks
  ``test_docs_projection_is_consistent`` on both localisations.
* Keeps the ja-specific behaviour (``check_docs``, ``check_on_disk``,
  ``run``, the CLI argparser, exit-code contract) here, since those
  read from the ja repo layout (permissions.md docs projection, the
  worker-tracked settings file walk).

Exit codes: 0 = OK, non-zero = drift detected.

Run ``python tools/check_role_configs.py --help`` for options.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from core_harness.schema import load_framework_schema, merge_schemas
from core_harness.validator import (
    Finding,
    check_worker_settings,
    validate_config,
    validate_schema_integrity,
)


# Issue #340: ja → en heading aliases. Keys are the ja heading strings
# that org_extension_schema.json declares as ``docs_section``; values
# are the English heading substrings that the en mirror's
# permissions.md uses for the same role. ``extract_role_blocks`` below
# treats either as a valid section marker so the same parser projects
# roles out of either localisation.
_JA_TO_EN_ROLE_HEADING_ALIASES: dict[str, str] = {
    "ユーザー共通": "User-wide",
    "窓口": "Lead",
    "ディスパッチャー": "Dispatcher",
    "キュレーター": "Curator",
    "ワーカー": "Worker",
}


def extract_role_blocks(md_text: str, roles: dict) -> dict:
    """Extract the first ```json code block under each role's docs heading.

    Bilingual wrapper around ``core_harness.validator.extract_role_blocks``:
    a section matches if its ``## ``-prefixed heading line contains
    either the canonical ja ``docs_section`` declared in the schema or
    its English alias from ``_JA_TO_EN_ROLE_HEADING_ALIASES`` (Issue
    #340, Option A). Roles whose ``docs_section`` is null/missing are
    skipped, mirroring the upstream contract.
    """
    results: dict = {}
    sections = re.split(r"(?m)^## ", md_text)
    for role_name, role_def in roles.items():
        marker = role_def.get("docs_section")
        if not marker:
            continue
        markers = [marker]
        en_alias = _JA_TO_EN_ROLE_HEADING_ALIASES.get(marker)
        if en_alias:
            markers.append(en_alias)
        block = None
        for section in sections[1:]:
            heading = section.splitlines()[0]
            if any(m in heading for m in markers):
                m = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
                if m:
                    try:
                        block = json.loads(m.group(1))
                    except json.JSONDecodeError as exc:
                        block = {"__parse_error__": str(exc)}
                break
        results[role_name] = block
    return results


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPO_ROOT / "tools" / "org_extension_schema.json"
DEFAULT_PERMISSIONS_MD = (
    REPO_ROOT
    / ".claude"
    / "skills"
    / "org-setup"
    / "references"
    / "permissions.md"
)

__all__ = [
    "Finding",
    "REPO_ROOT",
    "DEFAULT_SCHEMA",
    "DEFAULT_PERMISSIONS_MD",
    "load_schema",
    "validate_config",
    "validate_schema_integrity",
    "extract_role_blocks",
    "check_worker_settings",
    "check_docs",
    "check_on_disk",
    "run",
    "main",
]


def load_schema(path: Path) -> dict:
    """Load the org-extension data and return the merged framework +
    extension dict.

    ``path`` points at the org-extension JSON. The framework JSON
    Schema is fetched from the pinned ``core_harness`` package (so the
    exact ``requirements.txt`` pin governs validator behaviour). The
    returned dict is what every downstream engine function expects
    (``global``, ``required_hook_scripts``, ``roles``,
    ``worker_roles``).
    """
    with Path(path).open(encoding="utf-8") as fh:
        org_extension = json.load(fh)
    framework = load_framework_schema()
    return merge_schemas(framework, org_extension)


def _load_override_allow(settings_path: Path) -> set:
    """Return the allow entries declared in sibling
    ``settings.local.override.json`` (the closed-world escape hatch).
    """
    ov = settings_path.with_name("settings.local.override.json")
    if not ov.is_file():
        return set()
    try:
        data = json.loads(ov.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        return set()
    allow = perms.get("allow")
    if not isinstance(allow, list):
        return set()
    return {x for x in allow if isinstance(x, str)}


def check_docs(schema: dict, permissions_md: Path) -> list:
    if not Path(permissions_md).is_file():
        return [
            Finding(
                str(permissions_md),
                "<docs>",
                "ERROR",
                "permissions.md not found",
            )
        ]
    text = Path(permissions_md).read_text(encoding="utf-8")
    blocks = extract_role_blocks(text, schema["roles"])
    findings: list = []
    for role_name, role_schema in schema["roles"].items():
        if not role_schema.get("docs_section"):
            continue
        config = blocks.get(role_name)
        findings.extend(
            validate_config(
                f"permissions.md[{role_schema['docs_section']}]",
                role_name,
                config,
                role_schema,
                schema.get("global", {}),
            )
        )
    return findings


class _GitTrackedError(Exception):
    """Raised when ``_is_git_tracked`` cannot reach a definite answer.

    Carries a short ``reason`` so the caller can surface it as an
    audit ``Finding``. Renamed-internal so callers must handle the
    fail-CLOSED case explicitly (see cross-review M1).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_git_tracked(path: Path, root: Path) -> bool:
    """Return True when ``path`` is tracked by git (not gitignored).

    Raises ``_GitTrackedError`` when the answer cannot be determined —
    e.g. ``git`` is not on PATH, or ``path`` lives outside ``root``.
    The caller MUST treat this as an audit failure (Finding ERROR);
    silently skipping such paths previously hid real drift on
    machines where git happens to be missing (cross-review M1).
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        raise _GitTrackedError(
            f"path {str(path)!r} is not under repository root {str(root)!r}; "
            "cannot determine git-tracked status"
        )
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel).replace("\\", "/")],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        raise _GitTrackedError(
            "git executable not found on PATH; cannot determine "
            "git-tracked status (audit fails closed)"
        )
    # ``git ls-files --error-unmatch`` exits 0 for tracked, 1 for not
    # tracked, and 128 for fatal errors (``safe.directory`` /
    # ``not a git repository`` / corrupt index / permission issues).
    # Treating 128 as "untracked" would silently skip the audit on
    # exactly the misconfigured machines that should fail loudest, so
    # we surface it as ``_GitTrackedError`` (cross-review M1 follow-up).
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    stderr_tail = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    if len(stderr_tail) > 200:
        stderr_tail = stderr_tail[:200] + "..."
    raise _GitTrackedError(
        f"git ls-files exited {result.returncode}"
        + (f": {stderr_tail}" if stderr_tail else "")
    )


WORKER_LOCAL_SETTINGS = ".claude/settings.local.json"


def check_on_disk(
    schema: dict,
    root: Path,
    include_untracked: bool = False,
    role_override: str | None = None,
) -> list:
    findings: list = []
    if role_override is not None:
        role_schema = schema["roles"].get(role_override)
        if role_schema is None:
            findings.append(
                Finding(
                    "<cli>",
                    role_override,
                    "ERROR",
                    f"unknown --role: {role_override!r}",
                )
            )
            return findings
        candidate_paths = role_schema.get("settings_paths") or [WORKER_LOCAL_SETTINGS]
        checked_any = False
        for rel in candidate_paths:
            path = Path(root) / rel
            if not path.is_file():
                continue
            checked_any = True
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                findings.append(
                    Finding(
                        str(path),
                        role_override,
                        "ERROR",
                        f"JSON parse error: {exc}",
                    )
                )
                continue
            findings.extend(
                validate_config(
                    str(path),
                    role_override,
                    config,
                    role_schema,
                    schema.get("global", {}),
                    extra_allowed=_load_override_allow(path),
                )
            )
        if not checked_any:
            findings.append(
                Finding(
                    str(Path(root) / candidate_paths[0]),
                    role_override,
                    "ERROR",
                    (
                        "settings.local.json not found; tried: "
                        + ", ".join(str(Path(root) / p) for p in candidate_paths)
                    ),
                )
            )
        return findings

    for role_name, role_schema in schema["roles"].items():
        for rel in role_schema.get("settings_paths", []):
            path = Path(root) / rel
            if not path.is_file():
                continue
            if not include_untracked:
                try:
                    tracked = _is_git_tracked(path, Path(root))
                except _GitTrackedError as exc:
                    findings.append(
                        Finding(
                            str(path),
                            role_name,
                            "ERROR",
                            (
                                "could not determine git-tracked status "
                                f"({exc.reason}); pass --include-local to "
                                "audit this file regardless, or install git"
                            ),
                        )
                    )
                    continue
                if not tracked:
                    continue
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                findings.append(
                    Finding(
                        str(path),
                        role_name,
                        "ERROR",
                        f"JSON parse error: {exc}",
                    )
                )
                continue
            findings.extend(
                validate_config(
                    str(path),
                    role_name,
                    config,
                    role_schema,
                    schema.get("global", {}),
                    extra_allowed=_load_override_allow(path),
                )
            )
    return findings


def run(
    schema_path: Path = DEFAULT_SCHEMA,
    permissions_md: Path = DEFAULT_PERMISSIONS_MD,
    root: Path = REPO_ROOT,
    include_on_disk: bool = True,
    include_untracked: bool = False,
    role_override: str | None = None,
    worker_settings_base: Path | None = None,
) -> list:
    schema = load_schema(schema_path)
    findings: list = []
    findings.extend(validate_schema_integrity(schema))
    findings.extend(check_docs(schema, permissions_md))
    if include_on_disk:
        findings.extend(
            check_on_disk(
                schema,
                root,
                include_untracked=include_untracked,
                role_override=role_override,
            )
        )
    if worker_settings_base is not None:
        # include_worktrees=True (core-harness 0.3.1+) descends into
        # ``<base>/.worktrees/<branch>/`` so worker checkouts living
        # under a `.worktrees/` parent are audited too. Refs M4.
        findings.extend(
            check_worker_settings(
                schema,
                worker_settings_base,
                include_worktrees=True,
            )
        )
    return findings


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate per-role settings.local.json against the schema."
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--permissions-md", type=Path, default=DEFAULT_PERMISSIONS_MD)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--docs-only",
        action="store_true",
        help=(
            "Validate only permissions.md + schema integrity; skip every "
            "on-disk settings*.json. Default validates tracked settings files."
        ),
    )
    parser.add_argument(
        "--include-local",
        action="store_true",
        help=(
            "Also validate gitignored / untracked on-disk settings.local.json "
            "files at the schema-declared paths. Default checks only tracked "
            "files (e.g. .claude/settings.json) so CI and local runs agree."
        ),
    )
    parser.add_argument(
        "--role",
        default=None,
        help=(
            "Validate <root>/.claude/settings.local.json against the given "
            "role schema (e.g. 'worker' when invoked from inside a worker "
            "worktree). Resolves path ambiguity since .claude/settings.local.json "
            "hosts different role configs in different worktrees. Implies "
            "--include-local semantics."
        ),
    )
    parser.add_argument(
        "--include-worker-settings",
        type=Path,
        default=None,
        metavar="BASE_DIR",
        help=(
            "Also enumerate <BASE_DIR>/*/.claude/settings.local.json and "
            "report drift against the worker_roles templates in the schema. "
            "Opt-in; existing invocations are unaffected."
        ),
    )
    args = parser.parse_args(argv)

    findings = run(
        schema_path=args.schema,
        permissions_md=args.permissions_md,
        root=args.root,
        include_on_disk=not args.docs_only,
        include_untracked=args.include_local or args.role is not None,
        role_override=args.role,
        worker_settings_base=args.include_worker_settings,
    )

    if not findings:
        print("role_configs: OK")
        return 0

    for f in findings:
        try:
            print(f.format())
        except UnicodeEncodeError:
            print(f.format().encode("ascii", "replace").decode("ascii"))
    errors = sum(1 for f in findings if f.severity == "ERROR")
    print(f"role_configs: {errors} error(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
