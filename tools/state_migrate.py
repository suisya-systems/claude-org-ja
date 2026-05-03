#!/usr/bin/env python3
"""Central migration entry point per Set C §4.4 ratification.

Runs on /org-resume and on first-read of an old-version file. Today registers
zero migrations (Set C is at version 1, the initial state). The framework is
in place so future migrations can register themselves without per-reader
scattered version logic.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Set C contract version (bumped on any state-schema change).
CURRENT_SET_C_VERSION = 1

# Per-file current versions for JSON files (matches the version field in each).
CURRENT_JSON_VERSIONS = {
    ".state/org-state.json": 1,
}


@dataclass
class Migration:
    """A single per-file migration step."""

    file_pattern: str  # glob relative to repo root
    from_version: int
    to_version: int
    apply: Callable[[Path], None]

    @property
    def description(self) -> str:
        return f"{self.file_pattern}: v{self.from_version} -> v{self.to_version}"


# Migration registry. Empty today.
MIGRATIONS: list[Migration] = []


def detect_json_version(path: Path) -> int | None:
    """Read the `version` field from a JSON file. Return None if missing or unreadable."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("version")


def find_pending_migrations(repo_root: Path) -> list[tuple[Migration, Path]]:
    """Walk the migration registry and return (migration, file) pairs that apply."""
    pending: list[tuple[Migration, Path]] = []
    for migration in MIGRATIONS:
        for path in repo_root.glob(migration.file_pattern):
            current = detect_json_version(path)
            if current is None:
                continue
            if current == migration.from_version:
                pending.append((migration, path))
    return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="State schema migration entry point.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen but do not modify files",
    )
    args = parser.parse_args(argv)

    pending = find_pending_migrations(args.repo_root)

    if not pending:
        print(f"No pending migrations. Set C version: {CURRENT_SET_C_VERSION}")
        return 0

    print(f"Found {len(pending)} pending migrations.")
    for migration, path in pending:
        print(f"  {migration.description} ({path})")
        if not args.dry_run:
            migration.apply(path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
