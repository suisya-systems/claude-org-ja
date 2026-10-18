# relay gap の通知と de-dup (Step 5.1 の (e) / (f)) — 詳細

[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) から分けた本体仕様。Step 5.1 で relay gap の疑いが成立し通知するサイクルだけ読む（それ以外のサイクルでは読まない）。

   #### (e) 通知 — out-of-band で user に直接届ける
   relay gap の本人 (secretary) を経由した通知は **意味がない** (中継停止が原因の事象を、その中継経路で通知することになる)。代替:

   1. **dispatcher pane に "ALERT" として表示する** (採用): user は dispatcher pane を直接見ているか、最低でも端末上で確認可能。dispatcher pane は LLM session なので画面に直接書く API は無いが、dispatcher Claude が応答テキストとして "ALERT: SECRETARY_RELAY_GAP_SUSPECTED ..." を return すれば pane の最終行に表示される (= user の見えるところに残る)
   2. **secretary には並行で "user に直接通知してください" メッセージを送る** (採用): secretary が機能していれば中継、機能していなければ (1) で user 自身が dispatcher pane を見て認識する。fail-safe として両方の経路を保つ
      ```
      mcp__org-broker__send_message(to_id="secretary", message="
        SECRETARY_RELAY_GAP_SUSPECTED (請求 user 直接通知): worker-{task_id} が
        過去 15 分判断仰ぎ / 進捗を上げたが secretary 側 outbound 痕跡なし。
        user に直接お知らせください。中継が機能していればこのメッセージは
        通常 relay されるが、機能していない場合は dispatcher pane 末尾にも
        並行で alert 表示している。
      ")
      ```

   (1) と (2) の **両方** を必ず発行する。どちらか一方が届かなくても他方で user が認識できる、という冗長性設計。

   #### (f) de-dup と journal
   Step 4 (e) / Step 5 (e) と同じスキーマを共有し、同じ `notify_sent` ledger に乗せる:

   - 観測記録 (常時):
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=relay_gap_check worker=worker-{task_id} kind=relay_gap_suspected confidence=n/a
     ```
   - 通知判定: 直近 30 秒以内の journal に `event == "notify_sent"` かつ `(worker=worker-{task_id}, kind=relay_gap_suspected)` 一致のエントリがなければ通知に進む
   - 通知送信成功後 (即ち (e) の (1)+(2) 双方ペイロードを発行した後):
     ```bash
     bash ../tools/journal_append.sh notify_sent source=relay_gap_check worker=worker-{task_id} kind=relay_gap_suspected confidence=n/a
     ```
   - **再通知 cadence**: dedup window は 30 秒のみ (Step 4 / Step 5 と同じ at-least-once 担保のための短窓)。`/loop 3m` cadence では 30 秒は毎サイクル抜けるため、relay gap が解消するまで **3 分ごとに 1 回 user に再通知が届く**。relay gap は user の視認漏れが致命的な事象なので、stuck 通知のような長窓 suppress は採用しない。状態が変わった (= secretary 側 outbound が現れた、または新規 `T_last_worker_in` で起点更新により候補から外れた) 時点で次サイクルの観測時に (b) を不成立にして自然停止する
