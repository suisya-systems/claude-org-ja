# オンデマンド curate の完了監視 (Step 5.3) — 詳細

[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) の監視ループ Step 5.3 の本体仕様。`.state/dispatcher/curate-inflight.json` が存在するサイクルだけ読む（無ければ本ファイルは読まない）。

<a id="step-5-3"></a>
## Step 5.3. オンデマンド curate の完了監視 (curate-inflight)

CLOSE_PANE Step 5-3 ([`.dispatcher/references/pane-close.md`](pane-close.md)) が spawn 直後に書いた `.state/dispatcher/curate-inflight.json` が存在する場合のみ実行する (無ければ skip)。curator の完了待ちを CLOSE_PANE ハンドラでブロッキングせず、本監視ループの通常サイクルに載せるための受け口。判定順序は **(a) → (c) → (b)** ((a) が最優先。pane 消失より先に同サイクル受信済みの CURATE_* を処理しないと、curator が CURATE_DONE 送信後に消えたケースを「未受領のまま消えた」と誤報告して情報が欠落する):

   **定数**: `CURATE_TIMEOUT_MIN = 20` (curate 開始からの初回観測閾値) / `CURATE_HARD_CAP_MIN = 40` (延長を含む絶対上限)。

   <a id="step-5-3-close"></a>
   **共通の close 判定表** — (a) の完了受領も (b) の打ち切り / hard cap も、curator ペインを閉じるか / `curate-inflight.json` を残すかは**この 1 つの表に従う** (経路ごとに別の分岐を足さない)。curator の close は spawn とは別サイクルの**非同期**なので、spawn 戻り値の pane_id はその場限りでは残らない。識別の起点は `curate-inflight.json` の `pane_id` (CLOSE_PANE Step 5-3 が spawn 直後に書いた数値 pane id) で、close の直前に `mcp__org-broker__list_panes` で identity を照合する ([`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md) Phase 3.7 の attention watcher 停止と同じ idiom)。

   **表に入る前に、まず「この列挙が自タブのものか」を確立する** — 下の列挙健全性より手前の判定である。`name` / `role` の照合が見るのは「その id が期待どおりのペインか」だけで、**その列挙が自分のタブのものか**は見ていない。pre-capability の renga では `mcp__org-broker__list_panes` が**フォーカス中のタブ**に解決するので、確立できていない列挙から採った id は別タブのペインを指しうる (契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2 の MUST は数値 pane id の出所を "an enumeration it has independently established to be its own tab's" と書いている)。**確立手段は次の 2 つだけ**で、いずれか 1 つが成立したときにその列挙を**自タブと確立済み**とみなす:

   1. **backend が単一タブモデル** — `org-broker` は Group B も自身の単一タブモデル内で解決するので、列挙は構成上つねに自タブのもの (契約 §8.1 / §8.10)
   2. **`caller_scope` を確立できている** — `mcp__org-broker__list_panes` が caller のタブにフォーカス非依存で解決する (契約 T-§cap。`caller_scope_close_identity` とは別トークンで、一方から他方を導出しない)

   **`same_tab` は確立手段に数えない** — `same_tab` / `tab` は契約 T-§2.2-fields が **`list_peers` のレコード**に足すフィールドで、pane 制御の起点である `mcp__org-broker__list_panes` のレコードには載らない。`list_panes` が "never shows other tabs" であることは列挙が**ひとつの**タブに閉じることを保証するだけで、**それがどのタブか**は focused-tab 規則のまま (知る必要のある harness は別途確立しなければならない)。`list_peers` の `same_tab` を根拠に pane 制御を許すのは、契約が MUST で禁じる「messaging 到達性から pane 制御到達性を推論する」ことそのものである (同契約「The two scopes are not interchangeable (MUST)」)。**否定方向の絞り込みにのみ使える** — `same_tab == False` のレコードに `pane_id` を割り当てない ([`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md) Phase 1) という狭める使い方は正しく、自タブ確立の根拠にはならない。

   **2 つのいずれも成立しないなら close を撃たない** — 下の判定表の行 1 に合流させ、`curate-inflight.json` を保持して窓口へ報告し、次サイクルで再評価する。相対セレクタへのフォールバックはしない。2 つ目は確立できたサイクルだけ数える (契約 T-§cap。確かめられない / 確定できないときは fail-safe default で**不成立**、version 文字列やサーバー名からの推定は禁止) ので、確立手順を踏んでいないサイクルでは事実上 `org-broker` 面でのみ close を撃てる — 契約 T-§4.2 の MUST の帰結なので緩めない。**`curate-inflight.json` の `pane_id` / spawn 戻り値 / `CLOSE_PANE` の `{pane_id}` から得た id も例外ではない**: それらの id 自体が自タブ由来でも、close 直前の identity 照合に使う**列挙そのもの**が自タブと確立できていなければ照合結果を信用できない。確立は照合の前段に置く。

   **次に列挙の健全性を判定する** — Step 3 (3-a-1) と同じ弁別子を使う: `mcp__org-broker__list_panes` の応答に org の他メンバー (`secretary` / 他の worker / watcher 等) が**残っている**なら列挙は健全で、curator の不在は curator 固有の事象として読んでよい。**自分以外の org のペインが 1 つも出てこない**なら、curator の消失ではなく自分の**観測不能**である ([観測の原則](worker-monitoring.md#observation-principle) (P4))。

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
   - **行 8 の escalate の再通知 cadence は「保持区間あたり 1 回」**: 行 8 は close が成功するか hard cap で終端するまで続く区間なので、Step 4 (e) の 30 秒窓 (`/loop 3m` cadence では毎サイクル抜ける) は使わず、[観測の原則](worker-monitoring.md#observation-principle) (P4) の観測不能通知と同じ「区間あたり 1 回」の idiom を採る。escalate 送信成功時に journal を書き、**同キーの `notify_sent` が既にある間は再送しない**:

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

   (c) **ペイン消失の検知** ((a) で CURATE_* を受領しなかったサイクルのみ): curator の生存を確認する。判定材料は (i) Step 1 の `poll_events` で curator の `pane_exited` を観測した、(ii) `list_panes` の結果に `name == "curator"` が不在 — の 2 系統。**worker 不在の reduced mode (下記 7) では Step 3 が skip されるため、(ii) は本 step 内で `mcp__org-broker__list_panes` を直接呼んで評価する** (Step 3 が走ったサイクルではその結果を再利用してよい)。これにより `events_dropped` / cursor ギャップで `pane_exited` を取り逃しても list_panes 側で必ず検知できる。**ただし (ii) 単独で消失と断定しない** — worker の消失判定と同じく Step 3 (3-a) の裏取りゲートを通す ([観測の原則](worker-monitoring.md#observation-principle) (P2))。分岐は 2 つで、[close 判定表](#step-5-3-close) の自タブ確立判定 / 列挙健全性判定と同じ証拠に基づく:
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
