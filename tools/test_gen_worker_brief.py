"""Tests for tools/gen_worker_brief.py."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import gen_worker_brief as gwb


def _base_config(self_edit: bool = False) -> dict:
    return {
        "task": {
            "id": "demo-task",
            "description": "デモタスク。X を Y に変更する。",
            "verification_depth": "full",
            "branch": "demo-task",
            "commit_prefix": "feat(tools):",
            "refs_issues": [121, 214],
        },
        "worker": {
            "dir": "/tmp/workers/demo-task",
            "pattern": "B" if self_edit else "A",
            "role": "claude-org-self-edit" if self_edit else "default",
            "self_edit": self_edit,
        },
        "project": {
            "name": "claude-org-ja",
            "description": "テスト用説明",
        },
        "paths": {
            "claude_org": "/home/user/work/claude-org",
        },
    }


class RenderNormal(unittest.TestCase):
    def test_normal_full_contains_boilerplate(self):
        cfg = _base_config(self_edit=False)
        out = gwb.render(cfg)
        # boilerplate sections
        self.assertIn("作業ディレクトリ（最重要制約）", out)
        self.assertIn("禁止事項", out)
        self.assertIn("Windows 環境の注意事項", out)
        self.assertIn("プロジェクト情報", out)
        self.assertIn("権限", out)
        self.assertIn("Codex セルフレビュー", out)
        self.assertIn("作業完了時", out)
        self.assertIn("SUSPEND", out)
        # variable substitution
        self.assertIn("/tmp/workers/demo-task", out)
        self.assertIn("/home/user/work/claude-org", out)
        self.assertIn("claude-org-ja", out)
        self.assertIn("feat(tools):", out)
        self.assertIn("Refs #121 #214", out)
        # not-self-edit must NOT contain ignore-root note
        self.assertNotIn("ルート CLAUDE.md", out)
        # codex full present, minimal absent
        self.assertIn("検証深度 full", out)
        self.assertNotIn("minimal 用 1 行報告フォーマット", out)
        # no leftover marker comments
        self.assertNotIn("<!--BEGIN:", out)
        self.assertNotIn("<!--END:", out)
        # no leftover ${...} placeholders
        self.assertNotIn("${", out)

    def test_normal_emits_windows_cli_ascii_check(self):
        """Windows CLI tools must be told to keep stdout strings ASCII so
        cp932 consoles don't crash on --help (ja#537 / runtime#63 type)."""
        cfg = _base_config(self_edit=False)
        out = gwb.render(cfg)
        self.assertIn("ASCII の", out)
        self.assertIn("UnicodeEncodeError", out)
        self.assertIn("`--help` を実端末で", out)

    def test_normal_does_not_emit_self_edit_clone_directive(self):
        """Issue #484 bug 1: a non-self-edit worker (e.g. a remote
        translation-sync clone) must NOT be told to edit claude-org directly.
        The self-edit ``（直接編集すること）`` prohibition is reserved for the
        self-edit brief; the normal brief instructs the worker to clone the
        infra repo nowhere and work inside its own project clone."""
        cfg = _base_config(self_edit=False)
        out = gwb.render(cfg)
        self.assertNotIn("直接編集すること", out)
        self.assertNotIn("別途 clone", out)
        # The reworded guard still tells the worker claude-org is reference-only.
        self.assertIn("参照専用", out)


class RenderSelfEdit(unittest.TestCase):
    def test_self_edit_emits_ignore_root_note(self):
        cfg = _base_config(self_edit=True)
        out = gwb.render(cfg)
        self.assertIn("ルート CLAUDE.md", out)
        self.assertIn("Secretary 指示は無視せよ", out)
        self.assertIn("あなたは窓口ではなくワーカーである", out)
        self.assertNotIn("<!--BEGIN:", out)

    def test_self_edit_emits_windows_cli_ascii_check(self):
        """The terse self-edit brief carries the same CLI-ASCII guard."""
        cfg = _base_config(self_edit=True)
        out = gwb.render(cfg)
        self.assertIn("ASCII の", out)
        self.assertIn("`--help` を実端末で", out)

    def test_self_edit_keeps_direct_edit_prohibition(self):
        """The self-edit brief (origin URL == suisya-systems/claude-org-ja)
        legitimately works *inside* the live repo, so its prohibition still
        says ``直接編集`` (don't re-clone, edit in place). Issue #484 only
        moved this directive out of the *normal* brief."""
        cfg = _base_config(self_edit=True)
        out = gwb.render(cfg)
        self.assertIn("直接編集", out)


class OptionalSections(unittest.TestCase):
    def test_optional_sections_omitted_when_absent(self):
        cfg = _base_config(self_edit=False)
        out = gwb.render(cfg)
        # section header (not the bare word, which the Codex full block now
        # references when explaining the round-cap override)
        self.assertNotIn("### 実装ガイダンス", out)
        self.assertNotIn("並列タスクとの干渉", out)
        self.assertNotIn("ナレッジ参照", out)
        self.assertNotIn("Issue URL", out)

    def test_optional_sections_included_when_present(self):
        cfg = _base_config(self_edit=False)
        cfg["task"]["issue_url"] = "https://example.com/issues/9"
        cfg["implementation"] = {
            "target_files": ["a.md", "b.py"],
            "guidance": "  do the thing  ",
        }
        cfg["references"] = {"knowledge": ["/k/curated/x.md"]}
        cfg["parallel"] = {"notes": "並列ワーカーは無し"}
        out = gwb.render(cfg)
        self.assertIn("### 実装ガイダンス", out)
        self.assertIn("- `a.md`", out)
        self.assertIn("- `b.py`", out)
        self.assertIn("do the thing", out)
        self.assertIn("ナレッジ参照", out)
        self.assertIn("/k/curated/x.md", out)
        self.assertIn("並列タスクとの干渉", out)
        self.assertIn("並列ワーカーは無し", out)
        self.assertIn("Issue URL", out)
        self.assertIn("https://example.com/issues/9", out)

    def test_implementation_with_only_guidance_keeps_block(self):
        cfg = _base_config(self_edit=False)
        cfg["implementation"] = {"guidance": "guidance only"}
        out = gwb.render(cfg)
        self.assertIn("### 実装ガイダンス", out)
        self.assertIn("guidance only", out)


class PythonSrcLayoutRule(unittest.TestCase):
    """Issue #676: Python src-layout projects carry the PYTHONPATH=src /
    no-editable-install verification rule as a standing brief section."""

    RULE_HEADER = "Python 検証規約（src-layout）"

    def test_omitted_by_default(self):
        cfg = _base_config(self_edit=False)
        out = gwb.render(cfg)
        self.assertNotIn(self.RULE_HEADER, out)
        self.assertNotIn("PYTHONPATH=src", out)
        self.assertNotIn("pip install -e", out)

    def test_normal_brief_carries_rule_when_flagged(self):
        cfg = _base_config(self_edit=False)
        cfg["project"]["python_src_layout"] = True
        out = gwb.render(cfg)
        self.assertIn(self.RULE_HEADER, out)
        self.assertIn("`PYTHONPATH=src` を前置", out)
        self.assertIn("editable install（`pip install -e`）は禁止", out)
        self.assertNotIn("<!--BEGIN:", out)

    def test_self_edit_brief_carries_rule_when_flagged(self):
        cfg = _base_config(self_edit=True)
        cfg["project"]["python_src_layout"] = True
        out = gwb.render(cfg)
        self.assertIn(self.RULE_HEADER, out)
        self.assertIn("`PYTHONPATH=src` を前置", out)
        self.assertIn("editable install（`pip install -e`）は禁止", out)

    def test_explicit_false_omits_rule(self):
        cfg = _base_config(self_edit=False)
        cfg["project"]["python_src_layout"] = False
        out = gwb.render(cfg)
        self.assertNotIn(self.RULE_HEADER, out)

    def test_non_bool_flag_rejected(self):
        cfg = _base_config(self_edit=False)
        cfg["project"]["python_src_layout"] = "yes"
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)


class PythonSrcLayoutDetection(unittest.TestCase):
    """Filesystem detection helpers behind the Issue #676 rule."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _make_repo(self, name: str, *, src: bool, markers: tuple[str, ...]) -> Path:
        repo = self.td / name
        repo.mkdir(parents=True)
        if src:
            (repo / "src").mkdir()
        for m in markers:
            (repo / m).write_text("", encoding="utf-8")
        return repo

    def test_src_plus_pyproject_detected(self):
        repo = self._make_repo("runtime", src=True, markers=("pyproject.toml",))
        self.assertTrue(gwb.is_python_src_layout(repo))

    def test_setup_py_and_setup_cfg_also_count(self):
        for marker in ("setup.py", "setup.cfg"):
            repo = self._make_repo(f"proj-{marker}", src=True, markers=(marker,))
            self.assertTrue(gwb.is_python_src_layout(repo), marker)

    def test_src_without_python_marker_not_detected(self):
        # Rust-style layout: src/ + Cargo.toml, no Python packaging marker.
        repo = self._make_repo("rusty", src=True, markers=("Cargo.toml",))
        self.assertFalse(gwb.is_python_src_layout(repo))

    def test_python_marker_without_src_not_detected(self):
        # Flat-layout Python project — PYTHONPATH=src would be wrong advice.
        repo = self._make_repo("flat", src=False, markers=("pyproject.toml",))
        self.assertFalse(gwb.is_python_src_layout(repo))

    def test_missing_dir_not_detected(self):
        self.assertFalse(gwb.is_python_src_layout(self.td / "nope"))

    def test_worktree_shaped_worker_dir_probes_base_clone(self):
        # The unified <base>/.worktrees/<task>/ layout: the worktree does
        # not exist yet at brief-gen time, so detection must probe <base>.
        base = self._make_repo("base-clone", src=True, markers=("pyproject.toml",))
        worker_dir = base / ".worktrees" / "task-1"  # intentionally not created
        self.assertTrue(gwb._detect_python_src_layout(worker_dir, None))

    def test_registry_local_path_probed_as_fallback(self):
        repo = self._make_repo("registered", src=True, markers=("pyproject.toml",))
        worker_dir = self.td / "workers" / "registered"  # does not exist
        self.assertTrue(gwb._detect_python_src_layout(worker_dir, str(repo)))

    def test_url_and_placeholder_registry_paths_skipped(self):
        worker_dir = self.td / "workers" / "some-proj"  # does not exist
        self.assertFalse(
            gwb._detect_python_src_layout(
                worker_dir, "https://github.com/example/some-proj"
            )
        )
        self.assertFalse(gwb._detect_python_src_layout(worker_dir, "-"))


class VerificationDepth(unittest.TestCase):
    def test_minimal_replaces_codex_section(self):
        cfg = _base_config(self_edit=False)
        cfg["task"]["verification_depth"] = "minimal"
        out = gwb.render(cfg)
        self.assertIn("検証深度 minimal", out)
        self.assertIn("done: {commit SHA", out)
        # full-mode marker text must NOT be present
        self.assertNotIn("上限に達したら round N+1 に自走で入らない", out)

    def test_minimal_self_edit_uses_one_liner(self):
        cfg = _base_config(self_edit=True)
        cfg["task"]["verification_depth"] = "minimal"
        out = gwb.render(cfg)
        self.assertIn("minimal 用 1 行報告フォーマット", out)


class Validation(unittest.TestCase):
    def test_invalid_pattern(self):
        cfg = _base_config(False)
        cfg["worker"]["pattern"] = "Z"
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_invalid_role(self):
        cfg = _base_config(False)
        cfg["worker"]["role"] = "bogus"
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_invalid_depth(self):
        cfg = _base_config(False)
        cfg["task"]["verification_depth"] = "deep"
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_missing_required_section(self):
        cfg = _base_config(False)
        del cfg["paths"]
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_missing_required_key(self):
        cfg = _base_config(False)
        del cfg["task"]["branch"]
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_self_edit_must_be_bool(self):
        cfg = _base_config(False)
        cfg["worker"]["self_edit"] = "yes"
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_non_string_description_rejected(self):
        cfg = _base_config(False)
        cfg["task"]["description"] = 1
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_empty_string_rejected(self):
        cfg = _base_config(False)
        cfg["task"]["branch"] = ""
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_implementation_guidance_must_be_string(self):
        cfg = _base_config(False)
        cfg["implementation"] = {"guidance": 42}
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_target_files_must_be_string_list(self):
        cfg = _base_config(False)
        cfg["implementation"] = {"target_files": ["a.md", 7]}
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_closes_issue_must_be_int(self):
        cfg = _base_config(False)
        cfg["task"]["closes_issue"] = "217"
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_closes_issue_rejects_bool(self):
        cfg = _base_config(False)
        cfg["task"]["closes_issue"] = True
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)

    def test_refs_issues_rejects_bool(self):
        cfg = _base_config(False)
        cfg["task"]["refs_issues"] = [True, 2]
        with self.assertRaises(gwb.ConfigError):
            gwb.render(cfg)


class ClosesOrRefs(unittest.TestCase):
    def test_closes_takes_priority(self):
        cfg = _base_config(False)
        cfg["task"]["closes_issue"] = 42
        cfg["task"]["refs_issues"] = [1, 2]
        out = gwb.render(cfg)
        self.assertIn("Closes #42", out)
        self.assertNotIn("Refs #1 #2", out)

    def test_no_issue_refs(self):
        cfg = _base_config(False)
        cfg["task"].pop("refs_issues", None)
        out = gwb.render(cfg)
        self.assertIn("（なし）", out)


class RoundTrip217(unittest.TestCase):
    """Reproduce the session #9 #217 brief as a round-trip smoke test."""

    def test_217_brief_renders(self):
        example = (
            Path(__file__).parent / "templates" / "worker_brief.example.toml"
        )
        cfg = gwb.load_config(example)
        out = gwb.render(cfg)
        # the example TOML is the #217 brief — check key substrings round-trip
        self.assertIn("issue-217-renga-decoration-doc", out)
        self.assertIn("Closes #217", out)
        self.assertIn("docs(operations):", out)
        self.assertIn("renga decoration", out)
        self.assertIn(
            "C:/Users/iwama/Documents/work/workers/claude-org/.worktrees/issue-217-renga-decoration-doc",
            out,
        )
        # self_edit=true → ignore-root note present, output style is local
        self.assertIn("Secretary 指示は無視せよ", out)
        # optional sections all present
        self.assertIn("docs/operations/renga-pane-conventions.md", out)
        self.assertIn("knowledge/curated/renga.md", out)
        self.assertIn("issue-216-dispatcher-retro-gate", out)


class CLI(unittest.TestCase):
    def test_cli_writes_file(self):
        example = (
            Path(__file__).parent / "templates" / "worker_brief.example.toml"
        )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "CLAUDE.local.md"
            rc = gwb.main(["--config", str(example), "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())
            text = out.read_text(encoding="utf-8")
            self.assertIn("issue-217-renga-decoration-doc", text)

    def test_cli_invalid_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.toml"
            bad.write_text(
                '[task]\nid="x"\n', encoding="utf-8"
            )  # missing many keys
            out = Path(td) / "out.md"
            rc = gwb.main(["--config", str(bad), "--out", str(out)])
            self.assertEqual(rc, 2)
            self.assertFalse(out.exists())

    def test_cli_malformed_toml_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "broken.toml"
            bad.write_text("not = = valid", encoding="utf-8")
            out = Path(td) / "out.md"
            rc = gwb.main(["--config", str(bad), "--out", str(out)])
            self.assertEqual(rc, 2)
            self.assertFalse(out.exists())

    def test_cli_missing_config_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.md"
            rc = gwb.main(
                ["--config", str(Path(td) / "nope.toml"), "--out", str(out)]
            )
            self.assertEqual(rc, 2)
            self.assertFalse(out.exists())


class FromTaskSubcommand(unittest.TestCase):
    """``gen_worker_brief.py from-task ...`` (Issue #283 Stage 2).

    Builds a config from registry+state.db (via resolve_worker_layout),
    then renders the same template path as the legacy --config flow.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        td = Path(self._td.name)
        # Shape a claude-org-like sandbox.
        self.claude_org_root = td / "claude-org"
        (self.claude_org_root / ".state").mkdir(parents=True)
        (self.claude_org_root / "registry").mkdir()
        # Self-edit detection runs off the live repo's git origin URL, so
        # the sandbox needs an initialised git repo with the canonical
        # origin set for the claude-org-ja → CLAUDE.local.md auto-switch
        # to fire.
        import subprocess as _sp
        _sp.run(["git", "init", "-q", str(self.claude_org_root)], check=True)
        _sp.run(
            ["git", "-C", str(self.claude_org_root), "remote", "add",
             "origin", "https://github.com/suisya-systems/claude-org-ja.git"],
            check=True,
        )
        (self.claude_org_root / "registry" / "org-config.md").write_text(
            "## Workers Directory\nworkers_dir: ../workers\n",
            encoding="utf-8",
        )
        (self.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| 時計アプリ | clock-app | - | Web 時計 | デザイン |\n"
            f"| claude-org-ja | claude-org-ja | {self.claude_org_root} | Self | スキル改善 |\n",
            encoding="utf-8",
        )
        # workers/ alongside claude-org per workers_dir: ../workers
        (td / "workers").mkdir()

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run(self, *extra: str) -> Path:
        out = Path(self._td.name) / "CLAUDE.md"
        argv = [
            "from-task",
            "--task-id", "demo-from-task",
            "--project-slug", "clock-app",
            "--description", "demo description for clock app",
            "--claude-org-root", str(self.claude_org_root),
            "--out", str(out),
            *extra,
        ]
        rc = gwb.main(argv)
        self.assertEqual(rc, 0)
        return out

    def test_from_task_renders_clock_app_brief(self):
        out = self._run()
        text = out.read_text(encoding="utf-8")
        self.assertIn("demo-from-task", text)
        self.assertIn("作業ディレクトリ", text)
        # Pattern A → fresh clone path under ../workers/clock-app
        self.assertIn("clock-app", text)
        self.assertIn("feat(clock):", text)  # default scope from project_slug
        # Not self-edit (clock-app is not claude-org)
        self.assertNotIn("ルート CLAUDE.md", text)

    def test_from_task_self_edit_auto_switches_to_local_md(self):
        explicit_out = Path(self._td.name) / "CLAUDE.md"
        rc = gwb.main([
            "from-task",
            "--task-id", "self-edit-demo",
            "--project-slug", "claude-org-ja",
            "--description", "edit a doc in claude-org",
            "--commit-prefix", "feat(secretary):",
            "--claude-org-root", str(self.claude_org_root),
            "--out", str(explicit_out),
        ])
        self.assertEqual(rc, 0)
        self.assertFalse(explicit_out.exists(), "CLAUDE.md should be skipped")
        switched = explicit_out.with_name("CLAUDE.local.md")
        self.assertTrue(switched.exists(), "should auto-switch to .local.md")
        text = switched.read_text(encoding="utf-8")
        self.assertIn("Secretary 指示は無視せよ", text)
        self.assertIn("feat(secretary):", text)

    def test_from_task_unknown_slug_falls_back_to_pattern_c(self):
        # Unregistered slug → Pattern C ephemeral, role=default. Brief still
        # renders because we provide sensible defaults for project.name /
        # project.description.
        out = self._run(
            "--project-slug", "completely-new-slug",
            "--description", "ad-hoc investigation",
        )
        text = out.read_text(encoding="utf-8")
        self.assertIn("demo-from-task", text)
        # Pattern C → no commit_prefix override needed; default scope from slug
        self.assertIn("feat(completely):", text)

    def test_from_task_writes_audit_toml_when_requested(self):
        toml_path = Path(self._td.name) / "audit.toml"
        self._run("--write-toml", str(toml_path))
        self.assertTrue(toml_path.exists())
        body = toml_path.read_text(encoding="utf-8")
        self.assertIn("[task]", body)
        self.assertIn('id = "demo-from-task"', body)
        self.assertIn("[worker]", body)
        self.assertIn('pattern = "A"', body)

    def test_from_task_carries_implementation_and_references(self):
        out = self._run(
            "--impl-target", "src/foo.py",
            "--impl-target", "src/bar.py",
            "--impl-guidance", "step 1, step 2",
            "--knowledge", "knowledge/curated/notes.md",
            "--issue-url", "https://example.com/issues/9",
            "--closes-issue", "9",
        )
        text = out.read_text(encoding="utf-8")
        self.assertIn("実装ガイダンス", text)
        self.assertIn("src/foo.py", text)
        self.assertIn("src/bar.py", text)
        self.assertIn("step 1, step 2", text)
        self.assertIn("knowledge/curated/notes.md", text)
        self.assertIn("Closes #9", text)
        self.assertIn("https://example.com/issues/9", text)


class FromTaskPythonSrcLayout(unittest.TestCase):
    """Issue #676 end-to-end: the from-task path detects a Python
    src-layout base clone (the claude-org-runtime deployment shape) and
    bakes the PYTHONPATH=src / no-editable-install rule into the brief."""

    RULE_HEADER = "Python 検証規約（src-layout）"

    def setUp(self) -> None:
        import subprocess as _sp
        self._td = tempfile.TemporaryDirectory()
        td = Path(self._td.name)
        self.claude_org_root = td / "claude-org"
        (self.claude_org_root / ".state").mkdir(parents=True)
        (self.claude_org_root / "registry").mkdir()
        _sp.run(["git", "init", "-q", str(self.claude_org_root)], check=True)
        _sp.run(
            ["git", "-C", str(self.claude_org_root), "remote", "add",
             "origin", "https://github.com/suisya-systems/claude-org-ja.git"],
            check=True,
        )
        (self.claude_org_root / "registry" / "org-config.md").write_text(
            "## Workers Directory\nworkers_dir: ../workers\n",
            encoding="utf-8",
        )
        # URL-only registry rows: claude-org-runtime (Python src-layout)
        # and renga (Rust — src/ but no Python packaging marker).
        (self.claude_org_root / "registry" / "projects.md").write_text(
            "# Projects\n\n"
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
            "|---|---|---|---|---|\n"
            "| ランタイム | claude-org-runtime | "
            "https://github.com/suisya-systems/claude-org-runtime | runtime | release |\n"
            "| renga | renga | https://github.com/suisya-systems/renga "
            "| TUI | 機能追加 |\n",
            encoding="utf-8",
        )
        workers = td / "workers"
        workers.mkdir()
        # Base clone at workers/claude-org-runtime with matching origin URL
        # (the trust gate find_workers_dir_clone requires) + src-layout.
        runtime = workers / "claude-org-runtime"
        (runtime / "src" / "claude_org_runtime").mkdir(parents=True)
        (runtime / "pyproject.toml").write_text(
            "[project]\nname = 'claude-org-runtime'\n", encoding="utf-8"
        )
        _sp.run(["git", "init", "-q", str(runtime)], check=True)
        _sp.run(
            ["git", "-C", str(runtime), "remote", "add", "origin",
             "https://github.com/suisya-systems/claude-org-runtime.git"],
            check=True,
        )
        # Rust-shaped clone: src/ + Cargo.toml only.
        renga = workers / "renga"
        (renga / "src").mkdir(parents=True)
        (renga / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        _sp.run(["git", "init", "-q", str(renga)], check=True)
        _sp.run(
            ["git", "-C", str(renga), "remote", "add", "origin",
             "https://github.com/suisya-systems/renga.git"],
            check=True,
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _render(self, slug: str, task_id: str) -> str:
        out = Path(self._td.name) / f"{task_id}-CLAUDE.md"
        rc = gwb.main([
            "from-task",
            "--task-id", task_id,
            "--project-slug", slug,
            "--description", f"work on {slug}",
            "--claude-org-root", str(self.claude_org_root),
            "--out", str(out),
        ])
        self.assertEqual(rc, 0)
        return out.read_text(encoding="utf-8")

    def test_runtime_brief_carries_rule(self):
        text = self._render("claude-org-runtime", "runtime-676")
        self.assertIn(self.RULE_HEADER, text)
        self.assertIn("`PYTHONPATH=src` を前置", text)
        self.assertIn("editable install（`pip install -e`）は禁止", text)

    def test_rust_src_dir_brief_has_no_rule(self):
        text = self._render("renga", "renga-676")
        self.assertNotIn(self.RULE_HEADER, text)
        self.assertNotIn("PYTHONPATH=src", text)

    def test_write_toml_round_trips_flag(self):
        toml_path = Path(self._td.name) / "audit.toml"
        out = Path(self._td.name) / "rt-CLAUDE.md"
        rc = gwb.main([
            "from-task",
            "--task-id", "runtime-audit-676",
            "--project-slug", "claude-org-runtime",
            "--description", "work on runtime",
            "--claude-org-root", str(self.claude_org_root),
            "--out", str(out),
            "--write-toml", str(toml_path),
        ])
        self.assertEqual(rc, 0)
        body = toml_path.read_text(encoding="utf-8")
        self.assertIn("python_src_layout = true", body)
        # And the dumped TOML renders back to a brief with the rule.
        cfg = gwb.load_config(toml_path)
        self.assertIn(self.RULE_HEADER, gwb.render(cfg))


class LegacyCLIPreserved(unittest.TestCase):
    """Stage 2 must not break the legacy --config / --out invocation."""

    def test_main_dispatches_legacy_when_no_subcommand(self):
        # Reuses the existing example.toml round-trip — equivalent to the
        # original CLI test; this just re-asserts main() routes correctly
        # after the from-task addition.
        example = Path(__file__).parent / "templates" / "worker_brief.example.toml"
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "CLAUDE.local.md"
            rc = gwb.main(["--config", str(example), "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
