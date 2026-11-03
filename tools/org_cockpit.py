#!/usr/bin/env python3
"""Run the secretary inside the broker tmux cockpit, and open the cockpit.

broker + tmux backend only (the cockpit is runtime's ``org cockpit``, session
``org`` on socket ``claude-org-broker``). renga and the wezterm / herdr
backends are untouched.

  python3 tools/org_cockpit.py up [-- <org up args>]
      Start the secretary in the cockpit's first window ("home") with the
      same ``claude-org-runtime org up`` as today (identical MCP / channel
      wiring), then attach. A dead secretary is restarted with
      ``org adopt --continue`` while the daemon is still running.
  python3 tools/org_cockpit.py open
      End of /org-start: open the cockpit in a new Windows Terminal tab
      (WSL + wt.exe), switch to it when already inside the broker tmux, or
      print the attach command. Always exits 0.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Fixed: runtime's cockpit and tmux adapter do not read an override (0.1.52).
SOCKET = "claude-org-broker"
SESSION = "org"
MARK = "@org_secretary"
SHELLS = frozenset({"bash", "zsh", "sh", "dash", "fish", "ksh", "tcsh"})
PANE_FORMAT = "#{pane_id}\t#{@cockpit_home}\t#{" + MARK + "}\t#{pane_dead}\t#{pane_current_command}"


# ------------------------------------------------------------ pure decisions
def parse_panes(text: str) -> list[tuple[str, bool, bool, bool, str]]:
    """``list-panes`` rows -> (pane_id, in_home, marked, dead, command)."""
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        pid, home, mark, dead, cmd = parts
        rows.append((pid, home == "1", mark == "1", dead == "1", cmd))
    return rows


def pick_home_pane(rows) -> tuple[str, str | None]:
    """Which home pane hosts the secretary, and what to do with it.

    running     marked pane alive (also during its login-shell phase): leave it
    relaunch    marked pane dead: respawn without -k
    launch      unmarked dead home pane: respawn without -k
    launch_kill unmarked idle shell in home: respawn -k (kills only that shell)
    error       ambiguous or nothing usable: touch nothing
    """
    marked = [r for r in rows if r[2]]
    if len(marked) > 1:
        return "error", "more than one pane carries " + MARK
    if marked:
        pid, _, _, dead, _ = marked[0]
        return ("relaunch" if dead else "running"), pid
    home = [r for r in rows if r[1]]
    for pid, _, _, dead, cmd in home:
        if dead:
            return "launch", pid
    for pid, _, _, dead, cmd in home:
        if cmd in SHELLS:
            return "launch_kill", pid
    return "error", "no idle shell in the cockpit home window"


def secretary_command(action: str, runtime: list[str], up_args: list[str],
                      daemon_running: bool) -> list[str]:
    # org up on a daemon that still has the secretary registered exits with
    # "already up" and launches nothing, so a restart must adopt instead.
    if action == "relaunch" and daemon_running:
        return runtime + ["org", "adopt", "--continue"]
    return runtime + ["org", "up", *up_args]


def respawn_argv(pane: str, root: str, env: dict, command: list[str], *,
                 kill: bool, tmux: str) -> list[str]:
    # The tmux server's env belongs to whoever started it; pass the caller's
    # env so the secretary sees what a plain `org up` in this shell would.
    argv = [tmux, "-L", SOCKET, "respawn-pane"] + (["-k"] if kill else [])
    argv += ["-t", pane, "-c", root]
    for k, v in env.items():
        if k not in ("TMUX", "TMUX_PANE"):
            argv += ["-e", f"{k}={v}"]
    return argv + command


def plan_open(transport, adapter, env: dict, which, root: str,
              runtime: list[str]) -> tuple[str, list[str]]:
    attach = runtime + ["org", "cockpit"]
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
    attach = shlex.join(runtime + ["org", "cockpit"])
    try:
        if transport is None:
            transport, adapter = _transport_and_adapter()
        kind, argv = plan_open(transport, adapter, env, which, root, runtime)
        if kind == "skip":
            print(f"cockpit: skipped (transport={transport}, adapter={adapter})", file=out)
            return 0
        # Link every agent window now, so the cockpit is complete when it opens.
        run(runtime + ["org", "cockpit", "--no-attach"], cwd=root, check=True,
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


def _tmux(tmux: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([tmux, "-L", SOCKET, *args], capture_output=True, text=True)


def up(up_args: list[str], *, attach: bool) -> int:
    for i, a in enumerate(up_args):
        if a == "--backend" or a.startswith("--backend="):
            val = a.partition("=")[2] or "".join(up_args[i + 1:i + 2])
            if val != "tmux":
                print("org_cockpit up: the cockpit needs --backend tmux", file=sys.stderr)
                return 2
    _, adapter = _transport_and_adapter()
    # No daemon yet: org up picks the backend, honouring ORG_BACKEND.
    adapter = adapter or os.environ.get("ORG_BACKEND") or None
    if adapter not in (None, "tmux"):
        print(f"org_cockpit up: broker daemon backend is {adapter!r}, not tmux", file=sys.stderr)
        return 2
    tmux = shutil.which("tmux")
    if not tmux:
        print("org_cockpit up: tmux is not on PATH", file=sys.stderr)
        return 1
    rt = runtime_argv()
    root = str(ROOT)
    if subprocess.run(rt + ["org", "cockpit", "--no-attach"], cwd=root).returncode != 0:
        return 1
    lock_path = ROOT / ".state" / "org_cockpit.lock"
    lock_path.parent.mkdir(exist_ok=True)
    import fcntl  # POSIX only; `open` must still run on Windows
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # serialize inspect + claim
        rows = parse_panes(_tmux(tmux, "list-panes", "-s", "-t", f"={SESSION}",
                                 "-F", PANE_FORMAT).stdout)
        action, pane = pick_home_pane(rows)
        if action == "error":
            print(f"org_cockpit up: {pane}; nothing started", file=sys.stderr)
            return 1
        if action == "running":
            print(f"org_cockpit up: the secretary is already running in {pane}")
        else:
            daemon_running = action == "relaunch" and subprocess.run(
                rt + ["org", "adopt", "--status"], cwd=root, capture_output=True,
            ).returncode == 0
            cmd = secretary_command(action, rt, up_args, daemon_running)
            # Mark before respawning: a marked pane is never killed later.
            _tmux(tmux, "set-option", "-p", "-t", pane, MARK, "1")
            res = subprocess.run(respawn_argv(pane, root, dict(os.environ), cmd,
                                              kill=action == "launch_kill", tmux=tmux))
            if res.returncode != 0:
                return 1
            print(f"org_cockpit up: started `{shlex.join(cmd[len(rt):])}` in {pane}")
    if not attach:
        return 0
    os.execvp(rt[0], rt + ["org", "cockpit"])
    return 0  # pragma: no cover


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "open":
        return open_cockpit(str(ROOT), dict(os.environ))
    if cmd == "up":
        attach = "--no-attach" not in rest
        rest = [a for a in rest if a != "--no-attach"]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        return up(rest, attach=attach)
    print(f"unknown command: {cmd} (use up / open)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
