"""Unit tests for tools.state_db.migrate_workers (M3, Issue #267).

All tests run on temp filesystems with synthetic inventories — no real
../workers/ access. The "with worktree" test stubs git via FakeRunner.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from tools.state_db import migrate_workers as mw


# ---------------------------------------------------------------------------
# Synthetic inventory helpers
# ---------------------------------------------------------------------------


def _entry(name, tier, parent_project=None, parent_workstream=None):
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


def _synth_inventory():
    return [
        # 5 project-tier (the real-world set, with renames)
        _entry("ccmux", "project"),
        _entry("claude-org", "project"),
        _entry("claude-org-en", "project"),
        _entry("claude-org-runtime", "project"),
        _entry("core-harness", "project"),
        # runs
        _entry("auto-mirror-ci-p1", "run", "claude-org-ja", "auto-mirror"),
        _entry("dogfooding-smoke-053", "run", "claude-org-ja", "dogfooding-smoke"),
        _entry("layer3-design-qa", "run", "claude-org-ja", None),  # _solo
        _entry("en-branch-protect", "run", "claude-org", None),
        _entry("wave-c", "run", "claude-org", "i18n-en-bootstrap"),
        # _research
        _entry("ccswarm-depth-audit", "run", "_research", "ccswarm"),
        _entry("anthropic-extended-audit", "run", "_research", "anthropic"),
        _entry("discord-channel-research", "run", "_research", "_solo"),
        # scratch
        _entry("fizzbuzz", "scratch", "_scratch", None),
        _entry("hello-world", "scratch", "_scratch", None),
        # archive_candidate
        _entry("claude-org.old-worktrees-stale", "archive_candidate", "claude-org"),
    ]


def _build(workers_root, inv=None):
    inv = inv or _synth_inventory()
    workers_root.mkdir(exist_ok=True)
    for e in inv:
        (workers_root / e["name"]).mkdir(exist_ok=True)
    plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                         inventory_source="synth", detect_worktrees=False)
    return plan


# ---------------------------------------------------------------------------
# FakeRunner
# ---------------------------------------------------------------------------


class FakeRunner:
    def __init__(self):
        self.rename_calls = []
        self.makedirs_calls = []
        self.repair_calls = []
        self.junction_calls = []
        self.remove_junction_calls = []

    def rename(self, src, dst):
        self.rename_calls.append((str(src), str(dst)))
        os.rename(src, dst)

    def makedirs(self, path):
        self.makedirs_calls.append(str(path))
        os.makedirs(path, exist_ok=True)

    def repair(self, repo):
        self.repair_calls.append(str(repo))

    def junction(self, link, target):
        self.junction_calls.append((str(link), str(target)))

    def remove_junction(self, link):
        self.remove_junction_calls.append(str(link))


# ---------------------------------------------------------------------------
# 1. Plan generation
# ---------------------------------------------------------------------------


class TestPlanGeneration(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "workers"
        self.plan = _build(self.root)

    def tearDown(self):
        self._td.cleanup()

    def test_plan_targets_match_layout(self):
        moves = {op.src: op.dst for op in self.plan.operations if op.op == "move_run"}
        posix_root = PurePosixPath(self.root.as_posix())
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
            self.assertEqual(moves[src], dst)

    def test_claude_org_swap_three_step(self):
        proj_ops = [op for op in self.plan.operations if op.op == "rename_project"]
        notes = [op.note for op in proj_ops]
        self.assertTrue(any("ccmux → renga" in n for n in notes))
        swap = [op for op in proj_ops if "swap step" in op.note]
        self.assertEqual(len(swap), 3)
        self.assertTrue(swap[0].dst.endswith(mw.SWAP_INTERMEDIATE))
        self.assertTrue(swap[1].dst.endswith("/claude-org"))
        self.assertTrue(swap[2].dst.endswith("/claude-org-ja"))
        self.assertEqual(swap[0].dst, swap[2].src)

    def test_research_cluster_workstreams(self):
        posix_root = PurePosixPath(self.root.as_posix())
        moves = {op.src: op.dst for op in self.plan.operations if op.op == "move_run"}
        self.assertTrue(moves[str(posix_root / "ccswarm-depth-audit")].endswith(
            "_research/_runs/ccswarm/ccswarm-depth-audit"))
        self.assertTrue(moves[str(posix_root / "anthropic-extended-audit")].endswith(
            "_research/_runs/anthropic/anthropic-extended-audit"))
        self.assertTrue(moves[str(posix_root / "discord-channel-research")].endswith(
            "_research/_runs/_solo/discord-channel-research"))

    def test_scratch_collapses_to_solo(self):
        posix_root = PurePosixPath(self.root.as_posix())
        moves = {op.src: op.dst for op in self.plan.operations if op.op == "move_run"}
        for name in ("fizzbuzz", "hello-world"):
            self.assertEqual(
                moves[str(posix_root / name)],
                str(posix_root / "_scratch" / "_runs" / "_solo" / name),
            )

    def test_already_migrated_dir_is_noop(self):
        inv2 = [_entry("claude-org-runtime", "project")]
        plan = mw.build_plan(inv2, self.root, archive_quarter="2026-Q2",
                             inventory_source="synth", detect_worktrees=False)
        self.assertFalse(any(op.op in ("rename_project", "move_run") for op in plan.operations))


# ---------------------------------------------------------------------------
# 2. Apply / Rollback / Manifest
# ---------------------------------------------------------------------------


class TestApplyRollback(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "workers"
        self.plan = _build(self.root)

    def tearDown(self):
        self._td.cleanup()

    def test_apply_then_rollback_restores(self):
        runner = FakeRunner()
        manifest_path = self.tmp / "manifest.json"
        mw.apply_plan(self.plan, manifest_path=manifest_path, runner=runner)

        self.assertTrue((self.root / "renga").exists())
        self.assertTrue((self.root / "claude-org-ja").exists())
        self.assertTrue((self.root / "claude-org").exists())
        self.assertTrue((self.root / "_scratch" / "_runs" / "_solo" / "fizzbuzz").exists())
        self.assertTrue((self.root / "_research" / "_runs" / "ccswarm" / "ccswarm-depth-audit").exists())

        mw.rollback(manifest_path, runner=runner)
        for original in ("ccmux", "claude-org", "claude-org-en", "fizzbuzz", "ccswarm-depth-audit"):
            self.assertTrue((self.root / original).exists(), f"missing {original}")

    def test_manifest_round_trip_json(self):
        manifest_path = self.tmp / "m.json"
        mw.apply_plan(self.plan, manifest_path=manifest_path, runner=FakeRunner())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertTrue(data["workers_root"])
        self.assertEqual(data["archive_quarter"], "2026-Q2")
        self.assertTrue(any(e["op"] == "rename_project" for e in data["executed"]))
        self.assertTrue(any(e["op"] == "move_run" for e in data["executed"]))

    def test_replan_after_migration_is_noop(self):
        mw.apply_plan(self.plan, manifest_path=self.tmp / "m.json", runner=FakeRunner())
        plan2 = mw.build_plan(_synth_inventory(), self.root, archive_quarter="2026-Q2",
                              inventory_source="synth", detect_worktrees=False)
        move_ops = [op for op in plan2.operations if op.op in ("rename_project", "move_run")]
        self.assertEqual(move_ops, [])

    def test_incremental_manifest_survives_failure(self):
        runner = FakeRunner()
        fail_after = 3
        real_rename = runner.rename
        call_count = {"n": 0}

        def flaky_rename(src, dst):
            call_count["n"] += 1
            if call_count["n"] > fail_after:
                raise OSError("synthetic mid-batch failure")
            real_rename(src, dst)

        runner.rename = flaky_rename
        manifest_path = self.tmp / "m.json"
        with self.assertRaises(OSError):
            mw.apply_plan(self.plan, manifest_path=manifest_path, runner=runner)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("executed", data)
        self.assertGreaterEqual(len(data["executed"]), 1)

    def test_apply_from_manifest_replays_subset(self):
        plan_manifest = self.tmp / "plan.json"
        plan_manifest.write_text(
            json.dumps(mw.render_plan_manifest(self.plan), indent=2),
            encoding="utf-8",
        )
        data = json.loads(plan_manifest.read_text(encoding="utf-8"))
        data["operations"] = [
            op for op in data["operations"]
            if op["op"] == "ensure_dir" and "_scratch" in op["dst"]
            or (op["op"] == "move_run" and "tier=scratch" in op.get("note", ""))
        ]
        trimmed = self.tmp / "trimmed.json"
        trimmed.write_text(json.dumps(data, indent=2), encoding="utf-8")
        runner = FakeRunner()
        out = self.tmp / "executed.json"
        mw.apply_from_manifest(trimmed, out_manifest=out, runner=runner)
        self.assertTrue((self.root / "_scratch" / "_runs" / "_solo" / "fizzbuzz").exists())
        self.assertTrue((self.root / "ccmux").exists())
        self.assertFalse((self.root / "renga").exists())


# ---------------------------------------------------------------------------
# 3. Worktree fixup path
# ---------------------------------------------------------------------------


class TestWorktreeFixup(unittest.TestCase):

    def test_worktree_repair_called_on_project_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inv = [_entry("ccmux", "project")]
            workers_root = tmp / "workers"
            workers_root.mkdir()
            src = workers_root / "ccmux"
            src.mkdir()
            (src / ".git").mkdir()

            with mock.patch.object(
                mw, "_git_worktrees",
                side_effect=lambda repo: ["/fake/worktree-A"] if repo == src else [],
            ):
                plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                                     inventory_source="synth", detect_worktrees=True)
            proj = [op for op in plan.operations if op.op == "rename_project"][0]
            self.assertTrue(proj.has_worktrees)
            self.assertEqual(proj.worktrees, ["/fake/worktree-A"])

            runner = FakeRunner()
            mw.apply_plan(plan, manifest_path=tmp / "m.json", runner=runner)
            self.assertTrue(any(c.endswith("renga") for c in runner.repair_calls))


# ---------------------------------------------------------------------------
# 3b. SIGKILL-window rollback (round 1 review B1)
# ---------------------------------------------------------------------------


class TestSigkillWindow(unittest.TestCase):
    """Manifest must persist BEFORE rename, so rollback can recover even if
    the process is killed in the gap between rename() and the post-rename
    persist."""

    def test_rollback_handles_inprogress_after_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = tmp / "workers"
            plan = _build(root)
            runner = FakeRunner()

            # Real rename, but raise immediately after — simulating SIGKILL
            # arriving between the OS rename returning and our entry-state
            # update / persist.
            real_rename = runner.rename
            killed = {"once": False}

            def killing_rename(src, dst):
                if not killed["once"] and "fizzbuzz" in str(src):
                    killed["once"] = True
                    real_rename(src, dst)
                    raise KeyboardInterrupt("simulated SIGKILL after rename")
                real_rename(src, dst)

            runner.rename = killing_rename
            manifest_path = tmp / "m.json"
            with self.assertRaises(KeyboardInterrupt):
                mw.apply_plan(plan, manifest_path=manifest_path, runner=runner)

            # Manifest must contain an entry for fizzbuzz, even though its
            # state never reached "completed".
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            fb = [e for e in data["executed"]
                  if e.get("op") == "move_run" and "fizzbuzz" in e.get("src", "")]
            self.assertEqual(len(fb), 1, "fizzbuzz entry missing from manifest")
            self.assertEqual(fb[0]["state"], "in_progress")
            # FS state: rename DID succeed, so dst exists and src is gone.
            self.assertTrue(Path(fb[0]["dst"]).exists())
            self.assertFalse(Path(fb[0]["src"]).exists())

            # Rollback must reverse the rename despite state="in_progress".
            mw.rollback(manifest_path, runner=FakeRunner())
            self.assertTrue(Path(fb[0]["src"]).exists())
            self.assertFalse(Path(fb[0]["dst"]).exists())

    def test_rollback_skips_inprogress_that_never_renamed(self):
        """If SIGKILL hit BEFORE rename(), src still exists, dst doesn't —
        rollback should be a no-op for that entry."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            manifest = tmp / "m.json"
            (tmp / "src-only").mkdir()
            manifest.write_text(json.dumps({
                "version": 1, "workers_root": str(tmp),
                "archive_quarter": "2026-Q2", "operations": [],
                "executed": [{
                    "op": "move_run",
                    "src": str(tmp / "src-only"),
                    "dst": str(tmp / "never-renamed-here"),
                    "worktrees": [], "compat_junction": False,
                    "state": "in_progress",
                }],
            }), encoding="utf-8")
            mw.rollback(manifest, runner=FakeRunner())  # must not raise
            self.assertTrue((tmp / "src-only").exists())
            self.assertFalse((tmp / "never-renamed-here").exists())


# ---------------------------------------------------------------------------
# 4. Pre-flight conflict detection
# ---------------------------------------------------------------------------


class TestPreflight(unittest.TestCase):

    def test_preflight_conflict_when_target_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inv = [_entry("solo-run", "run", "claude-org-ja", None)]
            workers_root = tmp / "workers"
            workers_root.mkdir()
            (workers_root / "solo-run").mkdir()
            (workers_root / "claude-org-ja" / "_runs" / "_solo" / "solo-run").mkdir(parents=True)
            plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                                 inventory_source="synth", detect_worktrees=False)
            issues = mw.preflight(plan, workers_root)
            self.assertTrue(any("target already exists" in i for i in issues))

    def test_preflight_rejects_path_outside_workers_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            workers_root = tmp / "workers"
            workers_root.mkdir()
            (workers_root / "ok-src").mkdir()
            outside = tmp / "outside-root"
            outside.mkdir()
            # Build a synthetic plan whose dst escapes workers_root.
            plan = mw.Plan(
                workers_root=str(workers_root.as_posix()),
                inventory_source="hand-built",
                archive_quarter="2026-Q2",
                operations=[mw.Operation(
                    op="move_run",
                    src=str((workers_root / "ok-src").as_posix()),
                    dst=str((outside / "stolen").as_posix()),
                    note="malicious",
                )],
            )
            issues = mw.preflight(plan, workers_root)
            self.assertTrue(any("escapes workers_root" in i for i in issues))

    def test_preflight_warns_on_active_runs_in_db(self):
        from tools.state_db import apply_schema, connect
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            workers_root = tmp / "workers"
            workers_root.mkdir()
            db_path = tmp / "state.db"
            conn = connect(db_path)
            apply_schema(conn)
            # Seed a project + active in_use run.
            conn.execute(
                "INSERT INTO projects (slug, display_name) VALUES ('p', 'Project P')"
            )
            conn.execute(
                "INSERT INTO runs (task_id, project_id, pattern, title, status) "
                "VALUES ('t-1', 1, 'C', 'live', 'in_use')"
            )
            conn.commit()
            conn.close()

            plan = mw.Plan(
                workers_root=str(workers_root.as_posix()),
                inventory_source="x", archive_quarter="2026-Q2", operations=[],
            )
            issues = mw.preflight(plan, workers_root, db_path=db_path)
            self.assertTrue(any("active run" in i for i in issues))
            # --force semantics: the helper drops just the active-runs warning.
            self.assertEqual(mw._filter_overridable(issues, force=True), [])

    def test_preflight_source_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inv = [_entry("ghost", "run", "claude-org-ja", None)]
            workers_root = tmp / "workers"
            workers_root.mkdir()
            plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                                 inventory_source="synth", detect_worktrees=False)
            issues = mw.preflight(plan, workers_root)
            self.assertTrue(any("source missing" in i for i in issues))


# ---------------------------------------------------------------------------
# 5. Real-inventory smoke (skipped when env var not set / file absent)
# ---------------------------------------------------------------------------


_DEFAULT_REAL = (
    Path(__file__).resolve().parents[2].parent
    / "state-db-hierarchy-design" / "inventory.json"
)
REAL_INVENTORY = Path(os.environ.get("M3_REAL_INVENTORY", str(_DEFAULT_REAL)))


@unittest.skipUnless(REAL_INVENTORY.exists(), "real inventory.json not available")
class TestRealInventorySmoke(unittest.TestCase):

    def test_real_inventory_plan_is_well_formed(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = json.loads(REAL_INVENTORY.read_text(encoding="utf-8"))
            workers_root = Path(tmp) / "workers"
            workers_root.mkdir()
            plan = mw.build_plan(inv, workers_root, archive_quarter="2026-Q2",
                                 inventory_source=str(REAL_INVENTORY),
                                 detect_worktrees=False)
            self.assertGreaterEqual(len(plan.operations), 130)
            proj = [op for op in plan.operations if op.op == "rename_project"]
            self.assertTrue(any(op.note.startswith("ccmux → renga") for op in proj))
            swap = [op for op in proj if "swap step" in op.note]
            self.assertEqual(len(swap), 3)
            posix_root = PurePosixPath(workers_root.as_posix())
            for op in plan.operations:
                if op.op != "move_run":
                    continue
                self.assertTrue(op.dst.startswith(str(posix_root)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
