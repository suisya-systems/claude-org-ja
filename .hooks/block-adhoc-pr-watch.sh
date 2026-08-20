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
if printf '%s' "$COMMAND" | grep -qE 'gh[[:space:]]+pr[[:space:]]+checks'; then
  if [[ "$TOOL_NAME" == "Monitor" ]]; then
    deny_with_reason "Monitor tool による gh pr checks の CI 監視は禁止です。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
  if printf '%s' "$COMMAND" | grep -qE '(^|[;&|({[:space:]])(while|until|for|watch)([[:space:]]|$)|[[:space:]]--watch([[:space:]=]|$)'; then
    deny_with_reason "gh pr checks の polling ループ / --watch による ad-hoc CI 監視は禁止です (${TOOL_NAME} tool)。セッション寿命依存の監視は /clear やセッション終了で黙死します。"
  fi
fi

# --- 判定 2: tools/pr-watch.* の直接起動 ---
# コマンド位置 (行頭 / ; & | ( ` $( の直後)、または既知のラッパー
# (nohup / setsid / exec / env / command / time / bash / sh / zsh / pwsh /
# powershell / python / python3 / uv run) を介した起動のみを対象にする。
# `grep foo tools/pr-watch.sh` のような引数位置での出現は起動でないので許可。
PR_WATCH_LAUNCH_RE='(^|[;&|(`]|\$\()[[:space:]]*((nohup|setsid|exec|env|command|time)[[:space:]]+)*((bash|sh|zsh|pwsh|powershell(\.exe)?|python3?|uv[[:space:]]+run)[[:space:]]+)?[^[:space:]]*(pr-watch\.(sh|ps1)|pr_watch\.py)([[:space:]]|$|["'"'"'])'
if printf '%s' "$COMMAND" | grep -qE "$PR_WATCH_LAUNCH_RE"; then
  deny_with_reason "tools/pr-watch.* の直接起動は禁止です (${TOOL_NAME} tool)。Claude Code の背景タスクは spawn したシェルのみ追跡し、監視本体が孤児化します。緊急経路はユーザー自身の ! 手動実行のみです。"
fi

exit 0
