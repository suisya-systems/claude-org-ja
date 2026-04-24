#!/usr/bin/env bash
# PreToolUse Hook: 破壊的な git 操作をブロック
# 方式: exit 2 + stderr メッセージ でブロック
#
# ブロック対象:
#   - git push --force / -f / --force-with-lease  （履歴書き換え）
#   - git reset --hard                            （未コミット変更の消失）
#   - git branch -D / --delete --force            （未マージブランチ削除）
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

# ヘルパ: コマンド文字列に「git <subcmd>」のサブコマンドが含まれるか判定
# (`git -C path subcmd ...` のようにグローバルオプションが挟まる形も拾う)
has_git_subcommand() {
  local subcmd="$1"
  echo "$COMMAND" | grep -qE "(^|[|&;[:space:]])git([[:space:]]+(-[^[:space:]]+([[:space:]]+[^|&;[:space:]]+)?)?)*[[:space:]]+${subcmd}([[:space:]]|$)"
}

# 1) git push --force / --force-with-lease / -f / バンドル -fu 等
if has_git_subcommand "push"; then
  # --force, --force-with-lease を独立トークンとして検知
  if echo "$COMMAND" | grep -qE '(^|[[:space:]])--force(-with-lease)?([[:space:]=]|$)'; then
    deny_with_reason "git push --force / --force-with-lease は禁止です。履歴の書き換えはレビュー後に窓口経由で実施してください。"
  fi
  # -f を独立短オプションとして検知
  if echo "$COMMAND" | grep -qE '(^|[[:space:]])-f([[:space:]]|$)'; then
    deny_with_reason "git push -f は禁止です（--force 相当）。履歴の書き換えはレビュー後に窓口経由で実施してください。"
  fi
  # バンドル短オプション (-fu / -uf / -uvf 等) に f が混じるケース
  # 注: 数字や = を含まない、英字だけのバンドル短オプションを対象とする。
  if echo "$COMMAND" | grep -qE '(^|[[:space:]])-[a-zA-Z]*f[a-zA-Z]*([[:space:]]|$)'; then
    deny_with_reason "git push のバンドル短オプションに -f が含まれています（--force 相当）。履歴の書き換えはレビュー後に窓口経由で実施してください。"
  fi
fi

# 2) git reset --hard
if has_git_subcommand "reset"; then
  if echo "$COMMAND" | grep -qE '(^|[[:space:]])--hard([[:space:]=]|$)'; then
    deny_with_reason "git reset --hard は禁止です。未コミット変更が失われます。git stash か別ブランチへの退避を検討してください。"
  fi
fi

# 3) git branch -D / git branch --delete --force
if has_git_subcommand "branch"; then
  # -D 単独
  if echo "$COMMAND" | grep -qE '(^|[[:space:]])-D([[:space:]]|$)'; then
    deny_with_reason "git branch -D は禁止です。未マージのブランチが消えます。-d（小文字）で安全削除を試すか、窓口に確認してください。"
  fi
  # --delete --force / --force --delete の並び
  if echo "$COMMAND" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)' && \
     echo "$COMMAND" | grep -qE '(^|[[:space:]])--force([[:space:]=]|$)'; then
    deny_with_reason "git branch --delete --force は禁止です（-D 相当）。-d で安全削除を試すか、窓口に確認してください。"
  fi
fi

exit 0
