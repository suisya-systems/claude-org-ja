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
  - mcp__renga-peers__send_message
---

# dispatcher-handover: ディスパッチャーの引き継ぎ

ディスパッチャー session を長期化させずに、現在の monitoring 状態と組織員としての
立ち位置を次 session へ受け渡すための handover ファイルを作る。書き出した後、
secretary に「ack を受けたら send_keys で /clear → /dispatcher-resume を打って
ほしい」と通知する。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `renga` / opt-in `broker`）**: 本スキルの `mcp__renga-peers__*`（handover 通知の `send_message` 等）は **既定 `renga`** で書いてあり、`ORG_TRANSPORT` 無設定ならそのまま従えばよい（既定挙動不変）。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では完全修飾名が **`mcp__renga-peers__*` → `mcp__org-broker__*`** に機械置換され、secretary からの ack 受信は broker でも **push 一次**（channel sidecar `server:org-broker-channel` が `notifications/claude/channel` で idle に注入。runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9）で、**push 失効時のフォールバック**は能動 `check_messages`（ナッジが出れば契機になりうるが idle を起こさないため能動 poll が正路。§9.6）。secretary 側の `/clear` → `/dispatcher-resume` 打鍵は `mcp__org-broker__send_keys` で同型、エラーは broker 追加コード（[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) の broker 節）が加わる。詳細は [`.dispatcher/CLAUDE.md`](../../../.dispatcher/CLAUDE.md)「輸送層（transport）両系」節と [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）を参照。既定 renga の手順は不変（broker は加算）。

> **重要な前提**:
> - 本 skill は **ディスパッチャー自身**（`.dispatcher/` cwd）が実行する。
>   secretary から直接呼ぶものではない。
> - ワーカー / 窓口 / キュレーターのペインは生かしたまま残す。`/clear` は
>   ディスパッチャー Claude のコンテキストだけをリセットするので、state.db と
>   handover ファイルから復帰できれば monitoring は途切れない。
> - ディスパッチャーペイン (name=`dispatcher`) も生かしたまま残す。ペイン自体を
>   閉じると pane_id / peer_id が変わり、`/loop 3m` の hook 再登録が必要になる。
>   secretary は `mcp__renga-peers__send_keys(target="dispatcher", ...)` で `/clear`
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

`mcp__renga-peers__send_message(to_id="secretary", message=...)` で以下を伝える:

```
DISPATCHER_HANDOVER_READY: ../.state/dispatcher-handover.md に書き出しました。
ack を返したら mcp__renga-peers__send_keys(target="dispatcher") で
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
