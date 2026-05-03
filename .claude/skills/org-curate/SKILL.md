---
name: org-curate
description: >
  蓄積された生の学び（knowledge/raw/）を整理・統合する。
  キュレーターClaudeの /loop から定期呼び出しされる。
  手動で「知見を整理して」と言われたときにも使う。
---

# org-curate: 知見整理

knowledge/raw/ に蓄積された生の学びを読み、分類・統合して knowledge/curated/ に書き出す。

## Step 0: 移行 sweep（旧データの一掃）

閾値チェックより**先に**、無条件で実施する。これは新仕様への切り替え時に旧 in-place marking で残った active raw を片付けるための、毎回走らせる移行 cleanup である:

1. `mkdir -p knowledge/raw/archive/`（idempotent）
2. `knowledge/raw/` 直下のファイルのうち、先頭に `<!-- curated -->` を含むものを `knowledge/raw/archive/` に move する。マーカー付与は不要（既に付いている）
3. このステップは raw ファイルが 0 件でも実行する

これにより、新規 raw が閾値未満（5 件未満）の環境でも、旧マーカー付きファイルは sweep され、active raw 側に残留しない。新仕様化以降は該当ファイルが恒常的にゼロになり、本 Step は no-op になる。

## Step 1: 閾値チェック

1. `knowledge/raw/` 直下（`knowledge/raw/archive/` を**除く**）のファイルを列挙する（Step 0 sweep 済みのため active raw にマーカー付きファイルは存在しない前提）
2. それらをすべて未整理としてカウントする
3. 未整理ファイルが5件未満なら、何もせずスキップする
4. 5件以上なら次のステップに進む

> Set A § Role: curator により、curator の書き込み権限は `knowledge/curated/` と `knowledge/raw/archive/`（move 権限）に限定される。`knowledge/raw/` 直下の active entry は immutable。Step 0 の移行 sweep も書き換えではなく move なので、この制約に抵触しない。

## Step 2: 読み込みと分類

1. 未整理ファイルを全て読む
2. テーマ別に分類する。テーマの粒度は以下を目安にする:
   - 技術領域（例: authentication, database, frontend）
   - ツール・サービス（例: renga, github-api, aws）
   - プロセス（例: code-review, testing, deployment）
3. 既存の `knowledge/curated/` ファイルも読み、重複がないか確認する

## Step 2.5: skill 化候補の抽出

Step 2 で分類したテーマ群のうち、以下のいずれかに該当するものについて
`.claude/skills/skill-eligibility-check/SKILL.md` を呼ぶ:

- 同一テーマに属する未整理 raw ファイルが **3 件以上**ある（raw_reappearance シグナルが立つ候補）
- 既存の `knowledge/curated/` に同テーマ記事が無く、かつ手順的な知見（Step 群で記述できる内容）を含む

呼び出し時の入力（`context: curation`）は以下のとおり組み立てる:

```yaml
context: curation
pattern_name: <推定 skill 名、kebab-case。テーマ名から派生させる>
summary: <このテーマで何が再利用できるか 1-2 文>
task_ids: []                    # optional。raw ノートに task_id が無ければ空のままでよい
raw_files: <同テーマの raw/ パス配列。Step 4 で archive に move する前提のため、ここでは move 後のパス（`knowledge/raw/archive/<entry>.md`）を記録する。`skill-eligibility-check` はこれを `knowledge/skill-candidates.md` に永続化するため、追跡可能な最終パスを渡す>
steps_outline: <raw 群から抽出した主要手順>
trigger_description: <このテーマが発動する場面>
decision_criteria: <テーマ内に現れる判断基準>
output_format: <テーマの成果物フォーマット>
```

`task_ids` は既存 raw の標準スキーマ（`事実 / 判断 / 根拠 / 適用場面`）に含まれないため、
curation context では空配列でよい。raw ファイル名から日付等が読み取れればそれを `raw_files` に含めることで代用できる。

結果の decision によって次の扱いを決める。**いずれの decision でも Step 3 での curated/ 統合は通常どおり実施する**:

- `skill_recommend` → skill 側が `knowledge/skill-candidates.md` に自動追記済み。本ステップでは追加作業なし。
  該当 raw ファイルも **Step 3 で curated/ に統合し、Step 4 で `knowledge/raw/archive/` に move したうえでマーカーを付与する**
  （skill 化と curated ノート化は両立。curated ノートは背景知識として残り、
  skill は手順化として別途作成される。両立させないと未整理 raw が滞留して閾値チェックが壊れる）
- `candidate_queue` → 通常どおり Step 3 で curated/ に統合（次回の raw_reappearance を待つ）
- `curated_only` → 通常どおり Step 3 で curated/ に統合

人間への問い合わせは窓口 Claude の役目であり、org-curate 側では行わない。

## Step 3: 統合と書き出し

各テーマについて:

1. 既存のcuratedファイルがあれば、新しい知見を追記する
2. なければ新規作成する
3. ファイル名: `knowledge/curated/{theme}.md`
4. フォーマット:
   ```markdown
   # {テーマ名}

   ## {知見タイトル1}
   {事実・判断・根拠・適用場面を統合した記述}

   ## {知見タイトル2}
   ...
   ```
5. 重複する知見はマージする（より具体的・正確な記述を残す）
6. 矛盾する知見がある場合は、より新しい日付のものを優先し、矛盾を明記する

## Step 4: archive への move と処理済みマーカー

統合が完了した raw ファイルは、active raw 側に書き戻すのではなく `knowledge/raw/archive/` に move する（move-then-mark）。

1. `knowledge/raw/archive/` を作成する（idempotent）:
   ```
   mkdir -p knowledge/raw/archive/
   ```
2. 統合済み raw ファイルを archive に move する:
   ```
   mv knowledge/raw/<entry>.md knowledge/raw/archive/<entry>.md
   ```
3. move 後、archive 側のファイル先頭に視覚的マーカーとして以下を追記する:
   ```
   <!-- curated -->
   ```
   マーカー付与は **archive に move した後のファイルに対して**行う。active な `knowledge/raw/` 配下のファイルは絶対に書き換えない。

ファイルが archive/ に存在すること自体が「curated 済み」のシグナルだが、視覚的連続性のためにマーカーも付与する。Step 1 の閾値チェックは archive/ を除外するため、move しただけでも次回はカウント対象から外れる。

> 根拠: Set E §1.1 (Q1) ratification と Set A § Role: curator により、curator は `knowledge/raw/` 直下の active entry を mutate してはならない。書き込み権限は `knowledge/curated/` への新規作成・追記と、`knowledge/raw/archive/` への move（および archive 側ファイルの編集）に限定される。

## Step 5: 改善提案の検討

整理した知見を俯瞰し、以下を検討する:

1. **スキルの改善**: 知見がスキルの手順改善に繋がるか？
   - 例: 「ワーカーのペイン数上限」→ org-delegate に制約を追加すべき
2. **CLAUDE.mdの改善**: 窓口の原則に追加すべきことがあるか？
3. **新スキルの必要性**: 繰り返し登場するパターンが新スキルとして切り出せるか？

改善提案がある場合:
- references/knowledge-standards.md の基準に従って判断する
- renga-peers で窓口Claudeに提案を送信する
- 提案フォーマット: 「[改善提案] {対象}: {変更内容}。理由: {なぜ}」
- **窓口が人間に承認を取るまで、自分では変更しない**

## Step 6: skill 棚卸しの発火チェック

以下のいずれかを満たす場合、`.claude/skills/skill-audit/SKILL.md` を起動する:

- `knowledge/skill-candidates.md` 内の `status: pending` エントリが **5 件（N=5）** 以上
- `.claude/skills/` 配下の skill ディレクトリ数が **20 件（M=20）** 以上

両方を下回るなら何もしない。時間ベースの定期起動はしない。
閾値は `skill-audit` 自身も発火時に再確認するので、本ステップでは粗くてよい。
