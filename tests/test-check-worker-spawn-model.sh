#!/usr/bin/env bash
# Tests for .hooks/check-worker-spawn-model.sh
#   allow: worker + sonnet / worker + opus かつ inbox task JSON に "model": "opus" /
#          worker 以外の role / spawn_claude_pane 以外のツール
#   deny:  worker + opus で inbox 無し・inbox に model 無し・inbox が sonnet・inbox 壊れ /
#          model 無し・空・その他 / name から task_id を取れない / CLAUDE_ORG_PATH 未設定 / 壊れた payload
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/../.hooks/check-worker-spawn-model.sh"

PASS=0; FAIL=0; TEST_NUM=0

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export CLAUDE_ORG_PATH="$TMP/org"
INBOX="$CLAUDE_ORG_PATH/.state/dispatcher/inbox"
mkdir -p "$INBOX"
printf '%s' '{"task_id":"t-opus","model":"opus"}' > "$INBOX/t-opus.json"
printf '%s' '{"task_id":"t-none"}' > "$INBOX/t-none.json"
printf '%s' '{"task_id":"t-sonnet","model":"sonnet"}' > "$INBOX/t-sonnet.json"
printf '%s' '{"model":"opus"' > "$INBOX/t-broken.json"

check() {
  local expected="$1" json="$2" desc="$3" ec=0
  printf '%s' "$json" | bash "$HOOK" >/dev/null 2>&1 || ec=$?
  ((TEST_NUM++))
  if [[ "$ec" -eq "$expected" ]]; then
    echo "ok $TEST_NUM - $desc"; ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (expected exit $expected, got $ec)"; ((FAIL++))
  fi
}

spawn() {  # spawn <role> <model-json-or-omit> <name>
  local model=""
  [[ "$2" != "-" ]] && model=",\"model\":$2"
  printf '{"tool_name":"mcp__renga-peers__spawn_claude_pane","tool_input":{"role":"%s","name":"%s"%s}}' "$1" "$3" "$model"
}

check 0 "$(spawn worker '"sonnet"' worker-t-x)" "worker sonnet is allowed"
check 0 "$(spawn worker '"opus"' worker-t-opus)" "worker opus with inbox model opus is allowed"
check 2 "$(spawn worker '"opus"' worker-t-missing)" "worker opus without inbox file is denied"
check 2 "$(spawn worker '"opus"' worker-t-none)" "worker opus with inbox lacking model is denied"
check 2 "$(spawn worker '"opus"' worker-t-sonnet)" "worker opus with inbox model sonnet is denied"
check 2 "$(spawn worker '"opus"' worker-t-broken)" "worker opus with broken inbox JSON is denied"
check 2 "$(spawn worker '"opus"' worker-../t-opus)" "worker opus with path-like name is denied"
check 2 "$(spawn worker '"opus"' '')" "worker opus without name is denied"
check 2 "$(spawn worker - worker-t-x)" "worker without model is denied"
check 2 "$(spawn worker '""' worker-t-x)" "worker with empty model is denied"
check 2 "$(spawn worker '"haiku"' worker-t-x)" "worker with other model is denied"
check 2 "$(spawn worker '"Sonnet"' worker-t-x)" "worker model is case-sensitive"
check 2 "$(spawn worker '["sonnet"]' worker-t-x)" "worker non-string model is denied"
check 0 "$(spawn dispatcher - dispatcher)" "dispatcher role passes through"
check 0 "$(spawn curator '"opus"' curator)" "curator role passes through"
check 0 '{"tool_name":"spawn_claude_pane","tool_input":{"role":"worker","model":"sonnet","name":"worker-a"}}' "bare tool name sonnet is allowed"
check 2 '{"tool_name":"spawn_claude_pane","tool_input":{"role":"worker","name":"worker-a"}}' "bare tool name is checked too"
check 0 '{"tool_name":"mcp__renga-peers__spawn_pane","tool_input":{"role":"worker"}}' "non-spawn_claude_pane tool passes through"
check 0 '{"tool_name":"Bash","tool_input":{"command":"ls"}}' "Bash passes through"
check 2 '' "empty payload is denied"
check 2 'not json' "malformed payload is denied"
check 2 '{"tool_name":"mcp__renga-peers__spawn_claude_pane","tool_input":"x"}' "non-object tool_input is denied"

# CLAUDE_ORG_PATH 未設定では opus を照合できないので deny (sonnet は env 不要)
ec=0; spawn worker '"opus"' worker-t-opus | env -u CLAUDE_ORG_PATH bash "$HOOK" >/dev/null 2>&1 || ec=$?
((TEST_NUM++)); if [[ $ec -eq 2 ]]; then echo "ok $TEST_NUM - opus without CLAUDE_ORG_PATH is denied"; ((PASS++)); else echo "not ok $TEST_NUM - opus without CLAUDE_ORG_PATH is denied (got $ec)"; ((FAIL++)); fi
ec=0; spawn worker '"sonnet"' worker-t-x | env -u CLAUDE_ORG_PATH bash "$HOOK" >/dev/null 2>&1 || ec=$?
((TEST_NUM++)); if [[ $ec -eq 0 ]]; then echo "ok $TEST_NUM - sonnet without CLAUDE_ORG_PATH is allowed"; ((PASS++)); else echo "not ok $TEST_NUM - sonnet without CLAUDE_ORG_PATH is allowed (got $ec)"; ((FAIL++)); fi

echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
