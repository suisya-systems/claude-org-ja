# 自律 work-discovery（Issue triage）— 設計

> ステータス: **design only / 実装なし**。本リポジトリにこの設計の実装・配線は一切存在しない。本ドキュメントは「未実装の将来設計」であり、以下の記述はすべて**提案・計画**である。`.claude/` スキル・`.dispatcher/` prose・`tools/` への変更は本ブランチでは行っていない（成果物は本設計書 1 本のみ）。
>
> 一次入力:
> - [`.state/reports/loop-engineering-assessment.md`](../../.state/reports/loop-engineering-assessment.md) の **§5-1（唯一の構造的ギャップ = 仕事の自律発見が無い）** と **§7(b)（限定的な自律 work-discovery を「提案まで自動・着手判断は人間維持」で導入、+2〜3 点）**。
> - originating Issue: suisya-systems/claude-org-ja#520。
>
> 依存ドキュメント（参照は本設計書 → 既存文書の一方向のみ。既存文書側からの参照追加は行わない）:
> - [`CLAUDE.md`](../../CLAUDE.md)（窓口 = 唯一の人間接点 / 実作業は全委譲 / proactive next-dispatch / 役割の境界）
> - [`.claude/skills/org-delegate/SKILL.md`](../../.claude/skills/org-delegate/SKILL.md)（着手の正準経路 = 人間ゲート後の Step 0 から）
> - [`tools/check_curate_threshold.py`](../../tools/check_curate_threshold.py) と [`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md)（worker クローズ時のオンデマンド spawn = 本設計の delivery 先例）
> - [`.dispatcher/CLAUDE.md`](../../.dispatcher/CLAUDE.md)（dispatcher の役割境界・監視 /loop）
> - [`docs/journal-events.md`](../journal-events.md)（journal イベント台帳）

---

## 1. 背景と確定制約（本設計が覆さない前提）

この組織のループは**人間起点**である。ユーザーが窓口に依頼して初めてループが回り、`.state/reports/loop-engineering-assessment.md` §5-1 が指摘するとおり「issue tracker を scan して triage し次を選ぶ」自己給餌ループは存在しない。「マージ後に次の仕事を提案する」proactive 動作はあるが、**候補の選択は人間**であり、その提案自体も窓口がその場で即興している（[§2](#2-現状とこの設計の関係)）。

本設計は assessment §7(b) のレバー —— **「Issue triage を提案まで自動・着手判断は人間維持」** —— を具体化する。狙いは「人間をループから外す」ことでは**ない**。発見（discovery）の自律性だけを上げ、**判断（commitment）は従来どおり人間ゲートに残す**。

以下 3 点は本設計が覆さない確定制約である。

1. **窓口 = 唯一の人間接点**（[`CLAUDE.md`](../../CLAUDE.md)）。triage 結果が人間に届く経路は窓口を必ず経由する。discovery 機構が人間（または GitHub 上の人間可視面）へ直接到達してはならない。
2. **実作業は全委譲・秘書は調査しない**（[`CLAUDE.md`](../../CLAUDE.md)）。triage の scan は「調査」ではなく**決定的ツール実行**として設計する（[`tools/journal_append.sh`](../../tools/journal_append.sh) / `tools/pending_decisions.py` / [`tools/check_curate_threshold.py`](../../tools/check_curate_threshold.py) と同格の deterministic ops）。候補ごとの深掘り（実現性精査・設計）が要るなら、それは人間ゲートを通った後の委譲ワーカータスクになる。
3. **理解負債を増やさない**（assessment §5-2）。triage は「次に何をやり得るか」を**可視化**する機構であって、人間の理解を飛ばして着手を進める機構ではない。propose-only がこの制約と直結する（[§7](#7-安全レール不変条件)）。

## 2. 現状とこの設計の関係

**現行動作（実装済み・運用中）**: PR マージ後の post-merge cleanup が終わると、窓口は [`CLAUDE.md`](../../CLAUDE.md) の proactive next-dispatch 方針に従い、`gh issue list` 等をその場で叩いて次の仕事候補を 2〜4 件 + 推奨 1 つの形で人間に提示する。これは**窓口の即興**であり、判定基準（依存解決済みか・優先度・工数）は明文化されておらず、再現性・網羅性・監査可能性が無い。トリガも「PR マージ直後」に限られ、組織が idle になった時点での発見は行われない。

**この設計（未実装の提案）**: 上記の即興を、**決定的な triage 計算層**（[§3](#3-設計の二層構造)・[§4](#4-triage-基準)・[§5](#5-出力フォーマット)）と、**それを起動・配達する delivery 層**（[§6](#6-delivery-方式-3-案比較)）に分離する。post-merge proactive next-dispatch はこの triage 結果を消費する一利用者に格上げされる（[§8](#8-post-merge-proactive-next-dispatch-との統合)）。本設計が実装されるまで、現行の即興動作は一切変わらない。

| 観点 | 現行（即興、実装済み） | 提案（triage 機構、未実装） |
|---|---|---|
| 判定基準 | 暗黙（窓口の判断） | 明文化（依存解決済み / 優先度 / 工数見積もり、[§4](#4-triage-基準)） |
| 出力 | その都度の自由形式 | 構造化スキーマ（候補 N 件 + 推奨 1、[§5](#5-出力フォーマット)） |
| トリガ | PR マージ直後のみ | post-merge / worker クローズ / 窓口手動（[§6](#6-delivery-方式-3-案比較)） |
| 着手判断 | 人間（番号で即決） | 人間（変更なし。propose-only を不変条件化、[§7](#7-安全レール不変条件)） |
| 監査 | 無し | journal イベント + 候補 JSON で再現可能 |

## 3. 設計の二層構造

triage を「**計算（どの Issue がどう triage されるか）**」と「**配達（いつ・誰が走らせ・どう人間へ届けるか）**」の 2 層に分ける。これが本設計の骨格である。

```
┌─ 計算層（deterministic, delivery 非依存）──────────────┐
│  入力: open Issue / Epic（gh / rtk 経由）             │
│  処理: 依存解決判定 → 優先度スコア → 工数見積もり → ランク付け │
│  出力: 候補 JSON（候補 N 件 + 推奨 1、§5）             │
│  性質: 副作用ゼロ。Issue を読むだけ。spawn / commit / PR を一切しない │
└──────────────────────────────────────────────┘
            ▲ 同一ツールを 3 つの delivery が共有する
┌─ 配達層（3 案、§6）─────────────────────────────┐
│  A. cron クラウド routine                            │
│  B. ローカル skill（窓口手動 / イベント起動）          │
│  C. dispatcher-loop 拡張（worker クローズ時オンデマンド） │
│  共通: 出力は必ず窓口に届く → 窓口が人間に提示 → 人間が選択 │
└──────────────────────────────────────────────┘
```

**設計上の含意**: 計算層を delivery から切り離すことで、3 案は排他選択ではなく「同じ計算ツールをどう起動するか」の違いに収斂する。推奨（[§6.4](#64-推奨)）は単一の primary delivery を選ぶが、計算層 1 本に集約しておけば後から別 delivery を足しても triage の意味論はぶれない。

計算層の実体は本設計では proposed tool `tools/work_discovery_scan.py`（[`tools/check_curate_threshold.py`](../../tools/check_curate_threshold.py) と同格の純計算 + JSON stdout ツール）とする。**本設計書ではインタフェースのみ定義し、実装はしない。**

## 4. triage 基準

候補 Issue の評価軸は assessment §7(b) が挙げる 3 つ —— **依存解決済み / 優先度 / 工数見積もり** —— を一次基準とし、補助軸を 2 つ加える。各軸は計算層が Issue メタデータから算出し、**実行ごとに同じ入力なら同じ出力になる（再現性）こと**を契約とする。ただし全軸が「メタデータの素直な読み取り」で決まるわけではない: `dependency` と `priority`（ラベル/milestone 由来）は決定的だが、`effort`・`parallelizable`・`unblocked_by_recent_merge` は**ヒューリスティック推定**を含む。後者は出力に不確実性フラグ（`*_estimated` / `signals[]`）を必ず添え、「機械推定であって断定ではない」ことを人間に明示する（[§4.4](#44-推定軸の不確実性明示)）。これにより propose-only（推定が外れても着手は人間判断）と監査性（どのシグナルで推定したか追える）を両立させる。

### 4.1 一次基準

| 軸 | 算出元（決定的シグナル） | 値域 |
|---|---|---|
| **依存解決済み** (`dependency`) | Issue body / コメントの `Blocked by #N` / `Depends on #N` / `Requires #N` / タスクリスト `- [ ] #N` を抽出し、参照先 Issue/PR が **全て closed か** を判定。`blocked` / `on-hold` ラベルは即 unresolved 扱い。 | `resolved` / `blocked`（blocked は候補から除外、理由付きで別枠表示） |
| **優先度** (`priority`) | ラベル（`priority:high` / `p0`〜`p2` 等）＞ milestone ＞ 経過日数（stale 加点 or 減点はポリシで選択）。ラベル体系が無いリポジトリでは milestone と更新日時のみで算出。 | `high` / `medium` / `low` |
| **工数見積もり** (`effort`) | `size:S/M/L` 等のラベルがあれば採用。無ければヒューリスティック（body 長 / acceptance criteria 個数 / 変更が想定される領域数）で `S/M/L` を**推定**する。推定値には必ず `effort_estimated: true` を付し、人間に「これは機械推定」と明示する。 | `S` / `M` / `L`（+ `effort_estimated` フラグ） |

### 4.2 補助軸（ランク付けに使う）

| 軸 | 用途 |
|---|---|
| **並列性** (`parallelizable`) | 他 Issue と独立に着手でき、空いた pane 枠を埋められるか。判定シグナル: 当該 Issue が他の open Issue を `Blocked by` / `Depends on` で参照して**いない**こと（= 依存グラフ上の葉）。[`CLAUDE.md`](../../CLAUDE.md) の proactive 方針「independent な open issue で並列性を埋める」と直結。空き pane があるときランクを上げる。**ヒューリスティック**（依存記法に現れない暗黙の競合は検知できない）→ `parallelizable_estimated` を添える。 |
| **直近マージ起点** (`unblocked_by_recent_merge`) | 直近マージで unblock された / 自然な follow-up になる Issue か。判定シグナル: 当該 Issue の `Blocked by` / `Depends on` 参照先に「直近 K 件のマージ済み PR がクローズした Issue/PR」が含まれる、または直近マージ PR が `Refs #N` 等で当該 Issue を参照している。[§8](#8-post-merge-proactive-next-dispatch-との統合) の格上げで最重要。post-merge トリガではこの軸が強く効く。**ヒューリスティック**（記法に現れない「概念的 follow-up」は検知できない）→ `unblocked_by_recent_merge_estimated` を添える。 |

### 4.3 ランク付けと「推奨 1 つ」の決定

候補集合（`dependency == resolved` のもの）を `(優先度, 直近マージ起点, 並列性適合, 工数の小ささ)` の辞書式でソートし、上位 N 件（既定 N=3、設定可能）を返す。**推奨 1 つ**は最上位だが、推奨理由を必ず添える（「なぜ他でなくこれか」を 1 文）。推奨が機械順位そのままになるのを避けるため、推奨選定理由は構造化フィールド（[§5](#5-出力フォーマット)の `recommendation.reason`）として出力し、窓口が人間に提示する際の根拠にする。

> **重要**: 計算層は「推奨」を出すが、これは**提案**であって決定ではない。最終選択は人間（[§7](#7-安全レール不変条件) INV-2）。ランク 1 位を自動着手することは設計上禁止。

### 4.4 推定軸の不確実性明示

`effort` / `parallelizable` / `unblocked_by_recent_merge` はヒューリスティック推定を含む（[§4.1](#41-一次基準) / [§4.2](#42-補助軸ランク付けに使う)）。これらは出力で必ず次を満たす:

- 推定値には対応する `*_estimated: true` フラグを付す（`effort_estimated` / `parallelizable_estimated` / `unblocked_by_recent_merge_estimated`）。
- 推定の根拠となった生シグナルを `signals[]` に列挙する（例: `"label:size:M"`, `"leaf in dependency graph"`, `"follow-up of #528 (merged)"`）。人間が「なぜそう推定したか」を追える。
- 人間可読レンダリング（[§5.2](#52-人間可読レンダリング窓口--人間)）では推定値に `(推定)` を付す。

これは「機械が断定した」と人間が誤読して着手判断を機構に明け渡すこと（認知的降伏、assessment §5）を防ぐための装置であり、INV-1 / INV-2 を運用面で支える。

## 5. 出力フォーマット

計算層は 2 つの表現を持つ: 機械可読 JSON（ツール stdout、delivery 層が消費）と、それを窓口が人間へ提示する人間可読テキスト（plain text / markdown 互換）。JSON が SoT、後者は派生レンダリング。

### 5.1 機械可読 JSON（ツール stdout）

[`tools/check_curate_threshold.py`](../../tools/check_curate_threshold.py) の「stdout は単一 JSON オブジェクト + exit code で分岐」契約に倣う。

```json
{
  "status": "candidates_found",
  "generated_for": "post_merge",
  "candidate_count": 1,
  "truncated_count": 0,
  "candidates": [
    {
      "issue": 531,
      "title": "...",
      "summary": "一行要約（body から機械抽出）",
      "dependency": "resolved",
      "blocking_refs": [],
      "priority": "high",
      "effort": "S",
      "effort_estimated": true,
      "parallelizable": true,
      "parallelizable_estimated": true,
      "unblocked_by_recent_merge": true,
      "unblocked_by_recent_merge_estimated": true,
      "rank": 1,
      "signals": ["label:priority:high", "leaf in dependency graph", "follow-up of #528 (merged)"]
    }
  ],
  "recommendation": {
    "issue": 531,
    "reason": "直近マージ #528 の自然な follow-up で依存解決済み・工数 S・空き pane を埋められる"
  },
  "excluded_blocked": [
    { "issue": 540, "blocking_refs": [537], "note": "#537 が open のため除外" }
  ]
}
```

（上記 `candidates` は 1 件のみ示した例。実際は `candidate_count` 件が `rank` 昇順で並ぶ。JSON はコメントを許さないため省略記法は使わない。）

- `status`: `candidates_found` / `no_candidates`（候補ゼロ）/ `error`。
- `candidate_count`: `candidates[]` の実件数。`truncated_count`: N 件上限で `candidates[]` から落とした「依存解決済みだが順位外」の候補数（**必須フィールド**。`0` でも省略しない。サイレント truncation を禁じるため）。
- exit code で delivery 側が分岐する。[`tools/check_curate_threshold.py`](../../tools/check_curate_threshold.py) に倣い、**`1` を意味付けに使わない**（Python が未捕捉例外時に既定で返す exit `1` と衝突し、scan のクラッシュが「候補なし」に誤読されて error が窓口に届かなくなるのを防ぐ）。割り当ては `0` = 候補なし（`no_candidates`）、`10` = 候補あり（`candidates_found`）、`2` = error。delivery 層は JSON パース失敗に依存せず exit code で挙動を決める（curator threshold ツールと同方針）。
- `excluded_blocked` は「依存未解決で除外した Issue」を理由付きで残す。**サイレント truncation をしない**（`truncated_count` で順位外候補の存在も、`excluded_blocked` で依存除外も、ともに人間が監査できるようにする）。

### 5.2 人間可読レンダリング（窓口 → 人間）

窓口が人間に提示する形。proactive next-dispatch の現行慣行（候補 2〜4 件 + 推奨 1、番号で即決）と互換にして、人間の操作を変えない。

```text
次の仕事候補（triage 結果・提案のみ / 着手はあなたの判断です）:

1. [推奨] #531 ...（優先度 high / 工数 S(推定) / 依存解決済み / 並列可）
   └ 直近マージ #528 の follow-up。空き pane を埋められます。
2. #533 ...（優先度 medium / 工数 M(推定) / 依存解決済み）
3. #529 ...（優先度 medium / 工数 S / 依存解決済み / 並列可）

除外（依存未解決）: #540（#537 が open のため）

着手するものを番号で指定してください。着手判断後に /org-delegate を回します。
```

- 推奨は先頭に `[推奨]` を付け 1 件だけ。
- 工数が機械推定なら `(推定)` を必ず付す。
- 「提案のみ / 着手はあなたの判断」を毎回明示する（INV-1 の運用上の現れ）。
- 除外枠を必ず見せる（監査性 + 「全部見たうえで N 件」という安心）。

## 6. delivery 方式 3 案比較

計算層（[§3](#3-設計の二層構造)）は同一。違いは **誰が・いつ起動し・どう窓口へ届けるか**。

### 6.1 案 A: cron クラウド routine

`schedule` 系のクラウド routine（cron で走る headless cloud agent）に triage scan を載せる。

- **利点**: 組織セッションが起動していなくても時間ベースで走る真の自律発見。マシンが落ちていても回る。
- **欠点（採用を阻む）**:
  1. **窓口境界の侵犯**: クラウド routine は組織の renga タブ外で走り、窓口セッションへ in-band で結果を注入できない。結果を人間へ届けるには GitHub（Issue コメント / triage Issue）か通知へ**直接**書くことになり、「窓口 = 唯一の人間接点」を破る。窓口経由に戻すには結局ローカルへ橋渡しする層が要り、cron の利点が相殺される。
  2. **ライブ状態が見えない**: 空き pane 数・in-flight worker・`.state/` / state.db はローカルにあり、クラウドからは観測できない。`parallelizable` / 空き枠充当の判定（[§4.2](#42-補助軸ランク付けに使う)）が機能しない。
  3. **運用の不透明さ + 課金面**: 検出から提示までが組織セッションから切り離れて走り、監査・介入がしづらい。加えて headless / Agent SDK 系の別クレジット課金枠に載る可能性がある（コストは本組織の方針上 deciding factor ではないが、上記 1・2 と合わせると採用理由が無い）。
- **判定**: **不採用**。窓口境界とライブ状態可視性の 2 点が致命的。

### 6.2 案 B: ローカル skill

窓口がローカルで起動する skill（例: 仮称 `/work-discovery`）。skill が計算層ツールを呼び、出力を窓口が人間へ提示する。起動主体は**窓口に限定する**: 委譲済みワーカーが自タスク外の次仕事探索を起動すると、「1 worker = 1 task = 1 scope」と「別件は Step 0 から [`/org-delegate`](../../.claude/skills/org-delegate/SKILL.md)」（[`CLAUDE.md`](../../CLAUDE.md)）を崩すため。

- **利点**: 窓口境界を自然に保つ（窓口が起動し窓口が提示）。ライブ状態（空き pane）をローカルで見られる。手動 on-demand と相性が良い。
- **欠点 / 留意**:
  1. **トリガが受動的**: 窓口が「いつ走らせるか」を意識する必要がある。常駐 `/loop` で時間起動すると、変化の無い日に raw ログ・提示を汚す副作用が出る（`skill-audit` が「時間ベースの /loop では起動しない」とした教訓と同根）。よって常駐 /loop は避け、**イベント起動（post-merge / 手動）に限る**べき。
  2. **scan を誰が実行するか**: 窓口が直接 scan すると「秘書は調査しない」境界に触れうる。これは scan を**決定的ツール**に閉じ込めることで回避する（[§1](#1-背景と確定制約本設計が覆さない前提) 制約 2）。深掘りが要る候補は人間ゲート後にワーカー委譲。
- **判定**: **採用（手動エントリとして）**。ただし単独だと「いつ走らせるか」問題が残るため、定常トリガは案 C に委ねる。

### 6.3 案 C: dispatcher-loop 拡張

既に常駐している dispatcher の監視 `/loop`（worker 監視）と worker クローズ時のオンデマンド spawn 機構（[`tools/check_curate_threshold.py`](../../tools/check_curate_threshold.py) / [`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md)）を拡張し、**worker クローズ = pane 枠が空いた瞬間**に triage scan を走らせ、候補 JSON を窓口へ peer message で送る。

- **利点**:
  1. **既存常駐ループの再利用**: 新規常駐プロセスを増やさない。on-demand curator と全く同じ「worker クローズ時に閾値/条件チェック → 条件成立時のみ起動」パターンに乗る（実装・運用の認知コストが既知）。
  2. **トリガが意味的に正しい**: pane が空く = 次の仕事を入れられるタイミング、で発火する。idle 化検出とも自然に結びつく。
  3. **ライブ状態を持つ**: dispatcher は pane トポロジ・在席 worker を把握しており、`parallelizable` / 空き枠充当の判定材料がある。
- **欠点 / 留意**:
  1. **dispatcher の役割拡張**: dispatcher は「窓口の DELEGATE を代行・人間と直接対話しない」のが原則（[`.dispatcher/CLAUDE.md`](../../.dispatcher/CLAUDE.md)）。triage は新責務だが、dispatcher は**計算ツールを実行して候補 JSON を窓口へ転送するだけ**で、人間へは触れない・着手判断もしない。「dispatcher → 窓口 → 人間」の経路を守る限り境界は破れない。
  2. **発火が worker クローズに依存**: workers がゼロで完全 idle の間は発火しない。これは案 B（手動）で補完する。
- **判定**: **採用（定常トリガとして）**。

### 6.4 推奨

**推奨: 案 C を定常トリガ、案 B を手動オーバーライドとし、両者が同一の計算層ツールを共有する構成。案 A は不採用。**

| | 窓口境界 | ライブ状態可視 | トリガ品質 | 運用コスト | 採否 |
|---|---|---|---|---|---|
| A. cron クラウド | ✕ 破る | ✕ 見えない | ◯ 時間自律 | △ 別枠課金/不透明 | **不採用** |
| B. ローカル skill | ◯ | ◯ | △ 受動/手動 | ◯ | **採用（手動）** |
| C. dispatcher-loop 拡張 | ◯（窓口経由維持） | ◯ | ◯ イベント駆動 | ◯ 既存ループ再利用 | **採用（定常）** |

根拠: 計算層を 1 本に集約してあるので「C で定常起動 + B で手動起動」は同じツールの 2 つの入口にすぎず、二重実装にならない。C は on-demand curator という実証済みパターンの再利用で、窓口境界・ライブ状態・トリガ品質の 3 点を同時に満たす唯一の案。B は idle 時や任意タイミングの抜け穴を塞ぐ補完。A は窓口境界とライブ状態の 2 点で構造的に不適合。

> この推奨は assessment §7(b) の「提案まで自動・着手判断は人間維持」と完全に整合する: **発見（scan・ランク付け・提示）は自動化、判断（選択・着手）は人間**。

## 7. 安全レール（不変条件）

以下を本機構の**不変条件 (invariant)** とする。delivery 方式・将来の拡張にかかわらず破ってはならない。

- **INV-1 — propose-only / 提案で停止**: 機構の出力はランク付き候補リストのみ。生成後は**停止する**。spawn・delegate・ブランチ作成・commit・PR・Issue への書き込みのいずれも行わない。計算層は read-only（Issue を読むだけ・副作用ゼロ）。
- **INV-2 — 着手判断は人間ゲート必須**: 候補の選択は人間のみが行う。選ばれた候補は**既存の [`/org-delegate`](../../.claude/skills/org-delegate/SKILL.md) の Step 0 から**通常委譲フローに入る。discovery 機構が org-delegate を自分で呼ぶことは禁止。ランク 1 位（推奨）の自動着手も禁止。
- **INV-3 — 自動 PR / 自動 commit をしない**: 本機構は**ソースツリー・Issue・PR・git（commit / branch / push）を一切変更しない**。triage 結果をソースにコミットして残す運用にする場合も、それは別途人間判断による別タスクであり、機構が自動で行わない。
  - **例外（=変更ではなく組織状態の記帳）**: 通常の運用記帳である `.state/state.db` の events table への journal イベント追記（[§7.1](#71-不変条件の検証可能性)）は本 INV の対象外。これは他の全ロールが日常的に行う bookkeeping と同格で、git 履歴・ソース・GitHub を変えない。**read-only な計算層ツール自体は state.db にも書かない**（[§7.1](#71-不変条件の検証可能性) 「副作用ゼロの担保」）。journal 記帳を行うのは delivery 層（窓口 / dispatcher）であって計算層ツールではない、という分離を守る。
- **INV-4 — 窓口 = 唯一の人間接点**: triage 結果は必ず窓口に届き、窓口が人間へ提示する。discovery 機構（dispatcher / cron / ツール）が人間または GitHub 上の人間可視面へ直接到達してはならない（案 A 不採用の直接的根拠）。
- **INV-5 — 実作業は全委譲 / 秘書は調査しない**: scan は決定的ツール実行であり「調査」ではない。候補の実現性深掘り・設計が必要なら、それは人間ゲートを通った後の委譲ワーカータスクとして扱う。窓口・dispatcher が候補の中身を自前で調査・実装しない。

> これら 5 つは assessment §5-1 / §7(b) が要求する「発見の自律性は上げるが人間をループ頂点から外さない」を機械的に保証する装置である。とくに **INV-1 + INV-2 が「提案まで / 人間ゲート」の本体**であり、INV-4 が案 A を排除する根拠、INV-5 が理解負債（§5-2）を増やさない歯止めになる。

### 7.1 不変条件の検証可能性

- **監査ログ**: scan 実行・候補件数・推奨を journal イベント（proposed kind 例: `work_discovery_scanned` / payload に `candidate_count` / `recommendation_issue` / `trigger`）として残し、「いつ・何件・何を推奨したか」を後追いできるようにする。記帳するのは **delivery 層（窓口 / dispatcher）であって read-only な計算層ツールではない**（INV-3 例外の分離）。[`docs/journal-events.md`](../journal-events.md) のとおり events の SoT は `.state/state.db` の events table であり、emit は DB-routed helper（`tools/journal_append.sh` / `tools/journal_append.py`）経由で行う（旧 `.state/journal.jsonl` 直書きや直接 DB INSERT はしない）。**proposed イベントの台帳追記と実体配線は本設計のスコープ外**（別タスク）。
- **副作用ゼロの担保**: 計算層ツールは `gh issue list` / `rtk gh issue view` 等の**読み取り API のみ**を使い、書き込み系 API・git 操作を一切呼ばないことをツールの契約（および将来のユニットテスト）で固定する。

## 8. post-merge proactive-next-dispatch との統合

現行の post-merge proactive next-dispatch（[`CLAUDE.md`](../../CLAUDE.md) の方針 + 運用メモリ）は、窓口が PR マージ後に `gh issue list` を即興で叩いて候補を出す。これを **triage 結果ベースに格上げ**する。

### 8.1 統合方法

1. **トリガ点の合流**: PR マージ → post-merge cleanup → dispatcher の CLOSE_PANE 確認、までが終わった時点（[`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md) の worker クローズと同じ瞬間）を triage scan のトリガにする。案 C の worker クローズトリガと自然に重なる。
2. **即興 → 構造化**: 窓口が自前で `gh issue list` を叩く代わりに、計算層ツールの候補 JSON（[§5.1](#51-機械可読-jsonツール-stdout)）を受け取り、[§5.2](#52-人間可読レンダリング窓口--人間) の形で人間へ提示する。判定基準（依存解決済み / 優先度 / 工数）が明文化され、再現性・監査性が付く。
3. **直近マージ起点の優先**: post-merge コンテキストでは `unblocked_by_recent_merge`（[§4.2](#42-補助軸ランク付けに使う)）軸を強く効かせ、「直近マージの自然な follow-up」「直近マージで unblock された Issue」（運用メモリが挙げる proactive 候補パターン）を上位に出す。`generated_for: "post_merge"` を JSON に載せて文脈を明示する。
4. **人間操作の不変**: 提示形式・「番号で即決」の体験は現行と互換に保つ（[§5.2](#52-人間可読レンダリング窓口--人間)）。人間から見た変化は「候補の根拠が明示され、除外理由も見える」点のみ。

### 8.2 格上げ後の position

| | 現行 proactive next-dispatch | 格上げ後 |
|---|---|---|
| 候補生成 | 窓口の即興 `gh issue list` | 計算層ツール（基準明文化） |
| 判定根拠 | 暗黙 | `dependency` / `priority` / `effort` + signals |
| 除外の可視化 | 無し | `excluded_blocked` を提示 |
| トリガ | post-merge のみ | post-merge（案 C と合流）+ 手動（案 B） |
| 着手 | 人間（変更なし） | 人間（変更なし） |
| 監査 | 無し | journal `work_discovery_scanned` |

> 統合のキモ: proactive next-dispatch を**廃止・置換するのではなく、その「候補生成」部分だけを即興から triage 機構へ差し替える**。窓口が人間へ提示し人間が選ぶという外形は完全に維持される（INV-2 / INV-4）。

## 9. 段階導入と検証（提案）

実装する場合の推奨順序（本設計書では計画のみ。各 Phase の実装は別タスク）。

1. **Phase 1 — 計算層**: `tools/work_discovery_scan.py`（read-only、候補 JSON stdout、exit code 分岐、ユニットテスト）。これ単体は副作用ゼロで、手動 `python3 tools/work_discovery_scan.py` で出力検証できる。
2. **Phase 2 — 案 B 手動エントリ**: 窓口が手動起動して提示する経路。skill 追加は `.claude/` 編集を伴うため、本ワーカーのスコープ外（別タスク）。
3. **Phase 3 — 案 C 定常トリガ**: worker クローズ時に scan を起動し窓口へ転送する配線（[`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md) / [`.dispatcher/CLAUDE.md`](../../.dispatcher/CLAUDE.md) の prose 更新を伴う）。
4. **Phase 4 — post-merge 統合**: §8 の格上げ。proactive next-dispatch の候補生成を triage 出力へ差し替え。

各 Phase は INV-1〜INV-5 を破らないことをレビューゲートで確認する。とくに「read-only か」「人間ゲートを飛ばしていないか」を Phase ごとに検証する。

## 10. スコープ外 / 将来課題

- **着手の自動化**: 本設計の対象外（INV-1 / INV-2 で恒久的に禁止）。assessment §5 が言うとおり「人間をループ頂点に残す」のが本組織の確定方針。
- **クロスリポジトリ triage**: 複数リポジトリ（runtime / ja / renga 等）横断の依存解決は本設計では単一リポジトリ前提。将来拡張。
- **工数見積もりの高度化**: §4.1 の effort はヒューリスティック。過去 PR の実工数からの学習等は将来課題。
- **`.claude/` skill・`.dispatcher/` prose・`tools/` の実体実装**: 本ワーカーは DESIGN ONLY。すべて別タスク。
- **proposed journal イベントの台帳追記と配線**: `work_discovery_scanned` 等の [`docs/journal-events.md`](../journal-events.md) 追記・emit 配線は実装タスク側。

## 11. 未解決の論点（実装前に人間判断が要る点）

1. **N の既定値**: 候補上限 N=3 を既定としたが、空き pane 数に応じて可変（空き枠 = N）にするか固定にするか。
2. **優先度ラベル体系**: 本リポジトリの Issue が `priority:*` / `p0..p2` 等のラベル体系をどこまで持つか未確認。無い場合 §4.1 の priority 算出は milestone + 更新日時に縮退する。実装前に実ラベル分布の確認が要る。
3. **依存記法の揺れ**: `Blocked by` / `Depends on` / タスクリスト等、本リポジトリの実 Issue がどの記法を使っているか。抽出パターンは実データで較正が要る（過剰一致で blocked 誤判定 → 候補から不当除外、を避ける）。
4. **idle 時のトリガ**: workers ゼロの完全 idle 時、案 C は発火しない。案 B 手動以外に「窓口起動時に 1 回 scan」等の軽いトリガを足すかは運用判断。
