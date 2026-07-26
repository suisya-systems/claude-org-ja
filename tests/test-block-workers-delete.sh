#!/usr/bin/env bash
# block-workers-delete.sh のテスト
# 実行: bash tests/test-block-workers-delete.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-workers-delete.sh"
PASS=0
FAIL=0

# Portable realpath -m (matches hook fallback: GNU realpath → python3 → python)
portable_realpath() {
  local target="$1"
  if result=$(command realpath -m "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  else
    echo "FATAL: realpath -m も python も利用できません" >&2
    exit 1
  fi
}

# hook と同じ流儀で workers パスを解決する（registry/org-config.md の workers_dir は
# ORG_ROOT 起点の相対パスとして定義されている）。既定は REPO_ROOT で、hook 側にも
# 同じ値を CLAUDE_ORG_PATH として明示的に渡すことで、ランナーの cwd に依存せず
# テストと hook の workers パス解決を一致させる。
TEST_ORG_ROOT="${CLAUDE_ORG_PATH:-$REPO_ROOT}"
WORKERS_DIR=$(portable_realpath "$TEST_ORG_ROOT/../workers")

run_test() {
  local description="$1"
  local input_json="$2"
  local expected_exit="$3"  # 0=許可, 2=ブロック

  actual_exit=0
  echo "$input_json" | CLAUDE_ORG_PATH="$TEST_ORG_ROOT" bash "$HOOK" >/dev/null 2>&1 || actual_exit=$?

  if [[ "$actual_exit" -eq "$expected_exit" ]]; then
    echo "  PASS: $description"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $description (expected exit $expected_exit, got $actual_exit)"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== block-workers-delete.sh tests ==="
echo ""

# --- ブロックされるべきケース ---
echo "[ブロック対象]"

run_test "rm -rf workers/WI-016 (絶対パス)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf \\\"${WORKERS_DIR}/WI-016\\\"\"}}" \
  2

run_test "rm -rf workers/clock-app11 (絶対パス)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf ${WORKERS_DIR}/clock-app11\"}}" \
  2

run_test "rm -r workers/WI-003 (-r のみ)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -r ${WORKERS_DIR}/WI-003\"}}" \
  2

run_test "rm -rf workers/ (ディレクトリ全体)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf ${WORKERS_DIR}\"}}" \
  2

run_test "rm -rf 相対パス ../workers/WI-016" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf ../workers/WI-016\"}}" \
  2

run_test "for ループでの rm -rf" \
  '{"tool_name":"Bash","tool_input":{"command":"for d in '"${WORKERS_DIR}"'/WI-*; do rm -rf \"$d\"; done"}}' \
  2

run_test "複数引数の rm -rf (workers含む)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf /tmp/junk ${WORKERS_DIR}/WI-016\"}}" \
  2

run_test "rm -R 大文字 short flag" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -R ${WORKERS_DIR}/WI-016\"}}" \
  2

run_test "rm -Rf 大文字 short flag combined" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -Rf ${WORKERS_DIR}/WI-016\"}}" \
  2

run_test "rm -f -r フラグ分離 (separate short flags)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -f -r ${WORKERS_DIR}/WI-016\"}}" \
  2

run_test "rm --force -r 長短オプション混在" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm --force -r ${WORKERS_DIR}/WI-016\"}}" \
  2

# Windows バックスラッシュパスのテスト（jq で正しく JSON エスケープ）
WORKERS_WIN_PATH=$(echo "$WORKERS_DIR" | sed 's|^/\([a-zA-Z]\)/|\U\1:/|')
WORKERS_BS_CMD="rm -rf $(echo "$WORKERS_WIN_PATH" | tr '/' '\\')\\WI-016"
WORKERS_BS_JSON=$(jq -n --arg cmd "$WORKERS_BS_CMD" '{"tool_name":"Bash","tool_input":{"command":$cmd}}')
run_test "rm -rf Windows バックスラッシュパス" \
  "$WORKERS_BS_JSON" \
  2

run_test "rm --recursive (長オプション)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm --recursive ${WORKERS_DIR}/WI-016\"}}" \
  2

run_test "rm --force --recursive (長オプション複数)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm --force --recursive ${WORKERS_DIR}/WI-016\"}}" \
  2

echo ""

# --- renga 例外の回避経路 (Issue #777) ---
# 「コマンド列のどこかに renga トークンがある」だけで例外が成立していた頃の回避を固定する。
# 例外はトップレベルの全セグメントが renga 起動である場合にのみ成立する。
echo "[renga 例外の回避防止]"

run_test "echo renga ; rm -rf workers (無害な renga トークン混入)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo renga ; rm -rf ${WORKERS_DIR}\"}}" \
  2

run_test "renga 起動に破壊的コマンドを後続連結 (renga ... && rm -rf workers)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"renga new-tab --cwd ${WORKERS_DIR}/dummy-test && rm -rf ${WORKERS_DIR}/dummy-test\"}}" \
  2

run_test "renga 起動をパイプ後段に置いた rm -rf workers" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"renga list | grep -q x ; rm -rf ${WORKERS_DIR}/WI-016\"}}" \
  2

run_test "コメントに renga を書いた rm -rf workers" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf ${WORKERS_DIR}/WI-016 # renga cleanup\"}}" \
  2

# コマンド置換 / プロセス置換は renga 起動前にシェルが実行するため、renga 例外の内側でも
# 破壊的コマンドの実行経路になる。展開が起きる形では例外を成立させない。
SUBST_CMD="renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p \"\$(rm -rf ${WORKERS_DIR}/dummy-test)\""
run_test "renga 起動の引数内 \$( ) コマンド置換" \
  "$(jq -n --arg cmd "$SUBST_CMD" '{"tool_name":"Bash","tool_input":{"command":$cmd}}')" \
  2

BACKTICK_CMD="renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p \"\`rm -rf ${WORKERS_DIR}/dummy-test\`\""
run_test "renga 起動の引数内バッククォート置換" \
  "$(jq -n --arg cmd "$BACKTICK_CMD" '{"tool_name":"Bash","tool_input":{"command":$cmd}}')" \
  2

PROCSUB_CMD="renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p x <(rm -rf ${WORKERS_DIR}/dummy-test)"
run_test "renga 起動の <( ) プロセス置換" \
  "$(jq -n --arg cmd "$PROCSUB_CMD" '{"tool_name":"Bash","tool_input":{"command":$cmd}}')" \
  2

echo ""

# --- 許可されるべきケース ---
echo "[許可対象]"

run_test "rm (再帰なし) で個別ファイル削除" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm ${WORKERS_DIR}/WI-016/temp.txt\"}}" \
  0

run_test "rm -rf で workers 外のディレクトリ" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf /tmp/some-dir\"}}" \
  0

run_test "rm --preserve-root で長オプション内の r は無視 (false positive prevention)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm --preserve-root ${WORKERS_DIR}/WI-016/temp.txt\"}}" \
  0

run_test "rm --interactive=never で長オプション内の r は無視" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm --interactive=never ${WORKERS_DIR}/WI-016/tmp.txt\"}}" \
  0

run_test "rm -rf /tmp/workers/cache (P2: 無関係パスの偽陽性防止)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf /tmp/workers/cache\"}}" \
  0

run_test "renga new-tab で workers パスを含むコマンド (偽陽性防止)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"renga new-tab --command \\\"cd ${WORKERS_DIR}/dummy-test && claude -p 'rm -rf test'\\\"\"}}" \
  0

# 正当なワーカー起動: 先頭が renga で、引用符の内側に workers パスと rm が同居する形。
# 引用符内の ; / && では分割しないため、1 セグメント = renga 起動として例外が成立する。
run_test "renga 起動 (--cwd workers/... + -p 内に rm、引用符内に区切り文字)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p 'cd ${WORKERS_DIR}/dummy-test && rm -rf build; make'\"}}" \
  0

run_test "renga 起動を renga コマンドのみで連結 (全セグメント renga)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"renga list && renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p 'rm -rf tmp'\"}}" \
  0

run_test "環境変数プレフィックス付き renga 起動 (VAR=value renga ...)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"ORG_TRANSPORT=broker renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p 'rm -rf tmp'\"}}" \
  0

# シングルクォート内の $( ) はシェルに展開されないため不活性 = 例外は成立する
SQ_SUBST_CMD="renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p 'echo \$(rm -rf ${WORKERS_DIR}/dummy-test)'"
run_test "renga 起動のシングルクォート内 \$( ) (不活性なので許可)" \
  "$(jq -n --arg cmd "$SQ_SUBST_CMD" '{"tool_name":"Bash","tool_input":{"command":$cmd}}')" \
  0

# リダイレクトの & は区切りではない (2>&1 で余計なセグメントを作らない)
run_test "renga 起動 + 2>&1 リダイレクト (& を区切りにしない)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p 'rm -rf tmp' > /dev/null 2>&1\"}}" \
  0

run_test "renga 起動 + &> リダイレクト (& を区切りにしない)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p 'rm -rf tmp' &> /dev/null\"}}" \
  0

# ダブルクォート内のエスケープ済み引用符 \" で引用符状態を誤って閉じない
ESCQ_CMD="renga new-tab --cwd ${WORKERS_DIR}/dummy-test -p \"echo \\\"x\\\" && rm -rf build\""
run_test "renga 起動の -p 内にエスケープ済み引用符 + && (偽陽性防止)" \
  "$(jq -n --arg cmd "$ESCQ_CMD" '{"tool_name":"Bash","tool_input":{"command":$cmd}}')" \
  0

run_test "ls workers ディレクトリ (削除ではない)" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"ls ${WORKERS_DIR}/\"}}" \
  0

run_test "git コマンド" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"}}" \
  0

run_test "Edit ツール (Bash ではない)" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"${WORKERS_DIR}/WI-016/file.txt\"}}" \
  0

run_test "空コマンド" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"\"}}" \
  0

echo ""

# --- cwd 非依存性のテスト ---
# 回帰: hook が registry/org-config.md を cwd 相対で読んでいた頃、Dispatcher cwd=.dispatcher/
# では grep が exit 2 を返し set -euo pipefail で全 Bash がブロックされていた。
# また、WORKERS_REL の正規化を cwd 起点で行うと絶対パス指定の workers 削除を検知できない。
# CLAUDE_ORG_PATH 起点で config / workers パスを解決していることを担保する。
echo "[cwd 非依存性 (CLAUDE_ORG_PATH 起点解決)]"

# HOOK は REPO_ROOT 起点で組み立て済みなので既に絶対パス。
# .dispatcher 配下を擬似 cwd として使う。ORG_ROOT は TEST_ORG_ROOT に揃える
# （WORKERS_DIR と整合した workers パス解決を hook 側で起こすため）
HOOK_ABS="$HOOK"
ALT_CWD="$REPO_ROOT/.dispatcher"

if [[ ! -d "$ALT_CWD" ]]; then
  echo "  SKIP: .dispatcher ディレクトリが無いため cwd 非依存テストを省略"
else
  run_test_cwd() {
    local description="$1"
    local cwd="$2"
    local org_path="$3"
    local input_json="$4"
    local expected_exit="$5"

    actual_exit=0
    ( cd "$cwd" && CLAUDE_ORG_PATH="$org_path" bash "$HOOK_ABS" ) <<< "$input_json" >/dev/null 2>&1 || actual_exit=$?

    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
      echo "  PASS: $description"
      PASS=$((PASS + 1))
    else
      echo "  FAIL: $description (expected exit $expected_exit, got $actual_exit)"
      FAIL=$((FAIL + 1))
    fi
  }

  run_test_cwd "Dispatcher cwd + 良性コマンド (回帰: 全 Bash ブロック)" \
    "$ALT_CWD" "$TEST_ORG_ROOT" \
    '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
    0

  run_test_cwd "Dispatcher cwd + workers 絶対パスの rm -rf (Blocker 回帰)" \
    "$ALT_CWD" "$TEST_ORG_ROOT" \
    "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf ${WORKERS_DIR}/dummy-task\"}}" \
    2

  run_test_cwd "Dispatcher cwd + CLAUDE_ORG_PATH 未設定 + 良性コマンド (config 不在 fallback)" \
    "$ALT_CWD" "" \
    '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
    0
fi

echo ""
echo "# $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
