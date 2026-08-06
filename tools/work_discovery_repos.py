"""Resolve the ``--repo owner/repo`` set for a work-discovery triage scan.

Read-only helper (Issue #729; the default was inverted in Issue #801).
Turns the ``registry/projects.md`` project rows plus the ``triage_home``
opt-in in ``registry/org-config.md`` into a deterministic list of
``owner/repo`` slugs for ``tools/work_discovery_scan.py --repo``.

Two inputs drive the set:

- **registry rows (scanned by default)** -- every row in
  ``registry/projects.md`` is in the scan set unless its ``triage`` cell
  reads ``no`` / ``off`` / ``false`` (case-insensitive, trimmed), which
  opts that row out. Empty / ``-`` / ``yes`` / ``true`` / ``on`` mean
  included; any other value is still included but leaves an audit signal.
  An included row's ``パス`` (path) column must be a GitHub URL so an
  ``owner/repo`` can be derived; local paths / ``-`` cannot back a
  ``--repo`` slug and land in ``skipped`` with a signal. Rows opted out
  explicitly land in ``opted_out`` and emit no skip signal.
- **home repo (opt-in, off by default)** -- claude-org-ja itself never
  appears in the registry table, so ``triage_home`` in
  ``registry/org-config.md`` decides whether it joins the set (missing
  file / missing key / unrecognised value all mean off, never fatal).
  Only when it is on does the resolver run the two-stage lookup:
  1. ``git -C <claude_org_root> remote get-url origin`` -> owner/repo.
  2. fallback ``gh repo view --json nameWithOwner`` when (1) fails.
  Both failing emits a loud signal (non-fatal). When included, the home
  repo comes first in the set.

Output (stdout):

- ``--format json`` (default): one JSON object with ``repos``,
  ``home_repo``, ``triage_home``, ``included``, ``opted_out``,
  ``skipped``, ``base_branches``, ``signals`` (and ``error`` on failure).
- ``--format flags``: ``--repo a/b --repo c/d`` on a single line for shell
  splicing; ``skipped`` / ``signals`` go to stderr so stdout stays pure.

  **Shell-dependent — do not use it to drive a scan** (Issue #829). The
  string only becomes several argv entries if the calling shell word-splits
  an unquoted expansion, and zsh (the org panes' login shell) has
  ``SH_WORD_SPLIT`` off by default: ``FLAGS=$(… --format flags); scan
  $FLAGS`` reaches argparse as **one** argument under zsh (it needs the
  zsh-only ``${=FLAGS}``) while bash splits it into four. The portable way
  to scan the registry set is ``work_discovery_scan.py
  --all-registry-repos``, which calls ``resolve_repos()`` in-process with no
  shell in the path. ``--format flags`` is retained for interactive use
  (eyeballing the set, pasting the flags into a command by hand).

Exit code: ``0`` when at least one repo resolved, ``2`` on error (empty
set / read failure). The output is deterministic and this tool performs
no writes / spawns / git mutations (read-only ``git remote get-url`` and
optional ``gh repo view`` only -- and neither runs while ``triage_home``
is off).
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
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from tools.registry_parser import parse_projects_text
from tools.resolve_worker_layout import (
    _GITHUB_OWNER_REPO_RE,
    _git_origin_url,
)

# triage cell values (case-folded, trimmed) that opt a row OUT of the scan set.
_OPT_OUT_VALUES = frozenset({"no", "off", "false"})
# triage cell values that are recognised as "include" (the default). Anything
# outside both sets is still included but leaves an audit signal.
_INCLUDE_VALUES = frozenset({"", "-", "yes", "true", "on"})
# org-config triage_home values.
_TRIAGE_HOME_ON_VALUES = frozenset({"yes", "true", "on"})
_TRIAGE_HOME_OFF_VALUES = frozenset({"no", "false", "off"})

# Anchored at column 0 on purpose (no leading ``\s*``): registry/org-config.md
# prose quotes forms like `triage_home: on` inside bullets / backticks, and
# only a real setting line -- which starts at column 0 -- may be picked up.
# The inner whitespace classes are *horizontal only* (``[ \t]``) so the match
# can never cross a line break: with ``\s*`` a bare ``triage_home:`` would
# swallow the following blank line and capture the next prose line as its
# value (silently turning the home repo on when that line reads ``on``).
# Trailing whitespace is left to the caller's ``.strip()``.
_TRIAGE_HOME_RE = re.compile(r"^triage_home[ \t]*:[ \t]*(.*)$", re.MULTILINE)


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


def _normalize_base_branch(value: Optional[str]) -> Optional[str]:
    """Normalize a registry ``base_branch`` cell to a bare branch name.

    Delegates to :func:`tools.gen_delegate_payload.normalize_base_branch` —
    the single source of truth for that normalization since Issue #808 (trim,
    tolerated ``origin/`` prefix, ``""`` / ``-`` = unset) — so the triage scan
    and the delegation pipeline can never disagree on what ``develop`` means.

    The import is **lazy** on purpose: ``gen_delegate_payload`` is the heavy
    delegation planner (it pulls in the brief generator, the layout resolver
    and the transport module), and this file is a read-only resolver imported
    by the scan on every run. Importing it at module scope would make the
    triage path depend on the whole delegation stack loading cleanly for a
    three-line string normalization.
    """
    from tools.gen_delegate_payload import normalize_base_branch

    return normalize_base_branch(value)


def _base_branches_from_projects(projects) -> dict[str, str]:
    """Map ``owner/repo`` -> normalized ``base_branch`` over parsed rows.

    Issue #830. Only rows that (a) yield an ``owner/repo`` from their path and
    (b) declare a non-empty ``base_branch`` appear; every other row is absent,
    which is what "unset = historical behaviour" means downstream.

    Rows opted out of triage are **still** mapped: the ``triage`` column
    governs whether a repo is auto-scanned, not what its base branch *is*, so
    an explicit ``--repo`` scan of an opted-out repo still gets the correct
    base branch. The first row for a repo wins (deterministic on duplicates).
    """
    out: dict[str, str] = {}
    for proj in projects:
        repo = _owner_repo_from_url(proj.path)
        if repo is None or repo in out:
            continue
        branch = _normalize_base_branch(proj.base_branch)
        if branch:
            out[repo] = branch
    return out


def resolve_base_branches(registry_path: Path) -> dict[str, str]:
    """Read ``registry/projects.md`` and return ``{owner/repo: base_branch}``.

    Issue #830. Standalone entry point for callers that already know their
    repo set (``work_discovery_scan.py --repo …``) and only need the base
    branches; ``resolve_repos`` computes the same map from the rows it has
    already parsed. A missing registry yields ``{}`` (no base branches
    configured), matching the resolver's own "no registry rows" degradation.
    Read-only.
    """
    path = Path(registry_path)
    if not path.exists():
        return {}
    return _base_branches_from_projects(
        parse_projects_text(path.read_text(encoding="utf-8"))
    )


def _read_triage_home(org_config_path: Path, signals: list[str]) -> bool:
    """Return True only when org-config opts the home repo into the scan set.

    Every failure mode falls back to ``False`` (off) rather than raising:
    the home repo is an opt-in extra, so an unreadable / malformed config
    must not take the whole registry-driven scan down with it. A missing
    key is the documented default and stays silent; a missing file or an
    unrecognised value leaves an audit signal.
    """
    if not org_config_path.exists():
        signals.append(
            f"org-config not found at {org_config_path} -- triage_home "
            "defaults to off (home repo not scanned)"
        )
        return False
    try:
        text = org_config_path.read_text(encoding="utf-8")
    except OSError as e:
        signals.append(
            f"could not read org-config at {org_config_path}: {e} -- "
            "triage_home defaults to off"
        )
        return False
    m = _TRIAGE_HOME_RE.search(text)
    if m is None:
        # Documented default, not an anomaly -- no signal.
        return False
    raw = m.group(1).strip()
    val = raw.lower()
    if val in _TRIAGE_HOME_ON_VALUES:
        return True
    if val in _TRIAGE_HOME_OFF_VALUES:
        return False
    signals.append(
        f"org-config triage_home value '{raw}' is not recognised -- treated "
        "as off (expected on/yes/true or off/no/false)"
    )
    return False


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

    Only called when ``triage_home`` is on. Appends a loud signal and
    returns ``None`` when both stages fail: the operator asked for the home
    repo, so failing to produce it is an anomaly the caller should surface
    rather than silently swallow.
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
        "triage_home is on but the home repo is NOT included in the --repo "
        "set"
    )
    return None


def resolve_repos(
    *,
    registry_path: Path,
    claude_org_root: Path,
    org_config_path: Optional[Path] = None,
) -> dict:
    """Build the repo-set result dict. Pure read-only computation."""
    signals: list[str] = []
    included: list[dict] = []
    opted_out: list[dict] = []
    skipped: list[dict] = []

    if org_config_path is None:
        org_config_path = claude_org_root / "registry" / "org-config.md"

    triage_home = _read_triage_home(Path(org_config_path), signals)
    # Home resolution is skipped entirely while triage_home is off: neither
    # `git remote get-url origin` nor `gh repo view` runs.
    home_repo = (
        _resolve_home_repo(claude_org_root, signals) if triage_home else None
    )

    if registry_path.exists():
        text = registry_path.read_text(encoding="utf-8")
        projects = parse_projects_text(text)
    else:
        projects = []
        signals.append(
            f"registry not found at {registry_path} -- no registry rows to "
            "scan"
        )

    # Issue #830: computed from the rows already parsed above (no second read).
    base_branches = _base_branches_from_projects(projects)

    for proj in projects:
        raw = proj.triage.strip()
        val = raw.lower()
        if val in _OPT_OUT_VALUES:
            # Checked before the URL derivation so an explicitly opted-out
            # non-URL row stays quiet (no skip signal to triage).
            opted_out.append(
                {
                    "nickname": proj.nickname,
                    "path": proj.path,
                    "repo": _owner_repo_from_url(proj.path),
                    "value": raw,
                }
            )
            continue
        if val not in _INCLUDE_VALUES:
            signals.append(
                f"registry row '{proj.nickname}' triage value '{raw}' is not "
                "recognised -- treated as included (opt-out values are: no / "
                "off / false)"
            )
        repo = _owner_repo_from_url(proj.path)
        if repo is None:
            reason = (
                f"registry row '{proj.nickname}' path '{proj.path}' -- "
                "skipped (cannot derive owner/repo; expected a bare "
                "https://github.com/OWNER/REPO clone URL)"
            )
            skipped.append(
                {"nickname": proj.nickname, "path": proj.path, "reason": reason}
            )
            signals.append(reason)
            continue
        included.append(
            {
                "nickname": proj.nickname,
                "repo": repo,
                "path": proj.path,
                # Issue #830: the row's declared cut point / merge target
                # (``None`` when unset). Carried per row so the audit shows
                # *which registration* supplied a base branch, not just that
                # one exists somewhere.
                "base_branch": base_branches.get(repo),
            }
        )

    # Dedup preserving order; home first when it is included at all.
    repos: list[str] = []
    seen: set[str] = set()
    for candidate in ([home_repo] if home_repo else []) + [
        row["repo"] for row in included
    ]:
        if candidate not in seen:
            seen.add(candidate)
            repos.append(candidate)

    result: dict = {
        "repos": repos,
        "home_repo": home_repo,
        "triage_home": triage_home,
        "included": included,
        "opted_out": opted_out,
        "skipped": skipped,
        # Issue #830: {owner/repo: base_branch} for every registry row that
        # declares one (including opted-out rows — see
        # ``_base_branches_from_projects``). The triage scan uses it to detect
        # Issues already closed by a PR merged into a non-default base branch,
        # which GitHub's auto-close never fires for.
        "base_branches": base_branches,
        "signals": signals,
    }
    if not repos:
        result["error"] = (
            "no repos resolved (no scannable GitHub URL rows in the registry "
            "and the home repo is not included; set 'triage_home: on' in "
            "registry/org-config.md or add a GitHub URL project row)"
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
            "scan. registry/projects.md rows are scanned by default ('no' / "
            "'off' / 'false' in the triage column opts a row out); the home "
            "repo joins only when registry/org-config.md sets triage_home to "
            "on (off by default). Read-only."
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
        "--org-config",
        default=None,
        type=Path,
        help="Path to registry/org-config.md (default: <root>/registry/org-config.md).",
    )
    p.add_argument(
        "--format",
        choices=("json", "flags"),
        default="json",
        help=(
            "'json' (default) prints the full result object; 'flags' prints "
            "'--repo a/b --repo c/d' for shell splicing (signals to stderr). "
            "'flags' is shell-dependent - an unquoted expansion is NOT split "
            "by zsh (needs ${=VAR}), so do not pipe it into a scan; use "
            "work_discovery_scan.py --all-registry-repos instead (Issue 829)."
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
            org_config_path=args.org_config,
        )
    except (OSError, UnicodeError) as e:  # registry read / decode failure
        # ``UnicodeDecodeError`` is a ``ValueError``, NOT an ``OSError``: a
        # registry with undecodable bytes used to escape this handler and
        # exit 1 with a traceback, breaking the documented "exit 0 / 2"
        # contract that the delivery layer branches on.
        err = {
            "repos": [],
            "home_repo": None,
            "triage_home": False,
            "included": [],
            "opted_out": [],
            "skipped": [],
            "base_branches": {},
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
