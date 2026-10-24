#!/usr/bin/env bash
# PreToolUse Hook: Group B (close_pane / set_pane_identity) の相対セレクタ (`focused` / 裸の name) をブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景 (Issue #1018):
#   2026-09-20T16:10Z、ワーカーが CLOSE_PANE 処理中に `close_pane(target="focused")`
#   を撃ち、**窓口ペインを閉じた**。`"focused"` は「そのタブで人間がいま見ている
#   ペイン」であって caller 自身ではないため、ワーカーが撃てば窓口に当たりうる。
#   `close_pane` は不可逆である。
#
# 契約 (`docs/contracts/backend-interface-contract.md` T-§4.2 "Fail-safe
# consequence for Group B") は Group B (`close_pane` / `set_pane_identity`) の
# 宛先を「**自タブと独立に確立済みの列挙から採った数値 pane id**」に限ると
# **MUST** で定めている。本フックはその MUST のうち**機械判定できる半分**
# ―― 相対セレクタでないこと ―― を実行時に強制する。
#
# 範囲 (一律: 窓口 / ディスパッチャー / キュレーター / ワーカー全て):
#   ディスパッチャーは `bypassPermissions` で動くため `permissions.deny` が
#   効かず、フックだけが障壁になる。ワーカー側の `close_pane` deny (role schema)
#   とは別レイヤの、全ロール共通の防波堤として置く。
#
# 配置を ja の `.hooks/` にした理由 (Issue #1018「決めていないこと」の決定):
#   - 既存 guard 群 (`block-adhoc-pr-watch.sh` / `block-foreground-subagent.sh`
#     等) と同じ層・同じ作法・同じ横断テスト
#     (`tests/test-hooks-payload-fail-closed.sh`) に載る。
#   - 相対セレクタの静的チェッカー `tools/check_group_b_selectors.py`
#     (手順ドキュメントへの再混入を弾く) と同じリポジトリに置くことで、
#     **静的 (doc) と動的 (実行時) の 2 面が 1 つの allowlist 定義を共有**できる。
#     下の `pr-watch-` carve-out は同スクリプトの `ALLOWLIST` と対応する。
#   - runtime 側 (`claude-org-runtime`) に置く案は採らなかった: ja の
#     `tools/org_extension_schema.json` は runtime バンドル schema と byte 一致が
#     要求される (`tools/check_runtime_schema_drift.py`) ため、runtime 配置は
#     フックの一行修正ごとに runtime のペアリリースを要求する。guard の反復速度を
#     落とす割に、フック本体は ja 固有の運用規律 (Group B 手順) の表現である。
#
# 何を許すか:
#   - 数値 pane id: `3` / `"3"` / `"%3"` (renga / broker いずれの表記も)
#   - backend の native handle: herdr の `"w1:p2"` (broker の list_panes が id に返す形)
#   - DD-2 stale-binding carve-out: **`mcp__org-broker__close_pane` 宛のときだけ**
#     `pr-watch-<PR>` 形の裸 name。
#     登録簿に name binding だけが stale に残り `list_panes` に出ないため
#     **列挙から数値 pane id を取り直せない**経路で、契約 T-§4.2 が
#     transport 条件付きで認めている唯一の裸 name 経路
#     (SoT: `.claude/skills/pr-watch-pane/SKILL.md` Step 5 (b) の 3 条件。
#     静的側の対応は `tools/check_group_b_selectors.py` の `ALLOWLIST`)。
#     3 条件の成立判定は payload から機械的に確認できないので、フックは
#     **name の形だけ**を見て通し、条件の遵守は手順とレビューが受け持つ。
#
# 何を止めるか:
#   - `target` 省略 / null (契約が記す既定は相対セレクタ)
#   - `target="focused"`
#   - 上記 carve-out 以外の裸 name (`"secretary"` / `"dispatcher"` /
#     `"curator"` / `"worker-{task_id}"` 等)
#   - `set_pane_identity` も同じ規則で止める (契約 T-§4.2 は Group B 両方に
#     数値 pane id を要求する)。既定 `focused` のまま撃つと別ペインを改名する。
#     carve-out は `close_pane` だけ (改名に stale-binding の後片付け経路は無い)。
#
# 既知の制限 (Issue #1018 の求める 3 点目に対する残余):
#   **窓口ペインを数値 pane_id で撃つ経路は本フックでは止まらない。** 窓口の
#   pane_id を記録した場所が repo にも `.state/` にも無く (`.state/org-state.json`
#   / `tools/state_db/schema.sql` が持つのは dispatcher / curator の pane_id だけ)、
#   broker には renga の `RENGA_PANE_ID` に相当する caller pane id を out-of-band で
#   供給する surface が無い (契約 T-§4.2 caller pane id acquisition rule)。
#   したがってフックは「この数値 id が窓口か」を判定する材料を持たない。
#   今回の事故経路 (`"focused"`) と名前宛 (`"secretary"`) は本フックで塞がり、
#   ワーカーからの `close_pane` 自体は role schema の deny (runtime とのペア変更)
#   が塞ぐ。数値 id 経由の残余はその 2 枚で覆う設計。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload はすべて deny する
# (`tests/test-hooks-payload-fail-closed.sh` が全 enforcement hook 横断で固定する
#  不変条件。deny 理由の文言もそこでマーカー照合される)。

source "$(dirname "${BASH_SOURCE[0]}")/lib/deny-record.sh"

set -euo pipefail

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
}

INPUT=$(cat)

# 空 / 空白のみ payload の fail-closed ガード。jq に依存しないので jq チェックより前に置く
# (jq は「JSON 値ゼロ個」の入力を parse error にせず出力なし exit 0 を返すため、
#  後段に置くと素通りする — Issue #834)。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。close_pane 呼び出しは安全側 (fail-closed) で拒否します。"
fi

if ! command -v jq &>/dev/null; then
  deny_with_reason "jq がインストールされていません。セキュリティ Hook の実行に必要です。"
fi

# top-level が単一 JSON object か、tool_input が null か object かを一括検査する。
# `-s` (slurp) + `length == 1` で「JSON 値の連なり」を弾く。`printf '%s\n'` を使うのは
# `echo "$INPUT"` が INPUT == "-n" / "-e" / "-E" のとき 1 バイトも出力しないため。
if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。close_pane 呼び出しは安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')

# Group B (close_pane / set_pane_identity) 以外は passthrough。完全修飾名は transport で
# 変わる (`mcp__renga-peers__close_pane` / `mcp__org-broker__set_pane_identity` 等) ので
# 末尾一致で拾う。OP / HARM は deny 文言と carve-out 判定に使う。
case "$TOOL_NAME" in
  close_pane | *__close_pane) OP="close_pane"; HARM="不可逆に閉じ" ;;
  set_pane_identity | *__set_pane_identity) OP="set_pane_identity"; HARM="別の name / role に書き換え" ;;
  *) exit 0 ;;
esac

# ここから先は Group B 確定。tool_input が object であることを要求する
# (上のガードは対象外ツールのために null を許容している)。
if ! printf '%s\n' "$INPUT" | jq -e '(.tool_input | type) == "object"' >/dev/null 2>&1; then
  deny_with_reason "${OP} (${TOOL_NAME}) の PreToolUse payload に object 形式の tool_input がありませんでした。安全側 (fail-closed) で拒否します。"
fi

CONTRACT_NOTE="契約 docs/contracts/backend-interface-contract.md T-§4.2 'Fail-safe consequence for Group B' は ${OP} の宛先を「自タブと確立済みの列挙から採った数値 pane id」に限っています (MUST)。list_panes で name / role を照合し、その数値 pane_id で撃ち直してください。"

TARGET_TYPE=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.target | type')

case "$TARGET_TYPE" in
  "null")
    deny_with_reason "${OP} (${TOOL_NAME}) の target が指定されていません。既定の相対セレクタに解決され、人間が見ているペイン (窓口を含む) を${HARM}うるため拒否します。${CONTRACT_NOTE}"
    ;;
  "number")
    # 数値リテラルは整数のみ許す。
    if printf '%s\n' "$INPUT" | jq -e '(.tool_input.target | floor) == .tool_input.target and .tool_input.target >= 0' >/dev/null 2>&1; then
      exit 0
    fi
    deny_with_reason "${OP} (${TOOL_NAME}) の target が非整数の数値です。${CONTRACT_NOTE}"
    ;;
  "string")
    # 列挙から採った pane id の形だけ通す: 数値 `3` / tmux handle `%3` / herdr handle `w1:p2`
    # (broker の list_panes は backend の native handle を id に返す)。pane name は
    # `[A-Za-z0-9_-]` しか持てない (契約 §4.1) ので `%` / `:` を含む handle は name 解決に
    # 落ちえない。判定は jq 側で \A..\z に固定する ($(...) は末尾改行を剥がすので
    # bash 側で見ると "w1:p2\n" が通ってしまう)。
    # 形の定義は tools/check_group_b_selectors.py の _NUMERIC_RE /
    # block-close-before-retro.sh と揃える。
    if printf '%s\n' "$INPUT" | jq -e '.tool_input.target | test("\\A(%?[0-9]+|w[0-9A-Za-z_]+:p[0-9A-Z]+)\\z")' >/dev/null 2>&1; then
      exit 0
    fi
    TARGET=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.target')
    # DD-2 stale-binding carve-out (pr-watch ペインの後片付けのみ)。
    # 契約 T-§4.2 はこの carve-out に「Group B を実際に駆動している backend が
    # org-broker であることの**積極的証拠**」を要求し、解決済み既定からの推論を
    # MUST NOT としている。ハーネス上のその証拠は「これから呼ぶ完全修飾名が
    # mcp__org-broker__* であること」なので、TOOL_NAME で判定する。
    # renga は Group B を単一タブモデルで解決しないため、裸 name は別組織の
    # 同名 watcher へ先勝ちでフォールスルーしうる (carve-out 自体が事故になる)。
    # set_pane_identity には後片付け経路が無いので carve-out は close_pane だけに置く。
    if [[ "$OP" == "close_pane" && "$TARGET" =~ ^pr-watch-.+$ ]]; then
      if [[ "$TOOL_NAME" == mcp__org-broker__* ]]; then
        exit 0
      fi
      deny_with_reason "${OP} (${TOOL_NAME}) の target=\"${TARGET}\" は DD-2 stale-binding carve-out の形ですが、この carve-out は Group B を単一タブモデルで解決する backend (org-broker) でのみ成立します。完全修飾名が mcp__org-broker__* でない呼び出しでは、裸の name が別組織の同名ペインへフォールスルーしうるため拒否します。${CONTRACT_NOTE}"
    fi
    if [[ "$TARGET" == "focused" ]]; then
      deny_with_reason "${OP} (${TOOL_NAME}) の target=\"focused\" は禁止です。'focused' は caller 自身ではなく「そのタブで人間がいま見ているペイン」に解決するため、窓口ペインを${HARM}ます (Issue #1018 の実事故は close_pane)。${CONTRACT_NOTE}"
    fi
    if [[ "$OP" == "close_pane" && "$TARGET" == "secretary" ]]; then
      deny_with_reason "${OP} (${TOOL_NAME}) で窓口ペイン (name=\"secretary\") を閉じることは、どのロールからも禁止です。窓口は人間との唯一の接点であり、停止経路 (/org-suspend / /org-down) でも close_pane では畳まず自分自身で exit します。${CONTRACT_NOTE}"
    fi
    deny_with_reason "${OP} (${TOOL_NAME}) の target=\"${TARGET}\" は裸の name (相対セレクタ) です。裸の name は active タブ → 他タブの順に先勝ちでフォールスルーし、別組織の同名ペインや窓口を${HARM}うるため拒否します。${CONTRACT_NOTE}"
    ;;
  *)
    deny_with_reason "${OP} (${TOOL_NAME}) の target が数値でも文字列でもありません (${TARGET_TYPE})。安全側 (fail-closed) で拒否します。${CONTRACT_NOTE}"
    ;;
esac
