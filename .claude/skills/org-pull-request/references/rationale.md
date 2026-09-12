# org-pull-request — 規律の由来（経緯・実測ログ）

[`.claude/skills/org-pull-request/SKILL.md`](../SKILL.md) 本文から移設した「なぜこの規律があるか」の記録。規律そのもの（MUST / 禁止 / 条件）は本文に残してあり、ここにあるのはその由来（どの事故で・いつ・どの Issue で決まったか）だけ。**手順を実行するために読む必要はない**。

## 1. `set_run_pr_open.py` / `run_complete_on_merge.py` の `--repo` 解決（2b-i / Issue #828）

旧挙動は `--repo` 省略で `gh repo view`＝窓口の cwd＝ja を見ていたため、**ja に同番号の PR があるとその branch / commit が別リポジトリの run 行へ silent に書き込まれた**（2026-08-06 に renga PR #302 が ja PR #302 の情報で汚染された実害）。

`--task-id` も `--repo` も無い場合だけは「task を PR から逆引きする」経路なので従来どおり `gh repo view` が既定になり、**クロスリポジトリの PR に対して ja の同番号 PR を読む旧来の事故経路が残る**。

## 2. `tools/peer_notify.py` の分岐が `resolve()` 経由になった経緯（CI 完了検知の正路 / Refs #941）

直 push は best-effort 補助（path A）で、解決先 transport が未構成のときに silent no-op になる経路がある（PR #73 で実際に窓口が CI green を取りこぼした）。

なお同 helper は Refs #941 で raw env 判定をやめ [`tools/transport.py`](../../../../tools/transport.py) の `resolve()` 経由になった（`ORG_TRANSPORT` 無設定を renga と読んで**実際の解決先と食い違う** push を撃つ経路が、interlock#36 の CI green 取りこぼしの一因だったため）。

## 3. watcher ペインのイベント駆動 close（監視終端 節 / Issue #751）

自己 close は tmux backend でだけ効く低遅延経路であり、broker が herdr / wezterm backend で動く環境では `tmux kill-pane` が no-op になって watcher ペインがゾンビとして残留する（実測: 2026-07-22 に PR #154 / #749 / #750 の watcher 3 枚が herdr backend で残留した）。

## 4. `pr_merged` を窓口が手打ちしない理由（2b-ii / Issue #954）

打つと events が 1 マージにつき 2 行になり、relay が**二重配送**され、2 本目は helper の payload を持たない＝`head` が無いため `PR_MERGED: PR #<n> (head=<missing>) [head-unverifiable] [relay]` として届く。freshness gate はこの 2 本目の監視 head を照合できず、`[head-unverifiable]` 枝での events 引き直し / 人間報告に毎回落ちる（マーカー導入前は不一致と同じ枝に落ちて **黙って close skip** され、herdr / wezterm backend で watcher がゾンビ残留していた — Issue #751 の再発経路）。

helper の冪等性は helper 自身の再実行に対する性質であって、窓口の手打ちには効かない: helper は自分が書いた行しか見ないので、窓口が別途 `journal_append.sh pr_merged` を打てばそれは重複検出されず 2 行目として残り、独立に relay される。

## 5. 再 push 後の watcher 立て直しを機械確認する理由（2c / Refs #978）

修正の再 push は「worker の報告 → 窓口の push」と別ターンに分かれるため、watcher の立て直しが窓口の記憶頼みになり、実際に落ちた（2026-08-30、PR #73 で 2 回目の再 push 以降 誰も CI を見ていない状態が続き、ユーザーの指摘で発覚）。

`list_panes` に `pr-watch-<PR>` が居ることを根拠にしてはならないのは、herdr / renga では監視終了後もペインが自己 close せず、死んだ watch と生きている watch が外見上区別できないためで、これが同インシデントの誤判定そのものである。「exit 3 のまま『CI 走行中』とユーザーに報告しない」も、それが同インシデントで起きたことに由来する。

## 6. `worktree remove --force` と Pattern C cleanup の引数（2b-ii / Issue #491 / Issue #486）

`--force` が必須なのは、`gen_delegate_payload.py` の apply が `send_plan.json` を worker_dir 直下に残し、`worktree remove` は untracked file がある worktree を常に refuse するため（付けないとクローズ段階で必ず失敗する）。`send_plan.json` の close phase 自動削除は別 Issue。

`cleanup_pattern_c_local_md()` に `worker_dir_abs=` を明示で渡すのは、`remove_worker_dir()` が `worker_dirs` 行を DELETE すると `runs.worker_dir_id` が `ON DELETE SET NULL` になり、join 経由の `worker_dir` 解決が NULL 化して cleanup が no-op になるため（Issue #486）。明示で渡すと行削除の前後どちらで呼んでも検出が壊れない。

Pattern C gitignored_repo_root の残留 `CLAUDE.local.md` が問題なのは、残ると次回 `/org-start` で Secretary が「窓口かつワーカー」という矛盾 role identity を読み込むためである（Issue #478）。

## 7. 裸 name close の 3 条件をめぐる hazard と契約由来（監視終端 close 節）

**この論点の SoT は [`.claude/skills/pr-watch-pane/references/rationale.md`](../../pr-watch-pane/references/rationale.md) §6 / §7**（pre-capability renga の legacy 解決が作る誤 close hazard、`DEFAULT_TRANSPORT` から Group B の駆動系を推定してはならない理由、契約 T-§4.2 の carve-out が 3 条件を "MUST gate it on all three conditions together" と normative に固定していること、`tools/peer_notify.py` が Refs #941 で `resolve()` 経由になってもこの MUST NOT は緩まないこと）。本スキル側は条件の箇条書きと [`.claude/skills/pr-watch-pane/SKILL.md`](../../pr-watch-pane/SKILL.md) Step 5 (b) への参照だけを持ち、解説を二重に持たない（安全規律が 2 箇所に全文で存在すると片方だけが直る drift の温床になるため）。
