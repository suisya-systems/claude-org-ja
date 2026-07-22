# Worker

> このワーカーは claude-org リポジトリ自身の `${worker_dir}` で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。あなたは窓口ではなくワーカーである。

## 作業ディレクトリ
`${worker_dir}`

起動直後 `pwd` で確認。

### 禁止事項
1. claude-org 構造を `${worker_dir}` 内に再現しない
2. claude-org リポジトリ（`${claude_org_path}`）を別途 clone しない（直接編集）
3. `git push` 不可

### Windows
- Python は `py -3` または `python`（3.10 推奨。どちらも別の Python 環境を指す場合があるため `--version` で確認し、動作する方を使う）
- 日本語ファイル: `encoding="utf-8"` 明示
- CLI 出力文字列（argparse `help=` / `print()`）は ASCII の `-` を使う（em-dash 等 cp932 非対応文字は cp932 コンソールでの `--help` を `UnicodeEncodeError` でクラッシュさせる。pytest の `redirect_stdout` では検出できず実端末でのみ落ちる）。実装後 `--help` を実端末で 1 回スモーク

## プロジェクト
- ${project_name}: ${project_description}

## タスク
- ID: ${task_id}
- ブランチ: `${task_branch}`
- 検証深度: **${task_verification_depth}**
- commit prefix: `${task_commit_prefix}`
- 関連 Issue: ${closes_or_refs}
- 目的: ${task_description}
<!--BEGIN:issue_url-->- Issue URL: ${task_issue_url}
<!--END:issue_url-->
<!--BEGIN:implementation-->

### 実装ガイダンス
${implementation_target_files_block}${implementation_guidance_block}
<!--END:implementation-->
<!--BEGIN:parallel-->

## 並列タスクとの干渉
${parallel_notes}
<!--END:parallel-->
<!--BEGIN:references-->

## ナレッジ参照
${references_knowledge_block}
<!--END:references-->
<!--BEGIN:python_src_layout-->

## Python 検証規約（src-layout）
検証（pytest 等）は `PYTHONPATH=src` を前置して実行する（例: `PYTHONPATH=src python -m pytest`。stale install の shadow による phantom FAIL 防止）。共有 venv への editable install（`pip install -e`）は禁止（worktree 削除後に venv 残骸の `.pth` が ModuleNotFoundError を起こす）。
<!--END:python_src_layout-->

## 権限
- git commit 可、push 不可、PR 不可、`rm -rf` 不可

<!--BEGIN:codex_full-->
## Codex セルフレビュー
検証深度 full。`codex` available なら commit 後、`codex exec review`（review surface）で差分セルフレビュー（直打ち長文プロンプト形は廃止。中小 diff で約 2 倍速・安全側パリティ同等）:
```bash
# --base はブランチのベース（通常 origin/main）。ローカル main は古いと別タスク差分を巻き込むため remote-tracking の origin/main を使う。参照前に git fetch origin を 1 回（fetch 不能でも review は継続）。前景実行して出力を読んでから次へ進む。
codex exec review --base origin/main -m gpt-5.5 -c model_reasoning_effort=medium < /dev/null
```
- **前景実行する**（背景化 `&` はゲート素通り事故を招く）。Blocker/Major 修正、**round 既定上限 3**（brief の実装ガイダンスで別値指定があればそちら優先）
- **上限到達で自走継続せず**、残指摘 + 自己評価（設計問題化か収束途中か）を窓口に報告して停止。**同一指摘が 3 round 消えない場合は上限前でも即設計問題として報告**（別問題が各 1 round で順に解消する健全な収束とは区別）
- Minor/Nit 残置可
- **large diff では effort を上げない**（high-effort review は大 diff でスケールしない）。review surface は危険側 Major は守るが benign safe-side false-negative / ReDoS 級を取りこぼしうる（詳細: claude-org リポジトリの `knowledge/curated/codex.md`）
- `codex:rescue` skill 禁止、`codex exec review` / `codex exec` 系直打ちのみ。`gpt-5.5-codex` / API キー surface は不可（`-m gpt-5.5` 明示）

**完了報告に人間向け理解サマリを必須化（full）**: 窓口がコードを精読せず、そのままユーザーへの承認提示に使えるよう、完了報告に以下 3 点を必ず含める:
1. **最重要の変更点（N 個）**: 効果の大きい順に N 個（目安 3〜5 個、各 1〜2 行、diff を開かず要旨が掴める粒度）
2. **要確認ファイル / hunk**: 人間が承認前に必ず目を通すべきファイル / hunk（要点に絞る）
3. **設計判断と理由**: 採用した設計上の選択と、なぜそれを選んだか（却下した代替案があれば 1 行）

<!--END:codex_full-->
<!--BEGIN:codex_minimal-->
## Codex セルフレビュー
検証深度 minimal。minimal 用 1 行報告フォーマットを使用（`done: {SHA} {files}`）。Codex セルフレビュー・追加テスト・拡張された動作確認は一切禁止。

<!--END:codex_minimal-->
## 完了時
1. `${transport_send_message}(to_id="secretary", ...)` で完了内容・変更ファイル・commit SHA・動作確認結果・残作業を報告
2. PR 作成後ペイン保持
3. 振り返り記録: 任意（非自明な学びがあれば `${claude_org_path}/knowledge/raw/{YYYY-MM-DD}-{topic}.md`）

## SUSPEND
"SUSPEND:" → 即報告（完了したこと / 変更ファイル / 次の予定 / ブロッカー）
