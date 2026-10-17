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
assert_eq "$STATUS" '{"task": "t1", "used": 6, "allowed": "unlimited", "denied": 2, "design_used": 0}' "status reports used/allowed/denied/design_used"
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

# --- 迂回形 (すべて deny、往復は数えない) ---
"$PY" "$CAP" set --task-id t1 --allowed unlimited --reason test --db-path "$ORG/.state/state.db" >/dev/null
STARTED=$(count_kind codex_round_started)
BYPASS=(
  "bash -s base < $CANON"
  "bash /dev/stdin base < $CANON"
  "cat $CANON | bash -s base"
  "echo codex exec | bash"
  'bash <<< "codex exec"'
  "source <(echo codex exec)"
  "command -p codex exec"
  "time -p codex exec"
  "function f { codex exec; }; f"
  "coproc codex exec"
  'env -S"codex exec"'
  "setsid codex exec"
  "strace -f codex exec"
  'su -c "codex exec"'
  'script -qc "codex exec" /dev/null'
  'find . -exec codex exec \;'
  "npm exec -- codex exec"
  "node /x/@openai/codex/bin/codex.js exec"
  'co""dex exec'
  'c\odex exec'
  $'c\\\nodex exec'
  $'ls # it\'s a comment\ncodex exec'
)
for cmd in "${BYPASS[@]}"; do
  check 2 "bypass denied: $cmd" "$cmd"
done
assert_eq "$(count_kind codex_round_started)" "$STARTED" "bypass forms consume no round"

# --- codex を含むが実行しないコマンド (must allow) ---
ALLOWED=(
  $'git commit -F - <<\'EOF\'\nblock `codex exec`\nEOF'
  "git commit -m 'block \`codex exec\`'"
  "grep -n \"codex\" README.md # it's fine"
  "npm i -g @openai/codex@latest"
  "which codex"
  "python3 -m pytest -q tools/test_codex_round_cap.py"
  "bash tests/test-codex-review-gate.sh"
  "cat tools/codex_review_gate.sh"
  "git log --grep codex"
)
for cmd in "${ALLOWED[@]}"; do
  check 0 "allowed: $cmd" "$cmd"
done

# --- MCP のペイン操作 ---
run_mcp() {  # run_mcp <tool> <field> <value> -> exit code
  local ec=0
  jq -cn --arg t "mcp__renga-peers__$1" --arg f "$2" --arg v "$3" '{tool_name:$t,tool_input:{($f):$v}}' \
    | env WORKER_DIR="$WORKER" CLAUDE_ORG_PATH="$ORG" bash "$HOOK" >/dev/null 2>"$LAST_ERR" || ec=$?
  echo "$ec"
}
check_mcp() {  # check_mcp <expected> <tool> <field> <value>
  local ec; ec=$(run_mcp "$2" "$3" "$4"); ((TEST_NUM++))
  if [[ "$ec" -eq "$1" ]]; then echo "ok $TEST_NUM - $2 $3='$4' -> $1"; ((PASS++))
  else echo "not ok $TEST_NUM - $2 $3='$4' (expected $1, got $ec)"; ((FAIL++)); fi
}
check_mcp 2 spawn_codex_pane command ""
check_mcp 2 spawn_pane command "codex exec"
check_mcp 2 new_tab command "codex exec"
check_mcp 2 send_keys text "codex exec"
check_mcp 2 send_keys text "$GATE"
check_mcp 0 spawn_pane command "ls"
check_mcp 0 send_keys text "hello"
check_mcp 0 spawn_claude_pane command "codex"
assert_eq "$(count_kind codex_round_started)" "$STARTED" "MCP calls consume no round"

# --- 設計レビュー (--design): 差分とは別枠でタスクごとに 1 回 ---
add_task() {  # add_task <worker_dir> <task>
  PYTHONPATH="$REPO_ROOT" "$PY" - "$ORG/.state/state.db" "$@" <<'PYEOF'
import sys
from tools.state_db import connect
from tools.state_db.writer import StateWriter
conn = connect(sys.argv[1])
w = StateWriter(conn)
w.append_event(kind="delegate_sent", actor="secretary", payload={"task": sys.argv[3], "dir": sys.argv[2]})
w.commit()
conn.close()
PYEOF
}
count_design_denied() {
  PYTHONPATH="$REPO_ROOT" "$PY" - "$ORG/.state/state.db" "$1" <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM events WHERE kind = 'codex_round_denied' "
                "AND json_extract(payload_json, '$.task') = ? "
                "AND json_extract(payload_json, '$.mode') = 'design'", (sys.argv[2],)).fetchone()[0])
PYEOF
}
W2="$TMP/workers/w2"; W3="$TMP/workers/w3"
mkdir -p "$W2" "$W3"
add_task "$W2" t2
add_task "$W3" t3
DESIGN="bash $CANON --design design.md"
check 0 "t2: design review allowed once" "$DESIGN" WORKER_DIR="$W2"
check 0 "t2: diff round 1 allowed after design" "$GATE" WORKER_DIR="$W2"
check 0 "t2: diff round 2 allowed" "$GATE" WORKER_DIR="$W2"
check 0 "t2: diff round 3 allowed (design consumed no diff round)" "$GATE" WORKER_DIR="$W2"
check 2 "t2: diff round 4 denied" "$GATE" WORKER_DIR="$W2"
check 2 "t2: second design review denied" 'bash "$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" --design docs/design.md' WORKER_DIR="$W2"
assert_err_contains "mode=design" "second design stderr carries mode=design"
assert_eq "$(count_design_denied t2)" "1" "t2: 1 codex_round_denied row with mode=design"
STATUS=$("$PY" "$CAP" status --task-id t2 --db-path "$ORG/.state/state.db")
assert_eq "$STATUS" '{"task": "t2", "used": 3, "allowed": 3, "denied": 2, "design_used": 1}' "t2 status counts diff and design separately"
for _ in 1 2 3; do run "$GATE" WORKER_DIR="$W3" >/dev/null; done
check 2 "t3: diff exhausted" "$GATE" WORKER_DIR="$W3"
check 0 "t3: diff exhaustion does not block the design review" "$DESIGN" WORKER_DIR="$W3"
for cmd in \
  "bash $CANON --design design.md extra" \
  "bash $CANON --design 'a;b.md'" \
  'bash '"$CANON"' --design "$(touch x).md"' \
  "bash $CANON --design design.md; true" \
  "bash $CANON --design -x" \
  "bash $CANON --design"; do
  check 2 "non-canonical design form denied: $cmd" "$cmd" WORKER_DIR="$W2"
done

# --- 判定器の異常 (無出力 exit 1 / 不正出力) は deny ---
for stub in 'import sys; sys.exit(1)' 'print("garbage")'; do
  FC="$TMP/fc-$((TEST_NUM))"
  mkdir -p "$FC/.hooks" "$FC/tools"
  cp "$HOOK" "$FC/.hooks/"
  printf '%s\n' "$stub" > "$FC/tools/codex_round_cap.py"
  ec=0
  jq -cn --arg c "$GATE" '{tool_name:"Bash",tool_input:{command:$c}}' \
    | env WORKER_DIR="$WORKER" CLAUDE_ORG_PATH="$ORG" bash "$FC/.hooks/block-codex-over-cap.sh" >/dev/null 2>&1 || ec=$?
  assert_eq "$ec" "2" "classifier stub '$stub' fails closed"
done

echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
