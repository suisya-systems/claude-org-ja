# org-delegate — 規律の由来（経緯・実測ログ）

[`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) 本文と同スキルの `references/` から移設した「なぜこの規律があるか」の記録。規律そのもの（MUST / 禁止 / 条件）は本文に残してあり、ここにあるのはその由来（どの事故で・いつ・どの Issue で決まったか）だけ。**手順を実行するために読む必要はない**。

## 1. Windows worker への cp932 注意（brief の Windows 注意事項）

cp932 コンソールが em-dash(U+2014) を encode できず `--help` がクラッシュする型が 2 回発火（ja#537 / runtime#63）。pytest は `redirect_stdout` で UTF-8 キャプチャするため通り、実端末でのみ落ちる。規則は全ワーカーの brief（`tools/templates/worker_brief_normal.md` / `worker_brief_self_edit.md` の Windows 注意事項）に常時描画されるので、窓口が委譲ごとに重ねて書く手順は外した。

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

最後の Enter を落とすと承認文が入力欄に draft として滞留し、見た目は着弾と区別がつかないまま worker が待機し続ける。2026-07-31（task `ja-registry-template-001`）と 2026-08-25（task `pr-merged-double-entry-fix`）の 2 回発生し、いずれも人間の指摘で発覚した。分割は必要だが、その分割を人間の規律で守らせるのが誤りだったので、`tools/self_edit_approval.py` の内部に閉じ込めた。

## 8. 軽量レーンの導入と `run_in_background`（レーン選択判定 / Issue #515・#942）

- 2026-06-12 のパイロット 2 走（#546 / #545）で、ワーカーレーン同等の Codex ゲート品質を保ったまま着手→PR が 18 分（ワーカーレーンは 40〜60 分）に短縮された。これを受けてユーザー承認のうえ軽量レーンを例外として設けた。
- 導入時は `Agent` の `run_in_background=true` を必須と書いていた。2026-08-19 の実測で、ハーネスが `Agent` の入力スキーマから `run_in_background` を廃止し（`additionalProperties:false` で明示指定も送信前に除去される）、subagent が常時背景実行になったことが分かった。旧判定（`== true` を要求）のままのフックは全 subagent を誤 deny していたため、フックはキーの有無で解釈を分ける形に直した（`.hooks/block-foreground-subagent.sh` 冒頭コメント）。手順側は、パラメータの有無どちらのハーネスでも成り立つ書き方にした。

## 9. ack が必須になった観測（ack-template）

session #13 で、Secretary が「user に報告した」ことを「worker への返答」と誤認し、worker が ack を待って idle のまま止まった。

## 10. release-class タスクの pre-fetch（Step 0.6）

claude-org-runtime v0.1.10 のリリースで、local main が古いまま派遣した worker が着手 5 分以内に BLOCKER を出し、窓口との往復で 10 分以上を失った。導入時の説明は「worker は `git fetch` / `git pull` / `git remote update` を deny されている」だったが、現行の `tools/org_extension_schema.json` では素の `git fetch` は worker に許可されており、deny されているのは `git pull` と `git -C <path> fetch` / `pull`。local main を進められないことに変わりはないので、手順は維持している。

## 11. Codex design review ゲート（Step 1.7）

- 事前 Codex design review が 1 ラウンドで Blocker 2 件 + Major 5 件を捕捉した実績（curator session #18 の振り返り、Issue #283 / session #12）に基づいてゲートを設けた。
- リリースライン判定の観点: fail-closed 設計を完成させた後に対象 repo の semver policy を精読して初めて破壊的変更（major 行き）と判明し、非破壊化でほぼ全面やり直しになった（renga PR #325 / Issue #306）。

## 12. self-edit の live repo worktree（claude-org-self-edit.md §3）

session #11〜#12 を通じて全 claude-org self-edit ワーカー（PR #276, #279, #280, #282, #288, #291, #294, #293, #295, #296）が Secretary の live repo の `.worktrees/` を使っており、その de facto 慣行を Issue #289 で明文化した。

## 13. 手書き委譲経路（docs/legacy/hand-typed-delegate-path.md）

`gen_delegate_payload.py` 導入前の手書き手順は museum copy として `docs/legacy/hand-typed-delegate-path.md` に移した。通常運用で参照するのはプロトコル違反。手書き手順自体も `claude-org-runtime settings generate` に依存するので、runtime CLI が無い環境の fallback にもならない（その環境で使ってよいのは `--skip-settings` だけ）。手書き経路が過去に持ち込んだ失敗モード（settings env の不一致、drift_check の破損、T1 予約の欠落）は同ファイルに記載がある。
