#!/usr/bin/env bash
# Tests for check-worker-boundary.sh
# Validates: exit code (0=allow, 2=block) and stderr messages
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/check-worker-boundary.sh"

# Portable realpath -m (matches hook fallback: GNU realpath → python3 → python)
portable_realpath() {
  local target="$1"
  if result=$(command realpath -m "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  else
    echo "FATAL: realpath -m も python も利用できません" >&2
    exit 1
  fi
}

# Compute canonical paths (same method as hooks)
WORKER_DIR="$(portable_realpath "$REPO_ROOT")"
CLAUDE_ORG_PATH="$(portable_realpath "$REPO_ROOT/../..")"

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
  shift 2
  local exit_code=0
  echo "$json" | env WORKER_DIR="$WORKER_DIR" CLAUDE_ORG_PATH="$CLAUDE_ORG_PATH" "$@" bash "$HOOK" 2>"$stderr_file" || exit_code=$?
  echo "$exit_code"
}

run_hook_no_env() {
  local json="$1" stderr_file="$2"
  local exit_code=0
  echo "$json" | env -u WORKER_DIR -u CLAUDE_ORG_PATH bash "$HOOK" 2>"$stderr_file" || exit_code=$?
  echo "$exit_code"
}

# --- Test Cases ---

# 1. File inside WORKER_DIR (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/src/main.ts"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "file inside WORKER_DIR is allowed"

# 2. File in nested subdir (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Edit","tool_input":{"file_path":"'"$WORKER_DIR"'/a/b/c/file.txt"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "file in nested subdir is allowed"

# 3. Valid knowledge/raw filename (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$CLAUDE_ORG_PATH"'/knowledge/raw/2026-04-11-hook-testing.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "valid knowledge/raw filename is allowed"

# 4. File outside WORKER_DIR (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"/tmp/evil.sh"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "file outside WORKER_DIR is blocked"
assert_stderr_contains "許可パス外" "$stderr" "stderr mentions 許可パス外"

# 5. Parent traversal (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/../secret.txt"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "parent traversal is blocked"
assert_stderr_contains "許可パス外" "$stderr" "parent traversal stderr mentions 許可パス外"

# 6. Knowledge file with uppercase (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$CLAUDE_ORG_PATH"'/knowledge/raw/2026-04-11-BadName.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "knowledge file with uppercase is blocked"
assert_stderr_contains "ファイル名が不正" "$stderr" "uppercase filename stderr mentions ファイル名が不正"

# 7. Knowledge file without date (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$CLAUDE_ORG_PATH"'/knowledge/raw/notes.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "knowledge file without date is blocked"
assert_stderr_contains "ファイル名が不正" "$stderr" "no-date filename stderr mentions ファイル名が不正"

# 8. Knowledge file with underscores (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$CLAUDE_ORG_PATH"'/knowledge/raw/2026-04-11-bad_name.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "knowledge file with underscores is blocked"
assert_stderr_contains "ファイル名が不正" "$stderr" "underscore filename stderr mentions ファイル名が不正"

# 9. Missing file_path in JSON (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "missing file_path is blocked"
assert_stderr_contains "file_path" "$stderr" "missing file_path stderr mentions file_path"

# 10. Missing WORKER_DIR env var (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"/tmp/test.txt"}}'
ec=$(run_hook_no_env "$json" "$stderr")
assert_exit 2 "$ec" "missing WORKER_DIR env is blocked"
assert_stderr_contains "WORKER_DIR" "$stderr" "missing env stderr mentions WORKER_DIR"

# 11. Missing only CLAUDE_ORG_PATH env var (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"/tmp/test.txt"}}'
exit_code=0
echo "$json" | env WORKER_DIR="$WORKER_DIR" CLAUDE_ORG_PATH="" bash "$HOOK" 2>"$stderr" || exit_code=$?
assert_exit 2 "$exit_code" "missing CLAUDE_ORG_PATH env is blocked"
assert_stderr_contains "CLAUDE_ORG_PATH" "$stderr" "missing CLAUDE_ORG_PATH stderr mentions CLAUDE_ORG_PATH"

# 12. knowledge/curated/ (not raw) is blocked
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$CLAUDE_ORG_PATH"'/knowledge/curated/foo.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "knowledge/curated/ is blocked"
assert_stderr_contains "許可パス外" "$stderr" "knowledge/curated stderr mentions 許可パス外"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
