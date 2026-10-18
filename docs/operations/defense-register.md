# 防御の一覧（どの失敗を止めるための仕組みか）

失敗を止めるために足した仕組み（hook・テスト・`tools/` の検査）を 1 行ずつ並べた一覧。
`/org-retro` が足す検査はここへの追加を完了条件にする（[`/org-retro`](../../.claude/skills/org-retro/SKILL.md) Step 2.5）。
直近 30 日の止めた回数は `python tools/defense_report.py` が、この表と events から出す（Step 6）。

書式（`tools/defense_report.py` が読む）: 行頭が `|` の表。列は `id` / 種別 / パス / 止める失敗。
`id` は止めた記録の `hook` 欄と突き合わせる名前で、hook は拡張子を除いたファイル名にする。
種別は `hook` / `test` / `tool` のいずれか。

| id | 種別 | パス | 止める失敗 |
|----|------|------|-----------|
| block-adhoc-pr-watch | hook | `.hooks/block-adhoc-pr-watch.sh` | PR の CI 監視を `/pr-watch-pane` 以外の場当たりなループで回す |
| block-blind-spawn-keys | hook | `.hooks/block-blind-spawn-keys.sh` | 画面を確かめずに `send_keys` を打ち、ワーカーを終了させる |
| block-close-before-retro | hook | `.hooks/block-close-before-retro.sh` | 振り返りの完了報告ゲートを通る前にワーカーペインを閉じる |
| block-codex-over-cap | hook | `.hooks/block-codex-over-cap.sh` | Codex レビューの往復上限を超える・codex を直接実行する |
| block-codex-rescue | hook | `.hooks/block-codex-rescue.sh` | `codex:rescue` を Skill 経由で呼ぶ |
| block-dangerous-git | hook | `.hooks/block-dangerous-git.sh` | 破壊的な git 操作で作業を失う |
| block-dispatcher-out-of-scope | hook | `.hooks/block-dispatcher-out-of-scope.sh` | ディスパッチャーが業務スコープ外のファイルを編集する |
| block-foreground-subagent | hook | `.hooks/block-foreground-subagent.sh` | subagent を前景で起動して窓口の接点をふさぐ |
| block-git-push | hook | `.hooks/block-git-push.sh` | ワーカーが push や GitHub 書き込みをする |
| block-no-verify | hook | `.hooks/block-no-verify.sh` | commit / push の検査を迂回するフラグや環境変数を使う |
| block-org-structure | hook | `.hooks/block-org-structure.sh` | claude-org の組織構造ディレクトリを作業ディレクトリ内に再現する |
| block-relative-close-pane | hook | `.hooks/block-relative-close-pane.sh` | `focused` や裸の名前で `close_pane` / `set_pane_identity` を指定し別ペインを触る |
| block-workers-delete | hook | `.hooks/block-workers-delete.sh` | workers ディレクトリを再帰削除する |
| check-dispatcher-loop | hook | `.hooks/check-dispatcher-loop.sh` | ディスパッチャーの監視ループが未武装のまま運転が続く（通知のみ・止めない） |
| check-loop-directive | hook | `.hooks/check-loop-directive.sh` | 監視ループを canonical 正文でなく自己流の指示で武装する |
| check-worker-boundary | hook | `.hooks/check-worker-boundary.sh` | ワーカーが自分のディレクトリ外を編集する |
| check-worker-spawn-model | hook | `.hooks/check-worker-spawn-model.sh` | DELEGATE の指定なしにワーカーのモデルを選ぶ |
| gate-acceptance-before-merge | hook | `.hooks/gate-acceptance-before-merge.sh` | 受け入れテストを通さずに PR をマージする |
