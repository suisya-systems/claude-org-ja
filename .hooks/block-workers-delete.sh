#!/usr/bin/env bash
# PreToolUse Hook: workers ディレクトリ配下の再帰的ディレクトリ削除をブロックする
# 対象: 窓口（Secretary）の Bash コマンド
# 方式: exit 2 + stderr メッセージ でブロック
#
# ブロック対象:  rm -rf workers/..., rm -r workers/... 等（再帰削除）
# 許可:          rm workers/dir/file.txt 等（個別ファイル削除）

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
    echo "ブロック: realpath も python も利用できません。" >&2
    exit 2
  fi
}

# jq チェック（jq がなければこの Hook をスキップして許可する）
# 他の Hook（check-worker-boundary 等）は fail-closed だが、この Hook は窓口の全 Bash コマンドに
# 適用されるため、jq 未イ��ストール時に全コマンドをブロックするのは過剰。
# jq なし環境でもスキルの文言による指示レベルの保護は残る。
if ! command -v jq &>/dev/null; then
  exit 0
fi

# stdin から JSON を読み取り
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

if [[ "$TOOL_NAME" != "Bash" ]]; then
  exit 0
fi

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# ccmux / wezterm cli コマンドは除外する
# ワーカー起動時に --cwd workers/... と -p "...rm..." が共存し偽陽性を起こすため
if echo "$COMMAND" | grep -qE '(^|[|&;[:space:]])(ccmux|wezterm)[[:space:]]'; then
  exit 0
fi

# workers ディレクトリのパスを org-config.md から読み取って解決する
# Hook はプロジェクトルート（aainc-wezterm/）から実行される前提
WORKERS_REL=$(grep 'workers_dir:' registry/org-config.md 2>/dev/null | sed 's/.*workers_dir:[[:space:]]*//' | tr -d '[:space:]')
if [[ -z "$WORKERS_REL" ]]; then
  # org-config.md が読めない場合はスキップ（Hook の責務外）
  exit 0
fi
WORKERS_CANONICAL=$(portable_realpath "$WORKERS_REL")

# 判定ロジック: 「再帰削除コマンドが含まれる」AND「workers パスが含まれる」
# 引数パースではなく文字列マッチで判定する（for ループ等の回避パターンにも対応）

# 条件1: 再帰削除コマンドが含まれるか
# 検知対象:
#   A) 短オプション: rm -r, rm -rf, rm -R, rm -f -r 等
#   B) 長オプション: rm --recursive, rm --force --recursive 等
# 除外: --preserve-root 等（-r/-R を含むが再帰削除ではない長オプション）
#
# 既知���限界: シェル変数経由の間接パス（例: x=../workers; rm -rf "$x"）は
# 文字列マッチでは検知できない。スキルの文言による指示レベルの保護で補完する。
HAS_RECURSIVE=false
# 短オプション内の -r/-R
if echo "$COMMAND" | grep -qE '(^|[|&;[:space:]])rm[[:space:]]+-[a-zA-Z]*[rR]|(^|[|&;[:space:]])rm[[:space:]].*[[:space:]]-[a-zA-Z]*[rR]'; then
  HAS_RECURSIVE=true
fi
# 長オプション --recursive
if echo "$COMMAND" | grep -qE '(^|[|&;[:space:]])rm[[:space:]].*--recursive'; then
  HAS_RECURSIVE=true
fi
if [[ "$HAS_RECURSIVE" != "true" ]]; then
  exit 0
fi

# 条件2: workers ディレクトリのパスが含まれるか
# 正規化済み絶対パスの複数表記で判定する（相対パスの表記揺れに依存しない）
WORKERS_WIN=$(echo "$WORKERS_CANONICAL" | sed 's|^/\([a-zA-Z]\)/|\U\1:/|')  # /c/... → C:/...
WORKERS_BACKSLASH=$(echo "$WORKERS_WIN" | tr '/' '\\')  # C:/... → C:\...
# org-config.md の生の値も含める（../workers, ../../other 等）
WORKERS_REL_CANONICAL=$(portable_realpath "$WORKERS_REL")  # 二重正規化防止: 既に同じ値になるはず

FOUND=false
for PATTERN in "$WORKERS_CANONICAL" "$WORKERS_WIN" "$WORKERS_BACKSLASH" "$WORKERS_REL"; do
  if echo "$COMMAND" | grep -qF "$PATTERN"; then
    FOUND=true
    break
  fi
done

if [[ "$FOUND" == "true" ]]; then
  deny_with_reason "workers ディレクトリの再帰的削除は禁止されています。過去の作業成果が含まれている可能性があります。本当に削除が必要な場合はユーザーが手動で実行してください。"
fi

exit 0
