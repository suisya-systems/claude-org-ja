"""Tests for tools/gen_delegate_payload.py (Issue #283 Stage 3).

Coverage:
- preview is non-destructive (no DB / no files)
- apply reserves a runs.status='queued' row (Codex Blocker B-1)
- apply does NOT write Active Work Items (no writes outside the queued row)
- DELEGATE body contains all required rows: pattern / role / Permission Mode
  / 検証深度 / planned_branch
- Snapshot tests for each Pattern + role variant
- preview --json emits a structured object
- --skip-settings / runtime missing → graceful (apply still succeeds)
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import gen_delegate_payload as gdp  # noqa: E402
from tools import gen_worker_brief as gwb  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402


GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "delegate_payload"


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class _Sandbox:
    def __init__(self, td: Path, *, with_claude_org_origin: bool = True):
        self.root = td
        self.workers = td / "workers"
        self.workers.mkdir()
        self.claude_org_root = td / "claude-org"
        (self.claude_org_root / ".state").mkdir(parents=True)
        (self.claude_org_root / "registry").mkdir()
        # Self-edit detection runs off git origin URL. Tests that drive
        # their own git setup on claude_org_root (e.g. pre-seeding a
        # ``main`` branch + initial commit for worktree-creation tests)
        # opt out via ``with_claude_org_origin=False`` to avoid a
        # ``git init`` race that would silently swallow ``--initial-branch``.
        if with_claude_org_origin:
            subprocess.run(
                ["git", "init", "-q", str(self.claude_org_root)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(self.claude_org_root), "remote", "add",
                 "origin", "https://github.com/suisya-systems/claude-org-ja.git"],
                check=True,
            )
        (self.claude_org_root / "registry" / "org-config.md").write_text(
            "## Permission Mode\ndefault_permission_mode: auto\n"
            "## Workers Directory\nworkers_dir: ../workers\n",
            encoding="utf-8",
        )
        # The checked-in registry/projects.md no longer carries a
        # claude-org-ja row (origin-URL detection replaces the path
        # lookup), but several tests in this module still exercise the
        # legacy *registry-driven* code paths (Pattern A doc-audit on a
        # registered claude-org-ja path, gitignored sub-mode, etc.).
        # The sandbox row uses the per-test temp dir as its path, so it
        # doesn't leak any user-specific data — keeping it lets the legacy
        # paths stay covered while the production registry stays clean.
        (self.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| 時計アプリ | clock-app | - | Web 時計 | デザイン |\n"
            f"| claude-org-ja | claude-org-ja | {self.claude_org_root} | Self | スキル改善 |\n",
            encoding="utf-8",
        )
        self.db_path = self.claude_org_root / ".state" / "state.db"
        conn = connect(self.db_path)
        apply_schema(conn)
        conn.close()

    def add_active_run(self, *, task_id: str, project_slug: str, worker_dir: str) -> None:
        conn = connect(self.db_path)
        w = StateWriter(conn)
        with w.transaction() as tx:
            tx.register_worker_dir(abs_path=worker_dir, layout="flat")
            tx.upsert_run(
                task_id=task_id,
                project_slug=project_slug,
                pattern="A",
                title=task_id,
                status="in_use",
                worker_dir_abs_path=worker_dir,
            )
        conn.close()

    def list_runs(self) -> list[dict]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT task_id, status, pattern, branch FROM runs"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Pure planner
# ---------------------------------------------------------------------------


class TestBuildDelegatePlan(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _build(self, **kwargs) -> gdp.DelegatePlan:
        defaults = dict(
            task_id="demo-task",
            project_slug="clock-app",
            description="add a feature",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        defaults.update(kwargs)
        return gdp.build_delegate_plan(**defaults)

    def test_pattern_a_default_role_full(self):
        plan = self._build()
        body = plan.delegate_body
        # Required rows per org-delegate Step 2 template
        self.assertIn("DELEGATE: 以下のワーカーを派遣してください", body)
        self.assertIn("ワーカーディレクトリ:", body)
        self.assertIn("ディレクトリパターン: A: プロジェクトディレクトリ", body)
        self.assertIn("Permission Mode: auto", body)
        self.assertIn("検証深度: full", body)
        self.assertIn("ブランチ (planned): feat/demo-task", body)
        self.assertIn("窓口ペイン名: `secretary`", body)
        # Brief path uses CLAUDE.md (not self_edit)
        self.assertEqual(plan.brief_out_path.name, "CLAUDE.md")
        # Issue #725: preview/plan must disclose the delivery-guard .gitignore
        # that apply writes for a Pattern A new-project (base_repo None).
        self.assertIsNone(plan.base_repo)
        self.assertIn(
            Path(plan.layout.worker_dir) / ".gitignore",
            plan.artifacts_to_create,
        )

    def test_pattern_b_when_concurrent_active_run(self):
        self.sb.add_active_run(
            task_id="other-task",
            project_slug="clock-app",
            worker_dir=str(self.sb.workers / "clock-app"),
        )
        plan = self._build()
        body = plan.delegate_body
        self.assertIn("ディレクトリパターン: B: worktree", body)
        self.assertEqual(plan.layout.pattern, "B")

    def test_pattern_c_ephemeral_for_unknown_slug(self):
        plan = self._build(project_slug="unknown-thing")
        body = plan.delegate_body
        self.assertIn("ディレクトリパターン: C: エフェメラル", body)
        self.assertIn("Pattern C", body)  # branch line carries the Pattern C note

    def test_self_edit_brief_path_is_local_md(self):
        plan = self._build(
            project_slug="claude-org-ja",
            description="edit a doc",
        )
        self.assertEqual(plan.brief_out_path.name, "CLAUDE.local.md")
        self.assertEqual(plan.layout.role, "claude-org-self-edit")

    def test_audit_mode_emits_doc_audit_role(self):
        plan = self._build(mode="audit", project_slug="claude-org-ja")
        self.assertEqual(plan.layout.role, "doc-audit")
        # Should still be a CLAUDE.local.md because gitignored sub-mode
        # logic only triggers self_edit for the claude-org-self-edit role,
        # but doc-audit is not a self-edit. So plain CLAUDE.md is fine for
        # audit on Pattern A worker dir (which is outside claude-org).
        # (claude-org-ja audit uses Pattern A → workers/claude-org-ja/.)
        self.assertEqual(plan.brief_out_path.name, "CLAUDE.md")


class TestIssue484AliasRemoteRow(unittest.TestCase):
    """End-to-end (``build_delegate_plan``) coverage for the Issue #484 repro:
    a remote project registered under the alias *name* ``claude-org-en`` with
    a clone URL, dispatched on the first delegation (no local mirror clone, no
    state-DB run). Both bugs must be fixed together:

    - bug 2: resolves to Pattern A (clone the URL), not Pattern C ephemeral.
    - bug 1: renders the *normal* brief (CLAUDE.md) with no self-edit
      ``直接編集すること`` directive — the worker clones the EN upstream rather
      than being told to edit the live claude-org-ja repo in place.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        # Replace the default registry with an alias-named remote URL row.
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| 時計アプリ | clock-app | - | Web 時計 | デザイン |\n"
            "| claude-org-en | claude-org-en | "
            "https://github.com/suisya-systems/claude-org | EN upstream | 翻訳同期 |\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_alias_remote_row_pattern_a_normal_brief(self):
        plan = gdp.build_delegate_plan(
            task_id="en-translation-sync-v2",
            project_slug="claude-org-en",
            description="translation sync",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        # bug 2: Pattern A, anchored on the registered alias name.
        self.assertEqual(plan.layout.pattern, "A")
        self.assertIsNone(plan.layout.pattern_variant)
        self.assertFalse(plan.layout.self_edit)
        # Issue #709: a URL-only registered project with no local clone yet
        # now routes into the canonical ``<clone>/.worktrees/<task>/`` layout
        # and carries a pending base clone that apply will ``git clone``
        # first — instead of the old legacy-direct ``workers/<slug>/`` path
        # that left the worker in a non-git dir (the #709 bug). The clone
        # target is the canonical ``workers/<slug>/`` location.
        clone_target = (self.sb.workers / "claude-org-en").resolve()
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (clone_target / ".worktrees" / "en-translation-sync-v2").resolve(),
        )
        self.assertIsNotNone(plan.base_repo)
        self.assertEqual(Path(plan.base_repo).resolve(), clone_target)
        self.assertIsNotNone(plan.pending_clone)
        self.assertEqual(
            plan.pending_clone.url,
            "https://github.com/suisya-systems/claude-org",
        )
        self.assertEqual(Path(plan.pending_clone.target).resolve(), clone_target)
        # Pattern A worktree carve-out: sandbox pattern flips to B, base-clone
        # is surfaced for the runtime (Issue #489 parity).
        self.assertEqual(plan.settings_args["pattern"], "B")
        self.assertEqual(plan.settings_args["base-clone"], str(clone_target))
        # The DELEGATE body advertises a clone source (Pattern A label).
        self.assertIn("clone or reuse:", plan.delegate_body)
        self.assertIn(
            "https://github.com/suisya-systems/claude-org", plan.delegate_body
        )
        # bug 1: non-self-edit brief, no in-place-edit directive. Brief name
        # stays CLAUDE.md at plan time (the clone isn't present to inspect);
        # apply re-evaluates against the real checkout (Issue #712).
        self.assertEqual(plan.brief_out_path.name, "CLAUDE.md")
        brief = gwb.render(plan.config)
        self.assertNotIn("直接編集すること", brief)
        self.assertNotIn("ルート CLAUDE.md", brief)


# ---------------------------------------------------------------------------
# Apply — side effects
# ---------------------------------------------------------------------------


class TestApplyDelegatePlan(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _apply(self, **plan_kwargs):
        defaults = dict(
            task_id="apply-test",
            project_slug="clock-app",
            description="implement something",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        defaults.update(plan_kwargs)
        plan = gdp.build_delegate_plan(**defaults)
        return plan, gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )

    def test_apply_reserves_queued_row(self):
        _, result = self._apply()
        runs = self.sb.list_runs()
        match = [r for r in runs if r["task_id"] == "apply-test"]
        self.assertEqual(len(match), 1)
        row = match[0]
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["pattern"], "A")
        self.assertEqual(row["branch"], "feat/apply-test")
        self.assertEqual(result.db_reservation["status"], "queued")

    def test_apply_writes_brief_and_send_plan(self):
        plan, result = self._apply()
        self.assertTrue(result.brief_path.exists())
        text = result.brief_path.read_text(encoding="utf-8")
        self.assertIn("apply-test", text)
        self.assertTrue(result.send_plan_path.exists())
        send_plan = json.loads(result.send_plan_path.read_text(encoding="utf-8"))
        self.assertEqual(send_plan["to_id"], "dispatcher")
        self.assertIn("DELEGATE:", send_plan["message"])
        # The summary block in send_plan carries the layout for audit.
        self.assertEqual(send_plan["summary"]["pattern"], "A")
        self.assertEqual(send_plan["summary"]["task_id"], "apply-test")

    def test_apply_skips_settings_gracefully(self):
        _, result = self._apply()
        self.assertIsNone(result.settings_path)
        self.assertEqual(result.settings_skipped_reason, "skip_settings flag set")

    def test_apply_does_not_write_active_work_items(self):
        """Codex Blocker B-1: Active Work Items is dispatcher's T2.

        We assert this indirectly by checking no run row has a non-queued
        status after apply, and that the only run created is the queued
        one we just added.
        """
        _, _ = self._apply()
        runs = self.sb.list_runs()
        statuses = {r["status"] for r in runs}
        # Only the queued reservation; no in_use/review (= Active Work Items)
        # rows were created by apply.
        self.assertEqual(statuses, {"queued"})

    # ---- Issue #725: Pattern A new-project delivery guard ----

    def test_apply_pattern_a_writes_delivery_gitignore(self):
        """Issue #725: a Pattern A new-project (base_repo None, worker
        ``git init``s the delivery dir) gets a worker_dir/.gitignore that
        ignores the org-internal artifacts."""
        plan, result = self._apply()
        self.assertIsNone(plan.base_repo)  # precondition: new-project path
        worker_dir = Path(plan.layout.worker_dir)
        gitignore = worker_dir / ".gitignore"
        self.assertEqual(result.gitignore_path, gitignore)
        self.assertTrue(gitignore.exists())
        lines = {ln.strip() for ln in gitignore.read_text(encoding="utf-8").splitlines()}
        # Anchored to root so a subdir with the same basename stays tracked.
        self.assertIn(f"/{plan.brief_out_path.name}", lines)
        self.assertIn("/send_plan.json", lines)
        self.assertIn("/.claude/settings.local.json", lines)
        self.assertIn(gdp._GITIGNORE_MANAGED_HEADER, lines)

    def test_apply_pattern_a_gitignore_prevents_leak(self):
        """The end-to-end regression for the #725 incident: after the worker
        ``git init``s and ``git add -A``s the delivery tree, the brief /
        send_plan / settings must NOT be staged, while a genuine project file
        is."""
        plan, result = self._apply()
        worker_dir = Path(plan.layout.worker_dir)
        # Simulate the worker's first-commit sequence on the delivered project.
        settings = worker_dir / ".claude" / "settings.local.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text("{}\n", encoding="utf-8")
        real_file = worker_dir / "app.py"
        real_file.write_text("print('hello')\n", encoding="utf-8")
        env = {**__import__("os").environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
        subprocess.run(["git", "-C", str(worker_dir), "init", "-q"],
                       check=True, env=env)
        subprocess.run(["git", "-C", str(worker_dir), "add", "-A"],
                       check=True, env=env)
        staged = subprocess.check_output(
            ["git", "-C", str(worker_dir), "diff", "--cached", "--name-only"],
            env=env,
        ).decode("utf-8").split()
        self.assertNotIn(plan.brief_out_path.name, staged)
        self.assertNotIn("send_plan.json", staged)
        self.assertNotIn(".claude/settings.local.json", staged)
        # The real deliverable is unaffected; .gitignore itself is fine to ship.
        self.assertIn("app.py", staged)
        self.assertIn(".gitignore", staged)

    def test_ensure_gitignore_idempotent_and_appends(self):
        """Re-running the guard never duplicates lines, preserves a
        pre-existing .gitignore, and only adds the header once."""
        plan, _ = self._apply()
        worker_dir = Path(plan.layout.worker_dir)
        gitignore = worker_dir / ".gitignore"
        send_plan = worker_dir / "send_plan.json"
        # Second call is a no-op (everything already present).
        self.assertIsNone(
            gdp._ensure_worker_dir_gitignore(plan, send_plan_path=send_plan)
        )
        # Rewrite with unrelated pre-existing content, then re-run: the guard
        # appends its block under one header without clobbering the original.
        gitignore.write_text("node_modules/\n", encoding="utf-8")
        out = gdp._ensure_worker_dir_gitignore(plan, send_plan_path=send_plan)
        self.assertEqual(out, gitignore)
        text = gitignore.read_text(encoding="utf-8")
        self.assertIn("node_modules/", text)
        self.assertIn("/send_plan.json", text)
        self.assertEqual(text.count(gdp._GITIGNORE_MANAGED_HEADER), 1)
        # And now idempotent again.
        self.assertIsNone(
            gdp._ensure_worker_dir_gitignore(plan, send_plan_path=send_plan)
        )

    def test_apply_gitignore_follows_custom_send_plan_out(self):
        """Issue #725 (Codex P2): ``--send-plan-out`` relocating the manifest
        inside worker_dir must still be ignored — the guard anchors on the
        actual output path, not the default basename."""
        plan = gdp.build_delegate_plan(
            task_id="apply-test",
            project_slug="clock-app",
            description="implement something",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        worker_dir = Path(plan.layout.worker_dir)
        custom_send_plan = worker_dir / "meta" / "delegate.json"
        result = gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
            send_plan_out=custom_send_plan,
        )
        self.assertEqual(result.send_plan_path, custom_send_plan)
        lines = {
            ln.strip()
            for ln in (worker_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        }
        self.assertIn("/meta/delegate.json", lines)

    def test_apply_gitignore_omits_send_plan_when_written_outside_worker_dir(self):
        """Issue #725 (Codex P2 follow-up): a send_plan written OUTSIDE the
        delivery tree must not add any send_plan ignore line — otherwise a
        legitimate project-authored root ``send_plan.json`` would be silently
        excluded from the worker's first commit."""
        plan = gdp.build_delegate_plan(
            task_id="apply-test",
            project_slug="clock-app",
            description="implement something",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        worker_dir = Path(plan.layout.worker_dir)
        outside_send_plan = self.sb.root / "external_send_plan.json"
        result = gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
            send_plan_out=outside_send_plan,
        )
        self.assertEqual(result.send_plan_path, outside_send_plan)
        lines = {
            ln.strip()
            for ln in (worker_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        }
        self.assertNotIn("/send_plan.json", lines)
        self.assertFalse(any("send_plan" in ln for ln in lines))
        # Brief + settings guards are unaffected.
        self.assertIn(f"/{plan.brief_out_path.name}", lines)
        self.assertIn("/.claude/settings.local.json", lines)


# ---------------------------------------------------------------------------
# settings_args dispatch-context pass-through (Phase 1 PR4)
# ---------------------------------------------------------------------------


class TestSettingsGenerateCmd(unittest.TestCase):
    """Verifies _build_settings_generate_cmd forwards the dispatch context
    (--pattern / --base-clone / --task-id / --branch-ref) so the runtime
    can substitute `worker_roles.<role>.sandbox_by_pattern` placeholders.
    """

    _MANDATORY = {
        "role": "default",
        "worker-dir": "/wd",
        "claude-org-path": "/co",
        "out": "/wd/.claude/settings.local.json",
    }

    def _build(self, **extra: str) -> list[str]:
        args = dict(self._MANDATORY)
        args.update(extra)
        return gdp._build_settings_generate_cmd(args, runtime_cmd="claude-org-runtime")

    def test_pattern_a_omits_pattern_b_only_flags(self):
        cmd = self._build(pattern="A", **{"task-id": "t-1", "branch-ref": "feat/x"})
        # Pattern A leaves base-clone unset because the resolver does not
        # supply it; the runtime must error if a Pattern A body references
        # {base_clone}, so we MUST NOT pass an empty --base-clone here.
        self.assertNotIn("--base-clone", cmd)
        self.assertIn("--pattern", cmd)
        self.assertEqual(cmd[cmd.index("--pattern") + 1], "A")
        self.assertEqual(cmd[cmd.index("--task-id") + 1], "t-1")
        self.assertEqual(cmd[cmd.index("--branch-ref") + 1], "feat/x")

    def test_pattern_b_passes_full_dispatch_context(self):
        cmd = self._build(
            pattern="B",
            **{
                "base-clone": "/bc",
                "task-id": "T123",
                "branch-ref": "feat/self-edit",
            },
        )
        self.assertEqual(cmd[cmd.index("--pattern") + 1], "B")
        self.assertEqual(cmd[cmd.index("--base-clone") + 1], "/bc")
        self.assertEqual(cmd[cmd.index("--task-id") + 1], "T123")
        self.assertEqual(cmd[cmd.index("--branch-ref") + 1], "feat/self-edit")

    def test_pattern_c_ephemeral_omits_branch_ref(self):
        # Pattern C ephemeral has planned_branch=None; settings_args
        # therefore omits branch-ref entirely. The cmd builder MUST drop
        # the flag rather than emit "--branch-ref None" or empty string.
        cmd = self._build(pattern="C", **{"task-id": "t-c"})
        self.assertNotIn("--branch-ref", cmd)
        self.assertNotIn("--base-clone", cmd)
        self.assertEqual(cmd[cmd.index("--pattern") + 1], "C")
        self.assertEqual(cmd[cmd.index("--task-id") + 1], "t-c")

    def test_pre_pr4_settings_args_renders_minimum_cmd(self):
        # Backward compatibility: a settings_args dict that lacks the
        # PR4-added keys (e.g. an old caller that constructs the dict by
        # hand) must still render a runnable cmd — the runtime CLI's
        # required args are only the mandatory ones. Issue #625 added
        # --schema to that mandatory set (always pinned to ja's
        # org_extension_schema.json), so it appears here too.
        cmd = self._build()
        self.assertEqual(
            cmd,
            [
                "claude-org-runtime",
                "settings",
                "generate",
                "--role",
                "default",
                "--worker-dir",
                "/wd",
                "--claude-org-path",
                "/co",
                "--out",
                "/wd/.claude/settings.local.json",
                "--schema",
                "/co/tools/org_extension_schema.json",
            ],
        )

    def test_schema_pinned_to_ja_extension_schema_absolute(self):
        # Issue #625: --schema must always be emitted, pointing at ja's
        # tools/org_extension_schema.json so the runtime emits the worker
        # sandbox_by_pattern policy (Layer 3 bwrap isolation + ja denyRead)
        # instead of falling back to its bundled role_configs_schema.json.
        cmd = self._build(pattern="A", **{"task-id": "t-1"})
        self.assertIn("--schema", cmd)
        schema = cmd[cmd.index("--schema") + 1]
        self.assertEqual(schema, "/co/tools/org_extension_schema.json")
        # cwd-independent: the path is absolute, derived from
        # --claude-org-path, not from the runtime's working directory.
        self.assertTrue(Path(schema).is_absolute())

    def test_schema_absolutized_from_relative_claude_org_path(self):
        # The runtime resolves --schema relative to its cwd, and apply may
        # run from an arbitrary directory, so a relative claude-org-path
        # must still yield an absolute --schema.
        cmd = gdp._build_settings_generate_cmd(
            {
                "role": "default",
                "worker-dir": "/wd",
                "claude-org-path": "relative/co",
                "out": "/wd/.claude/settings.local.json",
            },
            runtime_cmd="claude-org-runtime",
        )
        schema = cmd[cmd.index("--schema") + 1]
        self.assertTrue(Path(schema).is_absolute())
        self.assertTrue(schema.endswith("/tools/org_extension_schema.json"))


# ---------------------------------------------------------------------------
# CLI smoke tests (preview + apply paths)
# ---------------------------------------------------------------------------


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _common_args(self) -> list[str]:
        return [
            "--task-id", "cli-task",
            "--project-slug", "clock-app",
            "--description", "do the thing",
            "--claude-org-root", str(self.sb.claude_org_root),
            "--state-db-path", str(self.sb.db_path),
        ]

    def test_preview_writes_no_files_no_db(self):
        from contextlib import redirect_stdout
        from io import StringIO

        worker_dir = self.sb.workers / "clock-app"
        # Pre-condition: the worker dir doesn't exist yet
        self.assertFalse(worker_dir.exists())
        runs_before = len(self.sb.list_runs())
        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main(["preview", *self._common_args()])
        self.assertEqual(rc, 0)
        self.assertFalse(worker_dir.exists())
        self.assertEqual(len(self.sb.list_runs()), runs_before)
        out = buf.getvalue()
        self.assertIn("DELEGATE body (preview, no writes)", out)
        self.assertIn("Permission Mode: auto", out)
        self.assertIn("検証深度: full", out)

    def test_preview_json_emits_structured_object(self):
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main(["preview", *self._common_args(), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("delegate_body", data)
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["pattern"], "A")

    def test_apply_creates_brief_and_reserves(self):
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main([
                "apply",
                *self._common_args(),
                "--skip-settings",
            ])
        self.assertEqual(rc, 0)
        runs = self.sb.list_runs()
        self.assertTrue(any(r["task_id"] == "cli-task" and r["status"] == "queued" for r in runs))
        brief = self.sb.workers / "clock-app" / "CLAUDE.md"
        self.assertTrue(brief.exists())
        send_plan = brief.with_name("send_plan.json")
        self.assertTrue(send_plan.exists())


# ---------------------------------------------------------------------------
# Snapshot tests against goldens
# ---------------------------------------------------------------------------


def _normalize_body(text: str, sandbox_root: Path) -> str:
    """Replace sandbox-specific paths with stable placeholders before snapshotting."""
    text = text.replace(str(sandbox_root.resolve()), "<SANDBOX>")
    # Windows backslashes vs forward slashes
    text = text.replace("\\", "/")
    return text


class TestGoldenSnapshots(unittest.TestCase):
    """Render DELEGATE bodies for each (pattern, role) combo and compare
    against committed goldens. Update goldens with::

        UPDATE_GOLDENS=1 python -m unittest tests.test_gen_delegate_payload

    Goldens live in tests/fixtures/delegate_payload/ and intentionally
    NORMALIZE absolute paths to ``<SANDBOX>`` placeholders to keep them
    stable across machines/OSes.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _check(self, name: str, body: str) -> None:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path = GOLDEN_DIR / f"delegate_payload_{name}.golden.md"
        normalized = _normalize_body(body, self.sb.root)
        if not path.exists() or _env_update_goldens():
            path.write_text(normalized, encoding="utf-8")
            return
        expected = path.read_text(encoding="utf-8")
        self.assertEqual(
            normalized,
            expected,
            f"DELEGATE body drift in {path}; rerun with UPDATE_GOLDENS=1 to refresh.",
        )

    def test_golden_pattern_a_default_full(self):
        plan = gdp.build_delegate_plan(
            task_id="snap-a-default",
            project_slug="clock-app",
            description="add a sparkline",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self._check("pattern_a_default_full", plan.delegate_body)

    def test_golden_pattern_b_self_edit_full(self):
        # claude-org-ja with concurrent active run forces Pattern B
        self.sb.add_active_run(
            task_id="self-edit-other",
            project_slug="claude-org-ja",
            worker_dir=str(self.sb.workers / "claude-org-ja"),
        )
        plan = gdp.build_delegate_plan(
            task_id="snap-b-self-edit",
            project_slug="claude-org-ja",
            description="refactor a skill",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self._check("pattern_b_self_edit_full", plan.delegate_body)

    def test_golden_pattern_c_ephemeral_minimal(self):
        plan = gdp.build_delegate_plan(
            task_id="snap-c-ephemeral",
            project_slug="totally-new",
            description="quick survey",
            verification_depth="minimal",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self._check("pattern_c_ephemeral_minimal", plan.delegate_body)

    def test_golden_pattern_c_gitignored_repo_root(self):
        """Codex Round 1 Minor: gitignored sub-mode is the highest-risk
        Pattern C variant; lock its DELEGATE rendering down with a golden."""
        try:
            import subprocess as _sp
            _sp.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, _sp.CalledProcessError):
            self.skipTest("git not available")
        local_repo = Path(self._td.name) / "host-repo"
        local_repo.mkdir()
        _sp.run(["git", "-C", str(local_repo), "init", "-q"], check=True)
        (local_repo / ".gitignore").write_text("tmp/\n", encoding="utf-8")
        # Re-seed registry so the host-repo project is registered.
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| ホスト | host-app | {local_repo} | Host repo | tmp 編集 |\n"
            f"| claude-org-ja | claude-org-ja | {self.sb.claude_org_root} | Self | スキル改善 |\n",
            encoding="utf-8",
        )
        plan = gdp.build_delegate_plan(
            task_id="snap-c-gitignored",
            project_slug="host-app",
            targets=["tmp/secret.txt"],
            description="redact gitignored notes",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        # Normalise the local_repo path so the golden stays portable.
        body = plan.delegate_body.replace(str(local_repo.resolve()), "<HOSTREPO>")
        self._check("pattern_c_gitignored_repo_root_full", body)
        # Variant must be visible in the formatted body.
        self.assertIn("gitignored サブモード", body)

    def test_golden_pattern_a_doc_audit(self):
        """Codex M-4 regression guard: --mode audit must surface as doc-audit
        and the brief filename must stay CLAUDE.md (no spurious .local.md)."""
        plan = gdp.build_delegate_plan(
            task_id="snap-a-audit",
            project_slug="claude-org-ja",
            description="audit recent changes",
            mode="audit",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self._check("pattern_a_doc_audit_full", plan.delegate_body)
        self.assertEqual(plan.layout.role, "doc-audit")


# ---------------------------------------------------------------------------
# --from-toml round-trip (Codex Round 1 Major: TOML survives bare CLI)
# ---------------------------------------------------------------------------


class TestFromTomlRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _write_toml(self, path: Path, *, role: str, depth: str) -> None:
        path.write_text(
            "[task]\n"
            'id = "round-trip"\n'
            'description = "round-trip via TOML"\n'
            f'verification_depth = "{depth}"\n'
            'branch = "round-trip"\n'
            'commit_prefix = "feat(clock):"\n'
            "\n[worker]\n"
            f'dir = "X:/dummy"\n'
            'pattern = "A"\n'
            f'role = "{role}"\n'
            f'self_edit = {"true" if role == "claude-org-self-edit" else "false"}\n'
            "\n[project]\n"
            'name = "clock-app"\n'
            'description = "Web 時計"\n'
            "\n[paths]\n"
            'claude_org = "."\n',
            encoding="utf-8",
        )

    def test_from_toml_preserves_doc_audit_mode_and_minimal_depth(self):
        toml = Path(self._td.name) / "input.toml"
        self._write_toml(toml, role="doc-audit", depth="minimal")
        # Bare CLI: no --mode / --verification-depth flag → TOML wins.
        kwargs = gdp._gather_plan_kwargs(
            argparse.Namespace(
                from_toml=toml,
                task_id=None, project_slug=None, target=[], description=None,
                mode=None, branch_override=None, commit_prefix=None,
                verification_depth=None, issue_url=None, closes_issue=None,
                refs_issues=None, project_name_override=None,
                project_description_override=None, impl_target=[],
                impl_guidance=None, knowledge=[], parallel_notes=None,
                registry_path=None, state_db_path=None, claude_org_root=None,
                workers_dir=None,
            )
        )
        self.assertEqual(kwargs["mode"], "audit")
        self.assertEqual(kwargs["verification_depth"], "minimal")
        self.assertEqual(kwargs["project_slug"], "clock-app")

    def test_from_toml_cli_override_wins(self):
        toml = Path(self._td.name) / "input.toml"
        self._write_toml(toml, role="doc-audit", depth="minimal")
        kwargs = gdp._gather_plan_kwargs(
            argparse.Namespace(
                from_toml=toml,
                task_id=None, project_slug=None, target=[], description=None,
                mode="edit", branch_override=None, commit_prefix=None,
                verification_depth="full", issue_url=None, closes_issue=None,
                refs_issues=None, project_name_override=None,
                project_description_override=None, impl_target=[],
                impl_guidance=None, knowledge=[], parallel_notes=None,
                registry_path=None, state_db_path=None, claude_org_root=None,
                workers_dir=None,
            )
        )
        self.assertEqual(kwargs["mode"], "edit")
        self.assertEqual(kwargs["verification_depth"], "full")


# ---------------------------------------------------------------------------
# Issue #290 regression tests — TOML [worker] / [paths] honor + encoding
# ---------------------------------------------------------------------------


class TestIssue290Regressions(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _write_toml(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")

    def _run_preview_json(self, argv: list[str]) -> dict:
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main(["preview", *argv, "--json"])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def test_290_a_worker_block_overrides_resolver(self):
        """TOML [worker] pattern/role/self_edit/dir survive into the layout
        summary instead of being recomputed by the resolver."""
        explicit_dir = self.sb.root / "explicit-worker-dir"
        toml = self.sb.root / "in.toml"
        self._write_toml(
            toml,
            "[task]\n"
            'id = "t-290a"\n'
            'description = "honor worker block"\n'
            "\n[worker]\n"
            f'dir = "{explicit_dir.as_posix()}"\n'
            'pattern = "B"\n'
            'role = "claude-org-self-edit"\n'
            "self_edit = true\n"
            "\n[project]\n"
            'name = "clock-app"\n'
            f'\n[paths]\nclaude_org = "{self.sb.claude_org_root.as_posix()}"\n',
        )
        data = self._run_preview_json([
            "--from-toml", str(toml),
            "--state-db-path", str(self.sb.db_path),
        ])
        s = data["summary"]
        self.assertEqual(s["pattern"], "B")
        self.assertEqual(s["role"], "claude-org-self-edit")
        self.assertTrue(s["self_edit"])
        self.assertEqual(
            Path(s["worker_dir"]).resolve(), explicit_dir.resolve()
        )
        # settings_args picks up the overridden role + worker_dir.
        self.assertEqual(s["settings_args"]["role"], "claude-org-self-edit")
        self.assertEqual(
            Path(s["settings_args"]["worker-dir"]).resolve(),
            explicit_dir.resolve(),
        )

    def test_290_b_paths_claude_org_flows_into_settings_args(self):
        """[paths] claude_org from TOML lands in settings_args.claude-org-path
        when no CLI override is supplied (no more cwd-derived drift)."""
        toml = self.sb.root / "in.toml"
        self._write_toml(
            toml,
            "[task]\n"
            'id = "t-290b"\n'
            'description = "paths.claude_org honored"\n'
            "\n[project]\n"
            'name = "clock-app"\n'
            f'\n[paths]\nclaude_org = "{self.sb.claude_org_root.as_posix()}"\n',
        )
        # Intentionally no --claude-org-root.
        data = self._run_preview_json([
            "--from-toml", str(toml),
            "--state-db-path", str(self.sb.db_path),
        ])
        self.assertEqual(
            Path(data["summary"]["settings_args"]["claude-org-path"]).resolve(),
            self.sb.claude_org_root.resolve(),
        )

    def test_290_c_cli_claude_org_root_overrides_toml_paths(self):
        """CLI --claude-org-root wins over [paths] claude_org."""
        bogus = self.sb.root / "bogus-elsewhere"
        toml = self.sb.root / "in.toml"
        self._write_toml(
            toml,
            "[task]\n"
            'id = "t-290c"\n'
            'description = "cli wins"\n'
            "\n[project]\n"
            'name = "clock-app"\n'
            f'\n[paths]\nclaude_org = "{bogus.as_posix()}"\n',
        )
        data = self._run_preview_json([
            "--from-toml", str(toml),
            "--claude-org-root", str(self.sb.claude_org_root),
            "--state-db-path", str(self.sb.db_path),
        ])
        resolved = Path(
            data["summary"]["settings_args"]["claude-org-path"]
        ).resolve()
        self.assertEqual(resolved, self.sb.claude_org_root.resolve())
        self.assertNotEqual(resolved, bogus.resolve())

    def test_290_d_japanese_preview_does_not_mojibake_under_cp932(self):
        """preview stdout decodes cleanly even when the underlying console
        is cp932 — the encoding wrapper rewraps stdout to utf-8."""
        import io as _io

        toml = self.sb.root / "in.toml"
        self._write_toml(
            toml,
            "[task]\n"
            'id = "t-290d"\n'
            'description = "日本語の説明文 — 派遣テスト"\n'
            "\n[project]\n"
            'name = "clock-app"\n'
            f'\n[paths]\nclaude_org = "{self.sb.claude_org_root.as_posix()}"\n',
        )

        raw = _io.BytesIO()
        wrapper = _io.TextIOWrapper(
            raw, encoding="cp932", errors="strict", write_through=True
        )
        old_stdout = sys.stdout
        sys.stdout = wrapper
        try:
            rc = gdp.main([
                "preview",
                "--from-toml", str(toml),
                "--state-db-path", str(self.sb.db_path),
            ])
        finally:
            try:
                wrapper.flush()
            except Exception:
                pass
            sys.stdout = old_stdout
        self.assertEqual(rc, 0)
        decoded = raw.getvalue().decode("utf-8")
        # Japanese phrases from the DELEGATE template + description survive.
        self.assertIn("以下のワーカーを派遣", decoded)
        self.assertIn("日本語の説明文", decoded)


# ---------------------------------------------------------------------------
# Issue #309: Pattern B apply must create the git worktree
# ---------------------------------------------------------------------------


class TestPatternBWorktreeCreation(unittest.TestCase):
    """apply for Pattern B (incl. live_repo_worktree variant) must run
    `git worktree add` so the brief lands inside a real worktree."""

    def setUp(self) -> None:
        try:
            import subprocess as _sp
            _sp.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        # This class drives its own ``git init -b main`` + initial commit on
        # claude_org_root, so we opt out of the sandbox-level git init that
        # would otherwise re-init and silently swallow ``--initial-branch``.
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=False)
        # Initialize claude_org_root as a real git repo so live_repo_worktree
        # can branch from it.
        import os
        self._git_env = os.environ.copy()
        self._git_env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        import subprocess as _sp
        self._init_repo_with_origin_main(self.sb.claude_org_root)

    def _init_repo_with_origin_main(self, base: Path) -> None:
        """Init ``base`` as a git repo on `main` with one commit and
        ``origin/HEAD`` pointing at ``origin/main`` (no real remote required)."""
        import subprocess as _sp
        _sp.run(["git", "-C", str(base), "init", "-q", "-b", "main"],
                check=True)
        _sp.run(["git", "-C", str(base), "commit", "--allow-empty",
                 "-m", "init", "-q"],
                check=True, env=self._git_env)
        sha = _sp.check_output(
            ["git", "-C", str(base), "rev-parse", "main"],
        ).decode().strip()
        _sp.run(
            ["git", "-C", str(base), "update-ref",
             "refs/remotes/origin/main", sha],
            check=True,
        )
        _sp.run(
            ["git", "-C", str(base), "symbolic-ref",
             "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
            check=True,
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _build_self_edit_b(self, *, task_id: str = "b-task"):
        return gdp.build_delegate_plan(
            task_id=task_id,
            project_slug="claude-org-ja",
            description="self-edit pattern B",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={
                "pattern": "B",
                "pattern_variant": "live_repo_worktree",
                "role": "claude-org-self-edit",
                "self_edit": True,
            },
        )

    def _list_worktrees(self) -> list[str]:
        import subprocess as _sp
        out = _sp.check_output(
            ["git", "-C", str(self.sb.claude_org_root),
             "worktree", "list", "--porcelain"],
        ).decode("utf-8", errors="replace")
        return [
            line[len("worktree "):].strip()
            for line in out.splitlines()
            if line.startswith("worktree ")
        ]

    def test_apply_creates_live_repo_worktree(self):
        plan = self._build_self_edit_b()
        # Plan should know which repo to branch from.
        self.assertEqual(
            Path(plan.base_repo).resolve(), self.sb.claude_org_root.resolve()
        )
        result = gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        worker_dir = Path(plan.layout.worker_dir).resolve()
        self.assertTrue(worker_dir.exists())
        self.assertTrue((worker_dir / ".git").exists())
        # Registered with git as a worktree
        registered = {Path(p).resolve() for p in self._list_worktrees()}
        self.assertIn(worker_dir, registered)
        # Brief landed inside the worktree
        brief = worker_dir / "CLAUDE.local.md"
        self.assertTrue(brief.exists())
        # Issue #725: worktree layouts inherit the base repo's .gitignore, so
        # the delivery guard is out of scope and writes nothing here.
        self.assertIsNone(result.gitignore_path)
        self.assertFalse((worker_dir / ".gitignore").exists())

    def test_apply_idempotent_when_worktree_already_registered(self):
        plan = self._build_self_edit_b(task_id="idem-task")
        worker_dir = Path(plan.layout.worker_dir)
        # Pre-create the worktree manually to simulate a partial / retry run.
        worker_dir.parent.mkdir(parents=True, exist_ok=True)
        import subprocess as _sp
        _sp.run(
            ["git", "-C", str(self.sb.claude_org_root),
             "worktree", "add", "-b", plan.layout.planned_branch,
             str(worker_dir)],
            check=True, capture_output=True,
        )
        # Apply must NOT raise and must not duplicate or replace it.
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        # Brief still lands in the worktree
        self.assertTrue((worker_dir / "CLAUDE.local.md").exists())

    def test_apply_aborts_when_worker_dir_has_unrelated_content(self):
        plan = self._build_self_edit_b(task_id="dirty-task")
        worker_dir = Path(plan.layout.worker_dir)
        worker_dir.mkdir(parents=True)
        (worker_dir / "stale.txt").write_text("garbage", encoding="utf-8")
        with self.assertRaises(gdp.WorktreeApplyError) as cm:
            gdp.apply_delegate_plan(
                plan,
                state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root,
                skip_settings=True,
            )
        self.assertIn("not a registered git worktree", str(cm.exception))
        # Brief was NOT written into the dirty dir.
        self.assertFalse((worker_dir / "CLAUDE.local.md").exists())

    def test_apply_aborts_do_not_leak_queued_db_row(self):
        """Codex Major: a failed _ensure_worktree must not leave behind a
        queued runs row, because resolve_worker_layout treats `queued` as an
        active run and would steer subsequent delegations onto Pattern B."""
        plan = self._build_self_edit_b(task_id="leak-task")
        worker_dir = Path(plan.layout.worker_dir)
        worker_dir.mkdir(parents=True)
        (worker_dir / "stale.txt").write_text("garbage", encoding="utf-8")
        with self.assertRaises(gdp.WorktreeApplyError):
            gdp.apply_delegate_plan(
                plan,
                state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root,
                skip_settings=True,
            )
        runs = self.sb.list_runs()
        self.assertFalse(
            any(r["task_id"] == "leak-task" for r in runs),
            f"queued row leaked after worktree abort: {runs}",
        )

    def test_apply_branches_off_default_ref_not_current_head(self):
        """Codex Blocker: the new worktree must branch off the default ref
        (origin/HEAD or main), not the base repo's currently-checked-out
        feature branch."""
        import subprocess as _sp
        # Make a commit on main so HEAD is non-empty, then check the base
        # repo out onto a feature branch with its own commit. apply must
        # still branch off main.
        base = self.sb.claude_org_root
        seed_file = base / "seed.txt"
        seed_file.write_text("seed", encoding="utf-8")
        _sp.run(["git", "-C", str(base), "add", "seed.txt"], check=True)
        _sp.run(["git", "-C", str(base), "commit", "-m", "seed", "-q"],
                check=True, env=self._git_env)
        main_sha = _sp.check_output(
            ["git", "-C", str(base), "rev-parse", "main"],
        ).decode().strip()
        # Refresh the simulated origin/main to point at the latest main tip,
        # since setUp captured an earlier commit before the seed file landed.
        _sp.run(
            ["git", "-C", str(base), "update-ref",
             "refs/remotes/origin/main", main_sha],
            check=True,
        )
        _sp.run(["git", "-C", str(base), "checkout", "-q", "-b", "feat/other"],
                check=True)
        feat_file = base / "feat.txt"
        feat_file.write_text("feat-only", encoding="utf-8")
        _sp.run(["git", "-C", str(base), "add", "feat.txt"], check=True)
        _sp.run(["git", "-C", str(base), "commit", "-m", "feat-only", "-q"],
                check=True, env=self._git_env)
        feat_sha = _sp.check_output(
            ["git", "-C", str(base), "rev-parse", "HEAD"],
        ).decode().strip()
        self.assertNotEqual(main_sha, feat_sha)

        plan = self._build_self_edit_b(task_id="default-ref-task")
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        worker_dir = Path(plan.layout.worker_dir)
        new_sha = _sp.check_output(
            ["git", "-C", str(worker_dir), "rev-parse", "HEAD"],
        ).decode().strip()
        self.assertEqual(
            new_sha, main_sha,
            "new worktree must branch off main, not the base repo's "
            "currently-checked-out feature branch",
        )
        # Sanity: the feat-only file must NOT appear in the new worktree.
        self.assertFalse((worker_dir / "feat.txt").exists())

    def test_apply_aborts_when_existing_worktree_on_wrong_branch(self):
        """Codex Round 2 Major: idempotent reuse must verify the existing
        worktree is on the planned branch — a stale partial-retry on a
        different branch must abort, not silently dispatch."""
        plan = self._build_self_edit_b(task_id="branch-mismatch")
        worker_dir = Path(plan.layout.worker_dir)
        import subprocess as _sp
        worker_dir.parent.mkdir(parents=True, exist_ok=True)
        # Pre-create on a different branch than planned_branch.
        _sp.run(
            ["git", "-C", str(self.sb.claude_org_root),
             "worktree", "add", "-b", "wrong-branch", str(worker_dir)],
            check=True, capture_output=True,
        )
        with self.assertRaises(gdp.WorktreeApplyError) as cm:
            gdp.apply_delegate_plan(
                plan,
                state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root,
                skip_settings=True,
            )
        self.assertIn("not the planned", str(cm.exception))

    def test_apply_aborts_when_no_origin_head(self):
        """Codex Round 2 + Round 3 Major: must abort when origin/HEAD is
        absent — never guess from local main/master (could be stale after
        a trunk-rename) and never fall back to HEAD (re-introduces the
        original Pattern-B bug)."""
        import subprocess as _sp
        base = self.sb.claude_org_root
        # Tear down the origin/HEAD setup from setUp so the resolver can't
        # find any authoritative trunk.
        _sp.run(["git", "-C", str(base), "symbolic-ref", "--delete",
                 "refs/remotes/origin/HEAD"], check=True)
        _sp.run(["git", "-C", str(base), "update-ref", "-d",
                 "refs/remotes/origin/main"], check=True)
        plan = self._build_self_edit_b(task_id="no-origin-head-task")
        with self.assertRaises(gdp.WorktreeApplyError) as cm:
            gdp.apply_delegate_plan(
                plan,
                state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root,
                skip_settings=True,
            )
        self.assertIn("origin/HEAD", str(cm.exception))
        # Neither DB row nor brief should exist.
        self.assertFalse(
            any(r["task_id"] == "no-origin-head-task"
                for r in self.sb.list_runs())
        )

    def test_apply_aborts_when_existing_worktree_in_detached_head(self):
        """Codex Round 3 Major: idempotent reuse must reject detached HEAD
        too, not just a different-named branch."""
        import subprocess as _sp
        plan = self._build_self_edit_b(task_id="detached-task")
        worker_dir = Path(plan.layout.worker_dir)
        worker_dir.parent.mkdir(parents=True, exist_ok=True)
        # Create as a detached worktree (no -b, no branch name).
        main_sha = _sp.check_output(
            ["git", "-C", str(self.sb.claude_org_root), "rev-parse", "main"],
        ).decode().strip()
        _sp.run(
            ["git", "-C", str(self.sb.claude_org_root),
             "worktree", "add", "--detach", str(worker_dir), main_sha],
            check=True, capture_output=True,
        )
        with self.assertRaises(gdp.WorktreeApplyError) as cm:
            gdp.apply_delegate_plan(
                plan,
                state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root,
                skip_settings=True,
            )
        self.assertIn("detached-HEAD", str(cm.exception))

    def test_apply_creates_plain_pattern_b_worktree_for_project_repo(self):
        """Non-self-edit Pattern B branches from the project's registered repo."""
        # Stand up a dedicated project repo on disk and re-seed the registry.
        import subprocess as _sp
        project_repo = Path(self._td.name) / "clock-app-repo"
        project_repo.mkdir()
        self._init_repo_with_origin_main(project_repo)
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| 時計アプリ | clock-app | {project_repo} | Web 時計 | デザイン |\n"
            f"| claude-org-ja | claude-org-ja | {self.sb.claude_org_root} | Self | スキル改善 |\n",
            encoding="utf-8",
        )
        # Force Pattern B by adding an active concurrent run on the project.
        self.sb.add_active_run(
            task_id="other-clock-task",
            project_slug="clock-app",
            worker_dir=str(self.sb.workers / "clock-app"),
        )
        plan = gdp.build_delegate_plan(
            task_id="plain-b-task",
            project_slug="clock-app",
            description="add a sparkline",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.layout.pattern_variant)
        self.assertEqual(
            Path(plan.base_repo).resolve(), project_repo.resolve()
        )
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        worker_dir = Path(plan.layout.worker_dir).resolve()
        # Worktree registered against the project repo (not claude-org).
        out = _sp.check_output(
            ["git", "-C", str(project_repo),
             "worktree", "list", "--porcelain"],
        ).decode("utf-8", errors="replace")
        registered = {
            Path(line[len("worktree "):].strip()).resolve()
            for line in out.splitlines() if line.startswith("worktree ")
        }
        self.assertIn(worker_dir, registered)
        self.assertTrue((worker_dir / "CLAUDE.md").exists())


class TestPatternBClaudeOrgRepoWorktreePlan(unittest.TestCase):
    """Issue #370: ``build_delegate_plan`` must populate ``base_repo`` for
    the ``claude_org_repo_worktree`` variant so ``apply`` can run
    ``git worktree add`` against the claude-org mirror clone instead of
    failing with ``no usable base repo could be determined``."""

    def setUp(self) -> None:
        try:
            import subprocess as _sp
            _sp.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=False)
        # Reset the auto-seeded registry to drop the synthesized
        # claude-org-ja row; otherwise it'd fight the claude-org detection
        # for the claude-org slug. (The registry has clock-app only.)
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| 時計アプリ | clock-app | - | Web 時計 | デザイン |\n",
            encoding="utf-8",
        )
        # claude_org_root needs to be a real git repo with a main branch
        # (the worktree-creation tests do the same dance).
        import os
        self._git_env = os.environ.copy()
        self._git_env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        # claude-org mirror clone with the canonical origin.
        self.clone = self.sb.workers / "claude-org"
        self.clone.mkdir()
        self._init_repo_with_origin_main(
            self.clone, "https://github.com/suisya-systems/claude-org.git"
        )

    def _init_repo_with_origin_main(self, base: Path, origin_url: str) -> None:
        import subprocess as _sp
        _sp.run(["git", "-C", str(base), "init", "-q", "-b", "main"], check=True)
        _sp.run(
            ["git", "-C", str(base), "remote", "add", "origin", origin_url],
            check=True,
        )
        _sp.run(
            ["git", "-C", str(base), "commit", "--allow-empty", "-m", "init", "-q"],
            check=True, env=self._git_env,
        )
        sha = _sp.check_output(
            ["git", "-C", str(base), "rev-parse", "main"]
        ).decode().strip()
        _sp.run(
            ["git", "-C", str(base), "update-ref",
             "refs/remotes/origin/main", sha],
            check=True,
        )
        _sp.run(
            ["git", "-C", str(base), "symbolic-ref",
             "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
            check=True,
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _force_pattern_b(self, slug: str) -> None:
        self.sb.add_active_run(
            task_id=f"prev-{slug}",
            project_slug=slug,
            worker_dir=str(self.clone),
        )

    def test_plan_for_claude_org_slug_populates_base_repo(self):
        """Issue #370 repro 1: slug=claude-org Pattern B used to leave
        base_repo=None, causing apply to raise WorktreeApplyError."""
        self._force_pattern_b("claude-org")
        plan = gdp.build_delegate_plan(
            task_id="en-issue-370-1",
            project_slug="claude-org",
            description="install runtime classify",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertEqual(plan.layout.pattern_variant, "claude_org_repo_worktree")
        self.assertIsNotNone(plan.base_repo)
        self.assertEqual(
            Path(plan.base_repo).resolve(), self.clone.resolve()
        )
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (self.clone / ".worktrees" / "en-issue-370-1").resolve(),
        )

    def test_plan_for_claude_org_en_slug_populates_base_repo(self):
        """Issue #370 repro 2: slug=claude-org-en used to land on Pattern C
        ephemeral; now Pattern B with base_repo anchored on the clone."""
        self._force_pattern_b("claude-org-en")
        plan = gdp.build_delegate_plan(
            task_id="en-issue-370-2",
            project_slug="claude-org-en",
            description="docs sweep",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertEqual(plan.layout.pattern_variant, "claude_org_repo_worktree")
        self.assertEqual(
            Path(plan.base_repo).resolve(), self.clone.resolve()
        )

    def test_plan_normalizes_alias_slug_to_canonical(self):
        """Codex Round 2 Major: alias slugs must be normalized to the
        post-migration canonical slug (``claude-org`` per migrate_workers
        PROJECT_RENAMES) before reaching state.db, otherwise the same
        physical repo's runs split across two project rows in the
        dashboard."""
        self._force_pattern_b("claude-org-en")
        plan = gdp.build_delegate_plan(
            task_id="alias-norm-task",
            project_slug="claude-org-en",
            description="test alias normalization",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        # Plan-level project_slug is the canonical form, regardless of input alias.
        self.assertEqual(plan.project_slug, "claude-org")

    def test_plan_rejects_variant_with_bad_worker_dir(self):
        """Codex Round 2 Major: pattern_variant=claude_org_repo_worktree with a
        worker_dir whose parent.parent is not a local git repo would silently
        run ``git worktree add`` against an unrelated directory. Reject."""
        with self.assertRaises(ValueError):
            gdp.build_delegate_plan(
                task_id="bad-shape-task",
                project_slug="claude-org",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={
                    "pattern": "B",
                    "pattern_variant": "claude_org_repo_worktree",
                    "worker_dir": str(Path(self._td.name) / "not" / "a-repo"),
                },
            )

    def test_plan_rejects_variant_pointing_at_unrelated_git_repo(self):
        """Codex Round 3 Major: a worker_dir whose parent.parent IS a git
        repo — but a different one — used to slip through the validation.
        Re-running origin-URL detection against the canonical claude-org clone
        path catches this."""
        import subprocess as _sp
        unrelated = Path(self._td.name) / "unrelated-repo"
        unrelated.mkdir()
        self._init_repo_with_origin_main(
            unrelated, "https://github.com/some-other-org/unrelated.git"
        )
        with self.assertRaises(ValueError):
            gdp.build_delegate_plan(
                task_id="evil-task",
                project_slug="claude-org",
                claude_org_root=self.sb.claude_org_root,
                state_db_path=self.sb.db_path,
                layout_overrides={
                    "pattern": "B",
                    "pattern_variant": "claude_org_repo_worktree",
                    "worker_dir": str(unrelated / ".worktrees" / "evil-task"),
                },
            )

    def test_plan_normalizes_alias_slug_for_pattern_a_too(self):
        """Codex Round 3 Major: Pattern A (no active concurrent run, variant
        None) on slug=claude-org-en still anchors worker_dir on the shared
        clone, so the same alias-split must be normalized for Pattern A,
        not just Pattern B."""
        # No add_active_run call — this is Pattern A.
        plan = gdp.build_delegate_plan(
            task_id="alias-norm-pattern-a",
            project_slug="claude-org-en",
            description="docs sync",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "A")
        self.assertEqual(plan.project_slug, "claude-org")

    def test_apply_creates_claude_org_repo_worktree(self):
        """End-to-end: the original repro (apply fails with `no usable
        base repo`) is gone — apply succeeds and registers the worktree
        against the claude-org clone."""
        import subprocess as _sp
        self._force_pattern_b("claude-org")
        plan = gdp.build_delegate_plan(
            task_id="en-issue-370-apply",
            project_slug="claude-org",
            description="add resolver fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        # Issue #480: apply now `git fetch origin` before branching. Detection
        # above needed the github origin URL, but the fetch must stay offline —
        # repoint origin at a local (empty) bare repo. The synthesized
        # origin/main remote-tracking ref survives the no-op fetch and is what
        # the worktree branches off (this test asserts registration, not fetch
        # freshness — see TestPatternBWorktreeFetchesStaleOrigin for that).
        upstream = Path(self._td.name) / "claude-org-upstream.git"
        _sp.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
        _sp.run(
            ["git", "-C", str(self.clone), "remote", "set-url", "origin",
             str(upstream)],
            check=True,
        )
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        worker_dir = Path(plan.layout.worker_dir).resolve()
        self.assertTrue(worker_dir.exists())
        self.assertTrue((worker_dir / ".git").exists())
        out = _sp.check_output(
            ["git", "-C", str(self.clone),
             "worktree", "list", "--porcelain"],
        ).decode("utf-8", errors="replace")
        registered = {
            Path(line[len("worktree "):].strip()).resolve()
            for line in out.splitlines() if line.startswith("worktree ")
        }
        self.assertIn(worker_dir, registered)
        self.assertTrue((worker_dir / "CLAUDE.md").exists())


# ---------------------------------------------------------------------------
# Issue #450: Pattern B base_repo fallback to workers_dir/<slug>
# ---------------------------------------------------------------------------


class TestPatternBUrlOnlyRegistryFallback(unittest.TestCase):
    """Issue #450: registry rows with a URL-only path (e.g. renga registered
    as ``| renga | renga | https://github.com/.../renga.git | ... |``) used to
    fall through all three base_repo branches in build_delegate_plan, leaving
    base_repo=None and causing apply to raise WorktreeApplyError. The fallback
    must pick up a manually-cloned local repo at ``workers_dir/<project_slug>``
    so Pattern B delegation works for URL-only registry entries."""

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        # Replace the auto-seeded registry with a URL-only row for ``renga``
        # so the build_delegate_plan path lookup yields a non-local path.
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| renga | renga | https://github.com/suisya-systems/renga.git "
            "| Renga | dev |\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _init_bare_repo(self, base: Path) -> None:
        base.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(base), "init", "-q"], check=True
        )

    def _init_repo_with_origin(self, base: Path, origin_url: str) -> None:
        base.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(base), "init", "-q"], check=True
        )
        subprocess.run(
            ["git", "-C", str(base), "remote", "add", "origin", origin_url],
            check=True,
        )

    def _force_pattern_b(self, slug: str) -> None:
        self.sb.add_active_run(
            task_id=f"prev-{slug}",
            project_slug=slug,
            worker_dir=str(self.sb.workers / slug),
        )

    def test_fallback_to_workers_dir_slug_when_registry_path_is_url(self):
        """workers_dir/<slug> with origin URL matching the registered github
        repo → base_repo resolves to that clone."""
        clone = self.sb.workers / "renga"
        self._init_repo_with_origin(
            clone, "https://github.com/suisya-systems/renga.git"
        )
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-fallback-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.layout.pattern_variant)
        self.assertIsNotNone(plan.base_repo)
        self.assertEqual(Path(plan.base_repo).resolve(), clone.resolve())

    def _set_registry(self, url: str) -> None:
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| renga | renga | {url} | Renga | dev |\n",
            encoding="utf-8",
        )

    def test_ssh_style_registry_url_still_enforces_origin_match(self):
        """Codex Round 3 Blocker: ``git@github.com:org/renga.git`` SSH-style
        registry entries used to bypass the github gate because the helper
        keyed on ``"://"``. ``_extract_github_repo_name`` accepts both forms
        so the gate must too — a bare-init clone under an SSH-registered
        slug must still be rejected."""
        self._set_registry("git@github.com:suisya-systems/renga.git")
        clone = self.sb.workers / "renga"
        self._init_bare_repo(clone)  # no origin
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-ssh-bare-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.base_repo)

    def test_explicit_port_ssh_url_still_enforces_origin_match(self):
        """Codex Round 4 Blocker: ``ssh://git@github.com:22/org/repo.git``
        (explicit-port SSH form) used to escape the github gate because the
        regex's ``[^/:\\s]+`` owner slot was eaten by the port digits, so
        ``_extract_github_repo_name`` returned None and origin matching was
        skipped. Regex now tolerates an optional ``:port``. Bare-init clone
        under such a registry entry must still be rejected."""
        self._set_registry("ssh://git@github.com:22/suisya-systems/renga.git")
        clone = self.sb.workers / "renga"
        self._init_bare_repo(clone)  # no origin
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-ssh-port-bare-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.base_repo)

    def test_ssh_style_registry_url_accepts_matching_origin(self):
        """SSH-registered renga + clone whose origin (https or ssh) resolves
        to the same github repo name → fallback accepts. Owner is
        intentionally unpinned so forks remain accepted, mirroring
        ``find_claude_org_clone``."""
        self._set_registry("git@github.com:suisya-systems/renga.git")
        clone = self.sb.workers / "renga"
        # Mismatched owner (fork), same repo name — must still match.
        self._init_repo_with_origin(
            clone, "https://github.com/happy-ryo/renga.git"
        )
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-ssh-match-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertEqual(Path(plan.base_repo).resolve(), clone.resolve())

    def test_fallback_rejected_when_clone_has_no_origin(self):
        """Codex Round 2 Blocker: a bare ``git init`` clone with no origin
        must not be accepted as a base for a github-registered project —
        otherwise any leftover same-named directory silently adopts the
        registered slug. ``origin`` URL must match the registered repo
        name for github URLs."""
        clone = self.sb.workers / "renga"
        self._init_bare_repo(clone)  # no origin remote
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-no-origin-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.base_repo)

    def test_no_fallback_when_workers_dir_slug_missing(self):
        """No directory at workers_dir/<slug> → base_repo stays None
        (existing apply-time error path preserved)."""
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-no-clone-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.base_repo)

    def test_fallback_rejected_when_clone_origin_url_mismatches_registry(self):
        """A leftover unrelated github repo at workers_dir/<slug> must not be
        adopted as the base — would redirect dispatch into the wrong repo
        (Issue #370 precedent). Origin URL repo-name match guards against it."""
        clone = self.sb.workers / "renga"
        self._init_repo_with_origin(
            clone, "https://github.com/some-other-org/not-renga.git"
        )
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-mismatch-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.base_repo)

    def test_pattern_b_override_uses_url_only_fallback_for_preflight(self):
        """Issue #450 consistency: ``--pattern B`` override preflight must
        accept the same workers_dir/<slug> base that auto-derived Pattern B
        uses, otherwise the same setup errors at preview only when forced."""
        clone = self.sb.workers / "renga"
        self._init_repo_with_origin(
            clone, "https://github.com/suisya-systems/renga.git"
        )
        # No add_active_run — this is the no-concurrent-run case where
        # ``--pattern B`` override is the only way to get Pattern B.
        plan = gdp.build_delegate_plan(
            task_id="renga-override-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={"pattern": "B"},
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertEqual(
            Path(plan.base_repo).resolve(), clone.resolve()
        )

    def test_no_fallback_when_workers_dir_slug_is_not_git_repo(self):
        """A plain directory (no .git) at workers_dir/<slug> must not be
        accepted as a base_repo — would yield "fatal: not a git repository"
        from git worktree add. Stay None and surface the existing error."""
        (self.sb.workers / "renga").mkdir()
        self._force_pattern_b("renga")
        plan = gdp.build_delegate_plan(
            task_id="renga-plain-dir-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertIsNone(plan.base_repo)


def _env_update_goldens() -> bool:
    import os
    return os.environ.get("UPDATE_GOLDENS") == "1"


# ---------------------------------------------------------------------------
# Issue #374: ``--pattern {A|B|C}`` override propagates into brief / send_plan
# ---------------------------------------------------------------------------


class TestPatternOverrideCLI(unittest.TestCase):
    """Issue #374: Secretary may force a specific pattern via ``--pattern``.
    The override must reach (a) the resolved layout, (b) the DELEGATE body
    rendering, and (c) the ``summary`` block in send_plan.json. Invalid
    combinations must surface as preview-time errors rather than after a DB
    reservation.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _common_args(self, *, slug: str = "clock-app") -> list[str]:
        return [
            "--task-id", "override-task",
            "--project-slug", slug,
            "--description", "force the pattern",
            "--claude-org-root", str(self.sb.claude_org_root),
            "--state-db-path", str(self.sb.db_path),
        ]

    def _run_preview_json(self, argv: list[str]) -> dict:
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main(["preview", *argv, "--json"])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def test_force_pattern_c_overrides_auto_a(self):
        """Without override, clock-app + no active run = Pattern A. With
        ``--pattern C`` the layout flips to ephemeral and planned_branch
        becomes None (Pattern C has no branch by contract)."""
        data = self._run_preview_json([*self._common_args(), "--pattern", "C"])
        s = data["summary"]
        self.assertEqual(s["pattern"], "C")
        self.assertIsNone(s["planned_branch"])
        # The DELEGATE body label reflects the override.
        self.assertIn("ディレクトリパターン: C", data["delegate_body"])

    def test_apply_writes_override_into_send_plan_summary(self):
        """End-to-end: apply with ``--pattern C`` produces a send_plan.json
        whose summary carries the overridden pattern (= what dispatcher
        will see when it copies the manifest into renga-peers)."""
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main([
                "apply", *self._common_args(),
                "--pattern", "C",
                "--skip-settings",
            ])
        self.assertEqual(rc, 0)
        # send_plan.json lives alongside the brief (Pattern C ephemeral
        # writes to workers_dir/<task_id>/CLAUDE.md).
        worker_dir = self.sb.workers / "override-task"
        send_plan_path = worker_dir / "send_plan.json"
        self.assertTrue(send_plan_path.exists())
        send_plan = json.loads(send_plan_path.read_text(encoding="utf-8"))
        self.assertEqual(send_plan["summary"]["pattern"], "C")

    def test_cli_pattern_drops_toml_worker_dir_and_variant(self):
        """Codex Round 2 Major: ``--pattern X`` together with ``--from-toml``
        used to keep the TOML's ``[worker].dir`` and ``[worker].pattern_variant``
        in the override dict, so the resolver treated worker_dir as
        explicitly set and skipped its pattern-driven re-derivation. Result
        was an inconsistent layout (e.g. ``pattern=C`` but worker_dir on
        the registered clone). The CLI flag must override fully."""
        # TOML pre-pins Pattern A worker_dir to a deterministic path.
        toml_path = self.sb.root / "in.toml"
        explicit_dir = self.sb.workers / "clock-app"
        toml_path.write_text(
            "[task]\n"
            'id = "toml-pattern"\n'
            'description = "drop dir on cli pattern"\n'
            "\n[worker]\n"
            f'dir = "{explicit_dir.as_posix()}"\n'
            'pattern = "A"\n'
            'role = "default"\n'
            'self_edit = false\n'
            "\n[project]\n"
            'name = "clock-app"\n'
            f'\n[paths]\nclaude_org = "{self.sb.claude_org_root.as_posix()}"\n',
            encoding="utf-8",
        )
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = gdp.main([
                "preview",
                "--from-toml", str(toml_path),
                "--state-db-path", str(self.sb.db_path),
                "--pattern", "C",
                "--json",
            ])
        self.assertEqual(rc, 0)
        s = json.loads(buf.getvalue())["summary"]
        # CLI pattern wins over TOML's pattern.
        self.assertEqual(s["pattern"], "C")
        # And worker_dir gets re-derived for the new pattern (workers_dir/<task_id>),
        # not left at the TOML-supplied registered clone path.
        self.assertEqual(
            Path(s["worker_dir"]).resolve(),
            (self.sb.workers / "toml-pattern").resolve(),
        )
        # Variant from TOML (or derived for old pattern) is dropped — Pattern
        # C ephemeral default.
        self.assertEqual(s["pattern_variant"], "ephemeral")

    def test_force_pattern_a_on_self_edit_slug_errors_in_preview(self):
        """Resolver rejects pattern=A on a self-edit slug — the error must
        propagate out of ``preview`` rather than letting the bad layout
        slip through."""
        # ResolveError is raised inside build_delegate_plan, which runs
        # under preview without reaching apply.
        from tools.resolve_worker_layout import ResolveError as _RE

        with self.assertRaises(_RE):
            gdp.main([
                "preview",
                *self._common_args(slug="claude-org-ja"),
                "--pattern", "A",
            ])


# ---------------------------------------------------------------------------
# Issue #480: apply must fetch origin before branching the Pattern B worktree
# ---------------------------------------------------------------------------


class TestFetchBaseOrigin(unittest.TestCase):
    """Unit coverage for :func:`gen_delegate_payload._fetch_base_origin`
    (Issue #480): refresh the base clone's remote-tracking refs before a
    Pattern B worktree branches off them, best-effort."""

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name) / "repo"
        self.repo.mkdir()
        subprocess.run(
            ["git", "-C", str(self.repo), "init", "-q", "-b", "main"],
            check=True,
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_skips_quietly_when_no_origin_remote(self):
        """No origin remote (local-only repo / synthetic-ref fixture) → no
        fetch attempt, no raise, no output."""
        import contextlib
        import io

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            # Must not raise even though there is nothing to fetch.
            gdp._fetch_base_origin(self.repo)
        self.assertEqual(err.getvalue(), "")

    def test_raises_when_fetch_fails(self):
        """An origin pointing at an unreachable (local, nonexistent) path must
        abort with WorktreeApplyError — branching off a possibly-stale
        origin/main is exactly the Issue #480 bug, so apply fails closed
        instead of silently proceeding."""
        bogus = Path(self._td.name) / "does-not-exist.git"
        subprocess.run(
            ["git", "-C", str(self.repo), "remote", "add", "origin",
             str(bogus)],
            check=True,
        )
        with self.assertRaises(gdp.WorktreeApplyError) as cm:
            gdp._fetch_base_origin(self.repo)
        self.assertIn("git fetch origin", str(cm.exception))
        self.assertIn("480", str(cm.exception))


class TestPatternBWorktreeFetchesStaleOrigin(unittest.TestCase):
    """Issue #480: apply must ``git fetch origin`` before branching the
    Pattern B worktree, so a base clone that has fallen behind origin still
    branches off the *latest* remote default-branch tip — not the stale local
    ``origin/main`` captured at the last fetch."""

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=False)
        import os
        self._git_env = os.environ.copy()
        self._git_env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        # A local non-bare repo plays the role of the GitHub remote; we commit
        # directly in its working tree to "advance origin" mid-test.
        self.upstream = Path(self._td.name) / "upstream"
        self.upstream.mkdir()
        self._git(self.upstream, "init", "-q", "-b", "main")
        (self.upstream / "trunk.txt").write_text("v1", encoding="utf-8")
        self._git(self.upstream, "add", "trunk.txt")
        self._git(self.upstream, "commit", "-q", "-m", "c1")
        self.c1 = self._rev(self.upstream, "main")
        # claude_org_root is a clone of upstream: a real origin remote,
        # origin/main + origin/HEAD set, local main == c1.
        base = self.sb.claude_org_root
        self._git(base, "init", "-q", "-b", "main")
        self._git(base, "remote", "add", "origin", str(self.upstream))
        self._git(base, "fetch", "-q", "origin")
        self._git(base, "symbolic-ref", "refs/remotes/origin/HEAD",
                  "refs/remotes/origin/main")
        self._git(base, "reset", "-q", "--hard", "origin/main")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, env=self._git_env,
        )

    def _rev(self, repo: Path, ref: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", ref],
        ).decode().strip()

    def _build_self_edit_b(self, *, task_id: str):
        return gdp.build_delegate_plan(
            task_id=task_id,
            project_slug="claude-org-ja",
            description="self-edit pattern B",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={
                "pattern": "B",
                "pattern_variant": "live_repo_worktree",
                "role": "claude-org-self-edit",
                "self_edit": True,
            },
        )

    def test_apply_fetches_so_worktree_branches_off_latest_origin(self):
        # Advance the upstream trunk to c2 AFTER the base clone last fetched,
        # so the base's local origin/main is now stale (still c1).
        (self.upstream / "trunk.txt").write_text("v2", encoding="utf-8")
        self._git(self.upstream, "add", "trunk.txt")
        self._git(self.upstream, "commit", "-q", "-m", "c2")
        c2 = self._rev(self.upstream, "main")
        self.assertNotEqual(self.c1, c2)
        # Precondition: the base clone is stale — origin/main still points at c1.
        self.assertEqual(
            self._rev(self.sb.claude_org_root, "refs/remotes/origin/main"),
            self.c1,
        )

        plan = self._build_self_edit_b(task_id="stale-origin-task")
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )

        worker_dir = Path(plan.layout.worker_dir)
        self.assertEqual(
            self._rev(worker_dir, "HEAD"), c2,
            "apply must `git fetch origin` and branch the worktree off the "
            "latest origin tip (c2), not the base clone's stale origin/main "
            "(c1)",
        )
        # The worktree reflects c2's content, not c1's.
        self.assertEqual(
            (worker_dir / "trunk.txt").read_text(encoding="utf-8"), "v2",
        )
        # The fetch also advanced the base clone's own origin/main.
        self.assertEqual(
            self._rev(self.sb.claude_org_root, "refs/remotes/origin/main"), c2,
        )

    def test_apply_aborts_when_fetch_fails_without_leaking_db_row(self):
        """Fail-closed: an origin that cannot be fetched aborts apply rather
        than branching off a possibly-stale ref (Issue #480). The abort runs
        before the DB reservation, so no queued run row leaks (which would
        otherwise steer the next delegation onto another Pattern B branch)."""
        # Repoint origin at a nonexistent path so the pre-branch fetch fails.
        self._git(self.sb.claude_org_root, "remote", "set-url", "origin",
                  str(Path(self._td.name) / "gone.git"))
        plan = self._build_self_edit_b(task_id="fetch-fail-task")
        with self.assertRaises(gdp.WorktreeApplyError) as cm:
            gdp.apply_delegate_plan(
                plan,
                state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root,
                skip_settings=True,
            )
        self.assertIn("480", str(cm.exception))
        self.assertFalse(
            any(r["task_id"] == "fetch-fail-task" for r in self.sb.list_runs()),
            "queued row leaked after fetch-failure abort",
        )
        # No worktree was created.
        self.assertFalse(Path(plan.layout.worker_dir).exists())


# ---------------------------------------------------------------------------
# Issue #489: preview warnings/blocking_warnings surface + atomic apply
# rollback when post-reservation steps fail (Codex review (d) + Blocker 2)
# ---------------------------------------------------------------------------


class TestIssue489PreviewWarnings(unittest.TestCase):
    """Issue #489 (d): preview must surface

    - ``warnings``: non-blocking notes (legacy ``_repo_clone`` layout in use).
    - ``blocking_warnings``: layout integrity refused apply (non-git
      ``workers/<slug>/`` with Pattern A residue and no usable base).

    Both are exposed in the preview JSON at the top level (and mirrored
    in ``summary``); the human preview emits them to stderr and exits
    nonzero on blocking_warnings. ``apply`` raises
    :class:`gdp.BlockingPreviewWarningError` rather than touching the
    DB / filesystem.
    """

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        # URL-only registry for renga — the Issue #489 motivating shape.
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| renga | renga | https://github.com/suisya-systems/renga.git "
            "| Renga | dev |\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _clone(self, path: Path, origin: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(path), "init", "-q"], check=True
        )
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", origin],
            check=True,
        )

    def test_non_git_workers_slug_with_residue_emits_blocking_warning(self):
        """``workers/renga/`` is a plain directory holding leftover
        Pattern-A artifacts (CLAUDE.md / send_plan.json), and no
        ``_repo_clone`` fallback exists — apply would be ambiguous, so
        preview refuses."""
        slug_dir = self.sb.workers / "renga"
        slug_dir.mkdir()
        (slug_dir / "CLAUDE.md").write_text("old brief", encoding="utf-8")
        (slug_dir / "send_plan.json").write_text("{}", encoding="utf-8")
        plan = gdp.build_delegate_plan(
            task_id="renga-blocked-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertTrue(plan.blocking_warnings)
        self.assertTrue(
            any("Pattern-A residue" in w for w in plan.blocking_warnings)
        )
        # Summary mirrors top-level + base_repo is None for this shape.
        self.assertIsNone(plan.base_repo)
        summary = plan.to_summary_dict()
        self.assertEqual(summary["blocking_warnings"], plan.blocking_warnings)

    def test_legacy_repo_clone_emits_non_blocking_deprecation_warning(self):
        """``_repo_clone`` legacy fallback resolved → preview ships a
        non-blocking deprecation warning so the deployment can migrate
        to the canonical ``workers/<slug>/`` layout. Apply still
        proceeds (warnings, not blocking_warnings)."""
        slug_dir = self.sb.workers / "renga"
        slug_dir.mkdir()
        (slug_dir / "README.md").write_text("loose note", encoding="utf-8")
        legacy = slug_dir / "_repo_clone"
        self._clone(legacy, "https://github.com/suisya-systems/renga.git")
        plan = gdp.build_delegate_plan(
            task_id="renga-legacy-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertFalse(plan.blocking_warnings)
        self.assertTrue(plan.warnings)
        self.assertTrue(any("_repo_clone" in w for w in plan.warnings))
        # Codex Round 1 follow-up: Pattern A now carries ``base_repo`` when
        # routed through the new unified ``<base>/.worktrees/<task>/``
        # layout — without it, apply's ``_ensure_worktree`` would skip
        # ``git worktree add`` for Pattern A entirely.
        self.assertEqual(plan.layout.pattern, "A")
        self.assertIsNotNone(plan.base_repo)
        self.assertEqual(Path(plan.base_repo).resolve(), legacy.resolve())
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (legacy / ".worktrees" / "renga-legacy-task").resolve(),
        )

    def test_preview_json_exposes_warnings_at_top_level(self):
        """JSON consumers (jq pipelines, dispatcher previews) read
        ``warnings`` / ``blocking_warnings`` at the top level."""
        slug_dir = self.sb.workers / "renga"
        slug_dir.mkdir()
        (slug_dir / "CLAUDE.md").write_text("old brief", encoding="utf-8")
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO

        out = StringIO()
        err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = gdp.main([
                "preview",
                "--task-id", "renga-blocked-cli",
                "--project-slug", "renga",
                "--description", "alt+p ux fix",
                "--claude-org-root", str(self.sb.claude_org_root),
                "--state-db-path", str(self.sb.db_path),
                "--json",
            ])
        self.assertEqual(rc, 3, msg=f"stderr={err.getvalue()!r}")
        data = json.loads(out.getvalue())
        self.assertIn("blocking_warnings", data)
        self.assertTrue(data["blocking_warnings"])
        # Mirrored in summary too.
        self.assertEqual(
            data["blocking_warnings"], data["summary"]["blocking_warnings"]
        )
        # JSON path keeps stderr clean — the warning content is already in
        # the JSON body. The human path (no ``--json``) separately writes
        # a ``BLOCKING:`` marker to stderr; covered by
        # :meth:`test_human_preview_writes_blocking_marker_to_stderr`.
        self.assertEqual(err.getvalue(), "")

    def test_human_preview_writes_blocking_marker_to_stderr(self):
        """Human preview path (no ``--json``) emits a ``BLOCKING:`` line
        per blocking_warning to stderr so a wrapper script can grep for
        the marker. Exit code is 3, matching the JSON path."""
        slug_dir = self.sb.workers / "renga"
        slug_dir.mkdir()
        (slug_dir / "CLAUDE.md").write_text("old brief", encoding="utf-8")
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO

        out = StringIO()
        err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = gdp.main([
                "preview",
                "--task-id", "renga-blocked-human",
                "--project-slug", "renga",
                "--description", "alt+p ux fix",
                "--claude-org-root", str(self.sb.claude_org_root),
                "--state-db-path", str(self.sb.db_path),
            ])
        self.assertEqual(rc, 3)
        self.assertIn("BLOCKING:", err.getvalue())

    def test_apply_refuses_when_plan_has_blocking_warnings(self):
        """``apply_delegate_plan`` must not touch DB / filesystem when
        the plan carries blocking_warnings — raise
        :class:`BlockingPreviewWarningError` BEFORE the DB reservation
        so no queued row leaks."""
        slug_dir = self.sb.workers / "renga"
        slug_dir.mkdir()
        (slug_dir / "CLAUDE.md").write_text("old brief", encoding="utf-8")
        plan = gdp.build_delegate_plan(
            task_id="renga-blocked-apply",
            project_slug="renga",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        with self.assertRaises(gdp.BlockingPreviewWarningError):
            gdp.apply_delegate_plan(
                plan,
                state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root,
                skip_settings=True,
            )
        runs = self.sb.list_runs()
        self.assertFalse(
            any(r["task_id"] == "renga-blocked-apply" for r in runs),
            f"queued row leaked after blocking-warning abort: {runs}",
        )

    def test_local_path_registry_with_residue_is_not_blocking(self):
        """Codex Round 3 Major regression: a project registered with a
        real local clone path (``project.path = /repos/clock-app``) gets
        ``base_repo`` derived from the registered path, NOT from
        ``find_workers_dir_clone``. The blocking_warning logic must use
        ``base_repo`` as the authoritative "is there a usable base?"
        signal — otherwise leftover residue in ``workers/<slug>/`` would
        false-positive even though apply could safely run worktree-add
        from the registered local clone."""
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        local_repo = Path(self._td.name) / "registered-clock-app"
        local_repo.mkdir()
        subprocess.run(
            ["git", "-C", str(local_repo), "init", "-q"], check=True
        )
        # Registry row points at the registered local clone (NOT at
        # workers/<slug>/). This is the existing
        # "test_apply_creates_plain_pattern_b_worktree_for_project_repo"
        # shape with the extra wrinkle that workers/clock-app/ has
        # leftover Pattern-A residue from an earlier dispatch.
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| 時計 | clock-app | {local_repo} | Demo clock | - |\n",
            encoding="utf-8",
        )
        # Force Pattern B so the registered local path drives base_repo.
        self.sb.add_active_run(
            task_id="prev-clock-task",
            project_slug="clock-app",
            worker_dir=str(self.sb.workers / "clock-app"),
        )
        # Leftover Pattern-A residue in workers/clock-app/.
        residue_dir = self.sb.workers / "clock-app"
        residue_dir.mkdir(exist_ok=True)
        (residue_dir / "CLAUDE.md").write_text("old", encoding="utf-8")
        plan = gdp.build_delegate_plan(
            task_id="clock-with-residue",
            project_slug="clock-app",
            description="non-blocking residue regression",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertEqual(Path(plan.base_repo).resolve(), local_repo.resolve())
        # Residue + leftover workers/<slug>/ exists, but base_repo is
        # the registered local clone — no blocking_warning fires.
        self.assertEqual(plan.blocking_warnings, [])

    def test_no_warnings_when_canonical_clone_present(self):
        """The clean Issue #489 canonical layout — clone at
        ``workers/renga/`` directly — emits neither a deprecation
        warning nor a blocker."""
        self._clone(
            self.sb.workers / "renga",
            "https://github.com/suisya-systems/renga.git",
        )
        plan = gdp.build_delegate_plan(
            task_id="renga-canonical-task",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.warnings, [])
        self.assertEqual(plan.blocking_warnings, [])


class TestIssue489PatternAWorktreeCreation(unittest.TestCase):
    """Issue #489 Codex Round 1 Blocker follow-up: when Pattern A resolves
    to the unified ``<base>/.worktrees/<task>/`` layout (i.e. a real base
    clone is detected at ``workers/<slug>/`` or its legacy ``_repo_clone``
    subdir), ``apply`` must actually run ``git worktree add`` against
    that base — not just mkdir an empty directory. ``base_repo`` is
    populated on the plan so :func:`_ensure_worktree` fires for Pattern A
    too."""

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        # Suppress sandbox-level git init so the per-test seed picks the
        # ``main`` branch deterministically.
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=False)
        import os
        self._git_env = os.environ.copy()
        self._git_env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        # Replace the auto-seeded registry with a renga URL-only row.
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| renga | renga | https://github.com/suisya-systems/renga.git "
            "| Renga | dev |\n",
            encoding="utf-8",
        )
        # Build a local upstream whose filesystem path *contains* a
        # ``github.com/suisya-systems/renga.git`` suffix so that the
        # origin URL passes :func:`_origin_matches_registered`'s github
        # gate AND ``git fetch origin`` succeeds locally. The gate uses
        # a non-anchored regex search so the leading directory prefix
        # is harmless; the trailing path is what matters.
        self.upstream = (
            Path(self._td.name)
            / "github.com"
            / "suisya-systems"
            / "renga.git"
        )
        self.upstream.mkdir(parents=True)
        self._git(self.upstream, "init", "-q", "-b", "main")
        (self.upstream / "trunk.txt").write_text("v1", encoding="utf-8")
        self._git(self.upstream, "add", "trunk.txt")
        self._git(self.upstream, "commit", "-q", "-m", "c1")
        # Clone (canonical layout) lives at workers/renga/. Point its
        # origin at the github-named local upstream so the gate accepts
        # AND the fetch can refresh remote-tracking refs.
        self.clone = self.sb.workers / "renga"
        self._git_init_match_origin(self.clone, str(self.upstream))
        self._git(self.clone, "fetch", "-q", "origin")
        self._git(self.clone, "symbolic-ref", "refs/remotes/origin/HEAD",
                  "refs/remotes/origin/main")
        self._git(self.clone, "reset", "-q", "--hard", "origin/main")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, env=self._git_env,
        )

    def _git_init_match_origin(self, repo: Path, origin_url: str) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "remote", "add", "origin", origin_url)

    def test_apply_creates_pattern_a_worktree_when_base_clone_present(self):
        """Plan must populate ``base_repo`` for Pattern A so apply runs
        ``git worktree add`` against the canonical clone — without this
        the worker lands in an empty ``<base>/.worktrees/<task>/``
        directory rather than a real git checkout (Codex Round 1
        Blocker)."""
        plan = gdp.build_delegate_plan(
            task_id="renga-pattern-a",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "A")
        self.assertIsNotNone(plan.base_repo)
        self.assertEqual(Path(plan.base_repo).resolve(), self.clone.resolve())
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (self.clone / ".worktrees" / "renga-pattern-a").resolve(),
        )
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        worker_dir = Path(plan.layout.worker_dir)
        self.assertTrue(worker_dir.exists())
        # A real worktree has a ``.git`` entry (file or directory).
        self.assertTrue((worker_dir / ".git").exists())
        # The clone treats it as a registered worktree.
        out = subprocess.check_output(
            ["git", "-C", str(self.clone),
             "worktree", "list", "--porcelain"],
        ).decode("utf-8", errors="replace")
        registered = {
            Path(line[len("worktree "):].strip()).resolve()
            for line in out.splitlines() if line.startswith("worktree ")
        }
        self.assertIn(worker_dir.resolve(), registered)
        # Brief lands inside the worktree (not in workers/renga/ root).
        self.assertTrue((worker_dir / "CLAUDE.md").exists())
        self.assertFalse((self.sb.workers / "renga" / "CLAUDE.md").is_file())

    def test_pattern_a_worktree_surfaces_sandbox_pattern_b_to_runtime(self):
        """Codex Round 3 Blocker: ``settings_args["pattern"]`` is what
        ``claude-org-runtime settings generate`` keys ``sandbox_by_pattern``
        off. Pattern A's unified worktree layout needs Pattern B's
        Git-metadata carve-outs (``<base>/.git/worktrees/<task>/``,
        objects, branch ref, packed-refs) — without the override the
        runtime selects A's sandbox and git commits inside the worktree
        fail under bwrap. ``layout.pattern`` stays at ``A`` (first-run
        label / DB row), only ``settings_args["pattern"]`` flips to B."""
        plan = gdp.build_delegate_plan(
            task_id="renga-sandbox-flip",
            project_slug="renga",
            description="alt+p ux fix",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "A")
        self.assertEqual(plan.settings_args["pattern"], "B")
        # base-clone is surfaced too so the runtime can substitute
        # ``{base_clone}`` in B's sandbox body.
        self.assertEqual(
            plan.settings_args["base-clone"], str(self.clone.resolve())
        )
        # The cmd builder honors the override.
        cmd = gdp._build_settings_generate_cmd(
            plan.settings_args, runtime_cmd="claude-org-runtime"
        )
        self.assertEqual(cmd[cmd.index("--pattern") + 1], "B")
        self.assertEqual(
            cmd[cmd.index("--base-clone") + 1], str(self.clone.resolve())
        )

    def test_apply_skips_worktree_for_pattern_a_legacy_direct(self):
        """When no base clone is detected (e.g. ``-`` placeholder
        registry row, or a clone is simply missing), Pattern A keeps the
        legacy ``workers/<slug>/`` direct layout and apply must NOT try
        to ``git worktree add`` — ``base_repo`` stays None and
        ``_ensure_worktree`` returns early."""
        # Reset registry to a `-` placeholder so find_workers_dir_clone
        # cannot resolve the renga clone we set up in setUp.
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| 時計 | clock-app | - | Demo clock | - |\n",
            encoding="utf-8",
        )
        plan = gdp.build_delegate_plan(
            task_id="clock-legacy",
            project_slug="clock-app",
            description="legacy direct",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "A")
        self.assertIsNone(plan.base_repo)
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (self.sb.workers / "clock-app").resolve(),
        )
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        # Brief landed in the direct workers/<slug>/ — no worktree
        # was created (no .git in workers/clock-app), but apply also
        # did not fail.
        wd = Path(plan.layout.worker_dir)
        self.assertTrue((wd / "CLAUDE.md").exists())
        self.assertFalse((wd / ".git").exists())


class TestIssue489OverrideWorkerDirAlignment(unittest.TestCase):
    """Issue #489 Codex Round 1 Major follow-up: ``--pattern A``/``B``
    override must consult :func:`find_workers_dir_clone` so the
    override-driven worker_dir lands on the same base the auto-derive
    would pick (canonical ``workers/<slug>/`` OR legacy
    ``workers/<slug>/_repo_clone/``). Previously the override
    hardcoded ``workers/<slug>/.worktrees/`` which diverged whenever
    the base lived under the legacy subdir."""

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| renga | renga | https://github.com/suisya-systems/renga.git "
            "| Renga | dev |\n",
            encoding="utf-8",
        )
        # Legacy layout: clone parked under workers/renga/_repo_clone/.
        (self.sb.workers / "renga").mkdir()
        (self.sb.workers / "renga" / "README.md").write_text(
            "loose note", encoding="utf-8"
        )
        self.legacy_clone = self.sb.workers / "renga" / "_repo_clone"
        self.legacy_clone.mkdir()
        subprocess.run(
            ["git", "-C", str(self.legacy_clone), "init", "-q"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.legacy_clone), "remote", "add",
             "origin", "https://github.com/suisya-systems/renga.git"],
            check=True,
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_force_pattern_b_override_uses_repo_clone_base_path(self):
        from tools import resolve_worker_layout as rwl

        layout = rwl.resolve(
            task_id="override-legacy-b",
            project_slug="renga",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={"pattern": "B"},
        )
        self.assertEqual(layout.pattern, "B")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.legacy_clone / ".worktrees" / "override-legacy-b").resolve(),
        )

    def test_force_pattern_a_override_uses_repo_clone_base_path(self):
        from tools import resolve_worker_layout as rwl

        layout = rwl.resolve(
            task_id="override-legacy-a",
            project_slug="renga",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            layout_overrides={"pattern": "A"},
        )
        self.assertEqual(layout.pattern, "A")
        self.assertEqual(
            Path(layout.worker_dir),
            (self.legacy_clone / ".worktrees" / "override-legacy-a").resolve(),
        )


class TestIssue489AtomicApplyRollback(unittest.TestCase):
    """Issue #489 Blocker 2: ``_reserve_in_db`` commits the queued run
    row before ``_write_brief`` / ``_run_settings_generate`` /
    ``_write_send_plan`` run. A failure in any of those post-reservation
    steps leaves the queued row in place; resolver then sees Pattern A
    as occupied on the next dispatch and silently flips to Pattern B.
    The atomic-rollback layer wraps the post-reservation block and
    compensates with ``status='abandoned'`` on failure so the leak is
    closed."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _build_plain_a(self, task_id: str = "atomic-task") -> gdp.DelegatePlan:
        return gdp.build_delegate_plan(
            task_id=task_id,
            project_slug="clock-app",
            description="atomic apply",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )

    def test_write_brief_failure_compensates_queued_run(self):
        """Patch ``_write_brief`` to raise after the DB reservation
        commits. Expect: the exception propagates, and the queued run
        row is flipped to ``abandoned`` so a follow-up dispatch sees
        Pattern A as free."""
        plan = self._build_plain_a()
        boom = RuntimeError("simulated disk failure during brief write")
        from unittest.mock import patch

        with patch.object(gdp, "_write_brief", side_effect=boom):
            with self.assertRaises(RuntimeError) as cm:
                gdp.apply_delegate_plan(
                    plan,
                    state_db_path=self.sb.db_path,
                    claude_org_root=self.sb.claude_org_root,
                    skip_settings=True,
                )
        self.assertIs(cm.exception, boom)
        runs = self.sb.list_runs()
        match = [r for r in runs if r["task_id"] == "atomic-task"]
        self.assertEqual(len(match), 1, runs)
        self.assertEqual(match[0]["status"], "abandoned")

    def test_send_plan_write_failure_compensates_queued_run(self):
        """Same contract for ``_write_send_plan`` — the last
        post-reservation side effect must also be guarded."""
        plan = self._build_plain_a(task_id="send-plan-fail")
        boom = OSError("permission denied")
        from unittest.mock import patch

        with patch.object(gdp, "_write_send_plan", side_effect=boom):
            with self.assertRaises(OSError):
                gdp.apply_delegate_plan(
                    plan,
                    state_db_path=self.sb.db_path,
                    claude_org_root=self.sb.claude_org_root,
                    skip_settings=True,
                )
        match = [r for r in self.sb.list_runs() if r["task_id"] == "send-plan-fail"]
        self.assertEqual(match[0]["status"], "abandoned")

    def test_successful_apply_leaves_queued_run_intact(self):
        """Sanity guard: on the happy path the queued row stays queued
        (the rollback layer must be a no-op when nothing fails). The
        next dispatcher T2 promotes it to ``in_use``."""
        plan = self._build_plain_a(task_id="happy-task")
        gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        match = [r for r in self.sb.list_runs() if r["task_id"] == "happy-task"]
        self.assertEqual(match[0]["status"], "queued")


# ---------------------------------------------------------------------------
# Issue #709: apply materializes the base clone for URL-only new projects
# ---------------------------------------------------------------------------


class TestIssue709BaseCloneMaterialization(unittest.TestCase):
    """Issue #709: a registry row with a remote URL and no local clone yet
    used to dispatch Pattern A *legacy-direct* — the brief landed in a non-git
    ``workers/<slug>/`` dir and the worker was blocked on the first git op.
    apply must now ``git clone`` the base into the canonical
    ``workers/<slug>/`` and cut ``<clone>/.worktrees/<task>/`` off it. The
    hermetic ``URL`` is a local *bare* repo (no network)."""

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        # with_claude_org_origin=False so the sandbox git-init doesn't race
        # our per-test seeds (matches the other worktree-creation classes).
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=False)
        import os
        self._git_env = os.environ.copy()
        self._git_env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            env=self._git_env,
        )

    def _make_bare_remote(
        self, name: str, *, claude_md: str | None = None
    ) -> Path:
        """Build a bare repo (stands in for the registered remote URL) with
        one commit on ``main``; optionally seed a tracked ``CLAUDE.md``."""
        src = Path(self._td.name) / f"{name}-src"
        src.mkdir()
        self._git(src, "init", "-q", "-b", "main")
        (src / "README.md").write_text("hi\n", encoding="utf-8")
        if claude_md is not None:
            (src / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
        self._git(src, "add", "-A")
        self._git(src, "commit", "-q", "-m", "init")
        bare = Path(self._td.name) / f"{name}.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(src), str(bare)],
            check=True, capture_output=True, env=self._git_env,
        )
        return bare

    def _set_url_registry(self, slug: str, url: str) -> None:
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| {slug} | {slug} | {url} | Remote proj | dev |\n",
            encoding="utf-8",
        )

    def _build(self, *, task_id: str, slug: str = "newproj"):
        return gdp.build_delegate_plan(
            task_id=task_id,
            project_slug=slug,
            description="first dispatch",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )

    def test_plan_records_pending_clone_and_canonical_worktree_layout(self):
        bare = self._make_bare_remote("newproj")
        self._set_url_registry("newproj", str(bare))
        plan = self._build(task_id="np-plan")
        # Legacy-direct is gone: worker_dir is the canonical worktree layout.
        clone_target = (self.sb.workers / "newproj").resolve()
        self.assertEqual(plan.layout.pattern, "A")
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (clone_target / ".worktrees" / "np-plan").resolve(),
        )
        self.assertIsNotNone(plan.base_repo)
        self.assertEqual(Path(plan.base_repo).resolve(), clone_target)
        # Pending clone points at the registered URL + canonical target.
        self.assertIsNotNone(plan.pending_clone)
        self.assertEqual(plan.pending_clone.url, str(bare))
        self.assertEqual(
            Path(plan.pending_clone.target).resolve(), clone_target
        )
        # Sandbox pattern flips to B (worktree carve-outs) like Issue #489.
        self.assertEqual(plan.settings_args["pattern"], "B")
        self.assertEqual(
            plan.settings_args["base-clone"], str(clone_target)
        )
        # The render config's worker dir tracks the rewritten worktree path
        # (not the pre-rewrite base-clone root), so the brief tells the worker
        # to work in the worktree, and the rendered brief agrees.
        self.assertEqual(
            Path(plan.config["worker"]["dir"]),
            (clone_target / ".worktrees" / "np-plan").resolve(),
        )
        brief = gwb.render(plan.config)
        self.assertIn(
            str((clone_target / ".worktrees" / "np-plan").resolve()), brief
        )
        self.assertNotIn(f"作業ディレクトリ: `{clone_target}`", brief)

    def test_plan_is_pure_no_clone_placed(self):
        bare = self._make_bare_remote("newproj")
        self._set_url_registry("newproj", str(bare))
        self._build(task_id="np-pure")
        # build_delegate_plan must not clone — the target stays absent.
        self.assertFalse((self.sb.workers / "newproj").exists())

    def test_apply_clones_base_and_creates_worktree(self):
        bare = self._make_bare_remote("newproj")
        self._set_url_registry("newproj", str(bare))
        plan = self._build(task_id="np-apply")
        result = gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        clone_target = self.sb.workers / "newproj"
        # The base clone was materialized as a real git repo.
        self.assertTrue((clone_target / ".git").exists())
        # The worktree is a real checkout (has a .git file) registered
        # against the clone.
        worker_dir = Path(plan.layout.worker_dir)
        self.assertTrue(worker_dir.exists())
        self.assertTrue((worker_dir / ".git").exists())
        out = subprocess.check_output(
            ["git", "-C", str(clone_target),
             "worktree", "list", "--porcelain"],
        ).decode("utf-8", errors="replace")
        registered = {
            Path(line[len("worktree "):].strip()).resolve()
            for line in out.splitlines() if line.startswith("worktree ")
        }
        self.assertIn(worker_dir.resolve(), registered)
        # Brief landed inside the worktree; the run is queued Pattern A.
        self.assertTrue(result.brief_path.exists())
        self.assertEqual(result.brief_path.parent.resolve(), worker_dir.resolve())
        row = [r for r in self.sb.list_runs() if r["task_id"] == "np-apply"][0]
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["pattern"], "A")

    def test_apply_is_idempotent_on_reapply(self):
        bare = self._make_bare_remote("newproj")
        self._set_url_registry("newproj", str(bare))
        plan = self._build(task_id="np-idem")
        gdp.apply_delegate_plan(
            plan, state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root, skip_settings=True,
        )
        # Second apply must not re-clone or duplicate the worktree.
        gdp.apply_delegate_plan(
            plan, state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root, skip_settings=True,
        )
        clone_target = self.sb.workers / "newproj"
        out = subprocess.check_output(
            ["git", "-C", str(clone_target),
             "worktree", "list", "--porcelain"],
        ).decode("utf-8", errors="replace")
        worktrees = [
            line for line in out.splitlines() if line.startswith("worktree ")
        ]
        # main clone + the one task worktree = 2 entries, no duplicate.
        self.assertEqual(len(worktrees), 2)

    def test_apply_clone_failure_is_blocking_and_leaks_no_db_row(self):
        # A bogus (non-existent) local repo path is a clone URL that git
        # cannot resolve — hermetic, no network.
        bogus = str(Path(self._td.name) / "does-not-exist.git")
        self._set_url_registry("newproj", bogus)
        plan = self._build(task_id="np-fail")
        self.assertIsNotNone(plan.pending_clone)
        with self.assertRaises(gdp.BaseCloneApplyError) as cm:
            gdp.apply_delegate_plan(
                plan, state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root, skip_settings=True,
            )
        self.assertIn("git clone", str(cm.exception))
        # BaseCloneApplyError is a WorktreeApplyError subclass (callers that
        # catch the base type keep working).
        self.assertIsInstance(cm.exception, gdp.WorktreeApplyError)
        # No queued row leaked (clone failed before the DB reservation).
        self.assertFalse(
            any(r["task_id"] == "np-fail" for r in self.sb.list_runs())
        )

    def test_apply_rejects_unrelated_repo_appearing_at_target(self):
        # Race guard (Issue #370): a plan is built while the target is absent
        # (pending clone), then an UNRELATED github-origin repo appears at
        # workers/<slug>/ before apply. The idempotent-reuse path must revalidate
        # the origin against the registered URL and refuse, not dispatch against
        # the wrong repo. Registered URL is github so the origin gate is strict;
        # apply never reaches a network clone because the target is a git repo.
        self._set_url_registry(
            "newproj", "https://github.com/suisya-systems/newproj.git"
        )
        plan = self._build(task_id="np-race")
        self.assertIsNotNone(plan.pending_clone)
        # An unrelated repo lands at the canonical target after planning.
        target = self.sb.workers / "newproj"
        target.mkdir(parents=True)
        self._git(target, "init", "-q", "-b", "main")
        self._git(target, "remote", "add", "origin",
                  "https://github.com/some-other-org/unrelated.git")
        with self.assertRaises(gdp.BaseCloneApplyError) as cm:
            gdp.apply_delegate_plan(
                plan, state_db_path=self.sb.db_path,
                claude_org_root=self.sb.claude_org_root, skip_settings=True,
            )
        self.assertIn("does not match", str(cm.exception))
        self.assertFalse(
            any(r["task_id"] == "np-race" for r in self.sb.list_runs())
        )

    def test_placeholder_dash_path_stays_legacy_direct_no_pending_clone(self):
        # ``-`` is a placeholder, not a URL — must NOT trigger auto-clone
        # (existing legacy-direct behavior preserved).
        self._set_url_registry("newproj", "-")
        plan = self._build(task_id="np-dash")
        self.assertEqual(plan.layout.pattern, "A")
        self.assertIsNone(plan.pending_clone)
        self.assertIsNone(plan.base_repo)
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (self.sb.workers / "newproj").resolve(),
        )

    def test_url_registry_with_residue_stays_blocking_no_pending_clone(self):
        # Residue in workers/<slug>/ must keep the Issue #489 BLOCKING path
        # (auto-clone only fires into an empty/absent target).
        bare = self._make_bare_remote("newproj")
        self._set_url_registry("newproj", str(bare))
        slug_dir = self.sb.workers / "newproj"
        slug_dir.mkdir()
        (slug_dir / "CLAUDE.md").write_text("old brief", encoding="utf-8")
        (slug_dir / "send_plan.json").write_text("{}", encoding="utf-8")
        plan = self._build(task_id="np-residue")
        self.assertIsNone(plan.pending_clone)
        self.assertIsNone(plan.base_repo)
        self.assertTrue(plan.blocking_warnings)
        self.assertTrue(
            any("Pattern-A residue" in w for w in plan.blocking_warnings)
        )

    def test_unknown_slug_stays_ephemeral_c_no_pending_clone(self):
        # No registry row at all → Pattern C ephemeral, never a pending clone.
        plan = self._build(task_id="np-unknown", slug="totally-unregistered")
        self.assertEqual(plan.layout.pattern, "C")
        self.assertIsNone(plan.pending_clone)


# ---------------------------------------------------------------------------
# Issue #712: brief placement generalized to "repo tracks CLAUDE.md"
# ---------------------------------------------------------------------------


class TestIssue712BriefPlacement(unittest.TestCase):
    """Issue #712: the worker brief must go to ``CLAUDE.local.md`` whenever the
    repo the worker lands in already tracks a ``CLAUDE.md`` — not only for
    self-edit — so the generated brief never clobbers the project's own
    checked-in CLAUDE.md. The brief *template* stays normal (decoupled from
    the placement)."""

    def setUp(self) -> None:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name), with_claude_org_origin=False)
        import os
        self._git_env = os.environ.copy()
        self._git_env.update(
            {
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            env=self._git_env,
        )

    # --- helper unit tests -------------------------------------------------

    def test_repo_tracks_claude_md_helper(self):
        repo = Path(self._td.name) / "r"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        # No CLAUDE.md yet.
        self.assertFalse(gdp._repo_tracks_claude_md(repo))
        (repo / "CLAUDE.md").write_text("proj\n", encoding="utf-8")
        # Untracked file does not count.
        self.assertFalse(gdp._repo_tracks_claude_md(repo))
        self._git(repo, "add", "CLAUDE.md")
        self._git(repo, "commit", "-q", "-m", "add claude")
        self.assertTrue(gdp._repo_tracks_claude_md(repo))
        # Non-git dir and absent dir → False (fail-safe).
        plain = Path(self._td.name) / "plain"
        plain.mkdir()
        self.assertFalse(gdp._repo_tracks_claude_md(plain))
        self.assertFalse(gdp._repo_tracks_claude_md(Path(self._td.name) / "nope"))

    def test_resolve_brief_filename_rule(self):
        repo = Path(self._td.name) / "r2"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        (repo / "CLAUDE.md").write_text("proj\n", encoding="utf-8")
        self._git(repo, "add", "CLAUDE.md")
        self._git(repo, "commit", "-q", "-m", "c")
        # self_edit short-circuit → local regardless of repo contents.
        self.assertEqual(
            gdp._resolve_brief_filename(self_edit=True, repo_dir=Path("/nope")),
            "CLAUDE.local.md",
        )
        # Non-self-edit but repo tracks CLAUDE.md → local (the #712 rule).
        self.assertEqual(
            gdp._resolve_brief_filename(self_edit=False, repo_dir=repo),
            "CLAUDE.local.md",
        )
        # Non-self-edit, no tracked CLAUDE.md → plain.
        empty = Path(self._td.name) / "empty"
        empty.mkdir()
        self._git(empty, "init", "-q", "-b", "main")
        self.assertEqual(
            gdp._resolve_brief_filename(self_edit=False, repo_dir=empty),
            "CLAUDE.md",
        )

    # --- integration: existing local clone that tracks CLAUDE.md -----------

    def test_pattern_b_local_repo_tracking_claude_md_uses_local_md(self):
        # A registered LOCAL project repo that tracks CLAUDE.md, forced to
        # Pattern B via an active run → base_repo is that repo → brief is
        # placed at CLAUDE.local.md, but the brief *body* stays the normal
        # (non-self-edit) template.
        project_repo = Path(self._td.name) / "clock-repo"
        project_repo.mkdir()
        self._git(project_repo, "init", "-q", "-b", "main")
        (project_repo / "CLAUDE.md").write_text(
            "# project agent file\n", encoding="utf-8"
        )
        self._git(project_repo, "add", "CLAUDE.md")
        self._git(project_repo, "commit", "-q", "-m", "seed")
        self._git(project_repo, "update-ref",
                  "refs/remotes/origin/HEAD", "refs/heads/main")
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| 時計 | clock-app | {project_repo} | Clock | - |\n",
            encoding="utf-8",
        )
        self.sb.add_active_run(
            task_id="prev-clock",
            project_slug="clock-app",
            worker_dir=str(self.sb.workers / "clock-app"),
        )
        plan = gdp.build_delegate_plan(
            task_id="clock-712",
            project_slug="clock-app",
            description="add feature",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertEqual(Path(plan.base_repo).resolve(), project_repo.resolve())
        # #712: brief placed at CLAUDE.local.md because the base tracks CLAUDE.md.
        self.assertEqual(plan.brief_out_path.name, "CLAUDE.local.md")
        # Decoupled from the template: config self_edit stays False, so the
        # rendered body is the normal brief (no in-place-edit directive).
        self.assertFalse(plan.config["worker"]["self_edit"])
        brief = gwb.render(plan.config)
        self.assertNotIn("直接編集すること", brief)
        # DELEGATE body advertises the CLAUDE.local.md path.
        self.assertIn("CLAUDE.local.md", plan.delegate_body)

    def test_pattern_b_local_repo_without_claude_md_uses_plain_md(self):
        project_repo = Path(self._td.name) / "clock-repo2"
        project_repo.mkdir()
        self._git(project_repo, "init", "-q", "-b", "main")
        (project_repo / "README.md").write_text("hi\n", encoding="utf-8")
        self._git(project_repo, "add", "README.md")
        self._git(project_repo, "commit", "-q", "-m", "seed")
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| 時計 | clock-app | {project_repo} | Clock | - |\n",
            encoding="utf-8",
        )
        self.sb.add_active_run(
            task_id="prev-clock2",
            project_slug="clock-app",
            worker_dir=str(self.sb.workers / "clock-app"),
        )
        plan = gdp.build_delegate_plan(
            task_id="clock-712b",
            project_slug="clock-app",
            description="add feature",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        self.assertEqual(plan.layout.pattern, "B")
        self.assertEqual(plan.brief_out_path.name, "CLAUDE.md")

    # --- interaction: #709 pending clone whose source tracks CLAUDE.md -----

    def _make_bare_remote(self, name: str, *, claude_md: str | None) -> Path:
        src = Path(self._td.name) / f"{name}-src"
        src.mkdir()
        self._git(src, "init", "-q", "-b", "main")
        (src / "README.md").write_text("hi\n", encoding="utf-8")
        if claude_md is not None:
            (src / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
        self._git(src, "add", "-A")
        self._git(src, "commit", "-q", "-m", "init")
        bare = Path(self._td.name) / f"{name}.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(src), str(bare)],
            check=True, capture_output=True, env=self._git_env,
        )
        return bare

    def _set_url_registry(self, slug: str, url: str) -> None:
        (self.sb.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            f"| {slug} | {slug} | {url} | Remote proj | dev |\n",
            encoding="utf-8",
        )

    def test_pending_clone_brief_flips_to_local_when_source_tracks_claude_md(self):
        bare = self._make_bare_remote(
            "trackedproj", claude_md="# UPSTREAM PROJECT CLAUDE\n"
        )
        self._set_url_registry("trackedproj", str(bare))
        plan = gdp.build_delegate_plan(
            task_id="tracked-apply",
            project_slug="trackedproj",
            description="first dispatch",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        # At plan time the clone is absent → CLAUDE.md is the best guess.
        self.assertEqual(plan.brief_out_path.name, "CLAUDE.md")
        result = gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        worker_dir = Path(plan.layout.worker_dir)
        # apply re-evaluated post-clone → brief moved to CLAUDE.local.md.
        self.assertEqual(result.brief_path.name, "CLAUDE.local.md")
        self.assertTrue((worker_dir / "CLAUDE.local.md").exists())
        # The project's own tracked CLAUDE.md is intact (NOT clobbered).
        tracked = worker_dir / "CLAUDE.md"
        self.assertTrue(tracked.exists())
        self.assertEqual(
            tracked.read_text(encoding="utf-8"), "# UPSTREAM PROJECT CLAUDE\n"
        )
        # send_plan.json points the worker at the corrected file.
        send_plan = json.loads(
            result.send_plan_path.read_text(encoding="utf-8")
        )
        self.assertIn("CLAUDE.local.md", send_plan["message"])
        self.assertNotIn("CLAUDE.md・設定配置済み", send_plan["message"])

    def test_pending_clone_brief_stays_md_when_source_has_no_claude_md(self):
        bare = self._make_bare_remote("plainproj", claude_md=None)
        self._set_url_registry("plainproj", str(bare))
        plan = gdp.build_delegate_plan(
            task_id="plain-apply",
            project_slug="plainproj",
            description="first dispatch",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        result = gdp.apply_delegate_plan(
            plan,
            state_db_path=self.sb.db_path,
            claude_org_root=self.sb.claude_org_root,
            skip_settings=True,
        )
        # No tracked CLAUDE.md in the source → brief stays CLAUDE.md.
        self.assertEqual(result.brief_path.name, "CLAUDE.md")
        self.assertTrue(
            (Path(plan.layout.worker_dir) / "CLAUDE.md").exists()
        )


# ---------------------------------------------------------------------------
# Issue #744 Stage 1 — project dossier execution profiles
#
# The resolution contract under test (docs/design/project-dossier.md §4.1):
#
#     profiles/base.toml < profiles/<class>.toml < --from-toml < CLI flags
#
# Unit-level dossier behaviour (key classification, embedding budget,
# contracts/ guard) lives in tests/test_project_dossier.py; these tests cover
# the wiring into the payload generator.
# ---------------------------------------------------------------------------


class TestProfileWiring(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _Sandbox(Path(self._td.name))
        self.dossier = (
            self.sb.claude_org_root / "registry" / "projects" / "clock-app"
        )
        (self.dossier / "profiles").mkdir(parents=True)
        (self.dossier / "notes").mkdir()

    def tearDown(self) -> None:
        self._td.cleanup()

    # -- helpers ----------------------------------------------------------

    def _profile(self, name: str, body: str) -> None:
        (self.dossier / "profiles" / f"{name}.toml").write_text(
            body, encoding="utf-8"
        )

    def _task_toml(self, **over: str) -> Path:
        path = Path(self._td.name) / "task.toml"
        fields = {
            "verification_depth": "minimal",
            "commit_prefix": "chore(toml):",
        }
        fields.update(over)
        path.write_text(
            "[task]\n"
            'id = "profile-task"\n'
            'description = "d"\n'
            f'verification_depth = "{fields["verification_depth"]}"\n'
            'branch = "toml/branch"\n'
            f'commit_prefix = "{fields["commit_prefix"]}"\n'
            "\n[worker]\n"
            'dir = "X:/dummy"\n'
            'pattern = "A"\n'
            'role = "default"\n'
            "self_edit = false\n"
            "\n[project]\n"
            'name = "clock-app"\n'
            'description = "Web 時計"\n'
            "\n[paths]\n"
            'claude_org = "."\n',
            encoding="utf-8",
        )
        return path

    def _kwargs(self, **ns: object) -> dict:
        base = dict(
            profile=None, from_toml=None, task_id="profile-task",
            project_slug=None, target=[], description="d", mode=None,
            branch_override=None, commit_prefix=None, verification_depth=None,
            issue_url=None, closes_issue=None, refs_issues=None,
            project_description_override=None, impl_target=[],
            impl_guidance=None, knowledge=[], parallel_notes=None,
            registry_path=None, state_db_path=None, claude_org_root=None,
            workers_dir=None, pattern=None,
        )
        base.update(ns)
        return gdp._gather_plan_kwargs(
            argparse.Namespace(**base), self.sb.claude_org_root
        )

    # -- precedence -------------------------------------------------------

    def test_profile_supplies_values_when_nothing_stronger_does(self):
        self._profile(
            "base",
            '[task]\nverification_depth = "minimal"\ncommit_prefix = "fix(mirror):"\n',
        )
        kwargs = self._kwargs(profile="clock-app")
        self.assertEqual(kwargs["verification_depth"], "minimal")
        self.assertEqual(kwargs["commit_prefix"], "fix(mirror):")

    def test_class_layer_beats_base_layer(self):
        self._profile("base", '[task]\ncommit_prefix = "chore(base):"\n')
        self._profile("ci-fix", '[task]\ncommit_prefix = "fix(mirror):"\n')
        kwargs = self._kwargs(profile="clock-app/ci-fix")
        self.assertEqual(kwargs["commit_prefix"], "fix(mirror):")

    def test_from_toml_beats_the_profile(self):
        self._profile("base", '[task]\ncommit_prefix = "fix(mirror):"\n')
        kwargs = self._kwargs(
            profile="clock-app", from_toml=self._task_toml()
        )
        self.assertEqual(kwargs["commit_prefix"], "chore(toml):")
        self.assertEqual(kwargs["verification_depth"], "minimal")

    def test_cli_flag_beats_both(self):
        self._profile("base", '[task]\ncommit_prefix = "fix(mirror):"\n')
        kwargs = self._kwargs(
            profile="clock-app",
            from_toml=self._task_toml(),
            commit_prefix="docs(cli):",
            verification_depth="full",
        )
        self.assertEqual(kwargs["commit_prefix"], "docs(cli):")
        self.assertEqual(kwargs["verification_depth"], "full")

    def test_toml_silence_does_not_erase_a_profile_value(self):
        # _load_task_args_from_toml returns every key (None when absent); a
        # blind update() would let a TOML that never mentions parallel_notes
        # wipe the profile's. Explicit-value-only must hold on this layer too.
        self._profile("base", '[parallel]\nnotes = "from profile"\n')
        kwargs = self._kwargs(
            profile="clock-app", from_toml=self._task_toml()
        )
        self.assertEqual(kwargs["parallel_notes"], "from profile")

    # -- branch_style -----------------------------------------------------

    def test_branch_style_renders_with_the_final_task_id(self):
        self._profile("base", '[task]\nbranch_style = "docs/{task_id}"\n')
        kwargs = self._kwargs(profile="clock-app", task_id="en-batch")
        self.assertEqual(kwargs["branch_override"], "docs/en-batch")

    def test_explicit_branch_flag_beats_branch_style(self):
        self._profile("base", '[task]\nbranch_style = "docs/{task_id}"\n')
        kwargs = self._kwargs(
            profile="clock-app", branch_override="fix/manual"
        )
        self.assertEqual(kwargs["branch_override"], "fix/manual")

    def test_toml_branch_beats_branch_style(self):
        self._profile("base", '[task]\nbranch_style = "docs/{task_id}"\n')
        kwargs = self._kwargs(
            profile="clock-app", from_toml=self._task_toml()
        )
        self.assertEqual(kwargs["branch_override"], "toml/branch")

    # -- failure modes ----------------------------------------------------

    def test_undefined_class_exits_rather_than_falling_back(self):
        self._profile("base", '[task]\ncommit_prefix = "fix(mirror):"\n')
        self._profile("ci-fix", "")
        with self.assertRaises(SystemExit) as ctx:
            self._kwargs(profile="clock-app/typo")
        self.assertIn("ci-fix", str(ctx.exception))

    def test_missing_dossier_exits(self):
        with self.assertRaises(SystemExit):
            self._kwargs(profile="no-such-project")

    def test_slug_mismatch_with_toml_exits(self):
        # Otherwise the worker is told they are on project X while the brief
        # carries project Y's charter, description and commit prefix.
        self._profile("base", '[task]\ncommit_prefix = "fix(clock):"\n')
        toml = self._task_toml()
        toml.write_text(
            toml.read_text(encoding="utf-8").replace(
                'name = "clock-app"', 'name = "claude-org-ja"'
            ),
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit) as ctx:
            self._kwargs(profile="clock-app", from_toml=toml)
        self.assertIn("clock-app", str(ctx.exception))
        self.assertIn("claude-org-ja", str(ctx.exception))

    def test_slug_mismatch_with_cli_flag_exits(self):
        self._profile("base", "")
        with self.assertRaises(SystemExit):
            self._kwargs(profile="clock-app", project_slug="renga")

    def test_matching_explicit_slug_is_accepted(self):
        self._profile("base", '[task]\ncommit_prefix = "fix(clock):"\n')
        kwargs = self._kwargs(profile="clock-app", project_slug="clock-app")
        self.assertEqual(kwargs["project_slug"], "clock-app")
        self.assertEqual(kwargs["commit_prefix"], "fix(clock):")

    def test_forbidden_axis_exits(self):
        self._profile("base", "[task]\nmerge_preapproved = true\n")
        with self.assertRaises(SystemExit):
            self._kwargs(profile="clock-app")

    # -- warnings / brief embedding ---------------------------------------

    def test_unwired_axis_warning_reaches_plan_warnings(self):
        self._profile("base", '[profile]\nmodel = "opus"\n')
        kwargs = self._kwargs(profile="clock-app")
        plan = gdp.build_delegate_plan(
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            **kwargs,
        )
        self.assertTrue(
            any("not wired in Stage 1" in w for w in plan.warnings), plan.warnings
        )
        self.assertEqual(plan.blocking_warnings, [])

    def test_dossier_content_is_embedded_in_the_rendered_brief(self):
        (self.dossier / "charter.md").write_text(
            "# 憲章\n\nEN ミラーは機械ミラーである。\n", encoding="utf-8"
        )
        (self.dossier / "notes" / "ci.md").write_text(
            "stale-base は origin/main を merge するだけで直る。\n",
            encoding="utf-8",
        )
        self._profile("base", '[dossier]\nembed_notes = ["ci.md"]\n')
        kwargs = self._kwargs(profile="clock-app")
        plan = gdp.build_delegate_plan(
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
            **kwargs,
        )
        brief = gwb.render(plan.config)
        self.assertIn("プロジェクト台帳", brief)
        self.assertIn("EN ミラーは機械ミラーである。", brief)
        self.assertIn("stale-base は origin/main を merge するだけで直る。", brief)

    def test_no_profile_leaves_the_brief_without_a_dossier_section(self):
        plan = gdp.build_delegate_plan(
            task_id="plain",
            project_slug="clock-app",
            description="d",
            claude_org_root=self.sb.claude_org_root,
            state_db_path=self.sb.db_path,
        )
        brief = gwb.render(plan.config)
        self.assertNotIn("プロジェクト台帳", brief)
        self.assertNotIn("dossier", plan.config["project"])

    def test_preview_cli_surfaces_the_profile_warning(self):
        self._profile("base", '[profile]\npr_shape = "single"\n')
        buf = StringIO()
        err = StringIO()
        with redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = gdp.main([
                "preview",
                "--task-id", "profile-cli",
                "--profile", "clock-app",
                "--description", "d",
                "--claude-org-root", str(self.sb.claude_org_root),
                "--state-db-path", str(self.sb.db_path),
            ])
        self.assertEqual(rc, 0)
        self.assertIn("not wired in Stage 1", err.getvalue())

    def test_preview_cli_derives_project_slug_from_the_profile(self):
        self._profile("base", '[task]\ncommit_prefix = "fix(mirror):"\n')
        buf = StringIO()
        with redirect_stdout(buf), contextlib.redirect_stderr(StringIO()):
            rc = gdp.main([
                "preview", "--json",
                "--task-id", "profile-cli",
                "--profile", "clock-app",
                "--description", "d",
                "--claude-org-root", str(self.sb.claude_org_root),
                "--state-db-path", str(self.sb.db_path),
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["summary"]["project_slug"], "clock-app")

    def test_undefined_class_via_cli_exits_nonzero(self):
        self._profile("ci-fix", "")
        with self.assertRaises(SystemExit):
            gdp.main([
                "preview",
                "--task-id", "t",
                "--profile", "clock-app/typo",
                "--description", "d",
                "--claude-org-root", str(self.sb.claude_org_root),
                "--state-db-path", str(self.sb.db_path),
            ])


if __name__ == "__main__":
    unittest.main()
