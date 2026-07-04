#!/usr/bin/env python3
"""Deterministic pane-state extractor for dispatcher stall detection (Issues #680 / #671).

This is the codified core of the screen-change / active-spinner judgement in
``.dispatcher/references/worker-monitoring.md`` Step 5. It replaces two prose
heuristics that were too weak to run deterministically by eyeballing the
``inspect_pane`` grid:

* **#680 — screen-change judgement was a single-point compare.** Step 5 used to
  compare ``(target_line_text, cursor)`` between cycles to decide idle vs active.
  Claude Code's TUI keeps that pair static while a long tool-run / ultracode turn
  churns the scrollback (Read/Edit/Bash output, thinking spinner), so ``idle_streak``
  climbed mechanically and STALL_SUSPECTED false-fired (observed twice in one
  session: ``worker-runtime-129-observed-session-binding`` + ``worker-ja-679-...``).
  This module hashes **all** normalized visible rows, so any real scrollback change
  flips the hash and resets the streak.

* **#671 — the spinner age regex no longer matched the rendered form.** Claude Code
  now renders an active spinner as ``{glyph} {Verb}… (1h 1m 42s · ↓ 121.5k tokens)``
  rather than the old ``{glyph} {verb} for 9m 12s``. A single long model turn (deep
  research, ultracode) legitimately spins for tens of minutes while the scrollback is
  static, so the hash alone would still climb ``idle_streak``. This module parses the
  new-form spinner and reports whether its elapsed counter is *increasing*, which the
  caller uses to suppress STALL/PANE_OUTPUT — but only up to
  ``SPINNER_ACTIVE_SUPPRESS_CAP_MIN`` (a frozen-API spinner can count forever, so the
  suppression must not be permanent).

## Why one module (Major: shared parser)

The spinner *read* (elapsed → suppression signal) and the hash *exclusion* (elapsed /
token counters / rotating glyph → placeholder) must agree on where a spinner line
starts and ends. Two independently-maintained regexes in prose would drift (one gets
updated, the other does not). Here :func:`parse_new_spinner` (read) and
:func:`normalize_visible_lines` (exclude) share the same :data:`_NEW_SPINNER_RE` /
:data:`_OLD_SPINNER_RE`, so a spinner-form change is a one-line edit.

## Division of labour with tools/inspect_anomaly_scan.py

``inspect_anomaly_scan.py`` keeps the **ERROR-class** detections (substring / status
code / anchored regex + the *old* ``for Xm Ys`` spinner-age → ERROR at 5 min). The old
spinner form carries the literal `` for `` and is an API-retry / hang signal, so it
stays an ERROR. This module owns the *new* active spinner, which is a
``spinner_active`` suppression signal (anomaly only past the cap). The two spinner
regexes are disjoint (`` for `` vs ``… (``), so the ERROR path never fires on a healthy
new-form spinner (Major: spinner detection split in two).

The dispatcher pipes the ``inspect_pane`` ``structuredContent`` JSON through this module
once per worker per cycle and writes the returned record straight into
``.state/dispatcher/worker-idle-state.json`` — the prose never recomputes a hash by hand
(Major: prose hand-hashing is non-deterministic and forbidden).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional, Sequence, Union

# Suppress cap for an active new-form spinner (minutes). While a spinner's elapsed
# counter is increasing AND below this cap, STALL_SUSPECTED / PANE_OUTPUT_WITHOUT_PEER_MSG
# are suppressed for that worker (the model is genuinely working through one long turn).
# Past the cap the suppression is released and the normal anomaly path resumes, so a
# frozen / dead-API spinner that keeps counting cannot mask a real stall forever
# (Issue #671 Blocker 1). 90 min tolerates observed ~61 min turns with headroom.
SPINNER_ACTIVE_SUPPRESS_CAP_MIN = 90

# Spinner glyphs Claude Code rotates through (kept generous — a new glyph must not
# silently change the hash). Shared by both spinner regexes.
_SPINNER_GLYPHS = r"✻✺✶✷✸✹✦✧✱✲✳·∙▪●○◍◌◐◑◒◓*"

# New-form active spinner: ``{glyph} {Verb}… (<elapsed> · <tokens> · esc to interrupt)``.
# The verb is followed by an ellipsis (real ``…`` U+2026 or ASCII ``...``) then a
# parenthesised group whose first token is the elapsed time. ``\w+`` is Unicode-aware
# for str patterns so non-ASCII verbs match.
_NEW_SPINNER_RE = re.compile(
    rf"^\s*(?:[{_SPINNER_GLYPHS}]+\s*)?(?P<verb>\w+)\s*(?:…|\.\.\.)\s*\((?P<inside>[^)]*)\)"
)

# Old-form spinner: ``{glyph} {verb} for {Xm Ys}``. Owned by inspect_anomaly_scan.py for
# the ERROR path; matched here only so its changing elapsed is excluded from the hash
# (a rotating old spinner must not churn the content hash either).
_OLD_SPINNER_RE = re.compile(
    rf"^\s*[{_SPINNER_GLYPHS}]+\s+\w+\s+for\s+\d+m\s+\d+s"
)

# Elapsed time inside the new-form parenthesis: ``1h 1m 42s`` / ``1m 42s`` / ``42s``.
_ELAPSED_RE = re.compile(
    r"^\s*(?:(?P<h>\d+)\s*h\s*)?(?:(?P<m>\d+)\s*m\s*)?(?P<s>\d+)\s*s\b"
)

# ANSI CSI escape residue (defensive — grid format is usually already de-ANSI'd).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Canonical placeholder a spinner line collapses to in the hash. Constant (no verb, no
# elapsed, no tokens, no glyph) so a purely-animating spinner hashes stable and only real
# scrollback movement flips the content hash.
_SPINNER_PLACEHOLDER = "⟪SPINNER⟫"


@dataclass(frozen=True)
class SpinnerInfo:
    """A parsed new-form active spinner (the suppression signal, #671)."""

    signature: str
    elapsed_sec: int


def parse_new_spinner(text: str) -> Optional[SpinnerInfo]:
    """Parse a single line as a new-form active spinner, or return ``None``.

    ``signature`` is the verb (e.g. ``"Gesticulating"``); ``elapsed_sec`` is the elapsed
    counter in seconds. Only the *new* form (``Verb… (...)``) is parsed — the old
    ``for Xm Ys`` form is an ERROR signal owned by ``inspect_anomaly_scan.py``.
    """
    m = _NEW_SPINNER_RE.match(text)
    if not m:
        return None
    em = _ELAPSED_RE.match(m.group("inside"))
    if not em:
        return None
    hours = int(em.group("h") or 0)
    minutes = int(em.group("m") or 0)
    seconds = int(em.group("s"))
    return SpinnerInfo(
        signature=m.group("verb"),
        elapsed_sec=hours * 3600 + minutes * 60 + seconds,
    )


def _normalize_one(text: str) -> str:
    """Normalize a single visible line for hashing.

    Strips ANSI residue and trailing whitespace, then collapses any spinner line (new or
    old form) to :data:`_SPINNER_PLACEHOLDER` so the volatile glyph / elapsed / token
    counters do not churn the hash.
    """
    line = _ANSI_RE.sub("", text)
    line = line.rstrip()
    # Gate the hash-EXCLUDE on the SAME validated parser the suppression-READ uses
    # (parse_new_spinner requires a real elapsed timer inside the parenthesis), not the
    # bare _NEW_SPINNER_RE. Otherwise a churning non-timer prose line of shape
    # ``Word… (attempt 1)`` → ``(attempt 2)`` would collapse to the placeholder and hash
    # stable across cycles — re-introducing the #680 STALL false positive with no spinner
    # parsed to compensate. Tying EXCLUDE to READ keeps the two in lockstep (Major a).
    if parse_new_spinner(line) is not None or _OLD_SPINNER_RE.match(line):
        return _SPINNER_PLACEHOLDER
    return line


def _coerce_lines(lines: Iterable[Union[str, dict]]) -> list[str]:
    """Coerce the ``inspect_pane`` ``lines`` payload to a list of text strings.

    Accepts the structured ``[{"row": int, "text": str}, ...]`` shape or a bare list of
    strings. Row indices are irrelevant to a whole-screen hash, so only text is kept.
    """
    out: list[str] = []
    for item in lines:
        if isinstance(item, dict):
            text = item.get("text", "")
        else:
            text = item
        out.append("" if text is None else str(text))
    return out


def normalize_visible_lines(lines: Iterable[Union[str, dict]]) -> list[str]:
    """Return the normalized visible lines used for the content hash.

    Trailing blank rows are dropped (a grid pads to a fixed height; the trailing pad is
    stable but stripping it keeps the hash robust to height changes across cycles).
    """
    normalized = [_normalize_one(t) for t in _coerce_lines(lines)]
    while normalized and normalized[-1] == "":
        normalized.pop()
    return normalized


def content_hash(normalized_lines: Sequence[str]) -> str:
    """SHA-256 of the normalized visible lines joined by newlines."""
    joined = "\n".join(normalized_lines)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def find_active_spinner(lines: Iterable[Union[str, dict]]) -> Optional[SpinnerInfo]:
    """Return the first new-form active spinner among the visible lines, if any."""
    for text in _coerce_lines(lines):
        info = parse_new_spinner(text)
        if info is not None:
            return info
    return None


@dataclass
class PaneState:
    """The deterministic pane observation the dispatcher writes into idle-state.

    ``spinner_elapsed_increased`` / ``cap_exceeded`` / ``suppress_stall`` are only
    meaningful when a spinner is present; they compare against the previous cycle's
    spinner (passed via CLI / kwargs) so the caller does not re-derive the suppression
    decision in prose.
    """

    content_hash: str
    normalized_lines: list[str] = field(default_factory=list)
    spinner_present: bool = False
    spinner_signature: Optional[str] = None
    spinner_elapsed_sec: Optional[int] = None
    spinner_elapsed_increased: bool = False
    cap_exceeded: bool = False
    suppress_stall: bool = False


def extract_pane_state(
    lines: Iterable[Union[str, dict]],
    *,
    prev_spinner_signature: Optional[str] = None,
    prev_spinner_elapsed_sec: Optional[int] = None,
    suppress_cap_min: int = SPINNER_ACTIVE_SUPPRESS_CAP_MIN,
) -> PaneState:
    """Extract hash + active-spinner suppression signal from one ``inspect_pane`` grid.

    The suppression decision (#671):

    * ``cap_exceeded`` — spinner elapsed >= cap. Suppression is released so a frozen /
      dead-API spinner cannot mask a stall forever (Blocker 1).
    * ``spinner_elapsed_increased`` — the counter advanced since last cycle. A new spinner
      (no previous, or the signature changed = new turn) counts as increased. Equal
      elapsed across a ~3 min cycle means a frozen spinner → not increased.
    * ``suppress_stall`` — spinner present AND not past cap AND increasing. Only then does
      the caller hold back STALL_SUSPECTED / PANE_OUTPUT for this worker.
    """
    lines_list = _coerce_lines(lines)
    normalized = normalize_visible_lines(lines_list)
    state = PaneState(
        content_hash=content_hash(normalized),
        normalized_lines=normalized,
    )

    spinner = find_active_spinner(lines_list)
    if spinner is None:
        return state

    state.spinner_present = True
    state.spinner_signature = spinner.signature
    state.spinner_elapsed_sec = spinner.elapsed_sec

    cap_sec = suppress_cap_min * 60
    state.cap_exceeded = spinner.elapsed_sec >= cap_sec

    if prev_spinner_elapsed_sec is None or prev_spinner_signature != spinner.signature:
        # First observation of this spinner (or a new turn) — treat as active.
        state.spinner_elapsed_increased = True
    else:
        state.spinner_elapsed_increased = spinner.elapsed_sec > prev_spinner_elapsed_sec

    state.suppress_stall = (
        not state.cap_exceeded and state.spinner_elapsed_increased
    )
    return state


def compute_idle_transition(
    prev_record: Optional[dict],
    observation: PaneState,
    now_ts: str,
    *,
    anomaly_fired: bool = False,
) -> tuple[dict, dict]:
    """Compute the next ``worker-idle-state.json`` record from one observation.

    ``prev_record`` is the existing record dict (``None`` / ``{}`` for a brand-new
    worker). ``observation`` is :func:`extract_pane_state` output (already fed the
    previous cycle's spinner signature / elapsed). ``now_ts`` is this cycle's inspect
    time (ISO-8601 UTC).

    Returns ``(new_record, decision)``. ``decision["transition"]`` is one of
    ``first_observation`` / ``idle`` / ``active`` / ``active_continuation``.

    Encodes the deterministic parts of ``worker-monitoring.md`` Step 5 (b) so the prose
    never hand-counts a streak or hand-hashes a screen:

    * **#680 hash update** — ``idle`` (hash unchanged) increments ``idle_streak_cycles``
      and holds ``last_content_change_ts``; a hash change resets the streak and, on an
      idle→active transition (``prev_streak >= 1``), sets ``last_content_change_ts`` to
      the *previous* ``last_check_ts`` (so a same-cycle peer message — persisted before
      this inspect — is not cut off; the round-3 race fix carried over from the
      target-line design). An active continuation (``prev_streak == 0``) holds the START
      ts.
    * **Blocker 2 migration** — a record missing ``last_visible_content_hash`` is treated
      as a *first observation*: store the hash, reset ``idle_streak_cycles = 0`` and
      ``last_content_change_ts = null``. The deprecated ``last_target_line_text`` /
      cursor fields are never read for the new logic (they are preserved untouched only
      so an operator can still see the pre-migration value).
    * **Anomaly reset (rule 3)** — when ``anomaly_fired`` (Step 4 sent APPROVAL_BLOCKED /
      ERROR to notify this cycle), the streak is reset like an idle→active re-observation
      (``idle_streak_cycles = 0``, ``last_content_change_ts = previous last_check_ts``),
      regardless of whether the hash moved. Passed in by the caller because a pane
      observation alone cannot know Step 4 fired.

    ``completion_reported_at`` and any unknown keys are preserved verbatim — the
    completion gate (#658) is lifecycle-event-driven (Step 2), never inspect-driven. Rule
    4 (record deletion on pane exit) and rule 6 (lifecycle ``completion_reported_at``
    set/clear) are outside the inspect scope and remain caller-applied.
    """
    prev = dict(prev_record) if prev_record else {}
    prev_hash = prev.get("last_visible_content_hash")
    prev_streak = prev.get("idle_streak_cycles") or 0
    prev_check_ts = prev.get("last_check_ts")

    new = dict(prev)  # preserve completion_reported_at + deprecated fields
    new["last_visible_content_hash"] = observation.content_hash
    new["last_check_ts"] = now_ts
    new["last_spinner_signature"] = observation.spinner_signature
    new["last_spinner_elapsed_sec"] = observation.spinner_elapsed_sec
    if observation.spinner_present:
        new["last_spinner_seen_ts"] = now_ts
    else:
        new.setdefault("last_spinner_seen_ts", None)

    if prev_hash is None:
        new["idle_streak_cycles"] = 0
        new["last_content_change_ts"] = None
        transition = "first_observation"
    elif anomaly_fired:
        # rule (3): APPROVAL_BLOCKED / ERROR went to notify — rewind the stall evaluation
        # and re-baseline the cutoff to the previous last_check_ts (same anchor as an
        # idle→active transition), independent of whether the hash moved.
        new["idle_streak_cycles"] = 0
        new["last_content_change_ts"] = prev_check_ts
        transition = "anomaly_reset"
    elif observation.content_hash == prev_hash:
        new["idle_streak_cycles"] = prev_streak + 1
        new["last_content_change_ts"] = prev.get("last_content_change_ts")
        transition = "idle"
    else:
        new["idle_streak_cycles"] = 0
        if prev_streak >= 1:
            new["last_content_change_ts"] = prev_check_ts
            transition = "active"
        else:
            new["last_content_change_ts"] = prev.get("last_content_change_ts")
            transition = "active_continuation"

    decision = {
        "transition": transition,
        "suppress_stall": observation.suppress_stall,
        "cap_exceeded": observation.cap_exceeded,
    }
    return new, decision


def _load_lines(payload: object) -> Sequence[Union[str, dict]]:
    """Pull the ``lines`` array out of an ``inspect_pane`` result blob.

    Accepts the raw ``structuredContent`` object (``{"lines": [...]}``), a top-level
    ``{"structuredContent": {"lines": [...]}}`` blob, or a bare list already at top level.
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


def _nullable_int(raw: str) -> Optional[int]:
    """argparse coercion that maps the JSON-null idioms to ``None``.

    ``worker-idle-state.json`` stores ``last_spinner_elapsed_sec`` as ``null`` on every
    first observation and every no-spinner cycle (the common case). A ``jq -r`` extraction
    of that null substitutes the literal ``"null"`` (an unset var → ``""``) into the
    prose one-liner, so a plain ``type=int`` would abort the pipeline on its most common
    input. Treat ``""`` / ``"null"`` / ``"None"`` as ``None`` instead.
    """
    if raw in ("", "null", "None"):
        return None
    return int(raw)


def _nullable_str(raw: str) -> Optional[str]:
    """As :func:`_nullable_int`, for string flags — ``""`` / ``"null"`` / ``"None"`` → None."""
    if raw in ("", "null", "None"):
        return None
    return raw


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract content hash + active-spinner suppression signal from an "
            "inspect_pane grid (dispatcher stall detection, Issues #680 / #671). "
            "With --now-ts (record mode) also emits the next worker-idle-state record."
        )
    )
    parser.add_argument(
        "--input",
        type=argparse.FileType("r", encoding="utf-8"),
        default=sys.stdin,
        help="JSON file with inspect_pane result (default: stdin).",
    )
    parser.add_argument(
        "--prev-spinner-signature",
        type=_nullable_str,
        default=None,
        help=(
            "Previous cycle's spinner verb (for the increased check). Ignored in record "
            "mode (derived from --prev-record). '' / 'null' / 'None' mean no prior spinner."
        ),
    )
    parser.add_argument(
        "--prev-spinner-elapsed-sec",
        type=_nullable_int,
        default=None,
        help=(
            "Previous cycle's spinner elapsed seconds. Ignored in record mode (derived "
            "from --prev-record). '' / 'null' / 'None' mean no prior spinner."
        ),
    )
    parser.add_argument(
        "--suppress-cap-min",
        type=int,
        default=SPINNER_ACTIVE_SUPPRESS_CAP_MIN,
        help=(
            "Active-spinner suppress cap in minutes; at / past this the suppression "
            f"is released (default: {SPINNER_ACTIVE_SUPPRESS_CAP_MIN})."
        ),
    )
    parser.add_argument(
        "--prev-record",
        type=_nullable_str,
        default=None,
        help=(
            "JSON of the worker's existing worker-idle-state record. When given together "
            "with --now-ts, the previous spinner signature / elapsed are read from it and "
            "the next record is emitted (record mode). '' / 'null' mean no prior record."
        ),
    )
    parser.add_argument(
        "--now-ts",
        type=_nullable_str,
        default=None,
        help=(
            "This cycle's inspect time (ISO-8601 UTC). Enables record mode: the output "
            "gains a 'record' (compute_idle_transition result, written to "
            "worker-idle-state.json as-is) and a 'decision' object."
        ),
    )
    parser.add_argument(
        "--anomaly-fired",
        action="store_true",
        help=(
            "Step 4 sent APPROVAL_BLOCKED / ERROR to notify this cycle. Record mode only: "
            "forces the rule (3) streak reset in the emitted record."
        ),
    )
    args = parser.parse_args(argv)

    payload = json.load(args.input)
    lines = _load_lines(payload)

    prev_record = json.loads(args.prev_record) if args.prev_record else None
    if prev_record is not None:
        # Record mode reads the prior spinner from the record so the caller passes one
        # blob instead of separately-extracted (and null-fragile) --prev-spinner-* flags.
        prev_sig = prev_record.get("last_spinner_signature")
        prev_elapsed = prev_record.get("last_spinner_elapsed_sec")
    else:
        prev_sig = args.prev_spinner_signature
        prev_elapsed = args.prev_spinner_elapsed_sec

    state = extract_pane_state(
        lines,
        prev_spinner_signature=prev_sig,
        prev_spinner_elapsed_sec=prev_elapsed,
        suppress_cap_min=args.suppress_cap_min,
    )

    if args.now_ts is not None:
        new_record, decision = compute_idle_transition(
            prev_record, state, args.now_ts, anomaly_fired=args.anomaly_fired
        )
        out: object = {
            "observation": asdict(state),
            "record": new_record,
            "decision": decision,
        }
    else:
        out = asdict(state)

    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    # Exit 4 when the caller should suppress this cycle's stall evaluation (spinner
    # active and below cap); exit 0 otherwise. Lets a shell caller branch without
    # parsing JSON. (Exit 3 is inspect_anomaly_scan.py's "ERROR found" — kept distinct.)
    return 4 if state.suppress_stall else 0


if __name__ == "__main__":
    raise SystemExit(main())
