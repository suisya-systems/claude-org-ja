#!/usr/bin/env python3
"""Hand the dispatcher's and the secretary's context over automatically, from outside.

``docs/design/context-auto-handover.md`` is the spec; section numbers below
refer to it. This is the "context-handover watcher" of §4.1: a resident,
deterministic process (no LLM) that every 30 s, for each role,

1. reads ``.state/context/<role>.json`` (written by the role's hooks,
   ``tools/context_record.py``) and the last ``usage`` in the transcript it
   points at (§2.2),
2. when the token count is over the threshold and the role is quiet
   (§3.2, plus §3.3 for the secretary and §3.4 for the dispatcher), types
   ``/<role>-handover auto op=OP`` -> ``/clear`` -> ``/<role>-resume auto
   op=OP`` into its pane (§4.3), and
3. checks that nothing was lost across the switch (§6.1 / §6.2).

Only one role is handed over at a time. Workers are out of scope (§5).
The secretary is handled on renga only: on broker its ``/secretary-resume``
cannot confirm its own pane id (§7). Because a person types into the
secretary's pane, the secretary is also handed over only after Claude Code
reported ``idle_prompt`` for the current turn (the person has not typed for
about a minute, §4.2 1), and only while an attention watcher is running,
since a failure has no other way to reach a person who is away (§4.3).

Keystrokes go through the backends of ``tools/self_edit_approval.py``
(renga CLI, or tmux for broker): the composer must be empty before typing,
and the typed text is read back before Enter is sent; a mismatch stops
without Enter and without erasing anything (§4.2). Any other configuration
(broker on wezterm / herdr) disables the watcher at start-up (§4.1).

Every step is written to ``.state/context/handover-op.json`` before it is
taken, so a restarted watcher continues from where it stopped (§4.5). A
failure is recorded as an ``auto_handover_failed`` event, sent to the
secretary through ``tools/peer_notify.py`` (for the secretary's own failure
also as ``notify_sent kind=awaiting_user``, which the attention watcher
turns into a beep) and the role is not tried again for 30 minutes (§4.3).

Usage::

    python3 tools/context_handover_watch.py            # resident loop (foreground)
    python3 tools/context_handover_watch.py --daemon   # detach: no pane, log in .state/
    python3 tools/context_handover_watch.py --status   # is it alive? (sidecar vs process)
    python3 tools/context_handover_watch.py --stop     # stop the running watcher
    python3 tools/context_handover_watch.py --must-name secretary
                                                       # task_ids the handover must name (§6.2)

``--daemon`` must run outside the worker sandbox (a sandboxed shell cannot
leave a process behind, and the watcher needs the renga CLI / tmux socket).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DISPATCHER = "dispatcher"
SECRETARY = "secretary"
ROLES = (DISPATCHER, SECRETARY)
SCRIPT_IDENT = "context_handover_watch.py"
POLL_SEC = 30
STALE_STOP_SEC = 600          # §3.1: a Stop older than this is "unknown"
BACKOFF_SEC = 1800            # §4.3: a failed role is not tried for 30 min
AWAITING_HOLD_SEC = 1800      # §3.3: no switch within 30 min of asking a person
# ponytail: §6.2 checks awaiting_user task_ids of the last 24 h only; an older
# unanswered question relies on the skill copying it forward. Widen if that slips.
AWAITING_NAME_WINDOW_SEC = 86400
HANDOVER_TIMEOUT_SEC = 600    # §4.3 step 3
CLEAR_TIMEOUT_SEC = 15        # §4.3 step 5
LATE_SETTLE_SEC = 5           # §4.4: the old transcript is written asynchronously
RESUME_TIMEOUT_SEC = 60       # §4.3 step 7
HEARTBEAT_TIMEOUT_SEC = 540   # §4.3 step 7: three /loop 3m cycles
INSPECT_LINES = 40
TAIL_BYTES = 4 * 1024 * 1024
MONITOR_MARKERS = ("worker-monitoring.md", "監視ループ 1 サイクル")
LIVE_RUN_STATUSES = ("in_use", "review")
# notify_sent gates that are not "waiting on a person" (§3.3): the reply has
# already arrived, or the line is this watcher's own failure notice.
NOT_WAITING_GATES = ("escalation_reply_forward", "auto_handover_failed")
# Not started (disabled or already running). Distinct from 0 so the pane's
# `... && exit` keeps the message on screen for the secretary to read.
EXIT_NOT_STARTED = 3


class HandoverFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def now_iso(ts: "float | None" = None) -> str:
    dt = datetime.now(timezone.utc) if ts is None else datetime.fromtimestamp(ts, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value) -> "float | None":
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def last_usage_tokens(transcript: Path) -> "int | None":
    """Input-side tokens of the last main-thread response, or None (§2.2).

    Same sum as the status line's ``used_percentage``: input +
    cache_creation_input + cache_read_input (output tokens excluded).
    """
    try:
        size = transcript.stat().st_size
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        usage = (entry.get("message") or {}).get("usage")
        if isinstance(usage, dict):
            return sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return None


def is_monitoring_cron(cron) -> bool:
    prompt = cron.get("prompt") if isinstance(cron, dict) else None
    return isinstance(prompt, str) and all(m in prompt for m in MONITOR_MARKERS)


def not_quiet_reason(rec: dict, now: float) -> "str | None":
    """Why the record does not show a quiet session, or None (§3.1 / §3.2)."""
    if not rec.get("session_id") or rec.get("stop_session_id") != rec.get("session_id"):
        return "no_stop_in_current_session"
    if not rec.get("pane"):
        return "pane_unknown"
    if rec.get("turn_open") != rec.get("turn_closed"):
        return "turn_open"
    stop_at = parse_iso(rec.get("stop_at"))
    if stop_at is None or now - stop_at > STALE_STOP_SEC:
        return "stop_stale"
    if rec.get("background_tasks"):
        return "background_tasks"
    return None


def not_idle_reason(rec: dict) -> "str | None":
    """§4.2 1: ``idle_prompt`` came for this session after its last Stop."""
    idle_at = parse_iso(rec.get("idle_at"))
    if (rec.get("idle_session_id") != rec.get("session_id") or idle_at is None
            or idle_at <= (parse_iso(rec.get("stop_at")) or float("inf"))
            or idle_at <= (parse_iso(rec.get("prompt_at")) or 0)):
        return "not_idle_prompt"
    return None


def command_name(content: str) -> "str | None":
    start = content.find("<command-name>")
    end = content.find("</command-name>")
    if start < 0 or end < start:
        return None
    return content[start + len("<command-name>"):end].strip()


def extract_late_inputs(lines: "list[str]", op: str, role: str = DISPATCHER) -> "list[str]":
    """Inputs that reached the old session from offset p0 on (§4.4).

    A message lands in the transcript in one of three forms, depending on
    whether it started a turn: a ``user`` line carrying ``origin`` (channel
    push, human input), an ``attachment`` of type ``queued_command`` (absorbed
    mid-turn), and in both cases a ``queue-operation`` ``enqueue`` line
    written when it arrived. All three are read and de-duplicated by text,
    keeping arrival order. The watcher's own ``/<role>-handover`` and
    ``/clear`` are dropped.
    """
    own = f"/{role}-handover auto op={op}"
    out: "list[str]" = []
    seen: set = set()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = entry.get("type")
        text = None
        if kind == "queue-operation" and entry.get("operation") == "enqueue":
            text = entry.get("content")
        elif kind == "attachment":
            att = entry.get("attachment") or {}
            if att.get("type") == "queued_command":
                text = att.get("prompt")
        elif kind == "user" and entry.get("origin"):
            text = (entry.get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        name = command_name(text)
        if name == "/clear" or (name == f"/{role}-handover" and f"op={op}" in text):
            continue
        if text.strip() in (own, "/clear"):
            continue
        key = text.strip()
        if key not in seen:
            seen.add(key)
            out.append(text)
    return out


def render_late_inputs(op: str, inputs: "list[str]") -> str:
    parts = [f"# /clear の前後に届いた入力（op={op}）\n",
             "context-handover watcher が旧会話記録から抜き出した。届いた順。\n"]
    for i, text in enumerate(inputs, 1):
        parts.append(f"\n## {i}\n\n````\n{text.rstrip()}\n````\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------

class Org:
    """Paths and state the watcher reads; one place so tests can point it at a tmp dir."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.db_path = state_dir / "state.db"
        self.context_dir = state_dir / "context"
        self.op_path = self.context_dir / "handover-op.json"
        self.backoff_path = self.context_dir / "handover-backoff.json"
        self.unset_path = self.context_dir / "handover-threshold-unset.json"
        self.attention_path = state_dir / "attention_pane.json"
        self.pid_path = state_dir / "context_handover_watch.json"
        self.log_path = state_dir / "context_handover_watch.log"

    @classmethod
    def discover(cls) -> "Org":
        from tools.state_db.discover import resolve_state_db_path

        return cls(Path(resolve_state_db_path(None)).parent)

    def record_path(self, role: str) -> Path:
        return self.context_dir / f"{role}.json"

    def handover_path(self, role: str) -> Path:
        return self.state_dir / f"{role}-handover.md"

    def read_json(self, path: Path, default):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
        return data if isinstance(data, type(default)) else default

    def write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def record(self, role: str) -> dict:
        return self.read_json(self.record_path(role), {})

    def _query(self, sql: str, params=()) -> list:
        import sqlite3

        if not self.db_path.exists():
            raise HandoverFailed("state_db_missing")
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def runs(self, statuses) -> "dict[str, str]":
        marks = ",".join("?" * len(statuses))
        return dict(self._query(
            f"SELECT task_id, status FROM runs WHERE status IN ({marks})", tuple(statuses)))

    def run_exists(self, task_id: str) -> bool:
        return bool(self._query("SELECT 1 FROM runs WHERE task_id = ?", (task_id,)))

    def resumed_event(self, role: str, op: str) -> "dict | None":
        rows = self._query(
            "SELECT payload_json FROM events WHERE kind = ? "
            "AND json_extract(payload_json, '$.op') = ? ORDER BY id DESC LIMIT 1",
            (f"{role}_resumed", op))
        return json.loads(rows[0][0]) if rows else None

    def awaiting_user(self, since: float) -> "list[tuple[float, str]]":
        """``notify_sent kind=awaiting_user`` rows after ``since``: (time, task_id) (§3.3)."""
        marks = ",".join("?" * len(NOT_WAITING_GATES))
        rows = self._query(
            "SELECT occurred_at, json_extract(payload_json, '$.task_id') FROM events "
            "WHERE kind = 'notify_sent' AND json_extract(payload_json, '$.kind') = 'awaiting_user' "
            f"AND coalesce(json_extract(payload_json, '$.gate'), '') NOT IN ({marks}) "
            "ORDER BY id", NOT_WAITING_GATES)
        out = []
        for at, task_id in rows:
            ts = parse_iso(at)
            if ts is not None and ts > since:
                out.append((ts, str(task_id or "")))
        return out

    def decision_entries(self) -> "list[dict]":
        data = self.read_json(self.state_dir / "pending_decisions.json", [])
        return [d for d in data if isinstance(d, dict)]

    def decisions(self) -> "list[list[str]]":
        return [[d.get("task_id"), d.get("received_at")] for d in self.decision_entries()]

    def open_decisions(self) -> "list[str]":
        """§3.3: every register entry that is not ``resolved``, replied-to ones included."""
        return [str(d.get("task_id")) for d in self.decision_entries()
                if d.get("status") != "resolved"]

    def monitored(self) -> bool:
        return bool(self.runs(LIVE_RUN_STATUSES)) or (
            self.state_dir / "dispatcher" / "curate-inflight.json").exists()

    def last_scan(self) -> "float | None":
        from tools.relay_scan import DEFAULT_RECIPIENT, _read_heartbeat

        entry = _read_heartbeat(self.db_path).get(DEFAULT_RECIPIENT)
        return parse_iso(entry.get("last_scan_at")) if isinstance(entry, dict) else None

    def append_event(self, kind: str, payload: dict) -> None:
        from tools.state_db import connect
        from tools.state_db.writer import StateWriter

        conn = connect(self.db_path)
        try:
            writer = StateWriter(conn)
            writer.append_event(kind=kind, actor="context_handover_watch", payload=payload)
            writer.commit()
        finally:
            conn.close()

    def notify_secretary(self, message: str) -> bool:
        from tools.peer_notify import notify_peer, secretary_target

        return notify_peer(secretary_target(), message)

    def transport(self) -> str:
        from tools.self_edit_approval import _resolve_transport

        return _resolve_transport(None)


def unsupported_reason() -> "str | None":
    """§4.1: only renga and broker + tmux can be typed into from a CLI."""
    from tools.self_edit_approval import _BROKER, _broker_adapter, _resolve_transport

    if _resolve_transport(None) != _BROKER:
        return None
    adapter = _broker_adapter(None)
    return None if adapter == "tmux" else f"broker terminal adapter is {adapter!r}"


def make_pane_backend(pane: dict):
    from tools.self_edit_approval import make_backend, _resolve_transport

    renga = pane.get("backend") == "renga"
    args = SimpleNamespace(
        backend="renga" if renga else "tmux", target=str(pane.get("id")),
        pane_id=int(pane["id"]) if renga else None, broker_state_dir=None,
        tmux_socket=None, call_timeout=10.0)
    return make_backend(args, _resolve_transport(None))


def must_name(org: Org, role: str, now: float) -> "list[str]":
    """§6.2: task_ids the role's handover file has to mention.

    Every live worker, and for the secretary also every task it asked a
    person about (``awaiting_user``) in the last 24 h: whether that question
    is still open is only in the conversation, so the handover must say.
    ``/secretary-handover`` runs ``--must-name secretary`` to get this list.
    """
    names = set(org.runs(LIVE_RUN_STATUSES))
    if role == SECRETARY:
        names |= {t for _, t in org.awaiting_user(now - AWAITING_NAME_WINDOW_SEC) if t}
    return sorted(names)


# ---------------------------------------------------------------------------
# the handover operation (§4.3)
# ---------------------------------------------------------------------------

class Watcher:
    def __init__(self, org: Org, *, backend_factory=make_pane_backend,
                 clock=time.time, sleep=time.sleep):
        self.org = org
        self.backend_factory = backend_factory
        self.clock = clock
        self.sleep = sleep
        self._unset_tried: set = set()

    # -- small waits -----------------------------------------------------
    def wait_until(self, predicate, timeout: float, interval: float = 1.0) -> bool:
        deadline = self.clock() + timeout
        while True:
            if predicate():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(interval)

    def type_line(self, op: dict, text: str) -> None:
        """Type one line and submit it, verifying each half (§4.2 2-3)."""
        from tools.self_edit_approval import BackendFailure, _norm, _poll_composer, extract_composer

        try:
            backend = self.backend_factory(op["pane"])
            composer = extract_composer(backend.inspect(INSPECT_LINES))
            if composer is None:
                raise HandoverFailed("composer_unreadable")
            if _norm(composer):
                raise HandoverFailed("composer_not_empty")
            backend.send_text(text)
            ok, _ = _poll_composer(backend, INSPECT_LINES,
                                   lambda c: c is not None and _norm(c) == _norm(text), 5.0, 0.3)
            if not ok:
                # Never Enter, never erase: the composer may hold someone else's text.
                raise HandoverFailed("readback_mismatch")
            backend.send_enter()
            ok, _ = _poll_composer(backend, INSPECT_LINES,
                                   lambda c: c is not None and _norm(text) not in _norm(c), 10.0, 0.3)
            if not ok:
                raise HandoverFailed("enter_not_taken")
        except BackendFailure as exc:
            raise HandoverFailed(f"backend_failed: {exc}") from exc

    # -- choosing to start -------------------------------------------------
    def secretary_disabled(self) -> "str | None":
        """§7: renga only. §4.3: a failure must be able to reach an absent person."""
        if self.org.transport() != "renga":
            return "secretary_broker_unsupported"
        if not self.org.attention_path.exists():
            return "attention_watcher_absent"
        return None

    def secretary_hold(self) -> "str | None":
        """§3.3: a person's answer is outstanding or may still be in the conversation."""
        if self.org.open_decisions():
            return "pending_decision"
        if self.org.awaiting_user(self.clock() - AWAITING_HOLD_SEC):
            return "awaiting_user"
        return None

    def due(self, role: str) -> "tuple[bool, str]":
        backoff = self.org.read_json(self.org.backoff_path, {})
        failed_at = parse_iso(backoff.get(role))
        if failed_at is not None and self.clock() - failed_at < BACKOFF_SEC:
            return False, "backoff"
        if role == SECRETARY:
            reason = self.secretary_disabled()
            if reason:
                return False, reason
        rec = self.org.record(role)
        reason = not_quiet_reason(rec, self.clock())
        if reason:
            return False, reason
        threshold = int(rec.get("threshold_tokens") or 0)
        if threshold <= 0:
            self.warn_threshold_unset(role, rec)
            return False, "threshold_unset"
        tokens = last_usage_tokens(Path(rec.get("transcript_path") or ""))
        if tokens is None:
            return False, "usage_unknown"
        if tokens < threshold:
            return False, "under_threshold"
        if role == DISPATCHER:
            if self.org.runs(("queued",)):
                return False, "dispatch_in_progress"
        else:
            if rec.get("session_crons"):
                return False, "session_crons"  # §3.3 / §7: kept until decided otherwise
            reason = self.secretary_hold() or not_idle_reason(rec)
            if reason:
                return False, reason
        return True, f"tokens={tokens}"

    def warn_threshold_unset(self, role: str, rec: dict) -> None:
        """Tell the secretary once per session that no threshold is configured."""
        sid = rec.get("session_id")
        seen = self.org.read_json(self.org.unset_path, {})
        if seen.get(role) == sid or (role, sid) in self._unset_tried:
            return
        # One attempt per process; the file marker is written only on delivery,
        # so a failed notice is retried after a watcher restart.
        self._unset_tried.add((role, sid))
        payload = {"role": role, "reason": "threshold_unset"}
        try:
            self.org.append_event("auto_handover_skipped", payload)
        except Exception as exc:  # noqa: BLE001 — still tell the secretary
            print(f"[context-handover] event write failed: {exc}", file=sys.stderr)
        where = "ディスパッチャー" if role == DISPATCHER else "窓口"
        line = (f"{role.upper()}_AUTO_HANDOVER_SKIPPED: reason=threshold_unset "
                "(CLAUDE_ORG_HANDOVER_THRESHOLD_TOKENS も CLAUDE_CODE_AUTO_COMPACT_WINDOW も未設定のため"
                f"自動引き継ぎをしません。{where}の settings の env に閾値を設定してください)")
        delivered = self.org.notify_secretary(line)
        if delivered:
            self.org.write_json(self.org.unset_path, {**seen, role: sid})
        print(f"[context-handover] {line} (secretary notified={delivered})", flush=True)

    def start_op(self, role: str) -> dict:
        rec = self.org.record(role)
        transcript = rec.get("transcript_path") or ""
        try:
            p0 = Path(transcript).stat().st_size
        except OSError:
            p0 = 0
        op = {
            "op": datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "role": role, "stage": "handover", "started_at": now_iso(self.clock()),
            "old_session_id": rec.get("session_id"), "old_transcript": transcript,
            "p0": p0, "s0": rec.get("turn_closed", 0), "pane": rec.get("pane"),
        }
        self.org.write_json(self.org.op_path, op)
        return op

    def save(self, op: dict, **changes) -> dict:
        op.update(changes)
        self.org.write_json(self.org.op_path, op)
        return op

    # -- stages ----------------------------------------------------------
    def stage_handover(self, op: dict) -> None:
        role = op["role"]
        self.type_line(op, f"/{role}-handover auto op={op['op']}")
        started = parse_iso(op["started_at"]) or 0

        def written() -> bool:
            rec = self.org.record(role)
            try:
                fresh = self.org.handover_path(role).stat().st_mtime >= started
            except OSError:
                fresh = False
            return (fresh and rec.get("turn_closed", 0) >= op["s0"] + 1
                    and rec.get("turn_open") == rec.get("turn_closed"))

        if not self.wait_until(written, HANDOVER_TIMEOUT_SEC):
            raise HandoverFailed("handover_not_written")
        self.check_handover_file(role)
        snapshot = {"runs": self.org.runs(("queued",) + LIVE_RUN_STATUSES),
                    "decisions": self.org.decisions()}
        self.save(op, stage="clearing", snapshot=snapshot)
        rec = self.org.record(role)
        if rec.get("turn_open") != rec.get("turn_closed"):
            raise HandoverFailed("turn_open_before_clear")
        if role == SECRETARY:
            # A question asked during the handover turn may be in the conversation only.
            reason = self.secretary_hold()
            if reason:
                raise HandoverFailed(f"not_quiet_before_clear: {reason}")
        self.type_line(op, "/clear")
        self.stage_clearing(op)

    def check_handover_file(self, role: str) -> None:
        """§6.2: every task_id from ``must_name`` must appear in the handover body."""
        try:
            body = self.org.handover_path(role).read_text(encoding="utf-8")
        except OSError:
            raise HandoverFailed("handover_unreadable")
        missing = [t for t in must_name(self.org, role, self.clock()) if t not in body]
        if missing:
            raise HandoverFailed(f"handover_missing_task_ids: {','.join(missing)}")

    def cleared(self, op: dict) -> bool:
        rec = self.org.record(op["role"])
        return (rec.get("source") == "clear" and bool(rec.get("session_id"))
                and rec.get("session_id") != op["old_session_id"])

    def stage_clearing(self, op: dict) -> None:
        if not self.wait_until(lambda: self.cleared(op), CLEAR_TIMEOUT_SEC):
            raise HandoverFailed("clear_not_observed")
        rec = self.org.record(op["role"])
        self.save(op, stage="cleared", new_session_id=rec.get("session_id"),
                  cleared_at=rec.get("cleared_at") or now_iso(self.clock()))
        self.stage_cleared(op)

    def stage_cleared(self, op: dict) -> None:
        role = op["role"]
        self.sleep(LATE_SETTLE_SEC)
        late = self.org.context_dir / f"{role}-late-input-{op['op']}.md"
        try:
            with open(op["old_transcript"], "rb") as fh:
                fh.seek(op["p0"])
                lines = fh.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        inputs = extract_late_inputs(lines, op["op"], role)
        if inputs and not late.exists() and not late.with_name(late.stem + ".consumed.md").exists():
            late.write_text(render_late_inputs(op["op"], inputs), encoding="utf-8")
        self.save(op, stage="resuming", late_inputs=len(inputs))
        self.stage_resuming(op)

    def stage_resuming(self, op: dict) -> None:
        role = op["role"]
        for _attempt in range(2):  # one re-send; the skill is idempotent per op (§4.5)
            if self.org.resumed_event(role, op["op"]) is None:
                self.type_line(op, f"/{role}-resume auto op={op['op']}")
            if self.wait_until(lambda: self.org.resumed_event(role, op["op"]) is not None,
                               RESUME_TIMEOUT_SEC):
                break
        else:
            raise HandoverFailed("resume_not_observed")
        event = self.org.resumed_event(role, op["op"]) or {}
        self.save(op, stage="verifying", resumed_at=now_iso(self.clock()), loop=event.get("loop"))
        self.stage_verifying(op)

    def stage_verifying(self, op: dict) -> None:
        """§6.1: compare by identifier; monitoring loop is exactly one when there is work."""
        role = op["role"]
        snapshot = op.get("snapshot") or {}
        lost = [t for t in snapshot.get("runs", {}) if not self.org.run_exists(t)]
        now_decisions = self.org.decisions()
        lost += [f"decision:{d[0]}@{d[1]}" for d in snapshot.get("decisions", [])
                 if d not in now_decisions]
        if lost:
            self.report("auto_handover_loss_suspected", op, "verifying", ",".join(lost))
        resumed_at = parse_iso(op.get("resumed_at")) or 0
        cleared_at = parse_iso(op.get("cleared_at")) or 0
        if role == DISPATCHER and self.org.monitored():
            def one_loop_running() -> bool:
                if not self.org.monitored():
                    return True  # the last worker finished meanwhile: idle is correct
                rec = self.org.record(role)
                scan = self.org.last_scan()
                crons = [c for c in rec.get("session_crons") or [] if is_monitoring_cron(c)]
                return ((parse_iso(rec.get("stop_at")) or 0) > resumed_at and len(crons) == 1
                        and scan is not None and scan > cleared_at)

            if not self.wait_until(one_loop_running, HEARTBEAT_TIMEOUT_SEC, interval=POLL_SEC):
                rec = self.org.record(role)
                count = len([c for c in rec.get("session_crons") or [] if is_monitoring_cron(c)])
                raise HandoverFailed(f"monitoring_loop_not_single: crons={count}")
        self.save(op, stage="done", done_at=now_iso(self.clock()))
        self.org.append_event("auto_handover_done", {"op": op["op"], "role": role,
                                                     "lost": len(lost)})

    # -- failure / recovery ----------------------------------------------
    def report(self, kind: str, op: dict, stage: str, reason: str) -> None:
        role = op.get("role") or DISPATCHER
        payload = {"op": op.get("op"), "role": role, "stage": stage, "reason": reason}
        try:
            self.org.append_event(kind, payload)
            if role == SECRETARY:
                # §4.3: the secretary cannot be relied on to tell a person about
                # its own failure; the attention watcher beeps on this row.
                self.org.append_event("notify_sent", {
                    "kind": "awaiting_user", "task_id": f"auto-handover-{op.get('op')}",
                    "gate": "auto_handover_failed", "note": f"{kind} stage={stage} {reason}"})
        except Exception as exc:  # noqa: BLE001 — still tell the secretary
            print(f"[context-handover] event write failed: {exc}", file=sys.stderr)
        label = role.upper()
        line = (f"{label}_AUTO_HANDOVER_FAILED: op={payload['op']} stage={stage} "
                f"reason={reason}" if kind == "auto_handover_failed" else
                f"{label}_AUTO_HANDOVER_LOSS_SUSPECTED: op={payload['op']} {reason}")
        if kind == "auto_handover_failed" and stage in ("cleared", "resuming", "verifying"):
            where = "ディスパッチャーペイン" if role == DISPATCHER else "窓口ペイン"
            line += f"（/clear 済み。{where}に /{role}-resume を打てば戻れます）"
        delivered = self.org.notify_secretary(line)
        print(f"[context-handover] {line} (secretary notified={delivered})", flush=True)

    def fail(self, op: dict, reason: str) -> None:
        stage = op.get("stage", "?")
        self.save(op, stage="failed", failed_stage=stage, reason=reason)
        backoff = self.org.read_json(self.org.backoff_path, {})
        backoff[op.get("role") or DISPATCHER] = now_iso(self.clock())
        self.org.write_json(self.org.backoff_path, backoff)
        self.report("auto_handover_failed", op, stage, reason)

    def recover(self, op: dict) -> "str | None":
        """§4.5: which stage to continue from after a restart, or None to start over."""
        op.setdefault("role", DISPATCHER)  # ops written before the secretary stage
        stage = op.get("stage")
        rec = self.org.record(op["role"])
        if stage in ("handover", "clearing"):
            if rec.get("session_id") == op.get("old_session_id"):
                return None  # /clear has not taken effect: start over
            if self.cleared(op):
                return "clearing"
            raise HandoverFailed("restart_state_indeterminate")
        if stage not in ("cleared", "resuming", "verifying"):
            return None
        # A suspend/start in between leaves this file behind with a dead pane id
        # that may since have been reused: continue only in the same session.
        if (rec.get("session_id") != op.get("new_session_id")
                or rec.get("pane") != op.get("pane")):
            raise HandoverFailed("restart_session_replaced")
        return stage

    def run_op(self, op: dict, stage: str) -> None:
        try:
            {"handover": self.stage_handover, "clearing": self.stage_clearing,
             "cleared": self.stage_cleared, "resuming": self.stage_resuming,
             "verifying": self.stage_verifying}[stage](op)
        except HandoverFailed as exc:
            self.fail(op, exc.reason)

    def tick(self) -> str:
        op = self.org.read_json(self.org.op_path, {})
        if op.get("stage") not in (None, "done", "failed"):
            try:
                stage = self.recover(op)
            except HandoverFailed as exc:
                self.fail(op, exc.reason)
                return "failed"
            if stage:
                self.run_op(op, stage)
                return f"continued:{stage}"
        results = []
        for role in ROLES:
            try:
                ok, why = self.due(role)
            except HandoverFailed as exc:
                ok, why = False, exc.reason
            if ok:
                op = self.start_op(role)
                print(f"[context-handover] start op={op['op']} role={role} ({why})", flush=True)
                self.run_op(op, "handover")
                return f"ran:{op['stage']}"
            results.append(f"{role}:{why}")
        return " ".join(results)


# ---------------------------------------------------------------------------
# process lifecycle (same shape as tools/secretary_queue_watcher.py)
# ---------------------------------------------------------------------------

def _identity(pid: int) -> bool:
    from tools.secretary_queue_watcher import live_cmdline, pid_alive

    if not pid_alive(pid):
        return False
    cmd = live_cmdline(pid)
    return cmd is not None and any(SCRIPT_IDENT in part for part in cmd)


def run_stop(org: Org) -> int:
    from tools.secretary_queue_watcher import wait_gone

    rec = org.read_json(org.pid_path, {})
    pid = rec.get("pid")
    if not isinstance(pid, int):
        print("[context-handover] STOP: not running (no sidecar).")
        org.pid_path.unlink(missing_ok=True)
        return 0
    if Path(rec.get("cwd") or "").resolve() != Path.cwd().resolve() or not _identity(pid):
        print(f"[context-handover] STOP: pid={pid} is not this org's watcher; sidecar removed.")
        org.pid_path.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    wait_gone(pid)
    org.pid_path.unlink(missing_ok=True)
    print(f"[context-handover] STOP: pid={pid} stopped.")
    return 0


def watcher_pid(org: Org) -> "int | None":
    """pid of this org's live watcher per the sidecar, else None."""
    rec = org.read_json(org.pid_path, {})
    pid = rec.get("pid")
    if not isinstance(pid, int) or not _identity(pid):
        return None
    if Path(rec.get("cwd") or "").resolve() != Path.cwd().resolve():
        return None
    return pid


def run_status(org: Org) -> int:
    pid = watcher_pid(org)
    if pid is None:
        stale = " (stale sidecar)" if org.pid_path.exists() else ""
        print(f"[context-handover] STATUS: not running{stale}.")
        return 1
    started = org.read_json(org.pid_path, {}).get("started_at")
    print(f"[context-handover] STATUS: running pid={pid} since {started} log={org.log_path}")
    return 0


def run_daemon(org: Org) -> int:
    """Start the resident loop detached from this shell and the pane (no pane needed)."""
    import subprocess

    # ponytail: POSIX only. Native Windows cannot verify the process identity (live_cmdline
    # is None), so --stop/--status could not manage a detached watcher; add that first.
    if os.name != "posix":
        print("[context-handover] 自動引き継ぎ無効: --daemon は POSIX のみ（Windows native は停止経路が無い）")
        return EXIT_NOT_STARTED
    pid = watcher_pid(org)
    if pid is not None:
        print(f"[context-handover] already running (pid={pid}); exiting.")
        return EXIT_NOT_STARTED
    reason = unsupported_reason()
    if reason:
        print(f"[context-handover] 自動引き継ぎ無効: {reason}（対応は renga と broker + tmux のみ）")
        return EXIT_NOT_STARTED
    org.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(org.log_path, "ab") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve())],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 close_fds=True, start_new_session=True)
    deadline = time.time() + 10
    while time.time() < deadline:
        if child.poll() is not None:
            print(f"[context-handover] DAEMON: exited at start (code={child.returncode}); see {org.log_path}")
            return child.returncode or 1
        if org.read_json(org.pid_path, {}).get("pid") == child.pid:
            print(f"[context-handover] DAEMON: started pid={child.pid} log={org.log_path}")
            return 0
        time.sleep(0.2)
    print(f"[context-handover] DAEMON: pid={child.pid} did not register within 10s; see {org.log_path}")
    return 1


def run_watch(org: Org) -> int:
    rec = org.read_json(org.pid_path, {})
    pid = rec.get("pid")
    if isinstance(pid, int) and pid != os.getpid() and _identity(pid):
        print(f"[context-handover] already running (pid={pid}); exiting.")
        return EXIT_NOT_STARTED
    reason = unsupported_reason()
    if reason:
        print(f"[context-handover] 自動引き継ぎ無効: {reason}（対応は renga と broker + tmux のみ）")
        return EXIT_NOT_STARTED
    org.write_json(org.pid_path, {"pid": os.getpid(), "cwd": str(Path.cwd().resolve()),
                                  "cmdline": list(sys.argv), "started_at": now_iso()})
    def _on_sigterm(signum, frame):  # noqa: ARG001 — run the finally below
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)
    watcher = Watcher(org)
    print(f"[context-handover] watching {', '.join(ROLES)} every {POLL_SEC}s", flush=True)
    last = None
    try:
        while True:
            try:
                result = watcher.tick()
            except Exception as exc:  # noqa: BLE001 — keep the resident loop alive
                result = f"error: {exc}"
            if result != last:
                print(f"[context-handover] {now_iso()} {result}", flush=True)
                last = result
            time.sleep(POLL_SEC)
    finally:
        if org.read_json(org.pid_path, {}).get("pid") == os.getpid():
            org.pid_path.unlink(missing_ok=True)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Automatic context handover for the dispatcher and the secretary.")
    parser.add_argument("--stop", action="store_true", help="stop the running watcher")
    parser.add_argument("--daemon", action="store_true",
                        help="start the watcher detached (no pane); log goes to .state/")
    parser.add_argument("--status", action="store_true",
                        help="report whether the watcher is alive (exit 1 if not)")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--must-name", choices=ROLES, metavar="ROLE",
                        help="print the task_ids the role's handover file must mention, one per line")
    args = parser.parse_args(argv)
    org = Org.discover()
    if args.must_name:
        for task_id in must_name(org, args.must_name, time.time()):
            print(task_id)
        return 0
    if args.stop:
        return run_stop(org)
    if args.status:
        return run_status(org)
    if args.daemon:
        return run_daemon(org)
    if args.once:
        print(Watcher(org).tick())
        return 0
    return run_watch(org)


if __name__ == "__main__":
    sys.exit(main())
