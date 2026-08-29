#!/usr/bin/env python3
"""Decide whether a PR is *actually* being watched, from the event log (Refs #978).

## Why this exists

On 2026-08-29 a PR went through the ordinary CI-fail loop: CI red -> the
worker pushed a fix -> the secretary re-pushed -> and then nobody restarted
the CI watcher. The secretary checked ``list_panes``, saw a live
``pr-watch-73`` pane, and concluded monitoring was alive. It was not: the
pane belonged to the watcher started before the *previous* push, which had
already emitted its verdict and exited. Self-close does not work on the
herdr / renga transports (only the broker tmux backend closes a finished
watcher pane), so a dead watch and a live watch look identical from the
outside. The run then sat unwatched until a human noticed.

The mistake was not carelessness, it was using the wrong predicate. "Is a
pane named ``pr-watch-<N>`` present?" answers a question about window
decoration. The question that matters is:

    **Is there a watcher that started AFTER the last push?**

That one is answerable from evidence the org already records. Every push
appends ``fix_pushed`` (the secretary is contractually required to emit it
-- see the org-pull-request skill), every watcher launch appends
``pr_watch_pane_started``, and every watch that ends appends a terminal
event (``ci_completed`` / ``pr_merged`` / ...). Their time ordering in the
``events`` table is the whole answer, and deriving it belongs in a
deterministic tool rather than in prose an operator re-derives under
pressure at the exact moment they are already distracted by a red CI.

## Two surfaces, one predicate

This module is the auditable half: a standalone CLI with exit codes, whose
output carries every row the decision was made from, so a verdict can be
checked rather than trusted. :func:`evaluate` is the same predicate as an
importable function.

The unforgettable half lives in :mod:`tools.journal_append`: after a
``fix_pushed`` row is committed, it calls :func:`evaluate` for that task's
PR and prints the remediation block on stderr. A separate tool can be
forgotten -- that is precisely the failure this guard exists to prevent --
whereas the ``fix_pushed`` emission cannot be, because it is the event that
records the push in the first place.

## Verdicts

``live``
    The newest watcher started strictly after the baseline push and no
    terminal event has arrived since. Monitoring is alive.
``completed``
    A watcher started after the baseline push and a terminal event arrived
    at or after it carrying an actual CI verdict (``passed`` / ``failed``)
    that is not contradicted by the head, or the PR was merged. Monitoring
    for the current head is legitimately over.
``ended_inconclusive``
    A watcher started after the baseline push, but the watch **stopped
    without answering**: a ``ci_completed`` whose status is ``incomplete``
    (checks stayed pending), ``indeterminate`` (no probe was ever readable
    -- that row even carries ``retry_recommended``, the watcher asking to be
    restarted), or ``canceled``, or a ``pr_watch_aborted`` /
    ``pr_merge_watch_timeout``, or a status field that was never recorded.
    Nobody is watching the current head, which is exactly the state this
    tool exists to surface, so it trips. Roughly 7% of the
    ``ci_completed`` rows in the live DB end this way, so this is a real
    path and not a theoretical one.
``ended_stale_head``
    A watcher started after the baseline push and a verdict was reached,
    but its head demonstrably belongs to an **older** push, so the current
    head is unwatched.
``stale``
    Watchers exist for this PR, but the newest one started at or before the
    baseline push. **This is the Issue #978 incident.**
``missing``
    No ``pr_watch_pane_started`` for this PR at all.
``unresolved``
    No PR could be resolved for the given task.

## Matching rules (asymmetric on purpose)

PR numbers are normalised to a canonical int-as-string on both sides,
because payloads carry both ``73`` and ``"73"``; a payload PR that is not
an integer never matches. Repos are compared case-insensitively with a
trailing ``.git`` stripped. Repo is part of the key at all because PR
numbers collide across repos -- the secretary runs from the ja root cwd and
routinely handles PR #N in two different repositories on the same day.

The asymmetry is deliberate and load-bearing:

* **Positive evidence of monitoring requires an explicit repo match.** A
  ``pr_watch_pane_started`` row with no ``repo`` field is NOT counted as a
  watcher -- it cannot prove that *this* PR is being watched. It is
  reported as ``ignored_unknown_repo`` so the exclusion is visible rather
  than silent.
* **Terminal evidence accepts a missing repo as evidence, but never as a
  conclusion.** A row that may have ended monitoring is counted even when
  under-specified -- dropping it would let a finished watch read as
  ``live``. It cannot, however, produce a non-tripping verdict: a repo-less
  ``pr_merged`` or legacy ``ci_completed`` belonging to a same-numbered PR
  in another repository would otherwise certify this one as accounted for.
  Such a terminal yields ``ended_inconclusive`` instead. 45 of the live
  DB's ``pr_merged`` rows carry no repo, so this is a real row shape.

Both directions therefore fail toward "warn the operator", never toward
"silently OK", which is the only safe bias for a guard whose false negative
is an unwatched production PR.

## The head is a demoter, not a requirement

Once a watcher is known to have started after the push and to have reached
a verdict, the head sha can only ever *demote* that verdict -- by proving
it belongs to an older push. It cannot be required, because it is often
simply unrecorded on one side or both: in the live DB no ``fix_pushed`` row
carries the documented ``commit`` key at all (47 of 69 write ``head``
instead, and 22 mention the sha only inside free-text ``note``), and about
a third of ``ci_completed`` rows carry no ``head``. Tripping on an absent
sha would fire on healthy watches, and a guard that cries wolf on the
normal case teaches the reader to skim -- which is the same reflex that
produced the original incident. So ``ended_stale_head`` is emitted only
when **both** shas are readable and they disagree; an unreadable sha
degrades to ``completed`` with the missing evidence visible in the output.
:data:`BASELINE_SHA_KEYS` is why the documented key and the emitted key are
both read.

## A watch can end before its launch is recorded

The skill spawns the watcher pane, verifies it, and only then appends
``pr_watch_pane_started``. A watch that finds CI already red can therefore
emit ``ci_completed`` and exit while the operator is still on the
verification step, leaving the terminal event sorted *before* the launch
row. Anchoring the terminal window on the watcher alone would find nothing
after it and answer ``live`` forever for a watch that is already over --
the guard's own failure mode, reintroduced one level down.

So watches and terminals are paired greedily in time order (see
:func:`_terminal_of`): each watcher, oldest first, claims the earliest
still-unclaimed terminal at or after its own start, and a terminal left
unclaimed after the baseline push is attributed to the newest watcher. The
greediness is what keeps this honest in both directions -- an earlier
watcher only disowns a terminal it has not already consumed -- so
``push -> watcher -> indeterminate -> restart`` (the documented response to
an inconclusive verdict) reads as ``live``, while
``watcher1 -> failed -> push -> watcher2's terminal -> watcher2's launch
row`` reads as ended. The output flags the attribution with
``terminal_precedes_watcher_row`` so the inference is visible rather than
assumed.

### Known boundary: terminals carry no pane identity

One ``pr_watch`` invocation can emit more than one terminal-kind row --
with ``--merge-watch`` it emits ``ci_completed`` and later ``pr_merged`` or
``pr_merge_watch_timeout`` -- and none of those rows names the pane that
wrote it. So when an old pane is still resident while a replacement has
been launched, a late row from the old pane is indistinguishable from the
new watcher's own, and the guard reads the newer watch as ended.

That is a false alarm, and it is accepted rather than fixed: distinguishing
the two would require a pane id in the terminal payload, which no writer
records today. It also fails in the safe direction -- one extra
``/pr-watch-pane`` restart, never a silently unwatched PR -- which is the
bias every other rule here is chosen for. Closing it properly means adding
pane identity to the terminal events, at which point the attribution can
become exact instead of positional.

Ordering compares ``(occurred_at, events.id)``. The schema's default
timestamp has millisecond resolution, and a push followed immediately by a
watcher restart really does land in the same millisecond, so the
autoincrement id is the tie-break. A watcher counts as "after" the push
only when that pair is strictly greater.

The baseline comes from the ``fix_pushed`` rows carrying the task id, so
both entry points must have a task: ``check --task`` has one by
construction, and ``check --pr`` recovers it from the run row holding that
PR (:func:`task_for_pr`). Without that recovery the ``--pr`` form would
have no baseline, the ``stale`` branch would be unreachable, and the
documented shorthand would answer exit 0 on the very incident above.

When there is genuinely no ``fix_pushed`` baseline (no run holds the PR, or
the push was never recorded), the baseline is treated as absent rather than
as "now": any watcher yields ``live`` / ``completed`` per the terminal
rule, and the output reports ``baseline: null`` so the weaker evidence is
visible instead of being passed off as a clean bill.

## CLI

    python3 tools/watcher_restart_guard.py check --pr 73 --repo owner/name
    python3 tools/watcher_restart_guard.py check --task ja-978-watcher-restart-guard
    python3 tools/watcher_restart_guard.py audit

Exit codes: 0 = monitoring accounted for (``live`` / ``completed``, and for
``audit`` every run checked and none tripped), 3 = a tripping verdict
(``stale`` / ``missing`` / ``ended_stale_head`` / ``ended_inconclusive``),
2 = the PR could not be resolved -- for ``audit`` that means an open PR
went **unchecked**, which is a failure to answer and never a clean bill --
1 = usage / DB / schema error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.state_db.queries import TERMINAL_STATUSES  # noqa: E402

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

WATCHER_KIND = "pr_watch_pane_started"
BASELINE_KIND = "fix_pushed"

# Terminal kinds that end a watch by delivering a CI verdict. Their ``head``
# is what decides whether the verdict describes the current push.
CI_TERMINAL_KINDS: tuple[str, ...] = ("ci_completed",)

# Terminal kinds that end a watch because the PR is merged. A merge needs no
# head corroboration: there is nothing left to watch.
MERGE_TERMINAL_KINDS: tuple[str, ...] = (
    "pr_merged",
    "pr_merged_no_run",
    "pr_merged_head_unconfirmed",
)

# Terminal kinds that end a watch WITHOUT any verdict at all: the watch
# stopped and no CI answer was ever recorded for any head.
ABORT_TERMINAL_KINDS: tuple[str, ...] = (
    "pr_merge_watch_timeout",
    "pr_watch_aborted",
)

TERMINAL_KINDS: tuple[str, ...] = (
    CI_TERMINAL_KINDS + MERGE_TERMINAL_KINDS + ABORT_TERMINAL_KINDS
)

# ``ci_completed`` carries the status under ``status``; a dozen older rows
# in the live DB use ``result`` instead, so both are read.
CI_STATUS_KEYS: tuple[str, ...] = ("status", "result")

# The only two ``ci_completed`` statuses that mean "CI answered". The other
# three that ``tools/pr_watch.py`` can record end the watch WITHOUT an
# answer: ``incomplete`` (checks stayed pending until the retry budget ran
# out), ``indeterminate`` (no probe response was ever parseable -- the row
# even carries ``retry_recommended``, i.e. the watcher itself asking to be
# restarted) and ``canceled``. Reaching one of those means the current head
# is unwatched, which is the property this tool exists to detect, so they
# must trip rather than read as a conclusion. A missing status is treated
# the same way: unknown is not an answer.
CONCLUSIVE_CI_STATUSES: tuple[str, ...] = ("passed", "failed")

# Payload keys that may carry the pushed sha on a ``fix_pushed`` row. The
# event catalog documents ``commit``, but every emitter in practice writes
# ``head`` (in the live DB: 47 of 69 rows carry ``head``, 0 carry
# ``commit``, and the remaining 22 bury the sha in free-text ``note``).
# Reading only the documented key would make the head comparison dead code
# and turn every healthy watch into a false alarm.
BASELINE_SHA_KEYS: tuple[str, ...] = ("commit", "head", "sha")

VERDICT_EXIT_CODES: dict[str, int] = {
    "live": 0,
    "completed": 0,
    "ended_inconclusive": 3,
    "ended_stale_head": 3,
    "stale": 3,
    "missing": 3,
    "unresolved": 2,
}

TRIPPING_VERDICTS: tuple[str, ...] = (
    "stale",
    "missing",
    "ended_stale_head",
    "ended_inconclusive",
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNRESOLVED = 2
EXIT_TRIPPED = 3


def remediation_text(pr: Optional[str], repo: Optional[str]) -> str:
    """The one canonical restart route, and nothing else.

    Naming an alternative here would be an invitation to take it: the
    project rule (.claude/rules/pr-ci-watch.md) makes /pr-watch-pane the
    only sanctioned route, and a PreToolUse hook denies the direct script
    invocations, so a guard that hinted at them would be telling the
    operator to walk into a machine-enforced refusal.
    """
    shown_pr = pr if pr is not None else "<PR>"
    shown_repo = repo if repo is not None else "<owner/name>"
    return (
        f"Restart the watcher: /pr-watch-pane {shown_pr} "
        f"(cross-repo: --repo {shown_repo}).\n"
        "Do NOT start tools/pr-watch.* directly - see "
        ".claude/rules/pr-ci-watch.md."
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def canonical_pr(value: Any) -> Optional[str]:
    """Normalise a PR number from a payload / CLI value to int-as-string.

    Payloads carry both ``73`` and ``"73"`` depending on which writer
    produced the row, and operators type ``#73``. Everything that is not an
    integer PR number returns None and therefore never matches -- a guard
    must not invent an equality it cannot justify.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None
    if not isinstance(value, str):
        return None
    s = value.strip().lstrip("#").strip()
    if not s:
        return None
    try:
        return str(int(s))
    except ValueError:
        return None


def canonical_repo(value: Any) -> Optional[str]:
    """Normalise ``OWNER/REPO`` for comparison (case-folded, no ``.git``)."""
    if not isinstance(value, str):
        return None
    s = value.strip().strip("/")
    if s.lower().endswith(".git"):
        s = s[: -len(".git")]
    s = s.strip()
    if not s:
        return None
    return s.lower()


def _sort_key(row: "EventRow") -> tuple:
    """Total order over events: timestamp first, then the autoincrement id."""
    return (row.occurred_at or "", row.event_id)


def head_corroborates(baseline_commit: Any, head: Any) -> bool:
    """True when ``head`` and ``baseline_commit`` denote the same commit.

    One side is typically a short sha (``pr_watch`` records 7 chars) and the
    other a full one, so the comparison is a prefix match in whichever
    direction is longer. A missing value on either side is NOT corroboration:
    an unresolved head is exactly the case where the guard must warn.
    """
    if not isinstance(baseline_commit, str) or not isinstance(head, str):
        return False
    a = baseline_commit.strip().lower()
    b = head.strip().lower()
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _baseline_sha(payload: dict) -> "tuple[Optional[str], Optional[str]]":
    """The pushed sha on a ``fix_pushed`` payload, and the key it came from.

    Returns ``(None, None)`` when the row records no sha at all -- 22 of the
    69 rows in the live DB only mention it inside a free-text ``note``, and
    a sha the guard cannot read is an absent comparison, never a mismatch.
    """
    for key in BASELINE_SHA_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), key
    return None, None


def _ci_status(payload: dict) -> Optional[str]:
    """The normalised ``ci_completed`` status, or None when unrecorded."""
    for key in CI_STATUS_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


# ---------------------------------------------------------------------------
# Event access
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventRow:
    event_id: int
    occurred_at: str
    kind: str
    payload: dict

    def to_jsonable(self) -> dict:
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "kind": self.kind,
            "payload": self.payload,
        }


def _load_events(conn: sqlite3.Connection, kinds: tuple[str, ...]) -> list[EventRow]:
    placeholders = ",".join("?" for _ in kinds)
    rows = conn.execute(
        "SELECT id, occurred_at, kind, payload_json FROM events "
        f"WHERE kind IN ({placeholders}) ORDER BY occurred_at, id",
        kinds,
    ).fetchall()
    out: list[EventRow] = []
    for row in rows:
        raw = row["payload_json"] if isinstance(row, sqlite3.Row) else row[3]
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out.append(
            EventRow(
                event_id=row["id"] if isinstance(row, sqlite3.Row) else row[0],
                occurred_at=(
                    row["occurred_at"] if isinstance(row, sqlite3.Row) else row[1]
                )
                or "",
                kind=row["kind"] if isinstance(row, sqlite3.Row) else row[2],
                payload=payload,
            )
        )
    return out


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    verdict: str
    exit_code: int
    pr: Optional[str] = None
    repo: Optional[str] = None
    task_id: Optional[str] = None
    baseline: Optional[dict] = None
    watcher: Optional[dict] = None
    terminal: Optional[dict] = None
    watcher_count: int = 0
    ignored_unknown_repo: list = field(default_factory=list)
    # True when the terminal event was attributed to a watcher whose launch
    # row landed after it -- a watch that ended before it was recorded.
    terminal_precedes_watcher_row: bool = False
    reason: str = ""
    remediation: Optional[str] = None

    @property
    def tripped(self) -> bool:
        return self.verdict in TRIPPING_VERDICTS

    def to_jsonable(self) -> dict:
        return asdict(self)


def _baseline_summary(row: EventRow) -> dict:
    commit, key = _baseline_sha(row.payload)
    return {
        "occurred_at": row.occurred_at,
        "commit": commit,
        "commit_key": key,
        "event_id": row.event_id,
    }


def _watcher_summary(row: EventRow) -> dict:
    return {
        "occurred_at": row.occurred_at,
        "pane_id": row.payload.get("pane_id"),
        "event_id": row.event_id,
    }


def _terminal_summary(row: EventRow) -> dict:
    return {
        "kind": row.kind,
        "occurred_at": row.occurred_at,
        "head": row.payload.get("head"),
        # The NORMALISED status, so the evidence cannot contradict the
        # verdict: legacy rows keep it under ``result``, and echoing the raw
        # ``status`` key would print null next to a verdict derived from a
        # value that was plainly there.
        "status": _ci_status(row.payload),
        "repo": row.payload.get("repo"),
        "event_id": row.event_id,
    }


def _terminal_of(
    newest_watcher: EventRow,
    watchers: "list[EventRow]",
    terminals: "list[EventRow]",
    baseline: Optional[EventRow],
) -> Optional[EventRow]:
    """The terminal event that ended ``newest_watcher``, if any.

    Watches and their terminals are paired greedily in time order: each
    watcher, oldest first, claims the earliest still-unclaimed terminal at
    or after its own start. A watcher with no claim is still running.

    The pairing exists because a watch can END BEFORE ITS LAUNCH IS
    RECORDED: the skill spawns the pane, verifies it, and only then appends
    ``pr_watch_pane_started``, so a watch that finds CI already red can emit
    ``ci_completed`` and exit while the operator is still verifying. Its
    terminal then sorts before its own launch row. A rule that only looks
    *after* the newest launch row finds nothing and answers ``live``
    forever for a watch that is already over -- the guard's own failure
    mode, one level down.

    So a terminal left unclaimed after the pairing, and newer than the last
    push, is attributed to the newest watcher. Greedy pairing is what keeps
    that from misfiring in both directions: an earlier watcher only disowns
    a terminal it has not already consumed, so
    ``watcher1 -> failed -> push -> watcher2's terminal -> watcher2's launch
    row`` correctly reports watcher2 as ended (watcher1's claim was spent on
    its own verdict), while ``push -> watcher1 -> indeterminate -> restart``
    -- the documented response to an inconclusive verdict -- correctly
    reports the restart as live.
    """
    ordered_watchers = sorted(watchers, key=_sort_key)
    ordered_terminals = sorted(terminals, key=_sort_key)
    claimed: dict = {}
    used: set = set()
    for w in ordered_watchers:
        for t in ordered_terminals:
            if t.event_id in used:
                continue
            if _sort_key(t) >= _sort_key(w):
                claimed[w.event_id] = t
                used.add(t.event_id)
                break

    own = claimed.get(newest_watcher.event_id)
    if own is not None:
        return own

    unclaimed = [
        t
        for t in ordered_terminals
        if t.event_id not in used
        and (baseline is None or _sort_key(t) > _sort_key(baseline))
    ]
    return unclaimed[-1] if unclaimed else None


def evaluate(
    conn: sqlite3.Connection,
    pr: Any,
    repo: Any,
    task_id: Optional[str] = None,
) -> Verdict:
    """Decide whether ``pr`` in ``repo`` is watched by a post-push watcher.

    ``task_id`` selects the ``fix_pushed`` baseline (its payload ``task``
    field). Without one there is no baseline and the answer degrades to
    "has this PR ever been watched, and did that watch end", which the
    output makes visible via ``baseline: null``.
    """
    pr_key = canonical_pr(pr)
    repo_key = canonical_repo(repo)
    if pr_key is None:
        return Verdict(
            verdict="unresolved",
            exit_code=VERDICT_EXIT_CODES["unresolved"],
            pr=None,
            repo=repo if isinstance(repo, str) else None,
            task_id=task_id,
            reason="no PR number could be resolved",
        )

    events = _load_events(conn, (WATCHER_KIND, BASELINE_KIND) + TERMINAL_KINDS)

    baseline_rows = [
        e
        for e in events
        if e.kind == BASELINE_KIND
        and task_id is not None
        and e.payload.get("task") == task_id
    ]
    baseline = max(baseline_rows, key=_sort_key) if baseline_rows else None

    watchers: list[EventRow] = []
    ignored_unknown_repo: list[dict] = []
    for e in events:
        if e.kind != WATCHER_KIND:
            continue
        if canonical_pr(e.payload.get("pr")) != pr_key:
            continue
        row_repo = canonical_repo(e.payload.get("repo"))
        if row_repo is None:
            # Positive evidence of monitoring must be explicit about which
            # repo it monitors; see the module docstring's asymmetry note.
            ignored_unknown_repo.append(e.to_jsonable())
            continue
        if row_repo != repo_key:
            continue
        watchers.append(e)

    # Terminal evidence splits by whether it can be attributed to THIS repo.
    # A repo-less row is still admitted -- it may well be the end of this
    # watch, and dropping it would let a finished watch read as live -- but
    # it may never produce a non-tripping verdict: with PR numbers colliding
    # across repos, a repo-less `pr_merged` or a legacy `ci_completed` from
    # another repository would otherwise certify this one as accounted for.
    # 45 of the live DB's `pr_merged` rows carry no repo, so this is a real
    # row shape, not a hypothetical one.
    terminals: list[EventRow] = []
    unattributable_terminals: list[EventRow] = []
    for e in events:
        if e.kind not in TERMINAL_KINDS:
            continue
        if canonical_pr(e.payload.get("pr")) != pr_key:
            continue
        row_repo = canonical_repo(e.payload.get("repo"))
        if row_repo is None:
            unattributable_terminals.append(e)
            terminals.append(e)
        elif row_repo == repo_key:
            terminals.append(e)
    unattributable_ids = {e.event_id for e in unattributable_terminals}

    base = Verdict(
        verdict="",
        exit_code=0,
        pr=pr_key,
        repo=repo if isinstance(repo, str) else repo_key,
        task_id=task_id,
        baseline=_baseline_summary(baseline) if baseline else None,
        watcher_count=len(watchers),
        ignored_unknown_repo=ignored_unknown_repo,
    )

    def finish(verdict: str, reason: str) -> Verdict:
        base.verdict = verdict
        base.exit_code = VERDICT_EXIT_CODES[verdict]
        base.reason = reason
        if verdict in TRIPPING_VERDICTS:
            base.remediation = remediation_text(base.pr, base.repo)
        return base

    if not watchers:
        return finish(
            "missing",
            f"no {WATCHER_KIND} event recorded for this PR at all",
        )

    newest_watcher = max(watchers, key=_sort_key)
    base.watcher = _watcher_summary(newest_watcher)

    if baseline is not None and _sort_key(newest_watcher) <= _sort_key(baseline):
        return finish(
            "stale",
            "the newest watcher started at or before the last push "
            f"({newest_watcher.occurred_at} <= {baseline.occurred_at}); a live "
            "pane proves nothing, the watch it belongs to predates the push",
        )

    terminal = _terminal_of(newest_watcher, watchers, terminals, baseline)
    if terminal is None:
        return finish(
            "live",
            "a watcher started after the last push and no terminal event is "
            "attributable to it",
        )
    if _sort_key(terminal) < _sort_key(newest_watcher):
        base.terminal_precedes_watcher_row = True
    base.terminal = _terminal_summary(terminal)

    if terminal.event_id in unattributable_ids:
        # Admitted as evidence that the watch may be over, but it names no
        # repo, so it cannot certify THIS PR's monitoring as accounted for.
        return finish(
            "ended_inconclusive",
            f"a {terminal.kind} for PR #{pr_key} is attributable to this "
            "watcher but carries no repo, so it cannot be confirmed to be "
            f"this PR's ({base.repo}); PR numbers collide across repos, so "
            "treating it as a conclusion would be a guess",
        )

    if terminal.kind in MERGE_TERMINAL_KINDS:
        return finish(
            "completed",
            f"the PR is merged ({terminal.kind}); there is nothing left to watch",
        )

    # Did the watch end with an ANSWER, or did it just stop? This question
    # comes before the head comparison, because a watch that stopped without
    # a verdict leaves the current head unwatched no matter which head it was
    # looking at.
    if terminal.kind in ABORT_TERMINAL_KINDS:
        return finish(
            "ended_inconclusive",
            f"the watch ended with {terminal.kind}, which carries no CI "
            "verdict for any head; nothing is watching the current head",
        )

    status = _ci_status(terminal.payload)
    if status not in CONCLUSIVE_CI_STATUSES:
        shown = status if status is not None else "unrecorded"
        return finish(
            "ended_inconclusive",
            f"the watch ended with {terminal.kind} status {shown!r}, which is "
            "not a CI verdict (checks still pending, unreadable probe, or "
            "canceled); the watch stopped without answering for the current "
            "head",
        )

    # A verdict was reached. From here the head is a DEMOTER, not a
    # requirement: it can prove the answer belongs to an older push, but a
    # head that either side failed to record proves nothing either way, and
    # tripping on that absence would fire on the ~36% of ci_completed rows
    # that carry no head at all.
    baseline_commit, _key = _baseline_sha(baseline.payload) if baseline else (None, None)
    terminal_head = terminal.payload.get("head")
    both_known = (
        isinstance(baseline_commit, str)
        and baseline_commit.strip() != ""
        and isinstance(terminal_head, str)
        and terminal_head.strip() != ""
    )
    if both_known and not head_corroborates(baseline_commit, terminal_head):
        return finish(
            "ended_stale_head",
            f"the watch ended with {terminal.kind} status {status!r} on head "
            f"{terminal_head!r}, which does not correspond to the pushed "
            f"commit {baseline_commit!r}; the verdict belongs to an older "
            "push and the current head is unwatched",
        )
    if both_known:
        return finish(
            "completed",
            f"{terminal.kind} reported {status!r} on head {terminal_head!r}, "
            f"which corresponds to the pushed commit {baseline_commit!r}",
        )
    return finish(
        "completed",
        f"a watcher started after the last push and ended with "
        f"{terminal.kind} status {status!r}; the heads could not be compared "
        f"(pushed commit {baseline_commit!r}, verdict head {terminal_head!r}), "
        "so the verdict is taken to be this push's",
    )


# ---------------------------------------------------------------------------
# PR / repo resolution
# ---------------------------------------------------------------------------


# ``runs.pr_url`` holds a PR web URL, not a git remote, so the shared
# remote-URL matcher in ``tools.resolve_run_repo`` (anchored at end of
# string) does not apply to it. Port handling mirrors that pattern's
# ``:\d+`` allowance so an enterprise-style host with an explicit port
# still parses.
_PR_URL_RE = re.compile(
    r"github\.com(?::\d+)?[:/]([^/:\s]+)/([^/:\s]+?)(?:\.git)?"
    r"/pulls?/(\d+)",
    re.IGNORECASE,
)


def _split_pr_url(url: Any) -> "tuple[Optional[str], Optional[str]]":
    """Return ``(OWNER/REPO, pr)`` for a GitHub PR URL, else ``(None, None)``.

    Case is preserved for the repo -- it is echoed back in the remediation
    line an operator copies -- while matching itself is case-insensitive.
    """
    if not isinstance(url, str):
        return (None, None)
    m = _PR_URL_RE.search(url.strip())
    if not m:
        return (None, None)
    return (f"{m.group(1)}/{m.group(2)}", canonical_pr(m.group(3)))


def _pr_number_from_url(url: Any) -> Optional[str]:
    return _split_pr_url(url)[1]


def _owner_repo_from_url(url: Any) -> Optional[str]:
    """``OWNER/REPO`` from a PR URL, falling back to the git-remote matcher."""
    repo, _pr = _split_pr_url(url)
    if repo is not None:
        return repo
    try:
        from tools.resolve_run_repo import owner_repo_from_url
    except Exception:  # pragma: no cover - bare checkout without the seam
        return None
    try:
        return owner_repo_from_url(url)
    except Exception:  # pragma: no cover - defensive
        return None


@dataclass(frozen=True)
class Resolution:
    pr: Optional[str]
    repo: Optional[str]
    source: str
    error: Optional[str] = None


def resolve_for_task(conn: sqlite3.Connection, task_id: str) -> Resolution:
    """Resolve ``(pr, repo)`` for ``task_id`` from the run row, then events.

    ``runs.pr_url`` is the primary source because it is written by the
    PR-opening helpers in the same transaction as ``pr_state``. The
    fallback is the newest ``pr_opened`` event carrying the task, which
    covers runs whose row was never updated.
    """
    try:
        row = conn.execute(
            "SELECT pr_url FROM runs WHERE task_id = ?", (task_id,)
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        return Resolution(None, None, "error", f"runs lookup failed: {exc}")

    if row is not None:
        pr_url = row["pr_url"] if isinstance(row, sqlite3.Row) else row[0]
        pr = _pr_number_from_url(pr_url)
        repo = _owner_repo_from_url(pr_url)
        if pr is not None:
            return Resolution(pr, repo, "runs.pr_url")

    events = _load_events(conn, ("pr_opened",))
    candidates = [e for e in events if e.payload.get("task") == task_id]
    for e in sorted(candidates, key=_sort_key, reverse=True):
        url = e.payload.get("url")
        pr = _pr_number_from_url(url) or canonical_pr(e.payload.get("pr"))
        repo = _owner_repo_from_url(url) or (
            e.payload.get("repo") if isinstance(e.payload.get("repo"), str) else None
        )
        if pr is not None:
            return Resolution(pr, repo, "pr_opened event")

    return Resolution(
        None,
        None,
        "none",
        f"no pr_url on run {task_id!r} and no pr_opened event for it",
    )


def task_for_pr(
    conn: sqlite3.Connection, pr: Any, repo: Any
) -> Optional[str]:
    """The task_id of the run holding this PR, for the ``--pr`` form.

    Without this the ``--pr`` form has no ``fix_pushed`` baseline, the
    ``stale`` branch is unreachable, and the tool answers exit 0 on the
    exact incident it exists to catch -- while the module docstring and
    ``--help`` both advertise ``--pr`` as an equal way in. So the baseline
    is recovered from the run row rather than left absent.
    """
    pr_key = canonical_pr(pr)
    repo_key = canonical_repo(repo)
    if pr_key is None:
        return None
    try:
        rows = conn.execute(
            "SELECT task_id, pr_url FROM runs "
            "WHERE pr_url IS NOT NULL AND TRIM(pr_url) != '' "
            "ORDER BY id DESC"
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    for row in rows:
        task_id = row["task_id"] if isinstance(row, sqlite3.Row) else row[0]
        pr_url = row["pr_url"] if isinstance(row, sqlite3.Row) else row[1]
        row_repo, row_pr = _split_pr_url(pr_url)
        if row_pr != pr_key:
            continue
        # Same asymmetry as the watcher match: a run whose repo cannot be
        # read is not proof that this PR is that run's.
        if repo_key is not None and canonical_repo(row_repo) != repo_key:
            continue
        if isinstance(task_id, str) and task_id.strip():
            return task_id
    return None


def _home_repo() -> Optional[str]:
    """``OWNER/REPO`` of this checkout's own origin, or None.

    Used only as the last fallback for ``check --pr N`` with no ``--repo``
    and no task: the operator is standing in a repo and asking about a PR
    number, so that repo is the only defensible default. It is never used
    to match "any repo" -- when it cannot be derived the tool exits 2
    rather than guessing.
    """
    try:
        from tools.resolve_worker_layout import _git_origin_url
    except Exception:  # pragma: no cover - bare checkout without the seam
        return None
    try:
        return _owner_repo_from_url(_git_origin_url(_REPO_ROOT))
    except Exception:  # pragma: no cover - defensive
        return None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit(conn: sqlite3.Connection) -> list[Verdict]:
    """Evaluate every run whose PR is still open (or of unknown state).

    Terminal runs and merged / closed PRs are excluded: an unwatched PR
    that is already finished is not an incident, and listing it would train
    the reader to skim past the ones that are.
    """
    placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    rows = conn.execute(
        "SELECT task_id, pr_url FROM runs "
        "WHERE pr_url IS NOT NULL AND TRIM(pr_url) != '' "
        "AND (pr_state IN ('draft','open') OR pr_state IS NULL) "
        f"AND status NOT IN ({placeholders}) "
        "ORDER BY task_id",
        TERMINAL_STATUSES,
    ).fetchall()

    results: list[Verdict] = []
    for row in rows:
        task_id = row["task_id"] if isinstance(row, sqlite3.Row) else row[0]
        pr_url = row["pr_url"] if isinstance(row, sqlite3.Row) else row[1]
        pr = _pr_number_from_url(pr_url)
        repo = _owner_repo_from_url(pr_url)
        if pr is None:
            results.append(
                Verdict(
                    verdict="unresolved",
                    exit_code=VERDICT_EXIT_CODES["unresolved"],
                    task_id=task_id,
                    reason=f"pr_url {pr_url!r} is not a recognisable PR URL",
                )
            )
            continue
        results.append(evaluate(conn, pr, repo, task_id=task_id))
    return results


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_verdict(v: Verdict) -> str:
    lines = [
        f"verdict: {v.verdict} (exit {v.exit_code})",
        f"  pr: {v.pr}  repo: {v.repo}  task: {v.task_id}",
    ]
    if v.baseline:
        lines.append(
            "  baseline push: {occurred_at} commit={commit} "
            "(from payload key {commit_key}, event {event_id})".format(**v.baseline)
        )
    else:
        lines.append("  baseline push: null (no fix_pushed recorded for this task)")
    if v.watcher:
        lines.append(
            "  newest watcher: {occurred_at} pane={pane_id} "
            "(event {event_id})".format(**v.watcher)
        )
    else:
        lines.append("  newest watcher: null")
    if v.terminal:
        lines.append(
            "  terminal: {kind} at {occurred_at} head={head} status={status} "
            "(event {event_id})".format(**v.terminal)
        )
    else:
        lines.append("  terminal: null")
    lines.append(f"  watchers matched: {v.watcher_count}")
    if v.ignored_unknown_repo:
        lines.append(
            f"  ignored_unknown_repo: {len(v.ignored_unknown_repo)} "
            f"{WATCHER_KIND} row(s) matched the PR number but carried no repo, "
            "so they were not counted as proof of monitoring"
        )
    if v.reason:
        lines.append(f"  why: {v.reason}")
    if v.remediation:
        for line in v.remediation.splitlines():
            lines.append(f"  {line}")
    return "\n".join(lines)


def render_audit(results: list[Verdict]) -> str:
    if not results:
        return "no open-PR runs to check."
    lines = []
    for v in results:
        lines.append(render_verdict(v))
        lines.append("")
    counts: dict[str, int] = {}
    for v in results:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    lines.append(
        "summary: " + ", ".join(f"{k}={counts[k]}" for k in sorted(counts))
    )
    return "\n".join(lines)


def _verdict_counts(results: list[Verdict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in results:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools/watcher_restart_guard.py",
        description=(
            "Decide whether a PR has a CI watcher that started AFTER the last "
            "push, from the events table. A live pr-watch pane is not proof: "
            "watcher panes do not self-close on every transport (Refs #978)."
        ),
    )
    p.add_argument(
        "--db-path",
        default=None,
        help=(
            "override the resolved state.db path "
            "(--db-path > $STATE_DB_PATH > discovery)."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="check one PR (by number or by task id).",
        description=(
            "Check one PR. Exit 0 when monitoring is accounted for (live / "
            "completed), 3 when it is not (stale / missing / "
            "ended_stale_head / ended_inconclusive), 2 when the PR could not "
            "be resolved."
        ),
    )
    check.add_argument(
        "--pr",
        default=None,
        help=(
            "PR number, e.g. 73. The fix_pushed baseline is recovered from "
            "the run row that holds this PR."
        ),
    )
    check.add_argument(
        "--task",
        default=None,
        help=(
            "task id; resolves the PR from runs.pr_url (or the newest "
            "pr_opened event) and selects the fix_pushed baseline."
        ),
    )
    check.add_argument(
        "--repo",
        default=None,
        help=(
            "OWNER/REPO. On the --task path it defaults to the repo named by "
            "the run row and never to another one; with --pr alone it falls "
            "back to this checkout's own origin."
        ),
    )
    check.add_argument(
        "--json", action="store_true", help="emit one JSON object instead of text."
    )

    aud = sub.add_parser(
        "audit",
        help="check every run with an open PR.",
        description=(
            "Check every run holding a non-terminal PR. Exit 3 if any run "
            "trips, 2 if none trip but a run's pr_url could not be read (that "
            "PR went unchecked), 0 otherwise."
        ),
    )
    aud.add_argument(
        "--json", action="store_true", help="emit one JSON object instead of text."
    )
    return p


def _open_conn(db_path_arg: Optional[str]) -> "tuple[sqlite3.Connection, Path]":
    from tools.state_db import connect
    from tools.state_db.discover import (
        StateDbSchemaError,
        resolve_state_db_path,
        verify_state_db_schema,
    )

    db_path = resolve_state_db_path(Path(db_path_arg) if db_path_arg else None)
    conn = connect(db_path)
    try:
        verify_state_db_schema(db_path, conn=conn)
    except StateDbSchemaError:
        conn.close()
        raise
    return conn, db_path


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "check":
        if bool(args.pr) == bool(args.task):
            print(
                "watcher_restart_guard: check requires exactly one of --pr / "
                "--task.",
                file=sys.stderr,
            )
            return EXIT_ERROR

    from tools.state_db.discover import StateDbSchemaError

    try:
        conn, _db_path = _open_conn(args.db_path)
    except StateDbSchemaError as exc:
        print(f"watcher_restart_guard: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except sqlite3.DatabaseError as exc:
        print(f"watcher_restart_guard: cannot open state.db: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.command == "audit":
            results = audit(conn)
            if args.json:
                print(
                    json.dumps(
                        {
                            "results": [v.to_jsonable() for v in results],
                            "verdict_counts": _verdict_counts(results),
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(render_audit(results))
            if any(v.tripped for v in results):
                return EXIT_TRIPPED
            # An `unresolved` run was NOT checked -- its pr_url could not be
            # read. Returning 0 would report an unexamined open PR as
            # healthy, which is the silent-miss this tool exists to remove.
            if any(v.verdict == "unresolved" for v in results):
                return EXIT_UNRESOLVED
            return EXIT_OK

        pr = args.pr
        repo = args.repo
        task_id = args.task
        if task_id:
            resolved = resolve_for_task(conn, task_id)
            if resolved.pr is None:
                v = Verdict(
                    verdict="unresolved",
                    exit_code=VERDICT_EXIT_CODES["unresolved"],
                    task_id=task_id,
                    reason=resolved.error or "no PR could be resolved",
                )
                print(
                    json.dumps(v.to_jsonable(), indent=2, ensure_ascii=False)
                    if args.json
                    else render_verdict(v),
                    file=sys.stdout,
                )
                return EXIT_UNRESOLVED
            pr = resolved.pr
            repo = repo or resolved.repo
        elif not repo:
            # Only the no-task form may fall back to this checkout's own
            # origin: the operator is standing in a repo asking about a bare
            # PR number, so that repo is the one defensible reading. On the
            # task path the run row names the repo, and substituting a
            # different one there would let another repo's watcher certify
            # this PR as watched (PR numbers collide across repos).
            repo = _home_repo()
        if not repo:
            print(
                "watcher_restart_guard: could not determine OWNER/REPO for PR "
                f"{pr}; pass --repo OWNER/REPO. (PR numbers collide across "
                "repos, so matching any repo would be a guess.)",
                file=sys.stderr,
            )
            return EXIT_UNRESOLVED

        if task_id is None:
            # Recover the baseline the --task form would have used, so both
            # documented entry points answer the same question.
            task_id = task_for_pr(conn, pr, repo)

        verdict = evaluate(conn, pr, repo, task_id=task_id)
        if args.json:
            print(json.dumps(verdict.to_jsonable(), indent=2, ensure_ascii=False))
        else:
            print(render_verdict(verdict))
        return verdict.exit_code
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# journal_append post-check
# ---------------------------------------------------------------------------

_GUARD_OFF_VALUES = ("off", "0", "false")


def guard_disabled(env: Optional[dict] = None) -> bool:
    """True when $ORG_WATCHER_GUARD switches the post-check off."""
    source = os.environ if env is None else env
    return str(source.get("ORG_WATCHER_GUARD", "")).strip().lower() in _GUARD_OFF_VALUES


def format_post_push_notice(v: Verdict) -> str:
    """The stderr block printed after a ``fix_pushed`` row is committed.

    At this point a tripping verdict is the NORMAL state -- the restart
    happens after the push, by definition -- so the text has to read as the
    next required action rather than as an alarm. An alarm that fires on
    every push is an alarm the reader learns to skip, which is how the
    Issue #978 incident happened in the first place.
    """
    if not v.tripped:
        return (
            f"[watcher-guard] PR #{v.pr} ({v.repo}): {v.verdict} - a watcher "
            "started after this push is already on record."
        )
    lines = [
        f"[watcher-guard] NEXT ACTION: PR #{v.pr} ({v.repo}) has no CI watcher "
        "for the commit you just pushed.",
        f"[watcher-guard] evidence: {v.verdict} - {v.reason}",
    ]
    if v.watcher:
        lines.append(
            "[watcher-guard] a pr-watch pane may still be on screen (pane "
            f"{v.watcher.get('pane_id')}); watcher panes do not self-close on "
            "every transport, so a visible pane is not proof of monitoring."
        )
    for line in remediation_text(v.pr, v.repo).splitlines():
        lines.append(f"[watcher-guard] {line}")
    return "\n".join(lines)


def post_push_check(
    conn: sqlite3.Connection,
    payload: dict,
    stream=None,
) -> Optional[Verdict]:
    """Evaluate the guard for a just-committed ``fix_pushed`` payload.

    Returns the verdict (for tests) or None when nothing could be checked.
    Callers must treat every exception as non-fatal: see
    :mod:`tools.journal_append`.
    """
    if stream is None:
        stream = sys.stderr
    task_id = payload.get("task")
    if not isinstance(task_id, str) or not task_id.strip():
        return None
    task_id = task_id.strip()

    resolved = resolve_for_task(conn, task_id)
    if resolved.pr is None:
        return None
    # No _home_repo() fallback here: on this path the task IS known, so
    # substituting this checkout's origin could match a same-numbered PR in
    # another repo and report someone else's watcher as this push's. An
    # unreadable repo is not evidence of monitoring, so say so rather than
    # going quiet -- silence is what the guard exists to remove.
    repo = resolved.repo
    if not repo:
        lines = [
            f"[watcher-guard] NEXT ACTION: could not determine OWNER/REPO for "
            f"PR #{resolved.pr} (task {task_id}), so the watcher state could "
            "not be checked.",
        ]
        for line in remediation_text(resolved.pr, None).splitlines():
            lines.append(f"[watcher-guard] {line}")
        stream.write("\n".join(lines) + "\n")
        return None

    verdict = evaluate(conn, resolved.pr, repo, task_id=task_id)
    stream.write(format_post_push_notice(verdict) + "\n")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
