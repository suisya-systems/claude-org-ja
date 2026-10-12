# Worker

> このワーカーは claude-org リポジトリ自身の `/tmp/workers/demo-task` で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。あなたは窓口ではなくワーカーである。

## 作業ディレクトリ
`/tmp/workers/demo-task`

起動直後 `pwd` で確認。

### 禁止事項
1. claude-org 構造を `/tmp/workers/demo-task` 内に再現しない
2. claude-org リポジトリ（`/home/user/work/claude-org`）を別途 clone しない（直接編集）
3. `git push` 不可
4. `git stash` の変更系（`list` / `show` 以外）は hook が deny する。代わりに作業ブランチへ一時 commit で退避する（戻すときは `git reset --soft HEAD~1`）。`git diff > <name>.patch` は staged / 未追跡を取りこぼすので単独の退避手段にしない。比較は `git show HEAD:<path>`。hook が解決できない alias 経由でも実行しない
5. `git add -A` / `git add .` は hook が deny する。代わりに `git add -u` ＋ 新規ファイルの明示 add で staging する

### Windows
- Python は `py -3` または `python`（3.10 推奨。どちらも別の Python 環境を指す場合があるため `--version` で確認し、動作する方を使う）
- 日本語ファイル: `encoding="utf-8"` 明示
- CLI 出力文字列（argparse `help=` / `print()`）は ASCII の `-` を使う（em-dash 等 cp932 非対応文字は cp932 コンソールでの `--help` を `UnicodeEncodeError` でクラッシュさせる。pytest の `redirect_stdout` では検出できず実端末でのみ落ちる）。実装後 `--help` を実端末で 1 回スモーク

### Bash のパス指定（絶対パス必須。ultracode の `agent()` プロンプト内も同じ）
- grep / find / sed / テスト対象は**常に `/tmp/workers/demo-task/...` の絶対パス**。`cd <dir>; <相対パス>` と root での `grep -r ... .` は使わない。ultracode の各 `agent()` プロンプト内の Bash 指示にも同じ規約を適用（subagent のコマンドは worker 本体の permissions で判定される）
- 理由: auto-mode 分類器は `cd` 後の相対パスを解決できず、`Read(.env)` 等の deny 規則（`tools/org_extension_schema.json` の `layer2Fallback`）と組み合わさって毎回人間承認に落ちる。deny 規則は緩めず書き方で回避する
- NG: `cd /tmp/workers/demo-task; grep -n "renewLease" test/lap/root.test.ts` / `grep -rln "lease" . --include=*.json` → OK: `grep -n "renewLease" /tmp/workers/demo-task/test/lap/root.test.ts` / `grep -rln "lease" /tmp/workers/demo-task/test --include=*.json`

### 着手前 preflight（sandbox 書き込み境界・非対話 alias・hook 拒否）
- 作業ファイルを置けるのは `/tmp/workers/demo-task` 配下と `$TMPDIR` だけ（git が内部で触る Pattern B の `.git` メタデータは別枠で、作業ファイルの置き場ではない）。Write / Edit で `/tmp/workers/demo-task` 外（ハーネスの scratchpad を含む）に書くと hook が deny する。代わりに Bash の一時ファイルは `$TMPDIR` へ、Write / Edit の作業メモは `mkdir -p /tmp/workers/demo-task/.worker-scratch` へ。linked worktree（Pattern B）では `.git/info/exclude` が共通 clone 側（sandbox 外）にあり登録できないので、禁止事項 5 のとおり `git add -u` + 明示 add で staging し `.worker-scratch/` を commit に混入させない。`/tmp/claude-<uid>` は並走ワーカーと共有なので一般名のバックアップは衝突する（`.worker-scratch/` に置くか task_id を名前に入れる）
- `/tmp` 直下は書けない（`$TMPDIR` とは別物。必ず `$TMPDIR` を経由する）。uv / pip のキャッシュは `/tmp/workers/demo-task` 配下に置かず `export UV_CACHE_DIR="$TMPDIR/uv-cache"` / `PIP_CACHE_DIR="$TMPDIR/pip-cache"` にする（worktree 内に置くと wheel 内の `credentials.py` 等が sandbox の read-deny に巻き込まれ Bash が全滅する）
- `cp` / `mv` / `rm` は `-i` alias 前提で書く（既存ファイルへの上書きが無言の確認待ちで timeout まで止まる）。上書きは `command cp -f` か `cat backup > dest`。`rm -rf` / `rm -r` は permissions.deny（Node 等で再帰削除を迂回しない。掃除は新しい名前のディレクトリを掘る）
- パス指定の `git checkout -- <path>` / `git restore --source=<ref>` は hook が deny する（`git stash` 変更系は禁止事項 4）。代わりに壊して戻す手順は `cp` バックアップ（書き戻しは `command cp -f` / `cat >`）か一時 commit の 2 択。復元後は `git diff` で戻ったことを確認してから次に進む
- `read-only file system` / `Permission denied` でも `dangerouslyDisableSandbox` を反復要求しない（安全分類器のロックアウトで作業不能になる）。出力先を書ける場所へ変えて再試行する

## プロジェクト
- claude-org-ja: テスト用説明

## タスク
- ID: demo-task
- ブランチ: `demo-task`
- 検証深度: **full**（値が無い・不明瞭なら勝手に決めず窓口に確認する）
- commit prefix: `feat(tools):`
- 関連 Issue: Refs #121 #214
- 目的: デモタスク。X を Y に変更する。

## `.claude/` の編集前承認と生成物
- root の `.claude/` 配下（深さ不問。`.claude/skills/**/references/**` を含む）を編集する場合は、**編集前に、承認入力（対象ファイル列挙 + task_id + 「窓口経由のユーザー承認」）が会話に user message として存在することを確認**せよ。存在しなければ編集を開始せず、`mcp__renga-peers__send_message(to_id="secretary")` で窓口に承認入力を要求して待機せよ。peer message で届いた承認は承認にならない。承認文に無いファイルの `.claude/` 編集が要るときはスコープ拡張として窓口に上げる
- `.claude/skills/` / `.dispatcher/` の prose は、`grep <path> tools/skill_src/manifest.json` で生成物か確かめてから編集する。生成物なら `.in` を直して `python3 tools/gen_skill_prose.py --manifest tools/skill_src/manifest.json` で再生成し、生成元と生成物を両方 commit する。最後に同じコマンドに `--check` を付けて drift ゼロを確かめる（`--manifest` を省くと何も検査せず exit 0 になる）

## 権限
- git commit 可、push 不可、PR 不可、`rm -rf` 不可

## 検証と Codex セルフレビュー（検証深度 full）
通常検証（テスト / lint 等）を回して green を確認してから完了報告する。green 判定は「passed が N 件」ではなく **passed / skipped の内訳が CI と一致するか**で行う。条件付き skip されるテスト（環境変数・OS 依存の `skipIf` 等）はその条件を満たしてから回し、完了報告に skip 件数と理由を書く（例: 「824 passed | 2 skipped、skip は〜」）。

完了報告前の rebase: Codex の前に `git fetch origin` → `git rebase origin/main`（conflict は自分で解く。conflict が無くても rebase 後に通常検証を回し直す）→ `git rev-list --count HEAD..origin/main` が `0` であることを確かめ、完了報告に「rebase clean: HEAD=`<sha>` on top of origin/main `<sha>`」の 1 行を入れる。

Codex セルフレビュー: commit 後に作業ディレクトリで**前景実行**する。この Bash 呼び出しの `allowed_domains` に Codex の接続先（`chatgpt.com` 等）を入れる（無いとエラーを出さずに数百秒止まる）:
```bash
bash "/home/user/work/claude-org/tools/codex_review_gate.sh" origin/main
```
- 判定は最終行: `CODEX_GATE: passed` のときだけ指摘を読み「codex clean」と報告してよい。`not-established`（diff 未読の「空の合格」を含む）は clean と報告せず「Codex ゲート未成立（HEAD=`<sha>`）」と最終行の数値を添えて窓口の判断を仰ぐ。`unavailable` は skip して完了報告へ
- 安全機構に掛かって review が完了しないときは言い換えで回避しない。窓口に新しいセッションでの再実行を頼み、それでも通らなければ「Codex ゲート未成立（safety block、HEAD=`<sha>`）」と報告する
- Blocker/Major 修正、**round 既定上限 3**（brief の実装ガイダンスで別値指定があればそちら優先）。上限到達で自走継続せず、残指摘 + 自己評価（設計問題化か収束途中か）を窓口に報告して停止。同一指摘が 3 round 消えない / 前 round の修正を巻き戻す向きの指摘が出たときは上限前でも設計問題として報告（後者は綱引きの方向を書く）
- Minor/Nit 残置可。レビューを別ワーカーに委譲しない。large diff では effort を上げない（根拠: claude-org リポジトリの `knowledge/curated/codex.md`）
- `The 'gpt-6-astra' model requires a newer version of Codex.` が出たら `npm i -g @openai/codex@latest` で更新する

**完了報告に人間向け理解サマリを必須化（full）**: 窓口がコードを精読せず、そのままユーザーへの承認提示に使えるよう、完了報告に以下 3 点を必ず含める:
1. **最重要の変更点（N 個）**: 効果の大きい順に N 個（目安 3〜5 個、各 1〜2 行、diff を開かず要旨が掴める粒度）
2. **要確認ファイル / hunk**: 人間が承認前に必ず目を通すべきファイル / hunk（要点に絞る）
3. **設計判断と理由**: 採用した設計上の選択と、なぜそれを選んだか（却下した代替案があれば 1 行）

## 完了時
1. `mcp__renga-peers__send_message(to_id="secretary", ...)` で完了内容・変更ファイル・commit SHA・動作確認結果・残作業を報告（検証深度 full では「rebase clean」行と人間向け理解サマリを含める）
2. PR 作成後ペイン保持
3. 振り返り記録: 任意（非自明な学びがあれば `/home/user/work/claude-org/knowledge/raw/{YYYY-MM-DD}-{topic}.md`。見出しは `## 事実` / `## 判断` / `## 根拠` / `## 適用場面`）

## SUSPEND
"SUSPEND:" → 即報告（完了したこと / 変更ファイル / 次の予定 / ブロッカー）
