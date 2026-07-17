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

**Spinner false-positive suppression (Issue #698)**: Claude Code renders a
completed turn's summary in the *same* ``{glyph} {verb} for {Xm Ys}`` shape
as a live old-form spinner (e.g. ``✻ Cooked for 31m 40s``). Such a summary
lingers in the scrollback of an idle worker, so every dispatcher cycle
re-matched it as a "5-minute stuck spinner" and emitted a recurring
false-positive ERROR alert. A single pane frame cannot tell a frozen
summary from a genuinely stuck spinner by content or position alone, so this
module keys on the invariant that actually separates them: a **live** spinner's
``for Xm Ys`` counter advances every cycle, while a frozen scrollback summary
is byte-identical. When the caller threads the previous cycle's spinner
identity keys back in (CLI ``--spinner-state-file``), an unchanged old-form
spinner is suppressed. The first observation of any spinner still fires (no
prior state to diff against), and a fully-frozen *live* pane is still caught
by the hash-based STALL path (``tools/inspect_pane_state.py``), so no real
signal is lost. Without prior state the scan behaves exactly as before.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Collection, Iterable, Optional, Sequence, Union

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

# Leading spinner glyphs + surrounding whitespace, stripped to form a stable
# cross-cycle identity for an old-form spinner line (Issue #698). The glyph
# rotates frame-to-frame even on a frozen summary line, and the indent can
# shift, so both are normalized away; what remains (``verb for Xm Ys``) is
# constant for a frozen scrollback summary and changes for a live spinner
# whose counter advances.
_SPINNER_GLYPH_PREFIX_RE = re.compile(
    r"^\s*[✻✺✶✷✸✹✦✧✱✲✳·∙▪●○◍◌◐◑◒◓*]+\s*"
)


def spinner_identity_key(text: str) -> str:
    """Glyph/indent-normalized identity for an old-form spinner line.

    Used by the cross-cycle diff suppression (Issue #698): a frozen scrollback
    summary (``✻ Cooked for 31m 40s``) maps to a constant key across cycles,
    while a live spinner's key changes as its ``for Xm Ys`` counter advances.
    """
    return _SPINNER_GLYPH_PREFIX_RE.sub("", text).rstrip()


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
    prev_spinner_keys: Optional[Collection[str]] = None,
) -> list[Detection]:
    """Scan **all** visible pane lines for ERROR-class anomalies.

    Returns a list of :class:`Detection`, one per matching line/trigger.
    An empty list means the pane looks clean. The scan deliberately covers
    every row (Issue #492 gap 1) rather than only the bottom N.

    ``prev_spinner_keys`` (Issue #698): identity keys
    (:func:`spinner_identity_key`) of the old-form spinner lines that reached
    the threshold on the *previous* cycle for this same worker. An aged
    spinner whose key is in this set is a frozen scrollback summary (its
    ``for Xm Ys`` counter did not advance) and its ``spinner_age`` detection
    is suppressed. ``None`` (the default) means "no prior state" — every aged
    spinner fires, so callers that do not thread state keep the pre-#698
    behaviour exactly. Only ``spinner_age`` detections are affected; substring
    / status-code / anchored-regex ERROR detections are never suppressed.
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
                # Cross-cycle diff (Issue #698): an identical old-form spinner
                # line already seen last cycle is a frozen scrollback summary,
                # not a live spinner (whose counter would have advanced), so
                # skip the recurring false positive. The first observation
                # (prev_spinner_keys None/absent) still fires.
                key = spinner_identity_key(text)
                if prev_spinner_keys is not None and key in prev_spinner_keys:
                    continue
                detections.append(
                    Detection(
                        kind="error",
                        reason=f"spinner_age:{minutes}m",
                        row=row,
                        matched=text,
                    )
                )

    return detections


def spinner_age_keys(
    lines: Iterable[Union[str, dict]],
    *,
    spinner_threshold_min: int = SPINNER_AGE_THRESHOLD_MIN,
) -> list[str]:
    """Identity keys of every threshold-reaching old-form spinner line.

    This is the set the caller persists after a cycle so the next cycle can
    diff against it (Issue #698). Keys are collected for **all** aged spinner
    lines regardless of whether their detection was suppressed, so a summary
    that fired once (first observation) is remembered and suppressed next
    cycle.
    """
    keys: list[str] = []
    for _, text in _normalize(lines):
        if not text:
            continue
        m = _SPINNER_AGE_RE.match(text)
        if m and int(m.group(1)) >= spinner_threshold_min:
            keys.append(spinner_identity_key(text))
    return keys


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


def _read_spinner_state(path: Optional[str]) -> Optional[list[str]]:
    """Load the previous cycle's aged-spinner keys from ``path``.

    Returns ``None`` when no state file was requested (so :func:`scan_lines`
    keeps its stateless pre-#698 behaviour), or an empty list when the file is
    absent / unreadable / malformed (treat as "nothing seen last cycle" — the
    first cycle after a corrupt or missing file re-fires, which is the safe
    side: a real stuck spinner is never silently masked by a bad state file).
    """
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    keys = data.get("spinner_keys") if isinstance(data, dict) else data
    if not isinstance(keys, list):
        return []
    return [str(k) for k in keys]


def _write_spinner_state(path: str, keys: Sequence[str]) -> None:
    """Persist this cycle's aged-spinner keys to ``path`` (best effort).

    Creates the parent directory if needed. A write failure is swallowed: the
    next cycle then reads no prior state and re-fires, which is the safe side.
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"spinner_keys": list(keys)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


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
    parser.add_argument(
        "--spinner-state-file",
        default=None,
        help=(
            "Per-worker JSON file holding the previous cycle's aged-spinner "
            "identity keys (Issue #698). When given, an old-form spinner whose "
            "line is unchanged from last cycle is a frozen scrollback summary "
            "and its detection is suppressed; the file is then rewritten with "
            "this cycle's keys. Omit for a stateless scan (pre-#698 behaviour)."
        ),
    )
    args = parser.parse_args(argv)

    payload = json.load(args.input)
    lines = list(_load_lines(payload))

    prev_keys = _read_spinner_state(args.spinner_state_file)
    detections = scan_lines(
        lines,
        spinner_threshold_min=args.spinner_threshold_min,
        prev_spinner_keys=prev_keys,
    )
    if args.spinner_state_file is not None:
        _write_spinner_state(
            args.spinner_state_file,
            spinner_age_keys(
                lines, spinner_threshold_min=args.spinner_threshold_min
            ),
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
