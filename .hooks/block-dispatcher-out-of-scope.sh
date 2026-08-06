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

# 空 payload の fail-closed ガード (Issue #834)。jq は「JSON 値がゼロ個」の入力を
# parse error にせず exit 0 + 出力なしで返すため、空 stdin では TOOL_NAME が空文字に
# なり、下の case が対象外扱いの passthrough に落ちて enforcement が素通りする。
# jq に渡す前に明示的に弾く。導出の詳細は block-foreground-subagent.sh の同じガード。
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

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')

# Edit / Write / NotebookEdit 以外はこのフックの対象外
case "$TOOL_NAME" in
  Edit|Write|NotebookEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty')
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
