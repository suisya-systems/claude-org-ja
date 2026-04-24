#!/usr/bin/env bash
# PreToolUse Hook: git commit / git push の --no-verify をブロック
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   --no-verify は pre-commit / pre-push hook（Issue #69 の secret スキャナを含む）
#   をスキップするフラグ。ワーカー Claude が秘匿情報スキャンを抜けて commit / push
#   するのを防ぐため、Claude のツール呼び出しレベルで拒否する。
#
# 入力: stdin から PreToolUse JSON
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 既知の制限:
#   コマンド文字列を正規表現で扱うため、git commit のメッセージ本文に
#   `--no-verify` という文字列を含めると false positive で拒否される。
#   その場合は別の表現（例: 「no-verify フラグ」）に書き換えること。
#   多層防御の最後の壁として false positive 寄りに振る方が安全。

set -euo pipefail

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

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# git commit / git push のサブコマンドが含まれているかをまずチェック。
# 含まれていなければ早期 return（誤検知防止）。
# `git -C path commit ...` のようにオプションが挟まる形も拾う。
if ! echo "$COMMAND" | grep -qE '(^|[|&;[:space:]])git([[:space:]]+(-[^[:space:]]+([[:space:]]+[^|&;[:space:]]+)?)?)*[[:space:]]+(commit|push)([[:space:]]|$)'; then
  exit 0
fi

# --no-verify は単独語として現れる（=value 形式は git に存在しない）。
# 隣接位置に依らず、独立トークンとして検知する。
if echo "$COMMAND" | grep -qE '(^|[[:space:]])--no-verify([[:space:]]|$)'; then
  deny_with_reason "git commit / git push の --no-verify は禁止です。pre-commit secret スキャナ（Issue #69）を必ず通してください。誤検知の場合は allow-secret マーカー、緊急時は SKIP_SECRET_SCAN=1 を使ってください。"
fi

exit 0
