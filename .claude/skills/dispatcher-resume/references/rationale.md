# dispatcher-resume — 規律の由来

[`.claude/skills/dispatcher-resume/SKILL.md`](../SKILL.md) 本文から移した「なぜこの規律があるか」の記録。規律そのものは本文に残してあり、ここにあるのは由来だけ。手順を実行するために読む必要はない。

## 1. `/loop` の自己再帰（Step 0 の前 / Step 5 の INVARIANT(loop-prompt)）

2026-06-19、Step 5 が `/loop 3m` を prompt 省略のまま skill 実行ターン内で起動し、反復対象としてアクティブな slash command（`/dispatcher-resume` 自身）が捕捉されて、本 skill が 3 分ごとに自己再帰した。恒久対策は 2 つ:

- `/loop` には monitoring 専用の自然文ディレクティブを必ず渡す。[`tests/test_dispatcher_resume_loop_invariant.py`](../../../../tests/test_dispatcher_resume_loop_invariant.py) が SKILL.md(.in) の fenced `/loop` 行と INVARIANT マーカーを pin し、[`.hooks/check-loop-directive.sh`](../../../../.hooks/check-loop-directive.sh) が実行時に同じ fenced block を正文として読む。
- 残った予約や手動操作で再発火しても害が出ないよう、Step 0 の前で handover の消費状態を判定し、消費済みなら早期 exit する。

## 2. 消費状態を時刻でなく存在で判定する理由（Step 0 の前 手順 1）

cold-start と resume の分岐は live の `.md` だけを見る（`.dispatcher/CLAUDE.md` の起動時分岐）。`now` との時刻比較にすると、この規約と判定基準がずれる。

## 3. `already_consumed` を観測で (i)/(ii) に分ける理由（Step 0 の前 手順 2）

- 存在判定だけで no-op に倒すと、組織が止まっていて本当に cold-start が要る (ii) の案内を恒久的に隠す。
- 承認済み路で三値判定の「在」だけを live に数えるのは、他タブの別 org のワーカーを live と誤認すると (ii) の cold-start 経路が抑止されるため。
- 「unknown」を (ii) に倒さないのは、判定不能を「監視対象なし」と読み替えると、誤った `/org-start` 案内で健全な組織を壊しうるため（不可逆側）。

## 4. 監視ループを張る前に `CronList` で数える理由（Step 5 の 2 / Step 0 の前 手順 2 (i)）

2026-10-18、`/loop 30m` を張ったセッションで `/clear` を打ち、次のターンの `Stop` フックが受け取った `session_crons` を見ると、同じ id の予約が残っていた。続けて同じ指示文で `/loop` を打つと、予約は上書きされず 2 本になった。以前の本文は「`/clear` で予約が消える」「`/loop` を打てば予約が上書きされ 1 本に収束する」前提で書かれていて、resume のたびに監視ループが 1 本ずつ増えうる形だった。

- 0 本のときだけ張り、1 本なら残っている予約に任せる。2 本以上は 1 本に減らす。
- 監視対象が無いときに残っている予約を消さないのは、次のサイクルが監視対象なしを見て worker-monitoring Step 7 の停止手順（`relay_scan.py --mark-halted` の記帳を含む）を通るため。resume 側で消すと記帳が抜ける。
