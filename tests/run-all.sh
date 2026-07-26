#!/usr/bin/env bash
# Test runner: executes all test-*.sh and reports summary
# Detects both test failures and abnormal exits (syntax errors, crashes)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
total_pass=0
total_fail=0
total_errors=0

# ---------------------------------------------------------------------------
# 収集
# ---------------------------------------------------------------------------
# このループが実行対象の SoT。収集パターンを増やしたら、下の収集漏れ検出ガードの
# TEST_NAME_RE も併せて見直すこと。
TEST_FILES=()
for test_file in "$SCRIPT_DIR"/test-*.sh "$SCRIPT_DIR"/sandbox/test_*.sh; do
  [[ -f "$test_file" ]] || continue
  TEST_FILES+=("$test_file")
done

# ---------------------------------------------------------------------------
# 収集漏れ検出ガード (Issue #787)
# ---------------------------------------------------------------------------
# 「テストに見える命名のシェルファイル」を git 追跡ファイル（index 込み）から列挙し、
# 上の収集ループが実際に拾った集合と突き合わせる。差分があれば error として fail する。
#
# Issue #787 の実体は個別テストのバグではなく「テストが .hooks/ に置かれていて CI から
# 静かに漏れていた」という置き場所のズレだった。該当ファイルを救うだけでは同型が再発する
# ため、ズレそのものを検出する。既知本数の固定チェックではなく未収集ファイルの検出に
# しているのは、テストを 1 本足すたびに数字を更新する保守コストを避けるため。
#
# 検出は CI ではなくこのランナーに置く。手元実行と CI の判定基準を 1 つに保つため
# （workflow 側に書くと SoT が二重化する）。
#
# 意図的に収集対象外にするファイルは COVERAGE_EXEMPT に理由付きで足すこと。
# ガードごと消すのではなく、例外を 1 行ずつ明示的に増やす方向で運用する。

TEST_NAME_RE='(^|/)(test[-_][^/]*|[^/]*[-_]test)\.sh$'

# repo root 相対パスを 1 行 1 件で記載する。空が正常。
COVERAGE_EXEMPT=()

coverage_status="ok"

_coverage_contains() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

check_test_collection_coverage() {
  local rel item
  local -a candidates=() collected_rel=() uncollected=() stale_exempt=()

  if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    coverage_status="skipped"
    echo ""
    echo "WARN: git リポジトリとして読めないため収集漏れ検出をスキップしました"
    echo "      （テスト収集のカバレッジは未検証です）"
    return 0
  fi

  # git ls-files のパスも collected_rel も REPO_ROOT 相対で揃える
  # （このランナーは tests/ 直下にあり REPO_ROOT = git top-level が前提）
  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    candidates+=("$rel")
  done < <(git -C "$REPO_ROOT" ls-files --full-name | grep -E "$TEST_NAME_RE" || true)

  for item in ${TEST_FILES[@]+"${TEST_FILES[@]}"}; do
    collected_rel+=("${item#"$REPO_ROOT"/}")
  done

  for rel in ${candidates[@]+"${candidates[@]}"}; do
    _coverage_contains "$rel" ${collected_rel[@]+"${collected_rel[@]}"} && continue
    _coverage_contains "$rel" ${COVERAGE_EXEMPT[@]+"${COVERAGE_EXEMPT[@]}"} && continue
    uncollected+=("$rel")
  done

  # 除外指定の腐敗検出: 収集済み / 候補ですらないエントリが残っていたら知らせる
  for rel in ${COVERAGE_EXEMPT[@]+"${COVERAGE_EXEMPT[@]}"}; do
    if _coverage_contains "$rel" ${collected_rel[@]+"${collected_rel[@]}"}; then
      stale_exempt+=("$rel (収集されているので除外指定は不要)")
    elif ! _coverage_contains "$rel" ${candidates[@]+"${candidates[@]}"}; then
      stale_exempt+=("$rel (テスト候補として存在しない)")
    fi
  done

  if [[ ${#uncollected[@]} -gt 0 || ${#stale_exempt[@]} -gt 0 ]]; then
    coverage_status="failed"
    echo ""
    echo "ERROR: テスト収集漏れ検出ガードが失敗しました (Issue #787)"
    if [[ ${#uncollected[@]} -gt 0 ]]; then
      echo "  テストに見えるが tests/run-all.sh が実行していないファイル:"
      for rel in "${uncollected[@]}"; do
        echo "    - $rel"
      done
      echo "  対処: tests/ 配下へ移設して収集対象に入れるか、テストでないなら"
      echo "        test に見えない名前へ変更するか、COVERAGE_EXEMPT に理由付きで追加する。"
    fi
    if [[ ${#stale_exempt[@]} -gt 0 ]]; then
      echo "  COVERAGE_EXEMPT の陳腐化したエントリ:"
      for rel in "${stale_exempt[@]}"; do
        echo "    - $rel"
      done
      echo "  対処: 不要になったエントリを削除する。"
    fi
    ((total_errors++))
  fi
}

echo "収集したテスト: ${#TEST_FILES[@]} 本"
check_test_collection_coverage

# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
for test_file in ${TEST_FILES[@]+"${TEST_FILES[@]}"}; do
  echo ""
  echo "=== $(basename "$test_file") ==="
  output=$(bash "$test_file" 2>&1)
  file_exit=$?
  echo "$output"

  # Extract pass/fail counts from "# N passed, M failed" line
  summary_line=$(echo "$output" | grep '# .* passed, .* failed' || true)

  if [[ -z "$summary_line" ]]; then
    # No summary line: script crashed or had a syntax error
    echo "ERROR: $(basename "$test_file") did not produce a summary line (exit code: $file_exit)"
    ((total_errors++))
    continue
  fi

  pass=$(echo "$summary_line" | sed -n 's/^# \([0-9]*\) passed.*/\1/p')
  fail=$(echo "$summary_line" | sed -n 's/.*passed, \([0-9]*\) failed.*/\1/p')
  pass=${pass:-0}
  fail=${fail:-0}

  # Also treat non-zero exit with 0 reported failures as an error
  if [[ $file_exit -ne 0 && $fail -eq 0 ]]; then
    echo "ERROR: $(basename "$test_file") exited with code $file_exit but reported 0 failures"
    ((total_errors++))
  fi

  total_pass=$((total_pass + pass))
  total_fail=$((total_fail + fail))
done

total_tests=$((total_pass + total_fail))
echo ""
echo "==============================="
echo "Total: $total_tests tests, $total_pass passed, $total_fail failed, $total_errors errors"
case "$coverage_status" in
  ok)      echo "収集漏れ検出: OK (${#TEST_FILES[@]} 本を収集)" ;;
  failed)  echo "収集漏れ検出: FAILED (上の ERROR を参照)" ;;
  skipped) echo "収集漏れ検出: SKIPPED (git 不可のため未検証)" ;;
esac
echo "==============================="

[[ $total_fail -eq 0 && $total_errors -eq 0 ]]
