"""Resolve worker layout (pattern / dir / role / branch) for a task.

Codifies the org-delegate Step 0.7 + Step 1 + Step 1.5 ("Role の選び方")
hand-decision flow as a deterministic library + thin CLI wrapper.

Library-first: callers in the same Python process should import
:func:`resolve` and consume the :class:`WorkerLayout` dataclass directly.
The CLI (``python -m tools.resolve_worker_layout`` or
``python tools/resolve_worker_layout.py``) is a thin JSON-stdout wrapper
for shell consumers and for ad-hoc Secretary inspection.

Inputs:
- task_id, project_slug
- targets (list of file paths for Step 0.7 gitignore check; 0 = skip)
- description (free text; used only for branch-prefix inference)
- mode ('edit' | 'audit') — explicit, so claude-org read-only audit is
  not misclassified as a self-edit. Codex Design Major M-4.
- branch_override (optional; bypasses inference)
- registry_path / state_db_path / claude_org_root / workers_dir
  (defaults derived from claude_org_root when None)

Output (JSON shape):

    {
      "pattern": "A" | "B" | "C",
      "pattern_variant": "ephemeral" | "gitignored_repo_root" | "live_repo_worktree" | None,
      "worker_dir": "<absolute path>",
      "role": "default" | "claude-org-self-edit" | "doc-audit",
      "self_edit": <bool>,
      "planned_branch": "<inferred or null>",
      "settings_args": {
        "role": "...",
        "worker-dir": "...",
        "claude-org-path": "...",
        "out": "<worker_dir>/.claude/settings.local.json"
      }
    }

Key contract notes (from Codex review of Issue #283):
- ``pattern_variant`` carries sub-mode labels: the two Pattern C sub-modes
  (``ephemeral`` / ``gitignored_repo_root``, M-1) and the Pattern B
  ``live_repo_worktree`` sub-mode used by claude-org self-edit (Issue #289).
- ``planned_branch`` is the resolver's *suggestion*; the actual branch
  may diverge after worktree creation, so callers MUST re-read git
  before pinning it into the brief / payload (M-2).
- Active-work detection uses ``runs.status in ('queued','in_use','review')``
  via a ``runs JOIN worker_dirs`` query — not a direct read of the
  ``worker_dirs`` table (Codex Design Blocker B-2).
"""
from __future__ import annotations

# When invoked as ``python tools/resolve_worker_layout.py`` (the form the
# org-delegate skill documents), sys.path[0] is the ``tools/`` directory and
# ``from tools import ...`` would fail with ModuleNotFoundError. Insert the
# repo root so the package import works regardless of launch form. Harmless
# when this module is imported (the path may already be there).
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from tools.registry_parser import Project as RegistryProject, parse_projects
from tools.state_db import connect as db_connect
from tools.state_db.queries import list_runs_with_dirs


VALID_MODES = ("edit", "audit")
VALID_PATTERNS = ("A", "B", "C")
# 'live_repo_worktree' is the Pattern B sub-mode used by claude-org self-edit
# tasks: the worktree base is Secretary's live repo (claude_org_root) instead
# of the conventional {workers_dir}/{project_slug}/. See Issue #289 and
# references/claude-org-self-edit.md for the rationale.
VALID_VARIANTS = (
    "ephemeral",
    "gitignored_repo_root",
    "live_repo_worktree",
    "claude_org_repo_worktree",
)
VALID_ROLES = ("default", "claude-org-self-edit", "doc-audit")

# Active reservation states — these mean someone else is occupying the base
# clone, so a new task on the same project must use a worktree (Pattern B).
# 'queued' is included because Issue #283 introduces T1 reservations that
# write runs.status='queued' before the worker pane is spawned (T2 flips to
# 'in_use'); a back-to-back delegation must see the queued reservation as
# "in use".
_ACTIVE_RUN_STATUSES = ("queued", "in_use", "review")

# Trigger words that flip the planned-branch prefix from feat/ to fix/.
_FIX_TRIGGERS = ("fix", "bug", "修正", "hotfix", "patch")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
#
# ``RegistryProject`` is re-exported from :mod:`tools.registry_parser` so
# downstream callers keep working without churn; the shared dataclass uses
# ``name`` (slug) / ``nickname`` (通称) instead of the legacy ``slug`` /
# ``common_name`` field names.


@dataclass(frozen=True)
class WorkerLayout:
    pattern: str                       # "A" | "B" | "C"
    pattern_variant: Optional[str]     # "ephemeral" | "gitignored_repo_root" | "live_repo_worktree" | None
    worker_dir: str                    # absolute path
    role: str                          # default | claude-org-self-edit | doc-audit
    self_edit: bool
    planned_branch: Optional[str]
    settings_args: dict[str, str] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "pattern_variant": self.pattern_variant,
            "worker_dir": self.worker_dir,
            "role": self.role,
            "self_edit": self.self_edit,
            "planned_branch": self.planned_branch,
            "settings_args": dict(self.settings_args),
        }


class ResolveError(ValueError):
    """Raised when inputs are malformed or contradictory."""


# ---------------------------------------------------------------------------
# registry/projects.md lookup
# ---------------------------------------------------------------------------


def find_project(rows: Iterable[RegistryProject], slug: str) -> Optional[RegistryProject]:
    for r in rows:
        if r.name == slug:
            return r
    return None


# ---------------------------------------------------------------------------
# org-config.md (workers_dir)
# ---------------------------------------------------------------------------


_WORKERS_DIR_RE = re.compile(r"^\s*workers_dir\s*:\s*(\S+)\s*$", re.MULTILINE)


def parse_workers_dir(org_config_text: str) -> Optional[str]:
    m = _WORKERS_DIR_RE.search(org_config_text)
    return m.group(1) if m else None


def resolve_workers_dir(claude_org_root: Path) -> Path:
    cfg_path = claude_org_root / "registry" / "org-config.md"
    raw = ""
    if cfg_path.exists():
        raw = cfg_path.read_text(encoding="utf-8")
    rel = parse_workers_dir(raw) or "../workers"
    return (claude_org_root / rel).resolve()


# ---------------------------------------------------------------------------
# Step 0.7: gitignore check
# ---------------------------------------------------------------------------


def is_local_git_repo(path: str) -> bool:
    """Local-path heuristic: not a URL, exists, has .git, isn't '-'."""
    if not path or path == "-":
        return False
    if "://" in path:
        return False
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return False
    return (p / ".git").exists()


def any_target_is_gitignored(project_path: str, targets: list[str]) -> bool:
    """Return True iff at least one target matches ``git check-ignore``.

    Only call this after :func:`is_local_git_repo` confirms the project
    path is a usable local repo. Targets are passed through to git as-is
    (git accepts both repo-relative and absolute paths under -C).
    """
    if not targets:
        return False
    for tgt in targets:
        try:
            rc = subprocess.run(
                ["git", "-C", project_path, "check-ignore", "-q", "--", tgt],
                capture_output=True,
            ).returncode
        except (FileNotFoundError, OSError):
            # git missing or permission denied — be conservative: treat
            # as "not ignored" so we fall through to normal Pattern judgment.
            return False
        if rc == 0:
            return True
    return False


# ---------------------------------------------------------------------------
# state.db — pattern detection via runs JOIN worker_dirs
# ---------------------------------------------------------------------------


def project_has_active_run(conn: sqlite3.Connection, project_slug: str) -> bool:
    """True iff this project has at least one run with worker_dir attached
    whose status is queued / in_use / review.

    Routes through ``list_runs_with_dirs`` (the same query the snapshotter
    uses) so resolver state-of-the-world matches what the dashboard and
    org-state.md regenerate from. See Codex Design Blocker B-2.
    """
    for row in list_runs_with_dirs(conn):
        if row.get("project_slug") != project_slug:
            continue
        if row.get("status") in _ACTIVE_RUN_STATUSES:
            return True
    return False


# ---------------------------------------------------------------------------
# Branch inference
# ---------------------------------------------------------------------------


def infer_branch(task_id: str, description: str) -> str:
    """Return ``feat/<task-id>`` or ``fix/<task-id>`` based on description.

    If the task_id already carries a feat/ or fix/ prefix, return as-is.
    """
    if task_id.startswith(("feat/", "fix/", "chore/", "docs/")):
        return task_id
    desc_lower = description.lower()
    is_fix = any(t in desc_lower for t in _FIX_TRIGGERS)
    return f"{'fix' if is_fix else 'feat'}/{task_id}"


# ---------------------------------------------------------------------------
# Role detection
# ---------------------------------------------------------------------------


# Canonical claude-org self-edit slug. The resolver short-circuits Pattern B +
# live_repo_worktree for this slug when claude_org_root's git origin matches
# one of the canonical repos below. Detection runs off the origin URL so the
# registry need not carry a user-specific local path for the self-edit target.
_CLAUDE_ORG_SELF_EDIT_SLUG = "claude-org-ja"

# Repo names (lowercased, no owner, no trailing ``.git``) that count as
# claude-org self-edit targets. Owner is intentionally NOT pinned: the
# project documents fork-based contribution (see CONTRIBUTING.md), and a
# fork's ``origin`` points at the contributor's fork (e.g.
# ``git@github.com:<user>/claude-org-ja.git``), not at suisya-systems'.
# Tuple constant so adding additional self-edit repo names is a one-line
# change.
_CLAUDE_ORG_REPO_NAMES: tuple[str, ...] = ("claude-org-ja",)

# Capture <owner>/<repo> from common github URL forms:
#   https://github.com/owner/repo(.git)
#   git@github.com:owner/repo(.git)
#   ssh://git@github.com/owner/repo(.git)
# A local-path origin (worker-dir clones use these) has no ``github.com``
# segment, so the regex naturally rejects it — even when the upstream dir
# happens to be named ``claude-org-ja``.
_GITHUB_OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/:\s]+)/([^/:\s]+?)(?:\.git)?/?$")


def _extract_github_repo_name(url: str) -> Optional[str]:
    """Return the lowercased repo name (no ``.git``) if ``url`` is a github
    remote, else None."""
    s = url.strip().lower()
    if not s:
        return None
    m = _GITHUB_OWNER_REPO_RE.search(s)
    return m.group(2) if m else None


def _git_origin_url(repo_path: Path) -> Optional[str]:
    """Return ``git -C repo_path remote get-url origin`` stdout, or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


# claude-org mirror clone — the sibling clone of claude-org-ja. Like the ja
# self-edit target, the mirror has no registry row to avoid leaking a
# user-specific local path; detection runs off the clone's git origin URL.
# The canonical project slug is ``claude-org`` (matches the github repo
# name and ``tools/state_db/migrate_workers.py:PROJECT_RENAMES``); the
# 通称 ``claude-org-en`` is recognized as a registry-display alias and
# normalized to the canonical slug at the resolve() boundary.
# Two signals must hold for detection:
# 1. The (already-normalized) slug equals ``claude-org``.
# 2. ``{workers_dir}/claude-org`` exists as a git repo whose origin URL
#    points at a github repo named ``claude-org`` (no ``-ja`` suffix).
#    Owner is intentionally NOT pinned: forks contribute the same way
#    claude-org-ja does (see ``CONTRIBUTING.md``).
_CLAUDE_ORG_MIRROR_REPO_NAMES: tuple[str, ...] = ("claude-org",)
_CLAUDE_ORG_CLONE_DIRNAME = "claude-org"

# Registry-display aliases that resolve to the canonical project slug. The
# checked-in registry's 通称 column carries ``claude-org-en`` for
# readability; normalize at the boundary so downstream code only sees the
# canonical project slug.
_CLAUDE_ORG_SLUG_ALIASES: dict[str, str] = {
    "claude-org-en": "claude-org",
}


def find_claude_org_clone(
    project_slug: str, workers_dir: Path
) -> Optional[Path]:
    """Return the canonical claude-org clone path if slug + origin URL
    match, else None.

    Issue #370: the mirror lives at ``{workers_dir}/claude-org``. Without
    this detection the resolver returned Pattern C ephemeral for unknown
    slugs (or Pattern B with ``variant=None`` + ``base_repo=None`` when a
    legacy registry row was present), and ``apply`` failed with "no usable
    base repo could be determined". Anchoring on the clone's origin URL
    makes detection independent of registry state.

    Caller must pass the already-normalized canonical slug (``claude-org``).
    The resolve() boundary maps registry-display aliases via
    :data:`_CLAUDE_ORG_SLUG_ALIASES` before reaching here.
    """
    if project_slug != _CLAUDE_ORG_CLONE_DIRNAME:
        return None
    candidate = (Path(workers_dir) / _CLAUDE_ORG_CLONE_DIRNAME).resolve()
    if not is_local_git_repo(str(candidate)):
        return None
    url = _git_origin_url(candidate)
    if url is None:
        return None
    repo_name = _extract_github_repo_name(url)
    return candidate if repo_name in _CLAUDE_ORG_MIRROR_REPO_NAMES else None


def is_claude_org_project(project_slug: str, claude_org_root: Path) -> bool:
    """True iff this delegation targets claude-org self-edit.

    Two signals must both hold:
    1. ``project_slug`` equals the canonical self-edit slug
       (``claude-org-ja``). Without this gate any edit task — clock-app,
       renga, etc. — would be flagged self-edit because Secretary always
       runs the resolver from inside the live claude-org checkout.
    2. ``claude_org_root``'s ``origin`` remote URL points at a github
       repo whose name matches a known claude-org target. Owner is not
       pinned — fork-based contribution is the documented workflow and a
       fork's origin is ``<contributor>/claude-org-ja``, not
       ``suisya-systems/claude-org-ja``. A worker-dir clone has origin
       set to a local filesystem path (no github.com segment), so it
       does not match; a fresh repo without any remote also does not
       match.
    """
    if project_slug != _CLAUDE_ORG_SELF_EDIT_SLUG:
        return False
    url = _git_origin_url(claude_org_root)
    if url is None:
        return False
    repo_name = _extract_github_repo_name(url)
    return repo_name in _CLAUDE_ORG_REPO_NAMES


def decide_role(
    *,
    mode: str,
    project_slug: str,
    claude_org_root: Path,
) -> str:
    if mode == "audit":
        return "doc-audit"
    if mode == "edit":
        if is_claude_org_project(project_slug, claude_org_root):
            return "claude-org-self-edit"
        return "default"
    raise ResolveError(f"unknown mode: {mode!r} (expected one of {VALID_MODES})")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve(
    *,
    task_id: str,
    project_slug: str,
    targets: Optional[list[str]] = None,
    description: str = "",
    mode: str = "edit",
    branch_override: Optional[str] = None,
    registry_path: Optional[Path] = None,
    state_db_path: Optional[Path] = None,
    claude_org_root: Path,
    workers_dir: Optional[Path] = None,
    layout_overrides: Optional[dict[str, Any]] = None,
) -> WorkerLayout:
    """Resolve worker layout for one delegation. See module docstring."""
    if not task_id:
        raise ResolveError("task_id is required")
    if not project_slug:
        raise ResolveError("project_slug is required")
    if mode not in VALID_MODES:
        raise ResolveError(f"mode must be one of {VALID_MODES}, got {mode!r}")

    # Normalize registry-display aliases to the canonical project slug.
    # ``claude-org-en`` is the 通称 (display name) for the ``claude-org``
    # mirror; downstream code only operates on the canonical form.
    project_slug = _CLAUDE_ORG_SLUG_ALIASES.get(project_slug, project_slug)

    targets = list(targets or [])
    claude_org_root = Path(claude_org_root).resolve()
    if registry_path is None:
        registry_path = claude_org_root / "registry" / "projects.md"
    if workers_dir is None:
        workers_dir = resolve_workers_dir(claude_org_root)

    # --- Project lookup ----------------------------------------------------
    if registry_path.exists():
        projects = parse_projects(registry_path)
    else:
        projects = []
    project = find_project(projects, project_slug)

    # claude-org-ja is intentionally absent from the checked-in registry
    # (the row used to leak a user-specific local path). When the slug +
    # claude_org_root's git origin both signal self-edit, synthesize a
    # virtual project pointing at the live checkout so audit mode and the
    # active-run/state.db driven Pattern A↔B logic below keep treating it
    # the same as a registered project.
    project_synthesized_for_self_edit = False
    if project is None and is_claude_org_project(project_slug, claude_org_root):
        project = RegistryProject(
            name=project_slug,
            nickname=project_slug,
            path=str(claude_org_root),
            description="",
            common_tasks="",
        )
        # Track that this row only exists because the auto-derived role is
        # self-edit. If a later layout_overrides flips the role away from
        # self-edit, the contract check below must not credit this row as
        # a worktree base — gen_delegate_payload re-reads the registry and
        # won't find it (Codex Round 1 Major).
        project_synthesized_for_self_edit = True

    # Issue #370: claude-org mirror at {workers_dir}/claude-org should
    # anchor any Pattern A/B for that repo regardless of whether the slug
    # is missing from the registry, present with a URL path (the live
    # deployment, where the row reads
    # ``| claude-org-en | claude-org | https://github.com/...``), or present
    # with a placeholder. We always re-pin onto the local clone when
    # detection succeeds — synthesizing a virtual project record when the
    # registry row is absent or carries a non-local path so downstream
    # Pattern A/B logic and ``gen_delegate_payload``'s ``base_repo``
    # derivation can reach it.
    claude_org_clone: Optional[Path] = find_claude_org_clone(
        project_slug, workers_dir
    )
    if claude_org_clone is not None and (
        project is None or not is_local_git_repo(project.path)
    ):
        project = RegistryProject(
            name=project_slug,
            nickname=project.nickname if project is not None else project_slug,
            path=str(claude_org_clone),
            description=project.description if project is not None else "",
            common_tasks=project.common_tasks if project is not None else "",
        )

    # --- Role decision (computed first so Pattern B can branch on it) -----
    role = decide_role(mode=mode, project_slug=project_slug, claude_org_root=claude_org_root)
    self_edit = role == "claude-org-self-edit"

    # --- Pattern decision --------------------------------------------------
    pattern: str
    variant: Optional[str]
    worker_dir: Path

    if project is None:
        # Unknown project → Pattern C ephemeral.
        pattern, variant = "C", "ephemeral"
        worker_dir = workers_dir / task_id
    elif is_local_git_repo(project.path) and any_target_is_gitignored(
        project.path, targets
    ):
        # Step 0.7 gitignored sub-mode → Pattern C / variant=gitignored_repo_root.
        pattern, variant = "C", "gitignored_repo_root"
        worker_dir = Path(project.path).resolve()
    else:
        # state.db driven A vs B decision. If DB read fails (missing file,
        # corrupt schema, etc.) we fall back to Pattern A — the safer default
        # for "no concurrent work known". The dispatcher / Stage 3 apply step
        # will re-validate before actually creating any worktree.
        active = False
        # Issue #370: legacy state.db rows may still carry the registry-display
        # alias ``claude-org-en`` (the post-migration canonical is
        # ``claude-org``). When the mirror is in play, gate on either form
        # so an active run recorded before normalization still forces
        # Pattern B for the new delegation.
        active_run_slugs: tuple[str, ...]
        if claude_org_clone is not None:
            active_run_slugs = (project_slug,) + tuple(
                k for k, v in _CLAUDE_ORG_SLUG_ALIASES.items() if v == project_slug
            )
        else:
            active_run_slugs = (project_slug,)
        if state_db_path is not None and Path(state_db_path).exists():
            try:
                conn = db_connect(state_db_path)
                try:
                    active = any(
                        project_has_active_run(conn, s) for s in active_run_slugs
                    )
                finally:
                    conn.close()
            except sqlite3.Error:
                active = False
        # Issue #374: two more reasons Pattern B is required even on a
        # first dispatch:
        # - ``self_edit`` is a *repo policy* (Secretary's live claude-org
        #   checkout must always be a worktree, single .git, no two-clone
        #   sync), independent of the concurrency policy that ``active``
        #   captures. Without this short-circuit the very first self-edit
        #   delegation lands on Pattern A and writes into the live repo.
        # - ``project.mirror_of`` flags a mirror back-port workflow — each
        #   task is independent, never accumulating, so worktree-per-task
        #   is the natural default. The 5-column legacy registry leaves
        #   this empty and behaviour is unchanged for those projects.
        # mirror_of additionally requires a local clone path: without one,
        # ``gen_delegate_payload`` cannot derive a worktree base, and apply
        # would fail with ``no usable base repo``. Surface that as a config
        # error here rather than after a DB reservation (Codex Round 1
        # Blocker).
        if project.mirror_of and not is_local_git_repo(project.path):
            raise ResolveError(
                f"project {project_slug!r} has mirror_of={project.mirror_of!r} "
                f"in the registry but path={project.path!r} is not a local "
                "git repo. Pattern B (the mirror back-port default) requires "
                "the mirror to be cloned at a usable local path; either "
                "register the local clone path or remove the mirror_of "
                "annotation."
            )
        force_b = active or self_edit or bool(project.mirror_of)
        if force_b:
            pattern = "B"
            if self_edit:
                variant = "live_repo_worktree"
                worker_dir = claude_org_root / ".worktrees" / task_id
            else:
                variant = None
                worker_dir = workers_dir / project_slug / ".worktrees" / task_id
        else:
            pattern, variant = "A", None
            worker_dir = workers_dir / project_slug

    worker_dir = worker_dir.resolve() if worker_dir.is_absolute() else worker_dir.resolve()

    # --- claude-org mirror anchoring (Issue #370) -------------------------
    # Re-pin worker_dir on the actual clone (which lives at
    # ``{workers_dir}/claude-org``). For Pattern B, also tag the variant so
    # gen_delegate_payload can derive base_repo from worker_dir.parent.parent
    # without needing a registry row. Skipped for Pattern C / gitignored
    # cases — those already use the synthesized project.path directly.
    if claude_org_clone is not None and pattern in ("A", "B"):
        if pattern == "B":
            variant = "claude_org_repo_worktree"
            worker_dir = (claude_org_clone / ".worktrees" / task_id).resolve()
        else:
            worker_dir = claude_org_clone.resolve()

    # --- TOML [worker] block overrides (Issue #290 defect 1) --------------
    # Honor explicit values from the caller (typically a worker_brief.toml
    # passed via gen_delegate_payload --from-toml) instead of letting the
    # auto-derive above override them. Priority order, highest first:
    #   TOML [worker] field > CLI flag > resolver auto-derive.
    # Applied BEFORE the branch decision so a TOML-supplied pattern flips
    # planned_branch consistently (e.g. pattern=C must imply planned_branch=None;
    # pattern=B/A must compute a feat-/fix- branch even if auto-derive
    # produced Pattern C without one). Codex Round 1 Major.
    if layout_overrides:
        explicit_worker_dir = bool(layout_overrides.get("worker_dir"))
        explicit_role_override = bool(layout_overrides.get("role"))
        if "pattern" in layout_overrides and layout_overrides["pattern"]:
            pat = layout_overrides["pattern"]
            if pat not in VALID_PATTERNS:
                raise ResolveError(
                    f"layout_overrides['pattern'] must be one of {VALID_PATTERNS}, got {pat!r}"
                )
            # Issue #374: ``--pattern`` is an override flag exposed for
            # Secretary judgment, so the contract must surface invalid combos
            # at preview time rather than letting apply discover them after
            # a DB reservation. The contract:
            #   - A is forbidden when the current role is claude-org-self-edit
            #     (Pattern A would land the brief in workers_dir/claude-org-ja/,
            #     a separate clone, which voids the single-.git invariant
            #     Issue #289 codified for the live repo).
            #   - B requires a worktree base — registered local clone OR the
            #     synthesized claude-org mirror clone OR self-edit (live repo
            #     base). When the only candidate is a URL/placeholder path
            #     and no clone is detected, fail loudly here so apply does
            #     not raise after the DB row exists.
            #   - C is always permitted.
            #   - The role override (if any) is applied later in this block,
            #     so consult the *post-override* role when its key is present;
            #     otherwise fall back to the auto-derived role/self_edit.
            effective_role = (
                layout_overrides["role"]
                if explicit_role_override
                else role
            )
            effective_self_edit = effective_role == "claude-org-self-edit"
            if pat == "A" and effective_self_edit:
                raise ResolveError(
                    "layout_overrides['pattern']='A' is incompatible with "
                    "role='claude-org-self-edit' (would dispatch into "
                    "{workers_dir}/claude-org-ja/, breaking the live-repo "
                    "single-.git invariant from Issue #289). Choose pattern=B "
                    "or override the role away from claude-org-self-edit."
                )
            if pat == "B":
                # Skip the base check when the caller is supplying their own
                # worker_dir or pattern_variant — those branches re-derive
                # the base further below (e.g. claude_org_repo_worktree).
                explicit_variant_now = (
                    layout_overrides.get("pattern_variant") is not None
                )
                if not (explicit_worker_dir or explicit_variant_now):
                    # The synthesized self-edit project record only exists
                    # in this resolve(); gen_delegate_payload re-reads the
                    # registry and finds nothing there for the slug. So if
                    # the role override is also flipping us out of
                    # self-edit, that synthesized record cannot back the
                    # plain Pattern B layout — drop it from the base check
                    # so we surface the contract error here rather than
                    # letting apply blow up later (Codex Round 1 Major).
                    project_for_base = project
                    if (
                        project_synthesized_for_self_edit
                        and not effective_self_edit
                    ):
                        project_for_base = None
                    has_base = (
                        effective_self_edit
                        or claude_org_clone is not None
                        or (
                            project_for_base is not None
                            and is_local_git_repo(project_for_base.path)
                        )
                    )
                    if not has_base:
                        raise ResolveError(
                            "layout_overrides['pattern']='B' requires a "
                            "resolvable worktree base, but none could be "
                            f"determined for project={project_slug!r}. "
                            "Pattern B needs one of: a registered project "
                            "row whose path is a local git repo, the "
                            "claude-org mirror clone, or role="
                            "'claude-org-self-edit' (live repo base). "
                            "Either register the project's local clone, set "
                            "role accordingly, or fall back to pattern=C."
                        )
            pattern = pat
            # Pattern explicitly set; reset variant unless TOML also supplied one.
            variant = layout_overrides.get("pattern_variant")
            if variant is not None and variant not in VALID_VARIANTS:
                raise ResolveError(
                    f"layout_overrides['pattern_variant'] must be one of {VALID_VARIANTS} or None, got {variant!r}"
                )
            # Pattern B + variant=live_repo_worktree without explicit worker_dir
            # → re-derive to claude_org_root/.worktrees/{task_id}/ (Issue #289).
            if pattern == "B" and variant == "live_repo_worktree" and not explicit_worker_dir:
                worker_dir = (claude_org_root / ".worktrees" / task_id).resolve()
            # Issue #370 (Codex Minor): same re-derivation for the
            # claude-org mirror variant — without it, an explicit override
            # leaves worker_dir at whatever auto-derive produced (often the
            # clone root) and gen_delegate_payload's
            # ``base_repo = worker_dir.parent.parent`` derivation lands on
            # the wrong directory.
            if pattern == "B" and variant == "claude_org_repo_worktree" and not explicit_worker_dir:
                clone_for_override = claude_org_clone or find_claude_org_clone(
                    project_slug, workers_dir
                )
                if clone_for_override is None:
                    raise ResolveError(
                        "layout_overrides requested pattern=B "
                        "variant=claude_org_repo_worktree but no claude-org "
                        f"clone was detected at {workers_dir}/"
                        f"{_CLAUDE_ORG_CLONE_DIRNAME} (slug={project_slug!r}). "
                        "Either supply layout_overrides['worker_dir'] "
                        "explicitly or clone the mirror at that path."
                    )
                worker_dir = (clone_for_override / ".worktrees" / task_id).resolve()
            # Issue #374: a plain ``--pattern B`` (no variant, no explicit
            # worker_dir) flips A → B for a registered project. The
            # auto-derived worker_dir is still the Pattern A path
            # (``workers_dir/<slug>/``); without re-deriving it here the
            # override would leave the brief at the clone root and apply
            # would refuse to treat it as a worktree. Skipped when
            # claude_org_clone has already pinned the clone-root path —
            # the post-override fallback below re-derives for that case.
            if (
                pattern == "B"
                and variant is None
                and not explicit_worker_dir
                and claude_org_clone is None
            ):
                worker_dir = (
                    workers_dir / project_slug / ".worktrees" / task_id
                ).resolve()
            # Same idea for ``--pattern A``: when the override drops
            # B → A, pin worker_dir back at the clone root rather than
            # leaving it in a stale ``.worktrees/<task_id>/`` path that
            # auto-derive built for Pattern B.
            if (
                pattern == "A"
                and not explicit_worker_dir
                and claude_org_clone is None
            ):
                worker_dir = (workers_dir / project_slug).resolve()
            # ``--pattern C`` override: ephemeral default. Without this
            # branch the override would dispatch into the *registered*
            # project's directory (auto-derive's Pattern A worker_dir),
            # silently re-using a clone instead of an ephemeral workspace.
            # Default variant is ``ephemeral`` (gitignored_repo_root needs
            # an explicit variant + targets at minimum and is left to the
            # auto-derive path).
            if (
                pattern == "C"
                and variant is None
                and not explicit_worker_dir
            ):
                variant = "ephemeral"
                worker_dir = (workers_dir / task_id).resolve()
        if "worker_dir" in layout_overrides and layout_overrides["worker_dir"]:
            worker_dir = Path(layout_overrides["worker_dir"]).resolve()
        if "role" in layout_overrides and layout_overrides["role"]:
            r = layout_overrides["role"]
            # Validate before any side effect (e.g. before apply reserves
            # the DB row): a malformed role used to leak through to
            # gen_worker_brief.validate() and only fail after the DB
            # reservation was already persisted (Codex Round 2 Major).
            if r not in VALID_ROLES:
                raise ResolveError(
                    f"layout_overrides['role'] must be one of {VALID_ROLES}, got {r!r}"
                )
            role = r
            self_edit = role == "claude-org-self-edit"
        if "self_edit" in layout_overrides:
            se = layout_overrides["self_edit"]
            # Strictly boolean — silently coercing a truthy string like
            # "false" would let a malformed TOML bypass downstream validate()
            # contracts (Codex Round 1 Minor).
            if not isinstance(se, bool):
                raise ResolveError(
                    f"layout_overrides['self_edit'] must be bool, got {type(se).__name__}"
                )
            self_edit = se

        # role / self_edit must agree. Otherwise a caller could pass
        # role='default' + self_edit=true and the coherence pass below would
        # relocate the worktree under claude_org_root while
        # `settings generate --role default` still emits non-self-edit
        # permissions (Codex Round 3 Major).
        if self_edit != (role == "claude-org-self-edit"):
            raise ResolveError(
                "layout_overrides yielded inconsistent role / self_edit: "
                f"role={role!r}, self_edit={self_edit!r}. "
                "self_edit must be True iff role == 'claude-org-self-edit'."
            )

        # Final coherence pass for Issue #289: if overrides upgraded the role
        # to claude-org-self-edit on a Pattern B layout but did not also
        # specify pattern_variant / worker_dir, re-derive them so the live
        # repo convention holds. Skipped when the caller explicitly supplied
        # either (their value wins). Keyed off ``role`` (not ``self_edit``)
        # because role is the field the downstream settings generator reads
        # — keeping them in sync prevents a mismatched permission profile
        # from being applied to a self-edit worktree (Codex Round 3 Major).
        explicit_variant = "pattern_variant" in layout_overrides and layout_overrides.get("pattern_variant") is not None
        if (
            role == "claude-org-self-edit"
            and pattern == "B"
            and not explicit_variant
            and not explicit_worker_dir
        ):
            variant = "live_repo_worktree"
            worker_dir = (claude_org_root / ".worktrees" / task_id).resolve()

    # --- Branch decision ---------------------------------------------------
    # Re-derived from the *final* pattern so a TOML-supplied pattern override
    # flips planned_branch consistently.
    if pattern == "C":
        # Pattern C ephemeral has no branch (no git); gitignored sub-mode
        # runs against the repo root's existing branch and must not invent
        # a new one (Codex M-2: planned_branch is a *suggestion*, not a
        # commitment, and for Pattern C the suggestion is "don't").
        planned_branch: Optional[str] = None
    else:
        planned_branch = branch_override or infer_branch(task_id, description)
    if layout_overrides and "planned_branch" in layout_overrides:
        planned_branch = layout_overrides["planned_branch"]

    # --- settings_args -----------------------------------------------------
    settings_args = {
        "role": role,
        "worker-dir": str(worker_dir),
        "claude-org-path": str(claude_org_root),
        "out": str(worker_dir / ".claude" / "settings.local.json"),
    }

    return WorkerLayout(
        pattern=pattern,
        pattern_variant=variant,
        worker_dir=str(worker_dir),
        role=role,
        self_edit=self_edit,
        planned_branch=planned_branch,
        settings_args=settings_args,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve worker layout for a delegation (org-delegate Step 0.7/1/1.5 codified).",
    )
    p.add_argument("--task-id", required=True)
    p.add_argument("--project-slug", required=True)
    p.add_argument(
        "--target",
        action="append",
        default=[],
        help="Target file path for Step 0.7 gitignore check (repeatable).",
    )
    p.add_argument("--description", default="")
    p.add_argument(
        "--mode",
        choices=VALID_MODES,
        default="edit",
        help="'edit' selects claude-org-self-edit or default; 'audit' forces doc-audit.",
    )
    p.add_argument("--branch", dest="branch_override", default=None,
                   help="Override the inferred planned_branch.")
    p.add_argument("--registry-path", default=None, type=Path)
    p.add_argument("--state-db-path", default=None, type=Path)
    p.add_argument("--claude-org-root", default=None, type=Path,
                   help="Path to claude-org repo root (default: auto-detected).")
    p.add_argument("--workers-dir", default=None, type=Path,
                   help="Override workers_dir (default: read from registry/org-config.md).")
    return p


def _detect_claude_org_root() -> Path:
    """Walk up from CWD until a registry/projects.md is found."""
    here = Path.cwd().resolve()
    for cand in (here, *here.parents):
        if (cand / "registry" / "projects.md").exists() and (cand / ".state").exists():
            return cand
    # Fallback: just use CWD; resolve() will surface an error later.
    return here


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    claude_org_root = args.claude_org_root or _detect_claude_org_root()
    state_db_path = args.state_db_path
    if state_db_path is None:
        candidate = claude_org_root / ".state" / "state.db"
        state_db_path = candidate if candidate.exists() else None
    try:
        layout = resolve(
            task_id=args.task_id,
            project_slug=args.project_slug,
            targets=args.target,
            description=args.description,
            mode=args.mode,
            branch_override=args.branch_override,
            registry_path=args.registry_path,
            state_db_path=state_db_path,
            claude_org_root=claude_org_root,
            workers_dir=args.workers_dir,
        )
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    json.dump(layout.to_json_dict(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
