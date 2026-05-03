#!/usr/bin/env python3
"""Watch GitHub PR CI checks and emit a journal event when finished.

Cross-platform helper for the secretary role: after creating a PR,
invoke this script to block on ``gh pr checks --watch`` and append a
``ci_completed`` event to ``.state/journal.jsonl``.

Usage::

    py -3 tools/pr_watch.py --pr <PR> [--repo OWNER/REPO] [--interval SEC]

Behavior:

* Resolves the repo via ``gh repo view --json nameWithOwner`` when
  ``--repo`` is omitted.
* Spawns ``gh pr checks <PR> --watch --interval <SEC>`` and forwards
  its stdout/stderr.
* After the watch loop returns, queries
  ``gh pr checks <PR> --json bucket,state,name`` for per-check
  ``bucket`` (gh's documented bucket values are
  ``{pass, fail, pending, skipping, cancel}``) so the journal status
  reflects what CI actually decided rather than just the gh process'
  exit code (gh exits non-zero on a transient watch error too, and
  exit 8 specifically means "Checks pending", not "failed").
  Classifies as ``passed`` (all pass/skipping), ``failed``
  (≥1 fail/cancel), ``incomplete`` (any pending / unknown bucket /
  empty list), or ``canceled`` (parent SIGINT). Falls back to
  exit-code-based classification only if the JSON probe itself
  fails. Appends one JSON-Lines record to
  ``<repo_root>/.state/journal.jsonl`` (anchored to ``tools/..`` so
  cwd doesn't matter).
* Prints the final status as a single line on stdout and exits with
  the gh process' exit code.

The journal payload shape is::

    {"ts": "<ISO8601>", "event": "ci_completed",
     "pr": <int>, "repo": "<owner/repo>",
     "status": "passed|failed|incomplete|canceled",
     "duration_sec": <int>}

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


# gh's `bucket` field (see `gh pr checks --help`) categorizes a check's
# `state` into one of these buckets: "pass", "fail", "pending",
# "skipping", "cancel". We treat fail+cancel as failure signals,
# pass+skipping as success, and pending as still-running.
_FAILED_BUCKETS = frozenset({"fail", "cancel"})
_PASSED_BUCKETS = frozenset({"pass", "skipping"})
_PENDING_BUCKETS = frozenset({"pending"})


def _classify(exit_code: int) -> str:
    """Fallback classifier used only when the JSON probe is unavailable.

    Reference: https://cli.github.com/manual/gh_help_exit-codes and
    https://cli.github.com/manual/gh_pr_checks. With ``--watch`` gh
    blocks until pending checks resolve and then returns 0 (all
    checks passed). Exit code 2 is gh's standard cancellation code
    (e.g. user interrupt). Exit code 8 means "Checks pending" per
    gh's docs (NOT failure). Other non-zero values most likely
    indicate an internal gh error rather than a CI verdict, so they
    map to the conservative ``incomplete`` status — refusing to
    silently turn a transient error into "passed" while also not
    libelling green CI as "failed".

    SIGINT raised in the parent (Python ``KeyboardInterrupt``) is
    normalized to 2 in :func:`main` before reaching this function.

    Note: as of Issue #224 the primary classifier is
    :func:`_classify_from_checks`, which inspects per-check JSON. This
    fallback is only used when the JSON probe itself is unavailable.
    """
    if exit_code == 0:
        return "passed"
    if exit_code == 2:
        return "canceled"
    if exit_code == 8:
        return "incomplete"
    return "incomplete"


def _classify_from_checks(checks: "list[dict]") -> str:
    """Classify CI status from `gh pr checks --json bucket,state,name` output.

    gh's documented ``bucket`` values are
    ``{pass, fail, pending, skipping, cancel}``.

    * Empty list → ``incomplete`` (no checks reported).
    * Any bucket in :data:`_FAILED_BUCKETS` (``fail``/``cancel``) →
      ``failed``.
    * Any bucket in :data:`_PENDING_BUCKETS` → ``incomplete``.
    * All buckets in :data:`_PASSED_BUCKETS` (``pass``/``skipping``)
      → ``passed``.
    * Anything else (unrecognized bucket) → ``incomplete``
      (conservative).
    """
    if not checks:
        return "incomplete"
    has_pending_or_unknown = False
    for chk in checks:
        bucket = (chk.get("bucket") or "").lower()
        if bucket in _FAILED_BUCKETS:
            return "failed"
        if bucket in _PASSED_BUCKETS:
            continue
        # pending, empty, or any unrecognized bucket → conservative incomplete.
        has_pending_or_unknown = True
    return "incomplete" if has_pending_or_unknown else "passed"


def _fetch_checks(pr: int, repo: str) -> "list[dict] | None":
    """Return parsed `gh pr checks <pr> --json` results, or ``None`` on error.

    Requests ``bucket,state,name``: ``bucket`` is the only field we
    classify on (see :func:`_classify_from_checks`); ``state`` and
    ``name`` are fetched purely to aid debugging when something goes
    sideways. ``gh pr checks --json`` exits non-zero with code 8 when
    checks are still pending, so we tolerate that as a successful
    probe and let :func:`_classify_from_checks` see the data.
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "checks", str(pr),
                "--repo", repo,
                "--json", "bucket,state,name",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    # gh exits 8 when checks pending, but still emits the JSON we want.
    if result.returncode not in (0, 8):
        return None
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return [c for c in data if isinstance(c, dict)]


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
    # Accept both `--pr <n>` (preferred, unambiguous on PowerShell) and the
    # legacy positional form `<n>` so direct python/bash invocations keep
    # working. Exactly one of the two must be supplied.
    parser.add_argument(
        "--pr",
        dest="pr_flag",
        type=int,
        default=None,
        help="pull request number",
    )
    parser.add_argument(
        "pr_positional",
        nargs="?",
        type=int,
        default=None,
        metavar="PR",
        help=argparse.SUPPRESS,
    )
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

    if args.pr_flag is not None and args.pr_positional is not None:
        parser.error("specify the PR number once (either --pr or positional)")
    pr_number = args.pr_flag if args.pr_flag is not None else args.pr_positional
    if pr_number is None:
        parser.error("missing PR number (use --pr <n>)")
    if pr_number <= 0:
        parser.error("PR number must be a positive integer")
    if args.interval <= 0:
        parser.error("--interval must be a positive integer")
    args.pr = pr_number

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
    canceled = False
    try:
        completed = subprocess.run(cmd)
        exit_code = completed.returncode
    except KeyboardInterrupt:
        # Normalize parent-side cancellation to gh's standard exit code 2
        # so callers (and the journal status mapping) see a portable signal.
        exit_code = 2
        canceled = True
    duration = int(round(time.monotonic() - started))

    if canceled:
        status = "canceled"
    else:
        # Issue #224: gh exit 1 from a transient watch-loop error must not
        # be conflated with "CI failed". Re-derive the status from the
        # per-check JSON; only fall back to the exit code if the probe
        # itself fails.
        checks = _fetch_checks(args.pr, repo)
        if checks is None:
            sys.stderr.write(
                "tools/pr_watch.py: warning: could not query check results "
                "via `gh pr checks --json`; falling back to exit-code "
                "classification.\n"
            )
            status = _classify(exit_code)
        else:
            status = _classify_from_checks(checks)

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
