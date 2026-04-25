#!/usr/bin/env bash
# PreToolUse Hook: Edit|Write のファイルパスが Worker ディレクトリ内か検証する
# 方式: exit 2 + stderr メッセージ でブロック

set -euo pipefail

# Helper: deny decision を stderr + exit 2 で返す
deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

# Helper: macOS/WSL/Windows(Git Bash) 対応の realpath -m 相当
# 1. GNU realpath -m を試行（WSL, Linux, MSYS2/Git Bash で動作）
# 2. 失敗時（macOS）は python3 → python の順でフォールバック
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

# jq チェック (fail closed)
if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

# 環境変数チェック
if [[ -z "${WORKER_DIR:-}" || -z "${CLAUDE_ORG_PATH:-}" ]]; then
  echo "ブロック: WORKER_DIR または CLAUDE_ORG_PATH が設定されていません。" >&2
  exit 2
fi

# stdin から JSON を読み取り
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [[ -z "$FILE_PATH" ]]; then
  deny_with_reason "file_path が取得できませんでした。"
fi

# Helper: バックスラッシュをフォワードスラッシュに統一（Windows対応）
normalize_slashes() {
  echo "$1" | tr '\\' '/'
}

# Helper: ドライブレター表記を統一（Git Bash /c/ → C:/ 変換 + 大文字統一）
# Git Bash(MSYS2) の realpath は入力形式に応じて /c/... と C:/... を返し分けるため、
# 全てのパスを C:/ 形式に統一してプレフィックスマッチの不一致を防ぐ。
# Linux/macOS では /home/... 等のパスは変更しない（単一文字ディレクトリ名のみ変換）。
normalize_drive_letter() {
  local p="$1"
  # /c/... → c:/... (MSYS2 → Windows 形式)
  if [[ "$p" =~ ^/([a-zA-Z])/(.*) ]]; then
    p="${BASH_REMATCH[1]}:/${BASH_REMATCH[2]}"
  fi
  # c:/... → C:/... (ドライブレター大文字統一)
  if [[ "$p" =~ ^([a-z]):/ ]]; then
    local upper
    upper=$(echo "${BASH_REMATCH[1]}" | tr '[:lower:]' '[:upper:]')
    p="${upper}${p:1}"
  fi
  echo "$p"
}

# パス正規化 (../traversal, symlink, C:/ vs /c/ 形式の統一, スラッシュ方向の統一)
CANONICAL_FILE=$(normalize_drive_letter "$(normalize_slashes "$(portable_realpath "$FILE_PATH")")")
CANONICAL_WORKER=$(normalize_drive_letter "$(normalize_slashes "$(portable_realpath "$WORKER_DIR")")")
CANONICAL_CLAUDE_ORG=$(normalize_drive_letter "$(normalize_slashes "$(portable_realpath "$CLAUDE_ORG_PATH")")")

# 許可パス 1: Worker ディレクトリ内
if [[ "$CANONICAL_FILE" == "$CANONICAL_WORKER/"* ]]; then
  exit 0
fi

# 許可パス 2: Claude Code プランファイル (~/.claude/plans/)
CLAUDE_PLANS="$(normalize_drive_letter "$(normalize_slashes "$(portable_realpath "${HOME}/.claude/plans")")")"
if [[ "$CANONICAL_FILE" == "$CLAUDE_PLANS/"* ]]; then
  exit 0
fi

# 許可パス 3: 振り返り記録 (knowledge/raw/YYYY-MM-DD-{topic}.md)
KNOWLEDGE_RAW="$CANONICAL_CLAUDE_ORG/knowledge/raw"
if [[ "$CANONICAL_FILE" == "$KNOWLEDGE_RAW/"* ]]; then
  BASENAME=$(basename "$CANONICAL_FILE")
  if [[ "$BASENAME" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.md$ ]]; then
    exit 0
  fi
  deny_with_reason "knowledge/raw/ へのファイル名が不正です。YYYY-MM-DD-{topic}.md 形式（topic は英語 kebab-case）にしてください。"
fi

# それ以外はブロック
deny_with_reason "$FILE_PATH は許可パス外です。作業は $WORKER_DIR 内で行ってください。振り返り記録は $CLAUDE_ORG_PATH/knowledge/raw/YYYY-MM-DD-{topic}.md に書けます。"
