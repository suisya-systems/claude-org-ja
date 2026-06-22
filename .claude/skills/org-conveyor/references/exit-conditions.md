# 機械的退出条件（exit conditions）まとめ

[`/org-conveyor`](../SKILL.md) は完了駆動で自走するが、**5 つの機械的退出条件** のいずれかに該当したら
自走を止めて人間へ戻す。退出は失敗ではなく **安全装置**: 自動判断の射程を超えた状況（churn / 想定外 /
スコープの縁）を機械的に検出し、ベルトを止めて人間の目を入れる。退出予算はスコープ契約
（[`.claude/skills/org-conveyor/references/scope-contract.md`](scope-contract.md)）に明記し、引き継ぎ後も読み戻せるようにする。

## 退出条件一覧

| # | 条件 | 既定閾値 | 何を見るか |
|---|---|---|---|
| 1 | **Codex round 最大数到達** | `codex_round_max`（既定 3） | ある PR の Codex セルフレビュー fix が上限ラウンドに達しても Blocker/Major が収束しない |
| 2 | **連続 false-positive 数到達** | `false_positive_streak_max`（既定 2） | CI red / Codex Blocker / verify 失敗を追ったが、いずれも変更起因の実欠陥でなかった（flaky / benign）回が連続 |
| 3 | **時間予算超過 / 最大反復数到達** | `time_budget` / `max_iterations` | ベルト稼働の経過時間 / ループ反復回数が契約の予算を超えた |
| 4 | **worker escalation** | 即時 | worker から「判断仰ぎ」「承認を仰ぎ」「スコープ拡張」「ブロッカー」「想定外」「runbook 逸脱」が来た |
| 5 | **scope 縁検知** | 即時 | スコープ述語に非合致 / 判定不能の候補・差分、または org-delegate チェック項目が人間入力を要求、verify 判定不能 |

## 各条件の詳細

### 1. Codex round 最大数到達

- worker の Codex セルフレビューは Blocker/Major ゼロまで回すが上限 3 ラウンド（[`/org-delegate`](../../org-delegate/SKILL.md)
  「ワーカー監視と介入判定」: Codex 4 ラウンド目以降は介入トリガー）。conveyor はこれを **ベルト退出条件としても扱う**:
  ある PR が上限ラウンドで収束しないなら、その候補はベルトから外し人間へ上げる（残りの候補のベルトは続けてよい）。
- 「収束しない」を機械的に: 同一 PR で Codex round が `codex_round_max` 回を超えても Blocker/Major が残る。

### 2. 連続 false-positive 数到達

- conveyor の自動 gate（CI 解釈 / Codex Blocker / verify 失敗）が **halt 候補シグナルを出したが、追ったら変更起因の
  実欠陥ではなかった**（flaky CI / benign な Codex 指摘 / 環境起因の verify 失敗）回を **false-positive** と数える。
- 実欠陥を 1 つでも掴んだら連続カウンタはリセット。**連続**して `false_positive_streak_max` 回 false-positive が続いたら、
  自動 gate のシグナルが信頼できない状態なので halt（誤シグナルで churn し続けない / `feedback-no-stopgap`）。
- カウンタは TaskList のメタや conveyor 制御タスクに紐づけて引き継げる形で持つ（観測可能性節）。

### 3. 時間予算超過 / 最大反復数到達

- `time_budget`（壁時計）または `max_iterations`（ループ周回数）の超過で halt。長時間の無人自走を青天井にしない。
- 経過の起点はスコープ契約の `approved_at`（または最初の反復開始時）。反復数は観測可能性サマリの出力回数に一致させると数えやすい。

### 4. worker escalation

- worker からの judgment escalation / scope 拡張 / ブロッカーは **即 halt** し、conveyor が一次承認せず
  [`/org-escalation`](../../org-escalation/SKILL.md) の正準フロー（ack → 3 層状態保存 → 人間伝達 → 返答転送）へ渡す（INV-3）。
- 該当 worker のベルト枠は人間判断が返るまで保留。他候補のベルトは独立に続けてよい。

### 5. scope 縁検知

- 以下はすべて scope 縁 → halt（INV-2）:
  - triage 候補がスコープ述語に **非合致 / 判定不能**（[`.claude/skills/org-conveyor/references/scope-contract.md`](scope-contract.md)）。
  - [`/org-delegate`](../../org-delegate/SKILL.md) の委譲前チェック（曖昧用語 / OS 前提 / incorporation 戦略等）が
    **人間入力を要求**した（conveyor が代わりに埋めない）。
  - verify の applicability classifier が **判定不能**（[`.claude/skills/org-conveyor/references/verify-evidence.md`](verify-evidence.md)）。
- スコープを使い切り、残り候補が外側しか無いなら、それも「これ以上自走できる範囲が無い」= 正常退出として人間へ報告する。

## halt 時の共通挙動

- 自走を止め、**観測可能性サマリ（[`.claude/skills/org-conveyor/SKILL.md`](../SKILL.md)「観測可能性」節）+ 退出理由 + 該当 PR/候補** を人間へ提示する。
  「ベルトのどこで何が起きて止まったか」を 1 枚で残す。
- **退出条件は自動解決しない**（INV-3 / INV-6）。merge / scope 拡張 / escalation の解消はすべて人間ゲート。
- 進行中の他 worker・未 merge の PR キューは halt しても消えない（人間が個別に merge / 対応できる）。
- 人間がスコープを更新（拡大は再承認、[`.claude/skills/org-conveyor/references/scope-contract.md`](scope-contract.md)）すればベルトを再開できる。
