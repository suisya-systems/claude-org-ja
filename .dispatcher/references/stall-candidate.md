# stall 候補 worker の判定と通知 (Step 5 の (b-2) 〜 (f)) — 詳細

[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) から分けた本体仕様。Step 5 (b) で stall 候補 (hash 不変が連続 3 サイクル以上) が出たサイクルだけ読む（それ以外のサイクルでは読まない）。

   #### (b-2) PR-pending-merge sub-state 判定
   stall 候補について、(c) の補助シグナル取得に進む **前に** PR-pending-merge sub-state を判定し、(c)(1) で使う lookback window を選択する。これは「worker が完了報告を出した後、Secretary が PR を open し、user が merge 承認するまでの待機」を通常 stall と区別するためのカテゴリ。

   `.state/state.db` の `events` テーブルを query し、`json_extract(payload_json, '$.task') == "{task_id}"` (= bare task_id、`worker-` prefix を **含まない**。`pr_opened` / `pr_merged` は `docs/journal-events.md` の "PR / push" 表で Writer = secretary、Emitted by = secretary、payload field `task` 値は task_id 本体と定義済) で次 2 件の **存在有無のみ** を取得 (timestamp は判定に使わない):
   - `kind == "pr_opened"` で同 task_id の行が 1 件以上ある
   - `kind == "pr_merged"` で同 task_id の行が 1 件以上ある

   **payload key は event 種別ごとに固定する**: `pr_opened` / `pr_merged` は `payload_json` の `$.task` が bare task_id、(c)(1) で使う worker 起点 event (`worker_escalation` / `worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered`) は `payload_json` の `$.worker` が `worker-{task_id}` 形式。両者を一律の key に揃えると PR-pending 判定か STALL 抑制のどちらかが常に空になる。

   分岐:
   - `pr_opened` あり かつ `pr_merged` なし → **PR-pending-merge sub-state**。(c)(1) の lookback に `STALL_PR_MERGE_LOOKBACK_MIN = 60` を採用する。`pr_opened` / `pr_merged` は同一 task に対して各 1 件しか記録されない契約 (`tools/run_complete_on_merge.py` 等の helper が idempotent 化) なので、複数行を時系列比較する必要はない (= 「最新の行」を選ぶ必要なし、存在有無で十分)
   - 上記以外 (PR 未 open、または既に merge 済み) → 通常 sub-state。(c)(1) の lookback は `STALL_SECRETARY_LOOKBACK_MIN = 15` のまま

   ```bash
   # ディスパッチャーの cwd は .dispatcher/ なので 1 階層上の .state/state.db を読む
   # (Step 5.2 (c) と同じ SQLite one-liner idiom)。
   # task_id を bare 値で渡す (worker- prefix 無し。pr_opened / pr_merged の payload key は $.task)。
   # EXISTS で 0/1 の真偽値を返す (行の有無を CLI 出力の空/非空や NULL に依存させない)。
   has_pr_opened=$(sqlite3 ../.state/state.db "
     SELECT EXISTS(SELECT 1 FROM events
                   WHERE kind = 'pr_opened'
                     AND json_extract(payload_json, '\$.task') = '{task_id}')
   ")
   has_pr_merged=$(sqlite3 ../.state/state.db "
     SELECT EXISTS(SELECT 1 FROM events
                   WHERE kind = 'pr_merged'
                     AND json_extract(payload_json, '\$.task') = '{task_id}')
   ")
   if [ "$has_pr_opened" = "1" ] && [ "$has_pr_merged" = "0" ]; then
     lookback_min=60   # PR-pending-merge
   else
     lookback_min=15   # default
   fi
   ```

   #### (b-fp) 既知の誤検知パターンと見分け方 — STALL の誤発火 (2026-07-28 実測 2 件)
   (b) の hash 不変判定 + (b-2) の lookback 選択でも捕まえきれない構造的 false positive が 2 型ある。いずれも **STALL_SUSPECTED が出ても即座に異常と断定せず、下記の見分け方で確認してから窓口へ上げる**。

   **型 1: 長時間バッチジョブの完了待ち (lookback 不足による構造的誤発火)**

   実環境デプロイ委譲で、formatter バッチジョブ (約 41 分) の完了待ち中に画面が 26 分以上静止し、`STALL_SECRETARY_LOOKBACK_MIN = 15` の通常 lookback で STALL_SUSPECTED が発火した。窓口が実環境側 (GCS 出力オブジェクト数の増加推移) を直接確認したところ、バッチは正常に進行中で false positive だった。

   - **なぜ (b-2) に乗らないか**: PR-pending-merge sub-state は `pr_opened` / `pr_merged` という **event ledger 上の痕跡**から待機パターンを検知して lookback を延ばす。一方「実行環境側の長時間バッチ完了待ち」は worker が能動的に待っているだけで **worker→secretary event を一切出さない**ため、同種の判定材料が events テーブルに存在せず既存 sub-state 判定に乗らない
   - **見分け方**: 対象ワーカーの brief が実環境デプロイ / データ処理ジョブ (formatter 再実行、バッチ ETL、Cloud Run 実行等) を含むか。含む場合は画面静止だけで stuck と断定せず、**実環境側 (実行ログ、出力オブジェクト数等) を直接確認**してから判断する。40 分級のバッチ待ちが本務のワーカーは、15 分 lookback では構造的に誤検知する
   - **当座の運用**: 窓口の指示で当該ワーカーに限り STALL 通知を抑止し、dispatcher は journal に `stall_suppressed_by_secretary` を記録して次の報告まで通知を止める
   - **恒久対応案 (未実装)**: ワーカー自身が事前に「X ブロックの完了予定 Y 分」を `worker_reported` 等で申告し、その分 lookback を動的に延長する「**長時間バッチ待ち sub-state**」を (b-2) と同じ仕組みで追加する。実測 1 件のみのため一般化は時期尚早で、**同種の委譲が繰り返された時点で起票を検討する**

   **型 2: ultracode Workflow ステータス行の再描画停止 (観測タイミング由来の誤発火)**

   ultracode 武装ペインの監視で、ペイン下部の Workflow ステータス行 (例: `4/5 agents done · 20m 58s · ↓ 597.4k tokens`) が 3 分間隔の監視サイクルで 2〜3 回連続「不変」に見えたため STALL_SUSPECTED を複数回発火した。窓口が同時刻に直接 `inspect_pane` で確認すると、経過時間・トークン数とも実際には進んでいた (**dispatcher 観測=不変 → 窓口観測=前進、が 3 回連続**)。

   - **なぜ起きるか**: このステータス行は**出力バーストのタイミングでしか再描画されない** UI で、長時間走る単一エージェントの最中は表示自体が固まって見える。dispatcher と secretary の観測タイミングの差 (同一 3 分窓内でも数十秒〜1 分ずれる) で描画更新の直前 / 直後を引くと不変に見える。ultracode 実行中はワーカー本体のターンが「1 個の長時間 dynamic workflow 呼び出しを待っている」状態で、**本体スクロールバックが変化しないのが正常**であり、(b) の「画面が変化しなければ idle」というモデルに反する
   - **(b-3) の active-spinner suppress ではカバーされない**: Workflow 待機中の親ターンには新形式 spinner が出ない (または出ても親ターン自体は本当に何もしていない) ため、`suppress_stall` 経路に乗らない
   - **ultracode 武装ペインの報告基準 (2026-07-28 方針変更)**: `Workflow(...)` 呼び出し後 `Waiting for N dynamic workflow to finish` 表示になっているペインでは、**ステータス行の数値 (agents done / 経過時間 / トークン数) による STALL 判定を行わない**。代わりに次の 2 つのみを報告基準とする:
     1. worker→secretary peer message が **60 分以上**途絶
     2. ペイン本体が Waiting 表示から離れ、Step 4 の APPROVAL_BLOCKED / ERROR パターンに入った
   - **非 ultracode ペインは従来どおり**: (b) の 15 分 lookback + content hash 比較による STALL 判定を継続する
   - **恒久対応案 (未実装)**: 監視ツール側での正式な ultracode sub-state 追加を Issue 化検討中 (窓口起票予定)。2026-07-28 に 3 回連続の false positive を実測済み

   #### (b-4) 発火前の観測面チェック — 報告痕跡の不在だけで STALL を申告しない ([観測の原則](worker-monitoring.md#observation-principle) の適用)
   `STALL_SUSPECTED` は「worker が異常停止している」という**対象の状態**の申告なので、(P2) により独立した複数面の一致を要求する。チェックは 2 段に分かれ、**評価順は (b-4-i) → (b-2) → (c) → (b-4-ii) → (d)** である (台帳面は (c) の scan を回さないと値が出ないので、(c) より前に判定しない):

   **(b-4-i) (c) に進む前に確認する (画面側 2 点)**:

   1. **画面面**: (b) の `content_hash` が 3 サイクル不変であること — **かつその 3 サイクルとも `inspect_pane` が実際に成功していること**。エラーで画面を観測できなかったサイクルは Step 4 のエラー分岐と (P4) のとおり record を更新しないので streak には積まれない (観測できなかった時間が「静止していた」に化けない)
   2. **busy の陰性確認**: (b-3) の `suppress_stall == false` であること。新形式 active spinner が生きている間は busy の**陽性証拠**なので発火しない。ただし spinner が**無いこと**は idle の証拠ではない ((P3)) ので、これは独立した 1 面であって画面面 (1) の代替にはならない

   どちらかが欠けたら (c) の補助シグナル取得を回さずに打ち切る (画面が観測不能なら (P4) を報告、busy なら (b-3) の soft-note を残して次サイクルへ)。

   **(b-4-ii) (c) の scan 後、(d) の timeout 分岐に入る直前に確認する (台帳側 1 点)**:

   3. **台帳面**: (c)(1) の worker→secretary 痕跡が lookback 窓内に 0 件であること — **かつ (c)(1) の SQLite query が実際に成功していること**。`.state/state.db` が読めずに 0 行だったなら、それは「痕跡が無い」ではなく観測不能である ((P3): 「痕跡が無い」は「読めなかった」を含意しない)

   **「報告痕跡が無い＝止まっている」ではない**: worker の報告は**ターン境界**で出るため、1 ターンが長い作業 (実装 → セルフレビュー → codex ゲートを 1 ターン内で連続実行) の最中に痕跡が出ないのは正常である。台帳面 1 面だけを見て発火したのが 2026-08-08 の実誤検知 2 件の型で、これは (b-fp) 型 1 / 型 2 と同じ族に属する。画面面が「動いている」または「観測できていない」なら、台帳面が 0 件でも申告しない。

   **観測不能な面が 1 つでもあれば STALL を申告しない**: (b-4-i) の 1 が満たせない (inspect が使えない) 間は台帳面だけで stuck を推測せず、(b-4-ii) の 3 で query が失敗した場合も「痕跡 0 件」として扱わず、いずれも (P4) の `OBSERVATION_UNAVAILABLE` を該当 `source` で 1 回報告して次サイクルへ送る。**この保留は「代わりに自分で対処してよい」ではない** ((P5)) — やることは報告と再評価であって、ペインへの介入ではない。

   **残余ケースと通知の書き方**: 3 面すべてが「活動を観測できない」で一致しても、それは *stuck の確定* ではない — 出力が凍り新形式 spinner も出ないまま正常な長い 1 ターンを回している worker は (b-4) を通過しうる ((b-fp) の型 1 = 長時間バッチ完了待ち / 型 2 = ultracode Workflow 待機がその実例で、**これらは (b-fp) 側の報告基準が優先する**)。したがって STALL_SUSPECTED は「worker が壊れている」という診断ではなく「**どの観測面にも活動が見えない**」という観測の申告として送る: 通知本文には観測した内容 (どの面を・どの窓で見て・何件だったか) を書き、原因の断定や復旧の提案を書かない。窓口が人間に上げる判断材料はこの観測であって、dispatcher の推測ではない ((P5))。

   #### (c) 補助シグナル取得 — 直近の worker→secretary コミュニケーション
   stall 候補が (b-4-i) の画面側 2 点を通ったら、STALL_SUSPECTED を発火する **前に** 補助シグナルを取得する ((b-4-ii) の台帳面はこの scan 結果で評価する)。lookback は (b-2) で選択した値 (`STALL_SECRETARY_LOOKBACK_MIN = 15` または `STALL_PR_MERGE_LOOKBACK_MIN = 60`) を使う:

   1. **events scan (primary, authoritative)**: `.state/state.db` の `events` テーブルを読み、`occurred_at >= now - lookback_min minutes` ((b-2) で選択した値) でフィルタし、以下のいずれかの event を持つ行が 1 件でもあるか確認する。これらの worker 起点 event は `payload_json` の `$.worker` が `worker-{task_id}` 形式 ((b-2) の `pr_opened` / `pr_merged` が使う `$.task` = bare task_id とは **別 key**):
      - `kind == "worker_escalation"` かつ `json_extract(payload_json, '$.worker') == "worker-{task_id}"` (judgment request の受信)
      - `kind == "worker_reported"` かつ `json_extract(payload_json, '$.worker') == "worker-{task_id}"` (mid-task progress の受信)
      - `kind == "worker_completed"` かつ `json_extract(payload_json, '$.worker') == "worker-{task_id}"` (完了報告の受信、`REVIEW` 待機中の idle 区別用)
      - `kind == "plan_delivered"` かつ `json_extract(payload_json, '$.worker') == "worker-{task_id}"` (plan 引き渡しの受信)
      - `kind == "prep_delivered"` かつ `json_extract(payload_json, '$.worker') == "worker-{task_id}"` (prep 引き渡しの受信)

      これらはいずれも worker 起点の `send_message` を secretary が受信した時点で append される ledger なので、worker→secretary コミュニケーションの authoritative な痕跡になる。`worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered` は `docs/journal-events.md` の **Emitted by = worker** + **Writer = secretary** 行で定義されている。`worker_escalation` は同 catalog 未掲載だが本 `CLAUDE.md` 「ワーカーからの判断仰ぎは人間にエスカレーションする」節および `.claude/skills/org-delegate/SKILL.md` Step 5 で書き込み手順が明文化されている (catalog への追記は curator 領域、本 PR スコープ外)。将来 catalog に **Emitted by = worker** な event が追加された場合は本リストにも追加する (catalog と同期する宣言的リスト)。

      ```bash
      # ディスパッチャーの cwd は .dispatcher/ なので 1 階層上の .state/state.db を読む
      # (Step 5.2 (c) と同じ SQLite one-liner idiom)。
      # 時間窓ベースの抽出 (行数 cap で打ち切らないこと、events が長期間追記され続けても
      # lookback 窓は occurred_at で正確に区切る)。
      # 通常時 lookback_min=15、PR-pending-merge sub-state では lookback_min=60 ((b-2) で決定)。
      # cutoff は shell の date ではなく SQLite の strftime で計算する: occurred_at は
      # sub-second 精度 ('%Y-%m-%dT%H:%M:%fZ') が正本なので、秒精度 cutoff と文字列比較すると
      # 境界秒を辞書順で取りこぼす。SQLite 側計算なら精度が揃い、shell 差分 (PowerShell の
      # date 代替等) も不要になる。
      sqlite3 ../.state/state.db "
        SELECT DISTINCT kind FROM events
        WHERE occurred_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-${lookback_min} minutes')
          AND kind IN ('worker_escalation','worker_reported','worker_completed','plan_delivered','prep_delivered')
          AND json_extract(payload_json, '\$.worker') = 'worker-{task_id}'
        ORDER BY kind
      "
      # 1 行以上返れば「ヒット」。返却された kind の集合は (c)(1-bis) の gate で
      # worker_escalation だけを除外する際の入力にもなる。
      ```

   **(c)(1-bis) decision-register gate**: (c)(1) で `worker_escalation` がヒットした場合に限り、それを acked に数える **前に** pending-decisions register を照会する。journal の `worker_escalation` 行は judgment request 受信時点の痕跡として残り続けるが、その後ユーザー返答が secretary 経由で worker へ転送済み (= 判断待ちが解消済み) でも 15 分窓内に古い行が残っていると acked 抑制が続き、転送後に 529 / stuck で idle な worker を取りこぼす。lifecycle の真値は journal ではなく register が持つので、register を **primary lookup source** として照会する:

      ```bash
      # ディスパッチャーの cwd は .dispatcher/ なので 1 階層上の tools/ を呼ぶ。
      # task_id は bare 値で渡す (worker- prefix 無し)。PowerShell 環境は py -3。
      python3 ../tools/pending_decisions.py latest-resolution --task-id {task_id}
      ```

      出力は 1 行 JSON。`escalation_trace_is_stale` が `true` (= その task_id の最新 decision が `status == "resolved"` かつ `resolution_kind == "to_worker"`、ユーザー返答後に secretary が `resolve --kind to_worker` で worker へ回答転送済) なら、`worker_escalation` 行はもはや open な判断待ちを表さないので **(c)(1) のヒット集合から `worker_escalation` を除外する**。他 4 event (`worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered`) はこの gate の影響を受けない (除外対象は `worker_escalation` のみ)。

      register は `.state/pending_decisions.json` (dispatcher cwd から `../.state/pending_decisions.json`)。`worker_escalation` 行が journal に残り続けても、judgment-escalation の lifecycle (pending → escalated → resolved) の authoritative な真値は register が持つ、という SoT 注記である (Step 5.1 (a-0) / (a-2) と同じ register を別軸で照会する)。

      **register-unavailable fallback** (Step 5.1 (a-0) / (a-2) と同じ degraded-mode 規約): CLI が非ゼロ終了する / 出力 JSON が読めない (helper not found / file corrupted で `ValueError`) 場合は、`escalation_trace_is_stale` を取得できないので **gate を適用しない** (= `worker_escalation` を従来どおり acked に数える、fail-safe 側)。これにより register 故障時に判断待ち idle を誤って timeout 経路へ落として false STALL を撃つことはない (代償として、register が壊れている間は #603 の取りこぼし修正が無効化され修正前の挙動に戻る)。観測性のため journal に `anomaly_observed source=stall_check kind=register_unavailable` を残す:
      ```bash
      bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=register_unavailable confidence=n/a
      ```

   2. **org-broker poll_events は使わない**: 現状 `mcp__org-broker__poll_events` は pane lifecycle event のみを流し `send_message` を流さず、`since` は opaque cursor で過去 15 分を検索できない。events scan を **authoritative source** とする。

   #### (d) 分岐 (acked vs timeout)
   - **acked の判定** ((c)(1-bis) の gate 込み): acked ⟺ (`worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered` のいずれかが lookback 窓内) **OR** (`worker_escalation` が lookback 窓内 **AND NOT** `escalation_trace_is_stale`)。`worker_escalation` が窓内でも (c)(1-bis) で `escalation_trace_is_stale == true` と判定された場合は acked に数えず、かつ他 4 event のヒットも無ければ **timeout 経路へ落ち**、(従来は古い `worker_escalation` 痕跡で抑制されていた) STALL_SUSPECTED が正しく発火する。soft-note の `note` 値は既存 (`awaiting_secretary_lookback_15m` / `awaiting_pr_merge_lookback_60m`) を維持する。
   - **acked** — 上記判定で 1 件以上ヒット: 「Secretary 判断待ち idle」または「PR-pending-merge 待機 idle」とみなし、STALL_SUSPECTED を **発火しない**。Step 4 (e) と同じ `anomaly_observed` ledger に soft-note として記録 (新 event 名は導入せず既存 catalog を再利用)。`note` は (b-2) で選択した sub-state を反映する:
     ```bash
     # 通常 sub-state (lookback 15m)
     bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=stall_acked confidence=n/a note=awaiting_secretary_lookback_15m
     # PR-pending-merge sub-state (lookback 60m)
     bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=stall_acked confidence=n/a note=awaiting_pr_merge_lookback_60m
     ```
     以降のサイクルで events エントリが lookback window から外れて 0 件になれば、改めて (c) → (d) を再評価する (持続的 stuck の検出が遅れる代償として、判断待ちの誤発火を避ける trade-off)。

   - **timeout** — 両系統とも痕跡なし、idle 継続: **(b-4-i) の 2 点と (b-4-ii) の 1 点をすべて満たしていることを確認した上で** (1 つでも欠けるなら発火せず (P4) の観測不能に倒す)、従来通り stall として扱い、窓口に通知 (lookback は (b-2) で選択した値、通知文に分単位で埋める)。**ただし (b-3) の active-spinner suppress が有効 (`suppress_stall == true`) の間は本 timeout 通知も保留する** — helper が cap 未到達の increasing spinner を検出している間は正常な長考なので発火せず、`kind=spinner_active_suppress` の soft-note のみ残す。cap 到達で `suppress_stall == false` に転じたサイクルで初めて発火する:
     ```
     mcp__org-broker__send_message(to_id="secretary", message="
       STALL_SUSPECTED: worker-{task_id} が直近 3 サイクル idle、
       過去 {lookback_min} 分以内に secretary 向け worker→secretary 送信痕跡
       (worker_escalation / worker_reported / worker_completed /
       plan_delivered / prep_delivered) なし。stuck の可能性あり、確認願います。
     ")
     ```
     通知後、Step 4 (e) の de-dup スキーマと整合させて `notify_sent` を journal に追記:
     ```bash
     bash ../tools/journal_append.sh notify_sent source=stall_check worker=worker-{task_id} kind=stall_suspected confidence=n/a
     ```

   #### (e) de-dup
   Step 4 (e) と同じ 30 秒窓を共有し、直近 30 秒以内に `(worker, kind=stall_suspected)` の `notify_sent` があれば再通知をスキップする。stall は本質的に長時間の状態なので、3 分サイクル毎に再通知するとノイズになる。worker が完了するか acked 経路に入るまで沈黙でよい (at-least-once 担保のため失敗時は次サイクルで再試行される)。

   #### (f) 60 分超過かつ `ci_completed` が `status="incomplete"` のとき
   PR-pending-merge sub-state の worker が拡張 lookback (60 分) を超過して timeout 経路に入り、かつ events の `ci_completed` payload が `status="incomplete"` のとき、`incomplete` は CI red では **なく判定不能** である (`tools/pr_watch.py` の `gh pr checks --json` クエリが final verdict を取得できないまま retry budget を使い切り、exit-code fallback として記録した値。`status` の値域は event catalog `docs/journal-events.md` を参照)。dispatcher は自分で `gh pr checks` 等を叩いて調査・再試行 **しない** (役割境界: dispatcher は調査しない)。やることは状態の記録 (通常の timeout 経路の soft-note / `notify_sent` に留める) と、secretary への informational 報告 (「`ci_completed` が `status="incomplete"` で CI 判定不能のまま。merge / pane 自動クローズが進んでいない可能性あり」の趣旨) のみ。
