---
name: skill-eligibility-check
owner: curator
description: >
  作業パターンを skill 化すべきか判定する共通スキル。org-retro と org-curate から呼ばれ、
  「skill 化推奨 / 候補止まり / curated ノートのまま」の 3 値と根拠を返す。
  自動 skill 化はせず、推奨は machine-local な knowledge/skill-candidates.local.md に追記し、
  窓口が候補キューが溜まった時点でバッチで人間に問い合わせる二段構え。
effort: low
allowed-tools:
  - Read
  - Edit
  - Write
---

# skill-eligibility-check: skill 化判定

作業パターンが新規 work-skill として切り出す価値があるかを 5 シグナルで採点し、
3 値（skill_recommend / candidate_queue / curated_only）で返す。
このスキル自体は skill 生成も人間問い合わせもしない — 判定専用。
org-retro と org-curate の両方がこのスキルを呼ぶ（理由は [`references/rationale.md`](references/rationale.md) §1）。

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

閾値の理由は [`references/rationale.md`](references/rationale.md) §2。

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

`knowledge/skill-candidates.local.md`（machine-local な実エントリ用ファイル。Issue #755）に
エントリを 1 件追記する。`candidate_queue` / `curated_only` の場合は書き込まない。
エントリの書式は [`knowledge/skill-candidates.md`](../../../knowledge/skill-candidates.md) の「エントリフォーマット」に従い、
`status` は `pending`、`呼び出し元` は入力の `context`、`提案 skill 名` は `proposed_skill_name` を入れる。

> **追記先は必ず `.local.md`（公開ファイルではない）**: 公開の
> [`knowledge/skill-candidates.md`](../../../knowledge/skill-candidates.md) は
> **エントリフォーマット定義のみ**を保持し、エントリ一覧は**常に空**に保つ（operator-private な
> 候補が OSS リポジトリに載らないため。`knowledge/skill-candidates.local.md` は .gitignore 済み）。
> フォーマット定義・status 語彙・運用ルールは公開ファイル冒頭が一次参照で、実エントリだけが
> `.local.md` に入る。`.local.md` が未作成なら見出し `## エントリ一覧` を持つ最小ファイルを新規作成して追記する。
> 閾値カウント（`tools/check_curate_threshold.py` / `/skill-audit`）は両ファイルを合算して読む。

書き込み後も呼び出し元には出力 YAML を返す（キュー追記は副作用として完了）。
このスキルは判定とキュー追記だけを行う。decision ごとの以降の扱いは呼び出し元が決める
（[`org-retro`](../org-retro/SKILL.md) Step 4.2 / [`org-curate`](../org-curate/SKILL.md) Step 2.5）。

## 重複呼び出しの扱い

同一 `pattern_name` が既に `knowledge/skill-candidates.local.md` に `status: pending` で
存在する場合、Step 4 では新規エントリを追加せず既存エントリの `関連タスク`・`関連 raw ファイル` を
追記マージする（重複チェックの読み先も実エントリを持つ `.local.md`。公開ファイルは常に空）。
status が `approved` / `rejected` / `merged-into-*` になっているエントリは
履歴として保持し、新しいエントリを別日付で追加する（過去の却下理由を失わない）。

status が `deferred`（人間が提示を受けて見送った候補）になっているエントリは、Step 4 では
**再追加も `pending` への差し戻しもしない**（見送りは確定として扱い、同じ候補を蒸し返さない。Issue #753）。
`deferred` は閾値カウント対象外・再問い合わせ対象外である。人間が明示的に再検討したいときのみ
新しい日付で別エントリを起こす。

## スキルを呼ばないケース

- ワーカーが単に「便利だった関数」をメモするだけ → これは `knowledge/raw/` の記録で十分で、
  本スキルの入力 5 項目（特に `steps_outline` と `trigger_description`）が埋まらない
- 一度きりの調査・デバッグ → パターン化する見込みがなく、判定コストの無駄
