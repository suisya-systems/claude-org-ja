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
  - mcp__renga-peers__set_summary
  - mcp__renga-peers__list_panes
  - mcp__renga-peers__set_pane_identity
  - mcp__renga-peers__list_peers
  - mcp__renga-peers__check_messages
  - mcp__renga-peers__send_message
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

## Step 0: 自分の identity を確認する

1. `mcp__renga-peers__set_summary` で「Dispatcher: 監視（resumed）」をセット
2. `mcp__renga-peers__list_panes` でフォーカスペインの name/role を確認:
   - 期待値: `name == "dispatcher"` かつ `role == "dispatcher"`
   - 不一致なら `mcp__renga-peers__set_pane_identity(target="focused", name="dispatcher", role="dispatcher")` で修復
3. 自分の `pane_id` を `list_panes` から取得（`focused: true` の id）
4. `mcp__renga-peers__list_peers` で `name == "dispatcher"` の `peer_id` を取得

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
2. フロントマター `created_at` を見て鮮度を判定:
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

`mcp__renga-peers__list_peers` を再度呼び、handover に記載のワーカー名が現存するか
確認する。消えていれば secretary に `WORKER_PANE_EXITED: worker-{task_id} (resume
時点で不存在)` を通知（reconcile は窓口の責務）。

## Step 5: 監視ループの再開

handover の `active_worker_count > 0`、state.db の active worker dirs が非空、
**または `.state/dispatcher/curate-inflight.json` が存在する**（オンデマンド curate の
完了監視が引き継ぎ対象。`.dispatcher/references/worker-monitoring.md` Step 5.3）ならば `/loop 3m` で
worker monitoring を再開する:

```
/loop 3m
```

- 監視ループの 1 サイクル目で `mcp__renga-peers__poll_events` は
  `.state/dispatcher-event-cursor.txt` の前 cursor（前 session 終了時点）から resume
  する。これで「pane が閉じている間に来た pane_exited は次回 poll で必ず拾える」
  semantics が維持される（renga 0.5.7+ の cursor 仕様）
- `mcp__renga-peers__check_messages` の 1 サイクル目で前 session 中にキューに溜まった
  worker → dispatcher peer message を drain する
- `.state/dispatcher/worker-idle-state.json` は前 session の `idle_streak_cycles` を
  保持しているので stall 検出の連続性も維持される

監視対象が 0 件（active worker dir も 0、active_runs も 0、`curate-inflight.json` も
無し）の場合は `/loop` を始動せず、idle 状態を secretary に通知して待機する:

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
