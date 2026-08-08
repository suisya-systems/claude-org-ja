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
