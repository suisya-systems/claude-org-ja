---
name: org-suspend
description: >
  組織を中断し、全状態をディスクに保存する。「中断」「保存して終了」
  「閉じたい」「一旦やめる」「今日は終わり」と言われたときに使う。
---

# org-suspend: 組織の中断

全ワーカーの状態を収集し、ディスクに保存し、全ペインを停止する。

ペイン操作は `mcp__ccmux-peers__*` MCP ツール経由で行う。pane_exited 相当の
lifecycle イベントは `ccmux-peers` が現状 push/poll を提供するまで（upstream
happy-ryo/ccmux#117 / ccmux PR #120 の `poll_events` merge 待ち）、
`mcp__ccmux-peers__list_panes` の**短間隔ポーリング**で代替する。

## Phase 1: ワーカー状態収集

1. `mcp__claude-peers__list_peers` で稼働中のピアを列挙する（scope: `machine`）
2. 自分自身とキュレーターを除いた全ピアに `mcp__claude-peers__send_message` で以下を送信:
   ```
   SUSPEND: 現在の状態を報告してください。
   1. これまでに完了したこと
   2. 変更したファイル（コミット済み/未コミット）
   3. 次にやろうとしていたこと
   4. ブロッカーや未解決の問題
   ```
3. 30 秒間 `mcp__claude-peers__check_messages` で応答を待つ（5 秒間隔でポーリング）
4. 応答があったワーカーの報告を記録する

## Phase 2: 未応答ワーカーのスクレイプ

応答がなかったワーカーについて:

1. `.state/workers/` から該当ワーカーの状態ファイルを読み、Pane Name と Directory を取得
2. 画面内容スクレイプは upstream happy-ryo/ccmux#116 / ccmux PR #121 で `mcp__ccmux-peers__inspect_pane` が追加済み（ccmux リリース後に利用可能）。利用可能なら:
   ```
   mcp__ccmux-peers__inspect_pane(target="worker-{task_id}", format="text")
   ```
   の結果テキストから最新のコンソール出力を読む。**未実装版 ccmux（リリース前）を使っている間は Step 3 の git 情報だけで推定する**
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

1. `mcp__claude-peers__list_peers` で稼働中のピアを列挙（scope: `machine`）
2. **ワーカーを先に停止**: 全ワーカーピアに `mcp__claude-peers__send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
3. **ワーカーペインが閉じたことを確認** — 2-pass 構造で実施:

   **Pass 1 (polite shutdown の観察、最大 10 秒)**:

   `mcp__ccmux-peers__poll_events` で `pane_exited` を long-poll する。`types=["pane_exited"]` フィルタで他 type を除外しつつ、deadline 内でループして待機対象が全て閉じたら break:
   ```
   pending_workers = {全ワーカーの name set}
   cursor = None                           # 初回は since 省略
   deadline = now + 10 秒
   while pending_workers not empty and now < deadline:
       remaining_ms = (deadline - now) ミリ秒
       result = mcp__ccmux-peers__poll_events(
           since=cursor,
           timeout_ms=min(remaining_ms, 10000),
           types=["pane_exited"]
       )
       cursor = result.next_since
       for ev in result.events:
           if ev.role == "worker" and ev.name in pending_workers:
               pending_workers.remove(ev.name)
   # deadline 到達 or pending_workers が空で抜ける
   ```
   - 初回 `since` 省略で「今以降のイベントだけ」セマンティクス（過去の pane_exited を replay しない）
   - `types=["pane_exited"]` filter は cursor を全 type で advance させるので重複 scan なし
   - filter 不一致イベント到着で long-poll が early return (`events:[]` + advanced cursor) するため、空応答時は deadline までループ継続
   - 10 秒以内に閉じなかった残留ワーカーは Pass 2 へ

   **Pass 2 (残留ワーカーへのフォールバック + 再確認、最大 5 秒)**:
   - Pass 1 で閉じていないワーカーそれぞれに対して:
     ```
     mcp__ccmux-peers__close_pane(target="worker-{task_id}")
     ```
     でペインを明示破棄する。成功時は `"Closed pane id=N."` テキストが返る。`[pane_not_found]` / `[pane_vanished]` は既に閉じた扱いで skip（`references/ccmux-error-codes.md` 参照）。`[last_pane]` はワーカー停止段階では通常発生しない（窓口/フォアマン/キュレーターが残っているため）
   - その後、同じ `poll_events` ループを `timeout_ms=5000` / deadline 5 秒で再度回し、close_pane 由来の `pane_exited` を消化する
   - Pass 2 後もまだ閉じていないワーカーは `mcp__ccmux-peers__list_panes` で生存確認し、残存なら journal に記録して人間に報告（強制終了は現状未サポート）

4. **フォアマンを停止**: フォアマンに `mcp__claude-peers__send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
5. **キュレーターを停止**: キュレーターに `mcp__claude-peers__send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
6. フォアマン・キュレーターも (3) と同じ 2-pass 構造で確認（`pending = {"foreman", "curator"}` を集合に入れ、`role == "foreman"` または `role == "curator"` の `pane_exited` を待つ）:
   - Pass 1: `poll_events(types=["pane_exited"], timeout_ms=10000)` 相当ループ
   - Pass 2: 残った pane に `mcp__ccmux-peers__close_pane(target="foreman")` / `mcp__ccmux-peers__close_pane(target="curator")` を送り、`poll_events` ループ (timeout_ms=5000) で再確認

**最後のペイン (窓口) の扱い**: フォアマン・キュレーターを閉じた時点でタブに残るのは窓口
ペインのみになる。窓口が自分自身を `mcp__ccmux-peers__close_pane(target="secretary")` で
閉じようとすると `[last_pane]` (唯一のタブの唯一のペイン) が返るので、**窓口は自分自身で
`exit` して自然終了させる** (人間が端末を閉じる、または `/exit` でシェルに戻る)。
org-suspend は窓口ペインを閉じる責任を負わない。

7. 人間に報告:
   ```
   組織を中断しました。
   - 保存済み: {N}件の作業アイテム
   - 状態ファイル: .state/org-state.md
   /org-start で再開できます。
   ```
