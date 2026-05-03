#!/usr/bin/env python3
"""One-shot sweep: move closed worker state files into .state/workers/archive/.

Used to bulk-clean accumulated stale `.state/workers/worker-*.md` files (Issue #264).

Two modes:

* **safe (default)** — archive only workers whose task id appears in
  `.state/org-state.md` Active Work Items with status `COMPLETED` or `ABANDONED`.
  `REVIEW` is treated as live (pane is still open, human approval pending).

* **--include-orphans** — additionally archive workers whose task id does NOT
  appear in org-state.md at all (assumed long-rotated-out, the typical Issue #264
  cause). Orphans whose md was modified within `--orphan-min-age-days` (default 7)
  are kept untouched as a safety guard against archiving still-live workers whose
  org-state entry got out of sync.

Usage:
    py -3 tools/sweep_stale_workers.py [--dry-run] [--include-orphans]
                                       [--orphan-min-age-days N] [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path


TERMINAL_STATUSES = {"COMPLETED", "ABANDONED"}


def parse_work_item_statuses(org_state_md: Path) -> dict[str, str]:
    """Return {task_id: STATUS} for every Active Work Item line. Empty if file missing."""
    if not org_state_md.exists():
        return {}
    text = org_state_md.read_text(encoding="utf-8").replace("\r\n", "\n")
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^- ([\w-]+):\s*.+?\s*\[(\w+)\]", line)
        if m:
            out[m.group(1)] = m.group(2).upper()
    return out


def task_id_from_worker_md(text: str, fallback: str) -> str:
    for line in text.splitlines():
        m = re.match(r"^Task:\s*(\S+)", line)
        if m:
            return m.group(1).strip()
    return fallback


def classify(task_id: str, work_items: dict[str, str], age_days: float, min_age: float):
    """Return ('archive' | 'keep', reason)."""
    status = work_items.get(task_id)
    if status in TERMINAL_STATUSES:
        return "archive", f"work item is {status}"
    if status is not None:
        # Present in org-state but not terminal (IN_PROGRESS / REVIEW / BLOCKED / PENDING).
        return "keep", f"work item is {status} (still live)"
    # Orphan: not in org-state at all.
    if age_days < min_age:
        return "keep", f"orphan but too recent ({age_days:.1f}d < {min_age:.1f}d)"
    return "archive-orphan", f"orphan, age {age_days:.1f}d"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include-orphans", action="store_true",
                   help="Also archive workers whose task id is absent from org-state.md.")
    p.add_argument("--orphan-min-age-days", type=float, default=7.0,
                   help="Skip orphans modified within this many days (default 7).")
    p.add_argument("--repo-root", type=Path, default=Path(__file__).parent.parent)
    args = p.parse_args()

    workers_dir = args.repo_root / ".state" / "workers"
    archive_dir = workers_dir / "archive"
    org_state = args.repo_root / ".state" / "org-state.md"

    if not workers_dir.exists():
        print(f"no workers dir: {workers_dir}", file=sys.stderr)
        return 1

    work_items = parse_work_item_statuses(org_state)
    print(f"work items in org-state: {len(work_items)}")

    now = time.time()
    moved = kept = 0
    for md in sorted(workers_dir.glob("worker-*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        fallback = md.stem.replace("worker-", "")
        task_id = task_id_from_worker_md(text, fallback)
        age_days = (now - md.stat().st_mtime) / 86400.0

        action, reason = classify(task_id, work_items, age_days, args.orphan_min_age_days)
        if action == "archive-orphan" and not args.include_orphans:
            action = "keep"
            reason = f"{reason} (pass --include-orphans to archive)"

        if action == "keep":
            kept += 1
            print(f"keep    : {md.name} (task={task_id}; {reason})")
            continue

        target = archive_dir / md.name
        if args.dry_run:
            print(f"[dry-run] archive: {md.name} (task={task_id}; {reason})")
        else:
            archive_dir.mkdir(parents=True, exist_ok=True)
            md.rename(target)
            print(f"archived: {md.name} (task={task_id}; {reason})")
        moved += 1

    print(f"\nsummary: {'would-move' if args.dry_run else 'moved'}={moved} kept={kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
