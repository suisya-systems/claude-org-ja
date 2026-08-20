#!/usr/bin/env bash
# PreToolUse Hook: PR CI 監視の ad-hoc 代替 (gh pr checks polling / pr-watch.* 直接起動) をブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景 (2026-08-20 実害):
#   PR #51 の CI 監視で正規経路 /pr-watch-pane が [split_refused] (ペイン枠不足) に
#   なった際、窓口がスキルの規定 (「報告して中断」) に従わず、セッション寿命依存の
#   Monitor (gh pr checks の polling ループ) へ自己判断で差し替えた。prose の規律
#   (root CLAUDE.md「PR 後の CI 監視」節 / .claude/rules/pr-ci-watch.md) だけでは
#   逸脱を防げなかったため、本フックで機械的に deny する。
#
# 検知方針:
#   1. tool_name が "Bash" / "Monitor" でなければ passthrough (exit 0)。
#   2. Monitor: command に `gh pr checks` を含んだ時点で deny (Monitor は定義上
#      「監視」)。Bash: `gh pr checks` + polling 構造 (while / until / for / watch)
#      または `--watch` フラグを伴うもののみ deny。単発の `gh pr checks <n>`
#      (ループなし) は状態確認であり監視ではないので許可 (false positive を作らない)。
#   3. command が tools/pr-watch.sh / tools/pr-watch.ps1 / tools/pr_watch.py を
#      「コマンド位置」で直接起動するものを deny する。緊急経路はユーザー自身の
#      `!` 手動実行であり、Claude のツール呼び出しは deny してよい。
#      grep / cat 等の引数としてファイル名が現れるだけの読み取りは deny しない
#      (コマンド位置 + 既知ラッパー (bash/nohup/setsid 等) のみを起動とみなす)。
#
# 正規経路: /pr-watch-pane <PR> (broker tmux セッション内の専用ペイン、
# セッション寿命非依存、events テーブルへ ci_completed を記録)。
# ペイン枠不足等で立てられないときは代替に流れず人間に報告して指示を仰ぐ。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 既知の制限:
#   - jq が無い環境では fail-closed で対象ツール呼び出しを deny する
#     (既存 block-foreground-subagent.sh と同じ安全側挙動)。
#   - 空 stdin / 不正 JSON / 非 object payload も fail-closed で deny する。
#   - 文字列連結等で難読化された起動 (V=tools/pr-watch; bash "$V.sh") は検出
#     できない。本フックは「うっかり逸脱」を止める防波堤であり、意図的回避の
#     完全防御ではない (その層は prose 規律とレビューが担う)。

set -euo pipefail

DENY_GUIDANCE="PR の CI 監視の正規経路は /pr-watch-pane <PR> のみです (.claude/rules/pr-ci-watch.md)。ペイン枠不足 ([split_refused]) 等で立てられない場合は、代替監視に流れず人間に報告して指示を仰いでください。"

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason $DENY_GUIDANCE" >&2
  exit 2
}

# jq チェック (fail closed)
if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

INPUT=$(cat)

# 空 payload の fail-closed ガード (block-foreground-subagent.sh と同じ根拠:
# jq は「JSON 値ゼロ個」の入力を parse error にせず exit 0 を返すため、
# 型ガードの手前で明示的に弾く)。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。安全側 (fail-closed) で拒否します。"
fi

# top-level が単一の JSON object であることを検証する (fail closed)。
# `echo` でなく `printf '%s\n'`、`-s` slurp で単一値を要求する理由は
# block-foreground-subagent.sh の同箇所コメントを参照。
if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした。安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
if [[ "$TOOL_NAME" != "Bash" && "$TOOL_NAME" != "Monitor" ]]; then
  exit 0
fi

# 対象ツール確定。command 文字列を取り出す (欠落 / 非文字列は空扱い)。
# Monitor は ws source (command なし) の形もあるため、command 欠落は許可に倒す
# (本フックの関心は shell command による polling のみ)。
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command? // empty | if type == "string" then . else "" end' 2>/dev/null || echo "")
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# --- 判定 1: gh pr checks による ad-hoc CI 監視 ---
# Monitor tool: command に `gh pr checks` を含んだ時点で deny する。Monitor は
#   定義上「監視」であり、ループの有無によらず gh pr checks を Monitor に載せる
#   ことがそのまま「正規経路外の CI 監視」になる (2026-08-20 の実害はこの形)。
# Bash tool: `gh pr checks` に加えて polling 構造を伴う場合のみ deny する:
#   - シェルのループ構文 (while / until / for) / watch コマンド
#   - gh 自身の --watch フラグ (gh pr checks <n> --watch は張り付き監視)。
#     短縮 -w は --web (ブラウザで開く) であり watch ではないので対象外
#     (gh pr checks --help で実確認済み)。
#   単発の `gh pr checks <n>` (ループなし) はどれにも該当せず許可される
#   (false positive を作らない)。
# 複数行 command は改行を `;` に潰してから判定する。grep は行単位で評価するため、
# そのままだと `while true; do\n  gh pr checks 51\n  sleep 30\ndone` のような
# 複数行ループで loop 構文と gh 呼び出しが別行に割れて検出を素通りする。
# 改行はシェルのコマンド境界なので `;` への置換は意味を保つ (空白への置換だと
# 「行頭 = コマンド位置」の情報が落ちて判定 2 の anchor を取りこぼす)。
COMMAND=$(printf '%s' "$COMMAND" | tr '\n\r' ';;')

# `gh pr checks` の検出はフラグの挿入位置 2 箇所を許容する:
#   - `gh` と `pr` の間の global フラグ (`gh -R owner/repo pr checks` 等)
#   - `pr` と `checks` の間の親コマンドフラグ (`gh pr --repo owner/repo checks` 等)
# 「`-` 始まりのフラグ + 任意でその引数トークン (非 `-` 始まり)」の繰り返しだけを
# 挟めるようにし、`gh pr view ... checks` のような別サブコマンドは挟めない。
# フラグ / 引数トークンにコマンド区切り文字 (; & | 括弧) を含めない
# (`gh --version; pr checks ...` のような別コマンドへの越境 FP を防ぐ)。
GH_FLAG_TOKENS='([[:space:]]+-[^[:space:];&|()]+([[:space:]]+[^-[:space:];&|()][^[:space:];&|()]*)?)*'
GH_PR_CHECKS_RE='gh'"$GH_FLAG_TOKENS"'[[:space:]]+pr'"$GH_FLAG_TOKENS"'[[:space:]]+checks([[:space:]]|$)'
if printf '%s' "$COMMAND" | grep -qE "$GH_PR_CHECKS_RE"; then
  if [[ "$TOOL_NAME" == "Monitor" ]]; then
    deny_with_reason "Monitor tool による gh pr checks の CI 監視は禁止です。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
  # ループ判定は「ループ構文が gh pr checks より前にある」(= 呼び出しがループ本体に
  # 入って反復される) 場合のみ deny する。`gh pr checks ... | while read ...` のように
  # 単発実行の結果をループで加工するだけの形は gh が 1 回しか走らないので許可する
  # (単発例外の false positive を作らない)。watch は path 付き (/usr/bin/watch) も対象。
  GH_LOOP_BEFORE_RE='(^|[;&|({[:space:]])(while|until|for|([^[:space:]]*/)?watch)[[:space:]].*'"$GH_PR_CHECKS_RE"
  if printf '%s' "$COMMAND" | grep -qE "$GH_LOOP_BEFORE_RE|[[:space:]]--watch([[:space:]=]|$)"; then
    deny_with_reason "gh pr checks の polling ループ / --watch による ad-hoc CI 監視は禁止です (${TOOL_NAME} tool)。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
fi

# --- 判定 2: tools/pr-watch.* の直接起動 ---
# コマンド位置 (行頭 / ; & | ( ` $( の直後) を起点に、次の 3 形を起動とみなす:
#   a. 環境変数代入 (VAR=value) の連なりの直後に pr-watch ファイル
#   b. 既知のラッパー / インタプリタが先頭トークンで、その後 (フラグ・引数を挟んで)
#      pr-watch ファイル。ラッパーは絶対パス前置 (/usr/bin/bash 等) も許容し、
#      timeout / source / stdbuf / sudo / xargs / py ランチャー / uv 等を含む。
#      ラッパー確定後の中間トークンは任意 (timeout の `1h` 等) だが、コマンド区切り
#      文字 (; & | ( )) を含むトークンは跨げない (別コマンドへの越境 FP を防ぐ)。
#   c. pr-watch ファイルがコマンド位置に直接 (./tools/pr-watch.sh 51 等)
# `grep foo tools/pr-watch.sh` のような読み取りは、先頭トークン grep がラッパー
# 一覧に無いため起動とみなされず許可される (false positive を作らない)。
# python の module 実行 (`python3 -m tools.pr_watch`) も拾うため、ファイル名は
# 拡張子なしの pr_watch も対象にする。
# コマンド位置の anchor は、記号境界 (行頭 ; & | ( ` { $( ) に加えてシェルの
# 予約語境界 (then / do / else / elif) も含める。`if ...; then bash tools/pr-watch.sh; fi`
# や `while ...; do bash tools/pr-watch.sh; done` の起動を素通りさせないため。
PR_WATCH_ANCHOR='((^|[;&|(`{]|\$\()[[:space:]]*|(^|[[:space:]])(then|do|else|elif)[[:space:]]+)'
PR_WATCH_ASSIGN='[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*'
PR_WATCH_WRAPPER='([^[:space:]]*/)?(nohup|setsid|exec|env|command|time|timeout|stdbuf|source|sudo|doas|xargs|bash|sh|zsh|dash|ksh|pwsh|powershell(\.exe)?|python[0-9.]*|py|uv|run)'
PR_WATCH_MIDDLE_TOKEN='[^[:space:];&|()`]+'
# ファイル名は basename 完全一致で照合する: 前置は `/` (パス) か `.` (python module
# の package 区切り / 引用符) で終わる場合のみ許容し、`tools/test_pr_watch.py` の
# ような別名 (前置が `_` 等で終わる) を拾わない。リポジトリ実在の
# tools/test_pr_watch.py (watcher の unit test) を deny しないための制約。
PR_WATCH_FILE='(["'"'"']?|[^[:space:]]*[/.'"'"'"])(pr-watch\.(sh|ps1)|pr_watch(\.py)?)([[:space:]]|$|["'"'"'])'
PR_WATCH_LAUNCH_RE="$PR_WATCH_ANCHOR"'('"$PR_WATCH_ASSIGN"'[[:space:]]+)*('"$PR_WATCH_WRAPPER"'[[:space:]]+('"$PR_WATCH_MIDDLE_TOKEN"'[[:space:]]+)*)?'"$PR_WATCH_FILE"
if printf '%s' "$COMMAND" | grep -qE "$PR_WATCH_LAUNCH_RE"; then
  deny_with_reason "tools/pr-watch.* の直接起動は禁止です (${TOOL_NAME} tool)。Claude Code の背景タスクは spawn したシェルのみ追跡し、監視本体が孤児化します。緊急経路はユーザー自身の ! 手動実行のみです。"
fi

exit 0
