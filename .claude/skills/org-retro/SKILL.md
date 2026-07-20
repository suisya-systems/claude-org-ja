---
name: org-retro
description: >
  委譲プロセスの振り返り。ワーカーへの作業委譲が完了したとき、
  委譲の進め方自体を振り返り、プロセス改善の知見を記録する。
  さらに、完了タスクの作業パターンをwork-skillとして蓄積すべきか判断する。
  実作業の技術的な振り返りはワーカーが自動的に行うため、ここでは扱わない。
effort: medium
allowed-tools:
  - Read
  - Write
  - Edit
  - mcp__org-broker__send_message
---

# org-retro: 委譲プロセスの振り返り

ワーカーへの委譲が完了した後、委譲プロセス自体を振り返り改善する。
加えて、完了タスクの作業パターンがwork-skillとして再利用可能か判断する。

**注意**: 実作業の技術的な知見（はまりポイント、API の癖等）はワーカーが CLAUDE.md の指示に従い
自動的に `knowledge/raw/` に記録する。ここでは扱わない。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節。）

## Step 1: 委譲プロセスの振り返り

以下を整理する:
- **タスク分解は適切だったか**: 粒度が大きすぎ/小さすぎなかったか
- **指示は明確だったか**: ワーカーが迷わず作業できたか、質問が多くなかったか
- **プロジェクト選定は正しかったか**: 正しいディレクトリで作業できたか
- **並列度は適切だったか**: ワーカー数が多すぎ/少なすぎなかったか
- **完了報告は十分だったか**: ワーカーからの報告で人間に説明するのに足りたか

## Step 2: 改善すべき知見の判断

以下の基準で「記録すべきか」を判断する:

**記録する**:
- 同じ種類の委譲で再び遭遇しそうなパターン
- 指示テンプレートの改善につながる気づき
- プロジェクト固有の制約で次回も影響しそうなもの
- ワーカーの振り返り記録が不十分/過剰だった場合の改善点

**記録しない**:
- タスク固有の一度きりの問題
- ワーカーが既に技術的知見として記録済みのこと

## Step 3: 記録

知見がある場合、以下のパスにファイルを作成する:

- パス: `knowledge/raw/{YYYY-MM-DD}-delegation-{topic}.md`
- `{topic}` は英語 kebab-case（例: `delegation-task-granularity`, `delegation-frontend-instructions`）
- プレフィックスに `delegation-` を付けて、ワーカーの技術的知見と区別する

### ファイルフォーマット

`.claude/skills/org-curate/references/knowledge-standards.md` の「記録フォーマット」を参照すること。

## Step 3.5: 実行プロファイルの成長則（プロジェクト台帳）

委譲で得た知見のうち「実行設定」に当たるものは、プロジェクト台帳
`registry/projects/<slug>/` の実行プロファイルへ育てる。三分を維持すること:

- **スキル** = 手順書（何をどの順でやるか）
- **プロファイル** = 実行設定（どの検証深度・どの commit prefix・どのブランチ様式で走らせるか）
- **ノート** = 知見（何が起きるか・なぜそうなるか）

### 成長則: 同一類型の 2 回目の実測でプロファイル化する

先回りで書かない。`skill-eligibility-check` の `raw_reappearance` と同じ規律である。

- **1 回目**: プロファイルを作らない。`registry/projects/<slug>/charter.md` に「観測済み・未プロファイル」として類型名だけ残す。
- **2 回目**: 同じ類型で実行設定が前回と同じ形に収束したことを確認してから
  `registry/projects/<slug>/profiles/<class>.toml` を作る。収束していない（毎回違う）なら、それは
  プロファイル化できる軸ではないので作らない。
- **書くのは実測で変動した軸だけ**。使われないまま形骸化したプロファイルは削除してよい。

### プロファイルに入れてはいけないもの

- **承認に相当するもの**: マージ事前承認の類は持たせない。プロファイルが設定するのは実行であって承認ではない。常設スコープ契約は人間承認つきの独立文書とし、参照のみに留める。
- **Lead / PR ゲートの計画コンテキスト**: 例「マージ直列制約」「並列度」「CI 監視の扱い」。1 タスクを実行するワーカーは観測も行動もできない。charter の散文か、窓口側の段取りに置く。
- **まだ配線されていない軸**: `model` / `codex_round_max` / `pr_shape` / `codex_review` は第 1 段では受け口が無い。書くなら `[profile]` テーブルに置くこと（警告つきで受理され、「黙って何もしない設定」にはならない）。

軸と配管の対応表・埋め込み仕様は [`docs/design/project-dossier.md`](../../../docs/design/project-dossier.md) を参照。

### 書き込み主体

台帳は `registry/` 配下にあり、`registry/` の所有者は窓口である
（[`docs/contracts/role-contract.md`](../../../docs/contracts/role-contract.md) § Role: secretary）。
プロファイルと charter の更新は窓口が行う。キュレーターは `registry/` に書けないため、
キュレーター経路でプロファイルを更新しないこと。

## Step 4: work-skill 化の判定

完了したタスクの作業パターンについて `skill-eligibility-check` を呼び出し、
work-skill として蓄積すべきか判定する。

判断基準の実体は `.claude/skills/skill-eligibility-check/references/signals.md` に集約されており、
org-retro と org-curate の両方が同じ基準を参照する（判定の乖離を防ぐため）。

### Step 4.1: skill-eligibility-check を呼ぶ

以下の入力を組み立てて呼び出す:

```yaml
context: post_retro
pattern_name: <推定される skill 名、kebab-case>
summary: <何を再利用できるかの 1-2 文>
task_ids: [<今回の task_id>]
raw_files: <ワーカーが記録した knowledge/raw/ のパス配列>
steps_outline:
  - <主要手順 1>
  - <主要手順 2>
  - ...
trigger_description: <このパターンが適用される状況>
decision_criteria: <判断基準や閾値>
output_format: <成果物の構造>
```

スキルは 5 シグナルで採点し、`decision` を返す:
- `skill_recommend`（3 点以上）
- `candidate_queue`（2 点）
- `curated_only`（1 点以下）

`skill_recommend` の場合は `knowledge/skill-candidates.md` への追記もスキル側で実施される。

### Step 4.2: decision に応じて分岐

#### decision == skill_recommend

キュー追記（`knowledge/skill-candidates.md` への追記はスキル側で実施済み）に留め、
人間への即時提案はせず**黙って次に進む**。人間への問い合わせは、候補キューの pending が
5 件以上（N=5）に達した時点で窓口が行うバッチ問い合わせ、または `/skill-audit` の発火時のみ。
一次参照は [`knowledge/skill-candidates.md`](../../../knowledge/skill-candidates.md) 冒頭の
運用ルール（Issue #68 方針）。

以下 1〜3 は即時には実行しない。バッチ問い合わせ（または `/skill-audit`）で人間が各候補を
判断した時点で**窓口が実行する**処理フローとしてここに温存する:

1. 人間が承認した場合:
   - **skill ファイルの作成・編集は窓口（secretary）が直接行わない**。Set E §2.4 (Q7) の批准に従い、
     skill-promotion は委譲タスクとして `org-delegate` 経由でワーカーに渡す。
   - 窓口は `org-delegate` を起動し、role `claude-org-self-edit` のワーカータスクを生成する。
     指示には以下を含める:
     - 対象 skill 名 `{skill-name}` と書き込み先 `.claude/skills/{skill-name}/SKILL.md`
     - テンプレート参照: `.claude/skills/org-retro/references/work-skill-template.md`
     - 抽出元（ワーカーの成果物・raw 知見ファイルのパス）と、
       タスク固有の値をプレースホルダーへ置換する旨
     - skill-promotion 委譲であること（Set A worker write-surface の carve-out 対象）
   - ディスパッチャー / 窓口は `.claude/skills/{skill-name}/` および `knowledge/skill-candidates.md` への
     直接書き込みを行わない。Set E §1.4 / §2.4 に従い、`skill-candidates.md` の status transition
     （`approved` への遷移と `決定日` の記入）も同じ委譲ワーカーの責務とし、指示にその旨を含める。
2. 人間が却下した場合:
   - 理由を `knowledge/raw/` に記録し、次回の判断に活かす
   - `knowledge/skill-candidates.md` の status を `rejected` に更新し却下理由を追記する作業も
     ワーカーへの委譲（`org-delegate`）経由で行う。窓口・ディスパッチャーは直接編集しない
     （Set E §1.4 の owner 定義に従う）。
3. 人間が「既存 skill に統合」を選択した場合（terminal status `merged-into-{existing-skill}`）:
   - 統合先となる既存 skill を特定し、`org-delegate` で skill-promotion ワーカーに以下を委譲する:
     既存 `.claude/skills/{existing-skill}/SKILL.md` への取り込み編集、および
     `knowledge/skill-candidates.md` 該当エントリの status を `merged-into-{existing-skill}` に
     更新（`統合先` フィールドに既存 skill 名を記入）。
   - 新規 skill ファイルは作成しない。窓口・ディスパッチャーは直接編集しない。

#### decision == candidate_queue

候補止まり。次回同パターンが raw に再出現すれば raw_reappearance シグナルが立つため、
この段階では skill 化しない。`knowledge/raw/` への技術的知見記録は通常どおり（ワーカー記録済みならスキップ）。

#### decision == curated_only

`knowledge/raw/` への技術的知見記録で十分（ワーカーが既に記録している場合はスキップ）。
報告は不要。

## Step 5: 報告

人間に簡潔に報告する:
- 知見を記録した場合: 「委譲プロセスについて{topic}の学びを記録しました」
- `skill_recommend` / `candidate_queue` / `curated_only` のいずれの場合も: 報告不要（黙って次に進む）。
  `skill_recommend` はキュー追記のみで完結し、人間への問い合わせは
  [`knowledge/skill-candidates.md`](../../../knowledge/skill-candidates.md) 冒頭の運用ルールに従い
  pending ≥5 のバッチ問い合わせまたは `/skill-audit` 発火時に窓口が行う
