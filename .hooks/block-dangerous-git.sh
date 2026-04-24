#!/usr/bin/env bash
# PreToolUse Hook: 破壊的な git 操作をブロック
# 方式: exit 2 + stderr メッセージ でブロック
#
# ブロック対象:
#   - git push --force / -f / --force-with-lease   （履歴書き換え）
#   - git reset --hard                              （未コミット変更の消失）
#   - git branch -D / --delete --force              （未マージブランチ削除）
#
# 補足:
#   - git push そのものはワーカーでは block-git-push.sh が先に止める。
#     本フックは「窓口側でうっかり叩いた場合の最後の壁」も兼ねる。
#   - --force-with-lease は --force より安全だが、本フェーズでは
#     workflow から外す方針で一律ブロックする（Phase 2 で再検討）。
#   - TODO(Phase 2): git clean -fd / git checkout -- . / git restore .
#     も同様にブロックする。本フェーズはスコープ外。
#
# 入力: stdin から PreToolUse JSON
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 検知方針:
#   1. Bash コマンド文字列を ; && || | 改行 でセグメントに分割する。
#      引用符（" / '）内の区切り文字は無視する（split_segments の awk 実装）。
#      これにより `echo --force; git push origin main` のような複合コマンド
#      で別セグメントの文字列を拾う false positive と、
#      `git push origin "refs/heads/x; y" --force` のような引用符内
#      separator での回避（false negative）の両方を防ぐ。
#   2. 各セグメントについて、`git` トークン経由で push/reset/branch
#      サブコマンドが呼ばれているかを判定し、同一セグメント内に
#      対応する破壊的フラグが独立トークンとして存在するときだけ拒否する。
#
# 既知の制限:
#   - サブコマンド判定は loose match。同一セグメント内のリテラル文字列も
#     拒否される。多層防御の最後の壁としては false positive 寄りで安全。
#   - 同一 git invocation の引数（commit メッセージ本文等）に
#     "--force" 等の文字列を含めると false positive で拒否される。
#     その場合は別表現に書き換えること。
#   - $(...) や `...` のサブシェル境界、バックスラッシュエスケープは
#     扱わない。

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

# セグメントの中に git の特定サブコマンドが含まれるか判定するヘルパ
segment_has_git_subcmd() {
  local segment="$1"
  local subcmd="$2"
  # 直接形: `git <subcmd> ...`
  if echo "$segment" | grep -qE "(^|[[:space:]])git[[:space:]]+${subcmd}([[:space:]]|$)"; then
    return 0
  fi
  # オプション介在形: `git -C "..." <subcmd> ...`（引用符込み空白入りパス対応）
  if echo "$segment" | grep -qE "(^|[[:space:]])git[[:space:]].*[[:space:]]${subcmd}([[:space:]]|$)"; then
    return 0
  fi
  return 1
}

# 全セグメントを 1 度収集してから既知の代入を抽出し、各セグメントで展開する。
SEGMENTS=()
while IFS= read -r seg; do
  SEGMENTS+=("$seg")
done < <(printf '%s' "$COMMAND" | split_segments)

ASSIGNMENTS=()
while IFS= read -r assign; do
  [[ -n "$assign" ]] && ASSIGNMENTS+=("$assign")
done < <(printf '%s\n' "${SEGMENTS[@]}" | collect_assignments)

for segment in "${SEGMENTS[@]}"; do
  [[ -z "$segment" ]] && continue

  # 既知の VAR=value を展開
  if [[ ${#ASSIGNMENTS[@]} -gt 0 ]]; then
    expanded=$(printf '%s' "$segment" | expand_known_vars "${ASSIGNMENTS[@]}")
  else
    expanded="$segment"
  fi

  # コマンド置換 $(...) / `...` 内のフラグも検査対象に含める
  flat=$(printf '%s' "$expanded" | flatten_substitutions)

  # 1) git push の force 系
  if segment_has_git_subcmd "$flat" "push"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])--force(-with-lease)?([[:space:]=]|$)'; then
      deny_with_reason "git push の force 系フラグは禁止です。履歴の書き換えはレビュー後に窓口経由で実施してください。"
    fi
    if echo "$flat" | grep -qE '(^|[[:space:]])-f([[:space:]]|$)'; then
      deny_with_reason "git push の短縮 force フラグは禁止です。履歴の書き換えはレビュー後に窓口経由で実施してください。"
    fi
    if echo "$flat" | grep -qE '(^|[[:space:]])-[a-zA-Z]*f[a-zA-Z]*([[:space:]]|$)'; then
      deny_with_reason "git push のバンドル短オプションに force フラグが含まれています。履歴の書き換えはレビュー後に窓口経由で実施してください。"
    fi
  fi

  # 2) git reset --hard
  if segment_has_git_subcmd "$flat" "reset"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])--hard([[:space:]=]|$)'; then
      deny_with_reason "git reset --hard は禁止です。未コミット変更が失われます。git stash か別ブランチへの退避を検討してください。"
    fi
  fi

  # 3) git branch -D / git branch --delete --force
  if segment_has_git_subcmd "$flat" "branch"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])-D([[:space:]]|$)'; then
      deny_with_reason "git branch -D は禁止です。未マージのブランチが消えます。-d（小文字）で安全削除を試すか、窓口に確認してください。"
    fi
    if echo "$flat" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)' && \
       echo "$flat" | grep -qE '(^|[[:space:]])--force([[:space:]=]|$)'; then
      deny_with_reason "git branch --delete --force は禁止です（-D 相当）。-d で安全削除を試すか、窓口に確認してください。"
    fi
  fi
done

exit 0
