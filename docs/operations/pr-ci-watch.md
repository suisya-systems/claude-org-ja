# PR の CI 監視とクローズ: 規律の理由

`/org-pull-request` と `/pr-watch-pane` の手順にある規律が、なぜそうなっているかをまとめる。手順そのものはスキル側にある。受信経路が多層になっている理由は [`docs/design/ci-watch-zero-miss.md`](../design/ci-watch-zero-miss.md)。

## `set_run_pr_open.py` / `run_complete_on_merge.py` に `--repo` を渡す理由

`--repo` を省くと `gh repo view` で解決され、窓口の cwd（ja）のリポジトリになる。別リポジトリの PR に対して実行すると、ja にある同じ番号の PR の branch / commit がその run 行に黙って書き込まれる（2026-08-06、renga の PR #302 の run 行が ja の PR #302 の情報で上書きされた。Issue #828）。`--task-id` も `--repo` も無い場合は PR から task を逆引きする経路なので今も `gh repo view` が既定で、クロスリポジトリの PR ではこの事故経路が残る。

## CI 監視を専用ペインで回す理由

Claude Code の背景タスクは spawn したシェルだけを追跡し、`nohup ... &` や `disown` で自己デタッチした監視本体は孤児になる。Bash の背景起動で `tools/pr-watch.*` を叩くと、`/clear` や `/secretary-resume` の後の新しいセッション、またはセッション終了で監視が黙って死に、CI 完了の event も peer 通知も出なくなる（空のログファイルだけが残るので気付きにくい）。Bash ラッパーの完了通知も監視本体の完了を意味しない。専用ペインは sandbox 外で、窓口のセッションに依存せずに回るので、この孤児化が起きない。直接起動は `.hooks/block-adhoc-pr-watch.sh` が deny する。

## `pr_merged` を窓口が手打ちしない理由

窓口が `journal_append.sh pr_merged` を打つと、1 回のマージにつき events が 2 行になり、relay が二重に配送される。2 通目は helper の payload を持たないので `head` が無く、`PR_MERGED: PR #<n> (head=<missing>) [head-unverifiable] [relay]` として届く。freshness gate は監視 head と照合できず、毎回 events の引き直しか人間への報告に落ちる。`[head-unverifiable]` マーカーを入れる前は不一致と同じ枝に落ちて黙って close が skip され、herdr / wezterm backend では watcher ペインがゾンビとして残った（Issue #751 の再発経路。Issue #954）。helper の冪等性は helper 自身の再実行にしか効かない。helper は自分が書いた行しか見ないので、窓口が別に打った行は重複として検出されない。

## 再 push の後に watcher の立て直しを機械で確認する理由

修正の再 push は「ワーカーの報告」と「窓口の push」が別のターンに分かれるので、watcher の立て直しが窓口の記憶頼みになり、実際に漏れた（2026-08-30、PR #73 で 2 回目の再 push 以降は誰も CI を見ておらず、ユーザーの指摘で発覚した。Refs #978）。`list_panes` に `pr-watch-<PR>` が居ることを根拠にしてはいけないのは、herdr / renga では監視が終わってもペインが自己 close せず、死んだ watch と生きている watch が見た目で区別できないためで、同じインシデントの誤判定がこれだった。`tools/watcher_restart_guard.py` はこの確認を機械化したもの。exit 3 のまま「CI 走行中」とユーザーに報告しない、という規律も同じインシデントから来ている。
