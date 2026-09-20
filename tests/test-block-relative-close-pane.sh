#!/usr/bin/env bash
# Tests for .hooks/block-relative-close-pane.sh (Issue #1018)
# Validates: exit code (0=allow/passthrough, 2=block) と deny stderr の誘導文言。
#
# 確認観点:
#   deny 側 (相対セレクタ):
#     - target="focused"                        -> block (2026-09-20 の実事故経路)
#     - target 省略 / null                      -> block (既定が相対セレクタ)
#     - target="secretary"                      -> block (窓口は誰からも閉じさせない)
#     - target="worker-foo" / "dispatcher" 等   -> block (裸の name のフォールスルー)
#     - renga / broker いずれの完全修飾名でも   -> block (transport 非依存)
#   allow 側 (正規経路を壊さないこと):
#     - target=3 / "3" / "%3"                   -> allow (数値 pane id)
#     - target="pr-watch-51"                    -> allow (DD-2 stale-binding carve-out)
#     - close_pane 以外のツール                 -> passthrough
#   fail-closed:
#     - 壊れた payload は tests/test-hooks-payload-fail-closed.sh が横断で固定するので
#       ここでは重複させない。ここで見るのは close_pane 固有の target 判定だけ。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-relative-close-pane.sh"

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

# run_hook <json> <stderr_file> -> echoes exit code
run_hook() {
  local json="$1" stderr_file="$2" ec=0
  printf '%s' "$json" | bash "$HOOK" >/dev/null 2>"$stderr_file" || ec=$?
  echo "$ec"
}

# payload_with_target <tool_name> <raw_json_target_literal>
payload_with_target() {
  printf '{"tool_name":"%s","tool_input":{"target":%s}}' "$1" "$2"
}

RENGA="mcp__renga-peers__close_pane"
BROKER="mcp__org-broker__close_pane"

# --- Deny: 相対セレクタ ---

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"focused"')" "$stderr")
assert_exit 2 "$ec" "renga: target=\"focused\" is blocked"
assert_stderr_contains "Issue #1018" "$stderr" "focused deny stderr cites the incident"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"focused"')" "$stderr")
assert_exit 2 "$ec" "broker: target=\"focused\" is blocked (transport-independent)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"'"$RENGA"'","tool_input":{}}' "$stderr")
assert_exit 2 "$ec" "omitted target is blocked (the default resolves relatively)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" 'null')" "$stderr")
assert_exit 2 "$ec" "target=null is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"secretary"')" "$stderr")
assert_exit 2 "$ec" "target=\"secretary\" is blocked"
assert_stderr_contains "窓口ペイン" "$stderr" "secretary deny stderr names the secretary pane"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"dispatcher"')" "$stderr")
assert_exit 2 "$ec" "target=\"dispatcher\" (bare name) is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"worker-ja-close-pane-guard"')" "$stderr")
assert_exit 2 "$ec" "target=\"worker-{task_id}\" (bare name) is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"curator"')" "$stderr")
assert_exit 2 "$ec" "target=\"curator\" (bare name) is blocked"

# 数値に見えるが数値でない形も通さない。
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"3x"')" "$stderr")
assert_exit 2 "$ec" "target=\"3x\" is blocked (not a pane id)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '1.5')" "$stderr")
assert_exit 2 "$ec" "non-integer numeric target is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '["3"]')" "$stderr")
assert_exit 2 "$ec" "array target is blocked (fail-closed)"

# pr-watch carve-out を名乗るだけの別名は通さない。
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"pr-watch"')" "$stderr")
assert_exit 2 "$ec" "target=\"pr-watch\" without a PR suffix is blocked"

# --- Allow: 正規経路 ---

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '3')" "$stderr")
assert_exit 0 "$ec" "numeric pane id (3) is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"98"')" "$stderr")
assert_exit 0 "$ec" "numeric pane id as a string (\"98\") is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"%3"')" "$stderr")
assert_exit 0 "$ec" "tmux-style pane id (\"%3\") is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"pr-watch-51"')" "$stderr")
assert_exit 0 "$ec" "DD-2 stale-binding carve-out (\"pr-watch-51\") is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"pr-watch-1011-ja"')" "$stderr")
assert_exit 0 "$ec" "cross-repo watcher name (\"pr-watch-1011-ja\") is allowed"

# --- Passthrough: close_pane 以外 ---

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"mcp__renga-peers__send_keys","tool_input":{"target":"focused","text":"hi"}}' "$stderr")
assert_exit 0 "$ec" "send_keys(target=\"focused\") passes through (Group A, out of scope)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"mcp__renga-peers__set_pane_identity","tool_input":{"target":"focused"}}' "$stderr")
assert_exit 0 "$ec" "set_pane_identity passes through (not in this hook's scope)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"git status"}}' "$stderr")
assert_exit 0 "$ec" "unrelated tool passes through"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
