#!/usr/bin/env bash
# Tests for .hooks/block-relative-close-pane.sh (Issue #1018)
# Validates: exit code (0=allow/passthrough, 2=block) と deny stderr の誘導文言。
#
# 確認観点:
#   deny 側 (相対セレクタ):
#     - target="focused"                        -> block (2026-09-20 の実事故経路)
#     - target 省略 / null                      -> block (既定が相対セレクタ)
#     - target="secretary"                      -> block (窓口は誰からも閉じさせない)
#     - target="worker-foo" / "dispatcher" 等   -> block (裸の name のフォールスルー)
#     - renga / broker いずれの完全修飾名でも   -> block (transport 非依存)
#   allow 側 (正規経路を壊さないこと):
#     - target=3 / "3" / "%3"                   -> allow (数値 pane id)
#     - target="w10:p3"                         -> allow (herdr native handle)
#     - handle 似の形 ("w1:p" / " w1:p2" / 末尾改行 等) -> block
#     - target="pr-watch-51"                    -> allow (DD-2 stale-binding carve-out)
#     - Group B (close_pane / set_pane_identity) 以外 -> passthrough
#   set_pane_identity も同じ deny/allow。pr-watch carve-out は close_pane だけ
#   fail-closed:
#     - 壊れた payload は tests/test-hooks-payload-fail-closed.sh が横断で固定するので
#       ここでは重複させない。ここで見るのは close_pane 固有の target 判定だけ。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-relative-close-pane.sh"

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

# run_hook <json> <stderr_file> -> echoes exit code
run_hook() {
  local json="$1" stderr_file="$2" ec=0
  printf '%s' "$json" | bash "$HOOK" >/dev/null 2>"$stderr_file" || ec=$?
  echo "$ec"
}

# payload_with_target <tool_name> <raw_json_target_literal>
payload_with_target() {
  printf '{"tool_name":"%s","tool_input":{"target":%s}}' "$1" "$2"
}

RENGA="mcp__renga-peers__close_pane"
BROKER="mcp__org-broker__close_pane"
SPI_BROKER_EARLY="mcp__org-broker__set_pane_identity"

# --- Deny: 相対セレクタ ---

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"focused"')" "$stderr")
assert_exit 2 "$ec" "renga: target=\"focused\" is blocked"
assert_stderr_contains "Issue #1018" "$stderr" "focused deny stderr cites the incident"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"focused"')" "$stderr")
assert_exit 2 "$ec" "broker: target=\"focused\" is blocked (transport-independent)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"'"$RENGA"'","tool_input":{}}' "$stderr")
assert_exit 2 "$ec" "omitted target is blocked (the default resolves relatively)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" 'null')" "$stderr")
assert_exit 2 "$ec" "target=null is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"secretary"')" "$stderr")
assert_exit 2 "$ec" "target=\"secretary\" is blocked"
assert_stderr_contains "窓口ペイン" "$stderr" "secretary deny stderr names the secretary pane"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"dispatcher"')" "$stderr")
assert_exit 2 "$ec" "target=\"dispatcher\" (bare name) is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"worker-ja-close-pane-guard"')" "$stderr")
assert_exit 2 "$ec" "target=\"worker-{task_id}\" (bare name) is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"curator"')" "$stderr")
assert_exit 2 "$ec" "target=\"curator\" (bare name) is blocked"

# 数値に見えるが数値でない形も通さない。
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"3x"')" "$stderr")
assert_exit 2 "$ec" "target=\"3x\" is blocked (not a pane id)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '1.5')" "$stderr")
assert_exit 2 "$ec" "non-integer numeric target is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '["3"]')" "$stderr")
assert_exit 2 "$ec" "array target is blocked (fail-closed)"

# pr-watch carve-out を名乗るだけの別名は通さない。
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"pr-watch"')" "$stderr")
assert_exit 2 "$ec" "target=\"pr-watch\" without a PR suffix is blocked"

# carve-out は Group B を単一タブモデルで解決する backend でのみ成立する
# (契約 T-§4.2: 積極的証拠 = 完全修飾名が mcp__org-broker__*)。
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"pr-watch-51"')" "$stderr")
assert_exit 2 "$ec" "carve-out does not apply on renga (bare name would fall through to another org)"
assert_stderr_contains "mcp__org-broker__" "$stderr" "renga carve-out deny stderr names the required backend"

# --- Allow: 正規経路 ---

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '3')" "$stderr")
assert_exit 0 "$ec" "numeric pane id (3) is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"98"')" "$stderr")
assert_exit 0 "$ec" "numeric pane id as a string (\"98\") is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$RENGA" '"%3"')" "$stderr")
assert_exit 0 "$ec" "tmux-style pane id (\"%3\") is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"pr-watch-51"')" "$stderr")
assert_exit 0 "$ec" "DD-2 stale-binding carve-out (\"pr-watch-51\") is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"pr-watch-1011-ja"')" "$stderr")
assert_exit 0 "$ec" "cross-repo watcher name (\"pr-watch-1011-ja\") is allowed"

# --- herdr native handle (broker の list_panes id): 形を閉じた集合で通す ---

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$BROKER" '"w10:p3"')" "$stderr")
assert_exit 0 "$ec" "herdr handle (\"w10:p3\") is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$SPI_BROKER_EARLY" '"w1:p2"')" "$stderr")
assert_exit 0 "$ec" "set_pane_identity with herdr handle (\"w1:p2\") is allowed"

# herdr の pane 番号は 10 枚目から英字 (p9 の次は pA)。workspace 部も英数字を含みうる。
for good in '"w10:pA"' '"w3:pZ9"' '"w1W:p2"' '"w10:p0"'; do
  stderr=$(mktemp); TMPFILES+=("$stderr")
  ec=$(run_hook "$(payload_with_target "$BROKER" "$good")" "$stderr")
  assert_exit 0 "$ec" "herdr handle with alphanumeric segment ($good) is allowed"
done

for bad in '"w10:p"' '"w10:pA "' '"w10:p-A"' '"pA"' '"w10:pA\n"' '"w:pA"' '"w1:p"' '"w1:p2:p3"' '"W1:P2"' '"x1:p2"' '" w1:p2"' '"w1:p2 "' '"w1:p2\n"' '"3\n"' '"%3\t"'; do
  stderr=$(mktemp); TMPFILES+=("$stderr")
  ec=$(run_hook "$(payload_with_target "$BROKER" "$bad")" "$stderr")
  assert_exit 2 "$ec" "handle-like but malformed target ($bad) is blocked"
done

# --- set_pane_identity: 同じ Group B 規則。carve-out は close_pane だけ ---

SPI_RENGA="mcp__renga-peers__set_pane_identity"
SPI_BROKER="mcp__org-broker__set_pane_identity"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"'"$SPI_RENGA"'","tool_input":{"name":"secretary"}}' "$stderr")
assert_exit 2 "$ec" "set_pane_identity with omitted target is blocked"
assert_stderr_contains "set_pane_identity (" "$stderr" "set_pane_identity deny stderr names the operation"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$SPI_BROKER" '"focused"')" "$stderr")
assert_exit 2 "$ec" "set_pane_identity(target=\"focused\") is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$SPI_RENGA" '"secretary"')" "$stderr")
assert_exit 2 "$ec" "set_pane_identity(target=\"secretary\") (bare name) is blocked"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$SPI_BROKER" '"pr-watch-51"')" "$stderr")
assert_exit 2 "$ec" "pr-watch carve-out does not apply to set_pane_identity, even on broker"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$SPI_RENGA" '3')" "$stderr")
assert_exit 0 "$ec" "set_pane_identity with numeric pane id (3) is allowed"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "$(payload_with_target "$SPI_BROKER" '"%3"')" "$stderr")
assert_exit 0 "$ec" "set_pane_identity with tmux-style pane id (\"%3\") is allowed"

# --- Passthrough: Group B 以外 ---

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"mcp__renga-peers__send_keys","tool_input":{"target":"focused","text":"hi"}}' "$stderr")
assert_exit 0 "$ec" "send_keys(target=\"focused\") passes through (Group A, out of scope)"

stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"git status"}}' "$stderr")
assert_exit 0 "$ec" "unrelated tool passes through"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
