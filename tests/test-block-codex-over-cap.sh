#!/usr/bin/env bash
# Tests for .hooks/block-codex-over-cap.sh (+ tools/codex_round_cap.py)
#   - 正規のゲート呼び出しは許可回数 (既定 3) まで通り、4 回目は CODEX_ROUND_DENIED で拒否され
#     events に codex_round_denied が残る
#   - 窓口の `codex_round_cap.py set` で許可回数を上げる / unlimited にすると通る
#   - codex の直接実行・正規形でないゲート呼び出しは拒否、codex を含むだけの無関係なコマンドは通す
#   - DB 欠落 / task 特定不能は拒否 (fail-closed)、STATE_DB_PATH は無視する
#   壊れた payload は tests/test-hooks-payload-fail-closed.sh が横断で見る。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-codex-over-cap.sh"
CAP="$REPO_ROOT/tools/codex_round_cap.py"
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

PASS=0; FAIL=0; TEST_NUM=0

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

ORG="$TMP/org"
WORKER="$TMP/workers/w1"
mkdir -p "$ORG/.state" "$ORG/tools" "$WORKER"
ln -s "$REPO_ROOT/tools/codex_review_gate.sh" "$ORG/tools/codex_review_gate.sh"
CANON="$ORG/tools/codex_review_gate.sh"

make_db() {  # make_db <db_path> <worker_dir> <task>
  PYTHONPATH="$REPO_ROOT" "$PY" - "$@" <<'PYEOF'
import sys
from tools.state_db import apply_schema, connect
from tools.state_db.writer import StateWriter
db, wdir, task = sys.argv[1:4]
conn = connect(db)
apply_schema(conn)
w = StateWriter(conn)
w.append_event(kind="delegate_sent", actor="secretary", payload={"task": task, "dir": wdir})
w.commit()
conn.close()
PYEOF
}
make_db "$ORG/.state/state.db" "$WORKER/" t1

count_kind() {  # count_kind <kind>
  PYTHONPATH="$REPO_ROOT" "$PY" - "$ORG/.state/state.db" "$1" <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM events WHERE kind = ?", (sys.argv[2],)).fetchone()[0])
PYEOF
}

LAST_ERR="$TMP/last_stderr"
run() {  # run <command> [env overrides...] -> exit code
  local cmd="$1"; shift
  local json ec=0
  json=$(jq -cn --arg c "$cmd" '{tool_name:"Bash",tool_input:{command:$c}}')
  printf '%s' "$json" | env WORKER_DIR="$WORKER" CLAUDE_ORG_PATH="$ORG" "$@" bash "$HOOK" >/dev/null 2>"$LAST_ERR" || ec=$?
  echo "$ec"
}

check() {  # check <expected-exit> <desc> <command> [env...]
  local expected="$1" desc="$2"; shift 2
  local ec
  ec=$(run "$@")
  ((TEST_NUM++))
  if [[ "$ec" -eq "$expected" ]]; then
    echo "ok $TEST_NUM - $desc"; ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (expected exit $expected, got $ec: $(head -c 300 "$LAST_ERR"))"; ((FAIL++))
  fi
}

assert_eq() {
  ((TEST_NUM++))
  if [[ "$1" == "$2" ]]; then
    echo "ok $TEST_NUM - $3"; ((PASS++))
  else
    echo "not ok $TEST_NUM - $3 (expected '$2', got '$1')"; ((FAIL++))
  fi
}

assert_err_contains() {
  ((TEST_NUM++))
  if grep -qF "$1" "$LAST_ERR"; then
    echo "ok $TEST_NUM - $2"; ((PASS++))
  else
    echo "not ok $TEST_NUM - $2 (stderr: $(head -c 300 "$LAST_ERR"))"; ((FAIL++))
  fi
}

GATE="bash $CANON origin/main"
GATE_VAR='bash "$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" origin/main'

# --- 往復上限 (既定 3) ---
check 0 "round 1 (canonical gate) allowed" "$GATE"
check 0 "round 2 (\$CLAUDE_ORG_PATH form) allowed" "$GATE_VAR"
check 0 "round 3 allowed" "$GATE"
check 2 "round 4 denied at default cap 3" "$GATE"
assert_err_contains "CODEX_ROUND_DENIED" "round 4 stderr carries CODEX_ROUND_DENIED"
assert_eq "$(count_kind codex_round_started)" "3" "3 codex_round_started rows"
assert_eq "$(count_kind codex_round_denied)" "1" "1 codex_round_denied row"

# --- 窓口による許可回数の変更 ---
"$PY" "$CAP" set --task-id t1 --allowed 4 --reason test --db-path "$ORG/.state/state.db" >/dev/null
check 0 "round 4 allowed after set --allowed 4" "$GATE"
check 2 "round 5 denied at allowed 4" "$GATE"
"$PY" "$CAP" set --task-id t1 --allowed unlimited --reason test --db-path "$ORG/.state/state.db" >/dev/null
check 0 "unlimited allows round 5" "$GATE"
check 0 "unlimited allows round 6" "$GATE"
assert_eq "$(count_kind codex_round_cap_changed)" "2" "2 codex_round_cap_changed rows"
STATUS=$("$PY" "$CAP" status --task-id t1 --db-path "$ORG/.state/state.db")
assert_eq "$STATUS" '{"task": "t1", "used": 6, "allowed": "unlimited", "denied": 2}' "status reports used/allowed/denied"
"$PY" "$CAP" set --task-id t1 --allowed 0 --reason test --db-path "$ORG/.state/state.db" >/dev/null

# --- codex の直接実行 ---
check 2 "codex exec review denied" "codex exec review --base origin/main"
assert_err_contains "codex の直接実行は禁止" "direct codex stderr names the gate"
check 2 "codex review denied" "codex review"
check 2 "/usr/bin/codex exec denied" "/usr/bin/codex exec"
check 2 "env X=1 codex exec denied" "env X=1 codex exec"
check 2 "bash -c codex denied" 'bash -c "codex exec"'
check 2 "\$(codex exec) denied" 'echo $(codex exec)'
check 2 "timeout npx @openai/codex denied" "timeout 5 npx @openai/codex exec"
check 2 "codex on a later line denied" $'ls\ncodex exec'

# --- 正規形でないゲート呼び出し (許可回数 0 でも形の判定が先) ---
check 2 "relative gate path denied" "bash tools/codex_review_gate.sh base"
assert_err_contains "codex_review_gate.sh" "non-canonical stderr shows the canonical form"
check 2 "chained gate denied" "bash $CANON base; true"
check 2 "worktree copy of the gate denied" "bash $WORKER/tools/codex_review_gate.sh base"
check 2 "gate with two args denied" "bash $CANON base extra"

# --- codex を含むだけの無関係なコマンド ---
check 0 "grep codex allowed" "grep -n codex file"
check 0 "commit message mentioning codex allowed" 'git commit -m "codex exec を塞ぐ"'
check 0 "gate test script allowed" "bash tests/test-codex-review-gate.sh"
check 0 "ls allowed" "ls"
assert_eq "$(count_kind codex_round_denied)" "2" "non-gate commands write no events"

# --- fail-closed ---
check 2 "allowance 0 denies the canonical gate" "$GATE"
make_db "$TMP/other.db" "$WORKER" t1
check 2 "STATE_DB_PATH pointing at a fresh DB is ignored" "$GATE" STATE_DB_PATH="$TMP/other.db"
check 2 "unresolvable task denied" "$GATE" WORKER_DIR="$TMP/workers/unknown"
assert_err_contains "task を特定できない" "unresolvable task stderr says so"
mv "$ORG/.state/state.db" "$ORG/.state/state.db.bak"
check 2 "missing state.db denied" "$GATE"
[[ -e "$ORG/.state/state.db" ]]; assert_eq "$?" "1" "hook does not create a state.db"
mv "$ORG/.state/state.db.bak" "$ORG/.state/state.db"
check 2 "missing CLAUDE_ORG_PATH denied" "$GATE" CLAUDE_ORG_PATH=
ec=0; printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"codex"}}' | bash "$HOOK" >/dev/null 2>&1 || ec=$?
((TEST_NUM++)); if [[ $ec -eq 0 ]]; then echo "ok $TEST_NUM - non-Bash tool passes through"; ((PASS++)); else echo "not ok $TEST_NUM - non-Bash tool passes through"; ((FAIL++)); fi

echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
