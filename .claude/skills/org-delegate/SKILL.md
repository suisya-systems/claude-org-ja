---
name: org-delegate
description: >
  ワーカーClaudeを派遣して作業を委譲する。窓口は司令塔であり、
  手を動かす実作業は原則としてワーカーに任せる。
  ユーザーから作業の依頼を受けたとき、ファイル編集・実装・調査等の
  実作業が発生する場合に発動する。
---

# org-delegate: ワーカー派遣

作業をワーカーClaudeに委譲する。窓口はタスク分解と派遣ペイロード生成だけ行い、
ペイン起動・指示送信はディスパッチャーに委託する。これにより窓口のロック時間を最小化する。

> **本 SKILL のスコープ**: 派遣の「初動」(タスク特定 → 派遣ペイロード生成 → ディスパッチャーへの DELEGATE 受け渡し → ワーカー起動後の挨拶 → 進捗・完了報告受信時の ack と REVIEW 遷移) のみ。以下は別スキル / reference に分離している:
> - **ワーカー起動・指示送信・状態記録の手順** → [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) (ディスパッチャー専属)
> - **ユーザー承認後の push / PR / CI 監視 / レビュー指摘ループ / マージ後クローズ** → [`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md)
> - **ワーカーからの判断仰ぎ / スコープ拡張 / ブロッカーのエスカレーション** → [`.claude/skills/org-escalation/SKILL.md`](../org-escalation/SKILL.md)
> - **ack 文面の最低 3 要素・種別ごとの例文** → [`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) (single SoT)

> **state-db cutover (M4, Issue #267 / #284)**: 構造化セクションの write は **必ず `StateWriter.transaction()` 経由**で行う。post-commit hook が `.state/org-state.md` / `.state/org-state.json` を DB から自動再生成し、`update_run_status('<task_id>', 'completed')` 呼び出しは `.state/workers/worker-<task_id>.md` を `.state/workers/archive/` へ自動 move する。markdown 直接編集は drift_check が検出する。events は DB の `events` テーブルが SoT (`tools/journal_append.sh` / `.py` は DB ルーティング済み)。DB 不在時は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で構築する。

## 窓口とディスパッチャーの役割分担

| 工程 | 担当 |
|---|---|
| プロジェクト名前解決 | **窓口** |
| work-skill 検索 | **窓口** |
| タスク分解 / 派遣ペイロード生成 | **窓口** (`gen_delegate_payload.py`) |
| DELEGATE 送信 | **窓口**（ここで窓口は解放される） |
| ペイン起動・ピア待ち・指示送信・状態記録 | **ディスパッチャー** ([`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)) |
| 窓口への派遣完了報告 | **ディスパッチャー** |
| ワーカーからの進捗/完了/escalation 報告の受信 | **窓口** |
| ワーカー完了時のペインクローズ | **ディスパッチャー**（窓口から `CLOSE_PANE` 依頼） |

## 委譲前チェックリスト（窓口が実行）

タスク分解に入る前に、依頼内容を以下の観点で確認する。該当する場合はユーザーに聞き返す。

| チェック項目 | 確認すべき状況 | 例 |
|---|---|---|
| **曖昧な用語・略語** | ツール名・サービス名・略語が複数の意味を持ちうる場合 | 「gog」→ Google OAuth? gog CLI? |
| **OS固有の前提条件** | OS別の成果物を作る場合、デフォルト設定の明示が必要 | Mac=zsh、Windows=py -3、パス区切り |

- 曖昧な用語がある場合: 「○○は△△のことですか？」とユーザーに確認してから進める
- OS別タスクの場合: ペイロード生成時に、OS固有の前提条件をワーカーへの指示に含める

### incorporation / sync 系タスクの初手チェックリスト

ソース（review 結果 / 別ブランチ / 別リポジトリの状態）を destination に取り込む incorporation / sync 系タスクでは、**ソース commit が destination の現状から N コミット以上進んでいる場合、selective merge（cherry-pick / 必要 hunk のみ apply）を初手として検討する**。byte 一致 cp は Codex iterative review fix を機械的に上書きするリスクがある。

| 観点 | チェック | アクション |
|---|---|---|
| ソースと destination の乖離 | `git log <source>..<destination>` / `git log <destination>..<source>` で双方向に確認 | 双方向に diverge があれば cp 禁止、selective merge を採用 |
| destination 側の追加修正 | destination ブランチで Codex review fix / Blocker fix が積まれていないか | 積まれている場合は cp で機械的に上書きしないこと（cherry-pick or hunk 単位の apply） |

背景: cp で destination の修正を機械的に巻き戻す事故が過去に発生（destination 側の credential 露出対策 Blocker fix を revert 寸前まで進んだ）。ワーカーへの brief で「初手 cp 禁止 / 取り込み戦略を明示」を要求する。

## Step 0: プロジェクト名前解決（窓口が実行）

ユーザーの依頼からプロジェクトを特定する:

1. `registry/projects.md` を読む
2. 依頼に含まれるキーワードから該当プロジェクトを特定する（通称・プロジェクト名・説明から照合）
3. 特定できた場合はそのパスを使う
4. 特定できない場合は登録済みプロジェクトの通称一覧を提示し、選ばせる
5. 新規プロジェクトの場合:
   - パスをユーザーに確認する
   - 通称・説明・よくある作業例を推定し、ユーザーに確認してから `registry/projects.md` に追記する

## Step 0.5: work-skill 検索（窓口が実行）

タスク分解の前に、関連する既存の work-skill がないか検索する。マッチした work-skill はワーカーへの指示に参考情報として含める。

1. `.claude/skills/` 配下の全 SKILL.md ファイルを列挙する
2. 各 SKILL.md の frontmatter (`type` / `description` / `triggers`) をタスク内容と照合する。`org-` プレフィックスは組織運営スキルなので検索対象外
3. 関連性があれば候補に含める（完全一致は不要、複数マッチは関連度順に全て）

**マッチした場合:**
- 人間に「関連 work-skill を見つけました: `{skill-name}` — 参考情報として含めます」と通知する
- `gen_delegate_payload.py` 呼び出しの `--knowledge` フラグに work-skill の SKILL.md パスを渡す。Stage 2 brief renderer がそのパスを `[references].knowledge` として CLAUDE.md / CLAUDE.local.md に埋め込む。複数マッチは `--knowledge <path1> --knowledge <path2>` のように繰り返す
- ワーカーへの指示（instruction-template）にも参考スキルの存在を明記する

work-skill の手順をそのままコピーしない。参考情報として提示し、ワーカーが判断する。

## Step 0.7 / 1 / 1.5 / 2: 1 コマンドで派遣ペイロードを生成（Issue #283）

Step 0.7 (gitignore 事前チェック) / Step 1 (Pattern 判定) / Step 1.5 (ワーカーディレクトリ準備 + role 決定 + settings 生成) / Step 2 (DELEGATE 本文組み立て) は **`tools/gen_delegate_payload.py` が一括で行う**。窓口の責務はタスク特定 (Step 0)・work-skill 検索 (Step 0.5)・対象ファイルの抽出・depth 判断のみ。

### 標準フロー (推奨)

```bash
# 1. preview: 完全に非破壊。DELEGATE 本文と作成予定ファイル一覧だけを確認する
python tools/gen_delegate_payload.py preview \
    --task-id <task-id> --project-slug <slug> \
    --target <path>... --description "<desc>" \
    --verification-depth full

# 1.5. Step 1.7 gate: preview 出力で Codex design review トリガー条件を評価
#      該当する場合のみ codex exec で design review を実行し、要約を
#      --impl-guidance または --knowledge で apply に渡す（下節 Step 1.7 参照）

# 2. apply: state.db に runs.status='queued' で予約 + CLAUDE.md/CLAUDE.local.md 配置
#    + claude-org-runtime settings generate 実行 + send_plan.json 出力
python tools/gen_delegate_payload.py apply \
    --task-id <task-id> --project-slug <slug> \
    --target <path>... --description "<desc>" \
    --verification-depth full

# 3. apply 出力の send_plan.json を MCP 呼び出しにコピペ
#    cat <worker_dir>/send_plan.json
#    → mcp__renga-peers__send_message(to_id="dispatcher", message=<message>)
```

`apply` は **T1 reservation のみ** (`runs.status='queued'`) を行う。Active Work Items への active 化はディスパッチャー T2 ([`docs/contracts/delegation-lifecycle-contract.md`](../../../docs/contracts/delegation-lifecycle-contract.md)) なので本 skill では触らない。失敗時はキューを残したまま Secretary に判断を仰ぐこと。

### よく使うフラグ

- `--mode edit|audit` (default `edit`): claude-org 上の **読み取り専用** 監査タスクは `--mode audit` を明示する
- `--branch <name>`: planned_branch を上書き。default は `feat/<task-id>` (description に "fix"/"bug"/"修正" を含むと `fix/<task-id>`)
- `--commit-prefix "<prefix>"`: 省略時は project_slug の頭部から推論 (例: `claude-org-ja` → `feat(claude):`)
- `--closes-issue N` / `--refs-issues N1 N2`: 「Closes #N」「Refs #N1 #N2」を brief に埋め込む
- `--impl-target <path>` / `--impl-guidance "<text>"` / `--knowledge <path>`: optional な `[implementation]` / `[references]` セクション
- `--skip-settings`: `claude-org-runtime settings generate` をスキップ (CLI 未導入環境向け)
- `--from-toml <path>`: 既存 `worker_brief.toml` を入力にする。CLI フラグは TOML を上書きする

### Pattern / role / branch の判定詳細

判定ロジック (Pattern A vs B vs C / gitignored サブモード / role 表 / planned_branch / DELEGATE 本文の必須行) は [`.claude/skills/org-delegate/references/delegate-flow-details.md`](references/delegate-flow-details.md) 参照。self-edit タスクの特例（Issue #289、`pattern_variant='live_repo_worktree'`）は [`.claude/skills/org-delegate/references/claude-org-self-edit.md`](references/claude-org-self-edit.md) §3 参照。

### 対象ファイル抽出

「対象ファイル」は窓口がタスク説明から抽出する（依頼文・Issue 本文・ユーザー発話の中で明示されたパス。機械的判定はしない）。対象ファイルが特定できないタスク（純粋な調査、対象パス未定の新規作成など）は `--target` を渡さなくてよい。

### 標準経路が想定外の出力を返した場合

標準経路 (`gen_delegate_payload.py apply`) が想定外の出力 (Pattern 誤判定 / resolver エラー / brief 不整合 等) を返した場合、Secretary は **手動で同じ作業を再現してはならない**。resolver のバグとして Issue を切り、当該タスクの delegation は resolver が直るまで pause する。手作業 fallback は skill のスコープ外。CLI 未導入環境では `--skip-settings` フラグに限定する。歴史的な手書き経路の museum copy は `docs/legacy/hand-typed-delegate-path.md` にあるが標準オペレーションでは参照禁止。

## Step 1.7: Codex design review trigger（窓口が実行、Issue #337）

`preview` 出力の `description` / `--target` 件数 / 参照ドキュメントを見て、以下の **いずれか 1 つ以上** に該当する場合は、`apply` の前に Codex design review を実行する。Curator session #18 retrospective (Issue #283 / session #12) で「事前 Codex design review が 1 ラウンドで Blocker 2 件 + Major 5 件を捕捉」した実績に基づくゲート。

| トリガー | 判定方法 |
|---|---|
| 推定工数 ≥ 3h | タスク説明から窓口が判断（ユーザー入力 / preview の規模感） |
| 新規 module / 新規 tool 導入 | description に「新規」「new tool」「新ツール」「新規導入」等、または preview の作成予定ファイルが新規パスのみ |
| ファイル変更 ≥ 3 件 | `--target` の数 + preview の brief に列挙される編集対象 |
| `docs/contracts/` 配下の契約ドキュメント参照 | description / brief / `--knowledge` で `docs/contracts/` を参照 |

**実行手順:**

```bash
codex exec --skip-git-repo-check "<task-id> の design review。\
  タスク内容: <description>。\
  対象ファイル: <target paths>。\
  関連 contract / 参考: <docs paths>。\
  事前設計上の Blocker / Major / Minor / Nit を分類し、各指摘に対象ファイル:行番号と根拠を添えて日本語で簡潔に。"
```

`codex:rescue` skill は使わない（CLAUDE.local.md の禁止事項）。`codex exec` 直打ちのみ。

**review 要約の組み込み:**

- 要約を `tmp/codex-review-{task-id}.md` に保存
- `apply` 呼び出し時に **`--impl-guidance "<要約本文>"`** を渡す。これにより要約本文が brief の `[implementation].guidance` に展開され、ワーカーが直読できる
- 補足として `--knowledge tmp/codex-review-{task-id}.md` を追加すると brief の `[references].knowledge` にパスが列挙され、ワーカーが必要に応じて全文を参照できる（`gen_worker_brief.py` はパスを列挙するだけで本文は埋め込まない）。本文を確実にワーカーへ届けるのは `--impl-guidance` 側の責務
- Blocker / Major が指摘された場合は、ユーザーに上げて方針変更可否を確認してから apply に進む

**helper script:** Issue #337 acceptance で optional とされており、本 PR では実装しない。Secretary が手動で上記表を判定する。

## Step 1.8: dogfood follow-up issue protocol（窓口 + org-pull-request 連携、Issue #338）

新規 tool / runtime / workflow を導入する PR では、実装 PR と paired で「dogfood follow-up」issue を作成し、次回その新規 tool を実使用する delegation を **dogfood pass** として明示的に予約する。Curator session #18 retrospective で「PR #288 で 4 カテゴリの defect が初回実使用時にしか出てこなかった」事象（session #11 でも再現）に基づく protocol。

### 適用条件

タスクが以下のいずれかに該当する場合に発動:

- 新規 CLI tool / script (`tools/*.py`, `tools/*.sh`, `tools/*.ps1` 等) の追加
- 新規 runtime / 新規 workflow / 新規 protocol の導入
- 既存 tool への break-change を伴う再設計

### 窓口（org-delegate）の責務

dogfood protocol は **2 つの delegation** に跨る: (A) 新規 tool を導入する **実装 delegation**, (B) その後その tool を実使用する **dogfood pass delegation**。窓口は両方で `registry/dogfood_pending.md` を読み書きする。

**(A) 実装 delegation の起票時（Step 1.7 評価と同タイミング）:**

1. 適用条件に該当することを判定し、preview と並行で「dogfood 対象タスク」とマーク
2. `registry/dogfood_pending.md` に新規行を 1 行 append し、`status=pending` / `dogfood_issue` / `dogfood_run_task_id` は空 / `impl_pr` は空（PR 番号は後で埋める）。この時点では実装 PR 自体まだ存在しない
3. 実装 worker への brief には dogfood の言及は不要（issue 番号も PR 番号もこの時点では未確定）。実装 worker は通常通り tool を作るだけ

**(B) dogfood pass delegation の起票時:**

4. 新規 delegation を起こす際は、毎回 `registry/dogfood_pending.md` の `status=open` 行（= paired follow-up issue 作成済 / dogfood pass 未実施）を確認する
5. 起票しようとしている新規 task が `tool / surface` 列の対象を実使用する場合、その task を dogfood pass として earmark:
   - `apply` 呼び出しに `--impl-guidance "Dogfood pass for paired follow-up issue #<N>. Report any defects to that issue using the format in references/dogfood-issue-template.md. Refs #<N>, do not Closes."` を追加する
   - 追加で `--knowledge .claude/skills/org-delegate/references/dogfood-issue-template.md` を渡し、defect 報告フォーマットを brief に含める
6. 該当行を更新: `dogfood_run_task_id=<新規 task_id>` を埋め、`status` は `open` のまま据え置き（dogfood worker からの完了報告を受領した時点で `consumed` に遷移、 §register 状態遷移参照）

### org-pull-request 側の責務（cross-ref）

実装 PR 作成 / マージのタイミングで以下を行う（手順詳細は org-pull-request 側で別途整備、Issue #338 は本 SKILL に protocol を記録するスコープ）:

1. 実装 PR 作成直後: `registry/dogfood_pending.md` で `status=pending` の該当行を探し、`impl_pr=#<NNN>` を埋め、`gh issue create --body-file <rendered template>` で paired follow-up issue を作成（template: [`references/dogfood-issue-template.md`](references/dogfood-issue-template.md)）
2. 作成した issue 番号を該当行の `dogfood_issue=#<MMM>` に埋め、`status` を `pending → open` に遷移
3. 実装 PR の本文末に `Paired dogfood issue: #<MMM>` を付ける
4. paired issue がクローズされた時点で該当行の `status` を `consumed → closed` に遷移

### dogfood_pending register フォーマット

`registry/dogfood_pending.md` は **append-only ではなく partial-update register**: 行追加は append、各列（`impl_pr` / `dogfood_issue` / `dogfood_run_task_id` / `status`）への追記更新は許可。論理削除や行の reorder は禁止。

```
| task_id | tool / surface | impl_pr | dogfood_issue | dogfood_run_task_id | status |
|---------|----------------|---------|---------------|---------------------|--------|
| issue-XXX-new-tool | tools/foo.py | #YYY | #ZZZ | issue-MMM-bar | open |
```

### register 状態遷移

```
[行追加] (org-delegate Step 1.8 §A.2)
  status = pending      ← issue 未作成 / impl_pr も空
       │
       │ 実装 PR 作成 + paired issue 作成 (org-pull-request §1-2)
       ▼
  status = open         ← paired issue 作成済 / dogfood pass 未実施
       │
       │ 後続 delegation で earmark (org-delegate Step 1.8 §B.5-6)
       │ dogfood_run_task_id を埋める。status は open のまま
       │
       │ dogfood pass worker 完了報告受領 → defect が paired issue に集約済
       ▼
  status = consumed     ← defect 監視期間
       │
       │ paired issue クローズ (org-pull-request §4)
       ▼
  status = closed       ← 終端
```

各遷移は表の単一行に対する **単一列の差分書き換え**。複数列を同時に書き換える場合（例: pending → open は `impl_pr` と `dogfood_issue` と `status` を一括更新）も同一行内なら可。

### consumed → closed 観察タイミング（窓口の register hygiene 責務）

paired follow-up issue のクローズは実装 PR のライフサイクル外で起こりうるため（手動 close / 個別 fix issue への split / 長期 idle 後の整理）、`org-pull-request` の発動契機（PR 作成・レビュー・マージ後クローズ）だけでは検出漏れが起きる。窓口は **`registry/dogfood_pending.md` に書き込みを行うあらゆるタイミング**（= 実装 delegation 起票 / dogfood pass earmark / dogfood pass 完了報告受領 / 状態確認）で次の hygiene チェックを行う:

```bash
# status=consumed の行について、paired dogfood_issue が closed なら closed に遷移
gh issue view <dogfood_issue> --json state -q .state
  # → "CLOSED" なら status を consumed → closed に書き換える
```

加えて、`/org-resume` 起動時のブリーフィングでも `status=consumed` の行を 1 度ずつスキャンして閉じる（resume 時 hygiene）。これにより consumed が register に滞留しても、次のレジスタ操作までに必ず回収される。

## Step 3 / 4: ワーカー起動・指示送信・状態記録（ディスパッチャーが実行）

詳細手順 (3-1 balanced split / 3-1c SPLIT_CAPACITY_EXCEEDED escalate / 3-2 spawn / 3-3 pane_started / 3-3b channel approve / 3-4 list_peers / 3-5 instruction send / 3-6 順次起動 / Step 4 状態記録 / Worker Directory Registry) は **[`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)** を一次参照する。窓口は触らない。

ディスパッチャーは派遣完了時に窓口へ `DELEGATE_COMPLETE` を返す。

## Step 5: 進捗管理（窓口が実行）

### DELEGATE_COMPLETE 受信時

ディスパッチャーから派遣完了報告を受け取ったら、各ワーカーに挨拶メッセージを送る:
```
mcp__renga-peers__send_message(
  to_id="worker-{task_id}",
  message="窓口です。{task_id} の作業をお願いしています。完了・進捗・ブロック、全ての報告は `to_id=\"secretary\"` で renga-peers 送信してください。"
)
```

### ワーカーからのメッセージ受信時

**Canonical event flow**（途中段階を飛ばしてはならない）:

```
worker → Secretary peer message
  1. ack to worker (全 message 共通で必須。dead-lock 防止)
  2. update Progress Log + DB (run.status / events / pending-decisions register)
  3. report to user           (完了 / escalation / blocker のみ。進捗報告は不要)
  4. wait for user approval before push/PR
  5. CI watch / next instruction → [`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md)
```

- ack の最低内容と種別ごとの例文は [`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) を参照。**ack ≠ user 承認**: `git push` / `gh pr create` / `tools/pr-watch.*` は user の明示的 OK 後にのみ発行
- 2 → 3 の順序は「内部状態を先に整合させてから user に報告する」原則

#### 0. 判断仰ぎ・スコープ拡張・ブロッカー（最優先で識別）

→ [`.claude/skills/org-escalation/SKILL.md`](../org-escalation/SKILL.md) を発動する。Secretary は一次承認しない。

#### 1. 進捗報告

- worker へ ack を返す（[`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) の「進捗報告 ack」節。Progress Log 追記より前）。**進捗報告は user に上げない・承認待ちもしない**
- `.state/workers/worker-{task_id}.md` の Progress Log に追記
- DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)

#### 2a. 完了報告

- worker へ ack を返す（[`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) の「完了報告 ack」節）
- **DB 経由で run を REVIEW に遷移**（markdown 直接編集禁止）:
  ```bash
  python -c "
  from pathlib import Path
  from tools.state_db import connect
  from tools.state_db.writer import StateWriter
  conn = connect('.state/state.db')
  with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
      w.update_run_status('<task_id>', 'review')
  "
  ```
- DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)
- **dogfood pass 完了時の register 更新（Issue #338）**: 完了したタスクが `registry/dogfood_pending.md` の `dogfood_run_task_id` 列に earmark されていた場合、該当行の `status` を `open → consumed` に遷移する。defect は paired follow-up issue (`dogfood_issue` 列) に既に集約されている前提（dogfood pass worker の brief で format 指定済）。protocol 全体は本 SKILL Step 1.8 を SoT
- 結果を人間に報告し、**ペインを閉じず承認待ちで停止**。承認なしで push/PR を発行すると worker / user 双方への protocol 違反

#### 2b / 2c. ユーザー承認後・レビュー指摘・マージ後クローズ

→ [`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md) を発動する。

### ワーカー監視と介入判定（窓口が実行）

派遣後、ワーカーが深掘り・過剰検証ループに入っていないか定期的に確認する。**介入トリガー**（いずれか 1 つ以上該当したら `mcp__renga-peers__inspect_pane` で状況確認）:

- 同一タスクで 30 分超経過、かつ同じフェーズ（実装 / レビュー / 検証）に 3 回目以降入っている
- 1 時間以上進捗報告なしで静穏（入力待ちでもなく、progress ログも出ない）
- (codex を使っている場合) Codex セルフレビューが 4 ラウンド目以降に入っている

**介入手順**: `inspect_pane` で画面確認 → 深掘りと判断したら `send_keys(target="worker-{task_id}", keys=["Escape"])` で中断 → `send_message` で tight な修正指示を送る (例「検証深度 minimal に切り替え。Codex レビュー・追加テスト禁止。`done: {commit SHA} {ファイル名}` の 1 行だけ返してください」)。

窓口が自らワーカーの worktree で commit を代行することは auto-mode classifier によりブロックされる（スコープ逸脱）。介入はあくまで「指示の再送」で行うこと。
