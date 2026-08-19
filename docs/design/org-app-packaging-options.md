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
> - [`renga`](https://github.com/suisya-systems/renga) v1.4.0 のソース（[§4.4](#44-案-d--org-native-な専用マルチプレクサrenga-の-org-native-化) 案 D の棚卸し対象。読み取りのみ・変更なし。パスは renga リポジトリのルート相対で示す）
>
> **renga の版に関する読み方の境界**: 上の v1.4.0 は **§4.4 の棚卸しが対象にした版**であって、本ドキュメント全体がその版を前提にしているという意味ではない。[§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性) の可視性の記述（および [§5.3](#53-可視性の保全--最重要要件) の backend 比較表の renga 行）は**現行 renga 2.0 の運用事実**に更新済みだが、§4.4 の機能棚卸しは 2.0 に対して**再調査していない**。

---

## 1. 目的とスコープ

### 1.1 何を決めるための文書か

現在の claude-org-ja は、**Claude Code の対話 TUI セッションを複数並べ、その間の連絡とペイン操作を MCP 経由で束ねる運用規律フレームワーク**である。利用者は前提ツールを自分で導入し、venv を activate し、`claude-org-runtime org up` をターミナルで叩き、`/org-setup` → `/org-start` をスラッシュコマンドで打つ（[`docs/getting-started.md`](../getting-started.md):9-22, :34-90）。

この形態は「ハーネスを自分で組める人」には十分だが、**組織運用そのものをプロダクトとして他人に渡す**には摩擦が大きい。本ドキュメントは次の 3 点を判断可能にする:

1. 現状の何がターミナルに**本質的に**縛られていて、何が既に切り離されているのか（[§2](#2-現状アーキテクチャの棚卸し)）
2. 「アプリ化」の実現形態として何が取り得て、それぞれの工数・リスク・移行経路はどうか（[§4](#4-実現形態の比較)）
3. どれを推し、どういう順番でやるのか（[§6](#6-推奨と段階ロードマップ)）

### 1.2 スコープと非スコープ

**スコープ**: 現状アーキテクチャの棚卸し、実現形態 **4 案**の比較（A / B / C に加え、追補として D = org-native な専用マルチプレクサ）、人間ゲートの UI 化設計、可視性の保全設計、推奨案と段階ロードマップ、配布時の考慮。

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

**タスクルーティングは 2 レーン制**。工数 S 以下・単一ファイル級・判断仰ぎなし・日またぎなしを全て満たす極小タスクのみ、窓口が Agent tool（`isolation="worktree"`、背景実行必須）で直処理してよい（[`CLAUDE.md`](../../CLAUDE.md):71-88）。背景実行は現行ハーネスでは常時保証される（`Agent` の入力スキーマから `run_in_background` が廃止された。Issue #942）。PreToolUse フック [`.hooks/block-foreground-subagent.sh`](../../.hooks/block-foreground-subagent.sh) は `run_in_background` が存在しかつ `true` でない呼び出しを exit 2 で deny する旧ハーネス互換の防波堤として残る。

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

- **broker / tmux フレーム** — 各ペインは `claude-org-broker-{pid}-{seq}` という独立した detached tmux session（1 ペイン = 1 session）として存在し、人間が自分の端末から `/usr/bin/tmux -L claude-org-broker attach -r -t <session>` で入る。`-r` は read-only attach、`Ctrl-b d` でデタッチ（ペインは動いたまま。`Ctrl-b` は tmux prefix の既定値で、変更していれば設定した prefix に読み替える。**attach する端末が renga の場合は org サイドバーが `Ctrl+B` を消費してこの打鍵が届かない** — 回避策は [`docs/operations/dispatcher-view.md`](../operations/dispatcher-view.md) の「外側フレームが renga の場合」）。[`.claude/skills/org-attach/SKILL.md`](../../.claude/skills/org-attach/SKILL.md):33-53, :174-185 がこの attach コマンド**文字列を生成するだけ**の read-only スキル。
- **renga フレーム**（transport が renga = `ORG_TRANSPORT=renga` のケース） — ペインは**所属タブ内のタイル**であって独立した detached session ではなく、「detached session へ attach し直す」概念が写像しない。org は全ペインを同一タブへ置くので（これは renga の性質ではなく org 側の配置規則で、規範の正本は契約 [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md) §4.2 の SINGLE-TAB MUST）、**その org のタブを表示している間は画面をそのまま見ればよい**。別のタブを表示している間は org のペインが視界に入らないため、org のタブへ切り替えるか、renga の org サイドバー（全タブ横断でタブとペインを一覧する cross-tab パネル）から該当ペインを選ぶ（同 :37-53）。

加えて [`tools/org-dispatcher-view.sh`](../../tools/org-dispatcher-view.sh):195-236 が「dispatcher の自己修復する read-only ビュー」を提供し、dispatcher の restart / auto-compact fork でセッション名が変わっても自動再探索・再 attach する。

> **重要な例外 — 窓口（root secretary）は attach 対象外**。窓口は broker 起動時に logical pane（bookkeeping entry）として登録されるだけで **adapter 実ペインを持たず `pane_id` が `null`** であり、broker socket の detached session に出現しない（[`.claude/skills/org-attach/SKILL.md`](../../.claude/skills/org-attach/SKILL.md):114-119）。これは `org up` が対話型 `claude` TUI を **`os.execvpe` で呼び出し元プロセスに置換して**起動するためで（`claude_org_runtime/broker/launcher.py:342-344`）、窓口は「org を起動した人間の手元 terminal」そのものに住んでいる。**つまり `worker` / `dispatcher` は埋め込めても、人間が最も長く見る窓口セッションだけは既存の tmux 経路では埋め込めない。** [§6.2](#62-段階ロードマップ) Phase 4 はこの穴を明示的に埋める必要がある。

> **backend による差**: 上記の「1 ペイン = 1 detached tmux session」は **broker × tmux backend に固有**である。Windows 正準の WezTerm backend と herdr backend は tmux socket を持たないため `tmux attach` の経路が存在せず、renga transport もペインが所属タブ内のタイルであって detached session の概念自体を持たない。可視性の実装は backend ごとに別の話になる（[§5.3](#53-可視性の保全--最重要要件)）。

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

   > **実装上の注記**: `settingSources` の省略は `["user","project","local"]` と等価だと公式が明記している（https://code.claude.com/docs/en/agent-sdk/claude-code-features.md の 「When you omit `settingSources`, `query()` reads the same filesystem settings as the Claude Code CLI: user, project, and local settings, CLAUDE.md files, and `.claude/` skills, agents, and commands.」および「Omitting `settingSources` is equivalent to `["user", "project", "local"]`.」の 2 箇所。**「省略すると何も読まれない」という理解は誤りで、本ドキュメントのレビューで 2 度出たため出典を明記しておく**）。それでも、**実装では省略に頼らず明示指定すべき**である。理由は 2 つ: (i) 既定に依存すると、どの設定が効いているかがコード上で読めず、ロール別 permission の監査ができない。(ii) 公式自身が「マルチテナント分離に既定の `query()` オプションを頼るな」と警告しており（同ページ Warning）、`settingSources` が制御しない入力（managed policy / `~/.claude.json` / auto memory / claude.ai MCP connectors）が別途あるため、意図しない設定の混入を防ぐには明示が要る。
2. **`canUseTool` は承認 UI を提供しない**。公式は「アプリがこれらのリクエストをユーザーに提示する必要がある」「スニペット中の `ask_user` はあなたのアプリ自身のプロンプト UI の代役」と明記する（https://code.claude.com/docs/en/agent-sdk/user-input.md ）。**承認 UI・ツール可視化・トランスクリプトビューアはすべて自前実装になる。**
3. **Agent teams は SDK から構成できない**。claude-org がやっている「複数の独立インスタンスの協調」に相当する公式機能は CLI 専用であり、SDK で同等をやるなら**今 broker がやっていることを自前で持ち続ける**ことになる（これは悪い知らせではない — 既に持っているものが無駄にならない）。

### 3.4 認証と課金 — アプリ化の最大の制約

**(1) 課金分離は発表されたが一時停止された。** [`docs/design/renga-decoupling.md`](./renga-decoupling.md):19 が確定制約 #1 として引く「2026-06-15 から Agent SDK / `claude -p` が subscription 上限と分離される」という変更は、**発効予定日に Anthropic が pause した**。公式ヘルプ記事は現在「この変更を一時停止している。現時点では何も変わっていない。Claude Agent SDK・`claude -p`・サードパーティアプリの利用は引き続き subscription の usage limits から消費される」と明記している（https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan ）。

> **この事実の扱い**: 本ドキュメントは design only であり、[`docs/design/renga-decoupling.md`](./renga-decoupling.md) を改訂しない（参照は一方向）。ただし**「ヘッドレス化は課金上不成立」という前提は現時点では成立していない**ことは、アプリ化の判断材料として明示しておく必要がある。同時に、これは**撤回ではなく一時停止**であり、再提案の時期は公式記事に「事前告知の上で」としか書かれていない。**恒久的に安全とみなしてはならない**。案 B / 案 C のいずれも、この制約が復活したときに壊れない設計にしておくべきである（[§4.6](#46-課金制約が復活した場合の耐性)）。

**(2) サードパーティ配布では claude.ai ログインを提供できない。** これは pause の影響を受けない、より重い制約である。Agent SDK Overview と Quickstart の双方が「**事前承認がない限り、Anthropic はサードパーティ開発者が自社製品（Claude Agent SDK ベースのエージェントを含む）で claude.ai ログインやそのレート制限をユーザーに提供することを認めない。代わりに API key 認証方式を使うこと**」と明記している（https://code.claude.com/docs/en/agent-sdk/overview.md ）。

つまり **事前承認を得ない限り、SDK ベースのアプリを製品として第三者に配ると、エンドユーザーは自分の Claude subscription を使えず API 従量課金になる**。claude-org のワーカー使用量では、これは subscription 利用とは桁の違うコストになる。

**ただしこれは「承認があれば可」という条件付きの制約である**（原文が "Unless previously approved" と書いている）。承認の申請方法・審査基準は一次情報に見当たらず（[§8](#8-未確認事項と次の検証) 項目 2）、**承認が現実的に取れるなら案 B の配布経路は開く**。本ドキュメントは「承認なし」を既定シナリオとして比較するが、これは案 B を恒久的に排除する根拠ではない。

**(3) ブランド制約**。SDK ベースの製品は「Claude Code」「Claude Code Agent」を名乗れず、独自ブランドを維持する必要がある（https://code.claude.com/docs/en/agent-sdk/overview.md の Branding guidelines）。

**(4) CLI をそのまま使う形態が (2) の対象外かどうかは、確認が必要な前提である**。引用した方針文は「サードパーティ開発者が**自社製品**で claude.ai ログインやレート制限を**ユーザーに提供**すること」を禁じ、SDK ベースのエージェントを「含まれる例」として挙げている。**「CLI をラップし、利用者を自分の `claude /login` に誘導する形」がこの禁止に当たらない**というのは本ドキュメントの**解釈であって、一次情報が明示しているわけではない**。

既存の Docker 配布設計はこの形をとっており、image は認証ゼロで出荷し、人間が初回に `claude /login` / `gh auth login` を対話で通す（[`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):212-223）。この形が許容されるという前提は、**案 A / 案 C が「利用者の subscription で動く」と述べる根拠そのもの**であり、[§4.5](#45-比較表) の課金行と [§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する案-d-は方針判断が下りれば-phase-4-を置換する) の推奨理由 3 がこれに依存している。**したがって、これは配布に踏み出す前に Anthropic へ確認すべき前提条件として扱う**（[§8](#8-未確認事項と次の検証) 項目 2）。確認が取れず CLI ラップ配布も禁止だと判明した場合、「第三者への配布」自体が全案で成立しなくなり、claude-org は**自分たちで使う内製ハーネス**として位置づけ直すことになる（その場合でも案 C の UI 化は自分たちの運用改善として価値を持ち、ロードマップは変わらない）。

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
| サードパーティは claude.ai ログイン提供不可 | 影響なし **と見込む（未検証、[§3.4](#34-認証と課金--アプリ化の最大の制約)(4)）** | **製品配布の致命的制約**（API 従量課金） | 影響なし **と見込む（同左・未検証）** |
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
- Docker 経路には環境固有の落とし穴が残る（コンテナ内 bwrap のため `seccomp=unconfined` が必要。同 :180）。なお Raspberry Pi 5 の 16KB page size については、**当初懸念した `kernel=kernel8.img`（4KB）切替は publish 済み tag では不要と実機実測で確定**（herdr 0.7.4 / Claude Code 2.1.223 内蔵 ripgrep 14.1.1 とも 16KB カーネルで動作。ただし `CLAUDE_CODE_VERSION` 既定が可変 `stable` のため免除は測った版限り。`docs/design/org-docker-distribution.md` §12 A1）。

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

### 4.4 案 D — org-native な専用マルチプレクサ（renga の org-native 化）

**方針**: 「汎用マルチプレクサ（tmux / WezTerm / herdr / renga）に org を載せる」のをやめ、**org を第一級市民として設計したターミナルマルチプレクサ**を作り、それが組織の前提になる。**新規開発ではなく [`renga`](https://github.com/suisya-systems/renga) の進化形**（org-native 化）として評価する。

#### 4.4.1 renga が既に持っているもの（実コード棚卸し）

以下は renga v1.4.0（`Cargo.toml` の `version = "1.4.0"`、Rust 実装・全 43 ファイル 28,864 LOC）の実ソースを読んで確認したもの。パスは renga リポジトリのルート相対。**この棚卸しは v1.4.0 時点のままで、renga 2.0 に対しては再調査していない**（2.0 の実体に合わせて更新したのは [§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性) と [§5.3](#53-可視性の保全--最重要要件) の可視性の記述だけである）。

- **MCP peer サーバ 15 ツール**（`src/mcp_peer/mod.rs:444-737` のツールカタログ、同 :967-981 のディスパッチ）— `list_peers` / `send_message` / `set_summary` / `check_messages` / `list_panes` / `spawn_pane` / `spawn_claude_pane` / `spawn_codex_pane` / `close_pane` / `focus_pane` / `new_tab` / `inspect_pane` / `send_keys` / `set_pane_identity` / `poll_events`。**[§2.5](#25-layer-3--輸送層と端末-backend) の Surface 1-6 に相当する面は既に全部ある。**
- **role 付き spawn** — ただし現状は**自由記述のラベル**であり、「UI と `list_panes` の出力に表示される」だけで権限や振る舞いを持たない（`src/mcp_peer/mod.rs:544-547` の `role` フィールド説明: "Optional free-form role label (e.g. 'worker', 'foreman', 'curator'). Shown in the UI and in list_panes output."）。
- **Claude セッションの構造化監視**（`src/claude_monitor.rs:1-4`）— `~/.claude/projects/<project>/*.jsonl` の transcript を追尾し、`current_tool` / `subagent_count` / `subagent_types` / `is_working` / `tool_use_count` / `model` / トークン使用量（input / output / cache read / cache creation）/ **TodoWrite の todo リスト** / `context_tokens` / `git_branch` を抽出する（同 :20-46 の `ClaudeState`）。**これは grid scrape ではなく構造化データの読み取りである。**
- **その状態の UI 描画** — ペインタイトルに稼働中サブエージェント名と現在ツール（`src/ui.rs:795-830`）、todo 進捗バーと進行中タスク（同 :850-865）、ステータスバーに model・context 使用率バー（0.9 超で赤 / 0.7 超で黄）・git ブランチ（同 :1727-1770）。
- **IME 合成オーバーレイ**（`src/input/overlay.rs:1-21`）— 中央のコンポジションボックスにホスト端末の IME 候補窓をアンカーさせる機構。WSL2 / Windows Terminal が `Alt+Enter` を奪う問題への `Ctrl+J` フォールバックまで実装済み。**日本語運用の中核。**
- レイアウト設定（TOML、`src/layout_config.rs`）、ファイルツリーサイドバー（`src/filetree.rs:110`）、画像プレビュー、i18n（ja / en、OS ロケール自動判定つき。`src/i18n.rs:1-15`）、workspace / タブ管理。
- 技術スタック: `ratatui` 0.30 / `crossterm` 0.29 / `portable-pty` 0.8 / `vt100` 0.16（`Cargo.toml` の `[dependencies]`）。

#### 4.4.2 renga が持っていないもの = 案 D の作業範囲

renga のソースツリー全体（`src/**/*.rs`）を横断 grep した結果、**org の運用規律にあたる概念は 1 つも存在しない**:

| 概念 | renga ソース中のヒット |
|---|---|
| `state.db` | **0 ファイル** |
| `pending_decision` | **0 ファイル** |
| `escalation` | **0 ファイル** |
| `attention` | **0 ファイル** |
| `journal_append` | **0 ファイル** |
| `awaiting_user` | **0 ファイル** |

つまり renga は「Claude Code を並べて見る良い端末」であって、「org を知っている端末」ではない。**案 D が足すのは正確にこの差分**である:

1. **人間ゲート 4 種のネイティブ UI**（[§5.1](#51-ui-化後の-4-ゲート) の 4 ゲートを承認カード + キーバインドとして TUI に実装）
2. **`state.db` の読み取りとサイドバー統合**（worker / run / events を常時表示。現在のファイルツリーサイドバーの隣、または切替タブとして）
3. **spawn 儀式の内部吸収** — folder-trust プロンプトと dev-channel プロンプトを**マルチプレクサ自身が知っていて自動で通す**（現在は org 側が `send_keys(enter=true)` で外から叩いている）
4. **escalation register の表示と応答**（`pending_decisions` の 4 段階ライフサイクルを UI 上で進める）
5. **attention 統合**（severity → ペイン装飾 + OS 通知。classifier ロジックは Layer 2 のものを再利用）
6. **`role` の一級市民化** — 自由記述ラベルから、permission・レイアウト配置・監視ポリシーを持つ型へ

#### 4.4.3 この案の構造的な強み

**(a) ターミナル依存 2 条項が「依存」から「ネイティブ機能」に転化する。** [§2.5](#25-layer-3--輸送層と端末-backend) で特定した Surface 1.7 (`inspect_pane`) / 1.9 (`send_keys`) は、現在「外部の org が PTY を叩いて Claude Code の TUI を操作する回避策」である。案 D ではこれらが**マルチプレクサ自身の内部 API** になる。契約上の REQUIRED は満たされ続けるが、**それが弱点でなくなる**。案 B がこの 2 条項を消そうとして安全保証の再設計を強いられるのと対照的に、案 D は 2 条項を**正当化する**。

**(b) 可視性は定義上無傷。** マルチプレクサが画面そのものなので、[§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性) の 4 要素（生の出力 / 動いている感 / 介入可能性 / 直接性）はすべて現状のまま。**さらに `claude_monitor.rs` により、[§5.3](#53-可視性の保全--最重要要件) の手段 A（生の端末）と手段 B（構造化ストリーム）を既に同時に持っている。** 案 C が Phase 4 で作ろうとしているものの一部が、renga には既に実装済みである。

**(c) 窓口ホスティング問題が発生しない。** [§5.3](#53-可視性の保全--最重要要件) で案 C の課題として挙げた「窓口は logical pane なので埋め込めない」は、マルチプレクサが窓口を最初のペインとして起動する構造（`renga-layouts/ops.toml` の `[root]` が `role = "secretary"`）ではそもそも問題にならない。

**(d) Windows が一級で通る。** IME overlay が WSL2 / Windows Terminal 前提で実装されており、案 C の Phase 4 が抱える「非 tmux backend では PTY ブリッジの経路がない」問題（[§5.3](#53-可視性の保全--最重要要件) の適用範囲表）が発生しない。

#### 4.4.4 この案の弱み

**(a) リモート / モバイル閲覧性がゼロ。** renga はローカル TUI である。SSH 越しに端末を開けば使えるが、**ブラウザやスマートフォンから「今どうなっている？」を見ることはできない**。案 C の dashboard は localhost HTTP なので、ポートフォワードすれば遠隔から見える。**外出先からの状況確認・承認という用途は案 D 単独では埋まらない。**

**(b) Rust / Python の言語境界をまたぐ。** org の状態層は Python（`tools/state_db/`）と SQLite で、renga は Rust。案 D は renga 側に SQLite クライアント（`rusqlite` 等）を新規依存として入れ、**`state.db` のスキーマが 2 言語間の契約面になる**。現在 `docs/contracts/state-schema-contract.md` / `state-semantics-contract.md` は Python 実装だけを想定しており、書込み規律（`StateWriter` 経由のみ、post-commit フックで `org-state.md` を再生成）を Rust 側からどう守るかは新しい設計問題である。

**(c) 現行の方針と逆行する。** `DEFAULT_TRANSPORT` は runtime 0.1.28 で `renga` → `broker` にフリップ済み（`claude_org_runtime/transport/descriptor.py:54-61`）で、[`docs/design/renga-decoupling.md`](./renga-decoupling.md):18 は「renga を『組織が要求する必須前提』から『ユーザーが任意に選べる opt-in fallback』へ**降格**する」完全移行を設計方針として掲げている。**案 D はこれを反転させ、renga を org の第一級前提へ再昇格させる。** 技術的に不可能ではないが、**これは技術判断ではなく方針判断**であり、オーナーの明示的な意思決定が要る。

**(d) 公式機能との部分的衝突。** Agent teams は tmux / iTerm2 の split-pane 表示を持つ（[§3.5](#35-公式が既に埋めつつある領域)）。「複数エージェントをペインで並べる」部分は公式も来る。ただし案 D の差別化は**org の運用規律がネイティブに入っている**ことなので、案 B ほど正面からはぶつからない。

**(e) 単一実装への集中リスク。** 案 D を選ぶと、org の体験が renga 1 実装に強く結びつく。tmux / WezTerm / herdr backend は「動くが org-native の恩恵は受けられない」二級市民になる。

#### 4.4.5 工数

AI ワーカー実行前提。**L 級中心に 15〜21 本（実時間 4〜6 か月）**。

| 作業 | 本数 |
|---|---|
| org 概念の Rust 側モデル化 + `state.db` read（SQLite クライアント新規依存、スキーマ契約の Rust 側実装） | L 級 3〜4 本 |
| 人間ゲート 4 種のネイティブ UI + キーバインド + 応答経路 | L 級 3〜4 本 |
| escalation register の表示・応答（`pending_decisions` の 4 段階ライフサイクル） | M 級 2〜3 本 |
| spawn 儀式の内部吸収（folder-trust / dev-channel の自動承認） | M 級 2 本 |
| attention 統合（severity → ペイン装飾 + OS 通知） | M 級 1〜2 本 |
| `role` の一級市民化（permission / レイアウト / 監視ポリシー） | L 級 2〜3 本 |
| `state.db` write 経路と Python 側との書込み規律の契約整備 | L 級 2〜3 本 |

**前提**: [§6.2](#62-段階ロードマップ) の Phase 1（状態の一本化）は案 D でも必須。`pending_decisions` が別 JSON のままでは、Rust 側が読む対象が 3 系統に散る。

#### 4.4.6 案 C との関係 — 排他ではなく複合が成立する

**これが案 D 評価の中で最も重要な発見である。**

案 C（Web UI）と案 D（org-native TUI）は、**同じ `state.db` を正本として別々の面を作る**構造なので、排他ではない。役割分担が自然に決まる:

- **案 D = ローカルの一次体験**（画面の前にいるとき。生の端末 + 構造化状態 + 人間ゲート）
- **案 C = 遠隔監視と承認**（画面の前にいないとき。ブラウザ / スマホから状況確認と承認）

複合構成にしたときの [§6.2](#62-段階ロードマップ) への影響:

| Phase | 案 C 単独 | 複合（C の Web + D の TUI） |
|---|---|---|
| 1 状態の一本化 | 必須 | **必須（両案の共通前提）** |
| 2 書込みサーフェスと認可 | 必須 | 必須（Web 面の遠隔承認に要る。案 D 単独なら同一ホスト直接書込みで HTTP 不要） |
| 3 人間ゲートの UI 化 | Web で実装 | **Web と TUI の両面で実装**（両方に要る。ロジックは共通、描画層が 2 つ） |
| 4 可視性の UI 化 | M〜L 級 4〜6 本。PTY ブリッジ + 窓口ホスティング S1 + 非 tmux backend スパイク | **不要になる**（案 D が可視性を担う）。代わりに案 D の TUI 実装が乗る |
| 5 セットアップ UI とパッケージング | 必須 | 必須（renga バイナリの同梱が加わる） |

**複合の正味コスト**: 案 C の Phase 4（M〜L 級 4〜6 本）が消え、案 D の 15〜21 本が乗る。Phase 3 は Web 面のみ（TUI 面は案 D の内訳に含まれる）。**Phase 0 を除いた合計は案 C 単独の 17〜22 本に対して 28〜37 本**（内訳は [§6.1.1](#611-案-d-を踏まえた推奨の再評価) の分岐表を参照）。**明確に高い。** ただし得られるものは「ローカルで最良の体験 + 遠隔からも見える」であり、案 C 単独では Phase 4 を全力でやってもローカル体験は既存 tmux の埋め込み止まりである。

### 4.5 比較表

| # | 観点 | A パッケージング | B GUI（Agent SDK） | C ハイブリッド | D org-native TUI |
|---|---|---|---|---|---|
| 1 | アーキテクチャ変更 | なし | 実行系を全面置換 | 人間接点のみ追加 | マルチプレクサを置換（実行系は不変） |
| 2 | ターミナル依存の解消 | しない | する（PTY は消える。`claude` バイナリは残る） | UI 内に埋め込む形で隠す | **しない（依存をネイティブ機能に転化させる）** |
| 3 | **worker 画面の可視性** | ◎（現状維持） | △（構造化ログ相当まで劣化） | ◎（tmux をそのまま埋め込む） | **◎（定義上無傷 + 構造化監視を既に実装済み）** |
| 4 | 人間ゲートの構造化 | ×（自然言語のまま） | ◎ | ◎ | ◎ |
| 5 | プロンプト層の再利用 | 100% | 高（`settingSources` でそのまま。pane 語彙の除去は要） | 100% | 100% |
| 6 | 既存契約（Set D）への影響 | なし | **Surface 1.7 / 1.9 の再批准が必要** | なし | なし（両条項が正当化される） |
| 7 | 公式機能との衝突 | なし | **大**（Desktop / Agent View / Agent teams） | 小 | 中（Agent teams の split-pane と部分衝突） |
| 8 | 配布時の課金 | 利用者の subscription **※要確認** | **API 従量課金**（第三者配布時。事前承認が取れれば subscription も可） | 利用者の subscription **※要確認** | 利用者の subscription **※要確認** |
| 9 | **リモート / モバイル閲覧** | ×（端末のみ） | ○（GUI 次第） | **◎（localhost HTTP + ポートフォワード）** | **×（ローカル TUI のみ）** |
| 10 | Windows 対応 | ○（WezTerm backend） | ○ | △（PTY ブリッジが非 tmux で未検証） | **◎（IME overlay が Windows 前提で実装済み）** |
| 11 | 実装言語 / 境界 | 変更なし | TS or Python 単一 | Python + JS（既存の延長） | **Rust + Python の境界が新規に発生** |
| 12 | 既存方針との整合 | 整合 | 整合 | 整合 | **逆行**（renga 降格方針の反転。方針判断が要る） |
| 13 | 工数 | S〜M × 6〜9 本（2〜4 週） | M〜L × 20〜30 本（4〜7 か月） | M〜L × 17〜22 本（5〜7 か月） | L 中心 × 15〜21 本（4〜6 か月） |
| 14 | 失敗時の巻き戻し | 容易 | 困難（並走しにくい） | 容易（フェーズ単位） | 中（renga を戻せばよいが org 側の依存が残る） |
| 15 | 対象読者の拡大 | 小 | 大（ただし課金モデルが変わる） | 中〜大 | 小〜中（端末利用者に限られる） |

**※要確認（観点 8）**: A / C / D が「利用者の subscription で動く」のは、**「CLI をラップし利用者を自分の `/login` に誘導する形は第三者配布の禁止対象外」という未検証の前提**に依存している（[§3.4](#34-認証と課金--アプリ化の最大の制約)(4) / [§8](#8-未確認事項と次の検証) 項目 2a）。この前提が否定されると 3 案とも「配布時に利用者の subscription を使う」が成立しなくなる。**配布に踏み出す前に Anthropic への確認が要る。**

**C と D は排他ではない**（[§4.4.6](#446-案-c-との関係--排他ではなく複合が成立する)）。複合すると 9・10 の弱点を D が C で、3・11 の弱点を C が D で補い合う。複合の工数は **Phase 0 込みで 34〜46 本 / 8〜12 か月**（Phase 0 を除くと 28〜37 本。内訳は [§6.1.1](#611-案-d-を踏まえた推奨の再評価) の分岐表を参照）。

### 4.6 課金制約が復活した場合の耐性

[§3.4](#34-認証と課金--アプリ化の最大の制約)(1) の分離課金は pause 中だが再開されうる。各案の耐性:

- **案 A / 案 C / 案 D**: 実行系が対話セッションのままなので**影響を受けない**。これは無視できない利点である。
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

   > **案 D（[§4.4](#44-案-d--org-native-な専用マルチプレクサrenga-の-org-native-化)）ではこの問題が消える**。マルチプレクサ自身が MCP peer サーバの実装主体なので、UI が「外部クライアントとして token で繋ぐ」必要がなく、窓口ペインへの配達は内部呼び出しになる。案 D の構造的な利点の 1 つ。
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
| **E. org-native マルチプレクサ**（[§4.4](#44-案-d--org-native-な専用マルチプレクサrenga-の-org-native-化) の案 D） | ◎ | ◎ | ◎ | ◎ | マルチプレクサが画面そのものなので定義上無傷。さらに renga は `claude_monitor.rs` で **A と B を既に同時に実装している**（サブエージェント名・現在ツール・todo 進捗・context 使用率）。ただしリモート / モバイル閲覧は不可 |

**案 C の枠内では A を第一、B を補完とする**（案 D を採る場合は [§4.4.3](#443-この案の構造的な強み)(b) のとおり E が A+B を包含するので、この節の議論は不要になる）。理由:

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
| renga（transport） | 概念が非適用（ペインは所属タブ内のタイルで detached session を持たない） | **適用外**。renga 利用者は従来どおり、org のペインが並ぶ org のタブを表示して直接見る（org 同一タブ規則の下で。[§2.8.3](#283-operator-が-worker-の作業画面を直接覗ける可視性)） | 対象外と明示 |

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

### 6.1 推奨: 案 C を主線とし、案 A をその第 0 フェーズとして吸収する。案 D は方針判断が下りれば Phase 4 を置換する

> **案 D 追補による再評価の結論（[§4.4](#44-案-d--org-native-な専用マルチプレクサrenga-の-org-native-化)）**: **主線は変えない**（案 C の Phase 1〜3 + 案 A を Phase 0）。**ただし Phase 4（可視性の UI 化）については、オーナーが「renga を org の第一級前提へ再昇格させる」方針判断を下すなら、案 D で置換するのが技術的に優れている。** 判断が下りなければ案 C の Phase 4（PTY ブリッジ + 窓口ホスティング S1）を予定どおり実施する。詳細は [§6.1.1](#611-案-d-を踏まえた推奨の再評価)。

**理由**:

1. **可視性という最重要要件を無傷で保てる**（[§5.3](#53-可視性の保全--最重要要件)）。案 B はこれを構造的に損なう。案 D はこの点で案 C より優れる（[§6.1.1](#611-案-d-を踏まえた推奨の再評価)）。
2. **外部の課金方針変更に対して構造的に頑健**（[§4.6](#46-課金制約が復活した場合の耐性)）。実行系が対話セッションのままである限り、分離課金が再開されても壊れない。
3. **案 B と違い、配布時の課金モデルが「事前承認の取得」に依存しない**。案 B は事前承認を取らない限り第三者配布で確実に API 従量課金になる（[§3.4](#34-認証と課金--アプリ化の最大の制約)(2)）。案 A / C / D は利用者が自分の subscription を使える**見込み**だが、**これは確定した利点ではなく未検証の前提である**（[§3.4](#34-認証と課金--アプリ化の最大の制約)(4)）。前提が否定されれば A / C / D も第三者配布では成立しなくなるため、**本項は「推奨を支える利点」ではなく「配布に踏み出す前に解消すべき前提条件」として読むこと**（[§8](#8-未確認事項と次の検証) 項目 2a）。ただし**自分たちで使う限りはどの案もこの前提に依存しない**ため、[§6.2](#62-段階ロードマップ) の Phase 0〜4 は確認を待たずに進められる。
4. **公式機能との衝突が最小**（[§3.5](#35-公式が既に埋めつつある領域)）。ペイン管理の器は公式に任せ、claude-org 固有の運用規律を見せる UI に投資できる。
5. **段階的で、どの時点で止めても損をしない**。案 B は並走期間を作りにくい。

**案 B は棄却ではなく「条件付き保留」とする**。次の 2 条件が両方満たされたときに再評価する:
- (a) 分離課金の方針が恒久的に確定し、それが claude-org の使用量で成立する水準であること
- (b) 公式のデスクトップアプリ / Agent View が claude-org の運用規律層まで踏み込まないことが見えていること

これに加え、**製品として配布する意図がある場合は (c) claude.ai ログイン提供の事前承認が取れること**が条件に加わる（[§3.4](#34-認証と課金--アプリ化の最大の制約)(2)）。自分たちで使うだけなら (c) は不要。

#### 6.1.1 案 D を踏まえた推奨の再評価

**変えた点と変えなかった点を分けて述べる。**

**変えなかった点 — 主線は案 C のまま**。Phase 0（配布基盤）と Phase 1（状態の一本化）は、**案 D を採っても採らなくても必要**である。特に Phase 1 は案 D の前提でもある（[§4.4.5](#445-工数)）。**したがって無条件に先行着手してよいのはこの 2 フェーズだけ**である。

   Phase 2（書込みサーフェスと認可）と Phase 3（人間ゲートの UI 化）は**分岐依存**で、案 D 置換（下記の分岐 2）を選ぶと案 D の実装に吸収されて別立てでは要らなくなる。**分岐が確定する前に Phase 2 に着手すると、分岐 2 を選んだ場合にその作業が無駄になる。**

**変えた点 — Phase 4 は案 D で置換しうる、という分岐を明示する**。案 C の Phase 4 は本ロードマップで最重量（M〜L 級 4〜6 本）かつ最も難所が多い: PTY ↔ WebSocket ブリッジ、窓口ホスティング S1、非 tmux backend（Windows 正準の WezTerm）での未検証性。**案 D はこの 3 つをいずれも構造的に解消する**——マルチプレクサが画面そのものなのでブリッジが要らず、窓口は最初のペインなのでホスティング問題が起きず（[§4.4.3](#443-この案の構造的な強み)(c)）、IME overlay が Windows 前提で実装済みなので Windows が一級で通る（同 (d)）。加えて renga は `claude_monitor.rs` で構造化監視を既に持っており、Phase 4 で作ろうとしていたものの一部が実装済みである。

**にもかかわらず案 D を無条件の推奨にしない理由は 3 つ**:

1. **方針の逆行であり、技術判断ではなく経営判断だから**（[§4.4.4](#444-この案の弱み)(c)）。`DEFAULT_TRANSPORT` は既に broker へフリップ済みで、[`docs/design/renga-decoupling.md`](./renga-decoupling.md):18 は renga を必須前提から opt-in fallback へ**降格**する完全移行を掲げている。案 D はこれを反転させる。**この反転をオーナーが選ぶかどうかは、私が技術的に決めてよい範囲を超えている。**
2. **リモート / モバイル閲覧性がゼロだから**（同 (a)）。外出先から状況を確認・承認したい用途があるなら、案 D 単独では埋まらず案 C の Web 面が別途要る（= 複合、[§4.4.6](#446-案-c-との関係--排他ではなく複合が成立する)）。この用途の有無はオーナーにしか分からない。
3. **Rust / Python の言語境界が新規に発生するから**（同 (b)）。`state.db` の書込み規律（`StateWriter` 経由のみ・post-commit フックで derived 層を再生成）を Rust 側からどう守るかは、既存契約（Set C / Set F）が想定していない新しい設計問題である。

**したがって推奨は次の 3 分岐になる**。各行は [§6.2](#62-段階ロードマップ) の Phase 別見積りと [§4.4.5](#445-工数) の案 D 見積りをそのまま足したもので、検算できる形にしてある。

| 分岐 | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 | 案 D 本体 | Phase 5 | 合計 | 実時間 |
|---|---|---|---|---|---|---|---|---|---|
| **1. renga 再昇格をしない**（現行方針を維持。案 C 単独） | 6〜9 | 4 | 3〜4 | 3〜4 | 4〜6 | — | 3〜4 | **23〜31 本** | 5〜8 か月 |
| **2. renga 再昇格を決め、遠隔閲覧は要らない**（案 D 置換） | 6〜9 | 4 | **D に吸収** | **D に吸収** | **D が担う** | 15〜21 | 3〜4 | **28〜38 本** | 6〜9 か月 |
| **3. renga 再昇格を決め、遠隔閲覧も要る**（複合） | 6〜9 | 4 | 3〜4 | 3〜4（Web 面のみ） | **不要** | 15〜21 | 3〜4 | **34〜46 本** | 8〜12 か月 |

**分岐 2 で Phase 2 / 3 が消える理由**: Phase 2（HTTP 書込みサーフェス + 認可 + UI 専用 broker token）は「UI がホストプロセスの外から state を書き、窓口へ配達する」ための層である。案 D ではマルチプレクサ自身が同一ホストで直接書き、窓口ペインへの配達も内部呼び出しになるため、この層が要らない（[§4.4.6](#446-案-c-との関係--排他ではなく複合が成立する)）。Phase 3（人間ゲートの UI 化）は [§4.4.5](#445-工数) の案 D 内訳に「人間ゲート 4 種のネイティブ UI」「escalation register の表示・応答」「attention 統合」として既に含まれている。**二重計上を避けるため、分岐 2 では Phase 2 / 3 を別立てしない。**

**どの分岐でも共通で先に着手できるのは Phase 0（配布基盤）と Phase 1（状態の一本化）である**。この 2 つは方針判断を待たずに始めてよい。Phase 2 以降は分岐が確定してから着手する（分岐 2 を選ぶと Phase 2 の作業が無駄になるため）。

**分岐の選択は 2 つの問いで決まる**。どちらもオーナーにしか答えられないため、[§8](#8-未確認事項と次の検証)（未確認事項）ではなくここに明示的な決定事項として置く。

**Q1: org-native な TUI に追加投資し、renga を org の第一級前提へ再昇格させるか。**
- **No** → **分岐 1（案 C 単独）**。Q2 は問わない。既存の tmux をアプリ内に埋め込む形でローカル体験を作り、現行の renga 降格方針を維持する。
- **Yes** → Q2 へ。

**Q2: 画面の前にいないときに org の状況を確認・承認したいか。**
- **No** → **分岐 2（案 D 置換）**。Web 面は作らず、ローカル TUI に一本化する。
- **Yes** → **分岐 3（複合）**。案 D をローカル体験、案 C の Web を遠隔監視に使う。

**Q1 が主で Q2 が従である**理由は、Q1 が No なら遠隔閲覧の要否にかかわらず案 C 単独で両方（ローカルの埋め込み端末 + ブラウザからの遠隔アクセス）が賄えるためである。**Q1 で問うているのは 2 点**——「追加投資を、ローカル体験の質と `send_keys` / `inspect_pane` の正当化に払う価値があるか」と、「[`docs/design/renga-decoupling.md`](./renga-decoupling.md):18 の降格方針を反転させる意思があるか」（[§4.4.4](#444-この案の弱み)(c)）。

追加投資の幅は、置き換わるフェーズの見積り幅から区間演算で出す（両端を単純に引くと幅を過小評価する）:

| 移行 | 構造的な変化 | 追加本数 |
|---|---|---|
| 分岐 1 → 分岐 3（複合） | Phase 4（4〜6）を落とし案 D（15〜21）を足す | **+9〜17 本** |
| 分岐 1 → 分岐 2（D 置換） | Phase 2（3〜4）+ Phase 3（3〜4）+ Phase 4（4〜6）を落とし案 D（15〜21）を足す | **+1〜11 本** |

分岐 2 の下限が +1 本まで縮むのは、案 D が Phase 2 / 3 / 4 の 3 つを同時に吸収するためである。**つまり「遠隔閲覧が要らない」なら、案 D 置換は案 C 単独とほぼ同コストで済む可能性がある**——ただし上限は +11 本まで振れるので、幅の広さ自体が Q1 の判断リスクである。

### 6.2 段階ロードマップ

各フェーズは独立して価値を持ち、単独で revert できる。フェーズ間の依存は「前が終わっていないと次が意味をなさない」ものだけに絞ってある。

> **対応輸送系のスコープ宣言（重要）**: **Phase 2 以降のアプリは `ORG_TRANSPORT=broker` 構成専用とする。** 理由は、UI からの応答配送（[§5.1](#51-ui-化後の-4-ゲート) 要点 4）が broker の agent bind token + `mcp__org-broker__send_message` を前提にしており、renga には UI がホストプロセス外から peer message を投げるための等価な identity / 認証面が無いためである（[`tools/peer_notify.py`](../../tools/peer_notify.py) は renga 経路で `renga` バイナリの `mcp-peer` サブコマンドを stdio で叩くブリッジを持つが、これは best-effort であり全失敗を bool false に握り潰す契約なので、承認配送の経路には使えない）。
>
> **案 D を採る場合、この宣言は反転する**（[§4.4](#44-案-d--org-native-な専用マルチプレクサrenga-の-org-native-化)）。案 D では renga 自身が MCP peer サーバの実装主体なので、UI から窓口への配達は外部 token を要さない内部呼び出しになり、broker 専用にする理由が消える。**このスコープ宣言は「案 C の Web 面を作る場合」に適用される。**
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

[§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する案-d-は方針判断が下りれば-phase-4-を置換する) の条件 (a)(b) が満たされた場合にのみ着手する、**破棄前提のスパイク**。1 ロール（例: curator のような判断境界の薄いロール）だけを SDK セッションに載せ替え、`settingSources` で既存 `.claude/` がそのまま効くかを実測する。**本線には取り込まない。**

**工数**: M 級 × 2 本（2 週間）

### 6.3 ロードマップ全体像

| Phase | 内容 | 工数 | 依存 | 単独価値 |
|---|---|---|---|---|
| 0 | 配布基盤の完成 | S〜M × 6-9（2-4 週） | — | あり |
| 1 | 状態の一本化 | M × 4（3-4 週） | — | あり |
| 2 | 書込みサーフェスと認可（UI 専用 broker token を含む） | M × 3-4（3-4 週） | 1 | 小 |
| 3 | 人間ゲートの UI 化 | M × 3-4（3-4 週） | 2 | 大 |
| 4 | 可視性の UI 化（窓口ホスティング S1 を含む）**／ 案 D で置換しうる** | M〜L × 4-6（5-8 週）／ 案 D なら L 中心 15-21 本 | 2（案 D なら 1 のみ） | 大 |
| 5 | セットアップ UI とパッケージング | M × 3-4（3-4 週） | 0, 4 | 大 |
| 6 | SDK スパイク（条件付き） | M × 2（2 週） | 条件 (a)(b) | 判断材料のみ |

Phase 0 と 1 は並行可能。**Phase 1〜5 の合計は 17〜22 本**（[§4.3](#43-案-c--ハイブリッドdashboard-を操作-ui-に昇格) の案 C 工数と一致）、**Phase 0 を含めた総計は 23〜31 本 / 実時間 5〜8 か月**（Phase 6 を除く）。Phase 4 が最も重く、かつ最も要件に直結する。

**案 D を採る場合の総計**は [§6.1.1](#611-案-d-を踏まえた推奨の再評価) の分岐表を参照（置換なら 28〜38 本 / 6〜9 か月、複合なら 34〜46 本 / 8〜12 か月）。**どの分岐でも共通で先に着手できるのは Phase 0 と Phase 1 の 2 つ**（Phase 2 / 3 は分岐 2 では案 D に吸収されるため、分岐確定前に着手すると無駄になりうる）。

---

## 7. 配布時の考慮

### 7.1 認証

**現行の設計は既に正しい方向にある**。Docker 配布設計は image を**認証ゼロで出荷**し、人間が初回に (1) `claude /login` (2) `gh auth login` (3) `codex login`（任意）(4) Slack / Google MCP 接続（任意）(5) `org_setup_prune.py --all` の 5 ステップを対話で 1 回通す。すべて `org_home` named volume に永続化され、以降のコンテナ再作成で再認証不要（[`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):212-223）。

アプリ化してもこのモデルを変えない。**「アプリが利用者の認証を代行する」形にはしない**——[§3.4](#34-認証と課金--アプリ化の最大の制約)(2) の制約に抵触するうえ、資格情報をアプリが預かる責任も生じる。UI は「Claude にログインしてください」というステップを案内し、実際のログインは `claude` プロセス自身に行わせる。

### 7.2 課金

利用者が自分の subscription で `claude` にログインする限り、claude-org 側が課金に介在することはない。**アプリが API key を要求する設計にしない**ことが、コストモデルを利用者にとって予測可能に保つ唯一の方法である（[§3.4](#34-認証と課金--アプリ化の最大の制約)(4)）。

分離課金が再開された場合の影響は [§4.6](#46-課金制約が復活した場合の耐性) の通りで、案 C はこれを受けない。

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

- **前提ツール**: [`docs/getting-started.md`](../getting-started.md):9-22 の表は 7 種（`git` / `gh` / Node.js / Python 3.10+ / `jq` / Claude Code CLI / `renga`）を挙げるが、**これは renga fallback を含めた全用途を満たす場合の数**である。既定の broker 経路に限れば内訳は次のとおり:

  | 区分 | ツール | インストーラの扱い |
  |---|---|---|
  | 既定経路で必須 | `git` / `claude` / `gh` / `jq` | **fail-close で検証**（[`scripts/install.sh`](../../scripts/install.sh):246-270 の `require_or_warn`） |
  | 既定経路で必須（だが soft-warn） | Python 3.10+、tmux（POSIX）または WezTerm（Windows） | soft-warn。ただし Python と renga の**両方**が無い場合のみ「no usable launcher」で fail（同 :337）。tmux / WezTerm は「既定 broker transport の端末 backend に必要」と警告文に明記されつつ install は止まらない（同 :281-283） |
  | renga fallback 専用（既定経路では不要） | Node.js / npm / `renga` | soft-warn。警告文にも "only needed to install renga (ORG_TRANSPORT=renga)" と明記（同 :262-268） |

  つまり **既定経路の実質的な前提は 6 種**（git / claude / gh / jq / Python / tmux か WezTerm）で、うち 2 種は「必要なのに install が止まらない」ため**未導入のまま起動して初めて失敗する**。Phase 0 の `doctor` が塞ぐべきはまさにこのギャップである。
- **手動ステップ**: ワンライナー後に `source .venv/bin/activate` → `install-hooks.sh` → `org_setup_prune.py --user-common-sandbox` → `claude-org-runtime org up`、さらに窓口 Claude 起動後に `/org-setup` → `/org-start` の **6 段**（[`docs/getting-started.md`](../getting-started.md):34-90）。`/org-setup` 未実行だと許可プロンプトが多発する。

**Phase 0 の目標はこれを「1 コマンド + ログイン 2 回」にすることである。**

---

## 8. 未確認事項と次の検証

本ドキュメントが**確認できなかった**こと。判断に効くものから順に並べる。

1. **分離課金の再開時期と条件**。公式ヘルプ記事は「事前告知の上で」としか書いておらず、具体的な予定は不明（https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan ）。**[§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する案-d-は方針判断が下りれば-phase-4-を置換する) の条件 (a) はこの不確実性そのものである。**
2. **配布と認証に関する 2 つの未確認前提（本ドキュメント最大の不確実性）**。
   - (2a) **「CLI をラップし利用者を自分の `/login` に誘導する形」が第三者配布の禁止対象外か**。[§3.4](#34-認証と課金--アプリ化の最大の制約)(4) のとおりこれは本ドキュメントの解釈であって一次情報の明示ではない。**案 A / 案 C が「利用者の subscription で動く」という前提はここに依存しており、[§4.5](#45-比較表) の課金行と [§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する案-d-は方針判断が下りれば-phase-4-を置換する) の推奨理由 3 がこれを土台にしている。** 配布に踏み出す前に Anthropic へ確認すること。
   - (2b) **claude.ai ログイン提供の「事前承認」の申請方法・審査基準**。制約の存在は明記されているが承認プロセスは一次情報に見当たらなかった。案 B を製品として配る道が本当に閉じているのかはこれ次第。

   **(2a) が否（CLI ラップ配布も不可）と判明した場合の含意**: 第三者への配布自体が全案で成立しなくなり、claude-org は自分たちで使う内製ハーネスとして位置づけ直すことになる。**その場合でも案 C の UI 化は自分たちの運用改善として価値を持ち、[§6.2](#62-段階ロードマップ) のロードマップは変わらない**（変わるのは Phase 5 のパッケージング / 配布の目的だけ）。したがって Phase 1〜4 は (2a) の確認を待たずに着手してよい。
3. **Agent SDK の programmatic hooks が `core_harness.hooks` の exit-code 契約とどう対応するか**。`settings.json` 経由の filesystem hooks はそのまま動くと明記されているが、`.hooks/*.sh` が前提とする「exit 2 + stderr で deny」がどこまで同一かは実測していない。**Phase 6 スパイクの主要な検証項目。**
4. **公式デスクトップアプリ / Agent View が今後どこまで運用規律層に踏み込むか**。Agent teams は現在 experimental・既定 disabled だが、共有タスクリストと mailbox は claude-org の broker と機能的に重なる。[§6.1](#61-推奨-案-c-を主線とし案-a-をその第-0-フェーズとして吸収する案-d-は方針判断が下りれば-phase-4-を置換する) の条件 (b)。
5. **PTY ↔ WebSocket ブリッジの実装コストの実測値**。Phase 4 の見積り（M〜L 級 × 4〜6 本）は既存 OSS（ttyd / gotty 相当の機構）の存在を前提にした粗い推定で、tmux の `attach -r` 相当をブラウザで再現する際の細部（リサイズ・IME・スクロールバック・カラー）は未検証。**特に IME は本組織の中心的関心事**（[`docs/design/renga-decoupling.md`](./renga-decoupling.md):23-25 が backend 別に実測して制約を撤回した経緯がある）で、ブラウザ埋め込み端末での日本語入力は別途実測が要る。
6. **非 tmux backend（WezTerm / herdr）で A 相当の可視性が成立するか**。tmux socket が無い構成では `tmux attach` の経路が存在せず、`TerminalAdapter` の `get_text` / `type_text` を streaming 化する方向になるが、**リアルタイム性と入力往復が実用水準に届くかは未検証**。[§5.3](#53-可視性の保全--最重要要件) の適用範囲表で「要スパイク」としたもの。**ここが不成立なら、アプリの対応構成を broker+tmux に絞る（= Windows 正準の WezTerm を Phase 4 の対象外にする）判断が Phase 4 着手前に必要になる。**
7. **窓口ホスティング（S1）が `org up` の既存起動セマンティクスを壊さずに追加できるか**。`os.execvpe` によるプロセス置換（`claude_org_runtime/broker/launcher.py:342-344`）を残したまま「PTY 上で起動する」第 2 経路を足す想定だが、mcp-config の受け渡し・token mint・folder-trust プロンプトの扱いが置換起動と同一に保てるかは未検証。
8. **Docker 配布設計の未実施検証項目の実際の難度**。S1〜S6（sandbox 検証マトリクス）/ H1 / A1 / A2 は「未実施」であること以上の情報がない（[`docs/design/org-docker-distribution.md`](./org-docker-distribution.md):239-251）。Phase 0 の工数見積りはこの不確実性を含む。
9. **`org_extension_schema.json` 全体の権限モデル**。本調査では冒頭のみ確認しており、各ロールの `required_allow` / `required_deny` / sandbox 定義の全体像は精読していない。[§7.3](#73-権限モデル) の「権限設定を理解できる UI」を設計するには全数把握が要る。
10. **案 D（org-native マルチプレクサ）に固有の未検証点**。
   - (10a) **Rust 側から `state.db` の書込み規律を守れるか**。現行契約（[`docs/contracts/state-semantics-contract.md`](../contracts/state-semantics-contract.md):38-42）は「書込みは `StateWriter` 経由のみ、post-commit フックで derived 層を再生成」を Python 実装前提で定めている。Rust から同じ規律をどう保証するか（薄い IPC で Python 側に委譲するか、規律ごと Rust に再実装して契約を 2 実装に広げるか）は未設計。
   - (10b) **renga の `claude_monitor.rs` が読む JSONL transcript の形式安定性**。`~/.claude/projects/<project>/*.jsonl` は Claude Code の内部形式であり、公式が後方互換を約束したものではない。renga は既にこれに依存しているが、案 D で人間ゲートの判断材料に使うなら依存度が上がる。形式変更時の劣化の仕方（壊れるか、静かに空になるか）は未確認。
   - (10c) **renga 再昇格の方針判断そのもの**。[§6.1.1](#611-案-d-を踏まえた推奨の再評価) のとおりこれは技術判断ではなくオーナーの判断であり、本ドキュメントは決めない。判断に必要な問いは「画面の前にいないときに org の状況を見たい / 承認したいか」の 1 点に絞れる。

11. **Managed Agents の課金体系と適用可能性**。Anthropic がエージェントとサンドボックスの両方をホストする形（https://platform.claude.com/docs/en/managed-agents/overview.md ）は本ドキュメントでは選択肢に含めなかったが、「配布」ではなく「サービス提供」に舵を切る場合には第 4 の案になりうる。課金体系・skills / MCP 対応の詳細は未確認。

---

## 改訂履歴

- 2026-08-04: 初版（task `org-app-design-research-001`）。
- 2026-08-04: 案 D（org-native な専用マルチプレクサ = renga の org-native 化）を追補（[§4.4](#44-案-d--org-native-な専用マルチプレクサrenga-の-org-native-化)）。renga v1.4.0 の実ソース棚卸し、§4.5 の 4 案 15 観点比較への拡張、案 C との複合成立性、[§6.1.1](#611-案-d-を踏まえた推奨の再評価) の推奨再評価（主線は不変・Phase 4 のみ分岐）を追加。
