# ワーカーペイン監視 — 詳細

`.dispatcher/CLAUDE.md` の「ワーカーペイン監視」エントリポイントから参照される本体仕様。監視ループ 1 サイクルの手順、stall 検出、secretary relay gap 検出、設計メモ、cadence 設定の根拠を含む。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `broker` / opt-in `renga`）**: 本ファイルの監視手順は **既定 `broker`** で書いてある（`ORG_TRANSPORT` 無設定ならそのまま従う。既定挙動）。`ORG_TRANSPORT=renga`（opt-in）では完全修飾名が **`mcp__org-broker__*` → `mcp__renga-peers__*`** に置換される（引数形・cursor / timeout セマンティクスは同一）。監視ループで輸送依存に**意味が変わる**のは次の 2 点:
> 1. **受信モデル（push 一次 = `claude/channel` / pull フォールバック）**: Step 2 の `check_messages` でワーカー自己報告を受ける流れはどちらの transport でも**同じツール名・同じ drain セマンティクス**で動く。既定 broker は **push 一次**に設計済（runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9）で、各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が `notifications/claude/channel` で本文を idle セッションへ注入する（ワーカー報告は channel push が一次）。**pull はフォールバック層**: sidecar 不在 / unhealthy / channel 非対応ペイン時は、ディスパッチャーが `/loop 3m` 各サイクルで能動的に `check_messages` する（§9.6 読み替え表の dispatcher cadence。ナッジが出れば契機になりうるが idle を起こさないため能動 poll が受信の正路。既存「ナッジを見たら `check_messages`」prose は撤回せずこの fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）ではワーカー報告が `<channel source="renga-peers" …>` として in-band で push される。**ただし `/loop 3m` 監視ループ自体は push 一次でも廃止しない** — Step 1 の `poll_events`（pane lifecycle）に依存し、かつ Step 2 の `check_messages` は push 失効時のフォールバック drain として機能するため。Step 5.2 のワーカーへの self-healing nudge / secretary への informational も既定 broker では channel push 一次・nudge + `check_messages` フォールバックの経路になる。
> 2. **`poll_events` の生成元（native push → 差分 reconcile 合成）**: 既定 broker は端末 backend（tmux/WezTerm）に native push が無いため、**`list_panes` 差分 reconcile で `pane_started` / `pane_exited` / `events_dropped` を合成**する（設計 §6、exactly-once `pane_exited` / overflow 時 `events_dropped` + `list_panes` リコンサイル回復）。`ORG_TRANSPORT=renga`（opt-in）ではペイン lifecycle を native に push する。`poll_events` の API 形・最小 vocabulary・cursor / 30s cap は同一なので Step 1 / Step 3 の手順は不変。`events_dropped` 時に `list_panes` で突き合わせる Step 3 の保険はどちらも同じ（既定 broker の正準経路）。
>
> エラーコードは shared codes（`[pane_not_found]` / `[shutting_down]` / `[io_error]` / `[app_timeout]` / `[internal]` 等、Step 4 のハンドリング参照）に加え、既定 broker は `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`(= adapter_unavailable) / `[nudge_failed]` / `[peer_not_found]` を返しうる（未知コードは default-branch）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次再設計）/ `docs/design/ja-migration-plan.md` §5.2(ii) / §6 / §8。broker 実走（dogfood）は Issue G スコープで本ファイルの既定経路ではない。

### 監視ループ 1 サイクル (3 分おき)

> **時刻はすべて UTC（決定的コマンド由来、JST-as-Z 厳禁）**: 本ファイルの `now` と、dispatcher
> 状態ファイルに書く全タイムスタンプ（`worker-idle-state.json` の `last_check_ts` /
> `last_content_change_ts`、`curate-inflight.json` の `started_at` / `last_inspect_ts`）は
> **必ず UTC** で、`date -u +%Y-%m-%dT%H:%M:%SZ`（PowerShell 環境は
> `(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")`）の出力をそのまま用いる。
> local 時刻（JST 等）を手書きして末尾に `Z` を付けてはならない。これらの値は journal /
> events table の `occurred_at`（core-harness が ISO-8601 UTC で記帳）や `started_at` と
> `now - <ts>` で経過比較されるため、1 つでも local-as-Z が混入すると経過が負値 / 過大になり、
> Step 5.2 の peer-msg 痕跡判定（`occurred_at >= last_content_change_ts`）や Step 5.3 の
> curate timeout（`now - started_at`）が破綻する（JST を `Z` で書くと約 9 時間先の未来時刻になり、
> 自動クローズが永久不発 → curator ペイン孤立）。`started_at` の決定的取得は
> [`.dispatcher/references/pane-close.md` 5-3](pane-close.md) を一次参照。

各サイクルで以下を順次実行する:

1. **`mcp__org-broker__poll_events` で直近のペイン lifecycle を drain** (タイムアウト付きで 1 回だけ):
   ```
   result = mcp__org-broker__poll_events(
       since=<前サイクルの next_since、初回は省略>,
       timeout_ms=5000,
       types=["pane_exited", "events_dropped"]
   )
   # cursor は .state/dispatcher-event-cursor.txt に保存して次サイクルで使う
   write_file(".state/dispatcher-event-cursor.txt", result.next_since)
   ```
   - 初回 (cursor ファイルが無い/空) は `since` 省略で「今以降」セマンティクス（過去イベントを flood しない）
   - 2 サイクル目以降は前回の `next_since` を使って idempotent resume（重複通知なし）
   - `types=["pane_exited", "events_dropped"]` フィルタで heartbeat / pane_started 等を除外。cursor は filter と無関係に advance するので重複 scan なし
   - `result.events[]` を順に処理:
     - `type == "pane_exited"` かつ `role == "worker"` → 窓口に `WORKER_PANE_EXITED` 通知
     - `type == "events_dropped"` → `.state/journal.jsonl` に drop 件数を記録（監視が追いついていないシグナル）
     - それ以外（ディスパッチャー/キュレーター/窓口の終了） → 誤ってワーカー終了として扱わない
   - **filter 不一致イベント到着で long-poll 早期終了する仕様**なので、空応答時は次サイクルで再 poll（cursor 保持で重複なし）
   - 絞り込んだ `pane_exited` 行の `name` (例: `worker-foo`) を拾い、`mcp__org-broker__send_message` で窓口に **ペインが閉じた** という事実だけを通知する:
     ```
     WORKER_PANE_EXITED: {name} (id={id}) のペインが閉じました。リコンサイル要。
     ```
     **重要**: これは「ペインが消えた」というライフサイクル事実のみ。タスクの完了判定ではない。
     窓口側は `.state/workers/worker-*.md` を `status=pane_closed` に遷移させ、タスクの完了/未完了は:
       - 直近の org-broker メッセージ履歴 (進捗ログ) を確認
       - `COMPLETED` 報告が届いていれば task 完了扱い
       - 届いていなければ、未完了終了 (ワーカー事故) として扱い、再派遣 or 放棄をユーザーに確認
     のプロセスで判定する
   - `type == "pane_started"` は現状 use case なしなので無視して良い (将来必要になれば追加)
   - `type == "events_dropped"` は drop 件数を `.state/journal.jsonl` に記録 (監視が追いついていないシグナル)
   - `type == "heartbeat"` は 30 秒おきの keep-alive。既存 jq フィルタで暗黙に skip されるので何もしなくてよい
   - 5 秒以内に 1 件も来なければ次の Step へ進む (Phase 2.1 の `--timeout` で勝手に exit する)

2. **`mcp__org-broker__check_messages` でワーカーからの自己報告を受信**:
   - 受信種別ごとに Step 4 (e) と同じシーケンスを適用してから窓口へ転送する:
     1. 観測記録: `anomaly_observed` を journal に追記 (`source=self_report`、confidence は `n/a`。worker が自発的に報告したので cursor 補強不要)
     2. 通知判定: 直近 30 秒以内の journal に `event=notify_sent` かつ `(worker, kind)` 一致のエントリがあればスキップ (Step 4 の inspect 通知と合算で de-dup)
     3. 通知送信
     4. `notify_sent` を journal に追記 (`source=self_report`, `confidence=n/a`)
   - `APPROVAL_BLOCKED` → 窓口に転送
     ```
     APPROVAL_BLOCKED: {task_id} のワーカー (ペイン名 worker-{task_id}) が承認待ちで停止しています。 (source=self_report, confidence=n/a)
     ```
   - `ERROR` / 停止メッセージ → 窓口に転送
     ```
     ERROR_DETECTED: {task_id} のワーカー (ペイン名 worker-{task_id}) がエラーまたは停止しています。 (source=self_report, confidence=n/a)
     ```
   - 通常進捗は `.state/workers/worker-*.md` に追記のみ (journal / de-dup スキーマには乗せない)
   - **secretary→dispatcher 監視制御メッセージ (Issue #658)** — `WORKER_COMPLETION_NOTED` / `WORKER_REOPENED` を受信したら anomaly ではなく **lifecycle-control** として扱い、`.state/dispatcher/worker-idle-state.json` の該当 worker record の `completion_reported_at` を更新する (journal notify / de-dup スキーマには乗せない。**anomaly ledger でもない**):
     - `WORKER_COMPLETION_NOTED: worker-{task_id} (task_id={task_id}, received_at={ISO-8601 UTC})` → 該当 record の `completion_reported_at` を本文の `received_at` に **set** する (record 未存在なら key を作って set)。worker が完了報告済みで review 待ち idle に入る合図で、Step 5.2 の PANE_OUTPUT_WITHOUT_PEER_MSG false positive (完了報告済み worker の正常な review 待ち idle を silent dead-lock と誤判定) を抑止する。**これは「完了判定」ではなく「監視抑止用の受領通知」** — dispatcher は依然として自分でタスク完了を判定しない (T4 の完了遷移は secretary の責務、`docs/contracts/delegation-lifecycle-contract.md` T4)
     - `WORKER_REOPENED: worker-{task_id} (task_id={task_id}, reopened_at={ISO-8601 UTC})` → 該当 record の `completion_reported_at` を **`null` に clear** する (**fast-path 解除**)。T6 (awaiting_review→in_progress、レビュー修正の追指示) で secretary が worker へ**直送**で再指示するとき送られる。再指示は secretary→worker 直送で dispatcher が経路上に居ないため、この明示 clear が解除の速報になる
     - **解除は best-effort な `WORKER_REOPENED` だけに依存しない (P2 対応、非対称性の解消)**: `WORKER_COMPLETION_NOTED` の取りこぼしは「監視が過剰に働く」安全側に倒れるが、`WORKER_REOPENED` の取りこぼしは「監視が止まったまま」= **危険側**に倒れる (レビュー修正中の本物の silent dead-lock を見逃す)。両者を同じ best-effort semantics で扱ってはならない。そこで **決定的 backstop** として、Step 5.2 (b)(6) の skip gate は `completion_reported_at != null` に加えて DB の `runs.status == 'review'` を要求する。T6 再指示で secretary は StateWriter 経由で `runs.status` を `review → in_use` に**確定的に**書く (`.claude/skills/org-pull-request/SKILL.md` 2c、peer message に依らない DB 遷移) ので、`WORKER_REOPENED` が落ちても `runs.status == 'in_use'` を観測した dispatcher が flag を self-heal clear して監視を再開できる (下記 (b)(6) / (d))。`WORKER_REOPENED` は fast-path、`runs.status` は reliable backstop の二段構え
     - `WORKER_COMPLETION_NOTED` は secretary が **best-effort・非 blocking** で送る (secretary は dispatcher ack を待たない)。push 一次では channel 注入、フォールバック時は本 Step 2 の能動 `check_messages` で受ける。取りこぼしても次の完了受領で再送・再セットされる (record が無ければ Step 5.2 (b)(6) は `completion_reported_at` を欠損 = `null` 扱いで評価するため、取りこぼしは「監視が過剰に働く」安全側に倒れる)

3. **`mcp__org-broker__list_panes` でペイン一覧を取得して突き合わせ**:
   - `poll_events` (Step 1) を見逃した場合の保険 (`events_dropped` 発生時や events 未受信で pane 状態がズレた時)
   - `list_panes` の結果テキストには各 pane の `id / name / role / focused / x / y / width / height` が含まれる
   - events 経由で exit を把握していないのに `list_panes` で pane が消えているワーカーがあれば、**ペインが閉じた事実**として `.state/workers/worker-*.md` の status を `pane_closed` に遷移させ、Step 1 と同じく窓口に `WORKER_PANE_EXITED` を転送 (task 完了判定は同じ手順で窓口側が実施)
   - pane 上限は 16 なので結果は常に小さく、都度 full scan で問題なし

4. **`mcp__org-broker__inspect_pane` でワーカーペインの画面内容を走査し異常検出**:
   - **目的**: ワーカー自己申告に依存せず、ディスパッチャー自身が画面内容から APPROVAL_BLOCKED / ERROR を検出する独立した観測チャネル
   - **実行**: Step 3 で得た `list_panes` の active worker (`role == "worker"`) それぞれに対し:
     ```
     result = mcp__org-broker__inspect_pane(
         target="worker-{task_id}",
         lines=<該当 worker pane の height>,   # Step 3 list_panes の height。取れなければ十分大きい固定値 (例 200)
         include_cursor=true,
         format="grid"
     )
     # result.structuredContent に {lines: [{row, text}], cursor: {visible, row, col}} が入る
     ```
     を順次実行 (16 ワーカー並列でも合計 1 秒未満)。`lines` は **Step 3 の `list_panes` で得た該当 worker pane の `height`** を渡し、pane の全 visible 行を取得して (d) の ERROR scan を全行対象にする (Issue #492 gap 1: `lines=10` の bottom-10 窓では row 15 のような scroll-up した error banner を取りこぼす。`inspect_pane` の `lines` は「末尾 N 行への trim」なので、固定値だと pane height がそれを超えた環境で上段を取りこぼす — 必ず実 height を使う)。`list_panes` の height が取れない場合のみ十分大きい固定値 (例 200) でフォールバックする。APPROVAL_BLOCKED の target line は (a) の通り「最後の非空行」なので返却行数を増やしても変わらない。
   - **エラー時の挙動**: tool result テキストに `[<code>] <msg>` 形式でエラーが埋まる。code で分岐する (詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`):
     - `[pane_not_found]` / `[pane_vanished]` — ワーカーが既に閉じた。そのワーカーの inspect を skip して Step 3 の list 結果で `WORKER_PANE_EXITED` 経路に回す (二重検出は de-dup で吸収される)
     - `[shutting_down]` — transport backend 停止中。監視ループを即停止し、`mcp__org-broker__send_message` で `FOREMAN_STOPPING` を窓口に通知
     - `[io_error]` / `[app_timeout]` / `[internal]` — 一過性の可能性。`.state/journal.jsonl` に記録して次サイクルで再試行
     - 未知 code (将来の transport backend が追加) — journal 記録のみで続行

   #### (a) マッチ対象の定義
   返却された `lines` 配列 (各要素 `{row, text}`) の中で、**`text != ""` を満たす最後の 1 要素** だけを APPROVAL_BLOCKED パターンの match 対象とする (複数行を対象にしない)。
   この 1 行を以降 **target line** と呼ぶ。ERROR / spinner-age パターン ((d)) は **全 visible 行** が対象 (プロンプト位置と無関係で、scroll-up した banner も拾うため。Issue #492 gap 1)。`inspect_pane(lines=<pane height>)` で取得した **返却行配列全体** を scan する。

   #### (b) APPROVAL_BLOCKED 検出 — target line の anchored regex 完全一致
   以下のいずれか:
   - `^Allow this tool use\? \(y/n\)$`
   - `^Do you want to proceed\? \(y/n\)$`
   - `^Do you want to make this edit to .+\?$`
   - `^❯\s*1\.\s*Yes\s*$`
   - `^Press .+ to continue`
   - `^\s*Esc to cancel` (cursor 非表示時に leading space が付く variant ` Esc to cancel` も match させる。`\s*` で tab / NBSP 等の将来 variant にも対応)

   **新しいプロンプト形が観測されたら、この regex リストに追記**。Claude Code の version 更新で形が変わる可能性があるため、網羅は前提にしない。

   #### (c) cursor 補強による confidence 分岐
   regex に一致した target line について:
   - **high-confidence**: `cursor.visible == true` かつ `cursor.row == target_line.row` または `cursor.row == target_line.row + 1`
   - **low-confidence**: 上記以外 (cursor が離れた位置にある、または非表示)

   **high-confidence のみ journal 記録 + `mcp__org-broker__send_message` 通知の両方を発行**。low-confidence は journal のみに記録し、窓口通知はスキップする (誤検出による窓口への偽通知を抑えるため)。

   #### (d) ERROR 検出 — 全 visible 行 substring / regex / spinner-age
   **全 visible 行** ((a) で説明した `inspect_pane(lines=<pane height>)` の **返却行配列全体**。bottom 10 ではない — Issue #492 gap 1) のいずれかが以下に該当:

   - **strong substring (大文字小文字無視、無条件で発火)**: `API Error`, `api error`, `rate limit`
   - **status code (語境界 + エラー文脈ゲート)**: `429`, `500`, `502`, `503`, `504`, `529` のいずれかが **語境界トークン** (`(?<!#)\b...\b`) として現れ、**かつ同一行に error 文脈キーワード** (`error` / `overload` / `unavailable` / `rate limit` / `too many requests` / `retry`(ing) / `gateway` / `server error` / `throttl`) がある場合のみ発火
     - `529` は Anthropic overload、`502/503/504` は transient gateway 系 (Issue #492 gap 2)。全行 scan に広げたことで bare 数字 substring の誤検出 (`localhost:5000` / `500 passed` / issue ref `#529` 等) が増えるため、語境界 + 文脈ゲート + `#` 接頭の issue ref 除外 (`(?<!#)`) で絞る (Codex review 対応)。主信号は `API Error` substring と spinner-age で、status code は文言変更への futureproof な補足
   - **anchored regex (大文字小文字区別)**: `^Error: `, `^ERROR: `
   - **spinner-age (Issue #492 gap 3、旧形式のみ)**: `^\s*[spinner glyphs]+\s+\w+\s+for\s+(\d+)m\s+(\d+)s` に該当し、かつ捕捉した分が **threshold (default 5 分) 以上**。Claude Code の **旧形式** `{glyph} {動詞} for {Xm Ys}` スピナーが 5 分以上回り続けるのは API retry loop / hang の signal で、substring とは独立に **ERROR 同等** として扱う (観測 case: `✻ Sautéed for 9m 12s`)。**新形式 active spinner (`{glyph} {Verb}… (Xm Ys · ...)` / `(Xh Xm Xs · ...)`) はここでは ERROR にしない** — 新形式は deep-research / ultracode の正常な長考でも数十分回るため、Step 5 (b-3) の active-spinner suppress (`tools/inspect_pane_state.py`、cap 90 分) 側で扱う。新旧 regex は ` for ` と `… (` で disjoint なので本 ERROR 経路は新形式を拾わない (Issue #671 Major: spinner 2 分類)

   ERROR / spinner-age は cursor 補強なしで journal + 通知の両方を発行する (error banner / 停止スピナーは cursor 位置と相関しないため)。spinner-age 検出も notify フォーマット上は `ERROR_DETECTED` 経路に乗せる (kind=error)。

   **正準実装**: 上記 substring / regex / spinner-age 判定の決定論的コアは `tools/inspect_anomaly_scan.py` (`scan_lines()`) に codify 済み。ディスパッチャーは inspect_pane 結果を JSON で渡してこの helper を呼ぶことで全行 scan を 1 コマンドで実行できる (cwd は `.dispatcher/` なので `../tools/`):
   ```bash
   # inspect_pane の structuredContent を JSON 化して渡す。
   # exit 3 = anomaly 検出、exit 0 = clean。detections[] に {kind, reason, row, matched}。
   echo "$inspect_json" | py -3 ../tools/inspect_anomaly_scan.py --spinner-threshold-min 5
   ```
   threshold やパターンの単一定義はこの module 側にあり、regression test (`tests/test_inspect_anomaly_scan.py`、観測 case = row 15 の 529 banner + 9m spinner + bottom 10 空) が契約を pin する。手で判定する場合も上記リストと同義。

   #### (e) 実行シーケンス (journal + de-dup + notify)
   以下の順番で厳密に実行する:

   1. **観測記録** (confidence に関わらず常に): `tools/journal_append.py` 経由で追記（`cursor` ネスト object のため Python wrapper を使う。`ts` は helper が自動付与）:
      ```bash
      py -3 ../tools/journal_append.py anomaly_observed \
          source=inspect worker=worker-{task_id} \
          kind=approval_blocked confidence=high matched='<該当行>' \
          --json '{"cursor": {"row": <r>, "col": <c>, "visible": <bool>}}'
      ```
   2. **通知するかの判定** — 以下を **すべて** 満たす場合のみ通知に進む:
      - APPROVAL_BLOCKED なら confidence == high (low-confidence は journal のみで終了)
      - ERROR は常に通知対象 (cursor 補強なし)
      - **de-dup チェック**: 直近 30 秒以内の journal に **`event == "notify_sent"`** かつ `(worker, kind)` 一致のエントリが存在しない
        - `anomaly_observed` エントリは de-dup キーに **含めない** (低 confidence や observation-only record が将来の通知を抑制しないため)
        - 今サイクルの step (1) で書いた `anomaly_observed` も de-dup 対象にならない
   3. **通知送信** (step 2 を通過した場合): `mcp__org-broker__send_message` で窓口に通知 (フォーマットは (f) 参照)
   4. **notify_sent 記録** (通知送信成功時): `confidence` は kind と source に一致させる (APPROVAL_BLOCKED かつ source=inspect のみ `"high"`、それ以外は `"n/a"`)。helper 経由で append:
      ```bash
      # APPROVAL_BLOCKED + source=inspect
      bash ../tools/journal_append.sh notify_sent source=inspect worker=worker-{task_id} kind=approval_blocked confidence=high
      # ERROR + source=inspect
      bash ../tools/journal_append.sh notify_sent source=inspect worker=worker-{task_id} kind=error confidence=n/a
      # APPROVAL_BLOCKED / ERROR + source=self_report (Step 2 から発行)
      bash ../tools/journal_append.sh notify_sent source=self_report worker=worker-{task_id} kind=approval_blocked confidence=n/a
      ```
   通知失敗時は `notify_sent` を書かない。次サイクルで再検出されれば de-dup が抜けて再通知が試行される (at-least-once)。
   Journal 書き込み自体が失敗した場合はそのサイクルの通知を断念、次サイクルで再試行。

   #### (f) 通知フォーマット
   (e) の step 3 に到達した場合のみ、`mcp__org-broker__send_message` で窓口に通知。既存 `APPROVAL_BLOCKED` / `ERROR_DETECTED` フォーマットに `source=inspect` + `confidence=<high|n/a>` を付与:
   ```
   APPROVAL_BLOCKED: worker-{task_id} の承認プロンプトを検出 (source=inspect, confidence=high): {該当行}
   ERROR_DETECTED: worker-{task_id} にエラーを検出 (source=inspect, confidence=n/a): {該当行}
   ```
   ERROR は cursor 補強を使わないため confidence は便宜上 `n/a`。

   #### (g) worker 自己申告 (Step 2) と inspect (Step 4) の併用設計
   両チャネルが同じ anomaly を通知しても de-dup ((e) の step 2) が 30 秒窓で合算するので、窓口は重複通知を受け取らない。self-report は先に届けば inspect を抑制、inspect は worker が通知を忘れていれば self-report を補完する。両方独立稼働で OK。

   #### (h) 設計メモ — secretary 側 inspect cadence は別 Issue (Issue #492 gap 4)
   Issue #492 gap 4「secretary 自身が active inspect cadence を持つべきか」は **本 PR スコープ外として別 Issue に切り出す判断**。理由: dispatcher の Step 4 を全 visible 行 scan + spinner-age に強化する (本 PR の gap 1–3) 方が変更が小さく、観測 case (529 + 9m spin) は dispatcher 側の検出強化だけで 5 分時点の ERROR 通知に乗る。secretary に二重の inspect ループを足すのは監視層の二重化で、まず dispatcher 強化の効果を観測してから要否を判断するのが妥当。secretary cadence / `secretary-monitor` skill が必要と判明したら別 Issue で扱う。

5. **stall 検出 (STALL_SUSPECTED)** — 「stuck」と「Secretary 判断待ち idle」を補助シグナルで区別する独立チャネル:

   **定数**: `STALL_SECRETARY_LOOKBACK_MIN = 15` (補助シグナル look-back window、分単位)。値変更が必要な場合は本ファイルのこの行を直接書き換える (env 化は将来課題)。

   **定数 (PR-pending-merge 用)**: `STALL_PR_MERGE_LOOKBACK_MIN = 60` (PR が open 済みかつ未 merge の worker に対する補助シグナル look-back window、分単位)。Issue #304。`pr_opened` を出してから user が merge 承認するまで 15–60 分かかるのが典型で、その間 worker は完了済みで idle のまま正しく待機する。15 分の通常 lookback では `worker_completed` が window から外れて誤発火 (session #12 で実測)。値変更が必要な場合は本ファイルのこの行を直接書き換える。

   **定数 (active-spinner suppress 用、Issue #671)**: `SPINNER_ACTIVE_SUPPRESS_CAP_MIN = 90` (新形式 active spinner が回り続けている間 STALL_SUSPECTED / PANE_OUTPUT を抑止する上限、分単位)。正準値は helper `tools/inspect_pane_state.py: SPINNER_ACTIVE_SUPPRESS_CAP_MIN` にあり、prose は helper の判定 (`suppress_stall` / `cap_exceeded`) を消費するだけ。elapsed が伸び続ける限り抑止するのを**無条件にはしない**: cap 到達で抑止を解除し anomaly 経路へ戻す (API dead で client 側タイマーだけ伸び続けるケースの永久マスクを防ぐ、Blocker 1)。観測済み ~61 分級の正常 turn を許容しつつ、90 分で頭打ちにする。値変更は helper 側の定数を書き換える (prose は追従不要)。

   #### (a) 動機
   Step 4 の inspect_pane 単独では、ワーカーが「Secretary に判断仰ぎを送って人間応答を待っている」状態と「stuck (異常停止)」を区別できない。判断仰ぎ中は worker pane の Claude session は継続中で画面 idle、APPROVAL_BLOCKED の regex にも該当しない。誤って STALL_SUSPECTED を発火すると、判断待ちワーカーに対してサイクル毎に窓口 escalation を投げ続ける。Issue #287 で実インシデント発覚 (session #12 / `worker-issue-283-delegate-payload`)。
   
   #### (b) いつ stall を疑うか
   Step 4 の inspect_pane で worker pane が APPROVAL_BLOCKED / ERROR どちらの regex にも該当せず、かつ **正規化済み全可視行のコンテンツハッシュが前サイクルから変化していない** 状態が **連続 3 サイクル以上** (= 9 分相当、`/loop 3m` cadence 前提) 続いた worker を **stall 候補** とする。サイクル数は本ファイルでこの 3 を目安として扱う。

   **screen-change 判定は content hash で行う (Issue #680)**: 旧実装は `(target_line_text, 最後の非空行)` + cursor 位置の単点比較で idle/active を判定していたが、Claude Code の TUI はツール実行中も末尾 footer と cursor を静的に保つため、scrollback (Read/Edit/Bash 出力・thinking spinner) が動いていても単点比較では不変と観測され `idle_streak` が機械的に加算されて STALL を誤発火した (`worker-runtime-129-observed-session-binding` 等で 1 セッション 2 件連続、`knowledge/raw/2026-07-04-delegation-stall-detection-static-footer-false-positive.md`)。**全可視行を正規化してハッシュ化**し、実 scrollback の変化だけを active とみなす。ハッシュの算出・spinner 正規化・active-spinner 判定は helper `tools/inspect_pane_state.py` に codify 済みで、prose は helper 出力を state に反映するだけ (prose 手製ハッシュは決定性が弱く禁止、Major 対応)。

   **helper 呼び出し (record モード)** — Step 4 の inspect_pane 結果と worker-idle-state.json の該当 record 全体を渡すと、helper が観測 (`observation`) と**次の record** (`record`) と遷移種別 (`decision`) を返す。dispatcher は返ってきた `record` を worker-idle-state.json に**そのまま書く** (hash / streak / last_content_change_ts を手計算しない)。dispatcher cwd は `.dispatcher/` なので `../tools/`:
   ```bash
   # $inspect_json = inspect_pane の structuredContent を JSON 化したもの
   # $prev_record  = worker-idle-state.json の worker-{task_id} record (無ければ 'null')
   # $now          = 本サイクルの inspect 時刻 (date -u +%Y-%m-%dT%H:%M:%SZ)
   # --anomaly-fired は Step 4 (e) で APPROVAL_BLOCKED / ERROR が本サイクル通知に進んだ場合のみ付ける
   echo "$inspect_json" | py -3 ../tools/inspect_pane_state.py \
       --prev-record "$prev_record" --now-ts "$now"
   # 出力 JSON: {
   #   observation: {content_hash, normalized_lines, spinner_present, spinner_signature,
   #                 spinner_elapsed_sec, spinner_elapsed_increased, cap_exceeded, suppress_stall},
   #   record: {last_visible_content_hash, idle_streak_cycles, last_content_change_ts,
   #            last_check_ts, last_spinner_signature, last_spinner_elapsed_sec,
   #            last_spinner_seen_ts, completion_reported_at, …(既存 key は保全)},
   #   decision: {transition, suppress_stall, cap_exceeded}
   # }
   # exit 4 = suppress_stall (active-spinner 抑止すべき) / exit 0 = 抑止不要
   ```
   前サイクルの spinner signature / elapsed は `--prev-record` の中の `last_spinner_signature` / `last_spinner_elapsed_sec` から helper が読む (null 安全 — 前サイクルに spinner が無くても crash しない)。`decision.suppress_stall == true` の間は (c)〜(d) の STALL 発火を抑止する (下記 (b-3))。`decision.transition` は `first_observation` / `idle` / `active` / `active_continuation` / `anomaly_reset` のいずれか。

   > **観測だけ欲しい場合 (record モードを使わない)**: `--now-ts` / `--prev-record` を省くと helper は `observation` フラット JSON のみを返す。この場合 `--prev-spinner-signature` / `--prev-spinner-elapsed-sec` で前サイクル spinner を渡す (どちらも `''` / `null` を None として受けるので null 安全)。record 更新は下記 update 規則を手で適用することになる。

   **idle streak の保持** (worker ごとに per-pane で永続化):
   - 保存先: `.state/dispatcher/worker-idle-state.json` (1 ファイルに全 worker を JSON object でまとめる、key は `worker-{task_id}`)
   - 各 worker のレコード形式 (例、Issue #680 / #671 併設スキーマ):
     ```json
     {
       "worker-issue-287-stall-signal": {
         "last_visible_content_hash": "9f2c…",
         "last_spinner_signature": "Gesticulating",
         "last_spinner_elapsed_sec": 3702,
         "last_spinner_seen_ts": "2026-07-04T05:48:56Z",
         "idle_streak_cycles": 2,
         "last_check_ts": "2026-07-04T05:48:56Z",
         "last_content_change_ts": "2026-07-04T05:42:30Z",
         "completion_reported_at": null,
         "last_target_line_text": "…(deprecated: 新ロジックでは参照しない、後方互換の残置のみ)"
       }
     }
     ```
   - **新フィールド (Issue #680 / #671)**: `last_visible_content_hash` (前サイクルの正規化済み全可視行ハッシュ、screen-change 判定の基準) / `last_spinner_signature` (前サイクルの新形式 spinner の verb、null 可) / `last_spinner_elapsed_sec` (同 elapsed 秒、null 可) / `last_spinner_seen_ts` (最後に spinner を観測した UTC ts、null 可)。`last_target_line_text` / `last_cursor_*` は **deprecated** — 残置してよいが新ロジックでは読まない。
   - **`completion_reported_at` schema (Issue #658)**: `null | ISO-8601 UTC`。worker が完了報告を出し secretary が `WORKER_COMPLETION_NOTED` を送った時刻 (`received_at`) を保持する review-待ち印。既存 record で **field 欠損は `null` 扱い** (= 完了未報告) とし migration は不要。set は Step 2 の `WORKER_COMPLETION_NOTED` 処理、clear は Step 2 の `WORKER_REOPENED` (fast-path) または Step 5.2 (b)(6) の `runs.status == 'in_use'` self-heal (reliable backstop)、削除は下記更新規則 (4) の record 削除に従う (timeout による自然失効は持たない、(g) 参照)
   - 更新規則 (Step 4 の inspect 直後に評価。**規則 (1)(2)(2-bis)(3)(5) は helper `tools/inspect_pane_state.py: compute_idle_transition()` が record モードで決定的に生成する** — dispatcher は返ってきた `record` をそのまま書き、hash / streak / last_content_change_ts を手計算しない。規則 (3) の anomaly reset は helper に `--anomaly-fired` を渡した時のみ適用される。**規則 (4) (pane 消失で record 削除) と (6) (`completion_reported_at` の lifecycle 更新) は inspect スコープ外なので helper record に**乗らず**、dispatcher が helper record を書いた後に別途適用する** (削除は key ごと消す / lifecycle は Step 2 の監視制御メッセージ処理)。以下は契約面の記述。`last_check_ts` は本サイクルの inspect_pane 呼び出し時刻 = `now` に常に更新される。`last_content_change_ts` の更新は idle→active 遷移時のみで、値は **前サイクルの `last_check_ts`** を採用する — Codex round 3 Major 対応。同サイクル内で `check_messages` (Step 2) が `inspect_pane` (Step 4) より先に実行されるため、現サイクルの inspect 時刻を `last_content_change_ts` に入れると同サイクルで届いた正当な worker→secretary peer-msg の `occurred_at` が cutoff に弾かれて (c)(ii) で空集合になり Step 5.2 が誤発火する。前サイクルの `last_check_ts` を起点にすれば「最後に画面が idle と確認できた時点」が cutoff になり、同サイクル中に届いた peer-msg は確実にその cutoff 以降になるので acked として正しく拾える):
     1. `content_hash` が `last_visible_content_hash` と一致 → `idle_streak_cycles += 1` (`last_content_change_ts` は据え置き)。`last_visible_content_hash` は本サイクルの `content_hash` に更新 (同値なので実質不変)
     2. `content_hash` が変化 (= 画面 scrollback に動きあり = アクティブ) → `last_visible_content_hash` を本サイクルの `content_hash` に更新した上で:
        - **idle→active 遷移時** (前サイクルの `idle_streak_cycles >= 1` から本サイクルで変化を観測) → `idle_streak_cycles = 0` で reset、`last_content_change_ts = (前サイクルの) last_check_ts` に更新 (= 直前まで idle と確認できていた時刻、本サイクルで届く peer-msg より必ず古い)。Step 5.2 (PANE_OUTPUT_WITHOUT_PEER_MSG) がこの ts を起点に worker→secretary 痕跡を scan して fire / acked 判定する
        - **active 継続時** (前サイクルの `idle_streak_cycles == 0` から本サイクルでも変化を観測、出力が連続している間) → `idle_streak_cycles = 0` のまま、`last_content_change_ts` は **据え置き** (active 期間の START 時刻を保持し、active 期間中に届く peer-msg を全部 acked 経路に乗せる)
     2-bis. **spinner フィールドは毎サイクル更新** (Issue #671): helper 出力の `spinner_signature` / `spinner_elapsed_sec` を `last_spinner_signature` / `last_spinner_elapsed_sec` に書き、spinner を観測したサイクルは `last_spinner_seen_ts = now`。spinner 不在 (`spinner_present == false`) なら両値 null (次サイクルの increased 判定を新規 turn 扱いにリセット)。この spinner 更新は hash 判定 (1)/(2) と独立 (spinner は hash から正規化除外されるので active/idle 判定を左右しない — active 継続は scrollback、active-spinner 抑止は elapsed という別軸)
     3. APPROVAL_BLOCKED / ERROR のどちらかが (e) の通知に進んだ場合も reset (anomaly が独立して扱われたので stall 評価を巻き戻す)、`idle_streak_cycles = 0` / `last_content_change_ts = (前サイクルの) last_check_ts` に更新 (anomaly 通知後は再観測扱いで、(2) の遷移時規則と同じ起点を使う)。この reset は hash 変化の有無に依らず適用される (静的な承認プロンプトが複数サイクル残って hash 不変でも、通知が出たサイクルは streak を巻き戻す)。**helper に `--anomaly-fired` を渡すと `decision.transition == "anomaly_reset"` として record にこの規則が織り込まれる** (pane 観測だけからは Step 4 の発火有無が分からないため、発火有無は dispatcher が flag で渡す)
     4. `pane_exited` を Step 1 で受信、または `list_panes` で消失検知 → 該当 key をファイルから削除 (record ごと消えるので `completion_reported_at` も同時に破棄される = CLOSE_PANE / pane 消失時の解除はこの規則が担う、Issue #658)
     5. **既存 record に `last_visible_content_hash` フィールドが無い場合は migration せず「初回観測」として扱う (Issue #680 Blocker 2)**: 本サイクルの `content_hash` を保存し、`idle_streak_cycles = 0` / `last_content_change_ts = null` に**リセット**する。旧実装の `last_target_line_text` と cursor 由来で積まれた `idle_streak_cycles` (実測 13 まで到達) を新ハッシュ判定へ**持ち越さない** (持ち越すと初回から STALL/PANE_OUTPUT 条件を満たして即誤発火する)。旧 `last_target_line_text` は読まない (deprecated 残置のみ)。この初回観測は `compute_idle_transition()` が `prev_record` の hash 欠損を検出して自動でリセットする。次サイクル以降、前サイクル `last_check_ts` が確定した上での idle→active 遷移を待ってから初めて `last_content_change_ts` が値を持つので、Step 5.2 (b)(1) の前提 (ts 存在) を満たさず初回直後の false positive を構造的に抑止する。**同様に `completion_reported_at` field が無い場合も `null` 扱い** (= 完了未報告、migration 不要)
     6. `completion_reported_at` の set / clear (Issue #658): **lifecycle-event ベース**で更新する — Step 2 の監視制御メッセージ処理が `WORKER_COMPLETION_NOTED` 受信で `received_at` を set / `WORKER_REOPENED` 受信で `null` に clear (fast-path)、Step 5.2 (b)(6) の gate が `runs.status == 'in_use'` を観測したとき `null` に self-heal clear (reliable backstop、`WORKER_REOPENED` 取りこぼし対策 = P2)、record 削除 ((4)) で破棄。上記 (1)〜(3) の inspect ベース更新 (idle_streak / last_content_change_ts) は `completion_reported_at` を **触らない** (完了報告済み worker がレビュー中に画面出力しても completion 印は維持され、review 待ち idle の false positive を抑止し続ける)。**timeout による自然失効は持たない** (レビューが長い正常ケースを false positive に戻さないため、(g) 参照)
   - 再起動時の挙動: ファイルが消失/読めない場合は全 worker `idle_streak_cycles = 0` から再観測する (誤検出より見逃しを優先、stall は数サイクル後に再評価される)。`last_visible_content_hash` も消えるので次の初回観測は上記 (5) の初回リセット経路に自然に落ちる。`completion_reported_at` も消えるが、消失中に PANE_OUTPUT が誤発火しても secretary への informational + worker への nudge に留まり (承認・拒否の自動代行はしない)、次の完了受領 / 再指示で再セットされる安全側挙動
   - screen-change 判定・spinner 解析・active-spinner 抑止/cap 判定・idle-state 遷移の決定的コアは helper `tools/inspect_pane_state.py` に codify 済み (`extract_pane_state()` / `compute_idle_transition()`、regression test は `tests/test_inspect_pane_state.py`)。dispatcher Claude は helper 出力を JSON で受けて worker-idle-state.json に反映するだけで、hash / streak を手計算しない (Major 対応)

   #### (b-3) active-spinner suppress (Issue #671)
   stall 候補 ((b) で `content_hash` 不変が 3 サイクル継続) であっても、helper 出力の **`suppress_stall == true`** の間は STALL_SUSPECTED を発火せず、Step 5.2 の PANE_OUTPUT_WITHOUT_PEER_MSG も抑止する。動機: deep-research / ultracode の 1 model turn は scrollback を動かさず (= hash 不変) に新形式 spinner (`{glyph} {Verb}… (1h 1m 42s · ↓ 121.5k tokens)` / `(Xm Ys · ...)`) だけを回し続けることがあり、hash 判定だけでは正常な長考を誤 STALL する。helper は前サイクルの `last_spinner_signature` / `last_spinner_elapsed_sec` と比較して spinner の elapsed が **増加中** (= 生きている) を判定し、`suppress_stall` を返す。

   - **suppress の解除は無条件にしない (Blocker 1)**: `suppress_stall` は「spinner present **かつ** cap 未到達 **かつ** elapsed 増加」の AND。`cap_exceeded == true` (= `spinner_elapsed_sec >= SPINNER_ACTIVE_SUPPRESS_CAP_MIN * 60`) になると helper は `suppress_stall = false` を返し、抑止が解ける。cap 到達後は通常の (c)〜(d) 評価に戻り、hash 不変が続いていれば STALL_SUSPECTED が発火する (API dead で client 側タイマーだけ伸び続けるケースの永久マスク防止)
   - **spinner が凍結したら抑止しない**: elapsed が前サイクルと同値 (= ~3 分サイクルを跨いで進んでいない = 凍結 / hang) なら `spinner_elapsed_increased == false` で `suppress_stall = false`。凍結 spinner は hung の signal なので STALL 評価に戻す
   - **spinner 非表示なら抑止しない**: `spinner_present == false` の worker は本抑止の対象外。hash 判定のみで通常の stall 評価に入る
   - **旧形式 spinner (`{glyph} {verb} for {Xm Ys}`) はここでは扱わない**: 旧形式は Step 4 (d) の spinner-age ERROR (5 分閾値) 側で ERROR として扱う。新旧の regex は ` for ` と `… (` で **disjoint** なので、健全な新形式 active spinner が 5 分で ERROR 化することはない (Major: spinner 2 分類)
   - **suppress 中の soft-note** (retro / debug 用): 抑止したサイクルは journal に記録して silent skip にしない:
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=spinner_active_suppress confidence=n/a note=spinner_elapsed_{spinner_elapsed_sec}s
     ```
   `idle_streak_cycles` は抑止中も (b) の hash 判定に従って加算し続ける (spinner は hash から除外されるため hash 不変 = idle 加算)。よって cap 到達で抑止が解けた瞬間に streak が既に閾値以上なら即 STALL_SUSPECTED が発火する (抑止は「発火の保留」であって streak の巻き戻しではない)。

   #### (b-2) PR-pending-merge sub-state 判定 (Issue #304)
   stall 候補について、(c) の補助シグナル取得に進む **前に** PR-pending-merge sub-state を判定し、(c)(1) で使う lookback window を選択する。これは「worker が完了報告を出した後、Secretary が PR を open し、user が merge 承認するまでの待機」を通常 stall と区別するためのカテゴリ (Issue #304、session #12 で誤発火実測)。

   `.state/journal.jsonl` を一度走査し、`task == "{task_id}"` (= bare task_id、`worker-` prefix を **含まない**。`pr_opened` / `pr_merged` は `docs/journal-events.md` の "PR / push" 表で Writer = secretary、Emitted by = secretary、payload field `task` 値は task_id 本体と定義済) で次 2 件の **存在有無のみ** を取得 (timestamp は判定に使わない):
   - `event == "pr_opened"` で同 task_id の行が 1 件以上ある
   - `event == "pr_merged"` で同 task_id の行が 1 件以上ある

   分岐:
   - `pr_opened` あり かつ `pr_merged` なし → **PR-pending-merge sub-state**。(c)(1) の lookback に `STALL_PR_MERGE_LOOKBACK_MIN = 60` を採用する。`pr_opened` / `pr_merged` は同一 task に対して各 1 件しか記録されない契約 (`tools/run_complete_on_merge.py` 等の helper が idempotent 化) なので、複数行を時系列比較する必要はない (= 「最新の行」を選ぶ必要なし、存在有無で十分)
   - 上記以外 (PR 未 open、または既に merge 済み) → 通常 sub-state。(c)(1) の lookback は `STALL_SECRETARY_LOOKBACK_MIN = 15` のまま

   ```bash
   # ディスパッチャーの cwd は .dispatcher/ なので 1 階層上の .state/journal.jsonl を読む。
   # task_id を bare 値で渡す (worker- prefix 無し)。
   has_pr_opened=$(jq -c --arg t "{task_id}" 'select(.event == "pr_opened" and .task == $t)' ../.state/journal.jsonl | head -1)
   has_pr_merged=$(jq -c --arg t "{task_id}" 'select(.event == "pr_merged" and .task == $t)' ../.state/journal.jsonl | head -1)
   if [ -n "$has_pr_opened" ] && [ -z "$has_pr_merged" ]; then
     lookback_min=60   # PR-pending-merge
   else
     lookback_min=15   # default
   fi
   ```

   **設計メモ**:
   - `pr_opened` / `pr_merged` は `docs/journal-events.md` で Writer = secretary、`task` payload field は task_id (bare) と定義済みなので、新規 event を導入せず既存 ledger だけで判定できる (Issue #304 提案 option 1)
   - `STALL_PR_MERGE_LOOKBACK_MIN = 60` の根拠: PR レビュー / merge 承認の人間応答時間スケール。短すぎると (15 分) `worker_completed` が window から外れて誤発火、長すぎると stuck が見逃される。Issue #304 / session #12 観測の中間値
   - PR-pending-merge を別 event ではなく lookback の延長で扱う理由: (c)(1) の補助シグナル軸 (`worker_completed` を含む 5 種) はそのまま再利用でき、acked 経路に乗せれば既存の de-dup / 通知抑制と整合する。新カテゴリ追加は最小差分
   - merge 後 `pr_merged` event が記録されると次サイクルで sub-state が解け、lookback は 15 分に戻る。merge 後 `worker_completed` の `ts` が 15 分窓から外れた状態で worker pane がまだ open していれば通常 stall として扱われる (運用上は merge と同時に CLOSE_PANE が走るので実質的にこの経路には乗らない)
   - `T_pr_opened` 自体の経過時間は判定に使わない (PR open から 60 分以内/超過の評価は不要)。代わりに (c)(1) の lookback を 60 分に拡張することで、`worker_completed` 等の最新 worker→secretary event が 60 分以内であれば acked、超えれば timeout という同一フォーマットの判定を維持する

   #### (c) 補助シグナル取得 — 直近の worker→secretary コミュニケーション
   stall 候補が見つかったら、STALL_SUSPECTED を発火する **前に** 補助シグナルを取得する。lookback は (b-2) で選択した値 (`STALL_SECRETARY_LOOKBACK_MIN = 15` または `STALL_PR_MERGE_LOOKBACK_MIN = 60`) を使う:

   1. **journal scan (primary, authoritative)**: `.state/journal.jsonl` を読み、`.ts >= now - lookback_min minutes` ((b-2) で選択した値) でフィルタし、以下のいずれかの event を持つ行が 1 件でもあるか確認する:
      - `event == "worker_escalation"` かつ `worker == "worker-{task_id}"` (judgment request の受信)
      - `event == "worker_reported"` かつ `worker == "worker-{task_id}"` (mid-task progress の受信)
      - `event == "worker_completed"` かつ `worker == "worker-{task_id}"` (完了報告の受信、`REVIEW` 待機中の idle 区別用)
      - `event == "plan_delivered"` かつ `worker == "worker-{task_id}"` (plan 引き渡しの受信)
      - `event == "prep_delivered"` かつ `worker == "worker-{task_id}"` (prep 引き渡しの受信)

      これらはいずれも worker 起点の `send_message` を secretary が受信した時点で append される ledger なので、worker→secretary コミュニケーションの authoritative な痕跡になる。`worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered` は `docs/journal-events.md` の **Emitted by = worker** + **Writer = secretary** 行で定義されている。`worker_escalation` は同 catalog 未掲載だが本 `CLAUDE.md` 「ワーカーからの判断仰ぎは人間にエスカレーションする」節および `.claude/skills/org-delegate/SKILL.md` Step 5 で書き込み手順が明文化されている (catalog への追記は curator 領域、本 PR スコープ外)。将来 catalog に **Emitted by = worker** な event が追加された場合は本リストにも追加する (catalog と同期する宣言的リスト)。

      ```bash
      # ディスパッチャーの cwd は .dispatcher/ なので 1 階層上の .state/journal.jsonl を読む。
      # 時間窓ベースの抽出 (行数 cap で打ち切らないこと、journal が長期間追記され続けても lookback 窓は ts で正確に区切る)。
      # 通常時 lookback_min=15、PR-pending-merge sub-state では lookback_min=60 ((b-2) で決定)。
      jq -c --arg cutoff "$(date -u -d "${lookback_min} minutes ago" +%Y-%m-%dT%H:%M:%SZ)" '
        select(.ts >= $cutoff) |
        select(.event == "worker_escalation"
            or .event == "worker_reported"
            or .event == "worker_completed"
            or .event == "plan_delivered"
            or .event == "prep_delivered") |
        select(.worker == "worker-{task_id}")
      ' ../.state/journal.jsonl
      # 1 件以上残れば「ヒット」。具体的な one-liner (PowerShell 環境での date 代替等) は dispatcher Claude の判断。
      ```

   **(c)(1-bis) decision-register gate (Issue #603)**: (c)(1) で `worker_escalation` がヒットした場合に限り、それを acked に数える **前に** pending-decisions register を照会する。journal の `worker_escalation` 行は judgment request 受信時点の痕跡として残り続けるが、その後ユーザー返答が secretary 経由で worker へ転送済み (= 判断待ちが解消済み) でも 15 分窓内に古い行が残っていると acked 抑制が続き、転送後に 529 / stuck で idle な worker を取りこぼす。lifecycle の真値は journal ではなく register が持つので、register を **primary lookup source** として照会する:

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

   2. **org-broker poll_events (forward-compat、現状は補助のみ)**: 現状 `mcp__org-broker__poll_events` は pane lifecycle event のみを流し、`send_message` は流れない (`.claude/skills/org-delegate/references/renga-error-codes.md` の type 表参照)。さらに `since` は時刻ではなく前サイクルから受け取る **opaque cursor** で、初回省略時は「今以降」セマンティクス (過去履歴は返らない) なので、本判定で「過去 15 分」をピンポイント検索する用途には今は使えない。journal scan を **authoritative source** とする。将来 transport backend が `send_message` event を `poll_events` に流すようになれば、Step 1 で既に保持している `.state/dispatcher-event-cursor.txt` の cursor 経由で受信した worker→secretary の送信を `(worker, kind=stall_acked)` ledger に変換するルートを追加する想定 (本 PR ではプレースホルダとして記述するに留める)。

   #### (d) 分岐 (acked vs timeout)
   - **acked の判定** ((c)(1-bis) の gate 込み): acked ⟺ (`worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered` のいずれかが lookback 窓内) **OR** (`worker_escalation` が lookback 窓内 **AND NOT** `escalation_trace_is_stale`)。`worker_escalation` が窓内でも (c)(1-bis) で `escalation_trace_is_stale == true` と判定された場合は acked に数えず、かつ他 4 event のヒットも無ければ **timeout 経路へ落ち**、(従来は古い `worker_escalation` 痕跡で抑制されていた) STALL_SUSPECTED が正しく発火する (Issue #603)。soft-note の `note` 値は既存 (`awaiting_secretary_lookback_15m` / `awaiting_pr_merge_lookback_60m`) を維持する。
   - **acked** — 上記判定で 1 件以上ヒット: 「Secretary 判断待ち idle」または「PR-pending-merge 待機 idle」とみなし、STALL_SUSPECTED を **発火しない**。Step 4 (e) と同じ `anomaly_observed` ledger に soft-note として記録 (新 event 名は導入せず既存 catalog を再利用)。`note` は (b-2) で選択した sub-state を反映する:
     ```bash
     # 通常 sub-state (lookback 15m)
     bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=stall_acked confidence=n/a note=awaiting_secretary_lookback_15m
     # PR-pending-merge sub-state (lookback 60m, Issue #304)
     bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=stall_acked confidence=n/a note=awaiting_pr_merge_lookback_60m
     ```
     以降のサイクルで journal entry が lookback window から外れて 0 件になれば、改めて (c) → (d) を再評価する (持続的 stuck の検出が遅れる代償として、判断待ちの誤発火を避ける trade-off)。

   - **timeout** — 両系統とも痕跡なし、idle 継続: 従来通り stall として扱い、窓口に通知 (lookback は (b-2) で選択した値、通知文に分単位で埋める)。**ただし (b-3) の active-spinner suppress が有効 (`suppress_stall == true`) の間は本 timeout 通知も保留する** — helper が cap 未到達の increasing spinner を検出している間は正常な長考なので発火せず、`kind=spinner_active_suppress` の soft-note のみ残す。cap 到達で `suppress_stall == false` に転じたサイクルで初めて発火する:
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

   #### (f) 設計メモ
   - **`STALL_SECRETARY_LOOKBACK_MIN = 15` の根拠**: Secretary が人間に判断を仰いでから応答を返すまで 5–10 分のオーダーが典型で、その間ワーカーは idle のまま待機する。15 分 window で「直近やり取りあり」を担保すれば、人間応答待ちの誤発火を実用上排除できる。短くすると判断待ちワーカーが timeout 経路に落ちて誤発火、長くすると完了後ペインの reactivation 痕跡を拾い続けて stuck が見逃される。中間値の 15 分が現状のスイートスポット
   - **`STALL_PR_MERGE_LOOKBACK_MIN = 60` の根拠 (Issue #304)**: PR open 後の merge 承認は user の手動操作で 15–60 分かかるのが典型。worker は完了報告済みで idle のまま正しく待機している (= stuck ではない) が、15 分 lookback では `worker_completed` が window から外れて誤 STALL 発火する (session #12 で実測)。`pr_opened` 済 / `pr_merged` 未の sub-state を event ledger だけで判定し、その期間だけ lookback を 60 分に拡張する。merge 後は `pr_merged` が記録されて即座に通常 sub-state に戻る
   - **60 分超過時の挙動 (Issue #304 long-tail)**: PR が 60 分以上 open のまま (週末越え / レビュー長期化) で `worker_completed` が window から外れると timeout 経路で再び STALL_SUSPECTED が発火する。これは仕様上「60 分を越えたら sticky な PR-pending-merge は人間判断対象として再通知する」設計で、Issue #304 の指定どおり。30 秒 de-dup のため 3 分サイクルごとに再通知される点はノイズだが、`org-pull-request` SKILL の close condition (24–48h レビュー idle で人間判断、参照: [`.claude/skills/org-pull-request/SKILL.md`](../../.claude/skills/org-pull-request/SKILL.md)) と組み合わせて運用判断する。長期 PR を完全 silence したい場合は将来 Issue で「`pr_opened` 済 task は STALL を一切上げない」へ変更する選択肢があるが、本 PR では「60 分まで猶予」の lookback 延長に留める (Issue 仕様準拠)
   - **60 分超過 + `ci_completed` の `status="incomplete"` playbook**: PR-pending-merge sub-state の worker が拡張 lookback (60 分) を超過して timeout 経路に入り、かつ events の `ci_completed` payload が `status="incomplete"` のとき、`incomplete` は CI red では **なく判定不能** である (`tools/pr_watch.py` の `gh pr checks --json` クエリが final verdict を取得できないまま retry budget を使い切り、exit-code fallback として記録した値。`status` の値域は event catalog `docs/journal-events.md` を参照)。dispatcher は自分で `gh pr checks` 等を叩いて調査・再試行 **しない** (役割境界: dispatcher は調査しない)。やることは状態の記録 (通常の timeout 経路の soft-note / `notify_sent` に留める) と、secretary への informational 報告 (「`ci_completed` が `status="incomplete"` で CI 判定不能のまま。merge / pane 自動クローズが進んでいない可能性あり」の趣旨) のみ。実例: runtime PR #126 / #127 で 2 連続観測 (merge / pane 自動クローズが進まない事象)。恒久修正 (`tools/pr_watch.py` 側の取得リトライ / 分類改善) は別 Issue で追跡する
   - **journal scan を primary にした理由**: `poll_events` は現状 pane lifecycle event (`pane_started` / `pane_exited` / `events_dropped` / `heartbeat`) のみで `send_message` を流さない (`.claude/skills/org-delegate/references/renga-error-codes.md` の type 表参照)。一方、secretary 受信時の `worker_escalation` / `worker_reported` は authoritative な ledger として既に永続化されている。再利用が正解
   - **soft-note を残す意味**: 後で「なぜ STALL_SUSPECTED が発火しなかったか」を retro / debug で再現できる。silent skip にすると、誤検出疑いが起きたとき journal だけでは判別不能になる。Step 4 と同じ `anomaly_observed` event を再利用するので、event catalog (`docs/journal-events.md`) への新規追記は不要 (kind は `stall_acked`、sub-state は `note` field で `awaiting_secretary_lookback_15m` / `awaiting_pr_merge_lookback_60m` を区別)
   - **decision-register gate を入れた理由 (Issue #603)**: journal の `worker_escalation` 行だけでは「判断待ち継続中」と「ユーザー返答が worker へ転送済みでもう idle」を区別できない (どちらも同じ行が 15 分窓内に残るため)。register の `resolution_kind == "to_worker"` (status `resolved`) が転送完了を表す authoritative signal なので、これを照会して stale な痕跡を acked から外す。これにより転送後に 529 / stuck で idle 化した worker が誤って acked 抑制されず STALL/ERROR 評価に戻る
   - **決定的判定を tools CLI に寄せた理由 (Issue #603)**: stale 判定 (最新 decision の選定 = `received_at` 最大、status / resolution_kind 照合) を prose の jq 直書きにせず `tools/pending_decisions.py latest-resolution` の CLI に寄せた。register schema を直接知る既存 helper (`list_pending_older_than` / `list_escalated_user_replied_older_than` 等) と同じ層分離を維持し、register の内部表現変更に prose が追従しなくて済む (決定的判定はコード側、prose は契約面のみ)
   - **想定シナリオ (Issue #304 acceptance)**:
     - regression: worker が `worker_completed` 報告 → secretary が PR 作成 (`pr_opened`) → CI green → user が 30 分後に merge 承認。30 分時点で (b-2) は PR-pending-merge sub-state (60m lookback)、`worker_completed` は 30 分 < 60 分で acked 経路、STALL_SUSPECTED は **発火しない** ✓
     - inverse: worker が完全停止 (PR 未 open、`worker_completed` も無し)。(b-2) は通常 sub-state (15m lookback)、journal scan で痕跡 0 件、idle streak ≥ 3 サイクルで timeout 経路、STALL_SUSPECTED **従来通り発火する** ✓
   - **想定シナリオ (Issue #603 acceptance)**:
     - regression (誤分類の修正): worker が判断仰ぎ → secretary が user へ escalate → user 返答 → secretary が `resolve --kind to_worker` で worker へ転送 → 直後 worker が 529 で stuck・idle 継続。旧挙動: `worker_escalation` 行が 15 分窓内 → acked → STALL 抑制 → stuck 取りこぼし。新挙動: (c)(1-bis) で register 最新 decision = `resolved` / `to_worker` → `escalation_trace_is_stale == true` → `worker_escalation` 除外 → 他痕跡なし → timeout 経路で STALL_SUSPECTED **発火する** ✓
     - 非該当 (再 escalation で誤発火しない): 転送後に worker が再び判断仰ぎ → secretary が register に新 pending を `append` → 最新 decision = `pending` → `escalation_trace_is_stale == false` → `worker_escalation` は acked に復活し STALL_SUSPECTED は **発火しない** (再判断待ちを stuck と誤判定しない) ✓

<a id="step-5-1"></a>
5.1. **secretary relay gap 検出 (SECRETARY_RELAY_GAP_SUSPECTED)** — Step 5 の sibling、worker→secretary→user の relay の **secretary 側中継漏れ** を検知する独立チャネル:

   **定数**: `STALL_SECRETARY_LOOKBACK_MIN = 15` を再利用 (Step 5 と同じ window、関連事象は同じ時間スケール)。

   #### (a-0) Primary check: pending-decisions register lookup (Issue #297)

   PR #298 (Issue #292) は (a) の動機 (1)(2) を proxy heuristics (snapshot diff / send_message timing) で検知していたが、(a)(2) (user 回答 → secretary → worker の転送漏れ) は worker outbound が起点となるため proxy では出ない死角があった。Issue #297 で Secretary 側に `.state/pending_decisions.json` 相当の **register** を導入し、両方向 (a)(1)(a)(2) を deterministic に追跡する:

   - Secretary は `worker_escalation` を受領した時点で register に `{task_id, received_at, status="pending"}` を append する (CLAUDE.md / `.claude/skills/org-escalation/SKILL.md`)
   - 人間に伝達した時点で `resolve --kind to_user` で `escalated` に更新
   - 人間判断をワーカーに転送した時点で `resolve --kind to_worker` で `resolved` に更新

   ディスパッチャーは tick ごとに register を lookup する:

   ```bash
   # ディスパッチャー cwd は .dispatcher/。helper は repo root 起点で
   # .state/pending_decisions.json を解決するため相対パスは不要。
   python ../tools/pending_decisions.py list --older-than-min 15
   ```

   - 出力 0 行 → register 経由の (a)(1) relay gap は **なし**。ただし (a)(2) は register では捕捉できないため、proxy 経路 ((a)〜(f)) は **必ず続行する** (skip しない)
   - 出力 1 行以上 → 各行 (1 entry per line, JSON、`status="pending"` のみ) を `task_id` 単位で集約し、SECRETARY_RELAY_GAP_SUSPECTED を **(e) と同じ通知経路** で発火する。register は (a)(1) 方向 (Secretary が worker→user の中継を忘れた) を deterministic に拾う ground truth。発火後も同サイクル内で proxy 経路を続行する (proxy が独立に拾う (a)(2) を見逃さないため)。同じ worker に対する重複通知は (f) の de-dup 30 秒窓で吸収される

   **(a)(2) 方向の取り扱い** (Issue #297 のスコープ制限、#301 で deterministic 化): Issue #297 時点では register に「人間が返答済みか」を表す signal が無く、`escalated` 状態を時間で alarm 化すると「人間が考え中」と「Secretary が user→worker 転送を忘れた」を区別できず false positive が常態化していたため、`list --older-than-min` は意図的に `pending` のみを返す設計だった。Issue #301 で `user_replied_at` marker を schema に追加したことで、(a)(2) 方向も deterministic に観測可能になった (下記 (a-2) 経路)。proxy 経路 ((a)〜(f)) は (a-2) を観測する手段が無かった旧 Secretary の既存 entry や Secretary が `mark-user-replied` を呼び忘れたケースの fallback として残置する
   - de-dup と journal 追記は (f) と同じスキーマを使う:

     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=relay_gap_check worker=worker-{task_id} kind=relay_gap_suspected confidence=high
     # 通知送信成功後:
     bash ../tools/journal_append.sh notify_sent source=relay_gap_check worker=worker-{task_id} kind=relay_gap_suspected confidence=high
     ```

     `confidence=high` は register lookup 経由 (proxy より信頼度が高い) を表す。proxy 経路の confidence (n/a) と区別したい場合のラベル。

   **register lookup は (a)(1) の primary、(a-2) と並列に (a)(2) も deterministic 化済み (Issue #301)、(a)〜(f) の proxy 経路は (a-3) Fallback**: 毎サイクル (a-0) → (a-2) → (b)〜(f) を順に実行する。register lookup は (a)(1)(a)(2) 双方の ground truth を提供し、proxy 経路は legacy entry / 呼び忘れ運用ミス / register 不通の degraded mode をカバーする。重複通知は de-dup 30 秒窓で吸収される。proxy 経路の最終削除は (a-2) 安定運用確認後に別 Issue で扱う。

   register が読めない (helper not found / file corrupted で `ValueError`) 場合は (a)(1)(a)(2) 双方とも proxy 経路に fallback する。journal に `anomaly_observed source=relay_gap_check kind=register_unavailable` を残し、(b)〜(f) を従来通り実行する。

   #### (a-2) Primary check: user_replied_at lookup (Issue #301)

   Issue #297 で register lookup を導入した際、(a)(2) 方向 (user 回答 → secretary → worker の転送漏れ) は schema に「人間返答済み signal」が無いため deterministic 化できず proxy 経路に依存していた。Issue #301 で `user_replied_at` (ISO timestamp) を `PendingDecision` に追加し、Secretary が user 返答受領時に `mark-user-replied` CLI で marker を記録する運用に変更したことで、(a)(2) 方向も register lookup で deterministic に判定できるようになった。

   Secretary 側のライフサイクル (CLAUDE.md 「ワーカーからの判断仰ぎは人間にエスカレーションする」セクション):

   - 判断仰ぎ受信 → `append` (status=`pending`)
   - 人間に伝達 → `resolve --kind to_user` (status=`escalated`)
   - **user 返答受領 → `mark-user-replied` (`user_replied_at` 設定、status=`escalated` のまま)**
   - worker に転送 → `resolve --kind to_worker` (status=`resolved`)

   ディスパッチャーは tick ごとに (a-0) の `--older-than-min` lookup と並行して、`user_replied_at` lookup を発行する:

   ```bash
   # ディスパッチャー cwd は .dispatcher/。helper は repo root 起点で
   # .state/pending_decisions.json を解決するため相対パスは不要。
   python ../tools/pending_decisions.py list --user-replied-older-than-min 15
   ```

   - 出力 0 行 → register 経由の (a)(2) relay gap は **なし**
   - 出力 1 行以上 → 各行 (1 entry per line, JSON、`status="escalated"` かつ `user_replied_at` が 15 分以上前のもの) を `task_id` 単位で集約し、SECRETARY_RELAY_GAP_SUSPECTED を **(e) と同じ通知経路** で発火する。register は (a)(2) 方向 (user 回答済みなのに Secretary が worker へ転送忘れ) を deterministic に拾う ground truth。発火後も同サイクル内で proxy 経路 ((b)〜(f)) を続行する (`mark-user-replied` を呼び忘れた legacy entry を proxy がカバーするため)

   - de-dup と journal 追記は (f) と同じスキーマを使う:

     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=relay_gap_check worker=worker-{task_id} kind=relay_gap_suspected confidence=high
     # 通知送信成功後:
     bash ../tools/journal_append.sh notify_sent source=relay_gap_check worker=worker-{task_id} kind=relay_gap_suspected confidence=high
     ```

     (a-0) と (a-2) は同じ `kind=relay_gap_suspected` を共有する。同 worker に対する重複通知は 30 秒窓 de-dup で吸収される (両方向の register lookup が同時 hit するケースは Secretary が両方の中継を忘れた時に限られ、相対的に稀)。

   register が読めない場合は (a-0) と同じ fallback (`register_unavailable` を journal に残し proxy 経路に委ねる)。

   **proxy 経路 ((a)〜(f)) は (a-3) Fallback に格下げ**: Issue #297 時点では (a)(2) の唯一のカバーだったが、#301 で (a-2) deterministic 化が完了したことで、proxy 経路は次のケースの fallback としてのみ意味を持つ:
   - 旧 Secretary 実装で書かれた entry (`user_replied_at` が None のまま) を後方互換でカバー
   - Secretary が `mark-user-replied` を呼び忘れた運用ミスの保険
   - register 自体が読めない / corrupted な状況の degraded mode

   proxy 経路の最終削除は (a-2) の安定運用が確認できた段階で別 Issue で扱う。

   #### (a) 動機
   Step 5 は worker 側 (worker→secretary 痕跡が **ある** ので stall 抑制) を見て補助シグナル化したが、逆方向 (secretary→user / secretary→worker の中継) には盲点がある。具体的なインシデントパターン:
   1. worker が "判断仰ぎます" を secretary に送信 → secretary は受領 (`worker_escalation` が journal に append) → secretary が **人間に上げ忘れ** → worker idle、Step 5 の補助シグナルは「ヒット」扱いで suppress、しかし user は何も知らない
   2. user が secretary に答えた → secretary が worker に **転送し忘れ** → worker idle、同じ穴 (worker 起点 ledger は古いままなので Step 5 はやはり suppress)

   どちらも Step 5 (worker 側監視) と Step 4 (worker pane 画面監視) では検知できない。secretary 側の outbound (secretary→user / secretary→worker) を観測する独立チャネルが必要。Issue #287 (PR #295) の sibling、両側監視で完成。Issue #292。

   **proxy 経路の歴史的スコープ (旧 PR #298)**: 以下 (b)〜(f) は PR #298 当時の proxy-only 実装を記述しており、(1) 「secretary が人間に上げ忘れ」のみを対象としていた。(2) 「user 回答を worker に転送し忘れ」は当時 journal に secretary→worker outbound の ledger が無く検知できなかった。Issue #297 (PR #302) で (a-0) primary lookup により (1) は register 経由で deterministic 化、Issue #301 で (a-2) primary lookup により (2) も `user_replied_at` marker 経由で deterministic 化済み。proxy 経路 (b)〜(f) は legacy entry / Secretary が CLI 呼び忘れ / register 不通の degraded mode の (a-3) Fallback として残置されている。

   #### (b) いつ relay gap を疑うか
   起点は **直近の worker→secretary event** に固定する。`.state/journal.jsonl` から `event ∈ {worker_escalation, worker_reported}` かつ `worker == "worker-{task_id}"` を満たすエントリの最新 1 件を取り、その `ts` を `T_last_worker_in` とする。`worker_completed` / `plan_delivered` / `prep_delivered` は **対象外** (これらは「完了 / 中間引き渡し」で、secretary が直ちに user に上げる契約ではない。判断仰ぎ・進捗共有のみが relay gap の対象)。

   以下を **すべて** 満たす worker を **relay gap 候補** とする:

   1. `T_last_worker_in` が存在し、`now - T_last_worker_in <= STALL_SECRETARY_LOOKBACK_MIN` (= 15 分以内)。これが「直近の worker→secretary 受信あり」の確定条件
   2. `T_last_worker_in` **以降** (= 「あの一手以降」) に secretary 側 outbound 痕跡が **どちらも** ない:
      - secretary→user の visible output 増加が観測されない ((d) 参照)
      - secretary→worker-{task_id} の `send_message` 痕跡が観測されない ((c) 参照)
      
      **どちらか一方** でも `T_last_worker_in` 以降に観測されれば「中継は途中まで動いている」とみなして候補から除外する (誤発火を抑える)
   3. worker pane 自体は idle 継続中 (Step 5 の (b) 「idle streak ≥ 3 サイクル」と同じ条件)。Step 5 の stall 候補集合と (1) の交差を取った後で (2) を見る、と読める

   起点を「直近の worker→secretary event」に固定する理由: 動機 (a) の (1) と (2) は両方とも「**この一手の後** に secretary 側 outbound が止まっている」が共通条件。15 分の固定 sliding window で「user 痕跡なし AND worker 痕跡なし」を要求すると、(a)(2) のように直前に secretary→user の問い合わせが既にあったケースを誤って除外してしまう (= 仕様矛盾、Codex 指摘 Blocker)。`T_last_worker_in` 起点なら、その一手の後で中継が止まったことを正しく拾える。

   #### (c) secretary→worker 観測手段 — 現状は不可、(d)+register で代替
   secretary→worker の `send_message` 発生を journal だけで authoritative に観測する手段は **現状存在しない**:

   1. **journal scan**: 既存 event catalog (`docs/journal-events.md`) に「secretary→worker の send_message 受信時に secretary が書く event」は定義されていない。`worker_escalation` / `worker_reported` / `worker_completed` 等は **worker 起点の inbound** を secretary が記録する ledger であり、逆方向 (secretary→worker outbound) は ledger 化されていない。`user_decision_relayed` のような新 event を捏造して proxy にするのは event 名の確定を要し、本 PR スコープ外 (curator 領域)
   2. **org-broker `poll_events` 経由**: Step 5 (c) と同じく現状の `poll_events` は pane lifecycle のみで `send_message` を流さない。将来 send_message が flow するようになれば、Step 1 の cursor (`.state/dispatcher-event-cursor.txt`) を再利用して `(actor=secretary, recipient=worker-{task_id})` を直接観測できる。プレースホルダ

   従って (b)(2) のうち「secretary→worker 痕跡なし」は、proxy 経路では **常に true** として扱う (痕跡を観測する手段が無いため、中継が動いているかどうかを判別できない)。これにより proxy 経路の絞り込みは事実上 (d) の secretary→user proxy だけに依存することになり、結果的に動機 (a)(2) の「user 答えた後に secretary が worker に転送し忘れ」ケースは proxy では **(d) の secretary 画面更新で擬陽性的に suppress** される。Issue #301 の (a-2) primary lookup (`user_replied_at` marker) で本ケースは deterministic 化済みであり、proxy 経路は legacy entry / 呼び忘れの (a-3) Fallback としてのみ機能する。

   #### (d) secretary→user 観測手段 — `inspect_pane` による画面 diff
   user 向け visible output を直接捉える journal event は無い (user pane に届く文字は端末 backend の terminal レイヤーに流れるだけで journal を経由しない)。代替として **secretary pane の画面差分** を proxy として使う:

   ```
   snapshot = mcp__org-broker__inspect_pane(
       target="secretary",
       lines=40,
       include_cursor=false,
       format="grid"
   )
   ```

   - 永続化契約 (一意化):
     - `.state/dispatcher/secretary-pane-snapshot.txt`: 直近の snapshot **本文** をそのまま保存する (改行 join 済み文字列)。次サイクル開始時にこのファイルを読み、新 snapshot 本文と byte-equal 比較を行う。SHA-256 hash は比較に使うが永続化はしない (毎サイクル本文から再計算する)
     - `.state/dispatcher/secretary-output-changes.jsonl`: 変化検知時に 1 行 append する追記専用 ledger
   - 比較・追記の手順:
     1. 旧 snapshot 本文 (前サイクルが書いた `secretary-pane-snapshot.txt`) と新 snapshot 本文を取得し、それぞれの SHA-256 hash を算出
     2. hash が一致 → なにもしない (画面無変化、(b)(2) は前回までの jsonl 内容で評価)
     3. hash が不一致 → 以下を実施:
        - 新 snapshot 本文を `secretary-pane-snapshot.txt` に上書き保存 (次サイクルの旧 snapshot になる)
        - `secretary-output-changes.jsonl` に `{ts, hash, diff_lines}` を append。`diff_lines` は **新 snapshot にあって旧 snapshot に **無い** 行のみ** を改行 join した文字列 (line-set diff、追加行の純粋な集合)。task_id 紐付け判定はこの "新規追加行" だけで行うため、末尾に残留しているだけの古い task_id 文字列は含まれない (Codex round 3 Major 2 の対応)
   - **(b)(2) の判定 (task_id 必須紐付け)**: `secretary-output-changes.jsonl` から `ts >= T_last_worker_in` の行を取り、`diff_lines` フィールドに **`task_id` または `worker-{task_id}` の substring** を含む行が 1 件以上あれば secretary→user 痕跡 **あり** とみなす。task_id を含まない更新 (別 worker の話題、curator 連携、無関係な user input echo) は痕跡カウントから除外する。これにより別タスク由来の更新で対象 worker の relay gap が誤抑制される問題を回避する。trade-off として、secretary が task_id を文中に明示せずに human に上げた場合は false positive (relay gap が出てない場面で alert) になり得るが、本 PR スコープでは false positive を false suppression より優先する (relay gap 見逃しの方が事故として大きい)
     - 観測履歴ファイルの retention: 末尾 50 行で十分 (15 分 / 3 分サイクル = 最大 5 行の変化観測 × バッファ。古い行は手動 / 別 cleanup で間引く)
   - 制限: secretary が pane 外の I/O (Slack 等) で user に通知した場合はこの proxy では検知できない。実運用上 user は secretary pane を直接見ていることが大半なので許容する trade-off
   - エラー時の挙動 (`[pane_not_found]` 等) は Step 4 と同じく該当サイクル skip し journal に `anomaly_observed source=relay_gap_check kind=inspect_error` を残す

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
   3. **`mcp__org-broker__list_peers` で `client_kind` ベースに user pane を identify して直接送る案** (将来): `list_peers` には `client_kind` が含まれる (`docs/operations/renga-pane-conventions.md` 参照) が、user (人間) のターミナル pane は MCP peer として register していないのが通常で、現状の API 表面では direct send は不可。Issue 化候補 (本 PR では実装しない)

   現状は (1) と (2) の **両方** を必ず発行する。どちらか一方が届かなくても他方で user が認識できる、という冗長性設計。

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

   #### (g) 設計メモ — register 化は (a-0) で着地済み (Issue #297)
   PR #298 で TODO 化した「`.state/pending_decisions.json` 相当の register」は Issue #297 で実装済み。詳細は本セクション (a-0) "Primary check: pending-decisions register lookup" を参照。本セクション (a)〜(f) の proxy 経路は fallback として残置されているが、primary は (a-0) の register lookup に切り替わっている。proxy 経路の最終削除は別 Issue (register lookup の安定運用が確認できた段階) で扱う。

   #### (h) 設計メモ — relay gap と Step 5 stall の関係
   Step 5 の stall 検出は worker→secretary 痕跡があれば「acked」として STALL_SUSPECTED を抑制する。relay gap 検出は **その抑制された acked 集合** にこそ存在する。即ち:
   - Step 5 で `acked` 経路に入った worker = relay gap 検出の入力候補
   - Step 5 で `timeout` 経路 (STALL_SUSPECTED 通知済み) に入った worker = worker 側痕跡が無いので relay gap の (b)(1) が成立せず candidate にならない (= 通常 stall として扱う、単純 stuck)
   - 両者は **disjoint な通知** になるため、windowed de-dup で merge する必要はない (kind が異なる)
   - Step 5 の `worker-idle-state.json` は relay gap 評価でも参照される (idle streak ≥ 3 サイクルが (b)(4) の条件)。新規 state file は (d) の secretary pane snapshot のみ追加で、Step 5 の state とは独立

<a id="step-5-2"></a>
5.2. **PANE_OUTPUT_WITHOUT_PEER_MSG 検出** — Step 5 / 5.1 の sibling、ワーカーがペイン上に応答出力したのに secretary 宛 `mcp__org-broker__send_message` を発行し忘れた **silent dead-lock** を検知する独立チャネル:

   **定数**: `PANE_OUTPUT_SETTLE_SEC = 60` (ペイン content が静止してから fire 候補とするまでの最小経過秒数、(g) で根拠説明)、`PANE_OUTPUT_IDLE_STREAK_MIN = 2` (= `idle_streak_cycles` の最低値。`/loop 3m` cadence で実効 ~6 分の完全静止を要求。(g) で根拠説明)。**look-back window は持たない** (worker→secretary 痕跡の比較は `last_content_change_ts` 起点で行い、(g) Codex round 1 Blocker 対応で 15 分窓は意図的に削除)。値変更時は本ファイルのこの行を直接書き換える。

   #### (a) 動機
   2026-05-09 の renga-ime-paste-routing タスクで実発生したインシデントパターン: worker は窓口の問い合わせに対しペイン上に長文回答を展開したが `mcp__org-broker__send_message(to_id="secretary", ...)` を呼び忘れたため、secretary 側は `mcp__org-broker__inspect_pane` で気づくまで待機し続けた。worker / secretary 間の peer message が SoT のため、これは **silent dead-lock** (どちらの pane も live、Step 4 / Step 5 / Step 5.1 のどの既存検知器にも該当しない) になる:

   - **Step 4 (画面 anomaly: APPROVAL_BLOCKED / ERROR)**: 規定の承認プロンプト / エラー banner が出ていなければ match しない。自由記述の長文回答は anchored regex (b) に乗らない
   - **Step 5 (STALL_SUSPECTED)**: idle_streak ≥ 3 サイクル (= ~9 分) が必要だが、worker がペインに出力を始めれば `idle_streak_cycles = 0` reset で stall 候補から外れる。output 自体が STALL を抑制してしまう
   - **Step 5.1 (SECRETARY_RELAY_GAP_SUSPECTED)**: secretary 側 outbound 不在 (secretary→user / secretary→worker) を観測する逆方向の検知器。worker outbound 不発はカバー対象外

   worker side outbound (= peer message 不発) を独立 channel で観測する必要がある。Issue #287 (Step 5) と Issue #292 (Step 5.1) で確立した `anomaly_observed` / `notify_sent` ledger スキーマと、Step 5 (b) の `worker-idle-state.json` の拡張のみで実装でき、新規 event / 新規 state file は不要。

   #### (b) いつ pane_output_without_peer_msg を疑うか
   ペイン content が一旦変化し、十分長い時間静止し、ペイン活動後に worker→secretary 痕跡が無い worker を **候補** とする。以下を **すべて** 満たすこと:

   1. Step 5 (b) の `worker-idle-state.json` に `last_content_change_ts` (Step 5 (b) 更新規則 (2)/(3) で更新される ISO-8601 UTC ts) が **存在し** (= `null` でない)、かつ `now - last_content_change_ts >= PANE_OUTPUT_SETTLE_SEC` (= ペインが少なくとも 60 秒静止)
   2. かつ `idle_streak_cycles >= PANE_OUTPUT_IDLE_STREAK_MIN` (= 直近 2 サイクル ≈ 6 分は内容変化なし、出力が完全に一段落している)。`/loop 3m` cadence で 6 分の完全静止を要求することで、worker が長時間コマンドを走らせて画面が連続的に shift しているケース (= idle_streak が 0 に reset され続ける) を構造的に除外し、Codex round 1 Major で指摘された「通常作業の途中出力」を nudge する誤検知を抑制する
   3. かつ Step 4 (e) の APPROVAL_BLOCKED / ERROR がこのサイクルで通知経路に乗っていない (= 画面に承認プロンプトやエラー banner が無い)。あれば Step 4 が優先され、PANE_OUTPUT は同サイクルで evaluate しない (規定パターン anomaly と自由出力 anomaly を 1 つの worker に対して二重通知しない)
   3-bis. **かつ Step 5 (b-3) の active-spinner suppress が有効でない** (`suppress_stall == false`、Issue #671)。helper が cap 未到達の increasing 新形式 spinner を検出している間 (= 正常な長考 1 turn で scrollback が動かず peer-msg も出ないのが正常) は本検知を skip する。新形式 spinner の 1 turn 中は worker→secretary の追加 peer-msg が無いのが正常なので、これを silent dead-lock と誤判定しない。cap 到達で `suppress_stall == false` になれば通常評価に戻る
   4. かつ worker の事前 peer-msg 履歴が **存在する** ((c)(i) の existence check)。worker が起動以来一度も worker→secretary 送信を発行していない record は本検知から除外する (Issue #312 の起動 ack を未発行な状態は別検知 (起動失敗 / instruction 未受領) のスコープで、PANE_OUTPUT の対象ではない。「peer-msg 慣行を確立済の worker が以後の活動で peer-msg を漏らした」という silent dead-lock の典型 signature に絞る)
   5. かつ (c)(ii) のクエリ返却が **NULL** (= `last_content_change_ts` 以降に worker→secretary 痕跡が 0 件、ペイン活動後に peer-msg を発行していない)。`last_content_change_ts` を WHERE 句に直接埋め込んでいるため非 NULL 値は必然的に `>= last_content_change_ts` (= acked) を意味し、両者は構造上 disjoint。「(c)(ii) returns NULL」を fire 条件、「(c)(ii) returns 非 NULL」を acked 条件として (d) で分岐する
   6. **かつ completion-review 状態ではない (Issue #658)**。「completion-review 状態」= `completion_reported_at != null` **かつ** DB `runs.status == 'review'` の両立で、この worker は完了報告済み・review 待ちの正常 idle なので本検知の対象外。この gate は最優先で評価し、completion-review 状態なら (c) の SQL query を **発行せず** (d) の completion-review-skip 経路へ直行する (完了報告後に review 待ちで idle な worker を silent dead-lock と誤判定していた false positive の直接原因を断つ)。`runs.status` は `sqlite3 ../.state/state.db "SELECT status FROM runs WHERE task_id = '{task_id}'"` で取得する ((c) と同じ SQLite one-liner idiom、task_id は slug 規約で補間安全):
      - `completion_reported_at != null` かつ `runs.status == 'review'` → (d) **completion-review-skip** (正常 review 待ち idle)
      - `completion_reported_at != null` かつ `runs.status == 'in_use'` → **T6 再指示が landed したが `WORKER_REOPENED` が未反映** (取りこぼし / 順序前後)。StateWriter が書いた `runs.status` は決定的 backstop なので、dispatcher は `completion_reported_at` を **self-heal で `null` clear** し ((d) reopen-self-heal)、通常の (c) 評価へ進んで監視を再開する (P2 対応、`WORKER_REOPENED` 取りこぼしで危険側に倒れないための reliable path)
      - `completion_reported_at != null` かつ `runs.status == 'completed'` → merge 済で CLOSE_PANE 直前。本サイクルは skip 相当で扱う (record は間もなく更新規則 (4) で削除される)
      - `runs.status` が読めない (row 不在 / DB 一過性エラー) → flag のみで skip 側にフォールバック (false positive 抑止を優先、一過性なので次サイクルで再評価)。解除は T6 再指示 (`WORKER_REOPENED` fast-path または `runs.status == 'in_use'` backstop) / CLOSE_PANE・pane 消失 (→ record 削除) の lifecycle event のみで、timeout は持たない (Step 2 / 更新規則 (6))

   `idle_streak_cycles` が ≥ 3 になった時点で Step 5 STALL_SUSPECTED 候補にも該当しうるが、Step 5 の (c)→(d) で acked / timeout の独立判定が走るため、PANE_OUTPUT と STALL は disjoint な kind で並行通知される (de-dup は kind 単位、(f) 参照)。

   #### (c) 補助シグナル取得 — worker→secretary 痕跡 (2 段問い合わせ)
   Step 5 (c)(1) と同じ event 集合 ({`worker_escalation`, `worker_reported`, `worker_completed`, `plan_delivered`, `prep_delivered`}) を 2 つの観点で query する。**look-back window は使わない** (Codex round 1 Blocker 対応): 「`last_content_change_ts` 起点で peer-msg 痕跡を見る」のが正しい sematics で、固定 window だと PR 待ち / レビュー待ちなど正常 idle の worker で痕跡が window から外れて誤発火する。

   M4 cutover (Issue #267、`docs/journal-events.md` 参照) で journal は `.state/state.db` の `events` テーブルが SoT になっているため、SQLite で直接 query する:

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

   **task_id の SQL 文字列補間について** (Codex round 1 Minor): claude-org-ja の task_id は slug 規約 (`[a-z0-9-]+`、CLAUDE.local.md で task_id 値が決まる時点で人間 / runtime helper の制約で実質遵守、引用符 / SQL metachar は事実上含まれない) を満たすため、上記の string interpolation は安全。helper script 化 (sqlite3 `.parameter` 渡し or Python `tools/state_db/queries.py` 拡張) は将来課題で、現状は dispatcher Claude が SQLite one-liner を直接実行する。slug 以外の値が混入する経路 (例: 旧 worker_id 直書き) を将来導入する場合は、helper script 経由必須に切り替える。

   #### (d) 分岐 (completion-review-skip vs reopen-self-heal vs acked vs fire vs no-baseline)
   - **completion-review-skip** — (b)(6) で `completion_reported_at != null` かつ `runs.status == 'review'` (= worker 完了報告済み・review 待ちの正常 idle、Issue #658): **fire しない**。(c) の SQL query は発行せず soft-note のみ追記して次サイクルへ:
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
   - **新規 worker / 旧 record の初回観測**: Step 5 (b) 更新規則 (5) で `last_content_change_ts = null` 扱いとし (b)(1) を不成立にすることで、worker spawn 直後の表示変化や本 PR 以前から永続化されている record で false positive nudge を出すことを防ぐ。worker は起動時に ack 相当の peer message を最初に送る (Issue #312、CLAUDE.md 「ワーカー peer message を受けたら必ず ack を返す」を worker 側からも遵守する) ことが前提なので、初回 activity 観測 → ts 確定 → 次サイクル以降に peer message との比較が始まる、の流れで正しく機能する
   - **(b)(4) 事前 peer-msg 履歴要件 (Codex round 1 Major 対応の補助)**: worker が起動 ack を含めて一度も peer-msg を発行していない record は「peer-msg 慣行が確立していない異常」であり、別検知 (起動失敗 / instruction 未受領) のスコープ。silent dead-lock の典型 signature は「peer-msg 慣行のある worker が以後の活動で peer-msg を漏らした」なので、(b)(4) の existence check で対象集合を絞ることで Major で指摘された誤検知層を更に削減する。`pane_output_no_baseline` soft-note のみ追記して (d) で fire しない
   - **Issue 化なしの起点インシデント**: 2026-05-09 renga-ime-paste-routing タスクで実発生 (issue 化はされていない、本 PR が初の機械検知化)。当時 worker は窓口に「修正完了。次の指示を待ちます」相当の長文回答をペイン上に展開したが send_message 未発行で silent dead-lock 化、人間が `inspect_pane` で発見するまで停滞
   - **既存 ack 強制 (Issue #312)** との関係: ack 強制は secretary 側の責務 (= worker 起点 message 受信時に ack を返す、CLAUDE.md 「ワーカー peer message を受けたら必ず ack を返す」)。Step 5.2 は dispatcher 側の機械観測で worker の outbound 不発を補完する (= ack 強制の対偶側面)。両者は補完関係で、人間運用契約 + 機械観測の二重化により silent dead-lock の発生確率を抑える
   - **`completion_reported_at` gate の動機 (Issue #658)**: worker が secretary へ完了報告を送り、secretary が ack (worker pane へ send_message) を返した後、その ack echo や worker の「レビュー待ちます」出力で `last_content_change_ts` が更新される。以後 worker は review 待ちで追加 peer-msg を出さないため、(c)(ii) が NULL を返し PANE_OUTPUT_WITHOUT_PEER_MSG が **誤発火**していた (完了報告済み worker の正常 idle を silent dead-lock と誤判定、実運用で 4 回再現)。`WORKER_COMPLETION_NOTED` 受領で `completion_reported_at` を立て、(b)(6) gate で本検知を skip することでこの false positive を断つ
   - **skip は `pane_output_without_peer_msg` に限定する (Codex Major 対応)**: `completion_reported_at != null` で抑止するのは Step 5.2 の PANE_OUTPUT のみ。**Step 1 の pane exit 検知 / Step 4 の APPROVAL_BLOCKED・ERROR / Step 5 の STALL_SUSPECTED は完了報告後も有効なまま**残す。完了後 pane に出るエラー banner や承認プロンプトは依然として本物の異常であり、監視全体を completion で止めると見落とすため。`completion_reported=true` を「監視まるごと停止」に拡張してはならない
   - **timeout を持たず lifecycle event で解除する理由 (Codex Major 対応)**: `completion_reported_at` は「完了報告済み・レビュー待ち」の状態印。時間で自然失効させると、レビューが長い正常ケース (数時間の PR レビュー等) で失効後に再び false positive に戻る。解除は lifecycle event のみに固定 — `CLOSE_PANE` / pane 消失で record 削除 (更新規則 (4))、T6 再指示で `WORKER_REOPENED` により `null` clear (Step 2)、新しい完了受領で再セット。この 3 event 以外では消えない
   - **何が検出不能になるか / 許容根拠 (3 問への回答)**: 完了報告後・レビュー待ち中の worker が pane に追加出力しても PANE_OUTPUT は検出しない。state semantics 上 `review` は「worker 側の作業 in-flight なし」なので、この idle は正常であり許容できる。**ただし T6 再指示 (awaiting_review→in_progress) 後まで skip が残ると、レビュー修正中の本物の silent dead-lock を見逃す**。これを防ぐのが T6 の解除契約 (Blocker) で、再指示は secretary→worker 直送で dispatcher が経路上に居ないため、`WORKER_REOPENED` (fast-path) と `runs.status == 'in_use'` backstop (reliable、上記 P2 対応) の 2 経路で監視を再開させる。この解除が無ければ本 gate 導入は net で監視穴を作るため、解除契約と一体で成立する
   - **`WORKER_REOPENED` の best-effort 非対称性と `runs.status` backstop (P2 対応)**: `WORKER_COMPLETION_NOTED` (skip を **立てる**) の取りこぼしは監視が過剰に働く安全側に倒れるが、`WORKER_REOPENED` (skip を **解く**) の取りこぼしは監視が止まったまま = 危険側に倒れる (レビュー修正中の本物の silent dead-lock を見逃す)。両者を同じ best-effort one-shot semantics で扱うと解除が信頼できない。そこで解除は 2 経路にする: (1) `WORKER_REOPENED` = fast-path (速報)、(2) DB `runs.status == 'in_use'` = reliable backstop。T6 再指示で secretary は StateWriter 経由で `runs.status` を `review → in_use` に**決定的に**書く (peer message に依らない DB 遷移) ので、(1) が落ちても (b)(6) gate が (2) を観測して flag を self-heal clear し監視を再開する ((d) reopen-self-heal)。これにより「skip を立てる側は best-effort で十分・skip を解く側は決定的 backstop で担保」の非対称設計になり、Blocker の懸念 (解除漏れで監視穴) を構造的に塞ぐ。`runs.status` は既存 schema (`runs` テーブル、`status ∈ {in_use, review, completed}`) をそのまま consume し新規 event を導入しない
   - **`WORKER_COMPLETION_NOTED` / `WORKER_REOPENED` は完了判定ではなく監視抑止用の受領通知 (Codex Minor 対応)**: 現行契約では worker 完了報告は secretary 宛が正で、dispatcher は自分で完了を判定しない (`docs/contracts/role-contract.md` dispatcher inputs、`delegation-lifecycle-contract.md` T4)。本 peer message はその原則を変えず、dispatcher の監視 loop の false positive を抑止するためだけの受領通知として additive に追加する (contract への追記も additive に留め、既存 ratified 記述は書き換えない)
   - **secretary は non-blocking で送る (Codex Major 対応)**: secretary は worker ack と状態更新 (REVIEW 遷移) を終えた後、best-effort で `WORKER_COMPLETION_NOTED` を送るだけで dispatcher 応答を待たない。dispatcher は `/loop 3m` の通常 `check_messages` で非同期に反映する。blocking wait にすると T4 の human review 移行に新しい停止点を作るため禁止 (secretary 側手順は `.claude/skills/org-delegate/SKILL.md` Step 5 §2a、再指示は `.claude/skills/org-pull-request/SKILL.md` 2c)

5.3. **オンデマンド curate の完了監視 (curate-inflight)** — CLOSE_PANE Step 5-3 ([`.dispatcher/references/pane-close.md`](pane-close.md)) が spawn 直後に書いた `.state/dispatcher/curate-inflight.json` が存在する場合のみ実行する (無ければ skip)。curator の完了待ちを CLOSE_PANE ハンドラでブロッキングせず、本監視ループの通常サイクルに載せるための受け口。判定順序は **(a) → (c) → (b)** ((a) が最優先。pane 消失より先に同サイクル受信済みの CURATE_* を処理しないと、curator が CURATE_DONE 送信後に消えたケースを「未受領のまま消えた」と誤報告して情報が欠落する):

   **定数**: `CURATE_TIMEOUT_MIN = 20` (curate 開始からの初回観測閾値) / `CURATE_HARD_CAP_MIN = 40` (延長を含む絶対上限)。

   (a) **完了受領** (最初に評価): Step 2 の `check_messages` で本サイクルに受領済みのメッセージに curator からの `CURATE_DONE` / `CURATE_SKIPPED` / `CURATE_ERROR` が含まれていたら:
   - `CURATE_ERROR` の場合のみ内容を 1 行で窓口に informational 転送する
   - `mcp__org-broker__close_pane(target="curator")` でペインを閉じる (`[pane_not_found]` / `[pane_vanished]` は既に閉じた扱いで skip — pane が先に消えていても受領済み CURATE_* の処理を優先する)
   - `curate-inflight.json` を削除して終了 (state.db への後始末は不要 — curator identity はそもそも書いていない)。(c)/(b) は評価しない

   (c) **ペイン消失の検知** ((a) で CURATE_* を受領しなかったサイクルのみ): curator の生存を確認する。判定材料は (i) Step 1 の `poll_events` で curator の `pane_exited` を観測した、(ii) `list_panes` の結果に `name == "curator"` が不在 — の 2 系統。**worker 不在の reduced mode (下記 7) では Step 3 が skip されるため、(ii) は本 step 内で `mcp__org-broker__list_panes` を直接呼んで評価する** (Step 3 が走ったサイクルではその結果を再利用してよい)。これにより `events_dropped` / cursor ギャップで `pane_exited` を取り逃しても list_panes 側で必ず検知できる。消失を検知したら inflight を削除し、CURATE_* 未受領のまま消えた旨を窓口に informational 報告する (curator 側クラッシュの可能性。閾値超過分はファイルとして残るため、次回 worker close の閾値チェックで再評価され取りこぼしにはならない)

   (b) **timeout 管理** (受領が無いサイクル): `now - started_at > CURATE_HARD_CAP_MIN` なら**無条件で**下記の打ち切り処理。そうでなく `now - started_at > CURATE_TIMEOUT_MIN` なら、`mcp__org-broker__inspect_pane(target="curator", lines=30)` の出力 hash を inflight の `last_inspect_hash` と突き合わせ、**サイクル間の hash 比較で決定的に**判定する (単発 inspect から「作業継続中か」を主観判定しない。hash 比較は Step 5.1 (d) の secretary-pane-snapshot と同じ idiom):
   - `last_inspect_hash == null` (timeout 後の初回観測) → 現 inspect 出力の hash / 現在時刻 (UTC、本サイクルの `date -u` 由来 `now`) を `last_inspect_hash` / `last_inspect_ts` に書いて継続 (このサイクルでは閉じない。次サイクル以降の比較基準になる)
   - 現 hash ≠ `last_inspect_hash` (前回観測から画面が変化 = 作業継続中) → `extended: true` と現 hash / 現在時刻 (UTC) を書いて継続 (hard cap 到達まで同様に再評価)
   - 現 hash == `last_inspect_hash` (1 サイクル ≈ 3 分以上完全静止 = stall / エラー表示 / 入力待ち) → **打ち切り処理**: 観測内容を添えて窓口に informational 報告 → `close_pane(target="curator")` → inflight 削除。curate は途中終了でも knowledge/ は move-then-mark 設計のため破壊的な中間状態は残らない

   `curate-inflight.json` は handover / resume / `/clear` で**保持される**内部状態ファイル (`.dispatcher/CLAUDE.md` 「監視 gap を埋める内部状態ファイル」)。resume 後の 1 サイクル目から `started_at` 起点で timeout 管理が継続する。

6. **重要**: ディスパッチャーが自動で承認・拒否することはしない (ユーザー判断が必要)

7. ワーカーペインがない場合は `poll_events` / `check_messages` / `inspect_pane` をすべてスキップし、監視ループを停止する。**ただし `.state/dispatcher/curate-inflight.json` が存在する間は停止しない**: Step 1 (`poll_events`) / Step 2 (`check_messages`) / Step 5.3 だけを継続し (worker 向けの Step 3〜5.2 は対象が無いので skip)、inflight 解消 (Step 5.3 (a)/(b)/(c) のいずれか) 後のサイクルで停止する

監視対象のペイン名は `.state/workers/worker-{peer_id}.md` の Pane Name (`worker-{task_id}`) から取得する。

### 設計メモ

- **なぜ `poll_events` を `timeout_ms=5000` で回すか**: 3 分のサイクル間隔の中で 5 秒分は long-poll する。5 秒経過で return して残りの時間は check_messages + list_panes + inspect_pane で補完。これにより pane 終了検知の平均遅延が cadence 非依存に 2.5 秒程度になる
- **cursor 管理**: `.state/dispatcher-event-cursor.txt` に前回 `next_since` を保存する。初回 (cursor 無し) は `since` 省略で「今以降」セマンティクス。crash recovery 時は cursor 消失 = 過去 5 秒分のイベントを取りこぼす可能性があるが、list_panes 突き合わせで回復可能
- **events と list_panes の二重カバー**: events は best-effort (EventsDropped あり得る) なので、`mcp__org-broker__list_panes` による突き合わせを保険として併用
- **inspect を独立した観測チャネルにする理由**: ワーカーが承認待ちで止まった時、worker 自己申告 (org-broker) だけに頼ると worker が通知を送る前に停止してしまう。inspect はディスパッチャー側から能動的に観測するので、worker 側の通知忘れ/遅延を補完する。自己申告と inspect は「同じ事象を 2 チャネルで観測できれば確度が上がる」という冗長性設計
- **anchored regex の意図**: 本文中に "Allow this tool use" が偶然出てもプロンプト自体の行フォーマット (末尾に `(y/n)`) まで揃うことは稀。末尾 non-empty 行に絞ることで誤検出をさらに減らす
- **エラーは message ではなく code で分岐する**: MCP tool result テキストの `[<code>] <msg>` 形式で返る。message 文字列は human-facing で将来変更あり得るので、`[pane_not_found]` / `[shutting_down]` 等の code で case 分岐する。詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`

### cadence 設定の根拠 (2026-05-02 検証)

`/loop 3m` を採用する根拠:

- journal.jsonl 実測 (188 events / 10.83 日, event rate 0.012/min, active session 内 gap median 224s)
- pane lifecycle (`pane_exited` / `events_dropped`) は `poll_events(timeout_ms=5000)` の long-poll 経路で **cadence 非依存に ~2.5 s 検知** されるため、cadence 延長で reactivity は劣化しない
- inspect / check_messages の最悪遅延 60 s → 180 s は anomaly 解消の人間判断時間スケール (数分〜十数分) より十分小さい
- 1m → 3m で Foreman Claude のトークン消費を **約 1/3 に圧縮** (895 → 298 サイクル相当 / 10.83 日)
- ja#6 (1m → 20s 提案) は既に CLOSED、本検証結果は close コメントと整合 (reopen 不要)
