---
name: org-pull-request
description: >
  ワーカー完了報告に対するユーザー承認後の push / PR 作成 / CI 監視 / レビュー指摘ループ /
  PR マージ後の最終クローズを窓口が実行する。発動条件:
  (1) ワーカーから完了報告を受領しユーザーが「OK」「進めて」等の明示的承認を出した直後、
  (2) GitHub PR にレビュー指摘 / CI 失敗が来てワーカーへ修正指示を送り直すとき、
  (3) PR がマージされ最終クローズ条件を満たしたとき。
  単に「ワーカーに作業を依頼する」初動は org-delegate であり本スキルではない。
effort: medium
allowed-tools:
  - Read
  - Bash(git push:*)
  - Bash(git -C * worktree remove:*)
  - Bash(git worktree remove:*)
  - Bash(gh pr create:*)
  - Bash(gh pr view:*)
  - Bash(gh pr checks:*)
  - Bash(gh issue create:*)
  - Bash(gh issue edit:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python tools/set_run_pr_open.py:*)
  - Bash(py -3 tools/set_run_pr_open.py:*)
  - Bash(python tools/run_complete_on_merge.py:*)
  - Bash(py -3 tools/run_complete_on_merge.py:*)
  - Bash(bash tools/pr-watch.sh:*)
  - Bash(pwsh tools/pr-watch.ps1:*)
  - Bash(powershell tools/pr-watch.ps1:*)
  - mcp__org-broker__send_message
  - mcp__org-broker__check_messages
---

# org-pull-request: PR 作成・レビュー・マージ後クローズ

ワーカー完了報告 → ユーザー承認 → push / PR 作成 / CI 監視 / レビュー指摘ループ / PR マージ後の最終クローズまでを担当する。**窓口専属**。発動の前提は「ワーカーが完了報告済み・ユーザーが明示的承認を出した」状態にあること。承認前段階（ack 発行・REVIEW 遷移・ユーザー報告）は `.claude/skills/org-delegate/SKILL.md` Step 5 (2a) を参照。

> **T5 contract**: 本スキルが扱う `awaiting_review → complete` 遷移の正準仕様は
> [`docs/contracts/delegation-lifecycle-contract.md`](../../../docs/contracts/delegation-lifecycle-contract.md) §2 T5 / T6 / §1.5 close-condition。
> 同 contract は close-condition / pane discipline / 再 spawn 禁止を pin する SoT。
> 本 SKILL は手順を、contract は不変条件を担当する。

> **ack ≠ user 承認**: 本スキルが発動した時点で ack は既に発行済み（`.claude/skills/org-delegate/SKILL.md` Step 5 step 1 / [`.claude/skills/org-delegate/references/ack-template.md`](../org-delegate/references/ack-template.md)）。push / `gh pr create` / `tools/pr-watch.*` はユーザー承認後にのみ発行する。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `broker` / opt-in `renga`）**: 本スキルの `mcp__org-broker__*`（worker への修正指示 `send_message` 等）は **コード既定 `broker`** フレーム（`tools/transport.py: DEFAULT_TRANSPORT`、移行目標。下記「二フレーム注記」を先に参照）で書いてあり、tool 名はそのまま従えばよい。ただし **`CI_COMPLETED` / `PR_MERGED` 等が broker の push 一次**（各ペイン同居の channel sidecar `server:org-broker-channel` が `notifications/claude/channel` で窓口の idle セッションへ注入。runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）**で届くのは `ORG_TRANSPORT=broker` を明示した場合**であって、pr-watch の peer 通知 helper `tools/peer_notify.py: notify_peer` は無設定（運用既定）時は renga にフォールバックするため、無設定環境の受信は renga の in-band push になる（下記の二フレーム注記・2b-i 参照）。**push 失効時のフォールバックは送信 transport に依存**（`ORG_TRANSPORT=broker` 明示時のみ窓口がターン冒頭で能動 `mcp__org-broker__check_messages`、無設定で renga 経路に乗った場合は broker キューに載らないため events テーブルのポーリングで受ける。下記 2b-i の受信モデル注記を参照。§9.6）、エラーは broker 拡張コード（[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) の broker 節）が加わる。`ORG_TRANSPORT=renga`（opt-in・切戻し可）では完全修飾名が **`mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換され、CI_COMPLETED 等は `<channel source="renga-peers">` の in-band push で届く（手順は同型・renga は削除せず常時有効な切戻しの安全装置）。詳細は CLAUDE.md「輸送層（transport）両系」節と [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）を参照。

> **「既定」の二フレーム注記（Refs #604）**: 本スキルが `mcp__org-broker__*` 一次で「既定 `broker`」と書くのは**コード既定**フレーム（`tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 / Epic #586 で `broker` にフリップ済み・移行目標）。**運用既定は renga**（broker 実走 dogfood は Epic #6 Issue G まで未活性）で、root `CLAUDE.md`「輸送層（transport）両系」節と同じ二フレーム関係にある。**重要（pr-watch peer 通知の実挙動）**: pr-watch が CI 完了 / merge / タイムアウト時に Secretary へ送る peer 通知 helper `tools/peer_notify.py: notify_peer` は `DEFAULT_TRANSPORT` を参照しない **raw env 判定**で、`ORG_TRANSPORT==broker` を明示した時のみ broker CLI（`claude-org-runtime broker send`）経路、**無設定（運用既定）時は renga / `RENGA_SOCKET` フォールバック**（`RENGA_SOCKET` も無ければ silent no-op）。したがって運用既定（無設定）環境では `CI_COMPLETED` 等は **renga の in-band `<channel source="renga-peers">` push** で届き、broker channel sidecar の push 一次は `ORG_TRANSPORT=broker` を明示した時の受信経路である（下記 2b-i は両系を併記する）。

## 2b-i. PR 作成段階（即時実行）

ユーザーが「OK」「確認した」「問題ない」「進めて」等の **明示的承認** を出した直後に発動する:

- 必要に応じて窓口がプッシュ・PR 作成を行う（ワーカーには `git push` / PR 作成権限がない）。PR 本文の言語規約は `feedback_pr_issue_english`（PR / Issue は英語）に従う
- **PR 番号が確定したら直ちに `tools/set_run_pr_open.py` で `runs.pr_url` / `runs.branch` を back-fill する** (Issue #323):
  ```bash
  python tools/set_run_pr_open.py --task-id <task_id> --pr <PR>
  ```
  これは `gh pr view <PR> --json url,headRefName` を 1 度引いて、`StateWriter.set_run_pr` 経由で `runs.pr_url` と `runs.branch` を上書きする。再呼び出しは idempotent（同じ値の上書き、events への追記なし）。これを行わないと後段の `tools/run_complete_on_merge.py` が `runs.pr_url` を引けず `no_run`（exit 3）で落ち、`-MergeWatch` の自動完了が失敗する
- DB の events テーブルにイベント追記 (push / PR open など、`bash tools/journal_append.sh ...`)
- PR 番号が確定したら `tools/pr-watch.ps1 <PR>` (Windows) / `tools/pr-watch.sh <PR>` (POSIX) で CI を監視する。完了時に `ci_completed` が自動で events に記録される。CI 完了で pr-watch は **return** する（review feedback loop 2c や手動 close 2b-ii に進めるよう同期占有しない）
- **pr-watch が CI 完了 / merge 検出 / 24h タイムアウトの瞬間に Secretary へ peer message を送る**（Issue #326）。`ORG_TRANSPORT=broker` を明示した場合は `CI_COMPLETED` / `PR_MERGED` / `PR_MERGE_WATCH_TIMEOUT` / `PR_MERGED_NO_RUN` / `PR_MERGED_HEAD_UNCONFIRMED` は **broker channel sidecar の push 一次**で届く（各ペイン同居の channel sidecar `server:org-broker-channel` が `notifications/claude/channel` で窓口の idle セッションへ注入。runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9。channel source は `org-broker`）。無設定（運用既定）時は `notify_peer` が renga にフォールバックするため、下記 `ORG_TRANSPORT=renga` 枝と同じく `<channel source="renga-peers">` の in-band push で届く。窓口は events テーブルをポーリングせず、`CI_COMPLETED: PR #<n> ...`（および `PR_MERGED: PR #<n>` / `PR_MERGE_WATCH_TIMEOUT: PR #<n>` / `PR_MERGED_NO_RUN: PR #<n>` / `PR_MERGED_HEAD_UNCONFIRMED: PR #<n>`）の到着で次のステップへ進める。`CI_COMPLETED` 受信 → ユーザーに merge 承認を仰ぐ → ユーザー承認 → `PR_MERGED` 受信で 2b-ii の post-merge cleanup へ。`PR_MERGED_NO_RUN` は merge は観測したが対応 run 行が見つからなかった失敗系（`tools/run_complete_on_merge.py` の `no_run` 終端）で、post-merge cleanup には進めず人間判断で対処する。**`PR_MERGED_HEAD_UNCONFIRMED` も同様に post-merge cleanup には進めず人間確認 gate に倒す**（push と merge が pr-watch の poll 間に同時に滑り込み CI 未確認 head でマージされた終端 — loopback 不能のため fail-closed exit 9 で通知される。本文形は `PR_MERGED_HEAD_UNCONFIRMED: PR #<n> (head=<merged_short>, last CI-confirmed head=<baseline_short>)`。窓口の対処手順・人間提示文・journal kind / severity は下記「PR_MERGED_HEAD_UNCONFIRMED 受信時」節を参照）。**push 失効時のフォールバックは送信に使われた transport に合わせる**: `ORG_TRANSPORT=broker` 明示時のみ窓口がターン冒頭で能動的に `mcp__org-broker__check_messages` で broker キューから本文を pull する（ナッジが出れば契機になりうるが idle を起こさないため能動 poll が正路。§9.6）。無設定（運用既定）で renga 経路に乗った通知は broker キューに載らないため `mcp__org-broker__check_messages` では拾えない — この場合は `<channel source="renga-peers">` の in-band push が一次で、取りこぼしは下記 events テーブルのポーリングで受ける。いずれの transport でも broker daemon 未起動 / `RENGA_SOCKET` 未設定の plain shell / CI では peer/channel 配送自体が成立しないため、従来どおり events テーブルのポーリングが最終フォールバックとなる
  - **`ORG_TRANSPORT=renga`（opt-in）の場合**: pr-watch は同じ瞬間に `<channel source="renga-peers"> CI_COMPLETED: PR #<n> ...`（および `PR_MERGED: PR #<n>` / `PR_MERGE_WATCH_TIMEOUT: PR #<n>` / `PR_MERGED_NO_RUN: PR #<n>` / `PR_MERGED_HEAD_UNCONFIRMED: PR #<n>`）を in-band push する。メッセージ本文の semantics・分岐（`PR_MERGED_HEAD_UNCONFIRMED` の人間 gate 含む）は broker と同一。RENGA_SOCKET 未設定の plain shell / CI では peer-send は silent noop となり、従来どおり events テーブルのポーリングにフォールバックする
- **CI_COMPLETED 受信 → ユーザーに merge 承認を仰ぐ直前で awaiting_user 通知を emit する（Issue #28）**: attention watcher にユーザーが merge 承認待ちで stop していることを知らせる:
  ```bash
  bash tools/journal_append.sh notify_sent kind=awaiting_user task_id=<task_id> gate=ci_green_merge_gate note="PR #<PR> CI green, awaiting merge approval"
  ```
  並走 runtime PR の classifier が `secretary_awaiting_user` (default severity `urgent`) として拾う。CLAUDE.md「secretary が user の判断を待っている状態を通知する」節を参照。`PR_MERGE_WATCH_TIMEOUT` 等の失敗系は対象外（awaiting_user ではなく別経路で人間判断）
- **merge 承認提示でも人間向け理解サマリを再掲する（検証深度 `full` 限定）**: CI green → ユーザーに merge 承認を仰ぐ際、[`.claude/skills/org-delegate/SKILL.md`](../org-delegate/SKILL.md) Step 5 (2a) で `.state/workers/worker-{task_id}.md` Progress Log に `Human Understanding Summary:` 見出し + fenced code block として永続化済みのサマリ（複数回完了時は最新ブロック）を再掲し、ユーザーが diff を開かずに最終 merge 判断を下せるようにする。永続コピーが無い場合（本フォーマット導入前から in-flight の PR 等）は PR 本文 / worker 完了報告メッセージに残るサマリを読む（窓口が diff を精読して再構成することはしない）。スキーマ SoT は [`.claude/skills/org-delegate/references/worker-claude-template.md`](../org-delegate/references/worker-claude-template.md)。minimal タスクには付かない
- **merge を待ち合わせたい時のみ** `-MergeWatch` (PowerShell) / `--merge-watch` (POSIX) を付ける。CI 通過後に `gh pr view --json mergedAt` を 24h ポーリングし、初回の merge で `tools/run_complete_on_merge.py` を呼ぶ (Issue #317)。merge-watch 中も pr-watch プロセスは生きたまま、merge 観測時に `pr_merged` イベントを events に追記してから return する
- run.status は **REVIEW のまま据え置く**（GitHub 側 PR レビュー指摘が来たら同ペインで対応するため。COMPLETED への遷移は 2b-ii で `update_run_status('<task_id>', 'completed')` を呼ぶ）。markdown 直接編集はしない
- **ペインはまだ閉じない**: PR 作成直後に `CLOSE_PANE` を送らない。worktree 除去・Worker Directory Registry 更新も 2b-ii まで遅延する
- PR レビューで指摘が来た場合は 2c のフローで同ワーカーに `send_message` 追指示を送り、同ペインで修正コミットを積ませる（新ワーカー再派遣は避ける — Issue / diff / 判断境界の再構築コストを払うことになる）
- **dogfood 対象 PR の場合（Issue #338）**: `registry/dogfood_pending.md` で当該 task_id の `status=pending` 行を探し、(a) `impl_pr=#<PR>` を埋め、(b) `gh issue create --title "dogfood follow-up: <surface>" --body-file <rendered template>` で paired follow-up issue を作成（template: [`.claude/skills/org-delegate/references/dogfood-issue-template.md`](../org-delegate/references/dogfood-issue-template.md)）、(c) 作成された issue 番号を `dogfood_issue=#<MMM>` に埋め、`status` を `pending → open` に遷移、(d) PR 本文末に `Paired dogfood issue: #<MMM>` を追記する。protocol 全体は [`.claude/skills/org-delegate/SKILL.md`](../org-delegate/SKILL.md) Step 1.8 を SoT とする

### CI 完了検知の正路: events DB poll が canonical、push は best-effort 補助（Issue #653）

CI 完了の **canonical 信号は events テーブルの `ci_completed` 行**（`payload_json` に対象 PR が一致し、`head` が push した SHA と一致、`status='passed'|'failed'`）であって、上の 2b-i 受信モデルで触れた `CI_COMPLETED` peer push（`<channel source="renga-peers">` の in-band push / broker channel sidecar の `notifications/claude/channel` 注入）ではない。peer push は **best-effort 補助**で、`tools/peer_notify.py: notify_peer` は raw env 判定の helper のため `ORG_TRANSPORT` 無設定（運用既定）かつ `RENGA_SOCKET` 不在の plain shell / broker daemon 未起動 / channel sidecar unhealthy 等で **silent no-op** になる経路がある（SKILL 冒頭の輸送層注記と同じ事情）。push が来たことを ground truth にすると no-op 経路で CI green を取りこぼし、人間に「CI 監視また失敗しただろ」と指摘されるまで放置する終端ケースに落ちる（本 Issue の動機）。

**ground truth の poll 推奨手順**:

- `git push origin <branch>` 直後から **~60s 経過した時点で events テーブルを定期 poll する**（推奨 60-90s 間隔。本リポジトリの CI は概ね 60-80s で確定する）。pr-watch pane が生きていれば watcher は push 通知の有無に関わらず `ci_completed` 行を events に書く（`tools/pr-watch.sh` / `tools/pr-watch.ps1` の `ci_completed` 書き出し経路）。
- 判定クエリ例（`PR_NUMBER` は窓口が把握している自局の int 値であり、ユーザー入力経路では渡らない。f-string の literal 展開で済ませる）:
  ```python
  from tools.state_db import connect
  conn = connect('.state/state.db')
  row = conn.execute(
      "SELECT payload_json FROM events WHERE kind='ci_completed' AND payload_json LIKE ? ORDER BY id DESC LIMIT 1",
      (f'%\"pr\": {PR_NUMBER}%',)
  ).fetchone()
  # row の payload_json を json.loads して head / status を取り出す:
  #   head が push した SHA と一致し status='passed' なら merge gate 通過 → 上の awaiting_user emit へ
  #   一致しない（前 CI 行）/ status='failed' なら原因を取得して fix → 再 push（2c のループへ）
  ```
- **push 通知が来た場合の扱い**: 補助的な早期通知として扱い、来た時点で events テーブルを引いて head 一致と status を確認する。push が来ない前提で events DB poll は止めない。peer push と events 行が両者 landed したら、判定は events DB の `head` + `status` を ground truth とする（受信モデル注記の「events テーブルのポーリングが最終フォールバック」と同じ姿勢を一次に倒したもの）。
- `PR_MERGE_WATCH_TIMEOUT` / `PR_MERGED_NO_RUN` / `PR_MERGED_HEAD_UNCONFIRMED` の人間 gate は本節の影響を受けない（これらは merge 観測側 / fail-closed gate であり、events DB poll は CI 完了検知のみ canonical 化する）。merge 観測自体の canonical 検知は従来どおり `pr-watch --merge-watch` の `pr_merged` イベントと `tools/run_complete_on_merge.py` の組み合わせで行う。

### ⚠️ cwd 注意: pr-watch 起動時

`tools/pr-watch.sh` / `tools/pr-watch.ps1` / `tools/pr_watch.py` は `state.db` を相対パスで開くため、起動時の cwd が ja root でないと CI 完了 event 書き込みでクラッシュし、peer 通知 (`CI_COMPLETED` / `PR_MERGED` 等) が飛ばない。直前に `cd .worktrees/...` していた場合は必ず `cd <ja-root> && nohup bash tools/pr-watch.sh <PR> ...` の形で起動すること。Issue #398 で根本対応中（cwd 非依存化）。

### ⚠️ Claude Code Bash tool 経由で起動する場合

窓口が Claude Code 内から `tools/pr-watch.sh` / `tools/pr-watch.ps1` を起動するときは、必ず Bash tool の `run_in_background: true` で投げる。`nohup ... &` + `disown` だけだと Claude Code の bash sub-shell が短命なため呼び出し終了と同時に pr-watch ごと kill され、CI 完了 event も peer 通知も一切飛ばなくなる（プロセスが消えていることに気付きづらく、ログファイルだけが空のまま残る）。特に `/clear` / [`/secretary-resume`](../secretary-resume/SKILL.md) 直後の fresh session ではこの罠を踏みやすい。`run_in_background: true` で投げれば完了通知（exit code 付き）が自動で届くので、CI 完了の検出経路がそちらでも担保される。

### `PR_MERGED_HEAD_UNCONFIRMED` 受信時（人間確認 gate, Issue #639 / pr-watch #638）

pr-watch から `PR_MERGED_HEAD_UNCONFIRMED: PR #<n> (head=<merged_short>, last CI-confirmed head=<baseline_short>)` を受け取ったら、**post-merge cleanup（2b-ii）に進まず人間確認を仰ぐ**。これは push と merge が pr-watch の poll 間に同時に滑り込み、CI 未確認の head で PR がマージされた終端ケース（merge は不可逆のため loopback 不能、pr-watch は fail-closed exit 9 で発信する。設計は PR #638）で、`PR_MERGED` / `PR_MERGED_NO_RUN` と並列の独立シグナル — `PR_MERGED` プレフィックスで auto-advance してはならない。

- **人間提示文（例）**: `PR #<n> は CI 未確認 head (<merged_short>) でマージされました。最後に CI で確認された head は <baseline_short> です。手動で head sha を確認し、post-merge cleanup を進めて良いか判断してください。`
- **journal イベント追記**（attention 通知用、CLAUDE.md「secretary が user の判断を待っている状態を通知する」節と同 emit 形）:
  ```bash
  bash tools/journal_append.sh notify_sent kind=awaiting_user task_id=<task_id> gate=ci_unconfirmed_head_gate note="PR #<PR> merged at unconfirmed head <merged_short> (last CI-confirmed <baseline_short>)"
  ```
  attention watcher classifier はこの emit を `kind=awaiting_user` → `secretary_awaiting_user` subkind として認識し **severity `urgent`（即時ビープ）** として拾う（CLAUDE.md「secretary が user の判断を待っている状態を通知する」節の canonical 形）。`gate` 値が `ci_unconfirmed_head_gate` であることで `worker_completed` / `ci_green_merge_gate` / `escalation_to_user` / `escalation_reply_forward` の既存 4 gate と区別される（CI green 根拠の merge 承認ではなく「マージ済みだが CI 未確認 head」の事後確認 gate）
- ユーザーが head 確認の結果「進めて良い」と明示判断したら、その時点で 2b-ii の post-merge cleanup へ手動で進む（`tools/run_complete_on_merge.py --pr <PR>` を含む通常の手順。`PR_MERGED` を受領しないまま進めるため、pattern B / C の StateWriter ブロックも窓口側で明示的に走らせる）。ユーザーが「マージを取り消す」「revert する」等の指示を出した場合は通常の `gh pr` 操作で対処し、cleanup は実行しない
- **既存の `PR_MERGED` / `PR_MERGED_NO_RUN` 経路は本節の影響を受けない**: 通常の clean merge は `PR_MERGED` プレフィックスで届き 2b-ii へ進む。`PR_MERGED_NO_RUN` は run 行未解決の失敗系で従来どおり人間判断（本節は CI 未確認 head の新規プレフィックスのみを追加するもので、既存 2 経路を変更しない）

## 2c. レビュー指摘 / CI 失敗のフィードバックループ

人間がフィードバック・修正指示を出した場合、または CI が失敗してユーザーが「直してもらって」と指示した場合:

- **再指示の前に dispatcher の監視フラグを解除する（Issue #658、T6 監視再開契約）**: worker が完了報告済み（完了時に §2a で secretary が dispatcher へ `WORKER_COMPLETION_NOTED` を送り `completion_reported_at` が立っている）の場合、追指示を送る**前に** dispatcher へ `WORKER_REOPENED` を **best-effort・非 blocking** で送り、`completion_reported_at` を `null` に clear させる。再指示は secretary→worker 直送で dispatcher が経路上に居ないため、この明示解除が dispatcher の PANE_OUTPUT_WITHOUT_PEER_MSG 検知（[`.dispatcher/references/worker-monitoring.md`](../../../.dispatcher/references/worker-monitoring.md) Step 5.2）の **fast-path 解除**になる。dispatcher 応答は待たない（dispatcher は `/loop 3m` の通常 `check_messages` で反映）。解除が無いと sticky skip のまま、awaiting_review→in_progress のレビュー修正中に発生する本物の silent dead-lock を見逃す。**`WORKER_REOPENED` が取りこぼされても、この直後の `run.status='in_use'` 遷移（下記、StateWriter が決定的に書く）が reliable backstop として働き、dispatcher が `runs.status == 'in_use'` を観測して監視を self-heal で再開する**（best-effort な解除通知だけに依存せず危険側に倒れない、Issue #658 P2）。本文に task_id と reopened_at（ISO-8601 UTC）を含める:
  ```
  mcp__org-broker__send_message(to_id="dispatcher", message="WORKER_REOPENED: worker-<task_id> (task_id=<task_id>, reopened_at=<ISO-8601 UTC>)")
  ```
- ワーカーに org-broker で追加指示を送る (`to_id="worker-{task_id}"`)
- 追加指示が trivial fix（CI 出力整形 / typo / コメント修正等）なら **検証深度 `minimal`** を明示し、完了報告は `done: {commit SHA 短縮形} {変更ファイル名}` の 1 行だけで返すよう伝える（フォーマットは [`.claude/skills/org-delegate/references/instruction-template.md`](../org-delegate/references/instruction-template.md) / [`.claude/skills/org-delegate/references/worker-claude-template.md`](../org-delegate/references/worker-claude-template.md) に従う）
- **DB 経由で run を IN_PROGRESS に戻す**（`run.status='in_use'`、markdown 直接編集禁止。post-commit hook が `.state/org-state.md` を再生成）:
  ```bash
  python -c "
  from pathlib import Path
  from tools.state_db import connect
  from tools.state_db.writer import StateWriter
  conn = connect('.state/state.db')
  with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
      w.update_run_status('<task_id>', 'in_use')
  "
  ```
- DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)（`tools/journal_append.py` が DB ルーティング済み）
- JSON snapshot は StateWriter post-commit hook が自動再生成 (Issue #284)
- （ペインが生きているのでワーカーはそのまま作業続行）
- **新ワーカーを再 spawn しない** (T6 contract): Issue / diff / 判断境界が失われるため。ワーカーが応答不能になった場合のみ窓口が判断する

ワーカーから新たな完了報告が届いたら、再度 `.claude/skills/org-delegate/SKILL.md` Step 5 (2a) → ユーザー承認 → 本スキル 2b-i の順で進む。

## 2b-ii. 最終クローズ段階（クローズ条件を満たしたら実行）

クローズ条件（contract §1.5 と同じ。少なくとも 1 つ満たすこと）:
- PR がマージされた（`gh pr view {n} --json mergedAt` 等で確認、または窓口がマージ通知を受ける、もしくは `pr-watch --merge-watch` の `pr_merged` イベントで通知される）
- ユーザーが明示的に「閉じてよい」「クローズして」「マージ済み」等の指示を出した
- 24-48 時間レビュー音沙汰なしの長期 idle（窓口の運用判断で随時。自動化はしない）

実施内容:

- 該当 run を **COMPLETED** に DB 更新（後述の `update_run_status('<task_id>', 'completed')` ブロックで実施）。markdown 直接編集はしない
- ワーカーの状態ファイルを最終更新（最後の Progress Log 追記など）
- **ワーカー状態ファイル (`.state/workers/worker-{task_id}.md`) は StateWriter が `update_run_status('<task_id>', 'completed')` の post-commit で自動的に `.state/workers/archive/` へ移動する** (Issue #284。`archive/` 不在時は lazy 作成、再呼び出しは idempotent。dashboard はこのディレクトリ内のファイルを live ワーカーとして扱わない (Issue #264)。journal / retro が履歴参照する可能性に備えて削除はしない)
- DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)
- ディスパッチャーにペインクローズを依頼:
  `CLOSE_PANE: {pane_id} のペインを閉じてください。`
- **ディレクトリパターンに応じた後処理**（同タイミングで実施）:
  - パターン A（プロジェクトディレクトリ）: ディレクトリは保持する（次タスクで再利用）
  - パターン B（worktree）: `git -C {workers_dir}/{project_slug}/ worktree remove --force .worktrees/{task_id}` を実行。ブランチは残す（マージ済みでもブランチ削除はしない、PR 履歴用）
    - **`--force` は意図的（Issue #491）**: `gen_delegate_payload.py` の apply が `send_plan.json` を worker_dir 直下に残し、`worktree remove` は untracked file がある worktree を常に refuse するため、`--force` を付けないとクローズ段階で必ず失敗する。`send_plan.json` の close phase 自動削除は別 Issue（本スキルでは `--force` 例示で吸収）
    - **self-edit (`pattern_variant='live_repo_worktree'`) の場合**: worktree base が `{claude_org_path}` なので `git -C {claude_org_path} worktree remove --force .worktrees/{task_id}` を実行する（Issue #289）。`--force` の理由は通常パターン B と同じ。ブランチは同様に残す
  - パターン C（エフェメラル, `pattern_variant='ephemeral'`）: ディレクトリは保持する（容量が問題になった場合のみ手動削除を検討）
  - **パターン C（`gitignored_repo_root`, claude-org 自己編集）の特例 cleanup（Issue #478）**: `worker_dir` が claude-org-ja repo root 自身なので、worktree remove も dir 削除も効かず、`{claude_org_root}/CLAUDE.local.md`（ワーカー指示ブリーフ）が残留する。残ると次回 `/org-start` で Secretary が「窓口かつワーカー」という矛盾 role identity を読み込む。**close 時に `tools/run_complete_on_merge.py` の `cleanup_pattern_c_local_md()` を呼んでブリーフを削除する**（下記 StateWriter ブロックに同梱）。判定は `runs.pattern == 'C'` AND `worker_dir == claude_org_root` で行われ、ephemeral C / パターン A・B では no-op。`events` に `pattern_c_cleanup`（payload: `task` / `removed_path` / `mode`）が 1 行残る。idempotent（ファイル不在なら `mode=skip`）。**Issue #486**: 下記ブロックの `remove_worker_dir()` が `worker_dirs` 行を DELETE すると `runs.worker_dir_id` が `ON DELETE SET NULL` になり join 経由の `worker_dir` 解決が NULL 化して cleanup が no-op になるため、`worker_dir_abs=` に削除した abs パスを明示で渡して順序非依存にする。PR 起点のクローズで `tools/run_complete_on_merge.py --pr <PR>` を呼ぶ場合は merge 記録時に自動で同 cleanup が走るが、gitignored タスクは PR を生まないことが多いので、下記 StateWriter ブロックでの明示呼び出しが本筋の経路。`.claude/settings.local.json` は worker 由来 / Secretary 由来の切り分けが要るためスコープ外（別 Issue）
- **dogfood 対象 PR の paired issue クローズ時（Issue #338）**: 実装 PR のマージと paired follow-up issue のクローズはライフサイクルが独立しうるため、本スキル側では「実装 PR マージで `consumed → closed` をする」という保証はしない。`consumed → closed` の終端遷移は窓口の register hygiene 責務として [`.claude/skills/org-delegate/SKILL.md`](../org-delegate/SKILL.md) Step 1.8 §consumed → closed 観察タイミング（register 書き込み時 + `/org-resume` 起動時に `gh issue view` で paired issue 状態確認）で回収する。本スキルが PR マージ時にたまたま該当行を観察した場合のみ、ついでに hygiene 手順を呼ぶ
- **PR 起点のクローズの場合は `tools/run_complete_on_merge.py` を呼ぶ** (Issue #317。`pr-watch --merge-watch` の merge-watch ループが自動で起動するので通常は手動実行不要だが、merge-watch を skip した場合や手動でマージを観測した場合のみ明示的に呼ぶ):
  ```bash
  python tools/run_complete_on_merge.py --pr <PR>
  ```
  これは `gh pr view <PR> --json url,state,mergedAt,mergeCommit,headRefName` を一度引いて、PR が merged なら `StateWriter.transaction()` 経由で `pr_state='merged'` / `commit_short` / `pr_url` / `completed_at` を更新し、`pr_merged` イベント (payload: `task` / `pattern` / `auto_completed`) を 1 行追記する。再呼び出しは idempotent（二重イベントを書かない）。task_id は `runs.pr_url` / `runs.branch`（active な runs 限定）から自動解決され、解決失敗時は `--task-id` を明示する。
  - **helper は runs.status を触らない**: dispatcher 側 pane close / worker_closed / worker-state final update が必要 (delegation-lifecycle-contract §T5)。helper は merge 事実のみ記録し、status flip と worker_dir 削除は窓口が下記の StateWriter で行う
  - **CLI 終了コード**: `merged` / `already` / `not_yet` は exit 0、`no_run`（runs に該当行なし）は exit 3 で失敗扱いになる。手動運用時は exit code を確認
- **パターン B / C のレジストリエントリ削除と最終 close は別途 StateWriter を呼ぶ**（markdown 直接編集禁止。run_complete_on_merge が `pr_state='merged'` と `completed_at` を既に書いているので、ここでは status flip と worker_dir 削除のみ行う）:
  ```bash
  python -c "
  from pathlib import Path
  from tools.state_db import connect
  from tools.state_db.writer import StateWriter
  from tools.run_complete_on_merge import cleanup_pattern_c_local_md
  conn = connect('.state/state.db')
  abs_path = '<abs>'  # worker_dir の絶対パス（パターン B / C）
  with StateWriter(conn).transaction() as w:
      w.update_run_status('<task_id>', 'completed')  # post-commit hook が worker-{task}.md を archive
      w.remove_worker_dir(abs_path)  # パターン B / C のみ
  # Issue #478 / #486: Pattern C gitignored_repo_root の CLAUDE.local.md を削除
  # （runs.pattern=='C' AND worker_dir==root のときのみ実削除。他は no-op）。
  # remove_worker_dir() が worker_dirs 行を DELETE し runs.worker_dir_id は
  # ON DELETE SET NULL になるため、join 経由の検出は NULL 化して no-op になる。
  # worker_dir_abs= に削除した abs_path を明示で渡し、行削除の前後どちらで呼んでも
  # 検出が壊れないようにする（Issue #486）。
  cleanup_pattern_c_local_md(conn, task_id='<task_id>', claude_org_root=Path('.').resolve(), worker_dir_abs=abs_path)
  "
  ```
  legacy のハンドロール完了スクリプトは `docs/legacy/pr-merge-completion-manual.md` に保管されている。標準経路は上記 `tools/run_complete_on_merge.py` であり、museum copy へ reach するのは Issue を切ってユーザー判断を仰いだ後に限る (PR #315 と同じ pattern)
  - パターン A: lifecycle='active' のまま、run.status='completed' で snapshotter が available 相当の表示にする
  - パターン B / C: 物理 dir は別途処理（worktree remove / dir 保持）。レジストリエントリ削除は上記 with ブロック内に `w.remove_worker_dir('<abs>')` を追加
- JSON snapshot は StateWriter post-commit hook が自動再生成 (Issue #284)

## 2b-iii. マージ後の次タスク提案（proactive next-dispatch）

2b-ii の post-merge cleanup（run COMPLETED 化・ペインクローズ・worktree 後処理）まで終わったら、窓口はユーザーの催促を待たず次の仕事候補を能動的に提示する。**候補生成はその場で `gh issue list` を即興で叩くのではなく、[`/work-discovery`](../work-discovery/SKILL.md) skill（= 決定的ツール `tools/work_discovery_scan.py` の triage 出力）を消費する**。判定基準（依存解決済み / 優先度 / 工数）が明文化され、提示に再現性・監査性が付く。方針の一次参照は [`CLAUDE.md`](../../../CLAUDE.md)「PR マージ後の次タスク提案（proactive next-dispatch）」、設計の一次参照は [`docs/design/work-discovery-triage.md`](../../../docs/design/work-discovery-triage.md) §8（post-merge 統合）。

- **post_merge トリガで走らせる**: `/work-discovery` を post-merge 文脈で起動する（scan を `--trigger post_merge`、空き pane があれば `--free-panes <数>` 付き）。候補 JSON に `generated_for: "post_merge"` が載り、**post-merge では「直近マージで unblock された / 自然な follow-up」を上位に出す `unblocked_by_recent_merge` 軸が強く効く**（設計 §4.2 / §8.1-3）。空き枠があれば `parallelizable` 候補のランクが上がり並列枠を埋められる。
- **提示と人間ゲートは現行と互換**: triage 結果を設計 §5.2 形式（候補 N 件 + 推奨 1、推定軸には `(推定)`、除外枠も提示）で窓口が人間へ提示する。**人間が番号で選択 → 選ばれた候補は [`/org-delegate`](../org-delegate/SKILL.md) の Step 0 から**通常委譲フローに入る。候補生成の手段が即興から triage に替わるだけで、人間の操作・人間ゲートの外形は変えない（設計 §8.1-4）。
- **propose-only で停止**: 候補を出したら止める。rank 1（推奨）の自動着手・自動 commit・自動 PR はしない（着手判断は人間のみ。設計 INV-1 / INV-2）。本ステップ・`/work-discovery` が org-delegate を呼んだり spawn することも禁止。
- 手動・任意タイミングの提示（idle 時など）は同じ `/work-discovery` を `--trigger manual` で起動する。skill 内の exit code 分岐・レンダリング規則は [`/work-discovery`](../work-discovery/SKILL.md) を一次参照する。
