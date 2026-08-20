#!/usr/bin/env bash
# PreToolUse Hook: ad-hoc PR CI 監視の定型逸脱を止める best-effort の安全網
#
# 設計方針 (確定):
#   - best-effort: Claude 自身の定型的な逸脱 (Monitor / Bash での gh pr checks
#     polling ループ、tools/pr-watch.* 直接起動) だけを止める。敵対者対策ではない。
#   - bash の完全パースは非目標。false negative (すり抜け) は許容する。
#   - すり抜けは一次規律 .claude/rules/pr-ci-watch.md と人間レビューが受ける。
#
# したがって判定不能 (jq 欠落 / JSON 破損 / command 欠落) は許可側に倒す
# (安全網であってゲートではない。fail-closed の enforcement フックとは方針が異なる)。
#
# 正規経路は /pr-watch-pane <PR>。pr-watch-pane skill 経由の監視 spawn は
# MCP ツール (mcp__renga-peers__spawn_pane 等) で行われ、本フックの matcher
# (Bash|Monitor) を通らないため誤 deny しない。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。

set -uo pipefail

# 判定材料が揃わなければ全て許可側 (best-effort 安全網)
command -v jq >/dev/null 2>&1 || exit 0
INPUT=$(cat 2>/dev/null) || exit 0
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[[ "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "Monitor" ]] || exit 0
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[[ -n "$COMMAND" ]] || exit 0

deny() {
  {
    echo "BLOCKED: $1"
    echo "PR の CI 監視の正規経路は /pr-watch-pane <PR> のみです (.claude/rules/pr-ci-watch.md)。"
    echo "ペイン枠不足等で /pr-watch-pane が立てられないときは、代替監視に流れず人間に報告して指示を仰いでください。"
  } >&2
  exit 2
}

# 1) tools/pr-watch.* の直接起動: Bash 背景タスクは spawn したシェルのみ追跡され、
#    自己デタッチした監視本体は孤児化して /clear やセッション終了で黙死する
if printf '%s' "$COMMAND" | grep -qE 'tools/(pr-watch\.(sh|ps1)|pr_watch\.py)'; then
  deny "tools/pr-watch.* の直接起動は禁止です (${TOOL_NAME} tool)。"
fi

# 2) gh pr checks + ループ語彙 (while / until / for / watch / --watch) の併用
#    = polling ループ / 張り付き監視。単発の gh pr checks <PR> は状態確認であって
#    監視ではないので許可する (false positive を避ける)
if printf '%s' "$COMMAND" | grep -q 'gh pr checks'; then
  if printf '%s' "$COMMAND" | grep -qE '(^|[;&|({[:space:]])(while|until|for|watch)[[:space:](]|--watch([[:space:]=;&|)]|$)'; then
    deny "gh pr checks の polling ループ / --watch による ad-hoc CI 監視は禁止です (${TOOL_NAME} tool)。"
  fi
fi

exit 0
