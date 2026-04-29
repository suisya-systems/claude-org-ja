---
name: skill-eligibility-check
description: >
  作業パターンを skill 化すべきか判定する共通スキル。org-retro と org-curate から呼ばれ、
  「skill 化推奨 / 候補止まり / curated ノートのまま」の 3 値と根拠を返す。
  自動 skill 化はせず、推奨は knowledge/skill-candidates.md に追記し、
  窓口が候補キューが溜まった時点でバッチで人間に問い合わせる二段構え。
---

# skill-eligibility-check: skill 化判定

作業パターンが新規 work-skill として切り出す価値があるかを 5 シグナルで採点し、
3 値（skill_recommend / candidate_queue / curated_only）で返す。
このスキル自体は skill 生成も人間問い合わせもしない — 判定専用。

## なぜ共通スキルか

判定基準が org-retro と org-curate の 2 箇所に分散すると必ず乖離するため。
このスキルを single source of truth として双方から呼ぶ。

## 入力契約

呼び出し元は以下の構造を渡す:

```yaml
context: post_retro | curation
pattern_name: <kebab-case の候補 skill 名>
summary: <何を再利用できるかの 1-2 文>
task_ids: [<関連タスク ID>, ...]          # optional。post_retro は通常 1 件、curation は空配列可
raw_files: [<knowledge/raw/ のファイルパス>, ...]
steps_outline:                              # 主要手順の箇条書き
  - <step 1>
  - <step 2>
  - ...
trigger_description: <このパターンが適用される状況を言語化できるか / できないなら空>
decision_criteria: <判断基準や閾値があるか / できないなら空>
output_format: <成果物の再利用可能フォーマット / なければ空>
```

**必須は `context` / `pattern_name` / `summary` / `raw_files` / `steps_outline` のみ**。
`task_ids` は raw ノートの標準スキーマに含まれないため curation 文脈では空配列でよい。
`trigger_description` / `decision_criteria` / `output_format` は採点対象そのもので、
空のまま渡すと該当シグナルが 0 点になる。

## Step 1: 5 シグナル評価

`references/signals.md` の定義に従い、各シグナル 0 点 / 1 点で採点する。

| シグナル | 1 点の条件 |
|---|---|
| raw_reappearance | 同パターンの raw 記録が 3 件以上ある |
| steps_complexity | `steps_outline` が 3 項目以上かつ非自明な判断を含む |
| trigger_articulable | `trigger_description` が具体的かつ検索可能な語彙で書ける |
| criteria_articulable | `decision_criteria` に定量閾値または分類ルールがある |
| reusable_output | `output_format` が他タスクで転用可能な構造を持つ |

詳細な判定手順は `references/signals.md` を参照。

## Step 2: 合計点から 3 値に分岐

| 合計点 | 判定 | 意味 |
|---|---|---|
| 3 点以上 | `skill_recommend` | skill 化推奨。候補キューに追加 |
| 2 点 | `candidate_queue` | 候補止まり。raw に追記し次回の raw_reappearance を待つ |
| 1 点以下 | `curated_only` | curated ノートのままで十分 |

閾値を 3 点に置いた根拠: org-retro 旧版の「2 つ以上で推奨」よりやや保守的にし、
候補止まり層を明示的に作ることで、「skill 検索面のノイズ」を予防する。

## Step 3: 出力

以下の構造を呼び出し元に返す:

```yaml
decision: skill_recommend | candidate_queue | curated_only
score: 0-5
matched_signals: [<1 点が付いたシグナル名>, ...]
rationale: <1-2 行の根拠文>
proposed_skill_name: <pattern_name>    # skill_recommend / candidate_queue のみ
```

## Step 4: 候補キュー書き込み（skill_recommend の場合のみ）

`knowledge/skill-candidates.md` に以下のエントリを追記する。
`candidate_queue` / `curated_only` の場合は書き込まない。

```markdown
### {YYYY-MM-DD} {pattern-name}
- **判定スコア**: {score}/5
- **該当シグナル**: {matched_signals}
- **根拠**: {rationale}
- **関連タスク**: {task_ids、curation 文脈では空 "[]" 可}
- **関連 raw ファイル**: {raw_files}
- **呼び出し元**: {context}
- **提案 skill 名**: {proposed_skill_name}
- **status**: pending
- **決定日**: 未定
- **却下理由**: （status が rejected に遷移したとき記入、それ以外は省略）
- **統合先**: （status が merged-into-* のとき記入、それ以外は省略）
```

書き込み後も呼び出し元には出力 YAML を返す（キュー追記は副作用として完了）。

## 呼び出し元の責務

このスキルは判定とキュー追記だけを行う。以降のアクションは呼び出し元の責務:

- **org-retro（post_retro）**:
  - `skill_recommend` → 人間に提案し、承認なら work-skill-template.md で skill 新規作成
  - `candidate_queue` → raw/ に技術的知見のみ記録
  - `curated_only` → raw/ への記録で十分（報告不要）

- **org-curate（curation）**: decision 値に関わらず通常の Step 3 で curated/ 統合 + Step 4 で `<!-- curated -->` 付与を行う（skill 化と curated ノート化は両立）。
  - `skill_recommend` → 候補キュー追記のみ（skill 側で自動書き込み済み）。
    人間への問い合わせは窓口の役目で、キュー閾値 N=5 に達したら窓口が行う
  - `candidate_queue` → 追加アクションなし（次回の raw_reappearance を待つのはシグナル上の話であり、raw を未整理のまま残すことではない）
  - `curated_only` → 追加アクションなし

## 重複呼び出しの扱い

同一 `pattern_name` が既に `knowledge/skill-candidates.md` に `status: pending` で
存在する場合、Step 4 では新規エントリを追加せず既存エントリの `関連タスク`・`関連 raw ファイル` を
追記マージする。status が `approved` / `rejected` / `merged-into-*` になっているエントリは
履歴として保持し、新しいエントリを別日付で追加する（過去の却下理由を失わない）。

## スキルを呼ばないケース

- ワーカーが単に「便利だった関数」をメモするだけ → これは `knowledge/raw/` の記録で十分で、
  本スキルの入力 5 項目（特に `steps_outline` と `trigger_description`）が埋まらない
- 一度きりの調査・デバッグ → パターン化する見込みがなく、判定コストの無駄
