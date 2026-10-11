# ディスパッチャー監視の決定的処理への移管 — 設計

> ステータス: **design only**。本書はコード・スキル・契約文書を変更しない。
> 対象: ディスパッチャーが `/loop 3m` で回しているワーカー監視を、常駐する決定的な watcher（Python プロセス）へ移し、意味判断が要る incident のときだけ AI を呼ぶ構成に変える計画。
> 前提として変えないこと: 通常監視は決定的な watcher / reconciler が担う。意味判断が要る incident だけ on-demand の Dispatcher AI に渡す。Claude の `/loop 3m` は廃止する。承認・再起動・close などの副作用は窓口・人間・権限付き handler を経由する。正本は SQLite（`.state/state.db`）。

## 結論

- **範囲は「監視だけ」を推奨する**（§6）。`/loop 3m` の周期ターンを watcher に置き換え、DELEGATE / CLOSE_PANE は今のディスパッチャー Claude が push 受信のたびに処理する形で残す。周期的に AI を消費しているのは監視ループだけで、spawn / close は依頼 1 件ごとの処理なので、移しても AI ターンはほとんど減らない。
- watcher は新規の Python 常駐プロセス 1 本（仮称 `tools/dispatcher_watch.py`）とする。中で使う検知部品の多くは ja に既にある（`tools/relay_scan.py`、`tools/spawn_gate.py`、`tools/inspect_anomaly_scan.py`、`tools/inspect_pane_state.py`、`tools/pending_decisions.py`）。新しく書くのは「サイクルの駆動」「ペイン操作の呼び出し」「状態の永続化」「incident の生成」の 4 つ。
- **watcher からペイン操作（`list_panes` / `inspect_pane` / `poll_events` / `close_pane`）を呼ぶ公開経路が、今の runtime には無い**。対になる runtime 側の作業（公開クライアント）が着手の前提になる（§0 判断 3）。
- 移行は 4 段階で進める。shadow 並走、監視の移管、（任意で）spawn / close の移管、LLM ペインの退役の順。各段の戻し方は「state.db の `monitoring_owner` を戻して `/loop` を再武装する」1 本に揃える（§5）。

## 0. オーナーに判断してほしいこと

| # | 論点 | 推奨 | 推奨しない場合に変わること |
|---|---|---|---|
| 1 | **この移管を claude-org-ja の中で行うか**。ディスパッチャー設計の Issue に 2026-08-17 付けで載った後継 fork（Interlock）の追補は「ja と runtime 0.1 系は v1 保守線とし、新設計を逆移植しない」と定めている。本書の依頼はそれと食い違う | ja の中で行う。ただし範囲は監視に限る（判断 2）。後継スタックの実装は持ち込める部品だけ借りる（§4） | 逆移植しない方針を維持するなら、本書は採らずに ja の監視は現状のまま保守する |
| 2 | 範囲: 監視だけか、spawn / close まで移すか | **監視だけ**（§6）。spawn / close を移すかは段階 2 の実測を見てから決める | spawn / close まで移すと、ディスパッチャーのペインと handover / resume スキルを退役できる。代わりに spawn 儀式（起動プロンプト承認、登録待ち、ultracode 武装）をコード化する費用がかかる |
| 3 | watcher がペインを操作する経路 | **runtime 側に公開クライアントを対で作る**（dispatcher tier の token を発行し、`list_panes` / `inspect_pane` / `poll_events` / `close_pane` / `send_message` を呼ぶ CLI か Python API）。runtime と ja の両方で作業が要る | ja が runtime の非公開 API（`_McpClient` と admin RPC の `mint_token`）を直接使えば ja 側だけで済む。ただし runtime の版上げのたびに壊れうる |
| 4 | 対象の輸送層 | **broker だけ**。renga 面は今の `/loop 3m` を残す（`monitoring_owner=llm` に固定） | renga 面でも外部プロセスから pane 操作を呼ぶ経路を別に用意する必要がある。本書の調査では、その経路を確認していない |

## 1. 全体の構成

```
            ┌──────────── state.db（正本）────────────┐
            │ runs / events / event_deliveries        │
            │ + monitoring_owner / incident /         │
            │   assessment / watch_lease（新設）       │
            └───▲───────────────▲──────────────▲──────┘
                │ 書く / 読む    │ 読む          │ assessment を書く
   ┌────────────┴───┐   ┌───────┴──────┐  ┌────┴────────────────┐
   │ watcher        │   │ 窓口          │  │ Dispatcher AI        │
   │ (常駐 Python)  │──▶│ (人間の唯一の │  │ (incident ごとに     │
   │ 検知・重複排除 │通知│  接点)        │  │  claude -p を 1 回)  │
   │ 期限・再試行   │   └──────────────┘  └─────────────────────┘
   └───┬────────────┘
       │ dispatcher tier の公開クライアント（判断 3）
   ┌───▼─────────────────────────────┐
   │ broker daemon（pane 操作・配送） │
   └─────────────────────────────────┘
```

- **watcher** は 1 サイクルごとに、ペインの lifecycle 取得、ペイン一覧の突き合わせ、全ワーカーの画面取得と既存検知器による判定、台帳クエリ、終端イベントの配送を行う。通知すべきものは窓口へ送る。決定的に結論できないものは incident として state.db に記録する。
- **Dispatcher AI** は incident が開いたときだけ起動する。今の work-discovery と同じく、ツール・MCP・セッションを持たない `claude -p` に incident packet を stdin で渡す（先例: `tools/work_discovery_goals.py:17` と `:745`）。返り値は §3.3 の固定スキーマの JSON だけで、副作用は持たない。watcher はそれを state.db に書いてから窓口へ渡す。
- **窓口**は今と同じ通知を受ける。行動するかどうかを決めるのは窓口と人間である。
- **ディスパッチャー Claude** は段階 2 以降も残り、DELEGATE と CLOSE_PANE だけを push 受信で処理する。周期ターンは持たない。

サイクルの周期は、段階 1・2 では今と同じ 180 秒にする。比較を素直に保つためである。今の重複排除窓と再通知の頻度は「サイクル数」で数えている（例: stall は 3 サイクル、PANE_OUTPUT は idle streak 2 サイクル。`.dispatcher/references/worker-monitoring.md` Step 5 (b) / Step 5.2）。周期を縮めるのは、これらを絶対時間へ書き直した後に限る。

## 2. 仕分け表 — 今の手順書は何に置き換わるか

対象は `.dispatcher/references/worker-monitoring.md`（1,359 行）、`.dispatcher/references/spawn-flow.md`、`.dispatcher/references/pane-close.md`、`.dispatcher/CLAUDE.md` の 4 本。

- **(a)** 既存の決定的部品で置き換えられる（部品の所在を根拠に付ける）
- **(b)** 部品が無く、新しく書く
- **(c)** 本当に意味判断が要る（AI か人間に残す）

本文は移植元として扱わない。持ち出すのは不変条件・検知の意味論・事故由来の警告だけで、警告は §2.5 の回帰テストに変える。

### 2.1 監視ループ（worker-monitoring.md）

| 手順 | 区分 | 置き換え先 / 新しく書くもの | 根拠 |
|---|---|---|---|
| サイクル冒頭の spawn 儀式の未検証検出 | (a) | `tools/spawn_gate.py audit` をそのまま呼ぶ | `tools/spawn_gate.py:876`（`cmd_audit`）。grace window と deployment cutoff は同ファイル `:161`（`GATE_EPOCH`） |
| Step 5.25 終端イベントの配送（CI 結果など） | (a) | `tools/relay_scan.py --recipient secretary --cycle` → 窓口へ送信 → `--mark-delivered` / `--mark-failed` | `tools/relay_scan.py:635`（`cmd_cycle`）、`:689-706`（引数）。台帳は `tools/state_db/schema.sql:129-141`（`event_deliveries` の `UNIQUE (source_event_id, recipient)`） |
| 窓口への送信 | (a) | `claude-org-runtime broker send --to secretary --message ...`（公開 CLI。宛先は安定名） | `claude-org-runtime broker send --help`。ja では既に `tools/peer_notify.py:157-161` が使っている |
| Step 7 停止時の意図的停止の記帳 | (a) | `tools/relay_scan.py --mark-halted`。watcher では「ワーカー側の手順を止める」ときに 1 回書く（tick 自体は止めない。下の Step 7 の行） | `tools/relay_scan.py:706` |
| Step 4 (d) ERROR / spinner 経過の検知 | (a) | `tools/inspect_anomaly_scan.py`（全可視行を走査し、検知ありで exit 3） | `tools/inspect_anomaly_scan.py:346`（`main`）、`:405`（exit 3） |
| Step 5 (b) 画面変化の判定、spinner による抑止、idle streak の遷移 | (a) | `tools/inspect_pane_state.py` の `extract_pane_state()` / `compute_idle_transition()` を import して呼ぶ | `tools/inspect_pane_state.py:217` / `:266`。抑止上限は `:69`（`SPINNER_ACTIVE_SUPPRESS_CAP_MIN = 90`） |
| Step 5.1 判断仰ぎの中継漏れ（register 経由） | (a) | `tools/pending_decisions.py list --older-than-min 15` | `tools/pending_decisions.py:299`（`list_pending_older_than`）、`:537`（`list`） |
| Step 5 (c)(1-bis) 転送済みの判断仰ぎを acked から外す | (a) | `tools/pending_decisions.py` の `latest_for_task` | `tools/pending_decisions.py:381` |
| Step 4 (b) 承認待ちの検知（末尾行の正規表現 6 本と数字選択メニュー） | (b) | 正規表現が本文にしか無い。`tools/inspect_anomaly_scan.py` に足す | `tools/inspect_anomaly_scan.py:27` に「APPROVAL_BLOCKED detection stays in the prose §4(b)」とある |
| Step 1 `poll_events` の取得と cursor の扱い | (b) | watcher は cursor をメモリに持つ。起動時と再起動時は「今以降」から張り直し、一覧の突き合わせ（Step 3）で取りこぼしを拾う。cursor ファイルとセッション束縛の手順は watcher には持ち込まない | `poll_events` は broker の in-memory ring で、daemon 再起動で失われ、履歴の再生も無い（runtime `broker/server.py:204-206`、`:1733-1766`）。イベントは `pane_started` / `pane_exited` の 2 種だけ |
| Step 2 `check_messages` によるワーカー自己報告の受信 | (b) | watcher は dispatcher 宛キューを読まない。`check_messages` は読んだ行を即座に配送済みにする破壊的な読み出しで（runtime `broker/store.py:1116-1139`）、ディスパッチャー Claude と奪い合いになる。監視に必要な事実は state.db から読む（次行） | 同左 |
| Step 2 `WORKER_COMPLETION_NOTED` / `WORKER_REOPENED` による完了フラグの set / clear | (a) | watcher はメッセージを使わず、`runs.status`（`review` / `in_use`）を直接読む（送信側は段階 2 の間は残す。§5）。今の手順書でもこの値が決定的な backstop になっている | `tools/state_db/schema.sql:75`（`runs.status` の値域）。backstop の記述は worker-monitoring.md Step 5.2 (b)(6) |
| Step 3 ペイン一覧の突き合わせ（消失を 2 面で裏取りする） | (b) | 「一覧から消えた」だけでは終了と言わない。`pane_exited` の受信か、2 サイクル連続の消失と別の面の一致で確定させる。この判定をコードにする | 不変条件は worker-monitoring.md「観測の原則」(P2)(P3) |
| Step 3 (3-a-5) 背景タブに置いたワーカーの生存判定 | 対象外 | 背景タブ配置は renga 面専用（worker-monitoring.md Step 3 (3-a-5)）。watcher は broker だけを扱う（§0 判断 4） | — |
| Step 4 (e) 通知の順序と 30 秒の重複排除 | (b) | 観測の記録 → 重複判定 → 送信 → 送信成功後に `notify_sent`、の順を 1 関数にまとめる | 記帳先は `events`（`tools/state_db/schema.sql:100-110`）。書き込みは `tools/journal_append.py` |
| (P4) 観測不能の報告と区間ごと 1 回の重複排除 | (b) | fact state `OBSERVATION_UNAVAILABLE` として持つ。重複キーは「ワーカー・観測面・種別」 | 不変条件は worker-monitoring.md (P4) |
| Step 5 (b-2) PR を開いてから merge までの lookback を 60 分に延ばす | (b) | 本文の SQL をコードにする（`pr_opened` があり `pr_merged` が無い） | worker-monitoring.md Step 5 (b-2) |
| Step 5 (c)(d) stall の acked / timeout 判定 | (b) | 台帳クエリをコードにする。結論が出ない場合は incident を開く（次行） | worker-monitoring.md Step 5 (c) |
| Step 5 (b-fp)(b-4) 「本当に止まっているか」の確認（Workflow 待ちの親ターン、長い単一ターン） | (c) | incident packet にして Dispatcher AI へ渡す。返答は「正常 / 異常 / 証拠不足」のどれか。証拠不足を許す | 誤検知の型は worker-monitoring.md Step 5 (b-fp) / (b-4) |
| Step 5.1 (b) 窓口ペインのスナップショット差分による中継漏れの推測 | (b) | 窓口ペインを画面取得して差分を `.state/dispatcher/secretary-output-changes.jsonl` に追記する処理。コードにするか、register だけで足りるかを段階 1 の差分で決める | worker-monitoring.md Step 5.1 (b)（同ファイル 978 行・985 行付近） |
| Step 5.2 ペインに出力したのに窓口へ送っていない（PANE_OUTPUT_WITHOUT_PEER_MSG） | (b) | 6 条件をコードにする。ワーカーへの促しは「促し handler」1 つに限る副作用とし、窓口への通知は今と同じにする | worker-monitoring.md Step 5.2 (b) |
| Step 5.3 オンデマンド curator の完了待ちと timeout | (b) | 20 分 / 40 分の判定をコードにする。curator には今の `CURATE_*` 送信に加えて `journal_append` で終了イベントを 1 行書かせ、watcher はその行を読む（メッセージは段階 2 の戻し先が使うので残す。§5 段階 2） | 定数は worker-monitoring.md Step 5.3 |
| Step 5.3 curator ペインの close（id の再利用を照合してから閉じる） | (b) | 判定表 8 行をコードにする。close は dispatcher tier の公開クライアント経由（判断 3） | worker-monitoring.md Step 5.3 の close 判定表 |
| Step 6 自動で承認・拒否しない | 不変条件 | watcher には承認・拒否の操作を持たせない | — |
| Step 7 対象ゼロで停止する | (b) | ワーカー・curate 待ち・判定保留のワーカーがすべてゼロなら、ペイン操作を伴う手順（画面取得・一覧の突き合わせ）だけを止める。tick は止めず、終端イベントの配送、dispatcher 宛キューの滞留確認、heartbeat の更新は回し続ける。これらは lifecycle イベントを伴わずに発生するので、lifecycle イベントだけを起床条件にすると取りこぼす。今の `/loop` が対象ゼロで止まるのは AI ターンを節約するためで、AI を使わない watcher には止まる理由が無い | — |
| `/loop` ディレクティブの正文固定フック | 退役 | `/loop` 自体が無くなるので不要（段階 2 以降。renga 面には残す） | `.hooks/check-loop-directive.sh`、`.dispatcher/references/loop-directive-guard.md` |
| 監視ループのサイクル中に画面取得が省かれる | 構造で解消 | watcher は毎サイクル全ワーカーを取得し、サイクルごとに「ワーカー数・画面取得数」を events に 1 行残す | 2026-09-05 に、サイクル内の画面取得が 30 分以上ゼロのまま「異常なし」と結論し続けた実例がある |

### 2.2 spawn（spawn-flow.md）— 範囲を広げた場合だけ移す

| 手順 | 区分 | 置き換え先 / 新しく書くもの | 根拠 |
|---|---|---|---|
| 3-1 配置と容量の判定、計画 JSON の生成 | (a) | `claude-org-runtime dispatcher delegate-plan` | runtime `dispatcher/runner.py:3527`。ja の現用は `.dispatcher/CLAUDE.md:47-55` |
| 3-1c 容量超過時の escalate 文を一字一句そのまま転送 | (a) | 計画の `escalate.message` をそのまま `broker send` に渡す | `.dispatcher/CLAUDE.md:86` |
| 3-2 `spawn_claude_pane` の呼び出し | (b) | dispatcher tier の公開クライアントが要る（判断 3） | spawn 系は dispatcher / secretary tier 限定（runtime `broker/surface.py:412-431`） |
| 3-3b 起動プロンプトの画面判定による承認 | (a)+(b) | 1 手の判定は `claude-org-runtime dispatcher spawn-prompt-step`（exit 0 / 10 / 2）。画面取得 → 判定 → 送鍵を繰り返す駆動部分は新規 | runtime `dispatcher/spawn_prompt.py:258`（コマンド）、`:231`（`decide`）。手順は `.dispatcher/references/spawn-flow.md:217` 以降 |
| 3-4 `list_peers` による登録待ちと capability ゲート | (b) | 共有 reference の三値判定をコードにする | `.dispatcher/CLAUDE.md:81-84` |
| 3-5 指示の送信 | (a) | 計画の `message_file` を `broker send --to worker-{task_id}` で送る | runtime `broker/notify.py:102-175` |
| 3-5a ultracode 武装（brief の本文を読んで許可を判定し、send_keys で kickoff） | (c) → (b) | 今は brief の散文を読んで判定している。`tools/gen_delegate_payload.py` の送信計画に構造化フラグを載せれば (b) になる | `.dispatcher/references/spawn-flow.md:280` 以降 |
| Step 4 状態記録（ワーカー md の active 化、run 登録、`worker_spawned`） | (a) | `tools/spawn_gate.py verify` が成功時にワーカー md を書き換え、`tools/journal_append.py` が events に書き、`StateWriter.transaction()` が run を登録する | `tools/spawn_gate.py:671`（`cmd_verify`）、`:482`（`_rewrite_worker_md`）。`tools/state_db/writer.py` |
| Step 5 派遣完了ゲート（`DELEGATE_COMPLETE` は自分で書かない） | (a) | `tools/spawn_gate.py verify` の stdout をそのまま窓口へ送る | `tools/spawn_gate.py:647`（`_delegate_complete_body`） |
| 3-1d / 3-2b 背景タブ配置 | 対象外 | renga 面専用 | — |

### 2.3 close（pane-close.md）— 範囲を広げた場合だけ移す

| 手順 | 区分 | 置き換え先 / 新しく書くもの | 根拠 |
|---|---|---|---|
| 1 完了報告ゲートの ack 判定 | (a) | `tools/dispatcher_retro_gate.py`（1 回ごとの単発判定。駆動は watcher が持つ） | `tools/dispatcher_retro_gate.py:321` |
| 1 振り返りの観点と記述、2 知見の記録 | (c) | LLM の作業。窓口の `/org-retro`（完了後に窓口が回す既存の振り返り）に寄せる | `.dispatcher/references/pane-close.md` 1「観点」 |
| 3 自タブの確立と identity 照合を経た `close_pane` | (b) | close 判定をコードにする | `.dispatcher/references/pane-close.md` 3 |
| 5 curate 閾値の判定 | (a) | `tools/check_curate_threshold.py`（exit 10 のときだけ curator を起動） | `tools/check_curate_threshold.py:315` |
| 5-3 curator ペインの spawn | (b) | 2.2 の spawn 部品を流用する | — |
| 6 work-discovery の triage scan と転送 | (a) | `tools/work_discovery_scan.py --trigger worker_close --all-registry-repos`。exit 10 なら JSON をそのまま窓口へ送り、exit 2 なら必ず報告する | `.dispatcher/CLAUDE.md:259-288` |

### 2.4 ディスパッチャー役割文書（.dispatcher/CLAUDE.md）

| 項目 | 区分 | 扱い |
|---|---|---|
| credential を代行しない | 構造で解消 | watcher にはワーカーの依頼を受け付ける口が無い |
| 窓口へは安定名 `secretary` で送る | (a) | `broker send --to secretary` |
| `spawn_claude_pane` の `args[]` に brief 中のフラグ風の文字列を直訳しない | 構造で解消 | 引数は計画 JSON からだけ組み立てる |
| handover / resume（context が太ったときの引き継ぎ） | 段階で縮退 | 段階 2 で監視状態が抜けるため、引き継ぐのは DELEGATE / CLOSE_PANE の処理中状態だけになる。退役するのは段階 4 |

### 2.5 事故由来の警告から作る回帰テスト

本文の警告段落は、コードにすると警告そのものが不要になる。代わりに次のテストを watcher 側に置く。

| テスト | 元になった事故 |
|---|---|
| 全タイムスタンプが UTC（`Z` 付き）で、経過比較が負にならない | ローカル時刻に `Z` を付けて書き、curate の timeout が永久に発火しなかった |
| `pane_exited` は完了ではない。ペインの終了と task の完了を別の事実として通知する | 終了＝完了と読む誤り |
| 一覧から消えたことや `[pane_not_found]` だけでは終了としない | 人間が別タブで開いていた生きたセッションを「消失」と申告した（2026-08-08） |
| 入力欄が空、spinner が無い、だけでは idle としない | 作業中も入力欄は空のまま出る |
| 末尾 N 行ではなく全可視行を走査する。529 を含める | 上へスクロールしたエラー表示を取りこぼした |
| 新形式 spinner による抑止は 90 分で解除する | API が死んで client 側のタイマーだけ伸び続ける場合に、永久に抑止される |
| エラーを説明する文書行を、発生したエラーと誤検知しない | worker-monitoring.md Step 4 (d) の status-code ゲート |
| 数字選択メニュー形式の承認待ちを拾う | 2026-07-27 の取りこぼし |
| PR を開いて未 merge の間は lookback が 60 分になる | merge 承認待ちを stall と誤判定した |
| 窓口が判断を転送済みのワーカーは acked に数えない | 古い判断仰ぎの痕跡で、転送後に止まったワーカーを見逃した |
| 観測不能の重複キーは観測面を含む。区間ごとに 1 回だけ報告する | 先に劣化した面の通知が、後から劣化した別の面を覆い隠した |
| 配送 scan は「対象なし」と「走っていない」を区別する（exit 127・空出力・非 JSON は失敗として扱う） | `python` と綴っていたため 20 日間 scan が空振りし、終端イベント 134 件が滞留した |
| 意図的な停止の後の初回 audit は警報を出さない | 起動のたびに偽警報が鳴り、読み手が警報を無視するようになる |
| curate の hard cap は経過時間だけで判定する | 観測不能が続くと終端条件が消えた |
| close の前に id の再利用を照合する | 別ペインを閉じる危険 |
| 起動プロンプトに盲目で Enter を送らない（spawn を移す場合） | folder-trust の初期カーソルが「No, exit」で、ワーカーが終了した（2026-09-25） |
| 毎サイクル、画面取得数がワーカー数と一致する | 2026-09-05 に画面取得が省かれたサイクルが続いた |

## 3. 新しく作るもの

### 3.1 watcher 本体

- 1 プロセスで、broker の lifecycle イベントと 180 秒の reconcile tick の両方で起きる。1 回の tick は §2.1 の (a)(b) の行を固定の順で実行する。サイクルの中身を省ける余地は作らない。
- 判定は「ワーカーごとの fact state」として持つ。値は次の閉じた集合とする: `ACTIVE_EVIDENCE` / `KNOWN_WAIT` / `EXPLICIT_BLOCK` / `NO_ACTIVITY_EVIDENCE` / `OBSERVATION_UNAVAILABLE` / `TERMINAL`。`NO_ACTIVITY_EVIDENCE` は異常ではない。窓口へ異常として通知するのは `EXPLICIT_BLOCK`（承認待ち・エラー）だけで、`NO_ACTIVITY_EVIDENCE` が続いたときは §2.1 の stall 判定を通り、結論が出なければ incident になる。
- 今 `.state/dispatcher/worker-idle-state.json` に持っているワーカーごとの記録は、state.db のテーブルに移す。`completion_reported_at` は `runs.status` に置き換わるので持たない。

### 3.2 state.db への追加（`tools/state_migrate.py` の migration で入れる）

| テーブル / 列 | 用途 |
|---|---|
| `monitoring_owner`（`org_sessions` の列か 1 行テーブル。値は `llm` / `shadow` / `watcher`） | 二重監視を構造で防ぐ。ディスパッチャーの `/loop` 武装（`/org-start` 起動後の検証、`/dispatcher-resume` Step 5、窓口からの再武装）と watcher の両方が必ずこれを読む |
| `watch_lease` | watcher を 1 プロセスに限る。§4 の lease と fencing token をそのまま使う |
| `watch_heartbeat` | watcher 自身の生存。tick ごとに更新する |
| `incident` / `assessment` | §3.3 |
| `events` の kind | shadow 期の記帳は `shadow_` で始まる別の kind に隔離する。本番の `notify_sent` に混ぜると、本番の重複排除が本物の通知を抑えてしまう |

### 3.3 incident と Dispatcher AI

- incident の行: incident id、run id / task id、発生・更新時刻、fact state、検知器の版、根拠の参照先（events の id、画面ハッシュ、`runs.status`、経過時間）、直近の状態遷移、重複キー、再試行回数、前回の assessment。
- AI への入力は incident packet だけにする。画面の自由文は packet に入れず、構造化したフィールドと正規化済みの画面行に限る。
- 返り値のスキーマ: `classification`（`normal` / `stalled` / `insufficient_evidence`）、`confidence`、`evidence_refs`、`missing_evidence`、`recommendation`（`keep_watching` / `notify_secretary` / `ask_worker_status`）、`human_gate`。
- watcher は返り値を `assessment` に書く。`notify_secretary` のときだけ窓口へ通知する。`ask_worker_status` は窓口に「状況確認を送るか」を委ねる通知として出し、watcher 自身はワーカーへ送らない。
- AI 呼び出しが失敗した、あるいはスキーマに合わなかった場合は、今と同じ `STALL_SUSPECTED` を窓口へ送る（今より悪くならない側へ倒す）。

### 3.4 常駐プロセスとしての登録と回収

- `/org-start` が watcher を起動し、watcher は起動直後に runtime の residents registry へ自分を登録する。置き場所は `org down` に渡す state dir の下の `residents/*.json` で（runtime `broker/residents.py:52`、`:580`）、schema は `RESIDENT_SCHEMA_V1`（同 `:357-394`。必須は `version` / `name` / `pid` / `started_at`（kernel のプロセス開始時刻） / `owner{state_dir, root_cwd, fingerprint}`）。
- 登録簿を書くのは利用側（ja）で、runtime は走査・照合・告知・回収だけを行う（runtime `broker/residents.py:2-25`）。**ja は今どのプロセスも登録していない**（ja 内で `residents` を参照する箇所は 0 件）。開始時刻と指紋の計算には runtime の `_observe_process_identity`（`:258-269`、非公開名）と `repo_fingerprint`（`:283`）を使う。前者は判断 3 の公開化に含めて runtime へ依頼する。
- `/org-down` は今 `org down` を `--reap` 無しで呼んでいる（`.claude/skills/org-down/SKILL.md:160`）。`--reap` を付ける。daemon の停止が確認できないとき、runtime は回収を告知だけに落とす（runtime `broker/launcher.py:1036-1047`）。
- watcher 自身の死は `watch_heartbeat` の鮮度で検知する。人間に見せる経路は attention watcher に任せる。attention watcher が今読んでいるのは events の 4 種類（`notify_sent` / `ci_completed` / `worker_completed` / `pr_merged`）、`.state/pending_decisions.json`、broker の `queue.jsonl` だけなので（runtime `attention/readers.py:22-35`）、heartbeat の鮮度を読む reader を runtime 側に 1 つ足す。

## 4. 後継スタック（Interlock / continuo）から持ち込むもの

調べた版は interlock `65f36c5`（Python、stdlib の sqlite3）と continuo `38c667b`（TypeScript / Node。interlock から移植したと明記したファイルが多い）。interlock の該当モジュールの多くは「spike の足場で、既定では捨てる。残るのはテストの方」と自ら書いている（例: `src/claude_org_runtime/control_plane/lease.py:1-14`）。

| 学び | 持ち込むか | 形 | 根拠 |
|---|---|---|---|
| lease と fencing token（書き込みと同じ SQL 文の中で lease を検査する） | **持ち込む（コードとして）** | watcher を 1 本に限る `watch_lease`。判定は interlock の `FENCE_SQL` と同じ形にする | interlock `control_plane/lease.py:132`（`FENCE_SQL`）、`:318`（acquire）、`:386`（renew）。テストは `tests/control_plane/test_lease.py` |
| watcher の heartbeat と「沈黙している監視範囲」の検出 | **持ち込む（考え方とコード）** | `watch_heartbeat` と鮮度判定。interlock の対象は CI / PR の監視範囲だけなので、ワーカー監視向けに読み替える | interlock `control_plane/watcher.py:428`（heartbeat）、`:645`（`silent_scopes`） |
| SIGKILL による fault injection（指定した区間で止めて外から kill し、再起動後に不変条件を名前で検査する） | **持ち込む（コードとして）** | 段階 2 の受け入れ試験。接続先を ja の `StateWriter` に付け替える | interlock `tests/fault_injection/`（`controller.py`、`manifest.json`、`test_cases.py`） |
| 一方向の reconcile（事実が正当化する分だけ確定し、残りは報告するだけ） | **持ち込む（考え方）** | §3.1 の tick の基本形 | continuo `src/gate/operator.ts:1084`（`reconcile`） |
| outbox（再試行回数を先に確定 → 効果 → 配送済み、handler ごとに 1 回限りの効果の担保方法を宣言する） | **持ち込む（不変条件だけ）** | ja には既に `event_deliveries` の配送台帳がある（`tools/state_db/schema.sql:129-141`）。新しい outbox は作らず、「handler ごとに担保方法を宣言する」規律だけを入れる。send_message / send_keys には宛先側の冪等キーが無いので、受け手が重複を許す（終端イベントの二重受信を窓口が冪等に扱う、今の relay と同じ）か、人間ゲートを通すかのどちらかを handler ごとに書く | interlock `control_plane/outbox.py:123`（担保方法の列挙）、`:688`（attempt） |
| incident packet と assessment のスキーマ、AI 呼び出しの台帳 | **持ち込む（考え方だけ）** | §3.3。interlock には incident テーブルの DDL があるが、incident を書くコードも assessment のスキーマも無い。ja で設計から作る | interlock `migrations/0001_initial.sql:441-484`。`INSERT INTO incident` は両リポジトリの src に無い |
| 6 つの fact state | **持ち込む（語彙だけ）** | 判定の述語は ja で書く。後継スタックでも、fact state を出す検知器は未実装 | interlock `DECISIONS.md` D-0005（`:242`） |
| 起動前に binding を書き、spawn を lease で囲み、再起動時は再 spawn する前に照合する（supervisor の walk） | 不変条件だけ | spawn を移す場合（段階 3）に使う。コードは provider の `start` / `resume` / `read_state` を前提にしており、実際の provider は `claude -p` | interlock `supervisor/session_orchestrator.py:585`（start）、`:651`（recover） |
| SessionProvider / MessageBus の分離、ワーカーが取りに行く方式の配送 | 持ち込まない | ja のワーカーは broker から push で受け取る。配送方式を変えるのは本書の範囲外 | interlock `messagebus/bus.py`、`session/provider.py:560` |
| `claude -p` の子プロセス provider、stream-json の起こし方 | 持ち込まない | ja のワーカーは tmux / renga のペインで動く対話セッション | interlock `session/claude_cli_provider.py` |
| 画面ハッシュ・spinner・画面正規表現を観測手段から外す | 持ち込まない（ja では残す） | 後継スタックがこれらを捨てたのは、構造化された状態を取れる provider があるからである。ja には代わりになる信号が無く、既存の検知器（§2.1 の (a)）が唯一の観測面 | interlock `PORTING_LEDGER.md:32-68` の discard 欄 |

## 5. 段階移行

各段の戻し方は共通で、`monitoring_owner` を `llm` に戻し、今の `/org-start` 起動後の検証手順で `/loop 3m` を再武装する。watcher は `monitoring_owner` が `watcher` でも `shadow` でもないと分かれば、heartbeat の更新以外を止める。

### 段階 1: shadow 並走

- やること: watcher を起動し、`monitoring_owner=shadow` にする。watcher は読むだけで、`list_panes` / `inspect_pane` / `poll_events` と state.db を読み、判定を `shadow_` 付きの kind で events に書く。窓口へは送らない。ディスパッチャー Claude は今のまま `/loop 3m` を回す。
- 前提: runtime 側の公開クライアント（判断 3）。§2.1 の (b) の検知コードと §2.5 の回帰テスト。
- 比較レポート: `tools/dispatcher_watch.py report` が、同じワーカー・同じ種別について、本番の `notify_sent` と shadow の判定を時間窓で突き合わせる。出すのは「watcher だけが出した」「LLM だけが出した」「観測不能率」「通知遅延」の 4 つ。
- 受け入れ条件: 連続した 30 run 以上で、LLM だけが出して watcher が出さなかった `APPROVAL_BLOCKED` / `ERROR_DETECTED` / `WORKER_PANE_EXITED` / 終端イベント配送が 0 件。watcher だけが出したものは 1 件ずつ実害の有無を記録する。§2.5 のテストがすべて通る。
- 戻し方: watcher を止めて登録を消す。本番への作用は無い。

### 段階 2: 監視の移管

- やること: `monitoring_owner=watcher` にし、ディスパッチャー Claude の `/loop 3m` を止める。watcher が窓口への通知と終端イベントの配送を始める。incident が開いたときだけ Dispatcher AI を呼ぶ。watcher は完了の受領を `runs.status` から、curate の終了を curator が書く終了イベントから読む。窓口の `WORKER_COMPLETION_NOTED` / `WORKER_REOPENED` と curator の `CURATE_*` の送信は段階 2 の間も残す（戻したときに LLM 側の手順がこれらを消費するため。worker-monitoring.md Step 2 / Step 5.3 (a)）。ディスパッチャー Claude は `monitoring_owner` が `llm` でない間も、これらを受けたら今の手順どおり `.state/dispatcher/worker-idle-state.json` の `completion_reported_at` と `curate-inflight.json` の `curate_result` を更新する（通知と close はしない）。段階 2 でも curator の spawn と inflight の記録はディスパッチャー Claude が続けるので、戻し先が読む状態はこれで最新に保たれる。
- `/loop` が担っていた監視以外の役目の新しい持ち主:
  - push が使えないときにディスパッチャー宛の DELEGATE / CLOSE_PANE を取りに行く役目 → watcher が dispatcher 宛キューの滞留を監視する。既存の `tools/secretary_queue_watcher.py --owner dispatcher` の判定を流用する（`--owner` 引数は同ツールの `--help` にある）。ただし同ツールは起動後に積まれた行だけを滞留判定の対象にするので、watcher の起動時と再起動時に、既に積まれている未配送の dispatcher 宛の行も 1 回走査して判定に含める。滞留したらディスパッチャーペインへ `check_messages` を促す 1 行を送る。
  - curate 待ちの管理、終端イベントの配送、判断仰ぎの経過管理、対象ゼロでの停止 → すべて watcher（§2.1）。
- 受け入れ条件:
  - incident が無い時間帯のディスパッチャー AI の周期ターンが 0（ディスパッチャーセッションの transcript で、受信メッセージにも incident にも紐付かないターンを数える）。
  - fault injection で、watcher・AI 呼び出し・窓口のいずれを途中で kill しても、通知の二重送信や未解決 incident の取りこぼしが起きない。
  - 既知の誤検知 fixture と段階 1 の実績で、検知遅延・見逃し・誤った終了判定が今より悪くない。
  - watcher を kill すると、heartbeat の鮮度切れが attention watcher 経由で人間に届く。
- 戻し方: `monitoring_owner=llm` に戻して `/loop 3m` を再武装する。完了受領と curate の状態は段階 2 の間もディスパッチャー Claude が旧ファイルに反映し続けているので、そのまま使える。画面ハッシュと idle streak は watcher が state.db に持っていて旧ファイルには無いので、戻した直後は 0 から観測し直す。これは今の手順書が状態ファイルを失ったときの挙動と同じである（worker-monitoring.md Step 5 (b)「再起動時の挙動」）。

### 段階 3: spawn / close の移管（範囲を広げた場合だけ）

- やること: DELEGATE の受理から spawn 儀式、状態記録、派遣完了ゲートまでと、CLOSE_PANE の close 判定を watcher に移す（§2.2 / §2.3）。振り返りは窓口の `/org-retro` に寄せる。ultracode の許可は送信計画の構造化フラグで渡す。
- 受け入れ条件: 連続 20 件の派遣で `tools/spawn_gate.py audit` の未検証が 0 件。起動プロンプトの判定は `spawn-prompt-step` の 1 手だけを送る（盲目 Enter が無い）。
- 戻し方: DELEGATE / CLOSE_PANE の宛先をディスパッチャー Claude に戻す。

### 段階 4: LLM ペインの退役（範囲を広げた場合だけ）

- やること: ディスパッチャーペインと `/dispatcher-handover` / `/dispatcher-resume` スキル、`.hooks/check-loop-directive.sh` を退役させる。ペインを目で見る代わりの観測面は、watcher のログと既存のダッシュボードにする。
- 受け入れ条件: 段階 3 の実績で、ディスパッチャー Claude が受けたメッセージが 0 件の期間が続く。
- 戻し方: `/org-start` のディスパッチャー spawn を戻す。

## 6. 範囲の選択肢

| | 監視だけ（段階 1〜2） | spawn / close まで（段階 1〜4） |
|---|---|---|
| 得られるもの | 周期的な AI ターンがほぼ無くなる。2026-07-18〜07-25 の実測では、3 分の周期実行が 1,041 回、出力 token が 567,839、cache read が約 14 億 token あった。検知規則がテストで固定され、「宣言しただけで武装していない」「サイクルの中身が省かれる」型の事故が構造的に起きなくなる | 左に加えて、ディスパッチャーペイン、handover / resume のスキル、`/loop` 正文固定フックを捨てられる |
| 費用 | watcher 本体、§2.1 の (b) の行、state.db の migration、runtime 側の公開クライアントと heartbeat reader | 左に加えて、spawn 儀式（起動プロンプトの判定ループ、登録待ちの三値判定、ultracode のフラグ化）、close 判定、振り返りの窓口への移設 |
| 危険 | push が効かないときに DELEGATE を受け取れない（段階 2 のキュー滞留監視で塞ぐ）。renga 面は今の `/loop` のまま残り、二系統になる | spawn は生きた委譲経路そのもので、壊れると派遣がすべて止まる。過去の spawn 事故（承認 Enter を送らずに完了と報告した 2026-08-18 の 2 件、盲目 Enter でワーカーが終了した 2026-09-25）はどれも儀式の中で起きている |
| AI ターンの削減 | 周期分はほぼすべて | 追加の削減は依頼 1 件あたりの数ターンだけ。周期的な費用ではない |

**推奨は「監視だけ」**。費用の大部分は周期ターンであり、それは監視だけで取り切れる。spawn / close を移す利点はペインと引き継ぎ機構を捨てられることだが、引き継ぎは監視状態が抜けた段階 2 の後なら軽くなる。spawn 儀式は事故の多かった区間なので、段階 2 の実績が出るまで動かさない。
