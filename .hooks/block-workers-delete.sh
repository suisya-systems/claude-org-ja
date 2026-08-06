#!/usr/bin/env bash
# PreToolUse Hook: workers ディレクトリ配下の再帰的ディレクトリ削除をブロックする
# 対象: 窓口（Secretary）の Bash コマンド
# 方式: exit 2 + stderr メッセージ でブロック
#
# ブロック対象:  rm -rf workers/..., rm -r workers/... 等（再帰削除）
# 許可:          rm workers/dir/file.txt 等（個別ファイル削除）

set -euo pipefail

deny_with_reason() {
  echo "ブロック: $1" >&2
  exit 2
}

portable_realpath() {
  local target="$1"
  if result=$(command realpath -m "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  elif result=$(python -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null); then
    echo "$result"
  else
    echo "ブロック: realpath も python も利用できません。" >&2
    exit 2
  fi
}

# stdin から JSON を読み取り
INPUT=$(cat)

# 空 payload の fail-closed ガード (Issue #834)。jq は「JSON 値がゼロ個」の入力を
# parse error にせず exit 0 + 出力なしで返すため、空 stdin では TOOL_NAME が空文字に
# なり、`!= "Bash"` の passthrough に落ちて enforcement が素通りする。
# 下の jq 未インストール時 fail-open は「環境全体で常に成立し、窓口の全 Bash を
# 止めてしまう」条件なので許容しているが、空 payload は個々の呼び出しが壊れている
# ケースであり正規のツール呼び出しではありえない。両者は別条件なので、ここは
# 兄弟フックと同じく fail-closed に倒す。
#
# このガードは jq チェックより「前」に置く必要がある。後ろに置くと jq なし環境で
# `exit 0` が先に走り、空 payload がガードに到達せず素通りしてしまう
# （jq の有無に依存しない不変条件にするための順序であり、jq がある環境での
# 挙動は前後どちらでも同じ）。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。安全側 (fail-closed) で拒否します。"
fi

# jq チェック（jq がなければこの Hook をスキップして許可する）
# 他の Hook（check-worker-boundary 等）は fail-closed だが、この Hook は窓口の全 Bash コマンドに
# 適用されるため、jq 未インストール時に全コマンドをブロックするのは過剰。
# jq なし環境でもスキルの文言による指示レベルの保護は残る。
if ! command -v jq &>/dev/null; then
  exit 0
fi

# 不正 JSON / 非 object payload の fail-closed ガード (Issue #834)。
# `VAR=$(printf '%s\n' "$INPUT" | jq ...)` は parse error (exit 4) や非 object への index
# error (exit 5) で set -e により script ごと中断し、PreToolUse では exit != 2 が
# 非ブロッキング扱い = fail-open になる。top-level が null のときは jq が index を
# 許すため error にすらならず、抽出結果が空になって passthrough に落ちる。
# そこで抽出の前に「top-level が object」かつ「tool_input が object または欠落」を
# 一括検査する。jq の `and` は短絡評価なので、左が false のとき右の index は評価
# されず error にならない。tool_input 欠落 (null) は正常な payload の一形態なので
# 従来どおり許容し、フィールド抽出が空になる既存の passthrough 経路に任せる。
# 導出の詳細は block-foreground-subagent.sh の同じガード。
#
# 上の空 payload ガードと違い、こちらは jq チェックより「後ろ」に置く。判定に jq 自身が
# 要るので前に出せないため。jq なし環境ではこの Hook 全体が意図的に無効化される設計
# （上の jq チェックのコメント参照）なので、これは新たな穴ではなくその設計の帰結である。
# 一方、空 payload ガードは jq 不要なので前に置き、jq の有無に依らない不変条件にしてある。
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
  deny_with_reason "PreToolUse payload を JSON object として解析できませんでした (tool_input が object でない場合を含む)。安全側 (fail-closed) で拒否します。"
fi

TOOL_NAME=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')

if [[ "$TOOL_NAME" != "Bash" ]]; then
  exit 0
fi

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# renga コマンドは除外する
# ワーカー起動時に --cwd workers/... と -p "...rm..." が共存し偽陽性を起こすため
#
# 例外の成立条件は「コマンド列のどこかに renga トークンがある」ではなく
# 「トップレベルの全セグメントが renga 起動である」こと。
# 前者だと `echo renga ; rm -rf workers/` のように無害な renga トークンを 1 つ混ぜるだけで
# ガード全体がスキップされる（回避経路）。
# セグメント分割は引用符を尊重するため、renga の -p / --command 引数に含まれる
# `;` や `&&` では分割されず、正当なワーカー起動は従来どおり通過する。

# コマンドをトップレベル（引用符の外）の制御演算子 ; & | 改行 で分割し、1 行 1 セグメントで出力する
split_top_level_segments() {
  local cmd="$1"
  local len=${#cmd}
  local seg="" quote="" ch prev i
  for ((i = 0; i < len; i++)); do
    ch="${cmd:i:1}"
    if [[ -n "$quote" ]]; then
      # ダブルクォート内の \" は閉じ引用符ではない（シングルクォート内の \ はただの文字）
      if [[ "$quote" == '"' && "$ch" == '\' ]] && ((i + 1 < len)); then
        seg+="$ch${cmd:i+1:1}"
        i=$((i + 1))
        continue
      fi
      seg+="$ch"
      [[ "$ch" == "$quote" ]] && quote=""
      continue
    fi
    case "$ch" in
      "'"|'"')
        quote="$ch"
        seg+="$ch"
        ;;
      '\')
        # エスケープ: 次の 1 文字は区切りとして解釈しない
        seg+="$ch"
        if ((i + 1 < len)); then
          seg+="${cmd:i+1:1}"
          i=$((i + 1))
        fi
        ;;
      '&')
        # リダイレクトの & （2>&1 / &>file 等）は区切りではない
        prev=""
        ((i > 0)) && prev="${cmd:i-1:1}"
        if [[ "$prev" == ">" || "$prev" == "<" || "${cmd:i+1:1}" == ">" ]]; then
          seg+="$ch"
        else
          printf '%s\n' "$seg"
          seg=""
        fi
        ;;
      ';'|'|'|$'\n')
        printf '%s\n' "$seg"
        seg=""
        ;;
      *)
        seg+="$ch"
        ;;
    esac
  done
  printf '%s\n' "$seg"
}

# セグメントの実行コマンドが renga かを判定する
# 先頭の環境変数代入（VAR=value）と env / command プレフィックスは読み飛ばす
segment_is_renga() {
  local seg="$1" word
  local words=()
  read -ra words <<< "$seg"
  for word in "${words[@]}"; do
    case "$word" in
      *=*) continue ;;
      env|command) continue ;;
      renga|*/renga) return 0 ;;
      *) return 1 ;;
    esac
  done
  return 1
}

# コマンド置換 / プロセス置換が「シェルに評価される形」で含まれるかを判定する
# $(...) / `...` / <(...) / >(...) は renga が起動される前にシェルが実行するため、
# renga 例外の内側であっても破壊的コマンドの実行経路になる。
# シングルクォートの内側は展開されないので不活性として扱う。
has_active_substitution() {
  local cmd="$1"
  local len=${#cmd}
  local state="none" ch next i
  for ((i = 0; i < len; i++)); do
    ch="${cmd:i:1}"
    next="${cmd:i+1:1}"
    case "$state" in
      single)
        [[ "$ch" == "'" ]] && state="none"
        continue
        ;;
      double)
        if [[ "$ch" == '\' ]]; then
          i=$((i + 1))
          continue
        fi
        [[ "$ch" == '"' ]] && state="none"
        ;;
      none)
        case "$ch" in
          "'") state="single"; continue ;;
          '"') state="double"; continue ;;
          '\') i=$((i + 1)); continue ;;
          '<'|'>')
            [[ "$next" == "(" ]] && return 0
            continue
            ;;
        esac
        ;;
    esac
    # single 以外（none / double）では $( と ` がシェルに評価される
    [[ "$ch" == '`' ]] && return 0
    [[ "$ch" == '$' && "$next" == "(" ]] && return 0
  done
  return 1
}

RENGA_ONLY=false
if [[ "$COMMAND" == *renga* ]] && ! has_active_substitution "$COMMAND"; then
  RENGA_ONLY=true
  HAS_SEGMENT=false
  while IFS= read -r SEGMENT; do
    # 空白のみのセグメント（区切り文字の連続 && / || 等）は無視
    [[ -z "${SEGMENT//[[:space:]]/}" ]] && continue
    HAS_SEGMENT=true
    if ! segment_is_renga "$SEGMENT"; then
      RENGA_ONLY=false
      break
    fi
  done < <(split_top_level_segments "$COMMAND")
  [[ "$HAS_SEGMENT" == "true" ]] || RENGA_ONLY=false
fi

if [[ "$RENGA_ONLY" == "true" ]]; then
  exit 0
fi

# workers ディレクトリのパスを org-config.md から読み取って解決する
# org-config.md の workers_dir はリポジトリルート起点の相対パスなので、
# config の読み込みも workers パスの正規化も ORG_ROOT 起点で行う
# （CLAUDE_ORG_PATH 未設定時は cwd を fallback。Secretary は cwd がプロジェクトルートなので動作する）
ORG_ROOT="${CLAUDE_ORG_PATH:-$(pwd)}"
ORG_CONFIG="$ORG_ROOT/registry/org-config.md"
WORKERS_REL=$(grep 'workers_dir:' "$ORG_CONFIG" 2>/dev/null | sed 's/.*workers_dir:[[:space:]]*//' | tr -d '[:space:]' || true)
if [[ -z "$WORKERS_REL" ]]; then
  # org-config.md が読めない場合はスキップ（Hook の責務外）
  exit 0
fi
case "$WORKERS_REL" in
  /*) WORKERS_ABS="$WORKERS_REL" ;;
  *)  WORKERS_ABS="$ORG_ROOT/$WORKERS_REL" ;;
esac
WORKERS_CANONICAL=$(portable_realpath "$WORKERS_ABS")

# 判定ロジック: 「再帰削除コマンドが含まれる」AND「workers パスが含まれる」
# 引数パースではなく文字列マッチで判定する（for ループ等の回避パターンにも対応）

# 条件1: 再帰削除コマンドが含まれるか
# 検知対象:
#   A) 短オプション: rm -r, rm -rf, rm -R, rm -f -r 等
#   B) 長オプション: rm --recursive, rm --force --recursive 等
# 除外: --preserve-root 等（-r/-R を含むが再帰削除ではない長オプション）
#
# 既知���限界: シェル変数経由の間接パス（例: x=../workers; rm -rf "$x"）は
# 文字列マッチでは検知できない。スキルの文言による指示レベルの保護で補完する。
# コマンド開始位置の直前に来うる文字。制御演算子・空白に加えて、コマンド置換 /
# プロセス置換 / サブシェルの開始（$( 、`、<( 、( ）も rm の先頭境界として扱う。
HAS_RECURSIVE=false
# 短オプション内の -r/-R
if echo "$COMMAND" | grep -qE '(^|[|&;()`$[:space:]])rm[[:space:]]+-[a-zA-Z]*[rR]|(^|[|&;()`$[:space:]])rm[[:space:]].*[[:space:]]-[a-zA-Z]*[rR]'; then
  HAS_RECURSIVE=true
fi
# 長オプション --recursive
if echo "$COMMAND" | grep -qE '(^|[|&;()`$[:space:]])rm[[:space:]].*--recursive'; then
  HAS_RECURSIVE=true
fi
if [[ "$HAS_RECURSIVE" != "true" ]]; then
  exit 0
fi

# 条件2: workers ディレクトリのパスが含まれるか
# 正規化済み絶対パスの複数表記で判定する（相対パスの表記揺れに依存しない）
WORKERS_WIN=$(echo "$WORKERS_CANONICAL" | sed 's|^/\([a-zA-Z]\)/|\U\1:/|')  # /c/... → C:/...
WORKERS_BACKSLASH=$(echo "$WORKERS_WIN" | tr '/' '\\')  # C:/... → C:\...
# org-config.md の生の値も含める（../workers, ../../other 等）
WORKERS_REL_CANONICAL=$(portable_realpath "$WORKERS_REL")  # 二重正規化防止: 既に同じ値になるはず

FOUND=false
for PATTERN in "$WORKERS_CANONICAL" "$WORKERS_WIN" "$WORKERS_BACKSLASH" "$WORKERS_REL"; do
  if echo "$COMMAND" | grep -qF "$PATTERN"; then
    FOUND=true
    break
  fi
done

if [[ "$FOUND" == "true" ]]; then
  deny_with_reason "workers ディレクトリの再帰的削除は禁止されています。過去の作業成果が含まれている可能性があります。本当に削除が必要な場合はユーザーが手動で実行してください。"
fi

exit 0
