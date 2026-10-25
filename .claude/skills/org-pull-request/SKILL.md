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
  - Bash(python3 tools/journal_append.py:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python3 tools/set_run_pr_open.py:*)
  - Bash(py -3 tools/set_run_pr_open.py:*)
  - Bash(python3 tools/run_complete_on_merge.py:*)
  - Bash(py -3 tools/run_complete_on_merge.py:*)
  - Bash(python3 tools/watcher_restart_guard.py:*)
  - Bash(py -3 tools/watcher_restart_guard.py:*)
  - mcp__org-broker__send_message
  - mcp__org-broker__check_messages
  - mcp__org-broker__list_panes
  - mcp__org-broker__close_pane
---

# org-pull-request: PR 作成・レビュー・マージ後クローズ

ワーカー完了報告 → ユーザー承認 → push / PR 作成 / CI 監視 / レビュー指摘ループ / PR マージ後の最終クローズまでを担当する。**窓口専属**。発動の前提は「ワーカーが完了報告済み・ユーザーが明示的承認を出した」状態にあること。承認前段階（ack 発行・REVIEW 遷移・ユーザー報告）は `.claude/skills/org-delegate/SKILL.md` Step 5 (2a) を参照。

> **T5 contract**: 本スキルが扱う `awaiting_review → complete` 遷移の正準仕様は
> [`docs/contracts/delegation-lifecycle-contract.md`](../../../docs/contracts/delegation-lifecycle-contract.md) §2 T5 / T6 / §1.5 close-condition。
> 同 contract は close-condition / pane discipline / 再 spawn 禁止を pin する SoT。
> 本 SKILL は手順を、contract は不変条件を担当する。

> **ack ≠ user 承認**: 本スキルが発動した時点で ack は既に発行済み（`.claude/skills/org-delegate/SKILL.md` Step 5 step 1 / [`.claude/skills/org-delegate/references/ack-template.md`](../org-delegate/references/ack-template.md)）。push / `gh pr create` / `/pr-watch-pane` はユーザー承認後にのみ発行する。

> **輸送層（transport）両系**: 本スキルのツール参照は `mcp__org-broker__*`（`org-broker`）で書いてあり、active transport が既定面ならそのまま従えばよい。輸送層で手順が変わるのは **受信モデル / spawn 儀式 / エラー分岐** の 3 点だけ。うち本スキルで実際に効くのは受信モデル 1 点で、**`CI_COMPLETED` / `PR_MERGED` の判定は輸送層に依らず events テーブルの canonical 行が正本**（直 push は best-effort 補助。分岐の本体は下記 2b-i の受信モデルと「CI 完了検知の正路」節）。両系の読み替え規則・「既定」の二フレーム注記・契約の批准経緯・broker 拡張エラーコードは [`.claude/skills/org-pull-request/references/transport-duality.md`](references/transport-duality.md) を参照（上記 3 分岐に入るまで読む必要はない）。

## 2a-p. ワーカーから「sandbox 外での rebase を依頼」の報告を受けたとき（承認前、Issue #1217）

ワーカーは rebase の前に `tools/rebase_preflight.sh <base_ref>` を回し、上流が sandbox で書けないパス（`.claude/skills` 等）を変えている（`REBASE_PREFLIGHT: blocked`）ときは rebase せずにこれを頼んでくる。そのまま rebase させると `could not detach HEAD` で止まり、作業ツリーに上流の中身が残るため。ack の後、窓口が自分の Bash（ワーカー sandbox の外）で次を行う。

1. `git -C <worker_dir> status --porcelain --untracked-files=no` が空であることを確かめる。空でなければワーカーの未コミット作業なので rebase せず、ワーカーに commit してから頼み直すよう送る
2. `git -C <worker_dir> rebase <base_ref>`（`<base_ref>` は `send_plan.json` の `summary.base_ref`）。conflict が出たら `git -C <worker_dir> rebase --abort` で戻し、[`.claude/skills/org-escalation/SKILL.md`](../org-escalation/SKILL.md) の手順で人間に上げる（窓口は conflict を解かない）
3. ワーカーに「sandbox 外で rebase した（HEAD=`<sha>`）。`git rev-list --count HEAD..<base_ref>` が 0 で `git status` が空であることを確かめ、通常検証と Codex ゲートを回し直して完了報告して」と送る

## 2a-r. ワーカーから「rebase 残差あり」/ `CODEX_GATE: dirty-worktree` の報告を受けたとき（承認前、Issue #1151）

ワーカーの sandbox は `.claude/skills` 等への書込みを拒否するため、統合用ブランチへの rebase が `Read-only file system` / `could not detach HEAD` で止まるか（HEAD は動かず、上流の中身が作業ツリーに残る。2a-p の事前判定を通らずに rebase したときに起きる）、成功表示でも該当ファイルが旧内容のまま作業ツリーに残る。後者のまま検証や Codex を回すと古い差分まで対象になる（`tools/codex_review_gate.sh` は追跡ファイルの差分を見ると `CODEX_GATE: dirty-worktree` で止まる）。ワーカーに update-ref / reset で回避させず、ack の後に窓口が自分の Bash（ワーカー sandbox の外）で直す。ワーカーは commit 済みの前提なので、追跡ファイルの差分は残差だけである。

1. `git -C <worker_dir> status` で状態を見る
2. **rebase 途中で止まっている**（`rebase in progress`）: `git -C <worker_dir> rebase --abort` → `git -C <worker_dir> rebase <base_ref>`（`<base_ref>` は `send_plan.json` の `summary.base_ref`）。conflict が出たら `git -C <worker_dir> rebase --abort` で戻し、[`.claude/skills/org-escalation/SKILL.md`](../org-escalation/SKILL.md) の手順で人間に上げる（窓口は conflict を解かない）
3. **rebase は完了し差分が残っている**: `git -C <worker_dir> diff --stat HEAD` で残差パスを確かめる。sandbox が書込みを拒否するパス（`.claude/skills` / `.claude/hooks` / `.claude/settings.json` 等）以外が含まれていたらワーカーの未コミット作業かもしれないので、戻さず [`.claude/skills/org-escalation/SKILL.md`](../org-escalation/SKILL.md) の手順で人間に上げる。保護パスだけなら `git -C <worker_dir> restore --staged --worktree -- <パス...>` で HEAD の内容に戻す（`--source` 指定と `checkout -- <path>` は hook が deny する）
4. `git -C <worker_dir> status --porcelain --untracked-files=no` が空になったことを確かめ、ワーカーに「rebase 残差を窓口で解消した。`git status` が空であることを確かめ、通常検証と Codex ゲートを回し直して完了報告して」と送る
5. 窓口の Bash でも `Read-only file system` 等で書けないときは、sandbox を外さず人間に報告して指示を仰ぐ

## 2b-i. PR 作成段階（即時実行）

ユーザーが「OK」「確認した」「問題ない」「進めて」等の **明示的承認** を出した直後に発動する:

- 必要に応じて窓口がプッシュ・PR 作成を行う（ワーカーには `git push` / PR 作成権限がない）。PR のタイトル・本文は対象リポジトリで使われている言語に合わせる
- **`gh pr create --base` は派遣時の起点ブランチと同じ設定を読む（Issue #808）**: プロジェクトの既定マージ先は [`registry/projects.md`](../../../registry/projects.md) の `base_branch` 列が SoT。値がある行（例 `develop`）は `gh pr create --base <base_branch>` を既定とし、**空欄 / `-` の行は従来どおり `--base` を付けない**（repo の既定ブランチ宛）。worktree を切った起点と PR のマージ先が食い違うと、develop が先行している分の差分が PR に混入するため、両者は必ず同じ値を使う。当該タスクの実効値は派遣時の DELEGATE 本文の「ベース:」行、または `send_plan.json` の `summary.base_branch` / `summary.base_ref` で確認できる（`--base-ref` で単発上書きされた hotfix もそこに出る）。値の解決順は `--base-ref` > `base_branch` 列 > `origin/HEAD`
- **PR 番号が確定したら直ちに `tools/set_run_pr_open.py` で `runs.pr_url` / `runs.branch` を back-fill する** (Issue #323):
  ```bash
  python3 tools/set_run_pr_open.py --task-id <task_id> --pr <PR>   # Mac/Linux
  py -3 tools/set_run_pr_open.py --task-id <task_id> --pr <PR>     # Windows native
  ```
  これは `gh pr view <PR> --json url,headRefName,title` を 1 度引いて、`StateWriter.set_run_pr` 経由で `runs.pr_url` と `runs.branch` を上書きする。再呼び出しは idempotent（同じ値の上書き、events への追記なし）。これを行わないと後段の `tools/run_complete_on_merge.py` が `runs.pr_url` を引けず `no_run`（exit 3）で落ち、`-MergeWatch` の自動完了が失敗する
  - **クロスリポジトリでも `--repo` は不要（Issue #828）**: `--repo` 省略時は `runs.project_id` → プロジェクト → GitHub URL（[`registry/projects.md`](../../../registry/projects.md) の パス列、次に `projects.origin_url`）で書き込み先リポジトリを決定的に解決する。解決できない場合は ja へ黙って落ちず **exit 2 で停止**する（`--repo OWNER/REPO` 明示は従来どおり最優先）。旧挙動で起きた cross-repo 汚染の経緯は [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §1
  - 実行すると 1 行目に `set_run_pr_open: repo=<owner/repo> (source=<registry|db_origin_url|home_repo|explicit>) PR #<N>: <PR タイトル>` が出る。**この行のリポジトリと PR タイトルが意図した書き込み先か目視で確認してから次へ進む**
- DB の events テーブルにイベント追記 — **手打ちの対象は helper が書かないイベントに限る**。この段階では `fix_pushed`（push）/ `pr_opened`（PR 作成）の 2 つ（`bash tools/journal_append.sh ...`）。`delegate_sent` や `pr_merged` のように **helper が同一トランザクションで記録するイベントを窓口が手で打ってはならない**（打つと events が 2 行になる）。どのイベントを誰が書くかの SoT は [`docs/journal-events.md`](../../../docs/journal-events.md) の Writer / Emitted by 列
- PR 番号が確定したら [`/pr-watch-pane <PR>`](../pr-watch-pane/SKILL.md) で CI を監視する（クロスリポジトリは `--repo OWNER/REPO` 付き）。broker tmux セッションの専用ペインで `tools/pr-watch.sh` が ja-root cwd・sandbox 外で回り、完了時に `ci_completed` が自動で events に記録される。CI 完了 / merge / timeout で watcher ペインは tmux backend では自己 close し（herdr / wezterm backend では自己 close が効かず残留するため、監視終端で窓口がイベント駆動 close する — 後掲「監視終端で watcher ペイン ... を窓口がイベント駆動 close する」節と Issue #751）、いずれも窓口セッションを同期占有しない（review feedback loop 2c や手動 close 2b-ii に進める）。`tools/pr-watch.*` の直接起動は hook が deny する（[`.claude/rules/pr-ci-watch.md`](../../rules/pr-ci-watch.md)。専用ペインを使う理由は [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §8）
- **CI 完了 / merge 検出 / 24h タイムアウトの受信は多層 (CI-watch zero-miss, Refs #653 #658)**。終端信号 (`CI_COMPLETED` / `PR_MERGED` / `PR_MERGE_WATCH_TIMEOUT` / `PR_MERGED_NO_RUN` / `PR_MERGED_HEAD_UNCONFIRMED` / `PR_WATCH_ABORTED`) はいずれも **`events` テーブルの canonical event を正本**とし、その配送を次の順で受ける（peer-only の終端信号は廃止済み — 必ず canonical event が先行する）:
  - **(B) ディスパッチャー relay = 見逃しゼロの主保証（一次受信経路）**: ディスパッチャーが `/loop 3m` 監視サイクルの relay scan ステップ（[`.dispatcher/references/worker-monitoring.md`](../../../.dispatcher/references/worker-monitoring.md) Step 5.25）で、未配送の終端イベントを `event_deliveries` 配送台帳と突き合わせて scan し、broker token 経由で窓口へ `mcp__org-broker__send_message` relay する。窓口はこの relay メッセージ（本文末尾 `[relay]` で直 push と区別可能。例 `CI_COMPLETED: PR #<n> (status=passed, head=<sha>) [relay]`）の到着で次のステップへ進める。**pr-watch ペインの直 push（下記 A）が env 欠如で silent no-op しても、この relay は events を直接読むため確実に届く**（PR #73 障害の根治: 汎用 spawn ペインで broker queue に `CI_COMPLETED` が 1 件も入らず窓口 idle だった終端をこの層が塞ぐ）。at-least-once（台帳 idempotency key で重複抑止・二重受信は冪等に扱う）。
  - **(A) pr-watch からの直 push = 低遅延の補助**: `ORG_TRANSPORT=broker` の pane では pr-watch が CI 確定の瞬間に `CI_COMPLETED` 等を **broker channel sidecar の push** で送る（各ペイン同居の channel sidecar `server:org-broker-channel` が `notifications/claude/channel` で窓口 idle セッションへ注入。runtime push-first 0.1.24+、channel source は `org-broker`）。届けば relay を待たず先に進めてよいが、これは **best-effort**（`tools/peer_notify.py` は raw env 判定のため env 欠如 / broker daemon 未起動 / sidecar unhealthy で無言に落ちる経路がある）。push 失効時は窓口がターン冒頭で能動的に `mcp__org-broker__check_messages` で pull する（§9.6）。push が失敗した場合、pr-watch は `notify_failed` イベントを fail-loud で記録し、それ自体も (B) の relay 対象になる（silent no-op の全廃）。
  - **(A) と (B) は独立層なので、両方健全な通常運転では同じ終端信号が 2 通届くのが既定であり異常ではない**（Issue #954）: 配送台帳 `event_deliveries` が記録するのは **(B) relay 側だけ**で、(A) の直 push は台帳を一切触らない（[`tools/pr_watch.py`](../../../tools/pr_watch.py) は push 成功で即 return する）。したがって **直 push が成功しても relay は抑止されない**。これは冗長性の設計どおりで、実際 2026-07-30〜08-19 は relay 層が死んで (A) だけ、その直後は (A) が失敗して (B) だけ、という「片方だけが生きている」期間が繰り返し起きている。**2 通目を障害と読み替えて片方を止めない**こと（1 通目で先へ進み、2 通目は冪等に無視してよい）。ただし **同じ PR で `PR_MERGED` が 2 通届き、片方が `[head-unverifiable]`（`head=<missing>`）である場合は別問題**で、これは窓口が `journal_append.sh pr_merged` を手打ちして events が 2 行になった痕跡である（2b-ii の手打ち禁止を参照）。
  - **(フォールバック) events テーブルの直接 poll**: broker daemon 未起動の plain shell / CI 等で relay も push も成立しない場合は、従来どおり窓口が events テーブルを直接引く（下記「CI 完了検知の正路」節）。canonical event はどの層でも同一なので、どこで受けても判定材料（`head` + `status`）は一致する。
  - **各終端信号の分岐（受信層に依らず不変）**: `CI_COMPLETED` 受信 → ユーザーに merge 承認を仰ぐ → ユーザー承認 → `PR_MERGED` 受信で 2b-ii の post-merge cleanup へ。`PR_MERGED_NO_RUN` は merge は観測したが対応 run 行が見つからなかった失敗系（`tools/run_complete_on_merge.py` の `no_run` 終端）で、post-merge cleanup には進めず人間判断で対処する。**`PR_MERGED_HEAD_UNCONFIRMED` も同様に post-merge cleanup には進めず人間確認 gate に倒す**（push と merge が pr-watch の poll 間に同時に滑り込み CI 未確認 head でマージされた終端 — loopback 不能のため fail-closed exit 9 で通知される。本文形は `PR_MERGED_HEAD_UNCONFIRMED: PR #<n> (head=<merged_short>, last CI-confirmed head=<baseline_short>)`。窓口の対処手順・人間提示文・journal kind / severity は下記「PR_MERGED_HEAD_UNCONFIRMED 受信時」節を参照）。`PR_WATCH_ABORTED` は watcher が想定外例外で異常終了した終端で、人間に監視断を知らせ手動 poll / 再起動を促す。
  - **`ORG_TRANSPORT=renga`（opt-in）の場合**: (A) の直 push は `<channel source="renga-peers"> CI_COMPLETED: PR #<n> ...` の in-band push になり、(B) のディスパッチャー relay は `mcp__renga-peers__send_message` 経由になる（relay 層は transport を問わず成立する）。メッセージ本文の semantics・分岐（`PR_MERGED_HEAD_UNCONFIRMED` の人間 gate 含む）は broker と同一。RENGA_SOCKET 未設定の plain shell / CI では直 push は silent noop となり、(B) relay か events テーブル poll にフォールバックする
- **CI_COMPLETED 受信 → ユーザーに merge 承認を仰ぐ直前で awaiting_user 通知を emit する（Issue #28）**: attention watcher にユーザーが merge 承認待ちで stop していることを知らせる:
  ```bash
  bash tools/journal_append.sh notify_sent kind=awaiting_user task_id=<task_id> gate=ci_green_merge_gate note="PR #<PR> CI green, awaiting merge approval"
  ```
  並走 runtime PR の classifier が `secretary_awaiting_user` (default severity `urgent`) として拾う。CLAUDE.md「secretary が user の判断を待っている状態を通知する」節を参照。`PR_MERGE_WATCH_TIMEOUT` 等の失敗系は対象外（awaiting_user ではなく別経路で人間判断）
- **merge 承認提示でも人間向け理解サマリを再掲する（検証深度 `full` 限定）**: CI green → ユーザーに merge 承認を仰ぐ際、[`.claude/skills/org-delegate/SKILL.md`](../org-delegate/SKILL.md) Step 5 (2a) で `.state/workers/worker-{task_id}.md` Progress Log に `Human Understanding Summary:` 見出し + fenced code block として永続化済みのサマリ（複数回完了時は最新ブロック）を再掲し、ユーザーが diff を開かずに最終 merge 判断を下せるようにする。永続コピーが無い場合（本フォーマット導入前から in-flight の PR 等）は PR 本文 / worker 完了報告メッセージに残るサマリを読む（窓口が diff を精読して再構成することはしない）。スキーマ SoT は brief テンプレート [`tools/templates/worker_brief_normal.md`](../../../tools/templates/worker_brief_normal.md) / [`tools/templates/worker_brief_self_edit.md`](../../../tools/templates/worker_brief_self_edit.md) の「人間向け理解サマリ」節。minimal タスクには付かない
- **merge を待ち合わせたい時のみ** `-MergeWatch` (PowerShell) / `--merge-watch` (POSIX) を付ける。CI 通過後に `gh pr view --json mergedAt` を 24h ポーリングし、初回の merge で `tools/run_complete_on_merge.py` を呼ぶ (Issue #317)。merge-watch 中も pr-watch プロセスは生きたまま、merge 観測時に `pr_merged` イベントを events に追記してから return する
- run.status は **REVIEW のまま据え置く**（GitHub 側 PR レビュー指摘が来たら同ペインで対応するため。COMPLETED への遷移は 2b-ii で `update_run_status('<task_id>', 'completed')` を呼ぶ）。markdown 直接編集はしない
- **ペインはまだ閉じない**: PR 作成直後に `CLOSE_PANE` を送らない。worktree 除去・Worker Directory Registry 更新も 2b-ii まで遅延する
- PR レビューで指摘が来た場合は 2c のフローで同ワーカーに `send_message` 追指示を送り、同ペインで修正コミットを積ませる（新ワーカー再派遣は避ける — Issue / diff / 判断境界の再構築コストを払うことになる）

### CI 完了検知の正路: events テーブルが canonical、ディスパッチャー relay が一次配送、events DB poll は最終フォールバック（Issue #653 / Refs #658）

CI 完了の **canonical 信号は events テーブルの `ci_completed` 行**（`payload_json` に対象 PR が一致し、`head` が push した SHA と一致、`status='passed'|'failed'`）であって、上の 2b-i 受信モデルで触れた `CI_COMPLETED` peer **直** push（`<channel source="renga-peers">` の in-band push / broker channel sidecar の `notifications/claude/channel` 注入）ではない。直 push は **best-effort 補助（path A）**で、`tools/peer_notify.py: notify_peer` は解決先 transport が未構成のとき（broker send CLI 不在 / renga opt-in なのに `RENGA_SOCKET` 不在の plain shell / broker daemon 未起動 / channel sidecar unhealthy 等）に **silent no-op** になる経路がある（SKILL 冒頭の輸送層注記と同じ事情）。実害と helper が `resolve()` 経由になった経緯は [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §2。

**見逃しゼロの主保証はディスパッチャー relay（path B、Refs #658）**: ディスパッチャーが `/loop 3m` 監視サイクル（[`.dispatcher/references/worker-monitoring.md`](../../../.dispatcher/references/worker-monitoring.md) Step 5.25）で `events` を `event_deliveries` 配送台帳と突き合わせ、未配送の `ci_completed` を broker token 経由で窓口へ `[relay]` 付き `send_message` する。通常運用では窓口はこの relay 到着で次へ進めばよく、events テーブルを能動 poll する必要はない。**events DB の直接 poll は relay も直 push も成立しない場合（broker daemon 未起動の plain shell / CI 等）の最終フォールバック**として下記手順で残す（どの層でも canonical event は同一なので判定材料 `head` + `status` は一致する）。push が来たことだけを ground truth にすると no-op 経路で CI green を取りこぼすため、直 push は早期通知としてのみ扱い、確定は必ず events 行（relay 受信 or 直接 poll）で行う。

**最終フォールバックの poll 手順**（relay も直 push も届かない環境に限る）:

- push 後、間隔を置いて events テーブルを引く。pr-watch pane が生きていれば watcher は push 通知の有無に関わらず `ci_completed` 行を events に書く（`tools/pr-watch.sh` / `tools/pr-watch.ps1` の `ci_completed` 書き出し経路）。
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
- **push 通知が来た場合の扱い**: 早期通知として扱い、来た時点で events テーブルを引いて head 一致と status を確認する。確定は常に events 行の `head` + `status` で行う。
- `PR_MERGE_WATCH_TIMEOUT` / `PR_MERGED_NO_RUN` / `PR_MERGED_HEAD_UNCONFIRMED` の人間 gate は本節の影響を受けない（これらは merge 観測側 / fail-closed gate であり、events DB poll は CI 完了検知のみ canonical 化する）。merge 観測自体の canonical 検知は従来どおり `pr-watch --merge-watch` の `pr_merged` イベントと `tools/run_complete_on_merge.py` の組み合わせで行う。

### ⚠️ cwd 注意: pr-watch 起動時

`state.db` とログのパスはスクリプト自身の位置（`tools/..`）を基点に解決されるため cwd に依らない。cwd に依存するのは、相対パス `tools/pr-watch.sh` での起動と、`--repo` 省略時のリポジトリ解決（`gh repo view`）の 2 点だけ。canonical 経路の [`/pr-watch-pane`](../pr-watch-pane/SKILL.md) は ja-root 絶対パスを `cwd` に渡し `--repo` も明示するので、どちらも吸収される。この注意が効くのは人間が `!` 経由で `tools/pr-watch.sh` を手動起動する緊急フォールバックだけで、その場合は `cd <ja-root> && bash tools/pr-watch.sh <PR> --repo <OWNER/REPO> ...` の形にする。

### 監視終端で watcher ペイン (`pr-watch-<PR>`) を窓口がイベント駆動 close する（herdr ゾンビ残留の根治, Issue #751）

[`/pr-watch-pane`](../pr-watch-pane/SKILL.md) が spawn する watcher ペイン (`name="pr-watch-<PR>"`) の
自己 close は **tmux backend でだけ効く低遅延経路**で、herdr / wezterm backend と opt-in renga では
no-op になり **watcher ペインがゾンビとして残留する**（backend 依存の詳細は
[`/pr-watch-pane`](../pr-watch-pane/SKILL.md) の「前提」節、残留の実測は
[`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §3）。このため
**監視の各終端で窓口がイベント駆動で掃除する**のを正路とする（tmux 自己 close は低遅延経路として温存し、
二重掃除にならないよう `[pane_not_found]` を正常応答として扱う）:

- **いつ入るか（終端トリガ）**: `PR_MERGED`（→ 掃除後 2b-ii post-merge cleanup）/
  `PR_MERGE_WATCH_TIMEOUT`（→ 下記受信時に即掃除）/ CI 失敗確定（→ 2c フィードバックループ入口で即掃除）/
  `PR_MERGED_HEAD_UNCONFIRMED` や `PR_MERGED_NO_RUN`（→ **人間確認を待たず即掃除**し、run 完了判断だけ
  人間確認 gate に残す）。**掃除は終端イベント受領で即時**行う（run.status の遷移 gate や人間確認 gate
  とは独立。遅らせる影響は
  [`.claude/skills/org-pull-request/references/watcher-cleanup.md`](references/watcher-cleanup.md)）。
- **掃除対象は spawn 時に控えた watcher instance に束縛する（`name` で再導出しない）— freshness gate**:
  終端イベントの監視 head が**いま追跡中の instance の監視 head と一致するときだけ** close する
  （`PR_MERGED_HEAD_UNCONFIRMED` だけは `head` ではなく `last CI-confirmed head` を突き合わせ、head が
  `unknown` / `<missing>` / 空の「照合不能」は superseded 扱いにせず別枝で処理する）。**イベント種別ごとの
  head フィールド・照合不能の処理手順・`name` 再導出が誤 close を生む機序は
  [`.claude/skills/org-pull-request/references/watcher-cleanup.md`](references/watcher-cleanup.md) が SoT**
  （[`/pr-watch-pane`](../pr-watch-pane/SKILL.md) Step 5 が「束縛の SoT」として指す先）。
- **識別子束縛 close**: freshness gate を通ったら、**まず照合に使う `mcp__org-broker__list_panes` の列挙を自タブの
  ものと確立する**（契約
  [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
  T-§4.2「Fail-safe consequence for Group B」。**(i) backend が Group B を自身の単一タブモデル内で解決
  する**（`org-broker`。契約 §8.1 / §8.10）/ **(ii) `caller_scope` を確立できている**（契約 T-§cap）の
  いずれか 1 つで足りる。**控えた pane_id が手元にあることは免除にならない**）。**どちらも成立しないなら
  close を撃たず**（相対セレクタへもフォールバックしない）、watcher ペインが残る旨をユーザー報告に含めて
  手動掃除に委ね、追跡はクリアしない（次の終端イベント / 手動掃除で判定をやり直せるようにするため）。
  確立できたら控えた **数値 pane_id** を `mcp__org-broker__list_panes` で `name="pr-watch-<PR>"` かつ
  `role="watcher"` を**なお指しているか identity 照合**し（pane_id recycle 対策。別ペインに再割当てされて
  いれば close しない）、合致したら `mcp__org-broker__close_pane(target=<控えた pane_id>)` で閉じて追跡をクリアする。
  `[pane_not_found]` / `[pane_vanished]` は既に self-close / 掃除済みの正常応答で skip。
- **stale 登録簿 binding のみ裸 name 指定（transport 条件付き allowlist）**: 控えた pane_id が既に消え、
  数値 id を `mcp__org-broker__list_panes` から取り直せない stale binding のときだけ、裸 name の
  `mcp__org-broker__close_pane(target="pr-watch-<PR>")` で登録簿を pop する。許可されるのは
  [`/pr-watch-pane`](../pr-watch-pane/SKILL.md) Step 5 の (b) の **3 条件がすべて成立するときだけ**
  （条件・判定方法・broker 以外での扱い・根拠の正本は同節。条件が揃わなければ close せずユーザーに報告する）。
  窓口の監視終端 cleanup では次の 2 点を加える（狭めるだけで、3 条件を置き換えない）:
  - **条件 (2)（再 spawn が `[name_in_use]` / `[name_taken]` で弾かれている）を別の証拠に差し替えない**。
    post-merge cleanup は watcher を掃除するだけで再 spawn しないので、pr-watch-pane Step 3 の分岐で
    現に観測した場合に限り成立し、観測が無ければ不成立として裸 name close を撃たない
  - 上の識別子束縛 close が「控えた pane_id のレコードなし」または `[pane_not_found]` で終わっており、
    かつ freshness gate を通っていま追跡中の watcher instance に束縛されていること。superseded と判定した
    終端イベントや、追跡を既に消してある場合は撃たない（別 instance の binding を pop しうる）
- **worker ペインの `CLOSE_PANE` とは別操作**: 2b-ii の worker Claude ペイン close はディスパッチャー宛
  `CLOSE_PANE: {pane_id}` で依頼するが、watcher ペインは窓口が spawn した CLI ペインなので窓口が
  `close_pane` で直接掃除する（窓口が `spawn_pane` の ops tier を持つ。契約 Surface 8）。

### `PR_MERGE_WATCH_TIMEOUT` 受信時（merge 未確定で監視終端, Issue #751）

`pr-watch --merge-watch` が CI green 後 24h 経ってもマージを観測できず `PR_MERGE_WATCH_TIMEOUT` を
発信したら、**監視は merge 未確定のまま終端**する（post-merge cleanup 2b-ii には進まない — マージが
起きていないため）。窓口は次を行う:

- **watcher ペインを即掃除する**: 上記「監視終端で watcher ペイン ... を窓口がイベント駆動 close する」の
  手順（spawn 時に控えた pane_id + head freshness gate に束縛し、identity 照合の上で close。`name` 再導出
  はしない）で掃除する（herdr backend では self-close が効かず残留するため必須。tmux backend では
  `[pane_not_found]` = 自己クローズ済みで正常）。
- **run.status は触らない**: マージ未確定なので REVIEW のまま据え置き、`update_run_status(..., 'completed')`
  は呼ばない（2b-ii のクローズ条件を満たしていない）。マージが遅れているだけか停滞かをユーザーに提示し、
  再監視するなら [`/pr-watch-pane <PR>`](../pr-watch-pane/SKILL.md) を再起動する（掃除済みなので冪等に spawn できる）。

### `PR_MERGED_HEAD_UNCONFIRMED` 受信時（人間確認 gate, Issue #639 / pr-watch #638）

pr-watch から `PR_MERGED_HEAD_UNCONFIRMED: PR #<n> (head=<merged_short>, last CI-confirmed head=<baseline_short>)` を受け取ったら、**post-merge cleanup（2b-ii）に進まず人間確認を仰ぐ**。これは push と merge が pr-watch の poll 間に同時に滑り込み、CI 未確認の head で PR がマージされた終端ケース（merge は不可逆のため loopback 不能、pr-watch は fail-closed exit 9 で発信する。設計は PR #638）で、`PR_MERGED` / `PR_MERGED_NO_RUN` と並列の独立シグナル — `PR_MERGED` プレフィックスで auto-advance してはならない。

- **watcher ペイン (`pr-watch-<PR>`) を人間確認を待たず即掃除する（Issue #751）**: この時点で watcher
  本体は既に exit 済みなので、人間確認 gate とは独立に前掲「監視終端で watcher ペイン ... を窓口が
  イベント駆動 close する」節の手順で掃除する（掃除を人間確認まで遅らせると herdr backend で確認待ちの
  間ずっとゾンビが残る）。**freshness gate では本イベントの `head`（マージ SHA）ではなく
  `last CI-confirmed head`（baseline）を監視 head として突き合わせる**（`head` で判定すると必ず不一致で
  誤 skip する）。run 完了判断だけを下記の人間確認 gate に残す。
- **人間提示文（例）**: `PR #<n> は CI 未確認 head (<merged_short>) でマージされました。最後に CI で確認された head は <baseline_short> です。手動で head sha を確認し、post-merge cleanup を進めて良いか判断してください。`
- **journal イベント追記**（attention 通知用、CLAUDE.md「secretary が user の判断を待っている状態を通知する」節と同 emit 形）:
  ```bash
  bash tools/journal_append.sh notify_sent kind=awaiting_user task_id=<task_id> gate=ci_unconfirmed_head_gate note="PR #<PR> merged at unconfirmed head <merged_short> (last CI-confirmed <baseline_short>)"
  ```
  attention watcher classifier はこの emit を `kind=awaiting_user` → `secretary_awaiting_user` subkind として認識し **severity `urgent`（即時ビープ）** として拾う（CLAUDE.md「secretary が user の判断を待っている状態を通知する」節の canonical 形）。`gate` 値が `ci_unconfirmed_head_gate` であることで `worker_completed` / `ci_green_merge_gate` / `escalation_to_user` / `escalation_reply_forward` の他の 4 gate と区別される（CI green 根拠の merge 承認ではなく「マージ済みだが CI 未確認 head」の事後確認 gate）
- ユーザーが head 確認の結果「進めて良い」と明示判断したら、その時点で 2b-ii の post-merge cleanup へ手動で進む（`tools/run_complete_on_merge.py --pr <PR>` を含む通常の手順。`PR_MERGED` を受領しないまま進めるため、pattern B / C の StateWriter ブロックも窓口側で明示的に走らせる）。ユーザーが「マージを取り消す」「revert する」等の指示を出した場合は通常の `gh pr` 操作で対処し、cleanup は実行しない
- **`PR_MERGED` / `PR_MERGED_NO_RUN` は本節の対象外**: 通常の clean merge は `PR_MERGED` プレフィックスで届き 2b-ii へ進む。`PR_MERGED_NO_RUN` は run 行未解決の失敗系で人間判断

## 2c. レビュー指摘 / CI 失敗のフィードバックループ

人間がフィードバック・修正指示を出した場合、または CI が失敗してユーザーが「直してもらって」と指示した場合:

- **CI 失敗確定で監視が終端したら watcher ペイン (`pr-watch-<PR>`) を窓口が掃除する（Issue #751）**:
  CI 失敗確定 (`ci_completed` の `status='failed'`) で `tools/pr-watch.sh` は監視を終端する（`--merge-watch`
  でも merge を待たずに exit）。**herdr / wezterm backend では自己 close が効かず watcher ペインが残留する**
  ため、修正指示に入る前に窓口が掃除する（spawn 時に控えた pane_id + head freshness gate に束縛し
  identity 照合の上で close。`name` 再導出はしない。詳細は前掲「監視終端で watcher ペイン
  (`pr-watch-<PR>`) を窓口がイベント駆動 close する」節）。tmux backend で既に self-close 済みなら
  `[pane_not_found]`（自己クローズ済みで正常、skip してよい）。修正後の再 push では新たに
  [`/pr-watch-pane <PR>`](../pr-watch-pane/SKILL.md) を起動し直し、その新しい pane_id / head を
  追跡し直す（旧イベントの重複配送は head 不一致で superseded 判定になり新 watcher を誤 close しない）。
- **再 push したら watcher が「その push より後に」起動されているかを機械確認する（Refs #978）**:
  修正の再 push は「worker の報告 → 窓口の push」と別ターンに分かれるため、watcher の立て直しが
  窓口の記憶頼みになりやすい（実際に落ちたインシデントは
  [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §5）。
  **`list_panes` に `pr-watch-<PR>` が居ることを根拠に
  してはならない**: herdr / renga では監視終了後もペインが自己 close せず、死んだ watch と生きて
  いる watch が外見上区別できない。判定は events の
  時系列で行う:
  ```bash
  python3 tools/watcher_restart_guard.py check --task <task_id>   # Mac/Linux
  py -3 tools/watcher_restart_guard.py check --task <task_id>     # Windows native
  ```
  exit 0 なら「最後の push より後に開始された watcher がある」。**exit 3 なら watcher が現 head を
  見ていない**ので [`/pr-watch-pane <PR>`](../pr-watch-pane/SKILL.md) を起動し直してから次へ進む
  （`stale`＝前 push の watcher しか居ない / `missing`＝一度も起動されていない /
  `ended_inconclusive`＝watch が CI の答えを出さずに終了した（`incomplete` / `indeterminate` /
  timeout。`indeterminate` は watcher 自身が再起動を要求している） / `ended_stale_head`＝出た判定が
  前 head のもの）。exit 2 は task から PR を特定できない状態なので、先に `tools/set_run_pr_open.py`
  で `runs.pr_url` を back-fill する。**exit 3 のまま「CI 走行中」とユーザーに報告しない**。
  なお `bash tools/journal_append.sh fix_pushed ...` を打った時点で同じ判定が stderr に出る
  （post-check。push 直後は watcher 再起動前なので exit 3 相当が出るのが正常で、これは異常通知では
  なく「次にやること」の提示）。判定ロジックの一次情報源は
  [`docs/journal-events.md`](../../../docs/journal-events.md) と当該ツールの module docstring。
- **再指示の前に dispatcher の監視フラグを解除する（Issue #658、T6 監視再開契約）**: worker が完了報告済み（完了時に §2a で secretary が dispatcher へ `WORKER_COMPLETION_NOTED` を送り `completion_reported_at` が立っている）の場合、追指示を送る**前に** dispatcher へ `WORKER_REOPENED` を **best-effort・非 blocking** で送り、`completion_reported_at` を `null` に clear させる。再指示は secretary→worker 直送で dispatcher が経路上に居ないため、この明示解除が dispatcher の PANE_OUTPUT_WITHOUT_PEER_MSG 検知（[`.dispatcher/references/worker-monitoring.md`](../../../.dispatcher/references/worker-monitoring.md) Step 5.2）の **fast-path 解除**になる。dispatcher 応答は待たない（dispatcher は `/loop 3m` の通常 `check_messages` で反映）。解除が無いと sticky skip のまま、awaiting_review→in_progress のレビュー修正中に発生する本物の silent dead-lock を見逃す。**`WORKER_REOPENED` が取りこぼされても、この直後の `run.status='in_use'` 遷移（下記、StateWriter が決定的に書く）が reliable backstop として働き、dispatcher が `runs.status == 'in_use'` を観測して監視を self-heal で再開する**（best-effort な解除通知だけに依存せず危険側に倒れない、Issue #658 P2）。本文に task_id と reopened_at（ISO-8601 UTC）を含める:
  ```
  mcp__org-broker__send_message(to_id="dispatcher", message="WORKER_REOPENED: worker-<task_id> (task_id=<task_id>, reopened_at=<ISO-8601 UTC>)")
  ```
- ワーカーに org-broker で追加指示を送る (`to_id="worker-{task_id}"`)
- 追加指示が trivial fix（CI 出力整形 / typo / コメント修正等）なら **検証深度 `minimal`** を明示し、完了報告は `done: {commit SHA 短縮形} {変更ファイル名}` の 1 行だけで返すよう伝える（フォーマットは [`.claude/skills/org-delegate/references/instruction-template.md`](../org-delegate/references/instruction-template.md) / [`tools/templates/worker_brief_normal.md`](../../../tools/templates/worker_brief_normal.md) の minimal 節に従う）
- **DB 経由で run を IN_PROGRESS に戻す**（`run.status='in_use'`、markdown 直接編集禁止。post-commit hook が `.state/org-state.md` を再生成）:
  ```bash
  # Mac/Linux
  python3 -c "
  from pathlib import Path
  from tools.state_db import connect
  from tools.state_db.writer import StateWriter
  conn = connect('.state/state.db')
  with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
      w.update_run_status('<task_id>', 'in_use')
  "
  # Windows native（Git Bash / PowerShell）
  py -3 -c "
  from pathlib import Path
  from tools.state_db import connect
  from tools.state_db.writer import StateWriter
  conn = connect('.state/state.db')
  with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
      w.update_run_status('<task_id>', 'in_use')
  "
  ```
- DB の events テーブルにイベント追記 — この再指示段階で窓口が手で打つのは `delegate_resume` / `delegate_resume_r2`（再指示の記録）と、修正の再 push 後の `fix_pushed`（`bash tools/journal_append.sh ...`、`tools/journal_append.py` が DB ルーティング済み）。helper が書くイベント（`delegate_sent` / `pr_merged`）は打たない — Writer / Emitted by の SoT は [`docs/journal-events.md`](../../../docs/journal-events.md)
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
- DB の events テーブルにイベント追記 — このクローズ段階で窓口が手で打つのは `issue_closed`（`Closes #N` が効かない
  リポジトリでの paired issue クローズ等）/ `prs_merged`（複数 PR をまとめた集計行）といった**窓口自身の後片付け**に限る
  （`bash tools/journal_append.sh ...`）。`worktree_removed` / `pane_closed` はディスパッチャーが書く
  （Writer / Emitted by の SoT は [`docs/journal-events.md`](../../../docs/journal-events.md)）
  - **`pr_merged` は窓口が手で打たない（Issue #954）**: `pr_merged` は下記 `tools/run_complete_on_merge.py` が
    `pr_state='merged'` / `commit` / `completed_at` と**同一トランザクションで**記録するので、窓口が手で
    `journal_append.sh pr_merged` を打つ必要はない。**打つと events が 1 マージにつき 2 行になり、relay が
    二重配送されて freshness gate が照合不能枝に落ちるため、打たないこと**（二重配送の中身と
    再発経路は [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §4）
- ディスパッチャーにペインクローズを依頼（**worker Claude ペイン**）:
  `CLOSE_PANE: {pane_id} のペインを閉じてください。`
- **CI 監視の watcher ペイン (`pr-watch-<PR>`) を窓口が掃除する（Issue #751）**: worker ペインとは
  別に、`/pr-watch-pane` で spawn した watcher ペインを窓口が掃除する（spawn 時に控えた pane_id + head
  freshness gate に束縛し identity 照合の上で close。`name` 再導出はしない。手順の SoT は前掲「監視終端で
  watcher ペイン (`pr-watch-<PR>`) を窓口がイベント駆動 close する」節）。`close_pane` は transport 抽象上
  tmux / herdr 両対応で、**herdr / wezterm backend では自己 close が効かず残留する**ため post-merge cleanup
  で必須。tmux backend で既に self-close 済みなら `[pane_not_found]`（自己クローズ済みで正常、skip してよい）
- **ディレクトリパターンに応じた後処理**（同タイミングで実施）:
  - パターン A（プロジェクトディレクトリ）: ディレクトリは保持する（次タスクで再利用）
  - パターン B（worktree）: `git -C {workers_dir}/{project_slug}/ worktree remove --force .worktrees/{task_id}` を実行。ブランチは残す（マージ済みでもブランチ削除はしない、PR 履歴用）
    - **`--force` は意図的（Issue #491）**: worker_dir 直下に残る `send_plan.json` のため、`--force` を付けないとクローズ段階で必ず失敗する（理由の詳細は [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §6）
    - **self-edit (`pattern_variant='live_repo_worktree'`) の場合**: worktree base が `{claude_org_path}` なので `git -C {claude_org_path} worktree remove --force .worktrees/{task_id}` を実行する（Issue #289）。`--force` の理由は通常パターン B と同じ。ブランチは同様に残す
  - パターン C（エフェメラル, `pattern_variant='ephemeral'`）: ディレクトリは保持する（容量が問題になった場合のみ手動削除を検討）
  - **パターン C（`gitignored_repo_root`, claude-org 自己編集）の特例 cleanup（Issue #478）**: `worker_dir` が claude-org-ja repo root 自身なので、worktree remove も dir 削除も効かず、`{claude_org_root}/CLAUDE.local.md`（ワーカー指示ブリーフ）が残留する。**close 時に `tools/run_complete_on_merge.py` の `cleanup_pattern_c_local_md()` を呼んでブリーフを削除する**（下記 StateWriter ブロックに同梱）。判定は `runs.pattern == 'C'` AND `worker_dir == claude_org_root` で行われ、ephemeral C / パターン A・B では no-op。`events` に `pattern_c_cleanup`（payload: `task` / `removed_path` / `mode`）が 1 行残る。idempotent（ファイル不在なら `mode=skip`）。**`worker_dir_abs=` に削除した abs パスを明示で渡す（Issue #486。順序非依存にするため。残留が問題になる理由と NULL 化の機序は [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §6）**。PR 起点のクローズで `tools/run_complete_on_merge.py --pr <PR>` を呼ぶ場合は merge 記録時に自動で同 cleanup が走るが、gitignored タスクは PR を生まないことが多いので、下記 StateWriter ブロックでの明示呼び出しが本筋の経路。`.claude/settings.local.json` は worker 由来 / Secretary 由来の切り分けが要るためスコープ外（別 Issue）
- **PR 起点のクローズの場合は `tools/run_complete_on_merge.py` を呼ぶ** (Issue #317。`pr-watch --merge-watch` の merge-watch ループが自動で起動するので通常は手動実行不要だが、merge-watch を skip した場合や手動でマージを観測した場合のみ明示的に呼ぶ):
  ```bash
  python3 tools/run_complete_on_merge.py --pr <PR> --task-id <task_id>   # Mac/Linux
  py -3 tools/run_complete_on_merge.py --pr <PR> --task-id <task_id>     # Windows native
  ```
  これは `gh pr view <PR> --json url,state,mergedAt,mergeCommit,headRefName,title` を一度引いて、PR が merged なら `StateWriter.transaction()` 経由で `pr_state='merged'` / `commit_short` / `pr_url` / `completed_at` を更新し、`pr_merged` イベント (payload: `task` / `pr` / `repo` / `pr_url` / `merge_commit` / `head` / `merged_at` / `pattern` / `auto_completed`) を 1 行追記する。再呼び出しは idempotent（二重イベントを書かない）。**この冪等性は helper 自身の再実行に対する性質であって、窓口の手打ちには効かない**（機序は [`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §4。上記の手打ち禁止はこのためにある）。task_id は `runs.pr_url` / `runs.branch`（active な runs 限定）から自動解決されるので省略もできる。
  - **`--task-id` を付けて呼ぶこと（Issue #828）**: `--task-id` があれば `--repo` 省略時のリポジトリを run → プロジェクト → GitHub URL で決定的に解決し、解決できなければ exit 2 で停止する。**`--task-id` も `--repo` も無い場合だけは旧来の事故経路（ja の同番号 PR を読む）が残る**（[`.claude/skills/org-pull-request/references/rationale.md`](references/rationale.md) §1）。set_run_pr_open と同じく 1 行目に `run_complete_on_merge: repo=... (source=...) PR #<N>: <PR タイトル>` が出るので書き込み先を目視確認する
  - **helper は runs.status を触らない**: dispatcher 側 pane close / worker_closed / worker-state final update が必要 (delegation-lifecycle-contract §T5)。helper は merge 事実のみ記録し、status flip と worker_dir 削除は窓口が下記の StateWriter で行う。run が非終端（`review` 等）のまま残っていれば、`merged` / `already` の行の後に `NOT COMPLETED task <id> status=<s>` と次の手順が 1 run 1 行で出る（Issue #1204）
  - **CLI 終了コード**: `merged` / `already` / `not_yet` は exit 0、`no_run`（runs に該当行なし）は exit 3 で失敗扱いになる。手動運用時は exit code を確認
- **パターン B / C のレジストリエントリ削除と最終 close は別途 StateWriter を呼ぶ**（markdown 直接編集禁止。run_complete_on_merge が `pr_state='merged'` と `completed_at` を既に書いているので、ここでは status flip と worker_dir 削除のみ行う）:
  ```bash
  # Mac/Linux
  python3 -c "
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
  # Windows native（Git Bash / PowerShell。本体は Mac/Linux 版と同一）
  py -3 -c "
  from pathlib import Path
  from tools.state_db import connect
  from tools.state_db.writer import StateWriter
  from tools.run_complete_on_merge import cleanup_pattern_c_local_md
  conn = connect('.state/state.db')
  abs_path = '<abs>'  # worker_dir の絶対パス（パターン B / C）
  with StateWriter(conn).transaction() as w:
      w.update_run_status('<task_id>', 'completed')  # post-commit hook が worker-{task}.md を archive
      w.remove_worker_dir(abs_path)  # パターン B / C のみ
  cleanup_pattern_c_local_md(conn, task_id='<task_id>', claude_org_root=Path('.').resolve(), worker_dir_abs=abs_path)
  "
  ```
  標準経路は上記 `tools/run_complete_on_merge.py`。想定外の出力を返したときは手作業で再現せず、Issue を切ってユーザー判断を仰ぐ
  - パターン A: lifecycle='active' のまま、run.status='completed' で snapshotter が available 相当の表示にする
  - パターン B / C: 物理 dir は別途処理（worktree remove / dir 保持）。レジストリエントリ削除は上記 with ブロック内に `w.remove_worker_dir('<abs>')` を追加
- **状態の切り替えを確かめる（Issue #1204）**: 上の StateWriter ブロックの後に `python3 tools/run_complete_on_merge.py --check-closed`（Windows native は `py -3`）を打ち、PR がマージ済みなのに `runs.status` が `in_use` / `review` 等のまま残っている run が無いことを確かめる。残っていれば 1 run 1 行で `NOT COMPLETED` と出て exit 1、無ければ `OK - no merged run left open` で exit 0。exit 0 を見るまでクローズを終えたとしない（残すとディスパッチャーの自動引き継ぎが `handover_missing_task_ids` で止まる）
- JSON snapshot は StateWriter post-commit hook が自動再生成 (Issue #284)

## 2b-iii. マージ後の次タスク提案（proactive next-dispatch）

2b-ii の post-merge cleanup（run COMPLETED 化・ペインクローズ・worktree 後処理）まで終わったら、窓口はユーザーの催促を待たず次の仕事候補を能動的に提示する。**候補生成はその場で `gh issue list` を即興で叩くのではなく、[`/work-discovery`](../work-discovery/SKILL.md) skill（= 決定的ツール `tools/work_discovery_scan.py` の triage 出力）を消費する**。判定基準（依存解決済み / 優先度 / 工数）が明文化され、提示に再現性・監査性が付く。方針の一次参照は [`CLAUDE.md`](../../../CLAUDE.md)「PR マージ後の次タスク提案（proactive next-dispatch）」、設計の一次参照は [`docs/design/work-discovery-triage.md`](../../../docs/design/work-discovery-triage.md) §8（post-merge 統合）。

- **post_merge トリガで走らせる**: `/work-discovery` を post-merge 文脈で起動する（scan を `--trigger post_merge`、空き pane があれば `--free-panes <数>` 付き）。候補 JSON に `generated_for: "post_merge"` が載り、**候補の並びの主キーはゴール台帳（`registry/goals/`）の条項順**で、「直近マージで unblock された / 自然な follow-up」（`unblocked_by_recent_merge`）や空き枠での `parallelizable` は同じ条項内の並びにだけ効く（設計 §4.2 / §8.1-3 / §12）。台帳の無いプロジェクトからは候補が出ない。**`--free-panes` に渡す数の算出定義（空き worker slot 数であって物理ペイン数ではない / 輸送層ごとの数え方 / 別タブ配置では `list_panes` ではなく `list_peers` から数える）の SoT は [`/work-discovery`](../work-discovery/SKILL.md) 側**なので、数え方に迷ったらそちらを参照する。
- **提示と人間ゲートは現行と互換**: triage 結果を設計 §5.2 形式（候補 N 件 + 推奨 1、推定軸には `(推定)`、除外枠も提示）で窓口が人間へ提示する。**人間が番号で選択 → 選ばれた候補は [`/org-delegate`](../org-delegate/SKILL.md) の Step 0 から**通常委譲フローに入る。候補生成の手段が即興から triage に替わるだけで、人間の操作・人間ゲートの外形は変えない（設計 §8.1-4）。
- **propose-only で停止**: 候補を出したら止める。rank 1（推奨）の自動着手・自動 commit・自動 PR はしない（着手判断は人間のみ。設計 INV-1 / INV-2）。本ステップ・`/work-discovery` が org-delegate を呼んだり spawn することも禁止。
- 手動・任意タイミングの提示（idle 時など）は同じ `/work-discovery` を `--trigger manual` で起動する。skill 内の exit code 分岐・レンダリング規則は [`/work-discovery`](../work-discovery/SKILL.md) を一次参照する。
