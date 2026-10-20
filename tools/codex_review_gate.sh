#!/usr/bin/env bash
# Codex セルフレビューを 1 回回し、「空の合格」を機械的に判定するゲート。
#
# 使い方（ワーカーの作業ディレクトリで前景実行する）:
#   bash tools/codex_review_gate.sh [<base-ref>]        差分レビュー（codex exec review）
#   bash tools/codex_review_gate.sh --design [<file>]   設計レビュー（codex exec。設計ファイルを
#                                                       リポジトリと突き合わせて検証させる）
#   例: bash tools/codex_review_gate.sh
#
# ワーカーが使うのは引数なしの形だけ。ワーカー設定はこの 2 形（引数なし・--design 単独）を
# 完全一致で sandbox の外に出している（codex 内の bwrap は sandbox の中では動かない）。
# 引数で任意の値を渡せる形を外に出すと、ワーカーが選んだ値で sandbox 外実行ができてしまうため:
#   - <base-ref> 省略時は state.db の delegate_sent（WORKER_DIR に一致する最新行）の base_ref を
#     tools/codex_round_cap.py base-ref で引く。記録が無い古い派遣は origin/main。state.db は
#     ワーカーから書けない ja root .state/ にあるので、ワーカーは値を書き換えられない
#   - <file> 省略時は $PWD/.worker-scratch/design.md を読む（中身はワーカーの文書で、codex に
#     読ませるだけ。read-only sandbox の codex に渡るのはパスの固定値なので引数の注入は起きない）
#
# 回数はワーカーの外のフック（.hooks/block-codex-over-cap.sh）が数える。差分レビューは
# 既定 3 回（窓口だけが変更できる）、設計レビューはそれとは別枠でタスクごとに 1 回固定。
#
# 設計モードは dirty-worktree 判定をしない（設計は実装前や実装途中に回すため）。
# 「空の合格」の判定と最終行の形は差分モードと同じで、ログ名だけ別にする。
#
# 終了コードと最終行:
#   0  CODEX_GATE: passed           成功実行 1 以上・失敗実行 0・codex の exit 0。指摘の中身を読んでよい
#   1  CODEX_GATE: not-established  未成立（空の合格を含む）。出力が合格に読めても「codex clean」と報告しない
#   2  使い方の誤り（設計モードで設計ファイルが無い・読めないときも）
#   3  CODEX_GATE: unavailable      codex CLI が無い。セルフレビューは skip してよい
#   4  CODEX_GATE: dirty-worktree   追跡ファイルに未コミット差分がある。codex は回さない
#
# dirty-worktree: commit 後に回す前提なので、追跡ファイルの差分は rebase 残差とみなす。
# sandbox が書込みを拒否するパス（.claude/skills 等）は rebase が成功表示でも旧内容の
# まま残り、そのまま回すと codex が古い差分までレビューする（Issue #1151）。
# 未追跡は見ない（sandbox が置く空のマウント点が未追跡として出るため）。
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

usage() {
    printf 'usage: %s [<base-ref>] | --design [<design-file>]\n' "$0" >&2
    exit 2
}
mode=diff
base_ref=
if [ "$#" -ge 1 ] && [ "$#" -le 2 ] && [ "$1" = "--design" ]; then
    mode=design
    design_file="${2:-.worker-scratch/design.md}"
    if [ ! -f "$design_file" ] || [ ! -r "$design_file" ]; then
        printf 'design file not found or unreadable: %s\n' "$design_file" >&2
        usage
    fi
elif [ "$#" -eq 1 ]; then
    base_ref="$1"
elif [ "$#" -ne 0 ]; then
    usage
fi

if [ "$mode" = diff ] && [ -z "$base_ref" ]; then
    gate_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    py=python3
    command -v "$py" >/dev/null 2>&1 || py=python
    if ! base_ref="$("$py" "$gate_dir/codex_round_cap.py" base-ref --worker-dir "${WORKER_DIR:-$PWD}")" \
        || [ -z "$base_ref" ]; then
        echo "CODEX_GATE: not-established (cannot resolve the task's base ref from state.db)"
        exit 1
    fi
    echo "codex_review_gate: base ref $base_ref (from delegate_sent)" >&2
fi

if [ "$mode" = diff ]; then
    if ! dirty="$(git status --porcelain --untracked-files=no)"; then
        echo "CODEX_GATE: not-established (git status failed; cannot rule out rebase residue)"
        exit 1
    fi
    if [ -n "$dirty" ]; then
        printf '%s\n' "$dirty"
        echo "CODEX_GATE: dirty-worktree (tracked changes above; likely rebase residue on sandbox-protected paths; HEAD=$(git rev-parse --short HEAD 2>/dev/null))"
        exit 4
    fi
fi

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
# リンク元は絶対パスにする（相対の CODEX_HOME はリンク先ディレクトリ基準で解決されてしまう）。
# 呼び出し側が既に $PWD/.codex-home を指していたら張り直さない（自己参照リンクになる）。
src_abs="$(cd "$codex_src" 2>/dev/null && pwd -P)"
if [ -n "$src_abs" ] && [ "$src_abs" != "$(cd "$CODEX_HOME" && pwd -P)" ]; then
    ln -sf "$src_abs/auth.json"   "$CODEX_HOME/auth.json"
    ln -sf "$src_abs/config.toml" "$CODEX_HOME/config.toml"
fi

# $TMPDIR は並走 worker と共有なので、作業ディレクトリのフルパス由来の名前にする。
log="${TMPDIR:-/tmp}/codex-review-$(basename "$PWD")-$(printf %s "$PWD" | cksum | cut -d' ' -f1)"

if [ "$mode" = design ]; then
    log="$log-design.log"
    prompt="Read the design document at $design_file and verify it against this repository: check that every file, function, flag, hook, path and behaviour it relies on actually exists and works as the design assumes, and that the design achieves its stated goal without gaps. List findings as Blocker / Major / Minor, each with the evidence (file:line) and a concrete fix."
    codex exec -m gpt-6-astra \
        -c model_reasoning_effort=high -c sandbox_mode='"read-only"' \
        "$prompt" < /dev/null 2>&1 | tee "$log"
    codex_status=$?
else
    log="$log.log"
    codex exec review --base "$base_ref" -m gpt-6-astra \
        -c model_reasoning_effort=medium -c sandbox_mode='"read-only"' \
        < /dev/null 2>&1 | tee "$log"
    codex_status=$?
fi

succeeded=$(grep -cE '^ *succeeded in [0-9]+(ms|s|m)' "$log")
failed=$(grep -cE '^ *failed in [0-9]+(ms|s|m)' "$log")

summary="succeeded=$succeeded failed=$failed codex_status=$codex_status log=$log"
if [ "$succeeded" -ge 1 ] && [ "$failed" -eq 0 ] && [ "$codex_status" -eq 0 ]; then
    echo "CODEX_GATE: passed ($summary)"
    exit 0
fi
echo "CODEX_GATE: not-established ($summary; HEAD=$(git rev-parse --short HEAD 2>/dev/null))"
exit 1
