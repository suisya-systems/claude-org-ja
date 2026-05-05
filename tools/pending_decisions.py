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

# Statuses considered "open" for the purpose of relay-gap detection. The
# dispatcher's Step 5.1 (a-0) lookup must surface anything not yet
# terminally relayed back to the worker, otherwise the (a)(2) direction
# (user answered but Secretary forgot to relay) is invisible.
_OPEN_STATUSES: tuple[Status, ...] = ("pending", "escalated")


@dataclass
class PendingDecision:
    task_id: str
    received_at: str
    raw_message: str
    status: Status = "pending"
    resolved_at: Optional[str] = None
    resolution_kind: Optional[ResolutionKind] = None

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
            out.append(PendingDecision.from_dict(entry))
        except KeyError as exc:
            raise ValueError(
                f"malformed register in {store_path}: entry[{i}] missing {exc}"
            ) from exc
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
    """Resolve the oldest pending entry for ``task_id``.

    ``kind="to_user"`` marks it ``escalated`` (Secretary relayed to
    user). ``kind="to_worker"`` marks it ``resolved`` (Secretary
    relayed user's answer back to worker). Returns the updated entry,
    or ``None`` if no pending entry exists for ``task_id`` (no-op).
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


def list_pending(store_path: Path = DEFAULT_PATH) -> list[PendingDecision]:
    """Return all entries that are not yet terminally resolved.

    Includes both ``pending`` (received but not yet relayed to user) and
    ``escalated`` (relayed to user, awaiting answer to relay back to
    worker). Excludes ``resolved`` (terminal). The function name keeps
    "pending" for API-spec continuity (Issue #297) but its semantics
    cover any open relay-gap candidate.
    """
    return [e for e in _load(store_path) if e.status in _OPEN_STATUSES]


def list_pending_older_than(
    threshold_minutes: int,
    store_path: Path = DEFAULT_PATH,
    now: Optional[datetime] = None,
) -> list[PendingDecision]:
    """Open entries whose ``received_at`` is older than ``threshold_minutes``.

    "Open" = ``pending`` or ``escalated`` (see :func:`list_pending`). The
    dispatcher uses this for SECRETARY_RELAY_GAP_SUSPECTED detection,
    which must catch both:

    * (a)(1) ``pending`` too long — Secretary forgot to relay to user
    * (a)(2) ``escalated`` too long — Secretary forgot to relay user's
      answer back to worker

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
    if args.older_than_min is not None:
        entries: Iterable[PendingDecision] = list_pending_older_than(
            args.older_than_min, store_path=store_path
        )
    else:
        entries = list_pending(store_path=store_path)
    for entry in entries:
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
        help="only entries whose received_at is older than N minutes",
    )
    p_list.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
