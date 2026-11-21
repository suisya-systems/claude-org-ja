---
name: org-conveyor
description: >
  人間が承認したスコープの内側で triage → worker 派遣 → verify → push → PR 作成 → CI 監視
  を完了駆動で自走し、PR ごとに merge gate で停止する（acceptance 方式では止める線に当たったときだけ停止する）窓口用のベルトコンベア。
  「この範囲は自走してよい」「triage 上位の S 級バグを空き pane 分まで流して」等、
  人間がスコープ承認を明示して複数候補を順に流したいときに窓口が起動する。
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
  - Bash(python3 tools/journal_append.py:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python3 tools/merge_approval_mode.py:*) # INV-1 のマージ承認方式（human / acceptance）を読む
  - Bash(py -3 tools/merge_approval_mode.py:*)
  - Bash(python3 tools/belt_slots.py:*) # 空き枠の数え方・park・hold（待ちだけの作業は枠に数えない）
  - Bash(py -3 tools/belt_slots.py:*)
  - Bash(python3 tools/gen_delegate_payload.py revive:*) # park した作業の立て直し
  - Bash(py -3 tools/gen_delegate_payload.py revive:*)
  - Bash(git diff:*) # verify applicability classifier の touch-domain 裏取り（read-only。worker branch が local に在るとき）
  - Bash(mkdir -p .state/conveyor:*) # Step 1 のスコープ契約書き出し先ディレクトリ確保（fresh checkout 対策）
  # 直接の transport 面は単一 transport（checked-in はコード既定 broker の面）のみ列挙する。broker + renga の
  # union は非アクティブ transport のツールまで認可し per-transport auth モデルを迂回するため不可
  # （union は却下）。送受信機構はサブスキルへ委譲し、
  # conveyor 自身が直接使うのは free-pane 会計（list_panes）とループ event のドレイン（check_messages）だけ。
  - mcp__org-broker__list_panes # free-pane 会計（バックプレッシャー）
  - mcp__org-broker__check_messages # broker pull フォールバック時の semantic event ドレイン
---

# org-conveyor: 承認スコープ内自走ループ（ベルトコンベア）

窓口が人間から **承認スコープの輪郭** を受け取り、それを機械契約として articulate したうえで、
その内側を `triage → /org-delegate → worker iteration → Codex round → verify（applicable なら必須）→
push → gh pr create → pr-watch CI 監視 → CI green` まで **完了駆動で自走** し、PR ごとに
merge gate で停止する。人間は「レビュー待ち PR のキュー」を順次 merge するだけになる。マージ承認の方式が `acceptance`
（[`registry/org-config.md`](../../../registry/org-config.md)「Merge Approval」）なら、止める線に当たらない PR はマージまで進み、
人間が見るのは止まった PR と結果の一覧になる（INV-1）。

本スキルは **薄い orchestrator** であって、実作業の機構は既存スキルに委譲する。本スキルが固有に持つのは
**(1) 承認スコープ契約の articulate と gate、(2) 完了駆動ループとバックプレッシャー、(3) 観測可能性、
(4) 機械的退出条件** の 4 つだけ。受信モデル・ack・push/PR/CI・escalation の機構は下記スキルを呼ぶ:

- triage: `/work-discovery`（propose-only / 候補生成）
- 派遣: `/org-delegate`（Step 0〜、レーン選択・派遣・受信モデル・ack・監視）
- 検証: `/verify`（Claude Code **組込みスキル**。app を起動して実行ビヘイビアを観測する。app + 変更が居る **worker の worktree 内で worker が実行**する）+ [`.claude/skills/org-conveyor/references/verify-evidence.md`](references/verify-evidence.md)（conveyor 側の applicability classifier + エビデンス転記）
- PR: `/org-pull-request`（push / PR / CI 監視 / レビューループ / マージ後クローズ）
- 判断仰ぎ: `/org-escalation`（worker escalation を人間へ）

他のループ系スキル（`/loop` / `/work-discovery` / `/org-delegate`）との違いは
[`.claude/skills/org-conveyor/references/rationale.md`](references/rationale.md) にまとめてある。

## 不変条件

- **INV-1 merge 承認は `merge_approval_mode` に従う**: 方式は `python3 tools/merge_approval_mode.py` の出力（設定は
  [`registry/org-config.md`](../../../registry/org-config.md)「Merge Approval」。設定値は `acceptance`、キーが無い・不正なときは安全側の `human`）。`human` ではスコープ契約は
  dispatch〜CI 監視を事前承認できるが、merge は事前承認の対象にしない（CI green で停止し、PR を人間へ提示して merge を仰ぐ。
  bare「OK」を merge 承認と解釈しない）。`acceptance` では、スコープ契約の内側で `/org-pull-request`
  2b-i の **止める線** に 1 つも当たらない PR に限り、受け入れテストのゲートと CI の通過でマージまで自走する。
  ゲート通過はスコープ承認の代わりにならず、止める線に当たったら `human` と同じく停止する。
- **INV-2 scope 境界に触れたら halt**: スコープ契約の述語に合致しない候補・判断・差分が現れたら、自走を止めて
  `/org-escalation` 経由で人間に上げる（場当たり継続をしない）。
- **INV-3 worker からの judgment escalation は人間へ**: worker の「判断仰ぎ」「承認を仰ぎ」「スコープ拡張」「ブロッカー」は
  窓口が一次承認せず `/org-escalation` で人間へ。conveyor は escalation を自動判断しない。
- **INV-4 propose-only の継承**: 候補生成は `/work-discovery`（決定的ツール）に委ね、conveyor は
  その出力をスコープ契約で gate するだけ。候補の中身を自前調査・実装しない。
- **INV-5 起動主体は窓口のみ**: 委譲済み worker は本スキルを起動しない（「1 worker = 1 task = 1 scope」原則。[`CLAUDE.md`](../../../CLAUDE.md)「役割の境界」）。
- **INV-6 `/loop` の置換ではない**: 時間駆動ではなく完了駆動で、間隔ポーリングをしない。PR キューに上限は設けない（人の返事・マージだけを待つ作業は枠に数えないので、ベルトの上限は契約の `max_parallel` と退出予算。Step 2-9）。

## 輸送層（transport）両系

**per-transport 認可（union 不可）**: 本スキルが直接使う transport ツールは free-pane 会計の `list_panes` と、pull フォールバック時のループ event ドレインの `check_messages` だけで、
allowed-tools には **その deployment で active な単一 transport の面のみ** を列挙する（broker + renga を union で並べると
非アクティブ transport のツールまで認可され per-transport auth モデルを迂回するため。union は却下）。allowlist は **コード既定**フレーム（`tools/transport.py` の
`DEFAULT_TRANSPORT=broker`）の broker 面で render してある。これは render 面の選択であって、本リポジトリの運用既定は renga
（root `CLAUDE.md`「輸送層（transport）両系」の二フレーム注記）なので、運用 transport が renga の環境ではこの allowlist は運用と食い違う。`renga` で運用する
deployment（運用既定・opt-in 切戻し）では、この surface を renga 面へ **per-transport 再生成** する（`mcp__org-broker__*` →
`mcp__renga-peers__*`、引数形・セマンティクスは同一。settings.local.json の per-transport 生成と同じ境界）。送受信機構（ack・relay・escalation・
push / PR）の輸送依存差・二フレーム関係・spawn 儀式・エラー分岐は本スキルが呼ぶ各スキルが per-transport に持つので、総説
[`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節とフル版注記 `/org-escalation` 冒頭の
同名 note を一次参照する（本スキルは重複コピーを置かない）。

**cross-tab の free-pane 会計**: renga の `list_panes` は caller のタブにスコープされるので、別タブの worker を数え落として
空き容量を過大申告しうる。空き枠の数え方（別タブ配置と overflow 配線時の扱いを含む）は
`/work-discovery` の `--free-panes` 節を正本とし、conveyor もそれに従う。

- **実害の限定**: この過大申告が生むのは**誤ランキングと、runtime に拒否される派遣試行まで**。**実際の overspawn に至るのは
  runtime の防止策を迂回する実装をした場合のみ**である（`delegate-plan` は overflow 配線時に `--peers-json` 欠落を
  `input_invalid` で拒否し、fleet ceiling を census + 未 bind 予約で数える）。
- **本リポジトリでは非該当**: active transport は broker（コード既定 `DEFAULT_TRANSPORT=broker`）でタブ概念を持たないため、
  上記は renga 面で背景タブ配置を配線した deployment にだけ効く。conveyor 自身に peer census を叩かせる設計にはしない
  （allowed-tools は `list_peers` を許可しない。容量会計は dispatcher / runtime 側の出力を消費する）。

**受信は transport ツールに依存しない**: conveyor 固有の輸送依存は **完了 / CI 遷移の受信** に集約されるが、ここは
worker 報告・pr-watch の `CI_COMPLETED` / `PR_MERGED` が **in-band push で注入される**（broker は channel sidecar、renga は
`<channel source="renga-peers">`。pr-watch の `tools/peer_notify.py: notify_peer` は raw env 判定で active transport を選ぶ）。
in-band 注入の受信に tool 呼び出しは要らず、push 失効時の最終フォールバックは **events テーブルのポーリング**（Read / Bash、
transport 非依存）なので、**broker の `check_messages` 単独待受や固定 sleep ポーリングに依存しない**。したがって conveyor は
「`CI_COMPLETED` / `PR_MERGED` / worker 報告という semantic event が届いたこと」を契機にループを 1 周進める。受信機構の
per-transport 詳細（pr-watch の二フレーム受信注記）は呼び先の `/org-pull-request` 2b-i に委ねる。

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
  前提（`/org-pull-request` 2b-i）は、**この起動時スコープ承認が満たす**
  （bare な per-PR OK ではなく、人間が明示した持続的スコープ承認の記録）。
- **merge の扱い（INV-1）**: `human` では契約は merge を承認しない（PR ごとの人間ゲートで、CI green で halt して提示する）。
  `acceptance` では止める線に当たらない PR の merge まで契約が覆う。契約には起動時の `merge_approval_mode` を「承認時の値」として
  記録し、人間への確認文に含める。稼働中に設定が `human` → `acceptance` へ変わっても、承認時の値が `human` なら再承認まで `human` として扱う（逆向きは即時に従う）。
- スコープ述語は機械的に判定できる形で書く（例: `label:bug AND size:S`、`<Issue> の follow-up に限る`、
  `<PR> の review feedback round を ≤6 まで`）。判定不能な候補は scope 縁として扱い投入しない（INV-2）。
- 並列上限は契約の `max_parallel` で、**手を動かしているワーカーの数**で数える（`python3 tools/belt_slots.py count` の `busy`。人のマージ承認・判断・完了報告の承認だけを待つ作業は数えない）。退出予算（Codex round 上限 / 連続 false-positive
  閾値 / 時間予算 / 最大反復数）も契約に明記する（[`.claude/skills/org-conveyor/references/exit-conditions.md`](references/exit-conditions.md)）。

## Step 2: 完了駆動ループ本体（ベルトコンベア）

スコープ契約確定後、以下を **完了駆動** で回す。各反復は「空き pane を埋める → 完了/CI 遷移を待つ →
進んだ分だけ次を投入する」の 1 周。**固定 sleep でポーリングしない**（state transition の到着を契機にする）。

1. **観測可能性サマリを出力**（反復開始時。「観測可能性」節）。
2. **triage**: `/work-discovery` を起動する。conveyor 文脈では
   `tools/work_discovery_scan.py --trigger post_merge --free-panes <空き枠> --design-candidates` 相当で候補 JSON を得る。空き枠は
   `python3 tools/belt_slots.py count` の `free_slots`。その出力の `parkable`（待ちだけになったのにペインを持っている作業）が
   空でなければ、**先に 1 件ずつ park する**（`python3 tools/belt_slots.py park --task <task_id>` → 出力の `close_pane` の 1 行を
   `/org-pull-request` 2b-ii と同じ送り方でディスパッチャーへ送る。run は `review` のまま、作業ツリーは残る）
   （空き枠があると同一条項内の `parallelizable` 候補のランクが上がりベルトを埋めやすい）。候補生成は決定的ツールに委ねる（INV-4）。
   - **候補プールはゴール台帳（`registry/goals/`）の条項に当たった候補だけ**に狭まっている。スコープ述語はこの狭まったプールに対して評価する。
   - `--repo` 無しの暗黙 scan では goal 段が repo を解決できないので、`gh repo view` で slug を求めて `--repo` を明示する。
   - **スコープ内の repo が `goal_rank.goal_unset_repos[]` / `goal_rank.goal_errors[]` に出たら「候補が無い」と読まず halt** し、ゴール設定か `--rank-mode legacy`（人間が明示指示したときだけ）での実行かを人間に仰ぐ。
   - scan が exit 2（判定段の失敗。`goal_rank.judge.status` = `failed` / `cooldown` / `budget_exhausted`、fail-closed）なら候補ゼロと読まず halt し、状態と理由を人間へ伝える。
3. **scope gate**: 各候補をスコープ契約の述語に照合する。
   - **合致 + 空き pane あり** → 投入対象。合致候補に対する派遣は契約で事前承認済みなので、per-candidate の
     人間確認は **しない**（ここが `/work-discovery` の hard-stop との差分）。
   - **設計まで進める候補**（契約の「依存で着地を待つ後続」が「設計まで」のとき）: scan の `design_candidates[]` のうち述語に合致するものを、空き枠の範囲で設計までの依頼として投入し、`python3 tools/belt_slots.py design-ahead --task <task_id> --issue <OWNER/REPO#N> --blocked-by <blocking_refs>` で記録する（設計を報告した run は待ちの理由 `dependency` になり park できる）。
   - **依存が着地した設計済みの run**: scan の JSON を `python3 tools/belt_slots.py design-ready` に渡し、返った `ready` の run を `python3 tools/gen_delegate_payload.py revive --task-id <task_id> --reason dependency_landed` で実装に進める（空き枠を使う）。
   - **非合致 / 判定不能** → **投入しない**。ベルトがその候補に触れざるを得ない（= スコープを使い切って外側しか残っていない等）
     状況なら、自走を止め `/org-escalation` で人間に上げて halt する（INV-2）。
4. **派遣**: 投入対象を空き枠（`free_slots`）まで `/org-delegate` の Step 0 から回す。レーン選択
   （軽量 / 重量）・brief 生成・ディスパッチャー経由派遣は org-delegate に委ねる。project 文脈はスコープ契約に
   pre-resolve 済みとし、org-delegate が **人間入力を要するチェック項目（曖昧用語・OS 前提・incorporation 戦略等）に
   当たったらそれは scope 縁** として halt する（自動で人間質問を埋めない）。
5. **worker iteration**: worker が走り、受信モデル（push 一次 / pull フォールバック）で報告する。ack・進捗 Progress Log・
   完了 REVIEW 遷移・監視/介入は `/org-delegate` Step 5 に委ねる。判断仰ぎ/ブロッカーは
   `/org-escalation` へ（INV-3）。**本スキルはこれらの機構を再規定しない**。
   - **人に聞いたら、同じ手番で次を入れる**: 判断仰ぎを人間へ上げた・完了報告の承認待ちに入った手番の中で、ターンを終える前に
     `python3 tools/belt_slots.py count` を叩き、`parkable` を park し、`free_slots` の分だけ次の候補を 3〜4 で投入する。
     質問の答えを待つ間に枠を空けたままにしない（作業途中の判断仰ぎ＝run が `in_use` はペインを残すが、枠には数えない）。
     投入しないなら `python3 tools/belt_slots.py hold --reason "<理由>"` で理由を残す（ディスパッチャーの見回りが
     空き枠と候補が 20 分続くと `BELT_IDLE_SUSPECTED` で知らせてくる。hold はそれを期限つきで止める）。
   - **park した作業に指摘・CI 失敗・衝突・人の回答が来たら**: 同じ作業ツリーに新しいワーカーを立てる
     （`python3 tools/gen_delegate_payload.py revive --task-id <task_id> --reason <review|ci_failed|conflict|answer> --feedback-file <本文>`。
     出力の `send_plan_path` を `/org-delegate` の apply 後と同じ手順でディスパッチャーへ送る）。空き枠が無ければ revive は断るので、
     枠が空いたときに優先して入れる。
6. **verify（条件付き必須）**: worker 完了時、conveyor は [`.claude/skills/org-conveyor/references/verify-evidence.md`](references/verify-evidence.md) の
   applicability classifier を適用する。app code に触れていれば **worker が worktree 内で `/verify`（Claude Code 組込みスキル）
   または同等の app 起動を必須実行**し（実作業は worker に委譲＝conveyor 自身は app を起動しない）、conveyor はその実行有無を gate し、
   worker が返した再現コマンド + 出力 / スクショパスを **エビデンスとして PR 本文 `## Test plan` へ転記** する。判定不能 = scope 縁として halt（INV-2）。
   並列 verify のポート衝突は [`.claude/skills/org-conveyor/references/dynamic-ports.md`](references/dynamic-ports.md) の動的ポート割当規律で避ける。
7. **push / PR / CI 監視**: `/org-pull-request` 2b-i を発動する（push → `gh pr create` →
   `pr-watch`）。前提の「ユーザー明示承認」はスコープ契約の事前承認が満たす（Step 1）。`CI_COMPLETED` の到着で次へ。
8. **CI green → merge gate**: `/org-pull-request` 2b-i のマージ承認方式の分岐に従う（INV-1。
   方式は契約の承認時の値で狭める）。`human`、または `acceptance` で止める線に当たったときは merge せずに停止し、awaiting_user を emit
   （`gate=ci_green_merge_gate`、`/org-pull-request` 2b-i / `/org-escalation`
   と同じ canonical emit）して、人間向け理解サマリ（full タスク）・PR・当たった線を提示して merge を仰ぐ。
   conveyor はこの PR についてここで停止する（ベルト全体は停止しない）。**同じ手番で** その run を park し
   （`python3 tools/belt_slots.py park --task <task_id>`。CI が通った head と作業ツリーが一致しないと断るので、そのときはペインを残す）、
   空いた枠に次の候補を投入してからターンを終える。`acceptance` で止める線に当たらなければ、2b-i の
   「マージのコマンド」でマージし、結果を観測可能性サマリと事後報告に残す。
9. **バックプレッシャー**: 枠が空くのは、ワーカーが待ちに入って park したとき（5・8）と、merge 後の post-merge cleanup
   （`/org-pull-request` 2b-ii）でペインが解放されたとき。どちらでも **空いた枠に triage の次候補を即時投入**する（2 へ戻る）。
   **PR キューに上限は設けない**（未 merge PR が N 件溜まっても新規 triage を止めない）。人の返事・merge だけを待つ作業は
   枠に数えないので、人が merge を止めてもベルトは詰まらない。ベルトの上限は `max_parallel` と退出予算
   （`time_budget` / `max_iterations`、[`.claude/skills/org-conveyor/references/exit-conditions.md`](references/exit-conditions.md)）。
   ベルトを止めたいときは、人の指示で窓口がスコープ契約の `belt_state` を `halted` に書き換える。
10. **state transition 直後に観測可能性サマリを再出力**（「観測可能性」節）。
11. **退出条件チェック**（[`.claude/skills/org-conveyor/references/exit-conditions.md`](references/exit-conditions.md)）。いずれか該当で halt。

> **再入と引き継ぎ**: 窓口 context が長くなったら `/secretary-handover` →
> `/clear` → `/secretary-resume` で引き継ぐ。スコープ契約 `.state/conveyor/scope-contract.md`
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
  ✔ Issue にコメントで決定事項を追記
  ◼ /org-delegate で worker を派遣して SKILL.md を起…
  ◻ worker 完了報告 → push → PR 作成 → CI 監視 → mer…
  ```
  - 1 行サマリ: `N tasks (X done, Y in progress, Z open)`（`X`=completed / `Y`=in_progress / `Z`=pending）。
  - 各タスク 1 行: 状態記号 + タイトル。**状態記号**は `✔` = completed / `◼` = in_progress / `◻` = pending。
  - タイトルが長ければ末尾を `…` で省略する（1 行に収める）。
- **出力タイミング**（skill 契約として明文化）:
  1. **ループ反復開始時**（新規 triage 投入後）。
  2. **state transition 直後**（worker 派遣 / verify 完了 / CI green / PR merged / pane 解放 等の遷移ごと）。状態が変わらない間は出さない（無変化の定期出力で人間の通知疲労を作らない）。
- halt（merge gate / 退出条件 / scope 縁）に入るときも、停止直前にこのサマリを出して「いまベルトのどこで止まったか」を残す。

## 起動主体とパス解決

- **起動主体は窓口だけ**（INV-5）。worker / dispatcher / curator は起動しない。
- 本スキル中の `tools/...` / `docs/...` / `.state/...` 表記はリポジトリルート相対。窓口セッションの CWD は
  リポジトリルートなのでそのまま実行できる。Windows では `python3` を `py -3` に読み替える（allowed-tools に両形登録済み）。
- references へのリンク（`references/...`）は本 SKILL.md からの document-relative。リンク表記は
  ``[`<repo-root path>`](<document-relative path>)`` に従う。
