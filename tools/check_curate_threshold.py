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
  ``- **status**: pending`` in ``knowledge/skill-candidates.md``
  (the same ``grep -c '^- \\*\\*status\\*\\*: pending'`` the
  skill-audit Step 1 uses).
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

LEGACY_MARKER = "<!-- curated -->"
# How much of a file head we scan for the legacy marker. The historical
# in-place marking wrote the marker as the very first line; 256 bytes
# tolerates a BOM / leading blank line without reading whole files.
_HEAD_BYTES = 256

_PENDING_RE = re.compile(r"^- \*\*status\*\*: pending\s*$", re.MULTILINE)


def _has_legacy_marker(path: Path) -> bool:
    """Read errors propagate (except a vanished file, handled by the
    caller): silently treating an unreadable head as "no marker" would
    be a false negative that suppresses a legitimate curator launch,
    breaking the error contract in the module docstring."""
    head = path.read_bytes()[:_HEAD_BYTES]
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


def count_pending(root: Path) -> int:
    """Count ``- **status**: pending`` lines in skill-candidates.md.

    A missing file counts as 0 (normal in fresh checkouts); any other
    read error propagates so ``main`` reports ``status=error`` / exit 2
    rather than masking a real queue behind a false 0.
    """
    candidates = root / "knowledge" / "skill-candidates.md"
    try:
        text = candidates.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    return len(_PENDING_RE.findall(text))


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
