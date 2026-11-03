#!/usr/bin/env python3
"""EVENT_CURSOR_RESET handling for the dispatcher, as one command (Refs #1233).

Steps 1-6 of .dispatcher/references/cursor-reset.md live here, so a dispatcher
that remembers an old copy of the prose still gets the current behaviour.
Call it once at the top of every worker-monitoring cycle:

    python3 ../tools/cursor_reset.py \
        --cursor-file ../.state/dispatcher-event-cursor.txt \
        --session-key '{session_key|none}' --session-key-source {source} \
        --idle-state ../.state/dispatcher/worker-idle-state.json

stdout, one JSON line:
  discard  true when the saved cursor was dropped (poll without ``since`` and
           save the new ``next_since`` with the current binding)
  since    saved ``next_since`` to poll with, or null (poll without ``since``)
  notices  [{message, sent_command}] to send to the secretary. Send each
           ``message`` with send_message(to_id="secretary"), and only after it
           succeeded run ``sent_command``. A notice whose ``--sent`` never ran
           is returned again by the next call (the ledger row is the retry).
Suppressed notices are recorded here and do not appear in ``notices``.

Exit 0 on a decision. A non-zero exit / unparseable output must be treated as
"send the EVENT_CURSOR_RESET message anyway" by the caller (fail loud).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.notify_gate import _load_idle_state, decide_event_cursor_reset  # noqa: E402

KEYS = {"source": "event_cursor", "kind": "stale_event_cursor", "confidence": "n/a"}
MESSAGE = (
    "EVENT_CURSOR_RESET: poll_events cursor を破棄し現在時点から再開しました (理由: {reason})。"
    "backend 再起動を跨いだ cursor は連番が復元されず、そのまま使うと以後の pane_exited が全て空振りします。"
    "破棄した区間に起きたペイン終了は回収不能です (poll_events に historical replay は無い)。"
    "同一タブのワーカーは列挙の突き合わせで従来どおり退役を確定できますが、"
    "背景タブ配置のワーカーは終了確定手段が pane_exited だけなので indeterminate として上げます。"
    "その退役確定は、以前「在」と観測した数値 id が後続の list_peers から消えたこと "
    "(契約 T-§2.1 step (3)) を窓口が reconcile する経路にフォールバックします。"
)


def judge_binding(raw: Optional[str], key: str, source: str) -> tuple[Optional[int], Optional[str], str]:
    """-> (since, reason if the cursor is discarded, saved session_key or 'none')."""
    if raw is None or not raw.strip():
        return None, None, "none"  # first start: nothing to discard
    if source == "unbindable":
        return None, None, "none"  # cannot judge: keep the file, poll without since
    try:
        saved = json.loads(raw)
    except ValueError:
        saved = None
    if not isinstance(saved, dict) or "session_key_source" not in saved:
        return None, "legacy_format", "none"
    saved_key = str(saved.get("session_key") or "none")
    if saved["session_key_source"] != source:
        return None, "transport_mismatch", saved_key
    if source != "transport_has_no_server_info" and saved.get("session_key") != key:
        return None, "session_mismatch", saved_key
    return saved.get("next_since"), None, saved_key


def _rows(conn, kind: str) -> list[dict[str, Any]]:
    out = []
    for (p,) in conn.execute("SELECT payload_json FROM events WHERE kind=? ORDER BY id", (kind,)):
        d = json.loads(p)
        if all(d.get(k) == v for k, v in KEYS.items()):
            out.append(d)
    return out


def _pair(d: dict[str, Any]) -> tuple[str, str]:
    return str(d.get("session_key")), str(d.get("discarded_session_key"))


def _append(db: Path, event: str, payload: dict[str, str]) -> None:
    from tools.journal_append import _db_append
    _db_append(db, event, payload, watcher_guard=False)


def _read(db: Path, kind: str) -> list[dict[str, Any]]:
    if not db.exists():
        return []
    from tools.state_db import connect
    conn = connect(db)
    try:
        return _rows(conn, kind)
    finally:
        conn.close()


def _cmd(pair: tuple[str, str], db_path: Optional[str]) -> str:
    db = " --db-path '%s'" % db_path if db_path else ""
    return ("python3 ../tools/cursor_reset.py --sent --session-key '%s' "
            "--discarded-session-key '%s'%s" % (*pair, db))


def run(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db_path) if args.db_path else None
    if db is None:
        from tools.state_db.discover import resolve_state_db_path
        db = resolve_state_db_path()
    cursor = Path(args.cursor_file)
    raw = cursor.read_text(encoding="utf-8") if cursor.exists() else None
    since, reason, saved_key = judge_binding(raw, args.session_key, args.session_key_source)
    if reason:  # step 2: observation row, always
        _append(db, "anomaly_observed", {**KEYS, "session_key": args.session_key,
                                         "discarded_session_key": saved_key, "note": reason})
    # steps 3-6: every anomaly pair without a notify_sent is owed a decision.
    # A pair already in the ledger as notify_sent is de-duped by construction.
    done = {_pair(d) for d in _read(db, "notify_sent")}
    owed: dict[tuple[str, str], str] = {}
    for d in _read(db, "anomaly_observed"):
        if _pair(d) not in done:
            owed[_pair(d)] = str(d.get("note") or "unknown")
    notices = []
    for pair, why in owed.items():
        send, gate_reason = decide_event_cursor_reset(
            _load_idle_state(Path(args.idle_state)) if args.idle_state else "unreadable")
        if send:
            notices.append({"message": MESSAGE.format(reason=why), "sent_command": _cmd(pair, args.db_path)})
        else:
            _append(db, "notify_sent", {**KEYS, "session_key": pair[0],
                                        "discarded_session_key": pair[1],
                                        "delivery": "suppressed", "reason": gate_reason})
    return {"discard": bool(reason), "since": since, "notices": notices}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path")
    ap.add_argument("--sent", action="store_true", help="record that the notice was sent")
    ap.add_argument("--session-key", required=True)
    ap.add_argument("--discarded-session-key")
    ap.add_argument("--cursor-file")
    ap.add_argument("--session-key-source")
    ap.add_argument("--idle-state")
    args = ap.parse_args(argv)
    if args.sent:
        if args.discarded_session_key is None:
            ap.error("--discarded-session-key is required with --sent")
        db = Path(args.db_path) if args.db_path else None
        if db is None:
            from tools.state_db.discover import resolve_state_db_path
            db = resolve_state_db_path()
        pair = (args.session_key, args.discarded_session_key)
        if pair not in {_pair(d) for d in _read(db, "notify_sent")}:
            _append(db, "notify_sent", {**KEYS, "session_key": pair[0],
                                        "discarded_session_key": pair[1], "delivery": "sent"})
        print(json.dumps({"recorded": True}))
        return 0
    if not args.cursor_file or not args.session_key_source:
        ap.error("--cursor-file and --session-key-source are required")
    print(json.dumps(run(args), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
