"""Acceptance tests for tools/org_cockpit.py (secretary inside the tmux cockpit).

No tmux, wt.exe, network or HOME: every external call goes through fakes.
"""

import importlib.util
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "org_cockpit.py"
RT = ["/opt/rt/claude-org-runtime"]


def _load():
    assert TOOL.exists(), "tools/org_cockpit.py is missing"
    spec = importlib.util.spec_from_file_location("org_cockpit_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(m, text):
    return m.parse_panes(text)


# --- home pane selection -----------------------------------------------------
def test_unmarked_idle_shell_in_home_is_launched_with_kill():
    m = _load()
    rows = _rows(m, "%1\t1\t\t0\tzsh\n%7\t\t\t0\tclaude\n")
    assert m.pick_home_pane(rows) == ("launch_kill", "%1")


def test_live_marked_secretary_is_never_touched():
    m = _load()
    # A marked pane still in its login-shell phase is "running", not a shell to kill.
    for cmd in ("claude", "bash", "zsh"):
        rows = _rows(m, f"%1\t1\t1\t0\t{cmd}\n%2\t1\t\t0\tpython3\n")
        assert m.pick_home_pane(rows) == ("running", "%1")


def test_dead_marked_secretary_is_relaunched_without_kill():
    m = _load()
    rows = _rows(m, "%1\t1\t1\t1\tclaude\n")
    assert m.pick_home_pane(rows) == ("relaunch", "%1")


def test_dead_unmarked_home_is_launched_without_kill():
    m = _load()
    rows = _rows(m, "%1\t1\t\t1\tzsh\n")
    assert m.pick_home_pane(rows) == ("launch", "%1")


def test_peek_pane_and_agent_windows_are_not_candidates():
    m = _load()
    rows = _rows(m, "%3\t1\t\t0\tpython3\n%4\t\t\t0\tzsh\n")
    action, _ = m.pick_home_pane(rows)
    assert action == "error"


def test_two_marked_panes_is_an_error():
    m = _load()
    rows = _rows(m, "%1\t1\t1\t0\tclaude\n%2\t1\t1\t0\tclaude\n")
    action, _ = m.pick_home_pane(rows)
    assert action == "error"


# --- secretary command -------------------------------------------------------
def test_relaunch_with_running_daemon_adopts_instead_of_up():
    m = _load()
    cmd = m.secretary_command("relaunch", RT, ["--model", "opus"], daemon_running=True)
    assert cmd == RT + ["org", "adopt", "--continue"]


def test_first_launch_runs_org_up_with_args():
    m = _load()
    for action in ("launch", "launch_kill"):
        cmd = m.secretary_command(action, RT, ["--model", "opus"], daemon_running=True)
        assert cmd == RT + ["org", "up", "--model", "opus"]
    cmd = m.secretary_command("relaunch", RT, [], daemon_running=False)
    assert cmd == RT + ["org", "up"]


def test_respawn_argv_shape():
    m = _load()
    env = {"PATH": "/x/bin", "FOO": "bar baz", "TMUX": "/s,1,0", "TMUX_PANE": "%9"}
    argv = m.respawn_argv("%1", "/ja", env, RT + ["org", "up"], kill=True, tmux="tmux")
    assert argv[:4] == ["tmux", "-L", "claude-org-broker", "respawn-pane"]
    assert "-k" in argv
    assert argv[argv.index("-t") + 1] == "%1"
    assert argv[argv.index("-c") + 1] == "/ja"
    assert "PATH=/x/bin" in argv and "FOO=bar baz" in argv
    assert not any(a.startswith(("TMUX=", "TMUX_PANE=")) for a in argv)
    assert argv[-3:] == RT + ["org", "up"]
    argv = m.respawn_argv("%1", "/ja", env, RT + ["org", "up"], kill=False, tmux="tmux")
    assert "-k" not in argv


# --- open (end of /org-start) ------------------------------------------------
def _which(*present):
    return lambda name: f"/bin/{name}" if name in present else None


def test_plan_open_skips_renga_and_non_tmux_backends():
    m = _load()
    assert m.plan_open("renga", "tmux", {}, _which("wt.exe"), "/ja", RT)[0] == "skip"
    assert m.plan_open("broker", "herdr", {}, _which("wt.exe"), "/ja", RT)[0] == "skip"
    assert m.plan_open("broker", None, {}, _which("wt.exe"), "/ja", RT)[0] == "skip"


def test_plan_open_inside_broker_tmux():
    m = _load()
    env = {"TMUX": "/tmp/tmux-1000/claude-org-broker,123,0", "WSL_DISTRO_NAME": "U"}
    kind, argv = m.plan_open("broker", "tmux", env, _which("wt.exe"), "/ja", RT)
    assert kind == "inside"
    assert argv == RT + ["org", "cockpit"]


def test_plan_open_wsl_with_wt_opens_new_tab():
    m = _load()
    env = {"WSL_DISTRO_NAME": "Ubuntu", "TMUX": "/tmp/tmux-1000/default,1,0"}
    kind, argv = m.plan_open("broker", "tmux", env, _which("wt.exe"), "/ja", RT)
    assert kind == "wt"
    assert argv[:4] == ["/bin/wt.exe", "-w", "0", "nt"]
    assert argv[argv.index("-d") + 1] == "Ubuntu"
    assert argv[argv.index("--cd") + 1] == "/ja"
    assert argv[-3:] == RT + ["org", "cockpit"]


def test_plan_open_prints_otherwise():
    m = _load()
    kind, argv = m.plan_open("broker", "tmux", {}, _which(), "/ja", RT)
    assert kind == "print"
    assert argv == RT + ["org", "cockpit"]
    kind, _ = m.plan_open("broker", "tmux", {"WSL_DISTRO_NAME": "U"}, _which(), "/ja", RT)
    assert kind == "print"


def test_open_never_blocks_and_prints_attach_command_on_failure():
    m = _load()
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        raise OSError("tmux not found")

    out = io.StringIO()
    rc = m.open_cockpit("/ja", {"WSL_DISTRO_NAME": "U"}, run=run, which=_which("wt.exe"),
                        transport="broker", adapter="tmux", runtime=RT, out=out)
    assert rc == 0
    assert "claude-org-runtime org cockpit" in out.getvalue()
    assert not any("wt.exe" in a[0] for a in calls)


# --- floor + wiring ----------------------------------------------------------
def test_runtime_floor_is_0_1_52_everywhere():
    for rel, needle in (
        ("pyproject.toml", "claude-org-runtime>=0.1.52,<0.2"),
        ("requirements.txt", "claude-org-runtime>=0.1.52,<0.2"),
        ("docker/Dockerfile", "ARG RUNTIME_VERSION=0.1.52"),
        ("docker/compose.yaml", "${RUNTIME_VERSION:-0.1.52}"),
        ("docker/README.md", "-r0.1.52"),
    ):
        assert needle in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_org_start_opens_the_cockpit():
    text = (ROOT / ".claude/skills/org-start/SKILL.md").read_text(encoding="utf-8")
    assert "tools/org_cockpit.py open" in text
