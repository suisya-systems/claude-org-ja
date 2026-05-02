# Phase 4 (Layer 2 = org-runtime) コード棚卸し

- 作成日: 2026-05-02
- 関連 Issue: ja#129 (Layer 2 = org-runtime 抽出)
- 目的: measurement-first で抽出方針を議論するための入力 inventory。
- 親リポ参照ベース: `C:/Users/iwama/Documents/work/claude-org/`（読み込みのみ。本 doc は worktree `phase4-inventory` 配下の編集に閉じる）。
- 行数は `wc -l` 計測値（2026-05-02 時点）。

## 1. 分類凡例

| 区分 | 意味 |
|---|---|
| **public** | 既に OSS（claude-org-ja MIT または en port）として公開済 / 抽出後も追加マスキング不要 |
| **scrub-then-public** | 構造は公開可能だが、現ファイル内容に session narrative / 個人的ロードマップ / Lead voice TODO が混入。schema / template だけ抽出する形なら公開可 |
| **ja-specific** | claude-org-ja の運用前提（日本語 prose、Pattern A/B/C 命名、`renga --layout ops` 固定など）に強く依存。抽出時に英語化 + 抽象化が必要 |
| **internal-only** | 抽出対象外。組織運営に固有で OSS にしても他者再利用価値が薄い |

抽出可否の見立ては「Layer 2 = org-runtime」を **renga + Claude Code 上で複数エージェントを協調させる runtime 部品** と仮定したうえでの粗評価。最終的な scope は Q&A 草案 (`phase4-questions-2026-05-02.md`) で詰める。

## 2. インベントリ（候補コード一覧）

### 2.1 ロール prompt（Markdown CLAUDE.md）

| パス | 行数 | 主な責務 | 依存 | 抽出可否 |
|---|---|---|---|---|
| `.dispatcher/CLAUDE.md` | 305 | DELEGATE 受信ループ / `poll_events` cursor 管理 / `inspect_pane` による APPROVAL_BLOCKED 検出 / journal de-dup / CLOSE_PANE retro | renga-peers MCP 14 種、`tools/dispatcher_runner.py`、`.state/dispatcher-event-cursor.txt`、`.claude/skills/org-delegate/**`（ref） | **ja-specific**（日本語、anchored-regex リスト、journal フォーマット）。anomaly 検出ロジック / cursor 管理 / de-dup 設計は普遍的なので抽出時に英語 prose + テンプレ化推奨 |
| `.curator/CLAUDE.md` | 41 | `knowledge/raw → curated` 整理ループ（`/loop 30m /org-curate`） | parent repo の `knowledge/` ディレクトリ規約、Glob | **scrub-then-public**。役割分担 + 親リポ相対パス問題は他プロジェクトでも生じるので extract 価値あり |

### 2.2 状態ファイル（書き手 = Claude / 読み手 = ダッシュボード・他 Claude）

| パス | 行数 / 件数 | スキーマ | 書き手 | 読み手 | 抽出可否 |
|---|---|---|---|---|---|
| `.state/journal.jsonl` | 189 行 (実環境スナップショット) | JSONL。`{"ts","event","worker?","task?","reason?","kind?","confidence?","source?","matched?","cursor?"}` | secretary / dispatcher（worker_spawned / worker_respawned / suspend / resume / anomaly_observed / notify_sent / events_dropped / secretary_identity_restored） | dashboard/server.py（`_parse_journal`）、人間 | **public** schema、**scrub-then-public** 実データ。`docs/journal-events.md` に既に半 schema 化されている。Layer 2 の中心 contract 候補 |
| `.state/org-state.md` | 507 (実セッション 5 のもの) | 自由 Markdown + 規約セクション (`Status:`, `Updated:`, `## Worker Directory Registry`, `## Dispatcher`, `## Curator`, `## Resume Instructions`) | secretary（人間との合意で更新）、`org-state_converter.py` は read-only | secretary（次セッション resume）、`org_state_converter.py` | **scrub-then-public**。schema は `docs/org-state-schema.md` に既存。session narrative / Lead voice TODO の混入が多く、抽出時はテンプレ化が必要 |

#### 2.2.1 Worker Directory Registry スキーマ（重要 contract）

Layer 2 抽出時に固める必要のある中核 contract のひとつ。Markdown と JSON の **2 表現を持つ** ため両方の field を明示しておく。

**JSON 表現**（`org-state.json` `workerDirectoryRegistry[]`、`schemaVersion=1`、source: `dashboard/org_state_converter.py`）:

| field | 型 | 例 | 備考 |
|---|---|---|---|
| `taskId` | string | `"role-config-drift-fix"` | 一意、`[A-Za-z0-9_-]+` 規約（`dispatcher_runner.py` の `_NAME_PATTERN` と同じ） |
| `pattern` | string | `"A"` / `"B"` / `"C"` | A=ホスト直編集、B=worktree、C=clone |
| `directory` | string (絶対パス) | `"C:/Users/iwama/.../workers/<task>/"` | OS 依存パスがそのまま入る |
| `project` | string | `"claude-org"` / `"ccmux"` 等 | `registry/projects.md` の通称 |
| `status` | string (自由文) | `"completed"` / `"merged (PR #93)"` / `"review (PR #94)"` | enum 化されておらず PR 番号付き自由文を許容 |

**Markdown 表現**（`org-state.md` `## Worker Directory Registry` セクション）:
- パイプ区切りテーブル。converter `parse_org_state_md` が行 → JSON に展開する。列順は JSON field と 1:1。
- 書き手: secretary（手動更新）/ 一部 dispatcher（worker spawn 時に追記）。
- 読み手: dashboard front-end、`/org-resume` の Phase 1 ブリーフィング、人間。

**抽出上の論点**:
- `status` を enum 化するか、PR 番号入り自由文を許容したまま JSON にするか（Q7 と連動）。
- `directory` の OS-absolute パスは publish 時に必ず scrub が必要（`scrub-then-public` 区分）。
- `pattern` を `A/B/C` のままにするか、`host/worktree/clone` 等の意味的名前に正規化するか。
- Markdown を「人間 SoT」、JSON を「派生」のままにするか、**JSON を SoT** に反転させるか（converter の方向反転）は Layer 2 設計の中心論点。
| `.state/org-state.json` | 〜 same | 派生 JSON (`schemaVersion=1`) | `dashboard/org_state_converter.py` の atomic write | dashboard front-end | **public**。converter とセットで extract 候補 |
| `.state/workers/worker-{peer_id_or_task_id}.md` | 64 ファイル × 〜10〜30 行 | `Task: / Directory: / Pane Name: / Status: / Pane ID:` ヘッダ + `## Assignment` + `## Progress Log` | dispatcher (`dispatcher_runner.py` seed) + worker self-update | dashboard/server.py、retro、resume | **scrub-then-public**。task 記述・パスに個人 path 含む。template 化して extract |
| `.state/dispatcher/outbox/*-instruction.md` | 可変 | 自動生成（`dispatcher_runner.py delegate-plan` の出力） | dispatcher_runner.py | dispatcher Claude（`send_message` の本文として読む） | **public**（ロジック）/ **scrub** 個別ファイル |
| `.state/dispatcher/inbox/*.json` | 可変 | secretary が DELEGATE 時に書く task JSON (`task_id, worker_dir, permission_mode, task_description, instruction, instruction_vars?, model?`) | secretary（org-delegate skill） | dispatcher_runner.py | **public** schema |
| `.state/dispatcher-event-cursor.txt` | 1 行 | renga `poll_events` の `next_since` cursor | dispatcher | dispatcher | **public**。普遍的なシンプル contract |
| `.state/dispatcher/panes-snapshot.json` | 可変 | `mcp__renga-peers__list_panes.structuredContent.panes` の cache | dispatcher | dispatcher_runner.py | **public** |
| `.state/dashboard.{pid,log,err}` | 1〜数行 | dashboard プロセス管理 | dashboard/server.py | server.py（自分自身） | **internal-only**（OS specific 扱い、抽出対象外でよい） |

### 2.3 Python ツール（`tools/`）

| パス | 行数 | 役割 | 入出力 | 抽出可否 |
|---|---|---|---|---|
| `tools/dispatcher_runner.py` | 653 | `delegate-plan` サブコマンド。balanced split target/direction、worker name / pane name 検証、instruction template 展開、worker state seed、dispatcher action plan JSON 生成 | in: task JSON + panes JSON / out: action plan JSON、`worker-{task_id}.md`、`outbox/{task_id}-instruction.md` | **public**（既に「OSS 抽出を見越した design」コメントあり）。Layer 2 中核 |
| `tools/test_dispatcher_runner.py` | 669 | 上記の unittest | — | **public** |
| `tools/check_role_configs.py` | 718 | secretary / dispatcher / curator / worker の `settings.json` 期待値を validate（drift 検出） | settings.json 群 | **public**。role-config schema 抽象化が必要 |
| `tools/role_configs_schema.json` | （JSON） | 上記 validator の expected schema | — | **public** |
| `tools/check_renga_compat.py` | 414 | renga バージョン要件 / MCP ツール一覧の compat check | renga CLI | **public**（renga 連携層として extract 価値あり） |
| `tools/test_check_renga_compat.py` | 156 | unittest | — | **public** |
| `tools/generate_worker_settings.py` | 140 | worker `settings.local.json` を schema-driven 生成（Issue #99） | input: role / project parameters | **public** |
| `tools/org_setup_prune.py` | 457 | `/org-setup` の deterministic part（古い hooks / settings 削除） | settings ファイル群 | **public**（Layer 2 候補だが Layer 0/1 に近い性質） |
| `tools/test_org_setup_prune.py` | 360 | unittest | — | **public** |

### 2.4 Dashboard（HTTP / SPA）

| パス | 行数 | 役割 | 抽出可否 |
|---|---|---|---|
| `dashboard/server.py` | 455 | stdlib only HTTP server（port 8099/8100/8101）、SSE feed、`.state/` parser、journal aggregator | **public**。runtime ↔ 観測層の間に入る重要 layer。Layer 2 抽出時は `_parse_*` 関数群を runtime parser library に切り出す候補 |
| `dashboard/org_state_converter.py` | 253 | `org-state.md → org-state.json` atomic write、parser、`SCHEMA_VERSION = 1` | **public**。schema 仕様の単一実装 |
| `dashboard/{app.js, index.html, style.css}` | — | SPA (vanilla JS) | **public**。Layer 2 (runtime 抽出) の scope に入れるか議論余地あり（観測 UI は別 layer かも） |

### 2.5 Skill prompt（`.claude/skills/`）

| パス | 行数 | 役割 | 抽出可否 |
|---|---|---|---|
| `org-start/SKILL.md` | 191 | renga 疎通確認、secretary identity 自動修復、`registry/org-config.md` 読み、ディスパッチャー / キュレーターペイン spawn | **ja-specific** prose、ロジックは **public** 化可能 |
| `org-suspend/SKILL.md` | 138 | 全ワーカー sweep → state save → ペイン停止 | 同上 |
| `org-resume/SKILL.md` | 65 | suspended 状態からの復元 (Phase 1〜3) | 同上 |
| `org-delegate/SKILL.md` | 617 | secretary が DELEGATE を組み立てる本体スキル。`dispatcher_runner.py` 呼び出しガイド、Pattern A/B/C 命名規約、verification depth 指針 | 同上、Layer 2 のスコープに最も近い |
| `org-delegate/references/{instruction-template.md, pane-layout.md, renga-error-codes.md, worker-claude-template.md, claude-org-self-edit.md}` | 計 ~700 | テンプレ群 | **public** schema、prose は ja |
| `org-retro/SKILL.md` (+ work-skill-template.md) | 125 + 77 | 委譲 retro と skill 候補抽出 | **scrub-then-public** |
| `org-curate/SKILL.md` (+ knowledge-standards.md) | 116 + 59 | knowledge 整理 | **public**（kn 規約は普遍） |
| `org-dashboard/SKILL.md` | 45 | ダッシュボード起動 | **public** |
| `org-setup/SKILL.md` (+ permissions.md) | 154 + 323 | 全 role の permission / hooks 配備 | **public**、role モデル抽象化次第 |
| `skill-audit/SKILL.md` (+ audit-checklist.md) | 115 + 105 | skill 棚卸し | **scrub-then-public**（claude-org-ja 流のキュー設計に依存） |
| `skill-eligibility-check/SKILL.md` (+ signals.md) | 130 + 80 | skill 化判定 | 同上 |

### 2.6 Pane lifecycle（renga-peers MCP 周りの skill 内ロジック）

renga-peers の **MCP プロトコル定義は `renga` 側に既に OSS 化済み**（v1.0.0 freeze）。本リポに残るのは「**runtime としての使い方**」レイヤで、以下に分散している:

- `.dispatcher/CLAUDE.md` — `poll_events`(`since`/`timeout_ms`/`types`)、`inspect_pane`(grid/lines/cursor)、`check_messages`、`list_panes`、`close_pane`、`set_pane_identity` の **ループ + de-dup + cursor 永続化** 設計（前述）
- `org-start/SKILL.md` — secretary identity 自動修復 (`set_pane_identity`)、`spawn_claude_pane` で role=dispatcher/curator 起動
- `org-delegate/references/pane-layout.md` — balanced split rules、min サイズ、セクレタリ最小幅
- `org-delegate/references/renga-error-codes.md` — `[<code>] <msg>` ハンドリングテーブル

**抽出単位の選択肢**:
- 選択肢 1: Python library 化（`renga_orchestration` package）—ループ・cursor・de-dup 状態を Python 側に寄せる（codex 路線）
- 選択肢 2: Markdown spec 集 → 言語非依存の "behavioral contract" として publish（現状の延長）
- 選択肢 3: ハイブリッド（state machine の deterministic 部分は Python、prompt は Markdown、cursor は file）

これは Q&A 5/6/7 で議論したい。

### 2.7 State machine（IN_PROGRESS / REVIEW / COMPLETED 等）

実装は **string convention** に近く、固定された型システムは未実装。実観測値:

| ドメイン | 値（現リポで観測） | 書く場所 |
|---|---|---|
| Org status | `ACTIVE`, `SUSPENDED`, `IDLE` | `org-state.md` `Status:` |
| Worker status (state file) | `planned`, `active`, `pane_closed`, `completed` | `.state/workers/worker-*.md` `Status:` |
| Worker registry status (registry) | `completed`, `merged (PR #93)`, `review (PR #94)` 等の自由文 | `org-state.md` Worker Directory Registry / `org-state.json` `workerDirectoryRegistry[].status` |
| Work item status (dashboard) | `IN_PROGRESS`, `REVIEW`, `COMPLETED` を想定（`server.py` は upper-case 化、自由値受け入れ） | `org-state.md` の `- task: title [STATUS]` |
| Journal events | `worker_spawned`, `worker_respawned`, `worker_closed`, `suspend`, `resume`, `anomaly_observed`, `notify_sent`, `events_dropped`, `secretary_identity_restored` | `journal.jsonl` |
| Anomaly kinds | `approval_blocked`, `error` | journal `kind` |
| Anomaly source | `inspect`, `self_report` | journal `source` |
| Confidence | `high`, `low`, `n/a` | journal `confidence` |
| Worker pattern | `A` (host), `B` (worktree), `C` (clone) | `org-state.json` `workerDirectoryRegistry[].pattern` |
| Verification depth | `full`, `minimal` | task instruction（dispatcher_runner で validate） |

抽出時は **これらの enum を contract として正規化する**（`docs/org-state-schema.md` を拡張）か、**state machine 図として library 化する**かが論点。現状 Markdown / JSON の両方に分散している。

### 2.8 install / hook（参考）

| パス | 行数 | 抽出可否 |
|---|---|---|
| `scripts/install.sh` / `install.ps1` | 208 / 200 | **public**（既に公開）、Layer 2 に含める必要は低い |
| `scripts/install-hooks.sh` | 65 | **public** |
| `.hooks/` / `.githooks/` | 列挙のみ | **public**、Layer 0/1 寄り |

## 3. 抽出方針サマリ（暫定）

`docs/design/core-harness-extraction.md`（Layer 1 の前例）からの推定で、Layer 2 = org-runtime の **MVP scope** はおおよそ次のいずれかに収束しそう:

- **Option α (narrow)**: `dispatcher_runner.py` + `dashboard/org_state_converter.py` + `.state/journal.jsonl` schema + worker state file template、の **deterministic 4 点セット** だけを Python package + JSON schema として publish。ロール prompt と skill prompt は claude-org-ja に残す
- **Option β (wide)**: 上記に加え、ロール prompt（dispatcher / curator / secretary）と skill 群を **英語化 + テンプレ化**して、「Claude Code を使った組織型 multi-agent runtime」の reference implementation 一式として publish
- **Option γ (renga 同等)**: renga / core-harness と同じく **API surface freeze + semver + consumer ≥ 2 ゲート**を最初から運用して、claude-org-ja 自身を最初の consumer にする

選択肢の意思決定材料を Q&A 草案で問う。

## 4. measurement の入力候補

Phase 3 (Layer 1) の learnings から、measurement-first で先に取りたい数値:

- `dispatcher_runner.py` の関数別 churn（git log で 3 ヶ月の line-touch カウント）→ API stable な部分とそうでない部分の見極め
- `journal.jsonl` の event 種別分布（過去 30 日）→ schema にすべき event がどれか
- `.state/workers/worker-*.md` の field 出現率 → template に固めるべきフィールド
- `inspect_pane` regex の hit 率 / false positive 率 → APPROVAL_BLOCKED 検出ロジックを extract する価値の根拠
- ja↔en 同期の drift 量（`/sync-policy` や `notify-en-changes` で蓄積）→ "中央 OSS layer" を作ったときに 2 言語維持するコスト
