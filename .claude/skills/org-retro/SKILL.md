---
name: org-retro
description: >
  委譲プロセスの振り返り。ワーカーへの作業委譲が完了したとき、
  委譲の進め方自体を振り返り、プロセス改善の知見を記録する。
  さらに、完了タスクの作業パターンをwork-skillとして蓄積すべきか判断する。
  実作業の技術的な振り返りはワーカーが自動的に行うため、ここでは扱わない。
---

# org-retro: 委譲プロセスの振り返り

ワーカーへの委譲が完了した後、委譲プロセス自体を振り返り改善する。
加えて、完了タスクの作業パターンがwork-skillとして再利用可能か判断する。

**注意**: 実作業の技術的な知見（はまりポイント、API の癖等）はワーカーが CLAUDE.md の指示に従い
自動的に `knowledge/raw/` に記録する。ここでは扱わない。

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

1. 人間に提案する:
   ```
   [work-skill 提案] このタスクの作業パターンはwork-skillとして記録すると再利用できそうです。
   - スキル名案: {proposed_skill_name}
   - 理由: {matched_signals} （合計 {score}/5 点）
   - 概要: {何を再利用できるか}

   記録しますか？
   ```
2. 人間が承認した場合:
   - **skill ファイルの作成・編集は窓口（secretary）が直接行わない**。Set E §2.4 (Q7) の批准に従い、
     skill-promotion は委譲タスクとして `org-delegate` 経由でワーカーに渡す。
   - 窓口は `org-delegate` を起動し、role `claude-org-self-edit` のワーカータスクを生成する。
     指示には以下を含める:
     - 対象 skill 名 `{skill-name}` と書き込み先 `.claude/skills/{skill-name}/SKILL.md`
     - テンプレート参照: `.claude/skills/org-retro/references/work-skill-template.md`
     - 抽出元（ワーカーの成果物・raw 知見ファイルのパス）と、
       タスク固有の値をプレースホルダーへ置換する旨
     - skill-promotion 委譲であること（Set A worker write-surface の carve-out 対象）
   - ディスパッチャー / 窓口は `.claude/skills/{skill-name}/` への直接書き込みを行わない。
     実ファイルの作成・編集は委譲先ワーカーが担当する。
   - 窓口は `knowledge/skill-candidates.md` の該当エントリの status を `approved` に更新し決定日を記入する
     （これは委譲発行に伴うステータス管理であり、skill ファイル本体の編集ではない）。
3. 人間が却下した場合:
   - 理由を `knowledge/raw/` に記録し、次回の判断に活かす
   - `knowledge/skill-candidates.md` の該当エントリの status を `rejected` に更新し却下理由を追記

#### decision == candidate_queue

候補止まり。次回同パターンが raw に再出現すれば raw_reappearance シグナルが立つため、
この段階では skill 化しない。`knowledge/raw/` への技術的知見記録は通常どおり（ワーカー記録済みならスキップ）。

#### decision == curated_only

`knowledge/raw/` への技術的知見記録で十分（ワーカーが既に記録している場合はスキップ）。
報告は不要。

## Step 5: 報告

人間に簡潔に報告する:
- 知見を記録した場合: 「委譲プロセスについて{topic}の学びを記録しました」
- work-skill 化を提案する場合: Step 4.2 の `skill_recommend` フォーマットで提案
- `candidate_queue` / `curated_only` の場合: 報告不要（黙って次に進む）
