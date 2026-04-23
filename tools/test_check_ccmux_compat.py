"""Unit tests for tools/check_ccmux_compat.py (Issue #61).

Run with:
  py -3 -m unittest tools.test_check_ccmux_compat
  (from repo root, or add aainc-ops to PYTHONPATH)
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_ccmux_compat as mod  # noqa: E402


class ParseVersionTests(unittest.TestCase):
    def test_parses_ccmux_prefixed_output(self) -> None:
        self.assertEqual(mod.parse_version("ccmux 0.18.0"), (0, 18, 0))

    def test_parses_bare_semver(self) -> None:
        self.assertEqual(mod.parse_version("0.14.0\n"), (0, 14, 0))

    def test_parses_with_suffix(self) -> None:
        self.assertEqual(mod.parse_version("ccmux 0.18.2-dev"), (0, 18, 2))

    def test_returns_none_when_absent(self) -> None:
        self.assertIsNone(mod.parse_version("no version here"))
        self.assertIsNone(mod.parse_version(""))


class CmpVersionTests(unittest.TestCase):
    def test_equal(self) -> None:
        self.assertEqual(mod.cmp_version((0, 18, 0), (0, 18, 0)), 0)

    def test_older_minor(self) -> None:
        self.assertEqual(mod.cmp_version((0, 17, 9), (0, 18, 0)), -1)

    def test_newer_patch(self) -> None:
        self.assertEqual(mod.cmp_version((0, 18, 1), (0, 18, 0)), 1)

    def test_newer_major(self) -> None:
        self.assertEqual(mod.cmp_version((1, 0, 0), (0, 18, 0)), 1)


class RequiredToolsContract(unittest.TestCase):
    """Guard against accidentally dropping a required tool from the list."""

    def test_has_structured_launch_tools(self) -> None:
        self.assertIn("spawn_claude_pane", mod.REQUIRED_MCP_TOOLS)
        self.assertIn("set_pane_identity", mod.REQUIRED_MCP_TOOLS)

    def test_has_peer_comms_tools(self) -> None:
        for t in ("list_peers", "send_message", "check_messages"):
            self.assertIn(t, mod.REQUIRED_MCP_TOOLS)

    def test_has_pty_tools(self) -> None:
        for t in ("inspect_pane", "send_keys", "poll_events"):
            self.assertIn(t, mod.REQUIRED_MCP_TOOLS)

    def test_no_duplicates(self) -> None:
        self.assertEqual(
            len(mod.REQUIRED_MCP_TOOLS),
            len(set(mod.REQUIRED_MCP_TOOLS)),
        )


class JsonShapeTests(unittest.TestCase):
    """The JSON output is a machine contract for Foreman/Secretary."""

    def test_json_has_stable_shape(self) -> None:
        report = mod.CheckReport()
        report.ccmux_version = "0.18.0"
        report.ccmux_version_tuple = [0, 18, 0]
        report.mcp_registered = True
        report.mcp_tools_found = list(mod.REQUIRED_MCP_TOOLS)

        # Capture stdout by redirecting
        import io
        buf = io.StringIO()
        saved = sys.stdout
        sys.stdout = buf
        try:
            mod.emit_json(report)
        finally:
            sys.stdout = saved

        doc = json.loads(buf.getvalue())
        self.assertIn("ok", doc)
        self.assertIn("ccmux", doc)
        self.assertIn("mcp", doc)
        self.assertIn("version", doc["ccmux"])
        self.assertIn("tools_required", doc["mcp"])
        self.assertIn("tools_missing", doc["mcp"])


if __name__ == "__main__":
    unittest.main()
