# PANE_OUTPUT_WITHOUT_PEER_MSG の補助シグナル・分岐・通知 (Step 5.2 の (c) 〜 (g)) — 詳細

[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) から分けた本体仕様。Step 5.2 (b) の条件 (1)〜(3-bis) を満たす worker が出たサイクルだけ読む (条件 (4)(5)(6) はこのファイルで判定する)（それ以外のサイクルでは読まない）。

   #### (c) 補助シグナル取得 — worker→secretary 痕跡 (2 段問い合わせ)
   Step 5 (c)(1) と同じ event 集合 ({`worker_escalation`, `worker_reported`, `worker_completed`, `plan_delivered`, `prep_delivered`}) を 2 つの観点で query する。**look-back window は使わない** (Codex round 1 Blocker 対応): 「`last_content_change_ts` 起点で peer-msg 痕跡を見る」のが正しい sematics で、固定 window だと PR 待ち / レビュー待ちなど正常 idle の worker で痕跡が window から外れて誤発火する。

   M4 cutover (`docs/journal-events.md` 参照) で journal は `.state/state.db` の `events` テーブルが SoT になっているため、SQLite で直接 query する:

   **(c)(i) existence check — worker は過去に peer-msg を発行したか** ((b)(4) の評価入力):

   ```bash
   # ディスパッチャー cwd は .dispatcher/。state.db は repo root の .state/ 下。
   sqlite3 ../.state/state.db "
     SELECT COUNT(*) FROM events
     WHERE kind IN ('worker_escalation','worker_reported','worker_completed','plan_delivered','prep_delivered')
       AND json_extract(payload_json, '\$.worker') = 'worker-{task_id}'
   "
   ```

   返却が `0` なら起動 ack 含めて peer-msg 履歴なし → (b)(4) 不成立で fire 候補から除外。

   **(c)(ii) 直近 peer-msg ts (`last_content_change_ts` 以降に絞り込む)** ((b)(5) の評価入力):

   ```bash
   sqlite3 ../.state/state.db "
     SELECT MAX(occurred_at) FROM events
     WHERE occurred_at >= '${last_content_change_ts}'
       AND kind IN ('worker_escalation','worker_reported','worker_completed','plan_delivered','prep_delivered')
       AND json_extract(payload_json, '\$.worker') = 'worker-{task_id}'
   "
   ```

   - 返却 `MAX(occurred_at)` が空 (NULL) → ペイン活動後に worker→secretary 痕跡なし → (b)(5) 成立 → fire 候補
   - 返却が `last_content_change_ts` 以降の ts → 既に peer-msg 発行済 → (b)(5) 不成立 → (d) acked 経路

   `last_content_change_ts` を WHERE 句に直接埋め込むことで「`last_content_change_ts` 以降の event」だけを scan するので、PR レビュー待ち等で 15 分以上前の `worker_completed` だけしかない worker でも、その worker_completed の ts ≥ last_content_change_ts であれば NULL ではない値が返り fire しない (Codex round 1 Blocker)。

   #### (d) 分岐 (completion-review-skip vs reopen-self-heal vs acked vs fire vs no-baseline)
   - **completion-review-skip** — (b)(6) で `completion_reported_at != null` かつ `runs.status == 'review'` (= worker 完了報告済み・review 待ちの正常 idle): **fire しない**。(c) の SQL query は発行せず soft-note のみ追記して次サイクルへ:
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=pane_output_check worker=worker-{task_id} kind=pane_output_completion_review_skip confidence=n/a note=completion_reported
     ```
   - **reopen-self-heal** — (b)(6) で `completion_reported_at != null` かつ `runs.status == 'in_use'` (= T6 再指示が landed したのに flag が残存 = `WORKER_REOPENED` 取りこぼし、P2 対応): `worker-idle-state.json` の該当 record の `completion_reported_at` を `null` に **clear** し、soft-note を追記した上で **通常の (c) 評価へ進む** (skip しない = 監視再開)。これで `WORKER_REOPENED` が落ちてもレビュー修正中の silent dead-lock 監視が確実に再開する:
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=pane_output_check worker=worker-{task_id} kind=pane_output_reopen_self_heal confidence=n/a note=runs_in_use_flag_cleared
     ```
   - **acked** — (c)(ii) が `last_content_change_ts` 以降の ts を返す (= ペイン活動後に peer message を発行済): **fire しない**。soft-note を journal に追記して次サイクルへ:
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=pane_output_check worker=worker-{task_id} kind=pane_output_acked confidence=n/a note=peer_msg_after_change
     ```
   - **no-baseline** — (c)(i) が `0` (= worker が過去一度も peer-msg を発行していない、起動 ack 未発行含む): **fire しない**。本検知のスコープ外 (起動失敗等の別検知が拾うべき領域) として soft-note のみ:
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=pane_output_check worker=worker-{task_id} kind=pane_output_no_baseline confidence=n/a note=no_prior_peer_msg
     ```
   - **fire** — (b)(1)〜(5) を全部満たす ((c)(i) ≥ 1 かつ (c)(ii) NULL): silent dead-lock 候補として (e) 通知 + (f) journal 追記に進む

   #### (e) 通知 — 二段構え (worker への self-healing nudge + secretary への informational)
   relay gap (Step 5.1 (e)) と同じ「両系統発火」で safety-net を組む。どちらか一方が届かなくても他方で状況が認識される冗長性設計:

   1. **worker への self-healing nudge** (採用): dispatcher が worker pane に直接 nudge を送り、peer message 慣行を促す。承認・拒否の自動代行ではないので `.dispatcher/CLAUDE.md` 「ディスパッチャーが自動で承認・拒否することはしない」に抵触しない (構造の遵守を促すだけで内容判断はしない):
      ```
      mcp__org-broker__send_message(to_id="worker-{task_id}", message="
        PANE_OUTPUT_WITHOUT_PEER_MSG: 直近のペイン出力 (last_content_change_ts={ts}) 以降に
        secretary 宛の send_message 痕跡が観測できません。silent dead-lock を防ぐため、直前の応答内容を
        `mcp__org-broker__send_message(to_id=\"secretary\", ...)` で secretary に送信してください。
        既に送信済みの場合は無視して構いません (`last_content_change_ts` 起点での痕跡走査で 0 件のため
        自動 nudge を発行しています)。
      ")
      ```
   2. **secretary への informational** (採用): silent dead-lock の検知事実と dispatcher の自動 nudge 発行を informational として secretary に届ける。secretary は人間応答待ちのつもりで idle になっているケースが想定されるため、worker が peer message を発行し忘れているシグナルとして受け取り、対応 (待つ / `inspect_pane` で内容を直接確認 / 人間にエスカレ) を判断する:
      ```
      mcp__org-broker__send_message(to_id="secretary", message="
        PANE_OUTPUT_WITHOUT_PEER_MSG (informational): worker-{task_id} がペインに出力済みですが、
        last_content_change_ts={ts} 以降に worker→secretary の send_message 痕跡 (worker_escalation /
        worker_reported / worker_completed / plan_delivered / prep_delivered) が観測できないため、
        dispatcher が worker に nudge を送信しました。worker からの応答到着を一度待ち、来なければ
        inspect_pane で確認・問い直しを推奨します。
      ")
      ```

   両系統とも送信成功した場合のみ (f) の `notify_sent` を書く。片方失敗時は `notify_sent` を書かず、次サイクルで dedup が抜けて再試行 (at-least-once、Step 4 (e) と同じセマンティクス)。

   #### (f) de-dup と journal
   Step 4 (e) / Step 5 (e) / Step 5.1 (f) と同じスキーマを共有し、同じ `notify_sent` ledger に乗せる。(d) で fire 経路に入った場合のみ本セクションが走る (acked / no-baseline は (d) で独自 kind の soft-note を書いて終了する。`pane_output_without_peer_msg` kind の `anomaly_observed` を always 書くと acked / no-baseline でも「peer-msg 不発」観測扱いになって retro / debug の信号が汚れるため、Codex round 2 Minor 対応で fire 経路限定にする):

   - 観測記録 (fire 経路のみ):
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=pane_output_check worker=worker-{task_id} kind=pane_output_without_peer_msg confidence=n/a
     ```
   - 通知判定: 直近 30 秒以内の events に `kind == "notify_sent"` かつ `payload_json` の `(worker=worker-{task_id}, kind=pane_output_without_peer_msg)` 一致のエントリが無ければ通知に進む
   - 通知送信成功後 ((e) の (1)+(2) 双方ペイロード発行成功時のみ):
     ```bash
     bash ../tools/journal_append.sh notify_sent source=pane_output_check worker=worker-{task_id} kind=pane_output_without_peer_msg confidence=n/a
     ```
   - **再通知 cadence**: 30 秒 dedup window のみ (Step 4 / 5 / 5.1 と同じ at-least-once 担保短窓)。`/loop 3m` cadence で 30 秒 window は毎サイクル抜けるため、状態が解消されるまで毎サイクル両系統発火する。worker が nudge を受けて peer message を発行すれば次サイクルで `T_last_worker_to_secretary >= last_content_change_ts` となり (d) acked 経路に切り替わって自然停止する。worker が反応せずペインが完全に静止し続ければ idle_streak が ≥ 3 になった時点で Step 5 STALL_SUSPECTED が並行発火し相補的にカバーする (kind が異なるので de-dup で merge されない)

   #### (g) 設計メモ — Step 4 / 5 / 5.1 との関係
   - **Step 4 (画面 anomaly)** との関係: PANE_OUTPUT は (b)(3) で「APPROVAL_BLOCKED / ERROR が (e) 通知経路に乗っていない」を要求するので、画面に規定パターンの承認プロンプト / エラーが出ているケースは Step 4 が優先される。Step 4 = 規定パターンの画面 anomaly、PANE_OUTPUT = 規定外の自由出力 anomaly で disjoint な事象
   - **Step 5 (STALL)** との関係: STALL は idle_streak ≥ 3 サイクル (= ~9 分) が必要。PANE_OUTPUT は idle_streak ≥ 2 (= ~6 分) で発火するため時間的に PANE_OUTPUT が先行する。worker が nudge を無視して停止し続けたら自然に STALL 経路へ移行する (= 二段階の検知層、worker の peer message 不発 → silent dead-lock 検知 → 全停止 → stuck 検知)。kind が `pane_output_without_peer_msg` と `stall_suspected` で異なるので de-dup は独立し、両通知が並行で出ても merge されない
   - **Step 5.1 (relay gap)** との関係: 5.1 は secretary→user / secretary→worker の中継漏れ (secretary 側 outbound)。PANE_OUTPUT は worker→secretary の outbound (peer message 不発) で、観測対象が逆方向。両者は完全に独立で重なり領域なし
   - **silent dead-lock の two-sided coverage**: Step 5 = worker stuck (双方向 outbound 不在)、Step 5.1 = secretary outbound 不在、Step 5.2 = worker outbound 不在。これで worker / secretary 両方向の send_message 健全性を監視ループで覆う
   - **`PANE_OUTPUT_SETTLE_SEC = 60` の根拠**: claude code の応答生成は数秒〜数十秒で完了するのが典型 (短い ack で 5–10 秒、長文回答で 30–90 秒)。60 秒静止すれば「output が一段落した」と判定して妥当。短すぎると thinking 中の一時的 idle で誤発火 (worker が次の output を生成中の谷間)、長すぎると silent dead-lock 検知遅延が増える。`PANE_OUTPUT_IDLE_STREAK_MIN = 2` (= 約 6 分) との AND で実効 settle 時間は ~6 分以上になるため、`PANE_OUTPUT_SETTLE_SEC` は事実上 idle_streak の最低値の補強 (ts ベースの floor、サイクルロジックに glitch があっても絶対時刻として 60 秒を担保) として効く
   - **`PANE_OUTPUT_IDLE_STREAK_MIN = 2` の根拠 (Codex round 1 Major 対応)**: round 1 で「`idle_streak_cycles >= 1` (~3 分) では worker が長時間コマンドを走らせている途中の谷間出力で誤発火しうる」と指摘された。`/loop 3m` cadence では 1 サイクル = 約 3 分なので、`>= 2` を要求すると **6 分連続で画面に変化が無い** ことを担保する。worker 側で Bash / build / test 等の出力が断続的に流れる作業はこの 6 分静止条件を満たさないため、構造的に nudge 候補から外れる。逆に、長文回答を出して入力待ちに戻った worker は 6 分以上完全静止するため正しく fire する
   - **`PANE_OUTPUT_LOOKBACK_MIN` を持たない理由 (Codex round 1 Blocker 対応)**: round 1 で「15 分の固定 window で worker→secretary 痕跡を切ると、PR レビュー待ち / 人間判断待ちで pane を開けたまま正常 idle している worker (`worker_completed` が 15 分以上前) を毎サイクル誤発火する」と指摘された。検知意図は「ペイン活動後に peer-msg が無いか」であり、固定 window では表現できない。(c)(ii) で `last_content_change_ts` を WHERE 句に直接埋め込み、その ts 以降に絞り込んで MAX を取ることで「ペイン活動 後 の peer-msg 痕跡」を正確に評価する。これにより 1 時間前に `worker_completed` を出して以後 PR 待ちで idle している worker が、何らかのきっかけ (cron driven screen update など) で `last_content_change_ts` が更新されない限り fire しない
   - **`last_content_change_ts` を新フィールドにした理由**: `idle_streak_cycles` だけでは「いつから idle か」の絶対時刻が不明 (cycle 数 × cadence の近似値しか出ない)。peer message ts との大小比較は ISO-8601 時刻で deterministic に行うべき。`last_check_ts` は更新時刻を表すが change 時刻ではない (idle 継続中も毎サイクル更新される) ため流用不可
   - **`last_content_change_ts` の値が「前サイクルの `last_check_ts`」になる理由 (Codex round 3 Major 対応)**: 同サイクル内のステップ順序は Step 2 (check_messages) → Step 4 (inspect_pane) で、worker→secretary の peer-msg は Step 2 の時点で events table に `occurred_at = T_msg` として永続化される。Step 4 の inspect_pane で初めて画面変化を検知して `last_content_change_ts` を更新する場合、現サイクルの inspect 時刻 (`now`) を入れると `T_msg < now` なので (c)(ii) の `WHERE occurred_at >= last_content_change_ts` で正当な peer-msg が cutoff に弾かれ、後続サイクルで Step 5.2 が誤発火する。「前サイクルの `last_check_ts`」(= 直前に画面 idle と確認できていた時刻) を起点にすれば、現サイクル中に届いた peer-msg は必ずその cutoff 以降の `occurred_at` を持つため acked として正しく拾える。前サイクルの `last_check_ts` は最低でも 1 サイクル前 (`/loop 3m` で ~3 分以上前) なので、`PANE_OUTPUT_SETTLE_SEC = 60` の (b)(1) 条件は次サイクル以降も自然に満たされる
   - **active 継続中に `last_content_change_ts` を据え置く理由**: 出力が複数サイクルに渡って連続している間 (worker が長文回答を流している、build ログが流れている等)、active 期間の START 時刻を保持し続ける必要がある。現サイクル = 0 から再リセットすると、active 期間中に届いた worker→secretary peer-msg (例: 進捗報告) が後続の (c)(ii) cutoff より古くなり、acked と判定されなくなる。active 期間が「1 つの連続イベント」で、その START 時点より後の peer-msg が 1 件でもあれば acked、というセマンティクスを保つ
   - **新規 worker / 旧 record の初回観測**: Step 5 (b) 更新規則 (5) で `last_content_change_ts = null` 扱いとし (b)(1) を不成立にすることで、worker spawn 直後の表示変化や本 PR 以前から永続化されている record で false positive nudge を出すことを防ぐ。worker は起動時に ack 相当の peer message を最初に送る (CLAUDE.md 「ワーカー peer message を受けたら必ず ack を返す」を worker 側からも遵守する) ことが前提なので、初回 activity 観測 → ts 確定 → 次サイクル以降に peer message との比較が始まる、の流れで正しく機能する
   - **(b)(4) 事前 peer-msg 履歴要件 (Codex round 1 Major 対応の補助)**: worker が起動 ack を含めて一度も peer-msg を発行していない record は「peer-msg 慣行が確立していない異常」であり、別検知 (起動失敗 / instruction 未受領) のスコープ。silent dead-lock の典型 signature は「peer-msg 慣行のある worker が以後の活動で peer-msg を漏らした」なので、(b)(4) の existence check で対象集合を絞ることで Major で指摘された誤検知層を更に削減する。`pane_output_no_baseline` soft-note のみ追記して (d) で fire しない
   - **Issue 化なしの起点インシデント**: 2026-05-09 renga-ime-paste-routing タスクで実発生 (issue 化はされていない、本 PR が初の機械検知化)。当時 worker は窓口に「修正完了。次の指示を待ちます」相当の長文回答をペイン上に展開したが send_message 未発行で silent dead-lock 化、人間が `inspect_pane` で発見するまで停滞
   - **既存 ack 強制** との関係: ack 強制は secretary 側の責務 (= worker 起点 message 受信時に ack を返す、CLAUDE.md 「ワーカー peer message を受けたら必ず ack を返す」)。Step 5.2 は dispatcher 側の機械観測で worker の outbound 不発を補完する (= ack 強制の対偶側面)。両者は補完関係で、人間運用契約 + 機械観測の二重化により silent dead-lock の発生確率を抑える
   - **`completion_reported_at` gate の動機**: worker が secretary へ完了報告を送り、secretary が ack (worker pane へ send_message) を返した後、その ack echo や worker の「レビュー待ちます」出力で `last_content_change_ts` が更新される。以後 worker は review 待ちで追加 peer-msg を出さないため、(c)(ii) が NULL を返し PANE_OUTPUT_WITHOUT_PEER_MSG が **誤発火**していた (完了報告済み worker の正常 idle を silent dead-lock と誤判定、実運用で 4 回再現)。`WORKER_COMPLETION_NOTED` 受領で `completion_reported_at` を立て、(b)(6) gate で本検知を skip することでこの false positive を断つ
   - **skip は `pane_output_without_peer_msg` に限定する (Codex Major 対応)**: `completion_reported_at != null` で抑止するのは Step 5.2 の PANE_OUTPUT のみ。**Step 1 の pane exit 検知 / Step 4 の APPROVAL_BLOCKED・ERROR / Step 5 の STALL_SUSPECTED は完了報告後も有効なまま**残す。完了後 pane に出るエラー banner や承認プロンプトは依然として本物の異常であり、監視全体を completion で止めると見落とすため。`completion_reported=true` を「監視まるごと停止」に拡張してはならない
   - **timeout を持たず lifecycle event で解除する理由 (Codex Major 対応)**: `completion_reported_at` は「完了報告済み・レビュー待ち」の状態印。時間で自然失効させると、レビューが長い正常ケース (数時間の PR レビュー等) で失効後に再び false positive に戻る。解除は lifecycle event のみに固定 — `CLOSE_PANE` / pane 消失で record 削除 (更新規則 (4))、T6 再指示で `WORKER_REOPENED` により `null` clear (Step 2)、新しい完了受領で再セット。この 3 event 以外では消えない
   - **何が検出不能になるか / 許容根拠 (3 問への回答)**: 完了報告後・レビュー待ち中の worker が pane に追加出力しても PANE_OUTPUT は検出しない。state semantics 上 `review` は「worker 側の作業 in-flight なし」なので、この idle は正常であり許容できる。**ただし T6 再指示 (awaiting_review→in_progress) 後まで skip が残ると、レビュー修正中の本物の silent dead-lock を見逃す**。これを防ぐのが T6 の解除契約 (Blocker) で、再指示は secretary→worker 直送で dispatcher が経路上に居ないため、`WORKER_REOPENED` (fast-path) と `runs.status == 'in_use'` backstop (reliable、上記 P2 対応) の 2 経路で監視を再開させる。この解除が無ければ本 gate 導入は net で監視穴を作るため、解除契約と一体で成立する
   - **`WORKER_REOPENED` の best-effort 非対称性と `runs.status` backstop (P2 対応)**: `WORKER_COMPLETION_NOTED` (skip を **立てる**) の取りこぼしは監視が過剰に働く安全側に倒れるが、`WORKER_REOPENED` (skip を **解く**) の取りこぼしは監視が止まったまま = 危険側に倒れる (レビュー修正中の本物の silent dead-lock を見逃す)。両者を同じ best-effort one-shot semantics で扱うと解除が信頼できない。そこで解除は 2 経路にする: (1) `WORKER_REOPENED` = fast-path (速報)、(2) DB `runs.status == 'in_use'` = reliable backstop。T6 再指示で secretary は StateWriter 経由で `runs.status` を `review → in_use` に**決定的に**書く (peer message に依らない DB 遷移) ので、(1) が落ちても (b)(6) gate が (2) を観測して flag を self-heal clear し監視を再開する ((d) reopen-self-heal)。これにより「skip を立てる側は best-effort で十分・skip を解く側は決定的 backstop で担保」の非対称設計になり、Blocker の懸念 (解除漏れで監視穴) を構造的に塞ぐ。`runs.status` は既存 schema (`runs` テーブル、`status ∈ {in_use, review, completed}`) をそのまま consume し新規 event を導入しない
   - **`WORKER_COMPLETION_NOTED` / `WORKER_REOPENED` は完了判定ではなく監視抑止用の受領通知 (Codex Minor 対応)**: 現行契約では worker 完了報告は secretary 宛が正で、dispatcher は自分で完了を判定しない (`docs/contracts/role-contract.md` dispatcher inputs、`delegation-lifecycle-contract.md` T4)。本 peer message はその原則を変えず、dispatcher の監視 loop の false positive を抑止するためだけの受領通知として additive に追加する (contract への追記も additive に留め、既存 ratified 記述は書き換えない)
   - **secretary は non-blocking で送る (Codex Major 対応)**: secretary は worker ack と状態更新 (REVIEW 遷移) を終えた後、best-effort で `WORKER_COMPLETION_NOTED` を送るだけで dispatcher 応答を待たない。dispatcher は `/loop 3m` の通常 `check_messages` で非同期に反映する。blocking wait にすると T4 の human review 移行に新しい停止点を作るため禁止 (secretary 側手順は `.claude/skills/org-delegate/SKILL.md` Step 5 §2a、再指示は `.claude/skills/org-pull-request/SKILL.md` 2c)
