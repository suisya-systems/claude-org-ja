> **このドキュメントは歴史的参考資料です。** Secretary / worker / dispatcher の標準オペレーションでは参照しないでください。標準経路は `python tools/pr-watch.ps1 <PR>` / `python tools/pr-watch.sh <PR>` の merge-watch ループ、もしくは `python tools/run_complete_on_merge.py --pr <PR>` です (Issue #317)。`tools/run_complete_on_merge.py` が想定外の挙動をする場合は手動再現せず Issue を切り、resolver / helper 側のバグが直るまで該当クローズを pause してください。例外的に手作業を行うかどうかはユーザーの明示判断に委ね、Secretary が自走で本ドキュメントに reach した場合は protocol 違反です。

# Legacy hand-rolled PR-merge completion (museum copy)

This file preserves the pre-Issue-317 manual completion snippet for archaeological reference. It was extracted from `.claude/skills/org-delegate/SKILL.md` Step 5 2b-ii (PR #315 と同じ externalization pattern)。

## Why this is no longer in the active skill

Documenting a hand-rolled `python -c` block inside the active skill behaved like an "easy button": when the secretary observed a PR merge, they would copy the snippet and run it ad-hoc, which had several historical failure modes:

- **Forgotten `pr_state='merged'`** — the legacy snippet only called `update_run_status('<task_id>', 'completed')`. `runs.pr_state` stayed at `'open'`, so dashboard / queries that filter on `pr_state` showed inconsistent state. `tools/run_complete_on_merge.py` writes `pr_state='merged'`, `commit_short`, `pr_url`, and `completed_at` from the `gh pr view` payload in one transaction.
- **Missing `pr_merged` event** — the snippet did not append an event row, so the journal had no record of *why* the run transitioned to completed. The helper appends a single `pr_merged` event with PR / repo / merge_commit / merged_at in the payload, and is idempotent (a second invocation does not double-write).
- **Manual `mergedAt` confirmation** — the secretary had to call `gh pr view --json mergedAt` themselves and decide whether the PR was actually merged. The merge-watch loop in `tools/pr_watch.py` handles this end-to-end.
- **No completed_at** — the snippet did not pass `completed_at`, so `runs.completed_at` stayed NULL and downstream "time-to-merge" queries lost data. The helper threads the PR's `mergedAt` directly into `update_run_status(..., completed_at=...)`.

Today, if `tools/run_complete_on_merge.py` errors or produces a wrong row, the canonical response is to **file an Issue against the helper and pause the affected close until the underlying bug is fixed**. Whether to invoke any manual workaround at all is a user judgment call — Secretary must not self-grant the exception.

## Legacy procedure (verbatim, do not use)

```bash
python -c "
from pathlib import Path
from tools.state_db import connect
from tools.state_db.writer import StateWriter
conn = connect('.state/state.db')
with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
    w.update_run_status('<task_id>', 'completed')
    # パターン B / C のエントリ削除はここで w.remove_worker_dir('<abs>') を追加
"
```

Operationally that meant:

- 窓口 が `gh pr view <PR> --json mergedAt` 等で merge を確認
- 上記 inline Python を `<task_id>` 置換して実行
- 必要なら `bash tools/journal_append.sh pr_merged ...` を別途叩いて event 行を残す（実際にはしばしば忘れられた）
- パターン B では同じスクリプト内で `w.remove_worker_dir('<abs>')` を追記

The legacy block did **not** set `pr_url` / `pr_state` / `commit_short` / `completed_at`, did **not** append a `pr_merged` event, and did **not** verify mergedAt against `gh pr view`; all four are now done in one transaction by `tools/run_complete_on_merge.py`.
