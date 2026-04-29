#!/usr/bin/env bash
# Tests for block-org-structure.sh
# Validates: exit code (0=allow, 2=block) and stderr messages
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-org-structure.sh"

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
  local exit_code=0
  echo "$json" | env WORKER_DIR="$WORKER_DIR" CLAUDE_ORG_PATH="$CLAUDE_ORG_PATH" bash "$HOOK" 2>"$stderr_file" || exit_code=$?
  echo "$exit_code"
}

# ========== Write/Edit Tool Tests ==========

# 1. Normal file in WORKER_DIR (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/src/app.ts"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Write: normal file in WORKER_DIR is allowed"

# 2. WORKER_DIR/.claude/settings.json (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/.claude/settings.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: .claude/ at WORKER_DIR root is blocked"
assert_stderr_contains "組織構造ディレクトリ" "$stderr" "Write: .claude/ stderr mentions 組織構造ディレクトリ"

# 3. WORKER_DIR/.claude/plans/plan.md (exception, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/.claude/plans/plan.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Write: .claude/plans/ exception is allowed"

# 4. WORKER_DIR/subproject/.claude/foo (nested, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/myproj/.claude/config.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Write: nested project .claude/ is allowed"

# 5. WORKER_DIR/.dispatcher/foo (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/.dispatcher/task.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: .dispatcher/ is blocked"
assert_stderr_contains "組織構造ディレクトリ" "$stderr" "Write: .dispatcher/ stderr mentions 組織構造ディレクトリ"

# 5b. WORKER_DIR/.foreman/foo (block — legacy alias kept after #149 rename)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/.foreman/task.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: .foreman/ legacy alias is still blocked"

# 6. WORKER_DIR/.curator/foo (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/.curator/log.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: .curator/ is blocked"
assert_stderr_contains "組織構造ディレクトリ" "$stderr" "Write: .curator/ stderr mentions 組織構造ディレクトリ"

# 7. WORKER_DIR/.state/foo (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/.state/state.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: .state/ is blocked"
assert_stderr_contains "組織構造ディレクトリ" "$stderr" "Write: .state/ stderr mentions 組織構造ディレクトリ"

# 8. WORKER_DIR/registry/foo (root-only, block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/registry/workers.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: registry/ at root is blocked"
assert_stderr_contains "組織構造ディレクトリ" "$stderr" "Write: registry/ stderr mentions 組織構造ディレクトリ"

# 9. WORKER_DIR/dashboard/foo (root-only, block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/dashboard/index.html"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: dashboard/ at root is blocked"
assert_stderr_contains "組織構造ディレクトリ" "$stderr" "Write: dashboard/ stderr mentions 組織構造ディレクトリ"

# 10. WORKER_DIR/knowledge/foo (root-only, block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/knowledge/file.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Write: knowledge/ at root is blocked"
assert_stderr_contains "組織構造ディレクトリ" "$stderr" "Write: knowledge/ stderr mentions 組織構造ディレクトリ"

# 11. knowledge/raw with valid name (exception, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$CLAUDE_ORG_PATH"'/knowledge/raw/2026-04-11-test.md"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Write: knowledge/raw exception is allowed"

# 12. WORKER_DIR/sub/registry/foo (not root, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/myproj/registry/data.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Write: registry/ in subdir is allowed (root-only rule)"

# 13. Empty file_path in JSON (allow - hook exits 0)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Write: empty file_path exits 0"

# 14. Missing env vars (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Write","tool_input":{"file_path":"'"$WORKER_DIR"'/test.txt"}}'
exit_code=0
echo "$json" | env -u WORKER_DIR -u CLAUDE_ORG_PATH bash "$HOOK" 2>"$stderr" || exit_code=$?
assert_exit 2 "$exit_code" "Write: missing env vars is blocked"
assert_stderr_contains "WORKER_DIR" "$stderr" "Write: missing env stderr mentions WORKER_DIR"

# ========== Bash Tool Tests ==========

# 15. mkdir .claude (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"mkdir -p .claude"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: mkdir .claude is blocked"
assert_stderr_contains ".claude" "$stderr" "Bash: mkdir .claude stderr mentions .claude"

# 16. mkdir -p .dispatcher (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"mkdir -p .dispatcher"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: mkdir -p .dispatcher is blocked"
assert_stderr_contains ".dispatcher" "$stderr" "Bash: mkdir -p .dispatcher stderr mentions .dispatcher"

# 17. touch ./registry/foo (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"touch ./registry/workers.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: touch ./registry/ is blocked"
assert_stderr_contains "registry" "$stderr" "Bash: touch ./registry stderr mentions registry"

# 18. cp to .state (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"cp foo.json .state/backup.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: cp to .state is blocked"
assert_stderr_contains ".state" "$stderr" "Bash: cp .state stderr mentions .state"

# 19. mv to dashboard (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"mv report.html dashboard/index.html"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: mv to dashboard is blocked"
assert_stderr_contains "dashboard" "$stderr" "Bash: mv dashboard stderr mentions dashboard"

# 20. mkdir knowledge/raw/ (exception, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"mkdir -p knowledge/raw/"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: mkdir knowledge/raw/ exception is allowed"

# 21. mkdir .claude/plans (exception, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"mkdir -p .claude/plans"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: mkdir .claude/plans exception is allowed"

# 22. Normal command (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"ls -la src/"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: normal command is allowed"

# ========== Bash Tool Tests: Quoted Paths ==========

# 23. mkdir -p "./.claude" (quoted, block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"mkdir -p \"./.claude\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: mkdir quoted .claude is blocked"
assert_stderr_contains ".claude" "$stderr" "Bash: mkdir quoted .claude stderr mentions .claude"

# 24. mkdir -p "./.claude/plans" (quoted exception, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"mkdir -p \"./.claude/plans\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: mkdir quoted .claude/plans exception is allowed"

# 25. touch quoted Windows .dispatcher path (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"touch \"C:\\Users\\test\\.dispatcher\\test\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: touch quoted Windows .dispatcher is blocked"
assert_stderr_contains ".dispatcher" "$stderr" "Bash: touch quoted Windows .dispatcher stderr mentions .dispatcher"

# 26. touch quoted Windows knowledge/raw/ path (exception, allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"touch \"C:\\Users\\test\\knowledge\\raw\\2026-04-13-test.md\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: touch quoted Windows knowledge/raw/ exception is allowed"

# ========== Other Tool Tests ==========

# 27. Non-Edit/Write/Bash tool_name (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Read","tool_input":{"file_path":"'"$WORKER_DIR"'/.claude/settings.json"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Other tool (Read): passes through"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
