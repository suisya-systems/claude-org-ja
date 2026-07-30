# Projects Registry (Template)

**これはテンプレートである。組織が実際に読むのは `registry/projects.md`（本ファイルの隣）で、そちらは operator-local な生成ファイルであり git 管理下に無い（Issue #811）。**

- **本ファイル（`registry/projects.example.md`）** — リポジトリにコミットされる。列スキーマ・仕様説明・一般サンプル行だけを持ち、operator 固有のプロジェクト行は**決して入れない**。
- **`registry/projects.md`** — operator-local。`.gitignore` 済みで、初回の [`/org-start`](../.claude/skills/org-start/SKILL.md) が本テンプレートから生成する（生成器は [`tools/ensure_projects_registry.py`](../tools/ensure_projects_registry.py)）。既に存在する場合は**絶対に上書きされない**。

読み手ツール（`dashboard/server.py` / [`tools/registry_parser.py`](../tools/registry_parser.py) / [`tools/gen_delegate_payload.py`](../tools/gen_delegate_payload.py) / [`tools/work_discovery_repos.py`](../tools/work_discovery_repos.py) / [`tools/resolve_worker_layout.py`](../tools/resolve_worker_layout.py) / [`tools/state_db/importer.py`](../tools/state_db/importer.py)）は従来どおり `registry/projects.md` を読む。本テンプレートの導入でインターフェースは変わっていない。

既存 checkout を移行する手順（pull でローカルの登録行を失わないための退避・復元）は [`docs/operations/registry-projects-migration.md`](../docs/operations/registry-projects-migration.md) を参照。

## 生成範囲のマーカー

下の `<!-- BEGIN-LIVE-REGISTRY -->` **より後ろだけ**が `registry/projects.md` へコピーされる。ここまでのテンプレート説明は実体ファイルに混入しない。マーカー行そのものも出力されない。

マーカーが見つからない場合、生成器は fail loud で停止する（テンプレート全体を黙ってコピーして実体ファイルにテンプレート説明を埋め込む事故を避けるため）。

## スキーマを更新するとき

列を足す / 意味を変える変更は、マーカーより後ろの表ヘッダーを編集して commit する。既に `registry/projects.md` を持っている operator の手元ファイルは**自動では書き換わらない**（上書きしない契約のため）。代わりに、`/org-start` が毎回 [`tools/ensure_projects_registry.py`](../tools/ensure_projects_registry.py) を呼び、手元ファイルのヘッダーが本テンプレートより古いと **warning を出す**（非 fatal）。operator はその警告を見て手元の表ヘッダーへ列を追記する。

列の追加は後方互換であること（未記入セル = 従来動作）を保つこと。パーサー（[`tools/registry_parser.py`](../tools/registry_parser.py)）は header 名で列を対応付け、未知の追加列を無視し、列が足りない旧行も legacy として読むため、既存行を編集せずに列を足せる。

<!-- BEGIN-LIVE-REGISTRY -->
# Projects Registry

既知のプロジェクト一覧。窓口Claudeがユーザーの依頼からプロジェクトを特定するために使う。
ワーカー派遣時に自動登録される。手動で追記・編集してもよい。

このファイルは operator-local であり git 管理下に無い（Issue #811）。列スキーマを変更したい場合は、本ファイルではなく `registry/projects.example.md` を編集して commit する。

「パス」列はプロジェクトの clone ソースを記録する。値によってワーカー派遣時の初期化手順が分岐する:

- URL（例: `https://github.com/...`）→ リモートリポジトリ。`git clone {URL} {worker_dir}` で取得
- ローカルパス（例: `C:/Users/.../existing-repo`）→ ローカル既存プロジェクト。`git clone {ローカルパス} {worker_dir}` で取得
- `-` → 新規プロジェクト（clone 元なし）。`git init {worker_dir}` で初期化（clone は実行しない）

注意: この列はワーカーの成果物パスを示すものではない（Issue #267 live-migration 後のレイアウトでは、active ワーカーは `workers/<project>/_runs/<workstream>/<run>/` を作業ルートとし、リサーチ系は `_research/_runs/<workstream>/<run>/`、検証用 sandbox は `_scratch/_runs/_solo/<name>/`、cold 成果物は curator が事後に `_archive/<YYYY-Qx>/<project>/<workstream>/<run>/` へ退避する）。

claude-org-ja 自身（self-edit）は本レジストリに載せない。`tools/resolve_worker_layout.py:is_claude_org_project()` が `claude_org_root` の git origin URL を見て `suisya-systems/claude-org-ja` リポジトリかを判定する。
この下の Markdown 表はワーカー派遣前に `dashboard/server.py:_parse_projects` で機械パースされるため、本セクションに追加の Markdown 表（`|---|` セパレータ付き）を差し込まないこと。説明を増やす場合はプレーン箇条書きで記述する。

「triage」列は work-discovery の cross-repo triage（[`docs/design/work-discovery-triage.md`](../docs/design/work-discovery-triage.md)）の scan 対象かどうかを opt-out で表す:

- 「パス」列が GitHub URL の行は**既定で scan 対象**。owner/repo を導いて `--repo` セットに加える。
- `no` / `off` / `false`（case-insensitive、trim 後）だけが opt-out。空欄・`-` は既定どおり scan 対象。それ以外の未知値は scan 対象に含めたうえで signal に記録する。
- 「パス」が GitHub URL でない行（ローカルパス / `-`）は owner/repo を導けず構造的に scan 対象外（`skipped` + signal）。明示 opt-out しておくとその skip signal も出ない。
- claude-org-ja 自身（home repo）はこの表に載らない契約は不変。home を scan 対象に含めるかは `registry/org-config.md` の `triage_home`（既定 off）で決める。

「base_branch」列（Issue #808）はそのプロジェクトの**既定の起点ブランチ兼 PR マージ先**を宣言する。`main` に直接マージせず `develop` に feature を溜める二系統運用のリポジトリのための設定:

- **空欄 / `-` = 未設定**。従来どおり `origin/HEAD`（リモートが知る既定ブランチ）から worktree を切り、PR も repo の既定ブランチ宛になる。**本列の追加前から在る行は 1 文字も編集せずそのまま有効**。
- 値を書いた行は、派遣時に `origin/<base_branch>` から worktree を切り（[`tools/gen_delegate_payload.py`](../tools/gen_delegate_payload.py) の `_resolve_base_ref`）、`gh pr create --base <base_branch>` が PR フローの既定になる（[`.claude/skills/org-pull-request/SKILL.md`](../.claude/skills/org-pull-request/SKILL.md) 2a）。
- 値はブランチ名（`develop`）。git が表示する形の `origin/develop` と書いても同じものとして扱う（前後空白は trim）。
- 単発の逸脱（hotfix を `main` から切る等）は本列を書き換えず `gen_delegate_payload.py --base-ref main` で上書きする。**優先順位は `--base-ref` > 本列 > `origin/HEAD`**。
- 設定したブランチが `origin` に存在しない場合、派遣は apply 時に fail loud で停止する（既定ブランチへ黙って落ちない。Issue #480 の stale-base ガードと同じ立場）。

下の行は列の書き方を示すサンプルである。実際の運用では自分のプロジェクトに置き換えてよい（サンプル行を全て削除しても、パーサーは 0 行の表を有効として扱う）。

| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage | base_branch |
|---|---|---|---|---|---|---|
| 時計アプリ | clock-app | - | Webブラウザで動くデジタル時計。「パス」が `-` = clone 元なしの新規プロジェクト | デザイン変更、機能追加 | no | |
| renga | renga | https://github.com/suisya-systems/renga | Rust 製の Claude Code 用ターミナルマルチプレクサ（TUI）。GitHub URL 行なので triage 既定 include | 機能追加、バグ修正、Issue 対応 | | |
| 二系統サンプル | sample-two-track | https://github.com/example/sample-two-track | `develop` に feature を溜め `main` へは直接マージしない運用のサンプル行（base_branch 列の記入例） | 機能追加、リリース準備 | no | develop |
