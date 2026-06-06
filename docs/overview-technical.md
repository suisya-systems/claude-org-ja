# claude-org — 技術概要

Claude Codeの複数インスタンスを協調させる、自己成長型AI組織基盤。
人間は窓口（Secretary）と対話するだけで、裏側でワーカーが自動派遣・管理される。

---

## アーキテクチャ

### インスタンス構成

```
┌──────────────┬──────────┬──────────┐
│              │ Worker1  │ Worker4  │
│  Secretary   │ Worker2  │ Worker5  │
│  (large)     │ Worker3  │ Worker6  │
├───────┬──────┤          │          │
│Dispatcher│Curat.│  ...     │  ...     │
└───────┴──────┴──────────┴──────────┘
```

| インスタンス | 常駐 | 役割 | 許可ツール |
|---|---|---|---|
| **Secretary** | Yes | ユーザー対話、タスク分解、状態管理 | 全ツール（ただし実作業は委譲） |
| **Dispatcher** | Yes | ペイン起動・指示送信・状態記録の代行 | Bash, Read, Write, Edit, Glob, Grep, Skill, renga-peers |
| **Curator** | No（オンデマンド） | worker クローズ時の閾値超過で一時起動し `/org-curate` を 1 回実行 | Read, Write, Edit, Glob, Grep, Skill, renga-peers |
| **Worker** | No | 実作業（コード編集、調査、テスト等） | Bash, Read, Write, Edit, Glob, Grep, Agent, Skill, renga-peers |

### 通信

- **インスタンス間**: `renga-peers` MCP（同タブ内 Claude 間の双方向メッセージング、プッシュ型チャネル通知）。`send_message` / `list_peers` / `check_messages` / `set_summary` を用い、peer ID にはペイン名（`secretary` / `dispatcher` / `curator` / `worker-{task_id}`）を使う
- **ペイン管理**: `renga-peers` MCP（`spawn_pane` / `spawn_claude_pane` / `close_pane` / `list_panes` / `new_tab` / `focus_pane` / `inspect_pane` / `send_keys` / `poll_events` / `set_pane_identity` 等、renga 0.18.0+ で 14 ツール）。役割/ワーカー起動は `spawn_claude_pane` の構造化フィールド（`cwd` / `permission_mode` / `model` / `args[]`）に統一（Issue #58）。`cd X && claude ...` 合成パターンは撤去済み
- **指示の二重化**: CLAUDE.md（永続・ベースライン）+ `renga-peers` メッセージ（リアルタイム・補足）

### 状態管理

組織状態は **`.state/state.db`（SQLite）を唯一の source of truth** とする（M4 cutover、Issue [#267](https://github.com/suisya-systems/claude-org-ja/issues/267) / [#284](https://github.com/suisya-systems/claude-org-ja/issues/284)）。語彙・遷移ルール・派生物の正準定義は [`docs/contracts/state-semantics-contract.md`](contracts/state-semantics-contract.md) を一次参照。

| 層 | パス | 用途 | 書き手 / 再生成 |
|---|---|---|---|
| **state.db**（authoritative）| `.state/state.db` | `runs` / `org_sessions` / `worker_dirs` / `events` の正本 | `tools/state_db.writer.StateWriter`（`upsert_run` / `update_run_status` 等）と `tools/journal_append.sh` / `tools/journal_append.py`（M4 以降は DB ルーティング） |
| **`.state/org-state.md`**（derived）| repo-relative | 人間が直接読むためのスナップショット（`/org-resume` の参照、retro 等）。**ダッシュボードは参照しない**（dashboard は state.db を直接読む） | `StateWriter.transaction()` の post-commit hook が `tools/state_db.snapshotter` で自動再生成。手動編集は `tools/state_db.drift_check` が drift 検出 |
| **`.state/org-state.prev.md`**（derived）| repo-relative | `/org-suspend` 直前の `org-state.md` のバックアップ | `/org-suspend` Phase 3 が `org-state.md` を copy |
| **`.state/org-state.json`**（derived）| repo-relative | プログラマブル消費者向けの JSON 投影 | `dashboard/org_state_converter.py`（M4 で `--source markdown` モードは廃止、state.db 直接読み） |
| **`.state/workers/worker-{task_id}.md`**（authoritative for pane-liveness + Progress Log）| per-worker | ワーカーペインの `Status:` ミラー + Progress Log | dispatcher の delegate-plan helper が T2 で生成、secretary が peer message ごとに append。T5 完了時に `.state/workers/archive/` へ post-commit hook で自動アーカイブ |

**legacy（参考）**: `.state/journal.jsonl` は M4 で廃止された旧ジャーナル層。現在のイベントは state.db `events` テーブルに格納される。歴史的な jsonl ファイルが残っているリポジトリも、新規書き込みは行われない。`docs/org-state-schema.md` および `docs/contracts/state-schema-contract.md` § 1.1 が記述する「Markdown 正本、JSON は派生」のモデルも pre-M4 の表現で、現在は state.db が正本。詳細は [`docs/contracts/state-semantics-contract.md` § 1.3](contracts/state-semantics-contract.md) を参照。

**run の状態語彙**: `runs.status ∈ {queued, in_use, review, completed, failed, suspended, abandoned}` の閉じた 7 値（[contract § 2](contracts/state-semantics-contract.md)）。`suspended` は将来用に予約（書き込み経路は現状なし）、`/org-suspend` は run のステータスを変更しない（[contract I4](contracts/state-semantics-contract.md)）。

---

## 技術スタック

- **AI**: Claude Code (Opus 4.6, 1M context)
- **ターミナル/マルチプレクサ**: renga (ペイン分割で複数インスタンスを管理)
- **インスタンス間通信**: `renga-peers` MCP server（同タブ内の Claude Code 間メッセージング + ペイン制御を統合）
- **バージョン管理**: Git + GitHub（OSS / MIT License）
- **OS**: 開発・運用想定は Windows 11 Pro (bash shell)。macOS / Linux でも基本動作は想定（パス前提のみ各自で読み替え）

---

## ディレクトリ構造

```
claude-org/
├── CLAUDE.md                      # Secretary の行動指針（薄く保つ）
├── .claude/
│   ├── settings.local.json        # ツール許可設定
│   └── skills/                    # スキル群（プログレッシブ・ディスクロージャー）
│       ├── org-start/             # 組織起動
│       ├── org-delegate/          # ワーカー派遣（窓口→ディスパッチャー連携）
│       │   └── references/
│       │       ├── pane-layout.md           # ペイン配置ルール
│       │       ├── worker-claude-template.md # ワーカー用CLAUDE.mdテンプレート
│       │       └── instruction-template.md  # ワーカーへの指示テンプレート
│       ├── org-suspend/           # 組織中断
│       ├── org-resume/            # 組織再開
│       ├── org-retro/             # 委譲プロセス振り返り
│       ├── org-curate/            # 知見整理（キュレーター用）
│       │   └── references/
│       │       └── knowledge-standards.md   # 知見の記録・整理基準
│       └── org-dashboard/         # ダッシュボード生成
├── .dispatcher/
│   └── CLAUDE.md                  # Dispatcher 用の役割指示
├── .curator/
│   └── CLAUDE.md                  # Curator 用の役割指示
├── .state/                        # セッション状態（.gitignore）
│   ├── state.db                   # SQLite。runs/org_sessions/worker_dirs/events の正本（M4 以降）
│   ├── org-state.md               # state.db から自動再生成される派生スナップショット（人間向け）
│   ├── org-state.prev.md          # /org-suspend 直前のバックアップ
│   ├── org-state.json             # state.db から生成される JSON 投影（外部 consumer 向け）
│   └── workers/
│       ├── worker-{task_id}.md    # 各ワーカーの pane-liveness + Progress Log
│       └── archive/               # T5 完了時に post-commit hook が自動移動
├── dashboard/                     # HTMLダッシュボード
│   ├── index.html                 # テンプレート（git管理）
│   ├── style.css                  # スタイル（git管理）
│   ├── app.js                     # レンダリング（git管理）
│   └── server.py                  # ライブサーバー（/api/state / SSE）
├── knowledge/
│   ├── raw/                       # 生の学び（.gitignore、一時データ）
│   └── curated/                   # 整理済み知見（git管理）
├── registry/
│   └── projects.md                # プロジェクト一覧（通称→パスの名前解決）
└── docs/
    ├── getting-started.md         # 使い方ガイド
    └── verification.md            # テスト手順
```

### Git管理方針

| パス | Git管理 | 理由 |
|---|---|---|
| `.state/*` | 除外 | ペインID等のマシン固有情報を含む |
| `knowledge/raw/*` | 除外 | 整理前の一時データ。curated に統合されれば不要 |
| `.claude/settings.local.json` | 除外 | マシン固有のツール許可設定 |

---

## スキルシステム

CLAUDE.md は最小限（行動指針のみ）に保ち、具体的手順はスキル（`.claude/skills/*/SKILL.md`）に委ねる。

**設計意図**: プログレッシブ・ディスクロージャー — 必要なときだけ詳細手順がロードされ、コンテキスト消費を最小化する。

### スキル一覧

| スキル | トリガー | 実行者 |
|---|---|---|
| `org-start` | 起動直後に手動実行 | Secretary |
| `org-delegate` | 実作業が発生する依頼時 | Secretary → Dispatcher |
| `org-suspend` | 「中断」「今日は終わり」等 | Secretary |
| `org-resume` | 中断状態での起動時 | Secretary |
| `org-retro` | 作業完了後 | Secretary |
| `org-curate` | worker クローズ時の閾値超過でオンデマンド実行 | Curator |
| `org-dashboard` | 「ダッシュボード見せて」等 | Secretary |

### 委譲フロー（org-delegate）

```
Secretary                          Dispatcher                         Worker
   │                                  │                              │
   ├─ プロジェクト名前解決            │                              │
   ├─ タスク分解（WI-xxx）            │                              │
   ├─ CLAUDE.md 生成                  │                              │
   ├─ DELEGATE メッセージ ──────────> │                              │
   │  (窓口はここで解放)              ├─ ペイン起動                  │
   │                                  ├─ ピア待ち                    │
   │                                  ├─ 指示送信 ──────────────────>│
   │                                  ├─ 状態記録                    │
   │  <────── DELEGATE_COMPLETE ──────┤                              │
   │                                  │                              ├─ 作業実行
   │  <──────────────── 完了報告 ─────────────────────────────────────┤
   ├─ ユーザーに報告                  │                              │
   ├─ CLOSE_PANE ────────────────────>│                              │
   │                                  ├─ ペインクローズ              │
```

**設計のポイント**: 窓口はタスク分解・CLAUDE.md生成までを行い、ペイン起動以降をディスパッチャーに委託する。これにより窓口は即座にユーザーとの対話に復帰できる。

---

## 自己成長ループ

```
Worker完了 → knowledge/raw/ に学び記録
                ↓ (5件以上蓄積)
Curator (org-curate) → knowledge/curated/ に整理・統合
                ↓ (パターン検出)
改善提案 → Secretary → ユーザー承認 → スキル/CLAUDE.md 更新
```

- ワーカーが技術的知見を `knowledge/raw/` に自動記録（CLAUDE.md の指示）
- ディスパッチャーが worker クローズ時に閾値チェック（`tools/check_curate_threshold.py`）→ 超過時のみキュレーターを一時起動して整理実行
- 同じ種類の知見が3件以上でプロセス改善を提案
- 提案はユーザー承認を経てから反映（安全弁）

---

## 主要な設計判断

| 判断 | 内容 | 根拠 |
|---|---|---|
| スキル中心設計 | CLAUDE.md は薄く、手順はスキルに委ねる | コンテキスト消費の最小化 |
| 委譲ファースト | 窓口は司令塔、実作業は全てワーカーに委譲 | 窓口のロック回避、常にユーザー対応可能 |
| ディスパッチャー導入 | ペイン起動・指示送信を代行する常駐インスタンス | 窓口のロック時間を最小化 |
| 指示の二重化 | CLAUDE.md + `renga-peers` メッセージ | 揮発的通信のみへの依存を回避 |
| state.db を SoT 化（M4） | run / session / worker_dir / event の正本を SQLite に集約。Markdown / JSON は派生 | トランザクショナルな整合性 + drift 検出可能。`/org-resume` の人間可読性は snapshotter で `org-state.md` を自動再生成して担保 |
| `.state/` を.gitignore | マシン固有情報（ペインID等）を含む | マシン間で状態を共有する必要なし |

---

## 拡張方法

### 新スキルの追加

1. `.claude/skills/{skill-name}/SKILL.md` を作成（frontmatter に name, description を記述）
2. 必要に応じて `references/` に補助ファイルを配置
3. CLAUDE.md への変更は不要（スキルのdescription でトリガーされる）

### 新プロジェクトの登録

- ユーザーが作業を依頼すると、`registry/projects.md` に自動登録される
- 手動で `registry/projects.md` を編集してもよい

### ダッシュボードのカスタマイズ

- `dashboard/index.html`, `style.css`, `app.js` を直接編集する
- `org-dashboard` スキルは `server.py` を起動し `http://localhost:8099` をブラウザで開く。データは `/api/state`（REST）と `/api/events`（SSE）で配信される
