#!/usr/bin/env bash
# block-dangerous-git.sh のテスト（Issue #470）
# 実行: bash tests/test-block-dangerous-git.sh
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
#   - git stash の変更系（bare / push / save / pop / apply / branch / drop /
#     clear / store / create / 未知トークン）→ deny、調査用の
#     read-only（list / show）→ allow（Issue #880）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-dangerous-git.sh"
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

run_test "git push --force-with-lease origin refs/tags/v1.0 (tag namespace)" \
  "$(mk_bash_json "git push --force-with-lease origin refs/tags/v1.0")" 2

run_test "git push --force-with-lease origin tag v1.0 (tag keyword)" \
  "$(mk_bash_json "git push --force-with-lease origin tag v1.0")" 2

run_test "git push --force-with-lease origin refs/notes/commits (notes namespace)" \
  "$(mk_bash_json "git push --force-with-lease origin refs/notes/commits")" 2

run_test "git push --force-with-lease origin refs/replace/abc (replace namespace)" \
  "$(mk_bash_json "git push --force-with-lease origin refs/replace/abc")" 2

run_test "git push --force-with-lease origin HEAD:refs/tags/v1.0 (dst=tag namespace)" \
  "$(mk_bash_json "git push --force-with-lease origin HEAD:refs/tags/v1.0")" 2

run_test "git push --force-with-lease origin feat/foo:refs/tags/v1.0 (cross-ns to tag)" \
  "$(mk_bash_json "git push --force-with-lease origin feat/foo:refs/tags/v1.0")" 2

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

# =====================================================================
# [git stash の変更系は deny（Issue #880）]
# allowlist 方式なので、read-only の list / show 以外は未知トークンも既定 deny。
# =====================================================================
echo "[git stash の変更系は deny（Issue #880）]"

run_test "git stash (bare, push 相当)" \
  "$(mk_bash_json "git stash")" 2

run_test "git stash -u (Issue #880 の事故の起点)" \
  "$(mk_bash_json "git stash -u")" 2

run_test "git stash --include-untracked (-u の長形式)" \
  "$(mk_bash_json "git stash --include-untracked")" 2

run_test "git stash push" \
  "$(mk_bash_json "git stash push")" 2

run_test "git stash push -u -m wip (フラグ後続でも deny)" \
  "$(mk_bash_json "git stash push -u -m wip")" 2

run_test "git stash save wip (deprecated だが有効な変更系)" \
  "$(mk_bash_json "git stash save wip")" 2

run_test "git stash pop (Issue #880 の事故の第 2 段)" \
  "$(mk_bash_json "git stash pop")" 2

run_test "git stash apply" \
  "$(mk_bash_json "git stash apply")" 2

run_test "git stash apply 'stash@{1}' (stash ref 引数付き)" \
  "$(mk_bash_json "git stash apply 'stash@{1}'")" 2

run_test "git stash branch tmp" \
  "$(mk_bash_json "git stash branch tmp")" 2

run_test "git stash drop" \
  "$(mk_bash_json "git stash drop")" 2

run_test "git stash clear" \
  "$(mk_bash_json "git stash clear")" 2

run_test "git stash store deadbeef (refs/stash を直接書く低レベル形)" \
  "$(mk_bash_json "git stash store deadbeef")" 2

run_test "git stash create (allowlist 外の既定 deny)" \
  "$(mk_bash_json "git stash create")" 2

run_test "git stash --help (allowlist 外は option 形でも安全側 deny)" \
  "$(mk_bash_json "git stash --help")" 2

run_test "git stash -h (--help の短形式)" \
  "$(mk_bash_json "git stash -h")" 2

run_test "git -C /tmp/repo stash pop (値取り global option 介在形)" \
  "$(mk_bash_json "git -C /tmp/repo stash pop")" 2

run_test "git -C \"C:/Program Files/repo\" stash pop (空白入りパスで割れても到達)" \
  "$(mk_bash_json 'git -C "C:/Program Files/repo" stash pop')" 2

run_test "git --git-dir=/tmp/r/.git stash pop (attached 値の global option)" \
  "$(mk_bash_json "git --git-dir=/tmp/r/.git stash pop")" 2

run_test "git -c core.pager=cat stash pop (-c 介在形)" \
  "$(mk_bash_json "git -c core.pager=cat stash pop")" 2

run_test "/usr/bin/git stash pop (絶対パス起動)" \
  "$(mk_bash_json "/usr/bin/git stash pop")" 2

run_test "git status && git stash pop (複合コマンド &&)" \
  "$(mk_bash_json "git status && git stash pop")" 2

run_test "git status ; git stash drop (複合コマンド ;)" \
  "$(mk_bash_json "git status ; git stash drop")" 2

run_test "git stash list && git stash pop (allow と deny の混在)" \
  "$(mk_bash_json "git stash list && git stash pop")" 2

run_test "git stash push tools/org_extension_schema.json (旧 doc の案内手順)" \
  "$(mk_bash_json "git stash push tools/org_extension_schema.json")" 2

run_test "git stash \$VAR (未展開の変数 → allowlist 外 deny)" \
  "$(mk_bash_json 'git stash $VAR')" 2

# Git for Windows の実行形式スペリング。brief に Windows 環境の節がある以上、
# ここが抜けると同じ事故が hook 素通りで再発する。
run_test "git.exe stash pop (Git for Windows のスペリング)" \
  "$(mk_bash_json "git.exe stash pop")" 2

run_test "git.exe stash drop" \
  "$(mk_bash_json "git.exe stash drop")" 2

run_test "GIT.EXE stash pop (Windows は実行形式名が大小無差別)" \
  "$(mk_bash_json "GIT.EXE stash pop")" 2

run_test "/usr/bin/git.exe stash pop (絶対パス + .exe)" \
  "$(mk_bash_json "/usr/bin/git.exe stash pop")" 2

run_test "C:/Git/bin/git.exe stash pop (Windows 絶対パス)" \
  "$(mk_bash_json "C:/Git/bin/git.exe stash pop")" 2

run_test "echo safe && git.exe stash pop (複合コマンド + .exe)" \
  "$(mk_bash_json "echo safe && git.exe stash pop")" 2

# 値を取る global option の値に空白が入ると、split_segments が引用符を空白へ
# 正規化した後で値が複数トークンへ割れる。その断片が既知サブコマンド名だと
# 走査が本物の stash より手前で打ち切られ deny をすり抜けていた（Codex round 1）。
run_test "git -C \"/tmp/my status repo\" stash pop (値に status を含む -C)" \
  "$(mk_bash_json 'git -C "/tmp/my status repo" stash pop')" 2

run_test "git -C \"/tmp/a show b\" stash clear (値に show を含む -C)" \
  "$(mk_bash_json 'git -C "/tmp/a show b" stash clear')" 2

run_test "git --git-dir \"/tmp/a status repo/.git\" stash drop (detached --git-dir)" \
  "$(mk_bash_json 'git --git-dir "/tmp/a status repo/.git" stash drop')" 2

run_test "git --git-dir=\"/tmp/a status repo/.git\" stash drop (attached --git-dir)" \
  "$(mk_bash_json 'git --git-dir="/tmp/a status repo/.git" stash drop')" 2

run_test "git --work-tree \"/tmp/a commit b\" stash apply (値に commit を含む)" \
  "$(mk_bash_json 'git --work-tree "/tmp/a commit b" stash apply')" 2

run_test "git -C \"/tmp/my status repo\" stash list (割れた値でも read-only は allow)" \
  "$(mk_bash_json 'git -C "/tmp/my status repo" stash list')" 0

run_test "git -C \"/tmp/my status repo\" status (stash 不在なら allow)" \
  "$(mk_bash_json 'git -C "/tmp/my status repo" status')" 0

# インライン alias 定義は alias 本体がコマンド文字列に載るので静的に拾える
# （Codex round 2）。config に定義済みの alias 経由は原理的に検出できないため
# 本フックの残存ギャップとしてファイル冒頭に明記してある。
run_test "git -c alias.s=stash s pop (インライン alias 定義)" \
  "$(mk_bash_json 'git -c alias.s=stash s pop')" 2

run_test "git -c alias.s=stash s drop (インライン alias 定義)" \
  "$(mk_bash_json 'git -c alias.s=stash s drop')" 2

run_test "git -c alias.st=\"stash pop\" st (値に空白入り alias)" \
  "$(mk_bash_json 'git -c alias.st="stash pop" st')" 2

run_test "git -c alias.lg=log lg (stash を含まない alias は allow)" \
  "$(mk_bash_json 'git -c alias.lg=log lg')" 0

run_test "git config --get alias.st (alias 定義の参照は allow)" \
  "$(mk_bash_json 'git config --get alias.st')" 0

# 既知の false positive を挙動として固定しておく（黙って変わらないように）。
# 値を取る global option があると値の分割を判別できないため permissive 走査へ
# 落ちる。その結果、引数に stash というリテラル語を置いた検索系まで deny になる。
# 安全側に倒した意図的な選択で、根治には引用符境界を保つトークナイズが要る
# （現状は split_segments が引用符を空白へ正規化した後の文字列しか見えない）。
# 回避策は deny メッセージが案内するとおり git トークンを外すこと（rg / grep）。
run_test "git -C /repo grep stash (既知の false positive: 安全側 deny)" \
  "$(mk_bash_json 'git -C /repo grep stash')" 2

run_test "git -c color.ui=false grep stash (同上)" \
  "$(mk_bash_json 'git -c color.ui=false grep stash')" 2

run_test "git grep stash -- src/ (global option 無しなら allow のまま)" \
  "$(mk_bash_json 'git grep stash -- src/')" 0

echo ""

# =====================================================================
# [git stash の read-only と stash リテラル語の false positive guard]
# =====================================================================
echo "[git stash の read-only allow / リテラル語の false positive guard]"

run_test "git stash list (調査用 read-only)" \
  "$(mk_bash_json "git stash list")" 0

run_test "git stash list --date=iso (list + フラグ)" \
  "$(mk_bash_json "git stash list --date=iso")" 0

run_test "git stash show (調査用 read-only)" \
  "$(mk_bash_json "git stash show")" 0

run_test "git stash show -p 'stash@{0}' (show + フラグ + stash ref)" \
  "$(mk_bash_json "git stash show -p 'stash@{0}'")" 0

run_test "git -C /tmp/repo stash list (介在形でも allow は通る)" \
  "$(mk_bash_json "git -C /tmp/repo stash list")" 0

run_test "git help stash (--help の代替ドキュメント経路)" \
  "$(mk_bash_json "git help stash")" 0

run_test "git commit -m \"stash\" (メッセージ中のリテラル語)" \
  "$(mk_bash_json 'git commit -m "stash"')" 0

run_test "git commit -m \"add stash guard\" (本 PR 自身の commit message 形)" \
  "$(mk_bash_json 'git commit -m "add stash guard"')" 0

run_test "git grep stash -- src/ (別サブコマンドの引数)" \
  "$(mk_bash_json "git grep stash -- src/")" 0

run_test "git log --grep=stash (attached 値に stash)" \
  "$(mk_bash_json "git log --grep=stash")" 0

run_test "git branch feat/stash-guard (branch 名に stash)" \
  "$(mk_bash_json "git branch feat/stash-guard")" 0

run_test "git stashx pop (トークン完全一致でないので allow)" \
  "$(mk_bash_json "git stashx pop")" 0

echo ""
echo "# $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
