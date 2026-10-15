#!/usr/bin/env bash
# tools/codex_review_gate.sh のテスト
# 実行: bash tests/test-codex-review-gate.sh
#
# 本物の codex は呼ばない。PATH 先頭に stub の codex を置き、ログの中身と exit を
# 変えて「空の合格」の判定（成功実行 1 以上・失敗実行 0・exit 0 のときだけ passed）を確かめる。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GATE="$REPO_ROOT/tools/codex_review_gate.sh"
PASS=0
FAIL=0

WORK="$(mktemp -d)"
trap 'rm -r "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/repo" "$WORK/tmp"
git -C "$WORK/repo" init -q

# stub: STUB_OUT の中身を出力し、STUB_EXIT で終わる。CODEX_HOME も記録する。
cat > "$WORK/bin/codex" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$CODEX_HOME" > "$STUB_DIR/codex_home"
printf '%b' "$STUB_OUT"
exit "$STUB_EXIT"
EOF
chmod +x "$WORK/bin/codex"

run_gate() {
  local description="$1" out="$2" stub_exit="$3" expected_exit="$4" expected_word="$5"
  local actual_exit=0 output
  output=$(cd "$WORK/repo" && PATH="$WORK/bin:$PATH" TMPDIR="$WORK/tmp" STUB_DIR="$WORK" \
    STUB_OUT="$out" STUB_EXIT="$stub_exit" bash "$GATE" origin/main 2>&1) || actual_exit=$?
  if [[ "$actual_exit" -eq "$expected_exit" ]] && printf '%s\n' "$output" | tail -1 | grep -q "CODEX_GATE: $expected_word"; then
    echo "  PASS: $description"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $description (expected exit $expected_exit / $expected_word, got $actual_exit: $(printf '%s\n' "$output" | tail -1))"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== codex_review_gate.sh tests ==="

run_gate "成功実行あり・失敗なし・exit 0 → passed" \
  'exec\n  succeeded in 12ms:\nNo actionable findings were identified\n' 0 0 passed
run_gate "秒・分単位の実行時間も成功実行として数える" \
  '  succeeded in 3s:\n  succeeded in 1m:\n' 0 0 passed
run_gate "コマンド実行ゼロで合格文だけ（空の合格）→ not-established" \
  'No actionable findings were identified\n' 0 1 not-established
run_gate "失敗実行が 1 つでもある → not-established" \
  '  succeeded in 5ms:\n  failed in 7ms:\n' 0 1 not-established
run_gate "codex の exit 非 0 → not-established" \
  '  succeeded in 5ms:\n' 1 1 not-established
run_gate "diff 本文の行頭でない記述は数えない（自己マッチ回避）" \
  '+ grep "succeeded in 5ms"\n+   failed in TUI\n' 0 1 not-established
run_gate "実行時間を伴わない行頭 failed in は失敗に数えない" \
  '  succeeded in 5ms:\n failed in TUI\n' 0 0 passed

# CODEX_HOME は作業ディレクトリ配下（一時ディレクトリ配下でない）に退避される
if [[ "$(cat "$WORK/codex_home")" == "$WORK/repo/.codex-home" && -d "$WORK/repo/.codex-home" ]]; then
  echo "  PASS: CODEX_HOME を \$PWD/.codex-home に退避する"
  PASS=$((PASS + 1))
else
  echo "  FAIL: CODEX_HOME が \$PWD/.codex-home でない: $(cat "$WORK/codex_home")"
  FAIL=$((FAIL + 1))
fi
if grep -qxF '.codex-home/' "$WORK/repo/.git/info/exclude"; then
  echo "  PASS: .codex-home/ を info/exclude に登録する"
  PASS=$((PASS + 1))
else
  echo "  FAIL: .codex-home/ が info/exclude に無い"
  FAIL=$((FAIL + 1))
fi

# 既に CODEX_HOME=$PWD/.codex-home で呼ばれても、認証リンクを自己参照に張り直さない
mkdir -p "$WORK/src"
echo '{}' > "$WORK/src/auth.json"
command rm -f "$WORK/repo/.codex-home/auth.json"
ln -s "$WORK/src/auth.json" "$WORK/repo/.codex-home/auth.json"
(cd "$WORK/repo" && PATH="$WORK/bin:$PATH" TMPDIR="$WORK/tmp" STUB_DIR="$WORK" \
  STUB_OUT='  succeeded in 1ms:\n' STUB_EXIT=0 CODEX_HOME="$WORK/repo/.codex-home" \
  bash "$GATE" origin/main >/dev/null 2>&1)
if [[ "$(readlink "$WORK/repo/.codex-home/auth.json")" == "$WORK/src/auth.json" ]]; then
  echo "  PASS: 退避済みの CODEX_HOME で呼んでも認証リンクを壊さない"
  PASS=$((PASS + 1))
else
  echo "  FAIL: 認証リンクが張り替えられた: $(readlink "$WORK/repo/.codex-home/auth.json")"
  FAIL=$((FAIL + 1))
fi

# codex が無い → unavailable (exit 3)。PATH から stub を外し、本物の codex も見えないようにする
actual_exit=0
output=$(cd "$WORK/repo" && PATH="/usr/bin:/bin" bash "$GATE" origin/main 2>&1) || actual_exit=$?
if command -v -p codex >/dev/null 2>&1; then
  echo "  SKIP: /usr/bin か /bin に codex があり unavailable を再現できない"
elif [[ "$actual_exit" -eq 3 && "$output" == *"CODEX_GATE: unavailable"* ]]; then
  echo "  PASS: codex が無い → unavailable (exit 3)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: codex が無いときの結果 (exit $actual_exit: $output)"
  FAIL=$((FAIL + 1))
fi

# 追跡ファイルの未コミット差分（rebase 残差）→ codex を呼ばず dirty-worktree (exit 4)。
# 未追跡ファイル（sandbox のマウント点等）は見ない
mkdir -p "$WORK/repo/.claude/skills"
echo new > "$WORK/repo/.claude/skills/SKILL.md"
git -C "$WORK/repo" add .claude/skills/SKILL.md
git -C "$WORK/repo" -c user.name=t -c user.email=t@t commit -qm init
echo untracked > "$WORK/repo/untracked.txt"
run_gate "未追跡ファイルだけなら dirty にしない" '  succeeded in 1ms:\n' 0 0 passed
echo old > "$WORK/repo/.claude/skills/SKILL.md"
command rm -f "$WORK/codex_home"
run_gate "追跡ファイルに残差 → dirty-worktree" '  succeeded in 1ms:\n' 0 4 dirty-worktree
if [[ ! -e "$WORK/codex_home" ]]; then
  echo "  PASS: dirty-worktree では codex を呼ばない"
  PASS=$((PASS + 1))
else
  echo "  FAIL: dirty-worktree なのに codex が呼ばれた"
  FAIL=$((FAIL + 1))
fi

actual_exit=0
bash "$GATE" >/dev/null 2>&1 || actual_exit=$?
if [[ "$actual_exit" -eq 2 ]]; then
  echo "  PASS: 引数なし → usage (exit 2)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: 引数なしの exit が $actual_exit"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "# $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
