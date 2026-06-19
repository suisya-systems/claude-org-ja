---
name: dispatcher-resume
description: >
  /dispatcher-handover で書き出した handover ファイルを読み込み、
  ディスパッチャーを新しい session で復帰させる。/clear 直後の最初のターンで使う。
  state.db の dispatcher_pane_id / dispatcher_peer_id を atomic に更新し、
  worker monitoring の /loop 3m を再開する。
  「ディスパッチャー復帰」「resume」「引き継ぎから再開」と secretary から指示された
  ときに使う。/org-start ではない（ワーカー・窓口・キュレーターは生きている前提）。
effort: low
allowed-tools:
  - Read
  - Bash(py -3 ../tools/journal_append.py:*)
  - Bash(bash ../tools/journal_append.sh:*)
  - Bash(python3 -c:*)
  - Bash(py -3 -c:*)
  - Bash(ls:*)
  - Bash(mv:*)
  - mcp__org-broker__set_summary
  - mcp__org-broker__list_panes
  - mcp__org-broker__set_pane_identity
  - mcp__org-broker__list_peers
  - mcp__org-broker__check_messages
  - mcp__org-broker__send_message
---

# dispatcher-resume: ディスパッチャーの復帰

`/dispatcher-handover` で書き出した `.state/dispatcher-handover.md` を読み込み、
ディスパッチャーとして最低限の自覚（組織員としての立ち位置・進行中の派遣・監視
対象ワーカー）を復元し、`/loop 3m` の worker monitoring を再開する。

> **前提**:
> - ワーカー / 窓口 / キュレーターのペインは前 session から生きたまま残っている。
>   新たに spawn しない（/org-start ではない）。
> - 自ペイン（name=`dispatcher`）も生きている。secretary が `send_keys` で
>   `/clear` → `/dispatcher-resume` を打鍵した直後の状態。pane_id / peer_id は
>   変わっていないはずだが、必ず観測して state.db を **atomic 更新する**。
> - state DB (`.state/state.db`) はそのまま使う。
> - 監視 gap を埋める内部状態ファイル（`.state/dispatcher-event-cursor.txt` /
>   `.state/dispatcher/worker-idle-state.json` / `.state/dispatcher/curate-inflight.json`（存在時） /
>   `.state/pending_decisions.json`）は
>   前 session から残っている。新規作成・初期化しない（既存値からそのまま継続）。
> - handover ファイルが存在しないか古すぎる場合は、`/org-start` を案内して停止する。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の `mcp__org-broker__*` 呼び出しは **既定 `broker`**（`ORG_TRANSPORT` 無設定）で書いてあり、そのまま従えばよい（既定挙動）。`ORG_TRANSPORT=renga`（opt-in・切戻し可）では MCP サーバー名が `renga-peers` になり、ツールの **完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので手順の論理は変わらない）。輸送依存で手順が変わる点だけ renga 併記する:
>
> - **受信モデル**: 既定 broker は **push 一次**（各ペイン同居の channel sidecar `server:org-broker-channel` が broker キューを ~1 秒間隔で claim→`notifications/claude/channel` で idle セッションへ本文注入。pull = ナッジ + `check_messages` は sidecar 不在 / unhealthy / channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時のフォールバック層）。`ORG_TRANSPORT=renga` 時は dispatcher / worker メッセージが `<channel source="renga-peers" …>` として in-band で push される。
> - **spawn 儀式**: 既定 broker は `--mcp-config <broker>` 注入による Claude Code **folder-trust プロンプト**の `send_keys(enter=true)` 機械承認に加え、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプトを `send_keys(enter=true)` で機械承認する（2 段承認）。`ORG_TRANSPORT=renga` 時は `--dangerously-load-development-channels server:renga-peers` の「Load development channel?」を Enter 承認する 1 段。
> - **エラー分岐**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8 + push-primary amendment（broker push 一次が **既定の契約**、pull は fallback として retain）。**opt-in `renga` は削除せず常時有効な切戻しの安全装置**として維持する。broker 実走（dogfood）は Epic #6 Issue G スコープで本ファイルの既定運用経路ではない（**二フレーム注記（Refs #604）**: ここの「既定 `broker`」は**コード既定**（`tools/transport.py: DEFAULT_TRANSPORT`、生成面はこれで render）。**運用既定**は broker dogfood が Epic #6 Issue G まで未活性のため `renga` で、両者は指す対象が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)。）

## Step 0 の前: 残存 loop 予約 / stale 再発火ガード（重複起動の早期遮断）

`/dispatcher-resume` は **handover を 1 回だけ消費する one-shot** である（Step 7 で
`.state/dispatcher-handover.md` を `.consumed.md` に rename する）。だが Step 5 で起動する
`/loop 3m` の予約が、過去のバグや手動操作で `/dispatcher-resume` 自身を反復対象に握って
いると、監視サイクルのたびに本 skill が再発火する。この **stale 再発火 / 重複起動** を
Step 0 に入る前に遮断する（恒久対策の経緯は
[`knowledge/raw/2026-06-19-dispatcher-resume-loop-recursion.md`](../../../knowledge/raw/2026-06-19-dispatcher-resume-loop-recursion.md)）。

1. live / consumed の handover 状態を **存在ベース**で判定する（`now` との時刻比較に
   依存しない。cold-start vs resume の分岐が live `.md` のみを対象とする
   `.dispatcher/CLAUDE.md` の規約と揃える）:
   ```bash
   python3 -c "
   import os
   md = '../.state/dispatcher-handover.md'
   cm = '../.state/dispatcher-handover.consumed.md'
   if os.path.exists(md):
       print('resume')            # 正規の resume: live handover あり
   elif os.path.exists(cm):
       print('already_consumed')  # 既に消費済み: 残存 loop / 重複起動の疑い
   else:
       print('no_handover')       # handover が一度も無い: 真の cold-start 候補
   "
   ```
2. **`already_consumed`（live `.md` なし・`.consumed.md` あり）**: live handover が無い
   resume 起動だが `.consumed.md` が残っている。これは「(i) 直前に成功した resume の残存
   loop 予約による再発火 / 重複起動（組織は健全・監視ループ稼働中）」か「(ii) 組織が止まって
   いて本来 cold-start が要る状況での誤起動」のいずれか。**存在判定だけで no-op に倒すと (ii)
   の cold-start 案内を恒久的に隠す**ので、監視対象が実際に live かを観測して分岐する:
   - `mcp__org-broker__list_panes` / `mcp__org-broker__list_peers` で **監視対象が live か**を確認する: active な
     worker ペイン（`role == "worker"`）が 1 つ以上ある、または
     `.state/dispatcher/curate-inflight.json` が存在する（= 監視ループが回っているべき状態）。
   - **(i) 監視対象が live = stale 再発火 / 重複起動**: 組織は健全で cold-start 不要。
     `DISPATCHER_RESUME_FAILED` を **送らず**（誤った `/org-start` 案内を出さない）、
     Step 1 以降に進まず早期 exit する。**残存予約を空のまま放置しない**: 監視 `/loop` が
     `/dispatcher-resume` を反復対象に握っている兆候があれば、Step 5 の monitoring 専用
     ディレクティブ（INVARIANT(loop-prompt) 準拠の `/loop 3m ...`）で loop を明示的に再 arm
     して予約を上書きする（重複起動はしない＝単一 loop に収束）。既に monitoring ディレクティブ
     で回っていれば当該サイクルは **no-op heartbeat**（何も出力しない）。どちらの分岐でも予約を
     skill 自身に握らせたまま放置しない。再発火の事実だけ journal に残す:
     ```bash
     bash ../tools/journal_append.sh anomaly_observed \
         source=dispatcher_resume worker=dispatcher kind=stale_loop_refire confidence=n/a \
         note=handover_already_consumed
     ```
   - **(ii) 監視対象が無い = 本来 cold-start が要る誤起動**: stale 再発火ではない。no-op に
     倒さず **Step 1 の「handover ファイルなし」分岐へフォールスルーし `DISPATCHER_RESUME_FAILED`
     → cold-start 案内を出す**（cold-start 案内を隠さない）。
3. **`resume`（live `.md` あり）**: 通常どおり Step 0 へ進む。
4. **`no_handover`（live `.md` も `.consumed.md` も無し）**: 通常どおり Step 0 → Step 1 へ
   進み、Step 1 の「handover ファイルなし」分岐で `DISPATCHER_RESUME_FAILED` → cold-start
   案内に落ちる。

## Step 0: 自分の identity を確認する

1. `mcp__org-broker__set_summary` で「Dispatcher: 監視（resumed）」をセット
2. `mcp__org-broker__list_panes` でフォーカスペインの name/role を確認:
   - 期待値: `name == "dispatcher"` かつ `role == "dispatcher"`
   - 不一致なら `mcp__org-broker__set_pane_identity(target="focused", name="dispatcher", role="dispatcher")` で修復
3. 自分の `pane_id` を `list_panes` から取得（`focused: true` の id）
4. `mcp__org-broker__list_peers` で `name == "dispatcher"` の `peer_id` を取得

## Step 1: handover ファイルを読み込む

ディスパッチャーの cwd は `.dispatcher/` なので、リポジトリ root のパスを 1 階層上に解決する。

1. `.state/dispatcher-handover.md` が存在するか確認:
   ```bash
   ls -la ../.state/dispatcher-handover.md 2>&1
   ```
   - 存在しない → secretary に通知して停止:
     ```
     DISPATCHER_RESUME_FAILED: handover ファイルがありません。
     /org-start でディスパッチャーを cold start してください。
     ```
2. フロントマター `created_at` を見て鮮度を判定する。`created_at` は決定的 UTC で書かれている
   ([`/dispatcher-handover`](../dispatcher-handover/SKILL.md)) ので、**比較用の `now` も決定的 UTC で取得する**
   （`date -u +%Y-%m-%dT%H:%M:%SZ`、PowerShell 環境は
   `(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")`）。local(JST)時刻で `now` を
   取ると `now - created_at` が約 9 時間ぶれ、24h / 7d 境界付近で鮮度を誤判定するため:
   - 24 時間以内 → そのまま採用
   - 24 時間超〜7 日以内 → secretary に警告（「handover が古い、続行する旨」）
   - 7 日超 → 採用せず `/org-start` への切り替えを推奨し停止
3. ファイル本文を Read で取り込む。書かれている内容は次 session の自分にとっての
   「事実」として扱う（後の Step 3 で state.db と照合する）。

## Step 2: state.db の dispatcher identity を atomic 更新する

Step 0 で観測した `pane_id` / `peer_id` を `StateWriter.transaction()` 経由で
**1 トランザクションで** 書く（post-commit hook が `.state/org-state.md` を再生成
してくれる）。これが acceptance の「state.db identity が atomic 更新」要件の本体。

```bash
python3 -c "
import sys, os
sys.path.insert(0, os.path.abspath('..'))
from pathlib import Path
from tools.state_db import connect
from tools.state_db.writer import StateWriter
conn = connect('../.state/state.db')
with StateWriter(conn, claude_org_root=Path('..')).transaction() as w:
    w.update_session(
        dispatcher_pane_id='<observed_pane_id>',
        dispatcher_peer_id='<observed_peer_id>',
    )
"
```

- `dispatcher_pane_id` / `dispatcher_peer_id` は **文字列**で書く（schema は TEXT、
  既存 `/org-start` も文字列を入れているので型を揃える）
- `transaction()` 内なので片方だけ書いて失敗しても DB は半端な状態にならない
- 観測値が handover frontmatter と異なっても、現観測（list_panes / list_peers）を
  真値として優先する。差分があった旨を secretary 宛 message に含める

## Step 3: state.db で現状を再取得し handover と照合する

```bash
python3 -c "
import sys, os
sys.path.insert(0, os.path.abspath('..'))
from tools.state_db import connect
from tools.state_db.queries import get_org_state_summary
import json
conn = connect('../.state/state.db')
print(json.dumps(get_org_state_summary(conn), ensure_ascii=False, indent=2, default=str))
"
```

確認項目:
- `active_runs[]` が handover の「監視対象のワーカー」セクションと整合するか
- `active_worker_dirs[]` のワーカーディレクトリ存在

handover には書かれていないが state.db に `active` で残っている worker、または
handover には書かれているが state.db / list_panes で消えている worker があれば、
secretary に **報告して** 判断を仰ぐ（勝手に再 spawn / status 変更しない）。

## Step 4: ペイン生存確認

`mcp__org-broker__list_peers` を再度呼び、handover に記載のワーカー名が現存するか
確認する。消えていれば secretary に `WORKER_PANE_EXITED: worker-{task_id} (resume
時点で不存在)` を通知（reconcile は窓口の責務）。

## Step 5: 監視ループの再開

以下を**この順序で**評価する:

1. **inflight 再生成（先に実行）**: Step 4 の `list_peers` / `list_panes` に
   `name == "curator"` のペインが生きているのに `.state/dispatcher/curate-inflight.json`
   が無い場合（前 session が spawn 直後の inflight 書き込み前に途切れた等）、untracked
   curator を放置しないよう `started_at = <決定的 UTC>` / `reasons: []` / `extended: false` /
   `last_inspect_hash: null` / `last_inspect_ts: null` で inflight を**再生成する**。
   `started_at` は **`date -u +%Y-%m-%dT%H:%M:%SZ`**（PowerShell 環境は
   `(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")`）の出力をそのまま埋め、
   **手書きの local(JST)時刻を `Z` で書かない**（再生成 started_at が未来時刻になると Step 5.3 の
   `now - started_at` が負値となり curator が永久に孤立する。決定的取得の正本は
   [`.dispatcher/references/pane-close.md` 5-3](../../../.dispatcher/references/pane-close.md)）。
   以降の判定はこの再生成後の状態で行う（= このケースは必ず 2 の再開条件を満たす）
2. **`/loop 3m` 再開条件**: handover の `active_worker_count > 0`、state.db の
   active worker dirs が非空、**または `curate-inflight.json` が存在する**
   （1 の再生成分を含む。オンデマンド curate の完了監視が引き継ぎ対象。
   `.dispatcher/references/worker-monitoring.md` Step 5.3）のいずれかを満たせば
   下記の monitoring 専用ディレクティブを渡して `/loop 3m` で worker monitoring を
   再開する（**prompt を省略しない**）:

<!--
INVARIANT(loop-prompt): `/loop` の prompt 引数に skill 自身（`/dispatcher-resume`）や
他のスラッシュコマンドを渡さない。skill 実行ターン内で prompt を省略して `/loop` を
起動すると、反復対象としてアクティブな slash command が捕捉され、本 skill が 3 分ごとに
自己再帰する（2026-06-19 incident。詳細は
knowledge/raw/2026-06-19-dispatcher-resume-loop-recursion.md）。必ず monitoring 専用の
自然文ディレクティブを明示的に渡すこと。tests/test_dispatcher_resume_loop_invariant.py が
この不変条件を pin する。
-->

```
/loop 3m references/worker-monitoring.md（ディスパッチャー cwd .dispatcher/ 基準）の「監視ループ 1 サイクル」を 1 回だけ実行する（poll_events → check_messages → list_panes → inspect_pane → stall / relay-gap / pane_output 評価）。anomaly / stall / relay-gap / pane_exited を検出したサイクルのみ secretary へ通知し、検出が無ければ何も出力しない（毎サイクルの状況サマリや自然言語の状況描写を書かない）。cadence は 3 分以上を保ち短縮しない。スラッシュコマンドや本 skill を反復対象にしない。
```

- 監視ループの 1 サイクル目で `mcp__org-broker__poll_events` は
  `.state/dispatcher-event-cursor.txt` の前 cursor（前 session 終了時点）から resume
  する。これで「pane が閉じている間に来た pane_exited は次回 poll で必ず拾える」
  semantics が維持される（cursor 仕様）
- `mcp__org-broker__check_messages` の 1 サイクル目で前 session 中にキューに溜まった
  worker → dispatcher peer message を drain する（broker = `mcp__org-broker__check_messages`。
  broker は push 一次に再設計済だが、`/clear` 中に channel sidecar 経由で注入できなかった
  滞留分はキューに残るため、resume 1 サイクル目の `check_messages` フォールバック drain で
  取りこぼさず回収する＝ツール名のみ変わり drain 論理は同型。§9.6 / §9.3 lease-reap）
- `.state/dispatcher/worker-idle-state.json` は前 session の `idle_streak_cycles` を
  保持しているので stall 検出の連続性も維持される

監視対象が 0 件（active worker dir も 0、active_runs も 0、`curate-inflight.json` も
無し、かつ 1 の評価後なので `list_panes` に curator ペインも不在）の場合のみ `/loop` を
始動せず、idle 状態を secretary に通知して待機する:

```
DISPATCHER_RESUMED_IDLE: 監視対象なしで resume 完了。DELEGATE 待機。
```

## Step 6: secretary にブリーフィングを返す

handover + state.db の現状を統合した上で、secretary に簡潔に報告:

```
DISPATCHER_RESUMED: ディスパッチャー復帰完了。
- pane=<observed_pane_id> / peer=<observed_peer_id> (state.db 更新済)
- 監視対象ワーカー: <task_id list>
- pending decisions: <count>
- handover との差分: <あれば 1 行、無ければ「なし」>
- 監視ループ: /loop 3m 再開 (or idle)
```

## Step 7: handover ファイルを consumed 状態に切り替える

resume が成功したら、`.state/dispatcher-handover.md` を `.state/dispatcher-handover.consumed.md` に **rename する**。これにより:

- `.dispatcher/CLAUDE.md` 起動時の自動分岐（handover ファイルが直近 7 日以内に存在すれば resume）が「次の `/org-start` cold-start 時にも誤って resume に分岐する」事故を防ぐ
- 直近 1 件のみ参照用に `.consumed.md` 形式で残る（次の `/dispatcher-handover` が新たに `.md` を書いた時点で `.prev.md` バックアップに置き換わる、または上書きされる）

```bash
mv ../.state/dispatcher-handover.md ../.state/dispatcher-handover.consumed.md
```

- 既に `.consumed.md` があれば上書きで構わない（直近 1 件のみ保持）
- `.state/dispatcher-handover.prev.md` は前回の `/dispatcher-handover` で書かれたバックアップ。読み込み済みでも消さない

## イベント記録

ディスパッチャーの cwd は `.dispatcher/` なので 1 階層上を呼ぶ:

```bash
bash ../tools/journal_append.sh dispatcher_resumed \
    pane_id=<observed_pane_id> peer_id=<observed_peer_id> \
    active_workers=<count> note=resumed_from_handover
```

## やってはいけないこと

- 新規にディスパッチャー / キュレーターを spawn する（既に生きている）
- ワーカーに勝手に SUSPEND / SHUTDOWN を送る
- `.state/dispatcher-event-cursor.txt` / `worker-idle-state.json` /
  `curate-inflight.json` / `pending_decisions.json` を初期化 / 削除する（前 session からの監視連続性が壊れる）
- handover の内容と state.db の現状が食い違うときに、勝手にどちらかへ寄せる
  （必ず secretary に報告して判断を仰ぐ）
- atomic 更新を分割して書く（必ず `StateWriter.transaction()` 1 ブロックで完結させる）
- Step 5 の `/loop 3m` を prompt 省略で起動する / `/dispatcher-resume`（本 skill 自身）や
  他のスラッシュコマンドを `/loop` の反復対象に渡す（skill が 3 分ごとに自己再帰する。
  必ず monitoring 専用ディレクティブを明示的に渡す。Step 5 の INVARIANT(loop-prompt) 参照）
