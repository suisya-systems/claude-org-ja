"""Unit tests for tools/check_renga_compat.py (Issue #61).

Run with:
  py -3 -m unittest tools.test_check_renga_compat
  (from repo root, or add claude-org to PYTHONPATH)
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

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
    """Version comparison is history-agnostic; these keep the older triples
    on purpose so the comparator stays covered across the 0.x -> 2.x jump."""

    def test_equal(self) -> None:
        self.assertEqual(mod.cmp_version((0, 18, 0), (0, 18, 0)), 0)

    def test_older_minor(self) -> None:
        self.assertEqual(mod.cmp_version((0, 17, 9), (0, 18, 0)), -1)

    def test_newer_patch(self) -> None:
        self.assertEqual(mod.cmp_version((0, 18, 1), (0, 18, 0)), 1)

    def test_newer_major(self) -> None:
        self.assertEqual(mod.cmp_version((1, 0, 0), (0, 18, 0)), 1)


class MinVersionTests(unittest.TestCase):
    """The support floor, distinct from the feature-introduction history.

    0.16.0 / 0.17.0 / 0.18.0 are when structured cwd / set_pane_identity /
    spawn_claude_pane were *introduced*; that history is unchanged. The
    floor org supports is a separate proposition and is now 2.0.0.
    """

    def test_min_required_version_is_two_zero_zero(self) -> None:
        self.assertEqual(mod.MIN_REQUIRED_VERSION, (2, 0, 0))

    def test_two_zero_zero_parses(self) -> None:
        self.assertEqual(mod.parse_version("renga 2.0.0"), (2, 0, 0))

    def test_one_four_zero_is_below_the_floor(self) -> None:
        # Measured: the PATH-first mcp-peer on the dev box is 1.4.0, so the
        # preflight is EXPECTED to fail there until that half is upgraded.
        self.assertEqual(
            mod.cmp_version((1, 4, 0), mod.MIN_REQUIRED_VERSION), -1
        )

    def test_zero_eighteen_zero_is_below_the_floor(self) -> None:
        self.assertEqual(
            mod.cmp_version((0, 18, 0), mod.MIN_REQUIRED_VERSION), -1
        )

    def test_two_zero_zero_meets_the_floor(self) -> None:
        self.assertEqual(
            mod.cmp_version((2, 0, 0), mod.MIN_REQUIRED_VERSION), 0
        )


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

    def test_has_capability_probe_tool(self) -> None:
        self.assertIn("server_info", mod.REQUIRED_MCP_TOOLS)

    def test_required_tools_count_is_fifteen(self) -> None:
        # This asserts the size of the REQUIRED list only. It is NOT an
        # assertion about how many tools a renga returns: the check is a
        # subset test and extra tools are allowed by design (measured:
        # mcp-peer 1.4.0 returns 15 tools, 2.0.0 returns 16, both including
        # spawn_codex_pane, which org does not require).
        self.assertEqual(len(mod.REQUIRED_MCP_TOOLS), 15)

    def test_extra_tools_do_not_fail_the_subset_check(self) -> None:
        found = set(mod.REQUIRED_MCP_TOOLS) | {"spawn_codex_pane", "future_x"}
        missing = [t for t in mod.REQUIRED_MCP_TOOLS if t not in found]
        self.assertEqual(missing, [])

    def test_no_duplicates(self) -> None:
        self.assertEqual(
            len(mod.REQUIRED_MCP_TOOLS),
            len(set(mod.REQUIRED_MCP_TOOLS)),
        )


class RequiredCapabilitiesContract(unittest.TestCase):
    """3 required tokens + 1 observed-only, out of renga's advertised 4.

    Source of truth for the advertised set: renga src/ipc/mod.rs:123-128
    (SERVER_CAPABILITIES).
    """

    def test_required_are_the_three_org_actually_exercises(self) -> None:
        self.assertEqual(
            mod.REQUIRED_CAPABILITIES,
            [
                "caller_scope",
                "cross_tab_peers",
                "caller_scope_close_identity",
            ],
        )

    def test_spawn_tab_is_observed_not_required(self) -> None:
        # The harness MUST keep every orchestrator-spawned pane in one tab
        # (backend-interface-contract.md, §4.2 SINGLE-TAB MUST -- cited by
        # section anchor, not line number, because that file is edited in the
        # same commits as this one), and `spawn_tab` gates only a `spawn_*`
        # call carrying a `tab` selector (renga
        # docs/api-surface-v1.0.md:571-574). Org never sends one, so its
        # absence breaks nothing org does.
        self.assertNotIn("spawn_tab", mod.REQUIRED_CAPABILITIES)
        self.assertIn("spawn_tab", mod.OBSERVED_CAPABILITIES)

    def test_required_plus_observed_cover_rengas_advertised_set(self) -> None:
        # Nothing upstream advertises is silently dropped from the report:
        # every token is either gated on or reported.
        self.assertEqual(
            sorted(mod.REQUIRED_CAPABILITIES + mod.OBSERVED_CAPABILITIES),
            sorted([
                "caller_scope",
                "cross_tab_peers",
                "spawn_tab",
                "caller_scope_close_identity",
            ]),
        )

    def test_close_identity_is_a_token_of_its_own(self) -> None:
        # renga docs/api-surface-v1.0.md:576-582 - a #290-era server
        # advertises caller_scope but still closes panes in the visible tab.
        self.assertIn("caller_scope_close_identity", mod.REQUIRED_CAPABILITIES)
        self.assertIn("caller_scope", mod.REQUIRED_CAPABILITIES)

    def test_missing_spawn_tab_alone_is_not_a_failure(self) -> None:
        structured = json.loads(json.dumps(CONNECTED_STRUCTURED))
        without = [
            c for c in CONNECTED_STRUCTURED["effective_capabilities"]
            if c != "spawn_tab"
        ]
        structured["server"]["capabilities"] = list(without)
        structured["effective_capabilities"] = list(without)
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, structured)
        self.assertTrue(r.ok)
        self.assertEqual(r.capabilities_missing, [])
        self.assertEqual(r.capabilities_observed_missing, ["spawn_tab"])


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


# --- layer 3b fixtures -------------------------------------------------------
#
# All three payloads below are VERBATIM structuredContent captured from
# `renga mcp-peer` 2.0.0 on 2026-08-07 (initialize + tools/call server_info
# over stdio). No live daemon is needed to run these tests.
#
# On the literals inside them: `pid` 3779 and the `/run/user/1000/renga/...`
# endpoint are FROZEN TEXT from that capture, not addresses. Nothing here
# resolves them - no test signals the pid, stats the socket, or otherwise
# touches the machine - so these stay reproducible after that process is
# gone, which it is. They exist only to prove the parser reads the fields
# through unchanged.

CONNECTED_STRUCTURED = {
    "client": {
        "capabilities": [
            "caller_scope", "cross_tab_peers", "spawn_tab",
            "caller_scope_close_identity",
        ],
        "name": "renga-peers",
        "pane_id": 3,
        "version": "2.0.0",
    },
    "effective_capabilities": [
        "caller_scope", "cross_tab_peers", "spawn_tab",
        "caller_scope_close_identity",
    ],
    "reason": None,
    "server": {
        "capabilities": [
            "caller_scope", "cross_tab_peers", "spawn_tab",
            "caller_scope_close_identity",
        ],
        "endpoint": "/run/user/1000/renga/renga-3779.sock",
        "pid": 3779,
    },
    "status": "connected",
}

# `reason` carries an em-dash upstream; kept as an escape so this source file
# stays ASCII while still exercising the non-ASCII pass-through path.
DETACHED_STRUCTURED = {
    "client": {
        "capabilities": [
            "caller_scope", "cross_tab_peers", "spawn_tab",
            "caller_scope_close_identity",
        ],
        "name": "renga-peers",
        "pane_id": None,
        "version": "2.0.0",
    },
    "effective_capabilities": None,
    "reason": (
        "RENGA_PANE_ID not set — Claude Code was not launched by renga"
    ),
    "server": {"capabilities": None, "endpoint": None, "pid": None},
    "status": "detached",
}

UNREACHABLE_STRUCTURED = {
    "client": {
        "capabilities": [
            "caller_scope", "cross_tab_peers", "spawn_tab",
            "caller_scope_close_identity",
        ],
        "name": "renga-peers",
        "pane_id": 3,
        "version": "2.0.0",
    },
    "effective_capabilities": None,
    "reason": "connect to /run/user/1000/renga/renga-3779.sock",
    "server": {
        "capabilities": None,
        "endpoint": "/run/user/1000/renga/renga-3779.sock",
        "pid": None,
    },
    "status": "unreachable",
}


def _wrap(structured: dict, request_id: int = 2) -> str:
    """Render a structuredContent payload as an mcp-peer stdout line."""
    return json.dumps({
        "id": request_id, "jsonrpc": "2.0",
        "result": {
            "content": [{"type": "text", "text": "..."}],
            "isError": False,
            "structuredContent": structured,
        },
    }) + "\n"


class ParseServerInfoResponseTests(unittest.TestCase):
    """Pure parse path: no subprocess, no live daemon."""

    def test_extracts_structured_content_from_connected_result(self) -> None:
        kind, body = mod.parse_server_info_response(
            '{"id":0,"jsonrpc":"2.0","result":{"capabilities":{}}}\n'
            + _wrap(CONNECTED_STRUCTURED)
        )
        self.assertEqual(kind, "result")
        self.assertEqual(body["status"], "connected")

    def test_detects_unknown_tool_error_32601(self) -> None:
        # Literal payload measured from mcp-peer 1.4.0.
        payload = (
            '{"error":{"code":-32601,"message":"unknown tool: server_info"},'
            '"id":2,"jsonrpc":"2.0"}\n'
        )
        kind, body = mod.parse_server_info_response(payload)
        self.assertEqual(kind, "error")
        self.assertEqual(body["code"], -32601)
        self.assertIn("server_info", body["message"])

    def test_returns_absent_when_no_matching_id(self) -> None:
        kind, body = mod.parse_server_info_response(
            _wrap(CONNECTED_STRUCTURED, request_id=9)
        )
        self.assertEqual(kind, "absent")
        self.assertIsNone(body)

    def test_ignores_tools_list_line_when_seeking_server_info(self) -> None:
        payload = (
            '{"id":1,"jsonrpc":"2.0","result":{"tools":['
            '{"name":"server_info"}]}}\n'
            + _wrap(CONNECTED_STRUCTURED)
        )
        kind, body = mod.parse_server_info_response(payload)
        self.assertEqual(kind, "result")
        self.assertEqual(body["status"], "connected")

    def test_skips_malformed_lines(self) -> None:
        payload = "not json\n\n" + _wrap(CONNECTED_STRUCTURED)
        kind, _body = mod.parse_server_info_response(payload)
        self.assertEqual(kind, "result")

    def test_empty_input_is_absent(self) -> None:
        self.assertEqual(mod.parse_server_info_response(""), ("absent", None))


class CapabilityBranchTests(unittest.TestCase):
    """The five capability-probe branches, driven by measured fixtures."""

    def test_connected_with_all_four_tokens_passes(self) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, CONNECTED_STRUCTURED)
        self.assertEqual(r.capability_probe_status, "connected")
        self.assertTrue(r.ok)
        self.assertEqual(r.capabilities_missing, [])
        self.assertFalse(r.live_readiness_unverified)
        self.assertEqual(r.server_pid, 3779)
        self.assertEqual(r.client_version_reported, "2.0.0")

    def test_connected_missing_close_identity_fails(self) -> None:
        # A #290-era server: three earlier tokens advertised, close/identity
        # token absent.
        structured = json.loads(json.dumps(CONNECTED_STRUCTURED))
        three = ["caller_scope", "cross_tab_peers", "spawn_tab"]
        structured["server"]["capabilities"] = list(three)
        structured["effective_capabilities"] = list(three)
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, structured)
        self.assertEqual(r.capability_probe_status, "connected")
        self.assertFalse(r.ok)
        self.assertEqual(r.capabilities_missing,
                         ["caller_scope_close_identity"])
        joined = " ".join(r.failures)
        # The remedy must be "update the daemon to 2.0 and restart", not
        # "fall back to the non-advertised path".
        self.assertIn("2.0", joined)
        self.assertIn("restart", joined)
        self.assertIn("re-probe", joined)
        self.assertNotIn("fallback to legacy", joined)

    def test_connected_with_empty_server_capabilities_fails(self) -> None:
        # Pre-#288 server: `[]` means "asked, supports nothing" - a real
        # answer, and a failing one. Not to be confused with `null`.
        structured = json.loads(json.dumps(CONNECTED_STRUCTURED))
        structured["server"]["capabilities"] = []
        structured["effective_capabilities"] = []
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, structured)
        self.assertEqual(r.capability_probe_status, "connected")
        self.assertFalse(r.ok)
        self.assertEqual(r.capabilities_missing, mod.REQUIRED_CAPABILITIES)
        self.assertEqual(r.server_capabilities, [])
        self.assertIsNotNone(r.server_capabilities)

    def test_detached_is_not_failure_but_marks_readiness_unverified(
        self,
    ) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, DETACHED_STRUCTURED)
        self.assertEqual(r.capability_probe_status, "detached")
        self.assertTrue(r.ok)
        self.assertTrue(r.live_readiness_unverified)
        self.assertEqual(r.failures, [])
        self.assertTrue(r.warnings)
        # Unknown, not empty.
        self.assertIsNone(r.server_capabilities)
        self.assertIsNone(r.effective_capabilities)

    def test_unreachable_is_failure_and_caps_not_treated_as_empty(
        self,
    ) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, UNREACHABLE_STRUCTURED)
        self.assertEqual(r.capability_probe_status, "unreachable")
        self.assertFalse(r.ok)
        self.assertIsNone(r.server_capabilities)
        self.assertIsNone(r.effective_capabilities)
        self.assertEqual(r.capabilities_missing, [])
        self.assertIn("UNKNOWN, not empty", " ".join(r.failures))

    def test_null_and_empty_capabilities_reach_different_conclusions(
        self,
    ) -> None:
        empty = json.loads(json.dumps(CONNECTED_STRUCTURED))
        empty["server"]["capabilities"] = []
        empty["effective_capabilities"] = []
        r_empty = mod.CheckReport()
        mod.evaluate_capability_probe(r_empty, empty)

        r_null = mod.CheckReport()
        mod.evaluate_capability_probe(r_null, DETACHED_STRUCTURED)

        # `[]` -> a concrete missing-token list; `null` -> no conclusion.
        self.assertEqual(r_empty.capabilities_missing,
                         mod.REQUIRED_CAPABILITIES)
        self.assertEqual(r_null.capabilities_missing, [])
        self.assertFalse(r_empty.ok)
        self.assertTrue(r_null.ok)

    def test_null_effective_never_satisfies_required(self) -> None:
        for fixture in (DETACHED_STRUCTURED, UNREACHABLE_STRUCTURED):
            r = mod.CheckReport()
            mod.evaluate_capability_probe(r, fixture)
            self.assertIsNone(r.effective_capabilities)

    def test_biconditional_violation_is_reported_as_failure(self) -> None:
        # status says detached but capabilities are non-null: not the
        # contract's peer (renga docs/api-surface-v1.0.md:347-350).
        structured = json.loads(json.dumps(CONNECTED_STRUCTURED))
        structured["status"] = "detached"
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, structured)
        self.assertFalse(r.ok)
        self.assertIn("biconditional", " ".join(r.failures))

    def test_unknown_status_is_failure(self) -> None:
        structured = json.loads(json.dumps(CONNECTED_STRUCTURED))
        structured["status"] = "who-knows"
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, structured)
        self.assertFalse(r.ok)
        self.assertEqual(r.capability_probe_status, "call_error")


class BlockedSocketTests(unittest.TestCase):
    """--tolerate-blocked-socket: the sandbox false-FAIL escape hatch.

    Layer 3b connects to the renga server over a unix socket. A sandbox that
    denies that connect turns a healthy server into `unreachable`, so the
    identical command FAILs inside a sandbox and passes outside it - and org
    agents run Bash sandboxed by default. The flag downgrades exactly the
    case where the socket file is still on disk (present server, blocked
    connect) and nothing else.
    """

    @staticmethod
    def _with_endpoint(path) -> dict:
        structured = json.loads(json.dumps(UNREACHABLE_STRUCTURED))
        structured["server"]["endpoint"] = path
        return structured

    def _existing_socket_path(self) -> str:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".sock", delete=False)
        tmp.close()
        self.addCleanup(lambda: __import__("os").unlink(tmp.name))
        return tmp.name

    # -- classifier ----------------------------------------------------

    def test_classifier_reports_present_for_an_existing_path(self) -> None:
        path = self._existing_socket_path()
        self.assertEqual(
            mod.classify_unreachable_endpoint(path), "socket_present"
        )

    def test_classifier_reports_absent_for_a_missing_path(self) -> None:
        self.assertEqual(
            mod.classify_unreachable_endpoint(
                "/run/user/1000/renga/renga-does-not-exist-9999.sock"
            ),
            "socket_absent",
        )

    def test_classifier_reports_unknown_without_an_endpoint(self) -> None:
        for value in (None, ""):
            with self.subTest(value=value):
                self.assertEqual(
                    mod.classify_unreachable_endpoint(value), "unknown"
                )

    def test_classifier_reports_unknown_for_a_windows_named_pipe(self) -> None:
        # `\\.\pipe\renga-<pid>` is not a filesystem path an existence check
        # can speak to, so the classifier refuses to guess (fail-closed).
        self.assertEqual(
            mod.classify_unreachable_endpoint(r"\\.\pipe\renga-1234"),
            "unknown",
        )

    def test_classifier_reports_unknown_when_stat_is_denied(self) -> None:
        with mock.patch.object(
            mod.os.path, "exists", side_effect=PermissionError("denied")
        ):
            self.assertEqual(
                mod.classify_unreachable_endpoint("/some/socket.sock"),
                "unknown",
            )

    def test_classifier_states_are_the_declared_set(self) -> None:
        self.assertEqual(
            sorted(mod.UNREACHABLE_ENDPOINT_STATES),
            sorted(["socket_present", "socket_absent", "unknown"]),
        )

    # -- default (flag off) stays fail-closed --------------------------

    def test_present_socket_without_the_flag_still_fails(self) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(
            r, self._with_endpoint(self._existing_socket_path())
        )
        self.assertFalse(r.ok)
        self.assertEqual(mod.exit_code(r), 1)
        self.assertEqual(r.unreachable_endpoint_state, "socket_present")
        self.assertFalse(r.live_readiness_unverified)

    def test_the_failure_names_the_flag_and_the_sandbox(self) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(
            r, self._with_endpoint(self._existing_socket_path())
        )
        joined = " ".join(r.failures)
        self.assertIn("--tolerate-blocked-socket", joined)
        self.assertIn("sandbox", joined)

    # -- flag on: only the socket_present case is downgraded -----------

    def test_flag_downgrades_a_present_socket_to_unverified(self) -> None:
        path = self._existing_socket_path()
        r = mod.CheckReport()
        mod.evaluate_capability_probe(
            r, self._with_endpoint(path), tolerate_blocked_socket=True
        )
        self.assertTrue(r.ok, r.failures)
        self.assertTrue(r.live_readiness_unverified)
        self.assertEqual(mod.exit_code(r), 2)
        self.assertEqual(r.capability_probe_status, "unreachable")
        self.assertIn("UNVERIFIED", " ".join(r.warnings))
        # Never a green light: no capability conclusion was drawn.
        self.assertIsNone(r.effective_capabilities)
        self.assertEqual(r.capabilities_missing, [])

    def test_flag_does_not_downgrade_a_missing_socket(self) -> None:
        # An explicitly nonexistent path, not the shared fixture's: on a box
        # that really is running renga the fixture path exists, and this test
        # must assert the absent-socket branch deterministically.
        r = mod.CheckReport()
        mod.evaluate_capability_probe(
            r,
            self._with_endpoint(
                "/run/user/1000/renga/renga-does-not-exist-9999.sock"
            ),
            tolerate_blocked_socket=True,
        )
        self.assertFalse(r.ok)
        self.assertEqual(mod.exit_code(r), 1)
        self.assertEqual(r.unreachable_endpoint_state, "socket_absent")

    def test_flag_does_not_downgrade_an_unclassifiable_endpoint(self) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(
            r, self._with_endpoint(None), tolerate_blocked_socket=True
        )
        self.assertFalse(r.ok)
        self.assertEqual(mod.exit_code(r), 1)
        self.assertEqual(r.unreachable_endpoint_state, "unknown")

    def test_flag_does_not_touch_connected_or_detached(self) -> None:
        for fixture, expected_ok in (
            (CONNECTED_STRUCTURED, True), (DETACHED_STRUCTURED, True),
        ):
            with self.subTest(status=fixture["status"]):
                r = mod.CheckReport()
                mod.evaluate_capability_probe(
                    r, fixture, tolerate_blocked_socket=True
                )
                self.assertEqual(r.ok, expected_ok, r.failures)
                self.assertIsNone(r.unreachable_endpoint_state)

    # -- end to end through run_checks ---------------------------------

    def test_run_checks_threads_the_flag_through(self) -> None:
        path = self._existing_socket_path()
        env = FakeRengaEnv(server_info=self._with_endpoint(path))
        env.install(self)

        strict = mod.CheckReport()
        mod.run_checks(strict)
        self.assertFalse(strict.ok)
        self.assertEqual(mod.exit_code(strict), 1)

        tolerant = mod.CheckReport()
        mod.run_checks(tolerant, tolerate_blocked_socket=True)
        self.assertTrue(tolerant.ok, tolerant.failures)
        self.assertEqual(mod.exit_code(tolerant), 2)
        self.assertTrue(tolerant.tolerate_blocked_socket)

    def test_run_checks_records_the_flag_even_when_it_never_fires(
        self,
    ) -> None:
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report, tolerate_blocked_socket=True)
        self.assertTrue(report.tolerate_blocked_socket)
        self.assertIsNone(report.unreachable_endpoint_state)

    def test_require_live_still_fails_on_a_downgraded_unreachable(
        self,
    ) -> None:
        # The downgrade produces "unverified", and --require-live's whole job
        # is to reject "unverified". The two flags must not cancel out into a
        # vacuous pass.
        path = self._existing_socket_path()
        env = FakeRengaEnv(server_info=self._with_endpoint(path))
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(
            report, require_live=True, tolerate_blocked_socket=True
        )
        self.assertFalse(report.ok)
        self.assertEqual(mod.exit_code(report), 1)

    # -- reporting -----------------------------------------------------

    def test_emit_text_reads_warn_when_downgraded_and_fail_otherwise(
        self,
    ) -> None:
        import contextlib
        import io

        path = self._existing_socket_path()

        def render(**kwargs) -> str:
            r = mod.CheckReport()
            r.mcp_peer_binary = CARGO_RENGA
            r.mcp_peer_binary_source = "claude mcp list registration"
            r.mcp_tools_found = list(mod.REQUIRED_MCP_TOOLS)
            mod.evaluate_capability_probe(
                r, self._with_endpoint(path), **kwargs
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.emit_text(r)
            return buf.getvalue()

        strict = render()
        self.assertIn("[FAIL] renga capability probe: unreachable", strict)
        self.assertIn("endpoint socket_present", strict)
        self.assertIn("Result: FAIL", strict)

        tolerant = render(tolerate_blocked_socket=True)
        self.assertIn("[WARN] renga capability probe: unreachable", tolerant)
        self.assertIn("UNVERIFIED", tolerant)

    def test_json_carries_the_new_diagnostic_keys(self) -> None:
        import contextlib
        import io

        r = mod.CheckReport()
        mod.evaluate_capability_probe(
            r, self._with_endpoint(self._existing_socket_path()),
            tolerate_blocked_socket=True,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.emit_json(r)
        doc = json.loads(buf.getvalue())
        self.assertEqual(
            doc["capabilities"]["unreachable_endpoint_state"],
            "socket_present",
        )
        self.assertTrue(doc["capabilities"]["tolerate_blocked_socket"])
        self.assertTrue(doc["capabilities"]["live_readiness_unverified"])

    def test_help_documents_the_sandbox_false_fail(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                mod.main(["--help"])
        text = buf.getvalue()
        self.assertIn("--tolerate-blocked-socket", text)
        self.assertIn("sandbox", text)
        # argparse help is printed to a possibly-cp932 console: ASCII only.
        text.encode("ascii")


class ToolAbsentTests(unittest.TestCase):
    def test_server_info_absent_from_tools_list_fails(self) -> None:
        r = mod.CheckReport()
        # Everything except the capability probe tool.
        r.mcp_tools_found = [
            t for t in mod.REQUIRED_MCP_TOOLS if t != "server_info"
        ]
        mod.check_capability_surface(r)
        self.assertEqual(r.capability_probe_status, "tool_absent")
        self.assertFalse(r.ok)
        joined = " ".join(r.failures)
        self.assertIn("2.0.0", joined)
        self.assertIn("server_info", joined)

    def test_empty_tool_surface_draws_no_capability_conclusion(self) -> None:
        # The layer-3 probe failed outright (renga missing / unreadable
        # tools/list). Absence of an observation is not an observation of
        # absence, so this must not be reported as tool_absent.
        r = mod.CheckReport()
        r.mcp_tools_found = []
        mod.check_capability_surface(r)
        self.assertEqual(r.capability_probe_status, "skipped")
        self.assertTrue(r.ok)
        self.assertEqual(r.failures, [])

    def test_tool_absent_does_not_fall_back_to_string_probe(self) -> None:
        r = mod.CheckReport()
        r.mcp_tools_found = [
            t for t in mod.REQUIRED_MCP_TOOLS if t != "server_info"
        ]
        mod.check_capability_surface(r)
        self.assertNotIn("server_too_old", " ".join(r.failures))


class ServerTooOldNotUsedForInference(unittest.TestCase):
    """`server_too_old` is a TOCTOU net at real call sites, never a probe
    input. Prose cannot enforce this; source inspection can."""

    def test_absent_from_capability_logic(self) -> None:
        for fn in (mod.evaluate_capability_probe,
                   mod.check_capability_surface,
                   mod.parse_server_info_response):
            src = inspect.getsource(fn)
            self.assertNotIn(
                "server_too_old", src,
                f"{fn.__name__} must not infer capabilities from the "
                "server_too_old failure string",
            )


class TwoHalvesMessagingTests(unittest.TestCase):
    """The server half and the mcp-peer half must be checked separately."""

    def test_note_names_both_halves(self) -> None:
        note = mod.TWO_HALVES_NOTE
        self.assertIn("SERVER", note)
        self.assertIn("MCP-PEER", note)
        self.assertIn("renga --version", note)
        # The mcp-peer's self-reported version is not the server's version.
        self.assertIn("client.version", note)
        self.assertIn("NOT the server's", note)

    def test_capability_failure_points_at_the_two_halves_note(self) -> None:
        r = mod.CheckReport()
        r.mcp_tools_found = [
            t for t in mod.REQUIRED_MCP_TOOLS if t != "server_info"
        ]
        mod.check_capability_surface(r)
        self.assertIn("Both renga halves", " ".join(r.failures))
        # The long-form note is emitted exactly once, as a warning.
        self.assertEqual(r.warnings.count(mod.TWO_HALVES_NOTE), 1)
        self.assertNotIn(mod.TWO_HALVES_NOTE, " ".join(r.failures))


# --- binary resolution -------------------------------------------------------
#
# Measured on a dev box 2026-08-07 (paths below are neutral stand-ins for the
# two real install locations; every comparison under test is plain string
# equality, so the literal values carry no behaviour):
#   claude mcp list -> renga-peers: <cargo-install-dir>/renga mcp-peer
#   <cargo-install-dir>/renga --version -> renga 2.0.0
#   command -v renga -> <volta-shim-dir>/renga
#   renga --version  -> renga 1.4.0
# So probing the PATH-first binary measures a program org never launches and
# reports a false FAIL. These fixtures freeze that exact shape.

CARGO_RENGA = "/opt/cargo/bin/renga"
VOLTA_RENGA = "/opt/volta/bin/renga"

MCP_LIST_OUTPUT = (
    "Checking MCP server health...\n"
    "\n"
    "plugin:slack:slack: https://mcp.slack.com/mcp (HTTP) - "
    "! Needs authentication\n"
    f"renga-peers: {CARGO_RENGA} mcp-peer - \u2714 Connected\n"
)


class ParseMcpRegistrationLineTests(unittest.TestCase):
    def test_extracts_absolute_path(self) -> None:
        line = f"renga-peers: {CARGO_RENGA} mcp-peer - \u2714 Connected"
        self.assertEqual(mod.parse_mcp_registration_line(line), CARGO_RENGA)

    def test_extracts_bare_command(self) -> None:
        # A registration installed without an absolute path still yields the
        # argv[0] the harness would run.
        line = "renga-peers: renga mcp-peer - \u2714 Connected"
        self.assertEqual(mod.parse_mcp_registration_line(line), "renga")

    def test_survives_a_path_containing_spaces(self) -> None:
        exe = "C:\\Program Files\\renga\\renga.exe"
        line = f"renga-peers: {exe} mcp-peer - \u2714 Connected"
        self.assertEqual(mod.parse_mcp_registration_line(line), exe)

    def test_is_not_confused_by_colons_in_other_server_names(self) -> None:
        # `plugin:slack:slack:` has three colons before its command, so
        # anchoring on the first colon would mis-parse neighbouring rows.
        line = (
            "plugin:slack:slack: https://mcp.slack.com/mcp (HTTP) - "
            "! Needs authentication"
        )
        self.assertIsNone(mod.parse_mcp_registration_line(line))

    def test_http_registration_yields_no_executable(self) -> None:
        line = "renga-peers: https://example.invalid/mcp (HTTP) - Connected"
        self.assertIsNone(mod.parse_mcp_registration_line(line))

    def test_handles_a_row_without_a_status_suffix(self) -> None:
        line = f"renga-peers: {CARGO_RENGA} mcp-peer"
        self.assertEqual(mod.parse_mcp_registration_line(line), CARGO_RENGA)


class FakeRengaEnv:
    """Deterministic stand-in for every subprocess this tool shells out to.

    Replaces `run_cmd` and `find_renga_on_path`, so no test needs a renga
    binary, a claude CLI, or a live daemon.
    """

    def __init__(
        self,
        *,
        mcp_list: str = MCP_LIST_OUTPUT,
        mcp_list_rc: int = 0,
        mcp_list_stderr: str = "",
        versions: dict | None = None,
        path_binaries: list | None = None,
        tools: list | None = None,
        server_info: dict | None = None,
    ) -> None:
        self.mcp_list = mcp_list
        # Non-zero models a `claude mcp list` that could not answer: 127 =
        # CLI missing, 124 = timed out (run_cmd's TimeoutExpired mapping).
        self.mcp_list_rc = mcp_list_rc
        self.mcp_list_stderr = mcp_list_stderr
        self.mcp_list_timeouts: list[float] = []
        self.versions = versions if versions is not None else {
            CARGO_RENGA: "2.0.0", VOLTA_RENGA: "1.4.0",
        }
        self.path_binaries = (
            path_binaries if path_binaries is not None
            else [VOLTA_RENGA, CARGO_RENGA]
        )
        self.tools = (
            tools if tools is not None else list(mod.REQUIRED_MCP_TOOLS)
        )
        self.server_info = (
            server_info if server_info is not None else CONNECTED_STRUCTURED
        )
        self.calls: list[list[str]] = []

    # -- fakes ---------------------------------------------------------
    def run_cmd(self, args, stdin=None, timeout=15.0):
        self.calls.append(list(args))
        if args[:3] == ["claude", "mcp", "list"]:
            self.mcp_list_timeouts.append(timeout)
            if self.mcp_list_rc != 0:
                return self.mcp_list_rc, "", self.mcp_list_stderr
            return 0, self.mcp_list, ""
        if len(args) == 2 and args[1] == "--version":
            version = self.versions.get(args[0])
            if version is None:
                return 127, "", f"{args[0]}: not found on PATH"
            return 0, f"renga {version}\n", ""
        if len(args) == 2 and args[1] == "mcp-peer":
            if self.versions.get(args[0]) is None:
                return 127, "", f"{args[0]}: not found on PATH"
            if stdin and "tools/list" in stdin:
                return 0, json.dumps({
                    "id": 1, "jsonrpc": "2.0",
                    "result": {"tools": [{"name": t} for t in self.tools]},
                }) + "\n", ""
            if stdin and "server_info" in stdin:
                return 0, _wrap(self.server_info), ""
        return 1, "", f"unexpected call: {args!r}"

    def find_renga_on_path(self):
        return list(self.path_binaries)

    # -- helpers -------------------------------------------------------
    def install(self, testcase: unittest.TestCase) -> None:
        for name, fake in (("run_cmd", self.run_cmd),
                           ("find_renga_on_path", self.find_renga_on_path)):
            patcher = mock.patch.object(mod, name, fake)
            patcher.start()
            testcase.addCleanup(patcher.stop)

    def mcp_peer_invocations(self) -> list[str]:
        return [c[0] for c in self.calls if c[-1] == "mcp-peer"]


class ProbeBinaryResolutionTests(unittest.TestCase):
    """Which executable the probes run against - the whole point of layer 2
    running first."""

    def test_probes_the_registered_binary_not_the_path_first_one(
        self,
    ) -> None:
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)

        self.assertEqual(report.mcp_peer_binary, CARGO_RENGA)
        self.assertEqual(
            report.mcp_peer_binary_source, "claude mcp list registration"
        )
        # Every stdio probe went to the registered binary.
        self.assertEqual(
            set(env.mcp_peer_invocations()), {CARGO_RENGA}
        )
        self.assertNotIn(VOLTA_RENGA, env.mcp_peer_invocations())

    def test_registered_binary_supplies_the_reported_version(self) -> None:
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        # 2.0.0 (the registered cargo build), NOT 1.4.0 (PATH-first volta).
        self.assertEqual(report.renga_version, "2.0.0")
        self.assertTrue(report.version_check_ok)

    def test_old_path_binary_no_longer_produces_a_false_fail(self) -> None:
        # Before the fix this exact environment failed the preflight on the
        # 1.4.0 volta shim while org was really running the 2.0.0 cargo
        # build.
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertTrue(report.ok, report.failures)
        self.assertEqual(mod.exit_code(report), 0)

    def test_version_skew_between_registered_and_path_is_reported(
        self,
    ) -> None:
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertTrue(report.binary_version_skew)
        joined = " ".join(report.warnings)
        self.assertIn("VERSION SKEW", joined)
        self.assertIn(CARGO_RENGA, joined)
        self.assertIn(VOLTA_RENGA, joined)
        self.assertIn("2.0.0", joined)
        self.assertIn("1.4.0", joined)
        self.assertEqual(report.path_first_renga, VOLTA_RENGA)
        self.assertEqual(report.path_first_renga_version, "1.4.0")

    def test_no_skew_warning_when_versions_agree(self) -> None:
        env = FakeRengaEnv(versions={
            CARGO_RENGA: "2.0.0", VOLTA_RENGA: "2.0.0",
        })
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertFalse(report.binary_version_skew)
        self.assertNotIn("VERSION SKEW", " ".join(report.warnings))

    def test_falls_back_to_path_and_says_so_when_unregistered(self) -> None:
        env = FakeRengaEnv(
            mcp_list="Checking MCP server health...\n",
            versions={
                CARGO_RENGA: "2.0.0", VOLTA_RENGA: "1.4.0", "renga": "1.4.0",
            },
        )
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertEqual(report.mcp_peer_binary, "renga")
        self.assertEqual(report.mcp_peer_binary_source, "PATH fallback")
        self.assertIn("PATH-first", " ".join(report.warnings))
        # And the fallback is disclosed rather than silently taken.
        self.assertFalse(report.ok)
        self.assertIn("not registered", " ".join(report.failures))

    def test_missing_registered_binary_names_it_in_the_failure(self) -> None:
        env = FakeRengaEnv(versions={VOLTA_RENGA: "1.4.0"})
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertFalse(report.ok)
        self.assertIn(CARGO_RENGA, " ".join(report.failures))

    def test_json_records_which_binary_was_probed(self) -> None:
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        doc = JsonShapeTests._render(report)
        self.assertEqual(doc["renga"]["probed_binary"], CARGO_RENGA)
        self.assertEqual(doc["renga"]["path_first"], VOLTA_RENGA)
        self.assertTrue(doc["renga"]["binary_version_skew"])


class UnresolvedRegistrationTests(unittest.TestCase):
    """`claude mcp list` did not answer -> UNDETERMINED, never a hard FAIL.

    Regression: the tool used to fall back to the PATH-first binary and then
    report its verdicts as confirmed failures. On the environment this whole
    design exists for - an old PATH shim shadowing the newer registered
    build - that produced exactly the false FAIL the module docstring
    describes, and it did so on the one code path where the corroborating
    VERSION SKEW comparison is unavailable too (there is no registered
    version to compare against).
    """

    # A PATH inventory whose first entry is BELOW the floor, i.e. the shape
    # that used to be reported as a confirmed version failure.
    OLD_PATH_FIRST = {CARGO_RENGA: "2.0.0", VOLTA_RENGA: "1.4.0",
                      "renga": "1.4.0"}

    def _run(self, rc: int, stderr: str = "", **kwargs):
        env = FakeRengaEnv(
            mcp_list_rc=rc, mcp_list_stderr=stderr,
            versions=dict(self.OLD_PATH_FIRST), **kwargs
        )
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        return env, report

    def test_timeout_is_classified_as_undetermined(self) -> None:
        _env, report = self._run(124, "claude: timed out after 45.0s")
        self.assertIs(report.mcp_registration_resolved, False)
        # NOT `registered: false` - the question was never put.
        self.assertIsNone(report.mcp_registered)
        self.assertIn("UNDETERMINED", " ".join(report.warnings))

    def test_missing_claude_cli_is_classified_as_undetermined(self) -> None:
        _env, report = self._run(127)
        self.assertIs(report.mcp_registration_resolved, False)
        self.assertIsNone(report.mcp_registered)

    def test_nonzero_exit_is_classified_as_undetermined(self) -> None:
        _env, report = self._run(1, "boom")
        self.assertIs(report.mcp_registration_resolved, False)

    def test_unresolved_registration_does_not_produce_a_hard_fail(
        self,
    ) -> None:
        # The PATH-first binary is 1.4.0, below the floor. Before the fix
        # that was a confirmed FAIL (exit 1) about a binary nobody
        # established org would ever launch.
        _env, report = self._run(124)
        self.assertTrue(report.ok, report.failures)
        self.assertEqual(report.failures, [])

    def test_unresolved_registration_is_not_fail_open(self) -> None:
        # "Undetermined" must not read as success to a caller that treats
        # exit 0 as a green light.
        _env, report = self._run(124)
        self.assertTrue(report.probe_target_unverified)
        self.assertEqual(mod.exit_code(report), 2)

    def test_downgraded_verdicts_are_kept_as_warnings(self) -> None:
        # Downgrading must not mean discarding: the observation still has to
        # be readable, just not as a verdict.
        _env, report = self._run(124)
        joined = " ".join(report.warnings)
        self.assertIn("UNVERIFIED (probe target undetermined", joined)
        self.assertIn("1.4.0", joined)

    def test_path_fallback_still_reports_skew_across_candidates(self) -> None:
        # check_binary_skew compares registered-vs-PATH and so has nothing to
        # say here; the fallback path must find the disagreement itself.
        _env, report = self._run(124)
        self.assertTrue(report.binary_version_skew)
        joined = " ".join(report.warnings)
        self.assertIn("VERSION SKEW", joined)
        self.assertIn(CARGO_RENGA, joined)
        self.assertIn(VOLTA_RENGA, joined)
        self.assertIn("2.0.0", joined)
        self.assertIn("1.4.0", joined)

    def test_no_skew_warning_when_path_candidates_agree(self) -> None:
        env = FakeRengaEnv(
            mcp_list_rc=124,
            versions={CARGO_RENGA: "2.0.0", VOLTA_RENGA: "2.0.0",
                      "renga": "2.0.0"},
        )
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertNotIn("VERSION SKEW", " ".join(report.warnings))

    def test_mcp_list_gets_the_raised_timeout(self) -> None:
        env, _report = self._run(0)
        self.assertEqual(env.mcp_list_timeouts, [mod.MCP_LIST_TIMEOUT])

    def test_timeout_ceiling_exceeds_the_measured_runtime(self) -> None:
        # Measured 3.15-3.66s on this machine with a remote MCP server
        # registered; the ceiling has to leave room for a slower link.
        self.assertGreater(mod.MCP_LIST_TIMEOUT, 15.0)

    def test_json_distinguishes_undetermined_from_unregistered(self) -> None:
        _env, report = self._run(124)
        doc = JsonShapeTests._render(report)
        self.assertIs(doc["mcp"]["registration_resolved"], False)
        self.assertIsNone(doc["mcp"]["registered"])
        self.assertFalse(doc["renga"]["probe_target_confirmed"])
        self.assertTrue(doc["renga"]["probe_target_unverified"])

    def test_determinate_absence_is_still_a_hard_fail(self) -> None:
        # The downgrade is scoped to layers that depend on the probe target.
        # Layer 2's own verdict - `claude mcp list` answered and named no
        # renga-peers row - is determinate and must stay exit 1.
        env = FakeRengaEnv(
            mcp_list="Checking MCP server health...\n",
            versions=dict(self.OLD_PATH_FIRST),
        )
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertIs(report.mcp_registration_resolved, True)
        self.assertFalse(report.ok)
        self.assertEqual(mod.exit_code(report), 1)
        self.assertIn("not registered", " ".join(report.failures))

    def test_resolved_registration_leaves_verdicts_confirmed(self) -> None:
        # The healthy path is untouched: a resolved registration means the
        # probe target is confirmed and nothing is downgraded.
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report)
        self.assertTrue(report.probe_target_confirmed)
        self.assertFalse(report.probe_target_unverified)
        self.assertEqual(mod.exit_code(report), 0)

    def test_text_output_marks_the_probe_target_unverified(self) -> None:
        _env, report = self._run(124)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod.emit_text(report)
        text = out.getvalue()
        self.assertIn("probe target UNVERIFIED", text)
        self.assertIn("UNDETERMINED", text)
        # And the run is not advertised as a clean pass.
        self.assertNotIn("Result: OK\n", text)
        self.assertIn("UNVERIFIED)", text)


class CapabilityIndependenceTests(unittest.TestCase):
    """Capability tokens must not be reasoned about through release order.

    The contract forbids it explicitly (backend-interface-contract.md,
    T-§cap, bullet "Independence": independence "is not a consequence of
    release timing, and MUST NOT be reasoned about through it"), and no
    upstream text states an implication in the direction this module would
    have needed. The justification for treating `spawn_tab` as observe-only
    is operational - org never sends a `tab` selector - and prose alone
    cannot keep the ordering argument from creeping back, so guard the
    source.
    """

    def test_spawn_tab_rationale_makes_no_ordering_argument(self) -> None:
        src = Path(mod.__file__).read_text(encoding="utf-8")
        start = src.index("# `spawn_tab` gates exactly one thing upstream")
        rationale = src[start:src.index("OBSERVED_CAPABILITIES = [", start)]
        for banned in ("proxy", "#290", "#296", "later", "LATER",
                       "pins the server", "at least as tightly"):
            self.assertNotIn(
                banned, rationale,
                "the spawn_tab rationale must not argue from issue order or "
                f"use one token as a proxy for another (found {banned!r})",
            )

    def test_required_capabilities_are_each_gated_on_their_own_token(
        self,
    ) -> None:
        # A server advertising every token EXCEPT close/identity must fail:
        # no other token may stand in for it.
        structured = json.loads(json.dumps(CONNECTED_STRUCTURED))
        without = [
            c for c in CONNECTED_STRUCTURED["effective_capabilities"]
            if c != "caller_scope_close_identity"
        ]
        structured["server"]["capabilities"] = list(without)
        structured["effective_capabilities"] = list(without)
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, structured)
        self.assertFalse(r.ok)
        self.assertEqual(
            r.capabilities_missing, ["caller_scope_close_identity"]
        )


class RequireLiveGateTests(unittest.TestCase):
    """--require-live must never pass vacuously.

    Regression: combined with a skip flag it used to exit 0 / `Result: OK`
    without running any live check, because the gate only fired on
    `live_readiness_unverified`, which the skip path never set.
    """

    def _run(self, **kwargs) -> "mod.CheckReport":
        env = FakeRengaEnv()
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report, **kwargs)
        return report

    def test_require_live_with_skip_capability_probe_fails(self) -> None:
        report = self._run(skip_capability_probe=True, require_live=True)
        self.assertFalse(report.ok)
        self.assertEqual(mod.exit_code(report), 1)
        self.assertIn("--require-live", " ".join(report.failures))
        self.assertIn("--skip-capability-probe", " ".join(report.failures))

    def test_require_live_with_skip_mcp_probe_fails(self) -> None:
        report = self._run(skip_mcp_probe=True, require_live=True)
        self.assertFalse(report.ok)
        self.assertEqual(mod.exit_code(report), 1)
        self.assertIn("--skip-mcp-probe", " ".join(report.failures))

    def test_require_live_with_both_skips_fails(self) -> None:
        report = self._run(
            skip_mcp_probe=True, skip_capability_probe=True,
            require_live=True,
        )
        self.assertFalse(report.ok)
        self.assertEqual(mod.exit_code(report), 1)

    def test_require_live_with_detached_still_fails(self) -> None:
        env = FakeRengaEnv(server_info=DETACHED_STRUCTURED)
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report, require_live=True)
        self.assertFalse(report.ok)
        self.assertEqual(mod.exit_code(report), 1)

    def test_require_live_passes_when_the_probe_really_connected(
        self,
    ) -> None:
        report = self._run(require_live=True)
        self.assertTrue(report.ok, report.failures)
        self.assertEqual(mod.exit_code(report), 0)

    def test_require_live_with_unresolved_probe_target_fails(self) -> None:
        # Unverified is exit 2 normally, but --require-live is the strict
        # reading and must reject it - including this new occupant.
        env = FakeRengaEnv(mcp_list_rc=124)
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report, require_live=True)
        self.assertFalse(report.ok)
        self.assertEqual(mod.exit_code(report), 1)

    # --- remediation text must match what actually went wrong -------------
    #
    # Regression: the message blamed --skip-* unconditionally, so a detached
    # or unreachable run - neither of which involves a skip flag - was told to
    # drop flags it never passed.

    def _remediation(self, report: "mod.CheckReport") -> str:
        return " ".join(report.failures)

    def test_skip_flag_run_is_told_about_the_skip_flags(self) -> None:
        text = self._remediation(
            self._run(skip_capability_probe=True, require_live=True)
        )
        self.assertIn("--skip-capability-probe", text)
        self.assertIn("--skip-mcp-probe", text)

    def test_detached_run_is_not_blamed_on_skip_flags(self) -> None:
        env = FakeRengaEnv(server_info=DETACHED_STRUCTURED)
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report, require_live=True)
        text = self._remediation(report)
        self.assertNotIn("--skip-capability-probe", text)
        self.assertNotIn("--skip-mcp-probe", text)
        self.assertIn("detached", text)
        self.assertIn("renga pane", text)

    def test_unreachable_run_gets_socket_advice(self) -> None:
        env = FakeRengaEnv(server_info=UNREACHABLE_STRUCTURED)
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report, require_live=True, tolerate_blocked_socket=True)
        text = self._remediation(report)
        self.assertNotIn("--skip-capability-probe", text)
        self.assertIn("unreachable", text)
        self.assertIn(report.server_endpoint or "", text)

    def test_unresolved_target_run_points_at_the_registration(self) -> None:
        env = FakeRengaEnv(mcp_list_rc=124)
        env.install(self)
        report = mod.CheckReport()
        mod.run_checks(report, require_live=True)
        text = self._remediation(report)
        self.assertNotIn("--skip-capability-probe", text)
        self.assertIn("claude mcp list", text)
        self.assertIn("renga mcp install", text)

    def test_skips_without_require_live_keep_exiting_zero(self) -> None:
        # The fix is scoped to --require-live: static-only CI callers that
        # pass a skip flag on its own are unaffected.
        for kwargs in ({"skip_capability_probe": True},
                       {"skip_mcp_probe": True}):
            with self.subTest(**kwargs):
                report = self._run(**kwargs)
                self.assertTrue(report.ok, report.failures)
                self.assertEqual(mod.exit_code(report), 0)


class ExitCodeTests(unittest.TestCase):
    def test_clean_connected_report_exits_zero(self) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, CONNECTED_STRUCTURED)
        self.assertEqual(mod.exit_code(r), 0)

    def test_failed_report_exits_one(self) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, UNREACHABLE_STRUCTURED)
        self.assertEqual(mod.exit_code(r), 1)

    def test_detached_report_exits_two(self) -> None:
        r = mod.CheckReport()
        mod.evaluate_capability_probe(r, DETACHED_STRUCTURED)
        self.assertEqual(mod.exit_code(r), 2)

    def test_failure_wins_over_unverified(self) -> None:
        r = mod.CheckReport()
        r.live_readiness_unverified = True
        r.ok = False
        self.assertEqual(mod.exit_code(r), 1)


class JsonShapeTests(unittest.TestCase):
    """The JSON output is a machine contract for Dispatcher/Secretary."""

    @staticmethod
    def _render(report: "mod.CheckReport") -> dict:
        import io
        buf = io.StringIO()
        saved = sys.stdout
        sys.stdout = buf
        try:
            mod.emit_json(report)
        finally:
            sys.stdout = saved
        return json.loads(buf.getvalue())

    def _connected_report(self) -> "mod.CheckReport":
        report = mod.CheckReport()
        report.renga_version = "2.0.0"
        report.renga_version_tuple = [2, 0, 0]
        report.version_check_ok = True
        report.mcp_registered = True
        report.mcp_peer_binary = CARGO_RENGA
        report.mcp_peer_binary_source = "claude mcp list registration"
        report.mcp_tools_found = list(mod.REQUIRED_MCP_TOOLS)
        mod.evaluate_capability_probe(report, CONNECTED_STRUCTURED)
        return report

    def test_json_has_stable_shape(self) -> None:
        doc = self._render(self._connected_report())
        self.assertIn("ok", doc)
        self.assertIn("renga", doc)
        self.assertIn("mcp", doc)
        self.assertIn("version", doc["renga"])
        self.assertIn("tools_required", doc["mcp"])
        self.assertIn("tools_missing", doc["mcp"])

    def test_json_preserves_pre_existing_keys(self) -> None:
        # Guards the additive-only promise for existing consumers.
        doc = self._render(self._connected_report())
        for key in ("ok", "renga", "mcp", "failures", "recommendations"):
            self.assertIn(key, doc)
        for key in ("version", "version_tuple", "min_required", "path"):
            self.assertIn(key, doc["renga"])
        for key in ("registered", "registration_line", "tools_found",
                    "tools_missing", "tools_required"):
            self.assertIn(key, doc["mcp"])

    def test_json_has_capabilities_block(self) -> None:
        doc = self._render(self._connected_report())
        caps = doc["capabilities"]
        self.assertEqual(caps["probe_status"], "connected")
        self.assertEqual(caps["required"], mod.REQUIRED_CAPABILITIES)
        self.assertEqual(caps["missing"], [])
        self.assertEqual(caps["server_pid"], 3779)
        self.assertFalse(caps["live_readiness_unverified"])

    def test_json_distinguishes_null_from_empty_capabilities(self) -> None:
        detached = mod.CheckReport()
        mod.evaluate_capability_probe(detached, DETACHED_STRUCTURED)
        doc_null = self._render(detached)
        self.assertIsNone(doc_null["capabilities"]["server"])
        self.assertIsNone(doc_null["capabilities"]["effective"])

        empty_structured = json.loads(json.dumps(CONNECTED_STRUCTURED))
        empty_structured["server"]["capabilities"] = []
        empty_structured["effective_capabilities"] = []
        empty = mod.CheckReport()
        mod.evaluate_capability_probe(empty, empty_structured)
        doc_empty = self._render(empty)
        self.assertEqual(doc_empty["capabilities"]["server"], [])
        self.assertEqual(doc_empty["capabilities"]["effective"], [])

    def test_json_reports_client_version_as_mcp_peer_version(self) -> None:
        doc = self._render(self._connected_report())
        self.assertEqual(doc["renga"]["client_version_reported"], "2.0.0")
        self.assertIn("path_candidates", doc["renga"])


class AsciiOutputTests(unittest.TestCase):
    """Windows guard: our own console literals must survive cp932."""

    def test_argparse_help_is_ascii(self) -> None:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                mod.main(["--help"])
        text = buf.getvalue()
        self.assertTrue(text)
        text.encode("cp932")  # raises UnicodeEncodeError on em-dash etc.
        text.encode("ascii")

    def test_module_literals_are_ascii(self) -> None:
        self.assertEqual(
            mod.TWO_HALVES_NOTE, mod.TWO_HALVES_NOTE.encode(
                "ascii", "replace").decode("ascii")
        )

    def test_emit_text_is_ascii_except_echoed_upstream_reason(self) -> None:
        import contextlib
        import io

        report = mod.CheckReport()
        report.renga_version = "2.0.0"
        report.version_check_ok = True
        report.mcp_registered = True
        report.mcp_peer_binary = CARGO_RENGA
        report.mcp_peer_binary_source = "claude mcp list registration"
        report.mcp_tools_found = list(mod.REQUIRED_MCP_TOOLS)
        mod.evaluate_capability_probe(report, DETACHED_STRUCTURED)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.emit_text(report)
        text = buf.getvalue()
        # The upstream `reason` is echoed verbatim and carries an em-dash;
        # everything we author ourselves must be ASCII.
        upstream_reason = DETACHED_STRUCTURED["reason"]
        self.assertIn(upstream_reason, text)
        ours = text.replace(upstream_reason, "")
        ours.encode("ascii")


if __name__ == "__main__":
    unittest.main()
