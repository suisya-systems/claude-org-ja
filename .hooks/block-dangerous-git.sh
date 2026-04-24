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
#      これにより `echo --force; git push origin main` のような複合コマンド
#      で別セグメントの文字列を拾う false positive を回避する。
#   2. 各セグメントについて、`git` トークン経由で push/reset/branch
#      サブコマンドが呼ばれているかを判定し、同一セグメント内に
#      対応する破壊的フラグが独立トークンとして存在するときだけ拒否する。
#
# 既知の制限:
#   - 引用符内の `;` 等を境界として誤分割する可能性がある。誤分割しても
#     false negative ではなく解析精度低下に倒れるため安全側。
#   - 同一 git invocation の引数（commit メッセージ本文等）に
#     "--force" 等の文字列を含めると false positive で拒否される。
#     その場合は別表現に書き換えること。

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

# セグメント分割
SEGMENTS=$(printf '%s' "$COMMAND" | sed -E 's/(\|\||&&|;|\|)/\n/g')

while IFS= read -r segment; do
  [[ -z "$segment" ]] && continue

  # 1) git push の force 系
  if segment_has_git_subcmd "$segment" "push"; then
    if echo "$segment" | grep -qE '(^|[[:space:]])--force(-with-lease)?([[:space:]=]|$)'; then
      deny_with_reason "git push の force 系フラグは禁止です。履歴の書き換えはレビュー後に窓口経由で実施してください。"
    fi
    if echo "$segment" | grep -qE '(^|[[:space:]])-f([[:space:]]|$)'; then
      deny_with_reason "git push の短縮 force フラグは禁止です。履歴の書き換えはレビュー後に窓口経由で実施してください。"
    fi
    # バンドル短オプション (-fu / -uf 等)
    if echo "$segment" | grep -qE '(^|[[:space:]])-[a-zA-Z]*f[a-zA-Z]*([[:space:]]|$)'; then
      deny_with_reason "git push のバンドル短オプションに force フラグが含まれています。履歴の書き換えはレビュー後に窓口経由で実施してください。"
    fi
  fi

  # 2) git reset --hard
  if segment_has_git_subcmd "$segment" "reset"; then
    if echo "$segment" | grep -qE '(^|[[:space:]])--hard([[:space:]=]|$)'; then
      deny_with_reason "git reset --hard は禁止です。未コミット変更が失われます。git stash か別ブランチへの退避を検討してください。"
    fi
  fi

  # 3) git branch -D / git branch --delete --force
  if segment_has_git_subcmd "$segment" "branch"; then
    if echo "$segment" | grep -qE '(^|[[:space:]])-D([[:space:]]|$)'; then
      deny_with_reason "git branch -D は禁止です。未マージのブランチが消えます。-d（小文字）で安全削除を試すか、窓口に確認してください。"
    fi
    if echo "$segment" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)' && \
       echo "$segment" | grep -qE '(^|[[:space:]])--force([[:space:]=]|$)'; then
      deny_with_reason "git branch --delete --force は禁止です（-D 相当）。-d で安全削除を試すか、窓口に確認してください。"
    fi
  fi

done <<< "$SEGMENTS"

exit 0
