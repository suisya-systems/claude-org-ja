# 案 b 設計: transport-neutral skill source + generator アーキテクチャ

> **目的**: Epic #586 の「prose 両系維持」を、**手動の両系反転（案 a）**から
> **transport-neutral source + generator による機械生成（案 b）**へ作り直す設計を確定する。
> 本ドキュメントは設計のみ。実装・スキル生成・既存ファイル反転は一切しない。成果物は本ファイル
> （`notes/broker-skill-generator-design.md`）の新規作成 1 件のみ。実装は人間 ratification ゲートを通してから。

- **関連 Issue**: Refs #586
- **生成日**: 2026-06-16
- **計画 SoT（上位）**: [`notes/broker-promotion-plan-586.md`](./broker-promotion-plan-586.md)
  （§1 反転テンプレ / §1.3 論理反転チェックリスト 8 軸 / §2 33 ファイル inventory + 難度表 /
  §3 既定値フリップ spec / §4 contract amendment / §5 Phase 依存順）。
- **pivot 入力（provenance）**: 手動反転ワーカー（broker-prose-sweep-t1、承認ゲートで stand down）の
  discovery 分析メモ `notes/broker-skill-gen-design-input.md`（自己矛盾の解消理由 / 保持すべき不変条件 /
  surgical 最難ファイル / 差異の局所性 4 点）。**本メモは現時点でローカル未コミットの pivot 成果物**であり、
  その実体（4 局所差異・自己矛盾リスク・最難 4 ファイル・保持すべき不変条件）は本設計 §0〜§5 に取り込み済みで、
  本ドキュメントは当該メモが無くても self-contained に読める。メモを監査証跡として repo に残すかは §9.2 の
  open decision 扱い（リンクは未コミット解消後に追加）。
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
| frontmatter `allowed-tools`（ツール認可面） | **transport-union（renga エントリを byte 保存 + broker エントリを加算）** | Claude Code はこの frontmatter をディスクから読み**ツール認可をゲート**する。broker 既定時に本文が `mcp__org-broker__*` を指示するなら frontmatter も broker を認可していないと**ツールが未認可で失敗**する。同時に renga エントリを byte 保存することで `ORG_TRANSPORT=renga` 即時切戻し（再生成なし）でも renga ツールが認可済みに保たれる |
| 本文 prose + dual-system ヘッダ | **`DEFAULT_TRANSPORT` = broker（render 面）** | 人間が読む面。既定フリップ後は broker 面が literal、renga は opt-in として併記 |

> **重要な訂正（設計の核心）**: 当初検討した「frontmatter を `TEMPLATE_TRANSPORT`=renga **恒等**のまま残す」案は
> **誤り**である。SKILL.md の `allowed-tools` は実際に `mcp__renga-peers__*` を列挙しており（現状 14 スキル）、
> Claude Code がディスクからこれを読んでツール認可をゲートする。broker 本文 × renga 恒等 frontmatter の組み合わせ
> は **broker 既定実行でツール未認可**を招く。正しい設計は **frontmatter = transport-union**:
>
> - **renga エントリは byte 保存**（除去・改変しない）。これが promotion-plan §3.2 / 入力メモの「permissions
>   アンカーで renga byte 不変」の正しい解釈 = **additive な「renga never removed」**（契約 §8.10 と同型）であって、
>   「broker 面で renga **だけ**を残す」ことではない。
> - **broker エントリを加算**。broker 既定の本文が指示するツールを認可する。
> - これにより `ORG_TRANSPORT=renga` の**即時切戻し（再生成なし・コード変更なし、promotion-plan §5.6 / §8.10）**
>   でも renga ツールが認可済みのまま保たれる（union は両系を同時認可するため、どちらの transport でも本文の
>   ツールが使える）。
>
> **例外 — `org-setup/references/permissions.md`**: このファイルだけは promotion-plan §3.2 の明示どおり
> **identity-anchor**（renga byte 完全不変・broker 射影は settings 生成のツール側）で別扱いする（§4.2(2)）。
> permissions.md は「skill frontmatter」ではなく org-setup が settings.local.json を生成する**素材テンプレ**であり、
> broker 射影が `rewrite_allow_entries` のツール層で起きるため、ファイル自体は renga のまま byte 不変でよい。

この**「frontmatter は transport-union / 本文は broker render」の二重基底**こそ、本設計が解く中心問題である。
generator は 1 ファイルを 2 基底で扱い、どちらも構造保証する（§4.2 / §論点 5）。union は既存
`rewrite_allow_entries`（現状は renga→broker の**置換**）を **union モード**（renga 保存 + broker 加算）へ
拡張して実現する（§論点 2 / §論点 5）。

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
| `{{CHANNEL_SRC}}` | `org-broker-channel` | `renga-peers` | フラグメント内で使用 |

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
| skill frontmatter `allowed-tools` の transport-union | **`transport.rewrite_allow_entries` を union モードへ拡張**（renga エントリ保存 + broker tier 加算。現状は置換のみ） | broker 既定で本文ツールを認可しつつ renga エントリを byte 保存（§0.4） |
| `permissions.md`（org-setup）の renga 恒等保持 | **既存 `transport.rewrite_allow_entries` を無改修で使う**（renga 解決 = 恒等、settings 層で broker 射影） | promotion-plan §3.2 の byte 不変を構造保証。skill frontmatter とは別扱い |
| 既定値リテラル（`{{DEFAULT_TRANSPORT}}`） | **既存 `transport.DEFAULT_TRANSPORT` を re-export 経由で読む** | 既定値の単一 SoT |
| prose トークン置換 + フラグメント注入 | **新規**（`tools/transport.py` に prose 用ヘルパを足すか、`gen_skill_prose.py` 内に閉じる） | allowlist 用 `rewrite_allow_entries` は flat list 専用で prose に流用不可 |

→ **「拡張すれど分岐させず（extend, don't fork）」**。transport 機構は全て既存 seam から consume し、
generator は「prose テンプレ → render」のオーケストレーションだけを新規に持つ。

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

- **on-disk の rendered SKILL.md 本文は `DEFAULT_TRANSPORT`（= broker）面の 1 枚だけ**。renga 面の本文を別途
  materialize する必要はない（opt-in は本文の dual-system 併記 + 後述の union frontmatter で処理済み）。
- frontmatter `allowed-tools` は同一ファイル内で **transport-union 面**（renga エントリ byte 保存 + broker 加算、
  §0.4）。**本文が broker 単一面でも frontmatter が両系認可なので、`ORG_TRANSPORT=renga` 即時切戻し（再生成なし）
  でも renga ツールが認可済み**に保たれる — これが「本文 1 枚で十分」を rollback-safe にする鍵。
- `org-setup/references/permissions.md` のみ identity-anchor（renga byte 完全不変、§4.2(2)）で別扱い。

drift CI（§7.2）が「broker 面の本文 + union 面の frontmatter（renga エントリ保存を含む）」を一括 assert する。

---

## 4. 論点 4: surgical 最難ファイルの個別ハンドリング

pivot 入力メモが挙げた 4 つの最難ファイルは、トークン展開だけでは表現できない。generator は **source manifest
で各ファイルの処理モードを宣言**し、surgical ファイルを token-body パイプラインから除外する。

### 4.1 処理モード分類（manifest）

| モード | 意味 | 対象例 |
|---|---|---|
| `template` | 全文をトークン + フラグメントで render | 短縮/長尺の標準スキル（promotion-plan §2 の大半） |
| `template+fragment` | 本文トークン + 個別 surgical 領域をマーカー区画で注入 | org-delegate / pane-layout / org-pull-request |
| `surgical-fragment` | token-body から除外。per-transport フラグメントを手当て、generator は領域注入のみ | renga-error-codes |
| `code-literal` | 既定値リテラルを `{{DEFAULT_TRANSPORT}}` で render（render 面トークンと区別） | org-start L66 echo |
| `identity-anchor` | render 対象外。renga byte 不変を構造保証（rewrite_allow_entries 恒等） | org-setup permissions.md |

### 4.2 4 最難ファイルの個別設計

**(1) `references/renga-error-codes.md`（renga=正典 / broker=加算の非対称）**

renga が正典、broker は**加算**という非対称構造はプレフィックス置換で表現できない。
- モード = `surgical-fragment`。token-body パイプラインから除外。
- 構造を「**shared codes（両系共通）+ per-transport 拡張表**」に再編する。broker 拡張コード
  （`[token_invalid]` 等）は generator が**末尾の拡張表として append**、renga 固有は据え置き。
- **ファイル名そのものが renga アンカー**。リネーム（`renga-error-codes` → `transport-error-codes`）は
  prose 整合と参照リンク更新を伴うため **open decision**（§9）へ。本設計では「リネームせず内容を非対称
  フラグメント化」を Phase 推奨とし、リネームは人間 ratify 後に別途。

**(2) `org-setup/SKILL.md` + `references/permissions.md`（byte 等価アンカーの向き）**

permissions の byte 等価アンカーは **renga 側固定**（promotion-plan §3.2）。**ここが §0.4 の union とは別扱いになる
唯一の領域**: permissions.md は skill frontmatter ではなく、org-setup が settings.local.json を生成する**素材
テンプレ**であり、broker 射影は `rewrite_allow_entries` の**ツール（settings 生成）層**で起きる。
- permissions.md = モード `identity-anchor`。generator は**触らない**。既存 `rewrite_allow_entries` の renga
  恒等が byte 不変を構造保証（明示 renga 解決 = `return list(entries)`）。union 化しない（settings 生成側で
  transport ごとに正しい allowlist が出るため、素材テンプレは renga 単一で byte 安定が最適）。
- org-setup/SKILL.md の dual-system **prose** だけ `template`/`template+fragment` で render。SKILL.md 自身の
  frontmatter `allowed-tools` は他スキル同様 **union**（§0.4。permissions.md の identity-anchor とは層が違う）。
- **アンカーの向きは反転しない**: 既定が broker になっても permissions の素材面は renga のまま
  （`TEMPLATE_TRANSPORT` 不変の原則。§0.3）。generator はこの「素材テンプレ=identity / skill frontmatter=union」
  の分離を強制する。

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
| frontmatter `allowed-tools` の renga エントリ byte 保存（additive「never removed」、§3.2 の正しい解釈、§0.4） | **skill frontmatter = union モード**（renga エントリ保存 + broker 加算）。renga エントリは除去・改変されないので byte 保存が構造保証され、かつ broker 既定で本文ツールが認可される。drift CI が「frontmatter に renga エントリが byte 保存」かつ「broker tier が加算済み」を両方 assert。**`permissions.md` のみ identity-anchor**（renga 完全 byte 不変、settings 層で broker 射影）として別 assert |
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

- generator は transport 事実（server 名・プレフィックス・spawn flag・既定値）を**一切定義しない**。全て
  `tools/transport.py` 経由で runtime から consume。
- skill frontmatter allowlist は `rewrite_allow_entries` の **union 拡張**を使う（renga 保存 + broker 加算）。
  `permissions.md`（org-setup 素材テンプレ）は既存 `rewrite_allow_entries` を**無改修・恒等**で使う。どちらも
  同じ 1 関数の派生で、transport 事実の二重定義は作らない。
- フラグメント文言は promotion-plan §1.1/§1.2 を**参照**（コピーして第 2 の文言 SoT を作らない。フラグメント
  ファイルは §1.1/§1.2 から導出した単一コピーとし、promotion-plan 側を「文言の設計 SoT」として明記）。
- settings generator（runtime）との関係: settings は allowlist を所有、prose generator は prose を所有。
  **両者の唯一の交点は allowlist 派生ロジック**で、そこは `rewrite_allow_entries`（恒等／置換／union の各モード）
  という 1 関数に集約する（交点を 1 関数に集約 = 二重化なし）。permissions.md は恒等、skill frontmatter は union。

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
2. **二重基底 assert**: 各生成物の frontmatter `allowed-tools` が (i) **renga エントリを byte 保存**している
   こと（union の renga 部分が `rewrite_allow_entries(renga 恒等)` と一致）かつ (ii) **broker tier を加算**して
   いること（broker 既定でツール認可漏れがない）。`permissions.md` のみ identity-anchor として renga 完全 byte
   一致を別 assert（§0.4 / §5）。
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
| Phase 4: rollback runbook + 後始末 | tools docstring 反転ほか | **概ね不変**。docstring も generator 対象に含めるか（`tools/*.py` の「既定 renga」前提文言）は open decision |

**依存順は維持**: contract-before-code → behavior-before-doc。generator（Phase 3'）は実体フリップ（Phase 2）
merge 済みを依存先に持つ（render 面 = `DEFAULT_TRANSPORT` = broker が成立している前提で生成）。

### 8.2 contract への影響

- **新たな contract surface は生じない**。generator は実装機構であって normative な transport 契約面ではない。
- promotion-plan §4 の「既定フリップ amendment」は**そのまま必要**（既定値の契約宣言の反転は generator とは独立）。
- 任意で contract の Decision rationale digest に「prose は手動両系維持から機械生成へ移行（drift CI で
  byte 固定）」の 1 文を追記しうるが、**normative ではない**ため必須ではない（open decision）。

### 8.3 案 a 対比の正味の差分

- **消えるもの**: 33 ファイル手動反転の自己矛盾リスク（pivot の主動機）、prose 反転の漏れ、**2 回目以降の
  transport 変更での L66 手動編集**（初回フリップの L66 は Phase 2 で手動のまま。トークン化は Phase 3'）。
- **増えるもの**: generator + フラグメント SoT + manifest + drift CI ジョブ + 各スキルの source ファイル
  （生成物との二重保持。settings.local.json と同じトレードオフ）。
- **収支**: 33 ファイル × 将来の transport 変更ごとの手動反転コストを、1 つの generator + CI に一元化。再現性・
  監査性・自己矛盾耐性を得る代わりに、初期の source 整備コストと生成物二重保持を負う。**両系を将来も維持する
  Epic #586 の前提下では案 b が優位**（手動両系は変更のたびに 33 ファイルの自己矛盾リスクを再生産する）。

---

## 9. 推奨案サマリと人間 ratification 待ち open decisions

### 9.1 推奨案（一括）

| 論点 | 推奨 |
|---|---|
| 1. 中立著述形式 | **案 1B**: 本文は中立トークン（`{{FQ}}`/`{{SERVER}}`/`{{DEFAULT_TRANSPORT}}`）、4 局所差異は名前付きフラグメント（`{{> dual-system-header-* }}` 等）。条件分岐を本文から追放 |
| 2. generator 置き場 | **案 2A**: ja `tools/gen_skill_prose.py`。transport 事実は既存 `tools/transport.py` 経由で consume。skill frontmatter は `rewrite_allow_entries` の **union 拡張**（renga 保存 + broker 加算）、`permissions.md` のみ既存の **恒等を無改修**で使う（§2.2 / §0.4） |
| 3. ロード機構 | **案 3A**: commit 時生成 + drift CI で byte 固定（settings.local.json 前例）。on-disk は broker 面 1 枚 |
| 4. surgical 最難 4 ファイル | manifest 処理モード（`surgical-fragment`/`code-literal`/`identity-anchor`）で個別ハンドリング（§4.2） |
| 5. 不変条件保証 | skill frontmatter = transport-union（renga byte 保存 + broker 加算）/ permissions.md = renga 恒等 / 両系フラグメント + neutral 固定区画で構造保証 + drift CI 機械 assert（§0.4 / §5） |
| 6. seam 接続 | runtime=機構 / ja seam / generator=consume の三層。frontmatter 交点は `rewrite_allow_entries` 1 関数に集約 |
| 7. 移行 + drift CI | G0〜G4 バッチ（§7.1）+ `tools/check_skill_drift.py`（§7.2） |
| 8. Epic #586 影響 | Phase 3 のみ案 b へ再スコープ。Phase 1/2 不変。contract に新 surface なし |

### 9.2 人間 ratification 待ち open decisions

| # | 論点 | 本設計の推奨 | 人間判断が要る理由 |
|---|---|---|---|
| 1 | **source と生成物の保持形態** | 隣接 source ファイル（`SKILL.md.in` 等）+ rendered `SKILL.md` を両 commit | 生成物二重保持 vs マーカー区画方式（単一ファイル内 `BEGIN/END GENERATED`）のトレードオフ。ほぼ同一の 2 ファイルを持つ重複コストの許容可否 |
| 2 | **`renga-error-codes.md` のリネーム** | リネームせず非対称フラグメント化（§4.2(1)） | `transport-error-codes` 等へのリネームは参照リンク全更新を伴う。ファイル名 = renga アンカーを残すか否かは設計時系列の判断 |
| 3 | **`tools/*.py` docstring を generator 対象に含めるか** | Phase 4 で手動反転（promotion-plan §3.2 踏襲）、generator 対象外 | コード docstring を prose generator に載せると Python source が token 化され可読性が落ちる。手動 vs 生成の線引き |
| 4 | **据え置き設計 docs の最終確定** | generator `exclude`、必要時 1 文の時点注記のみ（§7.1） | README 公開文書 / renga-decoupling / transport-switch-ux 等の end-state 記述を機械生成に巻き込まない判断（promotion-plan §2.1 と整合）の最終確認 |
| 5 | **フラグメント SoT の正本所在** | promotion-plan §1.1/§1.2 を文言設計 SoT とし、フラグメントファイルはその単一導出コピー | 「設計 SoT（promotion-plan）」と「実行 SoT（フラグメントファイル）」の二者間 drift をどう防ぐか（フラグメント自体を promotion-plan から生成するかは過剰設計の懸念） |
| 6 | **Phase 2 との merge 順序** | generator（Phase 3'）は実体フリップ merge 済みを依存先 | render 面 = broker を前提に生成するため。Phase 2 未 merge 段階で source 化だけ先行するか（renga 面で暫定生成）の運用判断 |
| 7 | **contract digest への 1 文追記** | 任意（normative でない） | prose 機構の移行を契約 digest に残すかは ratify 者の判断 |
| 8 | **pivot 入力メモ（`broker-skill-gen-design-input.md`）の commit 可否** | 監査証跡として repo へ commit を推奨（本設計は self-contained だが provenance を残す価値あり） | 当該メモは現時点でローカル未コミット。本設計の「成果物 1 件のみ」制約との兼ね合いで、別ファイル追加の可否は人間判断（commit するなら本設計のリンクも復活させる） |
| 9 | **skill frontmatter `allowed-tools` の transport-union vs broker 単一面** | **union（renga 保存 + broker 加算）を推奨**（§0.4）。即時切戻し（再生成なし）でも両系認可されるため rollback-safe | 代替は「broker 単一面 + 切戻し時に org-setup で frontmatter 再生成」。再生成を rollback 手順に組み込むなら単一面も可だが、promotion-plan §5.6「コード変更なしの即時切戻し」を厳守するなら union が必須。最終判断は rollback 運用設計（PR-2/PR-4）と合わせて人間が確定 |

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
- pivot 入力（provenance、現時点ローカル未コミット）: `notes/broker-skill-gen-design-input.md`
  — 実体は本設計 §0〜§5 に取り込み済み（self-contained）。コミット可否は §9.2 open decision #8
- 既存 seam: [`tools/transport.py`](../tools/transport.py)
  （`rewrite_allow_entries` 恒等パターン / `TEMPLATE_TRANSPORT` / `fq_prefix` / `DEFAULT_TRANSPORT`）
- contract SoT: [`docs/contracts/backend-interface-contract.md`](../docs/contracts/backend-interface-contract.md)
  （Surface 8 + push-primary amendment。generator は新 surface を作らない）
