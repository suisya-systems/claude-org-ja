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
  4. Emit ``send_plan.json`` describing the transport send_message
     call Secretary should issue (``to_id="dispatcher"``, ``message=<body>``).
     Codex M-3 reframed the original ``--send`` flag: this script never
     calls MCP itself; Secretary copies the JSON into their own
     ``mcp__<server>__send_message`` call, where ``<server>`` is the
     transport surface server resolved from the descriptor
     (``renga-peers`` by default, ``org-broker`` when ``ORG_TRANSPORT=broker``;
     Epic #6 D / ja#513, single SoT = ``tools.transport``).

The internal split is :func:`build_delegate_plan` (pure planner returning
a :class:`DelegatePlan`) and :func:`apply_delegate_plan` (side-effect
executor). Tests exercise both paths.
"""
from __future__ import annotations

# Direct-script bootstrap (see tools/resolve_worker_layout.py for context).
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

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
from tools import transport as _transport


_PERMISSION_MODE_RE = re.compile(
    r"^\s*default_permission_mode\s*:\s*(\S+)\s*$", re.MULTILINE
)

_PATTERN_LABELS = {
    "A": "A: プロジェクトディレクトリ",
    "B": "B: worktree",
    "C": "C: エフェメラル",
}


class WorktreeApplyError(RuntimeError):
    """Raised by apply when Pattern B worktree creation cannot proceed safely
    (e.g. ``worker_dir`` already contains unrelated content, or git fails).
    Issue #309: apply must abort rather than silently leave a half-formed
    worker dir for Secretary to discover after the worker has been spawned.
    """


class BlockingPreviewWarningError(RuntimeError):
    """Raised by ``apply`` when the plan carries one or more
    ``blocking_warnings`` (Issue #489 surface). Distinct from
    :class:`WorktreeApplyError` so callers can disambiguate "preview-time
    layout integrity refused apply" from "git worktree creation failed
    at apply time"; both classes always run before any DB / FS write so
    neither leaks a queued run row.
    """


# Filenames whose presence in a non-git ``workers/<slug>/`` is treated as a
# tell-tale leftover from an earlier Pattern A dispatch (the layout Issue
# #489 sunsets). Used by :func:`_compute_layout_warnings` to decide whether
# the directory is "non-git with residue" (blocking) vs "non-git but empty
# enough to safely bootstrap into" (no warning, falls through to legacy
# Pattern A direct).
_PATTERN_A_RESIDUE_FILENAMES: tuple[str, ...] = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    "send_plan.json",
    ".claude",
)


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
    # Absolute path of the repo from which ``git worktree add`` is run.
    # Set for Pattern B (live_repo_worktree, claude_org_repo_worktree,
    # generic) AND for Pattern A when the resolver routed worker_dir into
    # the Issue #489 unified ``<base>/.worktrees/<task>/`` layout. None
    # for Pattern A legacy direct (workers/<slug>/), Pattern C, or when
    # no usable base repo could be determined (apply then raises
    # WorktreeApplyError on Pattern B, or skips worktree creation on
    # Pattern A legacy / Pattern C).
    base_repo: Optional[Path] = None
    # Issue #489 surface: non-blocking notes (e.g. legacy ``_repo_clone``
    # layout still in use; canonical clone is preferred). Apply still
    # proceeds — they are informational only.
    warnings: list[str] = field(default_factory=list)
    # Issue #489 surface: layout integrity refused apply. ``preview`` JSON
    # exposes these so Secretary can fix the deployment before retrying;
    # ``apply`` raises :class:`BlockingPreviewWarningError` rather than
    # touching the DB / filesystem.
    blocking_warnings: list[str] = field(default_factory=list)

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
            "base_repo": str(self.base_repo) if self.base_repo else None,
            "warnings": list(self.warnings),
            "blocking_warnings": list(self.blocking_warnings),
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
    if layout.pattern_variant == "live_repo_worktree":
        return "B: worktree (live_repo_worktree — Secretary live repo 配下)"
    if layout.pattern_variant == "claude_org_repo_worktree":
        return "B: worktree (claude_org_repo_worktree — claude-org mirror 配下)"
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


def _has_pattern_a_residue(workers_slug_dir: Path) -> bool:
    """Return True iff ``workers_slug_dir`` is non-empty in a way that
    looks like a previous Pattern A dispatch's left-overs (CLAUDE.md /
    send_plan.json / .claude/ etc.).

    A directory that does not exist or only contains hidden git internals
    is treated as "safe to fall through to legacy Pattern A direct
    bootstrapping" (the historical first-time behavior). The residue
    check intentionally lists tell-tale dispatch artifacts rather than
    "any file" so a manually-placed README.md on a brand-new workers
    deployment doesn't trip the blocker.
    """
    if not workers_slug_dir.exists() or not workers_slug_dir.is_dir():
        return False
    try:
        entries = list(workers_slug_dir.iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.name in _PATTERN_A_RESIDUE_FILENAMES:
            return True
    return False


def _compute_layout_warnings(
    *,
    project_slug: str,
    workers_dir: Path,
    pattern: str,
    base_repo: Optional[Path],
) -> tuple[list[str], list[str]]:
    """Compute ``(warnings, blocking_warnings)`` for the preview surface
    (Issue #489 (d)).

    Currently emits:

    - ``blocking_warnings``: ``workers/<slug>/`` exists as a non-git
      directory with Pattern-A-residue files (CLAUDE.md / send_plan.json
      / .claude/) AND ``base_repo`` is None (no usable base discoverable
      from any source — canonical clone, ``_repo_clone`` legacy, or a
      registered local project path). The directory is the ambiguous
      Pattern A / Pattern B collision target — apply against it would
      either re-write residual files or try to ``git worktree add``
      from a non-git directory. Surface as blocking so Secretary can
      clean the residue or bootstrap a base before retrying.

      Codex Round 3 Major: the predicate is ``base_repo is None`` (the
      authoritative "no usable base" answer the rest of the plan was
      built against) — NOT "did ``find_workers_dir_clone`` return
      something". A registered local-path project (``project.path =
      /repos/clock-app``) supplies ``base_repo`` via the payload's
      project-path branch even when ``find_workers_dir_clone`` returns
      None, so leftover residue in ``workers/<slug>/`` is NOT a real
      collision — apply uses the registered local clone as base.

    - ``warnings``: ``base_repo`` resolved through the legacy
      ``workers/<slug>/_repo_clone/`` subdirectory rather than the
      canonical ``workers/<slug>/``. Apply still proceeds — the legacy
      layout works — but the canonical layout is preferred so a
      follow-up migration step can normalize it.

    Pattern C ephemeral / gitignored cases never touch ``workers/<slug>/``
    directly, so they bypass both checks.
    """
    warnings: list[str] = []
    blocking_warnings: list[str] = []
    if pattern == "C":
        return warnings, blocking_warnings
    workers_slug_dir = (Path(workers_dir) / project_slug).resolve()
    if base_repo is None and _has_pattern_a_residue(workers_slug_dir):
        blocking_warnings.append(
            f"workers_dir/{project_slug}/ exists with Pattern-A residue "
            f"(CLAUDE.md / send_plan.json / .claude/) but no usable base "
            f"clone could be determined (canonical workers_dir/{project_slug}/, "
            f"legacy workers_dir/{project_slug}/_repo_clone/, and the "
            "registered local-path base all came up empty). Refusing to "
            "apply — either clean the residue and rerun (legacy Pattern A "
            f"bootstrap), or clone the project at workers_dir/{project_slug}/ "
            "first so dispatch can use the Issue #489 canonical layout "
            "(workers_dir/<slug>/.worktrees/<task>/)."
        )
    if base_repo is not None:
        try:
            resolved = base_repo.resolve()
        except OSError:
            resolved = base_repo
        # Legacy fallback lives at ``<canonical>/_repo_clone``. We detect
        # the legacy subdir by exact-name comparison against the canonical
        # parent so a deliberately misnamed ``_repo_clone`` sibling of an
        # unrelated directory cannot trigger the warning.
        if (
            resolved.name == _REPO_CLONE_LEGACY_NAME
            and resolved.parent.resolve() == workers_slug_dir
        ):
            warnings.append(
                f"workers_dir/{project_slug}/_repo_clone/ detected as base "
                "clone (legacy layout). The Issue #489 canonical layout "
                f"places the clone directly at workers_dir/{project_slug}/. "
                "Migrate by moving the clone up one level so it lives at "
                f"workers_dir/{project_slug}/ (a strict migration script "
                "is out of scope for Issue #489 itself — a follow-up "
                "issue may automate the move)."
            )
    return warnings, blocking_warnings


# Mirrors :data:`tools.resolve_worker_layout._REPO_CLONE_SUBDIR` so the
# warning helper above doesn't reach into the resolver's private name to
# do an equality check.
_REPO_CLONE_LEGACY_NAME = "_repo_clone"


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
    # Use the platform-native joiner to avoid the mixed `\\…/CLAUDE.md`
    # output the literal-`/` template produced on Windows (Codex Round 1 Nit).
    brief_full_path = str(Path(layout.worker_dir) / brief_filename)
    body = f"""DELEGATE: 以下のワーカーを派遣してください。

タスク一覧:
- {task_id}: {instr_summary or task_id}
  - ワーカーディレクトリ: {layout.worker_dir}（{brief_filename}・設定配置済み）
  - ディレクトリパターン: {_pattern_label(layout)}
  - プロジェクト: {_project_label(layout, project_path)}
  - ブランチ (planned): {branch_line}
  - Permission Mode: {permission_mode}
  - 検証深度: {verification_depth}
  - 指示内容: 詳細は `{brief_full_path}` を参照。要約: {instr_summary or '(none)'}

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
    project_description_override: Optional[str] = None,
    implementation_target_files: Optional[list[str]] = None,
    implementation_guidance: Optional[str] = None,
    references_knowledge: Optional[list[str]] = None,
    parallel_notes: Optional[str] = None,
    registry_path: Optional[Path] = None,
    state_db_path: Optional[Path] = None,
    claude_org_root: Path,
    workers_dir: Optional[Path] = None,
    layout_overrides: Optional[dict[str, Any]] = None,
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
        project_description_override=project_description_override,
        implementation_target_files=implementation_target_files,
        implementation_guidance=implementation_guidance,
        references_knowledge=references_knowledge,
        parallel_notes=parallel_notes,
        registry_path=registry_path,
        state_db_path=state_db_path,
        claude_org_root=claude_org_root,
        workers_dir=workers_dir,
        layout_overrides=layout_overrides,
    )

    self_edit = bool(config["worker"]["self_edit"])
    brief_filename = _brief_filename(self_edit)
    brief_out_path = Path(layout.worker_dir) / brief_filename

    # Issue #370: the claude-org mirror's registry-display alias
    # ``claude-org-en`` (the 通称 column) and the canonical project slug
    # ``claude-org`` both point at the same physical clone. Normalize to
    # the canonical slug (matches both
    # ``tools/state_db/migrate_workers.py:PROJECT_RENAMES`` and the github
    # repo name) before storing, so state.db / dashboard aggregations
    # don't split the repo's runs across two project rows. Applies to ALL
    # patterns (A / B / C-gitignored) — Pattern A's variant is None but
    # the mirror still anchors worker_dir on the shared clone.
    workers_dir_for_norm = workers_dir or rwl.resolve_workers_dir(
        Path(claude_org_root)
    )
    canonical_slug = rwl._CLAUDE_ORG_SLUG_ALIASES.get(project_slug, project_slug)
    if rwl.find_claude_org_clone(canonical_slug, Path(workers_dir_for_norm)) is not None:
        project_slug = rwl._CLAUDE_ORG_CLONE_DIRNAME

    permission_mode = parse_permission_mode(Path(claude_org_root))

    # Look up project.path for the DELEGATE body label.
    project_path: Optional[str] = None
    registry_for_meta = registry_path or (
        Path(claude_org_root) / "registry" / "projects.md"
    )
    if registry_for_meta.exists():
        rows = rwl.parse_projects(registry_for_meta)
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

    # Pattern B: figure out which repo `git worktree add` should be run from.
    # live_repo_worktree → Secretary's live claude-org repo;
    # plain Pattern B    → the registered project's path.
    # Pattern A (Issue #489): when the resolver routed worker_dir into
    # ``<base>/.worktrees/<task>/`` (i.e. a real clone was detected), the
    # base is whatever ``find_workers_dir_clone`` resolved. Carry it on
    # ``plan.base_repo`` so ``_ensure_worktree`` actually creates the
    # worktree — without this, apply for the new Pattern A layout would
    # just mkdir an empty ``.worktrees/<task>/`` under the base clone and
    # the worker would land in a non-checkout directory (Codex Round 1
    # Blocker).
    base_repo: Optional[Path] = None
    if layout.pattern == "A":
        # Pattern A's Issue #489 unified layout puts worker_dir at
        # ``<base>/.worktrees/<task>/`` regardless of whether the base is a
        # workers/<slug>/ clone, a workers/<slug>/_repo_clone/ legacy
        # subdir, or the claude-org mirror clone. Derive base_repo from
        # the worker_dir shape so all three sources are handled with one
        # rule. Pattern A legacy direct (worker_dir == workers/<slug>/)
        # has no ``.worktrees`` parent and leaves ``base_repo`` None, so
        # ``_ensure_worktree`` stays a no-op for that path.
        try:
            wd_resolved = Path(layout.worker_dir).resolve()
        except OSError:
            wd_resolved = Path(layout.worker_dir)
        if wd_resolved.parent.name == ".worktrees":
            candidate = wd_resolved.parent.parent
            if rwl.is_local_git_repo(str(candidate)):
                base_repo = candidate
    if layout.pattern == "B":
        if layout.pattern_variant == "live_repo_worktree":
            base_repo = Path(claude_org_root).resolve()
        elif layout.pattern_variant == "claude_org_repo_worktree":
            # Issue #370: worker_dir = {clone}/.worktrees/<task>. A generic
            # git-repo check on parent.parent let
            # ``layout_overrides.worker_dir=<unrelated_repo>/.worktrees/<task>``
            # slip through. Re-run the origin-URL match against
            # ``workers_dir/claude-org`` and assert the derived base equals
            # that canonical clone path, so worktree creation can't be
            # redirected at an arbitrary repo via override.
            candidate = Path(layout.worker_dir).parent.parent.resolve()
            expected = rwl.find_claude_org_clone(
                rwl._CLAUDE_ORG_CLONE_DIRNAME,
                Path(workers_dir_for_norm),
            )
            if expected is None or candidate != expected.resolve():
                raise ValueError(
                    f"pattern_variant='claude_org_repo_worktree' requires "
                    f"worker_dir of shape <clone>/.worktrees/<task> where "
                    f"<clone> is the canonical claude-org mirror at "
                    f"{Path(workers_dir_for_norm) / rwl._CLAUDE_ORG_CLONE_DIRNAME}. "
                    f"worker_dir={layout.worker_dir!r} derives "
                    f"base_repo={candidate!s} which does not match the "
                    "canonical clone."
                )
            base_repo = candidate
        elif project_path and rwl.is_local_git_repo(project_path):
            base_repo = Path(project_path).resolve()
        else:
            # Issue #450: registry rows may carry only a URL (no local path),
            # with a manually-cloned repo at workers_dir/<project_slug> (renga
            # is the motivating case). Pattern B needs a local base for
            # ``git worktree add``; fall back to that conventional location
            # before giving up. The helper validates the clone's origin URL
            # so an unrelated repo left at that path can't redirect dispatch
            # (Issue #370 precedent).
            base_repo = rwl.find_workers_dir_clone(
                project_slug, project_path, Path(workers_dir_for_norm)
            )

    # Phase 1 PR4: surface base_clone into settings_args for Pattern B so the
    # runtime can substitute `{base_clone}` in
    # `worker_roles.<role>.sandbox_by_pattern.B.filesystem`. resolve_worker_layout
    # leaves this slot empty (the resolver does not materialize base_repo) and
    # gen_delegate_payload is the right place to fill it in because base_repo
    # is computed here (lines 310-339 above) from project_path / variant. For
    # Pattern A / C base_repo is None and base-clone is omitted from
    # settings_args — the runtime then errors if a Pattern A / C sandbox body
    # references {base_clone}, which is the desired loud-failure behaviour.
    settings_args = dict(layout.settings_args)
    if base_repo is not None:
        settings_args["base-clone"] = str(base_repo)
    # Issue #489 Codex Round 3 Blocker: Pattern A's Issue #489 unified
    # worktree layout (``<base>/.worktrees/<task>/``) requires the same
    # Git-metadata sandbox carve-outs as Pattern B (the worktree's
    # ``.git/worktrees/<task>/`` admin dir, the shared ``.git/objects``,
    # the THIS-branch ref, ``.git/packed-refs``). ``worker_roles.<role>.sandbox_by_pattern.A``
    # in ``tools/org_extension_schema.json`` is authored for the LEGACY
    # Pattern A layout (worker_dir == workers/<slug>/, no shared
    # ``.git``) and does NOT include those mounts; selecting it for a
    # worktree-based dispatch would cause git commit / push / fetch to
    # fail with "permission denied" inside the bwrap sandbox. We surface
    # ``pattern=B`` to the runtime ONLY for the sandbox selection while
    # leaving ``layout.pattern`` / ``settings_args["task-id"]`` / the
    # DELEGATE body / DB row at the original A label (first-vs-concurrent
    # dispatch tracking is independent of sandbox shape). ``base_repo is
    # not None`` is the same predicate that drives ``_ensure_worktree``,
    # so the two stay in lock-step. When the schema grows a
    # Pattern-A-with-worktree sandbox variant this override can be
    # removed.
    if layout.pattern == "A" and base_repo is not None:
        settings_args["pattern"] = "B"

    warnings, blocking_warnings = _compute_layout_warnings(
        project_slug=project_slug,
        workers_dir=Path(workers_dir_for_norm),
        pattern=layout.pattern,
        base_repo=base_repo,
    )

    return DelegatePlan(
        task_id=task_id,
        project_slug=project_slug,
        description=description,
        config=config,
        layout=layout,
        delegate_body=delegate_body,
        brief_out_path=brief_out_path,
        settings_args=settings_args,
        permission_mode=permission_mode,
        verification_depth=verification_depth,
        closes_issue=closes_issue,
        refs_issues=list(refs_issues or []),
        artifacts_to_create=artifacts,
        base_repo=base_repo,
        warnings=warnings,
        blocking_warnings=blocking_warnings,
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
                # Issue #489: Pattern A with a detected base clone (new
                # ``<base>/.worktrees/<task>/`` layout) is registered as
                # a worktree too — ``base_repo is not None`` covers both
                # patterns. Pattern A legacy direct (workers/<slug>/) and
                # Pattern C are NOT worktrees.
                is_worktree=plan.base_repo is not None,
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


def _resolve_base_ref(base_repo: Path) -> Optional[str]:
    """Pick the starting ref for ``git worktree add -b <branch> <path> <ref>``.

    Pattern B is triggered precisely when another active run is occupying
    the base clone — so the base's current ``HEAD`` is typically that
    other task's feature branch. Branching off it would mix unmerged
    commits into the new worktree.

    The only ref we treat as authoritative is ``origin/HEAD`` (the
    project's default branch as the remote knows it). We deliberately do
    NOT fall back to local ``main`` / ``master`` / ``HEAD`` because:

    - a stale local ``main`` left over after a trunk-rename would silently
      branch off the wrong commit (Codex Round 3 Major 2026-05-06);
    - ``HEAD`` is the original bug (Codex Round 2);
    - convention-based guessing of the trunk name is not safe in repos
      using ``trunk`` / ``develop`` / etc.

    Returns ``None`` when ``origin/HEAD`` is not set — apply then aborts
    with a recovery hint pointing to ``git remote set-head origin --auto``.

    Freshness of the returned ref is the caller's job: :func:`_ensure_worktree`
    runs :func:`_fetch_base_origin` immediately before this, so ``origin/HEAD``
    reflects the live remote tip rather than a stale local clone (Issue #480).
    """
    proc = subprocess.run(
        ["git", "-C", str(base_repo), "symbolic-ref", "--short",
         "refs/remotes/origin/HEAD"],
        capture_output=True,
    )
    if proc.returncode == 0:
        ref = proc.stdout.decode("utf-8", errors="replace").strip()
        if ref:
            return ref
    return None


def _fetch_base_origin(base_repo: Path) -> None:
    """Refresh ``base_repo``'s remote-tracking refs before a Pattern B
    worktree is branched off them (Issue #480).

    :func:`_resolve_base_ref` branches the new worktree off ``origin/HEAD``
    (i.e. ``origin/main``) — a *remote-tracking* ref only as fresh as the base
    clone's last ``git fetch``. A stale clone (e.g. another PR merged into the
    trunk after the last local fetch) would silently branch off an out-of-date
    commit, so the worker starts from a trunk missing already-merged work — the
    logical conflict / rework this fix eliminates. Fetching here makes the
    start ref the live remote tip.

    Fail-closed: the freshness guarantee only holds if the fetch actually
    succeeds, so a configured ``origin`` whose fetch fails aborts apply with a
    recovery hint (consistent with :func:`_resolve_base_ref` aborting on an
    unset ``origin/HEAD``) rather than silently branching off a possibly-stale
    ref — that silent path is exactly the Issue #480 bug. ``_ensure_worktree``
    runs before any DB reservation, so the abort leaks no ``queued`` run row.

    The only quiet path is "no ``origin`` remote configured": purely local
    repos (and the test fixtures that synthesize ``refs/remotes/origin/*``
    without a real remote) have nothing to fetch.
    """
    probe = subprocess.run(
        ["git", "-C", str(base_repo), "remote", "get-url", "origin"],
        capture_output=True,
    )
    if probe.returncode != 0:
        return  # no origin remote — nothing to refresh
    proc = subprocess.run(
        ["git", "-C", str(base_repo), "fetch", "origin"],
        capture_output=True,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise WorktreeApplyError(
            f"`git fetch origin` failed (rc={proc.returncode}) in {base_repo} "
            f"before creating the Pattern B worktree: {stderr}. Refusing to "
            "branch off a possibly-stale origin/main (Issue #480). Fix the "
            f"network / remote and retry apply (or run `git -C {base_repo} "
            "fetch origin` manually first). If this base is intentionally "
            "offline / local-only, remove its `origin` remote so apply skips "
            "the fetch."
        )


def _worktree_branch(worker_dir: Path) -> Optional[str]:
    """Return the branch name currently checked out at ``worker_dir`` (or
    None on detached HEAD / git error)."""
    proc = subprocess.run(
        ["git", "-C", str(worker_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    name = proc.stdout.decode("utf-8", errors="replace").strip()
    if not name or name == "HEAD":
        return None
    return name


def _is_registered_worktree(base_repo: Path, worker_dir: Path) -> bool:
    """True iff ``worker_dir`` is already a registered worktree of ``base_repo``.

    Compared by resolved absolute path so a re-run of apply against an
    already-created worktree is idempotent (Issue #309).
    """
    proc = subprocess.run(
        ["git", "-C", str(base_repo), "worktree", "list", "--porcelain"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return False
    try:
        target = worker_dir.resolve()
    except OSError:
        return False
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("worktree "):
            try:
                p = Path(line[len("worktree "):]).resolve()
            except OSError:
                continue
            if p == target:
                return True
    return False


def _ensure_worktree(plan: DelegatePlan) -> None:
    """For Pattern B, run ``git worktree add`` if the dir is not already one.

    Idempotent — no-op when ``worker_dir`` is already a registered worktree
    of ``base_repo``. Aborts with :class:`WorktreeApplyError` when the dir
    exists with unrelated content (Secretary judgment call to clean up,
    per Issue #309).

    The Issue #480 origin fetch gates *creation* only. A reused worktree
    keeps the start ref it was created from — re-fetching here would not move
    its already-committed branch tip, and force-resetting it could clobber
    work a worker has already committed on a partial-retry. To re-base a stale
    reused worktree the Secretary removes it so apply recreates it (which then
    fetches). See the reuse branch below.
    """
    # Issue #489: Pattern A with a detected base clone now uses the same
    # ``<base>/.worktrees/<task>/`` layout as Pattern B, so the worktree
    # creation must fire for either pattern when ``base_repo`` is set.
    # Conversely, Pattern A WITHOUT a base clone (legacy direct path,
    # worker_dir == workers/<slug>/) and Pattern C ephemeral / gitignored
    # both leave ``base_repo`` unset; for those, apply only writes the
    # brief — no worktree to create.
    if plan.base_repo is None:
        if plan.layout.pattern == "B":
            raise WorktreeApplyError(
                f"Pattern B but no usable base repo could be determined for "
                f"project {plan.project_slug!r} (variant="
                f"{plan.layout.pattern_variant!r}); cannot run `git worktree add`."
            )
        return
    worker_dir = Path(plan.layout.worker_dir)
    if _is_registered_worktree(plan.base_repo, worker_dir):
        # Idempotent reuse — but only when the existing worktree is on the
        # branch the brief / DB will pin. A stale partial-retry worktree on
        # a different branch (or in detached-HEAD state) would otherwise
        # silently dispatch on the wrong ref (Codex Round 2 + Round 3
        # Majors 2026-05-06). We deliberately do NOT fetch / re-base here
        # (Issue #480): the worktree already has a committed branch tip, so a
        # fetch can't advance it and a reset could discard a worker's
        # in-progress commits. Refresh = remove the worktree so the creation
        # path below recreates it off a freshly fetched origin/HEAD.
        expected = plan.layout.planned_branch
        if expected:
            actual = _worktree_branch(worker_dir)
            if actual is None:
                raise WorktreeApplyError(
                    f"worker_dir {worker_dir} is registered as a git "
                    f"worktree but is in detached-HEAD state, not on the "
                    f"planned branch {expected!r}. Refusing to dispatch — "
                    "Secretary must `git checkout` the intended branch (or "
                    "remove the worktree and let apply recreate it) and retry."
                )
            if actual != expected:
                raise WorktreeApplyError(
                    f"worker_dir {worker_dir} is registered as a git "
                    f"worktree but is on branch {actual!r}, not the planned "
                    f"{expected!r}. Refusing to dispatch on a mismatched "
                    "branch — Secretary must check out the intended branch "
                    "(or remove the worktree and let apply recreate it) "
                    "and retry."
                )
        return
    if worker_dir.exists() and any(worker_dir.iterdir()):
        raise WorktreeApplyError(
            f"worker_dir {worker_dir} exists with content but is not a "
            f"registered git worktree of {plan.base_repo}. Refusing to "
            "auto-recover — Secretary must clean up the directory (or run "
            "`git worktree add` manually) and retry apply."
        )
    branch = plan.layout.planned_branch
    if not branch:
        raise WorktreeApplyError(
            f"Pattern B requires a planned_branch but layout produced None "
            f"(task_id={plan.task_id!r})."
        )
    # Issue #480: refresh remote-tracking refs first so we branch off the
    # *current* origin/HEAD, not a stale local clone's old origin/main.
    _fetch_base_origin(plan.base_repo)
    base_ref = _resolve_base_ref(plan.base_repo)
    if base_ref is None:
        raise WorktreeApplyError(
            f"could not resolve `origin/HEAD` in {plan.base_repo}. Refusing "
            "to guess the trunk from local refs because a stale local "
            "`main` after a trunk-rename would silently branch the new "
            "worktree off the wrong commit, and `HEAD` is the original "
            "Pattern-B bug (it points to whatever feature branch the base "
            "is currently on). Run `git remote set-head origin --auto` in "
            f"{plan.base_repo} (or `git symbolic-ref refs/remotes/origin/"
            "HEAD refs/remotes/origin/<trunk>` if you don't have remote "
            "access) and retry."
        )
    worker_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "git", "-C", str(plan.base_repo),
        "worktree", "add", "-b", branch, str(worker_dir), base_ref,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise WorktreeApplyError(
            f"`git worktree add` failed (rc={proc.returncode}) for "
            f"{worker_dir} from {plan.base_repo}: {stderr}"
        )


def _write_brief(plan: DelegatePlan) -> Path:
    text = gwb.render(plan.config)
    out = plan.brief_out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def _build_settings_generate_cmd(
    settings_args: dict[str, Any],
    *,
    runtime_cmd: str,
) -> list[str]:
    """Build the ``claude-org-runtime settings generate`` argv list.

    Pure helper extracted from :func:`_run_settings_generate` so the
    Phase 1 PR4 dispatch-context pass-through (``--pattern`` /
    ``--base-clone`` / ``--task-id`` / ``--branch-ref``) is unit-testable
    without subprocess mocking.

    The mandatory flags (``--role`` / ``--worker-dir`` / ``--claude-org-path``
    / ``--out``) are always emitted in a stable order. The optional
    dispatch-context flags are emitted only when the corresponding key is
    present and non-None in ``settings_args`` — the runtime CLI accepts
    each independently and errors only if the rendered body references a
    placeholder for which the corresponding context is missing (that
    loud-failure surface is intentional: Pattern A / C without
    ``--base-clone`` must NOT silently substitute an empty string into a
    ``{base_clone}``-using sandbox body).
    """
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
    for flag, key in (
        ("--pattern", "pattern"),
        ("--base-clone", "base-clone"),
        ("--task-id", "task-id"),
        ("--branch-ref", "branch-ref"),
    ):
        if key in settings_args and settings_args[key] is not None:
            cmd.extend([flag, str(settings_args[key])])
    return cmd


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
    cmd = _build_settings_generate_cmd(settings_args, runtime_cmd=runtime_cmd)
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return None, (
            f"{runtime_cmd} settings generate failed (rc={e.returncode}): "
            f"{(e.stderr or b'').decode('utf-8', errors='replace').strip()}"
        )
    return out, None


def _write_send_plan(plan: DelegatePlan, *, out_path: Path) -> Path:
    """Write the transport MCP call manifest Secretary will copy from.

    The manifest is transport-neutral (``to_id`` / ``message`` only); the
    concrete ``mcp__<server>__send_message`` tool name is chosen by the
    Secretary from the descriptor-driven server (``tools.transport``,
    §5.2 (i)). Renga is the default, so the emitted JSON is byte-identical
    to the current output.
    """
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


def _abandon_queued_run(state_db_path: Path, task_id: str) -> None:
    """Compensating UPDATE that flips a same-task queued run to
    ``abandoned`` so it stops counting as an active reservation.

    Used by :func:`apply_delegate_plan` when a post-reservation step fails
    after the DB transaction has already committed (Issue #489 Blocker 2:
    a leaked queued row makes the next dispatch see Pattern A as
    occupied and silently flips it onto Pattern B). Best-effort: a
    compensation failure is logged to stderr but not re-raised, because
    we are already on the exception path and the original failure
    should be what the caller observes.
    """
    from tools.state_db import connect
    from tools.state_db.writer import StateWriter

    try:
        conn = connect(state_db_path)
        try:
            writer = StateWriter(conn)
            with writer.transaction() as tx:
                tx.update_run_status(
                    task_id,
                    "abandoned",
                    outcome_note=(
                        "apply post-reservation failed; compensated by "
                        "tools.gen_delegate_payload (Issue #489 Blocker 2)"
                    ),
                )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — best-effort compensation
        sys.stderr.write(
            "tools.gen_delegate_payload: failed to compensate queued run "
            f"task_id={task_id!r} ({type(exc).__name__}: {exc}). Manually "
            f"`UPDATE runs SET status='abandoned' WHERE task_id='{task_id}'` "
            "to unblock subsequent dispatch.\n"
        )


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
    # Issue #489 (d): preview-time layout integrity check. ``blocking_warnings``
    # surfaces deployments that have a non-git ``workers/<slug>/`` with
    # Pattern-A residue and no usable base clone — apply would either rewrite
    # the residue or fail loudly inside ``git worktree add``. Refuse here so
    # the failure happens BEFORE any DB / FS write, leaving no queued row to
    # compensate.
    if plan.blocking_warnings:
        raise BlockingPreviewWarningError(
            "apply refused: preview emitted blocking warnings:\n  - "
            + "\n  - ".join(plan.blocking_warnings)
        )
    # Issue #309: create the worktree FIRST. If this fails (dirty dir,
    # git error, etc.) we must not leak a `runs.status='queued'` row,
    # because resolve_worker_layout treats `queued` as an active run and
    # would silently steer the next delegation onto another Pattern B
    # branch (Codex Major 2026-05-06). No-op for Pattern A / C.
    _ensure_worktree(plan)
    db_reservation = _reserve_in_db(
        plan, state_db_path=state_db_path, claude_org_root=claude_org_root
    )
    # Issue #489 Blocker 2: ``_reserve_in_db`` already committed the queued
    # row. Anything that fails *after* this commit (brief write hitting a
    # full disk, send_plan write losing permissions, settings subprocess
    # raising unexpectedly) leaks an active reservation. ``StateWriter``
    # rollback can't help — its transaction is already closed. Wrap the
    # remaining steps and run the compensating ``abandoned`` update on
    # failure so the next dispatch sees Pattern A as free.
    try:
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
    except BaseException:
        _abandon_queued_run(state_db_path, plan.task_id)
        raise
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
    """Args shared by preview and apply for the from-task input form.

    Defaults for ``--mode`` and ``--verification-depth`` are intentionally
    ``None``. The merge step in :func:`_gather_plan_kwargs` only treats a
    CLI value as an override when it is not None, so a ``--from-toml``
    config's ``mode`` / ``verification_depth`` survives unless the caller
    explicitly re-passes the flag. Final defaults (``edit`` / ``full``)
    are applied after merging — see Codex Round 1 Major.
    """
    p.add_argument("--task-id")
    p.add_argument("--project-slug")
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--description", default=None)
    p.add_argument("--mode", choices=("edit", "audit"), default=None)
    p.add_argument("--branch", dest="branch_override", default=None)
    p.add_argument("--commit-prefix", default=None)
    p.add_argument(
        "--verification-depth", choices=("full", "minimal"), default=None
    )
    p.add_argument("--issue-url", default=None)
    p.add_argument("--closes-issue", type=int, default=None)
    p.add_argument("--refs-issues", type=int, nargs="*", default=None)
    # Intentionally no --project-name: project.name is the slug (use
    # --project-slug). Allowing an override created a round-trip drift
    # where the next --from-toml read 通称 back as the slug (Codex Round 2).
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
    # Issue #374: Secretary-side pattern override. The resolver enforces a
    # strict contract (B requires a usable base, A forbidden on self-edit,
    # C always permitted); see ``ResolveError`` raised from
    # ``resolve_worker_layout.resolve``. Default None means "let the
    # resolver auto-derive" — preserves the pre-#374 behaviour.
    p.add_argument(
        "--pattern",
        choices=("A", "B", "C"),
        default=None,
        help=(
            "Force a specific dispatch pattern (A/B/C). Validated by the "
            "resolver: B requires a worktree base; A forbidden on "
            "claude-org-self-edit; C always permitted."
        ),
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
    """Resolve claude-org repo root. Priority (highest first):

    1. ``--claude-org-root`` CLI flag (explicit).
    2. ``[paths] claude_org`` from ``--from-toml`` (Issue #290 defect 2 —
       previously ignored, leaving cwd-derived paths to drift).
    3. cwd-walk fallback (:func:`gwb._detect_claude_org_root`).
    """
    if args.claude_org_root is not None:
        return Path(args.claude_org_root).resolve()
    toml_path = getattr(args, "from_toml", None)
    if toml_path is not None:
        try:
            with Path(toml_path).open("rb") as fh:
                cfg = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            cfg = {}
        cfg_path = (cfg.get("paths") or {}).get("claude_org")
        if cfg_path:
            p = Path(cfg_path)
            if not p.is_absolute():
                p = (Path(toml_path).resolve().parent / p)
            return p.resolve()
    return gwb._detect_claude_org_root()


def _resolve_state_db_path(args: argparse.Namespace, claude_org_root: Path) -> Path:
    if args.state_db_path is not None:
        return args.state_db_path
    return claude_org_root / ".state" / "state.db"


def _load_task_args_from_toml(path: Path) -> dict[str, Any]:
    """Pull task-shape kwargs out of a worker_brief.toml.

    The TOML schema (see ``tools/templates/worker_brief.example.toml``)
    uses ``project.name`` for the slug — that's what both the legacy
    hand-written briefs and Stage 2's ``from-task`` output write — so we
    treat it as ``project_slug`` directly. Codex Round 1+2 caught the
    inversions (common_name vs slug; --project-name override drift).

    ``mode`` is derived from ``worker.role``: ``doc-audit`` → ``audit``,
    everything else → ``edit``. Returning the field at all (even when
    falsey) lets the merge step recognise an explicit TOML setting.
    """
    with path.open("rb") as fh:
        cfg = tomllib.load(fh)
    task = cfg.get("task", {})
    worker = cfg.get("worker", {})
    project = cfg.get("project", {})
    impl = cfg.get("implementation", {})
    refs = cfg.get("references", {})
    parallel = cfg.get("parallel", {})
    role = (worker.get("role") or "").strip()
    mode_from_toml = "audit" if role == "doc-audit" else "edit"

    # Issue #290 defect 1: surface explicit [worker] fields so the resolver
    # honors them instead of silently re-deriving pattern/role/dir/self_edit.
    layout_overrides: dict[str, Any] = {}
    if worker.get("pattern"):
        layout_overrides["pattern"] = worker["pattern"]
    if worker.get("pattern_variant"):
        layout_overrides["pattern_variant"] = worker["pattern_variant"]
    if worker.get("dir"):
        # Resolve relative dirs against the TOML file's parent so
        # [paths].claude_org and [worker].dir share the same base
        # (Codex Round 1 Minor — previously cwd-relative).
        wd = Path(worker["dir"])
        if not wd.is_absolute():
            wd = (path.resolve().parent / wd)
        layout_overrides["worker_dir"] = str(wd)
    if role:
        layout_overrides["role"] = role
    if "self_edit" in worker:
        # Pass through unchanged so resolve()'s strict bool check fires
        # on malformed input (e.g. self_edit = "false" parsed as a string).
        # Codex Round 2 Minor.
        layout_overrides["self_edit"] = worker["self_edit"]

    return {
        "task_id": task.get("id"),
        "project_slug": project.get("name"),
        "description": task.get("description"),
        "branch_override": task.get("branch"),
        "commit_prefix": task.get("commit_prefix"),
        "verification_depth": task.get("verification_depth"),
        "issue_url": task.get("issue_url"),
        "closes_issue": task.get("closes_issue"),
        "refs_issues": task.get("refs_issues"),
        "project_description_override": project.get("description"),
        "implementation_target_files": impl.get("target_files"),
        "implementation_guidance": impl.get("guidance"),
        "references_knowledge": refs.get("knowledge"),
        "parallel_notes": parallel.get("notes"),
        "mode": mode_from_toml,
        "layout_overrides": layout_overrides or None,
    }


def _gather_plan_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Merge --from-toml defaults with CLI flags (CLI wins).

    Defaults for ``mode`` (``edit``) and ``verification_depth`` (``full``)
    are applied **after** the merge so a TOML-supplied value survives a
    bare CLI invocation. Otherwise argparse's defaults would always win
    and TOML round-trip would silently flatten ``doc-audit`` / ``minimal``.
    """
    base: dict[str, Any] = {}
    if args.from_toml is not None:
        base.update(_load_task_args_from_toml(args.from_toml))
    # Issue #374: ``--pattern`` is a layout override, not a task field, so
    # merge it into ``layout_overrides`` rather than into the top-level
    # kwargs. CLI wins over a TOML [worker].pattern of the same key, matching
    # the rest of the merge order documented above. ``None`` means "no CLI
    # override", which preserves any TOML value already merged into base.
    #
    # When the CLI flag is supplied, also drop the TOML's [worker].dir and
    # [worker].pattern_variant from the override dict — otherwise the
    # resolver treats them as explicit values and skips its pattern-driven
    # re-derivation, leaving worker_dir / variant on the previous pattern's
    # convention. Codex Round 2 Major: ``--pattern C`` on a TOML that
    # carries [worker].dir = workers/<slug>/ used to keep worker_dir at
    # the registered clone, producing a contradictory C layout.
    pattern_override = getattr(args, "pattern", None)
    if pattern_override is not None:
        existing_overrides = base.get("layout_overrides") or {}
        merged = dict(existing_overrides)
        merged["pattern"] = pattern_override
        merged.pop("worker_dir", None)
        merged.pop("pattern_variant", None)
        base["layout_overrides"] = merged
    # CLI overrides — only when caller actually provided a value.
    cli_overrides: dict[str, Any] = {
        "task_id": args.task_id,
        "project_slug": args.project_slug,
        "targets": args.target or None,
        "description": args.description,
        "mode": args.mode,
        "branch_override": args.branch_override,
        "commit_prefix": args.commit_prefix,
        "verification_depth": args.verification_depth,
        "issue_url": args.issue_url,
        "closes_issue": args.closes_issue,
        "refs_issues": args.refs_issues,
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
    base.setdefault("mode", "edit")
    base.setdefault("verification_depth", "full")
    base.setdefault("description", "")
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
                # Issue #489 (d): hoist warnings / blocking_warnings to the
                # top level so shell consumers can ``jq '.blocking_warnings | length > 0'``
                # without descending into ``summary``. ``summary`` keeps a
                # copy too (every key of the schema is mirrored there) so
                # already-built tooling that only reads ``summary`` is not
                # broken.
                "warnings": list(plan.warnings),
                "blocking_warnings": list(plan.blocking_warnings),
            },
            sys.stdout,
            indent=2,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
        # Mirror the human-path exit code so JSON / human consumers agree:
        # blocking_warnings → rc=3 so CI wrappers detect the refusal even when
        # they only read stdout JSON. ``stderr`` is left clean here (the JSON
        # body already carries the warning content) to keep pipelines simple.
        return 3 if plan.blocking_warnings else 0

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
    # Issue #489 (d): emit warnings to stderr (so a piped stdout JSON / body
    # capture stays clean), and BLOCKING markers loudly so an attempted
    # ``apply`` after this preview will refuse. The non-zero exit on
    # blocking_warnings means CI / wrapper scripts see a clear signal.
    if plan.warnings:
        for w in plan.warnings:
            sys.stderr.write(f"warning: {w}\n")
    if plan.blocking_warnings:
        for w in plan.blocking_warnings:
            sys.stderr.write(f"BLOCKING: {w}\n")
        return 3
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
    print(_next_step_hint())
    return 0


def _next_step_hint() -> str:
    """Operator-facing next-step line for ``apply``.

    The transport server name is descriptor-driven (§5.2 (i) single SoT):
    ``renga-peers`` by default (byte-identical with the current output) and
    ``org-broker`` when ``ORG_TRANSPORT=broker``. This keeps the hint aligned
    with the ``mcp__<server>__send_message`` call the Secretary actually
    issues when copying ``send_plan.json``.
    """
    return (
        "Next step: copy send_plan.json's `to_id`/`message` into a "
        f"{_transport.server_name()} send_message call."
    )


def _reconfigure_stdout() -> None:
    """Force stdout/stderr to UTF-8 on Windows so the DELEGATE body / Layout
    summary (which contain Japanese) don't mojibake under cp932 consoles
    (Issue #290 defect 3). No-op when reconfigure is unavailable."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv: Optional[list[str]] = None) -> int:
    _reconfigure_stdout()
    args = _build_parser().parse_args(argv)
    if args.cmd == "preview":
        return _cmd_preview(args)
    if args.cmd == "apply":
        return _cmd_apply(args)
    raise SystemExit(f"unknown subcommand: {args.cmd!r}")


if __name__ == "__main__":
    raise SystemExit(main())
