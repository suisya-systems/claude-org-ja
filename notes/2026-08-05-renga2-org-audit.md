# renga 2.0 (マルチタブ化) への org 追随監査報告 — Issue #823 Phase A

> **Erratum (2026-08-07) — 読む前に必ずここを読むこと。以下の 2 点で本文の前提が覆っている。監査本文は当時 (2026-08-05) の観測記録として保存する目的で一切書き換えていないので、本文の記述と本 erratum が食い違う場合は本 erratum が優先する。**
>
> 1. **`close_pane` / `set_pane_identity` は caller-tab 化の対象外ではなくなった。** 本文 §2.2 と §3.4 は「両ツールは #288 の caller-tab 化の例外で、`focused`・名前は *ユーザーが表示中のタブ* 基準で解決し続ける」ことを前提に、org 側の対応案として**数値 pane id 必須化**を導いている。renga #296 でこの 2 ツールも他の 7 ツールと同じく **caller (呼び出し元ペイン) のタブ基準で `focused`・名前を解決する**ようになった (出典: suisya-systems/renga `CHANGELOG.md:127-128` の #296 エントリ「`close_pane` / `set_pane_identity` now resolve `focused` and names against the *caller's* tab.」)。さらにこの挙動は先行 3 トークンとは**独立した capability `caller_scope_close_identity`** で広告・ゲートされる (出典: suisya-systems/renga `src/ipc/mod.rs:123-128` の `SERVER_CAPABILITIES` に `CAP_CALLER_SCOPE` / `CAP_CROSS_TAB_PEERS` / `CAP_SPAWN_TAB` と並んで `CAP_CALLER_SCOPE_CLOSE_IDENTITY` が入る)。独立トークンである理由は、#290 世代のサーバーが先行 3 トークンを広告しながら未知の `from_pane` を落とし、**可視タブのペインを不可逆に閉じる**ためである (出典: suisya-systems/renga `docs/api-surface-v1.0.md:576-582`)。
> 2. **renga 2.0.0 はリリース済みである。** 本文 §1-1 / §2.1 / §2.5 は renga 2.0 を「未リリース (現行リリース 1.4.0)」、#290 を「in-flight」と書いているが、**2.0.0 は 2026-08-07 にリリースされた** (出典: suisya-systems/renga `CHANGELOG.md:10` の見出し `## [2.0.0] — 2026-08-07`)。`caller_scope` / `cross_tab_peers` / `spawn_tab` / `caller_scope_close_identity` の 4 トークンはいずれもこの単一リリース 2.0.0 に含まれる (出典: 同 `CHANGELOG.md:10` 以降の 2.0.0 節および `src/ipc/mod.rs:123-128`)。
>
> **したがって §3.4 の「close_pane / set_pane_identity の数値 id 必須化 (S)」という旧助言をそのまま再採用してはならない。** 前提となる「両ツールだけがユーザー可視タブ基準」という renga 側の性質が消えており、現行で org が満たすべき要件は「`caller_scope_close_identity` を含む capability を probe し、非広告時は fail closed にする」ことである。**この論点の現行の正本は [`docs/contracts/backend-interface-contract.md`](../docs/contracts/backend-interface-contract.md) の当該 amendment であり、本監査ノートではない。**
>
> **ただし「数値 pane id で宛先を指定する」という所作そのものは失効していない** — 失効したのは旧助言の**根拠**の方である。契約 T-§4.2 "Fail-safe consequence for Group B (SHOULD)" は、`caller_scope_close_identity` を確立できていない間は `close_pane` / `set_pane_identity` を数値 pane id で宛先指定することを **SHOULD** として推奨している。根拠は「この 2 ツールだけが caller-tab 化の対象外」という (撤回された) 性質ではなく、**pre-capability クライアントが相対セレクタをユーザー可視タブ基準で解決してしまう**ことである。強度が MUST ではなく SHOULD である理由も含め、正本は契約側にある。

- 日付: 2026-08-05
- タスク: ja-823-renga2-audit / ブランチ `fix/ja-823-renga2-audit` / Refs #823
- 種別: read-only 監査 (実装・修正は一切行っていない。対応は Phase B で別途)
- renga 一次情報: suisya-systems/renga **origin/main (merge commit `7609316`)** を `git show` 経由で参照。ローカルの renga checkout は origin/main から **21 コミット遅れ** (CHANGELOG は 1.4.0 + Unreleased #285 まで) のため直接は依拠していない
- 監査対象: org リポジトリ (claude-org-ja) 全域 (CLAUDE.md / .claude/skills / .dispatcher / .curator / docs / tools / scripts / registry / knowledge / .state / renga-layouts / notes / docker / .github / .hooks) + 容量会計トレースのため runtime パッケージ `claude_org_runtime` (インストール済み site-packages、リポジトリ外)
- Codex レビュー 1 round 済み (指摘 8 件: Blocker 2 / Major 2 / Minor 3 / Nit 1 — すべて本版に反映済み)

## 1. エグゼクティブサマリ

1. renga 2.0 (epic #287) で pane 操作の対象が「フォーカス中のタブ」から「呼び出し元 (caller) のタブ」基準へ変わり (#288)、peer messaging は数値 pane id 宛のみタブ横断配達・**名前宛は送信者タブ内限定のまま fail-loud 化** (#289)、org サイドバー (#291) がマージ済み。spawn のタブ指定配置 (#290) は in-flight。renga 2.0 自体は未リリース (現行リリース 1.4.0)。
2. org の全通信は `to_id="secretary"` 等の**安定名宛てに一元化**されている (本監査で延べ約 145 箇所)。役割ペインが別タブに置かれた瞬間、完了報告・ack・escalate・relay・retro gate の全系統が `pane_not_found` で不達になる。
3. dispatcher の**容量会計**は「list_panes スナップショット → runtime `runner.py`」の 1 本の連鎖 (延べ約 103 箇所が関与)。ただし count_active_workers / max_concurrent_workers は **broker 分岐限定** (runner.py:892-930。renga 面は choose_split 分岐 :940-954 で、registry/org-config.md:36-39 も renga では max_concurrent_workers 不参照と明記)。母集合が自タブに縮むと、renga 面は choose_split の current-tab geometry と監視母集合の縮小 (誤退役方向) で、broker 面は将来 broker で multi-tab 相当の配置が生じた場合に silent overspawn 方向で壊れる。
4. ただし現行運用は spawn-flow が「同一タブ内 spawn」を MUST としており、**全員が 1 タブに同居し続ける限り #288/#289 だけでは 1 本も壊れない** (むしろ focused→caller 化で堅牢化)。壊れるのは #290 で別タブ配置を始めた場合。**例外は org-suspend の broadcast 系 (SUSPEND/SHUTDOWN): list_peers 全タブ化 (#289) により、同一タブ運用のままでも別タブの無関係 agent を誤対象にしうる (§3.1 / §4.2b)。**
5. 契約 Surface 4.2 (SINGLE-TAB MUST / Q10) は「Multi-tab support … requires a contract amendment」と自ら予告しており、まさにその amendment の時が来た。根拠にしている renga#71 の制約自体が解消されるため、文言は根拠ごと書き換えが要る。
6. Phase B 最小セット (いずれも S): (a) 「名前で送受信し合うペイン群は同一タブ配置」規則としての Surface 4.2 amend、(b) renga-error-codes.md への `pane_not_found` 意味論変更 + `server_too_old` 追補、(c) バージョン前提 0.18.0 の一括 bump + capability probe 追加。
7. マルチタブ ops 配置 (worker の別タブ逃がし等) は #290 確定後の設計判断で、容量会計の実体が**別リポジトリ (claude-org-runtime)** にあるためクロスリポジトリ改修を伴う M〜L。
8. 追加発見: #291 サイドバーのトグルキー **Ctrl+B が tmux prefix Ctrl-b と物理キー衝突**する (5 箇所)。サイドバー有効時 (既定 coexist) は renga が Ctrl+B を消費して即 return するため**確定的な横取り** (src/app/keyboard_input.rs:318-334)。既知 6 影響点の外だが Phase B で注記追補を推奨。また同サイドバーは pane 領域から幅 26 桁を差し引くため**容量会計に直接効く** (§2.4 / §3.2)。

## 2. renga 側の新挙動 (一次情報)

### 2.1 版数状況と参照方法

- renga 2.0 は**未リリース**。npm 最新リリースは **1.4.0**。
- merged main (origin/main = `7609316`) に #288 / #289 / #291 が反映済み。#290 は in-flight (org の worker `worker-renga-290-spawn-tab-placement` が実装中)。
- ローカル checkout は 21 コミット遅れ (CHANGELOG 1.4.0 + Unreleased #285 まで) のため、一次情報は fetch 済み origin/main `7609316` の CHANGELOG / docs / src を `git show` 経由で参照した。
- org 側の前提は「renga 0.18.0+」で固定されており、0.18.0 → 1.x の間にも既に版数表記ドリフトがある (§7)。

### 2.2 #288 caller tab scope (merged)

- caller-tab 化された 7 ツールは **list_panes / spawn_pane / spawn_claude_pane / spawn_codex_pane / focus_pane / inspect_pane / send_keys** (一次仕様: origin/main docs/peer-messaging.md:79-83)。対象が「フォーカス中のタブ」→「**呼び出し元ペインのタブ**」へ変更。
- **例外: close_pane と set_pane_identity は caller-tab 化の対象外** (#288 以前から cross-tab)。ただし `focused` と名前は今も「**ユーザーが表示中のタブ**」基準で解決する。背景タブから `close_pane(target="focused")` するとユーザー操作中の pane を殺しうるため、一次仕様は「必ず数値 id を渡せ」と明記。名前指定の close_pane も、ユーザーが別タブ視聴中は pane_not_found (→「既に閉じた」誤判定) や同名ペイン誤 close を起こしうる (対応案は §3.4 の数値 id 必須化)。
- `list_panes` は caller のタブのペインのみ返す (人間がどのタブを見ているかに依存しなくなる。org にとってはこの点は改善)。
- capability `caller_scope`。旧 server に対しては `[server_too_old]` で **fail closed**。

### 2.3 #289 peer messaging のタブ横断化 (merged)

確定意味論 (トレースで確認した 5 点):

1. **数値 pane id 宛**の send_message はタブ横断で配達される。
2. **名前宛は従来どおり送信者のタブ内でのみ解決** (名前の一意性はタブ単位)。
3. 解決不能宛先は従来の **silent 偽成功 → `pane_not_found` エラー化** (fail-loud)。
4. `list_peers` は**全タブ列挙**になり、PeerInfo に display-only の `tab` / `tab_name` / `same_tab` (optional) が付く。
5. 旧 server へは `[server_too_old]` で fail closed。capability `cross_tab_peers`。

### 2.4 #291 org サイドバー (merged)

- 全タブ構成 (タブ一覧 + 各タブの pane/role) を表示するサイドバー。トグルキーは **Ctrl+B**、既定幅 26 桁。
- **org の容量会計に直接影響する**: `[ui] org_sidebar` は既定 coexist = 有効 (src/config.rs:53-59) で起動時から表示され (src/app/app_core.rs:207-211)、pane 領域から幅 26 桁 (狭い端末では 16) を差し引く (src/app/layout_geometry.rs:96-149)。org の choose_split は list_panes の rect をそのまま使うため、**同一タブ運用のままでも** split 候補数が減り SPLIT_CAPACITY_EXCEEDED の発生点が変わる (実効ワーカー容量の減少。§3.2 / §3.6)。
- ほかに (a) 「renga は単一画面タイリング」という org 内の説明群 4 箇所が陳腐化、(b) tmux prefix Ctrl-b との物理キー衝突 — サイドバー有効時は renga が Ctrl+B を確定的に横取りする (src/app/keyboard_input.rs:318-334。§3.6 / §4.9)。

### 2.5 #290 spawn のタブ配置指定 (in-flight)

- **Issue 本文由来の設計条件**: (a) tagged tab selector による spawn 先タブ指定、(b) capability `CAP_SPAWN_TAB` による能力ネゴシエーション、(c) 背景タブ生成 (フォーカスを奪わない spawn)。
- **Codex レビューでの確定分と未確定分の切り分けは本監査時点で不能** (PR が未マージのため)。本報告で #290 に依存する対応案はすべて「#290 最終仕様の確認待ち」の条件付きとして記す。
- .state/workers/worker-renga-290-spawn-tab-placement.md:8 が org 側の追跡記録。

## 3. 影響点別監査

### 3.1 影響点(1): 名前宛先 (`to_id` 安定名) のタブ内限定

**何が変わるか**: #289 で名前解決は送信者タブ内限定のまま、解決不能が silent 成功から `pane_not_found` エラーに変わる。org の名前宛先経路は 7 系統: (a) worker→secretary 完了報告、(b) secretary→worker ack/追指示、(c) secretary⇄dispatcher (DELEGATE / retro gate ack / WORKER_COMPLETION_NOTED)、(d) dispatcher→secretary/worker/curator (escalate / nudge / 起動指示)、(e) curator→secretary/dispatcher、(f) worker→dispatcher (retro gate)、(g) CLI 発 push (pr_watch→secretary)。

**呼び出し連鎖 (コード追跡)**:

```
worker CLAUDE.md (gen_worker_brief.py:286 が tools/templates/worker_brief_{normal:109, self_edit:81}.md を展開)
  → send_message(to_id="secretary")  ※ pane_not_found 時 numeric id フォールバック文言あり (normal のみ)
tools/pr_watch.py:199 (_PEER_NOTIFY_TARGET="secretary")
  → tools/peer_notify.py:81 (ORG_TRANSPORT 分岐; 無設定→renga 経路)
  → tools/peer_notify.py:238-239 (renga mcp-peer へ JSON-RPC tools/call send_message(to_id, message))
  → 失敗時 pr_watch.py:261-288 が notify_failed 記帳 → relay 経路へフォールバック
tools/relay_scan.py:74 (DEFAULT_RECIPIENT="secretary") ← dispatcher の outbox relay
tools/dispatcher_retro_gate.py:306 (--secretary 既定 "secretary"; :150-155 で from_name=="secretary" 照合)
tools/gen_delegate_payload.py:590 (DELEGATE 本文「窓口ペイン名: secretary」) / :1842 (send_plan to_id="dispatcher")
```

**中核該当箇所** (全量は §4):

| 箇所 | 引用 (要旨) | 何が壊れるか |
|---|---|---|
| tools/templates/worker_brief_normal.md:109 | `${transport_send_message}(to_id="secretary", ...)` / pane_not_found 時は numeric pane id | 全 worker の完了報告。numeric フォールバックは DELEGATE 本文に secretary の pane id が入る場合のみ自己回復するが、現状その注入機構は無い |
| CLAUDE.md:104 | secretary は最初に worker 宛 ack を `to_id="worker-{task_id}"` で発行 | ack 不達 → worker が idle dead-lock (#289 の fail-loud 化で初めて送信側が気づける) |
| CLAUDE.md:108 | retro gate ack は必ず `to_id="dispatcher"` の direct send | retro gate が永遠に開かない |
| .dispatcher/references/spawn-flow.md:50-52 | SPLIT_CAPACITY_EXCEEDED を `to_id="secretary"` へ escalate | 容量超過の報告経路自体が名前依存 (影響点 2/6 と複合) |
| .dispatcher/references/worker-monitoring.md:442 / 629 / 671 / 756 / 766 / 829 | STALL_SUSPECTED / RELAY_GAP / nudge / relay 送出 | 監視ループの全出力が名前宛。relay (path B) が死ぬと pr_watch push 失敗時の最終保証が消える |
| .dispatcher/references/spawn-flow.md:135 | `list_peers` に worker-{task_id} が現れるまでリトライ | #289 で list_peers 全タブ化 → 「一覧に見える ≠ 名前で送れる」。別タブ同名 peer で偽陽性。`same_tab` 絞り込みが必要 |
| .claude/skills/org-suspend/SKILL.md:62-63 / :236 相当 | list_peers で列挙した「全ピア」へ SUSPEND、「全ワーカーピア」へ SHUTDOWN を broadcast | ★実害大: #289 の list_peers 全タブ化で宛先集合が別タブへ拡大し、数値 id 宛はタブ横断配達されるため**別タブの無関係 agent への誤 SUSPEND/SHUTDOWN** が同一タブ運用でも起こりうる。`same_tab` (PeerInfo 新フィールド) or org 帰属での絞り込みが必要。org-start/SKILL.md:313 相当の list_peers 登録待ちや dispatcher-resume の生存確認にも別タブ同名/同 role の偽陽性リスク |
| .curator/CLAUDE.md:50 | 「送信は必ず安定名を使うこと」(secretary / dispatcher) | CURATE_DONE 不達 → dispatcher の curator 待ちが解けず org サイクル停止 |
| .claude/skills/org-escalation/SKILL.md:78 | `to_id="worker-{task_id}"` で人間判断を転送 | 判断仰ぎ中 worker の永久待機 |
| docs/contracts/role-contract.md:61 | 「must use stable pane names …, not numeric from_ids」 | 安定名 MUST の契約規定そのものが同一タブ前提 |

**対応案**:

- **案 B (推奨・Phase B 最小)**: 「名前で送受信し合うペイン群は同一タブ配置 MUST」を契約に明文化 (影響点 3 の amend と同一作業)。現行 spawn-flow は既にこれを満たしており**新規実装ゼロ**。付随: (i) spawn-flow.md:76-77 の理由文を #289 意味論へ更新、(ii) spawn-flow.md:135 の readiness 判定に `same_tab=true` 条件追加、(iii) 既存 numeric-id フォールバック (worker_brief_normal.md:109 / org-start SKILL.md:99) を「タブ違い事故時の復旧手段」として明示、(iv) list_peers 全タブ化で宛先集合が拡大する broadcast 系 (org-suspend の SUSPEND/SHUTDOWN) と list_peers 依存の登録待ち・生存確認 (org-start:313 相当 / dispatcher-resume) に `same_tab` (or org 帰属) フィルタを追加。
- **案 A (数値 pane id 管理への全面切替)**: state.db に前例あり (.dispatcher/CLAUDE.md:160 の dispatcher_pane_id) だが、org 自身の「安定名 MUST / numeric id 禁止」規範 (.dispatcher/CLAUDE.md:115-124, .curator/CLAUDE.md:57) の全面反転になり、id 失効管理という新しい失敗モードを持ち込む。#289 で数値 id はタブ横断配達が保証されたため、影響点 6 でタブ横断リンクが実需化した場合に**限定適用** (worker↔secretary 間のみ) するのが妥当。

**工数感**: 案 B = **S** (規則明文化 + 文言更新のみ)。案 A = **M〜L** (テンプレート/CLI/スキル prose 十数箇所 + id 鮮度管理の新設計)。

### 3.2 影響点(2): dispatcher 容量会計 (list_panes 依存の空き枠判定)

**呼び出し連鎖 (コード追跡)**:

```
spawn-flow 3-1a (.dispatcher/references/spawn-flow.md:26)
  mcp__renga-peers__list_panes → rect スナップショット取得 (以後の全カウント・target 選出の母集合)
→ .dispatcher/CLAUDE.md:49,73  delegate-plan --panes-json (structuredContent.panes を無加工で渡す)
→ claude_org_runtime/dispatcher/runner.py  build_plan()   ※ ja リポジトリ外 (PyPI claude-org-runtime)
   ├ renga 面: choose_split() (:536。MIN_PANE_WIDTH=20 / MIN_PANE_HEIGHT=5 = :78、SECRETARY_MIN 120x30)
   │    候補ゼロ → :941-951 status=split_capacity_exceeded (文面は terminal size / rect 語彙のみ)
   └ broker 面: count_active_workers() (:264 = スナップショット中 role=="worker" の数)
        :897 active 算出 → :905-906 free_slots / exceeded 判定
        (max_concurrent_workers 既定 8 = :175、運用値の導線は registry/org-config.md:34)
        → :909-929 split_capacity_exceeded + to_id="secretary" escalate 文面生成
→ dispatcher が exit 2 で escalate 実行 (.dispatcher/CLAUDE.md:80 / spawn-flow.md:52)
※ CLI (runner.py:1232) は live_worker_names (registry 照合フック :251) を未配線。
   実効的な worker 数の定義は「list_panes スナップショットに写る role==worker ペイン数」のみ。
副次消費者: worker-monitoring の pane 消失判定 (worker-monitoring.md:85,90)、curator single-flight
(.dispatcher/CLAUDE.md:201)、work_discovery の --free-panes (work_discovery_scan.py:80-81 / pane-close.md:324)
```

**壊れ方** (前提: 同一タブ MUST 継続なら #288 単独では壊れない。以下は #290 等で別タブ配置が生じた場合。ただし #291 サイドバー行のみ**同一タブ運用でも発生**):

| 箇所 | 何が壊れるか |
|---|---|
| runner.py:264 count_active_workers | **broker 分岐限定の壊れ方** (count_active_workers は transport == "broker" 分岐 :892-930 でのみ使用。renga 面は choose_split 分岐 :940-954 でこの関数を通らず、registry/org-config.md:36-39 も renga では max_concurrent_workers 不参照と明記): 将来 broker で multi-tab 相当の配置が生じた場合、別タブ worker が数から漏れ :906 の exceeded が発火せず max_concurrent_workers=8 を素通りする **silent overspawn**。renga 面の実害はこれではなく choose_split の current-tab geometry + 監視母集合の縮小 (次行以下) |
| runner.py:941 choose_split | rect geometry はタブ概念を持たない。全体上限が「タブごとの geometry 上限 x タブ数」に化ける。#290 の背景タブ spawn では対象タブの rect を入手できず target/direction 計算不能 |
| (renga #291) org サイドバー → choose_split | **同一タブ運用のままでも発生**: 既定有効のサイドバー (config.rs:53-59) が pane 領域から幅 26 桁 (狭い端末 16) を減算し (layout_geometry.rs:96-149)、list_panes の rect が縮んで split 候補数が減少 → SPLIT_CAPACITY_EXCEEDED の発生点が変わる (実効ワーカー容量の減少)。Phase B で SECRETARY_MIN / MIN_PANE_WIDTH 前提の再計算 or `[ui] org_sidebar="off"` の設定判断が必要 |
| runner.py:873 同名 pane ガード | 別タブの worker-{task_id} を検出できず、#289 の名前一意性タブ単位化と複合して**同名ペインのタブ違い並存**を許す → to_id が二義化 |
| spawn-flow.md:109 / worker-monitoring.md:85 | 「list_panes に無い = 退役」規則が別タブ worker に常に真 → 稼働中 worker を pane_closed **誤遷移**、容量の過大解放 (1 と逆方向の会計崩れ) |
| worker-monitoring.md:90 | 監視ループ Step 4 の走査対象が自タブ worker に縮み、別タブ worker が無監視化 |
| .dispatcher/CLAUDE.md:201 / role-contract.md:191 | curator single-flight が別タブ curator を見逃し二重 spawn |
| work_discovery_scan.py:80-81, 2159 | --free-panes が undercount で空き枠過大表示 (ランキングのみ、実害小) |
| docs/verification.md:143 | SPLIT_CAPACITY_EXCEEDED 検証手順 (9 人目) の前提が変わる |
| .claude/skills/org-down/SKILL.md:71 | suspend gate の「worker が list_panes に残っていない」確認が自タブ限定で通過し、別タブ稼働 worker を見落として未保存のまま daemon 停止するリスク (lifecycle 由来の実害大。§4.2b) |
| .claude/skills/org-conveyor/SKILL.md:30-31 | conveyor の free-pane 会計 (バックプレッシャー) が list_panes 直結。別タブ配置下で undercount し並列上限・投入判断が崩れる (同・実害大。§4.2b) |

**対応案**: Phase B 最小 = **「worker は当面 dispatcher と同一タブにのみ spawn する (multi-tab は ops/人間用)」を明文化** + check_renga_compat の bump (影響点 5 と同一修正)。multi-tab spawn 解禁時 = runtime 側改修: (i) count の入力を list_peers 全タブ + `same_tab` / state.db runs (SoT 化。tools/state_db/migrate_workers.py:464 `_count_active_runs` が既存実装例) の二重化 (spawn 儀式中の worker が peer に現れない race があるため max を取る)、(ii) live_worker_names の CLI 配線、(iii) CapacityPolicy (max_concurrent_workers) を renga 面にも**全体上限**として適用し choose_split を「タブ内 target 選出」の下位層へ降格する二層構造、(iv) 監視系を same_tab は list_panes / 別タブは list_peers+poll_events の二系統化。

**工数感**: 同一タブ維持の明文化のみ = **S**。multi-tab 解禁 = **M〜L** (実体が claude-org-runtime 側にあり**クロスリポジトリ作業**、監視二系統化を含む)。

### 3.3 影響点(3): 契約 Surface 4.2 (SINGLE-TAB MUST) の amend

**該当文言 (docs/contracts/backend-interface-contract.md)**:

- :196 「MUST resolve only against panes in the current tab. Cross-tab addressing returns `pane_not_found`.」 (list_panes / focus_pane / send_message / inspect_pane / close_pane / send_keys / set_pane_identity / spawn 全種を列挙する §4.2 の中核)
- :197 「hard constraint (suisya-systems/renga#71) — `new_tab` worker spawns would orphan dispatcher monitoring.」 (根拠にした renga#71 の制約自体が 2.0 で解消)
- :198 「SINGLE-TAB MUST. All pane-addressed operations resolve only against the current tab.」 + 「Multi-tab support is NOT in this contract revision; if added later it requires a contract amendment」 (本 Issue が要求する amend のトリガー文そのもの)
- :416 (Q10 決定表) / :427 「SINGLE-TAB MUST. Multi-tab addressing is deferred to a future contract amendment.」

**連動修正が必要な箇所**: role-contract.md:146 「Never spawn a worker pane in a different tab (`new_tab`) — renga visibility is per-tab and would orphan the worker.」 (hard prohibition)、backend-interface-contract.md:185 (broker が §4.2 を継承する注記)、:30 / :75 / :80 / :85 / :103 / :126 / :136 (各 Surface 定義の「current tab / same tab」記述)、docs/design/renga-decoupling.md:219, 263 (broker 移行設計が single-tab MUST を「継承」すると明言)、tools/skill_src/fragments/pane-layout-same-tab.{broker,renga}.md:1、.claude/skills/org-delegate/references/pane-layout.md:76、.claude/skills/pr-watch-pane/SKILL.md:184。

**対応案**: amend の方向は「タブ横断の全面禁止」から次の 3 点への再構成: (a) **名前アドレッシングの解決スコープは送信者タブ** (renga 2.0 の実仕様の追認)、(b) **数値 id 宛はタブ横断可** (新規)、(c) **orchestrator の名前送受信ペイン群は同一タブ配置 MUST** (影響点 1 案 B の規則。禁止の理由を「renga の可視性制約」から「名前解決スコープの保証」へ書き換え)。renga#71 引用は削除し #288/#289 の caller_scope / cross_tab_peers を新根拠にする。Q10 は「amendment 実施済み」として追記。broker 面は従来どおり single-tab 相当 (タブ概念なし) で変更不要である旨を明記。

**工数感**: **M** — 契約本文の amend 自体は局所だが、上記の連動 prose が 10 ファイル超に波及し、broker 移行設計 (design-only) との整合確認を含むため。

### 3.4 影響点(4): エラーコード追補 (renga-error-codes.md ほか分岐文言)

`.claude/skills/org-delegate/references/renga-error-codes.md` 本体は補完掃討で**全文精読済み** (全 177 行)。実測結果:

- **現行記載コード**: renga 共有 13 種 — `pane_not_found` (:20) / `pane_vanished` (:21) / `last_pane` (:22) / `split_refused` (:23) / `cwd_invalid` (:24) / `invalid-params` (:25) / `name_in_use` (:26) / `name_invalid` (:27) / `io_error` (:28) / `shutting_down` (:29) / `app_timeout` (:30) / `parse`・`protocol` (:31) / `internal` (:32)。加えて broker 固有 8 種 (:40-47)。`server_too_old` / `tab_*` 系は**未記載**。
- **同一タブ前提の明文** (要更新):
  - :3 「renga 0.14.0+ の renga-peers MCP サーバ」— 冒頭の版数前提が 0.18.0 系文書群よりさらに古い既存ドリフト (影響点 5 の bump 対象)。
  - :20 `pane_not_found` 行の注意書き「`list_panes` / `focus_pane` / `send_message` / `inspect_pane` は現在フォーカス中のタブのペインしか見えない。別タブ (`new_tab` 由来) のワーカーは本 code で返るので、org-delegate では全ワーカーを同一タブ内 `spawn_pane` で起動する (suisya-systems/renga#71)」— focused スコープ (#288 で caller スコープへ変更) と renga#71 根拠の双方が陳腐化。#289 の「名前解決不能の fail-loud 化」の意味論拡張もこの行に追記する。
  - :22 `last_pane` 行「唯一のタブの唯一のペイン」+「窓口/ディスパッチャー/キュレーターが同タブに同居するため」— **定義の再検討は不要**: renga 実装は既に「唯一残るタブの最後のペイン」のみ拒否し、他タブがあれば最後のペインとともにタブごと閉じる (origin/main src/ipc/mod.rs:182-186 / src/app/layout_ops.rs:598-608。#290 でも定義維持)。検討すべきは「worker close がタブ自動閉鎖を伴う」ことを org lifecycle (org-suspend の 2-pass、監視) がどう扱うか (影響点 6、§3.6 対応表と同件)。
  - :23 `split_refused` 行「`new_tab` フォールバックは tab-scoped 制約のため不可 (suisya-systems/renga#71)」— #290 のタブ指定 spawn 確定後に根拠ごと書き換え。
- **追補の挿入位置**: `server_too_old` は Known codes 表 (:18-32) への行追加、`pane_not_found` の意味論拡張は :20 注意書きの書き換え、#290 由来 3 コードは確定後に同表へ追加。:60-61 の `require_connected` ツール列挙 (`new_tab` / `focus_pane` を含む) は #288/#290 でツール集合が変わる場合に追随。broker 節 (:34-51) はタブ意味論の影響を受けず不変。

**追補すべきコード**:

| コード | 由来 | 意味 | org 側で必要な分岐追補 |
|---|---|---|---|
| `pane_not_found` (意味論拡張) | #289 | 従来「タブ内に pane が無い」に加え「名前解決不能 (旧 silent 偽成功)」「宛先がタブ違いに存在」も同コードに乗る | 「org-start 未実行」決め打ち分岐 (pr-watch-pane/SKILL.md:231, org-curate/SKILL.md:300) の見直し。「別タブに存在」と「消滅 (gone)」が区別不能になる旨 (org-attention-stop/SKILL.md:65) の注記 |
| `server_too_old` | #288/#289 | capability 不一致時の fail closed | check_renga_compat.py への preflight probe 追加 (影響点 5 と同一修正)。復旧案内 =「renga を 2.0 系へ更新」 |
| `tab_not_found` / `tab_ambiguous` | #290 (見込み) | タブ指定 spawn の selector 解決失敗 | in-flight につき確定待ち。spawn エラー分岐 (spawn-flow) への条件付き追補 |
| `tab_limit_reached` | #290 (見込み) | タブ数上限 | 新しい容量シグナル。T8 系 (delegation-lifecycle) / check_state_drift.py:23,207 の文言拡充 (§3.6 の対応表参照) |
| `target_tab_mismatch` | #290 (見込み) | 指定タブと解決先の不一致 | 同上、確定待ち |

**併せて更新する既存記述**: pr-watch-pane/SKILL.md:55 「shared codes + renga 固有コードのみ」の二分法、role-contract.md:127 / :233 の pane_not_found フォールバック規定、.curator/CLAUDE.md:58、docs/verification.md:48 (「14 ツール」+ エラー語彙)、:124。notes/broker-skill-generator-design.md:356 の「renga=正典 / broker=加算」構造上、renga-error-codes.md への追補は generator の drift CI assert (:414, :598) との整合確認が必要。

**close_pane / set_pane_identity の数値 id 必須化 (S)**: 両ツールは caller-tab 化 (#288) の例外で cross-tab のままだが、`focused`・名前は「ユーザーが表示中のタブ」基準で解決する (peer-messaging.md:79-83)。名前指定の close (.dispatcher/references/pane-close.md の close 手順、org-suspend/SKILL.md:269、pr-watch-pane/SKILL.md:215 等) は、ユーザーが別タブ視聴中に pane_not_found (→「既に閉じた」誤判定で稼働中 worker の誤退役) や同名ペイン誤 close を起こしうる。「org 群を同一タブに置く」だけでは防げないため、close/rename 系は数値 pane id 必須化を対応案とする。

**工数感**: **S** (renga-error-codes.md 追補 + 分岐文言更新。#290 由来 3 コードは確定待ちで Phase B 後半へ分割可)。

### 3.5 影響点(5): バージョン前提 (renga 0.18.0+) の bump

**該当箇所**: tools/check_renga_compat.py:33 `MIN_REQUIRED_VERSION = (0, 18, 0)` / :41 `REQUIRED_MCP_TOOLS` (14 ツール固定・capability チェックなし)、tools/test_check_renga_compat.py:21 (ほか 27/36/39/42/45/131)、scripts/install.sh:268 (ほか 344/414/574/587)、scripts/install.ps1:113 (ほか 166/250/354/382)、docs/getting-started.md:21, 111、docs/verification.md:5, 48、docs/overview-technical.md:32、docs/design/renga-decoupling.md:34、docs/design/org-app-packaging-options.md:313、docs/internal/readme-audit-2026-05-07.md:40、docs/design/core-harness-extraction.md:99、docs/contracts/backend-interface-contract.md:3, 9、tools/skill_src/fragments/pane-layout-spawn-rationale.{renga,broker}.md:1、pr-watch-pane/SKILL.md:55、org-attention-stop/SKILL.md:45 (「renga 0.18+」)、.github/workflows/install-scripts.yml (stub renga + 「Skipping 'renga mcp install'」文字列アサート)、.github/ISSUE_TEMPLATE/bug_report.yml (renga version 記入欄)。

**対応案**: renga 2.0 リリース後に版数文字列を一括 bump し、check_renga_compat.py に **capability probe (`caller_scope` / `cross_tab_peers` / 確定後 `CAP_SPAWN_TAB`) と `server_too_old` の preflight** を追加する (#288 が旧 server へ fail closed する以上、org 側の互換ゲートも「版数 + capability」の二段が必要)。同時に既存ドリフト (verification.md:592 の 0.14.0 表記、14 vs 15 ツール数不一致 = §7) を解消する。install CI のアサート文字列も追随。

**工数感**: **S** — 機械的な文字列 bump 約 15 ファイル + probe 追加 1 箇所。test の期待値追随を含む。

### 3.6 影響点(6): マルチタブ ops 配置ポリシー / SPLIT_CAPACITY 再設計

**#290 の具体化** (in-flight — Issue 本文由来。Codex レビュー確定分の切り分けは PR マージ後に要再確認):

- tagged tab selector: spawn_* に「どのタブへ」を指定する selector 引数。
- `CAP_SPAWN_TAB`: capability ネゴシエーション。org 側は check_renga_compat での probe と、非対応時の同一タブ縮退が必要。
- 背景タブ生成: フォーカスを奪わずに別タブへ spawn。「画面をそのまま見ればよい」前提 (org-attach/SKILL.md:37, org-start/SKILL.md:395, docs/operations/dispatcher-view.md:20, docs/design/org-app-packaging-options.md:180) が成立しなくなる。

**容量シグナル対応表**:

| org 現行シグナル | 現行の意味 | renga 2.0 での再定義 |
|---|---|---|
| `SPLIT_CAPACITY_EXCEEDED` (org 内部 escalate。runner.py:909/:941, delegation-lifecycle T8) | balanced split 候補ゼロ or worker 上限到達 | 維持。ただし発生条件を「全体上限 (CapacityPolicy) 到達」と「対象タブ内 geometry 枯渇」に二層化 (§3.2 対応案) |
| `split_refused` (renga error。contract:236) | タブ内 pane cap / MIN_PANE_WIDTH 割れ | 意味が **per-tab** に局所化。他タブに空きがあるケースの文言追補 |
| `tab_limit_reached` (#290 見込み) | — (新規) | 新しい容量シグナル。T8 遷移の新原因として delegation-lifecycle / check_state_drift.py:23,207 / state-drift-recovery 手順へ追補 |
| `last_pane` (contract:238) | 「唯一のタブの唯一の pane」 | 定義の再検討は不要 (renga 実装は複数タブ時、最後のペイン close でタブごと閉じ「唯一残るタブの最後のペイン」のみ拒否 — src/ipc/mod.rs:182-186 / src/app/layout_ops.rs:598-608、#290 でも維持)。検討すべきは「worker close がタブ自動閉鎖を伴う」ことの org lifecycle (org-suspend の 2-pass、監視) での扱い (verification.md:202 の手順にも波及) |

**該当箇所の柱**: 配置ポリシー正本 = tools/skill_src/fragments/pane-layout-same-tab.{broker,renga}.md:1 / pane-layout-initial.renga.md:3 / org-delegate/references/pane-layout.md:42,76 / renga-layouts/ops.toml:6-7 (タブ概念なしのレイアウト定義)。容量記述 = registry/org-config.md:36-40 (renga 面は rect balanced split 律速で max_concurrent_workers 不参照、正準 SoT は runtime 側)、work-discovery/SKILL.md:99-101、role-contract.md:136 (pane cap 16)。可視性前提 = 上記「単一画面」4 箇所 + docs/operations/attention-watch.md:22,56。

**対応案** (フェーズ分割):

1. **Phase B 当面 (S)**: 「org は当面 worker/curator/watcher を secretary・dispatcher と同一タブにのみ spawn する。multi-tab は人間の ops 用」を契約 + pane-layout fragment に明文化。これで影響点 1/2 の対応案と完全整合し、#288/#289 マージ済み環境でも現行運用が正当化される。
2. **#290 確定後 (M〜L)**: マルチタブ配置の設計判断。推奨方向は「**タブ = 自己完結セル**」(secretary+dispatcher+workers を 1 組として同一タブに閉じ、セル間は数値 id 宛のみ) か、runtime 二層容量モデル (§3.2) + 案 A 限定適用 (§3.1) の組み合わせ。org-attach (broker 専用) や dispatcher-view の「renga では不要」判断 (docs/operations/dispatcher-view.md:20) の再検討、journal-events.md:92 への spawn 先 tab 記録追加の要否検討を含む。
3. **#291 追随 (S)**: 「renga は単一画面」記述 4 箇所の書き換え + **Ctrl+B 衝突対応** (docker/README.md:34、docs/operations/dispatcher-view.md:54 ほか §4 全体、tools/org-dispatcher-view.sh:155、pr-watch-pane/SKILL.md:193、docs/operations/broker-dogfood-runbook.md:585)。renga を外側フレームに tmux を内包する運用では、サイドバー有効時 (既定 coexist) に renga が Ctrl+B を消費して即 return するため prefix は**確定的に横取りされる** (src/app/keyboard_input.rs:318-334)。tmux prefix 運用には `[ui] org_sidebar="off"` 等の設定変更 or prefix 変更が必要。加えて**サイドバー幅 26 桁の rect 減算が容量計算に直接効く** (影響点 2): Phase B で SECRETARY_MIN / MIN_PANE_WIDTH 前提の再計算 or org_sidebar off の設定判断が必要 (§3.2)。

**工数感**: 当面維持の明文化 + #291 追随 = **S**。マルチタブ配置設計 = **M〜L** (設計判断 + runtime 改修 + 監視二系統化 + 契約再 amend)。

## 4. 同一タブ前提・renga 依存の全出現箇所 (網羅列挙)

凡例: 種別 A = 同一タブ前提 (same-tab-assumption)、D = renga 依存で影響あり (renga-dependency)。影響点は 1-6。複数領域から重複報告された file:line は統合済み。delegation-lifecycle-contract.md の findings (lifecycle 領域) は §4.1 に統合した。補完掃討分: ルート文書 (CLAUDE.md / README / CONTRIBUTING / dashboard) は §4.0、lifecycle 系スキル群 (org-start / org-suspend / org-conveyor 等 9 skill) は §4.2b に掲載。

### 4.0 ルート文書 / registry / dashboard (補完掃討)

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| CLAUDE.md:26 | D | 1 | peer message・pane 操作の規約が mcp__renga-peers__* 基底である宣言 (名前指定規約の前提) |
| CLAUDE.md:28 | D | 1 | ワーカー ack (to_id="worker-{task_id}")・retro gate ack (to_id="dispatcher") の両系記述 |
| CLAUDE.md:30 | D | 4 | 「renga コードに加え broker 追加コード」の差分構造 — renga 側ベースライン変更で更新要 |
| CLAUDE.md:63 | A | 1 | dispatcher handover の send_keys(target="dispatcher") / inspect_pane 打鍵手順 (同一タブ前提) |
| CLAUDE.md:64 | A | 1 | 同じく dispatcher-resume の打鍵手順 (同一タブ前提) |
| CONTRIBUTING.md:17 | D | 5 | 「renga 0.18.0 以上」セットアップ前提 |
| README.md:57 | A | 6 | 「複数の Claude Code ペインを 1 つの画面に並べ」単一タブ前提の紹介文 |
| README.md:98 | A | 6 | スクリーンショットキャプション「窓口・ディスパッチャー・ワーカーが並んで動いている」 |
| README.md:141 | A | 6 | 「1 つの画面に各ペインが並ぶため attach ではなくその画面を直接見る」 |

CLAUDE.md:104 / :108 は §4.3、registry/org-config.md:39 は §4.8 に既出のため本表から除外 (重複統合)。

### 4.1 docs/contracts

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| backend-interface-contract.md:3 | D | 5 | 「renga 0.18.0+ is the reference implementation」 |
| backend-interface-contract.md:9 | D | 5 | Subject 節でも 0.18.0+ を明記 |
| backend-interface-contract.md:30 | A | 1,2,3,6 | 「All operations are scoped to a single tab」(Surface 1 冒頭の中核宣言) |
| backend-interface-contract.md:75 | A | 2,3 | list_panes =「enumerate every pane in the current tab」 |
| backend-interface-contract.md:80 | A | 2,3,6 | 「Visibility scope: current tab only. Panes in other tabs MUST NOT appear.」 |
| backend-interface-contract.md:85 | A | 3,6 | focus_pane =「another pane in the current tab」 |
| backend-interface-contract.md:103 | A | 1,3 | set_pane_identity 名前一意性が「in this tab」 |
| backend-interface-contract.md:126 | A | 1,3 | send_message =「another pane in the same tab」 |
| backend-interface-contract.md:136 | A | 1,2,3 | list_peers =「in the current tab」(#289 の全タブ列挙化と直接矛盾) |
| backend-interface-contract.md:185 | A | 3 | 「Broker honours the SINGLE-TAB MUST scope (§4.2)」 |
| backend-interface-contract.md:192 | A | 1,3 | 予約名 secretary/dispatcher/curator/worker-{task_id} (タブ内一意前提) |
| backend-interface-contract.md:196 | A | 1,2,3,4,6 | §4.2 中核「MUST resolve only against panes in the current tab. Cross-tab addressing returns pane_not_found」 |
| backend-interface-contract.md:197 | A | 3,4,6 | renga#71 を根拠にした hard constraint (根拠ごと陳腐化) |
| backend-interface-contract.md:198 | A | 3,6 | Q10 本文「SINGLE-TAB MUST … requires a contract amendment」 |
| backend-interface-contract.md:204 | D | 3,6 | new_tab 後の per-tab 可視性 (renga 固有挙動、#291 で変化可能性) |
| backend-interface-contract.md:205 | A | 3 | new_tab 非依存の根拠が Q10 に依拠 |
| backend-interface-contract.md:236 | D | 6 | split_refused = tab pane cap / MIN_PANE_WIDTH |
| backend-interface-contract.md:238 | D | 6 | last_pane =「the only pane of the only tab」 |
| backend-interface-contract.md:416 | A | 3 | Q10 決定表エントリ |
| backend-interface-contract.md:427 | A | 3,6 | 「Multi-tab addressing is deferred to a future contract amendment」 |
| role-contract.md:36 | A | 1 | secretary Inputs: worker からの to_id="secretary" |
| role-contract.md:37 | D | 2,6 | dispatcher→secretary 通知種別に SPLIT_CAPACITY_EXCEEDED |
| role-contract.md:44 | A | 1 | DELEGATE 送信先 to_id="dispatcher" |
| role-contract.md:46 | A | 1 | secretary→worker 追指示 to_id="worker-{task_id}" |
| role-contract.md:61 | A | 1 | 「must use stable pane names …, not numeric from_ids」(名前アドレッシングの明文規定) |
| role-contract.md:87 | D | 2 | /loop 監視が poll_events / check_messages / list_panes に依存 |
| role-contract.md:89 | A | 1 | close_pane のタブ内アドレッシング |
| role-contract.md:108 | D | 1,2 | list_peers polling / send_message / inspect_pane の watch loop |
| role-contract.md:112 | D | 2,6 | SPLIT_CAPACITY_EXCEEDED = balanced split ゼロ候補時 |
| role-contract.md:127 | A | 1,4 | numeric from_id は pane_not_found 時のみのフォールバック |
| role-contract.md:136 | D | 6 | renga pane cap (16) と balanced-split feasibility が並行度制約 |
| role-contract.md:146 | A | 3,6 | 「Never spawn a worker pane in a different tab (new_tab)」hard prohibition |
| role-contract.md:150 | D | 2 | 「reconciliation via list_panes is authoritative on restart」 |
| role-contract.md:175 | A | 1 | curator→secretary (to_id="secretary") |
| role-contract.md:176 | A | 1 | curator→dispatcher direct send |
| role-contract.md:184 | A | 1 | curator Constraints: 安定名 to_id="secretary" |
| role-contract.md:191 | D | 2 | curator single-flight を list_panes で判定 |
| role-contract.md:210 | A | 1 | worker は secretary へ直接送信 (dispatcher でなく) |
| role-contract.md:232 | A | 1 | worker Outputs: to_id="secretary" 完了報告 |
| role-contract.md:233 | A | 1,4 | numeric to_id フォールバック規定 (worker 側) |
| role-contract.md:263 | D | 1,6 | spawn_claude_pane(role="worker", name="worker-{task_id}", …) 具体呼び出し |
| role-contract.md:264 | D | 1,2 | spawn 後の worker 検出が list_peers 依存 |
| role-contract.md:285 | D | 2 | crash recovery =「list_panes reconciliation as authority」 |
| delegation-lifecycle-contract.md:31 | D | 1,2 | T2 = spawn_claude_pane 成功 + send_message |
| delegation-lifecycle-contract.md:35 | D | 2,6 | T8 (SPLIT_CAPACITY_EXCEEDED) は state file 非書き込み |
| delegation-lifecycle-contract.md:66 | D | 1,2 | T2 Trigger: balanced-split → spawn → list_peers |
| delegation-lifecycle-contract.md:78 | A | 1 | T4 Trigger: to_id="secretary" 完了報告 |
| delegation-lifecycle-contract.md:87 | A | 1 | T7 の close_pane タブ内アドレッシング |
| delegation-lifecycle-contract.md:101 | A | 1,2 | E1 検知 = poll_events (name 照合) or list_panes reconciliation |
| delegation-lifecycle-contract.md:107 | D | 2,6 | T8 見出し自体が SPLIT_CAPACITY_EXCEEDED |
| delegation-lifecycle-contract.md:110 | D | 6 | T8 の inbox 残置と再試行前提 |
| delegation-lifecycle-contract.md:121 | D | 1,2 | E1 Detection = poll_events + list_panes fallback |
| delegation-lifecycle-contract.md:122 | A | 1 | E1 Notification = to_id="secretary" |
| delegation-lifecycle-contract.md:132 | A | 1 | E3 見出しに to_id="secretary" |
| delegation-lifecycle-contract.md:173 | A | 1 | SUSPEND 応答の宛先指定 |
| state-schema-contract.md:88 | D | 6 | inbox 保持方針が T8 リトライ挙動に依拠 |
| state-schema-contract.md:177 | A | 1 | peerId 列定義が to_id 送信と結合 |
| state-schema-contract.md:189 | D | 2 | cursor rewrite 許容根拠 = list_panes reconcile |
| state-schema-contract.md:235 | D | 6 | rationale digest 内の SPLIT_CAPACITY 再掲 |
| state-semantics-contract.md:133 | D | 6 | T8 (queued→abandoned) 遷移条件 |
| state-semantics-contract.md:173 | D | 6 | queued 滞留兆候として SPLIT_CAPACITY_EXCEEDED |
| sandbox-launcher-contract.md:173 | D | 6 | worker spawn 経路 (role 別表) |
| role-pattern-sandbox-contract.md:754 | A | 1 | to_id="secretary" の self-loop 言及 |
| worker-git-guardrails-design.md:743 | A | 1 | hook 拒否時の worker→secretary 報告経路 |
| knowledge-curation-contract.md:145 | A | 1 | scrub 要求の renga-peers 送信経路 |

### 4.2 .claude/skills

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| pr-watch-pane/SKILL.md:184 | A | 1,3,6 | 「同一タブ MUST、contract Surface 4.2」を直接引用する設計根拠 (SKILL.md.in:178 相当) |
| pr-watch-pane/SKILL.md:182 | A | 1,6 | target="dispatcher" =「同一タブ scope の安定アンカー」(同 .in:176) |
| pr-watch-pane/SKILL.md:138 | D | 2,6 | 冪等チェックが list_panes に全面依存 |
| pr-watch-pane/SKILL.md:153 | D | 6 | spawn_pane 引数構成が単一タブ内 split 前提 |
| pr-watch-pane/SKILL.md:215 | D | 1,4 | name 指定 close_pane フォールバック |
| pr-watch-pane/SKILL.md:231 | D | 4,6 | pane_not_found =「org-start 未実行」決め打ち分岐 |
| pr-watch-pane/SKILL.md:236 | D | 2,6 | [name_taken] 分岐の live/stale 判定が list_panes 依存 |
| pr-watch-pane/SKILL.md:251 | D | 2,6 | identity 確定チェックも list_panes 依存 |
| pr-watch-pane/SKILL.md:55 | D | 4,5 | 「shared codes + renga 固有コードのみ」二分法 |
| org-attention-start/SKILL.md:106 | A | 1,6 | target="dispatcher" の安定名解決前提 |
| org-attention-start/SKILL.md:52 | D | 2,6 | 二重起動チェックが list_panes の全ペイン可視性に依存 |
| org-attention-start/SKILL.md:96 | D | 6 | dispatcher 起点の単一タブ前提 spawn |
| org-attention-start/SKILL.md:111 | A | 1 | name="attention" の close_pane 前提 |
| org-attention-start/SKILL.md:156 | D | 1,6 | close_pane — caller-tab 化の対象外 (cross-tab のまま。focused/名前はユーザー可視タブ基準)。数値 id 必須化 (§3.4) の対象 |
| org-attention-stop/SKILL.md:45 | D | 4,5,6 | 「renga 0.18+」版数前提 + pane id 再割当て検知が list_panes 依存 (同 .in:28) |
| org-attention-stop/SKILL.md:52 | D | 2,6 | attention ペイン集合の構築が全ペイン可視性前提 |
| org-attention-stop/SKILL.md:65 | D | 2,6 | 「list_panes に無い = gone」判定 (別タブ存在と区別不能化) |
| org-attention-stop/SKILL.md:78 | D | 1,6 | 数値 pane_id close (集合構築元のタブスコープ化を継承) |
| org-attention-stop/SKILL.md:89 | A | 1 | name 指定を使わない設計判断 (解決スコープ未定義) |
| org-curate/SKILL.md:277 (272) | D | 1 | CURATE_DONE の to_id="dispatcher" direct send |
| org-curate/SKILL.md:248 | D | 1 | to_id="secretary" 改善提案送信 |
| org-curate/SKILL.md:300 | D | 4 | pane_not_found =「dispatcher 不在」決め打ち分岐 |
| org-curate/SKILL.md:242 | D | 2 | 「ワーカーのペイン数上限」例示語彙の曖昧化 (軽微) |
| org-escalation/SKILL.md:78 | D | 1 | to_id="worker-{task_id}" 人間判断転送 |
| org-pull-request/SKILL.md:69 | D | 1 | pr-watch → in-band push (renga-peers) 経路 |
| org-pull-request/SKILL.md:153 | D | 2,6 | watcher freshness gate close が list_panes 依存 |
| org-pull-request/SKILL.md:159 | D | 1 | name 指定 close フォールバック |
| org-pull-request/SKILL.md:215 | D | 1 | WORKER_REOPENED を to_id="dispatcher" 送信 |
| org-pull-request/SKILL.md:217 | D | 1 | to_id="worker-{task_id}" 追加指示 |
| org-pull-request/SKILL.md:303 | D | 2,6 | --free-panes 算出根拠 (rect ベース空き枠) |
| secretary-resume/SKILL.md:46 | D | 6 | focused ペイン identity 確認の list_panes 呼び出し |
| secretary-resume/SKILL.md:88 | D | 1,6 | list_peers 生存確認 (全タブ化で他タブ peer 誤認リスク) |
| secretary-resume/SKILL.md:41 | D | 6 | new_tab / focus_pane の broker 意図的除外注記 |
| skill-audit/SKILL.md:131 | D | 1 | 監査結果の to_id="secretary" 送信 |
| work-discovery/SKILL.md:99 | D | 2,6 | --free-panes 意味論 (dispatch 可能 capacity) |
| work-discovery/SKILL.md:101 | D | 2,6 | renga 面 = rect ベース balanced split の空き枠 |
| 輸送層両系ボイラープレート: pr-watch-pane:51 / org-attention-stop:37 / org-curate:32 / org-escalation:24 / org-pull-request:47 / org-retro:24 / secretary-resume:35 / skill-audit:27 / org-attention-start:31 (逆方向) | D | 1,4 | ORG_TRANSPORT 切替で mcp__renga-peers__* へ機械置換される定型注記。renga 分岐が #289 の名前解決タブ制約・pane_not_found エラー化の影響を受ける |
| org-attach/SKILL.md:37 | A | 6 | 「renga は単一画面のタイリングモデル」前提 (陳腐化) |
| org-start/SKILL.md:395 | A | 6 | 「renga なら画面そのものを見ればよい」前提 (陳腐化) |
| org-start/SKILL.md:99 | A | 1,4 | name_in_use 修復不能時の numeric id 全面切替という縮退運用の先例 |
| org-delegate/references/pane-layout.md:42 | D | 2,6 | renga 面容量モデル (geometry = 暗黙上限) の正準 prose |
| org-delegate/references/pane-layout.md:76 | A | 1,3,6 | 「全ペインを同一タブ scope に配置 … Surface 4.2 の SINGLE-TAB MUST に従う」配置ポリシー正本 |
| org-delegate/references/ack-template.md:32 (43/54/63) | A | 1 | ack 文面 4 種すべて to_id="worker-{task_id}" 宛 + 返信先名指定 |
| org-delegate/references/instruction-template.md:3 (224) | A | 1 | タスク指示送信 + report_target 焼き込み |
| org-delegate/references/worker-claude-template.md:178 (181) | A | 1 | 「secretary は renga layout で固定された pane name」+ numeric フォールバック |
| org-delegate/SKILL.md:284 | A | 1 | 派遣後挨拶 (to_id="worker-{task_id}" + 報告先名再指定) |
| org-delegate/SKILL.md:340 | A | 1 | WORKER_COMPLETION_NOTED (to_id="dispatcher") |
| dispatcher-handover/SKILL.md:197 | A | 1 | handover 完了通知 to_id="secretary" |
| org-delegate/references/renga-error-codes.md:3 | D | 5 | 「renga 0.14.0+」版数前提 (0.18.0 系文書群より古い既存ドリフト) |
| org-delegate/references/renga-error-codes.md:20 | A | 1,4 | pane_not_found 注意書き「フォーカス中のタブのペインしか見えない … 同一タブ内 spawn_pane で起動 (renga#71)」— focused スコープと renga#71 根拠の双方が陳腐化 (§3.4) |
| org-delegate/references/renga-error-codes.md:22 | A | 6 | last_pane =「唯一のタブの唯一のペイン」+ 窓口/dispatcher/curator の同タブ同居前提 |
| org-delegate/references/renga-error-codes.md:23 | A | 6 | split_refused 行「new_tab フォールバックは tab-scoped 制約のため不可 (renga#71)」 |

### 4.2b .claude/skills (lifecycle 系・補完掃討)

lifecycle 9 skill (org-start / org-attach / org-setup / org-down / org-suspend / org-resume / dispatcher-handover / dispatcher-resume / org-conveyor) の 17 ファイルを全文読了した独立掃討の findings。パスは `.claude/skills/` 相対。broker render (`SKILL.md`) と生成元 (`SKILL.md.in`) の両方を持つものは `(.in:NN)` を併記。org-resume/SKILL.md と org-conveyor/references/{exit-conditions,verify-evidence}.md はヒットゼロを確認済み。org-start/SKILL.md:99 は §4.2 に既出。

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| org-start/SKILL.md:49 | A | 1,3 | 「同タブ内のペイン操作・ピア通信 … すべてカバー」の一括説明が #288/#289 のスコープ実仕様と不一致 |
| org-start/SKILL.md:50 | D | 5 | 「renga 0.18.0+ 前提」の中核記述 |
| org-start/SKILL.md:46-48 | D | 6 | MCP 14 種の列挙 (#290 のツール / 引数追加で陳腐化) |
| org-start/SKILL.md:66 | D | 4 | エラーコード列挙 |
| org-start/SKILL.md:68 | D | 6 | new_tab / focus_pane の broker 不在注記 |
| org-start/SKILL.md:93 | D | 2 | list_panes の focused=true で自ペイン特定 |
| org-start/SKILL.md:96 | D | 1 | set_pane_identity target="focused" — focused は全タブ解決の例外のため背景タブから誤爆リスク |
| org-start/SKILL.md:162 | D | 6 | pane-layout.md 参照 (配置ポリシー正本への依存) |
| org-start/SKILL.md:167 | A | 6 | spawn target="focused" の同一タブ分割前提 |
| org-start/SKILL.md:307 | A | 1 | send_keys target="dispatcher" (名前解決のタブスコープ) |
| org-start/SKILL.md:318 | D | 4 | pane_not_found を fatal とする分岐 |
| org-start/SKILL.md:349 | D | 1 | send_message 宛先の安定名 (name=) 前提 |
| org-start/SKILL.md:352 | D | 4 | エラー例示 |
| org-start/SKILL.md:374-376 | A | 1 | inspect_pane / send_keys target="dispatcher" — 窓口と dispatcher の同一タブ前提 |
| org-start/SKILL.md:433 | D | 5 | Appendix の版数記述 |
| org-start/SKILL.md:447 | D | 2,6 | worker は dispatcher の caller タブに生える旨の記述 |
| org-attach/SKILL.md:38-41 | D | 6 | 「単一画面タイリング」記述の事実誤り化 (:37 は §4.2 既出) |
| org-attach/SKILL.md:79-81 | D | 6 | ユーザー向け停止文言の誤案内化 |
| org-setup/references/permissions.md:27-40 | D | 6 | user-common 14 ツール allowlist (#290 で追補要) |
| org-setup/references/permissions.md:57 | D | 5 | 「14 種」固定表記 |
| org-setup/references/permissions.md:133-144 | D | 6 | 窓口 allow に new_tab / focus_pane 無し — 窓口がタブ操作を担う設計にすると即ブロック |
| org-down/SKILL.md:71 | A | 2 | ★実害大: suspend gate の「list_panes に残っていない」確認が自タブ限定化で別タブ worker を見落とし、未保存 worker ごと daemon 停止のリスク (§3.2) |
| org-suspend/SKILL.md:62-63 (.in:45-46) | A/D | 1,6 | ★実害大: list_peers で列挙した「全ピア」へ SUSPEND を送信 (:236 相当は「全ワーカーピア」へ SHUTDOWN)。#289 で list_peers が全タブ列挙化し数値 id 宛はタブ横断配達されるため、**別タブの無関係 agent への誤 SUSPEND/SHUTDOWN** が起こりうる。same_tab (PeerInfo 新フィールド) or org 帰属での絞り込みが必要 (§3.1) |
| org-suspend/SKILL.md:81 (.in:64) | A | 1 | inspect_pane target="worker-{task_id}" |
| org-suspend/SKILL.md:206 (.in:189) | A | 2 | attention ペイン収集が list_panes 依存 |
| org-suspend/SKILL.md:217 (.in:200) | D | 4 | pane_not_found / pane_vanished skip 分岐の意味再確認要 |
| org-suspend/SKILL.md:243 (.in:226) | D | 2 | poll_events は process-wide で機能する — 明文化推奨 |
| org-suspend/SKILL.md:269 (.in:252) | D | 1 | close_pane の name 指定 — close_pane は cross-tab だが focused/名前は**ユーザー可視タブ基準**のため、ユーザーが別タブ視聴中は名前解決が外れる (pane_not_found →「既に閉じた」誤判定や同名誤 close)。数値 id 必須化 (§3.4) の対象 |
| org-suspend/SKILL.md:273 (.in:256) | A | 2 | 残存 worker の list_panes 生存確認 |
| org-suspend/SKILL.md:278 (.in:261) | A | 2 | curator 停止判定 (list_panes 依存) |
| org-suspend/SKILL.md:286-289 (.in:269-272) | A | 4,6 | 「タブに残るのは窓口のみ … last_pane」単一タブ前提の明文 |
| dispatcher-handover/SKILL.md:34 | D | 1 | worker ack / retro gate ack の名前宛先 |
| dispatcher-handover/SKILL.md:36 | D | 4 | shared codes 列挙 |
| dispatcher-handover/SKILL.md:48 (.in:42) | A | 1 | send_keys target="dispatcher" の canonical 経路 |
| dispatcher-handover/SKILL.md:51 (.in:45) | D | 2 | 「真値は list_panes / list_peers」— 両者の観測範囲が非対称化するため書き分け要 |
| dispatcher-handover/SKILL.md:67-68 (.in:67) | D | 4 | エラー列挙 |
| dispatcher-handover/SKILL.md:202-203 (.in:195-197) | A | 1 | ack 後の send_keys 指示文面 |
| dispatcher-resume/SKILL.md:51 | D | 4 | shared codes + broker 固有コードのエラー分岐列挙 |
| dispatcher-resume/SKILL.md:53 | D | 6 | new_tab / focus_pane の broker 不在注記 |
| dispatcher-resume/SKILL.md:85-86 (.in:79-81) | D | 2 | list_panes で「監視対象なし」に誤倒れ → 誤 cold-start 案内 |
| dispatcher-resume/SKILL.md:112-115 (.in:106-109) | D | 1,2 | focused 意味論 + set_pane_identity 誤爆リスク |
| dispatcher-resume/SKILL.md:183 (.in:177) | D | 2 | 消えた worker の誤報告 → 誤 reconcile |
| dispatcher-resume/SKILL.md:234 (.in:228) | D | 1,2 | 監視ループの list_panes / inspect_pane が自タブスコープ化 |
| dispatcher-resume/SKILL.md:237-239 (.in:231-233) | D | 2 | poll_events cursor — process-wide で機能 (明文化推奨) |
| org-conveyor/SKILL.md:9 | D | 2 | 発動条件の「空き pane 分まで自走」例示 = free-pane 会計前提 |
| org-conveyor/SKILL.md:30-31 | D | 2 | ★free-pane 会計 (バックプレッシャー) が list_panes 直結 (§3.2) |
| org-conveyor/SKILL.md:119 | D | 2,6 | 並列上限 = 起動時 free pane 数 |
| org-conveyor/SKILL.md:129, 132, 136 | D | 2 | --free-panes 入力の受け渡し |
| org-conveyor/SKILL.md:154-157 | D | 2 | 解放枠への即時投入 (会計の連続性前提) |
| org-conveyor/references/scope-contract.md:9, 24 | D | 2 | 「空き pane 分だけ自走」を承認スコープの述語として明文化 |
| org-conveyor/references/scope-contract.md:59 | D | 2,6 | max_parallel = <起動時 free pane 数> の定義 |
| org-conveyor/references/dynamic-ports.md:3 | D | 2 | 「複数 worker を空き pane 分まで並列」前提の規律導入文 |

### 4.3 CLAUDE.md / .dispatcher (トレース由来)

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| CLAUDE.md:104 | A | 1 | secretary の worker 宛 ack 必須規定 (不達で dead-lock) |
| CLAUDE.md:108 | A | 1 | retro gate ack は to_id="dispatcher" direct のみ |
| .dispatcher/CLAUDE.md:49 | D | 2 | delegate-plan --panes-json 配管 |
| .dispatcher/CLAUDE.md:58 | D | 2,6 | broker 面 capacity gate 手順 (既定 8) |
| .dispatcher/CLAUDE.md:73 | D | 2 | structuredContent.panes を無加工で渡す契約 |
| .dispatcher/CLAUDE.md:80 | D | 2,6 | exit 2 / split_capacity_exceeded の escalate 分岐 |
| .dispatcher/CLAUDE.md:115-124 | A | 1 | 安定名 MUST / numeric id 禁止の現行規範 (案 A と正面衝突) |
| .dispatcher/CLAUDE.md:160 | D | 1 | dispatcher_pane_id の state.db 永続化 (数値 id 管理の唯一の前例) |
| .dispatcher/CLAUDE.md:201 | D | 2,6 | curator single-flight を list_panes で判定 |
| .dispatcher/references/spawn-flow.md:26 | D | 2 | 3-1a list_panes スナップショット取得点 |
| .dispatcher/references/spawn-flow.md:36 | D | 2 | 3-1b runtime helper への容量判定委譲 |
| .dispatcher/references/spawn-flow.md:38 | D | 2,6 | backend-aware capacity 分岐仕様 |
| .dispatcher/references/spawn-flow.md:50-52 | A/D | 1,2,6 | SPLIT_CAPACITY_EXCEEDED escalate (to_id="secretary") |
| .dispatcher/references/spawn-flow.md:76-77 | A | 1,3,6 | 同一タブ内 spawn MUST の根拠記述 (renga#71 引用、陳腐化) |
| .dispatcher/references/spawn-flow.md:78 | A | 1 | name="worker-{task_id}" 命名規約 (名前アドレッシング起点) |
| .dispatcher/references/spawn-flow.md:109 | D | 2 | pane_started 未検出時の list_panes 存在確認 (誤判定リスク) |
| .dispatcher/references/spawn-flow.md:135 | D | 1,2 | list_peers readiness リトライ (全タブ化で偽陽性可能) |
| .dispatcher/references/spawn-flow.md:183 | D | 2 | 複数 worker 順次起動時の容量再評価ループ |
| .dispatcher/references/worker-monitoring.md:85 | D | 2 | 「list_panes に無い = pane_closed」遷移規則 |
| .dispatcher/references/worker-monitoring.md:90 | D | 2 | Step 4 走査対象 = list_panes の role=="worker" |
| .dispatcher/references/worker-monitoring.md:442 | A | 1 | STALL_SUSPECTED (to_id="secretary") |
| .dispatcher/references/worker-monitoring.md:629/671/766/829 | A | 1 | RELAY_GAP / informational / outbox relay 送出 (全て secretary 名宛) |
| .dispatcher/references/worker-monitoring.md:756 | A | 1 | PANE_OUTPUT_WITHOUT_PEER_MSG nudge (二重の名前依存) |
| .dispatcher/references/pane-close.md:239 | A | 1 | curator 起動指示 (to_id="curator") |
| .dispatcher/references/pane-close.md:32/375/381/384 | A | 1 | dispatcher→secretary 通知群 |
| .dispatcher/references/pane-close.md:324 | D | 2,6 | --free-panes の空き枠把握 (work-discovery 連携) |

### 4.4 runtime パッケージ (リポジトリ外: claude_org_runtime/dispatcher/runner.py)

重要: 容量判定コードの実体は ja リポジトリ内でなく PyPI パッケージ claude-org-runtime (読了先: ~/.local/lib/python3.10/site-packages) にある。**Phase B の容量会計改修はクロスリポジトリ作業**。

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| runner.py:78 (86-87) | D | 2 | MIN_PANE_WIDTH=20 / MIN_PANE_HEIGHT=5 (SECRETARY_MIN 120x30) |
| runner.py:175 | D | 2 | DEFAULT_MAX_CONCURRENT_WORKERS = 8 |
| runner.py:264 (249-267) | D | 2 | count_active_workers() 本体 = panes 中 role=="worker" |
| runner.py:536 (601-602) | D | 2 | choose_split() rect balanced split (タブ概念なし) |
| runner.py:873 | D | 1,2 | worker 名重複ガード (母集合が自タブのみだと素通り) |
| runner.py:897-906 | D | 2 | active 算出 / free_slots / exceeded 判定 |
| runner.py:909-929 | D | 1,2,6 | split_capacity_exceeded + to_id="secretary" escalate 生成 |
| runner.py:941-951 | D | 2,6 | renga 面容量枯渇分岐 (文面にタブ語彙なし) |
| runner.py:1232 | D | 2 | CLI が live_worker_names (:251 registry 照合フック) を未配線 |

### 4.5 docs (contracts 以外)

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| getting-started.md:21 | D | 5 | renga 0.18.0+ / install コマンド版数固定 |
| getting-started.md:25 | A | 1,3,6 | 「同タブ内インスタンス間通信」明記 |
| getting-started.md:111 | D | 5 | 0.18.0 以上要求の再掲 |
| verification.md:5 | D | 5 | 検証マトリクス前提版数節 |
| verification.md:48 | D | 4,5 | 「必須 14 ツール」固定数 + chk スクリプト期待値 |
| verification.md:99 | A | 1,2,3,6 | E2E テスト 2 期待結果に「同一タブ内」明記 |
| verification.md:124 | D | 4 | split_refused / pane_not_found 分岐 (renga-error-codes.md 参照) |
| verification.md:143 | D | 2,6 | SPLIT_CAPACITY_EXCEEDED 検証手順 (9 人目) |
| verification.md:202 | D | 4,6 | last_pane 対応手順 |
| verification.md:574 | D | 1,2,4 | Test 11 の list_panes 期待値 |
| verification.md:583 | D | 1,2,3,4,6 | caller_scope 化対象ツールを含む E2E 列挙 |
| overview-technical.md:31 | A | 1,3 | 「同タブ内 Claude 間の双方向メッセージング」 |
| overview-technical.md:32 | D | 4,5,6 | ツール一覧 + 版数 +「14 ツール」 |
| overview-technical.md:56 | D | 6 | 「ペイン分割で複数インスタンス管理」単一画面前提 |
| overview-technical.md:57 | A | 1,3,6 | :31 と同旨の重複記述 |
| oss-comparison.md:19 / 63 / 140 | A | 1,3 | 「同タブ内 P2P」自己記述 x3 (:151/202 の本文にも波及) |
| non-goals.md:151 | A | 1,3,6 | 「同一タブ内 P2P が通信モデルの正本」(規範性の高い言明) |
| non-goals.md:343 | A | 1,3 | 批准済み例外注記でも「同一タブ内 P2P は不変」 |
| design/renga-decoupling.md:34 | D | 5 | 「renga 0.18.0+、14 ツール」 |
| design/renga-decoupling.md:60 | D | 1,6 | list_peers 呼出棚卸し (worker 登録待ち) |
| design/renga-decoupling.md:64 | D | 2,6 | spawn_claude_pane 呼出棚卸し (#290 で引数拡張) |
| design/renga-decoupling.md:75 | D | 2,3,6 | pane 操作の呼出主体集中 (secretary/dispatcher 別タブ未想定) |
| design/renga-decoupling.md:219 | D | 3,6 | 「single-tab addressing はサーバーが強制」前提の能力比較表 |
| design/renga-decoupling.md:263 | D | 3 | broker 設計が single-tab MUST (Q10) を「継承」 |
| design/org-app-packaging-options.md:180 (186) | D | 6 | 「renga = 単一画面」断定 (陳腐化) |
| design/org-app-packaging-options.md:313 | D | 4,5 | 「15 ツール」(14 との数不一致含む) |
| design/org-app-packaging-options.md:411 | D | 1,2,4,6 | v1.4.0 時点ツールカタログ棚卸し (#288/289/290 未反映) |
| operations/dispatcher-view.md:20 | D | 6 | 「renga は単一画面タイリングで不要」判断の再検討要 |
| operations/renga-pane-conventions.md:14 | D | 1,2 | pane 種別判定の正本 = list_peers |
| operations/renga-pane-conventions.md:24 | D | 1,2 | list_peers 出現待ち規約 (全タブ化で偽陽性検討要) |
| operations/attention-watch.md:22 | A | 6 | watcher は窓口同タブ内 split で起動する運用前提 |
| operations/attention-watch.md:56 | D | 6 | 「renga タブの外」という単一タブ時代の言い回し |
| operations/broker-dogfood-runbook.md:117 | D | 6 | renga との対比記述 (タブ概念追補が望ましい) |
| design/attention-notification.md:13 | D | 2,3 | 異常検知が inspect_pane / poll_events に依存 (caller_scope 化対象) |
| design/work-discovery-triage.md:197 | A | 1,3,6 | 「窓口境界 = renga タブ」の等式 |
| design/work-discovery-triage.md:198 | D | 2 | 「空き pane 数」ベース判定 |
| design/work-discovery-triage.md:219 | D | 2 | pane トポロジ把握前提の記述 |
| operations/state-drift-recovery.md:51 | D | 2,6 | drift 診断が SPLIT_CAPACITY_EXCEEDED / spawn 失敗を根拠 |
| internal/phase4-questions-2026-05-02.md:63 | D | 2,3,6 | dispatcher が poll_events / inspect_pane / check_messages に強依存 |
| internal/phase4-inventory-2026-05-02.md:110 | D | 2,3,4,6 | dispatcher ループの中核ツール棚卸し |
| internal/readme-audit-2026-05-07.md:40 | D | 5 | 版数整合の検証記録 (bump 時に整合チェックごと更新) |
| journal-events.md:92 | D | 6 | worker_spawned イベントに tab フィールドなし |
| legacy/hand-typed-delegate-path.md:29 | A | 1 | 手動委譲経路 (legacy、低優先) |
| sandbox-probe/notes/sandbox-probe-runbook.md:87 | D | 1,2 | list_peers での dispatcher 在席確認 (probe 専用、低優先) |
| sandbox-probe/notes/sandbox-probe-runbook.md:108 | A | 1 | send_message probe (同上) |
| org-state-schema.md:132 | A | 1 | peerId フィールド = to_id に渡す値 |
| org-state-schema.md:133 | D | 6 | paneId のタブ横断一意性が renga2 仕様次第 |
| design/core-harness-extraction.md:99 | D | 4,5 | mcp__renga-peers__* ワイルドカード allowlist |

### 4.6 tools / scripts / templates

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| tools/skill_src/fragments/pane-layout-same-tab.broker.md:1 | A | 1,2,3,6 | Surface 4.2 SINGLE-TAB MUST を直接引用する配置方針の中核 fragment |
| tools/skill_src/fragments/pane-layout-same-tab.renga.md:1 | A | 1,2,6 | renga 面の同一タブ配置根拠 (renga#71 引用) |
| tools/skill_src/fragments/pane-layout-initial.renga.md:3 | A | 1,2,6 | 初期レイアウトの同一タブ積み上げ方針 |
| tools/skill_src/fragments/pane-layout-initial.broker.md:5 | D | 2,6 | broker 面は max_concurrent_workers ゲートへ移行済み (renga 面のみ rect 依存の根拠) |
| tools/skill_src/fragments/pane-layout-spawn-rationale.renga.md:1 | D | 5 | 「renga 0.18.0+ で追加」版数記述 |
| tools/skill_src/fragments/pane-layout-spawn-rationale.broker.md:1 | D | 5 | 同上の broker 面重複 |
| tools/templates/worker_brief_normal.md:109 | A | 1,4 | 全 worker 配布の完了報告手順 (numeric フォールバック含む) |
| tools/templates/worker_brief_self_edit.md:81 | A | 1 | self-edit 版 (フォールバック記述なし) |
| tools/gen_delegate_payload.py:590 | A | 1 | 「窓口ペイン名: secretary」テンプレ (golden fixture 計 9 箇所が固定) |
| tools/gen_delegate_payload.py:1842 | A | 1 | send_plan.json to_id="dispatcher" |
| tools/gen_worker_brief.py:286 | D | 1 | transport 変数展開 (宛先名 "secretary" はテンプレ固定) |
| tools/pr_watch.py:199 | A | 1 | _PEER_NOTIFY_TARGET = "secretary" ハードコード |
| tools/pr_watch.py:261 (268-288) | D | 1,4 | push 失敗の notify_failed 記帳 → relay フォールバック |
| tools/peer_notify.py:81 | D | 1 | ORG_TRANSPORT 無設定 → renga 経路 |
| tools/peer_notify.py:238-239 | D | 1 | send_message(to_id, message) の JSON-RPC 発行実装 |
| tools/relay_scan.py:74 | A | 1 | DEFAULT_RECIPIENT = "secretary" |
| tools/dispatcher_retro_gate.py:306 (150-155) | A | 1 | --secretary 既定 + from_name 照合 |
| tools/check_renga_compat.py:33 | D | 5 | MIN_REQUIRED_VERSION = (0, 18, 0) |
| tools/check_renga_compat.py:41-60 | D | 4,5 | REQUIRED_MCP_TOOLS 14 個。capability probe 不在 (fail-closed への preflight 欠落) |
| tools/test_check_renga_compat.py:21 (27/36/39/42/45/131) | D | 5 | 版数ハードコードのテスト対 |
| tools/work_discovery_scan.py:80-81 | D | 2 | --free-panes 意味論の一次証跡 (renga = rect-available split pane) |
| tools/work_discovery_scan.py:2159 | D | 2 | CLI ヘルプの重複記述 |
| tools/check_state_drift.py:23 | D | 6 | T2 失敗例として SPLIT_CAPACITY_EXCEEDED (tab_limit_reached 追補候補) |
| tools/check_state_drift.py:207 | D | 6 | 同上のユーザー向け detail 出力 |
| scripts/install.sh:268 (344/414/574/587) | D | 5 | renga@0.18.0 インストールコマンド固定 |
| scripts/install.ps1:113 (166/250/354/382) | D | 5 | Windows 版の同重複 |
| tools/state_db/migrate_workers.py:464 | 参考 | 2 | _count_active_runs = DB ベースのタブ非依存カウント実装例 (改修の参考) |
| tests/fixtures/delegate_payload/*.golden.md:13 | A | 1 | 「窓口ペイン名: secretary」固定 (5 ファイル) — Phase B の文言変更で更新が必要になる golden fixture 面 |
| tests/fixtures/transport_seam/worker_brief_normal_full.golden.md:66 (minimal :55、self_edit 系 :52/:38) | A | 1 | to_id="secretary" + pane_not_found フォールバック文言の golden 固定 (同上) |
| tests/fixtures/transport_seam/delegate_body.golden.txt:13 | A | 1 | 「窓口ペイン名: secretary」固定 (同上) |
| tests/fixtures/transport_seam/send_plan.golden.json:3 | A | 1 | to_id="dispatcher" 固定 (同上) |
| tests/test_gen_delegate_payload.py:151 | A | 1 | secretary 宛先のテスト期待値 (同上) |

### 4.7 knowledge / .state / renga-layouts / .claude

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| knowledge/curated/dispatcher-monitoring.md:7 | A | 1,2 | 監視サイクルが list_panes / inspect_pane で全 worker 可視前提 |
| knowledge/curated/dispatcher-monitoring.md:221 | A | 2,6 | curator single-flight は list_panes ベース |
| knowledge/curated/doc-audit.md:15 | D | 1,3,4 | renga#289 実装時に renga 側凍結文書の same-tab 記述が見落とされた教訓 (本監査の直接の先行事例) |
| renga-layouts/ops.toml:6-7 | A/D | 6 | レイアウト定義にタブ概念なし。dispatcher 以下は動的 spawn (暗黙同一タブ) |
| .state/dispatcher/panes-snapshot.json:4 | A | 2 | list_panes キャッシュ実データにタブ識別子なし |
| .state/workers/worker-verify-broker-pane-layout.md:21 | A | 6 | 「同一タブ split 積み」指摘を誤検出却下した過去判断 (再検証要) |
| .state/workers/worker-verify-broker-pane-layout.md:23 | A | 6 | same-tab 語彙の論理 scope 表現化案の先行議論 |
| .state/workers/worker-broker-prose-sweep-t1.md:5 | D | 2 | Status: stale_pane_not_found (list_panes 由来の生存判定) |
| .state/dispatcher/inbox/kura-215-prod-release.json:6 | A | 1 | DELEGATE 定型句 to_id="secretary" (inbox/outbox 数十件の代表) |
| .state/dispatcher/outbox/dispatcher-resume-loop-fix-instruction.md:7 | A | 1 | 同上パターン別実例 |
| .state/dispatcher/inbox/agent-office-4-windows-pipe-smoke-001.json:10 | A | 1 | escalation 経路も同じ名前宛先前提 |
| .state/workers/archive/worker-feat-org-attach-skill.md:8 | D | 6 | org-attach が list_panes 全結果と tmux 実ペインの突き合わせ設計 |
| .claude/settings.json:19-32 | D | 要監視 | renga-peers 14 エントリ allowlist (origin/main の 15 ツール中 spawn_codex_pane を除く 14)。ツール名不変のため更新不要だが新 capability/引数追加時は棚卸し要 |

### 4.8 .curator / registry

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| .curator/CLAUDE.md:13 | A | 2,6 | dispatcher が curator ペインを閉じる運用 — close_pane は cross-tab のまま (caller-tab 化対象外) だが focused/名前はユーザー可視タブ基準のため、数値 id 必須化 (§3.4) の対象 |
| .curator/CLAUDE.md:39 | A | 1,4 | to_id="secretary" 改善提案送信 |
| .curator/CLAUDE.md:40 | A | 1,4 | to_id="dispatcher" 完了通知 (不達で org サイクル停止) |
| .curator/CLAUDE.md:48 | D | 1,4 | numeric id 脆弱性を理由とする安定名推奨 (根拠の amend 要) |
| .curator/CLAUDE.md:50 | A | 1,4 | 「送信は必ず安定名を使うこと」明示指示 |
| .curator/CLAUDE.md:56 | A | 1 | secretary/dispatcher = ops レイアウトで固定された pane name 前提 |
| .curator/CLAUDE.md:58 | A | 1,4 | pane_not_found を稀なフォールバック経路と想定 (別タブ時は常態化) |
| registry/org-config.md:36 | D | 2,6 | broker 面は choose_split をバイパスする対比記述 (SPLIT_CAPACITY 再設計と連動) |
| registry/org-config.md:39 | A | 2,6 | 「ターミナルサイズが許す限り分割し続ける」= 単一タブ時代の容量前提そのもの |
| registry/org-config.md:40 | D | 2,6 | 正準 SoT = claude_org_runtime.dispatcher.runner へのポインタ (改修完了待ちで文言確認) |

### 4.9 notes / CI / hooks / キーバインド (追加掃討)

| file:line | 種別 | 影響点 | 要旨 |
|---|---|---|---|
| notes/broker-skill-generator-design.md:356 | D | 4 | renga-error-codes.md =「renga 正典 / broker 加算」の非対称構造 (追補時に drift CI 整合要) |
| notes/broker-skill-generator-design.md:414 | D | 4,6 | new_tab/focus_pane 不在注記の drift CI assert (#290 で注記カテゴリ拡張要) |
| notes/broker-skill-generator-design.md:598 | D | 4 | auth トレードオフ議論内の focus_pane/new_tab 言及 (薄い関連) |
| .github/workflows/install-scripts.yml | D | 5 | stub renga で installer をスモーク +「Skipping 'renga mcp install'」文字列アサート (critic 実確認) |
| .github/ISSUE_TEMPLATE/bug_report.yml | D | 5 | renga version 記入欄 (critic 実確認) |
| docker/README.md:34 | D | 注記 | tmux デタッチ Ctrl-b d — #291 サイドバー有効時 (既定) は renga が Ctrl+B を確定的に横取り (keyboard_input.rs:318-334) |
| docs/operations/dispatcher-view.md:54 (§4 全体: 59/60/72-76) | D | 注記 | 入れ子 tmux の「Ctrl-b 2 回押し」運用 — renga 外側で三重競合の可能性 |
| tools/org-dispatcher-view.sh:155 | D | 注記 | ヘルプ文言の Ctrl-b 案内 (renga 併用注意なし) |
| .claude/skills/pr-watch-pane/SKILL.md:193 | D | 注記 | Ctrl-b s 言及 (同・確定的横取りの対象) |
| docs/operations/broker-dogfood-runbook.md:585 | D | 注記 | attach 手順の prefix Ctrl-b (broker 専用のため実害限定) |

## 5. renga 依存だが今回無関係の箇所 (完全性の証明)

kind=renga-dep-unrelated として確認・記録した全箇所。いずれも既知 6 影響点に該当しない理由を付す。

**.claude/skills**: pr-watch-pane:228 (MIN_PANE 割れ split 拒否 = タブ数無関係の物理制約) / :90, :98 (tmux/herdr/wezterm backend 差異の self-close 挙動 = backend 種別の別軸) / :54 (dev-channel 承認段数 = 名前解決・配置と無関係)。org-attention-start:31 (broker 固有エラーコードは broker backend 固有) / :116 (MIN_PANE_WIDTH 物理制約)。org-curate:80 (curator 識別は CWD ベースで pane/タブ非依存)。org-retro:103 (「Step 4.2」= retro フロー見出し番号。grep「4.2」の機械的誤検出として明示排除)。work-discovery:90 (「renga」= リポジトリ名として登場のみ)。secretary-handover:26, 71, 157 (renga API を一切呼ばず state.db 読取のみ)。lifecycle 系 (補完掃討分): org-suspend:215 (数値 pane id での close_pane — close_pane は cross-tab かつ数値 id 指定のため無影響)。※org-suspend:62 (list_peers) は当初「用途上無影響」と判定していたが、Codex レビューで broadcast 宛先集合の拡大 (誤 SUSPEND/SHUTDOWN) が判明したため §3.1 / §4.2b へ移動。org-start:315 (list_peers poll) / org-start:360 (close_pane — 数値 id 運用)。dispatcher-resume:116 (list_peers での peer_id 取得 — 新仕様下でも正しい設計) / dispatcher-resume:194 (list_peers での名前現存確認)。org-setup/SKILL.md:26, :92 (ツール名レベルの言及のみ)。org-attach:87-159 (broker 専用 join 手順)。org-down:24-32, 53-55, 127-128, 159-165 (broker / フォールバック記述)。org-conveyor:81-91 (per-transport 認可の話)。org-conveyor/references/dynamic-ports.md:34 (実害軽微)。

**docs/contracts**: backend-interface-contract:288, 293, 342 (org-broker は tmux/WezTerm 駆動の別実装で renga 2.0 の変更影響を受けない)。role-contract:75 (dispatcher 経由ルーティング統制でタブ配置無関係)。state-schema:102 (poll_events カーソル形式はタブスコープ無関係)。state-semantics:79, 127, 138 (spawn 失敗一般・終端状態責務分担の話)。sandbox-launcher:152 (spawn チェーン図の起点表記、sandbox 層)。role-pattern-sandbox:786 (settings.local.json クリーンアップ除外理由)。knowledge-curation:60 (curated note 命名例)。

**docs (その他)**: verification:592 (0.14.0 表記 = renga2 と無関係の既存ドリフト、§7 参照)。broker-dogfood-runbook:199 (broker 自体の role 別ツール表 = 同名なだけの独立実装)。attention-notification:556 (pane_crashed = 生死の話でタブ無関係)。phase-2b-guardrails:285 (「4.2」節番号誤検出)。linux-sandbox-verification:195 (bwrap 能力継承の文脈)。transport-switch-ux:153 (ORG_TRANSPORT env 継承の話。「ペイン境界」はプロセス境界の意)。org-docker-distribution:95 (broker 経由 spawn = renga 非依存)。

**tools**: dual-system-header-short.broker.md:7 ほか同型多数 (new_tab/focus_pane の broker 意図的除外は renga 側変更でも不変 — broker は今後も pane-addressed のまま)。pane-layout-viewport-rationale.broker.md:1 (split 時 viewport 半減 = 同一タブ内 split の UI 挙動)。pane-layout-future-upstream.renga.md:1 (split target 自動選出の将来提案 = タブと別軸)。org_setup_prune.py:1052 / check_role_configs.py:676 (transport 間ツール名プレフィックス書換のみ。ツール名は renga 2.0 でも不変)。dispatcher_retro_gate.py:150 (受信済み message dict の文字列比較のみ)。inspect_pane_state.py:47 / inspect_anomaly_scan.py:6 (渡された grid 文字列の解析のみ)。templates/attention.example.json:55 (汎用の無応答検知文言)。secretary_queue_watcher.py:467 (broker queue.jsonl の read-only 監視 = renga タブ意味論の影響外)。

**knowledge / .state / docker**: knowledge/curated/renga.md:7, 31 (renga 自体の Rust 実装/テスト作法の開発知見)。broker.md:15, 36 (broker daemon / transport 既定の別軸議論)。codex.md:187 (renga#288 実装過程のレビュー教訓 = renga 内部品質の話)。herdr.md:62 (pane id パーサの herdr 形式拒否 — #288-290 は pane id 形式を変更しない)。skill-development.md:211 (fragment 生成の仕組み自体はタブ無関係)。wsl-tui.md:26 (端末描画)。release-process.md:35 (CHANGELOG 運用慣行)。workflow-orchestration.md:79 (#291 実装時のレビュー手法知見)。docker/README.md:40 (docker 配布は renga transport 対象外)。.state/pending_decisions.json:275 (transport 既定の別軸判断), :833 (renga#288 実装タスク自身の設計判断ログ)。.state/workers/worker-renga-290-spawn-tab-placement.md:8 / .state/dispatcher/outbox/renga-291-org-sidebar-instruction.md:10 (renga 本体開発を dispatch した記録で org 自身の前提ではない)。worker-layout-multi-15.md:8 ほか同型 16 件 (自ペイン報告のみ = caller タブ限定化の影響なし)。panes_snapshot.json:2 (tmux backend の %N 形式 = renga transport ではない)。

**.curator / registry / notes / .hooks / renga-layouts**: .curator/CLAUDE.md:42 (broadcast vs direct の配送保証差 = 既存仕様)、:57 (numeric id 再採番の一般注意 = 同一タブ内でも起こる既存事項)。registry/org-config.md:34 (数値 8 自体は transport 非依存)、projects.md:35 / projects.md.bak:35 / projects.example.md:63 (プロジェクトカタログ行)。notes/broker-skill-generator-design.md:117 (:414 に集約した重複言及)。renga-layouts/ops.toml:10 (0.16.0 Alt+P 自動アップグレード = マルチタブと無関係の別機能)。docs/design/org-app-packaging-options.md:435 (#291 で概ね実現済みの構想 = 意味論変更の対象外)。.hooks/block-workers-delete.sh:51-190 (renga コマンド例外判定 = renga 依存コードだがタブ意味論と無関係。critic 実確認)。

**ルート文書 / dashboard / CHANGELOG (補完掃討分)**: CLAUDE.md:29 (dev-channel 承認は自ペイン対象でタブ非依存)、:37 (broker の tmux ペイン記述 = broker 面)、:66 (pane_id 維持ポリシー — 同一タブ内でも同じ)、:133 (renga-peers 受信の運用ポリシーで配置非依存)。CHANGELOG.md:117 (pr-watch の renga-peers 通知の履歴記録のみ)、:131 (README リビルドのペインレイアウト図言及 — 履歴記録のみ)。dashboard/server.py:97 (Pane ID を不透明文字列として表示するのみ)。notes/broker-skill-gen-design-input.md:26 (new_tab / focus_pane の broker 不在注記の保持方針 — §4.9 の drift CI 論点と同件の入力メモ)。registry/projects.md:35 等は前段落に既出。

## 6. 監査の方法と網羅性

**領域分割**: org リポジトリを 9+α 領域に分割し並列掃討した — (1) .claude/skills のうち 13 スキル (21 ファイル)、(2) docs/contracts 全 10 ファイル、(3) docs のうち contracts 以外 63 ファイル、(4) tools / scripts / tests / fixtures (tests/fixtures の golden 固定名・テスト期待値は §4.6 末尾に列挙)、(5) knowledge / .state / .claude 直下 / docker / renga-layouts、(6) .curator 全 4 ファイル、(7) registry 全 5 ファイル、(8) リポジトリ全体への追加パターン掃討 (Ctrl+B / サイドバー系)、(9) コード追跡 2 本 (容量会計 / 名前宛先)。各領域で指定 grep パターン一式 (同一タブ / same tab / list_panes / list_peers / to_id= / pane_not_found / SPLIT_CAPACITY / Surface 4.2 / renga 0.18 / spawn_* / new_tab / focus_pane / close_pane / renga-peers / count_active_workers / 空き枠 / 容量 / ペイン ほか補助セット) を適用し、ヒット全行 (contracts 226 件、docs 433+7 件、tools 394 件など) を文脈込みで目視判定、ゼロヒットファイルは個別に無該当を確認した。

**critic の verdict と追加掃討**: 初回 5 掃討に対し critic が「掃討漏れ 4 件」と判定 — (a) .curator/ (影響点 1/4 の該当箇所を含む)、(b) registry/org-config.md (影響点 2/6 の設定 SoT)、(c) .github/workflows + .hooks (CI・フック層)、(d) Ctrl+B キーバインド衝突観点 + notes/。(a)(b)(d) は追加掃討を実施し本報告に統合済み。(c) は critic 自身の実確認結果 (install-scripts.yml の stub renga アサート、bug_report.yml の version 欄、block-workers-delete.sh の renga 例外) を §4.9 / §5 に転記した — **専用の全パターン掃討は未実施のため Phase B 冒頭での補完を推奨**。dashboard/ はヒットなしを確認済み。knowledge-state 掃討が除外した .claude/worktrees/ (repo フルクローン 7 個 = 他エージェント worktree の実行時アーティファクト) の除外判断は critic も妥当と判定。

**カバレッジの限界 (明示)**:

1. **実行時挙動は未検証**。全て静的な読み取り監査であり、renga 2.0 環境での実挙動 (poll_events のイベントスコープ、名前解決の実際のエラー文字列等) は Phase B で要スモーク。
2. **#290 は in-flight**。同 Issue 依存の記述はすべて条件付き。
3. **renga-error-codes.md は本補完パッチで精読済み**。初回掃討では分担の継ぎ目に落ちて未読だったが、補完掃討で全 177 行を精読し、§3.4 を実測結果 (現行コード一覧・同一タブ前提記述の行番号・追補の挿入位置) に更新した。
4. .dispatcher / org-delegate 系 / renga リポジトリは sweep 形式の独立報告ではなく**経路追跡 (トレース 2 本) でカバー**した。パターン掃討としての網羅性は他領域より弱く、Phase B での差分確認を推奨。なお CLAUDE.md 本体ほかルート文書は §4.0 の補完掃討で、lifecycle 9 skill は 17 ファイル全文読了の独立掃討 (§4.2b) で補完済み。
5. runtime パッケージはインストール済み site-packages を読んだもので、claude-org-runtime リポジトリ HEAD との差分は未確認。
6. .state/pr-watch-*.log は運用ログとしてサンプル確認のみ。

**該当箇所数サマリ** (§4 の延べ数。統合行は 1 件扱い。§4.0 / §4.2b / renga-error-codes.md の補完掃討分を含む):

| 影響点 | 延べ該当 | 中核領域 |
|---|---|---|
| (1) 名前宛先のタブ制約 | 約 145 | contracts 34 / skills 32 + lifecycle 系 12 / dispatcher 系 12 / docs 20 / tools 14 / ルート文書 6 |
| (2) 容量会計 | 約 103 | dispatcher 系 15 / contracts 20 / lifecycle 系 22 / docs 15 / runtime 9 |
| (3) Surface 4.2 amend | 約 41 | contracts 17 / docs 17 |
| (4) エラーコード追補 | 約 52 | skills 14 + lifecycle 系 10 / docs 10 / .curator 5 |
| (5) 版数 bump | 約 28 | docs 9 / tools 7 / lifecycle 系 4 / CI 2 |
| (6) 配置ポリシー / SPLIT_CAPACITY | 約 117 | skills 26 + lifecycle 系 17 / docs 25 / contracts 23 |

## 7. 付記: renga 2.0 と無関係だが気づいた環境課題

1. **bwrap sandbox 破損**: 掃討時、Bash sandbox が壊れており読み取り専用コマンドですら sandbox 内で実行できなかった (dangerouslyDisableSandbox での読み取りが必要だった)。本報告書の書き出し時にも同事象が再現している。ワーカー環境の sandbox 設定の点検を推奨。
2. **docs/verification.md:592 の版数ドリフト**: 本文の 0.18.0 前提と矛盾する「0.14.0 以上か確認」記述が残存。影響点 5 の bump と同時修正を推奨。
3. **renga-peers ツール数の記述不一致**: overview-technical.md:32 は「14 ツール」、org-app-packaging-options.md:313 は「15 ツール」(spawn_codex_pane の数え方差)。renga 2.0 でのツール増減と併せて正規化を推奨。
4. **transport 既定の記述割れ**: tools 側トレースでは「broker 既定フリップ済み (Epic #586)」とする記述と、org-delegate/references/worker-claude-template.md の「既定 renga」記述が併存。正本の明確化を推奨。
5. **.claude/worktrees/ の残置**: repo フルクローン 7 個 (他エージェント worktree の実行時アーティファクト) が残っており、掃討・検索系タスクのノイズ源になる。クリーンアップ方針の検討を推奨。
6. **サブエージェント指示文中の sandbox 無効化文言による silent block**: サブエージェントへの指示プロンプト内に sandbox 無効化 (dangerouslyDisableSandbox) の許可文言を含めると、安全分類器がエージェント起動を silent block する事象が発生した (本監査の workflow で 5 エージェント、同日の別ワーカーでも 1 例)。項目 1 の bwrap sandbox 破損の回避策として書いた文言が別の障害を誘発する連鎖であり、根治は項目 1 の sandbox 修復側で行うべき。
