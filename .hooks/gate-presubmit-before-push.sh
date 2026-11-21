#!/usr/bin/env bash
# PreToolUse Hook: 窓口が開いている run の作業を、提出前の確認を通さずに送らないようにする (Issue 1298)
# 方式: exit 2 + stderr メッセージ でブロック
#
# 判定は `tools/presubmit.py hook` に委譲する (docstring が仕様の正本):
#   - 開いている run (queued / in_use / review) の作業を送る生の `git push`
#     (宛先がその run の作業ツリー = cwd か -C、またはコマンドにその run のブランチ名がある)、
#     `gh pr create` (--head がその run のブランチ、または cwd がその作業ツリー)、
#     `gh pr edit N` (N がその run の PR) を拒否し、`tools/presubmit.py push` / `pr` を案内する。
#     そちらは提出前の確認 (presubmit_checked) が passed の head だけを送り、PR の説明文の
#     閉じる語の並びを確かめてから gh を呼ぶ
#   - 宛先が読めない形 (変数・コマンド置換) の git push / gh pr create|edit は拒否する
#   - それ以外 (ja の main の取り込み、base clone の fetch、作業ツリーの ff-only の merge 等) は通す
#
# 範囲: 窓口だけ (`tools/org_extension_schema.json` の roles.secretary.required_hooks)。
#   ja 専用 (`tools/check_runtime_schema_drift.py` の `_JA_ONLY_HOOK_SCRIPTS`)。
#
# 既知の限界: alias・スクリプトファイルの中の push・別のセッション・GitHub の画面は見えない。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input, cwd})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload / python 欠落・
# 異常終了・空出力はすべて deny する (`tests/test-hooks-payload-fail-closed.sh`)。

source "$(dirname "${BASH_SOURCE[0]}")/lib/deny-record.sh"

set -euo pipefail

deny_with_reason() {
  echo "ブロック: $1" >&2
  exit 2
}

INPUT=$(cat)

if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。Bash 呼び出しは安全側 (fail-closed) で拒否します。"
fi

if ! command -v jq &>/dev/null; then
  deny_with_reason "jq がインストールされていません。セキュリティ Hook の実行に必要です。"
fi

if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。Bash 呼び出しは安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
[[ "$TOOL_NAME" == "Bash" ]] || exit 0

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty | tostring')
# 速い経路: 引用符・バックスラッシュ・改行を除いて `push` も `pr` も含まないコマンドは python を起動せずに通す。
STRIPPED=$(printf '%s' "$COMMAND" | tr -d "\"'\\\\\n")
[[ "$STRIPPED" == *push* || "$STRIPPED" == *pr* ]] || exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# tools/journal_append.sh と同じ interpreter の選び方 (Windows は py -3 / python)。
if command -v py >/dev/null 2>&1; then
  PY=(py -3)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1; then
  PY=(python)
else
  deny_with_reason "python が見つかりません。提出前の確認のフックを動かせないため拒否します。"
fi

# 判定は stdout の 1 行目だけで読み、python の終了コードも 0 を要求する。
ERR_FILE=$(mktemp)
HOOK_CLEANUP_FILES+=("$ERR_FILE")
RC=0
OUT=$(printf '%s\n' "$INPUT" | "${PY[@]}" "$HOOK_DIR/../tools/presubmit.py" hook 2>"$ERR_FILE") || RC=$?
FIRST_LINE=$(printf '%s\n' "$OUT" | head -n 1)
if [[ "$RC" == 0 && "$FIRST_LINE" == "ALLOW" ]]; then
  exit 0
fi
if [[ "$RC" != 0 || -z "$OUT" ]]; then
  deny_with_reason "提出前の確認のフックが異常終了しました (rc=$RC)。安全側で拒否します。$(head -c 500 "$ERR_FILE")"
fi
deny_with_reason "${FIRST_LINE#DENY: }"
