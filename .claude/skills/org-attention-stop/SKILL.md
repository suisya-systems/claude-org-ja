---
name: org-attention-stop
description: >
  `/org-attention-start` で起動した attention watcher ペインを停止する。
  `.state/attention_pane.json` に記録された pane_id を `list_panes` の name/role で
  identity 確認し、いまも attention watcher を指している場合だけ `close_pane`（pane 破棄）で
  破棄する（pane_id が別ペインへ再割当て済みなら close せず stale sidecar として削除）。
  「attention 止めて」「通知監視を停止」「watcher 落として」等で発動。
effort: low
allowed-tools:
  - Read
  - Bash(rm:*)
  - Bash(del:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
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

# org-attention-stop: attention watcher の停止

[`/org-attention-start`](../org-attention-start/SKILL.md) で起動した watcher ペインを閉じ、
sidecar (`.state/attention_pane.json`) をクリアする。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節。）

## Step 1: ライブペインと sidecar の照合（close 前に identity を確定する）

> **なぜ identity 確認が必要か（Issue #468）**: renga 0.18+ / broker は pane id を
> lifecycle 跨ぎで **recycle** する。watcher がクラッシュすると、その pane_id が
> 別ペイン（新規 spawn された worker 等）へ再割当てされることがある。このとき sidecar
> (`.state/attention_pane.json`) に残った pane_id を**無検証で `close_pane` すると、
> 無関係なペインを kill する**。よって本 skill は **`list_panes` の name/role で identity を
> 確認したペインだけを close 対象**とし、sidecar の pane_id を close の直接の根拠にしない。

1. `mcp__org-broker__list_panes` を呼び、`name="attention"` **または** `role="attention"` の
   live pane を**全て**収集する（複数あれば全部）。これを「**確認済み attention ペイン集合**」と
   呼び、各 pane の **数値 pane_id** を控える（**name と role の両方を見る**: 手動起動の孤児
   ペインは name を付けず role だけ持っていることがある）
2. `.state/attention_pane.json` を `Read` で開けたら `pane_id` を読み取る（= **sidecar pane_id**）。
   存在しなければ「sidecar 無し」として扱う
3. sidecar pane_id の identity を `list_panes` の結果に照らして分類する（**sidecar に記録された
   name は信用せず、いま list_panes が返す name/role で判定する**）:
   - **verified**: sidecar pane_id が「確認済み attention ペイン集合」に含まれる
     → その pane はいまも本物の watcher。close 対象
   - **recycled**: sidecar pane_id が list_panes に存在するが、その pane の name/role が
     attention では**ない** → pane_id が無関係なペイン（worker 等）へ再割当てされている。
     **絶対に close しない**
   - **gone**: sidecar pane_id が list_panes に存在しない → watcher は既に消えている
   - **sidecar 無し**: 上記判定はスキップ（孤児ペインの掃除のみ検討する）

   判定が紛らわしい場合（list_panes の name/role が空・曖昧）は
   `mcp__org-broker__inspect_pane(target="<sidecar pane_id>")` で内容を確認して補強してよいが、
   **一次の identity ソースは list_panes が返す name/role** である。

## Step 2: close 対象の決定と実行

**close してよいのは「確認済み attention ペイン集合」のペインだけ**である（identity を確認した
ペインのみ）。sidecar pane_id は **verified のとき（=その id が確認済み集合に入っているとき）に
限り** close 対象になる。recycled / gone の sidecar pane_id は close 対象にしない。

確認済み attention ペイン集合の各 pane を、**list_panes から得た数値 pane_id** で順に close する:

```
mcp__org-broker__close_pane(target="<確認済み集合の数値 pane_id>")
```

- 成功時: `"Closed pane id=N."` テキストが返る
- `[pane_not_found]` / `[pane_vanished]`: 直前に消えた。skip 扱いで次へ
- `[last_pane]`: タブの唯一のペインが attention だった（通常発生しない、dispatcher / secretary が
  残っているはず）。状況をユーザーに報告して abort（手動対応に委ねる）

`target="attention"` のような **name 指定は使わない**（role だけ持って name を持たない孤児ペインに
当たらないため）。必ず数値 pane_id で指定する。

分類別の挙動:

- **verified**: sidecar pane_id は確認済み集合に含まれるので、上記の close で一緒に閉じられる。
  集合に他の attention ペイン（drift / 二重起動の孤児）が居れば、それらも同じく数値 pane_id で
  close する
- **recycled / gone**: sidecar pane_id は **close しない**。ただし確認済み集合に
  （sidecar とは別 id の）孤児 watcher が居れば、それは数値 pane_id で close する。
  sidecar が指していた watcher 本体は既に消えている → Step 4 で「watcher は既に消えていた
  （sidecar が stale）」と報告する
- **sidecar 無し**: 確認済み集合に孤児 attention ペインが居ればそれを数値 pane_id で close する。
  集合が空なら close は何もしない（Step 4 で「既に停止しています」と報告）

## Step 3: sidecar の削除

sidecar が存在した場合は **分類によらず必ず削除する**（verified で close した後も、
recycled / gone で stale と判定した場合も削除する）:

```bash
rm -f .state/attention_pane.json
```

Windows native: `del .state\attention_pane.json` （既に削除済みでも無害化のため `2>nul` 等で抑制）。

journal event を 1 行追記する。**実際に close したペインがある場合のみ** `attention_watch_stopped`
を記録する（recycled / gone で close を 1 つも行わなかった場合は、無関係なペインを停めた誤記録に
ならないよう `<N>` を省き `reason=stale_sidecar` を付ける）:

```bash
# 確認済み attention ペインを close した場合（verified / 孤児掃除）
bash tools/journal_append.sh attention_watch_stopped pane_id=<N>

# sidecar が recycled / gone で close を行わなかった場合
bash tools/journal_append.sh attention_watch_stopped reason=stale_sidecar
```

Windows native では `py -3 tools/journal_append.py attention_watch_stopped ...`。

## Step 4: 報告

**verified（記録された watcher を停止した）/ 孤児掃除した場合**:

```
attention watcher を停止しました（pane id={N}）。
再開するときは /org-attention-start を実行してください。
```

**recycled / gone（sidecar が stale で、watcher 本体は既に消えていた）場合** — 無関係なペインを
close していないことを明示して報告する:

```
attention watcher は既に消えていました（sidecar が stale）。
記録されていた pane id={sidecar pane_id} は別のペインに再割当て済み / 既に消滅していたため、
無関係なペインを閉じないよう close は行わず、stale な sidecar だけを削除しました。
再開するときは /org-attention-start を実行してください。
```

（recycled で別 id の孤児 watcher も掃除した場合は、上に「孤児 attention ペイン id={M} も
併せて掃除しました」を 1 行添える。）

**sidecar が無く孤児ペインも無かった場合**:

```
attention watcher は既に停止しています。
```
