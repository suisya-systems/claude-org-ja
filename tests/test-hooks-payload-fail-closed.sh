#!/usr/bin/env bash
# Cross-hook invariant test: 壊れた PreToolUse payload は必ず fail-closed (Issue #834)
#
# 守る不変条件:
#   .hooks/ の全 enforcement hook は、以下の payload を exit 2 で deny する。
#     (a) 空 stdin / 空白のみ stdin
#     (b) 不正 JSON (parse 不能)
#     (c) 有効 JSON だが top-level が object でない (配列 / 文字列 / 数値 / bool / null)
#     (d) 有効な object だが tool_input が object でない (文字列 / 配列 / bool)
#     (e) echo のオプション文字列そのもの ("-n" / "-e" / "-E")
#     (f) JSON 値が 2 個以上並んだ入力 (jq が既定で受け付ける「値の連なり」)
#   PreToolUse では exit 2 以外がすべて「非ブロッキング」= fail-open 扱いになるため、
#   payload を解釈できない時点で通してしまうと enforcement 層としての保証が崩れる。
#
# 元々の穴 (すべて実測で確認):
#   (a) jq は「JSON 値がゼロ個」の入力を parse error にせず出力なしで exit 0 を返す:
#           printf ''   | jq -e 'type == "object"'  -> exit 0
#           printf 'x{' | jq -e 'type == "object"'  -> exit 4
#       このため空 stdin は型ガードもフィールド抽出もすり抜け、抽出結果が空文字に
#       なって「対象外ツール」「コマンド無し」の passthrough exit 0 に落ちていた。
#   (b)(c)(d) `VAR=$(echo "$INPUT" | jq ...)` は parse error (exit 4) / 非 object への
#       index error (exit 5) で set -e により script ごと中断し、exit != 2 = fail-open に
#       なっていた。top-level が null の場合は jq が index を許すので error にすらならず、
#       抽出結果が空になって passthrough していた。
#   (e) bash の `echo "$INPUT"` は INPUT が "-n" / "-e" / "-E" 等のオプションと完全一致
#       すると 1 バイトも出力しない。jq は「値ゼロ個」で exit 0 を返すため (a) と同じ穴に
#       なる。hook 側は `printf '%s\n'` に統一して塞いである。
#   (f) jq は既定で「JSON 値の連なり」を受け付ける。object を 2 個並べると型ガードの述語が
#       各値について真になり exit 0、続く抽出は値を改行連結して返す (例: "Edit\nEdit") ので
#       ツール名の一致判定を外して passthrough していた。hook 側は `-s` + `length == 1` で
#       単一 object のみを受け付けるようにして塞いである。
#
# 本テストの立て付け:
#   個別の hook テストに 1 件ずつ書くのではなく、.hooks/*.sh を動的に列挙して一括検査する。
#   将来 hook を追加したときも自動的に検査対象へ入り、同型の穴の再発を検出できる。
#
#   exit code だけでなく stderr の deny 理由も検査する。check-worker-boundary /
#   block-org-structure / block-dispatcher-out-of-scope は環境変数未設定でも exit 2 に
#   なるため、環境変数を与えたうえで「payload ガードが理由で落ちた」ことまで確認しないと
#   偽 pass になる。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.hooks"

# 全 hook 共通の deny 理由マーカー。各 hook の deny メッセージはこの語を含む。
EMPTY_MARKER="PreToolUse payload が空でした"
PARSE_MARKER="JSON object として解析できませんでした"

# 検査対象外。ファイル名と理由を 1 行ずつ明示する。
# - fixture-always-block.sh: hook 配線の手動確認用 fixture であり常時 exit 2。
#   stdin を /tmp/hook-test.log へ追記する副作用があるためテストからは呼ばない。
# - block-adhoc-pr-watch.sh: enforcement ゲートではなく best-effort の安全網
#   (一次規律は .claude/rules/pr-ci-watch.md)。false positive を避けるため
#   判定不能な payload は意図的に許可側 (fail-open) に倒す設計。
#   専用テスト tests/test-block-adhoc-pr-watch.sh 側で挙動を検査する。
EXEMPT=("fixture-always-block.sh" "block-adhoc-pr-watch.sh")

PASS=0; FAIL=0; TEST_NUM=0

TMPDIR_TEST=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR_TEST"; }
trap cleanup EXIT

# hook が要求する環境変数。未設定だと env ガードで exit 2 になり、payload ガードが
# 効いているのか区別できなくなるため、正常値を与えたうえで検査する。
export WORKER_DIR="$TMPDIR_TEST/worker"
export CLAUDE_ORG_PATH="$TMPDIR_TEST/claude-org"
mkdir -p "$WORKER_DIR" "$CLAUDE_ORG_PATH"

assert_ok() {
  local cond="$1" desc="$2" detail="${3:-}"
  ((TEST_NUM++))
  if [[ "$cond" == "yes" ]]; then
    echo "ok $TEST_NUM - $desc"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc${detail:+ ($detail)}"
    ((FAIL++))
  fi
}

is_exempt() {
  local name="$1" item
  for item in "${EXEMPT[@]}"; do
    [[ "$item" == "$name" ]] && return 0
  done
  return 1
}

# check_hook <hook_path> <label> <expected_stderr_marker> <stdin_payload>
check_hook() {
  local hook="$1" label="$2" marker="$3" payload="$4"
  local base stderr_file exit_code=0
  base="$(basename "$hook")"
  stderr_file="$TMPDIR_TEST/stderr.$$"

  printf '%s' "$payload" | bash "$hook" >/dev/null 2>"$stderr_file" || exit_code=$?

  if [[ $exit_code -eq 2 ]]; then
    assert_ok yes "$base: $label is blocked (exit 2)"
  else
    assert_ok no "$base: $label is blocked (exit 2)" "got exit $exit_code"
  fi

  if grep -qF "$marker" "$stderr_file" 2>/dev/null; then
    assert_ok yes "$base: $label is denied by the payload guard"
  else
    assert_ok no "$base: $label is denied by the payload guard" \
      "stderr did not contain '$marker'"
  fi

  rm -f "$stderr_file"
}

# 検査 payload。"<label>\t<marker>\t<payload>" のタブ区切りで積む。
PAYLOADS=()
add_payload() { PAYLOADS+=("$1"$'\t'"$2"$'\t'"$3"); }

# (a) 空 / 空白のみ
add_payload "empty stdin"             "$EMPTY_MARKER" ''
add_payload "whitespace-only stdin"   "$EMPTY_MARKER" $'  \n\t \n'
# (b) 不正 JSON (parse 不能)
add_payload "malformed JSON"          "$PARSE_MARKER" 'not json{'
add_payload "truncated JSON"          "$PARSE_MARKER" '{"tool_name":"Agent","tool_input":{'
# (c) 有効 JSON だが top-level が非 object
add_payload "top-level string"        "$PARSE_MARKER" '"a string"'
add_payload "top-level array"         "$PARSE_MARKER" '[1,2,3]'
add_payload "top-level number"        "$PARSE_MARKER" '42'
add_payload "top-level boolean"       "$PARSE_MARKER" 'true'
add_payload "top-level null"          "$PARSE_MARKER" 'null'
# (d) 有効な object だが tool_input が非 object
add_payload "tool_input is a string"  "$PARSE_MARKER" '{"tool_name":"Agent","tool_input":"not-object"}'
add_payload "tool_input is an array"  "$PARSE_MARKER" '{"tool_name":"Bash","tool_input":[1,2,3]}'
add_payload "tool_input is a boolean" "$PARSE_MARKER" '{"tool_name":"Edit","tool_input":true}'
# (e) echo のオプション文字列そのもの (bash の echo が 1 バイトも出力しない値)
add_payload "echo option -n"          "$PARSE_MARKER" '-n'
add_payload "echo option -e"          "$PARSE_MARKER" '-e'
add_payload "echo option -neE"        "$PARSE_MARKER" '-neE'
# (f) JSON 値が 2 個以上 (jq が既定で受け付ける「値の連なり」)
add_payload "two JSON objects" "$PARSE_MARKER" \
  '{"tool_name":"Edit","tool_input":{"file_path":"/x"}} {"tool_name":"Edit","tool_input":{"file_path":"/x"}}'
add_payload "object followed by scalar" "$PARSE_MARKER" '{"tool_name":"Bash","tool_input":{"command":"ls"}} 42'

hook_count=0
for hook in "$HOOKS_DIR"/*.sh; do
  [[ -f "$hook" ]] || continue
  base="$(basename "$hook")"
  is_exempt "$base" && continue
  ((hook_count++))

  for entry in "${PAYLOADS[@]}"; do
    IFS=$'\t' read -r p_label p_marker p_payload <<<"$entry"
    check_hook "$hook" "$p_label" "$p_marker" "$p_payload"
  done
done

# --- jq が無い環境でも空 payload は deny される (ガード順序の回帰検査) ---
#
# 他の hook は jq 未インストール時に exit 2 で fail-closed するので、payload が素通り
# する余地がない。block-workers-delete.sh だけは「窓口の全 Bash を止めるのは過剰」と
# いう明示的な判断で jq 未インストール時に exit 0 してスキップする。そのため空 payload
# ガードをその分岐より後ろに置くと、jq なし環境で空 stdin がガードに到達せず素通りする。
# ガードが jq チェックより前にあることを実行で固定する。
#
# 不正 JSON / 非 object の判定は jq 自身を要するので jq チェックより後ろにあり、jq なし
# 環境ではこの hook 全体が意図的に無効化される (設計どおり)。ここで固定するのは「jq に
# 依存しない空 payload ガードだけは jq の有無に関わらず効く」という不変条件。
#
# 検査用 PATH には cat だけを symlink する。ガード到達までに要る外部コマンドは cat のみ
# (echo / command -v / [[ ]] は bash builtin) で、jq を「存在しない」状態にできる。
# bash 自身は PATH 解決に頼れないので絶対パスで起動する。
jq_less_bin="$TMPDIR_TEST/bin-without-jq"
mkdir -p "$jq_less_bin"
bash_abs="$(command -v bash || true)"
cat_abs="$(command -v cat || true)"
[[ -n "$cat_abs" ]] && ln -sf "$cat_abs" "$jq_less_bin/cat"

((TEST_NUM++))
if [[ -z "$bash_abs" || -z "$cat_abs" ]]; then
  # 検査環境を組めない場合は静かに pass させず、その旨を fail として可視化する。
  echo "not ok $TEST_NUM - jq-less PATH probe could not be set up (bash/cat not resolvable)"
  ((FAIL++))
else
  wd_hook="$HOOKS_DIR/block-workers-delete.sh"
  exit_code=0
  printf '' | env PATH="$jq_less_bin" "$bash_abs" "$wd_hook" >/dev/null 2>&1 || exit_code=$?
  if [[ $exit_code -eq 2 ]]; then
    echo "ok $TEST_NUM - block-workers-delete.sh: empty stdin is blocked even without jq on PATH"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - block-workers-delete.sh: empty stdin is blocked even without jq on PATH (got exit $exit_code)"
    ((FAIL++))
  fi
fi

# hook を 1 本も拾えていないと全 assert がスキップされ、空の green で通ってしまう。
((TEST_NUM++))
if [[ $hook_count -gt 0 ]]; then
  echo "ok $TEST_NUM - enumerated $hook_count hook(s) under .hooks/"
  ((PASS++))
else
  echo "not ok $TEST_NUM - enumerated $hook_count hook(s) under .hooks/ (expected at least 1)"
  ((FAIL++))
fi

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
