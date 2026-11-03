#!/usr/bin/env bash
# UserPromptSubmit Hook: ユーザーが入力したら、窓口のホームタブの「判断待ち」目印 (@state=needs-decision) を消す
# 方式: 何も出力せず exit 0。ブロックしない
#
# 判定本体: tools/cockpit_state.py (broker + tmux のときだけ動く。窓口ペイン以外・
#   needs-decision 以外・channel/タスク通知の入力では何もしない)。
# 範囲: 窓口 (tools/org_extension_schema.json roles.secretary.required_hooks)。ja 専用。
# fail-open: python 欠落・tmux 不達はすべて無言で通す
#   (tests/test-hooks-payload-fail-closed.sh の対象外)。

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v py >/dev/null 2>&1; then
  PY=(py -3)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1; then
  PY=(python)
else
  exit 0
fi

"${PY[@]}" "$HOOK_DIR/../tools/cockpit_state.py" clear 2>/dev/null || true
exit 0
