# Worker

> このワーカーは claude-org リポジトリ自身の `/tmp/workers/demo-task` で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。あなたは窓口ではなくワーカーである。

## 作業ディレクトリ
`/tmp/workers/demo-task`

起動直後 `pwd` で確認。

### 禁止事項
1. claude-org 構造を `/tmp/workers/demo-task` 内に再現しない
2. claude-org リポジトリ（`/home/user/work/claude-org`）を別途 clone しない（直接編集）
3. `git push` 不可
4. `git stash` の変更系不可（hook で deny。引数なし `git stash` / `push` / `save` / `pop` / `apply` / `branch` / `drop` / `clear` / `store` / `create`、**許可リスト方式なので未列挙のサブコマンドも deny**）。キャラクタデバイス等の未追跡ファイルで `git stash -u` が途中失敗し、気づかず別の stash を pop して作業を壊す事故が実際に起きているため。退避は作業ブランチへ一時 commit（`git add -u` して commit、戻すときは `git reset --soft HEAD~1`）。`git diff > <name>.patch` は staged / 未追跡を取りこぼすので単独の退避手段にしないこと。比較は `git show HEAD:<path>`。調査用の `git stash list` / `git stash show` は可。**alias 経由でも実行しないこと**（定義済み alias は hook が静的に解決できず素通りするが事故の中身は同じ）
5. この repo の worktree root には同じキャラクタデバイスが未追跡で存在するため `git add -A` も `can only add regular files` で失敗する。**staging は `git add -u`（追跡済みの変更）＋ 新規ファイルの明示 add を使うこと**

### Windows
- Python は `py -3` または `python`（3.10 推奨。どちらも別の Python 環境を指す場合があるため `--version` で確認し、動作する方を使う）
- 日本語ファイル: `encoding="utf-8"` 明示
- CLI 出力文字列（argparse `help=` / `print()`）は ASCII の `-` を使う（em-dash 等 cp932 非対応文字は cp932 コンソールでの `--help` を `UnicodeEncodeError` でクラッシュさせる。pytest の `redirect_stdout` では検出できず実端末でのみ落ちる）。実装後 `--help` を実端末で 1 回スモーク

### Bash のパス指定（絶対パス必須。ultracode の `agent()` プロンプト内も同じ）
- grep / find / sed / テスト対象は**常に `/tmp/workers/demo-task/...` の絶対パス**。`cd <dir>; <相対パス>` と root での `grep -r ... .` は使わない。ultracode の各 `agent()` プロンプト内の Bash 指示にも同じ規約を適用（subagent のコマンドは worker 本体の permissions で判定される）
- 理由: auto-mode 分類器は `cd` 後の相対パスを解決できず、`Read(.env)` 等の deny 規則（`tools/org_extension_schema.json` の `layer2Fallback`）と組み合わさって毎回人間承認に落ちる（2026-09-04 continuo-110-lease-renewal で 1 タスク 6 回）。deny 規則は緩めず書き方で回避する
- NG: `cd /tmp/workers/demo-task; grep -n "renewLease" test/lap/root.test.ts` / `grep -rln "lease" . --include=*.json` → OK: `grep -n "renewLease" /tmp/workers/demo-task/test/lap/root.test.ts` / `grep -rln "lease" /tmp/workers/demo-task/test --include=*.json`

### 着手前 preflight（sandbox 書き込み境界・非対話 alias・hook 拒否）
- 作業ファイルを置けるのは `/tmp/workers/demo-task` 配下と `$TMPDIR` だけ（git が内部で触る Pattern B の `.git` メタデータは別枠で、作業ファイルの置き場ではない）。ハーネスの scratchpad（`/tmp/claude-<uid>/.../scratchpad`）は `/tmp/workers/demo-task` 外なので Write / Edit が `.hooks/check-worker-boundary.sh`（許可パスは worker dir / `~/.claude/plans/` / `knowledge/raw/` の 3 つ。113-136 行）で deny される。Bash の一時ファイルは `$TMPDIR` へ、Write / Edit の作業メモは `mkdir -p /tmp/workers/demo-task/.worker-scratch` へ。linked worktree（Pattern B）では `.git/info/exclude` が共通 clone 側（sandbox 外）にあり登録できないので、禁止事項 5 のとおり `git add -u` + 明示 add で staging し `.worker-scratch/` を commit に混入させない。`/tmp/claude-<uid>` は並走ワーカーと共有なので一般名のバックアップは衝突する（`.worker-scratch/` に置くか task_id を名前に入れる）
- `cp` / `mv` / `rm` は `-i` alias 前提で書く（既存ファイルへの上書きが無言の確認待ちで timeout まで止まる）。上書きは `command cp -f` か `cat backup > dest`。`rm -rf` / `rm -r` は permissions.deny（Node 等で再帰削除を迂回しない。掃除は新しい名前のディレクトリを掘る）
- git 側の退避／復元は使えない: `git stash` 変更系（禁止事項 4）に加え、パス指定の `git checkout -- <path>` / `git restore --source=<ref>`（`--staged` 単独の index-only を除く）も `.hooks/block-dangerous-git.sh` が deny（484-487 行 / 491-505 行）。壊して戻す手順は `cp` バックアップ（書き戻しは `command cp -f` / `cat >`）か一時 commit の 2 択。復元後は `git diff` で戻ったことを確認してから次に進む
- `read-only file system` / `Permission denied` でも `dangerouslyDisableSandbox` を反復要求しない（安全分類器のロックアウトで作業不能になる）。出力先を書ける場所へ変えて再試行する

## プロジェクト
- claude-org-ja: テスト用説明

## タスク
- ID: demo-task
- ブランチ: `demo-task`
- 検証深度: **full**
- commit prefix: `feat(tools):`
- 関連 Issue: Refs #121 #214
- 目的: デモタスク。X を Y に変更する。

## 権限
- git commit 可、push 不可、PR 不可、`rm -rf` 不可

## Codex セルフレビュー
検証深度 full。`codex` available なら commit 後、`codex exec review`（review surface）で差分セルフレビュー（直打ち長文プロンプト形は廃止。中小 diff で約 2 倍速・安全側パリティ同等）:
```bash
# --base はこのブランチのベース upstream（origin/main）。ローカルの追跡なしブランチは古いと別タスク差分を巻き込むため remote-tracking ref を使う。参照前に git fetch origin を 1 回（fetch 不能でも review は継続）。前景実行して出力を読んでから次へ進む。

# CODEX_HOME は「書き込み可能かつ一時ディレクトリでない」場所へ退避する（理由は直下）。
# 上書き前に既存の（認証済みの）codex home を控えてリンク元にする。
# （既定値つきパラメータ展開は brief 生成時のプレースホルダ検査に触れるため、同義の brace なし形で書く）
CODEX_SRC="$CODEX_HOME"
[ -n "$CODEX_SRC" ] || CODEX_SRC="$HOME/.codex"
export CODEX_HOME="$PWD/.codex-home"
# codex は session DB / cache / バイナリを CODEX_HOME に書く。作成前に worker ローカルの
# exclude に登録し、`git add -A` 等での誤 staging を防ぐ（.git/info/exclude は commit されない）。
grep -qxF '.codex-home/' "$(git rev-parse --git-path info/exclude)" 2>/dev/null \
  || echo '.codex-home/' >> "$(git rev-parse --git-path info/exclude)"
mkdir -p "$CODEX_HOME"
ln -sf "$CODEX_SRC/auth.json"   "$CODEX_HOME/auth.json"
ln -sf "$CODEX_SRC/config.toml" "$CODEX_HOME/config.toml"

# ログ名は worker ごとに分ける（$TMPDIR は並走 worker で共有。固定名だと別 worker の
# "succeeded in" を自分の成立根拠に取り違える）。basename だけでは別リポジトリの同名
# worktree で衝突しうるので、フルパス由来の識別子を付ける。
CODEX_REVIEW_LOG="$TMPDIR/codex-review-$(basename "$PWD")-$(printf %s "$PWD" | cksum | cut -d" " -f1).log"

# pipefail が無いとパイプの終了コードは tee のものになり codex 側の失敗が隠れる。
set -o pipefail
codex exec review --base origin/main -m gpt-5.6-sol -c model_reasoning_effort=medium -c sandbox_mode='"read-only"' < /dev/null 2>&1 | tee "$CODEX_REVIEW_LOG"
codex_status=$?
set +o pipefail
echo "codex exit status: $codex_status"
```

**`CODEX_HOME` 退避は必須（外すと下記「空の合格」を踏む）**: 既定の `~/.codex` はサンドボックスで書込不可 → codex が実行ヘルパーを配置できない → **コマンドを 1 つも実行できず `git diff` を一度も読まずにレビューを終える**。退避先は **書き込み可能** かつ **一時ディレクトリ配下でない** の 2 条件を両方満たすこと。**`$TMPDIR` 配下は不可**（codex が temp dir 配下へのヘルパー配置を明示的に拒否する）—「一時ファイルは `$TMPDIR` へ」の一般則に対する明示的な例外で、ここで `$TMPDIR` を使うと正直なエラーが「空の合格」に化けてかえって危険。`$PWD/.codex-home` は両条件を満たす。`.codex-home` は **commit しないこと**（`git status` で確認）。`-c sandbox_mode='"read-only"'` は codex 内側サンドボックスを**締める**設定で外側には触れない（緩める方向へ変えて回避しないこと。真因は緩さ不足ではなく `CODEX_HOME` の配置）。

**「空の合格」の検出（available かつエラー表示も無いのに未成立のケース）**: 上記設定を外すと codex は diff を読まないまま**「指摘なし」と読める文面で正常終了**する。**終了コードは成立判定に使えず（空の合格でも exit 0）**、**但し書きの有無も使えない**（環境により但し書きが消え `No actionable findings were identified` だけになる）。よって**「エラーが出ていない」（否定的証拠）ではなく「コマンドが実際に実行された」（肯定的証拠）で判定する**。review 後に必ず実行し、**中身を読む前に**判定すること:
```bash
grep -cE '^ *succeeded in [0-9]+(ms|s|m)' "$CODEX_REVIEW_LOG"   # 成功実行数: 1 以上が必要
grep -cE '^ *failed in [0-9]+(ms|s|m)' "$CODEX_REVIEW_LOG"      # 失敗実行数: 0 が必要
```
- **行頭アンカー（`^ *`）と実行時間（`[0-9]+(ms|s|m)`）を必ず両方付ける**。ログには diff 本文も codex の実行出力もそのまま載るため素の文字列 grep は自分自身にマッチする（実測: 素の grep は diff 側の記述に / 行頭アンカーだけでも ` failed in TUI` の断片にマッチ）。本物の記録は必ず実行時間を伴う
- **ゲート成立** = 成功数 **1 以上** かつ 失敗数 **0** かつ `codex_status` **0**。このときだけ「codex clean」と報告してよい。exit 0 は十分条件ではない（空の合格でも 0）が、**非 0 は失格条件として使える**ため必要条件に併用する
- **未成立なら「codex clean」と報告しない**。まず `CODEX_HOME` を見直して再実行（`$TMPDIR` 配下を疑う）。それでも成立しなければ **「Codex ゲート未成立（diff 未読の空の合格、HEAD=`<sha>`）」と明示**し、上記 2 数値を添えて窓口の判断を仰ぐ
- **前景実行する**（背景化 `&` はゲート素通り事故を招く）。Blocker/Major 修正、**round 既定上限 3**（brief の実装ガイダンスで別値指定があればそちら優先）
- **上限到達で自走継続せず**、残指摘 + 自己評価（設計問題化か収束途中か）を窓口に報告して停止。**同一指摘が 3 round 消えない場合は上限前でも即設計問題として報告**（別問題が各 1 round で順に解消する健全な収束とは区別）
- Minor/Nit 残置可
- **large diff では effort を上げない**（high-effort review は大 diff でスケールしない）。review surface は危険側 Major は守るが benign safe-side false-negative / ReDoS 級を取りこぼしうる（詳細: claude-org リポジトリの `knowledge/curated/codex.md`）
- `codex:rescue` skill 禁止、`codex exec review` / `codex exec` 系直打ちのみ。ChatGPT アカウントで通るモデル名は限られ、素の `gpt-5.6` / `gpt-5.6-codex` / `gpt-5.5-codex` は 400・API キー surface も不可（現行世代の `-m gpt-5.6-sol` 明示）

**完了報告に人間向け理解サマリを必須化（full）**: 窓口がコードを精読せず、そのままユーザーへの承認提示に使えるよう、完了報告に以下 3 点を必ず含める:
1. **最重要の変更点（N 個）**: 効果の大きい順に N 個（目安 3〜5 個、各 1〜2 行、diff を開かず要旨が掴める粒度）
2. **要確認ファイル / hunk**: 人間が承認前に必ず目を通すべきファイル / hunk（要点に絞る）
3. **設計判断と理由**: 採用した設計上の選択と、なぜそれを選んだか（却下した代替案があれば 1 行）

## 完了時
1. `mcp__renga-peers__send_message(to_id="secretary", ...)` で完了内容・変更ファイル・commit SHA・動作確認結果・残作業を報告
2. PR 作成後ペイン保持
3. 振り返り記録: 任意（非自明な学びがあれば `/home/user/work/claude-org/knowledge/raw/{YYYY-MM-DD}-{topic}.md`）

## SUSPEND
"SUSPEND:" → 即報告（完了したこと / 変更ファイル / 次の予定 / ブロッカー）
