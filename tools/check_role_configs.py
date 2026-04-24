#!/usr/bin/env python3
"""Role-based settings.local.json integrity checker.

Source of truth: ``tools/role_configs_schema.json``.

Validates two projections of the schema:

1. ``permissions.md`` (``.claude/skills/org-setup/references/permissions.md``)
   — the human-readable role templates embedded as fenced ``json`` blocks.
2. Any on-disk ``settings.local.json`` files found at the known role paths
   (optional; skipped silently when absent — typical in CI since these files
   are gitignored).

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
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPO_ROOT / "tools" / "role_configs_schema.json"
DEFAULT_PERMISSIONS_MD = (
    REPO_ROOT
    / ".claude"
    / "skills"
    / "org-setup"
    / "references"
    / "permissions.md"
)


class Finding:
    __slots__ = ("source", "role", "severity", "message")

    def __init__(self, source: str, role: str, severity: str, message: str):
        self.source = source
        self.role = role
        self.severity = severity
        self.message = message

    def format(self) -> str:
        return f"[{self.severity}] {self.source} :: {self.role} :: {self.message}"


def load_schema(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def extract_role_blocks(md_text: str, roles: dict) -> dict:
    """Extract the first ```json code block under each role's heading.

    Section boundaries are `## ` markdown headings. The ``docs_section`` field
    in the schema must appear inside the heading text.
    """
    results: dict[str, dict | None] = {}
    sections = re.split(r"(?m)^## ", md_text)
    # sections[0] = content before first ##
    for role_name, role_def in roles.items():
        marker = role_def.get("docs_section")
        if not marker:
            continue
        block = None
        for section in sections[1:]:
            if marker in section.splitlines()[0]:
                m = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
                if m:
                    try:
                        block = json.loads(m.group(1))
                    except json.JSONDecodeError as exc:
                        block = {"__parse_error__": str(exc)}
                break
        results[role_name] = block
    return results


def _get_allow(config: dict) -> list:
    return ((config.get("permissions") or {}).get("allow")) or []


def _get_deny(config: dict) -> list:
    return ((config.get("permissions") or {}).get("deny")) or []


def _iter_hooks(config: dict):
    hooks = (config.get("hooks") or {})
    for event, entries in hooks.items():
        for entry in entries or []:
            matcher = entry.get("matcher", "") or ""
            for sub in (entry.get("hooks") or []):
                cmd = sub.get("command", "") or ""
                yield event, matcher, cmd


def validate_config(
    source_label: str,
    role_name: str,
    config: dict | None,
    role_schema: dict,
    global_schema: dict,
) -> list[Finding]:
    findings: list[Finding] = []
    if config is None:
        findings.append(
            Finding(source_label, role_name, "ERROR", "config block missing")
        )
        return findings
    if "__parse_error__" in config:
        findings.append(
            Finding(
                source_label,
                role_name,
                "ERROR",
                f"JSON parse error: {config['__parse_error__']}",
            )
        )
        return findings

    allow = _get_allow(config)
    deny = _get_deny(config)

    # Global forbidden exact
    for entry in allow:
        if entry in global_schema.get("forbidden_allow_exact", []):
            findings.append(
                Finding(
                    source_label,
                    role_name,
                    "ERROR",
                    f"forbidden wide allow entry: {entry!r}",
                )
            )
    # Global forbidden regex
    for pattern in global_schema.get("forbidden_allow_regex", []):
        rgx = re.compile(pattern)
        for entry in allow:
            if rgx.search(entry):
                findings.append(
                    Finding(
                        source_label,
                        role_name,
                        "ERROR",
                        f"forbidden allow entry {entry!r} matches /{pattern}/",
                    )
                )

    # Per-role disallow regex
    for pattern in role_schema.get("disallow_allow_regex", []):
        rgx = re.compile(pattern)
        for entry in allow:
            if rgx.search(entry):
                findings.append(
                    Finding(
                        source_label,
                        role_name,
                        "ERROR",
                        f"role contract violation: {entry!r} matches /{pattern}/",
                    )
                )

    # Required allow
    allow_set = set(allow)
    required_allow = role_schema.get("required_allow", [])
    for req in required_allow:
        if req not in allow_set:
            findings.append(
                Finding(
                    source_label,
                    role_name,
                    "ERROR",
                    f"missing required allow: {req!r}",
                )
            )

    # Closed-world check: any allow entry must be in required_allow set or
    # match one of ``allowed_allow_regex``. Catches unknown entries sneaking
    # into docs / settings without a matching schema update.
    if role_schema.get("closed_world"):
        required_set = set(required_allow)
        extra_patterns = [
            re.compile(p) for p in role_schema.get("allowed_allow_regex", [])
        ]
        for entry in allow:
            if entry in required_set:
                continue
            if any(p.search(entry) for p in extra_patterns):
                continue
            findings.append(
                Finding(
                    source_label,
                    role_name,
                    "ERROR",
                    (
                        f"unknown allow entry {entry!r} — not in schema's "
                        "required_allow nor allowed_allow_regex; add to schema "
                        "(with justification) or remove."
                    ),
                )
            )

    # Required deny
    deny_set = set(deny)
    for req in role_schema.get("required_deny", []):
        if req not in deny_set:
            findings.append(
                Finding(
                    source_label,
                    role_name,
                    "ERROR",
                    f"missing required deny: {req!r}",
                )
            )

    # Required hooks
    hook_tuples = list(_iter_hooks(config))
    for req in role_schema.get("required_hooks", []):
        ev = req["event"]
        match_sub = req.get("matcher_contains", "")
        cmd_sub = req.get("command_contains", "")
        hit = any(
            event == ev and match_sub in matcher and cmd_sub in cmd
            for event, matcher, cmd in hook_tuples
        )
        if not hit:
            findings.append(
                Finding(
                    source_label,
                    role_name,
                    "ERROR",
                    (
                        "missing required hook: "
                        f"event={ev} matcher~={match_sub!r} command~={cmd_sub!r}"
                    ),
                )
            )

    return findings


def validate_schema_integrity(schema: dict) -> list[Finding]:
    findings: list[Finding] = []
    required_scripts = set(schema.get("required_hook_scripts", []))
    seen: set[str] = set()
    for role_name, role in schema.get("roles", {}).items():
        for hook in role.get("required_hooks", []):
            cmd = hook.get("command_contains", "")
            if cmd.endswith(".sh"):
                seen.add(cmd)
    missing = required_scripts - seen
    for script in sorted(missing):
        findings.append(
            Finding(
                "schema",
                "<global>",
                "ERROR",
                f"required hook script {script!r} not referenced by any role",
            )
        )
    return findings


def check_docs(
    schema: dict,
    permissions_md: Path,
) -> list[Finding]:
    if not permissions_md.is_file():
        return [
            Finding(
                str(permissions_md),
                "<docs>",
                "ERROR",
                "permissions.md not found",
            )
        ]
    text = permissions_md.read_text(encoding="utf-8")
    blocks = extract_role_blocks(text, schema["roles"])
    findings: list[Finding] = []
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


def check_on_disk(
    schema: dict, root: Path, include_untracked: bool = False
) -> list[Finding]:
    findings: list[Finding] = []
    for role_name, role_schema in schema["roles"].items():
        for rel in role_schema.get("settings_paths", []):
            path = root / rel
            if not path.is_file():
                continue
            if not _is_git_tracked(path, root) and not include_untracked:
                # Gitignored / untracked local configs vary per developer.
                # Default: only validate tracked files so CI and local runs
                # see the same picture. --include-local opts into validating
                # the current worktree's role configs as well.
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
                )
            )
    return findings


def run(
    schema_path: Path = DEFAULT_SCHEMA,
    permissions_md: Path = DEFAULT_PERMISSIONS_MD,
    root: Path = REPO_ROOT,
    include_on_disk: bool = True,
    include_untracked: bool = False,
) -> list[Finding]:
    schema = load_schema(schema_path)
    findings: list[Finding] = []
    findings.extend(validate_schema_integrity(schema))
    findings.extend(check_docs(schema, permissions_md))
    if include_on_disk:
        findings.extend(
            check_on_disk(schema, root, include_untracked=include_untracked)
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate per-role settings.local.json against the schema."
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--permissions-md", type=Path, default=DEFAULT_PERMISSIONS_MD
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--docs-only",
        action="store_true",
        help="Skip on-disk settings.local.json validation (default in CI).",
    )
    parser.add_argument(
        "--include-local",
        action="store_true",
        help=(
            "Also validate gitignored / untracked on-disk settings.local.json "
            "files in the current worktree. Off by default because role "
            "settings.local.json files are gitignored and their content "
            "varies per developer / worktree; turn on to audit the current "
            "machine's configs."
        ),
    )
    args = parser.parse_args(argv)

    findings = run(
        schema_path=args.schema,
        permissions_md=args.permissions_md,
        root=args.root,
        include_on_disk=not args.docs_only,
        include_untracked=args.include_local,
    )

    if not findings:
        print("role_configs: OK")
        return 0

    for f in findings:
        print(f.format())
    errors = sum(1 for f in findings if f.severity == "ERROR")
    print(f"role_configs: {errors} error(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
