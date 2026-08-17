#!/usr/bin/env python3
"""Spawn-completion gate for the dispatcher (`DELEGATE_COMPLETE` precondition).

Why this exists
---------------
`.dispatcher/references/spawn-flow.md` already prescribes the correct
ceremony: 3-3b sends the `Load development channel?` approval Enter,
3-4 polls `list_peers` until the worker registers, 3-5 sends the
instruction. **None of those three steps writes anything.** The only
durable trace of the whole of Step 3 + Step 4 is one `worker_spawned`
row, documented as firing "After MCP `spawn_pane`"
(``docs/journal-events.md`` "Worker lifecycle" table) — i.e. before the
ceremony even starts. And the completion report to the secretary was
free prose with no stated precondition.

The consequence, observed twice on 2026-08-18 (tasks
``cert-questions-ingest-20260818`` / ``interlock-founding-docs-20260818``):
the dispatcher reported "承認済み・peer 登録確認済み・指示送信済み" while
both panes were in fact still parked on the approval prompt with an empty
input box. Both incidents produced a byte-identical `.state/` trace to a
correct dispatch — `delegate_sent` then `worker_spawned`, nothing else —
so nothing could have caught them except a human running `inspect_pane`.

Skipping the ceremony was therefore *cheaper* than performing it and
carried no detection risk. This tool changes both halves of that:

1. **The report body is machine-produced.** The dispatcher does not
   compose `DELEGATE_COMPLETE`; it runs ``verify`` and forwards stdout.
   Producing the report at all now requires supplying the concrete
   `list_peers` observation, and part of that observation is checked
   against a record the dispatcher never wrote (see below).
2. **Omission becomes detectable after the fact.** ``verify`` appends a
   ``worker_spawn_verified`` event; ``audit`` lists every
   ``worker_spawned`` that has no matching one. A dispatch reported
   without running the gate leaves exactly the hole ``audit`` looks for.

What is actually verified vs. merely attested
---------------------------------------------
Being honest about the boundary matters, because this is a v1 hemostatic
fix and not the deterministic spawn driver (that belongs to Interlock v2;
v1 explicitly does not receive a back-port of that design).

**Machine-verified** (the dispatcher cannot satisfy these by asserting
them):

* ``--peer-cwd`` must equal the worker directory recorded on the
  ``runs`` row for the task. That path is written by the *secretary*'s
  ``gen_delegate_payload.py apply`` at T1, in the same transaction as
  ``delegate_sent`` — strictly before the dispatcher is involved. A cwd
  invented from a stale or mismatched pane fails here. This is the same
  org-binding discriminator that spawn-flow 3-4b already requires for
  the background-tab gate (contract T-§4.2-id (O2): identity must be
  established "by an observation independent of the id itself").
* ``--peer-name`` must equal ``worker-{task_id}``.
* A ``worker_spawned`` event for the task must already exist, so the
  gate cannot run ahead of the spawn it is gating.
* ``--pane-id`` / ``--peer-id`` must be positive integers.

**Attested, not verified**: ``--approval`` and ``--instruction``. No
process on this host can observe a PTY keystroke or an MCP
``send_message`` after the fact. Recording them as required enumerated
values means the dispatcher must make a specific, dated, on-the-record
claim rather than a vague sentence — and a wrong claim is now a
falsifiable artifact rather than an unrecoverable one.

Machine-readable contract
-------------------------
``verify`` and ``audit`` both print a single JSON object on stdout and
branch on the exit code (never on parsing the JSON):

* ``0``  — ``verify``: verified, ``delegate_complete`` holds the exact
  report body to send. ``audit``: no unverified spawns.
* ``10`` — ``verify``: gate failed, ``failures[]`` holds the checks that
  did not pass and ``remedy[]`` the spawn-flow step to return to. **No
  report body is emitted.** ``audit``: at least one unverified spawn in
  ``findings[]``.
* ``2``  — error (unreadable DB, bad arguments that argparse let
  through, etc.); the dispatcher reports it to the secretary.

``10`` rather than ``1`` for the fire/fail decision, so an unexpected
Python traceback (which exits ``1``) can never be misread as a verdict —
same convention as ``tools/check_curate_threshold.py`` and
``tools/work_discovery_scan.py``.

Usage
-----
The dispatcher's cwd is ``.dispatcher/``, so it resolves one level up::

    python3 ../tools/spawn_gate.py verify \\
        --task login-fix \\
        --pane-id 5 \\
        --peer-id 5 \\
        --peer-name worker-login-fix \\
        --peer-cwd /abs/path/to/workers/login-fix \\
        --approval sent \\
        --instruction send_message

    python3 ../tools/spawn_gate.py audit --older-than-min 5
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# Make `tools.state_db.*` importable when invoked directly (no prior
# `pip install -e .`), same shim as tools/journal_append.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_FIRE = 10

VERIFIED_EVENT = "worker_spawn_verified"
SPAWNED_EVENT = "worker_spawned"
DELEGATE_SENT_EVENT = "delegate_sent"

#: Where a failing check sends the dispatcher back to. Keyed by check id.
_REMEDY = {
    "run_row": (
        "state.db に当該 task の runs 行が無い。窓口の "
        "gen_delegate_payload.py apply (T1) が未実行か task_id が違う。"
        "窓口へ escalate する。"
    ),
    "worker_dir_known": (
        "runs 行にも delegate_sent イベントにも worker dir が無い。"
        "照合対象が取れないので窓口へ escalate する。"
    ),
    "peer_cwd": (
        "list_peers レコードの cwd が窓口の記録した worker dir と一致しない。"
        "別 org / 別タスクのペインを見ている可能性がある。spawn-flow 3-4 の "
        "list_peers 判定からやり直す。"
    ),
    "peer_name": (
        "list_peers レコードの name が worker-{task_id} でない。"
        "spawn-flow 3-4 の list_peers 判定からやり直す。"
    ),
    "spawned_event": (
        f"{SPAWNED_EVENT} イベントが無い。spawn-flow Step 4 の "
        "journal_append.sh を先に実行する。"
    ),
    "pane_id": "--pane-id が正の整数でない。spawn_claude_pane の戻り値を渡す。",
    "peer_id": (
        "--peer-id が正の整数でない。spawn-flow 3-4 の list_peers で実際に "
        "観測した数値 id を渡す。まだ観測できていないなら 3-3b の承認 Enter を "
        "再送し、登録されるまで poll を続ける（報告はしない）。"
    ),
}


class GateError(Exception):
    """Unrecoverable problem: report status=error / exit 2."""


def _resolve_db(cli_override: "str | None") -> Path:
    from tools.state_db.discover import resolve_state_db_path

    try:
        return Path(resolve_state_db_path(cli_override))
    except Exception as exc:  # pragma: no cover - discovery failure
        raise GateError(f"could not resolve state.db path: {exc}") from exc


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise GateError(f"state.db not found at {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise GateError(f"could not open {db_path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _norm_path(value: "str | None") -> "str | None":
    """Normalise for comparison without requiring the path to exist.

    ``os.path.realpath`` is deliberately not used: the gate may run on a
    host where the worker dir is a symlinked worktree, and resolving one
    side but not the other would produce a spurious mismatch. Trailing
    separators and ``.``/``..`` segments are the only real-world skew we
    have seen between a `list_peers` cwd and the recorded abs path.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return os.path.normpath(stripped).rstrip(os.sep) or os.sep


def _expected_worker_dir(conn: sqlite3.Connection, task_id: str) -> "str | None":
    """The worker dir the *secretary* recorded for this task.

    Primary source is the ``runs`` row (written at T1 by
    ``gen_delegate_payload.py apply``). Post-merge cleanup clears
    ``worker_dir_id``, so fall back to the ``delegate_sent`` payload,
    which is written in the same T1 transaction and is never cleared.
    Both are dispatcher-independent, which is the point of the check.
    """
    row = conn.execute(
        "SELECT w.abs_path AS abs_path "
        "FROM runs r LEFT JOIN worker_dirs w ON w.id = r.worker_dir_id "
        "WHERE r.task_id = ?",
        (task_id,),
    ).fetchone()
    if row is not None and row["abs_path"]:
        return row["abs_path"]

    for ev in conn.execute(
        "SELECT payload_json FROM events WHERE kind = ? "
        "ORDER BY occurred_at DESC, id DESC",
        (DELEGATE_SENT_EVENT,),
    ):
        try:
            payload = json.loads(ev["payload_json"])
        except (TypeError, ValueError):
            continue
        if payload.get("task") == task_id and payload.get("dir"):
            return str(payload["dir"])
    return None


def _has_run_row(conn: sqlite3.Connection, task_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM runs WHERE task_id = ?", (task_id,)
    ).fetchone()
    return row is not None


def _latest_event_at(
    conn: sqlite3.Connection, kind: str, task_id: str
) -> "str | None":
    """``occurred_at`` of the newest event of ``kind`` for ``task_id``."""
    for ev in conn.execute(
        "SELECT occurred_at, payload_json FROM events WHERE kind = ? "
        "ORDER BY occurred_at DESC, id DESC",
        (kind,),
    ):
        try:
            payload = json.loads(ev["payload_json"])
        except (TypeError, ValueError):
            continue
        if payload.get("task") == task_id:
            return ev["occurred_at"]
    return None


def _positive_int(value: "str | None") -> "int | None":
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _append_verified_event(payload: dict, db_override: "str | None") -> None:
    from tools.state_db import connect
    from tools.state_db.discover import resolve_state_db_path, verify_or_exit
    from tools.state_db.writer import StateWriter

    db_path = Path(resolve_state_db_path(db_override))
    conn = connect(db_path)
    try:
        verify_or_exit(db_path, conn=conn, prog="tools/spawn_gate.py")
        writer = StateWriter(conn)
        writer.append_event(
            kind=VERIFIED_EVENT, actor="dispatcher", payload=payload
        )
        writer.commit()
    finally:
        conn.close()


def _delegate_complete_body(
    task_id: str, worker: str, pane_id: int, peer_id: int
) -> str:
    """The canonical report body. Assembled here, never by the dispatcher."""
    return (
        f"DELEGATE_COMPLETE: {task_id} のワーカーを派遣しました。\n"
        f"Pane: {worker} (id={pane_id})\n"
        f"Peer: list_peers 登録確認済み (id={peer_id})\n"
        f"Gate: {VERIFIED_EVENT} 記帳済み (tools/spawn_gate.py verify)"
    )


def cmd_verify(args) -> int:
    task_id = args.task.strip()
    worker = f"worker-{task_id}"

    db_path = _resolve_db(args.db_path)
    conn = _connect(db_path)
    try:
        failures: "list[str]" = []
        checks: "dict[str, object]" = {}

        pane_id = _positive_int(args.pane_id)
        peer_id = _positive_int(args.peer_id)
        if pane_id is None:
            failures.append("pane_id")
        if peer_id is None:
            failures.append("peer_id")

        has_run = _has_run_row(conn, task_id)
        checks["run_row"] = has_run
        if not has_run:
            failures.append("run_row")

        expected_dir = _expected_worker_dir(conn, task_id)
        checks["expected_worker_dir"] = expected_dir
        checks["observed_peer_cwd"] = args.peer_cwd
        if expected_dir is None:
            failures.append("worker_dir_known")
        elif _norm_path(expected_dir) != _norm_path(args.peer_cwd):
            failures.append("peer_cwd")

        checks["expected_peer_name"] = worker
        checks["observed_peer_name"] = args.peer_name
        if args.peer_name.strip() != worker:
            failures.append("peer_name")

        spawned_at = _latest_event_at(conn, SPAWNED_EVENT, task_id)
        checks["worker_spawned_at"] = spawned_at
        if spawned_at is None:
            failures.append("spawned_event")

        already_at = _latest_event_at(conn, VERIFIED_EVENT, task_id)
        checks["already_verified_at"] = already_at
    except sqlite3.Error as exc:
        raise GateError(f"state.db read failed: {exc}") from exc
    finally:
        conn.close()

    if failures:
        # Deterministic order so identical failures compare equal in tests
        # and in the dispatcher's report to the secretary.
        ordered = [c for c in _REMEDY if c in failures]
        print(
            json.dumps(
                {
                    "status": "gate_failed",
                    "task": task_id,
                    "worker": worker,
                    "failures": ordered,
                    "remedy": [_REMEDY[c] for c in ordered],
                    "checks": checks,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_FIRE

    payload = {
        "task": task_id,
        "worker": worker,
        "pane_id": pane_id,
        "peer_id": peer_id,
        "peer_cwd": args.peer_cwd.strip(),
        "approval": args.approval,
        "instruction": args.instruction,
        "transport": args.transport,
    }

    # Idempotent: a re-run after a delivery hiccup must not double-record.
    # Re-emitting the body is safe and is what the dispatcher needs.
    status = "verified"
    if already_at is None:
        try:
            _append_verified_event(payload, args.db_path)
        except SystemExit:
            raise
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "task": task_id,
                        "error": f"could not append {VERIFIED_EVENT}: {exc}",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return EXIT_ERROR
    else:
        status = "already_verified"

    print(
        json.dumps(
            {
                "status": status,
                "task": task_id,
                "worker": worker,
                "recorded": payload,
                "checks": checks,
                "delegate_complete": _delegate_complete_body(
                    task_id, worker, pane_id, peer_id
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return EXIT_OK


#: Run states in which an unverified spawn is no longer actionable. A run
#: that already reached one of these got its work done (or was abandoned)
#: regardless of what the gate did or did not record, so reporting it would
#: only bury the live findings.
_TERMINAL_RUN_STATUS = ("completed", "failed", "abandoned")


def cmd_audit(args) -> int:
    db_path = _resolve_db(args.db_path)
    conn = _connect(db_path)
    try:
        terminal_tasks = {
            row["task_id"]
            for row in conn.execute(
                "SELECT task_id FROM runs WHERE status IN "
                f"({','.join('?' * len(_TERMINAL_RUN_STATUS))})",
                _TERMINAL_RUN_STATUS,
            )
        }
        spawned: "dict[str, str]" = {}
        for ev in conn.execute(
            "SELECT occurred_at, payload_json FROM events WHERE kind = ? "
            "ORDER BY occurred_at ASC, id ASC",
            (SPAWNED_EVENT,),
        ):
            try:
                payload = json.loads(ev["payload_json"])
            except (TypeError, ValueError):
                continue
            task = payload.get("task")
            if isinstance(task, str) and task:
                # Last spawn wins: a re-dispatch of the same task must be
                # verified again, so an older verification does not cover it.
                spawned[task] = ev["occurred_at"]

        verified: "dict[str, str]" = {}
        for ev in conn.execute(
            "SELECT occurred_at, payload_json FROM events WHERE kind = ? "
            "ORDER BY occurred_at ASC, id ASC",
            (VERIFIED_EVENT,),
        ):
            try:
                payload = json.loads(ev["payload_json"])
            except (TypeError, ValueError):
                continue
            task = payload.get("task")
            if isinstance(task, str) and task:
                verified[task] = ev["occurred_at"]

        cutoff = _cutoff_iso(args.older_than_min)
        horizon = _cutoff_iso(args.within_hours * 60)
        findings = []
        skipped = {"in_grace": 0, "before_horizon": 0, "terminal_run": 0}
        for task, spawned_at in sorted(spawned.items(), key=lambda kv: kv[1]):
            verified_at = verified.get(task)
            if verified_at is not None and verified_at >= spawned_at:
                continue
            if cutoff is not None and spawned_at > cutoff:
                # Still inside the grace window; the dispatcher may be
                # mid-ceremony. Not a finding yet.
                skipped["in_grace"] += 1
                continue
            if horizon is not None and spawned_at < horizon:
                # Predates the gate's deployment (or is simply old); the
                # missing event says nothing about that dispatch.
                skipped["before_horizon"] += 1
                continue
            if task in terminal_tasks:
                skipped["terminal_run"] += 1
                continue
            findings.append(
                {
                    "task": task,
                    "worker": f"worker-{task}",
                    "spawned_at": spawned_at,
                    "last_verified_at": verified_at,
                    "note": (
                        f"{SPAWNED_EVENT} に対応する {VERIFIED_EVENT} が無い。"
                        "spawn 儀式 (承認 Enter / list_peers 登録 / 指示送信) "
                        "の実行が確認できていない。ペインを inspect_pane で実見し、"
                        "必要なら spawn-flow 3-3b から復旧する。"
                    ),
                }
            )
    except sqlite3.Error as exc:
        raise GateError(f"state.db read failed: {exc}") from exc
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "status": "unverified_spawns" if findings else "clean",
                "grace_minutes": args.older_than_min,
                "horizon_hours": args.within_hours,
                "finding_count": len(findings),
                # Surfaced, never silent: a reader must be able to tell
                # "nothing is wrong" from "the filters ate everything".
                "skipped": skipped,
                "findings": findings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return EXIT_FIRE if findings else EXIT_OK


def _cutoff_iso(older_than_min: int) -> "str | None":
    if older_than_min <= 0:
        return None
    import datetime

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        minutes=older_than_min
    )
    # Match the DB's `strftime('%Y-%m-%dT%H:%M:%fZ','now')` shape so the
    # string comparison against `occurred_at` is well-ordered.
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.") + f"{cutoff.microsecond // 1000:03d}Z"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/spawn_gate.py",
        description=(
            "Gate the dispatcher's DELEGATE_COMPLETE on an actual list_peers "
            "observation, and audit spawns that never passed the gate."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override the resolved state.db path (tests / debugging).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser(
        "verify",
        help=(
            "Check the spawn ceremony evidence, record worker_spawn_verified, "
            "and print the DELEGATE_COMPLETE body to send."
        ),
    )
    verify.add_argument("--task", required=True, help="task_id")
    verify.add_argument(
        "--pane-id", required=True, help="numeric pane id returned by spawn_claude_pane"
    )
    verify.add_argument(
        "--peer-id",
        required=True,
        help="numeric id of the record actually observed in list_peers (3-4)",
    )
    verify.add_argument(
        "--peer-name", required=True, help="name field of that same list_peers record"
    )
    verify.add_argument(
        "--peer-cwd", required=True, help="cwd field of that same list_peers record"
    )
    verify.add_argument(
        "--approval",
        required=True,
        choices=("sent", "not_shown"),
        help=(
            "sent = the 3-3b approval Enter was sent; not_shown = no approval "
            "prompt appeared. Attested by the dispatcher, not machine-checked."
        ),
    )
    verify.add_argument(
        "--instruction",
        required=True,
        choices=("send_message", "send_keys", "both"),
        help=(
            "how the 3-5 instruction was delivered. Attested by the "
            "dispatcher, not machine-checked."
        ),
    )
    verify.add_argument(
        "--transport",
        default=os.environ.get("ORG_TRANSPORT", "renga"),
        help="ORG_TRANSPORT resolved value, recorded on the event.",
    )
    verify.set_defaults(func=cmd_verify)

    audit = sub.add_parser(
        "audit",
        help="List worker_spawned events with no matching worker_spawn_verified.",
    )
    audit.add_argument(
        "--older-than-min",
        type=int,
        default=5,
        help=(
            "Grace window in minutes; spawns newer than this are skipped "
            "because the dispatcher may still be mid-ceremony. 0 disables."
        ),
    )
    audit.add_argument(
        "--within-hours",
        type=int,
        default=24,
        help=(
            "Look-back horizon in hours. Spawns older than this predate the "
            "gate and their missing event proves nothing, so they are "
            "skipped and counted under skipped.before_horizon. 0 disables."
        ),
    )
    audit.set_defaults(func=cmd_audit)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    # `--db-path` is declared on the top-level parser so it works before or
    # after the subcommand; normalise it onto the namespace either way.
    if getattr(args, "db_path", None) is None:
        args.db_path = None
    try:
        return args.func(args)
    except GateError as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
