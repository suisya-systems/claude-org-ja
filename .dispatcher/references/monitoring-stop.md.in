# 監視ループの停止 (Step 7) — 詳細

[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) から分けた本体仕様。監視対象のワーカーペインが 0 件で、ループを止めるか判断するサイクルだけ読む（それ以外のサイクルでは読まない）。


ワーカーペインがない場合は `poll_events` / `check_messages` / `inspect_pane` をすべてスキップし、監視ループを停止する。**ただし次のいずれかが存在する間は停止しない**:
   - `.state/dispatcher/curate-inflight.json` が存在する間: Step 1 (`poll_events`) / Step 2 (`check_messages`) / Step 5.3 だけを継続し (worker 向けの Step 3〜5.2 は対象が無いので skip)、inflight 解消 (Step 5.3 (a)/(b)/(c) のいずれか) 後のサイクルで停止する
   - **未配送の終端イベントが残っている間**: `python3 ../tools/relay_scan.py --recipient secretary --cycle` の `items` が非空である間は Step 5.25 だけを継続する。PR merge 後に worker pane が閉じても `ci_completed` 等の配送漏れをカバーするため、pane 不在でも relay を走らせる。`items` が空配列 (`[]`) だったサイクルで (curate-inflight も無ければ) 停止する
   - **unknown / indeterminate として保持している worker が居る間 ((3-a-4))**: `list_panes` に列挙されないだけで退役が**確定していない** worker は監視対象・active のままなので、**「ワーカーペインがない」に数えない**。当該 worker については Step 4 / Step 5 / Step 5.2 を skip するが、Step 1 / Step 2 / Step 3 / Step 5.1 は通常どおり回し、次サイクルで (3-a-2) を再評価する。停止できるのは、その worker が (3-a-2) の 1 行目 / 4 行目で**終了確定した**か、窓口が `.state/workers/worker-*.md` を終端状態へ遷移させたサイクル以降
   - **`placement == "background_tab"` の worker が居る間 ((3-a-5))**: `list_panes` に出ないのは配置上の定数なので、同じく **「ワーカーペインがない」に数えない**。こちらは Step 4 / Step 5 / Step 5.2 も `bound_pane_id` 宛で**通常どおり回す** (skip しない)。停止できるのは、attribution 済みの `pane_exited` で**終了確定した**か、窓口が `.state/workers/worker-*.md` を終端状態へ遷移させたサイクル以降

   **停止する前に、意図的停止であることを記帳する**:

   ```bash
   python3 ../tools/relay_scan.py --recipient secretary --mark-halted
   ```

   Step 7 の停止は設計どおりの停止なので、これを記帳せずに止まると「停止中に終端イベントが 1 件でも入った」翌セッションの初回 audit がそれを `RELAY_SCAN_STALE` として報告する — **異常が無いのに鳴る alert は、読み手が無視することを学習する**。記帳は heartbeat に `halted_at` を書くだけで `last_scan_at` は書き換えない (trace は正直なまま)。抑止されるのは再開直後 1 回の audit だけで、**その抑止を使った audit 自身がマーカーを消す**ため以後は通常判定に戻る。記帳せずに死んだループは従来どおり finding になるので、検出力は落ちない。

   **exit で分岐する**: **exit 10 = 記帳を拒否** — 未配送の終端イベントが残っている (最後の `--cycle` とこの記帳の間に終端イベントが入った競合)。**停止せず Step 5.25 を続行する** (Step 7 の停止条件「relay 空」をツール側で再確認している)。**exit 2 = 記帳に失敗** — 記帳が唯一の副作用なので握り潰さない (握り潰すと「記帳できたつもりで停止 → 翌セッションで偽 alert」になる)。停止前に窓口へ `RELAY_SCAN_HALT_UNRECORDED: <stderr 1 行>` を送る。exit 0 のときだけ記帳済みとして停止してよい。

監視対象のペイン名は `.state/workers/worker-{task_id}.md` の Pane Name (`worker-{task_id}`) から取得する。
