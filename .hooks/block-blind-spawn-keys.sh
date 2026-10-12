#!/usr/bin/env bash
# PreToolUse Hook: 画面判定の記録が無いキー送信 (send_keys) をブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   spawn 直後のワーカーには Claude Code の起動プロンプト (folder-trust / dev-channel) が出る。
#   folder-trust の初期カーソルは「No, exit」なので、盲目の Enter はワーカーを終了させる
#   (2026-09-25 の事故)。`.dispatcher/references/spawn-flow.md` 3-3b は `inspect_pane` →
#   `claude-org-runtime dispatcher spawn-prompt-step` が返した 1 手だけを送ると定めるが、
#   ディスパッチャーは `bypassPermissions` で動くので、手順を飛ばしたキー送信を止める層はフックしか無い。
#   runtime 0.1.50 の spawn-prompt-step は `--target` を付けると判定を
#   `<state-dir>/dispatcher/spawn-prompt/<target>.json` に書く (runtime docs/cli.md
#   「Decision record (stable contract, Issue #244)」)。このフックはその記録で送信を照合する。
#
# 対象 (ディスパッチャーの send_keys のうち、キーを含むものだけ):
#   - `keys` が空でない、または `enter` が true の呼び出しを判定する。
#   - `text` だけの呼び出し (3-5a の kickoff 本文など、文字の入力) は判定せず通す。
#     ただし text に制御文字 (\r / \n / ESC 等) があれば deny する (renga は text を生バイトで書くので
#     "\r" は Enter と同じになる)。
#   ディスパッチャーがキーを送るのは 3-3b の承認 (`{"enter": true}` / `{"keys": ["Down"|"Up"]}`)、
#   キュレーター起動時の同じ承認 (pane-close.md 5-4)、3-5a kickoff の Enter だけなので、
#   キーを含む送信はすべて記録で裏付ける。窓口の打鍵 (/clear、承認ハンドシェイク等) は
#   このフックを登録していない窓口の設定で動くので止めない。
#
# 判定 (記録 = <state-dir>/dispatcher/spawn-prompt/<target>.json、target は呼び出しの target そのもの):
#   - 記録が無い・読めない・schema_version != 1・target 不一致・期限切れ (now >= expires_at_ms) なら deny
#   - action == "send_keys": 呼び出しのキー列が記録の send_keys と一致し、text が空なら allow
#     (キー名は大小文字を無視し Return を Enter に畳んで比べる。それ以外の別名は一致とみなさない)
#   - action == "done" (起動プロンプトを抜けて入力欄が見えている): Enter 単発なら allow。
#     3-5a kickoff の Enter はこれで通す (直前の inspect 結果で spawn-prompt-step を 1 回回すと done が出る)
#   - allow したら記録を消す (1 回限り)。消せなければ deny
#
# 既知の制限:
#   - state dir は state.db と同じディレクトリとみなす (block-close-before-retro.sh と同じ前提)。
#     `STATE_DB_PATH` で別の場所を指したまま spawn-prompt-step に別の `--state-dir` を渡すと記録が見えず deny になる (安全側)。
#   - 記録はペインの世代を持たない。done を得たあと Enter を送らずに同じ宛先で spawn し直すと、
#     30 秒以内なら新しいペインの起動プロンプトに Enter 単発が通る (残りは TTL だけが縛る)。
#   - 見るのは MCP の send_keys だけ。Bash から `renga send` / `tmux send-keys` で打つ経路は止めない。
#   - 記録の `target` は送信先の表記と文字列で一致させる必要がある。数値 pane id 宛に送るなら
#     `--target` にも同じ数値を渡す (spawn-flow.md 3-3b)。`focused` は宛先に使えない。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload / 想定外の引数形 / 記録の読み取り・削除失敗は deny する
# (`tests/test-hooks-payload-fail-closed.sh` が payload 側を横断で固定する)。

set -euo pipefail

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

INPUT=$(cat)

if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。send_keys 呼び出しは安全側 (fail-closed) で拒否します。"
fi

if ! command -v jq &>/dev/null; then
  deny_with_reason "jq がインストールされていません。セキュリティ Hook の実行に必要です。"
fi

if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。send_keys 呼び出しは安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
case "$TOOL_NAME" in
  send_keys | *__send_keys) ;;
  *) exit 0 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# tools/journal_append.sh と同じ interpreter の選び方 (Windows は py -3 / python)。
if command -v py >/dev/null 2>&1; then
  PY="py -3"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

if ! VERDICT=$(printf '%s\n' "$INPUT" | $PY -c '
import json, os, re, sys, time

sys.path.insert(0, sys.argv[1])
# Windows の py -3 はパイプの stdin を cp932 で読むので bytes から UTF-8 で読む
args = json.loads(sys.stdin.buffer.read().decode("utf-8")).get("tool_input") or {}


def canon(keys, enter):
    out = []
    for k in keys:
        k = k.strip().lower()
        out.append("enter" if k == "return" else k)
    return out + (["enter"] if enter else [])


text, keys, enter, target = (args.get(k) for k in ("text", "keys", "enter", "target"))
if keys is None:
    keys = []
if enter is None:
    enter = False
if (not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)
        or not isinstance(enter, bool) or not (text is None or isinstance(text, str))):
    print("deny:send_keys の引数形が想定外です (keys は文字列の配列、enter は真偽値、text は文字列)。")
    sys.exit(0)
# renga は text を生バイトで PTY に書くので、"\r" や ESC 列は text でもキーになる
if text and any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
    print("deny:text に制御文字 (改行・ESC 等) が含まれています。キーは keys / enter で送ってください。")
    sys.exit(0)
call = canon(keys, enter)
if not call:
    print("allow")  # 文字の入力だけ。起動プロンプトへの応答にならない
    sys.exit(0)
if isinstance(target, int) and not isinstance(target, bool):
    target = str(target)
# runtime spawn_prompt._TARGET_RE と同じ。記録のファイル名になれない宛先は照合できない。
# focused は呼び出しごとに別のペインを指しうるので照合に使わない
if (not isinstance(target, str) or target == "focused"
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", target)):
    print(f"deny:宛先 {target!r} は判定記録と照合できない形です (worker-<task_id> か数値 pane id で送ってください)。")
    sys.exit(0)

from tools.state_db.discover import resolve_state_db_path

path = os.path.join(os.path.dirname(str(resolve_state_db_path())),
                    "dispatcher", "spawn-prompt", f"{target}.json")
try:
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
except FileNotFoundError:
    print(f"deny:{target} の判定記録がありません ({path})。")
    sys.exit(0)
except (OSError, ValueError) as e:
    print(f"deny:{target} の判定記録を読めませんでした ({type(e).__name__})。")
    sys.exit(0)


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


if (not isinstance(rec, dict) or rec.get("schema_version") != 1 or not is_int(rec.get("schema_version"))
        or rec.get("target") != target or not is_int(rec.get("expires_at_ms"))):
    print(f"deny:{target} の判定記録が契約の形ではありません (schema_version / target / expires_at_ms)。")
    sys.exit(0)
if int(time.time() * 1000) >= rec["expires_at_ms"]:
    print(f"deny:{target} の判定記録は期限切れです (30 秒)。")
    sys.exit(0)
action, sk = rec.get("action"), rec.get("send_keys")
if action == "send_keys":
    ok = (isinstance(sk, dict) and not text
          and isinstance(sk.get("keys", []), list) and all(isinstance(k, str) for k in sk.get("keys", []))
          and isinstance(sk.get("enter", False), bool)
          and call == canon(sk.get("keys", []), sk.get("enter", False)))
    want = json.dumps(sk, ensure_ascii=False)
elif action == "done":
    ok = not text and call == ["enter"]
    want = "Enter 単発 (done)"
else:
    ok, want = False, f"action={action!r}"
if not ok:
    print(f"deny:送ろうとしたキー {call} (text={bool(text)}) が {target} の判定記録 {want} と一致しません。")
    sys.exit(0)
try:
    os.remove(path)  # 1 回限り
except OSError as e:
    print(f"deny:{target} の判定記録を消費できませんでした ({type(e).__name__})。")
    sys.exit(0)
print("allow")
' "$REPO_ROOT" 2>&1); then
  deny_with_reason "send_keys の判定記録の照合に失敗しました (${VERDICT//$'\n'/ })。安全側 (fail-closed) で拒否します。"
fi

[[ "$VERDICT" == "allow" ]] && exit 0
if [[ "$VERDICT" == deny:* && "$VERDICT" != *$'\n'* ]]; then
  deny_with_reason "${VERDICT#deny:} キーを送る前に、直前の inspect_pane の結果で claude-org-runtime dispatcher spawn-prompt-step を --target <send_keys と同じ宛先> --task-id <task_id> --state-dir ../.state 付きで回し、返された send_keys をそのまま 1 回だけ送ってください (.dispatcher/references/spawn-flow.md 3-3b)。3-5a kickoff の Enter は、本文入力後の inspect 結果で同じ判定を回して done を得てから送ります。盲目の Enter は禁止です (folder-trust の初期カーソルは「No, exit」)。"
fi
deny_with_reason "send_keys の判定が想定外の出力を返しました (${VERDICT//$'\n'/ })。安全側 (fail-closed) で拒否します。"
