#!/usr/bin/env bash
# Tests for block-adhoc-pr-watch.sh (best-effort 安全網の簡素版)
# Validates: exit code (0=allow/passthrough, 2=block) and deny stderr の誘導文言。
#
# 確認観点:
#   - Monitor + while + gh pr checks polling ループ  -> block
#   - Bash + until ループ + gh pr checks            -> block
#   - Bash + tools/pr-watch.sh の直接起動           -> block
#   - Bash + 単発 gh pr checks <n>                  -> allow (状態確認は監視でない)
#   - Bash + gh pr view                             -> allow (無関係な gh サブコマンド)
#   - Bash + 無関係コマンド                          -> allow
#   - 壊れた JSON                                   -> allow (best-effort: 判定不能は許可側)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-adhoc-pr-watch.sh"

PASS=0; FAIL=0; TEST_NUM=0
TMPFILES=()
cleanup() { rm -f "${TMPFILES[@]}"; }
trap cleanup EXIT

assert_exit() {
  local expected="$1" actual="$2" desc="$3"
  ((TEST_NUM++))
  if [[ "$actual" -eq "$expected" ]]; then
    echo "ok $TEST_NUM - $desc"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (expected exit $expected, got $actual)"
    ((FAIL++))
  fi
}

assert_stderr_contains() {
  local pattern="$1" file="$2" desc="$3"
  ((TEST_NUM++))
  if grep -qF "$pattern" "$file" 2>/dev/null; then
    echo "ok $TEST_NUM - $desc"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (stderr did not contain '$pattern')"
    ((FAIL++))
  fi
}

run_hook() {
  local json="$1" stderr_file="$2"
  local exit_code=0
  printf '%s' "$json" | bash "$HOOK" 2>"$stderr_file" || exit_code=$?
  echo "$exit_code"
}

# jq で command を安全に JSON エンコードして payload を作る (ループ構文等の
# クォート事故を避ける)
make_payload() {
  local tool="$1" cmd="$2"
  jq -cn --arg tool "$tool" --arg cmd "$cmd" '{tool_name: $tool, tool_input: {command: $cmd}}'
}

# --- Block Cases ---

# 1. Monitor + while + gh pr checks polling ループ (実害と同形) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Monitor" 'while true; do gh pr checks 51 --json name,bucket; sleep 30; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Monitor: while + gh pr checks polling loop is blocked"
assert_stderr_contains "/pr-watch-pane" "$stderr" "deny stderr names the canonical path /pr-watch-pane"
assert_stderr_contains "人間に報告して指示を仰いでください" "$stderr" "deny stderr instructs to consult the human"

# 2. Bash + until ループ + gh pr checks -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'until gh pr checks 51 --json bucket | jq -e "all(.bucket == \"pass\")"; do sleep 60; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: until loop + gh pr checks is blocked"

# 3. Bash + tools/pr-watch.sh の直接起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'setsid bash tools/pr-watch.sh 51 < /dev/null > /dev/null 2>&1 &')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: direct launch of tools/pr-watch.sh is blocked"
assert_stderr_contains "/pr-watch-pane" "$stderr" "pr-watch.sh deny stderr names /pr-watch-pane"

# --- Allow Cases ---

# 4. Bash + 単発 gh pr checks <n> (状態確認) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr checks 51 --json name,bucket')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: single-shot gh pr checks is allowed (status check, not watching)"

# 5. Bash + gh pr view -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr view 51 --json state,title')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: gh pr view is allowed"

# 6. Bash + 無関係コマンド -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'git status && python3 -m pytest tests/')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: unrelated command is allowed"

# 7. 壊れた JSON -> allow (best-effort 安全網: 判定不能は許可側に倒す)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name": "Bash", broken' "$stderr")
assert_exit 0 "$ec" "broken JSON is allowed (best-effort safety net, not a gate)"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
