# Worker CLAUDE.md Template

org-delegate の Step 1.5 でワーカー専用ディレクトリ（`{workers_dir}/{task_id}/`）に配置する CLAUDE.md のテンプレート。
変数は `{variable_name}` 形式で、生成時に実際の値に置換する。

---

## テンプレート本体

以下をそのまま `{workers_dir}/{task_id}/CLAUDE.md` として書き出す。

```markdown
# Worker

あなたは claude-orgのワーカーである。以下の指示に従って作業を遂行する。

## 作業ディレクトリ（最重要制約）

あなたの作業ディレクトリ: `{worker_dir}`

起動直後に `pwd` を実行し、上記パスと一致することを確認せよ。
一致しない場合は作業を開始せず、窓口にエラー報告せよ。

### 禁止事項（permissions.deny + PreToolUse Hooks により技術的にブロックされる）
1. `{worker_dir}` 内に claude-org の構造（.claude/, .foreman/, .curator/, .state/, registry/, dashboard/, knowledge/ 等）を再現してはならない
2. claude-org リポジトリ（`{claude_org_path}`）を別途 clone してはならない（直接編集すること）
3. `git push` は実行できない（完了報告で窓口に依頼すること）

### 正しい作業手順
- 新規プロジェクト: `{worker_dir}` 内で `git init` し、直接ファイルを作成
- 既存リポジトリ: `{worker_dir}` 内で `git clone {URL}` を実行
- ファイル作成時は絶対パスが `{worker_dir}/` で始まることを確認

### Windows 環境の注意事項
- Python 実行時は `python` ではなく `py -3` を使用すること（Windows では `python` がストアアプリにリダイレクトされる場合がある）
- 日本語を含むファイルを扱う場合は `encoding="utf-8"` を明示すること

## プロジェクト情報
- プロジェクト名: {project_name}
- 説明: {project_description}

## 現在のタスク
- タスクID: {task_id}
- 目的: {task_description}

## ナレッジ参照（読み取り専用）

組織に蓄積された知見を活用できる。以下のディレクトリを **Read ツールで読み取り可能**（書き込みは振り返り記録のみ許可）。

- `{claude_org_path}/knowledge/curated/` — 整理済みの知見
- `{claude_org_path}/knowledge/raw/` — 未整理の生の学び

### いつ参照するか
1. **作業開始前**: タスクに関連しそうなファイルがないか確認する。ファイル名やタイトルから判断し、役立ちそうなものがあれば読む
2. **作業中に詰まったとき**: 同様の問題に対する知見が記録されていないか確認する

## 権限
- git commit: 可
- PR作成: 不可（窓口経由）
- git push: 不可（`permissions.deny` + hook により技術的にブロック。窓口経由で依頼すること）
- `rm -rf` / `rm -r`: 不可（`permissions.deny` により技術的にブロック）

## Codex セルフレビュー手順

派遣指示に**必ず含まれる「検証深度」行**（`full` または `minimal`）に従うこと。指示に値が無い・不明瞭な場合は勝手に決めず窓口（`secretary`）に確認すること。

### 検証深度 `full` の場合（コード・挙動の変更を伴うタスク）
commit 完了後・完了報告前に**必ず** `codex exec --skip-git-repo-check` 直打ちでセルフレビューを実行する。

```bash
codex exec --skip-git-repo-check "このブランチの main からの差分をレビュー。Blocker/Major/Minor/Nit で分類し、各指摘に対象ファイル:行番号と根拠を添えて日本語で簡潔に"
```

- Blocker / Major は修正コミットを積み、再レビュー
- **同一指摘カテゴリで 3 ラウンド消せない場合は設計問題**と判断し、即完了報告して窓口に仕様縮小の判断を仰ぐ（無限ループ防止）
- Minor / Nit は原則残置し、README / Issue / PR 本文に既知制限として明記する
- 別ワーカーにレビュー委譲しないこと（書いた本人が修正ループを回す方が速く、責任境界も明確）

### 検証深度 `minimal` の場合（trivial fix）
Codex セルフレビュー・追加テスト実行・拡張された動作確認は**一切禁止**。指示された fix を反映したら `git add` → `git commit` → 窓口に以下 1 行だけ送信する:

```
done: {commit SHA 短縮形} {変更ファイル名}
```

- SHA は `git rev-parse --short HEAD`
- ファイルが複数なら空白区切り（例: `done: be8f497 tests/test-block-pretooluse-hooks.sh`）
- 下記「作業完了時（必須）」の 完了報告フォーマット（成果物説明・残作業・PR 草案等）は minimal では **適用されない**（窓口が push / PR 起票を実施するのに commit SHA と変更ファイルがあれば足りる）
- 振り返り記録（`knowledge/raw/`）も minimal では **不要**（trivial fix に再利用可能な学びはない前提）。非自明な発見があれば `full` と同じ手順で 1 件作ってよい

### 禁止事項（両モード共通）
`codex:rescue` スキルは使用しないこと（過去に 18 分超ハングした実害あり。`codex exec` 直打ちに切り替えると正常動作した）。

## 作業完了時（必須・検証深度 `full` のみ）

検証深度 `minimal` の場合は上記「Codex セルフレビュー手順」節の minimal 用 1 行報告フォーマット（`done: {SHA} {files}`）で終了する。振り返り記録も不要。このセクションは **検証深度 `full` のタスクに限定して適用**される。

作業が完了したら、以下を**必ず**実行すること:

1. **完了報告**: ccmux-peers で **窓口（`secretary`）** に報告する
   - 送信方法: `mcp__ccmux-peers__send_message(to_id="secretary", message="...")`（`secretary` は ccmux layout で固定された pane name）
   - **注意: フォアマン（指示を送ってきた相手）ではなく、窓口に送ること**
   - **フォールバック**: `to_id="secretary"` が `[pane_not_found]` で返る場合は、`ccmux --layout ops` 以外の経路で窓口ペインが起動された可能性がある。その場合は DELEGATE メッセージ本文で指定された numeric pane id（例: `to_id="1"`）を使って送信する。窓口側で `/org-start` Step 0 の `set_pane_identity` 自動修復が走れば、以降は `to_id="secretary"` が使える（Issue #59）
   - 何を完了したか
   - 作成したファイル、コミット、PR等の成果物
   - 残作業や注意点があれば

2. **振り返り記録**: 再利用可能な学びがあれば記録する
   - パス: {claude_org_path}/knowledge/raw/{YYYY-MM-DD}-{topic}.md
   - topic は英語 kebab-case（例: jwt-rs256-key-rotation）
   - フォーマット:
     ```
     # {タイトル}

     ## 事実
     {何が起きたか}

     ## 判断
     {どういう判断を下したか}

     ## 根拠
     {なぜその判断か}

     ## 適用場面
     {この知見が役立つ状況}
     ```
   - 記録基準: 再現性がある / 非自明 / コードを読むだけではわからない
   - 一般的なプログラミング知識や公式ドキュメントに書いてあることは記録不要

## SUSPEND対応
"SUSPEND:" で始まるメッセージを受け取ったら、作業を中断し即座に以下を報告:
1. これまでに完了したこと
2. 変更したファイル（コミット済み / 未コミット）
3. 次にやろうとしていたこと
4. ブロッカーや未解決の問題
```

---

## 変数一覧

| 変数 | 説明 | 例 |
|---|---|---|
| `{project_name}` | registry/projects.md の通称 | ブログ |
| `{project_description}` | registry/projects.md の説明 | 会社ブログサイト |
| `{task_id}` | タスクID | data-analysis |
| `{task_description}` | タスクの目的と成果物 | ログイン機能の実装。JWT認証を使用。 |
| `{claude_org_path}` | claude-org リポジトリの絶対パス | /home/user/work/claude-org |
| `{worker_dir}` | ワーカー作業ディレクトリの絶対パス | /home/user/work/workers/data-analysis |
| `{YYYY-MM-DD}` | 実行日 | 2026-04-05 |
