#!/usr/bin/env bash
# PreToolUse Hook: Skill ツールでの `codex:rescue` 呼び出しをブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   ワーカーの Codex セルフレビューは `codex exec review` 直打ち
#   (`tools/codex_review_gate.sh`) だけを正規経路にしている。`codex:rescue` skill は
#   レビューの成立判定 (「空の合格」の検出) を通らない別経路で、brief の文章
#   (`tools/templates/worker_brief_normal.md` の Codex 節) だけで禁じていたものを
#   機械的に止める。
#
# 範囲: ワーカー (`tools/org_extension_schema.json` の worker_roles から配る)。
#   ja 専用フックなので runtime 同梱スキーマとの照合からは外す
#   (`tools/check_runtime_schema_drift.py` の `_JA_ONLY_HOOK_SCRIPTS`)。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload はすべて deny する
# (`tests/test-hooks-payload-fail-closed.sh` が全 enforcement hook 横断で固定する)。

source "$(dirname "${BASH_SOURCE[0]}")/lib/deny-record.sh"

set -euo pipefail

deny_with_reason() {
  echo "ブロック: $1" >&2
  exit 2
}

INPUT=$(cat)

if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。Skill 呼び出しは安全側 (fail-closed) で拒否します。"
fi

if ! command -v jq &>/dev/null; then
  deny_with_reason "jq がインストールされていません。セキュリティ Hook の実行に必要です。"
fi

if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。Skill 呼び出しは安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
[[ "$TOOL_NAME" == "Skill" ]] || exit 0

SKILL=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.skill // empty | tostring')
if [[ "${SKILL#/}" == "codex:rescue" ]]; then
  deny_with_reason "codex:rescue skill は使えません。Codex セルフレビューは bash tools/codex_review_gate.sh <base> (codex exec review 直打ち) で回してください。"
fi
exit 0
