---
description: PR の CI 監視を /pr-watch-pane に限定し、Bash / Monitor による代替監視を禁じる全体規律（全ファイル適用）
---

# PR の CI 監視は /pr-watch-pane のみ

- PR の CI 監視の正規経路は `/pr-watch-pane <PR>` だけ（canonical 記録は events テーブルの `ci_completed` 行）。`gh pr checks` の polling ループ・`--watch` と `tools/pr-watch.*` / `tools/pr_watch.py` の直接起動は hook（[`.hooks/block-adhoc-pr-watch.sh`](../../.hooks/block-adhoc-pr-watch.sh)）が deny する。単発の `gh pr checks <PR>` による状態確認は可。
- `/pr-watch-pane` が `[split_refused]` 等で立てられないときは、代替監視に流れず人間に報告して指示を仰ぐ。自己判断で別の監視に差し替えない（hook は best-effort ですり抜けを許すので、この規律は文章で守る）。
