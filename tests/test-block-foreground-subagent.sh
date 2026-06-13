#!/usr/bin/env bash
# Tests for block-foreground-subagent.sh
# Validates: exit code (0=allow/passthrough, 2=block) and stderr messages.
#
# 確認観点:
#   - Agent + run_in_background=true (boolean)   -> allow (exit 0)
#   - Agent + false / 欠落 / 非 boolean true      -> block (exit 2, 前景扱い)
#   - Agent + tool_input 欠落                     -> block
#   - top-level run_in_background (tool_input 外) -> block (.tool_input.* のみ参照)
#   - legacy Task の前景 / 背景                    -> block / allow
#   - 非 subagent ツール / 近接 tool_name          -> passthrough (exact match)
#   - 不正 JSON / 空 stdin                         -> block (fail-closed)
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
assert_stderr_contains "run_in_background=true" "$stderr" "deny stderr guides run_in_background=true"

# 4. Agent + run_in_background omitted (default foreground) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","tool_input":{"description":"x","prompt":"y"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Agent run_in_background omitted is blocked"

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

# 9. run_in_background at TOP level (outside tool_input) -> block
#    The hook reads .tool_input.run_in_background only; a top-level key must
#    NOT be treated as background (guards against an over-broad fallback).
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Agent","run_in_background":true,"tool_input":{}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "top-level run_in_background (outside tool_input) is blocked"

# 10. legacy Task + run_in_background omitted (foreground) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Task","tool_input":{"description":"x","prompt":"y"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Task foreground is blocked"

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

# 19. Empty stdin -> block (fail-closed; not valid JSON)
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '' "$stderr")
assert_exit 2 "$ec" "empty stdin is blocked (fail-closed)"

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
