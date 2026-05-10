#!/usr/bin/env bash
# PreToolUse Hook: git commit / push / merge / pull / am の verify-bypass
# フラグおよび HUSKY=0 / SKIP_SECRET_SCAN=1 系 env-var bypass をブロック
# 方式: exit 2 + stderr メッセージ でブロック
#
# 背景:
#   verify-bypass フラグは pre-commit / pre-push hook（Issue #69 の secret
#   スキャナを含む）をスキップする。ワーカー Claude が秘匿情報スキャンを
#   迂回して commit / push するのを防ぐため、Claude のツール呼び出し
#   レベルで拒否する。
#
#   Phase 2 (Refs claude-org-ja#379, worker-git-guardrails-design.md §5.2.3 /
#   §4.7): merge / pull / am も --no-verify でこの commit hook を迂回するため
#   検査対象に追加する。pull 自体は permissions.deny 側で原則ブロックされる
#   が、本フックは多層防御として redundant に持つ。
#   HUSKY=0 / SKIP_SECRET_SCAN=1 / NO_VERIFY=1 等の inline env-var による
#   bypass も検出する（commit メッセージに "SKIP_SECRET_SCAN=1" 等が
#   含まれる場合は false positive になり得るが、安全側に倒す）。
#
# 入力: stdin から PreToolUse JSON
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 検知方針:
#   1. Bash コマンド文字列を ; && || | 改行 でセグメントに分割する。
#      引用符（" / '）内の区切り文字は無視する（split_segments の awk 実装）。
#      これにより `echo --no-verify; git commit -m ok` のような複合コマンド
#      で別セグメントの文字列を拾う false positive と、
#      `git commit -m "a ; b" --no-verify` のような引用符内 separator での
#      回避（false negative）の両方を防ぐ。
#   2. 各セグメントについて、`git` トークン経由で commit/push サブコマンド
#      が呼ばれているかを判定し、同一セグメント内に verify-bypass フラグ
#      が独立トークンとして存在するときだけ拒否する。
#
# 既知の制限:
#   - サブコマンド判定は loose match（`git` トークンの後の任意の場所に
#     subcmd トークンが現れれば match）。`echo git commit --no-verify` の
#     ような同一セグメント内のリテラル文字列も拒否される。多層防御の
#     最後の壁としては false positive 寄りの方が安全であり、許容する。
#   - 同一 git invocation の引数（commit メッセージ本文等）に
#     verify-bypass の文字列を含めると false positive で拒否される。
#     その場合は別表現に書き換えること。
#   - $(...) や `...` のサブシェル境界、バックスラッシュエスケープは
#     扱わない（split_segments の制限を参照）。
#   - 人間が直接ターミナルで叩く場合は本フックは効かない。Claude Code の
#     Bash ツール経由でのみ作用する。

set -euo pipefail

# shellcheck source=lib/segment-split.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/segment-split.sh"

deny_with_reason() {
  local reason="$1"
  echo "ブロック: $reason" >&2
  exit 2
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
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# 全セグメントを 1 度収集してから:
#   1. 単純な VAR=value 形の代入をすべて抽出
#   2. 各セグメントで $VAR / ${VAR} を既知値に展開してから検査
# これにより `flag=--no-verify; git commit "$flag" -m x` のような
# 動的構築 bypass を catch する。
SEGMENTS=()
while IFS= read -r seg; do
  SEGMENTS+=("$seg")
done < <(printf '%s' "$COMMAND" | split_segments)

# eval / bash -c / sh -c の引数文字列を追加の検査対象セグメントとして
# 並列に取り出す。flatten_substitutions の gsub 副作用に依存せず、
# 明示的な関数で bypass 検出を独立化する（Phase 2a, Issue #79）。
while IFS= read -r unwrapped; do
  [[ -n "$unwrapped" ]] && SEGMENTS+=("$unwrapped")
done < <(printf '%s\n' "${SEGMENTS[@]}" | unwrap_eval_and_bashc)

ASSIGNMENTS=()
while IFS= read -r assign; do
  [[ -n "$assign" ]] && ASSIGNMENTS+=("$assign")
done < <(printf '%s\n' "${SEGMENTS[@]}" | collect_assignments)

for segment in "${SEGMENTS[@]}"; do
  [[ -z "$segment" ]] && continue

  # 既知の VAR=value を展開
  if [[ ${#ASSIGNMENTS[@]} -gt 0 ]]; then
    expanded=$(printf '%s' "$segment" | expand_known_vars "${ASSIGNMENTS[@]}")
  else
    expanded="$segment"
  fi

  # コマンド置換 $(...) / `...` 内のフラグも検査対象に含める
  flat=$(printf '%s' "$expanded" | flatten_substitutions)

  # git commit / push / merge / pull / am の有無（loose match）。展開後で判定する。
  has_git_commit=0
  has_git_push=0
  has_git_merge=0
  has_git_pull=0
  has_git_am=0
  if echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]]+commit([[:space:]]|$)' \
     || echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]]commit([[:space:]]|$)'; then
    has_git_commit=1
  fi
  if echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]]+push([[:space:]]|$)' \
     || echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]]push([[:space:]]|$)'; then
    has_git_push=1
  fi
  if echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]]+merge([[:space:]]|$)' \
     || echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]]merge([[:space:]]|$)'; then
    has_git_merge=1
  fi
  if echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]]+pull([[:space:]]|$)' \
     || echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]]pull([[:space:]]|$)'; then
    has_git_pull=1
  fi
  if echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]]+am([[:space:]]|$)' \
     || echo "$flat" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]]am([[:space:]]|$)'; then
    has_git_am=1
  fi
  has_any_git_subcmd=$(( has_git_commit + has_git_push + has_git_merge + has_git_pull + has_git_am ))
  [[ $has_any_git_subcmd -eq 0 ]] && continue

  # 同一セグメント（展開・フラット化後）に verify-bypass フラグが独立トークンとして存在するか
  if echo "$flat" | grep -qE '(^|[[:space:]])--no-verify([[:space:]]|$)'; then
    if [[ $has_git_commit -eq 1 ]]; then
      deny_with_reason "git commit の verify-bypass フラグは禁止です。pre-commit secret スキャナ（Issue #69）を必ず通してください。誤検知の場合は allow-secret マーカー、緊急時は SKIP_SECRET_SCAN=1 を使ってください。"
    elif [[ $has_git_push -eq 1 ]]; then
      deny_with_reason "git push の verify-bypass フラグは禁止です。pre-push hook を迂回するため拒否します（現在 pre-push は未配備ですが、将来追加された hook を保護する目的で先行ブロックします）。push が必要な場合は窓口経由で実施してください。"
    elif [[ $has_git_merge -eq 1 ]]; then
      deny_with_reason "git merge の verify-bypass フラグは禁止です。merge commit が pre-commit hook を迂回するため拒否します。"
    elif [[ $has_git_pull -eq 1 ]]; then
      deny_with_reason "git pull の verify-bypass フラグは禁止です。pull は fetch+merge/rebase であり、結果の commit が pre-commit hook を迂回するため拒否します。"
    else
      deny_with_reason "git am の verify-bypass フラグは禁止です。patch 適用時の pre-commit / pre-applypatch hook を迂回するため拒否します。"
    fi
  fi

done

# HUSKY=0 / SKIP_SECRET_SCAN=1 / NO_VERIFY=1 等の inline env-var bypass の検出は
# COMMAND 文字列全体に対して行う。`export HUSKY=0; git commit ...` のように
# 別セグメントで env を立ててから git を呼ぶ形式は per-segment ループでは
# catch できない（assign セグメントに git は無く、git セグメントに env name は
# 無いため）。COMMAND 全体に env name + git subcmd の両方が含まれる時点で
# 多層防御として一律拒否する。
COMMAND_FLAT=$(printf '%s' "$COMMAND" | flatten_substitutions)
if echo "$COMMAND_FLAT" | grep -qE '(^|[[:space:]])git[[:space:]]+(commit|push|merge|pull|am)([[:space:]]|$)' \
   || echo "$COMMAND_FLAT" | grep -qE '(^|[[:space:]])git[[:space:]].*[[:space:]](commit|push|merge|pull|am)([[:space:]]|$)'; then
  if echo "$COMMAND_FLAT" | grep -qE '(^|[[:space:]])HUSKY='; then
    deny_with_reason "HUSKY=... による pre-commit/pre-push hook 迂回は禁止です（HUSKY=0 / HUSKY=false 等で hook が無効化されます）。"
  fi
  if echo "$COMMAND_FLAT" | grep -qE '(^|[[:space:]])SKIP_SECRET_SCAN='; then
    # SKIP_SECRET_SCAN=1 は緊急時の人間オペレーション用 escape hatch であり、
    # ワーカー Claude のツール呼び出し経路では絶対に使ってはならない（Issue #69）。
    deny_with_reason "SKIP_SECRET_SCAN=... は人間用 escape hatch です。ワーカーは secret スキャナを必ず通してください（Issue #69）。誤検知時は allow-secret マーカーで個別対応してください。"
  fi
  if echo "$COMMAND_FLAT" | grep -qE '(^|[[:space:]])NO_VERIFY='; then
    deny_with_reason "NO_VERIFY=... による hook 迂回は禁止です。--no-verify と等価のため拒否します。"
  fi
fi

exit 0
