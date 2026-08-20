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
# --- command 文字列の正規化 (判定 1 / 2 共通の前処理) ---
# (0) heredoc の本文を落とす。`cat > doc.md <<'EOF' ... EOF` の本文は shell にとって
#     データであり、本文中に polling ループのテキストが書かれていても実行されない。
#     本文行を残すと (2) の改行境界化でコマンド扱いになり false positive になる。
#     heredoc 開始 (`<<` / `<<-` + 任意で引用された delimiter) を見つけたら、
#     delimiter 単独行まで本文行を捨てる (1 行 1 heredoc の簡易対応)。
#     ただし heredoc がシェルインタプリタ (bash / sh 等) の stdin になっている場合、
#     本文はデータでなく実行されるコードなので落とさず残す (残した本文は (2) 以降で
#     コードとして走査される)。
COMMAND=$(printf '%s\n' "$COMMAND" | awk '
  BEGIN { inhd = 0; drop = 0; delim = "" }
  {
    if (inhd) {
      line = $0
      sub(/^[[:space:]]*/, "", line)
      if (line == delim) { inhd = 0; next }
      if (drop) next
      print
      next
    }
    print
    if (match($0, /<<-?[[:space:]]*["'"'"']?[A-Za-z_][A-Za-z0-9_]*/)) {
      d = substr($0, RSTART, RLENGTH)
      sub(/^<<-?[[:space:]]*["'"'"']?/, "", d)
      delim = d
      inhd = 1
      # heredoc の届き先がシェルインタプリタなら本文はコード: 残す。
      # 前置形 (bash <<EOF) と pipe 消費形 (cat <<EOF | bash) の両方を見る。
      # pipe 消費形はラッパー越し (cat <<EOF | env bash 等) も認める。
      drop = (match($0, /(^|[;&|(`[:space:]])([^[:space:]]*\/)?(bash|sh|zsh|dash|ksh|eval)([[:space:]][^<]*)?<</) == 0 \
              && match($0, /\|[[:space:]]*(([^[:space:]]*\/)?(env|nohup|setsid|command|time|timeout|stdbuf|sudo|doas|xargs)[[:space:]]+([^|;&]*[[:space:]])?)?([^[:space:]]*\/)?(bash|sh|zsh|dash|ksh)([[:space:]]|$)/) == 0)
    }
  }
')
# (1) バックスラッシュ継続行 (`\` + 改行) は空白に潰す。シェルはこれを 1 つの
#     コマンドとして実行するため、改行をコマンド境界にすると `gh pr \<NL> checks`
#     の分割で検出を素通りする。
COMMAND=${COMMAND//$'\\\n'/ }
# (2) 残る改行を `;` に潰す。grep は行単位で評価するため、複数行ループで loop 構文と
#     gh 呼び出しが別行に割れると検出を素通りする。改行はシェルのコマンド境界なので
#     `;` への置換は意味を保つ (空白だと「行頭 = コマンド位置」の情報が落ちる)。
COMMAND=$(printf '%s' "$COMMAND" | tr '\n\r' ';;')
# (2.5) シェルインタプリタへ渡す引用済みスクリプト (`bash -c '...'` / `eval '...'`)
#     は引用符を外す。この引用内はデータでなく実行されるコードであり、(3) で区切り
#     文字を潰すと `bash -c 'while ...; do gh pr checks ...; done'` が素通りする。
#     `-c` は `-lc` のような結合フラグ形も対象。
COMMAND=$(printf '%s' "$COMMAND" | sed -E \
  -e "s/(([^[:space:]\/]*\/)*)?(bash|sh|zsh|dash|ksh)(([[:space:]]+-[^[:space:]]+)*)[[:space:]]+-[A-Za-z]*c[[:space:]]+'([^']*)'/\3 -c ;\6/g" \
  -e 's/(([^[:space:]\/]*\/)*)?(bash|sh|zsh|dash|ksh)(([[:space:]]+-[^[:space:]]+)*)[[:space:]]+-[A-Za-z]*c[[:space:]]+"([^"]*)"/\3 -c ;\6/g' \
  -e "s/(^|[;&|({[:space:]])eval[[:space:]]+'([^']*)'/\1eval ;\2/g" \
  -e 's/(^|[;&|({[:space:]])eval[[:space:]]+"([^"]*)"/\1eval ;\2/g' \
  -e "s/(^|[;&|({[:space:]])(([^[:space:]\/]*\/)*)?watch((([[:space:]]+-[^[:space:]]+)([[:space:]]+[^-[:space:]][^[:space:]]*)?)*)[[:space:]]+'([^']*)'/\1watch\4 \8/g" \
  -e 's/(^|[;&|({[:space:]])(([^[:space:]\/]*\/)*)?watch((([[:space:]]+-[^[:space:]]+)([[:space:]]+[^-[:space:]][^[:space:]]*)?)*)[[:space:]]+"([^"]*)"/\1watch\4 \8/g')
# (3) 引用符の中のコマンド区切り文字 (; & | 括弧 ` ) を空白に潰す。引用内は shell
#     にとってデータであり、`echo 'x; bash tools/pr-watch.sh 51'` の `;` を境界扱い
#     すると echo のデータが起動に見える false positive になる。引用符自体と
#     その他の文字は保持する (bash -c '...' 内の起動は引き続き検出できる)。
#     例外: 二重引用内の `$( ... )` (コマンド置換) は shell が実行するコードなので
#     潰さずそのまま残す (`echo "$(bash tools/pr-watch.sh 51)"` は起動)。
COMMAND=$(printf '%s' "$COMMAND" | awk '{
  out = ""; sq = 0; dq = 0; cs = 0
  n = length($0)
  for (i = 1; i <= n; i++) {
    c = substr($0, i, 1)
    if (sq) {
      if (c == "\x27") sq = 0
      else if (index(";&|(){}`", c)) c = " "
    } else if (dq && cs > 0) {
      # 二重引用内のコマンド置換: コードとして保持し、括弧の深さだけ追跡する
      if (c == "(") cs++
      else if (c == ")") cs--
    } else if (dq) {
      if (c == "\\") { out = out c; i++; c = (i <= n) ? substr($0, i, 1) : ""; out = out c; continue }
      if (c == "\"") dq = 0
      else if (c == "$" && substr($0, i + 1, 1) == "(") { cs = 1; out = out c; i++; c = "(" }
      else if (index(";&|(){}`", c)) c = " "
    } else {
      if (c == "\x27") sq = 1
      else if (c == "\"") dq = 1
    }
    out = out c
  }
  print out
}')
# (4) 判定 1 (polling 検出) 用に、単一引用内をすべて空白にマスクした変体を作る。
#     この時点で残っている単一引用内は純データ (実行される bash -c / eval / watch の
#     引用は (2.5) で既に外れている) であり、`printf '%s' 'docs: while ...; do gh pr
#     checks ...; done'` のような文書テキストを polling と誤認しないため。
#     判定 2 は引用込みの COMMAND を使う (引用されたパス引数 `pwsh '.\tools\...'` を
#     見る必要があるため)。
COMMAND_POLL=$(printf '%s' "$COMMAND" | awk '{
  out = ""; sq = 0
  n = length($0)
  for (i = 1; i <= n; i++) {
    c = substr($0, i, 1)
    if (sq) {
      if (c == "\x27") sq = 0
      else c = " "
    } else if (c == "\x27") sq = 1
    out = out c
  }
  print out
}')

# `gh pr checks` の検出はフラグの挿入位置 2 箇所を許容する:
#   - `gh` と `pr` の間の global フラグ (`gh -R owner/repo pr checks` 等)
#   - `pr` と `checks` の間の親コマンドフラグ (`gh pr --repo owner/repo checks` 等)
# 「`-` 始まりのフラグ + 任意でその引数トークン (非 `-` 始まり)」の繰り返しだけを
# 挟めるようにし、`gh pr view ... checks` のような別サブコマンドは挟めない。
# フラグ / 引数トークンにコマンド区切り文字 (; & | 括弧) を含めない
# (`gh --version; pr checks ...` のような別コマンドへの越境 FP を防ぐ)。
GH_FLAG_TOKENS='([[:space:]]+-[^[:space:];&|()]+([[:space:]]+[^-[:space:];&|()][^[:space:];&|()]*)?)*'
GH_PR_CHECKS_PREFIX='gh'"$GH_FLAG_TOKENS"'[[:space:]]+pr'"$GH_FLAG_TOKENS"'[[:space:]]+checks'
GH_PR_CHECKS_RE="$GH_PR_CHECKS_PREFIX"'([[:space:];&|)]|$)'
# コマンド位置の gh: 区切り記号 / 予約語境界の直後 (環境変数代入・パス前置は許容)。
# `echo gh pr checks 51` のような引数テキスト (gh が別コマンドの引数) を polling と
# 誤認しないための左境界。watch の引数位置 (`watch -n 30 gh pr checks`) は
# GH_WATCH_CMD_RE 側で別途照合するのでここには含めない。
GH_PR_CHECKS_CMD='((^|[;&|({])[[:space:]]*|(^|[[:space:]])(if|then|elif|else|do|while|until)[[:space:]]+)([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*([^[:space:]]*/)?'"$GH_PR_CHECKS_PREFIX"
# --watch は「同一の gh pr checks 呼び出しに付いたフラグ」だけを対象にする:
# checks の後、コマンド区切り文字を跨がずに --watch トークンへ到達する場合のみ。
# `gh pr checks 51; other-tool --watch x` のような別コマンドの --watch は拾わない。
GH_CHECKS_WATCH_RE="$GH_PR_CHECKS_CMD"'([[:space:]]+[^[:space:];&|()]+)*[[:space:]]+--watch([[:space:];&|=]|$)'
if printf '%s' "$COMMAND_POLL" | grep -qE "$GH_PR_CHECKS_RE"; then
  if [[ "$TOOL_NAME" == "Monitor" ]] && printf '%s' "$COMMAND_POLL" | grep -qE "$GH_PR_CHECKS_CMD"; then
    deny_with_reason "Monitor tool による gh pr checks の CI 監視は禁止です。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
  # 同一呼び出しの --watch フラグ (張り付き監視)。
  if printf '%s' "$COMMAND_POLL" | grep -qE "$GH_CHECKS_WATCH_RE"; then
    deny_with_reason "gh pr checks --watch による ad-hoc CI 監視は禁止です (${TOOL_NAME} tool)。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
  # ループ判定は「gh pr checks の呼び出し位置が未閉のループスコープ内にある」場合のみ
  # deny する。各出現位置について、その手前のテキストでループ開始語
  # (while/until/for/select) と閉じ語 (done) を数え、開始が閉じより多ければその
  # 呼び出しはループの条件部または本体にあり反復される。
  #   - `gh pr checks ... | while read ...; do ...; done` (単発結果のループ加工) は
  #     手前にループ開始が無いので許可 (gh は 1 回しか走らない)。
  #   - `for f in a b; do ...; done; gh pr checks 51` (閉じたループの後の単発) は
  #     開始 1 / 閉じ 1 で釣り合うので許可。兄弟ループが後続しても影響しない。
  #   - ネスト (`while ...; do for ...; done; gh pr checks; done`) は開始 2 / 閉じ 1
  #     で未閉スコープ内と判定される。
  # ループ開始語の直後は空白のほか `;` (改行由来: `until\n gh ...`) と `(`
  # (算術 for: `for((;;))`) も境界として認める。
  # awk には CMD 形 (境界込み) と PREFIX 形 (gh 本体) の両方を渡す。CMD 形の一致は
  # 予約語境界 (until 等) を一致範囲に含むため、そのまま prefix を切るとループ開始語
  # が prefix から消えて計数を誤る。CMD 一致範囲の中で PREFIX の開始位置を取り直し、
  # その手前までを prefix とする。
  GH_IN_LOOP=$(printf '%s' "$COMMAND_POLL" | awk -v ghre="$GH_PR_CHECKS_CMD" -v ghpre="$GH_PR_CHECKS_PREFIX" '
    {
      s = $0
      off = 0
      while (match(substr(s, off + 1), ghre) > 0) {
        pos = off + RSTART
        rest = substr(s, pos)
        if (match(rest, ghpre) > 0) pos = pos + RSTART - 1
        prefix = substr(s, 1, pos - 1)
        t = prefix; opens  = gsub(/(^|[;&|({[:space:]])(while|until|for|select)([[:space:](;]|$)/, " ", t)
        t = prefix; closes = gsub(/(^|[;&|([:space:]])done([;&|)[:space:]]|$)/, " ", t)
        if (opens > closes) { print "in_loop"; exit }
        off = pos
      }
    }')
  if [[ "$GH_IN_LOOP" == "in_loop" ]]; then
    deny_with_reason "gh pr checks の polling ループによる ad-hoc CI 監視は禁止です (${TOOL_NAME} tool)。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
  # 関数間接呼び出しの保守的 deny: `poll() { gh pr checks 51; }; while true; do poll; done`
  # のように関数へ包むと、gh の出現位置自体はループ外になり上の計数を逃れる。
  # 関数呼び出しの追跡は静的にはできないため、「関数定義 + ループ構文 + gh pr checks」
  # が 1 つの command に同居する形は反復とみなして保守的に deny する。
  if printf '%s' "$COMMAND_POLL" | grep -qE '[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\(\)' \
    && printf '%s' "$COMMAND_POLL" | grep -qE '(^|[;&|({[:space:]])(while|until|for|select)([[:space:](;]|$)'; then
    deny_with_reason "関数定義とループ構文を伴う gh pr checks は、関数経由の polling とみなして保守的に拒否します (${TOOL_NAME} tool)。単発確認は関数・ループに包まず gh pr checks <PR> を直接 1 回だけ実行してください。"
  fi
  # watch コマンド (path 付き /usr/bin/watch も対象) は done を使わないため別判定:
  # 同一コマンド区間内 (; & | を跨がない) で watch の引数に gh pr checks が
  # 現れる場合を deny する。
  GH_WATCH_CMD_RE='(^|[;&|({[:space:]])([^[:space:]]*/)?watch[[:space:]][^;&|]*'"$GH_PR_CHECKS_PREFIX"
  if printf '%s' "$COMMAND_POLL" | grep -qE "$GH_WATCH_CMD_RE"; then
    deny_with_reason "watch コマンドによる gh pr checks の ad-hoc CI 監視は禁止です (${TOOL_NAME} tool)。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
fi

# --- 判定 2: tools/pr-watch.* の直接起動 ---
# 次の 3 形を起動とみなす:
#   a. 環境変数代入 (VAR=value) の連なりの直後に pr-watch ファイル
#   b. 既知のラッパー / インタプリタが先頭トークンで、その後 (フラグ・引数を挟んで)
#      pr-watch ファイル。ラッパーは絶対パス前置 (/usr/bin/bash 等) も許容し、
#      timeout / source / stdbuf / sudo / xargs / py ランチャー / uv 等を含む。
#   c. pr-watch ファイルがコマンド位置に直接 (./tools/pr-watch.sh 51 等)
# `grep foo tools/pr-watch.sh` のような読み取りは、先頭トークン grep がラッパー
# 一覧に無いため起動とみなされず許可される (false positive を作らない)。
# python の module 実行 (`python3 -m tools.pr_watch`) も拾うため、ファイル名は
# 拡張子なしの pr_watch も対象にする。
# 判定はコマンド区切り文字 (; & | ( ) ` { }) で区間に分割し、区間単位で行う。
# これにより起動判定と読み取り例外が別コマンドへ越境しない
# (`cat tools/pr-watch.sh && bash tools/pr-watch.sh` の後半は読み取り例外に隠れず
# deny され、`cat x; bash tools/pr-watch.sh` も後半区間だけで起動と判定される)。
# 環境変数代入は引用された値 (`NOTE='CI watch'` のような空白入り) も 1 トークン
# として許容する。
PR_WATCH_ASSIGN='[A-Za-z_][A-Za-z0-9_]*=('"'"'[^'"'"']*'"'"'|"[^"]*"|[^[:space:]]*)'
# POSIX の dot-source (`. tools/pr-watch.sh`) も source と同じ実行なので `\.` を
# 単独トークンとしてラッパーに含める。
PR_WATCH_WRAPPER='(([^[:space:]]*/)?(nohup|setsid|exec|env|command|eval|time|timeout|stdbuf|source|sudo|doas|xargs|bash|sh|zsh|dash|ksh|pwsh|powershell(\.exe)?|python[0-9.]*|py|uv|run)|\.)'
# ファイル名は basename 完全一致で照合する: 前置は `/` (パス) か `.` (python module
# の package 区切り) か引用符で終わる場合のみ許容し、`tools/test_pr_watch.py` の
# ような別名 (前置が `_` 等で終わる) を拾わない。リポジトリ実在の
# tools/test_pr_watch.py (watcher の unit test) を deny しないための制約。
# 前置は `/` `\` (Windows パス) `.` (python module 区切り) 引用符で終わる場合のみ。
# 終端は空白 / 行末 / 引用符 / リダイレクト (`>` `<`) を認める
# (`bash tools/pr-watch.sh>/tmp/log` のような密着リダイレクトも起動)。
# 前置に `=` を含めない: `SCRIPT=tools/pr-watch.sh` のような変数代入の値は
# 実行されないデータであり、起動として誤検出しない。
PR_WATCH_FILE='(["'"'"']?|[^[:space:]=]*[/.\'"'"'"])(pr-watch\.(sh|ps1)|pr_watch(\.py)?)([[:space:]<>]|$|["'"'"'])'
# 区間内の起動形: 区間先頭から、予約語 (if/then/elif/else/do/while/until) →
# 環境変数代入 → ラッパー / インタプリタ (+ その引数トークン任意) の順に前置を
# 許し、pr-watch ファイルへ到達するもの。先頭トークンがラッパー一覧に無い語
# (grep / sed 等) の場合は前置が成立せず起動とみなされない。
PR_WATCH_LAUNCH_SEG_RE='^[[:space:]]*((if|then|elif|else|do|while|until)[[:space:]]+)*('"$PR_WATCH_ASSIGN"'[[:space:]]+)*('"$PR_WATCH_WRAPPER"'[[:space:]]+([^[:space:]]+[[:space:]]+)*)?'"$PR_WATCH_FILE"
# 読み取り専用コマンドの例外: ラッパーの中間トークンは任意の語を許すため、
# `env LC_ALL=C grep -n x tools/pr-watch.sh` / `timeout 1s cat tools/pr-watch.sh`
# のような「ラッパー越しの読み取り」も LAUNCH_SEG_RE に一致してしまう。同一区間内で
# 既知の読み取りコマンドが pr-watch ファイルより前に現れる場合は読み取りとみなす。
PR_WATCH_READER_SEG_RE='(^|[[:space:]])(grep|egrep|fgrep|rg|ag|cat|bat|head|tail|sed|awk|less|more|wc|diff|cmp|stat|file|md5sum|sha[0-9]*sum|cksum|shellcheck|shfmt|cut|sort|uniq|hexdump|xxd|strings|nl|od|ls|realpath|readlink|basename|dirname|du|touch|chmod|cp|mv|ln|git)[[:space:]].*(pr-watch\.(sh|ps1)|pr_watch(\.py)?)'
# 実行しないインタプリタモードの例外: 構文チェック (`bash -n` / `sh -n`) や
# バイトコンパイル (`python3 -m py_compile`) はファイルを実行しないため許可する。
# `-n` は `-nv` のような結合フラグ形も対象。
PR_WATCH_NOEXEC_SEG_RE='(^|[[:space:]/])((bash|sh|zsh|dash|ksh)[[:space:]]+-[A-Za-z]*n[A-Za-z]*[[:space:]]|python[0-9.]*[[:space:]]+-m[[:space:]]+py_compile[[:space:]])'
# sudo / doas の値付きフラグ (`sudo -u git ...` の `git` はユーザー名) は、reader
# 例外の判定前に区間から取り除く。除去しないと `git` が読み取りコマンド語と誤認され、
# `sudo -u git bash tools/pr-watch.sh` が読み取り扱いで素通りする。除去後の区間
# (`bash tools/pr-watch.sh ...`) は通常の起動判定に掛かる。grep のパターン引数が
# たまたま `bash` である読み取り (`grep -n bash tools/pr-watch.sh`) は sudo/doas が
# 無いので影響を受けず、reader 例外がそのまま効く。
PR_WATCH_SUDO_STRIP_RE='(^|[[:space:]])(sudo|doas)(([[:space:]]+-[^[:space:]]+)([[:space:]]+[^-[:space:]][^[:space:]]*)?)*[[:space:]]+'
while IFS= read -r SEGMENT; do
  [[ -z "${SEGMENT//[[:space:]]/}" ]] && continue
  SEGMENT=$(printf '%s' "$SEGMENT" | sed -E "s/$PR_WATCH_SUDO_STRIP_RE/\1/g")
  if printf '%s' "$SEGMENT" | grep -qE "$PR_WATCH_LAUNCH_SEG_RE"; then
    if ! printf '%s' "$SEGMENT" | grep -qE "$PR_WATCH_READER_SEG_RE" \
      && ! printf '%s' "$SEGMENT" | grep -qE "$PR_WATCH_NOEXEC_SEG_RE"; then
      deny_with_reason "tools/pr-watch.* の直接起動は禁止です (${TOOL_NAME} tool)。Claude Code の背景タスクは spawn したシェルのみ追跡し、監視本体が孤児化します。緊急経路はユーザー自身の ! 手動実行のみです。"
    fi
  fi
done < <(printf '%s\n' "$COMMAND" | tr ';&|(){}`' '\n')

exit 0
