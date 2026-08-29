#!/usr/bin/env python3
"""Pick the split anchor(s) for a watcher pane, deterministically (Refs #335).

## Why this exists

``/pr-watch-pane`` used to hard-code ``target="dispatcher"`` as the split
anchor for the CI-watch pane. On 2026-08-30 the dispatcher pane was 24
columns wide, so the split was refused (``[split_refused]``) while the
secretary pane (397x53) sat entirely free. The skill read that single
target-local refusal as "the tab is full" and stalled worker dispatch.

``[split_refused]`` is **target-local**: it says the pane you aimed at
cannot be halved, not that the tab has no room. Deciding "no room" is a
geometry question over *every* pane, and a geometry question belongs in a
deterministic tool rather than in prose the reader re-derives each time.
This module answers it: given the ``list_panes`` snapshot, it returns the
anchors that can actually absorb a watcher, in priority order, so the
skill can retry the next candidate and only report "out of capacity" when
the list is exhausted.

## Floors: the runtime is the SoT

``MIN_PANE_WIDTH`` / ``MIN_PANE_HEIGHT`` / ``SECRETARY_MIN_WIDTH`` /
``SECRETARY_MIN_HEIGHT`` / ``DISPATCHER_MIN_WIDTH`` are imported from
``claude_org_runtime.dispatcher.runner`` -- the same constants the
dispatcher's balanced split enforces. They are NOT transcribed here: a
private copy that drifts from the runtime reproduces exactly the class of
inexplicable ``[split_refused]`` this tool exists to explain.

``_FALLBACK_FLOORS`` is a last-resort mirror used only when the runtime is
not importable at all (a bare checkout, a broken venv), and every output
carries ``constants_source`` so a consumer can tell which one it got.
``tools/test_pick_watcher_anchor.py`` asserts the mirror equals the
runtime's values whenever the runtime is installed, so the fallback cannot
drift silently.

## Priority order

1. ``dispatcher`` -- the historical anchor, kept first while its own (left)
   child stays >= ``DISPATCHER_MIN_WIDTH``. This mirrors the runtime's
   dispatcher-first-then-demote rule (``runner._ROLE_PRIORITY`` /
   ``_DISPATCHER_NARROW_PRIORITY``).
2. ``watcher`` / ``attention`` -- an already-resident watcher pane (a
   previous ``pr-watch-*`` pane, or the attention watcher, which
   ``/org-attention-start`` registers under ``role="attention"``).
   Watchers are low-content panes, so halving one costs the operator the
   least; the ones adjacent to the dispatcher come first so watchers keep
   stacking in one zone instead of scattering across the frame.
3. ``secretary`` -- allowed as an anchor (user-approved 2026-08-30) but
   last, and only when the split clears the ``SECRETARY_MIN_*`` floors so
   the human-facing viewport stays usable.
4. a dispatcher already narrowed to ``DISPATCHER_MIN_WIDTH`` -- strict last
   resort, below the secretary, same as the runtime's demotion.

Ties inside a tier break by larger remaining child first, then by pane id
ascending, so the ordering is total and reproducible.

Any other role (``worker``, ``curator``, ...) is never offered: a watcher
must not eat a working pane's viewport.

## Direction is chosen too, not assumed

Each candidate carries a ``direction``. Both are evaluated and the one
leaving the roomier remaining child wins (ties favour ``vertical``), which
is the runtime's own rule -- ``test_split_options_match_the_runtime_algorithm``
pins this re-implementation to ``runner._split_options`` over a grid.

This is load-bearing, not cosmetic: on 2026-08-30 a watcher was hand-placed
on the 397x53 secretary with ``direction="horizontal"``, cutting the
human's interactive pane to 397x13 -- 397 columns for a log tail, paid for
with the height of the pane a person reads and types in. On a wide, short
rect the metric rule picks ``vertical`` and that inversion cannot recur.

## The floors veto under renga, and only rank under broker

The rect ceiling is renga's physical constraint: one tab tiled across every
pane, so a child below the floors cannot exist. Under the default broker
transport each pane is an independent detached session with its own
terminal size, and the runtime's own capacity policy bypasses the rect
geometry there entirely (``max_concurrent_workers``). A broker pane whose
snapshot reports a small -- or zero, as a logical pane's does -- rect
therefore says nothing about whether another pane can be spawned off it.

So the floors are applied as a **veto under renga** and as **ranking only
under broker**: a broker anchor that fails the floors is demoted to a
``non-geometric`` tail rather than rejected, and ``capacity_exhausted``
under broker means "no anchor-role pane exists at all", never "every rect
is too small". Reporting geometric exhaustion on a transport that has no
rect ceiling would manufacture the same false "the tab is full" this tool
exists to prevent.

The transport comes from ``tools.transport.resolve()`` (``--transport``
overrides it).

## CLI

    python3 tools/pick_watcher_anchor.py --panes-json panes.json
    mcp list_panes output | python3 tools/pick_watcher_anchor.py

Exit codes: 0 = at least one candidate, 2 = no candidate (genuine capacity
exhaustion -- report to the human), 1 = malformed input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from tools import transport as _transport
except Exception:  # pragma: no cover - bare checkout without the seam
    _transport = None

TRANSPORTS = ("broker", "renga")


def resolve_transport(explicit: Optional[str] = None) -> str:
    """Resolve the active transport flag, falling back to ``broker``.

    ``tools.transport`` is the ja seam over the runtime's transport
    descriptor. When it cannot be imported (bare checkout, missing
    runtime) fall back to the code default rather than raising: the
    fallback only widens what is offered, since broker treats the floors
    as ranking rather than as a veto.
    """
    if explicit:
        return explicit
    if _transport is not None:
        try:
            return _transport.resolve()
        except Exception:
            pass
    return "broker"

# ---------------------------------------------------------------------------
# Floors (SoT = claude_org_runtime.dispatcher.runner)
# ---------------------------------------------------------------------------

# Mirror of the runtime constants, used ONLY when the runtime cannot be
# imported. Kept honest by test_pick_watcher_anchor.TestFloorDrift.
_FALLBACK_FLOORS = {
    "min_pane_width": 20,
    "min_pane_height": 5,
    "secretary_min_width": 120,
    "secretary_min_height": 30,
    "dispatcher_min_width": 80,
}

# renga's per-tab pane cap (layout_ops MAX_PANES). Reported, never used as a
# verdict -- see ``pane_cap_reached`` in :func:`build_result`.
_FALLBACK_RENGA_MAX_PANES = 16

try:  # pragma: no cover - trivial import branch
    from claude_org_runtime.dispatcher import runner as _runner

    FLOORS = {
        "min_pane_width": _runner.MIN_PANE_WIDTH,
        "min_pane_height": _runner.MIN_PANE_HEIGHT,
        "secretary_min_width": _runner.SECRETARY_MIN_WIDTH,
        "secretary_min_height": _runner.SECRETARY_MIN_HEIGHT,
        "dispatcher_min_width": _runner.DISPATCHER_MIN_WIDTH,
    }
    RENGA_MAX_PANES = _runner.RENGA_MAX_PANES
    CONSTANTS_SOURCE = "claude_org_runtime.dispatcher.runner"
except Exception:  # pragma: no cover - exercised via monkeypatch in tests
    FLOORS = dict(_FALLBACK_FLOORS)
    RENGA_MAX_PANES = _FALLBACK_RENGA_MAX_PANES
    CONSTANTS_SOURCE = "fallback-mirror"


# Tier ranks. Higher wins. The dispatcher's demoted rank sits below every
# other tier, mirroring runner._DISPATCHER_NARROW_PRIORITY.
TIER_DISPATCHER = 3
TIER_WATCHER = 2
TIER_SECRETARY = 1
TIER_DISPATCHER_NARROW = 0
# Broker-only tail: an anchor whose rect fails the floors. Below every
# geometric tier, but still offered, because broker has no rect ceiling.
TIER_NON_GEOMETRIC = -1

# Roles that may host a watcher split. ``attention`` is the attention
# watcher's own role label (``/org-attention-start``); it belongs to the
# same low-content watcher tier as ``pr-watch-*``, and leaving it out
# would report capacity exhaustion in a layout where it is the only
# splittable pane.
WATCHER_ROLES = ("watcher", "attention")
ANCHOR_ROLES = ("dispatcher",) + WATCHER_ROLES + ("secretary",)

# Within the broker non-geometric tail the geometric tiers no longer
# apply, so keep the same role preference by hand.
_ROLE_TAIL_ORDER = {"dispatcher": 0, "watcher": 1, "attention": 1, "secretary": 2}

_TRAILING_DIGITS = re.compile(r"(\d+)\s*$")


class PaneInputError(ValueError):
    """The panes payload is not a usable list_panes snapshot."""


@dataclass(frozen=True)
class Pane:
    id: str
    name: Optional[str]
    role: Optional[str]
    x: int
    y: int
    width: int
    height: int

    @property
    def id_key(self) -> int:
        """Numeric tie-breaker derived from the trailing digits of ``id``.

        Pane ids are ``%16`` under tmux and ``w1:p3`` under herdr; both end
        in the pane number, which is the ordering the runtime uses too.
        An id with no digits sorts last rather than raising -- ordering is
        a comfort here, not a correctness property.
        """
        m = _TRAILING_DIGITS.search(self.id)
        return int(m.group(1)) if m else sys.maxsize


@dataclass
class Candidate:
    target: str
    target_id: str
    role: str
    tier: str
    direction: str
    new_w: int
    new_h: int
    metric: int
    reason: str
    _sort: tuple = field(default=(), repr=False, compare=False)

    def to_jsonable(self, rank: int) -> dict:
        return {
            "rank": rank,
            "target": self.target,
            "target_id": self.target_id,
            "role": self.role,
            "tier": self.tier,
            "direction": self.direction,
            "new_w": self.new_w,
            "new_h": self.new_h,
            "reason": self.reason,
        }


@dataclass
class Rejection:
    target: Optional[str]
    target_id: str
    role: Optional[str]
    reason: str

    def to_jsonable(self) -> dict:
        return {
            "target": self.target,
            "target_id": self.target_id,
            "role": self.role,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_panes(payload: Any) -> list[Pane]:
    """Normalise a ``list_panes`` payload into :class:`Pane` records.

    Accepts the MCP ``structuredContent`` shape (``{"panes": [...]}``) and a
    bare list, since callers paste either.
    """
    if isinstance(payload, dict):
        raw = payload.get("panes")
        if raw is None:
            raise PaneInputError("payload has no 'panes' key")
    else:
        raw = payload
    if not isinstance(raw, list):
        raise PaneInputError("'panes' is not a list")

    panes: list[Pane] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise PaneInputError(f"panes[{i}] is not an object")
        try:
            width = entry.get("width", entry.get("w"))
            height = entry.get("height", entry.get("h"))
            panes.append(
                Pane(
                    id=str(entry["id"]),
                    name=entry.get("name"),
                    role=entry.get("role"),
                    x=int(entry.get("x", 0)),
                    y=int(entry.get("y", 0)),
                    width=int(width),
                    height=int(height),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PaneInputError(f"panes[{i}] has malformed geometry: {exc}") from exc
    return panes


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def rect_adjacent(a: Pane, b: Pane) -> bool:
    """True when ``a`` and ``b`` share an edge (same rule as the runtime)."""
    horizontal_share = (a.x + a.width == b.x or b.x + b.width == a.x) and (
        max(a.y, b.y) < min(a.y + a.height, b.y + b.height)
    )
    vertical_share = (a.y + a.height == b.y or b.y + b.height == a.y) and (
        max(a.x, b.x) < min(a.x + a.width, b.x + b.width)
    )
    return horizontal_share or vertical_share


def split_options(pane: Pane) -> list[tuple[str, int, int, int]]:
    """Return ``(direction, new_w, new_h, metric)`` for the splits that fit.

    Both directions are evaluated -- vertical halves the width, horizontal
    halves the height -- and a direction survives only if the resulting
    child clears ``MIN_PANE_*`` (plus ``SECRETARY_MIN_*`` for the
    secretary). ``metric`` is the size along the halved dimension, so a
    bigger metric means a roomier child.
    """
    options: list[tuple[str, int, int, int]] = []
    for direction, new_w, new_h, metric in (
        ("vertical", pane.width // 2, pane.height, pane.width // 2),
        ("horizontal", pane.width, pane.height // 2, pane.height // 2),
    ):
        if new_w < FLOORS["min_pane_width"] or new_h < FLOORS["min_pane_height"]:
            continue
        if pane.role == "secretary" and (
            new_w < FLOORS["secretary_min_width"]
            or new_h < FLOORS["secretary_min_height"]
        ):
            continue
        options.append((direction, new_w, new_h, metric))
    return options


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _floor_reject_reason(pane: Pane) -> str:
    if pane.role == "secretary":
        return (
            "neither direction clears the floors "
            f"({FLOORS['min_pane_width']}x{FLOORS['min_pane_height']} plus the "
            f"secretary floor {FLOORS['secretary_min_width']}x"
            f"{FLOORS['secretary_min_height']}); pane is "
            f"{pane.width}x{pane.height}"
        )
    return (
        "neither direction clears the floor "
        f"{FLOORS['min_pane_width']}x{FLOORS['min_pane_height']}; pane is "
        f"{pane.width}x{pane.height}"
    )


def _non_geometric_direction(pane: Pane) -> tuple[str, int, int, int]:
    """Direction for a broker anchor whose rect fails the floors.

    Halve the longer side so the reported shape stays plausible; the
    numbers are informational under broker, where the child pane gets its
    own terminal size rather than a slice of this rect.
    """
    if pane.height > pane.width:
        return ("horizontal", pane.width, pane.height // 2, pane.height // 2)
    return ("vertical", pane.width // 2, pane.height, pane.width // 2)


def pick(
    panes: list[Pane], transport: str = "broker"
) -> tuple[list[Candidate], list[Rejection]]:
    """Return ``(candidates, rejections)`` -- candidates in priority order.

    Under ``renga`` the floors veto a pane; under ``broker`` they only rank
    it (see the module docstring).
    """
    floors_veto = transport == "renga"
    dispatcher = next((p for p in panes if p.role == "dispatcher"), None)

    candidates: list[Candidate] = []
    rejections: list[Rejection] = []

    for pane in panes:
        if pane.role not in ANCHOR_ROLES:
            rejections.append(
                Rejection(
                    pane.name,
                    pane.id,
                    pane.role,
                    "role is not an anchor role "
                    f"({'/'.join(ANCHOR_ROLES)}); a watcher must not halve a "
                    "working pane",
                )
            )
            continue
        if pane.name is None:
            # spawn_pane addresses the anchor by name; an unnamed pane is
            # not addressable, so it cannot be offered as a target.
            rejections.append(
                Rejection(None, pane.id, pane.role, "pane has no name to target")
            )
            continue

        options = split_options(pane)
        if not options:
            if floors_veto:
                rejections.append(
                    Rejection(pane.name, pane.id, pane.role, _floor_reject_reason(pane))
                )
                continue
            # broker: the rect is not a ceiling here, so keep the pane as a
            # last-resort candidate instead of calling the tab full.
            direction, new_w, new_h, metric = _non_geometric_direction(pane)
            candidates.append(
                Candidate(
                    target=pane.name,
                    target_id=pane.id,
                    role=pane.role,
                    tier="non-geometric",
                    direction=direction,
                    new_w=new_w,
                    new_h=new_h,
                    metric=metric,
                    reason=(
                        "broker fallback: this rect fails the floors ("
                        f"{pane.width}x{pane.height}), but broker panes are "
                        "independent detached sessions with no rect ceiling, "
                        "so the geometry is not a refusal"
                    ),
                    _sort=(
                        -TIER_NON_GEOMETRIC,
                        _ROLE_TAIL_ORDER.get(pane.role or "", 9),
                        -metric,
                        pane.id_key,
                        pane.name,
                    ),
                )
            )
            continue

        # Both directions may fit; take the roomier child. max() keeps the
        # first maximal element and vertical is listed first, so ties
        # deterministically favour the vertical split.
        direction, new_w, new_h, metric = max(options, key=lambda o: o[3])

        if pane.role == "dispatcher":
            vertical = next((o for o in options if o[0] == "vertical"), None)
            if vertical is not None and vertical[1] >= FLOORS["dispatcher_min_width"]:
                direction, new_w, new_h, metric = vertical
                tier_rank, tier = TIER_DISPATCHER, "dispatcher"
                reason = (
                    "primary anchor: vertical split leaves the dispatcher "
                    f"{new_w} cols, at or above the {FLOORS['dispatcher_min_width']}"
                    "-col comfort floor"
                )
            else:
                tier_rank, tier = TIER_DISPATCHER_NARROW, "dispatcher-narrow"
                reason = (
                    "last resort: the dispatcher is already at or below its "
                    f"{FLOORS['dispatcher_min_width']}-col comfort floor, so "
                    "splitting it again squeezes the monitoring viewport"
                )
        elif pane.role in WATCHER_ROLES:
            tier_rank, tier = TIER_WATCHER, "watcher"
            adjacent = dispatcher is not None and rect_adjacent(pane, dispatcher)
            reason = (
                "resident watcher pane (low-content, cheapest to halve)"
                + (
                    "; adjacent to the dispatcher so watchers stay in one zone"
                    if adjacent
                    else ""
                )
            )
        else:  # secretary
            tier_rank, tier = TIER_SECRETARY, "secretary"
            reason = (
                "secretary is a permitted anchor (approved 2026-08-30) and the "
                f"split leaves {new_w}x{new_h}, clearing the secretary floor "
                f"{FLOORS['secretary_min_width']}x{FLOORS['secretary_min_height']}"
            )

        adjacency_rank = 0
        if pane.role in WATCHER_ROLES:
            adjacency_rank = (
                0 if dispatcher is not None and rect_adjacent(pane, dispatcher) else 1
            )

        candidates.append(
            Candidate(
                target=pane.name,
                target_id=pane.id,
                role=pane.role,
                tier=tier,
                direction=direction,
                new_w=new_w,
                new_h=new_h,
                metric=metric,
                reason=reason,
                _sort=(-tier_rank, adjacency_rank, -metric, pane.id_key, pane.name),
            )
        )

    candidates.sort(key=lambda c: c._sort)
    return candidates, rejections


def build_result(panes: list[Pane], transport: str = "broker") -> dict:
    """Assemble the JSON result for ``panes`` under ``transport``.

    ``pane_cap_reached`` reports that the snapshot already holds renga's
    per-tab pane cap (``RENGA_MAX_PANES``). It is **transport-scoped, like
    the floors**: under renga the cap is a real ceiling -- every further
    spawn is refused whatever the geometry says -- so it sets
    ``capacity_exhausted`` and the skill reports the condition instead of
    walking a candidate list that cannot succeed. Under broker each pane is
    its own detached session with no per-tab ceiling, so there the count is
    reported and nothing else; treating it as a verdict would manufacture
    the very false "the tab is full" this tool exists to prevent.
    """
    candidates, rejections = pick(panes, transport)
    pane_cap_reached = len(panes) >= RENGA_MAX_PANES
    cap_is_a_ceiling = transport == "renga" and pane_cap_reached
    return {
        "constants_source": CONSTANTS_SOURCE,
        "transport": transport,
        "floors": dict(FLOORS),
        "pane_count": len(panes),
        "renga_max_panes": RENGA_MAX_PANES,
        "pane_cap_reached": pane_cap_reached,
        "pane_cap_is_a_ceiling": cap_is_a_ceiling,
        "candidates": [c.to_jsonable(i + 1) for i, c in enumerate(candidates)],
        "rejected": [r.to_jsonable() for r in rejections],
        "capacity_exhausted": not candidates or cap_is_a_ceiling,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_text(result: dict) -> str:
    lines = []
    floors = result["floors"]
    lines.append(
        "floors: MIN_PANE {min_pane_width}x{min_pane_height} / SECRETARY_MIN "
        "{secretary_min_width}x{secretary_min_height} / DISPATCHER_MIN_WIDTH "
        "{dispatcher_min_width}".format(**floors)
        + f" (from {result['constants_source']})"
    )
    lines.append(
        f"transport: {result['transport']}"
        + (
            " (floors veto: a pane below them is not offered)"
            if result["transport"] == "renga"
            else " (floors rank only: broker panes are independent sessions "
            "with no rect ceiling)"
        )
    )
    if result["pane_cap_reached"]:
        lines.append(
            f"note: this snapshot holds {result['pane_count']} panes, at or "
            f"over renga's per-tab cap of {result['renga_max_panes']}"
            + (
                " -- under renga that refuses every further spawn regardless "
                "of geometry, so capacity is exhausted"
                if result["pane_cap_is_a_ceiling"]
                else " (advisory only: the broker transport has no per-tab cap)"
            )
        )
    if result["candidates"] and not result["pane_cap_is_a_ceiling"]:
        lines.append("candidates (try in order):")
        for c in result["candidates"]:
            lines.append(
                f"  {c['rank']}. target={c['target']} (id={c['target_id']}, "
                f"role={c['role']}, tier={c['tier']}) direction={c['direction']} "
                f"-> {c['new_w']}x{c['new_h']} -- {c['reason']}"
            )
    elif result["pane_cap_is_a_ceiling"]:
        lines.append(
            "candidates: SUPPRESSED - the renga per-tab pane cap is reached, "
            "so no spawn can succeed (capacity exhausted)"
        )
    else:
        lines.append("candidates: NONE - every pane fails the floors (capacity exhausted)")
    if result["rejected"]:
        lines.append("rejected:")
        for r in result["rejected"]:
            lines.append(
                f"  - target={r['target']} (id={r['target_id']}, role={r['role']}): "
                f"{r['reason']}"
            )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pick_watcher_anchor",
        description=(
            "Pick the split anchor(s) for a watcher pane from a list_panes "
            "snapshot, in priority order (dispatcher -> resident watcher -> "
            "secretary), honouring the runtime's pane floors."
        ),
    )
    p.add_argument(
        "--panes-json",
        default="-",
        help="path to the list_panes JSON snapshot ('-' = stdin, the default).",
    )
    p.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default=None,
        help=(
            "active transport (default: resolve via ORG_TRANSPORT / the code "
            "default). Under renga the pane floors veto an anchor; under "
            "broker they only rank it."
        ),
    )
    p.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="output format (default: json).",
    )
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.panes_json == "-":
        raw = sys.stdin.read()
    else:
        with open(args.panes_json, encoding="utf-8") as fh:
            raw = fh.read()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"pick_watcher_anchor: input is not valid JSON: {exc}", file=sys.stderr)
        return 1

    try:
        panes = parse_panes(payload)
    except PaneInputError as exc:
        print(f"pick_watcher_anchor: {exc}", file=sys.stderr)
        return 1

    result = build_result(panes, resolve_transport(args.transport))
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_render_text(result))
    return 2 if result["capacity_exhausted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
