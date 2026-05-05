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
> - **ワーカー起動・指示送信・状態記録の手順** → [`../../../.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) (ディスパッチャー専属)
> - **ユーザー承認後の push / PR / CI 監視 / レビュー指摘ループ / マージ後クローズ** → [`../org-pull-request/SKILL.md`](../org-pull-request/SKILL.md)
> - **ワーカーからの判断仰ぎ / スコープ拡張 / ブロッカーのエスカレーション** → [`../org-escalation/SKILL.md`](../org-escalation/SKILL.md)
> - **ack 文面の最低 3 要素・種別ごとの例文** → [`references/ack-template.md`](references/ack-template.md) (single SoT)

> **state-db cutover (M4, Issue #267 / #284)**: 構造化セクションの write は **必ず `StateWriter.transaction()` 経由**で行う。post-commit hook が `.state/org-state.md` / `.state/org-state.json` を DB から自動再生成し、`update_run_status('<task_id>', 'completed')` 呼び出しは `.state/workers/worker-<task_id>.md` を `.state/workers/archive/` へ自動 move する。markdown 直接編集は drift_check が検出する。events は DB の `events` テーブルが SoT (`tools/journal_append.sh` / `.py` は DB ルーティング済み)。DB 不在時は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で構築する。

## 窓口とディスパッチャーの役割分担

| 工程 | 担当 |
|---|---|
| プロジェクト名前解決 | **窓口** |
| work-skill 検索 | **窓口** |
| タスク分解 / 派遣ペイロード生成 | **窓口** (`gen_delegate_payload.py`) |
| DELEGATE 送信 | **窓口**（ここで窓口は解放される） |
| ペイン起動・ピア待ち・指示送信・状態記録 | **ディスパッチャー** ([`spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)) |
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

判定ロジック (Pattern A vs B vs C / gitignored サブモード / role 表 / planned_branch / DELEGATE 本文の必須行) は [`references/delegate-flow-details.md`](references/delegate-flow-details.md) 参照。self-edit タスクの特例（Issue #289、`pattern_variant='live_repo_worktree'`）は [`references/claude-org-self-edit.md`](references/claude-org-self-edit.md) §3 参照。

### 対象ファイル抽出

「対象ファイル」は窓口がタスク説明から抽出する（依頼文・Issue 本文・ユーザー発話の中で明示されたパス。機械的判定はしない）。対象ファイルが特定できないタスク（純粋な調査、対象パス未定の新規作成など）は `--target` を渡さなくてよい。

### 標準経路が想定外の出力を返した場合

標準経路 (`gen_delegate_payload.py apply`) が想定外の出力 (Pattern 誤判定 / resolver エラー / brief 不整合 等) を返した場合、Secretary は **手動で同じ作業を再現してはならない**。resolver のバグとして Issue を切り、当該タスクの delegation は resolver が直るまで pause する。手作業 fallback は skill のスコープ外。CLI 未導入環境では `--skip-settings` フラグに限定する。歴史的な手書き経路の museum copy は `docs/legacy/hand-typed-delegate-path.md` にあるが標準オペレーションでは参照禁止。

## Step 3 / 4: ワーカー起動・指示送信・状態記録（ディスパッチャーが実行）

詳細手順 (3-1 balanced split / 3-1c SPLIT_CAPACITY_EXCEEDED escalate / 3-2 spawn / 3-3 pane_started / 3-3b channel approve / 3-4 list_peers / 3-5 instruction send / 3-6 順次起動 / Step 4 状態記録 / Worker Directory Registry) は **[`../../../.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)** を一次参照する。窓口は触らない。

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
  5. CI watch / next instruction → [`../org-pull-request/SKILL.md`](../org-pull-request/SKILL.md)
```

- ack の最低内容と種別ごとの例文は [`references/ack-template.md`](references/ack-template.md) を参照。**ack ≠ user 承認**: `git push` / `gh pr create` / `tools/pr-watch.*` は user の明示的 OK 後にのみ発行
- 2 → 3 の順序は「内部状態を先に整合させてから user に報告する」原則

#### 0. 判断仰ぎ・スコープ拡張・ブロッカー（最優先で識別）

→ [`../org-escalation/SKILL.md`](../org-escalation/SKILL.md) を発動する。Secretary は一次承認しない。

#### 1. 進捗報告

- worker へ ack を返す（[`references/ack-template.md`](references/ack-template.md) の「進捗報告 ack」節。Progress Log 追記より前）。**進捗報告は user に上げない・承認待ちもしない**
- `.state/workers/worker-{task_id}.md` の Progress Log に追記
- DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)

#### 2a. 完了報告

- worker へ ack を返す（[`references/ack-template.md`](references/ack-template.md) の「完了報告 ack」節）
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
- 結果を人間に報告し、**ペインを閉じず承認待ちで停止**。承認なしで push/PR を発行すると worker / user 双方への protocol 違反

#### 2b / 2c. ユーザー承認後・レビュー指摘・マージ後クローズ

→ [`../org-pull-request/SKILL.md`](../org-pull-request/SKILL.md) を発動する。

### ワーカー監視と介入判定（窓口が実行）

派遣後、ワーカーが深掘り・過剰検証ループに入っていないか定期的に確認する。**介入トリガー**（いずれか 1 つ以上該当したら `mcp__renga-peers__inspect_pane` で状況確認）:

- 同一タスクで 30 分超経過、かつ同じフェーズ（実装 / レビュー / 検証）に 3 回目以降入っている
- 1 時間以上進捗報告なしで静穏（入力待ちでもなく、progress ログも出ない）
- (codex を使っている場合) Codex セルフレビューが 4 ラウンド目以降に入っている

**介入手順**: `inspect_pane` で画面確認 → 深掘りと判断したら `send_keys(target="worker-{task_id}", keys=["Escape"])` で中断 → `send_message` で tight な修正指示を送る (例「検証深度 minimal に切り替え。Codex レビュー・追加テスト禁止。`done: {commit SHA} {ファイル名}` の 1 行だけ返してください」)。

窓口が自らワーカーの worktree で commit を代行することは auto-mode classifier によりブロックされる（スコープ逸脱）。介入はあくまで「指示の再送」で行うこと。
