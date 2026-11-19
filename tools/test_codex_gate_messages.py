"""Worker-facing Codex gate wording must not teach a variable-bearing command.

The worker settings register the gate by absolute path only, so a command
typed with a variable runs inside the sandbox and the gate reports
not-established. The wording the worker reads must therefore point at the
command rendered in the brief, not at a variable form.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import codex_round_cap as cap  # noqa: E402

CANON = "/org/tools/codex_review_gate.sh"
ORG = "/org"
# Forms that name the gate through a variable, placeholder, or relative path.
BAD_FRAGMENTS = (
    "$CLAUDE_ORG_PATH",
    "${CLAUDE_ORG_PATH}",
    "<claude_org_path>",
    "bash tools/codex_review_gate.sh",
)


def _bad(text: str) -> "list[str]":
    return [f for f in BAD_FRAGMENTS if f in text]


def _run_hook(script: str, payload: dict) -> str:
    proc = subprocess.run(
        ["bash", str(ROOT / ".hooks" / script)],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    return proc.stderr


class MessageTest(unittest.TestCase):
    def test_constants_have_no_variable_form(self):
        for name in ("GATE_FORM", "DESIGN_FORM", "MSG_DIRECT", "MSG_GATE_FORM"):
            self.assertEqual(_bad(getattr(cap, name)), [], name)

    def test_direct_codex_deny_message(self):
        msg = cap.classify("codex exec review", CANON, ORG)[1]
        self.assertTrue(msg)
        self.assertEqual(_bad(msg), [])

    def test_noncanonical_gate_deny_message(self):
        msg = cap.classify('bash "tools/codex_review_gate.sh"', CANON, ORG)[1]
        self.assertTrue(msg)
        self.assertEqual(_bad(msg), [])

    def test_spawn_codex_pane_deny_message(self):
        out = cap.run_hook(
            json.dumps({"tool_name": "mcp__x__spawn_codex_pane", "tool_input": {}}),
            {"WORKER_DIR": "/w", "CLAUDE_ORG_PATH": ORG},
        )
        self.assertTrue(out.startswith("DENY"))
        self.assertEqual(_bad(out), [])

    def test_over_cap_hook_spawn_codex_pane_message(self):
        err = _run_hook("block-codex-over-cap.sh",
                        {"tool_name": "mcp__x__spawn_codex_pane", "tool_input": {}})
        self.assertIn("ブロック", err)
        self.assertEqual(_bad(err), [])

    def test_rescue_hook_message(self):
        err = _run_hook("block-codex-rescue.sh",
                        {"tool_name": "Skill", "tool_input": {"skill": "codex:rescue"}})
        self.assertIn("ブロック", err)
        self.assertEqual(_bad(err), [])


class SourceWordingTest(unittest.TestCase):
    def test_files_do_not_show_variable_form(self):
        self.assertEqual(_bad(cap.__doc__ or ""), [], "codex_round_cap docstring")
        for rel in (
            ".hooks/block-codex-over-cap.sh",
            ".hooks/block-codex-rescue.sh",
            "docs/journal-events.md",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertEqual(_bad(text), [], rel)


if __name__ == "__main__":
    unittest.main()
