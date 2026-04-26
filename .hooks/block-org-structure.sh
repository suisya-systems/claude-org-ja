#!/usr/bin/env bash
# PreToolUse Hook: claude-org の組織構造ディレクトリの再作成をブロックする
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

# Helper: バックスラッシュをフォワードスラッシュに統一（Windows対応）
normalize_slashes() {
  echo "$1" | tr '\\' '/'
}

# Helper: ドライブレター表記を統一（Git Bash /c/ → C:/ 変換 + 大文字統一）
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
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# ブロック対象ディレクトリ名
# - 全深度でブロック: .claude, .dispatcher, .curator, .state (claude-org 固有)
# - WORKER_DIR 直下のみブロック: registry, dashboard, knowledge (一般的な名前)
ALWAYS_BLOCKED=('.claude' '.dispatcher' '.curator' '.state')
ROOT_ONLY_BLOCKED=('registry' 'dashboard' 'knowledge')

CANONICAL_WORKER=$(normalize_drive_letter "$(normalize_slashes "$(portable_realpath "$WORKER_DIR")")")
CANONICAL_CLAUDE_ORG=$(normalize_drive_letter "$(normalize_slashes "$(portable_realpath "$CLAUDE_ORG_PATH")")")

# --- Write/Edit の場合: file_path をチェック ---
if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
  if [[ -z "$FILE_PATH" ]]; then
    exit 0
  fi

  CANONICAL_FILE=$(normalize_drive_letter "$(normalize_slashes "$(portable_realpath "$FILE_PATH")")")

  # knowledge/raw/ への振り返り記録は許可 (check-worker-boundary.sh と整合)
  KNOWLEDGE_RAW="$CANONICAL_CLAUDE_ORG/knowledge/raw"
  if [[ "$CANONICAL_FILE" == "$KNOWLEDGE_RAW/"* ]]; then
    BASENAME=$(basename "$CANONICAL_FILE")
    if [[ "$BASENAME" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.md$ ]]; then
      exit 0
    fi
  fi

  # 例外: WORKER_DIR/.claude/plans/ への書き込みは許可 (Claude Code Plan モード用)
  PLANS_DIR="$CANONICAL_WORKER/.claude/plans"
  if [[ "$CANONICAL_FILE" == "$PLANS_DIR/"* || "$CANONICAL_FILE" == "$PLANS_DIR" ]]; then
    exit 0
  fi

  # 全深度ブロック: パスコンポーネントに含まれるかチェック
  for DIR in "${ALWAYS_BLOCKED[@]}"; do
    if [[ "$CANONICAL_FILE" == *"/$DIR/"* || "$CANONICAL_FILE" == *"/$DIR" ]]; then
      # ただし WORKER_DIR 内のサブプロジェクト（clone 先）は許可
      # WORKER_DIR/project/.claude/ は OK、WORKER_DIR/.claude/ は NG
      # WORKER_DIR 直下かどうか: WORKER_DIR/{dir}/ のパターンに一致するか
      if [[ "$CANONICAL_FILE" == "$CANONICAL_WORKER/$DIR/"* || "$CANONICAL_FILE" == "$CANONICAL_WORKER/$DIR" ]]; then
        deny_with_reason "$DIR/ は claude-org の組織構造ディレクトリです。Worker ディレクトリ直下に作成できません。"
      fi
    fi
  done

  # WORKER_DIR 直下のみブロック
  for DIR in "${ROOT_ONLY_BLOCKED[@]}"; do
    if [[ "$CANONICAL_FILE" == "$CANONICAL_WORKER/$DIR/"* || "$CANONICAL_FILE" == "$CANONICAL_WORKER/$DIR" ]]; then
      deny_with_reason "$DIR/ は claude-org の組織構造ディレクトリです。Worker ディレクトリ直下に作成できません。"
    fi
  done

  exit 0
fi

# --- Bash の場合: コマンド文字列をチェック ---
if [[ "$TOOL_NAME" == "Bash" ]]; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
  if [[ -z "$COMMAND" ]]; then
    exit 0
  fi

  # Windows対応: コマンド文字列中のバックスラッシュをスラッシュに正規化して検査
  # (Write/Edit 側の normalize_slashes と同じ方針。Mac では実質 no-op)
  NORMALIZED_CMD=$(echo "$COMMAND" | tr '\\' '/')

  # ファイル/ディレクトリ作成コマンド + claude-org 構造ディレクトリのパターン検知
  # ベストエフォート: 全てのパターンは捕捉できないが、典型的なものをブロック
  ALL_BLOCKED_NAMES=('.claude' '.dispatcher' '.curator' '.state' 'registry' 'dashboard' 'knowledge')
  for DIR in "${ALL_BLOCKED_NAMES[@]}"; do
    # mkdir, touch, cp, mv + ディレクトリ名を含むパス
    if echo "$NORMALIZED_CMD" | grep -qE "(mkdir|touch|cp|mv)[[:space:]]+.*([[:space:]]|/)${DIR}(/|[[:space:]]|\"|$)"; then
      # knowledge/raw/ への正当な操作は許可
      if [[ "$DIR" == "knowledge" ]] && echo "$NORMALIZED_CMD" | grep -qE "knowledge/raw/"; then
        continue
      fi
      # .claude/plans/ への操作は許可 (Claude Code Plan モード用)
      if [[ "$DIR" == ".claude" ]] && echo "$NORMALIZED_CMD" | grep -qE "\.claude/plans(/|[[:space:]]|\"|$)"; then
        continue
      fi
      deny_with_reason "Bash コマンドで claude-org の組織構造ディレクトリ ($DIR/) を作成しようとしています。"
    fi
  done

  exit 0
fi

# その他のツール名は許可
exit 0
