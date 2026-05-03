"""Tests for tools/gen_worker_brief.py."""
from __future__ import annotations

import copy
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


class RenderSelfEdit(unittest.TestCase):
    def test_self_edit_emits_ignore_root_note(self):
        cfg = _base_config(self_edit=True)
        out = gwb.render(cfg)
        self.assertIn("ルート CLAUDE.md", out)
        self.assertIn("Secretary 指示は無視せよ", out)
        self.assertIn("あなたは窓口ではなくワーカーである", out)
        self.assertNotIn("<!--BEGIN:", out)


class OptionalSections(unittest.TestCase):
    def test_optional_sections_omitted_when_absent(self):
        cfg = _base_config(self_edit=False)
        out = gwb.render(cfg)
        self.assertNotIn("実装ガイダンス", out)
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
        self.assertIn("実装ガイダンス", out)
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
        self.assertIn("実装ガイダンス", out)
        self.assertIn("guidance only", out)


class VerificationDepth(unittest.TestCase):
    def test_minimal_replaces_codex_section(self):
        cfg = _base_config(self_edit=False)
        cfg["task"]["verification_depth"] = "minimal"
        out = gwb.render(cfg)
        self.assertIn("検証深度 minimal", out)
        self.assertIn("done: {commit SHA", out)
        # full-mode marker text must NOT be present
        self.assertNotIn("3 ラウンド消せない場合は設計問題", out)

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


if __name__ == "__main__":
    unittest.main()
