"""Unit tests for ``tools/check_role_configs.py``.

These tests use hand-crafted schemas and synthetic permissions.md fragments
so they stay decoupled from the real repo content — the CI smoke-test that
the real schema + real permissions.md still agree lives in
``.github/workflows/tests.yml`` (``python tools/check_role_configs.py``).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_role_configs as crc  # noqa: E402


MINIMAL_SCHEMA: dict = {
    "version": 1,
    "global": {
        "forbidden_allow_exact": ["Bash(git *)"],
        "forbidden_allow_regex": ["^mcp__claude-peers__"],
    },
    "required_hook_scripts": ["block-git-push.sh"],
    "roles": {
        "secretary": {
            "docs_section": "窓口",
            "settings_paths": [],
            "closed_world": True,
            "required_allow": ["Bash(git add:*)"],
            "allowed_allow_regex": [r"^Bash\(gh [a-z]+:\*\)$"],
            "required_deny": [],
            "required_hooks": [],
            "disallow_allow_regex": [r"^Bash\(\*\)$"],
        },
        "worker": {
            "docs_section": "ワーカー",
            "settings_paths": [],
            "closed_world": False,
            "required_allow": ["Bash(git add:*)"],
            "allowed_allow_regex": [],
            "required_deny": ["Bash(git push *)"],
            "required_hooks": [
                {
                    "event": "PreToolUse",
                    "matcher_contains": "Bash",
                    "command_contains": "block-git-push.sh",
                }
            ],
            "disallow_allow_regex": [],
        },
    },
}


def _good_secretary() -> dict:
    return {"permissions": {"allow": ["Bash(git add:*)"]}}


def _good_worker() -> dict:
    return {
        "permissions": {
            "allow": ["Bash(git add:*)"],
            "deny": ["Bash(git push *)"],
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "bash .hooks/block-git-push.sh"}
                    ],
                }
            ]
        },
    }


class ValidateConfigTests(unittest.TestCase):
    def _validate(self, role: str, config: dict | None) -> list[crc.Finding]:
        role_schema = MINIMAL_SCHEMA["roles"][role]
        return crc.validate_config(
            "test", role, config, role_schema, MINIMAL_SCHEMA["global"]
        )

    # OK cases ---------------------------------------------------------
    def test_good_secretary_passes(self):
        self.assertEqual(self._validate("secretary", _good_secretary()), [])

    def test_good_worker_passes(self):
        self.assertEqual(self._validate("worker", _good_worker()), [])

    # NG cases ---------------------------------------------------------
    def test_missing_config_errors(self):
        findings = self._validate("secretary", None)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing", findings[0].message)

    def test_claude_peers_is_forbidden(self):
        config = _good_secretary()
        config["permissions"]["allow"].append("mcp__claude-peers__send_message")
        findings = self._validate("secretary", config)
        self.assertTrue(any("claude-peers" in f.message for f in findings))

    def test_wide_git_allow_is_forbidden(self):
        config = _good_secretary()
        config["permissions"]["allow"].append("Bash(git *)")
        findings = self._validate("secretary", config)
        self.assertTrue(
            any("forbidden wide allow" in f.message for f in findings),
            msg=[f.message for f in findings],
        )

    def test_role_contract_unlimited_bash(self):
        config = _good_secretary()
        config["permissions"]["allow"].append("Bash(*)")
        findings = self._validate("secretary", config)
        self.assertTrue(any("role contract" in f.message for f in findings))

    def test_missing_required_allow(self):
        findings = self._validate("secretary", {"permissions": {"allow": []}})
        self.assertTrue(any("missing required allow" in f.message for f in findings))

    def test_worker_missing_required_deny(self):
        config = _good_worker()
        config["permissions"]["deny"] = []
        findings = self._validate("worker", config)
        self.assertTrue(any("missing required deny" in f.message for f in findings))

    def test_worker_missing_required_hook(self):
        config = _good_worker()
        config["hooks"] = {}
        findings = self._validate("worker", config)
        self.assertTrue(any("missing required hook" in f.message for f in findings))

    def test_closed_world_flags_unknown_allow(self):
        config = {
            "permissions": {
                "allow": ["Bash(git add:*)", "Bash(unexpected:*)"],
            }
        }
        findings = self._validate("secretary", config)
        self.assertTrue(
            any(
                "unknown allow entry" in f.message and "unexpected" in f.message
                for f in findings
            ),
            msg=[f.message for f in findings],
        )

    def test_closed_world_allows_pattern_match(self):
        config = {
            "permissions": {
                "allow": ["Bash(git add:*)", "Bash(gh pr:*)"],
            }
        }
        findings = self._validate("secretary", config)
        self.assertEqual(findings, [], msg=[f.format() for f in findings])

    def test_open_world_ignores_extras(self):
        config = {
            "permissions": {
                "allow": ["Bash(git add:*)", "Bash(totally new:*)"],
                "deny": ["Bash(git push *)"],
            },
            "hooks": _good_worker()["hooks"],
        }
        findings = self._validate("worker", config)
        self.assertEqual(findings, [], msg=[f.format() for f in findings])

    def test_parse_error_surfaces(self):
        findings = self._validate("secretary", {"__parse_error__": "boom"})
        self.assertEqual(len(findings), 1)
        self.assertIn("parse error", findings[0].message)


class ExtractRoleBlocksTests(unittest.TestCase):
    def test_extract_first_json_block_per_section(self):
        md = (
            "# heading\n\n"
            "## 窓口 (x)\n\n"
            "intro\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"a\"]}}\n```\n\n"
            "## ワーカー\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"b\"]}}\n```\n"
        )
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertEqual(blocks["secretary"]["permissions"]["allow"], ["a"])
        self.assertEqual(blocks["worker"]["permissions"]["allow"], ["b"])

    def test_missing_section_returns_none(self):
        md = "## 窓口\n\n```json\n{\"permissions\": {\"allow\": []}}\n```\n"
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertIsNone(blocks["worker"])

    def test_bilingual_en_headings_match(self):
        # Issue #340: an en mirror permissions.md uses English headings.
        md = (
            "# heading\n\n"
            "## Lead (`<repo>/.claude/settings.local.json`)\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"a\"]}}\n```\n\n"
            "## Worker (dynamically generated)\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"b\"]}}\n```\n"
        )
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertEqual(blocks["secretary"]["permissions"]["allow"], ["a"])
        self.assertEqual(blocks["worker"]["permissions"]["allow"], ["b"])

    def test_bilingual_mixed_ja_and_en_headings(self):
        # A repo mid-translation may have a mix of ja and en headings.
        md = (
            "## 窓口\n\n```json\n{\"permissions\": {\"allow\": [\"a\"]}}\n```\n\n"
            "## Worker\n\n```json\n{\"permissions\": {\"allow\": [\"b\"]}}\n```\n"
        )
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertEqual(blocks["secretary"]["permissions"]["allow"], ["a"])
        self.assertEqual(blocks["worker"]["permissions"]["allow"], ["b"])

    def test_bilingual_secretary_alias_accepted(self):
        # Issue #340 codex review: the codebase calls the 窓口 role
        # "Secretary" in schema descriptions; that variant must work
        # alongside the org-skill-aligned "Lead".
        md = (
            "## Secretary (`<repo>/.claude/settings.local.json`)\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"sec\"]}}\n```\n"
        )
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertEqual(blocks["secretary"]["permissions"]["allow"], ["sec"])

    def test_bilingual_alias_does_not_substring_match(self):
        # Issue #340 codex review: a heading that merely *contains* an
        # alias as a substring (e.g. parenthetical) must not be
        # picked up. ``## Dispatcher (Lead-owned)`` had silently been
        # mis-projected as ``secretary`` under the substring matcher.
        md = (
            "## Dispatcher (Lead-owned)\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"disp\"]}}\n```\n\n"
            "## Lead\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"sec\"]}}\n```\n"
        )
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertEqual(blocks["secretary"]["permissions"]["allow"], ["sec"])

    def test_bilingual_all_alias_table_entries_match(self):
        # Issue #340 codex round 2: every en alias declared in
        # ``_JA_TO_EN_ROLE_HEADING_ALIASES`` must be exercised so a
        # future typo or translation update is caught here. Build a
        # synthetic schema that covers all five canonical roles.
        schema = {
            name: {"docs_section": ja}
            for name, ja in {
                "user_common": "ユーザー共通",
                "secretary": "窓口",
                "dispatcher": "ディスパッチャー",
                "curator": "キュレーター",
                "worker": "ワーカー",
            }.items()
        }
        for ja, aliases in crc._JA_TO_EN_ROLE_HEADING_ALIASES.items():
            for alias in aliases:
                md = (
                    f"## {alias} (`...`)\n\n"
                    f'```json\n{{"permissions": {{"allow": ["{alias}"]}}}}\n```\n'
                )
                blocks = crc.extract_role_blocks(md, schema)
                # Find which role uses this ja heading and assert the
                # alias projects to it.
                role_for_ja = next(
                    role for role, defn in schema.items()
                    if defn["docs_section"] == ja
                )
                self.assertEqual(
                    blocks[role_for_ja]["permissions"]["allow"], [alias],
                    msg=f"alias {alias!r} (for ja {ja!r}) failed to project",
                )

    def test_bilingual_alias_rejects_longer_word(self):
        # ``Lead`` must not match ``Leadership``.
        md = (
            "## Leadership notes\n\n"
            "```json\n{\"permissions\": {\"allow\": [\"x\"]}}\n```\n"
        )
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertIsNone(blocks["secretary"])

    def test_invalid_json_surfaces_parse_error(self):
        md = "## 窓口\n\n```json\n{not json}\n```\n"
        blocks = crc.extract_role_blocks(md, MINIMAL_SCHEMA["roles"])
        self.assertIn("__parse_error__", blocks["secretary"])


class SchemaIntegrityTests(unittest.TestCase):
    def test_unreferenced_required_script_errors(self):
        schema = json.loads(json.dumps(MINIMAL_SCHEMA))
        schema["required_hook_scripts"].append("nonexistent.sh")
        findings = crc.validate_schema_integrity(schema)
        self.assertTrue(
            any("nonexistent.sh" in f.message for f in findings),
            msg=[f.message for f in findings],
        )

    def test_all_referenced_passes(self):
        findings = crc.validate_schema_integrity(MINIMAL_SCHEMA)
        self.assertEqual(findings, [])


class CheckDocsTests(unittest.TestCase):
    def test_ok_docs_pass(self):
        import tempfile

        md = (
            "## 窓口\n\n```json\n"
            + json.dumps(_good_secretary())
            + "\n```\n\n## ワーカー\n\n```json\n"
            + json.dumps(_good_worker())
            + "\n```\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(md)
            path = Path(f.name)
        try:
            findings = crc.check_docs(MINIMAL_SCHEMA, path)
            self.assertEqual(findings, [], msg=[x.format() for x in findings])
        finally:
            path.unlink()

    def test_missing_file_errors(self):
        findings = crc.check_docs(
            MINIMAL_SCHEMA, Path("/definitely/does/not/exist.md")
        )
        self.assertEqual(len(findings), 1)


class CheckOnDiskTests(unittest.TestCase):
    def test_role_override_validates_path(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            settings_dir = tmp_root / ".claude"
            settings_dir.mkdir()
            (settings_dir / "settings.local.json").write_text(
                json.dumps(_good_worker()), encoding="utf-8"
            )
            findings = crc.check_on_disk(
                MINIMAL_SCHEMA,
                tmp_root,
                include_untracked=True,
                role_override="worker",
            )
            self.assertEqual(findings, [], msg=[f.format() for f in findings])

    def test_role_override_unknown_role_errors(self):
        findings = crc.check_on_disk(
            MINIMAL_SCHEMA,
            Path("."),
            include_untracked=True,
            role_override="ghost",
        )
        self.assertTrue(any("unknown --role" in f.message for f in findings))


class CheckWorkerSettingsTests(unittest.TestCase):
    """Coverage for the --include-worker-settings drift path (Issue #99)."""

    SCHEMA = {
        "version": 1,
        "global": {"forbidden_allow_exact": [], "forbidden_allow_regex": []},
        "required_hook_scripts": [],
        "roles": {},
        "worker_roles": {
            "$comment": "test fixture",
            "default": {
                "description": "test default",
                "permissions": {"allow": ["Bash(sleep:*)"], "deny": []},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "bash \"{claude_org_path}/.hooks/x.sh\"",
                                }
                            ],
                        }
                    ]
                },
                "env": {
                    "WORKER_DIR": "{worker_dir}",
                    "CLAUDE_ORG_PATH": "{claude_org_path}",
                },
            },
        },
    }

    def _emit(self, worker_dir: str, claude_org_path: str) -> dict:
        from claude_org_runtime.settings.generator import render_role
        return render_role(
            self.SCHEMA,
            role="default",
            worker_dir=worker_dir,
            claude_org_path=claude_org_path,
        )

    def test_generated_file_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wd = base / "w1"
            (wd / ".claude").mkdir(parents=True)
            cfg = self._emit(str(wd.resolve()), "/abs/co")
            (wd / ".claude" / "settings.local.json").write_text(
                json.dumps(cfg), encoding="utf-8"
            )
            findings = crc.check_worker_settings(self.SCHEMA, base)
            self.assertEqual(findings, [], [f.format() for f in findings])

    def test_drift_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wd = base / "w1"
            (wd / ".claude").mkdir(parents=True)
            (wd / ".claude" / "settings.local.json").write_text(
                json.dumps({"permissions": {"allow": ["Bash(rogue)"]}}),
                encoding="utf-8",
            )
            findings = crc.check_worker_settings(self.SCHEMA, base)
            self.assertTrue(
                any("does not match" in f.message for f in findings),
                [f.format() for f in findings],
            )

    def test_inconsistent_path_substitution_rejected(self):
        # Two occurrences of {claude_org_path} resolved to different values
        # — a copy/paste class of drift the wildcard-only matcher would miss.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wd = base / "w1"
            (wd / ".claude").mkdir(parents=True)
            cfg = self._emit(str(wd.resolve()), "/abs/co")
            cfg["env"]["CLAUDE_ORG_PATH"] = "/different/co"
            (wd / ".claude" / "settings.local.json").write_text(
                json.dumps(cfg), encoding="utf-8"
            )
            findings = crc.check_worker_settings(self.SCHEMA, base)
            self.assertTrue(
                any("does not match" in f.message for f in findings),
                [f.format() for f in findings],
            )

    def test_wrong_worker_dir_rejected(self):
        # File is under <base>/w1 but its WORKER_DIR env points at /elsewhere.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wd = base / "w1"
            (wd / ".claude").mkdir(parents=True)
            cfg = self._emit("/elsewhere", "/abs/co")
            (wd / ".claude" / "settings.local.json").write_text(
                json.dumps(cfg), encoding="utf-8"
            )
            findings = crc.check_worker_settings(self.SCHEMA, base)
            self.assertTrue(
                any("does not match" in f.message for f in findings),
                [f.format() for f in findings],
            )

    def test_missing_base_dir_errors(self):
        findings = crc.check_worker_settings(
            self.SCHEMA, Path("/no/such/dir/__nope__")
        )
        self.assertTrue(any("does not exist" in f.message for f in findings))

    def test_worktrees_descent_default_true(self):
        # 0.3.1 contract: include_worktrees=True is the ja default; a
        # generated settings.local.json sitting under .worktrees/<branch>/
        # must be enumerated. Refs cross-review M4.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wt = base / ".worktrees" / "branch-a"
            (wt / ".claude").mkdir(parents=True)
            cfg = self._emit(str(wt.resolve()), "/abs/co")
            (wt / ".claude" / "settings.local.json").write_text(
                json.dumps(cfg), encoding="utf-8"
            )
            findings = crc.check_worker_settings(self.SCHEMA, base)
            self.assertEqual(findings, [], [f.format() for f in findings])

    def test_worktrees_descent_detects_drift(self):
        # And: a *broken* settings.local.json under .worktrees/<branch>
        # must produce a drift finding rather than being silently
        # skipped. Refs cross-review M4.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wt = base / ".worktrees" / "branch-a"
            (wt / ".claude").mkdir(parents=True)
            (wt / ".claude" / "settings.local.json").write_text(
                json.dumps({"permissions": {"allow": ["Bash(rogue)"]}}),
                encoding="utf-8",
            )
            findings = crc.check_worker_settings(self.SCHEMA, base)
            self.assertTrue(
                any("does not match" in f.message for f in findings),
                [f.format() for f in findings],
            )


class IsGitTrackedFailClosedTests(unittest.TestCase):
    """0.3.1: _is_git_tracked must raise rather than return False on
    indeterminate cases so check_on_disk records a Finding(ERROR)
    instead of silently skipping. Refs cross-review M1."""

    def test_path_outside_root_raises(self):
        with self.assertRaises(crc._GitTrackedError):
            crc._is_git_tracked(Path("/totally/elsewhere/file"), REPO_ROOT)

    def test_git_fatal_exit_raises(self):
        # `git ls-files --error-unmatch` exits 128 on safe.directory /
        # not-a-git-repo / corrupt index. Those must surface as
        # _GitTrackedError, not silently fall through as "untracked".
        # Refs codex self-review Blocker (M1 follow-up).
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            non_repo = Path(tmp)
            target = non_repo / "file.json"
            target.write_text("{}", encoding="utf-8")
            with self.assertRaises(crc._GitTrackedError) as ctx:
                crc._is_git_tracked(target, non_repo)
            # Sanity-check the error text mentions the non-zero exit
            # rather than masquerading as "git not found".
            self.assertIn("git ls-files exited", ctx.exception.reason)

    def test_check_on_disk_records_finding_when_git_missing(self):
        # Simulate `git not on PATH` by pointing PATH at an empty dir.
        import os as _os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            settings_dir = tmp_root / ".claude"
            settings_dir.mkdir()
            (settings_dir / "settings.local.json").write_text(
                json.dumps(_good_worker()), encoding="utf-8"
            )
            schema = {
                "version": 1,
                "global": {"forbidden_allow_exact": [], "forbidden_allow_regex": []},
                "required_hook_scripts": [],
                "roles": {
                    "worker": {
                        "settings_paths": [".claude/settings.local.json"],
                        "closed_world": False,
                        "required_allow": [],
                        "allowed_allow_regex": [],
                        "required_deny": [],
                        "required_hooks": [],
                        "disallow_allow_regex": [],
                    }
                },
                "worker_roles": {},
            }
            saved_path = _os.environ.get("PATH", "")
            try:
                empty = Path(tmp) / "empty_path"
                empty.mkdir(exist_ok=True)
                _os.environ["PATH"] = str(empty)
                findings = crc.check_on_disk(
                    schema, tmp_root, include_untracked=False
                )
            finally:
                _os.environ["PATH"] = saved_path
            self.assertTrue(
                any(
                    "could not determine git-tracked status" in f.message
                    and f.severity == "ERROR"
                    for f in findings
                ),
                [f.format() for f in findings],
            )


HOOK_PATH_SCHEMA: dict = {
    "version": 1,
    "global": {"forbidden_allow_exact": [], "forbidden_allow_regex": []},
    "required_hook_scripts": ["block-workers-delete.sh"],
    "roles": {"secretary": {"docs_section": "窓口", "settings_paths": []}},
}


def _md_with_secretary_hook(command: str) -> str:
    block = {
        "permissions": {"allow": []},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": command}],
                }
            ]
        },
    }
    return (
        "# perms\n\n## 窓口 (`<repo>/.claude/settings.local.json`)\n\n"
        "```json\n" + json.dumps(block, indent=2) + "\n```\n"
    )


class HookCommandPathTests(unittest.TestCase):
    """Hook commands must normalize to ``<org root>/.hooks/<script>``.

    Issue #768: ``required_hooks.command_contains`` matches the bare
    script basename anywhere in the command string, so a relative
    ``bash .hooks/x.sh`` and the absolute quoted form are the same to
    it -- but the relative form silently no-ops for any role whose cwd
    is not the org root. Every ``_flagged`` case below passes
    ``command_contains``; only this check rejects them.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / ".hooks").mkdir()
        (self.root / ".hooks" / "block-workers-delete.sh").write_text(
            "#!/usr/bin/env bash\n", encoding="utf-8"
        )

    def tearDown(self):
        self.td.cleanup()

    def _run(self, command, schema=None):
        md = self.root / "permissions.md"
        md.write_text(_md_with_secretary_hook(command), encoding="utf-8")
        return crc.check_hook_command_paths(
            schema or HOOK_PATH_SCHEMA, md, self.root
        )

    def _assert_flagged(self, command, needle="not anchored at the org root"):
        findings = self._run(command)
        self.assertEqual(len(findings), 1, [f.format() for f in findings])
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertIn(needle, findings[0].message)

    def _assert_clean(self, command):
        findings = self._run(command)
        self.assertEqual(findings, [], [f.format() for f in findings])

    def test_relative_hooks_dir_is_flagged(self):
        # The literal Issue #768 shape.
        self._assert_flagged("bash .hooks/block-workers-delete.sh")

    def test_bare_filename_is_flagged(self):
        # Cheapest reintroduction of #768, and the case a ``.hooks/``-keyed
        # trigger would skip entirely.
        self._assert_flagged("bash block-workers-delete.sh")

    def test_dot_slash_filename_is_flagged(self):
        self._assert_flagged("bash ./block-workers-delete.sh")

    def test_other_directory_is_flagged(self):
        self._assert_flagged("bash scripts/block-workers-delete.sh")

    def test_quoted_bare_filename_is_flagged(self):
        self._assert_flagged('bash "block-workers-delete.sh"')

    def test_cd_then_bare_filename_is_flagged(self):
        self._assert_flagged(
            'cd "{claude_org_path}" && bash block-workers-delete.sh'
        )

    def test_anchored_command_with_unanchored_fallback_is_flagged(self):
        self._assert_flagged(
            'bash "{claude_org_path}/.hooks/block-workers-delete.sh"'
            " || bash block-workers-delete.sh"
        )

    def test_missing_separator_is_flagged(self):
        self._assert_flagged('bash "{claude_org_path}block-workers-delete.sh"')

    def test_foreign_absolute_root_is_flagged(self):
        # A leading-slash ``command_contains`` fragment would pass this.
        self._assert_flagged(
            'bash "/some/other/root/.hooks/block-workers-delete.sh"'
        )

    def test_parent_traversal_is_flagged(self):
        self._assert_flagged(
            'bash "{claude_org_path}/.hooks/../../etc/block-workers-delete.sh"'
        )

    def test_missing_script_is_flagged(self):
        self._assert_flagged(
            'bash "{claude_org_path}/.hooks/block-does-not-exist.sh"',
            needle="does not exist",
        )

    def test_placeholder_absolute_form_passes(self):
        self._assert_clean(
            'bash "{claude_org_path}/.hooks/block-workers-delete.sh"'
        )

    def test_windows_backslash_form_passes(self):
        self._assert_clean(
            'bash "{claude_org_path}\\.hooks\\block-workers-delete.sh"'
        )

    def test_command_without_hook_reference_is_ignored(self):
        self._assert_clean("echo hello")

    def test_operator_custom_hook_is_ignored(self):
        # Not a required hook script and not under .hooks/: operator-owned.
        self._assert_clean('bash "{claude_org_path}/mytools/my-own-lint.sh"')

    def test_suffix_glued_to_quoted_path_is_flagged(self):
        # The shell joins the quote to what abuts it, so this really runs
        # ...block-workers-delete.sh.bak, which does not exist -> guard dead.
        # Validating the quoted segment alone would call this anchored.
        self._assert_flagged(
            'bash "{claude_org_path}/.hooks/block-workers-delete.sh".bak',
            needle="does not exist",
        )

    def test_prefix_glued_to_quoted_path_is_flagged(self):
        self._assert_flagged(
            'bash /wrong"{claude_org_path}/.hooks/block-workers-delete.sh"'
        )

    def test_symlinked_root_is_accepted(self):
        # A checkout reached through a symlink is lexically different but
        # identifies the same script; it must not be reported.
        link = Path(self.td.name).parent / (self.root.name + "-link")
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        try:
            md = self.root / "permissions.md"
            md.write_text(
                _md_with_secretary_hook(
                    'bash "' + link.as_posix()
                    + '/.hooks/block-workers-delete.sh"'
                ),
                encoding="utf-8",
            )
            findings = crc.check_hook_command_paths(
                HOOK_PATH_SCHEMA, md, self.root
            )
        finally:
            link.unlink()
        self.assertEqual(findings, [], [f.format() for f in findings])

    def test_worker_roles_template_is_checked(self):
        schema = dict(HOOK_PATH_SCHEMA)
        schema["worker_roles"] = {
            "$comment": "string entries must be skipped, not crashed on",
            "default": {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "bash .hooks/block-workers-delete.sh",
                                }
                            ],
                        }
                    ]
                }
            },
        }
        findings = self._run(
            'bash "{claude_org_path}/.hooks/block-workers-delete.sh"', schema
        )
        self.assertEqual(len(findings), 1, [f.format() for f in findings])
        self.assertIn("worker_roles.default", findings[0].source)

    def test_non_org_source_root_returns_no_findings(self):
        # Guard against a false-positive storm: pointing the check at a
        # directory that ships no .hooks/ must not flag every template.
        with tempfile.TemporaryDirectory() as bare:
            md = self.root / "permissions.md"
            md.write_text(
                _md_with_secretary_hook("bash .hooks/block-workers-delete.sh"),
                encoding="utf-8",
            )
            findings = crc.check_hook_command_paths(
                HOOK_PATH_SCHEMA, md, Path(bare)
            )
        self.assertEqual(findings, [])


class OnDiskHookPathTests(unittest.TestCase):
    """Root-anchoring check for *generated* settings files.

    Opt-in: only the ``--include-local`` / ``--role`` paths run it, so
    the CI invocation stays byte-identical. Real ``settings.local.json``
    files are gitignored, so this is the only mechanical way to confirm
    an installed terminal was actually migrated.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / ".hooks").mkdir()
        (self.root / ".hooks" / "block-workers-delete.sh").write_text(
            "#!/usr/bin/env bash\n", encoding="utf-8"
        )
        self.settings = self.root / ".claude" / "settings.local.json"
        self.settings.parent.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def _config(self, command):
        return {
            "permissions": {"allow": []},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": command}],
                    }
                ]
            },
        }

    def _run(self, command):
        return crc.check_on_disk_hook_paths(
            HOOK_PATH_SCHEMA,
            self.settings,
            "secretary",
            self._config(command),
            self.root,
        )

    def test_relative_command_on_disk_is_flagged(self):
        findings = self._run("bash .hooks/block-workers-delete.sh")
        self.assertEqual(len(findings), 1, [f.format() for f in findings])
        self.assertIn("not anchored at the org root", findings[0].message)

    def test_resolved_absolute_command_passes(self):
        cmd = (
            'bash "' + self.root.resolve().as_posix()
            + '/.hooks/block-workers-delete.sh"'
        )
        self.assertEqual(self._run(cmd), [])

    def test_claude_project_dir_variable_passes(self):
        # Claude Code expands this itself; tracked .claude/settings.json
        # legitimately uses it.
        self.assertEqual(
            self._run(
                'bash "${CLAUDE_PROJECT_DIR}/.hooks/block-workers-delete.sh"'
            ),
            [],
        )

    def test_unresolved_prune_placeholder_on_disk_is_flagged(self):
        # The "pasted the permissions.md sample without resolving it" case:
        # the literal placeholder is not a real path, so the guard is dead.
        findings = self._run(
            'bash "{claude_org_path}/.hooks/block-workers-delete.sh"'
        )
        self.assertEqual(len(findings), 1, [f.format() for f in findings])
        self.assertIn("not anchored at the org root", findings[0].message)

    def test_declared_org_path_anchors_a_worktree_worker(self):
        # A worker in a worktree has root=<worktree> but its hooks correctly
        # point at the central checkout named by env.CLAUDE_ORG_PATH.
        # Anchoring on root would flag every valid hook.
        worktree = self.root / ".worktrees" / "task"
        (worktree / ".claude").mkdir(parents=True)
        config = self._config(
            'bash "' + self.root.resolve().as_posix()
            + '/.hooks/block-workers-delete.sh"'
        )
        config["env"] = {"CLAUDE_ORG_PATH": self.root.resolve().as_posix()}
        findings = crc.check_on_disk_hook_paths(
            HOOK_PATH_SCHEMA,
            worktree / ".claude" / "settings.local.json",
            "worker",
            config,
            worktree,
        )
        self.assertEqual(findings, [], [f.format() for f in findings])

    def test_declared_org_path_still_flags_a_relative_command(self):
        config = self._config("bash .hooks/block-workers-delete.sh")
        config["env"] = {"CLAUDE_ORG_PATH": self.root.resolve().as_posix()}
        findings = crc.check_on_disk_hook_paths(
            HOOK_PATH_SCHEMA, self.settings, "worker", config, self.root
        )
        self.assertEqual(len(findings), 1, [f.format() for f in findings])

    def test_project_dir_variable_resolves_to_the_project_not_the_org_root(self):
        # ${CLAUDE_PROJECT_DIR} expands to the directory holding the settings
        # file. When that differs from the org root, a script that exists only
        # centrally makes this command dead and it must be reported.
        worktree = self.root / ".worktrees" / "task"
        (worktree / ".claude").mkdir(parents=True)
        config = self._config(
            'bash "${CLAUDE_PROJECT_DIR}/.hooks/block-workers-delete.sh"'
        )
        config["env"] = {"CLAUDE_ORG_PATH": self.root.resolve().as_posix()}
        findings = crc.check_on_disk_hook_paths(
            HOOK_PATH_SCHEMA,
            worktree / ".claude" / "settings.local.json",
            "worker",
            config,
            worktree,
        )
        self.assertEqual(len(findings), 1, [f.format() for f in findings])

    def test_project_dir_variable_passes_when_script_is_present(self):
        worktree = self.root / ".worktrees" / "task"
        (worktree / ".claude").mkdir(parents=True)
        (worktree / ".hooks").mkdir()
        (worktree / ".hooks" / "block-workers-delete.sh").write_text(
            "#!/usr/bin/env bash\n", encoding="utf-8"
        )
        config = self._config(
            'bash "${CLAUDE_PROJECT_DIR}/.hooks/block-workers-delete.sh"'
        )
        config["env"] = {"CLAUDE_ORG_PATH": self.root.resolve().as_posix()}
        findings = crc.check_on_disk_hook_paths(
            HOOK_PATH_SCHEMA,
            worktree / ".claude" / "settings.local.json",
            "worker",
            config,
            worktree,
        )
        self.assertEqual(findings, [], [f.format() for f in findings])

    def test_stale_declared_org_path_is_reported(self):
        # A moved / deleted checkout leaves every absolute hook command dead;
        # silently skipping would hide a broken installation.
        config = self._config("bash /gone/.hooks/block-workers-delete.sh")
        config["env"] = {"CLAUDE_ORG_PATH": str(self.root / "gone")}
        findings = crc.check_on_disk_hook_paths(
            HOOK_PATH_SCHEMA, self.settings, "worker", config, self.root
        )
        self.assertEqual(len(findings), 1, [f.format() for f in findings])
        self.assertIn("no .hooks/", findings[0].message)

    def test_skipped_when_root_ships_no_hooks_dir(self):
        with tempfile.TemporaryDirectory() as bare:
            findings = crc.check_on_disk_hook_paths(
                HOOK_PATH_SCHEMA,
                self.settings,
                "secretary",
                self._config("bash .hooks/block-workers-delete.sh"),
                Path(bare),
            )
        self.assertEqual(findings, [])

    def _check_on_disk(self, include_untracked):
        schema = {
            **HOOK_PATH_SCHEMA,
            "roles": {
                "secretary": {
                    "docs_section": "窓口",
                    "settings_paths": [".claude/settings.local.json"],
                }
            },
        }
        self.settings.write_text(
            json.dumps(self._config("bash .hooks/block-workers-delete.sh")),
            encoding="utf-8",
        )
        return crc.check_on_disk(
            schema,
            self.root,
            include_untracked=include_untracked,
            role_override="secretary",
        )

    def test_opt_in_path_reports_the_finding(self):
        messages = [f.message for f in self._check_on_disk(True)]
        self.assertTrue(
            any("not anchored at the org root" in m for m in messages),
            messages,
        )

    def test_default_path_does_not_report_the_finding(self):
        # CI does not pass --include-local / --role, so its result must be
        # unchanged by this feature.
        messages = [f.message for f in self._check_on_disk(False)]
        self.assertFalse(
            any("not anchored at the org root" in m for m in messages),
            messages,
        )


class NonOrgAuditRootTests(unittest.TestCase):
    """``--root`` pointing outside an org checkout must stay non-fatal.

    ``check_hook_command_paths`` validates the SoT templates, which ship
    next to ``.hooks/``, so ``run()`` anchors it at ``REPO_ROOT`` rather
    than the audit root. Without that, every template command would be
    reported as a missing script whenever ``--root`` is elsewhere.
    """

    def test_docs_only_run_with_foreign_root_is_clean(self):
        with tempfile.TemporaryDirectory() as bare:
            findings = crc.run(
                schema_path=crc.DEFAULT_SCHEMA,
                permissions_md=crc.DEFAULT_PERMISSIONS_MD,
                root=Path(bare),
                include_on_disk=False,
            )
        self.assertEqual(
            findings, [], msg="\n".join(f.format() for f in findings)
        )

    def test_main_docs_only_with_foreign_root_exits_zero(self):
        with tempfile.TemporaryDirectory() as bare:
            rc = crc.main(["--docs-only", "--root", bare])
        self.assertEqual(rc, 0)


class RealRepoSmokeTests(unittest.TestCase):
    """Sanity check: the real schema + real permissions.md must pass.

    If these ever fail, either (a) the docs legitimately changed and the
    schema needs updating, or (b) drift has been introduced.
    """

    def test_docs_projection_is_consistent(self):
        findings = crc.run(
            schema_path=crc.DEFAULT_SCHEMA,
            permissions_md=crc.DEFAULT_PERMISSIONS_MD,
            root=crc.REPO_ROOT,
            include_on_disk=True,
        )
        self.assertEqual(
            findings, [], msg="\n".join(f.format() for f in findings)
        )

    def test_real_repo_hook_commands_are_root_anchored(self):
        # Named regression lock for Issue #768: a reintroduced relative hook
        # command fails here with a legible test name, not only inside the
        # aggregate projection smoke above.
        schema = crc.load_schema(crc.DEFAULT_SCHEMA)
        findings = crc.check_hook_command_paths(
            schema, crc.DEFAULT_PERMISSIONS_MD, crc.REPO_ROOT
        )
        self.assertEqual(
            findings, [], msg="\n".join(f.format() for f in findings)
        )


if __name__ == "__main__":
    unittest.main()
