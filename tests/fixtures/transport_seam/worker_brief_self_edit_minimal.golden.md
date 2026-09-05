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
- 検証深度: **minimal**
- commit prefix: `feat(tools):`
- 関連 Issue: Refs #121 #214
- 目的: デモタスク。X を Y に変更する。

## 権限
- git commit 可、push 不可、PR 不可、`rm -rf` 不可

## Codex セルフレビュー
検証深度 minimal。minimal 用 1 行報告フォーマットを使用（`done: {SHA} {files}`）。Codex セルフレビュー・追加テスト・拡張された動作確認は一切禁止。

## 完了時
1. `mcp__renga-peers__send_message(to_id="secretary", ...)` で完了内容・変更ファイル・commit SHA・動作確認結果・残作業を報告
2. PR 作成後ペイン保持
3. 振り返り記録: 任意（非自明な学びがあれば `/home/user/work/claude-org/knowledge/raw/{YYYY-MM-DD}-{topic}.md`）

## SUSPEND
"SUSPEND:" → 即報告（完了したこと / 変更ファイル / 次の予定 / ブロッカー）
