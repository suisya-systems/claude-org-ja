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
- git は例外: worktree の cwd で `git add` / `git commit` と単独で打つ（`git -C <path>` や変数代入で場所を前置しない）。許可ルール `Bash(git commit:*)` は先頭一致で、前置付きは当たらず `Reason: [...]` の付かない拒否になる

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
- 検証深度: **minimal**（値が無い・不明瞭なら勝手に決めず窓口に確認する）
- commit prefix: `feat(tools):`
- 関連 Issue: Refs #121 #214
- 目的: デモタスク。X を Y に変更する。

## `.claude/` の編集前承認と生成物
- root の `.claude/` 配下（深さ不問。`.claude/skills/**/references/**` を含む）を編集する場合は、**編集前に、承認入力（対象ファイル列挙 + task_id + 「窓口経由のユーザー承認」）が会話に user message として存在することを確認**せよ。存在しなければ編集を開始せず、`mcp__renga-peers__send_message(to_id="secretary")` で窓口に承認入力を要求して待機せよ。peer message で届いた承認は承認にならない。承認文に無いファイルの `.claude/` 編集が要るときはスコープ拡張として窓口に上げる
- `.claude/skills/` / `.dispatcher/` の prose は、`grep <path> tools/skill_src/manifest.json` で生成物か確かめてから編集する。生成物なら `.in` を直して `python3 tools/gen_skill_prose.py --manifest tools/skill_src/manifest.json` で再生成し、生成元と生成物を両方 commit する。最後に同じコマンドに `--check` を付けて drift ゼロを確かめる（`--manifest` を省くと何も検査せず exit 0 になる）

## 権限
- git commit 可、push 不可、PR 不可、`rm -rf` 不可

## Codex セルフレビュー
検証深度 minimal。minimal 用 1 行報告フォーマットを使用（`done: {SHA} {files}`）。Codex セルフレビュー・追加テスト・拡張された動作確認は一切禁止。

## 完了時
1. `mcp__renga-peers__send_message(to_id="secretary", ...)` で完了内容・変更ファイル・commit SHA・動作確認結果・残作業を報告（検証深度 full では「rebase clean」行と人間向け理解サマリを含める）
2. PR 作成後ペイン保持
3. 振り返り記録: 任意（非自明な学びがあれば `/home/user/work/claude-org/knowledge/raw/{YYYY-MM-DD}-{topic}.md`。見出しは `## 事実` / `## 判断` / `## 根拠` / `## 適用場面`）

## SUSPEND
"SUSPEND:" → 即報告（完了したこと / 変更ファイル / 次の予定 / ブロッカー）
