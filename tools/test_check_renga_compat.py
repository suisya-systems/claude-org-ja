"""Unit tests for tools/check_renga_compat.py (Issue #61).

Run with:
  py -3 -m unittest tools.test_check_renga_compat
  (from repo root, or add claude-org to PYTHONPATH)
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_renga_compat as mod  # noqa: E402


class ParseVersionTests(unittest.TestCase):
    def test_parses_renga_prefixed_output(self) -> None:
        self.assertEqual(mod.parse_version("renga 0.18.0"), (0, 18, 0))

    def test_parses_bare_semver(self) -> None:
        self.assertEqual(mod.parse_version("0.14.0\n"), (0, 14, 0))

    def test_parses_with_suffix(self) -> None:
        self.assertEqual(mod.parse_version("renga 0.18.2-dev"), (0, 18, 2))

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


class ParseToolsListResponseTests(unittest.TestCase):
    """Cover the stdio parse path without spawning renga."""

    def test_extracts_tools_from_tools_list_response(self) -> None:
        payload = (
            '{"id":0,"jsonrpc":"2.0","result":{"capabilities":{}}}\n'
            '{"id":1,"jsonrpc":"2.0","result":{"tools":['
            '{"name":"list_panes"},{"name":"send_message"}'
            ']}}\n'
        )
        found = mod.parse_tools_list_response(payload)
        self.assertEqual(found, {"list_panes", "send_message"})

    def test_returns_none_when_no_tools_response(self) -> None:
        payload = (
            '{"id":0,"jsonrpc":"2.0","result":{"capabilities":{}}}\n'
        )
        self.assertIsNone(mod.parse_tools_list_response(payload))

    def test_skips_malformed_lines(self) -> None:
        payload = (
            'not json\n'
            '\n'
            '{"id":1,"jsonrpc":"2.0","result":{"tools":['
            '{"name":"list_panes"}]}}\n'
        )
        found = mod.parse_tools_list_response(payload)
        self.assertEqual(found, {"list_panes"})

    def test_skips_tools_with_missing_name(self) -> None:
        payload = (
            '{"id":1,"jsonrpc":"2.0","result":{"tools":['
            '{"name":"list_panes"},{}'
            ']}}\n'
        )
        found = mod.parse_tools_list_response(payload)
        self.assertEqual(found, {"list_panes"})

    def test_empty_input(self) -> None:
        self.assertIsNone(mod.parse_tools_list_response(""))


class ToolMismatchTests(unittest.TestCase):
    def test_subset_reports_missing_tools(self) -> None:
        # Simulate check_mcp_tool_surface's mismatch branch without
        # subprocessing: a subset-only payload should surface missing tools.
        payload = (
            '{"id":1,"jsonrpc":"2.0","result":{"tools":[{"name":"list_panes"}]}}'
        )
        found = mod.parse_tools_list_response(payload)
        assert found is not None
        missing = [t for t in mod.REQUIRED_MCP_TOOLS if t not in found]
        self.assertIn("spawn_claude_pane", missing)
        self.assertIn("set_pane_identity", missing)


class JsonShapeTests(unittest.TestCase):
    """The JSON output is a machine contract for Foreman/Secretary."""

    def test_json_has_stable_shape(self) -> None:
        report = mod.CheckReport()
        report.renga_version = "0.18.0"
        report.renga_version_tuple = [0, 18, 0]
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
        self.assertIn("renga", doc)
        self.assertIn("mcp", doc)
        self.assertIn("version", doc["renga"])
        self.assertIn("tools_required", doc["mcp"])
        self.assertIn("tools_missing", doc["mcp"])


if __name__ == "__main__":
    unittest.main()
