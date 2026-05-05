"""Generate the Secretary's DELEGATE payload for a worker dispatch.

Issue #283 Stage 3. Top-level CLI that takes the same task-shape inputs
:mod:`tools.gen_worker_brief` ``from-task`` accepts, then assembles the
full delegation packet:

- ``preview`` (default): pure planning. Emits the DELEGATE message body
  to stdout plus a list of artifacts that *would* be created. **Touches
  no files, no DB, no MCP.** This lets Secretary inspect the plan before
  committing to a reservation.

- ``apply``: performs the T1 reservation per Set B contract:

  1. Reserve the worker_dir + run row in state.db with
     ``runs.status='queued'`` (Codex Design Blocker B-1; ``Active Work
     Items`` remains the dispatcher's T2 responsibility — see
     ``docs/contracts/delegation-lifecycle-contract.md``).
  2. Write CLAUDE.md / CLAUDE.local.md via :mod:`tools.gen_worker_brief`.
  3. Optionally call ``claude-org-runtime settings generate`` to write
     ``.claude/settings.local.json`` (skip with ``--skip-settings``;
     useful when the runtime CLI isn't on PATH yet, e.g. in tests).
  4. Emit ``send_plan.json`` describing the renga-peers send_message
     call Secretary should issue (``to_id="dispatcher"``, ``message=<body>``).
     Codex M-3 reframed the original ``--send`` flag: this script never
     calls MCP itself; Secretary copies the JSON into their own
     ``mcp__renga-peers__send_message`` call.

The internal split is :func:`build_delegate_plan` (pure planner returning
a :class:`DelegatePlan`) and :func:`apply_delegate_plan` (side-effect
executor). Tests exercise both paths.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from tools import gen_worker_brief as gwb
from tools import resolve_worker_layout as rwl


_PERMISSION_MODE_RE = re.compile(
    r"^\s*default_permission_mode\s*:\s*(\S+)\s*$", re.MULTILINE
)

_PATTERN_LABELS = {
    "A": "A: プロジェクトディレクトリ",
    "B": "B: worktree",
    "C": "C: エフェメラル",
}


@dataclass(frozen=True)
class DelegatePlan:
    task_id: str
    project_slug: str
    description: str
    config: dict[str, Any]
    layout: rwl.WorkerLayout
    delegate_body: str
    brief_out_path: Path
    settings_args: dict[str, str]
    permission_mode: str
    verification_depth: str
    closes_issue: Optional[int]
    refs_issues: list[int]
    artifacts_to_create: list[Path] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_slug": self.project_slug,
            "pattern": self.layout.pattern,
            "pattern_variant": self.layout.pattern_variant,
            "role": self.layout.role,
            "self_edit": self.layout.self_edit,
            "worker_dir": self.layout.worker_dir,
            "planned_branch": self.layout.planned_branch,
            "permission_mode": self.permission_mode,
            "verification_depth": self.verification_depth,
            "brief_out_path": str(self.brief_out_path),
            "settings_args": dict(self.settings_args),
            "artifacts_to_create": [str(p) for p in self.artifacts_to_create],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_permission_mode(claude_org_root: Path) -> str:
    """Return the org-wide ``default_permission_mode`` (defaults to ``auto``)."""
    cfg = claude_org_root / "registry" / "org-config.md"
    if not cfg.exists():
        return "auto"
    text = cfg.read_text(encoding="utf-8")
    m = _PERMISSION_MODE_RE.search(text)
    return m.group(1) if m else "auto"


def _pattern_label(layout: rwl.WorkerLayout) -> str:
    base = _PATTERN_LABELS.get(layout.pattern, layout.pattern)
    if layout.pattern_variant == "gitignored_repo_root":
        return "C: gitignored サブモード (registered repo 直接編集)"
    return base


def _project_label(layout: rwl.WorkerLayout, project_path: Optional[str]) -> str:
    if layout.pattern == "A":
        return f"clone or reuse: {project_path or '-'}"
    if layout.pattern == "B":
        return f"worktree base: {project_path or '-'}"
    if layout.pattern == "C":
        if layout.pattern_variant == "gitignored_repo_root":
            return f"既存 repo 直接編集: {layout.worker_dir}"
        return "新規作成 (clone なし) — エフェメラル"
    return project_path or "-"


def _summarize_description(description: str, limit: int = 120) -> str:
    one_line = " ".join(description.strip().split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rstrip() + "…"


def _brief_filename(self_edit: bool) -> str:
    return "CLAUDE.local.md" if self_edit else "CLAUDE.md"


def _format_delegate_body(
    *,
    layout: rwl.WorkerLayout,
    task_id: str,
    description: str,
    project_path: Optional[str],
    permission_mode: str,
    verification_depth: str,
    brief_filename: str,
) -> str:
    """Format the DELEGATE message body per org-delegate Step 2 template."""
    instr_summary = _summarize_description(description)
    branch_line = (
        layout.planned_branch
        if layout.planned_branch
        else "(Pattern C: 既存 repo の現在ブランチで作業 / 新規 branch なし)"
    )
    body = f"""DELEGATE: 以下のワーカーを派遣してください。

タスク一覧:
- {task_id}: {instr_summary or task_id}
  - ワーカーディレクトリ: {layout.worker_dir}（{brief_filename}・設定配置済み）
  - ディレクトリパターン: {_pattern_label(layout)}
  - プロジェクト: {_project_label(layout, project_path)}
  - ブランチ (planned): {branch_line}
  - Permission Mode: {permission_mode}
  - 検証深度: {verification_depth}
  - 指示内容: 詳細は `{layout.worker_dir}/{brief_filename}` を参照。要約: {instr_summary or '(none)'}

窓口ペイン名: `secretary`（renga layout で登録済み）"""
    return body


# ---------------------------------------------------------------------------
# Planner — pure
# ---------------------------------------------------------------------------


def build_delegate_plan(
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
) -> DelegatePlan:
    """Resolve the layout, assemble the brief config, format the DELEGATE body.

    Pure: no file writes, no DB writes, no subprocesses other than the
    ``git check-ignore`` Step 0.7 probe (read-only) inside the resolver.
    """
    config, layout = gwb.build_config_from_task(
        task_id=task_id,
        project_slug=project_slug,
        targets=targets,
        description=description,
        mode=mode,
        branch_override=branch_override,
        commit_prefix=commit_prefix,
        verification_depth=verification_depth,
        issue_url=issue_url,
        closes_issue=closes_issue,
        refs_issues=refs_issues,
        project_name_override=project_name_override,
        project_description_override=project_description_override,
        implementation_target_files=implementation_target_files,
        implementation_guidance=implementation_guidance,
        references_knowledge=references_knowledge,
        parallel_notes=parallel_notes,
        registry_path=registry_path,
        state_db_path=state_db_path,
        claude_org_root=claude_org_root,
        workers_dir=workers_dir,
    )

    self_edit = bool(config["worker"]["self_edit"])
    brief_filename = _brief_filename(self_edit)
    brief_out_path = Path(layout.worker_dir) / brief_filename

    permission_mode = parse_permission_mode(Path(claude_org_root))

    # Look up project.path for the DELEGATE body label.
    project_path: Optional[str] = None
    registry_for_meta = registry_path or (
        Path(claude_org_root) / "registry" / "projects.md"
    )
    if registry_for_meta.exists():
        rows = rwl.parse_registry(registry_for_meta.read_text(encoding="utf-8"))
        match = rwl.find_project(rows, project_slug)
        if match is not None:
            project_path = match.path

    delegate_body = _format_delegate_body(
        layout=layout,
        task_id=task_id,
        description=description,
        project_path=project_path,
        permission_mode=permission_mode,
        verification_depth=verification_depth,
        brief_filename=brief_filename,
    )

    artifacts = [
        brief_out_path,
        Path(layout.worker_dir) / ".claude" / "settings.local.json",
    ]

    return DelegatePlan(
        task_id=task_id,
        project_slug=project_slug,
        description=description,
        config=config,
        layout=layout,
        delegate_body=delegate_body,
        brief_out_path=brief_out_path,
        settings_args=dict(layout.settings_args),
        permission_mode=permission_mode,
        verification_depth=verification_depth,
        closes_issue=closes_issue,
        refs_issues=list(refs_issues or []),
        artifacts_to_create=artifacts,
    )


# ---------------------------------------------------------------------------
# Executor — side effects (DB write, file write, settings subprocess)
# ---------------------------------------------------------------------------


@dataclass
class ApplyResult:
    plan: DelegatePlan
    brief_path: Path
    settings_path: Optional[Path]
    settings_skipped_reason: Optional[str]
    send_plan_path: Path
    db_reservation: dict[str, Any]


def _reserve_in_db(
    plan: DelegatePlan,
    *,
    state_db_path: Path,
    claude_org_root: Optional[Path],
) -> dict[str, Any]:
    """Reserve the worker_dir + queue the run row.

    Per Codex Design Blocker B-1 + Set B contract, this writes
    ``runs.status='queued'`` only. Active Work Items remains the
    dispatcher's T2 responsibility and is *not* touched here.
    """
    from tools.state_db import apply_schema, connect
    from tools.state_db.writer import StateWriter

    if not state_db_path.exists():
        # Fresh DB — create empty file and apply schema. The importer
        # would normally seed projects/workstreams; we only need a usable
        # schema here, ensure_project below will create the project row.
        state_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(state_db_path)
        apply_schema(conn)
        conn.close()

    conn = connect(state_db_path)
    try:
        writer = StateWriter(conn, claude_org_root=claude_org_root)
        with writer.transaction() as tx:
            tx.register_worker_dir(
                abs_path=plan.layout.worker_dir,
                layout="flat",
                is_git_repo=plan.layout.pattern != "C"
                or plan.layout.pattern_variant == "gitignored_repo_root",
                is_worktree=plan.layout.pattern == "B",
            )
            issue_refs_iter = None
            if plan.closes_issue is not None:
                issue_refs_iter = [str(plan.closes_issue)]
            elif plan.refs_issues:
                issue_refs_iter = [str(n) for n in plan.refs_issues]
            tx.upsert_run(
                task_id=plan.task_id,
                project_slug=plan.project_slug,
                pattern=plan.layout.pattern,
                title=plan.task_id,
                status="queued",
                branch=plan.layout.planned_branch,
                issue_refs=issue_refs_iter,
                verification=(
                    "minimal" if plan.verification_depth == "minimal" else "standard"
                ),
                worker_dir_abs_path=plan.layout.worker_dir,
            )
        return {
            "task_id": plan.task_id,
            "project_slug": plan.project_slug,
            "worker_dir": plan.layout.worker_dir,
            "status": "queued",
        }
    finally:
        conn.close()


def _write_brief(plan: DelegatePlan) -> Path:
    text = gwb.render(plan.config)
    out = plan.brief_out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def _run_settings_generate(
    plan: DelegatePlan,
    *,
    runtime_cmd: str = "claude-org-runtime",
) -> tuple[Optional[Path], Optional[str]]:
    """Run ``claude-org-runtime settings generate``.

    Returns ``(written_path, skipped_reason)``. When the CLI is missing
    we return ``(None, reason)`` and let the caller record it in the
    ApplyResult — apply does NOT crash, since several real-world dispatch
    flows happen on machines without the runtime installed (e.g. fresh
    test sandboxes).
    """
    if shutil.which(runtime_cmd) is None:
        return None, f"{runtime_cmd!r} not on PATH"

    settings_args = plan.settings_args
    out = Path(settings_args["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        runtime_cmd,
        "settings",
        "generate",
        "--role",
        settings_args["role"],
        "--worker-dir",
        settings_args["worker-dir"],
        "--claude-org-path",
        settings_args["claude-org-path"],
        "--out",
        settings_args["out"],
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return None, (
            f"{runtime_cmd} settings generate failed (rc={e.returncode}): "
            f"{(e.stderr or b'').decode('utf-8', errors='replace').strip()}"
        )
    return out, None


def _write_send_plan(plan: DelegatePlan, *, out_path: Path) -> Path:
    """Write the renga-peers MCP call manifest Secretary will copy from."""
    payload = {
        "to_id": "dispatcher",
        "message": plan.delegate_body,
        "summary": plan.to_summary_dict(),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def apply_delegate_plan(
    plan: DelegatePlan,
    *,
    state_db_path: Path,
    claude_org_root: Optional[Path] = None,
    skip_settings: bool = False,
    runtime_cmd: str = "claude-org-runtime",
    send_plan_out: Optional[Path] = None,
) -> ApplyResult:
    """Execute the side effects: reserve in DB, write brief, settings, send_plan."""
    db_reservation = _reserve_in_db(
        plan, state_db_path=state_db_path, claude_org_root=claude_org_root
    )
    brief_path = _write_brief(plan)
    settings_path: Optional[Path] = None
    skipped_reason: Optional[str] = None
    if skip_settings:
        skipped_reason = "skip_settings flag set"
    else:
        settings_path, skipped_reason = _run_settings_generate(
            plan, runtime_cmd=runtime_cmd
        )
    if send_plan_out is None:
        send_plan_out = brief_path.with_name("send_plan.json")
    send_plan_path = _write_send_plan(plan, out_path=send_plan_out)
    return ApplyResult(
        plan=plan,
        brief_path=brief_path,
        settings_path=settings_path,
        settings_skipped_reason=skipped_reason,
        send_plan_path=send_plan_path,
        db_reservation=db_reservation,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_task_args(p: argparse.ArgumentParser) -> None:
    """Args shared by preview and apply for the from-task input form."""
    p.add_argument("--task-id")
    p.add_argument("--project-slug")
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--description", default="")
    p.add_argument("--mode", choices=("edit", "audit"), default="edit")
    p.add_argument("--branch", dest="branch_override", default=None)
    p.add_argument("--commit-prefix", default=None)
    p.add_argument(
        "--verification-depth", choices=("full", "minimal"), default="full"
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
    p.add_argument("--impl-target", action="append", default=[])
    p.add_argument("--impl-guidance", default=None)
    p.add_argument("--knowledge", action="append", default=[])
    p.add_argument("--parallel-notes", default=None)
    p.add_argument("--registry-path", type=Path, default=None)
    p.add_argument("--state-db-path", type=Path, default=None)
    p.add_argument("--claude-org-root", type=Path, default=None)
    p.add_argument("--workers-dir", type=Path, default=None)
    p.add_argument(
        "--from-toml",
        type=Path,
        default=None,
        help="Load task arguments from a worker_brief-style TOML instead of CLI flags.",
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gen_delegate_payload",
        description="Generate the Secretary's DELEGATE payload (preview or apply).",
    )
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    preview = sub.add_parser(
        "preview",
        help="Print the planned DELEGATE body and artifact paths. No writes.",
    )
    _add_task_args(preview)
    preview.add_argument(
        "--json", dest="json_out", action="store_true",
        help="Emit a single JSON object instead of human-readable text.",
    )

    apply_p = sub.add_parser(
        "apply",
        help="Reserve in DB (runs.status='queued'), write brief, settings, send_plan.json.",
    )
    _add_task_args(apply_p)
    apply_p.add_argument(
        "--skip-settings", action="store_true",
        help="Skip the claude-org-runtime settings generate subprocess.",
    )
    apply_p.add_argument("--runtime-cmd", default="claude-org-runtime")
    apply_p.add_argument(
        "--send-plan-out",
        type=Path,
        default=None,
        help="Override send_plan.json output path (default: alongside the brief).",
    )

    return p


def _resolve_claude_org_root(args: argparse.Namespace) -> Path:
    if args.claude_org_root is not None:
        return args.claude_org_root
    return gwb._detect_claude_org_root()


def _resolve_state_db_path(args: argparse.Namespace, claude_org_root: Path) -> Path:
    if args.state_db_path is not None:
        return args.state_db_path
    return claude_org_root / ".state" / "state.db"


def _load_task_args_from_toml(path: Path) -> dict[str, Any]:
    """Pull task-shape kwargs out of a legacy worker_brief.toml.

    Returns the subset of build_delegate_plan kwargs derivable from the
    config; CLI flags still override (CLI processed after TOML).
    """
    with path.open("rb") as fh:
        cfg = tomllib.load(fh)
    task = cfg.get("task", {})
    worker = cfg.get("worker", {})
    project = cfg.get("project", {})
    impl = cfg.get("implementation", {})
    refs = cfg.get("references", {})
    parallel = cfg.get("parallel", {})
    return {
        "task_id": task.get("id"),
        "project_slug": project.get("name"),
        "description": task.get("description", ""),
        "branch_override": task.get("branch"),
        "commit_prefix": task.get("commit_prefix"),
        "verification_depth": task.get("verification_depth", "full"),
        "issue_url": task.get("issue_url"),
        "closes_issue": task.get("closes_issue"),
        "refs_issues": task.get("refs_issues"),
        "project_name_override": project.get("name"),
        "project_description_override": project.get("description"),
        "implementation_target_files": impl.get("target_files"),
        "implementation_guidance": impl.get("guidance"),
        "references_knowledge": refs.get("knowledge"),
        "parallel_notes": parallel.get("notes"),
        "mode": "edit" if not worker.get("self_edit") else "edit",
    }


def _gather_plan_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Merge --from-toml defaults with CLI flags (CLI wins)."""
    base: dict[str, Any] = {}
    if args.from_toml is not None:
        base.update(_load_task_args_from_toml(args.from_toml))
    # CLI overrides — only when caller actually provided a value.
    cli_overrides: dict[str, Any] = {
        "task_id": args.task_id,
        "project_slug": args.project_slug,
        "targets": args.target or None,
        "description": args.description or None,
        "mode": args.mode,
        "branch_override": args.branch_override,
        "commit_prefix": args.commit_prefix,
        "verification_depth": args.verification_depth,
        "issue_url": args.issue_url,
        "closes_issue": args.closes_issue,
        "refs_issues": args.refs_issues,
        "project_name_override": args.project_name_override,
        "project_description_override": args.project_description_override,
        "implementation_target_files": args.impl_target or None,
        "implementation_guidance": args.impl_guidance,
        "references_knowledge": args.knowledge or None,
        "parallel_notes": args.parallel_notes,
        "registry_path": args.registry_path,
        "workers_dir": args.workers_dir,
    }
    for k, v in cli_overrides.items():
        if v is not None:
            base[k] = v
    if not base.get("task_id"):
        raise SystemExit("error: --task-id is required (or supply --from-toml)")
    if not base.get("project_slug"):
        raise SystemExit(
            "error: --project-slug is required (or supply --from-toml)"
        )
    return base


def _cmd_preview(args: argparse.Namespace) -> int:
    claude_org_root = _resolve_claude_org_root(args)
    state_db_path = _resolve_state_db_path(args, claude_org_root)
    kwargs = _gather_plan_kwargs(args)
    plan = build_delegate_plan(
        claude_org_root=claude_org_root,
        state_db_path=state_db_path if state_db_path.exists() else None,
        **kwargs,
    )

    if args.json_out:
        json.dump(
            {
                "delegate_body": plan.delegate_body,
                "summary": plan.to_summary_dict(),
            },
            sys.stdout,
            indent=2,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        return 0

    print("--- DELEGATE body (preview, no writes) ---")
    print(plan.delegate_body)
    print()
    print("--- Artifacts that 'apply' would create ---")
    for p in plan.artifacts_to_create:
        print(f"  {p}")
    print(f"  send_plan.json (alongside brief)")
    print()
    print("--- Layout summary ---")
    print(json.dumps(plan.to_summary_dict(), indent=2, ensure_ascii=False))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    claude_org_root = _resolve_claude_org_root(args)
    state_db_path = _resolve_state_db_path(args, claude_org_root)
    kwargs = _gather_plan_kwargs(args)
    plan = build_delegate_plan(
        claude_org_root=claude_org_root,
        state_db_path=state_db_path if state_db_path.exists() else None,
        **kwargs,
    )
    result = apply_delegate_plan(
        plan,
        state_db_path=state_db_path,
        claude_org_root=claude_org_root,
        skip_settings=args.skip_settings,
        runtime_cmd=args.runtime_cmd,
        send_plan_out=args.send_plan_out,
    )
    print(f"reserved (queued) task_id={plan.task_id} pattern={plan.layout.pattern}")
    print(f"brief: {result.brief_path}")
    if result.settings_path is not None:
        print(f"settings: {result.settings_path}")
    elif result.settings_skipped_reason:
        print(f"settings: SKIPPED ({result.settings_skipped_reason})")
    print(f"send_plan: {result.send_plan_path}")
    print(
        "Next step: copy send_plan.json's `to_id`/`message` into a "
        "renga-peers send_message call."
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "preview":
        return _cmd_preview(args)
    if args.cmd == "apply":
        return _cmd_apply(args)
    raise SystemExit(f"unknown subcommand: {args.cmd!r}")


if __name__ == "__main__":
    raise SystemExit(main())
