#!/usr/bin/env bash
# PreToolUse Hook: Worker からの git push / gh 経由の GitHub 書き込みをブロックする
# 方式: exit 2 + stderr メッセージ でブロック
#
# ブロック対象:
#   1. git push（従来から。オプション介在形 / eval / bash -c 経由を含む）
#   2. gh CLI の GitHub 書き込みサブコマンド（Refs #429）
#
# ---------------------------------------------------------------------------
# gh 検知の方針: read-only allowlist（default-deny）
# ---------------------------------------------------------------------------
# gh のサブコマンドは版ごとに増える。「書き込みだけを列挙して deny」する
# blocklist 方式だと、新しい書き込みサブコマンドが追加された瞬間に穴が開く
# （検知漏れ = fail open）。そこで「read-only と確認済みの (group, subcommand)
# だけを allow し、それ以外の gh 呼び出しは deny する」default-deny 方式を採る。
# 未知 / 新規 / 静的に判定できない形は自動的に deny 側へ倒れる（fail closed）。
#
# allowlist は gh 2.74.0 (2025-05-29) の `gh <group> --help` 出力を実機で
# 列挙して分類した（下の READ_KEYS / READ_GROUPS）。調査業務で日常的に使う
# 読み取り系（gh pr view / list / diff / checks、gh run view / list / watch /
# download、gh issue view、gh release view / download、gh search *、
# gh api の GET 等）は全て allow に含めてある。塞ぎすぎは「正当な作業が
# 塞がれた結果、より危険な回避策に飛びつく」形の事故を誘発するため、
# 読み取り面は意図的に広く取っている。
#
# gh api はサブコマンドではなく HTTP メソッドで読み書きが決まるため個別に
# 解析する:
#   - `-X` / `--method` の明示値が GET / HEAD → allow、それ以外 → deny
#   - メソッド明示が無く `-f` / `--raw-field` / `-F` / `--field` / `--input`
#     がある → gh は自動的に POST になる（`gh api --help` に明記）ため deny
#   - メソッド明示も body パラメータも無い → GET → allow
#   - `gh api graphql` は read クエリでも POST 形になるので一律 deny にはせず、
#     `mutation` の字面を含む / `--input` で body が外部ファイル（静的に読めない）
#     の場合のみ deny する
#
# ---------------------------------------------------------------------------
# 既知の制限（多層防御の最後の壁であって、意図的な回避の防止ではない）
# ---------------------------------------------------------------------------
#   - `gh` の字面を含むリテラル文字列も deny になる。
#     例: `git commit -m "後で gh pr create する"` / `grep -rn "gh pr merge" docs/`
#     （`echo 'git push'` が既に deny されるのと同じ性質）。文字列検索は
#     `gh` トークンを外す（例: `grep -rn "pr merge"`）と通る。
#   - **gh alias 経由の残存ギャップ**: `gh config` に事前登録済みの alias
#     （`gh alias set pm 'pr merge'` 済みの環境での `gh pm 123`）は、コマンド
#     文字列に `pr merge` の痕跡が無いため静的解析では解決できない。ただし
#     alias を登録する側（`gh alias set` / `gh alias import` / `gh alias delete`）
#     は allowlist に無いので deny される（`gh alias list` のみ allow）。また
#     未知の group 名（`gh pm`）は default-deny 側に落ちるため、実質的には
#     alias 経由も塞がる。block-dangerous-git.sh の git alias ギャップと違い、
#     こちらは default-deny のおかげで塞がっている。
#   - `gh auth token` は allowlist に無い（deny）。トークンを取り出して curl 等で
#     直接 API を叩く経路を塞ぐため。`gh auth status` は allow。
#   - `gh extension install` / `exec` は任意コード実行経路なので deny
#     （`gh extension list` / `search` / `browse` は allow）。
#   - **静的解析の原理的な限界**: シェルの語形成は本フックが再現できる範囲より
#     広い（可変長のクォート連結・エスケープ・パス経由の再スペル・実行時に
#     解決される alias 等）。Codex レビュー 3 round でリダイレクト密着形 /
#     短縮フラグクラスタ / 引用符分断の実行ファイル名などを順次塞いだが、
#     「意図的な回避を完全に防ぐ」ことは静的解析では達成できない。本フックは
#     多層防御の 1 枚（うっかり実行と素直な迂回を止める壁）であり、最終的な
#     安全弁は資格情報側の deny と人間ゲートである。
#   - curl / wget で api.github.com を直接叩く経路は本フックの対象外
#     （gh 経由の書き込みを塞ぐのが Refs #429 のスコープ）。資格情報側の
#     deny と対で人間ゲートを守る設計。
#   - `$(...)` / `` `...` `` のサブシェル境界、バックスラッシュエスケープは
#     segment-split.sh の制限に従う。

set -euo pipefail

# shellcheck source=lib/segment-split.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/segment-split.sh"

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

# awk チェック (fail closed)。gh 判定は awk のトークン走査に依存する。
if ! command -v awk &>/dev/null; then
  echo "ブロック: awk がインストールされていません。セキュリティ Hook の実行に必要です。" >&2
  exit 2
fi

# stdin から JSON を読み取り
INPUT=$(cat)

# 空 payload の fail-closed ガード (Issue #834)。jq は「JSON 値がゼロ個」の入力を
# parse error にせず exit 0 + 出力なしで返すため、空 stdin では下の抽出結果が空文字に
# なり、`[[ -z "$COMMAND" ]]` の passthrough に落ちて enforcement が素通りする。
# jq に渡す前に明示的に弾く。導出の詳細は block-foreground-subagent.sh の同じガード。
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  deny_with_reason "PreToolUse payload が空でした。安全側 (fail-closed) で拒否します。"
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

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. git push 検知（既存挙動。変更しないこと）
# ---------------------------------------------------------------------------
# `git push` と、サブコマンド前にオプションが挿入された形を捕捉する。
# 例: git push, git  push, echo | git push, git -C /path push
# 一方で `git config push.default` のような別サブコマンドは誤検知しない。
PUSH_RE='(^|[|&;[:space:]])git([[:space:]]+(-[^[:space:]]+([[:space:]]+[^|&;[:space:]]+)?)?)*[[:space:]]+push([[:space:]]|$)'

if echo "$COMMAND" | grep -qE "$PUSH_RE"; then
  deny_with_reason "git push は Worker から直接実行できません。完了報告で窓口に依頼してください。窓口が push/PR を実施します。"
fi

# eval "git push ..." / bash -c "git push ..." 経由の bypass も明示的に捕捉する
# （Phase 2a, Issue #79）。unwrap_eval_and_bashc が引数文字列を取り出すので、
# その文字列に対しても同じ正規表現を適用する。
while IFS= read -r body; do
  [[ -z "$body" ]] && continue
  if echo "$body" | grep -qE "$PUSH_RE"; then
    deny_with_reason "git push は Worker から直接実行できません（eval/bash -c 経由も検知）。完了報告で窓口に依頼してください。"
  fi
done < <(printf '%s\n' "$COMMAND" | unwrap_eval_and_bashc)

# ---------------------------------------------------------------------------
# 2. gh 経由の GitHub 書き込み検知（Refs #429）
# ---------------------------------------------------------------------------
# セグメント内の各 `gh` 呼び出しについて group / subcommand を決め、read-only
# allowlist に載っていなければ 1 行 `DENY|<detail>` を出力する。allow の場合は
# 何も出力しない（呼び出し側は出力行の有無だけを見る）。
#
# 走査規則（block-dangerous-git.sh の extract_stash_subcommands と同じ考え方）:
#   - `gh` / `gh.exe` / パス付き（/usr/bin/gh, C:\bin\gh.exe）を実行トークンとして拾う。
#   - `-` 始まりは option。値を取る既知 option（--repo / -R / --hostname 等）は
#     続く値トークンも読み飛ばす。未知 option は値を読み飛ばさないので、値が
#     group 位置に来て「未知 group」= deny に倒れる（安全側）。
#   - group / subcommand が未知なら deny。group だけで subcommand が無い形
#     （`gh pr`）は help 出力なので allow。
classify_gh_invocations() {
  awk '
    BEGIN {
      # 全体が read-only の group（サブコマンドを問わず allow）
      READ_GROUPS = " search browse status org attestation help completion version "
      # group:subcommand 単位の read-only allowlist（gh 2.74.0 の全 subcommand を分類）
      READ_KEYS = " \
pr:list pr:status pr:checks pr:diff pr:view pr:checkout \
issue:list issue:status issue:view \
repo:list repo:view repo:clone repo:set-default \
release:list release:view release:download \
run:list run:view run:watch run:download \
workflow:list workflow:view \
gist:list gist:view gist:clone \
project:list project:view project:field-list project:item-list \
cache:list \
label:list \
secret:list \
variable:list variable:get \
ruleset:check ruleset:list ruleset:view \
alias:list \
auth:status \
config:get config:list \
extension:list extension:search extension:browse \
gpg-key:list \
ssh-key:list \
codespace:list codespace:view codespace:logs \
"
      # 3 階層目まで見る group:subcommand（残りは group:subcommand で判定）
      NESTED = " repo:deploy-key repo:autolink repo:gitignore repo:license "
      READ_KEYS3 = " \
repo:deploy-key:list \
repo:autolink:list repo:autolink:view \
repo:gitignore:list repo:gitignore:view \
repo:license:list repo:license:view \
"
      # 既知 group（ここに無い group は未知として deny）
      GROUPS = " pr issue repo release run workflow gist org project cache label \
secret variable ruleset search alias api auth attestation config extension \
gpg-key ssh-key codespace preview browse status co help completion version "
    }
    # 値を取る option（続く 1 トークンを読み飛ばす）
    function takes_value(t) {
      return (t == "-R" || t == "--repo" || t == "--hostname" \
              || t == "-H" || t == "--header" || t == "-X" || t == "--method" \
              || t == "-f" || t == "--raw-field" || t == "-F" || t == "--field" \
              || t == "-q" || t == "--jq" || t == "-t" || t == "--template" \
              || t == "--json" || t == "--input" || t == "--cache" \
              || t == "-L" || t == "--limit" || t == "-b" || t == "--body" \
              || t == "-T" || t == "--title")
    }
    function in_list(list, key) { return index(list, " " key " ") > 0 }
    # トークンの「種別」を返す。
    #   cmd  : サブコマンド名の字面（^[a-z][a-z0-9-]*$）。allowlist 判定にかける。
    #   var  : 変数 / コマンド置換が残っており静的に読めない → deny（fail closed）。
    #   other: コマンド名になりえない字面（日本語・パス・記号・数字始まり等）。
    #          gh の group / subcommand は全て cmd 形なので、other は「コマンド
    #          ではなくリテラル文字列中の gh」とみなして読み飛ばす。これが無いと
    #          `git commit -m "gh の書き込みを塞ぐ"` のような日常操作まで deny に
    #          なる（flatten_substitutions が引用符を空白へ潰すため）。読み飛ばして
    #          も、実在する gh の group 名は全て cmd 形なので検知漏れにはならない。
    function word_kind(t) {
      if (index(t, "$") > 0 || index(t, "`") > 0) return "var"
      # gh の alias 名は `[a-z-]+` に限らない（アンダースコア / 数字 / ドットを
      # 含む名前も gh alias set で登録でき、`gh <alias>` として実行できる）。
      # そのため cmd 判定は「ASCII の語形トークン」まで広げ、未知なら deny に
      # 倒す。狭い `^[a-z][a-z0-9-]*$` にすると `gh foo_bar 123`（alias 経由の
      # pr merge）が other 扱いで素通りする。
      if (t ~ /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/) return "cmd"
      return "other"
    }
    # idx 以降で最初の非 option トークンの位置を返す（無ければ 0）
    function next_word(idx,    j) {
      j = idx
      while (j <= NF) {
        if (substr($j, 1, 1) == "-") {
          if (takes_value($j)) j++
          j++
          continue
        }
        return j
      }
      return 0
    }
    {
      # シェルのリダイレクト演算子は空白なしで語に密着できる
      # （`gh pr create>/dev/null` / `gh pr>out merge 1`）。シェルは実行前に
      # これを取り除くので、字面のままだと「コマンド語ではない」と誤判定して
      # 素通りする。判定前に演算子を空白へ正規化してトークンを割り直す
      # （$0 への代入で NF / $n が再計算される）。
      gsub(/[<>]/, " ")
      for (i = 1; i <= NF; i++) {
        ghname = tolower($i)
        # Windows は gh.exe。実行形式のスペリング差で deny が外れないようにする。
        if (ghname != "gh" && ghname != "gh.exe" && ghname !~ /[\/\\]gh(\.exe)?$/) continue

        gi = next_word(i + 1)
        # フラグのみ（gh --version / gh --help）または gh 単独 → 書き込み不能
        if (gi == 0) continue
        kind = word_kind($gi)
        if (kind == "var") { print "DENY|group-undecidable:" $gi; continue }
        if (kind == "other") continue   # リテラル文字列中の gh
        group = tolower($gi)

        if (!in_list(GROUPS, group)) { print "DENY|unknown-group:" group; continue }
        if (in_list(READ_GROUPS, group)) continue
        # `gh co` は `gh pr checkout` の組み込み alias（ローカル checkout のみ）
        if (group == "co") continue

        if (group == "api") {
          classify_api(gi)
          continue
        }

        si = next_word(gi + 1)
        # `gh pr` のように subcommand が無い形は help 出力 → allow
        if (si == 0) continue
        kind = word_kind($si)
        if (kind == "var") { print "DENY|subcommand-undecidable:" group ":" $si; continue }
        if (kind == "other") continue   # subcommand 名になりえない字面
        scmd = tolower($si)
        key = group ":" scmd

        if (in_list(NESTED, key)) {
          ti = next_word(si + 1)
          if (ti == 0) continue   # `gh repo deploy-key` 単独 = help
          kind = word_kind($ti)
          if (kind == "var") { print "DENY|subcommand-undecidable:" key ":" $ti; continue }
          if (kind == "other") continue
          key3 = key ":" tolower($ti)
          if (in_list(READ_KEYS3, key3)) continue
          print "DENY|" key3
          continue
        }

        if (in_list(READ_KEYS, key)) continue
        print "DENY|" key
      }
    }
    # gh api の HTTP メソッド解析。start は "api" トークンの位置。
    function classify_api(start,    k, t, method, hasbody, hasinput, graphql, m, fieldval, opaquebody, ei, nospace, cluster, cval, fpos) {
      method = ""; hasbody = 0; hasinput = 0; graphql = 0; opaquebody = 0
      for (k = start + 1; k <= NF; k++) {
        t = $k
        if (t == "-X" || t == "--method") {
          method = (k < NF) ? $(k + 1) : "__missing__"
          k++
          continue
        }
        if (t ~ /^--method=/) { method = substr(t, 10); continue }
        # pflag の短縮形は値を密着させられる（-XPOST）
        if (t ~ /^-X./)       { method = substr(t, 3);  continue }
        if (t == "--input") { hasinput = 1; hasbody = 1; k++; continue }
        if (t ~ /^--input=/) { hasinput = 1; hasbody = 1; continue }
        if (t == "-f" || t == "--raw-field" || t == "-F" || t == "--field") {
          hasbody = 1
          if (k < NF) fieldval = $(k + 1); else fieldval = ""
          if (fieldval ~ /=[@$]/) opaquebody = 1
          k++
          continue
        }
        if (t ~ /^(--raw-field|--field)=/) {
          hasbody = 1
          if (t ~ /=[^=]*=[@$]/) opaquebody = 1
          continue
        }
        if (t ~ /^-[fF]./) {
          hasbody = 1
          if (t ~ /=[@$]/) opaquebody = 1
          continue
        }
        # pflag は値を取らない短縮フラグと値を取る短縮フラグを 1 トークンに
        # まとめられる（`-iXPOST` = `-i -X POST`、`-iFtitle=x` = `-i -F title=x`）。
        # 上の完全一致 / 密着形の分岐はこの形を取りこぼすので、クラスタ内に
        # X / f / F が現れたら残りを値として解釈する。
        if (t ~ /^-[A-Za-z]/ && t !~ /^--/) {
          cluster = substr(t, 2)
          if (index(cluster, "X") > 0) {
            cval = substr(cluster, index(cluster, "X") + 1)
            if (cval == "") {
              method = (k < NF) ? $(k + 1) : "__missing__"
              k++
            } else {
              method = cval
            }
          }
          fpos = index(cluster, "f")
          if (fpos == 0) fpos = index(cluster, "F")
          if (fpos > 0) {
            hasbody = 1
            cval = substr(cluster, fpos + 1)
            if (cval == "") {
              cval = (k < NF) ? $(k + 1) : ""
              k++
            }
            if (cval ~ /=[@$]/) opaquebody = 1
          }
          continue
        }
      }

      # GraphQL かどうかは endpoint 引数だけで決める。全トークンを見ると
      # `gh api -X DELETE /repos/o/r/issues/1 --template graphql` のように
      # option の値に graphql が現れる REST 書き込みが GraphQL 扱いになり、
      # メソッド判定を素通りする。
      ei = next_word(start + 1)
      if (ei > 0 && tolower($ei) == "graphql") graphql = 1

      # 静的に読めないメソッド指定（変数が残っている / 値が欠落）は安全側で deny
      if (method == "__missing__" || method ~ /\$/) {
        print "DENY|api:method-undecidable"
        return
      }

      if (graphql == 1) {
        # GraphQL は read クエリでも POST 形になるため、メソッドではなく本文で判定。
        # 本文を静的に読めない形（--input file / -f query=@file / -f query=$VAR）は
        # mutation が隠れていても見えないので、まとめて deny に倒す（fail closed）。
        if (hasinput == 1) { print "DENY|api:graphql-input-undecidable"; return }
        if (opaquebody == 1) { print "DENY|api:graphql-body-undecidable"; return }
        # `-f query="muta""tion{x}"` のように隣接クォートで連結された形は、
        # flatten_substitutions が引用符を空白へ潰すため字面が分断される。
        # 空白を除去した版でも判定して取りこぼしを塞ぐ（安全側）。
        nospace = tolower($0)
        gsub(/[[:space:]]/, "", nospace)
        if (index(tolower($0), "mutation") > 0 || index(nospace, "mutation") > 0) {
          print "DENY|api:graphql-mutation"; return
        }
        return
      }

      m = toupper(method)
      if (m == "") m = (hasbody == 1) ? "POST" : "GET"
      if (m == "GET" || m == "HEAD") return
      print "DENY|api:" m
    }
  '
}

# 全セグメントを 1 度収集してから既知の代入を抽出し、各セグメントで展開する。
GH_SEGMENTS=()
while IFS= read -r seg; do
  GH_SEGMENTS+=("$seg")
done < <(printf '%s' "$COMMAND" | split_segments)

# eval / bash -c / sh -c の引数文字列を追加の検査対象セグメントとして取り出す
# （Phase 2a, Issue #79 と同じ回避耐性層）。
while IFS= read -r unwrapped; do
  [[ -n "$unwrapped" ]] && GH_SEGMENTS+=("$unwrapped")
done < <(printf '%s\n' "${GH_SEGMENTS[@]}" | unwrap_eval_and_bashc)

GH_ASSIGNMENTS=()
while IFS= read -r assign; do
  [[ -n "$assign" ]] && GH_ASSIGNMENTS+=("$assign")
done < <(printf '%s\n' "${GH_SEGMENTS[@]}" | collect_assignments)

for segment in "${GH_SEGMENTS[@]}"; do
  [[ -z "$segment" ]] && continue
  gh_verdicts=""

  # 既知の VAR=value を展開（`sub=merge; gh pr "$sub" 1` 対策）
  if [[ ${#GH_ASSIGNMENTS[@]} -gt 0 ]]; then
    gh_expanded=$(printf '%s' "$segment" | expand_known_vars "${GH_ASSIGNMENTS[@]}")
  else
    gh_expanded="$segment"
  fi

  # コマンド置換 $(...) / `...` の本体も検査対象に含める
  gh_flat=$(printf '%s' "$gh_expanded" | flatten_substitutions)

  # 引用符を「削除」した版も併せて検査する。flatten_substitutions は引用符を
  # 空白へ潰すため、シェルが 1 語として連結する `g"h" pr create` /
  # `/usr/bin/g"h" pr merge 1` が `g h ...` に割れて実行ファイル名の検知を
  # すり抜ける。削除版ではシェルと同じ連結結果（`gh pr create`）になる。
  gh_joined=$(printf '%s' "$gh_expanded" | tr -d '\042\047' | flatten_substitutions)

  for gh_variant in "$gh_flat" "$gh_joined"; do
    # `gh` トークンが無いセグメントは早期に抜ける（awk 起動コストの節約）。
    # 大小文字を無視する（-i）: awk 側の判定は tolower() 済みなので、case-insensitive
    # なファイルシステム（Windows / macOS）で通る `GH pr merge` を pre-filter で
    # 落としてしまわないようにする。
    echo "$gh_variant" | grep -qiE '(^|[[:space:]])[^[:space:]]*gh(\.exe)?([[:space:]]|$)' || continue
    gh_verdicts=$(printf '%s' "$gh_variant" | classify_gh_invocations)
    [[ -n "$gh_verdicts" ]] && break
  done

  if [[ -n "${gh_verdicts:-}" ]]; then
    gh_detail=$(printf '%s' "$gh_verdicts" | sed 's/^DENY|//' | tr '\n' ' ')
    deny_with_reason "gh 経由の GitHub 書き込み操作は Worker から直接実行できません（検知: ${gh_detail%% }）。PR 作成 / merge / comment / review / release / workflow 実行などは人間の承認後に窓口が実施します。読み取り系（gh pr view / list / diff / checks、gh run view / list、gh api の GET 等）は許可されています。読み取りのつもりで拒否された場合は、判定できない形（未知のサブコマンド / 変数経由の指定）になっていないか確認し、必要なら窓口に相談してください。"
  fi
done

exit 0
