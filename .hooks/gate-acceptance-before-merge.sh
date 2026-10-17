#!/usr/bin/env bash
# PreToolUse Hook: 窓口の PR マージの手前で受け入れテストのゲートを動かす (Issue #1167)
# 方式: exit 2 + stderr メッセージ でブロック
#
# 判定は `tools/acceptance_merge_hook.py` に委譲する (docstring が仕様の正本):
#   - PR をマージする Bash は単独の `gh pr merge [<PR>] [flags]` に限る
#     (連結・パイプ・置換・変数・bash -c は deny。API 経由のマージも deny)
#   - その PR の head / base を fetch し `tools/acceptance_gate.py --pr OWNER/REPO#N` を実行。
#     passed は許可 (ただし --auto の後回しマージは deny)、no-manifest は許可
#     (パイロット中は記録のみ)、rejected は deny
#   - ゲート側で、head の途中終了 (pytest.exit) と、テストのコミットの作り直し
#     (初回に見た宣言テストの内容をピン留めし、変わった / 消えたら拒否) も拒否する
#
# 範囲: 窓口だけ (`tools/org_extension_schema.json` の roles.secretary.required_hooks)。
#   ja 専用 (`tools/check_runtime_schema_drift.py` の `_JA_ONLY_HOOK_SCRIPTS`)。
#   ゲートは pytest を 2 回走らせるので登録時に timeout 900 を付ける。タイムアウトした
#   フックはブロックしない扱いになるため、python 側は全体を 840 秒で打ち切って deny する。
#
# 既知の限界: GitHub の画面・別セッション・gh の alias・変数で組み立てたコマンド・
#   マージキューによるマージは見えない。ゲートからマージまでの数秒間の push は再検査しない。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload / python 欠落・
# 異常終了・空出力はすべて deny する (`tests/test-hooks-payload-fail-closed.sh`)。

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
# 速い経路: 引用符・バックスラッシュ・改行を除いて `merge` を含まないコマンドは python を起動せずに通す。
STRIPPED=$(printf '%s' "$COMMAND" | tr -d "\"'\\\\\n")
[[ "$STRIPPED" == *merge* ]] || exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# tools/journal_append.sh と同じ interpreter の選び方 (Windows は py -3 / python)。
if command -v py >/dev/null 2>&1; then
  PY=(py -3)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1; then
  PY=(python)
else
  deny_with_reason "python が見つかりません。受け入れテストのゲートを動かせないため拒否します。"
fi

# 判定は stdout の 1 行目だけで読み、python の終了コードも 0 を要求する。
ERR_FILE=$(mktemp)
trap 'rm -f "$ERR_FILE"' EXIT
RC=0
OUT=$(printf '%s\n' "$INPUT" | "${PY[@]}" "$HOOK_DIR/../tools/acceptance_merge_hook.py" 2>"$ERR_FILE") || RC=$?
FIRST_LINE=$(printf '%s\n' "$OUT" | head -n 1)
if [[ "$RC" == 0 && "$FIRST_LINE" == "ALLOW" ]]; then
  exit 0
fi
if [[ "$RC" != 0 || -z "$OUT" ]]; then
  deny_with_reason "受け入れテストのゲートが異常終了しました (rc=$RC)。安全側で拒否します。$(head -c 500 "$ERR_FILE")"
fi
deny_with_reason "${FIRST_LINE#DENY: }"
