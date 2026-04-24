"""Unit tests for ``tools/check_role_configs.py``.

These tests use hand-crafted schemas and synthetic permissions.md fragments
so they stay decoupled from the real repo content — the CI smoke-test that
the real schema + real permissions.md still agree lives in
``.github/workflows/tests.yml`` (``python tools/check_role_configs.py``).
"""

from __future__ import annotations

import json
import sys
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


if __name__ == "__main__":
    unittest.main()
