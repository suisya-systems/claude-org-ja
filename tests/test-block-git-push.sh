#!/usr/bin/env bash
# Tests for block-git-push.sh
# Validates: exit code (0=allow, 2=block) and stderr messages
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-git-push.sh"

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
  echo "$json" | bash "$HOOK" 2>"$stderr_file" || exit_code=$?
  echo "$exit_code"
}

# --- Block Cases ---

# 1. Simple git push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git push is blocked"
assert_stderr_contains "git push" "$stderr" "git push stderr mentions git push"

# 2. git push with remote and branch (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git push origin main is blocked"
assert_stderr_contains "git push" "$stderr" "git push origin main stderr mentions git push"

# 3. git push with extra spaces (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git  push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git with extra spaces push is blocked"

# 4. Piped git push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"echo foo | git push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "piped git push is blocked"

# 5. Chained git push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git add . && git push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "chained git push is blocked"

# --- Allow Cases ---

# 6. git commit (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git commit -m \"test\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git commit is allowed"

# 7. git pull (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git pull"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git pull is allowed"

# 8. git status (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git status"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git status is allowed"

# 9. git log (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git log --oneline"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git log is allowed"

# 10. Empty command (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "empty command is allowed"

# 11. Non-git command (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"ls -la"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "non-git command is allowed"

# 12. git -C /path push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git -C /some/path push origin main"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git -C /path push is blocked"
assert_stderr_contains "git push" "$stderr" "git -C /path push stderr mentions git push"

# 13. git --git-dir=/tmp/repo push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git --git-dir=/tmp/repo push origin main"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git --git-dir=... push is blocked"

# 14. git config push.default (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git config push.default simple"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git config push.default is allowed"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
