---
name: org-suspend
description: >
  組織を中断し、全状態をディスクに保存する。「中断」「保存して終了」
  「閉じたい」「一旦やめる」「今日は終わり」と言われたときに使う。
effort: low
allowed-tools:
  - Read
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python -m tools.state_db.importer:*)
  - Bash(python3 tools/secretary_queue_watcher.py:*)
  - Bash(py -3 tools/secretary_queue_watcher.py:*)
  - Bash(rm -f .state/attention_pane.json)
  - Bash(del .state\attention_pane.json)
  - mcp__org-broker__check_messages
  - mcp__org-broker__close_pane
  - mcp__org-broker__inspect_pane
  - mcp__org-broker__list_panes
  - mcp__org-broker__list_peers
  - mcp__org-broker__poll_events
  - mcp__org-broker__send_keys
  - mcp__org-broker__send_message
  - mcp__org-broker__set_pane_identity
  - mcp__org-broker__set_summary
  - mcp__org-broker__spawn_claude_pane
  - mcp__org-broker__spawn_pane
---

# org-suspend: 組織の中断

全ワーカーの状態を収集し、ディスクに保存し、全ペインを停止する。

> **curator 不在は正常系（オンデマンド化）**: キュレーターは常駐しない。state.db の
> `curator_pane_id` / `curator_peer_id` は null が正常で、`list_panes` / `list_peers` に
> curator が見えないことは異常ではない。curator ペインが存在するのは「worker クローズ起点の
> オンデマンド curate が実行中に suspend が重なった」一時的なケースのみで、その場合だけ
> Phase 4 の停止対象に含める。

> **責務境界（/org-suspend と [`/org-down`](../org-down/SKILL.md)）**: /org-suspend は
> 「状態保存 + ja 管理下の補助プロセス（dashboard / secretary_queue_watcher / attention
> watcher）とペインの停止」までを担い、**`claude-org-runtime org down`（broker daemon の停止）は
> 呼ばない**。suspend 単体は「また `/org-start` で再開する」前提の中断であり、broker daemon は
> 走らせたままにする（端末を閉じても daemon はすぐ再開できるよう生存する）。daemon ごと完全に
> 落とすのは [`/org-down`](../org-down/SKILL.md) の責務で、/org-down が suspend の成功を確認した
> 後にのみ `org down` を実行する。

ペイン操作は `mcp__org-broker__*` MCP ツール経由で行う。pane_exited
相当の lifecycle イベントは `mcp__org-broker__poll_events` で long-poll、画面スクレイプ
は `mcp__org-broker__inspect_pane` で取得、raw キー入力は `mcp__org-broker__send_keys`。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の `mcp__org-broker__*` 呼び出しは **既定 `broker`**（`ORG_TRANSPORT` 無設定）で書いてあり、そのまま従えばよい（既定挙動）。`ORG_TRANSPORT=renga`（opt-in・切戻し可）では MCP サーバー名が `renga-peers` になり、ツールの **完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので手順の論理は変わらない）。輸送依存で手順が変わる点だけ renga 併記する:
>
> - **受信モデル**: 既定 broker は **push 一次**（各ペイン同居の channel sidecar `server:org-broker-channel` が broker キューを ~1 秒間隔で claim→`notifications/claude/channel` で idle セッションへ本文注入。pull = ナッジ + `check_messages` は sidecar 不在 / unhealthy / channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時のフォールバック層）。`ORG_TRANSPORT=renga` 時は dispatcher / worker メッセージが `<channel source="renga-peers" …>` として in-band で push される。
> - **spawn 儀式**: 既定 broker は `--mcp-config <broker>` 注入による Claude Code **folder-trust プロンプト**の `send_keys(enter=true)` 機械承認に加え、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプトを `send_keys(enter=true)` で機械承認する（2 段承認）。`ORG_TRANSPORT=renga` 時は `--dangerously-load-development-channels server:renga-peers` の「Load development channel?」を Enter 承認する 1 段。
> - **エラー分岐**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8 + push-primary amendment（broker push 一次が **既定の契約**、pull は fallback として retain）。**opt-in `renga` は削除せず常時有効な切戻しの安全装置**として維持する。broker 実走（dogfood）は Epic #6 Issue G スコープで本ファイルの既定運用経路ではない（**二フレーム注記（Refs #604）**: ここの「既定 `broker`」は**コード既定**（`tools/transport.py: DEFAULT_TRANSPORT`、生成面はこれで render）。**運用既定**は broker dogfood が Epic #6 Issue G まで未活性のため `renga` で、両者は指す対象が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)。）

## Phase 1: ワーカー状態収集

1. **id 台帳を作る**。messaging とペイン制御は**別々の面**なので、id もそれぞれの面から取る
   （契約 Surface 4 の proposed amendment T-§4.2 は「到達性は面ごとに別々に確立する」ことを MUST に
   している。`list_peers` にレコードがあることは、そのピアがペイン制御で addressable であることの
   証拠にならない）:
   - `mcp__org-broker__list_peers` — **messaging 用の peer id**。各レコードの `id` / `name` / `role` / `cwd` と、
     マルチタブ対応 backend が返す optional な `same_tab` / `tab` / `tab_name` を控える（この 3 つは
     `cross_tab_peers` capability を広告する backend でのみ載る。非広告 backend では欠落する）
   - `mcp__org-broker__list_panes` — **ペイン制御用の pane id**。各レコードの `id` / `name` / `role` を控える
     （caller のタブのペインだけが返る）
   - 2 つを突合して 1 エントリ =
     `{peer_id, pane_id（割り当てられない場合は未設定）, name, role, cwd, same_tab, tab}` の台帳にする。
     **`to_id` には必ず `peer_id`、`target` には必ず `pane_id`** を渡す。2 つの `id` が同じ値空間だとは
     仮定しない（backend 定義。契約 §4.1 は id を "opaque" と規定しており、面をまたいだ流用は契約が
     保証していない）
   - **突合（join）キーは `name` だけで、`role` は使わない**。`role` は非一意で（worker が複数居れば
     必ず衝突する）join キーとして成立しない。`name` も**全タブ列挙では一意ではない**ので
     （契約 T-§2.2: §1.8 の名前一意性は "this tab" 限定。`worker-{task_id}` 等の予約名は 2 org 並走で
     構造的に衝突する）、`name` で `pane_id` を割り当てられるのは**同タブであることが確定したエントリに
     限る**。2.0 系の `list_peers` は**全タブ**を返す一方 `list_panes` は**caller のタブだけ**を返す
     （契約 §1.5、本 amendment で非 supersede）ため、ここを誤ると別タブのピアのレコードに自タブの同名
     ペインの `pane_id` が付き、Phase 2 が**別ワーカーのコンソールをスクレイプ**し Phase 4 の lifecycle
     追跡が壊れる:
     - `same_tab == True` が確定したレコード → `name` で `pane_id` を割り当ててよい
     - `same_tab == False` のレコード → **`pane_id` を絶対に割り当てない**（pane 制御面では到達できない。
       契約 T-§4.2）
     - 2.0 系だが `same_tab` 欠落（‡ のケース）→ 「判定できない場合は他タブ扱いに倒す」規則どおり
       **`pane_id` を割り当てない**
     - 旧版 fallback の列挙（全レコードで `same_tab` / `tab` が両方欠落）→ `name` join は安全。
       **ただしその安全性は「この列挙が単一タブに閉じているから」であって、`name` join が無条件に
       安全だからではない**（契約 §2.2 ratified の現在タブ列挙。列挙がタブ横断になった瞬間に前提が消える）
     - `pane_id` が**未設定のエントリは「pane 制御では触れない」**ことを意味する。Phase 2 ではスクレイプを
       諦めて git 情報に倒し、Phase 4 では `close_pane` を撃たずに pending に残す（各 Phase の該当箇所と
       同じ結論）
   - この台帳を **Phase 2 / Phase 4 まで引き回す**。宛先解決・pending 集合はすべて台帳エントリを
     primary key にし、`name` は人間可読の補助ラベルに留める
2. 直後の「**宛先選定の真理値表（Phase 1 / Phase 4 共通）**」で broadcast 対象を決める
   （Phase 1 の対象 role 集合は `R = {worker, dispatcher}`）。**版判定が 2.0 系だった場合は、同節の
   operational gate に従って手順 3 の送信前に人間へ報告し、確認を得てから進む**（旧版 fallback では
   gate を発動せず、そのまま手順 3 へ進む）
3. 対象ピアの **`peer_id`** を `to_id` に渡して `mcp__org-broker__send_message` で以下を送信:
   ```
   SUSPEND: 現在の状態を報告してください。
   1. これまでに完了したこと
   2. 変更したファイル（コミット済み/未コミット）
   3. 次にやろうとしていたこと
   4. ブロッカーや未解決の問題
   ```
4. 30 秒間 `mcp__org-broker__check_messages` で応答を待つ（5 秒間隔でポーリング）
5. 応答があったワーカーの報告を、送信先の台帳エントリに紐付けて記録する（受信メッセージの
   `from_id` を `peer_id` と突合し、`name` 一致は補助にする）

### 宛先選定の真理値表（Phase 1 / Phase 4 共通）

`list_peers` の各ピアレコードについて、`same_tab` / `tab` / `role` の 3 つで broadcast 可否と
宛先指定の形を決める。Phase 1（SUSPEND）と Phase 4（SHUTDOWN）はこの 1 つの表を共有し、
**対象 role 集合 `R` だけをフェーズごとに差し替える**:

- **Phase 1**（状態報告の依頼）: `R = {worker, dispatcher}`。curator には状態報告を求めないので含めない
- **Phase 4**（停止指示）: 停止順序どおりステップごとに `R = {worker}` → `R = {dispatcher}` →
  `R = {curator}`（curator ペインが存在する場合のみ）

`role` が**欠落 / `null` / 空文字列 / 未知の文字列**のピア、および `role == "secretary"` のピアは
どの `R` にも入らない。窓口自身が列挙に含まれるかは backend 次第だが（契約 §2.2 は caller を除外すると
書く一方、除外しない実装もありうる）、**自己除外は role gate が担う**ので列挙の挙動に依存しない。
他タブに別 org の `secretary` が居ても同じく `R` 外で除外される。

**版判定は列挙 1 回につき 1 度だけ決める**（レコード単位で切り替えない）。`list_peers` の結果全体を見て、
`same_tab` / `tab` の**どちらか一方でも載っているレコードが 1 件でもあれば、その列挙全体を 2.0 系**として
扱う。全レコードが両方欠落しているときだけ旧版 fallback に落とす（契約 T-§2.2-fields の検出規則は
「backend についての証拠」であり、混在列挙で一部のピアだけ旧版規則を当てるのは禁止）。下表の「版判定」
列は、こうして決まった版のもとで各レコードがどの行に当たるかを読む。

| # | `same_tab` | `tab` | `role` | 版判定 | broadcast | 理由 |
|---|---|---|---|---|---|---|
| 1 | `True` | 値あり | ∈ `R` | 2.0 系 | **する**（`peer_id` 宛・† 同タブ = 所属 signal） | 自タブの対象役割。tab 情報が揃っており同タブが確定している |
| 2 | `True` | 値あり | ∉ `R` | 2.0 系 | **しない** | 役割が対象外 / 不明。SUSPEND・SHUTDOWN は org の役割にだけ意味を持つ |
| 3 | `True` | `None` | ∈ `R` | 2.0 系（片方欠落） | **する**（`peer_id` 宛・† 同タブ = 所属 signal） | `same_tab=True` だけで同タブが確定する。`tab` 欠落は旧版の徴候ではない |
| 4 | `True` | `None` | ∉ `R` | 2.0 系（片方欠落） | **しない** | 役割で除外（版判定より役割判定が優先） |
| 5 | `False` | 値あり | ∈ `R` | 2.0 系 | **identity が取れたときだけする**（`peer_id` 宛・† 他タブ確認・§ 他タブ注記） | 他タブでも `peer_id` 宛なら到達する。名前宛は送信者タブ内でしか解決されないので使わない |
| 6 | `False` | 値あり | ∉ `R` | 2.0 系 | **しない** | 他タブの無関係ペインである可能性が最も高い |
| 7 | `False` | `None` | ∈ `R` | 2.0 系（片方欠落） | **identity が取れたときだけする**（`peer_id` 宛・† 他タブ確認・§ 他タブ注記） | `same_tab=False` だけで他タブが確定する。`peer_id` 宛は `tab` 不明でも成立する |
| 8 | `False` | `None` | ∉ `R` | 2.0 系（片方欠落） | **しない** | 役割で除外 |
| 9 | `None` | 値あり | ∈ `R` | 2.0 系（片方欠落） | **‡ の導出結果による**（同タブなら行 1 と同じ / 他タブなら行 5 と同じ = identity 必須 + §） | `tab` が載る時点で 2.0 系。`same_tab` 欠落は `list_panes` で代替判定できる |
| 10 | `None` | 値あり | ∉ `R` | 2.0 系（片方欠落） | **しない** | 役割で除外 |
| 11 | `None` | `None` | ∈ `R` | **旧版 fallback**（`cross_tab_peers` 非広告。renga 1.4 系および現行 `org-broker` はここ） | **する**（`peer_id` 宛・† 単一タブ = 所属 signal） | tab 情報が無い＝列挙が単一タブに閉じているので、従来どおり role gate だけで決める（現行挙動と同一） |
| 12 | `None` | `None` | ∉ `R` | 旧版 fallback | **しない** | 旧版経路でも未知 role は対象に含めない。役割が判定できないペインへの broadcast は org 外プロセスへの誤爆になる |

**旧版 fallback の発動条件は 1 つだけ**: 列挙内の**全レコード**で `same_tab` と `tab` が **両方 `None`**
のときに限る（行 #11 / #12）。片方でも値が載っていれば 2.0 系として扱う。特に `same_tab=False` は
「他タブに居ることが分かっている」という 2.0 系の積極的な情報であり、旧版扱いして role 判定だけに
戻してはならない（他タブのピアを名前宛で取りこぼす）。

**2.0 系と判定したら、broadcast の前に人間へ報告して確認を取る（operational gate）**: 契約
T-§ratification は「**capability 広告 backend を実際に駆動する初回**は dogfood step として実施し
人間へ報告する」ことを harness 側の MUST にしている。列挙を 2.0 系と判定したということは、その
capability 広告 backend をいま駆動しているということなので、`/org-suspend` は**黙って先に進まない**。
`/org-suspend` は人間が起動する flow なので、人間は画面の前に居る（この gate は現実的に成立する）。

- 報告に必ず含める: **見えているタブ数**（レコードの `tab` の相異なる値の数。`tab` が欠落する列挙では
  「不明」と書く）、**列挙されたピア総数**、**うち他タブ判定（`same_tab=False`、または ‡ で他タブと
  導出）の件数**、および broadcast 対象に選んだピアの `name` / `peer_id` / `cwd` の一覧。人間が誤爆
  リスク（別 org を巻き込まないか）を判断できる材料にする
- **人間の確認を得てから** capability 経路で先へ進む。特に **`same_tab=False` のピア（別タブ＝別 org の
  可能性がある宛先）へ SUSPEND / SHUTDOWN を送る前の確認は必須**で、確認前は 1 通も出さない
- 人間が他タブ分を除外した場合は、同タブ確定のピアだけを対象に続行する
- gate は 1 回の `/org-suspend` につき 1 度（最初に 2.0 系と判定した列挙＝通常は Phase 1 手順 1）でよい。
  Phase 4 の再列挙で**他タブ判定のピアが増えていた**場合だけ、その差分を報告して再確認する
- **旧版 fallback（行 #11 / #12。現在配備されている全 backend — `org-broker` を含む — はここを通る）では
  この gate を発動しない**。従来どおり無停止で broadcast に進む（既存運用は一切変わらない）

**宛先は必ず `peer_id`**: broadcast 対象に決まったピアへは `send_message(to_id="<peer_id>")` で送る。
`to_id` に `name` を渡すと 2.0 系では**名前解決が送信者タブ内に限定される**ため、他タブのピアは
`[pane_not_found]` になる（`list_peers` 由来の id 宛はタブ横断で解決される）。旧版 fallback の行でも
`peer_id` 宛は同じく有効なので、経路を 1 本に揃えるため版によらず常に `peer_id` を使う。

**†（broadcast 対象の自組織確認）**: SUSPEND / SHUTDOWN を送る前に、そのピアが**自組織のもの**である
ことを確認する。**確認の手段はピアが同タブか他タブかで変わる**:

- **同タブのピア（`same_tab=True`、および旧版 fallback の列挙全体）— タブ所属そのものが所属の signal**。
  caller のタブのペイン群は、この org が組み上げたレイアウトである。これは ratified の SINGLE-TAB 規則が
  encode していた前提そのもので、**現行の `/org-suspend` が依拠しているのもこれ**。したがってここでは
  **role gate だけで判定し、追加の identity 一致を要求しない**（要求すると、identity を state.db に記録
  しない役割 — 後述の on-demand curator — を永久に取りこぼす。現在配備の backend の挙動も変わってしまう）
- **他タブのピア（`same_tab=False`、および 2.0 系で同タブと確定できないもの）— identity 一致を要求する**。
  マルチタブ化で初めて「タブ所属が所属を意味しない」相手が列挙に混ざるようになった。ここが本節の
  本題であり、**確認できないピアには送らない**（他組織を巻き込んで中断させないため）

**`cwd` が「この org のツリーの配下にある」ことは自組織の証明にならない**（他タブピアの第一次の識別子に
使ってはならない）。ja root 配下には、この org のワーカー以外にも人間が手で開いたペインや pr-watch ペインが
生えるし、**別 checkout の org のワーカーが、たまたま似た階層構造の配下に居る**こともある。真理値表は
`same_tab=False` のピアにも broadcast するので、「ツリー配下だから自組織」と判定すると
**`/org-suspend` が別 org を停止させる**。したがって他タブピアの第一次は「**このセッションが
`.state/state.db` に記録した identity との一致**」で、`cwd` は補強に降格する。

> **この確認が守れる範囲（前提の明示）**: state.db は **1 org につき 1 つ**である。
> `tools/state_db/discover.py` は worktree を main checkout に解決し直し（"The canonical state.db lives
> in the main checkout, not in worktree-private `.state/` directories"）、`org_sessions` は
> `CHECK (id = 1)` の singleton（`tools/state_db/schema.sql` の「the org never has multiple concurrent
> sessions」）だからである。したがって identity 一致が排除できるのは **別 checkout で走る別 org のピア**
> — マルチタブで現実に混ざる相手 — であり、そこでは相手の task_id はこちらの `runs` に無く、相手の
> ディスパッチャーの `peer_id` は `dispatcher_peer_id` と一致しない。
> **逆に「同一 checkout から 2 つの org を同時に走らせる」構成は state.db のデータモデルが表現できない**
> （`runs.task_id` は org 所有者列を持たない globally UNIQUE、`org_sessions` は 1 行のみ）。その構成では
> 他 org のワーカーも*こちらの* `runs` に載るため、本節の確認では区別できない。**org-suspend はこの構成を
> 区別できると主張しない** — 対応が要るなら schema に所有者列を足す別作業になる。

**他タブピア**について、役割ごとに次を確認する（同タブピアには適用しない — 上記のとおりタブ所属が
signal になる）:

- **worker（第一次）**: `name` の `worker-{task_id}` から取り出した task_id が、この org の
  `.state/state.db` の `runs` に**非終端 status**（`in_use` / `review`。`tools/state_db/schema.sql` の
  CHECK は `queued/in_use/review/completed/failed/suspended/abandoned` の 7 値で、active 集合は
  `tools/state_db/queries.py` の `_ACTIVE_STATUSES`）で存在すること
- **worker（cwd による棄却）**: その task_id に worker ディレクトリが登録されている場合は、ピアレコードの
  `cwd` がその登録エントリと**一致**しなければ broadcast しない（Worker Directory Registry の Directory 列。
  `tools/state_db/snapshotter.py` が DB から `.state/org-state.md` へ再生成する）。**配下に含まれることでは
  なく登録エントリとの一致**を見る。ただし `runs.worker_dir_id` は nullable（`tools/state_db/schema.sql`）
  なので、**登録が無いときは cwd 一致を要求しない** — 要求すると自組織の稼働ワーカーを取りこぼす。
  cwd はここでは「積極的に食い違ったら弾く」棄却条件であって、存在必須の合致条件ではない
- **dispatcher（第一次）**: `.state/state.db` の **`dispatcher_peer_id`**（`org_sessions` の TEXT 列。
  定義は `tools/state_db/__init__.py`）が当該 `peer_id` と一致すること（`list_peers` 由来の id を格納する列はこちら。
  `dispatcher_pane_id` は `list_panes` 由来なので、pane 制御側の id と照合したいときにそちらを使う）
- **curator（確認不能 — 他タブでは送らない）**: on-demand curator の identity は **state.db に書かれない**
  （`org-start` は `curator_pane_id` / `curator_peer_id` を `StateWriter.CLEAR` で明示クリアし、
  [`.dispatcher/CLAUDE.md`](../../../.dispatcher/CLAUDE.md) は「state.db に**書かない**（null が正常系。
  生存確認は `list_panes` のみ）」と規定する）。したがって**他タブの curator は原理的に自組織確認が
  できない**ので broadcast しない。**同タブの curator はタブ所属で確認できるので通常どおり SHUTDOWN を
  受け取る**（`list_panes` に `role == "curator"` として現れる。オンデマンド curate と suspend が
  重なったケースはこの経路で従来どおり graceful に停止する）。他タブに curator が見えた場合は
  停止できない残存として人間に報告する
- **`cwd` の位置づけ**: `cwd` は**棄却にだけ**使う（記録と積極的に食い違ったら弾く）。**単独では自組織の
  証明にならない**ので、identity 一致が取れていない他タブピアを `cwd` だけを根拠に broadcast してはならない
- **identity が取れない他タブピアには broadcast しない**（安全側）。`role` が対象でも送らず、
  停止できない残存として人間に報告する

**§（他タブピアの停止確認に関する注記）**: `same_tab=False` と判定されたピアは、messaging では到達
できても**ペイン制御では到達できない**（契約 T-§4.2: pane 制御は caller のタブ内に留まる）。Phase 4 で
このピアの `close_pane` が `[pane_not_found]` を返しても**閉鎖の証拠にはならない**ので、Phase 4 の
「`[pane_not_found]` の扱い」節の他タブ例外に従う。

**‡（`same_tab` 欠落時の代替導出）**: `same_tab` が無く `tab` だけ載る backend では、`mcp__org-broker__list_panes`
（caller のタブのペインだけを返す）に当該ピアが `name` / `role` で見つかるかで同タブ / 他タブを判定する。
判定できない場合は**他タブ扱いに倒す**（§ の制約を適用する安全側）。

## Phase 2: 未応答ワーカーのスクレイプ

応答がなかったワーカーについて:

1. `.state/workers/` から該当ワーカーの状態ファイルを読み、Pane Name と Directory を取得
2. Pane Name を Phase 1 の**台帳**で **`pane_id`** に解決する。`inspect_pane` はペイン制御面なので、
   使うのは `list_panes` 由来の `pane_id` であって `peer_id` ではない:
   - 台帳に `pane_id` が無い（＝ `list_panes` に居ない）ピアは、**同タブに存在しない**。
     `same_tab=False` / ‡ で他タブと導出されたピアなら「他タブに居るのでスクレイプ不能」、
     それ以外なら「消滅済み」。いずれもスクレイプは諦め、Step 4 の git 情報だけで状態を推定する
     （**他タブ = 消滅と読み替えない**。前者は生存している可能性がある）
   - Phase 1 から時間が経っているので、使う直前に `mcp__org-broker__list_panes` を取り直し、その
     `pane_id` の `name` / `role` が台帳と一致することを確認してから叩く。**一致しなければ
     pane_id が別ペインへ再割当てされている**ので、そのペインには触れずスクレイプを諦める
     （Phase 3.7 と同じ identity 照合の規律。無関係なペインの画面を当該ワーカーの
     「Current State at Suspend」に書き込むのを防ぐ）
3. 画面内容スクレイプで最新のコンソール出力を読む:
   ```
   mcp__org-broker__inspect_pane(target="<pane_id>", format="text")
   ```
   `target="worker-{task_id}"` の**名前指定はしない** — 2.0 系では名前解決が呼び出し側のタブ内に
   限定され、同名ペインが別タブに存在しうるため。画面表示だけでは不十分な場合は、次の Step 4 の
   git 情報で補完する
4. ワーカーの作業ディレクトリで以下を実行:
   - `git status`
   - `git diff --stat`
   - `git log --oneline -5`
5. これらの情報からワーカーの状態を推定する

## Phase 3: 状態書き込み

> **state-db cutover (M4, Issue #267)**: `.state/state.db` が唯一の SoT。
> 構造化セクション (Status / Updated / Suspended / Dispatcher / Curator /
> Worker Directory Registry / Active Work Items / Resume Instructions) は
> **必ず StateWriter 経由で書く**。`transaction()` の post-commit hook が
> `.state/org-state.md` を DB から自動再生成する (markdown 直接編集禁止 —
> drift_check で検出される)。free-form な session notes / Pending Lead /
> 学び等は `notes/` 配下に保存する (`notes/README.md` 参照)。
> `.state/journal.jsonl` は M4 で廃止 (events テーブルが SoT)。
> DB が古い場合は
> `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict`
> で再構築する。

1. 既存の `org-state.md` を `org-state.prev.md` にコピー（バックアップ）
2. **DB に Status / Suspended を書く** (`StateWriter.transaction()` 経由。post-commit hook が `.state/org-state.md` を自動再生成、regen 失敗時も DB は確定済みで stderr 警告のみ):

   ```bash
   python -c "
   from datetime import datetime, timezone
   from pathlib import Path
   from tools.state_db import connect
   from tools.state_db.writer import StateWriter
   ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%fZ')
   conn = connect('.state/state.db')
   with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
       w.update_session(status='SUSPENDED', suspended_at=ts, updated_at=ts)
   "
   ```

   - bash / zsh / PowerShell すべて `"..."` 内の改行をそのまま透過するので multi-line でも cross-shell。Windows CMD は heredoc 不可なので `py -3 -c "ts=...; conn=...; w=...; w.begin(); w.update_session(...); w.commit()"` の単行 fallback を使う（その場合 `transaction()` の rollback / regen 自動 swallow は失われるので追加で try/except を書く）
   - 同コマンドが `.state/org-state.md` の Status 行を `SUSPENDED` に切り替える (DB 由来で再生成)
   - free-form な「Resume Instructions の補足説明」「Pending Lead」「学び」等は **`notes/` に保存する** (`notes/README.md` 参照)。markdown 直接編集は drift_check で検出される。`update_session(resume_instructions=...)` は構造化セクションとして DB に書く
3. 各 Work Item の状態を更新する場合は `upsert_run(task_id=..., status=...)` を `transaction()` 内で呼ぶ
4. 各ワーカーの `.state/workers/worker-{id}.md` を更新:
   - Current State at Suspend セクションを追加/更新
   - Progress Log に中断時の状態を追記
5. suspend イベントを DB に追記 (`tools/journal_append.py` は M4 で DB-only ルーティング。`ts` は自動付与):
   ```bash
   py -3 tools/journal_append.py suspend \
       reason=user_requested \
       --json '{"active_workers": ["worker-xxx"], "pending_items": ["blog-redesign"]}'
   ```
   event 名と payload key の規約は [`docs/journal-events.md`](../../../docs/journal-events.md) を参照。

## Phase 3.5: ダッシュボードサーバー停止

```bash
kill $(cat .state/dashboard.pid 2>/dev/null) 2>/dev/null || true
```

> **注**: この blind kill は「/org-start で再開する前提の中断」なので簡素なまま残す。daemon ごと
> 落とす [`/org-down`](../org-down/SKILL.md) では、pid recycle による誤 kill を避ける stale-pid-safe な
> 停止（`/proc` / `Get-CimInstance` の CommandLine 照合）に差し替える。

## Phase 3.6: secretary_queue_watcher の停止（broker のみ）

broker 面（`ORG_TRANSPORT=broker`）で org-start Block C3 が `run_in_background` で
常駐させた滞留 watcher を停止する。**renga では watcher が存在しない**（queue.jsonl 非依存）ので、
transport が `renga` なら本 Phase は**まるごと skip**する。

watcher は起動時に `.state/secretary_queue_watcher.json` へ自分の pid / cwd / cmdline / started_at /
broker_state_dir を記録している。停止は **pid 単独で kill せず**、(a) 記録された broker_state_dir が
現在の `ORG_BROKER_STATE_DIR` と一致し（別 org / 別 broker の watcher 誤停止防止）、かつ (b) pid が
生存し live argv（Linux/WSL は `/proc/<pid>/cmdline`、macOS/BSD は `ps -p <pid> -o args=` フォールバック）が
本 watcher であることを照合できたときだけ SIGTERM する。照合が外れたら kill せず sidecar を stale として
削除する（誤 kill 防止）。この照合ロジックは helper に入っているので、POSIX では 1 行呼ぶだけでよい:

**Mac / Linux / WSL**:
```bash
python3 tools/secretary_queue_watcher.py --stop   # Windows で console python を使う場合は py -3 ...
```
出力の 1 行（`STOP: ...`）で結果を確認する（`SIGTERM を送信し停止` / `stale sidecar を削除` /
`既に停止済み`）。exit 0 が正常系（停止・stale 掃除・既停止のいずれも 0）。macOS は `ps` フォールバックで
identity 照合できるので `--stop` がそのまま効く。exit 2（identity 未確認）は `/proc` も `ps` も無い環境
（Windows native）でのみ出るシグナルで、その場合は次の PowerShell 手順を使う。

**Windows native（PowerShell）** — `/proc` が無く helper の argv 照合が使えないので、
`Get-CimInstance Win32_Process` の CommandLine で identity を照合してから `Stop-Process` する
（`kill -0` / `kill -TERM` の直訳ではなく Windows 別手順）:
```powershell
$pf = ".state\secretary_queue_watcher.json"
if (Test-Path $pf) {
  $rec = Get-Content $pf -Raw | ConvertFrom-Json
  $wpid = [int]$rec.pid
  $ownOk = $false
  try {
    if ($env:ORG_BROKER_STATE_DIR) {
      $ownOk = ((Resolve-Path $rec.broker_state_dir).Path -eq (Resolve-Path $env:ORG_BROKER_STATE_DIR).Path)
    } else {
      $ownOk = ((Resolve-Path $rec.cwd).Path -eq (Get-Location).Path)
    }
  } catch { $ownOk = $false }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$wpid" -ErrorAction SilentlyContinue
  $idOk = $proc -and ($proc.CommandLine -match 'secretary_queue_watcher\.py')
  if ($ownOk -and $idOk) {
    Stop-Process -Id $wpid -Force
    Write-Output "secretary_queue_watcher (pid=$wpid) stopped"
  } else {
    Write-Output "watcher pid stale / different org / not running; not killing, removing stale sidecar"
  }
  Remove-Item $pf -ErrorAction SilentlyContinue
}
```

## Phase 3.7: attention watcher の停止（ペイン teardown の前）

attention watcher は dispatcher ペインの右 split に常駐する CLI ペインなので、**Phase 4 のペイン
一括 teardown より前に**停止する（dispatcher を先に閉じると attention ペインが孤児化 / pane_id
recycle され、後続の識別を壊すため）。attention watcher を起動していないセッションでは sidecar も
live pane も無く、本 Phase は no-op。

停止は [`/org-attention-stop`](../org-attention-stop/SKILL.md) と同じ **identity 照合**を使う
（sidecar の pane_id を無検証で `close_pane` しない。pane_id が別ペインへ再割当てされていると
無関係なペインを kill する — Issue #468）:

1. `mcp__org-broker__list_panes` で `name="attention"` **または** `role="attention"` の live pane を
   **全て**集める（= 確認済み attention ペイン集合。各レコードの **`id`（= pane 制御用の pane_id）** を控える）
2. `.state/attention_pane.json` を `Read` で開けたら `pane_id` を読む（= sidecar pane_id）。無ければ
   「sidecar 無し」
3. sidecar pane_id を **list_panes が返す name/role** で分類する（sidecar 記録の name は信用しない）:
   - **verified**（確認済み集合に含まれる）→ その pane はいまも本物の watcher。close 対象
   - **recycled**（list_panes にあるが name/role が attention でない）→ pane_id が別ペインへ再割当て済み。
     **絶対に close しない**
   - **gone**（list_panes に無い）→ 既に消滅。close しない
4. 確認済み集合の各ペインを **`list_panes` 由来の `pane_id`** で `mcp__org-broker__close_pane(target="<pane_id>")` する
   （`target="attention"` の name 指定はしない — role だけ持つ孤児に当たらないため）。
   ここは **pane 制御文脈**で、かつ対象は `mcp__org-broker__list_panes` 由来＝ caller のタブに居ることが確定
   しているので、`[pane_not_found]` / `[pane_vanished]` は既に閉じた扱いで skip してよい（close の
   目的は消滅であり、既に消えていれば結果は同じ。他タブのペインに対する例外と messaging 文脈での
   扱いは Phase 4 の「**`[pane_not_found]` の扱い — messaging と pane 制御で分岐する**」を参照）
5. sidecar があれば **分類によらず必ず削除**する:
   ```bash
   rm -f .state/attention_pane.json     # Windows native は del .state\attention_pane.json
   ```
6. journal に 1 行追記する。**実際に close したペインがある場合のみ** pane_id を載せ、recycled / gone で
   close を 1 つも行わなかった場合は `reason=stale_sidecar` にする（無関係なペインを停めた誤記録を防ぐ）:
   ```bash
   bash tools/journal_append.sh attention_watch_stopped pane_id=<N>          # close した場合
   bash tools/journal_append.sh attention_watch_stopped reason=stale_sidecar # close しなかった場合
   ```

分類別の詳細な挙動と報告文は [`/org-attention-stop`](../org-attention-stop/SKILL.md) を参照（本 Phase は
その要点を suspend フローに埋め込んだもの）。

## Phase 4: 全ペイン停止

停止順序が重要。ワーカー → ディスパッチャー → キュレーターの順で停止する。

**このフェーズは全ステップを Phase 1 の台帳の id で回す**: `mcp__org-broker__send_message` の `to_id` は
`peer_id`、`mcp__org-broker__close_pane` の `target` は `pane_id`、pending 集合と `poll_events` の突合は台帳
エントリを主キーにする。`name` は人間可読のラベルとしてのみ扱い、宛先解決には使わない — 2.0 系では
名前解決が呼び出し側のタブ内に限定され、同名ペインが別タブに存在しうるため。

### `[pane_not_found]` の扱い — messaging と pane 制御で分岐する

本フェーズ（および Phase 1 / Phase 2）の送信・ペイン制御すべてに適用する分岐。分岐の**第一軸は
呼び出し文脈**、**第二軸は同タブ / 他タブ**である（世代では分けない）。正本は
[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md)。

- **messaging 文脈**（`mcp__org-broker__send_message`。**broker では `[peer_not_found]`** が同じ意味で返る）:
  受けても**即「ワーカー閉鎖」と断定しない**。2.0 系では名前解決が送信者タブ内に限定され、また
  宛先台帳が古く id が再割当て / 消滅していると、生存中のペインでもこのコードが返るため、次の順で
  切り分ける:
  1. `mcp__org-broker__list_peers` を取り直す（2.0 系は全タブ列挙なので他タブのワーカーもここで拾える）
  2. 得られた **`peer_id`** で **1 回だけ**再送する（ループにしない — 2 回目以降も結果は同じで、
     本当に閉じていた場合の検知が遅れるだけ）
  3. **`peer_id` 宛でも失敗したときに初めて** lifecycle **確認**へ進む。ここで確認を省いて
     いきなり閉鎖確定にしない（契約 T-§2.1 の復旧順は確認段を含む）:
     - 同タブのピア: `mcp__org-broker__list_panes` に居ないこと、または `pane_exited` の観測をもって消滅を確定
     - **他タブのピア**（`same_tab=False` / ‡ で他タブと導出）: `list_panes` には原理的に出ないので
       不在は証拠にならない。`pane_exited` の観測か、後続 `mcp__org-broker__list_peers` からの消失で確定する
     - どちらも取れなければ **indeterminate**。閉鎖に倒さず journal に記録して人間に報告する
  4. 消滅が確定したら pending 集合から外す。**worker 状態ファイルの `Status: pane_closed` は窓口が
     書かない** — この terminal transition の書き手はディスパッチャーである
     （[`docs/contracts/state-semantics-contract.md`](../../../docs/contracts/state-semantics-contract.md)）。
     窓口は Progress Log への追記と journal 記録に留める
- **pane 制御文脈**（`mcp__org-broker__close_pane` / `mcp__org-broker__inspect_pane` / `mcp__org-broker__list_panes` の target）:
  - **同タブのペイン**に `pane_id` 宛で `[pane_not_found]` / `[pane_vanished]` が返ったら、従来どおり
    「既に閉じた」扱いで skip してよい（close は消滅させるのが目的なので、既に消えていれば結果は同じ）
  - **他タブのペインは例外 — 閉鎖と読んではならない**。契約 T-§4.2 はタブ横断の pane-addressed
    control が `pane_not_found` を返すと規定しているので、**生存中でも同じコードが返る**。
    このコードを「閉じた」と読むと、稼働中のワーカーを残したまま「全ペイン停止」を完了報告する。
    該当ピアは pending に残し、journal に記録して人間に報告する（停止できない残存）
  - 名前宛で受けた場合はこの結論を出さず、`pane_id` に解決し直してから判断する

### 停止手順

1. `mcp__org-broker__list_peers` と `mcp__org-broker__list_panes` を取り直し、Phase 1 の**台帳を最新化**する（Phase 1 から
   時間が経っているため、`peer_id` / `pane_id` の両方を取り直す）。再構築でも Phase 1 手順 1 の
   **join 規則をそのまま適用する**（`role` は join キーにしない / `name` join は同タブ確定エントリだけ /
   他タブ・`same_tab` 欠落のエントリには `pane_id` を割り当てない）。**他タブ判定のピアが Phase 1 から
   増えていた場合は、真理値表の operational gate に従ってその差分を人間に報告し、確認を得てから
   停止指示へ進む**
2. **ワーカーを先に停止**: Phase 1 の**宛先選定の真理値表**を `R = {worker}` で適用し、選ばれた
   ワーカーの **`peer_id`** を `to_id` に渡して `mcp__org-broker__send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
3. **ワーカーペインが閉じたことを確認** — 2-pass 構造で実施:

   **Pass 1 (polite shutdown の観察、最大 10 秒)**:

   `mcp__org-broker__poll_events` で `pane_exited` を long-poll する。`types=["pane_exited"]` フィルタで他 type を除外しつつ、deadline 内でループして待機対象が全て閉じたら break:
   ```
   pending = {SHUTDOWN を送った全ワーカーの台帳エントリ set}
   cursor = None                           # 初回は since 省略
   deadline = now + 10 秒
   while pending not empty and now < deadline:
       remaining_ms = (deadline - now) ミリ秒
       result = mcp__org-broker__poll_events(
           since=cursor,
           timeout_ms=min(remaining_ms, 10000),
           types=["pane_exited"]
       )
       cursor = result.next_since
       for ev in result.events:
           # 突合は id で行う。フィールド名は backend 差があるので候補を順に見る
           ev_pane = ev.id ?? ev.pane_id          # list_panes と同じ id 空間
           ev_peer = ev.agent_id ?? ev.peer_id    # list_peers と同じ id 空間
           hit = pending の中で entry.pane_id == ev_pane または entry.peer_id == ev_peer
           if hit is None and ev.name is not None:
               # name フォールバックは「同タブ確定エントリ」に限定する（下記）
               hit = pending の中で entry.name == ev.name かつ entry が同タブ確定
           if hit: pending.remove(hit)
   # deadline 到達 or pending が空で抜ける
   ```
   - **突合キーは id**。ただし `poll_events` の per-event フィールド名は契約 §3.1 が規定しておらず
     backend 差がある（renga 面は `type` / `role` / `name` / `id` / `ts`、`pane_id` / `agent_id` を
     載せる backend もある）ので、**pane 側 id と peer 側 id の候補を順に見て、どちらか一方でも
     一致したら hit** とする。契約側の規定は proposed amendment T-§3.1
   - **`name` 単独を突合キーにしてはならない**。2.0 系の名前はタブ内一意でしかないので、別タブの
     同名ペインの `pane_exited` で自組織の**生存**ワーカーを誤って retire する。`name` フォールバックは
     `same_tab=True` / 旧版 fallback で**同タブと確定しているエントリ**に限って使う（他タブ対象が
     pending に居る間は name フォールバックを無効にしてもよい）
   - 初回 `since` 省略で「今以降のイベントだけ」セマンティクス（過去の pane_exited を replay しない）
   - `types=["pane_exited"]` filter は cursor を全 type で advance させるので重複 scan なし
   - filter 不一致イベント到着で long-poll が early return (`events:[]` + advanced cursor) するため、空応答時は deadline までループ継続
   - 10 秒以内に閉じなかった残留ワーカーは Pass 2 へ

   **Pass 2 (残留ワーカーへのフォールバック + 再確認、最大 5 秒)**:
   - `pending` に残っているエントリのうち、**`pane_id` を持つもの**（＝ caller のタブに居るペイン）
     それぞれに対して:
     ```
     mcp__org-broker__close_pane(target="<pane_id>")
     ```
     でペインを明示破棄する（`target="worker-{task_id}"` の名前指定はしない）。成功時は `"Closed pane id=N."` テキストが返る。ここは **pane 制御文脈**なので `[pane_not_found]` / `[pane_vanished]` は既に閉じた扱いで skip してよい（[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) 参照）。`[last_pane]` はワーカー停止段階では通常発生しない（窓口/ディスパッチャー/キュレーターが残っているため）
   - **`pane_id` を持たないエントリ（他タブのワーカー）には `close_pane` を撃たない**。タブ横断の
     pane 制御は原理的に不可で、返る `[pane_not_found]` は閉鎖の証拠にならない（契約 T-§4.2）。
     このエントリは pending に残したまま次の再確認へ送る
   - その後、同じ `poll_events` ループを `timeout_ms=5000` / deadline 5 秒で再度回し、close_pane 由来の `pane_exited` を消化する
   - Pass 2 後もまだ `pending` が空でなければ `mcp__org-broker__list_panes` / `mcp__org-broker__list_peers` で生存確認する（**id 突合**。`list_panes` は呼び出し側のタブしか返さないため、他タブのワーカーは `list_peers` からの消失で判定する）。**残存または判定不能なら**、閉鎖扱いにせず journal に記録して人間に報告する（強制終了は現状未サポート。「停止できなかったワーカーが居る」ことを報告に明記し、全ペイン停止を偽って完了報告しない）

4. **ディスパッチャーを停止**: 真理値表を `R = {dispatcher}` で適用し、選ばれたディスパッチャーの
   **`peer_id`** を `to_id` に渡して `mcp__org-broker__send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
5. **キュレーターを停止（存在する場合のみ）**: curator は常駐しないため、通常このステップは
   no-op。curator の存在確認は `mcp__org-broker__list_peers` の `role == "curator"`（`name == "curator"` は補助）
   で行い、**`peer_id`** を得る（`list_panes` は呼び出し側のタブしか返さないので、別タブで起動した
   curator を取りこぼす）。存在した場合（オンデマンド curate 実行中に suspend が重なったケース）
   のみ真理値表を `R = {curator}` で適用し、その `peer_id` 宛に `send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」（curate は move-then-mark 設計のため途中停止でも
   破壊的な中間状態は残らない）
6. ディスパッチャー（および存在した場合のみキュレーター）も (3) と同じ 2-pass 構造で確認する。
   pending 集合は名前ではなく**台帳エントリの集合**にする（`pending = {dispatcher のエントリ}`、
   curator が存在した場合はそのエントリも追加）。`pane_exited` の突合も (3) と同じく
   pane 側 id / peer 側 id のいずれか一致で行い、`ev.role == "dispatcher"` / `"curator"` と
   `ev.name` は同タブ確定エントリに対する補助に留める:
   - Pass 1: `poll_events(types=["pane_exited"], timeout_ms=10000)` 相当ループ
   - Pass 2: `pending` に残ったエントリのうち **`pane_id` を持つもの**に
     `mcp__org-broker__close_pane(target="<pane_id>")` を送り（名前指定はしない）、`poll_events` ループ
     (timeout_ms=5000) で再確認。`pane_id` を持たない（他タブの）エントリには撃たず、(3) と同じく
     残存として人間に報告する

**最後のペイン (窓口) の扱い**: ディスパッチャー（と存在した場合のキュレーター）を閉じた時点で、
窓口ペインが窓口タブに残る最後のペインになる。**窓口は自分自身を `mcp__org-broker__close_pane` しない**。
その根拠は次の 2 つで、どちらも「最後の 1 ペインだから backend が閉じさせてくれない」ことには
依存しない**構造的な理由**である:

- **窓口は人間との唯一の接点である**: 自分を閉じると、この後の「人間に報告」ステップで suspend 完了を
  渡す相手の画面が消える。報告前に自分を殺すと、人間から見て「中断できたのか分からないまま画面が
  消えた」になる
- **自己終了は人間の責務境界にある**: 窓口プロセスの終了は人間の `exit` / `/exit` / 端末クローズに
  委ねる。org-suspend が担うのは「状態保存 + ja 管理下の補助プロセスとペインの停止」までで、窓口
  ペインを閉じる責任は負わない（冒頭の「責務境界（/org-suspend と /org-down）」で引いた線と同じ）

`[last_pane]` は**エラーハンドリングとしてのみ残す**: 何らかの経路で窓口ペインに `close_pane` が飛び
`[last_pane]` が返った場合は、**強制再試行せず**そのまま人間の `exit` に委ねる。ただしこのコードを
自己 close の安全網として当てにしてはならない — このコードの意味は
[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md)
のとおり「**唯一のタブの**唯一のペイン」であり、他のタブが存在する状況で窓口タブの最後のペインが
保護されるかは 2.0 系では未確認である（保護されない読みが素直だが、いずれにせよ当てにしない）。
自己 close をしない根拠は上の 2 つだけである。

7. 人間に報告:
   ```
   組織を中断しました。
   - 保存済み: {N}件の作業アイテム
   - 状態ファイル: .state/org-state.md
   /org-start で再開できます。
   ```
