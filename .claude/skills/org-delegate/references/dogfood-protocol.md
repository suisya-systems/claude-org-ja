# dogfood follow-up issue protocol（窓口 + org-pull-request 連携、Issue #338）

> **一次参照元**: [`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) Step 1.8（適用判定の概要のみ）。本ドキュメントは適用条件・窓口責務 A/B・org-pull-request 連携・register フォーマット・状態遷移・consumed→closed hygiene の詳細 SoT。

新規 tool / runtime / workflow を導入する PR では、実装 PR と paired で「dogfood follow-up」issue を作成し、次回その新規 tool を実使用する delegation を **dogfood pass** として明示的に予約する。Curator session #18 retrospective で「PR #288 で 4 カテゴリの defect が初回実使用時にしか出てこなかった」事象（session #11 でも再現）に基づく protocol。

## 適用条件

タスクが以下のいずれかに該当する場合に発動:

- 新規 CLI tool / script (`tools/*.py`, `tools/*.sh`, `tools/*.ps1` 等) の追加
- 新規 runtime / 新規 workflow / 新規 protocol の導入
- 既存 tool への break-change を伴う再設計

## 窓口（org-delegate）の責務

dogfood protocol は **2 つの delegation** に跨る: (A) 新規 tool を導入する **実装 delegation**, (B) その後その tool を実使用する **dogfood pass delegation**。窓口は両方で `registry/dogfood_pending.md` を読み書きする。

### (A) 実装 delegation の起票時（Step 1.7 評価と同タイミング）

1. 適用条件に該当することを判定し、preview と並行で「dogfood 対象タスク」とマーク
2. `registry/dogfood_pending.md` に新規行を 1 行 append し、`status=pending` / `dogfood_issue` / `dogfood_run_task_id` は空 / `impl_pr` は空（PR 番号は後で埋める）。この時点では実装 PR 自体まだ存在しない
3. 実装 worker への brief には dogfood の言及は不要（issue 番号も PR 番号もこの時点では未確定）。実装 worker は通常通り tool を作るだけ

### (B) dogfood pass delegation の起票時

4. 新規 delegation を起こす際は、毎回 `registry/dogfood_pending.md` の `status=open` 行（= paired follow-up issue 作成済 / dogfood pass 未実施）を確認する
5. 起票しようとしている新規 task が `tool / surface` 列の対象を実使用する場合、その task を dogfood pass として earmark:
   - `apply` 呼び出しに `--impl-guidance "Dogfood pass for paired follow-up issue #<N>. Report any defects to that issue using the format in references/dogfood-issue-template.md. Refs #<N>, do not Closes."` を追加する
   - 追加で `--knowledge .claude/skills/org-delegate/references/dogfood-issue-template.md` を渡し、defect 報告フォーマットを brief に含める
6. 該当行を更新: `dogfood_run_task_id=<新規 task_id>` を埋め、`status` は `open` のまま据え置き（dogfood worker からの完了報告を受領した時点で `consumed` に遷移、 §register 状態遷移参照）

## org-pull-request 側の責務（cross-ref）

実装 PR 作成 / マージのタイミングで以下を行う（手順詳細は org-pull-request 側で別途整備、Issue #338 は本 protocol を記録するスコープ）:

1. 実装 PR 作成直後: `registry/dogfood_pending.md` で `status=pending` の該当行を探し、`impl_pr=#<NNN>` を埋め、`gh issue create --body-file <rendered template>` で paired follow-up issue を作成（template: [`dogfood-issue-template.md`](dogfood-issue-template.md)）
2. 作成した issue 番号を該当行の `dogfood_issue=#<MMM>` に埋め、`status` を `pending → open` に遷移
3. 実装 PR の本文末に `Paired dogfood issue: #<MMM>` を付ける
4. paired issue がクローズされた時点で該当行の `status` を `consumed → closed` に遷移

## dogfood_pending register フォーマット

`registry/dogfood_pending.md` は **append-only ではなく partial-update register**: 行追加は append、各列（`impl_pr` / `dogfood_issue` / `dogfood_run_task_id` / `status`）への追記更新は許可。論理削除や行の reorder は禁止。

```
| task_id | tool / surface | impl_pr | dogfood_issue | dogfood_run_task_id | status |
|---------|----------------|---------|---------------|---------------------|--------|
| issue-XXX-new-tool | tools/foo.py | #YYY | #ZZZ | issue-MMM-bar | open |
```

## register 状態遷移

```
[行追加] (org-delegate Step 1.8 §A.2)
  status = pending      ← issue 未作成 / impl_pr も空
       │
       │ 実装 PR 作成 + paired issue 作成 (org-pull-request §1-2)
       ▼
  status = open         ← paired issue 作成済 / dogfood pass 未実施
       │
       │ 後続 delegation で earmark (org-delegate Step 1.8 §B.5-6)
       │ dogfood_run_task_id を埋める。status は open のまま
       │
       │ dogfood pass worker 完了報告受領 → defect が paired issue に集約済
       ▼
  status = consumed     ← defect 監視期間
       │
       │ paired issue クローズ (org-pull-request §4)
       ▼
  status = closed       ← 終端
```

各遷移は表の単一行に対する **単一列の差分書き換え**。複数列を同時に書き換える場合（例: pending → open は `impl_pr` と `dogfood_issue` と `status` を一括更新）も同一行内なら可。

## consumed → closed 観察タイミング（窓口の register hygiene 責務）

paired follow-up issue のクローズは実装 PR のライフサイクル外で起こりうるため（手動 close / 個別 fix issue への split / 長期 idle 後の整理）、`org-pull-request` の発動契機（PR 作成・レビュー・マージ後クローズ）だけでは検出漏れが起きる。窓口は **`registry/dogfood_pending.md` に書き込みを行うあらゆるタイミング**（= 実装 delegation 起票 / dogfood pass earmark / dogfood pass 完了報告受領 / 状態確認）で次の hygiene チェックを行う:

```bash
# status=consumed の行について、paired dogfood_issue が closed なら closed に遷移
gh issue view <dogfood_issue> --json state -q .state
  # → "CLOSED" なら status を consumed → closed に書き換える
```

加えて、`/org-resume` 起動時のブリーフィングでも `status=consumed` の行を 1 度ずつスキャンして閉じる（resume 時 hygiene）。これにより consumed が register に滞留しても、次のレジスタ操作までに必ず回収される。
