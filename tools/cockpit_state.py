#!/usr/bin/env python3
"""Mark the cockpit home tab (secretary) as waiting for a decision.

The cockpit's second status line lists windows whose ``@state`` window option
is waiting|needs-decision|done under "Needs you", but the broker never writes
``@state`` for the secretary's logical pane. This module does, on the ja side:

* ``mark_for_event``: journal_append calls it after recording a
  ``notify_sent kind=awaiting_user`` row -> home window ``@state=needs-decision``.
* ``mark_for_tool``: the secretary's PostToolUse(Bash) hook
  (``.hooks/mark-needs-decision.sh``) calls it. Needed because the secretary runs
  journal_append in the sandbox, where the call above cannot reach tmux.
* ``clear_for_prompt``: the secretary's UserPromptSubmit hook
  (``.hooks/clear-needs-decision.sh``) calls it -> the marker is removed, but only
  when it is ``needs-decision`` and only from the home pane itself.

Broker + tmux only. Everything else (renga, no tmux, no server, no home pane,
any error) is a silent no-op returning False: recording an event must never fail
because of a status marker.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MARK = "needs-decision"
# Not "waiting on a person": the answer already arrived / this is the watcher's own failure notice.
NOT_WAITING_GATES = ("escalation_reply_forward", "auto_handover_failed")
# Peer messages and background notices are not the user typing (same filter as premise_check).
_MACHINE_PROMPTS = ("<channel source=", "<task-notification>")


def _default_adapter():
    from claude_org_runtime.terminal.tmux import TmuxAdapter  # default socket claude-org-broker
    return TmuxAdapter()


def _is_broker(env) -> bool:
    from tools.transport import resolve
    return resolve(env=env) == "broker"


def mark_needs_decision(adapter=None, env=None) -> bool:
    try:
        env = os.environ if env is None else env
        if not _is_broker(env):
            return False
        adapter = adapter or _default_adapter()
        pane = adapter.secretary_pane_id()
        if not pane:
            return False
        adapter.set_agent_options(pane, state=MARK)
        return True
    except Exception:  # noqa: BLE001 - best effort, see module docstring
        return False


def mark_for_event(event, payload, adapter=None, env=None) -> bool:
    if event != "notify_sent" or not isinstance(payload, dict):
        return False
    if payload.get("kind") != "awaiting_user" or payload.get("gate") in NOT_WAITING_GATES:
        return False
    return mark_needs_decision(adapter=adapter, env=env)


_GATE = re.compile(r"""\bgate=["']?([^\s"']+)""")


def mark_for_tool(stdin_text, adapter=None, env=None) -> bool:
    """PostToolUse(Bash) hook body. The secretary runs journal_append inside the
    Bash sandbox where tmux is unreachable, so the mark is applied here (hooks run
    outside it). Marks only for a ``notify_sent kind=awaiting_user`` command, from the home pane."""
    try:
        env = os.environ if env is None else env
        data = json.loads(stdin_text)
        cmd = data["tool_input"]["command"]
        if data.get("tool_name") != "Bash" or not isinstance(cmd, str):
            return False
        if "journal_append" not in cmd or "notify_sent" not in cmd or not re.search(r"\bkind=awaiting_user\b", cmd):
            return False
        gate = _GATE.search(cmd)
        if gate and gate.group(1) in NOT_WAITING_GATES:
            return False
        own = env.get("TMUX_PANE")
        if not own or not _is_broker(env):
            return False
        adapter = adapter or _default_adapter()
        if adapter.secretary_pane_id() != own:
            return False
        return mark_needs_decision(adapter=adapter, env=env)
    except Exception:  # noqa: BLE001
        return False


def clear_for_prompt(stdin_text, adapter=None, env=None) -> bool:
    try:
        env = os.environ if env is None else env
        prompt = json.loads(stdin_text).get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or any(m in prompt for m in _MACHINE_PROMPTS):
            return False
        own = env.get("TMUX_PANE")
        if not own or not _is_broker(env):
            return False
        adapter = adapter or _default_adapter()
        pane = adapter.secretary_pane_id()
        if not pane or pane != own:
            return False
        cur = adapter._tmux("show-option", "-w", "-v", "-t", pane, "@state", check=False).stdout.strip()
        if cur != MARK:
            return False
        adapter._tmux("set-option", "-wu", "-t", pane, "@state")
        adapter._tmux("refresh-client", "-S", check=False)
        return True
    except Exception:  # noqa: BLE001
        return False


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["clear"]:
        clear_for_prompt(sys.stdin.read())
    elif argv[:1] == ["mark-tool"]:
        mark_for_tool(sys.stdin.read())
    elif argv[:1] == ["mark"]:
        mark_needs_decision()
    return 0


if __name__ == "__main__":
    sys.exit(main())
