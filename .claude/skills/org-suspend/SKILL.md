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

1. `.state/workers/` から該当ワーカーの状態ファイルを読み、Pane Name と Directory を取得
2. (ccmux にはペイン表示内容スクレイプの API が未実装。Phase 2 の `ccmux events` 実装待ち)
   当面は git 情報のみで状態を推定する
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
3. **ワーカーペインが閉じたことを確認** — 2-pass 構造で実施:

   **Pass 1 (polite shutdown の観察、最大 10 秒)**:
   ```bash
   ccmux events --timeout 10s \
     | jq -c 'select(.type == "pane_exited" and .role == "worker")'
   ```
   - 出力された各行の `name` を `worker-{task_id}` と対応付けて、完了リストを作る
   - 10 秒経過で `ccmux events` が自動 exit

   **Pass 2 (残留ワーカーへのフォールバック + 再確認、最大 5 秒)**:
   - Pass 1 で `pane_exited` を確認できなかったワーカーそれぞれに対して:
     ```bash
     ccmux send --name worker-{task_id} --enter "exit"
     ```
     でシェル終了を促す
   - その後、再度:
     ```bash
     ccmux events --timeout 5s \
       | jq -c 'select(.type == "pane_exited" and .role == "worker")'
     ```
     で残りの `pane_exited` を収集
   - Pass 2 後もまだ閉じていないワーカーは `ccmux list` で生存確認し、残存なら人間に報告 (強制終了は現状未サポート)

4. **フォアマンを停止**: フォアマンに `send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
5. **キュレーターを停止**: キュレーターに `send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
6. フォアマン・キュレーターも (3) と同じ 2-pass 構造で確認:
   - Pass 1: `ccmux events --timeout 10s | jq 'select(.type=="pane_exited" and (.role=="foreman" or .role=="curator"))'`
   - Pass 2: 残った pane に `ccmux send --name foreman --enter "exit"` / `--name curator --enter "exit"` を送り、`ccmux events --timeout 5s` で再確認

**TODO (Phase 3)**: 明示的な `ccmux close --name X` API が入れば、シェル終了を経由せずに pane を
直接破棄できるようになる。それまではシェル exit + `pane_exited` イベント確認の組み合わせで運用。
7. 人間に報告:
   ```
   組織を中断しました。
   - 保存済み: {N}件の作業アイテム
   - 状態ファイル: .state/org-state.md
   /org-start で再開できます。
   ```
