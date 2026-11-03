"""Acceptance: one cockpit command (runtime 0.1.53); ja only opens, never starts.

No tmux, wt.exe, network or HOME: every external call goes through fakes.
"""

import importlib.util
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "org_cockpit.py"
RT = ["/opt/rt/claude-org-runtime"]


def _load():
    spec = importlib.util.spec_from_file_location("org_cockpit_attach_only", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _which(*present):
    return lambda name: f"/bin/{name}" if name in present else None


def test_up_is_gone():
    m = _load()
    for name in ("up", "pick_home_pane", "secretary_command", "respawn_argv", "parse_panes"):
        assert not hasattr(m, name), name
    assert m.main(["up"]) == 2


def test_open_prelink_is_no_attach_attach_only():
    m = _load()
    calls = []
    out = io.StringIO()
    rc = m.open_cockpit("/ja", {}, run=lambda argv, **kw: calls.append(argv),
                        which=_which(), transport="broker", adapter="tmux",
                        runtime=RT, out=out)
    assert rc == 0
    assert calls == [RT + ["org", "cockpit", "--no-attach", "--attach-only"]]
    assert "org cockpit --attach-only" in out.getvalue()


def test_plan_open_attach_commands_are_attach_only():
    m = _load()
    want = RT + ["org", "cockpit", "--attach-only"]
    env = {"TMUX": "/tmp/tmux-1000/claude-org-broker,1,0"}
    assert m.plan_open("broker", "tmux", env, _which(), "/ja", RT) == ("inside", want)
    assert m.plan_open("broker", "tmux", {}, _which(), "/ja", RT) == ("print", want)
    kind, argv = m.plan_open("broker", "tmux", {"WSL_DISTRO_NAME": "U"},
                             _which("wt.exe"), "/ja", RT)
    assert kind == "wt" and argv[-4:] == want[-4:]


def test_runtime_floor_is_0_1_53_everywhere():
    for rel, needle in (
        ("pyproject.toml", "claude-org-runtime>=0.1.53,<0.2"),
        ("requirements.txt", "claude-org-runtime>=0.1.53,<0.2"),
        ("docker/Dockerfile", "ARG RUNTIME_VERSION=0.1.53"),
        ("docker/compose.yaml", "${RUNTIME_VERSION:-0.1.53}"),
        ("docker/README.md", "-r0.1.53"),
    ):
        assert needle in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_docs_name_one_command_and_no_up():
    for rel in (".claude/skills/org-start/SKILL.md", "README.md",
                "docs/getting-started.md", "docs/operations/dispatcher-view.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "org_cockpit.py up" not in text, rel
        assert "claude-org-runtime org cockpit" in text, rel
