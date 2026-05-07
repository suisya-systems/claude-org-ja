# dogfood_pending register

org-delegate Step 1.8 / Issue #338 の paired dogfood follow-up protocol が
参照する SoT。新規 tool / runtime / workflow を導入する PR に対して 1 行追加し、
paired follow-up issue 作成と次回 dogfood pass の earmark を追跡する。

詳細は [`.claude/skills/org-delegate/SKILL.md`](../.claude/skills/org-delegate/SKILL.md) Step 1.8 と
[`.claude/skills/org-delegate/references/dogfood-issue-template.md`](../.claude/skills/org-delegate/references/dogfood-issue-template.md) を参照。

## 更新規約

partial-update register。新規行は append、既存行は単一行内での列追記更新を許可（論理削除 / 行 reorder は禁止）。状態遷移と更新主体は SKILL.md Step 1.8 §register 状態遷移を SoT とする。

## status の意味

- `pending`: 実装 PR は予定 / 起票済だが paired follow-up issue 未作成（org-delegate Step 1.8 §A で append された直後）
- `open`: paired follow-up issue 作成済 / dogfood pass 未実施（org-pull-request 側が pending → open に遷移）
- `consumed`: dogfood pass 実施完了。defect は paired issue に集約済（dogfood pass worker の完了報告受領時に open → consumed）
- `closed`: paired issue クローズ済 / 個別 fix issue に split されたか defect なし（org-pull-request 側が consumed → closed に遷移）

## entries

| task_id | tool / surface | impl_pr | dogfood_issue | dogfood_run_task_id | status |
|---------|----------------|---------|---------------|---------------------|--------|
| <!-- 例: issue-NNN-foo --> | <!-- tools/foo.py --> | <!-- #NNN --> | <!-- #NNN --> | <!-- issue-MMM-bar --> | <!-- pending --> |
