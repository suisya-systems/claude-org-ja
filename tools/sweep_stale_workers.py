#!/usr/bin/env python3
"""One-shot sweep: move closed worker state files into .state/workers/archive/.

Used to bulk-clean accumulated stale `.state/workers/worker-*.md` files (Issue #264).
A worker is considered live iff its task id appears in `.state/org-state.md` Work Items
with status NOT in {COMPLETED, ABANDONED, REVIEW}; everything else is archived.

Usage:
    py -3 tools/sweep_stale_workers.py [--dry-run] [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


DONE_STATUSES = {"COMPLETED", "ABANDONED", "REVIEW"}


def parse_live_task_ids(org_state_md: Path) -> set[str]:
    if not org_state_md.exists():
        return set()
    text = org_state_md.read_text(encoding="utf-8").replace("\r\n", "\n")
    live: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^- ([\w-]+):\s*.+?\s*\[(\w+)\]", line)
        if m and m.group(2).upper() not in DONE_STATUSES:
            live.add(m.group(1))
    return live


def task_id_from_worker_md(text: str, fallback: str) -> str:
    for line in text.splitlines():
        m = re.match(r"^Task:\s*(\S+)", line)
        if m:
            return m.group(1).strip()
    return fallback


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--repo-root", type=Path, default=Path(__file__).parent.parent)
    args = p.parse_args()

    workers_dir = args.repo_root / ".state" / "workers"
    archive_dir = workers_dir / "archive"
    org_state = args.repo_root / ".state" / "org-state.md"

    if not workers_dir.exists():
        print(f"no workers dir: {workers_dir}", file=sys.stderr)
        return 1

    live = parse_live_task_ids(org_state)
    print(f"live task ids ({len(live)}): {sorted(live) if live else '(none)'}")

    moved = kept = 0
    for md in sorted(workers_dir.glob("worker-*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        fallback = md.stem.replace("worker-", "")
        task_id = task_id_from_worker_md(text, fallback)
        if task_id in live:
            kept += 1
            continue
        target = archive_dir / md.name
        if args.dry_run:
            print(f"[dry-run] archive: {md.name} (task={task_id})")
        else:
            archive_dir.mkdir(parents=True, exist_ok=True)
            md.rename(target)
            print(f"archived: {md.name} (task={task_id})")
        moved += 1

    print(f"\nsummary: moved={moved} kept_live={kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
