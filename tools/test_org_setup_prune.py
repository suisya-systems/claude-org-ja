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

## ディスパッチャー (`<repo>/.dispatcher/.claude/settings.local.json`)

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
        "dispatcher": {"docs_section": "ディスパッチャー", "settings_paths": [".dispatcher/.claude/settings.local.json"]},
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


class OverrideShapeValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / ".curator" / ".claude").mkdir(parents=True)
        (self.root / ".curator" / ".claude" / "settings.local.json").write_text(
            json.dumps({"permissions": {"allow": []}}), encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.td.cleanup()

    def _run_with_override(self, override_payload) -> int:
        ov = self.root / ".curator" / ".claude" / "settings.local.override.json"
        ov.write_text(json.dumps(override_payload), encoding="utf-8")
        return p.main(["--role", "curator", "--root", str(self.root), "--no-backup"])

    def test_top_level_array_aborts(self) -> None:
        rc = self._run_with_override(["Bash(foo:*)"])
        self.assertNotEqual(rc, 0)

    def test_scalar_permissions_aborts(self) -> None:
        rc = self._run_with_override({"permissions": "oops"})
        self.assertNotEqual(rc, 0)

    def test_allow_non_string_aborts(self) -> None:
        rc = self._run_with_override({"permissions": {"allow": [123]}})
        self.assertNotEqual(rc, 0)

    def test_well_formed_override_succeeds(self) -> None:
        rc = self._run_with_override({"permissions": {"allow": ["Bash(my-tool:*)"]}})
        self.assertEqual(rc, 0)


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


class FilterExistingUserDirsTests(unittest.TestCase):
    """Existence check must stat against the injected HOME, return the
    original ``~``-prefixed literal (not the expanded path), and ignore
    candidates that aren't ``~``-rooted directories."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        (self.home / ".ssh").mkdir()
        (self.home / ".config").mkdir()
        (self.home / ".config" / "gh").mkdir()
        # Sentinel file (not a directory) -- must be skipped because the
        # bwrap parent-dir mitigation only makes sense on directories.
        (self.home / ".netrc").write_text("placeholder", encoding="utf-8")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_returns_only_existing_dirs(self) -> None:
        result = p.filter_existing_user_dirs(
            ["~/.ssh", "~/.aws", "~/.config/gh", "~/.kube"], home=self.home,
        )
        self.assertEqual(result, ["~/.ssh", "~/.config/gh"])

    def test_returned_entries_keep_tilde_prefix(self) -> None:
        result = p.filter_existing_user_dirs(["~/.ssh"], home=self.home)
        self.assertEqual(result, ["~/.ssh"])
        # No absolute home path leaked into the entry -- portability is
        # the whole point of using the ``~``-prefixed literal in the
        # settings file.
        self.assertFalse(any(str(self.home) in e for e in result))

    def test_file_is_not_a_directory_match(self) -> None:
        # ``~/.netrc`` exists as a file but is not a directory; the
        # bwrap directory-unit deny would be ill-typed against a file.
        result = p.filter_existing_user_dirs(["~/.netrc"], home=self.home)
        self.assertEqual(result, [])

    def test_non_tilde_entries_are_skipped(self) -> None:
        result = p.filter_existing_user_dirs(
            ["/etc", "relative/path", "~/.ssh"], home=self.home,
        )
        self.assertEqual(result, ["~/.ssh"])

    def test_symlink_escaping_home_is_dropped(self) -> None:
        # Simulate the WSL pattern: ``~/.aws`` is a symlink to a directory
        # that lives outside HOME (on WSL this would be ``/mnt/c/...``).
        outside = Path(tempfile.mkdtemp(prefix="user_common_outside_"))
        try:
            (self.home / ".aws").symlink_to(outside)
            result = p.filter_existing_user_dirs(
                ["~/.ssh", "~/.aws"], home=self.home,
            )
            # ``~/.ssh`` (regular dir inside HOME) survives; ``~/.aws``
            # (symlink with realpath outside HOME) is suppressed so the
            # rendered settings.json does not ask bwrap to deny an entry
            # that would fail its bootstrap on WSL.
            self.assertEqual(result, ["~/.ssh"])
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)

    def test_symlink_inside_home_is_kept(self) -> None:
        # A symlink whose target resolves back inside HOME is still safe
        # (no realpath escape), so it should be retained.
        (self.home / ".real-kube").mkdir()
        (self.home / ".kube").symlink_to(self.home / ".real-kube")
        result = p.filter_existing_user_dirs(["~/.kube"], home=self.home)
        self.assertEqual(result, ["~/.kube"])


class MergeUserCommonSandboxTests(unittest.TestCase):
    """``merge_user_common_sandbox_denyread`` is the pure core: idempotent,
    preserving existing values, never mutates the input."""

    def test_creates_sandbox_block_when_missing(self) -> None:
        out = p.merge_user_common_sandbox_denyread({"theme": "dark"}, ["~/.ssh"])
        self.assertEqual(out["theme"], "dark")
        self.assertEqual(out["sandbox"]["filesystem"]["denyRead"], ["~/.ssh"])

    def test_preserves_existing_deny_read_entries_and_order(self) -> None:
        base = {
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": False,
                "filesystem": {
                    "denyRead": ["**/credentials*", "~/.config/gh/hosts.yml"],
                    "denyWrite": ["~/.claude/settings.json"],
                },
            },
        }
        out = p.merge_user_common_sandbox_denyread(base, ["~/.ssh", "~/.aws"])
        self.assertEqual(
            out["sandbox"]["filesystem"]["denyRead"],
            ["**/credentials*", "~/.config/gh/hosts.yml", "~/.ssh", "~/.aws"],
        )
        # Sibling sandbox keys MUST survive.
        self.assertTrue(out["sandbox"]["enabled"])
        self.assertEqual(out["sandbox"]["failIfUnavailable"], False)
        self.assertEqual(
            out["sandbox"]["filesystem"]["denyWrite"],
            ["~/.claude/settings.json"],
        )

    def test_idempotent_on_repeat(self) -> None:
        base = {"sandbox": {"filesystem": {"denyRead": ["~/.ssh"]}}}
        once = p.merge_user_common_sandbox_denyread(base, ["~/.ssh", "~/.aws"])
        twice = p.merge_user_common_sandbox_denyread(once, ["~/.ssh", "~/.aws"])
        self.assertEqual(once, twice)
        self.assertEqual(twice["sandbox"]["filesystem"]["denyRead"], ["~/.ssh", "~/.aws"])

    def test_input_is_not_mutated(self) -> None:
        base = {"sandbox": {"filesystem": {"denyRead": ["existing"]}}}
        snapshot = json.loads(json.dumps(base))
        p.merge_user_common_sandbox_denyread(base, ["~/.ssh"])
        self.assertEqual(base, snapshot)

    def test_malformed_sandbox_block_raises(self) -> None:
        with self.assertRaises(ValueError):
            p.merge_user_common_sandbox_denyread({"sandbox": "off"}, ["~/.ssh"])

    def test_malformed_filesystem_raises(self) -> None:
        with self.assertRaises(ValueError):
            p.merge_user_common_sandbox_denyread(
                {"sandbox": {"filesystem": "broken"}}, ["~/.ssh"],
            )

    def test_malformed_deny_read_raises(self) -> None:
        with self.assertRaises(ValueError):
            p.merge_user_common_sandbox_denyread(
                {"sandbox": {"filesystem": {"denyRead": "not-a-list"}}}, ["~/.ssh"],
            )

    def test_non_string_existing_entry_raises(self) -> None:
        # Claude Code's bwrap launcher only accepts the raw-string form
        # at the user-level settings.json; a structured ``{anchor, path}``
        # dict must NOT be silently merged with new entries (and would
        # also blow up set / diff bookkeeping later because dicts are
        # unhashable). The merger refuses with ValueError so the caller
        # aborts before any write.
        with self.assertRaises(ValueError):
            p.merge_user_common_sandbox_denyread(
                {"sandbox": {"filesystem": {"denyRead": [{"anchor": "home", "path": ".aws/**"}]}}},
                ["~/.ssh"],
            )

    def test_dedup_is_value_exact(self) -> None:
        # ``~/.ssh`` and ``~/.ssh/**`` are distinct strings -- dedup is by
        # literal equality, so both survive. This keeps the helper agnostic
        # to glob normalization (which belongs to the bwrap launcher).
        out = p.merge_user_common_sandbox_denyread(
            {"sandbox": {"filesystem": {"denyRead": ["~/.ssh"]}}}, ["~/.ssh/**"],
        )
        self.assertEqual(out["sandbox"]["filesystem"]["denyRead"], ["~/.ssh", "~/.ssh/**"])


class UserCommonSandboxEndToEndTests(unittest.TestCase):
    """CLI ``--user-common-sandbox`` end-to-end against an injected HOME."""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        (self.home / ".claude").mkdir()
        self.settings_path = self.home / ".claude" / "settings.json"
        # Pretend the user has .ssh and .aws but not .kube / .gnupg.
        (self.home / ".ssh").mkdir()
        (self.home / ".aws").mkdir()

    def tearDown(self) -> None:
        self.td.cleanup()

    def _candidates_match(self, deny_read: list[str]) -> None:
        # Only existing directories should appear; non-existent ones must not.
        self.assertIn("~/.ssh", deny_read)
        self.assertIn("~/.aws", deny_read)
        self.assertNotIn("~/.kube", deny_read)
        self.assertNotIn("~/.gnupg", deny_read)

    def test_creates_settings_when_missing(self) -> None:
        # File deliberately absent; the merge should create it with only
        # the sandbox block populated.
        self.assertFalse(self.settings_path.exists())
        # Inject ``home`` via the helper-level API to avoid touching the
        # real home. We call ``process_user_common_sandbox`` directly so
        # the test stays user-agnostic (no $HOME monkey-patching needed).
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertEqual(rc, 0)
        loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self._candidates_match(loaded["sandbox"]["filesystem"]["denyRead"])
        # No spurious extra keys.
        self.assertEqual(set(loaded.keys()), {"sandbox"})

    def test_preserves_unrelated_keys(self) -> None:
        self.settings_path.write_text(json.dumps({
            "theme": "dark",
            "permissions": {"allow": ["Bash(echo)"]},
            "env": {"FOO": "1"},
        }), encoding="utf-8")
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertEqual(rc, 0)
        loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        # Unrelated top-level keys survive untouched.
        self.assertEqual(loaded["theme"], "dark")
        self.assertEqual(loaded["permissions"]["allow"], ["Bash(echo)"])
        self.assertEqual(loaded["env"]["FOO"], "1")
        self._candidates_match(loaded["sandbox"]["filesystem"]["denyRead"])

    def test_existing_deny_read_entries_are_preserved(self) -> None:
        # Operator already added their own credential entry; the merge
        # must NOT remove it.
        self.settings_path.write_text(json.dumps({
            "sandbox": {
                "enabled": True,
                "filesystem": {
                    "denyRead": ["**/*.pem", "/etc/secret"],
                    "denyWrite": ["~/.claude/settings.json"],
                },
            },
        }), encoding="utf-8")
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertEqual(rc, 0)
        loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        deny = loaded["sandbox"]["filesystem"]["denyRead"]
        self.assertIn("**/*.pem", deny)
        self.assertIn("/etc/secret", deny)
        self.assertIn("~/.ssh", deny)
        self.assertIn("~/.aws", deny)
        # Sibling keys preserved.
        self.assertTrue(loaded["sandbox"]["enabled"])
        self.assertEqual(loaded["sandbox"]["filesystem"]["denyWrite"], ["~/.claude/settings.json"])

    def test_idempotent_run_no_changes(self) -> None:
        rc1 = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertEqual(rc1, 0)
        first = self.settings_path.read_text(encoding="utf-8")
        rc2 = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertEqual(rc2, 0)
        # Second run must not modify the file -- byte-identical content.
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), first)

    def test_dry_run_does_not_modify(self) -> None:
        original = json.dumps({"theme": "dark"})
        self.settings_path.write_text(original, encoding="utf-8")
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=True,
            no_backup=True,
        )
        self.assertEqual(rc, 0)
        # File untouched.
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), original)

    def test_invalid_json_aborts(self) -> None:
        self.settings_path.write_text("{not json", encoding="utf-8")
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertNotEqual(rc, 0)
        # Untouched: no backup, no write attempted.
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), "{not json")

    def test_malformed_sandbox_aborts_without_write(self) -> None:
        original = json.dumps({"sandbox": "off"})
        self.settings_path.write_text(original, encoding="utf-8")
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), original)
        baks = list(self.settings_path.parent.glob("settings.json.bak.*"))
        self.assertEqual(baks, [])

    def test_top_level_array_aborts(self) -> None:
        self.settings_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertNotEqual(rc, 0)

    def test_cli_entrypoint(self) -> None:
        # Smoke-test the argparse plumbing: --user-common-sandbox with a
        # custom settings path runs without --role and exits 0. The CLI
        # auto-derives HOME from the ``<DIR>/.claude/settings.json`` shape
        # so existence checks run against the test's temp HOME (which has
        # ``.ssh`` and ``.aws`` but not ``.kube``).
        rc = p.main([
            "--user-common-sandbox",
            "--user-common-settings-path", str(self.settings_path),
            "--no-backup",
        ])
        self.assertEqual(rc, 0)
        loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        deny = loaded["sandbox"]["filesystem"]["denyRead"]
        self.assertIn("~/.ssh", deny)
        self.assertIn("~/.aws", deny)
        self.assertNotIn("~/.kube", deny)

    def test_cli_home_override_takes_precedence(self) -> None:
        # When both --user-common-settings-path and --user-common-home are
        # supplied, the explicit home wins over the auto-derived one.
        alt_home = Path(tempfile.mkdtemp(prefix="user_common_home_"))
        try:
            (alt_home / ".gnupg").mkdir()
            rc = p.main([
                "--user-common-sandbox",
                "--user-common-settings-path", str(self.settings_path),
                "--user-common-home", str(alt_home),
                "--no-backup",
            ])
            self.assertEqual(rc, 0)
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
            deny = loaded["sandbox"]["filesystem"]["denyRead"]
            # Only the alt_home's directories should match -- self.home's
            # ``.ssh`` / ``.aws`` should NOT appear.
            self.assertIn("~/.gnupg", deny)
            self.assertNotIn("~/.ssh", deny)
            self.assertNotIn("~/.aws", deny)
        finally:
            import shutil
            shutil.rmtree(alt_home, ignore_errors=True)

    def test_dry_run_renders_skipped_even_on_noop(self) -> None:
        # First merge populates the file; then a second --dry-run is a
        # no-op merge but should still list the candidates that were
        # skipped (e.g. ``~/.kube`` not existing in this test's HOME) so
        # the operator can audit the result.
        rc = p.process_user_common_sandbox(
            settings_path=self.settings_path,
            home=self.home,
            dry_run=False,
            no_backup=True,
        )
        self.assertEqual(rc, 0)
        # Capture stdout of a subsequent dry-run.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc2 = p.process_user_common_sandbox(
                settings_path=self.settings_path,
                home=self.home,
                dry_run=True,
                no_backup=True,
            )
        self.assertEqual(rc2, 0)
        out = buf.getvalue()
        # No-op header is OK, but the skipped section MUST appear so the
        # operator can see what was excluded.
        self.assertIn("skipped", out)
        self.assertIn("~/.kube", out)


if __name__ == "__main__":
    unittest.main()
