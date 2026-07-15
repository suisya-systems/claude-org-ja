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
> - **spawn 儀式**: 既定 broker は `--mcp-config <broker>` 注入による Claude Code **folder-trust プロンプト**の `send_keys(enter=true)` 機械承認に加え、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプトを `send_keys(enter=true)` で機械承認する（2 段承認）。`ORG_TRANSPORT=renga` 時は `--dangerously-load-development-channels server:renga-peers` の「Load development channel?」を Enter 承認する 1 段。
> - **エラー分岐**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8 + push-primary amendment（broker push 一次が **既定の契約**、pull は fallback として retain）。**opt-in `renga` は削除せず常時有効な切戻しの安全装置**として維持する。broker 実走（dogfood）は Epic #6 Issue G スコープで本ファイルの既定運用経路ではない（**二フレーム注記（Refs #604）**: ここの「既定 `broker`」は**コード既定**（`tools/transport.py: DEFAULT_TRANSPORT`、生成面はこれで render）。**運用既定**は broker dogfood が Epic #6 Issue G まで未活性のため `renga` で、両者は指す対象が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)。）

## Phase 1: ワーカー状態収集

1. `mcp__org-broker__list_peers` で稼働中のピアを列挙する
2. 自分自身とキュレーターを除いた全ピアに `mcp__org-broker__send_message` で以下を送信:
   ```
   SUSPEND: 現在の状態を報告してください。
   1. これまでに完了したこと
   2. 変更したファイル（コミット済み/未コミット）
   3. 次にやろうとしていたこと
   4. ブロッカーや未解決の問題
   ```
3. 30 秒間 `mcp__org-broker__check_messages` で応答を待つ（5 秒間隔でポーリング）
4. 応答があったワーカーの報告を記録する

## Phase 2: 未応答ワーカーのスクレイプ

応答がなかったワーカーについて:

1. `.state/workers/` から該当ワーカーの状態ファイルを読み、Pane Name と Directory を取得
2. 画面内容スクレイプで最新のコンソール出力を読む:
   ```
   mcp__org-broker__inspect_pane(target="worker-{task_id}", format="text")
   ```
   画面表示だけでは不十分な場合は、次の Step 3 の git 情報で補完する
3. ワーカーの作業ディレクトリで以下を実行:
   - `git status`
   - `git diff --stat`
   - `git log --oneline -5`
4. これらの情報からワーカーの状態を推定する

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

> **注**: この blind kill は「/org-start で再開する前提の中断」なので簡素なまま残す。daemon ごと
> 落とす [`/org-down`](../org-down/SKILL.md) では、pid recycle による誤 kill を避ける stale-pid-safe な
> 停止（`/proc` / `Get-CimInstance` の CommandLine 照合）に差し替える。

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
   **全て**集める（= 確認済み attention ペイン集合。各 **数値 pane_id** を控える）
2. `.state/attention_pane.json` を `Read` で開けたら `pane_id` を読む（= sidecar pane_id）。無ければ
   「sidecar 無し」
3. sidecar pane_id を **list_panes が返す name/role** で分類する（sidecar 記録の name は信用しない）:
   - **verified**（確認済み集合に含まれる）→ その pane はいまも本物の watcher。close 対象
   - **recycled**（list_panes にあるが name/role が attention でない）→ pane_id が別ペインへ再割当て済み。
     **絶対に close しない**
   - **gone**（list_panes に無い）→ 既に消滅。close しない
4. 確認済み集合の各ペインを **数値 pane_id** で `mcp__org-broker__close_pane(target="<id>")` する
   （`target="attention"` の name 指定はしない — role だけ持つ孤児に当たらないため）。
   `[pane_not_found]` / `[pane_vanished]` は既に閉じた扱いで skip
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

1. `mcp__org-broker__list_peers` で稼働中のピアを列挙
2. **ワーカーを先に停止**: 全ワーカーピアに `mcp__org-broker__send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
3. **ワーカーペインが閉じたことを確認** — 2-pass 構造で実施:

   **Pass 1 (polite shutdown の観察、最大 10 秒)**:

   `mcp__org-broker__poll_events` で `pane_exited` を long-poll する。`types=["pane_exited"]` フィルタで他 type を除外しつつ、deadline 内でループして待機対象が全て閉じたら break:
   ```
   pending_workers = {全ワーカーの name set}
   cursor = None                           # 初回は since 省略
   deadline = now + 10 秒
   while pending_workers not empty and now < deadline:
       remaining_ms = (deadline - now) ミリ秒
       result = mcp__org-broker__poll_events(
           since=cursor,
           timeout_ms=min(remaining_ms, 10000),
           types=["pane_exited"]
       )
       cursor = result.next_since
       for ev in result.events:
           if ev.role == "worker" and ev.name in pending_workers:
               pending_workers.remove(ev.name)
   # deadline 到達 or pending_workers が空で抜ける
   ```
   - 初回 `since` 省略で「今以降のイベントだけ」セマンティクス（過去の pane_exited を replay しない）
   - `types=["pane_exited"]` filter は cursor を全 type で advance させるので重複 scan なし
   - filter 不一致イベント到着で long-poll が early return (`events:[]` + advanced cursor) するため、空応答時は deadline までループ継続
   - 10 秒以内に閉じなかった残留ワーカーは Pass 2 へ

   **Pass 2 (残留ワーカーへのフォールバック + 再確認、最大 5 秒)**:
   - Pass 1 で閉じていないワーカーそれぞれに対して:
     ```
     mcp__org-broker__close_pane(target="worker-{task_id}")
     ```
     でペインを明示破棄する。成功時は `"Closed pane id=N."` テキストが返る。`[pane_not_found]` / `[pane_vanished]` は既に閉じた扱いで skip（`references/renga-error-codes.md` 参照）。`[last_pane]` はワーカー停止段階では通常発生しない（窓口/ディスパッチャー/キュレーターが残っているため）
   - その後、同じ `poll_events` ループを `timeout_ms=5000` / deadline 5 秒で再度回し、close_pane 由来の `pane_exited` を消化する
   - Pass 2 後もまだ閉じていないワーカーは `mcp__org-broker__list_panes` で生存確認し、残存なら journal に記録して人間に報告（強制終了は現状未サポート）

4. **ディスパッチャーを停止**: ディスパッチャーに `mcp__org-broker__send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」
5. **キュレーターを停止（存在する場合のみ）**: curator は常駐しないため、通常このステップは
   no-op。`mcp__org-broker__list_panes` に `name == "curator"` のペインが存在する場合
   （オンデマンド curate 実行中に suspend が重なったケース）のみ、`send_message` で終了を指示:
   「SHUTDOWN: 作業を終了してください。」（curate は move-then-mark 設計のため途中停止でも
   破壊的な中間状態は残らない）
6. ディスパッチャー（および存在した場合のみキュレーター）も (3) と同じ 2-pass 構造で確認（`pending = {"dispatcher"}`、curator が存在した場合は `"curator"` も集合に入れ、`role == "dispatcher"` または `role == "curator"` の `pane_exited` を待つ）:
   - Pass 1: `poll_events(types=["pane_exited"], timeout_ms=10000)` 相当ループ
   - Pass 2: 残った pane に `mcp__org-broker__close_pane(target="dispatcher")`（curator 残存時は `close_pane(target="curator")` も）を送り、`poll_events` ループ (timeout_ms=5000) で再確認

**最後のペイン (窓口) の扱い**: ディスパッチャー（と存在した場合のキュレーター）を閉じた時点でタブに残るのは窓口
ペインのみになる。窓口が自分自身を `mcp__org-broker__close_pane(target="secretary")` で
閉じようとすると `[last_pane]` (唯一のタブの唯一のペイン) が返るので、**窓口は自分自身で
`exit` して自然終了させる** (人間が端末を閉じる、または `/exit` でシェルに戻る)。
org-suspend は窓口ペインを閉じる責任を負わない。

7. 人間に報告:
   ```
   組織を中断しました。
   - 保存済み: {N}件の作業アイテム
   - 状態ファイル: .state/org-state.md
   /org-start で再開できます。
   ```
