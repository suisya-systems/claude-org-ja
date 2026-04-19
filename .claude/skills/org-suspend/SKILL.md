---
name: org-suspend
description: >
  組織を中断し、全状態をディスクに保存する。「中断」「保存して終了」
  「閉じたい」「一旦やめる」「今日は終わり」と言われたときに使う。
---

# org-suspend: 組織の中断

全ワーカーの状態を収集し、ディスクに保存し、全ペインを停止する。

## Phase 1: ワーカー状態収集

1. claude-peers の `list_peers` で稼働中のピアを列挙する（scope: machine）
2. 自分自身とキュレーターを除いた全ピアに以下を送信:
   ```
   SUSPEND: 現在の状態を報告してください。
   1. これまでに完了したこと
   2. 変更したファイル（コミット済み/未コミット）
   3. 次にやろうとしていたこと
   4. ブロッカーや未解決の問題
   ```
3. 30秒間 `check_messages` で応答を待つ（5秒間隔でポーリング）
4. 応答があったワーカーの報告を記録する

## Phase 2: 未応答ワーカーのスクレイプ

応答がなかったワーカーについて:

1. `.state/workers/` から該当ワーカーの状態ファイルを読み、Pane ID と Directory を取得
2. `wezterm cli get-text --pane-id {pane_id}` でペインの表示内容を取得する（可能な範囲で）
3. ワーカーの作業ディレクトリで以下を実行:
   - `git status`
   - `git diff --stat`
   - `git log --oneline -5`
4. これらの情報からワーカーの状態を推定する

## Phase 3: 状態書き込み

1. 既存の `org-state.md` を `org-state.prev.md` にコピー（バックアップ）
2. `org-state.md` を更新:
   - Status を `SUSPENDED` に変更
   - Updated を現在時刻に更新
   - 各 Work Item の状態を収集した情報で更新
   - Resume Instructions に再開時の注意事項を記載
3. JSON スナップショットを再生成する:

   ```bash
   py -3 dashboard/org_state_converter.py    # Windows
   python3 dashboard/org_state_converter.py   # Mac/Linux
   ```

4. 各ワーカーの `.state/workers/worker-{id}.md` を更新:
   - Current State at Suspend セクションを追加/更新
   - Progress Log に中断時の状態を追記
5. `journal.jsonl` に suspend イベントを追記:
   ```json
   {"ts":"<ISO timestamp>","event":"suspend","reason":"user_requested","active_workers":["worker-xxx"],"pending_items":["blog-redesign"]}
   ```

## Phase 3.5: ダッシュボードサーバー停止

```bash
kill $(cat .state/dashboard.pid 2>/dev/null) 2>/dev/null || true
```

## Phase 4: 全ペイン停止

停止順序が重要。ワーカー → フォアマン → キュレーターの順で停止する。

1. claude-peers の `list_peers` で稼働中のピアを列挙
2. **ワーカーを先に停止**: 全ワーカーピアに `send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
3. ワーカーのペインが閉じたことを確認（フォアマンに `CLOSE_PANE` を依頼、またはフォールバックとして `wezterm cli kill-pane --pane-id {pane_id}` で直接クローズ）
4. **フォアマンを停止**: フォアマンに `send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
5. **キュレーターを停止**: キュレーターに `send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
6. フォアマン・キュレーターのペインが閉じない場合は `wezterm cli kill-pane --pane-id {pane_id}` で強制クローズ
7. 人間に報告:
   ```
   組織を中断しました。
   - 保存済み: {N}件の作業アイテム
   - 状態ファイル: .state/org-state.md
   /org-start で再開できます。
   ```
