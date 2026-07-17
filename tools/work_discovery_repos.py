"""Resolve the ``--repo owner/repo`` set for a work-discovery triage scan.

Read-only helper (Issue #729). Turns the ``registry/projects.md`` triage
opt-in column plus the always-included home repo (claude-org-ja itself)
into a deterministic list of ``owner/repo`` slugs for
``tools/work_discovery_scan.py --repo``.

Two inputs drive the set:

- **home repo (always included, first)** — resolved in two stages:
  1. ``git -C <claude_org_root> remote get-url origin`` -> owner/repo.
  2. fallback ``gh repo view --json nameWithOwner`` when (1) fails.
  Both failing emits a loud signal (non-fatal) and the home repo is
  simply absent from the set.
- **triage opt-in rows** — rows in ``registry/projects.md`` whose
  ``triage`` column reads ``yes`` / ``true`` / ``on`` (case-insensitive,
  trimmed). The row's ``パス`` (path) column must be a GitHub URL so an
  ``owner/repo`` can be derived; local paths / ``-`` are skipped and left
  in a ``skipped`` audit signal (they cannot back a ``--repo`` slug).

Output (stdout):

- ``--format json`` (default): one JSON object with ``repos``,
  ``home_repo``, ``opted_in``, ``skipped``, ``signals`` (and ``error`` on
  failure).
- ``--format flags``: ``--repo a/b --repo c/d`` on a single line for shell
  splicing; ``skipped`` / ``signals`` go to stderr so stdout stays pure.

Exit code: ``0`` when the set contains at least the home repo, ``2`` on
error (empty set / read failure). The output is deterministic and this
tool performs no writes / spawns / git mutations (read-only ``git remote
get-url`` and optional ``gh repo view`` only).
"""
from __future__ import annotations

# Match resolve_worker_layout.py: allow ``python tools/work_discovery_repos.py``
# (sys.path[0] == tools/) to still import the ``tools`` package by inserting
# the repo root. Harmless when imported as a module.
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from tools.registry_parser import parse_projects_text
from tools.resolve_worker_layout import (
    _GITHUB_OWNER_REPO_RE,
    _git_origin_url,
)

# triage cell values (case-folded, trimmed) that count as opt-in. Anything
# else -- ``no`` / empty / ``-`` -- is treated as not opted in.
_OPT_IN_VALUES = frozenset({"yes", "true", "on"})


def _owner_repo_from_url(url: Optional[str]) -> Optional[str]:
    """Return lowercased ``owner/repo`` from a GitHub URL, else ``None``.

    Reuses ``resolve_worker_layout._GITHUB_OWNER_REPO_RE`` directly (group 1
    = owner, group 2 = repo) rather than the ``_extract_github_repo_name``
    wrapper, which lowercases and returns the repo only (owner dropped).
    Non-GitHub strings (local paths, ``-``) return ``None`` so callers skip
    them. Output is lowercased so the engine's closing-issue join (which
    ``.lower()``-compares repo slugs) stays consistent regardless of the
    registry's casing.
    """
    if not url:
        return None
    s = url.strip().lower()
    if "github.com" not in s:
        return None
    # Match on the lowercased URL so a mixed-case host (e.g. `GitHub.com`) is
    # still recognised; the output is lowercased anyway, so casefolding the
    # input first does not change the resolved owner/repo.
    m = _GITHUB_OWNER_REPO_RE.search(s)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def _gh_home_repo(claude_org_root: Path) -> Optional[str]:
    """Fallback home-repo resolution via ``gh repo view``. Read-only.

    Runs with ``cwd=claude_org_root`` so ``gh`` resolves the intended repo
    rather than whatever directory the process happens to be launched from.
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(claude_org_root),
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    name = data.get("nameWithOwner")
    if not isinstance(name, str) or "/" not in name:
        return None
    return name.strip().lower() or None


def _resolve_home_repo(
    claude_org_root: Path, signals: list[str]
) -> Optional[str]:
    """Two-stage home-repo resolution (git origin, then ``gh repo view``).

    Appends a loud signal and returns ``None`` when both stages fail. The
    home repo is meant to always resolve; a ``None`` here is an anomaly the
    caller should surface, not silently swallow.
    """
    origin = _git_origin_url(claude_org_root)
    home = _owner_repo_from_url(origin)
    if home is not None:
        return home
    home = _gh_home_repo(claude_org_root)
    if home is not None:
        signals.append(
            "home repo resolved via 'gh repo view' fallback "
            "(git origin URL was unavailable or non-GitHub)"
        )
        return home
    signals.append(
        "could not resolve home repo from git origin or 'gh repo view' -- "
        "home repo NOT included in the --repo set (scan will be "
        "home-relative or empty)"
    )
    return None


def resolve_repos(
    *, registry_path: Path, claude_org_root: Path
) -> dict:
    """Build the repo-set result dict. Pure read-only computation."""
    signals: list[str] = []
    opted_in: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    home_repo = _resolve_home_repo(claude_org_root, signals)

    if registry_path.exists():
        text = registry_path.read_text(encoding="utf-8")
        projects = parse_projects_text(text)
    else:
        projects = []
        signals.append(
            f"registry not found at {registry_path} -- only the home repo "
            "will be scanned"
        )

    for proj in projects:
        if proj.triage.strip().lower() not in _OPT_IN_VALUES:
            continue
        repo = _owner_repo_from_url(proj.path)
        if repo is None:
            reason = (
                f"triage opt-in row '{proj.nickname}' path '{proj.path}' -- "
                "skipped (cannot derive owner/repo; expected a bare "
                "https://github.com/OWNER/REPO clone URL)"
            )
            skipped.append(
                {"nickname": proj.nickname, "path": proj.path, "reason": reason}
            )
            signals.append(reason)
            continue
        opted_in.append(
            {"nickname": proj.nickname, "repo": repo, "path": proj.path}
        )

    # Dedup preserving order; home first.
    repos: list[str] = []
    seen: set[str] = set()
    for candidate in ([home_repo] if home_repo else []) + [
        row["repo"] for row in opted_in
    ]:
        if candidate not in seen:
            seen.add(candidate)
            repos.append(candidate)

    result: dict = {
        "repos": repos,
        "home_repo": home_repo,
        "opted_in": opted_in,
        "skipped": skipped,
        "signals": signals,
    }
    if not repos:
        result["error"] = (
            "no repos resolved (home repo unresolvable and no valid triage "
            "opt-in rows)"
        )
    return result


def _emit(result: dict, fmt: str) -> None:
    """Write the result to stdout (and stderr for the flags side-channel)."""
    if fmt == "flags":
        parts: list[str] = []
        for repo in result["repos"]:
            parts.append("--repo")
            parts.append(repo)
        sys.stdout.write(" ".join(parts))
        if parts:
            sys.stdout.write("\n")
        # skip/signal detail goes to stderr so stdout stays pure flags.
        for row in result.get("skipped", []):
            sys.stderr.write(f"skipped: {row['reason']}\n")
        for sig in result.get("signals", []):
            sys.stderr.write(f"signal: {sig}\n")
        if "error" in result:
            sys.stderr.write(f"error: {result['error']}\n")
    else:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Resolve the --repo owner/repo set for a work-discovery triage "
            "scan from registry/projects.md triage opt-in rows plus the "
            "always-included home repo. Read-only."
        ),
    )
    p.add_argument(
        "--registry",
        default=None,
        type=Path,
        help="Path to registry/projects.md (default: <root>/registry/projects.md).",
    )
    p.add_argument(
        "--claude-org-root",
        default=None,
        type=Path,
        help="Path to the claude-org repo root (default: repo root / cwd).",
    )
    p.add_argument(
        "--format",
        choices=("json", "flags"),
        default="json",
        help=(
            "'json' (default) prints the full result object; 'flags' prints "
            "'--repo a/b --repo c/d' for shell splicing (signals to stderr)."
        ),
    )
    return p


def _default_claude_org_root() -> Path:
    """Repo root = this file's grandparent (tools/ -> root)."""
    return Path(__file__).resolve().parent.parent


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    claude_org_root = (args.claude_org_root or _default_claude_org_root()).resolve()
    registry_path = (
        args.registry
        if args.registry is not None
        else claude_org_root / "registry" / "projects.md"
    )
    try:
        result = resolve_repos(
            registry_path=Path(registry_path),
            claude_org_root=claude_org_root,
        )
    except OSError as e:  # registry read failure etc.
        err = {
            "repos": [],
            "home_repo": None,
            "opted_in": [],
            "skipped": [],
            "signals": [],
            "error": f"failed to resolve repos: {e}",
        }
        _emit(err, args.format)
        print(f"error: {err['error']}", file=sys.stderr)
        return 2
    _emit(result, args.format)
    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
