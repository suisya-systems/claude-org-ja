#!/usr/bin/env bash
# PreToolUse Hook: ワーカーの Codex レビュー往復上限を強制し、codex の直接実行をブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   Codex セルフレビューの往復上限 (既定 3) は brief の文章だけでは守られなかった。
#   回数の数え上げと上限超過の拒否をワーカーの外 (このフック) で行う。フックは
#   サンドボックスの外で動くので、ワーカーからは読み取り専用の ja root
#   `.state/state.db` に events を書ける。
#
# 対象: Bash の command、MCP の spawn_pane / new_tab の command、send_keys の text。
#   MCP の spawn_codex_pane は常に deny (ワーカーは Codex ペインを起動しない)。
# 判定 (`tools/codex_round_cap.py hook` に委譲。引用符等を除いて `codex` を含むときだけ):
#   - 既定 deny: `codex` 語を含む単純コマンドは、引数を実行しない読み取り系
#     (grep / cat / echo / git / which / `command -v` / `npm i` 等) 以外 deny。
#     `bash -c` 等の文字列は入れ子で再判定し、標準入力やヒアドキュメントで
#     `codex` を含むテキストをシェルに流す形と `$(codex ...)` も deny
#   - Codex ゲートは `bash "$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" <base-ref>`（差分）と
#     `... --design <file>`（設計。パスは空白・シェル記号を含まない 1 語）の
#     単独の形だけ通す。それ以外の形 (ワーカー側のコピー・相対パス・他コマンドとの連結) は deny
#   - 正規のゲート呼び出しは 1 回ごとに `codex_round_started` を記録して通す。
#     許可回数 (既定 3、窓口が `tools/codex_round_cap.py set` で変更) を超えたら
#     `codex_round_denied` を記録して deny (CODEX_ROUND_DENIED)。設計レビューは別枠で
#     タスクごとに 1 回固定 (payload の mode=design)。差分の許可回数を消費も参照もしない
#
# 既知の限界: 変数展開・グロブ・ブレース展開・$'...'・別ファイルに書いたスクリプト・
#   他言語で組み立てた文字列・許可リストのコマンドの実行機能 (git alias 等) 経由の
#   codex 実行はコマンド文字列から判別できない (tools/codex_round_cap.py の docstring)。
#
# 範囲: ワーカー (`tools/org_extension_schema.json` の worker_roles から配る)。ja 専用
#   (`tools/check_runtime_schema_drift.py` の `_JA_ONLY_HOOK_SCRIPTS`)。
#
# 入力: stdin から PreToolUse JSON ({tool_name, tool_input})
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# fail-closed: jq 欠落 / 空 stdin / 不正 JSON / 非 object payload / 環境変数欠落 /
# python 欠落・異常終了・空出力はすべて deny する
# (`tests/test-hooks-payload-fail-closed.sh` が payload 側を全 enforcement hook 横断で固定する)。

set -euo pipefail

deny_with_reason() {
  echo "ブロック: $1" >&2
  exit 2
}

INPUT=$(cat)

if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。Bash 呼び出しは安全側 (fail-closed) で拒否します。"
fi

if ! command -v jq &>/dev/null; then
  deny_with_reason "jq がインストールされていません。セキュリティ Hook の実行に必要です。"
fi

if ! printf '%s\n' "$INPUT" | jq -e -s 'length == 1 and (.[0] | type) == "object" and (.[0].tool_input == null or (.[0].tool_input | type) == "object")' >/dev/null 2>&1; then
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。Bash 呼び出しは安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
case "$TOOL_NAME" in
  Bash|mcp__*__spawn_pane|mcp__*__new_tab) FIELD=command ;;
  mcp__*__send_keys) FIELD=text ;;
  mcp__*__spawn_codex_pane)
    deny_with_reason "ワーカーは Codex ペインを起動できません。Codex レビューは bash \"\$CLAUDE_ORG_PATH/tools/codex_review_gate.sh\" <base-ref> だけで回してください。" ;;
  *) exit 0 ;;
esac

COMMAND=$(printf '%s\n' "$INPUT" | jq -r --arg f "$FIELD" '.tool_input[$f] // empty | tostring')
# 速い経路: 引用符・バックスラッシュ・改行を除いて `codex` を含まないコマンドは python を起動せずに通す
# (`co""dex` / `c\odex` / 行継続で分けた形も python 側の判定に回す)。
STRIPPED=$(printf '%s' "$COMMAND" | tr -d "\"'\\\\\n")
[[ "$STRIPPED" == *codex* ]] || exit 0

if [[ -z "${WORKER_DIR:-}" || -z "${CLAUDE_ORG_PATH:-}" ]]; then
  deny_with_reason "WORKER_DIR または CLAUDE_ORG_PATH が設定されていません。"
fi

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# tools/journal_append.sh と同じ interpreter の選び方 (Windows は py -3 / python)。
if command -v py >/dev/null 2>&1; then
  PY=(py -3)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1; then
  PY=(python)
else
  deny_with_reason "python が見つかりません。Codex 往復上限を判定できないため拒否します。"
fi

# 判定は stdout の 1 行目だけで読む (stderr の警告で ALLOW が 2 行目にずれて誤 deny しないよう分ける)。
ERR_FILE=$(mktemp)
trap 'rm -f "$ERR_FILE"' EXIT
OUT=$(printf '%s\n' "$INPUT" | "${PY[@]}" "$HOOK_DIR/../tools/codex_round_cap.py" hook 2>"$ERR_FILE") || true
FIRST_LINE=$(printf '%s\n' "$OUT" | head -n 1)
if [[ "$FIRST_LINE" == "ALLOW" ]]; then
  exit 0
fi
if [[ -z "$OUT" ]]; then
  deny_with_reason "Codex 往復上限の判定が出力を返しませんでした。安全側で拒否します。$(head -c 500 "$ERR_FILE")"
fi
deny_with_reason "${OUT#DENY: }"
