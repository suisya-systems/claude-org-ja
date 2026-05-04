# Projects Registry

既知のプロジェクト一覧。窓口Claudeがユーザーの依頼からプロジェクトを特定するために使う。
ワーカー派遣時に自動登録される。手動で追記・編集してもよい。

「パス」列はプロジェクトの clone ソースを記録する。値によってワーカー派遣時の初期化手順が分岐する:

- URL（例: `https://github.com/...`）→ リモートリポジトリ。`git clone {URL} {worker_dir}` で取得
- ローカルパス（例: `C:/Users/.../existing-repo`）→ ローカル既存プロジェクト。`git clone {ローカルパス} {worker_dir}` で取得
- `-` → 新規プロジェクト（clone 元なし）。`git init {worker_dir}` で初期化（clone は実行しない）

注意: この列はワーカーの成果物パスを示すものではない（ワーカーは Issue #267 live-migration 後の 3 段階層 `workers/<project>/_runs/<workstream>/<run>/`（リサーチ系は `_research/`、検証用は `_scratch/`、コールド成果物は `_archive/`）内で作業する）。
この下の Markdown 表はワーカー派遣前に `dashboard/server.py:_parse_projects` で機械パースされるため、本セクションに追加の Markdown 表（`|---|` セパレータ付き）を差し込まないこと。説明を増やす場合はプレーン箇条書きで記述する。

| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |
|---|---|---|---|---|
| 時計アプリ | clock-app | - | Webブラウザで動くデジタル時計 | デザイン変更、機能追加 |
| renga | renga | https://github.com/suisya-systems/renga | Rust 製の Claude Code 用ターミナルマルチプレクサ（TUI） | 機能追加、バグ修正、Issue 対応 |
| claude-org-ja | claude-org-ja | https://github.com/suisya-systems/claude-org-ja | Claude Code 多役 AI 組織ハーネス（Secretary / Dispatcher / Curator / Worker）日本語版本体 | スキル改善、ドキュメント追記、Issue 対応 |
