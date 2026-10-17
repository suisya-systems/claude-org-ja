#!/usr/bin/env bash
# PreToolUse Hook: 振り返りの完了報告ゲートを通っていない worker ペインへの close_pane をブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   `.dispatcher/references/pane-close.md` は「Step 1〜2 の振り返りが終わるまでペインを閉じない」
#   と定める (閉じると worker の出力が失われ、振り返りができなくなる)。ディスパッチャーは
#   `bypassPermissions` で動くので、手順を飛ばした close を止める層はフックしか無い。
#
# 判定 (数値 pane id 宛の close_pane だけを見る):
#   1. events の `worker_spawn_verified` (tools/spawn_gate.py verify) で pane_id -> task を引く。
#      同じ pane_id が複数あれば最新のもの。見つからなければ worker ペインではない
#      (curator / pr-watch / attention watcher 等) ので通す。
#   2. その task について、次のどれかがあれば通す:
#      - その `worker_spawn_verified` 以降に次のどちらかがある
#        - `retro_gate_acked` (tools/dispatcher_retro_gate.py が acked 時に記録)
#        - `retro_gate_confirmed_missing` (replied_no_ack で窓口が「届いていない」と確定した
#          ときに dispatcher が journal_append.sh で記録。pane-close.md の exit 3 分岐)
#      - `runs.status` が `completed` / `failed` / `abandoned` (run が閉じている)
#      - worker-idle-state.json の `completion_reported_at` があり、かつ `runs.status == 'review'`
#      `completed` と最後のケースは pane-close.md「0. 受領記録の確認」が ack 相当として通す 2 ケース。
#   3. どれも無ければ deny。
#   相対セレクタ (`focused` / 裸の name / target 省略) は block-relative-close-pane.sh が
#   止めるので、ここでは判定せず通す。
#
# 既知の制限:
#   - daemon 再起動で pane id が採番し直されると、古い `worker_spawn_verified` の pane_id が
#     別ペインに当たりうる。その task が未完了なら close が deny される (安全側)。
#   - ack 後に同じ worker へ再指示 (T6) が入っても、その ack で close は通る。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
# DB: 書き手 (spawn_gate.py / dispatcher_retro_gate.py) と同じ resolve_state_db_path() で引く
#     ($STATE_DB_PATH > linked worktree からも main checkout へ解決する discovery)。
#     worker-idle-state.json は DB と同じディレクトリの dispatcher/ 配下。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload / DB の読み取り失敗は deny する
# (`tests/test-hooks-payload-fail-closed.sh` が payload 側を横断で固定する)。

set -euo pipefail

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

INPUT=$(cat)

if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。close_pane 呼び出しは安全側 (fail-closed) で拒否します。"
fi

if ! command -v jq &>/dev/null; then
  deny_with_reason "jq がインストールされていません。セキュリティ Hook の実行に必要です。"
fi

if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。close_pane 呼び出しは安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
case "$TOOL_NAME" in
  close_pane | *__close_pane) ;;
  *) exit 0 ;;
esac

# 数値 pane id (3 / "3" / "%3") 以外は block-relative-close-pane.sh の担当。
PANE_ID=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.target // empty | tostring | ltrimstr("%")')
[[ "$PANE_ID" =~ ^[0-9]+$ ]] || exit 0

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# tools/journal_append.sh と同じ interpreter の選び方 (Windows は py -3 / python)。
if command -v py >/dev/null 2>&1; then
  PY="py -3"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

if ! VERDICT=$($PY - "$REPO_ROOT" "$PANE_ID" <<'PY' 2>&1
import json, os, sqlite3, sys

sys.path.insert(0, sys.argv[1])
from tools.state_db.discover import resolve_state_db_path

db, pane_id = str(resolve_state_db_path()), int(sys.argv[2])
if not os.path.isfile(db):
    print("allow")
    sys.exit(0)
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
row = conn.execute(
    "SELECT id, json_extract(payload_json, '$.task') FROM events "
    "WHERE kind = 'worker_spawn_verified' "
    "AND CAST(json_extract(payload_json, '$.pane_id') AS INTEGER) = ? "
    "ORDER BY id DESC LIMIT 1", (pane_id,)).fetchone()
if not row or not row[1]:
    print("allow")
    sys.exit(0)
verified_id, task = row
acked = conn.execute(
    "SELECT 1 FROM events "
    "WHERE kind IN ('retro_gate_acked', 'retro_gate_confirmed_missing') "
    "AND json_extract(payload_json, '$.task') = ? AND id > ? LIMIT 1",
    (task, verified_id)).fetchone()
status = conn.execute(
    "SELECT status FROM runs WHERE task_id = ?", (task,)).fetchone()
status = status[0] if status else ""
reported = ""
idle = os.path.join(os.path.dirname(db), "dispatcher", "worker-idle-state.json")
try:
    with open(idle, encoding="utf-8") as f:
        reported = (json.load(f).get(f"worker-{task}") or {}).get("completion_reported_at") or ""
except (OSError, ValueError, AttributeError):
    pass
if acked or status in ("completed", "failed", "abandoned") or (reported and status == "review"):
    print("allow")
else:
    print(f"deny {task} {status or '-'}")
PY
); then
  deny_with_reason "close_pane の振り返り判定で state.db を読めませんでした (${VERDICT//$'\n'/ })。安全側 (fail-closed) で拒否します。"
fi

[[ "$VERDICT" == "allow" ]] && exit 0
if [[ "$VERDICT" == deny\ * && "$VERDICT" != *$'\n'* ]]; then
  read -r _ TASK STATUS <<<"$VERDICT"
  deny_with_reason "pane ${PANE_ID} は worker-${TASK} のペインで、振り返りの完了報告ゲートを通った記録がありません (retro_gate_acked / retro_gate_confirmed_missing なし / runs.status=${STATUS})。.dispatcher/references/pane-close.md の「完了報告ゲート」で tools/dispatcher_retro_gate.py を回し、acked (または replied_no_ack で未着を確定し retro_gate_confirmed_missing を記録) になってから閉じてください。secretary unreachable なら閉じずに retro_deferred を記録します。"
fi
deny_with_reason "close_pane の振り返り判定が想定外の出力を返しました (${VERDICT//$'\n'/ })。安全側 (fail-closed) で拒否します。"
