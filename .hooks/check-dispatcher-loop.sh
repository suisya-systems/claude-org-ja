#!/usr/bin/env bash
# UserPromptSubmit Hook: ディスパッチャーの /loop 3m 監視が未武装のまま運転が続いていたら窓口に知らせる
# 方式: stdout に 1 行出して exit 0 (UserPromptSubmit の stdout は窓口の文脈に入る)。ブロックはしない
#
# 背景 (Issue #1168):
#   ディスパッチャーは初回 DELEGATE_COMPLETE で「/loop 3m で監視します」と宣言したまま
#   idle に落ちることがある。検出は /org-start「起動後の裏取り」の書面手順だけで、
#   読み飛ばされると誰も気付かない。relay_scan.py --audit もループの中でしか走らない。
#   よってループの外 (窓口の毎ターン) から、ループが毎サイクル書く heartbeat を読む。
#
# 判定本体: tools/dispatcher_loop_watch.py (稼働中ワーカーがいて、最終見回りが 9 分 =
#   3 サイクルより古いときだけ 1 行出す)。
#
# 範囲: 窓口 (tools/org_extension_schema.json roles.secretary.required_hooks)。ja 専用。
#
# fail-open: 助言フックなので、python 欠落・DB 不在・判定の異常終了はすべて無言で通す
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

"${PY[@]}" "$HOOK_DIR/../tools/dispatcher_loop_watch.py" 2>/dev/null </dev/null || true
exit 0
