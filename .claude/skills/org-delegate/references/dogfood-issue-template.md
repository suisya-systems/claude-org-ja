# Dogfood follow-up issue template

org-delegate Step 1.8 / Issue #338 で「新規 tool / runtime / workflow を導入する PR」と paired で作成する follow-up issue の本文テンプレート。`gh issue create --title "dogfood follow-up: <tool>" --body-file <this rendered>` で使う想定。

source PR を必ず Refs し、初回実使用 (dogfood pass) で観測した defect をこの issue に集約する。defect が出揃ったら個別 fix issue へ split / close する。

---

## Title

```
dogfood follow-up: <tool / surface name>
```

例: `dogfood follow-up: tools/gen_delegate_payload.py`、`dogfood follow-up: codex companion runtime`

## Body template

```markdown
## Source

- Implementation PR: #<NNN>
- Originating org-delegate task: `<task-id>`
- Introduced surface: `<file path / module / runtime name>`

## Why this issue exists

This is a paired dogfood follow-up created by org-delegate Step 1.8.
The implementation PR introduces a new tool / runtime / workflow whose
real-world failure modes are not fully observable from unit tests or
self-review. We earmark the next delegation that uses this surface as a
**dogfood pass** and report any defects observed there to this issue
before they spread.

## Expected validation surface

<!-- What aspects we expect the dogfood pass to actually exercise.
     Fill in at issue creation time so the dogfood worker has a checklist. -->
- [ ] <例: 実際の Pattern A タスクで brief が壊れず生成されるか>
- [ ] <例: --skip-settings なしで claude-org-runtime と整合するか>
- [ ] <例: 失敗時にエラーメッセージから原因特定可能か>
- [ ] <例: 既存スキル / 既存 CLAUDE.md と衝突しないか>

## Defect reporting format

Append findings as comments on this issue using the following block:

```
### Defect <N>: <one-line summary>

- Severity: Blocker | Major | Minor | Nit
- Observed in: dogfood task `<task-id>` / commit `<SHA>` / PR #<NNN>
- Repro: <minimal steps>
- Expected: <what should have happened>
- Actual: <what did happen>
- Suspected cause: <if any> 
- Proposed fix: <if known> 
```

## Dogfood pass tracking

| field | value |
|---|---|
| dogfood_run_task_id | <set by Secretary when earmarked> |
| dogfood_run_pr | <set after dogfood worker opens PR> |
| status | open / consumed / closed |

`registry/dogfood_pending.md` の該当行と一致させること。

## Closing criteria

- 1 round の dogfood pass が完了している
- 検出された Blocker / Major は個別 fix issue に split されている
- Minor / Nit はこの issue に残置 OK だが、accumulate しすぎたら整理 issue を切る

Refs #<source PR>
```

---

## Notes

- `gh issue create` は **org-pull-request 側** が PR 作成と同タイミングで発行する。org-delegate は `registry/dogfood_pending.md` への append のみを行う（org-delegate Step 1.8 §窓口の責務）。
- `Closes` は使わない。実装 PR がマージされても dogfood 観察期間は続くため、`Refs #<source PR>` のみ。
- 検出された defect が複数カテゴリに渡る場合は、follow-up issue を 1 件に集約しつつ個別 fix issue を split する（PR #288 の 4 カテゴリ defect が原型）。
