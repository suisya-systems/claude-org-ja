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

* ``--peer-cwd`` must equal the worker directory in the ``delegate_sent``
  event, which the *secretary*'s ``gen_delegate_payload.py apply`` writes
  at T1 — strictly before the dispatcher is involved, into an append-only
  table nothing downstream can rewrite. A cwd invented from a stale or
  mismatched pane fails here. This is the same org-binding discriminator
  that spawn-flow 3-4b already requires for the background-tab gate
  (contract T-§4.2-id (O2): identity must be established "by an
  observation independent of the id itself").

  ``runs.worker_dir_id`` is deliberately **not** the reference: spawn-flow
  Step 4 item 2 calls ``upsert_run(..., worker_dir_abs_path=...)`` before
  this gate runs, and that overwrites the column — so comparing against it
  would let the dispatcher validate a value it had just written. ``runs``
  serves only as a fallback when no ``delegate_sent`` event exists, and a
  divergence between the two is itself a gate failure.
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

**Two evidence modes.** ``--evidence list_peers`` (default) is the normal
3-4 enumeration and gets the machine-checked half above.
``--evidence send_delivery`` is the documented 3-4 degraded path
(capability-shaped backend, ``first_drive`` unapproved): there the
enumeration is discarded as untrustworthy and a successful
``send_message`` *is* the readiness probe, so no peer record exists to
quote. That mode requires the ``--peer-*`` flags to be **absent** — a
discarded enumeration must not ride back in under the weaker mode's name
— and has no machine-checked half at all, which is why the mode is
recorded on the event and named in the report body. Every currently
deployed backend takes the legacy fallback path, i.e. the default mode.

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
import re
import sqlite3
import sys
import tempfile
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

#: Deployment cutoff for ``audit``. Spawns before this predate the gate, so
#: their missing ``worker_spawn_verified`` proves nothing and would bury the
#: live findings (the DB carries ~380 such rows back to 2026-05).
#:
#: This is a **fixed** date on purpose, not a rolling window. A rolling
#: horizon would be correct only during the first day after rollout: after
#: that it silently drops a still-active unverified spawn out of the report
#: the moment it ages past the window, and every later monitoring cycle
#: would say "clean" while the gap is still actionable.
#:
#: **UTC**, like every ``occurred_at``. Note the offset: the 2026-08-18 JST
#: incidents are stamped ``2026-08-17T17:07Z`` / ``…T19:01Z`` and therefore
#: fall *before* this cutoff. That is correct — the gate did not exist when
#: they happened, so their missing event is expected, not a finding. They
#: are the shape the detector looks for, not rows it should report now.
GATE_EPOCH = "2026-08-18T00:00:00.000Z"

#: Where a failing check sends the dispatcher back to. Keyed by check id.
_REMEDY = {
    "run_row": (
        "state.db に当該 task の runs 行が無い。窓口の "
        "gen_delegate_payload.py apply (T1) が未実行か task_id が違う。"
        "窓口へ escalate する。"
    ),
    "worker_dir_known": (
        "delegate_sent イベントにも runs 行にも worker dir が無い。"
        "照合対象が取れないので窓口へ escalate する。"
    ),
    "worker_dir_divergence": (
        "delegate_sent (窓口が T1 で書いた不変値) と runs.worker_dir_id "
        "(Step 4 の upsert_run が上書きしうる値) が食い違っている。"
        "独立照合の基準が失われているので、どちらが誤りかを窓口へ escalate する。"
    ),
    "evidence_mismatch": (
        "--evidence send_delivery では list_peers 列挙を破棄しているので "
        "--peer-id / --peer-name / --peer-cwd を渡してはならない。"
        "列挙を実際に見たなら --evidence list_peers を使う。"
    ),
    "bound_pane_id": (
        "背景タブ経路 (placement=background_tab) では、list_peers の数値 id が "
        "spawn_claude_pane の戻り値 (bound_pane_id) と完全一致することが受理条件 "
        "(spawn-flow 3-4b)。一致しないレコードは別ペイン (並走 org の同名 worker や "
        "退役し損ねた前回分) なので、ゲートを開けずに 3-4b の登録 poll を続ける。"
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


#: Restated on every ``verify`` result so the boundary travels with the
#: evidence instead of living only in prose that a reader may not open.
#: See the module docstring for why the tool cannot close this in v1.
_ATTESTED_ONLY = (
    "approval / instruction、および evidence=list_peers の peer_* は "
    "dispatcher の申告であり、本ツールからは MCP を観測できないため機械検証"
    "できない。ゲートが保証するのは (a) delegate_sent との cwd 照合等の"
    "機械照合可能な半分と、(b) 記帳が無い報告を audit が必ず検出することの 2 点。"
    "儀式そのものの決定論化は Interlock (v2) の担当 (Issue #740 2026-08-17 追補)。"
)


class GateError(Exception):
    """Unrecoverable problem: report status=error / exit 2."""


def _resolve_transport(explicit: "str | None") -> str:
    """Shared resolver first; a literal only if runtime import is unavailable.

    ``tools.transport`` re-exports runtime's ``DEFAULT_TRANSPORT``, which
    Epic #586 flipped renga -> broker. Hard-coding a default here would
    silently stamp every verification event with the wrong transport in
    the default configuration.
    """
    if explicit:
        return explicit
    try:
        from tools.transport import resolve as _resolve

        return str(_resolve())
    except Exception:
        # runtime not importable (fresh checkout / CI without the pin):
        # fall back to the env var alone and say so via the literal.
        return os.environ.get("ORG_TRANSPORT") or "unresolved"


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


def _delegate_sent_dir(conn: sqlite3.Connection, task_id: str) -> "str | None":
    """Worker dir from the ``delegate_sent`` event — the immutable source.

    ``gen_delegate_payload.py apply`` writes this in the same T1
    transaction as the ``runs`` reservation, and the ``events`` table is
    append-only, so nothing downstream can rewrite it. That is precisely
    what makes it usable as the independent side of the ``--peer-cwd``
    comparison.
    """
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


def _runs_dir(conn: sqlite3.Connection, task_id: str) -> "str | None":
    """Worker dir currently on the ``runs`` row.

    Weaker than :func:`_delegate_sent_dir`: spawn-flow Step 4 item 2 calls
    ``upsert_run(..., worker_dir_abs_path=...)`` *before* this gate runs,
    and ``StateWriter.upsert_run`` overwrites ``runs.worker_dir_id`` when
    the argument is supplied. So the dispatcher can move this value. It is
    used only as a fallback when the ``delegate_sent`` event is absent,
    and any divergence between the two is itself a gate failure.
    """
    row = conn.execute(
        "SELECT w.abs_path AS abs_path "
        "FROM runs r LEFT JOIN worker_dirs w ON w.id = r.worker_dir_id "
        "WHERE r.task_id = ?",
        (task_id,),
    ).fetchone()
    if row is not None and row["abs_path"]:
        return str(row["abs_path"])
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


#: Marker the Progress Log bullet carries; a bullet in the `## Progress Log`
#: section holding it is the idempotency key for the side effect below.
DISPATCHED_MARK = "派遣完了"


def _workers_dir_for(db_path: Path) -> Path:
    """`.state/workers/` sits beside `state.db` (`.state/state.db`)."""
    return db_path.parent / "workers"


def _utc_now_iso() -> str:
    """Same shape as `events.occurred_at` (`%Y-%m-%dT%H:%M:%fZ`)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


#: Level-2 markdown heading (CommonMark allows up to 3 leading spaces).
#: The one detector used for header end, section start and section end,
#: so the three never disagree about where a section is.
_H2_RE = re.compile(r"^\s{0,3}##\s+(?P<title>.*?)\s*#*\s*$")
#: `Status: planned` with the value isolated; only the value is rewritten
#: so indentation / capitalisation / trailing whitespace stay intact.
_STATUS_PLANNED_RE = re.compile(
    r"^(?P<lead>\s*status\s*:\s*)planned(?P<tail>\s*)$", re.IGNORECASE
)


#: Fenced code block delimiter (``` or ~~~, up to 3 leading spaces).
_FENCE_RE = re.compile(r"^\s{0,3}(?P<run>`{3,}|~{3,})(?P<rest>.*)$")


def _fenced_lines(lines: "list[str]") -> "list[bool]":
    """Per-line flag: inside (or delimiting) a fenced code block.

    CommonMark closing rule: same character, a run at least as long as the
    opener, nothing but whitespace after it. So a ```` fence is not closed
    by ```, and ```py (an info string) cannot close anything.
    """
    flags: "list[bool]" = []
    fence: "str | None" = None  # the opening run, e.g. "````"
    for ln in lines:
        fm = _FENCE_RE.match(ln)
        if fm:
            run, rest = fm.group("run"), fm.group("rest")
            if fence is None:
                fence = run
                flags.append(True)
                continue
            if (
                run[0] == fence[0]
                and len(run) >= len(fence)
                and rest.strip() == ""
            ):
                fence = None
                flags.append(True)
                continue
        flags.append(fence is not None)
    return flags


def _h2_titles(lines: "list[str]") -> "list[str | None]":
    """Per-line lowercase H2 title, or None. Lines inside a fenced code
    block are never headings, so a ``## `` quoted in an example cannot
    end the header or a section."""
    titles: "list[str | None]" = []
    for ln, fenced in zip(lines, _fenced_lines(lines)):
        m = None if fenced else _H2_RE.match(ln)
        titles.append(m.group("title").strip().lower() if m else None)
    return titles


def _has_dispatch_bullet(lines: "list[str]", start: int, end: int) -> bool:
    """A real bullet carrying the marker in ``lines[start:end]``; fenced
    example text does not count."""
    fenced = _fenced_lines(lines)
    return any(
        not fenced[i]
        and lines[i].lstrip().startswith("- ")
        and DISPATCHED_MARK in lines[i]
        for i in range(start, end)
    )


def _replace_file_atomically(path: Path, data: bytes) -> None:
    """Same-directory temp file + ``os.replace`` so a failed write leaves
    the original record intact rather than truncated."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        # mkstemp creates 0600; keep the record's own mode across the swap.
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _rewrite_worker_md(
    text: str, pane_id: int, started_at: str
) -> "tuple[str, list[str]]":
    """Pure edit of one worker record; returns (new_text, changes).

    ``changes`` is empty when nothing needed doing (then ``new_text`` is
    ``text`` unchanged). See :func:`_record_dispatch_in_worker_md` for
    what is written and why.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    trailing_newline = text.endswith("\n")
    if trailing_newline:
        lines.pop()  # split() leaves an empty tail after the final newline
    changes: "list[str]" = []

    # Header = everything before the first `## ` section. Only lines there
    # count as fields, so a `Pane ID:` quoted inside prose is not mistaken
    # for the header field.
    titles = _h2_titles(lines)
    header_end = next(
        (i for i, t in enumerate(titles) if t is not None), len(lines)
    )
    header = lines[:header_end]

    def _has_field(name: str) -> bool:
        return any(
            ln.lstrip().lower().startswith(name.lower() + ":") for ln in header
        )

    def _anchor() -> int:
        """After `Pane Name:` (its natural neighbour); else before
        `Status:` (the spawn-flow template order); else after the last
        `Key: value` header line; else right after the title line."""
        for i, ln in enumerate(header):
            if ln.lstrip().lower().startswith("pane name:"):
                return i + 1
        for i, ln in enumerate(header):
            if ln.lstrip().lower().startswith("status:"):
                return i
        last_field = -1
        for i, ln in enumerate(header):
            stripped = ln.lstrip()
            if stripped and stripped[0].isalpha() and ":" in stripped:
                last_field = i
        return last_field + 1 if last_field >= 0 else min(1, len(header))

    insert_at = _anchor()
    if not _has_field("Pane ID"):
        header.insert(insert_at, f"Pane ID: {pane_id}")
        insert_at += 1
        changes.append("pane_id")
    if not _has_field("Started"):
        header.insert(insert_at, f"Started: {started_at}")
        changes.append("started")
    lines[:header_end] = header
    titles = _h2_titles(lines)  # indices shifted by the header inserts

    # Status: first `status:` line top-to-bottom, exactly how the runtime
    # reads it (state-schema-contract §7). planned -> active only, and
    # only the value is rewritten.
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("status:"):
            m = _STATUS_PLANNED_RE.match(ln)
            if m:
                lines[i] = f"{m.group('lead')}active{m.group('tail')}"
                changes.append("status")
            break

    log_start = next(
        (i for i, t in enumerate(titles) if t == "progress log"), None
    )
    bullet = f"- [{started_at}] {DISPATCHED_MARK}、作業開始（pane id={pane_id}）"
    if log_start is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(["## Progress Log", bullet])
        changes.append("progress_log")
    else:
        # Section ends at the next `## ` heading or EOF. The bullet goes
        # after the last non-blank line so the blank separator before the
        # following section survives.
        section_end = next(
            (
                i
                for i in range(log_start + 1, len(lines))
                if titles[i] is not None
            ),
            len(lines),
        )
        if not _has_dispatch_bullet(lines, log_start + 1, section_end):
            insert = section_end
            while insert > log_start + 1 and lines[insert - 1] == "":
                insert -= 1
            lines.insert(insert, bullet)
            changes.append("progress_log")

    if not changes:
        return text, changes
    return newline.join(lines) + (newline if trailing_newline else ""), changes


#: Read-modify-replace attempts before giving up on a record that keeps
#: changing underneath us (the secretary also appends to Progress Log).
_WORKER_MD_ATTEMPTS = 3


def _record_dispatch_in_worker_md(
    workers_dir: Path, task_id: str, pane_id: int, started_at: str
) -> dict:
    """Write spawn-flow Step 4 items (b)/(c) into the worker record.

    A side effect of ``verify``, not a gate check. The dispatcher used to
    do this by hand right after ``spawn_claude_pane`` and, on 2026-09-03,
    flipped ``Status`` only — leaving five live workers rendered as
    "pane not yet spawned" on the dashboard (which reads ``Pane ID:`` /
    ``Started:`` / the last Progress Log bullet from this file). Since the
    dispatcher cannot produce ``DELEGATE_COMPLETE`` without running
    ``verify``, doing the writes here makes them unforgettable.

    Idempotent per item: existing ``Pane ID:`` / ``Started:`` headers are
    kept as they are, the progress bullet is added only when the
    ``## Progress Log`` section has no bullet carrying ``派遣完了``, and
    ``Status`` moves ``planned`` -> ``active`` only (value-only rewrite of
    the line the runtime reads, state-schema-contract §7). Nothing else in
    the file is touched: line endings and the presence/absence of a
    trailing newline are preserved, ``## `` inside fenced code is not a
    heading, and the file is replaced atomically only if its bytes are
    still what was read (retried a few times; a record that keeps moving
    is reported as ``status: error`` rather than clobbered). A missing
    file is reported (``status: missing``), never raised: a bookkeeping
    side effect must not withhold the gate result.
    """
    path = workers_dir / f"worker-{task_id}.md"
    result: "dict[str, object]" = {"path": str(path), "changes": []}
    if not path.is_file():
        result["status"] = "missing"
        return result

    for _ in range(_WORKER_MD_ATTEMPTS):
        # Bytes, not read_text(): universal-newline decoding would hide CRLF.
        before = path.read_bytes()
        new_text, changes = _rewrite_worker_md(
            before.decode("utf-8"), pane_id, started_at
        )
        if not changes:
            result["status"] = "unchanged"
            return result
        # Lost-update guard: another writer (secretary Progress Log append)
        # may have landed between the read and now. Re-read and redo the
        # edit on the fresh content instead of overwriting it.
        if path.read_bytes() != before:
            continue
        _replace_file_atomically(path, new_text.encode("utf-8"))
        result["status"] = "updated"
        result["changes"] = changes
        return result

    result["status"] = "error"
    result["error"] = (
        f"record changed concurrently {_WORKER_MD_ATTEMPTS} times; not written"
    )
    return result


def _delegate_complete_body(
    task_id: str, worker: str, pane_id: int, peer_id: "int | None", evidence: str
) -> str:
    """The canonical report body. Assembled here, never by the dispatcher.

    The readiness line names *which* evidence established registration, so
    the secretary can see at a glance whether the enumeration was used or
    the weaker degraded probe (spawn-flow 3-4 の縮退) stood in for it.
    """
    if evidence == "list_peers":
        readiness = f"Peer: list_peers 登録確認済み (id={peer_id})"
    else:
        readiness = (
            "Peer: list_peers 列挙は未承認縮退のため破棄。"
            "send_message 送達成功を readiness probe とした (spawn-flow 3-4 縮退経路)"
        )
    return (
        f"DELEGATE_COMPLETE: {task_id} のワーカーを派遣しました。\n"
        f"Pane: {worker} (id={pane_id})\n"
        f"{readiness}\n"
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
        if pane_id is None:
            failures.append("pane_id")

        peer_id = None
        if args.evidence == "list_peers":
            peer_id = _positive_int(args.peer_id)
            if peer_id is None:
                failures.append("peer_id")
            elif (
                args.placement == "background_tab"
                and pane_id is not None
                and peer_id != pane_id
            ):
                # spawn-flow 3-4b opens the background gate only on an exact
                # `bound_pane_id` match. Accepting any positive id here would
                # let a stale same-name/same-cwd worker's peer record certify
                # the freshly spawned pane while that pane stays blocked.
                failures.append("bound_pane_id")
        elif args.peer_id is not None or args.peer_name or args.peer_cwd:
            # `send_delivery` means the enumeration was discarded, so there
            # is no peer record to quote. Accepting one anyway would let a
            # fabricated record ride in under the weaker mode's name.
            failures.append("evidence_mismatch")

        has_run = _has_run_row(conn, task_id)
        checks["run_row"] = has_run
        if not has_run:
            failures.append("run_row")

        sent_dir = _delegate_sent_dir(conn, task_id)
        runs_dir = _runs_dir(conn, task_id)
        expected_dir = sent_dir if sent_dir is not None else runs_dir
        checks["delegate_sent_worker_dir"] = sent_dir
        checks["runs_worker_dir"] = runs_dir
        checks["expected_worker_dir"] = expected_dir
        checks["expected_worker_dir_source"] = (
            DELEGATE_SENT_EVENT if sent_dir is not None else "runs"
        )
        if expected_dir is None:
            failures.append("worker_dir_known")
        elif (
            sent_dir is not None
            and runs_dir is not None
            and _norm_path(sent_dir) != _norm_path(runs_dir)
        ):
            # Step 4 moved runs.worker_dir_id off the T1 value. Whichever
            # side is wrong, the gate has lost its independent reference
            # and must not certify the dispatch.
            failures.append("worker_dir_divergence")

        if args.evidence == "list_peers":
            checks["observed_peer_cwd"] = args.peer_cwd
            checks["expected_peer_name"] = worker
            checks["observed_peer_name"] = args.peer_name
            if expected_dir is not None and _norm_path(
                expected_dir
            ) != _norm_path(args.peer_cwd):
                failures.append("peer_cwd")
            if (args.peer_name or "").strip() != worker:
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
        "peer_cwd": (args.peer_cwd or "").strip() or None,
        "evidence": args.evidence,
        "placement": args.placement,
        "approval": args.approval,
        "instruction": args.instruction,
        "transport": _resolve_transport(args.transport),
    }

    # Idempotent within one spawn: a re-run after a delivery hiccup must not
    # double-record. But a verification that predates the latest
    # `worker_spawned` belongs to an *earlier* dispatch of the same task, and
    # `audit` (rightly) demands one at or after the current spawn — so a
    # redispatch must append a fresh event or it stays unverified forever.
    covers_current_spawn = (
        already_at is not None
        and spawned_at is not None
        and already_at >= spawned_at
    )
    status = "verified"
    if not covers_current_spawn:
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

    # Spawn-flow Step 4 (b)/(c) as a side effect of the gate the dispatcher
    # cannot skip. Runs on the re-verify path too (idempotent), so a record
    # the helper wrote late still gets its fields. Never alters the exit
    # code or the event above: a failed write is reported on stderr and in
    # the JSON, and the gate result stands.
    try:
        worker_record = _record_dispatch_in_worker_md(
            _workers_dir_for(db_path),
            task_id,
            pane_id,
            spawned_at or _utc_now_iso(),
        )
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail the gate
        worker_record = {
            "path": str(_workers_dir_for(db_path) / f"worker-{task_id}.md"),
            "status": "error",
            "error": str(exc),
            "changes": [],
        }
    if worker_record["status"] in ("missing", "error"):
        detail = worker_record.get("error", "file not found")
        print(
            f"spawn_gate: worker record not updated ({detail}): "
            f"{worker_record['path']} — dispatcher must write Pane ID / "
            "Started / 派遣完了 by hand (spawn-flow Step 4)",
            file=sys.stderr,
        )

    print(
        json.dumps(
            {
                "status": status,
                "task": task_id,
                "worker": worker,
                "recorded": payload,
                "worker_record": worker_record,
                "checks": checks,
                "limitations": _ATTESTED_ONLY,
                "delegate_complete": _delegate_complete_body(
                    task_id, worker, pane_id, peer_id, args.evidence
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
        since = (args.since or "").strip() or None
        findings = []
        skipped = {"in_grace": 0, "before_gate_epoch": 0, "terminal_run": 0}
        for task, spawned_at in sorted(spawned.items(), key=lambda kv: kv[1]):
            verified_at = verified.get(task)
            if verified_at is not None and verified_at >= spawned_at:
                continue
            if cutoff is not None and spawned_at > cutoff:
                # Still inside the grace window; the dispatcher may be
                # mid-ceremony. Not a finding yet.
                skipped["in_grace"] += 1
                continue
            if since is not None and spawned_at < since:
                # Predates the gate's deployment, so the missing event says
                # nothing about that dispatch. Fixed cutoff, not a rolling
                # window: anything after it stays reportable forever.
                skipped["before_gate_epoch"] += 1
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
                "since": since,
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
        "--placement",
        default="same_tab",
        choices=("same_tab", "background_tab"),
        help=(
            "where the pane was placed. background_tab (spawn-flow 3-1d/3-2b) "
            "additionally requires --peer-id to equal --pane-id, which is the "
            "bound_pane_id equality 3-4b makes the sole gate-opening key."
        ),
    )
    verify.add_argument(
        "--evidence",
        default="list_peers",
        choices=("list_peers", "send_delivery"),
        help=(
            "how worker readiness was established. list_peers (default) = the "
            "normal 3-4 enumeration; requires --peer-id/--peer-name/--peer-cwd "
            "and gets the machine-checked cwd comparison. send_delivery = the "
            "documented 3-4 degraded path, where the enumeration is discarded "
            "and a successful send_message is the readiness probe; the --peer-* "
            "flags MUST be omitted and no machine check is possible."
        ),
    )
    verify.add_argument(
        "--peer-id",
        default=None,
        help=(
            "numeric id of the record actually observed in list_peers (3-4). "
            "Required for --evidence list_peers, forbidden for send_delivery."
        ),
    )
    verify.add_argument(
        "--peer-name",
        default=None,
        help="name field of that same list_peers record (list_peers evidence only)",
    )
    verify.add_argument(
        "--peer-cwd",
        default=None,
        help="cwd field of that same list_peers record (list_peers evidence only)",
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
        default=None,
        help=(
            "Transport to record on the event. Defaults to the shared "
            "resolver (tools.transport.resolve: explicit > $ORG_TRANSPORT > "
            "DEFAULT_TRANSPORT), not to a literal, so the recorded evidence "
            "cannot drift from the code default that Epic #586 flipped."
        ),
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
        "--since",
        default=GATE_EPOCH,
        help=(
            "Fixed deployment cutoff (ISO-8601 UTC). Spawns before it predate "
            "the gate and are counted under skipped.before_gate_epoch; "
            "everything after it stays reportable no matter how old it gets. "
            "Pass an empty string to disable."
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
