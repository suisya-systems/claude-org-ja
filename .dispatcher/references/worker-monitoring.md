# ワーカーペイン監視 — 詳細

`.dispatcher/CLAUDE.md` の「ワーカーペイン監視」エントリポイントから参照される本体仕様。全判定が従う観測の原則、監視ループ 1 サイクルの手順、stall 検出、secretary relay gap 検出、設計メモ、cadence 設定の根拠を含む。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `broker` / opt-in `renga`）**: 本ファイルの監視手順は **既定 `broker`** で書いてある（`ORG_TRANSPORT` 無設定ならそのまま従う。既定挙動）。`ORG_TRANSPORT=renga`（opt-in）では完全修飾名が **`mcp__org-broker__*` → `mcp__renga-peers__*`** に置換される（引数形・cursor / timeout セマンティクスは同一）。監視ループで輸送依存に**意味が変わる**のは次の 2 点:
> 1. **受信モデル（push 一次 = `claude/channel` / pull フォールバック）**: Step 2 の `check_messages` でワーカー自己報告を受ける流れはどちらの transport でも**同じツール名・同じ drain セマンティクス**で動く。既定 broker は **push 一次**に設計済（runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9）で、各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が `notifications/claude/channel` で本文を idle セッションへ注入する（ワーカー報告は channel push が一次）。**pull はフォールバック層**: sidecar 不在 / unhealthy / channel 非対応ペイン時は、ディスパッチャーが `/loop 3m` 各サイクルで能動的に `check_messages` する（§9.6 読み替え表の dispatcher cadence。ナッジが出れば契機になりうるが idle を起こさないため能動 poll が受信の正路。既存「ナッジを見たら `check_messages`」prose は撤回せずこの fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）ではワーカー報告が `<channel source="renga-peers" …>` として in-band で push される。**ただし `/loop 3m` 監視ループ自体は push 一次でも廃止しない** — Step 1 の `poll_events`（pane lifecycle）に依存し、かつ Step 2 の `check_messages` は push 失効時のフォールバック drain として機能するため。Step 5.2 のワーカーへの self-healing nudge / secretary への informational も既定 broker では channel push 一次・nudge + `check_messages` フォールバックの経路になる。
> 2. **`poll_events` の生成元（native push → 差分 reconcile 合成）**: 既定 broker は端末 backend（tmux/WezTerm）に native push が無いため、**`list_panes` 差分 reconcile で `pane_started` / `pane_exited` / `events_dropped` を合成**する（設計 §6、exactly-once `pane_exited` / overflow 時 `events_dropped` + `list_panes` リコンサイル回復）。`ORG_TRANSPORT=renga`（opt-in）ではペイン lifecycle を native に push する。`poll_events` の API 形・最小 vocabulary・cursor / 30s cap は同一なので Step 1 / Step 3 の手順は不変。`events_dropped` 時に `list_panes` で突き合わせる Step 3 の保険はどちらも同じ（既定 broker の正準経路）。
>
> エラーコードは shared codes（`[pane_not_found]` / `[shutting_down]` / `[io_error]` / `[app_timeout]` / `[internal]` 等、Step 4 のハンドリング参照）に加え、既定 broker は `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`(= adapter_unavailable) / `[nudge_failed]` / `[peer_not_found]` を返しうる（未知コードは default-branch）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次再設計）/ `docs/design/ja-migration-plan.md` §5.2(ii) / §6 / §8。broker 実走（dogfood）は Issue G スコープで本ファイルの既定経路ではない。

<a id="observation-principle"></a>
### 観測の原則 — 観測不能と不在を区別する

**本ファイルの全判定 (Step 1 / 3 / 4 / 5 / 5.1 / 5.2 / 5.3、および今後追加される判定) はこの節を参照する。**
2026-08-08 の稼働で誤検知が 3 種 (ペイン消失を異常として申告 / `STALL_SUSPECTED` 2 件 / 完了確認の重複問い合わせ 3 回) 同時に出たが、根は 1 つで「**自分の観測手段が届いていない状態を、対象に異常があると解釈していた**」ことだった (Issue #869)。3 箇所を個別に直すのではなく、原則をここに置いて各判定がこれを参照する。

#### (P1) 観測できないことは、起きていないことの証拠にならない

観測手段が届かないとき、対象の状態を**推測しない**。「見えない」「返事が無い」「痕跡が無い」はいずれも**自分の観測面の状態**であって、対象の状態ではない。観測手段が使えないと分かったら、対象の状態を判定せず **観測不能** として報告する ((P4) の語彙)。

同じ規律は sibling の手順書に先例がある: CLOSE_PANE の完了報告ゲートは「dispatcher のメッセージキュー (`check_messages` の戻り) に完了報告が無いことは『システム上に存在しない』ことを意味しない」を存在理由に持つ ([`.dispatcher/references/pane-close.md`](pane-close.md) 1 の「理由」)。本節はそれを監視ループの全判定へ一般化したもの。

#### (P2) 異常の申告は、独立した複数の観測面が一致したときに限る

1 面だけの否定的観測 (見えない / 痕跡が無い / 変化が無い) は **候補** どまりで、それ自体は申告理由にならない。窓口へ異常を申告する前に、独立した別の観測面で裏を取る:

- 面が**一致** → 申告してよい (従来どおり窓口へ報告する)
- 面が**食い違う** → 異常ではなく **観測不能** として扱う (片方を採って断定しない)
- 裏を取る面が**取れない** → 同じく **観測不能**。「取れなかったので残った 1 面だけで断定する」は (P1) 違反

ここでの「独立」は観測**経路**が別ということ。同じ `list_panes` 応答を 2 通りに読み替えたものは 1 面である。

#### (P3) 観測面ごとの証拠能力

各判定はこの表を根拠に「何を証拠にしてよいか」を決める。**右列は「その観測からは言えないこと」であり、右列の事象を左列の観測から結論してはならない。**

| 観測 | そこから言えること | 言えないこと (誤読の型) |
|---|---|---|
| `list_panes` にペインが在る | 呼び出しが解決したタブにそのペインが在る | — |
| `list_panes` からペインが消えた | 呼び出しが解決したタブに**見えない** | **ペインが終了した**とは言えない。`list_panes` の可視範囲は current tab のみで、他タブのペインは出ない ([`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) §1.5 "Visibility scope")。フォーカスが別タブに移ると前タブのペインは MCP 呼び出しから見えなくなりうる (同 §4.3 "Visibility consequence") |
| `[pane_not_found]` が返った | 呼び出しが解決したタブにその名前のペインが無い | **ペインが終了した**とは言えない。cross-tab addressing はこの code を返す契約なので (同 §4.2 "All pane-addressed operations … Cross-tab addressing returns `pane_not_found`")、**フォーカスが別タブにある間に返る `[pane_not_found]` は正常であり、異常ではない** |
| `poll_events` に `pane_exited` が在る | そのペインは終了した (close 成功ごと / crash ごとに exactly once、同 §3.1 minimum vocabulary) | — |
| `poll_events` に `pane_exited` が無い | 通常の終了経路を**観測していない** | **ペインが生きている / 死んでいる**のどちらも言えない。cursor 消失や `events_dropped` の区間は取りこぼしうる (同 §3.1 "Cursor-loss recovery" は BEST-EFFORT + reconciliation)。同区間に `events_dropped` を受けていたら、この面は**観測不能**として扱う |
| `list_peers` に居る | その peer は存在し、messaging で到達しうる | `list_panes` から見える / pane-control 操作が効く、とは言えない (列挙面 §2.2 と可視面 §1.5 は別サーフェスで、両者を突き合わせる規律は同 T-§2.2 "Deliberate asymmetry with `list_panes`" に明文化されている。status: RATIFIED 2026-08-09) |
| `inspect_pane` の入力欄が空 | **何も言えない** | **idle とは言えない**。modern Claude Code は作業中も空の composer を出したままにする (2026-08-08 に renga 側で実ペインを採取して確定)。旧実装の `(target_line + cursor)` 単点比較を全可視行の content hash へ置き換えた根 (Step 5 (b)) と同じ誤りである |
| フッタに新形式 active spinner (`{glyph} {Verb}… (Xm Ys · … · esc to interrupt)`、正準形は `tools/inspect_pane_state.py` の `_NEW_SPINNER_RE`) が在る | ターンが進行中 (**busy**) | — |
| 同 spinner が**無い** | — | **idle とは言えない**。ステータス行は出力バーストのタイミングでしか再描画されず、長い単一ターンの最中は表示が固まって見える (Step 5 (b-fp) 型 2 の実測) |
| events テーブルに worker→secretary 痕跡が無い | 自分が読んだ台帳のその窓に行が無い | **worker が報告していない / 止まっている**とは言えない。worker の報告は**ターン境界**で出るので、1 ターンが長い作業 (実装 → セルフレビュー → codex ゲートを 1 ターン内で連続実行) の最中に痕跡が無いのは**正常** |

#### (P4) 観測不能の報告語彙

観測面が使えないと判定したら、対象の状態を推測した通知 (`WORKER_PANE_EXITED` / `STALL_SUSPECTED` / `PANE_OUTPUT_WITHOUT_PEER_MSG` 等) を**送らず**、監視手段が劣化した事実だけを 1 行で報告する。窓口が受け取るのは「対象が異常だ」ではなく「こちらが見えていない」である:

```
mcp__org-broker__send_message(to_id="secretary", message="
  OBSERVATION_UNAVAILABLE: {対象} の {観測面} が使えません ({理由。例: list_panes に自分以外の org のペインが 1 つも出ない = 別タブを解決している / inspect_pane が [io_error]})。
  対象の状態は判定していません。{フォールバックがあれば: peer 経路 (list_peers + events テーブルの報告痕跡) で監視を継続します}。
")
```

journal は既存 catalog を再利用する (新 event 名は導入しない。Step 5 (f)「soft-note を残す意味」と同方針):

```bash
# 劣化を観測したサイクル (通知の有無に関わらず)
bash ../tools/journal_append.sh anomaly_observed source={面} worker=worker-{task_id} kind=observation_unavailable confidence=n/a note={理由}
# 通知送信成功時
bash ../tools/journal_append.sh notify_sent source={面} worker=worker-{task_id} kind=observation_unavailable confidence=n/a
# 当該面が回復したサイクル (次の劣化で再通知できるようにする)
bash ../tools/journal_append.sh anomaly_observed source={面} worker=worker-{task_id} kind=observation_recovered confidence=n/a
```

- **再通知 cadence は「面ごとに劣化区間あたり 1 回」**: 観測不能は数サイクル継続するのが普通なので、Step 4 (e) の 30 秒窓 (毎サイクル抜ける) を使わない。de-dup キーは **`(worker, source, kind=observation_unavailable)`** で、`source` (どの観測面か) を**必ず含める** — キーを `(worker, kind)` にすると、先に劣化した面の通知が後から劣化した別の面 (例: `inspect` の次に events テーブル) を覆い隠し、2 つ目の劣化が無期限に未報告になる。同キーの `notify_sent` が既にあり、それ以降に**同じ `source`** の `observation_recovered` を書いていない間だけ再通知を抑える。判定を保留している同じ 1 事実で窓口の受信箱を埋めないため
- **観測不能サイクルでは state を進めない**: 画面を観測できなかった worker について `worker-idle-state.json` の record を更新しない (`idle_streak_cycles` を加算せず、`last_check_ts` / `last_visible_content_hash` も書き換えない)。`last_content_change_ts` の意味は「最後に画面が idle と**確認できた**時点」(Step 5 (b) 更新規則) なので、確認できていないサイクルで時計を進めると、後続の Step 5 / Step 5.2 が**観測していない区間を「静止していた」と読む**
- **観測不能は「異常なし」ではない**: 判定を保留しただけなので、次サイクルで面が回復したら通常評価に戻す。回復しないまま worker の消息が分からない状態が続く場合も、やることは (P5) のとおり報告の継続であって復旧ではない

#### (P5) 精度が上がっても、行動は増えない

本節は**異常と申告する条件を厳しくするだけ**で、申告後の振る舞いも dispatcher の権限も一切変えない。次を明示する:

- (P1)(P2) を通って「異常ではない」と判定したときの正しい行動は **通知を出さない (観測を続ける)** ことであって、**自分で直すことではない**。「異常ではないと切り分けられたのだから自分で復旧してよい」という読み方は**誤り**
- ペインの再 spawn / レイアウト再構築 / 承認・拒否の代行を dispatcher が自己判断で行わないのは従来どおり (本ファイル 6、[`.dispatcher/CLAUDE.md`](../CLAUDE.md) 「ディスパッチャーが自動で承認・拒否することはしない」)。`close_pane` も、**既に明文化されている 3 経路でのみ**呼ぶ — 窓口の `CLOSE_PANE` 指示 ([`.dispatcher/references/pane-close.md`](pane-close.md))、Step 5.3 (a) の curate 完了受領 (`CURATE_DONE` / `CURATE_SKIPPED` / `CURATE_ERROR`)、Step 5.3 (b) の curate timeout / hard cap 打ち切り。**本節はこの 3 経路を一切変更しない**し、ここに worker ペインの監視判定を結論として自分で閉じる経路は無い (本節が新設した観測不能判定も close の理由にならない)。**「観測不能」と判定した対象にも同じ**で、見えないペインを「壊れているかもしれないから作り直す」のは禁止
- この禁止は実績で担保されている: 2026-08-08 にペイン消失を観測したケースは、**人間が別タブで開いていた生きているセッション**だった。`list_panes` からの消失を「消えた = 死んだ」と解釈してペイン再構築を始めていれば、人間の作業セッションを壊していた。3 件の誤検知が実害ゼロで済んだのは、dispatcher が**観測結果だけを報告し、自己判断で復旧作業・再 spawn・`close_pane` をしなかった**からである (Issue #869)
- 判定が付かないことは**行動を増やす理由にならない**。安全側は常に「報告して止まる」

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
   # (1) cursor の session 束縛を確立する (下記「cursor は backend session に束縛する」)
   binding = <現サイクルの session_key / session_key_source。導出規則は下記>
   saved   = read_json(".state/dispatcher-event-cursor.txt")   # 非 JSON / 読めない → null

   # (2) 束縛が一致したときだけ前 cursor を since に使う
   if saved != null and binding.session_key != null and saved.session_key == binding.session_key:
       since = saved.next_since
   elif (binding.session_key_source == "transport_has_no_server_info"
         and saved != null
         and saved.session_key_source == "transport_has_no_server_info"):
       since = saved.next_since          # broker 面: 束縛を確立する surface が無いので従来どおり
   else:
       since = <省略>                     # 別 session の連番。使うと全ての pane_exited が空振りする

   result = mcp__org-broker__poll_events(
       since=since,
       timeout_ms=5000,
       types=["pane_exited", "events_dropped"]
   )
   # cursor は session 束縛と揃えて .state/dispatcher-event-cursor.txt に保存し、次サイクルで使う。
   # ただし unbindable のサイクルは書き換えない (有効だったかもしれない束縛を自分で潰さない)
   if binding.session_key_source != "unbindable":
       write_file(".state/dispatcher-event-cursor.txt", json({
           "version": 1,
           "session_key": binding.session_key,
           "session_key_source": binding.session_key_source,
           "next_since": result.next_since,
       }))
   ```
   - 初回 (cursor ファイルが無い/空) は `since` 省略で「今以降」セマンティクス（過去イベントを flood しない）
   - 2 サイクル目以降は **束縛が一致した session に限り** 前回の `next_since` を使って idempotent resume（重複通知なし）。**同一 session 内の挙動は従来と同一**で、変わるのは session を跨いだときだけ
   - **cursor は backend session に束縛する (MUST)**: `next_since` は **daemon session 内のカウンタ**で、restart を跨いで復元されない。一方 cursor **ファイル**は restart を跨いで残るので、束縛せずに使うと「前 session の大きい連番」で poll し続け、**新 session のイベントが全て cursor より手前に落ちて永久に空振りする**（2026-08-09 実測: renga 2.0.0 → 2.1.0 再起動後、`.state/dispatcher-event-cursor.txt` = 64 に対し `since` 省略の `next_since` = 7。57 件以上積まれるまで全ての `pane_exited` が空振りし、最初の worker close で終了検知が無言で死んだ）。契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec (R3) も同じ事故を「a backend restart invalidated the `poll_events` cursor and `pane_exited` has no replay」と記録している。renga 2.1.0 の `mcp__renga-peers__server_info` ツール記述はこの規律を「`server.session_id` identifies the running renga PROCESS INSTANCE and changes on every restart … store `(session_id, pane_id)` together and discard the pane id when the session_id you read back differs」と pane id について書いており、**cursor は同じ理由で同じ規律に従う**（契約 T-§4.2-id が pane id に課す (O1) session provenance の cursor 版）。縮退の向きが安全側（ゲートは回り続ける）で症状が正常に見えるため、束縛と下記の fail loud をセットで置く
   - **`session_key` の導出**（renga 面のみ `server_info` を 1 回引く。**この tool 名は輸送層の機械置換の対象外で `mcp__renga-peers__server_info` を literal で書く (MUST)** — (3-a-5) 末尾の同型注記と同じ理由に加え、**broker 面には当該 tool が存在しない**ため（dispatcher の tool 集合は [`tools/transport.py`](../../tools/transport.py) の descriptor が SoT で、`broker` 側に `server_info` は無い）。**(3-a-5) の session provenance 照合はこのサイクルで引いた同じ応答を再利用する**ので往復は増えない）:
     - `status == "connected"` かつ `server.session_id != null` → `session_key = server.session_id` / `session_key_source = "server_info.session_id"`（**正確な形**。restart ごとに変わる process instance 識別子）
     - `status == "connected"` だが `server.session_id == null`（当該フィールドを返さない版） → `session_key = "pid={server.pid};endpoint={server.endpoint}"` / `session_key_source = "pid_endpoint"`（**近似**。OS は pid を再利用しうるので (3-a-5) 第 1 面と同じ弱点を持つ）
     - `status != "connected"`（`detached` / `unreachable`）/ 呼び出し自体が失敗 → `session_key = null` / `session_key_source = "unbindable"`。**provenance を確立できていないので保存済み cursor を使わず、かつ cursor ファイルを書き換えない (MUST)**。このサイクルは `since` 省略で poll するだけにして、**lifecycle 面が観測不能だったサイクルとして扱う**（(P4) を `source=lifecycle_event` で報告し、次サイクルで再評価する）。**破棄扱いにしない**理由は 2 つある: (i) 束縛を確かめられないだけで前 session の cursor が壊れたとは限らず、上書きすると**確かに有効だった cursor を自分で捨てる**ことになる（`server_info` が引けない状況は多くの場合 backend 自体に届いていない状況で、そのとき `poll_events` も同様に届いていない）; (ii) 上書きすると保存値も `null` になり、**「捨てた側」と「今回」の区別が消えて**下記 de-dup がエピソードを識別できなくなる。したがって cursor の破棄 = fail loud の対象は、**実在の束縛と実在の現束縛が食い違った形**（`session_mismatch` / `transport_mismatch` / `legacy_format`）に限られる
     - **broker 面**（`server_info` が無い） → `session_key = null` / `session_key_source = "transport_has_no_server_info"`。**保存側の `session_key_source` も同じ値だったときに限り、読み側は従来どおり保存済み cursor をそのまま使う**（broker には backend restart を観測する surface が無く、ここで reset に倒すと毎サイクル cursor を捨てて逆に取りこぼしが増える。**保存側が別の source だった場合 = `ORG_TRANSPORT` を跨いで同じファイルを引き継いだ場合は、renga の連番を broker へ渡すことになるので通常どおり破棄する** — 別 backend の cursor は別 session の cursor より安全ではない）。**broker 面ではこの事故は未解決のまま残る** — 解消には broker 側に session 識別子を返す surface が要る（既知の穴として明示的に持つ。`unbindable` と別値にしてあるのは、「surface が無い」と「surface はあるが今引けなかった」を読み側が取り違えないため）
   - **束縛が一致しなかったサイクル = cursor 破棄。黙って捨てない (MUST, fail loud)**。**適用条件は「捨てるべき cursor が実在したこと」**: cursor ファイルが**存在して中身が空でない**サイクルだけが本手順の対象で、**初回（ファイルが無い / 空）は破棄ではなく開始なので報告しない**（従来どおり `since` 省略で始めるだけ）。ファイルが在って JSON として読めないケースは「壊れた cursor を捨てた」なので対象に**含める**（`legacy_format`）:
     **順序は Step 4 (e) の通知シーケンスと同型にする（観測記録 → de-dup 判定 → 送信 → 送信成功時に `notify_sent`）。`notify_sent` を送信前に書いてはならない (MUST NOT)** — cursor は手順 1 で既に張り直されており次サイクルは一致してこの枝に入らないので、送信前に `notify_sent` を書くと送信が失敗した回の報告が**恒久的に失われる**（台帳も成功と読めてしまう）:
     1. `since` を省略して poll し（上記 (2)）、得た `next_since` を現 `session_key` と揃えて保存し直す（次サイクル以降は通常運用に戻る）
     2. **観測記録** (常に、送信可否に関わらず): journal に `anomaly_observed` を 1 行追記する（**新しい event 名は導入しない**。`kind` は 2026-08-09 の実走が既に使った `stale_event_cursor` を再利用する — 契約 T-§4.2-place-rec (R3) が同名で記録している）。**`session_key` を payload に載せる** — 下記 de-dup キーはこの値で引くので、載せないと 2 回目以降の restart で「同じ `(source, kind)` が既に在る」と読めてしまい、fail loud が恒久的に沈黙する（`payload_json` は自由形の JSON なので追加キーを取れる: [`tools/state_db/schema.sql`](../../tools/state_db/schema.sql) の `events.payload_json`、消費側の unknown-field 許容は [`docs/journal-events.md`](../../docs/journal-events.md)）。確立できなかったサイクルは literal `none` を入れる:
        ```
        bash ../tools/journal_append.sh anomaly_observed source=event_cursor kind=stale_event_cursor confidence=n/a session_key='{session_key|none}' discarded_session_key='{saved.session_key|none}' note={reason}
        ```
        **`discarded_session_key`（捨てた側の束縛）も載せる (MUST)**: `session_key` だけを de-dup キーにすると、`unbindable` や broker 面のように `session_key = none` が繰り返し現れる形で、**2 回目以降のエピソードが 1 回目の行に吸収されて恒久的に黙る**（`none` はエピソードを識別しない）。捨てた側の値を併記すると、正常復帰を挟んだ次のエピソードは「実在の束縛を捨てた」形になってキーが変わり、必ず 1 回報告される。読めなかった cursor は `none` を入れる
        **`session_key` の値は必ずクォートする (MUST)**: `pid_endpoint` 形は `pid=...;endpoint=/run/...` のように `;` を含み、素で置換するとそこでコマンドが切れて **pid 部分しか記帳されない**（endpoint 面を失った key は de-dup の識別力が落ち、pid 再利用時に後続の restart を握り潰す）。`server_info.session_id` 形でも同じ規律で書く（値の形に依らずクォートを外さない）
        `{reason}` は `session_mismatch`（保存値と現 `session_key` が違う） / `transport_mismatch`（`session_key_source` が保存値と違う = 輸送層を跨いだ） / `legacy_format`（JSON として読めない旧形式 = 束縛の無い cursor） のいずれか。**`unbindable` はここに含まれない** — 上記のとおり cursor を書き換えず破棄もしないので、観測不能サイクル ((P4)) として扱う
     3. **de-dup 判定（送信の前に引く）**: journal に **`event == "notify_sent"`** かつ **`(source=event_cursor, kind=stale_event_cursor, session_key, discarded_session_key)`** 一致の行が既に在れば**送信しない**（手順 2 の `anomaly_observed` は de-dup キーに含めない — Step 4 (e) と同じ規約）。**`(source, kind)` だけで引いてはならない**（2 回目以降の restart が既存行に吸収されて黙る）。**`discarded_session_key` を落としてもならない**（`unbindable` / broker 面で `session_key = none` が繰り返す形が同じ理由で黙る）。**この 4 つ組が「エピソードごとに 1 回」を満たすのは、破棄が `session_mismatch` / `transport_mismatch` / `legacy_format` の 3 形に限られているから**である（`unbindable` は破棄しないので入ってこない）: いずれの形でも捨てる側・今回側の両方が**実在の値**で、次に破棄が起きるのは実際に別の restart / 別の輸送層を跨いだときだけなのでキーが必ず変わる。逆に `unbindable` を破棄扱いにすると両側が `none` に潰れてエピソードを識別できなくなる — これが上記で cursor を書き換えない設計上の理由でもある
     4. **通知送信** (手順 3 を通過した場合): 窓口へ 1 行送る（`mcp__org-broker__send_message(to_id="secretary", ...)`）:
        ```
        EVENT_CURSOR_RESET: poll_events cursor を破棄し現在時点から再開しました (理由: {reason})。backend 再起動を跨いだ cursor は連番が復元されず、そのまま使うと以後の pane_exited が全て空振りします。破棄した区間に起きたペイン終了は回収不能です (poll_events に historical replay は無い)。同一タブのワーカーは列挙の突き合わせで従来どおり退役を確定できますが、背景タブ配置のワーカーは終了確定手段が pane_exited だけなので indeterminate として上げます。その退役確定は、以前「在」と観測した数値 id が後続の list_peers から消えたこと (契約 T-§2.1 step (3)) を窓口が reconcile する経路にフォールバックします。
        ```
     5. **`notify_sent` 記録** (送信成功時のみ):
        ```
        bash ../tools/journal_append.sh notify_sent source=event_cursor kind=stale_event_cursor confidence=n/a session_key='{session_key|none}' discarded_session_key='{saved.session_key|none}'
        ```
        （2 フィールドとも手順 2 と同じ値・同じクォート規律で書く。片方でも欠けると de-dup の照合対象が壊れる）
     6. **未達の持ち越し (MUST)**: 送信が失敗した回は手順 5 を書かないので、台帳に **`anomaly_observed` が在るのに対応する `notify_sent` が無い `(session_key, discarded_session_key)`** が残る。**毎サイクル Step 1 の冒頭でこの形を 1 回引き、在れば手順 4→5 だけを再実行する**（手順 1・2 は再実行しない。cursor は既に張り直され、観測記録も既に在る）。この再試行が唯一の持ち越し手段である — cursor 側は既に一致しているので、mismatch 枝は二度と発火しない
     7. **破棄した区間の `pane_exited` は回収できない**（`poll_events` は初回省略時「今以降」semantics で historical replay を持たない — 契約 §3.1）。**新しい分岐も per-worker の reset マーカーも足さない**（Step 3 の判定は変えない）。何が失われ何が失われないかは worker の配置で違うので、そこだけを正確に押さえる:
        - **同タブ worker**: 影響は無い。(3-a-2) の退役確定はもともと `pane_exited` に依存しておらず、2 サイクル連続の列挙消失 + 4 行目の裏取りという**独立の証拠**で立つ。cursor 破棄はこの経路を弱めないので、従来どおり dispatcher が `WORKER_PANE_EXITED` を出してよい
        - **`placement == "background_tab"` の worker**: ここが失われる面である。(3-a-5) は列挙からの消失を **unknown であって不在ではない**と扱うので、終了の確定手段は attribution 済みの `pane_exited` **だけ**であり、破棄区間にそれが落ちた worker は 3 行目が続く形 = **indeterminate** として (3-a-4) に載り、(P4) を `source=lifecycle_event` で報告して**窓口の reconcile 判断**に委ねる（既存の解消経路そのままで、新設ではない）。これが契約 T-§2.1 step (3) の 2 つ目の手段 = 以前「在」と観測した数値 id が後続の `list_peers` から消えたことを窓口が突き合わせる形で、2026-08-09 の実走はこの経路で退役を確定させている（T-§4.2-place-rec (R3) の `secretary_reconcile_T2.1_ii_list_peers_vanish`）。**dispatcher 側で自動退役させる新しい経路は作らない**
        窓口への通知本文（手順 4）が「退役確定は窓口 reconcile へフォールバックする」と書くのは**この背景 worker の面**を指す
     8. record に保管済みの `pending_exit_event` ((3-a-5)) は **破棄しない** — 受信済みの確定証拠であって cursor に依存しないため
   - `types=["pane_exited", "events_dropped"]` フィルタで heartbeat / pane_started 等を除外。cursor は filter と無関係に advance するので重複 scan なし
   - `result.events[]` を順に処理:
     - `type == "pane_exited"` かつ `role == "worker"` → **直接通知せず、下記の attribution に回す**（同定できたものだけが `WORKER_PANE_EXITED` になる。capability 形の `poll_events` は他タブのペインの終了も流すので、`role == "worker"` だけで通知すると別 org の同名ワーカーの終了で自タブのワーカーを退役させる）
     - `type == "events_dropped"` → journal helper (`../tools/journal_append.sh`) 経由で `.state/state.db` の `events` テーブルに drop 件数を記録（監視が追いついていないシグナル）
     - それ以外（ディスパッチャー/キュレーター/窓口の終了） → 誤ってワーカー終了として扱わない
   - **filter 不一致イベント到着で long-poll 早期終了する仕様**なので、空応答時は次サイクルで再 poll（cursor 保持で重複なし）
   - **どの追跡対象のイベントかを同定してから通知する (attribution)**: `pane_exited` の**受信**が終了の確定証拠であること ([`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) §3.1: close 成功ごと / crash ごとに exactly once) は変わらない。変わるのは**そのイベントがどの追跡対象のものか**の同定方法で、同契約 T-§3.1 は「a harness MUST NOT match a lifecycle event to a tracked pane by `name` alone unless it has independently established that the pane is in the caller's tab」「Matching by name across an all-tab world can retire a live local pane on another tab's exit event」と書く:
     - **第一に控えてある数値 id で照合する**。契約 T-§3.1 は `pane_exited` が **`id`**（「the pane identifier, in the same id space as `list_panes`.`id`」）と、peer を持つペインでは **`peer_id`**（「the peer identifier, in the same id space as `list_peers`.`id`」）を**契約されたフィールド名で**載せると定める。突き合わせ先は 2 つあり、**どちらか一方でも一致すれば同定は確定する**:
       - イベントの pane 識別子 ↔ Step 3 (3-a-3) で控えた `tracked_pane_id`
       - **`placement == "background_tab"` の worker では、突き合わせ先を `bound_pane_id` (pane 面) と `bound_peer_id` (peer 面) に読み替える** ((3-a-5))。背景 worker では `tracked_pane_id` が構造的に `null` で、`same_tab_peer_id` は意味が違うので使わない。**背景 worker の未同定イベントは `name` 一致へフォールバックせず、そのまま attribution 未確定として扱う** (全タブ世界で `name` は一意でない — T-§3.1)
       - **ただし背景 worker の同定は、この Step 1 では確定させない (MUST)**: 束縛した id は **backend session に閉じた識別子**で、daemon restart 後は同じ数値が別の生きたペインに再発行される (契約 T-§4.2-id)。Step 1 は Step 3 の session provenance 照合 ((3-a-5)) より**前**に走るので、ここで id 等値だけを確定証拠として扱うと、**restart 後の無関係なペインの終了イベントで追跡中の worker を退役させる**。したがって背景 worker については、id が一致したイベントを **未同定の `pane_exited` 候補として同じサイクルの Step 3 へ持ち越し**、(3-a-5) の **session provenance (第 1 面) が通ったときに限り**同定を確定する (終了したペインは列挙から消えているのが正常なので、attribution に第 2 面 = identity 再照合は要求しない。同項の「2 面が gate するものは違う」)。
       - **第 1 面が通らなかったサイクルでは、候補を捨てずに `pending_exit_event` へ書く (MUST)**: `pane_exited` は close / crash ごとに **exactly once** で ([`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) §3.1)、cursor は本サイクルで既に advance しているので**次サイクルに同じイベントを読み直す手段は無い**。候補を同一サイクル内でしか持ち越さないと、`server_info` が一時的に引けない / `detached` を返したサイクルにたまたま終了イベントが来た worker は、以後どのサイクルでも終了を確定できず (列挙からの消失は意図的に unknown に倒すので)、**恒久的に active のまま滞留する**。そこで当該サイクルでは `pending_exit_event = {pane_id, peer_id, observed_at}` を record に書き、`WORKER_PANE_EXITED` は送らず (P4) の観測不能報告 (`source=session_provenance`) に落とす。次サイクル以降、(3-a-5) の第 1 面が通ったサイクルで **`pending_exit_event` を取り出して attribution を確定する** (新しい観測は要らない — 保管してあるのは既に受信済みの確定証拠そのものである) — これは同タブ worker の「未同定イベントを Step 3 (3-a) へ持ち越す」既存の扱いと同型で、新しい経路ではない
       - イベントの peer 識別子 ↔ 同 (3-a-3) の `same_tab_peer_id`。**`list_panes` 側が使えないとき (resume 直後 / フォーカスが別タブ) でもこちらは効く** — T-§3.1 が `peer_id` を REQUIRED にしている理由がまさに「`list_panes` が一度も見せていない peer を追跡していることがある」ケースだからである
       **フィールド名は canonical を先に、次に alias を見る**: ratified §3.1 は per-event のフィールド名を規定していないので、canonical 名を載せない backend もありうる（pane 側 `ev.id` → `ev.pane_id`、peer 側 `ev.peer_id` → `ev.agent_id`）。**alias を先に読んではならない** — 両方載っている場合に backend 固有 / 古い `agent_id` が canonical 値を隠し、終了したピアが pending に残る（手順の一次参照は [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) の `poll_events` 節）。この照合は**版に依らず適用でき**、T-§3.1 が求める「`name` 単独に依らない同定」を満たす最も厳しい形である。`name` は通知本文の**表示用**にだけ使う
     - **上の id 照合がどちらも成立しなかったイベント** (控えが無い = Step 3 がまだ一度も当該ペインを列挙しておらず §1-2 の「在」確定も無い / イベントが canonical・alias いずれの識別子も載せていない) は `name` 一致しか取れない。**このとき Step 1 では attribution を決めない** — Step 1 が持つのは `poll_events` の応答だけで、版判定の材料 (`list_peers` の列挙) は Step 3 より後にしか来ないからである。**record の欠損を「旧版だから」と読んではならない**: capability 形の backend でも、初回の陽性観測より前 (spawn 直後 / resume 直後) は同じく欠損する。当該イベントは **未同定の `pane_exited` 候補**として同じサイクルの Step 3 (3-a) へ持ち越し、そこで版が確定してから同定する:
       - Step 3 (3-a-2) の評価で列挙を引き、それが **旧版 fallback** と判定された (marker の無い列挙。現行配備の全 backend — `org-broker` を含む — はここ、同 reference §1-2-a 段 5) → **従来どおり `name` 一致**で同定し、(3-a-2) の 1 行目 (終了確定) に入れる。同一タブ内の `name` 一意性は契約 §1.8 が保証しているので安全であり、**この経路の挙動は変わらない**
       - 同じ評価が **capability 形**と判定した → `name` 単独では同定できない (T-§3.1)。ただし**その評価自体が `list_peers` を引いている**ので、**同じサイクルで id 照合をもう一度試す**: (3-a-2) の 3 列目評価で §1-2 が「在」を返していれば (3-a-3) がそのサイクルに `same_tab_peer_id` を控えるので、**その控えたばかりの id で保留中のイベントの peer 識別子と join を引き直す** (canonical → alias の順は上と同じ)。
         - **一致した → attribution 確定**。控えが「無かった」だけの理由で確定証拠を捨てない。§1-2 が「在」と出ていることは矛盾ではなく登録の遅延なので、(3-a-2) の 1 行目 (終了確定) に入れる (同行の「『在』と出ていても登録の遅延として扱う」)
         - **不一致だった → そのイベントはこの worker のものではない** (別タブの同名ワーカーの終了)。当該 worker については何も起きなかったものとして扱い、通知も state 変更もしない。観測不能報告も出さない (こちらの観測面は劣化しておらず、判定は付いている)
         - **引き直す材料が無い** (§1-2 が「不在」/「unknown」を返して `same_tab_peer_id` が控えられない、またはイベントが peer 識別子を載せていない) → **attribution 未確定**。当該イベントを (3-a-2) の `pane_exited` 列に数えず、`WORKER_PANE_EXITED` を送らず (P4) の観測不能報告 (`source=lifecycle_event`) に落として次サイクルで再評価する (下記 (3-a-4))。別タブの同名ペインの終了イベントで、生きている自タブの worker を退役させないため
       - そもそも Step 3 (3-a) に到達しない (当該ペインが `list_panes` に残っている = 生きている) → 退役の判断自体が不要である。このサイクルで `tracked_pane_id` を控えられるので ((3-a-3))、以後は 1 つ目の id 照合で解決する。**未同定のイベントを根拠に退役させない**
   - 同定できた `pane_exited` 行の `name` (例: `worker-foo`) を通知本文に載せ、`mcp__org-broker__send_message` で窓口に **ペインが閉じた** という事実だけを通知する:
     ```
     WORKER_PANE_EXITED: {name} (id={id}) のペインが閉じました。リコンサイル要。
     ```
     **重要**: これは「ペインが消えた」というライフサイクル事実のみ。タスクの完了判定ではない。
     `pane_exited` の**受信**は終了の確定証拠なので ([`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) §3.1: close 成功ごと / crash ごとに exactly once)、**attribution が確定している限り** [観測の原則](#observation-principle) (P2) の裏取りは不要でこのまま通知してよい (確定証拠であることは変わらず、上の attribution は「誰の終了か」だけを決める)。裏取りが要るのは Step 3 の「event が無いのに `list_panes` から消えている」ケース (下記 (3-a))。
     窓口側は `.state/workers/worker-*.md` を `status=pane_closed` に遷移させ、タスクの完了/未完了は:
       - 直近の org-broker メッセージ履歴 (進捗ログ) を確認
       - `COMPLETED` 報告が届いていれば task 完了扱い
       - 届いていなければ、未完了終了 (ワーカー事故) として扱い、再派遣 or 放棄をユーザーに確認
     のプロセスで判定する
   - `type == "pane_started"` は現状 use case なしなので無視して良い (将来必要になれば追加)
   - `type == "events_dropped"` は drop 件数を journal helper (`../tools/journal_append.sh`) 経由で `.state/state.db` の `events` テーブルに記録 (監視が追いついていないシグナル)
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
     - **受領記録がある問いを再送しない ([観測の原則](#observation-principle) (P1) の適用)**: `completion_reported_at != null` は「secretary が worker の完了報告を受領済み」という**陽性の記録**である。CLOSE_PANE の完了報告ゲート ([`.dispatcher/references/pane-close.md`](pane-close.md) 1) は「『完了報告未着』と結論する前に secretary へ確認する」ためのゲートなので、この記録を持つ task では**その前提が記録によって既に満たされている**: 初回送信 (`{task_id} の完了報告は届いていますか？`) を発行せず、ゲートを acked 相当として通過してよい。答えは既に手元にあり、聞き直しは窓口の受信箱を同じ 1 事実で埋めるだけになる (2026-08-08 に同一 task へ 3 回再送した実誤検知、Issue #869)。逆に **記録が無いことは「未着」の証拠にならない**: `WORKER_COMPLETION_NOTED` は best-effort・非 blocking で取りこぼしうる (次項)。記録が無い場合は従来どおりゲートを回す — ゲートの存在理由そのものが「dispatcher の受信キューに無い ≠ システム上に無い」だからである。
       **手順側の反映先**: 実際に問い合わせを発行するのは CLOSE_PANE の完了報告ゲートなので、同じ skip 条件が [`.dispatcher/references/pane-close.md`](pane-close.md) 1 の「0. 受領記録の確認」に置かれている (初回送信の前に評価する)。本項は監視ループ側でその判定材料 (`completion_reported_at`) を維持する契約で、両者は同じ 1 つの規則の set 側と consume 側にあたる
     - `WORKER_COMPLETION_NOTED` は secretary が **best-effort・非 blocking** で送る (secretary は dispatcher ack を待たない)。push 一次では channel 注入、フォールバック時は本 Step 2 の能動 `check_messages` で受ける。取りこぼしても次の完了受領で再送・再セットされる (record が無ければ Step 5.2 (b)(6) は `completion_reported_at` を欠損 = `null` 扱いで評価するため、取りこぼしは「監視が過剰に働く」安全側に倒れる)

3. **`mcp__org-broker__list_panes` でペイン一覧を取得して突き合わせ**:
   - `poll_events` (Step 1) を見逃した場合の保険 (`events_dropped` 発生時や events 未受信で pane 状態がズレた時)
   - `list_panes` の結果テキストには各 pane の `id / name / role / focused / x / y / width / height` が含まれる
   - **在るワーカーの数値 `id` をその場で控える**: 応答に出た `role == "worker"` の各ペインについて、`.state/dispatcher/worker-idle-state.json` の該当 record の `tracked_pane_id` を本サイクルの `id` に更新する (下記 (3-a-3))。Step 1 の lifecycle event attribution はこの控えを突き合わせ先にする
   - **`placement == "background_tab"` の worker が 1 件でも居るサイクルでは、`mcp__renga-peers__list_peers` を全 worker 共通に 1 回だけ引き、`{数値 id} → レコード` のローカル index を作る** (生存判定用)。**session provenance 照合に使う `server.pid` は Step 1 が cursor 束縛のために既に引いた `mcp__renga-peers__server_info` の応答をそのまま再利用する** (同一サイクル内で 2 度引かない。Step 1 が `server_info` を引くのは renga 面だけだが、背景タブ配置自体が renga 面でしか成立しない ((3-a-5)) ので前提は常に揃う)。背景 worker の生存判定 ((3-a-5)) はこの index と再利用した応答だけを参照し、per-worker の再取得も retry もしない (追加待ち時間 0 分)。背景 worker が 1 件も居なければ (= 通常運用) **`list_peers` の呼び出しは発生しない**。列挙が引けなかった / 旧版形だったサイクルは (3-a-5) の 4 行目 (観測不能) に落とす
   - **同じサイクルで全ペイン index も作る — ただし (3-a-5) の 3 条件ゲートを通ったときだけ (fail closed)**: 上の `list_peers` を引くのと同じ branch で、`mcp__renga-peers__list_panes(tab={"all": true})` を **1 サイクル 1 回だけ**引き、`{数値 pane id} → レコード` のローカル index を作る。**ゲートを 1 つでも欠いたら selector を送らない**（`tab` 引数無しの `list_panes` は Step 3 冒頭のとおり従来どおり引く。**その caller タブのみの応答を全タブ census と解釈してはならない (MUST NOT)** — 詳細と根拠は (3-a-5) の「全ペイン index」節）。この index は **裏取り専用**で、単独では 在 も 不在 も確定させない（同節）。ゲート未通過のサイクルでは index が存在しないだけで、生存判定は従来どおり `list_peers` 面だけで回る（**今日の挙動は 1 行も変わらない**）
   - **背景 worker は下記 (3-a) の裏取りゲートに掛けない**: `list_panes` に出ないのは配置上の定数であって消失ではないので、(3-a-1)〜(3-a-4) ではなく (3-a-5) が評価の owner になる。以下の 2 つの bullet は `placement` が `"same_tab"` (= 欠損含む) の worker だけを対象に読む
   - events 経由で exit を把握していないのに `list_panes` で pane が消えているワーカーがあれば、**下記 (3-a) の裏取りゲートを通してから**、**終了が確定した場合に限り** `.state/workers/worker-*.md` の status を `pane_closed` に遷移させ、Step 1 と同じく窓口に `WORKER_PANE_EXITED` を転送する (task 完了判定は同じ手順で窓口側が実施)。ゲートが **観測不能 / indeterminate / unknown** に倒したサイクルでは status 遷移も転送もせず、当該 worker を監視対象・active のまま次サイクルへ送る (下記 (3-a-4))
   - pane 上限は 16 なので結果は常に小さく、都度 full scan で問題なし

   #### (3-a) 消失の裏取り — `list_panes` の消失を単独根拠にしない ([観測の原則](#observation-principle) (P2) の適用)
   `list_panes` からペインが消えたことは「呼び出しが解決したタブに見えない」以上を意味しない ((P3))。`WORKER_PANE_EXITED` は「ペインが終了した」という**対象の状態**の申告なので、送る前に独立面で裏を取る。

   **(3-a-1) まず「自分の `list_panes` が org のタブを解決できているか」を見る** (同じ応答から読める最初の弁別子):
   - 応答に org の他メンバー (`secretary` / 他の worker / `curator` / watcher 等) が**残っている** → `list_panes` は org のタブを解決できている。消失は当該ペイン固有の事象なので (3-a-2) へ進む
   - 応答に **自分以外の org のペインが 1 つも出てこない** (一斉に消えた) → ペインの一斉終了より「自分の `list_panes` が別タブを解決している」ほうが桁違いに起こりやすい (可視範囲は current tab のみ・フォーカス移動で前タブが不可視になりうる、(P3) の contract §1.5 / §4.3)。これは異常ではなく **観測不能**: `WORKER_PANE_EXITED` を送らず、(P4) の `OBSERVATION_UNAVAILABLE` を 1 回報告し、peer 経路 (`mcp__org-broker__list_peers` + events テーブルの worker→secretary 報告痕跡) にフォールバックして監視を続ける。**`list_peers` の直前に [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md) を Read し、`monitoring-read-only` の分岐を適用する** (同 reference §6 の表 #9)。capability 形かつ未承認なら**このフォールバックから `list_peers` を外し、events テーブルの報告痕跡だけ**で監視を継続する (停止しない・待ち時間 0 分。列挙を生存判定に使うと、別タブの同名ピアを当該ワーカーと取り違える — 予約名 `worker-{task_id}` は別 org の並走タブに同名で実在しうる)。**フォーカスが別タブにある間、pane-addressed 呼び出しが `[pane_not_found]` を返すのは正常であり異常ではない** (contract §4.2)。
     ただし **このフォールバックが常に成立するとは限らない**: ratified 契約では `send_message` (§2.1) も `list_peers` (§2.2) も current tab スコープで、`org-broker` は cross-tab を持たない (§4.2)。フォーカス移動が原因のときは**観測不能の報告そのものが届かず、`list_peers` も対象を列挙できない**ことがある。その場合でも `.state/` のローカル台帳 (journal / events テーブル) は読み書きできるので、(P4) の journal 記録だけは必ず残し、通知は次サイクル以降に再送する。**自分の送信経路が届かないことを「対象が異常だ」の根拠にしてはならない** — (P1) は観測面だけでなく**自分の報告経路にも適用される**

   **(3-a-2) 当該ペイン固有の消失を、独立面で突き合わせる**:

   **責務分割 (本表が owner なもの / owner でないもの)**: 「対象名 `worker-{task_id}` が**自タブに 在 / 不在 / unknown のどれか**」を決める規範の正本は [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md) §1-2 だけで、本表はその**適用結果**を 3 列目に受け取る。本表が owner なのは「`list_panes` / `pane_exited` / 同タブ生存判定 という**独立した観測面をどう突き合わせるか**」— どの組み合わせなら窓口へ申告してよいか、および 2 サイクル継続要件 — の側だけである。判定条件・評価順・三値の定義を本表に**再掲しない** (同 §1-2-e。call site としての分岐は同 §6 の表 #9)。

   | # | `list_panes` | 同区間の `pane_exited` | 同タブ生存の判定 (capability 形かつ承認済み → §1-2 の適用結果 / 旧版 fallback → 従来どおり `name` 一致) | 判定 |
   |---|---|---|---|---|
   | 1 | 消えた | **在り** | 問わず | **終了確定** → `WORKER_PANE_EXITED` を送る (`pane_exited` は exactly-once の確定証拠。「在」と出ていても登録の遅延として扱う) |
   | 2 | 消えた | 無し | **在** | **観測不能** (面が食い違う) → 送らない。(P4) を 1 回報告し次サイクルで再評価 |
   | 3 | 消えた | 無し (かつ同区間に `events_dropped` **在り**) | 問わず | **観測不能** (event 面が取りこぼしている) → 送らない。(P4) を 1 回報告し次サイクルで再評価 |
   | 4 | 消えた | 無し (`events_dropped` も無し) | **不在**。capability 形かつ承認済みのときは、さらに §1-2-c の pane 面裏取り (ii)（以前「在」と観測したレコードの**追跡済み数値 `id`** が後続の `list_peers` から消えた）が取れたときに限る。**旧版 fallback は (ii) を要求せず、不在がそのまま本行に落ちる** | 2 面が一致するので**終了とみなす**。ただし単発サイクルでは断定せず、**連続 2 サイクル同じ形が続いた時点で** `WORKER_PANE_EXITED` を送る |
   | 5 | 消えた | 無し (`events_dropped` も無し) | (**capability 形かつ承認済みのときだけ現れる**) **不在** だが裏取り (ii) が取れない (陽性の「在」履歴が無い = `same_tab_peer_id` が `null`) | **indeterminate** → 送らない。(P4) を 1 回報告し次サイクルで再評価 (下記 (3-a-4)) |
   | 6 | 消えた | 無し | (**capability 形のときだけ現れる** — 承認済みなら §1-2 が unknown を返した場合、未承認なら列挙を破棄するので常にこの行) **unknown** | **観測不能** → 送らない。unknown は **在にも不在にも数えない**。(P4) を 1 回報告し次サイクルで再評価 (下記 (3-a-4)) |

   **本表の `pane_exited` 列に「在り」と数えてよいのは、Step 1 の attribution で当該 worker と同定できたイベントだけである**。同定できないイベントは Step 1 の観測不能経路 (`source=lifecycle_event`) に落ちるので、本表では「無し」として扱う — 同定していないイベントを 1 行目に数えると、別タブの同名ペインの終了で自タブの worker を退役させる経路が本表から再び開く (契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§3.1「a harness MUST NOT match a lifecycle event to a tracked pane by `name` alone」)。**旧版 fallback では同定は従来どおり `name` 一致**なので、この条件は今日の挙動を変えない。

   4 行目 / 5 行目の「裏取り」は §1-2-c が「不在 → lifecycle 断定へ進むには pane 面の裏取りを別に取る」として (i) 同タブの `pane_exited` 観測 / (ii) 追跡済み数値 `id` の消失 の 2 つを挙げるもの。**(i) は本表の 1 行目そのもの**なので、`pane_exited` 無しの行に残るのは (ii) だけである。どちらも取れなければ indeterminate に倒す (契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§2.1: 「If neither is available the harness MUST record the outcome as **indeterminate** and escalate it; it MUST NOT resolve it as "closed"」)。(ii) の突き合わせに使う陽性履歴の保持は (3-a-3)。

   **capability 形かつ未承認のときの 3 列目（`list_peers` 由来の同タブ生存判定）**: (3-a-1) と同じ縮退が本表にも及ぶ。列挙を破棄するので 3 列目は **unknown** として読み、「在」にも「不在」にも数えない。したがって 2 行目（`pane_exited` 無し・在）と 4 行目 / 5 行目（`pane_exited` 無し・不在）はどれも成立せず、**`pane_exited` を観測した 1 行目以外はすべて 6 行目の「観測不能」**に落ちる（送らない・(P4) を 1 回報告し次サイクルで再評価）。これは判定を厳しくするだけで行動を増やさない ((P5)) 側の変化であり、生きているペインを「終了」と誤申告するより安全側。共有 reference §6 の表 #9。

   **capability 形かつ承認済み（§2 の `first_drive` が `recorded`）のとき**: 共有 reference §1-2 の三値判定を `worker-{task_id}` に適用し、その結果を本表の 3 列目に入れる。「在」なら 2 行目（観測不能）、「不在」なら §1-2-c の裏取り (ii) の有無で 4 行目 / 5 行目、「unknown」なら 6 行目。`WORKER_PANE_EXITED` へ進めるのは 1 行目と 4 行目だけで、5 行目 / 6 行目は (3-a-4) の継続動作に落ちる。（判定手順と評価順の正本は §1-2。ここに重ねて書かない）

   **旧版 fallback（marker の無い列挙。現行配備の全 backend — `org-broker` を含む — はここ、共有 reference §1-2-a 段 5）**: §1-2 は適用されない。3 列目は**従来どおり `name` 一致**で「在」／「不在」を読み、**§1-2-c の裏取り (ii) を要求しない**（不在は 4 行目に直接落ちる。5 行目 / 6 行目は capability 形かつ承認済みのときにだけ現れる）。2 サイクル継続要件も (P4) の落とし先も従来のままで、**今日の挙動は変わらない**。

   4 行目で 1 サイクル待つ trade-off: Step 3 はそもそも `poll_events` の取りこぼしを拾う**保険**なので、~3 分の遅延は許容できる。逆に単発サイクルで断定すると、フォーカス移動やタイミング差で生きているペインを「終了」と申告することになる。

   **このゲートは判定を厳しくするだけで、行動を増やさない** ((P5)): 「終了確定」でやることは従来どおり窓口への `WORKER_PANE_EXITED` 転送であり、dispatcher がペインを再 spawn / 再構築することはない。「観測不能」でやることは報告と再評価であって、見えないペインへの介入ではない。

   **(3-a-3) 「在」と観測した履歴を控える** — 裏取り (ii) の突き合わせ先を `worker-idle-state.json` に持つ:

   (3-a-2) 4 行目の裏取り (ii) は「**以前この経路が 在 と観測したレコードの数値 `id`** が後続の `list_peers` から消えた」なので、陽性の観測を保存しておかないと構造的に成立しない (共有 reference §1-2-c:「**在 と判定した時点でそのレコードの数値 `id` を控え、以後の消失判定はその id で行う**」)。控え先は `.state/dispatcher/worker-idle-state.json` の per-worker record で、次の 3 つを **caller-applied フィールド**として持つ:

   | フィールド | 型 | set する場所 | 意味 |
   |---|---|---|---|
   | `same_tab_peer_id` | `null \| 数値` | §1-2 の三値判定が「在」と確定したサイクル (監視サイクル / spawn 時受理の 2 producer。下記) | その「在」レコードの `list_peers` 上の数値 `id`。以後の消失判定 ((3-a-2) 4 行目の (ii)) はこの id だけで行う (`name` で集合所属を判定しない — 契約 T-§2.2「MUST NOT key a lookup, a **set-membership test**, or a reverse map on `name` alone」) |
   | `same_tab_observed_at` | `null \| ISO-8601 UTC` | 同上 | その観測時刻。**消失判定には使わない** (判定は `same_tab_peer_id` の id だけで行う) — 用途は窓口報告・journal 突合の監査値で、「いつの『在』を根拠に (ii) を評価しているか」を人間が追えるようにするためのもの。本ファイル冒頭の時刻規約どおり `date -u +%Y-%m-%dT%H:%M:%SZ` の決定的取得を使う (local-as-Z 厳禁) |
   | `tracked_pane_id` | `null \| 数値` | Step 3 の `list_panes` で当該 worker pane を観測したサイクル | `list_panes` 応答上の数値 `id`。Step 1 の lifecycle event attribution の突き合わせ先 |
   | `placement` | `"same_tab" \| "background_tab"` | **spawn 戻り値を得た直後**に 1 度だけ ([`.dispatcher/references/spawn-flow.md`](spawn-flow.md) 3-2b。Step 4 まで遅らせない — 記録の無い背景ペインは発見不能になる) | この worker のペインをどこに置いたか。**欠損は `"same_tab"` 扱い**で migration 不要。`"background_tab"` の record だけが下記 (3-a-5) の判定に入り、それ以外は (3-a-1)〜(3-a-4) が従来どおり owner |
   | `bound_pane_id` | `null \| 数値` | 同上 (3-2b で spawn 戻り値をそのまま控える) | 背景タブ spawn が返した **session-bound な数値 pane id**。Group A の pane 操作 (`inspect_pane` / `send_keys`) の宛先と、`pane_exited` の pane 面 join に使う。**`tracked_pane_id` とは別フィールド** (あちらは `list_panes` に出た worker にしか値が入らず、背景 worker では構造的に `null` のまま) |
   | `bound_server_pid` / `bound_server_endpoint` | `null \| 数値` / `null \| 文字列` | 同上 (3-2b で `server_info` の `server.pid` / `server.endpoint` を控える) | `bound_pane_id` / `bound_peer_id` を発行した **backend session** の識別子。使用前に毎サイクル照合する ((3-a-5) の session provenance)。**`bound_pane_id` と必ず 3 つ揃えて持つ** — pane id の一意性保証は session に閉じており、daemon restart 後の同じ数値は別の生きたペインを指す (契約 T-§4.2-id)。**監視サイクルは spawn ターンの手元値を持たない**ので、record に書いていない面は照合しようがない |
   | `bound_cwd` | `null \| 文字列` | 同上 (3-2b で spawn に渡した `{workers_dir}/{task_id}` の絶対パス) | (3-a-5) の identity 再照合で **org 束縛**を担う弁別子。予約名は別 org の並走タブに同名で実在するので `name` / `role` だけでは自 org のペインだと言えない (契約 T-§4.2-id の (O2)「bound to the expected organisation」) |
   | `bound_peer_id` | `null \| 数値` | spawn-flow 3-4b の登録ゲートが `bound_pane_id` で受理したあと (Step 4 の該当項で merge。**この 1 フィールドだけが spawn 直後ではなく登録後に書かれる**) |
   | `pending_exit_event` | `null \| {pane_id, peer_id, observed_at}` | Step 1 で id が一致する `pane_exited` を観測したが、そのサイクルで (3-a-5) の session provenance を確立できなかったとき | **確定できなかった終了イベントの保管庫**。`pane_exited` は exactly-once なので、cursor が進んだ後は二度と読み直せない — 同一サイクル内でしか持ち越さないと、`server_info` が一時的に引けないサイクルに終了イベントが来た worker は**恒久的に active のまま残る**。次サイクル以降 provenance が通った時点でここから取り出して attribution を確定する。破棄は attribution 確定時 (record ごと規則 (4) で消える) と、窓口が reconcile して終端状態へ遷移させたときだけ | 背景 worker の毎サイクル生存判定 ((3-a-5)) と `pane_exited` の peer 面 join に使う数値 id。**`same_tab_peer_id` とは別フィールド** (あちらは「自タブに 在 と確定した」意味を持ち (3-a-2) 4 行目の裏取り (ii) の入力になるので、背景 worker の id を入れてはならない — MUST NOT) |

   - **producer は 2 本ある。どちらも「§1-2 が『在』1 件と確定した `list_peers` レコードの数値 `id`」しか書かない**: **`list_panes` の pane id・旧版 fallback の `name` 一致 poll で得た id・縮退中の send-as-probe から推測した値は書かない (MUST NOT)**。誤った陽性履歴は (3-a-2) 4 行目の裏取り (ii) を偽で満たし、`pane_exited` の attribution を別 worker に結び付けて**生きている worker を退役させうる**。**受理条件・三値判定の正本は共有 reference §1-2-c であって本項ではない** (本項が定めるのは控え先と producer の所在だけである)。
     1. **監視サイクル producer**: この監視ループが `list_peers` を引いたサイクル ((3-a-1) の観測不能フォールバック / (3-a-2) の 3 列目評価) に §1-2 が「在」を返したとき。**通常サイクルの Step 3 は `list_peers` を引かない**ので、監視ループだけを回している間に新しく「在」が確定することは無い (呼び出しを増やさない = (P5))。裏取り (ii) はこれで成立する — ペインが消えても peer 登録が残っているサイクル ((3-a-2) の 2 行目) で `same_tab_peer_id` が入り、次サイクルで同じ id が列挙から消えれば 4 行目に落ちる
     2. **spawn producer (配線済み)**: ワーカー spawn 直後の peer 登録待ちが §1-2-c の「登録待ち型は 在 のときだけゲートを開ける」で受理したとき、受理の根拠になった数値 id を控え、**post-spawn の状態記録**([`.dispatcher/references/spawn-flow.md`](spawn-flow.md) Step 4 の該当項) で既存 record へ merge する。**列挙経路は 2 つあるが書き込み口は 1 つである**: spawn-flow 3-4 を実行手順として通る経路と、runtime delegate-plan helper が機械生成した `after_spawn[]` の `list_peers` 待ち (`.dispatcher/CLAUDE.md`) の両方が、同じ post-spawn 状態記録に合流する。**spawn を放棄する場合の始末も同項が定める** (放棄経路は状態記録に到達しないので陽性履歴はそもそも書かれない。**書いた後の record 削除規則は下記更新規則 (4) だけが持ち、spawn 経路は削除条件を足さない** — 「派遣後にタスクを取り止めた」ことを削除の根拠にすると、生きているペインの record を消して `tracked_pane_id` / `same_tab_peer_id` を失わせ、規則 (4) が名指しで禁じている恒久 indeterminate をこちらから作ることになる)。この producer により「ペインと peer 登録が同じ区間で同時に消えた」ケースでも裏取り (ii) が成立するようになり、(3-a-4) の恒久 indeterminate は狭くなる
   - **陽性履歴が無いまま消失したときの着地は indeterminate で正しい**: (ii) が構造的に成立しないので確定証拠は「同タブの `pane_exited` 観測」だけになり、それも無ければ (3-a-2) の 5 行目に落ちる。これは契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§2.1 が要求する安全側 (「MUST record the outcome as **indeterminate** and escalate it; it MUST NOT resolve it as "closed"」) であり、窓口へ上げて人間が判断する経路に載る
   - **既存 record での field 欠損は `null` 扱い**とし migration は不要 (`completion_reported_at` と同じ規約、下記 Step 5 (b) の schema 節)
   - **inspect helper の scope 外・lifecycle 由来**である点も `completion_reported_at` と同じ: `tools/inspect_pane_state.py` の `compute_idle_transition()` は docstring に「``completion_reported_at`` and any unknown keys are preserved verbatim」「Rule 4 (record deletion on pane exit) and rule 6 (lifecycle ``completion_reported_at`` set/clear) are outside the inspect scope and remain caller-applied」と書き、実装も `prev = dict(prev_record)` → `new = dict(prev)` で未知キーをそのまま引き継ぐ。したがって **3 フィールドの導入に helper のコード変更もテスト追加も要らない** (dispatcher が helper record を書いた後に別途適用する。更新規則側の相互参照は Step 5 (b) 更新規則 (7))
   - **削除**: Step 5 (b) 更新規則 (4) の record 削除 (Step 1 の `pane_exited` 受信 / (3-a) ゲートで終了確定 / CLOSE_PANE) にそのまま乗り、record ごと 3 フィールドも消える。**`list_panes` から消えたことだけを削除の根拠にしない**のも同規則のまま (ゲートが観測不能に倒したサイクルは record を保持する)
   - **旧版 fallback では `same_tab_peer_id` / `same_tab_observed_at` は書かれない** (§1-2 を通らないので「在」の確定が発生しない)。現行配備の全 backend がこちらで、(3-a-2) は従来どおり 4 行目に落ちるため **今日の挙動は変わらない**。`tracked_pane_id` は `list_panes` 由来なので版に依らず控えてよいが、旧版 fallback の Step 1 attribution は従来どおり `name` 一致で行う

   **(3-a-4) unknown / indeterminate と判定したサイクルで、監視は何を続けるか**:

   (3-a-2) の 5 行目 (indeterminate) と 6 行目 (unknown) は「対象の状態を**判定できなかった**」だけで、監視を降りる合図ではない ((P1))。当該サイクルで dispatcher がやることは次の 4 点に固定する:

   1. **worker を監視対象・active のまま保持する**: `.state/workers/worker-{task_id}.md` を `Status: pane_closed` にしない / `WORKER_PANE_EXITED` を送らない / `worker-idle-state.json` の該当 record を削除しない (Step 5 (b) 更新規則 (4) の「ゲートが『観測不能』に倒したサイクルでは record を**保持したまま**次サイクルへ送る」がそのまま適用される)
   2. **観測不能を journal + 窓口へ重複抑止付きで報告する**: (P4) の語彙を**そのまま再利用し、新しい event 名も新しい `kind` も導入しない** — journal は `anomaly_observed` / `notify_sent` に `kind=observation_unavailable`、de-dup キーは **`(worker, source, kind=observation_unavailable)`**、解除は同じ `source` の `observation_recovered`、窓口への本文は (P4) の `OBSERVATION_UNAVAILABLE:` 形。`source` には判定に使った面 (例: 同タブ生存判定なら `same_tab_judgement`、Step 1 の attribution 未確定なら `lifecycle_event`) を入れる — キーから `source` を落とすと、先に劣化した面の通知が後から劣化した別の面を覆い隠す ((P4))
   3. **次の `/loop 3m` サイクルで再評価する**: `poll_events` (Step 1) / peer message (Step 2) / events テーブルと state DB の痕跡 / §1-2 の三値判定を、次サイクルで改めて取り直す。判定は保留であって結論ではない ((P4)「観測不能は『異常なし』ではない」)
   4. **画面監視の skip は当該 worker の Step 4 / Step 5 / Step 5.2 に限る** (**`placement == "same_tab"` の worker のみ**。背景 worker は `bound_pane_id` で `inspect_pane` が届くので skip しない — (3-a-5) の「画面監視は数値 id 経由で維持する」): ペインが列挙されない以上 `inspect_pane` は取れないので、その worker についてはこの 3 つを skip し、(P4)「観測不能サイクルでは state を進めない」に従って record も更新しない (`idle_streak_cycles` を加算せず、`last_check_ts` / `last_visible_content_hash` / `last_content_change_ts` も書き換えない)。**監視ループ全体・Step 1 / Step 2・Step 5.1 の relay gap scan は止めない** — いずれも当該 worker のペイン可視性に依存しない観測面である

   これは (P5) の側の変化である: unknown / indeterminate を根拠にペインを再 spawn したり `close_pane` したりはしない (同節の禁止がそのまま掛かる)。判定が付かないことは行動を増やす理由にならず、安全側は常に「報告して止まる」。

   **解消の経路 — 6 行目は自然回復するが、5 行目は窓口の reconcile 判断でしか閉じない**: 6 行目 (unknown) は次サイクルで `same_tab` が読めるようになれば解ける一過性の状態である。一方 **5 行目 (indeterminate) は、陽性の「在」履歴を持たないまま pane と peer が同じ区間で消えた形なので自然回復しない** — 裏取り (ii) の突き合わせ先 (`same_tab_peer_id`) を書く機会 ((3-a-3) の 2 producer = spawn 時の受理サイクル / 「ペインは消えたが peer 登録は残っている」監視サイクル) が**どちらも**一度も来ないまま両面が消えた形なので、以後どのサイクルでも得られず、毎サイクル同じ 5 行目に落ち続ける (spawn producer の配線後も、旧版 fallback・縮退中・spawn より後に承認が入った worker ではこの形が残る)。dispatcher 側にこれを閉じる権限は無い ((P5)) ので、**窓口へ渡す通知本文に「reconcile 判断が要る」ことを明記し、判断材料 (陽性履歴が無いこと) を添える**:

   ```
   mcp__org-broker__send_message(to_id="secretary", message="
     OBSERVATION_UNAVAILABLE: worker-{task_id} の同タブ生存を確定できません (追跡済み数値 id の陽性履歴が無く、裏取り (i) 同タブ pane_exited / (ii) 追跡済み id の消失 がどちらも取れない)。
     ペインの生死は判定していません。監視対象・active のまま保持し毎サイクル再評価しますが、この形は自然回復しないため窓口の reconcile 判断が要ります。
   ")
   ```

   (P4) の de-dup は「面ごとに劣化区間あたり 1 回」なので、この通知も 1 回しか出ない。取りこぼしても窓口の再起動 / handover で拾えるよう、**同じ 1 行を `.state/workers/worker-{task_id}.md` の Progress Log にも残す** (Step 2 の通常進捗と同じ追記経路)。窓口が判断して `.state/workers/worker-{task_id}.md` を終端状態へ遷移させれば、[Step 5.3](#step-5-3) の監視ループ停止条件がそのまま効いてループも降りられる。**dispatcher が代わりに終端状態を書くことはしない** (契約 [`docs/contracts/delegation-lifecycle-contract.md`](../../docs/contracts/delegation-lifecycle-contract.md) T7 / E1 の dated additive: unknown / indeterminate では遷移自体が発生しない)。

   **(3-a-5) 背景タブに置いた worker の生存判定 — tracked-any-tab (id 束縛)**

   **適用対象は `placement == "background_tab"` の record を持つ worker だけ**である ((3-a-3) の schema。欠損は `"same_tab"` 扱いなので、通常委譲で spawn した worker は 1 件も入らない)。この配置は [`.dispatcher/references/spawn-flow.md`](spawn-flow.md) 3-1d の 6 条件を全て満たす dogfood 指定タスクでのみ発生し (2026-08-09 時点では条件 6 が満たされないため実際には 1 件も発生しない)、**通常委譲経路の同一タブ MUST は一切変わらない**。同タブ worker の消失評価は従来どおり (3-a-1)〜(3-a-4) が owner である。**本項は renga 面専用**でもある — 3-1d の条件 2 が transport を `renga` に限っており (broker surface に `tab` セレクタも `new_tab` も無い)、broker 運用では `placement == "background_tab"` の record がそもそも生まれないので本項は 1 度も評価されない。

**したがって本項の tool 名だけは輸送層の機械置換の対象外で、`mcp__renga-peers__*` を literal で書く (MUST)**: 本ファイル冒頭の両系注記は「完全修飾名を使用中 transport のものに読み替える」と定めるが、**背景タブ配置は renga でしか成立しない**ので、ここで broker 名に読み替えると「そのペインを作った renga session ではなく broker の状態を問い合わせる」ことになり、session provenance 照合も生存判定も成立しない。本項および Step 3 / Step 4 の背景 worker 向け bullet に literal で `mcp__renga-peers__` と書いてあるのはこの理由による (読み替えない)。

   **共有 reference §1-2 を背景 worker に流用してはならない (MUST NOT)**: [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md) §1-2 は自身を「対象名 N のピアが **自タブに** 生存しているか」を判定する規範と定義しており、その真理値表は `same_tab == False` を「他タブ」→ §1-2-c #4 の**不在**に落とす。背景タブに置いた worker は定義上つねに `same_tab == False` なので、§1-2 を当てると毎サイクル「不在」と読まれ、spawn-flow 3-4 の登録ゲートは**永久に開かない**。同じ理由で **`same_tab_peer_id` を背景 worker に流用してはならない** — あのフィールドは「自タブに 在 と確定したレコードの id」という意味を持ち (3-a-2) 4 行目の裏取り (ii) の入力になるので、別の意味の id を入れると同表が誤って終了確定へ進む。背景 worker が使うのは別フィールドの `bound_pane_id` / `bound_peer_id` である。
   （§1-2-e の「call site に独自の same-tab 規範を書いてはならない」は **同タブ生存判定** についての MUST NOT であり、本項は主語が違う = **背景タブに置いたペインの生存判定**なので抵触しない。同タブ判定の正本は引き続き §1-2 のみで、本項はそれを再掲も改変もしない。）

   **判定の入力は 3 つだけ**:

   | 入力 | 出どころ | 用途 |
   |---|---|---|
   | `bound_pane_id` | spawn-flow 3-2x の背景タブ spawn が返した**数値 pane id** | Group A の pane 操作 (`inspect_pane` / `send_keys`) の宛先と、`pane_exited` の pane 面 join |
   | `bound_peer_id` | spawn-flow 3-4 の登録ゲートが `bound_pane_id` で受理したレコードの数値 `id` | 毎サイクルの生存判定と、`pane_exited` の peer 面 join |
   | 本サイクルの `mcp__renga-peers__list_peers` 全タブ列挙 | **Step 3 で全 worker 共通に 1 サイクル 1 回だけ**引き、`{数値 id} → レコード` のローカル index にする | 上記 id の在否 |

   **判定を確定させる入力は上の 3 つだけである。** 下の全ペイン index は **4 つ目の入力ではなく裏取り面**で、単独では在否を動かさない（次節）。

   | 裏取り面 | 出どころ | 用途 |
   |---|---|---|
   | 本サイクルの `mcp__renga-peers__list_panes(tab={"all": true})` 全ペイン index | **Step 3 で全 worker 共通に 1 サイクル 1 回だけ**引く。**下記 3 条件ゲートを通ったサイクルに限る**（fail closed） | `bound_pane_id` の pane 面での corroboration（在 の裏取り / 不一致の検出）のみ。**終了確定にも `initializing` 解除にも使わない** |

   **タブ index を束縛キーにしない (MUST NOT)**: `list_peers` のタブメタデータは表示用で、タブが閉じるとずれる (renga の `list_peers` tool 契約:「tab indexes shift when tabs close, so always address a peer by its numeric pane id」)。契約側も `tab` を「meaningful **only** for equality comparison within a single enumeration」(T-§2.2-fields) に限っているので、サイクルを跨いで保持する期待値にはならない。「期待タブに束縛する」は **そのタブへ spawn したときに返った数値 id に束縛する**ことで満たす — 同じ束縛をより強い形 (タブ単位ではなくペイン単位) で行うので、タブ値を控える必要が無い。

   **session provenance を毎サイクル確かめる — 束縛した id を使う前の前提条件 (MUST)**: 数値 pane id の一意性保証は **backend session に閉じている**。契約 T-§4.2-id は renga の id counter について「Nothing persists or restores it … **across a daemon restart they are reissued from the beginning**」と書き、restart を跨いで使った id について「is not addressing a stale pane — it is addressing **a different, live pane**, and the backend answers **successfully**. No error code marks this」と続ける。**成功応答が返るので、照合しない限り取り違えに気付けない。** したがって背景 worker を評価するサイクルでは、`list_peers` を引くのと同じ 1 回の branch で `mcp__renga-peers__server_info` を引き、`server.pid` を record の `bound_server_pid` と突き合わせる:

   - **一致した (`server.pid` が `bound_server_pid` と、`server.endpoint` が `bound_server_endpoint` と、両方)** → **それだけでは足りない (下記)**。第 2 面の identity 再照合まで通ったときに `bound_pane_id` / `bound_peer_id` を使ってよい
   - **一致しない / `server_info` が `status != "connected"` を返す / `bound_server_pid` ・ `bound_server_endpoint` ・ `bound_cwd` のいずれかが `null`** → **束縛は失効している。下表の三値評価に入らず、`bound_pane_id` / `bound_peer_id` を宛先にも判定にも一切使わない**。当該 worker を **indeterminate** として (3-a-4) の 4 点に載せ、(P4) を `source=session_provenance` で 1 回報告する。**裸の `name` や相対セレクタへ降格してはならない** (同節 **(O4)**: 「MUST NOT issue or re-issue it under a relative selector, a bare `name`, or any widened search」)。この形は自然回復しないので、(3-a-4) の「窓口の reconcile 判断が要る」通知文に載せる (新しい escalation 経路は作らない)

   **pid 等値は必要条件であって十分条件ではない — identity を毎サイクル再照合する (MUST)**: OS は daemon 終了後に同じ pid を再利用しうるので、「renga が restart し、pane id を再発行し、たまたま同じ pid を得た」形では pid 等値が**通ってしまう**。これは本照合が防ごうとしている当の事故 (別の生きたペインを成功裏に撃つ) なので、pid + endpoint の等値だけで先へ進んではならない。**独立した第 2 面として、本サイクルの `list_peers` index で `bound_peer_id` を引いたレコードが `name == "worker-{task_id}"` かつ `role == "worker"` かつ `cwd == bound_cwd` を満たすことを毎サイクル確かめる** (`cwd` が **org 束縛**を担う — 予約名は別 org の並走タブに同名で実在するので `name` / `role` だけでは「自 org のペインである」ことを示さず、同節が求める「bound to the expected organisation」を discharge できない) (契約 T-§4.2-id の **(O2)**「an observation **independent of the id itself**」。index は既に手元にあるので追加の往復は発生しない):

   **2 面が gate するものは違う (重要)**: 第 1 面 (session provenance) は**この record の `bound_*` に触れる全ての操作**の前提で、第 2 面 (identity 再照合) は**生きているペインを宛先にする操作** (`inspect_pane` / `send_keys` / 下表 2 行目の「在」判定) の前提である。**`pane_exited` の attribution に第 2 面を要求してはならない** — ペインが終了していれば `bound_peer_id` は列挙から消えているのが正常なので、第 2 面を要求すると**終了を永久に確定できなくなる**。契約 T-§4.2-id は「**Within one daemon lifetime ids are therefore never recycled in any reachable operation**」と書くので、第 1 面が通っている限り session 内で id が別ペインを指すことはなく、**id 等値だけで attribution は確定してよい**。第 2 面が存在する理由は「pid + endpoint がたまたま再利用され、第 1 面が偽陽性になった restart」を捕まえることにあり、その形は第 2 面の不一致として現れる。

   - **2 面とも通った** → (O1) / (O2) を discharge。下表の三値評価へ進み、宛先操作も許可される
   - **pid + endpoint は一致したが、`bound_peer_id` のレコードが `name` / `role` / `cwd` のいずれかで食い違った** → **id が別のペインに再割り当てされている。** 束縛失効と同じ扱いで `bound_*` を一切使わず、**indeterminate** として (P4) を `source=session_provenance` で報告する (下表 3 行目の「消えた = unknown」とは区別する — こちらは「在るが別物」という陽性の不一致なので、窓口の reconcile 判断が要る形として通知文に明記する)
   - **`bound_peer_id` が index に無い** → identity 再照合の材料が無いだけなので、これは下表 3 行目 (unknown) であって不一致ではない

   > **restart 単位で一意な session 識別子は既に `server_info` の `server.session_id` として存在する** (renga 2.1.0 で確認。同ツール記述は「identifies the running renga PROCESS INSTANCE and changes on every restart」「Do NOT substitute `server.pid` or `server.endpoint` for it (the endpoint embeds the pid, and pids get recycled)」と書く)。Step 1 の cursor 束縛は**既にこの値を第一候補に使っている**が、**背景 worker の第 1 面 (`bound_server_pid` + `bound_server_endpoint` の等値) は現行手順では差し替えていない** — 差し替えは spawn 時に控える面 (3-2b) と照合面の両方を変える挙動変更で、cursor の束縛とは独立に取るべき変更だからである。差し替える場合は `bound_server_pid` の代わりに `server.session_id` を控えて第 1 面をその等値に置き換える (pid + endpoint の対は、それが無い版のための近似として残る)。第 2 面の identity 再照合は置き換え後も残す — (O2) は (O1) とは別の obligation なので、一方が強くなっても他方を免除しない。

   **毎サイクルの三値** (上の session provenance が一致したサイクルだけ評価する):

   | # | 本サイクルの観測 | 判定 |
   |---|---|---|
   | 1 | `bound_peer_id` が `null` (登録ゲート未通過) | **initializing** — 在にも不在にも数えない。監視対象・active のまま次サイクルへ。**ただし恒久 initializing にはしない**: spawn-flow 3-4b の登録予算 (最大 30 秒) は監視サイクル (3 分) より短いので、**監視ループが観測する `bound_peer_id == null` は「まだ登録待ち」ではなく「登録に失敗して派遣が取り止められた」形である**。したがって 2 サイクル連続でこの行に落ちたら **indeterminate** として (3-a-4) の 4 点に載せ、(P4) を `source=registration_timeout` で 1 回報告する (下記) |
   | 2 | `bound_peer_id` が本サイクルの index に**在る** | **在** (tracked-any-tab)。通常評価を続ける |
   | 3 | `bound_peer_id` が index から**消えた** | **unknown** — 不在に潰さない (下記)。(3-a-4) の 4 点をそのまま適用する |
   | 4 | `list_peers` を引けなかった / 列挙が旧版形に戻った (marker が 1 件も無い) | **観測不能** — (P4) を `source=tracked_any_tab` で 1 回報告し次サイクルで再評価 |

   **1 行目 (registration-timeout) の着地 — 記録は残すが、放置もしない**: spawn-flow 3-2b は **spawn 戻り値を得た直後に** `placement` / `bound_pane_id` / `bound_server_pid` / `bound_server_endpoint` / `bound_cwd` を書き、`bound_peer_id` だけを 3-4b の受理後に merge する。この順序は「`list_panes` に出ない子ペインを記録なしで残さない」ためのもので、その代償として **peer 登録に失敗して派遣が取り止められた worker の record が `bound_peer_id == null` で残りうる**。この形は:

   - **record を消さない** — ペインは実在するので、消すと発見手段が全て失われる (規則 (4) の「消してはならない」と同じ理由)
   - **`bound_pane_id` での画面監視は続ける** — session provenance (第 1 面) さえ通っていれば `inspect_pane` は届くので、Step 4 の宛先規則どおり観測する。**承認プロンプトで止まっている / エラーを出しているペインを、登録に失敗したという理由で見えなくしない**
   - **`pane_exited` の attribution も続ける** — pane 面 (`bound_pane_id`) の join は `bound_peer_id` が無くても成立するので、人間がそのペインを閉じれば通常どおり終了確定して record が消える
   - **窓口へ 1 回上げる** — 「起動はしたが peer 登録に至らなかった背景ペインが残っている」ことは人間の reconcile 判断が要る形なので、(3-a-4) の通知文にその旨を書く

   **3 行目を不在に潰さない理由**: 背景 worker が `list_panes` に出ないのは**配置上の定数**であって終了の証拠ではない ((P3) の contract §1.5「Visibility scope: current tab only」)。したがって背景 worker には (3-a-2) が要求する「独立した 2 面の一致」を組む材料が構造的に 1 面しか無く、`list_peers` からの消失 1 面だけで退役させると peer の登録遅延・一時 deregister をそのまま終了として扱うことになる。**在→消失は unknown であり、不在ではない。**

   **全ペイン index — pane 面の裏取りを 1 面足す (fail closed・corroboration only)**

   ここまでの三値は `list_peers` = **messaging 面**の 1 面だけで回っている。renga 2.2.0 は pane 制御側にも全タブ列挙を足した (`list_panes(tab={"all": true})`。renga `CHANGELOG.md` `## [2.2.0] — 2026-08-10`、契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) L-§1.5) ので、**同じ判定に対して独立した 2 面目**を置ける。本節はそれを **裏取り専用**で足すもので、**上の三値の owner は変わらない**。

   **発動ゲート — 3 条件を全て満たしたときだけ `tab` セレクタを送る。1 つでも欠けたら送らない (fail closed, MUST)**:

   | # | 条件 | 確認方法 |
   |---|---|---|
   | 1 | **契約側が批准済みである** | [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) の *Proposed amendment (2026-08-10): all-tab pane enumeration — the `cross_tab_list` capability* 節の `Status:` 行が **ratified** であること。**PROPOSED のあいだは §1.5 が verbatim で生きており、`tab` セレクタを載せた `list_panes` は非適合**である (同節 L-§ratification「Effect before ratification」)。判定は 3-1d 条件 6 が T-§4.2-place-rec の verdict を読むのと同じ形で、**節の本文を毎回読み直す** (本ファイルの記述を事実の代わりに読まない) |
   | 2 | **first-drive ゲートが記録済みである** | 契約 L-§ratification が定める記録 id **`cross_tab_list_first_drive`** が recorded であること。**現行の照会面 [`tools/capability_gate.py`](../../tools/capability_gate.py) はこの名前を持たない**ので、今日の答えは **undetermined = 未 clear** である (契約 T-§cap の fail-safe 既定。T-§ratification-pc が 6 つの rung 名について書いているのと同じ扱い)。**messaging 側の `first_drive` / `production_activation` や pane-control ladder の rung 記録で代替してはならない (MUST NOT)** — gate レコードは capability / surface の欄を持たないので、別 family の記録は本ゲートについて何も証拠しない。**ただし本条件が禁じるのは「列挙結果を判定に使うこと」であって「レコードを作るための 1 回きりのドライブそのもの」ではない** — そう読むとレコードを作る唯一の行為が禁じられ、ゲートが構造的に開かなくなる。下記解除手順 2(b) の初回ドライブだけは、**条件 1 を通したあと・1 回だけ・結果を判定に使わず捨て・人間へ報告する**という 4 条件のもとで本条件の**適用外**である（契約 L-§ratification の operational gate 節「what this gate bars」）。2 回目以降は本条件がそのまま掛かる |
   | 3 | **`cross_tab_list` を広告している** | Step 1 が cursor 束縛のために既に引いた `mcp__renga-peers__server_info` の**同じ応答**を再利用し、`status == "connected"` **かつ** `effective_capabilities` に **exact `"cross_tab_list"`** が在ること。`server.capabilities` では gate しない。`status` が `detached` / `unreachable` のとき、`effective_capabilities` が `null` のとき、当該 token が無いときは**いずれも「無い」側**に倒す (`[]` と `null` は別の観測なので**報告文では区別する**が、分岐先は同じ) |

   **ゲート未通過サイクルの挙動 (MUST)**: `tab` セレクタを**送らない**。Step 3 冒頭の `tab` 引数無し `list_panes` は従来どおり引くが、**その caller タブのみの応答を全タブ census と解釈してはならない**。#329 以前のサーバーは未知の `tab` フィールドを黙って捨てて caller タブだけを `Ok` で返し、**正しい応答と区別がつかない**ためである (契約 L-§1.5「The fail-closed rule」、renga `src/ipc/mod.rs:300-307`)。**呼び出し時に `[server_too_old]` が返った場合も同じ扱い**で、非 transient なので**リトライしない** (契約 T-§6)。裏取り面が無いだけなので、生存判定は従来どおり 3 入力で回し、**この縮退自体は (P4) の報告対象にしない** ((P5): 精度が下がっただけで行動は増やさない)。ただし**条件 3 が「在ると確認できていたのに call-time で `server_too_old` に転んだ」形は TOCTOU なので 1 回だけ報告する** (`source=tracked_any_tab`)。

   **index を自 org のものと確定する規則 (MUST)**: レコードの照合キーは **`cwd == bound_cwd`** である。**`name` / `tab` / `tab_name` を ownership key にしてはならない** — 予約名 (`worker-{task_id}` 等) は並走 org のタブに同名で実在し、タブ index は表示用でタブが閉じるとずれ、タブラベルは一意ですらない (契約 L-§1.5-fields、renga `src/ipc/mod.rs:970-977`)。`cwd` が欠損 / 曖昧なレコードは**自 org と確定しない**側に倒す。**`focused` は全タブ応答ではタブごとに 1 件**なので「唯一の focused」を前提にしない (同 renga `src/ipc/mod.rs:954-960`)。**`same_tab` は `list_panes` 面と `list_peers` 面で別物**であり、片方を他方の証拠にしない (契約の two-scopes MUST)。

   **使い方 — 三値を動かせるのは 1 行だけ**:

   | 本サイクルの `list_peers` 面 | 全ペイン index 面 (`bound_pane_id` を引く) | 判定 |
   |---|---|---|
   | 在 (三値 2 行目) | 在・`cwd` 一致 | **在**。判定は変わらない。2 面一致なので (3-a-4) 送りの材料が増えるだけ |
   | 在 (三値 2 行目) | 消えた | **在のまま**。列挙 1 面の消失は unknown 止まりであって不在ではない (契約 L-§4.2「A negative observation on this surface is not, on its own, an exit」)。**判定を落とさない** |
   | 消えた (三値 3 行目) | 在・`cwd` 一致 | **unknown のまま**。ただし「pane 面では在」という**陽性の観測**が付くので、(3-a-4) の通知文にその旨を書く。**退役へは進めない** |
   | 消えた (三値 3 行目) | 消えた | **unknown のまま**。2 面消えても終了確定にしない — 両面とも**同じクラスの列挙観測**であって独立した 2 種類の証拠ではなく、ここを不在に潰すと (R2) が防いでいる「生きている worker の退役」がそのまま戻る |
   | 任意 | 在るが `cwd` / `name` / `role` が食い違う | **indeterminate**。id が別ペインに再割り当てされている疑いなので、第 2 面 (identity 再照合) の不一致と同じ扱いで `bound_*` を使わず、(P4) を `source=session_provenance` で報告する |
   | 任意 | index が無い (ゲート未通過 / 引けなかった) | **裏取り無し**。三値をそのまま採用する |

   **したがって index が単独で動かせるのは最終行 (陽性の不一致 → indeterminate) だけ**で、**在 も 不在 も index だけでは確定しない**。これは (R2) を巻き戻さないための設計であって保守性の問題ではない: 「消えた」を強い証拠として扱う面をもう 1 つ増やすことが、まさに (R2) が harness に禁じている読み方だからである。

   **終了確定の経路は増えない (MUST NOT)**: 本節は `pane_exited` の attribution にも `initializing` の解除にも一切関与しない。終了確定は次項のとおり **attribution 済みの `pane_exited` だけ**である。

   **容量会計へは配線しない (本タスクのスコープ外・MUST NOT)**: 全ペイン index を `delegate-plan` helper の `--panes-json` や `choose_split` へ渡さない。全タブの `PaneInfo` は**タブごとに座標が重複し `focused` が複数ある**ので、rect ベースの split planner にそのまま食わせると別タブ・別 org のペインを split 対象にしうる (契約 L-§place)。全タブ census を容量会計へ載せるには caller タブの **geometry** と全体の **population** を別入力に分ける設計が要り、それは本節ではなく dispatch 経路側の作業である。**本節は契約 T-§4.2-place の (R1) / (R4) の verdict を動かさない** — (R1) は「まだ id を握っていないペインを発見する手順」を別に要求しており本節はそれを持たず、(R4) は容量会計そのものを要求している (契約 T-§4.2-place-rec の 2026-08-10 再測定)。

   > **現況 (2026-08-10 時点の記録された観測 — 恒久の事実ではないので、サイクルごとに照会し直す)**: 上表の**条件 1 と条件 2 が成立しない** (契約節は PROPOSED、`cross_tab_list_first_drive` は照会面に名前が無く undetermined)。**したがって本節の枝は今日 1 度も発火せず、背景 worker の生存判定は従来どおり 3 入力だけで回る。** 本節が定めるのは「契約が批准され first-drive が記録されたあとに何をどう読むか」であって、その前の発動許可ではない。**解除の手順は下記に書いてある** — 「いつか誰かが批准する」ではなく、順に実行できる形になっている。

   **3 条件の解除手順 — 運用面 (規範面の正本は契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) L-§discharge。本項はそこを運用の手順として写したもので、食い違ったら契約側が正)**

   **順序 (MUST NOT で入れ替えない)**: **条件 1 → 条件 2** の順。批准前に first drive を撃つのは**批准が governs している当の行為**なので、先に書いた first-drive レコードは非適合なコールを記録するだけで何も clear しない。**条件 3 は他の 2 つと順序関係を持たない** — backend の性質であり、いつでも満たされ・いつでも失われる。

   | 条件 | 誰が | どこで | 何をすれば clear になるか |
   |---|---|---|---|
   | 1. 契約節の批准 | **人間**（ディスパッチャー・窓口・ワーカーのいずれも代行しない。運用の副作用として書かれることは無い） | 契約 `docs/contracts/backend-interface-contract.md` + state DB `events` | 節本文をレビューして PROPOSED → RATIFIED を決め、`bash tools/journal_append.sh notify_sent recipient=<role> kind=cross_tab_list_ratified summary="<1 行>"` で記録する（**直接 DB INSERT はしない**）。あわせて契約側の status 記載 5 箇所（節ヘッダ / L-§sup / L-§ratification / 4 つのポインタ注記 / Amendments log）を同じ変更で更新する。**該当箇所の列挙と編集内容は契約 L-§discharge 条件 1 が正本。** このレコードは `tools/capability_gate.py` の gate 名に**しない**（契約テキストについての決定であって運用ゲートではないため。同ツールの ladder rung 1 と同じ扱い） |
   | 2. first-drive 記録 | 手順 (a) は実装者、手順 (b) は**ディスパッチャーが実行し人間が確認** | (a) `tools/capability_gate.py` / (b) state DB `events` | **(a) 名前を照会可能にする**: `cross_tab_list_first_drive` **と `cross_tab_list_first_drive_pending` の 2 つ**を同ツールの gate 表へ **identity mapping**（短名 == 記録される `kind`）で追加する（pending 名が無いと (b) の中断復帰規則が観測できない）。追加前は `--gate` の choices に無く argparse が exit 2 を返すので **undetermined = 未 clear**、追加後は記録が出るまで `not_recorded`（exit 1）。**どちらも「未 clear」だが別の観測なので報告文では区別する**（再照会に意味があるのは `undetermined` のときだけ）。**(b) 初回ドライブを実行して報告し、記録する**: これは**自分が作るレコードを前提条件にしない唯一のドライブ**である（上表条件 2 の適用外。そう扱わないとゲートが開かない）。手順は **(i) 撃つ前に pending を書く → (ii) 1 回だけ撃つ → (iii) 報告 → (iv) 人間の確認後に本記録**。**(i) 先に `bash tools/journal_append.sh notify_sent recipient=<role> kind=cross_tab_list_first_drive_pending summary="<1 行>"` を書く (MUST)** — ドライブと本記録のあいだに人間の確認が挟まるので、その窓で crash / `/clear` / handover が起きると次のディスパッチャーは `not_recorded` を見て**同じコールを撃ち直してしまう**。pending を**コールの前**に書くことで、`pending 在り・本記録なし` を「**既に撃たれたかもしれない**」と読める（撃った後に書くと、まさに埋めたい窓で pending が欠ける）。この状態を観測したら**撃ち直さず**窓口経由で人間へ報告し、人間が確認を与えて本記録を完成させるか、再ドライブを 1 回だけ許可する。**(ii)(iii)** `tab` セレクタ付き `list_panes` を **1 回だけ**撃ち、**結果はレポートを書くためだけに読む**（タブ数とペイン数 / 自タブ外と判定したペイン数 / 自 org と数えた各ペインの ownership 確立方法。これらは応答を読まないと書けないので、この読みは禁止どころか必須である）。**禁じられているのは「使うこと」**で、三値評価・裏取り・退役判定・容量計算へ渡すこと、および監視 state として永続化することが該当する。**結果を使ってしまったドライブは本手順ではなく、レコード無しの通常ドライブ = 条件 2 違反**である。**(iv)** 人間の確認を得てから `bash tools/journal_append.sh notify_sent recipient=<role> kind=cross_tab_list_first_drive summary="<1 行>"`。以後 `python3 ../tools/capability_gate.py --gate cross_tab_list_first_drive` が exit 0 / `recorded` を返す |
   | 3. capability 広告 | — （**今日すでに満たされている**） | `mcp__renga-peers__server_info` の応答 | renga 2.2.0 以降のサーバー + 同 token を理解する mcp-peer なら `effective_capabilities` に載る。**やることは無い。** ただし恒久ではなく、**サイクルごとに引き直す**: 古い binary のまま動いている daemon / サーバーより古い mcp-peer / `status` が `detached`・`unreachable`（= `null` は「不明」であって「無い」ではない）/ 呼び出し時の `[server_too_old]` で unmet に戻る |

   **3 つとも clear になった時点で実際に変わること**: 背景 worker が居るサイクルで、いま作っている全タブ `list_peers` index に加えて全ペイン index を作るようになる（**1 サイクルあたり 1 往復増えるだけ・背景 worker が 0 件のサイクルでは 0 往復**）。**index にできることは live になっても変わらない** — 上の「使い方」表のとおり裏取り面のままで、動かせるのは「`bound_pane_id` のレコードが在るが `cwd` / `name` / `role` で食い違う → **indeterminate**」の 1 行だけであり、**在 の側にも 終了 の側にも 1 行も動かせない**。生存判定は引き続き peer 面、退役確定は引き続き attribution 済み `pane_exited`、容量会計は依然として無配線である。契約 T-§4.2-place-rec の verdict も、spawn-flow 3-1d 条件 4（背景配置は 1 枚まで）も動かない。

   **終了確定は attribution 済みの `pane_exited` だけ (MUST)**: Step 1 から持ち越した候補イベントについて、**本項の session provenance (第 1 面) が通っているサイクルに限り** (第 2 面は要求しない — 上記「2 面が gate するものは違う」)、イベントの pane 識別子 (canonical `ev.id` → alias `ev.pane_id`) が `bound_pane_id` に一致するか、peer 識別子 (canonical `ev.peer_id` → alias `ev.agent_id`) が `bound_peer_id` に一致したときに終了を確定し、`WORKER_PANE_EXITED` を送って Step 5 (b) 更新規則 (4) の record 削除に進む (canonical を先に、alias を後に読む規律は Step 1 と同じ)。**背景 worker では `name` 一致を同定に使わない** — 全タブ世界で `name` は一意でない (T-§2.2 / T-§3.1「a harness MUST NOT match a lifecycle event to a tracked pane by `name` alone」)。join が引けないまま 3 行目が続く形は **indeterminate** で、(3-a-4) の解消経路 (窓口の reconcile 判断) にそのまま乗せる。**背景 worker 専用の新しい escalation 経路・新しい event 名・新しい `kind` は作らない。**

   **(3-a-2) の表は背景 worker には適用しない**: 同表の 1 列目 (`list_panes` から消えた) が定数で成立し、3 列目 (§1-2 の同タブ生存) が構造的に「不在」を返すので、そのまま当てると毎サイクル 4 行目 (終了とみなす) に落ちる。背景 worker の消失評価は本項が owner で、(3-a-2) は同タブ worker が owner である。

   **画面監視は数値 id 経由で維持する**: 背景 worker は `list_panes` に出ないが、`inspect_pane` は Group A なので**自タブと確立した列挙に依らず数値 pane id なら他タブへ届く**（2026-08-09 に人間が批准した cross-tab pane control。契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2 の Group A / 数値 id 規律）。したがって背景 worker では **Step 4 / Step 5 / Step 5.2 を skip しない** — Step 4 の inspect 宛先を `target=<bound_pane_id>` に切り替えて従来どおり評価する。skip したままにすると `APPROVAL_BLOCKED` / `ERROR_DETECTED` / `STALL_SUSPECTED` / `PANE_OUTPUT_WITHOUT_PEER_MSG` の 4 系統が背景配置でまとめて検出不能になる。Step 5.1 (relay gap) と Step 5.25 (relay scan) は元から pane 可視性に依存しないので本項の影響を受けない。

   **追加の待ち時間は 0 分**: 増えるのは 3 分サイクルあたり `server_info` と全タブ `list_peers` の **各 1 往復だけ** (背景 worker が 0 件のサイクルでは 0 往復。上記「全ペイン index」のゲートを 3 条件とも通ったサイクルではこれに全タブ `list_panes` の **1 往復**が加わるが、ゲート未通過サイクルでは 0 往復のまま) で、その 1 回ずつの応答から作った `server.pid` と index を全背景 worker が参照する。**per-worker の再取得・retry・新しい blocking wait を足さない** ((P5) と同じ向き: 精度は上がるが行動は増えない)。列挙が引けなかったサイクルは 4 行目 (観測不能) に落として次サイクルへ送る。

4. **`mcp__org-broker__inspect_pane` でワーカーペインの画面内容を走査し異常検出**:
   - **目的**: ワーカー自己申告に依存せず、ディスパッチャー自身が画面内容から APPROVAL_BLOCKED / ERROR を検出する独立した観測チャネル
   - **実行**: Step 3 で得た `list_panes` の active worker (`role == "worker"`)、**および `placement == "background_tab"` で (3-a-5) の session provenance (第 1 面) が通り `bound_pane_id` を持つ worker** (登録待ち / registration-timeout の worker も含む — 承認プロンプトやエラーを登録状態を理由に見落とさない) それぞれに対し:
     ```
     result = mcp__org-broker__inspect_pane(
         target="worker-{task_id}",              # 背景 worker は target=<bound_pane_id> (数値)。下記の宛先規則
         lines=<該当 worker pane の height>,   # Step 3 list_panes の height。取れなければ十分大きい固定値 (例 200)
         include_cursor=true,
         format="grid"
     )
     # result.structuredContent に {lines: [{row, text}], cursor: {visible, row, col}} が入る
     ```
     を順次実行 (16 ワーカー並列でも合計 1 秒未満)。`lines` は **Step 3 の `list_panes` で得た該当 worker pane の `height`** を渡し、pane の全 visible 行を取得して (d) の ERROR scan を全行対象にする (Issue #492 gap 1: `lines=10` の bottom-10 窓では row 15 のような scroll-up した error banner を取りこぼす。`inspect_pane` の `lines` は「末尾 N 行への trim」なので、固定値だと pane height がそれを超えた環境で上段を取りこぼす — 必ず実 height を使う)。`list_panes` の height が取れない場合のみ十分大きい固定値 (例 200) でフォールバックする。APPROVAL_BLOCKED の target line は (a) の通り「最後の非空行」なので返却行数を増やしても変わらない。
   - **宛先規則 — 背景 worker は数値 id で撃つ ((3-a-5))**: `placement == "background_tab"` の worker には **`mcp__renga-peers__inspect_pane`** (背景配置は renga 面専用なので tool 名を読み替えない — (3-a-5)) を `target=<bound_pane_id>` (背景タブ spawn の戻り値の数値 pane id) で呼ぶ。**その前に (3-a-5) の session provenance 照合を通していること**が前提で、通っていないサイクルは inspect を撃たない (契約 T-§4.2-id の (O1)/(O4))。**name 宛にしない** — 予約名 `worker-{task_id}` は並走 org の別タブに同名で実在しうるうえ、相対セレクタ / 裸の name は自タブに解決するので背景タブには届かない (契約 T-§4.2)。背景 worker は `list_panes` に出ないので `height` も取れず、`lines` は上記フォールバック (十分大きい固定値、例 200) を使う。**エラー分岐と (a)〜(g) の判定は同タブ worker と完全に同じ**で、背景 worker 専用の判定パターン・通知 kind・de-dup キーは足さない
   - **エラー時の挙動**: tool result テキストに `[<code>] <msg>` 形式でエラーが埋まる。code で分岐する (詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`):
     - `[pane_not_found]` / `[pane_vanished]` — **「ワーカーが閉じた」と読まない** ([観測の原則](#observation-principle) (P3))。この code は cross-tab addressing でも返る契約なので (contract §4.2)、フォーカスが別タブにある間は**生きているペインに対しても返る**。そのワーカーの inspect を skip した上で、Step 3 (3-a) の裏取りゲートに掛けて `WORKER_PANE_EXITED` を送るか観測不能に倒すかを決める (ゲートを通らずに exit 経路へ直行しない)
     - `[shutting_down]` — transport backend 停止中。監視ループを即停止し、`mcp__org-broker__send_message` で `FOREMAN_STOPPING` を窓口に通知
     - `[io_error]` / `[app_timeout]` / `[internal]` — 一過性の可能性。journal helper (`../tools/journal_append.sh`) 経由で `.state/state.db` の `events` テーブルに記録して次サイクルで再試行
     - 未知 code (将来の transport backend が追加) — journal 記録のみで続行
     - **どの error code であれ、そのサイクルはその worker の画面を観測できていない = 観測不能である** ((P1))。当該 worker について Step 5 / Step 5.2 の判定に進まず、(P4) のとおり `worker-idle-state.json` の record も更新しない (`idle_streak_cycles` を加算せず、観測できなかった時間を「静止していた」と数えない)

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

   #### (b-fp) 既知の取りこぼしパターンと見分け方 — 数字選択メニュー形式 (2026-07-27 実測)
   (b) の regex リストは **単一行の y/n 確認**を前提に設計されているため、Claude Code の **数字選択メニュー UI** を取りこぼす (false negative)。破壊的 DB 操作 (`DELETE`) を含む委譲で、ワーカーが実行方法を確認する場面に以下の形が出た:

   ```
   DELETE の実行をどう進めますか？（対象: documents 155,612 行 + cascade。バックアップ検証済み・取り消し不可）

   ❯ 1. 自分で実行する（推奨）
     2. 権限を付与して私が実行
     3. 中止する
     4. Type something.
   ──────────────────────────────────────
     5. Chat about this

   Enter to select · ↑/↓ to navigate · Esc to cancel
   ```

   - **なぜ漏れるか**: target line ((a) の「最後の非空行」) は `Enter to select · ↑/↓ to navigate · Esc to cancel` になる。(b) の `^\s*Esc to cancel` は **行頭 anchored** なので、`Esc to cancel` が行**末**に来るこの形には一致しない。`^❯\s*1\.\s*Yes\s*$` も選択肢文言が `Yes` 以外 (上例では `自分で実行する（推奨）`) なので一致しない。結果 `tools/inspect_anomaly_scan.py` は `{"detections": []}` を返し、承認待ちを素通りする
   - **見分け方**: `❯ N. <選択肢>` の複数行 + 末尾行が `Enter to select · ↑/↓ to navigate · Esc to cancel`。y/n 確認とは別系統の Claude Code インタラクティブ選択 UI で、**破壊的・取り消し不可能な操作の実行方法選択で現れやすい**。見逃すと人間承認が必要な重大な分岐点で dispatcher が気づけない
   - **当座の運用**: 破壊的操作 (DB 削除、ファイル削除等) を含む委譲では、helper が clean (`detections: []`) を返しても target line を目視確認する。目視で承認待ちと判断したら APPROVAL_BLOCKED 相当として (e) の journal → de-dup → notify 手順にそのまま乗せる。ただし **判定根拠が regex 一致ではなく目視である事実を journal に残す** (`matched=` に該当行、`note=manual_visual` 等)。実測時は `confidence=high` で通知したため、後から journal だけを見ると自動 regex 一致と区別できなくなっていた
   - **恒久対応案 (未実装)**: (b) の regex リストに `^\s*❯\s*\d+\.` (複数選択肢の先頭行パターン) または `Enter to select · ↑/↓ to navigate · Esc to cancel` の**末尾行 anchored** 形を追加する。次に (b) を更新する機会に併せて検討する

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
   # --spinner-state-file は worker ごとに 1 ファイル。旧形式 spinner-age の
   # 前サイクル差分抑止 (Issue #698) に使う。省略すると従来どおり stateless scan。
   echo "$inspect_json" | py -3 ../tools/inspect_anomaly_scan.py \
       --spinner-threshold-min 5 \
       --spinner-state-file ../.state/dispatcher/spinner-age-seen/worker-{task_id}.json
   ```
   threshold やパターンの単一定義はこの module 側にあり、regression test (`tests/test_inspect_anomaly_scan.py`、観測 case = row 15 の 529 banner + 9m spinner + bottom 10 空) が契約を pin する。手で判定する場合も上記リストと同義。

   **旧形式 spinner-age の false positive 抑止 (Issue #698)**: Claude Code は完了ターンの所要時間サマリ (`✻ Cooked for 31m 40s` 等) を live な旧形式 spinner と**同一の shape** で描画する。これが idle worker のスクロールバックに残ると、毎サイクル「5 分以上回る stuck spinner」として再マッチし false positive alert を繰り返していた。単一フレームでは凍結サマリと本物の stuck spinner を内容・位置だけで区別できないため、helper は「live spinner の `for Xm Ys` counter は毎サイクル進むが、凍結サマリは byte 一致で不変」という不変条件で差分を取る。`--spinner-state-file` を worker ごとに渡すと、前サイクルと同一の旧形式 spinner 行は凍結サマリとみなして検知を抑止する。初回観測は必ず発火 (差分対象が無い) し、完全凍結した live pane は hash ベースの STALL 経路 (`tools/inspect_pane_state.py`) が拾うので signal は失われない。state file 欠落 / 破損時は「前サイクル無し」扱いで再発火する安全側フォールバック (本物の stuck spinner を state file の不備で黙殺しない)。

   #### (d-fp) 既知の誤検知パターンと見分け方 — doc / コードコメント中の status code (2026-07-29 実測)
   (d) の status-code ゲート (`(?<!#)\b(429|500|502|503|504|529)\b` + 同一行の error 文脈キーワード) は、**その行が「実際に発生したエラーの記録」か「エラーの意味を説明する文書」かを区別できない**。ワーカーが `README.md` のトラブルシューティング表と `.py` のコードコメントに「`temporarily_unavailable` (503) が返る」「retry 可能な 503 になる」と書いた委譲で、**同一サイクル内に 5 回連続の false positive** が出た。該当行はいずれもライブエラーではなく、ワーカーの文書化作業そのものだった。

   - **見分け方 (文体で判別する)**: target 行の前後文脈が「〜が返る」「〜になる」「説明」「表の 1 行」等の**解説・ドキュメント文体**なら false positive の可能性が高い。実際のコマンド出力 (`$ curl ...` の応答、pytest 失敗ログ等) に status code が含まれる場合とは文体が明確に異なる
   - **繰り返し発火は仕様どおり**: 同じ 503 系の行が複数サイクルにわたって画面内に留まり続ける (scroll で流れる速度が遅いだけ) 場合、毎サイクル再検出されるのは (d) の設計どおりの動作。都度 journal に理由を書いて抑制すればよい
   - **起きやすい委譲**: API のエラーハンドリングを実装・文書化するタスク。この種の説明文が大量に書かれるため、**同一ワーカーに対して同じ false positive が繰り返し発生しやすい**
   - **当座の運用**: 発生ごとに `anomaly_observed` へ理由付きで記録し、**通知は送らない** ((e) の step 2 で dispatcher が抑制判断する)。実測時は誤通知として窓口へ一度も送っていない
   - **恒久対応案 (未実装、別 Issue)**: `tools/inspect_anomaly_scan.py` 側に「diff の追加行 (`+` prefix) やコメント記号 (`#`, `--`, docstring 内) にある status code は除外する」ヒューリスティックを足せば自動的に減らせる。現行の `(?<!#)` は `#529` のような **issue ref に直接隣接する `#` のみ**を除外するもので、`# retry 可能な 503 になる` のような**コメント行**は除外できない

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
   Step 4 の inspect_pane で worker pane が APPROVAL_BLOCKED / ERROR どちらの regex にも該当せず、かつ **正規化済み全可視行のコンテンツハッシュが前サイクルから変化していない** 状態が **連続 3 サイクル以上** (= 9 分相当、`/loop 3m` cadence 前提) 続いた worker を **stall 候補** とする。サイクル数は本ファイルでこの 3 を目安として扱う。**候補は申告ではない** ([観測の原則](#observation-principle) (P2)) — 画面面 1 面が「変化なし」と言っているだけの状態であり、(b-2)〜(b-4) を通過して初めて (c)/(d) の判定に進む。

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
   - 更新規則 (Step 4 の inspect 直後に評価。**規則 (1)(2)(2-bis)(3)(5) は helper `tools/inspect_pane_state.py: compute_idle_transition()` が record モードで決定的に生成する** — dispatcher は返ってきた `record` をそのまま書き、hash / streak / last_content_change_ts を手計算しない。規則 (3) の anomaly reset は helper に `--anomaly-fired` を渡した時のみ適用される。**規則 (4) (pane 消失で record 削除) と (6) (`completion_reported_at` の lifecycle 更新) と (7) (同タブ生存判定用の追跡 id) は inspect スコープ外なので helper record に**乗らず**、dispatcher が helper record を書いた後に別途適用する** (削除は key ごと消す / lifecycle は Step 2 の監視制御メッセージ処理)。以下は契約面の記述。`last_check_ts` は本サイクルの inspect_pane 呼び出し時刻 = `now` に常に更新される。`last_content_change_ts` の更新は idle→active 遷移時のみで、値は **前サイクルの `last_check_ts`** を採用する — Codex round 3 Major 対応。同サイクル内で `check_messages` (Step 2) が `inspect_pane` (Step 4) より先に実行されるため、現サイクルの inspect 時刻を `last_content_change_ts` に入れると同サイクルで届いた正当な worker→secretary peer-msg の `occurred_at` が cutoff に弾かれて (c)(ii) で空集合になり Step 5.2 が誤発火する。前サイクルの `last_check_ts` を起点にすれば「最後に画面が idle と確認できた時点」が cutoff になり、同サイクル中に届いた peer-msg は確実にその cutoff 以降になるので acked として正しく拾える):
     1. `content_hash` が `last_visible_content_hash` と一致 → `idle_streak_cycles += 1` (`last_content_change_ts` は据え置き)。`last_visible_content_hash` は本サイクルの `content_hash` に更新 (同値なので実質不変)
     2. `content_hash` が変化 (= 画面 scrollback に動きあり = アクティブ) → `last_visible_content_hash` を本サイクルの `content_hash` に更新した上で:
        - **idle→active 遷移時** (前サイクルの `idle_streak_cycles >= 1` から本サイクルで変化を観測) → `idle_streak_cycles = 0` で reset、`last_content_change_ts = (前サイクルの) last_check_ts` に更新 (= 直前まで idle と確認できていた時刻、本サイクルで届く peer-msg より必ず古い)。Step 5.2 (PANE_OUTPUT_WITHOUT_PEER_MSG) がこの ts を起点に worker→secretary 痕跡を scan して fire / acked 判定する
        - **active 継続時** (前サイクルの `idle_streak_cycles == 0` から本サイクルでも変化を観測、出力が連続している間) → `idle_streak_cycles = 0` のまま、`last_content_change_ts` は **据え置き** (active 期間の START 時刻を保持し、active 期間中に届く peer-msg を全部 acked 経路に乗せる)
     2-bis. **spinner フィールドは毎サイクル更新** (Issue #671): helper 出力の `spinner_signature` / `spinner_elapsed_sec` を `last_spinner_signature` / `last_spinner_elapsed_sec` に書き、spinner を観測したサイクルは `last_spinner_seen_ts = now`。spinner 不在 (`spinner_present == false`) なら両値 null (次サイクルの increased 判定を新規 turn 扱いにリセット)。この spinner 更新は hash 判定 (1)/(2) と独立 (spinner は hash から正規化除外されるので active/idle 判定を左右しない — active 継続は scrollback、active-spinner 抑止は elapsed という別軸)
     3. APPROVAL_BLOCKED / ERROR のどちらかが (e) の通知に進んだ場合も reset (anomaly が独立して扱われたので stall 評価を巻き戻す)、`idle_streak_cycles = 0` / `last_content_change_ts = (前サイクルの) last_check_ts` に更新 (anomaly 通知後は再観測扱いで、(2) の遷移時規則と同じ起点を使う)。この reset は hash 変化の有無に依らず適用される (静的な承認プロンプトが複数サイクル残って hash 不変でも、通知が出たサイクルは streak を巻き戻す)。**helper に `--anomaly-fired` を渡すと `decision.transition == "anomaly_reset"` として record にこの規則が織り込まれる** (pane 観測だけからは Step 4 の発火有無が分からないため、発火有無は dispatcher が flag で渡す)
     4. **Step 1 で受信した `pane_exited` が当該 worker のものと同定できた** (Step 1 の attribution)、または **Step 3 (3-a) の裏取りゲートで終了が確定した** → 該当 key をファイルから削除 (record ごと消えるので `completion_reported_at` も同時に破棄される = CLOSE_PANE / pane 消失時の解除はこの規則が担う、Issue #658)。**同定できていない `pane_exited` の受信は削除の根拠にならない** — attribution 未確定のまま record を消すと `same_tab_peer_id` / `tracked_pane_id` が失われ、以後どのサイクルでも id 照合も裏取り (ii) も成立しなくなって、まさに再評価が要る worker を恒久 indeterminate に固定してしまう ((3-a-4))。**`list_panes` から消えたことだけを削除の根拠にしない**のも同じ ([観測の原則](#observation-principle) (P2)、Issue #869): フォーカス移動による一時的な不可視で record を消すと `completion_reported_at` と観測履歴 (hash / streak / `last_content_change_ts`) が失われ、フォーカスが戻った後に Step 5.2 の false positive と完了確認の重複が再発する。ゲートが「観測不能」に倒したサイクル・attribution 未確定のサイクルはいずれも record を**保持したまま**次サイクルへ送る ((P4)「観測不能サイクルでは state を進めない」と同じ扱い)
     5. **既存 record に `last_visible_content_hash` フィールドが無い場合は migration せず「初回観測」として扱う (Issue #680 Blocker 2)**: 本サイクルの `content_hash` を保存し、`idle_streak_cycles = 0` / `last_content_change_ts = null` に**リセット**する。旧実装の `last_target_line_text` と cursor 由来で積まれた `idle_streak_cycles` (実測 13 まで到達) を新ハッシュ判定へ**持ち越さない** (持ち越すと初回から STALL/PANE_OUTPUT 条件を満たして即誤発火する)。旧 `last_target_line_text` は読まない (deprecated 残置のみ)。この初回観測は `compute_idle_transition()` が `prev_record` の hash 欠損を検出して自動でリセットする。次サイクル以降、前サイクル `last_check_ts` が確定した上での idle→active 遷移を待ってから初めて `last_content_change_ts` が値を持つので、Step 5.2 (b)(1) の前提 (ts 存在) を満たさず初回直後の false positive を構造的に抑止する。**同様に `completion_reported_at` field が無い場合も `null` 扱い** (= 完了未報告、migration 不要)
     6. `completion_reported_at` の set / clear (Issue #658): **lifecycle-event ベース**で更新する — Step 2 の監視制御メッセージ処理が `WORKER_COMPLETION_NOTED` 受信で `received_at` を set / `WORKER_REOPENED` 受信で `null` に clear (fast-path)、Step 5.2 (b)(6) の gate が `runs.status == 'in_use'` を観測したとき `null` に self-heal clear (reliable backstop、`WORKER_REOPENED` 取りこぼし対策 = P2)、record 削除 ((4)) で破棄。上記 (1)〜(3) の inspect ベース更新 (idle_streak / last_content_change_ts) は `completion_reported_at` を **触らない** (完了報告済み worker がレビュー中に画面出力しても completion 印は維持され、review 待ち idle の false positive を抑止し続ける)。**timeout による自然失効は持たない** (レビューが長い正常ケースを false positive に戻さないため、(g) 参照)
     7. `same_tab_peer_id` / `same_tab_observed_at` / `tracked_pane_id` (同タブ生存の二段判定用) と `placement` / `bound_pane_id` / `bound_server_pid` / `bound_server_endpoint` / `bound_cwd` / `bound_peer_id` / `pending_exit_event` (背景タブ配置の tracked-any-tab 判定用、Step 3 (3-a-5))。**schema と set / 参照規則は Step 3 (3-a-3)**: (4) / (6) と同じ **inspect スコープ外の caller-applied フィールド**なので (1)〜(3) の inspect ベース更新は触らず、helper は未知キーとして verbatim 保存する (`tools/inspect_pane_state.py: compute_idle_transition()` docstring)。set は Step 3 の `list_panes` 観測 (`tracked_pane_id`) と §1-2 の「在」確定 (`same_tab_peer_id` / `same_tab_observed_at`)、破棄は (4) の record 削除。**`same_tab_peer_id` / `same_tab_observed_at` は監視ループ外からも set されうる** — (3-a-3) の spawn producer は post-spawn の状態記録 ([`.dispatcher/references/spawn-flow.md`](spawn-flow.md) Step 4 の該当項) で書くので、監視ループが 1 サイクルも回っていない worker の record にこの 2 フィールドだけが載っている状態は正常である (残りのフィールドは初回サイクルの helper record が埋める。**merge であって上書きではない**ので、どちらの向きでも相手のフィールドを消さない)。**背景タブ配置の 5 フィールド (`placement` / `bound_pane_id` / `bound_server_pid` / `bound_server_endpoint` / `bound_cwd`) はさらに早く、spawn 戻り値を得た直後に書かれる** ([`.dispatcher/references/spawn-flow.md`](spawn-flow.md) 3-2b) ので、`bound_peer_id` すら未設定の record が存在する状態も正常である ((3-a-5) の三値表 1 行目)。`pending_exit_event` は監視ループの Step 1 が書き、attribution 確定時に record ごと (4) で破棄される。
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

   **設計メモ**:
   - `pr_opened` / `pr_merged` は `docs/journal-events.md` で Writer = secretary、`task` payload field は task_id (bare) と定義済みなので、新規 event を導入せず既存 ledger だけで判定できる (Issue #304 提案 option 1)
   - `STALL_PR_MERGE_LOOKBACK_MIN = 60` の根拠: PR レビュー / merge 承認の人間応答時間スケール。短すぎると (15 分) `worker_completed` が window から外れて誤発火、長すぎると stuck が見逃される。Issue #304 / session #12 観測の中間値
   - PR-pending-merge を別 event ではなく lookback の延長で扱う理由: (c)(1) の補助シグナル軸 (`worker_completed` を含む 5 種) はそのまま再利用でき、acked 経路に乗せれば既存の de-dup / 通知抑制と整合する。新カテゴリ追加は最小差分
   - merge 後 `pr_merged` event が記録されると次サイクルで sub-state が解け、lookback は 15 分に戻る。merge 後 `worker_completed` の `ts` が 15 分窓から外れた状態で worker pane がまだ open していれば通常 stall として扱われる (運用上は merge と同時に CLOSE_PANE が走るので実質的にこの経路には乗らない)
   - `T_pr_opened` 自体の経過時間は判定に使わない (PR open から 60 分以内/超過の評価は不要)。代わりに (c)(1) の lookback を 60 分に拡張することで、`worker_completed` 等の最新 worker→secretary event が 60 分以内であれば acked、超えれば timeout という同一フォーマットの判定を維持する

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

   #### (b-4) 発火前の観測面チェック — 報告痕跡の不在だけで STALL を申告しない ([観測の原則](#observation-principle) の適用)
   `STALL_SUSPECTED` は「worker が異常停止している」という**対象の状態**の申告なので、(P2) により独立した複数面の一致を要求する。チェックは 2 段に分かれ、**評価順は (b-4-i) → (b-2) → (c) → (b-4-ii) → (d)** である (台帳面は (c) の scan を回さないと値が出ないので、(c) より前に判定しない):

   **(b-4-i) (c) に進む前に確認する (画面側 2 点)**:

   1. **画面面**: (b) の `content_hash` が 3 サイクル不変であること — **かつその 3 サイクルとも `inspect_pane` が実際に成功していること**。エラーで画面を観測できなかったサイクルは Step 4 のエラー分岐と (P4) のとおり record を更新しないので streak には積まれない (観測できなかった時間が「静止していた」に化けない)
   2. **busy の陰性確認**: (b-3) の `suppress_stall == false` であること。新形式 active spinner が生きている間は busy の**陽性証拠**なので発火しない。ただし spinner が**無いこと**は idle の証拠ではない ((P3)) ので、これは独立した 1 面であって画面面 (1) の代替にはならない

   どちらかが欠けたら (c) の補助シグナル取得を回さずに打ち切る (画面が観測不能なら (P4) を報告、busy なら (b-3) の soft-note を残して次サイクルへ)。

   **(b-4-ii) (c) の scan 後、(d) の timeout 分岐に入る直前に確認する (台帳側 1 点)**:

   3. **台帳面**: (c)(1) の worker→secretary 痕跡が lookback 窓内に 0 件であること — **かつ (c)(1) の SQLite query が実際に成功していること**。`.state/state.db` が読めずに 0 行だったなら、それは「痕跡が無い」ではなく観測不能である ((P3): 「痕跡が無い」は「読めなかった」を含意しない)

   **「報告痕跡が無い＝止まっている」ではない**: worker の報告は**ターン境界**で出るため、1 ターンが長い作業 (実装 → セルフレビュー → codex ゲートを 1 ターン内で連続実行) の最中に痕跡が出ないのは正常である。台帳面 1 面だけを見て発火したのが 2026-08-08 の実誤検知 2 件の型で (Issue #869)、これは (b-fp) 型 1 / 型 2 と同じ族に属する。画面面が「動いている」または「観測できていない」なら、台帳面が 0 件でも申告しない。

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

   2. **org-broker poll_events (forward-compat、現状は補助のみ)**: 現状 `mcp__org-broker__poll_events` は pane lifecycle event のみを流し、`send_message` は流れない (`.claude/skills/org-delegate/references/renga-error-codes.md` の type 表参照)。さらに `since` は時刻ではなく前サイクルから受け取る **opaque cursor** で、初回省略時は「今以降」セマンティクス (過去履歴は返らない) なので、本判定で「過去 15 分」をピンポイント検索する用途には今は使えない。events scan を **authoritative source** とする。将来 transport backend が `send_message` event を `poll_events` に流すようになれば、Step 1 で既に保持している `.state/dispatcher-event-cursor.txt` の cursor 経由で受信した worker→secretary の送信を `(worker, kind=stall_acked)` ledger に変換するルートを追加する想定 (本 PR ではプレースホルダとして記述するに留める)。

   #### (d) 分岐 (acked vs timeout)
   - **acked の判定** ((c)(1-bis) の gate 込み): acked ⟺ (`worker_reported` / `worker_completed` / `plan_delivered` / `prep_delivered` のいずれかが lookback 窓内) **OR** (`worker_escalation` が lookback 窓内 **AND NOT** `escalation_trace_is_stale`)。`worker_escalation` が窓内でも (c)(1-bis) で `escalation_trace_is_stale == true` と判定された場合は acked に数えず、かつ他 4 event のヒットも無ければ **timeout 経路へ落ち**、(従来は古い `worker_escalation` 痕跡で抑制されていた) STALL_SUSPECTED が正しく発火する (Issue #603)。soft-note の `note` 値は既存 (`awaiting_secretary_lookback_15m` / `awaiting_pr_merge_lookback_60m`) を維持する。
   - **acked** — 上記判定で 1 件以上ヒット: 「Secretary 判断待ち idle」または「PR-pending-merge 待機 idle」とみなし、STALL_SUSPECTED を **発火しない**。Step 4 (e) と同じ `anomaly_observed` ledger に soft-note として記録 (新 event 名は導入せず既存 catalog を再利用)。`note` は (b-2) で選択した sub-state を反映する:
     ```bash
     # 通常 sub-state (lookback 15m)
     bash ../tools/journal_append.sh anomaly_observed source=stall_check worker=worker-{task_id} kind=stall_acked confidence=n/a note=awaiting_secretary_lookback_15m
     # PR-pending-merge sub-state (lookback 60m, Issue #304)
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

   #### (f) 設計メモ
   - **`STALL_SECRETARY_LOOKBACK_MIN = 15` の根拠**: Secretary が人間に判断を仰いでから応答を返すまで 5–10 分のオーダーが典型で、その間ワーカーは idle のまま待機する。15 分 window で「直近やり取りあり」を担保すれば、人間応答待ちの誤発火を実用上排除できる。短くすると判断待ちワーカーが timeout 経路に落ちて誤発火、長くすると完了後ペインの reactivation 痕跡を拾い続けて stuck が見逃される。中間値の 15 分が現状のスイートスポット
   - **`STALL_PR_MERGE_LOOKBACK_MIN = 60` の根拠 (Issue #304)**: PR open 後の merge 承認は user の手動操作で 15–60 分かかるのが典型。worker は完了報告済みで idle のまま正しく待機している (= stuck ではない) が、15 分 lookback では `worker_completed` が window から外れて誤 STALL 発火する (session #12 で実測)。`pr_opened` 済 / `pr_merged` 未の sub-state を event ledger だけで判定し、その期間だけ lookback を 60 分に拡張する。merge 後は `pr_merged` が記録されて即座に通常 sub-state に戻る
   - **60 分超過時の挙動 (Issue #304 long-tail)**: PR が 60 分以上 open のまま (週末越え / レビュー長期化) で `worker_completed` が window から外れると timeout 経路で再び STALL_SUSPECTED が発火する。これは仕様上「60 分を越えたら sticky な PR-pending-merge は人間判断対象として再通知する」設計で、Issue #304 の指定どおり。30 秒 de-dup のため 3 分サイクルごとに再通知される点はノイズだが、`org-pull-request` SKILL の close condition (24–48h レビュー idle で人間判断、参照: [`.claude/skills/org-pull-request/SKILL.md`](../../.claude/skills/org-pull-request/SKILL.md)) と組み合わせて運用判断する。長期 PR を完全 silence したい場合は将来 Issue で「`pr_opened` 済 task は STALL を一切上げない」へ変更する選択肢があるが、本 PR では「60 分まで猶予」の lookback 延長に留める (Issue 仕様準拠)
   - **60 分超過 + `ci_completed` の `status="incomplete"` playbook**: PR-pending-merge sub-state の worker が拡張 lookback (60 分) を超過して timeout 経路に入り、かつ events の `ci_completed` payload が `status="incomplete"` のとき、`incomplete` は CI red では **なく判定不能** である (`tools/pr_watch.py` の `gh pr checks --json` クエリが final verdict を取得できないまま retry budget を使い切り、exit-code fallback として記録した値。`status` の値域は event catalog `docs/journal-events.md` を参照)。dispatcher は自分で `gh pr checks` 等を叩いて調査・再試行 **しない** (役割境界: dispatcher は調査しない)。やることは状態の記録 (通常の timeout 経路の soft-note / `notify_sent` に留める) と、secretary への informational 報告 (「`ci_completed` が `status="incomplete"` で CI 判定不能のまま。merge / pane 自動クローズが進んでいない可能性あり」の趣旨) のみ。実例: runtime PR #126 / #127 で 2 連続観測 (merge / pane 自動クローズが進まない事象)。恒久修正 (`tools/pr_watch.py` 側の取得リトライ / 分類改善) は別 Issue で追跡する
   - **events scan を primary にした理由**: `poll_events` は現状 pane lifecycle event (`pane_started` / `pane_exited` / `events_dropped` / `heartbeat`) のみで `send_message` を流さない (`.claude/skills/org-delegate/references/renga-error-codes.md` の type 表参照)。一方、secretary 受信時の `worker_escalation` / `worker_reported` は authoritative な ledger として既に永続化されている。再利用が正解
   - **soft-note を残す意味**: 後で「なぜ STALL_SUSPECTED が発火しなかったか」を retro / debug で再現できる。silent skip にすると、誤検出疑いが起きたとき journal だけでは判別不能になる。Step 4 と同じ `anomaly_observed` event を再利用するので、event catalog (`docs/journal-events.md`) への新規追記は不要 (kind は `stall_acked`、sub-state は `note` field で `awaiting_secretary_lookback_15m` / `awaiting_pr_merge_lookback_60m` を区別)
   - **decision-register gate を入れた理由 (Issue #603)**: journal の `worker_escalation` 行だけでは「判断待ち継続中」と「ユーザー返答が worker へ転送済みでもう idle」を区別できない (どちらも同じ行が 15 分窓内に残るため)。register の `resolution_kind == "to_worker"` (status `resolved`) が転送完了を表す authoritative signal なので、これを照会して stale な痕跡を acked から外す。これにより転送後に 529 / stuck で idle 化した worker が誤って acked 抑制されず STALL/ERROR 評価に戻る
   - **決定的判定を tools CLI に寄せた理由 (Issue #603)**: stale 判定 (最新 decision の選定 = `received_at` 最大、status / resolution_kind 照合) を prose の jq 直書きにせず `tools/pending_decisions.py latest-resolution` の CLI に寄せた。register schema を直接知る既存 helper (`list_pending_older_than` / `list_escalated_user_replied_older_than` 等) と同じ層分離を維持し、register の内部表現変更に prose が追従しなくて済む (決定的判定はコード側、prose は契約面のみ)
   - **想定シナリオ (Issue #304 acceptance)**:
     - regression: worker が `worker_completed` 報告 → secretary が PR 作成 (`pr_opened`) → CI green → user が 30 分後に merge 承認。30 分時点で (b-2) は PR-pending-merge sub-state (60m lookback)、`worker_completed` は 30 分 < 60 分で acked 経路、STALL_SUSPECTED は **発火しない** ✓
     - inverse: worker が完全停止 (PR 未 open、`worker_completed` も無し)。(b-2) は通常 sub-state (15m lookback)、events scan で痕跡 0 件、idle streak ≥ 3 サイクルで timeout 経路、STALL_SUSPECTED **従来通り発火する** ✓
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
   python3 ../tools/pending_decisions.py list --older-than-min 15
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
   python3 ../tools/pending_decisions.py list --user-replied-older-than-min 15
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
   起点は **直近の worker→secretary event** に固定する。`.state/state.db` の `events` テーブルから `kind ∈ {worker_escalation, worker_reported}` かつ `json_extract(payload_json, '$.worker') == "worker-{task_id}"` を満たすエントリの最新 1 件を取り、その `occurred_at` を `T_last_worker_in` とする。`worker_completed` / `plan_delivered` / `prep_delivered` は **対象外** (これらは「完了 / 中間引き渡し」で、secretary が直ちに user に上げる契約ではない。判断仰ぎ・進捗共有のみが relay gap の対象)。**Step 5 (c)(1) の acked 集合 (5 種) と混同して `worker_completed` / `plan_delivered` / `prep_delivered` を kind 集合に含めてはならない** — 対象外 event を起点にすると relay gap を誤検知する:

   ```bash
   # ディスパッチャーの cwd は .dispatcher/ なので 1 階層上の .state/state.db を読む
   # (Step 5.2 (c) と同じ SQLite one-liner idiom)。
   # kind は worker_escalation / worker_reported の 2 種のみ (Step 5 (c)(1) の 5 種ではない)。
   sqlite3 ../.state/state.db "
     SELECT MAX(occurred_at) FROM events
     WHERE kind IN ('worker_escalation','worker_reported')
       AND json_extract(payload_json, '\$.worker') = 'worker-{task_id}'
   "
   # 空 (NULL) なら T_last_worker_in なし → (b)(1) 不成立で候補から除外。
   ```

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

   1. **events scan**: 既存 event catalog (`docs/journal-events.md`) に「secretary→worker の send_message 受信時に secretary が書く event」は定義されていない。`worker_escalation` / `worker_reported` / `worker_completed` 等は **worker 起点の inbound** を secretary が記録する ledger であり、逆方向 (secretary→worker outbound) は ledger 化されていない。`user_decision_relayed` のような新 event を捏造して proxy にするのは event 名の確定を要し、本 PR スコープ外 (curator 領域)
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
   - エラー時の挙動 (`[pane_not_found]` 等): secretary pane を観測できていないので、snapshot 更新だけでなく **当該サイクルの relay gap 判定そのものを skip する**。画面差分が取れなかったことを (b)(2) の「secretary→user 痕跡なし」に数えると、**自分の観測不能を secretary の中継漏れとして申告する**ことになる ([観測の原則](#observation-principle) (P1))。journal に `anomaly_observed source=relay_gap_check kind=inspect_error` を残す (kind は既存値を維持する。意味は (P4) の `observation_unavailable` と同じ「観測不能の記録」)

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
   ペイン content が一旦変化し、十分長い時間静止し、ペイン活動後に worker→secretary 痕跡が無い worker を **候補** とする。以下を **すべて** 満たすこと (加えて、画面面 (1)(2) と台帳面 (5) が**どちらも実際に観測できている**こと — [観測の原則](#observation-principle) (P2)。inspect がエラーだったサイクルは record を更新しないので (1)(2) は自然に不成立になるが、`.state/state.db` が読めず (c) の query 自体が失敗した場合は「痕跡なし (NULL)」と区別できないため、fire せず (P4) の観測不能として扱う):

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

5.25. **未配送終端イベントの relay scan (CI-watch zero-miss, Refs #653 #658)** — `events` を正本とする outbox 型 relay。`pr_watch` が書いた終端イベント (`ci_completed` / `pr_merged` / `pr_merge_watch_timeout` / `pr_merged_no_run` / `pr_merged_head_unconfirmed` / `pr_watch_aborted` / `notify_failed`) のうち、まだ窓口へ配送されていないものを `event_deliveries` 配送台帳と突き合わせて scan し、ディスパッチャー (broker token 保有) が窓口へ確実に relay する。**pr-watch ペインからの低遅延 peer push (path A) は best-effort で silent no-op しうる** (PR #73 障害: 汎用 spawn ペインの env 欠如で broker queue に `CI_COMPLETED` が 1 件も入らず窓口 idle) ため、この relay が **見逃しゼロの主保証 (path B)** になる。

   **実行手順** (dispatcher cwd は `.dispatcher/` なので `../tools/`):

   0. **本ステップが実際に走っているかを機械検証する (Refs #941)**。サイクル冒頭の `spawn_gate.py audit` と同じ位置・同じ exit 規約で 1 回叩く:

      ```bash
      python3 ../tools/relay_scan.py --recipient secretary --audit
      ```

      stdout は単一 JSON (`{status, recipient, checked_at, last_scan_at, age_min, stale_min, pending_now}`)。exit code で分岐する:

      - **exit 0** — `status` は `fresh` (直近 `stale_min` 分以内に scan 実績あり) か `no_db` (state DB 不在 = relay 対象が構造的に存在しない)。何もせず 1. へ進む
      - **exit 10** — `status` は `stale` (scan が `stale_min` 分以上止まっている) か `never_scanned` (一度も走った記録が無い / heartbeat が壊れている)。**窓口へ `RELAY_SCAN_STALE: status=<status> last_scan_at=<ts> pending_now=<n>` を送る**。原因は概ね本ステップ自体が実行されていないことなので、報告後も 1. は通常どおり実行する (報告と復旧を同じサイクルで行う)
      - **exit 2** — ツールエラー。stdout / stderr を添えて窓口へ通知する

      `--list` は実行のたびに `.state/dispatcher/relay-scan-heartbeat.json` へ実行痕跡 (heartbeat) を無条件で残す。**「配送対象が無かった scan」と「そもそも走らなかった scan」は台帳の行だけでは区別できない** (どちらも `event_deliveries` に何も足さない) ため、この heartbeat が唯一の弁別子になる。閾値は既定 15 分 = `/loop 3m` の 5 サイクル分で、`--stale-min` で変更できる。

   1. 未配送の終端イベントを列挙する (各行に relay 試行を記帳する):

      ```bash
      python3 ../tools/relay_scan.py --recipient secretary --list
      ```

      出力は JSON 配列。各要素は `{source_event_id, kind, occurred_at, attempt, message, payload}`。空配列 (`[]`) なら本ステップは何もせず終了。DB 不在時も `[]` (エラーにしない)。

      **`[]` と「出力が無い」を同一視しない (Refs #941)**: 判定は **exit code 0 かつ stdout が JSON としてパースできること**の両方で行う。exit != 0 / stdout が空 / JSON でない場合は「配送対象なし」ではなく **ツールが走っていない**（`command not found`・import エラー・DB 破損等）ので、**本ステップを完了扱いにせず窓口へ `RELAY_SCAN_BROKEN: <exit code> <stderr 1 行>` を送る**。2026-07-30 〜 2026-08-19 の 20 日間、runbook の綴りが `python`（この環境には `python3` しか無い）だったため毎サイクル `command not found` で stdout が空になり、それが `[]` と区別されずに「配送対象なし」と読まれ続けた。終端イベント 134 件がその間に滞留している。

   2. 各要素について、`message` フィールドをそのまま窓口へ送る:

      ```
      mcp__org-broker__send_message(to_id="secretary", message="<各要素の message>")
      ```

      `message` は `CI_COMPLETED: PR #<n> (status=..., head=...) [relay]` 等、窓口の CI 検知節が認識する形式に整形済み (末尾 `[relay]` で直 push と区別可能)。

   3. **send_message が成功したら**、その配送を台帳に確定する (以後 relay されない):

      ```bash
      python3 ../tools/relay_scan.py --recipient secretary --mark-delivered --source-event-id <source_event_id>
      ```

      送信が `[pane_not_found]` 等で失敗した場合は `--mark-failed --source-event-id <id> --error "<code>"` を記帳する (台帳上は `failed` = 再試行可能。次サイクルで再 surface される)。

   **設計上の不変条件**:
   - **at-least-once・冪等**: 重複抑止は「送信マーカー」ではなく `event_deliveries` の `UNIQUE (source_event_id, recipient)` idempotency key で行う。Step 2 で確認した send を先に投げ、成功後に `--mark-delivered` する順序なので、send と mark の間で dispatcher が落ちても次サイクルで再 relay される (窓口は終端信号の二重受信を冪等に扱う)。一度 delivered になった event は二度と surface されない。
   - **この台帳 dedup は Step 4/5/5.1/5.2 の 30 秒 anomaly 通知 dedup とは別レイヤー**: 前者は「配送成功済みイベントの再配送防止」、後者は「同一異常の再通知抑制」で目的が異なる。両者を混同しない。
   - **worker 不在でも実行する**: PR merge 後などで worker pane が既に閉じていても `ci_completed` 等の配送漏れをカバーする必要があるため、本ステップは pane 存在に依らず走る (下記 Step 7 の reduced-mode 例外リストに含める)。
   - **escalation は対象外**: `worker_escalation` は Step 5.1 の SECRETARY_RELAY_GAP 経路が relay owner なので本 relay set から除外する (二重 relay 防止)。`notify_failed` は含める (push 失敗そのものが窓口に伝えるべき配送ギャップ = fail-loud end-to-end)。
   - **実行痕跡は台帳と別に持つ (Refs #941)**: `event_deliveries` の行は「配送対象があった scan」しか証拠しないので、relay の**不実行**は台帳からは検出できない。`--list` が無条件に書く heartbeat と `--audit` の staleness 判定がその検出面であり、`--audit` を毎サイクル叩かなければ本ステップは再び 20 日間黙って止まりうる。heartbeat の書き込み失敗は握り潰される (relay 本体を telemetry の失敗で止めないため) が、その場合 `--audit` は `stale` 側に倒れる = fail-loud 方向である。
   - **scan floor は「配送台帳エポック」**（`event_deliveries` 台帳が生成された瞬間 = schema version 3 migration の適用時刻）を既定にする（`relay_scan.py` は `--since-hours` 省略時にこのエポックを floor にする）。エポックは **pre-ledger history（台帳導入前の過去イベント）を除外**（初回デプロイの一斉 relay flood を防ぐ anti-flood 境界）しつつ、**エポック以降のイベントは配送されるまで age に依らず eligible** に保つ。これは wall-clock の移動窓（`now - N h`）と違い、ディスパッチャーが数週間停止しても post-ledger の終端イベントを取りこぼさない（wall-clock 窓だと停止が窓長を超えると一度も試行していないイベントが窓外に落ちて見逃す — Codex P2）。手動 backfill が要る特殊時のみ `--since-hours 0`（unbounded、pre-ledger history も relay）を明示する。

<a id="step-5-3"></a>
5.3. **オンデマンド curate の完了監視 (curate-inflight)** — CLOSE_PANE Step 5-3 ([`.dispatcher/references/pane-close.md`](pane-close.md)) が spawn 直後に書いた `.state/dispatcher/curate-inflight.json` が存在する場合のみ実行する (無ければ skip)。curator の完了待ちを CLOSE_PANE ハンドラでブロッキングせず、本監視ループの通常サイクルに載せるための受け口。判定順序は **(a) → (c) → (b)** ((a) が最優先。pane 消失より先に同サイクル受信済みの CURATE_* を処理しないと、curator が CURATE_DONE 送信後に消えたケースを「未受領のまま消えた」と誤報告して情報が欠落する):

   **定数**: `CURATE_TIMEOUT_MIN = 20` (curate 開始からの初回観測閾値) / `CURATE_HARD_CAP_MIN = 40` (延長を含む絶対上限)。

   <a id="step-5-3-close"></a>
   **共通の close 判定表** — (a) の完了受領も (b) の打ち切り / hard cap も、curator ペインを閉じるか / `curate-inflight.json` を残すかは**この 1 つの表に従う** (経路ごとに別の分岐を足さない)。curator の close は spawn とは別サイクルの**非同期**なので、spawn 戻り値の pane_id はその場限りでは残らない。識別の起点は `curate-inflight.json` の `pane_id` (CLOSE_PANE Step 5-3 が spawn 直後に書いた数値 pane id) で、close の直前に `mcp__org-broker__list_panes` で identity を照合する ([`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md) Phase 3.7 の attention watcher 停止と同じ idiom)。

   **表に入る前に、まず「この列挙が自タブのものか」を確立する** — 下の列挙健全性より手前の判定である。`name` / `role` の照合が見るのは「その id が期待どおりのペインか」だけで、**その列挙が自分のタブのものか**は見ていない。pre-capability の renga では `mcp__org-broker__list_panes` が**フォーカス中のタブ**に解決するので、確立できていない列挙から採った id は別タブのペインを指しうる (契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2 の MUST は数値 pane id の出所を "an enumeration it has independently established to be its own tab's" と書いている)。**確立手段は次の 2 つだけ**で、いずれか 1 つが成立したときにその列挙を**自タブと確立済み**とみなす:

   1. **backend が単一タブモデル** — `org-broker` は Group B も自身の単一タブモデル内で解決するので、列挙は構成上つねに自タブのもの (契約 §8.1 / §8.10)
   2. **`caller_scope` を確立できている** — `mcp__org-broker__list_panes` が caller のタブにフォーカス非依存で解決する (契約 T-§cap。`caller_scope_close_identity` とは別トークンで、一方から他方を導出しない)

   **`same_tab` は確立手段に数えない** — `same_tab` / `tab` は契約 T-§2.2-fields が **`list_peers` のレコード**に足すフィールドで、pane 制御の起点である `mcp__org-broker__list_panes` のレコードには載らない。`list_panes` が "never shows other tabs" であることは列挙が**ひとつの**タブに閉じることを保証するだけで、**それがどのタブか**は focused-tab 規則のまま (知る必要のある harness は別途確立しなければならない)。`list_peers` の `same_tab` を根拠に pane 制御を許すのは、契約が MUST で禁じる「messaging 到達性から pane 制御到達性を推論する」ことそのものである (同契約「The two scopes are not interchangeable (MUST)」)。**否定方向の絞り込みにのみ使える** — `same_tab == False` のレコードに `pane_id` を割り当てない ([`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md) Phase 1) という狭める使い方は正しく、自タブ確立の根拠にはならない。

   **2 つのいずれも成立しないなら close を撃たない** — 下の判定表の行 1 に合流させ、`curate-inflight.json` を保持して窓口へ報告し、次サイクルで再評価する。相対セレクタへのフォールバックはしない。2 つ目は確立できたサイクルだけ数える (契約 T-§cap。確かめられない / 確定できないときは fail-safe default で**不成立**、version 文字列やサーバー名からの推定は禁止) ので、確立手順を踏んでいないサイクルでは事実上 `org-broker` 面でのみ close を撃てる — 契約 T-§4.2 の MUST の帰結なので緩めない。**`curate-inflight.json` の `pane_id` / spawn 戻り値 / `CLOSE_PANE` の `{pane_id}` から得た id も例外ではない**: それらの id 自体が自タブ由来でも、close 直前の identity 照合に使う**列挙そのもの**が自タブと確立できていなければ照合結果を信用できない。確立は照合の前段に置く。

   **次に列挙の健全性を判定する** — Step 3 (3-a-1) と同じ弁別子を使う: `mcp__org-broker__list_panes` の応答に org の他メンバー (`secretary` / 他の worker / watcher 等) が**残っている**なら列挙は健全で、curator の不在は curator 固有の事象として読んでよい。**自分以外の org のペインが 1 つも出てこない**なら、curator の消失ではなく自分の**観測不能**である ([観測の原則](#observation-principle) (P4))。

   | # | 条件 | close するか | `curate-inflight.json` |
   |---|---|---|---|
   | 1 | 列挙を**自タブと確立できない** (上記 2 手段のいずれも不成立) / 列挙が**観測不能** (org のペインが一斉に出ない = (P4)) / **消失の裏取りが確定しない** (行 4 / 行 6 の前提が揃わない) | **撃たない** | **保持** (どれに落ちたかを報告し次サイクルで再評価) |
   | 2 | 列挙健全 / `pane_id` 在り / その id が `name == "curator"` かつ `role == "curator"` を指す | **`mcp__org-broker__close_pane(target=<pane_id>)`** | close 成功時に削除 |
   | 3 | 列挙健全 / `pane_id` 在り / その id は live だが `name` / `role` が curator でない (id recycle) | 撃たない | 削除 (curator は消失済みで id が再利用された = 消失確定) + **窓口へ informational 報告** |
   | 4 | 列挙健全 / `pane_id` 在り / その id が列挙に無く、**Step 3 (3-a) の裏取りゲートで消失が確定した** | 撃たない (既に消えている) | 削除 (消失確定) + **窓口へ informational 報告** |
   | 5 | 列挙健全 / `pane_id` **無し** (schema 追加前に書かれた残存ファイル) / 列挙に `name == "curator"` かつ `role == "curator"` のレコードが在る | **そのレコードの数値 `id` で `mcp__org-broker__close_pane(target=<pane_id>)`** | close 成功時に削除 |
   | 6 | 列挙健全 / `pane_id` **無し** / curator のレコードが列挙に無く、**Step 3 (3-a) の裏取りゲートで消失が確定した** | 撃たない | 削除 (消失確定) + **窓口へ informational 報告** |
   | 7 | close を撃って `[pane_not_found]` / `[pane_vanished]` が返った | — | 既に閉じた扱いで削除 |
   | 8 | close を撃って**それ以外**のエラーコードが返った (`server_too_old` / `[no_backend]` / `[tool_not_authorized]` 等) | 失敗 | **保持** + コードを添えて窓口へ escalate |

   - **行 2 / 行 5 の数値 id はどちらも「自タブと確立した `mcp__org-broker__list_panes`」由来なので契約適合形である** (契約が求めるのは数値であることだけでなく、その出所が自タブと確立済みの列挙であること。前段の確立を飛ばした列挙から採った数値 id は契約適合形にならない)。一方で `target="curator"` のような**相対セレクタへのフォールバックは全行で禁止**する ([`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2 "Fail-safe consequence for Group B" の MUST。数値 id を持たないまま裸 name で close を撃つと、別タブの同名ペインに当たりうる)
   - **行 5 を「閉じない」に倒さない理由**: 列挙から id を採らずに live な curator を残すと、[`.dispatcher/references/pane-close.md`](pane-close.md) 5-2 の single-flight 確認 (`name == "curator"` が存在すれば coalesce) が以後の curate をすべて抑止する
   - **行 4 / 行 6 は「1 回の列挙に出ない」だけでは成立しない**: 同じ「消失確定」という結論を (c) は Step 3 (3-a) の裏取りゲート (同区間の `pane_exited` との突き合わせ / `events_dropped` の確認 / 連続 2 サイクルの一致) を通してから出しているので、判定表だけ単発の列挙で確定させると**同じ結論に別々の証拠バーが立つ**。列挙が一時的に不完全なサイクルでは弱いほうが先に当たり、**live な curator を残したまま inflight だけ消える** (以後 5-2 の single-flight 確認が curate をすべて抑止するのに、その事実を誰も持たない状態になる)。**証拠バーは (c) に揃え、確定できないサイクルは行 1 に落として保持する**。`curate-inflight.json` を削除するのは、裏取りで消失が確定した行 4 / 行 6・id recycle を現認した行 3・既に閉じていた行 7、および close が成功した行 2 / 行 5 だけである。**保持が無限に続くことはない** — (b) の hard cap が経過時間だけで終端する (下記の終端規則)
   - **close を撃たずに追跡を捨てる行 (行 3 / 行 4 / 行 6) は必ず窓口へ informational 報告する**: この 3 行は「消失確定」と読んで `curate-inflight.json` を消す判断であり、消せば curator が居たという記録は dispatcher の手元から無くなる。**close せずに追跡を捨てる判断は必ず窓口に見えるようにする**のが本表の一貫した規則で、報告の中身は「どの行に落ちたか (id recycle / 裏取りゲートで確定した『id が列挙に無い』 / `pane_id` を欠く残存ファイルで curator も列挙に無い)」と `curate_result` の値でよい。行 1 / 行 8 の escalate (人間の行動を求める形) とは別枠で、こちらは**人間の行動を求めない informational** である
   - **行 8 を飲み込まない**: 同契約 T-§4.2 は capability gate を実装した client が呼び出しを拒否する場合を "safe, but the operation does not happen and **MUST be surfaced, not swallowed**" と MUST で書いている。`server_too_old` は daemon 世代に由来する**非 transient** な失敗なので**再試行せず**、エラーコードをそのまま添えて窓口へ escalate する (daemon の upgrade 要求として上げる)
   - **行 8 の escalate の再通知 cadence は「保持区間あたり 1 回」**: 行 8 は close が成功するか hard cap で終端するまで続く区間なので、Step 4 (e) の 30 秒窓 (`/loop 3m` cadence では毎サイクル抜ける) は使わず、[観測の原則](#observation-principle) (P4) の観測不能通知と同じ「区間あたり 1 回」の idiom を採る。escalate 送信成功時に journal を書き、**同キーの `notify_sent` が既にある間は再送しない**:

     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=curate_inflight kind=curate_close_failed confidence=n/a started_at={inflight の started_at} note={エラーコード}
     bash ../tools/journal_append.sh notify_sent source=curate_inflight kind=curate_close_failed confidence=n/a started_at={inflight の started_at}
     ```

     de-dup キーは **`(source=curate_inflight, kind=curate_close_failed, started_at)`** で、inflight の `started_at` を**必ず含める** — キーを `(source, kind)` だけにすると、次にオンデマンド起動した別の curator が同じコードで閉じられなくなっても最初の 1 件に覆い隠されて無期限に未報告になる (`started_at` は inflight ごとに一意なので、区間の境界がそのままキーの境界になる)。行 1 の保持は 3 つの理由 (自タブ未確立 / 観測不能 / 裏取り未確定) のいずれでも (P4) の `observation_unavailable` 経路が同じ規律で de-dup する。**hard cap 到達時の escalate はこの抑制の対象外** — 終端規則の escalate は inflight を削除する 1 回きりの通知で、保持中の再通知ではない
   - **行 1 で inflight を保持するのは (c) と同じ扱い**: 自タブを確立できない / 列挙が観測不能 / 消失の裏取りが確定しない のいずれも curator の消失ではなく**自分の観測面が足りていない**状態なので、その証拠で state を進めない ((P4)「観測不能サイクルでは state を進めない」)。保持したまま監視ループが止まらなくならないよう、(b) の hard cap が**経過時間だけで**終端する (下記の終端規則)

   (a) **完了受領** (最初に評価): Step 2 の `check_messages` で本サイクルに受領済みのメッセージに curator からの `CURATE_DONE` / `CURATE_SKIPPED` / `CURATE_ERROR` が含まれていたら:
   - `CURATE_ERROR` の場合のみ内容を 1 行で窓口に informational 転送する
   - **上記の [close 判定表](#step-5-3-close) に従ってペインを閉じ、`curate-inflight.json` も表のとおりに扱う** (行 1 / 行 8 は**保持**、それ以外の行は削除)。`[pane_not_found]` / `[pane_vanished]` (行 7) は既に閉じた扱いで skip する — pane が先に消えていても受領済み CURATE_* の処理を優先する
   - **行 1 / 行 8 で inflight を保持するときは、受領した CURATE_* を `curate_result` に書いてから保持する** (`CURATE_DONE` → `"done"` / `CURATE_SKIPPED` → `"skipped"` / `CURATE_ERROR` → `"error"`。schema と初期値 `null` は [`.dispatcher/references/pane-close.md`](pane-close.md) 5-3)。この 1 フィールドが「受領済みだが close だけ未確認」という事実を次サイクルへ持ち越す。書かずに保持すると、次サイクルの (c) が消失を確定したときに**受領済みの curate を「CURATE_* 未受領のまま消えた」と誤報告**し、(b) の stall 判定が既に完了している curator に掛かる
   - **「close の可否によらず削除する」という無条件削除はしない**: 閉じられなかった curator を追跡ごと捨てると、[`.dispatcher/references/pane-close.md`](pane-close.md) 5-2 の single-flight 確認が以後の curate を抑止しているのに、その事実を誰も持たない状態になる。保持した inflight は (b) の hard cap が終端する ((b) の終端規則)。state.db への後始末は不要 — curator identity はそもそも書いていない。**このサイクルでは (c)/(b) は評価しない**

   (c) **ペイン消失の検知** ((a) で CURATE_* を受領しなかったサイクルのみ): curator の生存を確認する。判定材料は (i) Step 1 の `poll_events` で curator の `pane_exited` を観測した、(ii) `list_panes` の結果に `name == "curator"` が不在 — の 2 系統。**worker 不在の reduced mode (下記 7) では Step 3 が skip されるため、(ii) は本 step 内で `mcp__org-broker__list_panes` を直接呼んで評価する** (Step 3 が走ったサイクルではその結果を再利用してよい)。これにより `events_dropped` / cursor ギャップで `pane_exited` を取り逃しても list_panes 側で必ず検知できる。**ただし (ii) 単独で消失と断定しない** — worker の消失判定と同じく Step 3 (3-a) の裏取りゲートを通す ([観測の原則](#observation-principle) (P2))。分岐は 2 つで、[close 判定表](#step-5-3-close) の自タブ確立判定 / 列挙健全性判定と同じ証拠に基づく:
   - **消失を確定した** (自タブと確立済み / 列挙健全 / 裏取りが一致) → inflight を削除し、窓口に informational 報告する。**報告の中身は `curate_result` で分ける**: `null` (未受領) なら CURATE_* 未受領のまま消えた旨 (curator 側クラッシュの可能性)、非 null (前サイクルの (a) が受領済みで、行 1 / 行 8 で close だけ確認できていなかった) なら「`curate_result` を受領済みで、close を確認できないまま消えた」旨を書く — **受領済みを未受領と報告すると、実際には走り切った curate を失敗として扱うことになる**。どちらでも閾値超過分はファイルとして残るため、次回 worker close の閾値チェックで再評価され取りこぼしにはならない。inflight が無くなるのでこのサイクルの (b) は評価しない
   - **自タブを確立できない / 観測不能に倒れた / 裏取りが確定しない** (判定表の行 1 と同じ証拠) → どれに落ちたかを添えて (P4) を報告し、**inflight を保持**して次サイクルで再評価する。**このサイクルでは (b) の打ち切り処理は行わない**が、**(b) の hard cap の経過時間判定だけは行う** ((b) の終端規則へ入る)。観測不能が続く間 hard cap が発火しないと、下記 7 の「inflight が在る間は監視ループを停止しない」と噛み合って監視ループが永久に止まらなくなる

   (b) **timeout 管理** (受領が無いサイクル): 先に `now - started_at > CURATE_HARD_CAP_MIN` を評価する。**この hard cap 判定は経過時間だけで行い、観測可否に依存させない** ((c) が観測不能に倒れたサイクルでも評価する。観測できない間 hard cap を保留すると終端条件そのものが消え、下記 7 の停止条件と噛み合って監視ループが終われなくなる)。到達していれば下記の**終端規則**へ進む。

   **hard cap 未到達で `curate_result` が非 null なら、stall 判定は行わない** ((a) が前サイクルで CURATE_* を受領済みで、行 1 / 行 8 のため close だけ未確認の状態)。完了した curator の画面が静止しているのは正常であり、ここに hash 比較を掛けると**完了を stall として打ち切る**ことになる。このサイクルは [close 判定表](#step-5-3-close) の再評価だけを行い (行 1 / 行 8 が続く間は保持したまま次サイクルへ送る)、hard cap 到達時に下記の終端規則が終端する。

   `curate_result` が `null` で、hard cap 未到達かつ `now - started_at > CURATE_TIMEOUT_MIN` なら hash 比較に進む。**観測の宛先は close 対象と同じ数値 pane_id にする**: inflight の `pane_id` (欠く残存ファイルでは列挙から引いた `name == "curator"` かつ `role == "curator"` のレコードの `id`) を [close 判定表](#step-5-3-close) と同じ**自タブ確立 → identity 照合**の順に**先に**通し、照合が通った id で `mcp__org-broker__inspect_pane(target=<照合済みの pane_id>, lines=30)` する。裸の name で観測すると、その hash が close の可否を決めるのに**観測対象と close 対象が同一ペインである保証が無い** (`inspect_pane` は Group A なので契約上は名前でも撃てるが、同じ判断の中で同定規律を食い違わせない)。自タブを確立できない場合は hash 比較にも close にも進まず判定表の行 1 として扱い、照合が外れた / 列挙に無い場合は hash 比較に入らず判定表の行 3 / 行 4 / 行 6 として扱う (close せず窓口へ informational。行 4 / 行 6 の削除は裏取りゲートで消失が確定したときだけで、確定しなければ行 1 として保持する)。得た出力 hash を inflight の `last_inspect_hash` と突き合わせ、**サイクル間の hash 比較で決定的に**判定する (単発 inspect から「作業継続中か」を主観判定しない。hash 比較は Step 5.1 (d) の secretary-pane-snapshot と同じ idiom):
   - `last_inspect_hash == null` (timeout 後の初回観測) → 現 inspect 出力の hash / 現在時刻 (UTC、本サイクルの `date -u` 由来 `now`) を `last_inspect_hash` / `last_inspect_ts` に書いて継続 (このサイクルでは閉じない。次サイクル以降の比較基準になる)
   - 現 hash ≠ `last_inspect_hash` (前回観測から画面が変化 = 作業継続中) → `extended: true` と現 hash / 現在時刻 (UTC) を書いて継続 (hard cap 到達まで同様に再評価)
   - 現 hash == `last_inspect_hash` (1 サイクル ≈ 3 分以上完全静止 = stall / エラー表示 / 入力待ち) → **打ち切り処理**: 観測内容を添えて窓口に informational 報告 → (a) と同じ [close 判定表](#step-5-3-close) に従って閉じ、`curate-inflight.json` も表のとおりに扱う (行 1 / 行 8 は**保持**し、次サイクル以降に再評価する。ここでも「close の可否によらず削除」はしない)。curate は途中終了でも knowledge/ は move-then-mark 設計のため破壊的な中間状態は残らない。**行 1 / 行 8 で保持したまま同じ静止に戻ってきたサイクルでは informational を再送しない** — 行 8 の escalate と同じ「保持区間あたり 1 回」の de-dup を `kind=curate_stall_truncated` で適用する (キーに inflight の `started_at` を含める。同じ 1 事実で窓口の受信箱を埋めないため)。**close の再試行は「そのコードが transient か」で分ける**: `[no_backend]` のように環境が復帰しうるコードは次サイクルで再試行してよいが、**`server_too_old` は非 transient** なので**再試行しない** — 契約 T-§6 が「リトライもタブ切替もせず daemon の upgrade / re-probe に回す」と定める復旧経路であり、毎サイクル同じ close を撃つのはその定めに反する。非 transient で保持に入ったときは inflight を持ったまま **close を再発行せず**、環境が変わる (daemon upgrade) か hard cap 到達で終端規則に入るまで待つ。**抑制マーカーは打ち切り報告を送った側が書く**: informational の送信成功時に次の 2 行を記帳し、**同キー `(source=curate_inflight, kind=curate_stall_truncated, started_at)` の `notify_sent` が既にある間は再送しない** (記帳を落とすと参照すべき行が一度も生まれず、抑制規則が書いてあっても毎サイクル再送される):

     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=curate_inflight kind=curate_stall_truncated confidence=n/a started_at={inflight の started_at} note={観測内容の要約}
     bash ../tools/journal_append.sh notify_sent source=curate_inflight kind=curate_stall_truncated confidence=n/a started_at={inflight の started_at}
     ```

   **hard cap 到達時の終端規則** (`now - started_at > CURATE_HARD_CAP_MIN`): close の判定は通常サイクルと同じ [close 判定表](#step-5-3-close) に従う。異なるのは inflight の始末だけで、ここが「保持し続けて監視ループを止められなくする」ことを断つ終端点になる:
   - **close できた / 既に消えていた** (行 2・行 5 の成功、および行 3・行 4・行 6・行 7) → 表のとおり inflight を削除し、**観測内容 (最後に観測した画面 / どの行に落ちたか / `curate_result`) を添えて窓口へ informational 報告する**。hard cap は「curate が `CURATE_HARD_CAP_MIN` で打ち切られた」という運用事実なので、**close に成功したサイクルでも窓口に何も告げないまま終わらない** (下の行 1 / 行 8 の escalate とは別枠。escalate は人間の行動を求める形、こちらは人間の行動を求めない informational)
   - **行 1 / 行 8 で close できないまま hard cap に達した** → **窓口へ escalate してから inflight を削除する**。escalate は informational ではなく**人間の行動を求める形**にする (dispatcher 自身は再 spawn も復旧もしない — (P5)):
     ```
     mcp__org-broker__send_message(to_id="secretary", message="
       CURATOR_CLOSE_UNRESOLVED: curator ペインが残っている可能性があります ({理由: 列挙を自タブと確立できない / 列挙が観測不能 / 消失の裏取りが確定しない / close が [<code>] で失敗})。
       人間が確認して閉じてください。閉じないと single-flight coalesce により以後の curate が抑止されます。
     ")
     ```
     ```bash
     bash ../tools/journal_append.sh anomaly_observed source=curate_inflight kind=curate_close_unresolved confidence=n/a note={理由 / エラーコード}
     bash ../tools/journal_append.sh notify_sent source=curate_inflight kind=curate_close_unresolved confidence=n/a
     ```
     **inflight を永久に保持して監視ループを止められなくするより、リークを人間に手渡すほうが安全側である** (黙って飲み込むのではなく、残存の可能性を宛先付きで渡す)。**削除するのは判定表で削除と決まった行と、この escalate の後だけ**である

   `curate-inflight.json` は handover / resume / `/clear` で**保持される**内部状態ファイル (`.dispatcher/CLAUDE.md` 「監視 gap を埋める内部状態ファイル」)。resume 後の 1 サイクル目から `started_at` 起点で timeout 管理が継続する。

6. **重要**: ディスパッチャーが自動で承認・拒否することはしない (ユーザー判断が必要)

7. ワーカーペインがない場合は `poll_events` / `check_messages` / `inspect_pane` をすべてスキップし、監視ループを停止する。**ただし次のいずれかが存在する間は停止しない**:
   - `.state/dispatcher/curate-inflight.json` が存在する間: Step 1 (`poll_events`) / Step 2 (`check_messages`) / Step 5.3 だけを継続し (worker 向けの Step 3〜5.2 は対象が無いので skip)、inflight 解消 (Step 5.3 (a)/(b)/(c) のいずれか) 後のサイクルで停止する
   - **未配送の終端イベントが残っている間 (Refs #653 #658)**: `python3 ../tools/relay_scan.py --recipient secretary --list` が非空を返す間は Step 5.25 だけを継続する。PR merge 後に worker pane が閉じても `ci_completed` 等の配送漏れをカバーするため、pane 不在でも relay を走らせる。空配列 (`[]`) を返したサイクルで (curate-inflight も無ければ) 停止する
   - **unknown / indeterminate として保持している worker が居る間 ((3-a-4))**: `list_panes` に列挙されないだけで退役が**確定していない** worker は監視対象・active のままなので、**「ワーカーペインがない」に数えない**。当該 worker については Step 4 / Step 5 / Step 5.2 を skip するが、Step 1 / Step 2 / Step 3 / Step 5.1 は通常どおり回し、次サイクルで (3-a-2) を再評価する。停止できるのは、その worker が (3-a-2) の 1 行目 / 4 行目で**終了確定した**か、窓口が `.state/workers/worker-*.md` を終端状態へ遷移させたサイクル以降
   - **`placement == "background_tab"` の worker が居る間 ((3-a-5))**: `list_panes` に出ないのは配置上の定数なので、同じく **「ワーカーペインがない」に数えない**。こちらは Step 4 / Step 5 / Step 5.2 も `bound_pane_id` 宛で**通常どおり回す** (skip しない)。停止できるのは、attribution 済みの `pane_exited` で**終了確定した**か、窓口が `.state/workers/worker-*.md` を終端状態へ遷移させたサイクル以降

監視対象のペイン名は `.state/workers/worker-{peer_id}.md` の Pane Name (`worker-{task_id}`) から取得する。

### 設計メモ

- **観測の原則を個別 patch ではなく判定より前に置いた理由 (Issue #869)**: 2026-08-08 の誤検知 3 種は発火した判定が別々 (pane 消失 / STALL / 完了確認) だったが、根は 1 つで「自分の観測手段が届いていない状態を、対象の異常と解釈した」だった。3 箇所を個別に直すと同じ誤りが**次に増える判定**で再発する。[観測の原則](#observation-principle) を監視ループの手前に置き、各判定がそこを参照する形にすると、新しい判定を足す人が「この観測面は何の証拠になるのか」を (P3) の表で先に決めることになる。表への行追加 (新しい観測面) と判定の追加は独立して行える
- **精度向上と権限拡大を分けて書く理由 (Issue #869)**: 判定を厳しくする変更は「誤検知が減る」と同時に「異常ではないと切り分けられた」という判断を dispatcher に与えるため、放っておくと「異常ではないので自分で直してよい」へ滑りやすい。(P5) で**申告条件の変更は行動の変更を含まない**ことを否定形で固定してある。実際 2026-08-08 の pane 消失は人間が別タブで開いていた生きたセッションで、dispatcher が観測結果だけを報告して復旧に動かなかったことが事故を防いだ。精度が上がったからといってこの抑制を緩めてはならない
- **なぜ `poll_events` を `timeout_ms=5000` で回すか**: 3 分のサイクル間隔の中で 5 秒分は long-poll する。5 秒経過で return して残りの時間は check_messages + list_panes + inspect_pane で補完。これにより pane 終了検知の平均遅延が cadence 非依存に 2.5 秒程度になる
- **cursor 管理**: `.state/dispatcher-event-cursor.txt` に前回 `next_since` を **`session_key` と揃えて** 保存する (単一行 JSON。schema は [`docs/contracts/state-schema-contract.md`](../../docs/contracts/state-schema-contract.md) §1.7)。初回 (cursor 無し) は `since` 省略で「今以降」セマンティクス。crash recovery 時は cursor 消失 = 過去 5 秒分のイベントを取りこぼす可能性があるが、list_panes 突き合わせで回復可能
- **なぜ cursor を session に束縛するか**: cursor の**値**は daemon session 内のカウンタで restart を跨いで復元されないのに、cursor **ファイル**は跨いで残る。この非対称を放置すると、restart 後は「前 session の大きい連番」で poll し続けて全ての `pane_exited` が空振りする — しかも**縮退の向きが安全側**（ゲートは回り続け、通知が出ないだけ）なので**症状が正常に見える**。2026-08-09 の実走ではこれが最初の worker close で顕在化し、終了検知が無言で死んだ。束縛 (Step 1) だけでは同じ静かさが残る（cursor を捨てたことに誰も気付かない）ので、**破棄を必ず窓口へ 1 行報告する fail loud** をセットにしてある。cursor を捨てた区間は原理的に回収不能なので、報告本文でフォールバック経路 (契約 T-§2.1 step (3) の `list_peers` 消失 + 窓口 reconcile) まで明示する
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
