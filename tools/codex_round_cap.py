#!/usr/bin/env python3
"""Per-task Codex review round cap, enforced outside the worker.

The worker's only permitted Codex entry is ``bash
"$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" <base-ref>``. The PreToolUse
hook ``.hooks/block-codex-over-cap.sh`` runs ``hook`` here (outside the
worker's sandbox) for every Bash command (and MCP ``spawn_pane`` /
``new_tab`` command, ``send_keys`` text) that mentions ``codex`` once
quotes and backslashes are removed. Classification is default-deny:

* in every simple command (after dequoting), a word whose basename is
  ``codex`` / ``codex.js`` or that contains ``@openai/codex`` is refused
  unless the program is in a small allowlist of programs that do not run
  their arguments (``grep``, ``cat``, ``echo``, ``git``, ``which``,
  ``command -v``, ``npm i`` ...). Words containing whitespace are
  classified again as commands (``bash -c``, ``su -c``, ``env -S``,
  ``python -c``, ``watch``, ``eval``);
* a shell / ``eval`` / ``xargs`` that runs text other than a named script
  file (stdin, pipe, heredoc, here-string, ``-c``, ``-s``, ``/dev/stdin``,
  ``<(...)``) is refused when the command text mentions ``codex``, as is a
  ``$(...)`` / backtick / ``<(...)`` body that mentions it (single-quoted
  text and quoted-delimiter heredoc bodies do not expand and are ignored);
* MCP ``spawn_codex_pane`` is always refused;
* a gate call that is not exactly the canonical form is refused;
* a canonical gate call consumes one round. Rounds are counted from
  ``codex_round_started`` events for the task; past the allowance a
  ``codex_round_denied`` event is written and the call is refused.

The allowance defaults to 3 and only the secretary changes it, with
``set`` (``codex_round_cap_changed`` events). The worker cannot write the
events table: ja-root ``.state/`` is read-only from its sandbox and outside
its Edit/Write boundary.

Subcommands::

    codex_round_cap.py hook < payload.json        # prints ALLOW / DENY: <msg>
    codex_round_cap.py set --task-id X --allowed N|unlimited --reason TEXT
    codex_round_cap.py status --task-id X

``set`` refuses a task id with no ``delegate_sent`` event (a typo would
otherwise raise nothing).

Known limits (the command text cannot show them): codex reached through a
variable, glob, brace expansion, ``$'...'``, a script written to a file,
a string built in another language, an allowlisted program's own exec
feature (``git`` alias / pager, ``sed`` ``e``), or ``send_keys`` ``keys``
sent one key at a time.

``STATE_DB_PATH`` is deliberately ignored: the hook inherits the worker's
environment, and a worker-controlled DB path would let it count rounds in
a file it can write.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_ALLOWED = 3
UNLIMITED = "unlimited"
GATE_NAME = "codex_review_gate.sh"
HOOK_ACTOR = "codex-round-cap-hook"

GATE_FORM = 'bash "$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" <base-ref>'
MSG_DIRECT = (
    "codex の直接実行は禁止。" + GATE_FORM + " を使う"
)
MSG_GATE_FORM = (
    "Codex ゲートは単独のコマンドとして " + GATE_FORM
    + " の形でだけ呼べます (ワーカー側のコピーや相対パス・他コマンドとの連結は不可)"
)

# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------

# Programs that never execute their arguments: a codex word there is data.
_SAFE_PROGS = {
    "grep", "egrep", "fgrep", "rg", "ag", "cat", "less", "more", "head",
    "tail", "wc", "ls", "stat", "file", "echo", "printf", "sort", "uniq",
    "cut", "tr", "diff", "cmp", "jq", "test", "[", "true", "false", "which",
    "type", "sed", "git",
}
# Workers are told to run `npm i -g @openai/codex@latest`.
_NPM_SAFE = {"install", "i", "ls", "view", "outdated"}
# Programs that can run text that is not a script file named on the line.
_RUNNERS = {"bash", "sh", "zsh", "dash", "ksh", "source", ".", "eval", "xargs"}
_SHELL_OPT_ARGS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_KEYWORDS = {"!", "{", "}", "if", "then", "else", "elif", "do", "while",
             "until", "fi", "done"}
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_BASE_REF_RE = re.compile(r"^[A-Za-z0-9._/@^~+-]+$")
_HEREDOC_RE = re.compile(r"<<(-?)[ \t]*(\\?)(['\"]?)([^\s;&|<>()'\"]+)\3")
_SUBST_RE = re.compile(r"[$<>]\(|`")
_OPS = ";&|()<>\n"


def _mentions_codex(text: str) -> bool:
    return "codex" in text.translate(str.maketrans("", "", "\"'\\\n"))


def _scan(text: str) -> "tuple[str, str]":
    """Return ``(code, live)`` for one shell text.

    ``code`` drops comments and every heredoc body, keeping quotes: it is
    what the lexer reads. ``live`` additionally drops single-quoted
    segments and the bodies of quoted-delimiter heredocs: the text where
    ``$(...)`` / backticks still expand.
    """
    code: list[str] = []
    live: list[str] = []
    pending: list[tuple[str, bool, bool]] = []  # (delimiter, strip tabs, quoted)
    i, n, dq = 0, len(text), False
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            code.append(text[i:i + 2])
            live.append(text[i:i + 2])
            i += 2
            continue
        if dq:
            dq = c != '"'
        elif c == '"':
            dq = True
        elif c == "'":
            j = text.find("'", i + 1)
            j = n - 1 if j < 0 else j  # unbalanced: the lexer fails closed
            code.append(text[i:j + 1])
            i = j + 1
            continue
        elif c == "#" and (i == 0 or text[i - 1] in " \t" + _OPS):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif (text.startswith("<<", i) and not text.startswith("<<<", i)
              and (i == 0 or text[i - 1] != "<")):
            m = _HEREDOC_RE.match(text, i)
            if m:
                pending.append((m.group(4), m.group(1) == "-",
                                bool(m.group(2) or m.group(3))))
                code.append(m.group())
                live.append(m.group())
                i = m.end()
                continue
        elif c == "\n" and pending:
            code.append(c)
            live.append(c)
            i += 1
            for delim, tabs, quoted in pending:
                while i < n:
                    j = text.find("\n", i)
                    j = n if j < 0 else j
                    line, i = text[i:j], j + 1
                    if (line.lstrip("\t") if tabs else line) == delim:
                        break
                    if not quoted:
                        live.append(line + "\n")
            pending = []
            continue
        code.append(c)
        live.append(c)
        i += 1
    return "".join(code), "".join(live)


def _substitutions(text: str) -> "list[str]":
    """Bodies of ``$(...)`` / ``<(...)`` / ``>(...)`` / backtick substitutions."""
    bodies: list[str] = []
    i = 0
    while (m := _SUBST_RE.search(text, i)):
        s = m.end()
        if m.group() == "`":
            e = text.find("`", s)
            e = len(text) if e < 0 else e
            i = e + 1
        else:
            depth, e = 1, s
            while e < len(text):
                depth += (text[e] == "(") - (text[e] == ")")
                if depth == 0:
                    break
                e += 1
            i = s  # nested substitutions are scanned too
        bodies.append(text[s:e])
    return bodies


def _split_simple_commands(code: str) -> "list[list[str]]":
    lex = shlex.shlex(code, posix=True, punctuation_chars=_OPS)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""  # comments are already gone (_scan); shlex would eat the newline
    simple: list[list[str]] = []
    current: list[str] = []
    for tok in lex:
        if tok and all(c in _OPS for c in tok):
            if ("<" in tok or ">" in tok) and not any(c in tok for c in "();|\n"):
                current.append(tok)  # redirection operator, stays in place
                continue
            if current:
                simple.append(current)
            current = []
            continue
        current.append(tok)
    if current:
        simple.append(current)
    return simple


def _is_redirect(tok: str) -> bool:
    return bool(tok) and all(c in "<>&" for c in tok) and ("<" in tok or ">" in tok)


def _without_redirects(words: "list[str]") -> "list[str]":
    out: list[str] = []
    i = 0
    while i < len(words):
        if _is_redirect(words[i]):
            i += 2
        elif words[i].isdigit() and i + 1 < len(words) and _is_redirect(words[i + 1]):
            i += 1
        else:
            out.append(words[i])
            i += 1
    return out


def _runs_text(words: "list[str]") -> bool:
    """True when a shell / eval / xargs here runs text that is not a script file."""
    words = _without_redirects(words)
    k = next((i for i, w in enumerate(words)
              if os.path.basename(w) in _RUNNERS), None)
    if k is None:
        return False
    if os.path.basename(words[k]) in ("eval", "xargs"):
        return True
    i = k + 1
    while i < len(words):
        w = words[i]
        if w == "--":
            i += 1
            break
        if w in _SHELL_OPT_ARGS:
            i += 2
            continue
        if w[:1] in "-+" and w != "-":
            if not w.startswith("--") and ("c" in w[1:] or "s" in w[1:]):
                return True
            i += 1
            continue
        break
    if i >= len(words):
        return True  # reads commands from stdin (pipe / redirect / heredoc)
    op = words[i]
    return op in ("-", "/dev/stdin") or op.startswith(("/dev/fd/", "/proc/"))


def _variants(word: str) -> "list[str]":
    """The word plus the value of an attached option (``-Sx`` / ``--opt=x`` / ``N=x``)."""
    out = [word]
    if word.startswith("-") and not word.startswith("--"):
        out.append(word[2:])
    if "=" in word:
        out.append(word.split("=", 1)[1])
    return out


def _classify_words(words: "list[str]", text: str, depth: int) -> "list[str]":
    k = 0
    while k < len(words) and (_ASSIGN_RE.match(words[k]) or words[k] in _KEYWORDS):
        k += 1
    prog = words[k] if k < len(words) else ""
    nxt = words[k + 1] if k + 1 < len(words) else ""
    if (prog in _SAFE_PROGS or (prog == "command" and nxt in ("-v", "-V"))
            or (prog == "npm" and nxt in _NPM_SAFE)):
        return []
    found: list[str] = []
    for word in words:
        for v in _variants(word):
            base = os.path.basename(v)
            if base in ("codex", "codex.js") or "@openai/codex" in v:
                found.append("direct")
            elif base == GATE_NAME:
                found.append("gate")
            if any(c.isspace() for c in v):
                found += classify_text(v, depth + 1)
    if _runs_text(words) and _mentions_codex(text):
        found.append("direct")
    return found


def classify_text(command: str, depth: int = 0) -> "list[str]":
    """Return findings (``"direct"`` / ``"gate"``) for one shell text."""
    if depth > 8:
        raise ValueError("command nesting too deep")
    command = command.replace("\\\n", "")
    code, live = _scan(command)
    found = ["direct" for body in _substitutions(live) if _mentions_codex(body)]
    for words in _split_simple_commands(code):
        found += _classify_words(words, command, depth)
    return found


def _norm(path: str) -> str:
    return os.path.normpath(path).rstrip(os.sep) or os.sep


def _expand_org_path(word: str, org_path: str) -> str:
    for ref in ("${CLAUDE_ORG_PATH}", "$CLAUDE_ORG_PATH"):
        if word.startswith(ref):
            return org_path + word[len(ref):]
    return word


def classify(command: str, canonical_gate: str, org_path: str = "") -> "tuple[str, str]":
    """Return ``("allow"|"deny"|"gate", message)`` for one Bash command.

    ``gate`` means the command is exactly one canonical gate call and must
    now be counted. ``deny`` is returned for anything unparsable that
    mentions codex (fail-closed).
    """
    try:
        findings = classify_text(command)
        simple = _split_simple_commands(_scan(command.replace("\\\n", ""))[0])
    except ValueError as exc:
        if _mentions_codex(command):
            return "deny", f"コマンドを解析できません ({exc})。{MSG_DIRECT}"
        return "allow", ""
    if "direct" in findings:
        return "deny", MSG_DIRECT
    if not findings:
        return "allow", ""
    words = simple[0] if len(simple) == 1 else []
    if (
        findings == ["gate"]
        and len(words) == 3
        and words[0] == "bash"
        and _norm(_expand_org_path(words[1], org_path)) == _norm(canonical_gate)
        and _BASE_REF_RE.match(words[2])
    ):
        return "gate", ""
    return "deny", MSG_GATE_FORM


# ---------------------------------------------------------------------------
# State DB
# ---------------------------------------------------------------------------


def _open_existing(db_path: Path, busy_ms: int = 10000):
    """Open an existing state.db and wrap it in a StateWriter. Never creates."""
    from tools.state_db import connect
    from tools.state_db.discover import verify_state_db_schema
    from tools.state_db.writer import StateWriter

    if not db_path.is_file():
        raise FileNotFoundError(f"state.db が見つかりません: {db_path}")
    conn = connect(db_path)
    try:
        verify_state_db_schema(db_path, conn=conn)
        writer = StateWriter(conn)
        conn.execute(f"PRAGMA busy_timeout = {int(busy_ms)}")
    except BaseException:
        conn.close()
        raise
    return conn, writer


def _count(conn, kind: str, task: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind = ? "
        "AND json_extract(payload_json, '$.task') = ?",
        (kind, task),
    ).fetchone()
    return int(row[0])


def _allowed(conn, task: str):
    row = conn.execute(
        "SELECT json_extract(payload_json, '$.allowed') FROM events "
        "WHERE kind = 'codex_round_cap_changed' "
        "AND json_extract(payload_json, '$.task') = ? "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (task,),
    ).fetchone()
    if row is None or row[0] is None:
        return DEFAULT_ALLOWED
    if row[0] == UNLIMITED:
        return UNLIMITED
    return int(row[0])


def _task_for_worker_dir(conn, worker_dir: str):
    target = _norm(worker_dir)
    for task, d in conn.execute(
        "SELECT json_extract(payload_json, '$.task'), "
        "json_extract(payload_json, '$.dir') FROM events "
        "WHERE kind = 'delegate_sent' ORDER BY occurred_at DESC, id DESC"
    ):
        if task and isinstance(d, str) and d.strip() and _norm(d.strip()) == target:
            return str(task)
    return None


def _status(conn, task: str) -> dict:
    return {
        "task": task,
        "used": _count(conn, "codex_round_started", task),
        "allowed": _allowed(conn, task),
        "denied": _count(conn, "codex_round_denied", task),
    }


def consume_round(db_path: Path, worker_dir: str,
                  busy_ms: int = 10000) -> "tuple[bool, str]":
    """Count one canonical gate call. Returns (allowed, message)."""
    conn, writer = _open_existing(db_path, busy_ms)
    try:
        task = _task_for_worker_dir(conn, worker_dir)
        if task is None:
            return False, (
                f"WORKER_DIR ({worker_dir}) に対応する task を特定できない "
                "(delegate_sent が無い)。codex を回さず窓口へ報告してください。"
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            used = _count(conn, "codex_round_started", task)
            allowed = _allowed(conn, task)
            payload = {"task": task, "allowed": allowed, "worker_dir": _norm(worker_dir)}
            if allowed == UNLIMITED or used < allowed:
                writer.append_event(
                    kind="codex_round_started", actor=HOOK_ACTOR,
                    payload={**payload, "round": used + 1}, run_task_id=task)
                ok = True
            else:
                writer.append_event(
                    kind="codex_round_denied", actor=HOOK_ACTOR,
                    payload={**payload, "used": used}, run_task_id=task)
                ok = False
            writer.commit()
        except BaseException:
            conn.rollback()
            raise
        if ok:
            return True, ""
        return False, (
            f"CODEX_ROUND_DENIED: Codex レビューの許可回数 (allowed={allowed}) を"
            f"使い切りました (used={used})。codex を再実行せず、残指摘と自己評価を"
            "窓口へ報告して停止してください。許可回数を増やせるのは窓口だけです。"
        )
    finally:
        conn.close()


def run_hook(stdin_text: str, env) -> str:
    """Return the hook's first output line (``ALLOW`` / ``DENY: ...``)."""
    try:
        payload = json.loads(stdin_text)
        tool = str(payload.get("tool_name") or "")
        if tool.endswith("__spawn_codex_pane"):
            return "DENY: ワーカーは Codex ペインを起動できません。" + MSG_DIRECT
        field = "text" if tool.endswith("__send_keys") else "command"
        command = (payload.get("tool_input") or {}).get(field)
        if not isinstance(command, str):
            return "ALLOW"
        worker_dir = env.get("WORKER_DIR") or ""
        org_path = env.get("CLAUDE_ORG_PATH") or ""
        if not worker_dir or not org_path:
            return "DENY: WORKER_DIR または CLAUDE_ORG_PATH が設定されていません。"
        canonical = os.path.join(org_path, "tools", GATE_NAME)
        verdict, msg = classify(command, canonical, org_path)
        if verdict == "allow":
            return "ALLOW"
        if verdict == "deny":
            return f"DENY: {msg}"
        if tool != "Bash":
            return f"DENY: {MSG_GATE_FORM}"  # rounds are counted for Bash only
        db_path = Path(org_path) / ".state" / "state.db"
        ok, msg = consume_round(db_path, worker_dir)
        return "ALLOW" if ok else f"DENY: {msg}"
    except Exception as exc:  # noqa: BLE001 - fail closed
        return f"DENY: codex round cap の判定に失敗しました ({type(exc).__name__}: {exc})"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_allowed(value: str):
    if value == UNLIMITED:
        return UNLIMITED
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("want an integer >= 0 or 'unlimited'")
    if n < 0:
        raise argparse.ArgumentTypeError("want an integer >= 0 or 'unlimited'")
    return n


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/codex_round_cap.py",
        description="Per-task Codex review round cap (hook + secretary CLI).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook", help="PreToolUse check; payload on stdin")
    p_set = sub.add_parser("set", help="secretary: change a task's allowance")
    p_set.add_argument("--task-id", required=True)
    p_set.add_argument("--allowed", required=True, type=_parse_allowed)
    p_set.add_argument("--reason", required=True)
    p_set.add_argument("--db-path", default=None)
    p_status = sub.add_parser("status", help="show used / allowed / denied")
    p_status.add_argument("--task-id", required=True)
    p_status.add_argument("--db-path", default=None)
    args = parser.parse_args(argv)

    if args.cmd == "hook":
        print(run_hook(sys.stdin.read(), os.environ))
        return 0

    db_path = Path(args.db_path) if args.db_path else _REPO_ROOT / ".state" / "state.db"
    try:
        conn, writer = _open_existing(db_path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"tools/codex_round_cap.py: error: {exc}\n")
        return 1
    try:
        if args.cmd == "set":
            if conn.execute(
                "SELECT 1 FROM events WHERE kind = 'delegate_sent' "
                "AND json_extract(payload_json, '$.task') = ? LIMIT 1",
                (args.task_id,),
            ).fetchone() is None:
                sys.stderr.write(
                    f"tools/codex_round_cap.py: error: no delegate_sent event for "
                    f"task {args.task_id!r} (typo?)\n")
                return 1
            previous = _allowed(conn, args.task_id)
            writer.append_event(
                kind="codex_round_cap_changed", actor="secretary",
                payload={"task": args.task_id, "allowed": args.allowed,
                         "previous": previous, "reason": args.reason},
                run_task_id=args.task_id)
            writer.commit()
        print(json.dumps(_status(conn, args.task_id), ensure_ascii=False))
        return 0
    except sqlite3.Error as exc:
        sys.stderr.write(f"tools/codex_round_cap.py: error: {exc}\n")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
