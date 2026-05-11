#!/usr/bin/env bash
# block-workers-delete.sh のテスト
# 実行: bash .hooks/test-block-workers-delete.sh

set -euo pipefail

HOOK=".hooks/block-workers-delete.sh"
PASS=0
FAIL=0
# hook と同じ流儀で workers パスを解決する（registry/org-config.md の workers_dir は
# ORG_ROOT 起点の相対パスとして定義されているため、CLAUDE_ORG_PATH があれば優先する）
TEST_ORG_ROOT="${CLAUDE_ORG_PATH:-$(pwd)}"
WORKERS_DIR=$(realpath -m "$TEST_ORG_ROOT/../workers")

run_test() {
  local description="$1"
  local input_json="$2"
  local expected_exit="$3"  # 0=許可, 2=ブロック

  actual_exit=0
  echo "$input_json" | bash "$HOOK" >/dev/null 2>&1 || actual_exit=$?

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

HOOK_ABS="$(realpath "$HOOK")"
# .dispatcher 配下を擬似 cwd として使う。ORG_ROOT は TEST_ORG_ROOT に揃える
# （WORKERS_DIR と整合した workers パス解決を hook 側で起こすため）
ALT_CWD="$(pwd)/.dispatcher"

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
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
