# PR の CI 監視は /pr-watch-pane のみ

- PR の CI チェック / 監視の正規経路は `/pr-watch-pane <PR>` だけ（canonical 記録は events テーブルの `ci_completed` 行）。
- `/pr-watch-pane` が `[split_refused]` 等で立てられないときは、代替監視に流れず**人間に報告して指示を仰ぐ**。自己判断での差し替えは禁止。
- Monitor / Bash（背景・前景とも）での `gh pr checks` polling ループ、および `tools/pr-watch.sh` / `tools/pr-watch.ps1` / `tools/pr_watch.py` の直接起動による代替監視は禁止（PreToolUse フック [`.hooks/block-adhoc-pr-watch.sh`](../../.hooks/block-adhoc-pr-watch.sh) が機械的に deny する）。
- 例外: 単発の `gh pr checks <PR>`（ループ・`--watch` なし）による現在状態の確認は監視ではないので可。
