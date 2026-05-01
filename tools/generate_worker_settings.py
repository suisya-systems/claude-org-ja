#!/usr/bin/env python3
"""Schema-driven worker ``.claude/settings.local.json`` generator
(Step B shim).

The substitution engine now lives in ``core_harness.generator``. This
module is a thin CLI shim that:

* Loads ``tools/org_extension_schema.json`` (renamed from
  ``role_configs_schema.json``) and merges it with the framework JSON
  Schema (``tools/framework_schema.json``).
* Re-exports :func:`render_role` with the historical kwarg signature
  (``schema, role, worker_dir, claude_org_path``) so existing callers
  — including ``tests/test_generate_worker_settings.py`` — continue to
  work unchanged.
* Forwards the ja-specific ``{claude_org_path}`` placeholder to the
  org-neutral ``core_harness.generator.render_role`` engine through
  its ``**placeholders`` kwargs interface.

CLI compatibility (``--role / --worker-dir / --claude-org-path /
--out / --schema``) is preserved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core_harness.generator import (
    UnresolvedPlaceholderError,
    render_role as _core_render_role,
)
from core_harness.schema import load_framework_schema, merge_schemas

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPO_ROOT / "tools" / "org_extension_schema.json"
DEFAULT_FRAMEWORK_SCHEMA = REPO_ROOT / "tools" / "framework_schema.json"

__all__ = ["render_role", "load_schema", "main"]


def load_schema(path: Path) -> dict:
    """Load the org-extension data and return the merged framework +
    extension dict. Mirrors :func:`check_role_configs.load_schema`:
    the pinned core-harness package is the source of truth; the local
    ``tools/framework_schema.json`` is a fallback only."""
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


def render_role(
    schema: dict,
    role: str,
    worker_dir: str,
    claude_org_path: str,
) -> dict:
    """Render the worker settings for ``role`` from ``schema``.

    Forwards to ``core_harness.generator.render_role`` while
    preserving the historical ja kwarg names. ``{worker_dir}`` and
    ``{claude_org_path}`` are passed through as placeholder
    substitutions; the core engine fails closed if any placeholder is
    left unresolved.
    """
    return _core_render_role(
        schema,
        role,
        worker_dir=worker_dir,
        claude_org_path=claude_org_path,
    )


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate <worker_dir>/.claude/settings.local.json from "
            "the org extension schema's worker_roles[<role>]."
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
    except UnresolvedPlaceholderError as exc:
        print(f"error: {exc}", file=sys.stderr)
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
