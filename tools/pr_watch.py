#!/usr/bin/env python3
"""Watch GitHub PR CI checks and emit a journal event when finished.

Cross-platform helper for the secretary role: after creating a PR,
invoke this script to block on ``gh pr checks --watch`` and append a
``ci_completed`` event to ``.state/journal.jsonl``.

Usage::

    py -3 tools/pr_watch.py <PR> [--repo OWNER/REPO] [--interval SEC]

Behavior:

* Resolves the repo via ``gh repo view --json nameWithOwner`` when
  ``--repo`` is omitted.
* Spawns ``gh pr checks <PR> --watch --interval <SEC>`` and forwards
  its stdout/stderr.
* Maps the gh exit code to ``passed`` / ``failed`` / ``canceled`` and
  appends one JSON-Lines record to ``<repo_root>/.state/journal.jsonl``
  (anchored to ``tools/..`` so cwd doesn't matter).
* Prints the final status as a single line on stdout and exits with
  the gh process' exit code.

The journal payload shape is::

    {"ts": "<ISO8601>", "event": "ci_completed",
     "pr": <int>, "repo": "<owner/repo>",
     "status": "passed|failed|canceled", "duration_sec": <int>}

No new third-party dependencies; only the standard library plus the
already-pinned ``core_harness.audit`` for the journal write.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from core_harness.audit import Journal


REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / ".state" / "journal.jsonl"


def _ensure_gh_installed() -> None:
    if shutil.which("gh") is None:
        sys.stderr.write(
            "tools/pr_watch.py: error: GitHub CLI (gh) not found in PATH.\n"
        )
        sys.exit(127)


def _resolve_repo() -> str:
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(
            "tools/pr_watch.py: error: failed to auto-detect repo via "
            f"`gh repo view`: {exc.stderr.strip() or exc}\n"
        )
        sys.exit(2)
    try:
        data = json.loads(result.stdout)
        repo = data["nameWithOwner"]
    except (json.JSONDecodeError, KeyError) as exc:
        sys.stderr.write(
            "tools/pr_watch.py: error: unexpected `gh repo view` output: "
            f"{exc}\n"
        )
        sys.exit(2)
    if not isinstance(repo, str) or "/" not in repo:
        sys.stderr.write(
            f"tools/pr_watch.py: error: invalid repo {repo!r}\n"
        )
        sys.exit(2)
    return repo


def _classify(exit_code: int) -> str:
    """Map a `gh pr checks --watch` exit code to a status string.

    Reference: https://cli.github.com/manual/gh_help_exit-codes and
    https://cli.github.com/manual/gh_pr_checks. With ``--watch`` gh
    blocks until pending checks resolve and then returns 0 (all
    checks passed) or 8 (at least one check failed). Exit code 2 is
    gh's standard cancellation code (e.g. user interrupt). Other
    non-zero values are treated as a generic failure so downstream
    automation does not silently mistake an error for success.

    SIGINT raised in the parent (Python ``KeyboardInterrupt``) is
    normalized to 2 in :func:`main` before reaching this function.
    """
    if exit_code == 0:
        return "passed"
    if exit_code == 2:
        return "canceled"
    if exit_code == 8:
        return "failed"
    return "failed"


def _pr_exists(pr: int, repo: str) -> bool:
    try:
        subprocess.run(
            ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "number"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/pr_watch.py",
        description="Watch a GitHub PR's CI checks and journal the result.",
    )
    parser.add_argument("pr", type=int, help="pull request number")
    parser.add_argument(
        "--repo",
        default=None,
        help="OWNER/REPO; auto-detected via `gh repo view` if omitted",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="poll interval in seconds (default: 30)",
    )
    args = parser.parse_args(argv)

    if args.pr <= 0:
        parser.error("PR number must be a positive integer")
    if args.interval <= 0:
        parser.error("--interval must be a positive integer")

    _ensure_gh_installed()
    repo = args.repo or _resolve_repo()

    if not _pr_exists(args.pr, repo):
        sys.stderr.write(
            f"tools/pr_watch.py: error: PR #{args.pr} not found in {repo}\n"
        )
        return 2

    cmd = [
        "gh", "pr", "checks", str(args.pr),
        "--repo", repo,
        "--watch",
        "--interval", str(args.interval),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(cmd)
        exit_code = completed.returncode
    except KeyboardInterrupt:
        # Normalize parent-side cancellation to gh's standard exit code 2
        # so callers (and the journal status mapping) see a portable signal.
        exit_code = 2
    duration = int(round(time.monotonic() - started))
    status = _classify(exit_code)

    Journal(JOURNAL_PATH).append(
        "ci_completed",
        pr=args.pr,
        repo=repo,
        status=status,
        duration_sec=duration,
    )

    sys.stdout.write(f"pr_watch: PR #{args.pr} {status} ({duration}s)\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
