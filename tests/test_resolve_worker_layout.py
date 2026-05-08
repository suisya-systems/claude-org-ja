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

    def __init__(self, td: Path, *, with_claude_org_origin: bool = False):
        self.root = td
        self.workers = td / "workers"
        self.workers.mkdir()
        # claude-org repo skeleton lives in <td>/claude-org/
        self.claude_org_root = td / "claude-org"
        self.claude_org_root.mkdir()
        (self.claude_org_root / ".state").mkdir()
        (self.claude_org_root / "registry").mkdir()
        if with_claude_org_origin:
            self.init_git_with_origin(
                self.claude_org_root,
                "https://github.com/suisya-systems/claude-org-ja.git",
            )
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

    # ---- git helpers -------------------------------------------------------

    @staticmethod
    def init_git(repo: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)

    @classmethod
    def init_git_with_origin(cls, repo: Path, origin_url: str) -> None:
        cls.init_git(repo)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", origin_url],
            check=True,
        )

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
        # claude-org-ja self-edit detection runs off the sandbox's git origin
        # URL, so the sandbox needs ``with_claude_org_origin=True`` and no
        # registry row for the self-edit target.
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=True)
        self.sb.write_registry(
            [
                ("時計", "clock-app", "-", "Demo clock"),
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

    def test_audit_mode_for_claude_org_keeps_pattern_a_via_synthesized_project(self):
        """Regression: the production registry no longer carries a
        claude-org-ja row, but ``mode='audit'`` on this slug must still
        land on Pattern A with worker_dir under workers_dir/claude-org-ja
        (read-only audit clone) — not silently fall through to Pattern C
        ephemeral. The resolver synthesizes a virtual project entry from
        the slug + matching git origin so the legacy pattern logic keeps
        working."""
        layout = rwl.resolve(
            task_id="audit-task",
            project_slug="claude-org-ja",
            mode="audit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.role, "doc-audit")
        self.assertEqual(layout.pattern, "A")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "claude-org-ja").resolve(),
        )

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
        # Self-edit detection runs off git origin (claude-org-ja has no
        # registry row); the active-run gate on Pattern B vs A still
        # applies the same way it did when the row was present.
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=True)
        self.sb.write_registry(
            [
                ("時計", "clock-app", "-", "Demo clock"),
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


# ---------------------------------------------------------------------------
# is_claude_org_project() — git origin URL based self-edit detection
# ---------------------------------------------------------------------------


class TestIsClaudeOrgProject(unittest.TestCase):
    """Self-edit detection runs off ``git -C <claude_org_root> remote
    get-url origin`` so the claude-org-ja target has no registry row to
    leak a user-specific local path. Two signals must both hold: the
    canonical slug AND a matching origin URL."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_live_claude_org_checkout_returns_true(self):
        """(a) Inside the live claude-org-ja checkout (origin =
        ``https://github.com/suisya-systems/claude-org-ja.git``) returns
        True for the canonical slug."""
        repo = self.tmp / "live"
        repo.mkdir()
        _Sandbox.init_git_with_origin(
            repo, "https://github.com/suisya-systems/claude-org-ja.git"
        )
        self.assertTrue(rwl.is_claude_org_project("claude-org-ja", repo))

    def test_worker_dir_clone_returns_false(self):
        """(b) A worker-dir clone has origin pointing at a local filesystem
        path (no ``github.com`` segment), so detection returns False even
        for the canonical slug.

        Implementation note: we do NOT invoke ``git clone`` here. The
        contract under test is purely about the recorded ``origin`` URL —
        ``is_claude_org_project`` only reads ``git remote get-url origin``
        and rejects any value lacking a ``github.com`` segment. Driving an
        actual clone (via ``file://`` URI or plain path) is fragile on
        Windows because Git for Windows / MSYS path translation rules
        differ across installations. Instead, we initialize a repo and
        register a local-path origin directly, modeling each origin shape
        a worker-dir clone would plausibly record. Trade-off: this no
        longer asserts the exact string Git itself would write — only
        that the github-URL gate rejects each modeled local form. The
        gate's regex (no ``github.com`` segment → reject) is what guards
        the contract, so this coverage is sufficient and stable."""
        upstream = self.tmp / "upstream"
        # ``git clone`` of a local source records origin in one of these
        # shapes depending on how the source was specified. We assert that
        # ALL of them fail the github-URL gate.
        local_origin_forms = [
            upstream.as_uri(),                  # file:///... URI form
            str(upstream),                      # raw absolute path form
            upstream.as_posix(),                # forward-slash path form
        ]
        for i, origin_url in enumerate(local_origin_forms):
            with self.subTest(origin_url=origin_url):
                worker_clone = self.tmp / f"worker-{i}"
                worker_clone.mkdir()
                _Sandbox.init_git_with_origin(worker_clone, origin_url)
                self.assertFalse(
                    rwl.is_claude_org_project("claude-org-ja", worker_clone)
                )

    def test_repo_without_remote_returns_false(self):
        """(c) A fresh git repo with no ``origin`` remote returns False."""
        repo = self.tmp / "noremote"
        repo.mkdir()
        _Sandbox.init_git(repo)
        self.assertFalse(rwl.is_claude_org_project("claude-org-ja", repo))

    def test_non_self_edit_slug_returns_false_even_with_matching_origin(self):
        """Slug gate: even with a matching origin URL, a non-canonical slug
        (clock-app, renga, ...) must not be flagged as self-edit. Without
        this gate every edit task run from inside the live claude-org
        checkout would misfire as self-edit, since Secretary always
        operates from there."""
        repo = self.tmp / "live"
        repo.mkdir()
        _Sandbox.init_git_with_origin(
            repo, "https://github.com/suisya-systems/claude-org-ja.git"
        )
        self.assertFalse(rwl.is_claude_org_project("clock-app", repo))

    def test_ssh_origin_form_normalizes(self):
        """``git@github.com:suisya-systems/claude-org-ja.git`` → match."""
        repo = self.tmp / "ssh"
        repo.mkdir()
        _Sandbox.init_git_with_origin(
            repo, "git@github.com:suisya-systems/claude-org-ja.git"
        )
        self.assertTrue(rwl.is_claude_org_project("claude-org-ja", repo))

    def test_fork_origin_still_matches(self):
        """CONTRIBUTING.md documents fork-based contribution: a fork's
        ``origin`` points at the contributor's fork, not at suisya-systems.
        Self-edit detection must still fire so fork-based maintainers
        keep getting Pattern B + CLAUDE.local.md."""
        repo = self.tmp / "fork-origin"
        repo.mkdir()
        _Sandbox.init_git_with_origin(
            repo, "git@github.com:some-contributor/claude-org-ja.git"
        )
        self.assertTrue(rwl.is_claude_org_project("claude-org-ja", repo))

    def test_unrelated_github_repo_returns_false(self):
        """A different github repo (e.g. an unrelated fork) must not match."""
        repo = self.tmp / "fork"
        repo.mkdir()
        _Sandbox.init_git_with_origin(
            repo, "https://github.com/someone-else/some-other-repo.git"
        )
        self.assertFalse(rwl.is_claude_org_project("claude-org-ja", repo))


# ---------------------------------------------------------------------------
# Issue #370: Pattern B claude_org_repo_worktree variant for the claude-org mirror
# ---------------------------------------------------------------------------


class TestPatternBClaudeOrgRepoWorktree(unittest.TestCase):
    """Issue #370: the canonical claude-org mirror clone at
    ``{workers_dir}/claude-org`` is detected via origin URL (no registry
    row), and Pattern B routes worktrees under it. The registry-display
    alias ``claude-org-en`` is normalized to the canonical project slug
    ``claude-org`` at the resolve() boundary.

    Requires `git` on PATH. Skipped when unavailable."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise unittest.SkipTest("git not available")

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        self.clone = self.sb.workers / "claude-org"
        self.clone.mkdir()
        _Sandbox.init_git_with_origin(
            self.clone, "https://github.com/suisya-systems/claude-org.git"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_pattern_a_for_claude_org_slug_anchors_on_clone(self):
        layout = rwl.resolve(
            task_id="en-task-a",
            project_slug="claude-org",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "A")
        self.assertIsNone(layout.pattern_variant)
        self.assertEqual(Path(layout.worker_dir), self.clone.resolve())
        self.assertEqual(layout.planned_branch, "feat/en-task-a")

    def test_pattern_a_for_claude_org_en_slug_anchors_on_clone(self):
        """slug=claude-org-en (registry-style alias) must still land on the
        same physical clone — the resolver overrides worker_dir off the
        clone path, not the slug."""
        layout = rwl.resolve(
            task_id="en-task-en",
            project_slug="claude-org-en",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "A")
        self.assertEqual(Path(layout.worker_dir), self.clone.resolve())

    def test_pattern_b_for_claude_org_slug_emits_claude_org_repo_worktree(self):
        """Issue #370 repro 1: slug=claude-org with active concurrent run
        used to emit Pattern B with variant=None and base_repo unresolvable."""
        self.sb.add_run(
            task_id="other-en-task",
            project_slug="claude-org",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.clone),
        )
        layout = rwl.resolve(
            task_id="en-issue-159",
            project_slug="claude-org",
            description="install runtime classify",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.clone / ".worktrees" / "en-issue-159").resolve(),
        )
        self.assertEqual(layout.planned_branch, "feat/en-issue-159")
        self.assertEqual(layout.role, "default")
        self.assertFalse(layout.self_edit)

    def test_pattern_b_for_alias_slug_emits_claude_org_repo_worktree(self):
        """Issue #370 repro 2: slug=claude-org-en (registry-display alias)
        used to fall through to Pattern C ephemeral. Now normalizes to the
        canonical slug and anchors on the mirror clone with the new variant."""
        self.sb.add_run(
            task_id="other-en-task",
            project_slug="claude-org-en",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.clone),
        )
        layout = rwl.resolve(
            task_id="en-task-b-alias",
            project_slug="claude-org-en",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.clone / ".worktrees" / "en-task-b-alias").resolve(),
        )

    def test_mirror_of_preserved_when_synthesizing_from_url_path_row(self):
        """Codex Round 3 Major: when the registry row carries
        ``path=https://...`` AND ``mirror_of=<slug>``, the resolver
        re-pins onto the local clone but used to drop ``mirror_of``,
        silently turning the row into a non-mirror project. The
        first-run Pattern B short-circuit then never fired. Re-create
        the production registry shape (URL path + mirror_of) and
        verify Pattern B kicks in on the very first dispatch."""
        # Live deployment shape: URL path AND mirror_of populated.
        path = self.sb.claude_org_root / "registry" / "projects.md"
        path.write_text(
            "# Projects Registry\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | mirror_of |\n"
            "|---|---|---|---|---|---|\n"
            "| 時計アプリ | clock-app | - | Demo clock | - |  |\n"
            "| claude-org-en | claude-org | "
            "https://github.com/suisya-systems/claude-org | mirror | - | "
            "claude-org-ja |\n",
            encoding="utf-8",
        )
        layout = rwl.resolve(
            task_id="first-run-mirror",
            project_slug="claude-org",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        # No active run → without mirror_of preservation this would have
        # been Pattern A. With it preserved, the mirror_of short-circuit
        # fires and lifts the layout to Pattern B (claude_org_repo_worktree
        # variant added by the post-derive claude_org_clone re-pin).
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.clone / ".worktrees" / "first-run-mirror").resolve(),
        )

    def test_no_clone_falls_through_to_pattern_c_ephemeral(self):
        """Without a clone at workers_dir/claude-org, the slug is still
        unknown → Pattern C ephemeral (regression guard for the no-en-clone
        deployment)."""
        import shutil as _shutil
        _shutil.rmtree(self.clone)
        layout = rwl.resolve(
            task_id="en-task-no-clone",
            project_slug="claude-org",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "C")
        self.assertEqual(layout.pattern_variant, "ephemeral")

    def test_clone_with_unrelated_origin_does_not_match(self):
        """A repo at workers_dir/claude-org that happens to be an unrelated
        github repo (e.g. a typo'd clone) must not be promoted to claude-org
        mirror."""
        subprocess.run(
            ["git", "-C", str(self.clone), "remote", "set-url",
             "origin", "https://github.com/someone-else/claude-org-fork.git"],
            check=True,
        )
        layout = rwl.resolve(
            task_id="en-task-bad-origin",
            project_slug="claude-org",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "C")
        self.assertEqual(layout.pattern_variant, "ephemeral")

    def test_registry_url_path_falls_back_to_local_clone(self):
        """Live deployment regression: ``registry/projects.md`` carries
        ``| claude-org-en | claude-org | https://github.com/.../claude-org |``
        (path = remote URL). Before the fix the resolver would land on the
        registry row, see ``is_local_git_repo(URL)=False``, run state.db
        Pattern B, and emit ``variant=None`` + ``base_repo=None`` because
        the URL can't be a worktree base. With the en-clone fallback, we
        re-pin onto the local clone and tag the variant."""
        self.sb.write_registry(
            [
                ("時計", "clock-app", "-", "Demo clock"),
                (
                    "claude-org-en",
                    "claude-org",
                    "https://github.com/suisya-systems/claude-org",
                    "mirror",
                ),
            ]
        )
        self.sb.add_run(
            task_id="other-en-task",
            project_slug="claude-org",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.clone),
        )
        layout = rwl.resolve(
            task_id="en-task-live",
            project_slug="claude-org",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.clone / ".worktrees" / "en-task-live").resolve(),
        )

    def test_active_run_under_other_alias_forces_pattern_b(self):
        """Codex Major regression: alias slugs share the same physical clone,
        so an active run under ``claude-org`` must gate ``claude-org-en``
        (and vice versa) into Pattern B. Without this both would land on
        Pattern A in the same worker_dir simultaneously."""
        self.sb.add_run(
            task_id="run-under-claude-org",
            project_slug="claude-org",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(self.clone),
        )
        layout = rwl.resolve(
            task_id="incoming-en-alias",
            project_slug="claude-org-en",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.clone / ".worktrees" / "incoming-en-alias").resolve(),
        )

    def test_layout_overrides_claude_org_repo_worktree_re_derives_worker_dir(self):
        """Codex Minor regression: layout_overrides supplying pattern=B +
        variant=claude_org_repo_worktree without an explicit worker_dir must re-pin
        worker_dir to {clone}/.worktrees/{task_id}, otherwise
        gen_delegate_payload would derive base_repo from a stale path."""
        layout = rwl.resolve(
            task_id="explicit-en",
            project_slug="claude-org",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={
                "pattern": "B",
                "pattern_variant": "claude_org_repo_worktree",
            },
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.clone / ".worktrees" / "explicit-en").resolve(),
        )

    def test_layout_overrides_claude_org_repo_worktree_without_clone_raises(self):
        """When the override requests claude_org_repo_worktree but no clone exists,
        we'd silently emit an unusable layout — fail loudly instead."""
        import shutil as _shutil
        _shutil.rmtree(self.clone)
        with self.assertRaises(rwl.ResolveError):
            rwl.resolve(
                task_id="explicit-en-no-clone",
                project_slug="claude-org",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={
                    "pattern": "B",
                    "pattern_variant": "claude_org_repo_worktree",
                },
            )

    def test_unrelated_slug_does_not_match_even_with_clone_present(self):
        """Slug gate: a non-canonical slug (clock-app, renga) must not be
        promoted even when the claude-org clone is on disk."""
        layout = rwl.resolve(
            task_id="clock-task",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        # clock-app is in the registry as Pattern A under workers_dir/clock-app.
        self.assertEqual(layout.pattern, "A")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "clock-app").resolve(),
        )


# ---------------------------------------------------------------------------
# Issue #374: self-edit first-run short-circuit + mirror_of + --pattern override
# ---------------------------------------------------------------------------


class TestSelfEditFirstRunShortCircuit(unittest.TestCase):
    """Issue #374: ``role=claude-org-self-edit`` is a *repo policy* (single
    ``.git``, no two-clone sync), not a concurrency policy. Even on the
    very first dispatch (no active runs) the layout must be Pattern B +
    ``live_repo_worktree`` — not the legacy Pattern A that landed the
    brief in the live repo root.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=True)
        # No registry row for claude-org-ja (matches the production layout
        # — origin URL detection replaces the row).
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_first_run_self_edit_picks_pattern_b_live_repo_worktree(self):
        """No active run exists. Before #374 this returned Pattern A and
        wrote into ``workers_dir/claude-org-ja/`` (a separate clone),
        breaking the single-.git invariant from Issue #289."""
        layout = rwl.resolve(
            task_id="self-edit-first",
            project_slug="claude-org-ja",
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.role, "claude-org-self-edit")
        self.assertTrue(layout.self_edit)
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "live_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.claude_org_root / ".worktrees" / "self-edit-first").resolve(),
        )

    def test_first_run_audit_mode_keeps_pattern_a(self):
        """``mode='audit'`` is doc-audit, not self-edit, so the
        short-circuit must not fire — audit clones go under
        ``workers_dir/claude-org-ja/`` and behave like Pattern A."""
        layout = rwl.resolve(
            task_id="audit-first",
            project_slug="claude-org-ja",
            mode="audit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.role, "doc-audit")
        self.assertEqual(layout.pattern, "A")


class TestMirrorOfRegistryMetadata(unittest.TestCase):
    """Issue #374: a registered project with non-empty ``mirror_of`` is a
    back-port style mirror — each task is independent rather than
    accumulating on a shared branch — so worktree-per-task (Pattern B) is
    the natural default even on the very first dispatch."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise unittest.SkipTest("git not available")

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        # A real local clone for the mirror, so Pattern B has a usable base.
        self.mirror_repo = Path(self._td.name) / "mirror-repo"
        self.mirror_repo.mkdir()
        subprocess.run(
            ["git", "-C", str(self.mirror_repo), "init", "-q"], check=True
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _write_registry_with_mirror(self, mirror_of: str = "upstream-slug") -> None:
        # Hand-write the table so we can include the new 6th column without
        # adding a Sandbox helper for every column variant.
        path = self.sb.claude_org_root / "registry" / "projects.md"
        path.write_text(
            "# Projects Registry\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | mirror_of |\n"
            "|---|---|---|---|---|---|\n"
            f"| 時計 | clock-app | - | Demo clock | - |  |\n"
            f"| ミラー | my-mirror | {self.mirror_repo} | mirror | - | {mirror_of} |\n",
            encoding="utf-8",
        )

    def test_first_run_mirror_picks_pattern_b_with_clone_base(self):
        """No active run, mirror_of populated → Pattern B with worker_dir
        under the mirror project's worktrees subtree. The base for
        ``git worktree add`` is the registered local clone (gen_delegate_payload
        derives base_repo from project.path)."""
        self._write_registry_with_mirror()
        layout = rwl.resolve(
            task_id="mirror-task-1",
            project_slug="my-mirror",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "B")
        # Generic mirror — no special variant; base is the registered
        # project path, not the live claude-org repo.
        self.assertIsNone(layout.pattern_variant)
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "my-mirror" / ".worktrees" / "mirror-task-1").resolve(),
        )
        self.assertEqual(layout.planned_branch, "feat/mirror-task-1")

    def test_mirror_of_with_url_path_raises_resolve_error(self):
        """Codex Round 1 Blocker: ``mirror_of`` set on a row whose path is
        a URL (or ``-``) used to force Pattern B even though
        gen_delegate_payload could not derive a base for ``git worktree
        add``. The mismatch surfaced as ``no usable base repo`` at apply
        time, after a DB reservation. Surface the misconfiguration at
        resolve() instead, before any side effect."""
        path = self.sb.claude_org_root / "registry" / "projects.md"
        path.write_text(
            "# Projects Registry\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | mirror_of |\n"
            "|---|---|---|---|---|---|\n"
            "| ミラー | url-mirror | "
            "https://github.com/example/mirror | mirror | - | upstream |\n",
            encoding="utf-8",
        )
        with self.assertRaises(rwl.ResolveError) as cm:
            rwl.resolve(
                task_id="bad-mirror",
                project_slug="url-mirror",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
            )
        self.assertIn("mirror_of", str(cm.exception))

    def test_empty_mirror_of_keeps_legacy_pattern_a(self):
        """A project whose 6th column is empty must keep the legacy A/B/C
        decision tree — the mirror short-circuit triggers only on
        non-empty values, otherwise the column rollout would silently
        change the pattern of every existing project."""
        self._write_registry_with_mirror(mirror_of="")
        layout = rwl.resolve(
            task_id="ordinary-task",
            project_slug="my-mirror",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(layout.pattern, "A")


class TestPatternOverrideContract(unittest.TestCase):
    """Issue #374: ``layout_overrides['pattern']`` is a Secretary judgment
    override. The contract is enforced at resolve() time so invalid combos
    surface in preview rather than after a DB reservation:
      - C is always allowed
      - B requires a worktree base (registered local clone, claude-org
        mirror clone, or self-edit)
      - A is forbidden when the role is claude-org-self-edit (would dispatch
        into a separate clone, breaking Issue #289 single-.git invariant)
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=True)
        self.sb.write_registry([("時計", "clock-app", "-", "Demo clock")])

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_force_pattern_c_always_allowed(self):
        layout = rwl.resolve(
            task_id="force-c",
            project_slug="clock-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={"pattern": "C"},
        )
        self.assertEqual(layout.pattern, "C")
        self.assertIsNone(layout.planned_branch)

    def test_force_pattern_a_on_self_edit_is_rejected(self):
        """Pattern A on a self-edit slug would dispatch into
        ``workers_dir/claude-org-ja/``, voiding the live-repo single-.git
        invariant."""
        with self.assertRaises(rwl.ResolveError) as cm:
            rwl.resolve(
                task_id="bad-a",
                project_slug="claude-org-ja",
                mode="edit",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={"pattern": "A"},
            )
        self.assertIn("claude-org-self-edit", str(cm.exception))

    def test_force_pattern_a_on_self_edit_role_override_is_rejected(self):
        """When the override also flips role to self-edit (e.g.
        ``--pattern A`` plus ``[worker].role = 'claude-org-self-edit'``),
        the contract still rejects — we consult the *post-override* role
        so the bad combo can't slip in via the role channel."""
        with self.assertRaises(rwl.ResolveError):
            rwl.resolve(
                task_id="bad-a-via-role",
                project_slug="clock-app",
                mode="edit",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={
                    "pattern": "A",
                    "role": "claude-org-self-edit",
                    "self_edit": True,
                },
            )

    def test_force_pattern_b_without_local_clone_is_rejected(self):
        """Slug whose registry path is ``-`` (no clone) and not a self-edit
        target / mirror clone → Pattern B has no base, must error."""
        with self.assertRaises(rwl.ResolveError) as cm:
            rwl.resolve(
                task_id="bad-b",
                project_slug="clock-app",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={"pattern": "B"},
            )
        self.assertIn("Pattern B", str(cm.exception))

    def test_force_pattern_b_on_self_edit_succeeds(self):
        """Self-edit always has a base (the live repo), so ``--pattern B``
        is permitted and re-derives variant + worker_dir like the
        auto-resolved case."""
        layout = rwl.resolve(
            task_id="ok-b-self",
            project_slug="claude-org-ja",
            mode="edit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={"pattern": "B"},
        )
        self.assertEqual(layout.pattern, "B")
        # Coherence pass kicks in because role auto-resolves to self-edit
        # and worker_dir wasn't supplied.
        self.assertEqual(layout.pattern_variant, "live_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.claude_org_root / ".worktrees" / "ok-b-self").resolve(),
        )

    def test_force_pattern_b_with_role_default_on_self_edit_slug_rejects(self):
        """Codex Round 1 Major: ``--pattern B`` together with a role
        override flipping the slug out of self-edit dropped through the
        contract — the resolver let the layout through because the
        synthesized self-edit project record satisfied ``has_base``, but
        gen_delegate_payload (which re-reads the registry and finds no
        claude-org-ja row) ended up with ``base_repo=None``. The check
        must drop the synthesized record when the override removes the
        self-edit role."""
        with self.assertRaises(rwl.ResolveError) as cm:
            rwl.resolve(
                task_id="bad-b-via-role",
                project_slug="claude-org-ja",
                mode="edit",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={
                    "pattern": "B",
                    "role": "default",
                    "self_edit": False,
                },
            )
        self.assertIn("Pattern B", str(cm.exception))

    def test_force_pattern_b_with_claude_org_mirror_re_derives_variant(self):
        """Codex Round 2 Blocker: ``--pattern B`` on slug=claude-org used
        to lose its variant (override resets it to None) and leave
        worker_dir at the clone root, so gen_delegate_payload could not
        derive a base. Re-default the variant to claude_org_repo_worktree
        when the clone is detected, then re-derive worker_dir."""
        # Build a fresh sandbox with a real claude-org mirror clone.
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        sb = _Sandbox(Path(td.name))
        sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        clone = sb.workers / "claude-org"
        clone.mkdir()
        _Sandbox.init_git_with_origin(
            clone, "https://github.com/suisya-systems/claude-org.git"
        )
        layout = rwl.resolve(
            task_id="force-b-mirror",
            project_slug="claude-org",
            claude_org_root=sb.claude_org_root,
            state_db_path=sb.db_path,
            layout_overrides={"pattern": "B"},
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(layout.worker_dir),
            (clone / ".worktrees" / "force-b-mirror").resolve(),
        )

    def test_force_pattern_a_on_claude_org_mirror_re_derives_worker_dir(self):
        """Codex Round 2 Major: ``--pattern A`` on slug=claude-org used to
        leave worker_dir at the auto-derived ``.worktrees/<task>/`` path
        because the A re-derivation was gated on
        ``claude_org_clone is None``. Mirror branch must re-pin to the
        clone root."""
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        sb = _Sandbox(Path(td.name))
        sb.write_registry([("時計", "clock-app", "-", "Demo clock")])
        clone = sb.workers / "claude-org"
        clone.mkdir()
        _Sandbox.init_git_with_origin(
            clone, "https://github.com/suisya-systems/claude-org.git"
        )
        # Auto-derive will be Pattern A here (no active run); add one so
        # auto-derive picks B + claude_org_repo_worktree (the case where
        # the override stale-path bug used to bite).
        sb.add_run(
            task_id="other-en-task",
            project_slug="claude-org",
            pattern="A",
            status="in_use",
            worker_dir_abs=str(clone),
        )
        layout = rwl.resolve(
            task_id="force-a-mirror",
            project_slug="claude-org",
            claude_org_root=sb.claude_org_root,
            state_db_path=sb.db_path,
            layout_overrides={"pattern": "A"},
        )
        self.assertEqual(layout.pattern, "A")
        self.assertEqual(Path(layout.worker_dir), clone.resolve())

    def test_variant_live_repo_worktree_requires_self_edit_role(self):
        """Codex Round 3 Blocker: ``pattern_variant=live_repo_worktree``
        pins worker_dir + base_repo to Secretary's live claude-org repo.
        Allowing it on a non-self-edit task (e.g. ``role=default``,
        ``project_slug=clock-app``) would dispatch a clock-app worker into
        the live claude-org repo — a different project entirely. The
        override must reject this combination."""
        with self.assertRaises(rwl.ResolveError) as cm:
            rwl.resolve(
                task_id="bad-variant",
                project_slug="clock-app",
                mode="edit",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={
                    "pattern": "B",
                    "pattern_variant": "live_repo_worktree",
                    # role/self_edit explicitly NOT self-edit
                    "role": "default",
                    "self_edit": False,
                },
            )
        self.assertIn("live_repo_worktree", str(cm.exception))

    def test_variant_claude_org_repo_worktree_requires_canonical_slug(self):
        """Same blocker class for the other variant: it must only be
        permitted on the canonical claude-org slug *and* with a clone
        actually detected at workers_dir/claude-org. clock-app must not
        be allowed to slip into the mirror's worktree pool."""
        with self.assertRaises(rwl.ResolveError) as cm:
            rwl.resolve(
                task_id="bad-mirror-variant",
                project_slug="clock-app",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={
                    "pattern": "B",
                    "pattern_variant": "claude_org_repo_worktree",
                },
            )
        self.assertIn("claude_org_repo_worktree", str(cm.exception))

    def test_force_pattern_b_with_local_repo_path_succeeds(self):
        """Registered project with a local git repo path → ``--pattern B``
        is allowed and the conventional ``workers_dir/<slug>/.worktrees/``
        location is used."""
        local_repo = Path(self._td.name) / "local-repo"
        local_repo.mkdir()
        subprocess.run(
            ["git", "-C", str(local_repo), "init", "-q"], check=True
        )
        self.sb.write_registry(
            [("ローカル", "local-app", str(local_repo), "Demo")]
        )
        layout = rwl.resolve(
            task_id="ok-b-local",
            project_slug="local-app",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={"pattern": "B"},
        )
        self.assertEqual(layout.pattern, "B")
        self.assertIsNone(layout.pattern_variant)
        self.assertEqual(
            Path(layout.worker_dir),
            (self.sb.workers / "local-app" / ".worktrees" / "ok-b-local").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
