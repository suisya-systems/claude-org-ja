#!/usr/bin/env bash
# Tests for .hooks/lib/deny-record.sh (Issue #1194): hook の拒否が events に hook_denied を 1 行残す
#   検査: 拒否しうる hook (.hooks/*.sh) はすべて共通の出口を最初の実行行で source し、
#         自前の EXIT trap も stdout の JSON 判定 (permissionDecision 等) も持たない
#   動作: 拒否で 1 行・許可で 0 行・stderr と終了コードは変わらない・記録失敗でも拒否は変わらない
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS="$SCRIPT_DIR/../.hooks"
SOURCE_LINE='source "$(dirname "${BASH_SOURCE[0]}")/lib/deny-record.sh"'
# 拒否しない (常に exit 0) hook。足すときは理由を書く。
NON_DENYING=(
  check-dispatcher-loop.sh # 助言だけ (fail-open)
  context-record.sh        # 記録だけ (fail-open)
)

PASS=0; FAIL=0; TEST_NUM=0
ok() { TEST_NUM=$((TEST_NUM + 1)); PASS=$((PASS + 1)); echo "ok $TEST_NUM - $1"; }
ng() { TEST_NUM=$((TEST_NUM + 1)); FAIL=$((FAIL + 1)); echo "not ok $TEST_NUM - $1"; }

# --- 検査: 共通の出口を通らない拒否が無いこと -------------------------------------
for f in "$HOOKS"/*.sh; do
  name=$(basename "$f")
  first=$(grep -vE '^[[:space:]]*(#|$)' "$f" | head -n 1)
  if [[ " ${NON_DENYING[*]} " == *" $name "* ]]; then
    grep -qE '\bexit 2\b' "$f" && ng "$name is listed as non-denying but has exit 2" || ok "$name is non-denying"
    continue
  fi
  [[ "$first" == "$SOURCE_LINE" ]] && ok "$name sources deny-record first" || ng "$name does not source deny-record as its first command (got: $first)"
  grep -qE '^[[:space:]]*trap .*EXIT' "$f" && ng "$name sets its own EXIT trap" || ok "$name has no own EXIT trap"
  grep -qE 'permissionDecision|"decision"' "$f" && ng "$name decides via stdout JSON" || ok "$name decides only via exit code"
done

# --- 動作 ------------------------------------------------------------------------
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
DB="$TMP/state.db"
rows() { python3 -c "
import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
for (p,) in c.execute(\"select payload_json from events where kind='hook_denied' order by id\"): print(p)
" "$DB" 2>/dev/null; }

DENY='{"tool_name":"Skill","tool_input":{"skill":"codex:rescue"}}'
ec=0; printf '%s' "$DENY" | STATE_DB_PATH="$DB" bash "$HOOKS/block-codex-rescue.sh" >/dev/null 2>"$TMP/err" || ec=$?
[[ $ec -eq 2 ]] && ok "deny still exits 2" || ng "deny exit code changed ($ec)"
grep -q 'codex:rescue skill は使えません' "$TMP/err" && ok "deny stderr is passed through" || ng "deny stderr lost"
out=$(rows)
[[ $(printf '%s\n' "$out" | grep -c .) -eq 1 ]] && ok "deny writes one hook_denied row" || ng "expected 1 row, got: $out"
printf '%s' "$out" | python3 -c '
import json,sys
p=json.loads(sys.stdin.read())
assert p["hook"]=="block-codex-rescue.sh" and p["tool"]=="Skill" and p["actor"]=="hook", p
assert "codex:rescue skill" in p["reason"], p
assert p["command"]=="{\"skill\": \"codex:rescue\"}", p
' && ok "row carries hook / tool / reason / command" || ng "row fields wrong: $out"

LONG=$(printf 'x%.0s' {1..400})
ec=0; printf '{"tool_name":"Bash","tool_input":{"command":"bash tools/pr-watch.sh %s"}}' "$LONG" \
  | STATE_DB_PATH="$DB" bash "$HOOKS/block-adhoc-pr-watch.sh" >/dev/null 2>&1 || ec=$?
last=$(rows | tail -n 1)
[[ $ec -eq 2 ]] && printf '%s' "$last" | python3 -c '
import json,sys
p=json.loads(sys.stdin.read())
assert p["hook"]=="block-adhoc-pr-watch.sh" and len(p["command"])==200 and len(p["reason"])<=500, p
' && ok "command is cut to 200 chars" || ng "long command not cut ($ec): $last"

before=$(rows | grep -c .)
ec=0; out=$(printf '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | STATE_DB_PATH="$DB" bash "$HOOKS/block-adhoc-pr-watch.sh" 2>&1) || ec=$?
[[ $ec -eq 0 && -z "$out" && $(rows | grep -c .) -eq $before ]] && ok "allow writes nothing" || ng "allow changed ($ec, $out)"

# 記録先に書けなくても拒否は変わらない
ec=0; printf '%s' "$DENY" | STATE_DB_PATH="/dev/null/state.db" bash "$HOOKS/block-codex-rescue.sh" >/dev/null 2>"$TMP/err2" || ec=$?
[[ $ec -eq 2 ]] && cmp -s "$TMP/err" "$TMP/err2" && ok "record failure keeps the deny and its message" || ng "record failure changed the deny ($ec)"

# 一時ディレクトリが作れなくても拒否は変わらない (記録だけあきらめる)
ec=0; printf '%s' "$DENY" | TMPDIR=/dev/null/x STATE_DB_PATH="$DB" bash "$HOOKS/block-codex-rescue.sh" >/dev/null 2>"$TMP/err3" || ec=$?
[[ $ec -eq 2 ]] && grep -q 'codex:rescue skill は使えません' "$TMP/err3" && ok "no temp dir keeps the deny" || ng "no temp dir changed the deny ($ec)"

echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
