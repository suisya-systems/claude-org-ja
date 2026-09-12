# org-delegate — 規律の由来（経緯・実測ログ）

[`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) 本文から移設した「なぜこの規律があるか」の記録。規律そのもの（MUST / 禁止 / 条件）は本文に残してあり、ここにあるのはその由来（どの事故で・いつ・どの Issue で決まったか）だけ。**手順を実行するために読む必要はない**。

## 1. Windows worker への cp932 注意（委譲前チェックリスト）

cp932 コンソールが em-dash(U+2014) を encode できず `--help` がクラッシュする型が 2 回発火（ja#537 / runtime#63）。pytest は `redirect_stdout` で UTF-8 キャプチャするため通り、実端末でのみ落ちる。

## 2. runtime pin ラグの確認タイミング（委譲前チェックリスト）

pin ラグはセッション定数なので確認は org-start 時 1 回で足り、委譲ごとの再チェックは不要（背景: 2026-07-08 #119）。

## 3. incorporation / sync 系の初手 cp 禁止（初手チェックリスト）

cp で destination の修正を機械的に巻き戻す事故が過去に発生（destination 側の credential 露出対策 Blocker fix を revert 寸前まで進んだ）。

## 4. 並列委譲の brief 事前文言（並列委譲 節）

事後の追指示（peer message での追送）でも救えるが（2026-07-22 の ja#747 / ja#748 並列編集の実例）、brief 段階で入れておけば窓口→worker の 1 往復が減り、integration point conflict を事前に防げる。完了報告前に base upstream へ rebase してから報告する既存運用と組み合わせると、窓口の残作業は merge 順序の決定と続報通知だけに縮退する。

## 5. 1 worker = 1 task = 1 scope の違反事例（メッセージング規約 / Issue #475）

2026-05-21 voice-v2-independent ペインへの別件混入投入（スコープ外作業を同 worker に追送し、本来別 worker を立てるべき別件を 1 worker に集約してしまった）。本節の guard / CI 実装は別 Issue。

## 6. `worker_reported` 記帳漏れが作る false positive（進捗報告 / Issue #699）

dispatcher の PANE_OUTPUT_WITHOUT_PEER_MSG 判定は events テーブルの `worker_reported` 痕跡を SQL で参照するため、中間ハンドオフ報告の記帳漏れは「peer message は届いているのに events に痕跡が無い」状態を作り、正常稼働中の worker を silent dead-lock と誤検知する false positive の直接原因になる（2026-07-08 kura conveyor で実例）。

## 7. self-edit 承認を手打ちしない理由（DELEGATE_COMPLETE 受信時 / Issue #956）

最後の Enter を落とすと承認文が入力欄に draft として滞留し、見た目は着弾と区別がつかないまま worker が待機し続ける。
