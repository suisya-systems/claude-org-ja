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
