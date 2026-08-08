# capability 広告 backend の初回駆動ゲート（operational gate）— `list_peers` 全経路共通

`list_peers` の列挙が **capability 広告 backend のもの**だと分かった瞬間に、各経路が何をするかを
定める共有 reference。**`list_peers` を呼ぶ経路は、呼び出しの直前に本ファイルを Read し、下の
§1 → §2 → §3 を順に適用する**。適用先の一覧は §6。

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
   - **重複抑止の印を残す**（記録側も cwd でパスが変わる）:
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

#### 3-B-2. 送信失敗後の復旧手順にも縮退が伝播する（MUST）

上の probe や通常の `send_message` が `[pane_not_found]` / `[peer_not_found]` を返すと、
[`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の messaging
復旧手順に入り、そこは**新しい `list_peers` を引き直して**宛先を選び直し、さらに「後続 `list_peers`
からの消失」を死亡確定の根拠に使う。これは呼び出し元が gate を適用した列挙とは別の、**後から発行される
列挙**である。

**縮退中の経路からこの復旧手順に入った場合、縮退はそのまま引き継がれる**:

- 引き直した列挙も §1-1 の non-reliance の対象。**そこから採った数値 id で他タブへ再送しない**
  （承認前に cross-tab addressing へ依拠する行動そのもの）。
- **「後続 `list_peers` から消えた」を死亡確定の根拠にしない**。同手順の `indeterminate`
  （journal + 窓口 escalate、「閉じた」に倒さない）へ倒す。
- 同タブの `pane_exited` を観測できた場合だけ、従来どおり lifecycle を進めてよい。

（`renga-error-codes.md` 本体への同趣旨の追記は follow-up Issue で行う。本節が先に規範を置くのは、
復旧手順が **spawn / readiness の再送という常用経路**から入るためで、そこを未配線のまま残すと
縮退が最も効いてほしい局面で抜ける。）

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

| # | 経路 | 役割 / cwd | 分岐 | capability 形かつ未承認のときの縮退先 |
|---|---|---|---|---|
| 1 | [`.claude/skills/org-suspend/SKILL.md`](../../org-suspend/SKILL.md) Phase 1 手順 1（id 台帳を作る） | secretary / root | **interactive-action** | 送信前に停止し人間確認。確認後も他タブピアは対象外のまま |
| 1b | 同 Phase 4 の再列挙（台帳の最新化 / Pass 2 残存確認 / curator 存在確認 / `[pane_not_found]` messaging 復旧） | secretary / root | **interactive-action**（#1 で適用済み） | **同一実行内で #1 の版判定と確認結果に従う**。Phase 4 は必ず Phase 1 の後に走るので gate を再適用しない。ただし**他タブ判定のピアが Phase 1 から増えていたら差分を人間に報告する** |
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
