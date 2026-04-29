"""renga compatibility preflight for claude-org (Issue #61).

Layered checks:
  1. renga binary version (static)
  2. `renga-peers` MCP registration in `claude mcp list`
  3. MCP tool surface via `renga mcp-peer` stdio (no live session needed)
  4. Optional live smoke (inside a renga --layout ops session) — MCP tools
     run by Claude; this script only *documents* them (does not shell in)
  5. Optional `--e2e` — spawn/close a throwaway pane to verify lifecycle

Usage:
  py -3 tools/check_renga_compat.py
  py -3 tools/check_renga_compat.py --json
  py -3 tools/check_renga_compat.py --e2e      # (reserved, not implemented)

Exit codes:
  0 — all required checks pass
  1 — any required check failed
  2 — required checks pass, optional/future recommendations present
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# claude-org' renga contract.
# When this list grows, bump MIN_REQUIRED_VERSION accordingly.
MIN_REQUIRED_VERSION = (0, 18, 0)

# Required `renga-peers` MCP tools. Source of truth:
# `printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | renga mcp-peer`
# on renga 0.18.0 returns exactly these 14 tools. The in-repo docs
# (README.md, docs/verification.md, docs/overview-technical.md) are updated
# to this count by PR #62 (Issue #58). Until that merges, the docs may
# still show an older count; this list is the authoritative one.
REQUIRED_MCP_TOOLS = [
    # peer comms
    "list_peers",
    "send_message",
    "set_summary",
    "check_messages",
    # pane listing / lifecycle
    "list_panes",
    "poll_events",
    # pane control
    "spawn_pane",
    "spawn_claude_pane",
    "close_pane",
    "focus_pane",
    "new_tab",
    "set_pane_identity",
    # PTY / screen
    "inspect_pane",
    "send_keys",
]


@dataclass
class CheckReport:
    ok: bool = True
    renga_version: Optional[str] = None
    renga_version_tuple: Optional[list[int]] = None
    renga_min_required: str = ".".join(str(x) for x in MIN_REQUIRED_VERSION)
    renga_path: Optional[str] = None
    mcp_registered: Optional[bool] = None
    mcp_registration_line: Optional[str] = None
    mcp_tools_found: list[str] = field(default_factory=list)
    mcp_tools_missing: list[str] = field(default_factory=list)
    mcp_tools_probe_skipped: bool = False
    failures: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def parse_version(s: str) -> Optional[tuple[int, int, int]]:
    """Parse 'renga 0.18.0' or '0.18.0' into (0, 18, 0).

    Returns None if no semver-looking triple is present.
    """
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def cmp_version(
    got: tuple[int, int, int], want: tuple[int, int, int]
) -> int:
    """Return -1 if got<want, 0 if equal, 1 if got>want."""
    return (got > want) - (got < want)


def run_cmd(args: list[str], stdin: Optional[str] = None, timeout: float = 15.0
            ) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr).

    Swallows FileNotFoundError as returncode=127 (POSIX convention) so the
    caller can distinguish 'binary missing' from 'binary ran and failed'.
    """
    try:
        proc = subprocess.run(
            args,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", f"{args[0]}: not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"{args[0]}: timed out after {timeout}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# Layer 1 ---------------------------------------------------------------------


def check_renga_version(report: CheckReport) -> None:
    rc, out, err = run_cmd(["renga", "--version"])
    if rc == 127:
        report.ok = False
        report.failures.append("renga binary not found on PATH")
        return
    if rc != 0:
        report.ok = False
        report.failures.append(f"`renga --version` exited {rc}: {err.strip()}")
        return
    v = parse_version(out)
    if v is None:
        report.ok = False
        report.failures.append(
            f"could not parse renga version from output: {out!r}"
        )
        return
    report.renga_version = ".".join(str(x) for x in v)
    report.renga_version_tuple = list(v)
    if cmp_version(v, MIN_REQUIRED_VERSION) < 0:
        report.ok = False
        report.failures.append(
            f"renga {report.renga_version} is older than required "
            f"{report.renga_min_required}. Run: "
            "`npm install -g @suisya-systems/renga@0.18.0` (or later)"
        )


# Layer 2 ---------------------------------------------------------------------


def check_mcp_registration(report: CheckReport) -> None:
    rc, out, err = run_cmd(["claude", "mcp", "list"])
    if rc == 127:
        report.ok = False
        report.failures.append(
            "`claude` CLI not found. MCP registration cannot be verified."
        )
        return
    if rc != 0:
        report.ok = False
        report.failures.append(f"`claude mcp list` exited {rc}: {err.strip()}")
        return
    for line in out.splitlines():
        if "renga-peers" in line:
            report.mcp_registration_line = line.strip()
            # `✓ Connected` or `Connected` indicates live
            if "Connected" in line:
                report.mcp_registered = True
                # Extract renga path for display (best-effort)
                m = re.search(r":\s*(\S+renga\S*)", line)
                if m:
                    report.renga_path = m.group(1)
            else:
                report.mcp_registered = False
                report.failures.append(
                    "renga-peers MCP is registered but not Connected. "
                    "Try `renga mcp install --force`."
                )
                report.ok = False
            return
    report.mcp_registered = False
    report.ok = False
    report.failures.append(
        "renga-peers MCP not registered in Claude Code. "
        "Run: `renga mcp install`"
    )


# Layer 3 ---------------------------------------------------------------------


def parse_tools_list_response(raw_stdout: str) -> Optional[set[str]]:
    """Extract the tools/list result tool names from renga mcp-peer stdout.

    renga mcp-peer speaks newline-delimited JSON-RPC on stdio (MCP stdio
    transport — not LSP-style Content-Length framing). We send multiple
    requests on separate lines and the peer writes one JSON response per
    line. Iterate lines looking for the tools/list response (method result
    has a `tools` array).

    Returns the set of tool names on success, or None if the stream
    contained no tools/list result.
    """
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = msg.get("result")
        if not isinstance(result, dict):
            continue
        tools = result.get("tools")
        if isinstance(tools, list):
            return {t.get("name") for t in tools if t.get("name")}
    return None


def check_mcp_tool_surface(report: CheckReport) -> None:
    """Query `renga mcp-peer` stdio for tools/list. No live session needed.

    Sends an MCP-spec-compliant pair of requests on stdio:
      1. `initialize` (required by some strict MCP servers; renga-peers
         is lenient but we send it defensively)
      2. `tools/list`

    renga mcp-peer uses newline-delimited JSON-RPC over stdio (the MCP
    stdio transport), not LSP Content-Length framing.
    """
    payload = (
        json.dumps({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "claude-org-preflight", "version": "1.0",
                },
            },
        }) + "\n"
        + json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list"
        }) + "\n"
    )
    rc, out, err = run_cmd(
        ["renga", "mcp-peer"], stdin=payload, timeout=10.0,
    )
    if rc == 127:
        # Already flagged in layer 1
        return
    if rc != 0 and not out.strip():
        report.ok = False
        report.failures.append(
            f"`renga mcp-peer` tools/list probe failed (rc={rc}): "
            f"{err.strip()[:200]}"
        )
        return
    found = parse_tools_list_response(out)
    if found is None:
        report.ok = False
        report.failures.append(
            "could not extract tools/list response from renga mcp-peer "
            "output (no JSON-RPC message with result.tools[])"
        )
        return
    report.mcp_tools_found = sorted(found)
    missing = [t for t in REQUIRED_MCP_TOOLS if t not in found]
    report.mcp_tools_missing = missing
    if missing:
        report.ok = False
        report.failures.append(
            f"renga-peers MCP is missing required tools: {', '.join(missing)}. "
            "Upgrade renga or re-run `renga mcp install --force`."
        )


# Reporting -------------------------------------------------------------------


def emit_text(report: CheckReport) -> None:
    def status(cond: bool) -> str:
        return "OK  " if cond else "FAIL"

    print("renga compatibility preflight")
    print("=" * 56)

    v_ok = report.renga_version is not None and not any(
        "renga" in f and "older" in f for f in report.failures
    ) and not any("renga binary not found" in f for f in report.failures)
    print(f"[{status(v_ok)}] renga version: "
          f"{report.renga_version or '(unknown)'} "
          f"(need >= {report.renga_min_required})")

    mcp_ok = report.mcp_registered is True
    print(f"[{status(mcp_ok)}] renga-peers MCP registered + connected")
    if report.mcp_registration_line:
        print(f"         {report.mcp_registration_line}")

    if report.mcp_tools_probe_skipped:
        print("[SKIP] MCP tool surface (probe skipped via --skip-mcp-probe)")
    else:
        tools_ok = (
            not report.mcp_tools_missing
            and bool(report.mcp_tools_found)
        )
        print(f"[{status(tools_ok)}] MCP tool surface "
              f"({len(report.mcp_tools_found)}/{len(REQUIRED_MCP_TOOLS)} "
              "required tools present)")
        if report.mcp_tools_missing:
            print(f"         missing: {', '.join(report.mcp_tools_missing)}")

    if report.failures:
        print()
        print("Failures:")
        for f in report.failures:
            print(f"  - {f}")

    if report.recommendations:
        print()
        print("Recommendations:")
        for r in report.recommendations:
            print(f"  - {r}")

    print()
    print(f"Result: {'OK' if report.ok else 'FAIL'}")


def emit_json(report: CheckReport) -> None:
    # Produce a stable-shape JSON doc; Dispatcher/Secretary can consume it.
    doc = {
        "ok": report.ok,
        "renga": {
            "version": report.renga_version,
            "version_tuple": report.renga_version_tuple,
            "min_required": report.renga_min_required,
            "path": report.renga_path,
        },
        "mcp": {
            "registered": report.mcp_registered,
            "registration_line": report.mcp_registration_line,
            "tools_found": report.mcp_tools_found,
            "tools_missing": report.mcp_tools_missing,
            "tools_required": list(REQUIRED_MCP_TOOLS),
        },
        "failures": report.failures,
        "recommendations": report.recommendations,
    }
    print(json.dumps(doc, indent=2, ensure_ascii=False))


def _reconfigure_stdout() -> None:
    # On Windows, the default console encoding (cp932 on JP locales) can't
    # encode `✓` or other chars that appear in `claude mcp list` output.
    # Re-wrap stdout/stderr to UTF-8 with replacement so the script never
    # crashes on display. `reconfigure` is available on 3.7+ TextIOWrapper.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv: Optional[list[str]] = None) -> int:
    _reconfigure_stdout()

    p = argparse.ArgumentParser(
        description="renga compatibility preflight for claude-org"
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of console text",
    )
    p.add_argument(
        "--e2e", action="store_true",
        help="(reserved) run opt-in pane spawn/close smoke test; not yet "
             "implemented — must not mutate the user's live renga layout",
    )
    p.add_argument(
        "--skip-mcp-probe", action="store_true",
        help="skip `renga mcp-peer` tool-surface probe (static checks only)",
    )
    args = p.parse_args(argv)

    report = CheckReport()

    check_renga_version(report)
    check_mcp_registration(report)
    if args.skip_mcp_probe:
        report.mcp_tools_probe_skipped = True
    else:
        check_mcp_tool_surface(report)

    if args.e2e:
        report.recommendations.append(
            "--e2e mode is reserved; pane spawn/close smoke not yet "
            "implemented in v1 (would mutate live layout)"
        )

    if args.json:
        emit_json(report)
    else:
        emit_text(report)

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
