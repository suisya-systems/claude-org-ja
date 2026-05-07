# dogfood_pending register

org-delegate Step 1.8 / Issue #338 の paired dogfood follow-up protocol が
参照する SoT。新規 tool / runtime / workflow を導入する PR に対して 1 行追加し、
paired follow-up issue 作成と次回 dogfood pass の earmark を追跡する。

詳細は [`.claude/skills/org-delegate/SKILL.md`](../.claude/skills/org-delegate/SKILL.md) Step 1.8 と
[`.claude/skills/org-delegate/references/dogfood-issue-template.md`](../.claude/skills/org-delegate/references/dogfood-issue-template.md) を参照。

## status の意味

- `pending`: 実装 PR は予定 / 起票済だが paired follow-up issue 未作成（窓口が append した直後）
- `open`: paired follow-up issue 作成済 / dogfood pass 未実施
- `consumed`: dogfood pass 実施完了。defect は paired issue に集約済
- `closed`: paired issue クローズ済（個別 fix issue に split されたか defect なし）

## entries

| task_id | tool / surface | impl_pr | dogfood_issue | dogfood_run_task_id | status |
|---------|----------------|---------|---------------|---------------------|--------|
| <!-- 例: issue-NNN-foo --> | <!-- tools/foo.py --> | <!-- #NNN --> | <!-- #NNN --> | <!-- issue-MMM-bar --> | <!-- pending --> |
