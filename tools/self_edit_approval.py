#!/usr/bin/env python3
"""One-command approval handshake for `.claude/**` self-edit workers.

Why this exists
---------------
``.claude/skills/org-delegate/references/claude-org-self-edit.md`` §5 is
the *only* gate on a self-edit worker's `.claude/**` edits: the
``block-org-structure.sh`` hook is dropped for the
``claude-org-self-edit`` role, ``check-worker-boundary.sh`` allows the
whole WORKER_DIR, and the generated ``settings.local.json`` carries no
rule for `.claude/**`. The auto-mode classifier is what actually stops
an unapproved edit, and the only thing that clears it is an approval
that reaches the worker **as a user message** — i.e. a PTY keystroke,
not a peer ``send_message``.

That handshake was three hand-typed tool calls: send the text, inspect
that it landed, send Enter on its own. The split is not ceremony — text
and Enter in one call makes the text a bracketed paste that swallows the
trailing Enter, so the approval stays in the composer as an unsent
draft. (Confirmed in renga's own source: ``write_input_to_pane``
writes ``data`` and then ``b"\\r"`` back to back with no settle delay for
Claude panes — ``renga/src/app/codex_peer.rs:196-203``.)

The failure mode is that **step 3 is silent when omitted, and looks
exactly like success**:

* ``inspect_pane`` still shows the approval text — it is sitting in the
  input box, which is what "delivered" looks like to a reader.
* The worker never received a user message, so it waits. From the
  secretary's side that is indistinguishable from a worker at work.
* Nothing is written anywhere, so no audit can find it afterwards.

Observed twice: 2026-07-31 (task ``ja-registry-template-001``) and
2026-08-25 (task ``pr-merged-double-entry-fix``), the second time with
the secretary explaining to the user that leaving the text in the input
box was correct. A human caught it both times.

This tool takes the same shape as ``tools/spawn_gate.py``, which fixed
the structurally identical problem on the dispatcher side (a ceremony
that was cheaper to skip than to perform, and carried no detection
risk):

1. **``send`` performs the whole handshake and fails loud at each
   stage.** The secretary runs one command. The approval text is
   assembled here, so the three mandatory elements (file enumeration /
   task_id / the explicit "窓口経由のユーザー承認" wording) cannot be
   dropped by composing it freehand. Landing and submission are each
   verified against a fresh ``inspect``, and a stage that cannot be
   verified exits non-zero instead of reporting success.
2. **``audit`` makes omission detectable after the fact.** ``send``
   records ``self_edit_approval_sent``; ``audit`` lists self-edit
   dispatches with no such row.

What is actually verified vs. merely observed
---------------------------------------------
**Verified** (the caller cannot satisfy these by asserting them):

* The approval text is present in the worker's composer after the text
  write — read back from a fresh ``inspect`` of the pane, not from the
  write's own exit code.
* The approval text is *gone from the composer* after the Enter write.
  That is what separates a submitted message from a resident draft, and
  it is the exact discrimination the manual procedure could not make.

**Not verified**: that the worker's session then *processed* the
message. No process on this host can observe another agent's turn. A
submitted message can still be queued behind a running turn — which is
fine and expected (an Enter into a busy pane is queued, not lost), and
is why this tool deliberately does **not** wait for the pane to be idle
before sending.

Backends
--------
The keystroke path is the terminal multiplexer's, not the MCP server's,
so it is selected from the resolved transport
(:func:`tools.transport.resolve` — explicit > ``ORG_TRANSPORT`` env >
``DEFAULT_TRANSPORT``), the same SoT every other ja consumer reads
(the shape ``tools/peer_notify.py`` adopted in Refs #941; no raw-env
check):

* ``renga``  -> ``renga send`` / ``renga inspect``.
* ``broker`` -> ``/usr/bin/tmux -L claude-org-broker send-keys /
  capture-pane``, since broker panes are detached tmux sessions
  (``.claude/skills/org-attach/SKILL.md``) and
  ``claude-org-runtime broker`` exposes only ``serve`` / ``send`` — it
  has no keystroke subcommand. On this backend the pane must be named
  explicitly (``--target %N``): the logical ``worker-{task_id}`` lives
  only in broker's own registry, reachable through
  ``mcp__org-broker__list_panes`` (an MCP tool, so not callable from a
  CLI), and tmux has nothing to match it against. The default is
  refused up front rather than failing after the fact.

**Verification status**: the renga path was exercised against a live
pane while this was written (text write lands in the composer; the
composer renders as ``❯`` between two ``─`` fences and reads back empty
once cleared). The tmux path is built to the interface documented in
``org-attach`` and covered by unit tests with a fake runner, but no tmux
backend was running on this host, so it has **not** been driven against
a live broker pane.

Machine-readable contract
-------------------------
Both subcommands print one JSON object on stdout and branch on the exit
code, never on parsing the JSON (same convention as
``tools/spawn_gate.py`` / ``tools/check_curate_threshold.py``):

* ``0``  — ``send``: handshake completed and recorded. ``audit``: no
  missing approvals.
* ``10`` — ``send``: a stage did not verify; ``failures[]`` names it and
  ``remedy[]`` says what to do. **No approval is claimed.**
  ``audit``: at least one self-edit dispatch with no approval row.
* ``2``  — error (bad arguments, unreadable DB, backend binary missing).

``10`` rather than ``1`` so an unexpected traceback (which exits ``1``)
can never be misread as a verdict.

Usage
-----
::

    python3 tools/self_edit_approval.py send \\
        --task self-edit-approval-gate \\
        --file .claude/skills/org-delegate/SKILL.md.in \\
        --file docs/journal-events.md

    python3 tools/self_edit_approval.py audit --older-than-min 5
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

# Make `tools.*` importable when invoked directly (no prior `pip install
# -e .`), same shim as tools/spawn_gate.py and tools/journal_append.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_FIRE = 10

APPROVAL_EVENT = "self_edit_approval_sent"
DELEGATE_SENT_EVENT = "delegate_sent"

#: Deployment cutoff for ``audit``. Self-edit dispatches before this
#: predate the tool, so their missing ``self_edit_approval_sent`` proves
#: nothing and would bury the live findings.
#:
#: A **fixed** date, not a rolling window — the same reasoning as
#: ``spawn_gate.GATE_EPOCH``: a rolling horizon silently drops a still-
#: actionable finding the moment it ages out, and every later cycle then
#: reports "clean" while the gap is still real. **UTC**, like every
#: ``occurred_at``.
GATE_EPOCH = "2026-08-25T00:00:00.000Z"

#: Run states in which a missing approval is no longer actionable: the
#: run is over, so reporting it would only bury live findings. Mirrors
#: ``spawn_gate._TERMINAL_RUN_STATUS``.
_TERMINAL_RUN_STATUS = ("completed", "failed", "abandoned")

_RENGA = "renga"
_BROKER = "broker"
_TMUX_BIN = "/usr/bin/tmux"
_TMUX_SOCKET = "claude-org-broker"

#: Box-drawing character renga/Claude Code draw the composer fences with.
_FENCE_CHARS = set("─━-")
#: Prompt markers that open the composer row.
_PROMPT_MARKERS = ("❯", ">")

_REMEDY = {
    "prior_draft": (
        "送信前の時点で worker の入力欄に別の draft が残っている。"
        "このまま text を送ると承認文が既存 draft に連結され、"
        "必須 3 要素が壊れた文面が submit される。"
        "先に入力欄を空にしてから (Ctrl-U 相当) 再実行する。"
    ),
    "text_not_landed": (
        "text を書き込んだが、入力欄に承認文が現れない。"
        "ペイン名 / pane id が間違っているか、"
        "対象ペインが Claude Code の入力欄を出していない可能性がある。"
        "observed_composer を見て対象を確認し直す。"
        "承認は送られていないので、worker はまだ待機している。"
    ),
    "not_submitted": (
        "Enter を送ったが承認文が入力欄に残ったまま = submit されていない。"
        "これが 2026-07-31 / 2026-08-25 に無言で起きた失敗そのもの。"
        "入力欄に残った draft を消してから再実行する。"
        "**承認は届いていない。届いたものとして扱わないこと。**"
    ),
    "enter_ambiguous": (
        "Enter 送信がタイムアウトした。打鍵が PTY に届いたかどうかが確定しない。"
        "**再送する前に必ず worker ペインを inspect すること** — "
        "既に submit されていれば再送は承認の二重送信になり、"
        "入力欄に draft が残っていればそれを消してから送り直す。"
    ),
    "submit_unverified": (
        "Enter は送信できたが、その後の入力欄確認 (inspect) が失敗したため "
        "submit されたかを確認できない。**再送する前に必ず worker ペインを "
        "inspect すること** — 既に submit されていれば再送は二重送信になり、"
        "入力欄に draft が残っていればそれを消してから送り直す。"
        "なお記帳 (self_edit_approval_sent) はしていないので、"
        "submit 済みだった場合は audit に穴として出る。"
    ),
    "backend_failed": (
        "打鍵バックエンドの呼び出しが失敗した。stderr を見て、"
        "renga なら RENGA_SOCKET とペイン名、broker なら tmux socket "
        f"({_TMUX_SOCKET}) と pane id を確認する。"
    ),
}

#: Restated on every ``send`` result so the boundary travels with the
#: evidence instead of living only in prose a reader may not open.
_LIMITATIONS = (
    "本ツールが検証するのは (a) 承認文が入力欄に着弾したこと と "
    "(b) Enter 後に入力欄から消えた = submit されたこと の 2 点。"
    "worker がそのターンを実際に処理したかは観測できない "
    "(busy ペインへの Enter はキューされるのが正常で、"
    "そのため本ツールは idle 待ちをしない)。"
)


class ApprovalError(Exception):
    """Unrecoverable problem: report status=error / exit 2."""


# ---------------------------------------------------------------------------
# Approval text
# ---------------------------------------------------------------------------

def build_approval_text(task_id: str, files: "list[str]") -> str:
    """Assemble the approval message. Never composed by the caller.

    The wording tracks the template in
    ``claude-org-self-edit.md`` §5 and carries all three mandatory
    elements: the file enumeration, the ``task_id``, and the explicit
    statement that this is a user approval relayed by the secretary.

    Deliberately a **single line**: a literal newline in the payload is
    a mid-string submit in the Claude Code composer (the same trap
    ``.dispatcher/references/spawn-flow.md`` 3-5a warns about for
    ultracode kickoffs), which would submit a truncated approval.
    """
    joined = " / ".join(files)
    return (
        f"承認します: 本タスク ({task_id}) における {joined} の編集を承認します。"
        "これは窓口経由のユーザー承認です。"
    )


# ---------------------------------------------------------------------------
# Composer parsing
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Whitespace-insensitive form for comparing rendered screen text.

    The renderer does not preserve the byte sequence that was written:
    a live probe of ``renga inspect`` came back as
    ``❯\\xa0PROBE-A-956text-only(selftest,ignore)`` for text sent as
    ``PROBE-A-956 text-only (self test, ignore)`` — the prompt is
    followed by NBSP and the interior spaces are gone. Comparing on a
    whitespace-stripped, NFKC-normalised form is therefore the only
    stable way to ask "is my string on screen".
    """
    folded = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in folded if not ch.isspace())


def _is_fence(text: str) -> bool:
    stripped = text.strip()
    # A fence is a long run of box-drawing rule characters. The length
    # floor keeps a stray "-" in prose from being read as a fence.
    return len(stripped) >= 8 and set(stripped) <= _FENCE_CHARS


def extract_composer(lines: "list[dict]") -> "str | None":
    """Return the composer's current contents, or ``None`` if not found.

    The Claude Code composer renders as a prompt row bracketed by two
    rule fences::

        ────────────────────────────────────────
        ❯ some draft text that wraps onto
          the following row
        ────────────────────────────────────────
         ⏵⏵ auto mode on ...

    So: find the **last** prompt row (the live composer, not an echoed
    transcript line above it), then take that row plus the rows after it
    until the closing fence. Long text wraps, which is why the trailing
    rows must be included — checking only the prompt row would miss the
    tail of a wrapped approval and could pass a truncated write.
    """
    idx = None
    for i, line in enumerate(lines):
        text = (line.get("text") or "").strip()
        if any(text.startswith(m) for m in _PROMPT_MARKERS):
            idx = i
    if idx is None:
        return None

    head = (lines[idx].get("text") or "").strip()
    for marker in _PROMPT_MARKERS:
        if head.startswith(marker):
            head = head[len(marker):]
            break
    parts = [head]
    for line in lines[idx + 1:]:
        text = line.get("text") or ""
        if _is_fence(text):
            break
        parts.append(text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _run(cmd: "list[str]", timeout: float) -> subprocess.CompletedProcess:
    """Run a backend call, mapping every failure to :class:`BackendFailure`.

    Deliberately **not** ``ApprovalError``: these happen mid-handshake, so
    they must reach ``cmd_send``'s gate-failure path (exit 10, with the
    stage that failed and the delivery status) rather than surfacing as a
    generic exit 2 that says nothing about whether the approval landed.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise BackendFailure(f"backend binary not found: {cmd[0]} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        # The subprocess may have written some or all of its bytes before
        # the timeout, so the caller has to treat this as *unknown*, not
        # as "nothing happened".
        raise BackendFailure(
            f"backend call timed out: {' '.join(cmd)}", timed_out=True
        ) from exc


class RengaBackend:
    """Drive a renga pane through the ``renga`` CLI.

    ``renga send`` writes the text to the pane's PTY and, with
    ``--enter``, appends ``\\r`` immediately afterwards
    (``renga/src/app/codex_peer.rs:196-203``). That back-to-back write is
    exactly the paste-absorption condition, so this backend never passes
    ``--enter`` together with text: the two writes are separate process
    invocations with a verifying ``inspect`` round-trip between them.
    """

    name = _RENGA

    def __init__(self, target: str, pane_id: "int | None", timeout: float,
                 binary: str = "renga"):
        self.target = target
        self.pane_id = pane_id
        self.timeout = timeout
        self.binary = binary

    def _selector(self) -> "list[str]":
        if self.pane_id is not None:
            return ["--id", str(self.pane_id)]
        return ["--name", self.target]

    def describe(self) -> str:
        return f"renga {' '.join(self._selector())}"

    def send_text(self, text: str) -> None:
        proc = _run([self.binary, "send", text, *self._selector()], self.timeout)
        if proc.returncode != 0:
            raise BackendFailure(f"renga send (text) exit={proc.returncode}: "
                                 f"{proc.stderr.strip()}")

    def send_enter(self) -> None:
        # Empty positional text + --enter writes zero bytes and then a
        # bare CR, so this is an Enter and nothing else.
        proc = _run([self.binary, "send", "", *self._selector(), "--enter"],
                    self.timeout)
        if proc.returncode != 0:
            raise BackendFailure(f"renga send (enter) exit={proc.returncode}: "
                                 f"{proc.stderr.strip()}")

    def inspect(self, lines: int) -> "list[dict]":
        proc = _run(
            [self.binary, "inspect", *self._selector(), "--lines", str(lines)],
            self.timeout,
        )
        if proc.returncode != 0:
            raise BackendFailure(f"renga inspect exit={proc.returncode}: "
                                 f"{proc.stderr.strip()}")
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            raise BackendFailure(f"renga inspect returned non-JSON: {exc}") from exc
        rows = payload.get("lines")
        if not isinstance(rows, list):
            raise BackendFailure("renga inspect payload has no 'lines' array")
        return rows


class TmuxBackend:
    """Drive a broker pane through tmux.

    Broker panes are detached tmux sessions on the ``claude-org-broker``
    socket (``.claude/skills/org-attach/SKILL.md``), and
    ``claude-org-runtime broker`` exposes only ``serve`` / ``send`` — no
    keystroke subcommand — so tmux is the keystroke path on that
    transport.

    ``send-keys -l`` sends the text literally (no key-name parsing, no
    Enter); Enter is a second invocation, keeping the same two-write
    split as the renga backend.

    The absolute ``/usr/bin/tmux`` is the org convention: bare ``tmux``
    is alias-shadowed under oh-my-zsh, which drops the ``-L`` socket
    selection (``org-attach`` "なぜ ``/usr/bin/tmux`` 絶対パス必須か").

    **Not exercised against a live broker pane** — no tmux backend was
    running on this host when this was written. Unit tests cover the
    argv construction and the capture-pane parsing with a fake runner.
    """

    name = _BROKER

    def __init__(self, target: str, timeout: float, socket: str = _TMUX_SOCKET,
                 binary: str = _TMUX_BIN):
        self.target = target
        self.timeout = timeout
        self.socket = socket
        self.binary = binary

    def _base(self) -> "list[str]":
        return [self.binary, "-L", self.socket]

    def describe(self) -> str:
        return f"tmux -L {self.socket} -t {self.target}"

    def send_text(self, text: str) -> None:
        proc = _run([*self._base(), "send-keys", "-t", self.target, "-l", text],
                    self.timeout)
        if proc.returncode != 0:
            raise BackendFailure(f"tmux send-keys (text) exit={proc.returncode}: "
                                 f"{proc.stderr.strip()}")

    def send_enter(self) -> None:
        proc = _run([*self._base(), "send-keys", "-t", self.target, "Enter"],
                    self.timeout)
        if proc.returncode != 0:
            raise BackendFailure(f"tmux send-keys (enter) exit={proc.returncode}: "
                                 f"{proc.stderr.strip()}")

    def inspect(self, lines: int) -> "list[dict]":
        proc = _run([*self._base(), "capture-pane", "-p", "-t", self.target],
                    self.timeout)
        if proc.returncode != 0:
            raise BackendFailure(f"tmux capture-pane exit={proc.returncode}: "
                                 f"{proc.stderr.strip()}")
        rendered = proc.stdout.splitlines()
        tail = rendered[-lines:] if lines > 0 else rendered
        # Normalise to the renga row shape so the composer parser is
        # shared rather than duplicated per backend.
        first_row = len(rendered) - len(tail)
        return [{"row": first_row + i, "text": t} for i, t in enumerate(tail)]


class BackendFailure(Exception):
    """A backend call failed; maps to a gate failure, never to a crash.

    ``timed_out`` matters because a timeout is the one failure whose
    effect is unknown: the write may already have reached the PTY. On the
    Enter write that is the difference between "not delivered, safe to
    retry" and "possibly delivered, re-sending would duplicate it".
    """

    def __init__(self, message: str, *, timed_out: bool = False):
        super().__init__(message)
        self.timed_out = timed_out


def _resolve_transport(explicit: "str | None") -> str:
    """Shared resolver first; a literal only if the runtime import fails.

    ``tools.transport`` re-exports runtime's ``DEFAULT_TRANSPORT``, which
    Epic #586 flipped renga -> broker, so hard-coding a default here
    would aim the keystrokes at the wrong multiplexer in the default
    configuration — the same defect ``tools/peer_notify.py`` carried
    until Refs #941.

    An **unknown** value is a configuration error, not something to fall
    back from. ``peer_notify`` may degrade quietly because a dropped
    notification is decoration on top of a canonical row; here the value
    picks which multiplexer receives the keystrokes, so quietly treating
    "not exactly ``broker``" as renga would type an approval into the
    wrong terminal and then record the invalid value as evidence.
    """
    try:
        from tools.transport import resolve as _resolve
    except Exception:  # noqa: BLE001 — runtime not installed / not importable
        value = explicit or os.environ.get("ORG_TRANSPORT") or _RENGA
        if value not in (_RENGA, _BROKER):
            raise ApprovalError(
                f"unknown transport {value!r} (expected {_RENGA} or {_BROKER})"
            )
        return value
    try:
        return str(_resolve(explicit))
    except ValueError as exc:
        raise ApprovalError(f"unknown transport: {exc}") from exc


def _broker_state_dir(override: "str | None") -> str:
    """Where the broker daemon publishes ``daemon.json``.

    ``ORG_BROKER_STATE_DIR`` is injected into pane envs when the daemon
    runs on a non-default state dir (paired contract, runtime #122 — the
    same variable ``tools/peer_notify.py`` forwards to ``broker send``).
    """
    if override:
        return override
    from_env = os.environ.get("ORG_BROKER_STATE_DIR")
    if from_env:
        return from_env
    from tools.state_db.discover import resolve_state_db_path

    return str(Path(resolve_state_db_path(None)).resolve().parent / "broker")


def _broker_adapter(state_dir_override: "str | None") -> "str | None":
    """The broker daemon's **resolved** terminal adapter, or ``None``.

    ``broker`` is a transport, not a terminal: the daemon drives one of
    several terminal adapters (``tmux`` / ``wezterm`` per
    ``docs/contracts/backend-interface-contract.md`` Surface 8, plus
    herdr). Only the tmux one has a CLI keystroke path, so the adapter
    has to be known before typing anything.

    The daemon publishes it in ``<state_dir>/daemon.json``
    (``claude_org_runtime.broker.sidecar.write_sidecar`` records the
    resolved value, not the requested one, exactly so readers can match
    on it). ``None`` means "could not determine" — no daemon file, no
    runtime install, or a malformed record.
    """
    try:
        from claude_org_runtime.broker.sidecar import read_sidecar
    except Exception:  # noqa: BLE001 — runtime not installed
        return None
    try:
        data = read_sidecar(_broker_state_dir(state_dir_override))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    adapter = data.get("backend")
    return adapter if isinstance(adapter, str) and adapter else None


def make_backend(args, transport: str, target_defaulted: bool = False):
    """Build the keystroke backend, refusing an unaddressable tmux target.

    On renga a pane *name* is a first-class selector, so the
    ``worker-{task_id}`` default addresses the worker directly.

    On broker it does not. Broker panes are detached tmux sessions named
    ``claude-org-broker-{pid}-{seq}``, and the logical worker name exists
    only in broker's own pane registry — reachable through
    ``mcp__org-broker__list_panes``, which is an MCP tool and therefore
    not callable from a CLI. tmux itself carries nothing to match the
    logical name against, so handing ``worker-{task_id}`` to ``tmux -t``
    can only fail. Refusing here turns that into an actionable
    configuration error *before* anything is typed, instead of a
    ``backend_failed`` after the fact.

    Deliberately **not** auto-resolved from a recorded ``pane_id``: this
    backend could not be exercised against a live broker pane, and a
    wrong guess here types an approval into someone else's terminal.
    Refusing is the safe direction; the secretary already has
    ``list_panes`` open at this point in the flow.
    """
    explicit = bool(args.backend) and args.backend != "auto"
    if explicit:
        # `broker` is kept as an alias for `tmux`: naming the transport
        # here used to be the only way to ask for the tmux driver.
        chosen = _RENGA if args.backend == _RENGA else "tmux"
    else:
        chosen = "tmux" if transport == _BROKER else _RENGA

    if chosen == "tmux":
        if not explicit:
            # Auto-selection must confirm the adapter rather than assume
            # it: a wezterm/herdr deployment has no pane on the tmux
            # socket at all, so driving tmux there would fail obscurely
            # (or, worse, hit an unrelated tmux server).
            adapter = _broker_adapter(args.broker_state_dir)
            if adapter is None:
                raise ApprovalError(
                    "broker daemon の terminal adapter を daemon.json から確認できない "
                    f"(state dir: {_broker_state_dir(args.broker_state_dir)})。"
                    "daemon が起動しているか確認するか、tmux であると分かっているなら "
                    "--backend tmux を明示する。"
                )
            if adapter != "tmux":
                raise ApprovalError(
                    f"broker の terminal adapter は {adapter!r} で、CLI から打鍵する経路が無い "
                    "(本ツールが駆動できるのは renga CLI と tmux のみ)。"
                    "この構成では承認ハンドシェイクを手順書 §5 の手動 3 段で行い、"
                    "**Enter を単独で送ったこと**を inspect で必ず確認すること。"
                )
        if target_defaulted:
            raise ApprovalError(
                f"broker (tmux) backend needs an explicit tmux target: "
                f"{args.target!r} is the logical worker name, which tmux "
                f"cannot resolve. Read the worker's pane id (%N) from "
                f"mcp__org-broker__list_panes and pass it as "
                f"--target %N (or force the renga backend with --backend renga)."
            )
        socket = (args.tmux_socket or os.environ.get("ORG_BROKER_SOCKET")
                  or _TMUX_SOCKET)
        return TmuxBackend(args.target, args.call_timeout, socket=socket)
    return RengaBackend(args.target, args.pane_id, args.call_timeout)


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

def _poll_composer(backend, lines: int, predicate, timeout: float,
                   interval: float) -> "tuple[bool, str | None]":
    """Re-``inspect`` until ``predicate`` holds or ``timeout`` elapses.

    PTY rendering is asynchronous, so a single inspect immediately after
    a write races the redraw. Returns the last observed composer either
    way, so a failure can report what was actually on screen.
    """
    deadline = time.monotonic() + timeout
    observed: "str | None" = None
    while True:
        observed = extract_composer(backend.inspect(lines))
        if predicate(observed):
            return True, observed
        if time.monotonic() >= deadline:
            return False, observed
        time.sleep(interval)


def _fail(task_id: str, check: str, extra: dict) -> int:
    body = {
        "status": "gate_failed",
        "task": task_id,
        "failures": [check],
        "remedy": [_REMEDY[check]],
        "approval_delivered": False,
    }
    body.update(extra)
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return EXIT_FIRE


def cmd_send(args) -> int:
    task_id = args.task.strip()
    if not task_id:
        raise ApprovalError("--task must not be empty")

    files = [f.strip() for f in (args.file or []) if f.strip()]
    if not files:
        # The file enumeration is one of the three mandatory elements of
        # the approval, and an approval that lists nothing authorises
        # nothing. Refuse rather than send a vacuous one.
        raise ApprovalError(
            "--file is required (at least one): the approval must enumerate "
            "the files it authorises (claude-org-self-edit.md §5)"
        )
    for f in files:
        if "\n" in f or "\r" in f:
            raise ApprovalError(f"--file must not contain a newline: {f!r}")

    target_defaulted = not args.target
    if target_defaulted:
        args.target = f"worker-{task_id}"

    transport = _resolve_transport(args.transport)
    backend = make_backend(args, transport, target_defaulted=target_defaulted)
    text = build_approval_text(task_id, files)
    wanted = _norm(text)

    common = {
        "task": task_id,
        "worker": f"worker-{task_id}",
        "pane": args.target,
        "files": files,
        "backend": backend.name,
        "transport": transport,
        "target": backend.describe(),
        "approval_text": text,
    }

    if args.dry_run:
        print(json.dumps(
            {"status": "dry_run", **common,
             "note": "何も送信していない。--dry-run を外すと送信する。"},
            ensure_ascii=False, indent=2))
        return EXIT_OK

    # Fail on an unusable state.db *before* typing anything. Discovering
    # it only at the append leaves the approval delivered but unrecorded,
    # which is the one outcome with no clean recovery: `audit` reports a
    # gap that is not real, and a re-run would submit a second approval.
    _precheck_db(args.db_path)

    try:
        # Stage 0 — refuse to append onto an existing draft. Writing the
        # approval after a resident draft concatenates the two, and the
        # result is submitted as one message whose mandatory elements no
        # longer parse as an approval.
        pre = extract_composer(backend.inspect(args.lines))
        if pre is not None and _norm(pre):
            return _fail(task_id, "prior_draft",
                         {**common, "observed_composer": pre})

        # Stage 1 — write the text. No Enter: see RengaBackend docstring.
        backend.send_text(text)

        # Stage 2 — verify it actually landed in the composer.
        landed, observed = _poll_composer(
            backend, args.lines, lambda c: c is not None and wanted in _norm(c),
            args.verify_timeout, args.poll_interval)
        if not landed:
            return _fail(task_id, "text_not_landed",
                         {**common, "observed_composer": observed})

        # Stage 3 — Enter on its own.
        try:
            backend.send_enter()
        except BackendFailure as exc:
            if not exc.timed_out:
                raise
            # The write may have reached the PTY before the timeout, so
            # neither "delivered" nor "not delivered" can be claimed. Say
            # so: a blind retry here is what duplicates an approval.
            return _fail(task_id, "enter_ambiguous",
                         {**common, "error": str(exc),
                          "approval_delivered": "unknown"})

        # Stage 4 — verify the composer no longer holds it. An empty
        # composer, or one holding something else, both mean the draft
        # left the input box; the text still sitting there is precisely
        # the silent failure this tool exists to stop.
        #
        # A backend failure *here* is not "not delivered": the Enter write
        # already returned, so the message may well be submitted and only
        # the confirmation is missing. Reporting that as a plain failure
        # would invite the retry that duplicates the approval.
        try:
            submitted, after = _poll_composer(
                backend, args.lines,
                lambda c: c is None or wanted not in _norm(c),
                args.verify_timeout, args.poll_interval)
        except BackendFailure as exc:
            return _fail(task_id, "submit_unverified",
                         {**common, "error": str(exc),
                          "approval_delivered": "unknown"})
        if not submitted:
            return _fail(task_id, "not_submitted",
                         {**common, "observed_composer": after})
    except BackendFailure as exc:
        return _fail(task_id, "backend_failed", {**common, "error": str(exc)})

    verified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "task": task_id,
        "pane": args.target,
        "files": files,
        "verified_at": verified_at,
        "backend": backend.name,
        "transport": transport,
    }
    try:
        _append_event(payload, args.db_path)
    except BaseException as exc:  # noqa: BLE001
        # `SystemExit` included on purpose: `verify_or_exit` reports a bad
        # schema by calling `sys.exit`, and letting that propagate here
        # would drop the "delivered but unrecorded" report on the floor —
        # the operator would see a bare exit and might re-send.
        # The approval *was* delivered; only the record failed. Say both,
        # and do not exit 0 — a missing row is what `audit` reports.
        print(json.dumps(
            {"status": "error", "task": task_id, "approval_delivered": True,
             "error": f"承認は送信・submit 済みだが {APPROVAL_EVENT} の記帳に失敗: {exc}",
             "recorded": payload},
            ensure_ascii=False, indent=2))
        return EXIT_ERROR

    print(json.dumps(
        {"status": "approved", **common, "approval_delivered": True,
         "composer_after_submit": after,
         "recorded": payload, "limitations": _LIMITATIONS},
        ensure_ascii=False, indent=2))
    return EXIT_OK


def _precheck_db(db_override: "str | None") -> None:
    """Resolve and schema-check state.db before any keystroke is sent.

    Uses ``verify_state_db_schema`` rather than ``verify_or_exit``: the
    latter reports by writing to stderr and calling ``sys.exit``, which
    would leave ``send`` emitting no JSON object at all and break the
    machine-readable contract this tool's callers branch on. Every
    failure here is turned into an ``ApprovalError`` (status=error /
    exit 2) instead — and it happens before any keystroke, so an
    unusable DB never produces a delivered-but-unrecorded approval.
    """
    from tools.state_db import connect
    from tools.state_db.discover import (
        StateDbSchemaError,
        resolve_state_db_path,
        verify_state_db_schema,
    )

    try:
        db_path = Path(resolve_state_db_path(db_override))
    except Exception as exc:  # noqa: BLE001
        raise ApprovalError(f"could not resolve state.db path: {exc}") from exc
    try:
        conn = connect(db_path)
    except Exception as exc:  # noqa: BLE001 — missing file, locked, corrupt
        raise ApprovalError(f"could not open state.db at {db_path}: {exc}") from exc
    try:
        verify_state_db_schema(db_path, conn=conn)
    except StateDbSchemaError as exc:
        raise ApprovalError(f"state.db schema check failed: {exc}") from exc
    finally:
        conn.close()


def _append_event(payload: dict, db_override: "str | None") -> None:
    from tools.state_db import connect
    from tools.state_db.discover import resolve_state_db_path, verify_or_exit
    from tools.state_db.writer import StateWriter

    db_path = Path(resolve_state_db_path(db_override))
    conn = connect(db_path)
    try:
        verify_or_exit(db_path, conn=conn, prog="tools/self_edit_approval.py")
        writer = StateWriter(conn)
        writer.append_event(kind=APPROVAL_EVENT, actor="secretary", payload=payload)
        writer.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def _resolve_db(cli_override: "str | None") -> Path:
    from tools.state_db.discover import resolve_state_db_path

    try:
        return Path(resolve_state_db_path(cli_override))
    except Exception as exc:  # pragma: no cover - discovery failure
        raise ApprovalError(f"could not resolve state.db path: {exc}") from exc


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise ApprovalError(f"state.db not found at {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ApprovalError(f"could not open {db_path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _norm_path(value: "str | None") -> "str | None":
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return os.path.normpath(stripped).rstrip(os.sep) or os.sep


def _is_self_edit_dir(worker_dir: str, claude_org_root: str) -> bool:
    """Whether a dispatch's worker dir marks it as a claude-org self-edit.

    The ``claude-org-self-edit`` role is **not** persisted anywhere: the
    delegate plan carries it (``send_plan.json``: ``"role":
    "claude-org-self-edit"``) but ``delegate_sent``'s payload is only
    ``task`` / ``worker`` / ``dir`` and ``runs`` has no role column. What
    *is* persisted is the consequence of the role: it is exactly what
    routes the worker dir into the claude-org repo itself
    (``tools/resolve_worker_layout.py`` ``decide_role`` ->
    ``self_edit`` -> Pattern B ``live_repo_worktree``, i.e.
    ``<claude-org root>/.worktrees/<task>``), whereas every other project
    lands under a ``workers/`` tree. So containment in the claude-org
    root is the durable trace of the role.
    """
    norm_dir = _norm_path(worker_dir)
    norm_root = _norm_path(claude_org_root)
    if norm_dir is None or norm_root is None:
        return False
    return norm_dir == norm_root or norm_dir.startswith(norm_root + os.sep)


def _events_by_task(conn: sqlite3.Connection, kind: str) -> "dict[str, tuple[str, dict]]":
    """Newest event of ``kind`` per task, as ``{task: (occurred_at, payload)}``."""
    out: "dict[str, tuple[str, dict]]" = {}
    for ev in conn.execute(
        "SELECT occurred_at, payload_json FROM events WHERE kind = ? "
        "ORDER BY occurred_at ASC, id ASC",
        (kind,),
    ):
        try:
            payload = json.loads(ev["payload_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        task = payload.get("task")
        if isinstance(task, str) and task:
            # Last wins: a re-dispatch of the same task needs its own
            # approval, so an older one must not cover it.
            out[task] = (ev["occurred_at"], payload)
    return out


def cmd_audit(args) -> int:
    db_path = _resolve_db(args.db_path)
    root = args.claude_org_root or str(db_path.resolve().parent.parent)
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
        dispatched = _events_by_task(conn, DELEGATE_SENT_EVENT)
        approved = _events_by_task(conn, APPROVAL_EVENT)

        cutoff = _cutoff_iso(args.older_than_min)
        since = (args.since or "").strip() or None
        findings = []
        skipped = {"not_self_edit": 0, "in_grace": 0,
                   "before_gate_epoch": 0, "terminal_run": 0}
        for task, (sent_at, payload) in sorted(dispatched.items(),
                                               key=lambda kv: kv[1][0]):
            if not _is_self_edit_dir(str(payload.get("dir") or ""), root):
                skipped["not_self_edit"] += 1
                continue
            approved_at = approved.get(task, (None, None))[0]
            if approved_at is not None and approved_at >= sent_at:
                continue
            if cutoff is not None and sent_at > cutoff:
                skipped["in_grace"] += 1
                continue
            if since is not None and sent_at < since:
                skipped["before_gate_epoch"] += 1
                continue
            if task in terminal_tasks:
                skipped["terminal_run"] += 1
                continue
            findings.append({
                "task": task,
                "worker": f"worker-{task}",
                "worker_dir": payload.get("dir"),
                "delegate_sent_at": sent_at,
                "last_approved_at": approved_at,
                "note": (
                    f"self-edit 派遣に対応する {APPROVAL_EVENT} が無い。"
                    "承認ハンドシェイクを踏まずに派遣したか、"
                    "手動 3 段の Enter を落とした可能性がある。"
                    "worker ペインを inspect し、必要なら "
                    "tools/self_edit_approval.py send で送り直す。"
                ),
            })
    except sqlite3.Error as exc:
        raise ApprovalError(f"state.db read failed: {exc}") from exc
    finally:
        conn.close()

    print(json.dumps({
        "status": "missing_approvals" if findings else "clean",
        "claude_org_root": root,
        "grace_minutes": args.older_than_min,
        "since": since,
        "finding_count": len(findings),
        # Surfaced, never silent: a reader must be able to tell "nothing
        # is wrong" from "the filters ate everything".
        "skipped": skipped,
        "findings": findings,
        "scope_note": (
            "対象は worker dir が claude-org root 配下にある派遣 "
            "(= claude-org-self-edit ロールの持続的な痕跡)。"
            "ロール自体は state.db に残らないため、この判定は **superset** で、"
            "self-edit ロールでも .claude/** を触らなかった派遣は "
            "false positive として出る。承認不要だったことを確認したら潰してよい。"
        ),
    }, ensure_ascii=False, indent=2))
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/self_edit_approval.py",
        description=(
            "Run the .claude/** self-edit approval handshake as one command, "
            "and audit dispatches that never got one."
        ),
    )
    parser.add_argument("--db-path", default=None,
                        help="Override the resolved state.db path (tests / debugging).")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser(
        "send",
        help=("Send the approval to a worker pane (text -> verify landed -> "
              "Enter -> verify submitted) and record self_edit_approval_sent."),
    )
    send.add_argument("--task", required=True, help="task_id")
    send.add_argument(
        "--file", action="append", default=[], metavar="PATH",
        help=("File the approval authorises; repeat per file. At least one is "
              "required - the enumeration is a mandatory element of the "
              "approval, so an empty one is refused."),
    )
    send.add_argument("--target", default=None,
                      help="Pane name (renga) or tmux target. Default worker-<task>.")
    send.add_argument("--pane-id", type=int, default=None,
                      help="Numeric renga pane id; use instead of --target when "
                           "the name would resolve in the wrong tab.")
    send.add_argument("--backend", default="auto",
                      choices=("auto", "renga", "tmux", "broker"),
                      help="Keystroke backend. auto (default) follows the "
                           "resolved transport: renga -> renga CLI; broker -> "
                           "tmux, but only after confirming from daemon.json "
                           "that the daemon's terminal adapter really is tmux. "
                           "tmux (alias: broker) skips that check and drives "
                           "tmux directly.")
    send.add_argument("--transport", default=None,
                      help="Transport to resolve/record. Defaults to the shared "
                           "resolver (tools.transport.resolve: explicit > "
                           "$ORG_TRANSPORT > DEFAULT_TRANSPORT), not a literal.")
    send.add_argument("--tmux-socket", default=None,
                      help="tmux -L socket for the broker backend. Defaults to "
                           f"$ORG_BROKER_SOCKET, then {_TMUX_SOCKET} - the same "
                           "resolution tools/org-dispatcher-view.sh uses, so a "
                           "deployment that moved the socket does not need an "
                           "extra flag here.")
    send.add_argument("--broker-state-dir", default=None,
                      help="Broker state dir holding daemon.json, used to read "
                           "the daemon's terminal adapter. Defaults to "
                           "$ORG_BROKER_STATE_DIR, then <repo>/.state/broker.")
    send.add_argument("--lines", type=int, default=24,
                      help="Screen rows to inspect when reading the composer.")
    send.add_argument("--verify-timeout", type=float, default=10.0,
                      help="Seconds to keep re-inspecting for each verification.")
    send.add_argument("--poll-interval", type=float, default=0.5,
                      help="Seconds between verification inspects.")
    send.add_argument("--call-timeout", type=float, default=15.0,
                      help="Seconds before a single backend call is abandoned.")
    send.add_argument("--dry-run", action="store_true",
                      help="Print the assembled approval and the resolved target, "
                           "then stop without sending anything.")
    send.set_defaults(func=cmd_send)

    audit = sub.add_parser(
        "audit",
        help=("List self-edit dispatches with no matching "
              "self_edit_approval_sent."),
    )
    audit.add_argument("--older-than-min", type=int, default=5,
                       help="Grace window in minutes; dispatches newer than this "
                            "are skipped because the handshake may be in flight. "
                            "0 disables.")
    audit.add_argument("--since", default=GATE_EPOCH,
                       help="Fixed deployment cutoff (ISO-8601 UTC). Dispatches "
                            "before it predate the tool. Pass an empty string to "
                            "disable.")
    audit.add_argument("--claude-org-root", default=None,
                       help="claude-org repo root used to recognise self-edit "
                            "worker dirs. Defaults to the state.db's repo root.")
    audit.set_defaults(func=cmd_audit)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "db_path", None) is None:
        args.db_path = None
    try:
        return args.func(args)
    except ApprovalError as exc:
        print(json.dumps({"status": "error", "error": str(exc)},
                         ensure_ascii=False, indent=2))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
