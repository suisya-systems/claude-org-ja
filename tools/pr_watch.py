#!/usr/bin/env python3
"""Watch GitHub PR CI checks and emit a journal event when finished.

Cross-platform helper for the secretary role: after creating a PR,
invoke this script to block until CI resolves and record a
``ci_completed`` event in ``.state/state.db`` (events table).

Usage::

    py -3 tools/pr_watch.py --pr <PR> [--repo OWNER/REPO] [--interval SEC]

Behavior:

* Resolves the repo via ``gh repo view --json nameWithOwner`` when
  ``--repo`` is omitted.
* Issue #695: watches CI via a **self-poll loop** over
  ``gh pr checks <PR> --json bucket,state,name`` (:func:`_fetch_checks`
  / :func:`_self_poll_watch`) at ``--interval`` cadence, instead of
  shelling out to ``gh pr checks --watch``. ``gh``'s own ``--watch``
  loop does not treat the ``skipping`` bucket as terminal, so a PR
  whose checks are entirely ``pass``/``skipping`` (no ``pending``) —
  e.g. 4 passed + 2 skipped, 0 pending — never made ``--watch``
  return, and ``ci_completed`` was never recorded (observed on kura
  PR #38). The self-poll loop instead stops as soon as every check's
  ``bucket`` is outside :data:`_PENDING_BUCKETS`
  (``pass``/``skipping``/``fail``/``cancel``), matching what
  :func:`_classify_from_checks` already treats as decided. gh's
  documented ``bucket`` values are ``{pass, fail, pending, skipping,
  cancel}``.
* Once the self-poll loop observes a decided verdict, or bails out on
  an inconclusive observation (an empty check list / an unparseable
  probe — the Issue #413 freshly-created-PR race), the result is
  classified via :func:`_classify_from_checks` /
  :func:`_resolve_final_status` so the journal status reflects what CI
  actually decided. :func:`_resolve_final_status`'s bounded
  retry-with-backoff absorbs that inconclusive-observation race;
  genuinely still-running checks (a real ``pending`` bucket) are
  instead polled unbounded by the self-poll loop itself, at
  ``--interval`` cadence — mirroring ``gh --watch``'s own indefinite
  block while CI is actually running.
  Classifies as ``passed`` (all pass/skipping), ``failed``
  (≥1 fail/cancel), ``incomplete`` (checks parseable but at least one
  still pending / unknown bucket / empty list), ``indeterminate``
  (Issue #685: the ``gh pr checks --json`` probe never returned a
  parseable response within the retry budget, so no CI verdict could
  be read), or ``canceled`` (parent SIGINT). The JSON probe is retried
  with exponential backoff so a transient ``gh`` failure resolves to a
  definitive ``passed`` / ``failed`` instead of degrading; only a
  persistent probe failure lands on ``indeterminate``. Appends one row
  to the ``events`` table in ``<repo_root>/.state/state.db`` (anchored
  to ``tools/..`` so cwd doesn't matter).
* Prints the final status as a single line on stdout and exits with
  a deterministic exit code derived from the *resolved* status
  (Issue #413 / Codex round-1 Major): ``passed``→0, ``failed``→1,
  ``canceled``→2, ``incomplete``→8, ``indeterminate``→8 (Issue #685:
  like ``incomplete`` it is not a clean pass/fail for ``$?`` callers).
  Issue #695: since there is no longer a ``gh pr checks --watch``
  subprocess exit code to consult, ``indeterminate`` (probe never
  parsed at all) is the only fallback verdict when nothing could be
  read — the resolver no longer has a raw gh exit code to upgrade into
  an optimistic ``passed``. The post-CI merge-watch helper may further
  override 0 → 9 on its own failure modes (timeout / no_run /
  helper exception).

M4 (Issue #267): events flow through the SQLite DB only —
``.state/journal.jsonl`` is decommissioned. The recorder uses the same
``StateWriter.append_event`` path as ``tools/journal_append.py``.

The event payload shape is::

    {"event": "ci_completed", "ts": "<ISO8601>",
     "pr": <int>, "repo": "<owner/repo>",
     "status": "passed|failed|incomplete|indeterminate|canceled",
     "duration_sec": <int>, "head": "<short-sha|null>"}

Issue #685: when the verdict was derived from a parseable
``gh pr checks --json`` response, the payload additionally carries
``fail_count`` / ``pending_count`` / ``total_checks`` (so a consumer
can tell a single-check red from a broad outage without re-querying
gh). An ``indeterminate`` verdict instead carries
``retry_recommended: true`` / ``retry_after_sec`` / ``probe_attempts``,
making the retry schedule explicit so the monitoring side can
distinguish "verdict not yet knowable, re-invoke pr_watch" from a
genuinely stalled merge gate. The base keys above are unchanged, so
existing consumers keep working.

Issue #636: ``--merge-watch`` no longer assumes the head is frozen
after CI passes. Each merge-watch iteration polls ``headRefOid`` (via
``gh pr view``); if a new commit lands on the PR branch, the watcher
loops back to ci-watch for the new head and re-emits ``CI_COMPLETED``,
so the secretary never approves a merge against a stale verdict. The
``head`` field (short sha) is added to the ``ci_completed`` event and to
every peer message (``CI_COMPLETED`` / ``PR_MERGED`` /
``PR_MERGED_NO_RUN`` / ``PR_MERGE_WATCH_TIMEOUT``) so callers can tell
which head the signal belongs to.
"""

from __future__ import annotations

import argparse
import json
import os
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

# Issue #636 (Codex review): sentinel returned by _watch_for_merge when the
# PR merged at a head whose CI this watcher never confirmed (a push + merge
# both landed between polls). Distinct from a clean merge so main surfaces a
# non-zero exit and the peer signal uses a distinct prefix — fail-closed.
MERGE_RESULT_HEAD_UNCONFIRMED = "merged_head_unconfirmed"

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

# Issue #695 (Codex review round 3, P2): the self-poll loop's own
# handoff to `_resolve_final_status` (`_run_ci_watch_phase`, on an empty
# / unparseable first observation) uses a WIDER budget than
# `RETRY_BUDGET_SEC`. Pre-#695, `gh pr checks --watch` itself blocked
# until check rows were visible before the JSON-probe race ever mattered
# — that race was a narrow post-watch API-propagation lag, genuinely
# bounded to a few seconds. With `--watch` removed, the very FIRST
# observation for every freshly opened PR can be empty simply because no
# CI system has registered a check yet (not a lag, an honest "hasn't
# started"), and some external CI integrations can take longer than
# `RETRY_BUDGET_SEC` to publish their first check row. Widening this
# specific handoff's budget gives such integrations room without
# resorting to unbounded polling on data that has never once appeared.
CI_WATCH_EMPTY_RACE_BUDGET_SEC = 300

# Issue #685: back off between JSON-probe retries so a persistently
# flaky `gh pr checks --json` doesn't hammer the API. The sleep starts
# at RETRY_INTERVAL_SEC and multiplies by RETRY_BACKOFF_FACTOR after
# each attempt, capped at RETRY_MAX_INTERVAL_SEC. The overall retry
# window is still bounded by RETRY_BUDGET_SEC, so backoff only changes
# how the attempts are spaced, not how long we keep trying.
RETRY_BACKOFF_FACTOR = 2.0
RETRY_MAX_INTERVAL_SEC = 30

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


def _configured_transport() -> "str | None":
    """Return the configured transport name, or None if none is set.

    ``broker`` when ``ORG_TRANSPORT=broker`` (raw env, matching
    ``tools/peer_notify.notify_peer``'s dispatch), ``renga`` when
    ``RENGA_SOCKET`` is set, else None. Used to decide whether a failed
    peer push is a genuine delivery failure worth recording (fail-loud)
    versus an expected no-op in a plain shell / CI where no transport is
    configured by design.
    """
    if os.environ.get("ORG_TRANSPORT") == "broker":
        return "broker"
    if os.environ.get("RENGA_SOCKET"):
        return "renga"
    return None


def _notify_or_record(
    message: str,
    *,
    db_path: Path,
    failed_kind: str,
    pr: int,
    to_id: str = _PEER_NOTIFY_TARGET,
) -> bool:
    """Push a terminal peer message, recording a ``notify_failed`` event
    on failure (Refs #653 #658 — silent no-op elimination).

    The historical bug (PR #73): a terminal peer push silently no-op'd in
    a pane with no transport env, the ``bool`` return was discarded, and
    nothing recorded the miss — so the secretary sat idle. This wrapper
    keeps the low-latency push (path A) but makes a *configured*-transport
    failure fail-loud: it writes a ``notify_failed`` event that the
    dispatcher's outbox relay (path B) and the attention watcher (path C)
    both surface. When NO transport is configured (plain shell / CI, the
    documented best-effort no-op), it records nothing — that is expected
    behavior, not a delivery gap. Either way the canonical terminal event
    was already written before this call, so the dispatcher relay
    guarantees delivery regardless of this push.

    Never raises: a fail-loud recording error must not abort the watch.
    """
    ok = _notify_peer(message, to_id)
    if ok:
        return True
    transport = _configured_transport()
    if transport is None:
        return False
    try:
        _record_event(
            db_path=db_path,
            kind="notify_failed",
            payload={
                "pr": pr,
                "failed_kind": failed_kind,
                "target": to_id,
                "transport": transport,
                "message": message,
                # Diagnostic: which env the pane actually had, so a
                # spawn-time injection regression is reverse-derivable
                # from the recorded event (pairs with the pr-watch-pane
                # env-prefix fix).
                "env_present": {
                    "ORG_TRANSPORT": bool(os.environ.get("ORG_TRANSPORT")),
                    "ORG_BROKER_STATE_DIR": bool(
                        os.environ.get("ORG_BROKER_STATE_DIR")),
                    "RENGA_SOCKET": bool(os.environ.get("RENGA_SOCKET")),
                },
            },
        )
    except Exception:  # noqa: BLE001 — fail-loud must not itself abort the watch
        pass
    return False


def _short_head(oid: "str | None") -> "str | None":
    """Return the 7-char short form of a commit OID, or ``None``.

    None-safe (Issue #636): a missing / non-string OID degrades to
    ``None`` so head-change detection and message formatting never raise
    on a PR view that omits ``headRefOid``.
    """
    if not oid or not isinstance(oid, str):
        return None
    return oid[:7]


def _fetch_head_oid(pr: int, repo: str) -> "str | None":
    """Return the PR's current head commit OID (full sha), or ``None``.

    Issue #636: fetched once at the start of each ci-watch round so the
    recorded / messaged head is the head whose CI we actually observed,
    and threaded into :func:`_watch_for_merge` as the baseline for
    head-change detection. Best-effort: any gh / parse failure degrades
    to ``None`` (treated downstream as "head unknown → no change"), so a
    flaky probe never aborts the watch or fakes a head movement.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr), "--repo", repo,
             "--json", "headRefOid"],
            capture_output=True,
            text=True,
            encoding="utf-8",  # gh emits UTF-8; locale decode (cp932) corrupts/crashes (#537)
            check=False,
        )
    except OSError:
        return None
    try:
        # ``ValueError`` covers json.JSONDecodeError; ``TypeError`` covers a
        # non-string stdout (e.g. a Mock in tests that don't stub this call).
        data = json.loads(result.stdout or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    oid = data.get("headRefOid")
    return oid if isinstance(oid, str) and oid else None


def _record_ci_completed(*, db_path: Path, pr: int, repo: str,
                         status: str, duration: int,
                         head: "str | None" = None,
                         extra: "dict | None" = None) -> None:
    """Append a ``ci_completed`` event to the DB events table.

    ``head`` (Issue #636) is the short sha of the head whose CI verdict
    this event records; ``None`` when the head could not be resolved.

    ``extra`` (Issue #685) carries optional additive payload keys —
    per-bucket counts (``fail_count`` / ``pending_count`` /
    ``total_checks``) and, for an ``indeterminate`` verdict, the retry
    schedule (``retry_recommended`` / ``retry_after_sec`` /
    ``probe_attempts``). The base keys (``pr`` / ``repo`` / ``status`` /
    ``duration_sec`` / ``head``) always win, so a stray ``extra`` key
    can never clobber them and existing consumers keep working.
    """
    from tools.state_db import apply_schema, connect
    from tools.state_db.discover import verify_or_exit
    from tools.state_db.writer import StateWriter

    payload = {
        "pr": pr,
        "repo": repo,
        "status": status,
        "duration_sec": duration,
        "head": head,
    }
    if extra:
        # Additive only: never let extra shadow a base key.
        for key, value in extra.items():
            payload.setdefault(key, value)

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
            payload=payload,
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
            encoding="utf-8",  # gh emits UTF-8; locale decode (cp932) corrupts/crashes (#537)
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
    gh's docs (NOT failure).

    Issue #685: exit 0 (definitively "all passed") and exit 2
    (cancellation) are honoured, but exit 8 ("Checks pending") and any
    other non-zero code reach this function only when the JSON probe
    never yielded a parseable response within the retry budget — so we
    literally could not read whether CI passed, failed, or is still
    running. Those map to ``indeterminate`` (verdict undetermined /
    fetch failure) rather than ``incomplete``: ``incomplete`` now means
    "we DID read the checks and at least one is still pending", while
    ``indeterminate`` means "we could not read the checks at all". The
    split lets the events table distinguish a genuinely pending CI from
    a gh outage, and keeps a real red (``failed``) from being libelled
    as merely pending when its probe happened to fail transiently.

    SIGINT raised in the parent (Python ``KeyboardInterrupt``) is
    handled directly in :func:`_run_ci_watch_phase` as a ``canceled``
    verdict and never reaches this function (Issue #695: there is no
    longer a real gh process exit code carrying that signal — the
    caller always passes the neutral ``8`` placeholder here on the
    non-cancellation path).

    Note: as of Issue #224 the primary classifier is
    :func:`_classify_from_checks`, which inspects per-check JSON. This
    fallback is only used when the JSON probe itself is unavailable.
    """
    if exit_code == 0:
        return "passed"
    if exit_code == 2:
        return "canceled"
    return "indeterminate"


def _summarize_checks(checks: "list[dict]") -> "tuple[str, int, int, int]":
    """Return ``(status, fail_count, pending_count, total)`` for a checks list.

    Issue #685: the counts are threaded into the ``ci_completed`` payload
    so a consumer can tell a single-check red from a broad failure — and
    an ``incomplete`` with 1 pending check from one with 20 — without
    re-querying gh. ``status`` follows the same rules as
    :func:`_classify_from_checks`:

    * ``fail_count`` counts :data:`_FAILED_BUCKETS` (``fail``/``cancel``).
    * ``pending_count`` counts everything that is neither a failure nor a
      pass: :data:`_PENDING_BUCKETS` plus empty / unrecognized buckets
      (all treated conservatively as "not yet decided").
    * ``total`` is ``len(checks)``.

    Status: any failure → ``failed``; else an empty list or any
    pending/unknown → ``incomplete``; else (all pass/skipping) →
    ``passed``.
    """
    total = len(checks)
    fail_count = 0
    pending_count = 0
    for chk in checks:
        bucket = (chk.get("bucket") or "").lower()
        if bucket in _FAILED_BUCKETS:
            fail_count += 1
        elif bucket in _PASSED_BUCKETS:
            continue
        else:
            # pending, empty, or any unrecognized bucket → not yet decided.
            pending_count += 1
    if fail_count > 0:
        status = "failed"
    elif total == 0 or pending_count > 0:
        status = "incomplete"
    else:
        status = "passed"
    return status, fail_count, pending_count, total


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

    Thin wrapper over :func:`_summarize_checks` (which also returns the
    per-bucket counts used in the ``ci_completed`` payload, Issue #685).
    """
    return _summarize_checks(checks)[0]


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
            encoding="utf-8",  # gh emits UTF-8; locale decode (cp932) corrupts/crashes (#537)
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
    backoff_factor: "float | None" = None,
    max_interval_sec: "float | None" = None,
) -> dict:
    """Drive `_fetch_checks` until a final CI verdict is observed.

    Issue #413: on a freshly created PR, the very first
    :func:`_fetch_checks` response can be ``[]`` (transient empty,
    before any check-run row has propagated), and the legacy code
    classified that as ``incomplete`` and wrote it as the *final*
    ``ci_completed`` event with ``duration_sec=1`` (e.g. PRs #411 /
    #14 / #15 / #416 in a single session).

    Issue #695: this function is now called only when
    :func:`_self_poll_watch` (the self-poll loop that replaced the
    blocking ``gh pr checks --watch`` subprocess) bails out on such an
    inconclusive observation. ``exit_code`` is therefore a caller-
    supplied placeholder (``8``, "Checks pending") rather than a real
    gh process exit code — it is consulted only in the final fallback
    branch below, when the JSON probe never parses at all within the
    budget.

    Final-verdict semantics:

    * ``passed`` / ``failed`` → return immediately (deterministic).
    * ``incomplete`` (empty list, ``pending`` bucket, or
      ``gh exit 8``) → enter a bounded retry loop. Each iteration
      sleeps and re-queries; the sleep grows by ``backoff_factor``
      each attempt (Issue #685: exponential backoff, capped at
      ``max_interval_sec``) so a persistently flaky gh doesn't hammer
      the API.
    * ``_fetch_checks`` returns ``None`` (probe was unparseable —
      empty / malformed stdout, JSON parse error, unexpected
      shape) → also retried within the same budget (Codex round-2
      Major: a single transient probe failure used to bypass the
      retry loop and short-circuit to ``_classify(exit_code)``,
      reintroducing the Issue #413 race when it coincided with
      ``gh exit 8``).
    * Budget exhausted with at least one parseable response →
      return the last observed ``incomplete`` verdict (recorded as
      a single, honest final event whose ``duration_sec`` reflects
      the full observation window), carrying the per-bucket counts.
    * Budget exhausted with NO parseable response → fall back to
      :func:`_classify` against ``exit_code``. On exit 0 that is a
      definitive ``passed``; otherwise it is ``indeterminate``
      (Issue #685: verdict undetermined / fetch failure — kept
      distinct from ``incomplete`` so the events table separates a
      genuine pending CI from a gh outage).

    Returns a dict verdict::

        {"status": str,           # passed|failed|incomplete|indeterminate
         "fail_count": int|None,  # None when derived from the exit-code fallback
         "pending_count": int|None,
         "total_checks": int|None,
         "probe_attempts": int}   # how many gh pr checks --json calls were made

    Time / sleep are referenced via the ``time`` module attribute
    lookup so existing tests (``mock.patch.object(pr_watch.time,
    "monotonic", ...)``) keep working.
    """
    if budget_sec is None:
        budget_sec = RETRY_BUDGET_SEC
    if retry_interval_sec is None:
        retry_interval_sec = RETRY_INTERVAL_SEC
    if backoff_factor is None:
        backoff_factor = RETRY_BACKOFF_FACTOR
    if max_interval_sec is None:
        max_interval_sec = RETRY_MAX_INTERVAL_SEC

    # Codex round-2 Major: a transient JSON parse failure (the
    # subprocess succeeded but stdout was empty / malformed for a
    # single observation) used to short-circuit the retry budget and
    # return :func:`_classify`(exit_code) — which on `gh exit 8`
    # would record `incomplete` immediately, re-introducing the
    # Issue #413 race. Treat both ``None`` (unparseable probe) and
    # ``incomplete`` (transient empty / pending) as retryable, and
    # only fall back to the exit-code classifier if NO parseable
    # response is observed within the budget.
    last_summary: "tuple[str, int, int, int] | None" = None
    probe_attempts = 0
    interval = retry_interval_sec
    deadline_set = False
    deadline = 0.0

    def _set_deadline_once() -> None:
        nonlocal deadline_set, deadline
        if not deadline_set:
            deadline = time.monotonic() + budget_sec
            deadline_set = True

    while True:
        checks = _fetch_checks(pr, repo)
        probe_attempts += 1
        if checks is not None:
            summary = _summarize_checks(checks)
            if summary[0] in ("passed", "failed"):
                return {
                    "status": summary[0],
                    "fail_count": summary[1],
                    "pending_count": summary[2],
                    "total_checks": summary[3],
                    "probe_attempts": probe_attempts,
                }
            last_summary = summary
        # Either probe was unparseable (checks is None) or the verdict
        # is `incomplete`. Initialise the budget on the first observed
        # need to wait, then back off and try again.
        _set_deadline_once()
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
        # Issue #685: exponential backoff between probes, capped.
        interval = min(interval * backoff_factor, max_interval_sec)

    if last_summary is None:
        # Never got a parseable probe response. Catastrophic-end:
        # honour the exit-code fallback so the recorded status still
        # reflects what gh believed the watched PR's CI was doing —
        # `passed` on exit 0, else `indeterminate` (Issue #685).
        sys.stderr.write(
            "tools/pr_watch.py: warning: could not query check results "
            "via `gh pr checks --json` within the retry budget; falling "
            "back to exit-code classification.\n"
        )
        return {
            "status": _classify(exit_code),
            "fail_count": None,
            "pending_count": None,
            "total_checks": None,
            "probe_attempts": probe_attempts,
        }
    return {
        "status": last_summary[0],
        "fail_count": last_summary[1],
        "pending_count": last_summary[2],
        "total_checks": last_summary[3],
        "probe_attempts": probe_attempts,
    }


def _self_poll_watch(pr: int, repo: str, interval: int) -> "dict | None":
    """Replace the blocking ``gh pr checks --watch`` subprocess (Issue #695).

    ``gh``'s own ``--watch`` loop does not treat the ``skipping`` bucket
    as terminal, so a PR whose checks are entirely ``pass``/``skipping``
    (0 pending, e.g. 4 passed + 2 skipped) never made ``--watch``
    return — ``ci_completed`` was never recorded (observed on kura PR
    #38, the auto-merge gate never fired). This polls
    :func:`_fetch_checks` directly at ``interval`` cadence and applies
    the same terminal-bucket rule :func:`_classify_from_checks` already
    uses (``pass``/``skipping``/``fail``/``cancel`` are all decided;
    only :data:`_PENDING_BUCKETS` — or an unrecognized bucket — means
    "still running").

    Two distinct "not decided yet" observations are handled
    differently:

    * A non-empty checks list with at least one genuinely pending (or
      unrecognized) bucket means CI is still running. This case loops
      here, unbounded, at ``interval`` cadence — mirroring the
      indefinite block ``gh --watch`` performed while checks were
      pending, so a CI run that legitimately takes many minutes is
      never prematurely declared ``incomplete``. Codex review (Issue
      #695 round 2, P2): this holds even when a check has ALREADY
      failed while a sibling check is still pending —
      :func:`_summarize_checks` reports ``status="failed"`` the moment
      any bucket is in :data:`_FAILED_BUCKETS`, regardless of
      ``pending_count``, so the termination gate below checks
      ``pending_count == 0`` directly rather than ``status !=
      "incomplete"``. Otherwise a fast-failing check would make this
      function return before ``gh pr checks --watch``'s own
      wait-for-everything contract would have, missing whatever the
      still-running sibling check goes on to report.
    * An empty list (no check rows visible yet) or an unparseable probe
      (:func:`_fetch_checks` returns ``None`` — a gh/network hiccup) is
      inconclusive rather than "still running": this function returns
      ``None`` immediately so the caller's existing
      :func:`_resolve_final_status` bounded retry-with-backoff (Issue
      #413 / #685) reconciles it, exactly as it did for the
      post-``--watch`` race it was originally built for.

    Returns a verdict dict shaped like :func:`_resolve_final_status`'s
    return value (``status`` / ``fail_count`` / ``pending_count`` /
    ``total_checks`` / ``probe_attempts``) once a decided verdict is
    observed (every check's bucket is outside
    :data:`_PENDING_BUCKETS`), or ``None`` to signal "inconclusive,
    hand off". May raise ``KeyboardInterrupt`` (propagated from
    ``time.sleep`` or the ``gh`` subprocess on SIGINT) — the caller
    treats that as cancellation, matching the previous ``gh --watch``
    Ctrl-C behavior.
    """
    probe_attempts = 0
    while True:
        checks = _fetch_checks(pr, repo)
        probe_attempts += 1
        if checks:
            status, fail_count, pending_count, total = _summarize_checks(checks)
            if pending_count == 0:
                # Every check has left the "not yet decided" bucket set
                # (pass / skipping / fail / cancel only) -- fully
                # decided, whether that nets out to `passed` or
                # `failed`.
                return {
                    "status": status,
                    "fail_count": fail_count,
                    "pending_count": pending_count,
                    "total_checks": total,
                    "probe_attempts": probe_attempts,
                }
            # At least one check is still pending (or an unrecognized
            # bucket) -- genuinely still running, even if another check
            # already failed. Fall through to the unbounded
            # interval-cadence poll below.
        else:
            # checks is None (unparseable probe) or [] (no check rows
            # visible yet) -- inconclusive; the bounded resolver is
            # better suited to this than unbounded polling here.
            return None
        time.sleep(interval)


def _watch_for_merge(
    *,
    pr: int,
    repo: str,
    interval: int,
    db_path: Path,
    max_seconds: int = MERGE_WATCH_MAX_SECONDS,
    sleeper=time.sleep,
    monotonic=time.monotonic,
    baseline_head: "str | None" = None,
) -> str:
    """Poll `gh pr view` until merged, the head moves, or the bound elapses.

    Issue #317. On the first poll that returns a non-null ``mergedAt``,
    invoke :func:`tools.run_complete_on_merge.complete_on_merge` to
    drive the run row to its terminal state and return its result.
    On bound exhaustion, append a ``pr_merge_watch_timeout`` event to
    the DB and return ``"timeout"``. ``sleeper`` and ``monotonic`` are
    injectable for tests.

    Issue #636: each poll also compares ``headRefOid`` against
    ``baseline_head`` (the head whose CI we just watched). If a new
    commit landed on the PR branch, return ``"head_changed"`` so the
    caller loops back to ci-watch for the new head — the secretary must
    not approve a merge against a CI verdict for an older head. The
    comparison is None-safe: when either side is unknown we treat it as
    "no change" and keep polling (so callers / mocks that don't supply
    ``headRefOid`` retain the pre-#636 mergedAt-only behavior).
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
            # Head sha of the branch tip that was merged.
            merged_full = view.get("headRefOid")
            merged_head = _short_head(merged_full)
            # Issue #636 (Codex review): a merge is terminal — the PR is
            # already on main, so we can NOT loop back to ci-watch to
            # confirm it (there is nothing left to watch and the merge is
            # irreversible). But if the merged head differs from the head
            # whose CI we last confirmed (baseline_head), the PR was merged
            # at a commit pr_watch never separately verified. Rather than
            # report a clean success, we flag the discrepancy loudly so the
            # secretary doesn't treat the merge as "the approved head
            # landed". The head tag already lets a consumer compare against
            # the CI_COMPLETED it acted on; this makes the mismatch explicit.
            stale_head = bool(
                baseline_head and isinstance(merged_full, str)
                and merged_full and merged_full != baseline_head
            )
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
                # variant instead of PR_MERGED. Issue #636: tag the head.
                head_tag = merged_head or "unknown"
                if stale_head:
                    # Issue #636 (Codex review): the PR merged at a head
                    # whose CI this watcher never confirmed (a push +
                    # merge slipped between polls). A merge is terminal —
                    # we cannot loop back to ci-watch — but we must NOT
                    # report a clean PR_MERGED, or a consumer keying on
                    # that prefix / exit 0 would proceed as if the merged
                    # head had passing CI. Emit a DISTINCT prefix (like
                    # PR_MERGED_NO_RUN, this fails closed: an unrecognized
                    # signal makes the secretary escalate to a human
                    # rather than auto-advance) and return a sentinel that
                    # main maps to a non-zero exit.
                    sys.stderr.write(
                        f"pr_watch: PR #{pr} merged at {head_tag} but the "
                        f"last CI-confirmed head was "
                        f"{_short_head(baseline_head)}; the merged head's CI "
                        "was never separately confirmed by pr_watch.\n"
                    )
                    # Refs #653 #658: write a canonical DB event BEFORE the
                    # peer push so this terminal signal is never peer-only
                    # (the dispatcher outbox relay reads the event; a lost
                    # push can't strand it).
                    _record_event(
                        db_path=db_path,
                        kind="pr_merged_head_unconfirmed",
                        payload={
                            "pr": pr, "repo": repo, "head": head_tag,
                            "baseline_head": _short_head(baseline_head),
                        },
                    )
                    _notify_or_record(
                        f"PR_MERGED_HEAD_UNCONFIRMED: PR #{pr} "
                        f"(head={head_tag}, last CI-confirmed head="
                        f"{_short_head(baseline_head)})",
                        db_path=db_path,
                        failed_kind="pr_merged_head_unconfirmed",
                        pr=pr,
                    )
                    return MERGE_RESULT_HEAD_UNCONFIRMED
                if result == RESULT_NO_RUN:
                    # Refs #653 #658: canonical event first (was peer-only).
                    _record_event(
                        db_path=db_path,
                        kind="pr_merged_no_run",
                        payload={"pr": pr, "repo": repo, "head": head_tag},
                    )
                    _notify_or_record(
                        f"PR_MERGED_NO_RUN: PR #{pr} (head={head_tag})",
                        db_path=db_path, failed_kind="pr_merged_no_run", pr=pr)
                else:
                    # pr_merged canonical event is written by
                    # run_complete_on_merge.py; here we only push + fail-loud.
                    _notify_or_record(
                        f"PR_MERGED: PR #{pr} (head={head_tag})",
                        db_path=db_path, failed_kind="pr_merged", pr=pr)
                return result
            # RESULT_NOT_YET shouldn't occur once mergedAt is set; treat
            # defensively as "keep polling".

        # Issue #636: detect a new commit on the PR branch and hand control
        # back to the caller's ci-watch loop. Checked after the merge gate
        # (a merged PR is terminal) and only when both heads are known.
        if view is not None and baseline_head:
            current_head = view.get("headRefOid")
            if (isinstance(current_head, str) and current_head
                    and current_head != baseline_head):
                sys.stdout.write(
                    f"pr_watch: PR #{pr} head moved "
                    f"{_short_head(baseline_head)} -> "
                    f"{_short_head(current_head)}; returning to ci-watch\n"
                )
                return "head_changed"

        if monotonic() >= deadline:
            timeout_head = _short_head(baseline_head)
            _record_event(
                db_path=db_path,
                kind="pr_merge_watch_timeout",
                payload={
                    "pr": pr, "repo": repo,
                    "max_seconds": max_seconds,
                    "head": timeout_head,
                },
            )
            # Issue #326: surface the 24h bound to secretary so a stuck
            # PR doesn't sit silently after the loop releases. The
            # pr_merge_watch_timeout event was already recorded above, so
            # the dispatcher relay backstops a lost push (Refs #653 #658).
            _notify_or_record(
                f"PR_MERGE_WATCH_TIMEOUT: PR #{pr} "
                f"(head={timeout_head or 'unknown'})",
                db_path=db_path, failed_kind="pr_merge_watch_timeout", pr=pr)
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
            encoding="utf-8",  # gh emits UTF-8; locale decode (cp932) corrupts/crashes (#537)
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def _run_ci_watch_phase(
    *, pr: int, repo: str, interval: int, db_path: Path,
) -> "tuple[str, int, str | None]":
    """Run one ci-watch round and record/emit its verdict (Issue #636).

    Self-polls (:func:`_self_poll_watch`, Issue #695) until a decided CI
    verdict for ``pr`` is observed, resolves the final verdict, appends a
    single ``ci_completed`` event and emits one ``CI_COMPLETED`` peer
    message — both tagged with the short head sha so the secretary can
    tell which head the verdict belongs to. Returns
    ``(status, exit_code, head_oid)`` where ``head_oid`` is the full head
    OID observed at verdict time (or ``None``).

    Returns ``("head_changed", 0, head)`` instead when the branch advances
    while we are resolving the verdict (see below); the caller treats that
    like the merge-watch loop-back and re-runs a fresh ci-watch round.

    Head/verdict pairing (Codex review, rounds 1-6): both the self-poll
    loop and ``_resolve_final_status``'s ``gh pr checks --json`` observe
    the PR's *live* head, so a branch that advances any time between the
    start of the watch and the end of resolution can yield a verdict
    describing a different commit than the one we tag. We bracket the
    *entire* watch+resolve phase with a head read taken before the watch
    starts and one taken after the verdict resolves: if they differ, we do
    NOT record a (possibly stale or transiently-incomplete) verdict — we
    return ``head_changed`` so :func:`main` restarts the *full* ci-watch
    (self-poll blocks until the new head's CI actually completes) rather
    than tagging a verdict to a head it doesn't describe or
    short-circuiting on the bounded JSON resolver. When the head is stable
    across the whole phase, the recorded head is exactly the one whose
    checks the verdict describes.

    Factored out of :func:`main` so the head-poll loop (Issue #636) can
    re-run a fresh ci-watch round — re-emitting ``CI_COMPLETED`` — when the
    head moves, whether observed here (across the watch) or by merge-watch.
    """
    # Baseline head captured BEFORE the (blocking) watch starts, so an
    # advance *during* the watch is caught when compared to the
    # post-resolution head below (Codex review round 6).
    head_before = _fetch_head_oid(pr, repo)
    started = time.monotonic()
    canceled = False
    verdict: "dict | None" = None
    try:
        # Issue #695: self-poll replaces the blocking `gh pr checks
        # --watch` subprocess. Raises KeyboardInterrupt on SIGINT, same
        # as the previous blocking subprocess.run(cmd) did.
        #
        # `_self_poll_watch` polls unbounded while genuinely pending, but
        # bails out (returns None) on an *inconclusive* observation (an
        # empty check list / an unparseable probe — the Issue #413
        # freshly-created-PR race), handing off to
        # `_resolve_final_status`'s bounded retry/backoff. That resolver
        # applies the SAME bounded budget to any non-empty verdict it
        # sees while a check is still pending -- including one where
        # another check has already failed, since `_summarize_checks`
        # reports `status="failed"` the moment any bucket is in
        # `_FAILED_BUCKETS`, regardless of `pending_count` (Codex
        # review, Issue #695 round 1 P1 / round 2 P2): without the loop
        # below, a PR whose checks start out invisible and then take
        # longer than the budget to finish -- or where one check fails
        # fast while a sibling is still running -- would be wrongly
        # finalized instead of watched to completion. So: only accept
        # the resolver's verdict as final when `pending_count` is 0 (or
        # unknown, i.e. the exit-code fallback with no parseable
        # response at all); whenever real, still-pending check rows are
        # observed, hand control back to the unbounded self-poll loop
        # instead of giving up.
        while True:
            verdict = _self_poll_watch(pr, repo, interval)
            if verdict is not None:
                break
            # Issue #695 round 3 (Codex review, P2): use the wider
            # CI_WATCH_EMPTY_RACE_BUDGET_SEC here rather than the
            # RETRY_BUDGET_SEC default -- see that constant's docstring
            # for why the self-poll handoff needs more room than the
            # narrow post-watch race `_resolve_final_status` was
            # originally built for.
            verdict = _resolve_final_status(
                pr, repo, exit_code=8,
                budget_sec=CI_WATCH_EMPTY_RACE_BUDGET_SEC,
            )
            if verdict["pending_count"]:
                # Real, still-pending checks exist -- not an empty-race
                # artifact, and not fully decided even if a sibling
                # check already failed. Resume unbounded polling.
                continue
            break
    except KeyboardInterrupt:
        canceled = True

    head_oid: "str | None" = None
    # Issue #685: per-bucket counts (parseable probe) and probe attempts,
    # threaded into the payload / peer message below. Stay None for the
    # cancellation path (no verdict was resolved).
    fail_count: "int | None" = None
    pending_count: "int | None" = None
    total_checks: "int | None" = None
    probe_attempts = 0
    if canceled:
        status = "canceled"
        # Skip the head probe on cancellation: the user is aborting, so a
        # second SIGINT during an extra subprocess would surface an ugly
        # traceback for no benefit (a canceled verdict has no head to act
        # on). head_oid stays None → "unknown".
    else:
        # Resolve the verdict against a stable head (see the head/verdict
        # pairing note above). Issue #413: a freshly created PR may have
        # no check rows yet, so an empty / unparseable JSON response is
        # "still observing" rather than the final verdict.
        # Issue #695: the loop above already drove `verdict` to a
        # decided, non-None result — either directly from
        # `_self_poll_watch`, or from `_resolve_final_status` (whose
        # `exit_code=8` neutral "Checks pending" placeholder degrades a
        # totally-unparseable probe to `indeterminate` rather than
        # fabricating a passed/failed guess, per Issue #685 intent).
        assert verdict is not None
        status = verdict["status"]
        fail_count = verdict["fail_count"]
        pending_count = verdict["pending_count"]
        total_checks = verdict["total_checks"]
        probe_attempts = verdict["probe_attempts"]
        head_after = _fetch_head_oid(pr, repo)
        if (head_before is not None and head_after is not None
                and head_before != head_after):
            # The branch advanced somewhere across the watch+resolve phase:
            # the verdict may describe a different commit than the live
            # head, and the new head's checks may still be running. Don't
            # record it — hand control back to main to restart the full
            # self-poll ci-watch for the new head (which blocks until it
            # completes), rather than tagging a stale head or
            # short-circuiting on the JSON resolver's budget.
            sys.stderr.write(
                f"pr_watch: PR #{pr} head advanced across the ci-watch "
                f"phase ({_short_head(head_before)} -> "
                f"{_short_head(head_after)}); restarting ci-watch for the "
                "new head.\n"
            )
            return "head_changed", 0, head_after
        # Anchor on the pre-watch head; fall back to the post-resolution
        # read only if the pre-watch probe failed.
        head_oid = head_before if head_before is not None else head_after
    head_short = _short_head(head_oid)

    # Issue #413: duration is measured from the start of the watch to
    # the moment we have a final verdict (post-retry), so a
    # transient-empty race no longer reports `1s`.
    duration = int(round(time.monotonic() - started))

    # Codex review (round 1, Major): the script's exit code must reflect
    # the resolved verdict. Issue #695: there is no longer a raw gh
    # process exit code to consult at all (the self-poll loop only ever
    # calls `gh pr checks --json`), so the mapping below is the sole
    # source of truth. It mirrors :func:`_classify` (gh's documented
    # codes: 0=passed, 2=canceled, 8=incomplete) and adds 1 for failed.
    # Issue #685: `indeterminate` (verdict undetermined) maps to 8 as
    # well — like `incomplete` it is not a clean pass/fail for `$?`
    # callers. `status` is always one of the five keys below, so the
    # ``.get`` default is unreachable in practice. The later merge-watch
    # block can still override ``0`` → ``9`` when the post-CI loop itself
    # fails.
    exit_code = {
        "passed": 0,
        "failed": 1,
        "canceled": 2,
        "incomplete": 8,
        "indeterminate": 8,
    }.get(status, 8)

    # Issue #685: additive payload enrichment. Per-bucket counts ride
    # along whenever the verdict came from a parseable probe; an
    # `indeterminate` verdict instead carries the retry schedule so the
    # monitoring side can tell "re-invoke pr_watch" from a stalled gate.
    extra: dict = {}
    if total_checks is not None:
        extra["fail_count"] = fail_count
        extra["pending_count"] = pending_count
        extra["total_checks"] = total_checks
    if status == "indeterminate":
        extra["retry_recommended"] = True
        extra["retry_after_sec"] = RETRY_INTERVAL_SEC
        extra["probe_attempts"] = probe_attempts

    _record_ci_completed(
        db_path=db_path,
        pr=pr,
        repo=repo,
        status=status,
        duration=duration,
        head=head_short,
        extra=extra,
    )

    # Issue #326: nudge secretary as soon as the CI verdict is recorded
    # so it doesn't have to poll the DB. Best-effort — silent fallback
    # in non-renga environments (RENGA_SOCKET unset). Issue #413: the
    # peer notification, like the DB event, fires once per ci-watch
    # round and only on the final verdict (the retry loop above already
    # absorbed transient incomplete observations). Issue #636: the head
    # tag lets the secretary distinguish a re-emitted CI_COMPLETED for a
    # new head from the original one. If a progress channel is ever
    # wanted, route it through a distinct event/message name (e.g.
    # `ci_progress`) rather than overloading `CI_COMPLETED`.
    ci_msg = (
        f"CI_COMPLETED: PR #{pr} {status} "
        f"(head={head_short or 'unknown'}, duration {duration}s, repo {repo})"
    )
    # Issue #685: give the human-facing message the same disambiguation
    # the payload got — name the fail count on a red, and flag an
    # undetermined verdict as retry-recommended (not a stall).
    if status == "indeterminate":
        ci_msg += " [verdict undetermined; retry recommended]"
    elif status == "failed" and fail_count:
        ci_msg += f" [{fail_count} of {total_checks} checks failed]"
    # Path A (low-latency push) with fail-loud recording; path B (the
    # dispatcher outbox relay) guarantees delivery of the ci_completed
    # event above regardless of whether this push lands.
    _notify_or_record(ci_msg, db_path=db_path, failed_kind="ci_completed",
                      pr=pr)

    sys.stdout.write(
        f"pr_watch: PR #{pr} {status} "
        f"({duration}s, head={head_short or 'unknown'})\n"
    )
    return status, exit_code, head_oid


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

    # Issue #636: ci-watch → merge-watch is now a loop, not a one-shot.
    # Each round watches the PR's CI, records the head observed at verdict
    # time, and (when --merge-watch is on and CI passed) polls for merge
    # against that head. If the merge-watch loop observes the head move to
    # a new commit, it returns "head_changed" and we loop back to ci-watch
    # for the new head — so the secretary always sees a CI_COMPLETED for
    # the *current* head and never approves a merge against a stale
    # verdict. The merge-watch 24h timeout resets each round because
    # _watch_for_merge recomputes its own deadline on entry (a moving head
    # is a sign of active work, so resetting the human-intervention grace
    # is correct — Issue #636 design note 6).
    merge_watch = bool(args.merge_watch and not args.no_merge_watch)
    try:
        return _watch_loop(pr=args.pr, repo=repo, interval=args.interval,
                           merge_watch=merge_watch)
    except Exception as exc:  # noqa: BLE001
        # Refs #653 #658: a watcher that dies on an unexpected exception
        # would otherwise leave no terminal record — the secretary never
        # learns the watch stopped. Record a canonical pr_watch_aborted
        # event (the dispatcher outbox relay surfaces it) and best-effort
        # push, then re-raise so exit behavior is unchanged. KeyboardInterrupt
        # (BaseException) is intentional cancel and propagates untouched —
        # the canceled verdict is already recorded as ci_completed.
        try:
            _record_event(
                db_path=JOURNAL_PATH,
                kind="pr_watch_aborted",
                payload={
                    "pr": args.pr, "repo": repo,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            _notify_or_record(
                f"PR_WATCH_ABORTED: PR #{args.pr} "
                f"({type(exc).__name__}: {exc})",
                db_path=JOURNAL_PATH, failed_kind="pr_watch_aborted",
                pr=args.pr)
        except Exception:  # noqa: BLE001 — never mask the original failure
            pass
        raise


def _watch_loop(*, pr: int, repo: str, interval: int,
                merge_watch: bool) -> int:
    """The ci-watch → merge-watch loop, extracted so :func:`main` can wrap
    it with pr_watch_aborted recording (Refs #653 #658)."""
    while True:
        status, exit_code, head_oid = _run_ci_watch_phase(
            pr=pr, repo=repo, interval=interval,
            db_path=JOURNAL_PATH,
        )

        if status == "head_changed":
            # The head moved while resolving this round's verdict; no
            # verdict was recorded. Restart the full ci-watch for the new
            # head (re-emitting CI_COMPLETED once it resolves stably).
            continue

        # Issue #317: only enter merge-watch when CI actually passed and
        # the caller explicitly opted in via --merge-watch. The default
        # is off so pr_watch stays a "CI passed → return" command
        # compatible with secretary's 2c/T6 review-feedback loop.
        # Codex pre-design review (Minor 1): `run_complete_on_merge` is
        # the downstream actor for the green-PR path and is invoked only
        # when `status == "passed"`. `incomplete` / `failed` /
        # `canceled` results never trigger merge-watch.
        if not (status == "passed" and merge_watch):
            return exit_code

        merge_result = _watch_for_merge(
            pr=pr,
            repo=repo,
            interval=interval,
            db_path=JOURNAL_PATH,
            baseline_head=head_oid,
        )

        if merge_result == "head_changed":
            # A new commit landed on the PR branch during merge-watch.
            # Loop back to ci-watch for the new head (re-emitting
            # CI_COMPLETED for it). No exit-code mutation — the next
            # round computes a fresh verdict.
            continue

        # Codex Major: surface merge-watch failure modes via exit code
        # so callers can distinguish "CI passed but PR did not merge in
        # 24h" / "helper raised" from "CI passed and we successfully
        # transitioned the run". Don't override a non-zero CI exit
        # code — that already signaled trouble.
        if exit_code == 0 and merge_result in (
            "timeout", "error", "no_run", MERGE_RESULT_HEAD_UNCONFIRMED,
        ):
            # Codex round-2 Major: no_run means we observed a merge but
            # could not resolve the PR back to a runs row, so the
            # status flip didn't happen and the secretary needs to
            # intervene. Issue #636: merged_head_unconfirmed means the PR
            # merged at a head whose CI we never confirmed. Surface both
            # as exit 9 so callers don't treat them as success.
            exit_code = 9

        return exit_code


if __name__ == "__main__":
    sys.exit(main())
