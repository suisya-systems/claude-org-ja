#!/usr/bin/env python3
"""Work-discovery triage — Phase 1 computation layer (Issue #520).

This is the **deterministic, side-effect-free computation layer** described
in ``docs/design/work-discovery-triage.md`` §3 (二層構造) / §4 (triage 基準)
/ §5 (出力フォーマット). It reads open Issues (via the GitHub CLI ``gh``,
read-only), ranks the ones whose dependencies are resolved, and prints a
single candidate JSON object to stdout. It does **nothing else**.

Invariants enforced here (design §7):

* **INV-1 / INV-3 — read-only, side effects zero.** Only ``gh`` *read*
  subcommands are invoked (``gh issue list``, ``gh pr list``; the repo is
  taken from ``--repo`` or gh's current-repo default). No write API, no
  ``git``, no ``spawn`` / ``commit`` /
  ``PR``, and — unlike delivery-layer tools — **no journal / state.db
  write either** (journal bookkeeping is the delivery layer's job, design
  §7.1 「副作用ゼロの担保」). The scan never decides to start work; it only
  proposes (INV-1 propose-only).
* The tool is a **pure function of its inputs** at heart: ``scan()`` and
  every helper below take already-fetched data and return the result dict
  with no I/O, so the same input always yields the same output (design §4
  再現性契約) and so it works equally as a "startup one-shot scan"
  (design §11-4) without any delivery wiring.

Machine-readable contract (design §5.1), modelled on
``tools/check_curate_threshold.py``:

* stdout — a single JSON object (see ``scan()`` for the schema).
* exit code — the delivery layer branches on this, **not** on JSON parsing:

  - ``0``  — ``no_candidates``: zero candidates after triage.
  - ``10`` — ``candidates_found``: at least one ranked candidate.
  - ``2``  — ``error``: unexpected failure (``gh`` missing / API error /
    bad JSON). ``status=error`` is printed with an ``error`` field.

  ``10`` (not ``1``) so an uncaught Python traceback — which exits ``1`` —
  can never be misread as "candidates found"; and ``0`` cleanly means
  "no candidates" without colliding with the crash code (design §5.1).

Calibration of the three §11 open points against this repo's real Issues
(``gh label list`` / ``gh issue list`` / ``gh pr list`` on 2026-06-10):

* **Priority labels (§11-2)**: this repo has **no** ``priority:*`` / ``p0..p2``
  labels and **no milestones**. Priority therefore degrades exactly as
  §4.1 prescribes: a ``backlog`` / ``wontfix`` label → ``low``; otherwise
  ``medium`` (the generic ``priority:*`` / ``p0..p2`` matchers are kept so
  the contract still works on repos that do have them). Recency
  (``updatedAt``) is used only as a ranking tiebreaker + signal, never to
  promote/demote the priority *level* (keeps the level deterministic from
  metadata).
* **Dependency notation (§11-3)**: real blockers use ``Blocked by #N`` /
  ``Depends on #N`` / ``Requires #N``. Crucially, ``Parent: #N``,
  ``Design: PR #N``, ``Refs #N``, ``Closes #N``, ``Discovered while
  working on #N`` and bare ``#N`` are **NOT** blockers — matching any of
  them would wrongly exclude live candidates (the §11-3 over-matching
  risk). The extractor keys off the three blocking keywords and pulls
  ``#N`` only from the trailing clause, so ``Depends on: Commit 1
  follow-up Issue`` (real #177/#178, no ``#N``) yields *no* refs.
* **N default (§11-1)**: fixed ``N=3`` (``--top-n``), configurable.
  ``--free-panes`` is accepted and, when > 0, boosts ``parallelizable``
  candidates in the ranking, but does not change N in Phase 1.

Estimated axes (design §4.4) — ``effort`` / ``parallelizable`` /
``unblocked_by_recent_merge`` — always carry a ``*_estimated`` flag and
contribute entries to the per-candidate ``signals[]`` so a human can audit
*why* the machine guessed what it did. ``truncated_count`` and
``excluded_blocked`` are always emitted (no silent truncation, design §5.1).

Effort learning (design §10「工数見積もりの高度化」): when ``--effort-history``
> 0 the tool learns a repo-calibrated effort model from recently-merged PRs'
*realized* effort (changed lines/files; review_rounds and time-to-merge are
captured as context but excluded from the composite — degenerate here: zero
GitHub reviews, minute-scale merges). The model bridges each PR to the issue
it closed (``closingIssuesReferences``) to measure whether the only
triage-time predictor we have — issue body length — actually correlates with
realized effort. The model overrides the static heuristic ONLY past a
data-driven gate (enough samples AND Spearman ≥ ``MIN_EFFORT_CORRELATION``);
otherwise the static estimate is retained and the reason + realized-effort
context are disclosed in ``signals[]``. On this repo the predictor does not
track effort (ρ ≈ 0), so the gate correctly declines — the model adds audit
context without manufacturing false precision (anti-cognitive-surrender,
§4.4). The learned model summary is echoed in the output as ``effort_model``
(``None`` when learning is disabled/offline). The learning fetch is wired
NON-FATALLY: a gh failure there degrades to the static heuristic, it never
aborts the triage. ``effort_estimated`` stays ``true`` on every estimated
route (learned or static).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

EXIT_NO_CANDIDATES = 0
EXIT_CANDIDATES_FOUND = 10
EXIT_ERROR = 2

DEFAULT_TOP_N = 3
# How many most-recent merged PRs feed the `unblocked_by_recent_merge`
# heuristic (design §4.2 "直近 K 件"). Configurable via --recent-merges.
DEFAULT_RECENT_MERGES = 10
# Row cap for the open-Issue / open-PR fetches. If a fetch returns exactly
# this many rows the result may be truncated, which is surfaced via the
# output's `input_truncated` flags (never silent — design §5.1).
DEFAULT_OPEN_LIMIT = 500

# --- dependency notation (design §4.1, calibrated §11-3) ---------------
# Match a blocking *keyword* (anywhere — body or comment, inline or list-led,
# e.g. "Update: Blocked by #5"). extract_blocking_refs locates every keyword
# occurrence (so multiple clauses on one line — "Blocked by #1; depends on
# #2" — are all seen), derives the same-line text *before* it (for the
# negation guard) and the text *after* it (for the leading refs). Precision
# is enforced by _LEADING_REFS_RE below, not by anchoring: a keyword not
# immediately followed by a `#N` (e.g. "requires careful thought",
# "Depends on: Commit 1 ...") contributes no ref.
#
# Negation guard: a keyword carrying a *negation* shortly before it
# ("not blocked by #5", "no longer blocked by #5", "not currently blocked by
# #5", "doesn't depend on #5") is NOT a blocker — extract_blocking_refs drops
# the clause when `pre` ends with a negation marker optionally followed by a
# short run of plain words/spaces (adverbs like "currently"/"yet"). The run
# is word+space only, so punctuation/clause boundaries stop it: "not a
# blocker, but blocked by #5" still counts (the comma breaks the run). The
# leading `\b` on the keyword also rejects "unblocked by #5" (no word
# boundary inside "unblocked", so the keyword never matches mid-word). Both
# guards prefer *not* excluding (§11-3: false-exclusion is the worse error).
_BLOCK_NEG_RE = re.compile(
    r"(?i)(?:\b(?:not|never|no\s+longer|no\s+more)|n['’]t)[\w\s]{0,20}$"
)
_BLOCK_KEYWORD_RE = re.compile(
    r"(?i)\b(?:blocked\s+by|depends\s+on|requires)\b"
)
# From a blocking clause, consume only the *leading* run of refs
# (`#N`, optionally `PR #N`, comma/and-separated). #N refs are taken from
# this leading run **only** — so `Depends on: Commit 1 ... filed under #80`
# (real #177/#178) yields *no* ref (the clause starts with prose, not a
# `#N`), and `Parent: #N` / `Design: PR #N` / `Refs #N` / `Closes #N` /
# `Discovered while working on #N` / bare `#N` are never misread as
# blockers. This is the §11-3 over-matching guard: prefer *not* excluding.
_LEADING_REFS_RE = re.compile(
    r"^[\s:]*((?:(?:pr\s+)?#\d+[\s,]*(?:and\s+|&\s*)?)+)", re.I
)
# A bare-number ref like `#531`; the leading `(?<![\w/])` stops it firing
# inside `org/repo#531`-style cross-repo refs (single-repo scope, §10).
_ISSUE_REF_RE = re.compile(r"(?<![\w/])#(\d+)\b")
# Unchecked task-list item used as a pending-dependency signal: `- [ ] #N`
# (design §4.1). Counted for NON-epic issues only — an epic's child checklist
# is tracking, not a blocker on the epic itself (calibrated against epic
# #376; see classify_dependency).
#
# §11-3 calibration: the item's content must be a *pure run of issue refs*
# (`- [ ] #11`, `- [ ] #11, #12`). An item with descriptive prose around the
# ref (`- [ ] #123 を参考に確認する`, `- [ ] Fix #11`) is a mere *mention*,
# NOT a blocker — counting it would wrongly exclude a live candidate. Work
# discovery prefers false-inclusion over false-exclusion (誤除外 < 誤包含):
# when in doubt, do not treat the item as a blocker.
_OPEN_TASK_ITEM_RE = re.compile(r"(?im)^[ \t]*[-*]\s*\[ \]\s*(.+?)\s*$")
# True iff the captured task content is nothing but a `#N` ref run.
_PURE_REF_RUN_RE = re.compile(r"(?i)^(?:#\d+[\s,]*(?:and\s+|&\s*)?)+$")

# PR → linked-Issue notation for the recent-merge heuristic (design §4.2).
# §4.2 keeps two *distinct* conditions, so we keep two patterns:
#   * a *closing* keyword (Closes/Fixes/Resolves #N) means the merged PR
#     actually resolved #N — used to decide a blocking ref "was closed by a
#     recent merge";
#   * a *mere reference* (Refs #N) does not close #N — only used to decide
#     "this Issue is referenced by (a natural follow-up of) a recent merge".
# Conflating them would let a bare `Refs #100` mark #100 as resolved.
#
# A single keyword can reference several issues at once
# (`Closes #100, #101 and #102`), so both patterns capture the whole leading
# run of comma/space/`and`-separated `#N` refs (mirroring `_LEADING_REFS_RE`);
# `_extract_ref_run` then pulls every number with `_ISSUE_REF_RE`. Capturing
# only the first would silently drop the 2nd+ issue from
# `recent_merge_closed_issues` / `recent_merge_referenced_issues` (hurts the
# unblocked_by_recent_merge axis).
#
# The keyword may be followed by a colon (`Closes: #1`, `Fixes: #1`) — a
# common GitHub notation — so the separator is `[\s:]+`, not just `\s+`.
_KEYWORD_REF_RUN = r"[\s:]+((?:#\d+[\s,]*(?:and\s+|&\s*)?)+)"
_PR_CLOSE_RE = re.compile(
    r"(?i)\b(?:closes|close|closed|fixes|fix|fixed|resolves|resolve|resolved)"
    + _KEYWORD_REF_RUN
)
_PR_REF_RE = re.compile(r"(?i)\b(?:refs|ref|re)" + _KEYWORD_REF_RUN)


def _extract_ref_run(pattern: re.Pattern, text: str) -> set[int]:
    """All issue numbers in `text`'s keyword-led `#N` runs (design §4.2).

    Every `#N` in the leading run after each keyword is captured, so
    `Closes #100, #101` / `Refs #100, #101` yield ``{100, 101}`` — not just
    the first.

    A keyword negated on the same line (`does not close #100`,
    `no longer closes #100`) is skipped — this is exactly GitHub's auto-close
    false-positive that reopened #520, applied here so a recent merge that
    *disclaims* closing #N does not wrongly mark #N resolved.
    """
    nums: set[int] = set()
    for match in pattern.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if _BLOCK_NEG_RE.search(text[line_start : match.start()]):
            continue  # negated keyword ("does not close #100") — not a close
        nums.update(int(n) for n in _ISSUE_REF_RE.findall(match.group(1)))
    return nums


def _pr_close_refs(text: str) -> set[int]:
    """Issue numbers a PR *closes* (Closes/Fixes/Resolves #N, …)."""
    return _extract_ref_run(_PR_CLOSE_RE, text)


def _pr_referenced_refs(text: str) -> set[int]:
    """Issue numbers a PR merely *references* (Refs/Ref/Re #N, …)."""
    return _extract_ref_run(_PR_REF_RE, text)

# Labels that force `blocked` regardless of refs (design §4.1).
_BLOCK_LABELS = {"blocked", "on-hold", "on hold"}
# Labels that force priority `low` when no explicit priority label exists.
_LOW_PRIORITY_LABELS = {"backlog", "wontfix"}

_PRIORITY_LABEL_RE = re.compile(
    r"(?i)^(?:priority[:/\s-]*)?(high|medium|med|low|p0|p1|p2)$"
)
_SIZE_LABEL_RE = re.compile(r"(?i)^(?:size|effort)[:/\s-]*(s|m|l|xs|xl)$")
# A standalone size label like `S` / `M` / `L`.
_BARE_SIZE_RE = re.compile(r"(?i)^(xs|s|m|l|xl)$")

_PRIORITY_RANK = {"high": 2, "medium": 1, "low": 0}
_EFFORT_RANK = {"S": 0, "M": 1, "L": 2}

# --- effort learning (design §4.1 / §10「工数見積もりの高度化」) -----------
# Default window of recently-merged PRs whose *realized* effort feeds the
# learned effort model (design §10). Larger than DEFAULT_RECENT_MERGES (which
# wants recency for the unblocked-by-recent-merge axis) because learning wants
# *volume*. Configurable via --effort-history; 0 disables learning entirely.
DEFAULT_EFFORT_HISTORY = 60
# Minimum number of (issue ↔ merged-PR) training pairs before the learned
# model is allowed to *override* the static heuristic. Below this the model
# still reports realized-effort context but does not change the estimate.
MIN_EFFORT_SAMPLES = 8
# Minimum Spearman correlation between the triage-time predictor (issue body
# length) and realized effort before the model overrides the static estimate.
# The override gate is the heart of the anti-cognitive-surrender design: if the
# only predictor we can observe at triage time does not actually track realized
# effort (this repo: ρ ≈ 0 — body length reflects spec verbosity, not code
# size), the model declines to manufacture a point estimate it cannot justify
# and the static heuristic is retained, with the weakness disclosed in signals.
MIN_EFFORT_CORRELATION = 0.3
# Fixed (NOT learned) weight blending changed_files into the realized-effort
# composite: a touched file ≈ this many lines of coordination cost. Held
# constant so the learned degrees of freedom stay limited to the cutpoints
# (design §4.4 / advisor: fewer learned params on a small, noisy sample =
# defensible). review_rounds and time-to-merge are captured as context but
# deliberately excluded from the composite (degenerate here: this org has zero
# GitHub reviews — Codex local review — and merges in minutes, so both are
# dominated by process, not effort).
EFFORT_FILE_WEIGHT = 20


@dataclass
class ScanConfig:
    """Knobs for a scan run (design §11-1 / §8 generated_for)."""

    top_n: int = DEFAULT_TOP_N
    free_panes: int | None = None
    trigger: str = "manual"


# ----------------------------------------------------------------------
# Pure helpers — no I/O. Each takes already-fetched data and is a pure
# function of its arguments (design §4 再現性契約).
# ----------------------------------------------------------------------


def _is_int(value) -> bool:
    """True for a *genuine* int — `bool` is an int subclass, so `True`/`False`
    must be rejected as Issue/PR numbers (else `True` matches PR #1)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _label_names(issue: dict) -> list[str]:
    """Normalize an issue's labels to a list of lowercase name strings.

    Accepts both ``gh``'s ``[{"name": ...}]`` shape and a plain list of
    strings (the latter is convenient for tests / `--from-file`)."""
    out: list[str] = []
    labels = issue.get("labels")
    if not isinstance(labels, list):  # malformed/absent → no labels
        return out
    for label in labels:
        if isinstance(label, dict):
            name = label.get("name")
        else:
            name = label
        if isinstance(name, str):
            out.append(name.strip().lower())
    return out


def _comment_bodies(issue: dict) -> list[str]:
    """Return comment body strings for an Issue (``gh`` ``comments`` field).

    Accepts ``gh``'s ``[{"body": ...}]`` shape and a plain list of strings
    (test / ``--from-file`` convenience). Missing → empty list."""
    out: list[str] = []
    comments = issue.get("comments")
    if not isinstance(comments, list):  # e.g. a bare count, or absent → none
        return out
    for c in comments:
        if isinstance(c, dict):
            body = c.get("body")
        else:
            body = c
        if isinstance(body, str):
            out.append(body)
    return out


def dependency_text(issue: dict) -> str:
    """Concatenate body + all comment bodies for dependency scanning.

    Design §4.1 says blockers may live in the Issue **body or comments**
    (a blocker added later in a comment must still be detected), so the
    dependency extractor reads both."""
    parts = [issue.get("body") or ""]
    parts.extend(_comment_bodies(issue))
    return "\n".join(parts)


def extract_blocking_refs(text: str | None, *, is_epic: bool) -> list[int]:
    """Return Issue/PR numbers this Issue is blocked by / depends on.

    ``text`` is the Issue body concatenated with its comments (see
    ``dependency_text``). Only the three blocking keywords (`Blocked by` /
    `Depends on` / `Requires`) contribute, and only via the `#N` refs in
    their trailing clause (design §4.1, §11-3 calibration). For non-epic
    issues an unchecked task-list item `- [ ] #N` also counts as a pending
    dependency; for epics it does not (child checklists are tracking).

    Deduplicated, sorted ascending for a stable, reproducible output.
    """
    if not text:
        return []
    refs: set[int] = set()
    for m in _BLOCK_KEYWORD_RE.finditer(text):
        # the keyword's own line, split at the keyword (`.` never crosses
        # newlines in the original regex — keep refs same-line so a keyword on
        # one line and a bare `#N` on the next are NOT linked, §11-3).
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        if _BLOCK_NEG_RE.search(text[line_start : m.start()]):
            continue  # negated clause ("not blocked by #5") — not a blocker
        # Leading refs come from immediately after THIS keyword; the run stops
        # at the next non-ref token, so a following keyword's refs are picked
        # up by that keyword's own iteration ("Blocked by #1; depends on #2").
        lead = _LEADING_REFS_RE.match(text[m.end() : line_end])
        if not lead:
            continue
        for num in _ISSUE_REF_RE.findall(lead.group(1)):
            refs.add(int(num))
    if not is_epic:
        for item in _OPEN_TASK_ITEM_RE.finditer(text):
            content = item.group(1)
            if not _PURE_REF_RUN_RE.match(content):
                continue  # prose-annotated mention, not a blocker (§11-3)
            for num in _ISSUE_REF_RE.findall(content):
                refs.add(int(num))
    return sorted(refs)


def has_block_label(issue: dict) -> bool:
    """True if a `blocked` / `on-hold` label forces unresolved (design §4.1)."""
    return any(name in _BLOCK_LABELS for name in _label_names(issue))


def classify_dependency(
    issue: dict, open_refs: set[int]
) -> tuple[str, list[int]]:
    """Decide `resolved` vs `blocked` for one Issue (design §4.1).

    ``open_refs`` is the set of Issue/PR numbers that are still **open**
    (open issues ∪ open PRs). A blocking ref counts as unresolved iff it is
    in ``open_refs``; a ref that is not (closed issue, merged/closed PR, or
    a number that does not exist) is treated as resolved — deliberately, to
    avoid the §11-3 over-exclusion failure mode.

    Returns ``(status, open_blocking_refs)`` where ``status`` is
    ``"blocked"`` or ``"resolved"`` and ``open_blocking_refs`` is the
    sorted list of refs that are still open (empty when resolved).
    """
    is_epic = "epic" in _label_names(issue)
    blocking = extract_blocking_refs(dependency_text(issue), is_epic=is_epic)
    open_blocking = sorted(n for n in blocking if n in open_refs)
    if has_block_label(issue) or open_blocking:
        return "blocked", open_blocking
    return "resolved", []


def milestone_title(issue: dict) -> str | None:
    """Return the Issue's milestone title, or ``None`` (``gh`` ``milestone``).

    Accepts ``gh``'s ``{"title": ...}`` object, a plain title string, or
    ``None``."""
    ms = issue.get("milestone")
    if isinstance(ms, dict):
        title = ms.get("title")
        return title if isinstance(title, str) and title else None
    if isinstance(ms, str) and ms:
        return ms
    return None


def compute_priority(issue: dict) -> tuple[str, list[str]]:
    """Compute `high`/`medium`/`low` priority + signals (design §4.1).

    Tier order per §4.1 — **label > milestone > recency**:

    1. an explicit priority label (`priority:*` / `p0..p2`) → its level;
    2. a `backlog` / `wontfix` label → `low`;
    3. otherwise `medium` (the default). A milestone, when present, does
       not change the *level* (mapping a milestone to high/low needs a
       due-date policy, deferred to §9 future work) but is emitted as a
       signal and used as a ranking tiebreaker (see ``_sort_key`` — a
       milestoned Issue ranks above a non-milestoned one of equal
       priority), which is exactly the "milestone > recency" tier.

    This repo has neither priority labels nor milestones (§11-2), so in
    practice the result is `low` for `backlog`/`wontfix` and `medium`
    otherwise. Recency never changes the level (it is a tiebreaker only),
    keeping the level deterministic from metadata (§4 再現性契約).
    """
    signals: list[str] = []
    for name in _label_names(issue):
        m = _PRIORITY_LABEL_RE.match(name)
        if not m:
            continue
        token = m.group(1).lower()
        level = {
            "high": "high",
            "p0": "high",
            "medium": "medium",
            "med": "medium",
            "p1": "medium",
            "low": "low",
            "p2": "low",
        }.get(token)
        if level:
            signals.append(f"label:{name}")
            return level, signals
    for name in _label_names(issue):
        if name in _LOW_PRIORITY_LABELS:
            signals.append(f"label:{name}")
            return "low", signals
    ms = milestone_title(issue)
    if ms:
        signals.append(f"milestone:{ms} (level not promoted — see §9)")
        return "medium", signals
    signals.append("no priority label/milestone → default medium")
    return "medium", signals


def _count_acceptance_criteria(body: str) -> int:
    """Number of checklist items `- [ ]` / `- [x]` (acceptance criteria)."""
    return len(re.findall(r"(?im)^[ \t]*[-*]\s*\[[ xX]\]", body))


def estimate_effort(
    issue: dict, model: dict | None = None
) -> tuple[str, bool, list[str]]:
    """Return ``(size, estimated, signals)`` — S/M/L (design §4.1 / §10).

    A ``size:S/M/L`` / ``effort:*`` label (or a bare ``S``/``M``/``L``
    label) is authoritative → ``estimated=False``. Otherwise the size is
    *estimated* (``estimated=True``, design §4.4: estimated values must say
    so) by one of two routes:

    * **Learned (design §10)** — when ``model`` was learned from realized
      merged-PR effort *and* its override gate fired (enough samples AND the
      issue-body predictor actually correlates with realized effort), the
      body length is bucketed against the model's repo-calibrated cutpoints.
    * **Static fallback** — otherwise the original heuristic over body length
      + acceptance-criteria count applies, unchanged. When a ``model`` exists
      but its gate declined (e.g. this repo, where body length does not track
      realized effort), the static estimate is kept and the *reason* plus the
      realized-effort context are appended to ``signals[]`` so a human sees
      both the estimate and why the machine did not over-claim.
    """
    for name in _label_names(issue):
        m = _SIZE_LABEL_RE.match(name) or _BARE_SIZE_RE.match(name)
        if not m:
            continue
        raw = m.group(1).upper()
        size = {"XS": "S", "S": "S", "M": "M", "L": "L", "XL": "L"}.get(raw)
        if size:
            return size, False, [f"label:{name}"]

    body = issue.get("body") or ""
    length = len(body)
    criteria = _count_acceptance_criteria(body)

    # Learned route: only when the model's data-driven gate fired. Bucket the
    # body length against repo-calibrated cutpoints (larger body → larger
    # effort holds *because* the gate verified a positive correlation).
    if model and model.get("applies") and model.get("predictor_cutpoints"):
        t1, t2 = model["predictor_cutpoints"]
        size = "S" if length <= t1 else ("M" if length <= t2 else "L")
        signals = [
            f"learned effort: body_len={length} vs repo-calibrated cutpoints "
            f"S≤{round(t1)}<M≤{round(t2)}<L "
            f"(n={model.get('sample_size')}, ρ={model.get('predictor_correlation')})",
        ]
        median_lines = model.get("realized_median_lines")
        if median_lines is not None:
            signals.append(
                f"realized basis: recent merged tasks median {median_lines} "
                f"changed lines / {model.get('realized_median_files')} files"
            )
        return size, True, signals

    # Static fallback heuristic (documented buckets, kept identical to the
    # pre-learning behaviour so it is the safe default):
    #   L: long body OR many acceptance criteria (broad scope)
    #   S: short body AND few criteria
    #   M: everything in between
    if length >= 2000 or criteria >= 8:
        size = "L"
    elif length < 600 and criteria <= 2:
        size = "S"
    else:
        size = "M"
    signals = [
        f"estimated effort from body_len={length}, acceptance_criteria={criteria}"
    ]
    # If a model was attempted but its gate declined, disclose *why* and show
    # the realized-effort context, so the human is not left thinking the
    # estimate is uninformed (anti-cognitive-surrender, design §4.4).
    if model is not None and not model.get("applies"):
        reason = model.get("reason")
        if reason:
            signals.append(f"effort model not applied — {reason}")
        median_lines = model.get("realized_median_lines")
        if median_lines is not None:
            signals.append(
                f"realized context: recent merged tasks median {median_lines} "
                f"changed lines / {model.get('realized_median_files')} files "
                f"(n={model.get('sample_size')})"
            )
        for note in model.get("degenerate_signals", []):
            signals.append(f"signal note: {note}")
    return size, True, signals


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation of two equal-length numeric sequences.

    Uses average ranks for ties (standard Spearman). Returns ``0.0`` for
    degenerate input (n<2, mismatched lengths, or zero variance) — a neutral
    value that makes the override gate decline rather than fire on noise."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0

    def _ranks(vals: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0  # average rank across a tie run
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((r - mx) ** 2 for r in rx) ** 0.5
    vy = sum((r - my) ** 2 for r in ry) ** 0.5
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy)


def _tertile_cutpoints(values: list[float]) -> tuple[float, float] | None:
    """Two cutpoints splitting ``values`` into ~thirds (``S``/``M``/``L``).

    ``statistics.quantiles(values, n=3)`` → ``[t1, t2]``; deterministic
    (sorts internally). Returns ``None`` when there are fewer than 3 values
    (cannot form two cuts)."""
    if len(values) < 3:
        return None
    q = statistics.quantiles(values, n=3)
    return (q[0], q[1])


def _realized_composite(changed_lines: int, changed_files: int) -> float:
    """Fixed (non-learned) realized-effort composite for one merged PR.

    ``changed_lines`` (additions+deletions) is the primary signal;
    ``changed_files`` is folded in with the documented constant
    ``EFFORT_FILE_WEIGHT`` (secondary). review_rounds / time-to-merge are
    intentionally NOT in the composite (see ``EFFORT_FILE_WEIGHT``)."""
    return changed_lines + EFFORT_FILE_WEIGHT * changed_files


def empty_effort_model(reason: str) -> dict:
    """A full-shape, not-applied effort model carrying only ``reason``.

    Used whenever a model can be reported but nothing was learned (zero
    training pairs, or a NON-FATAL learning-fetch failure). Returning the
    **full key set** (None-filled) — not a partial dict — keeps ``effort_model``
    a single shape across every path, so a consumer can read e.g.
    ``effort_model["predictor_correlation"]`` without a KeyError regardless of
    why learning produced nothing."""
    return {
        "sample_size": 0,
        "predictor": "issue_body_length",
        "predictor_correlation": None,
        "predictor_cutpoints": None,
        "realized_metric": f"changed_lines + {EFFORT_FILE_WEIGHT}*changed_files",
        "realized_cutpoints": None,
        "realized_median_lines": None,
        "realized_median_files": None,
        "realized_median_hours": None,
        "degenerate_signals": [],
        "coverage": None,
        "applies": False,
        "reason": reason,
    }


def learn_effort_model(
    samples: list[dict],
    *,
    min_samples: int = MIN_EFFORT_SAMPLES,
    min_correlation: float = MIN_EFFORT_CORRELATION,
) -> dict:
    """Learn a repo-calibrated effort model from realized merged-PR effort.

    ``samples`` — one dict per merged PR that closed *exactly one* issue,
    pairing the issue's triage-time predictor with the PR's realized effort::

        {"body_len": int, "criteria": int, "changed_lines": int,
         "changed_files": int, "review_rounds": int,
         "hours_to_merge": float | None}

    Pure function (no I/O); deterministic — the result is independent of
    ``samples`` order (every statistic sorts or is order-invariant).

    Returns a model dict (never raises). Its ``applies`` flag is a
    **data-driven override gate**: it fires only when there are enough
    samples AND the issue-body predictor actually correlates with realized
    effort (Spearman ≥ ``min_correlation``). When the predictor does not
    track effort (this repo: ρ ≈ 0), ``applies`` is ``False`` and the caller
    keeps the static estimate — the model still reports realized-effort
    *context* for the human, it just does not manufacture a point estimate it
    cannot justify (design §4.4 anti-cognitive-surrender).
    """
    n = len(samples)
    if n == 0:
        return empty_effort_model(
            "no linked (issue ↔ merged-PR) training samples → static heuristic"
        )

    body_lens = [s["body_len"] for s in samples]
    lines = [s["changed_lines"] for s in samples]
    files = [s["changed_files"] for s in samples]
    composites = [
        _realized_composite(s["changed_lines"], s["changed_files"]) for s in samples
    ]
    reviews = [s.get("review_rounds", 0) for s in samples]
    hours = [s["hours_to_merge"] for s in samples if s.get("hours_to_merge") is not None]

    degenerate: list[str] = []
    if not any(reviews):
        degenerate.append(
            "review_rounds: all zero across samples (no GitHub reviews — "
            "Codex local review) → excluded from composite"
        )
    degenerate.append(
        "time-to-merge: captured as context only, excluded from composite "
        "(dominated by queueing, not effort)"
    )

    realized_cuts = _tertile_cutpoints(composites)
    predictor_cuts = _tertile_cutpoints(body_lens)
    rho = _spearman(body_lens, composites)

    applies = (
        n >= min_samples
        and realized_cuts is not None
        and predictor_cuts is not None
        and rho >= min_correlation
    )
    if applies:
        reason = (
            f"body length tracks realized effort (ρ={round(rho, 2)} ≥ "
            f"{min_correlation}, n={n}) → learned cutpoints applied"
        )
    elif n < min_samples:
        reason = (
            f"insufficient training pairs (n={n} < {min_samples}) → "
            f"static heuristic retained"
        )
    elif realized_cuts is None or predictor_cuts is None:
        reason = f"could not form cutpoints (n={n}) → static heuristic retained"
    else:
        reason = (
            f"body length does not predict realized effort (ρ={round(rho, 2)} "
            f"< {min_correlation}, n={n}) → static heuristic retained"
        )

    return {
        "sample_size": n,
        "predictor": "issue_body_length",
        "predictor_correlation": round(rho, 3),
        "predictor_cutpoints": [round(predictor_cuts[0], 1), round(predictor_cuts[1], 1)]
        if predictor_cuts
        else None,
        "realized_metric": f"changed_lines + {EFFORT_FILE_WEIGHT}*changed_files",
        "realized_cutpoints": [round(realized_cuts[0], 1), round(realized_cuts[1], 1)]
        if realized_cuts
        else None,
        "realized_median_lines": statistics.median(lines),
        "realized_median_files": statistics.median(files),
        "realized_median_hours": round(statistics.median(hours), 2) if hours else None,
        "degenerate_signals": degenerate,
        # Coverage of the learning data (how many linked PRs vs usable samples)
        # is an I/O concern filled in by build_effort_model; None on the pure
        # path (e.g. offline --from-file, where samples are supplied directly).
        "coverage": None,
        "applies": applies,
        "reason": reason,
    }


def estimate_parallelizable(
    open_blocking_refs: list[int],
) -> tuple[bool, list[str]]:
    """Estimate whether the Issue is a dependency-graph leaf (design §4.2).

    Parallelizable iff it has no blocking ref to a still-open Issue — i.e.
    it can be picked up independently to fill a free pane. Always an
    estimate (implicit conflicts not expressed as refs are invisible), so
    ``estimated=True`` is implied by the caller's ``*_estimated`` flag.

    Note: for an actual *candidate* this is True by construction — a
    candidate is precisely an Issue with no open blocking refs (anything
    with open blocking refs is excluded as ``blocked``). The axis is kept
    explicit because it is meaningful output for the human ("yes,
    independent") and because the ``free_panes`` ranking weight (design
    §4.2 「空き pane があるときランクを上げる」) reads it; it simply does not
    discriminate *between* candidates in the common case.
    """
    if open_blocking_refs:
        return False, [
            "has open dependency refs: "
            + ", ".join(f"#{n}" for n in open_blocking_refs)
        ]
    return True, ["leaf in dependency graph (no open dependency refs)"]


def estimate_unblocked_by_recent_merge(
    issue: dict,
    blocking_refs: list[int],
    recent_merge_pr_numbers: set[int],
    recent_merge_closed_issues: set[int],
    recent_merge_referenced_issues: set[int],
) -> tuple[bool, list[str]]:
    """Estimate whether a recent merge unblocked / spawned this Issue.

    Per design §4.2 — two distinct conditions, deliberately separated:
      * **blocking-ref side**: a blocking ref of this Issue is a
        recently-merged PR, or an Issue/PR that a recent merge actually
        *closed* (``recent_merge_closed_issues``, from Closes/Fixes/Resolves
        — NOT a bare ``Refs``). I.e. the thing it depended on just got done.
      * **this-Issue side**: a recently-merged PR *references* this Issue
        (``recent_merge_referenced_issues``, any ref incl. ``Refs``) — a
        natural follow-up.
    Always an estimate (conceptual follow-ups not in any ref are invisible).
    """
    signals: list[str] = []
    hit = False
    for n in blocking_refs:
        if n in recent_merge_pr_numbers:
            signals.append(f"blocking ref #{n} was a recently-merged PR")
            hit = True
        elif n in recent_merge_closed_issues:
            signals.append(
                f"blocking ref #{n} was closed by a recently-merged PR"
            )
            hit = True
    number = issue.get("number")
    if _is_int(number) and number in recent_merge_referenced_issues:
        signals.append("referenced by a recently-merged PR")
        hit = True
    if not hit:
        signals.append("no recent-merge linkage detected")
    return hit, signals


def extract_summary(body: str | None, title: str) -> str:
    """One-line machine summary from the body (design §5.1 `summary`).

    First meaningful line: skips blank lines, markdown headings, block
    quotes, HTML comments, list/table markers and horizontal rules. Falls
    back to the title. Truncated to 120 chars on a word boundary.
    """
    candidate = ""
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("#", ">", "<!--", "---", "***", "|", "```")):
            continue
        # strip a leading list marker / bold wrapper
        line = re.sub(r"^[-*+]\s+", "", line)
        line = line.strip("*_` ")
        if line:
            candidate = line
            break
    if not candidate:
        candidate = title.strip()
    if len(candidate) > 120:
        cut = candidate[:120].rsplit(" ", 1)[0]
        candidate = (cut or candidate[:120]).rstrip() + "…"
    return candidate


def build_candidate(
    issue: dict,
    *,
    open_refs: set[int],
    recent_merge_pr_numbers: set[int],
    recent_merge_closed_issues: set[int],
    recent_merge_referenced_issues: set[int],
    effort_model: dict | None = None,
) -> dict | None:
    """Build one candidate dict, or ``None`` if the Issue is blocked.

    Blocked Issues are not candidates; the caller records them in
    ``excluded_blocked`` instead (design §5.1).
    """
    status, open_blocking = classify_dependency(issue, open_refs)
    if status == "blocked":
        return None

    is_epic = "epic" in _label_names(issue)
    all_blocking = extract_blocking_refs(dependency_text(issue), is_epic=is_epic)

    priority, prio_signals = compute_priority(issue)
    effort, effort_estimated, effort_signals = estimate_effort(issue, effort_model)
    parallelizable, par_signals = estimate_parallelizable(open_blocking)
    unblocked, merge_signals = estimate_unblocked_by_recent_merge(
        issue,
        all_blocking,
        recent_merge_pr_numbers,
        recent_merge_closed_issues,
        recent_merge_referenced_issues,
    )

    signals = prio_signals + effort_signals + par_signals + merge_signals

    # Coerce `title` to a string here so the candidate JSON always satisfies
    # its schema regardless of input shape (a malformed `--from-file` could
    # carry `title: null`/a number). This also keeps extract_summary's
    # title fallback safe (it calls `.strip()` on the title).
    raw_title = issue.get("title")
    title = raw_title if isinstance(raw_title, str) else ""

    return {
        "issue": issue.get("number"),
        "title": title,
        "summary": extract_summary(issue.get("body"), title),
        "dependency": "resolved",
        "blocking_refs": all_blocking,
        "priority": priority,
        "effort": effort,
        "effort_estimated": effort_estimated,
        "parallelizable": parallelizable,
        "parallelizable_estimated": True,
        "unblocked_by_recent_merge": unblocked,
        "unblocked_by_recent_merge_estimated": True,
        # rank filled in by rank_candidates; _updated_at / _has_milestone
        # are internal tiebreak fields stripped before serialization.
        "rank": None,
        "signals": signals,
        "_updated_at": issue.get("updatedAt") or "",
        "_has_milestone": milestone_title(issue) is not None,
    }


def _sort_key(cand: dict, free_panes: int | None) -> tuple:
    """Lexicographic ranking key (design §4.3), higher = better.

    (priority, unblocked_by_recent_merge, parallelizable-when-free-panes,
    effort smallness, milestone-presence, recency). The milestone term sits
    just above recency, realising §4.1's "label > milestone > recency" tier.
    Returned negated where needed so a plain ascending sort puts the best
    candidate first.
    """
    prio = _PRIORITY_RANK.get(cand["priority"], 1)
    unblocked = 1 if cand["unblocked_by_recent_merge"] else 0
    # parallelizable only earns rank weight when there is a *known* free pane
    # to fill (design §4.2 「空き pane があるとき」); unknown (`--free-panes`
    # unspecified → None) and zero are both neutral, matching the documented
    # contract. (In practice every candidate is parallelizable by
    # construction, so this term only discriminates when free_panes > 0.)
    par = 1 if (cand["parallelizable"] and (free_panes or 0) > 0) else 0
    effort_small = -_EFFORT_RANK.get(cand["effort"], 1)  # S best
    has_ms = 1 if cand.get("_has_milestone") else 0
    recency = cand.get("_updated_at") or ""  # ISO8601 sorts lexically
    return (-prio, -unblocked, -par, -effort_small, -has_ms, _neg_str(recency))


def _neg_str(s: str) -> tuple:
    """Sort helper: make a later ISO timestamp sort *earlier* (better).

    Python can't negate a string, so invert each codepoint into a tuple of
    negative ordinals; longer strings (more recent, equal prefix) then sort
    earlier as desired.
    """
    return tuple(-ord(c) for c in s)


def rank_candidates(
    candidates: list[dict], top_n: int, free_panes: int | None
) -> tuple[list[dict], int]:
    """Sort candidates, assign 1-based ``rank``, return ``(top, truncated)``.

    ``truncated`` is the number of resolved candidates dropped past
    ``top_n`` — always reported so truncation is never silent (design §5.1).
    """
    ordered = sorted(candidates, key=lambda c: _sort_key(c, free_panes))
    for i, cand in enumerate(ordered, start=1):
        cand["rank"] = i
    top = ordered[:top_n] if top_n >= 0 else ordered
    truncated = max(0, len(ordered) - len(top))
    return top, truncated


def make_recommendation(top: list[dict]) -> dict | None:
    """Build the single recommendation (rank 1) with a reason (design §4.3)."""
    if not top:
        return None
    best = top[0]
    bits = [f"優先度 {best['priority']}"]
    if best["unblocked_by_recent_merge"]:
        bits.append("直近マージの follow-up")
    if best["parallelizable"]:
        bits.append("並列可（空き pane を埋められる）")
    bits.append(f"工数 {best['effort']}{'(推定)' if best['effort_estimated'] else ''}")
    bits.append("依存解決済み")
    return {
        "issue": best["issue"],
        "reason": "・".join(bits),
    }


def scan(
    issues: list[dict],
    open_pr_numbers: set[int],
    recent_merges: list[dict],
    config: ScanConfig,
    input_truncated: dict | None = None,
    effort_model: dict | None = None,
) -> dict:
    """Pure triage core: produce the candidate JSON dict (design §5.1).

    ``issues`` — open Issues (each: ``number``, ``title``, ``body``,
    ``labels``, ``updatedAt``, ``milestone``, ``comments``).
    ``open_pr_numbers`` — currently-open PR numbers (combined with
    open-issue numbers to resolve blocking refs). ``recent_merges`` —
    recent merged PRs (each: ``number``, ``title``, ``body``) for the
    unblocked-by-recent-merge heuristic. ``input_truncated`` — optional
    ``{"open_issues": bool, "open_prs": bool}`` set by the caller when a
    fetch hit its row cap, surfaced in the output so input-side truncation
    is never silent (design §5.1). ``effort_model`` — optional learned
    effort model (``learn_effort_model``) passed to each candidate's effort
    estimate and echoed in the output for audit (design §10). No I/O here.
    """
    open_issue_numbers = {
        i["number"] for i in issues if _is_int(i.get("number"))
    }
    open_refs = open_issue_numbers | open_pr_numbers

    # §4.2: keep "closed by a recent merge" and "merely referenced" apart.
    recent_merge_pr_numbers: set[int] = set()
    recent_merge_closed_issues: set[int] = set()
    recent_merge_referenced_issues: set[int] = set()
    for pr in recent_merges:
        num = pr.get("number")
        if _is_int(num):
            recent_merge_pr_numbers.add(num)
        text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
        closed = _pr_close_refs(text)
        referenced = closed | _pr_referenced_refs(text)
        recent_merge_closed_issues |= closed
        recent_merge_referenced_issues |= referenced

    candidates: list[dict] = []
    excluded_blocked: list[dict] = []
    for issue in issues:
        status, open_blocking = classify_dependency(issue, open_refs)
        if status == "blocked":
            note = (
                "blocked/on-hold label"
                if has_block_label(issue) and not open_blocking
                else (
                    ", ".join(f"#{n}" for n in open_blocking) + " が open のため除外"
                )
            )
            excluded_blocked.append(
                {
                    "issue": issue.get("number"),
                    "blocking_refs": open_blocking,
                    "note": note,
                }
            )
            continue
        cand = build_candidate(
            issue,
            open_refs=open_refs,
            recent_merge_pr_numbers=recent_merge_pr_numbers,
            recent_merge_closed_issues=recent_merge_closed_issues,
            recent_merge_referenced_issues=recent_merge_referenced_issues,
            effort_model=effort_model,
        )
        if cand is not None:
            candidates.append(cand)

    top, truncated = rank_candidates(candidates, config.top_n, config.free_panes)
    recommendation = make_recommendation(top)

    # strip internal-only fields before serialization
    for cand in top:
        cand.pop("_updated_at", None)
        cand.pop("_has_milestone", None)

    excluded_blocked.sort(key=lambda e: (e["issue"] is None, e["issue"]))

    truncation = {"open_issues": False, "open_prs": False}
    if input_truncated:
        truncation.update(
            {k: bool(v) for k, v in input_truncated.items() if k in truncation}
        )

    return {
        "status": "candidates_found" if top else "no_candidates",
        "generated_for": config.trigger,
        "candidate_count": len(top),
        "truncated_count": truncated,
        # Input-side coverage caveat: True when the open-Issue/open-PR fetch
        # hit its row cap, so some open Issues (candidates) or open blockers
        # may be unseen — a blocker not fetched would be mis-resolved
        # (design §5.1: never truncate silently).
        "input_truncated": truncation,
        # The learned effort model summary (design §10), or ``None`` when
        # learning was disabled / unavailable. Surfaced so the delivery layer
        # and a human can audit what was learned and whether it was applied —
        # never hidden, mirroring truncated_count / excluded_blocked.
        "effort_model": effort_model,
        "candidates": top,
        "recommendation": recommendation,
        "excluded_blocked": excluded_blocked,
    }


# ----------------------------------------------------------------------
# I/O layer — read-only `gh` invocations + CLI. Kept thin and separate
# from the pure core above.
# ----------------------------------------------------------------------


class GhError(RuntimeError):
    """A `gh` read call failed (missing binary, auth, API error, bad JSON)."""


def _decode_gh_stdout(raw: bytes, args: list[str]) -> str:
    """Decode a ``gh`` stdout byte stream as UTF-8 in the **caller's** thread.

    ``gh`` always emits UTF-8 regardless of the OS locale. We deliberately
    capture *bytes* (no ``text=True``) and decode here, rather than letting
    ``subprocess`` decode inside its reader thread: on a non-UTF-8 locale
    (e.g. cp932 on Japanese Windows) ``text=True`` decodes with the *locale*
    codec, and a ``UnicodeDecodeError`` raised in that daemon reader thread is
    **swallowed** — ``proc.stdout`` comes back ``None`` and the failure
    resurfaces downstream as a baffling ``the JSON object must be str, bytes
    or bytearray, not NoneType`` (Issue #537). Decoding in the main thread
    means a genuine decode failure raises *here*, naming the offending byte
    and the command, instead of cascading into a misleading NoneType error.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GhError(
            f"`gh {' '.join(args)}` stdout was not valid UTF-8 "
            f"(byte 0x{exc.object[exc.start]:02x} at position {exc.start}); "
            f"gh emits UTF-8 regardless of locale — {exc}"
        ) from exc


def _run_gh_json(args: list[str]) -> list | dict:
    """Run a read-only ``gh`` command and parse its JSON stdout.

    Only ``gh`` subcommands that *read* are ever passed here (callers pass
    ``repo view`` / ``issue list`` / ``pr list``). Raises ``GhError`` on
    any failure so ``main`` can emit ``status=error`` / exit 2.

    Output is captured as bytes and decoded as UTF-8 in this thread (see
    ``_decode_gh_stdout``) so a cp932-locale decode failure surfaces as a
    clear ``GhError`` rather than being swallowed in subprocess's reader
    thread and cascading into a NoneType error (Issue #537).
    """
    if shutil.which("gh") is None:
        raise GhError("GitHub CLI (gh) not found in PATH")
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, check=True
        )
    except subprocess.CalledProcessError as exc:
        # stderr is diagnostic only → lossy decode is fine (a mangled error
        # message must not mask the real `gh` failure being reported).
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise GhError(
            f"`gh {' '.join(args)}` failed: {stderr or exc}"
        ) from exc
    stdout = _decode_gh_stdout(proc.stdout or b"", args)
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise GhError(
            f"`gh {' '.join(args)}` returned non-JSON output: {exc}"
        ) from exc


def _run_gh_json_list(args: list[str]) -> list:
    """Like ``_run_gh_json`` but require a JSON **array**.

    ``gh ... list --json`` always returns an array; a non-list payload means
    an unexpected/changed response. Treat it as an error (``GhError`` → exit
    2) rather than silently degrading to ``[]`` (which would masquerade as
    ``no_candidates`` / exit 0 — a contract break, design §5.1)."""
    data = _run_gh_json(args)
    if not isinstance(data, list):
        raise GhError(
            f"`gh {' '.join(args)}` returned a non-array JSON payload "
            f"({type(data).__name__}); expected a list"
        )
    return data


def _repo_args(repo: str | None) -> list[str]:
    return ["--repo", repo] if repo else []


def fetch_open_issues(repo: str | None, limit: int = DEFAULT_OPEN_LIMIT) -> list[dict]:
    return _run_gh_json_list(
        [
            "issue",
            "list",
            *_repo_args(repo),
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,labels,updatedAt,createdAt,milestone,comments",
        ]
    )


def fetch_open_pr_numbers(repo: str | None, limit: int = DEFAULT_OPEN_LIMIT) -> set[int]:
    data = _run_gh_json_list(
        [
            "pr",
            "list",
            *_repo_args(repo),
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number",
        ]
    )
    return {p["number"] for p in data if _is_int(p.get("number"))}


# How much larger than the requested K to fetch before picking the mergedAt
# top-K. `gh pr list`'s DEFAULT order is createdAt-desc (NOT merge-time), and
# even `sort:updated-desc` is only an *approximation* of merge recency: an
# old merged PR that later gets a comment has its `updatedAt` bumped and can
# crowd a genuinely-recent merge out of the top-K. Over-fetching a few × K
# and then taking the `mergedAt` top-K makes that false-negative effectively
# impossible (it would take >2K old PRs each freshly touched). Cheap: K is
# small (default 10) and these PRs are metadata-only.
_RECENT_MERGE_OVERFETCH = 3


def fetch_recent_merges(repo: str | None, limit: int) -> list[dict]:
    # Over-fetch in merge-recency-biased order (`sort:updated-desc`), then
    # take the exact `mergedAt` top-K client-side. Two layers because neither
    # alone guarantees the "直近 K 件" (design §4.2): the server sort biases
    # the pool toward recency, the client sort + cap makes the final K exact.
    fetch_limit = max(limit, limit * _RECENT_MERGE_OVERFETCH)
    merges = _run_gh_json_list(
        [
            "pr",
            "list",
            *_repo_args(repo),
            "--state",
            "merged",
            "--search",
            "sort:updated-desc",
            "--limit",
            str(fetch_limit),
            "--json",
            "number,title,body,mergedAt",
        ]
    )
    # Exact newest-first ordering by mergedAt (ISO-8601 sorts
    # lexicographically = chronologically); a missing `mergedAt` sorts last
    # (treated as oldest). The slice takes the genuine 直近 K 件.
    merges.sort(key=lambda p: p.get("mergedAt") or "", reverse=True)
    return merges[:limit]


# ----------------------------------------------------------------------
# Effort learning I/O (design §10). All read-only. These feed the learned
# effort model and are wired NON-FATALLY in main(): a failure here degrades
# to the static heuristic, it never aborts the triage (the model is an
# enhancement, not a core input).
# ----------------------------------------------------------------------


def fetch_effort_history(repo: str | None, limit: int) -> list[dict]:
    """Fetch the ``limit`` most-recently-*merged* PRs with realized-effort
    fields for learning.

    A larger window than ``fetch_recent_merges`` (which wants *recency* for
    the unblocked-by-recent-merge axis); learning wants *volume*. Pulls the
    realized-effort signals (changed lines/files, reviews, timestamps) plus
    ``closingIssuesReferences`` to bridge each PR to the issue it closed.

    Mirrors ``fetch_recent_merges``' two-layer ordering: ``gh pr list``
    defaults to createdAt-desc and even ``sort:updated-desc`` only
    *approximates* merge recency (an old PR with a fresh comment bubbles up),
    so over-fetch in recency-biased order then take the exact ``mergedAt``
    top-K client-side — otherwise a freshly-commented old merge could displace
    a genuinely-recent one from the learning window (Codex Major). Read-only
    (``gh pr list``)."""
    fetch_limit = max(limit, limit * _RECENT_MERGE_OVERFETCH)
    merges = _run_gh_json_list(
        [
            "pr",
            "list",
            *_repo_args(repo),
            "--state",
            "merged",
            "--search",
            "sort:updated-desc",
            "--limit",
            str(fetch_limit),
            "--json",
            "number,additions,deletions,changedFiles,reviews,createdAt,"
            "mergedAt,closingIssuesReferences",
        ]
    )
    merges.sort(key=lambda p: p.get("mergedAt") or "", reverse=True)
    return merges[:limit]


def fetch_closed_issue_bodies(repo: str | None, limit: int) -> dict[int, str]:
    """Map closed-issue number → body, in ONE batched read (not per-issue).

    Used to recover the predictor (issue body) of the issues that recent
    merges closed. Ordered by ``sort:updated-desc`` so recently-closed issues
    (including long-lived ones closed only recently) sit near the top of the
    window and are not silently dropped (Codex Major). Read-only
    (``gh issue list --state closed``).

    Caveat (inherent, documented): this returns each issue's *current* body,
    not its body at merge/triage time. A post-close edit to an issue body
    shifts the learned correlation/cutpoints. Spec issues are rarely edited
    after closing, but this is a known limitation of learning the predictor
    from issue text and is why ``build_effort_model`` also surfaces a
    ``coverage`` summary for audit."""
    data = _run_gh_json_list(
        [
            "issue",
            "list",
            *_repo_args(repo),
            "--state",
            "closed",
            "--search",
            "sort:updated-desc",
            "--limit",
            str(limit),
            "--json",
            "number,body",
        ]
    )
    return {
        i["number"]: (i.get("body") or "")
        for i in data
        if _is_int(i.get("number"))
    }


def _hours_between(created: str | None, merged: str | None) -> float | None:
    """Hours from ``createdAt`` to ``mergedAt`` (ISO-8601), or ``None``.

    Context-only (NOT in the effort composite — see ``EFFORT_FILE_WEIGHT``):
    this org merges in minutes, so the value reflects queueing, not effort."""
    if not created or not merged:
        return None
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
        m = datetime.fromisoformat(merged.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return max(0.0, (m - c).total_seconds() / 3600.0)


def _single_closing_issue(pr: dict) -> int | None:
    """The issue number a PR closes, iff it closes *exactly one* issue.

    Returns ``None`` for unlinked or multi-issue PRs — one PR's effort cannot
    be honestly attributed across several issues, so multi-issue PRs are not
    training pairs."""
    refs = pr.get("closingIssuesReferences") or []
    if not isinstance(refs, list) or len(refs) != 1:
        return None
    ref = refs[0]
    num = ref.get("number") if isinstance(ref, dict) else None
    return num if _is_int(num) else None


def _build_effort_samples(
    merged_prs: list[dict], closed_bodies: dict[int, str]
) -> list[dict]:
    """Join single-issue-linked merged PRs with their closed issue's body to
    form ``learn_effort_model`` training pairs.

    Multi-issue PRs are skipped (see ``_single_closing_issue``). PRs whose
    closed issue was not fetched or has an empty body are also skipped (the
    drop is counted in ``build_effort_model``'s ``coverage``, not silent)."""
    samples: list[dict] = []
    for pr in merged_prs:
        num = _single_closing_issue(pr)
        if num is None:
            continue
        body = closed_bodies.get(num)
        if not body:  # unfetched or empty → unusable predictor
            continue
        adds = pr.get("additions")
        dels = pr.get("deletions")
        cfiles = pr.get("changedFiles")
        reviews = pr.get("reviews")
        samples.append(
            {
                "issue": num,
                "body_len": len(body),
                # Reserved: the static fallback predictor also uses acceptance-
                # criteria count, so it is recorded here to keep a sample a full
                # picture of the predictor. learn_effort_model does not yet fold
                # it into the learned predictor (body_len alone keeps the learned
                # DOF minimal); kept so a future composite predictor needs no
                # re-fetch.
                "criteria": _count_acceptance_criteria(body),
                "changed_lines": (adds if _is_int(adds) else 0)
                + (dels if _is_int(dels) else 0),
                "changed_files": cfiles if _is_int(cfiles) else 0,
                "review_rounds": len(reviews) if isinstance(reviews, list) else 0,
                "hours_to_merge": _hours_between(
                    pr.get("createdAt"), pr.get("mergedAt")
                ),
            }
        )
    return samples


def build_effort_model(repo: str | None, history_limit: int) -> dict:
    """Fetch effort history + closed-issue bodies and learn the effort model.

    Thin I/O wrapper around ``_build_effort_samples`` + ``learn_effort_model``.
    Read-only. Raises ``GhError`` on a gh failure — the caller (``main``) wraps
    this NON-FATALLY so learning degrades to the static heuristic rather than
    aborting the triage."""
    merged = fetch_effort_history(repo, history_limit)
    # Fetch enough closed issues to cover the linked set. Closed issues that
    # recent merges reference are themselves recent, so a window a few × the
    # PR window comfortably covers them; capped so the batch stays cheap.
    closed_limit = min(max(history_limit * 4, 200), DEFAULT_OPEN_LIMIT)
    closed_bodies = fetch_closed_issue_bodies(repo, closed_limit)
    samples = _build_effort_samples(merged, closed_bodies)
    model = learn_effort_model(samples)
    # Surface learning-data coverage so dropped linkages are never silent
    # (Codex Major / design §5.1 ethos): a single-issue-linked PR whose closed
    # issue body was not in the fetch window does not become a sample.
    linked = sum(1 for pr in merged if _single_closing_issue(pr) is not None)
    model["coverage"] = {
        "single_issue_linked_prs": linked,
        "usable_samples": len(samples),
        "dropped_missing_body": linked - len(samples),
    }
    return model


def _load_bundle(
    path: str,
) -> tuple[list[dict], set[int], list[dict], dict | None]:
    """Load a pre-fetched bundle JSON.

    ``{issues, open_pr_numbers, recent_merges}`` plus an optional
    ``effort_samples`` (a list of ``learn_effort_model`` training-pair dicts)
    that, when present, is learned into the returned effort model — letting
    the effort-learning path be exercised fully offline / hermetically.
    Strictly read-only.
    """
    with open(path, encoding="utf-8") as f:
        bundle = json.load(f)
    if not isinstance(bundle, dict):
        raise GhError(
            f"--from-file bundle must be a JSON object, got "
            f"{type(bundle).__name__}"
        )
    # Validate shapes up front so a malformed bundle yields a *pinpointed*
    # error (exit 2) instead of a confusing downstream exception or — worse —
    # a malformed candidate JSON (`"issue": null`). The gh path never hits
    # this (its fetchers always return well-formed arrays); this guards the
    # offline/test `--from-file` affordance against untrusted input.
    #
    # Default ONLY when the key is absent or explicitly null — a *present*
    # non-list (`{}`, `""`, `false`, `0`) is malformed and must error, not be
    # coalesced to `[]` (which would masquerade as no_candidates / exit 0).
    def _list_field(name: str) -> list:
        value = bundle.get(name)
        if value is None:
            return []
        if not isinstance(value, list):
            raise GhError(
                f"--from-file `{name}` must be a list, got "
                f"{type(value).__name__}"
            )
        return value

    issues = _list_field("issues")
    recent_merges = _list_field("recent_merges")
    pr_raw = _list_field("open_pr_numbers")
    for i, item in enumerate(issues):
        if not isinstance(item, dict):
            raise GhError(
                f"--from-file issues[{i}] must be an object, got "
                f"{type(item).__name__}"
            )
        # `bool` is an int subclass — exclude it so True/False can't pose as a
        # number; every candidate's `issue` field must be a real integer.
        if not isinstance(item.get("number"), int) or isinstance(
            item.get("number"), bool
        ):
            raise GhError(
                f"--from-file issues[{i}] must have an integer `number`"
            )
    for i, item in enumerate(recent_merges):
        if not isinstance(item, dict):
            raise GhError(
                f"--from-file recent_merges[{i}] must be an object, got "
                f"{type(item).__name__}"
            )
    open_pr_numbers: set[int] = set()
    for i, n in enumerate(pr_raw):
        if not isinstance(n, int) or isinstance(n, bool):
            raise GhError(
                f"--from-file open_pr_numbers[{i}] must be an integer, got "
                f"{type(n).__name__}"
            )
        open_pr_numbers.add(n)

    # Optional effort-learning training pairs. Absent/null → no model (the
    # offline path then behaves exactly like the pre-learning tool). A present
    # non-list is malformed and must error (same contract as the lists above).
    effort_model: dict | None = None
    raw_samples = bundle.get("effort_samples")
    if raw_samples is not None:
        if not isinstance(raw_samples, list):
            raise GhError(
                f"--from-file `effort_samples` must be a list, got "
                f"{type(raw_samples).__name__}"
            )
        for i, s in enumerate(raw_samples):
            if not isinstance(s, dict):
                raise GhError(
                    f"--from-file effort_samples[{i}] must be an object, got "
                    f"{type(s).__name__}"
                )
            for field in ("body_len", "changed_lines", "changed_files"):
                if not _is_int(s.get(field)):
                    raise GhError(
                        f"--from-file effort_samples[{i}] must have an "
                        f"integer `{field}`"
                    )
        effort_model = learn_effort_model(raw_samples)

    return issues, open_pr_numbers, recent_merges, effort_model


def _error_payload(trigger: str, message: str) -> dict:
    """The fixed-schema error envelope (design §5.1), used by every error
    path so the delivery layer parses one shape regardless of cause."""
    return {
        "status": "error",
        "generated_for": trigger,
        "candidate_count": 0,
        "truncated_count": 0,
        "input_truncated": {"open_issues": False, "open_prs": False},
        "effort_model": None,
        "candidates": [],
        "recommendation": None,
        "excluded_blocked": [],
        "error": message,
    }


class _JsonErrorParser(argparse.ArgumentParser):
    """ArgumentParser that emits the error envelope as a single stdout JSON
    on a usage error (instead of bare usage text), keeping the §5.1
    "stdout is a single JSON object / exit 2 on error" contract even for
    CLI parse errors. ``--help`` still exits 0 via the default path.

    ``trigger`` is the resolved ``--trigger`` (best-effort, see
    ``_probe_trigger``) so the error envelope's ``generated_for`` matches the
    CLI context even for argparse type errors raised mid-parse."""

    def __init__(self, *args, trigger: str = "manual", **kwargs):
        super().__init__(*args, **kwargs)
        self._trigger = trigger

    def error(self, message: str):  # noqa: D102 — argparse override
        print(
            json.dumps(
                _error_payload(self._trigger, f"argument error: {message}"),
                ensure_ascii=False,
                indent=2,
            )
        )
        self.exit(EXIT_ERROR)


def _probe_trigger(argv) -> str:
    """Best-effort `--trigger` recovery *before* the main parse.

    A type error (e.g. ``--top-n nope``) makes argparse call ``error()``
    during ``parse_args``, before ``--trigger`` is bound — so a lightweight
    pre-parse (that tolerates unknown/other args) lets the error envelope
    still carry the real trigger. Falls back to ``manual`` on any hiccup.

    The probe must stay *silent*: a malformed probe parse must NOT print
    usage to stderr (the main parser owns error reporting — JSON to stdout)."""

    class _SilentParser(argparse.ArgumentParser):
        def error(self, message):  # no stderr usage; just abort the probe
            raise SystemExit(2)

    probe = _SilentParser(add_help=False)
    probe.add_argument("--trigger", default="manual")
    try:
        known, _ = probe.parse_known_args(argv)
        return known.trigger
    except SystemExit:
        return "manual"


def main(argv=None) -> int:
    parser = _JsonErrorParser(
        trigger=_probe_trigger(argv),
        description=(
            "Work-discovery triage scan (read-only). Prints a single "
            "candidate JSON to stdout; exit 0=no_candidates, "
            "10=candidates_found, 2=error."
        )
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="OWNER/REPO (default: current repo via gh auto-detection).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Max candidates to return (default {DEFAULT_TOP_N}).",
    )
    parser.add_argument(
        "--free-panes",
        type=int,
        default=None,
        help="Free worker pane count; when > 0, boosts parallelizable "
        "candidates in ranking (does not change --top-n in Phase 1).",
    )
    parser.add_argument(
        "--trigger",
        default="manual",
        help="Context label written to generated_for "
        "(e.g. post_merge / worker_close / startup / manual).",
    )
    parser.add_argument(
        "--recent-merges",
        type=int,
        default=DEFAULT_RECENT_MERGES,
        help=f"How many recent merged PRs feed the unblocked-by-recent-"
        f"merge heuristic (default {DEFAULT_RECENT_MERGES}).",
    )
    parser.add_argument(
        "--effort-history",
        type=int,
        default=DEFAULT_EFFORT_HISTORY,
        help=f"How many recent merged PRs to learn realized effort from "
        f"(design §10); 0 disables effort learning (static heuristic only). "
        f"Default {DEFAULT_EFFORT_HISTORY}.",
    )
    parser.add_argument(
        "--from-file",
        default=None,
        help="Read a pre-fetched {issues, open_pr_numbers, recent_merges, "
        "effort_samples?} JSON bundle instead of calling gh (offline / "
        "validation).",
    )
    args = parser.parse_args(argv)

    config = ScanConfig(
        top_n=args.top_n, free_panes=args.free_panes, trigger=args.trigger
    )

    try:
        # `--top-n 0` (or negative) would silently return an empty `top` even
        # when candidates exist, yielding status no_candidates / exit 0 — a
        # contract break for the exit-code-driven delivery layer. Reject it
        # here (not via parser.error) so the error envelope carries the real
        # `--trigger` context in `generated_for`, not a hardcoded "manual".
        if config.top_n < 1:
            raise ValueError("--top-n must be >= 1")
        # `--recent-merges` feeds `gh pr list --limit` and the mergedAt
        # top-K slice; a non-positive value would request a nonsensical limit
        # and break the "直近 K 件" contract. Require a positive integer.
        if args.recent_merges < 1:
            raise ValueError("--recent-merges must be >= 1")
        # `--free-panes` is a count of free worker panes; 0 is valid (none
        # free → no parallelizable boost), but a negative count is nonsense.
        if args.free_panes is not None and args.free_panes < 0:
            raise ValueError("--free-panes must be >= 0")
        # `--effort-history` is a count of merged PRs to learn from; 0 disables
        # learning, negative is nonsense.
        if args.effort_history < 0:
            raise ValueError("--effort-history must be >= 0")
        input_truncated = {"open_issues": False, "open_prs": False}
        effort_model: dict | None = None
        if args.from_file:
            issues, open_pr_numbers, recent_merges, effort_model = _load_bundle(
                args.from_file
            )
        else:
            issues = fetch_open_issues(args.repo)
            open_pr_numbers = fetch_open_pr_numbers(args.repo)
            recent_merges = fetch_recent_merges(args.repo, args.recent_merges)
            # A full page (== cap) means the fetch may have dropped rows; the
            # flags surface that input-side truncation in the output.
            input_truncated = {
                "open_issues": len(issues) >= DEFAULT_OPEN_LIMIT,
                "open_prs": len(open_pr_numbers) >= DEFAULT_OPEN_LIMIT,
            }
            # Effort learning is an ENHANCEMENT, not a core input: wire it
            # NON-FATALLY. A *fetch* failure here (gh hiccup on the
            # history/closed-issue read) must NOT abort the triage — degrade to
            # the static heuristic with the reason disclosed in each candidate's
            # effort signals. Crucially this catches ONLY GhError (the fetch
            # failure mode): a genuine bug or unexpected schema in the pure
            # learning code must still propagate to the outer handler → exit 2,
            # never be masked as "fetch failed" / exit 0|10 (that would break
            # the §5.1 `exit 2 = error` contract — Codex Blocker).
            if args.effort_history > 0:
                try:
                    effort_model = build_effort_model(args.repo, args.effort_history)
                except GhError as exc:
                    effort_model = empty_effort_model(
                        f"effort-history fetch failed "
                        f"({type(exc).__name__}) → static heuristic retained"
                    )
        result = scan(
            issues,
            open_pr_numbers,
            recent_merges,
            config,
            input_truncated,
            effort_model,
        )
    except Exception as exc:  # noqa: BLE001 — report any failure as error/exit 2
        # Keep the fixed schema (design §5.1) so the delivery layer parses
        # the error branch the same way; the audit fields are present (empty)
        # rather than absent, and `error` carries the cause.
        print(
            json.dumps(
                _error_payload(config.trigger, str(exc)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_ERROR

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        EXIT_CANDIDATES_FOUND
        if result["status"] == "candidates_found"
        else EXIT_NO_CANDIDATES
    )


if __name__ == "__main__":
    sys.exit(main())
