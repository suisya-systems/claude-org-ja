"""ccmux compatibility preflight for aainc-ops (Issue #61).

Layered checks:
  1. ccmux binary version (static)
  2. `ccmux-peers` MCP registration in `claude mcp list`
  3. MCP tool surface via `ccmux mcp-peer` stdio (no live session needed)
  4. Optional live smoke (inside a ccmux --layout ops session) — MCP tools
     run by Claude; this script only *documents* them (does not shell in)
  5. Optional `--e2e` — spawn/close a throwaway pane to verify lifecycle

Usage:
  py -3 tools/check_ccmux_compat.py
  py -3 tools/check_ccmux_compat.py --json
  py -3 tools/check_ccmux_compat.py --e2e      # (reserved, not implemented)

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

# aainc-ops' ccmux contract.
# When this list grows, bump MIN_REQUIRED_VERSION accordingly.
MIN_REQUIRED_VERSION = (0, 18, 0)

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
    ccmux_version: Optional[str] = None
    ccmux_version_tuple: Optional[list[int]] = None
    ccmux_min_required: str = ".".join(str(x) for x in MIN_REQUIRED_VERSION)
    ccmux_path: Optional[str] = None
    mcp_registered: Optional[bool] = None
    mcp_registration_line: Optional[str] = None
    mcp_tools_found: list[str] = field(default_factory=list)
    mcp_tools_missing: list[str] = field(default_factory=list)
    mcp_tools_probe_skipped: bool = False
    failures: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def parse_version(s: str) -> Optional[tuple[int, int, int]]:
    """Parse 'ccmux 0.18.0' or '0.18.0' into (0, 18, 0).

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


def check_ccmux_version(report: CheckReport) -> None:
    rc, out, err = run_cmd(["ccmux", "--version"])
    if rc == 127:
        report.ok = False
        report.failures.append("ccmux binary not found on PATH")
        return
    if rc != 0:
        report.ok = False
        report.failures.append(f"`ccmux --version` exited {rc}: {err.strip()}")
        return
    v = parse_version(out)
    if v is None:
        report.ok = False
        report.failures.append(
            f"could not parse ccmux version from output: {out!r}"
        )
        return
    report.ccmux_version = ".".join(str(x) for x in v)
    report.ccmux_version_tuple = list(v)
    if cmp_version(v, MIN_REQUIRED_VERSION) < 0:
        report.ok = False
        report.failures.append(
            f"ccmux {report.ccmux_version} is older than required "
            f"{report.ccmux_min_required}. Run: "
            "`npm install -g ccmux-fork@0.18.0` (or later)"
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
        if "ccmux-peers" in line:
            report.mcp_registration_line = line.strip()
            # `✓ Connected` or `Connected` indicates live
            if "Connected" in line:
                report.mcp_registered = True
                # Extract ccmux path for display (best-effort)
                m = re.search(r":\s*(\S+ccmux\S*)", line)
                if m:
                    report.ccmux_path = m.group(1)
            else:
                report.mcp_registered = False
                report.failures.append(
                    "ccmux-peers MCP is registered but not Connected. "
                    "Try `ccmux mcp install --force`."
                )
                report.ok = False
            return
    report.mcp_registered = False
    report.ok = False
    report.failures.append(
        "ccmux-peers MCP not registered in Claude Code. "
        "Run: `ccmux mcp install`"
    )


# Layer 3 ---------------------------------------------------------------------


def check_mcp_tool_surface(report: CheckReport) -> None:
    """Query `ccmux mcp-peer` stdio for tools/list. No live session needed."""
    req = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ) + "\n"
    rc, out, err = run_cmd(["ccmux", "mcp-peer"], stdin=req, timeout=10.0)
    if rc == 127:
        # Already flagged in layer 1
        return
    if rc != 0 and not out.strip():
        report.ok = False
        report.failures.append(
            f"`ccmux mcp-peer` tools/list probe failed (rc={rc}): "
            f"{err.strip()[:200]}"
        )
        return
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as e:
        report.ok = False
        report.failures.append(
            f"could not parse tools/list JSON from ccmux mcp-peer: {e}"
        )
        return
    tools = payload.get("result", {}).get("tools", [])
    found = {t.get("name") for t in tools if t.get("name")}
    report.mcp_tools_found = sorted(found)
    missing = [t for t in REQUIRED_MCP_TOOLS if t not in found]
    report.mcp_tools_missing = missing
    if missing:
        report.ok = False
        report.failures.append(
            f"ccmux-peers MCP is missing required tools: {', '.join(missing)}. "
            "Upgrade ccmux or re-run `ccmux mcp install --force`."
        )


# Reporting -------------------------------------------------------------------


def emit_text(report: CheckReport) -> None:
    def status(cond: bool) -> str:
        return "OK  " if cond else "FAIL"

    print("ccmux compatibility preflight")
    print("=" * 56)

    v_ok = report.ccmux_version is not None and not any(
        "ccmux" in f and "older" in f for f in report.failures
    ) and not any("ccmux binary not found" in f for f in report.failures)
    print(f"[{status(v_ok)}] ccmux version: "
          f"{report.ccmux_version or '(unknown)'} "
          f"(need >= {report.ccmux_min_required})")

    mcp_ok = report.mcp_registered is True
    print(f"[{status(mcp_ok)}] ccmux-peers MCP registered + connected")
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
    # Produce a stable-shape JSON doc; Foreman/Secretary can consume it.
    doc = {
        "ok": report.ok,
        "ccmux": {
            "version": report.ccmux_version,
            "version_tuple": report.ccmux_version_tuple,
            "min_required": report.ccmux_min_required,
            "path": report.ccmux_path,
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
        description="ccmux compatibility preflight for aainc-ops"
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of console text",
    )
    p.add_argument(
        "--e2e", action="store_true",
        help="(reserved) run opt-in pane spawn/close smoke test; not yet "
             "implemented — must not mutate the user's live ccmux layout",
    )
    p.add_argument(
        "--skip-mcp-probe", action="store_true",
        help="skip `ccmux mcp-peer` tool-surface probe (static checks only)",
    )
    args = p.parse_args(argv)

    report = CheckReport()

    check_ccmux_version(report)
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
