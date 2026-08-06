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

# 空 payload の fail-closed ガード (Issue #834)。jq は「JSON 値がゼロ個」の入力を
# parse error にせず exit 0 + 出力なしで返す。本フックは FILE_PATH 空を既に deny
# しているため空 stdin でも結果的に exit 2 だが、それは「file_path が取れなかった」
# という別条件への相乗りにすぎない。抽出ロジックが将来変わっても payload 不正が
# 素通りしないよう、意図を明示したガードを jq の前に置く。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。安全側 (fail-closed) で拒否します。"
fi

# 不正 JSON / 非 object payload の fail-closed ガード (Issue #834)。
# `VAR=$(printf '%s\n' "$INPUT" | jq ...)` は parse error (exit 4) や非 object への index
# error (exit 5) で set -e により script ごと中断し、PreToolUse では exit != 2 が
# 非ブロッキング扱い = fail-open になる。top-level が null のときは jq が index を
# 許すため error にすらならず、抽出結果が空になって passthrough に落ちる。
# そこで抽出の前に「top-level が object」かつ「tool_input が object または欠落」を
# 一括検査する。jq の `and` は短絡評価なので、左が false のとき右の index は評価
# されず error にならない。tool_input 欠落 (null) は正常な payload の一形態なので
# 従来どおり許容し、フィールド抽出が空になる既存の passthrough 経路に任せる。
# 導出の詳細は block-foreground-subagent.sh の同じガード。
#
# 入力は `echo` ではなく `printf '%s\n'` で渡す。`echo "$INPUT"` は INPUT が "-n" / "-e"
# / "-E" 等の echo オプションと完全一致すると 1 バイトも出力せず、jq が「JSON 値ゼロ個」
# として exit 0 を返してガードを素通りする (実測で確認)。
# また `-s` (slurp) で入力ストリーム全体を 1 つの配列にまとめ `length == 1` を要求する。
# jq は既定で「JSON 値の連なり」を受け付けるため、slurp しないと JSON object を 2 個
# 並べた payload で述語が各値について真になり exit 0 になる。その後の抽出は値を改行で
# 連結して返す (例: tool_name が "Edit\nEdit") ので、ツール名の一致判定を外して
# passthrough に落ちる。PreToolUse payload は常に単一 object なので 1 個だけを受け付ける。
if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。安全側 (fail-closed) で拒否します。"
fi

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty')

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
  # LC_ALL=C で [a-z] のロケール拡張（Git Bash で大文字を拾うことがある）を防ぐ
  if LC_ALL=C bash -c '[[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.md$ ]]' _ "$BASENAME"; then
    exit 0
  fi
  deny_with_reason "knowledge/raw/ へのファイル名が不正です。YYYY-MM-DD-{topic}.md 形式（topic は英語 kebab-case）にしてください。"
fi

# それ以外はブロック
deny_with_reason "$FILE_PATH は許可パス外です。作業は $WORKER_DIR 内で行ってください。振り返り記録は $CLAUDE_ORG_PATH/knowledge/raw/YYYY-MM-DD-{topic}.md に書けます。"
