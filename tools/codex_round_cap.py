#!/usr/bin/env python3
"""Per-task Codex review round cap, enforced outside the worker.

The worker's only permitted Codex entry is ``bash
"$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" <base-ref>``. The PreToolUse
hook ``.hooks/block-codex-over-cap.sh`` runs ``hook`` here (outside the
worker's sandbox) for every Bash command that mentions ``codex``:

* a direct ``codex`` invocation is refused;
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

_SHELLS = {"bash", "sh", "zsh", "dash", "source", "."}
_SUBSHELL_CODEX_RE = re.compile(r"(\$\(|`)\s*([^\s)`]*/)?codex(\s|\)|`|$)")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_BASE_REF_RE = re.compile(r"^[A-Za-z0-9._/@^~+-]+$")
# Words that only introduce the real command of a simple command.
_KEYWORDS = {"!", "{", "}", "if", "then", "else", "elif", "do", "while",
             "until", "fi", "done"}
_PLAIN_WRAPPERS = {"exec", "command", "builtin", "nohup", "time"}
# Option words of a wrapper that take a separate argument.
_OPT_ARGS = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "xargs": {"-I", "-n", "-P", "-d", "-E", "-L", "-s", "-a",
              "--max-args", "--max-procs", "--delimiter", "--arg-file"},
    "nice": {"-n", "--adjustment"},
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-U", "-r", "-t"},
    "npx": {"-p", "--package"},
}
_RECURSE_OPTS = {
    "env": {"-S", "--split-string"},
    "npx": {"-c", "--call"},
}
_DLX = {"pnpx", "bunx"}


class Finding:
    """One classified simple command that matters to the cap."""

    def __init__(self, kind, *, program=None, script=None, args=None,
                 plain=False):
        self.kind = kind  # "direct" | "gate"
        self.program = program
        self.script = script
        self.args = args or []
        # True when the gate was the program word itself, with no
        # assignment / wrapper / keyword in front of it.
        self.plain = plain


def _split_simple_commands(command: str) -> "list[list[str]]":
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    simple: list[list[str]] = []
    current: list[str] = []
    for tok in lex:
        if tok and all(c in ";&|()<>\n" for c in tok):
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


def _skip_opts(words: "list[str]", i: int, prog: str) -> "tuple[int, list[str]]":
    """Skip option words of wrapper ``prog``; collect strings to recurse into."""
    takes = _OPT_ARGS.get(prog, set())
    recurse = _RECURSE_OPTS.get(prog, set())
    nested: list[str] = []
    while i < len(words) and words[i].startswith("-") and words[i] != "-":
        w = words[i]
        if w == "--":
            return i + 1, nested
        if w in recurse:
            if i + 1 < len(words):
                nested.append(words[i + 1])
            i += 2
            continue
        i += 2 if w in takes else 1
    return i, nested


def _classify_words(words: "list[str]", depth: int) -> "list[Finding]":
    findings: list[Finding] = []
    i = 0
    plain = True
    while i < len(words):
        w = words[i]
        base = os.path.basename(w)
        if _is_redirect(w) or (
                w.isdigit() and i + 1 < len(words) and _is_redirect(words[i + 1])):
            i += 2 if _is_redirect(w) else 1
            plain = False
            continue
        if _ASSIGN_RE.match(w) or w in _KEYWORDS or base in _PLAIN_WRAPPERS:
            i += 1
            plain = False
            if base == "exec":
                # exec -a NAME / -c / -l
                while i < len(words) and words[i].startswith("-"):
                    i += 2 if words[i] == "-a" else 1
            continue
        if base == "eval":
            findings += classify_text(" ".join(words[i + 1:]), depth + 1)
            return findings
        if base in _OPT_ARGS or base in _DLX:
            plain = False
            i, nested = _skip_opts(words, i + 1, base)
            for text in nested:
                findings += classify_text(text, depth + 1)
            if base == "env":
                while i < len(words) and _ASSIGN_RE.match(words[i]):
                    i += 1
            elif base == "timeout":
                i += 1  # duration
            continue
        if base in {"pnpm", "yarn"} and i + 1 < len(words) and words[i + 1] == "dlx":
            plain = False
            i, nested = _skip_opts(words, i + 2, "npx")
            for text in nested:
                findings += classify_text(text, depth + 1)
            continue
        break
    if i >= len(words):
        return findings
    prog = words[i]
    rest = words[i + 1:]
    base = os.path.basename(prog)
    if base == "codex" or prog.startswith("@openai/codex"):
        findings.append(Finding("direct", program=prog))
        return findings
    if base == GATE_NAME:
        findings.append(Finding("gate", program=prog, script=prog, args=rest))
        return findings
    if base in _SHELLS:
        j = 0
        while j < len(rest) and rest[j].startswith("-") and rest[j] not in ("-", "--"):
            flag = rest[j]
            if not flag.startswith("--") and "c" in flag[1:]:
                if j + 1 < len(rest):
                    findings += classify_text(rest[j + 1], depth + 1)
                # The -c string is itself a (nested) command; anything after it
                # is $0/$@ for it, never a script path.
                findings.append(Finding("nested", program=prog))
                return findings
            j += 2 if flag in ("-o", "+o", "-O", "+O") else 1
        if j < len(rest) and rest[j] == "--":
            j += 1
        if j < len(rest) and os.path.basename(rest[j]) == GATE_NAME:
            findings.append(Finding("gate", program=prog, script=rest[j],
                                    args=rest[j + 1:],
                                    plain=plain and prog == "bash" and j == 0))
    return findings


def classify_text(command: str, depth: int = 0) -> "list[Finding]":
    if depth > 8:
        raise ValueError("command nesting too deep")
    findings: list[Finding] = []
    if ("$(" in command or "`" in command) and _SUBSHELL_CODEX_RE.search(command):
        findings.append(Finding("direct", program="codex"))
    for words in _split_simple_commands(command):
        findings += _classify_words(words, depth)
    return findings


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
        n_simple = len(_split_simple_commands(command))
    except ValueError as exc:
        if "codex" in command:
            return "deny", f"コマンドを解析できません ({exc})。{MSG_DIRECT}"
        return "allow", ""
    if any(f.kind == "direct" for f in findings):
        return "deny", MSG_DIRECT
    gates = [f for f in findings if f.kind == "gate"]
    if not gates:
        return "allow", ""
    g = gates[0]
    if (
        len(findings) == 1
        and n_simple == 1
        and g.plain
        and g.program == "bash"
        and _norm(_expand_org_path(g.script, org_path)) == _norm(canonical_gate)
        and len(g.args) == 1
        and _BASE_REF_RE.match(g.args[0])
    ):
        return "gate", ""
    return "deny", MSG_GATE_FORM


# ---------------------------------------------------------------------------
# State DB
# ---------------------------------------------------------------------------


def _open_existing(db_path: Path):
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
        conn.execute("PRAGMA busy_timeout = 10000")
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


def consume_round(db_path: Path, worker_dir: str) -> "tuple[bool, str]":
    """Count one canonical gate call. Returns (allowed, message)."""
    conn, writer = _open_existing(db_path)
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
        command = (payload.get("tool_input") or {}).get("command")
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
