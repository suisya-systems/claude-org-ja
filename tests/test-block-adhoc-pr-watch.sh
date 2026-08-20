#!/usr/bin/env bash
# Tests for block-adhoc-pr-watch.sh
# Validates: exit code (0=allow/passthrough, 2=block) and stderr messages.
#
# 確認観点 (2026-08-20 の PR #51 実害への機械ガード):
#   - Monitor + gh pr checks (ループの有無問わず)   -> block (Monitor は定義上「監視」)
#   - Bash + while/until/for + gh pr checks         -> block (polling ループ)
#   - Bash + gh pr checks --watch                   -> block (張り付き監視)
#   - Bash/Monitor + tools/pr-watch.* の直接起動     -> block (孤児化する ad-hoc 監視)
#   - Bash + 単発 gh pr checks <n>                  -> allow (状態確認は監視でない)
#   - Bash + gh pr checks | grep -w (=--web でない) -> allow (-w を watch と誤認しない)
#   - Bash + grep/cat の引数に pr-watch.sh          -> allow (読み取りは起動でない)
#   - 非対象ツール / command 欠落 (Monitor ws)       -> passthrough
#   - 不正 JSON / 空 stdin                          -> block (fail-closed)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-adhoc-pr-watch.sh"

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

run_hook() {
  local json="$1" stderr_file="$2"
  local exit_code=0
  printf '%s' "$json" | bash "$HOOK" 2>"$stderr_file" || exit_code=$?
  echo "$exit_code"
}

# jq で command を安全に JSON エンコードして payload を作る (ループ構文等の
# クォート事故を避ける)
make_payload() {
  local tool="$1" cmd="$2"
  jq -cn --arg tool "$tool" --arg cmd "$cmd" '{tool_name: $tool, tool_input: {command: $cmd}}'
}

# --- Block Cases ---

# 1. Monitor + while + gh pr checks polling ループ (実害と同形) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Monitor" 'while true; do gh pr checks 51 --json name,bucket; sleep 30; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Monitor: while + gh pr checks polling loop is blocked"
assert_stderr_contains "/pr-watch-pane" "$stderr" "deny stderr names the canonical path /pr-watch-pane"
assert_stderr_contains "人間に報告して指示を仰いでください" "$stderr" "deny stderr instructs to consult the human"

# 2. Monitor + gh pr checks (ループなしでも Monitor は監視) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Monitor" 'gh pr checks 51 --json name,bucket')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Monitor: gh pr checks without loop is still blocked (Monitor is watching by definition)"

# 3. Bash + until ループ + gh pr checks -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'until gh pr checks 51 | grep -q pass; do sleep 60; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: until + gh pr checks polling loop is blocked"

# 4. Bash + gh pr checks --watch -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr checks 51 --watch')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: gh pr checks --watch is blocked"

# 5. Bash + tools/pr-watch.sh 直接起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'setsid bash tools/pr-watch.sh 51 < /dev/null > /dev/null 2>&1 &')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: setsid bash tools/pr-watch.sh direct launch is blocked"
assert_stderr_contains "tools/pr-watch.* の直接起動は禁止" "$stderr" "deny stderr names the pr-watch direct-launch ban"

# 6. Bash + 絶対パスの pr_watch.py 起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'python3 /home/user/repo/tools/pr_watch.py --pr 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: python3 .../tools/pr_watch.py launch is blocked"

# 7. Monitor + pr-watch.sh 起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Monitor" './tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Monitor: ./tools/pr-watch.sh launch is blocked"

# --- Block Cases (Codex round 1 指摘: gh 親コマンドフラグ / インタプリタ形の取りこぼし) ---

# 7b. Bash + while + gh pr --repo owner/repo checks (pr と checks の間にフラグ) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while true; do gh pr --repo owner/repo checks 51; sleep 30; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: while + gh pr --repo owner/repo checks is blocked"

# 7c. Monitor + gh pr -R owner/repo checks -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Monitor" 'gh pr -R owner/repo checks 51 --json name,bucket')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Monitor: gh pr -R owner/repo checks is blocked"

# 7d. Bash + py -3 tools/pr_watch.py (Windows launcher 形) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'py -3 tools/pr_watch.py --pr 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: py -3 tools/pr_watch.py launch is blocked"

# 7e. Bash + 絶対パスのインタプリタ -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" '/usr/bin/bash tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: /usr/bin/bash tools/pr-watch.sh launch is blocked"

# 7f. Bash + 環境変数代入前置 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'env ORG_TRANSPORT=broker bash tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: env VAR=... bash tools/pr-watch.sh launch is blocked"

# 7g. Bash + python3 -u tools/pr_watch.py (インタプリタフラグ付き) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'python3 -u tools/pr_watch.py --pr 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: python3 -u tools/pr_watch.py launch is blocked"

# --- Block Cases (Codex round 2 指摘: path 付き watch / 未網羅ラッパー / module 実行) ---

# 7h. Bash + /usr/bin/watch -n 30 gh pr checks -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" '/usr/bin/watch -n 30 gh pr checks 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: path-qualified /usr/bin/watch + gh pr checks is blocked"

# 7i. Bash + timeout ラッパー経由の pr-watch.sh -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'timeout 1h bash tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: timeout 1h bash tools/pr-watch.sh launch is blocked"

# 7j. Bash + source での取り込み実行 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'source tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: source tools/pr-watch.sh is blocked"

# 7k. Bash + python module 実行 (python3 -m tools.pr_watch) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'python3 -m tools.pr_watch --pr 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: python3 -m tools.pr_watch module launch is blocked"

# --- Block Cases (Codex round 3 指摘: 複数行 / 予約語境界 / gh global フラグ) ---

# 7m. Bash + 複数行の while ループ (loop 構文と gh が別行) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while true; do
  gh pr checks 51
  sleep 30
done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: multiline while + gh pr checks polling loop is blocked"

# 7n. Bash + if/then 内の pr-watch.sh 起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'if true; then bash tools/pr-watch.sh 51; fi')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: pr-watch.sh launch after 'then' is blocked"

# 7o. Bash + while/do 内の pr-watch.sh 起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while true; do bash tools/pr-watch.sh 51; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: pr-watch.sh launch after 'do' is blocked"

# 7p. Bash + gh global フラグ (-R) が pr の前に来る形 + --watch -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh -R owner/repo pr checks 51 --watch')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: gh -R owner/repo pr checks --watch is blocked"

# 7q. Monitor + gh -R owner/repo pr checks -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Monitor" 'gh -R owner/repo pr checks 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Monitor: gh -R owner/repo pr checks is blocked"

# --- Block Cases (Codex round 4 指摘: 条件位置 / case 腕 / 読み取り例外の越境) ---

# 7t. Bash + if 条件位置での pr-watch.sh 起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'if bash tools/pr-watch.sh 51; then echo ok; fi')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: pr-watch.sh launch as if-condition is blocked"

# 7u. Bash + case 腕 (`)` の直後) での pr-watch.sh 起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'case $x in y) bash tools/pr-watch.sh 51;; esac')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: pr-watch.sh launch in case arm is blocked"

# 7v. Bash + 読み取りと起動の複合 (読み取り例外が起動区間へ越境しない) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'cat tools/pr-watch.sh && bash tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: cat then launch compound is still blocked (reader exemption does not leak)"

# --- Block Cases (Codex round 5 指摘: ネストループ / 継続行 / dot-source) ---

# 7aa. Bash + ネストしたループ (内側 done の後に gh pr checks、外側 done で反復) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while true; do for x in a; do :; done; gh pr checks 51; sleep 30; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: nested loop with gh pr checks after inner done is blocked"

# 7ab. Bash + バックスラッシュ継続行入りの polling ループ -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while true; do gh pr \
checks 51; sleep 30; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: polling loop with backslash-newline continuation is blocked"

# 7ac. Bash + POSIX dot-source での pr-watch.sh 実行 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" '. tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: POSIX dot-source of tools/pr-watch.sh is blocked"

# --- Block Cases (Codex round 6 指摘: 密着リダイレクト / 引用付き代入 / Windows パス) ---

# 7ae. Bash + ファイル名直後の密着リダイレクト -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'bash tools/pr-watch.sh>/tmp/watch.log 51 &')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: pr-watch.sh launch with attached redirection is blocked"

# 7af. Bash + 空白入り引用値の環境変数代入前置 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "NOTE='CI watch' bash tools/pr-watch.sh 51")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: quoted env assignment prefix launch is blocked"

# 7ag. Bash + Windows パス区切りの pr-watch.ps1 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "pwsh '.\\tools\\pr-watch.ps1' 51")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: pwsh with backslash-separated pr-watch.ps1 path is blocked"

# --- Block Cases (Codex round 7 指摘: interpreter heredoc / bash -c 引用スクリプト) ---

# 7ai. Bash + シェルへの heredoc stdin (本文はコード) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "bash <<'EOF'
while true; do gh pr checks 51; sleep 30; done
EOF")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: heredoc piped into bash (executable body) is blocked"

# 7aj. Bash + bash -c の引用スクリプト内の polling ループ -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "bash -c 'while true; do gh pr checks 51; sleep 30; done'")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: bash -c quoted polling loop is blocked"

# 7ak. Bash + bash -c の引用スクリプト内の --watch -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "bash -c 'gh pr checks 51 --watch'")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: bash -c quoted gh pr checks --watch is blocked"

# --- Block Cases (Codex round 8 指摘: 関数間接 / 改行区切り予約語 / pipe heredoc / sudo -u) ---

# 7ao. Bash + 関数に包んだ gh pr checks をループから呼ぶ -> block (保守的)
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'poll_ci() { gh pr checks 51; }; while true; do poll_ci; sleep 30; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: function-wrapped gh pr checks called from loop is blocked"

# 7ap. Bash + 予約語が独立行のループ -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'until
gh pr checks 51 | grep -q pass
do sleep 30; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: loop with reserved word on its own line is blocked"

# 7aq. Bash + cat heredoc を bash に pipe (本文はコード) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "cat <<'EOF' | bash
while true; do gh pr checks 51; sleep 30; done
EOF")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: heredoc piped to bash (executable body) is blocked"

# 7ar. Bash + sudo -u git (reader 語がユーザー名) 経由の起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'sudo -u git bash tools/pr-watch.sh 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: sudo -u git bash tools/pr-watch.sh is blocked (git is a username, not a reader)"

# --- Allow Cases (Codex round 7 指摘: 兄弟ループ / 非実行インタプリタモード) ---

# 7al. Bash + 兄弟ループに挟まれた単発 gh pr checks -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'for x in a; do :; done; gh pr checks 51; for y in b; do :; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: one-shot gh pr checks between sibling loops is allowed"

# 7am. Bash + bash -n の構文チェック -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'bash -n tools/pr-watch.sh')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: bash -n syntax check of pr-watch.sh is allowed"

# 7an. Bash + py_compile のバイトコンパイル -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'python3 -m py_compile tools/pr_watch.py')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: python3 -m py_compile of pr_watch.py is allowed"

# --- Allow Cases (Codex round 6 指摘: heredoc 本文はデータ) ---

# 7ah. Bash + heredoc 本文に polling ループのテキストを書くドキュメント生成 -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "cat > doc.md <<'EOF'
while true; do gh pr checks 51; sleep 30; done
EOF")
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: heredoc body containing polling-loop text is allowed (data, not code)"

# --- Allow Cases (Codex round 5 指摘: 引用内の区切り文字はデータ) ---

# 7ad. Bash + 引用文字列の中に `; bash tools/pr-watch.sh` を含む echo -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "echo 'x; bash tools/pr-watch.sh 51'")
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: quoted separator inside echo data is not treated as a launch"

# --- Allow Cases (Codex round 4 指摘: 閉じたループ後の単発 / 無関係 --watch / ラッパー越し読み取り) ---

# 7w. Bash + 閉じたループの後の単発 gh pr checks -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'for f in a b; do echo "$f"; done; gh pr checks 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: one-shot gh pr checks after a closed loop is allowed"

# 7x. Bash + 単発 gh pr checks の後に別コマンドの --watch -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr checks 51; other-tool --watch x')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: unrelated other-tool --watch after one-shot gh pr checks is allowed"

# 7y. Bash + env 越しの grep 読み取り -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'env LC_ALL=C grep -n ci_completed tools/pr-watch.sh')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: env-wrapped grep read of pr-watch.sh is allowed"

# 7z. Bash + timeout 越しの cat 読み取り -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'timeout 1s cat tools/pr-watch.sh')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: timeout-wrapped cat read of pr-watch.sh is allowed"

# --- Allow Cases ---

# 7r. Bash + watcher の unit test 実行 (tools/test_pr_watch.py) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'python3 tools/test_pr_watch.py')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: python3 tools/test_pr_watch.py (unit test) is allowed"

# 7s. Bash + unittest module 形の unit test -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'python3 -m unittest tools.test_pr_watch')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: python3 -m unittest tools.test_pr_watch is allowed"

# 7l. Bash + 単発 gh pr checks の結果を while read で加工 (gh は 1 回実行) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr checks 51 --json name,bucket | while read -r row; do echo "$row"; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: one-shot gh pr checks piped into while-read processing is allowed"

# 8. Bash + 単発 gh pr checks (ループなし) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr checks 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: one-shot gh pr checks is allowed"

# 9. Bash + 単発 gh pr checks --repo 付き + jq 加工 -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr checks 51 --repo owner/repo --json name,bucket | jq -r ".[].bucket"')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: one-shot gh pr checks with --repo/--json pipeline is allowed"

# 10. Bash + grep -w (=word-regexp) は --watch でない -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'gh pr checks 51 | grep -w pass')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: gh pr checks piped to grep -w is allowed (-w is not --watch)"

# 11. Bash + pr-watch.sh を引数位置で読むだけ (grep) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'grep -n ci_completed tools/pr-watch.sh')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: reading tools/pr-watch.sh as a grep argument is allowed"

# 12. Bash + gh pr checks を含まない while ループ -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while read -r line; do echo "$line"; done < input.txt')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: unrelated while loop without gh pr checks is allowed"

# 13. 非対象ツール (Edit) は passthrough -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Edit","tool_input":{"file_path":"/tmp/x","old_string":"while gh pr checks","new_string":"y"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "non-target tool (Edit) passes through"

# 14. Monitor + ws source (command 欠落) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Monitor","tool_input":{"ws":{"url":"wss://example.com/stream"},"description":"x"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Monitor ws source without command is allowed"

# 14b. Bash + gh pr view (checks でない別サブコマンド + 文中の checks 語) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while true; do gh pr view 51 --json title; echo checks done; sleep 5; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: gh pr view loop with unrelated 'checks' word is allowed"

# --- Block Cases (Codex round 9 指摘: 二重引用内 cmdsubst / eval / watch 引用引数) ---

# 7as. Bash + 二重引用内のコマンド置換での起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'echo "$(bash tools/pr-watch.sh 51)"')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: launch inside double-quoted command substitution is blocked"

# 7at. Bash + eval の引用スクリプトでの起動 -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "eval 'bash tools/pr-watch.sh 51'")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: eval-quoted launch of pr-watch.sh is blocked"

# 7au. Bash + watch の引用引数の gh pr checks -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "watch -n 30 'gh pr checks 51'")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: watch with quoted gh pr checks argument is blocked"

# --- Allow Cases (Codex round 9 指摘: grep パターンの bash / 単一引用データ) ---

# 7av. Bash + grep のパターン引数がたまたま bash である読み取り -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'env LC_ALL=C grep -n bash tools/pr-watch.sh')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: grep with pattern 'bash' reading pr-watch.sh is allowed"

# 7aw. Bash + 単一引用データに polling ループのテキストを含む printf -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "printf '%s\\n' 'docs: while true; do gh pr checks 51; done'")
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: single-quoted polling-loop text passed to printf is allowed (data)"

# --- Block Cases (Codex round 10 指摘: ラッパー越し pipe heredoc) ---

# 7ax. Bash + heredoc を env bash に pipe (本文はコード) -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "cat <<'EOF' | env bash
while true; do gh pr checks 51; sleep 30; done
EOF")
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: heredoc piped to env bash (executable body) is blocked"

# --- Allow Cases (Codex round 10 指摘: 代入値 / 引数テキストの gh) ---

# 7ay. Bash + 変数代入の値が pr-watch.sh (実行しない) -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'SCRIPT=tools/pr-watch.sh echo assigned')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: assignment value tools/pr-watch.sh without execution is allowed"

# 7az. Bash + ループ内で echo の引数に gh pr checks のテキスト -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'while read -r x; do echo gh pr checks 51; done')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: echo of gh pr checks text inside loop is allowed (argument position)"

# --- Cases (Codex round 11 指摘: subshell 終端 / watch のコマンド位置 / 非実行コマンド引数) ---

# 7ba. Bash + subshell 内の gh pr checks --watch -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" '(gh pr checks 51 --watch)')
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "Bash: (gh pr checks --watch) in subshell is blocked"

# 7bb. Bash + echo の引数に watch gh pr checks のテキスト -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" 'echo watch gh pr checks 51')
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: echo of 'watch gh pr checks' text is allowed (argument position)"

# 7bc. Bash + env printf の引数に pr-watch.sh のパス -> allow
stderr=$(mktemp); TMPFILES+=("$stderr")
json=$(make_payload "Bash" "env printf '%s\\n' tools/pr-watch.sh")
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "Bash: env printf with pr-watch.sh path argument is allowed"

# --- Fail-closed Cases ---

# 15. 空 stdin -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook "" "$stderr")
assert_exit 2 "$ec" "empty stdin is blocked (fail-closed)"

# 16. 不正 JSON -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook 'not json {' "$stderr")
assert_exit 2 "$ec" "invalid JSON is blocked (fail-closed)"

# 17. tool_input が object でない -> block
stderr=$(mktemp); TMPFILES+=("$stderr")
ec=$(run_hook '{"tool_name":"Bash","tool_input":"gh pr checks"}' "$stderr")
assert_exit 2 "$ec" "non-object tool_input is blocked (fail-closed)"

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
