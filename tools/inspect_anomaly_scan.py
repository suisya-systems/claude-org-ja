#!/usr/bin/env python3
"""Deterministic ERROR / spinner-age anomaly detector for dispatcher §4(d).

This is the codified core of ``.dispatcher/references/worker-monitoring.md``
Step 4 (d) "ERROR 検出". The detection used to live as prose that the
dispatcher Claude applied by eyeballing the ``inspect_pane`` grid, which
let three classes of failure slip through (Issue #492):

* **Gap (1) — bottom-N window**: §4(d) scanned only the bottom 10 rows, so
  an error banner that scrolled up (e.g. row 15 of a 43-row pane, with a
  blank middle band below it) was invisible. This module scans **every
  visible row** returned by ``inspect_pane``.
* **Gap (2) — missing 529**: Anthropic's overload code ``529`` (and the
  transient ``502`` / ``503`` / ``504``) were not in the substring list.
* **Gap (3) — stuck spinner**: a ``{glyph} {verb} for {Xm Ys}`` spinner
  that grows past a threshold (default 5 minutes) signals an effectively
  hung API retry loop, independent of any substring match. This module
  treats an aged spinner as an ERROR-equivalent detection.

Keeping the logic as a pure function (:func:`scan_lines`) gives the
contract a regression test (``tests/test_inspect_anomaly_scan.py``
reproduces the 2026-05-28 case: 529 banner at row 15 + 9m spinner +
empty bottom 10) and lets the dispatcher pipe ``inspect_pane`` output
through a single deterministic detector via the CLI below.

Scope: this module covers the ERROR-class detections (substring + anchored
regex + spinner age). APPROVAL_BLOCKED detection stays in the prose §4(b)
because it is target-line + cursor-position sensitive and not part of
Issue #492.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Sequence, Union

# Default spinner-age threshold in minutes. A Claude Code spinner that has
# been counting for this many minutes or more is treated as a stuck run.
# Issue #492: observed 6m23s / 9m spins that never recovered on their own.
SPINNER_AGE_THRESHOLD_MIN = 5

# Case-insensitive *strong* substrings — these fire unconditionally because
# they are unambiguous error wording. ``API Error`` covers the lowercase
# ``api error`` variant via the case-insensitive compare, but both are kept
# to mirror the documented §4(d) list verbatim.
ERROR_SUBSTRINGS: tuple[str, ...] = (
    "API Error",
    "api error",
    "rate limit",
)

# HTTP / Anthropic status codes. Matched only as standalone tokens (word
# boundaries) AND only when the same line carries an error-ish keyword.
# Broadening the scan from bottom-10 to *all* visible rows (Issue #492 gap 1)
# amplifies the false-positive risk of bare-digit substrings, so a benign
# line like ``localhost:5000``, ``500 passed``, or an issue ref ``#529`` must
# not trip ``ERROR_DETECTED``. The codes remain the deliberate, futureproof
# supplement for when Claude Code renames its error wording (Issue #492 gap 2):
# ``529`` = Anthropic overload, ``502``/``503``/``504`` = transient gateway.
ERROR_STATUS_CODES: tuple[str, ...] = ("429", "500", "502", "503", "504", "529")
# ``(?<!#)`` drops GitHub-style issue refs (``#529``) which ``\b`` alone keeps
# (``#`` is a non-word char, so a word boundary sits before the digit). A
# longer-digit token like ``5000`` is already excluded by the trailing ``\b``.
_STATUS_CODE_RE = re.compile(r"(?<!#)\b(429|500|502|503|504|529)\b")
_ERROR_CONTEXT_RE = re.compile(
    r"error|overload|unavailable|rate limit|too many requests|"
    r"retry|retrying|gateway|server error|throttl",
    re.IGNORECASE,
)

# Line-prefix anchored regexes (case-sensitive, matching the §4(d) prose).
ERROR_ANCHORED_PATTERNS: tuple[str, ...] = (
    r"^Error: ",
    r"^ERROR: ",
)
_ERROR_ANCHORED_RES = tuple(re.compile(p) for p in ERROR_ANCHORED_PATTERNS)

# Stuck-spinner regex. Claude Code renders ``{spinner_glyph} {verb} for
# {Xm Ys}`` while a tool call or API retry runs; under healthy operation the
# counter clears within seconds. The glyph class covers the rotating braille
# / star glyphs Claude Code cycles through (kept generous on purpose — a new
# glyph should not silently disable the detector). ``\w+`` matches the verb
# including non-ASCII letters (e.g. "Sautéed") since Python's ``\w`` is
# Unicode-aware for ``str`` patterns.
SPINNER_AGE_PATTERN = (
    r"^\s*[✻✺✶✷✸✹✦✧✱✲✳·∙▪●○◍◌◐◑◒◓*]+\s+\w+\s+for\s+(\d+)m\s+(\d+)s"
)
_SPINNER_AGE_RE = re.compile(SPINNER_AGE_PATTERN)


@dataclass(frozen=True)
class Detection:
    """One ERROR-class anomaly found on a pane line.

    ``kind`` is always ``"error"`` so callers map every detection onto the
    existing ``ERROR_DETECTED`` notification path (spinner age is an
    ERROR-equivalent per Issue #492 gap 3). ``reason`` distinguishes the
    trigger for journal / debugging (``substring:API Error`` /
    ``status_code:529`` / ``regex:^Error: `` / ``spinner_age:9m``).
    """

    kind: str
    reason: str
    row: Optional[int]
    matched: str


def _normalize(
    lines: Iterable[Union[str, dict]],
) -> list[tuple[Optional[int], str]]:
    """Coerce the ``inspect_pane`` ``lines`` payload to ``(row, text)`` pairs.

    Accepts either the structured ``[{"row": int, "text": str}, ...]`` shape
    or a bare list of strings (row index inferred positionally).
    """
    out: list[tuple[Optional[int], str]] = []
    for idx, item in enumerate(lines):
        if isinstance(item, dict):
            row = item.get("row")
            text = item.get("text", "")
        else:
            row = idx
            text = item
        out.append((row, "" if text is None else str(text)))
    return out


def scan_lines(
    lines: Iterable[Union[str, dict]],
    *,
    spinner_threshold_min: int = SPINNER_AGE_THRESHOLD_MIN,
) -> list[Detection]:
    """Scan **all** visible pane lines for ERROR-class anomalies.

    Returns a list of :class:`Detection`, one per matching line/trigger.
    An empty list means the pane looks clean. The scan deliberately covers
    every row (Issue #492 gap 1) rather than only the bottom N.
    """
    detections: list[Detection] = []
    for row, text in _normalize(lines):
        if not text:
            continue

        lowered = text.lower()
        content_hit = False
        for needle in ERROR_SUBSTRINGS:
            if needle.lower() in lowered:
                detections.append(
                    Detection(
                        kind="error",
                        reason=f"substring:{needle}",
                        row=row,
                        matched=text,
                    )
                )
                # One content hit per line is enough to flag it.
                content_hit = True
                break

        # Status code, gated on error context (only if no strong substring
        # already flagged the line, to avoid a duplicate detection).
        if not content_hit:
            code_m = _STATUS_CODE_RE.search(text)
            if code_m and _ERROR_CONTEXT_RE.search(text):
                detections.append(
                    Detection(
                        kind="error",
                        reason=f"status_code:{code_m.group(1)}",
                        row=row,
                        matched=text,
                    )
                )

        for rx in _ERROR_ANCHORED_RES:
            if rx.search(text):
                detections.append(
                    Detection(
                        kind="error",
                        reason=f"regex:{rx.pattern}",
                        row=row,
                        matched=text,
                    )
                )
                break

        m = _SPINNER_AGE_RE.match(text)
        if m:
            minutes = int(m.group(1))
            if minutes >= spinner_threshold_min:
                detections.append(
                    Detection(
                        kind="error",
                        reason=f"spinner_age:{minutes}m",
                        row=row,
                        matched=text,
                    )
                )

    return detections


def _load_lines(payload: dict) -> Sequence[Union[str, dict]]:
    """Pull the ``lines`` array out of an ``inspect_pane`` result blob.

    Accepts the raw ``structuredContent`` object (``{"lines": [...]}``) or a
    bare list already at the top level.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("lines"), list):
            return payload["lines"]
        sc = payload.get("structuredContent")
        if isinstance(sc, dict) and isinstance(sc.get("lines"), list):
            return sc["lines"]
    raise ValueError(
        "input JSON must be a list of lines or contain a 'lines' / "
        "'structuredContent.lines' array"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan inspect_pane grid lines for ERROR / spinner-age anomalies "
            "(dispatcher worker-monitoring §4(d), Issue #492)."
        )
    )
    parser.add_argument(
        "--input",
        type=argparse.FileType("r", encoding="utf-8"),
        default=sys.stdin,
        help="JSON file with inspect_pane result (default: stdin).",
    )
    parser.add_argument(
        "--spinner-threshold-min",
        type=int,
        default=SPINNER_AGE_THRESHOLD_MIN,
        help=(
            "Spinner-age threshold in minutes; a spinner counting for >= this "
            f"many minutes is an ERROR-equivalent (default: {SPINNER_AGE_THRESHOLD_MIN})."
        ),
    )
    args = parser.parse_args(argv)

    payload = json.load(args.input)
    detections = scan_lines(
        _load_lines(payload), spinner_threshold_min=args.spinner_threshold_min
    )
    json.dump(
        {"detections": [asdict(d) for d in detections]},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    # Exit 3 when an anomaly is found so a shell caller can branch without
    # parsing JSON; exit 0 means the pane is clean.
    return 3 if detections else 0


if __name__ == "__main__":
    raise SystemExit(main())
