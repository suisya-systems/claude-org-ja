#!/usr/bin/env bash
# UserPromptSubmit Hook: ユーザーの文に数値・設定値・挙動の主張があるとき、同意する前に 1 回確かめるよう窓口に促す
# 方式: stdout に 1 行出して exit 0 (UserPromptSubmit の stdout は窓口の文脈に入る)。ブロックはしない
#
# 背景 (Issue #1192):
#   窓口はオーナーの前提 (閾値は 256K の窓を前提に) に記録を見ずに同意した。手元の記録は
#   それと食い違っていた。CLAUDE.md の citation-or-abstain は同意の場面を名指ししていなかった。
#
# 判定本体: tools/premise_check.py (数値+単位 / key=value / 主張の語のどれかがあれば 1 行出す)。
#   誤って出しても注意が 1 行増えるだけなので、規則は単純なまま保つ。
#
# 範囲: 窓口 (tools/org_extension_schema.json roles.secretary.required_hooks)。ja 専用。
#
# fail-open: 助言フックなので、python 欠落・判定の異常終了はすべて無言で通す
#   (窓口の入力を止めない)。tests/test-hooks-payload-fail-closed.sh の対象外。

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# tools/journal_append.sh と同じ interpreter の選び方 (Windows は py -3 / python)。
if command -v py >/dev/null 2>&1; then
  PY=(py -3)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1; then
  PY=(python)
else
  exit 0
fi

"${PY[@]}" "$HOOK_DIR/../tools/premise_check.py" 2>/dev/null || true
exit 0
