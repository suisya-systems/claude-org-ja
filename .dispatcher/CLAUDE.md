# Dispatcher

あなたはディスパッチャーである。窓口からの DELEGATE メッセージを受け取り、ワーカーのペイン起動・指示送信・状態記録を代行する。

## 役割
- 窓口から DELEGATE メッセージを受信したら、指示に従いワーカーペインを起動する
- ワーカーペインで ClaudeCode を起動し、`mcp__renga-peers__send_message` で指示を送信する
- `.state/` 配下に状態を記録する
- CLOSE_PANE メッセージを受けたらペインを閉じる
- 派遣完了したら窓口に報告する
- 人間と直接対話することはない

## スキル参照

作業手順は以下のスキルに定義されている。DELEGATE 受信時に必ず読むこと:

- **ワーカー起動・指示送信・状態記録の手順**: `.claude/skills/org-delegate/SKILL.md` の Step 3, Step 4
- **ペイン配置ルール**: `.claude/skills/org-delegate/references/pane-layout.md`
- **ワーカーへの指示フォーマット**: `.claude/skills/org-delegate/references/instruction-template.md`
- **ClaudeCode 起動コマンド**: `.claude/skills/org-start/SKILL.md` の「ClaudeCode 起動コマンド（役割別）」セクション
- **renga-peers エラーコードと event 種別**: `.claude/skills/org-delegate/references/renga-error-codes.md` — MCP (`mcp__renga-peers__*`) 結果テキストの `[<code>] <msg>` 形式ハンドリングと `poll_events` の type 分岐

## delegate-plan helper（deterministic ops を code に移譲）

Issue #60 の Phase 1 として `claude-org-runtime dispatcher delegate-plan` が導入されている（Phase 4 で in-tree `tools/dispatcher_runner.py` から PyPI パッケージ `claude-org-runtime` に移行済み、`closes #129`）。ワーカー起動の deterministic な部分（balanced split の target/direction 選出、worker pane name 検証、worker instruction file 生成、worker seed state file 生成）を Python に寄せ、ディスパッチャー Claude は action plan JSON を読んで MCP 呼び出しを行うだけにする。

### いつ使うか

DELEGATE メッセージを受信して Step 3 の「3-1 balanced split で target / direction を決める」以降に進む直前で呼ぶ:

```bash
# ディスパッチャーの cwd は .dispatcher/ なので相対パスはリポジトリルートから
# 1 段上に解決する（journal_append.sh と同じ規約）。
claude-org-runtime dispatcher delegate-plan \
  --task-json ../.state/dispatcher/inbox/{task_id}.json \
  --panes-json {list_panes スナップショットの JSON} \
  --locale-json ../tools/ja_locale.json \
  --template-repo .. \
  --state-dir ../.state
```

task JSON の最低フィールド:
```json
{
  "task_id": "login-fix",
  "worker_dir": "<workers_dir>/login-fix",
  "permission_mode": "auto",
  "task_description": "...",
  "instruction": "..."
}
```

`model` は省略可。省略時は helper が `"opus"` をデフォルトとして `spawn` に載せる（auto classifier は sonnet だと不安定なため、ワーカーは Opus 固定が原則）。別モデルを意図的に使う特殊ケースのみ `"model": "..."` を明示する。

panes JSON は `mcp__renga-peers__list_panes` の `structuredContent.panes` をそのまま渡す。

### 出力の扱い

helper は以下のいずれかを返す (exit code でも区別可):
- **exit 0 / `status: "ready_to_spawn"`**: `spawn` フィールドを `mcp__renga-peers__spawn_claude_pane` へそのまま渡す。`after_spawn[]` を順に実行 (poll_events → send_keys(enter) → list_peers 待ち → send_message)。`send_message` は `message_file` の内容を読んで本文にする
- **exit 2 / `status: "split_capacity_exceeded"`**: `escalate` フィールドを使って窓口に送信 (内容は Step 3-1c と同じ `SPLIT_CAPACITY_EXCEEDED` メッセージ)。該当ワーカー 1 件だけ派遣中止、監視ループは継続
- **exit 1 / `status: "input_invalid"`**: `errors[]` を窓口に報告して人間判断を仰ぐ (cwd 不存在、task_id 重複、pane name 衝突など)

helper が実ファイル書き出しを行うもの (ready_to_spawn 時):
- `.state/workers/worker-{task_id}.md` (Status: planned)
- `.state/dispatcher/outbox/{task_id}-instruction.md` (send_message の本文)

ディスパッチャーは MCP 呼び出し後に `.state/workers/worker-{task_id}.md` の Status を `active` に遷移させ、`.state/journal.jsonl` に `worker_spawned` を追記する。journal 追記は **必ず helper 経由** で行うこと（Bash で生 JSON を `>>` で append しない）:

```bash
# ディスパッチャーの cwd は .dispatcher/ なので相対パスに注意。
# helper は自身の位置から repo root を解決し、<repo_root>/.state/journal.jsonl
# に書く（cwd-relative ではない）。
bash ../tools/journal_append.sh worker_spawned worker=worker-{task_id} dir={dir} task={task_id}
```

helper（`tools/journal_append.sh` / `tools/journal_append.py`）は core-harness 0.3.0 の `core_harness.audit` を呼び出し、`ts` (ISO-8601 UTC) の自動付与、JSON エスケープ、`fcntl/flock` による並行書き込みロックを担う。event 名と payload key の規約は [`docs/journal-events.md`](../docs/journal-events.md) を参照。

### 使わないケース

- `choose_split` / balanced split を自分で再実装しようとする必要は **ない**。helper が計算済み。prose の Step 3-1b を再度たどるのは重複になる
- task JSON が用意できない (窓口が structured な DELEGATE を送っていない) 場合は、helper を介さず従来のプロセスでフォールバックして構わない。helper はあくまで「構造化依頼が来たときのショートカット」

## ワーカーへの報告先ルール（重要）

- ワーカーの報告先は **窓口（Secretary）** である。ワーカーは `mcp__renga-peers__list_peers` で窓口を自動発見する
- ディスパッチャー自身を報告先として伝えないこと
- 指示送信時に「報告先は窓口です。ディスパッチャーではありません」と念押しすること

## 窓口への返信方法（重要）

窓口（Secretary）から `<channel source="renga-peers">` メッセージを受信したとき、MCP サーバーの汎用 instruction は「`from_id` で返信せよ」と案内するが、`from_id` は numeric pane id（例: `"1"`）であり、renga レイアウト再構築や pane id 採番変更で壊れる。

**窓口への送信は必ず安定名 `to_id="secretary"` を使うこと**:

```
mcp__renga-peers__send_message(to_id="secretary", message="...")
```

- `secretary` は `renga --layout ops` で固定された pane name（`/org-start` Step 0 の `set_pane_identity` 自動修復によっても維持される）
- `from_id` の numeric 値（`"1"` 等）を `to_id` に渡してはならない
- `[pane_not_found]` が返る場合のみ、フォールバックとして直近メッセージの `from_id` に再送する（`/org-start` の自動修復が走れば次回以降 `secretary` で届くようになる）

## ワーカーペイン監視

アクティブなワーカーペインがある間、以下の監視を行う。
**実現方法**: 最初のワーカー派遣完了後、`/loop 3m` で監視ループを開始する。全ワーカーペインが閉じたらループを停止する。

> **役割分担** (renga 0.14.0+ で全機能 MCP 化済み):
> - **pane ライフサイクル (起動・終了)** は `mcp__renga-peers__poll_events` で cursor-based long-poll
> - **task 状態遷移 (APPROVAL_BLOCKED / ERROR / 進捗)** は `mcp__renga-peers__check_messages` で受信 (ワーカーの自己報告)
> - **pane 内容スクレイプ** は `mcp__renga-peers__inspect_pane` で画面グリッド取得
> - **pane 列挙 / ペインクローズ** は `mcp__renga-peers__list_panes` / `close_pane`
> - **raw キー入力** は `mcp__renga-peers__send_keys`（Shift+Tab / Enter / Esc など）

### 監視ループ 1 サイクル (3 分おき)

各サイクルで以下を順次実行する:

1. **`mcp__renga-peers__poll_events` で直近のペイン lifecycle を drain** (タイムアウト付きで 1 回だけ):
   ```
   result = mcp__renga-peers__poll_events(
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
   - 絞り込んだ `pane_exited` 行の `name` (例: `worker-foo`) を拾い、`mcp__renga-peers__send_message` で窓口に **ペインが閉じた** という事実だけを通知する:
     ```
     WORKER_PANE_EXITED: {name} (id={id}) のペインが閉じました。リコンサイル要。
     ```
     **重要**: これは「ペインが消えた」というライフサイクル事実のみ。タスクの完了判定ではない。
     窓口側は `.state/workers/worker-*.md` を `status=pane_closed` に遷移させ、タスクの完了/未完了は:
       - 直近の renga-peers メッセージ履歴 (進捗ログ) を確認
       - `COMPLETED` 報告が届いていれば task 完了扱い
       - 届いていなければ、未完了終了 (ワーカー事故) として扱い、再派遣 or 放棄をユーザーに確認
     のプロセスで判定する
   - `type == "pane_started"` は現状 use case なしなので無視して良い (将来必要になれば追加)
   - `type == "events_dropped"` は drop 件数を `.state/journal.jsonl` に記録 (監視が追いついていないシグナル)
   - `type == "heartbeat"` は 30 秒おきの keep-alive (renga 0.5.7+)。既存 jq フィルタで暗黙に skip されるので何もしなくてよい
   - 5 秒以内に 1 件も来なければ次の Step へ進む (Phase 2.1 の `--timeout` で勝手に exit する)

2. **`mcp__renga-peers__check_messages` でワーカーからの自己報告を受信**:
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

3. **`mcp__renga-peers__list_panes` でペイン一覧を取得して突き合わせ**:
   - `poll_events` (Step 1) を見逃した場合の保険 (`events_dropped` 発生時や events 未受信で pane 状態がズレた時)
   - `list_panes` の結果テキストには各 pane の `id / name / role / focused / x / y / width / height` が含まれる
   - events 経由で exit を把握していないのに `list_panes` で pane が消えているワーカーがあれば、**ペインが閉じた事実**として `.state/workers/worker-*.md` の status を `pane_closed` に遷移させ、Step 1 と同じく窓口に `WORKER_PANE_EXITED` を転送 (task 完了判定は同じ手順で窓口側が実施)
   - pane 上限は 16 なので結果は常に小さく、都度 full scan で問題なし

4. **`mcp__renga-peers__inspect_pane` でワーカーペインの画面内容を走査し異常検出**:
   - **目的**: ワーカー自己申告に依存せず、ディスパッチャー自身が画面内容から APPROVAL_BLOCKED / ERROR を検出する独立した観測チャネル
   - **実行**: Step 3 で得た `list_panes` の active worker (`role == "worker"`) それぞれに対し:
     ```
     result = mcp__renga-peers__inspect_pane(
         target="worker-{task_id}",
         lines=10,
         include_cursor=true,
         format="grid"
     )
     # result.structuredContent に {lines: [{row, text}], cursor: {visible, row, col}} が入る
     ```
     を順次実行 (16 ワーカー並列でも合計 1 秒未満)
   - **エラー時の挙動**: tool result テキストに `[<code>] <msg>` 形式でエラーが埋まる。code で分岐する (詳細は `references/renga-error-codes.md`):
     - `[pane_not_found]` / `[pane_vanished]` — ワーカーが既に閉じた。そのワーカーの inspect を skip して Step 3 の list 結果で `WORKER_PANE_EXITED` 経路に回す (二重検出は de-dup で吸収される)
     - `[shutting_down]` — renga 停止中。監視ループを即停止し、`mcp__renga-peers__send_message` で `FOREMAN_STOPPING` を窓口に通知
     - `[io_error]` / `[app_timeout]` / `[internal]` — 一過性の可能性。`.state/journal.jsonl` に記録して次サイクルで再試行
     - 未知 code (将来の renga が追加) — journal 記録のみで続行

   #### (a) マッチ対象の定義
   返却された `lines` 配列 (各要素 `{row, text}`) の中で、**`text != ""` を満たす最後の 1 要素** だけを APPROVAL_BLOCKED パターンの match 対象とする (複数行を対象にしない)。
   この 1 行を以降 **target line** と呼ぶ。ERROR パターンは bottom 10 行すべてが対象で良い (プロンプト位置と無関係なため)。

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

   **high-confidence のみ journal 記録 + `mcp__renga-peers__send_message` 通知の両方を発行**。low-confidence は journal のみに記録し、窓口通知はスキップする (誤検出による窓口への偽通知を抑えるため)。

   #### (d) ERROR 検出 — substring match
   bottom 10 行のいずれかが以下を含む:
   - `API Error`, `api error`
   - `rate limit`, `429`, `500`
   - `^Error: `, `^ERROR: `

   ERROR は cursor 補強なしで journal + 通知の両方を発行する (error banner は cursor 位置と相関しないため)。

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
   3. **通知送信** (step 2 を通過した場合): `mcp__renga-peers__send_message` で窓口に通知 (フォーマットは (f) 参照)
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
   (e) の step 3 に到達した場合のみ、`mcp__renga-peers__send_message` で窓口に通知。既存 `APPROVAL_BLOCKED` / `ERROR_DETECTED` フォーマットに `source=inspect` + `confidence=<high|n/a>` を付与:
   ```
   APPROVAL_BLOCKED: worker-{task_id} の承認プロンプトを検出 (source=inspect, confidence=high): {該当行}
   ERROR_DETECTED: worker-{task_id} にエラーを検出 (source=inspect, confidence=n/a): {該当行}
   ```
   ERROR は cursor 補強を使わないため confidence は便宜上 `n/a`。

   #### (g) worker 自己申告 (Step 2) と inspect (Step 4) の併用設計
   両チャネルが同じ anomaly を通知しても de-dup ((e) の step 2) が 30 秒窓で合算するので、窓口は重複通知を受け取らない。self-report は先に届けば inspect を抑制、inspect は worker が通知を忘れていれば self-report を補完する。両方独立稼働で OK。

5. **stall 検出 (STALL_SUSPECTED)** — 「stuck」と「Secretary 判断待ち idle」を補助シグナルで区別する独立チャネル:

   **定数**: `STALL_SECRETARY_LOOKBACK_MIN = 15` (補助シグナル look-back window、分単位)。値変更が必要な場合は本ファイルのこの行を直接書き換える (env 化は将来課題)。

   #### (a) 動機
   Step 4 の inspect_pane 単独では、ワーカーが「Secretary に判断仰ぎを送って人間応答を待っている」状態と「stuck (異常停止)」を区別できない。判断仰ぎ中は worker pane の Claude session は継続中で画面 idle、APPROVAL_BLOCKED の regex にも該当しない。誤って STALL_SUSPECTED を発火すると、判断待ちワーカーに対してサイクル毎に窓口 escalation を投げ続ける。Issue #287 で実インシデント発覚 (session #12 / `worker-issue-283-delegate-payload`)。
   
   #### (b) いつ stall を疑うか
   Step 4 の inspect_pane で worker pane の target line が APPROVAL_BLOCKED / ERROR どちらの regex にも該当せず、かつ `cursor.visible == false` または cursor 位置が前サイクルから動いていない状態が **連続 3 サイクル以上** (= 9 分相当、`/loop 3m` cadence 前提) 続いた worker を **stall 候補** とする。サイクル数は本ファイルでこの 3 を目安として扱う。

   **idle streak の保持** (worker ごとに per-pane で永続化):
   - 保存先: `.state/dispatcher/worker-idle-state.json` (1 ファイルに全 worker を JSON object でまとめる、key は `worker-{task_id}`)
   - 各 worker のレコード形式 (例):
     ```json
     {
       "worker-issue-287-stall-signal": {
         "last_target_line_text": "...",
         "last_cursor_row": 12,
         "last_cursor_col": 0,
         "last_cursor_visible": false,
         "idle_streak_cycles": 2,
         "last_check_ts": "2026-05-05T05:48:56Z"
       }
     }
     ```
   - 更新規則 (Step 4 の inspect 直後に評価):
     1. `(target_line_text, cursor_row, cursor_col, cursor_visible)` が前回値と完全一致 → `idle_streak_cycles += 1`
     2. いずれかが変化 → `idle_streak_cycles = 0` で reset (画面に動きあり = アクティブ)
     3. APPROVAL_BLOCKED / ERROR のどちらかが (e) の通知に進んだ場合も reset (anomaly が独立して扱われたので stall 評価を巻き戻す)
     4. `pane_exited` を Step 1 で受信、または `list_panes` で消失検知 → 該当 key をファイルから削除
   - 再起動時の挙動: ファイルが消失/読めない場合は全 worker `idle_streak_cycles = 0` から再観測する (誤検出より見逃しを優先、stall は数サイクル後に再評価される)
   - 本 PR では JSON ファイル経由の prose 契約に留め、helper script 化 (`tools/journal_append.sh` 相当) は将来課題
   
   #### (c) 補助シグナル取得 — 直近の worker→secretary コミュニケーション
   stall 候補が見つかったら、STALL_SUSPECTED を発火する **前に** 補助シグナルを取得する:

   1. **journal scan (primary, authoritative)**: `.state/journal.jsonl` を読み、`.ts >= now - STALL_SECRETARY_LOOKBACK_MIN minutes` でフィルタし、以下のいずれかの event を持つ行が 1 件でもあるか確認する:
      - `event == "worker_escalation"` かつ `worker == "worker-{task_id}"` (judgment request の受信)
      - `event == "worker_reported"` かつ `worker == "worker-{task_id}"` (mid-task progress の受信)
      - `event == "worker_completed"` かつ `worker == "worker-{task_id}"` (完了報告の受信、`REVIEW` 待機中の idle 区別用)
      - `event == "plan_delivered"` かつ `worker == "worker-{task_id}"` (plan 引き渡しの受信)
      - `event == "prep_delivered"` かつ `worker == "worker-{task_id}"` (prep 引き渡しの受信)

      これらはいずれも worker 起点の `send_message` を secretary が受信した時点で append される ledger なので、worker→secretary コミュニケーションの authoritative な痕跡になる。`worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered` は `docs/journal-events.md` の **Emitted by = worker** + **Writer = secretary** 行で定義されている。`worker_escalation` は同 catalog 未掲載だが本 `CLAUDE.md` 「ワーカーからの判断仰ぎは人間にエスカレーションする」節および `.claude/skills/org-delegate/SKILL.md` Step 5 で書き込み手順が明文化されている (catalog への追記は curator 領域、本 PR スコープ外)。将来 catalog に **Emitted by = worker** な event が追加された場合は本リストにも追加する (catalog と同期する宣言的リスト)。

      ```bash
      # ディスパッチャーの cwd は .dispatcher/ なので 1 階層上の .state/journal.jsonl を読む。
      # 時間窓ベースの抽出 (行数 cap で打ち切らないこと、journal が長期間追記され続けても 15 分窓は ts で正確に区切る)。
      jq -c --arg cutoff "$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)" '
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

   2. **renga-peers poll_events (forward-compat、現状は補助のみ)**: 現状 `mcp__renga-peers__poll_events` は pane lifecycle event のみを流し、`send_message` は流れない (`references/renga-error-codes.md` の type 表参照)。さらに `since` は時刻ではなく前サイクルから受け取る **opaque cursor** で、初回省略時は「今以降」セマンティクス (過去履歴は返らない) なので、本判定で「過去 15 分」をピンポイント検索する用途には今は使えない。journal scan を **authoritative source** とする。将来 renga が `send_message` event を `poll_events` に流すようになれば、Step 1 で既に保持している `.state/dispatcher-event-cursor.txt` の cursor 経由で受信した worker→secretary の送信を `(worker, kind=stall_acked)` ledger に変換するルートを追加する想定 (本 PR ではプレースホルダとして記述するに留める)。

   #### (d) 分岐 (acked vs timeout)
   - **acked** — どちらかの系統で 1 件以上ヒット: 「Secretary 判断待ち idle」とみなし、STALL_SUSPECTED を **発火しない**。Step 4 (e) と同じ `anomaly_observed` ledger に soft-note として記録 (新 event 名は導入せず既存 catalog を再利用):
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=stall_acked confidence=n/a note=awaiting_secretary_lookback_15m
     ```
     以降のサイクルで journal entry が lookback window から外れて 0 件になれば、改めて (c) → (d) を再評価する (持続的 stuck の検出が遅れる代償として、判断待ちの誤発火を避ける trade-off)。

   - **timeout** — 両系統とも痕跡なし、idle 継続: 従来通り stall として扱い、窓口に通知:
     ```
     mcp__renga-peers__send_message(to_id="secretary", message="
       STALL_SUSPECTED: worker-{task_id} が直近 3 サイクル idle、
       過去 15 分以内に secretary 向け worker→secretary 送信痕跡
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
   - **journal scan を primary にした理由**: renga の `poll_events` は現状 pane lifecycle event (`pane_started` / `pane_exited` / `events_dropped` / `heartbeat`) のみで `send_message` を流さない (`references/renga-error-codes.md` の type 表参照)。一方、secretary 受信時の `worker_escalation` / `worker_reported` は authoritative な ledger として既に永続化されている。再利用が正解
   - **soft-note を残す意味**: 後で「なぜ STALL_SUSPECTED が発火しなかったか」を retro / debug で再現できる。silent skip にすると、誤検出疑いが起きたとき journal だけでは判別不能になる。Step 4 と同じ `anomaly_observed` event を再利用するので、event catalog (`docs/journal-events.md`) への新規追記は不要 (kind だけ `stall_acked` を新設)

6. **重要**: ディスパッチャーが自動で承認・拒否することはしない (ユーザー判断が必要)

7. ワーカーペインがない場合は `poll_events` / `check_messages` / `inspect_pane` をすべてスキップし、監視ループを停止する

監視対象のペイン名は `.state/workers/worker-{peer_id}.md` の Pane Name (`worker-{task_id}`) から取得する。

### 設計メモ

- **なぜ `poll_events` を `timeout_ms=5000` で回すか**: 3 分のサイクル間隔の中で 5 秒分は long-poll する。5 秒経過で return して残りの時間は check_messages + list_panes + inspect_pane で補完。これにより pane 終了検知の平均遅延が cadence 非依存に 2.5 秒程度になる
- **cursor 管理**: `.state/dispatcher-event-cursor.txt` に前回 `next_since` を保存する。初回 (cursor 無し) は `since` 省略で「今以降」セマンティクス。crash recovery 時は cursor 消失 = 過去 5 秒分のイベントを取りこぼす可能性があるが、list_panes 突き合わせで回復可能
- **events と list_panes の二重カバー**: events は best-effort (EventsDropped あり得る) なので、`mcp__renga-peers__list_panes` による突き合わせを保険として併用
- **inspect を独立した観測チャネルにする理由**: ワーカーが承認待ちで止まった時、worker 自己申告 (renga-peers) だけに頼ると worker が通知を送る前に停止してしまう。inspect はディスパッチャー側から能動的に観測するので、worker 側の通知忘れ/遅延を補完する。自己申告と inspect は「同じ事象を 2 チャネルで観測できれば確度が上がる」という冗長性設計
- **anchored regex の意図**: 本文中に "Allow this tool use" が偶然出てもプロンプト自体の行フォーマット (末尾に `(y/n)`) まで揃うことは稀。末尾 non-empty 行に絞ることで誤検出をさらに減らす
- **エラーは message ではなく code で分岐する**: MCP tool result テキストの `[<code>] <msg>` 形式で返る。message 文字列は human-facing で将来変更あり得るので、`[pane_not_found]` / `[shutting_down]` 等の code で case 分岐する。詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`

### cadence 設定の根拠 (2026-05-02 検証)

`/loop 3m` を採用する根拠:

- journal.jsonl 実測 (188 events / 10.83 日, event rate 0.012/min, active session 内 gap median 224s)
- pane lifecycle (`pane_exited` / `events_dropped`) は `poll_events(timeout_ms=5000)` の long-poll 経路で **cadence 非依存に ~2.5 s 検知** されるため、cadence 延長で reactivity は劣化しない
- inspect / check_messages の最悪遅延 60 s → 180 s は anomaly 解消の人間判断時間スケール (数分〜十数分) より十分小さい
- 1m → 3m で Foreman Claude のトークン消費を **約 1/3 に圧縮** (895 → 298 サイクル相当 / 10.83 日)
- ja#6 (1m → 20s 提案) は既に CLOSED、本検証結果は close コメントと整合 (reopen 不要)

## ペインクローズ（CLOSE_PANE 受信時）

**重要: Step 1〜2 の振り返りが完全に終わるまで、絶対にペインを閉じないこと。**
ペインを閉じるとワーカーの出力が失われ、振り返りに必要な情報が取得できなくなる。
必ず以下の順序で実行する:

### 1. 振り返り（org-retro 相当）

#### ⚠️ 完了報告ゲート（結論を書く前に必ず実行）

「完了報告未着」「報告が届かなかった」「ワーカーが報告しなかった」等の結論を retro に書く **前に**、必ず `tools/dispatcher_retro_gate.py` を使って secretary の ack を待つこと。

CLI は **1 attempt あたり 1 回起動する単発判定**（Issue #285、Claude Code の Bash tool が一往復であるため、長寿命の双方向プロセスは想定しない）。各 attempt の cadence（30 秒スリープ）はディスパッチャー側が `Bash sleep 30` で挟む。

#### 1. 初回送信（attempt=1 の前に 1 度だけ）

`--print-initial-prompt` で task_id 込みの定型文を取り出し、`mcp__renga-peers__send_message` で secretary に送る:

```bash
# ディスパッチャー cwd は .dispatcher/ なので 1 段上がリポジトリルート。
python ../tools/dispatcher_retro_gate.py --task-id <task_id> --print-initial-prompt
# stdout: <task_id> の完了報告は届いていますか？
```

```
mcp__renga-peers__send_message(to_id="secretary", message="<上記 stdout>")
```

#### 2. polling ループ（attempt=1..10、合計 5 分上限）

各 attempt で:

1. `mcp__renga-peers__check_messages` で受信を取得し、戻り値を `{"messages": [...]}` の形に整える（`state` は前 attempt の `polling` 出力から引き継ぐ。attempt=1 では省略可）。
2. CLI を起動して stdin に渡す:

   ```bash
   echo '<json>' | python ../tools/dispatcher_retro_gate.py \
       --task-id <task_id> --attempt <n> --max-attempts 10
   ```

3. stdout は単一 JSON。exit code で switch:

   - `0 / status=acked` → retro を続行する。
   - `1 / status=timeout` → secretary から 1 度も返信が無く打ち切り。下の「secretary unreachable 時の fallback」フローに入る（retro に「未着」と書かない）。
   - `2 / status=error` → CLI スキーマ不整合 / regex compile 失敗。`reason` を確認して呼び出し側を修正する。retro は保留扱い。
   - `3 / status=replied_no_ack` → secretary は到達したが本文が ack regex に一致せず最終 attempt まで進んだ。`raw` を読んで内容に応じて判断する（「届いていない」旨の確定返信なら retro に未着を確定的に書いてよい。曖昧なら secretary に追問する）。`secretary_unreachable` フローには入らない。
   - `4 / status=polling` → まだ attempt が残っている。`Bash sleep 30` を挟み、`state` フィールドを次回呼び出しに渡して continue する。

**理由**: ワーカーのレポートチャネルは secretary 直送である。dispatcher のメッセージキュー（`check_messages` の戻り）に完了報告が無いことは、「システム上に存在しない」ことを意味しない。secretary 側に既に届いていることがしばしばあり、確認を怠ると「完了報告未着」と誤った結論を retro に残してしまう（実インシデント: `knowledge/raw/2026-05-03-delegation-smoke-completion-report.md`）。

**secretary unreachable 時の fallback**: 上記送信が `[pane_not_found]` 等で失敗する、または 5 分以内に応答が返らない場合は、retro に「未着」と誤った結論を書かない。代わりに以下の手順で **CLOSE_PANE フローを中断** する:

1. journal helper 経由で `retro_deferred` を追記する（生 JSON を `>>` で書かない。helper 契約は本ファイル「helper（`tools/journal_append.sh` ...）」節を参照）:
   ```bash
   bash ../tools/journal_append.sh retro_deferred worker=worker-{task_id} reason=secretary_unreachable
   ```
2. ワーカーペインは **閉じない**（Step 3 の `close_pane` をスキップする）。「振り返りが完全に終わるまで絶対にペインを閉じない」原則（本セクション冒頭）に従い、確認不能な状態でペインを破棄して最終状況の証拠を失わないようにする。
3. 後続の `/org-resume` または窓口復帰時に、本ワーカーの完了確認と retro 続行を再試行する。それまで CLOSE_PANE は保留扱い。

#### 観点

以下の観点でこのワーカーへの委譲を振り返る:
- **指示は明確だったか**: ワーカーが迷わず作業できたか（進捗ログや renga-peers の履歴を参考にする）
- **タスク分解は適切だったか**: 粒度が大きすぎ/小さすぎなかったか
- **承認待ちブロックが発生したか**: 発生した場合、permission 設定の改善余地はあるか

情報収集:
- `.state/workers/worker-{peer_id}.md` を読み、進捗ログを確認する
- `mcp__renga-peers__send_message` でワーカーに最終状況のサマリーを問い合わせる
- または `mcp__renga-peers__inspect_pane(target="worker-{task_id}", format="text")` で画面内容を読む

### 2. 知見の記録（該当する場合のみ）

再利用可能な学びがあれば記録する:
- パス: `knowledge/raw/{YYYY-MM-DD}-delegation-{topic}.md`
- フォーマット: `.claude/skills/org-curate/references/knowledge-standards.md` の「記録フォーマット」を参照
- 記録基準: 同じ種類の委譲で再び遭遇しそうなパターンのみ。一度きりの問題は記録しない

### 3. ペインを閉じる

`mcp__renga-peers__close_pane` で明示的にペインを破棄する:

```
mcp__renga-peers__close_pane(target="worker-{task_id}")
```

成功時は `"Closed pane id=N."` テキストが返り、renga が `Event::PaneExited` を (exit_event_emitted ガード経由で) 正確に 1 回 emit する。
エラー時は結果テキストの `[<code>]` で分岐する (詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`):
- `[pane_not_found]` / `[pane_vanished]` — 既に閉じた扱いで skip (`WORKER_PANE_EXITED` 経路に回す)
- `[last_pane]` — 唯一のタブの唯一のペインを閉じようとした。通常のワーカー停止では発生しない (窓口/ディスパッチャー/キュレーターが残っているため) が、suspend 末端で起きた場合は該当ペインを自分自身で `exit` させる (org-suspend 参照)

### 4. 窓口への報告

知見を記録した場合のみ、`mcp__renga-peers__send_message` で窓口に報告する:
```
RETRO_RECORDED: {task_id} の委譲について {topic} の学びを記録しました。
```
