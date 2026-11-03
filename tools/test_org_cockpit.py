"""Acceptance tests for tools/org_cockpit.py (opening the tmux cockpit).

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
    assert argv == RT + ["org", "cockpit", "--attach-only"]


def test_plan_open_wsl_with_wt_opens_new_tab():
    m = _load()
    env = {"WSL_DISTRO_NAME": "Ubuntu", "TMUX": "/tmp/tmux-1000/default,1,0"}
    kind, argv = m.plan_open("broker", "tmux", env, _which("wt.exe"), "/ja", RT)
    assert kind == "wt"
    assert argv[:4] == ["/bin/wt.exe", "-w", "0", "nt"]
    assert argv[argv.index("-d") + 1] == "Ubuntu"
    assert argv[argv.index("--cd") + 1] == "/ja"
    assert argv[-4:] == RT + ["org", "cockpit", "--attach-only"]


def test_plan_open_prints_otherwise():
    m = _load()
    kind, argv = m.plan_open("broker", "tmux", {}, _which(), "/ja", RT)
    assert kind == "print"
    assert argv == RT + ["org", "cockpit", "--attach-only"]
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
    assert "claude-org-runtime org cockpit --attach-only" in out.getvalue()
    assert not any("wt.exe" in a[0] for a in calls)


# --- wiring ------------------------------------------------------------------
def test_org_start_opens_the_cockpit():
    text = (ROOT / ".claude/skills/org-start/SKILL.md").read_text(encoding="utf-8")
    assert "tools/org_cockpit.py open" in text
