"""Unit tests for tools/resolve_worker_layout.py.

Covers Issue #283 Stage 1 acceptance:
- All 3 patterns (A new / A reuse / B / C ephemeral / C gitignored)
- All 3 roles (default / claude-org-self-edit / doc-audit)
- planned_branch inference (feat / fix / explicit override / null for Pattern C)
- registry-not-found → Pattern C ephemeral
- runs.status='queued' counts as active reservation (B-1 contract)
- ``runs JOIN worker_dirs`` driven decision (B-2)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import resolve_worker_layout as rwl  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


class _Sandbox:
    """One self-contained claude-org-like tree for a single test."""

    def __init__(self, td: Path):
        self.root = td
        self.workers = td / "workers"
        self.workers.mkdir()
        # claude-org repo skeleton lives in <td>/claude-org/
        self.claude_org_root = td / "claude-org"
        self.claude_org_root.mkdir()
        (self.claude_org_root / ".state").mkdir()
        (self.claude_org_root / "registry").mkdir()
        # workers_dir relative to claude-org root → ../workers
        (self.claude_org_root / "registry" / "org-config.md").write_text(
            "## Workers Directory\nworkers_dir: ../workers\n",
            encoding="utf-8",
        )
        self.db_path = self.claude_org_root / ".state" / "state.db"
        self.db_path.touch()  # let connect() open it (apply_schema below)
        conn = connect(self.db_path)
        apply_schema(conn)
        conn.close()

    # ---- registry helpers --------------------------------------------------

    def write_registry(self, rows: list[tuple[str, str, str, str]]) -> None:
        """Each row: (通称, slug, path, description)."""
        lines = [
            "# Projects Registry",
            "",
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |",
            "|---|---|---|---|---|",
        ]
        for common, slug, path, desc in rows:
            lines.append(f"| {common} | {slug} | {path} | {desc} | - |")
        (self.claude_org_root / "registry" / "projects.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    # ---- state.db helpers --------------------------------------------------

    def add_run(
        self,
        *,
        task_id: str,
        project_slug: str,
        pattern: str,
        status: str,
        worker_dir_abs: str,
        title: str = "",
    ) -> None:
        conn = connect(self.db_path)
        # NOTE: pass claude_org_root=False-equivalent; we don't want post-commit
        # snapshotter to fire during fixture setup.
        w = StateWriter(conn)
        with w.transaction() as tx:
            tx.register_worker_dir(abs_path=worker_dir_abs, layout="flat",
                                   is_git_repo=True, is_worktree=(pattern == "B"))
            tx.upsert_run(
                task_id=task_id,
                project_slug=project_slug,
                pattern=pattern,
                title=title or task_id,
                status=status,
                worker_dir_abs_path=worker_dir_abs,
            )
        conn.close()


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


class TestPatternDetection(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_pattern_a_new_when_no_runs(self):
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        layout = rwl.resolve(
            task_id="clock-task-1",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "A")
        self.assertIsNone(layout.pattern_variant)
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "clock-app").resolve(),
        )
        self.assertEqual(layout.role, "default")
        self.assertFalse(layout.self_edit)
        self.assertEqual(layout.planned_branch, "feat/clock-task-1")

    def test_pattern_a_reuse_when_only_completed_runs(self):
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        self.sb.add_run(
            task_id="clock-prev",
            project_slug="clock-app",
            pattern="A",
            status="completed",
            worker_dir_abs=str(self.sb.workers / "clock-app"),
        )
        layout = rwl.resolve(
            task_id="clock-task-2",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "A")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "clock-app").resolve(),
        )

    def test_pattern_b_when_in_use_run_exists(self):
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        self.sb.add_run(
            task_id="clock-other",
            project_slug="clock-app",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.sb.workers / "clock-app"),
        )
        layout = rwl.resolve(
            task_id="clock-task-3",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "clock-app" / ".worktrees" / "clock-task-3").resolve(),
        )
        self.assertEqual(layout.planned_branch, "feat/clock-task-3")

    def test_pattern_b_when_queued_run_exists(self):
        """B-1 contract: runs.status='queued' is an active reservation."""
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        self.sb.add_run(
            task_id="clock-pending",
            project_slug="clock-app",
            pattern="A",
            status="queued",
            worker_dir_abs=str(self.sb.workers / "clock-app"),
        )
        layout = rwl.resolve(
            task_id="clock-task-q",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")

    def test_pattern_b_when_review_run_exists(self):
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        self.sb.add_run(
            task_id="clock-prev-rev",
            project_slug="clock-app",
            pattern="A",
            status="review",
            worker_dir_abs=str(self.sb.workers / "clock-app"),
        )
        layout = rwl.resolve(
            task_id="clock-task-r",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")

    def test_pattern_c_ephemeral_when_slug_not_in_registry(self):
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        layout = rwl.resolve(
            task_id="adhoc-survey",
            project_slug="not-in-registry",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "C")
        self.assertEqual(layout.pattern_variant, "ephemeral")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "adhoc-survey").resolve(),
        )
        self.assertIsNone(layout.planned_branch)


# ---------------------------------------------------------------------------
# Pattern C gitignored sub-mode (Step 0.7)
# ---------------------------------------------------------------------------


class TestPatternCGitignored(unittest.TestCase):
    """Requires `git` on PATH. Skipped when unavailable."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise unittest.SkipTest("git not available")

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        # Set up a real local repo that has tmp/secret.txt gitignored.
        self.local_repo = Path(self._td.name) / "local-repo"
        self.local_repo.mkdir()
        subprocess.run(
            ["git", "-C", str(self.local_repo), "init", "-q"],
            check=True,
        )
        (self.local_repo / ".gitignore").write_text("tmp/\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_gitignored_target_forces_pattern_c_repo_root(self):
        self.sb.write_registry(
            [("ローカルアプリ", "local-app", str(self.local_repo), "Local repo demo")]
        )
        layout = rwl.resolve(
            task_id="cleanup-tmp",
            project_slug="local-app",
            targets=["tmp/secret.txt"],
            description="cleanup gitignored notes",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "C")
        self.assertEqual(layout.pattern_variant, "gitignored_repo_root")
        self.assertEqual(
            Path(layout.worker_dir).resolve(),
            self.local_repo.resolve(),
        )
        self.assertIsNone(layout.planned_branch)

    def test_tracked_target_falls_through_to_normal_pattern_judgment(self):
        # tracked file is NOT in tmp/ so check-ignore returns 1.
        self.sb.write_registry(
            [("ローカルアプリ", "local-app", str(self.local_repo), "Local repo demo")]
        )
        layout = rwl.resolve(
            task_id="edit-readme",
            project_slug="local-app",
            targets=["README.md"],
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "A")
        self.assertIsNone(layout.pattern_variant)


# ---------------------------------------------------------------------------
# Role / mode detection
# ---------------------------------------------------------------------------


class TestRoleDetection(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        # Register the sandbox claude-org root as a project so role detection
        # has something to compare against.
        self.sb.write_registry(
            [
                ("時計", "clock-app", "-", "Demo clock"),
                (
                    "claude-org-ja",
                    "claude-org-ja",
                    str(self.sb.claude_org_root),
                    "claude-org self",
                ),
            ]
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_default_role_for_non_claude_org_edit(self):
        layout = rwl.resolve(
            task_id="t-1",
            project_slug="clock-app",
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.role, "default")
        self.assertFalse(layout.self_edit)

    def test_claude_org_self_edit_role_when_editing_claude_org(self):
        layout = rwl.resolve(
            task_id="t-2",
            project_slug="claude-org-ja",
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.role, "claude-org-self-edit")
        self.assertTrue(layout.self_edit)

    def test_audit_mode_forces_doc_audit_even_for_claude_org(self):
        layout = rwl.resolve(
            task_id="t-3",
            project_slug="claude-org-ja",
            mode="audit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.role, "doc-audit")
        self.assertFalse(layout.self_edit)

    def test_audit_mode_for_non_claude_org_also_doc_audit(self):
        layout = rwl.resolve(
            task_id="t-4",
            project_slug="clock-app",
            mode="audit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.role, "doc-audit")


# ---------------------------------------------------------------------------
# Branch inference
# ---------------------------------------------------------------------------


class TestBranchInference(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        self.sb.write_registry([("時計", "clock-app", "-", "")])

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resolve(self, **kw):
        defaults = dict(
            task_id="task-x",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        defaults.update(kw)
        return rwl.resolve(**defaults)

    def test_feat_prefix_when_description_has_no_fix_words(self):
        layout = self._resolve(description="add a new sparkline component")
        self.assertEqual(layout.planned_branch, "feat/task-x")

    def test_fix_prefix_for_english_bug_word(self):
        layout = self._resolve(description="bug in clock tick handler")
        self.assertEqual(layout.planned_branch, "fix/task-x")

    def test_fix_prefix_for_japanese_word(self):
        layout = self._resolve(description="ロード時の修正")
        self.assertEqual(layout.planned_branch, "fix/task-x")

    def test_branch_override_wins(self):
        layout = self._resolve(
            description="bug bug bug",
            branch_override="release/2026-05",
        )
        self.assertEqual(layout.planned_branch, "release/2026-05")

    def test_pattern_c_branch_is_null(self):
        # unregistered slug → Pattern C → no branch
        layout = self._resolve(project_slug="totally-new")
        self.assertEqual(layout.pattern, "C")
        self.assertIsNone(layout.planned_branch)


# ---------------------------------------------------------------------------
# Settings args & CLI smoke test
# ---------------------------------------------------------------------------


class TestSettingsArgs(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        self.sb.write_registry([("時計", "clock-app", "-", "")])

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_settings_args_carries_all_required_keys(self):
        layout = rwl.resolve(
            task_id="t-5",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        args = layout.settings_args
        self.assertEqual(set(args), {"role", "worker-dir", "claude-org-path", "out"})
        self.assertEqual(args["role"], layout.role)
        self.assertEqual(args["worker-dir"], layout.worker_dir)
        self.assertTrue(args["out"].endswith(os.path.join(".claude", "settings.local.json")))


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        self.sb.write_registry([("時計", "clock-app", "-", "")])

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_cli_emits_valid_json(self):
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = rwl.main([
                "--task-id", "cli-test",
                "--project-slug", "clock-app",
                "--claude-org-root", str(self.sb.claude_org_root),
                "--state-db-path", str(self.sb.db_path),
            ])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["pattern"], "A")
        self.assertEqual(data["role"], "default")
        self.assertEqual(data["planned_branch"], "feat/cli-test")
        self.assertIn("settings_args", data)

    def test_cli_invalid_mode_returns_2(self):
        # argparse rejects choices and exits 2; we route through SystemExit.
        with self.assertRaises(SystemExit) as cm:
            rwl.main([
                "--task-id", "x",
                "--project-slug", "clock-app",
                "--mode", "delete",
                "--claude-org-root", str(self.sb.claude_org_root),
            ])
        self.assertEqual(cm.exception.code, 2)


# ---------------------------------------------------------------------------
# Issue #289: Pattern B live_repo_worktree variant for claude-org self-edit
# ---------------------------------------------------------------------------


class TestPatternBLiveRepoWorktree(unittest.TestCase):
    """Issue #289: claude-org self-edit Pattern B places the worktree under
    Secretary's live repo (claude_org_root/.worktrees/{task_id}/) rather than
    {workers_dir}/{project_slug}/.worktrees/."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        self.sb.write_registry(
            [
                ("時計", "clock-app", "-", "Demo clock"),
                (
                    "claude-org-ja",
                    "claude-org-ja",
                    str(self.sb.claude_org_root),
                    "claude-org self",
                ),
            ]
        )
        # An active run on claude-org-ja forces Pattern B for the next task.
        self.sb.add_run(
            task_id="self-edit-prev",
            project_slug="claude-org-ja",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.sb.claude_org_root),
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_pattern_b_self_edit_auto_selects_live_repo_worktree(self):
        """Pattern B + role=claude-org-self-edit + variant unset →
        variant auto-selected to 'live_repo_worktree' and worker_dir under
        claude_org_root/.worktrees/."""
        layout = rwl.resolve(
            task_id="self-edit-task",
            project_slug="claude-org-ja",
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.role, "claude-org-self-edit")
        self.assertTrue(layout.self_edit)
        self.assertEqual(layout.pattern_variant, "live_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.claude_org_root / ".worktrees" / "self-edit-task").resolve(),
        )

    def test_pattern_b_default_role_keeps_null_variant_and_workers_dir(self):
        """Pattern B + role=default → variant stays None and worker_dir is
        the conventional {workers_dir}/{project_slug}/.worktrees/ path."""
        # Add an active run on clock-app so Pattern B fires for the new task.
        self.sb.add_run(
            task_id="clock-prev",
            project_slug="clock-app",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.sb.workers / "clock-app"),
        )
        layout = rwl.resolve(
            task_id="clock-next",
            project_slug="clock-app",
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.role, "default")
        self.assertIsNone(layout.pattern_variant)
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "clock-app" / ".worktrees" / "clock-next").resolve(),
        )

    def test_inconsistent_role_self_edit_combo_is_rejected(self):
        """Codex Round 3 Major regression: role and self_edit must agree.
        role='default' + self_edit=true would otherwise let the coherence
        pass relocate the worktree under claude_org_root while the
        settings generator still emits non-self-edit permissions."""
        with self.assertRaises(rwl.ResolveError):
            rwl.resolve(
                task_id="bad-combo",
                project_slug="clock-app",
                mode="edit",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={"role": "default", "self_edit": True},
            )

    def test_role_only_override_to_self_edit_re_derives_variant_and_worker_dir(self):
        """Codex Round 1 Major regression: passing ONLY
        layout_overrides={'role': 'claude-org-self-edit'} on a Pattern B
        layout must promote pattern_variant to 'live_repo_worktree' and
        relocate worker_dir to {claude_org_root}/.worktrees/. Otherwise
        the resolver would emit an incoherent layout (role=self_edit but
        worker_dir under {workers_dir}/{project_slug}/.worktrees/)."""
        # Active run on clock-app forces Pattern B for the new task; the
        # auto-resolved role would be 'default' (clock-app != claude-org).
        self.sb.add_run(
            task_id="clock-prev",
            project_slug="clock-app",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.sb.workers / "clock-app"),
        )
        layout = rwl.resolve(
            task_id="role-promoted",
            project_slug="clock-app",
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={"role": "claude-org-self-edit"},
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.role, "claude-org-self-edit")
        self.assertTrue(layout.self_edit)
        self.assertEqual(layout.pattern_variant, "live_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.claude_org_root / ".worktrees" / "role-promoted").resolve(),
        )

    def test_explicit_variant_via_layout_overrides_resets_worker_dir(self):
        """layout_overrides supplying pattern=B + variant='live_repo_worktree'
        without an explicit worker_dir → resolver re-derives worker_dir under
        claude_org_root/.worktrees/."""
        layout = rwl.resolve(
            task_id="explicit-self-edit",
            project_slug="clock-app",  # not claude-org, so auto-derive picks default role
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={
                "pattern": "B",
                "pattern_variant": "live_repo_worktree",
                "role": "claude-org-self-edit",
            },
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "live_repo_worktree")
        self.assertEqual(layout.role, "claude-org-self-edit")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.claude_org_root / ".worktrees" / "explicit-self-edit").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
