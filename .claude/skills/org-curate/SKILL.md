---
name: org-curate
description: >
  蓄積された生の学び（knowledge/raw/）を整理・統合する。
  ディスパッチャーが worker クローズ時の閾値チェック
  (tools/check_curate_threshold.py) 超過でオンデマンド起動した
  キュレーターから 1 回だけ呼び出される（常駐 /loop は廃止）。
  手動で「知見を整理して」と言われたときにも使う。
effort: medium
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(mkdir -p knowledge/raw/archive/)
  - Bash(mkdir -p ../knowledge/raw/archive/)
  - Bash(mv knowledge/raw/*)
  - Bash(mv ../knowledge/raw/*)
  - Bash(grep:*)
  - Bash(find knowledge/*)
  - Bash(find ../knowledge/*)
  - Bash(py -3 tools/check_curate_threshold.py:*)
  - Bash(python3 tools/check_curate_threshold.py:*)
  - Bash(py -3 ../tools/check_curate_threshold.py:*)
  - Bash(python3 ../tools/check_curate_threshold.py:*)
  - mcp__renga-peers__send_message
---

# org-curate: 知見整理

knowledge/raw/ に蓄積された生の学びを読み、分類・統合して knowledge/curated/ に書き出す。

**起動モデル（オンデマンド化）**: 本スキルは 1 回の起動で 1 サイクルだけ実行する（`/loop` 禁止）。
閾値判定は外部スクリプト [`tools/check_curate_threshold.py`](../../../tools/check_curate_threshold.py) に
一本化されており、本スキル内部には「raw 5 件未満なら即終了」のようなゲートは**存在しない**。
起動理由 `reasons[]` を受け取り、該当するステップだけを実行する。

**パス解決（重要）**: 本スキル中の `knowledge/...` / `tools/...` 表記は**リポジトリルート相対の
論理パス**である。オンデマンド起動された curator ペインの CWD は `.curator/` なので、Bash で
実行する際は `../knowledge/...` / `../tools/...`（または `cd .. && pwd` で得た絶対パス）に
読み替えること（`.curator/CLAUDE.md` の「パス」節と同じ規約。allowed-tools には両形が
許可済み）。リポジトリルートで手動実行する場合はそのまま使う。

## Step 0: 起動理由 (reasons) の確定

`reasons[]` の値は以下の 4 種:

| reason | 意味 | 実行するステップ |
|---|---|---|
| `raw_threshold` | active raw が 5 件以上 | Step 2〜5（分類・統合・archive・改善提案） |
| `skill_candidates_pending` | skill-candidates pending が 5 件以上 | Step 6（skill-audit 発火） |
| `work_skill_count` | work-skill 数（org-* 除外）が 20 件以上 | Step 6（skill-audit 発火） |
| `legacy_marker_sweep` | raw/ 直下に `<!-- curated -->` 残骸あり | Step 1（移行 sweep。※常に実行） |

1. **ディスパッチャー経由のオンデマンド起動**: 起動指示メッセージに
   `tools/check_curate_threshold.py` の JSON（`reasons[]` / `counts`）が含まれている。
   それをそのまま採用する（再計算しない）。
2. **手動起動（reasons が渡されていない場合）**: 自分でスクリプトを実行して確定する。
   curator ペイン（CWD=`.curator/`）からは `py -3 ../tools/check_curate_threshold.py`、
   リポジトリルートからは `py -3 tools/check_curate_threshold.py`（POSIX は `python3`）:
   - exit 0（below_threshold）→ 実行する作業なし。Step 1 の sweep だけ行い、Step 7 で
     `CURATE_SKIPPED` を通知して終了する
   - exit 10（curate_needed）→ stdout JSON の `reasons[]` を採用して続行
   - exit 2（error）→ Step 7 で `CURATE_ERROR` を通知して終了する

## Step 1: 移行 sweep（旧データの一掃）— 常に実行

`reasons[]` の内容に関わらず、毎回**無条件で**実施する（idempotent な cleanup で、
残骸が無ければ no-op）。`legacy_marker_sweep` reason は「この sweep のためだけにでも
curator を起動する」ことを保証するための起動条件であり、sweep 自体は起動理由を問わず走る:

1. `mkdir -p knowledge/raw/archive/`（idempotent）
2. `knowledge/raw/` 直下のファイルのうち、先頭に `<!-- curated -->` を含むものを
   `knowledge/raw/archive/` に move する。マーカー付与は不要（既に付いている）
3. このステップは raw ファイルが 0 件でも実行する

> Set A § Role: curator により、curator の書き込み権限は `knowledge/curated/` と
> `knowledge/raw/archive/`（move 権限）に限定される。`knowledge/raw/` 直下の active entry は
> immutable。Step 1 の移行 sweep も書き換えではなく move なので、この制約に抵触しない。

**以降の分岐**: `reasons[]` に `raw_threshold` があれば Step 2 へ。無ければ Step 2〜5 を
スキップして Step 6 の判定に進む。

## Step 2: 読み込みと分類（reason: raw_threshold）

1. `knowledge/raw/` 直下（`knowledge/raw/archive/` を**除く**）のファイルを列挙する。
   `.gitkeep` 等の sentinel（`.` 始まりのエントリ）は除外する（Step 1 sweep 済みのため
   active raw にマーカー付きファイルは存在しない前提）
2. それらをすべて未整理として読む
3. テーマ別に分類する。テーマの粒度は以下を目安にする:
   - 技術領域（例: authentication, database, frontend）
   - ツール・サービス（例: renga, github-api, aws）
   - プロセス（例: code-review, testing, deployment）
4. 既存の `knowledge/curated/` ファイルも読み、重複がないか確認する

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

## Step 3: 統合と書き出し（reason: raw_threshold）

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

## Step 4: archive への move と処理済みマーカー（reason: raw_threshold）

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

ファイルが archive/ に存在すること自体が「curated 済み」のシグナルだが、視覚的連続性のためにマーカーも付与する。`tools/check_curate_threshold.py` の `raw_active` カウントは archive/ を除外するため、move しただけでも次回はカウント対象から外れる。

> 根拠: Set E §1.1 (Q1) ratification と Set A § Role: curator により、curator は `knowledge/raw/` 直下の active entry を mutate してはならない。書き込み権限は `knowledge/curated/` への新規作成・追記と、`knowledge/raw/archive/` への move（および archive 側ファイルの編集）に限定される。

## Step 5: 改善提案の検討（reason: raw_threshold）

整理した知見を俯瞰し、以下を検討する:

1. **スキルの改善**: 知見がスキルの手順改善に繋がるか？
   - 例: 「ワーカーのペイン数上限」→ org-delegate に制約を追加すべき
2. **CLAUDE.mdの改善**: 窓口の原則に追加すべきことがあるか？
3. **新スキルの必要性**: 繰り返し登場するパターンが新スキルとして切り出せるか？

改善提案がある場合:
- references/knowledge-standards.md の基準に従って判断する
- renga-peers で窓口Claudeに提案を送信する（`to_id="secretary"`）
- 提案フォーマット: 「[改善提案] {対象}: {変更内容}。理由: {なぜ}」
- **窓口が人間に承認を取るまで、自分では変更しない**

## Step 6: skill 棚卸しの発火（reason: skill_candidates_pending / work_skill_count）

`reasons[]` に `skill_candidates_pending` または `work_skill_count` が含まれる場合、
`.claude/skills/skill-audit/SKILL.md` を起動する。どちらも含まれなければ何もしない。

閾値の定義（pending 5 件以上 / work-skill 20 件以上、org-* 除外）は
`tools/check_curate_threshold.py` と skill-audit Step 1 で完全一致している。
`skill-audit` 自身も発火時に閾値を再確認するので、本ステップでの再計算は不要。

## Step 7: 完了通知（必ず最後に実行）

サイクルの結果を**ディスパッチャー宛て direct send** で通知する。これがオンデマンド
curator のペインクローズのトリガーになるため、**送信先は必ず `to_id="dispatcher"`**
（channel broadcast や secretary 宛てでは dispatcher の `check_messages` 待ちが
timeout し、ペインリーク / 早閉じの原因になる）:

```
mcp__renga-peers__send_message(to_id="dispatcher", message="CURATE_DONE: ...")
```

**順序規定**: Step 5 の改善提案（secretary 宛て）が**すべて送信し終わった後**に送る。
CURATE_* を受けた dispatcher はペインを閉じてよい契約なので、先に送ると改善提案が
送信されないままペインが破棄されうる。

メッセージは以下の 3 種のいずれか:

- `CURATE_DONE: reasons={reasons[]} raw {n}件 → curated {m}テーマ統合 / archive {k}件 / sweep {s}件 / skill-audit {発火 or なし}`
  — 1 つ以上のステップを実行して正常完了した場合
- `CURATE_SKIPPED: below_threshold (counts: raw_active={n}, pending={p}, work_skill={w}, legacy_marker={l})`
  — 手動起動等で閾値未達と判明し、sweep 以外何もしなかった場合
- `CURATE_ERROR: {1 行の要約}` — 途中で続行不能なエラーが発生した場合（部分完了の内容も 1 行に含める）

手動起動（窓口ペイン等、dispatcher にペインクローズ責務が無い文脈）では、dispatcher が
peer に存在すれば同様に送り（情報共有として無害）、`[pane_not_found]` なら省略してよい。
