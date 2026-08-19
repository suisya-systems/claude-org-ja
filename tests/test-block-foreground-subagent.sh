#!/usr/bin/env bash
# Tests for block-foreground-subagent.sh
# Validates: exit code (0=allow/passthrough, 2=block) and stderr messages.
#
# 確認観点 (Issue #942 でハーネス仕様変更に追随):
#   - Agent + run_in_background 欠落              -> allow (現行ハーネス = 常時背景)
#   - Task  + run_in_background 欠落              -> block (旧仕様の既定前景を維持)
#   - Agent/Task + run_in_background=true         -> allow (旧ハーネス互換)
#   - Agent/Task + false / 非 boolean true        -> block (exit 2, 前景ないし不明指定)
#   - Agent + tool_input 欠落                     -> block (fail-closed)
#   - top-level run_in_background (tool_input 外) -> 無視 (.tool_input.* のみ参照)
#   - legacy Task の前景 / 背景                    -> block / allow
#   - 非 subagent ツール / 近接 tool_name          -> passthrough (exact match)
#   - 不正 JSON / 空 stdin / 非 object payload     -> block (fail-closed)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-foreground-subagent.sh"

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

# --- Allow Cases ---

# 1. Agent + run_in_background=true (boolean) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","tool_input":{"description":"x","prompt":"y","run_in_background":true}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Agent run_in_background=true is allowed"

# 2. legacy Task + run_in_background=true -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Task","tool_input":{"description":"x","prompt":"y","run_in_background":true}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Task run_in_background=true is allowed"

# --- Block Cases (foreground subagent) ---

# 3. Agent + run_in_background=false -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","tool_input":{"description":"x","prompt":"y","run_in_background":false}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Agent run_in_background=false is blocked"
assert_stderr_contains "run_in_background に true 以外が指定されています" "$stderr" \
  "deny stderr names the offending run_in_background value"

# 4. Agent + run_in_background omitted -> allow (Issue #942)
#    現行ハーネスは Agent の入力スキーマから run_in_background を廃止し subagent を
#    常時背景実行にした。2026-08-19 の実測 payload は
#      {"description":...,"prompt":...,"subagent_type":...}
#    でキー自体が無く、明示指定しても additionalProperties:false で除去される。
#    旧判定 (`== true` を要求) はこの形を deny してしまい、全 subagent が
#    誤ブロックされていた。これが本テストの回帰対象。
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","tool_input":{"description":"x","prompt":"y","subagent_type":"general-purpose"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Agent with no run_in_background key is allowed (harness always-background)"

# 5. Agent + run_in_background="true" (string, not strict boolean) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","tool_input":{"description":"x","prompt":"y","run_in_background":"true"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Agent run_in_background string \"true\" is blocked"

# 6. Agent + run_in_background=1 (numeric) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","tool_input":{"description":"x","prompt":"y","run_in_background":1}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Agent run_in_background numeric 1 is blocked"

# 7. Agent + run_in_background=null -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","tool_input":{"description":"x","prompt":"y","run_in_background":null}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Agent run_in_background null is blocked"

# 8. Agent + tool_input missing entirely -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent"}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Agent with no tool_input is blocked"
assert_stderr_contains "object 形式の tool_input がありませんでした" "$stderr" \
  "missing tool_input is denied by the subagent payload guard (not the bg check)"

# 9. run_in_background at TOP level (outside tool_input) is IGNORED.
#    The hook reads .tool_input.run_in_background only. Scoping is asserted from
#    both sides so the test still fails if the lookup ever widens to top-level:
#    (a) a top-level false must NOT cause a block, and
#    (b) a tool_input false must block even when top-level says true.
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","run_in_background":false,"tool_input":{"description":"x","prompt":"y"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "top-level run_in_background=false is ignored (tool_input scope only)"

# 9b. tool_input の false は top-level の true に優先して block される
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","run_in_background":true,"tool_input":{"description":"x","run_in_background":false}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "tool_input run_in_background=false blocks despite top-level true"

# 10. legacy Task + run_in_background omitted -> block。
#     test 4 (Agent) と対になる契約。「キー欠落」は旧仕様では『既定前景』、現行仕様では
#     『常時背景』という真逆の意味で、payload だけでは判別できない。現行ハーネスは
#     Task を emit しないので、Task という名前が届いた時点で run_in_background が
#     スキーマに存在した世代とみなし、欠落を前景として deny し続ける (旧世代の防護維持)。
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Task","tool_input":{"description":"x","prompt":"y"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Task with no run_in_background key is blocked (legacy default-foreground)"
assert_stderr_contains "legacy Task ツールでは run_in_background の欠落が既定の前景実行を意味する" "$stderr" \
  "Task omission is denied by the legacy-semantics branch"

# 10b. legacy Task + run_in_background=false -> block (明示前景は従来どおり拒否)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Task","tool_input":{"description":"x","run_in_background":false}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Task run_in_background=false is blocked"

# --- Passthrough Cases (non-subagent / exact-match semantics) ---

# 11. Bash tool -> passthrough
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"ls"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash tool passes through"

# 12. Bash tool with run_in_background=false -> passthrough (only subagent tools gated)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"ls","run_in_background":false}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash tool with run_in_background=false passes through"

# 13. Edit tool -> passthrough
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Edit","tool_input":{"file_path":"/x","old_string":"a","new_string":"b"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Edit tool passes through"

# 14. tool_name missing -> passthrough
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_input":{"foo":"bar"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "missing tool_name passes through"

# 15. AgentFoo (substring, not exact) -> passthrough (exact-match only)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"AgentFoo","tool_input":{}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "AgentFoo (substring) passes through (exact match)"

# 16. lowercase agent -> passthrough (exact-match is case-sensitive)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"agent","tool_input":{}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "lowercase agent passes through (case-sensitive exact match)"

# --- Fail-closed Cases (malformed input) ---

# 17. Malformed JSON -> block (fail-closed, NOT fail-open exit 5)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook 'not json{' "$stderr")
assert_exit 2 "$ec" "malformed JSON is blocked (fail-closed)"

# 18. Truncated JSON -> block (fail-closed)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Agent","tool_input":{' "$stderr")
assert_exit 2 "$ec" "truncated JSON is blocked (fail-closed)"

# 19. Empty stdin -> block (fail-closed)
#     jq は「JSON 値がゼロ個」の入力を parse error にせず exit 0 で返すため、
#     `type == "object"` の型ガードでは捕まらない。型ガードより前の明示的な
#     空判定が deny 理由になっていることを stderr でも確認する (Issue #834)。
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '' "$stderr")
assert_exit 2 "$ec" "empty stdin is blocked (fail-closed)"
assert_stderr_contains "PreToolUse payload が空でした" "$stderr" \
  "empty stdin is denied by the empty-payload guard (not the type guard)"

# 19b. Whitespace-only stdin -> block (fail-closed)
#      改行だけの入力も jq から見れば同じ「値ゼロ個」なので同じ穴になる。
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook $'  \n\t \n' "$stderr")
assert_exit 2 "$ec" "whitespace-only stdin is blocked (fail-closed)"
assert_stderr_contains "PreToolUse payload が空でした" "$stderr" \
  "whitespace-only stdin is denied by the empty-payload guard"

# 20. tool_input is a string (JSON-valid but not an object) -> block
#     Without a type guard, .tool_input.run_in_background would make jq
#     error (exit 5) and fail OPEN. Must deny (exit 2) instead.
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Agent","tool_input":"not-object"}' "$stderr")
assert_exit 2 "$ec" "Agent with non-object (string) tool_input is blocked"

# 21. tool_input is an array (JSON-valid but not an object) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Agent","tool_input":[1,2,3]}' "$stderr")
assert_exit 2 "$ec" "Agent with array tool_input is blocked"

# 22. tool_input is a boolean -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Agent","tool_input":true}' "$stderr")
assert_exit 2 "$ec" "Agent with boolean tool_input is blocked"

# 22b. 非 subagent ツールでも tool_input が非 object なら block (Issue #834)
#      以前は tool_name の exact match で先に passthrough していたため、壊れた
#      payload が Bash 等の名前を持つだけで exit 0 に落ちていた。tool_name を見る
#      前に payload の形を確定させる方針に変更したので、ここは deny になる。
#      (正常な object tool_input の Bash / Edit は test 11-13 のとおり passthrough)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Bash","tool_input":[1,2,3]}' "$stderr")
assert_exit 2 "$ec" "non-subagent tool with array tool_input is blocked (malformed payload)"
assert_stderr_contains "JSON object として解析できませんでした" "$stderr" \
  "malformed tool_input is denied by the payload guard"

# 23. Top-level JSON is an array (valid JSON, not an object) -> block (fail-closed)
#     Without a top-level type guard, .tool_name indexing would jq-error (exit 5)
#     and fail OPEN. Must deny (exit 2).
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '[]' "$stderr")
assert_exit 2 "$ec" "top-level array payload is blocked (fail-closed)"

# 24. Top-level JSON is a string -> block (fail-closed)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '"str"' "$stderr")
assert_exit 2 "$ec" "top-level string payload is blocked (fail-closed)"

# 25. Top-level JSON is a boolean -> block (fail-closed)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook 'true' "$stderr")
assert_exit 2 "$ec" "top-level boolean payload is blocked (fail-closed)"

# 26. Top-level JSON is a number -> block (fail-closed)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '1' "$stderr")
assert_exit 2 "$ec" "top-level number payload is blocked (fail-closed)"

# 27. Top-level JSON is null -> block (fail-closed)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook 'null' "$stderr")
assert_exit 2 "$ec" "top-level null payload is blocked (fail-closed)"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
