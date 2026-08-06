#!/usr/bin/env bash
# PreToolUse Hook: Worker からの git push をブロックする
# 方式: exit 2 + stderr メッセージ でブロック

set -euo pipefail

# shellcheck source=lib/segment-split.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/segment-split.sh"

# Helper: deny decision を stderr + exit 2 で返す
deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

# jq チェック (fail closed)
if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

# stdin から JSON を読み取り
INPUT=$(cat)

# 空 payload の fail-closed ガード (Issue #834)。jq は「JSON 値がゼロ個」の入力を
# parse error にせず exit 0 + 出力なしで返すため、空 stdin では下の抽出結果が空文字に
# なり、`[[ -z "$COMMAND" ]]` の passthrough に落ちて enforcement が素通りする。
# jq に渡す前に明示的に弾く。導出の詳細は block-foreground-subagent.sh の同じガード。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。安全側 (fail-closed) で拒否します。"
fi

# 不正 JSON / 非 object payload の fail-closed ガード (Issue #834)。
# `VAR=$(echo "$INPUT" | jq ...)` は parse error (exit 4) や非 object への index
# error (exit 5) で set -e により script ごと中断し、PreToolUse では exit != 2 が
# 非ブロッキング扱い = fail-open になる。top-level が null のときは jq が index を
# 許すため error にすらならず、抽出結果が空になって passthrough に落ちる。
# そこで抽出の前に「top-level が object」かつ「tool_input が object または欠落」を
# 一括検査する。jq の `and` は短絡評価なので、左が false のとき右の index は評価
# されず error にならない。tool_input 欠落 (null) は正常な payload の一形態なので
# 従来どおり許容し、フィールド抽出が空になる既存の passthrough 経路に任せる。
# 導出の詳細は block-foreground-subagent.sh の同じガード。
if ! echo "$INPUT" | jq -e 'type == "object" and (.tool_input == null or (.tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。安全側 (fail-closed) で拒否します。"
fi

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# git push パターン検知
# `git push` と、サブコマンド前にオプションが挿入された形を捕捉する。
# 例: git push, git  push, echo | git push, git -C /path push
# 一方で `git config push.default` のような別サブコマンドは誤検知しない。
PUSH_RE='(^|[|&;[:space:]])git([[:space:]]+(-[^[:space:]]+([[:space:]]+[^|&;[:space:]]+)?)?)*[[:space:]]+push([[:space:]]|$)'

if echo "$COMMAND" | grep -qE "$PUSH_RE"; then
  deny_with_reason "git push は Worker から直接実行できません。完了報告で窓口に依頼してください。窓口が push/PR を実施します。"
fi

# eval "git push ..." / bash -c "git push ..." 経由の bypass も明示的に捕捉する
# （Phase 2a, Issue #79）。unwrap_eval_and_bashc が引数文字列を取り出すので、
# その文字列に対しても同じ正規表現を適用する。
while IFS= read -r body; do
  [[ -z "$body" ]] && continue
  if echo "$body" | grep -qE "$PUSH_RE"; then
    deny_with_reason "git push は Worker から直接実行できません（eval/bash -c 経由も検知）。完了報告で窓口に依頼してください。"
  fi
done < <(printf '%s\n' "$COMMAND" | unwrap_eval_and_bashc)

exit 0
