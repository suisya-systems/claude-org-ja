#!/usr/bin/env bash
# Codex セルフレビューを 1 回回し、「空の合格」を機械的に判定するゲート。
#
# 使い方（ワーカーの作業ディレクトリで前景実行する）:
#   bash tools/codex_review_gate.sh <base-ref>
#   例: bash tools/codex_review_gate.sh origin/main
#
# 終了コードと最終行:
#   0  CODEX_GATE: passed           成功実行 1 以上・失敗実行 0・codex の exit 0。指摘の中身を読んでよい
#   1  CODEX_GATE: not-established  未成立（空の合格を含む）。出力が合格に読めても「codex clean」と報告しない
#   2  使い方の誤り
#   3  CODEX_GATE: unavailable      codex CLI が無い。セルフレビューは skip してよい
#
# 「空の合格」: CODEX_HOME が書けないと codex は実行ヘルパーを置けず、コマンドを
# 1 つも実行しないまま「指摘なし」と読める文面で exit 0 する。但し書きも出ないこと
# があるため、エラーの不在ではなく「コマンドが実際に実行された」記録で判定する。
#   - 成功実行 = 行頭の `succeeded in <時間>`、失敗実行 = 行頭の `failed in <時間>`。
#     ログには diff 本文も載るので、行頭アンカーと実行時間の両方で自己マッチを避ける
#   - exit 0 は十分条件ではないが、非 0 は失格条件として使う
#
# CODEX_HOME は「書き込み可能」かつ「一時ディレクトリ配下でない」場所に退避する
# （codex は temp dir 配下へのヘルパー配置を拒否する。$TMPDIR に置くと正直なエラー
# ではなく空の合格に化ける）。$PWD/.codex-home は両方を満たす。commit しないこと。
# sandbox_mode=read-only は codex 内側のサンドボックスを締める設定で、緩めないこと。

set -uo pipefail

if [ "$#" -ne 1 ]; then
    printf 'usage: %s <base-ref>\n' "$0" >&2
    exit 2
fi
base_ref="$1"

if ! command -v codex >/dev/null 2>&1; then
    echo "CODEX_GATE: unavailable (codex CLI not found; skip self-review)"
    exit 3
fi

# 既存の（認証済みの）codex home をリンク元にしてから退避先へ切り替える。
codex_src="${CODEX_HOME:-$HOME/.codex}"
export CODEX_HOME="$PWD/.codex-home"
# worker ローカルの exclude に登録して誤 staging を防ぐ。linked worktree では exclude が
# sandbox 外にあって書けないことがあるので、失敗しても止めない。
exclude="$(git rev-parse --git-path info/exclude 2>/dev/null)"
if [ -n "$exclude" ] && ! grep -qxF '.codex-home/' "$exclude" 2>/dev/null; then
    echo '.codex-home/' >> "$exclude" 2>/dev/null || true
fi
mkdir -p "$CODEX_HOME"
# 呼び出し側が既に $PWD/.codex-home を指していたら張り直さない（自己参照リンクになる）。
if [ "$(cd "$codex_src" 2>/dev/null && pwd -P)" != "$(cd "$CODEX_HOME" && pwd -P)" ]; then
    ln -sf "$codex_src/auth.json"   "$CODEX_HOME/auth.json"
    ln -sf "$codex_src/config.toml" "$CODEX_HOME/config.toml"
fi

# $TMPDIR は並走 worker と共有なので、作業ディレクトリのフルパス由来の名前にする。
log="${TMPDIR:-/tmp}/codex-review-$(basename "$PWD")-$(printf %s "$PWD" | cksum | cut -d' ' -f1).log"

codex exec review --base "$base_ref" -m gpt-6-astra \
    -c model_reasoning_effort=medium -c sandbox_mode='"read-only"' \
    < /dev/null 2>&1 | tee "$log"
codex_status=$?

succeeded=$(grep -cE '^ *succeeded in [0-9]+(ms|s|m)' "$log")
failed=$(grep -cE '^ *failed in [0-9]+(ms|s|m)' "$log")

summary="succeeded=$succeeded failed=$failed codex_status=$codex_status log=$log"
if [ "$succeeded" -ge 1 ] && [ "$failed" -eq 0 ] && [ "$codex_status" -eq 0 ]; then
    echo "CODEX_GATE: passed ($summary)"
    exit 0
fi
echo "CODEX_GATE: not-established ($summary; HEAD=$(git rev-parse --short HEAD 2>/dev/null))"
exit 1
