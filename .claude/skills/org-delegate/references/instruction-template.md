# ワーカーへの指示テンプレート

renga-peers の send_message（`to_id="worker-{task_id}"`）で送信する、タスク固有の指示。
権限・報告先・SUSPEND対応・知見記録の指示は worker-claude-template.md（CLAUDE.md経由）に一元化されているため、ここでは繰り返さない。

## テンプレート

```
以下のタスクを遂行してください。詳細な行動規範は CLAUDE.md に記載されています。

## タスク
{タスクの目的と期待する成果物を具体的に記述}

## プロジェクトの準備
重要: あなたの作業ディレクトリは CLAUDE.md に記載された絶対パスです。
まず `pwd` を実行し、CLAUDE.md の作業ディレクトリと一致することを確認してください。
全てのファイル作成はこのディレクトリ内に限定。`..` への移動や claude-org 構造の再現は禁止です。
{ディレクトリパターンに応じて以下のいずれかを指示}

### パターン A（プロジェクトディレクトリ・初回）の場合:
{以下のいずれかを指示}
- ローカル既存プロジェクトの場合: カレントディレクトリ内で `git clone {ローカルパス}` を実行してください。
- リモートリポジトリの場合: カレントディレクトリ内で `git clone {URL}` を実行してください。
- 新規プロジェクトの場合: カレントディレクトリ（`pwd` の出力パス）で `git init` し、直接ファイルを作成してください。claude-org の構造を真似たディレクトリ（.claude/, .state/ 等）を作らないでください。

### パターン A（プロジェクトディレクトリ・再利用）の場合:
このディレクトリは以前のタスクで使用されたプロジェクトディレクトリです。既存のファイルとgit履歴が残っています。
clone は不要です。{引き継ぎ事項があれば記載}

### パターン B（worktree）の場合:
このディレクトリは git worktree として準備済みです。ブランチ `{branch_name}` にチェックアウトされています。
clone は不要です。そのまま作業を開始してください。

### パターン C（エフェメラル）の場合:
{以下のいずれかを指示}
- 既存リポジトリの場合: カレントディレクトリ内で `git clone {URL}` を実行し、クローンされたディレクトリ内で作業してください
- ローカル既存プロジェクトの場合: カレントディレクトリ内で `git clone {ローカルパス}` を実行してください
- 新規プロジェクトの場合: カレントディレクトリ（`pwd` の出力パス）で `git init` し、直接ファイルを作成してください。claude-org の構造を真似たディレクトリ（.claude/, .state/ 等）を作らないでください

## ブランチ戦略
{ブランチ名の指定、またはmainで直接作業等}

## 作業の進め方
auto モードで直接作業してください。Plan モードは使用しないこと。

## 制約
{使用言語、フレームワーク、テスト要件等があれば記載}

## 検証深度: {full | minimal}
この行はテンプレートから**削除せず必ず送信する**。窓口は 2 値のどちらか 1 つだけを埋める。
既定は `full`。trivial fix のときのみ `minimal` を窓口側で選択して埋めること。

- **full**（新機能実装 / 修正 / リファクタ / テスト追加 / hook・skill・設定編集など、コードまたは挙動の変更を伴うもの）
  - **codex の有無に関わらず必須**: 既存テストスイート / lint / type-check 等のリポジトリ通常検証を green まで実行し、通常の完了報告フォーマット（成果物説明・残作業・PR 草案 / 振り返り記録）で報告
  - **追加ゲート（任意）**: commit 完了後に `codex` CLI が available なら `codex exec --skip-git-repo-check` で Codex セルフレビューを実行する
    - 確認コマンド: `command -v codex`（Bash/zsh）/ `Get-Command codex -ErrorAction SilentlyContinue`（PowerShell）
    - codex 未導入環境ではセルフレビューを skip し、上記の通常検証だけで完了報告に進む（以下のラウンド規律は適用されない）
  - **以下は codex を実行した場合のみ適用**:
    - Blocker / Major は修正コミットを積んでから完了報告
    - **同一指摘カテゴリ（例: loose match 精緻化 / 型絞り等）で 3 ラウンド消せない場合は設計問題**。即完了報告し、窓口に仕様縮小の判断を仰ぐ（無限ループ防止）
    - Minor / Nit は原則残置。README / Issue / PR 本文に既知制限として明記する
    - `codex:rescue` スキルは使用しないこと（18 分超ハング事例あり、`codex exec` 直打ちが安定）
  - レビュー指示例: `codex exec --skip-git-repo-check "このブランチの main からの差分をレビュー。Blocker/Major/Minor/Nit で分類し、各指摘に対象ファイル:行番号と根拠を添えて日本語で簡潔に"`

- **minimal**（trivial fix: CI 出力整形 / typo / コメント修正 / 既存テスト形式合わせ等、指示で変更箇所が 1 ファイル数行に限定されるもの）
  - 指示された fix を反映 → `git add` → `git commit` 直行
  - Codex セルフレビュー・追加テスト実行・差分確認を超えた動作検証は**一切禁止**
  - 完了報告は窓口（`secretary`）に送信する 1 行:
    - `done: {commit SHA 短縮形} {変更ファイル名}`（例: `done: be8f497 tests/test-block-pretooluse-hooks.sh`）
    - SHA は `git rev-parse --short HEAD`、ファイル名は単独ファイルなら 1 つ、複数なら空白区切り
    - これ以外の情報（成果物説明・PR 草案・残論点等）は不要。push / PR 起票は窓口側で実施する
  - 振り返り記録（`knowledge/raw/`）は minimal では**不要**（trivial fix に再利用可能な学びはない前提）。非自明な発見があれば `full` と同様 1 件作ってよい

**選択は窓口の責任**。ワーカーは指示に書かれた値（`full` or `minimal`）にそのまま従い、自分で切り替え判断をしないこと。派遣時にこの行自体が送信されなかった・または値が不明瞭だった場合はワーカーから窓口に確認すること（勝手に `full` にフォールバックしない）。
```

## cross-cutting operational change の場合の consistency grep target list

運用モード・共通設定・命名規約のような **cross-cutting な変更**（1 ファイルに閉じず、複数のロール / skill / 設定 / ドキュメントを横断する変更）を委譲するときは、ワーカー指示の「制約」または「タスク」セクションに、整合性確認のための grep スコープを明示する。スコープを書かないと、ワーカーは目に付いたファイルだけ直して、別ロール側 / docs 側の同名参照を取りこぼす（rename / mode 変更で起きやすい）。

### 「cross-cutting」と判定する例

- **運用モード変更**: Plan / auto / `bypassPermissions` 等のデフォルト切り替え
- **permissions / hook 設定の wholesale 変更**: `.claude/settings*.json` の allow / deny / hooks を横断的に書き換える
- **communication channel / MCP server 名変更**: renga-peers の peer 名・MCP サーバ名・ロール識別子の rename（例: `foreman` → `dispatcher`）
- **共通 flag / env var の追加削除**: 全ロール / 複数 skill が読む環境変数や CLI flag

逆に、1 つの skill / 1 つのロール内に閉じる挙動変更（例: `org-retro` 内のフォーマット調整）は cross-cutting ではないので、このセクションは不要。

### 推奨 grep target ディレクトリ

cross-cutting と判定したら、**最低限以下を grep スコープとしてワーカー指示に列挙する**。プロジェクト構成によって存在しないものは適宜削る:

- `.claude/skills/` — 全 skill の SKILL.md / references
- `registry/` — projects.md / org-config.md / worker-directory.md
- `knowledge/curated/` — 蓄積された運用知見（旧名で書かれた pattern が残りやすい）
- `dashboard/` — JSON 生成スクリプト・テンプレ
- `.dispatcher/` — フォアマン（dispatcher）ロールの runtime / プロンプト
- `.curator/` — キュレーターロールの runtime / プロンプト
- `docs/` — 公開ドキュメント
- `tools/` — チェッカ・補助スクリプト（`check_role_configs.py` 等）

ワーカー指示の例:

```
## 制約
- 旧名 `foo` の参照が以下のディレクトリに残っていないか grep し、見つけたら全て新名 `bar` に置換すること:
  - .claude/skills/
  - registry/
  - knowledge/curated/
  - dashboard/
  - .dispatcher/
  - .curator/
  - docs/
  - tools/
- grep コマンド例: `grep -rn "foo" .claude/skills/ registry/ knowledge/curated/ dashboard/ .dispatcher/ .curator/ docs/ tools/`
```

委譲時点で旧名 / 新名が確定していない場合は、ワーカーに「対象パターンを検出して一覧化 → 窓口に確認 → 置換」の 2 段で動かすこと。

## 使用時の注意

- タスクの記述は具体的に。曖昧な指示はワーカーの判断コストを上げる
- 制約がある場合は必ず明示する
