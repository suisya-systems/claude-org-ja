"""Tests for tools/state_migrate.py."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import state_migrate
from tools.state_migrate import (
    CURRENT_SET_C_VERSION,
    Migration,
    detect_json_version,
    find_pending_migrations,
    main,
)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class TestStateMigrate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name)
        (self.repo_root / ".state").mkdir()

        self._original_migrations = list(state_migrate.MIGRATIONS)
        self.addCleanup(self._restore_migrations)

        self._original_current_versions = dict(state_migrate.CURRENT_JSON_VERSIONS)
        self.addCleanup(self._restore_current_versions)

        self._original_max_passes = state_migrate.MAX_MIGRATION_PASSES
        self.addCleanup(self._restore_max_passes)

    def _restore_migrations(self) -> None:
        state_migrate.MIGRATIONS[:] = self._original_migrations

    def _restore_current_versions(self) -> None:
        state_migrate.CURRENT_JSON_VERSIONS.clear()
        state_migrate.CURRENT_JSON_VERSIONS.update(self._original_current_versions)

    def _restore_max_passes(self) -> None:
        state_migrate.MAX_MIGRATION_PASSES = self._original_max_passes

    def _run_main(self, *args: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--repo-root", str(self.repo_root), *args])
        return rc, buf.getvalue()

    def test_empty_registry_is_noop(self) -> None:
        rc, out = self._run_main()
        self.assertEqual(rc, 0)
        self.assertIn("No pending migrations", out)
        self.assertIn(f"version: {CURRENT_SET_C_VERSION}", out)

    def test_detect_json_version_reads_version_field(self) -> None:
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 1, "foo": "bar"})
        self.assertEqual(detect_json_version(p), 1)

    def test_detect_json_version_missing_file(self) -> None:
        self.assertIsNone(detect_json_version(self.repo_root / "missing.json"))

    def test_detect_json_version_no_version_field(self) -> None:
        p = self.repo_root / "no-ver.json"
        write_json(p, {"foo": "bar"})
        self.assertIsNone(detect_json_version(p))

    def test_detect_json_version_malformed(self) -> None:
        p = self.repo_root / "bad.json"
        p.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(detect_json_version(p))

    def test_stub_migration_applied(self) -> None:
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 0, "payload": "old"})

        def bump(path: Path) -> None:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["version"] = 1
            data["migrated"] = True
            path.write_text(json.dumps(data), encoding="utf-8")

        state_migrate.MIGRATIONS.append(
            Migration(
                file_pattern=".state/org-state.json",
                from_version=0,
                to_version=1,
                apply=bump,
            )
        )

        rc, _ = self._run_main()
        self.assertEqual(rc, 0)
        after = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(after["version"], 1)
        self.assertTrue(after["migrated"])

    def test_dry_run_does_not_modify(self) -> None:
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 0})

        def bump(path: Path) -> None:
            path.write_text(json.dumps({"version": 1}), encoding="utf-8")

        state_migrate.MIGRATIONS.append(
            Migration(
                file_pattern=".state/org-state.json",
                from_version=0,
                to_version=1,
                apply=bump,
            )
        )

        rc, out = self._run_main("--dry-run")
        self.assertEqual(rc, 0)
        after = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(after, {"version": 0})
        self.assertIn("Pending migrations", out)
        # Dry-run must never claim it applied anything.
        self.assertNotIn("Applied", out)

    def test_idempotent_with_empty_registry(self) -> None:
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 1})
        self._run_main()
        self._run_main()
        after = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(after, {"version": 1})

    def test_multi_step_chain_runs_to_completion(self) -> None:
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 0})

        def bump_to(target: int):
            def _apply(path: Path) -> None:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["version"] = target
                path.write_text(json.dumps(data), encoding="utf-8")

            return _apply

        state_migrate.MIGRATIONS.extend(
            [
                Migration(".state/org-state.json", 0, 1, bump_to(1)),
                Migration(".state/org-state.json", 1, 2, bump_to(2)),
            ]
        )
        state_migrate.CURRENT_JSON_VERSIONS[".state/org-state.json"] = 2

        rc, _ = self._run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["version"], 2)

    def test_unsupported_version_fails(self) -> None:
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 99})
        state_migrate.CURRENT_JSON_VERSIONS[".state/org-state.json"] = 1

        rc, out = self._run_main()
        self.assertEqual(rc, 1)
        self.assertIn("unsupported schema versions", out)

    def test_overlapping_migrations_do_not_double_apply(self) -> None:
        """Two migrations matching the same file at the same from_version must not stack in one pass."""
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 0, "applies": []})

        def make_apply(label: str):
            def _apply(path: Path) -> None:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["version"] = 1
                data["applies"].append(label)
                path.write_text(json.dumps(data), encoding="utf-8")

            return _apply

        state_migrate.MIGRATIONS.extend(
            [
                Migration(".state/org-state.json", 0, 1, make_apply("specific")),
                Migration(".state/*.json", 0, 1, make_apply("glob")),
            ]
        )
        state_migrate.CURRENT_JSON_VERSIONS[".state/org-state.json"] = 1

        rc, _ = self._run_main()
        self.assertEqual(rc, 0)
        after = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(after["version"], 1)
        self.assertEqual(len(after["applies"]), 1)

    def test_runaway_migration_loop_caught(self) -> None:
        """A migration that fails to advance the version must not loop forever."""
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 0})

        state_migrate.MIGRATIONS.append(
            Migration(".state/org-state.json", 0, 1, lambda _path: None)
        )
        state_migrate.MAX_MIGRATION_PASSES = 3

        rc, out = self._run_main()
        self.assertEqual(rc, 2)
        self.assertIn("exceeded", out)

    def test_find_pending_skips_already_current(self) -> None:
        p = self.repo_root / ".state" / "org-state.json"
        write_json(p, {"version": 1})

        state_migrate.MIGRATIONS.append(
            Migration(
                file_pattern=".state/org-state.json",
                from_version=0,
                to_version=1,
                apply=lambda _: None,
            )
        )

        pending = find_pending_migrations(self.repo_root)
        self.assertEqual(pending, [])


if __name__ == "__main__":
    unittest.main()
