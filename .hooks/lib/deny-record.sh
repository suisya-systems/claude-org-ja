#!/usr/bin/env bash
# 拒否の記録 (Issue #1194): hook が exit 2 で止めたら events に hook_denied を 1 行書く共通の出口。
#
# 使い方: 拒否しうる hook は stdin を読む前・最初の exit 2 より前に 1 行だけ source する。
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/deny-record.sh"
#   hook 側で EXIT trap を張らない (上書きすると記録が止まる)。後片付けしたい一時ファイルは
#   HOOK_CLEANUP_FILES+=("$path") で渡す。tests/test-hook-deny-record.sh がこの規約を全 hook で検査する。
#
# 仕組み: stdin (PreToolUse payload) と stderr を一時ディレクトリに逃がし、stdin は同じ内容で
#   hook に読ませ直す。EXIT trap で終了コードを見て、2 なら stderr を拒否理由として記録する。
#   stderr は終了時にまとめて元の stderr へ書き戻す。
#   記録する項目: hook (スクリプト名) / tool (tool_name) / reason (stderr 先頭 500 字) /
#   command (tool_input の command・text・file_path・path の先頭 200 字。秘密を含みうるので切る)。
#   日時は events.ts。
#
# fail-closed を変えない: 記録の失敗 (一時ディレクトリが作れない・python 欠落・DB 書き込み失敗) は
#   すべて無言で捨て、終了コードは hook が決めたものをそのまま返す。

HOOK_CLEANUP_FILES=()
__DENY_RECORD_HOOK="$(basename "$0")"
__DENY_RECORD_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || __DENY_RECORD_LIB_DIR=""
__DENY_RECORD_TMP="$(mktemp -d 2>/dev/null)" || __DENY_RECORD_TMP=""

if [[ -n "$__DENY_RECORD_TMP" ]] && cat >"$__DENY_RECORD_TMP/payload" 2>/dev/null; then
  exec 0<"$__DENY_RECORD_TMP/payload" 3>&2 2>"$__DENY_RECORD_TMP/stderr"
else
  # stdin を逃がせなかったら記録をあきらめ、hook の動きには一切触らない。
  __DENY_RECORD_TMP=""
fi

__deny_record_on_exit() {
  local rc=$1
  trap - EXIT
  set +eu
  if [[ -n "$__DENY_RECORD_TMP" ]]; then
    exec 2>&3 3>&-
    cat "$__DENY_RECORD_TMP/stderr" >&2 2>/dev/null
    if [[ "$rc" == 2 && -n "$__DENY_RECORD_LIB_DIR" ]]; then
      __deny_record_write >/dev/null 2>&1 </dev/null
    fi
    rm -rf "$__DENY_RECORD_TMP" 2>/dev/null
  fi
  rm -f "${HOOK_CLEANUP_FILES[@]}" 2>/dev/null
  exit "$rc"
}

__deny_record_write() {
  local -a py
  if command -v py >/dev/null 2>&1; then
    py=(py -3)
  elif command -v python3 >/dev/null 2>&1; then
    py=(python3)
  elif command -v python >/dev/null 2>&1; then
    py=(python)
  else
    return 0
  fi
  local -a limit=()
  command -v timeout >/dev/null 2>&1 && limit=(timeout 5)
  "${limit[@]}" "${py[@]}" - "$__DENY_RECORD_LIB_DIR/../../tools" "$__DENY_RECORD_HOOK" \
    "$__DENY_RECORD_TMP/payload" "$__DENY_RECORD_TMP/stderr" <<'PY'
import json, sys
tools, hook, payload_path, stderr_path = sys.argv[1:5]
sys.path.insert(0, tools)
try:
    payload = json.loads(open(payload_path, encoding="utf-8", errors="replace").read())
except ValueError:
    payload = None
if not isinstance(payload, dict):
    payload = {}
tool_input = payload.get("tool_input")
if not isinstance(tool_input, dict):
    tool_input = {}
command = next((tool_input[k] for k in ("command", "text", "file_path", "path")
                if isinstance(tool_input.get(k), str)), None)
if command is None:
    command = json.dumps(tool_input, ensure_ascii=False) if tool_input else ""
reason = open(stderr_path, encoding="utf-8", errors="replace").read().strip()
import journal_append
journal_append.main(["hook_denied", "--no-watcher-guard", "--json", json.dumps({
    "actor": "hook",
    "hook": hook,
    "tool": payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else "",
    "reason": reason[:500],
    "command": command[:200],
}, ensure_ascii=False)])
PY
}

trap '__deny_record_on_exit $?' EXIT
