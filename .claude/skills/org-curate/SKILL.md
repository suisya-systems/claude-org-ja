---
name: org-curate
description: >
  knowledge/raw/ の生の学びを分類・統合して knowledge/curated/ に書き出す。
  ディスパッチャーが閾値超過で起動したキュレーターから呼ばれるほか、
  「知見を整理して」と言われたときに使う。
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
  - mcp__org-broker__send_message
---

# org-curate: 知見整理

knowledge/raw/ に蓄積された生の学びを読み、分類・統合して knowledge/curated/ に書き出す。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を画面判定で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を画面判定で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を画面判定で承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。 **画面判定** = `inspect_pane` → `claude-org-runtime dispatcher spawn-prompt-step` が返した 1 手だけを `send_keys` する反復（runtime 0.1.43+）。盲目 Enter は禁止（folder-trust の初期カーソルは「No, exit」で、Enter 一発はワーカーを終了させる）。手順は spawn-flow 3-3b。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節。）

**起動モデル**: 本スキルは 1 回の起動で 1 サイクルだけ実行する（`/loop` 禁止）。
閾値判定は外部スクリプト [`tools/check_curate_threshold.py`](../../../tools/check_curate_threshold.py) が行い、
本スキル内部では件数による早期終了をしない。
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

### 0-A: 起動文脈の判定 — Step 7 の送信義務を決める

Step 7 の送信義務は、このインスタンスが dispatcher（peer name `dispatcher`）から起動指示メッセージを
受けたかだけで決まる。

- **受けた（on-demand）**: 結果に関わらず Step 7 で `to_id="dispatcher"` に `CURATE_*` を送る
  （不送信は curator ペインのリークになる）。起動指示に reasons の JSON が無く下の項 2 に落ちても同じ。
- **受けていない（人間が自分のペインで `/org-curate` を打った）**: Step 7 は送らない。

`.state/dispatcher/curate-inflight.json` は判断材料にしない。並走中の別の on-demand curator の
マーカーでもあり得て、それを見て送った `CURATE_*` を dispatcher は本物の curator の完了として扱い、
作業中のペインを閉じうる（[`.dispatcher/references/worker-monitoring.md` Step 5.3](../../../.dispatcher/references/worker-monitoring.md#step-5-3)）。

1. **ディスパッチャー経由のオンデマンド起動**: 起動指示メッセージに
   `tools/check_curate_threshold.py` の JSON（`reasons[]` / `counts`）が含まれている。
   それをそのまま採用する（再計算しない）。
2. **reasons が渡されていない場合**: 自分でスクリプトを実行して確定する。
   curator ペイン（CWD=`.curator/`）からは `py -3 ../tools/check_curate_threshold.py`、
   リポジトリルートからは `py -3 tools/check_curate_threshold.py`（POSIX は `python3`）:
   - exit 0（below_threshold）→ 実行する作業なし。Step 1 の sweep だけ行い、Step 7 へ進む
     （送る場合は `CURATE_SKIPPED`）
   - exit 10（curate_needed）→ stdout JSON の `reasons[]` を採用して続行
   - exit 2（error）→ Step 7 へ進む（送る場合は `CURATE_ERROR`）

## Step 1: 移行 sweep（旧データの一掃）— 常に実行

`reasons[]` の内容に関わらず、毎回**無条件で**実施する（idempotent な cleanup で、
残骸が無ければ no-op）。`legacy_marker_sweep` reason は「この sweep のためだけにでも
curator を起動する」ことを保証するための起動条件であり、sweep 自体は起動理由を問わず走る:

1. `mkdir -p knowledge/raw/archive/`（idempotent）
2. `knowledge/raw/` 直下のファイルのうち、先頭に `<!-- curated -->` を含むものを
   `knowledge/raw/archive/` に move する。マーカー付与は不要（既に付いている）
3. このステップは raw ファイルが 0 件でも実行する

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
raw_files: <同テーマの raw/ パス配列。Step 4 で archive に move する前提のため、ここでは move 後のパス（`knowledge/raw/archive/<entry>.md`）を記録する。`skill-eligibility-check` はこれを machine-local な `knowledge/skill-candidates.local.md`（実エントリ用）に永続化するため、追跡可能な最終パスを渡す>
steps_outline: <raw 群から抽出した主要手順>
trigger_description: <このテーマが発動する場面>
decision_criteria: <テーマ内に現れる判断基準>
output_format: <テーマの成果物フォーマット>
```

`task_ids` は既存 raw の標準スキーマ（`事実 / 判断 / 根拠 / 適用場面`）に含まれないため、
curation context では空配列でよい。raw ファイル名から日付等が読み取れればそれを `raw_files` に含めることで代用できる。

結果の decision によって次の扱いを決める。**いずれの decision でも Step 3 での curated/ 統合は通常どおり実施する**:

- `skill_recommend` → skill 側が machine-local な `knowledge/skill-candidates.local.md`（実エントリ用）に自動追記済み。本ステップでは追加作業なし。
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
6. 矛盾する知見は references/knowledge-standards.md の「マージしない場合」に従う

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
   （通常フローでは archive 側に同名ファイルは無く新規パス宛ての move なので `mv -i` alias でも上書きプロンプトは出ない。alias で壊れるのは次の sub-step 3 のマーカー付与であり、通常の move ではない。例外として同名 raw の再発生や部分復旧で `knowledge/raw/archive/<entry>.md` が既に存在する場合は `mv -i` が対話確認を出すため、その時は停止して既存 archive 側を確認し、リネーム等で衝突を解消してから move する）
3. move 後、archive 側のファイル先頭に視覚的マーカーとして `<!-- curated -->` 行を追記する。
   **付与は Write ツールで行う（必須手順）**: 対象 archive ファイルの内容を Read し、先頭に
   `<!-- curated -->` + 改行を prepend して同じパスに Write し直す（= read → prepend → write）。
   マーカー付与は **archive に move した後のファイルに対して**行う。active な `knowledge/raw/` 配下のファイルは絶対に書き換えない。

   シェルで付与しない（`.md.tmp` 経由の上書き `mv` は `mv -i` alias で止まり、`!` は zsh の history expansion で壊れる）。やむを得ずシェルを使うときは `command mv -f` と `chr(33)` でマーカーを組み立てる。理由は [`.claude/skills/org-curate/references/rationale.md`](references/rationale.md) §2。

ファイルが archive/ に存在すること自体が「curated 済み」のシグナルだが、視覚的連続性のためにマーカーも付与する。`tools/check_curate_threshold.py` の `raw_active` カウントは archive/ を除外するため、move しただけでも次回はカウント対象から外れる。

## Step 5: 改善提案の検討（reason: raw_threshold）

整理した知見を俯瞰し、以下を検討する:

1. **スキルの改善**: 知見がスキルの手順改善に繋がるか？
   - 例: 「ワーカーのペイン数上限」→ org-delegate に制約を追加すべき
2. **CLAUDE.mdの改善**: 窓口の原則に追加すべきことがあるか？
3. **新スキルの必要性**: 繰り返し登場するパターンが新スキルとして切り出せるか？

改善提案がある場合:
- references/knowledge-standards.md の基準に従って判断する
- org-broker で窓口Claudeに提案を送信する（`to_id="secretary"`）
- 提案フォーマット: 「[改善提案] {対象}: {変更内容}。理由: {なぜ}」
- **窓口が人間に承認を取るまで、自分では変更しない**

## Step 6: skill 棚卸しの発火（reason: skill_candidates_pending / work_skill_count）

`reasons[]` に `skill_candidates_pending` または `work_skill_count` が含まれる場合、
`.claude/skills/skill-audit/SKILL.md` を起動する。どちらも含まれなければ何もしない。

閾値の定義（pending 5 件以上 / work-skill 20 件以上、org-* 除外）は
`tools/check_curate_threshold.py` と skill-audit Step 1 で完全一致している。
`skill-audit` 自身も発火時に閾値を再確認するので、本ステップでの再計算は不要。

## Step 7: 完了通知（必ず最後に実行）

送信が必須か省略可かは Step 0-A の判定だけで決まる。on-demand なら below_threshold で sweep しか
しなかった場合も `CURATE_SKIPPED` を送る（dispatcher は `CURATE_*` を `check_messages` で待っている）。

サイクルの結果を**ディスパッチャー宛て direct send** で通知する。これがオンデマンド
curator のペインクローズのトリガーになるため、**送信先は必ず `to_id="dispatcher"`**
（channel broadcast や secretary 宛てでは dispatcher の `check_messages` 待ちが
timeout し、ペインリーク / 早閉じの原因になる）:

```
mcp__org-broker__send_message(to_id="dispatcher", message="CURATE_DONE: ...")
```

**順序規定**: Step 5 の改善提案（secretary 宛て）が**すべて送信し終わった後**に送る。
CURATE_* を受けた dispatcher はペインを閉じてよい契約なので、先に送ると改善提案が
送信されないままペインが破棄されうる。

メッセージは以下の 3 種のいずれか:

- `CURATE_DONE: reasons={reasons[]} raw {n}件 → curated {m}テーマ統合 / archive {k}件 / sweep {s}件 / skill-audit {発火 or なし}`
  — 1 つ以上のステップを実行して正常完了した場合
- `CURATE_SKIPPED: below_threshold (counts: raw_active={n}, pending={p}, work_skill={w}, legacy_marker={l})`
  — 閾値未達（below_threshold）と判明し、sweep 以外何もしなかった場合
- `CURATE_ERROR: {1 行の要約}` — 途中で続行不能なエラーが発生した場合（部分完了の内容も 1 行に含める）

`[pane_not_found]` が返ったときだけ送信を諦める。
