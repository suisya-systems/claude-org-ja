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
  - Bash(py -3 tools/capability_gate.py:*)
  - Bash(python3 tools/capability_gate.py:*)
  - Bash(python -c "from tools.state_db import connect;*)
  - Bash(python3 -c "from tools.state_db import connect;*)
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
> - **spawn 儀式**: 既定 broker は `--mcp-config <broker>` 注入による Claude Code **folder-trust プロンプト**の画面判定による機械承認に加え、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプトを画面判定で機械承認する（2 段承認）。`ORG_TRANSPORT=renga` 時は `--dangerously-load-development-channels server:renga-peers` の「Load development channel?」を画面判定で承認する 1 段。 **画面判定** = `inspect_pane` → `claude-org-runtime dispatcher spawn-prompt-step` が返した 1 手だけを `send_keys` する反復（runtime 0.1.43+）。盲目 Enter は禁止（folder-trust の初期カーソルは「No, exit」で、Enter 一発はワーカーを終了させる）。手順は spawn-flow 3-3b。
> - **エラー分岐**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8 + push-primary amendment（broker push 一次が **既定の契約**、pull は fallback として retain）。**opt-in `renga` は削除せず常時有効な切戻しの安全装置**として維持する。broker 実走（dogfood）は Epic #6 Issue G スコープで本ファイルの既定運用経路ではない（**二フレーム注記（Refs #604）**: ここの「既定 `broker`」は**コード既定**（`tools/transport.py: DEFAULT_TRANSPORT`、生成面はこれで render）。**運用既定**は broker dogfood が Epic #6 Issue G まで未活性のため `renga` で、両者は指す対象が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)。）

## Phase 1: ワーカー状態収集

**着手前に [`.claude/skills/org-suspend/references/recipient-ledger.md`](references/recipient-ledger.md) を Read する（MUST）**。
id 台帳の作り方（面ごとの id 取得・pane 制御の 2 グループ (G)・フォーカス前提 (F)・join 規則）と
宛先選定の真理値表の本文は同ファイルにあり、以降の Phase は同ファイルの記号（(G-MUST) /
(G-OWNTAB) / F-1 / F-2 / † / ‡ / §）をそのまま参照する。

1. **id 台帳を作る**（reference §1〜§4）。`mcp__org-broker__list_peers` から messaging 用の `peer_id`、
   `mcp__org-broker__list_panes` からペイン制御用の `pane_id` を取り、突合して 1 エントリ =
   `{peer_id, pane_id（割り当てられない場合は未設定）, name, role, cwd, same_tab, tab}` の台帳にする。
   この台帳を **Phase 2 / Phase 4 まで引き回す**
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

表の本文・role gate と `unresolved` の扱い・宛先は必ず `peer_id`・†（同タブのみが自組織）・
他タブピアの扱い・§・‡ は [`.claude/skills/org-suspend/references/recipient-ledger.md`](references/recipient-ledger.md) §5 にある。
対象 role 集合は Phase 1 が `R = {worker, dispatcher}`、Phase 4 が停止順に `R = {worker}` →
`R = {dispatcher}` → `R = {curator}`（curator ペインが存在する場合のみ）。

**2.0 系と判定したら、broadcast の前に人間へ報告して確認を取る（operational gate）**: ゲート本文は
`list_peers` を呼ぶ全経路の共有 reference に切り出してある。**`list_peers` の直前に
[`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../org-delegate/references/capability-first-drive-operational-gate.md)
を Read し、`interactive-action` の分岐を適用する**（`/org-suspend` は人間が起動する flow なので
人間が画面の前に居り、送信前に停止して確認を取れる。同 reference §6 の表 #1）。

## Phase 2: 未応答ワーカーのスクレイプ

応答がなかったワーカーについて:

1. `.state/workers/` から該当ワーカーの状態ファイルを読み、Pane Name と Directory を取得
2. Pane Name を Phase 1 の**台帳**で **`pane_id`** に解決する。`inspect_pane` はペイン制御面なので、
   使うのは `list_panes` 由来の `pane_id` であって `peer_id` ではない:
   - 台帳に `pane_id` が無いピア（`list_panes` に居ない、または Phase 1 の**列挙面のゲート (i) が
     不成立**で台帳全体に id を載せなかった）は 3 通りに分かれる。**いずれもスクレイプは
     諦め、Step 4 の git 情報だけで状態を推定する**が、**記録する状態が違う**:
     - `same_tab=False` / ‡ で他タブと導出されたピア → 「他タブに居るのでスクレイプ不能」。生存して
       いる可能性があるので**消滅と読み替えない**
     - それ以外で、**F-1 の (i) `pane_exited` 観測 or (ii) 列挙が窓口タブのものと確認済み** →「消滅済み」
     - それ以外で **F-1 のどちらも取れない** → **indeterminate**。フォーカスが別タブへ移っただけで
       生存中のピアが不在に見えている可能性があるので、消滅済みとして記録せず判定不能のまま残し、
       Phase 4 の最終報告に載せる。**列挙面のゲート (i) が不成立のケースはここに落ちる** — 列挙を
       自タブと確立できていない以上 F-1 の (ii) も同時に満たせないため
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

> **注**: blind kill のままにしている理由は [`.claude/skills/org-suspend/references/rationale.md`](references/rationale.md)。

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
   **全て**集める（= 確認済み attention ペイン集合。各レコードの **`id`（= pane 制御用の pane_id）** を控える）。
   **この列挙も (G-OWNTAB) の 2 手段で自タブのものと確立してから使う** — 確立できなければ close へ
   進まない（相対セレクタへフォールバックしない）。以降の手順 5 / 6 は gone / recycled と同じ
   「close しなかった」経路として扱い、**attention watcher を停止できなかったことを人間への報告に
   明記する**（Phase 4 手順 7 の部分中断報告に載せる）
2. `.state/attention_pane.json` を `Read` で開けたら `pane_id` を読む（= sidecar pane_id）。無ければ
   「sidecar 無し」
3. sidecar pane_id を **list_panes が返す name/role** で分類する（sidecar 記録の name は信用しない）:
   - **verified**（確認済み集合に含まれる）→ その pane はいまも本物の watcher。close 対象
   - **recycled**（list_panes にあるが name/role が attention でない）→ pane_id が別ペインへ再割当て済み。
     **絶対に close しない**
   - **gone**（list_panes に無い）→ 既に消滅。close しない
4. 確認済み集合の各ペインを **`list_panes` 由来の `pane_id`** で `mcp__org-broker__close_pane(target="<pane_id>")` する
   （`target="attention"` の name 指定はしない — role だけ持つ孤児に当たらないため）。
   ここは **pane 制御文脈**で、かつ対象は**自タブと確立済みの `mcp__org-broker__list_panes`**（手順 1）由来＝
   caller のタブに居ることが確定
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
  受けても**即「ワーカー閉鎖」と断定しない**。切り分けは
  [`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md)
  の「`pane_not_found` の messaging 分岐」の手順 2〜4 に従う（`list_peers` を取り直し、名前では選び直さず
  控えた `peer_id` → `same_tab: true` を確認できた同名レコードの順で 1 件に絞り、**1 回だけ**再送する。
  marker を欠く列挙の同名レコードは 1 件でも採らない。絞れなければ再送せず lifecycle 確認へ進む）。
  本スキルでの読み替えは次のとおり:
  1. 「送信時に控えていた peer id」は Phase 1 の台帳の `peer_id` を使う
  2. lifecycle 確認で同タブのピアの `mcp__org-broker__list_panes` 不在を根拠にするのは **F-1 の (ii) を満たすときだけ**。
     他タブのピア（`same_tab=False` / ‡ で他タブと導出）は `pane_exited` の観測か後続 `mcp__org-broker__list_peers`
     からの消失で確定する。どちらも取れなければ **indeterminate** として journal に記録し人間に報告する
  3. 消滅が確定したら pending 集合から外す。**worker 状態ファイルの `Status: pane_closed` は窓口が
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
  - **`close_pane` / `set_pane_identity` はそもそも名前宛・`"focused"` 宛で撃たない**（(G-MUST)）。
    この 2 本は Group B なので、`caller_scope` が広告されていても
    `caller_scope_close_identity` が確立していなければ相対セレクタが user 可視タブ / 全タブに
    解決されうる。**(G-OWNTAB) の 2 手段で自タブと確立した `mcp__org-broker__list_panes` 由来の数値 `pane_id`**
    でのみアドレスする

### 停止手順

1. `mcp__org-broker__list_peers` と `mcp__org-broker__list_panes` を取り直し、Phase 1 の**台帳を最新化**する（Phase 1 から
   時間が経っているため、`peer_id` / `pane_id` の両方を取り直す）。再構築でも Phase 1 手順 1 の
   **join 規則をそのまま適用する**（`pane_id` の出所は `mcp__org-broker__list_panes` だけ / 列挙を自タブと
   確立できなければどのエントリにも `pane_id` を割り当てない（(G-OWNTAB)） / `role` は join キーに
   しない / 他タブ・`same_tab` 欠落のエントリには `pane_id` を割り当てない）。**他タブ判定のピアが
   Phase 1 から増えていた場合は、真理値表の operational gate に従ってその差分を人間に報告し、
   確認を得てから停止指示へ進む**
   - **この取り直しが (G-OWNTAB) の確立段でもある**: 以降の `mcp__org-broker__close_pane` はすべてこの列挙から
     採った数値 `pane_id` で撃つので、**列挙を自タブと確立できたかをここで判定して以降の全 close に
     引き回す**（単一タブ backend = `org-broker` / `caller_scope` の 2 手段だけ。`list_peers` の
     `same_tab` は確立手段に数えない）。**いずれも成立しなければ close を 1 つも撃たない** — 台帳の
     全エントリが「`pane_id` を持たないエントリ」と同じ扱いになり、pending のまま手順 7 の**部分中断**
     報告に載せる（相対セレクタへフォールバックしない）
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
           # 突合は id で行う。契約 T-§3.1 が canonical 名を固定している
           ev_pane = ev.id ?? ev.pane_id          # canonical: ev.id (list_panes と同じ id 空間)
           ev_peer = ev.peer_id ?? ev.agent_id    # canonical: ev.peer_id (list_peers と同じ id 空間)
           # id 突合は「両辺が非 null」を前提にする。name のみのイベントでは ev_pane / ev_peer が
           # どちらも null になり、pane_id 未設定 (他タブ) のエントリと null == null で誤一致して
           # 無関係な生存ワーカーを pending から external してしまう
           hit = pending の中で
                   (ev_pane is not None かつ entry.pane_id == ev_pane) または
                   (ev_peer is not None かつ entry.peer_id == ev_peer)
           if hit is None and ev.name is not None:
               # name フォールバックは「同タブ確定エントリ」に限定する（下記）
               hit = pending の中で entry.name == ev.name かつ entry が同タブ確定
           if hit: pending.remove(hit)
   # deadline 到達 or pending が空で抜ける
   ```
   - **突合キーは id**。ratified §3.1 は per-event フィールド名を規定していないが、批准済み amendment (2026-08-09) の
     **T-§3.1 が canonical 名を `id`（pane 側）/ `peer_id`（peer 側）に固定**している。上の疑似コードが
     `ev.pane_id` / `ev.agent_id` も見るのは、canonical 名を出さない backend への**寛容（tolerance）**で
     あって、これらが正の名前だという意味ではない。**pane 側 id と peer 側 id のどちらか一方でも
     一致したら hit** とする（他タブピアは `list_panes` に出ないので pane 側だけでは join できない）
   - **`name` 単独を突合キーにしてはならない**。2.0 系の名前はタブ内一意でしかないので、別タブの
     同名ペインの `pane_exited` で自組織の**生存**ワーカーを誤って retire する。`name` フォールバックは
     `same_tab=True` / 旧版 fallback で**同タブと確定しているエントリ**に限って使う（他タブ対象が
     pending に居る間は name フォールバックを無効にしてもよい）
   - 初回 `since` 省略で「今以降のイベントだけ」セマンティクス（過去の pane_exited を replay しない）
   - `types=["pane_exited"]` filter は cursor を全 type で advance させるので重複 scan なし
   - filter 不一致イベント到着で long-poll が early return (`events:[]` + advanced cursor) するため、空応答時は deadline までループ継続
   - 10 秒以内に閉じなかった残留ワーカーは Pass 2 へ

   **Pass 2 (残留ワーカーへのフォールバック + 再確認、最大 5 秒)**:
   - **close の前に (G-OWNTAB) が成立していることを確かめる**（手順 1 で判定済み。成立していなければ
     この Pass 2 では 1 つも close せず、`pending` をそのまま次の再確認へ送る）
   - **close の直前に identity 照合する**（Phase 3.7 の attention watcher 停止と同じ idiom。台帳を
     取り直してから時間が経つと `pane_id` が別ペインへ再割当てされうるため、控えた id が期待どおりの
     ペインをなお指していることを毎回確かめる）:
     1. `mcp__org-broker__list_panes` を実行する
     2. 控えた `pane_id` のレコードが `name == "worker-{task_id}"` かつ `role == "worker"` を**なお
        指している** → その数値 id で close する
     3. レコードはあるが `name` / `role` が一致しない（別ペインへ再割当て済み）→ **close しない**
        （相対セレクタへフォールバックしない）。`pending` に残したまま次の再確認へ送る
     4. レコードが列挙に無い → close 対象を確認できないので **close しない**。消滅の確定は F-1 の
        (ii) を満たすときだけで、満たせなければ「判定不能」に倒す
   - `pending` に残っているエントリのうち、**`pane_id` を持ち、上の照合を通ったもの**
     （＝ caller のタブに居ることが確定したペイン）それぞれに対して:
     ```
     mcp__org-broker__close_pane(target="<照合済みの数値 pane_id>")
     ```
     でペインを明示破棄する（`worker-{task_id}` のような相対セレクタでは撃たない — (G-MUST)）。成功時は `"Closed pane id=N."` テキストが返る。ここは **pane 制御文脈**なので `[pane_not_found]` / `[pane_vanished]` は既に閉じた扱いで skip してよい（[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) 参照）。`[last_pane]` はワーカー停止段階では通常発生しない（窓口/ディスパッチャー/キュレーターが残っているため）
   - **`pane_id` を持たないエントリ（他タブのワーカー、および (G-OWNTAB) 不成立で id を割り当てられ
     なかったエントリ）には `close_pane` を撃たない**。タブ横断の
     pane 制御は原理的に不可で、返る `[pane_not_found]` は閉鎖の証拠にならない（契約 T-§4.2）。
     このエントリは pending に残したまま次の再確認へ送る
   - その後、同じ `poll_events` ループを `timeout_ms=5000` / deadline 5 秒で再度回し、close_pane 由来の `pane_exited` を消化する
   - Pass 2 後もまだ `pending` が空でなければ `mcp__org-broker__list_panes` / `mcp__org-broker__list_peers` で生存確認する（**id 突合**。`list_panes` は 1 タブ分しか返さないため、他タブのワーカーは `list_peers` からの消失で判定する。**`list_panes` の不在を停止の証拠にするのは F-1 の (ii) を満たすときだけ**で、満たせなければ「判定不能」に倒す）。**残存または判定不能なら**、閉鎖扱いにせず journal に記録して人間に報告する（強制終了は現状未サポート。「停止できなかったワーカーが居る」ことを報告に明記し、全ペイン停止を偽って完了報告しない）

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
   - Pass 2: **(3) と同じ前段**（(G-OWNTAB) の成立確認 → close 直前の identity 照合。期待する
     `name` / `role` は `dispatcher` / `curator` に読み替える）を通したうえで、`pending` に残った
     エントリのうち **`pane_id` を持ち照合を通ったもの**に
     `mcp__org-broker__close_pane(target="<照合済みの数値 pane_id>")` を送り（相対セレクタでは撃たない）、
     `poll_events` ループ
     (timeout_ms=5000) で再確認。`pane_id` を持たない（他タブの / (G-OWNTAB) 不成立の）エントリや
     照合が外れたエントリには撃たず、(3) と同じく
     残存として人間に報告する

**最後のペイン (窓口) の扱い**: ディスパッチャー（と存在した場合のキュレーター）を閉じた時点で、
窓口ペインが窓口タブに残る最後のペインになる。**窓口は自分自身を `mcp__org-broker__close_pane` しない**
（窓口の終了は人間の `exit` / `/exit` / 端末クローズに委ねる。根拠は
[`.claude/skills/org-suspend/references/rationale.md`](references/rationale.md)）。何らかの経路で窓口ペインに `close_pane` が飛び
`[last_pane]` が返った場合は、**強制再試行せず**そのまま人間の `exit` に委ねる。このコードを
自己 close の安全網として当てにしてはならない。

7. 人間に報告。**`pending` と `unresolved` が両方空のときだけ「中断しました」と言い切る**。
   どちらかに残りがあれば**部分中断**として報告し、完了を偽らない:

   **全て停止できた場合**:
   ```
   組織を中断しました。
   - 保存済み: {N}件の作業アイテム
   - 状態ファイル: .state/org-state.md
   /org-start で再開できます。
   ```

   **停止できなかった / 判定できなかったピアが残る場合**（`pending` が空でない、`unresolved` が
   空でない、他タブの対象 role ピアを検出した、Phase 3.7 で attention watcher を停止できなかった、
   のいずれか）:
   ```
   組織を部分的に中断しました（停止しきれていないペインがあります）。
   - 保存済み: {N}件の作業アイテム
   - 状態ファイル: .state/org-state.md
   - 停止できなかったワーカー: {peer_id / name / cwd を列挙}
   - role が判定できず対象外にしたピア: {peer_id / name / cwd を列挙}
   - 他タブに見えたが自組織と確認できずに対象外にしたピア: {peer_id / name / cwd を列挙}
   - 停止できなかった attention watcher: {pane_id を列挙}
   これらは走り続けている可能性があります。手動での確認 / 停止をお願いします。
   /org-start で再開できますが、上記のペインが残っている前提でご判断ください。
   ```
   - 該当が無いセクションは行ごと省く。**1 件でも残っていれば「組織を中断しました」の文面は使わない**
     （全ペイン停止を偽って報告しないこと自体が本フェーズの目的）
