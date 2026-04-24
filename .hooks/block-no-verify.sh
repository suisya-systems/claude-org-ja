#!/usr/bin/env bash
# PreToolUse Hook: git commit / git push の verify-bypass フラグをブロック
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   verify-bypass フラグは pre-commit / pre-push hook（Issue #69 の secret
#   スキャナを含む）をスキップする。ワーカー Claude が秘匿情報スキャンを
#   迂回して commit / push するのを防ぐため、Claude のツール呼び出し
#   レベルで拒否する。
#
# 入力: stdin から PreToolUse JSON
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 検知方針:
#   1. Bash コマンド文字列を ; && || | 改行 でセグメントに分割する。
#      これにより `echo --no-verify; git commit -m ok` のような複合コマンド
#      で別セグメントの文字列を拾う false positive を回避する。
#   2. 各セグメントについて、`git` トークン経由で commit/push サブコマンド
#      が呼ばれているかを判定し、同一セグメント内に verify-bypass フラグ
#      が独立トークンとして存在するときだけ拒否する。
#
# 既知の制限:
#   - 引用符内の `;` 等を境界として誤分割する可能性がある（例: `git commit
#     -m "msg with ; semicolon"`）。この場合 false negative ではなく、
#     セグメントが分かれて解析の精度が落ちるだけなので安全側に倒れる。
#   - 同一 git invocation の引数（commit メッセージ本文等）に
#     verify-bypass の文字列を含めると false positive で拒否される。
#     その場合は別表現に書き換えること。多層防御の最後の壁としては
#     false positive 寄りの方が安全。
#   - 人間が直接ターミナルで叩く場合は本フックは効かない。Claude Code の
#     Bash ツール経由でのみ作用する。

set -euo pipefail

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# セグメントごとに走査
# sed で ; && || | を改与に置換 → 1 行 1 セグメント
SEGMENTS=$(printf '%s' "$COMMAND" | sed -E 's/(\|\||&&|;|\|)/\n/g')

while IFS= read -r segment; do
  [[ -z "$segment" ]] && continue
  # git commit / git push の有無（loose: `git` トークンの後に subcmd トークンが
  # 同一セグメント内に存在すれば match。`git -C "C:/Program Files/repo" commit`
  # のような空白入り引用符パスにも対応する）。
  has_git_target=0
  if echo "$segment" | grep -qE '(^|[[:space:]])git[[:space:]]+(commit|push)([[:space:]]|$)'; then
    has_git_target=1
  elif echo "$segment" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]](commit|push)([[:space:]]|$)'; then
    has_git_target=1
  fi
  [[ $has_git_target -eq 0 ]] && continue

  # 同一セグメント内に verify-bypass フラグが独立トークンとして存在するか
  if echo "$segment" | grep -qE '(^|[[:space:]])--no-verify([[:space:]]|$)'; then
    deny_with_reason "git commit / git push の verify-bypass フラグは禁止です。pre-commit secret スキャナ（Issue #69）を必ず通してください。誤検知の場合は allow-secret マーカー、緊急時は SKIP_SECRET_SCAN=1 を使ってください。"
  fi
done <<< "$SEGMENTS"

exit 0
