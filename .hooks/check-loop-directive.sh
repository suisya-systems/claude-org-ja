#!/usr/bin/env bash
# PreToolUse Hook: ディスパッチャーの /loop 監視ディレクティブを canonical 正文に固定する
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景 (2026-09-02 incident):
#   ディスパッチャーが `/loop 3m` の監視ループを、canonical 正文
#   (.claude/skills/dispatcher-resume/SKILL.md Step 5 のコードブロック) ではなく
#   自分で書いた短縮版で武装した。短縮版は relay scan の `--audit` (滞留検知) だけを
#   毎サイクル叩き、Step 5.25 の実配送手順 (`--list` -> `send_message` ->
#   `--mark-delivered`) を一度も実行していなかった。結果、契約上「見逃しゼロの主保証」で
#   ある relay 層が 1 日以上「滞留を報告するだけの層」に退化した。直 push が生きていた
#   ため症状が出ず、`last_scan_at` の age が 2237 分になって初めて発覚した。
#   判定方針と D- entry: .dispatcher/references/loop-directive-guard.md
#
#   手順書には既に「正文を使え」と書かれていて、それでも守られなかった。窓口が peer
#   message で再武装を指示しても、効くのはその session が続く間だけで、次の /org-start・
#   handover・auto-compact で同じことが起きる。よって prose ではなく機構で固定する。
#
# 適用範囲 (ディスパッチャー限定):
#   本フックは **ディスパッチャーの role config にだけ** 配る
#   (.claude/skills/org-setup/references/permissions.md のディスパッチャー節 /
#    tools/org_extension_schema.json roles.dispatcher.required_hooks)。
#   ワーカーの完了後 bounded /loop・キュレーター・窓口の /loop 用途は対象外であり、
#   それらの role config には入れない (誤 deny を避けるため)。
#
# canonical 正文の SoT (単一):
#   .claude/skills/dispatcher-resume/SKILL.md の fenced code block のうち、
#   先頭行が `/loop` で始まるもの。SKILL.md は tools/gen_skill_prose.py の生成物で、
#   編集の SoT は SKILL.md.in 側。本フックは **写経を持たず**、実行時に SKILL.md を
#   読んで正文を取り出す (2 箇所に正文を置かない)。
#   ディスパッチャーが実際に読むのも rendered な SKILL.md なので、「フックが許す文面」と
#   「手順書が指示する文面」は構造的に一致する。
#
# 判定 (D-entry: .dispatcher/references/loop-directive-guard.md に理由を記録):
#   1. tool_name が CronCreate / ScheduleWakeup 以外 -> passthrough (exit 0)。
#   2. ScheduleWakeup の stop:true (prompt 無し) -> 許可。loop の停止を塞がない。
#   3. prompt を空白正規化し、canonical 正文の **本文全体** (= `/loop [interval]` を
#      取り除いた残り) が連続部分列として現れることを要求する。現れなければ deny。
#      -> ディスパッチャーの CronCreate/ScheduleWakeup は監視ループ以外に用途が無いので、
#         canonical 以外は全て deny する (brief の推奨どおり)。
#   なぜ「厳密な完全一致」ではなく「正文全体の包含」か:
#      CronCreate の prompt は `/loop 3m <本文>` から interval とコマンド名を剥がした
#      <本文> が入り、ScheduleWakeup の prompt は「/loop 入力をそのまま」= `/loop 3m <本文>`
#      が入る (ツール定義の prompt 説明)。同じ正文が 2 つの包み方で届くため、素の
#      文字列等価を課すと片方が構造的に必ず deny される。包含にしても **短縮版・自己流の
#      言い換えは正文全体を含まないので通らない** (今回の事故はこれで塞がる)。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 既知の制限:
#   - jq / awk が無い環境、payload が空 / 不正 JSON / 非 object の場合は fail-closed で
#     deny する (兄弟 enforcement フックと同じ規律。tests/test-hooks-payload-fail-closed.sh)。
#   - CLAUDE_ORG_PATH 未設定 / SKILL.md が読めない / 正文が 1 つも抽出できない場合も
#     fail-closed で deny する。正文を確認できないまま監視ループを張らせると、まさに
#     本フックが防ごうとしている「正文でない /loop」を通すことになるため。
#   - 人間が直接ターミナルで叩く場合は本フックは効かない。

set -euo pipefail

CANONICAL_REL=".claude/skills/dispatcher-resume/SKILL.md"

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

# canonical 正文の場所と、短縮版が危険な理由をディスパッチャーに毎回示す。
print_sot_pointer() {
  {
    echo "canonical 正文: ${CANONICAL_REL} の Step 5「監視ループの再開」にある /loop の fenced code block (編集 SoT は SKILL.md.in)。"
    echo "自己流の短縮版は relay scan の --audit だけを回して Step 5.25 の実配送 (--list -> send_message -> --mark-delivered) を落とし、relay 層を「滞留を報告するだけ」に退化させます (2026-09-02 incident)。正文をそのまま貼ってください。"
  } >&2
}

if ! command -v jq &>/dev/null; then
  echo "ブロック: jq がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

if ! command -v awk &>/dev/null; then
  echo "ブロック: awk がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

INPUT=$(cat)

# 空 payload の fail-closed ガード (Issue #834)。jq は「JSON 値がゼロ個」の入力を
# parse error にせず exit 0 + 出力なしで返すため、jq に渡す前に明示的に弾く。
# 導出の詳細は .hooks/block-foreground-subagent.sh の同じガード。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。安全側 (fail-closed) で拒否します。"
fi

# 不正 JSON / 非 object payload の fail-closed ガード (Issue #834)。
# `-s` (slurp) + `length == 1` で「JSON 値の連なり」を弾き、`printf '%s\n'` で
# INPUT が "-n" / "-e" 等のときに echo が何も出力しない穴も塞ぐ。
if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
if [[ "$TOOL_NAME" != "CronCreate" && "$TOOL_NAME" != "ScheduleWakeup" ]]; then
  exit 0
fi

# ScheduleWakeup(stop:true) は「loop を今すぐ終了する」呼び出しで prompt を持たない
# (ツール定義: stop が true のとき他フィールドは全て無視される)。監視ループの停止まで
# 塞ぐと、監視対象ゼロで止める正規の経路が使えなくなるため許可する。
if [[ "$TOOL_NAME" == "ScheduleWakeup" ]]; then
  if printf '%s\n' "$INPUT" | jq -e '.tool_input.stop == true' >/dev/null 2>&1; then
    exit 0
  fi
fi

PROMPT=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.prompt // empty')
if [[ -z "${PROMPT//[[:space:]]/}" ]]; then
  print_sot_pointer
  deny_with_reason "${TOOL_NAME} の prompt が空です。ディスパッチャーの ${TOOL_NAME} は監視ループの武装以外に用途がないため、canonical 正文以外は拒否します。"
fi

# --- canonical 正文の取り出し (SoT は 1 箇所: SKILL.md) ---
if [[ -z "${CLAUDE_ORG_PATH:-}" ]]; then
  print_sot_pointer
  deny_with_reason "CLAUDE_ORG_PATH が未設定で canonical 正文 (${CANONICAL_REL}) を解決できませんでした。安全側 (fail-closed) で拒否します。settings.local.json の env.CLAUDE_ORG_PATH を確認してください。"
fi

CANONICAL_FILE="${CLAUDE_ORG_PATH%/}/${CANONICAL_REL}"
if [[ ! -r "$CANONICAL_FILE" ]]; then
  print_sot_pointer
  deny_with_reason "canonical 正文のファイルを読めませんでした: ${CANONICAL_FILE}。安全側 (fail-closed) で拒否します。"
fi

# 空白正規化: 空白 (改行・タブ含む) を **全て除去** して比較する。
# 「1 個のスペースに畳む」ではなく除去にするのは、正文本文が日本語で語間に空白を
# 持たないため: 端末幅やツールの整形で本文が折り返されると、元は空白が無かった位置に
# 改行が入り、畳み方式では「空白差だけ」の正文が deny される (実測)。除去なら折り返し
# 位置に依存せず一致する。副作用として "a b" と "ab" が同一視されるが、正文全体の
# 包含判定において短縮版を通す方向には働かない。
# LC_ALL=C でバイト単位に固定する (UTF-8 の多バイト文字は [:space:] に該当しないので
# 日本語本文は壊れない)。
normalize_ws() {
  LC_ALL=C tr -d '[:space:]'
}

# fenced code block 内で `/loop` から始まる行だけを拾う (prose / HTML コメント中の
# `/loop` 言及は対象外)。tests/test_dispatcher_resume_loop_invariant.py の
# _fenced_loop_command_lines と同じ抽出規則。
CANONICAL_LINES=$(awk '
  /^[[:space:]]*```/ { in_fence = !in_fence; next }
  in_fence {
    line = $0
    sub(/^[[:space:]]+/, "", line)
    if (line ~ /^\/loop([[:space:]]|$)/) print line
  }
' "$CANONICAL_FILE")

if [[ -z "${CANONICAL_LINES//[[:space:]]/}" ]]; then
  print_sot_pointer
  deny_with_reason "canonical 正文を ${CANONICAL_FILE} から抽出できませんでした (fenced code block 内に /loop 行が無い)。安全側 (fail-closed) で拒否します。"
fi

PROMPT_NORM=$(printf '%s' "$PROMPT" | normalize_ws)

matched=no
while IFS= read -r loop_line; do
  [[ -z "${loop_line//[[:space:]]/}" ]] && continue
  # `/loop [interval] <本文>` から <本文> を取り出す。interval (3m / 30s / 1h) は
  # 省略可能なので、数値+単位のトークンのときだけ剥がす。
  body="${loop_line#/loop}"
  body="${body#"${body%%[![:space:]]*}"}"
  first_tok="${body%%[[:space:]]*}"
  if [[ "$first_tok" =~ ^[0-9]+[smh]$ ]]; then
    body="${body#"$first_tok"}"
  fi
  body_norm=$(printf '%s' "$body" | normalize_ws)
  [[ -z "$body_norm" ]] && continue
  if [[ "$PROMPT_NORM" == *"$body_norm"* ]]; then
    matched=yes
    break
  fi
done <<< "$CANONICAL_LINES"

if [[ "$matched" != "yes" ]]; then
  print_sot_pointer
  deny_with_reason "${TOOL_NAME} の prompt が canonical な監視ディレクティブの正文を含んでいません (空白正規化後の全文照合)。自己流の短縮・要約・言い換えでは監視ループを張れません。"
fi

exit 0
