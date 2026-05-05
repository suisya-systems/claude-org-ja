#!/usr/bin/env python3
"""Secretary-side pending-decisions register (Issue #297).

Tracks judgment-requests received by the Secretary from workers so that
the dispatcher's SECRETARY_RELAY_GAP_SUSPECTED detection (Step 5.1 in
``.dispatcher/CLAUDE.md``) can use a deterministic register lookup
instead of proxy heuristics (snapshot diff / send_message timing).

Lifecycle:

* When the Secretary receives a judgment-request from a worker, it
  appends an entry with ``status="pending"``.
* When the Secretary relays the question to the human user, it resolves
  the entry with ``kind="to_user"`` (status becomes ``escalated``).
* When the Secretary relays the human's answer back to the worker, it
  resolves with ``kind="to_worker"`` (status becomes ``resolved``).

The dispatcher periodically calls :func:`list_pending_older_than` to
find entries whose ``received_at`` is older than the relay-gap window;
non-empty result triggers a SECRETARY_RELAY_GAP_SUSPECTED notification
to the user pane.

Storage is a JSON file (default ``.state/pending_decisions.json``)
written atomically via tmp-file + ``os.replace``. Missing file is
treated as an empty register. Malformed JSON raises ``ValueError`` —
callers that want to keep working through corruption should catch it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional

# Repo root resolution mirrors tools/journal_append.py — keeps the
# default store path stable regardless of the caller's cwd (the
# dispatcher runs from ``.dispatcher/`` and uses relative paths).
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = _REPO_ROOT / ".state" / "pending_decisions.json"

ResolutionKind = Literal["to_user", "to_worker"]
Status = Literal["pending", "resolved", "escalated"]

_KIND_TO_STATUS: dict[str, Status] = {
    "to_user": "escalated",
    "to_worker": "resolved",
}

# Statuses that ``resolve`` can transition *from*. ``to_user`` only accepts
# fresh ``pending`` entries (Secretary just received the request from the
# worker). ``to_worker`` accepts either ``pending`` (Secretary skipped the
# explicit user-relay step) or ``escalated`` (canonical flow:
# pending → escalated when user is informed → resolved when worker is told).
_KIND_TO_ELIGIBLE_STATUSES: dict[str, tuple[Status, ...]] = {
    "to_user": ("pending",),
    "to_worker": ("pending", "escalated"),
}

# Statuses considered "open" — neither terminal nor in steady-state at
# the user's hands. Only ``pending`` qualifies: ``escalated`` means the
# Secretary has already done its half (relayed to user) and the entry
# is now waiting on the human, which we cannot distinguish from
# "Secretary forgot to relay back" without a separate user-replied
# marker. So ``escalated`` is intentionally **not** an alarm trigger
# for relay-gap detection (would produce false positives whenever a
# human takes >15 min to reply).
_OPEN_STATUSES: tuple[Status, ...] = ("pending",)

_VALID_STATUSES: frozenset[str] = frozenset({"pending", "escalated", "resolved"})


@dataclass
class PendingDecision:
    task_id: str
    received_at: str
    raw_message: str
    status: Status = "pending"
    resolved_at: Optional[str] = None
    resolution_kind: Optional[ResolutionKind] = None
    # ISO timestamp recorded when the user replies to this escalated
    # entry (Issue #301). Lets the dispatcher's Step 5.1 (a-2) path
    # deterministically detect the "Secretary forgot to relay user's
    # answer back to worker" direction without proxy heuristics. None
    # for legacy entries written before #301 — those fall back to the
    # snapshot-diff proxy path (a-3).
    user_replied_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PendingDecision":
        return cls(
            task_id=data["task_id"],
            received_at=data["received_at"],
            raw_message=data["raw_message"],
            status=data.get("status", "pending"),
            resolved_at=data.get("resolved_at"),
            resolution_kind=data.get("resolution_kind"),
            user_replied_at=data.get("user_replied_at"),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(store_path: Path) -> list[PendingDecision]:
    if not store_path.exists():
        return []
    try:
        raw = store_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"failed to read {store_path}: {exc}") from exc
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"malformed JSON in {store_path}: {exc.msg} at line {exc.lineno}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(
            f"malformed register in {store_path}: top-level must be a list"
        )
    out: list[PendingDecision] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(
                f"malformed register in {store_path}: entry[{i}] is not a dict"
            )
        try:
            decoded = PendingDecision.from_dict(entry)
        except KeyError as exc:
            raise ValueError(
                f"malformed register in {store_path}: entry[{i}] missing {exc}"
            ) from exc
        if decoded.status not in _VALID_STATUSES:
            raise ValueError(
                f"malformed register in {store_path}: entry[{i}] has "
                f"unknown status {decoded.status!r}; expected one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        out.append(decoded)
    return out


def _atomic_write(store_path: Path, entries: list[PendingDecision]) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = store_path.with_name(store_path.name + ".tmp")
    payload = json.dumps(
        [e.to_dict() for e in entries],
        ensure_ascii=False,
        indent=2,
    )
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, store_path)


def append(
    task_id: str,
    raw_message: str,
    store_path: Path = DEFAULT_PATH,
) -> PendingDecision:
    """Add a new pending entry, or return the existing pending one.

    Idempotent: if ``task_id`` already has a ``pending`` entry, returns
    that entry without modifying the store. Existing ``resolved`` /
    ``escalated`` entries for the same ``task_id`` are ignored (a fresh
    judgment-request can re-open).
    """
    entries = _load(store_path)
    for entry in entries:
        if entry.task_id == task_id and entry.status == "pending":
            return entry
    new_entry = PendingDecision(
        task_id=task_id,
        received_at=_now_iso(),
        raw_message=raw_message,
        status="pending",
    )
    entries.append(new_entry)
    _atomic_write(store_path, entries)
    return new_entry


def resolve(
    task_id: str,
    kind: ResolutionKind,
    store_path: Path = DEFAULT_PATH,
) -> Optional[PendingDecision]:
    """Advance the oldest open entry for ``task_id`` along the lifecycle.

    Lifecycle: ``pending`` → ``escalated`` (relayed to user) →
    ``resolved`` (relayed user's answer back to worker).

    * ``kind="to_user"`` advances ``pending`` → ``escalated``. Only
      pending entries are eligible.
    * ``kind="to_worker"`` advances ``pending`` or ``escalated`` →
      ``resolved``. Either step is a valid terminal transition (the
      Secretary may skip the explicit ``to_user`` step in trivial cases
      where it inlines the relay-back).

    Returns the updated entry, or ``None`` if no eligible entry exists
    for ``task_id`` (no-op). When multiple eligible entries exist
    (rare), the one with the oldest ``received_at`` is advanced.
    """
    if kind not in _KIND_TO_STATUS:
        raise ValueError(f"unknown kind {kind!r}; expected to_user|to_worker")
    eligible = _KIND_TO_ELIGIBLE_STATUSES[kind]
    entries = _load(store_path)
    target_index: Optional[int] = None
    for i, entry in enumerate(entries):
        if entry.task_id != task_id or entry.status not in eligible:
            continue
        if target_index is None:
            target_index = i
            continue
        # Multiple eligible entries (rare): keep the oldest received_at.
        if entry.received_at < entries[target_index].received_at:
            target_index = i
    if target_index is None:
        return None
    target = entries[target_index]
    target.status = _KIND_TO_STATUS[kind]
    target.resolved_at = _now_iso()
    target.resolution_kind = kind
    _atomic_write(store_path, entries)
    return target


def mark_user_replied(
    task_id: str,
    store_path: Path = DEFAULT_PATH,
) -> Optional[PendingDecision]:
    """Record that the user has replied to the oldest ``escalated`` entry.

    Issue #301. Sets ``user_replied_at`` on the oldest ``escalated``
    entry for ``task_id`` (status stays ``escalated``). The dispatcher's
    Step 5.1 (a-2) path uses this marker to deterministically detect
    the "Secretary forgot to relay user's answer back to worker"
    direction without proxy heuristics.

    Idempotent: if the matching entry already has ``user_replied_at``
    set, returns it unchanged (no rewrite). Returns ``None`` when no
    ``escalated`` entry exists for ``task_id`` — Secretary should call
    ``resolve --kind to_user`` first.
    """
    entries = _load(store_path)
    # Prefer the oldest escalated entry whose user_replied_at is still
    # unset. If all are already set, fall back to the oldest escalated
    # so callers see an idempotent no-op (Codex round 1 Major: a stale
    # escalated entry with marker set must not shadow a newer one that
    # actually needs marking after a re-open via append()).
    target_index: Optional[int] = None
    fallback_index: Optional[int] = None
    for i, entry in enumerate(entries):
        if entry.task_id != task_id or entry.status != "escalated":
            continue
        if entry.user_replied_at is None:
            if target_index is None or entry.received_at < entries[target_index].received_at:
                target_index = i
        else:
            if fallback_index is None or entry.received_at < entries[fallback_index].received_at:
                fallback_index = i
    if target_index is not None:
        target = entries[target_index]
        target.user_replied_at = _now_iso()
        _atomic_write(store_path, entries)
        return target
    if fallback_index is not None:
        # All escalated entries already marked — idempotent no-op,
        # return the oldest so callers can observe the prior marker.
        return entries[fallback_index]
    return None


def list_pending(store_path: Path = DEFAULT_PATH) -> list[PendingDecision]:
    """Return entries with status ``pending``.

    ``escalated`` entries are awaiting human reply and are deliberately
    excluded — they're in the user's hands, not Secretary's, and surface
    as an alarm would produce false positives whenever the human takes
    a while to answer. ``resolved`` is terminal and also excluded.
    """
    return [e for e in _load(store_path) if e.status in _OPEN_STATUSES]


def list_pending_older_than(
    threshold_minutes: int,
    store_path: Path = DEFAULT_PATH,
    now: Optional[datetime] = None,
) -> list[PendingDecision]:
    """``pending`` entries whose ``received_at`` is older than ``threshold_minutes``.

    Used by the dispatcher's Step 5.1 (a-0) for the (a)(1) direction
    (Secretary forgot to relay worker's question to user). The (a)(2)
    direction (Secretary forgot to relay user's answer back to worker)
    is intentionally not caught here — the register has no signal for
    "user has replied" so escalated-too-long would fire on every slow
    human answer. (a)(2) coverage requires a richer schema (e.g. a
    ``user_replied_at`` marker) and is left for a follow-up Issue.

    ``now`` is injectable for tests; defaults to ``datetime.now(UTC)``.
    Entries with an unparseable ``received_at`` are surfaced (treated as
    arbitrarily old) rather than silently skipped — silent drop would
    create a relay-gap false negative against the fallback contract in
    ``.dispatcher/CLAUDE.md`` Step 5.1.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=threshold_minutes)
    out: list[PendingDecision] = []
    for entry in _load(store_path):
        if entry.status not in _OPEN_STATUSES:
            continue
        try:
            received = datetime.strptime(
                entry.received_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            # Unparseable timestamp: surface the entry as a relay-gap
            # candidate (loud failure beats silent suppression).
            out.append(entry)
            continue
        if received <= cutoff:
            out.append(entry)
    return out


def list_escalated_user_replied_older_than(
    threshold_minutes: int,
    store_path: Path = DEFAULT_PATH,
    now: Optional[datetime] = None,
) -> list[PendingDecision]:
    """``escalated`` entries with ``user_replied_at`` older than threshold.

    Issue #301. Drives the dispatcher's Step 5.1 (a-2) deterministic
    path: when a user has replied (user_replied_at is set) but Secretary
    has not yet forwarded back to the worker (status still ``escalated``,
    resolved_at unset), the elapsed time since the user reply is the
    Secretary's relay-gap window.

    Entries with a malformed ``user_replied_at`` are surfaced (treated
    as arbitrarily old) — same loud-failure stance as
    :func:`list_pending_older_than`.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=threshold_minutes)
    out: list[PendingDecision] = []
    for entry in _load(store_path):
        if entry.status != "escalated":
            # status=="resolved" means Secretary already forwarded the
            # answer back to the worker — terminal, no relay-gap.
            continue
        if entry.user_replied_at is None:
            continue
        try:
            replied = datetime.strptime(
                entry.user_replied_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            out.append(entry)
            continue
        if replied <= cutoff:
            out.append(entry)
    return out


# --------------------------------------------------------------------- CLI


def _print_entry(entry: PendingDecision) -> None:
    print(json.dumps(entry.to_dict(), ensure_ascii=False))


def _cmd_append(args: argparse.Namespace) -> int:
    store_path = Path(args.store) if args.store else DEFAULT_PATH
    entry = append(args.task_id, args.message, store_path=store_path)
    _print_entry(entry)
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    store_path = Path(args.store) if args.store else DEFAULT_PATH
    entry = resolve(args.task_id, args.kind, store_path=store_path)
    if entry is None:
        print(json.dumps({"status": "no_pending", "task_id": args.task_id}))
        return 0
    _print_entry(entry)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store_path = Path(args.store) if args.store else DEFAULT_PATH
    if args.user_replied_older_than_min is not None:
        entries: Iterable[PendingDecision] = list_escalated_user_replied_older_than(
            args.user_replied_older_than_min, store_path=store_path
        )
    elif args.older_than_min is not None:
        entries = list_pending_older_than(
            args.older_than_min, store_path=store_path
        )
    else:
        entries = list_pending(store_path=store_path)
    for entry in entries:
        _print_entry(entry)
    return 0


def _cmd_mark_user_replied(args: argparse.Namespace) -> int:
    store_path = Path(args.store) if args.store else DEFAULT_PATH
    entry = mark_user_replied(args.task_id, store_path=store_path)
    if entry is None:
        print(json.dumps({"status": "no_escalated", "task_id": args.task_id}))
        return 0
    _print_entry(entry)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/pending_decisions.py",
        description="Secretary pending-decisions register (Issue #297).",
    )
    parser.add_argument(
        "--store",
        help=f"override store path (default: {DEFAULT_PATH})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_append = sub.add_parser("append", help="add a new pending entry (idempotent)")
    p_append.add_argument("--task-id", required=True)
    p_append.add_argument("--message", required=True)
    p_append.set_defaults(func=_cmd_append)

    p_resolve = sub.add_parser("resolve", help="resolve a pending entry")
    p_resolve.add_argument("--task-id", required=True)
    p_resolve.add_argument(
        "--kind", required=True, choices=["to_user", "to_worker"]
    )
    p_resolve.set_defaults(func=_cmd_resolve)

    p_list = sub.add_parser("list", help="list pending entries")
    p_list.add_argument(
        "--older-than-min",
        type=int,
        default=None,
        help="only pending entries whose received_at is older than N minutes",
    )
    p_list.add_argument(
        "--user-replied-older-than-min",
        type=int,
        default=None,
        help=(
            "only escalated entries whose user_replied_at is older than N "
            "minutes (Issue #301, dispatcher Step 5.1 (a-2) deterministic path)"
        ),
    )
    p_list.set_defaults(func=_cmd_list)

    p_mark = sub.add_parser(
        "mark-user-replied",
        help="record that the user has replied to an escalated entry (Issue #301)",
    )
    p_mark.add_argument("--task-id", required=True)
    p_mark.set_defaults(func=_cmd_mark_user_replied)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
