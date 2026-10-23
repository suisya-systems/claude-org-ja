#!/usr/bin/env bash
# Tests for .hooks/block-close-before-retro.sh
# 振り返りの完了報告ゲートを通っていない worker ペインへの close_pane を deny するか。
#
# 確認観点:
#   deny 側:
#     - verify 済み worker、ack なし、runs.status=in_use          -> block
#     - "%5" 表記でも同じ                                         -> block
#     - ack が今回の worker_spawn_verified より前 (前回の派遣)    -> block
#     - completion_reported_at あり、status=in_use (stale marker) -> block
#     - status=review、completion_reported_at なし                -> block
#     - state.db が壊れている                                     -> block (fail-closed)
#   allow 側:
#     - retro_gate_acked が verify 以降にある                     -> allow
#     - runs.status=completed                                     -> allow (pane-close.md 0. の 1 つ目)
#     - runs.status=failed / abandoned                            -> allow (run が閉じている)
#     - retro_gate_confirmed_missing が verify 以降にある         -> allow (replied_no_ack で未着確定)
#       (verify より前のものは block)
#     - completion_reported_at あり かつ status=review            -> allow (pane-close.md 0. の 2 つ目)
#     - verify 記録の無い pane (curator / pr-watch 等)            -> allow
#     - close_pane 以外 / 相対セレクタ (別フックの担当)           -> passthrough
#     - state.db が無い                                           -> allow
#   end-to-end: dispatcher_retro_gate.py の acked / journal_append.sh の未着確定が記録した行で
#   close が通る。
#   壊れた payload は tests/test-hooks-payload-fail-closed.sh が横断で固定する。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-close-before-retro.sh"

PASS=0; FAIL=0; TEST_NUM=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

assert_exit() {
  local expected="$1" actual="$2" desc="$3"
  ((TEST_NUM++))
  if [[ "$actual" -eq "$expected" ]]; then
    echo "ok $TEST_NUM - $desc"; ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (expected exit $expected, got $actual)"; ((FAIL++))
  fi
}

# new_state <dir>: 空の state.db を作る (本番と同じ schema)
new_state() {
  mkdir -p "$1/dispatcher"
  python3 - "$1/state.db" "$REPO_ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from tools.state_db import apply_schema, connect
conn = connect(sys.argv[1]); apply_schema(conn)
conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (1, 'p', 'p')")
conn.commit(); conn.close()
PY
}

# sql <dir> <statement>
sql() { python3 -c 'import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute(sys.argv[2]);c.commit()' "$1/state.db" "$2"; }

run_row()   { sql "$1" "INSERT INTO runs (task_id, project_id, pattern, title, status) VALUES ('$2', 1, 'B', 't', '$3')"; }
verified()  { sql "$1" "INSERT INTO events (kind, payload_json) VALUES ('worker_spawn_verified', '{\"task\":\"$2\",\"pane_id\":$3}')"; }
acked()     { sql "$1" "INSERT INTO events (kind, payload_json) VALUES ('retro_gate_acked', '{\"task\":\"$2\"}')"; }
missing()   { sql "$1" "INSERT INTO events (kind, payload_json) VALUES ('retro_gate_confirmed_missing', '{\"task\":\"$2\"}')"; }
reported()  { printf '{"worker-%s":{"completion_reported_at":"2026-10-12T00:00:00Z"}}' "$2" >"$1/dispatcher/worker-idle-state.json"; }

# close <dir> <target-json> [tool] -> exit code
close() {
  local ec=0
  printf '{"tool_name":"%s","tool_input":{"target":%s}}' "${3:-mcp__renga-peers__close_pane}" "$2" \
    | STATE_DB_PATH="$1/state.db" bash "$HOOK" >/dev/null 2>"$WORK/stderr" || ec=$?
  echo "$ec"
}

S="$WORK/s1"; new_state "$S"; run_row "$S" t1 in_use; verified "$S" t1 5
assert_exit 2 "$(close "$S" 5)" "verified worker without ack (in_use) is blocked"
grep -qF "完了報告ゲート" "$WORK/stderr" && assert_exit 0 0 "deny message points to the retro gate" \
  || assert_exit 0 1 "deny message points to the retro gate"
assert_exit 2 "$(close "$S" '"%5"' mcp__org-broker__close_pane)" "\"%5\" form on broker is blocked the same"
assert_exit 0 "$(close "$S" 6)" "pane without worker_spawn_verified (curator / pr-watch) is allowed"
assert_exit 0 "$(close "$S" '"focused"')" "relative selector is left to block-relative-close-pane.sh"
assert_exit 0 "$(close "$S" 5 mcp__renga-peers__send_keys)" "non-close tool passes through"

acked "$S" t1
assert_exit 0 "$(close "$S" 5)" "retro_gate_acked after verify is allowed"

S="$WORK/s2"; new_state "$S"; run_row "$S" t2 in_use; acked "$S" t2; verified "$S" t2 7
assert_exit 2 "$(close "$S" 7)" "ack older than the current verify (previous dispatch) is blocked"

S="$WORK/s3"; new_state "$S"; run_row "$S" t3 completed; verified "$S" t3 8
assert_exit 0 "$(close "$S" 8)" "runs.status=completed is allowed without ack"

S="$WORK/s4"; new_state "$S"; run_row "$S" t4 review; verified "$S" t4 9
assert_exit 2 "$(close "$S" 9)" "status=review without completion_reported_at is blocked"
reported "$S" t4
assert_exit 0 "$(close "$S" 9)" "completion_reported_at with status=review is allowed"

S="$WORK/s5"; new_state "$S"; run_row "$S" t5 in_use; verified "$S" t5 10; reported "$S" t5
assert_exit 2 "$(close "$S" 10)" "completion_reported_at with status=in_use (stale marker) is blocked"

for st in failed abandoned; do
  S="$WORK/s-$st"; new_state "$S"; run_row "$S" "t-$st" "$st"; verified "$S" "t-$st" 12
  assert_exit 0 "$(close "$S" 12)" "runs.status=$st is allowed without ack"
done

S="$WORK/s8"; new_state "$S"; run_row "$S" t8 in_use; missing "$S" t8; verified "$S" t8 13
assert_exit 2 "$(close "$S" 13)" "confirmed_missing older than the current verify is blocked"
missing "$S" t8
assert_exit 0 "$(close "$S" 13)" "retro_gate_confirmed_missing after verify is allowed"

assert_exit 0 "$(close "$WORK/missing" 5)" "missing state.db is allowed"

S="$WORK/s6"; mkdir -p "$S"; printf 'not a database' >"$S/state.db"
assert_exit 2 "$(close "$S" 5)" "unreadable state.db is blocked (fail-closed)"

# end-to-end: the gate CLI's own record opens the close.
# broker の native handle は文字列のまま記帳される。INTEGER CAST で 0 に潰すと
# 照合が外れて ack 無しで閉じられる (設計レビューの Blocker)。
S="$WORK/s10"; new_state "$S"; run_row "$S" t10 in_use; verified "$S" t10 '"w21:p2"'
assert_exit 2 "$(close "$S" '"w21:p2"' mcp__org-broker__close_pane)" "herdr handle without ack is blocked"
assert_exit 0 "$(close "$S" '"w21:p3"' mcp__org-broker__close_pane)" "a different herdr handle is not matched"
acked "$S" t10
assert_exit 0 "$(close "$S" '"w21:p2"' mcp__org-broker__close_pane)" "herdr handle after ack is allowed"

S="$WORK/s11"; new_state "$S"; run_row "$S" t11 in_use; verified "$S" t11 '"%4"'
assert_exit 2 "$(close "$S" '"%4"' mcp__org-broker__close_pane)" "tmux handle stored as \"%4\" without ack is blocked"
assert_exit 2 "$(close "$S" 4 mcp__org-broker__close_pane)" "numeric 4 also matches a stored \"%4\" (deny side)"
assert_exit 0 "$(close "$S" 0 mcp__org-broker__close_pane)" "pane 0 does not match a stored handle"

S="$WORK/s7"; new_state "$S"; run_row "$S" t7 in_use; verified "$S" t7 11
assert_exit 2 "$(close "$S" 11)" "e2e: blocked before the retro gate acks"
printf '{"messages":[{"from_name":"secretary","message":"届いています"}]}' \
  | STATE_DB_PATH="$S/state.db" python3 "$REPO_ROOT/tools/dispatcher_retro_gate.py" --task-id t7 >/dev/null
assert_exit 0 "$(close "$S" 11)" "e2e: allowed after dispatcher_retro_gate.py acked"

# end-to-end: pane-close.md の exit 3 分岐の 1 行で close が通る。
S="$WORK/s9"; new_state "$S"; run_row "$S" t9 in_use; verified "$S" t9 14
assert_exit 2 "$(close "$S" 14)" "e2e: blocked before the missing report is confirmed"
STATE_DB_PATH="$S/state.db" bash "$REPO_ROOT/tools/journal_append.sh" retro_gate_confirmed_missing task=t9 >/dev/null
assert_exit 0 "$(close "$S" 14)" "e2e: allowed after journal_append.sh recorded confirmed_missing"

echo "# block-close-before-retro: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
