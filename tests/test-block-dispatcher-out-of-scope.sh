#!/usr/bin/env bash
# Tests for .hooks/block-dispatcher-out-of-scope.sh
# Validates: Edit/Write file_path is constrained to dispatcher business scope
#   (.dispatcher/, .state/, knowledge/raw/YYYY-MM-DD-{topic}.md)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-dispatcher-out-of-scope.sh"

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

CLAUDE_ORG_PATH="$(portable_realpath "$REPO_ROOT")"

PASS=0
FAIL=0

run_hook_edit() {
  local file_path="$1"
  local tool_name="${2:-Write}"
  local exit_code=0
  local json
  json="$(jq -nc --arg t "$tool_name" --arg p "$file_path" \
    '{tool_name:$t, tool_input:{file_path:$p}}')"
  echo "$json" | env CLAUDE_ORG_PATH="$CLAUDE_ORG_PATH" bash "$HOOK" >/dev/null 2>&1 || exit_code=$?
  echo "$exit_code"
}

run_hook_bash() {
  local cmd="$1"
  local exit_code=0
  local json
  json="$(jq -nc --arg c "$cmd" '{tool_name:"Bash", tool_input:{command:$c}}')"
  echo "$json" | env CLAUDE_ORG_PATH="$CLAUDE_ORG_PATH" bash "$HOOK" >/dev/null 2>&1 || exit_code=$?
  echo "$exit_code"
}

assert_eq() {
  local expected="$1" actual="$2" desc="$3"
  if [[ "$expected" == "$actual" ]]; then
    printf "PASS %s\n" "$desc"
    PASS=$((PASS+1))
  else
    printf "FAIL %s (expected exit %s, got %s)\n" "$desc" "$expected" "$actual"
    FAIL=$((FAIL+1))
  fi
}

# ---- Allowed paths ----
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.dispatcher/CLAUDE.md")" \
  "Allow: .dispatcher/CLAUDE.md"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.dispatcher/.claude/skills/custom/SKILL.md")" \
  "Allow: nested file under .dispatcher/"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.state/journal.jsonl")" \
  "Allow: .state/journal.jsonl"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.state/dispatcher/inbox/task1.json")" \
  "Allow: .state/dispatcher/inbox/"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.state/workers/worker-task-1.md")" \
  "Allow: .state/workers/"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.state/dispatcher-event-cursor.txt")" \
  "Allow: .state/dispatcher-event-cursor.txt"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/knowledge/raw/2026-04-27-some-topic.md")" \
  "Allow: knowledge/raw/YYYY-MM-DD-{topic}.md"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.dispatcher/x.md" "Edit")" \
  "Allow: Edit tool name"
assert_eq 0 "$(run_hook_edit "$CLAUDE_ORG_PATH/.dispatcher/notebook.ipynb" "NotebookEdit")" \
  "Allow: NotebookEdit tool name"

# ---- Blocked paths (out-of-scope app code) ----
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/tools/check_role_configs.py")" \
  "Block: tools/check_role_configs.py"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/dashboard/app.py")" \
  "Block: dashboard/"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/tests/test_parsers.py")" \
  "Block: tests/"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/.claude/skills/org-setup/SKILL.md")" \
  "Block: .claude/skills/ (root)"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/docs/non-goals.md")" \
  "Block: docs/"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/registry/projects.md")" \
  "Block: registry/"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/README.md")" \
  "Block: README.md at repo root"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/.curator/CLAUDE.md")" \
  "Block: .curator/ (other role's home)"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/.hooks/block-git-push.sh")" \
  "Block: .hooks/ (security scripts)"

# ---- Path-traversal escape attempts ----
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/.dispatcher/../tools/x.py")" \
  "Block: .dispatcher/../tools/ traversal escapes scope"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/.state/../README.md")" \
  "Block: .state/../README.md traversal escapes scope"

# ---- knowledge/raw/ filename rules ----
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/knowledge/raw/random-note.md")" \
  "Block: knowledge/raw/ wrong filename format"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/knowledge/raw/2026-04-27-Topic-With-Caps.md")" \
  "Block: knowledge/raw/ uppercase in topic"
assert_eq 2 "$(run_hook_edit "$CLAUDE_ORG_PATH/knowledge/curated/note.md")" \
  "Block: knowledge/curated/ (curator territory)"

# ---- Bash tool / unknown tool: hook is Edit-scoped, must pass through ----
assert_eq 0 "$(run_hook_bash 'ls -la')" \
  "Pass-through: Bash tool not subject to this hook"

# ---- env not set ----
not_set_exit=0
echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x"}}' \
  | env -u CLAUDE_ORG_PATH bash "$HOOK" >/dev/null 2>&1 || not_set_exit=$?
assert_eq 2 "$not_set_exit" "Block (fail-closed): CLAUDE_ORG_PATH not set"

TOTAL=$((PASS + FAIL))
echo ""
echo "=== Result: pass=$PASS fail=$FAIL ==="
echo "# $PASS passed, $FAIL failed out of $TOTAL tests"
[[ $FAIL -eq 0 ]] || exit 1
