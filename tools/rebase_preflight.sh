#!/usr/bin/env bash
# rebase の前に、上流との差分に sandbox の中で書けないパスが含まれるかを判定する。
#
# 使い方（ワーカーの作業ディレクトリで、git rebase の前に実行する）:
#   bash tools/rebase_preflight.sh <base-ref>
#   例: bash tools/rebase_preflight.sh origin/main
#
# なぜ要るか: ワーカーの sandbox は Claude Code 本体の組み込みで .claude/skills・.claude/hooks・
# 設定ファイル等を読み取り専用にし、無いファイル（.gitmodules・.mcp.json・.claude/agents 等）
# には /dev/null のかぶせ物（文字デバイス）を置く。上流がそこを変えていると rebase は
# 上流の中身を書く途中で `could not detach HEAD` で止まり、HEAD は動かないまま作業ツリーに
# 上流の中身が残る（Issue #1217）。ここで先に見つけて rebase させない。
#
# 判定は保護パスのリストを持たず、実際のファイルシステムで行う（Claude Code の版で
# 保護の範囲が変わっても追従する）。HEAD と <base-ref> の間で変わるパスごとに:
#   - パスがある: 通常ファイルかシンボリックリンクで、それ自体と親ディレクトリが書ける
#   - パスが無い: いちばん近い既存の祖先がディレクトリで、書ける
# を満たさなければ書けないとみなす。かぶせ物の文字デバイスは test -w が真を返すので、
# 種類（通常ファイル / ディレクトリか）でも見る。
#
# 終了コードと最終行:
#   0  REBASE_PREFLIGHT: ok        書けないパスは無い。sandbox の中で rebase してよい
#   1  REBASE_PREFLIGHT: blocked   書けないパスがある（直前の行に列挙）。rebase しない
#   2  使い方の誤り / <base-ref> が解決できない

set -uo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: bash tools/rebase_preflight.sh <base-ref>" >&2
  exit 2
fi
base="$1"

top=$(git rev-parse --show-toplevel) || exit 2
cd "$top" || exit 2
if ! git rev-parse --verify --quiet "$base^{commit}" >/dev/null; then
  echo "rebase_preflight: cannot resolve base ref: $base" >&2
  exit 2
fi

writable_target() {
  local p="$1" d
  if [[ -e "$p" || -L "$p" ]]; then
    [[ -f "$p" || -L "$p" ]] || return 1
    [[ -L "$p" || -w "$p" ]] || return 1
    d=$(dirname "$p")
  else
    d=$(dirname "$p")
    while [[ ! -e "$d" && ! -L "$d" ]]; do d=$(dirname "$d"); done
  fi
  [[ -d "$d" && -w "$d" ]]
}

blocked=0
while IFS= read -r -d '' p; do
  if ! writable_target "$p"; then
    echo "not writable in sandbox: $p"
    blocked=1
  fi
done < <(git diff --no-renames --name-only -z HEAD "$base" --)

if [[ "$blocked" -eq 1 ]]; then
  echo "REBASE_PREFLIGHT: blocked"
  exit 1
fi
echo "REBASE_PREFLIGHT: ok"
