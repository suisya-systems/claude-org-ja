"""Unit tests for tools/org_setup_prune.py (Issue #88)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import org_setup_prune as p  # noqa: E402


SAMPLE_PERMISSIONS_MD = """# perms

## ユーザー共通 (`~/.claude/settings.json`)

```json
{
  "permissions": {"allow": ["mcp__renga-peers__list_peers"]},
  "env": {"CLAUDE_CODE_NO_FLICKER": "1"}
}
```

## 窓口 (`<repo>/.claude/settings.local.json`)

```json
{
  "permissions": {
    "allow": ["Bash(git status:*)", "Bash(gh pr:*)"]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "bash .hooks/block-workers-delete.sh"}]
      }
    ]
  }
}
```

## フォアマン (`<repo>/.dispatcher/.claude/settings.local.json`)

```json
{
  "permissions": {"allow": ["Bash(claude :*)"]},
  "env": {"CLAUDE_ORG_PATH": "{claude_org_path}"},
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "bash \\"{claude_org_path}/.hooks/block-no-verify.sh\\""}]
      }
    ]
  }
}
```

## キュレーター (`<repo>/.curator/.claude/settings.local.json`)

```json
{"permissions": {"allow": []}}
```
"""


SAMPLE_SCHEMA = {
    "version": 1,
    "global": {"forbidden_allow_exact": [], "forbidden_allow_regex": []},
    "roles": {
        "user_common": {"docs_section": "ユーザー共通", "settings_paths": []},
        "secretary": {"docs_section": "窓口", "settings_paths": [".claude/settings.local.json"]},
        "dispatcher": {"docs_section": "フォアマン", "settings_paths": [".dispatcher/.claude/settings.local.json"]},
        "curator": {"docs_section": "キュレーター", "settings_paths": [".curator/.claude/settings.local.json"]},
    },
}


class ExtractTests(unittest.TestCase):
    def test_extracts_each_role_block(self) -> None:
        blocks = p.extract_role_blocks(SAMPLE_PERMISSIONS_MD, SAMPLE_SCHEMA["roles"])
        self.assertIn("Bash(git status:*)", blocks["secretary"]["permissions"]["allow"])
        self.assertEqual(blocks["dispatcher"]["env"]["CLAUDE_ORG_PATH"], "{claude_org_path}")
        self.assertEqual(blocks["curator"]["permissions"]["allow"], [])


class DeepMergeTests(unittest.TestCase):
    def test_dict_overlay_wins(self) -> None:
        out = p.deep_merge({"a": 1, "b": {"x": 1}}, {"b": {"y": 2}, "c": 3})
        self.assertEqual(out, {"a": 1, "b": {"x": 1, "y": 2}, "c": 3})

    def test_list_union_preserves_base_order(self) -> None:
        out = p.deep_merge({"l": ["a", "b"]}, {"l": ["b", "c"]})
        self.assertEqual(out["l"], ["a", "b", "c"])

    def test_hooks_dedupe_by_value(self) -> None:
        base_hooks = [{"type": "command", "command": "x"}]
        out = p.deep_merge(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": base_hooks}]}},
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": base_hooks}]}},
        )
        self.assertEqual(len(out["hooks"]["PreToolUse"]), 1)


class PlaceholderTests(unittest.TestCase):
    def test_substitutes_claude_org_path(self) -> None:
        tmpl = {"env": {"CLAUDE_ORG_PATH": "{claude_org_path}"},
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": 'bash "{claude_org_path}/.hooks/x.sh"'}]}]}}
        out = p.substitute_placeholders(tmpl, {"{claude_org_path}": "C:/org"})
        self.assertEqual(out["env"]["CLAUDE_ORG_PATH"], "C:/org")
        self.assertIn("C:/org/.hooks/x.sh", out["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_unresolved_placeholder_aborts(self) -> None:
        with self.assertRaises(SystemExit):
            p.build_target("dispatcher", {"env": {"CLAUDE_ORG_PATH": "{claude_org_path}"}}, None, None,
                           claude_org_path=None, worker_dir=None)

    def test_detect_from_existing_env(self) -> None:
        cur = {"env": {"CLAUDE_ORG_PATH": "C:/found"}}
        self.assertEqual(p.detect_claude_org_path(cur), "C:/found")

    def test_detect_from_existing_hook_command(self) -> None:
        cur = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": 'bash "C:/from-hook/.hooks/x.sh"'}
        ]}]}}
        self.assertEqual(p.detect_claude_org_path(cur), "C:/from-hook")


class DiffTests(unittest.TestCase):
    def test_diff_reports_add_remove(self) -> None:
        cur = {"permissions": {"allow": ["Bash(git status:*)", "Bash(gh:*)"]}}
        tgt = {"permissions": {"allow": ["Bash(git status:*)", "Bash(gh pr:*)"]}}
        d = p.compute_diff(cur, tgt)
        self.assertEqual(d["allow_removed"], ["Bash(gh:*)"])
        self.assertEqual(d["allow_added"], ["Bash(gh pr:*)"])


class EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / ".claude").mkdir()
        # Stale current settings with drift entries.
        self.cur_path = self.root / ".claude" / "settings.local.json"
        self.cur_path.write_text(json.dumps({
            "permissions": {"allow": ["Bash(gh:*)", "Bash(git status:*)"]},
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.td.cleanup()

    def _md_file(self) -> Path:
        f = self.root / "permissions.md"
        f.write_text(SAMPLE_PERMISSIONS_MD, encoding="utf-8")
        return f

    def _schema_file(self) -> Path:
        f = self.root / "schema.json"
        f.write_text(json.dumps(SAMPLE_SCHEMA), encoding="utf-8")
        return f

    def test_dry_run_does_not_modify(self) -> None:
        rc = p.main([
            "--role", "secretary", "--dry-run",
            "--root", str(self.root),
            "--schema", str(self._schema_file()),
            "--permissions-md", str(self._md_file()),
        ])
        self.assertEqual(rc, 0)
        # File untouched.
        unchanged = json.loads(self.cur_path.read_text(encoding="utf-8"))
        self.assertIn("Bash(gh:*)", unchanged["permissions"]["allow"])

    def test_prune_replaces_and_backs_up(self) -> None:
        rc = p.main([
            "--role", "secretary",
            "--root", str(self.root),
            "--schema", str(self._schema_file()),
            "--permissions-md", str(self._md_file()),
        ])
        self.assertEqual(rc, 0)
        new_cfg = json.loads(self.cur_path.read_text(encoding="utf-8"))
        # Old wide allow gone, template entries present.
        self.assertNotIn("Bash(gh:*)", new_cfg["permissions"]["allow"])
        self.assertIn("Bash(gh pr:*)", new_cfg["permissions"]["allow"])
        # Backup exists.
        backups = list(self.cur_path.parent.glob("settings.local.json.bak.*"))
        self.assertEqual(len(backups), 1)

    def test_override_file_is_merged(self) -> None:
        ov = self.cur_path.with_name("settings.local.override.json")
        ov.write_text(json.dumps({
            "permissions": {"allow": ["Bash(my-custom-tool:*)"]},
            "env": {"MY_CUSTOM": "1"},
        }), encoding="utf-8")
        rc = p.main([
            "--role", "secretary",
            "--root", str(self.root),
            "--schema", str(self._schema_file()),
            "--permissions-md", str(self._md_file()),
            "--no-backup",
        ])
        self.assertEqual(rc, 0)
        new_cfg = json.loads(self.cur_path.read_text(encoding="utf-8"))
        self.assertIn("Bash(my-custom-tool:*)", new_cfg["permissions"]["allow"])
        self.assertEqual(new_cfg["env"]["MY_CUSTOM"], "1")

    def test_dispatcher_placeholder_resolved_from_existing_env(self) -> None:
        d_path = self.root / ".dispatcher" / ".claude" / "settings.local.json"
        d_path.parent.mkdir(parents=True)
        d_path.write_text(json.dumps({
            "env": {"CLAUDE_ORG_PATH": "C:/from-existing"},
            "permissions": {"allow": []},
        }), encoding="utf-8")
        rc = p.main([
            "--role", "dispatcher",
            "--root", str(self.root),
            "--schema", str(self._schema_file()),
            "--permissions-md", str(self._md_file()),
            "--no-backup",
        ])
        self.assertEqual(rc, 0)
        new_cfg = json.loads(d_path.read_text(encoding="utf-8"))
        self.assertEqual(new_cfg["env"]["CLAUDE_ORG_PATH"], "C:/from-existing")
        cmd = new_cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("C:/from-existing/.hooks/block-no-verify.sh", cmd)

    def test_dispatcher_aborts_when_placeholder_unresolvable(self) -> None:
        # Fresh dispatcher dir with no existing settings.
        rc = p.main([
            "--role", "dispatcher",
            "--root", str(self.root),
            "--schema", str(self._schema_file()),
            "--permissions-md", str(self._md_file()),
        ])
        # Should exit non-zero (SystemExit string -> exit code 1 from argparse).
        self.assertNotEqual(rc, 0)


class SafetyGateTests(unittest.TestCase):
    """A malicious / mistaken settings.local.override.json must NOT be able to
    smuggle a forbidden wide allow into settings.local.json. The prune writer
    re-validates the merged target before persisting."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / ".curator" / ".claude").mkdir(parents=True)
        (self.root / ".curator" / ".claude" / "settings.local.json").write_text(
            json.dumps({"permissions": {"allow": []}}), encoding="utf-8",
        )
        # Override that tries to inject a forbidden wide allow.
        (self.root / ".curator" / ".claude" / "settings.local.override.json").write_text(
            json.dumps({"permissions": {"allow": ["Bash(git *)"]}}), encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_forbidden_override_aborts_before_write(self) -> None:
        # Use the real schema/permissions.md so the forbidden_allow_exact list applies.
        rc = p.main([
            "--role", "curator",
            "--root", str(self.root),
            "--no-backup",
        ])
        self.assertNotEqual(rc, 0)
        # Original settings untouched -- no .bak either (no write attempted).
        cfg = json.loads((self.root / ".curator" / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["permissions"]["allow"], [])
        baks = list((self.root / ".curator" / ".claude").glob("settings.local.json.bak.*"))
        self.assertEqual(baks, [])


class CheckerOverrideAwarenessTests(unittest.TestCase):
    """check_role_configs.py must subtract sibling settings.local.override.json
    allows from the closed-world check, otherwise the documented prune+override
    workflow would always fail on-disk validation."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / ".curator" / ".claude").mkdir(parents=True)
        # curator schema declares closed_world with required_allow=[].
        # Any extra entry would trip "unknown allow entry" without override-awareness.
        (self.root / ".curator" / ".claude" / "settings.local.json").write_text(
            json.dumps({"permissions": {"allow": ["Bash(my-private-tool:*)"]}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.td.cleanup()

    def _import_checker(self):
        # Real schema/permissions.md from repo root.
        import check_role_configs as c  # noqa
        return c

    def test_override_present_silences_unknown_allow_warning(self) -> None:
        c = self._import_checker()
        # Without override file: should fail on the unknown allow.
        findings_no_ov = c.check_on_disk(
            c.load_schema(c.DEFAULT_SCHEMA),
            self.root,
            include_untracked=True,
        )
        self.assertTrue(any("my-private-tool" in f.message for f in findings_no_ov))

        # Add override that whitelists the entry; checker must now pass for that entry.
        ov = self.root / ".curator" / ".claude" / "settings.local.override.json"
        ov.write_text(json.dumps({"permissions": {"allow": ["Bash(my-private-tool:*)"]}}), encoding="utf-8")
        findings_with_ov = c.check_on_disk(
            c.load_schema(c.DEFAULT_SCHEMA),
            self.root,
            include_untracked=True,
        )
        self.assertFalse(
            any("my-private-tool" in f.message for f in findings_with_ov),
            f"override should suppress closed-world warning, got: {[x.format() for x in findings_with_ov]}",
        )


if __name__ == "__main__":
    unittest.main()
