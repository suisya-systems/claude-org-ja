#!/usr/bin/env bash
# PreToolUse Hook: Dispatcher の Edit/Write 対象パスが業務スコープ外ならブロックする。
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景: ディスパッチャーは Sonnet 制約により permission_mode=bypassPermissions で起動するため、
#       permissions.allow / deny は無効化される（Claude Code 公式仕様）。実効的な書き込み境界は
#       PreToolUse フックでしか強制できない。本フックがその唯一の障壁。
#
# 許可パス（CLAUDE_ORG_PATH を base に解決）:
#   1. <claude_org>/.dispatcher/**            — ディスパッチャーの home（CLAUDE.md / skills 等）
#   2. <claude_org>/.state/**              — inbox/outbox/journal/workers/cursor 等
#   3. <claude_org>/knowledge/raw/YYYY-MM-DD-{topic}.md  — 振り返り記録（kebab-case topic）
#
# それ以外（tools/, dashboard/, tests/, .claude/skills/, docs/, registry/, ワーカー worktree 等）は
# すべてブロックする。

set -euo pipefail

deny_with_reason() {
  echo "ブロック: $1" >&2
  exit 2
}

portable_realpath() {
  local target="$1"
  if result=$(command realpath -m "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  else
    echo "ブロック: realpath -m も python も利用できません。パス正規化に失敗しました。" >&2
    exit 2
  fi
}

normalize_slashes() {
  echo "$1" | tr '\\' '/'
}

# /c/... → C:/... + ドライブレター大文字化（worker boundary hook と同等）
normalize_drive_letter() {
  local p="$1"
  if [[ "$p" =~ ^/([a-zA-Z])/(.*) ]]; then
    p="${BASH_REMATCH[1]}:/${BASH_REMATCH[2]}"
  fi
  if [[ "$p" =~ ^([a-z]):/ ]]; then
    local upper
    upper=$(echo "${BASH_REMATCH[1]}" | tr '[:lower:]' '[:upper:]')
    p="${upper}${p:1}"
  fi
  echo "$p"
}

canonicalize() {
  normalize_drive_letter "$(normalize_slashes "$(portable_realpath "$1")")"
}

if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

if [[ -z "${CLAUDE_ORG_PATH:-}" ]]; then
  echo "ブロック: CLAUDE_ORG_PATH が設定されていません。dispatcher settings.local.json の env を確認してください。" >&2
  exit 2
fi

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Edit / Write / NotebookEdit 以外はこのフックの対象外
case "$TOOL_NAME" in
  Edit|Write|NotebookEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
if [[ -z "$FILE_PATH" ]]; then
  deny_with_reason "file_path が取得できませんでした。"
fi

CANONICAL_FILE=$(canonicalize "$FILE_PATH")
CANONICAL_ORG=$(canonicalize "$CLAUDE_ORG_PATH")

# 許可 1: .dispatcher/ 配下
if [[ "$CANONICAL_FILE" == "$CANONICAL_ORG/.dispatcher/"* ]]; then
  exit 0
fi

# 許可 2: .state/ 配下
if [[ "$CANONICAL_FILE" == "$CANONICAL_ORG/.state/"* ]]; then
  exit 0
fi

# 許可 3: knowledge/raw/YYYY-MM-DD-{topic}.md（振り返り記録）
KNOWLEDGE_RAW="$CANONICAL_ORG/knowledge/raw"
if [[ "$CANONICAL_FILE" == "$KNOWLEDGE_RAW/"* ]]; then
  BASENAME=$(basename "$CANONICAL_FILE")
  # LC_ALL=C で [a-z] のロケール拡張（Windows Git Bash で大文字を拾う）を防ぐ
  if LC_ALL=C bash -c '[[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.md$ ]]' _ "$BASENAME"; then
    exit 0
  fi
  deny_with_reason "knowledge/raw/ へのファイル名が不正です。YYYY-MM-DD-{topic}.md 形式（topic は英語 kebab-case）にしてください。"
fi

deny_with_reason "$FILE_PATH はディスパッチャーの業務スコープ外です。書き込みは .dispatcher/, .state/, knowledge/raw/YYYY-MM-DD-{topic}.md に限定されています。アプリケーションコード（tools/, dashboard/, docs/ 等）の編集はワーカーに委譲してください。"
