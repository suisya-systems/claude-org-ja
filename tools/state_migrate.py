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
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("version")


def find_pending_migrations(repo_root: Path) -> list[tuple[Migration, Path]]:
    """Walk the migration registry and return (migration, file) pairs that apply now.

    Single-pass: returns only migrations whose `from_version` matches the file's
    current version. Multi-step chains (v0->v1->v2) are handled by `main()`,
    which re-scans after each apply pass until no further migrations apply.
    """
    pending: list[tuple[Migration, Path]] = []
    for migration in MIGRATIONS:
        for path in repo_root.glob(migration.file_pattern):
            current = detect_json_version(path)
            if current is None:
                continue
            if current == migration.from_version:
                pending.append((migration, path))
    return pending


def find_unsupported_files(repo_root: Path) -> list[tuple[Path, int | None, int]]:
    """Return JSON files whose version != the expected current version.

    These represent unmigratable state (no registered migration covers the gap).
    Treating this as success would let `/org-resume` and first-read silently
    consume stale data, so callers should fail loudly.
    """
    out: list[tuple[Path, int | None, int]] = []
    for rel, expected in CURRENT_JSON_VERSIONS.items():
        for path in repo_root.glob(rel):
            current = detect_json_version(path)
            if current != expected:
                out.append((path, current, expected))
    return out


# Safety bound on the migration loop to prevent infinite chains (mis-registered
# migration that does not advance the file's version).
MAX_MIGRATION_PASSES = 64


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

    if args.dry_run:
        # Dry-run is informational: report what *would* apply on the first
        # pass plus any files stuck at an unsupported version. Always exits 0
        # so it can be wired into status checks without becoming a blocker.
        pending = find_pending_migrations(args.repo_root)
        unsupported = find_unsupported_files(args.repo_root)
        if pending:
            print(f"Pending migrations ({len(pending)}):")
            for migration, path in pending:
                print(f"  {migration.description} ({path})")
        if unsupported:
            print("Files at unsupported schema versions (no migration registered):")
            for path, current, expected in unsupported:
                print(f"  {path}: version={current!r}, expected={expected}")
        if not pending and not unsupported:
            print(f"No pending migrations. Set C version: {CURRENT_SET_C_VERSION}")
        return 0

    total_applied = 0
    for _ in range(MAX_MIGRATION_PASSES):
        pending = find_pending_migrations(args.repo_root)
        if not pending:
            break
        for migration, path in pending:
            print(f"  {migration.description} ({path})")
            migration.apply(path)
            total_applied += 1
    else:
        print(
            f"ERROR: migration loop exceeded {MAX_MIGRATION_PASSES} passes — "
            "a registered migration likely fails to advance its file's version."
        )
        return 2

    if total_applied:
        print(f"Applied {total_applied} migration step(s).")

    unsupported = find_unsupported_files(args.repo_root)
    if unsupported:
        print("ERROR: files at unsupported schema versions remain:")
        for path, current, expected in unsupported:
            print(f"  {path}: version={current!r}, expected={expected}")
        return 1

    if not total_applied:
        print(f"No pending migrations. Set C version: {CURRENT_SET_C_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
