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
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

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

# --- dependency notation (design §4.1, calibrated §11-3) ---------------
# Match a blocking *keyword* and capture the rest of its line.
_BLOCK_KEYWORD_RE = re.compile(
    r"(?im)^[ \t>*\-]*\**\s*(?:blocked\s+by|depends\s+on|requires)\b(.*)$"
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
# Unchecked task-list item that references an Issue: `- [ ] #N`. Counted
# as a pending dependency for NON-epic issues only — an epic's child
# checklist is tracking, not a blocker on the epic itself (calibrated
# against epic #376; see classify_dependency).
_OPEN_TASK_REF_RE = re.compile(r"(?im)^[ \t]*[-*]\s*\[ \]\s*.*?#(\d+)\b")

# PR → linked-Issue notation for the recent-merge heuristic (design §4.2).
_PR_LINK_RE = re.compile(
    r"(?i)(?:closes|close|closed|fixes|fix|fixed|resolves|resolve|resolved|refs|ref)\s+#(\d+)\b"
)

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


def _label_names(issue: dict) -> list[str]:
    """Normalize an issue's labels to a list of lowercase name strings.

    Accepts both ``gh``'s ``[{"name": ...}]`` shape and a plain list of
    strings (the latter is convenient for tests / `--from-file`)."""
    out: list[str] = []
    for label in issue.get("labels") or []:
        if isinstance(label, dict):
            name = label.get("name")
        else:
            name = label
        if isinstance(name, str):
            out.append(name.strip().lower())
    return out


def extract_blocking_refs(body: str | None, *, is_epic: bool) -> list[int]:
    """Return Issue/PR numbers this Issue is blocked by / depends on.

    Only the three blocking keywords (`Blocked by` / `Depends on` /
    `Requires`) contribute, and only via the `#N` refs in their trailing
    clause (design §4.1, §11-3 calibration). For non-epic issues an
    unchecked task-list item `- [ ] #N` also counts as a pending
    dependency; for epics it does not (child checklists are tracking).

    Deduplicated, sorted ascending for a stable, reproducible output.
    """
    if not body:
        return []
    refs: set[int] = set()
    for clause in _BLOCK_KEYWORD_RE.findall(body):
        lead = _LEADING_REFS_RE.match(clause)
        if not lead:
            continue
        for num in _ISSUE_REF_RE.findall(lead.group(1)):
            refs.add(int(num))
    if not is_epic:
        for num in _OPEN_TASK_REF_RE.findall(body):
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
    blocking = extract_blocking_refs(issue.get("body"), is_epic=is_epic)
    open_blocking = sorted(n for n in blocking if n in open_refs)
    if has_block_label(issue) or open_blocking:
        return "blocked", open_blocking
    return "resolved", []


def compute_priority(issue: dict) -> tuple[str, list[str]]:
    """Compute `high`/`medium`/`low` priority + signals (design §4.1).

    Order: explicit priority label > low-priority label > default medium.
    This repo has neither priority labels nor milestones (§11-2), so in
    practice the result is `low` for `backlog`/`wontfix` and `medium`
    otherwise; the label matchers remain for repos that do carry them.
    Recency does NOT change the level here (see rank_candidates — it is a
    tiebreaker only), keeping the level deterministic from metadata.
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
    signals.append("no priority label/milestone → default medium")
    return "medium", signals


def _count_acceptance_criteria(body: str) -> int:
    """Number of checklist items `- [ ]` / `- [x]` (acceptance criteria)."""
    return len(re.findall(r"(?im)^[ \t]*[-*]\s*\[[ xX]\]", body))


def estimate_effort(issue: dict) -> tuple[str, bool, list[str]]:
    """Return ``(size, estimated, signals)`` — S/M/L (design §4.1).

    A ``size:S/M/L`` / ``effort:*`` label (or a bare ``S``/``M``/``L``
    label) is authoritative → ``estimated=False``. Otherwise a heuristic
    over body length + acceptance-criteria count estimates the size and
    ``estimated=True`` (design §4.4: estimated values must say so).
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
    # Heuristic buckets (documented so the estimate is auditable):
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
    return size, True, signals


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
    recent_merge_linked_issues: set[int],
) -> tuple[bool, list[str]]:
    """Estimate whether a recent merge unblocked / spawned this Issue.

    True (design §4.2) when either:
      * a blocking ref of this Issue is a recently-merged PR, or
      * a recently-merged PR references this Issue (Refs/Closes #thisN).
    Always an estimate (conceptual follow-ups not in any ref are invisible).
    """
    signals: list[str] = []
    hit = False
    for n in blocking_refs:
        if n in recent_merge_pr_numbers:
            signals.append(f"blocking ref #{n} was a recently-merged PR")
            hit = True
    number = issue.get("number")
    if isinstance(number, int) and number in recent_merge_linked_issues:
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
    recent_merge_linked_issues: set[int],
) -> dict | None:
    """Build one candidate dict, or ``None`` if the Issue is blocked.

    Blocked Issues are not candidates; the caller records them in
    ``excluded_blocked`` instead (design §5.1).
    """
    status, open_blocking = classify_dependency(issue, open_refs)
    if status == "blocked":
        return None

    is_epic = "epic" in _label_names(issue)
    all_blocking = extract_blocking_refs(issue.get("body"), is_epic=is_epic)

    priority, prio_signals = compute_priority(issue)
    effort, effort_estimated, effort_signals = estimate_effort(issue)
    parallelizable, par_signals = estimate_parallelizable(open_blocking)
    unblocked, merge_signals = estimate_unblocked_by_recent_merge(
        issue, all_blocking, recent_merge_pr_numbers, recent_merge_linked_issues
    )

    signals = prio_signals + effort_signals + par_signals + merge_signals

    return {
        "issue": issue.get("number"),
        "title": issue.get("title", ""),
        "summary": extract_summary(issue.get("body"), issue.get("title", "")),
        "dependency": "resolved",
        "blocking_refs": all_blocking,
        "priority": priority,
        "effort": effort,
        "effort_estimated": effort_estimated,
        "parallelizable": parallelizable,
        "parallelizable_estimated": True,
        "unblocked_by_recent_merge": unblocked,
        "unblocked_by_recent_merge_estimated": True,
        # rank filled in by rank_candidates; updatedAt kept for tiebreak.
        "rank": None,
        "signals": signals,
        "_updated_at": issue.get("updatedAt") or "",
    }


def _sort_key(cand: dict, free_panes: int | None) -> tuple:
    """Lexicographic ranking key (design §4.3), higher = better.

    (priority, unblocked_by_recent_merge, parallelizable-when-free-panes,
    effort smallness, recency). Returned negated where needed so a plain
    ascending sort puts the best candidate first.
    """
    prio = _PRIORITY_RANK.get(cand["priority"], 1)
    unblocked = 1 if cand["unblocked_by_recent_merge"] else 0
    # parallelizable only earns rank weight when there is a free pane to
    # fill (design §4.2); otherwise it is neutral.
    par = (
        1
        if (cand["parallelizable"] and (free_panes is None or free_panes > 0))
        else 0
    )
    effort_small = -_EFFORT_RANK.get(cand["effort"], 1)  # S best
    recency = cand.get("_updated_at") or ""  # ISO8601 sorts lexically
    return (-prio, -unblocked, -par, -effort_small, _neg_str(recency))


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
) -> dict:
    """Pure triage core: produce the candidate JSON dict (design §5.1).

    ``issues`` — open Issues (each: ``number``, ``title``, ``body``,
    ``labels``, ``updatedAt``). ``open_pr_numbers`` — currently-open PR
    numbers (combined with open-issue numbers to resolve blocking refs).
    ``recent_merges`` — recent merged PRs (each: ``number``, ``title``,
    ``body``) for the unblocked-by-recent-merge heuristic. No I/O here.
    """
    open_issue_numbers = {
        i["number"] for i in issues if isinstance(i.get("number"), int)
    }
    open_refs = open_issue_numbers | open_pr_numbers

    recent_merge_pr_numbers: set[int] = set()
    recent_merge_linked_issues: set[int] = set()
    for pr in recent_merges:
        num = pr.get("number")
        if isinstance(num, int):
            recent_merge_pr_numbers.add(num)
        text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
        for n in _PR_LINK_RE.findall(text):
            recent_merge_linked_issues.add(int(n))

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
            recent_merge_linked_issues=recent_merge_linked_issues,
        )
        if cand is not None:
            candidates.append(cand)

    top, truncated = rank_candidates(candidates, config.top_n, config.free_panes)
    recommendation = make_recommendation(top)

    # strip internal-only fields before serialization
    for cand in top:
        cand.pop("_updated_at", None)

    excluded_blocked.sort(key=lambda e: (e["issue"] is None, e["issue"]))

    return {
        "status": "candidates_found" if top else "no_candidates",
        "generated_for": config.trigger,
        "candidate_count": len(top),
        "truncated_count": truncated,
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


def _run_gh_json(args: list[str]) -> list | dict:
    """Run a read-only ``gh`` command and parse its JSON stdout.

    Only ``gh`` subcommands that *read* are ever passed here (callers pass
    ``repo view`` / ``issue list`` / ``pr list``). Raises ``GhError`` on
    any failure so ``main`` can emit ``status=error`` / exit 2.
    """
    if shutil.which("gh") is None:
        raise GhError("GitHub CLI (gh) not found in PATH")
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as exc:
        raise GhError(
            f"`gh {' '.join(args)}` failed: {exc.stderr.strip() or exc}"
        ) from exc
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GhError(
            f"`gh {' '.join(args)}` returned non-JSON output: {exc}"
        ) from exc


def _repo_args(repo: str | None) -> list[str]:
    return ["--repo", repo] if repo else []


def fetch_open_issues(repo: str | None, limit: int = 300) -> list[dict]:
    data = _run_gh_json(
        [
            "issue",
            "list",
            *_repo_args(repo),
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,labels,updatedAt,createdAt",
        ]
    )
    return data if isinstance(data, list) else []


def fetch_open_pr_numbers(repo: str | None, limit: int = 300) -> set[int]:
    data = _run_gh_json(
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
    if not isinstance(data, list):
        return set()
    return {p["number"] for p in data if isinstance(p.get("number"), int)}


def fetch_recent_merges(repo: str | None, limit: int) -> list[dict]:
    data = _run_gh_json(
        [
            "pr",
            "list",
            *_repo_args(repo),
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,mergedAt",
        ]
    )
    return data if isinstance(data, list) else []


def _load_bundle(path: str) -> tuple[list[dict], set[int], list[dict]]:
    """Load a pre-fetched ``{issues, open_pr_numbers, recent_merges}`` JSON.

    Lets the tool run fully offline (manual validation / determinism
    checks) without touching ``gh``. Strictly read-only.
    """
    with open(path, encoding="utf-8") as f:
        bundle = json.load(f)
    issues = bundle.get("issues") or []
    open_pr_numbers = {
        int(n) for n in (bundle.get("open_pr_numbers") or [])
    }
    recent_merges = bundle.get("recent_merges") or []
    return issues, open_pr_numbers, recent_merges


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
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
        "--from-file",
        default=None,
        help="Read a pre-fetched {issues, open_pr_numbers, recent_merges} "
        "JSON bundle instead of calling gh (offline / validation).",
    )
    args = parser.parse_args(argv)

    config = ScanConfig(
        top_n=args.top_n, free_panes=args.free_panes, trigger=args.trigger
    )

    try:
        if args.from_file:
            issues, open_pr_numbers, recent_merges = _load_bundle(args.from_file)
        else:
            issues = fetch_open_issues(args.repo)
            open_pr_numbers = fetch_open_pr_numbers(args.repo)
            recent_merges = fetch_recent_merges(args.repo, args.recent_merges)
        result = scan(issues, open_pr_numbers, recent_merges, config)
    except Exception as exc:  # noqa: BLE001 — report any failure as error/exit 2
        print(
            json.dumps(
                {"status": "error", "error": str(exc)}, ensure_ascii=False
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
