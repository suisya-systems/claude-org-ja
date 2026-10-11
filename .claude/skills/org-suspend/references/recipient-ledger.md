# org-suspend: id 台帳と宛先選定の規範

[`.claude/skills/org-suspend/SKILL.md`](../SKILL.md) の Phase 1 手順 1（id 台帳を作る）と、Phase 1 / Phase 4 が共有する
宛先選定の真理値表の本文。Phase 2 / Phase 3.7 / Phase 4 は本ファイルの記号
（(G) / (G-MUST) / (G-OWNTAB) / (F) / F-1 / F-2 / † / ‡ / §）をそのまま参照する。

## 目次

1. [面ごとに id を取る](#1-面ごとに-id-を取る)
2. [pane 制御の 2 グループ（G）](#2-pane-制御の-2-グループg)
3. [フォーカス前提（F）](#3-フォーカス前提f)
4. [台帳の join 規則](#4-台帳の-join-規則)
5. [宛先選定の真理値表](#5-宛先選定の真理値表)

## 1. 面ごとに id を取る

messaging とペイン制御は**別々の面**なので、id もそれぞれの面から取る
（契約 Surface 4 の批准済み amendment (2026-08-09) T-§4.2 は「到達性は面ごとに別々に確立する」ことを MUST に
している。`list_peers` にレコードがあることは、そのピアがペイン制御で addressable であることの
証拠にならない）:
   - `mcp__org-broker__list_peers` — **messaging 用の peer id**。各レコードの `id` / `name` / `role` / `cwd` と、
     マルチタブ対応 backend が返す optional な `same_tab` / `tab` / `tab_name` を控える（この 3 つは
     `cross_tab_peers` capability を広告する backend でのみ載る。非広告 backend では欠落する）。
     **`list_peers` のタブ範囲を決めるのは `cross_tab_peers` であって `caller_scope` ではない**
     （契約 T-§cap は capability を互いに独立と規定する。`caller_scope` が変えるのは `list_panes` と
     **Group A の** pane 制御呼び出しだけで、`list_peers` には影響しない。`close_pane` /
     `set_pane_identity` は `caller_scope` の管轄外＝別 token — 下記 (G) を参照）:
     - **`cross_tab_peers` を広告する backend**: `list_peers` は**全タブを列挙**し、`same_tab` は
       **呼び出し側から見た**値として返る（契約 T-§2.2 / T-§2.2-fields）。フォーカスに依存しないので
       フォーカスの確認は不要
     - **`cross_tab_peers` を広告しない backend**（旧版・renga 1.x・現行 `org-broker`、および
       **`caller_scope` だけを広告する backend**）: ratified の messaging 規則どおり
       **フォーカス中のタブ**に追随する。marker 全欠落が保証するのは「その列挙が単一タブに閉じて
       いる」ことだけで（契約 T-§cap の conformance MUST）、**それが窓口のタブであることまでは
       保証しない**。`/org-suspend` を走らせる前にフォーカスが窓口ペインのタブにあることを確認する。
       列挙されたタブを呼び出し側に紐付けられない場合は **broadcast しない**（別 org のタブへ
       SUSPEND / SHUTDOWN を撃つ経路になる）。紐付けの確認は、`list_panes` に窓口ペイン自身が
       居ること、あるいは列挙に自組織の既知 identity（state.db の `dispatcher_peer_id` 等）が
       含まれることで足りる
   - `mcp__org-broker__list_panes` — **ペイン制御用の pane id**。各レコードの `id` / `name` / `role` を控える。
     **どのタブが返るかは capability による**（契約 T-§cap は `caller_scope` と `cross_tab_peers` を
     独立と規定する）: `caller_scope` を広告する backend では **caller のタブ**が返る（`list_panes` は
     Group A なのでこの token だけで足りる）。広告しない
     backend（旧版、および `cross_tab_peers` だけを広告する backend）では ratified §4.3 の
     visibility consequence どおり**フォーカス中のタブ**が返るので、`/org-suspend` を走らせる窓口ペインの
     タブがフォーカスされていることが前提になる。フォーカスが別タブにあると**別タブのペイン id を
     台帳に取り込み、後段で無関係なペインを `inspect_pane` / `close_pane` しうる**。前提が成り立つか
     確認できない場合は pane 制御を行わず、pending として人間に報告する

## 2. pane 制御の 2 グループ（G）

   - > **pane 制御の 2 グループ（G）— 以降この記号で参照する**: 契約 T-§4.2 は pane 制御ツールを
     > **管轄 capability の異なる 2 群**に分ける。分ける理由は**リリース時期ではなくサーバー世代の
     > スキュー**である（4 トークンは 1 リリースで出ており renga `src/ipc/mod.rs`:123-128 の
     > `SERVER_CAPABILITIES` は 4 定数を並べる。それでもトークンが別なのは、先行 3 トークンを
     > 広告する `#290` 世代の server が未知の `from_pane` を落として `close` /
     > `set_pane_identity` を**ユーザーが見ているタブ**に解決し不可逆にペインを閉じるからで、
     > 上流は `caller_scope_close_identity` を "a token of its own" と書いている
     > — renga `docs/api-surface-v1.0.md`:576-582）。契約 T-§cap
     > "Independence" は `caller_scope` から `caller_scope_close_identity` を推論することを
     > MUST NOT としている。**「`caller_scope` があるからあらゆる pane 制御がフォーカス非依存」
     > という推論をしてはならない**:
     > - **Group A — `caller_scope` 管轄**（suisya-systems/renga#288 の 7 ツール）:
     >   `list_panes` / `spawn_pane` / `spawn_claude_pane` / `spawn_codex_pane` / `focus_pane` /
     >   `inspect_pane` / `send_keys`。この token を広告する backend では相対セレクタ
     >   （リテラル `"focused"` / 裸の `name`）が **caller のタブ**に解決され、フォーカス非依存になる
     > - **Group B — `caller_scope_close_identity` 管轄**（suisya-systems/renga#296）:
     >   `close_pane` / `set_pane_identity` の 2 本。**`caller_scope` はこの 2 本を覆わない**。
     >   `caller_scope`（さらに `cross_tab_peers` / `spawn_tab`）を広告する backend であっても、
     >   この token が無ければ `"focused"` は **user が見ているタブ**に解決され、裸の `name` は
     >   **アクティブタブを最初に探し、そこで見つからなかったときだけ他タブを index 順に走査して
     >   最初の一致を採る**（無差別の全タブ検索ではない。危険なのはこのフォールスルーと、優先される
     >   タブが caller ではなく **user の**タブである点。契約 T-§4.2 "Pane-control scope, Group B"）
     > - **(G-MUST)** `caller_scope_close_identity` が確立していない間は、`close_pane` /
     >   `set_pane_identity` に**相対セレクタ（リテラル `"focused"` / 裸の `name`）を使ってはならない
     >   （MUST NOT）**（契約 T-§4.2 "Fail-safe consequence for Group B (MUST)"）。**自タブのものだと
     >   独立に確立済みの列挙から採った数値 pane id でのみ**アドレスする（MUST）。誤爆した場合、
     >   `close_pane` は不可逆で（人間が入力中のペインを落とす / 並走する別 org の同名 `worker-*` を
     >   閉じる）、しかも**エラーを返さない**。契約はこの MUST に carve-out を 1 つだけ置いているが
     >   （stale-binding cleanup = 再 spawn が `[name_taken]` で弾かれるのに当の name が `list_panes` に
     >   出ない状態）、**本スキルはその条件に当たらないので取らない** — /org-suspend が閉じるのは列挙に
     >   居るペインだけで、`[name_taken]` 起点の再 spawn 経路を持たないため、数値 pane_id は常に列挙から
     >   採れる
     > - **(G-OWNTAB)** 上の MUST が言う「自タブのものだと独立に確立済みの列挙」は、**`mcp__org-broker__list_panes`
     >   の列挙**を指す。`name` / `role` の一致が見るのは「その id が期待どおりのペインか」だけで、
     >   **その列挙が自分のタブのものか**は見ていないので、確立は照合の前段に置く。**確立手段は次の 2 つ
     >   だけ**で、いずれか 1 つが成立したときにその列挙を**自タブと確立済み**とみなす（規範の出所は
     >   契約 T-§4.2 の MUST。同じ規則を harness 側で書き下したものが
     >   [`.dispatcher/references/pane-close.md`](../../../../.dispatcher/references/pane-close.md) の
     >   「照合の前に「その列挙が自タブのものか」を確立する」節で、両者は同一規則であり片方だけを
     >   緩めない）:
     >   1. **backend が Group B を自身の単一タブモデル内で解決する** — `org-broker` は Group B も
     >      単一タブモデル内で解決するので、列挙は構成上つねに自タブのもの（契約 §8.1 / §8.10）
     >   2. **`caller_scope` を確立できている** — `list_panes` が caller のタブにフォーカス非依存で
     >      解決する（契約 T-§cap。`caller_scope_close_identity` とは別トークンで、一方から他方を
     >      導出しない）。**確立できたときだけ数える**: 広告を確かめられない / 確定できないときは
     >      T-§cap の fail-safe default で**不成立**に倒れる（version 文字列やサーバー名から推定しない）
     >
     >   **`same_tab` は確立手段に数えない**。`same_tab` / `tab` は契約 T-§2.2-fields が
     >   **`list_peers` のレコード**に足すフィールドで、pane 制御の起点である `list_panes` のレコードには
     >   載らない。`list_peers` 側の `same_tab` を根拠に pane 制御を許すのは、契約が MUST で禁じる
     >   「messaging 到達性から pane 制御到達性を推論する」ことそのものである（同契約
     >   「The two scopes are not interchangeable (MUST)」）。**`same_tab` が使えるのは否定方向の
     >   絞り込みだけ**で、`same_tab == False` のレコードに `pane_id` を割り当てないという**狭める**
     >   使い方は正しく、「自タブと確立できた」と**広げる**使い方は license されない。
     >   **2 つのいずれも成立しなければ Group B を撃たず**、pending として人間に報告する
     >   （相対セレクタへフォールバックしない）
     > - SHOULD だった時期の経緯は [`docs/design/renga-pane-control-history.md`](../../../../docs/design/renga-pane-control-history.md) §5。本スキルが従うのは上の (G-MUST) である
     > - capability gate 実装済みクライアントでは、Group B の呼び出しが token 欠落時に
     >   `[server_too_old]` で **fail-closed** する。これは黙って握り潰さず、未実行として
     >   pending に残し人間へ報告する（契約 T-§6 clause (b)。renga では回復手順は「daemon を
     >   2.0.0 以降へ更新して**再起動**し `server_info` で再 probe」であり、旧挙動での継続ではない）

## 3. フォーカス前提（F）

   - > **フォーカス前提（F）— 以降この記号で参照する**: `caller_scope` を広告しない backend では
     > `list_panes` と pane 制御呼び出しは**フォーカス中のタブ**に向く。したがって
     > **(F-1)** `list_panes` にピアが**居ないこと**は、それ単独では消滅の証拠にならない
     > （フォーカスが別タブへ移っただけで、生存中の同タブピアが「不在」になる）。
     > **(F-2)** pane 制御呼び出しが返す `[pane_not_found]` も、同じ理由で単独では閉鎖の証拠にならない。
     > 消滅を確定してよいのは **(i) `pane_exited` を観測した**、または
     > **(ii) 列挙が窓口のタブのものだと確認できている**ときだけで、どちらも取れなければ
     > **indeterminate** として残す。**F の解除も (G) のグループ別**である: `caller_scope` 広告 backend
     > では Group A（`list_panes` / `inspect_pane` / `send_keys` 等）がフォーカス非依存になるので
     > **F-1 は効かない**。一方 **F-2 は `close_pane` について残る** — `caller_scope_close_identity`
     > が未確立なら `close_pane` の `[pane_not_found]` は依然フォーカス（user 可視タブ）由来でありうる。
     > 契約 T-§4.3 の visibility supersession が完全になるのは **2 token 揃ったとき**だけで、
     > `caller_scope` 単独では §4.3 の文言が Group B に対して verbatim で立つ。

## 4. 台帳の join 規則

   - 2 つを突合して 1 エントリ =
     `{peer_id, pane_id（割り当てられない場合は未設定）, name, role, cwd, same_tab, tab}` の台帳にする。
     **`to_id` には必ず `peer_id`、`target` には必ず `pane_id`** を渡す。2 つの `id` が同じ値空間だとは
     仮定しない（backend 定義。契約 §4.1 は id を "opaque" と規定しており、面をまたいだ流用は契約が
     保証していない）
   - **`pane_id` の出所は必ず `mcp__org-broker__list_panes`（pane 制御面）である**。`list_peers` の台帳は
     messaging（`to_id`）用に保ち、そこから pane 制御用の id を導かない — 契約が MUST で禁じる
     「messaging 到達性から pane 制御到達性を推論する」ことになるため（「The two scopes are not
     interchangeable (MUST)」）。割り当ては**列挙面のゲート → レコード面の絞り込み**の 2 段で決める:
     - **(i) 列挙面のゲート（(G-OWNTAB)）**: `mcp__org-broker__list_panes` の列挙を**自タブと確立できない**なら、
       **どのエントリにも `pane_id` を割り当てない**。確立手段は単一タブ backend / `caller_scope` の
       2 つだけで、`same_tab` はここに数えない。確立できないまま台帳に id を載せると、後段の
       identity 照合が「期待どおりのペインか」しか見ないため、**別タブのペインを照合済みとして
       `close_pane` しうる**
     - **(ii) レコード面の絞り込み（`same_tab` は否定方向にだけ使う）**: (i) が成立した列挙の中で
       `list_peers` のレコードと突合するとき、**`same_tab == False` / 欠落のレコードを除外する**
   - **突合（join）キーは `name` だけで、`role` は使わない**。`role` は非一意で（worker が複数居れば
     必ず衝突する）join キーとして成立しない。`name` も**全タブ列挙では一意ではない**ので
     （契約 T-§2.2: §1.8 の名前一意性は "this tab" 限定。`worker-{task_id}` 等の予約名は 2 org 並走で
     構造的に衝突する）、`name` join が効くのは **`list_panes` 側が単一タブに閉じている**からであって、
     `name` が無条件に一意だからではない。2.0 系の `list_peers` は**全タブ**を返す一方 `list_panes` は
     **単一タブ分しか返さない**（契約 §1.5 は本 amendment で非 supersede。どのタブかは上記のとおり
     capability 依存）ため、(ii) を誤ると別タブのピアのレコードに自タブの同名
     ペインの `pane_id` が付き、Phase 2 が**別ワーカーのコンソールをスクレイプ**し Phase 4 の lifecycle
     追跡が壊れる:
     - `same_tab == False` のレコード → **`pane_id` を絶対に割り当てない**（pane 制御面では到達できない。
       契約 T-§4.2）
     - 2.0 系だが `same_tab` 欠落（‡ のケース）→ 「判定できない場合は他タブ扱いに倒す」規則どおり
       **`pane_id` を割り当てない**
     - 上の 2 つで**除外されなかった**レコード（`same_tab == True` が確定した 2.0 系のレコード、および
       旧版 fallback の列挙 = 全レコードで `same_tab` / `tab` が両方欠落）→ (i) が成立している列挙に
       同名レコードが在れば、**その `list_panes` レコードの `id`** を `pane_id` として割り当てる。
       **`same_tab == True` はこの割り当てを license する根拠ではない** — license するのは (i) であり、
       `same_tab` はそこから他タブのピアを外す negative filter として働いているだけである
     - `pane_id` が**未設定のエントリは「pane 制御では触れない」**ことを意味する（(i) が不成立で
       台帳全体に id を載せなかった場合を含む）。Phase 2 ではスクレイプを
       諦めて git 情報に倒し、Phase 4 では `close_pane` を撃たずに pending に残す（各 Phase の該当箇所と
       同じ結論）
   - この台帳を **Phase 2 / Phase 4 まで引き回す**。宛先解決・pending 集合はすべて台帳エントリを
     primary key にし、`name` は人間可読の補助ラベルに留める

## 5. 宛先選定の真理値表

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

> **role gate で外した同タブピアは「黙って落とす」のではなく `unresolved` として追跡する**:
> 契約 §2.2 は `role` を **optional** と規定しているので、**実体はワーカーなのに `role` が欠落 / `null` の
> レコード**を適合 backend が返しうる。これを broadcast 対象から外すのは安全側で正しいが、そのまま
> 忘れると Phase 4 が「組織を中断しました」と報告する裏で**そのワーカーが走り続ける**。
> したがって、**同タブ**でありながら `R` 外で除外したピア（role 欠落 / `null` / 空文字列 / 未知）は
> 台帳に `unresolved` として残し、Phase 4 の最終報告で「role が判定できず停止操作の対象外にしたピア」
> として `peer_id` / `name` / `cwd` 付きで人間に提示する。`role == "secretary"` で外したものは
> 窓口自身なので `unresolved` に入れない。他タブの除外ピアは † の他タブ報告に含める。

**版判定は列挙 1 回につき 1 度だけ決める**（正本は [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../org-delegate/references/capability-first-drive-operational-gate.md) §1 / §1-1。全レコードで `same_tab` / `tab` が両方欠落しているときだけ旧版 fallback）。下表の「版判定」列は、こうして決まった版のもとで各レコードがどの行に当たるかを読む。

| # | `same_tab` | `tab` | `role` | 版判定 | broadcast | 理由 |
|---|---|---|---|---|---|---|
| 1 | `True` | 値あり | ∈ `R` | 2.0 系 | **する**（`peer_id` 宛・† 同タブ = 所属 signal） | 自タブの対象役割。tab 情報が揃っており同タブが確定している |
| 2 | `True` | 値あり | ∉ `R` | 2.0 系 | **しない** | 役割が対象外 / 不明。SUSPEND・SHUTDOWN は org の役割にだけ意味を持つ |
| 3 | `True` | `None` | ∈ `R` | 2.0 系（片方欠落） | **する**（`peer_id` 宛・† 同タブ = 所属 signal） | `same_tab=True` だけで同タブが確定する。`tab` 欠落は旧版の徴候ではない |
| 4 | `True` | `None` | ∉ `R` | 2.0 系（片方欠落） | **しない** | 役割で除外（版判定より役割判定が優先） |
| 5 | `False` | 値あり | ∈ `R` | 2.0 系 | **しない**（† 他タブは対象外・人間に報告） | 他タブのピアは、この org が spawn したものではない（T-§4.2 retained の同一タブ launch 規則の対偶）。所属を証明する signal も無いので送らない |
| 6 | `False` | 値あり | ∉ `R` | 2.0 系 | **しない** | 他タブの無関係ペインである可能性が最も高い |
| 7 | `False` | `None` | ∈ `R` | 2.0 系（片方欠落） | **しない**（† 他タブは対象外・人間に報告） | `same_tab=False` だけで他タブが確定する。行 5 と同じ理由 |
| 8 | `False` | `None` | ∉ `R` | 2.0 系（片方欠落） | **しない** | 役割で除外 |
| 9 | `None` | 値あり | ∈ `R` | 2.0 系（片方欠落） | **原則 しない**（行 5 と同じ）。`tab` が caller 自身のタブ値と一致すると確認できたときだけ行 1 と同じ扱い（‡） | `tab` が載る時点で 2.0 系。`same_tab` 欠落は unknown であり、`name` 一致で同タブと結論してはならない（‡） |
| 10 | `None` | 値あり | ∉ `R` | 2.0 系（片方欠落） | **しない** | 役割で除外 |
| 11 | `None` | `None` | ∈ `R` | **旧版 fallback**（`cross_tab_peers` 非広告。renga 1.4 系および現行 `org-broker` はここ） | **する**（`peer_id` 宛・† 単一タブ = 所属 signal。**フォーカス前提の確認が済んでいること**） | tab 情報が無い＝列挙が単一タブに閉じている（契約 T-§cap の conformance MUST）ので、従来どおり role gate だけで決める（現行挙動と同一）。ただしその単一タブが窓口のタブであることは手順 1 のフォーカス確認で担保する |
| 12 | `None` | `None` | ∉ `R` | 旧版 fallback | **しない** | 旧版経路でも未知 role は対象に含めない。役割が判定できないペインへの broadcast は org 外プロセスへの誤爆になる |

**宛先は必ず `peer_id`**: broadcast 対象に決まったピアへは `send_message(to_id="<peer_id>")` で送る。
`to_id` に `name` を渡すと 2.0 系では**名前解決が送信者タブ内に限定される**ため、他タブのピアは
`[pane_not_found]` になる（`list_peers` 由来の id 宛はタブ横断で解決される）。旧版 fallback の行でも
`peer_id` 宛は同じく有効なので、経路を 1 本に揃えるため版によらず常に `peer_id` を使う。

**†（broadcast 対象の所属判定 — 同タブのみが自組織）**: SUSPEND / SHUTDOWN は**同タブのピアにだけ送る**。
他タブのピアには**送らず、人間に報告する**。根拠は identity 照合ではなく**構造**である:

- **同タブのピア（`same_tab=True`、および旧版 fallback の列挙全体）は自組織のもの**。caller のタブの
  ペイン群は、この org が組み上げたレイアウトである（旧版 fallback では、その単一タブが窓口のタブで
  あることを手順 1 のフォーカス前提の確認で担保したうえで成り立つ）。契約 T-§4.2 は ratified §4.2 の
  「harnesses MUST launch every orchestrator-spawned pane in the same tab」を **retained（非 supersede）**
  として維持しており、この org が spawn したペインは**全て同一タブに居る**ことが契約上保証されている。
  したがってここは **role gate だけで判定し、追加の identity 一致を要求しない**（要求すると、identity を
  state.db に記録しない役割 — 後述の on-demand curator — を永久に取りこぼし、現在配備の backend の挙動も
  変わってしまう）
- **他タブのピアは、同じ契約規則の対偶により、この org が spawn したものではない**。よって
  **broadcast 対象から外す**。`role` が対象役割でも送らない

> **なぜ identity 照合で他タブピアを「自組織」と認定しないのか**: **他タブピアの所属を証明できる signal が
> 現状存在しない**ためである。`worker-{task_id}` の task_id は人間が付ける slug で、**別 checkout の org が
> 独立に同じ値を使いうる**（`runs.task_id` の UNIQUE 制約はその DB の中だけの話で、org を跨いで一意で
> あることは何も保証しない）。`cwd` も証明にならない — ja root 配下には人間が開いたペインや pr-watch
> ペインが生えるし、別 checkout の org が似た階層構造を持つこともある。`runs.worker_dir_id` は nullable
> （`tools/state_db/schema.sql`）なので cwd 照合を必須にもできない。**証明できない以上、送らないのが唯一の
> 安全な選択**であり、「たぶん自組織だろう」で SUSPEND / SHUTDOWN を撃つと別 org を止める。
>
> **将来この規則を再訪する条件**: worker を意図的に別タブへ配置する運用（マルチタブ配置ポリシー）を
> 導入するときは、本規則と併せて **session スコープの identity をピアに刻む仕組み**が必要になる。
> それが無いまま他タブへ broadcast を許すと、上の証明不能性がそのまま誤爆になる。

**他タブピアの扱い**（役割共通）:

- **broadcast しない**。SUSPEND も SHUTDOWN も送らない
- **`close_pane` も撃たない**（そもそも pane 制御面では到達できない。契約 T-§4.2 と下記 §）
- **人間に報告する**: 「他タブに `role` が {worker,dispatcher,curator} のピアが N 件見えているが、
  自組織のものと確認できないため停止操作の対象外にした」ことを、`name` / `peer_id` / `cwd` 付きで
  提示する。人間がそれを自組織のものだと判断した場合の対応（手動停止など）は人間の判断に委ねる
- **curator の補足**: on-demand curator の identity は設計上 **state.db に書かれない**
  （`org-start` は `curator_pane_id` / `curator_peer_id` を `StateWriter.CLEAR` で明示クリアし、
  [`.dispatcher/CLAUDE.md`](../../../../.dispatcher/CLAUDE.md) は「state.db に**書かない**（null が正常系。
  生存確認は `list_panes` のみ）」と規定する）。**同タブの curator はタブ所属で自組織と判定できるので
  従来どおり graceful に SHUTDOWN を受け取る**（オンデマンド curate と suspend が重なったケースは
  この経路で停止する）

**§（他タブピアの停止確認に関する注記）**: `same_tab=False` と判定されたピアは、messaging では到達
できても**ペイン制御では到達できない**（契約 T-§4.2: pane 制御は caller のタブ内に留まる）。Phase 4 で
このピアの `close_pane` が `[pane_not_found]` を返しても**閉鎖の証拠にはならない**ので、Phase 4 の
「`[pane_not_found]` の扱い」節の他タブ例外に従う。

**‡（`same_tab` 欠落時の扱い — 名前一致で「同タブ」と結論してはならない）**: 2.0 系と判定した列挙で
`same_tab` が載らないレコードは **unknown** であり、**他タブ扱いに倒す**（§ の制約と † の identity 確認を
両方適用する安全側）。

`mcp__org-broker__list_panes` に同じ `name` / `role` のペインが在ることを「同タブである」の根拠に**使ってはならない**。
`name` は全タブ列挙では一意でなく（契約 T-§2.2。予約名は 2 org 並走で構造的に衝突する）、`role` はさらに
非一意なので、**他タブのピアが自タブのペインと `name` / `role` を共有していても矛盾しない**。この一致を
同タブの証拠に使うと、他タブのピアが同タブと誤分類され、† の identity 確認を**素通り**して別 org へ
SUSPEND / SHUTDOWN が飛ぶ（本節が防ごうとしている事象そのもの）。

同タブと積極的に結論してよいのは、`same_tab == True` が載っているか、`tab` の値が **caller 自身のタブの
値と一致**することを確認できた場合だけである。どちらも取れなければ unknown のまま他タブ扱いにする。
