# ペインクローズ（CLOSE_PANE 受信時） — 詳細

`.dispatcher/CLAUDE.md` の「ペインクローズ」エントリポイントから参照される本体仕様。retro ゲート、知見記録、`close_pane` 呼び出し、窓口報告までの全手順を含む。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `renga` / opt-in `broker`）**: 本ファイルの手順は **既定 `renga`** で書いてある（無設定ならそのまま従う。挙動不変）。`ORG_TRANSPORT=broker` では完全修飾名が **`mcp__renga-peers__*` → `mcp__org-broker__*`** に置換される。retro gate の `send_message`（secretary 宛）/ `check_messages`（受信）は broker でも同じツール名で動くが、受信は **ナッジ + `check_messages` の pull** になる（renga の in-band `<channel>` push に対して）。`close_pane` のエラーは renga の `[pane_not_found]` / `[pane_vanished]` に加え broker では `[no_backend]`(adapter 不在) / `[session_invalid]` / `[token_invalid]` も返しうる（未知コードは default-branch）。両系の総説は [`.dispatcher/CLAUDE.md`](../CLAUDE.md) の「輸送層 両系」節、契約面は [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14）。（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）

## ペインクローズ（CLOSE_PANE 受信時）

**重要: Step 1〜2 の振り返りが完全に終わるまで、絶対にペインを閉じないこと。**
ペインを閉じるとワーカーの出力が失われ、振り返りに必要な情報が取得できなくなる。
必ず以下の順序で実行する:

### 1. 振り返り（org-retro 相当）

#### ⚠️ 完了報告ゲート（結論を書く前に必ず実行）

「完了報告未着」「報告が届かなかった」「ワーカーが報告しなかった」等の結論を retro に書く **前に**、必ず `tools/dispatcher_retro_gate.py` を使って secretary の ack を待つこと。

CLI は **1 attempt あたり 1 回起動する単発判定**（Issue #285、Claude Code の Bash tool が一往復であるため、長寿命の双方向プロセスは想定しない）。各 attempt の cadence（30 秒スリープ）はディスパッチャー側が `Bash sleep 30` で挟む。

#### 0. 受領記録の確認（初回送信の前に必ず実行）

問い合わせを出す前に、答えが既に手元にないかを見る。`.state/dispatcher/worker-idle-state.json` の該当 record の `completion_reported_at` は、secretary が `WORKER_COMPLETION_NOTED` を送った時刻を保持する **受領済みの陽性記録** である（set / clear の契約は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 2）:

記録だけでは足りない。`WORKER_REOPENED`（T6 再指示の解除通知）は best-effort で取りこぼしうるため、再指示が入った後も前回完了時の `completion_reported_at` が残りうる。そこで **DB の `runs.status` を authoritative な lifecycle として併せて見る**（[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 5.2 (b)(6) が同じ理由で `runs.status` を要求しているのと同じ非対称性への対処）:

```bash
# ディスパッチャー cwd は .dispatcher/ なので 1 段上がリポジトリルート。
# 記録が無い / ファイルが読めない場合は空文字になる（= 下の分岐でゲートを回す側に倒れる）。
completion_reported_at=$(python -c 'import json;d=json.load(open("../.state/dispatcher/worker-idle-state.json"));print((d.get("worker-<task_id>") or {}).get("completion_reported_at") or "")' 2>/dev/null)
run_status=$(sqlite3 ../.state/state.db "SELECT status FROM runs WHERE task_id = '<task_id>'" 2>/dev/null)
```

- **値があり、かつ `run_status` が `in_use` **でない**（`review` / `completed`）** → 完了報告は既に secretary に届いており、その記録は現在の lifecycle のものである。**初回送信を発行せず**、本ゲートを acked 相当として通過し（「2. polling ループ」も回さない）retro を続行する。答えが手元にあるのに聞き直すと、窓口の受信箱を同じ 1 事実で埋めることになる（2026-08-08 に同一 task へ 4 回再送した実誤検知、Issue #869）。CLOSE_PANE 時点では merge 済みで `completed` になっているのが典型なので、`review` だけに絞らない
- **値はあるが `run_status == 'in_use'`** → T6 再指示が landed 済みで、記録は**前回の完了を指す stale な marker** である（`WORKER_REOPENED` の取りこぼし）。skip せず従来どおりゲートを回す。flag の self-heal clear は監視ループ側の責務なので**ここでは触らない**（worker-monitoring.md Step 5.2 (d) reopen-self-heal が担う）
- **値が無い / record が無い / ファイルが読めない / `run_status` が取れない** → **記録の不在は「未着」の証拠にならない**（`WORKER_COMPLETION_NOTED` は secretary が best-effort・非 blocking で送るので取りこぼしうる）。従来どおり下の「1. 初回送信」から本ゲートを回す。ゲートの存在理由そのものが「dispatcher の受信キューに無い ≠ システム上に無い」だからである（下の「理由」節）

この skip は初回送信の有無だけを変えるもので、polling ループ・secretary unreachable fallback・exit code 分岐は一切変えない。判定の一般形は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) の「観測の原則」(P1)（観測できないことは起きていないことの証拠にならない）に対応する。

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

1. journal helper 経由で `retro_deferred` を追記する（生 JSON を `>>` で書かない。helper 契約は `.dispatcher/CLAUDE.md`「delegate-plan helper」節末尾の `tools/journal_append.sh` 説明を参照）:
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

`mcp__renga-peers__close_pane` で明示的にペインを破棄する。**相対セレクタ（`"focused"` / 裸の `name`）では撃たない**: 契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2「Fail-safe consequence for Group B」は `close_pane` / `set_pane_identity` を**自タブのものと確認済みの列挙から採った数値 pane_id** でのみ撃つことを求める（裸 name の legacy 解決は active タブで外すと他タブへ index 順にフォールスルーして先勝ちし、別 org の同名ペインを不可逆に閉じうる）。

**pane_id の出所**: 窓口からの依頼 `CLOSE_PANE: {pane_id} のペインを閉じてください。` に載っている値をそのまま使う（[`.dispatcher/CLAUDE.md`](../CLAUDE.md)「ペインクローズ（CLOSE_PANE 受信時）」）。pane_id を欠く依頼を受けた場合は `mcp__renga-peers__list_panes` の列挙から `name == "worker-{task_id}"` かつ `role == "worker"` のレコードを引き、その `id` を使う（列挙を自タブのものと確立できたときに限る — 下記の 2 手段）。

**照合の前に「その列挙が自タブのものか」を確立する**。下の identity 照合は `mcp__renga-peers__list_panes` の列挙に依るが、`name` / `role` の一致が見るのは「その id が期待どおりのペインか」だけで、**その列挙が自分のタブのものか**は見ていない。pre-capability の renga では `list_panes` が**フォーカス中のタブ**に解決するので、確立できていない列挙で照合しても結果を信用できない（契約 T-§4.2 の MUST は数値 pane id の出所を "an enumeration it has independently established to be its own tab's" と書いている）。**確立手段は次の 2 つだけ**で、いずれか 1 つが成立したときにその列挙を**自タブと確立済み**とみなす:

1. **backend が単一タブモデル** — `org-broker` は Group B も自身の単一タブモデル内で解決するので、列挙は構成上つねに自タブのもの（契約 §8.1 / §8.10）
2. **`caller_scope` を確立できている** — `list_panes` が caller のタブにフォーカス非依存で解決する（契約 T-§cap。`caller_scope_close_identity` とは別トークンで、一方から他方を導出しない）

**`same_tab` は確立手段に数えない**。`same_tab` / `tab` は契約 T-§2.2-fields が **`list_peers` のレコード**に足すフィールドであり、pane 制御の起点である `list_panes` のレコードには載らない。契約が `list_panes` を "never shows other tabs" と書くのは、列挙が**ひとつの**タブに閉じることを保証するだけで、**それがどのタブか**は依然として focused-tab 規則のままである（"which tab it is remains the ratified focused-tab rule, and a harness that needs to know MUST establish it separately"）。`list_peers` 側の `same_tab` を根拠に pane 制御を許すのは、契約が MUST で禁じている「messaging 到達性から pane 制御到達性を推論する」ことそのものである（同契約「The two scopes are not interchangeable (MUST)」— `list_peers` は messaging 面、`list_panes` は pane 制御面で**別々に**確立しなければならない）。**`same_tab` が使えるのは否定方向の絞り込みだけ**である: `same_tab == False` のレコードに `pane_id` を**絶対に割り当てない**（[`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md) Phase 1 の id 台帳）という**狭める**使い方は正しく、「自タブと確立できた」と**広げる**使い方は license されない。

**2 つのいずれも成立しないなら close を撃たず**、identity 照合にも進まずに下の「保留（close を見送った場合）の扱い」へ進む（相対セレクタへフォールバックしない）。**窓口の依頼に載っていた `{pane_id}` も例外ではない** — その値自体は窓口が自タブで採ったものでも、close 直前の照合に使う**列挙そのもの**が自タブと確立できていなければ照合結果を信用できないので、確立は照合の前段に置く。

**2 つ目は確立できたときだけ数える**: capability の確立規則は契約 T-§cap で、広告を確かめられない / 確定できないときは **fail-safe default で不成立**に倒れる（version 文字列やサーバー名から推定してはならない）。したがって確立手順を踏んでいないサイクルでは 1 つ目だけが成立しうるものとなり、事実上 `org-broker` 面でのみ close を撃てる。これは契約 T-§4.2 の MUST の帰結であって運用上の不便ではないので、緩めずにそのまま保留・報告へ倒す。

**close の直前に identity 照合する**（pane_id は recycle されうるため、控えた id が別ペインへ再割当てされていないことを毎回確かめる。`/org-attention-stop` / `/org-pull-request` の「識別子束縛 close」と同じ idiom）:

1. `mcp__renga-peers__list_panes` を実行する
2. 控えた pane_id のレコードが `name == "worker-{task_id}"` かつ `role == "worker"` を**なお指している** → その数値 id で close する
3. レコードはあるが `name` / `role` が一致しない（別ペインへ再割当て済み）→ **close しない**（相対セレクタへ
   フォールバックしない）。下の「保留（close を見送った場合）の扱い」へ進む
4. レコードが列挙に無い → close 対象を確認できないので **close しない**。消失の裏取り
   （[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 3 (3-a)、
   [観測の原則](worker-monitoring.md#observation-principle) (P2)）は同区間の `poll_events` との突き合わせと
   連続サイクルでの一致を要求するので、**窓口の 1 通で起動する本ハンドラでは完走できない**。ここで消失を
   判定せず、裏取りは監視ループに委ねて下の「保留（close を見送った場合）の扱い」へ進む。ただし同じ
   `list_panes` 応答から (3-a-1) の弁別子（org の他メンバーが残っているか / 自分以外が 1 つも出てこないか）
   だけは読めるので、それを保留報告の理由欄に載せる

```
mcp__renga-peers__close_pane(target=<照合済みの数値 pane_id>)
```

成功時は `"Closed pane id=N."` テキストが返り、renga が `Event::PaneExited` を (exit_event_emitted ガード経由で) 正確に 1 回 emit する。
エラー時は結果テキストの `[<code>]` で分岐する (詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`):
- `[pane_not_found]` / `[pane_vanished]` — 既に閉じた扱いで skip (`WORKER_PANE_EXITED` 経路に回す)
- `[last_pane]` — 唯一のタブの唯一のペインを閉じようとした。通常のワーカー停止では発生しない (窓口/ディスパッチャー/キュレーターが残っているため) が、suspend 末端で起きた場合は該当ペインを自分自身で `exit` させる (org-suspend 参照)

#### 保留（close を見送った場合）の扱い

自タブを確立できずに照合へ進めなかった場合、および上の 3 / 4 で close しなかった CLOSE_PANE は**失敗ではなく保留**である。本フローは窓口からの 1 通で起動する
**単発のイベントハンドラで、自前の「次サイクル」を持たない**ので、保留は (a) 記帳 → (b) 既存の再試行契機 →
(c) 後続 Step の続行、の 3 点で決着させる。**保留専用の state ファイルは作らない**（[`.dispatcher/CLAUDE.md`](../CLAUDE.md)
「監視 gap を埋める内部状態ファイル」の一覧を増やすと、handover / resume / `/clear` で保全すべき対象が増える）。

**(a) 記帳**: journal helper 経由で 1 行残す（生 JSON を `>>` で書かない。helper 契約は
`.dispatcher/CLAUDE.md`「delegate-plan helper」節末尾の `tools/journal_append.sh` 説明を参照）。
**新しい event 名は導入せず既存 catalog の `anomaly_observed` を再利用する**（[観測の原則](worker-monitoring.md#observation-principle) (P4)
「journal は既存 catalog を再利用する」と同方針。`kind` は payload 値なので [`docs/journal-events.md`](../../docs/journal-events.md)
の event 台帳を増やさない）:

```bash
# 自タブ確立の 2 手段がいずれも成立せず、照合に進めなかった
bash ../tools/journal_append.sh anomaly_observed source=close_pane worker=worker-{task_id} kind=close_deferred confidence=n/a note=own_tab_unestablished_pane_{pane_id}
# 3（控えた pane_id が別ペインへ再割当て済み）
bash ../tools/journal_append.sh anomaly_observed source=close_pane worker=worker-{task_id} kind=close_deferred confidence=n/a note=identity_mismatch_pane_{pane_id}
# 4（レコードが列挙に無い = 消失を確定できない）
bash ../tools/journal_append.sh anomaly_observed source=close_pane worker=worker-{task_id} kind=close_deferred confidence=n/a note=not_enumerated_pane_{pane_id}
```

ペインは閉じていないので `.state/workers/worker-{task_id}.md` を `status=pane_closed` に遷移させない
（この遷移は終了が確定したときに監視ループが行う。[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 3）。

**(b) 再試行契機**（新しい常駐監視は足さず、実在する 2 経路に載せる）:

- **ペインが実際に消えていた場合** → 監視ループ [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md)
  Step 3 (3-a) の裏取りゲートが通常サイクルで再評価し、終了が確定した時点で `status=pane_closed` 遷移と
  窓口への `WORKER_PANE_EXITED` を出す。閉じる対象がもう無いので**本 CLOSE_PANE はそこで決着**する
  （上の `[pane_vanished]` を受けたときと同じ扱い）。ただし本ワーカーが最後の 1 枚で監視ループが停止条件
  （[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) 7）に入った場合は再評価も止まるので、
  そのときの契機は次項の窓口側だけになる
- **ペインが生きている / 判定が付かない場合** → **窓口が pane_id を取り直して CLOSE_PANE を再送する**のが
  再試行である（(c) の報告でそれを依頼する）。窓口が `/clear` / session を跨いだ場合は `/org-resume` または
  窓口復帰時に再送される（本ファイル 1 の「secretary unreachable 時の fallback」が名指ししているのと同じ契機）
- **ディスパッチャー側で `name` 一致だけを頼りに別 id へ撃ち直さない**: 窓口が送った pane_id が古いのか、
  前のペインが消えた後に同名で別ペインが立ち上がったのかを、この 1 回の列挙からは区別できない
  （worker pane name の衝突は `delegate-plan` helper が `input_invalid` で弾く（[`.dispatcher/CLAUDE.md`](../CLAUDE.md)
  「delegate-plan helper」の「出力の扱い」）ので同名が同時に 2 枚並ぶことはなく、
  名前が一致する別 id は「作り直された別ペイン」でありうる — それを閉じると生きているワーカーを落とす）。
  区別が付かない判定を自分で倒さず窓口へ返す（[観測の原則](worker-monitoring.md#observation-principle) (P2)）

**(c) 後続 Step は保留でも実行する**: 閉じられなかったのはペインだけで、Step 4〜6 はペインの生死に依存しない。

- **Step 4（窓口への報告）** — 知見を記録した場合の `RETRO_RECORDED` は通常どおり送る。**それとは別に、
  知見記録の有無に関わらず保留の informational を必ず 1 通送る**（窓口側に新しい合図語彙を要求せず、
  読んで再送を判断できる本文にする）:

  ```
  mcp__renga-peers__send_message(to_id="secretary", message="{task_id} のペイン（依頼の pane_id={pane_id}）は identity を確定できなかったため閉じていません（理由: {列挙を自タブと確立できない（単一タブ backend でも `caller_scope` でも示せない） / 列挙に無い（org の他メンバーは出ている / 自分以外が 1 つも出てこない） / その id は name={実際の name} role={実際の role} の別ペインを指している}）。振り返りと知見記録は完了しています。ペインが生きていれば pane_id を取り直して CLOSE_PANE を再送してください。既に終了していれば監視ループの裏取りゲートが WORKER_PANE_EXITED を出します。")
  ```

- **Step 5（curate 閾値チェック）** — 通常どおり 5-1 から回す。`tools/check_curate_threshold.py` は
  `knowledge/raw/` 等の**ファイル数**を数えるだけで「worker のペインが閉じた」ことを条件にしていない
  （同スクリプト冒頭の counting rules）ので、保留でも判定はそのまま成立する
- **Step 6（triage scan）** — 通常どおり 1 回走らせる（read-only の候補提示で、着手判断は人間）。ただし
  **本ワーカーのペイン枠は空いていない**ので、任意の `--free-panes` にこの 1 枚を数えない。6-4 で候補を
  転送する場合は、当該ワーカーが保留中である旨を 1 行添える

### 4. 窓口への報告

知見を記録した場合のみ、`mcp__renga-peers__send_message` で窓口に報告する:
```
RETRO_RECORDED: {task_id} の委譲について {topic} の学びを記録しました。
```

Step 3 で close を見送った（保留）場合は、**知見記録の有無に関わらず**保留の informational も送る
（本文と理由欄は Step 3 の「保留（close を見送った場合）の扱い」(c) を参照）。

### 5. curate 閾値チェックとオンデマンド curator 起動

常駐キュレーター（`/org-start` 起動 + `/loop 30m`）は廃止されている。worker クローズは
knowledge/raw/ が増える主経路なので、**Step 1〜4 完了後に毎回**
閾値チェックを行い、超過時のみ curator を一時起動する。

> **本ステップで CLOSE_PANE は終わらない**: curate（Step 5）の後に triage scan（Step 6）が続く。
> 以下の各分岐で「Step 6 へ進む」とあるのは、curate 部分を終えて triage scan に移ることを意味する。
> curate が不要 / coalesce / error のいずれでも、CLOSE_PANE ハンドラを抜ける前に必ず Step 6 を実行する。

> **実行コンテキスト**: 閾値チェック（5-1）〜 spawn / 起動指示（5-5）と inflight 記録（5-6）
> までが CLOSE_PANE ハンドラの**インライン処理**。CURATE_* の完了受領・timeout 管理・
> curator ペインのクローズは**ブロッキングせず** `/loop 3m` 監視サイクル側
> （[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3)）が行う。
> 「全ワーカーペインが閉じたら監視ループを停止する」判定よりも**先に** 5-1〜5-6（および続く
> Step 6 の triage scan）を完走させ、
> `curate-inflight.json` が存在する間は監視ループを停止しないこと。
>
> **starvation の既知の限界**: worker close が発生しない期間（手動 raw 追加のみ /
> skill-candidate のみ増加等）はこのチェックが走らない。補助トリガーは
> [Issue #501](https://github.com/suisya-systems/claude-org-ja/issues/501)（org-retro 末尾）/
> [Issue #502](https://github.com/suisya-systems/claude-org-ja/issues/502)（org-start バックストップ）
> としてバックログ化済み。

#### 5-1. 閾値チェックスクリプトの実行

```bash
# ディスパッチャー cwd は .dispatcher/ なので 1 段上がリポジトリルート。
py -3 ../tools/check_curate_threshold.py    # Windows
python3 ../tools/check_curate_threshold.py  # Mac/Linux
```

stdout に `status` / `reasons[]` / `counts` の JSON が 1 行出る。分岐は **exit code** で行う:

- **exit 0 (below_threshold)** → curate は不要（何もしない）。Step 6 へ進む
- **exit 10 (curate_needed)** → stdout の JSON を控えて 5-2 へ
- **exit 2 (error)** → 窓口に informational として 1 行報告し、curate はスキップして Step 6 へ進む
  （閾値チェック失敗で worker クローズを止めない）

#### 5-2. single-flight 確認（coalesce）

spawn の**前に必ず** `mcp__renga-peers__list_panes` で既存 curator を確認する。分岐は
**列挙の curator と `.state/dispatcher/curate-inflight.json`（cwd は `.dispatcher/` なので
`../.state/dispatcher/curate-inflight.json`）の有無の組み合わせ**で決める:

- `name == "curator"` のペインが既に存在し、**inflight も在る** → **coalesce: 再 spawn しない**。Step 6
  へ進む。実行中のサイクルが raw 列挙（org-curate Step 2）を既に通過していた場合、
  今回の増分はそのサイクルでは拾われないが、**取りこぼしではなく遅延**である: 未処理の
  raw / pending はファイルとして残り続け、次回の worker close の閾値チェックで再評価される
  （worker close が長期間発生しないケースの保険は backlog Issue #501 / #502 の補助トリガー）
- `name == "curator"` のペインは存在するが **inflight が無い** → 下記の「live curator + inflight
  無し」に従い、**inflight を再取得してから** coalesce する（再 spawn しない点は上と同じ）
- 存在しない → 5-3 へ

worker close が短時間に連続した場合の `name_in_use` 衝突 / 別名重複起動（knowledge/ への
競合書き込み）をこの規約で防ぐ。

**live curator + inflight 無しを無報告で通さない**: この組み合わせは設計上到達しうる
（5-3 の spawn は成功したが inflight 書き込み前に dispatcher が `/clear` / crash した経路、
[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3-close) の
close 判定表で close を撃たずに inflight を削除する行（行 3 / 行 4 / 行 6）に落ちたのち、その
「消失確定」が実際には外れていた経路）。ここで inflight を作らずに coalesce だけすると、
**以後の worker クローズごとに curate が無報告で抑止され続ける**: 追跡が無いので監視ループ
Step 5.3 も回収せず、復帰は
[`.claude/skills/dispatcher-resume/SKILL.md`](../../.claude/skills/dispatcher-resume/SKILL.md)
Step 5 の inflight 再生成（= 次の handover / resume）を待つしかない。よって:

1. **inflight を再取得する**（フィールドの正本は 5-3 の JSON。再生成の形は上記
   `/dispatcher-resume` Step 5 と揃える）。`pane_id` は**この生存確認に使った列挙で
   `name == "curator"` かつ `role == "curator"` を指しているレコードの `id`** を、
   **列挙が返した JSON の型を変えずに**書き（tmux backend は `"%3"` のような文字列、
   WezTerm backend は整数。引用符の有無を型に合わせる — 詳細と理由は 5-3 の
   `pane_id` 注記）、`curate_result` は `null`、`started_at` は 5-3 と同じ決定的 UTC コマンドの
   出力をそのまま（手書きの local(JST) 時刻を `Z` で書かない）、`reasons` は **5-1 の JSON の
   `reasons[]`**（本サイクルの閾値チェック結果）、`extended: false` /
   `last_inspect_hash: null` / `last_inspect_ts: null` を初期値で書く。`trigger_task_id` は
   **書かない** — このフィールドは当の curator を spawn した CLOSE_PANE を指すもので、本サイクルは
   それではない（Step 5.3 の完了受領 / timeout 判定はこのフィールドを読まない。`/dispatcher-resume`
   の再生成も同じ理由で落としている）。`started_at` が**再取得時刻**になるため
   `CURATE_TIMEOUT_MIN` / `CURATE_HARD_CAP_MIN` はここから測り直しになる（curator の実 spawn
   時刻は復元できない。resume 側の再生成と同じ性質）。
   **列挙に `name == "curator"` かつ `role == "curator"` のレコードが無い**場合は
   **再取得しない**（数値 pane id を採れない形で書くと Step 5.3 が旧形式の残存ファイルと
   同じ扱いになる）。次項の報告だけ行って Step 6 へ進む
2. **窓口へ informational 報告する**（無報告で放置しない。人間の行動を求めない形）:

   ```
   mcp__renga-peers__send_message(to_id="secretary", message="curator ペインが live なのに追跡（curate-inflight）が無い状態を検出しました。inflight を再取得（pane_id={N} / started_at={再取得時刻}）して今回の curate は coalesce しています。{再取得できなかった場合はその旨と理由}")
   ```

3. 再取得後は**通常どおり監視ループ
   [`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3) が回収する**
   （完了受領 / timeout 管理 / 自タブ確立 → identity 照合を通した数値 close / hard cap 終端まで
   close 判定表 1 つに従う）。本 5-2 は追跡を戻すだけで、close の判定規則を新設しない

#### 5-3. curator ペインの spawn

```
mcp__renga-peers__spawn_claude_pane(
  target="dispatcher",
  direction="vertical",
  role="curator",
  name="curator",
  cwd="../.curator",
  permission_mode="auto",
  model="sonnet"
)
```

- `cwd` は caller（dispatcher、cwd=`.dispatcher/`）基準の相対解決なので `../.curator`
- `model="sonnet"` の理由: auto モードの safety classifier はセッションモデルと独立した専用モデルで動作し、承認判定はセッションモデルに依存しない（公式: https://www.anthropic.com/engineering/claude-code-auto-mode）。キュレーターの知見整理ワークロードは軽量・機械的側に分類できるため Sonnet で十分（ワーカーの既定 opus 方針とは独立の判断）
- `[name_in_use]` が返った場合は **live pane と stale 登録簿を切り分ける**。このコードだけでは
  「直前に別トリガーが spawn した真のレース」と「ペインは消えたのに登録簿に name binding だけが
  残っている **stale-binding**」を区別できず、後者を無条件 coalesce に倒すと **curator も inflight も
  無いまま curate が無報告で捨てられる**。切り分けは `list_panes`（Group A）だけで行い、**本分岐から
  `close_pane` は撃たない**:
  1. `mcp__renga-peers__list_panes` で `name == "curator"` の **live pane が実在するか**再確認する
  2. **live pane が在る** → 5-2 とのレース。**coalesce 扱い**でよい（再 spawn せず Step 6 へ
     進む。無報告でよい）。ただし追跡の有無は 5-2 と同じ規律で確かめる — この時点で
     `curate-inflight.json` が無ければ 5-2 の「live curator + inflight 無し」に従って再取得し、
     窓口へ報告してから Step 6 へ進む
  3. **live pane が無い**（`list_panes` に出ない）→ **stale 登録簿 binding の疑い**。列挙にペインが
     出ないので数値 pane_id を取り直せず、Step 3 冒頭の「数値 pane_id で撃つ」原則を満たせない。
     **ここで close は撃たない**（裸 name の `close_pane` は Step 3 冒頭の Group B 規則どおり禁止で、
     本分岐は契約 T-§4.2 の stale-binding carve-out を取る箇所ではない — carve-out を取るのは
     [`.claude/skills/pr-watch-pane/SKILL.md`](../../.claude/skills/pr-watch-pane/SKILL.md) Step 3 /
     Step 5 (b) と [`.claude/skills/org-pull-request/SKILL.md`](../../.claude/skills/org-pull-request/SKILL.md)
     の post-merge cleanup の 3 手順だけであり、契約もその 3 つとして記録している）。**stale binding を
     検出したので今回の curate をスキップした旨を窓口へ informational 報告し**（**無報告で捨てない**）、
     Step 6 へ進む。登録簿の掃除が要る場合は人間の判断に委ねる
  - **これは close 判定表に足す行ではない**: 判定表
    （[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3-close)）は
    「spawn 済み curator を追跡している `curate-inflight.json` をどう畳むか」の規則で、本分岐には
    **curator ペインも inflight もまだ存在しない**。表の対象外の別状況なので、表の disposition 規則と
    競合しない
- その他の `[<code>]` エラーは窓口に informational として報告し、curate をスキップして Step 6 へ進む

**spawn 成功（pane_id 取得）直後、5-4 の boot 確認より前に**、追跡状態を
`.state/dispatcher/curate-inflight.json` に書く（cwd は `.dispatcher/` なので
`../.state/dispatcher/curate-inflight.json`）。boot 確認・指示送信の途中で dispatcher が
`/clear` / crash しても、実在する curator ペインが untracked にならないようにするため:

> **`started_at` は決定的 UTC コマンドの出力をそのまま埋める（手書き厳禁・JST 禁止）**:
> [`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3) の timeout 管理は
> `now - started_at`（`now` は `date -u` 由来の UTC）で経過を測るため、`started_at` も
> **必ず UTC** でなければならない。dispatcher が現在の local 時刻（JST 等）を手で書いて末尾に
> `Z` を付けると、UTC より進んだ未来時刻が記録され（JST なら約 9 時間先）、`now - started_at`
> が負値になって 20 分 / 40 分の自動クローズが**永久に不発**になる（curator ペインが孤立する）。
> 値は次のコマンドの出力をそのまま埋めること（prose の「ISO-8601 UTC」という説明では local
> 時刻が混入しうるため、明示コマンドで決定的に得る）:
>
> ```bash
> # POSIX (bash) — dispatcher の既定。出力例（UTC）: 2026-06-14T00:26:00Z
> date -u +%Y-%m-%dT%H:%M:%SZ
> ```
> ```powershell
> # PowerShell 環境での等価（UTC 固定。Get-Date の既定は local なので ToUniversalTime 必須）
> (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
> ```

```json
{
  "started_at": "<上記 date -u コマンドの出力をそのまま。UTC のみ、JST-as-Z 禁止>",
  "pane_id": <spawn 戻り値の pane id を JSON の型どおりに書く。下記注意>,
  "reasons": ["<5-1 の JSON の reasons[] をそのまま>"],
  "trigger_task_id": "<本 CLOSE_PANE の対象だった task_id>",
  "extended": false,
  "curate_result": null,
  "last_inspect_hash": null,
  "last_inspect_ts": null
}
```

> **`pane_id` は「数値で書く」ではなく「backend が返した型のまま書く」**: pane id の JSON 型は
> **backend 依存**である — tmux backend（broker の既定）は `"%3"` のような**文字列**、
> WezTerm backend は**整数**を返す（[`.claude/skills/org-attach/SKILL.md`](../../.claude/skills/org-attach/SKILL.md)
> の `list_panes` 戻り値フィールド表、runtime `broker_queue_event.schema.json`: "Backend-native
> pane id: int on WezTerm, string (e.g. `\"%3\"`) on tmux"）。したがって
> **`"pane_id": %3` と引用符無しで書くと JSON として構文非妥当**になり、後続サイクルの
> Step 5.3 も `/dispatcher-resume` もこのファイルを読めず、curator の追跡が失われる
> （＝ live curator が inflight 無しで残り、5-2 の single-flight coalesce で以後の curate が
> 恒久抑止される、この schema がまさに防ぐはずの状態）。**文字列で返ったなら引用符付きの
> 文字列として、整数で返ったなら数値として**、戻り値の型をそのまま写すこと。
> 読む側（Step 5.3 / resume）も同様に、`list_panes` の `id` とは**型を変換せずに**突き合わせる。
>
> 「数値 pane_id で撃つ」という契約 T-§4.2 の語の "numeric" は**相対セレクタでない
> backend-native な id** の意であって、JSON の number 型を指してはいない。

> **`pane_id` は非同期 close の identity 照合の起点**: curator の close は本ハンドラではなく監視ループ
> [`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3) が別サイクルで
> 非同期に行うため、spawn 戻り値の数値 id をここに控えないと後続サイクルで失われる。監視ループは close の
> 直前に、まず照合に使う列挙を**自タブのものと確立**したうえで（Step 3 冒頭の 2 手段）、この `pane_id` を
> `list_panes` で照合し（`name == "curator"` かつ `role == "curator"` をなお指しているか）、
> 一致したときだけ `close_pane(target=<pane_id>)` する。相対セレクタ（`target="curator"` のような裸の name）へ
> フォールバックしない（契約 T-§4.2 Group B。理由は Step 3 冒頭）。照合が外れた（id recycle）場合は
> curator が既に消えていると現認できるので閉じずに inflight を削除し、**窓口へ informational 報告する**
> （close を撃たずに追跡を捨てる判断は必ず窓口に見えるようにする）。**その id が列挙に無いだけでは消失を
> 確定させない** — 単発の列挙は Step 3 (3-a) の裏取りゲートより弱い証拠なので、ゲートで確定するまで
> inflight を保持して次サイクルで再評価する。`pane_id` を欠く残存ファイルでも列挙に
> `name == "curator"` かつ `role == "curator"` のレコードが在れば**その数値 `id` で閉じる**。
> **inflight を保持するのは判定表の 2 行だけ**: 行 1（列挙を自タブと確立できない / 列挙が観測不能 =
> org のペインが一斉に出ない / 消失の裏取りが確定しない）と行 8（close が `[pane_not_found]` /
> `[pane_vanished]` **以外**のコードで失敗）は、削除せず保持して
> 窓口へ上げる（契約 T-§4.2 の "MUST be surfaced, not swallowed"）。この 2 つは hard cap 到達時に
> 「窓口へ escalate してから削除」で終端する。**「close の可否によらず削除する」無条件削除はしない。**
> 8 行の判定の正本は上記 Step 5.3 の close 判定表。

> **`curate_result` は「受領済みだが close 未確認」を次サイクルへ持ち越すフィールド**: 初期値は
> `null`（CLOSE_PANE ハンドラ側はこの `null` を書くだけで、以後この値に触らない）。監視ループ
> [`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3) の (a) が
> `CURATE_DONE` / `CURATE_SKIPPED` / `CURATE_ERROR` を受領したのに close 判定表の行 1 / 行 8 で閉じられず
> inflight を保持するとき、受領内容を `"done"` / `"skipped"` / `"error"` のいずれかで書き込む。
> これが無いと次サイクルの消失検知が**受領済みの curate を「CURATE_* 未受領のまま消えた」と誤報告**し、
> timeout 判定が既に完了している curator を stall 扱いする。

このファイルは [`.claude/skills/dispatcher-handover/SKILL.md`](../../.claude/skills/dispatcher-handover/SKILL.md) /
`/clear` / resume で**保持される側**の内部状態ファイル（`.dispatcher/CLAUDE.md`
「監視 gap を埋める内部状態ファイル」リスト参照）。resume 後の監視ループが
`started_at` 起点で timeout 管理を引き継ぐ。

> **state.db には書かない**: オンデマンド curator は ephemeral であり、`curator_pane_id` /
> `curator_peer_id` は **null のまま**が正常系。生存確認は `list_panes`（5-2）のみで行う。
> DB に書くと常駐前提が復活し、suspend / handover / dashboard の照合が誤る。

#### 5-4. boot 確認（Enter / list_peers poll）

`/org-start` Block D-1〜D-2 の dispatcher 分と同じ手順:

1. `mcp__renga-peers__send_keys(target="curator", enter=true)` で
   「Load development channel? (Y/n)」プロンプトを承認する
2. `mcp__renga-peers__list_peers` で `name="curator"` の peer 登録を poll する。
   未登録なら Enter を再送して再 poll（最大 3 回 retry）。
   **`list_peers` の直前に
   [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md)
   を Read し、`monitoring-read-only` の分岐を適用する**（同 reference §6 の表 #8）。
   capability 形かつ未承認なら列挙を登録確認に使わず破棄する（停止しない・待ち時間 0 分）。
   **`name="curator"` の一致だけで登録ゲートを開けない**（予約名は別 org の並走タブに
   同名で実在しうる。契約 T-§2.2 / 共有 reference §3-B-1）。
   **代替の readiness 判定は 5-5 の起動指示の送信そのもので行う**: `list_panes` の生存と
   `inspect_pane` のプロンプト表示は Claude の起動しか示さず peer 登録を示さないので、
   これを登録確認に代用すると 5-5 の**一度きりの `/org-curate` 指示が
   `[pane_not_found]`（broker では `[peer_not_found]`）で消え**、`curate-inflight.json` が
   timeout まで残る。したがって `list_panes` でペイン生存だけ確認したら 5-5 の
   `send_message` を送り、失敗したら Enter 再送とあわせて **retry する**（既存の
   最大 3 回 retry の予算をそのまま使う）。送達成功をもって boot と登録を同時に確定する。
   **送達に成功した時点で 5-5 は消化済みである。5-5 に戻って `/org-curate` をもう一度
   送らないこと** — 縮退経路の probe は 5-5 の送信「そのもの」であって別立ての試し送信では
   なく、二度送ると curate が二重に走る。そのまま 5-6 以降へ進む。
   3 回 retry しても送達できなければ従来どおり次項の破棄・skip へ進む

   **capability 形かつ承認済み（同 reference §2 の `first_drive` が `recorded`）のときは、共有
   reference §1-2 の三値判定を `curator` に適用する。「在」のときだけ登録ゲートを開け、その 1 件の
   数値 `id` で 5-5 を送る（`to_id=<その数値 id>`。名前宛にしない）。「不在」「unknown」はゲートを
   開けず、上記の poll をそのまま続ける。**（判定手順・評価順・宛先規則の正本は §1-2。ここに
   重ねて書かない）**旧版 fallback（現行配備の全 backend）と未承認縮退では従来どおり
   `to_id="curator"` の名前宛のままで、今日の挙動は変わらない。**
   この枝で本節が足す帰結は 2 つだけである:
   - **`unknown` を次項の破棄・skip の根拠にしない** — 次項へ進む条件は上記 retry 予算を使い
     切ったことだけで、`unknown` はその条件を早めも遅らせもしない。
   - **この数値 `id` を `close_pane` のセレクタに使わない** — `list_peers` 由来の peer id なので、
     close は次項のとおり 5-3 の spawn 戻り値の pane_id を起点にする（契約 T-§4.2）。close の
     可否そのものは次項がそのまま委ねる close 判定表が決める（本節は close の判断を持たない）。
3. 3 回 retry しても登録されない場合はペインを破棄し、窓口に informational として報告して curate を
   スキップし、Step 6 へ進む。**close の可否と `curate-inflight.json` の始末は
   [`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3-close) の
   close 判定表 1 つに委ねる**（この give-up 分岐だけの別ルールを足さない。表の外に分岐を作ると、
   表が塞いだリークが表の外に開く）。**この分岐は spawn 直後で id が手元にあるので裸 name の
   `target="curator"` は使わない**（Step 3 冒頭と同じ Group B 規則）: **5-3 の spawn 戻り値で得た
   数値 pane_id**（= `curate-inflight.json` の `pane_id`）を起点に、close の直前に
   `mcp__renga-peers__list_panes` でその pane_id が `name == "curator"` かつ `role == "curator"` を
   なお指していることを identity 照合し、一致したときだけ
   `mcp__renga-peers__close_pane(target=<spawn 戻り値の pane_id>)` する。**照合に使う列挙は、その前に
   自タブのものと確立する**（Step 3 冒頭の 2 手段 = 単一タブ backend / `caller_scope`）。
   **spawn 戻り値が手元にあることは確立の代わりにならない** — その id 自体が自タブ由来でも、
   照合に使う列挙が自タブと確立できていなければ照合結果を信用できないためで、2 手段のいずれも成立しない
   なら close を撃たず判定表の行 1 として `curate-inflight.json` を保持し、理由を添えて窓口へ報告する。
   **spawn 戻り値がその場で手元にある同期経路なので、この分岐では判定表の行 2（identity 照合して
   数値 close）が主経路になる**が、それ以外の行に落ちたときの始末も表のとおりに行う:
   - **照合が外れた（id recycle）**（行 3）→ close せず `curate-inflight.json` を削除し、その旨を
     窓口への informational に含める（その id は既に別ペインを指しており、curator は消失済みと現認できる）
   - **その id が列挙に無い**（行 4）→ **消失を確定させず `curate-inflight.json` を保持**し、その旨を
     窓口への informational に含める。行 4 の削除は Step 3 (3-a) の裏取りゲートで消失が確定したときだけで、
     そのゲートは同区間の `pane_exited` との突き合わせと連続サイクルの一致を要求するので、
     **窓口の 1 通で起動する本ハンドラでは完走できない**（Step 3 の 4 と同じ理由）。判定は監視ループ
     Step 5.3 の次サイクルに委ね、保持は同 Step の hard cap 終端規則が終端する
   - **close が `[pane_not_found]` / `[pane_vanished]` を返した**（行 7）→ 既に閉じた扱いで削除する
   - 列挙を自タブと確立できない / 列挙が観測不能（行 1）/ close が `[pane_not_found]` / `[pane_vanished]`
     **以外**のコードで失敗した
     （行 8。`server_too_old` / `[no_backend]` / `[tool_not_authorized]` 等）→ **`curate-inflight.json` を
     削除せず保持し**、理由 / エラーコードを添えて窓口へ上げる（契約 T-§4.2 の "MUST be surfaced,
     not swallowed"）。**live な curator が残ったまま追跡だけ捨てると、5-2 の single-flight 確認が
     以後の curate をすべて抑止しているのに、その事実を誰も持たない状態になる**。保持した inflight は
     宙吊りにならない — 監視ループ Step 5.3 の hard cap 終端規則（`CURATE_HARD_CAP_MIN` 到達で
     窓口へ escalate してから削除）が終端する

#### 5-5. 起動指示の送信

5-1 で控えた JSON を**そのまま**埋め込んで送る（dispatcher 側で再解釈・再計算しない）:

```
mcp__renga-peers__send_message(to_id="curator", message="あなたはキュレーターです。/org-curate を 1 回だけ実行してください（/loop 禁止）。起動理由: {check_curate_threshold.py の stdout JSON}。完了時は改善提案（secretary 宛て）を送った後、必ず dispatcher 宛て direct send で CURATE_DONE / CURATE_SKIPPED / CURATE_ERROR のいずれかを送ってください。")
```

#### 5-6. 監視ループへの即時復帰（ブロッキング待ちをしない）

**ここで CURATE_* を待たない**。完了待ちで CLOSE_PANE ハンドラをブロックすると、その間
他 worker の `/loop 3m` 監視（stall / relay gap / silent dead-lock 検出）が止まり、
安全網の目的と矛盾するため、完了受領とクローズは監視ループ側
（[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3)）に委ねる。

1. 追跡状態 `curate-inflight.json` は **5-3 の spawn 直後に書き込み済み**であることを確認する
   （未書き込みならここで書く — 内容は 5-3 参照）
2. **CURATE_* を待たず即座に Step 6（triage scan）へ進み、それも完了したら `/loop 3m` 監視ループへ
   復帰する**（curate の完了受領は監視ループ側の責務。ここでブロックしない）
3. 今回の worker close で全 worker ペインが閉じていても、`curate-inflight.json` が存在する
   間は監視ループを**停止しない**（curate 完了監視のため継続。
   [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) 末尾の停止条件参照）

#### 5-7. 完了受領・timeout 管理・クローズ（監視ループ側の責務）

CURATE_DONE / CURATE_SKIPPED / CURATE_ERROR の受領、20 分 timeout の観測、
**列挙を自タブと確立したうえで、inflight に控えた `pane_id` を `list_panes` で identity 照合してからの
数値 close**（`pane_id` を欠く
残存ファイルでは列挙から `name == "curator"` かつ `role == "curator"` のレコードを引き、その数値 `id` で
閉じる。閉じられないのは列挙を自タブと確立できないサイクル・列挙が観測不能なサイクル・消失の裏取りが
確定しないサイクルと、close が `[pane_not_found]` / `[pane_vanished]`
**以外**のコードで失敗した場合だけで、そのときは inflight を**保持**して窓口へ上げ、hard cap 到達時に
escalate してから削除する。5-3 の注記と Step 5.3 の close 判定表を参照）、
inflight ファイルの削除は、すべて監視ループの
[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3) が通常サイクル内で行う。
本 CLOSE_PANE ハンドラ側に残る作業は無い。state.db への後始末も不要（5-3 の注記どおり
そもそも書いていない）。

### 6. work-discovery triage scan（worker クローズ時 = pane 枠が空いた契機）

worker クローズは pane 枠が空く瞬間であり、設計上「次の仕事を入れられるタイミング」である
（[`docs/design/work-discovery-triage.md`](../../docs/design/work-discovery-triage.md) §6.3 案 C dispatcher-loop 拡張 /
§8 post-merge トリガ点の合流）。Step 5 のオンデマンド curator と同じ「**worker クローズ時に条件チェック →
該当時のみ窓口へ転送**」パターンに乗せ、**CLOSE_PANE 処理の最後（Step 1〜5 完了後）に毎回** triage scan を
1 回走らせ、着手可能な候補があれば候補 JSON を**窓口へ転送するだけ**で終わる。

> **不変条件（設計 §7、厳守）**:
> - **INV-4 窓口 = 唯一の人間接点**: dispatcher は scan 結果を**窓口（secretary）へ送って終わり**。
>   人間にも GitHub にも直接到達しない。候補は必ず「dispatcher → 窓口 → 人間」の経路を通る。
> - **INV-1 propose-only / INV-2 着手判断は人間**: scan は read-only（Issue を読むだけ。spawn / commit /
>   PR を一切しない）。dispatcher は候補を**提案として転送するだけ**で、自分で着手判断・`/org-delegate`
>   起動をしない。ランク 1 位（推奨）の自動着手も禁止。
> - **INV-5 dispatcher は調査しない**: scan は決定的ツール実行であって「調査」ではない。dispatcher は候補の
>   中身を自前で精査・実装しない。深掘りが要る候補は人間ゲートを通った後の委譲ワーカータスクになる。

#### 6-1. scan の実行（1 コマンド・シェル非依存）

`--all-registry-repos` を付けて scan を **1 回** 実行する。repo セット解決（`registry/projects.md` の triage 列
（既定 include / 明示 opt-out）と `registry/org-config.md` の `triage_home`（既定 off）から `--repo owner/repo`
を決定的に導出。設計 §10.4）は scan が `tools/work_discovery_repos.py` を **プロセス内で呼んで**行うので、
resolver を別コマンドで走らせる必要はない。resolver は read-only（`git remote get-url` と任意の `gh repo view`
読み取りのみ）で INV-1〜5 を崩さない。

```bash
# ディスパッチャー cwd は .dispatcher/ なので 1 段上がリポジトリルート。
# Windows
py -3 ../tools/work_discovery_scan.py --trigger worker_close --all-registry-repos
# Mac/Linux
python3 ../tools/work_discovery_scan.py --trigger worker_close --all-registry-repos
```

- **`REPO_FLAGS=$(… --format flags)` を経由する旧手順は使わない**（Issue #829）。フラグ文字列が複数引数に
  なるかは**呼び出し元シェルの単語分割**次第で、ペインの login shell である zsh は既定 `SH_WORD_SPLIT` off の
  ため未クォートの `$REPO_FLAGS` を分割せず、argparse には 1 引数として届いて **worker クローズ時の scan が
  毎回 exit 2 で失敗していた**（bash では 4 引数に分割されるので bash では気付けない）。zsh 専用の
  `${=REPO_FLAGS}` も対症療法なので使わない。上の 1 コマンド形はシェルを一切経由しないので zsh / bash /
  Windows `py -3` で同一に動く。
- **解決失敗は scan 自身が exit 2 にする**（`--repo` 無し = gh カレントリポジトリの暗黙 scan へ無言
  フォールバックしない）。呼び出し側の `if … then … else` 分岐は不要になった。exit 2 の扱いは 6-2 / 6-3 /
  6-4 の error 経路に一本化されている。
- 既定状態では registry の GitHub URL 行が全て scan 対象に並ぶ（home repo は `registry/org-config.md` の
  `triage_home: on` のときだけ先頭に付く）。単一 repo scan になるのは scan 対象が 1 件のときだけ。
- **監査情報は scan の出力に載る**: 解決結果は stdout JSON の `repo_resolution`（`repos` / `home_repo` /
  `triage_home` / `included` / `opted_out` / `skipped` / `signals`）にそのまま入る（`--all-registry-repos` を
  付けなかった場合は `null`）。**exit 2 の error envelope にも載る**ので、解決失敗の理由もそのまま窓口へ渡せる。
  resolver を `--format json` で二度目に走らせる必要はない。
- **`repos[0]` を控える（6-3 の ref 用）**: `repo_resolution.repos[0]` が resolver の先頭 repo（= scan 対象が
  1 件のときに `recommendation.repo` が `null` に畳まれる場合の補完値）。`home_repo` ではない。
- `--trigger worker_close` は出力 JSON の `generated_for` に載る文脈ラベル（監査用、設計 §8）。
- 空き worker pane 数を把握していれば `--free-panes <n>` を添えてよい（任意。Step 3 で close を保留した場合、
  そのペインは空いていないので数に入れない）。`parallelizable` 候補の
  ランキングを押し上げるだけで、候補上限 N は変えない（計算層の Phase 1 契約）。
- stdout は単一 JSON（設計 §5.1）。分岐は **exit code** で行う（JSON パース結果に依存しない。
  curator threshold ツールと同方針）。

#### 6-2. exit code 分岐

- **exit 0 (no_candidates)** → 着手可能な候補なし。窓口へは送らない。監査のため scan 実行を journal に
  記帳（6-3）して CLOSE_PANE フロー完了。
- **exit 10 (candidates_found)** → stdout の JSON を控えて 6-3（記帳）→ 6-4（窓口へ転送）。
- **exit 2 (error)** → 窓口に informational として 1 行のエラー通知を送る（6-4 のエラー形）。scan 失敗で
  worker クローズを止めない（CLOSE_PANE フロー自体は完了扱い。候補ゼロと誤読させず、scan のクラッシュを
  握り潰さないため窓口へ届ける）。repo セット解決の失敗もこの枝に入る（6-1）。
  **exit 2 を握り潰さない（Issue #829）**: 「候補が出ないのが普通」と受け取られると次タスク提案の仕組みが
  事実上死ぬ。exit 2 では **6-3 の error 記帳と 6-4 の窓口通知を必ず両方行う**（片方だけで済ませない）。
  エラー本文は stdout JSON の `error` と `repo_resolution`（あれば `signals` / `skipped`）から要約する。

#### 6-3. 監査ログ（journal 記帳）

scan 実行を journal イベントに記帳する（生 JSON を `>>` で直書きしない。helper 契約は Step 5 系統と同じく
`.dispatcher/CLAUDE.md`「delegate-plan helper」節末尾の `tools/journal_append.sh` 説明を参照）。payload は
候補件数・推奨 ref・トリガを載せる:

```bash
# exit 10 の例。candidate_count / recommendation_ref は scan の stdout JSON から取る。
bash ../tools/journal_append.sh work_discovery_scanned \
    trigger=worker_close candidate_count={JSON.candidate_count} recommendation_ref={owner/repo#N}
# exit 0 の例（候補ゼロ。recommendation は無いので省略）。
bash ../tools/journal_append.sh work_discovery_scanned trigger=worker_close candidate_count=0
# exit 2 の例（失敗。candidate_count / recommendation_ref は組めないので載せず、
# 代わりに outcome=error と 1 行要約を残す。silent skip にしないための記帳＝省略不可）。
bash ../tools/journal_append.sh work_discovery_scanned \
    trigger=worker_close outcome=error note="{JSON.error の 1 行要約}"
```

- **`recommendation_ref` は `owner/repo#N` 形に統一**（cross-repo triage で `ja#60` と `runtime#60` を
  区別可能にするため。旧 `recommendation_issue=<番号>` は廃止）。組み立て:
  `{JSON.recommendation.repo}` が非 null ならその値、`null`（scan 対象が 1 件で単一 repo 形に畳まれた場合）なら
  6-1 で控えた resolver の **`repos[0]`** を使い、`#` + `{JSON.recommendation.issue}` を続ける。
  例: `recommendation_ref=suisya-systems/claude-org-ja#531` / `recommendation_ref=aainc/token-tracking#42`。
- 候補ゼロ（exit 0）や `recommendation` が `null` のときは `recommendation_ref` を省略する。

> **記帳は delivery 層（dispatcher）の責務**: 計算層ツール `work_discovery_scan.py` 自身は state.db に一切
> 書かない（read-only・副作用ゼロ、設計 §7.1）。journal への記帳を行うのは候補を配達する dispatcher 側で
> ある（§7.1 の計算層 / delivery 層の分離）。
>
> **イベント名の台帳定義は別タスク**: `work_discovery_scanned` の `docs/journal-events.md` への定義追記は
> 本タスクのスコープ外（実装タスク側、設計 §10 / §7.1）。`tools/journal_append.sh` は event 名を free-form
> 文字列として受け取り key=value をそのまま記帳する（台帳を参照・検証しない）ので、本手順は台帳定義の
> 有無に依存しない。

#### 6-4. 窓口への転送（exit 10）/ エラー通知（exit 2）

**exit 10**: scan の stdout JSON を**そのまま**埋め込んで窓口へ送る（dispatcher 側で再解釈・再計算・
再レンダリングしない。人間可読 §5.2 形式へのレンダリングは窓口の責務。Step 5-5 の「JSON をそのまま
埋め込んで送る」と同方針）:

```
mcp__renga-peers__send_message(to_id="secretary", message="WORK_DISCOVERY_CANDIDATES: worker {task_id} クローズで pane 枠が空きました。triage scan の候補を転送します（提案のみ / 着手判断は人間です）。\n\n{work_discovery_scan.py の stdout JSON}")
```

**exit 2**: エラーを窓口に informational として 1 行通知する:

```
mcp__renga-peers__send_message(to_id="secretary", message="WORK_DISCOVERY_SCAN_ERROR: worker {task_id} クローズ時の triage scan が失敗しました（exit 2）。候補提示はスキップします。{stdout JSON の error 要約}")
```

- **exit 2 の通知は省略しない**（Issue #829）。scan の失敗が窓口に届かないと「候補が出ないのが普通」と
  受け取られ、次タスク提案が黙って死ぬ。repo セット解決の失敗なら `repo_resolution.signals` /
  `repo_resolution.skipped` の 1 行要約を添えると窓口が原因（registry 行が無い / `triage_home` off / パスが
  GitHub URL でない等）まで人間へ伝えられる。

- 送信先は **必ず安定名 `to_id="secretary"`**（`.dispatcher/CLAUDE.md`「窓口への返信方法」参照）。
- dispatcher は窓口へ送って終わりで、人間 / GitHub の人間可視面へは触れない（INV-4）。
- 送信後（または exit 0 で送信しなかった場合）は CLOSE_PANE フローを完了し、`/loop 3m` 監視ループへ
  復帰する。triage scan は read-only ツール実行 + 窓口への 1 送信のみで、curate のような完了待ち
  （CURATE_* / inflight 管理）は持たない。
