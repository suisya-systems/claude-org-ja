# Projects Registry

既知のプロジェクト一覧。窓口Claudeがユーザーの依頼からプロジェクトを特定するために使う。
ワーカー派遣時に自動登録される。手動で追記・編集してもよい。

「パス」列はプロジェクトの clone ソースを記録する。値によってワーカー派遣時の初期化手順が分岐する:

- URL（例: `https://github.com/...`）→ リモートリポジトリ。`git clone {URL} {worker_dir}` で取得
- ローカルパス（例: `C:/Users/.../existing-repo`）→ ローカル既存プロジェクト。`git clone {ローカルパス} {worker_dir}` で取得
- `-` → 新規プロジェクト（clone 元なし）。`git init {worker_dir}` で初期化（clone は実行しない）

注意: この列はワーカーの成果物パスを示すものではない（Issue #267 live-migration 後のレイアウトでは、active ワーカーは `workers/<project>/_runs/<workstream>/<run>/` を作業ルートとし、リサーチ系は `_research/_runs/<workstream>/<run>/`、検証用 sandbox は `_scratch/_runs/_solo/<name>/`、cold 成果物は curator が事後に `_archive/<YYYY-Qx>/<project>/<workstream>/<run>/` へ退避する）。

claude-org-ja 自身（self-edit）は本レジストリに載せない。`tools/resolve_worker_layout.py:is_claude_org_project()` が `claude_org_root` の git origin URL を見て `suisya-systems/claude-org-ja` リポジトリかを判定する。
この下の Markdown 表はワーカー派遣前に `dashboard/server.py:_parse_projects` で機械パースされるため、本セクションに追加の Markdown 表（`|---|` セパレータ付き）を差し込まないこと。説明を増やす場合はプレーン箇条書きで記述する。

「triage」列は work-discovery の cross-repo triage（[`docs/design/work-discovery-triage.md`](../docs/design/work-discovery-triage.md)）の scan 対象かどうかを opt-in で表す:

- 既定は `no`（未 opt-in）。`yes` / `true` / `on`（case-insensitive）だけが opt-in として扱われ、その行の「パス」列 GitHub URL から owner/repo を導いて `--repo` セットに加える。それ以外の値・空・`-` は非 opt-in。
- opt-in が効くには「パス」が GitHub URL であることが必須。ローカルパスや `-` の行を `yes` にしても owner/repo を導出できず **skip される**（監査可能な signal に残るだけで scan 対象にはならない）。
- claude-org-ja 自身（home repo）はこの表に載らないが、resolver が git origin から常時 scan 対象へ含める（この列で opt-in する必要はない）。

| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |
|---|---|---|---|---|---|
| 時計アプリ | clock-app | - | Webブラウザで動くデジタル時計 | デザイン変更、機能追加 | no |
| renga | renga | https://github.com/suisya-systems/renga | Rust 製の Claude Code 用ターミナルマルチプレクサ（TUI） | 機能追加、バグ修正、Issue 対応 | no |
| サンドボックス検証 | sandbox-probe | - | Issue #376 / #377 用の sandbox profile / hook / settings 配備の実測検証エリア。candidate profile を handcraft して probe を回し allow/deny matrix を作る | Pre-Phase 0 spike、probe 反復、profile validation | no |
| ランタイム | claude-org-runtime | https://github.com/suisya-systems/claude-org-runtime | Layer 2 = org-runtime: claude-org-ja から抽出された Python runtime (dispatcher / state schema / reference role prompts)。ja は pin で参照する | role_configs_schema.json 同期、release 駆動、dispatcher / settings.generator のメンテ | no |
| token-tracking | token-tracking | https://github.com/aainc/token-tracking | Claude Code OTel ローカル監視スタック (otel-collector + Prometheus + Grafana)。個人 (Pro/Max) ローカル構成 + Team plan スケール拡張ガイド付き | dashboard 改修、collector / Grafana 設定更新、export スクリプト機能追加、Team 化作業 | no |
