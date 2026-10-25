# 窓口・ディスパッチャーの文脈自動引き継ぎ（調査と設計）

窓口とディスパッチャーの文脈が閾値を超えたら、人の指示を待たずに引き継ぎ → `/clear` → 復帰を回す仕組みの設計。本書は調査と設計までで、実装は別タスクで行う。

## 0. 結論

- **測る**: 各役割の `Stop` フックが、自分の会話記録（`transcript_path`）の場所・セッション・ペインを `.state/context/<role>.json` に書く。トークン数は watcher が会話記録の最後の応答の `usage` から毎回読み直す。Claude Code はフックの入力に文脈の使用量を直接渡さない（§2.1）。
- **区切りを判定する**: ターンの開始（`UserPromptSubmit`）と終了（`Stop`）をフックで数え、開いているターンが無く、Claude Code が `Stop` に渡す背景タスクの一覧と state.db・判断仰ぎの register が条件を満たすときだけ「静穏」とする（§3）。
- **外から打つ**: 常駐する決定的プロセス `context-handover watcher`（LLM を含まない）が `/…-handover` → `/clear` → `/…-resume` を順に打つ。打鍵と入力欄の読み返しには既存の [`tools/self_edit_approval.py`](../../tools/self_edit_approval.py) のバックエンドを使う。対応する構成は renga と broker + tmux だけで、それ以外では自動引き継ぎを止める（§4）。
- **`/clear` の後に届いたものを拾う**: `/clear` で消えた会話記録は削除されずに残るので、引き継ぎの書き出し後に旧会話記録へ追記された入力を watcher が抜き出し、復帰のスキルに渡す（§4.4）。
- **順序**: ディスパッチャーを先に作って動かし（人の入力欄が無い）、窓口を次に足した（renga の窓口だけ。§5・§7）。ワーカーは対象外。
- **失わないことの確かめ方**: 切り替えの前後で、判断仰ぎ・稼働中ワーカーを件数でなく識別子で突き合わせ、監視ループが 1 本だけ動いていることを次の `Stop` の予約一覧と heartbeat で確かめる。故障を注入する試験の一覧を §6.3 に置く。

## 1. 再利用できる既存部品

| 部品 | 何に使うか | 出典 |
|---|---|---|
| `/secretary-handover` / `/secretary-resume` | 窓口の書き出しと復帰はそのまま使う。自動用に引数（§4.3）を足す | [`.claude/skills/secretary-handover/SKILL.md`](../../.claude/skills/secretary-handover/SKILL.md) Step 5・末尾、[`.claude/skills/secretary-resume/SKILL.md`](../../.claude/skills/secretary-resume/SKILL.md) |
| `/dispatcher-handover` / `/dispatcher-resume` | ディスパッチャーの書き出しと復帰はそのまま使う。`/clear` を外から打つ前提が既にある | [`.claude/skills/dispatcher-handover/SKILL.md`](../../.claude/skills/dispatcher-handover/SKILL.md)「重要な前提」・Step 5、[`.claude/skills/dispatcher-resume/SKILL.md`](../../.claude/skills/dispatcher-resume/SKILL.md) |
| 監視が触らないファイル一覧 | 引き継ぎ後もファイルとして残る状態の一覧（§6.1） | [`.claude/skills/dispatcher-handover/SKILL.md`](../../.claude/skills/dispatcher-handover/SKILL.md)「重要な前提」の「絶対に削除・編集しない」節 |
| 打鍵の手順（text と Enter を別送、`/clear` 後は入力欄が空になるまで 1 秒間隔で最大 15 秒） | watcher の打鍵手順にそのまま写す | [`CLAUDE.md`](../../CLAUDE.md)「窓口側の打鍵規律」 |
| `tools/self_edit_approval.py` の `RengaBackend` / `TmuxBackend` / `extract_composer` / `_norm` / `make_backend` | LLM を介さずにペインへ打鍵し、入力欄を読み返す。transport の解決と、broker で adapter が tmux かどうかの確認も同じものを使う | [`tools/self_edit_approval.py`](../../tools/self_edit_approval.py) 冒頭 docstring「Backends」、`_norm`（357 行）、`extract_composer`（401 行）、`make_backend`（683 行。tmux 以外の adapter と論理名のままの target を拒否する分岐は 705〜744 行） |
| 監視ループの heartbeat | ディスパッチャー復帰後に監視が実際に回り始めたことの確認 | [`tools/dispatcher_loop_watch.py`](../../tools/dispatcher_loop_watch.py) 冒頭 docstring（`relay-scan-heartbeat.json` の `last_scan_at`） |
| フック → 判定スクリプト、fail-open の書き方 | 本設計のフックも同じ形にする | [`.hooks/check-dispatcher-loop.sh`](../../.hooks/check-dispatcher-loop.sh) |
| 常駐プロセスの停止経路（pid・cmdline を記録した sidecar を `/org-suspend` / `/org-down` が照合して止める） | watcher の単一起動と停止に同じ形を使う | [`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md) Block C3 の 3 |
| 判断仰ぎの register | 「人の返事待ち」の判定と、引き継ぎ前後の照合 | [`tools/pending_decisions.py`](../../tools/pending_decisions.py)、段階は [`.claude/skills/org-escalation/SKILL.md`](../../.claude/skills/org-escalation/SKILL.md) Step 3〜5 |
| ワーカーへの peer 通知を CLI から送る helper | ディスパッチャーの自動引き継ぎ失敗を窓口に知らせる | [`tools/peer_notify.py`](../../tools/peer_notify.py) |

再利用しないもの: 窓口が `DISPATCHER_HANDOVER` を送る経路（発火の判断が窓口の LLM に残る）。自動経路では watcher が `/dispatcher-handover` を直接打つ。手動の経路は残す。

編集対象の注意: dispatcher-handover・dispatcher-resume・secretary-resume は生成物で、直すのは `SKILL.md.in`（[`tools/skill_src/manifest.json`](../../tools/skill_src/manifest.json) 4〜11 行・48〜49 行）。secretary-handover は生成物ではない。

## 2. 論点 1: 閾値の測り方

### 2.1 一次資料で確かめたこと

- フック共通の入力は `session_id` / `transcript_path` / `cwd` などで、文脈の使用量の欄は無い（https://code.claude.com/docs/en/hooks 「Common input fields」）。トークン数が入るのは、再開時の `SessionStart` の `context_tokens` と、`Agent` ツールの `PostToolUse` の `totalTokens`（サブエージェント分）だけ（同ページの SessionStart input と PostToolUse の Agent 節）。
- 使用率をそのまま受け取れるのは status line のコマンドで、`context_window.used_percentage` / `context_window_size` / `current_usage` が stdin の JSON で渡る（https://code.claude.com/docs/en/statusline 「Available data」）。`used_percentage` は `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` で計算され、出力トークンを含まない（同ページ「Context window fields」）。更新は応答の到着などの出来事ごとで、idle 中は止まりうるが、`refreshInterval` で一定間隔の再実行も足せる（同ページ「When the status line updates」と `refreshInterval` の説明）。
- `transcript_path` の会話記録は非同期に書かれ、直前の応答がまだ入っていないことがある（hooks ページ「Common input fields」の `transcript_path` 行）。
- `PreCompact` は自動要約の直前に `trigger`（`manual` / `auto`）と `custom_instructions` を受け、使用量は受けない。止めることはできるが、上限の手前の要約なら要約なしで会話が続き、API の上限エラーからの回復として起きた要約ならそのリクエストが失敗する（hooks ページ「PreCompact」）。`PostCompact` は `compact_summary` を受けるが結果は変えられない（同「PostCompact」）。
- 自動要約が始まる量はトークン数の auto-compact window で決まり、`autoCompactWindow`・`/autocompact`・`--autocompact`・`CLAUDE_CODE_AUTO_COMPACT_WINDOW` で変えられる。範囲は 100K〜1M トークン（https://code.claude.com/docs/en/model-config 「Set the auto-compact window」）。
- idle 中の要約（`idleCompaction`）もあり、設定で止められる（https://code.claude.com/docs/en/settings-reference の `idleCompaction` 行）。これも `PreCompact` の `auto` で発火する（hooks ページ「PreCompact」の matcher 表）。
- `SessionStart` の `source` は `startup` / `resume` / `clear` / `compact` / `fork` のいずれか（hooks ページ「SessionStart」の matcher 表）。`/clear` が終わったことはここで取れる。
- 会話記録の各応答行には `message.usage`（`input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` / `output_tokens`）が入っている。実装の最初に、ワーカーの会話記録で `type=assistant` の行に同じ欄があることを確かめた。

#### 実機で確かめたこと（実装の最初）

Stop フックを付けた Claude Code のペインで確かめた。

- `Stop` の入力に `background_tasks` と `session_crons`（`id` / `schedule` / `recurring` / `prompt`）が入る。
- **`/clear` は `/loop` の予約を消さない**。`/loop` を張って `/clear` を打つと、新しいセッションの最初の `Stop` の `session_crons` に同じ id の予約が残っていた。同じ指示文で `/loop` をもう一度打つと、予約は上書きされず 2 本になった。§3.4・§6.1 はこれを前提に書き直した。
- チャネルのメッセージで始まったターンも、`/loop` の予約が発火して始まったターンも、`UserPromptSubmit` が発火する（`prompt` にそれぞれチャネルの本文、予約の指示文が入る）。開いているターンは `UserPromptSubmit` と `Stop` で数えられる。
- `/clear` では `UserPromptSubmit` は発火しない。`/clear` の行は旧会話記録ではなく新しい会話記録に入る。旧会話記録のファイルは消えずに残る。
- renga のペインではフックの環境変数に `RENGA_PANE_ID` が入る。tmux はペイン内のプロセスに `TMUX_PANE` を渡し（`man tmux` の pane ID の節）、broker の tmux adapter は spawn 時に足した変数だけを消す（claude-org-runtime `terminal/tmux.py` の `new-session -e` と `set-environment -u` の組み）。broker + tmux のペインでの実測は、作業環境の sandbox が tmux のソケットを塞ぐためできていない。
- メッセージが会話記録に入る形は §4.4 に書いた。

### 2.2 採用: watcher が会話記録から読み直す

- フックは会話記録の場所を知らせるだけにし、トークン数は watcher が 30 秒ごとに会話記録の末尾から読み直す。フックが走った時点で直前の応答がまだ書かれていなくても、次の読み直しで拾える。`usage` の付いた応答が見つからない会話記録（直後・エラーで終わったターン）は「不明」として扱い、切り替えない。
- 式は status line と同じ入力 3 種の和。閾値はトークン数で役割の settings に書く（auto-compact window もトークン数なので）。既定は **window の 60%**（window 1M なら 600K）。
- 保証の範囲はターンの区切りで閾値を超えていれば引き継ぐところまで。1 ターンの中で閾値から自動要約の位置まで一気に進んだ場合は引き継ぎが間に合わない。そのときは `PreCompact` フックが要約を**止めずに** `context_compacted_before_handover` を events に 1 行書く。止めると上限エラーからの回復のときにリクエストが失敗するため（§2.1）。この行は閾値を下げる合図として読む。
- 窓口とディスパッチャーの settings では `idleCompaction` を止める。idle 中の要約が引き継ぎより先に走らないようにするため。

却下した案:
- **status line で測る**: 使用率を直接もらえる経路で、`refreshInterval` を付ければ idle 中も更新できる。ただし status line は settings に 1 枠しか無く、オペレーターが自分の status line を使っていると、包んで呼び直す仕組みが要る。会話記録を読む方が触る場所が少ない。会話記録の `usage` が取れないと分かった場合の代わりとして残す。
- **会話記録のファイルサイズから見積もる**: ツール出力の全文などが入り、送信される文脈と比例しない。

## 3. 論点 2: 区切りのよいときだけ切り替える

### 3.1 フックが書く記録

各役割に 4 つのフックを置き、`.state/context/<role>.json` を書き換える（一時ファイルに書いてから置き換える）。

| フック | 書く内容 |
|---|---|
| `SessionStart` | `session_id`・`transcript_path`・ペイン id（`RENGA_PANE_ID` または tmux の `TMUX_PANE`）・`source`・閾値。`source=clear` のときは `cleared_at` も書く。前のセッションが開いたまま残したターンは閉じる（`turn_open = turn_closed`） |
| `UserPromptSubmit` | `turn_open = turn_closed + 1`、`prompt_at` |
| `Stop` | `turn_closed += 1` の後 `turn_open = turn_closed`、`background_tasks`・`session_crons`（Claude Code が Stop に渡す。hooks ページ「Stop input」） |
| `PreCompact` | §2.2 の 1 行 |
| `Notification`（窓口だけ。matcher `idle_prompt`） | `idle_at`・`idle_session_id`（§4.2 の 1） |

- `turn_open` を `UserPromptSubmit` の回数でなく `turn_closed` に結び付けるのは、`UserPromptSubmit` を伴わずに始まったターンや、ターンの途中に吸収された入力で、2 つの数が恒久的にずれないようにするため。開いているターンがあるかどうかだけが要る。
- 閾値は記録フックが環境変数から決める。`CLAUDE_ORG_HANDOVER_THRESHOLD_TOKENS` があればその値、無ければ `CLAUDE_CODE_AUTO_COMPACT_WINDOW` の 60%。どちらも無ければ window の大きさを仮定せず `threshold_tokens` を記録しない（ディスパッチャーの雛形は 600000 = window 1M の 60% を `env` に置く）。閾値が無い記録を見た watcher は引き継ぎをせず、理由 `threshold_unset` を events（`auto_handover_skipped`）に 1 行書き、窓口へ 1 回だけ知らせる（同じセッションでは再通知しない）。役割の settings の `env` に書けばフックに渡る。
- 実装は [`tools/context_record.py`](../../tools/context_record.py)（フックは [`.hooks/context-record.sh`](../../.hooks/context-record.sh)）。

- 役割はフックの `cwd` で決める（`.dispatcher/` 配下ならディスパッチャー、org 直下なら窓口、それ以外は記録しない）。リポジトリ直下の `.claude/settings.local.json` はディスパッチャーのセッションにも読まれる（[`.claude/skills/org-setup/SKILL.md`](../../.claude/skills/org-setup/SKILL.md) 30〜32 行）ので、役割で書き分けないと記録が混ざる。`.curator/` で起動するキュレーターも同じ settings を読むので、org 直下以外を窓口とみなすと、キュレーターの記録で窓口の記録が上書きされ、watcher がキュレーターのペインへ打鍵しうる。同じコマンドのフックが複数の settings にあっても 1 回だけ走る（https://code.claude.com/docs/en/hooks 「Hook handler fields」）。`cwd` はセッションのシェルに付いて動くので、記録のセッションと違う `session_id` の出来事は捨て、`source=clear` の `SessionStart` は記録済みのペインからのものだけ受ける（窓口のシェルが `.dispatcher/` に移っていても、ディスパッチャーの記録を書き換えない）。`idle_prompt` は `UserPromptSubmit` と同時に走りうるので、別ファイル（`<role>.idle.json`）に書く。`source=compact` の `SessionStart` は同じセッションのものだけ受ける。それでも org 直下で起動した別の Claude（人の作業用ペインなど）は記録を奪えるので、renga では watcher が打つ前に毎回 `renga inspect` の `pane.name` が役割名（`secretary` / `dispatcher`）であることを確かめ、違えば打たない。`UserPromptSubmit` の本文が watcher の復帰の行なら、その `op` とターン番号も記録する（§4.5）。
- フックの登録は他のフックと同じく [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) の `roles.<role>.required_hooks` に足す。
- watcher は、`session_id` とペイン id が最後の `SessionStart` と一致しない記録、`turn_open != turn_closed` の記録、10 分以上前の `Stop` しか無い記録を「不明」として扱い、切り替えない。フックが失敗して更新が止まった場合も、古い `quiet` が残って切り替わることはない。
- ペイン id を環境変数から取れることは実装の最初に確かめた（§2.1 の「実機で確かめたこと」）。

### 3.2 共通の条件

- 開いているターンが無い（`turn_open == turn_closed`）。ワーカーへの返事の途中・コマンドの最中は、ここで外れる。
- `background_tasks` が空。ただし窓口では、broker のときに `/org-start` が背景で常駐させる `secretary_queue_watcher.py`（[`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md) Block C3）が平常時もここに載るので、この 1 本だけはコマンド名で除く。`/clear` の後にこれが生き残るかは Claude Code の資料に書かれていない。`/secretary-resume` に「sidecar `.state/secretary_queue_watcher.json` の pid が生きていなければ起動し直す」手順を足し、生き残るかどうかに依らず 1 本になるようにする。
- 自動要約・`/clear` の直後で、`usage` の付いた応答がまだ無い場合は「不明」（§2.2）。

### 3.3 窓口の条件

- 判断仰ぎの register に `resolved` でないエントリが無い。人へ伝えて返事待ちのものも、返事を受けてワーカーへの転送前のもの（`mark-user-replied` 済みで `resolve --kind to_worker` 前）も含む。後者は返事の中身が register に無く会話の中にしか無いので、ここで切り替えると返事が失われる（register の欄は [`tools/pending_decisions.py`](../../tools/pending_decisions.py) 77〜91 行）。
- 人への承認待ち（`worker_completed`・`ci_green_merge_gate`・`ci_unconfirmed_head_gate` など）は register に載らない。これは `notify_sent kind=awaiting_user` の最後の 1 行から 30 分経つまで切り替えない、で代える。人がすぐ返事をするときに質問の文脈を消さないためで、30 分を過ぎた未回答の質問は引き継ぎファイルに書かれ、復帰後のブリーフィングで出し直される。`gate=escalation_reply_forward` は返事を受けた後に出す 1 行（[`.claude/skills/org-escalation/SKILL.md`](../../.claude/skills/org-escalation/SKILL.md) Step 4.5）なので、待ちには数えない。
- `session_crons` が空。`/clear` は予約を消さない（§2.1）が、窓口の `/loop` は会話の文脈を前提にした指示文でありうるので、ある間は切り替えない（§7）。
- 実装（[`tools/context_handover_watch.py`](../../tools/context_handover_watch.py) の `secretary_hold`）: register は `status` が `resolved` 以外の行を全部数える。`notify_sent kind=awaiting_user` は `gate` が `escalation_reply_forward` と watcher 自身の失敗通知（`auto_handover_failed`）を除いて数える。引き継ぎのターンの間に新しく判断仰ぎ・承認待ちが出たときは、`/clear` の直前にもう一度確かめて止める（質問が会話にしか無いため）。

### 3.4 ディスパッチャーの条件

- `runs.status = 'queued'` の行が無い（派遣の途中ではない。[`tools/state_db/queries.py`](../../tools/state_db/queries.py) 40 行 `ACTIVE_RESERVATION_STATUSES`）。
- `session_crons` に監視ループが入っているのは平常なので条件にしない。`/clear` は予約を消さないので、監視ループは `/clear` を越えてそのまま残る（§2.1 の「実機で確かめたこと」）。`/dispatcher-resume` は `CronList` で監視ループの予約を数え、0 本のときだけ `/loop 3m` を張り、2 本以上なら 1 本に減らす（[`.claude/skills/dispatcher-resume/SKILL.md`](../../.claude/skills/dispatcher-resume/SKILL.md) Step 5 の 2）。復帰後に 1 本だけになっているかを §6.1 で確かめる。
- 振り返りゲート（`dispatcher_retro_gate.py`）の ack 待ちは 1 ターンの中で回る（呼び出し形は [`tools/dispatcher_retro_gate.py`](../../tools/dispatcher_retro_gate.py) 冒頭 docstring）ので、開いているターンが無ければ終わっている。

## 4. 論点 3: 誰が `/clear` を打つか

### 4.1 採用: 常駐する決定的プロセス（context-handover watcher）

[`tools/context_handover_watch.py`](../../tools/context_handover_watch.py) を 1 本作り、`/org-start` が Block D（ディスパッチャーの合流）の後にディスパッチャーペインを分割した専用ペインで起動する（LLM を含まない。[`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md) Block E）。

- 30 秒ごとに `.state/context/*.json` と会話記録を読み、閾値超過かつ §3 の条件を満たす役割があれば §4.3 を回す。同時に回すのは 1 役割だけ。
- 単一起動と停止は `secretary_queue_watcher.py` と同じく、pid・cmdline を書いた sidecar を `/org-suspend` / `/org-down` が照合して止める。`/org-resume` は起動し直す。
- 進行中の手順は `.state/context/handover-op.json`（操作 id・役割・段・旧 `session_id`・旧会話記録の位置・開始時刻）に段ごとに書く。watcher が途中で落ちても、起動し直したときにここから続きを判断する（§4.5）。
- 打鍵の構成は `make_backend` と同じ判定で決める。renga と broker + tmux 以外（wezterm・herdr など）では起動時に「自動引き継ぎ無効」と 1 行出して止まる。broker + tmux では論理名でなく tmux のペイン id が要るので、§3.1 で記録したペイン id を使う。

却下した案:
- **窓口とディスパッチャーが互いに打つ**: どちらも LLM で、発火を書面の手順に頼る形が残る。Issue が直したい形そのもの。
- **`Stop` フックが自分のペインへ遅延打鍵するプロセスを切り離して起動する**: 常駐ペインは要らないが、失敗の記録・再試行の間隔・二重起動の防止を持つ場所が無い。

### 4.2 人の入力に割り込まない（窓口ペイン）

次を全部満たしたときだけ打つ。

1. 窓口の `Notification` フック（matcher `idle_prompt`）の記録が最後の `Stop` より後にある。`idle_prompt` は応答の約 60 秒後、人が打鍵しておらず背景のエージェントも走っていないときに出る（hooks ページ Notification の matcher 表と timing の注記）。
2. 打つ直前に読んだ入力欄が空。入力欄が読み取れない・判別できないときは「不明」で、打たない。
3. 自分の文字列を打った直後に読み返した入力欄が、`_norm` で正規化して自分の文字列と一致する（プロンプト記号の後の空白や NBSP の置き換えは `_norm` が吸収する。`tools/self_edit_approval.py` 357〜366 行）。一致しなければ Enter を送らず、消しもせず、`auto_handover_failed` で止まる。人の文字と混ざった入力欄を機械が直すと人の文字を消しうるので、直すのは人に任せる。

ディスパッチャーのペインは人が打たない前提なので 2 と 3 だけを使う。

1 は引き継ぎを始めるときだけ見る。始める前に入力欄が空でなければ、失敗にせず次の周期まで待つ（人が戻ってきただけなので知らせない）。`/clear` と復帰の打鍵の前は 2 と 3 だけで、人が入力欄に文字を残していれば打たずに止まる。引き継ぎのターンの間に人が入力を送っていた（旧会話記録の `p0` 以降に、チャネル・背景タスク完了などの機械の入力の形でない入力がある。画像や貼り付けも人の入力に数える）ときも `/clear` を打たずに止まる。人がいま読んでいる返事を消し、その指示を新しいセッションが拾い直してもう一度実行してしまうため。`idle_prompt` は端末の前に人がいないように見えるときだけ出る（hooks ページ Notification の timing の注記）ので、人が画面の前にいる間は窓口は切り替わらない。また `Stop` が 10 分より古い記録は「不明」（§3.1）なので、窓口が切り替わるのは応答の約 1 分後から 10 分後までの間になる。

残るもの: 3 の読み返しと Enter の間（数百ミリ秒）に人が打った文字は、自分の文字列と一緒に送られる。打鍵の排他は端末側に無いので、この隙間は消せない。自動引き継ぎは人が離れている時間のためのもので、人が画面の前にいるなら 1 の条件でほぼ外れる。

### 4.3 打鍵の手順（1 回分）

`R` を役割（`secretary` / `dispatcher`）、`OP` を操作 id とする。

1. 開始時の `turn_closed` を `s0`、旧会話記録の末尾のバイト位置を `p0` として `handover-op.json` に書く。
2. `/R-handover auto op=OP` を打つ（text → 読み返し → Enter を別送）。`auto` のときスキルは人への案内文（secretary-handover Step 5）と打鍵の依頼（dispatcher-handover Step 5）を出さず、`op=OP` 付きの event を書く。ディスパッチャーは窓口に「自動引き継ぎ中、打鍵は watcher が行う」とだけ送る（窓口が `/clear` を打ちに来ないように）。
3. 引き継ぎファイルの更新時刻が開始より新しく、`turn_closed >= s0 + 1` かつ `turn_open == turn_closed` になるまで待つ（最大 10 分）。`==` でなく `>=` にするのは、引き継ぎのターンの後に監視ループの予約が別のターンを回すことがあるため。そのターンに届いた入力は §4.4 で拾う。
4. §6.2 の検査を通す。通らなければ止める。
5. 打つ直前に、開いているターンが無いことをもう一度確かめてから `/clear` を打つ。完了は入力欄が空になることではなく、`.state/context/R.json` に `source=clear` の `SessionStart` が新しい `session_id` で書かれたことで判定する（最大 15 秒）。
6. §4.4 で `p0` 以降の旧会話記録から入力を抜き出す。
7. `/R-resume auto op=OP` を打つ。完了は `op=OP` 付きの `*_resumed` event で判定する（最大 60 秒。来なければ 1 回だけ打ち直す）。ディスパッチャーは、監視対象がいる（`runs.status` が `in_use` / `review` の行か `curate-inflight.json` がある）ときに限り、監視ループの heartbeat（`last_scan_at`）が `/clear` より後に更新され、復帰後の `Stop` の `session_crons` に監視ループがちょうど 1 本あることを最大 9 分待つ（3 サイクル。`dispatcher_loop_watch.py` の既定と同じ）。監視ループは `/clear` を越えて残っていることが多く、そのときは復帰のスキルが張り直さないので、待つのは残った予約の次の発火になる。監視対象が無ければ `DISPATCHER_RESUMED_IDLE` 相当の完了で成功とする。
8. §6.1 の突き合わせをする。

どの段で止まっても `auto_handover_failed`（`OP`・役割・段・理由）を events に書き、その役割は 30 分試さない。知らせる先は次のとおり。events に書くだけでは知らせたことにしない。

- ディスパッチャーの失敗: `peer_notify` で窓口に送る。窓口が人に伝える。
- 窓口の失敗: watcher のログに書き、窓口にも `peer_notify` で送り（`/clear` 済みなら新しいセッションが受ける）、`notify_sent kind=awaiting_user gate=auto_handover_failed` を書く。後者が人に届くのは attention watcher が動いているときだけ（attention watcher は自動起動しない。[`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md) Block C3 の後の Sidebar）。無人で回すときは attention watcher を起動しておくことを、窓口の自動引き継ぎを有効にする条件にする。watcher は `.state/attention_pane.json` の有無でなく、記録されたペインが renga でいまも `attention` という名前で在ることで確かめる（ペインが落ちても sidecar は残るため）。

### 4.4 `/clear` の前後に届いた入力を拾う

broker の「配送済み」はハーネスが通知を受け付けたという意味で、モデルに見えたことまでは保証しない（[`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) Surface 8 の配送状態の表と、その下の `DELIVERED` の説明）。配送済みのメッセージは再配送されないので、引き継ぎの書き出しの後・`/clear` の前に届いたメッセージは、キューにも新しい会話にも残らない。

そこで watcher は、`/clear` の完了（§4.3 の 5）を確かめた後に、旧会話記録の `p0`（引き継ぎのターンを始める前）以降からユーザー側の行（チャネルのメッセージ、`/loop` の反復、人の入力）を、自分が打った `/R-handover` と `/clear` を除いて抜き出し、`.state/context/R-late-input-OP.md`（操作 id ごとのファイル）に書く。`/R-resume auto op=OP` は同じ `OP` のファイルだけを読み、復帰のブリーフィングの後に処理し、読み終えたら `.consumed.md` に rename する。抜き出す入力が無ければファイルは作らない。前の操作のファイルを次の操作で読み直すことはない。

- 起点を引き継ぎのターンの後でなく `p0` にするのは、引き継ぎのターンの途中に届いて引き継ぎファイルに入らなかった入力も拾うため。引き継ぎファイルに既に入っている入力も重ねて渡すことになるが、欠落より重複を許す側に倒す。broker の配送も「重複表示は許容、欠落は致命」の方針で作られている（上記 Surface 8 の配送状態の説明）。復帰のスキルは、引き継ぎファイルに処理済みと書かれた入力を飛ばしてよい。

- 旧会話記録は非同期に書かれる（§2.1）ので、`/clear` の完了から 5 秒待ってから読む。それでも書かれていない分は拾えない。
- 届いたメッセージが会話記録に入る形は実装の最初に確かめた。届いた時点で必ず `type=queue-operation`・`operation=enqueue` の行が本文付きで書かれ、そのうえで次のどちらかになる。
  - そのメッセージでターンが始まったとき: `type=user` の行。チャネルの push は `origin.kind=channel`（`isMeta=true`）、人の入力は `origin.kind=human`。
  - 動いているターンの途中に届いたとき: `type=user` の行にはならず、`type=attachment`・`attachment.type=queued_command` の行に本文が入る（`enqueue` の後に `operation=remove`・`reason=absorbed_mid_turn` の行が続く）。

  watcher はこの 3 種（`enqueue`・`origin` の付いた `user`・`queued_command`）を全部読み、本文が同じものは 1 つにまとめ、届いた順に並べる。`origin` の無い `user` 行（ツール結果・スキル本文の展開）は入力ではないので読まない。自分が打った `/R-handover auto op=OP` は除く。`/clear` の行は新しい会話記録に入るので、旧会話記録には出てこない。`check_messages` のツール結果は、受け取ったターンの中でモデルが既に読んでいるので拾わない。実装は [`tools/context_handover_watch.py`](../../tools/context_handover_watch.py) の `extract_late_inputs`。

### 4.5 再送と再起動

- watcher は復帰の行を、新しいセッションがまだその行でターンを始めていないとき（記録の復帰の `op` が一致しない。チャネルのメッセージで始まったターンは数えない）だけ打ち直す。始まっていれば最大 10 分そのターンを待ち、完了の event が無いまま閉じたら失敗にする（窓口の復帰はブリーフィングが長く、60 秒で打ち直すと動いているペインに 2 本目を積むため）。窓口の復帰のスキルは完了の event をブリーフィングの直後、拾い直した入力の処理より前に書き、拾い直しのファイルは読む前に rename する。`/clear` 済みの段で失敗したときの案内は `/R-resume auto op=OP` にする（`op` が無いと拾い直しのファイルが読まれない）。
- 復帰のスキルは `op=OP` が同じなら 2 回目以降は初期化をやり直さず、同じ完了の event を書き直すだけにする。ディスパッチャーの `already_consumed` の分岐は何も送らない作りだったので、`auto op=OP` のときは完了の event を書くように足した（[`.claude/skills/dispatcher-resume/SKILL.md`](../../.claude/skills/dispatcher-resume/SKILL.md)「自動引き継ぎ」節）。
- watcher は `/clear` を打つ直前に段を `clearing` と書く。再起動したときは、段が `clearing` 以前なら、`.state/context/R.json` の現在の `session_id` を `handover-op.json` の旧 `session_id` と比べる。同じなら `/clear` はまだ効いていないので最初からやり直す。違っていて `source=clear` なら `/clear` は効いているので §4.3 の 6 から続ける（空のセッションで引き継ぎをやり直して、復帰に要る引き継ぎファイルを上書きしないため）。どちらとも判断できなければ失敗として知らせる（人が `/R-resume` を打てば戻れる状態になっている）。
- `/clear` が効いた後の段（入力の拾い直し・復帰・突き合わせ）から続けるのは、現在の `session_id` とペイン id が `/clear` の直後に記録したものと同じときだけにする。`/org-suspend` → `/org-start` を挟むと `handover-op.json` は残ったままペインが作り直され、記録した古いペイン id が別のペインに使い回されていることがあるため。違えば打たずに失敗として知らせる。

## 5. 論点 4: 順序

1. ディスパッチャー（人の入力欄が無く、`/clear` を外から打つ経路が既に動いている）。
2. 窓口（§4.2 の割り込み防止、§3.3 の返事待ちの判定、broker での identity 確認（§7）が要る）。
3. ワーカーは対象外。1 タスクで閉じるので、文脈が尽きる前にペインごと終わる。

## 6. 論点 5: 引き継ぎで失うものが無いかの確かめ方

### 6.1 切り替えの前後の突き合わせ（watcher が毎回やる）

`/clear` の直前と復帰の後に同じものを取り、識別子で比べる。消えたものに対応する event が無ければ `auto_handover_loss_suspected` を §4.3 の知らせ方で出す。

| 項目 | 識別子 | 残る理由 |
|---|---|---|
| 判断仰ぎ | register の `(task_id, received_at)` と段階 | ファイル。窓口は `resolved` 以外があると切り替えない（§3.3） |
| 稼働中ワーカー | `runs.task_id` と `status`（`queued` / `in_use` / `review`） | state.db |
| 監視ループ（ディスパッチャー） | 復帰後の最初の `Stop` の `session_crons` のうち監視ループの指示文のもの、と heartbeat | 監視対象がいるときは 1 本だけで、heartbeat が `/clear` より後に動いていること（0 本なら止まっている、2 本なら重複）。`/clear` は予約を消さないので、復帰のスキルが `CronList` で数えずに `/loop` を張ると 2 本になる（§3.4）。監視対象が無いときは確かめない（残った予約は次のサイクルで監視対象なしを見て自分で止まる。worker-monitoring Step 7）。`.consumed.md` の分岐は `/dispatcher-resume` が走ったときにしか効かず、監視ループ自体はこのスキルを呼ばないので、ここで数える |
| 窓口の滞留 watcher（broker） | sidecar の pid | 復帰後に生きていること（§3.2） |
| `/clear` の前後に届いた入力 | 旧会話記録の `p0` 以降の入力（操作 id ごとのファイル） | §4.4 |
| ディスパッチャーの cursor・idle streak・curate 記録 | dispatcher-handover が触らないとするファイル | ファイル |

### 6.2 引き継ぎファイルの中身の検査（`/clear` の前に 1 回）

引き継ぎファイルは LLM が書くので、写すべきものを写したかを書かれた後で確かめる。`review` / `in_use` の全ワーカーの task_id と、§3.3 の 30 分を過ぎた未回答の `awaiting_user` の task_id が、本文に出てくることを確かめる。欠けていれば `/clear` を打たない。

「未回答」かどうかは会話の中にしか無いので、窓口では直近 24 時間の `awaiting_user`（§3.3 と同じ除外）の task_id を全部求め、回答済みのものも「回答済み」と書かせる。書く側と確かめる側が同じ一覧を使うよう、`/secretary-handover` は `python3 tools/context_handover_watch.py --must-name secretary` で一覧を取る。24 時間より前の未回答の質問は、前の引き継ぎファイルから写されることに頼る。

閉じたワーカーの run が state.db に残っていても、この検査は緩めない。代わりに引き継ぎを書く側（`/dispatcher-handover`）が、`in_use` / `review` の run のうち監視対象に入れなかったものを `list_panes` で確かめ、`## 監視外の run` に「監視外: <task_id>（ペインなし）」と 1 件ずつ書く。task_id が本文に載るので検査は通り、生きているワーカーが欠けたままなら今までどおり止まる。復帰側（`/dispatcher-resume`）は「監視外」の task_id をペインで引き直し、在れば監視対象に戻す。ペインなしの判定を書き手が誤っても、監視から落ちたままにならない。

この検査で分かるのは識別子が載っていることまでで、会話でしか決まっていない合意（「B 案で進める」など）が正しく写されたかは分からない。そこは今の手動の引き継ぎと同じく、スキルの書き出し手順に頼る。

### 6.3 実装時の試験

閾値を小さくして、少なくとも次を 1 回ずつ通す。renga と broker + tmux の両方で回す。

| 状況 | 期待 |
|---|---|
| 判断仰ぎが人の返事待ち／返事を受けて転送前 | 切り替わらない |
| 承認待ちを出した直後（30 分以内） | 切り替わらない |
| 引き継ぎの書き出しの後、`/clear` の前に窓口宛のメッセージが届く | `R-late-input-OP.md` に入り、復帰後に処理されて ack が返る |
| `/clear` の後、復帰の前にメッセージが届く | 同上 |
| 窓口の入力欄に人が文字を残している | 打たない |
| watcher が自分の文字列を打った後に入力欄が一致しない | Enter を送らず止まり、知らせる |
| 引き継ぎの直後で会話記録に応答がまだ無い | 「不明」で切り替えない |
| 復帰が 60 秒以内に終わらない → 再送 | 初期化は 1 回だけ、完了の event が出る |
| watcher を `/clear` の前と後でそれぞれ止めて起動し直す | §4.5 のとおり続くか、失敗として知らせる |
| ディスパッチャーの監視ループが `/clear` の後も残っている | 復帰後の監視ループは 1 本 |
| 監視対象が無いディスパッチャーを引き継ぐ | 監視ループ 0 本のまま成功になる |
| broker で wezterm adapter | watcher は起動時に無効と出して止まる |

## 7. 残り（本設計では扱わない）

- 窓口に `/loop` があると切り替えない（§3.3）。窓口の段では条件に残した。窓口の `/loop` は用途が決まっておらず、指示文が会話の文脈を前提にしていると `/clear` の後に意味が変わるため。窓口で `/loop` を常用するようになったら外すかを決め直す。
- §4.2 の読み返しと Enter の間の隙間は消せない。
- broker では窓口の `/secretary-resume` が自分のペイン id を確定できず、人に続行の判断を仰ぐ（[`.claude/skills/secretary-resume/SKILL.md`](../../.claude/skills/secretary-resume/SKILL.md) Step 0 の 2）。§3.1 でフックが環境変数から取ったペイン id を `auto` の復帰で使ってよいかは、契約（[`docs/contracts/backend-interface-contract.md`](../../docs/contracts/backend-interface-contract.md) T-§4.2）の解釈の問題なので、窓口の自動引き継ぎを作る段で決める。決まるまで broker の窓口は自動引き継ぎの対象にしない。窓口の段でもこの解釈は決めず、watcher は transport が renga のときだけ窓口を扱う（broker では窓口を飛ばしてディスパッチャーだけを見る）。
- 1 ターンの中で閾値から自動要約まで進む場合（§2.2）は引き継ぎが間に合わない。
- 会話でしか決まっていない合意の写し漏れは機械では検出できない（§6.2）。

## 8. 独立レビューの指摘と扱い

Codex の設計レビュー（`codex_review_gate.sh --design`、1 回）で Blocker 3・Major 10・Minor 2 が出た。全件を根拠の行まで確かめ、次のように扱った。

| # | 重さ | 指摘 | 扱い |
|---|---|---|---|
| 1 | Blocker | 引き継ぎの `Stop` の後にチャネルや予約で新しいターンが始まると、引き継ぎに入っていない仕事ごと `/clear` で消える。入力欄が空でも `/clear` の完了にならない | 採用。ターンの開始も数えて開いているターンが無いことを `/clear` の直前に確かめ（§3.1・§4.3 の 5）、完了は `SessionStart source=clear` で判定する。消えた分は旧会話記録から拾う（§4.4）。配送を止める仕組みを足す案は、broker に止める手段が無く大きいので採らない |
| 2 | Blocker | 窓口の入力欄で人の文字を送ってしまう・Backspace で人の文字を消しうる | 一部採用。不一致のときに消す動作はやめ、Enter を送らずに止める（§4.2 の 3）。読み返しと Enter の間の隙間は端末側に排他が無く消せないので、残りとして書いた（§4.2・§7）。窓口の無人打鍵を全部やめる案は、Issue の目的（人が張り付かない時間を長くする）と逆なので採らない |
| 3 | Blocker | broker の配送済みは再配送されず、書き出しと `/clear` の間に届いたメッセージが失われる | 採用。§4.4 で旧会話記録から拾う。アプリ側に受信箱を作って処理の ack まで持つ案は、Issue の範囲を超えて broker の契約を変えるので採らない |
| 4 | Major | broker の窓口は `secretary_queue_watcher.py` が常に背景にいて、背景タスクが空にならない | 採用。この 1 本を除き、復帰で生存を確かめて起動し直す（§3.2・§6.1） |
| 5 | Major | 入力欄の完全一致は表示の空白などで外れる | 採用。`_norm` で比べ、読めないときは「不明」（§4.2） |
| 6 | Major | `Stop` で 1 回読むだけだと会話記録の遅れで閾値を取り逃し続ける。1 ターン内の進行も拾えない | 採用。watcher が毎回読み直す。1 ターン内の進行は保証の範囲外と明記した（§2.2） |
| 7 | Major | `awaiting_user` の最後の 1 行を「待ち」とみなすと、返事の後に出る `escalation_reply_forward` も待ちに数える。人の別の入力で全部解けたことにもならない | 採用。判断仰ぎは register の段階で判定し、`escalation_reply_forward` は数えない。register に載らない承認待ちは「30 分経つまで切り替えない」にした（§3.3）。ゲートごとに開閉を記録する仕組みは、30 分の待ちと引き継ぎファイルの出し直しで足りるので作らない |
| 8 | Major | task_id があるだけ・件数が同じだけでは失っていないことにならない。返事を受けて転送前の判断仰ぎは返事が会話にしか無い | 採用。`resolved` 以外の判断仰ぎがあれば切り替えない（§3.3）、前後は識別子と段階で比べる（§6.1）。会話でしか決まっていない合意は機械で確かめられないと明記した（§6.2・§7） |
| 9 | Major | 打鍵が使える構成と、窓口のペイン id、broker での窓口の identity 確認が決まっていない | 採用。renga と broker + tmux に限り（§4.1）、ペイン id はフックが環境変数から取る（§3.1）。broker の窓口の identity は契約の解釈が要るので、決まるまで対象外にした（§7） |
| 10 | Major | 復帰の再送が冪等でなく、ディスパッチャーの `already_consumed` の分岐は完了を返さない | 採用。操作 id を付けて同じ id の再送は完了の event を書き直すだけにする（§4.5） |
| 11 | Major | `.consumed.md` は監視ループの重複を防がない | 採用。復帰後の `session_crons` と heartbeat で 1 本だけ動いていることを確かめる（§6.1） |
| 12 | Major | フックの記録にセッション・ペインの紐付けが無く、古い `quiet` が残りうる。リポジトリ直下の settings がディスパッチャーにも読まれる | 採用。§3.1 の記録の形と「不明」の扱い、役割の書き分け、`required_hooks` への登録 |
| 13 | Major | watcher の単一起動・停止・再起動と、失敗を人に届ける経路が無い | 採用。§4.1 の sidecar と §4.3 の知らせ方。窓口の失敗が人に届くのは attention watcher があるときだけなので、それを窓口の自動引き継ぎを有効にする条件にした |
| 14 | Minor | 生成物のスキルは `.in` が編集対象 | 採用（§1 の末尾） |
| 15 | Minor | status line を却下した理由に `refreshInterval` が抜けている | 採用。理由を「settings の 1 枠を取り合う」に絞り、代わりの案として残した（§2.2） |

その後、差分の Codex セルフレビュー（`codex_review_gate.sh origin/main`）で次の 2 件が出た。どちらも採用した。

| 重さ | 指摘 | 扱い |
|---|---|---|
| P1 | 引き継ぎのターンの途中に届いた入力は、拾い直しの起点（ターンの後）より前にあり、引き継ぎファイルにも入らないことがある | 起点をターンの前（`p0`）にし、重複は許す（§4.4） |
| P2 | `/clear` を打った後、段を書く前に watcher が落ちると、再起動で空のセッションに引き継ぎをやり直し、引き継ぎファイルを上書きする | `/clear` の直前に `clearing` と書き、再起動時は `session_id` を比べて `/clear` が効いたかを判断する（§4.5） |

2 回目の差分レビューで P1 は出ず、次の P2 が 2 件出た。どちらも採用した。

| 重さ | 指摘 | 扱い |
|---|---|---|
| P2 | 拾い直した入力のファイルが 1 本だと、次の操作で前の操作の入力を読み直す | 操作 id ごとのファイルにし、読み終えたら rename する（§4.4） |
| P2 | 監視対象が無いディスパッチャーは監視ループを張らない（`DISPATCHER_RESUMED_IDLE`）ので、heartbeat を無条件に待つと正常な復帰を失敗にする | heartbeat と監視ループ 1 本の確認を、監視対象がいるときに限る（§4.3 の 7・§6.1） |

ディスパッチャー段の実装の差分レビューで次の 2 件が出た。

| 重さ | 指摘 | 扱い |
|---|---|---|
| P1 | チャネルのメッセージや予約で始まったターンは `UserPromptSubmit` を伴わず、開いているターンを見落として `/clear` を打ちうる | 実機で確かめ、前提が違った。どちらのターンでも `UserPromptSubmit` は発火する（§2.1 の「実機で確かめたこと」）。記録の形はそのまま |
| P2 | `/org-suspend` → `/org-start` を挟むと、`/clear` 後の段から続けるときに古いペイン id へ打鍵しうる | 採用。`/clear` 直後の `session_id` とペイン id を記録し、一致するときだけ続ける（§4.5） |
| P2（2 回目） | 突き合わせの 9 分の間に最後のワーカーが終わると、監視ループが正しく止まったのに失敗として知らせる | 採用。待つ間も監視対象の有無を毎回見直し、無くなったら成功とする（§4.3 の 7） |
