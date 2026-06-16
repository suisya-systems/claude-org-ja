---
name: dispatcher-handover
description: >
  ディスパッチャーのコンテキストを圧迫したまま session を続けるのを避けるため、
  monitoring 状態（active workers / 直近 polling cursor / pending escalations）を
  handover ファイルに書き出し、secretary の指示で /clear → /dispatcher-resume の
  流れで新しいディスパッチャー session を開始する準備をする。
  Secretary から DISPATCHER_HANDOVER peer message を受領したとき、または
  ディスパッチャー自身が context が長くなったと判断したときに使う。
effort: low
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(py -3 ../tools/journal_append.py:*)
  - Bash(bash ../tools/journal_append.sh:*)
  - Bash(python3 -c:*)
  - Bash(py -3 -c:*)
  - Bash(ls:*)
  - Bash(cp:*)
  - mcp__org-broker__send_message
---

# dispatcher-handover: ディスパッチャーの引き継ぎ

ディスパッチャー session を長期化させずに、現在の monitoring 状態と組織員としての
立ち位置を次 session へ受け渡すための handover ファイルを作る。書き出した後、
secretary に「ack を受けたら send_keys で /clear → /dispatcher-resume を打って
ほしい」と通知する。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節。）

> **重要な前提**:
> - 本 skill は **ディスパッチャー自身**（`.dispatcher/` cwd）が実行する。
>   secretary から直接呼ぶものではない。
> - ワーカー / 窓口 / キュレーターのペインは生かしたまま残す。`/clear` は
>   ディスパッチャー Claude のコンテキストだけをリセットするので、state.db と
>   handover ファイルから復帰できれば monitoring は途切れない。
> - ディスパッチャーペイン (name=`dispatcher`) も生かしたまま残す。ペイン自体を
>   閉じると pane_id / peer_id が変わり、`/loop 3m` の hook 再登録が必要になる。
>   secretary は `mcp__org-broker__send_keys(target="dispatcher", ...)` で `/clear`
>   と `/dispatcher-resume` を打鍵するだけで pane を維持する canonical 経路を取る。
> - state DB (`.state/state.db`) は唯一の SoT。pane/peer identity は handover に
>   参考値として書くが、resume 時の真値は `list_panes` / `list_peers` の現観測。
> - 監視ループに gap を生まないために、以下のファイルは **絶対に削除・編集しない**:
>   - `.state/dispatcher-event-cursor.txt`（次サイクルの poll_events cursor）
>   - `.state/dispatcher/worker-idle-state.json`（stall 検出の idle streak）
>   - `.state/dispatcher/curate-inflight.json`（オンデマンド curate の開始記録。存在する場合のみ）
>   - `.state/pending_decisions.json`（判断仰ぎ register）
>   - `.state/workers/worker-*.md`（各ワーカー run state）
>   handover ファイルは上記の **追加**コンテキスト（人間とのやり取りの温度感は無いが、
>   進行中の派遣事情・直近の anomaly 観測）に絞る。

## Step 1: handover 対象を整理する

書き出す前に、ディスパッチャー自身の context から以下を抽出する:

1. **直近の派遣事情**
   - DELEGATE 受信 → spawn 成否、escalate 経路に乗ったタスクの ID
2. **進行中のワーカー監視**
   - `.state/workers/worker-*.md` の Status が `active` なペイン名と最新の Progress Log 抜粋
3. **直近 anomaly 観測の要約**
   - 過去 1 サイクル分で `journal_append` した `anomaly_observed` / `notify_sent` のうち
     未解消のもの
4. **未配送 / 失敗した送信**
   - `[pane_not_found]` / `[split_refused]` 等で secretary に escalate 済み or
     再試行待ちのもの
5. **次のアクション（ディスパッチャー視点）**
   - 次サイクルで優先的に確認すべき worker / 中継待ちの判断

## Step 2: state.db から構造化情報を取得する

handover に参考情報として埋め込む。書き出し先は sandbox で write 可能な `$TMPDIR`
（未設定なら `/tmp` フォールバック）に置く:

```bash
python3 -c "
from tools.state_db import connect
from tools.state_db.queries import get_org_state_summary
import json, os
conn = connect('.state/state.db')
out_path = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'dispatcher-handover-state.json')
with open(out_path, 'w') as f:
    json.dump(get_org_state_summary(conn), f, ensure_ascii=False, indent=2, default=str)
print(out_path)
"
```

ここから以下を取り出す:
- `session.dispatcher_pane_id` / `session.dispatcher_peer_id`（現在の identity）
- `active_runs[]`（進行中タスク）
- `active_worker_dirs[]`（生きているワーカーディレクトリ）
- 直近の `recent_events` のうち `worker_spawned` / `worker_reported` / `worker_escalation`
  上位 5 件程度

ディスパッチャーの cwd は `.dispatcher/` なので相対パスは 1 階層上に解決する:

```bash
# .dispatcher/ から実行する場合
python3 -c "
import sys, os
sys.path.insert(0, os.path.abspath('..'))
from tools.state_db import connect
from tools.state_db.queries import get_org_state_summary
import json
conn = connect('../.state/state.db')
out_path = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'dispatcher-handover-state.json')
with open(out_path, 'w') as f:
    json.dump(get_org_state_summary(conn), f, ensure_ascii=False, indent=2, default=str)
print(out_path)
"
```

## Step 3: handover ファイルを書き出す

書き出し先: `.state/dispatcher-handover.md`（リポジトリルート起点。ディスパッチャー
cwd `.dispatcher/` からは `../.state/dispatcher-handover.md`）。

既存ファイルがあれば `.prev.md` にバックアップしてから上書きする:

```bash
[ -f ../.state/dispatcher-handover.md ] && \
  cp ../.state/dispatcher-handover.md ../.state/dispatcher-handover.prev.md
```

フォーマット（YAML frontmatter + markdown）:

```markdown
---
created_at: <date -u +%Y-%m-%dT%H:%M:%SZ の出力。決定的 UTC、JST-as-Z 禁止>
dispatcher_pane: <pane_id> / peer=<peer_id>
active_worker_count: <int>
event_cursor_present: <true | false>
idle_state_present: <true | false>
pending_decisions_count: <int>
---

# Dispatcher Handover

## 監視対象のワーカー
- worker-<task_id> (<worker_dir>): Status=<active|...>、直近 Progress Log 1 行抜粋
- ...

## 直近 anomaly / notify_sent サマリー
- worker-<task_id>: kind=<approval_blocked|stall_suspected|relay_gap_suspected> ...
（無ければ「なし」と明記する）

## 未配送 / 失敗した送信
- ...
（無ければ「なし」）

## 次のアクション（ディスパッチャー視点）
- 次サイクルで再確認: worker-<task_id> の <理由>
- ...

## 監視 gap を埋める参照ファイル（read-only、本 skill は触らない）
- `.state/dispatcher-event-cursor.txt`: poll_events 次 cursor（resume 後そのまま使う）
- `.state/dispatcher/worker-idle-state.json`: stall 検出の idle streak
- `.state/dispatcher/curate-inflight.json`: オンデマンド curate の開始記録（存在する場合のみ。resume 後の Step 5.3 timeout 管理が `started_at` 起点で継続する）
- `.state/pending_decisions.json`: 判断仰ぎ register
- `.state/workers/worker-*.md`: 各ワーカー run state

## 参考: state.db スナップショット
（Step 2 で取得した session / active_runs / recent_events を簡潔に転記）
```

**書き方の注意**:
- 「過去ログ」ではなく「次の自分への申し送り」として書く。
- `created_at` は **`date -u +%Y-%m-%dT%H:%M:%SZ`**（PowerShell 環境は
  `(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")`）の出力をそのまま埋める。
  local(JST)時刻を手書きして `Z` を付けない（JST-as-Z 禁止）。[`/dispatcher-resume`](../dispatcher-resume/SKILL.md)
  はこの `created_at` を 7 日鮮度窓（cold-start vs resume 分岐）で `now - created_at` 評価するため、
  未来時刻が混入すると判定がぶれる。dispatcher 状態ファイルの時刻を一律 UTC に揃える方針
  （[`.dispatcher/references/worker-monitoring.md`](../../../.dispatcher/references/worker-monitoring.md) 冒頭の時刻規約）と整合させる。
- 機密情報・トークン・パスワードは絶対に書かない。
- ファイルは secretary / 人間も読むことを想定する。

## Step 4: イベントを記録する

ディスパッチャーの cwd は `.dispatcher/` なので 1 階層上を呼ぶ:

```bash
bash ../tools/journal_append.sh dispatcher_handover \
    active_workers=<int> pending_decisions=<int> \
    note=context_compaction
```

## Step 5: secretary に通知する

`mcp__org-broker__send_message(to_id="secretary", message=...)` で以下を伝える:

```
DISPATCHER_HANDOVER_READY: ../.state/dispatcher-handover.md に書き出しました。
ack を返したら mcp__org-broker__send_keys(target="dispatcher") で
/clear → /dispatcher-resume を順に打鍵してください。
ペインは閉じないでください（pane_id 維持で監視 gap を最小化）。
active workers: <count>, pending decisions: <count>。
```

secretary はこの message を受領して、人間にエスカレーションせず（routine handover
は判断仰ぎではない）、`send_keys` で /clear と /dispatcher-resume を打鍵する。
ack が secretary から戻った後、本 skill は完了。次に何もしない（/clear で context
がリセットされる前提）。

**ディスパッチャーがやってはいけないこと**:
- `/clear` を自分で打とうとしない（外部から send_keys で受ける側）
- ワーカーやキュレーターに SHUTDOWN を送らない（pane は生かしたまま）
- `.state/dispatcher-event-cursor.txt` / `worker-idle-state.json` /
  `curate-inflight.json` / `pending_decisions.json` を編集 / 削除しない（resume 時の連続性が壊れる）
- `/loop 3m` を自分で停止しない（resume 後に再開する設計だが、現サイクルは継続）
