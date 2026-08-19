#!/usr/bin/env bash
# PreToolUse Hook: subagent ツール (Agent) の前景(同期)起動をブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景・範囲 B(一律: 窓口 + ワーカー全て):
#   subagent が呼び出し元セッションを同期ブロックすると、窓口では人間との接点が
#   止まり、ワーカーでは窓口からの差し込み (peer message / ack / SUSPEND) に
#   即応できなくなる。そこで本フックは窓口・ワーカーを問わず一律に前景 subagent を
#   禁止し、全ロールが常に「次の指示・割り込みに応答可能」な状態を保つ。
#
#   実機検証(PreToolUse payload) — 2026-08-19 再実測 (Issue #942):
#     - subagent ツールの tool_name は "Agent"(安全のため legacy "Task" も対象)。
#     - ハーネス現行仕様では Agent ツールの入力スキーマから run_in_background が
#       **廃止され、subagent は常時背景実行**になった。実測した tool_input は
#         {"description":..., "prompt":..., "subagent_type":...}
#       で run_in_background キーは存在しない。
#     - さらにスキーマが additionalProperties:false のため、呼び出し側が
#       run_in_background=true を明示指定しても**ハーネスが送信前に除去**する。
#       実測: 明示指定あり / なし の 2 回の呼び出しで、フックに届いた tool_input は
#       いずれも上記 3 キーのみで完全一致した。
#   すなわち現行ハーネスで「run_in_background == true」を要求する判定は
#   **どう呼んでも満たせない** = 全 subagent 呼び出しが誤 deny される。
#   これが Issue #942 で軽量レーンが機能停止した原因。
#
#   旧仕様(run_in_background が入力スキーマに存在した頃)では「キー欠落 = 既定前景」
#   だったが、現行仕様ではキー欠落は「常時背景」を意味する。同じ「欠落」が真逆の
#   意味を持つため、キーの**有無**を先に見て解釈を分岐する。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 検知方針:
#   1. tool_name が "Agent" / "Task" でなければ passthrough(exit 0)。
#   2. キーが存在する場合(旧ハーネス / 将来の再導入)は、厳密な boolean true の
#      ときだけ許可し、それ以外(false / 文字列 "true" / 数値 / null 等)は
#      前景ないし不明な指定とみなして deny する(安全側)。
#   3. キーが存在しない場合は tool_name で解釈を分ける。"Agent" は現行ハーネスの
#      常時背景実行として許可、legacy "Task" は旧仕様の既定前景として deny。
#      (根拠と残存リスクは該当箇所のコメントを参照)
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
#   - Agent/Task で tool_input が object でない(欠落 / null を含む)payload も
#     fail-closed で deny する。実測した subagent payload は必ず object の
#     tool_input を持つため、正常系を弾かない。
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
# tool_input の型検査が下の判定式だけだと `{"tool_name":"Bash","tool_input":[1]}`
# のような壊れた payload が passthrough exit 0 に落ちていた (Issue #834 の横断点検で実測)。
# 兄弟フックと同じ判定式に揃え、tool_name を見る前に payload の形を確定させる。
# tool_input 欠落 (null) は非 subagent ツールでは正常な payload の一形態なので
# ここでは許容し、Agent/Task に限って下の専用ガードで deny する (test 8 の契約)。
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

# ここから先は Agent/Task 確定。tool_input が object であることを要求する
# (上のガードは非 subagent ツールのために null を許容している)。実測した
# subagent payload は必ず description/prompt/subagent_type を持つ object なので、
# object でない = 想定外の payload として fail-closed で deny する。
if ! printf '%s\n' "$INPUT" | jq -e '(.tool_input | type) == "object"' >/dev/null 2>&1; then
  deny_with_reason "subagent (${TOOL_NAME}) の PreToolUse payload に object 形式の tool_input がありませんでした。安全側 (fail-closed) で拒否します。"
fi

# run_in_background の解釈を「キーの有無」で分岐する (Issue #942)。
# 現行ハーネスは Agent の入力スキーマから run_in_background を廃止し subagent を
# 常時背景実行にしたため、キーは常に欠落する。旧判定 (`== true` を要求) では
# どう呼んでも条件を満たせず全 subagent が誤 deny されていた。
#   - キー欠落        -> "absent"  : ツール名で意味が変わる。下の分岐を参照。
#   - 厳密 boolean true -> "yes"   : 明示的な背景指定。許可。
#   - それ以外        -> "no"      : false / 文字列 "true" / 数値 / null 等。
#                                    前景ないし不明な指定として deny (安全側)。
# jq の `== true` は厳密比較なので、文字列 "true" や数値 1 は "no" に倒れる。
BG_STATE=$(printf '%s\n' "$INPUT" | jq -r '
  if (.tool_input | has("run_in_background") | not) then "absent"
  elif (.tool_input.run_in_background == true) then "yes"
  else "no"
  end')

if [[ "$BG_STATE" == "no" ]]; then
  deny_with_reason "subagent (${TOOL_NAME}) の前景(同期)起動は禁止です。run_in_background に true 以外が指定されています。前景起動は呼び出し元(窓口・ワーカー)をブロックし、人間接点や窓口からの差し込みへの即応を止めるため、ハーネスで一律に拒否しています。現行ハーネスでは run_in_background は廃止され subagent は常時背景実行なので、この指定自体を外してください。"
fi

# キー欠落の扱いはツール名で分ける。
# 「欠落」は旧仕様では『既定前景』、現行仕様では『常時背景』という真逆の意味を持ち、
# payload だけでは両者を判別できない。そこで判別材料になる唯一の信号 = tool_name を使う:
#   - "Agent": 現行ハーネスのツール名。実測どおりキーは常に欠落し、常時背景実行。許可する。
#   - "Task" : 旧ハーネスのツール名 (現行ハーネスは emit しない)。この名前が届いた時点で
#              run_in_background が入力スキーマに存在した世代とみなせるので、欠落 =
#              既定前景として従来どおり deny し、旧世代での防護を落とさない。
# 現行ハーネスは Task を emit しないため、この分岐に正常系の巻き添えコストは無い。
#
# 残存リスク (既知・受容): run_in_background を持つ**旧世代の "Agent"** へ downgrade した
# 場合だけは、欠落を背景と誤認して前景 subagent を通しうる。payload にも環境にも世代を
# 示す信号が無く、フックから毎回ハーネス版を問い合わせるのは PreToolUse の実行コストに
# 見合わないため、ここは検出せず素通りさせる。実運用は現行ハーネス固定であり、
# downgrade 時は本フックの前提ごと見直す想定。
if [[ "$BG_STATE" == "absent" && "$TOOL_NAME" == "Task" ]]; then
  deny_with_reason "subagent (${TOOL_NAME}) の前景(同期)起動は禁止です。legacy Task ツールでは run_in_background の欠落が既定の前景実行を意味するため、明示的に run_in_background=true を指定してください。前景起動は呼び出し元(窓口・ワーカー)をブロックし、人間接点や窓口からの差し込みへの即応を止めるため、ハーネスで一律に拒否しています。"
fi

exit 0
