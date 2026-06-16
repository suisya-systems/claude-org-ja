---
name: org-escalation
description: >
  ワーカーから「判断仰ぎます」「承認を仰ぎます」「続行可否」「スコープ拡張提案」
  「想定外」「runbook 逸脱」「ブロック / ブロッカー」等の peer message を受信した際、
  窓口は一次承認せず人間にエスカレーションする。発動条件は worker からの judgment-escalation /
  scope-expansion / blocker メッセージ受信時。
  通常の進捗 / 完了報告は org-delegate Step 5 (1) / (2a) で扱う。本スキルは
  「自己解釈で承認しないための register 更新を含む正準フロー」を担当する。
effort: medium
allowed-tools:
  - Read
  - Edit
  - Bash(bash tools/journal_append.sh:*)
  - Bash(python tools/pending_decisions.py:*)
  - Bash(py -3 tools/pending_decisions.py:*)
  - mcp__org-broker__send_message
---

# org-escalation: 判断仰ぎ・スコープ拡張・ブロッカーのエスカレーション

ワーカーから「承認を仰ぎます」「判断仰ぎます」「続行可否を確認」「スコープ拡張」「提案」「想定外」「runbook 逸脱」「ブロック」「ブロッカー」「block」等を含む peer message を受け取ったとき、窓口は **一次承認せず** 人間にエスカレーションする。窓口は伝言役であり判断レイヤーではない。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節。）

> **状態保存の必要性**: 窓口再起動・引き継ぎで pending 判断を失わないため、
> Progress Log / events / pending-decisions register の 3 層に同時記録する。
> どれが欠落してもディスパッチャーの SECRETARY_RELAY_GAP_SUSPECTED 検出
> ([`../../../.dispatcher/references/worker-monitoring.md` Step 5.1](../../../.dispatcher/references/worker-monitoring.md#step-5-1)) が誤発火 / 見逃しになる。

> **ack template SoT**: 判断仰ぎ ack の最低 3 要素・例文・アンチパターンは
> [`.claude/skills/org-delegate/references/ack-template.md`](../org-delegate/references/ack-template.md) を一次参照する。本 SKILL は重複コピーを置かず、リンクで委譲する。

## 正準フロー

1. **最初に worker へ ack を返す**（状態保存・user 伝達より前に発行する）。文面は [`.claude/skills/org-delegate/references/ack-template.md`](../org-delegate/references/ack-template.md) §「判断仰ぎ ack」を一次参照する（本 SKILL は重複コピーを置かない）。
   - **Secretary は一次承認しない**。worker への返答も「受領しました、人間に確認します」のみ
   - 「ユーザーは選択肢 X を選んだから自動的に含意される」「一気通貫の意図に含まれる」等の自己解釈で承認してはならない（CLAUDE.md `feedback_relay_user_decisions_to_workers` 参照）
   - ack ≠ user 承認: ack は worker dead-lock 解除のための受領確認であり、push/PR 権限を生まない

2. **状態を保存する**（3 層同時、欠落禁止）:
   - `.state/workers/worker-{task_id}.md` の Progress Log に「判断仰ぎ受信」内容と要点を追記
   - DB の events テーブルに追記:
     ```bash
     bash tools/journal_append.sh worker_escalation worker=worker-{task_id} task={task_id} reason="<要約>"
     ```
   - **pending-decisions register に追加** (Issue #297):
     ```bash
     python tools/pending_decisions.py append --task-id {task_id} --message "<本文要約>"
     ```
     同 task_id の pending entry が既存なら idempotent (no-op)。register はディスパッチャーの SECRETARY_RELAY_GAP_SUSPECTED 検出 ([`../../../.dispatcher/references/worker-monitoring.md` Step 5.1](../../../.dispatcher/references/worker-monitoring.md#step-5-1)) の primary lookup source

3. **人間に伝達する**: 内容と選択肢を整理して提示する。**選択肢を提示した時点（ask の瞬間）** で、register を `escalated` に更新する直前/同時に attention watcher へ awaiting_user を即時 emit する（Issue #28、ask-time ゲート）:
   ```bash
   bash tools/journal_append.sh notify_sent kind=awaiting_user task_id={task_id} gate=escalation_to_user note="<選択肢提示の短い要約>"
   python tools/pending_decisions.py resolve --task-id {task_id} --kind to_user
   ```
   classifier が `secretary_awaiting_user` (default severity `urgent`) として拾い、判断を仰がれた瞬間に即ビープする。interactive ではユーザーが数十秒〜数分で返答するため pending_decision aging (15分) は実質発火せず、この ask-time emit が urgent 通知の主経路となる。Step 4.5 (`escalation_reply_forward`, 転送時) の emit はそのまま別タイミングとして残す（Step 3=ask 時 / 4.5=転送時）。本 emit は journal 1 行追記のみで register / pending_decisions の状態は触らない。

4. **ユーザーから返答を受領した時点** — ワーカーへ転送する **前に** `user_replied_at` marker を register に記録する (Issue #301):
   ```bash
   python tools/pending_decisions.py mark-user-replied --task-id {task_id}
   ```
   escalated entry が無ければ no-op、既に設定済みでも idempotent。これにより [`../../../.dispatcher/references/worker-monitoring.md` Step 5.1 (a-2)](../../../.dispatcher/references/worker-monitoring.md#step-5-1) で「ユーザー返答済みなのに Secretary が転送忘れ」を deterministic に検知できる

4.5. **awaiting_user 通知の emit（Issue #28）**: `mark-user-replied` → `resolve --kind to_worker` の境界で、attention watcher に「ユーザー返答が secretary 側に着き、worker へ転送する間の secretary 側 user-driven 動作」を知らせる 1 行を emit する:
   ```bash
   bash tools/journal_append.sh notify_sent kind=awaiting_user task_id={task_id} gate=escalation_reply_forward note="<decision の短い要約>"
   ```
   並走 runtime PR の classifier が `secretary_awaiting_user` (default severity `urgent`) として拾う。CLAUDE.md「secretary が user の判断を待っている状態を通知する」節を参照。本 emit は escalated entry の有無に依らず副作用を残さない（journal 1 行追記のみ、register / pending_decisions は触らない）

5. **ワーカーに人間判断を転送する** (`to_id="worker-{task_id}"` で `send_message`)。伝達直後に register を `resolved` に更新:
   ```bash
   python tools/pending_decisions.py resolve --task-id {task_id} --kind to_worker
   ```

## 重複保険ポリシー

- Progress Log / journal events / pending-decisions register の 3 層は **重複保険として独立に維持**する
- register への append / resolve のどちらかが欠落するとディスパッチャー側で SECRETARY_RELAY_GAP_SUSPECTED が誤発火（中継済みなのにアラート）または見逃し（中継忘れを検知できない）になる
- ブロッカー報告も本スキルで扱う。「3. ブロック報告」と判断仰ぎが重複した場合は本フローを優先する

## 発動条件外（本スキルで扱わないもの）

- 進捗報告（Progress Log 追記 + ack のみ。ユーザー報告・承認待ちは行わない） → `.claude/skills/org-delegate/SKILL.md` Step 5 (1)
- 完了報告（REVIEW 遷移 + ユーザー報告 + 承認待ち） → `.claude/skills/org-delegate/SKILL.md` Step 5 (2a)
- ユーザー承認後の push / PR 作成 / レビューループ / マージ後クローズ → `.claude/skills/org-pull-request/SKILL.md`
- ワーカー監視と介入判定（深掘り検出・Esc 中断・tight 修正指示）→ `.claude/skills/org-delegate/SKILL.md` 「ワーカー監視と介入判定」
