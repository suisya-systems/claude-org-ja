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
import json
import re
import subprocess
import sys
import tempfile
import unittest
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
        self.assertEqual(
            Path(plan.layout.worker_dir),
            (self.sb.workers / "claude-org-en").resolve(),
        )
        # The DELEGATE body advertises a clone source (Pattern A label).
        self.assertIn("clone or reuse:", plan.delegate_body)
        self.assertIn(
            "https://github.com/suisya-systems/claude-org", plan.delegate_body
        )
        # bug 1: non-self-edit brief, no in-place-edit directive.
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
        # required args are only the four mandatory ones.
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
            ],
        )


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
        gdp.apply_delegate_plan(
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


if __name__ == "__main__":
    unittest.main()
