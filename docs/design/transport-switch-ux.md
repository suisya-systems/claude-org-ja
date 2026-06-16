# 輸送系切替の会話インターフェース化 — UX 設計

> ステータス: **design only / 実装なし**。本ドキュメントは「未実装の将来設計（提案・計画）」であり、以下の記述はすべて**提案**である。本リポジトリにこの設計の実装は存在せず、規範文書（[`CLAUDE.md`](../../CLAUDE.md) / `.claude/skills/**/SKILL.md` / `docs/contracts/**`）・運用 runbook・生成器コードはいずれも本設計によって**変更されない**。参照は本設計書 → 既存文書の**一方向のみ**（既存文書側へ本設計書への参照を足すことはしない）。実装済みかのような現在形では書かない。
>
> **スコープ（Issue #535）**: 輸送系（transport）切替の**会話インターフェース化の設計**に限定する。具体的には次の 3 機構を設計する:
> 1. 生 env `ORG_TRANSPORT` を利用者から隠蔽し、窓口（secretary）/ `org-start` が会話インターフェース（「broker で起動して」「renga に戻して」）で env 設定・子ペイン継承を代行する機構（[§5](#5-機構-1-会話インターフェースによる-env-設定子ペイン継承の代行)）。
> 2. `org-start` 起動報告への現在輸送系の常時 1 行可視化（[§6](#6-機構-2-org-start-起動報告への現在輸送系の常時-1-行可視化)）。
> 3. broker-dogfood-runbook の生 env 手順の付録降格 + PowerShell 併記の**方針**（[§7](#7-機構-3-broker-dogfood-runbook-の生-env-手順の付録降格--powershell-併記の方針)。runbook 本体は変更しない）。
>
> **不可侵の前提（本設計が覆さない確定制約、[§2](#2-不可侵の前提制約の明文化)）**: (a) **既定 `renga`（`ORG_TRANSPORT` 無設定）= bit 等価の非破壊不変条件**は不可侵、(b) `ORG_TRANSPORT` の**解決順（explicit 引数 > `ORG_TRANSPORT` env > 既定 `renga`）と SoT（runtime transport descriptor）は変更しない**、(c) 本設計は **Epic #6 Issue G トラック 3（本番 ja の broker 実走 / dogfood 実行）とは独立**（[§8](#8-issue-g-トラック-3-との独立性)）。
>
> 依存ドキュメント（参照は本設計書 → 既存文書の一方向のみ）:
> - [`tools/transport.py`](../../tools/transport.py)（ja 側 transport アクセサ。runtime descriptor を読む単一シーム）
> - [`docs/operations/broker-dogfood-runbook.md`](../operations/broker-dogfood-runbook.md)（生 env 手順の現状。§7 の付録降格提案の対象）
> - [`docs/design/renga-decoupling.md`](./renga-decoupling.md)（renga 依存解消の上位設計。本設計はその UX 層）
> - [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md)（Set D / Surface 8 案。broker auth & delivery）
> - [`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md)（起動報告の所在。§6 の可視化提案の対象）
>
> **二フレーム注記（Refs #586 #604、2026-06-17 追加 — design only の本文は変更しない）**: 本設計書は 2026-06-11 執筆時点の **運用フレーム**で書かれており、随所の「既定 `renga`（`ORG_TRANSPORT` 無設定）」、および [§2](#2-不可侵の前提制約の明文化)-1 / [§5.5](#55-非破壊不変条件の因果連鎖) の「env 無し → `resolve()` は既定 renga → `rewrite_allow_entries` 恒等 → bit 等価」という非破壊不変条件は、執筆時 runtime（0.1.27、`DEFAULT_TRANSPORT = "renga"`）の **運用既定経路**を指す表現である。これとは別に **コード定数フレーム**があり、`DEFAULT_TRANSPORT` は runtime 0.1.28（Epic #586 Phase 2）で `renga` → `broker` にフリップ済みで、コード上は `resolve(env={})` が `broker` を返す。両フレームは指す対象（運用既定経路 vs コード定数）が異なり矛盾しない。本設計が提案する持続選択機構（[§5.4](#54-提案する持続選択機構persisted-choice)）/ 会話 IF は **どちらが組込み既定でも成立する**薄い層であり（[§5.1](#51-会話トリガ意図--輸送系の選択) L97 が「反転の前後どちらでも成立する」と明示）、運用上の既定経路（本番 broker 実走 = Epic #6 Issue G **トラック 3**（[§8](#8-issue-g-トラック-3-との独立性)）が活性化するまで renga。Issue G の #515 併存 dogfood 自体は批准済みで、未了なのは本番昇格＝トラック 3 のみ）は持続選択が `ORG_TRANSPORT` を代行設定することで実現される。したがって本注記は設計本体を改訂せず、上記の「既定 renga」prose を運用フレームの表現として読むための整合注記である（flip / revert はしない）。

---

## 1. 背景と目的

### 1.1 現状: 生 env が利用者に露出している

輸送層は既定 `renga` / opt-in `broker` の両系を持つ（[`tools/transport.py`](../../tools/transport.py)、[`docs/design/renga-decoupling.md`](./renga-decoupling.md)）。切替の唯一の制御点は環境変数 `ORG_TRANSPORT`（`renga` | `broker`、無設定 = 既定 `renga`）であり、現状この生 env を**利用者が自分でシェルに設定する**ことが切替手段になっている。broker 系を試す利用者は [`docs/operations/broker-dogfood-runbook.md`](../operations/broker-dogfood-runbook.md) の手順で `ORG_TRANSPORT=broker python3 ...` のように env を前置し、切戻しには `unset ORG_TRANSPORT`（§5(1)）を打つ。

この現状には次の摩擦がある（**いずれも提案の動機であって、現状が壊れているという主張ではない** — 既定 renga 経路は完全に機能している）:

- **生 env が業務言語でない**: `CLAUDE.md` の窓口方針は「技術用語を避け、業務言語で会話する」だが、`ORG_TRANSPORT=broker` は技術用語そのものであり、窓口の会話モデルから外れる。
- **設定箇所と継承範囲が利用者の暗黙知に依存する**: 窓口・ディスパッチャー・ワーカーは別々のペイン（別プロセス）で動く。どのプロセスの env に設定すれば切替が全ロールに行き渡るかは [`tools/transport.py`](../../tools/transport.py) と生成器の内部知識を要し、利用者には見えない（[§5.2](#52-二つの伝播面)）。
- **現在どちらの系で動いているかが見えない**: 起動報告に輸送系の表示が無く、利用者は「いま renga か broker か」を env を読まないと確かめられない。
- **runbook の生 env 手順が POSIX 前提**: [`docs/operations/broker-dogfood-runbook.md`](../operations/broker-dogfood-runbook.md) の env 操作は bash（`export` / `unset` / `kill -INT`）で書かれており、Windows（PowerShell）利用者がそのまま使えない。

### 1.2 目的

生 env `ORG_TRANSPORT` を**利用者の操作面から隠蔽**し、輸送系の切替を**窓口の会話インターフェース**に載せる。同時に、現在の輸送系を起動報告で**常時 1 行可視化**し、runbook の生 env 手順は**最終手段（付録）に降格**して PowerShell 併記を添える方針を定める。**生 env は廃止しない** — 解決順の最下層の制御点（env）は SoT 連鎖の一部として不変に残し、会話インターフェースはその env を**代行設定する上位の薄い層**として被せる（[§3](#3-中心テーゼ-会話インターフェースは-resolve-連鎖の上に乗る薄い層)）。

---

## 2. 不可侵の前提（制約の明文化）

本設計が**覆さない**確定制約。設計のすべてはこの 3 点の下に置かれる。

1. **既定 `renga`（無設定）= bit 等価の非破壊不変条件は不可侵**。`ORG_TRANSPORT` 無設定では、生成器（settings / allowlist 等の生成物）が現行と 1 byte も変わらない。これは [`tools/transport.py`](../../tools/transport.py) の `rewrite_allow_entries`（既定 `renga` で入力をそのまま返す恒等）と runtime descriptor が構造的に保証している。**会話インターフェースを足しても、会話 IF で broker を選んでいない限り（= 永続選択が未設定 / renga）env は設定されず、既定 renga のまま bit 等価が保たれる**（条件は「このセッションで会話したか」ではなく「broker を opt-in したか」。[§5.4](#54-提案する持続選択機構persisted-choice) クロスセッション含意 / [§5.5](#55-非破壊不変条件の因果連鎖)）。

2. **解決順（explicit 引数 > `ORG_TRANSPORT` env > 既定 `renga`）と SoT は変更しない**。輸送系の決定ロジックは [`tools/transport.py`](../../tools/transport.py) の `resolve()`（= runtime の `resolve_transport`）に閉じており、その唯一の SoT は runtime transport descriptor（`claude_org_runtime.transport`）である。**会話インターフェースは `resolve()` を置き換えず、その入力（env）を*供給する*だけ**。可視化（[§6](#6-機構-2-org-start-起動報告への現在輸送系の常時-1-行可視化)）も `resolve()` を**consume** するのみで、輸送系を再導出（独自判定）しない。

3. **Epic #6 Issue G トラック 3 とは独立**。本設計は「切替の UX / 人間工学の層」であり、トラック 3（本番 ja の broker 実走 = dogfood 実行）に**依存もブロックもしない**（[§8](#8-issue-g-トラック-3-との独立性)）。

---

## 3. 中心テーゼ: 会話インターフェースは `resolve()` 連鎖の上に乗る薄い層

Issue #535 は一見矛盾する 2 つを要求する — 「生 env を利用者から隠蔽する」と「解決順・SoT を変更しない」。本設計の核は**この 2 つが両立すること**を示すことにある。両立の鍵は次の一文に集約される:

> **会話インターフェースは、変更しない `resolve()` 連鎖の*上*に被せる薄い write-side 自動化（env 設定の代行）+ read-side 表示（現在系の可視化）の層であって、`resolve()` の置き換えや再導出ではない。**

- 「broker で起動して」→ この層が利用者に代わって `ORG_TRANSPORT=broker` を**設定**する（+ 子ペインへ伝播する、[§5](#5-機構-1-会話インターフェースによる-env-設定子ペイン継承の代行)）。
- 「renga に戻して」→ この層が `ORG_TRANSPORT` を**解除**する（既定 renga に戻す）。
- `resolve_transport()` / descriptor は今日と**寸分違わず**同じことをする。会話はその入力 env を*供給する*だけで、迂回も再導出もしない。
- 非破壊不変条件は**この層から自動的に従う**: 会話 IF で broker を一度も選択しない限り（= 永続選択が未設定 / renga）→ env 未設定 → 既定 renga → `rewrite_allow_entries` 恒等 → bit 等価（[§5.5](#55-非破壊不変条件の因果連鎖)）。**不変条件が支配するのは「broker を選んでいない既定 renga 状態」であって「このセッションで会話したか否か」ではない**点に注意（[§5.5](#55-非破壊不変条件の因果連鎖) で精密化）。

以降の各機構は、すべてこのテーゼの下にある詳細である。

---

## 4. 機構の所在（窓口 + org-start）

会話インターフェースの**聞き手**と、可視化の**表示主体**は次のとおり位置付ける（[`CLAUDE.md`](../../CLAUDE.md) で窓口 = 人間との唯一の接点であることに整合）:

| 役割 | 本設計での位置付け |
|---|---|
| **窓口（secretary）** | 「broker で起動して」「renga に戻して」等の会話トリガを聞く主体。利用者の意図を輸送系の選択（renga / broker）に翻訳し、[§5](#5-機構-1-会話インターフェースによる-env-設定子ペイン継承の代行) の代行設定を起動する。輸送系の選択は人間ゲートを伴う判断（broker は opt-in / 切戻し可）であり、窓口がその唯一の対話面になる |
| **`org-start`（および各ペインの起動シーケンス）** | 二役を担う: (i) **代行設定の反映** — 起動時に永続選択（[§5.4](#54-提案する持続選択機構persisted-choice)）を読み、自ペインの env に `ORG_TRANSPORT` を反映する（窓口が会話で確定した選択を「env 設定の代行」として起動時に効かせる主体）。(ii) **可視化** — 確定した輸送系を `resolve()` 経由で読み、起動報告に常時 1 行で添える（[§6](#6-機構-2-org-start-起動報告への現在輸送系の常時-1-行可視化)） |

> **責務分担の整理（Issue #535 スコープ文言との対応）**: 冒頭スコープの「窓口 / `org-start` が会話 IF で env 設定・子ペイン継承を代行する」は、**窓口 = 会話の聞き手（意図 → 選択の確定・永続化）**、**`org-start` / 各ペイン起動シーケンス = 確定選択の env への反映（代行設定の実行点）+ 可視化**、という二段で実現される。env 設定の「指示」は窓口、「反映」は起動シーケンス、という分担であり、両者が合わさって「会話 IF による env 設定・子ペイン継承の代行」になる。

> **design only の注記**: 上記は規範文書（`CLAUDE.md` / 各 SKILL）への**変更提案ではなく**、会話トリガ・可視化・env 反映を「どの役割がどこで担うか」の設計上の位置付けである。実際の prose 反映は別スコープ（本設計の取り込み判断を経た後）であり、本設計書は規範文書に触れない。

---

## 5. 機構 (1): 会話インターフェースによる env 設定・子ペイン継承の代行

本設計で最も注意を要する機構。輸送系の選択が**窓口 → ディスパッチャー → ワーカー**の全ロール（複数プロセス）に行き渡る必要があり、しかも `spawn_claude_pane` のペイン境界を 2 回越える。

### 5.1 会話トリガ（意図 → 輸送系の選択）

窓口は次のような業務言語の発話を輸送系の選択に翻訳する（語彙は例示。**規範化はしない**）:

| 利用者の発話（例） | 翻訳される選択 | 代行する操作 |
|---|---|---|
| 「broker で起動して」「broker を試したい」 | `broker` | `ORG_TRANSPORT=broker` 相当を代行設定（[§5.4](#54-提案する持続選択機構persisted-choice)）+ 子ペイン継承 |
| 「renga に戻して」「renga で動かして」「元に戻して」 | `renga`（既定） | `ORG_TRANSPORT` 解除相当を代行（持続選択を renga = 未設定に戻す） |
| 「いまどっち（の輸送系）？」 | （変更なし、照会） | `resolve()` を consume して現在系を報告（[§6](#6-機構-2-org-start-起動報告への現在輸送系の常時-1-行可視化) と同じ表示経路） |

- **broker 選択は opt-in / 切戻し可の判断**として扱う。これは**現行の transport descriptor セマンティクス**（既定 = renga / `broker` は `ORG_TRANSPORT=broker` を明示した時のみ。[`tools/transport.py`](../../tools/transport.py) L17-22、[`CLAUDE.md`](../../CLAUDE.md)「輸送層（transport）両系」）に基づく。窓口は選択を代行設定するだけで、broker daemon の起動・dogfood 実行（トラック 3）には踏み込まない（[§8](#8-issue-g-トラック-3-との独立性)）。
  - **注（[`docs/design/renga-decoupling.md`](./renga-decoupling.md) との関係）**: renga-decoupling の**将来 end-state**（完全移行）では既定が pure backend に反転し「renga が opt-in fallback」になる（同 §1 採用方針 / §2）。本設計が前提にするのは**現行セマンティクス**（既定 renga / broker が opt-in）であり、end-state の反転とは時点が異なる。本設計の会話 IF は「現在どちらが既定であれ、選択を代行設定し可視化する層」として、反転の前後どちらでも成立する（既定が renga か broker かは descriptor が決め、本設計は判定しない）。
- 解決順（[§2](#2-不可侵の前提制約の明文化)-2）は不変なので、利用者が**明示引数**（`explicit`）で個別呼び出しに輸送系を渡す経路は会話 IF より優先される（最上位）。会話 IF が触るのは env 層（中位）のみ。

### 5.2 二つの伝播面

輸送系の選択が「行き渡る」ことには、混同されがちな**二つの別個の伝播面**がある。本設計はこれを分けて扱う:

- **(A) 生成時ベイク（generation-time baking）**: ja の生成器（[`tools/gen_delegate_payload.py`](../../tools/gen_delegate_payload.py) / [`tools/gen_worker_brief.py`](../../tools/gen_worker_brief.py)）が、生成を実行する**プロセスの env** の `ORG_TRANSPORT` を [`tools/transport.py`](../../tools/transport.py) アクセサ経由で読み、輸送固有値（サーバー名 `renga-peers` / `org-broker`、`spawn_inject` flag、allowlist）を成果物（delegate payload / worker brief）に**焼き込む**。すなわち、選択は「生成器を走らせるプロセスの env」を通じて成果物の*内容*に伝播する。

- **(B) 子ペインのプロセス env（child-pane process env）**: `spawn_claude_pane` で起動された子ペイン（ディスパッチャー / ワーカーの Claude プロセス）自身が、起動時に `ORG_TRANSPORT` を**自分の `os.environ` に持つか**。これは、その子ペインが**自分でさらに生成器を走らせる**場合（例: ディスパッチャーが `delegate-plan` / `gen_delegate_payload` を実行してワーカー向け成果物を作る）に効く。

> **重要な区別**: (A) は「成果物の内容」、(B) は「子プロセスの環境」。Issue #535 が言う「子ペイン継承」は主に (B) を指すが、実運用で輸送系が全ロールに行き渡る経路は (A)（生成器が焼き込んだ成果物が伝わる）にもまたがる。本設計は両面を明示し、どちらに賭けるかを [§5.4](#54-提案する持続選択機構persisted-choice) で決める。

### 5.3 伝播チェーン（窓口 → ディスパッチャー → ワーカー）

輸送系が全ロールに一貫するために伝播すべき経路（**現状の素朴な「env を 1 箇所に export」では不十分**な理由を明示する）:

```
利用者「broker で起動して」
   │
   ▼
窓口（secretary プロセス）── env 設定の代行 ─┐
   │ (A) 窓口自身が生成器を走らせる分は窓口プロセス env が効く
   │ (B) 子ペイン spawn 時に ORG_TRANSPORT が継承されるか？  ← 不確実（下記）
   ▼
ディスパッチャー（別ペイン = 別プロセス）
   │ (A) ディスパッチャーが gen_delegate_payload / delegate-plan を走らせる分は
   │     ディスパッチャープロセス env が効く
   │ (B) ワーカー spawn 時に ORG_TRANSPORT が継承されるか？  ← 不確実
   ▼
ワーカー（さらに別ペイン = 別プロセス）
```

- **不確実点（実装時検証が必要、[§9](#9-残存リスクと実装時検証項目)）**: `spawn_claude_pane` が起動する子ペインの env が、**呼び出し元 Claude プロセスの（セッション途中で変更された）`os.environ` を継承するとは限らない**。renga は同一 renga サーバープロセス系統下でペインを起動するため、子ペインが継承するのは `renga --layout ops` を起動した時点のシェル env であって、セッション途中に窓口 Claude の `os.environ` に加えた変更ではない可能性が高い。**「セッション途中の env 設定が spawn で子ペインに継承される」を既成事実として書かない**（advisor 指摘 / grep でも spawn-flow に env 継承の記述は無いことを確認済み: [`.dispatcher/references/spawn-flow.md`](../../.dispatcher/references/spawn-flow.md) は broker spawn を `--mcp-config <broker>` 注入として記述し、`ORG_TRANSPORT` 継承には言及していない）。

### 5.4 提案する持続選択機構（persisted choice）

[§5.3](#53-伝播チェーン窓口--ディスパッチャー--ワーカー) の不確実点（プロセス env 継承に賭けられない）を踏まえ、本設計は**プロセス env の継承に依存しない持続選択機構**を提案する。プロセス env への素朴な依存より頑健であり、解決順・SoT を変更しないという制約とも両立する。

**提案: 輸送系の選択を小さな永続状態として持ち、各プロセスが起動時にそれを env へ反映する**。

- 窓口が会話で broker を選んだとき、代行操作は「窓口プロセスの `os.environ` を変える」ことに賭けるのではなく、**輸送系の選択を 1 箇所の永続点に書く**（候補: 既存 state の一部 / 専用の小さな設定。具体的な所在・形式は実装時に決定し、[`docs/contracts/state-schema-contract.md`](../contracts/state-schema-contract.md) Set C の inventory との整合を要する場合は契約改訂提案として別途扱う）。
- 各ペイン（窓口 / ディスパッチャー / ワーカー）の**起動シーケンス**が、この永続選択を読んで自分のプロセス env に `ORG_TRANSPORT` を反映する。これにより (B) 子ペイン env と (A) 生成時ベイクの**両面が同じ選択に揃う**。
- **解決順・解決入力は不変（重要）**: 永続選択は `resolve()` の**新しい解決入力ではない**。`resolve()` が見る非明示入力は今日と同じく `ORG_TRANSPORT` env **だけ**である（[`tools/transport.py`](../../tools/transport.py) L18-21 / L62-73）。永続選択は「各ペインの起動シーケンスが env に `ORG_TRANSPORT` を書く際の値の出どころ」=**env 設定の自動化機構**であって、利用者が各ペインのシェルで手で `ORG_TRANSPORT` を export するのを一貫・自動化したものに等しい。解決順（explicit > env > 既定）も `resolve()` の優先順位ロジックも一切触れない。
- **切戻し**: 「renga に戻して」は永続選択を renga（= env 未設定相当）に戻す。次の起動から全ペインが既定 renga に揃う。実行中の broker ペインの即時復帰は [`docs/operations/broker-dogfood-runbook.md`](../operations/broker-dogfood-runbook.md) §5 の切戻し条件に従う運用領域であり、本設計（UX 層）はそこへは踏み込まない。
- **クロスセッション含意（明示）**: 永続選択は session をまたいで残る。一度 broker を選ぶと、明示的に「renga に戻して」と言うまで以後の起動も broker に揃う（= opt-in 状態が継続する。これは意図した UX）。したがって**「このセッションで会話しなかった ⇒ renga」とは言えない** — 過去に broker を選べば永続選択は broker のまま。bit 等価の不変条件はあくまで「broker を選んでいない（永続選択が未設定 / renga）状態」を支配するものであり、broker を opt-in した後の非 bit 等価は**利用者が明示選択した正しい帰結**である（[§5.5](#55-非破壊不変条件の因果連鎖)）。

> **代替案と却下理由**:
> - 「窓口プロセスの env を変えて spawn 継承に賭ける」案 — renga の spawn が窓口 Claude のセッション途中 env を継承する保証が無い（[§5.3](#53-伝播チェーン窓口--ディスパッチャー--ワーカー)）ため、頑健性で persisted choice に劣る。
> - 「永続化せず毎回会話で指定」案 — 「会話しなければ常に既定 renga」という単純な不変条件は保てる長所があるが、ディスパッチャー / ワーカーが独立に生成器を走らせる (A) 経路で選択を共有できず、かつ起動のたびに再選択が要る。本設計は cross-pane / cross-session の一貫性を優先して persisted choice を推奨するが、クロスセッション含意（上記）を受け入れない運用なら本案も選べる（最終決定は実装スコープ）。

> **design only**: 上記は機構の**提案**であり、永続点の新設・起動シーケンスへの組み込みは実装スコープ（規範文書・state schema は本設計書では変更しない）。

### 5.5 非破壊不変条件の因果連鎖

[§2](#2-不可侵の前提制約の明文化)-1 が会話 IF を足しても保たれることの**証明**（因果連鎖）。不変条件が支配する条件は**「会話 IF で broker を選んでいない（= 永続選択が未設定 / renga）」**であって「このセッションで会話したか」ではない（[§5.4](#54-提案する持続選択機構persisted-choice) クロスセッション含意）:

```
会話 IF で broker を一度も選択していない（永続選択 = 未設定 / renga）
   → 各ペイン起動シーケンスは ORG_TRANSPORT を env に設定しない
   → resolve() は「env 無し」を見て既定 renga を返す
   → rewrite_allow_entries は DEFAULT_TRANSPORT で入力を恒等返し
   → 生成物は 1 byte も変わらない（bit 等価）
```

- すなわち、会話 IF は**既定 renga 経路（broker 非選択）に対して完全に passive**である（broker を選ばなければ env は設定されず、何も起きない）。これが「会話 IF を被せても非破壊不変条件は不可侵」の構造的根拠である。
- 逆に、利用者が broker を opt-in した後は永続選択が broker のまま残り、起動シーケンスが `ORG_TRANSPORT=broker` を env に設定する。このとき生成物が broker 面に変わる（非 bit 等価になる）のは**不変条件の侵害ではなく、利用者の明示選択（opt-in）に対する正しい応答**である。不変条件はあくまで「既定 renga（broker 非選択）状態」の保護を約束するものであり、opt-in 後の状態には及ばない。

---

## 6. 機構 (2): `org-start` 起動報告への現在輸送系の常時 1 行可視化

### 6.1 提案

`org-start` の起動完了報告（[`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md) Step 4 の報告テンプレート群）に、**現在の輸送系を常時 1 行**で添える。renga / broker いずれでも、また選択の有無にかかわらず**常時表示**する（「broker のときだけ出す」ではなく、現在系を常に明示することで「いま暗黙に何で動いているか」の不可視性を解消する）。

表示例（**文面は提案であり規範化しない**）:

```
組織を起動しました。
前回の状態: {サマリー}
ディスパッチャーを起動しました（キュレーターは知見が溜まったときに自動で一時起動されます）。
輸送系: renga（既定）          ← 常時 1 行（broker 選択時は「輸送系: broker（opt-in）」）
何をしますか？
```

### 6.2 不可侵制約との整合

- **SoT を再導出しない**: 表示する輸送系は [`tools/transport.py`](../../tools/transport.py) の `resolve()`（= runtime descriptor 駆動）を**consume** して得る。独自に env を読んで判定したり、輸送系を再計算したりしない（[§2](#2-不可侵の前提制約の明文化)-2）。これが「SoT 変更しない」を表示側で守る方法である。
- **bit 等価を脅かさない**: 起動報告は**会話出力**であってファイルを書かない。bit 等価の不変条件が支配するのは*生成物（settings / allowlist）*であり、人間向けの 1 行報告ではない。したがって既定 renga でこの 1 行を常時出しても、生成物の bit 等価には一切影響しない（混同しないよう明記する）。
- **runtime drift 行との関係**: 既存の `org-start` Step 4 は `tools/check_runtime_version.py` の drift 行を末尾に転記する仕組みを持つ（[`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md) Block C2 / Step 4）。輸送系の 1 行はそれと**独立な常時行**として位置付ける（drift は条件付き warning、輸送系は無条件の状態表示）。

> **design only**: Step 4 テンプレートへの実反映は別スコープ。本設計書は SKILL を変更しない。

---

## 7. 機構 (3): broker-dogfood-runbook の生 env 手順の付録降格 + PowerShell 併記の方針

### 7.1 方針（runbook 本体は変更しない）

[§5](#5-機構-1-会話インターフェースによる-env-設定子ペイン継承の代行) の会話インターフェースが**主経路**になることを前提に、[`docs/operations/broker-dogfood-runbook.md`](../operations/broker-dogfood-runbook.md) の生 env 手順（`ORG_TRANSPORT=broker python3 ...` の前置、`unset ORG_TRANSPORT` での切戻し等）を**最終手段 = 付録に降格する**方針を定める。意図は次の優先順位を runbook に持たせることである:

1. **主経路**: 会話インターフェース（「broker で起動して」「renga に戻して」）。
2. **付録 / 最終手段**: 生 env の直接操作（会話 IF が使えない / デバッグ / CI / 自動化など、利用者が明示的に低レベル制御を要する場面に限定）。

降格は「削除」ではない — 生 env は解決順の正当な制御点（[§2](#2-不可侵の前提制約の明文化)-2）として残り、runbook の付録で引き続き正確に文書化される。**本設計書はこの方針を述べるだけで、runbook 本体は編集しない**（design only）。

### 7.2 PowerShell 併記（付録に添える対応表の提案）

現行 runbook の env 操作は bash 前提（`export` / `unset` / `kill -INT`）。付録には Windows（PowerShell）の対応形を併記する方針とする。提案する対応表:

| 操作 | bash（現行 runbook） | PowerShell（併記提案） |
|---|---|---|
| broker を **1 コマンドだけ**に効かせる（子プロセス限定） | `ORG_TRANSPORT=broker python3 ...`（その 1 プロセスにのみ及び、シェルには残らない） | PowerShell に**等価な前置形は無い**。`$env:ORG_TRANSPORT = "broker"` はセッション env を書き換え以後も残るため、`$env:ORG_TRANSPORT = "broker"; python ...; Remove-Item Env:\ORG_TRANSPORT` のように**実行後に明示解除**するか、子プロセス限定が必須なら `Start-Process` の `-Environment` 等で起動プロセスにだけ渡す（bash の child-only 等価は自明には書けない旨を付記） |
| broker を当該セッションに設定（以後残す） | `export ORG_TRANSPORT=broker` | `$env:ORG_TRANSPORT = "broker"` |
| 現在値の確認 | `echo "$ORG_TRANSPORT"` | `$env:ORG_TRANSPORT` |
| 切戻し（解除） | `unset ORG_TRANSPORT` | `Remove-Item Env:\ORG_TRANSPORT`（未設定でもエラーにしないなら `Remove-Item Env:\ORG_TRANSPORT -ErrorAction SilentlyContinue`） |
| daemon 停止（前景 serve に SIGINT） | `kill -INT <pid>` | 前景なら `Ctrl+C`。PID 指定停止は `Stop-Process -Id <pid>`（SIGINT 相当の graceful 停止は Windows では一般に困難なため、前景 `Ctrl+C` を主とする旨を付記） |

> **注（前置形の非等価、重要）**: bash の `VAR=val cmd` は**起動する子プロセスにのみ** env を渡しシェル自身には残さない。PowerShell の `$env:VAR = "val"` は**現在のセッション env を書き換え**、明示解除するまで以後の起動にも残る。両者を同一視すると Windows 利用者が意図せず broker を残置するため、付録では「PowerShell では set → 実行 → 解除（または `Start-Process -Environment`）」を明記する。

> **注（解決順の不変）**: PowerShell 併記は表記の追加であって、解決順・SoT・bit 等価のいずれにも影響しない（`$env:ORG_TRANSPORT` は bash の `ORG_TRANSPORT` と同じ env 層に値を置くだけ）。
>
> **注（CLAUDE.local.md の Windows ガイダンスとの整合）**: 本リポジトリの Windows 環境では Python は `py -3` または `python`。付録の PowerShell 例もこれに合わせる（`python3` 直叩きが無い環境では `python` / `py -3`）。

> **design only**: 上記対応表は runbook 付録への**提案**であり、本設計書から runbook を編集しない。

---

## 8. Issue G トラック 3 との独立性

本設計（切替の UX 層）と Epic #6 Issue G トラック 3（本番 ja の broker 実走 / dogfood 実行）の境界を明示する:

- **本設計が扱うもの**: 輸送系**選択の人間工学** — 会話での切替、現在系の可視化、生 env 手順の降格方針。renga / broker のどちらが選ばれても成立する、選択肢を提示・記録・表示する層。
- **本設計が扱わないもの**: broker daemon の起動・ライフサイクル・dogfood 実走・切戻し条件の実行（これらは [`docs/operations/broker-dogfood-runbook.md`](../operations/broker-dogfood-runbook.md) とトラック 3 のスコープ）。
- **依存関係**: 本設計は**トラック 3 に依存しない**（broker を実走しなくても、会話 IF・可視化・runbook 方針は設計・実装できる）。逆に**トラック 3 をブロックもしない**（トラック 3 は生 env 経路でそのまま進められる。会話 IF は主経路を被せるだけで、生 env 経路を塞がない — [§7.1](#71-方針runbook-本体は変更しない)）。両者は独立に進行できる。

---

## 9. 残存リスクと実装時検証項目

| 項目 | 整理 |
|---|---|
| **子ペイン env 継承の実機挙動（[§5.3](#53-伝播チェーン窓口--ディスパッチャー--ワーカー)）** | `spawn_claude_pane` が起動する子ペインが、セッション途中に変更された呼び出し元 env を継承するか否かは**実機検証が必要**。本設計は継承に賭けず persisted choice（[§5.4](#54-提案する持続選択機構persisted-choice)）を提案するが、起動シーケンスが永続選択を env へ反映する具体経路は実装時に検証する |
| **永続選択の所在と Set C 整合** | persisted choice の格納先（既存 state の一部か専用設定か）と形式は実装時決定。`.state/` 配下に新設するなら [`docs/contracts/state-schema-contract.md`](../contracts/state-schema-contract.md) Set C の inventory 改訂提案が要る（本設計書はその改訂を行わない） |
| **会話トリガ語彙の曖昧性** | 「broker で起動して」等の自然言語は曖昧になりうる。窓口が選択を確定する前に、broker が opt-in / 切戻し可であること・現在系を確認できるようにする（[§6](#6-機構-2-org-start-起動報告への現在輸送系の常時-1-行可視化) の照会経路）。規範化は別スコープ |
| **生成時ベイクと表示の不整合** | 生成器が成果物に焼き込んだ輸送系（[§5.2](#52-二つの伝播面) (A)）と、起動報告の表示（[§6](#6-機構-2-org-start-起動報告への現在輸送系の常時-1-行可視化)）が同じ `resolve()` を consume することで一致を保証する。両者が別経路で輸送系を判定すると drift しうるため、表示は必ず SoT を consume する（[§6.2](#62-不可侵制約との整合)） |

---

## 改訂履歴

- 2026-06-11: 初版（design only。Issue #535「輸送系切替の会話インターフェース化」の UX 設計）。生 env `ORG_TRANSPORT` の隠蔽（会話 IF による env 設定・子ペイン継承の代行 / persisted choice 提案）、`org-start` 起動報告への現在輸送系の常時 1 行可視化、broker-dogfood-runbook の生 env 手順の付録降格 + PowerShell 併記方針を設計。不可侵制約（既定 renga = bit 等価 / 解決順・SoT 不変 / Issue G トラック 3 独立）の下で、会話 IF が `resolve()` 連鎖の上に乗る薄い層であることを中心テーゼとして固定。規範文書・runbook・runtime には触れない（一方向参照のみ）。
