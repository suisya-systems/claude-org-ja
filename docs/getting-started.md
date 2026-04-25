# Getting Started

claude-orgの使い方ガイド。

---

## セットアップ

### 前提条件

以下が全てインストール・設定済みであること。詳細は [README.md](../README.md#前提条件) を参照。

- **Claude Code** — AIエージェント本体
- **ccmux** — ターミナルマルチプレクサ (組織のペイン管理に使用)
- **ccmux-peers MCP** — 同タブ内インスタンス間通信とペイン操作（`ccmux mcp install` で登録）
- **GitHub CLI (`gh`)** — 認証済み（`gh auth status` で確認）

### インストール

本リポジトリをクローンし、そのディレクトリで ccmux を起動する。

```bash
git clone <このリポジトリの URL>
cd <クローン先>
ccmux mcp install              # 初回のみ。ccmux-peers MCP を user-scope 登録
ccmux --layout ops
```

`ccmux-layouts/ops.toml` の定義に従って窓口 (Secretary) ペインが立ち上がる。
窓口の Claude Code が立ち上がったら、**順に以下を実行する**:

1. `/org-setup` — ロール別 `settings.local.json`（窓口・フォアマン・キュレーター・ワーカー）と必須 hook を配置。**初回のみ必須**。未実行だと ccmux-peers MCP / git / gh で大量の許可プロンプトが出る。
2. `/org-start` — 組織を起動。フォアマンとキュレーターが同一タブ内に派生する。

`/org-setup` は **additive-only**（不足分を追加するだけで既存を消さない）。drift を baseline に戻したい場合は [`.claude/skills/org-setup/references/permissions.md`](../.claude/skills/org-setup/references/permissions.md) のロール別サンプル JSON で `settings.local.json` を手動置換する。

### 互換性プリフライト（任意、推奨）

`/org-start` を実行する前に、ccmux のバージョンと MCP ツール surface が claude-org の要件を満たすか検証できる:

```bash
py -3 tools/check_ccmux_compat.py            # Windows
python3 tools/check_ccmux_compat.py          # macOS / Linux
```

- ccmux バージョン（0.18.0 以上を要求）
- `ccmux-peers` MCP 登録 (`claude mcp list` で Connected)
- 必須 14 ツールが tools/list に出現するか

機械可読 JSON が欲しい場合は `--json`。フェイルを終了コードで扱いたいスクリプトはこちらを使う:

```bash
py -3 tools/check_ccmux_compat.py --json
```

このスクリプトは live ccmux セッションを必要としない（静的 + MCP stdio probe のみ）ので、`ccmux --layout ops` の前にも後にも実行できる。

---

## 基本的な使い方

### 起動する

初回 clone 後は、上の「インストール」節に従って `/org-setup` → `/org-start` の順で 1 回だけ実行する（`/org-setup` 未実行だと許可プロンプトが多発する）。

2 回目以降は `ccmux --layout ops` で窓口ペインを開き、Claude Code で `/org-start` を実行するだけでよい。
前回の状態があれば報告され、フォアマン（作業割り当て担当）とキュレーター（知見整理担当）が自動で起動する。

```
あなた: /org-start
窓口:   組織を起動しました。
        前回の状態: ブログ記事更新が完了、ECサイト修正が途中です。
        フォアマンとキュレーターを起動しました。
        何をしますか？
```

### やりたいことを伝える

やりたいことをそのまま伝える。
技術的な言葉を使う必要はない。窓口Claudeが内容を理解し、適切なワーカーに作業を割り当てる。
相談や質問には窓口が直接答える。

```
あなた: ブログに新しい記事を追加したい
窓口:   ブログですね。記事のタイトルと内容を教えてください。
```

窓口は登録済みプロジェクトの中から「ブログ」を特定し、適切に作業を進める。
どのプロジェクトか分からないときは聞き返してくれる。

```
窓口:   どのプロジェクトですか？
        ・ブログ（会社ブログサイト）
        ・ECサイト（EC販売サイト）
        ・管理画面（社内管理ツール）
```

### 複数の作業を同時に頼む

```
あなた: ブログの記事を更新して、あとECサイトの商品ページも直して
窓口:   2つの作業を同時に進めます。
        ・ブログ: 記事更新
        ・ECサイト: 商品ページ修正
        それぞれ担当を割り当てました。完了したら報告します。
```

裏側で別々のワーカーが並行して作業する。完了するとまとめて報告が届く。

### 作業結果の確認

窓口は技術用語を避けて報告する。

```
窓口:   ブログの記事更新が完了しました。
        ・新しい記事「2026年春の新商品」を追加しました
        ・変更内容を提出済みです。レビュー待ちの状態です
```

### 中断する

```
あなた: 今日は終わり
窓口:   組織を中断しました。状態は保存済みです。
```

端末を安全に閉じてよい。

### 再開する

次に本リポジトリのディレクトリで `ccmux --layout ops` を起動して窓口の Claude Code に入ると、自動的に前回の状態を報告する。

```
窓口:   前回の状態（4/5 18:30に中断）:
        ・ブログ記事更新: 完了
        ・ECサイト商品ページ: 作業途中（60%）
        ・テスト追加: まだ手をつけていません
        続けますか？
あなた: ECサイトの続きからお願い
窓口:   承知しました。ECサイトの作業を再開します。
```

---

## トラブルシューティング

### 起動時に大量の許可プロンプトが出る

**症状**: 窓口・フォアマン・ワーカーのいずれかで `mcp__ccmux-peers__*` / `git` / `gh` 系ツール呼び出しのたびに許可ダイアログが立つ。

**診断**: まず該当ロールの `settings.local.json` の状態を確認する。

```bash
python tools/check_role_configs.py --include-local
```

特定 role の worktree 内で実行する場合は `--role <secretary|foreman|curator|worker>` を併用する。出力で role 別の missing / unknown allow と必須 hook の欠落が列挙される。

**対処**:

- **`settings.local.json` が存在しない / 必須 allow と hook が大量に missing**: 窓口の Claude Code で `/org-setup` を実行する（additive-only なので既存設定は壊れない）。実行後にもう一度 `check_role_configs.py` で missing が解消されたか確認する。
- **missing が局所的（特定の allow が 1〜2 件足りないだけ）**: schema → `permissions.md` → 実 `settings.local.json` の順で当該エントリを足す（次節の drift 解消フローと同じ手順）。

### `tools/check_role_configs.py` が schema/permissions/settings の drift を報告する

**症状**: CI または手元の `python tools/check_role_configs.py --include-local` が `unknown allow entry` / `permissions.md mismatch` / `missing required hook` を報告する。

**診断**: drift の発生源を「schema 側 / permissions.md 側 / 実 settings.local.json 側」のどこかに切り分ける。

```bash
python tools/check_role_configs.py --include-local        # 全 role を一括検証
python tools/check_role_configs.py --role <role>          # 当該 role の worktree で個別検証
git diff tools/role_configs_schema.json                   # schema 側の最近の編集を確認
git diff .claude/skills/org-setup/references/permissions.md
```

正典は `tools/role_configs_schema.json`。**ルール追加・修正は必ず schema → `permissions.md` → 実 `settings.local.json` の順で反映する**（README の「ロール別設定の source of truth」節と整合）。逆順にすると CI が drift を検出する。

**対処** — 切り分け結果ごとに:

- **schema に未登録の allow が `permissions.md` または実 settings に混入している場合**: 必要なエントリなら schema にまず追記してから permissions.md / settings.local.json に展開する。不要なら該当エントリを削除する。
- **`permissions.md` のサンプル JSON が schema と乖離している場合**: schema を正と見なし、`permissions.md` 側を schema に合わせて書き直す。
- **`/org-setup` 再実行後も `settings.local.json` に drift が残る場合**: additive-only なので自動では消えない。`.claude/skills/org-setup/references/permissions.md` のロール別サンプル JSON で該当ロールの `settings.local.json` を**丸ごと置換**して baseline に戻す（**last resort**）。worker サンプルの `{worker_dir}` / `{claude_org_path}` プレースホルダは置換時に実環境の絶対パスへ手で解決する必要がある。ローカル独自に追加していた override があれば事前に控えておくこと。

### schema の JSON parse エラー / 読み込み失敗

**症状**: `check_role_configs.py` または `/org-setup` が schema 読み込み時に即時 fail する（JSON syntax error 等）。

**診断**:

```bash
git status tools/role_configs_schema.json
git diff tools/role_configs_schema.json
python -c "import json; json.load(open('tools/role_configs_schema.json'))"
```

**対処**: 直近の編集で壊しているなら `git restore tools/role_configs_schema.json` で戻すか、未コミットの変更を一旦 `git stash push tools/role_configs_schema.json` で退避してから再挑戦する。schema 構文を修正したら必ず `python tools/check_role_configs.py --include-local` を通してから commit する。

---

## ダッシュボードで全体像を見る

「ダッシュボード見せて」と言えば、ブラウザで組織の全体像を確認できる。

```
あなた: ダッシュボード見せて
窓口:   （ライブサーバーを起動してブラウザで http://localhost:8099 を開く）
```

ダッシュボードには以下が表示される:

- **プロジェクト一覧** — 登録済みプロジェクトと、よくある作業例
- **作業状況** — 現在進行中・完了・保留の作業アイテム
- **最近のアクティビティ** — いつ何が起きたかのタイムライン
- **蓄積された知見** — テーマ別にどんな知見が溜まっているか

ダッシュボードはSSEで自動更新される。ブラウザをリロードしなくてもリアルタイムで最新状態が反映される。

---

## スキル一覧

| コマンド | 用途 | いつ使うか |
|---|---|---|
| `/org-start` | 組織の起動 | **Claude Code 起動直後に1回実行する** |
| `/org-delegate` | 作業の割り当て | 作業依頼時に自動発動（窓口は司令塔、実作業はワーカー） |
| `/org-suspend` | 作業の中断 | 「終わり」「中断」と言えば自動発動 |
| `/org-resume` | 作業の再開 | 前回中断していた場合に org-start から自動呼び出し |
| `/org-retro` | 学びの記録 | 作業完了後（多くは自動発動） |
| `/org-curate` | 知見の整理 | 自動実行。手動でも可 |
| `/org-dashboard` | ダッシュボード表示 | 「ダッシュボード見せて」で発動 |

基本的に、スキルを意識して呼ぶ必要はない。
窓口Claudeが状況に応じて適切なスキルを使う。

---

## ディレクトリ構造

```
claude-org/
  CLAUDE.md              <- 窓口Claudeの行動指針
  .claude/skills/        <- 組織のスキル群（git管理）
  .state/                <- セッション状態（git管理外）
  dashboard/             <- ダッシュボード（HTML/CSS/JS/server.pyはgit管理）
  knowledge/
    raw/                 <- 生の学び（git管理外）
    curated/             <- 整理済み知見（git管理）
  registry/
    projects.md          <- プロジェクト一覧（自動登録）
  docs/                  <- ドキュメント
```

### 自分で触るもの
- `knowledge/curated/` — 整理された知見を確認する（自動生成される）

### 触らなくていいもの
- `registry/projects.md` — 作業依頼時に自動登録される
- `dashboard/` — ダッシュボードのデザインとデータ。自動管理される
- `.claude/skills/` — スキル定義。組織の成長に伴い自動改善提案される
- `.state/` — セッション状態。自動管理される
- `CLAUDE.md` — 変えたくなったら変えてよいが、薄く保つこと

---

## 知見の蓄積と成長

組織は使うほど賢くなる。

1. 作業が完了するたびに、学びが記録される
2. 30分ごとに自動で整理される（5件以上溜まったとき）
3. 整理された知見はテーマ別に保存される
4. スキルやプロセスの改善が必要な場合、提案される
5. 承認すると改善が反映され、次回から組織全体が改善された状態で動く

---

## Tips

- **端末を突然閉じても大丈夫**: 状態は定期的に保存されている。次回起動時に復元される。ただし「今日は終わり」と言って正式に中断したほうが、より正確な状態が残る。
- **作業が多すぎると思ったら**: 「1つずつやって」と言えばよい。窓口は人間の指示を優先する。
- **学びが的外れなら**: 「その学びは不要」と言えば記録されない。改善提案も却下できる。
- **プロジェクトの登録は自動**: 新しいプロジェクトの作業を依頼すると、名前や場所を確認してから自動登録される。
- **ダッシュボードはいつでも見られる**: 「ダッシュボード見せて」「全体像見せて」と言えばブラウザで開く。
