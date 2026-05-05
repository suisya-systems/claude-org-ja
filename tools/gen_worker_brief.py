"""Generate a worker CLAUDE.md / CLAUDE.local.md brief from a TOML config.

Replaces hand-written per-task briefs with template + variable substitution.
See ``tools/templates/worker_brief.example.toml`` for the input schema.

Two CLI shapes are supported:

1. Legacy (preserved for callers that build TOML by hand):

       python tools/gen_worker_brief.py --config <path>.toml --out <CLAUDE.md>

2. ``from-task`` subcommand (Issue #283 Stage 2). Calls
   :mod:`tools.resolve_worker_layout` internally, builds the TOML config,
   and renders. Inferred fields (`worker.dir`, `worker.pattern`, role,
   branch) are derived from registry/projects.md + state.db, so the caller
   only supplies task-shape inputs:

       python tools/gen_worker_brief.py from-task \\
           --task-id ... --project-slug ... --target ... --out <CLAUDE.md>

The legacy mode and ``from-task`` mode share the rendering pipeline
(`render(config) -> str`). Behaviour and TOML schema are unchanged for
existing callers; from-task assembles an equivalent config dict in memory.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from string import Template
from typing import Any, Optional

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


# ---------------------------------------------------------------------------
# from-task: build a config dict from registry + state.db and render
# ---------------------------------------------------------------------------


def _commit_prefix_from_branch(branch: Optional[str], project_slug: str) -> str:
    """Default the commit prefix to ``feat(<scope>):`` / ``fix(<scope>):``.

    Scope defaults to ``project_slug`` truncated at the first hyphen
    (e.g. ``claude-org-ja`` → ``claude``). Callers should override with
    ``--commit-prefix`` when convention differs (the org-delegate skill
    historically uses ``feat(secretary):`` / ``feat(dispatcher):`` /
    ``docs(secretary):`` rather than the project-derived scope).
    """
    scope = project_slug.split("-", 1)[0] if project_slug else "task"
    kind = "fix" if branch and branch.startswith("fix/") else "feat"
    return f"{kind}({scope}):"


def _resolve_out_path(out: Path, self_edit: bool) -> Path:
    """Auto-switch CLAUDE.md → CLAUDE.local.md when self_edit is true.

    The Codex review flagged that self_edit means the worker is inside a
    repo whose root CLAUDE.md belongs to someone else (claude-org's
    Secretary brief, or the gitignored sub-mode's host-repo CLAUDE.md);
    overwriting it is destructive. ``from-task`` therefore enforces the
    suffix automatically and warns when the caller's --out disagrees.
    """
    if not self_edit:
        return out
    if out.name == "CLAUDE.md":
        return out.with_name("CLAUDE.local.md")
    return out


def build_config_from_task(
    *,
    task_id: str,
    project_slug: str,
    targets: Optional[list[str]] = None,
    description: str = "",
    mode: str = "edit",
    branch_override: Optional[str] = None,
    commit_prefix: Optional[str] = None,
    verification_depth: str = "full",
    issue_url: Optional[str] = None,
    closes_issue: Optional[int] = None,
    refs_issues: Optional[list[int]] = None,
    project_name_override: Optional[str] = None,
    project_description_override: Optional[str] = None,
    implementation_target_files: Optional[list[str]] = None,
    implementation_guidance: Optional[str] = None,
    references_knowledge: Optional[list[str]] = None,
    parallel_notes: Optional[str] = None,
    registry_path: Optional[Path] = None,
    state_db_path: Optional[Path] = None,
    claude_org_root: Path,
    workers_dir: Optional[Path] = None,
) -> tuple[dict[str, Any], "object"]:
    """Resolve layout + assemble a render-ready config.

    Returns ``(config, layout)``. ``layout`` is the
    :class:`tools.resolve_worker_layout.WorkerLayout` so callers (notably
    :mod:`tools.gen_delegate_payload`) can consume the same resolver
    output without re-running the resolver.
    """
    # Imported lazily so legacy --config callers don't pay the import cost
    # and don't pull in sqlite3 unless they need it.
    from tools import resolve_worker_layout as rwl

    layout = rwl.resolve(
        task_id=task_id,
        project_slug=project_slug,
        targets=targets,
        description=description,
        mode=mode,
        branch_override=branch_override,
        registry_path=registry_path,
        state_db_path=state_db_path,
        claude_org_root=claude_org_root,
        workers_dir=workers_dir,
    )

    # gitignored_repo_root inherits the .local.md template treatment even
    # when role=default — we're inside someone else's repo and must not
    # clobber their CLAUDE.md (Codex M-1).
    treat_as_self_edit = layout.self_edit or (
        layout.pattern_variant == "gitignored_repo_root"
    )

    # Look up project metadata from the registry for project.name /
    # project.description defaults. Stage 2 doesn't add a registry-read
    # round-trip — the resolver already parsed it; we re-read here only
    # for the description string (resolver returns slug + path only on the
    # public dataclass, not the full row).
    registry_for_meta = registry_path or (
        claude_org_root / "registry" / "projects.md"
    )
    project_name = project_name_override or project_slug
    project_description = project_description_override or ""
    if registry_for_meta.exists():
        rows = rwl.parse_registry(
            registry_for_meta.read_text(encoding="utf-8")
        )
        match = rwl.find_project(rows, project_slug)
        if match is not None:
            if not project_name_override:
                project_name = match.common_name or match.slug
            if not project_description_override:
                project_description = match.description

    # Branch field for the brief: Pattern C has no branch; use task_id as
    # a stable label so the existing template (which requires task.branch
    # to be a non-empty string) doesn't reject the config. Stage 3 will
    # mark this as null in the DELEGATE body.
    brief_branch = layout.planned_branch or task_id

    if commit_prefix is None:
        commit_prefix = _commit_prefix_from_branch(layout.planned_branch, project_slug)

    config: dict[str, Any] = {
        "task": {
            "id": task_id,
            "description": description or task_id,
            "verification_depth": verification_depth,
            "branch": brief_branch,
            "commit_prefix": commit_prefix,
        },
        "worker": {
            "dir": layout.worker_dir,
            "pattern": layout.pattern,
            "role": layout.role,
            "self_edit": treat_as_self_edit,
        },
        "project": {
            "name": project_name or project_slug,
            "description": project_description or f"Project {project_slug}",
        },
        "paths": {
            "claude_org": str(Path(claude_org_root).resolve()),
        },
    }
    if issue_url:
        config["task"]["issue_url"] = issue_url
    if closes_issue is not None:
        config["task"]["closes_issue"] = int(closes_issue)
    if refs_issues:
        config["task"]["refs_issues"] = [int(n) for n in refs_issues]

    if implementation_target_files or implementation_guidance:
        impl: dict[str, Any] = {}
        if implementation_target_files:
            impl["target_files"] = list(implementation_target_files)
        if implementation_guidance:
            impl["guidance"] = implementation_guidance
        config["implementation"] = impl
    if references_knowledge:
        config["references"] = {"knowledge": list(references_knowledge)}
    if parallel_notes:
        config["parallel"] = {"notes": parallel_notes}

    return config, layout


def _dump_toml(config: dict[str, Any]) -> str:
    """Minimal TOML dumper for the audit-trail file written by ``--write-toml``.

    We avoid taking a runtime dependency on ``tomli-w`` for a feature
    most callers won't use; the subset of TOML the brief schema produces
    (tables of strings, ints, bool, lists of strings/ints) is small
    enough to handle inline.
    """
    def _atom(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            if "\n" in v:
                escaped = v.replace("\\", "\\\\").replace('"""', '\\"""')
                return f'"""\n{escaped}\n"""'
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        if isinstance(v, list):
            return "[" + ", ".join(_atom(x) for x in v) + "]"
        raise TypeError(f"unsupported TOML value type: {type(v).__name__}")

    lines: list[str] = []
    for section, body in config.items():
        if not isinstance(body, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in body.items():
            lines.append(f"{k} = {_atom(v)}")
        lines.append("")
    return "\n".join(lines)


def _build_from_task_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gen_worker_brief.py from-task",
        description="Resolve worker layout from registry+state.db and render a brief.",
    )
    p.add_argument("--task-id", required=True)
    p.add_argument("--project-slug", required=True)
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--description", default="")
    p.add_argument("--mode", choices=("edit", "audit"), default="edit")
    p.add_argument("--branch", dest="branch_override", default=None)
    p.add_argument("--commit-prefix", default=None)
    p.add_argument(
        "--verification-depth",
        choices=("full", "minimal"),
        default="full",
    )
    p.add_argument("--issue-url", default=None)
    p.add_argument("--closes-issue", type=int, default=None)
    p.add_argument("--refs-issues", type=int, nargs="*", default=None)
    p.add_argument("--project-name", dest="project_name_override", default=None)
    p.add_argument(
        "--project-description",
        dest="project_description_override",
        default=None,
    )
    p.add_argument("--impl-target", action="append", default=[],
                   help="Add an entry to [implementation].target_files (repeatable).")
    p.add_argument("--impl-guidance", default=None)
    p.add_argument("--knowledge", action="append", default=[],
                   help="Add an entry to [references].knowledge (repeatable).")
    p.add_argument("--parallel-notes", default=None)
    p.add_argument("--registry-path", type=Path, default=None)
    p.add_argument("--state-db-path", type=Path, default=None)
    p.add_argument("--claude-org-root", type=Path, default=None)
    p.add_argument("--workers-dir", type=Path, default=None)
    p.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output path (auto-switched to .local.md when self_edit).",
    )
    p.add_argument(
        "--write-toml",
        type=Path,
        default=None,
        help="Optional: also dump the assembled TOML config to this path (audit trail).",
    )
    return p


def _detect_claude_org_root() -> Path:
    """Walk up from CWD looking for a registry/projects.md + .state/ pair."""
    here = Path.cwd().resolve()
    for cand in (here, *here.parents):
        if (cand / "registry" / "projects.md").exists() and (cand / ".state").exists():
            return cand
    return here


def _main_from_task(argv: list[str]) -> int:
    args = _build_from_task_parser().parse_args(argv)
    claude_org_root = args.claude_org_root or _detect_claude_org_root()
    state_db_path = args.state_db_path
    if state_db_path is None:
        candidate = claude_org_root / ".state" / "state.db"
        state_db_path = candidate if candidate.exists() else None

    try:
        config, layout = build_config_from_task(
            task_id=args.task_id,
            project_slug=args.project_slug,
            targets=args.target,
            description=args.description,
            mode=args.mode,
            branch_override=args.branch_override,
            commit_prefix=args.commit_prefix,
            verification_depth=args.verification_depth,
            issue_url=args.issue_url,
            closes_issue=args.closes_issue,
            refs_issues=args.refs_issues,
            project_name_override=args.project_name_override,
            project_description_override=args.project_description_override,
            implementation_target_files=args.impl_target,
            implementation_guidance=args.impl_guidance,
            references_knowledge=args.knowledge,
            parallel_notes=args.parallel_notes,
            registry_path=args.registry_path,
            state_db_path=state_db_path,
            claude_org_root=claude_org_root,
            workers_dir=args.workers_dir,
        )
        output = render(config)
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    out_path = _resolve_out_path(args.out, config["worker"]["self_edit"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(f"wrote {out_path} ({len(output)} bytes)")

    if args.write_toml is not None:
        args.write_toml.parent.mkdir(parents=True, exist_ok=True)
        args.write_toml.write_text(_dump_toml(config), encoding="utf-8")
        print(f"wrote {args.write_toml} (TOML audit trail)")

    # Report layout fields the brief itself doesn't expose — useful for
    # Stage 3 callers and for human inspection.
    print(
        f"layout: pattern={layout.pattern}"
        + (f"/{layout.pattern_variant}" if layout.pattern_variant else "")
        + f" role={layout.role} planned_branch={layout.planned_branch!r}"
    )
    return 0


# ---------------------------------------------------------------------------
# Legacy --config / --out CLI (preserved for backwards compat)
# ---------------------------------------------------------------------------


def _main_legacy(argv: list[str]) -> int:
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


def main(argv: list[str] | None = None) -> int:
    """Dispatch ``from-task <…>`` to the new path; everything else is legacy.

    Subcommand-style argparse would also work, but staying with a leading
    sentinel keeps the legacy ``--config/--out`` invocation byte-identical
    and prevents argparse's automatic ``--help`` from changing output."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "from-task":
        return _main_from_task(argv[1:])
    return _main_legacy(argv)


if __name__ == "__main__":
    raise SystemExit(main())
