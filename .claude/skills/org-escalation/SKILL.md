---
name: org-escalation
description: >
  ワーカーから「判断仰ぎます」「承認を仰ぎます」「続行可否」「スコープ拡張提案」
  「想定外」「runbook 逸脱」「ブロック / ブロッカー」等の peer message を受信した際、
  窓口は一次承認せず人間にエスカレーションする。発動条件は worker からの judgment-escalation /
  scope-expansion / blocker メッセージ受信時。
  通常の進捗 / 完了報告は org-delegate Step 5 (1) / (2a) で扱う。本スキルは
  「自己解釈で承認しないための register 更新を含む正準フロー」を担当する。
---

# org-escalation: 判断仰ぎ・スコープ拡張・ブロッカーのエスカレーション

ワーカーから「承認を仰ぎます」「判断仰ぎます」「続行可否を確認」「スコープ拡張」「提案」「想定外」「runbook 逸脱」「ブロック」「ブロッカー」「block」等を含む peer message を受け取ったとき、窓口は **一次承認せず** 人間にエスカレーションする。窓口は伝言役であり判断レイヤーではない。

> **状態保存の必要性**: 窓口再起動・引き継ぎで pending 判断を失わないため、
> Progress Log / events / pending-decisions register の 3 層に同時記録する。
> どれが欠落してもディスパッチャーの SECRETARY_RELAY_GAP_SUSPECTED 検出
> ([`../../../.dispatcher/references/worker-monitoring.md` Step 5.1](../../../.dispatcher/references/worker-monitoring.md#step-5-1)) が誤発火 / 見逃しになる。

> **ack template SoT**: 判断仰ぎ ack の最低 3 要素・例文・アンチパターンは
> [`../org-delegate/references/ack-template.md`](../org-delegate/references/ack-template.md) を一次参照する。本 SKILL は重複コピーを置かず、リンクで委譲する。

## 正準フロー

1. **最初に worker へ ack を返す**（状態保存・user 伝達より前に発行する）。文面は [`../org-delegate/references/ack-template.md`](../org-delegate/references/ack-template.md) §「判断仰ぎ ack」を一次参照する（本 SKILL は重複コピーを置かない）。
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

3. **人間に伝達する**: 内容と選択肢を整理して提示する。提示直後に register を `escalated` に更新:
   ```bash
   python tools/pending_decisions.py resolve --task-id {task_id} --kind to_user
   ```

4. **ユーザーから返答を受領した時点** — ワーカーへ転送する **前に** `user_replied_at` marker を register に記録する (Issue #301):
   ```bash
   python tools/pending_decisions.py mark-user-replied --task-id {task_id}
   ```
   escalated entry が無ければ no-op、既に設定済みでも idempotent。これにより [`../../../.dispatcher/references/worker-monitoring.md` Step 5.1 (a-2)](../../../.dispatcher/references/worker-monitoring.md#step-5-1) で「ユーザー返答済みなのに Secretary が転送忘れ」を deterministic に検知できる

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
