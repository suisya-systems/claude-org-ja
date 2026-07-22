#!/usr/bin/env python3
"""Curate-threshold check for the on-demand curator.

The resident curator (``/org-start`` spawn + ``/loop 30m /org-curate``)
is retired. Instead, the dispatcher runs this script when a worker
pane closes (``.dispatcher/references/pane-close.md`` Step 5) and
spawns a one-shot curator pane only when a threshold is exceeded.
Threshold judgment lives **here, and only here** — ``org-curate``
no longer has an internal "raw < 5 → return" gate; it receives the
``reasons[]`` computed by this script and executes the matching steps
(Codex design review B1).

Machine-readable contract (Codex review m8):

* stdout — single JSON object::

      {
        "status": "curate_needed" | "below_threshold" | "error",
        "reasons": ["raw_threshold", "skill_candidates_pending",
                     "work_skill_count", "legacy_marker_sweep"],
        "counts": {
          "raw_active": <int>,
          "legacy_marker": <int>,
          "skill_candidates_pending": <int>,
          "work_skill": <int>
        },
        "thresholds": {"raw_active": 5, "skill_candidates_pending": 5,
                        "work_skill": 20, "legacy_marker": 1}
      }

  (``status == "error"`` adds an ``"error"`` string field instead of
  ``counts`` being trustworthy.)

* exit code — the dispatcher branches on this, not on JSON parsing:

  - ``0``  — below_threshold: no reason fired, do not spawn a curator
  - ``10`` — curate_needed: at least one reason fired, spawn the
    on-demand curator (after the single-flight ``list_panes`` check)
  - ``2``  — error: unexpected failure (unreadable root etc.); the
    dispatcher reports it to the secretary and skips the curate step

  ``10`` (not ``1``) so an unexpected Python traceback — which exits
  ``1`` — can never be mistaken for a fire decision.

Counting rules (Codex review M4 / m9):

* ``raw_active`` — regular files directly under ``knowledge/raw/``
  (``knowledge/raw/archive/`` excluded), excluding sentinel / hidden
  entries (any name starting with ``.``, e.g. ``.gitkeep``), and
  excluding files whose head carries the legacy ``<!-- curated -->``
  marker (those are already-curated remnants counted separately).
* ``legacy_marker`` — files directly under ``knowledge/raw/`` whose
  head carries ``<!-- curated -->`` (pre-archive-migration remnants;
  Codex review B3: their mere existence fires the
  ``legacy_marker_sweep`` reason so the sweep can't starve).
* ``skill_candidates_pending`` — lines matching exactly
  ``- **status**: pending`` **excluding lines inside code fences**
  (blocks opened/closed by a line starting with three backticks or
  ``~~~``) so the entry-format template example at the head of the
  file can never inflate the count. The count is summed over **both**
  candidate-entry files (Issue #755): the tracked, public
  ``knowledge/skill-candidates.md`` — which now carries the entry
  FORMAT definition only, its entry list always empty — and the
  machine-local, gitignored ``knowledge/skill-candidates.local.md``
  that holds the real (operator-private) entries so they never reach
  the OSS repo. ``CANDIDATE_ENTRY_PATHS`` is the single source of
  truth for that file list; each file is scanned with independent
  code-fence state and a missing file counts as 0. This
  fence-excluding, two-file semantics is kept in three-way sync with
  the skill-audit Step 1 count command (which must read the SAME two
  files in the SAME order) and the operational note at the head of
  ``knowledge/skill-candidates.md`` — change one, change all three
  (``tools/test_check_curate_threshold.py`` asserts parity, including
  that the awk command references exactly ``CANDIDATE_ENTRY_PATHS``).

  **Only ``pending`` counts — every other status is excluded by the
  exact-match design (invariant relied on by Issue #753).** Because
  the regex requires the literal ``pending`` token, entries carrying
  ``deferred`` / ``approved`` / ``rejected`` / ``merged-into-*`` never
  match and so never fire this reason. ``deferred`` in particular is
  the "presented to the human, human chose to hold" state: marking a
  candidate ``deferred`` is exactly how a shelved candidate stops
  re-firing the threshold on every worker close (previously such
  candidates, left ``pending``, spawned the on-demand curator
  repeatedly). Do **not** relax the match to a prefix / substring on
  ``status`` — that would recount ``deferred`` and reopen the bug.
* ``work_skill`` — ``SKILL.md`` files at ``.claude/skills/*/SKILL.md``
  whose skill directory does **not** start with ``org-``. This matches
  skill-audit Step 1's
  ``find .claude/skills -maxdepth 2 -name SKILL.md | grep -v '/org-' | wc -l``
  exactly (Codex review M4); ``tools/test_check_curate_threshold.py``
  asserts parity against that very pipeline.

Used by ``.dispatcher/references/pane-close.md`` Step 5 and (for
manual invocations without dispatcher-provided reasons) by
``.claude/skills/org-curate/SKILL.md`` Step 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

EXIT_BELOW_THRESHOLD = 0
EXIT_CURATE_NEEDED = 10
EXIT_ERROR = 2

RAW_THRESHOLD = 5
PENDING_THRESHOLD = 5
WORK_SKILL_THRESHOLD = 20
LEGACY_MARKER_THRESHOLD = 1

# Single source of truth (Issue #755) for the files that hold
# skill-candidate entries, in read order. The tracked public file
# carries the entry-FORMAT definition only (its entry list is always
# empty); real, operator-private entries live in the machine-local,
# gitignored ``.local.md`` sibling so they never reach the OSS repo.
# count_pending sums over both (each with independent fence state,
# missing == 0). The skill-audit Step 1 awk MUST read these same two
# files in this same order — three-way sync, asserted by
# tools/test_check_curate_threshold.py.
CANDIDATE_ENTRY_PATHS = (
    Path("knowledge") / "skill-candidates.md",
    Path("knowledge") / "skill-candidates.local.md",
)

LEGACY_MARKER = "<!-- curated -->"
# How much of a file head we scan for the legacy marker. The historical
# in-place marking wrote the marker as the very first line; 256 bytes
# tolerates a BOM / leading blank line without reading whole files.
_HEAD_BYTES = 256

# Matched per line (not MULTILINE over the whole text) because fence
# state is tracked line by line in count_pending. The literal
# ``pending`` token is load-bearing: it is what excludes ``deferred``
# (and approved / rejected / merged-into-*) from the count so shelved
# candidates stop re-firing the threshold (Issue #753). Keep it exact
# — do not widen to a prefix/substring match on ``status``.
_PENDING_RE = re.compile(r"^- \*\*status\*\*: pending\s*$")
# A code fence opens/closes on a line *starting* with ``` or ~~~
# (three-way sync: skill-audit Step 1 awk command and the operational
# note in knowledge/skill-candidates.md use the same rule).
_FENCE_RE = re.compile(r"^(```|~~~)")


def _has_legacy_marker(path: Path) -> bool:
    """Read errors propagate (except a vanished file, handled by the
    caller): silently treating an unreadable head as "no marker" would
    be a false negative that suppresses a legitimate curator launch,
    breaking the error contract in the module docstring."""
    with path.open("rb") as f:
        head = f.read(_HEAD_BYTES)
    text = head.decode("utf-8", errors="replace").lstrip("﻿").lstrip()
    return text.startswith(LEGACY_MARKER)


def count_raw(root: Path) -> tuple[int, int]:
    """Return ``(raw_active, legacy_marker)`` for ``knowledge/raw/``.

    Only regular files directly under the directory count;
    ``archive/`` (and any other subdirectory) is excluded, as are
    sentinel / hidden entries whose name starts with ``.``. A file that
    vanishes between listing and reading is skipped (race with a
    concurrent archive move); any other read error propagates so
    ``main`` reports ``status=error`` / exit 2 instead of silently
    under-counting.
    """
    raw_dir = root / "knowledge" / "raw"
    if not raw_dir.is_dir():
        return 0, 0
    active = 0
    legacy = 0
    for entry in sorted(raw_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.startswith("."):
            continue
        try:
            marked = _has_legacy_marker(entry)
        except FileNotFoundError:
            continue
        if marked:
            legacy += 1
        else:
            active += 1
    return active, legacy


def _count_pending_in(path: Path) -> int:
    """Count ``- **status**: pending`` lines in a single candidates file.

    Lines inside code fences (blocks delimited by lines starting with
    ``` or ``~~~``) are excluded: the entry-format template example at
    the head of the public file must never count as a real pending
    entry (it once did, spuriously spawning the on-demand curator on
    every worker close). Fence state is per-file, so this function is
    called once per ``CANDIDATE_ENTRY_PATHS`` entry — a fence left open
    at the end of one file can never bleed into the next.

    Only the literal ``pending`` status counts. ``deferred`` (a
    presented-then-shelved candidate), ``approved``, ``rejected`` and
    ``merged-into-*`` all fail the exact match and are excluded — that
    is precisely how a shelved candidate stops re-firing the threshold
    (Issue #753).

    A missing file counts as 0 (normal in fresh checkouts, and normal
    for the machine-local ``.local.md`` sibling on a clean tree); any
    other read error propagates so ``main`` reports ``status=error`` /
    exit 2 rather than masking a real queue behind a false 0.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    count = 0
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _PENDING_RE.match(line):
            count += 1
    return count


def count_pending(root: Path) -> int:
    """Sum pending entries across ``CANDIDATE_ENTRY_PATHS`` (Issue #755).

    The tracked public ``knowledge/skill-candidates.md`` holds the entry
    FORMAT definition only (its entry list is always empty); the
    machine-local, gitignored ``knowledge/skill-candidates.local.md``
    holds the real operator-private entries. Both are counted with the
    same fence-excluding, exact-``pending``-match rule so the threshold
    fires on the true queue depth without operator-private candidates
    ever being committed. Missing files count as 0; non-missing read
    errors propagate (via ``_count_pending_in``) to ``main``.
    """
    return sum(_count_pending_in(root / rel) for rel in CANDIDATE_ENTRY_PATHS)


def count_work_skills(root: Path) -> int:
    """Count work-skills exactly as skill-audit Step 1 does.

    Shell equivalent::

        find .claude/skills -maxdepth 2 -name SKILL.md \\
          | grep -v '/org-' | wc -l

    ``-maxdepth 2`` relative to ``.claude/skills`` admits only
    ``.claude/skills/<dir>/SKILL.md`` (plus a hypothetical top-level
    ``SKILL.md``, which we also admit for parity), and ``grep -v
    '/org-'`` drops every path with a ``/org-`` segment. Paths are
    normalized to ``/`` before the filter so Windows separators can't
    skew the count.
    """
    skills_dir = root / ".claude" / "skills"
    if not skills_dir.is_dir():
        return 0
    matches: list[Path] = []
    top = skills_dir / "SKILL.md"
    if top.is_file():
        matches.append(top)
    matches.extend(p for p in skills_dir.glob("*/SKILL.md") if p.is_file())
    count = 0
    for p in matches:
        rel = p.relative_to(skills_dir).as_posix()
        if f"/{rel}".find("/org-") != -1:
            continue
        count += 1
    return count


def evaluate(root: Path) -> dict:
    """Compute counts and fire reasons. Returns the stdout JSON dict."""
    raw_active, legacy_marker = count_raw(root)
    pending = count_pending(root)
    work_skill = count_work_skills(root)

    reasons = []
    if raw_active >= RAW_THRESHOLD:
        reasons.append("raw_threshold")
    if pending >= PENDING_THRESHOLD:
        reasons.append("skill_candidates_pending")
    if work_skill >= WORK_SKILL_THRESHOLD:
        reasons.append("work_skill_count")
    if legacy_marker >= LEGACY_MARKER_THRESHOLD:
        reasons.append("legacy_marker_sweep")

    return {
        "status": "curate_needed" if reasons else "below_threshold",
        "reasons": reasons,
        "counts": {
            "raw_active": raw_active,
            "legacy_marker": legacy_marker,
            "skill_candidates_pending": pending,
            "work_skill": work_skill,
        },
        "thresholds": {
            "raw_active": RAW_THRESHOLD,
            "skill_candidates_pending": PENDING_THRESHOLD,
            "work_skill": WORK_SKILL_THRESHOLD,
            "legacy_marker": LEGACY_MARKER_THRESHOLD,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Decide whether the on-demand curator should run "
        "(JSON on stdout; exit 0=below_threshold, 10=curate_needed, "
        "2=error)."
    )
    parser.add_argument(
        "--root",
        default=None,
        help="claude-org repo root (default: parent of this script's "
        "directory, so callers may run it from any cwd)",
    )
    args = parser.parse_args(argv)

    root = (
        Path(args.root)
        if args.root
        else Path(__file__).resolve().parent.parent
    )

    try:
        result = evaluate(root)
    except Exception as exc:
        # Real path, not just defensive: count_raw / count_pending
        # propagate non-FileNotFoundError read errors here so the
        # dispatcher sees status=error / exit 2 instead of a silent
        # under-count (false negative would suppress curator launches).
        print(
            json.dumps(
                {"status": "error", "reasons": [], "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return EXIT_ERROR

    print(json.dumps(result, ensure_ascii=False))
    return (
        EXIT_CURATE_NEEDED
        if result["status"] == "curate_needed"
        else EXIT_BELOW_THRESHOLD
    )


if __name__ == "__main__":
    sys.exit(main())
