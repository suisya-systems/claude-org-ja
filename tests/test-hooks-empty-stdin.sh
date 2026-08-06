#!/usr/bin/env bash
# Cross-hook invariant test: 空 payload は必ず fail-closed (Issue #834)
#
# 背景:
#   jq は「JSON 値がゼロ個」の入力を parse error にせず、出力なしで exit 0 を返す:
#       printf ''   | jq -e 'type == "object"'  -> exit 0 (出力なし)
#       printf 'x{' | jq -e 'type == "object"'  -> exit 4 (parse error)
#   このため `INPUT=$(cat)` した空 stdin は型ガードもフィールド抽出もすり抜け、
#   抽出結果が空文字になって「対象外ツール」「コマンド無し」の passthrough exit 0 に
#   落ちる。不正 JSON は deny されるのに空 payload だけが素通りする穴になっていた。
#
# 本テストの立て付け:
#   個別の hook テストに 1 件ずつ書くのではなく、.hooks/*.sh を動的に列挙して
#   「全 hook が空 stdin を exit 2 で deny する」ことを一括検査する。将来 hook が
#   追加されたときも自動的に検査対象へ入り、同型の穴の再発を検出できる。
#
#   exit code だけでなく stderr の deny 理由も検査する。check-worker-boundary /
#   block-org-structure / block-dispatcher-out-of-scope は環境変数未設定でも exit 2 に
#   なるため、環境変数を与えたうえで「空 payload ガードが理由で落ちた」ことまで
#   確認しないと偽 pass になる。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.hooks"

# 全 hook 共通の deny 理由マーカー。各 hook の deny メッセージはこの語を含む。
DENY_MARKER="PreToolUse payload が空でした"

# 検査対象外。ファイル名と理由を 1 行ずつ明示する。
# - fixture-always-block.sh: hook 配線の手動確認用 fixture であり常時 exit 2。
#   stdin を /tmp/hook-test.log へ追記する副作用があるためテストからは呼ばない。
EXEMPT=("fixture-always-block.sh")

PASS=0; FAIL=0; TEST_NUM=0

TMPDIR_TEST=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR_TEST"; }
trap cleanup EXIT

# hook が要求する環境変数。未設定だと env ガードで exit 2 になり、空 payload ガードが
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

# check_hook <hook_path> <label> <stdin_payload>
check_hook() {
  local hook="$1" label="$2" payload="$3"
  local base stderr_file exit_code=0
  base="$(basename "$hook")"
  stderr_file="$TMPDIR_TEST/stderr.$$"

  printf '%s' "$payload" | bash "$hook" >/dev/null 2>"$stderr_file" || exit_code=$?

  if [[ $exit_code -eq 2 ]]; then
    assert_ok yes "$base: $label stdin is blocked (exit 2)"
  else
    assert_ok no "$base: $label stdin is blocked (exit 2)" "got exit $exit_code"
  fi

  if grep -qF "$DENY_MARKER" "$stderr_file" 2>/dev/null; then
    assert_ok yes "$base: $label stdin denied by the empty-payload guard"
  else
    assert_ok no "$base: $label stdin denied by the empty-payload guard" \
      "stderr did not contain '$DENY_MARKER'"
  fi

  rm -f "$stderr_file"
}

hook_count=0
for hook in "$HOOKS_DIR"/*.sh; do
  [[ -f "$hook" ]] || continue
  base="$(basename "$hook")"
  is_exempt "$base" && continue
  ((hook_count++))

  check_hook "$hook" "empty" ''
  # 空白のみの入力も jq から見れば同じ「JSON 値ゼロ個」で、同じ穴になる。
  check_hook "$hook" "whitespace-only" $'  \n\t \n'
done

# --- jq が無い環境でも空 payload は deny される (ガード順序の回帰検査) ---
#
# 他の hook は jq 未インストール時に exit 2 で fail-closed するので、空 payload が
# 素通りする余地がない。block-workers-delete.sh だけは「窓口の全 Bash を止めるのは
# 過剰」という明示的な判断で jq 未インストール時に exit 0 してスキップする。そのため
# 空 payload ガードをその分岐より後ろに置くと、jq なし環境で空 stdin がガードに
# 到達せず素通りする。ガードが jq チェックより前にあることを実行で固定する。
#
# 検査用 PATH には cat だけを symlink する。ガード到達までに要る外部コマンドは cat のみ
# （echo / command -v / [[ ]] は bash builtin）で、jq を「存在しない」状態にできる。
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
