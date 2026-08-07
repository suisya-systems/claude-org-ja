"""renga compatibility preflight for claude-org (Issue #61, Issue #823).

Layered checks:
  2. `renga-peers` MCP registration in `claude mcp list` — runs FIRST,
     because its registration row is what names the executable Claude Code
     actually launches as the mcp-peer. Every later layer probes THAT
     executable.
  1. mcp-peer binary version (static), measured on the resolved executable
  3. MCP tool surface via `<resolved> mcp-peer` stdio (no live session
     needed)
  3b. Live capability probe via the `server_info` MCP tool (renga 2.0.0+).
     Deliberately a SEPARATE status from layer 3: layer 3 only proves the
     mcp-peer *build* exposes a tool, while the capability set is a property
     of the running renga *server* and is only knowable once connected
     (renga docs/api-surface-v1.0.md:321-361).
  4. Optional live smoke (inside a renga --layout ops session) — MCP tools
     run by Claude; this script only *documents* them (does not shell in)
  5. Optional `--e2e` — spawn/close a throwaway pane to verify lifecycle

WHICH BINARY IS PROBED (and why it is not the bare `renga` on PATH):
Claude Code launches the mcp-peer by the exact command stored in its MCP
registration, which is an absolute path. Measured on a dev box,
`claude mcp list` printed
`renga-peers: /home/<user>/.cargo/bin/renga mcp-peer - Connected` and that
binary reported `renga 2.0.0`, while the PATH-first `renga`
(`/home/<user>/.volta/bin/renga`, an npm/volta shim) reported `renga 1.4.0`.
Probing the PATH-first binary therefore measures a program org never runs and
produces a false FAIL. So the registration path is the probe target; PATH is
only a fallback, the choice is always printed, and a version skew between the
two is reported rather than swallowed.

WHEN THE PROBE TARGET CANNOT BE RESOLVED: if `claude mcp list` cannot be run,
errors, or times out, then no registration row was read and the probe target
is UNDETERMINED. The PATH-first binary is measured anyway (a report saying
nothing is useless), but its verdicts are then reported as UNVERIFIED rather
than as confirmed failures - emitting a hard FAIL there would be the very
false FAIL this design exists to prevent, and it is exactly the case in which
the version-skew evidence is missing too. Unverified is not fail-open: it maps
to exit 2, never exit 0.

Two halves, checked separately: the renga *server* process and the
*mcp-peer* client binary are independently versioned and can skew. `renga
--version` reports whichever binary comes first on PATH, which may be
neither half that matters; the server half exposes no version string at all
(only capability tokens - renga docs/api-surface-v1.0.md:334-344). So layer
1 constrains the client half by version and layer 3b constrains the server
half by capability. Both must be 2.0 series.

Usage:
  py -3 tools/check_renga_compat.py
  py -3 tools/check_renga_compat.py --json
  py -3 tools/check_renga_compat.py --require-live   # unverified is a failure
  py -3 tools/check_renga_compat.py --tolerate-blocked-socket   # see below
  py -3 tools/check_renga_compat.py --e2e      # (reserved, not implemented)

`--require-live` asserts that live readiness was actually verified, so it
fails on anything unverified rather than passing vacuously: a skipped probe
(`--skip-capability-probe` / `--skip-mcp-probe`), a `detached` / `unreachable`
answer, or an unresolved probe target. Its remediation text branches on which
of those actually happened, because the fixes have nothing in common.

RUNNING THIS INSIDE A SANDBOX:
layer 3b connects to the renga server over a unix socket. A sandbox that
denies socket connections makes that connect fail even when the server is
running and healthy - `server_info` then answers `unreachable`, which this
preflight reports as FAIL (exit 1). The very same command exits 0 outside the
sandbox. org agents run Bash inside a sandbox by default, so that FAIL is a
routine false alarm there. `--tolerate-blocked-socket` is the opt-in escape
hatch: when the reported `server.endpoint` path still EXISTS on disk - a
server socket is there, only the connection failed - it downgrades that one
case to "live readiness unverified" (exit 2) instead of FAIL. It is opt-in
and narrow on purpose: the default stays fail-closed, and even with the flag
a *missing* socket path is still a hard FAIL, because that is a genuinely
absent server rather than a blocked connection.

Exit codes:
  0 - all required checks pass, live capability readiness verified
  1 - any required check failed
  2 - no required check failed, but something is UNVERIFIED:
      * live capability readiness (run outside a renga pane: `server_info`
        reports `detached`; or `unreachable` with the socket still present
        under `--tolerate-blocked-socket`), or
      * the probe target itself (`claude mcp list` did not resolve, so
        layers 1 / 3 / 3b measured a PATH fallback rather than the binary
        Claude Code launches)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# claude-org's renga support floor.
#
# NOTE ON SCOPE: this constant constrains the **CLI / mcp-peer half only**.
# `server_info` exposes `server.pid` / `server.endpoint` /
# `server.capabilities` but deliberately no server version string, and
# `client.version` is documented as "CARGO_PKG_VERSION of the mcp-peer
# binary, which is *not* the server's version"
# (renga docs/api-surface-v1.0.md:334-344). The running server is therefore
# capability-gatable but NOT version-gatable; layer 3b handles that half.
#
# 2.0.0 is a breaking operational floor for the renga transport, not a
# backward-compatible bump: a 2.0 mcp-peer refuses close_pane /
# set_pane_identity against a server that does not advertise
# `caller_scope_close_identity` (renga docs/api-surface-v1.0.md:576-582),
# so org's pane lifecycle cannot run on an older daemon at all.
MIN_REQUIRED_VERSION = (2, 0, 0)

# Required `renga-peers` MCP tools. Source of truth:
# `printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | renga mcp-peer`
#
# This is a REQUIRED SUBSET, not an exact surface: extra tools are allowed by
# design and always have been. Measured on this machine, mcp-peer 1.4.0
# returns 15 tools and 2.0.0 returns 16 (both include `spawn_codex_pane`,
# which org does not require). Never assert on the observed tool count.
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
    # capability probe (renga 2.0.0, #304)
    "server_info",
]

# Capability tokens org requires the *running* renga server to advertise.
# The full advertised set is renga src/ipc/mod.rs:123-128
# (`SERVER_CAPABILITIES`) and has four tokens; org REQUIRES three of them and
# merely OBSERVES the fourth. The dividing line is "does an operation the
# harness actually performs stop working when the token is absent":
#
# CITATION STYLE (deliberate): references into this repo's own contract are
# made by SECTION ANCHOR, never by line number. That document is edited in the
# same commits as this file, so a line number recorded here goes stale - or
# silently lands on a blank line - the moment a paragraph is inserted above it.
# For the same reason a bullet is quoted only where its label is reproduced in
# full; where a label is long enough that quoting it invites a stale
# paraphrase, the section anchor alone is cited. Line numbers are reserved for
# the upstream renga tree, which this repo does not edit.
#
#   caller_scope - gates the seven pane tools of renga #288 (list_panes,
#     spawn_pane, spawn_claude_pane, spawn_codex_pane, focus_pane,
#     inspect_pane, send_keys). Without it those resolve against the tab the
#     user is VIEWING, so org may read a focus change as a pane exit
#     (docs/contracts/backend-interface-contract.md, T-§4.2).
#   cross_tab_peers - the bundled mcp-peer refuses list_peers / send_message
#     outright without it (renga docs/api-surface-v1.0.md:561-566), and this
#     contract makes the peer-messaging channel a MUST
#     (docs/contracts/backend-interface-contract.md, Surface 2: Messaging).
#   caller_scope_close_identity - a token of its own (#296, renga
#     docs/api-surface-v1.0.md:576-582): a #290-era server advertises the
#     three earlier tokens yet drops the unknown `from_pane` on close /
#     set_pane_identity and closes a pane in whatever tab the user is
#     viewing - irreversibly. Must be required explicitly, never inferred
#     from `caller_scope`
#     (docs/contracts/backend-interface-contract.md, T-§cap, bullet
#     "Independence").
REQUIRED_CAPABILITIES = [
    "caller_scope",
    "cross_tab_peers",
    "caller_scope_close_identity",
]

# Advertised but NOT required: reported for diagnostics only, never a
# failure.
#
# `spawn_tab` gates exactly one thing upstream: a `spawn_*` call that
# carries a `tab` selector. "Calls without `tab` keep requiring only
# `caller_scope`" (renga docs/api-surface-v1.0.md:573-574). Org never sends
# one - the harness MUST launch every orchestrator-spawned pane in the same
# tab (backend-interface-contract.md, §4.2 SINGLE-TAB MUST), and the 2.0
# amendment explicitly leaves that deployment rule intact (same file, T-§4.2,
# bullet "Retained from ratified §4.2 (not superseded)") and puts placement
# behaviour out of scope (same file, T-§cap, the `spawn_tab` bullet:
# "The placement behaviour itself is out of scope for this amendment").
# So `spawn_tab` gates no code path org can reach, and hard-failing a
# preflight on a capability the contract forbids org from exercising has no
# operational basis.
#
# That is the whole argument, and it is the only admissible shape of
# argument: each token is decided against itself, never against another. The
# contract requires every gate to be resolved on its own token and rules out
# reasoning about the four through release timing
# (backend-interface-contract.md, T-§cap, bullet "Independence").
OBSERVED_CAPABILITIES = [
    "spawn_tab",
]

# Capability probe statuses (report.capability_probe_status).
#   connected   - live server answered; effective_capabilities is authoritative
#   detached    - a statement about THIS CLIENT, not about any server: the
#                 mcp-peer was not started inside a renga pane (upstream
#                 reason: "RENGA_PANE_ID not set"), so it has no pane to
#                 resolve a socket from and asked nobody. It carries ZERO
#                 information about whether a renga server exists or what it
#                 supports. Live readiness UNVERIFIED (exit 2), never
#                 "server absent" and never "capabilities empty".
#   unreachable - a socket WAS identified and the connection failed (gone /
#                 different instance / blocked by a sandbox); treated as FAIL
#                 unless --tolerate-blocked-socket applies (see
#                 UNREACHABLE_ENDPOINT_STATES)
#   tool_absent - `server_info` not in tools/list -> below the 2.0.0 floor
#   call_error  - `server_info` present but the call errored (e.g. -32601)
#   skipped     - probe not run (--skip-capability-probe / --skip-mcp-probe)
CAPABILITY_PROBE_STATUSES = (
    "connected", "detached", "unreachable",
    "tool_absent", "call_error", "skipped",
)

# Sub-classification of an `unreachable` probe, from the endpoint renga
# reports (it is retained on `unreachable` precisely so a caller can tell
# WHICH socket it failed to reach - renga
# docs/api-surface-v1.0.md:339). This is what makes
# `--tolerate-blocked-socket` narrow rather than a blanket fail-open.
#   socket_present - the endpoint path still exists on this filesystem, so a
#                    server socket IS there and only the *connect* failed.
#                    That is the shape a sandbox that denies unix-socket
#                    connections produces. Downgradable, opt-in only.
#   socket_absent  - the endpoint path does not exist: no server. NEVER
#                    downgraded, flag or no flag.
#   unknown        - nothing was reported, or the path could not be examined
#                    (permission error, or a Windows named pipe, where an
#                    existence check is not dependable). Fail-closed.
UNREACHABLE_ENDPOINT_STATES = ("socket_present", "socket_absent", "unknown")


def classify_unreachable_endpoint(endpoint: Optional[str]) -> str:
    """Decide whether an `unreachable` looks like a blocked-but-live socket.

    Returns one of UNREACHABLE_ENDPOINT_STATES. Pure apart from one
    filesystem existence check, so it is directly unit-testable.

    Deliberately conservative: anything that cannot be positively established
    as "the socket file is still there" comes back `unknown`, which the
    caller treats as a failure. A sandbox that blocks `connect()` normally
    still permits `stat()`, so the distinguishing observation is available in
    the case this exists for; where it is not, the answer stays FAIL.
    """
    if not endpoint:
        return "unknown"
    # Windows named pipes (`\\.\pipe\renga-<pid>`) are not filesystem paths in
    # the sense os.path.exists reasons about; do not pretend to classify them.
    if endpoint.startswith("\\\\"):
        return "unknown"
    try:
        present = os.path.exists(endpoint)
    except OSError:
        return "unknown"
    return "socket_present" if present else "socket_absent"

# Shared remediation text. The two renga halves are versioned independently
# and `renga --version` only ever reports one of them. The long form is
# emitted once (as a warning); failures carry the short pointer.
TWO_HALVES_NOTE = (
    "TWO HALVES: the renga SERVER and the renga-peers MCP-PEER client are "
    "separate, independently-versioned halves and BOTH must be 2.0 series. "
    "`renga --version` reports whichever binary is first on PATH, which is "
    "not necessarily the one Claude Code launches, so verify the halves "
    "individually: run `--version` on the ABSOLUTE PATH printed by "
    "`claude mcp list` for the client half, and read `server_info` for the "
    "server half. Note that server_info.client.version is the mcp-peer's "
    "version, NOT the server's - the server exposes no version string at "
    "all, only capability tokens, so it is capability-gated rather than "
    "version-gated."
)
TWO_HALVES_SHORT = (
    "Both renga halves (server and mcp-peer) must be 2.0 series and must be "
    "verified separately - see the TWO HALVES note under Warnings."
)


@dataclass
class CheckReport:
    ok: bool = True
    # Version of the binary org actually launches as the mcp-peer (see
    # `mcp_peer_binary`), NOT of whatever `renga` happens to be first on PATH.
    renga_version: Optional[str] = None
    renga_version_tuple: Optional[list[int]] = None
    renga_min_required: str = ".".join(str(x) for x in MIN_REQUIRED_VERSION)
    version_check_ok: bool = False
    renga_path: Optional[str] = None
    # The executable every probe (layers 1 / 3 / 3b) is run against, and
    # where that choice came from. Recorded so a reader can never be left
    # guessing which program was measured.
    mcp_peer_binary: Optional[str] = None
    mcp_peer_binary_source: Optional[str] = None
    path_first_renga: Optional[str] = None
    path_first_renga_version: Optional[str] = None
    binary_version_skew: bool = False
    mcp_registered: Optional[bool] = None
    mcp_registration_line: Optional[str] = None
    # Did `claude mcp list` actually answer? True = a verdict was reached
    # (registered or provably not); False = the question could not be put at
    # all (CLI missing, non-zero exit, timeout), which is NOT the same as "not
    # registered" and must not be reported as one.
    mcp_registration_resolved: Optional[bool] = None
    # True only when the probed executable came out of a registration row.
    # False means layers 1 / 3 / 3b measured a PATH fallback, i.e. possibly a
    # program org never launches.
    probe_target_confirmed: bool = False
    # Set when an unconfirmed probe target made those layers' verdicts
    # unverifiable; forces exit 2 rather than exit 0.
    probe_target_unverified: bool = False
    mcp_tools_found: list[str] = field(default_factory=list)
    mcp_tools_missing: list[str] = field(default_factory=list)
    mcp_tools_probe_skipped: bool = False
    # --- layer 3b: live capability probe (additive; all defaulted) ---
    capability_probe_status: Optional[str] = None
    capability_probe_reason: Optional[str] = None
    # Optional[list] on purpose: `[]` ("asked, supports nothing") and `null`
    # ("never asked") are semantically distinct per
    # renga docs/api-surface-v1.0.md:337. Collapsing them would reintroduce
    # exactly the misreading this probe exists to prevent.
    server_capabilities: Optional[list[str]] = None
    effective_capabilities: Optional[list[str]] = None
    client_capabilities: Optional[list[str]] = None
    capabilities_missing: list[str] = field(default_factory=list)
    # Advertised-but-not-required tokens that were absent. Diagnostic only:
    # never sets `ok = False` (see OBSERVED_CAPABILITIES).
    capabilities_observed_missing: list[str] = field(default_factory=list)
    client_version_reported: Optional[str] = None
    server_pid: Optional[int] = None
    server_endpoint: Optional[str] = None
    # Set only when the probe came back `unreachable`; one of
    # UNREACHABLE_ENDPOINT_STATES. Recorded even when the flag is off, so a
    # FAIL report still tells the reader whether the socket was there.
    unreachable_endpoint_state: Optional[str] = None
    tolerate_blocked_socket: bool = False
    renga_path_candidates: list[str] = field(default_factory=list)
    # Same inventory as `renga_path_candidates`, unformatted, so a second
    # consumer does not have to re-run `--version` on every candidate (or
    # parse the display strings back apart). Not emitted.
    path_candidate_pairs: list[tuple[str, Optional[str]]] = field(
        default_factory=list, repr=False
    )
    live_readiness_unverified: bool = False
    warnings: list[str] = field(default_factory=list)
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


def find_renga_on_path() -> list[str]:
    """Return every `renga` executable on PATH, in PATH order.

    `shutil.which` returns only the first match, which is precisely what
    hides a PATH skew (measured on a dev box: a 1.4.0 npm/volta shim
    shadowing a 2.0.0 cargo build). Scan the PATH entries directly.
    """
    names = ["renga.exe", "renga.cmd", "renga.bat"] if os.name == "nt" \
        else ["renga"]
    seen: set[str] = set()
    found: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for name in names:
            cand = os.path.join(entry, name)
            if cand in seen:
                continue
            seen.add(cand)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                found.append(cand)
    return found


def probe_renga_path_candidates() -> list[tuple[str, Optional[str]]]:
    """`(path, version-or-None)` for every renga on PATH, in PATH order."""
    out: list[tuple[str, Optional[str]]] = []
    for path in find_renga_on_path():
        out.append((path, binary_version(path)))
    return out


def binary_version(path: str) -> Optional[str]:
    """`--version` of one executable, or None if it did not answer."""
    rc, out, _err = run_cmd([path, "--version"], timeout=10.0)
    if rc != 0:
        return None
    v = parse_version(out)
    return ".".join(str(x) for x in v) if v else None


def describe_renga_path_candidates() -> list[str]:
    """`"<path> (<version-or-?>)"` for every renga on PATH."""
    return [
        f"{path} ({ver or 'unknown'})"
        for path, ver in probe_renga_path_candidates()
    ]


def _ensure_path_candidates(report: CheckReport) -> None:
    """Populate the PATH inventory once, and warn when it is ambiguous.

    Without the candidate list, a developer whose PATH-first renga is old has
    no way to learn that a newer one is already installed elsewhere (measured
    on this machine: a 1.4.0 volta shim ahead of a 2.0.0 cargo build).
    """
    if report.renga_path_candidates:
        return
    candidates = probe_renga_path_candidates()
    report.path_candidate_pairs = list(candidates)
    report.renga_path_candidates = [
        f"{path} ({ver or 'unknown'})" for path, ver in candidates
    ]
    if candidates:
        report.path_first_renga, report.path_first_renga_version = \
            candidates[0]
    if len(candidates) > 1:
        report.warnings.append(
            "multiple renga binaries on PATH: "
            + "; ".join(report.renga_path_candidates)
            + ". `renga --version` reports the first one only, so it cannot "
            "tell you which half is which."
        )


def _record_path_skew(report: CheckReport) -> None:
    """Populate path candidates AND emit the two-halves note once."""
    if TWO_HALVES_NOTE not in report.warnings:
        report.warnings.append(TWO_HALVES_NOTE)
    _ensure_path_candidates(report)


# Layer 2 ---------------------------------------------------------------------
#
# Runs FIRST: its output names the executable Claude Code launches as the
# mcp-peer, which is the executable every later layer must measure.

MCP_SERVER_NAME = "renga-peers"

# `claude mcp list` health-checks every registered server, remote ones
# included, so its runtime is bounded by the slowest HTTP endpoint rather than
# by anything local. Measured on this machine it took 3.15-3.66s with a remote
# server in the list; the default 15s ceiling is close enough to that to be
# reachable on a slower link or with more remote servers registered, and a
# timeout here used to turn into a confirmed FAIL measured on the wrong
# binary. Given a generous ceiling AND the unverified classification below,
# neither the slow case nor the hung case can produce a false FAIL.
MCP_LIST_TIMEOUT = 45.0

# Recorded as `mcp_peer_binary_source`; compared by value in more than one
# place, so it is a constant rather than a repeated literal.
SOURCE_REGISTRATION = "claude mcp list registration"
SOURCE_PATH_FALLBACK = "PATH fallback"


def parse_mcp_registration_line(line: str) -> Optional[str]:
    """Extract the mcp-peer executable from a `claude mcp list` row.

    Rows look like `<name>: <command> <args...> - <status>`, e.g.
    `renga-peers: /home/<user>/.cargo/bin/renga mcp-peer - Connected`
    (measured). The server name itself may contain colons (`plugin:slack:
    slack: ...`), so anchor on the `renga-peers:` marker rather than the
    first colon, strip the trailing status after the last ` - `, and take
    everything before the ` mcp-peer` argument so an executable path
    containing spaces (Windows `Program Files`) survives intact.

    Returns None when the row carries no usable command (e.g. an HTTP URL, or
    a bare `renga` with no path), which is the caller's cue to fall back to
    PATH.
    """
    text = line.strip()
    marker = MCP_SERVER_NAME + ":"
    idx = text.find(marker)
    if idx < 0:
        return None
    rest = text[idx + len(marker):].strip()
    if " - " in rest:
        rest = rest.rsplit(" - ", 1)[0].strip()
    if not rest:
        return None
    suffix = " mcp-peer"
    if rest.endswith(suffix):
        exe = rest[: -len(suffix)].strip()
    else:
        exe = rest.split()[0]
    if not exe or "://" in exe:
        return None
    return exe


def check_mcp_registration(report: CheckReport) -> None:
    """Read the registration row, or record that the question went unanswered.

    Three outcomes, kept distinct on purpose:
      * a row was read -> `mcp_registration_resolved = True`, and
        `mcp_registered` is that row's verdict;
      * `claude mcp list` ran and named no `renga-peers` row -> resolved, and
        the absence is a real FAIL;
      * `claude mcp list` could not be run / errored / timed out ->
        `mcp_registration_resolved = False`. This is UNDETERMINED, not
        "unregistered": nothing was learned about the registration, and in
        particular the probe target was not learned either. It is recorded as
        a warning, and `run_checks` downgrades the dependent layers rather
        than failing them against a binary that may be the wrong one.
    """
    rc, out, err = run_cmd(["claude", "mcp", "list"], timeout=MCP_LIST_TIMEOUT)
    if rc in (127, 124) or rc != 0:
        report.mcp_registration_resolved = False
        if rc == 127:
            detail = "`claude` CLI not found"
        elif rc == 124:
            detail = (
                f"`claude mcp list` timed out after {MCP_LIST_TIMEOUT:g}s "
                "(it health-checks every registered server, remote ones "
                "included)"
            )
        else:
            detail = f"`claude mcp list` exited {rc}: {err.strip()}"
        report.warnings.append(
            f"MCP registration UNDETERMINED: {detail}. This is not evidence "
            "that renga-peers is unregistered - the question could not be "
            "put. It also means the mcp-peer executable could not be resolved "
            "from the registration, so every layer below measured a PATH "
            "fallback instead of the binary Claude Code launches. Re-run once "
            "`claude mcp list` answers before drawing any conclusion."
        )
        return
    report.mcp_registration_resolved = True
    for line in out.splitlines():
        if MCP_SERVER_NAME in line:
            report.mcp_registration_line = line.strip()
            # The registered command is the binary org actually runs; capture
            # it whether or not the health check passed, since a failing
            # health check is exactly when you want to know which binary it
            # was.
            report.renga_path = parse_mcp_registration_line(line)
            # `✓ Connected` or `Connected` indicates live
            if "Connected" in line:
                report.mcp_registered = True
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


# Binary resolution -----------------------------------------------------------


def resolve_probe_binary(report: CheckReport) -> str:
    """Pick the executable layers 1 / 3 / 3b probe, and record the choice.

    The registered path wins: Claude Code launches the mcp-peer by that exact
    command, so it is the only binary whose version and tool surface describe
    what org will really run. PATH is a fallback for environments where the
    registration is missing or carries no path - the preflight must still say
    something useful there - and the fallback is always disclosed, never
    silent.
    """
    if report.renga_path:
        report.mcp_peer_binary = report.renga_path
        report.mcp_peer_binary_source = SOURCE_REGISTRATION
        report.probe_target_confirmed = True
        return report.renga_path
    report.mcp_peer_binary = "renga"
    report.mcp_peer_binary_source = SOURCE_PATH_FALLBACK
    report.probe_target_confirmed = False
    report.warnings.append(
        "no mcp-peer executable could be read out of `claude mcp list`, so "
        "this preflight fell back to the PATH-first `renga`. That binary is "
        "not necessarily the one Claude Code launches; treat every result "
        "below as being about the PATH binary only."
    )
    _warn_on_fallback_candidate_skew(report)
    return "renga"


def _warn_on_fallback_candidate_skew(report: CheckReport) -> None:
    """On a PATH fallback, look for skew across the whole PATH inventory.

    `check_binary_skew` compares the registered binary against PATH, so it has
    nothing to compare on a fallback run - which is precisely the run where a
    reader most needs to be told that the number below is one of several
    disagreeing answers. Compare the candidates against each other instead.
    """
    _ensure_path_candidates(report)
    candidates = [
        (path, ver) for path, ver in report.path_candidate_pairs if ver
    ]
    versions = {ver for _path, ver in candidates}
    if len(versions) < 2:
        return
    report.binary_version_skew = True
    if TWO_HALVES_NOTE not in report.warnings:
        report.warnings.append(TWO_HALVES_NOTE)
    report.warnings.append(
        "VERSION SKEW among the renga binaries on PATH: "
        + "; ".join(f"{path} reports {ver}" for path, ver in candidates)
        + ". The registration could not be read on this run, so which of "
        "these Claude Code would launch is UNKNOWN and the version reported "
        "below is only the PATH-first answer. Resolve `claude mcp list` "
        "before treating any of them as this environment's verdict."
    )


def check_binary_skew(report: CheckReport) -> None:
    """Report a version disagreement between the registered binary and PATH.

    This is the trap the preflight exists to expose, so it is never
    swallowed: measured on a dev box, the registered
    `/home/<user>/.cargo/bin/renga` was 2.0.0 while `renga --version` in a
    shell answered 1.4.0 from `/home/<user>/.volta/bin/renga`. Whichever
    binary a human happens to type at, the two answers disagree, and only the
    registered one is the one org runs.
    """
    _ensure_path_candidates(report)
    if report.mcp_peer_binary_source != SOURCE_REGISTRATION:
        return
    registered = report.mcp_peer_binary
    if not registered or not report.path_first_renga:
        return
    if os.path.realpath(registered) == os.path.realpath(
        report.path_first_renga
    ):
        return
    registered_version = report.renga_version
    path_version = report.path_first_renga_version
    if registered_version is None or path_version is None:
        return
    if registered_version == path_version:
        return
    report.binary_version_skew = True
    if TWO_HALVES_NOTE not in report.warnings:
        report.warnings.append(TWO_HALVES_NOTE)
    report.warnings.append(
        "VERSION SKEW between the registered mcp-peer and the PATH-first "
        f"renga: the registered binary {registered} reports "
        f"{registered_version}, while {report.path_first_renga} - the one a "
        f"bare `renga --version` answers from - reports {path_version}. "
        "Everything in this report is about the registered binary, because "
        "that is the one Claude Code launches. Do not use a bare "
        "`renga --version` to judge this environment."
    )


# Layer 1 ---------------------------------------------------------------------


def check_renga_version(report: CheckReport, binary: str = "renga") -> None:
    """Version-gate the mcp-peer half, measured on the resolved binary."""
    rc, out, err = run_cmd([binary, "--version"])
    if rc == 127:
        report.ok = False
        report.failures.append(
            f"renga binary not found: {binary} "
            f"(source: {report.mcp_peer_binary_source or 'PATH'})"
        )
        return
    if rc != 0:
        report.ok = False
        report.failures.append(
            f"`{binary} --version` exited {rc}: {err.strip()}"
        )
        return
    v = parse_version(out)
    if v is None:
        report.ok = False
        report.failures.append(
            f"could not parse renga version from `{binary} --version` "
            f"output: {out!r}"
        )
        return
    report.renga_version = ".".join(str(x) for x in v)
    report.renga_version_tuple = list(v)
    if cmp_version(v, MIN_REQUIRED_VERSION) < 0:
        report.ok = False
        _record_path_skew(report)
        # Remediation must act on THIS binary, not on some other copy. The
        # path here came from the `claude mcp list` registration, and that
        # registration is what Claude Code actually launches. A bare
        # `npm install -g ...` installs into whichever prefix npm owns, which
        # is a different file whenever the registered binary was built or
        # installed another way (a cargo build, a distro package, a manual
        # copy). Prescribing it alone produces the worst outcome available:
        # the operator runs the command, sees it succeed, re-runs the
        # preflight, and gets the identical failure - because Claude Code is
        # still launching the old registered file. So name the file and give
        # both halves of the fix.
        report.failures.append(
            f"{binary} is {report.renga_version}, older than the required "
            f"floor {report.renga_min_required}. Upgrade THIS file - it is "
            "the one named by the `claude mcp list` registration, so it is "
            "the binary Claude Code launches; installing a different copy "
            "elsewhere will not clear this failure. Either (a) update it "
            "through whatever installed it (for an npm install: "
            "`npm install -g @suisya-systems/renga@2.0.0` or later; for a "
            "cargo build: rebuild/reinstall from a 2.0 series source), or "
            "(b) install 2.0 series wherever you prefer and then re-point "
            "the registration at it with `renga mcp install --force`, "
            "verifying with `claude mcp list` that the path changed. "
            "Then restart the running renga server so both halves are 2.0 "
            "series. " + TWO_HALVES_SHORT
        )
        return
    report.version_check_ok = True


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


def check_mcp_tool_surface(report: CheckReport, binary: str = "renga") -> None:
    """Query `<binary> mcp-peer` stdio for tools/list. No live session needed.

    `binary` is the executable resolved by `resolve_probe_binary` - the one
    Claude Code launches - not the bare PATH `renga`.

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
        [binary, "mcp-peer"], stdin=payload, timeout=10.0,
    )
    if rc == 127:
        # Already flagged in layer 1
        return
    if rc != 0 and not out.strip():
        report.ok = False
        report.failures.append(
            f"`{binary} mcp-peer` tools/list probe failed (rc={rc}): "
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
    # Required SUBSET check: unknown extra tools are allowed on purpose
    # (2.0.0 ships spawn_codex_pane, which org does not require).
    report.mcp_tools_found = sorted(found)
    missing = [t for t in REQUIRED_MCP_TOOLS if t not in found]
    report.mcp_tools_missing = missing
    if missing:
        report.ok = False
        _record_path_skew(report)
        report.failures.append(
            f"{binary} is missing required renga-peers MCP tools: "
            f"{', '.join(missing)}. "
            "Upgrade renga to 2.0.0 or later and re-run "
            "`renga mcp install --force`. " + TWO_HALVES_SHORT
        )


# Layer 3b --------------------------------------------------------------------
#
# Live capability probe. Kept strictly separate from layer 3: layer 3
# inspects the tool surface of the mcp-peer BUILD (static, answerable
# offline), whereas the capability set belongs to the running renga SERVER
# and is only knowable once actually connected
# (renga docs/api-surface-v1.0.md:321-361). A build that lists the tool
# proves nothing about the daemon it is pointed at.
#
# Note on `server_too_old`: that failure string is deliberately NOT an input
# to any inference here. It is a TOCTOU last line of defence at real call
# sites (the server can be restarted between this probe and a later call);
# capability decisions are made from `effective_capabilities` only.


def parse_server_info_response(
    raw_stdout: str, *, request_id: int = 2
) -> tuple[str, Any]:
    """Extract the `server_info` JSON-RPC response from mcp-peer stdout.

    Returns a discriminated pair:
      ("result", structuredContent)  - the tool answered normally
      ("error",  {"code": int, "message": str}) - JSON-RPC error object
      ("absent", None) - no message with this id in the stream

    Pure and fixture-testable: no subprocess, no live daemon.
    """
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict) or msg.get("id") != request_id:
            continue
        err = msg.get("error")
        if isinstance(err, dict):
            return "error", {
                "code": err.get("code"),
                "message": err.get("message"),
            }
        result = msg.get("result")
        if isinstance(result, dict):
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                return "result", structured
    return "absent", None


def evaluate_capability_probe(
    report: CheckReport,
    structured: dict[str, Any],
    *,
    tolerate_blocked_socket: bool = False,
) -> None:
    """Branch on `server_info.status` and decide pass / warn / fail.

    Contract (renga docs/api-surface-v1.0.md:321-361):
      - `status` is the discriminant; read it first.
      - Gate on `effective_capabilities` (server capabilities intersected
        with what this client build understands), never on
        `server.capabilities` alone and never on any version comparison.
      - `[]` and `null` are different answers and must not be conflated.

    `tolerate_blocked_socket` affects the `unreachable` branch ONLY, and only
    when the reported endpoint still exists on disk. Default False keeps the
    branch fail-closed.
    """
    status = structured.get("status")
    server = structured.get("server") or {}
    client = structured.get("client") or {}

    reason = structured.get("reason")
    report.capability_probe_reason = reason if isinstance(reason, str) else None
    server_caps = server.get("capabilities")
    eff_caps = structured.get("effective_capabilities")
    client_caps = client.get("capabilities")
    report.server_capabilities = (
        list(server_caps) if isinstance(server_caps, list) else None
    )
    report.effective_capabilities = (
        list(eff_caps) if isinstance(eff_caps, list) else None
    )
    report.client_capabilities = (
        list(client_caps) if isinstance(client_caps, list) else None
    )
    pid = server.get("pid")
    report.server_pid = pid if isinstance(pid, int) else None
    endpoint = server.get("endpoint")
    report.server_endpoint = endpoint if isinstance(endpoint, str) else None
    cver = client.get("version")
    report.client_version_reported = cver if isinstance(cver, str) else None

    if status not in ("connected", "detached", "unreachable"):
        report.capability_probe_status = "call_error"
        report.ok = False
        report.failures.append(
            f"`server_info` returned an unrecognized status {status!r}. "
            "This peer does not match the documented contract "
            "(renga docs/api-surface-v1.0.md:321-361)."
        )
        return

    report.capability_probe_status = status

    # Two biconditionals are pinned by upstream test
    # (renga docs/api-surface-v1.0.md:347-350):
    #   server.capabilities != null  <=> status == "connected"
    #   effective_capabilities != null <=> status == "connected"
    # A violation means this is not the contract's peer, so report it rather
    # than proceeding on a value we cannot interpret.
    connected = status == "connected"
    if (report.server_capabilities is not None) != connected or \
            (report.effective_capabilities is not None) != connected:
        report.ok = False
        report.failures.append(
            "`server_info` violated the documented capability/status "
            f"biconditional (status={status!r}, "
            f"server.capabilities null={report.server_capabilities is None}, "
            "effective_capabilities null="
            f"{report.effective_capabilities is None}). Refusing to infer "
            "capabilities from this response "
            "(renga docs/api-surface-v1.0.md:347-350)."
        )
        return

    if connected:
        # `null` never satisfies a requirement; the biconditional above
        # already rules it out here, and `or []` keeps that explicit.
        present = report.effective_capabilities or []
        report.capabilities_missing = [
            c for c in REQUIRED_CAPABILITIES if c not in present
        ]
        # Diagnostic only - absence of an OBSERVED token is never a failure.
        report.capabilities_observed_missing = [
            c for c in OBSERVED_CAPABILITIES if c not in present
        ]
        if report.capabilities_missing:
            report.ok = False
            report.failures.append(
                "the running renga server does not provide required "
                "capabilities: "
                f"{', '.join(report.capabilities_missing)} (advertised: "
                f"{', '.join(report.server_capabilities or []) or '(none)'}). "
                "Recovery: update the running renga daemon to the 2.0 series, "
                "restart it, and re-probe. There is no legacy fallback - a "
                "2.0 mcp-peer gates close_pane / set_pane_identity / "
                "list_peers client-side and refuses to issue the request at "
                "all, so org cannot keep running against an older daemon. "
                + TWO_HALVES_SHORT
            )
        return

    if status == "detached":
        # Not a failure: the preflight is routinely run from a plain shell
        # that renga did not launch. `detached` describes THIS CLIENT (no
        # RENGA_PANE_ID, so no pane to resolve a socket from) and says
        # nothing whatsoever about whether a server is running. But live
        # readiness is genuinely unverified, so say so rather than implying a
        # green light.
        report.live_readiness_unverified = True
        report.warnings.append(
            "live capability readiness UNVERIFIED: `server_info` reports "
            "detached ("
            f"{report.capability_probe_reason or 'no reason given'}). That is "
            "a fact about this CLIENT - it was not launched inside a renga "
            "pane, so it asked no server - and not a claim that a server is "
            "absent or unsupported. Capabilities here are unknown, NOT "
            "empty. Re-run this preflight from inside a renga pane to verify "
            "the server half."
        )
        return

    # unreachable
    endpoint_state = classify_unreachable_endpoint(report.server_endpoint)
    report.unreachable_endpoint_state = endpoint_state
    report.tolerate_blocked_socket = tolerate_blocked_socket
    endpoint_text = report.server_endpoint or "(no endpoint reported)"
    reason_text = report.capability_probe_reason or "no reason given"

    if tolerate_blocked_socket and endpoint_state == "socket_present":
        # The socket file is still there; only connect() failed. That is the
        # signature of a sandbox denying unix-socket connections, not of an
        # absent server. Downgrade to "unverified" (exit 2) rather than FAIL -
        # but never to a green light: nothing about the server's capabilities
        # was actually learned here.
        report.live_readiness_unverified = True
        report.warnings.append(
            "live capability readiness UNVERIFIED: `server_info` reports "
            f"unreachable ({reason_text}), but the endpoint it names still "
            f"exists ({endpoint_text}), so a server socket IS present and "
            "only the "
            "connection failed - the shape a sandbox that blocks unix-socket "
            "connect produces. Downgraded from FAIL to UNVERIFIED because "
            "--tolerate-blocked-socket was given. Capabilities here are "
            "UNKNOWN, not empty, and the server half is NOT verified: re-run "
            "this preflight outside the sandbox, from inside a renga pane, "
            "before treating the server half as checked."
        )
        return

    report.ok = False
    if endpoint_state == "socket_present":
        sandbox_hint = (
            f"The endpoint it names still exists ({endpoint_text}), so a "
            "server socket IS present and only the connection failed. If this "
            "preflight is running inside a sandbox, the sandbox is the likely "
            "cause of that - the identical command exits 0 outside it. Re-run "
            "outside the sandbox, or pass --tolerate-blocked-socket to "
            "downgrade exactly this case to exit 2 (unverified) instead of "
            "FAIL."
        )
    elif endpoint_state == "socket_absent":
        sandbox_hint = (
            f"The endpoint it names does not exist ({endpoint_text}), so the "
            "server really is gone - this is not the sandbox case, and "
            "--tolerate-blocked-socket will not (and must not) downgrade it."
        )
    else:
        sandbox_hint = (
            f"The endpoint could not be classified ({endpoint_text}), so it "
            "is not known whether a socket is still present. "
            "--tolerate-blocked-socket downgrades only a socket that is "
            "provably still there, so it does not apply here. If this "
            "preflight is running inside a sandbox, re-run it outside the "
            "sandbox before believing this result."
        )
    report.failures.append(
        f"`server_info` reports unreachable ({reason_text}); the renga "
        "socket is gone, belongs to a different instance, or could not be "
        "connected to. Capabilities are UNKNOWN, not empty - no capability "
        "conclusion is drawn from this. Recovery: make sure the renga server "
        "is running (2.0 series) and re-probe. " + sandbox_hint
    )


def check_capability_surface(
    report: CheckReport,
    binary: str = "renga",
    *,
    tolerate_blocked_socket: bool = False,
) -> None:
    """Call the `server_info` MCP tool over `<binary> mcp-peer` stdio."""
    if not report.mcp_tools_found:
        # The layer-3 probe itself did not produce a surface (renga missing,
        # or the tools/list read failed). "server_info is absent" would be a
        # false conclusion from a missing observation, so draw none - the
        # underlying failure is already recorded by layer 1 / layer 3.
        report.capability_probe_status = "skipped"
        report.capability_probe_reason = (
            "tool surface unknown (the tools/list probe did not succeed)"
        )
        return
    if "server_info" not in report.mcp_tools_found:
        report.capability_probe_status = "tool_absent"
        report.ok = False
        _record_path_skew(report)
        report.failures.append(
            "`server_info` is absent from the renga-peers tool surface, so "
            "this renga predates capability exposure (renga 2.0.0, #304). "
            "That absence is itself the answer: the installed renga is below "
            "org's 2.0.0 support floor. Recovery: install renga 2.0.0 or "
            "later and restart the running server, then re-probe. "
            + TWO_HALVES_SHORT
        )
        return

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
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "server_info", "arguments": {}},
        }) + "\n"
    )
    rc, out, err = run_cmd([binary, "mcp-peer"], stdin=payload, timeout=10.0)
    if rc == 127:
        # Already flagged in layer 1
        report.capability_probe_status = "skipped"
        report.capability_probe_reason = f"renga binary not found: {binary}"
        return
    if rc != 0 and not out.strip():
        report.capability_probe_status = "call_error"
        report.ok = False
        report.failures.append(
            f"`{binary} mcp-peer` server_info probe failed (rc={rc}): "
            f"{err.strip()[:200]}"
        )
        return

    kind, body = parse_server_info_response(out)
    if kind == "error":
        report.capability_probe_status = "call_error"
        report.capability_probe_reason = str(body.get("message"))
        report.ok = False
        _record_path_skew(report)
        report.failures.append(
            "`server_info` call failed with JSON-RPC error "
            f"{body.get('code')}: {body.get('message')}. A peer that lists "
            "the tool but refuses the call is below org's 2.0.0 support "
            "floor. Recovery: install renga 2.0.0 or later and restart the "
            "running server, then re-probe. " + TWO_HALVES_SHORT
        )
        return
    if kind == "absent":
        report.capability_probe_status = "call_error"
        report.ok = False
        report.failures.append(
            "could not extract a `server_info` response from renga mcp-peer "
            "output (no JSON-RPC message with the matching id). The probe is "
            "inconclusive; no capability conclusion is drawn from it."
        )
        return

    evaluate_capability_probe(
        report, body, tolerate_blocked_socket=tolerate_blocked_socket
    )


# Reporting -------------------------------------------------------------------


def emit_text(report: CheckReport) -> None:
    def status(cond: bool) -> str:
        return "OK  " if cond else "FAIL"

    def tri(state: str) -> str:
        """3-value status marker: pass / warn (unverified) / fail."""
        return {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[state]

    # When the probe target is unconfirmed, layers 1 / 3 / 3b describe a
    # binary that may not be the one org runs, so their markers read WARN
    # rather than FAIL - matching the verdicts, which were reclassified for
    # the same reason.
    unconfirmed = report.probe_target_unverified

    def dep(cond: bool) -> str:
        """Marker for a layer whose meaning depends on the probe target."""
        if cond:
            return "OK  "
        return "WARN" if unconfirmed else "FAIL"

    print("renga compatibility preflight")
    print("=" * 56)

    # Say which program was measured before saying anything about it: the
    # whole point of the resolution step is that "renga" is ambiguous.
    print(f"[----] mcp-peer binary: {report.mcp_peer_binary or '(unresolved)'}"
          f" (source: {report.mcp_peer_binary_source or 'n/a'})")
    if unconfirmed:
        print("         probe target UNVERIFIED: not resolved from "
              "`claude mcp list`; results below are about this binary only")

    print(f"[{dep(report.version_check_ok)}] mcp-peer version: "
          f"{report.renga_version or '(unknown)'} "
          f"(need >= {report.renga_min_required})")

    if report.mcp_registration_resolved is False:
        print("[WARN] renga-peers MCP registration: UNDETERMINED "
              "(`claude mcp list` did not answer)")
    else:
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
        # Count REQUIRED tools present, not tools returned: the check is a
        # subset test and the peer legitimately returns extras.
        required_present = len(REQUIRED_MCP_TOOLS) - len(
            report.mcp_tools_missing
        )
        print(f"[{dep(tools_ok)}] MCP tool surface "
              f"({required_present}/{len(REQUIRED_MCP_TOOLS)} "
              f"required tools present, {len(report.mcp_tools_found)} "
              "returned)")
        if report.mcp_tools_missing:
            print(f"         missing: {', '.join(report.mcp_tools_missing)}")

    # Layer 3b - reported separately from the static tool surface above.
    cap = report.capability_probe_status
    if cap is None or cap == "skipped":
        print("[SKIP] renga capability probe (server_info not run)")
        if report.capability_probe_reason:
            print(f"         reason: {report.capability_probe_reason}")
    else:
        state = {
            "connected": "ok" if not report.capabilities_missing else "fail",
            "detached": "warn",
            # `unreachable` is FAIL by default; it reads WARN only when
            # --tolerate-blocked-socket actually downgraded this run.
            "unreachable": (
                "warn" if report.live_readiness_unverified else "fail"
            ),
            "tool_absent": "fail",
            "call_error": "fail",
        }.get(cap, "fail")
        if state == "fail" and unconfirmed:
            state = "warn"
        detail = ""
        if cap == "connected" and report.server_pid is not None:
            detail = f" (pid {report.server_pid})"
        elif cap == "unreachable" and report.unreachable_endpoint_state:
            detail = f" (endpoint {report.unreachable_endpoint_state})"
        print(f"[{tri(state)}] renga capability probe: {cap}{detail}")
        if report.effective_capabilities is not None:
            print("         effective: "
                  f"{', '.join(report.effective_capabilities) or '(none)'}")
        else:
            print("         effective: (unknown - server was not asked; "
                  "this is not the same as none)")
        if report.capabilities_missing:
            print("         missing (required): "
                  f"{', '.join(report.capabilities_missing)}")
        if report.capabilities_observed_missing:
            print("         missing (observed only, not a failure): "
                  f"{', '.join(report.capabilities_observed_missing)}")
        if report.capability_probe_reason:
            # The upstream `reason` string is echoed verbatim and may contain
            # non-ASCII (renga uses an em-dash there). Safe because
            # _reconfigure_stdout() re-wraps stdout with errors="replace";
            # our own literals stay ASCII for cp932 consoles.
            print(f"         reason: {report.capability_probe_reason}")
        if report.client_version_reported:
            print("         mcp-peer client version: "
                  f"{report.client_version_reported} "
                  "(NOT the server's version)")

    if report.renga_path_candidates:
        print("         renga on PATH: "
              f"{'; '.join(report.renga_path_candidates)}")

    if report.warnings:
        print()
        print("Warnings:")
        for w in report.warnings:
            print(f"  - {w}")

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
    if not report.ok:
        print("Result: FAIL")
    else:
        unverified = []
        if report.live_readiness_unverified:
            unverified.append("live capability readiness")
        if report.probe_target_unverified:
            unverified.append("probe target")
        if unverified:
            print(f"Result: OK ({' and '.join(unverified)} UNVERIFIED)")
        else:
            print("Result: OK")


def emit_json(report: CheckReport) -> None:
    # Produce a stable-shape JSON doc; Dispatcher/Secretary can consume it.
    doc = {
        "ok": report.ok,
        "renga": {
            "version": report.renga_version,
            "version_tuple": report.renga_version_tuple,
            "min_required": report.renga_min_required,
            "path": report.renga_path,
            # Additive (Issue #823): PATH skew diagnostics + the mcp-peer's
            # self-reported version, which is NOT the server's version.
            "path_candidates": report.renga_path_candidates,
            "client_version_reported": report.client_version_reported,
            # Additive: which executable every layer was actually measured
            # against, and how it disagrees with the PATH-first one.
            "probed_binary": report.mcp_peer_binary,
            "probed_binary_source": report.mcp_peer_binary_source,
            "path_first": report.path_first_renga,
            "path_first_version": report.path_first_renga_version,
            "binary_version_skew": report.binary_version_skew,
            "version_check_ok": report.version_check_ok,
            # Additive: whether the probed binary was resolved from the
            # registration. False means every field above describes a PATH
            # fallback, and the layer verdicts were reclassified accordingly.
            "probe_target_confirmed": report.probe_target_confirmed,
            "probe_target_unverified": report.probe_target_unverified,
        },
        "mcp": {
            "registered": report.mcp_registered,
            # Tri-state companion to `registered`: False here means the
            # question could not be put at all, which is NOT `registered:
            # false`. Consumers MUST branch on this before reading the above.
            "registration_resolved": report.mcp_registration_resolved,
            "registration_line": report.mcp_registration_line,
            "tools_found": report.mcp_tools_found,
            "tools_missing": report.mcp_tools_missing,
            "tools_required": list(REQUIRED_MCP_TOOLS),
        },
        # Additive (Issue #823): live capability probe, reported separately
        # from the static tool surface above.
        "capabilities": {
            "probe_status": report.capability_probe_status,
            "reason": report.capability_probe_reason,
            "server": report.server_capabilities,
            "effective": report.effective_capabilities,
            "client": report.client_capabilities,
            "required": list(REQUIRED_CAPABILITIES),
            "observed": list(OBSERVED_CAPABILITIES),
            "missing": report.capabilities_missing,
            "observed_missing": report.capabilities_observed_missing,
            "server_pid": report.server_pid,
            "server_endpoint": report.server_endpoint,
            # Additive: why an `unreachable` was (or was not) downgraded.
            "unreachable_endpoint_state": report.unreachable_endpoint_state,
            "tolerate_blocked_socket": report.tolerate_blocked_socket,
            "live_readiness_unverified": report.live_readiness_unverified,
        },
        "failures": report.failures,
        "warnings": report.warnings,
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


def run_checks(
    report: CheckReport,
    *,
    skip_mcp_probe: bool = False,
    skip_capability_probe: bool = False,
    require_live: bool = False,
    tolerate_blocked_socket: bool = False,
) -> None:
    """Run every layer in order and fill in `report`.

    Separated from `main` so the ordering (which is load-bearing - layer 2
    resolves the binary the rest measure) is exercised directly by tests
    rather than re-implemented by them.
    """
    # Recorded up front so the JSON report always states whether the escape
    # hatch was armed, not only on the runs where it happened to fire.
    report.tolerate_blocked_socket = tolerate_blocked_socket

    # Layer 2 runs first: its registration row names the executable Claude
    # Code launches, and every later layer must measure THAT one.
    check_mcp_registration(report)
    binary = resolve_probe_binary(report)

    # Everything from here on is a statement about `binary`. If that is a PATH
    # fallback rather than a resolved registration, the statements may be
    # about a program org never launches, so their verdicts are captured and
    # reclassified below rather than being reported as confirmed failures.
    ok_before_dependent_layers = report.ok
    failures_before_dependent_layers = len(report.failures)

    check_renga_version(report, binary)
    check_binary_skew(report)

    if skip_mcp_probe:
        report.mcp_tools_probe_skipped = True
    else:
        check_mcp_tool_surface(report, binary)

    if skip_capability_probe or report.mcp_tools_probe_skipped:
        report.capability_probe_status = "skipped"
        report.capability_probe_reason = (
            "--skip-capability-probe" if skip_capability_probe
            else "--skip-mcp-probe (tool surface unknown)"
        )
    else:
        check_capability_surface(
            report, binary,
            tolerate_blocked_socket=tolerate_blocked_socket,
        )

    _reclassify_unconfirmed_target(
        report,
        ok_before=ok_before_dependent_layers,
        failures_before=failures_before_dependent_layers,
    )

    # A skipped probe verifies nothing, so under --require-live it must count
    # as unverified. Without this the gate below only saw `detached`, and the
    # flag combination exited 0 with `Result: OK` while no live check had run
    # at all - a fail-OPEN in the one flag whose entire job is to be strict.
    # Scoped to --require-live on purpose: plain --skip-mcp-probe /
    # --skip-capability-probe callers keep exiting 0 as before.
    if require_live and report.capability_probe_status in (None, "skipped"):
        report.live_readiness_unverified = True

    if require_live and (
        report.live_readiness_unverified or report.probe_target_unverified
    ):
        report.ok = False
        report.failures.append(
            "--require-live was given but live capability readiness is "
            "unverified (capability probe status "
            f"{report.capability_probe_status!r}). "
            + _require_live_remediation(report)
        )


def _reclassify_unconfirmed_target(
    report: CheckReport, *, ok_before: bool, failures_before: int
) -> None:
    """Downgrade layer 1 / 3 / 3b verdicts measured on an unconfirmed binary.

    Those layers are only ever statements about the executable they were
    handed. When the registration did not resolve, that executable is the
    PATH-first `renga`, which the module docstring documents as routinely a
    DIFFERENT program from the one Claude Code launches - the original false
    FAIL this tool was written to eliminate. Reporting their verdicts as
    confirmed failures would reintroduce it, and would do so in the one case
    where the corroborating version-skew comparison is also unavailable.

    So the failures they recorded become warnings and `ok` is restored to what
    it was before they ran. Layer 2's own verdict is untouched: a resolved
    `claude mcp list` that names no `renga-peers` row is a determinate FAIL
    and stays one.

    This is not fail-open. The run is marked `probe_target_unverified`, which
    `exit_code` maps to 2 - never 0 - so a caller that treats only 0 as
    success still refuses to proceed.
    """
    if report.probe_target_confirmed:
        return
    downgraded = report.failures[failures_before:]
    if not downgraded:
        report.probe_target_unverified = True
        return
    del report.failures[failures_before:]
    report.ok = bool(ok_before)
    report.probe_target_unverified = True
    for text in downgraded:
        report.warnings.append(
            "UNVERIFIED (probe target undetermined - this was measured on the "
            f"PATH fallback `{report.mcp_peer_binary}`, not on a binary read "
            "out of `claude mcp list`, so it is not established that org "
            "would ever run it): " + text
        )


def _require_live_remediation(report: CheckReport) -> str:
    """Remediation text for a failed `--require-live` gate.

    Branches on what actually left readiness unverified. The advice used to be
    unconditional ("cannot be combined with --skip-*"), which was wrong on
    every run that passed no skip flag at all - detached and unreachable
    reach this gate on their own.
    """
    status = report.capability_probe_status
    if report.probe_target_unverified and status not in (
        "detached", "unreachable"
    ):
        return (
            "The probe target itself is undetermined: `claude mcp list` did "
            "not resolve, so the live probe (if it ran at all) was aimed at "
            f"the PATH fallback `{report.mcp_peer_binary}` rather than at the "
            "binary Claude Code launches. Make `claude mcp list` answer - "
            "`renga mcp install --force` if the registration is missing - and "
            "re-run."
        )
    if status == "skipped":
        return (
            "The live probe did not run: --require-live cannot be combined "
            "with --skip-capability-probe / --skip-mcp-probe, which prevent "
            "it from running at all. Drop them and run this preflight from "
            "inside a renga pane attached to a 2.0-series server."
        )
    if status == "detached":
        return (
            "`server_info` reports detached: this preflight was not launched "
            "from inside a renga pane, so it had no endpoint and asked no "
            "server. No skip flag is involved. Re-run it from INSIDE a renga "
            "pane attached to a 2.0-series server (in a renga session, run it "
            "from a pane renga started, so RENGA_PANE_ID is set)."
        )
    if status == "unreachable":
        endpoint = report.server_endpoint or "(no endpoint reported)"
        return (
            "`server_info` reports unreachable: an endpoint was identified "
            f"({endpoint}) and could not be reached. No skip flag is "
            "involved. Make sure a 2.0-series renga server is running and "
            "that its socket is reachable from here; if this preflight runs "
            "inside a sandbox that denies unix-socket connect, re-run it "
            "outside the sandbox. --tolerate-blocked-socket downgrades that "
            "one case to unverified, which --require-live still rejects by "
            "design."
        )
    return (
        "Live capability readiness was not established. Run this preflight "
        "from inside a renga pane attached to a 2.0-series server."
    )


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
             "implemented - must not mutate the user's live renga layout",
    )
    p.add_argument(
        "--skip-mcp-probe", action="store_true",
        help="skip the mcp-peer tool-surface probe (static checks only)",
    )
    p.add_argument(
        "--skip-capability-probe", action="store_true",
        help="skip the live `server_info` capability probe (layer 3b); "
             "static tool-surface checks still run",
    )
    p.add_argument(
        "--tolerate-blocked-socket", action="store_true",
        help="opt-in: when the live probe reports 'unreachable' but the "
             "server socket path it names still EXISTS, report exit 2 "
             "(live readiness unverified) instead of exit 1 (FAIL). This is "
             "the sandbox case: a sandbox that blocks unix-socket connect "
             "makes a healthy server look unreachable, so the same command "
             "FAILs inside the sandbox and passes outside it. Off by default "
             "(fail-closed), and even when on, a socket path that does NOT "
             "exist stays a hard FAIL - that is a genuinely absent server",
    )
    p.add_argument(
        "--require-live", action="store_true",
        help="treat anything unverified (server_info status 'detached' or "
             "'unreachable', a skipped probe, or an unresolved probe target) "
             "as a failure instead of exit code 2; for CI callers that want "
             "the strict reading. In particular it cannot be satisfied "
             "together with --skip-capability-probe / --skip-mcp-probe: "
             "skipping the live probe leaves readiness unverified, so the "
             "combination fails rather than passing vacuously. The failure "
             "message names whichever of these actually applied",
    )
    args = p.parse_args(argv)

    report = CheckReport()
    run_checks(
        report,
        skip_mcp_probe=args.skip_mcp_probe,
        skip_capability_probe=args.skip_capability_probe,
        require_live=args.require_live,
        tolerate_blocked_socket=args.tolerate_blocked_socket,
    )

    if args.e2e:
        report.recommendations.append(
            "--e2e mode is reserved; pane spawn/close smoke not yet "
            "implemented in v1 (would mutate live layout)"
        )

    if args.json:
        emit_json(report)
    else:
        emit_text(report)

    return exit_code(report)


def exit_code(report: CheckReport) -> int:
    """Map a report to a process exit code.

    Exit 2 ("no required check failed, but something is unverified") was
    documented from the start but was unreachable until layer 3b existed;
    `detached` is its first occupant, an undetermined probe target its second.
    Both are deliberately NOT exit 0: "I could not find out" must not be
    reported to a caller as "I found out it is fine".
    """
    if not report.ok:
        return 1
    if report.live_readiness_unverified or report.probe_target_unverified:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
