# 案 b 設計: transport-neutral skill source + generator アーキテクチャ

> **目的**: Epic #586 の「prose 両系維持」を、**手動の両系反転（案 a）**から
> **transport-neutral source + generator による機械生成（案 b）**へ作り直す設計を確定する。
> 本ドキュメントは設計のみ。実装・スキル生成・既存ファイル反転は一切しない。成果物は本設計 doc
> （`notes/broker-skill-generator-design.md`）と、その provenance である
> `notes/broker-skill-gen-design-input.md`（pivot 入力メモ、§9.2 #8 ratified で commit）の 2 ファイル。
> 実装は人間 ratification ゲートを通してから。

- **関連 Issue**: Refs #586
- **生成日**: 2026-06-16
- **計画 SoT（上位）**: [`notes/broker-promotion-plan-586.md`](./broker-promotion-plan-586.md)
  （§1 反転テンプレ / §1.3 論理反転チェックリスト 8 軸 / §2 33 ファイル inventory + 難度表 /
  §3 既定値フリップ spec / §4 contract amendment / §5 Phase 依存順）。
- **pivot 入力（provenance）**: [`notes/broker-skill-gen-design-input.md`](./broker-skill-gen-design-input.md)
  — 手動反転ワーカー（broker-prose-sweep-t1、承認ゲートで stand down）の discovery 分析メモ（自己矛盾の解消理由 /
  保持すべき不変条件 / surgical 最難ファイル / 差異の局所性 4 点）。その実体は本設計 §0〜§5 に取り込み済みで本
  ドキュメントは self-contained に読めるが、監査証跡として本ブランチに commit 済み（§9.2 #8 ratified）。
  **当該メモの「frontmatter = renga byte 不変」は §0.4 / §9.2 #9 で per-transport render へ訂正**（union は
  auth 迂回欠陥で不採用、§9.2 #9 は再 ratify 待ち。メモ側にも訂正注記あり）。
- **既存 transport seam（一次再利用対象）**: [`tools/transport.py`](../tools/transport.py)
  （`rewrite_allow_entries` = renga↔broker のツール名機械変換。`TEMPLATE_TRANSPORT=renga` を恒等素材面と
  する確立済みパターン）。runtime descriptor（`claude_org_runtime.transport`）が transport 機構の単一 SoT。

---

## 0. 出発点と問題の定式化

### 0.1 案 a（手動両系 prose）の構造的欠陥

Epic #586 計画（§2）は dual-system 文言を持つ 33 ファイルを手動で反転する PR-3a/3b/3c を置いた。pivot 入力
メモが指摘した手動反転の最大の難所は **「定型ブロックの既定宣言」と「本文の操作系ツール参照
（`mcp__renga-peers__*`）」が transport に連動して両方反転しなければならない**点である。片方だけ反転すると
**ファイル内で自己矛盾**（ヘッダは「broker で書いてある」と宣言するのに本文は renga のまま）が起きる。33
ファイル × 散在する本文ツール参照を人手で漏れなく反転するのは、レビューでも検出しにくい高リスク作業である。

### 0.2 案 b の核心アイデア

source を **transport-neutral プレースホルダ**化し、**ヘッダの既定宣言ブロックも本文のツール参照も同一の
`transport` パラメータから 1 パスで render する**。これにより:

- ヘッダと本文が **構造的に同一 transport 面へ render される**ので、両者が乖離する自己矛盾リスクが
  **設計レベルで消える**（手動反転の最大の難所が消滅する）。
- 既定フリップ（broker 既定）も opt-in 反転（renga）も、source を変えずに **generator のパラメータ切替**で
  導出できる。33 ファイルの「手動反転 PR」は「source 整備 + 1 回の機械生成 + drift CY」へ置き換わる。

### 0.3 すでに存在する前例（本設計が乗る土台）

[`tools/transport.py`](../tools/transport.py) の `rewrite_allow_entries` は、**allowlist（frontmatter
`allowed-tools` 相当）について既に同じことを機械生成している**:

- テンプレートの著述面 `TEMPLATE_TRANSPORT = "renga"` を**恒等素材面**とみなす（`if resolved ==
  TEMPLATE_TRANSPORT: return list(entries)` = byte 等価を構造保証）。
- broker へは `mcp__renga-peers__` プレフィックスを role-tier 集合へ機械置換して導出。
- 付け替え基準を `DEFAULT_TRANSPORT`（既定値）から**意図的に分離**し `TEMPLATE_TRANSPORT` で固定。
  → 既定が broker にフリップしても renga テンプレは恒等のまま（#586 で顕在化したバグの再発防止）。

**本設計の眼目は、この allowlist で確立済みの seam パターンを prose 本文へ拡張すること**であり、新しい
SoT を作るのではなく既存 seam を延伸する（§論点 6）。

### 0.4 「2 つの render 基底」— 本設計で最重要の前提

1 つの SKILL.md の中に、**異なる transport 基底で render すべき 2 領域**が同居する:

| 領域 | render 基底 | 理由 |
|---|---|---|
| frontmatter `allowed-tools`（ツール認可面） | **per-transport render（`DEFAULT_TRANSPORT`=broker）を per-entry 接頭辞リネームで**（skill 固有のツール集合を保存。§2.2 ※3） | Claude Code はこの frontmatter をディスクから読み**ツール認可をゲート**する。broker 既定時は本文が指示する broker ツールを認可する。renga ツールは broker 既定面では**認可しない**（auth 迂回防止）。**`rewrite_allow_entries`（role-tier 置換）は使わず** server 接頭辞だけ broker へリネームし、skill 固有サブセットを過剰拡大しない（§2.2 ※3・broker 省略ツールは ※4 で drop） |
| 本文 prose + dual-system ヘッダ | **`DEFAULT_TRANSPORT` = broker（render 面）** | 人間が読む面。既定フリップ後は broker 面が literal、renga は opt-in として併記 |

> **重要な設計判断（2 段の訂正を経て確定）**:
>
> 1. **当初案「frontmatter を renga 恒等のまま残す」は誤り**（R2 で検出）。SKILL.md の `allowed-tools` は実際に
>    `mcp__renga-peers__*` を列挙しており（現状 14 スキル）Claude Code がツール認可をゲートするため、broker 本文 ×
>    renga 恒等 frontmatter は **broker 既定実行でツール未認可**を招く。
> 2. **次に検討した「transport-union（renga + broker 両系を同時認可）」も不適**（auth レビューで検出）。union は
>    broker 既定セッションで renga 面（特に broker が**意図的に省く** `focus_pane`/`new_tab` や上位 tier の pane
>    制御）を認可したままにし、**broker auth モデルを迂回**できてしまう。さらに union は既存 `rewrite_allow_entries`
>    の置換挙動と矛盾し、settings.local.json も per-transport 置換なので **union の「再生成なし切戻し」利点は実は
>    成立しない**（settings 層も切戻し時に renga へ戻す必要がある）。
> 3. **確定 = per-transport render（per-entry 接頭辞リネーム）**: frontmatter は本文と同じ **broker 単一面**で
>    render する（renga ツールは broker 面に出さない）。これにより (a) broker 既定で本文ツールが認可され、
>    (b) renga 面の auth 迂回が無い。**`rewrite_allow_entries`（role-tier 置換）ではなく per-entry の server 接頭辞
>    リネーム**で skill 固有サブセットを保存する（§2.2 ※3。role-tier 置換は per-skill 認可を過剰拡大するため不可）。
>
> **renga byte 不変の正しい意味（rollback byte 安定）**: promotion-plan §3.2 / 入力メモの「frontmatter
> `allowed-tools` = renga byte 不変」は、「broker 面に renga を残す」ではなく **「renga が `rewrite_allow_entries`
> の恒等基底（`TEMPLATE_TRANSPORT`）であり、`ORG_TRANSPORT=renga` で再生成すると byte 等価な renga frontmatter が
> 得られる」= rollback byte 安定性**を意味する（§0.3 の恒等核と同じ）。
>
> **rollback の運用（§3.3）**: 既定 broker からの切戻しは `ORG_TRANSPORT=renga` + **再生成**（org-setup が
> generator を renga で再走）で、frontmatter・settings・本文すべてが byte 安定な renga 面に戻る。これは settings.
> local.json が既に取っている per-transport 再生成モデルと**完全に同型**で、新たな運用負担を足さない（promotion-plan
> §5.6 の「コード変更なし」は満たす — 再生成はコード変更ではなく機械的な regen ステップ）。
>
> **`org-setup/references/permissions.md`**: skill frontmatter とは**機構が異なる**（§4.2(2)）。permissions.md は
> 「役割の全 tier 認可」が意図なので **`rewrite_allow_entries`（role-tier 置換）**を使う。skill frontmatter は
> **per-entry 接頭辞リネーム（subset 保存、§2.2 ※3）で role-tier 拡大をしない**。両者の共通点は「renga が恒等基底で
> rollback byte 安定」だけで、置換ロジックは別経路（混同すると skill frontmatter で過剰認可になる）。

この**「frontmatter のツール認可面をどの transport で固定するか」**こそ本設計が解く中心問題で、broker auth 整合と
rollback 運用のトレードオフ（上記の根源的トレードオフ／モデル 1 vs 2、§9.2 #9）として人間判断に載せる。本設計は
**モデル 1（per-transport render + 再生成、auth クリーン）を推奨**し、frontmatter は **per-entry 接頭辞リネーム**
（skill 固有サブセット保存、§2.2 ※3）で broker 面へ render、renga 再生成を恒等基底として rollback byte 安定を
担保する（§論点 2 / §論点 5）。

---

## 1. 論点 1: 中立著述形式（プレースホルダ構文 + 4 局所差異の表現）

### 1.1 制約と差異の局所性

pivot 入力メモ §「差異の局所性」より、2 transport の差は実質 **4 点に局所化**される:

1. **ツール名接頭辞**（`mcp__renga-peers__*` ↔ `mcp__org-broker__*`）— 本文に散在するが純粋な接頭辞置換。
2. **受信モデル**（broker push 一次 vs renga in-band push）— dual-system ブロック内の記述。
3. **spawn 儀式**（renga 1 段承認 vs broker 2 段承認 = folder-trust + dev-channel sidecar）— ブロック内記述。
4. **エラーコード**（broker 拡張コード）+ `new_tab`/`focus_pane` 不在注記 — ブロック内記述。

**本文の大半は transport 非依存**。差異は (1) 散在接頭辞 と (2)〜(4) の dual-system ブロックに限られる。

### 1.2 3 案の比較

| 軸 | 案 1A: インライン条件分岐 | 案 1B: トークン + per-transport フラグメント（推奨） | 案 1C: per-transport 別ソース全体 |
|---|---|---|---|
| 形式 | `{{#if broker}}…{{else}}…{{/if}}` を本文に散在 | 本文は中立トークン（`{{FQ}}` 等）、4 局所差異は名前付きフラグメント `{{> dual-system-header }}` で注入 | broker 用ソースと renga 用ソースを別々に保持 |
| 可読性 | ✕ 本文が条件だらけ。`mcp__` 参照ごとに分岐 → 「条件分岐最小化」要件に反する | ◎ 本文は条件ゼロ（トークンのみ）。分岐は 4 つの名前付きフラグメントへ押し出される | △ 各ソースは読めるが 2 本ある |
| 自己矛盾耐性 | △ 分岐の取りこぼしが残る | ◎ 1 パス render で header/本文が同基底。乖離不可能 | ✕ 案 a と同じ二重管理（pivot で否定した形） |
| 差異の局所化 | ✕ 差異が本文全体に拡散 | ◎ 差異が 4 フラグメント + トークン定義に集約 | ✕ ソース全体が複製 |
| 保守 | 分岐の整合を全箇所で目視 | フラグメント SoT を 1 箇所で保守、本文は中立で共有 | 2 ソースの同期が常時必要 |

### 1.3 推奨: 案 1B（トークン + per-transport フラグメント）

**本文ツール参照は中立トークン化**し、**4 局所差異は少数の名前付きフラグメントで注入**する。条件分岐を本文から
追い出し、可読性を最優先する。具体構文:

**(a) 接頭辞・サーバー名トークン（本文の散在ツール参照）**

| トークン | render(broker) | render(renga) | 由来 |
|---|---|---|---|
| `{{FQ}}` | `mcp__org-broker__` | `mcp__renga-peers__` | `transport.fq_prefix(flag)` |
| `{{SERVER}}` | `org-broker` | `renga-peers` | `transport.server_name(flag)` |
| `{{CHANNEL_SRC}}` | `org-broker` | `renga-peers` | `<channel source="…">` の**transport タグ値**（契約 L120/L304/L365） |

> **重要な区別（契約整合）**: `<channel source="…">` の**ソースタグ値**は broker で **`org-broker`**（renga は
> `renga-peers`）であり、これが `{{CHANNEL_SRC}}` の render 値。一方 **channel sidecar の MCP サーバー名**は
> `org-broker-channel`（spawn 注入 `--dangerously-load-development-channels server:org-broker-channel`）で、これは
> **別物**。サーバー名は render 面トークンではなく **`{{> spawn-ritual }}` フラグメント内の per-transport リテラル**
> （broker=`org-broker-channel` / renga=`renga-peers`）として持つ。両者を混同して `{{CHANNEL_SRC}}` に
> `org-broker-channel` を render すると、ratified 契約（Surface 2.1 / S3）と既存 skill prose（「channel source は
> `org-broker`」）に矛盾する broker 受信 cue を生成してしまうため、generator は token と sidecar サーバー名を
> 厳密に分離する。

本文の `mcp__renga-peers__send_message` は source 上 `{{FQ}}send_message` と書く。1 パス render で header と
同じ `flag` から展開されるため、両者は**構造的に同一面**になる（自己矛盾不可能）。

**(b) dual-system ブロックのフラグメント注入（4 局所差異）**

dual-system ブロックは「両系を記述する」prose であり「broker XOR renga を出す」分岐ではない。**既定を主・他方を
opt-in fallback として記述する向き**が transport パラメータで決まる。よって 4 差異は**フレーミングごと
canonical フラグメント**として保持する:

| フラグメント | 内容 | SoT |
|---|---|---|
| `{{> dual-system-header-short }}` | §1.1 短縮版（1 行ヘッダ） | promotion-plan §1.1 |
| `{{> dual-system-header-long }}` | §1.2 長尺版（受信モデル / spawn 儀式 / エラー分岐の 3 点） | promotion-plan §1.2 |
| `{{> spawn-ritual }}` | 1 段（renga）/ 2 段（broker）承認フロー | promotion-plan §1.2・spawn-flow |
| `{{> surface-omissions }}` | `new_tab`/`focus_pane` 不在 + attention watcher 非依存注記 | 本設計 §論点 5 |

**promotion-plan §1.1/§1.2 のテンプレ群が、そのままフラグメント SoT になる**（新規に文言を作らず既存
canonical を参照）。generator は `flag` に応じて各フラグメントの broker-default 版 / renga-default 版を選ぶ。

**(c) 既定値リテラルトークン（コード相当 — §論点 4 と連動）**

`org-start` L66 の `echo "${ORG_TRANSPORT:-renga}"` は prose ではなく**既定値リテラル**。これは
`{{DEFAULT_TRANSPORT}}` という**別トークン**にする（`{{FQ}}` 等の render 面トークンとは意味が違う）:

- `{{FQ}}` 等は **render 面（broker）** に追従。
- `{{DEFAULT_TRANSPORT}}` は **`transport.DEFAULT_TRANSPORT`（既定値そのもの）** に追従。

フリップ後はどちらも broker に解決され値が一致するが、**意味的に別物**。混同すると promotion-plan §3 が警告した
「既定フリップで broker 側が恒等 no-op に化ける」級のバグを prose 側に再生産する。トークンを分けることで
構造的に防ぐ。

---

## 2. 論点 2: generator の置き場と既存 seam の再利用

### 2.1 比較

| 軸 | 案 2A: ja `tools/`（推奨） | 案 2B: claude-org-runtime |
|---|---|---|
| 所有境界 | skill prose は ja 固有資産（`.claude/skills/**`・`CLAUDE.md`・`.dispatcher/**`）。runtime は transport 機構 SoT | runtime に ja 固有の skill 内容を持ち込む = 境界侵犯 |
| seam 整合 | 既存 `tools/transport.py` を import して consume（`fq_prefix`/`server_name`/`spawn_inject`/`rewrite_allow_entries`） | runtime 側に ja skill 知識が漏れ、二重 SoT 化 |
| 単一 SoT | ◎ runtime=機構 / ja=内容 / generator=consume の三層が保たれる | ✕ runtime が内容も持つと層が崩れる |
| リリース結合 | ja 内で完結（runtime pin 越境不要） | generator 改修ごとに runtime リリースに律速される |

### 2.2 推奨: 案 2A（ja `tools/`）+ 既存 seam の延伸

新規 generator を **ja `tools/gen_skill_prose.py`**（仮）として置く。transport 事実は一切ハードコードせず、
[`tools/transport.py`](../tools/transport.py) を経由して runtime descriptor を読む。

**再利用と新規の切り分け**:

| 機能 | 実装 | 根拠 |
|---|---|---|
| 接頭辞・サーバー名解決（`{{FQ}}`/`{{SERVER}}`） | **既存 `transport.fq_prefix`/`server_name` をそのまま使う** | transport 事実の単一 SoT |
| skill frontmatter `allowed-tools` の per-transport render | **per-entry 接頭辞リネーム**（`mcp__renga-peers__<tool>`→`mcp__org-broker__<tool>`。`transport.fq_prefix` を各エントリに適用）。**ワイルドカード `mcp__renga-peers__*` は renga source surface で明示展開してから per-tool リネーム**（下記 ※5。broker `*` のまま出さない）。**`rewrite_allow_entries` は使わない**（※3）。リネーム後に各ツールが broker descriptor に存在するか検証し broker 省略ツールは drop（※4） | **skill 固有のサブセット（= source 集合の像）を保存**。broker `*` で broker 固有/将来ツールまで広げない＝per-skill auth-clean（§0.4） |
| `permissions.md`（org-setup）の per-transport 射影 | **`transport.rewrite_allow_entries` を無改修で使う**（broker 解決 = role-tier 置換、settings 層で射影） | ここは **settings の意図が「役割の全 tier 認可」**なので tier 置換が正しい。skill frontmatter（subset 保存）とは**機構が異なる**（※3） |
| 既定値リテラル（`{{DEFAULT_TRANSPORT}}`） | **既存 `transport.DEFAULT_TRANSPORT` を re-export 経由で読む** | 既定値の単一 SoT |
| prose トークン置換 + フラグメント注入 | **新規**（`tools/transport.py` に prose 用ヘルパを足すか、`gen_skill_prose.py` 内に閉じる） | allowlist 用 `rewrite_allow_entries` は flat list 専用で prose に流用不可 |

→ **「拡張すれど分岐させず（extend, don't fork）」**。transport 機構は全て既存 seam から consume し、
generator は「prose テンプレ → render」のオーケストレーションだけを新規に持つ。

> **※1 source 正規化（必須・実装制約）**: `rewrite_allow_entries` は **renga プレフィックスで始まるエントリのみ
> 除去**し、それ以外（`Bash(...)` 等）は順序保存で残す挙動。ところが現状の一部 SKILL.md（例: `org-start`）は
> opt-in 時代の `mcp__org-broker__*` エントリを**既に frontmatter に持つ**。これを無正規化で broker render すると
> 「broker tier を挿入 + 既存 broker wildcard も残存」で**重複・過剰認可**になる。よって source は **renga/template
> 面に正規化**（= renga エントリのみを持ち、target-transport (broker) エントリは事前に strip）してから rewrite に
> 渡す。これは「source は renga 面で著述」という TEMPLATE_TRANSPORT 原則（§0.3）の実装上の必須前提でもある。
> drift CI（§7.2）が「source allowlist に broker プレフィックスが混入していない」ことを assert する。

> **※2 role メタデータ（tier 置換 + ワイルドカード展開で必要）**: `rewrite_allow_entries(entries, role, ...)` は
> **role 必須引数**で broker tier は role により異なる（worker/curator=messaging 4 / dispatcher/secretary=ops tier。
> `spawn_pane` は secretary 限定 等）。role が要るのは 2 ケース: (a) `permissions.md`（settings 素材、tier 置換）、
> (b) **ワイルドカード `*` を持つ skill frontmatter の展開**（※5。source surface を `tools_for_role(role)` で列挙）。
> **明示 per-tool エントリのみの skill は role 不要**（接頭辞リネーム ※3 + descriptor 検証 ※4 で決定的）。role は
> いずれも**列挙にのみ**使い、skill frontmatter で **tier ブロック置換には使わない**（※3 の過剰認可を作らない）。
>
> **※3 skill frontmatter は per-entry リネーム（`rewrite_allow_entries` を使わない理由・auth 上重要）**:
> `rewrite_allow_entries` は **renga ブロックを broker の役割全 tier に置換**する settings 用ロジック。skill
> frontmatter は **skill 固有のサブセット**（例: `dispatcher-handover` は `mcp__renga-peers__send_message` のみ、
> `org-suspend` は `mcp__renga-peers__*` ワイルドカード）を持つため、これに `rewrite_allow_entries` を当てると
> 「`send_message` 1 個だけ欲しいスキルに secretary の pane/spawn 全 tier が付く」= **per-skill 認可の過剰拡大**に
> なる（auth-clean を破る）。よって skill frontmatter は **各エントリの server 接頭辞だけを broker へリネーム**
> （`mcp__renga-peers__<tool>` → `mcp__org-broker__<tool>`、`*` は `*`）し、**ツール集合は不変**に保つ。
> 実装は `transport.fq_prefix` の per-entry 適用（`{{FQ}}` トークンと同じ機構）で、新しい SoT を作らない。
>
> **※4 broker 省略ツールの検証（descriptor gate）**: per-entry リネーム後、各ツールが broker descriptor surface に
> 存在するか検証する（既存 `surface().tools_for_role` / `send_message_call` の存在検証と同型）。broker が意図的に
> 省くツール（`new_tab`/`focus_pane`）を名指しする renga エントリがあれば、リネーム先が存在しないため **drop し
> drift ログに記録**（silent に dangling 認可を作らない）。
>
> **※5 ワイルドカード展開（auth 上重要・subset 保存の要）**: `mcp__renga-peers__*` を **broker `mcp__org-broker__*`
> へ直訳してはならない**。broker descriptor には **broker 固有ツール（`spawn_codex_pane` 等）や将来追加ツール**が
> あり得るため、broker `*` は source（renga）が認可していた集合より**広く認可してしまう**（subset 保存を破る）。
> よって `*` は **renga source surface（`surface("renga").tools_for_role(role)` 等の descriptor 由来集合）で
> 明示展開**し、各ツールを per-tool リネーム + ※4 の descriptor 検証にかけ、**broker 側は「source 集合の像」の
> 明示リスト**として出力する（broker `*` は出さない）。これにより (a) broker 固有/将来ツールが skill に漏れ込まず、
> (b) ※4 の省略ツール drop が per-tool で効く。drift CI（§7.2）が「生成 frontmatter に broker ワイルドカードが
> 無い」かつ「broker 集合 ⊆ source 集合の像」を assert する。

---

## 3. 論点 3: ロード機構（commit 時生成 + drift CI vs org-start 時生成）

### 3.1 制約: Claude Code は SKILL.md をディスクから常時ロードする

skill は org-start の時だけでなく **Claude Code セッション全体でディスク上の実ファイルとしてロードされる**。
よって rendered SKILL.md は**常に on-disk の実ファイルとして存在しなければならない**。org-start 時オンメモリ
生成は不可（skill 解決機構がファイルを要求する）。

### 3.2 比較

| 軸 | 案 3A: commit 時生成 + drift CI で byte 固定（推奨） | 案 3B: org-start 時生成 |
|---|---|---|
| 前例 | settings.local.json と同じ（テンプレ → 生成物を commit、drift CI で再生成 diff） | 前例なし |
| ディスク存在 | ◎ rendered SKILL.md が常に repo に存在（Claude Code が常時ロード可能） | ✕ org-start 前 / 非起動時に skill が無い or stale |
| レビュー可能性 | ◎ rendered diff が PR に出る（人間が読める） | ✕ 生成物が repo に無く diff が見えない |
| offline / 起動非依存 | ◎ 生成は CI/commit 時のみ。実行時依存ゼロ | ✕ org-start に runtime 依存が増える |
| drift 検出 | ◎ CI が `render(source) == committed` を assert | △ 起動毎に再生成するので drift 概念が無い代わりに監査痕跡も無い |

### 3.3 推奨: 案 3A（commit 時生成 + drift CI、単一 render 面を byte 固定）

settings.local.json の前例どおり **source + 生成物を両方 commit し、drift CI で `render(source) == committed`
を byte で固定**する。

**重要な単純化 — on-disk は 1 面だけでよい**:

dual-system ブロックは**両系を記述する prose** なので、render 面が broker でも renga は「opt-in fallback」と
して本文中に併記されている。worker が `ORG_TRANSPORT=renga` で動いても、ヘッダの機械置換指示
（「renga 時は `{{FQ}}` を `mcp__renga-peers__` と読み替え」）が現行どおり機能する。**これは現行モデルと同型**で、
literal 面が renga→broker に替わるだけ。よって:

- **git に commit する rendered SKILL.md は `DEFAULT_TRANSPORT`（= broker）面の 1 枚だけ**（本文・frontmatter とも
  broker）。renga 面を git に二重 commit しない。本文の dual-system ブロックは両系を記述するので、broker 面の本文は
  renga 運用時も読解可能（renga ユーザーは併記の機械置換指示で読み替え）。
- **frontmatter `allowed-tools` は本文と同じ broker 面で render**（renga ツールは broker 面に出さない、§0.4）。
  これにより broker 既定でツール認可が本文と一致し、renga 面の auth 迂回も生じない。
- **rollback は再生成**: `ORG_TRANSPORT=renga` に倒すと org-setup が generator を renga で再走し、frontmatter・
  settings・本文すべてが byte 安定な renga 面に戻る（settings.local.json と同型の per-transport 再生成。renga 恒等
  基底で byte 安定）。git-committed 面は broker 1 枚、rollback 時の renga 面は再生成で得る（transient）。

> **根源的トレードオフ（#9 の本質・人間判断が要る）**: 「broker auth 整合（broker 既定で renga 面を出さない）」と
> 「**再生成なし**の env-only 即時切戻し（`ORG_TRANSPORT=renga` だけで renga ツールが即認可）」は、単一の静的
> committed frontmatter では**同時に満たせない**（Claude Code は `allowed-tools` を実行時 transport で条件分岐
> できない）。選べるのは 2 モデル:
>
> - **モデル 1（per-transport + 再生成。本設計の推奨）**: frontmatter も settings.local.json も broker 単一面。
>   切戻しは `ORG_TRANSPORT=renga` + **org-setup 再生成**。auth クリーン（迂回なし）。**この再生成は settings.
>   local.json が既に取っている per-transport 生成と同型**で新規負担を足さない。promotion-plan §5.6 の「コード変更
>   なし」は満たす（再生成 ≠ コード変更）が、§5.6 の「**再生成なし**」の語感は「env flip + org-setup 再生成」へ
>   読み替える必要がある。
> - **モデル 2（両層 union）**: frontmatter も settings.local.json も両系認可。env-only 切戻しが真に再生成なしで
>   成立するが、**broker 既定セッションで renga 面（`focus_pane`/`new_tab` 等の broker 省略ツール）が認可されたまま
>   ＝ broker auth 迂回**を受容することになる。
>
> **不変条件**: skill frontmatter の auth モードは **settings.local.json の auth モードと必ず一致**させる（片方
> per-transport・片方 union は不整合）。本設計はモデル 1（auth クリーン・既存挙動整合）を推奨するが、env-only
> 切戻しを契約上譲れない場合はモデル 2 になる。**この二択が §9.2 #9 の再 ratify 対象**であり、settings.local.json
> 側の現行 auth モード確認とセットで人間が確定する。なお**この緊張は skill frontmatter 固有ではなく、settings.
> local.json の per-transport 生成（promotion-plan §3）と §5.6「再生成なし切戻し」の間に既に内在**しており、prose
> generator はそれを新たに作るのではなく継承・顕在化させているだけ。

- `org-setup/references/permissions.md` は別経路の **`rewrite_allow_entries`（role-tier 置換）**（§4.2(2)。skill
  frontmatter の per-entry リネームとは機構が違う。混同すると過剰認可）。

drift CI（§7.2）が「broker 面の本文 + broker 面の frontmatter（== per-entry リネーム結果・skill 固有サブセット
保存）」を assert し、さらに「renga 再生成が byte 安定（恒等）」を回帰固定する。

---

## 4. 論点 4: surgical 最難ファイルの個別ハンドリング

pivot 入力メモが挙げた 4 つの最難ファイルは、トークン展開だけでは表現できない。generator は **source manifest
で各ファイルの処理モードを宣言**し、surgical ファイルを token-body パイプラインから除外する。

### 4.1 処理モード分類（manifest）

各 manifest エントリは **`mode`（処理モード）+ `allowlist`（source 正規化フラグ、§2.2 ※1）** を持つ。
**`role` は次の 2 ケースで必須**: (a) `identity-anchor`（permissions.md の role-tier 置換、§2.2 ※2）、
(b) **source frontmatter にワイルドカード `mcp__renga-peers__*` を含む skill**（§2.2 ※5 のワイルドカード展開が
`surface("renga").tools_for_role(role)` で source surface を列挙するため role が要る。broker tier は role 差あり=
`spawn_pane` は secretary 限定 等）。**明示 per-tool エントリのみの skill は role 不要**（接頭辞リネーム + descriptor
検証だけで決定的）。role は**列挙にのみ使い tier ブロック置換には使わない**ので、過剰認可（§2.2 ※3）は起きない。

| モード | 意味 | 対象例 |
|---|---|---|
| `template` | 全文をトークン + フラグメントで render | 短縮/長尺の標準スキル（promotion-plan §2 の大半） |
| `template+fragment` | 本文トークン + 個別 surgical 領域をマーカー区画で注入 | org-delegate / pane-layout / org-pull-request |
| `surgical-fragment` | token-body から除外。per-transport フラグメントを手当て、generator は領域注入のみ | renga-error-codes |
| `code-literal` | 既定値リテラルを `{{DEFAULT_TRANSPORT}}` で render（render 面トークンと区別） | org-start L66 echo |
| `identity-anchor` | render 対象外。renga byte 不変を構造保証（rewrite_allow_entries 恒等） | org-setup permissions.md |

**`role` フィールドの適用範囲**（前掲と同じ・再掲）: role が必須なのは (a) `identity-anchor`（permissions.md の
`rewrite_allow_entries` role-tier 置換）と (b) **ワイルドカード `mcp__renga-peers__*` を持つ skill**（§2.2 ※5 の
展開で `tools_for_role(role)` が必要）。**明示 per-tool エントリのみの skill は role 不要**（接頭辞リネーム §2.2 ※3
＋ descriptor 検証 ※4 で決定的）。role は (a)(b) いずれも**列挙・tier 解決にのみ使い**、skill frontmatter で tier
ブロック置換には使わない（過剰認可 ※3 を作らない）。**manifest 各エントリは最低限 `mode` + `allowlist`（§2.2 ※1）
を持ち、`role` は上記 (a)(b) で必須**。

### 4.2 4 最難ファイルの個別設計

**(1) `references/renga-error-codes.md`（renga=正典 / broker=加算の非対称）**

renga が正典、broker は**加算**という非対称構造はプレフィックス置換で表現できない。
- モード = `surgical-fragment`。token-body パイプラインから除外。
- 構造を「**shared codes（両系共通）+ per-transport 拡張表**」に再編する。broker 拡張コード
  （`[token_invalid]` 等）は generator が**末尾の拡張表として append**、renga 固有は据え置き。
- **ファイル名そのものが renga アンカー**。リネーム（`renga-error-codes` → `transport-error-codes`）は
  prose 整合と参照リンク更新を伴う。**§9.2 #2 で「リネームせず内容を非対称フラグメント化」が ratified 確定**
  （2026-06-16）。リネームは行わない。

**(2) `org-setup/SKILL.md` + `references/permissions.md`（byte 等価アンカーの向き）**

permissions の byte 等価アンカーは **renga 側固定**（promotion-plan §3.2）。permissions.md は skill frontmatter
ではなく、org-setup が settings.local.json を生成する**素材テンプレ**で、broker 射影は `rewrite_allow_entries` の
**ツール（settings 生成）層**で起きる。
- permissions.md = モード `identity-anchor`。generator は素材テンプレを**触らない**。`rewrite_allow_entries` の
  renga 恒等が「素材テンプレは renga byte 不変・broker 射影は settings 生成層」を構造保証（明示 renga 解決 =
  `return list(entries)`、broker 解決 = 置換）。
- org-setup/SKILL.md の dual-system **prose** は `template`/`template+fragment` で render。SKILL.md 自身の
  frontmatter `allowed-tools` は他スキル同様 **per-transport render（broker 面、§0.4）**。
- **skill frontmatter と permissions.md は機構が異なる**（§2.2 ※3）: skill frontmatter は **per-entry 接頭辞
  リネーム**（skill 固有サブセット保存）、permissions.md は **`rewrite_allow_entries`（role-tier 置換）**。共通点は
  「renga が恒等基底で rollback byte 安定」（§0.3）だけ。org-setup/SKILL.md 自身の frontmatter は他スキル同様
  per-entry リネーム、その参照する permissions.md（素材テンプレ）だけが tier 置換・identity-anchor。

**(3) `org-start/SKILL.md` L66 `echo "${ORG_TRANSPORT:-renga}"`（コード相当の既定値）**

これは prose ではなく**シェル既定値リテラル**で、`DEFAULT_TRANSPORT` に一致しなければならない。
- 該当行 = モード `code-literal`。source 上 `echo "${ORG_TRANSPORT:-{{DEFAULT_TRANSPORT}}}"` と書く。
- `{{DEFAULT_TRANSPORT}}` は `transport.DEFAULT_TRANSPORT` に解決（render 面トークンと別系統）。
- **初回フリップ（Phase 2）の L66 は手動のまま**（promotion-plan PR-2 スコープを踏襲）。generator は Phase 3'
  （Phase 2 merge 後）に初めて存在するため、Phase 2 時点では `{{DEFAULT_TRANSPORT}}` トークンを render できない。
  よって初回の `:-renga`→`:-broker` は PR-2 で手動反転する（§8.1 の Phase 2 行と整合）。
- **Phase 3'（G3）で org-start を source 化した後**、L66 を `echo "${ORG_TRANSPORT:-{{DEFAULT_TRANSPORT}}}"`
  へトークン化する。これは既に broker 化済みの値を再導出するだけ（挙動不変）だが、**以降の transport 変更で
  L66 の手動編集が不要**になる（将来のフリップに generator が自動追従）。トークン化の効果は「初回フリップの
  自動化」ではなく「**2 回目以降のフリップの手動編集排除**」である。
- L66 周辺の説明 prose（「`renga`（無設定を含む既定）」等）は Phase 3' の `template` 面で render され同期する。

**(4) `.dispatcher/references/spawn-flow.md`（2 段承認フロー）**

spawn 儀式の SoT。renga 1 段（dev-channel `server:renga-peers` を Enter 承認）vs broker 2 段
（folder-trust + dev-channel `server:org-broker-channel` sidecar 承認）。
- spawn 儀式記述 = `{{> spawn-ritual }}` フラグメントで注入（§1.3(b)）。broker 面では 2 段、renga 面では 1 段。
- フラグメントの broker 版に **3-2 / 3-3b の 2 段承認手順**を canonical に保持（promotion-plan §1.2 注記準拠）。
- 散在する `send_keys` 両系記述・transport 送信記述は `{{FQ}}` トークンで本文 render。
- **attention watcher は 2 段承認の対象外**注記を `{{> surface-omissions }}` で必ず併記（§論点 5）。

---

## 5. 論点 5: 保持すべき不変条件と source/generator の保証方法

不変条件は**レビューアの注意力**ではなく**source/フラグメント構造 + drift CI の assert**で構造保証する。

| 不変条件 | source/generator の保証機構 |
|---|---|
| frontmatter `allowed-tools` の renga byte 安定（rollback 再生成が byte 等価、§3.2 の正しい解釈、§0.4） | **skill frontmatter = per-entry 接頭辞リネーム（skill 固有サブセット保存、※3）**。committed は broker 面（本文と一致・auth 迂回なし・過剰拡大なし）。`ORG_TRANSPORT=renga` 再生成は接頭辞を renga へ戻すだけで byte 等価（rollback byte 安定）。drift CI が「broker 面 == per-entry リネーム結果」かつ「renga 再生成 == 恒等」を assert。**`permissions.md` のみ `rewrite_allow_entries`（role-tier 置換）**で機構が別（※3） |
| renga-fallback 固有参照（`server:renga-peers` / `<channel source="renga-peers">` / 切戻し手順） | dual-system フラグメントは**両系記述**が構造的前提。renga 側記述はフラグメントの構成要素なので render 面が broker でも**必ず残る**。drift CI が「各 dual-system ブロックに renga fallback 文字列が存在」を assert |
| `new_tab` / `focus_pane` の broker surface 不在注記（意図的除外） | `{{> surface-omissions }}` フラグメントに固定。transport 非依存（両面で同一出力）。drift CI が dispatcher 系ファイルに当該注記の存在を assert |
| attention watcher の transport 非依存（2 段承認の対象外） | 同じく `{{> surface-omissions }}` の neutral 注記。**spawn-ritual（2 段）フラグメントには絶対に混入させない**分離を manifest で強制。drift CI が「watcher 注記が spawn-ritual 区画の外」を assert |

**保証の核**: 不変条件を「**generator が壊せない構造**（恒等パス・両系フラグメント・neutral 固定区画）」として
encode し、加えて drift CI で**機械 assert** する二重化。人間レビューは最終確認であって一次防衛線ではない。

---

## 6. 論点 6: 既存 transport seam との接続（二重 SoT を作らない）

三層の SoT 分離を厳守する:

```
[runtime] claude_org_runtime.transport        ← transport 機構の単一 SoT
   │  (DEFAULT_TRANSPORT / fq_prefix / server / spawn_inject / allowlist)
   ▼  pin で consume（ハードコードしない）
[ja seam] tools/transport.py                   ← ja の単一アクセサ
   │  (resolve / fq_prefix / server_name / spawn_inject / rewrite_allow_entries / TEMPLATE_TRANSPORT)
   ▼  import で consume
[ja gen]  tools/gen_skill_prose.py (新規)      ← prose render のオーケストレーションのみ
   │  + フラグメント SoT = promotion-plan §1.1/§1.2 テンプレ
   ▼  render
[出力]    .claude/skills/**/SKILL.md ほか      ← commit + drift CI で byte 固定
```

**二重 SoT を作らないための規律**:

- generator は transport 事実（server 名・プレフィックス・既定値）を**一切定義しない**。全て
  `tools/transport.py` 経由で runtime から consume。
- **spawn flag は部分的に seam・残りはフラグメント所有（明示）**: 現状の `transport.spawn_inject` は broker で
  **`--mcp-config <broker>` のみ**を返す（renga は `--dangerously-load-development-channels server:renga-peers`）。
  しかし broker 儀式は push 一次のため **`--dangerously-load-development-channels server:org-broker-channel`
  （channel sidecar）+ 2 段目の dev-channel 承認**も要る（§4.2(4)）。この **sidecar 注入 flag と 2 段承認は
  `{{> spawn-ritual }}` フラグメントが所有**する（per-transport の divergent 領域、§1.3(b)）。これは二重 SoT に
  ならない: `spawn_inject` が返すのは **daemon の mcp-config flag（transport 機構の事実）**、フラグメントが持つのは
  **push-primary 配送をどう立てるかの prose（spawn-flow §3-3b / promotion-plan §1.2 が文言 SoT）**で、層が違う。
  sidecar サーバー名 `org-broker-channel` は §1.3 で確立した broker 固有リテラル（`{{CHANNEL_SRC}}` とは別）として
  フラグメント内に持つ。**推奨（実装フェーズの follow-up）**: 将来 runtime `spawn_inject` を「broker の全 spawn flag
  集合（mcp-config + sidecar）」を返すよう拡張できれば seam が単一 SoT に寄り、フラグメントは承認手順 prose のみを
  持てばよくなる（runtime 越境のため別 Issue。§9.2 の実装 follow-up 候補）。
- skill frontmatter は **per-entry 接頭辞リネーム**（`transport.fq_prefix` の per-entry 適用、skill 固有サブセット
  保存、※3）。`permissions.md` のみ **`rewrite_allow_entries`（role-tier 置換）**。どちらも transport 接頭辞・
  tier の SoT は runtime descriptor（`transport.py` 経由）から consume し、新しい union モードや transport 事実の
  二重定義は作らない。
- フラグメント文言は promotion-plan §1.1/§1.2 を**参照**（コピーして第 2 の文言 SoT を作らない。フラグメント
  ファイルは §1.1/§1.2 から導出した単一コピーとし、promotion-plan 側を「文言の設計 SoT」として明記）。
- settings generator（runtime）との関係: settings は allowlist を所有、prose generator は prose を所有。
  **allowlist 生成には 2 つの別経路がある（混同してはならない）**:
  - **skill frontmatter（prose generator）= per-entry 接頭辞リネーム**（`transport.fq_prefix` を各エントリに適用、
    skill 固有サブセット保存、※3）。**`rewrite_allow_entries` は使わない**（role-tier 拡大による過剰認可を避けるため）。
  - **`permissions.md` / settings.local.json（org-setup）= `rewrite_allow_entries`（role-tier 置換）**。ここは
    「役割の全 tier 認可」が意図なので tier 置換が正しい。
  - 二重 SoT にならないのは、**両経路とも transport 接頭辞・tier・server 名の事実を runtime descriptor
    （`transport.py` 経由）から consume**し、generator 側で transport 事実を定義しないから。「1 関数に集約」では
    なく「2 経路だが事実 SoT は 1 つ（descriptor）」が正しい不変条件。

---

## 7. 論点 7: 25 スキルの移行計画（バッチ分割）と drift CI 設計

### 7.1 移行バッチ（promotion-plan §5.3 の PR-3a/3b/3c を本方式へ再スコープ）

「手動反転」を「source 化 + 生成」へ置換する。対象は skill-family 35 md ファイル（SKILL.md 19 + references 16）
+ `CLAUDE.md` + `.dispatcher/**` prose（promotion-plan §2 inventory 33 件から contract と据え置き設計 docs を除く）。

| バッチ | スコープ | 処理モード | 依存 | 検証 |
|---|---|---|---|---|
| **G0** | generator + フラグメント SoT + manifest スキーマの新設（生成物 0 件、ツールのみ） | — | runtime pin（既定 broker） | unit test（render 面 broker / renga 両方を golden 固定） |
| **G1** | 標準スキル（`template` のみ — 短縮/長尺で surgical なし）を source 化 + 生成 | `template` | G0 | drift CI green、生成 diff レビュー |
| **G2** | 本文 surgical 併存（org-delegate / pane-layout / org-pull-request 等）を source 化 | `template+fragment` | G1 | drift CI green、surgical 区画の手動精査 |
| **G3** | 最難 4 ファイル（renga-error-codes / org-setup / org-start / spawn-flow）を個別設計（§4.2）で source 化 | `surgical-fragment` / `code-literal` / `identity-anchor` | G2 | drift CI green + 各不変条件の専用 assert（§5） |
| **G4** | `CLAUDE.md` / `.dispatcher/CLAUDE.md` の正本（見出し短縮 + 本文長尺の二段） | `template+fragment` | G3 | drift CI green、正本ゆえ慎重レビュー |

**据え置き判定（generator 対象外）**: promotion-plan §2.1 の「据え置き有力」「設計文書（end-state 既述）」
（README 公開文書・renga-decoupling・transport-switch-ux・non-goals・attention-* 等）は **token-body 化しない**。
これらは generator スコープ外として manifest の `exclude` に列挙し、必要なら 1 文の時点注記を手動で足す
（promotion-plan §2.1 の方針を踏襲）。

### 7.2 drift CI 設計

settings.local.json の drift 前例に倣う `tools/check_skill_drift.py`（仮）を新設し CI ジョブ化:

1. **再生成 diff**: manifest の**全生成モード**（`template` / `template+fragment` / `surgical-fragment` /
   `code-literal`）を `render(source, flag=DEFAULT_TRANSPORT)` し、committed 生成物と **byte 比較**。差分あれば
   fail（「source を直して再生成せよ」を案内）。**除外は真の非生成アンカーのみ**: `identity-anchor`
   （`permissions.md` = renga byte 不変）と manifest `exclude`（据え置き設計 docs）。`surgical-fragment`
   （renga-error-codes）と `code-literal`（org-start L66）は**生成物なので必ず byte 比較に含める**（生成モードを
   drift から漏らさない）。
2. **frontmatter per-entry リネーム assert（§2.2 ※3/※4/※5）**: 各 skill 生成物の frontmatter `allowed-tools` が
   (i) committed の broker 面で **source の各エントリの server 接頭辞だけを broker へリネームした結果と byte 一致**
   （ツール集合が source 集合の像と同一＝過剰拡大なし・auth 迂回なし）、(ii) broker 省略ツールが drop され drift
   ログに記録、(iii) **renga 再生成が byte 安定**（接頭辞を renga へ戻すだけ＝恒等）、(iv) **生成 frontmatter に
   broker ワイルドカード（`mcp__org-broker__*`）が無く、broker 集合 ⊆ source 集合の像**であること（※5 のワイルド
   カード展開が効いている）。**role-tier への拡大が起きていない**（例: `send_message` のみのスキルに ops tier が
   付いていない）ことを明示 assert。
2b. **source 正規化 assert（§2.2 ※1）**: 各 source の allowlist に **broker プレフィックス（`mcp__org-broker__`）が
   混入していない**こと（source は renga/template 面で著述。混入は opt-in 時代の残骸で過剰認可の元）。
2c. **role assert（§2.2 ※2）**: role を要する全エントリ — `identity-anchor`（permissions.md）と **ワイルドカードを
   持つ skill** — に `role` が存在し有効集合（worker/curator/dispatcher/secretary）であること。明示 per-tool のみの
   skill は role 不在でも可（リネーム + descriptor 検証で決定的）。
2d. **permissions.md identity assert**: 素材テンプレが renga 恒等であること（§0.4 / §5）。
3. **不変条件 assert**（§5 表の機械 assert）:
   - 各 dual-system ブロックに renga fallback 文字列（`renga-peers` / `<channel source="renga-peers"` /
     切戻し手順マーカー）が存在。
   - dispatcher 系に `new_tab`/`focus_pane` 不在注記が存在。
   - attention watcher 非依存注記が spawn-ritual 区画の**外**にある。
4. **両面 render 健全性**: `flag=renga` でも render が例外なく成立すること（opt-in 切戻し時の prose 健全性回帰）。
5. **manifest 網羅**: promotion-plan §2 inventory の対象ファイルが manifest に漏れなく登録されているか
   （`exclude` 明示分を除き取りこぼし 0 を assert）。

CI は既存の markdown-conventions 検証スクリプト（CLAUDE.md「ドキュメント表記」）と同列のジョブとして追加。

---

## 8. 論点 8: Epic #586 への影響（Phase 再スコープ・contract への影響）

### 8.1 Phase の再スコープ

本方式は promotion-plan の **Phase 3（prose sweep）の機構だけを置換**する。Phase 1（contract amendment）と
Phase 2（実体フリップ）は**不変**:

| Phase | promotion-plan（案 a） | 本設計（案 b）での扱い |
|---|---|---|
| Phase 1: contract amendment | 既定反転 amendment 節を追記 | **不変**。generator は contract surface ではない |
| Phase 2: 実体フリップ + テスト | runtime pin bump + test 反転 + **org-start L66 手動反転** | **不変**（generator は Phase 3' でしか存在しないため、初回フリップの L66 は PR-2 で手動反転のまま）。runtime pin・test 反転も不変。**L66 のトークン化は Phase 3'（G3）で行い、2 回目以降のフリップの手動編集を排除する**（§4.2(3)） |
| Phase 3: prose 33 ファイル**手動**反転（PR-3a/3b/3c） | → **Phase 3' に再スコープ**: generator + フラグメント SoT 新設 → 1 回の機械生成 → drift CI（§7 の G0〜G4） | 手動 33 ファイル反転 PR が「source 化 + 生成」へ置換 |
| Phase 4: rollback runbook + 後始末 | tools docstring 反転ほか | **概ね不変**。`tools/*.py` docstring は **Phase 4 で手動反転・generator 対象外**で確定（§9.2 #3 ratified） |

**依存順は維持**: contract-before-code → behavior-before-doc。generator（Phase 3'）は実体フリップ（Phase 2）
merge 済みを依存先に持つ（render 面 = `DEFAULT_TRANSPORT` = broker が成立している前提で生成）。

### 8.2 contract への影響

- **新たな contract surface は生じない**。generator は実装機構であって normative な transport 契約面ではない。
- promotion-plan §4 の「既定フリップ amendment」は**そのまま必要**（既定値の契約宣言の反転は generator とは独立）。
- 任意で contract の Decision rationale digest に「prose は手動両系維持から機械生成へ移行（drift CI で
  byte 固定）」の 1 文を追記しうるが、**normative ではない**ため必須ではない（**§9.2 #7 で「任意」が ratified
  確定**、2026-06-16）。

### 8.3 案 a 対比の正味の差分

- **消えるもの**: 33 ファイル手動反転の自己矛盾リスク（pivot の主動機）、prose 反転の漏れ、**2 回目以降の
  transport 変更での L66 手動編集**（初回フリップの L66 は Phase 2 で手動のまま。トークン化は Phase 3'）。
- **増えるもの**: generator + フラグメント SoT + manifest + drift CI ジョブ + 各スキルの source ファイル
  （生成物との二重保持。settings.local.json と同じトレードオフ）。
- **収支**: 33 ファイル × 将来の transport 変更ごとの手動反転コストを、1 つの generator + CI に一元化。再現性・
  監査性・自己矛盾耐性を得る代わりに、初期の source 整備コストと生成物二重保持を負う。**両系を将来も維持する
  Epic #586 の前提下では案 b が優位**（手動両系は変更のたびに 33 ファイルの自己矛盾リスクを再生産する）。

---

## 9. 推奨案サマリと open decisions（#1-#8 = 2026-06-16 ratified / **#9 = 再 ratify 待ち**）

### 9.1 推奨案（一括）

| 論点 | 推奨 |
|---|---|
| 1. 中立著述形式 | **案 1B**: 本文は中立トークン（`{{FQ}}`/`{{SERVER}}`/`{{DEFAULT_TRANSPORT}}`）、4 局所差異は名前付きフラグメント（`{{> dual-system-header-* }}` 等）。条件分岐を本文から追放 |
| 2. generator 置き場 | **案 2A**: ja `tools/gen_skill_prose.py`。transport 事実は既存 `tools/transport.py` 経由で consume。skill frontmatter は **per-entry 接頭辞リネーム**（subset 保存、※3）、`permissions.md` のみ **`rewrite_allow_entries`（role-tier）**（§2.2 / §0.4） |
| 3. ロード機構 | **案 3A**: commit 時生成 + drift CI で byte 固定（settings.local.json 前例）。on-disk は broker 面 1 枚 |
| 4. surgical 最難 4 ファイル | manifest 処理モード（`surgical-fragment`/`code-literal`/`identity-anchor`）で個別ハンドリング（§4.2） |
| 5. 不変条件保証 | skill frontmatter = per-transport render（broker 面、auth 迂回なし）/ rollback は再生成で renga byte 安定 / 両系フラグメント + neutral 固定区画で構造保証 + drift CI 機械 assert（§0.4 / §5） |
| 6. seam 接続 | runtime=機構 / ja seam / generator=consume の三層。allowlist は 2 経路（skill frontmatter=per-entry リネーム ※3 / permissions.md=`rewrite_allow_entries` role-tier）だが transport 事実 SoT は descriptor 1 つ（§6） |
| 7. 移行 + drift CI | G0〜G4 バッチ（§7.1）+ `tools/check_skill_drift.py`（§7.2） |
| 8. Epic #586 影響 | Phase 3 のみ案 b へ再スコープ。Phase 1/2 不変。contract に新 surface なし |

### 9.2 open decisions（**#1-#8 Ratified 2026-06-16 / #9 再 ratify 待ち**）

> **Ratified 2026-06-16**: 下表 **#1-#8 を本設計の推奨どおり承認**。**#9 のみ、ratify 後の Codex auth レビューで
> 欠陥が判明したため再 ratify 待ち**（下記 #9 参照）。#1-#8 の確定値（各 # の「本設計の推奨」列を採用）:
> - #1 = **隣接 source ファイル**（`SKILL.md.in` 等）+ rendered を両 commit。
> - #2 = **`renga-error-codes.md` はリネームせず**、非対称フラグメント化（§4.2(1)）。
> - #3 = **`tools/*.py` docstring は手動反転**（Phase 4）、**generator 対象外**。
> - #4 = 据え置き設計 docs は **generator `exclude`** + 必要時 1 文の時点注記のみ。
> - #5 = フラグメント SoT は **promotion-plan §1.1/§1.2 を文言設計 SoT**、フラグメントファイルは単一導出コピー。
> - #6 = generator（**Phase 3'**）は **実体フリップ（Phase 2）merge 済みを依存先**にする。
> - #7 = contract digest への 1 文追記は **任意**（normative でない）。
> - #8 = pivot 入力メモを **本ブランチに commit**（本最終化で実施済み・provenance）。
> - #9 = **【再 ratify 待ち — 注意】** 2026-06-16 ratify 時点の値は **transport-union** だったが、その後の
>   Codex auth レビューで **「broker auth 整合」と「再生成なし env-only 切戻し」が単一の静的 frontmatter では
>   両立しない根源的トレードオフ**が判明（§0.4）。二択 = **モデル 1（per-transport + 再生成、auth クリーン・推奨）**
>   / **モデル 2（両層 union、env-only 切戻し可だが broker auth 迂回を受容）**。本設計はモデル 1 を推奨に更新済みだが、
>   **ratify 済み値（union）からの変更 + 契約 §5.6 の語感調整を伴うため人間の再 ratify を要する**。§0.4 / 本表 #9 行参照。
>
> **実装フェーズ（Phase 3'）は #1-#8 の確定値を前提とする。#9 のみ再 ratify 待ち**（per-transport 訂正案）。
> #9 以外の表は「確定済み決定の記録」として読む。

| # | 論点 | 確定値（本設計の推奨 = ratified） | 人間判断が要った理由（記録） |
|---|---|---|---|
| 1 | **source と生成物の保持形態** | 隣接 source ファイル（`SKILL.md.in` 等）+ rendered `SKILL.md` を両 commit | 生成物二重保持 vs マーカー区画方式（単一ファイル内 `BEGIN/END GENERATED`）のトレードオフ。ほぼ同一の 2 ファイルを持つ重複コストの許容可否 |
| 2 | **`renga-error-codes.md` のリネーム** | リネームせず非対称フラグメント化（§4.2(1)） | `transport-error-codes` 等へのリネームは参照リンク全更新を伴う。ファイル名 = renga アンカーを残すか否かは設計時系列の判断 |
| 3 | **`tools/*.py` docstring を generator 対象に含めるか** | Phase 4 で手動反転（promotion-plan §3.2 踏襲）、generator 対象外 | コード docstring を prose generator に載せると Python source が token 化され可読性が落ちる。手動 vs 生成の線引き |
| 4 | **据え置き設計 docs の最終確定** | generator `exclude`、必要時 1 文の時点注記のみ（§7.1） | README 公開文書 / renga-decoupling / transport-switch-ux 等の end-state 記述を機械生成に巻き込まない判断（promotion-plan §2.1 と整合）の最終確認 |
| 5 | **フラグメント SoT の正本所在** | promotion-plan §1.1/§1.2 を文言設計 SoT とし、フラグメントファイルはその単一導出コピー | 「設計 SoT（promotion-plan）」と「実行 SoT（フラグメントファイル）」の二者間 drift をどう防ぐか（フラグメント自体を promotion-plan から生成するかは過剰設計の懸念） |
| 6 | **Phase 2 との merge 順序** | generator（Phase 3'）は実体フリップ merge 済みを依存先 | render 面 = broker を前提に生成するため。Phase 2 未 merge 段階で source 化だけ先行するか（renga 面で暫定生成）の運用判断 |
| 7 | **contract digest への 1 文追記** | 任意（normative でない） | prose 機構の移行を契約 digest に残すかは ratify 者の判断 |
| 8 | **pivot 入力メモ（`broker-skill-gen-design-input.md`）の commit 可否** | **commit 確定**（監査証跡として provenance を残す）。本最終化で本ブランチに追加済み | 「成果物 1 件のみ」制約との兼ね合いで別ファイル追加の可否を人間が判断 → 承認 |
| 9 | **skill frontmatter `allowed-tools` の auth モデル（broker auth 整合 vs env-only 切戻し）** | **【再 ratify 待ち】** ratify 時の値=union → Codex auth レビューで根源的トレードオフ判明（§0.4）。二択: **モデル 1=per-transport + 再生成（推奨。auth クリーン・既存挙動整合・「コード変更なし」は満たすが再生成は要る）** / **モデル 2=両層 union（env-only 再生成なし切戻しが成立するが broker auth 迂回を受容）**。skill frontmatter は settings.local.json の auth モードと一致必須 | 単一の静的 frontmatter では「broker 面で renga を出さない」と「再生成なし env-only 切戻し」を同時に満たせない（Claude Code は allowed-tools を実行時 transport で条件分岐不可）。**この緊張は settings.local.json の per-transport 生成（§3）と §5.6 に既に内在**。決定は settings 側現行 auth モードの確認とセット。**ratify 済み値（union）からの変更 + 契約 §5.6 の語感調整を伴うため人間の再 ratify が要る** |

### 9.3 実装着手の前提ゲート（厳守）

- 本設計は **計画 / 設計のみ**。実装・スキル生成・既存ファイル反転・generator 実装は人間 ratification ゲート
  通過後の別フェーズ（Phase 3'）。
- push / `gh pr create` / merge は全フェーズで窓口の人間承認後に限る（subagent / worker の自動 push 禁止）。
- 案 b は promotion-plan（案 a）を**破棄しない**。Phase 1/2/4 は promotion-plan を一次参照とし、Phase 3 の機構
  だけを本設計で置換する。両ドキュメントは相補的。

---

## 参照

- 計画 SoT: [`notes/broker-promotion-plan-586.md`](./broker-promotion-plan-586.md)
  （§1 テンプレ = フラグメント SoT / §2 inventory = 移行対象 / §3 既定値フリップ / §4 contract / §5 Phase 依存）
- pivot 入力（provenance）: [`notes/broker-skill-gen-design-input.md`](./broker-skill-gen-design-input.md)
  — 実体は本設計 §0〜§5 に取り込み済み（self-contained）。監査証跡として commit 済み（§9.2 #8 ratified）
- 既存 seam: [`tools/transport.py`](../tools/transport.py)
  （`rewrite_allow_entries` 恒等パターン / `TEMPLATE_TRANSPORT` / `fq_prefix` / `DEFAULT_TRANSPORT`）
- contract SoT: [`docs/contracts/backend-interface-contract.md`](../docs/contracts/backend-interface-contract.md)
  （Surface 8 + push-primary amendment。generator は新 surface を作らない）
