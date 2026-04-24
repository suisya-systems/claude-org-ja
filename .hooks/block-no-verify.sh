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
#      引用符（" / '）内の区切り文字は無視する（split_segments の awk 実装）。
#      これにより `echo --no-verify; git commit -m ok` のような複合コマンド
#      で別セグメントの文字列を拾う false positive と、
#      `git commit -m "a ; b" --no-verify` のような引用符内 separator での
#      回避（false negative）の両方を防ぐ。
#   2. 各セグメントについて、`git` トークン経由で commit/push サブコマンド
#      が呼ばれているかを判定し、同一セグメント内に verify-bypass フラグ
#      が独立トークンとして存在するときだけ拒否する。
#
# 既知の制限:
#   - サブコマンド判定は loose match（`git` トークンの後の任意の場所に
#     subcmd トークンが現れれば match）。`echo git commit --no-verify` の
#     ような同一セグメント内のリテラル文字列も拒否される。多層防御の
#     最後の壁としては false positive 寄りの方が安全であり、許容する。
#   - 同一 git invocation の引数（commit メッセージ本文等）に
#     verify-bypass の文字列を含めると false positive で拒否される。
#     その場合は別表現に書き換えること。
#   - $(...) や `...` のサブシェル境界、バックスラッシュエスケープは
#     扱わない（split_segments の制限を参照）。
#   - 人間が直接ターミナルで叩く場合は本フックは効かない。Claude Code の
#     Bash ツール経由でのみ作用する。

set -euo pipefail

# shellcheck source=lib/segment-split.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/segment-split.sh"

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

if ! command -v awk &>/dev/null; then
  echo "ブロック: awk がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# 引用符対応セグメント分割
while IFS= read -r segment; do
  [[ -z "$segment" ]] && continue

  # git commit / git push の有無（loose match: `git` トークン後に subcmd
  # トークンが同一セグメント内に存在すれば match。`git -C "C:/Program Files/repo"
  # commit` のような空白入り引用符パスにも対応する）。
  has_git_commit=0
  has_git_push=0
  if echo "$segment" | grep -qE '(^|[[:space:]])git[[:space:]]+commit([[:space:]]|$)' \
     || echo "$segment" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]]commit([[:space:]]|$)'; then
    has_git_commit=1
  fi
  if echo "$segment" | grep -qE '(^|[[:space:]])git[[:space:]]+push([[:space:]]|$)' \
     || echo "$segment" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]]push([[:space:]]|$)'; then
    has_git_push=1
  fi
  [[ $has_git_commit -eq 0 && $has_git_push -eq 0 ]] && continue

  # 同一セグメント内に verify-bypass フラグが独立トークンとして存在するか
  if echo "$segment" | grep -qE '(^|[[:space:]])--no-verify([[:space:]]|$)'; then
    if [[ $has_git_commit -eq 1 ]]; then
      deny_with_reason "git commit の verify-bypass フラグは禁止です。pre-commit secret スキャナ（Issue #69）を必ず通してください。誤検知の場合は allow-secret マーカー、緊急時は SKIP_SECRET_SCAN=1 を使ってください。"
    else
      deny_with_reason "git push の verify-bypass フラグは禁止です。pre-push hook を迂回するため拒否します（現在 pre-push は未配備ですが、将来追加された hook を保護する目的で先行ブロックします）。push が必要な場合は窓口経由で実施してください。"
    fi
  fi
done < <(printf '%s' "$COMMAND" | split_segments)

exit 0
