# Ack template — Secretary → worker peer message

Secretary が worker からの peer message を受け取ったときの ack（acknowledgement）文面テンプレート。SKILL.md Step 5 の "Canonical event flow" の **step 1** で必ず発行する。

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
mcp__renga-peers__send_message(
  to_id="worker-{task_id}",
  message="進捗受領しました。続行 OK。完了したら同じ to_id=\"secretary\" で報告してください。ペイン保持で。"
)
```

### 完了報告 ack（PR 未作成時）

```
mcp__renga-peers__send_message(
  to_id="worker-{task_id}",
  message="完了報告受領しました。これから user に報告し、承認を取ってから push / PR 作成を Secretary 側で行います。ペイン保持で待機してください。CI / レビュー指摘が来たら同ペインで対応依頼を送ります。"
)
```

### Codex セルフレビュー round 完了 ack

```
mcp__renga-peers__send_message(
  to_id="worker-{task_id}",
  message="Codex round 受領しました。Blocker/Major があれば修正コミット → 次 round へ。3 round 上限まで進めて OK。残りが Minor/Nit のみなら最終完了報告に切り替えてください。ペイン保持で。"
)
```

### 判断仰ぎ ack（escalation）

```
mcp__renga-peers__send_message(
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
