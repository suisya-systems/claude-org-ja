#!/usr/bin/env bash
# tools/rebase_preflight.sh のテスト
# 実行: bash tests/test-rebase-preflight.sh
#
# sandbox の読み取り専用マウントとかぶせ物は CI では作れないので、書込み権を外した
# ディレクトリ / ファイルと、文字デバイスの代わりの FIFO（通常ファイルでないもの）で模す。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PREFLIGHT="$REPO_ROOT/tools/rebase_preflight.sh"
PASS=0
FAIL=0

if [[ "$(id -u)" -eq 0 ]]; then
  echo "SKIP: root ignores write permission bits"
  echo "# 0 passed, 0 failed"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'chmod -R u+w "$WORK"; rm -r "$WORK"' EXIT
R="$WORK/repo"
git init -q "$R"
git -C "$R" config user.email t@example.com
git -C "$R" config user.name t
mkdir -p "$R/ro" "$R/rw"
echo a > "$R/ro/file"; echo a > "$R/rofile"; echo a > "$R/rw/file"
git -C "$R" add ro rofile rw
git -C "$R" commit -qm base
git -C "$R" branch base-commit

# 上流側: パスを 1 つ変える / 足すコミットを作り、その sha を返す
upstream_touching() {
  local p="$1"
  git -C "$R" switch -q --detach base-commit
  mkdir -p "$R/$(dirname "$p")"
  echo b >> "$R/$p"
  git -C "$R" add -- "$p"
  git -C "$R" commit -qm "touch $p"
  git -C "$R" rev-parse HEAD
  git -C "$R" switch -q --detach base-commit
}
UP_RO_DIR=$(upstream_touching ro/file)
UP_RO_FILE=$(upstream_touching rofile)
UP_RW=$(upstream_touching rw/file)
UP_NEW_RW=$(upstream_touching rw/sub/new)
UP_NEW_RO=$(upstream_touching ro/sub/new)
UP_MASK=$(upstream_touching .gitmodules)
UP_UNDER_MASK=$(upstream_touching agents/x.md)

# sandbox を模す
chmod a-w "$R/ro" "$R/rofile"
mkfifo "$R/.gitmodules" "$R/agents"

run() {
  local description="$1" base="$2" expected_exit="$3" expected_word="$4"
  local actual_exit=0 output
  output=$(cd "$R" && bash "$PREFLIGHT" "$base" 2>&1) || actual_exit=$?
  if [[ "$actual_exit" -eq "$expected_exit" ]] && printf '%s\n' "$output" | tail -1 | grep -q "$expected_word"; then
    echo "  PASS: $description"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $description (expected exit $expected_exit / $expected_word, got $actual_exit: $(printf '%s\n' "$output" | tail -1))"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== rebase_preflight.sh tests ==="
run "書けるディレクトリの変更 → ok" "$UP_RW" 0 "REBASE_PREFLIGHT: ok"
run "書けるディレクトリへの新規（途中ディレクトリも新規）→ ok" "$UP_NEW_RW" 0 "REBASE_PREFLIGHT: ok"
run "読み取り専用ディレクトリの中の変更 → blocked" "$UP_RO_DIR" 1 "REBASE_PREFLIGHT: blocked"
run "読み取り専用ファイルの変更 → blocked" "$UP_RO_FILE" 1 "REBASE_PREFLIGHT: blocked"
run "読み取り専用ディレクトリの下への新規 → blocked" "$UP_NEW_RO" 1 "REBASE_PREFLIGHT: blocked"
run "かぶせ物（通常ファイルでない）の位置への新規 → blocked" "$UP_MASK" 1 "REBASE_PREFLIGHT: blocked"
run "かぶせ物を祖先に持つパスへの新規 → blocked" "$UP_UNDER_MASK" 1 "REBASE_PREFLIGHT: blocked"
run "解決できない base → exit 2" no-such-ref 2 "cannot resolve base ref"

# 上流を取り込み済み（base が HEAD の祖先）なら、自分のコミットが書けないパスを変えていても ok
git -C "$R" switch -q --detach "$UP_RO_FILE"
run "取り込み済みで自分のコミットだけが書けないパスを変える → ok" base-commit 0 "REBASE_PREFLIGHT: ok"
run "取り込み済みの上流そのもの → ok" "$UP_RO_FILE" 0 "REBASE_PREFLIGHT: ok"
git -C "$R" switch -q --detach base-commit

out=$(cd "$R" && bash "$PREFLIGHT" "$UP_RO_DIR" 2>&1)
if printf '%s\n' "$out" | grep -qx "not writable in sandbox: ro/file"; then
  echo "  PASS: 書けないパスを列挙する"; PASS=$((PASS + 1))
else
  echo "  FAIL: 書けないパスを列挙する (got: $out)"; FAIL=$((FAIL + 1))
fi

echo ""
echo "# $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
