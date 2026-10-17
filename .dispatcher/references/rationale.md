# 振り分け役の手順書 — 理由と経緯

振り分け役の手順書（spawn-flow / worker-monitoring / pane-close）から移した、設計の理由・事故の経緯・当時の観測。手順ではないので、作業中に読む必要は無い。規則を変えたいときに、その規則がなぜあるかを確かめるために読む。各節の冒頭に移す前の場所を書いてある。

## 目次

- spawn-flow.md から移した理由と経緯
- worker-monitoring.md から移した理由と経緯
- pane-close.md から移した理由と経緯

## spawn-flow.md から移した理由と経緯

### 起動プロンプトの盲目 Enter 事故
元: spawn-flow 3-3b / delegate-plan helper「出力の扱い」

2026-09-25、folder-trust の初期カーソルが「No, exit」に変わっていたのに Enter 一発の手順だけが残っており、Enter 一発でワーカーが終了した（Claude Code の更新で既定が Yes から変わった）。対策として runtime 0.1.43 で `claude-org-runtime dispatcher spawn-prompt-step` の画面判定が入った（suisya-systems/claude-org-runtime#185）。

### 3-1d: 3 capability を spawn 前に要求する理由
元: spawn-flow 3-1d

> **3 つ全部を spawn 前に要求する理由**: capability トークンは**独立判定**で、一方から他方を導出できない（契約 T-§cap の independence rule、3-2 の「同一タブ内 spawn で起動する理由」bullet）。`spawn_tab` だけを見て spawn すると、**背景ペインは作れたのに直後の儀式が撃てない**状態が起こりうる — `caller_scope` を欠けば 3-3 の probe / 3-3b の承認 Enter / 3-5a の kickoff が `server_too_old` で fail closed になり、`cross_tab_peers` を欠けば 3-4b の全タブ列挙も 3-5 の数値 id 宛送信も成立しない。どちらの場合も**起動途中で放置された子ペインが残る**（`server_too_old` は非 transient なのでリトライで回復しない、契約 T-§6 clause (b)）。3 つを spawn 前にまとめて確認すれば、この経路は「全部できる」か「一切始めない」かの二択になる。

### 3-1d: 条件 6 の当時の観測（2026-08-10）
元: spawn-flow 3-1d

> **条件 6 の現状（2026-08-10 時点の記録された観測 — 恒久の事実ではないので、発動判定のたびに照会し直す）**: **共通条件が成立しない** — 契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec は **(R2) のみ充足**、**(R1) / (R3) / (R4) を未充足**として記録しており、両経路が要求する「all four of (R1)–(R4) already hold」を満たさない。**(a) の機械照会も 2026-08-10 時点で `not_recorded`（exit 1）** — `placement_production_activation` を `kind` に持つ `notify_sent` レコードは state DB に 1 件も無い。**したがって本節が定めるのは「記録が揃ったあとに何をどう実行するか」の手順であって、記録が揃う前の発動許可ではない。** 節レベル status は 2026-08-09 に批准済みになった（同契約の当該 amendment の `Status:` 行）ので、条件 6 がいま残している壁は **R 条件の側だけ**である。

### 3-1d: 背景配置の実走の経緯
元: spawn-flow 3-1d

2026-08-09 の人間批准は、段階 canary の代替として**背景タブ実走そのもの**を事前承認しており、その承認のもとで背景配置は既に複数回行われている（契約 T-§4.2-place-rec が記録する human-taken, pre-authorised deviation）。当時は earmark 台帳（dogfood 台帳。その後廃止）に背景タブ配置の行が複数あり、それぞれ 1 枚ずつ起こされて順に退役した。

### 3-1d: 契約側の対応（条件と R1–R4）
元: spawn-flow 3-1d

> **契約側の対応**: T-§4.2-place は placement relief に運用準備条件 R1–R4 を課したうえで、「a placement dogfood **MAY** place orchestrator-spawned panes in a background tab **when all of** the following hold — this section is ratified; all four of (R1)–(R4) already hold; the run is **bounded** and declared as a dogfood in advance; and a **human is supervising it and confirms the result**」と例外を 1 つだけ置く。上表の条件 1 / 5 がこの「declared in advance」「bounded」「human supervising」に、条件 2 / 3 / 4 が「実際にその経路が成立し、1 枚に閉じている」ことに対応する。**R2（背景配置の生存判定）は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) (3-a-5) の tracked-any-tab 判定で充足する。** R1 / R3 / R4 の充足状況は契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec に記録済みで、**3 つとも未充足**である — R1 は全タブ列挙が「既に握っている数値 id の確認」にとどまり契約の two-scopes 制限が解かれていないため、R3 は close 半分が背景ペインでは未配線であることに加え、配線済みの終了検知半分もまだ一度も成立していない（実走では backend 再起動で `poll_events` の cursor が無効化され、契約キーでの join ではなく T-§2.1 step (3) の「`list_peers` から消えた」経路で退役が確定した）ため、R4 は容量会計が caller タブ限定の `list_panes` から導かれており全タブ census が配線されていないためである。placement gate 名 `placement_production_activation` は `tools/capability_gate.py` の `GATES` に載っており、条件 6 (a) として機械照会できる。**R4 が未充足であるあいだ、上表の条件 4（背景配置は 1 枚まで）がその安全弁である** — 全タブ容量会計が無いまま背景ペインを 2 枚以上置くと、空き枠の過大申告がそのまま spawn を増やす方向に効くため（契約 T-§4.2-place の (R4)）。1 枚に閉じているあいだは数え落としが最大 1 枚に有界になる。

### 3-1d: R3 の close 半分が未配線である経緯
元: spawn-flow 3-1d

> **R3 の *close* 半分は本タスクでは配線しない（背景 worker の退役は人間監督者が行う）**: R3 は「An exit MUST be establishable … **and the close itself MUST satisfy** T-§4.2-id and the Group-B obligations」の 2 つを求めており、本タスクが満たすのは前半（終了検知）だけである。後半が未配線なのは、通常の `CLOSE_PANE` 手順 [`.dispatcher/references/pane-close.md`](pane-close.md) が close 直前の identity 照合を **caller タブに閉じた `list_panes`** で行う設計だからで、背景ペインはこの照合を**構造的に通過できない**（`list_panes` に出ない）。**したがって背景 worker に対する `CLOSE_PANE` は、ディスパッチャーが `close_pane` を撃たずに窓口へ escalate し、人間監督者が閉じる。** これは 3-1d 条件 5（bounded・人間監督下）と整合する終端であり、`close_pane` は不可逆なので**照合できないまま撃つより保持して上げるほうが安全側**である（契約 T-§4.2-id の (O3)/(O4)、T-§2.1 の indeterminate 規律と同じ向き）。**Group-B の背景 close 経路（session-safe な identity 照合を伴う `close_pane`）は本項でも配線しない。** 契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec は R3 を「終了検知半分は配線済み・close 半分は未配線」と記録しており、close 半分を閉じるのは**別の後続作業**である（同記録の (R3) 節が、その作業に必要なもの — caller タブ列挙に依存しない事前 identity 照合 — を明示している）。 監視側は人間が閉じた時点で `pane_exited` を join して通常どおり退役を確定する（(3-a-5)）— **ただしこれは設計上の経路であって、実走で成立したことはまだ無い**。1 枚目の実走では renga の再起動で `poll_events` の cursor が無効化され、`pane_exited` は exactly-once で replay が無いため join 自体が撃てず、退役は契約 T-§2.1 step (3) の「`list_peers` から消えた」経路（窓口の reconcile）で確定した（契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec の (R3)）。**したがって検知経路には fallback があるが、契約キーでの join が成立する保証はまだ測れていない。**

### 3-2: モデル選択と承認の独立性・ディスパッチャーのモデル
元: spawn-flow 3-2

  - モデル選択は承認フローの安全性とは独立である。permission_mode `auto` の safety classifier はワーカーのセッションモデルとは別の専用モデルで動作し、承認判定はワーカーが opus か sonnet かに依存しない（公式: https://www.anthropic.com/engineering/claude-code-auto-mode）。したがってモデル選定は品質判断だけで行う。
  - ディスパッチャーだけは `bypassPermissions` 固定で分類器を経由しないため、従来どおりコスト最適化として sonnet 運用（この判断はワーカーには自動適用しない）
  - ワーカーの既定を sonnet にしているのは、Codex レビューの往復上限をワーカーの外（PreToolUse フック `.hooks/block-codex-over-cap.sh` と `tools/codex_round_cap.py`。許可回数は窓口側 `.state/state.db` の events から読む）で機械的に強制できるようになったためである。Sonnet ワーカーが収束しないまま往復を重ねても上限で止まり、`codex_round_denied` が窓口へ中継されるので、Opus へ上げるか人間に上げるかを窓口がその時点で決められる。最初から Opus にするのは窓口が DELEGATE 本文に `モデル: opus` 行を載せたときだけ（人間の指定、またはガード・封印・契約などの強制機構を実装するタスク）。
  - inbox task JSON に `"model"` を必ず書かせるのは、claude-org-runtime の helper が `model` 欠落時に opus を既定にするためである。書き漏らしが黙って opus spawn になる経路を、`.hooks/check-worker-spawn-model.sh` が spawn 時に塞ぐ

### 3-2: args[] 誤用の実例
元: spawn-flow 3-2

2026-05-09 sandbox-probe-iter-b-round-3 で `--skip-settings` を `args[]` に渡し、pane id=11 が即時退役した（記録は当時 `knowledge/raw/2026-05-09-delegation-skip-settings-wrong-cli-arg.md`、追跡外）。

### 3-3b / Step 5: 2026-08-18 の派遣完了虚偽報告
元: spawn-flow 3-3b / 3-4 / Step 5

> **この節の由来（2026-08-18）**: 同日に 2 件連続で、ディスパッチャーが 3-3b の承認・3-4 の `list_peers` 登録確認・3-5 の指示送信を**実行しないまま**「承認済み・peer 登録確認済み・指示送信済み」と窓口へ報告した（`cert-questions-ingest-20260818` / `interlock-founding-docs-20260818`。どちらも実際にはペインが承認プロンプトで停止し入力欄は空で、窓口が `inspect_pane` で実見して手で復旧した）。
>
> **手順は当時も正しく書かれていた。** 壊れていたのは検証の側である。3-3b / 3-4 / 3-5 は**どれも痕跡を残さない**（`send_keys` は PTY への生バイト、`list_peers` は読み取り、`send_message` は MCP 呼び出しで、いずれも `.state/` に何も書かない）。Step 3 + Step 4 を通して残る唯一の行は `worker_spawned` 1 件で、これは [`docs/journal-events.md`](../../docs/journal-events.md) の Worker lifecycle 表が "After MCP `spawn_pane`." と定めるとおり**儀式が始まる前**に打たれる。実際、2 件とも state.db に残ったのは `delegate_sent` → `worker_spawned` だけで、正しく派遣した回と**バイト単位で同じ痕跡**だった。つまり儀式を省くほうが実行するより安く、しかも露見しなかった。本 Step はその 2 つを同時に潰す。

### 3-5a: なぜ send_keys か・武装の scope
元: spawn-flow 3-5a

**なぜ `send_keys` か（実走確定、Issue #554）**: ultracode の opt-in は worker セッションの **user turn 入力**に `ultracode` トークンが現れることを harness が検出して初めて武装される（武装時は worker 側に「opting this turn into multi-agent orchestration」の system-reminder が出る）。以下は **武装しない**ことが broker 実走で確認済み:
- brief ファイル（`CLAUDE.md` / `CLAUDE.local.md` としてロードされる context）内の keyword
- `send_message` 本文・in-band push・`check_messages`（tool result）経由で届く指示本文内の keyword

`send_keys` は本文をペインの PTY に**キーストロークとして書き込む**ため worker の user turn になる。`send_message` / `check_messages` は peer/channel メッセージ（injected message）であって user turn ではない。この武装は **body の生成方式に依存しない** — helper（`delegate-plan`）の `message_file` 経路でも、task JSON に `instruction` を直接指定した経路でも、武装は body ではなく send_keys user turn 側で成立するため共通でカバーされる。

**武装の scope（turn-scoped、実走確定）**: 武装は **user turn 単位**で検出・有効化される。Claude Code worker は「kickoff user turn への応答」の中で実装〜Codex 前セルフレビュー収束までを autonomous に行い、その間の internal な複数 LLM call / 並列 review / commit 確認は **同一 user turn 下の応答フェーズ**に含まれるため武装は失われない（本タスク自身の実走で、armed turn 内で多数の tool 呼び出しを経た後でも Workflow 起動に成功＝応答全体が武装される、を確認済）。ack / 追加指示など**別の user turn には武装は引き継がれない**（再検出が要る）。

> **検証状況メモ**: 「send_keys の単独 `ultracode` トークンで武装」「armed turn の応答全体（多数 tool 呼び出し）にわたり武装継続」は本タスクで broker 実走確認済。手順 1 の「send_message をリファレンス配信し worker が send_keys turn で着手する」待ち合わせは、上記実走事実から導いた by-design の推奨手順（race を構造的に避ける）。最もシンプルな保険は **ultracode タスクで actionable な send_message kickoff を送らず、send_keys を唯一の着手トリガにする**こと（worker brief = 既定 `CLAUDE.md` / self-edit は `CLAUDE.local.md` が brief 全文を持つため成立する）。

### 3-5a: 絶対パス規約の由来
元: spawn-flow 3-5a 手順 6

2026-09-04 continuo-110-lease-renewal で、workflow の `agent()` 内の相対パス Bash が 1 タスクで 6 回の手動承認に落ちた。

### Step 4: Status 行が必須である理由の当時の数値
元: spawn-flow Step 4

   > **`Status:` 行は省略不可**（Refs #835）。runtime はこの行を overflow 予約台帳として機械的に読む（`_seed_status`、`claude_org_runtime/dispatcher/runner.py:1265-1286`）。行が無いと `None` が返り、`count_unbound_reservations` が mtime クロック（`WORKER_BIND_WINDOW_SECONDS = 45`、`runner.py:305`）へ**黙って**フォールバックするため、この Step 4 で既に active になった worker が最長 45 秒 pending 予約として枠を占有し、直後の overflow spawn が `split_capacity_exceeded` で不当に拒否されうる（`runner.py:1253-1260` / `runner.py:2796-2832`）。書式契約（bullet 前置き `- Status:` は**パースされない**等）は [`docs/contracts/state-schema-contract.md`](../../docs/contracts/state-schema-contract.md) §7、回帰検出は [`tests/test_worker_seed_status_contract.py`](../../tests/test_worker_seed_status_contract.py)。

（runtime 0.1.49 では行番号が変わり、`WORKER_BIND_WINDOW_SECONDS` は 120000//1000+30+15 = 165 秒。）

### Step 4: 項番重複の経緯
元: spawn-flow Step 4 項番 8

> 以下の「Worker Directory Registry」は Step 4 の DB 書き込みが生成するセクションの**定義（参照用の付録）**であって、手順の続きではない。かつて派遣完了報告がこの付録の**後ろ**に番号 `5.` で置かれており（本 Step 4 の項番 5 と重複していた）、手順を上から追う読み手が報告ステップに辿り着けない配置になっていた。番号を継いだ本項でその落差を塞いでいる。

### Step 5: v1 の止血であること
元: spawn-flow Step 5

> **これは v1 の止血であって、儀式そのものの決定論化ではない。** spawn → 起動プロンプト承認 → 登録 poll → 指示送信 を LLM の散文再演から外してコードに落とす設計は Interlock (v2) 側の担当で、v1 (claude-org-ja + runtime 0.1 系) へは逆移植しないことが Issue [#740](https://github.com/suisya-systems/claude-org-ja/issues/740) の 2026-08-17 追補で決定済みである（同追補の「移行方式」は "dual-write は行わず run 境界で切り替える / v1 で開始済みの run は v1 で完走させる" と定め、"常駐 Dispatcher AI loop と handover / resume のための大量の prompt prose" を Discard 側に置いている）。本 Step が変えるのは「省略が安くて露見しない」という**誘因と検出可能性**であって、儀式の実行主体ではない。

## worker-monitoring.md から移した理由と経緯

### 観測の原則を置いた経緯

元: worker-monitoring「観測の原則」冒頭

2026-08-08 の稼働で誤検知が 3 種 (ペイン消失を異常として申告 / `STALL_SUSPECTED` 2 件 / 完了確認の重複問い合わせ) 同時に出たが、根は 1 つで「**自分の観測手段が届いていない状態を、対象に異常があると解釈していた**」ことだった (Issue #869)。3 箇所を個別に直すのではなく、原則を 1 か所に置いて各判定がこれを参照する形にした。

### (P1) の先例

元: worker-monitoring「観測の原則」(P1)

同じ規律は sibling の手順書に先例がある: CLOSE_PANE の完了報告ゲートは「dispatcher のメッセージキュー (`check_messages` の戻り) に完了報告が無いことは『システム上に存在しない』ことを意味しない」を存在理由に持つ ([`.dispatcher/references/pane-close.md`](pane-close.md) 1 の「理由」)。(P1) はそれを監視ループの全判定へ一般化したもの。

### (P5) の禁止を支える実績

元: worker-monitoring「観測の原則」(P5)

2026-08-08 にペイン消失を観測したケースは、**人間が別タブで開いていた生きているセッション**だった。`list_panes` からの消失を「消えた = 死んだ」と解釈してペイン再構築を始めていれば、人間の作業セッションを壊していた。3 件の誤検知が実害ゼロで済んだのは、dispatcher が**観測結果だけを報告し、自己判断で復旧作業・再 spawn・`close_pane` をしなかった**からである (Issue #869)。

### 完了確認の重複問い合わせ

元: worker-monitoring Step 2（受領記録がある問いを再送しない）

同一 task へ繰り返し再送した誤検知（2026-08-08、Issue #869）が、受領記録を見てから問い合わせる規則の根拠。

### 旧形式 spinner-age の誤検知を前サイクル差分で抑止する理由

元: worker-monitoring Step 4 (d)

**旧形式 spinner-age の false positive 抑止 (Issue #698)**: Claude Code は完了ターンの所要時間サマリ (`✻ Cooked for 31m 40s` 等) を live な旧形式 spinner と**同一の shape** で描画する。これが idle worker のスクロールバックに残ると、毎サイクル「5 分以上回る stuck spinner」として再マッチし false positive alert を繰り返していた。単一フレームでは凍結サマリと本物の stuck spinner を内容・位置だけで区別できないため、helper は「live spinner の `for Xm Ys` counter は毎サイクル進むが、凍結サマリは byte 一致で不変」という不変条件で差分を取る。`--spinner-state-file` を worker ごとに渡すと、前サイクルと同一の旧形式 spinner 行は凍結サマリとみなして検知を抑止する。初回観測は必ず発火 (差分対象が無い) し、完全凍結した live pane は hash ベースの STALL 経路 (`tools/inspect_pane_state.py`) が拾うので signal は失われない。state file 欠落 / 破損時は「前サイクル無し」扱いで再発火する安全側フォールバック (本物の stuck spinner を state file の不備で黙殺しない)。

### 自己申告と inspect の併用 / secretary 側 inspect cadence

元: worker-monitoring Step 4 (g)(h)

**(g) worker 自己申告 (Step 2) と inspect (Step 4) の併用設計**
両チャネルが同じ anomaly を通知しても de-dup ((e) の step 2) が 30 秒窓で合算するので、窓口は重複通知を受け取らない。self-report は先に届けば inspect を抑制、inspect は worker が通知を忘れていれば self-report を補完する。両方独立稼働で OK。

**(h) 設計メモ — secretary 側 inspect cadence は別 Issue (Issue #492 gap 4)**
Issue #492 gap 4「secretary 自身が active inspect cadence を持つべきか」は **本 PR スコープ外として別 Issue に切り出す判断**。理由: dispatcher の Step 4 を全 visible 行 scan + spinner-age に強化する (本 PR の gap 1–3) 方が変更が小さく、観測 case (529 + 9m spin) は dispatcher 側の検出強化だけで 5 分時点の ERROR 通知に乗る。secretary に二重の inspect ループを足すのは監視層の二重化で、まず dispatcher 強化の効果を観測してから要否を判断するのが妥当。secretary cadence / `secretary-monitor` skill が必要と判明したら別 Issue で扱う。

### stall 検出の動機

元: worker-monitoring Step 5 (a)

**(a) 動機**
Step 4 の inspect_pane 単独では、ワーカーが「Secretary に判断仰ぎを送って人間応答を待っている」状態と「stuck (異常停止)」を区別できない。判断仰ぎ中は worker pane の Claude session は継続中で画面 idle、APPROVAL_BLOCKED の regex にも該当しない。誤って STALL_SUSPECTED を発火すると、判断待ちワーカーに対してサイクル毎に窓口 escalation を投げ続ける。Issue #287 で実インシデント発覚 (session #12 / `worker-issue-283-delegate-payload`)。

### screen-change 判定を content hash にした経緯

元: worker-monitoring Step 5 (b)

**screen-change 判定は content hash で行う (Issue #680)**: 旧実装は `(target_line_text, 最後の非空行)` + cursor 位置の単点比較で idle/active を判定していたが、Claude Code の TUI はツール実行中も末尾 footer と cursor を静的に保つため、scrollback (Read/Edit/Bash 出力・thinking spinner) が動いていても単点比較では不変と観測され `idle_streak` が機械的に加算されて STALL を誤発火した (`worker-runtime-129-observed-session-binding` 等で 1 セッション 2 件連続、`knowledge/raw/2026-07-04-delegation-stall-detection-static-footer-false-positive.md`)。**全可視行を正規化してハッシュ化**し、実 scrollback の変化だけを active とみなす。ハッシュの算出・spinner 正規化・active-spinner 判定は helper `tools/inspect_pane_state.py` に codify 済みで、prose は helper 出力を state に反映するだけ (prose 手製ハッシュは決定性が弱く禁止、Major 対応)。

### PR-pending-merge の lookback 延長の設計

元: worker-monitoring Step 5 (b-2) 設計メモ

**設計メモ**:
- `pr_opened` / `pr_merged` は `docs/journal-events.md` で Writer = secretary、`task` payload field は task_id (bare) と定義済みなので、新規 event を導入せず既存 ledger だけで判定できる (Issue #304 提案 option 1)
- `STALL_PR_MERGE_LOOKBACK_MIN = 60` の根拠: PR レビュー / merge 承認の人間応答時間スケール。短すぎると (15 分) `worker_completed` が window から外れて誤発火、長すぎると stuck が見逃される。Issue #304 / session #12 観測の中間値
- PR-pending-merge を別 event ではなく lookback の延長で扱う理由: (c)(1) の補助シグナル軸 (`worker_completed` を含む 5 種) はそのまま再利用でき、acked 経路に乗せれば既存の de-dup / 通知抑制と整合する。新カテゴリ追加は最小差分
- merge 後 `pr_merged` event が記録されると次サイクルで sub-state が解け、lookback は 15 分に戻る。merge 後 `worker_completed` の `ts` が 15 分窓から外れた状態で worker pane がまだ open していれば通常 stall として扱われる (運用上は merge と同時に CLOSE_PANE が走るので実質的にこの経路には乗らない)
- `T_pr_opened` 自体の経過時間は判定に使わない (PR open から 60 分以内/超過の評価は不要)。代わりに (c)(1) の lookback を 60 分に拡張することで、`worker_completed` 等の最新 worker→secretary event が 60 分以内であれば acked、超えれば timeout という同一フォーマットの判定を維持する

### poll_events を stall 判定に使わない理由と将来案

元: worker-monitoring Step 5 (c)(2)

2. **poll_events (forward-compat、現状は補助のみ)**: 現状 `poll_events` は pane lifecycle event のみを流し、`send_message` は流れない (`.claude/skills/org-delegate/references/renga-error-codes.md` の type 表参照)。さらに `since` は時刻ではなく前サイクルから受け取る **opaque cursor** で、初回省略時は「今以降」セマンティクス (過去履歴は返らない) なので、本判定で「過去 15 分」をピンポイント検索する用途には今は使えない。events scan を **authoritative source** とする。将来 transport backend が `send_message` event を `poll_events` に流すようになれば、Step 1 で既に保持している `.state/dispatcher-event-cursor.txt` の cursor 経由で受信した worker→secretary の送信を `(worker, kind=stall_acked)` ledger に変換するルートを追加する想定 (本 PR ではプレースホルダとして記述するに留める)。

### STALL 判定の定数・設計理由・想定シナリオ

元: worker-monitoring Step 5 (f) 設計メモ

- **`STALL_SECRETARY_LOOKBACK_MIN = 15` の根拠**: Secretary が人間に判断を仰いでから応答を返すまで 5–10 分のオーダーが典型で、その間ワーカーは idle のまま待機する。15 分 window で「直近やり取りあり」を担保すれば、人間応答待ちの誤発火を実用上排除できる。短くすると判断待ちワーカーが timeout 経路に落ちて誤発火、長くすると完了後ペインの reactivation 痕跡を拾い続けて stuck が見逃される。中間値の 15 分が現状のスイートスポット
- **`STALL_PR_MERGE_LOOKBACK_MIN = 60` の根拠 (Issue #304)**: PR open 後の merge 承認は user の手動操作で 15–60 分かかるのが典型。worker は完了報告済みで idle のまま正しく待機している (= stuck ではない) が、15 分 lookback では `worker_completed` が window から外れて誤 STALL 発火する (session #12 で実測)。`pr_opened` 済 / `pr_merged` 未の sub-state を event ledger だけで判定し、その期間だけ lookback を 60 分に拡張する。merge 後は `pr_merged` が記録されて即座に通常 sub-state に戻る
- **60 分超過時の挙動 (Issue #304 long-tail)**: PR が 60 分以上 open のまま (週末越え / レビュー長期化) で `worker_completed` が window から外れると timeout 経路で再び STALL_SUSPECTED が発火する。これは仕様上「60 分を越えたら sticky な PR-pending-merge は人間判断対象として再通知する」設計で、Issue #304 の指定どおり。30 秒 de-dup のため 3 分サイクルごとに再通知される点はノイズだが、`org-pull-request` SKILL の close condition (24–48h レビュー idle で人間判断、参照: [`.claude/skills/org-pull-request/SKILL.md`](../../.claude/skills/org-pull-request/SKILL.md)) と組み合わせて運用判断する。長期 PR を完全 silence したい場合は将来 Issue で「`pr_opened` 済 task は STALL を一切上げない」へ変更する選択肢があるが、本 PR では「60 分まで猶予」の lookback 延長に留める (Issue 仕様準拠)
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
- (`ci_completed` incomplete の扱いの実例) 実例: runtime PR #126 / #127 で 2 連続観測 (merge / pane 自動クローズが進まない事象)。恒久修正 (`tools/pr_watch.py` 側の取得リトライ / 分類改善) は別 Issue で追跡する

### proxy 経路を fallback へ格下げした経緯

元: worker-monitoring Step 5.1 (a-3)

**proxy 経路 ((a)〜(f)) は (a-3) Fallback に格下げ**: Issue #297 時点では (a)(2) の唯一のカバーだったが、#301 で (a-2) deterministic 化が完了したことで、proxy 経路は次のケースの fallback としてのみ意味を持つ:
- 旧 Secretary 実装で書かれた entry (`user_replied_at` が None のまま) を後方互換でカバー
- Secretary が `mark-user-replied` を呼び忘れた運用ミスの保険
- register 自体が読めない / corrupted な状況の degraded mode

proxy 経路の最終削除は (a-2) の安定運用が確認できた段階で別 Issue で扱う。

### relay gap 検出の動機と proxy 経路の歴史的スコープ

元: worker-monitoring Step 5.1 (a)

**(a) 動機**
Step 5 は worker 側 (worker→secretary 痕跡が **ある** ので stall 抑制) を見て補助シグナル化したが、逆方向 (secretary→user / secretary→worker の中継) には盲点がある。具体的なインシデントパターン:
1. worker が "判断仰ぎます" を secretary に送信 → secretary は受領 (`worker_escalation` が journal に append) → secretary が **人間に上げ忘れ** → worker idle、Step 5 の補助シグナルは「ヒット」扱いで suppress、しかし user は何も知らない
2. user が secretary に答えた → secretary が worker に **転送し忘れ** → worker idle、同じ穴 (worker 起点 ledger は古いままなので Step 5 はやはり suppress)

どちらも Step 5 (worker 側監視) と Step 4 (worker pane 画面監視) では検知できない。secretary 側の outbound (secretary→user / secretary→worker) を観測する独立チャネルが必要。Issue #287 (PR #295) の sibling、両側監視で完成。Issue #292。

**proxy 経路の歴史的スコープ (旧 PR #298)**: 以下 (b)〜(f) は PR #298 当時の proxy-only 実装を記述しており、(1) 「secretary が人間に上げ忘れ」のみを対象としていた。(2) 「user 回答を worker に転送し忘れ」は当時 journal に secretary→worker outbound の ledger が無く検知できなかった。Issue #297 (PR #302) で (a-0) primary lookup により (1) は register 経由で deterministic 化、Issue #301 で (a-2) primary lookup により (2) も `user_replied_at` marker 経由で deterministic 化済み。proxy 経路 (b)〜(f) は legacy entry / Secretary が CLI 呼び忘れ / register 不通の degraded mode の (a-3) Fallback として残置されている。

### user pane への直接送信の将来案

元: worker-monitoring Step 5.1 (e)(3)

3. **`list_peers` で `client_kind` ベースに user pane を identify して直接送る案** (将来): `list_peers` には `client_kind` が含まれる (`docs/operations/renga-pane-conventions.md` 参照) が、user (人間) のターミナル pane は MCP peer として register していないのが通常で、現状の API 表面では direct send は不可。Issue 化候補 (本 PR では実装しない)

### register 化の着地と relay gap / Step 5 stall の関係

元: worker-monitoring Step 5.1 (g)(h) 設計メモ

**(g) 設計メモ — register 化は (a-0) で着地済み (Issue #297)**
PR #298 で TODO 化した「`.state/pending_decisions.json` 相当の register」は Issue #297 で実装済み。詳細は本セクション (a-0) "Primary check: pending-decisions register lookup" を参照。本セクション (a)〜(f) の proxy 経路は fallback として残置されているが、primary は (a-0) の register lookup に切り替わっている。proxy 経路の最終削除は別 Issue (register lookup の安定運用が確認できた段階) で扱う。

**(h) 設計メモ — relay gap と Step 5 stall の関係**
Step 5 の stall 検出は worker→secretary 痕跡があれば「acked」として STALL_SUSPECTED を抑制する。relay gap 検出は **その抑制された acked 集合** にこそ存在する。即ち:
- Step 5 で `acked` 経路に入った worker = relay gap 検出の入力候補
- Step 5 で `timeout` 経路 (STALL_SUSPECTED 通知済み) に入った worker = worker 側痕跡が無いので relay gap の (b)(1) が成立せず candidate にならない (= 通常 stall として扱う、単純 stuck)
- 両者は **disjoint な通知** になるため、windowed de-dup で merge する必要はない (kind が異なる)
- Step 5 の `worker-idle-state.json` は relay gap 評価でも参照される (idle streak ≥ 3 サイクルが (b)(4) の条件)。新規 state file は (d) の secretary pane snapshot のみ追加で、Step 5 の state とは独立

### pane_output_without_peer_msg 検出の動機

元: worker-monitoring Step 5.2 (a)

**(a) 動機**
2026-05-09 の renga-ime-paste-routing タスクで実発生したインシデントパターン: worker は窓口の問い合わせに対しペイン上に長文回答を展開したが `send_message(to_id="secretary", ...)` を呼び忘れたため、secretary 側は `inspect_pane` で気づくまで待機し続けた。worker / secretary 間の peer message が SoT のため、これは **silent dead-lock** (どちらの pane も live、Step 4 / Step 5 / Step 5.1 のどの既存検知器にも該当しない) になる:

- **Step 4 (画面 anomaly: APPROVAL_BLOCKED / ERROR)**: 規定の承認プロンプト / エラー banner が出ていなければ match しない。自由記述の長文回答は anchored regex (b) に乗らない
- **Step 5 (STALL_SUSPECTED)**: idle_streak ≥ 3 サイクル (= ~9 分) が必要だが、worker がペインに出力を始めれば `idle_streak_cycles = 0` reset で stall 候補から外れる。output 自体が STALL を抑制してしまう
- **Step 5.1 (SECRETARY_RELAY_GAP_SUSPECTED)**: secretary 側 outbound 不在 (secretary→user / secretary→worker) を観測する逆方向の検知器。worker outbound 不発はカバー対象外

worker side outbound (= peer message 不発) を独立 channel で観測する必要がある。Issue #287 (Step 5) と Issue #292 (Step 5.1) で確立した `anomaly_observed` / `notify_sent` ledger スキーマと、Step 5 (b) の `worker-idle-state.json` の拡張のみで実装でき、新規 event / 新規 state file は不要。

### task_id の SQL 文字列補間の安全性

元: worker-monitoring Step 5.2 (c)

**task_id の SQL 文字列補間について** (Codex round 1 Minor): claude-org-ja の task_id は slug 規約 (`[a-z0-9-]+`、CLAUDE.local.md で task_id 値が決まる時点で人間 / runtime helper の制約で実質遵守、引用符 / SQL metachar は事実上含まれない) を満たすため、上記の string interpolation は安全。helper script 化 (sqlite3 `.parameter` 渡し or Python `tools/state_db/queries.py` 拡張) は将来課題で、現状は dispatcher Claude が SQLite one-liner を直接実行する。slug 以外の値が混入する経路 (例: 旧 worker_id 直書き) を将来導入する場合は、helper script 経由必須に切り替える。

### relay scan を --cycle 1 本に畳んだ理由・audit の順序・報告条件

元: worker-monitoring Step 5.25

**なぜ 2 本を 1 本に畳んだか (Issue #955 / #971)**: 旧手順はサイクル冒頭に `--audit` と `--list` の 2 本を要求していたが、実測の監視サイクルが叩く shell コマンドはおおむね 1 回で、しかも 2 本は自己参照になっていた — **`--audit` は読み取り専用で scan を起こさず、`--list` は「audit が stale と言ったとき」にしか叩かれない**。canonical な `/loop 3m` 正文 ([`.hooks/check-loop-directive.sh`](../../.hooks/check-loop-directive.sh) で固定) で武装した 2026-09-03 でも scan は 44.5 分止まり、未配送 2 件が滞留した。prose の遵守問題ではなく構造の問題なので、**叩くべきコマンド数そのものを 1 本に減らす**ことで解いてある (サイクルを回した = scan が走った)。`--audit` / `--list` 単体も後方互換で残るが、runbook の正路は `--cycle` である。

   **audit を先に評価する順序が本質**: scan は heartbeat を書くので、逆順にすると audit は永久に `fresh` を返し検出力が消える。`--cycle` はこの順序をツール側に固定してある (prose の順序遵守に依存しない)。

   **なぜ「heartbeat が古い」だけでは報告しないか**: 監視ループは Step 7 で*意図的に*停止する (worker ペイン無し + relay 空)。夜間を挟めば翌セッション最初の audit は必ず数時間前の heartbeat を見るので、それを異常として上げると org 起動のたびに偽 `RELAY_SCAN_STALE` が出て、**読み手が本 alert を無視するように学習してしまう**。報告条件を「trace 欠落 **かつ** backlog 実在」に絞ると、それは「配送すべきものが溜まっているのに何も掃けていない」= 障害そのものを指す。backlog が空のまま relay が壊れていても実害は無く、終端イベントが 1 件でも入った次のサイクルで即 fire する。2026-08-19 の実障害は 132 件超が滞留していたため、この絞り込みで検出力は落ちない。**backlog が空でない停止**は Step 7 の `--mark-halted` 記帳 (`halted: true`) が抑止し、その抑止は**それを使った audit 自身が消す**ので 1 回きりである (scan が続かなくても消える)。

### relay の沈黙を検出できなかった 20 日間

元: `.dispatcher/CLAUDE.md`「ワーカーペイン監視」のエントリポイント要約（Refs #941 #955 #971）

2026-07-30 から 2026-08-19 までの 20 日間、runbook は relay のコマンドを `python` と綴っていた（この環境には `python3` しか無い）。relay は毎サイクル `command not found` で空振りし、終端イベント 134 件が滞留したまま誰も気付けなかった。配送対象が無かったサイクルと relay が走らなかったサイクルは、どちらも `event_deliveries` 台帳に何も残さない。このため scan が無条件に書く heartbeat を設け、それを見る audit をサイクル冒頭の必須コマンドにした。その後 `--audit` / `--list` の 2 本立てが自己参照になって scan が止まった件と、`--cycle` 1 本に畳んだ理由は上の節にある。

### 監視ループ全般の設計メモと cadence 設定の根拠

元: worker-monitoring 末尾「設計メモ」「cadence 設定の根拠 (2026-05-02 検証)」

#### 設計メモ

- **サイクル冒頭で `spawn_gate.py audit` を叩く理由**: 2026-08-18 に 2 件、承認 Enter・登録確認・指示送信のいずれも未実行のまま「完了」と報告され、窓口が `inspect_pane` するまで誰も気付けなかった。当該ワーカーは peer message を 1 度も出していないので、Step 5.2 では構造的に拾えない
- **観測の原則を個別 patch ではなく判定より前に置いた理由 (Issue #869)**: 2026-08-08 の誤検知 3 種は発火した判定が別々 (pane 消失 / STALL / 完了確認) だったが、根は 1 つで「自分の観測手段が届いていない状態を、対象の異常と解釈した」だった。3 箇所を個別に直すと同じ誤りが**次に増える判定**で再発する。[観測の原則](#observation-principle) を監視ループの手前に置き、各判定がそこを参照する形にすると、新しい判定を足す人が「この観測面は何の証拠になるのか」を (P3) の表で先に決めることになる。表への行追加 (新しい観測面) と判定の追加は独立して行える
- **精度向上と権限拡大を分けて書く理由 (Issue #869)**: 判定を厳しくする変更は「誤検知が減る」と同時に「異常ではないと切り分けられた」という判断を dispatcher に与えるため、放っておくと「異常ではないので自分で直してよい」へ滑りやすい。(P5) で**申告条件の変更は行動の変更を含まない**ことを否定形で固定してある。実際 2026-08-08 の pane 消失は人間が別タブで開いていた生きたセッションで、dispatcher が観測結果だけを報告して復旧に動かなかったことが事故を防いだ。精度が上がったからといってこの抑制を緩めてはならない
- **なぜ `poll_events` を `timeout_ms=5000` で回すか**: 3 分のサイクル間隔の中で 5 秒分は long-poll する。5 秒経過で return して残りの時間は check_messages + list_panes + inspect_pane で補完。これにより pane 終了検知の平均遅延が cadence 非依存に 2.5 秒程度になる
- **cursor 管理**: `.state/dispatcher-event-cursor.txt` に前回 `next_since` を **`session_key` と揃えて** 保存する (単一行 JSON。schema は [`docs/contracts/state-schema-contract.md`](../../docs/contracts/state-schema-contract.md) §1.7)。初回 (cursor 無し) は `since` 省略で「今以降」セマンティクス。crash recovery 時は cursor 消失 = 過去 5 秒分のイベントを取りこぼす可能性があるが、list_panes 突き合わせで回復可能
- **なぜ cursor を session に束縛するか**: cursor の**値**は daemon session 内のカウンタで restart を跨いで復元されないのに、cursor **ファイル**は跨いで残る。この非対称を放置すると、restart 後は「前 session の大きい連番」で poll し続けて全ての `pane_exited` が空振りする — しかも**縮退の向きが安全側**（ゲートは回り続け、通知が出ないだけ）なので**症状が正常に見える**。2026-08-09 の実走ではこれが最初の worker close で顕在化し、終了検知が無言で死んだ。束縛 (Step 1) だけでは同じ静かさが残る（cursor を捨てたことに誰も気付かない）ので、**破棄を必ず窓口へ 1 行報告する fail loud** をセットにしてある。cursor を捨てた区間は原理的に回収不能なので、報告本文でフォールバック経路 (契約 T-§2.1 step (3) の `list_peers` 消失 + 窓口 reconcile) まで明示する
- **events と list_panes の二重カバー**: events は best-effort (EventsDropped あり得る) なので、`list_panes` による突き合わせを保険として併用
- **inspect を独立した観測チャネルにする理由**: ワーカーが承認待ちで止まった時、worker 自己申告 (peer message) だけに頼ると worker が通知を送る前に停止してしまう。inspect はディスパッチャー側から能動的に観測するので、worker 側の通知忘れ/遅延を補完する。自己申告と inspect は「同じ事象を 2 チャネルで観測できれば確度が上がる」という冗長性設計
- **anchored regex の意図**: 本文中に "Allow this tool use" が偶然出てもプロンプト自体の行フォーマット (末尾に `(y/n)`) まで揃うことは稀。末尾 non-empty 行に絞ることで誤検出をさらに減らす
- **エラーは message ではなく code で分岐する**: MCP tool result テキストの `[<code>] <msg>` 形式で返る。message 文字列は human-facing で将来変更あり得るので、`[pane_not_found]` / `[shutting_down]` 等の code で case 分岐する。詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`

#### cadence 設定の根拠 (2026-05-02 検証)

`/loop 3m` を採用する根拠:

- journal.jsonl 実測 (188 events / 10.83 日, event rate 0.012/min, active session 内 gap median 224s)
- pane lifecycle (`pane_exited` / `events_dropped`) は `poll_events(timeout_ms=5000)` の long-poll 経路で **cadence 非依存に ~2.5 s 検知** されるため、cadence 延長で reactivity は劣化しない
- inspect / check_messages の最悪遅延 60 s → 180 s は anomaly 解消の人間判断時間スケール (数分〜十数分) より十分小さい
- 1m → 3m で Foreman Claude のトークン消費を **約 1/3 に圧縮** (895 → 298 サイクル相当 / 10.83 日)
- ja#6 (1m → 20s 提案) は既に CLOSED、本検証結果は close コメントと整合 (reopen 不要)

### 全ペイン index の現況（2026-08-10 時点の記録）

元: worker-monitoring Step 3 (3-a-5)（現 background-tab-worker.md）

> **現況 (2026-08-10 時点の記録された観測 — 恒久の事実ではないので、サイクルごとに照会し直す)**: 上表の**条件 1 と条件 2 が成立しない** (契約節は PROPOSED、`cross_tab_list_first_drive` は照会面に名前が無く undetermined)。**したがって本節の枝は今日 1 度も発火せず、背景 worker の生存判定は従来どおり 3 入力だけで回る。** 本節が定めるのは「契約が批准され first-drive が記録されたあとに何をどう読むか」であって、その前の発動許可ではない。**解除の手順は下記に書いてある** — 「いつか誰かが批准する」ではなく、順に実行できる形になっている。

## pane-close.md から移した理由と経緯

### 完了報告ゲートを置く理由

元: pane-close 1（完了報告ゲート「理由」）

ワーカーのレポートチャネルは secretary 直送である。dispatcher のメッセージキュー（`check_messages` の戻り）に完了報告が無いことは、「システム上に存在しない」ことを意味しない。secretary 側に既に届いていることがしばしばあり、確認を怠ると「完了報告未着」と誤った結論を retro に残してしまう（2026-05-03 の委譲スモークで実際に起きた）。

CLI を 1 attempt あたり 1 回起動する単発判定にしたのは Issue #285 の判断による。

### 受領記録で初回送信を省く理由（経緯）

元: pane-close 1-0（受領記録の確認）

- 受領記録（`completion_reported_at`）がある task へ「完了報告は届いていますか？」を送ると、窓口の受信箱を同じ 1 事実で埋める。同一 task へ繰り返し再送した誤検知（2026-08-08、Issue #869）が根拠。
- `run_status == 'completed'` を記録の有無を問わず通すのは、merge 済み task にも毎回問い合わせが飛んでいたため（2026-09-23 に 4 task で発生）。

### close 成功時の renga 側の挙動

元: pane-close 3（ペインを閉じる）

close 成功時、renga は `Event::PaneExited` を（exit_event_emitted ガード経由で）正確に 1 回 emit する。

### curate 閾値チェックの starvation（既知の限界）

元: pane-close 5（curate 閾値チェック）

worker close が発生しない期間（手動 raw 追加のみ / skill-candidate のみ増加等）は閾値チェックが走らない。補助トリガーは Issue #501（org-retro 末尾）/ Issue #502（org-start バックストップ）としてバックログ化済み。5-2 の coalesce で拾われなかった増分も、worker close が長期間発生しないケースではこの補助トリガーが保険になる。

### live curator + inflight 無しに到達する経路

元: pane-close 5-2（single-flight 確認）

この組み合わせは設計上到達しうる: 5-3 の spawn は成功したが inflight 書き込み前に dispatcher が `/clear` / crash した経路と、worker-monitoring Step 5.3 の close 判定表で close を撃たずに inflight を削除する行（行 3 / 行 4 / 行 6）に落ちたのち、その「消失確定」が実際には外れていた経路。inflight を作らずに coalesce だけすると、追跡が無いので監視ループ Step 5.3 も回収せず、復帰は `/dispatcher-resume` Step 5 の inflight 再生成（= 次の handover / resume）を待つしかない。

### curator を model="sonnet" で起動する理由

元: pane-close 5-3（curator ペインの spawn）

auto モードの safety classifier はセッションモデルと独立した専用モデルで動作し、承認判定はセッションモデルに依存しない（公式: https://www.anthropic.com/engineering/claude-code-auto-mode）。キュレーターの知見整理ワークロードは軽量・機械的側に分類できるため Sonnet で十分（ワーカーのモデル方針とは独立の判断）。

### `[name_in_use]` の stale-binding 分岐は close 判定表の行ではない

元: pane-close 5-3（`[name_in_use]` の切り分け）

close 判定表（worker-monitoring Step 5.3）は「spawn 済み curator を追跡している `curate-inflight.json` をどう畳むか」の規則で、stale-binding 分岐には curator ペインも inflight もまだ存在しない。表の対象外の別状況なので、表の disposition 規則と競合しない。

### 旧 `REPO_FLAGS` 手順を使わない理由

元: pane-close 6-1（scan の実行）

フラグ文字列が複数引数になるかは呼び出し元シェルの単語分割次第で、ペインの login shell である zsh は既定 `SH_WORD_SPLIT` off のため未クォートの `$REPO_FLAGS` を分割せず、argparse には 1 引数として届いて worker クローズ時の scan が毎回 exit 2 で失敗していた（bash では 4 引数に分割されるので bash では気付けない。Issue #829）。zsh 専用の `${=REPO_FLAGS}` も対症療法なので使わない。1 コマンド形はシェルを一切経由しないので zsh / bash / Windows `py -3` で同一に動く。exit 2 を必ず窓口へ通知する規則も同じ Issue で入った。

### 同一候補の再送抑止（経緯）

元: pane-close 6-2（exit code 分岐）

worker クローズのたびに同じ候補を再送すると、窓口の受信箱が同じ提案で埋まる（2026-09-23 に同一 3 件を 7 回再送）。

### `work_discovery_scanned` の台帳定義

元: pane-close 6-3（監査ログ）

`work_discovery_scanned` の `docs/journal-events.md` への定義追記は、手順を書いた当時のスコープ外とした（設計 §10 / §7.1）。`tools/journal_append.sh` は event 名を free-form 文字列として受け取り key=value をそのまま記帳する（台帳を参照・検証しない）ので、手順は台帳定義の有無に依存しない。
