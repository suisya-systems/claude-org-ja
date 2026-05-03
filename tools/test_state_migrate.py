"""Tests for tools/state_migrate.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import state_migrate
from tools.state_migrate import (
    CURRENT_SET_C_VERSION,
    Migration,
    detect_json_version,
    find_pending_migrations,
    main,
)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    (tmp_path / ".state").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def restore_registry():
    original = list(state_migrate.MIGRATIONS)
    yield
    state_migrate.MIGRATIONS[:] = original


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_empty_registry_is_noop(repo_root: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["--repo-root", str(repo_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No pending migrations" in out
    assert f"version: {CURRENT_SET_C_VERSION}" in out


def test_detect_json_version_reads_version_field(repo_root: Path) -> None:
    p = repo_root / ".state" / "org-state.json"
    write_json(p, {"version": 1, "foo": "bar"})
    assert detect_json_version(p) == 1


def test_detect_json_version_missing_file(repo_root: Path) -> None:
    assert detect_json_version(repo_root / "missing.json") is None


def test_detect_json_version_no_version_field(repo_root: Path) -> None:
    p = repo_root / "no-ver.json"
    write_json(p, {"foo": "bar"})
    assert detect_json_version(p) is None


def test_detect_json_version_malformed(repo_root: Path) -> None:
    p = repo_root / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    assert detect_json_version(p) is None


def test_stub_migration_applied(repo_root: Path) -> None:
    p = repo_root / ".state" / "org-state.json"
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

    rc = main(["--repo-root", str(repo_root)])
    assert rc == 0
    after = json.loads(p.read_text(encoding="utf-8"))
    assert after["version"] == 1
    assert after["migrated"] is True


def test_dry_run_does_not_modify(repo_root: Path) -> None:
    p = repo_root / ".state" / "org-state.json"
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

    pending_before = find_pending_migrations(repo_root)
    assert len(pending_before) == 1

    rc = main(["--repo-root", str(repo_root), "--dry-run"])
    assert rc == 0
    after = json.loads(p.read_text(encoding="utf-8"))
    assert after == {"version": 0}


def test_idempotent_with_empty_registry(repo_root: Path) -> None:
    p = repo_root / ".state" / "org-state.json"
    write_json(p, {"version": 1})
    main(["--repo-root", str(repo_root)])
    main(["--repo-root", str(repo_root)])
    after = json.loads(p.read_text(encoding="utf-8"))
    assert after == {"version": 1}


def test_find_pending_skips_already_current(repo_root: Path) -> None:
    p = repo_root / ".state" / "org-state.json"
    write_json(p, {"version": 1})

    state_migrate.MIGRATIONS.append(
        Migration(
            file_pattern=".state/org-state.json",
            from_version=0,
            to_version=1,
            apply=lambda _: None,
        )
    )

    pending = find_pending_migrations(repo_root)
    assert pending == []
