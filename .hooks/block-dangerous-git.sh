#!/usr/bin/env bash
# PreToolUse Hook: 破壊的な git 操作をブロック
# 方式: exit 2 + stderr メッセージ でブロック
#
# ブロック対象:
#   - git push --force / -f                         （履歴書き換え、無条件 deny）
#   - git push --force-with-lease （protected branch / ambiguous のみ deny / Issue #470）
#       protected = main / master / develop / release/* / production
#       非保護 branch への --force-with-lease は許容（PR rebase 後の安全な再 push）
#       下記いずれかに該当する場合は安全側で deny:
#         - refspec を解決できない（remote のみ / 引数無し）
#         - target が HEAD / @ （実行時の current branch 依存）
#         - target が wildcard（refs/heads/* / : 等の matching/delete-all）
#         - --all / --mirror / --tags（upstream 全体に作用）
#         - refspec トークンに引用符 / $展開 / `...` 等を含み静的解析不能
#         - destination が refs/heads/ 以外の namespace（refs/tags/* 等の
#           tag / notes / replace 等の任意 ref。--force-with-lease の本来の
#           用途は branch のみ）
#         - `git push origin tag <name>` の "tag" キーワード形式
#   - git reset --hard                              （未コミット変更の消失）
#   - git branch -D / --delete --force              （未マージブランチ削除）
#   - git clean -f / -fd / -fx / -dfx 等            （ワークツリー破壊）
#   - git checkout -- <path> / git checkout -- .    （未コミット変更破棄）
#   - git restore --worktree --source=<ref> .       （--source 指定の同上）
#   - git tag -d / --delete                         （共有タグ namespace 改変）
#   - git update-ref -d                             （任意 ref 削除）
#   - git reflog expire/delete --all/--expire=now   （audit trail 改変）
#
# 補足:
#   - git push そのものはワーカーでは block-git-push.sh が先に止める。
#     本フックは「窓口側でうっかり叩いた場合の最後の壁」も兼ねる。
#   - --force-with-lease の条件付き許可は Issue #470 で導入。
#     上流 ref を確認してから強制 push するため --force より安全であり、
#     PR レビュー指摘の rebase / squash 後の再 push 等で正当な需要がある。
#     ただし protected branch への適用は引き続き禁止（共有履歴の保護）。
#   - Phase 2 (Refs claude-org-ja#379): clean -fd / checkout -- . / tag -d /
#     update-ref -d / reflog expire 等のカバレッジを追加した。
#     詳細は docs/contracts/worker-git-guardrails-design.md §5.2.2 参照。
#
# 入力: stdin から PreToolUse JSON
# 出力: 拒否時 exit 2 + stderr。許可時 exit 0。
#
# 検知方針:
#   1. Bash コマンド文字列を ; && || | 改行 でセグメントに分割する。
#      引用符（" / '）内の区切り文字は無視する（split_segments の awk 実装）。
#      これにより `echo --force; git push origin main` のような複合コマンド
#      で別セグメントの文字列を拾う false positive と、
#      `git push origin "refs/heads/x; y" --force` のような引用符内
#      separator での回避（false negative）の両方を防ぐ。
#   2. 各セグメントについて、`git` トークン経由で push/reset/branch
#      サブコマンドが呼ばれているかを判定し、同一セグメント内に
#      対応する破壊的フラグが独立トークンとして存在するときだけ拒否する。
#
# 既知の制限:
#   - サブコマンド判定は loose match。同一セグメント内のリテラル文字列も
#     拒否される。多層防御の最後の壁としては false positive 寄りで安全。
#   - 同一 git invocation の引数（commit メッセージ本文等）に
#     "--force" 等の文字列を含めると false positive で拒否される。
#     その場合は別表現に書き換えること。
#   - $(...) や `...` のサブシェル境界、バックスラッシュエスケープは
#     扱わない。

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

# Issue #470: protected branch（共有履歴）への --force-with-lease は引き続き
# deny する。非保護 branch（feature/* 等）への --force-with-lease は許容。
# protected branch 名は本ファイル内で完結（環境変数経由の override は意図的に
# 提供しない: hook の振る舞いを env で動的に変えると drift 検出が難しくなるため）。
# master は git の歴史的デフォルト名で main の同義として扱う組織が多いため保護
# 対象に含める（task brief の main/develop/release/*/production を超える保護
# 拡張だが、誤検知に倒すリスクの方が小さい）。
PROTECTED_BRANCH_PATTERNS=(
  "main"
  "master"
  "develop"
  "production"
  # release/* は別途 case でマッチ
)

# git push の引数群を解析し、deny が必要かを判定する。
#
# 入力: flat（コマンド置換等が展開済みの 1 セグメント文字列）
# 戻り値: 0 = deny 推奨（protected branch を含む / refspec を決定できない / 不審
#               な refspec が混在 / wildcard・matching push 等の広域 push）
#         1 = 明確に非保護 branch のみへの push（allow 可）
#
# 安全側方針: allow と判断するためには「全 positional refspec が確実に非保護
# branch を指している」ことが必要。少しでも曖昧さがあれば deny に倒す。
push_target_requires_deny() {
  local flat="$1"

  # --all / --mirror / --tags は upstream 全体に作用するため protected branch を
  # 含み得る → 無条件 deny
  if echo "$flat" | grep -qE '(^|[[:space:]])--(all|mirror|tags)([[:space:]=]|$)'; then
    return 0
  fi

  # `git ... push <args...>` の <args> 部分を切り出す（awk でトークン化）。
  # awk は引用符を理解しないため "..."/'...' を含む refspec は次の段で
  # 個別に ambiguous deny に倒す。
  local args_str
  args_str=$(printf '%s\n' "$flat" | awk '
    {
      found=0
      out=""
      for(i=1;i<=NF;i++){
        if(!found){
          if($i=="push") { found=1 }
        } else {
          out = out " " $i
        }
      }
      print substr(out,2)
    }')

  # 非フラグの positional トークンを収集。--force-with-lease=<ref> 等の attached
  # value はフラグ側に属するため positional には数えない。
  local positional=()
  local tok
  for tok in $args_str; do
    case "$tok" in
      -*) continue ;;
      *) positional+=("$tok") ;;
    esac
  done

  # 0 positional → `git push` のみ（upstream of HEAD を使用）→ ambiguous
  # 1 positional → remote のみ・refspec 無し → ambiguous（push.default 依存）
  if (( ${#positional[@]} < 2 )); then
    return 0
  fi

  # positional[0] = remote、それ以降が refspec
  local refspec target pat dst
  local idx
  for (( idx=1; idx<${#positional[@]}; idx++ )); do
    refspec="${positional[$idx]}"
    # 先頭の '+' force prefix を剥がす（refspec 内 force 指定の慣習）
    refspec="${refspec#+}"

    # 引用符・$ 展開・`...`・コマンド置換の残骸を含むトークンは
    # シェル構文を再解釈しないと安全に判定できない → ambiguous deny
    case "$refspec" in
      *\"*|*\'*|*\$*|*\`*) return 0 ;;
    esac

    # `git push origin tag <name>` 形式の "tag" キーワード → ambiguous deny
    # （branch 以外の namespace、--force-with-lease の本来の用途外）
    if [[ "$refspec" == "tag" ]]; then
      return 0
    fi

    # `<src>:<dst>` 形式 → dst が destination。`:<dst>` (delete) や
    # `<src>:<dst>` も最後の `:` 以降を採用
    dst="${refspec##*:}"

    # 空 destination（`:` で matching push / delete 全件等）→ ambiguous deny
    if [[ -z "$dst" ]]; then
      return 0
    fi

    # wildcard を含む destination は protected branch を含み得る → ambiguous deny
    if [[ "$dst" == *\** || "$dst" == *\?* || "$dst" == *\[* ]]; then
      return 0
    fi

    # HEAD / @ は実行時の current branch 依存 → ambiguous deny
    if [[ "$dst" == "HEAD" || "$dst" == "@" ]]; then
      return 0
    fi

    # destination の namespace 判定
    # --force-with-lease の正当な用途は branch (refs/heads/) のみ。
    # refs/tags/* / refs/notes/* / refs/remotes/* / refs/replace/* /
    # refs/pull/* 等の任意 ref は共有 namespace の改変リスクがあるため
    # ambiguous deny。
    case "$dst" in
      refs/heads/*)
        # branch namespace、prefix を剥がして name のみで protected 判定
        target="${dst#refs/heads/}"
        ;;
      refs/*)
        # branch 以外の任意 ref → deny
        return 0
        ;;
      *)
        # prefix 無しの bare name → git のデフォルト解決で
        # refs/heads/<name> へ push される branch shorthand
        target="$dst"
        ;;
    esac

    # protected branch 一致判定
    for pat in "${PROTECTED_BRANCH_PATTERNS[@]}"; do
      if [[ "$target" == "$pat" ]]; then
        return 0
      fi
    done
    if [[ "$target" == release/* ]]; then
      return 0
    fi
  done

  return 1
}

# セグメントの中に git の特定サブコマンドが含まれるか判定するヘルパ
segment_has_git_subcmd() {
  local segment="$1"
  local subcmd="$2"
  # 直接形: `git <subcmd> ...`
  if echo "$segment" | grep -qE "(^|[[:space:]])git[[:space:]]+${subcmd}([[:space:]]|$)"; then
    return 0
  fi
  # オプション介在形: `git -C "..." <subcmd> ...`（引用符込み空白入りパス対応）
  if echo "$segment" | grep -qE "(^|[[:space:]])git[[:space:]].*[[:space:]]${subcmd}([[:space:]]|$)"; then
    return 0
  fi
  return 1
}

# 全セグメントを 1 度収集してから既知の代入を抽出し、各セグメントで展開する。
SEGMENTS=()
while IFS= read -r seg; do
  SEGMENTS+=("$seg")
done < <(printf '%s' "$COMMAND" | split_segments)

# eval / bash -c / sh -c の引数文字列を追加の検査対象セグメントとして
# 並列に取り出す（Phase 2a, Issue #79）。
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

  # 1) git push の force 系（Issue #470 で --force-with-lease を条件付き許可）
  if segment_has_git_subcmd "$flat" "push"; then
    has_force_with_lease=0
    has_plain_force=0

    # --force-with-lease は --force より先に判定する（部分一致回避）
    if echo "$flat" | grep -qE '(^|[[:space:]])--force-with-lease([[:space:]=]|$)'; then
      has_force_with_lease=1
    fi
    # 素の --force（--force-with-lease は除外: 後続文字が '-' で regex 不一致）
    if echo "$flat" | grep -qE '(^|[[:space:]])--force([[:space:]=]|$)'; then
      has_plain_force=1
    fi
    # 短縮形 -f 単独
    if echo "$flat" | grep -qE '(^|[[:space:]])-f([[:space:]]|$)'; then
      has_plain_force=1
    fi
    # バンドル短オプション内に 'f' を含む（-uf 等）
    if echo "$flat" | grep -qE '(^|[[:space:]])-[a-zA-Z]*f[a-zA-Z]*([[:space:]]|$)'; then
      has_plain_force=1
    fi

    # 素の force は無条件 deny。Issue #470 でも維持。
    if (( has_plain_force == 1 )); then
      deny_with_reason "git push の素の force フラグ（--force / -f / バンドル短オプション）は禁止です。--force-with-lease（非保護 branch のみ許可）を使ってください。protected branch への履歴書き換えはレビュー後に窓口経由で実施してください。"
    fi

    # --force-with-lease は protected branch にのみ deny
    if (( has_force_with_lease == 1 )); then
      if push_target_requires_deny "$flat"; then
        deny_with_reason "git push --force-with-lease は protected branch (main / master / develop / release/* / production) や refspec 未指定 / HEAD / wildcard / --all / --mirror / --tags など宛先が曖昧なケースでは禁止です。push 先 branch を明示し、非保護 branch のみに使用してください。"
      fi
    fi
  fi

  # 2) git reset --hard
  if segment_has_git_subcmd "$flat" "reset"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])--hard([[:space:]=]|$)'; then
      deny_with_reason "git reset --hard は禁止です。未コミット変更が失われます。git stash か別ブランチへの退避を検討してください。"
    fi
  fi

  # 3) git branch -D / git branch --delete --force
  if segment_has_git_subcmd "$flat" "branch"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])-D([[:space:]]|$)'; then
      deny_with_reason "git branch -D は禁止です。未マージのブランチが消えます。-d（小文字）で安全削除を試すか、窓口に確認してください。"
    fi
    if echo "$flat" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)' && \
       echo "$flat" | grep -qE '(^|[[:space:]])--force([[:space:]=]|$)'; then
      deny_with_reason "git branch --delete --force は禁止です（-D 相当）。-d で安全削除を試すか、窓口に確認してください。"
    fi
  fi

  # 4) git clean -f / -fd / -fx / -dfx ...（ワークツリー破壊）
  if segment_has_git_subcmd "$flat" "clean"; then
    # 長形式 --force / 短形式 -f（単独）
    if echo "$flat" | grep -qE '(^|[[:space:]])--force([[:space:]=]|$)'; then
      deny_with_reason "git clean --force は禁止です。未追跡ファイルが失われます。事前に内容確認してから個別に削除してください。"
    fi
    if echo "$flat" | grep -qE '(^|[[:space:]])-f([[:space:]]|$)'; then
      deny_with_reason "git clean -f は禁止です。未追跡ファイルが失われます。事前に内容確認してから個別に削除してください。"
    fi
    # バンドル短オプション（-fd / -dfx 等、f を含む）
    if echo "$flat" | grep -qE '(^|[[:space:]])-[a-zA-Z]*f[a-zA-Z]*([[:space:]]|$)'; then
      deny_with_reason "git clean のバンドル短オプションに force フラグが含まれています。未追跡ファイルが失われます。"
    fi
  fi

  # 5) git checkout -- <path> / git checkout -- . （未コミット変更の破棄）
  if segment_has_git_subcmd "$flat" "checkout"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])--([[:space:]]|$)'; then
      deny_with_reason "git checkout -- <path> は禁止です。未コミット変更が失われます。git stash / git diff で退避を検討してください。"
    fi
  fi

  # 6) git restore --source=<ref> ... （checkout -- 相当の worktree 上書き）
  # git restore のデフォルトモードは --worktree（--staged 単独でない限り
  # worktree 書き換えが発生）なので、--source / -s が指定された restore は
  # 一律拒否する。--staged 単独の場合のみ除外（index のみ書き換えで未コミット
  # 変更は失われない）。
  # `-s` の attached-arg 形式（例: `-sHEAD~1`）も catch するため、`-s` の後に
  # スペース / `=` / 任意の非空白文字 / 行末いずれが続く場合も拾う。
  if segment_has_git_subcmd "$flat" "restore"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])(--source([[:space:]=])|-s([[:space:]=]|$|[^[:space:]]))'; then
      # --staged が独立トークンとして存在し、かつ --worktree / -W が無い場合のみ pass
      if echo "$flat" | grep -qE '(^|[[:space:]])(--staged|-S)([[:space:]]|$)' \
         && ! echo "$flat" | grep -qE '(^|[[:space:]])(--worktree|-W)([[:space:]]|$)'; then
        : # index-only restore: 安全
      else
        deny_with_reason "git restore --source=<ref> は禁止です。未コミット変更が <ref> 内容で上書きされ失われます。index のみの restore は --staged 単独で実行してください。"
      fi
    fi
  fi

  # 7) git tag -d / --delete （共有タグ namespace 改変）
  if segment_has_git_subcmd "$flat" "tag"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])-d([[:space:]]|$)'; then
      deny_with_reason "git tag -d は禁止です。共有タグ namespace を改変します。タグの追加/削除は窓口経由で実施してください。"
    fi
    if echo "$flat" | grep -qE '(^|[[:space:]])--delete([[:space:]]|$)'; then
      deny_with_reason "git tag --delete は禁止です。共有タグ namespace を改変します。タグの追加/削除は窓口経由で実施してください。"
    fi
  fi

  # 8) git update-ref -d （任意 ref 削除）
  if segment_has_git_subcmd "$flat" "update-ref"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])-d([[:space:]]|$)'; then
      deny_with_reason "git update-ref -d は禁止です。任意の ref を直接削除する低レベル escape hatch であり、ワーカーの作業範囲外です。"
    fi
    if echo "$flat" | grep -qE '(^|[[:space:]])--stdin([[:space:]]|$)'; then
      deny_with_reason "git update-ref --stdin は禁止です。任意 ref をバッチ書換する低レベル escape hatch であり、ワーカーの作業範囲外です。"
    fi
  fi

  # 9) git reflog expire/delete --all / --expire=now / --expire-unreachable=now （audit trail 改変）
  if segment_has_git_subcmd "$flat" "reflog"; then
    if echo "$flat" | grep -qE '(^|[[:space:]])(expire|delete)([[:space:]]|$)'; then
      if echo "$flat" | grep -qE '(^|[[:space:]])--all([[:space:]]|$)' \
         || echo "$flat" | grep -qE '(^|[[:space:]])--expire(=|[[:space:]])(now|0)' \
         || echo "$flat" | grep -qE '(^|[[:space:]])--expire-unreachable(=|[[:space:]])(now|0)'; then
        deny_with_reason "git reflog expire/delete --all / --expire=now は禁止です。reflog audit trail が失われます。"
      fi
    fi
  fi
done

exit 0
