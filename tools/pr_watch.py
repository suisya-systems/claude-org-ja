#!/usr/bin/env python3
"""Watch GitHub PR CI checks and emit a journal event when finished.

Cross-platform helper for the secretary role: after creating a PR,
invoke this script to block on ``gh pr checks --watch`` and record a
``ci_completed`` event in ``.state/state.db`` (events table).

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
  fails. Appends one row to the ``events`` table in
  ``<repo_root>/.state/state.db`` (anchored to ``tools/..`` so cwd
  doesn't matter).
* Prints the final status as a single line on stdout and exits with
  a deterministic exit code derived from the *resolved* status
  (Issue #413 / Codex round-1 Major): ``passed``→0, ``failed``→1,
  ``canceled``→2, ``incomplete``→8. The original ``gh`` exit code
  is intentionally NOT propagated, because the resolver may upgrade
  ``gh exit 8`` ("Checks pending") to a final ``passed`` verdict
  via retry — returning 8 in that case would mislead shell callers
  inspecting ``$?``. The post-CI merge-watch helper may further
  override 0 → 9 on its own failure modes (timeout / no_run /
  helper exception).

M4 (Issue #267): events flow through the SQLite DB only —
``.state/journal.jsonl`` is decommissioned. The recorder uses the same
``StateWriter.append_event`` path as ``tools/journal_append.py``.

The event payload shape is::

    {"event": "ci_completed", "ts": "<ISO8601>",
     "pr": <int>, "repo": "<owner/repo>",
     "status": "passed|failed|incomplete|canceled",
     "duration_sec": <int>}
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Bound on the post-CI merge-watch loop. Issue #317: after CI passes we
# keep polling `gh pr view --json mergedAt` until the PR is merged or
# this many seconds elapse, whichever comes first. 24h matches the
# upper end of the org-delegate Step 5 2b-ii idle window so the
# secretary can intervene manually past that.
MERGE_WATCH_MAX_SECONDS = 24 * 60 * 60

# Issue #413: post-watch verdict-resolution retry. `gh pr checks --watch`
# can return immediately on a freshly-created PR (before any check-run
# row has propagated through GitHub's API), in which case the JSON
# probe sees `[]` and the legacy code wrote a final
# `ci_completed(status=incomplete)` event with `duration_sec=1`. The
# retry loop absorbs `[]` / `pending` / `gh exit 8` as "still
# observing" until either a final verdict (`passed` / `failed`)
# appears or the budget is exhausted (in which case we record a final
# `incomplete` once, capturing the elapsed time honestly).
RETRY_BUDGET_SEC = 60
RETRY_INTERVAL_SEC = 5

# Make `tools.state_db.*` importable when running this script directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.state_db.discover import resolve_state_db_path  # noqa: E402

# Path used by tests via mock.patch — kept as a module-level Path so the
# legacy test seam (`mock.patch.object(pr_watch, "JOURNAL_PATH", ...)`)
# continues to redirect writes at the tempdir. Issue #398: resolved
# via discovery so worktree-cwd invocations target the main checkout's
# state.db, not the worktree's empty `.state/`.
JOURNAL_PATH = resolve_state_db_path()

# Issue #326: after writing a CI-completion / merge / timeout event,
# also push a peer message to secretary so it doesn't have to poll the
# DB. The dispatch is wrapped in a tiny module-level seam so tests can
# mock it without poking subprocess.Popen of the real renga binary.
_PEER_NOTIFY_TARGET = "secretary"


def _notify_peer(message: str, to_id: str = _PEER_NOTIFY_TARGET) -> bool:
    """Best-effort peer-message dispatch. Never raises.

    Returns True on confirmed delivery, False otherwise (RENGA_SOCKET
    unset, renga binary missing, transport error, recipient unknown).
    Wrapped here so tests can patch a single seam.
    """
    try:
        from tools.peer_notify import notify_peer
    except Exception:  # noqa: BLE001
        return False
    try:
        return notify_peer(to_id, message)
    except Exception:  # noqa: BLE001
        return False


def _record_ci_completed(*, db_path: Path, pr: int, repo: str,
                         status: str, duration: int) -> None:
    """Append a ``ci_completed`` event to the DB events table."""
    from tools.state_db import apply_schema, connect
    from tools.state_db.discover import verify_or_exit
    from tools.state_db.writer import StateWriter

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_db = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new_db:
            apply_schema(conn)
        else:
            verify_or_exit(db_path, conn=conn, prog="tools/pr_watch.py")
        writer = StateWriter(conn)
        writer.append_event(
            kind="ci_completed",
            actor="pr_watch",
            payload={
                "pr": pr,
                "repo": repo,
                "status": status,
                "duration_sec": duration,
            },
        )
        writer.commit()
    finally:
        conn.close()


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
    sideways.

    ``gh pr checks`` exits non-zero on multiple non-error conditions:
    ``8`` for "Checks pending" and ``1`` when at least one check has
    failed (gh treats a red PR as a CLI error too). In both cases
    gh still writes the requested JSON to stdout. So we trust the
    JSON whenever it parses as a list, and only fall back when the
    output is unparseable or the binary is missing entirely — that's
    the only condition under which downgrading to the exit-code
    classifier is appropriate.
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
    try:
        data = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return [c for c in data if isinstance(c, dict)]


def _resolve_final_status(
    pr: int,
    repo: str,
    exit_code: int,
    *,
    budget_sec: "float | None" = None,
    retry_interval_sec: "float | None" = None,
) -> str:
    """Drive `_fetch_checks` until a final CI verdict is observed.

    Issue #413: ``gh pr checks --watch`` occasionally returns
    immediately when invoked on a freshly created PR — before any
    check-run row has propagated. The first :func:`_fetch_checks`
    response is then ``[]`` (transient empty), and the legacy code
    classified that as ``incomplete`` and wrote it as the *final*
    ``ci_completed`` event with ``duration_sec=1`` (e.g. PRs #411 /
    #14 / #15 / #416 in a single session).

    Final-verdict semantics:

    * ``passed`` / ``failed`` → return immediately (deterministic).
    * ``incomplete`` (empty list, ``pending`` bucket, or
      ``gh exit 8``) → enter a bounded retry loop. Each iteration
      sleeps ``retry_interval_sec`` and re-queries.
    * Budget exhaustion → return the last observed ``incomplete``
      verdict (recorded as a single, honest final event whose
      ``duration_sec`` reflects the full observation window).
    * JSON probe unavailable (``_fetch_checks`` returns ``None``)
      → fall back to :func:`_classify` against ``exit_code``. This
      is a catastrophic state (gh missing / unparseable stdout); the
      retry loop is not the right tool for it.

    Time / sleep are referenced via the ``time`` module attribute
    lookup so existing tests (``mock.patch.object(pr_watch.time,
    "monotonic", ...)``) keep working.
    """
    if budget_sec is None:
        budget_sec = RETRY_BUDGET_SEC
    if retry_interval_sec is None:
        retry_interval_sec = RETRY_INTERVAL_SEC

    # Codex round-2 Major: a transient JSON parse failure (the
    # subprocess succeeded but stdout was empty / malformed for a
    # single observation) used to short-circuit the retry budget and
    # return :func:`_classify`(exit_code) — which on `gh exit 8`
    # would record `incomplete` immediately, re-introducing the
    # Issue #413 race. Treat both ``None`` (unparseable probe) and
    # ``incomplete`` (transient empty / pending) as retryable, and
    # only fall back to the exit-code classifier if NO parseable
    # response is observed within the budget.
    last_verdict: "str | None" = None
    deadline_set = False
    deadline = 0.0

    def _set_deadline_once() -> None:
        nonlocal deadline_set, deadline
        if not deadline_set:
            deadline = time.monotonic() + budget_sec
            deadline_set = True

    while True:
        checks = _fetch_checks(pr, repo)
        if checks is not None:
            verdict = _classify_from_checks(checks)
            if verdict in ("passed", "failed"):
                return verdict
            last_verdict = verdict
        # Either probe was unparseable (checks is None) or the verdict
        # is `incomplete`. Initialise the budget on the first observed
        # need to wait, then back off and try again.
        _set_deadline_once()
        if time.monotonic() >= deadline:
            break
        time.sleep(retry_interval_sec)

    if last_verdict is None:
        # Never got a parseable probe response. Catastrophic-end:
        # honour the exit-code fallback so the recorded status still
        # reflects what gh believed the watched PR's CI was doing.
        sys.stderr.write(
            "tools/pr_watch.py: warning: could not query check results "
            "via `gh pr checks --json` within the retry budget; falling "
            "back to exit-code classification.\n"
        )
        return _classify(exit_code)
    return last_verdict


def _watch_for_merge(
    *,
    pr: int,
    repo: str,
    interval: int,
    db_path: Path,
    max_seconds: int = MERGE_WATCH_MAX_SECONDS,
    sleeper=time.sleep,
    monotonic=time.monotonic,
) -> str:
    """Poll `gh pr view --json mergedAt` until merged or bound elapses.

    Issue #317. On the first poll that returns a non-null ``mergedAt``,
    invoke :func:`tools.run_complete_on_merge.complete_on_merge` to
    drive the run row to its terminal state and return its result.
    On bound exhaustion, append a ``pr_merge_watch_timeout`` event to
    the DB and return ``"timeout"``. ``sleeper`` and ``monotonic`` are
    injectable for tests.
    """
    from tools.run_complete_on_merge import (
        complete_on_merge, fetch_pr_view, RESULT_ALREADY, RESULT_MERGED,
        RESULT_MERGED_PENDING_CLEANUP, RESULT_NO_RUN, RESULT_NOT_YET,
    )

    deadline = monotonic() + max_seconds
    while True:
        try:
            view = fetch_pr_view(pr, repo)
        except RuntimeError as exc:
            sys.stderr.write(
                f"pr_watch: merge-watch: gh pr view failed: {exc}\n"
            )
            view = None

        if view is not None and view.get("mergedAt"):
            try:
                result = complete_on_merge(
                    pr=pr, repo=repo, db_path=db_path, pr_view=view,
                )
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(
                    f"pr_watch: merge-watch: complete_on_merge raised: {exc}\n"
                )
                return "error"
            sys.stdout.write(
                f"pr_watch: PR #{pr} merge-watch result: {result}\n"
            )
            if result in (
                RESULT_MERGED, RESULT_MERGED_PENDING_CLEANUP,
                RESULT_ALREADY, RESULT_NO_RUN,
            ):
                # Issue #326: notify secretary when we observe the
                # merge so it can kick off post-merge cleanup without
                # waiting for a human to refresh. NO_RUN means the
                # merge was observed but no matching run row was found
                # — Secretary must NOT treat it as the post-merge
                # cleanup signal, so we surface a distinct error
                # variant instead of PR_MERGED.
                if result == RESULT_NO_RUN:
                    _notify_peer(f"PR_MERGED_NO_RUN: PR #{pr}")
                else:
                    _notify_peer(f"PR_MERGED: PR #{pr}")
                return result
            # RESULT_NOT_YET shouldn't occur once mergedAt is set; treat
            # defensively as "keep polling".

        if monotonic() >= deadline:
            _record_event(
                db_path=db_path,
                kind="pr_merge_watch_timeout",
                payload={
                    "pr": pr, "repo": repo,
                    "max_seconds": max_seconds,
                },
            )
            # Issue #326: surface the 24h bound to secretary so a stuck
            # PR doesn't sit silently after the loop releases.
            _notify_peer(f"PR_MERGE_WATCH_TIMEOUT: PR #{pr}")
            sys.stdout.write(
                f"pr_watch: PR #{pr} merge-watch timed out after "
                f"{max_seconds}s\n"
            )
            return "timeout"

        sleeper(interval)


def _record_event(*, db_path: Path, kind: str, payload: dict) -> None:
    """Append a single event row via StateWriter (used for merge-watch timeout)."""
    from tools.state_db import apply_schema, connect
    from tools.state_db.discover import verify_or_exit
    from tools.state_db.writer import StateWriter

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_db = not db_path.exists()
    conn = connect(db_path)
    try:
        if is_new_db:
            apply_schema(conn)
        else:
            verify_or_exit(db_path, conn=conn, prog="tools/pr_watch.py")
        writer = StateWriter(conn)
        writer.append_event(kind=kind, actor="pr_watch", payload=payload)
        writer.commit()
    finally:
        conn.close()


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
    parser.add_argument(
        "--merge-watch",
        action="store_true",
        help=(
            "After CI passes, keep polling `gh pr view --json mergedAt` "
            "for up to 24h and invoke tools/run_complete_on_merge.py on "
            "the first mergedAt (Issue #317). Off by default — pr_watch "
            "is otherwise a CI-only blocking call, and a 24h wall is "
            "incompatible with the secretary's 2c/T6 review-feedback "
            "loop. Opt in only when secretary actually wants to wait."
        ),
    )
    # --no-merge-watch is kept as a no-op alias for back-compat with
    # callers / tests that already opted out. The default is off either
    # way; this keeps argv compatible with the prior turn's commits.
    parser.add_argument(
        "--no-merge-watch",
        action="store_true",
        help=argparse.SUPPRESS,
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

    # gh's documented cancellation exit code is 2 (parent SIGINT or
    # subprocess-side Ctrl-C). Honor it directly so we don't overwrite a
    # genuine cancellation with whatever the JSON probe returns.
    if canceled or exit_code == 2:
        status = "canceled"
    else:
        # Issue #224: gh exit 1 from a transient watch-loop error must
        # not be conflated with "CI failed". Issue #413: a freshly
        # created PR may have no check rows yet when --watch returns,
        # so an empty / pending JSON response is "still observing"
        # rather than the final verdict. Drive the resolver until it
        # returns a final verdict (or exhausts its retry budget).
        status = _resolve_final_status(args.pr, repo, exit_code)

    # Issue #413: duration is measured from the start of the watch to
    # the moment we have a final verdict (post-retry), so a
    # transient-empty race no longer reports `1s`.
    duration = int(round(time.monotonic() - started))

    # Codex review (round 1, Major): once the resolver picks the final
    # verdict, the script's exit code must reflect that verdict, not
    # whatever gh returned from the initial `--watch` invocation. With
    # the retry loop, gh can exit ``8`` ("Checks pending") on the first
    # observation and then a later JSON probe surfaces the actual
    # ``passed`` state — returning ``8`` to the caller would falsely
    # signal "incomplete" to shell-script consumers checking `$?`. The
    # mapping below mirrors :func:`_classify` (gh's documented codes:
    # 0=passed, 2=canceled, 8=incomplete) and adds 1 for failed. The
    # later merge-watch block can still override ``0`` → ``9`` when
    # the post-CI loop itself fails.
    exit_code = {
        "passed": 0,
        "failed": 1,
        "canceled": 2,
        "incomplete": 8,
    }.get(status, exit_code)

    _record_ci_completed(
        db_path=JOURNAL_PATH,
        pr=args.pr,
        repo=repo,
        status=status,
        duration=duration,
    )

    # Issue #326: nudge secretary as soon as the CI verdict is recorded
    # so it doesn't have to poll the DB. Best-effort — silent fallback
    # in non-renga environments (RENGA_SOCKET unset). Issue #413: the
    # peer notification, like the DB event, fires once per pr_watch
    # invocation and only on the final verdict (the retry loop above
    # already absorbed transient incomplete observations). If a
    # progress channel is ever wanted, route it through a distinct
    # event/message name (e.g. `ci_progress`) rather than overloading
    # `CI_COMPLETED`.
    _notify_peer(
        f"CI_COMPLETED: PR #{args.pr} {status} "
        f"(duration {duration}s, repo {repo})"
    )

    sys.stdout.write(f"pr_watch: PR #{args.pr} {status} ({duration}s)\n")

    # Issue #317: only enter merge-watch when CI actually passed and
    # the caller explicitly opted in via --merge-watch. The default is
    # off so pr_watch stays a "CI passed → return" command compatible
    # with secretary's 2c/T6 review-feedback loop.
    # Codex pre-design review (Minor 1): `run_complete_on_merge` is
    # the downstream actor for the green-PR path and is invoked here
    # only when `status == "passed"`. `incomplete` / `failed` /
    # `canceled` results never trigger merge-watch, so callers can
    # treat the `passed`-gated invocation as the contract.
    if status == "passed" and args.merge_watch and not args.no_merge_watch:
        merge_result = _watch_for_merge(
            pr=args.pr,
            repo=repo,
            interval=args.interval,
            db_path=JOURNAL_PATH,
        )
        # Codex Major: surface merge-watch failure modes via exit code
        # so callers can distinguish "CI passed but PR did not merge in
        # 24h" / "helper raised" from "CI passed and we successfully
        # transitioned the run". Don't override a non-zero CI exit
        # code — that already signaled trouble.
        if exit_code == 0 and merge_result in ("timeout", "error", "no_run"):
            # Codex round-2 Major: no_run means we observed a merge but
            # could not resolve the PR back to a runs row, so the
            # status flip didn't happen and the secretary needs to
            # intervene. Surface that as exit 9 so callers don't treat
            # it as success.
            exit_code = 9

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
