#!/usr/bin/env bash
# Tests for block-git-push.sh
# Validates: exit code (0=allow, 2=block) and stderr messages
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.hooks/block-git-push.sh"

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
  echo "$json" | bash "$HOOK" 2>"$stderr_file" || exit_code=$?
  echo "$exit_code"
}

# --- Block Cases ---

# 1. Simple git push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git push is blocked"
assert_stderr_contains "git push" "$stderr" "git push stderr mentions git push"

# 2. git push with remote and branch (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git push origin main is blocked"
assert_stderr_contains "git push" "$stderr" "git push origin main stderr mentions git push"

# 3. git push with extra spaces (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git  push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git with extra spaces push is blocked"

# 4. Piped git push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"echo foo | git push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "piped git push is blocked"

# 5. Chained git push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git add . && git push"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "chained git push is blocked"

# --- Allow Cases ---

# 6. git commit (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git commit -m \"test\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git commit is allowed"

# 7. git pull (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git pull"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git pull is allowed"

# 8. git status (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git status"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git status is allowed"

# 9. git log (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git log --oneline"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git log is allowed"

# 10. Empty command (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "empty command is allowed"

# 11. Non-git command (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"ls -la"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "non-git command is allowed"

# 12. git -C /path push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git -C /some/path push origin main"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git -C /path push is blocked"
assert_stderr_contains "git push" "$stderr" "git -C /path push stderr mentions git push"

# 13. git --git-dir=/tmp/repo push (block)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git --git-dir=/tmp/repo push origin main"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "git --git-dir=... push is blocked"

# 14. git config push.default (allow)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"git config push.default simple"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 0 "$ec" "git config push.default is allowed"

# --- Phase 2a: eval / bash -c / sh -c explicit unwrap (Issue #79) ---

# 15. eval "git push ..." (block via unwrap)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"eval \"git push origin main\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "eval \"git push ...\" is blocked via unwrap"

# 16. bash -c "git push ..." (block via unwrap)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"bash -c \"git push origin main\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "bash -c \"git push ...\" is blocked via unwrap"

# 17. sh -c 'git push ...' (block via unwrap, single-quoted)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"sh -c '"'"'git push origin main'"'"'"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "sh -c '\''git push ...'\'' is blocked via unwrap"

# 18. bash -c "eval 'git push ...'" (nested, block via 2-level unwrap)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"bash -c \"eval '"'"'git push origin main'"'"'\""}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "nested bash -c / eval git push is blocked"

# 19. eval git push --force (unquoted multi-token — caught by existing segment-level regex)
stderr=$(mktemp); TMPFILES+=("$stderr")
json='{"tool_name":"Bash","tool_input":{"command":"eval git push origin main"}}'
ec=$(run_hook "$json" "$stderr")
assert_exit 2 "$ec" "eval git push unquoted multi-token is blocked"

# Refs #429: gh 経由の GitHub 書き込み検知ケース。
# 陽性 (block) / 陰性 (allow) の両方を書く。陰性が無いと、読み取り系まで
# 巻き込んで塞いだことに気づけない（調査業務が回らなくなる回帰）。
#
# コマンド文字列は Python の json.dumps でエスケープする。gh のケースは
# 引用符・波括弧・パイプを多用するため、手書きの JSON リテラルだと
# エスケープ誤りがテスト自体の偽陽性になる。

json_for() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1"
}

# assert_gh <expected_exit> <command> [desc]
assert_gh() {
  local expected="$1" cmd="$2" desc="${3:-$2}"
  local stderr_file
  stderr_file=$(mktemp); TMPFILES+=("$stderr_file")
  local ec
  ec=$(run_hook "$(json_for "$cmd")" "$stderr_file")
  assert_exit "$expected" "$ec" "$desc"
}

echo "# --- gh write operations: block cases ---"

# pr グループ（書き込み系）
assert_gh 2 'gh pr create --title x --body y'
assert_gh 2 'gh pr merge 123 --squash --delete-branch'
assert_gh 2 'gh pr close 123'
assert_gh 2 'gh pr reopen 123'
assert_gh 2 'gh pr edit 123 --add-label bug'
assert_gh 2 'gh pr review 123 --approve'
assert_gh 2 'gh pr comment 123 --body hi'
assert_gh 2 'gh pr ready 123'
assert_gh 2 'gh pr lock 123'
assert_gh 2 'gh pr unlock 123'
assert_gh 2 'gh pr update-branch 123'

# issue グループ（書き込み系）
assert_gh 2 'gh issue create --title x --body y'
assert_gh 2 'gh issue close 42'
assert_gh 2 'gh issue comment 42 --body hi'
assert_gh 2 'gh issue delete 42'
assert_gh 2 'gh issue edit 42 --add-label bug'
assert_gh 2 'gh issue transfer 42 owner/other'
assert_gh 2 'gh issue pin 42'

# repo グループ（書き込み系）
assert_gh 2 'gh repo create owner/newrepo --public'
assert_gh 2 'gh repo delete owner/repo --yes'
assert_gh 2 'gh repo edit --default-branch main'
assert_gh 2 'gh repo rename newname'
assert_gh 2 'gh repo fork owner/repo'
assert_gh 2 'gh repo sync'
assert_gh 2 'gh repo archive owner/repo'
assert_gh 2 'gh repo deploy-key add key.pub'
assert_gh 2 'gh repo autolink create TICKET- https://example.com/n'

# release / workflow / run / gist（書き込み系）
assert_gh 2 'gh release create v1.0.0 --notes x'
assert_gh 2 'gh release delete v1.0.0'
assert_gh 2 'gh release upload v1.0.0 dist.tgz'
assert_gh 2 'gh release edit v1.0.0 --draft=false'
assert_gh 2 'gh workflow run ci.yml'
assert_gh 2 'gh workflow disable ci.yml'
assert_gh 2 'gh run cancel 999'
assert_gh 2 'gh run rerun 999'
assert_gh 2 'gh run delete 999'
assert_gh 2 'gh gist create notes.md'
assert_gh 2 'gh gist delete abc123'

# 権限・資格情報・任意コード実行の経路
assert_gh 2 'gh secret set TOKEN --body v'
assert_gh 2 'gh variable set NAME --body v'
assert_gh 2 'gh label create bug --color ff0000'
assert_gh 2 'gh ssh-key add id_ed25519.pub'
assert_gh 2 'gh gpg-key add key.asc'
assert_gh 2 'gh auth login --with-token'
assert_gh 2 'gh auth token' 'gh auth token (token 抜き取り経由の直接 API 書き込みを塞ぐ)'
assert_gh 2 'gh auth setup-git'
assert_gh 2 'gh extension install owner/gh-ext'
assert_gh 2 'gh extension exec ext arg'
assert_gh 2 'gh alias set pm "pr merge"' 'gh alias set (alias 経由の別名実行を塞ぐ)'
assert_gh 2 'gh alias import aliases.yml'
assert_gh 2 'gh project item-create 1 --title x'
assert_gh 2 'gh codespace create -r owner/repo'

# gh api: 書き込みメソッド
assert_gh 2 'gh api -X POST /repos/o/r/issues -f title=x'
assert_gh 2 'gh api --method POST /repos/o/r/issues'
assert_gh 2 'gh api --method=DELETE /repos/o/r/issues/1'
assert_gh 2 'gh api -XPATCH /repos/o/r/pulls/1' 'gh api -XPATCH (短縮形に値密着)'
assert_gh 2 'gh api -X PUT /repos/o/r/collaborators/u'
assert_gh 2 'gh api repos/o/r/issues -f title=x' 'gh api with -f (メソッド省略時 gh は POST になる)'
assert_gh 2 'gh api repos/o/r/issues -F body=@body.md' 'gh api with -F (同上)'
assert_gh 2 'gh api repos/o/r/issues --input payload.json' 'gh api --input (同上)'
assert_gh 2 'gh api graphql -f query="mutation{addComment(input:{}){clientMutationId}}"' 'gh api graphql mutation'
assert_gh 2 'gh api graphql --input q.json' 'gh api graphql --input (本文を静的に読めない)'

# 判定不能 → fail closed
assert_gh 2 'gh api -X "$METHOD" /repos/o/r/issues' 'gh api メソッドが変数（判定不能）'
assert_gh 2 'gh unknowncmd foo' 'gh の未知 group は default-deny'
assert_gh 2 'gh pr unknownsub 1' 'gh pr の未知 subcommand は default-deny'
assert_gh 2 'gh --unknownflag value pr view 1' 'gh の未知フラグで group 位置がずれる形は default-deny'

# 回避形（segment-split.sh の回避耐性層の上で検知されること）
assert_gh 2 'eval "gh pr merge 123"' 'eval 経由は unwrap で検知'
assert_gh 2 'bash -c "gh pr create -t x -b y"' 'bash -c 経由は unwrap で検知'
assert_gh 2 "sh -c 'gh release create v1'" 'sh -c 経由は unwrap で検知'
assert_gh 2 'bash -c "eval '"'"'gh pr merge 1'"'"'"' 'ネストした bash -c / eval も検知'
assert_gh 2 'git status && gh pr create -t x' 'セグメント連結（&&）でも検知'
assert_gh 2 'echo x | gh pr comment 1 --body-file -' 'パイプ後段の gh 書き込みも検知'
assert_gh 2 'GH_TOKEN=xxx gh pr merge 1' '環境変数を前置した形も検知'
assert_gh 2 'GH_REPO=owner/repo gh pr create -t x' 'GH_REPO 経由の repo 指定形も検知'
assert_gh 2 'subcmd=merge; gh pr "$subcmd" 123' '変数展開でサブコマンドを組み立てる形も検知'
assert_gh 2 'gh pr merge --repo owner/repo 123' '--repo を挟む形も検知'
assert_gh 2 'gh --repo owner/repo pr merge 123' '--repo を group より前に挟む形も検知'
assert_gh 2 '/usr/bin/gh pr merge 123' '絶対パス起動も検知'
assert_gh 2 'gh.exe pr merge 123' 'Windows の gh.exe も検知'
assert_gh 2 'GH pr merge 123' '大文字表記も検知'
assert_gh 2 'echo $(gh pr create -t x)' 'コマンド置換内の gh 書き込みも検知'
assert_gh 2 'gh --hostname github.example.com pr merge 1' '--hostname を挟む形も検知'
assert_gh 2 'cd /tmp && gh issue create -t x' 'cd 連結後も検知'

# 未展開の変数でサブコマンド / group を組み立てる形は判定不能 → fail closed
assert_gh 2 'gh pr "$UNRESOLVED" 123' 'subcommand が未展開の変数（判定不能）'
assert_gh 2 'gh "$UNRESOLVED_GROUP" merge 1' 'group が未展開の変数（判定不能）'
assert_gh 2 'gh pr `printf merge` 1' 'subcommand がコマンド置換（判定不能）'

# リテラル文字列中の gh はコマンド起動ではないので巻き込まない
assert_gh 0 'git commit -m "gh の書き込みを塞ぐ"' '日本語コミットメッセージ中の gh は誤検知しない'
assert_gh 0 'echo "see gh https://cli.github.com/manual"' 'URL を伴う散文中の gh は誤検知しない'
assert_gh 0 'ls tools/gh' 'パス末尾が gh でも後続に subcommand が無ければ allow'

# Codex round 1 (P1): alias 名は [a-z-]+ に限らない。語形トークンは未知でも deny。
assert_gh 2 'gh foo_bar 123' 'アンダースコア入りの未知 group（alias 経由）も deny'
assert_gh 2 'gh pr.merge 1' 'ドット入りの未知 group も deny'
assert_gh 2 'gh 123 1' '数字始まりの未知 group も deny'
assert_gh 2 'gh issue foo_bar 42' 'アンダースコア入りの未知 subcommand も deny'

# Codex round 1 (P1): graphql の本文を静的に読めない形は deny
assert_gh 2 'gh api graphql -F query=@q.graphql' 'graphql の body が @file（読めない）'
assert_gh 2 'gh api graphql -f query=@-' 'graphql の body が標準入力（読めない）'
assert_gh 2 'gh api graphql -f query=$QUERY' 'graphql の body が未展開の変数（読めない）'

# Codex round 2 (P1): リダイレクトが語に密着した形も検知する
assert_gh 2 'gh pr create>/dev/null' 'リダイレクトが subcommand に密着した形も検知'
assert_gh 2 'gh pr>out merge 1' 'リダイレクトが group に密着した形も検知'
assert_gh 2 'gh pr merge 1 2>&1' 'リダイレクトを伴う書き込みも検知'

# Codex round 2 (P1): graphql 判定は endpoint 位置のみで行う
assert_gh 2 'gh api -X DELETE /repos/o/r/issues/1 --template graphql' 'option 値の graphql で REST 書き込みが素通りしない'
assert_gh 2 'gh api -X POST /repos/o/r/issues -H "X-Foo: graphql"' 'header 値の graphql でも素通りしない'

# Codex round 2 (P1): 隣接クォート連結で分断された mutation も検知する
assert_gh 2 'gh api graphql -f query="muta""tion{x}"' '隣接クォート連結の mutation も検知'

# Codex round 3 (P1): pflag の短縮フラグクラスタも解釈する
assert_gh 2 'gh api -iXPOST /repos/o/r/issues' 'クラスタ -iXPOST の POST を検知'
assert_gh 2 'gh api -iFtitle=x /repos/o/r/issues' 'クラスタ -iFtitle=x の暗黙 POST を検知'
assert_gh 2 'gh api -iX DELETE /repos/o/r/issues/1' 'クラスタ -iX + 別トークン値も検知'
assert_gh 2 'gh api graphql -iFquery=@q.graphql' 'クラスタ内の @file body も検知'

# Codex round 3 (P1): 引用符で分断された実行ファイル名も検知する
assert_gh 2 'g"h" pr create -t x' '引用符で分断された gh も検知'
assert_gh 2 '/usr/bin/g"h" pr merge 1' 'パス付きで引用符分断された gh も検知'
assert_gh 2 "g'h' pr merge 1" 'シングルクォートで分断された gh も検知'

echo "# --- gh read-only operations: allow cases (回帰の要) ---"

# pr / issue の読み取り
assert_gh 0 'gh pr view 123'
assert_gh 0 'gh pr view 123 --json state,title -q .state'
assert_gh 0 'gh pr list --limit 20 --state open'
assert_gh 0 'gh pr diff 123'
assert_gh 0 'gh pr status'
assert_gh 0 'gh pr checkout 123' 'gh pr checkout はローカル操作のみで GitHub 書き込みではない'
assert_gh 0 'gh co 123' 'gh co は pr checkout の組み込み alias'
assert_gh 0 'gh issue view 42'
assert_gh 0 'gh issue view 42 --comments'
assert_gh 0 'gh issue list --label bug --limit 50'
assert_gh 0 'gh issue status'

# CI / run / workflow の読み取り（調査業務の主経路）
assert_gh 0 'gh run list --workflow ci.yml --limit 5'
assert_gh 0 'gh run view 999'
assert_gh 0 'gh run view 999 --log-failed'
assert_gh 0 'gh run download 999 --name artifact'
assert_gh 0 'gh workflow list'
assert_gh 0 'gh workflow view ci.yml'

# repo / release / gist / search の読み取り
assert_gh 0 'gh repo view owner/repo'
assert_gh 0 'gh repo list owner --limit 10'
assert_gh 0 'gh repo clone owner/repo'
assert_gh 0 'gh repo deploy-key list'
assert_gh 0 'gh repo license list'
assert_gh 0 'gh release view v1.0.0'
assert_gh 0 'gh release list'
assert_gh 0 'gh release download v1.0.0 --pattern "*.tgz"'
assert_gh 0 'gh gist view abc123'
assert_gh 0 'gh search prs --state open --repo owner/repo'
assert_gh 0 'gh search issues bug --limit 10'
assert_gh 0 'gh search code needle --repo owner/repo'

# その他の読み取り
assert_gh 0 'gh auth status'
assert_gh 0 'gh label list'
assert_gh 0 'gh cache list'
assert_gh 0 'gh secret list'
assert_gh 0 'gh variable get NAME'
assert_gh 0 'gh ruleset list'
assert_gh 0 'gh alias list'
assert_gh 0 'gh config get editor'
assert_gh 0 'gh extension list'
assert_gh 0 'gh status'
assert_gh 0 'gh --version'
assert_gh 0 'gh --help'
assert_gh 0 'gh pr' 'group のみ（help 出力）は allow'
assert_gh 0 'gh pr --help'

# gh api の読み取り
assert_gh 0 'gh api repos/o/r/pulls/1'
assert_gh 0 'gh api /repos/o/r/commits --paginate'
assert_gh 0 'gh api -X GET /repos/o/r/issues'
assert_gh 0 'gh api --method GET /repos/o/r/issues'
assert_gh 0 'gh api -H "Accept: application/vnd.github+json" repos/o/r'
assert_gh 0 'gh api repos/o/r/pulls/1 --jq .head.sha'
assert_gh 0 'gh api graphql -f query="query{viewer{login}}"' 'graphql の read クエリは allow'

# 読み取りの連結・変数展開
assert_gh 0 'gh pr view 1 && gh run list'
assert_gh 0 'prnum=123; gh pr view "$prnum"'
assert_gh 0 'gh pr view --repo owner/repo 1'
assert_gh 0 'gh --repo owner/repo pr view 1'
assert_gh 0 'eval "gh pr view 1"' 'eval 経由の読み取りは allow'

# gh を含まない既存コマンドへの巻き込みが無いこと
assert_gh 0 'ls -la'
assert_gh 0 'git status'
assert_gh 0 'bash tools/gh-helper.sh' 'gh を接尾辞に含むファイル名は gh 起動ではない'
assert_gh 0 'echo through the night' 'gh を含む単語（through）は誤検知しない'

# 短縮フラグクラスタでも読み取りは通る
assert_gh 0 'gh api -i /repos/o/r/pulls/1' 'クラスタでない -i 付き GET は allow'
assert_gh 0 'gh api -iX GET /repos/o/r/issues' 'クラスタ -iX + GET は allow'
assert_gh 0 'gh api -q .head.sha repos/o/r/pulls/1' '-q 付き GET は allow'

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
