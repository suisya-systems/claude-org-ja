# ディスパッチャー worker-monitoring 設計知見

`.dispatcher/references/worker-monitoring.md` の検知ロジック設計時に出てきた、再現性のある落とし穴。

## 同一監視サイクル内での時刻順序を明示的にモデル化する（PANE_OUTPUT_WITHOUT_PEER_MSG）

worker-monitoring の 1 サイクルは Step 1 (poll_events) → Step 2 (check_messages) → Step 3 (list_panes) → Step 4 (inspect_pane) → Step 5 / 5.1 / 5.2 評価、の順で**直列に実行される**。Step 2 で worker→secretary peer-msg を受信すると、secretary はその時点で events table に `occurred_at = T_msg` で永続化する。Step 4 の `inspect_pane` で初めて画面変化を検知して `last_content_change_ts` を更新する設計だと、現サイクルの inspect 時刻 (= `now`) は必ず `T_msg` より後 (`T_msg < T_inspect`) になる。

このとき (c)(ii) の `WHERE occurred_at >= last_content_change_ts` クエリで、正当な peer-msg `T_msg` が cutoff に弾かれてしまう。後続サイクルで `last_content_change_ts` 以降に worker→secretary 痕跡なしと判定 → fire という誤発火が起きる。

### 解法: 「前サイクルの `last_check_ts`」を `last_content_change_ts` に採用

idle→active 遷移時、`last_content_change_ts` を **現サイクルの inspect 時刻**ではなく **前サイクルの `last_check_ts`** にする。前サイクルの `last_check_ts` は「最後に画面が idle と確認できていた時刻」で、現サイクルで届く peer-msg より必然的に古い。これで (c)(ii) の cutoff は peer-msg より古くなり、acked と正しく判定される。

active 継続中（出力が複数サイクルにわたって連続）は `last_content_change_ts` を **据え置く**（active 期間の START 時刻を保持）。active 期間中に届いた進捗 peer-msg を全て acked 経路に乗せるため。

### 一般化できる教訓

監視ループが直列実行されるとき、**同サイクル内で発生する複数イベントの時刻順序は明示的にモデル化する**。「イベント A の永続化時刻 vs イベント B の観測時刻」のような場面で、cutoff を「観測時刻」(= `now`) に置くと cutoff が後発イベントを除外する race を生む。

cutoff の正しい起点は「直前まで対象状態が継続していた時刻の上限」(= 前サイクルの末尾時刻)。Step 5 (stall) と Step 5.1 (relay gap) は「window 内に痕跡があるか」の存在判定で時間順序を厳密に要求しなかったため race が顕在化していなかったが、新しい検知パターンを追加する際は同サイクル race の有無を desk-check すべき。

## Codex iterative review はラウンドごとに別の抽象レベルを露呈する

PANE_OUTPUT_WITHOUT_PEER_MSG の 3 ラウンドレビューで指摘されたバグは、それぞれ独立した抽象レベルで露呈した:

- **Round 1 Blocker**: 時間 window のスコープが誤っていた（15 分窓 vs `last_content_change_ts` 起点）
- **Round 1 Major**: 検知粒度の精度が緩かった（idle ≥ 1 vs ≥ 2）
- **Round 2 Major**: 記述同士の整合性 — (b)(5) と (c)(ii) の論理矛盾（Round 1 修正の副作用）
- **Round 3 Major**: 同サイクル race — Round 1+2 の正しい論理を仮定しても残るタイミングバグ

各ラウンドは前のラウンドで露呈した粒度の問題を解いた後でなければ見えない。1 回のレビューで「全部出しきる」のは構造的に難しく、3 ラウンド上限の運用契約は妥当（Blocker/Major が連続して出続けるなら設計やり直し signal、出ない/Minor 化したら収束 signal、と読める）。

出典: `2026-05-09-pane-output-without-peer-msg-race.md`

## dispatcher_retro_gate と channel 通知の不整合

`tools/dispatcher_retro_gate.py --attempt N` は内部で `check_messages` をポーリングして secretary の ack を検出する設計。しかし secretary が `send_message(to_id="dispatcher", ...)` ではなく **renga-peers の channel broadcast 形式**（dispatcher の system-reminder として届く形式）で返答した場合、gate は `check_messages` キューに何も入らないため検出できず、`status: "polling"` のまま全 10 attempt を消費する。

### 実例

`phase3-doc-fix-issue-ref` のクローズ時 (2026-05-09): secretary の YES ack が channel 通知で届いたため gate が検出できず timeout。実際には ack 確認済みとして手動でペインクローズを続行（retro_deferred は書かなかった）。

### 影響

- gate が ack を拾えず timeout → 本来は不要な retro_deferred 記録または手動判断が必要になる。

### 暫定回避（運用側）

channel 通知で secretary ack が届いた場合、dispatcher が直接「ack 受領済み」と判断して retro 手順を続行する。retro_deferred は書かない。

### 根本対策（要設計）

1. retro gate に `inspect_pane` ベースのフォールバックを追加し、secretary ペインの画面を見て YES/NO パターンをスキャンする経路を持たせる、または
2. secretary 側で retro gate ack は必ず `send_message(to_id="dispatcher")` で返す規約を明文化する。

両受信経路（`check_messages` キュー / channel broadcast）を retro gate が考慮していないのが構造的な原因。

出典: `2026-05-09-retro-gate-channel-notification-gap.md`
