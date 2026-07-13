# Ack template — Secretary → worker peer message

Secretary が worker からの peer message を受け取ったときの ack（acknowledgement）文面テンプレート。SKILL.md Step 5 の "Canonical event flow" の **step 1** で必ず発行する。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../../CLAUDE.md)「輸送層（transport）両系」節。）

## なぜ ack が必須か

- worker-claude-template は完了 / 進捗報告の末尾に「ペイン保持。次の指示お待ちします」を書くよう指示している
- Secretary が ack を返さないと worker は「Secretary に届いたか / 次の指示は何か」が判らないまま idle で待ち続ける（dead-lock）
- session #13 で実際に観測された失敗モード — Secretary は「user に報告した」を「worker への返答」と誤認していた
- ack ≠ user 承認。ack は dead-lock 解除のための受領確認であり、push/PR 権限を生まない

## ack の最低内容（必須 3 要素）

1. **受領確認**: 「受領」「了解」「確認」等
2. **次の予定**: PR 作成は user 承認後 / CI 結果待ち / 追加レビュー要否 / 人間判断待ち 等
3. **ペイン状態**: 保持 / クローズ予定

## 種別ごとの例文

### 進捗報告 ack

```
mcp__org-broker__send_message(
  to_id="worker-{task_id}",
  message="進捗受領しました。続行 OK。完了したら同じ to_id=\"secretary\" で報告してください。ペイン保持で。"
)
```

進捗に限らず、rebase 報告・確認依頼など完了 (2a) / 判断仰ぎ (0) / plan・prep 引き渡し（専用 kind `plan_delivered` / `prep_delivered` で記帳する既存フロー）以外の中間ハンドオフ報告はすべてこの ack を使う。ack 後は SKILL.md Step 5 (1) に従い、Progress Log 追記に加えて `worker_reported` の journal 追記（`bash tools/journal_append.sh worker_reported worker=worker-{task_id} task={task_id} summary="<要約>"`）を必ず行う（記帳漏れは dispatcher の PANE_OUTPUT_WITHOUT_PEER_MSG 誤検知の原因、Issue #699）。

### 完了報告 ack（PR 未作成時）

```
mcp__org-broker__send_message(
  to_id="worker-{task_id}",
  message="完了報告受領しました。これから user に報告し、承認を取ってから push / PR 作成を Secretary 側で行います。ペイン保持で待機してください。CI / レビュー指摘が来たら同ペインで対応依頼を送ります。"
)
```

（full 完了報告に「人間向け理解サマリ」が欠落していた場合は特別な ack を設けず、通常の review feedback として同ペインに補完を依頼する。手順は [`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) Step 5 (2a) / [`.claude/skills/org-pull-request/SKILL.md`](../../org-pull-request/SKILL.md) 2c を参照。）

### Codex セルフレビュー round 完了 ack

```
mcp__org-broker__send_message(
  to_id="worker-{task_id}",
  message="Codex round 受領しました。Blocker/Major があれば修正コミット → 次 round へ。3 round 上限まで進めて OK。残りが Minor/Nit のみなら最終完了報告に切り替えてください。ペイン保持で。"
)
```

### 判断仰ぎ ack（escalation）

```
mcp__org-broker__send_message(
  to_id="worker-{task_id}",
  message="判断仰ぎ受領しました。Secretary では一次承認しません。人間に確認します。返答が来るまでペイン保持で待機してください（自動続行しないこと）。"
)
```

判断仰ぎの場合は SKILL.md Step 5 の subsection 0 に従い、Progress Log 追記 + `worker_escalation` journal 追記 + `pending_decisions append` も並行して行う。ack はそれらと独立した worker 宛のフィードバック。

### ブロック報告 ack

判断仰ぎ ack と同じ文面で OK。ブロック内容によっては「該当タスクは中断、別 worker に振り直すかも」「ペインクローズ予定」と次の予定を明示する。

## アンチパターン

- ❌ ack を省略して run.status 更新と user 報告だけ行う → worker dead-lock
- ❌ ack の内容に「OK、push します」と書いて user 承認前に push を発行 → user approval gate 違反（SKILL.md Step 5 (2a→2b) gate）
- ❌ user 承認後の "進めます" 通知を ack の代わりにする → 完了報告から user 承認までのタイムラグの間 worker は idle で待つことになる。ack は受信直後に即発行する
