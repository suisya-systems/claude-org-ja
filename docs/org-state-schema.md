# org-state.json スキーマ定義

## 概要

`.state/org-state.json` は `.state/org-state.md` の機械可読スナップショットです。
ダッシュボード（`dashboard/server.py`）その他のプログラム的消費者が JSON を優先的に読み込めるようにするために導入されました。

### Source of truth ルール

**Markdown が正本、JSON は派生です。**

- `org-state.md` が常に正本です。Claude Code インスタンスが直接読み書きします。
- `org-state.json` は派生ファイルです。`org_state_converter.py` が生成します。
- `org-state.md` を手動編集した場合は、必ず converter を再実行してください。
- 両者が矛盾する場合は `org-state.md` を信頼してください。

### JSON の再生成

```bash
py -3 dashboard/org_state_converter.py      # Windows
python3 dashboard/org_state_converter.py     # Mac/Linux
```

### 更新ポイント

以下の操作を行った後、converter を実行して JSON を更新してください:

| 操作 | スキル | 更新内容 |
|---|---|---|
| ワーカー派遣 | org-delegate Step 4 | Current Objective, Active Work Items, Worker Directory Registry |
| ステータス変更 | org-delegate Step 5 | Work Item のステータス（REVIEW/COMPLETED/IN_PROGRESS） |
| 組織中断 | org-suspend Phase 3 | Status=SUSPENDED, Updated, Work Items, Resume Instructions |
| 組織再開 | org-resume Phase 4 | Status=ACTIVE |
| 起動（Foreman/Curator 記録） | org-start Steps 2-3 | Foreman/Curator のピア ID とペイン名 |

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
  "foreman": {
    "peerId": "<peer ID>",
    "paneId": "<renga pane name>"
  },
  "curator": {
    "peerId": "<peer ID>",
    "paneId": "<renga pane name>"
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
| `foreman` | `object \| null` | フォアマンのピア・ペイン情報。未記録なら `null` |
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

### foreman / curator

| フィールド | 型 | 説明 |
|---|---|---|
| `peerId` | `string` | renga-peers のペイン名（`worker-{task_id}` / `foreman` / `curator` 形式）。`mcp__renga-peers__send_message` の `to_id` に渡す値 |
| `paneId` | `string` | renga のペイン名 (`--id` で命名したもの、例: `foreman`, `curator`)。旧 WezTerm 時代は数値の pane-id を格納していたが、ccmux 移行に伴い安定名ベースに変更。現行仕様では `peerId` と同値になることが多い |

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
