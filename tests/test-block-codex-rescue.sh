#!/usr/bin/env bash
# Tests for .hooks/block-codex-rescue.sh
#   deny: Skill(skill="codex:rescue") / 先頭 "/" 付き
#   allow: 他の skill / Skill 以外のツール
#   壊れた payload は tests/test-hooks-payload-fail-closed.sh が横断で見る。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/../.hooks/block-codex-rescue.sh"

PASS=0; FAIL=0; TEST_NUM=0

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

check 2 '{"tool_name":"Skill","tool_input":{"skill":"codex:rescue"}}' "Skill codex:rescue is blocked"
check 2 '{"tool_name":"Skill","tool_input":{"skill":"/codex:rescue","args":"x"}}' "Skill /codex:rescue is blocked"
check 0 '{"tool_name":"Skill","tool_input":{"skill":"org-delegate"}}' "other skill is allowed"
check 0 '{"tool_name":"Skill","tool_input":{"skill":"codex:review"}}' "other codex skill is allowed"
check 0 '{"tool_name":"Bash","tool_input":{"command":"codex exec review"}}' "non-Skill tool passes through"

echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
