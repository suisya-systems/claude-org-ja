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
      "pattern_variant": "ephemeral" | "gitignored_repo_root" | None,
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
- ``pattern_variant`` distinguishes the two Pattern C sub-modes (M-1).
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

from tools.state_db import connect as db_connect
from tools.state_db.queries import list_runs_with_dirs


VALID_MODES = ("edit", "audit")
VALID_PATTERNS = ("A", "B", "C")
VALID_VARIANTS = ("ephemeral", "gitignored_repo_root")
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


@dataclass(frozen=True)
class RegistryProject:
    """Single row of registry/projects.md, for resolver-internal use."""
    common_name: str        # 通称
    slug: str               # プロジェクト名 (machine slug)
    path: str               # local abs path, URL, or '-'
    description: str


@dataclass(frozen=True)
class WorkerLayout:
    pattern: str                       # "A" | "B" | "C"
    pattern_variant: Optional[str]     # "ephemeral" | "gitignored_repo_root" | None
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
# registry/projects.md parser
# ---------------------------------------------------------------------------
#
# TODO(#286): replace the ad-hoc parser below with the shared parser from
# tools/registry_parser when Issue #286 lands. dashboard/server.py and
# tools/state_db/importer.py have their own parsers today; #286 extracts a
# single SoT module that all three call. Until then this resolver carries
# the third (intentionally minimal) implementation, and we accept the drift
# risk for the duration of one PR.

_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|\s*$")


def parse_registry(text: str) -> list[RegistryProject]:
    """Parse the markdown table in registry/projects.md.

    Tolerates leading prose / multiple tables. Returns rows with at
    least 4 columns (通称 / プロジェクト名 / パス / 説明). The 5th
    column ('よくある作業例') is dropped — resolver doesn't use it.
    """
    rows: list[RegistryProject] = []
    in_table = False
    header_seen = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if _TABLE_SEP_RE.match(line):
            in_table = True
            header_seen = True
            continue
        if not in_table:
            # Header line that precedes the |---| separator — capture so
            # malformed tables (separator missing) still don't accidentally
            # parse.  We only flip in_table on the separator.
            if line.startswith("|"):
                header_seen = True
            continue
        if not line.startswith("|"):
            # blank line or end-of-table prose
            in_table = False
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 4:
            continue
        common_name, slug, path, description = cols[0], cols[1], cols[2], cols[3]
        if not slug:
            continue
        rows.append(
            RegistryProject(
                common_name=common_name,
                slug=slug,
                path=path,
                description=description,
            )
        )
    if not header_seen:
        return []
    return rows


def find_project(rows: Iterable[RegistryProject], slug: str) -> Optional[RegistryProject]:
    for r in rows:
        if r.slug == slug:
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


def is_claude_org_project(project: Optional[RegistryProject], claude_org_root: Path) -> bool:
    """True iff the registered project resolves to the same dir as claude-org root."""
    if project is None:
        return False
    if not project.path or project.path == "-" or "://" in project.path:
        return False
    try:
        return Path(project.path).resolve() == claude_org_root.resolve()
    except (OSError, RuntimeError):
        return False


def decide_role(
    *,
    mode: str,
    project: Optional[RegistryProject],
    claude_org_root: Path,
) -> str:
    if mode == "audit":
        return "doc-audit"
    if mode == "edit":
        if is_claude_org_project(project, claude_org_root):
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
) -> WorkerLayout:
    """Resolve worker layout for one delegation. See module docstring."""
    if not task_id:
        raise ResolveError("task_id is required")
    if not project_slug:
        raise ResolveError("project_slug is required")
    if mode not in VALID_MODES:
        raise ResolveError(f"mode must be one of {VALID_MODES}, got {mode!r}")

    targets = list(targets or [])
    claude_org_root = Path(claude_org_root).resolve()
    if registry_path is None:
        registry_path = claude_org_root / "registry" / "projects.md"
    if workers_dir is None:
        workers_dir = resolve_workers_dir(claude_org_root)

    # --- Project lookup ----------------------------------------------------
    registry_text = ""
    if registry_path.exists():
        registry_text = registry_path.read_text(encoding="utf-8")
    projects = parse_registry(registry_text)
    project = find_project(projects, project_slug)

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
        if state_db_path is not None and Path(state_db_path).exists():
            try:
                conn = db_connect(state_db_path)
                try:
                    active = project_has_active_run(conn, project_slug)
                finally:
                    conn.close()
            except sqlite3.Error:
                active = False
        if active:
            pattern, variant = "B", None
            worker_dir = workers_dir / project_slug / ".worktrees" / task_id
        else:
            pattern, variant = "A", None
            worker_dir = workers_dir / project_slug

    worker_dir = worker_dir.resolve() if worker_dir.is_absolute() else worker_dir.resolve()

    # --- Role decision -----------------------------------------------------
    role = decide_role(mode=mode, project=project, claude_org_root=claude_org_root)
    self_edit = role == "claude-org-self-edit"

    # --- Branch decision ---------------------------------------------------
    if pattern == "C":
        # Pattern C ephemeral has no branch (no git); gitignored sub-mode
        # runs against the repo root's existing branch and must not invent
        # a new one (Codex M-2: planned_branch is a *suggestion*, not a
        # commitment, and for Pattern C the suggestion is "don't").
        planned_branch: Optional[str] = None
    else:
        planned_branch = branch_override or infer_branch(task_id, description)

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
