# Guardrails Phase 2b: 3-in-1 refactor 設計

> 関連 Issue: [#80](https://github.com/suisya-systems/claude-org-ja/issues/80)
> ステータス: **design only**（本 PR は実装を含まない。実装は別 Issue で切り出す）
> 前提: #70 Phase 1 / #79 Phase 2a がマージ済み
> 対象ファイル: `.hooks/lib/segment-split.sh`、`.hooks/block-*.sh`、`.claude/settings.json`

本 PR は Issue #80 が要求する 3 項目 ((1) command-allowlist 統合 / (3) loose-match tokenizer 移行 / (4) reassignment timeline tracking) を **1 PR で束ねる refactor** の設計と意思決定を文書化する。実装担当ワーカーが本ドキュメントから直接コードを書き起こせる粒度で、疑似コード・データ構造・テストケース列挙・段階分割・open question を含める。

---

## 0. なぜ 3 項目を束ねるのか（bundling rationale）

3 項目は **`segment-split.sh` の同じ箇所** に手を入れる。個別 PR にすると:

- (3) は `flatten_substitutions` の L104 `gsub(/[\047\042]/, " ", out)` を「append 部分のみ」に絞る変更。これにより Phase 1 で **副作用として偶発検出されていた** `eval "git commit --no-verify"` などのケースが壊れる。
- (4) は `collect_assignments` + `expand_known_vars` の 1 値固定ロジックをスナップショット方式に書き換える。`flag=A; flag=ok; git commit "$flag"` のような re-assignment が現状 FP として残る。
- (1) は新 API `allowlist_check()` を `segment-split.sh` に追加し、`block-*.sh` 群が呼び出す。

(3) を単独でマージすると Phase 2a の `unwrap_eval_and_bashc` だけが eval 経路の防衛線となるため、回帰テストを 3 セット書くより **1 PR に束ねて pytest の test fixture を共通化** する方がコストが低い。さらに (1) が tokenizer の API を借りるため、(3) を先行マージしても (1) で再度 segment-split を触ることになる。

**結論: bundling は engineering trade-off として正当**。リスクは PR 規模が大きくなる点だが、commit を 3 段階（後述 §5）に分けて pytest が各段階で通る粒度を保てば、レビューと revert は段階単位で可能。

---

## 1. 実装言語決定（awk 継続 vs python 移行）

### 1.1 比較表

| 項目 | awk | python (`py -3` + `shlex` + `re`) |
|---|---|---|
| LOC 見積もり | ~150（既存 ~120 + 拡張 ~30） | ~120（新規） |
| ヒアドキュメント前処理 | 既存 awk で行ベースに対応可（`<<EOF`/`<<-EOF`/`<<'EOF'`/`<<\EOF` の 4 形態を手書き） | DIY 必要（`re.compile` で同 4 形態を扱う、ライン状態機械は同等） |
| `shlex` の利用 | 不可 | 可（quoted token / nested escape / POSIX mode） |
| `re` の利用 | gsub / match に制約あり（ロックアラウンド不可、PCRE 不可） | 完全な re（lookbehind, named group, VERBOSE） |
| 既存テストの互換 | 互換維持（呼び出し API 不変） | 移行コストあり（`tests/test-block-pretooluse-hooks.sh` が新 CLI を呼ぶ形に書き換え） |
| 起動コスト | 軽い（awk 1 プロセス） | python 起動 ~50–80 ms × hook 1 回（hook 数は 1 PreToolUse 内で 5 個程度） |
| デバッグ性 | 低（awk は printf-debug 中心、stack なし） | 高（`pdb`, traceback, unit test 単位の関数分割） |
| 学習曲線 | チーム内既習だが実装者が限られる | チーム内で `tools/` 群（`generate_worker_settings.py` 等）と整合 |
| Windows portability | Git Bash awk 同梱で OK | `py -3` launcher 前提（本 worktree でも `py -3` は動作不安定、`python` 経由が安定） |
| 1 値固定 / branch union 等の構造化処理 | dict/list が貧弱、awk 連想配列で擬似実装 | `dict[str, set[str]]` で素直に書ける |

### 1.2 推奨: **awk 継続**

**判断**: 当初 python 移行が候補だったが、以下 3 点で awk 継続を推奨する。

1. **起動コスト**: PreToolUse hook は **`block-no-verify.sh`/`block-git-push.sh`/`block-dangerous-git.sh`/`block-org-structure.sh`/`block-workers-delete.sh`/`block-dispatcher-out-of-scope.sh`** の 6 本が 1 ツール呼び出しごとに直列実行される。各々が `segment-split.sh` を source する構造。python 化すると `python tools/segment_split.py < cmd.txt` を 6 回呼ぶ形になり、`50–80ms × 6 ≈ 300–500ms` の常時オーバーヘッド。Bash ツール呼び出しの体感応答が悪化する。awk なら数 ms。
2. **Windows portability**: 本 worktree でも `py -3` は launcher 経由で起動失敗する状態を確認した（`py -3 --version` が文字化けエラー）。`python` 直叩きはパス依存で portable でない。Git Bash 同梱の `awk` は確実に動く。
3. **既存テスト互換**: `tests/test-block-pretooluse-hooks.sh`、`tests/test-unwrap-eval-bashc.sh` は shell 統合テストで現存する。awk 継続なら API 不変で追加テストのみ。python 移行は CLI 形態を作って既存 shell テストの呼び出し方を全面改修する必要があり、「設計 PR の隣で安全装置を一時剥がす」リスクがある。

**ただし allowlist_check() のみは検討の余地**: shlex 相当の堅牢な tokenizer が要る場合、当該関数だけ `tools/allowlist_check.py` として切り出し、segment-split.sh から `python tools/allowlist_check.py` で呼ぶハイブリッド構成もある。Lead 確認 open question として §8 に残す。

### 1.3 落ちた案: 全面 python 移行
- メリット（shlex / 構造化データ）はあるが、起動コストとテスト書き換えコストの 2 点で短期 ROI が出ない。
- 「将来 sandbox 全体を python に移すなら」という上位設計が決まってから再評価する。

---

## 2. (3) Tokenizer migration 設計

### 2.1 責務（既存からの追加分）

| # | 責務 | FP/TP | 実装位置 |
|---|---|---|---|
| T1 | `#` 以降のコメント剥離 | FP（コメント内の `--no-verify` 文字列） | `split_segments` の前段 `strip_comments` 関数を新規追加 |
| T2 | ヒアドキュメント範囲のセグメント化除外 | FP（heredoc 本文に紛れた flag 文字列） | `split_segments` の awk に heredoc state を追加 |
| T3 | 引用符内 token の flag 検出除外 | FP（`git commit -m "use --no-verify carefully"`） | 検出側（`block-no-verify.sh`）の grep 前に **引用符内を空白に置換した正規化文字列** を別系統で持つ |
| T4 | `eval` / `bash -c` 引数の明示再パース | TP（quoted bypass） | 既存 `unwrap_eval_and_bashc()` を継続使用（Phase 2a 済） |
| T5 | `flatten_substitutions` の L104 gsub バグ修正 | TP/FP 両方 | gsub の対象を `out` 全体ではなく **`appended portion` のみ** に限定 |

### 2.2 API スケッチ

```bash
# segment-split.sh に追加・改修される関数群

# 新規: コメント剥離。引用符内の `#` は剥離しない。
# 入力: 1 行の Bash コマンド文字列
# 出力: 引用符外の `#` 以降を削除した文字列
strip_comments() {
  awk '
    {
      in_dq=0; in_sq=0; out=""
      n=length($0); i=1
      while(i<=n) {
        c=substr($0,i,1)
        if(in_sq){ if(c=="\x27") in_sq=0; out=out c; i++; continue }
        if(in_dq){ if(c=="\"")  in_dq=0; out=out c; i++; continue }
        if(c=="\x27"){ in_sq=1; out=out c; i++; continue }
        if(c=="\""){   in_dq=1; out=out c; i++; continue }
        if(c=="#"){ break }      # コメント開始（引用符外のみ）
        out=out c; i++
      }
      print out
    }
  '
}

# 改修: heredoc 範囲をセグメント化対象外に。
# 入力例:
#   cat <<EOF
#   git commit --no-verify
#   EOF
#   git status
# 期待出力（セグメント単位）:
#   cat <<EOF\n[HEREDOC:EOF]\n            ← heredoc 1 件（タグ付きで透過させる）
#   git status
split_segments() {
  awk '
    BEGIN { in_dq=0; in_sq=0; in_bt=0; paren_depth=0; seg=""; in_heredoc=0; heredoc_tag=""; heredoc_quoted=0 }
    {
      line=$0
      # heredoc 内の場合: 終了タグ判定のみ
      if(in_heredoc){
        if(line == heredoc_tag || (heredoc_indented && line ~ "^[ \t]*" heredoc_tag "$")){
          in_heredoc=0; heredoc_tag=""
        }
        # heredoc 本文は seg に追加せず、検査対象から除外（透過マーカ "[HEREDOC]" で痕跡だけ残す案も可）
        next
      }
      # heredoc 開始判定: <<TAG / <<-TAG / <<"TAG" / <<\TAG
      # （実装は match() で取得し、in_heredoc=1, heredoc_tag=TAG, heredoc_indented=(<<- なら 1) をセット）
      ...（既存のセグメント化ロジック）...
    }
  '
}

# 改修: gsub を appended portion のみに限定。
flatten_substitutions() {
  awk '
    {
      original = $0
      appended = ""
      s = $0
      while (match(s, /\$\([^()]*\)/)) {
        body = substr(s, RSTART+2, RLENGTH-3)
        appended = appended " " body
        s = substr(s, RSTART+RLENGTH)
      }
      s = $0
      while (match(s, /`[^`]*`/)) {
        body = substr(s, RSTART+1, RLENGTH-2)
        appended = appended " " body
        s = substr(s, RSTART+RLENGTH)
      }
      # appended portion のみクォート文字を空白化（FIX: 元の original を破壊しない）
      gsub(/[\047\042]/, " ", appended)
      print original appended
    }
  '
}
```

### 2.3 データフロー

```
raw command (stdin)
   │
   ▼
strip_comments              ← T1 新規
   │
   ▼
split_segments              ← T2 heredoc 対応を追加
   │（segments per line）
   ▼
unwrap_eval_and_bashc       ← T4 既存（Phase 2a）
   │（segments + unwrapped bodies）
   ▼
collect_assignments         ← (4) で書き換え
   │（assignment snapshots per segment index）
   ▼
expand_known_vars (per seg) ← (4) で snapshot を渡す形に
   │
   ▼
flatten_substitutions       ← T5 gsub 位置 fix
   │
   ▼
detection regex (per hook)
```

### 2.4 ヒアドキュメント形態の対応マトリクス

| 形態 | 例 | 終了判定 | indent strip |
|---|---|---|---|
| `<<TAG` | `cat <<EOF` | `^EOF$` | × |
| `<<-TAG` | `cat <<-EOF` | `^[ \t]*EOF$` | ◯ |
| `<<'TAG'` | `cat <<'EOF'` | `^EOF$`（変数展開無し） | × |
| `<<"TAG"` | `cat <<"EOF"` | `^EOF$` | × |
| `<<\TAG` | `cat <<\EOF` | `^EOF$` | × |

**スコープ外（受容リスク）**: `<<` の左に変数展開やコマンド置換を含む高度ケース、複数 heredoc を 1 行に並べる `cat <<A <<B` の同時 heredoc。これらは README の既知制限に追記。

---

## 3. (4) Reassignment timeline tracking 設計

### 3.1 現状の問題

現在 `collect_assignments` は **全セグメントから VAR= を抽出して 1 値固定（`val` 上書き保存だが、後の値が前を上書きする）**。`expand_known_vars` はその固定値で全セグメントを展開する。結果:

- `flag=VERIFY_SKIP; flag=ok; git commit "$flag"` → 最後の `flag=ok` が勝ち、3 番目セグメントは `git commit "ok"` に展開され **検出されない** (TP miss、ただし元々 `--no-verify` で書かれていない例なので benign)。
- `flag=ok; flag=--no-verify; git commit "$flag"` → 最後の `--no-verify` が勝ち、3 番目セグメントは `git commit "--no-verify"` に展開され **検出される** (TP hit、現状の挙動)。
- `flag=--no-verify; flag=ok; git commit "$flag"` → 最後の `ok` が勝ち、`git commit "ok"` で **見逃す** (TP miss)。Issue #80 の (4) で取り上げられている FP/TP 反転ケース。

**正しい挙動**: 各 git commit セグメントの **直前まで** に観測された値の **集合（branch を考慮した union）** で展開し、その集合のいずれかに `--no-verify` が含まれていれば block。

### 3.2 提案: snapshot list + branch union

```
データ構造（疑似 Python 表記、awk でも同等な連想配列で実装可）:

env_at[i: int] -> dict[var: str -> set[str]]   # セグメント i 直前のスナップショット
branch_stack: list[dict[str -> set[str]]]       # if/&&/|| 分岐の保留枝

# 列挙アルゴリズム
env = {}                          # 現在の累積環境
for i, seg in enumerate(segments):
    env_at[i] = deepcopy(env)
    if seg は assignment（VAR=val）:
        env[VAR] = {val}          # 直線代入は単一値で上書き
    elif seg が if 分岐の開始（簡易検出: `if `, `case `, `[ ... ]`）:
        # 分岐 over-approximation: 後続を一旦両枝として並走できないため、
        # **assignment が分岐内に現れたら** 既存値と union する保守側に倒す
        in_branch = True
    elif seg が `||` / `&&` で連結された assignment:
        env[VAR] = env.get(VAR, set()) | {val}   # 既存値との union
```

**Phase 2b スコープでは「if/case の分岐解析」までは踏み込まず**、`&&` / `||` セパレータを既に持つ split_segments の境界情報を使って **「セグメント連結子が `&&`/`||` の場合は assignment を union、`;` の場合は上書き」** とする簡略版で良い。理由:

- if/case を hook の Bash で扱うケースは実運用でほぼない（worker Claude は単純な複合コマンドが大半）。
- 過剰一般化は実装コスト > FP 抑制の便益。

### 3.3 API スケッチ

```bash
# 改修: collect_assignments_snapshots
# 入力: 全セグメント（split_segments の出力）+ 区切り情報（;/&&/|| のいずれか）
#       区切り情報は split_segments を「区切り種別をペア出力する」形に拡張するか、
#       別関数 split_segments_with_seps として並走させる。
# 出力: 1 行 1 スナップショット
#   `<seg_index>\t<VAR>=<val1>,<val2>,...`
collect_assignments_snapshots() {
  awk '
    # ... segment ごとに env を更新し、env_at[i] を行列形式で出力 ...
  '
}

# 改修: expand_known_vars_at
# 入力: スナップショット dict（VAR=val1,val2,... の組）+ セグメント本文
# 出力: $VAR を `(val1|val2|...)` の **regex disjunction 風** に展開した文字列。
#       下流の grep -E はそのまま使える。
expand_known_vars_at() {
  local snapshot_at_i=("$@")
  # ... 既存の expand_known_vars を多値対応に拡張 ...
}
```

### 3.4 検出ロジック側の変更

`block-no-verify.sh` の各セグメントループは:

```bash
for i in "${!SEGMENTS[@]}"; do
  segment="${SEGMENTS[$i]}"
  snapshot=( $(get_snapshot_at "$i") )
  expanded=$(printf '%s' "$segment" | expand_known_vars_at "${snapshot[@]}")
  flat=$(printf '%s' "$expanded" | flatten_substitutions)
  # 以降の grep は変更なし
done
```

### 3.5 over-approximation の副作用

`flag=--no-verify; flag=ok; git commit "$flag"` のケースは新ロジックで `flag` の値集合が `{--no-verify, ok}` となり、**block する**（保守側に倒す）。これは「過去のどこかで `--no-verify` を `flag` に代入したことがある」コンテキストでは `git commit "$flag"` を block するという挙動で、false positive とも見えるが、**worker が動的構築 bypass を試みる正当な理由は無い** と判断し受容する。

---

## 4. (1) Command-allowlist 統合設計

### 4.1 方針

- 上流 `sugiyama34/cc_harness` の `shell-parse.sh` を vendor しない。**理由**: そちらの依存（`shell-parse.sh` 内部で別 lib に依存）を引き込まずに済む方が repo シンプル。`segment-split.sh` の自前 tokenizer が既にあるため、その上に薄く `allowlist_check()` を載せる方が合理的。
- `GOVERNED_PREFIXES` は `.claude/settings.json` の新フィールド `guardrails.governedPrefixes` から読む。

### 4.2 settings.json 追加スキーマ

```jsonc
{
  // ... 既存 permissions など ...
  "guardrails": {
    "governedPrefixes": [
      "git push",
      "git commit",
      "gh pr create",
      "gh pr merge",
      "npm publish"
    ]
  }
}
```

### 4.3 API スケッチ

```bash
# segment-split.sh に追加
# allowlist_check: セグメント先頭が GOVERNED_PREFIXES のいずれかに前方一致するか
# 入力: stdin にセグメント、$1 に prefix 配列を空白区切りで（または環境変数 GOVERNED_PREFIXES_FILE 経由）
# 出力: 一致したら exit 0 + 一致した prefix を stdout、なければ exit 1
allowlist_check() {
  local segment
  segment=$(cat)
  segment="${segment#"${segment%%[![:space:]]*}"}"  # ltrim
  for prefix in "${GOVERNED_PREFIXES[@]}"; do
    if [[ "$segment" == "$prefix"* ]]; then
      printf '%s\n' "$prefix"
      return 0
    fi
  done
  return 1
}
```

### 4.4 governed prefixes 暫定リスト（Lead 確認 open question）

claude-org の文脈で **governance 価値が高い** と判断した 5 件を提案する。本ドキュメント時点では Lead 未確認（§8 で open question として残す）。

| # | prefix | governance 理由 |
|---|---|---|
| 1 | `git push` | リモートに公開される。secretary 経由が原則。 |
| 2 | `git commit` | secret スキャナを通す。`block-no-verify.sh` と組み合わせ。 |
| 3 | `gh pr create` | 人間レビューが介入するチェックポイント。worker が直接 PR を作るのは secretary 越権。 |
| 4 | `gh pr merge` | mainline への影響大。 |
| 5 | `npm publish` | 公開レジストリへの副作用。本 repo 直接の使用は無いが将来導入時の保険。 |

**当初検討した prefix から落としたもの**:

- `npm install` / `pip install` / `cargo build` 等: ローカル副作用にとどまる。allowlist より sandbox（denyWrite 等）で十分。
- `make` / `docker`: 本 repo で使用頻度低。ノイズになる。
- `rm` / `mv` 等: 個別 hook（`block-workers-delete.sh`）で扱う方がメッセージが具体的。

### 4.5 README 記載項目

`README.md` の guardrails セクションに以下を追加（実装 PR で）:

- governed prefixes の運用ポリシー（誰が prefix を追加・削除できるか、レビュー経路）
- 暫定リストとその根拠（本 §4.4 を要約）
- prefix が検出された際の hook 挙動（拒否ではなく warn + log の方針か、即拒否か）

**運用ポリシー素案**: governed prefixes の追加・削除は `tools/check_role_configs.py` の drift CI と同じ扱いで PR レビュー経由のみ。worker / secretary が runtime で書き換えてはならない。

---

## 5. 統合 refactor の段階分け（commit 分割案）

実装 PR は以下 3 commit に分割する。**各 commit ごとに `python -m pytest tests/ tools/` と shell 統合テストが緑** であることが必須。

### Commit 1: tokenizer migration
- `.hooks/lib/segment-split.sh`:
  - `strip_comments` 新規追加
  - `split_segments` の heredoc 対応追加
  - `flatten_substitutions` の gsub 位置修正
- `tests/test_segment_split.py`（新規、pytest fixture を `tests/fixtures/` に追加）
- 既存 `block-*.sh` の呼び出しは変えない（`strip_comments` をパイプの先頭に追加するのみ）。
- AC: 既存 TP 全 pass、コメント・heredoc・引用符内文字列の FP 解消。

### Commit 2: reassignment timeline tracking
- `.hooks/lib/segment-split.sh`:
  - `collect_assignments_snapshots` 新規
  - `expand_known_vars_at` 新規（旧 `expand_known_vars` は互換のため残置 → 全 hook 移行確認後に削除）
- `block-no-verify.sh` 等 6 本の検出ループを snapshot 方式に書き換え
- `tests/test_reassignment.py`（新規）
- AC: §3.1 の 3 ケースすべて期待通り。

### Commit 3: allowlist API + settings 統合 + governed prefixes 暫定リスト
- `.hooks/lib/segment-split.sh`:
  - `allowlist_check` 新規
- `.claude/settings.json`:
  - `guardrails.governedPrefixes` フィールド追加（暫定 5 件）
- 新規 hook `.hooks/governed-prefix-warn.sh`（最初は **warn-only**、即拒否はしない方針で導入リスクを抑える）
- `README.md` ガイダンス追記
- `tests/test_allowlist.py`
- AC: prefix を含むコマンドで warn ログが出ること、含まないコマンドで素通りすること。

### revert plan
- 各 commit は独立 revert 可能。Commit 2 は Commit 1 の `flatten_substitutions` fix に依存するが、Commit 1 単独 revert なら Commit 2 も連鎖 revert する手順を PR description に明記する。

---

## 6. リスク & 後退条件

### 6.1 言語選択リスク
- **awk 継続を選んだ**ため、shlex 相当の堅牢性は得られない。受容リスクとして README に記載: 「ネスト深 3+ の引用符・バックスラッシュエスケープは検出経路の対象外。多層防御の他レイヤ（sandbox / secretary review）が補完する」。

### 6.2 回帰テスト一覧

#### FP（現状ブロックされてはいけないが、ブロックされている。本 PR で解消されるべき）

| ID | コマンド | 現状 | 期待 |
|---|---|---|---|
| FP-1 | `git commit -m "do not use --no-verify"` | block | 通す |
| FP-2 | `cat <<EOF`<br>`do not use --no-verify`<br>`EOF` | block | 通す |
| FP-3 | `# avoid --no-verify`<br>`git commit -m ok` | block | 通す |
| FP-4 | `flag=VERIFY_SKIP; flag=ok; git commit "$flag" -m x`（VERIFY_SKIP は無害な定数想定） | block | 通す（snapshot で `flag={ok}`） |
| FP-5 | `git commit -m "fix L104 gsub which used to break $(echo --no-verify) handling"` | block | block 維持（このケースは TP）。**ただし `gsub` 修正でメッセージの quote 部分は `original` を保存しつつ `appended` 側で `printf` body が `--no-verify` と展開され検出される** |

#### TP（現状ブロックされていて、本 PR でも維持されるべき）

| ID | コマンド | 期待 |
|---|---|---|
| TP-1 | `git commit --no-verify -m x` | block |
| TP-2 | `git push --no-verify` | block |
| TP-3 | `eval "git commit --no-verify -m x"` | block（unwrap 経路） |
| TP-4 | `bash -c "git commit --no-verify"` | block |
| TP-5 | `flag=--no-verify; git commit "$flag"` | block |
| TP-6 | `flag=--no-verify; flag=ok; git commit "$flag"` | block（snapshot union） |
| TP-7 | `git commit $(printf -- '--no-verify')` | block（flatten 経路） |
| TP-8 | `git commit $(printf -- "--no-verify")` | block（gsub fix 後も appended で検出） |

### 6.3 後退条件 (kill-switch)
- Commit 3 の `governed-prefix-warn.sh` が予想外に noisy であれば、その hook ファイルだけを `.hooks/` から外す（settings.json `guardrails.governedPrefixes` を空配列にしても warn-only なので影響少）。
- Commit 1 の heredoc 対応が誤った heredoc 終了判定をした場合、`split_segments` を Phase 1 版にリバートし `strip_comments` のみ残す部分 revert で運用継続可。

---

## 7. Acceptance Criteria mapping

Issue #80 の AC と本ドキュメント対応箇所:

| AC | 対応節 |
|---|---|
| Current FPs (comment / quotes / heredoc / reassignment) all pass | §2.1 (T1/T2/T3) + §3 + §6.2 FP-1..4 |
| No regression in existing TPs (eval-routed verify-bypass rejected) | §2.1 T4（既存 unwrap 流用）+ §6.2 TP-1..8 |
| Allowlist operational policy documented in README | §4.5 + Commit 3 で README 編集 |

---

## 8. Open questions（Lead 確認事項）

1. **§1.2 推奨の awk 継続で合意するか**。python ハイブリッド案（allowlist_check のみ python 切り出し）も選択肢。
2. **§4.4 governed prefixes 暫定 5 件で合意するか**。追加・削減の意向。
3. **§4 allowlist hook の挙動**: warn-only でスタートするか、即拒否でスタートするか。本ドキュメントは warn-only 推奨。
4. **§3.5 over-approximation の受容**: `flag=--no-verify; flag=ok; git commit "$flag"` を block する挙動でよいか。ユーザビリティ観点の確認。
5. **Phase 2b 実装 Issue を本 PR と別に切り出す形でよいか**（本 PR は design only という方針確認）。

---

## 9. 参考

- Phase 1 報告: `workers/hook-phase2-feasibility/report.md`（本 worktree には未取り込み。PR #170 系列で追加されたかは別途確認）
- Phase 2a 関連: PR #79 の `unwrap_eval_and_bashc()` 導入
- 既存 hook: `.hooks/block-*.sh`、`.hooks/lib/segment-split.sh`
- schema-driven role configs（参考: 同じ schema-as-SOT 思想）: `tools/role_configs_schema.json`、`docs/worker-permissions-design.md`
