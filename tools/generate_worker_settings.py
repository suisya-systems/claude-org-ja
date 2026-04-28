#!/usr/bin/env python3
"""Schema-driven worker ``.claude/settings.local.json`` generator.

Reads ``tools/role_configs_schema.json`` -> ``worker_roles[<role>]``,
substitutes ``{worker_dir}`` and ``{claude_org_path}`` placeholders, and
prints the resulting JSON. Used by org-delegate Step 3 (Phase 2 migration)
and by the drift checker's ``--include-worker-settings`` mode to derive
the expected template for an on-disk worker config. See Issue #99.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPO_ROOT / "tools" / "role_configs_schema.json"

# Keys under worker_roles[<role>] that are metadata, not part of the emitted
# settings.local.json content.
_META_KEYS = {"description", "$comment"}


def load_schema(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _substitute(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        out = value
        for placeholder, replacement in mapping.items():
            out = out.replace("{" + placeholder + "}", replacement)
        return out
    if isinstance(value, list):
        return [_substitute(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, mapping) for k, v in value.items()}
    return value


def render_role(
    schema: dict,
    role: str,
    worker_dir: str,
    claude_org_path: str,
) -> dict:
    roles = schema.get("worker_roles") or {}
    if role not in roles:
        available = sorted(k for k in roles if not k.startswith("$"))
        raise KeyError(
            f"unknown worker role: {role!r}. available: {available}"
        )
    template = {
        k: v for k, v in roles[role].items() if k not in _META_KEYS
    }
    return _substitute(
        template,
        {"worker_dir": worker_dir, "claude_org_path": claude_org_path},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate <worker_dir>/.claude/settings.local.json from "
            "role_configs_schema.json -> worker_roles[<role>]."
        ),
    )
    parser.add_argument(
        "--role",
        required=True,
        help="worker role name (e.g. default, claude-org-self-edit, doc-audit)",
    )
    parser.add_argument(
        "--worker-dir",
        required=True,
        help="absolute path that {worker_dir} resolves to",
    )
    parser.add_argument(
        "--claude-org-path",
        required=True,
        help="absolute path to the claude-org repo (for hook script paths)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output file (default: stdout)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help=f"schema path (default: {DEFAULT_SCHEMA})",
    )
    args = parser.parse_args(argv)

    try:
        schema = load_schema(args.schema)
    except FileNotFoundError as exc:
        print(f"error: schema not found: {exc.filename}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: schema is not valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        rendered = render_role(
            schema,
            role=args.role,
            worker_dir=args.worker_dir,
            claude_org_path=args.claude_org_path,
        )
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2

    text = json.dumps(rendered, indent=2, ensure_ascii=False) + "\n"
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
