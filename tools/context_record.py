#!/usr/bin/env python3
"""Record a role session's turn boundaries for the context-handover watcher.

Run by the ``SessionStart`` / ``UserPromptSubmit`` / ``Stop`` /
``PreCompact`` hooks, and for the secretary also ``Notification``
(``idle_prompt``, design §4.2 1), (``.hooks/context-record.sh``) with the hook's JSON
on stdin. It keeps one small file per role, ``.state/context/<role>.json``,
which ``tools/context_handover_watch.py`` reads to decide when the role is
between turns and where its transcript is
(``docs/design/context-auto-handover.md`` §3.1).

Claude Code hands hooks no context-usage figure, so this records only
*where* the transcript is; the watcher reads the token count from the
transcript itself (§2.2).

The role comes from the hook's ``cwd``: a session under ``.dispatcher/``
is the dispatcher, a session at the org root the secretary, and anything
else (the on-demand curator under ``.curator/``, a worker worktree that
picks up the root settings) is not recorded. The repo-root settings are
read by those sessions too, so without this their hooks would overwrite
the secretary's record and the watcher would type into their pane.
The cwd follows the session's shell, so a session whose shell moved into
the other role's directory is held to its own record: an event whose
``session_id`` is not the role record's session is dropped, and a
``/clear`` (``SessionStart`` with ``source=clear``) is accepted only from
the pane already recorded. ``idle_prompt`` goes to its own file
(``<role>.idle.json``) because the Notification hook can run at the same
moment as a ``UserPromptSubmit`` and a read-modify-write of the main
record could undo the turn it opened.

Turn accounting: ``UserPromptSubmit`` opens a turn (``turn_open =
turn_closed + 1``) and ``Stop`` closes it (``turn_closed += 1``, then
``turn_open = turn_closed``). Turns started by a channel message or by a
``/loop`` cron fire ``UserPromptSubmit`` too (checked on a live pane,
design §2.1). Tying ``turn_open`` to ``turn_closed`` rather than counting
prompts keeps a prompt absorbed mid-turn from leaving the two counters
apart for good. ``SessionStart`` also closes any turn the previous
session left open.

Always exits 0 and prints nothing: a recording failure must never block
the session. The watcher treats a stale or inconsistent record as
"unknown" and does not hand over (§3.1).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

THRESHOLD_ENV = "CLAUDE_ORG_HANDOVER_THRESHOLD_TOKENS"
WINDOW_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
THRESHOLD_RATIO = 0.6
# The watcher's resume line, typed or expanded into <command-name>/<command-args>.
RESUME_RE = re.compile(r"/(?:secretary|dispatcher)-resume\b.*?\bop=([A-Za-z0-9_-]+)", re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def role_for(cwd: str, root: Path = REPO_ROOT) -> "str | None":
    try:
        rel = Path(cwd).resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if rel.parts[:1] == (".dispatcher",):
        return "dispatcher"
    return "secretary" if not rel.parts else None


def context_dir() -> Path:
    from tools.state_db.discover import resolve_state_db_path

    return Path(resolve_state_db_path(None)).parent / "context"


def pane_from_env(env) -> "dict | None":
    if env.get("RENGA_PANE_ID"):
        return {"backend": "renga", "id": env["RENGA_PANE_ID"]}
    if env.get("TMUX_PANE"):
        return {"backend": "tmux", "id": env["TMUX_PANE"]}
    return None


def threshold_from_env(env) -> "int | None":
    """Token threshold from the env, or None when neither var is set (no assumed window)."""
    for name, ratio in ((THRESHOLD_ENV, 1.0), (WINDOW_ENV, THRESHOLD_RATIO)):
        try:
            value = int(env.get(name, ""))
        except ValueError:
            continue
        if value > 0:
            return int(value * ratio)
    return None


def foreign(rec: dict, payload: dict, env) -> bool:
    """The event comes from another session than the one this record follows."""
    if payload.get("hook_event_name") == "SessionStart":
        if payload.get("source") == "compact":  # a compaction keeps the session
            return bool(rec.get("session_id")) and payload.get("session_id") != rec.get("session_id")
        return (payload.get("source") == "clear" and bool(rec.get("pane"))
                and pane_from_env(env) != rec.get("pane"))
    return bool(rec.get("session_id")) and payload.get("session_id") != rec.get("session_id")


def update(rec: dict, payload: dict, env, now: str) -> dict:
    """Return the record after applying one hook event (pure)."""
    rec = dict(rec)
    event = payload.get("hook_event_name")
    rec.setdefault("turn_open", 0)
    rec.setdefault("turn_closed", 0)
    if event == "SessionStart":
        rec["session_id"] = payload.get("session_id")
        rec["transcript_path"] = payload.get("transcript_path")
        rec["source"] = payload.get("source")
        rec["started_at"] = now
        rec["pane"] = pane_from_env(env)
        threshold = threshold_from_env(env)
        if threshold is None:
            rec.pop("threshold_tokens", None)
        else:
            rec["threshold_tokens"] = threshold
        if payload.get("source") == "clear":
            rec["cleared_at"] = now
        rec["turn_open"] = rec["turn_closed"]
    elif event == "UserPromptSubmit":
        rec["turn_open"] = rec["turn_closed"] + 1
        rec["prompt_at"] = now
        match = RESUME_RE.search(payload.get("prompt") or "")
        if match:
            # Lets the watcher tell its resume turn from a channel turn (§4.5).
            rec["resume_op"] = match.group(1)
            rec["resume_turn"] = rec["turn_open"]
    elif event == "Stop":
        rec["turn_closed"] += 1
        rec["turn_open"] = rec["turn_closed"]
        rec["stop_session_id"] = payload.get("session_id")
        rec["stop_at"] = now
        rec["background_tasks"] = payload.get("background_tasks") or []
        rec["session_crons"] = payload.get("session_crons") or []
    return rec


def write_atomic(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def record_precompact(role: str, payload: dict) -> None:
    """§2.2: a compaction started before the handover could run. Never block it."""
    from tools.state_db import connect
    from tools.state_db.discover import resolve_state_db_path, verify_state_db_schema
    from tools.state_db.writer import StateWriter

    db_path = Path(resolve_state_db_path(None))
    verify_state_db_schema(db_path)
    conn = connect(db_path)
    try:
        writer = StateWriter(conn)
        writer.append_event(
            kind="context_compacted_before_handover",
            actor=role,
            payload={"role": role, "trigger": payload.get("trigger"),
                     "session_id": payload.get("session_id")},
        )
        writer.commit()
    finally:
        conn.close()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        role = role_for(payload.get("cwd") or os.getcwd())
        if role is None:
            return 0
        if payload.get("hook_event_name") == "PreCompact":
            record_precompact(role, payload)
            return 0
        if payload.get("hook_event_name") == "Notification":
            if payload.get("notification_type") == "idle_prompt":
                # About 60 s after a response nobody typed into (§4.2 1).
                write_atomic(context_dir() / f"{role}.idle.json",
                             {"idle_at": now_iso(), "idle_session_id": payload.get("session_id")})
            return 0
        path = context_dir() / f"{role}.json"
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict):
                rec = {}
        except (OSError, ValueError):
            rec = {}
        if foreign(rec, payload, os.environ):
            return 0
        write_atomic(path, update(rec, payload, os.environ, now_iso()))
    except Exception:  # noqa: BLE001 — advisory hook, never block the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
