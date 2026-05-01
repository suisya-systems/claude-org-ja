#!/usr/bin/env python3
"""Role-based settings.local.json integrity checker (Step B shim).

The validation engine now lives in ``core_harness.validator``. This
module is a thin CLI shim that:

* Loads the org-extension data (``tools/org_extension_schema.json``)
  and merges it with the framework JSON Schema
  (``tools/framework_schema.json``, a temporary local copy of
  ``core_harness.schema.framework_schema.json`` while ja transitions
  off it).
* Re-exports the public engine symbols (``Finding``,
  ``validate_config``, ``validate_schema_integrity``,
  ``extract_role_blocks``, ``check_worker_settings``) so existing
  callers — including the test suite under
  ``tests/test_check_role_configs.py`` — keep using
  ``check_role_configs`` as the import surface unchanged.
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
import subprocess
import sys
from pathlib import Path

from core_harness.schema import load_framework_schema, merge_schemas
from core_harness.validator import (
    Finding,
    check_worker_settings,
    extract_role_blocks,
    validate_config,
    validate_schema_integrity,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPO_ROOT / "tools" / "org_extension_schema.json"
DEFAULT_FRAMEWORK_SCHEMA = REPO_ROOT / "tools" / "framework_schema.json"
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
    "DEFAULT_FRAMEWORK_SCHEMA",
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
    exact ``requirements.txt`` pin governs validator behaviour); the
    local ``tools/framework_schema.json`` copy is only used as a
    fallback for offline / pre-install workflows. The returned dict
    is what every downstream engine function expects (``global``,
    ``required_hook_scripts``, ``roles``, ``worker_roles``).
    """
    with Path(path).open(encoding="utf-8") as fh:
        org_extension = json.load(fh)
    try:
        framework = load_framework_schema()
    except Exception:
        if DEFAULT_FRAMEWORK_SCHEMA.is_file():
            with DEFAULT_FRAMEWORK_SCHEMA.open(encoding="utf-8") as fh:
                framework = json.load(fh)
        else:
            raise
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


def _is_git_tracked(path: Path, root: Path) -> bool:
    """Return True when ``path`` is tracked by git (not gitignored)."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel).replace("\\", "/")],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


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
            if not _is_git_tracked(path, Path(root)) and not include_untracked:
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
        findings.extend(check_worker_settings(schema, worker_settings_base))
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
