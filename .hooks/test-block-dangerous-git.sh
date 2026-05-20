#!/usr/bin/env bash
# block-dangerous-git.sh のテスト（Issue #470）
# 実行: bash .hooks/test-block-dangerous-git.sh
#
# 主な確認観点:
#   - 素の git push --force / -f / バンドル短オプション → deny
#   - protected branch (main / develop / release/* / production) への
#     --force-with-lease → deny
#   - 非保護 branch への --force-with-lease → allow
#   - refspec 未指定など ambiguous な --force-with-lease → 安全側 deny
#   - その他の既存破壊的コマンド（reset --hard / branch -D / clean -f /
#     checkout -- . / restore --source / tag -d / update-ref -d /
#     reflog expire --all）が引き続き deny されること

set -euo pipefail

HOOK=".hooks/block-dangerous-git.sh"
PASS=0
FAIL=0

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

mk_bash_json() {
  # JSON エスケープを jq に任せる（バックスラッシュ・引用符を安全に扱う）
  local cmd="$1"
  jq -n --arg cmd "$cmd" '{"tool_name":"Bash","tool_input":{"command":$cmd}}'
}

echo "=== block-dangerous-git.sh tests ==="
echo ""

# =====================================================================
# [素の force は無条件 deny]
# =====================================================================
echo "[素の --force / -f は protected/非保護を問わず deny]"

run_test "git push --force origin feat/foo (素 force, 非保護でも deny)" \
  "$(mk_bash_json "git push --force origin feat/foo")" 2

run_test "git push --force origin main (素 force, protected)" \
  "$(mk_bash_json "git push --force origin main")" 2

run_test "git push -f origin feat/foo (短縮 force)" \
  "$(mk_bash_json "git push -f origin feat/foo")" 2

run_test "git push -uf origin feat/foo (バンドル短オプション f 含む)" \
  "$(mk_bash_json "git push -uf origin feat/foo")" 2

run_test "git push --force (refspec 無し)" \
  "$(mk_bash_json "git push --force")" 2

echo ""

# =====================================================================
# [protected branch への --force-with-lease は deny]
# =====================================================================
echo "[protected branch (main/develop/release/*/production) への --force-with-lease は deny]"

run_test "git push --force-with-lease origin main" \
  "$(mk_bash_json "git push --force-with-lease origin main")" 2

run_test "git push --force-with-lease origin develop" \
  "$(mk_bash_json "git push --force-with-lease origin develop")" 2

run_test "git push --force-with-lease origin production" \
  "$(mk_bash_json "git push --force-with-lease origin production")" 2

run_test "git push --force-with-lease origin release/v1.0" \
  "$(mk_bash_json "git push --force-with-lease origin release/v1.0")" 2

run_test "git push --force-with-lease origin release/2026-05" \
  "$(mk_bash_json "git push --force-with-lease origin release/2026-05")" 2

run_test "git push --force-with-lease origin master (alias of main)" \
  "$(mk_bash_json "git push --force-with-lease origin master")" 2

run_test "git push --force-with-lease origin HEAD:master (refspec)" \
  "$(mk_bash_json "git push --force-with-lease origin HEAD:master")" 2

run_test "git push --force-with-lease origin refs/heads/master (full ref)" \
  "$(mk_bash_json "git push --force-with-lease origin refs/heads/master")" 2

run_test "git push --force-with-lease origin feat/foo:master (cross-name master)" \
  "$(mk_bash_json "git push --force-with-lease origin feat/foo:master")" 2

# 引用符は split_segments で空白へ正規化される（lib/segment-split.sh 内部実装）
# ため、quoted refspec はクオート無しと同じ判定経路を通る。protected 名は
# 引用符の有無に関わらず deny できることを回帰確認する。
run_test "git push --force-with-lease origin \"main\" (quoted protected → deny via 正規化後の name 一致)" \
  "$(mk_bash_json 'git push --force-with-lease origin "main"')" 2

run_test "git push --force-with-lease origin \$BRANCH (未展開の変数 → ambiguous deny)" \
  "$(mk_bash_json 'git push --force-with-lease origin $BRANCH')" 2

run_test "git push --force-with-lease origin HEAD:main (refspec)" \
  "$(mk_bash_json "git push --force-with-lease origin HEAD:main")" 2

run_test "git push --force-with-lease origin feat/foo:main (cross-name refspec)" \
  "$(mk_bash_json "git push --force-with-lease origin feat/foo:main")" 2

run_test "git push --force-with-lease origin refs/heads/main (full ref)" \
  "$(mk_bash_json "git push --force-with-lease origin refs/heads/main")" 2

run_test "git push --force-with-lease origin feat/foo main (複数 refspec, main 含む)" \
  "$(mk_bash_json "git push --force-with-lease origin feat/foo main")" 2

run_test "git push --force-with-lease=feat/foo origin main (--force-with-lease=<ref> with main destination)" \
  "$(mk_bash_json "git push --force-with-lease=feat/foo origin main")" 2

echo ""

# =====================================================================
# [非保護 branch への --force-with-lease は allow]
# =====================================================================
echo "[非保護 branch への --force-with-lease は allow]"

run_test "git push --force-with-lease origin feat/foo" \
  "$(mk_bash_json "git push --force-with-lease origin feat/foo")" 0

run_test "git push --force-with-lease origin feat/issue-470" \
  "$(mk_bash_json "git push --force-with-lease origin feat/issue-470")" 0

run_test "git push --force-with-lease origin fix/bug-123" \
  "$(mk_bash_json "git push --force-with-lease origin fix/bug-123")" 0

run_test "git push --force-with-lease origin HEAD:feat/foo (refspec で非保護)" \
  "$(mk_bash_json "git push --force-with-lease origin HEAD:feat/foo")" 0

run_test "git push --force-with-lease=feat/foo origin feat/foo (--force-with-lease=<ref>)" \
  "$(mk_bash_json "git push --force-with-lease=feat/foo origin feat/foo")" 0

run_test "git push --force-with-lease origin feat/foo feat/bar (複数 refspec, 全て非保護)" \
  "$(mk_bash_json "git push --force-with-lease origin feat/foo feat/bar")" 0

run_test "git -C /some/repo push --force-with-lease origin feat/foo" \
  "$(mk_bash_json "git -C /some/repo push --force-with-lease origin feat/foo")" 0

echo ""

# =====================================================================
# [ambiguous（refspec 未指定）の --force-with-lease は安全側 deny]
# =====================================================================
echo "[ambiguous な --force-with-lease は安全側 deny]"

run_test "git push --force-with-lease (引数無し)" \
  "$(mk_bash_json "git push --force-with-lease")" 2

run_test "git push --force-with-lease origin (remote のみ, refspec 無し)" \
  "$(mk_bash_json "git push --force-with-lease origin")" 2

run_test "git push --force-with-lease origin HEAD (current branch 依存)" \
  "$(mk_bash_json "git push --force-with-lease origin HEAD")" 2

run_test "git push --force-with-lease origin @ (HEAD alias)" \
  "$(mk_bash_json "git push --force-with-lease origin @")" 2

run_test "git push --force-with-lease origin : (matching push 全件)" \
  "$(mk_bash_json "git push --force-with-lease origin :")" 2

run_test "git push --force-with-lease origin refs/heads/*:refs/heads/* (wildcard refspec)" \
  "$(mk_bash_json "git push --force-with-lease origin refs/heads/*:refs/heads/*")" 2

run_test "git push --force-with-lease --all origin (--all flag)" \
  "$(mk_bash_json "git push --force-with-lease --all origin")" 2

run_test "git push --force-with-lease --mirror origin" \
  "$(mk_bash_json "git push --force-with-lease --mirror origin")" 2

run_test "git push --force-with-lease --tags origin" \
  "$(mk_bash_json "git push --force-with-lease --tags origin")" 2

run_test "git push --force-with-lease origin HEAD:refs/heads/* (wildcard 部分含む)" \
  "$(mk_bash_json "git push --force-with-lease origin HEAD:refs/heads/*")" 2

echo ""

# =====================================================================
# [既存の破壊的コマンドは引き続き deny（regression check）]
# =====================================================================
echo "[既存の破壊的コマンド deny の回帰確認]"

run_test "git reset --hard HEAD" \
  "$(mk_bash_json "git reset --hard HEAD")" 2

run_test "git branch -D feat/foo" \
  "$(mk_bash_json "git branch -D feat/foo")" 2

run_test "git branch --delete --force feat/foo" \
  "$(mk_bash_json "git branch --delete --force feat/foo")" 2

run_test "git clean -fd" \
  "$(mk_bash_json "git clean -fd")" 2

run_test "git clean --force" \
  "$(mk_bash_json "git clean --force")" 2

run_test "git checkout -- ." \
  "$(mk_bash_json "git checkout -- .")" 2

run_test "git restore --source=HEAD~1 src/foo" \
  "$(mk_bash_json "git restore --source=HEAD~1 src/foo")" 2

run_test "git tag -d v1.0" \
  "$(mk_bash_json "git tag -d v1.0")" 2

run_test "git update-ref -d refs/heads/foo" \
  "$(mk_bash_json "git update-ref -d refs/heads/foo")" 2

run_test "git reflog expire --all" \
  "$(mk_bash_json "git reflog expire --all")" 2

echo ""

# =====================================================================
# [良性コマンドは allow]
# =====================================================================
echo "[良性コマンドは allow]"

run_test "git push origin feat/foo (force 無し)" \
  "$(mk_bash_json "git push origin feat/foo")" 0

run_test "git push origin main (force 無し protected でも push 自体は OK)" \
  "$(mk_bash_json "git push origin main")" 0

run_test "git push (引数無し, force 無し)" \
  "$(mk_bash_json "git push")" 0

run_test "git status" \
  "$(mk_bash_json "git status")" 0

run_test "git diff" \
  "$(mk_bash_json "git diff")" 0

run_test "git restore --staged src/foo (staged 単独は安全)" \
  "$(mk_bash_json "git restore --staged src/foo")" 0

run_test "git checkout feat/foo (-- 無しの branch 切替)" \
  "$(mk_bash_json "git checkout feat/foo")" 0

run_test "git reset HEAD~1 (--hard 無し)" \
  "$(mk_bash_json "git reset HEAD~1")" 0

run_test "git branch -d feat/foo (-D ではない小文字 -d)" \
  "$(mk_bash_json "git branch -d feat/foo")" 0

run_test "Edit ツール (Bash ではない)" \
  '{"tool_name":"Edit","tool_input":{"file_path":"src/foo.txt"}}' 0

run_test "空コマンド" \
  '{"tool_name":"Bash","tool_input":{"command":""}}' 0

echo ""

# =====================================================================
# [複合コマンド / セグメント分割の確認]
# =====================================================================
echo "[複合コマンド / セグメント分割]"

run_test "echo --force ; git push origin feat/foo (別セグメントの --force は無視)" \
  "$(mk_bash_json "echo --force ; git push origin feat/foo")" 0

run_test "git status && git push --force origin main (後続セグメントの素 force)" \
  "$(mk_bash_json "git status && git push --force origin main")" 2

run_test "git status && git push --force-with-lease origin feat/foo (後続セグメントの with-lease 非保護)" \
  "$(mk_bash_json "git status && git push --force-with-lease origin feat/foo")" 0

run_test "git status && git push --force-with-lease origin main (後続セグメントの with-lease protected)" \
  "$(mk_bash_json "git status && git push --force-with-lease origin main")" 2

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
