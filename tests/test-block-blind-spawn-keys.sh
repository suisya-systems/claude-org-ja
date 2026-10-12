#!/usr/bin/env bash
# Tests for .hooks/block-blind-spawn-keys.sh
# spawn-prompt-step の判定記録が無い／合わないキー送信 (send_keys) を deny するか。
#
# 確認観点:
#   deny 側:
#     - 記録なしの Enter (盲目 Enter)                              -> block
#     - 記録 {"keys":["Down"]} に Enter を送る                     -> block (キー不一致)
#     - 記録 {"enter":true} に text 付き Enter を送る              -> block
#     - 記録 {"enter":true} に Down + Enter を送る                 -> block
#     - 期限切れ / schema_version 違い / target 不一致の記録       -> block
#     - 記録は 1 回限り: allow の直後の同じ送信                   -> block
#     - done 記録に Down / text+Enter / Down+Enter を送る           -> block (done は Enter 単発だけ)
#     - 宛先が "focused" (記録があっても) / "%3" / 省略            -> block
#     - text に制御文字 (\r / \n / ESC 列)                          -> block
#     - keys が配列でない / enter が真偽値でない                   -> block
#     - 記録が壊れている                                           -> block (fail-closed)
#   allow 側:
#     - 記録 {"enter":true} に Enter                               -> allow
#     - 記録 {"keys":["Down"]} に keys ["down"] (大小文字違い)     -> allow
#     - 記録 {"enter":true} に keys ["Return"]                     -> allow (Return は Enter)
#     - 数値 pane id 宛で記録の target も同じ数値                  -> allow
#     - done 記録に Enter 単発 (3-5a kickoff)                      -> allow
#     - text だけの送信 (記録なし)                                 -> allow
#     - send_keys 以外のツール                                     -> passthrough
#   end-to-end: runtime の spawn-prompt-step --target が書いた記録で Enter が 1 回だけ通る。
#   壊れた payload は tests/test-hooks-payload-fail-closed.sh が横断で固定する。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-blind-spawn-keys.sh"

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

# state dir は state.db と同じディレクトリ。DB ファイル自体は hook が開かない。
S="$WORK/state"; mkdir -p "$S/dispatcher/spawn-prompt"
REC_DIR="$S/dispatcher/spawn-prompt"
now_ms() { python3 -c 'import time;print(int(time.time()*1000))'; }

# record <target> <action> <send_keys-json> [expires-offset-ms] [schema] [record-target]
record() {
  local exp=$(( $(now_ms) + ${4:-30000} ))
  printf '{"schema_version":%s,"task_id":"t1","target":"%s","action":"%s","send_keys":%s,"state":"folder_trust","decided_at_ms":0,"expires_at_ms":%s}' \
    "${5:-1}" "${6:-$1}" "$2" "$3" "$exp" >"$REC_DIR/$1.json"
}

# send <tool_input-json> [tool] -> exit code
send() {
  local ec=0
  printf '{"tool_name":"%s","tool_input":%s}' "${2:-mcp__renga-peers__send_keys}" "$1" \
    | STATE_DB_PATH="$S/state.db" bash "$HOOK" >/dev/null 2>"$WORK/stderr" || ec=$?
  echo "$ec"
}

T='"target":"worker-t1"'

assert_exit 2 "$(send "{$T,\"enter\":true}")" "blind Enter without a record is blocked"
grep -qF "spawn-prompt-step" "$WORK/stderr" && assert_exit 0 0 "deny message points to spawn-prompt-step" \
  || assert_exit 0 1 "deny message points to spawn-prompt-step"
assert_exit 0 "$(send "{$T,\"text\":\"ultracode で着手\"}")" "text-only send without a record is allowed"
assert_exit 0 "$(send "{$T,\"text\":\"x\",\"enter\":false,\"keys\":[]}")" "text with enter=false and empty keys is allowed"
assert_exit 2 "$(send "{$T,\"text\":\"\\r\"}")" "text carrying CR (raw Enter) is blocked"
assert_exit 2 "$(send "{$T,\"text\":\"\\n\"}")" "text carrying LF is blocked"
assert_exit 2 "$(send "{$T,\"text\":\"\\u001b[B\\r\"}")" "text carrying ESC [B + CR (Down, Enter) is blocked"
record worker-t1 send_keys '{"enter":true}'
assert_exit 2 "$(send "{$T,\"text\":\"\\r\"}")" "text CR is blocked even with an Enter record"
rm -f "$REC_DIR/worker-t1.json"
assert_exit 0 "$(send "{$T,\"enter\":true}" mcp__renga-peers__inspect_pane)" "non-send_keys tool passes through"

record worker-t1 send_keys '{"enter":true}'
assert_exit 0 "$(send "{$T,\"enter\":true}")" "Enter matching the record is allowed"
assert_exit 2 "$(send "{$T,\"enter\":true}")" "the same Enter again is blocked (record consumed)"
[[ -e "$REC_DIR/worker-t1.json" ]] && assert_exit 0 1 "record file is removed after use" \
  || assert_exit 0 0 "record file is removed after use"

record worker-t1 send_keys '{"keys":["Down"]}'
assert_exit 2 "$(send "{$T,\"enter\":true}")" "Enter against a Down record is blocked"
assert_exit 2 "$(send "{$T,\"keys\":[\"Down\",\"Enter\"]}")" "Down+Enter against a Down record is blocked"
assert_exit 2 "$(send "{$T,\"keys\":[\"Down\"],\"enter\":true}")" "Down with enter=true against a Down record is blocked"
assert_exit 0 "$(send "{$T,\"keys\":[\"down\"]}" mcp__org-broker__send_keys)" "lower-case down on broker matches a Down record"

record worker-t1 send_keys '{"enter":true}'
assert_exit 2 "$(send "{$T,\"text\":\"y\",\"enter\":true}")" "text+Enter against an Enter record is blocked"
assert_exit 0 "$(send "{$T,\"keys\":[\"Return\"]}")" "keys [Return] matches an Enter record"

record worker-t1 send_keys '{"enter":true}' -60000
assert_exit 2 "$(send "{$T,\"enter\":true}")" "expired record is blocked"
record worker-t1 send_keys '{"enter":true}' 30000 2
assert_exit 2 "$(send "{$T,\"enter\":true}")" "schema_version 2 record is blocked"
record worker-t1 send_keys '{"enter":true}' 30000 1 worker-other
assert_exit 2 "$(send "{$T,\"enter\":true}")" "record whose target differs from the file name is blocked"
record worker-t1 send_keys '{"enter":true}' 30000 true
assert_exit 2 "$(send "{$T,\"enter\":true}")" "schema_version true (bool) record is blocked"
printf 'not json' >"$REC_DIR/worker-t1.json"
assert_exit 2 "$(send "{$T,\"enter\":true}")" "unparsable record is blocked (fail-closed)"

record worker-t1 done null
assert_exit 2 "$(send "{$T,\"keys\":[\"Down\"]}")" "Down against a done record is blocked"
assert_exit 2 "$(send "{$T,\"keys\":[\"Escape\"]}")" "Escape against a done record is blocked"
assert_exit 0 "$(send "{$T,\"enter\":true}")" "single Enter against a done record is allowed (3-5a kickoff)"
assert_exit 2 "$(send "{$T,\"enter\":true}")" "done record is single-use too"
record worker-t1 done null
assert_exit 2 "$(send "{$T,\"text\":\"x\",\"enter\":true}")" "text+Enter against a done record is blocked"
assert_exit 2 "$(send "{$T,\"keys\":[\"Down\",\"Enter\"]}")" "Down+Enter against a done record is blocked"
assert_exit 0 "$(send "{$T,\"keys\":[\"Enter\"]}")" "the done record was not consumed by the denied calls"

record 7 send_keys '{"enter":true}'
assert_exit 0 "$(send '{"target":7,"enter":true}')" "numeric pane id target matches a record keyed by the same id"
record 8 send_keys '{"enter":true}'
assert_exit 0 "$(send '{"target":"8","enter":true}')" "string pane id target matches the same record"

record focused done null
assert_exit 2 "$(send '{"target":"focused","enter":true}')" "focused target with Enter is blocked even with a record"
assert_exit 2 "$(send '{"target":"%3","enter":true}')" "%3 target with Enter is blocked"
assert_exit 2 "$(send '{"enter":true}')" "missing target with Enter is blocked"
assert_exit 2 "$(send '{"target":"../worker-t1","enter":true}')" "path-like target is blocked"
assert_exit 2 "$(send "{$T,\"keys\":\"Enter\"}")" "keys as a string is blocked"
assert_exit 2 "$(send "{$T,\"enter\":\"true\"}")" "enter as a string is blocked"
assert_exit 2 "$(send "{$T,\"enter\":1}")" "enter as a number is blocked"

# Windows の py -3 はパイプの stdin を cp932 で読む。日本語の kickoff 本文でも通るか
ec=0; printf '{"tool_name":"mcp__renga-peers__send_keys","tool_input":{"target":"worker-t1","text":"ultracode で本タスクに着手してください。詳細は worker brief の通り。"}}' \
  | PYTHONIOENCODING=cp932 STATE_DB_PATH="$S/state.db" bash "$HOOK" >/dev/null 2>"$WORK/stderr" || ec=$?
assert_exit 0 "$ec" "Japanese kickoff text is allowed under a cp932 stdin encoding"

# end-to-end: runtime の spawn-prompt-step が書いた記録で Enter が 1 回だけ通る。
if python3 -c 'from claude_org_runtime.dispatcher.spawn_prompt import read_decision_record' 2>/dev/null; then
  step() {
    python3 -m claude_org_runtime.dispatcher.runner spawn-prompt-step \
      --target worker-e2e --task-id e2e --state-dir "$S" >/dev/null
  }
  TRUST='["Do you trust the files in this folder?","","❯ 1. Yes, I trust this folder","  2. No, exit","","Enter to confirm · Esc to cancel"]'
  TE='"target":"worker-e2e"'
  assert_exit 2 "$(send "{$TE,\"enter\":true}")" "e2e: blocked before spawn-prompt-step runs"
  printf '{"screen":%s,"previous_screen":%s,"elapsed_ms":0}' "$TRUST" "$TRUST" | step
  assert_exit 0 "$(send "{$TE,\"enter\":true}")" "e2e: Enter allowed after spawn-prompt-step decided send_keys"
  assert_exit 2 "$(send "{$TE,\"enter\":true}")" "e2e: second Enter blocked"
  printf '{"screen":%s,"previous_screen":%s,"elapsed_ms":0}' "$TRUST" "$TRUST" | step
  printf '{"screen":%s,"previous_screen":null,"elapsed_ms":0}' "$TRUST" | step
  [[ -e "$REC_DIR/worker-e2e.json" ]] && assert_exit 0 1 "e2e: a wait decision removes the earlier send_keys record" \
    || assert_exit 0 0 "e2e: a wait decision removes the earlier send_keys record"
  assert_exit 2 "$(send "{$TE,\"enter\":true}")" "e2e: Enter blocked after the wait decision"
  COMPOSER='["────────────────────","❯ ultracode で本タスクに着手","────────────────────"]'
  printf '{"screen":%s,"previous_screen":null,"elapsed_ms":0}' "$COMPOSER" | step
  assert_exit 0 "$(send "{$TE,\"enter\":true}")" "e2e: kickoff Enter allowed after a done decision"
  # 参照実装 read_decision_record と同じ記録を有効とみなす
  record worker-e2e send_keys '{"enter":true}'
  python3 -c 'import sys
from pathlib import Path
from claude_org_runtime.dispatcher.spawn_prompt import read_decision_record
sys.exit(0 if read_decision_record(Path(sys.argv[1]), "worker-e2e") else 1)' "$S"
  assert_exit 0 $? "e2e: the test fixture record is valid for the runtime reader"
else
  echo "ok - # SKIP e2e: claude_org_runtime (>=0.1.50) not importable"
fi

echo "# block-blind-spawn-keys: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
