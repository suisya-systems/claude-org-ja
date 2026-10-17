# 背景タブに置いた worker の生存判定 — tracked-any-tab (id 束縛)

`.state/dispatcher/worker-idle-state.json` に `placement == "background_tab"` の record があるサイクルでだけ読む。[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 3 の (3-a-5) を切り出したもので、本文中の Step 1 / Step 3 / Step 4 / Step 5、(3-a-1)〜(3-a-4)、(P1)〜(P5) は同ファイルの節を指す。

## 目次

- 適用対象と輸送層
- 判定の入力（3 入力と裏取り面）
- session provenance と identity 再照合
- 毎サイクルの三値
- 全ペイン index（裏取り専用）と発動ゲート・解除手順
- 終了確定・画面監視・追加の待ち時間

## 適用対象と輸送層

**適用対象は `placement == "background_tab"` の record を持つ worker だけ**である ((3-a-3) の schema。欠損は `"same_tab"` 扱いなので、通常委譲で spawn した worker は 1 件も入らない)。この配置は [`.dispatcher/references/spawn-flow.md`](spawn-flow.md) 3-1d の条件を全て満たすタスク、または同節が記す人間承認の例外経路でのみ発生し、**通常委譲経路の同一タブ MUST は一切変わらない**。同タブ worker の消失評価は従来どおり (3-a-1)〜(3-a-4) が owner である。**本項は renga 面専用**でもある — 3-1d の条件 2 が transport を `renga` に限っており (broker surface に `tab` セレクタも `new_tab` も無い)、broker 運用では `placement == "background_tab"` の record がそもそも生まれないので本項は 1 度も評価されない。

**したがって本項の tool 名だけは輸送層の機械置換の対象外で、`mcp__renga-peers__*` を literal で書く (MUST)**: 本ファイル冒頭の両系注記は「完全修飾名を使用中 transport のものに読み替える」と定めるが、**背景タブ配置は renga でしか成立しない**ので、ここで broker 名に読み替えると「そのペインを作った renga session ではなく broker の状態を問い合わせる」ことになり、session provenance 照合も生存判定も成立しない。本項および Step 3 / Step 4 の背景 worker 向け bullet に literal で `mcp__renga-peers__` と書いてあるのはこの理由による (読み替えない)。

**共有 reference §1-2 を背景 worker に流用してはならない (MUST NOT)**: [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md) §1-2 は自身を「対象名 N のピアが **自タブに** 生存しているか」を判定する規範と定義しており、その真理値表は `same_tab == False` を「他タブ」→ §1-2-c #4 の**不在**に落とす。背景タブに置いた worker は定義上つねに `same_tab == False` なので、§1-2 を当てると毎サイクル「不在」と読まれ、spawn-flow 3-4 の登録ゲートは**永久に開かない**。同じ理由で **`same_tab_peer_id` を背景 worker に流用してはならない** — あのフィールドは「自タブに 在 と確定したレコードの id」という意味を持ち (3-a-2) 4 行目の裏取り (ii) の入力になるので、別の意味の id を入れると同表が誤って終了確定へ進む。背景 worker が使うのは別フィールドの `bound_pane_id` / `bound_peer_id` である。
（§1-2-e の「call site に独自の same-tab 規範を書いてはならない」は **同タブ生存判定** についての MUST NOT であり、本項は主語が違う = **背景タブに置いたペインの生存判定**なので抵触しない。同タブ判定の正本は引き続き §1-2 のみで、本項はそれを再掲も改変もしない。）

## 判定の入力（3 入力と裏取り面）

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

## session provenance と identity 再照合

**session provenance を毎サイクル確かめる — 束縛した id を使う前の前提条件 (MUST)**: 数値 pane id の一意性保証は **backend session に閉じている**。契約 T-§4.2-id は renga の id counter について「Nothing persists or restores it … **across a daemon restart they are reissued from the beginning**」と書き、restart を跨いで使った id について「is not addressing a stale pane — it is addressing **a different, live pane**, and the backend answers **successfully**. No error code marks this」と続ける。**成功応答が返るので、照合しない限り取り違えに気付けない。** したがって背景 worker を評価するサイクルでは、`list_peers` を引くのと同じ 1 回の branch で `mcp__renga-peers__server_info` を引き、`server.pid` を record の `bound_server_pid` と突き合わせる:

- **一致した (`server.pid` が `bound_server_pid` と、`server.endpoint` が `bound_server_endpoint` と、両方)** → **それだけでは足りない (下記)**。第 2 面の identity 再照合まで通ったときに `bound_pane_id` / `bound_peer_id` を使ってよい
- **一致しない / `server_info` が `status != "connected"` を返す / `bound_server_pid` ・ `bound_server_endpoint` ・ `bound_cwd` のいずれかが `null`** → **束縛は失効している。下表の三値評価に入らず、`bound_pane_id` / `bound_peer_id` を宛先にも判定にも一切使わない**。当該 worker を **indeterminate** として (3-a-4) の 4 点に載せ、(P4) を `source=session_provenance` で 1 回報告する。**裸の `name` や相対セレクタへ降格してはならない** (同節 **(O4)**: 「MUST NOT issue or re-issue it under a relative selector, a bare `name`, or any widened search」)。この形は自然回復しないので、(3-a-4) の「窓口の reconcile 判断が要る」通知文に載せる (新しい escalation 経路は作らない)

**pid 等値は必要条件であって十分条件ではない — identity を毎サイクル再照合する (MUST)**: OS は daemon 終了後に同じ pid を再利用しうるので、「renga が restart し、pane id を再発行し、たまたま同じ pid を得た」形では pid 等値が**通ってしまう**。これは本照合が防ごうとしている当の事故 (別の生きたペインを成功裏に撃つ) なので、pid + endpoint の等値だけで先へ進んではならない。**独立した第 2 面として、本サイクルの `list_peers` index で `bound_peer_id` を引いたレコードが `name == "worker-{task_id}"` かつ `role == "worker"` かつ `cwd == bound_cwd` を満たすことを毎サイクル確かめる** (`cwd` が **org 束縛**を担う — 予約名は別 org の並走タブに同名で実在するので `name` / `role` だけでは「自 org のペインである」ことを示さず、同節が求める「bound to the expected organisation」を discharge できない) (契約 T-§4.2-id の **(O2)**「an observation **independent of the id itself**」。index は既に手元にあるので追加の往復は発生しない):

**2 面が gate するものは違う (重要)**: 第 1 面 (session provenance) は**この record の `bound_*` に触れる全ての操作**の前提で、第 2 面 (identity 再照合) は**生きているペインを宛先にする操作** (`inspect_pane` / `send_keys` / 下表 2 行目の「在」判定) の前提である。**`pane_exited` の attribution に第 2 面を要求してはならない** — ペインが終了していれば `bound_peer_id` は列挙から消えているのが正常なので、第 2 面を要求すると**終了を永久に確定できなくなる**。契約 T-§4.2-id は「**Within one daemon lifetime ids are therefore never recycled in any reachable operation**」と書くので、第 1 面が通っている限り session 内で id が別ペインを指すことはなく、**id 等値だけで attribution は確定してよい**。第 2 面が存在する理由は「pid + endpoint がたまたま再利用され、第 1 面が偽陽性になった restart」を捕まえることにあり、その形は第 2 面の不一致として現れる。

- **2 面とも通った** → (O1) / (O2) を discharge。下表の三値評価へ進み、宛先操作も許可される
- **pid + endpoint は一致したが、`bound_peer_id` のレコードが `name` / `role` / `cwd` のいずれかで食い違った** → **id が別のペインに再割り当てされている。** 束縛失効と同じ扱いで `bound_*` を一切使わず、**indeterminate** として (P4) を `source=session_provenance` で報告する (下表 3 行目の「消えた = unknown」とは区別する — こちらは「在るが別物」という陽性の不一致なので、窓口の reconcile 判断が要る形として通知文に明記する)
- **`bound_peer_id` が index に無い** → identity 再照合の材料が無いだけなので、これは下表 3 行目 (unknown) であって不一致ではない

> **restart 単位で一意な session 識別子は既に `server_info` の `server.session_id` として存在する** (renga 2.1.0 で確認。同ツール記述は「identifies the running renga PROCESS INSTANCE and changes on every restart」「Do NOT substitute `server.pid` or `server.endpoint` for it (the endpoint embeds the pid, and pids get recycled)」と書く)。Step 1 の cursor 束縛は**既にこの値を第一候補に使っている**が、**背景 worker の第 1 面 (`bound_server_pid` + `bound_server_endpoint` の等値) は現行手順では差し替えていない** — 差し替えは spawn 時に控える面 (3-2b) と照合面の両方を変える挙動変更で、cursor の束縛とは独立に取るべき変更だからである。差し替える場合は `bound_server_pid` の代わりに `server.session_id` を控えて第 1 面をその等値に置き換える (pid + endpoint の対は、それが無い版のための近似として残る)。第 2 面の identity 再照合は置き換え後も残す — (O2) は (O1) とは別の obligation なので、一方が強くなっても他方を免除しない。

## 毎サイクルの三値

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

## 全ペイン index（裏取り専用）と発動ゲート・解除手順

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

> **発動の前提**: 本節が定めるのは「契約が批准され first-drive が記録されたあとに何をどう読むか」であって、その前の発動許可ではない。上表の 3 条件はサイクルごとに照会し直す（本ファイルの記述を事実の代わりに読まない）。**解除の手順は下記に書いてある** — 「いつか誰かが批准する」ではなく、順に実行できる形になっている。

**3 条件の解除手順 — 運用面 (規範面の正本は契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) L-§discharge。本項はそこを運用の手順として写したもので、食い違ったら契約側が正)**

**順序 (MUST NOT で入れ替えない)**: **条件 1 → 条件 2** の順。批准前に first drive を撃つのは**批准が governs している当の行為**なので、先に書いた first-drive レコードは非適合なコールを記録するだけで何も clear しない。**条件 3 は他の 2 つと順序関係を持たない** — backend の性質であり、いつでも満たされ・いつでも失われる。

| 条件 | 誰が | どこで | 何をすれば clear になるか |
|---|---|---|---|
| 1. 契約節の批准 | **人間**（ディスパッチャー・窓口・ワーカーのいずれも代行しない。運用の副作用として書かれることは無い） | 契約 `docs/contracts/backend-interface-contract.md` + state DB `events` | 節本文をレビューして PROPOSED → RATIFIED を決め、`bash tools/journal_append.sh notify_sent recipient=<role> kind=cross_tab_list_ratified summary="<1 行>"` で記録する（**直接 DB INSERT はしない**）。あわせて契約側の status 記載 5 箇所（節ヘッダ / L-§sup / L-§ratification / 4 つのポインタ注記 / Amendments log）を同じ変更で更新する。**該当箇所の列挙と編集内容は契約 L-§discharge 条件 1 が正本。** このレコードは `tools/capability_gate.py` の gate 名に**しない**（契約テキストについての決定であって運用ゲートではないため。同ツールの ladder rung 1 と同じ扱い） |
| 2. first-drive 記録 | 手順 (a) は実装者、手順 (b) は**ディスパッチャーが実行し人間が確認** | (a) `tools/capability_gate.py` / (b) state DB `events` | **(a) 名前を照会可能にする**: `cross_tab_list_first_drive` **と `cross_tab_list_first_drive_pending` の 2 つ**を同ツールの gate 表へ **identity mapping**（短名 == 記録される `kind`）で追加する（pending 名が無いと (b) の中断復帰規則が観測できない）。追加前は `--gate` の choices に無く argparse が exit 2 を返すので **undetermined = 未 clear**、追加後は記録が出るまで `not_recorded`（exit 1）。**どちらも「未 clear」だが別の観測なので報告文では区別する**（再照会に意味があるのは `undetermined` のときだけ）。**(b) 初回ドライブを実行して報告し、記録する**: これは**自分が作るレコードを前提条件にしない唯一のドライブ**である（上表条件 2 の適用外。そう扱わないとゲートが開かない）。手順は **(i) 撃つ前に pending を書く → (ii) 1 回だけ撃つ → (iii) 報告 → (iv) 人間の確認後に本記録**。**(i) 先に `bash tools/journal_append.sh notify_sent recipient=<role> kind=cross_tab_list_first_drive_pending summary="<1 行>"` を書く (MUST)** — ドライブと本記録のあいだに人間の確認が挟まるので、その窓で crash / `/clear` / handover が起きると次のディスパッチャーは `not_recorded` を見て**同じコールを撃ち直してしまう**。pending を**コールの前**に書くことで、`pending 在り・本記録なし` を「**既に撃たれたかもしれない**」と読める（撃った後に書くと、まさに埋めたい窓で pending が欠ける）。この状態を観測したら**撃ち直さず**窓口経由で人間へ報告し、人間が確認を与えて本記録を完成させるか、再ドライブを 1 回だけ許可する。**(ii)(iii)** `tab` セレクタ付き `list_panes` を **1 回だけ**撃ち、**結果はレポートを書くためだけに読む**（タブ数とペイン数 / 自タブ外と判定したペイン数 / 自 org と数えた各ペインの ownership 確立方法。これらは応答を読まないと書けないので、この読みは禁止どころか必須である）。**禁じられているのは「使うこと」**で、三値評価・裏取り・退役判定・容量計算へ渡すこと、および監視 state として永続化することが該当する。**結果を使ってしまったドライブは本手順ではなく、レコード無しの通常ドライブ = 条件 2 違反**である。**(iv)** 人間の確認を得てから `bash tools/journal_append.sh notify_sent recipient=<role> kind=cross_tab_list_first_drive summary="<1 行>"`。以後 `python3 ../tools/capability_gate.py --gate cross_tab_list_first_drive` が exit 0 / `recorded` を返す |
| 3. capability 広告 | — （**今日すでに満たされている**） | `mcp__renga-peers__server_info` の応答 | renga 2.2.0 以降のサーバー + 同 token を理解する mcp-peer なら `effective_capabilities` に載る。**やることは無い。** ただし恒久ではなく、**サイクルごとに引き直す**: 古い binary のまま動いている daemon / サーバーより古い mcp-peer / `status` が `detached`・`unreachable`（= `null` は「不明」であって「無い」ではない）/ 呼び出し時の `[server_too_old]` で unmet に戻る |

**3 つとも clear になった時点で実際に変わること**: 背景 worker が居るサイクルで、いま作っている全タブ `list_peers` index に加えて全ペイン index を作るようになる（**1 サイクルあたり 1 往復増えるだけ・背景 worker が 0 件のサイクルでは 0 往復**）。**index にできることは live になっても変わらない** — 上の「使い方」表のとおり裏取り面のままで、動かせるのは「`bound_pane_id` のレコードが在るが `cwd` / `name` / `role` で食い違う → **indeterminate**」の 1 行だけであり、**在 の側にも 終了 の側にも 1 行も動かせない**。生存判定は引き続き peer 面、退役確定は引き続き attribution 済み `pane_exited`、容量会計は依然として無配線である。契約 T-§4.2-place-rec の verdict も、spawn-flow 3-1d 条件 4（背景配置は 1 枚まで）も動かない。

## 終了確定・画面監視・追加の待ち時間

**終了確定は attribution 済みの `pane_exited` だけ (MUST)**: Step 1 から持ち越した候補イベントについて、**本項の session provenance (第 1 面) が通っているサイクルに限り** (第 2 面は要求しない — 上記「2 面が gate するものは違う」)、イベントの pane 識別子 (canonical `ev.id` → alias `ev.pane_id`) が `bound_pane_id` に一致するか、peer 識別子 (canonical `ev.peer_id` → alias `ev.agent_id`) が `bound_peer_id` に一致したときに終了を確定し、`WORKER_PANE_EXITED` を送って Step 5 (b) 更新規則 (4) の record 削除に進む (canonical を先に、alias を後に読む規律は Step 1 と同じ)。**背景 worker では `name` 一致を同定に使わない** — 全タブ世界で `name` は一意でない (T-§2.2 / T-§3.1「a harness MUST NOT match a lifecycle event to a tracked pane by `name` alone」)。join が引けないまま 3 行目が続く形は **indeterminate** で、(3-a-4) の解消経路 (窓口の reconcile 判断) にそのまま乗せる。**背景 worker 専用の新しい escalation 経路・新しい event 名・新しい `kind` は作らない。**

**(3-a-2) の表は背景 worker には適用しない**: 同表の 1 列目 (`list_panes` から消えた) が定数で成立し、3 列目 (§1-2 の同タブ生存) が構造的に「不在」を返すので、そのまま当てると毎サイクル 4 行目 (終了とみなす) に落ちる。背景 worker の消失評価は本項が owner で、(3-a-2) は同タブ worker が owner である。

**画面監視は数値 id 経由で維持する**: 背景 worker は `list_panes` に出ないが、`inspect_pane` は Group A なので**自タブと確立した列挙に依らず数値 pane id なら他タブへ届く**（2026-08-09 に人間が批准した cross-tab pane control。契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2 の Group A / 数値 id 規律）。したがって背景 worker では **Step 4 / Step 5 / Step 5.2 を skip しない** — Step 4 の inspect 宛先を `target=<bound_pane_id>` に切り替えて従来どおり評価する。skip したままにすると `APPROVAL_BLOCKED` / `ERROR_DETECTED` / `STALL_SUSPECTED` / `PANE_OUTPUT_WITHOUT_PEER_MSG` の 4 系統が背景配置でまとめて検出不能になる。Step 5.1 (relay gap) と Step 5.25 (relay scan) は元から pane 可視性に依存しないので本項の影響を受けない。

**追加の待ち時間は 0 分**: 増えるのは 3 分サイクルあたり `server_info` と全タブ `list_peers` の **各 1 往復だけ** (背景 worker が 0 件のサイクルでは 0 往復。上記「全ペイン index」のゲートを 3 条件とも通ったサイクルではこれに全タブ `list_panes` の **1 往復**が加わるが、ゲート未通過サイクルでは 0 往復のまま) で、その 1 回ずつの応答から作った `server.pid` と index を全背景 worker が参照する。**per-worker の再取得・retry・新しい blocking wait を足さない** ((P5) と同じ向き: 精度は上がるが行動は増えない)。列挙が引けなかったサイクルは 4 行目 (観測不能) に落として次サイクルへ送る。
