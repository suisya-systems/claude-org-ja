# capability 広告 backend の初回駆動ゲート（operational gate）— `list_peers` 全経路共通

`list_peers` の列挙が **capability 広告 backend のもの**だと分かった瞬間に、各経路が何をするかを
定める共有 reference。**`list_peers` を呼ぶ経路は、呼び出しの直前に本ファイルを Read し、下の
§1 → §2 → §3 を順に適用する**（承認が揃った経路が**同タブ生存**を判定する評価順と真理値表は
**§1-2 が正本**）。適用先の一覧は §6。

> **本ファイルが担うのは 3 ゲートのうち operational gate 1 つだけ**。
> [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md)
> の T-§ratification は「**Three gates apply, with different subjects, and they MUST NOT be
> conflated**」と書き、**ratification gate**（主語＝backend contract。契約改定を批准するかの人間判断）/
> **operational gate**（主語＝harness。capability 広告列挙を初めて観測した時点の停止と人間報告）/
> **production-activation gate**（主語＝deployment。server × mcp-peer 双方 2.0 系での実機 dogfood と
> 人間確認）を分けている。同節は「Ratifying the amendment does not discharge this gate, and
> satisfying it does not ratify the amendment」とも書く。
>
> **本ファイルは何も批准しないし、何も有効化しない。** 批准可否の判断と実機 dogfood は人間の作業で、
> どのスキルもこのファイルの手順で代行できない。ファイル名を `capability-first-drive-…` に限定して
> あるのはそのため（`ratification` / `production-activation` と読み違えられないように）。
> production-activation gate との関係だけは §4 で扱う（未記録時の縮退規則がここに要るため）。

> **輸送層（transport）両系**: 本文は tool 名を修飾なしの `list_peers` / `send_message` /
> `list_panes` で書く。実際に呼ぶときは使用中 transport の完全修飾名に読み替える
> （`mcp__renga-peers__*` / `ORG_TRANSPORT=broker` なら `mcp__org-broker__*`）。引数形と
> セマンティクスは両系で同一。**現在配備されている backend で本ゲートが発火するものは無い**:
> `org-broker` は本 amendment の capability を 1 つも広告せず（契約 T-§cap「Non-advertising
> backends are unaffected」）、renga も 2.0.0 を実際に挿すまでは列挙が旧版形のままである。

---

## 1. 版判定 — 列挙 1 回につき 1 度だけ決める

`list_peers` の**結果全体**を見て、次のどちらかに分類する。レコード単位で切り替えてはならない。

| 観測 | 分類 | 本ゲート |
|---|---|---|
| `same_tab` / `tab` の**どちらか一方でも**載っているレコードが **1 件でもある** | **capability 形**（2.0 系） | **発動する**（§2 へ） |
| **全レコード**で `same_tab` と `tab` が **両方欠落** | **旧版 fallback**（非広告 backend） | **発動しない**。従来どおり無停止で進む |

旧版 fallback は現在配備されている全 backend（`org-broker` を含む）が通る経路で、**既存運用は一切
変わらない**。契約 T-§ratification も「The operational gate is **not** triggered on the
non-advertising path」と明記している。

### 1-1. marker を 1 件でも観測したら、旧版形として読み替えてはならない（MUST）

契約 T-§cap は marker の検出規則を harness 側の義務として次のように書く。

- **capability 形の解釈を強制する**: marker は「the presence of **either** field on **any** record
  of an enumeration is sufficient to detect the capability」であり、「a record carrying `same_tab`
  or `tab` **proves that enumeration is cross-tab-shaped whatever the query reported**, and reading
  it as legacy is the exact failure this amendment exists to prevent」。
- **fail-safe で上書きすることを禁じる**: fail-safe default 節の *Precedence over the marker
  definition* は「this fail-safe applies only where **no** marker is observed at all. Where at least
  one of `same_tab` / `tab` is present on at least one record of the enumeration, T-§2.2-fields
  governs, the capability-advertising path applies, and **the fail-safe MUST NOT be invoked to
  override it**」と書く。

したがって、本ゲートが発動した経路で

> ~~「capability 未承認なので、この列挙を非広告経路（旧版形）として解釈して続行する」~~

と判断してはならない。これは契約が名指しで禁じている当の誤りである。

**分けるべき 2 つ**:

- **解釈（interpretation）**: 列挙が capability 形であることは**確定事実**として扱う。marker を読み、
  他タブのレコードは他タブのものとして認識する。
- **行動（action / reliance）**: 承認前は cross-tab addressing に**依拠した行動をしない**。

以降このドキュメントで **「capability 経路で行動しない（non-reliance）」** と書くのは、後者だけを指す。
具体的には:

1. 他タブ判定のピアへは送らない / 触らない / 自組織のメンバーとして扱わない。
2. **monitoring 系（§3-B）では、列挙結果を peer identity・生存判定・宛先解決のいずれにも使わず破棄し、
   `list_panes` / events テーブル / state DB だけで継続する**。
3. どちらの場合も、列挙を「旧版形＝単一タブに閉じている」と読み替えることは**しない**（契約 T-§cap の
   単一タブ保証は marker の**無い**列挙にしか掛かっていないので、marker のある列挙に流用すると
   別 org のピアを自 org のものとして扱う誤りになる）。

### 1-2. 同タブ生存の三値判定と、評価順の正本

**本節は「対象名 N のピアが *自タブに生存しているか*」を判定する規範の正本である。** §1 の版判定と
§2 の照会の結果を受け、**承認が揃った経路だけ**が本節を適用する。

**call site（§6 の表）側には *適用結果* だけを書く（MUST）。** 各スキルに独自の same-tab poll 規範・
生存判定規範・再送予算を置いてはならない。二重規範化すると、未承認時の縮退（§3-B / §3-B-1）と
承認済み時の受理条件が 2 箇所で別々に育ち、「probe にした 1 通」と「経路本来の 1 通」の二重送信が
どちらの文書からも読み取れなくなる。

#### 1-2-a. 評価順 — 列挙 1 回につき、上から 1 度だけ

1. **列挙全体で版判定する**（§1）。レコード単位で切り替えない。
2. **§2 で `first_drive` を照会する**。
3. **capability 形 かつ `first_drive` が未承認**（`not_recorded` / `undetermined`）→ **本節を適用しない**。
   列挙を破棄し §3 の分岐（§6 の表の「縮退先」）に従う。登録待ち型の readiness は §3-B-1 の
   send-as-probe が唯一の代替であり、**この段では same-tab 選別に踏み込まない**（§1-1 の non-reliance:
   解釈は capability 形のまま、行動だけを控える）。
4. **capability 形 かつ `first_drive` が `recorded`** → 1-2-b / 1-2-c を適用する。
   **`capability_production_activation` の未記録は本節を止めない。** §4 は「**このゲートで止まる経路は
   無い**」「他タブピアを対象外にしたまま同タブ集合で続行する」「**止まるのは first drive gate だけで
   ある**」と定める。未記録の間に控えるのは cross-tab addressing（他タブピアへの送信・他タブ由来 id の
   使用）であって、**同タブへ絞り込む本節の選別そのものではない**。本節の後段はすべて同タブ集合にしか
   作用しない（＝行動を狭める操作である）ので、`first_drive` の承認だけで到達する。
5. **旧版 fallback**（§1 の下段。現在配備の全 backend — `org-broker` を含む — はここ）→ **従来の
   `name` 一致のまま**で、本節は適用しない。契約 T-§ratification は「The operational gate is **not**
   triggered on the non-advertising path」と書き、同節は「A harness whose enumerations are all
   legacy-shaped — which is every currently deployed backend, `org-broker` included — proceeds exactly
   as it does today; nothing about existing operation changes」と続ける。

**この 5 段が評価順の正本である。各 call site にこれと別の順序を書いてはならない（MUST NOT）。**

#### 1-2-b. 真理値表（レコード単位）— 対象名 N の同タブ性

**`name` は絞り込みの前段にしか使わない。** 契約 T-§2.2 は

> Harnesses MUST disambiguate by `same_tab` / `tab` or by the record `id`, and **MUST NOT key a lookup,
> a set-membership test, or a reverse map on `name` alone.**

と書く。名前で候補を集めること自体は禁じていないが、**判定を `name` で終わらせること**を禁じている。
列の並びは [`.claude/skills/org-suspend/SKILL.md`](../../org-suspend/SKILL.md) の「宛先選定の真理値表」と
揃えてあるので、往復して読める。

| # | `same_tab` | `tab` | 自タブ `tab` 照合 | 判定 | 理由 |
|---|---|---|---|---|---|
| 1 | `True` | 値あり | 不要 | **在**（同タブ） | `same_tab=True` だけで同タブが確定する（org-suspend 表の同条件行と同じ） |
| 2 | `True` | `None` | 不要 | **在** | `tab` 欠落は旧版の徴候ではない（契約 T-§2.2-fields の検出規則） |
| 3 | `False` | 値あり | 不要 | **他タブ** | この org が spawn したペインは全て同一タブに居る（T-§4.2 retained の対偶） |
| 4 | `False` | `None` | 不要 | **他タブ** | 「`same_tab: false` is a **positive** statement … and **MUST NOT** be conflated with `same_tab` being absent/null」（T-§2.2-fields） |
| 5 | `None` | 値あり | **一致を確認できた** | **在** | org-suspend の ‡ の例外形（`tab` 値の一致だけが `same_tab` の代替になりうる） |
| 6 | `None` | 値あり | **照合不能** | **unknown** | `tab` が載る時点で capability 形。`same_tab` 欠落は unknown であり、`name` 一致で同タブと結論してはならない |
| 7 | `None` | `None` | 照合対象が無い | **unknown** | 片方欠落でも列挙が capability 形なら capability 規則で扱う（欠落は legacy marker ではない） |

**行 5 は形式上の余地であり、既定の着地ではない（重要）**: 契約には **caller 自身の `tab` 値を得る
surface が無い**。T-§2.2 は「the §2.2 distinction from `list_panes` (excludes the caller, hides
geometry)」を Unchanged として維持するので自分のレコードは列挙に現れず、`tab` は T-§2.2-fields が

> It is meaningful **only** for equality comparison within a single enumeration.

と書くので、比較すべき自分側の値が同一列挙内に無い。T-§cap も legacy determination について
「the conformance requirement above, warrants that the enumeration is confined to the caller's tab.
It licenses nothing beyond that」と書き、単一タブ保証を marker の**無い**列挙に限っている。
**行 5 が成立する唯一の形**は、同じ列挙の中に `same_tab == True` のレコードが在り、その `tab` 値が
caller のタブ値だと確定できるとき、欠落レコードの `tab` を**その値と等値比較**して一致した場合である
（MAY）。それ以外の方法で自タブ値を推定してはならない（MUST NOT）。とくに **`list_panes` に同じ
`name` / `role` のペインが在ることを「同タブである」の根拠に使ってはならない**（org-suspend ‡: 予約名も
`role` も全タブでは非一意なので、他タブのピアが自タブのペインと一致していても矛盾しない）。したがって
**`same_tab` 欠落の既定の着地は行 6 / 7 の unknown** である。

> **unknown で禁じられること（上位形）**: **その unknown を根拠にした作用を一切起こさない。在にも不在
> にも数えない。** 具体的には (i) 登録待ちゲートを開けない、(ii) lifecycle（`WORKER_PANE_EXITED` /
> 「消滅済み」記録）を断定しない、(iii) 列挙から `peer_id` を採らない、(iv) identity を DB に書かない、
> (v) `close_pane` / `StateWriter.CLEAR` / fatal 分岐へ進まない。契約 T-§2.1 は判定不能な lifecycle に
> ついて「the harness MUST record the outcome as **indeterminate** and escalate it; it MUST NOT resolve
> it as "closed"」と書く。**unknown を不在に潰すことが契約違反**であり、誤 `WORKER_PANE_EXITED` の
> 直接の原因になる。unknown は journal に残し、人間（窓口）へ上げる。

> **org-suspend の ‡ との対応（往復して読むための注）**: org-suspend は unknown を「**他タブ扱いに
> 倒す**」と書く。あちらは broadcast の宛先選定なので、他タブ扱い＝送らない＝**不作為**が安全側になる。
> **本節は生存判定なので、unknown を「他タブ扱い」に潰すと *不在* に化け、安全側が反転する**。両者は
> 上位形「unknown では *その unknown を根拠にした作用* を起こさない」で一致しており、帰結だけが経路
> ごとに違う。**経路ごとの帰結を、もう一方の文面からコピーしないこと。** org-suspend 自身も生存判定の
> 面では本節と同じ向きに倒しており（Phase 2 / Phase 4 の「消滅済みとして記録せず判定不能のまま残す」
> `indeterminate`）、矛盾しているのは字面だけである。

#### 1-2-c. 集約 — 名前 N に一致するレコード集合 `S(N)` で決める

`S(N)` = 列挙のうち `name == N` のレコード全体。全タブ列挙では複数件になりうる（T-§2.2: 予約名
`secretary` / `dispatcher` / `curator` / `worker-{task_id}` は 2 org 並走で **by construction** に衝突する）。

| # | `S(N)` の内訳 | 判定 | 許される後段 |
|---|---|---|---|
| 1 | 在（行 1 / 2 / 5）が **ちょうど 1 件**（unknown の併存は問わない） | **在** | その 1 件の**数値 `id`** を宛先 / 生存の根拠に使う |
| 2 | 在が **2 件以上** | **unknown**（ambiguous・fail-closed） | どの id も採らない。同一タブ内の name 一意性を破る非適合 backend なので人間に報告する |
| 3 | 在 0 件・unknown（行 6 / 7）が 1 件以上 | **unknown** | 1-2-b の「unknown で禁じられること」 |
| 4 | 在 0・unknown 0・他タブ（行 3 / 4）が 1 件以上 | **不在**（自タブに居ない） | 下記。**他タブの同名ピアを代用の宛先にしない** |
| 5 | `S(N)` = ∅ | **不在** | #4 と同じ。ただし**人間への報告では #4 と区別する**（他タブに同名が居たかは誤爆リスク判断の材料） |

**「不在」が licence するのは messaging 面だけである（MUST）。** T-§4.2 は

> a peer MAY be addressable for messaging and simultaneously **not** addressable for pane control.
> Harnesses **MUST NOT** infer pane-control reachability from messaging reachability, from a
> `list_peers` record existing, or from a successful `send_message`.

と書く。したがって **不在 → lifecycle 断定へ進むには pane 面の裏取りを別に取る**: (i) 同タブの
`pane_exited` を観測した、または (ii) **以前この経路が 在 と観測した名前**が後続の `list_peers` から
消えた。契約 T-§2.1 はこの 2 つを列挙したうえで「If neither is available the harness MUST record the
outcome as **indeterminate** and escalate it」と書く。**Group-A 列挙（`list_panes` など）からの不在は
裏取りにならない** — T-§4.2 は `caller_scope` 未確立下について「**MUST NOT read absence from such an
enumeration, or a `pane_not_found` from such a call, as evidence that a pane has exited**」と書く
（フォーカス変更だけで両方が起きるため）。どちらも取れなければ **indeterminate** に倒し、unknown と
同じ扱いで報告する。lifecycle event 側も同じで、T-§3.1 は「a harness MUST NOT match a lifecycle event
to a tracked pane by `name` alone」と書く。

**登録待ち型（spawn / boot 直後）は 在 のときだけゲートを開ける（MUST）。** T-§2.2 は

> the gate MUST require `same_tab: true` before accepting a record as the child the caller just spawned,
> because an unfiltered name match can be satisfied by a same-named peer in another tab and open the gate
> before the child has registered.

と書く。**不在・unknown はどちらもゲートを開けない**（poll を継続する）。**承認済み路では §3-B-1 の
send-as-probe へ切り替えない**（あれは未承認縮退の代替であり、両者は排他）。受理後、経路本来の 1 通
（挨拶 / タスク指示 / `/org-curate`）は**通常どおり 1 回だけ**送る。**poll の継続条件・再送予算・予算切れ
後の失敗処理は各経路の従来手順のままで、本節が差し替えるのは受理条件だけである。**

**宛先は必ず数値 id。** T-§2.1 は「Numeric ids obtained from `list_peers` are the **only** tab-stable
address form」、T-§2.2-fields は `same_tab` について「Never used as an address」と書く。`same_tab` は
**選別述語**であって宛先ではない。

#### 1-2-d. 自分自身の identity を名前引きで確定してはならない（MUST NOT）

**適用範囲**: capability 形かつ 1-2-a の段 4 に到達した列挙にのみ適用する。**旧版 fallback では従来
どおり**（§1 / 段 5。現行配備は全てこちらで、挙動は変わらない）。

T-§2.2 は「the §2.2 distinction from `list_panes` (excludes the caller, hides geometry)」を Unchanged
として維持する。**したがって自分の `name` に一致したレコードが自分である保証は無い**（caller を除外
しない非適合実装もありうるが、その場合も同名の別ピアと区別する手段が無いので結論は変わらない）。よって:

- **自分の `peer_id` を名前引きで確定しない。** 自 identity は `list_panes` の `focused: true` の
  pane_id で確定する。
- 名前引きの結果が 在 / 不在 / unknown の**いずれであっても、自 `peer_id` は未確定のまま先へ進む**。
  未確定の identity を DB に書かない（推測値・placeholder・他タブ由来の id を書くと、以後の routing が
  壊れた宛先を恒久的に掴む）。**未確定であることを人間 / 窓口への報告に明示する。**

> **導出 — 「自 `peer_id` が未確定になる」分岐は本節から出る**（call site 側に独自規範を置かなくてよい）:
>
> | 分岐 | 本節のどこから出るか | 帰結 |
> |---|---|---|
> | 他タブの同名ピアしか居ない | 1-2-c #4（不在）／行 3・4 | `peer_id` 未確定 |
> | 同タブ性が unknown | 1-2-c #3 →「列挙から `peer_id` を採らない」 | `peer_id` 未確定 |
> | `same_tab=True` が複数（ambiguity） | 1-2-c #2（fail-closed） | `peer_id` 未確定 |
> | 在 1 件だが、それが自役割名である | 本項の caller 除外 | `peer_id` 未確定 |
>
> **4 分岐すべてが未確定に落ちる**ので、DB 更新には pane_id だけを渡す。「未指定 = 保持」と「明示
> クリア」のどちらが正しいかは経路の性質で決まる: **生きた同一ペインを引き継ぐ resume 系は保持**
> （渡さないこと自体が fail-closed になる）、**ペインを新規 spawn する起動系は残値が前セッションの
> 死んだペインを指すので明示クリア**。どちらであれ、推測値を書かない点は共通である。

#### 1-2-e. call site 側に書く形

§6 の表の規範文と既存の未承認縮退段落は**そのまま残し**、その直後に**適用結果だけ**を 2〜5 行で書く。
判定条件・評価順・三値の定義を再掲しない（MUST NOT）:

```text
**capability 形かつ承認済み（§2 の `first_drive` が `recorded`）のときは、共有 reference §1-2 の
三値判定を <対象名> に適用する。「在」のときだけ <この経路の後段>。「不在」は <経路の既定処理>、
「unknown」は <作用を起こさずに報告>。**（判定手順と評価順の正本は §1-2。ここに重ねて書かない）
```

**書いてはならないもの（MUST NOT）**: 独自の same-tab poll 規範 / 独自の再送予算 / 三値表・版判定条件・
承認条件の複製 / 未承認のまま 1-2-b へ抜ける記述。

---

## 2. 記録の照会 — 既に通したゲートで毎回止めない

版判定が capability 形になったら、**行動に移る前に**過去の通過記録を照会する。

```bash
# 窓口 / org-start / org-suspend / secretary-resume（cwd = リポジトリ root）
python3 tools/capability_gate.py --gate first_drive

# ディスパッチャー（cwd = .dispatcher/）
python3 ../tools/capability_gate.py --gate first_drive
```

| exit | stdout | 意味 | 本ゲート |
|---|---|---|---|
| `0` | `recorded` | 通過済み | **発動しない**。§3 の分岐に入らず、承認済みとして進む（§4 の production-activation 側は別途見る） |
| `1` | `not_recorded` | DB を引けたが記録が無い | **発動する**（§3 へ） |
| `2` | `undetermined` | DB を引けなかった | **発動する**（§3 へ）。**照会できないことを素通りの理由にしない** |

- **`not_recorded` と `undetermined` は同じ分岐に落ちるが、別の観測として報告する**。契約 T-§cap は
  determined-empty と undetermined について「they are **not** the same observation and MUST be
  logged distinguishably: only the former is evidence about the backend, and only the latter is a
  reason to re-probe」と書く。`undetermined` のときだけ再照会に意味がある。
- **照会は [`tools/capability_gate.py`](../../../../tools/capability_gate.py) を使う**。インラインの
  `python3 -c "from tools.state_db import ..."` は使わない。理由は 2 つあり、どちらも黙って壊れる:
  - **cwd 依存**: ディスパッチャーの cwd は `.dispatcher/` なので、リポジトリ root が `sys.path` に
    無く `from tools.state_db import …` が `ModuleNotFoundError` になる。ツール側は自身の `__file__`
    から root を解決するので、どの cwd からでも同じ答えを返す。
  - **部分一致**: `payload_json LIKE '%capability_first_drive%'` は、**別のイベントの自由記述
    `note` がゲート名に言及しているだけ**でも一致し、ゲートを恒久的に「通過済み」にしてしまう。
    ツールは `json_extract(payload_json, '$.kind')` の完全一致で引く。
- DB のパスはツール側が `resolve_state_db_path()` で解決する（`.state/state.db` 直書きにしない）。
  記録側の `tools/journal_append.py` と同じ解決なので、`STATE_DB_PATH` で別 DB に向いている環境でも
  記録した DB と照会する DB がずれない。
- **直接クエリで引く**（直近 N 件の走査で代用しない）。件数を区切ると、通過記録より後にイベントが
  N 件積まれた時点で未通過に戻り、一度きりのはずのゲートが再発動する。

---

## 3. 分岐 — 呼び出しコンテキストで 2 つに分かれる

**同じゲートでも、呼び出し側に人間が居るかどうかで取れる行動が違う。** 各 call site は §6 の表で
自分がどちらかを宣言しており、その分岐だけを適用する。

### 3-A. interactive-action — 人間が居て、外向きの作用を起こす直前

**適用条件（両方満たすときのみ）**: (i) その flow を人間が起動していて画面の前に居る、かつ
(ii) 列挙結果がこれから**外向きの作用**（broadcast / 送信 / 停止指示 / ペイン破棄）を駆動する。

**現在の該当は [`.claude/skills/org-suspend/SKILL.md`](../../org-suspend/SKILL.md) の Phase 1 / Phase 4 だけ。**

手順:

1. **作用の前で止まる**。確認が取れるまで 1 通も出さない。
2. 人間に報告する。契約 T-§ratification が MUST として求める最小項目は
   「**at minimum the number of tabs and peers observed and how many of those peers it judged to be
   in another tab**」:
   - **見えているタブ数**（レコードの `tab` の相異なる値の数。`tab` が欠落する列挙では「不明」と書く）
   - **列挙されたピア総数**
   - **うち他タブ判定の件数**（`same_tab=False`、または `tab` から他タブと導出したもの）
   - 加えて、作用の対象に選んだピアの `name` / `peer_id` / `cwd` の一覧（人間が誤爆リスク＝別 org を
     巻き込まないかを判断できる材料にする）
3. **人間の確認を得てから** capability 経路で先へ進む。
4. 通過した**直後**に記録する:
   ```bash
   bash tools/journal_append.sh notify_sent kind=capability_first_drive note=<backend/tab 概要>
   ```
5. 以後この経路は §2 の照会で `recorded` を得るので、実行のたびには止まらない。

記録がある場合でも、**他タブ判定のピアが前回より増えていた**ときはその差分を報告する（ゲートの
再発動ではなく、作用対象外にした集合の変化を人間に見せるため）。

### 3-B. monitoring-read-only — 人間が居ない。停止できない

**適用条件**: 自律監視 / spawn / resume / pane-close など、人間が画面の前に居ない経路。

**この分岐は止まらない。待ち時間は 0 分。ディスパッチャーをブロックする待ちを新設しない。**
監視を止めると、ゲートが守ろうとしている誤送信より重い障害（ワーカーの放置・報告の滞留）になる。

手順:

1. **縮退して継続する**。列挙結果を **peer identity / 生存判定 / 宛先解決のいずれにも使わず破棄し**、
   `list_panes`・events テーブル・state DB だけで判定を続ける（§1-1 の non-reliance）。
   経路ごとの具体的な縮退先は §6 の表に書く。
2. **`capability_first_drive` を記録しない**。これは人間の承認記録であり、無人経路が自分で通せる
   ものではない。
3. **報告は 1 度だけ上げる**（契約 T-§ratification の人間報告 MUST を、重複なしで満たす）:
   ```bash
   # 照会（未報告のときだけ報告する）。§2 と同じく cwd でパスが変わる
   python3 tools/capability_gate.py --gate first_drive_pending      # cwd = リポジトリ root
   python3 ../tools/capability_gate.py --gate first_drive_pending   # cwd = .dispatcher/
   ```
   `not_recorded` / `undetermined` のときだけ、次を行う:
   - **人間に届く経路へ流す**: ディスパッチャー側は窓口へ `send_message(to_id="secretary", …)`、
     窓口側は次の人間向け報告に載せる。§3-A と同じ 3 項目（タブ数 / ピア総数 / 他タブ判定数）を含める。
     `notify_sent` の記録だけでは人間に届かない（attention watcher の classifier はこの kind を
     知らないのでビープは鳴らない）。
   - **印を残すのは送達を確認できたときだけ（MUST）**。送信が
     `[pane_not_found]` / `[peer_not_found]` / `[nudge_failed]` 等で失敗したまま印だけ書くと、
     以後のサイクルは `recorded` を見て報告を抑止し、**一度も人間に届いていない報告が恒久的に
     握り潰される**。送達できなかったサイクルでは**印を書かず、次サイクルで再送する**
     （縮退継続そのものは変わらない。報告が滞留していること自体は
     `.state/` のローカル台帳に残る journal からは分からないので、印の有無＝送達の有無に
     一致させておくことが唯一の再送契機になる）。
   - 送達を確認できたら、**そのあとで**重複抑止の印を残す（記録側も cwd でパスが変わる）:
     ```bash
     bash tools/journal_append.sh notify_sent kind=capability_first_drive_pending note=<backend/tab 概要>     # cwd = リポジトリ root
     bash ../tools/journal_append.sh notify_sent kind=capability_first_drive_pending note=<backend/tab 概要>  # cwd = .dispatcher/
     ```
4. 以後この経路は `first_drive_pending` が `recorded` になるので、**サイクルごとに報告を撃たない**
   （縮退継続そのものは、承認が入るまで毎回続ける）。

#### 3-B-1. 「登録待ち」型の経路が特に危ない — `name` 一致だけで開けてはならない（MUST）

spawn / boot 直後に「目的のピアが `list_peers` に現れるまで poll する」型の経路（§6 の表 #2 / #8 / #9）は、
**`name` 一致でゲートを開ける**構造になっている。全タブ列挙ではこれが壊れる。契約 T-§2.2 は

> **`name` is not unique across an all-tab enumeration.** … The §4.1 reserved names (`secretary`,
> `dispatcher`, `curator`, `worker-{task_id}`) collide **by construction** as soon as two
> organisations run in parallel tabs. Harnesses MUST disambiguate by `same_tab` / `tab` or by the
> record `id`, and **MUST NOT key a lookup, a set-membership test, or a reverse map on `name`
> alone.**

と書く。`worker-{task_id}` や `dispatcher` は**別 org の並走タブに同名で実在しうる**ので、素の name 一致は
**まだ登録していない子のゲートを、他 org のピアで開けてしまう**。

したがってこれらの経路では:

- **登録確認を `list_peers` の name 一致で行わない**（縮退中は列挙そのものを破棄するので、そもそも使わない）。
- **「列挙に出てこない」を未登録の証拠にしない**。これは §1-1 の non-reliance の帰結だが、
  **未登録判定が破壊的な後段（ペイン破棄 / identity クリア / 停止確定）に繋がる経路では特に重要**で、
  その判定を根拠にその後段へ進んではならない（§6 の表 #2 の脚注）。
- **代わりの readiness 判定は「その経路が次に送る `send_message` そのもの」で行う。`list_panes` の
  pane 生存と `inspect_pane` のプロンプト表示で代用してはならない**（MUST）。プロンプトが見えることは
  Claude が起動したことしか示さず、**MCP の peer 登録が済んだことを示さない**。登録前に「boot 完了」と
  みなして先へ進むと、その直後の**一度きりのメッセージ**（ワーカーへのタスク割り当て / dispatcher への
  挨拶 / curator への `/org-curate`）が `[pane_not_found]`（broker では `[peer_not_found]`）で消える。
  手順は:
  1. `list_panes` でペイン生存だけ確認する（死んでいれば通常の spawn 失敗処理へ）。
  2. **その経路が本来送る 1 通を送る**。未登録なら送信が失敗するので、失敗が「まだ登録していない」の
     証拠になる（列挙と違い、この判定は他タブの同名ピアに汚染されない）。
  3. 失敗したら各経路の既定予算で**再送**する（spawn / org-start は 2 秒間隔・最大 30 秒、
     pane-close は既存の最大 3 回 retry）。Enter の再送も従来どおり併用する。
  4. **送達成功をもって「起動・登録・送信」が同時に確定する。その 1 通は消化済みなので、後段の送信
     step に戻って同じメッセージを二度送らないこと**（二重実行になる）。
  5. 予算を使い切っても送達できなければ、**その経路の従来のタイムアウト / 失敗処理に落とす**
     （縮退は「失敗を握り潰す」ことではない。到達不能なペインを正常として報告してはならない）。

#### 3-B-2. 送信失敗後の復旧手順は、それ自体が gate 対象の call site（MUST）

上の probe や通常の `send_message` が `[pane_not_found]` / `[peer_not_found]` を返すと、
[`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の messaging
復旧手順に入り、そこは**新しい `list_peers` を引き直して**宛先を選び直し、さらに「後続 `list_peers`
からの消失」を死亡確定の根拠に使う。これは呼び出し元が gate を適用した列挙とは別の、**後から発行される
列挙**である。

**この復旧手順の `list_peers` は、それ自体が独立した gate 対象の call site である**（§6 の表と
同格に扱う）。入り方で 2 つに分かれ、**どちらでも結論は同じ**:

- **縮退中の経路から入った場合**: 縮退がそのまま引き継がれる。
- **gate を通っていない `send_message` の失敗から入った場合**（監視中の通常の peer 送信など、直前に
  `list_peers` を呼んでいない経路）: **この列挙が「初回の capability 観測」になりうる**。その場合は
  §1 の版判定 → §2 の記録照会 → 本 §3-B を**この場で新規に適用する**（`monitoring-read-only` として
  扱う。復旧手順は無人経路から入るので止まらない）。「呼び出し元が既に縮退していたか」は
  条件ではない。

いずれの入り方でも、縮退中は次を守る:

- 引き直した列挙も §1-1 の non-reliance の対象。**そこから採った数値 id で他タブへ再送しない**
  （承認前に cross-tab addressing へ依拠する行動そのもの）。
- **「後続 `list_peers` から消えた」を死亡確定の根拠にしない**。同手順の `indeterminate`
  （journal + 窓口 escalate、「閉じた」に倒さない）へ倒す。
- 同タブの `pane_exited` を観測できた場合だけ、従来どおり lifecycle を進めてよい。

> **未解消の discoverability gap（follow-up Issue で塞ぐ）**: 規範は本節にあるが、
> [`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の復旧手順
> **本体には本ファイルへのポインタがまだ無い**。したがって `[pane_not_found]` を受けて
> そちらだけを読んだ役割は、本節に到達しないまま列挙を採り直しうる。**規範の所在と参照の所在が
> ずれている状態**であり、follow-up ではポインタ 1 行を復旧手順側に置いて解消する
> （本 PR のスコープ外＝人間判断）。それまでの間、`send_message` の失敗を扱う役割は
> renga-error-codes.md と本節を**セットで**読むこと。

> **`capability_first_drive_pending` は承認記録ではない。** 「報告は済ませた」ことだけを表す
> 重複抑止の印であり、これがあっても capability 経路で行動してよいことにはならない。承認は
> §3-A の `capability_first_drive`（人間確認つき）だけが与える。

---

## 4. production-activation gate との関係（未記録時の縮退）

契約 T-§ratification の第 3 ゲートは、capability branch を**実運用で有効なまま残す**前に
server × mcp-peer 双方 2.0 系での実機 dogfood と人間確認を要求し、
「Until that dogfood is recorded, a harness MAY implement the branch and MAY probe with
`server_info`, but **the probe MUST resolve to the non-advertising path in production**」と書く。

したがって:

- **`capability_production_activation` の記録が無い間は、first drive の承認があっても、
  どの経路も capability 経路で行動しない**（§1-1 の non-reliance）。照会は §2 と同じく cwd で
  パスが変わる — cwd = リポジトリ root なら
  `python3 tools/capability_gate.py --gate production_activation`、cwd = `.dispatcher/` なら
  `python3 ../tools/capability_gate.py --gate production_activation`。**dispatcher 側の経路
  （§6 の表 #4〜#9）で root 形のパスを使うとファイルが見つからず常に `undetermined` に落ち、
  dogfood 承認後も永久に縮退したままになる**ので、cwd に合う形を使うこと。
- ここでも **「非広告経路として解釈する」のではない**（§1-1）。列挙は capability 形として読んだうえで、
  cross-tab addressing に依拠した行動を取らないだけである。
- **このゲートで止まる経路は無い**。実機 dogfood と人間確認が要るので、どのスキルも自力では通せない。
  §3-A の interactive-action でさえ、production-activation 未記録なら「止まって承認を待つ」のでは
  なく、他タブピアを対象外にしたまま同タブ集合で続行する（org-suspend の宛先真理値表がまさに
  この形）。**止まるのは first drive gate だけである。**
- 記録は人間確認のあとに:
  ```bash
  bash tools/journal_append.sh notify_sent kind=capability_production_activation \
      note=<server 版 / mcp-peer 版 / dogfood 概要>
  ```
  双方 2.0 系であることの検証には [`tools/check_renga_compat.py`](../../../../tools/check_renga_compat.py)
  `--require-live` を使う（契約 T-§6 は「A harness MUST verify the two **separately**, and MUST NOT
  accept a single `renga --version` invocation as evidence for both」と書く）。

---

## 5. イベント種別 — 3 つを別々の記録で管理する

契約が分けた承認を 1 つに潰さないため、**同じイベント種別で兼ねない**。

| gate / 印 | `kind=` | 何を表すか | 誰が記録できるか |
|---|---|---|---|
| operational（first drive） | `capability_first_drive` | capability 広告 backend を初めて駆動したことの報告と、**人間の確認** | §3-A の interactive-action 経路のみ（人間確認の直後） |
| （承認ではない） | `capability_first_drive_pending` | 無人経路が capability 形の列挙を**観測して報告を上げた**という重複抑止の印 | §3-B の monitoring-read-only 経路 |
| production-activation | `capability_production_activation` | server × mcp-peer 双方 2.0 系での**実機 dogfood と人間確認** | 人間の作業。スキルは自動記録しない |

いずれも `notify_sent` イベントの `kind=` フィールドとして記録し、照会は
`tools/capability_gate.py --gate {first_drive,first_drive_pending,production_activation}` で行う。

---

## 6. 適用先 — `list_peers` を呼ぶ経路と、その分岐

各 call site には、`list_peers` 呼び出しの直前に次の規範文を置く。リンクの表示は
リポジトリ root 形で固定し、target だけを各ファイルからの相対パスにする
（[`docs/contributing/markdown-conventions.md`](../../../../docs/contributing/markdown-conventions.md)）:

```text
**list_peers の直前に
[`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](RELATIVE_PATH)
を Read し、<分岐名> の分岐を適用する。**
```

下表の「縮退先」列は **capability 形かつ未承認のときだけ**を規定する。**承認が揃ったあとの判定は、
`monitoring-read-only` の全経路（表 #2 / #2b / #3 / #4 / #5 / #6 / #7 / #8）について §1-2 が owner
である** — いずれも `name` 一致で identity / 登録 / 生存 / live 判定を決める構造なので、全タブ列挙では
例外なく §1-2 の三値判定を通す必要がある。**owner から外れるのは 3 つだけ**: #1 / #1b は §3-A と
org-suspend 自身の宛先真理値表、#9 は
[`.dispatcher/references/worker-monitoring.md`](../../../../.dispatcher/references/worker-monitoring.md)
の裏取り真理値表が引き続き owner である（いずれも自前の同タブ規範を持つので §1-2 は掛からない）。

**自分自身の identity を名前で引く経路（#2b / #4）には §1-2-d が掛かる**（`list_peers` は caller を
除外するので、自役割名に一致したレコードは自分ではありえない）。**他の経路には §1-2-b / §1-2-c の
同タブ選別が掛かる**。

| # | 経路 | 役割 / cwd | 分岐 | capability 形かつ未承認のときの縮退先 |
|---|---|---|---|---|
| 1 | [`.claude/skills/org-suspend/SKILL.md`](../../org-suspend/SKILL.md) Phase 1 手順 1（id 台帳を作る） | secretary / root | **interactive-action** | 送信前に停止し人間確認。確認後も他タブピアは対象外のまま |
| 1b | 同 Phase 4 の再列挙（台帳の最新化 / Pass 2 残存確認 / curator 存在確認 / `[pane_not_found]` messaging 復旧） | secretary / root | **interactive-action** | **Phase 1 が capability 形と判定していた場合に限り**、同一実行内で #1 の確認結果に従い gate を再適用しない（そのとき**他タブ判定のピアが Phase 1 から増えていたら差分を人間に報告する**）。**Phase 1 が旧版 fallback だった場合は再適用する**（§1 の版判定を Phase 4 の列挙に対して引き直す）— Phase 1 と Phase 4 の間に backend が upgrade / 再接続して**Phase 4 が最初の capability 観測になりうる**ので、Phase 1 の判定を持ち回すと初回駆動が無停止で通る |
| 2 | [`.claude/skills/org-start/SKILL.md`](../../org-start/SKILL.md) Block D-2（dispatcher の peer 登録 poll） | secretary / root | monitoring-read-only | 列挙を登録確認に使わない（§3-B-1: `name` 一致でゲートを開けない）。readiness は §3-B-1 の send-as-probe で判定する（手順 3 の挨拶送信そのものを probe にし再送。プロンプト表示で代用しない）。**「peer 未登録」を根拠に失敗モードの fatal 分岐（`close_pane` + `StateWriter.CLEAR`）へ進んではならない** — 縮退中の列挙は未登録の証拠にならず、健全な dispatcher ペインを破棄することになる |
| 2b | 同 Step 0-3 の broker 分岐（secretary 自身の identity 検証） | secretary / root | monitoring-read-only | 列挙を identity 検証の充足根拠にしない。`list_panes` の `focused` ペインで確認できなければ、identity 未確認のまま人間に報告して続行判断を仰ぐ（勝手に fatal にしない） |
| 3 | [`.claude/skills/secretary-resume/SKILL.md`](../../secretary-resume/SKILL.md) Step 3（ペイン生存確認） | secretary / root | monitoring-read-only | 列挙を生存判定に使わない。`list_panes` と state DB の `active_runs[]` で突き合わせ、差分は人間に報告 |
| 4 | [`.claude/skills/dispatcher-resume/SKILL.md`](../../dispatcher-resume/SKILL.md) Step 0 手順 4（自分の `peer_id` 取得） | dispatcher / `.dispatcher/` | monitoring-read-only | 列挙から `peer_id` を採らない。`list_panes` の `focused: true` の pane_id で identity を確定し、`peer_id` は未取得のまま進む |
| 5 | 同 `already_consumed` 分岐（監視対象が live か） | dispatcher / `.dispatcher/` | monitoring-read-only | 列挙を live 判定に使わない。`list_panes` の `role == "worker"` と `.state/dispatcher/curate-inflight.json` の有無だけで分岐する |
| 6 | 同 Step 4（ワーカーのペイン生存確認） | dispatcher / `.dispatcher/` | monitoring-read-only | 列挙を不存在の根拠にしない。`list_panes` + events テーブルの報告痕跡で判定し、確定できなければ `WORKER_PANE_EXITED` を送らない |
| 7 | [`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-4（新ピア出現待機） | dispatcher / `.dispatcher/` | monitoring-read-only | 列挙を peer 登録の ground truth にしない（§3-B-1: `worker-{task_id}` は別 org の並走タブに同名で実在しうる）。readiness は §3-B-1 の send-as-probe で判定する（3-5 の指示送信そのものを probe にし再送。送達成功で 3-5 は消化済み＝二度送らない） |
| 8 | [`.dispatcher/references/pane-close.md`](../../../../.dispatcher/references/pane-close.md) 5-4（curator の boot 確認 poll） | dispatcher / `.dispatcher/` | monitoring-read-only | 列挙を登録確認に使わない（§3-B-1）。readiness は §3-B-1 の send-as-probe で判定する（5-5 の `/org-curate` 指示そのものを probe にし既存の 3 回 retry で再送。送達成功で 5-5 は消化済み）。retry を使い切れば従来どおり破棄して curate を skip |
| 9 | [`.dispatcher/references/worker-monitoring.md`](../../../../.dispatcher/references/worker-monitoring.md) (3-a-1) 観測不能フォールバック / (3-a-2) 裏取り真理値表 | dispatcher / `.dispatcher/` | monitoring-read-only | (3-a-2) の `list_peers` 列は **unknown** として読む（在/不在のどちらとしても数えない）。(3-a-1) の peer 経路フォールバックからも `list_peers` を外し、**events テーブルの報告痕跡のみ**で継続する |

[`.claude/skills/org-down/SKILL.md`](../../org-down/SKILL.md) は `/org-suspend` の全 Phase を実行するので
#1 / #1b に推移的に到達する。org-down も人間が起動する flow なので分岐は同じ **interactive-action**。

### 未配線 — 本 Issue のスコープ外（窓口の判断待ち）

棚卸しで**追加の実行 call site が 3 つ見つかったが、本タスクで承認された編集対象ファイルに含まれない**
ため配線していない。いずれも実在する経路なので、別途の判断が要る。

| 経路 | なぜ配線が要るか |
|---|---|
| `.dispatcher/CLAUDE.md` の delegate-plan `after_spawn[]` 要約 | `claude-org-runtime` の delegate-plan helper が `list_peers` 待ちを**機械生成した plan 要素として emit する**ため、helper 経路に乗ったディスパッチャーは [`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-4（表 #7）を読まずに列挙する。emitter は ja の外にあるので、**ja 内で唯一の介入点がこの要約**である（当初「索引だから対象外」と判断したが、それは誤り） |
| [`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の messaging 復旧手順への**同趣旨の追記** | **規範自体は §3-B-2 で先に置いてある**（縮退は復旧手順に伝播する = 引き直した列挙の数値 id で他タブへ再送しない / 消失を死亡確定の根拠にしない）。復旧手順は spawn / readiness の再送という常用経路から入るので、未配線のまま残すと縮退が最も効いてほしい局面で抜けるため。残っているのは `renga-error-codes.md` 本体側にも同じ注記を置いて二重管理を解消する作業 |
| [`.claude/skills/org-attach/SKILL.md`](../../org-attach/SKILL.md) の表示ラベル突き合わせ | 結果は attach コマンドの**ラベル生成にしか使わない**（join key は `list_panes` の pane_id）。ゲート判定には使わないので危険度は最も低いが、全タブ列挙では他 org のペインに自 org のラベルを付けて人間に提示しうる |

runtime 側（`claude_org_runtime` の delegate-plan helper が `list_peers` 再実行を指示する
`target_tab_mismatch` / `tab_ambiguous` / `pane_not_found` の復旧文言）は ja リポジトリの外なので、
本 reference では配線できない。

### 対象外にした `list_peers` の言及

以下は「役割が手順として `list_peers` を実行する箇所」ではないので、本ゲートを配線しない。

| 箇所 | 対象外の理由 |
|---|---|
| `.claude/skills/org-setup/references/permissions.md` の `mcp__renga-peers__list_peers` 行、および各スキル frontmatter の `allowed-tools` | 権限宣言であって呼び出しではない。実行時に列挙結果を受け取らないので、捨てるべき結果が存在しない |
| [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) / `docs/verification.md` / `docs/overview-technical.md` の言及 | ツールの意味論・適合条件を**記述**する契約文および説明文。手順ではない |
| [`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の Known codes 表・capability 説明・シェル例のコメント | エラーコードの**意味**の記述。実行手順は同ファイルの messaging 復旧 step で、そちらは上の「未配線」表に挙げてある（対象外ではなく判断待ち） |
| `.dispatcher/CLAUDE.md` の「ワーカーは `list_peers` で窓口を自動発見する」 | ワーカー側の事実の記述。ディスパッチャーの手順ではない（ワーカーに `list_peers` を指示する文書はリポジトリ内に存在しない） |
| `notes/**` / `docs/design/**` の設計メモ・監査メモ、`docs/operations/**` / `docs/sandbox-probe/**` の runbook | 運用手順ではない / 人間オペレーター向けの一回性 runbook であって役割の常設手順ではない（[`docs/contributing/markdown-conventions.md`](../../../../docs/contributing/markdown-conventions.md) の in-scope からも外れる） |
| `tools/**` の定数・テスト fixture・generator golden | コード上の tool 名定数とテストデータ。実行時に列挙結果を受け取らない |
