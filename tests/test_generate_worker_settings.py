"""Unit tests for ``tools/generate_worker_settings.py``."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import generate_worker_settings as gws  # noqa: E402


def _run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gws.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class GenerateWorkerSettingsTest(unittest.TestCase):
    def test_role_default_emits_valid_json(self):
        rc, stdout, _ = _run(
            "--role", "default",
            "--worker-dir", "/tmp/wd",
            "--claude-org-path", "/tmp/co",
        )
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        self.assertIn("permissions", data)
        self.assertIn("hooks", data)
        self.assertIn("env", data)
        self.assertEqual(data["env"]["WORKER_DIR"], "/tmp/wd")
        self.assertEqual(data["env"]["CLAUDE_ORG_PATH"], "/tmp/co")
        # description is metadata, must not leak into emitted settings.
        self.assertNotIn("description", data)

    def test_role_resolves_paths_in_hook_commands(self):
        rc, stdout, _ = _run(
            "--role", "default",
            "--worker-dir", "/abs/worker",
            "--claude-org-path", "/abs/claude-org",
        )
        self.assertEqual(rc, 0)
        text = stdout
        self.assertNotIn("{worker_dir}", text)
        self.assertNotIn("{claude_org_path}", text)
        data = json.loads(text)
        bash_hooks = next(
            entry for entry in data["hooks"]["PreToolUse"]
            if entry["matcher"] == "Bash"
        )
        commands = [h["command"] for h in bash_hooks["hooks"]]
        self.assertTrue(
            any("/abs/claude-org/.hooks/block-git-push.sh" in c for c in commands),
            commands,
        )

    def test_unknown_role_exits_nonzero(self):
        rc, _, stderr = _run(
            "--role", "no-such-role",
            "--worker-dir", "/tmp/wd",
            "--claude-org-path", "/tmp/co",
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("unknown worker role", stderr)

    def test_role_claude_org_self_edit_drops_block_org_structure(self):
        rc, stdout, _ = _run(
            "--role", "claude-org-self-edit",
            "--worker-dir", "/tmp/wd",
            "--claude-org-path", "/tmp/co",
        )
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        all_commands = [
            h["command"]
            for entry in data["hooks"]["PreToolUse"]
            for h in entry["hooks"]
        ]
        self.assertFalse(
            any("block-org-structure.sh" in c for c in all_commands),
            f"claude-org-self-edit must not include block-org-structure.sh: {all_commands}",
        )
        self.assertTrue(
            any("check-worker-boundary.sh" in c for c in all_commands),
            "boundary check must remain",
        )
        self.assertTrue(
            any("block-git-push.sh" in c for c in all_commands),
            "block-git-push must remain",
        )

    def test_role_doc_audit_has_no_write_allows(self):
        rc, stdout, _ = _run(
            "--role", "doc-audit",
            "--worker-dir", "/tmp/wd",
            "--claude-org-path", "/tmp/co",
        )
        self.assertEqual(rc, 0)
        data = json.loads(stdout)
        allow = data["permissions"]["allow"]
        # Read-only contract: no add / commit / write-side git verbs.
        for forbidden in ("git add", "git commit", "git push", "git checkout"):
            self.assertFalse(
                any(forbidden in entry for entry in allow),
                f"doc-audit allow must not include {forbidden!r}: {allow}",
            )

    def test_out_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "settings.local.json"
            rc, _, _ = _run(
                "--role", "default",
                "--worker-dir", "/tmp/wd",
                "--claude-org-path", "/tmp/co",
                "--out", str(target),
            )
            self.assertEqual(rc, 0)
            self.assertTrue(target.is_file())
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("permissions", data)


if __name__ == "__main__":
    unittest.main()
