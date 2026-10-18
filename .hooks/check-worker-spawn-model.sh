#!/usr/bin/env bash
# PreToolUse Hook: ワーカー spawn のモデル指定を検査する (sonnet 既定 / opus は DELEGATE 指定時だけ)
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   ワーカーの既定モデルは sonnet。opus で spawn してよいのは DELEGATE 本文に `モデル: opus` 行が
#   あるときだけで、そのときディスパッチャーは inbox task JSON
#   (`$CLAUDE_ORG_PATH/.state/dispatcher/inbox/<task_id>.json`) に "model": "opus" を書く
#   (.dispatcher/references/spawn-flow.md)。runtime helper は model 未指定時に opus を既定にするため、
#   書き忘れや model 省略の spawn がそのまま opus ワーカーになる。ディスパッチャーは
#   bypassPermissions で動くので、ここで止める層はフックしか無い。
#
# 判定 (spawn_claude_pane のうち tool_input.role == "worker" だけ):
#   - model == "sonnet" なら allow
#   - model == "opus" なら task_id = name から先頭の "worker-" を除いたもの。
#     inbox task JSON の .model が "opus" のときだけ allow (ファイル無し・読めない・model 不一致は deny)
#   - model が無い・空・その他の値なら deny
#   role が worker 以外 (dispatcher / curator 等) は判定せず通す。
#
# 範囲: ディスパッチャー (`tools/org_extension_schema.json` の roles.dispatcher.required_hooks)。
#   ja 専用フックなので runtime 同梱スキーマとの照合からは外す
#   (`tools/check_runtime_schema_drift.py` の `_JA_ONLY_HOOK_SCRIPTS`)。
#
# 既知の制限:
#   - role を worker 以外にして spawn した呼び出しは見ない (ワーカーの識別は role ラベルだけ)。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})。env CLAUDE_ORG_PATH (opus 判定時に必須)
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload / 想定外の値はすべて deny する
# (`tests/test-hooks-payload-fail-closed.sh` が全 enforcement hook 横断で固定する)。

source "$(dirname "${BASH_SOURCE[0]}")/lib/deny-record.sh"

set -euo pipefail

deny_with_reason() {
  echo "ブロック: $1" >&2
  exit 2
}

INPUT=$(cat)

if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。spawn 呼び出しは安全側 (fail-closed) で拒否します。"
fi

if ! command -v jq &>/dev/null; then
  deny_with_reason "jq がインストールされていません。セキュリティ Hook の実行に必要です。"
fi

if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。spawn 呼び出しは安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
case "$TOOL_NAME" in
  spawn_claude_pane | *__spawn_claude_pane) ;;
  *) exit 0 ;;
esac

ROLE=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.role // empty | tostring')
[[ "$ROLE" == "worker" ]] || exit 0

MODEL=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.model // empty | tostring')
case "$MODEL" in
  sonnet) exit 0 ;;
  opus) ;;
  *) deny_with_reason "ワーカーの model は \"sonnet\" か \"opus\" を明示してください (指定値: \"$MODEL\")。既定は sonnet で、opus は DELEGATE に \`モデル: opus\` 行があるときだけです。" ;;
esac

NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.name // empty | tostring')
TASK_ID="${NAME#worker-}"
# inbox のファイル名になれない値 (空 / パス区切り / .. 等) は照合できない
if [[ -z "$TASK_ID" || ! "$TASK_ID" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]]; then
  deny_with_reason "opus spawn の name ($NAME) から task_id を特定できません。worker-<task_id> の形で spawn してください。"
fi
if [[ -z "${CLAUDE_ORG_PATH:-}" ]]; then
  deny_with_reason "CLAUDE_ORG_PATH が未設定のため inbox task JSON を照合できません。安全側 (fail-closed) で拒否します。"
fi

INBOX="$CLAUDE_ORG_PATH/.state/dispatcher/inbox/$TASK_ID.json"
if [[ ! -f "$INBOX" ]] || ! jq -e -s 'length == 1 and (.[0] | type) == "object" and .[0].model == "opus"' "$INBOX" >/dev/null 2>&1; then
  deny_with_reason "opus spawn ですが inbox task JSON ($INBOX) に \"model\": \"opus\" がありません。runtime helper の既定 opus が漏れた可能性。DELEGATE に \`モデル: opus\` 行が無ければ sonnet で spawn する。ある場合は inbox task JSON に \"model\": \"opus\" を書く。"
fi
exit 0
