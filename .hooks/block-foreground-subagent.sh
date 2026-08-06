#!/usr/bin/env bash
# PreToolUse Hook: subagent ツール (Agent) の前景(同期)起動をブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景・範囲 B(一律: 窓口 + ワーカー全て):
#   Agent(subagent) ツールを run_in_background=true なしで呼ぶと、subagent が
#   完了するまで呼び出し元セッションが同期ブロックされる。窓口がブロックされると
#   人間との接点が止まり、ワーカーがブロックされると窓口からの差し込み
#   (peer message / ack / SUSPEND) に即応できなくなる。
#   そこで本フックは窓口・ワーカーを問わず一律に前景 subagent を禁止し、
#   run_in_background=true の非同期起動のみを許可する。これにより全ロールが
#   常に「次の指示・割り込みに応答可能」な状態を保つ。
#
#   実機検証(PreToolUse payload):
#     - subagent ツールの tool_name は "Agent"(安全のため legacy "Task" も対象)。
#     - 前景起動では tool_input.run_in_background キー自体が欠落する
#       (false で送られるのではなく省略 = 既定前景)。
#     - 非同期起動でのみ tool_input.run_in_background == true (厳密に boolean)。
#   したがって「厳密に boolean true」以外(false / 欠落 / 文字列 "true" 等)は
#   すべて前景扱いで deny する(安全側)。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 検知方針:
#   1. tool_name が "Agent" / "Task" でなければ passthrough(exit 0)。
#   2. tool_input.run_in_background が厳密な boolean true のときだけ許可。
#      それ以外(false / 欠落 / 非 boolean)は前景とみなし deny。
#
# 既知の制限:
#   - jq が無い環境では fail-closed で全 Agent/Task 呼び出しを deny する
#     (既存 block-no-verify.sh / block-git-push.sh と同じ安全側挙動)。
#   - stdin が空 / 空白のみ / 不正な JSON / 非 object / JSON 値が 2 個以上 の
#     場合も fail-closed で deny する。
#     enforcement フックとして parse 不能な payload を素通り(fail-open)させない
#     (本フックには permissions.deny の backstop が無いため、兄弟フックより
#     fail-open の影響が大きい)。実運用ではハーネスが整形済み JSON のみを
#     PreToolUse へ渡すため、この経路は理論上のもの。
#   - 人間が直接 CLI で起動する場合は本フックは効かない。Claude Code の
#     ツール呼び出し経路でのみ作用する。

set -euo pipefail

# Helper: deny decision を stderr + exit 2 で返す
deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

# jq チェック (fail closed)
if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

# stdin から JSON を読み取り
INPUT=$(cat)

# 空 payload の fail-closed ガード (Issue #834)。
# jq は「JSON 値がゼロ個」の入力を parse error にせず、出力なしで exit 0 を返す:
#     printf ''      | jq -e 'type == "object"'  -> exit 0 (出力なし)
#     printf 'x{'    | jq -e 'type == "object"'  -> exit 4 (parse error)
# そのため空 stdin は下の型ガードの `if !` 分岐を発火させずにすり抜け、続く
# `.tool_name // empty` が空文字になって「Agent/Task 以外」と判定され、
# passthrough の exit 0 に落ちていた。不正 JSON は deny されるのに空 payload
# だけが素通りする穴だったため、型ガードより前に明示的に空判定して deny する。
# 空白のみ (改行だけ等) の入力も jq から見れば同じ「値ゼロ個」なので併せて弾く。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。subagent ツール呼び出しは安全側 (fail-closed) で拒否します。"
fi

# top-level が JSON object か検証する (fail closed)。
# set -euo pipefail 下で `VAR=$(printf '%s\n' "$INPUT" | jq ...)` 形式は、jq の parse
# error や非 object への index error 時に exit 5 でスクリプトを中断し、
# PreToolUse では exit!=2 が非ブロッキング扱い = fail-open になる。これを避ける
# ため、deny ロジック前に明示的な `if !` 条件で「parse 可能 かつ top-level が
# object」を一括検査し、満たさなければ exit 2 で deny する。
#   - `jq -e '<述語>'`: 真なら exit 0、偽なら exit 1、不正 JSON なら parse error
#     (exit 4/5)。いずれの非 object/不正系も非ゼロ → fail-closed deny。
# これにより後続の `.tool_name` / `.tool_input` の index は top-level が object
# である前提で安全に評価できる。
#
# tool_input が object でない (文字列・配列等) payload も同じ「不正 payload」の族なので
# ここで併せて弾く。本フックは Agent/Task 以外を tool_name で passthrough させるため、
# tool_input の型検査が下の IS_BACKGROUND 式だけだと `{"tool_name":"Bash","tool_input":[1]}`
# のような壊れた payload が passthrough exit 0 に落ちていた (Issue #834 の横断点検で実測)。
# 兄弟フックと同じ判定式に揃え、tool_name を見る前に payload の形を確定させる。
# tool_input 欠落 (null) は正常な payload の一形態なので許容し、Agent/Task であれば
# 下の IS_BACKGROUND 式が「前景」とみなして deny する (test 8 の契約)。
# jq の `and` は短絡評価なので、左が false のとき右の index は評価されず error にならない。
#
# 入力は `echo` ではなく `printf '%s\n'` で渡す。`echo "$INPUT"` は INPUT が "-n" / "-e"
# / "-E" 等の echo オプションと完全一致すると 1 バイトも出力せず、jq が「JSON 値ゼロ個」
# として exit 0 を返してガードを素通りする (実測で確認)。
# また `-s` (slurp) で入力ストリーム全体を 1 つの配列にまとめ `length == 1` を要求する。
# jq は既定で「JSON 値の連なり」を受け付けるため、slurp しないと JSON object を 2 個
# 並べた payload で述語が各値について真になり exit 0 になる。その後の抽出は値を改行で
# 連結して返す (例: tool_name が "Edit\nEdit") ので、ツール名の一致判定を外して
# passthrough に落ちる。PreToolUse payload は常に単一 object なので 1 個だけを受け付ける。
if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。subagent ツール呼び出しは安全側 (fail-closed) で拒否します。"
fi

# tool_name を取得。subagent ツール以外は passthrough。
TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
if [[ "$TOOL_NAME" != "Agent" && "$TOOL_NAME" != "Task" ]]; then
  exit 0
fi

# run_in_background が厳密に boolean true のときだけ許可。
# jq の `== true` は厳密比較: boolean true のみ真。文字列 "true" / 数値 1 /
# null / 欠落はすべて偽になるため、安全側(前景とみなす)に倒れる。
# tool_input が object でない(文字列・配列等)JSON-valid payload では
# `.tool_input.run_in_background` の index が jq error(exit 5)になり
# fail-open するため、先に `type == "object"` で型ガードする。jq の `and` は
# 短絡評価で、左が false なら右の index は評価されないので error にならない。
IS_BACKGROUND=$(printf '%s\n' "$INPUT" | jq -r 'if ((.tool_input | type) == "object") and (.tool_input.run_in_background == true) then "yes" else "no" end')

if [[ "$IS_BACKGROUND" != "yes" ]]; then
  deny_with_reason "subagent (${TOOL_NAME}) の前景(同期)起動は禁止です。run_in_background=true を指定して非同期で起動してください。前景起動は呼び出し元(窓口・ワーカー)をブロックし、人間接点や窓口からの差し込みへの即応を止めるため、ハーネスで一律に拒否しています。"
fi

exit 0
