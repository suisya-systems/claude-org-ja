# Spawn flow — ワーカー起動と状態記録（ディスパッチャーが実行）

`.dispatcher/CLAUDE.md` から参照される、`DELEGATE` 受信後のワーカー起動 / 指示送信 / 状態記録の詳細手順。**ディスパッチャー専属**で、窓口は触らない。判定ロジックは `claude-org-runtime dispatcher delegate-plan` helper に委譲済み。本ファイルは MCP 呼び出しと state 書き込みの具体手順をまとめる。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `renga` / opt-in `broker`）**: 本ファイルの手順は **既定 `renga`** で書いてあり、`ORG_TRANSPORT` 無設定なら以下すべてそのまま従えばよい（既定挙動は不変）。`ORG_TRANSPORT=broker`（opt-in）では MCP サーバー名が `org-broker` になり、ツールの **完全修飾名が `mcp__renga-peers__*` → `mcp__org-broker__*`** に機械的に置換される（引数形・セマンティクスは同一なので手順の論理は変わらない）。輸送依存で**手順が変わる 3 点**だけ broker 併記する:
> 1. **受信モデル（push 一次 = `claude/channel` / pull フォールバック）**: renga はワーカー報告が `<channel source="renga-peers" …>` として in-band で push される。broker は **push 一次**に再設計済（runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し `notifications/claude/channel` で本文を idle セッションへ注入する。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン / claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（§9.6 読み替え表の役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。Step 3-5 で送る指示や `worker-monitoring.md` の `check_messages` 受信は broker でも同じツール名で動く。ナッジが出れば契機になりうるが idle を起こさないため能動 poll が受信の正路。既存「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。
> 2. **spawn 儀式（folder-trust 承認 + dev-channel sidecar 承認の再導入）**: renga の `spawn_claude_pane` は `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」プロンプトを Enter 承認する（3-3b）。broker は **`--mcp-config <broker>` を注入**し承認プロンプトが Claude Code の **folder-trust プロンプト**に変わる（`send_keys(enter=true)` で機械承認）**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し、dev-channel 承認プロンプト（「Load development channel?」）を `send_keys(enter=true)` で **再導入**機械承認する（**3-3b の dev-channel 承認の再導入**。3-3b 詳細手順を参照）。これは ratified §5/§8.5 の folder-trust フローへの **加算であり置換ではない**（設計 broker-native-roles.md §9.5。※ contract §5.1・§8.5 / `docs/design/renga-decoupling.md` §4.6 の「dev-channel→`--mcp-config` 置換・dev-channel prompt は存在しない」記述は、push 一次採用で channel sidecar 分の dev-channel load が additive に復活する＝S3 で 2026-06-15 に ratified・contract で amend 済み）。
> 3. **エラー分岐（broker 追加コード）**: renga のエラーコード（`[split_refused]` / `[pane_not_found]` / `[cwd_invalid]` / `[invalid-params]` 等、3-2 のエラーハンドリング参照）に加え、broker は `[token_invalid]` / `[session_invalid]`（token 系）・`[tool_not_authorized]`（auth_role tier gating）・`[no_backend]`（adapter 不在 = adapter_unavailable）・`[nudge_failed]`・`[peer_not_found]` / `[name_taken]` を返しうる。未知コードは renga と同じく default-branch で escalate 経路に流す。
>
> なお `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外。本フローは元々使わない）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次再設計）/ `docs/design/ja-migration-plan.md` §5.2(ii) / §3 / §8。broker 実走（dogfood）は Issue G スコープで本ファイルの既定経路ではない。（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）

> **state-db cutover (M4, Issue #267)**: 構造化セクション
> (Worker Directory Registry / Active Work Items / Dispatcher / Curator / Status) の write は
> **必ず `StateWriter.transaction()` 経由**で行う。`transaction()` の post-commit hook が
> `.state/org-state.md` を DB から自動再生成するので、本 reference では markdown 直接編集の
> 手順を残さない (drift_check が検出する)。events は DB の `events` テーブルが SoT。

## Step 3: ワーカー起動と指示送信

### 3-1. balanced split で target / direction を決める

旧設計は序数 `k` ベースの lookup table で target を決めていたが、ワーカーが途中で閉じた後の再派遣や想定外の退役順でテーブル前提と実レイアウトが乖離し、`[split_refused]` を誘発しやすかった。renga-peers MCP の `mcp__renga-peers__list_panes` が各ペインの `id / name / role / focused / x / y / width / height` (cell 単位) を返すため、**現在のレイアウト (rect) から動的に target と direction を選ぶ方式**を取る。詳細ルールは [`.claude/skills/org-delegate/references/pane-layout.md`](../../.claude/skills/org-delegate/references/pane-layout.md) の「ワーカーの balanced split 戦略」セクションを参照。

#### 3-1a. レイアウト取得

`mcp__renga-peers__list_panes` を呼び、返却テキストから全ペインの属性を抽出する。各ペインは以下のフィールドを持つ:

- `id`: 整数
- `name`: 文字列（`spawn_pane` / `new_tab` で明示指定されたペインのみ、未設定なら省略）
- `role`: 文字列 ("secretary" / "dispatcher" / "curator" / "worker" のいずれか。未設定なら省略)
- `focused`: bool（出力行に `(focused)` が付くかで判断）
- `x / y / width / height`: cell 単位の整数

#### 3-1b. balanced split アルゴリズム（runtime helper が判定ロジックを実行）

balanced split の判定 (target / direction の選択、MIN_PANE / secretary 保険 / role priority によるソート、rect 隣接判定) は **dispatcher が `claude-org-runtime` の `delegate-plan` helper 経由で実行する**。Claude 側で再実装するロジックではない。helper は `mcp__renga-peers__list_panes` の rect スナップショットと task JSON を入力に取り、`spawn` ターゲット名と direction を含む action plan を返す（候補が空なら `split_capacity_exceeded` で escalate を指示）。

> **輸送層で capacity 機構が分岐する (runtime 0.1.31 / #104、backend-aware worker capacity)**: 上記 rect ベース balanced split は **renga 面の既定経路（caller タブ内に配置する場合）** の律速。**broker 面**（`ORG_TRANSPORT=broker` / コード既定）では helper が `choose_split` / rect geometry を**バイパス**し、`--max-concurrent-workers N`（既定 8 / `unlimited` opt-in）による capacity gate に切り替わる（「アクティブ worker 数 < `N` なら固定 spawn target で spawn / 到達で `split_capacity_exceeded`」）。dispatcher は `ORG_TRANSPORT` を解決して `--transport` を helper へ**明示で渡す**（runtime は panes snapshot から transport を推定しない契約）。`.dispatcher/CLAUDE.md` の delegate-plan helper 節の CLI 例と `registry/org-config.md` の `max_concurrent_workers` 導線を参照。
>
> **renga 面で `max_concurrent_workers` が無視されるのは caller タブ内に配置する既定経路のときだけ** (runtime 0.1.39 以降): **`--overflow-to-new-tab` を armed にし、かつ `--server-capability spawn_tab` を宣言した**場合（明示 `--tab` が無く、`choose_split` も候補ゼロで実際に新規タブへ overflow する場合）に限り、rect 上限が消える代わりに **fleet ceiling が live になり**、`--peers-json` が必須になる（欠落は `input_invalid` / exit 1 で拒否。overflow で置いたワーカーは自分のタブに居て `--panes-json` に二度と現れないため、census 無しでは上限が 0 のままタブを無制限に増やす）。
> - **`spawn_tab` を宣言せずに `--overflow-to-new-tab` だけ渡しても overflow は発火しない**: capability 未宣言は fail closed で、overflow は「無視した」旨を `reasons` に記録して caller タブ配置に降格し、従来どおりの rect escalate に戻る（`--peers-json` 欠落も拒否されない）。この分岐を「必須なのに拒否されない」と誤読しないこと
> - **現行 ja はこれらのフラグを渡していない**（dispatcher の実呼び出し経路 — `.dispatcher/CLAUDE.md` の CLI 例・`tools/` のスクリプト — のいずれにも配線されていない。以下の説明文中の言及を除き、実行される呼び出しは存在しない）ので、今日の運用では従来どおり無視される
> - なお runtime CLI の `ceiling_applies`（`transport == "broker" or (transport == "renga" and --overflow-to-new-tab and --tab 未指定)`）は **`--max-concurrent-workers` の値を検証するかどうか**の条件であって、ceiling が実際に binding になる条件そのものではない（後者は上記のとおり `spawn_tab` と overflow 到達も要る）

仕様詳細・定数値・ソートキー・rect 隣接の正確な定義は **runtime SoT** を参照する:
- CLI (運用上の標準呼び出し): `claude-org-runtime dispatcher delegate-plan --task-json ... --panes-json ... --state-dir ... --transport {broker|renga} [--max-concurrent-workers N] [--template-repo ...] [--locale-json ...]`。`--transport` は `ORG_TRANSPORT` 解決値を明示で渡す（runtime は panes snapshot から transport を推定しない、runtime 0.1.31 / #104）。broker 面では `--max-concurrent-workers N`（既定 8 / `unlimited` opt-in）が capacity を gate する。`.dispatcher/CLAUDE.md` の delegate-plan helper 節が一次手順。**この CLI 例は現行 ja の運用形であって helper の完全なフラグ集合ではない**: runtime 0.1.39 は `--peers-json` / `--overflow-to-new-tab` / `--server-capability` / `--tab` も受け取るが、ja はいずれも渡していない（実配線は未着手）。フラグの網羅は `claude-org-runtime dispatcher delegate-plan --help` を参照
- ライブラリ: `claude_org_runtime.dispatcher.runner` モジュールの `build_plan()` (action plan 全体: `spawn` / `after_spawn` / `escalate` / `state_writes` / `status`) と、その内部で呼ばれる `choose_split()` (target / direction 選択) / `rect_adjacent()` / `_ROLE_PRIORITY` / `MIN_PANE_*` / `SECRETARY_MIN_*` 定数

dispatcher が helper を経由しない degraded mode に入った場合、判定再現は `claude_org_runtime.dispatcher.runner` モジュール (インストール先は `python3 -c "import claude_org_runtime.dispatcher.runner; print(claude_org_runtime.dispatcher.runner.__file__)"` で解決可能) を一次参照する。

#### 3-1c. 候補が空だった場合

`$target` が空（候補セットが空）の場合、ディスパッチャー Claude は **`spawn_pane` を発行せず**、代わりに renga-peers で窓口 (`secretary`) に escalate メッセージを送信する:

1. `mcp__renga-peers__send_message(to_id=..., message=...)` を呼び、**helper が返した `escalate.to_id` / `escalate.message` をそのまま渡す**。本文は **runtime 生成物の verbatim 転送**であり、ディスパッチャーが自前の文面を組み立てる余地はない:
   - **言い換え・要約・翻訳・再構成をしない**（1 文字も変えない）。runtime 側は `runner.py: _renga_rect_escalation_message` の docstring で「claude-org-ja forwards this text to the secretary verbatim」と明記し、その前提で **診断を本文に merge せず append** している（`explain_left_panels` の docstring も同じ前提を置く）。ja が言い換えると、実測されたペイン領域・左パネルが食っている桁数と回収案内（`Ctrl+B` / `[ui] org_sidebar = "off"`）・新規タブ見積もり・tabs_seen が窓口に 1 つも届かない
   - **文面は英語で届くが、英語のまま転送する**。escalate 文面を日本語化する経路は runtime に無い（`LocaleConfig` のフィールドは `constraints_default` / `report_target_default` / `claude_md_filename_default` / `instruction_template` の 4 つだけで、ja が渡す `tools/ja_locale.json` も同じ 4 キー。escalate 文面は locale の対象外）
   - **日本語の文脈を添えたい場合は、見出しを「別の `send_message`」として先に送る**。`escalate.message` を載せる `send_message` の本文には**前置きも後置きも足さない**（例: 1 通目に `SPLIT_CAPACITY_EXCEEDED: runtime の原文をそのまま転送します`、2 通目に `escalate.message` を単独で）。runtime は **1 文目が結果の prefix であり続けること**を保って診断を append しているので、同じ本文に 1 行足すだけでもこの prefix 性が壊れる。ペイロードに触らない限り日本語の補足は自由
   - この verbatim 規律は本ステップ（rect 候補ゼロ）だけでなく、**helper が `status="split_capacity_exceeded"` で返す全分岐に適用する**。0.1.39 以降 escalate 文面は 5 種類あり内容が異なる（renga 面 4 種: rect 候補ゼロ / タブ上限 `tab_limit_reached` / overflow 時の fleet ceiling 到達 / overflow しても新規タブが `MIN_PANE` 未満で助けにならない。broker 面 1 種: `max_concurrent_workers` 到達）。ja 側に literal を持つとこのうち 1 種の旧形しか表現できない
2. 併せて **`plan.layout`（診断）を窓口報告に添える**（`.dispatcher/CLAUDE.md`「出力の扱い」参照）。renga 面の `split_capacity_exceeded` では `layout` が実測診断オブジェクトになる
3. 3-2 以降（`spawn_pane` / 起動確認 / `list_peers` 待ち / instruction 送信）は **skip** する。該当ワーカー 1 件だけ派遣を中止し、ディスパッチャー本体の監視ループは **継続**させる。`exit` / `return` などでディスパッチャーを落とさないこと

#### 3-1d. 背景タブ配置 — dogfood 指定タスクに限った例外経路

**既定は同一タブ配置で、それが MUST である**（3-2 の「同一タブ内 spawn で起動する理由」。契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) §4.2「harnesses MUST launch every orchestrator-spawned pane in the same tab」）。本項が定めるのは、その MUST に対して契約 T-§4.2-place が **placement dogfood のためだけに**明示した狭い例外を、ディスパッチャーが**取り違えなく**発動するための機械的条件である。**通常委譲経路は 1 行も変わらない。**

**6 条件を全て満たしたときだけ背景タブへ置く。1 つでも欠けたら、判断を保留せず既定の同一タブ経路（3-1a 以降そのまま）に倒す（fail closed）**:

| # | 条件 | 確認方法 |
|---|---|---|
| 1 | **dogfood 台帳に earmark されている** | [`registry/dogfood_pending.md`](../../registry/dogfood_pending.md) の `dogfood_run_task_id` 列に、本タスクの `task_id` が**完全一致**で載っている行が**ちょうど 1 行**あり、その行の `status` が `open` である。前後空白を除いた文字列の完全一致で判定し、**部分一致・prefix 一致・`task_id` 列との照合で代用しない**（`task_id` 列は実装タスク側の id で、dogfood 実走タスクの id ではない — 台帳の列定義は [`.claude/skills/org-delegate/references/dogfood-protocol.md`](../../.claude/skills/org-delegate/references/dogfood-protocol.md) §register） |
| 2 | **transport が `renga`** | `ORG_TRANSPORT` の解決値が `renga`。**broker には `tab` セレクタも `new_tab` も無い**（surface 意図的除外。冒頭の輸送層注記）ので broker 面ではこの経路自体が存在しない |
| 3 | **この経路が使う capability を 3 つとも確認済み** | `mcp__renga-peers__server_info` を呼び、`status == "connected"` かつ `effective_capabilities` に **`spawn_tab`（`tab` セレクタ）・`caller_scope`（Group A = `inspect_pane` / `send_keys` を数値 id で他タブへ撃つ）・`cross_tab_peers`（全タブ `list_peers` 列挙と数値 id 宛 `send_message`）が 3 つとも**在ることを**事前に**確認する（`server.capabilities` ではなく `effective_capabilities` で gate する。`status` が `detached` / `unreachable` のときは両者が `null` = **不明**であって「無い」ではないので、条件 3 は**満たさない**扱いにする）。未確認のまま `tab` セレクタを撃たない |

> **3 つ全部を spawn 前に要求する理由**: capability トークンは**独立判定**で、一方から他方を導出できない（契約 T-§cap の independence rule、3-2 の「同一タブ内 spawn で起動する理由」bullet）。`spawn_tab` だけを見て spawn すると、**背景ペインは作れたのに直後の儀式が撃てない**状態が起こりうる — `caller_scope` を欠けば 3-3 の probe / 3-3b の承認 Enter / 3-5a の kickoff が `server_too_old` で fail closed になり、`cross_tab_peers` を欠けば 3-4b の全タブ列挙も 3-5 の数値 id 宛送信も成立しない。どちらの場合も**起動途中で放置された子ペインが残る**（`server_too_old` は非 transient なのでリトライで回復しない、契約 T-§6 clause (b)）。3 つを spawn 前にまとめて確認すれば、この経路は「全部できる」か「一切始めない」かの二択になる。
| 4 | **背景配置は 1 枚まで** | `.state/dispatcher/worker-idle-state.json` に `placement == "background_tab"` の record が **0 件**であること。1 件でも在れば既定の同一タブ経路に倒す |
| 5 | **人間監督下の bounded run である** | 窓口からの `DELEGATE` が当該 dogfood の実施を明示しており、実走中は人間が監督して結果を確認する前提が立っていること。立っていなければ窓口へ確認する（ディスパッチャーが自分で dogfood と判断しない） |
| 6 | **契約側の前提が実際に記録済みである** | **まず共通条件（どちらの経路でも省略できない）**: 契約 T-§4.2-place の運用準備条件 **(R1)–(R4) が 4 つとも充足と記録されている**こと。SoT は [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec の verdict で、本節が批准済みであることも同節の `Status:` 行で確認する。契約は production 経路にも dogfood 例外にも同じ「all four of (R1)–(R4)」を課しており（前者は T-§4.2-place の bar と T-§ratification-pc rung 3 の "Placement relief **additionally requires** all four operational-readiness conditions"、後者は同 place の例外本文）、**gate レコード 1 件でこれを代替してはならない** — gate レコードは phase 名 1 個だけを持ち capability / surface の欄を持たないので、「誰かが記録を書いた」以上のことは証拠しない（契約 T-§ratification-pc「gate records are keyed by a single opaque phase name and carry **no capability or surface field**」）。**そのうえで次の (a) か (b) のいずれか**が成立すること。共通条件を欠く場合、および (a)(b) どちらも成立しない場合は条件 6 を満たさない（fail closed）。**(a) production 経路 — 機械照会**: `python3 ../tools/capability_gate.py --gate placement_production_activation`（ディスパッチャーの cwd は `.dispatcher/` なので 1 段上に解決する。リポジトリ root からは `python3 tools/capability_gate.py --gate placement_production_activation`）が **exit 0 / stdout `recorded`** を返すこと（= 契約 T-§ratification-pc rung 3 の placement 半分が discharge 済み）。**exit 1 `not_recorded` と exit 2 `undetermined` はどちらも「未 discharge」で同じ分岐に倒す** — 根拠は契約 T-§cap の **fail-safe 既定**（確定できないものは不成立側に倒す）で、3 値の扱いは [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md) §2 の exit 表と同一（同表も `undetermined` を「ゲート未通過」に落とす）。ただし**両者は別の観測として報告する** — 再照会に意味があるのは `undetermined` のときだけ。**なお exit 2 は出所が 2 通りある**: gate 名がツールの choices に無いと argparse も exit 2 を返すので、stdout に `undetermined` が出ていれば本体・stderr に usage エラーが出ていれば argparse と切り分ける。後者は再照会しても直らない（古い checkout / タイポ）ので、先に `--help` の choices に名前が在ることを確かめる。**(b) dogfood 例外経路**: 契約 T-§4.2-place の例外が要求する残りの 2 条件（**bounded** かつ事前に dogfood と宣言・**人間監督下**）が成立すること。これは上表の条件 1 / 5 が担う。**(a) が `recorded` になっても本節の条件 1 / 4 / 5 は外れない** — 本節は見出しのとおり *dogfood 指定タスクに限った例外経路* であり、契約 T-§ratification-pc rung 3 が想定する「無人での常用配置」への緩和を与える経路ではない。(a) はここでは**前提条件の照会**であって production 経路そのものではないので、rung 3 が実際に discharge されたあとに無人・複数枚の配置を運用へ載せるなら、それは本節の条件を緩めるのではなく**別経路として設計する**（その設計は本節の範囲外）|

> **条件 6 の現状（2026-08-10 時点の記録された観測 — 恒久の事実ではないので、発動判定のたびに照会し直す）**: **共通条件が成立しない** — 契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec は **(R2) のみ充足**、**(R1) / (R3) / (R4) を未充足**として記録しており、両経路が要求する「all four of (R1)–(R4) already hold」を満たさない。**(a) の機械照会も 2026-08-10 時点で `not_recorded`（exit 1）** — `placement_production_activation` を `kind` に持つ `notify_sent` レコードは state DB に 1 件も無い。**したがって本節が定めるのは「記録が揃ったあとに何をどう実行するか」の手順であって、記録が揃う前の発動許可ではない。** 節レベル status は 2026-08-09 に批准済みになった（同契約の当該 amendment の `Status:` 行）ので、条件 6 がいま残している壁は **R 条件の側だけ**である。

> **ただし「背景配置は 1 度も起きていない」と読んではならない**: 2026-08-09 の人間批准は、段階 canary の代替として**背景タブ実走そのもの**を事前承認しており、その承認のもとで背景配置は既に複数回行われている（契約 T-§4.2-place-rec が記録する human-taken, pre-authorised deviation。**この 6 条件ゲートを通ったのではなく、人間が明示的に例外として承認した経路**である）。[`registry/dogfood_pending.md`](../../registry/dogfood_pending.md) には背景タブ配置の earmark 行が複数あり、それぞれ 1 枚ずつ起こされて順に退役している。**行数や status を本項の記述から読まず、判定のたびに実体を見ること** — 稼働中の背景 worker が居るかどうかは `.state/dispatcher/worker-idle-state.json` の `placement == "background_tab"` の record で決まり、それがそのまま条件 4 の判定である。**つまり今日この経路の実行数を縛っているのは条件 6 ではなく、上表の条件 4（背景配置は 1 枚まで）である。** 台帳と `worker-idle-state.json` は**運用中の実体（ja root 側）**を読むこと — worktree 内のコミット済みコピーは雛形のままなので、それを見て「`background_tab` の record は 0 件」と判定すると条件 4 を取り違える。

> **(a) が `recorded` に変わるのは、人間が dogfood 結果を確認して記録したときだけで、本経路の副作用としては書かれない**（契約 T-§ratification-pc の rung 3 は placement dogfood と人間確認を要件にしており、記録が人間の作業でスキルの自動副作用ではない点は messaging 側の production-activation について [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md) §5 が同じ規律を書いている）。

> **この経路は `delegate-plan` helper の `spawn` / `after_spawn[]` を使わない**: helper が返すのは rect ベース balanced split（3-1b）の同一タブ plan で、`tab` セレクタも `bound_pane_id` の束縛も生成しない。3-1d を通ったタスクではディスパッチャーが 3-2b / 3-4b を手順どおり実行し、helper の plan は**参照しない**（ja は `--tab` / `--overflow-to-new-tab` / `--server-capability` を helper に渡していないので、helper 側は今日この配置を知らない）。**3-1d に落ちなかったタスクの helper 経路は 1 行も変わらない。**

> **契約側の対応**: T-§4.2-place は placement relief に運用準備条件 R1–R4 を課したうえで、「a placement dogfood **MAY** place orchestrator-spawned panes in a background tab **when all of** the following hold — this section is ratified; all four of (R1)–(R4) already hold; the run is **bounded** and declared as a dogfood in advance; and a **human is supervising it and confirms the result**」と例外を 1 つだけ置く。上表の条件 1 / 5 がこの「declared in advance」「bounded」「human supervising」に、条件 2 / 3 / 4 が「実際にその経路が成立し、1 枚に閉じている」ことに対応する。**R2（背景配置の生存判定）は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) (3-a-5) の tracked-any-tab 判定で充足する。** R1 / R3 / R4 の充足状況は契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec に記録済みで、**3 つとも未充足**である — R1 は全タブ列挙が「既に握っている数値 id の確認」にとどまり契約の two-scopes 制限が解かれていないため、R3 は close 半分が背景ペインでは未配線であることに加え、配線済みの終了検知半分もまだ一度も成立していない（実走では backend 再起動で `poll_events` の cursor が無効化され、契約キーでの join ではなく T-§2.1 step (3) の「`list_peers` から消えた」経路で退役が確定した）ため、R4 は容量会計が caller タブ限定の `list_panes` から導かれており全タブ census が配線されていないためである。placement gate 名 `placement_production_activation` は `tools/capability_gate.py` の `GATES` に載っており、条件 6 (a) として機械照会できる。**R4 が未充足であるあいだ、上表の条件 4（背景配置は 1 枚まで）がその安全弁である** — 全タブ容量会計が無いまま背景ペインを 2 枚以上置くと、空き枠の過大申告がそのまま spawn を増やす方向に効くため（契約 T-§4.2-place の (R4)）。1 枚に閉じているあいだは数え落としが最大 1 枚に有界になる。

> **R3 の *close* 半分は本タスクでは配線しない（背景 worker の退役は人間監督者が行う）**: R3 は「An exit MUST be establishable … **and the close itself MUST satisfy** T-§4.2-id and the Group-B obligations」の 2 つを求めており、本タスクが満たすのは前半（終了検知）だけである。後半が未配線なのは、通常の `CLOSE_PANE` 手順 [`.dispatcher/references/pane-close.md`](pane-close.md) が close 直前の identity 照合を **caller タブに閉じた `list_panes`** で行う設計だからで、背景ペインはこの照合を**構造的に通過できない**（`list_panes` に出ない）。**したがって背景 worker に対する `CLOSE_PANE` は、ディスパッチャーが `close_pane` を撃たずに窓口へ escalate し、人間監督者が閉じる。** これは 3-1d 条件 5（bounded・人間監督下）と整合する終端であり、`close_pane` は不可逆なので**照合できないまま撃つより保持して上げるほうが安全側**である（契約 T-§4.2-id の (O3)/(O4)、T-§2.1 の indeterminate 規律と同じ向き）。**Group-B の背景 close 経路（session-safe な identity 照合を伴う `close_pane`）は本項でも配線しない。** 契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec は R3 を「終了検知半分は配線済み・close 半分は未配線」と記録しており、close 半分を閉じるのは**別の後続作業**である（同記録の (R3) 節が、その作業に必要なもの — caller タブ列挙に依存しない事前 identity 照合 — を明示している）。 監視側は人間が閉じた時点で `pane_exited` を join して通常どおり退役を確定する（(3-a-5)）— **ただしこれは設計上の経路であって、実走で成立したことはまだ無い**。1 枚目の実走では renga の再起動で `poll_events` の cursor が無効化され、`pane_exited` は exactly-once で replay が無いため join 自体が撃てず、退役は契約 T-§2.1 step (3) の「`list_peers` から消えた」経路（窓口の reconcile）で確定した（契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2-place-rec の (R3)）。**したがって検知経路には fallback があるが、契約キーでの join が成立する保証はまだ測れていない。**

### 3-2. ワーカーペインを起動する

3-1 で算出した `$target` / `$direction` を使って `mcp__renga-peers__spawn_claude_pane` を呼ぶ。**`$target` が空なら spawn せず 3-1c の escalate 手順に従う**:

```
mcp__renga-peers__spawn_claude_pane(
  target=$target,                         # 3-1 で算出した既存ペイン名
  direction=$direction,                   # "vertical" or "horizontal"
  role="worker",
  name="worker-{task_id}",                # 後続操作で参照する安定名。英字含む前提
  cwd="{workers_dir}/{task_id}",          # 絶対パス推奨。相対は caller pane の cwd 基点
  permission_mode="auto",
  model="opus"                            # 既定 opus（品質優先）。oracle が強い帯は窓口の明示指定で Sonnet 5 メイン可
)
```

- **既定は `model="opus"`（品質優先）。ただし oracle が強いタスクは、窓口が明示指定したときに限り `model="sonnet"`（Sonnet 5 メイン）を選んでよい。** 判定軸は工数ラベル (S/M/L) ではなく**タスクの oracle 強度と判断密度**で、これは ultracode 許可基準と同じ思想である。
  - **Sonnet 5 メインを選んでよい帯**: 次の 2 条件を**両方**満たすこと。(a) 機械検証可能な正解が実装の外に固定されている（fixture 駆動の期待値・テストスイートの assertion・DB の行など）、(b) 設計判断が brief で事前固定済みで、実装中に決め直す判断が残らない。この帯は誤りを oracle が機械的に捕捉し、実装中の判断密度が低い。
  - **opus を維持する帯**: (a)(b) のどちらかを欠くもの。機構の再導出が要る（retarget 型）/ 契約設計を含む / 実測しないと決められない決定が実装中に残る帯が該当し、既定の品質優先を崩さない。
  - **指定の受け取り方**: 窓口は既定を外すときだけ DELEGATE 本文に `モデル: sonnet` の 1 行を載せる。ディスパッチャーはこの行があるときに限り `model="sonnet"` で spawn し、**行が無ければ `model="opus"`** で spawn する（`モデル:` 行は `tools/gen_delegate_payload.py` の生成物には含まれず、窓口が手で足す逸脱指定である）。`sonnet` 以外の値は自己解釈せず窓口へ差し戻す。
  - モデル選択は承認フローの安全性とは独立である。permission_mode `auto` の safety classifier はワーカーのセッションモデルとは別の専用モデルで動作し、承認判定はワーカーが opus か sonnet かに依存しない（公式: https://www.anthropic.com/engineering/claude-code-auto-mode）。したがってモデル選定は品質判断だけで行う。
  - ディスパッチャーだけは `bypassPermissions` 固定で分類器を経由しないため、従来どおりコスト最適化として sonnet 運用（この判断はワーカーには自動適用しない）
- ペイン配置ルールは [`.claude/skills/org-delegate/references/pane-layout.md`](../../.claude/skills/org-delegate/references/pane-layout.md) を参照
- **同一タブ内 spawn を既定にする理由は「pane 制御が別タブへ届かないから」ではなく、「列挙 (`list_panes`) が別タブを映さないから」**（結論＝同一タブ配置は契約 T-§4.2 の「Retained from ratified §4.2」で retained だが、その**理由は 2026-08-09 改稿で差し替わっている**: 旧来の「pane 制御はどの capability 組み合わせでも one tab wide」は誤りで、契約自身が T-§sup correction 4 として撤回している）。根拠は renga の版数ではなく **capability を確立できたか**で読む（各トークンは独立判定 = 契約 T-§cap の independence rule）。**到達性 (reachability) と配置 (placement) は別軸で、「届くこと」は別タブ配置の licence にならない (MUST NOT)**（契約 T-§4.2-place「Numeric-id reachability does not discharge the placement rule」）:
  - `caller_scope`（Group A: `list_panes` / `focus_pane` / `inspect_pane` / `send_keys` / 各 `spawn_*` の `target`）と `caller_scope_close_identity`（Group B: `close_pane` / `set_pane_identity`）は**個別に判定するトークンで、一方から他方を導出しない**。そして**どちらの群も、scope は capability の組み合わせではなく selector 種別で割れる**（契約 T-§4.2 の Group-A / Group-B bullet）:
    - **相対セレクタ（`"focused"` / 裸の name）は one tab wide**。確立できた側だけ解決先が **caller のタブ**（フォーカス非依存）になり、確立できていない側は**フォーカス中**のタブに解決する（後者では列挙・`pane_not_found` を「自タブの事実」と読んではならない）。**ただし Group B の pre-capability legacy 経路だけは例外で one tab wide ですらない** — 裸の name が active タブ → 他タブへフォールスルーする（下の 4 つ目の bullet と `close_pane` / `set_pane_identity` の MUST を参照。契約 T-§4.2-sel の分岐表）
    - **数値 pane id はタブを越える**（caller marker `from_pane` を載せる capability 世代の client の場合。`Id` は全 workspace を検索する — renga `src/app/layout_ops.rs:703-731`、契約 T-§4.2 Group-A bullet が引用）。したがって `new_tab` で別タブに置いた worker でも、**`inspect_pane` / `send_keys` は数値 id なら wire レベルでは届く**。「別タブだから観測・操作できなくなる」は事実として誤りなので、その理由付けで同一タブ配置を説明しない
    - **本当に届かないのは列挙面**: `list_panes` は `target` を取らず（renga `src/mcp_peer/mod.rs:518-521`）、契約 §1.5 の「**Visibility scope**: current tab only. Panes in other tabs MUST NOT appear.」が **verbatim かつ無条件**で維持される（契約 T-§4.2 の `list_panes` bullet）。別タブの worker は**生きていても列挙に現れない**ため、この列挙から導く監視母集団・空き枠会計・退役 join がまとめて欠落し、**不在を exit と読む監視ループが生きた worker を退役させる**（契約 T-§4.2-place「absent from the enumeration while alive」）。これが同一タブ配置が防いでいる失敗である
    - **規範面（運用で数値 id 越境に寄りかからない理由）**: cross-tab pane control は 2026-08-09 に人間が批准済みで、**批准が解消したのは「契約と実装が食い違っている」という divergence の側だけ**である — 数値 id が越境することの**意味**が契約上確定した、というのが批准の全部（契約 [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) の当該 amendment、`Status: RATIFIED 2026-08-09`）。**「何も licence しない」ほうは 1 ミリも変わっていない**: 同 `Status:` 行が批准は "licenses no operation by itself" と明記している。活性化を握るのは別立ての契約 T-§ratification-pc で、rung 2（hazard class ごとの canary）と rung 3（production activation）を別々に課しており、`tools/capability_gate.py` で照会すると `pane_control_canary_a_ro` / `_a_mut` / `_a_ui` / `pane_control_canary_b` / `pane_control_production_activation` はいずれも `not_recorded`（= 未 clear）を返す。**配置（placement）は addressing 側の批准では一切緩まない**（契約 T-§4.2-place。(R1) / (R3) / (R4) が未充足 — T-§4.2-place-rec）。よって「wire では届く」ことを根拠に**既定の配置方針を変えない**（背景配置は 3-1d の 6 条件を通った経路か、人間が明示的に承認した dogfood 逸脱に限る）
    - **退役に使う `close_pane` は Group B なので「届かない」では済まない**: 下記 legacy 解決の経路では裸の name が別タブの同名ペインに当たりうる（別タブを撃つ側に倒れる）
  - `cross_tab_peers` を確立した経路では messaging もタブ横断（`list_peers` 全タブ列挙・数値 id 宛 `send_message`）になるが、契約 T-§4.2 は「messaging 到達性から pane 制御到達性を推論するな」を **MUST** で禁じている。別タブの worker は**指示は届くが `list_panes` には現れない**（= 監視母集団から落ちる）状態になる。**`cross_tab_peers` を確立できたことから pane 制御が届くと導出してはならない**: 数値 id の越境は `caller_scope`（Group B は `caller_scope_close_identity`）の確立**かつ** caller marker を載せる client 世代という別条件に懸かっており、gating client なら `server_too_old` で拒否され、pre-capability client の **Group A** なら数値 id も active タブ内に限定される。**Group B（`close_pane` / `set_pane_identity`）は逆で、legacy 経路の数値 id は別 resolver が全 workspace を検索するためタブを越えて成功しうる**（契約 T-§4.2-sel「Group B, numeric id, request without the caller marker」: "A numeric id is not a containment measure on this path" — 数値形は封じ込めにならず、identity binding が要る）
  - いずれの capability も確立できない経路（renga 1.x を含む pre-capability backend）では pane 制御が**フォーカス中**のタブに向くため、フォーカス移動だけで監視・指示送信が不能になる（2026-04-20 判明の 1.x 系の歴史的根拠。renga 本体側の対応は suisya-systems/renga#71）
  - **どちらの selector 種別でも、タブ切替は `pane_not_found` の復旧手順にならない**。確立できたトークン側の**相対セレクタ**は解決タブが caller の所有タブに固定されるため切り替えても変わらず、**数値 id** の `pane_not_found` は **caller marker を載せる request に限り**「対象が別タブに居る」ではなく「その id が現 backend session に解決しない」を意味する（契約 T-§6「`pane_not_found` on pane-control calls」: "a numeric-id `pane_not_found` means the id does not resolve in the backend session — it does not mean 'the target is in another tab'"。同節はこの code に対してタブ切替 / focus で応じることを **MUST NOT** としている）。この読みは下記 session provenance（daemon restart で id が再採番される）を疑う信号になる。**caller marker を載せない pre-capability client の Group A では読みが変わる** — legacy 経路は数値 id も active タブ内でしか解決しないので、対象が別タブに居るだけで同じ code が返る（契約 T-§4.2 Group-A bullet の `from_pane` absent 分岐）。**Group B の legacy 経路は逆に全タブを検索するので、この code の不在を「対象が自タブに居る」証拠にしてはならない**（成功してしまう側に倒れる）。それでもタブ切替は復旧手順にせず、client 世代 / capability の未確立として窓口へ報告する
  - **確立できていない側は client 世代で挙動が割れる**（「未確立 = 従来どおりフォーカス依存」と一括りにしない）。capability ゲートを実装した client は要求を送る前に `server_too_old` で **fail closed** にする — **非 transient なので、リトライもタブ切替もせず daemon の upgrade / re-probe に回して窓口へ escalate する**（契約 T-§6 clause (b)）。gate 以前の client だけが legacy 解決に落ち、そこでは**ユーザーが見ているタブ**基準になる。**Group B の legacy 解決は one tab wide ですらない**: 裸の name は active タブ → 他タブを index 順に探して**先勝ち**でフォールスルーするため、別組織の同名ペイン（`worker-*` は構造的に衝突する）を rename / close しうる（`close_pane` は不可逆）。**したがって `close_pane` / `set_pane_identity` は相対セレクタ（`"focused"` / 裸の name）で撃ってはならず（MUST NOT）、自タブと確認済みの列挙から採った数値 pane id で当てる（MUST）**（契約 T-§4.2「Fail-safe consequence for Group B」。唯一の例外は同契約が transport 条件付きで allowlist する stale-binding 復旧経路であり、ワーカーペインの spawn / 退役はこれに当たらない）。分岐手順は [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) の `pane_not_found` / `split_refused` 行、契約は [`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2
- `name="worker-{task_id}"`: 後続の `mcp__renga-peers__send_message(to_id="worker-{task_id}", ...)` で addressable にする安定名で、**用途は messaging に限る**。ペインの退役（`close_pane`）と identity 更新（`set_pane_identity`）はこの name では撃たず、`mcp__renga-peers__list_panes` の列挙から採った数値 pane id で当てる（上記 bullet の MUST。契約 T-§4.2 Group B）。**全桁数字は id 扱いになる** ので、`worker-` プレフィックス等で英字を必ず含める
- `role="worker"`: `list_panes` の結果で役割識別（次回以降の balanced split の target 選出にも使われる）
- `cwd` / `permission_mode` / `model` / `args[]` は `spawn_claude_pane` の構造化フィールド。renga が `claude --permission-mode {mode} --dangerously-load-development-channels server:renga-peers ...` を合成する。旧方式（`cd`-プレフィックス付き command 文字列を `spawn_pane` に渡す）は **禁止**
- **`args[]` は通常空（省略）にする**。`args[]` は Claude Code CLI の実フラグ（例: `--resume`, `--continue`）のみが渡せる。DELEGATE メッセージや worker brief 本文に `--skip-settings` / `--no-foo` のような **flag-like text** が現れても、それは窓口側ツール（`gen_delegate_payload.py` の `--skip-settings` 等）のコンテキスト情報や作業の説明であり、`spawn_claude_pane` の `args[]` に直訳してはならない。直訳すると Claude Code が `error: unknown option '--xxx'` で即時 exit し、ペインが起動直後に閉じる（実例: 2026-05-09 sandbox-probe-iter-b-round-3 で `--skip-settings` を args に渡して pane id=11 が即時退役 — `knowledge/raw/2026-05-09-delegation-skip-settings-wrong-cli-arg.md`）。worker 用 settings 等の準備状態は窓口の `apply` 段階で完了済みなので、ワーカー起動側で追加 flag を載せる必要はない
- 起動コマンドの仕様は `.claude/skills/org-start/SKILL.md` の「ClaudeCode 起動コマンド（役割別）」セクションを参照
- `spawn_claude_pane` が内部で `--dangerously-load-development-channels` を付与するため、`Load development channel?` 確認プロンプトが初回表示される。3-3b で `send_keys(enter=true)` による承認が必要
- **エラーハンドリング**: MCP 結果テキストに `[<code>] <msg>` 形式でエラーが埋まる。主な code:
  - `[split_refused]` (MAX_PANES / too small): [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) の手順に従いキュレーター → 窓口に escalate
  - `[pane_not_found]`: `$target` に選んだ既存ペインが spawn 発行直前に閉じたレース。同じくエラーコード経路で escalate
  - `[cwd_invalid]`: 指定した cwd が存在しない / ディレクトリでない。窓口に escalate し、ワーカーディレクトリ準備（`.claude/skills/org-delegate/SKILL.md` Step 0.7 / 1 / 1.5 / 2）が完了しているか確認
  - `[invalid-params]`: `args[]` に `--permission-mode` / `--model` / `--dangerously-load-development-channels` を含めた場合の拒否。構造化フィールドで渡す。同じく brief 本文中の flag-like text を args[] に転記して unknown option を起こす経路も避ける（上の args[] ルール参照）
  - その他の code は同 reference 参照

#### 3-2b. 背景タブ spawn と id 束縛（3-1d の 6 条件を全て満たしたときだけ）

3-1d を通ったタスクでは、`target` / `direction`（= 3-1 の rect ベース balanced split）を**使わず** `tab` セレクタで背景タブへ置く。`{"new": {...}}` は**フォーカスを移さずに**新規タブを 1 枚起こすので、人間の見ているタブは変わらない:

```
# 3-1d 条件 3 で呼んだ server_info の server.pid を控えておく（下記の session provenance 用）
si = server_info()
bound_server_pid      = si.server.pid
bound_server_endpoint = si.server.endpoint   # pid だけでは restart 一意にならないので対で控える

res = mcp__renga-peers__spawn_claude_pane(
  tab={"new": {"name": "worker-{task_id}"}},   # 背景タブを新規作成。target / direction は省略する
  role="worker",
  name="worker-{task_id}",
  cwd="{workers_dir}/{task_id}",
  permission_mode="auto",
  model="opus"
)
bound_pane_id = res の数値 pane id      # ★ 直後に控える。以降の儀式は全てこの id 宛

# ★★ ここで即座に永続化する（Step 4 まで持ち越さない。下記「spawn 直後に書く」）
#     .state/dispatcher/worker-idle-state.json の worker-{task_id} record へ merge:
#       placement="background_tab" / bound_pane_id / bound_server_pid /
#       bound_server_endpoint / bound_cwd="{workers_dir}/{task_id}"
#     bound_peer_id はまだ書かない（3-4b の受理後に merge する）
```

> **spawn 直後に書く — 記録より先にペインを作らない (MUST)**: 背景ペインは `list_panes` に出ないので、**記録が無い背景ペインは以後どの経路からも発見できない**。永続化を Step 4 まで遅らせると、その間 (3-3 の起動確認 / 3-3b の承認 / 3-4b の最大 30 秒の登録待ち) に dispatcher がクラッシュしたり handover を跨いだりした場合、**すでに起動して作業を始めうる子ペインが完全に untracked のまま残る** — 同一タブ worker なら `list_panes` の突き合わせ (Step 3) が拾うが、背景ペインにはその保険が無い。したがって `bound_peer_id` 以外の 5 フィールドは **spawn 戻り値を得た直後に書き**、`bound_peer_id` だけを 3-4b の受理後に merge する（Step 4 の該当項）。
>
> **3-4b が受理できずに派遣を取り止めた場合も、この record は消さない**: ペインは実在するので、記録を消すと上記の untracked 状態を自分で作ることになる。`bound_peer_id` が `null` のまま残った record の扱いは [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) (3-a-5) の三値表 1 行目（registration-timeout の着地）が定める。

> **数値 pane id は backend session に閉じた識別子なので、session を必ず一緒に控える（MUST）**: 契約 T-§4.2-id は「the id counter is initialised unconditionally in the constructor … Nothing persists or restores it … **across a daemon restart they are reissued from the beginning**」と書き、restart を跨いで使った id について「is not addressing a stale pane — it is addressing **a different, live pane**, and the backend answers **successfully**. No error code marks this」と続ける。同節の proof obligation **(O1) Session provenance** は「the id was issued by, and still belongs to, the backend session **currently serving the call**」の確立を MUST にしており、**(O4)** は確立できないときの相対セレクタ / 裸 name への降格を MUST NOT にしている。したがって `bound_pane_id` は単独では使えず、**同じ record に `bound_server_pid` を並べて控え、使用前に毎回照合する**（照合手順は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) (3-a-5) の「session provenance を毎サイクル確かめる」）。

**戻り値の数値 pane id を直後に控え、以降の儀式を全てその id に固定する（MUST）**。控えは `.state/dispatcher/worker-idle-state.json` の当該 record の **`bound_pane_id`**（Step 4 の該当項で書く。schema は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) (3-a-3)）で、**peer id とは別フィールド**に持つ。理由は 2 つある:

- **name 解決が背景タブへ届かない**: 相対セレクタ（`"focused"` / 裸の name）は caller のタブに解決するので、背景タブのペインには当たらない（契約 T-§4.2）。数値 pane id だけが cross-tab に届く address form である
- **並走 org の同名イベントと衝突する**: 予約名 `worker-{task_id}` は別タブに同名で実在しうるので、`pane_started` を name 単独で照合すると他 org の起動イベントで自分のゲートが開く（T-§2.2 / T-§3.1）

したがって以下を **`bound_pane_id` 宛に固定する**（同一タブ経路の name 宛は変更しない）:

| 儀式 | 同一タブ経路（不変） | 背景タブ経路 |
|---|---|---|
| 3-3 の `pane_started` 照合 | `ev.name == "worker-{task_id}"` | **`ev` の pane 識別子（canonical `ev.id` → alias `ev.pane_id`）が `bound_pane_id` に一致**。`name` は照合に使わない（表示用のみ） |
| 3-3b の承認 Enter | `send_keys(target="worker-{task_id}", enter=true)` | **`send_keys(target=<bound_pane_id>, enter=true)`** |
| 3-3b の表示確認 `inspect_pane` | `target="worker-{task_id}"` | **`target=<bound_pane_id>`** |
| 3-5a の ultracode kickoff `send_keys`（2 段とも） | `target="worker-{task_id}"` | **`target=<bound_pane_id>`**（text 段・Enter 段の両方） |
| 3-5 の指示 `send_message` | `to_id="worker-{task_id}"` | **`to_id=<bound_peer_id>`**（3-4b の登録ゲートが確定した数値 peer id。name 宛は自タブにしか解決しない） |

**エラー時**: `[server_too_old]` が返ったら（3-1d 条件 3 の事前確認をすり抜けた場合）**リトライもタブ切替もせず**、背景タブ配置を諦めて既定の同一タブ経路（3-1a から）に倒すか、窓口へ escalate する（契約 T-§6 clause (b) の fail closed）。その他の code は 3-2 のエラーハンドリングと同じ。

### 3-3. ペインが起動したことを確認

`mcp__renga-peers__poll_events` で `pane_started` イベントを最大 3 秒待つ。target 以外の worker の同時 spawn や filter 不一致イベント到着による early return に備え、**3 秒 deadline 内で再 poll するループ**として書く:

```
cursor = None                    # 初回は since 省略（「今以降のイベントだけ」セマンティクス）
deadline = now + 3 秒
while now < deadline:
    remaining_ms = (deadline - now) ミリ秒
    result = mcp__renga-peers__poll_events(
        since=cursor,                                  # 2 回目以降は前回の next_since
        timeout_ms=min(remaining_ms, 3000),
        types=["pane_started"]
    )
    cursor = result.next_since                          # 次呼び出しで使う
    for ev in result.events:
        if ev.name == "worker-{task_id}":
            return OK                                   # 起動確認完了
# deadline 超過 → 起動イベント未検出
# mcp__renga-peers__list_panes でペイン存在を再確認、未存在なら窓口にエスカレーション
```

- 初回 `since` 省略 = `renga events --timeout` と同じ「今以降」セマンティクス（過去の起動イベントを replay しない）
- `types=["pane_started"]` で他 type（`pane_exited` 等）を除外しつつ、cursor は全 type で advance（重複 scan なし）
- **filter 不一致イベントが到着すると long-poll が早期終了し `events:[]` + 進んだ cursor が返る**ので、空応答のままループ継続（cursor 保持で重複なし）
- `name == "worker-{task_id}"` の `pane_started` で break。deadline 超過で未検出なら `list_panes` で pane 存在を再確認
- **背景タブ経路 (3-2b) では `ev.name` ではなく `ev` の pane 識別子 (canonical `ev.id` → alias `ev.pane_id`) が `bound_pane_id` に一致したときに break する**。deadline 超過時の再確認は **`inspect_pane(target=<bound_pane_id>)` の pane 宛 probe** で行う (背景ペインは `list_panes` に構造的に出ないので `list_panes` は使えない)。**ここで `list_peers` を再確認に使ってはならない** — 3-3b の dev-channel 承認がまだなら peer 登録は原理的に成立しておらず (下記 3-3b「承認しないと … 3-4 の `list_peers` 待ちがタイムアウトし」)、**承認プロンプトで待っている生きたペインを「起動失敗」と誤判定して escalate する**。peer 登録の確認は承認後の 3-4b が owner である。probe が `[pane_not_found]` / `[pane_vanished]` 以外のコードで失敗した場合も起動失敗とは読まず、コードを添えて窓口へ escalate する

### 3-3b. 「Load development channel?」プロンプトを Enter で承認

`spawn_claude_pane` は内部で `--dangerously-load-development-channels server:renga-peers` を付与するため、初回起動で Y/n 確認プロンプトが出る。Enter で承認する:

```
mcp__renga-peers__send_keys(target="worker-{task_id}", enter=true)
```

承認しないと `server:renga-peers` チャネルが有効化されず、3-4 の `list_peers` 待ちがタイムアウトし、3-5 の `send_message` も届かない。Enter は CR (0x0D) として PTY に書き込まれる（byte-identical to renga `append_enter`）。

> **この Enter は痕跡を残さない**（PTY への生バイト書き込みで `.state/` に何も書かない）。**「撃ったつもり」を成立させないための ground truth は 3-4 の `list_peers` 登録**であり、その観測値を Step 5 のゲートに渡すまで `DELEGATE_COMPLETE` は出せない。2026-08-18 にここを飛ばしたまま「承認済み」と報告した事故が 2 件続いている（経緯は Step 5 冒頭）。

**背景タブ経路 (3-2b) では `target=<bound_pane_id>` で撃つ**（`send_keys` も表示確認の `inspect_pane` も。裸の name は caller のタブに解決するので背景ペインに当たらない）。ground truth は 3-4b の登録ゲートで、未登録なら Enter を再送する点は同じ。

> **broker（`ORG_TRANSPORT=broker`）の場合 — push 一次採用で承認は 2 段（folder-trust + dev-channel sidecar の再導入）**: `spawn_claude_pane` は `--mcp-config <broker>`（daemon）を注入し、初回に Claude Code の **folder-trust プロンプト**（「Do you trust the files in this folder?」相当）が出る。これを `mcp__org-broker__send_keys(target="worker-{task_id}", enter=true)` で機械承認する。**加えて push 一次のため**、`spawn_claude_pane` は channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load するため、**「Load development channel?」プロンプトが再出現**する（`--mcp-config`-only 設計で一旦消えた 3-3b 承認の **broker 枝での再導入**）。これも `mcp__org-broker__send_keys(target="worker-{task_id}", enter=true)` で機械承認する。両プロンプトの順序は boot タイミング依存なので、`inspect_pane` で各プロンプトの表示を確認しつつ順に Enter 承認する（renga と同じく未表示段階の Enter は no-op になりうるため、3-4 の `list_peers` 登録 poll を ground truth とし、未登録なら再送する）。承認しないと broker token のバインド／channel sidecar の登録が完了せず、`list_peers` 待ち・`send_message`（push 配送）・フォールバックの `check_messages` が成立しない。これは ratified §5/§8.5 の folder-trust フローへの **加算であり置換ではない**（設計 transport-lab `docs/design/broker-native-roles.md` §9.5。S3 で contract §5.1/§8.5 を amend 済み・2026-06-15 ratified）。

### 3-4. `mcp__renga-peers__list_peers` で新ピア出現を待機

**`list_peers` の直前に [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md) を Read し、`monitoring-read-only` の分岐を適用する**（同 reference §6 の表 #7）。ここは spawn のたびに走るので、**実運用で capability 広告列挙を最初に観測するのはこの経路である公算が高い**。

pane は live でも Claude がまだ起動中の場合があるため二重確認。`mcp__renga-peers__list_peers` を呼び、`worker-{task_id}` が peer 一覧に現れるまで短い間隔（例: 2 秒）でリトライする（最大 30 秒程度）。タイムアウトした場合は `list_panes` でペイン状態を再確認し、必要なら窓口に escalate する。

> **在を確定したレコードの `id` / `name` / `cwd` を 3 つとも控える（Step 5 のゲート入力）。** 控えは記憶や期待値ではなく列挙の実値であること。`cwd` は窓口が T1 で書いた `runs.worker_dir_id` と Step 5 で照合され、食い違えばゲートが exit 10 で `DELEGATE_COMPLETE` を止める。ここで登録が確認できないまま先へ進むと、3-3b の承認が実際には通っていない可能性が残ったまま報告に至る（2026-08-18 の 2 件がこの形）。

> **capability 形かつ未承認のときの縮退（停止しない・待ち時間 0 分）**: 列挙を peer 登録の ground truth にせず破棄する。**`worker-{task_id}` の name 一致で登録ゲートを開けてはならない** — 予約名は別 org の並走タブに同名で実在しうるので、name 一致は「まだ登録していない自分の子」のゲートを他 org のピアで開けてしまう（契約 T-§2.2 の「MUST NOT key a lookup … on `name` alone」。共有 reference §3-B-1）。
>
> **代替の readiness 判定は「送信そのもの」で行う（pane 生存 + プロンプト表示では足りない）**: ペインが live でプロンプトが出ていることは Claude が起動したことしか示さず、**MCP の peer 登録が済んだことを示さない**。3-4 は本来そこを二重確認する step なので、縮退時にこれを「boot 確認」で置き換えると、**登録前のワーカーに 3-5 の指示を撃って唯一のタスク割り当てを取りこぼす**。したがって縮退時は次のようにする:
>
> 1. `list_panes` の pane 生存を確認する（ペインが死んでいれば spawn 失敗として通常の失敗処理へ）。
> 2. **3-5 の `send_message` 自体を readiness probe として使う**。peer が未登録なら送信は `[pane_not_found]`（broker では `[peer_not_found]`）で失敗するので、**失敗は「まだ登録していない」の証拠として読める**（列挙と違い、この判定は他タブの同名ピアに汚染されない — 送信先は自分が spawn した `target` 名の解決結果であり、失敗コードは送達不成立そのものを表す）。
> 3. 失敗したら 2 秒間隔で最大 30 秒まで**再送**する（3-4 の元の poll 予算と同じ）。この間 `send_keys(enter=true)` の再送も従来どおり行う。
> 4. 30 秒を過ぎても送達できなければ、従来のタイムアウト処理と同じく `list_panes` でペイン状態を再確認し、窓口に escalate する。
> 5. **送達に成功した時点で 3-5 は消化済みである。3-5 に戻って同じ指示をもう一度送らないこと** — 縮退経路の probe は 3-5 の送信「そのもの」であって、別立ての試し送信ではない。二度送るとワーカーが同一タスクを 2 回受け取って二重実行する。送達成功をもって「起動・登録・指示送信」が同時に確定し、そのまま 3-6 以降（state 書き込み）へ進む。ultracode 武装 kickoff（3-5a）が要るタスクでは、その `send_keys` は送達成功の**後**に 1 回だけ行う。
>
> 報告は共有 reference §3-B の手順で窓口へ 1 度だけ上げ、監視は止めない。

**capability 形かつ承認済み（§2 の `first_drive` が `recorded`）のときは、共有 reference §1-2 の三値判定を `worker-{task_id}` に適用する。「在」のときだけ登録ゲートを開け、その 1 件の数値 `id` で 3-5 を送り（`to_id=<その数値 id>`。名前宛にしない）、同じ id を Step 4 の該当項で `worker-idle-state.json` へ控える。「不在」「unknown」はゲートを開けず、上記の poll をそのまま続ける。**（判定手順・評価順・宛先規則の正本は §1-2。ここに重ねて書かない）**旧版 fallback（現行配備の全 backend）と未承認縮退では従来どおり `to_id="worker-{task_id}"` の名前宛のままで、今日の挙動は変わらない。**

#### 3-4b. 背景タブ child の登録ゲート — `bound_pane_id` の完全一致だけで開ける

3-4 の `list_peers` 待ちは、背景 child では **共有 reference §1-2 を適用しない**。§1-2 は「対象名 N のピアが**自タブに**生存しているか」を判定する規範で、背景 child は定義上つねに `same_tab == False`（= §1-2-c #4 の**不在**）なので、当てるとゲートが永久に開かない（判定の owner が違う。詳細と根拠は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) (3-a-5)）。代わりに:

1. `mcp__renga-peers__list_peers` を 2 秒間隔でリトライする（**最大 30 秒。3-4 の元の poll 予算と同じで、新しい待ち合わせは足さない**）
2. 列挙のうち **数値 `id` が `bound_pane_id` と完全一致**するレコードが**ちょうど 1 件**あり、**かつそのレコードの `name` が `worker-{task_id}`・`role` が `worker`・`cwd` が spawn に渡した `{workers_dir}/{task_id}` と一致**することを受理条件にする。その `id` を `bound_peer_id` として控える（renga の `list_peers` は「always address a peer by its numeric pane id」と定めており、pane 面で得た id をそのまま引ける）
   - **`name` / `role` / `cwd` の照合を省かない理由（契約 T-§4.2-id の (O2) Identity binding）**: 同節は「verified — by an observation **independent of the id itself** — that the pane the id denotes is the pane intended, **bound to the expected organisation** and to the expected role or name」を MUST とし、「An id re-confirmed only against the record that supplied it discharges nothing: that is the same assertion made twice」と書く。id の等値だけでは spawn 戻り値を spawn 戻り値で確かめているだけになるので、**列挙側が持つ独立な属性と突き合わせる**ことで (O2) を discharge する
   - **`cwd` が org 束縛を担う（`name` / `role` だけでは足りない）**: 予約名 `worker-{task_id}` は並走する**別 org** のタブに同名で実在しうる（上記 bullet / 契約 T-§2.2「collide **by construction**」）ので、`name` + `role` の一致は「期待した役割のペインである」ことしか示さず「**自分の org のペインである**」ことを示さない。`list_peers` のレコードは `cwd` を持つ（契約 §1.5 / renga `list_peers` の tool 契約）ので、**spawn に渡した `{workers_dir}/{task_id}` の絶対パスとの一致**を org の弁別子に使う（別 org の同名 worker は別の worker ディレクトリで動いているため一致しない）。この値は `bound_cwd` として record に控え、監視サイクル側の再照合でも同じ 3 属性を使う
3. **`name` 一致・`same_tab` / `tab` 値のどちらも、単独ではゲートを開ける根拠にしない（MUST NOT）**。予約名は別タブに同名で実在し、タブ index は表示用でずれる。**ゲートを開ける主キーはあくまで `bound_pane_id` の等値**で、`name` / `role` はその上に重ねる (O2) の識別子照合である
4. 30 秒で受理できなければ、従来のタイムアウト処理と同じく状態を確認して窓口へ escalate する（**この経路は Step 4 に到達しないので、`bound_*` の陽性履歴はそもそも書かれない**）


### 3-5. ワーカーに指示を送信

[`.claude/skills/org-delegate/references/instruction-template.md`](../../.claude/skills/org-delegate/references/instruction-template.md) のフォーマットに従い、**使用中 transport の `send_message`**（既定 renga なら `mcp__renga-peers__send_message`、`ORG_TRANSPORT=broker` なら `mcp__org-broker__send_message`）で `to_id="worker-{task_id}"` 宛に送る。

#### 3-5a. ultracode 武装（worker brief に ultracode 許可がある場合のみ）

worker brief に **ultracode 使用許可**があるタスクでは、kickoff を **「使用中 transport の `send_keys`」によるペイン打鍵 user turn** として送り、その本文に standalone トークン `ultracode` を含める。**これを行わないと、brief に許可があっても worker は ultracode（multi-agent workflow / Workflow tool）を発動できない**（Issue #554 の defect 本体）。

> **transport 両系**: `send_keys` も冒頭の機械置換ルール対象。`ORG_TRANSPORT` 無設定なら `mcp__renga-peers__send_keys`、`ORG_TRANSPORT=broker` なら `mcp__org-broker__send_keys`。`send_keys` は配送方式ではなく PTY キーストロークなので、**武装ロジックは transport 非依存と考えてよい**（broker で実走武装確認済。renga は未実走だが、同じ PTY キーストローク経路で user turn 化するため同様に武装する想定）。

**許可の判定（dispatcher が行う）**: worker が読む行動規範ファイル（`claude_md_filename`。**既定 `CLAUDE.md`、claude-org 自己編集タスクは `CLAUDE.local.md`** — instruction-template の helper 変数参照）の「実装ガイダンス」/ implementation 指示に ultracode 使用許可が描画されているかを確認する（窓口の `gen_delegate_payload.py --impl-guidance "... ultracode の使用を許可する"` がこの箇所にレンダリングされる。dispatcher は `worker_dir` を持つので読める。brief ファイル名は `worker_dir` 内の実ファイルで判別する＝既定 `CLAUDE.md`、claude-org 自己編集タスクのみ `CLAUDE.local.md`。`claude_md_filename` は helper-rendered instruction の optional var であって spawn action plan には現れない）。これが常時参照可能な正準シグナル。task JSON 側に `implementation_guidance` が載る dispatch 経路ならそれを併用してよいが、`delegate-plan` helper はこのフィールドを消費しないため brief を一次シグナルとする。

**なぜ `send_keys` か（実走確定、Issue #554）**: ultracode の opt-in は worker セッションの **user turn 入力**に `ultracode` トークンが現れることを harness が検出して初めて武装される（武装時は worker 側に「opting this turn into multi-agent orchestration」の system-reminder が出る）。以下は **武装しない**ことが broker 実走で確認済み:
- brief ファイル（`CLAUDE.md` / `CLAUDE.local.md` としてロードされる context）内の keyword
- `send_message` 本文・in-band push・`check_messages`（tool result）経由で届く指示本文内の keyword

`send_keys` は本文をペインの PTY に**キーストロークとして書き込む**ため worker の user turn になる。`send_message` / `check_messages` は peer/channel メッセージ（injected message）であって user turn ではない。この武装は **body の生成方式に依存しない** — helper（`delegate-plan`）の `message_file` 経路でも、task JSON に `instruction` を直接指定した経路でも、武装は body ではなく send_keys user turn 側で成立するため共通でカバーされる。

**武装の scope（turn-scoped、実走確定）**: 武装は **user turn 単位**で検出・有効化される。Claude Code worker は「kickoff user turn への応答」の中で実装〜Codex 前セルフレビュー収束までを autonomous に行い、その間の internal な複数 LLM call / 並列 review / commit 確認は **同一 user turn 下の応答フェーズ**に含まれるため武装は失われない（本タスク自身の実走で、armed turn 内で多数の tool 呼び出しを経た後でも Workflow 起動に成功＝応答全体が武装される、を確認済）。ack / 追加指示など**別の user turn には武装は引き継がれない**（再検出が要る）。

**手順:**

1. **kickoff を send_keys の armed turn にする（race 回避、全 instruction 経路で成立させる）**: ultracode タスクでは、send_keys の `ultracode` kickoff を **唯一の actionable な着手トリガ**にし、3-5 の `send_message` を「worker が即座に本作業を始める kickoff」として使わない。詳細 brief は worker の行動規範ファイル（既定 `CLAUDE.md` / self-edit は `CLAUDE.local.md`、spawn 時ロード済）が全文を持つため、send_keys 行は短い 1 行で良く、worker はこれだけで着手できる。actionable kickoff を send_message 側にすると worker は**未武装の応答**を始め、turn-scoped ゆえに収束フェーズまで未武装のままになる。
   - **reference として send_message を送る場合**、worker が先に着手しないための待機文（「この peer message では着手せず ultracode send_keys kickoff を待つ」）が worker に届くかは **instruction 経路に依存する**ので注意する:
     - **AUTO 展開テンプレ経路**（helper-rendered。`instruction_vars` 指定）: 待機文は body の「作業の進め方」節に条件付きで埋め込み済みで worker に届く（[`.claude/skills/org-delegate/references/instruction-template.md`](../../.claude/skills/org-delegate/references/instruction-template.md) の「作業の進め方」節。冒頭の「ultracode タスクでの worker 着手規約」節は SoT 説明）。
     - **`instruction` 直指定経路**（AUTO テンプレを使わない backward-compat 経路）: body に待機文が入らない。この経路で reference の send_message を送るなら dispatcher が待機文を body 冒頭に **prepend** すること。
   - **最も単純で経路非依存な保険**は、ultracode タスクで **actionable な send_message を一切送らず send_keys を唯一のトリガにする**こと（brief が全文を持つため成立）。経路差を気にしたくない場合はこれを既定にする。

2. **send_keys は 2 段で打つ**（text と Enter を別呼び出しに分ける。同時送は draft 残りになりやすい — 既存の承認ハンドシェイク規律と同型）。`ultracode` は **語境界付きの単独トークン**として置く（行頭に単独で置くのを推奨。`ultracode-arming-fix` のような **slug 内 substring では武装しなかった**＝実走確認済。harness の検出方式の内部仕様には依存せず「語境界付き単独トークンなら武装／slug 内 substring なら非武装」という実走事実で運用する）。`send_keys` は **1 行**（埋め込み改行を入れない。生 `\n` は Claude Code 入力欄で途中 submit になる）:
   ```
   send_keys(target="worker-{task_id}",
     text="ultracode で本タスクに着手してください。詳細は worker brief の通り。まず pwd で作業ディレクトリを確認。",
     enter=false)
   # inspect_pane で text が入力欄に乗ったことを確認後、別呼び出しで Enter:
   send_keys(target="worker-{task_id}", enter=true)
   ```
   **背景タブ経路 (3-2b) では 2 段とも `target=<bound_pane_id>` に固定する**（確認用の `inspect_pane` も同じ id）。裸の name は caller のタブに解決するので背景ペインに当たらず、武装用の user turn がそもそも入らない。

3. **位置づけ（worker brief と一致させる）**: ultracode は実装と **Codex 前のセルフレビュー収束**（複数観点の並列レビューで指摘の種を潰し Codex 周回数を減らす）に使う。最終ゲート「Codex Blocker / Major ゼロ（別モデルによる独立レビュー）」は**従来どおり維持**する。ultracode は Codex ゲートの**前段**であって置き換えではない。

4. **段別モデル指定を促す（推奨）**: ultracode workflow は段ごとにモデルを使い分けられる。機械的なファンアウト段（定型置換・多数ファイルへの同一変換・単純収集など判断を伴わない並列作業）は `agent(..., {model: 'sonnet'})` で Sonnet 5 に振ってコスト・速度を最適化し、判断・検証・統合の段（レビュー・設計判断・adversarial verify・synthesis）はセッションモデル（既定 opus）を継承させて品質を確保する。worker brief（[`.claude/skills/org-delegate/SKILL.md`](../../.claude/skills/org-delegate/SKILL.md) の「重量レーンの brief 強化（ultracode）」）と同じ段別方針を kickoff / brief で促す。粒度は Workflow tool の `agent()` opts.model で制御する。

5. **強い権限の事前許可文言を workflow の agent プロンプトに書かせない（重要）**: ultracode を許可しても、`dangerouslyDisableSandbox` / `--dangerously-skip-permissions` 等の**強い権限を事前に許可する文言**を workflow の `agent()` プロンプト本文に書き込ませない（kickoff / brief でもそう促す）。安全分類器が該当 agent 呼び出しを **silent block** し、workflow が原因不明のまま停滞する事故になる。どうしても強い権限が要る段がある場合は、その agent の**成否を明示確認**（戻り値 / 失敗検知でブロックを検出）し、**block 時のリカバリ手段**（当該段を通常権限で再実行 / worker 本体で手動実行等）を workflow に組み込むよう促す。これは worker brief（[`.claude/skills/org-delegate/SKILL.md`](../../.claude/skills/org-delegate/SKILL.md) の「重量レーンの brief 強化（ultracode）」）と**同じ意味の制約**であり、両所に置くことで kickoff 導線から危険例が再発しないようにする。
   - **既存の args[] 規約とは別レイヤ**: 3-2 / 上記 `[invalid-params]` の「`spawn_claude_pane` の `args[]` に `--permission-mode` / `--dangerously-load-development-channels` 等の flag を入れない」は **pane 起動 CLI 引数**の話。本項は **worker が書く workflow script の `agent()` プロンプト本文**に強権限の事前許可文言を書かない話で、対象レイヤが異なる（混同しない）。

> **検証状況メモ**: 「send_keys の単独 `ultracode` トークンで武装」「armed turn の応答全体（多数 tool 呼び出し）にわたり武装継続」は本タスクで broker 実走確認済。手順 1 の「send_message をリファレンス配信し worker が send_keys turn で着手する」待ち合わせは、上記実走事実から導いた by-design の推奨手順（race を構造的に避ける）。最もシンプルな保険は **ultracode タスクで actionable な send_message kickoff を送らず、send_keys を唯一の着手トリガにする**こと（worker brief = 既定 `CLAUDE.md` / self-edit は `CLAUDE.local.md` が brief 全文を持つため成立する）。

**ultracode 許可が無いタスク**: 従来どおり 3-5 の `send_message` kickoff のみ（send_keys 武装は行わない）。

### 3-6. 複数ワーカーの順次起動

複数ワーカーがある場合は 3-1〜3-5 を順次繰り返す。`list_panes` の結果が毎回変わるので、**都度再取得して** balanced split 判定をし直す（前ワーカーの起動が完了するのを 3-3 / 3-4 で待ってから次に進むこと）。

## Step 4: 状態記録

> **delegate-plan helper との分担**: `.dispatcher/CLAUDE.md` の delegate-plan helper が `.state/workers/worker-{task_id}.md` を `Status: planned` で生成し、`worker-instruction.md` も書き出す（ready_to_spawn 時）。本 Step 4 はその後 dispatcher が **MCP 呼び出し成功後に行う** active 遷移 + DB 登録 + events 追記をまとめる。worker file テンプレートを再生成するのではなく、helper 出力に Progress Log を追記して Status を `active` に flip する。

各ワーカーについて:

1. helper が生成した `.state/workers/worker-{task_id}.md` の Status を `planned` → `active` に更新し、Progress Log に「派遣完了、作業開始」を追記する（renga-peers では pane name `worker-{task_id}` が安定識別子。旧 peer-id は使わない）。helper 未経由 / 構造化 DELEGATE が無いフォールバック経路の場合のみ、以下のテンプレートで新規作成する:
   ```markdown
   # Worker: worker-{task_id}
   Task: {task_id}
   Directory: {作業ディレクトリ}
   Pane ID: {pane_id}
   Started: {ISO timestamp}
   Status: active

   ## Assignment
   {タスクの説明}

   ## Progress Log
   - [{time}] 派遣完了、作業開始
   ```

   > **`Status:` 行は省略不可**（Refs #835）。runtime はこの行を overflow 予約台帳として機械的に読む（`_seed_status`、`claude_org_runtime/dispatcher/runner.py:1265-1286`）。行が無いと `None` が返り、`count_unbound_reservations` が mtime クロック（`WORKER_BIND_WINDOW_SECONDS = 45`、`runner.py:305`）へ**黙って**フォールバックするため、この Step 4 で既に active になった worker が最長 45 秒 pending 予約として枠を占有し、直後の overflow spawn が `split_capacity_exceeded` で不当に拒否されうる（`runner.py:1253-1260` / `runner.py:2796-2832`）。書式契約（bullet 前置き `- Status:` は**パースされない**等）は [`docs/contracts/state-schema-contract.md`](../../docs/contracts/state-schema-contract.md) §7、回帰検出は [`tests/test_worker_seed_status_contract.py`](../../tests/test_worker_seed_status_contract.py)。

2. **DB 経由で run と Active Work Items を登録する**（`.state/org-state.md` 直接編集は禁止。`StateWriter.transaction()` 経由、post-commit hook が再生成）:

   ```bash
   python3 -c "
   from pathlib import Path
   from tools.state_db import connect
   from tools.state_db.writer import StateWriter
   conn = connect('.state/state.db')
   with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
       w.register_worker_dir(abs_path='<worker_dir_abs>', layout='<flat|worktree|ephemeral>', is_worktree=<True|False>)
       w.upsert_run(task_id='<task_id>', project_slug='<slug>', pattern='<A|B|C>', title='<title>', status='in_use', worker_dir_abs_path='<worker_dir_abs>')
       w.update_session(objective='<人間の依頼>')
   "
   ```

   - free-form な「Current Objective の補足説明」は **`notes/` に保存する**（`notes/README.md` 参照）。markdown 直接編集は drift_check で検出される。`update_session(objective=...)` は構造化 header フィールドとして DB に書く
   - `pattern` は A / B / C のいずれか

3. DB の events テーブルにイベント追記（helper 経由。`ts` は自動付与。Bash で生 JSON を `>>` で append しないこと）:
   ```bash
   bash tools/journal_append.sh worker_spawned worker=worker-{task_id} dir=<dir> task={task_id}
   ```
   event 名と payload key の規約は [`docs/journal-events.md`](../../docs/journal-events.md) を参照。

4. JSON スナップショット (dashboard 用 `.state/org-state.json`) は **StateWriter の post-commit hook が自動再生成**する (Issue #284)。手動で `org_state_converter.py` を呼ぶ必要はない。

5. ワーカーペインを監視対象として登録する:
   - 派遣後、そのペインを監視対象として記録し、`.dispatcher/CLAUDE.md` の「ワーカーペイン監視」に従って定期的に承認待ちを確認する

6. **3-4（helper 経路ではその `after_spawn[]` の `list_peers` 待ち）で「在」を確定して控えた数値 `id` があるときだけ**、`.state/dispatcher/worker-idle-state.json` の当該 worker record に `same_tab_peer_id` / `same_tab_observed_at` を **merge する**（cwd は `.dispatcher/` なので `../.state/dispatcher/worker-idle-state.json`）。既存 record の他フィールドは読み書きせずそのまま残し、record がまだ無ければこの 2 フィールドだけを持つ record を新規に作る。これは同タブ生存の裏取り (ii) の **producer 2 本目**であり、schema・参照規則・消費側の正本は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 3 (3-a-3) である（ここで定めるのは「spawn 経路がいつ何を控えるか」だけ）。

   > **書いてよい id は 1 つだけ（MUST）**: 3-4 が **capability 形・§2 の `first_drive` が `recorded`・共有 reference §1-2 が「在」1 件と確定**した `list_peers` レコードの数値 `id`。**`spawn_claude_pane` の戻り値 pane id / 旧版 fallback の `name` 一致 poll で得た id / 縮退中の send-as-probe から推測した値は書いてはならない（MUST NOT）** — pane id は別空間の識別子であり、後 2 者は「自タブの当該 worker である」ことを確定していない。誤った陽性履歴を残すと、後続サイクルで `pane_exited` の attribution が別 worker に結び付き、**生きている worker を退役させうる**。
   >
   > `same_tab_observed_at` は「在」を確定した時刻を `date -u +%Y-%m-%dT%H:%M:%SZ` の出力そのままで書く（local-as-Z 厳禁。[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) 冒頭の時刻規約と同じ）。
   >
   > **控えが無いとき（旧版 fallback / 縮退中 / 判定が「不在」「unknown」）は 2 フィールドを書かない。** 欠損は `null` 扱いで migration 不要なので、`null` を明示的に書く必要も無い。現行配備の全 backend は旧版 fallback なので、**今日この producer は発火せず挙動は変わらない**。
   >
   > **spawn を放棄する場合の始末**: 3-4 のタイムアウト / 3-5 の送達不能で窓口へ escalate して派遣を取り止める経路は**本 Step 4 に到達しない**ので、そもそも陽性履歴は書かれない（＝この producer に「書いてから取り消す」窓は無い）。**本項を通した後は、record の削除規則は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 5 (b) 更新規則 (4) だけが持つ** — 本項は削除条件を足さない。とくに**「派遣後にタスクを取り止めた」ことを削除の根拠にしてはならない**: ペインがまだ生きているうちに record を消すと `tracked_pane_id` と `same_tab_peer_id` が失われ、以後 `pane_exited` の attribution も裏取り (ii) も成立しなくなって、当該 worker を恒久 indeterminate に固定する（規則 (4) が名指しで禁じている当の帰結）。ペインの退役は通常の退役経路を通し、削除はそこで規則 (4) が発火する。

7. **背景タブに置いた worker のときだけ** (3-1d の 6 条件を満たして 3-2b / 3-4b を通った場合)、`.state/dispatcher/worker-idle-state.json` の当該 record に **`bound_peer_id` (3-4b の登録ゲートが受理した数値 id) を merge する**。上記 6 と同じく既存 record の他フィールドは触らず、schema・参照規則・消費側の正本は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 3 (3-a-3) / (3-a-5) である。

   > **残り 5 フィールドは本項ではなく 3-2b が書く**: `placement="background_tab"` / `bound_pane_id` / `bound_server_pid` / `bound_server_endpoint` / `bound_cwd` は **spawn 戻り値を得た直後**に永続化済みである (3-2b の「spawn 直後に書く」)。本項に到達する頃には record は既に存在するので、ここでの操作は `bound_peer_id` 1 フィールドの merge だけになる。**この分割は必須である** — 5 フィールドを本項まで遅らせると、3-3 / 3-3b / 3-4b の間にクラッシュや handover が入った場合に、`list_panes` に出ない子ペインが記録なしで残る。

   > **`bound_pane_id` は `bound_server_pid` / `bound_server_endpoint` / `bound_cwd` と必ず一緒に書く (MUST。だからこの 4 つは 3-2b で 1 度に書く)**: pane id の一意性保証は backend session に閉じており、daemon restart 後の同じ数値は**別の生きたペイン**を指す (契約 T-§4.2-id)。session 側 2 つを欠くと (O1) session provenance を、`bound_cwd` を欠くと (O2) の org 束縛を **監視サイクル側が discharge できなくなる** — spawn ターンが終わった後・dispatcher handover を跨いだ後の監視は、この record に書いてある値しか手元に持たない。
   >
   > **同一タブ経路では 3-2b も本項も実行しない。** `placement` の欠損は `"same_tab"` 扱い (migration 不要) なので、通常委譲では 6 フィールドとも書かず、今日の record 形は変わらない。
   >
   > **`same_tab_peer_id` に背景 worker の id を書いてはならない (MUST NOT)**: あちらは「§1-2 が**自タブに**在と確定したレコードの id」という意味を持ち、(3-a-2) 4 行目の裏取り (ii) の入力になる。背景 worker の id を入れると同表が偽の裏取りで終了確定へ進み、**生きている worker を退役させうる**。背景 worker の id は `bound_peer_id` にだけ書く。
   >
   > **3-4b で受理できずに派遣を取り止めた場合、本項には到達しないので `bound_peer_id` は `null` のまま残る。ただし 3-2b が書いた 5 フィールドは残す (record を消さない)** — ペインは実在するので、消すと `list_panes` に出ない untracked なペインを自分で作ることになる。`bound_peer_id` が `null` の record の着地は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) (3-a-5) の三値表 1 行目が定める (registration-timeout として窓口へ上げ、画面監視は `bound_pane_id` で継続する)。**この点だけが上記 6 (同一タブの `same_tab_peer_id` producer) と異なる**: あちらは「放棄経路は状態記録に到達しないので陽性履歴が書かれない」が成立するが、背景配置では記録のほうが先に必要になる。record 削除規則は [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) Step 5 (b) 更新規則 (4) だけが持ち、本項も 3-2b も削除条件を足さない。

8. **本ファイル末尾の「Step 5: 派遣完了ゲート」へ進む。** 窓口への `DELEGATE_COMPLETE` は Step 5 のゲート出力だけが正本で、ここで自分で書いて送ってはならない。

   > 以下の「Worker Directory Registry」は Step 4 の DB 書き込みが生成するセクションの**定義（参照用の付録）**であって、手順の続きではない。かつて派遣完了報告がこの付録の**後ろ**に番号 `5.` で置かれており（本 Step 4 の項番 5 と重複していた）、手順を上から追う読み手が報告ステップに辿り着けない配置になっていた。番号を継いだ本項でその落差を塞いでいる。

### Worker Directory Registry（DB 由来のセクション定義）

> **M2.1 cutover (Issue #272)**: このセクションは DB の `worker_dirs` × `runs` から
> snapshotter が自動生成する。窓口・ディスパッチャーは `register_worker_dir(...)` /
> `update_worker_dir_lifecycle(...)` / `upsert_run(...)` を `transaction()` 経由で呼ぶ。
> markdown を直接編集するのは厳禁（次の post-commit 再生成で消える）。

`.state/org-state.md` の以下のセクションは DB 由来。ワーカーディレクトリの再利用状態を追跡する。

```markdown
## Worker Directory Registry

| Task ID | Pattern | Directory | Project | Status |
|---|---|---|---|---|
| blog-redesign | A | /path/to/workers/blog/ | blog | in_use |
| blog-auth-fix | B | /path/to/workers/blog/.worktrees/blog-auth-fix/ | blog | in_use |
| data-analysis | C | /path/to/workers/data-analysis/ | - | in_use |
```

**フィールド説明:**
- **Task ID**: 現在そのディレクトリを使用しているタスクID
- **Pattern**: A（プロジェクトディレクトリ）/ B（worktree）/ C（エフェメラル）
- **Directory**: ワーカーディレクトリの絶対パス
- **Project**: registry/projects.md の通称（エフェメラルで無関係なら `-`）
- **Status**: `in_use`（作業中）/ `available`（完了済み・再利用可能）

**運用ルール:**
- 窓口の `gen_delegate_payload.py apply` でディレクトリ準備時にエントリを追加する
- 窓口の `.claude/skills/org-pull-request/SKILL.md` 2b-ii でクローズ条件（PR マージ / 明示クローズ指示 / 長期 idle 判断）を満たした時にステータス更新・エントリ削除を行う（2b-i の PR 作成段階では触らない）
- `gen_delegate_payload.py` の Pattern 判定フローでこのテーブルを参照し、再利用可能なディレクトリや並行作業の有無を判定する

## Step 5: 派遣完了ゲート — `DELEGATE_COMPLETE` は自分で書かない

> **この節の由来（2026-08-18）**: 同日に 2 件連続で、ディスパッチャーが 3-3b の承認 Enter・3-4 の `list_peers` 登録確認・3-5 の指示送信を**実行しないまま**「承認済み・peer 登録確認済み・指示送信済み」と窓口へ報告した（`cert-questions-ingest-20260818` / `interlock-founding-docs-20260818`。どちらも実際にはペインが承認プロンプトで停止し入力欄は空で、窓口が `inspect_pane` で実見して手で復旧した）。
>
> **手順は当時も正しく書かれていた。** 壊れていたのは検証の側である。3-3b / 3-4 / 3-5 は**どれも痕跡を残さない**（`send_keys` は PTY への生バイト、`list_peers` は読み取り、`send_message` は MCP 呼び出しで、いずれも `.state/` に何も書かない）。Step 3 + Step 4 を通して残る唯一の行は `worker_spawned` 1 件で、これは [`docs/journal-events.md`](../../docs/journal-events.md) の Worker lifecycle 表が "After MCP `spawn_pane`." と定めるとおり**儀式が始まる前**に打たれる。実際、2 件とも state.db に残ったのは `delegate_sent` → `worker_spawned` だけで、正しく派遣した回と**バイト単位で同じ痕跡**だった。つまり儀式を省くほうが実行するより安く、しかも露見しなかった。本 Step はその 2 つを同時に潰す。

**`DELEGATE_COMPLETE` の本文を自分で組み立ててはならない（MUST NOT）。** 下記ゲートを通し、その stdout の `delegate_complete` を**そのまま** `send_message` の本文にする:

```bash
# ディスパッチャーの cwd は .dispatcher/ なので 1 段上に解決する
python3 ../tools/spawn_gate.py verify \
  --task {task_id} \
  --pane-id <spawn_claude_pane の戻り値の数値 pane id> \
  --peer-id <3-4 の list_peers で実際に観測した数値 id> \
  --peer-name <同じレコードの name> \
  --peer-cwd <同じレコードの cwd> \
  --approval {sent|not_shown} \
  --instruction {send_message|send_keys|both}
  # 背景タブ経路 (3-1d/3-2b) を通った場合のみ --placement background_tab を足す
  # (--peer-id == --pane-id の完全一致を要求する。3-4b の受理条件と同じ)
  # 3-4 の縮退経路を通った場合は --evidence send_delivery（--peer-* は渡さない）
```

- **exit 0** → stdout の `delegate_complete` を `mcp__renga-peers__send_message(to_id="secretary", ...)` でそのまま送る（前置き・後置きを足さない）
- **exit 10** → **報告しない。** `failures[]` / `remedy[]` が戻るべきステップを名指しするので、3-3b の承認 Enter 再送 → 3-4 の登録 poll へ戻り、通ってから再実行する
- **exit 2** → 窓口へエラー通知（ゲート自体が壊れている。派遣は止めるが監視ループは継続する）

**`--peer-*` の 3 つは 3-4 の `list_peers` で自分が実際に見たレコードの値を書く。** 期待値・記憶・spawn 引数の再掲で埋めない。`--peer-cwd` は窓口の `gen_delegate_payload.py apply` が T1 で書いた **`delegate_sent` イベントの `dir`** と照合され、食い違えば exit 10 になる。**照合の基準に `runs.worker_dir_id` ではなく `delegate_sent` を使うのは意図的である** — Step 4 項番 2 の `upsert_run(..., worker_dir_abs_path=...)` は `runs.worker_dir_id` を**上書きしうる**（つまりディスパッチャー自身が本ゲートの直前に動かせる値なので、独立照合にならない）のに対し、events テーブルは append-only で書き換えられない。両者が食い違う場合は `worker_dir_divergence` として exit 10 になる（どちらが誤りであれ独立基準が失われているため）。これは 3-4b が背景タブ経路に課しているのと同じ org 束縛の弁別（契約 T-§4.2-id (O2)「id 自身とは独立な観測」）を、同一タブ経路にも最小コストで効かせるものである。

**3-4 の縮退経路（capability 形かつ未承認）を通った場合は `--evidence send_delivery` を使う。** 縮退では `list_peers` の列挙を ground truth にせず破棄し、`send_message` の送達成功そのものを readiness probe にする（3-4 の縮退注記 2〜5）ので、引用できる peer レコードが存在しない。このモードでは `--peer-*` を**渡してはならない**（渡すと `evidence_mismatch` で exit 10。破棄したはずの列挙が弱いモードの名前で紛れ込むのを防ぐ）。**代わりに機械照合できる半分が無くなる**ので、ゲート出力の報告本文はその旨を明示し、`worker_spawn_verified` にも `evidence=send_delivery` が残る（窓口が証拠の強さを一目で区別できる）。現行配備の全 backend は旧版 fallback で通常経路（`--evidence list_peers`、既定）に乗るため、今日この分岐には到達しない。

**`--approval` / `--instruction` は attest であって検証ではない**（PTY 打鍵と MCP 送信は事後にこのホストから観測できない）。ゲートが保証するのは次の 2 点だけで、それ以上を主張しない:

1. **機械照合できる半分**（`peer_cwd` / `peer_name` / `worker_spawned` の存在 / id が正整数 / `delegate_sent` と `runs` の非乖離）は assert では通せない
2. **省略が事後に必ず検出される**: ゲートを通ると `worker_spawn_verified` が記帳される。ゲートを通さずに報告すると記帳が無いので、`python3 ../tools/spawn_gate.py audit`（固定の deployment cutoff `--since` 以降の spawn は、何日経っても報告対象から消えない） が `worker_spawned` との差分として検出する（監視ループ 1 サイクルの一部。[`.dispatcher/CLAUDE.md`](../CLAUDE.md)「ワーカーペイン監視」参照）。2026-08-18 の 2 件はまさにこの差分の形で残っている

> **これは v1 の止血であって、儀式そのものの決定論化ではない。** spawn → 承認 Enter → 登録 poll → 指示送信 を LLM の散文再演から外してコードに落とす設計は Interlock (v2) 側の担当で、v1 (claude-org-ja + runtime 0.1 系) へは逆移植しないことが Issue [#740](https://github.com/suisya-systems/claude-org-ja/issues/740) の 2026-08-17 追補で決定済みである（同追補の「移行方式」は "dual-write は行わず run 境界で切り替える / v1 で開始済みの run は v1 で完走させる" と定め、"常駐 Dispatcher AI loop と handover / resume のための大量の prompt prose" を Discard 側に置いている）。本 Step が変えるのは「省略が安くて露見しない」という**誘因と検出可能性**であって、儀式の実行主体ではない。
