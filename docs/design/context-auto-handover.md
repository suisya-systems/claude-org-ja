# 窓口・ディスパッチャーの文脈自動引き継ぎ（調査と設計）

窓口とディスパッチャーの文脈が閾値を超えたら、人の指示を待たずに引き継ぎ → `/clear` → 復帰を回す仕組みの設計。本書は調査と設計までで、実装は別タスクで行う。

## 0. 結論

- **測る**: 各役割の `Stop` フックが、会話記録（`transcript_path`）の最後の応答の `usage` から入力トークン数を計算し、`.state/context/<role>.json` に書く。Claude Code がフックの入力に文脈の使用量を直接渡すことはない（§2.1）。
- **区切りを判定する**: 同じ `Stop` フックが、ターン終了時点の `background_tasks` / `session_crons`（Claude Code が Stop フックに渡す）と state.db・判断仰ぎの register から「静穏かどうか」を機械判定し、同じファイルに書く。
- **外から打つ**: 常駐する決定的プロセス `context-handover watcher`（LLM を含まない）が、閾値超過かつ静穏の役割に対して `/…-handover` → `/clear` → `/…-resume` を順に打鍵する。打鍵と入力欄の読み返しには既存の [`tools/self_edit_approval.py`](../../tools/self_edit_approval.py) の renga / tmux バックエンドを使う。人の入力欄（窓口ペイン）には、入力欄が空で、人が 60 秒以上打鍵していないときだけ打つ。
- **順序**: ディスパッチャーを先に実装・稼働させる（人の入力欄が無い）。窓口は次段。ワーカーは対象外。
- **失わないことの確かめ方**: 切り替え前後で、register と state.db から機械的に取れる件数（判断仰ぎ・稼働中ワーカー・未配送メッセージ）を watcher が突き合わせる。加えて、引き継ぎファイルに register の全 task_id が載っているかを `/clear` の前に検査し、載っていなければ `/clear` を打たない。

## 1. 再利用できる既存部品

| 部品 | 何に使うか | 出典 |
|---|---|---|
| `/secretary-handover` / `/secretary-resume` | 窓口の書き出しと復帰はそのまま使う。自動起動用に「人への案内文を出さない」引数を足すだけ | [`.claude/skills/secretary-handover/SKILL.md`](../../.claude/skills/secretary-handover/SKILL.md) Step 5（案内文）/ 末尾「`/clear` を自分で打とうとしない」 |
| `/dispatcher-handover` / `/dispatcher-resume` | ディスパッチャーの書き出しと復帰はそのまま使う。`/clear` を外から打つ前提が既にある | [`.claude/skills/dispatcher-handover/SKILL.md`](../../.claude/skills/dispatcher-handover/SKILL.md)「重要な前提」・Step 5 |
| 二重起動ガード（`.consumed.md`） | resume の再送・残った `/loop` 予約による再発火を吸収する。watcher のタイムアウト時の再送もここに落ちる | [`.claude/skills/dispatcher-resume/SKILL.md`](../../.claude/skills/dispatcher-resume/SKILL.md) 「Step 0 の前」（63〜127 行） |
| 監視が触らないファイル一覧 | 引き継ぎ後も残る状態の一覧として §6 の検証に使う | [`.claude/skills/dispatcher-handover/SKILL.md`](../../.claude/skills/dispatcher-handover/SKILL.md)「重要な前提」の「絶対に削除・編集しない」節 |
| 打鍵の手順（text と Enter を別送、`/clear` 後は入力欄が空になるまで 1 秒間隔で最大 15 秒） | watcher の打鍵手順をそのまま写す | [`CLAUDE.md`](../../CLAUDE.md)「窓口側の打鍵規律」 |
| `tools/self_edit_approval.py` の `RengaBackend` / `TmuxBackend` / `extract_composer` | LLM を介さずにペインへ打鍵し、入力欄の中身を読み返す。transport の解決も同じ（`transport.resolve()`） | [`tools/self_edit_approval.py`](../../tools/self_edit_approval.py) 冒頭 docstring「Backends」節、`extract_composer`（401 行）、`RengaBackend`（466 行）、`TmuxBackend`（527 行） |
| 当事者の外から読む型（フック → 判定スクリプト → 1 行） | 窓口の `UserPromptSubmit` フックが監視ループの heartbeat を外から読む形。本設計の `Stop` フックも同じ fail-open の書き方にする | [`.hooks/check-dispatcher-loop.sh`](../../.hooks/check-dispatcher-loop.sh)、[`tools/dispatcher_loop_watch.py`](../../tools/dispatcher_loop_watch.py) |
| 判断仰ぎの register | 「人への確認待ち」の機械判定と、引き継ぎで落ちていないかの照合に使う | [`tools/pending_decisions.py`](../../tools/pending_decisions.py)、運用は [`.claude/skills/org-escalation/SKILL.md`](../../.claude/skills/org-escalation/SKILL.md) |
| 窓口宛メッセージの滞留会計 | 「未配送の窓口宛メッセージ」の件数取り。broker の `queue.jsonl` を読む | [`tools/secretary_queue_watcher.py`](../../tools/secretary_queue_watcher.py) 冒頭 docstring |

再利用しないもの: 窓口が `DISPATCHER_HANDOVER` を送る経路（発火判断が窓口の LLM にあるので当事者の外にならない）。自動経路では watcher が `/dispatcher-handover` を直接打つ（§4）。

## 2. 論点 1: 閾値の測り方

### 2.1 一次資料で確かめたこと

- フック共通の入力は `session_id` / `transcript_path` / `cwd` などで、文脈の使用量を表す欄は無い（https://code.claude.com/docs/en/hooks 「Common input fields」）。トークン数が入るのは、再開時の `SessionStart` の `context_tokens` と、`Agent` ツールの `PostToolUse` の `totalTokens`（サブエージェント分）だけ（同ページ SessionStart input / PostToolUse の Agent 節）。
- 使用率を直接受け取れるのは status line のコマンドだけ。`context_window.used_percentage` / `context_window_size` / `current_usage` が stdin の JSON で渡る（https://code.claude.com/docs/en/statusline 「Available data」）。`used_percentage` は `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` で計算され、出力トークンを含まない（同ページ「Context window fields」）。
- status line は新しい応答が届いたときなどに走り、300ms でデバウンスされる。メインセッションが idle の間は更新が止まりうる（同ページ「When the status line updates」）。
- `transcript_path` の会話記録は非同期に書かれ、直前の応答がまだ入っていないことがある（hooks ページ「Common input fields」の `transcript_path` 行）。
- `PreCompact` は自動要約の直前に `trigger`（`manual` / `auto`）と `custom_instructions` を受ける。使用量は受けない。exit 2 または `"decision": "block"` で止められるが、止めた場合、文脈上限の手前で起きた要約なら要約なしで会話が続き、API の上限エラーからの回復として起きた要約ならそのリクエストが失敗する（hooks ページ「PreCompact」）。
- `PostCompact` は `compact_summary` を受け取るが、結果に影響を与える手段は無い（hooks ページ「PostCompact」）。
- 自動要約が始まる量はトークン数の「auto-compact window」で決まり、`autoCompactWindow` 設定・`/autocompact`・`--autocompact`・`CLAUDE_CODE_AUTO_COMPACT_WINDOW` で変えられる。指定できる範囲は 100K〜1M トークンで、未設定なら概ねモデルの文脈上限で要約する（https://code.claude.com/docs/en/model-config 「Set the auto-compact window」）。
- idle 中の要約（`idleCompaction`）もあり、設定で止められる（https://code.claude.com/docs/en/settings-reference の `idleCompaction` 行）。これも `PreCompact` の `auto` で発火する（hooks ページ「PreCompact」の matcher 表）。
- 会話記録の各応答行には `message.usage`（`input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` / `output_tokens`）が入っている。これは手元の会話記録 1 本で確かめただけの観察なので、実装の最初に複数バージョンで確かめ直す。

### 2.2 採用: `Stop` フックが会話記録から計算する

`Stop` フックが `transcript_path` の末尾から最後の `assistant` 行の `usage` を読み、status line と同じ式（入力 3 種の和）でトークン数を出す。閾値はトークン数で持つ（auto-compact window がトークン数で指定されるため）。

- 既定の閾値: **auto-compact window の 60%**。window はフックから見えないので、役割の settings に閾値トークン数を書く（例: window 1M なら 600K）。窓口とディスパッチャーで別の値を持てるようにする。
- 会話記録の遅れ（§2.1）は 1 ターン分の誤差で、60% の余裕の中に収まる。
- 閾値を超える前に自動要約が来たら（閾値が高すぎる・idle 中の要約が先に走った）、`PreCompact` フックは**止めずに** `context_compacted_before_handover` を events に 1 行書くだけにする。止めると、上限エラーからの回復として起きた要約のときにリクエストが失敗する（§2.1）。この 1 行が出たら閾値を下げる合図として読む。
- 窓口とディスパッチャーの settings では `idleCompaction` を止める。idle 中の要約が引き継ぎより先に走るのを防ぐため。

却下した案:
- **status line で測る**: 使用率を直接もらえる唯一の経路だが、status line は settings に 1 枠しか無く、オペレーターが自分の status line を使っている場合に上書きしてしまう。idle 中に更新が止まる点（§2.1）も、静穏判定と組み合わせるには扱いにくい。
- **会話記録のファイルサイズから見積もる**: JSONL にはツール出力の全文やメタデータが入り、送信される文脈と比例しない。`usage` が同じファイルにあるので見積もる理由が無い。

## 3. 論点 2: 区切りのよいときだけ切り替える

`Stop` フックはターンが終わったときにしか走らない。したがって「ワーカーへの返事の途中」「コマンドの最中」はフックが走ること自体で外れる。そのうえで、ターン終了時点の状態を次の条件で判定し、全部満たしたときだけ `quiet: true` を書く。

### 3.1 共通の条件（Claude Code が渡す情報）

- `background_tasks` が空（背景のコマンド・サブエージェントが無い）。Claude Code は「終わった」と「背景の仕事を待って止まっている」を区別するためにこの配列を Stop フックへ渡している（hooks ページ「Stop input」）。
- `stop_hook_active` が `false`（Stop フックによる継続の途中ではない。同上）。

### 3.2 窓口の条件

- 判断仰ぎの register に、`resolve --kind to_user`（人へ伝えた）の後で `mark-user-replied` がまだ無いエントリが無い。つまり人の返事待ちが 1 件も無い（register の段階は [`.claude/skills/org-escalation/SKILL.md`](../../.claude/skills/org-escalation/SKILL.md) の 4 段階手順）。
- 人の返事待ちで止まる 5 つのゲート（`notify_sent kind=awaiting_user`、[`CLAUDE.md`](../../CLAUDE.md)「secretary が user の判断を待っている状態を通知する」）の最後の 1 行より後に、人の入力が 1 回以上ある。人の入力の時刻は窓口の `UserPromptSubmit` フックが記録する。ただし `UserPromptSubmit` は `/loop` の反復や背景サブエージェントの報告でも走る（hooks ページ「UserPromptSubmit」節）ので、人の入力と区別できる欄があるかを実装の最初に確かめる。区別できなければ、この条件は「最後の `awaiting_user` から N 分（既定 30 分）経過」で代える。引き継ぎファイルに未回答の質問が書かれていれば、復帰後の窓口がブリーフィングで出し直せるため。
- `session_crons` が空。窓口に `/loop` が仕込まれていると `/clear` 後に落ちるか残るかが Claude Code の資料に書かれていないので、ある間は切り替えない（§7 の残り）。

### 3.3 ディスパッチャーの条件

- `runs.status = 'queued'` の行が無い（派遣の途中ではない。状態の定義は [`tools/state_db/queries.py`](../../tools/state_db/queries.py) 40 行 `ACTIVE_RESERVATION_STATUSES`）。
- `session_crons` は `/loop 3m` の監視ループが入っているのが正常なので条件にしない。`/dispatcher-resume` が張り直し、残った予約による再発火は `.consumed.md` の分岐で吸収する（[`.claude/skills/dispatcher-resume/SKILL.md`](../../.claude/skills/dispatcher-resume/SKILL.md) 「Step 0 の前」）。
- 振り返りゲート（`dispatcher_retro_gate.py`）の ack 待ちは同じターンの中で回るので、`Stop` が走った時点で終わっている（ゲートの呼び出し形は [`tools/dispatcher_retro_gate.py`](../../tools/dispatcher_retro_gate.py) 冒頭 docstring）。

### 3.4 書き出す形

```json
{"role": "dispatcher", "at": "<UTC ISO8601>", "turn_seq": 412,
 "input_tokens": 640123, "threshold": 600000,
 "quiet": false, "reasons": ["queued_run:t-123"]}
```

`turn_seq` は `Stop` ごとに 1 増やす。watcher はこれで「引き継ぎのターンの後に別のターンが割り込んだか」を判定する（§4.3）。

## 4. 論点 3: 誰が `/clear` を打つか

### 4.1 採用: 常駐する決定的プロセス（context-handover watcher）

`tools/context_handover_watch.py` を 1 本作り、`/org-start` が専用ペインで起動する（pr-watch や attention watcher と同じく LLM を含まないプロセス）。30 秒ごとに `.state/context/*.json` を読み、`input_tokens >= threshold` かつ `quiet` の役割があれば §4.3 の手順を回す。同時に回すのは 1 役割だけにする。

却下した案:
- **窓口がディスパッチャーに、ディスパッチャーが窓口に打つ（相互）**: どちらも LLM で、発火を書面の手順に頼る形が残る。Issue が直したい形そのもの。
- **`Stop` フックが自分のペインへ遅延打鍵するプロセスを切り離して起動する**: 常駐ペインは要らないが、失敗の記録と再試行の間隔を持つ場所が無く、同時に 2 本走る可能性を潰しにくい。

### 4.2 人の入力に割り込まない（窓口ペイン）

次の 3 つを全部満たしたときだけ打つ。

1. 窓口の `Notification` フック（matcher `idle_prompt`）が記録した時刻が、最後の `Stop` より後にある。`idle_prompt` は応答の約 60 秒後、その間に人が打鍵しておらず背景のエージェントも走っていないときに出る（hooks ページ Notification の matcher 表と timing の注記）。
2. 打鍵の直前に `extract_composer` で読んだ入力欄が空。
3. 自分の文字列を打った直後に読み返した入力欄が、自分の文字列と完全に一致する。一致しなければ人が同時に打っていたとみなし、Enter を送らずに中止する。人の文字に自分の文字が混ざっている状態は残るので、watcher は打った文字列だけを Backspace で消し、その旨を events に書く。

ディスパッチャーのペインは人が打たない前提なので 2 と 3 だけを使う。

### 4.3 打鍵の手順（1 回分）

`R` を役割（`secretary` / `dispatcher`）とする。

1. 開始時の `turn_seq` を `s0` として控える。
2. `/R-handover auto` を打つ（text → 読み返し → Enter を別送）。`auto` 引数のときはスキルが人への案内文（secretary-handover Step 5）と `DISPATCHER_HANDOVER_READY` の打鍵依頼（dispatcher-handover Step 5）を出さず、`<R>_handover` の event だけを書く。窓口への通知は「自動引き継ぎ中、打鍵は watcher が行う」の 1 行に変える（窓口が `/clear` を打ちに来ないように）。
3. 引き継ぎファイルの `created_at` が開始より新しく、`turn_seq == s0 + 1` になるまで待つ（最大 10 分）。`s0 + 1` を超えたら、引き継ぎの後に別のターン（チャネルからのメッセージなど）が入ったので、2 からやり直す。2 回続けて超えたら中止する。
4. §6.2 の検査（引き継ぎファイルに register の全 task_id が載っているか）を通す。通らなければ中止する。
5. `/clear` を打ち、入力欄が空になるまで 1 秒間隔で最大 15 秒待つ（[`CLAUDE.md`](../../CLAUDE.md)「窓口側の打鍵規律」）。
6. `/R-resume` を打つ。窓口なら `secretary_resumed`、ディスパッチャーなら `dispatcher_resumed` の event（または窓口宛の `DISPATCHER_RESUMED`）を最大 60 秒待つ。来なければ 1 回だけ再送する（ディスパッチャーは `.consumed.md` の分岐で重複が吸収される）。
7. §6.1 の突き合わせをする。

どの段で止まっても、`auto_handover_failed`（役割・段・理由）を events に書き、`notify_sent kind=awaiting_user gate=auto_handover_failed` で人に知らせ、その役割は 30 分試さない。`/clear` を打った後に止まった場合は、人が `/R-resume` を打てば戻れる状態になっている。

## 5. 論点 4: 順序

1. ディスパッチャー（人の入力欄が無く、`/clear` を外から打つ経路が既に動いている）。
2. 窓口（§4.2 の割り込み防止が要る）。
3. ワーカーは対象外。1 タスクで閉じるので、文脈が尽きる前にペインごと終わる。

## 6. 論点 5: 引き継ぎで失うものが無いかの確かめ方

### 6.1 切り替え前後の件数の突き合わせ（watcher が毎回やる）

`/clear` の直前と復帰の直後に同じ件数を取り、減っていて events に理由が無ければ `auto_handover_loss_suspected` を人に知らせる。

| 項目 | 取り方 | 引き継ぎで失われうるか |
|---|---|---|
| 判断仰ぎの未完了件数 | `.state/pending_decisions.json` | ファイルなので残る。窓口が把握し直したかは §6.2 で見る |
| 稼働中ワーカー | `runs.status IN ('queued','in_use','review')` | state.db なので残る |
| 窓口宛の未配送メッセージ | broker の `queue.jsonl`（`secretary_queue_watcher.py` と同じ会計） | セッションの外にあるので残る。復帰後に届く |
| 背景タスク・予約 | 直前の `Stop` の `background_tasks` / `session_crons` | 窓口は空であることを条件にしている（§3）。ディスパッチャーの `/loop` は resume が張り直す |
| ディスパッチャーの cursor・idle streak・curate 記録 | dispatcher-handover が「触らない」とするファイル | ファイルなので残る |

### 6.2 引き継ぎファイルの中身の検査（`/clear` の前に 1 回）

引き継ぎファイルは LLM が書くので、構造化した状態をちゃんと写したかは書かれた後で確かめる。register の未完了エントリの task_id と、稼働中ワーカーの task_id が全部、引き継ぎファイルの本文に出てくることを確かめる。1 つでも欠けていれば `/clear` を打たない（§4.3 の 4）。

### 6.3 実装時の通し試験（1 回）

判断仰ぎ 1 件（人の返事待ち）、review 中のワーカー 1 件、窓口宛の未配送メッセージ 1 件を置いた状態で、閾値を小さくして自動引き継ぎを回す。確かめること:

- 人の返事待ちがある間は切り替わらない。
- 返事待ちを解いた後に切り替わり、復帰後のブリーフィングに 3 件とも出る。
- 未配送メッセージが復帰後に届き、ack が返る。

## 7. 残り（本設計では扱わない）

- 窓口に `/loop` が仕込まれていると切り替えない（§3.2）。`/clear` が予約を消すかどうかを確かめてから、引き継ぎファイルに予約を書いて復帰時に張り直す形にするか決める。
- 窓口ペインで人と watcher の打鍵が同時に起きた場合、§4.2 の 3 で検出して中止できるが、人の入力欄に watcher の文字が一瞬混ざる。
- `UserPromptSubmit` で人の入力とその他のターンを区別できるかは未確認（§3.2）。区別できなければ時間で代える。

## 8. 独立レビューの指摘と扱い

（Codex の設計レビュー後に追記する）
