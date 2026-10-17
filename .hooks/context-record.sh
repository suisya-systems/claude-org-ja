#!/usr/bin/env bash
# SessionStart / UserPromptSubmit / Stop / PreCompact Hook: 文脈の自動引き継ぎ用にターンの区切りを記録する
# 方式: stdin の hook JSON を tools/context_record.py に渡し、.state/context/<role>.json を書き換える。
#   何も出力せず exit 0 (ブロックしない)
#
# 背景 (docs/design/context-auto-handover.md §3.1):
#   Claude Code はフックに文脈の使用量を渡さない。会話記録の場所・ペイン id・開いているターンの有無・
#   背景タスクと予約の一覧だけを記録し、閾値の判定と打鍵は tools/context_handover_watch.py が外から行う。
#
# 範囲: ディスパッチャー (tools/org_extension_schema.json roles.dispatcher.required_hooks)。ja 専用。
#
# fail-open: 記録に失敗しても会話を止めない。watcher は古い・食い違う記録を「不明」として扱い、
#   引き継ぎをしない。tests/test-hooks-payload-fail-closed.sh の対象外。

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

"${PY[@]}" "$HOOK_DIR/../tools/context_record.py" >/dev/null 2>&1 || true
exit 0
