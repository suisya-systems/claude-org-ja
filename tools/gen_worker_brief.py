"""Generate a worker CLAUDE.md / CLAUDE.local.md brief from a TOML config.

Replaces hand-written per-task briefs with template + variable substitution.
See ``tools/templates/worker_brief.example.toml`` for the input schema.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from string import Template
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


TEMPLATES_DIR = Path(__file__).parent / "templates"

VALID_PATTERNS = {"A", "B", "C"}
VALID_ROLES = {"default", "claude-org-self-edit", "doc-audit"}
VALID_DEPTHS = {"full", "minimal"}

REQUIRED_STRING_KEYS = {
    "task": ("id", "description", "verification_depth", "branch", "commit_prefix"),
    "worker": ("dir", "pattern", "role"),
    "project": ("name", "description"),
    "paths": ("claude_org",),
}

_BLOCK_RE = re.compile(
    r"<!--BEGIN:(?P<name>[a-z_]+)-->(?P<body>.*?)<!--END:(?P=name)-->\n?",
    re.DOTALL,
)


class ConfigError(ValueError):
    """Raised when the TOML config is invalid."""


def _require_string(d: dict[str, Any], section: str, keys: tuple[str, ...]) -> None:
    if not isinstance(d.get(section), dict):
        raise ConfigError(f"missing or non-table required section [{section}]")
    for k in keys:
        if k not in d[section]:
            raise ConfigError(f"missing required key {section}.{k}")
        if not isinstance(d[section][k], str) or not d[section][k]:
            raise ConfigError(f"{section}.{k} must be a non-empty string")


def _require_string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{label} must be a list of strings")


def validate(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ConfigError("config root must be a TOML table")

    for section, keys in REQUIRED_STRING_KEYS.items():
        _require_string(config, section, keys)

    depth = config["task"]["verification_depth"]
    if depth not in VALID_DEPTHS:
        raise ConfigError(f"task.verification_depth must be one of {sorted(VALID_DEPTHS)}, got {depth!r}")

    pattern = config["worker"]["pattern"]
    if pattern not in VALID_PATTERNS:
        raise ConfigError(f"worker.pattern must be one of {sorted(VALID_PATTERNS)}, got {pattern!r}")

    role = config["worker"]["role"]
    if role not in VALID_ROLES:
        raise ConfigError(f"worker.role must be one of {sorted(VALID_ROLES)}, got {role!r}")

    if "self_edit" not in config["worker"]:
        raise ConfigError("missing required key worker.self_edit")
    if not isinstance(config["worker"]["self_edit"], bool):
        raise ConfigError("worker.self_edit must be a boolean")

    task = config["task"]
    if "issue_url" in task and not isinstance(task["issue_url"], str):
        raise ConfigError("task.issue_url must be a string")
    if "closes_issue" in task:
        v = task["closes_issue"]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ConfigError("task.closes_issue must be an integer")
    if "refs_issues" in task:
        v = task["refs_issues"]
        if not isinstance(v, list) or not all(
            isinstance(n, int) and not isinstance(n, bool) for n in v
        ):
            raise ConfigError("task.refs_issues must be a list of integers")

    impl = config.get("implementation")
    if impl is not None:
        if not isinstance(impl, dict):
            raise ConfigError("[implementation] must be a TOML table")
        if "target_files" in impl:
            _require_string_list(impl["target_files"], "implementation.target_files")
        if "guidance" in impl and not isinstance(impl["guidance"], str):
            raise ConfigError("implementation.guidance must be a string")

    refs = config.get("references")
    if refs is not None:
        if not isinstance(refs, dict):
            raise ConfigError("[references] must be a TOML table")
        if "knowledge" in refs:
            _require_string_list(refs["knowledge"], "references.knowledge")

    parallel = config.get("parallel")
    if parallel is not None:
        if not isinstance(parallel, dict):
            raise ConfigError("[parallel] must be a TOML table")
        if "notes" in parallel and not isinstance(parallel["notes"], str):
            raise ConfigError("parallel.notes must be a string")


def _closes_or_refs(task: dict[str, Any]) -> str:
    closes = task.get("closes_issue")
    refs = task.get("refs_issues")
    if closes:
        return f"Closes #{closes}"
    if refs:
        nums = " ".join(f"#{n}" for n in refs)
        return f"Refs {nums}"
    return "（なし）"


def _impl_target_files_block(files: list[str]) -> str:
    if not files:
        return ""
    lines = ["対象ファイル:"]
    for f in files:
        lines.append(f"- `{f}`")
    return "\n".join(lines) + "\n\n"


def _impl_guidance_block(guidance: str | None) -> str:
    if not guidance:
        return ""
    return guidance.strip() + "\n"


def _references_knowledge_block(items: list[str]) -> str:
    if not items:
        return ""
    return "\n".join(f"- `{p}`" for p in items)


def _select_blocks(config: dict[str, Any]) -> dict[str, bool]:
    """Decide which optional <!--BEGIN:name--> blocks to keep."""
    task = config["task"]
    depth = task["verification_depth"]
    blocks = {
        "issue_url": bool(task.get("issue_url")),
        "implementation": "implementation" in config
        and (
            config["implementation"].get("target_files")
            or config["implementation"].get("guidance")
        ),
        "parallel": "parallel" in config and bool(config["parallel"].get("notes")),
        "references": "references" in config
        and bool(config["references"].get("knowledge")),
        "codex_full": depth == "full",
        "codex_minimal": depth == "minimal",
    }
    return blocks


def _apply_blocks(text: str, keep: dict[str, bool]) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group("name")
        body = m.group("body")
        if keep.get(name, False):
            return body
        return ""

    return _BLOCK_RE.sub(repl, text)


def _build_substitutions(config: dict[str, Any]) -> dict[str, str]:
    task = config["task"]
    worker = config["worker"]
    project = config["project"]
    paths = config["paths"]

    impl = config.get("implementation", {})
    refs = config.get("references", {})
    parallel = config.get("parallel", {})

    return {
        "worker_dir": worker["dir"],
        "worker_pattern": worker["pattern"],
        "worker_role": worker["role"],
        "claude_org_path": paths["claude_org"],
        "project_name": project["name"],
        "project_description": project["description"],
        "task_id": task["id"],
        "task_description": task["description"].strip(),
        "task_branch": task["branch"],
        "task_verification_depth": task["verification_depth"],
        "task_commit_prefix": task["commit_prefix"],
        "task_issue_url": task.get("issue_url", ""),
        "closes_or_refs": _closes_or_refs(task),
        "implementation_target_files_block": _impl_target_files_block(
            impl.get("target_files", []) or []
        ),
        "implementation_guidance_block": _impl_guidance_block(impl.get("guidance")),
        "references_knowledge_block": _references_knowledge_block(
            refs.get("knowledge", []) or []
        ),
        "parallel_notes": (parallel.get("notes") or "").strip(),
    }


def render(config: dict[str, Any]) -> str:
    validate(config)
    self_edit = config["worker"]["self_edit"]
    template_name = (
        "worker_brief_self_edit.md" if self_edit else "worker_brief_normal.md"
    )
    raw = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    keep = _select_blocks(config)
    raw = _apply_blocks(raw, keep)
    subs = _build_substitutions(config)
    rendered = Template(raw).safe_substitute(subs)
    # collapse 3+ blank lines to a single blank line
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate a worker CLAUDE.md / CLAUDE.local.md from a TOML config",
    )
    p.add_argument("--config", required=True, type=Path, help="path to TOML config")
    p.add_argument(
        "--out",
        required=True,
        type=Path,
        help="output path (typically CLAUDE.md or CLAUDE.local.md)",
    )
    args = p.parse_args(argv)

    try:
        config = load_config(args.config)
        output = render(config)
    except (ConfigError, FileNotFoundError, tomllib.TOMLDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(output, encoding="utf-8")
    print(f"wrote {args.out} ({len(output)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
