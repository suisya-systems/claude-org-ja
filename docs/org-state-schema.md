# org-state.json スキーマ定義

> **Status: historical / pre-M4**（Issue [#353](https://github.com/suisya-systems/claude-org-ja/issues/353) 同期時点）
>
> 本ドキュメントの「Source of truth ルール」「Markdown が正本」「converter 再実行」記述は **M4 cutover 前のモデル**であり、現行実装とは整合しません。**現行の正準は [`docs/contracts/state-semantics-contract.md`](contracts/state-semantics-contract.md)** です:
>
> - `.state/state.db` が `runs` / `org_sessions` / `worker_dirs` / `events` の **唯一の SoT**。
> - `.state/org-state.md` は `StateWriter.transaction()` の post-commit hook が `tools/state_db.snapshotter` で自動再生成する **派生物**（手動編集は drift）。
> - `.state/org-state.json` も state.db を直接読む `dashboard/org_state_converter.py` が生成する派生物（`--source markdown` モードは M4 で削除済み）。
> - 旧来の `org-state.md` 手動編集 → converter 再実行ループは **行ってはならない**。state.db への書き込みは `tools/state_db.writer.StateWriter` の API（`upsert_run` / `update_run_status` / 他）を経由する。
>
> 以下に残す JSON スキーマ定義（version 1）は dashboard / 外部 consumer がペイロード形状を参照するためのリファレンスとして migration-only で保持している。フィールドの意味づけが state-semantics-contract と矛盾する場合は contract が governs する。

## 概要

`.state/org-state.json` は state.db から `dashboard/org_state_converter.py` が生成する派生 JSON。
ダッシュボード（`dashboard/server.py`）その他のプログラム的消費者が JSON を優先的に読み込めるようにするために導入された。

### JSON の再生成（参考）

state.db への書き込みが `StateWriter.transaction()` を経由していれば snapshotter が自動的に派生物を更新するため、通常は手動再生成は不要。ローカル debug でのみ:

```bash
py -3 dashboard/org_state_converter.py      # Windows
python3 dashboard/org_state_converter.py     # Mac/Linux
```

### 更新ポイント（参考、historical）

下表は pre-M4 の「skill が markdown を編集 → converter で JSON 化」フローの記述。**現行は state.db への書き込みが SoT** であり、対応する API は以下:

| 操作 | スキル / 経路 | 現行の writer |
|---|---|---|
| ワーカー派遣（T1 reservation）| org-delegate | `tools/gen_delegate_payload.py`（`StateWriter.upsert_run` で `runs.status='queued'`）|
| ペイン spawn（T2）| dispatcher delegate-plan helper | `StateWriter.upsert_run` で `runs.status='in_use'` |
| ステータス変更（T4 review / T5 completed / T6 review→in_use）| org-delegate / org-pull-request | `StateWriter.update_run_status` |
| 組織中断 / 再開 | org-suspend / org-resume | `StateWriter` 経由で `org_sessions.status` を更新（個別 run の status は変更しない、[contract I4](contracts/state-semantics-contract.md)）|
| Dispatcher/Curator 記録 | org-start | `StateWriter` 経由で `org_sessions` の dispatcher/curator pane+peer フィールドを更新 |

これらの書き込みは全て `StateWriter.transaction()` の post-commit hook で snapshotter が `.state/org-state.md` と派生 JSON を自動再生成するため、skill 側で converter を呼ぶ必要は無い。

---

## スキーマ（version 1）

```json
{
  "version": 1,
  "updated": "<ISO 8601 timestamp | null>",
  "status": "ACTIVE | SUSPENDED | IDLE",
  "currentObjective": "<string | null>",
  "workItems": [
    {
      "id": "<kebab-case task ID>",
      "title": "<task title (may be Japanese)>",
      "status": "IN_PROGRESS | COMPLETED | PENDING | BLOCKED | REVIEW | ABANDONED",
      "progress": "<latest progress note | null>",
      "worker": "<peer ID | null>"
    }
  ],
  "workerDirectoryRegistry": [
    {
      "taskId": "<task ID>",
      "pattern": "A | B | C",
      "directory": "<absolute path>",
      "project": "<project name | ->",
      "status": "in_use | available"
    }
  ],
  "dispatcher": {
    "peerId": "<renga-peers peer ID>",
    "paneId": "<renga pane ID>"
  },
  "curator": {
    "peerId": "<renga-peers peer ID>",
    "paneId": "<renga pane ID>"
  },
  "resumeInstructions": "<free text | null>"
}
```

---

## フィールド説明

### トップレベル

| フィールド | 型 | 説明 |
|---|---|---|
| `version` | `integer` | スキーマバージョン。現在は `1`。将来の非互換変更時にインクリメント |
| `updated` | `string \| null` | org-state.md の `Updated:` フィールドの値（ISO 8601）。未設定なら `null` |
| `status` | `string` | 組織の状態。`ACTIVE`（稼働中）/ `SUSPENDED`（中断）/ `IDLE`（未使用） |
| `currentObjective` | `string \| null` | 現在の目標（`Current Objective:` フィールド）。未設定なら `null` |
| `workItems` | `array` | 作業アイテム一覧 |
| `workerDirectoryRegistry` | `array` | ワーカーディレクトリ再利用テーブル |
| `dispatcher` | `object \| null` | ディスパッチャーのピア・ペイン情報。未記録なら `null` |
| `curator` | `object \| null` | キュレーターのピア・ペイン情報。未記録なら `null` |
| `resumeInstructions` | `string \| null` | 再開時の注意事項（org-suspend が書く）。なければ `null` |

### workItems 要素

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | `string` | タスク ID（kebab-case 英語）。例: `blog-redesign`, `data-analysis` |
| `title` | `string` | タスク名（日本語可）。org-state.md の `- {id}: {title} [{status}]` から取得 |
| `status` | `string` | タスクの状態（下記参照） |
| `progress` | `string \| null` | 最新の進捗メモ（`- 結果:` サブ項目）。なければ `null` |
| `worker` | `string \| null` | 担当ワーカーのピア ID（`- ワーカー:` サブ項目）。なければ `null` |

**status の値:**

| 値 | 意味 |
|---|---|
| `IN_PROGRESS` | 作業中 |
| `COMPLETED` | 完了（人間が承認済み） |
| `PENDING` | 待機中（まだ開始していない） |
| `BLOCKED` | ブロック中（依存関係や問題あり） |
| `REVIEW` | レビュー中（ワーカーが完了報告済み、人間の承認待ち） |
| `ABANDONED` | 中止 |

### workerDirectoryRegistry 要素

| フィールド | 型 | 説明 |
|---|---|---|
| `taskId` | `string` | そのディレクトリを使用しているタスク ID |
| `pattern` | `string` | ディレクトリパターン: `A`（プロジェクトディレクトリ）/ `B`（worktree）/ `C`（エフェメラル） |
| `directory` | `string` | ワーカーディレクトリの絶対パス |
| `project` | `string` | プロジェクト名。エフェメラルの場合は `-` |
| `status` | `string` | `in_use`（作業中）/ `available`（完了済み・再利用可能） |

### dispatcher / curator

| フィールド | 型 | 説明 |
|---|---|---|
| `peerId` | `string` | renga-peers が割り当てた安定 peer 識別子（`assigned` のとき設定）。`mcp__renga-peers__send_message` の `to_id` に渡す値。例: `peer-dispatcher-001` |
| `paneId` | `string` | renga のペイン id。`peerId` とは独立した識別子で、ペインのライフサイクルに紐付く。例: `pane-42` |

---

## ダッシュボードとの統合

`dashboard/server.py` は以下の優先順位で org-state を読み込みます:

1. `.state/org-state.json` が存在し、かつ mtime が `.state/org-state.md` 以上の場合 → JSON を使用
2. それ以外 → `.state/org-state.md` を正規表現でパース（フォールバック）

この設計により、converter 未実行の環境や JSON が stale な場合でも正常動作します。

---

## バージョン履歴

| バージョン | 変更内容 |
|---|---|
| 1 | 初版。Issue #20 で導入 |
