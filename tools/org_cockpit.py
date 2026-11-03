#!/usr/bin/env python3
"""Open the broker tmux cockpit at the end of /org-start.

broker + tmux backend only (the cockpit is runtime's ``org cockpit``, session
``org`` on socket ``claude-org-broker``). Starting and reconnecting the
secretary is runtime's ``claude-org-runtime org cockpit`` (0.1.53+); this tool
only opens a view and never starts anything (``--attach-only``).

  python3 tools/org_cockpit.py open
      Open the cockpit in a new Windows Terminal tab (WSL + wt.exe), switch to
      it when already inside the broker tmux, or print the attach command.
      Always exits 0.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Fixed: runtime's cockpit and tmux adapter do not read an override.
SOCKET = "claude-org-broker"
SESSION = "org"


# ------------------------------------------------------------ pure decisions
def plan_open(transport, adapter, env: dict, which, root: str,
              runtime: list[str]) -> tuple[str, list[str]]:
    attach = runtime + ["org", "cockpit", "--attach-only"]
    if transport != "broker" or adapter != "tmux":
        return "skip", []
    tmux_env = env.get("TMUX", "")
    if tmux_env and os.path.basename(tmux_env.split(",")[0]) == SOCKET:
        return "inside", attach  # runtime switch-clients to the cockpit
    distro, wt = env.get("WSL_DISTRO_NAME"), which("wt.exe")
    if distro and wt:
        return "wt", [wt, "-w", "0", "nt", "--title", SESSION, "wsl.exe",
                      "-d", distro, "--cd", root, "-e", *attach]
    return "print", attach


# ------------------------------------------------------------- side effects
def runtime_argv() -> list[str]:
    exe = shutil.which("claude-org-runtime")
    return [exe] if exe else [sys.executable, "-m", "claude_org_runtime.cli"]


def _transport_and_adapter():
    sys.path.insert(0, str(ROOT))
    from tools.self_edit_approval import _broker_adapter, _resolve_transport
    return _resolve_transport(None), _broker_adapter(None)


def open_cockpit(root: str, env: dict, *, run=subprocess.run, which=shutil.which,
                 transport=None, adapter=None, runtime=None, out=sys.stdout) -> int:
    runtime = runtime or runtime_argv()
    attach = shlex.join(runtime + ["org", "cockpit", "--attach-only"])
    try:
        if transport is None:
            transport, adapter = _transport_and_adapter()
        kind, argv = plan_open(transport, adapter, env, which, root, runtime)
        if kind == "skip":
            print(f"cockpit: skipped (transport={transport}, adapter={adapter})", file=out)
            return 0
        # Link every agent window now, so the cockpit is complete when it opens.
        run(runtime + ["org", "cockpit", "--no-attach", "--attach-only"], cwd=root, check=True,
            timeout=20, capture_output=True)
        if kind == "print":
            print(f"cockpit: attach with: {attach}", file=out)
            return 0
        run(argv, cwd=root, check=True, timeout=20, capture_output=True)
        print("cockpit: opened in a new Windows Terminal tab" if kind == "wt"
              else "cockpit: switched to the cockpit", file=out)
    except Exception as e:  # noqa: BLE001 - never block /org-start
        print(f"cockpit: could not open ({e}); attach with: {attach}", file=out)
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "open":
        return open_cockpit(str(ROOT), dict(os.environ))
    print(f"unknown command: {cmd} (use open)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
