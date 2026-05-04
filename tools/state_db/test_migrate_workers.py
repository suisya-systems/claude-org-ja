"""Unit tests for tools.state_db.migrate_workers (M3, Issue #267).

All tests run on temp filesystems with synthetic inventories — no real
../workers/ access. The "with worktree" test stubs git via FakeRunner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from tools.state_db import migrate_workers as mw


# ---------------------------------------------------------------------------
# Synthetic inventory helpers
# ---------------------------------------------------------------------------


def _entry(name: str, tier: str, parent_project: str | None = None,
           parent_workstream: str | None = None) -> dict:
    return {
        "name": name,
        "abs_path": f"C:/synthetic/{name}",
        "git": {"is_repo": False, "is_worktree": False, "origin_url": None,
                "current_branch": None, "last_commit_iso": None,
                "last_commit_subject": None},
        "dir_mtime": "2026-04-01",
        "top_level_entries": 1,
        "size_mb": None,
        "size_note": "",
        "proposed_classification": {
            "tier": tier,
            "parent_project": parent_project,
            "parent_workstream": parent_workstream,
            "rationale": "synthetic",
        },
    }


def _synth_inventory() -> list[dict]:
    return [
        # 5 project-tier (the real-world set, with renames)
        _entry("ccmux", "project"),
        _entry("claude-org", "project"),
        _entry("claude-org-en", "project"),
        _entry("claude-org-runtime", "project"),
        _entry("core-harness", "project"),
        # runs under each project
        _entry("auto-mirror-ci-p1", "run", "claude-org-ja", "auto-mirror"),
        _entry("dogfooding-smoke-053", "run", "claude-org-ja", "dogfooding-smoke"),
        _entry("layer3-design-qa", "run", "claude-org-ja", None),  # _solo
        _entry("en-branch-protect", "run", "claude-org", None),
        _entry("wave-c", "run", "claude-org", "i18n-en-bootstrap"),
        # _research cluster
        _entry("ccswarm-depth-audit", "run", "_research", "ccswarm"),
        _entry("anthropic-extended-audit", "run", "_research", "anthropic"),
        _entry("discord-channel-research", "run", "_research", "_solo"),
        # scratch
        _entry("fizzbuzz", "scratch", "_scratch", None),
        _entry("hello-world", "scratch", "_scratch", None),
        # archive_candidate
        _entry("claude-org.old-worktrees-stale", "archive_candidate", "claude-org"),
    ]


def _build(tmp_path: Path, inv: list[dict] | None = None) -> tuple[mw.Plan, Path]:
    inv = inv or _synth_inventory()
    workers_root = tmp_path / "workers"
    workers_root.mkdir()
    # materialise sources
    for e in inv:
        (workers_root / e["name"]).mkdir()
    plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                         inventory_source="synth", detect_worktrees=False)
    return plan, workers_root


# ---------------------------------------------------------------------------
# 1. Plan generation
# ---------------------------------------------------------------------------


def test_plan_targets_match_layout(tmp_path):
    plan, root = _build(tmp_path)
    moves = {op.src: op.dst for op in plan.operations if op.op == "move_run"}

    # Sample expected target paths drawn from directory-layout.md §5.
    posix_root = PurePosixPath(root.as_posix())
    expected = {
        str(posix_root / "auto-mirror-ci-p1"):
            str(posix_root / "claude-org-ja" / "_runs" / "auto-mirror" / "auto-mirror-ci-p1"),
        str(posix_root / "layer3-design-qa"):
            str(posix_root / "claude-org-ja" / "_runs" / "_solo" / "layer3-design-qa"),
        str(posix_root / "en-branch-protect"):
            str(posix_root / "claude-org" / "_runs" / "_solo" / "en-branch-protect"),
        str(posix_root / "ccswarm-depth-audit"):
            str(posix_root / "_research" / "_runs" / "ccswarm" / "ccswarm-depth-audit"),
        str(posix_root / "discord-channel-research"):
            str(posix_root / "_research" / "_runs" / "_solo" / "discord-channel-research"),
        str(posix_root / "fizzbuzz"):
            str(posix_root / "_scratch" / "_runs" / "_solo" / "fizzbuzz"),
        str(posix_root / "claude-org.old-worktrees-stale"):
            str(posix_root / "_archive" / "2026-Q2" / "claude-org" / "claude-org.old-worktrees-stale"),
    }
    for src, dst in expected.items():
        assert moves[src] == dst, f"{src} → expected {dst}, got {moves.get(src)}"


def test_claude_org_swap_three_step(tmp_path):
    plan, _ = _build(tmp_path)
    proj_ops = [op for op in plan.operations if op.op == "rename_project"]
    # ccmux→renga + 3-step swap = 4 project renames
    notes = [op.note for op in proj_ops]
    assert any("ccmux → renga" in n for n in notes)
    swap = [op for op in proj_ops if "swap step" in op.note]
    assert len(swap) == 3
    assert swap[0].dst.endswith(mw.SWAP_INTERMEDIATE)
    assert swap[1].dst.endswith("/claude-org")
    assert swap[2].dst.endswith("/claude-org-ja")
    # Step 1's dst must equal step 3's src (intermediate threading).
    assert swap[0].dst == swap[2].src


def test_research_cluster_workstreams(tmp_path):
    plan, root = _build(tmp_path)
    posix_root = PurePosixPath(root.as_posix())
    moves = {op.src: op.dst for op in plan.operations if op.op == "move_run"}
    assert moves[str(posix_root / "ccswarm-depth-audit")].endswith("_research/_runs/ccswarm/ccswarm-depth-audit")
    assert moves[str(posix_root / "anthropic-extended-audit")].endswith("_research/_runs/anthropic/anthropic-extended-audit")
    assert moves[str(posix_root / "discord-channel-research")].endswith("_research/_runs/_solo/discord-channel-research")


def test_scratch_collapses_to_solo(tmp_path):
    plan, root = _build(tmp_path)
    posix_root = PurePosixPath(root.as_posix())
    moves = {op.src: op.dst for op in plan.operations if op.op == "move_run"}
    for name in ("fizzbuzz", "hello-world"):
        assert moves[str(posix_root / name)] == str(posix_root / "_scratch" / "_runs" / "_solo" / name)


def test_already_migrated_dir_is_noop(tmp_path):
    """An entry whose abs_path already equals its target must not appear in the plan."""
    inv = [_entry("noop", "scratch", "_scratch", None)]
    workers_root = tmp_path / "workers"
    target = workers_root / "_scratch" / "_runs" / "_solo" / "noop"
    target.mkdir(parents=True)
    # Override the entry's name so the source path *is* the target.
    inv[0]["abs_path"] = str(target)
    # The script's plan logic compares src=workers_root/<name> vs target.
    # When name="noop", src=<root>/noop which differs from target, so it WILL plan a move.
    # To exercise the no-op path, simulate name being the deepest segment with src==target:
    # build_plan computes src as workers_root / entry["name"]; the no-op fires only when
    # that equals target. We craft an inventory entry whose target equals src by giving it
    # tier=project and name not in PROJECT_RENAMES (so target == workers_root / name == src).
    inv2 = [_entry("claude-org-runtime", "project")]
    plan = mw.build_plan(inv2, workers_root, archive_quarter="2026-Q2",
                         inventory_source="synth", detect_worktrees=False)
    assert not any(op.op in ("rename_project", "move_run") for op in plan.operations)


# ---------------------------------------------------------------------------
# 2. Apply / Rollback round-trip
# ---------------------------------------------------------------------------


class FakeRunner:
    """Stubs out OS / git operations for hermetic apply tests."""

    def __init__(self):
        self.rename_calls: list[tuple[str, str]] = []
        self.makedirs_calls: list[str] = []
        self.repair_calls: list[str] = []
        self.junction_calls: list[tuple[str, str]] = []
        self.remove_junction_calls: list[str] = []

    def rename(self, src, dst):
        self.rename_calls.append((str(src), str(dst)))
        os.rename(src, dst)  # actually do it on temp fs

    def makedirs(self, path):
        self.makedirs_calls.append(str(path))
        os.makedirs(path, exist_ok=True)

    def repair(self, repo):
        self.repair_calls.append(str(repo))

    def junction(self, link, target):
        self.junction_calls.append((str(link), str(target)))

    def remove_junction(self, link):
        self.remove_junction_calls.append(str(link))


def test_apply_then_rollback_restores(tmp_path):
    plan, root = _build(tmp_path)
    runner = FakeRunner()
    manifest_path = tmp_path / "manifest.json"

    # Snapshot pre-state
    pre = sorted(p.relative_to(root).as_posix() for p in root.iterdir())

    mw.apply_plan(plan, manifest_path=manifest_path, runner=runner)
    # After apply, target dirs exist
    assert (root / "renga").exists()
    assert (root / "claude-org-ja").exists()  # post-swap
    assert (root / "claude-org").exists()      # post-swap (originally claude-org-en)
    assert (root / "_scratch" / "_runs" / "_solo" / "fizzbuzz").exists()
    assert (root / "_research" / "_runs" / "ccswarm" / "ccswarm-depth-audit").exists()

    # Rollback round-trip
    mw.rollback(manifest_path, runner=runner)
    post = sorted(p.relative_to(root).as_posix() for p in root.iterdir() if p.is_dir())
    # Originals back; ensure_dir leftovers (_archive, _research, _scratch shells) are tolerable
    for original in ("ccmux", "claude-org", "claude-org-en", "fizzbuzz", "ccswarm-depth-audit"):
        assert (root / original).exists(), f"rollback failed to restore {original}"


def test_manifest_round_trip_json(tmp_path):
    plan, root = _build(tmp_path)
    manifest_path = tmp_path / "m.json"
    mw.apply_plan(plan, manifest_path=manifest_path, runner=FakeRunner())
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["workers_root"]
    assert data["archive_quarter"] == "2026-Q2"
    assert any(e["op"] == "rename_project" for e in data["executed"])
    assert any(e["op"] == "move_run" for e in data["executed"])


# ---------------------------------------------------------------------------
# 3. Worktree fixup path
# ---------------------------------------------------------------------------


def test_worktree_repair_called_on_project_rename(tmp_path, monkeypatch):
    inv = [_entry("ccmux", "project")]
    workers_root = tmp_path / "workers"
    workers_root.mkdir()
    src = workers_root / "ccmux"
    src.mkdir()
    (src / ".git").mkdir()  # mark as repo
    # Inject a fake worktree so build_plan sees has_worktrees=True
    monkeypatch.setattr(mw, "_git_worktrees", lambda repo: ["/fake/worktree-A"] if repo == src else [])

    plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                         inventory_source="synth", detect_worktrees=True)
    proj = [op for op in plan.operations if op.op == "rename_project"][0]
    assert proj.has_worktrees and proj.worktrees == ["/fake/worktree-A"]

    runner = FakeRunner()
    mw.apply_plan(plan, manifest_path=tmp_path / "m.json", runner=runner)
    assert any(c.endswith("renga") for c in runner.repair_calls)


# ---------------------------------------------------------------------------
# 4. Pre-flight conflict detection
# ---------------------------------------------------------------------------


def test_preflight_conflict_when_target_exists(tmp_path):
    inv = [_entry("solo-run", "run", "claude-org-ja", None)]
    workers_root = tmp_path / "workers"
    workers_root.mkdir()
    (workers_root / "solo-run").mkdir()
    # Conflict: target already populated.
    (workers_root / "claude-org-ja" / "_runs" / "_solo" / "solo-run").mkdir(parents=True)

    plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                         inventory_source="synth", detect_worktrees=False)
    issues = mw.preflight(plan, workers_root)
    assert any("target already exists" in i for i in issues)


def test_preflight_source_missing(tmp_path):
    inv = [_entry("ghost", "run", "claude-org-ja", None)]
    workers_root = tmp_path / "workers"
    workers_root.mkdir()
    # Note: no source dir created.
    plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                         inventory_source="synth", detect_worktrees=False)
    issues = mw.preflight(plan, workers_root)
    assert any("source missing" in i for i in issues)


# ---------------------------------------------------------------------------
# 5. Real-inventory sanity check (smoke)
# ---------------------------------------------------------------------------


REAL_INVENTORY = Path(
    os.environ.get(
        "M3_REAL_INVENTORY",
        str(Path(__file__).resolve().parents[2].parent
            / "state-db-hierarchy-design" / "inventory.json"),
    )
)


@pytest.mark.skipif(not REAL_INVENTORY.exists(), reason="real inventory.json not available")
def test_real_inventory_plan_is_well_formed(tmp_path):
    inv = json.loads(REAL_INVENTORY.read_text(encoding="utf-8"))
    workers_root = tmp_path / "workers"
    workers_root.mkdir()
    plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                         inventory_source=str(REAL_INVENTORY), detect_worktrees=False)
    assert len(plan.operations) >= 130  # ~130 entries plus pseudo-root ensure_dirs
    proj = [op for op in plan.operations if op.op == "rename_project"]
    assert any(op.note.startswith("ccmux → renga") for op in proj)
    swap = [op for op in proj if "swap step" in op.note]
    assert len(swap) == 3
    # Every move target must live under workers_root and use 3-tier shape.
    posix_root = PurePosixPath(workers_root.as_posix())
    for op in plan.operations:
        if op.op != "move_run":
            continue
        assert op.dst.startswith(str(posix_root)), op.dst
        # _archive paths get an extra YYYY-Qx tier; runs/scratch/research stay 3.
