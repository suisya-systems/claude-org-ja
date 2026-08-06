"""Deterministic ``OWNER/REPO`` resolution for a run's PR (Issue #828).

``tools/set_run_pr_open.py`` and ``tools/run_complete_on_merge.py`` used to
default to ``gh repo view`` (i.e. the cwd repo, which for the secretary is
always claude-org-ja) whenever ``--repo`` was omitted. For a cross-repo run
that silently pointed ``gh pr view <N>`` at **ja's** PR #N: when ja happened
to have a PR with the same number, its branch / commit / mergedAt were
written onto the foreign run's row and the tool still exited ``ok``. That
actually happened on 2026-08-06 (renga PR #302 recorded with ja PR #302's
metadata), and whether it corrupted silently or failed loudly depended only
on whether ja owned that number.

A run already carries the answer: ``runs.project_id`` -> ``projects`` ->
the project's GitHub URL. This module makes that path the default and
**never falls back to the home repo as a catch-all** -- the home repo is
used only when the run is positively identified as a claude-org-ja
self-edit run, which is a correct resolution rather than a guess.

Resolution order (first hit wins):

1. ``registry/projects.md`` -- the operator-maintained SoT. Matched on the
   ``プロジェクト名`` (slug) column first, then the ``通称`` (nickname)
   column, because runs in the wild carry both forms (e.g. the kura project
   has runs under ``kura`` and under ``kura-data-aggregator-trial``).
2. ``projects.origin_url`` in state.db -- a derivative of (1) written by
   ``tools/state_db/importer.py`` at legacy-import time. Second because it
   can be stale relative to the live registry, but it still covers projects
   whose registry row was dropped.
3. the home repo -- only when
   :func:`tools.resolve_worker_layout.is_claude_org_project` positively
   identifies the run's project as claude-org-ja self-edit, resolved from
   ``claude_org_root``'s git origin.

Anything else raises :class:`RepoResolutionError` so the caller can exit
non-zero instead of writing a foreign repo's PR onto the run.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.registry_parser import parse_projects_text  # noqa: E402
from tools.resolve_worker_layout import (  # noqa: E402
    _GITHUB_OWNER_REPO_RE,
    _git_origin_url,
    is_claude_org_project,
)

# Case-preserving twin of ``resolve_worker_layout._GITHUB_OWNER_REPO_RE``.
# That module lowercases the URL before matching (its callers only need a
# normalized repo *name*), but we hand the result to ``gh pr view --repo``
# and record it in the ``pr_merged`` event payload, where a case-folded
# ``OWNER/REPO`` would no longer match the real ``pr_url`` string that
# ``run_complete_on_merge._resolve_task_id`` LIKE-compares against. Reusing
# the shared pattern (rather than copying it) keeps a future fix there --
# e.g. the Issue #450 ``:port`` follow-up -- propagating here; IGNORECASE
# only affects the literal ``github.com`` host, since the owner / repo
# groups are negated character classes.
_GITHUB_OWNER_REPO_RE_CI = re.compile(
    _GITHUB_OWNER_REPO_RE.pattern, re.IGNORECASE
)

# ``RepoResolution.source`` values. Stable strings so callers (stdout
# notices, tests) can branch without parsing prose.
SOURCE_EXPLICIT = "explicit"        # operator passed --repo
SOURCE_REGISTRY = "registry"        # registry/projects.md path column
SOURCE_DB_ORIGIN = "db_origin_url"  # projects.origin_url in state.db
SOURCE_HOME_REPO = "home_repo"      # claude-org-ja self-edit run
SOURCE_GH_CWD = "gh_cwd"            # `gh repo view` (no --task-id to resolve from)


class RepoResolutionError(RuntimeError):
    """The run's repo could not be determined; the caller must not guess."""


class RunNotFound(RepoResolutionError):
    """No ``runs`` row for the given ``task_id``.

    A distinct type because the CLIs already have a ``no_run`` terminal
    (exit 3) for this case and must keep reporting it as such rather than
    as a repo-resolution failure.
    """


@dataclass(frozen=True)
class RepoResolution:
    """A resolved ``OWNER/REPO`` plus where it came from."""

    repo: str
    source: str
    project_slug: Optional[str] = None


def owner_repo_from_url(url: Optional[str]) -> Optional[str]:
    """Return ``OWNER/REPO`` from a GitHub URL preserving case, else None.

    Non-GitHub values (local paths, the ``-`` placeholder, empty cells)
    return None so callers fall through to the next resolution step.
    """
    if not url:
        return None
    s = url.strip()
    if "github.com" not in s.lower():
        return None
    m = _GITHUB_OWNER_REPO_RE_CI.search(s)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def _repo_from_registry(
    project_slug: str, registry_path: Path
) -> Optional[str]:
    """Return the project's ``OWNER/REPO`` from ``registry/projects.md``.

    Rows are matched on the slug column first and only then on the
    nickname column, so a nickname that collides with another project's
    slug can never outrank an exact slug match. When several matching rows
    disagree about the repo we raise instead of picking one -- an ambiguous
    registry is exactly the situation where guessing reintroduces Issue
    #828.
    """
    if not registry_path.exists():
        return None
    try:
        text = registry_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepoResolutionError(
            f"could not read the project registry at {registry_path}: {exc}"
        ) from None
    projects = parse_projects_text(text)

    for attr in ("name", "nickname"):
        repos = {
            repo
            for p in projects
            if getattr(p, attr) == project_slug
            and (repo := owner_repo_from_url(p.path)) is not None
        }
        if len(repos) > 1:
            raise RepoResolutionError(
                f"{registry_path} maps project {project_slug!r} to more than "
                f"one GitHub repo ({', '.join(sorted(repos))}); refusing to "
                "guess. Fix the duplicate registry rows, or pass "
                "--repo OWNER/REPO explicitly."
            )
        if repos:
            return repos.pop()
    return None


def resolve_repo_for_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    claude_org_root: Path,
    registry_path: Optional[Path] = None,
) -> RepoResolution:
    """Resolve the GitHub repo that owns ``task_id``'s PR.

    Raises :class:`RunNotFound` when there is no run row and
    :class:`RepoResolutionError` when the repo cannot be determined. Never
    returns the home repo as a fallback -- see the module docstring.
    """
    if registry_path is None:
        registry_path = Path(claude_org_root) / "registry" / "projects.md"

    try:
        row = conn.execute(
            "SELECT p.slug AS slug, p.origin_url AS origin_url "
            "FROM runs r LEFT JOIN projects p ON p.id = r.project_id "
            "WHERE r.task_id = ?",
            (task_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise RepoResolutionError(
            f"could not query the run row for task_id={task_id!r}: {exc}"
        ) from None

    if row is None:
        raise RunNotFound(f"no run row for task_id={task_id!r}")

    project_slug = row["slug"]
    if not project_slug:
        raise RepoResolutionError(
            f"run {task_id!r} has no project row to resolve a repo from "
            "(runs.project_id does not join to projects); pass "
            "--repo OWNER/REPO explicitly."
        )

    repo = _repo_from_registry(project_slug, Path(registry_path))
    if repo is not None:
        return RepoResolution(repo, SOURCE_REGISTRY, project_slug)

    repo = owner_repo_from_url(row["origin_url"])
    if repo is not None:
        return RepoResolution(repo, SOURCE_DB_ORIGIN, project_slug)

    # Positive identification only. `is_claude_org_project` requires BOTH
    # the canonical self-edit slug AND a claude-org origin on
    # claude_org_root, so a foreign project can never land here and inherit
    # the home repo.
    if is_claude_org_project(project_slug, Path(claude_org_root)):
        home = owner_repo_from_url(_git_origin_url(Path(claude_org_root)))
        if home is not None:
            return RepoResolution(home, SOURCE_HOME_REPO, project_slug)
        raise RepoResolutionError(
            f"run {task_id!r} is a {project_slug} self-edit run but the git "
            f"origin of {claude_org_root} could not be read as a GitHub URL; "
            "pass --repo OWNER/REPO explicitly."
        )

    raise RepoResolutionError(
        f"cannot determine the GitHub repo for task_id={task_id!r} "
        f"(project={project_slug!r}): {registry_path} has no GitHub URL for "
        "it, projects.origin_url is unset, and it is not a claude-org-ja "
        "self-edit run. Refusing to fall back to the home repo -- a "
        "same-numbered PR there would be silently written onto this run "
        "(Issue #828). Pass --repo OWNER/REPO explicitly, or add the "
        "project's GitHub URL to the registry."
    )


def infer_claude_org_root(db_path: Path) -> Path:
    """Locate the claude-org checkout that ``db_path`` belongs to.

    In the canonical ``<root>/.state/state.db`` layout the DB's grandparent
    *is* the root, and using it keeps resolution self-consistent with the DB
    actually being written (the convention ``run_complete_on_merge`` already
    follows for its Pattern C cleanup).

    ``--db-path`` / ``STATE_DB_PATH`` may point anywhere, though, and then
    the grandparent is an arbitrary directory -- looking for
    ``registry/projects.md`` and a git origin there would find neither and
    turn every claude-org-ja self-edit run into a hard exit 2 (self-edit runs
    are absent from the registry by contract and normally have no
    ``projects.origin_url``, so the home-repo branch is the only one that can
    resolve them). Fall back to the same cwd-walk discovery the rest of the
    tooling uses in that case.
    """
    db_path = Path(db_path)
    if db_path.parent.name == ".state":
        return db_path.parent.parent
    from tools.state_db.discover import discover_repo_root

    try:
        return discover_repo_root(start=Path.cwd())
    except (RuntimeError, OSError):
        return db_path.parent.parent


def resolve_repo_for_task_at(
    db_path: Path,
    task_id: str,
    *,
    claude_org_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> RepoResolution:
    """:func:`resolve_repo_for_task` against a state.db path.

    ``claude_org_root`` defaults to :func:`infer_claude_org_root`.
    """
    db_path = Path(db_path)
    if claude_org_root is None:
        claude_org_root = infer_claude_org_root(db_path)
    if not db_path.exists():
        raise RunNotFound(
            f"no state.db at {db_path}; cannot resolve the repo for "
            f"task_id={task_id!r}"
        )

    from tools.state_db import connect

    conn = connect(db_path)
    try:
        return resolve_repo_for_task(
            conn,
            task_id,
            claude_org_root=Path(claude_org_root),
            registry_path=registry_path,
        )
    finally:
        conn.close()
