# claude-org のアプリ化 — 実現形態の比較と段階ロードマップ

> ステータス: **調査・構想設計のみ / 実装なし**。本ドキュメントは「この組織をアプリにできないか」という問いに対する判断材料である。本リポジトリにこの構想の実装は一切存在せず、規範文書（[`CLAUDE.md`](../../CLAUDE.md) / `.claude/skills/**/SKILL.md` / `docs/contracts/**`）・運用 runbook・生成器コードはいずれも本ドキュメントによって**変更されない**。参照は本ドキュメント → 既存文書の**一方向のみ**（既存文書側から本ドキュメントへの参照追加は行わない）。
>
> **対象読者**: アプリ化の可否・方向性・投資規模を判断する人間（オーナー）。およびフェーズ実装を担当することになるワーカー。
>
> **一次入力**:
> - 本リポジトリの実コード・実ドキュメント（本文中に `file:line` で併記）
> - pip インストール済みの `claude-org-runtime` 0.1.38 / `core-harness` 0.3.2 のパッケージ実体（repo 外。パス表記は `claude_org_runtime/...` の形で示す）
> - Claude 公式ドキュメント（`code.claude.com/docs` / `platform.claude.com/docs` / `support.claude.com`。本文中に URL を併記）
>
> **依存ドキュメント**:
> - [`docs/non-goals.md`](../non-goals.md)（§3 大規模並列 / §5 Claude 専用 / §6 PTY 層 / §12 MCP HTTP 外部公開）
> - [`docs/design/renga-decoupling.md`](./renga-decoupling.md)（§1 確定制約 #1 = 課金制約。[§3.4](#34-認証と課金--アプリ化の最大の制約) で最新状況を再確認する）
> - [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md)（Set D。Surface 1.7 / 1.9 / 5.1 / 8）
> - [`docs/contracts/state-semantics-contract.md`](../contracts/state-semantics-contract.md)（Set F。state.db を正本とする層構造）
> - [`docs/design/core-harness-extraction.md`](./core-harness-extraction.md)（Layer 1 抽出の設計）
> - [`docs/design/org-docker-distribution.md`](./org-docker-distribution.md)（Docker 配布の既存設計）
> - [`docs/design/attention-notification.md`](./attention-notification.md)（人間ゲートの通知層）
> - [`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md)（権限モデルの外部依存）
> - [`docs/contracts/state-fixture-scrub-policy.md`](../contracts/state-fixture-scrub-policy.md)（公開資産と operator 固有情報の境界）

---

## 1. 目的とスコープ

### 1.1 何を決めるための文書か

現在の claude-org-ja は、**Claude Code の対話 TUI セッションを複数並べ、その間の連絡とペイン操作を MCP 経由で束ねる運用規律フレームワーク**である。利用者は前提ツールを自分で導入し、venv を activate し、`claude-org-runtime org up` をターミナルで叩き、`/org-setup` → `/org-start` をスラッシュコマンドで打つ（[`docs/getting-started.md`](../getting-started.md):9-22, :34-90）。

この形態は「ハーネスを自分で組める人」には十分だが、**組織運用そのものをプロダクトとして他人に渡す**には摩擦が大きい。本ドキュメントは次の 3 点を判断可能にする:

1. 現状の何がターミナルに**本質的に**縛られていて、何が既に切り離されているのか（[§2](#2-現状アーキテクチャの棚卸し)）
2. 「アプリ化」の実現形態として何が取り得て、それぞれの工数・リスク・移行経路はどうか（[§4](#4-実現形態の比較)）
3. どれを推し、どういう順番でやるのか（[§6](#6-推奨と段階ロードマップ)）

### 1.2 スコープと非スコープ

**スコープ**: 現状アーキテクチャの棚卸し、実現形態 3 案の比較、人間ゲートの UI 化設計、可視性の保全設計、推奨案と段階ロードマップ、配布時の考慮。

**非スコープ**: 実装、UI のビジュアルデザイン、価格設計、法務・ライセンス条項の確定、マーケティング。

### 1.3 本設計が前提とする確定制約

以下は既存文書で確定済みであり、本ドキュメントはこれらを前提として動く。ただし **1 番は外部事実の変化により再確認が必要**で、[§3.4](#34-認証と課金--アプリ化の最大の制約) で扱う。

1. **課金制約 — 全エージェントは対話型セッションのまま**。[`docs/design/renga-decoupling.md`](./renga-decoupling.md):19 が「2026-06-15 から `claude -p` / Agent SDK の使用は対話利用と分離された『Agent SDK 月間クレジット』に計上され、超過分は API 従量課金になる。本組織のワーカー使用量では確実に超過するため、全エージェントは対話型 TUI セッションのままとする」と確定させている。**この前提は本ドキュメントの調査時点で外部事実が変化しており、[§3.4](#34-認証と課金--アプリ化の最大の制約) で最新状況を示す。**
2. **PTY / 端末多重化層を本リポジトリに持たない**。[`docs/non-goals.md`](../non-goals.md):87-93 が Layer 3（`renga` / `org-broker`）に責務を譲ると明示している。アプリ化しても「ja が PTY を実装する」方向には行かない（実装は Layer 2/3 に置く）。
3. **MCP の HTTP 外部公開はしない**。[`docs/non-goals.md`](../non-goals.md):147-155。ただし 2026-06-14 に **host-local 例外**が批准済みで、`127.0.0.1` バインドの localhost-only HTTP MCP（= `org-broker`）は禁止対象外（同 :155）。**アプリのバックエンドが localhost で HTTP を話すことは既存の非ゴールに抵触しない。**
4. **大規模並列はしない**。[`docs/non-goals.md`](../non-goals.md):57-63 が「3〜5 ワーカー / 品質重視」を立ち位置としている。UI 設計もこの規模を前提にしてよい。
5. **Claude 専用**。[`docs/non-goals.md`](../non-goals.md):77-83。マルチプロバイダ対応は検討しない。

---

## 2. 現状アーキテクチャの棚卸し

### 2.1 全体像

現行は 4 層スタック + 状態層 + 可視化層で構成される。

| 層 | 実体 | 配布形態 | ターミナル依存 |
|---|---|---|---|
| Layer 4 | ja のプロンプト層（[`CLAUDE.md`](../../CLAUDE.md) / `.claude/skills/` 23 件 / `.dispatcher/`） | 本リポジトリ | 間接（記述内容が pane 概念前提） |
| Layer 3 | 輸送層 = `renga`（npm）または `org-broker`（runtime 内蔵）+ 端末 backend（tmux / herdr / WezTerm） | npm / PyPI | **本質的** |
| Layer 2 | `claude-org-runtime` 0.1.38（broker daemon・terminal adapter・settings generator・attention・CLI） | PyPI | 一部本質的 |
| Layer 1 | `core-harness` 0.3.2（permission schema / validator / generator / hooks / audit） | PyPI | Claude Code CLI 規約依存 |
| 状態層 | `.state/state.db`（SQLite）+ `.state/` の markdown / JSON | ローカル生成 | なし |
| 可視化層 | `dashboard/`（stdlib HTTP サーバ + vanilla JS） | 本リポジトリ | なし |

### 2.2 Layer 4 — ja のプロンプト層

**責務**: 窓口（Secretary）は人間との唯一の接点として、対話・判断、タスク分解、ワーカー報告の受信と伝達、`.state/` と `registry/` の管理、`/org-retro` を担う。実作業は原則すべてワーカーへ委譲する（[`CLAUDE.md`](../../CLAUDE.md):3, :52, :68）。運用責務は `org-delegate`（作業委託）/ `org-escalation`（人間エスカレーション）/ `org-pull-request`（push・PR・CI 監視・マージ後クローズ）の 3 スキルに分割されている（同 :53-56）。

**タスクルーティングは 2 レーン制**。工数 S 以下・単一ファイル級・判断仰ぎなし・日またぎなしを全て満たす極小タスクのみ、窓口が Agent tool（`isolation="worktree"`、`run_in_background=true` 必須）で直処理してよい（[`CLAUDE.md`](../../CLAUDE.md):71-88）。この `run_in_background=true` はハーネス強制で、PreToolUse フック [`.hooks/block-foreground-subagent.sh`](../../.hooks/block-foreground-subagent.sh) が違反を exit 2 で deny する。

**スキルの一部は生成物**。23 スキルのうち 12 件は `SKILL.md.in` から [`tools/gen_skill_prose.py`](../../tools/gen_skill_prose.py) が transport トークンを render して生成する。ja 側のシームは [`tools/transport.py`](../../tools/transport.py)、SoT は `claude_org_runtime.transport`。

**権限モデルはロール別ディレクトリで実現されている**。Claude Code は起動ディレクトリの `.claude/` からのみ設定を読むため、窓口 (`.claude/`)・ディスパッチャー (`.dispatcher/.claude/`)・キュレーター (`.curator/.claude/`)・ワーカー（`org-delegate` が動的生成）がそれぞれ別ディレクトリで Claude Code を起動し、ロールごとに独立した `settings.local.json` を持つ（[`.claude/skills/org-setup/SKILL.md`](../../.claude/skills/org-setup/SKILL.md):28-41）。リポジトリ共通の [`.claude/settings.json`](../../.claude/settings.json) は allow 26 件 / deny 24 件 + `PreToolUse` フック群を持つ。

**アプリ化の観点**: プロンプト層そのものは自然言語テキストであり移植可能だが、記述内容が「ペイン」「pane_id」「`spawn_claude_pane`」というターミナル多重化の語彙で書かれている。**ロール = 別ディレクトリで起動する別プロセス**という権限分離の実現手段は、単一プロセスの GUI アプリでは丸ごと置換対象になる。ただし [§3.3](#33-cli-と-sdk-の機能差) の通り、Agent SDK は `settingSources` で同じファイルシステム上の設定を読むため、**「別 cwd で SDK セッションを起こす」形なら現在の分離モデルはそのまま生きる**。

### 2.3 Layer 2 — claude-org-runtime

pip インストール済み実体は `claude_org_runtime` 0.1.38。CLI は `dispatcher` / `settings` / `attention` / `broker` / `org` / `migrate` の 6 グループを束ねる（`claude_org_runtime/cli.py:43-139`）。

主要コンポーネント:

- **broker daemon**（`claude_org_runtime/broker/server.py:53-90`）— `127.0.0.1:48720` にバインドする localhost HTTP MCP サーバー（`broker/cli.py:31-33`）。per-agent bind token による認証と、`auth_role` tier によるツール可視性の構造的絞り込みを行う。
- **配送キュー**（`broker/store.py`）— `UNDELIVERED → CLAIMED → DELIVERED` の三状態。push 一次 + pull フォールバック。
- **MCP tool カタログ**（`broker/surface.py:148-421`）— 全 13 面。messaging 4 面は全ロール共通、pane 操作 8 面は dispatcher / secretary 限定。
- **terminal adapter**（`terminal/base.py:134-273` の `TerminalAdapter` Protocol + `tmux.py` / `herdr.py` / `wezterm.py`）— `spawn` / `get_text` / `type_text` / `send_named_keys` / `kill_pane` 等の PTY プリミティブ。broker はこの Protocol 経由でのみ backend に触る（一方向依存）。
- **launcher**（`broker/launcher.py`）— `org up` は broker daemon の健全性を到達性で判定し、secretary tier の root token を mint して mcp-config を 0600 で書き、**対話型 `claude` TUI を `os.execvpe` でプロセス置換起動する**（同 :342-344）。
- **settings generator**（`settings/generator.py` + `settings/role_configs_schema.json`）— ロール別 `.claude/settings.local.json` をスキーマからレンダリングする。
- **transport descriptor**（`transport/descriptor.py:54-61`）— `DEFAULT_TRANSPORT = "broker"`、`TRANSPORTS = ("renga", "broker")`。
- **delegate-plan**（`dispatcher/runner.py`）— **MCP 呼び出しをせず JSON action plan を計算するだけ**のヘルパー。実行は対話 Dispatcher Claude が担う。

**アプリ化の観点**: この層の**メッセージング系と計算系は既にターミナル非依存**である。broker は localhost HTTP MCP なので GUI バックエンドから直接叩ける。一方 **pane 操作 8 面と terminal adapter は PTY そのもの**で、実行系を替えれば丸ごと別機構になる。

### 2.4 Layer 1 — core-harness

`core_harness` 0.3.2 は claude-org のドクトリンを一切知らない片方向依存の safety primitive パッケージで、`schema`（framework schema + org 拡張のマージ）/ `validator`（`settings.local.json` の audit engine）/ `generator` / `hooks` / `audit` の 5 モジュールを提供する。ja 側の [`tools/check_role_configs.py`](../../tools/check_role_configs.py) は `core_harness.validator` を呼ぶ薄い CLI shim に縮退済みで、`core_harness` が import 不能なときは `EXIT_UNVERIFIED(2)` を返して「検証不能」と「drift 検出」を区別する（同 :40-95）。

**注意すべき既存の乖離**: `core_harness.audit` は append-only JSONL journal を規約化しているが、ja 側の実際の書込先は M4（Issue #267）以降 SQLite の `state.db` 一本であり、`.state/journal.jsonl` は廃止済み（[`tools/journal_append.py`](../../tools/journal_append.py):9-19）。**監査ログを読むアプリは `core-harness` ではなく `state.db` の `events` テーブルを SoT として扱う必要がある。**

`hooks` モジュールは「exit code 0 = allow / exit 2 + stderr = deny / stdin = PreToolUse JSON」という **Claude Code CLI のフック呼び出し規約**に依存する（[`docs/design/core-harness-extraction.md`](./core-harness-extraction.md):83）。

**権限モデルの実体は外部にある**。[`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md):87-113 は「bwrap sandbox launcher は Claude Code CLI 本体の内部に存在し、claude-org-ja / runtime 側にランチャー実装は一切ない」と明記する。org 側は `sandbox.filesystem.*` ブロックを生成するだけの consumer である。**アプリ化しても sandbox の実行主体は Anthropic の Claude Code core のままであり、これは自前実装に置き換えられない。**

### 2.5 Layer 3 — 輸送層と端末 backend

`ORG_TRANSPORT`（`renga` | `broker`）で切り替わる 2 系統。差分は 3 点に集約される（[`CLAUDE.md`](../../CLAUDE.md) 輸送層節）:

1. **受信モデル** — renga は `<channel source="renga-peers">` の in-band push。broker は channel sidecar が queue を claim → `notifications/claude/channel` で idle セッションへ注入する push 一次、pull はフォールバック。
2. **spawn 儀式** — renga は dev-channel プロンプト 1 段承認。broker は folder-trust + channel sidecar の dev-channel の 2 段承認。**いずれも `send_keys(enter=true)` によるキー打鍵で機械承認する。**
3. **エラーコード** — broker は `token_invalid` / `session_invalid` / `tool_not_authorized` / `no_backend` / `nudge_failed` / `peer_not_found` / `name_taken` を追加で返しうる。

契約面の正本は [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md)（Surface 1-8）。**アプリ化の判断に直結する 2 つの REQUIRED 条項**:

- **Surface 1.7 `inspect_pane` は REQUIRED**。理由は「独立した grid-scrape 観測が、ワーカーの自己報告をクロスチェックするディスパッチャーの confidence-graded 通知モデルの基礎である。論理メッセージングだけを公開する backend は、Set A / Set B が依存する安全保証には**不十分**」（同 :97）。
- **Surface 1.9 `send_keys` は REQUIRED**。「dev-channel 承認、over-validation の `Esc` 介入、`Shift+Tab` の permission-mode トグルはすべて raw PTY 入力に依存する。`send_keys` の無い backend はハーネスの既存の承認・介入フローを駆動できない」（同 :114）。

さらに Surface 5.1 は「承認プロンプトは **Claude Code の機能であって backend の機能ではない**」と明記し、`send_keys` なしの代替経路を契約が提供しないことを述べている（同 :211, :217-218）。

**アプリ化の観点**: **この 2 条項が現行アーキテクチャをターミナルに縛っている中核**である。逆に言えば、「Claude Code CLI プロセスを PTY 越しに操る」ことをやめれば両条項は消滅する（置換ではなく消滅）。ただしその瞬間、`inspect_pane` が担っていた**独立観測による安全保証**を別機構で再構築する義務が発生する。SDK ベースなら streaming message と tool_use 結果が構造化されて届くため情報量はむしろ増えるが、「ハーネスの自己申告とは独立した観測」という性質は薄まる（同じプロセスが報告と観測の両方を担うため）。

なお、`ultracode` の武装は `send_keys` による user turn 化が唯一のトリガであり、`send_message` / `check_messages` 経由の本文では武装しないことが実走確認済みである（[`.dispatcher/references/spawn-flow.md`](../../.dispatcher/references/spawn-flow.md):137-179）。CLI ホスト固有の実装詳細への依存の一例。

### 2.6 状態層 — state.db と .state/

`.state/state.db`（SQLite）は M4 カットオーバー（Issue #267）以降、`runs` / `org_sessions` / `events` / `worker_dirs` について**正本**であり、書込みは [`tools/state_db/writer.py`](../../tools/state_db/writer.py) の `StateWriter` を通じてのみ許される（[`docs/contracts/state-semantics-contract.md`](../contracts/state-semantics-contract.md):38-42）。

テーブルは 11 個: `projects` / `workstreams` / `worker_dirs` / `runs` / `events` / `event_deliveries` / `tags` / `tag_assignments` / `unparsed_legacy` / `org_sessions` / `schema_migrations`（[`tools/state_db/schema.sql`](../../tools/state_db/schema.sql):5-195）。

**アプリ化にとって重要な 2 つの穴**:

1. **`pending_decisions` テーブルが存在しない**。判断仰ぎ register は `.state/pending_decisions.json` という別 JSON ファイルで完全に別管理されている（[`tools/pending_decisions.py`](../../tools/pending_decisions.py):44）。**人間ゲートの中核データが正本 DB の外にある。**
2. **worker の Progress Log と pane-liveness Status は `.state/workers/worker-{task_id}.md` が依然 authoritative**（[`docs/contracts/state-semantics-contract.md`](../contracts/state-semantics-contract.md):42 の例外行）。dashboard はこれを正規表現でパースしている（[`dashboard/server.py`](../../dashboard/server.py):82-132）。

つまり状態は「SQLite 正本 + markdown 例外 + 別 JSON」の三重管理になっている。UI 化はこの統合を避けて通れない。

### 2.7 可視化層 — dashboard

[`dashboard/server.py`](../../dashboard/server.py) は標準ライブラリのみ（`http.server` + `socketserver.ThreadingMixIn`）で実装され、ポート 8099 → 8100 → 8101 の順に bind を試みる（同 :49）。

**エンドポイントは GET 専用**。`/api/state`（JSON 全量）、`/api/events`（SSE、1.5 秒ポーリングで mtime 差分をプッシュ）、`/`、`/style.css`、`/app.js` の 5 経路のみで、**`do_POST` は存在しない**（同 :396-411）。フロントは vanilla JS で、フォーム要素・操作ボタンは HTML/JS に一切ない（[`dashboard/index.html`](../../dashboard/index.html):42-84）。表示は Workers / Work Items / Activity Log / Projects / Knowledge の 5 パネル。

起動・停止はスキル駆動の手動 spawn（`.claude/skills/org-dashboard/SKILL.md` が `python3 dashboard/server.py &` を起動し `.state/dashboard.pid` に PID を書く。停止は [`tools/stop_dashboard.py`](../../tools/stop_dashboard.py) が identity 照合付き SIGTERM）。

**アプリ化の観点**: **dashboard は完全な read-only ビューアである**。案 C（dashboard を操作 UI に昇格）は「既存の UI にボタンを足す」ではなく、**HTTP 書込みサーフェスと認可層を新設する**作業になる。`StateWriter` は Python API としてのみ存在し、HTTP surface を持たない。

### 2.8 人間ゲートと可視性

#### 2.8.1 4 つの人間ゲート

[`CLAUDE.md`](../../CLAUDE.md):110-125 が 4 ゲートを定義し、各ゲート直前に `bash tools/journal_append.sh notify_sent kind=awaiting_user gate=<GATE>` を emit する。これを attention watcher の classifier が `secretary_awaiting_user`（既定 severity `urgent`）にマップし、OS 通知 + 音で人間を呼ぶ。

| ゲート | 発火点 | 人間が返すもの |
|---|---|---|
| `worker_completed` | [`.claude/skills/org-delegate/SKILL.md`](../../.claude/skills/org-delegate/SKILL.md):323-350（ack → REVIEW 遷移 → events 追記 → 人間向け理解サマリ永続化 → emit → 承認待ちで停止） | push / PR に進んでよいかの承認 |
| `ci_green_merge_gate` | [`.claude/skills/org-pull-request/SKILL.md`](../../.claude/skills/org-pull-request/SKILL.md):64-74（CI green 受信後、merge 承認を仰ぐ直前） | merge の可否 |
| `escalation_to_user` | [`.claude/skills/org-escalation/SKILL.md`](../../.claude/skills/org-escalation/SKILL.md):59-64（Step 3、ask の瞬間） | 選択肢からの判断 |
| `escalation_reply_forward` | 同 :66-76（`mark-user-replied` → `resolve --kind to_worker` の境界） | （転送の確認） |

判断仰ぎの register は 4 段階更新（`append` / `resolve --kind to_user` / `mark-user-replied` / `resolve --kind to_worker`）で、`.state/pending_decisions.json` を SoT としてディスパッチャーの `SECRETARY_RELAY_GAP_SUSPECTED` 検出の一次参照になる。append と resolve のどちらが欠けても誤検知 / 見逃しになる。

`org-conveyor` の自走ループも不変条件 INV-1 として「merge 承認は人間ゲート。CI green に到達したら必ず停止し PR を人間へ提示して merge を仰ぐ。bare な OK を merge 承認と解釈しない」を持ち、同じ機構を再利用する（[`.claude/skills/org-conveyor/SKILL.md`](../../.claude/skills/org-conveyor/SKILL.md):65-77）。

#### 2.8.2 attention 通知

attention watcher（`claude-org-runtime attention watch`）は `state.db` と `pending_decisions.json` を監視して `AttentionEvent` に正規化し、severity（urgent / normal）に分類、OS 別 backend（macOS `osascript` / Linux `notify-send` / WSL `wsl-notify-send.exe`、フォールバックは stdout + bell）で desktop notification + 音を出す。dedup / cooldown は `.state/attention_notified.json`、pending_decisions には 4 段 TTL ladder がある（[`docs/design/attention-notification.md`](./attention-notification.md):157-226, :543-591）。

**この層は既にターミナル非依存である**。`state.db` と JSON を読むだけの独立プロセスで、Claude Code TUI にも tmux にも依存しない。

#### 2.8.3 operator が worker の作業画面を直接覗ける可視性

**これはユーザーが明示的に重視した要件である。** 現状の実体は次の 2 経路:

- **broker / tmux フレーム** — 各ペインは `claude-org-broker-{pid}-{seq}` という独立した detached tmux session（1 ペイン = 1 session）として存在し、人間が自分の端末から `/usr/bin/tmux -L claude-org-broker attach -r -t <session>` で入る。`-r` は read-only attach、`Ctrl-b d` でデタッチ（ペインは動いたまま）。[`.claude/skills/org-attach/SKILL.md`](../../.claude/skills/org-attach/SKILL.md):33-42, :161-172 がこの attach コマンド**文字列を生成するだけ**の read-only スキル。
- **renga フレーム** — 単一画面のタイリングモデルで、ペインは 1 つのライブウィンドウ内のタイル。「detached session へ attach し直す」概念が写像せず、**画面をそのまま見ればよい**（同 :37-41）。

加えて [`tools/org-dispatcher-view.sh`](../../tools/org-dispatcher-view.sh):195-236 が「dispatcher の自己修復する read-only ビュー」を提供し、dispatcher の restart / auto-compact fork でセッション名が変わっても自動再探索・再 attach する。

> **重要な例外 — 窓口（root secretary）は attach 対象外**。窓口は broker 起動時に logical pane（bookkeeping entry）として登録されるだけで **adapter 実ペインを持たず `pane_id` が `null`** であり、broker socket の detached session に出現しない（[`.claude/skills/org-attach/SKILL.md`](../../.claude/skills/org-attach/SKILL.md):101-107）。これは `org up` が対話型 `claude` TUI を **`os.execvpe` で呼び出し元プロセスに置換して**起動するためで（`claude_org_runtime/broker/launcher.py:342-344`）、窓口は「org を起動した人間の手元 terminal」そのものに住んでいる。**つまり `worker` / `dispatcher` は埋め込めても、人間が最も長く見る窓口セッションだけは既存の tmux 経路では埋め込めない。** [§6.2](#62-段階ロードマップ) Phase 4 はこの穴を明示的に埋める必要がある。

> **backend による差**: 上記の「1 ペイン = 1 detached tmux session」は **broker × tmux backend に固有**である。Windows 正準の WezTerm backend と herdr backend は tmux socket を持たないため `tmux attach` の経路が存在せず、renga は単一画面タイリングで detached session の概念自体がない。可視性の実装は backend ごとに別の話になる（[§5.3](#53-可視性の保全--最重要要件)）。

**この可視性が実際に人間へ与えているもの**を分解すると:

1. **リアルタイムのターミナル出力そのまま** — スピナー、途中の思考、tool 呼び出しの引数と結果、承認プロンプト
2. **「動いている感」** — 止まっているのか進んでいるのかが一目で分かる
3. **介入可能性** — `-r` を外して attach すれば、その場で人間が打鍵できる
4. **ハーネスを介さない直接性** — 窓口の要約というフィルタを通さずに一次情報が見える

**単純なログ表示への置換ではこの 4 点のうち 1・2・4 が失われる**。[§5.3](#53-可視性の保全--最重要要件) でこの保全を扱う。

### 2.9 結合点の総括 — 何がターミナルに縛られているか

| 機構 | 実体 | 実行系を替えたときの扱い | 難度 |
|---|---|---|---|
| broker messaging 4 面（`send_message` / `check_messages` / `list_peers` / `set_summary`） | localhost HTTP MCP | そのまま HTTP で叩ける | 容易 |
| attention watcher | `state.db` / JSON を読む独立プロセス | 通知経路だけ差し替え | 容易 |
| `delegate-plan` / `work_discovery_scan` / `pr_watch` / `pending_decisions` | 副作用のない Python 計算層 | 直接 import / API 化 | 容易 |
| `state.db` read | SQLite（WAL、複数リーダー可） | そのまま | 容易 |
| `state.db` write | `StateWriter` Python API（HTTP surface なし） | HTTP 書込み層の新設 | 中 |
| `settings.local.json` / `CLAUDE.md` / skills / hooks | Claude Code CLI の設定形式 | **SDK でも `settingSources` でそのまま読める**（[§3.3](#33-cli-と-sdk-の機能差)） | 容易〜中 |
| ロール別 permission = 別ディレクトリ別プロセス | Claude Code の設定読込仕様 | SDK でも cwd 単位でセッションを起こせば維持可能 | 中 |
| `send_keys`（PTY 生キーストローク） | 承認プロンプトの機械押下、`ultracode` 武装、`Esc` 介入 | **消滅**（SDK の `canUseTool` へ） | 根本的書き換え |
| `inspect_pane`（画面 grid scrape） | 独立観測による self-report クロスチェック | **消滅**（SDK の streaming message へ）+ 安全保証の再設計 | 根本的書き換え |
| worker 画面の直接 attach | tmux detached session / renga タイル | ターミナルエミュレータ相当 UI か web attach が必要 | 困難（[§5.3](#53-可視性の保全--最重要要件)） |
| 対話 TUI での `claude` 起動 | `os.execvpe` によるプロセス置換 | SDK の `query()` によるセッション生成へ | 中 |
| bwrap sandbox | Claude Code core 内部（org 側は設定生成のみ） | **どの案でも Anthropic 依存のまま** | 置換不可 |

---

## 3. 外部前提の確認（Claude 公式一次情報）

本節の記述はすべて公式ドキュメントを実際に開いて確認したものであり、URL を併記する。

### 3.1 Claude Code の提供形態

Claude Code は「ターミナル / IDE 拡張（VS Code, JetBrains）/ デスクトップアプリ / Web（claude.ai/code）」の複数サーフェスで提供され、**すべてのサーフェスが同一の基盤エンジンに接続され、`CLAUDE.md`・settings・MCP サーバーが共通で効く**（https://code.claude.com/docs/en/overview.md 、"Use Claude Code everywhere" 節の "Each surface connects to the same underlying Claude Code engine"）。

**デスクトップアプリは Chat / Cowork / Code の 3 タブ構成で、Code タブは「Git 分離された並列セッション、ドラッグ&ドロップのペインレイアウト、統合ターミナルとファイルエディタ、サイドチャット、computer use、電話からの Dispatch、visual diff review、アプリプレビュー、PR 監視」を提供する**（https://code.claude.com/docs/en/desktop.md ）。新規セッションごとに自動で独自の git worktree が割り当てられる（https://code.claude.com/docs/en/worktrees.md ）。

**Web 版（claude.ai/code）は Anthropic 管理のクラウド VM 上でタスクを実行し、ブラウザを閉じてもセッションが継続し、モバイルアプリから監視できる**（https://code.claude.com/docs/en/claude-code-on-the-web.md ）。

### 3.2 Claude Agent SDK の実体

- **位置づけ**: 「Claude Code をライブラリとして使い、本番用 AI エージェントを構築する」SDK。Claude Code を動かしているのと同じツール・agent loop・context management を Python と TypeScript から呼べる。パッケージは `@anthropic-ai/claude-agent-sdk` / `claude-agent-sdk`（https://code.claude.com/docs/en/agent-sdk/overview.md ）。
- **CLI の別途インストールは不要**。TypeScript・Python いずれの SDK パッケージも各プラットフォーム向けの native Claude Code バイナリを**バンドルしている**（https://code.claude.com/docs/en/agent-sdk/quickstart.md ）。
- **内部実装は「`claude` CLI サブプロセスを spawn し stdio で通信」**。1 エージェントセッション = 1 サブプロセスで、そのサブプロセスがシェル・working directory・ローカルディスク上の JSONL transcript を所有する（https://code.claude.com/docs/en/agent-sdk/hosting.md 、"The Agent SDK spawns and supervises a `claude` CLI subprocess"）。
- **Managed Agents は別プロダクト**。Anthropic がエージェントとサンドボックスの両方をホストする REST API で、Agent / Environment / Session / Events（SSE）の 4 概念で構成される（https://platform.claude.com/docs/en/managed-agents/overview.md ）。

**ここから読める重要な事実**: 「Agent SDK に載せ替えれば Claude Code CLI 依存が消える」は**誤り**である。消えるのは**対話 TUI と PTY への依存**であって、`claude` バイナリ自体はサブプロセスとして残る。ただしバイナリは SDK に同梱されるため、**利用者が `claude` を別途インストールする必要はなくなる**（配布上は大きな利得）。

### 3.3 CLI と SDK の機能差

| 項目 | Claude Code CLI（対話利用） | Claude Agent SDK |
|---|---|---|
| 提供形態 | ターミナル / IDE 拡張 / デスクトップ / Web | ライブラリ（npm / pip）。CLI バイナリ同梱 |
| 内部実装 | ネイティブに `claude` プロセスとして動作 | アプリが `claude` CLI サブプロセスを spawn し stdio 通信 |
| Built-in tools | Read/Edit/Write/Bash/Glob/Grep/WebSearch/WebFetch 等 | 同一（Agent / Skill / AskUserQuestion / TaskCreate 等も含む） |
| サブエージェント | あり | あり |
| **Agent teams**（複数インスタンス協調） | あり（CLI 専用） | **SDK オプションで直接構成不可**（CLI 専用と明記） |
| MCP 接続 | あり | あり（`mcpServers`） |
| Hooks | settings.json | **settings.json 経由（CLI と共有）+ programmatic hooks の両方** |
| Agent Skills | `.claude/skills/` | **同じ場所から読む。プログラム的な登録 API は無く filesystem artifact が必須** |
| Permission | 対話的承認プロンプト + settings.json ルール | `permission_mode` + allow/deny/ask ルール + `canUseTool` コールバックの 6 段階評価 |
| Session 永続化 / resume / fork | `--continue` / `--resume` | `continue` / `resume` / `fork_session`。保存先は同じ `~/.claude/projects/<encoded-cwd>/*.jsonl` |
| `CLAUDE.md` 等メモリ | 自動読み込み | `settingSources` で制御（省略は `["user","project","local"]` と等価。**実装では明示指定を推奨**、下記注記） |
| ホスティング主体 | 利用者のマシン | 開発者が自己ホスト（Anthropic はホストしない） |

出典: https://code.claude.com/docs/en/agent-sdk/overview.md 、https://code.claude.com/docs/en/agent-sdk/claude-code-features.md 、https://code.claude.com/docs/en/agent-sdk/permissions.md 、https://code.claude.com/docs/en/agent-sdk/sessions.md 、https://code.claude.com/docs/en/agent-sdk/hosting.md 。

**本設計にとって決定的に重要な 3 点**:

1. **ja のプロンプト層・フック・permission 設定はほぼそのまま SDK に持ち込める**。`settingSources` に `project` を含めれば「プロジェクトの `.claude/settings.json` に既にある hooks は追加設定なしで SDK 側でもそのまま動く」と公式が明記している（https://code.claude.com/docs/en/agent-sdk/claude-code-features.md ）。`.claude/settings.json` の allow/deny/ask ルールも同様に読まれ、6 段階評価に組み込まれる。**これは案 B の工数見積りを大幅に押し下げる。** [§2.2](#22-layer-4--ja-のプロンプト層) の「ロール = 別ディレクトリ」も、cwd を変えて SDK セッションを起こせば維持できる。

   > **実装上の注記**: `settingSources` の省略は `["user","project","local"]` と等価だと公式が明記しているが（同ページ「Omitting `settingSources` is equivalent to ...」）、**実装では省略に頼らず明示指定すべき**である。理由は 2 つ: (i) 既定に依存すると、どの設定が効いているかがコード上で読めず、ロール別 permission の監査ができない。(ii) 公式自身が「マルチテナント分離に既定の `query()` オプションを頼るな」と警告しており（同ページ Warning）、`settingSources` が制御しない入力（managed policy / `~/.claude.json` / auto memory / claude.ai MCP connectors）が別途あるため、意図しない設定の混入を防ぐには明示が要る。
2. **`canUseTool` は承認 UI を提供しない**。公式は「アプリがこれらのリクエストをユーザーに提示する必要がある」「スニペット中の `ask_user` はあなたのアプリ自身のプロンプト UI の代役」と明記する（https://code.claude.com/docs/en/agent-sdk/user-input.md ）。**承認 UI・ツール可視化・トランスクリプトビューアはすべて自前実装になる。**
3. **Agent teams は SDK から構成できない**。claude-org がやっている「複数の独立インスタンスの協調」に相当する公式機能は CLI 専用であり、SDK で同等をやるなら**今 broker がやっていることを自前で持ち続ける**ことになる（これは悪い知らせではない — 既に持っているものが無駄にならない）。

### 3.4 認証と課金 — アプリ化の最大の制約

**(1) 課金分離は発表されたが一時停止された。** [`docs/design/renga-decoupling.md`](./renga-decoupling.md):19 が確定制約 #1 として引く「2026-06-15 から Agent SDK / `claude -p` が subscription 上限と分離される」という変更は、**発効予定日に Anthropic が pause した**。公式ヘルプ記事は現在「この変更を一時停止している。現時点では何も変わっていない。Claude Agent SDK・`claude -p`・サードパーティアプリの利用は引き続き subscription の usage limits から消費される」と明記している（https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan ）。

> **この事実の扱い**: 本ドキュメントは design only であり、[`docs/design/renga-decoupling.md`](./renga-decoupling.md) を改訂しない（参照は一方向）。ただし**「ヘッドレス化は課金上不成立」という前提は現時点では成立していない**ことは、アプリ化の判断材料として明示しておく必要がある。同時に、これは**撤回ではなく一時停止**であり、再提案の時期は公式記事に「事前告知の上で」としか書かれていない。**恒久的に安全とみなしてはならない**。案 B / 案 C のいずれも、この制約が復活したときに壊れない設計にしておくべきである（[§4.5](#45-課金制約が復活した場合の耐性)）。

**(2) サードパーティ配布では claude.ai ログインを提供できない。** これは pause の影響を受けない、より重い制約である。Agent SDK Overview と Quickstart の双方が「**事前承認がない限り、Anthropic はサードパーティ開発者が自社製品（Claude Agent SDK ベースのエージェントを含む）で claude.ai ログインやそのレート制限をユーザーに提供することを認めない。代わりに API key 認証方式を使うこと**」と明記している（https://code.claude.com/docs/en/agent-sdk/overview.md ）。

つまり **事前承認を得ない限り、SDK ベースのアプリを製品として第三者に配ると、エンドユーザーは自分の Claude subscription を使えず API 従量課金になる**。claude-org のワーカー使用量では、これは subscription 利用とは桁の違うコストになる。

**ただしこれは「承認があれば可」という条件付きの制約である**（原文が "Unless previously approved" と書いている）。承認の申請方法・審査基準は一次情報に見当たらず（[§8](#8-未確認事項と次の検証) 項目 2）、**承認が現実的に取れるなら案 B の配布経路は開く**。本ドキュメントは「承認なし」を既定シナリオとして比較するが、これは案 B を恒久的に排除する根拠ではない。

**(3) ブランド制約**。SDK ベースの製品は「Claude Code」「Claude Code Agent」を名乗れず、独自ブランドを維持する必要がある（https://code.claude.com/docs/en/agent-sdk/overview.md の Branding guidelines）。

**(4) CLI をそのまま使う形態が (2) の対象外かどうかは、確認が必要な前提である**。引用した方針文は「サードパーティ開発者が**自社製品**で claude.ai ログインやレート制限を**ユーザーに提供**すること」を禁じ、SDK ベースのエージェントを「含まれる例」として挙げている。**「CLI をラップし、利用者を自分の `claude /login` に誘導する形」がこの禁止に当たらない**というのは本ドキュメントの**解釈であって、一次情報が明示しているわけではない**。

既存の Docker 配布設計はこの形をとっており、image は認証ゼロで出荷し、人間が初回に `claude /login` / `gh auth login` を対話で通す（[`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):212-223）。この形が許容されるという前提は、**案 A / 案 C が「利用者の subscription で動く」と述べる根拠そのもの**であり、[§4.4](#44-比較表) の課金行と [§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する) の推奨理由 3 がこれに依存している。**したがって、これは配布に踏み出す前に Anthropic へ確認すべき前提条件として扱う**（[§8](#8-未確認事項と次の検証) 項目 2）。確認が取れず CLI ラップ配布も禁止だと判明した場合、「第三者への配布」自体が全案で成立しなくなり、claude-org は**自分たちで使う内製ハーネス**として位置づけ直すことになる（その場合でも案 C の UI 化は自分たちの運用改善として価値を持ち、ロードマップは変わらない）。

### 3.5 公式が既に埋めつつある領域

claude-org の中核と重なる機能が、公式側で相次いで提供されている。

- **Agent View**（`claude agents`）— 複数 Claude Code セッションを並列に dispatch・監視する単一画面。各バックグラウンドセッションは `.claude/worktrees/` 配下に自動で git worktree が割り当てられ編集衝突を防ぐ。research preview 段階（https://code.claude.com/docs/en/agent-view.md ）。
- **Agent teams**（実験的、既定 disabled、`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` で opt-in）— 1 セッションが team lead となり、複数の独立 Claude Code インスタンス（teammates）が共有タスクリストと直接メッセージング（mailbox）で協調する。tmux / iTerm2 で split-pane 表示可能（https://code.claude.com/docs/en/agent-teams.md ）。
- **デスクトップアプリの Code タブ** — 並列セッション + git 分離 + ペインレイアウト + 統合ターミナル（[§3.1](#31-claude-code-の提供形態)）。

**これは claude-org のアプリ化戦略に直接効く**。「複数エージェントを並べてペインで見る」という**器**の部分は公式が埋めに来ている。したがってアプリ化の価値は器ではなく、claude-org が固有に持っている**運用規律**——ロール別の絞り込まれた permission と多層フック、4 つの人間ゲートと escalation register、知見の curation ループ、work-discovery の triage、`org-conveyor` の不変条件——の側にある。**UI 投資はこの規律を見せる方向に寄せるべきで、ペイン管理 UI を自前で作り込む方向は公式に追い抜かれるリスクが高い。**

### 3.6 選択肢への含意

| 発見 | 案 A への影響 | 案 B への影響 | 案 C への影響 |
|---|---|---|---|
| SDK は `claude` を同梱・subprocess 起動 | — | CLI 依存は消えない。ただし利用者の別途インストールは不要に（配布上プラス） | — |
| `settingSources` で設定・skills・hooks を共有 | — | **工数を大幅に押し下げる**（プロンプト層の作り直しが不要） | — |
| `canUseTool` は UI を提供しない | — | 承認 UI・可視化・トランスクリプトは全て自前 | 同左（ただし既存 tmux 画面を残せる） |
| サードパーティは claude.ai ログイン提供不可 | 影響なし（利用者が自分で `/login`） | **製品配布の致命的制約**（API 従量課金） | 影響なし |
| 課金分離は pause 中（撤回ではない） | 影響なし | 現時点では成立しうるが再開リスク | 影響なし |
| 公式が Agent View / Agent teams / Desktop を出している | 差別化を規律側に寄せる必要 | **公式と正面衝突** | 規律を見せる UI に集中すれば共存可能 |

---

## 4. 実現形態の比較

工数はすべて **AI ワーカーによる実行を前提**とした見積りである（機械的だが量の多い作業は人間基準より軽く見る）。「M 級 × N 本」はワーカータスクの粒度と本数、括弧内は着手からマージまでの実時間目安。

### 4.1 案 A — パッケージング製品化

**方針**: アーキテクチャは一切変えない。ターミナル運用のまま、**導入から起動までの摩擦だけを潰す**。

**アーキテクチャ概略**: 現状のまま。追加するのは (1) 配布物（Docker image のマルチアーキ publish / OS 別インストーラ）、(2) 前提充足を機械判定して直す `doctor` コマンド、(3) 初回セットアップの対話ウィザード（`/org-setup` → `/org-start` を含む多段手順を 1 コマンドに畳む）、(4) アンインストール / アップグレード経路。

**主要依存**: 既存のまま（`claude-org-runtime` / `core-harness` / `claude` / `gh` / `jq` / Python 3.10+ / tmux または WezTerm）。renga は opt-in fallback として残置。

**現在地**: [`docs/design/org-docker-distribution.md`](./org-docker-distribution.md) が「設計 + PoC 骨格」ステータスで既にある。B1〜B4（build / 起動 / 初回認証導線 / フルサイクル）は 2026-07-17 実測済み。**未実施は sandbox 検証マトリクス S1〜S6、herdr headless→TUI attach 導線 H1、Raspberry Pi 5 実機起動 A1、buildx arm64 ビルド A2**（同 :239-251）。非スコープとして「認証の完全自動化」「dashboard の認証機構」「Compose 以外のオーケストレータ」「CI での image 自動 publish」の 4 点が明示されている（同 :27-31）。

**工数**: **S〜M 級 × 6〜9 本（実時間 2〜4 週間）**。既存 PoC の残検証（S1-S6 / H1 / A1 / A2）が 4〜5 本、`doctor` / ウィザードが 2〜3 本、CI publish が 1〜2 本。

**リスク**:
- **低**。既存構造に触らないため回帰リスクがほぼない。
- ただし**価値の天井が低い**。前提ツールを減らし手順を畳んでも、利用者は依然としてターミナルで tmux セッションを見る。「ターミナルを使える人」という対象読者は変わらない。
- Docker 経路には環境固有の落とし穴が残る（コンテナ内 bwrap のため `seccomp=unconfined` が必要、Raspberry Pi 5 は 16KB page size カーネルで Rust バイナリがクラッシュするため `kernel=kernel8.img` への切替が必要。同 :45-46, :180）。

**移行経路**: 完全に加算的。既存利用者は何もしなくてよい。

### 4.2 案 B — GUI アプリ（Claude Agent SDK ベースで再構築）

**方針**: ターミナル依存を排し、デスクトップアプリ（Electron / Tauri）+ Agent SDK バックエンドで組織を再構築する。

**アーキテクチャ概略**:

- **実行系**: broker + terminal adapter を廃し、アプリのバックエンドが Agent SDK の `query()` で各ロールのセッションを起こす。ロール分離は cwd を分けることで維持（`settingSources` が cwd 単位で `.claude/` を読むため、現在の `.dispatcher/.claude/` 等の構造がそのまま使える）。
- **プロンプト層**: [`CLAUDE.md`](../../CLAUDE.md) / `.claude/skills/` / `.hooks/` / `settings.json` は `settingSources: ["user","project","local"]` でそのまま読ませる。ただし**記述内容から pane 語彙を抜く書き換えは必要**（`spawn_claude_pane` / `send_keys` / `inspect_pane` を参照している箇所）。
- **エージェント間通信**: broker の messaging 4 面は localhost HTTP MCP なので**そのまま残せる**（`mcpServers` オプションで各セッションに接続）。pane 操作 8 面は廃止。
- **人間ゲート**: `canUseTool` コールバック + アプリ UI。承認は構造化されたボタン操作になる。
- **可視性**: SDK の streaming message（思考・tool_use・結果）をカード列で描画する。tmux が無いので attach はできない。
- **状態**: `state.db` はそのまま。`pending_decisions` を DB へ統合。

**主要依存**: `@anthropic-ai/claude-agent-sdk` または `claude-agent-sdk`（`claude` バイナリ同梱）、Electron / Tauri、既存の `claude-org-runtime`（broker messaging と settings generator のみ利用）。

**工数**: **M〜L 級 × 20〜30 本（実時間 4〜7 か月）**。内訳の目安:
- SDK セッション管理層 + ロール別 cwd 起動: L 級 3〜4 本
- 承認 UI / `canUseTool` 統合 / permission 表示: L 級 3〜4 本
- ストリーミング可視化 UI（思考・tool_use・diff）: L 級 4〜5 本
- 4 人間ゲートの UI + `pending_decisions` DB 統合: M 級 4〜5 本
- プロンプト層の pane 語彙除去と再検証: M 級 3〜4 本
- 独立観測に代わる安全機構の再設計（[§2.5](#25-layer-3--輸送層と端末-backend) の Surface 1.7 相当）: L 級 2〜3 本
- パッケージング / 自動更新 / クラッシュ復旧: M 級 3〜4 本

**リスク**:
- **可視性の劣化が最大のコスト**。tmux セッションが存在しなくなるため、[§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性) の 4 要素のうち「介入可能性」「直接性」が構造的に失われる（[§5.3](#53-可視性の保全--最重要要件) の表 B 相当まで劣化）。**ユーザーが明示的に重視した要件を最も損なう案である。**
- **事前承認を得ない限り、配布すると API 従量課金になる**（[§3.4](#34-認証と課金--アプリ化の最大の制約)(2)）。自分で使う分には問題ないが、承認が取れない前提では「製品として配る」目的と正面から衝突する。承認可否は [§8](#8-未確認事項と次の検証) 項目 2 の未確認事項。
- **公式のデスクトップアプリ / Agent View と正面衝突する**（[§3.5](#35-公式が既に埋めつつある領域)）。同じ器を後から追う投資になりやすい。
- **契約 Set D の再批准が必要**。Surface 1.7 / 1.9 が REQUIRED と書かれている以上、SDK 実行系は現行契約を満たさない。契約改訂は設計判断であり、単なる実装作業ではない。
- **Agent teams が SDK から構成できない**ため、協調機構は自前で持ち続ける（既存の broker が使えるので致命的ではない）。

**移行経路**: 段階移行が難しい。実行系を替えると `send_keys` / `inspect_pane` に依存する dispatcher の監視ロジック（stall 検出・`APPROVAL_BLOCKED` 検出・relay gap 検出）が一斉に無効になるため、**並走期間を作りにくい**。現実的には「既存を維持したまま別プロダクトとして作り、完成後に切り替える」形になる。

### 4.3 案 C — ハイブリッド（dashboard を操作 UI に昇格）

**方針**: 実行系（Claude Code CLI + tmux/broker）は現行のまま維持し、**人間の接点だけを UI に引き上げる**。承認・エスカレーション・merge gate を UI 化し、worker の作業画面は既存の tmux セッションをアプリ内に埋め込んで見せる。

**アーキテクチャ概略**:

```
[ デスクトップアプリ / ブラウザ UI ]
        │  HTTP + SSE/WebSocket（localhost のみ）
        ▼
[ dashboard backend（拡張） ]
   ├─ read:  state.db（既存 queries.py）
   ├─ write: StateWriter + pending_decisions（新規 HTTP surface + 認可）
   ├─ 通知:  attention classifier を再利用しアプリ内バッジ + OS 通知
   └─ 画面:  PTY ↔ WebSocket ブリッジ → tmux セッションへ read-only / read-write attach
        │
        ▼
[ 実行系（無変更） ]
   broker daemon + tmux/herdr/WezTerm + Claude Code CLI ペイン（窓口・dispatcher・worker）
```

**主要依存**: 既存すべて + フロントの端末エミュレータ（xterm.js 相当）+ PTY ブリッジ。**PTY ブリッジは [`docs/non-goals.md`](../non-goals.md):87-93（PTY 層を ja に持たない）に抵触しないよう Layer 2/3 側、すなわち `claude-org-runtime` に置く。**

**工数**: **M〜L 級 × 17〜22 本（実時間 5〜7 か月）**。これは [§6.2](#62-段階ロードマップ) の Phase 1〜5 の合計と一致する（Phase 0 は案 A の内容なので含めない）。内訳:
| Phase | 内容 | 本数 |
|---|---|---|
| 1 | `pending_decisions` の `state.db` テーブル化（2 本）+ worker Progress Log / Status の DB 化（2 本） | M 級 4 本 |
| 2 | dashboard backend への書込みサーフェス + localhost 認可 + **UI 専用 broker bind token** + 監査記録 | M 級 3〜4 本 |
| 3 | 4 人間ゲートの UI 化 + attention の UI 統合 | M 級 3〜4 本 |
| 4 | PTY ↔ WebSocket ブリッジ + **窓口ホスティング（S1）** + 端末エミュレータ埋め込み + 非 tmux backend スパイク | M〜L 級 4〜6 本 |
| 5 | セットアップウィザードの UI 化 + ライフサイクル管理 + インストーラ + 自動更新 | M 級 3〜4 本 |

**この見積りは初版（M 級 10〜14 本）から上方修正されている**。Codex レビューで判明した 2 つの必須作業——**窓口（root secretary）は logical pane なので既存の attach 経路では埋め込めず、PTY ホスティング経路の新設が要る**（[§5.3](#53-可視性の保全--最重要要件)）ことと、**UI が窓口の broker token を借用できず専用 identity が要る**（[§5.1](#51-ui-化後の-4-ゲート) 要点 4）こと——が初版の内訳から抜けていたためである。

**リスク**:
- **中**。各フェーズが独立して価値を持ち、単独で revert できる。実行系に触らないため、失敗しても既存運用は無傷。
- **localhost 書込みサーフェスの認可が新しい攻撃面になる**。同一マシン上の他プロセスから merge 承認を叩ける状態にしてはならない。[`docs/non-goals.md`](../non-goals.md):155 の host-local 例外は「外部到達不可」を根拠にしているが、**書込み系は localhost でも認証を要求すべき**（broker が既に per-agent bind token でやっているのと同じ発想）。
- 状態の三重管理（[§2.6](#26-状態層--statedb-と-state)）の解消がフェーズ 1 に必要で、ここを飛ばすと UI が不整合を表示する。
- 実行系がターミナルのままなので、**「ターミナルを一切見なくてよい」状態には到達しない**（tmux は UI 内に埋め込まれるが、裏では動いている）。これを「未達」と見るか「可視性の保全」と見るかは目的次第。

**移行経路**: 完全に段階的。フェーズごとに既存運用と並走でき、どの時点で止めても損をしない。

### 4.4 比較表

| 観点 | A パッケージング | B GUI（Agent SDK） | C ハイブリッド |
|---|---|---|---|
| アーキテクチャ変更 | なし | 実行系を全面置換 | 人間接点のみ追加 |
| ターミナル依存の解消 | しない | する（PTY は消える。`claude` バイナリは残る） | UI 内に埋め込む形で隠す |
| **worker 画面の可視性** | ◎（現状維持） | △（構造化ログ相当まで劣化） | **◎（tmux をそのまま埋め込む）** |
| 人間ゲートの構造化 | ×（自然言語のまま） | ◎ | ◎ |
| プロンプト層の再利用 | 100% | 高（`settingSources` でそのまま。pane 語彙の除去は要） | 100% |
| 既存契約（Set D）への影響 | なし | **Surface 1.7 / 1.9 の再批准が必要** | なし |
| 公式機能との衝突 | なし | **大**（Desktop / Agent View / Agent teams） | 小 |
| 配布時の課金 | 利用者の subscription | **API 従量課金**（第三者配布時。事前承認が取れれば subscription も可） | 利用者の subscription |
| 工数 | S〜M × 6〜9 本（2〜4 週） | M〜L × 20〜30 本（4〜7 か月） | M〜L × 17〜22 本（5〜7 か月） |
| 失敗時の巻き戻し | 容易 | 困難（並走しにくい） | 容易（フェーズ単位） |
| 対象読者の拡大 | 小 | 大（ただし課金モデルが変わる） | 中〜大 |

### 4.5 課金制約が復活した場合の耐性

[§3.4](#34-認証と課金--アプリ化の最大の制約)(1) の分離課金は pause 中だが再開されうる。各案の耐性:

- **案 A / 案 C**: 実行系が対話セッションのままなので**影響を受けない**。これは無視できない利点である。
- **案 B**: SDK セッションが分離課金の対象になれば、[`docs/design/renga-decoupling.md`](./renga-decoupling.md):19 が述べた「本組織のワーカー使用量では確実に超過する」状況が復活する。**案 B は外部の課金方針変更に構造的に脆弱である。**

---

## 5. 人間ゲートの UI 化と可視性の保全

### 5.1 UI 化後の 4 ゲート

現在の 4 ゲートは、いずれも「窓口 Claude が自然言語で人間に提示し、人間が自然言語で返す」形をとる。UI 化するとは、**提示と応答を構造化データにし、`pending_decisions` / `state.db` を経路にする**ことである。

| ゲート | UI 化後の提示 | 応答の構造 | 必要な新設 |
|---|---|---|---|
| `worker_completed` | ワーカーカードに「完了・承認待ち」バッジ + 人間向け理解サマリ 3 点（最重要変更点 / 要確認 hunk / 設計判断と理由）+ 差分リンク | `approve_push` / `request_changes(text)` / `hold` | `runs.status` 遷移の HTTP write |
| `ci_green_merge_gate` | PR カードに CI 結果と「merge 可」バッジ + 変更サマリ | `merge` / `hold` / `close` — **明示的な選択操作のみを承認とみなす** | merge 実行の委譲経路（`gh` 呼び出しは既存） |
| `escalation_to_user` | エスカレーションカードに worker の質問文 + 窓口が用意した選択肢 | 選択肢の選択 + 自由記述 | `pending_decisions` の read/write HTTP surface |
| `escalation_reply_forward` | 「worker へ転送済み」を**転送成功の確認後に**表示 | なし | `mark-user-replied`（UI 応答時）と `resolve --kind to_worker`（転送確認後）の**分離**呼び出し |

**設計上の要点 5 つ**:

1. **`pending_decisions` を `state.db` に取り込む**。現状は `.state/pending_decisions.json` という別ファイルで、`state.db` にテーブルがない（[§2.6](#26-状態層--statedb-と-state)）。UI が読み書きする以上、正本 DB のテーブルにして `StateWriter` 経由の書込みに統一すべきである。**これは UI 化の前提作業であり、UI そのものより先に来る。** ディスパッチャーの `SECRETARY_RELAY_GAP_SUSPECTED` 検出が同じ register を一次参照しているため（[§2.8.1](#281-4-つの人間ゲート)）、既存 CLI の読み書き互換を保ったまま移行する必要がある。
2. **`bare OK` を承認と解釈しない不変条件を UI で構造的に保証する**。現在この規律は自然言語の解釈規則（`org-conveyor` INV-1、[`.claude/skills/org-conveyor/SKILL.md`](../../.claude/skills/org-conveyor/SKILL.md):65-77）としてプロンプトに書かれているが、UI 化すれば「承認ボタンを押した」という**曖昧さのない事実**になる。これは UI 化の最大の品質的利得である。
3. **窓口の要約を経由する経路を殺さない**。UI は窓口を置き換えるのではなく、窓口が既に emit している `notify_sent kind=awaiting_user` を別の面で描画するものと位置付ける。窓口 Claude との自然言語対話は残る（それ自体が価値であり、[`CLAUDE.md`](../../CLAUDE.md):3 の「窓口は人間との唯一の接点」という設計を UI が二重化するわけではない）。
4. **UI 操作の結果を窓口セッションに戻す経路が要る。そのために UI 自身の broker identity を新設する**。承認ボタンが押されたことを窓口 Claude が知る必要があり、実行系を変えない案 C ではこれを broker の `send_message` で窓口ペインへ送る形になる。ただし **UI が窓口の root token を借用してはならない**: broker の bind は token ごとに `session_id` を 1 つしか持たず、同じ token で 2 つ目の MCP クライアントが `initialize` すると `bind.session_id` が上書きされ（`claude_org_runtime/broker/server.py:1809`）、**先にいた窓口セッションの以降の呼び出しが `404 [session_invalid] initialize first` で落ちる**（同 :1780-1789）。したがって Phase 2 で **UI 専用の agent bind token（messaging tier のみ）を発行・ローテート・失効させるライフサイクル**を用意する。副次的に、送信者が `secretary` ではなく `ui` として正しく attribution されるという利点もある。
5. **UI での応答は「ユーザーが答えた」であって「worker に届いた」ではない。register の resolve を前倒ししてはならない**。UI がユーザーの選択を受け取った時点で呼ぶのは [`tools/pending_decisions.py`](../../tools/pending_decisions.py) の `mark-user-replied` までであり、`resolve --kind to_worker` は **窓口から worker への転送が成功したことを確認した後**にのみ呼ぶ。理由は、entry を `resolved` にした瞬間にディスパッチャーの `SECRETARY_RELAY_GAP_SUSPECTED` 検出（[`.claude/skills/org-escalation/SKILL.md`](../../.claude/skills/org-escalation/SKILL.md):66-76）が対象外になるためである。UI → 窓口の通知（要点 4）または窓口 → worker の relay が落ちた場合、**resolve を前倒ししていると「ユーザーは答えたのに worker に届いていない」状態を既存の安全検出器が拾えなくなる**。UI 化は人間ゲートを速くするが、この検出器を無効化してはならない。

### 5.2 attention 通知の UI 化

attention watcher は既に `state.db` / `pending_decisions.json` を読む独立プロセスであり、severity 分類・dedup・4 段 TTL ladder を持つ（[§2.8.2](#282-attention-通知)）。UI 化では:

- **classifier / severity / dedup / TTL のロジックはそのまま再利用する**（Layer 2 に切り出し済みのため）
- 通知経路を「OS 通知 + 音」から「アプリ内バッジ + OS 通知 + 音」の多重に拡張する
- `urgent` は既存どおり即時ビープ、`normal` はバッジのみ、という severity → UI 表現のマッピングを設ける

**この層はアプリ化の中で最も安く済む部分である。**

### 5.3 可視性の保全 — 最重要要件

[§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性) で分解した 4 要素（生の出力 / 動いている感 / 介入可能性 / 直接性）を、手段ごとにどう保つか。

| 手段 | 生の出力 | 動いている感 | 介入可能性 | 直接性 | 備考 |
|---|---|---|---|---|---|
| **現状: tmux attach / renga タイル** | ◎ | ◎ | ◎ | ◎ | ターミナルが必要 |
| **A. アプリ内ターミナルエミュレータ**（xterm.js 等で既存 tmux セッションに接続） | ◎ | ◎ | ◎ | ◎ | PTY ↔ WebSocket ブリッジが要る。ブリッジは Layer 2/3 側に置く |
| **B. 構造化イベントストリームの UI 描画**（思考・tool_use・結果をカード列で表示） | ○ | ○ | △ | △ | SDK の streaming message から構築。情報量は同等以上にできるが「一次情報を直接見ている」感は落ちる |
| **C. 定期スクリーンショット / grid scrape の表示** | △ | △ | × | △ | `inspect_pane` の再利用で安く作れるが体験は劣化 |
| **D. ログ表示のみ** | × | × | × | × | **採用しない**（要件を満たさない） |

**推奨は A を第一、B を補完とする**。理由:

- ユーザーが重視しているのは「覗ける」ことであり、それは**フィルタされていない一次情報**への到達性を意味する。A だけがこれを完全に保つ。
- A は既存の tmux session をそのまま利用でき、[`.claude/skills/org-attach/SKILL.md`](../../.claude/skills/org-attach/SKILL.md) が既に確立している「1 ペイン = 1 detached session」というモデルに乗る。つまり**新しい実行系を作らずに UI だけを足せる**。既存の `-r`（read-only）/ 書込みの区別もそのまま UI のトグルにできる。
- B は A の上に重ねる価値がある（一覧性・検索性・履歴・複数 worker の横断ビュー）。B 単体では 3・4 が落ちる。
- **案 B（Agent SDK 全面再構築）を選ぶと A の土台（tmux セッション）自体が消えるため、可視性は B 相当が上限になる。これは案 B の最大のコストであり、ユーザーが明示した要件と正面から衝突する。**

**A の実装上の注意**:

- 既定は **read-only attach**（現在の `-r` 既定と同じ思想）。書込みは明示的なトグル操作を要求する。
- WebSocket は localhost バインドかつトークン認証必須（[§4.3](#43-案-c--ハイブリッドdashboard-を操作-ui-に昇格) のリスク項と同じ理由）。
- ブリッジの実装は [`docs/non-goals.md`](../non-goals.md):87-93 に従い ja ではなく `claude-org-runtime` 側に置く。runtime は既に `TerminalAdapter`（`claude_org_runtime/terminal/base.py:134-273`）で backend を抽象化しているため、**ブリッジもこの Protocol の面で定義し、backend 別実装をその下に置く**のが自然である。

**A の適用範囲 — backend ごとに別の話になる**（[§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性) の backend 差の注記）:

| backend | detached session への attach | A の実現方法 | 位置づけ |
|---|---|---|---|
| broker × tmux（POSIX 正準） | あり（`tmux attach -r -t <session>`） | 既存 session に PTY ブリッジを噛ませる。最も安い | **Phase 4 の第一対象** |
| broker × WezTerm（Windows 正準） | tmux socket なし | `TerminalAdapter` の `get_text` / `type_text` を streaming 化するか、WezTerm 側の multiplexer 機能に乗る。**未検証** | Phase 4 の第二対象。実現性は要スパイク |
| broker × herdr | tmux socket なし | 同上（herdr の adapter 面を streaming 化） | Phase 4 の第三対象 |
| renga | 概念が非適用（単一画面タイリング） | **適用外**。renga 利用者は従来どおり画面を直接見る | 対象外と明示 |

**この差を放置すると「Windows では中が見えないアプリ」になる**。Phase 4 は tmux から着手してよいが、**「A は tmux 専用」で終わらせるなら、アプリの対応 backend を broker+tmux に明示的に絞る**（= Windows は Phase 4 の対象外と宣言する）判断が要る。どちらを取るかは Phase 4 着手前の設計判断であり、[§8](#8-未確認事項と次の検証) の未確認事項に挙げる。

**窓口（root secretary）の扱い — A では埋め込めない**:

[§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性) の例外注記のとおり、窓口は logical pane であり adapter 実ペインを持たない。**人間が最も長く見るセッションが、既存の attach 経路では UI に載らない。** 取り得る道は 3 つある。

| 案 | 内容 | 評価 |
|---|---|---|
| **S1. アプリが窓口を PTY でホストする** | `org up` の `os.execvpe` によるプロセス置換をやめ、アプリが自前で PTY を確保して `claude` を起動し、その PTY をブリッジに流す | **推奨**。runtime に「呼び出し元を置換せず PTY 上で起動する」経路を足すだけで済み、既存の CLI 起動経路は残せる |
| **S2. 窓口も adapter 実ペインとして spawn する** | root secretary を他ロールと同じく backend の detached session として起こす | 一貫性は最も高いが、`org up` の起動セマンティクス（人間の端末で動く）を変えるため既存運用への影響が大きい |
| **S3. 窓口だけターミナルに残す** | worker / dispatcher は UI に埋め込み、窓口は従来どおり端末で見る | 実装は最小だが「ターミナルを見なくてよい」という目標を達成しない。**暫定としてのみ許容** |

**Phase 4 は S1 を前提に設計する**。これを解かないと、案 C は「worker は見えるが窓口は端末」という中途半端な状態で止まる。

---

## 6. 推奨と段階ロードマップ

### 6.1 推奨: 案 C を主線とし、案 A をその第 0 フェーズとして吸収する

**理由**:

1. **可視性という最重要要件を無傷で保てる唯一の案である**（[§5.3](#53-可視性の保全--最重要要件)）。案 B はこれを構造的に損なう。
2. **外部の課金方針変更に対して構造的に頑健**（[§4.5](#45-課金制約が復活した場合の耐性)）。実行系が対話セッションのままである限り、分離課金が再開されても壊れない。
3. **配布時にエンドユーザーが自分の subscription を使える**（[§3.4](#34-認証と課金--アプリ化の最大の制約)(4)）。案 B は事前承認を取らない限り第三者配布で API 従量課金を強いる。
4. **公式機能との衝突が最小**（[§3.5](#35-公式が既に埋めつつある領域)）。ペイン管理の器は公式に任せ、claude-org 固有の運用規律を見せる UI に投資できる。
5. **段階的で、どの時点で止めても損をしない**。案 B は並走期間を作りにくい。

**案 B は棄却ではなく「条件付き保留」とする**。次の 2 条件が両方満たされたときに再評価する:
- (a) 分離課金の方針が恒久的に確定し、それが claude-org の使用量で成立する水準であること
- (b) 公式のデスクトップアプリ / Agent View が claude-org の運用規律層まで踏み込まないことが見えていること

これに加え、**製品として配布する意図がある場合は (c) claude.ai ログイン提供の事前承認が取れること**が条件に加わる（[§3.4](#34-認証と課金--アプリ化の最大の制約)(2)）。自分たちで使うだけなら (c) は不要。

### 6.2 段階ロードマップ

各フェーズは独立して価値を持ち、単独で revert できる。フェーズ間の依存は「前が終わっていないと次が意味をなさない」ものだけに絞ってある。

> **対応輸送系のスコープ宣言（重要）**: **Phase 2 以降のアプリは `ORG_TRANSPORT=broker` 構成専用とする。** 理由は、UI からの応答配送（[§5.1](#51-ui-化後の-4-ゲート) 要点 4）が broker の agent bind token + `mcp__org-broker__send_message` を前提にしており、renga には UI がホストプロセス外から peer message を投げるための等価な identity / 認証面が無いためである（[`tools/peer_notify.py`](../../tools/peer_notify.py) は renga 経路で `renga` バイナリの `mcp-peer` サブコマンドを stdio で叩くブリッジを持つが、これは best-effort であり全失敗を bool false に握り潰す契約なので、承認配送の経路には使えない）。
>
> **renga を捨てるわけではない**。renga は従来どおり CLI 運用の opt-in fallback として残り（[`docs/non-goals.md`](../non-goals.md):155 / [§2.5](#25-layer-3--輸送層と端末-backend)）、renga 利用者は現行の窓口対話 + 画面直視という体験を続ける。**アプリは broker 構成に対する追加の面であって、renga 経路を置き換えも劣化もさせない。** この宣言を怠ると「サポート構成のはずなのに承認 UI が黙って動かない」状態になる。

#### Phase 0 — 配布基盤の完成（案 A の中身）

**deliverable**:
- [`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):239-251 の未実施検証を潰す（sandbox マトリクス S1〜S6 / herdr attach 導線 H1 / Raspberry Pi 5 実機 A1 / buildx arm64 A2）
- `doctor` サブコマンド — 前提ツール・venv・pin・role config drift・broker 到達性を機械判定し、直し方を出力する
- 初回セットアップの 1 コマンド化（`source .venv/bin/activate` → `install-hooks.sh` → `org_setup_prune.py` → `org up` → `/org-setup` → `/org-start` の多段手順を畳む）
- CI での image publish（現在は非スコープ、同 :27-31）

**工数**: S〜M 級 × 6〜9 本（2〜4 週間）

**このフェーズだけで完結する価値**: ターミナル利用者にとっての導入摩擦が実際に下がる。UI 化が頓挫しても無駄にならない。

#### Phase 1 — 状態の一本化（UI 化の前提）

**deliverable**:
- `pending_decisions` を `state.db` のテーブルへ移行。既存 CLI（[`tools/pending_decisions.py`](../../tools/pending_decisions.py) の `append` / `resolve` / `mark-user-replied`）の外形と、ディスパッチャーの relay-gap 検出の参照経路は変えない
- worker の Progress Log / Status を `events` テーブルへ格納し、`.state/workers/*.md` を derived 層に降格（[`docs/contracts/state-semantics-contract.md`](../contracts/state-semantics-contract.md):42 の例外を解消）。契約改訂を伴う
- `StateWriter` に上記 2 つの write API を追加

**工数**: M 級 × 4 本（3〜4 週間）

**依存**: なし（Phase 0 と並行可）

**このフェーズだけで完結する価値**: dashboard の worker 表示が正規表現パースから解放され、状態の drift 源が 1 つ減る。

#### Phase 2 — 書込みサーフェスと認可

**deliverable**:
- dashboard backend に `POST` エンドポイント群を追加（`do_GET` 専用の現状から拡張、[`dashboard/server.py`](../../dashboard/server.py):396-411）
- localhost バインド + トークン認証 + origin 検証。トークンは broker の per-agent bind token と同じ発想でファイル 0600 に置く
- **UI 専用の broker agent bind token（messaging tier）の発行・ローテート・失効**（[§5.1](#51-ui-化後の-4-ゲート) 要点 4。窓口 root token の借用は `session_invalid` を招くため禁止）
- 全書込みを `events` に監査記録する（誰が / いつ / 何を承認したか）

**工数**: M 級 × 3〜4 本（3〜4 週間）

**依存**: Phase 1（書込み先が DB に揃っていること）

#### Phase 3 — 人間ゲートの UI 化

**deliverable**: [§5.1](#51-ui-化後の-4-ゲート) の 4 ゲートを UI カード化。承認結果を broker `send_message` で窓口セッションへ構造化通知。attention severity → UI 表現のマッピング（[§5.2](#52-attention-通知の-ui-化)）。

**工数**: M 級 × 3〜4 本（3〜4 週間）

**依存**: Phase 2

**このフェーズで初めて「人間がターミナルを見ずに承認できる」状態になる。**

#### Phase 4 — 可視性の UI 化（最重要フェーズ）

**deliverable**:
- PTY ↔ WebSocket ブリッジを `claude-org-runtime` に実装。**`TerminalAdapter` Protocol の面で定義し、backend 別実装をその下に置く**（[§5.3](#53-可視性の保全--最重要要件) の適用範囲表）
- **窓口（root secretary）のホスティング経路（S1）**: `org up` に「呼び出し元プロセスを `os.execvpe` で置換せず、アプリが確保した PTY 上で `claude` を起動する」経路を追加する。既存の置換起動は残す（切戻し可）。**これを解かないと人間が最も長く見るセッションが UI に載らない**
- フロントに端末エミュレータを埋め込み、**read-only 既定**で接続。書込みは明示トグル
- 複数 worker のタイル表示と、1 ペインへのフォーカス切替
- **対応 backend の宣言**: tmux から着手し、WezTerm（Windows 正準）/ herdr の可否をスパイクで確定する。tmux 専用で終える場合は「アプリの対応構成は broker+tmux」と明示的に宣言する（Windows を黙って落とさない）
- renga フレームでは適用外として明示的に案内（`org-attach` と同じ適用範囲）

**工数**: M〜L 級 × 4〜6 本（5〜8 週間）。内訳: ブリッジ本体 2 本 / 窓口ホスティング S1 が 1〜2 本 / フロント埋め込みとタイル表示 1〜2 本 / 非 tmux backend スパイク 1 本。

**依存**: Phase 2（認可基盤）

**このフェーズを飛ばすと案 C は「承認 UI だけあって中が見えないアプリ」になる。順序を落とさないこと。**

#### Phase 5 — セットアップ体験の UI 化とパッケージング

**deliverable**: Phase 0 の `doctor` / ウィザードを UI に載せる。アプリからの `org up` / `org down` / dashboard ライフサイクル管理（現在のスキル駆動 spawn を置換）。OS 別インストーラ。自動更新。

**工数**: M 級 × 3〜4 本（3〜4 週間）

**依存**: Phase 0、Phase 4

#### Phase 6（条件付き）— Agent SDK 評価スパイク

[§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する) の条件 (a)(b) が満たされた場合にのみ着手する、**破棄前提のスパイク**。1 ロール（例: curator のような判断境界の薄いロール）だけを SDK セッションに載せ替え、`settingSources` で既存 `.claude/` がそのまま効くかを実測する。**本線には取り込まない。**

**工数**: M 級 × 2 本（2 週間）

### 6.3 ロードマップ全体像

| Phase | 内容 | 工数 | 依存 | 単独価値 |
|---|---|---|---|---|
| 0 | 配布基盤の完成 | S〜M × 6-9（2-4 週） | — | あり |
| 1 | 状態の一本化 | M × 4（3-4 週） | — | あり |
| 2 | 書込みサーフェスと認可（UI 専用 broker token を含む） | M × 3-4（3-4 週） | 1 | 小 |
| 3 | 人間ゲートの UI 化 | M × 3-4（3-4 週） | 2 | 大 |
| 4 | 可視性の UI 化（窓口ホスティング S1 を含む） | M〜L × 4-6（5-8 週） | 2 | 大 |
| 5 | セットアップ UI とパッケージング | M × 3-4（3-4 週） | 0, 4 | 大 |
| 6 | SDK スパイク（条件付き） | M × 2（2 週） | 条件 (a)(b) | 判断材料のみ |

Phase 0 と 1 は並行可能。**Phase 1〜5 の合計は 17〜22 本**（[§4.3](#43-案-c--ハイブリッドdashboard-を操作-ui-に昇格) の案 C 工数と一致）、**Phase 0 を含めた総計は 23〜31 本 / 実時間 5〜8 か月**（Phase 6 を除く）。Phase 4 が最も重く、かつ最も要件に直結する。

---

## 7. 配布時の考慮

### 7.1 認証

**現行の設計は既に正しい方向にある**。Docker 配布設計は image を**認証ゼロで出荷**し、人間が初回に (1) `claude /login` (2) `gh auth login` (3) `codex login`（任意）(4) Slack / Google MCP 接続（任意）(5) `org_setup_prune.py --all` の 5 ステップを対話で 1 回通す。すべて `org_home` named volume に永続化され、以降のコンテナ再作成で再認証不要（[`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):212-223）。

アプリ化してもこのモデルを変えない。**「アプリが利用者の認証を代行する」形にはしない**——[§3.4](#34-認証と課金--アプリ化の最大の制約)(2) の制約に抵触するうえ、資格情報をアプリが預かる責任も生じる。UI は「Claude にログインしてください」というステップを案内し、実際のログインは `claude` プロセス自身に行わせる。

### 7.2 課金

利用者が自分の subscription で `claude` にログインする限り、claude-org 側が課金に介在することはない。**アプリが API key を要求する設計にしない**ことが、コストモデルを利用者にとって予測可能に保つ唯一の方法である（[§3.4](#34-認証と課金--アプリ化の最大の制約)(4)）。

分離課金が再開された場合の影響は [§4.5](#45-課金制約が復活した場合の耐性) の通りで、案 C はこれを受けない。

### 7.3 権限モデル

**実体は Anthropic 側にある**。[`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md):87-113 が「bwrap sandbox launcher は Claude Code CLI 本体の内部に存在し、claude-org-ja / runtime 側にランチャー実装は一切ない」と明記する。org 側は `sandbox.filesystem.*` を生成する consumer にすぎない。**アプリ化しても sandbox の実行主体は変わらない。**

org 側が持っているのは:
- ロール別の `settings.local.json`（allow / deny / hooks）を [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) と runtime 同梱 schema のマージから生成する仕組み
- `PreToolUse` フック群（[§2.2](#22-layer-4--ja-のプロンプト層)）
- CI での drift 検出（[§7.5](#75-依存追随と-ci-ゲート)）

**アプリ化で追加すべきは「利用者が権限設定を理解できる UI」**である。現在は `.claude/settings.local.json` の JSON を読むしかない。どのロールが何を許されているかを一覧表示するだけでも、配布物としての説明責任は大きく上がる。

### 7.4 公開資産と operator 固有設定の分離

境界は既に機械的に引かれている。[`.gitignore`](../../.gitignore):2-72 が `.state/*`（state.db・worker 状態・broker トークン）、`knowledge/raw/*`、`knowledge/skill-candidates.local.md`、`knowledge/curated/*.local.md`、`registry/projects.md`（実際の顧客名・非公開 repo URL を含む operator 実データ）、`.claude/settings.local.json` 系、`.dispatcher/.claude/`、`.curator/.claude/`、`CLAUDE.local.md`、`.env*` を除外する。

Docker 側も同じ境界を踏襲する。[`docker/Dockerfile`](../../docker/Dockerfile):15-54 は 2 段ビルドで、secret-scan stage が禁止パスの存在チェックと `sk-ant-` / `ghp_` / `github_pat_` / `xoxb-` / `xoxp-` / `PRIVATE KEY` のトークン形状 grep（`-l` でファイル名のみ出力し、トークン本体を build ログへ転写しない）を行い、引っかかると build を fail させる。runtime stage はこの scan 済み stage から COPY するため、scan を通らないコンテンツは image に入らない。**`.git` は image に含めない**（reflog / stash 由来の秘匿物をパターン grep で検査できないため。[`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):158）。

[`docs/contracts/state-fixture-scrub-policy.md`](../contracts/state-fixture-scrub-policy.md):51-82 は、`.state/` と `registry/` を公開テスト fixture 化する際に (1) 絶対パス `/home/<user>/` (2) 認証トークン (3) 非公開 repo URL / ngrok / internal host (4) `org-state.md` の Suspended 節の会話ログ の 4 カテゴリを MUST scrub とし、未スクラブは fixture commit を hard-block すると定める。**根拠として、過去に sandbox-probe 記録で dispatcher stdout への oauth_token 漏洩が複数回あったことが明記されている。**

**アプリ化で追加すべきこと**:
- UI が表示する内容（worker 画面のターミナル出力を含む）に資格情報が流れうる。**Phase 4 の端末埋め込みは、スクリーンショット共有やログエクスポート機能を安易に付けない**（付けるなら scrub を通す）。
- 配布物に operator 固有設定が混入していないかを、既存の secret-scan と同じ発想でインストーラ / パッケージのビルド時に検査する。

### 7.5 依存追随と CI ゲート

pin は `core-harness>=0.3.2,<0.4` と `claude-org-runtime>=0.1.37,<0.2`（[`pyproject.toml`](../../pyproject.toml):29-32、[`requirements.txt`](../../requirements.txt):22-111）。requirements.txt には 0.1.17 から 0.1.37 まで 16 回程度の floor bump 履歴がコメントとして残っており、herdr / broker のバグ修正・破壊的変更（socket 名変更等）・schema drift・delegate-plan 変更への追随が**頻繁に発生している**ことを示す。

**これは配布物としての claude-org にとって実質的なコストである**。Layer 2 が pre-1.0 で動いている限り、配布物は数週間おきに pin 追随を要求される。アプリ化するなら、**アプリのバージョンと runtime pin を紐付けて、利用者が pin を意識しなくてよい形にする**（アプリ更新 = 検証済みの組み合わせへの更新）ことが望ましい。

CI は [`.github/workflows/tests.yml`](../../.github/workflows/tests.yml):25-64 で 6 ゲートを直列実行する: `pip install -e .` → unittest discover（`tests/` と `tools/` の `test_*.py`）→ `check_runtime_schema_drift.py`（ja の `org_extension_schema.json` と pinned runtime 同梱 schema の drift 比較）→ `check_role_configs.py --include-worker-settings .` → shell hook tests（`tests/run-all.sh`）→ state-db drift_check の round-trip。別ワークフロー [`.github/workflows/install-scripts.yml`](../../.github/workflows/install-scripts.yml) が `install.sh` / `install.ps1` を shellcheck / PSScriptAnalyzer 静的解析 + 3 OS（ubuntu / macos / windows）のスタブ経由スモークテストにかける。

**アプリ化で追加すべきゲート**: UI ↔ backend の契約テスト、書込みサーフェスの認可テスト、PTY ブリッジの接続テスト。

### 7.6 現在のインストール摩擦（定量）

配布の出発点を数字で押さえておく。

- **前提ツール**: `git` / `gh` / Node.js / Python 3.10+ / `jq` / Claude Code CLI / `renga` の **7 種**（[`docs/getting-started.md`](../getting-started.md):9-22）。うちインストーラが fail-close で検証するのは `git` / `claude` / `gh` / `jq` の **4 種**のみで（[`scripts/install.sh`](../../scripts/install.sh):246-270 の `require_or_warn`）、`renga` / node / npm / tmux / wezterm は soft-warn に留まる（同 :262-283 の `optional_or_warn`）。Python と renga の両方が無い場合のみ「no usable launcher」で fail する（同 :337）。
- **手動ステップ**: ワンライナー後に `source .venv/bin/activate` → `install-hooks.sh` → `org_setup_prune.py --user-common-sandbox` → `claude-org-runtime org up`、さらに窓口 Claude 起動後に `/org-setup` → `/org-start` の **6 段**（[`docs/getting-started.md`](../getting-started.md):34-90）。`/org-setup` 未実行だと許可プロンプトが多発する。

**Phase 0 の目標はこれを「1 コマンド + ログイン 2 回」にすることである。**

---

## 8. 未確認事項と次の検証

本ドキュメントが**確認できなかった**こと。判断に効くものから順に並べる。

1. **分離課金の再開時期と条件**。公式ヘルプ記事は「事前告知の上で」としか書いておらず、具体的な予定は不明（https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan ）。**[§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する) の条件 (a) はこの不確実性そのものである。**
2. **配布と認証に関する 2 つの未確認前提（本ドキュメント最大の不確実性）**。
   - (2a) **「CLI をラップし利用者を自分の `/login` に誘導する形」が第三者配布の禁止対象外か**。[§3.4](#34-認証と課金--アプリ化の最大の制約)(4) のとおりこれは本ドキュメントの解釈であって一次情報の明示ではない。**案 A / 案 C が「利用者の subscription で動く」という前提はここに依存しており、[§4.4](#44-比較表) の課金行と [§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する) の推奨理由 3 がこれを土台にしている。** 配布に踏み出す前に Anthropic へ確認すること。
   - (2b) **claude.ai ログイン提供の「事前承認」の申請方法・審査基準**。制約の存在は明記されているが承認プロセスは一次情報に見当たらなかった。案 B を製品として配る道が本当に閉じているのかはこれ次第。
   
   **(2a) が否（CLI ラップ配布も不可）と判明した場合の含意**: 第三者への配布自体が全案で成立しなくなり、claude-org は自分たちで使う内製ハーネスとして位置づけ直すことになる。**その場合でも案 C の UI 化は自分たちの運用改善として価値を持ち、[§6.2](#62-段階ロードマップ) のロードマップは変わらない**（変わるのは Phase 5 のパッケージング / 配布の目的だけ）。したがって Phase 1〜4 は (2a) の確認を待たずに着手してよい。
3. **Agent SDK の programmatic hooks が `core_harness.hooks` の exit-code 契約とどう対応するか**。`settings.json` 経由の filesystem hooks はそのまま動くと明記されているが、`.hooks/*.sh` が前提とする「exit 2 + stderr で deny」がどこまで同一かは実測していない。**Phase 6 スパイクの主要な検証項目。**
4. **公式デスクトップアプリ / Agent View が今後どこまで運用規律層に踏み込むか**。Agent teams は現在 experimental・既定 disabled だが、共有タスクリストと mailbox は claude-org の broker と機能的に重なる。[§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する) の条件 (b)。
5. **PTY ↔ WebSocket ブリッジの実装コストの実測値**。Phase 4 の見積り（M〜L 級 × 4〜6 本）は既存 OSS（ttyd / gotty 相当の機構）の存在を前提にした粗い推定で、tmux の `attach -r` 相当をブラウザで再現する際の細部（リサイズ・IME・スクロールバック・カラー）は未検証。**特に IME は本組織の中心的関心事**（[`docs/design/renga-decoupling.md`](./renga-decoupling.md):23-25 が backend 別に実測して制約を撤回した経緯がある）で、ブラウザ埋め込み端末での日本語入力は別途実測が要る。
6. **非 tmux backend（WezTerm / herdr）で A 相当の可視性が成立するか**。tmux socket が無い構成では `tmux attach` の経路が存在せず、`TerminalAdapter` の `get_text` / `type_text` を streaming 化する方向になるが、**リアルタイム性と入力往復が実用水準に届くかは未検証**。[§5.3](#53-可視性の保全--最重要要件) の適用範囲表で「要スパイク」としたもの。**ここが不成立なら、アプリの対応構成を broker+tmux に絞る（= Windows 正準の WezTerm を Phase 4 の対象外にする）判断が Phase 4 着手前に必要になる。**
7. **窓口ホスティング（S1）が `org up` の既存起動セマンティクスを壊さずに追加できるか**。`os.execvpe` によるプロセス置換（`claude_org_runtime/broker/launcher.py:342-344`）を残したまま「PTY 上で起動する」第 2 経路を足す想定だが、mcp-config の受け渡し・token mint・folder-trust プロンプトの扱いが置換起動と同一に保てるかは未検証。
8. **Docker 配布設計の未実施検証項目の実際の難度**。S1〜S6（sandbox 検証マトリクス）/ H1 / A1 / A2 は「未実施」であること以上の情報がない（[`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):239-251）。Phase 0 の工数見積りはこの不確実性を含む。
9. **`org_extension_schema.json` 全体の権限モデル**。本調査では冒頭のみ確認しており、各ロールの `required_allow` / `required_deny` / sandbox 定義の全体像は精読していない。[§7.3](#73-権限モデル) の「権限設定を理解できる UI」を設計するには全数把握が要る。
10. **Managed Agents の課金体系と適用可能性**。Anthropic がエージェントとサンドボックスの両方をホストする形（https://platform.claude.com/docs/en/managed-agents/overview.md ）は本ドキュメントでは選択肢に含めなかったが、「配布」ではなく「サービス提供」に舵を切る場合には第 4 の案になりうる。課金体系・skills / MCP 対応の詳細は未確認。

---

## 改訂履歴

- 2026-08-04: 初版（task `org-app-design-research-001`）。
