---
name: org-conveyor
description: >
  窓口が人間から「承認スコープの輪郭」を受けて、その内側で triage → worker 派遣 →
  iteration → verify → push → PR 作成 → CI 監視 を完了駆動で自走し、PR ごとに
  merge gate で必ず停止するベルトコンベア。/work-discovery + /org-delegate + verify +
  /org-pull-request + /org-escalation を呼ぶ薄い orchestrator。
  発動条件: 人間が「この範囲は自走してよい」というスコープ承認を明示し、複数候補を
  完了駆動で順次流したいとき（例「triage 上位の S 級バグ修正を空き pane 分まで自走、
  merge は都度わたしが判断」）。起動主体は窓口に限定。スコープ外候補・判断境界・退出条件に
  触れたら必ず halt する。merge 自動化はしない。
effort: medium
allowed-tools:
  - Read
  - Write # .state/conveyor/scope-contract.md の初回書き出し（Step 1）
  - Edit # スコープ契約の更新（再承認による拡大・早期停止）
  - Skill
  - TaskCreate # Claude Code 組込みの task-list（todo）ツール。観測可能性 backbone。repo 定義ではなくハーネス提供
  - TaskUpdate
  - TaskList
  - Bash(python3 tools/work_discovery_scan.py:*)
  - Bash(py -3 tools/work_discovery_scan.py:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(git diff:*) # verify applicability classifier の touch-domain 裏取り（read-only。worker branch が local に在るとき）
  - Bash(mkdir -p .state/conveyor:*) # Step 1 のスコープ契約書き出し先ディレクトリ確保（fresh checkout 対策）
  # 直接の transport 面は active transport（コード既定 broker）のみ列挙する。broker + renga の
  # union は非アクティブ transport のツールまで認可し per-transport auth モデルを迂回するため不可
  # （設計 SoT: notes/broker-skill-generator-design.md #9。union 却下）。送受信機構はサブスキルへ委譲し、
  # conveyor 自身が直接使うのは free-pane 会計（list_panes）とループ event のドレイン（check_messages）だけ。
  - mcp__org-broker__list_panes # free-pane 会計（バックプレッシャー）
  - mcp__org-broker__check_messages # broker pull フォールバック時の semantic event ドレイン
---

# org-conveyor: 承認スコープ内自走ループ（ベルトコンベア）

窓口が人間から **承認スコープの輪郭** を受け取り、それを機械契約として articulate したうえで、
その内側を `triage → /org-delegate → worker iteration → Codex round → verify（applicable なら必須）→
push → gh pr create → pr-watch CI 監視 → CI green` まで **完了駆動で自走** し、**PR ごとに
merge gate で必ず停止** する。人間は「レビュー待ち PR のキュー」を順次 merge するだけになる。

本スキルは **薄い orchestrator** であって、実作業の機構は既存スキルに委譲する。本スキルが固有に持つのは
**(1) 承認スコープ契約の articulate と gate、(2) 完了駆動ループとバックプレッシャー、(3) 観測可能性、
(4) 機械的退出条件** の 4 つだけ。受信モデル・ack・push/PR/CI・escalation の機構は下記スキルを呼ぶ:

- triage: [`/work-discovery`](../work-discovery/SKILL.md)（propose-only / 候補生成）
- 派遣: [`/org-delegate`](../org-delegate/SKILL.md)（Step 0〜、レーン選択・派遣・受信モデル・ack・監視）
- 検証: `/verify`（Claude Code **組込みスキル**。app を起動して実行ビヘイビアを観測する。app + 変更が居る **worker の worktree 内で worker が実行**する）+ [`.claude/skills/org-conveyor/references/verify-evidence.md`](references/verify-evidence.md)（conveyor 側の applicability classifier + エビデンス転記）
- PR: [`/org-pull-request`](../org-pull-request/SKILL.md)（push / PR / CI 監視 / レビューループ / マージ後クローズ）
- 判断仰ぎ: [`/org-escalation`](../org-escalation/SKILL.md)（worker escalation を人間へ）

## 位置づけ（他ループ系との差分）

| skill | 駆動 | 停止条件 | 人間ゲート |
|---|---|---|---|
| `/loop` | 時間駆動 | なし（間隔ごとに再実行） | なし |
| `/work-discovery` | イベント / 手動 | 候補提示で hard stop（propose-only） | 候補ごとに人間が番号選択 |
| `/org-delegate` | 単発委譲 | escalation 境界で人間に戻る | 派遣ごと / escalation ごと |
| **`/org-conveyor`** | **完了駆動ループ** | **PR ごとに merge gate で halt / 退出条件** | **起動時のスコープ承認（1 回）+ merge ごと** |

`/org-conveyor` は `/work-discovery` の per-candidate 人間選択を、**起動時に 1 回だけ取る「承認スコープ契約」**
へ畳み込んだものである。スコープ契約の内側では候補を再質問なしで自動投入し、**スコープ外の候補は scope 縁として
投入せず halt** する。これが propose-only / hard-stop の `/work-discovery` との本質的な差分。

## 不変条件（非交渉 / 破ってはならない）

- **INV-1 merge 承認は人間 gate**: スコープ契約は dispatch〜CI 監視を事前承認できるが、**merge は決して事前承認できない**。
  CI green に到達したら必ず停止し、PR を人間へ提示して merge を仰ぐ（`feedback-merge-approval` / `feedback-no-overgate-after-decision`
  の「不可逆点でのみ再承認」）。bare「OK」を merge 承認と解釈しない。
- **INV-2 scope 境界に触れたら必ず halt**: スコープ契約の述語に合致しない候補・判断・差分が現れたら、自走を止めて
  [`/org-escalation`](../org-escalation/SKILL.md) 経由で人間に上げる（`feedback-no-stopgap` / 場当たり継続をしない）。
- **INV-3 worker からの judgment escalation は人間へ**: worker の「判断仰ぎ」「承認を仰ぎ」「スコープ拡張」「ブロッカー」は
  窓口が一次承認せず [`/org-escalation`](../org-escalation/SKILL.md) で人間へ。conveyor は escalation を自動判断しない。
- **INV-4 propose-only の継承**: 候補生成は [`/work-discovery`](../work-discovery/SKILL.md)（決定的ツール）に委ね、conveyor は
  その出力をスコープ契約で gate するだけ。候補の中身を自前調査・実装しない。
- **INV-5 起動主体は窓口のみ**: 委譲済み worker は本スキルを起動しない（「1 worker = 1 task = 1 scope」原則。[`CLAUDE.md`](../../../CLAUDE.md)「役割の境界」）。
- **INV-6 merge 自動化・escalation 自動判断・`/loop` 置換をしない**（Non-goals 節）。

## 輸送層（transport）両系

**per-transport 認可（union 不可）**: 本スキルが直接使う transport ツールは free-pane 会計の `list_panes` だけで、
allowed-tools には **その deployment で active な単一 transport の面のみ** を列挙する（broker + renga を union で並べると
非アクティブ transport のツールまで認可され per-transport auth モデルを迂回するため。設計 SoT:
notes/broker-skill-generator-design.md #9。union 却下）。本リポジトリの active transport は **`broker`**（コード既定
`DEFAULT_TRANSPORT=broker`、`org-broker-channel` 稼働中）なので allowlist は broker 面で render してある。`renga` で運用する
deployment（opt-in 切戻し）では、この surface を renga 面へ **per-transport 再生成** する（`mcp__org-broker__*` →
`mcp__renga-peers__*`、引数形・セマンティクスは同一。settings.local.json の per-transport 生成と同じ境界。フル per-transport
render が要るなら他の transport 参照スキルと同じく `.md.in` + manifest 化が follow-up 候補）。送受信機構（ack・relay・escalation・
push / PR）の輸送依存差・二フレーム関係・spawn 儀式・エラー分岐は本スキルが呼ぶ各スキルが per-transport に持つので、総説
[`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節とフル版注記 [`/org-escalation`](../org-escalation/SKILL.md) 冒頭の
同名 note を一次参照する（本スキルは重複コピーを置かない）。

**受信は transport ツールに依存しない**: conveyor 固有の輸送依存は **完了 / CI 遷移の受信** に集約されるが、ここは
worker 報告・pr-watch の `CI_COMPLETED` / `PR_MERGED` が **in-band push で注入される**（broker は channel sidecar、renga は
`<channel source="renga-peers">`。pr-watch の `tools/peer_notify.py: notify_peer` は raw env 判定で active transport を選ぶ）。
in-band 注入の受信に tool 呼び出しは要らず、push 失効時の最終フォールバックは **events テーブルのポーリング**（Read / Bash、
transport 非依存）なので、**broker の `check_messages` 単独待受や固定 sleep ポーリングに依存しない**。したがって conveyor は
「`CI_COMPLETED` / `PR_MERGED` / worker 報告という semantic event が届いたこと」を契機にループを 1 周進める。受信機構の
per-transport 詳細（pr-watch の二フレーム受信注記）は呼び先の [`/org-pull-request`](../org-pull-request/SKILL.md) 2b-i に委ねる。

## Step 1: 承認スコープ契約を articulate する（起動時・人間ゲート）

ループを回す **前に**、人間から受けた承認の輪郭を機械契約として articulate し、人間に確認を取る。
スコープ契約は `/work-discovery` の per-candidate 選択の代わりに置かれる **唯一の事前人間ゲート** であり、
この確認なしにループを開始してはならない。

- テンプレート・フィールド定義・「何を事前承認し / 何を絶対に承認しないか」の境界は
  [`.claude/skills/org-conveyor/references/scope-contract.md`](references/scope-contract.md) を一次参照する。
- 確定した契約は `.state/conveyor/scope-contract.md` に書き出す（ループ中の gate 判定で読み戻す SoT）。書き出し前に
  `mkdir -p .state/conveyor` で親ディレクトリを確保する（`.state/` は gitignore 配下で fresh checkout には `conveyor/`
  サブディレクトリが無いため。idempotent）。
- **契約が事前承認する範囲**: スコープ述語に合致する候補に対する `triage 投入 → /org-delegate 派遣 →
  worker iteration → verify → push → gh pr create → pr-watch CI 監視`。push / PR 作成の「ユーザー明示承認」
  前提（[`/org-pull-request`](../org-pull-request/SKILL.md) 2b-i）は、**この起動時スコープ承認が満たす**
  （bare な per-PR OK ではなく、人間が明示した持続的スコープ承認の記録）。
- **契約が決して承認しない範囲**: **merge**（INV-1）。merge は常に PR ごとの独立人間ゲート。CI green で halt し提示する。
- スコープ述語は機械的に判定できる形で書く（例: `label:bug AND size:S`、`#637 の follow-up に限る`、
  `PR #635 の review feedback round を ≤6 まで`）。判定不能な候補は scope 縁として扱い投入しない（INV-2）。
- 並列上限は **起動時の free pane 数**（バックプレッシャー節）。退出予算（Codex round 上限 / 連続 false-positive
  閾値 / 時間予算 / 最大反復数）も契約に明記する（[`.claude/skills/org-conveyor/references/exit-conditions.md`](references/exit-conditions.md)）。

## Step 2: 完了駆動ループ本体（ベルトコンベア）

スコープ契約確定後、以下を **完了駆動** で回す。各反復は「空き pane を埋める → 完了/CI 遷移を待つ →
進んだ分だけ次を投入する」の 1 周。**固定 sleep でポーリングしない**（state transition の到着を契機にする）。

1. **観測可能性サマリを出力**（反復開始時。「観測可能性」節）。
2. **triage**: [`/work-discovery`](../work-discovery/SKILL.md) を起動する。conveyor 文脈では
   `tools/work_discovery_scan.py --trigger post_merge --free-panes <空き pane 数>` 相当で候補 JSON を得る
   （空き枠があると `parallelizable` 候補のランクが上がりベルトを埋めやすい）。候補生成は決定的ツールに委ねる（INV-4）。
3. **scope gate**: 各候補をスコープ契約の述語に照合する。
   - **合致 + 空き pane あり** → 投入対象。合致候補に対する派遣は契約で事前承認済みなので、per-candidate の
     人間確認は **しない**（ここが `/work-discovery` の hard-stop との差分）。
   - **非合致 / 判定不能** → **投入しない**。ベルトがその候補に触れざるを得ない（= スコープを使い切って外側しか残っていない等）
     状況なら、自走を止め [`/org-escalation`](../org-escalation/SKILL.md) で人間に上げて halt する（INV-2）。
4. **派遣**: 投入対象を空き pane 数まで [`/org-delegate`](../org-delegate/SKILL.md) の Step 0 から回す。レーン選択
   （軽量 / 重量）・brief 生成・ディスパッチャー経由派遣は org-delegate に委ねる。project 文脈はスコープ契約に
   pre-resolve 済みとし、org-delegate が **人間入力を要するチェック項目（曖昧用語・OS 前提・incorporation 戦略等）に
   当たったらそれは scope 縁** として halt する（自動で人間質問を埋めない）。
5. **worker iteration**: worker が走り、受信モデル（push 一次 / pull フォールバック）で報告する。ack・進捗 Progress Log・
   完了 REVIEW 遷移・監視/介入は [`/org-delegate`](../org-delegate/SKILL.md) Step 5 に委ねる。判断仰ぎ/ブロッカーは
   [`/org-escalation`](../org-escalation/SKILL.md) へ（INV-3）。**本スキルはこれらの機構を再規定しない**。
6. **verify（条件付き必須）**: worker 完了時、conveyor は [`.claude/skills/org-conveyor/references/verify-evidence.md`](references/verify-evidence.md) の
   applicability classifier を適用する。app code に触れていれば **worker が worktree 内で `/verify`（Claude Code 組込みスキル）
   または同等の app 起動を必須実行**し（実作業は worker に委譲＝conveyor 自身は app を起動しない）、conveyor はその実行有無を gate し、
   worker が返した再現コマンド + 出力 / スクショパスを **エビデンスとして PR 本文 `## Test plan` へ転記** する。判定不能 = scope 縁として halt（INV-2）。
   並列 verify のポート衝突は [`.claude/skills/org-conveyor/references/dynamic-ports.md`](references/dynamic-ports.md) の動的ポート割当規律で避ける。
7. **push / PR / CI 監視**: [`/org-pull-request`](../org-pull-request/SKILL.md) 2b-i を発動する（push → `gh pr create` →
   `pr-watch`）。前提の「ユーザー明示承認」はスコープ契約の事前承認が満たす（Step 1）。`CI_COMPLETED` の到着で次へ。
8. **CI green → merge gate で HALT**: CI green に達したら **merge せず停止** する。awaiting_user を emit
   （`gate=ci_green_merge_gate`、[`/org-pull-request`](../org-pull-request/SKILL.md) 2b-i / [`/org-escalation`](../org-escalation/SKILL.md)
   と同じ canonical emit）し、人間向け理解サマリ（full タスク）と PR を提示して merge を仰ぐ（INV-1）。
   conveyor はこの PR についてここで停止する（ベルト全体は停止しない）。
9. **バックプレッシャー**: 人間が merge し post-merge cleanup（[`/org-pull-request`](../org-pull-request/SKILL.md) 2b-ii）で
   pane が解放されたら、**解放枠に triage の次候補を即時投入**する（2 へ戻る）。**PR キューに上限は設けない**
   （未 merge PR が N 件溜まっても新規 triage を止めない）。**人間の merge が自然なバックプレッシャー（natural gate）** であり、
   人間が merge を止めれば pane が解放されずベルトが自然に詰まって停止する。
10. **state transition 直後に観測可能性サマリを再出力**（「観測可能性」節）。
11. **退出条件チェック**（[`.claude/skills/org-conveyor/references/exit-conditions.md`](references/exit-conditions.md)）。いずれか該当で halt。

> **再入と引き継ぎ**: 窓口 context が長くなったら [`/secretary-handover`](../secretary-handover/SKILL.md) →
> `/clear` → [`/secretary-resume`](../secretary-resume/SKILL.md) で引き継ぐ。スコープ契約 `.state/conveyor/scope-contract.md`
> と TaskList が SoT なので、resume 後は契約を読み戻してベルトを継続できる（ループ状態をメモリに依存させない）。

## 観測可能性（必須・能動出力）

ベルトコンベアの稼働状況を **人間が一目で読めるタスクリスト要約** として能動出力する。これは任意の運用習慣ではなく
**skill 契約**であり、ベルト稼働中は省略しない（人間が「いまどこまで進んだか」を催促なしで把握できる状態を保つ）。

- **状態 backbone は `TaskCreate` / `TaskUpdate` / `TaskList`**（Claude Code 組込みの task-list / todo ツール。repo 定義ではなく
  ハーネスが窓口セッションに提供する標準ツールで、`status` は `pending` / `in_progress` / `completed` を取る）: ベルト上の各候補を
  1 タスクとして起こし、state transition ごとに `TaskUpdate` で `status` を遷移させる（`pending` → `in_progress` → `completed`）。これは窓口の標準 UI なので
  人間の認知負荷ゼロで、native 出力をそのまま人間向け窓に流せる。スコープ契約に紐づく conveyor 自身の制御タスク
  （triage / 派遣 / verify / PR）と、候補ごとのタスクの両方をこの backbone に乗せてよい。
- **要求フォーマット**（`TaskList` 由来の状態を 1 枚に整形）:
  ```
  3 tasks (1 done, 1 in progress, 1 open)
  ✔ Issue #637 にコメントで決定事項を追記
  ◼ /org-delegate で worker を派遣して SKILL.md を起…
  ◻ worker 完了報告 → push → PR 作成 → CI 監視 → mer…
  ```
  - 1 行サマリ: `N tasks (X done, Y in progress, Z open)`（`X`=completed / `Y`=in_progress / `Z`=pending）。
  - 各タスク 1 行: 状態記号 + タイトル。**状態記号**は `✔` = completed / `◼` = in_progress / `◻` = pending。
  - タイトルが長ければ末尾を `…` で省略する（1 行に収める）。
- **出力タイミング**（skill 契約として明文化）:
  1. **ループ反復開始時**（新規 triage 投入後）。
  2. **state transition 直後**（worker 派遣 / verify 完了 / CI green / PR merged / pane 解放 等の遷移ごと）。
  3. **長時間 verify / CI watch の最中、一定間隔で**（例: 5 分毎）。**ただし前回出力から状態に変化が無ければ抑制する**
     （無変化の定期出力で人間の通知疲労を作らない）。
- halt（merge gate / 退出条件 / scope 縁）に入るときも、停止直前にこのサマリを出して「いまベルトのどこで止まったか」を残す。

## verify 統合（条件付き必須）

- **applicability classifier**: worker が diff の touch domain（app code / docs / config / fixture）を判定し、
  **app code に触れている場合のみ `/verify` を必須** とする。**判定不能 = scope 縁扱いで停止**（INV-2）。
- **エビデンス転記**: 完了報告に再現コマンド + 出力 / スクショパスを含め、PR 本文 `## Test plan` へ自動転記する。
- **ポート衝突**: worktree 並列 verify で固定ポート前提のアプリ（Next.js / broker サーバー等）が衝突するため、
  **動的ポート割り当て**（worker / app 側に env で port を受け取らせる規律）で避ける。
- 詳細は [`.claude/skills/org-conveyor/references/verify-evidence.md`](references/verify-evidence.md) と [`.claude/skills/org-conveyor/references/dynamic-ports.md`](references/dynamic-ports.md) を一次参照。

## 退出条件（機械的）

以下のいずれかに該当したら自走を止め、観測可能性サマリ + 理由を人間へ提示して halt する。詳細・閾値の決め方・
halt 後の扱いは [`.claude/skills/org-conveyor/references/exit-conditions.md`](references/exit-conditions.md) を一次参照する。

- **Codex round 最大数到達** → 停止
- **連続 false-positive 数が閾値到達** → 停止
- **時間予算超過 / 最大反復数到達** → 停止
- **worker escalation** → 停止（[`/org-escalation`](../org-escalation/SKILL.md) 経由、INV-3）
- **scope 縁検知**（非合致候補 / org-delegate チェック項目で人間入力要求 / verify 判定不能）→ 停止（INV-2）

## やらないこと（Non-goals）

- **merge 自動化はしない**（INV-1。CI green で必ず人間へ）。
- **escalation の自動判断はしない**（[`/org-escalation`](../org-escalation/SKILL.md) に委譲、INV-3）。
- **`/loop` の置換ではない**（時間駆動ではなく完了駆動。間隔ポーリングをしない）。
- **スコープ外候補の自動投入をしない**（INV-2 / INV-4）。
- **worker 作業の代行をしない**（ファイル編集 / commit / テストは worker。窓口は司令塔）。
- **PR キュー上限を設けない**（人間 merge が natural gate）。

## 起動主体とパス解決

- **起動主体は窓口だけ**（INV-5）。worker / dispatcher / curator は起動しない。
- 本スキル中の `tools/...` / `docs/...` / `.state/...` 表記はリポジトリルート相対。窓口セッションの CWD は
  リポジトリルートなのでそのまま実行できる。Windows では `python3` を `py -3` に読み替える（allowed-tools に両形登録済み）。
- references へのリンク（`references/...`）は本 SKILL.md からの document-relative。表記規約は
  [`docs/contributing/markdown-conventions.md`](../../../docs/contributing/markdown-conventions.md) に従う。
