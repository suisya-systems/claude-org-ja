# herdr-core 計測記録（hc 0.1.18 × Claude Code）

> ステータス: **計測記録のみ**。本書は herdr-core（`hc`）上で Claude Code を動かしたときの挙動を実測した記録で、設計の決定や実装を含まない。既存の設計書・規範文書・スキルは本書によって変わらない。

## 1. 目的と範囲

herdr-core を claude-org-ja の端末・状態・報告の土台に使えるかを判断するための計測。次の 3 点に限る。

| # | 問い | 何の判断に要るか |
| --- | --- | --- |
| 計測 1 | `hc run --bg` で起動した Claude Code の OSC 7501 自己申告（`working` / `blocked` / `done`、`kind`、`msg`）が `hc get` と `hc events --json` に届くか | ディスパッチャーの画面判定（画面 hash・スピナー）を状態申告で置き換えられるか |
| 計測 2 | 子の `hc send` が、作業中の親 Claude Code にどう届くか（ターン途中の割り込み / 次のプロンプト / 打ちかけとの連結） | 合図の設計（メッセージは箱に置き、端末への流し込みは最初のタスクと中断だけにする方針の妥当性） |
| 計測 3 | core を再起動した後、同じ作業場所で `claude --continue` を `hc run` で起動して会話が戻るか | core 再起動後のワーカー復帰の方法（run 単位で作り直すか、`--continue` を試みるか） |

## 2. 環境

| 項目 | 値 | 出典 |
| --- | --- | --- |
| hc | `hc 0.1.18`（`~/.local/bin/hc`） | `hc --version` |
| Claude Code | `2.1.319`、`--model sonnet`（画面表示は `Sonnet 5.5`） | `claude --version`、計測中の `hc read` の画面 |
| OS | WSL2（Linux `6.18.54.1-microsoft-standard-WSL2`） | `uname -r` |
| core | 計測専用の core（`HC_SESSION=oscm`）。ユーザーの既定 core（`default.sock`）には触れていない | 実行開始時の `/tmp/runtime-1000/herdr-core` のソケット一覧 `['default.sock', 'oscm.holder.c76bad6d.sock', 'oscm.sock']` |
| 作業場所 | 計測 1・2 は `/tmp/hc-oscm-cwd`、計測 3 は `/tmp/hc-oscm-cwd3`（CLAUDE.md もプロジェクト設定も無い空のフォルダ） | 計測スクリプト |
| 実行日 | 2026-11-08 | |

`hc get --json` の `identity` は、全端末で `"tmux 3.4"` だった。

## 3. 判定基準（実測の前に固定）

以下は計測を始める前に書いたもので、結果を見て書き換えない。

### 計測 1: OSC 7501 の状態が hc に届くか

- **成功**: 次の 3 つがすべて観測できる。
  1. Claude Code が作業中に `hc get` の状態が `working` になり、`hc events --json` に `working` への遷移が 1 行以上出る。
  2. 権限確認のダイアログで止まったとき、`hc get` が `blocked` になり、`kind`（`permission` など）と `msg` の少なくとも一方が入っている。`hc events --json` にも同じ遷移が出る。
  3. ターンが終わったとき `done`（または `idle`）になり、`hc events --json` にその遷移が出る。
- **部分成功**: 3 つのうち一部だけ届く、または `kind` / `msg` が空。どれが欠けたかを記録する。
- **失敗**: 状態が一度も届かない（`hc get` に状態が出ない、`hc events --json` に状態遷移が出ない）。

### 計測 2: 子の `hc send` が作業中の親にどう届くか

観測する 3 つの場面と、それぞれ記録する内容:

- **場面 A（親が作業中、ツール実行中）**: 子から送った文が (a) ターン途中で親の会話に入るか（ツール結果のあとに割り込む）、(b) 入力欄に溜まって次のプロンプトとして送られるか、(c) 失われるか。`hc send` 自体が待つか・返すかと終了コードも記録する。
- **場面 B（親がターンを終えて待機中）**: 送った文が次のプロンプトとしてすぐ処理されるか。
- **場面 C（入力欄に打ちかけの文字がある）**: 送った文が打ちかけの文字と連結されて 1 つのプロンプトになるか、別々になるか。

判定: 3 場面それぞれで (a)〜(c) のどれに当たるかを、画面と Claude Code の会話記録（`~/.claude/projects/<cwd>/*.jsonl`）の両方で確かめられれば**計測成功**。どちらか一方でしか確かめられなかった場面はその旨を書く。届き方そのものに良し悪しの基準は置かない（合図の設計への材料として記録する）。

### 計測 3: core 再起動後に `--continue` で会話が戻るか

- **成功**: core 再起動後に同じ作業場所で `hc run ... claude --continue` を起動し、再起動前の会話で覚えさせた合言葉（`PAPAYA-42`）を答える。会話記録が再起動前と同じセッションに続く（または `--continue` が前のセッションを読み込んだと分かる）。
- **失敗**: 合言葉を答えられない、`--continue` が「続ける会話が無い」と返す、または起動できない。
- 合わせて記録すること: `hc server restart` で元の Claude Code が生き残るか（help には「programs keep running」とある）、`hc server stop` → `start` 後に端末に何が戻るか（help には「stop parks kept terminals; next core start gives them shells」とある）。

## 4. 方法

- Worker の Bash は sandbox 内で動いていて、hc が core に繋がらない（`hc ls` → `{"error":{..."kind":"unavailable","message":"cannot start core: Read-only file system (os error 30)"}}`）。そこで、計測手順を 1 本の Python スクリプトにまとめ、窓口が sandbox の外（ホスト）で実行した。スクリプトは hc のコマンドとその出力をすべて時刻付きでログに残す。
- ユーザーの core を再起動しないよう、全計測で `HC_SESSION=oscm` の専用 core を使った（hc の手引き `hc --skill` に「Never `hc server stop` or restart the user's core unless asked. … Tests need their own `HC_SESSION`」とある）。専用ソケットが無ければ何もせず止まるようにした。
- 状態は `hc get <t> --json` を約 1.5 秒ごとに取り、同時に `hc events --json` を裏で流し続けて記録した。画面は `hc read <t> --lines N` で取った。会話のどこに文が入ったかは Claude Code の会話記録 `~/.claude/projects/<cwd>/*.jsonl` で確かめた。
- 計測 1 では、権限確認のダイアログを出すために `--permission-mode default` で起動した（このユーザーの既定は `auto` で、確認が出にくい）。
- 全計測を通しで 2 回実行した。1 回目は、スクリプトが状態を読むキーを間違えていたため途中で止めた（実際の形は `"status":{"app":…,"kind":…,"msg":…,"state":"blocked"}` で、状態は `status.state` にある）。2 回目で計測 1・3 と計測 2 の場面 B・C を取った。場面 A は、この 2 回目を含めて 4 回試した（残り 3 回は場面 A だけを実行）。親を「作業中」にしたまま子に送らせる段取りが毎回崩れ、成立しなかった（理由は §5.2）。

## 5. 結果

ログの所在: 計測スクリプトと全ログは作業用フォルダ（コミット対象外）に置いた。以下の引用は、ログから必要な行だけを抜き出したもの（JSON は原文のまま。長い `argv` 等は `…` で省略した）。

### 5.1 計測 1: OSC 7501 の状態は hc に届く — **成功**

起動コマンド:

```sh
HC_SESSION=oscm hc run --bg --json --name m1 --cwd /tmp/hc-oscm-cwd claude --model sonnet \
  --permission-mode default \
  "Use the Bash tool to run exactly this command: touch hello-m1.txt  -- then reply with the single word FINISHED."
```

`hc events --json` に流れた状態遷移（`type` が `status` / `turn` の行を全部。`seq` 18〜25）:

```json
{"jsonrpc":"2.0","method":"event","params":{"data":{"records":{},"reported":true,"status":{"app":"claude-code","state":"working"}},"seq":18,"terminal":"t2","type":"status"}}
{"jsonrpc":"2.0","method":"event","params":{"data":{"records":{},"reported":true,"status":{"app":"claude-code","kind":"permission","msg":"approve Bash: touch hello-m1.txt","state":"blocked"}},"seq":21,"terminal":"t2","type":"status"}}
{"jsonrpc":"2.0","method":"event","params":{"data":{"state":"blocked","turn":1},"seq":22,"terminal":"t2","type":"turn"}}
{"jsonrpc":"2.0","method":"event","params":{"data":{"records":{},"reported":true,"status":{"app":"claude-code","state":"working"}},"seq":23,"terminal":"t2","type":"status"}}
{"jsonrpc":"2.0","method":"event","params":{"data":{"records":{},"reported":true,"status":{"app":"claude-code","state":"done"}},"seq":24,"terminal":"t2","type":"status"}}
{"jsonrpc":"2.0","method":"event","params":{"data":{"state":"done","turn":2},"seq":25,"terminal":"t2","type":"turn"}}
```

`seq` 23 の `working` は、権限ダイアログで `hc keys m1 enter`（「1. Yes」を選択）を押した後の遷移。

`hc get m1 --json` も同じ値を持つ（blocked 中。スクリプトが整形して記録したものから抜粋）:

```text
"reported": true, "state": "live",
"status": {"app": "claude-code", "kind": "permission", "msg": "approve Bash: touch hello-m1.txt", "state": "blocked"},
"status_ms": 1794129868730, "title": "Touch hello-m1.txt file", "turn": 1
```

ターン終了後の `hc get m1 --json`（抜粋）: `"status":{"app":"claude-code","state":"done"}, … "turn":2`。

同時刻の画面（`hc read m1 --lines 0`、blocked 中）には Claude Code の権限ダイアログが出ていた:

```text
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and always allow access to /tmp/hc-oscm-cwd from this project
   3. Yes, and switch to auto mode · auto mode handles these prompts for you
   4. No
```

判定基準との照合:

| 基準 | 結果 |
| --- | --- |
| 1. 作業中に `working` が `hc get` / `hc events` に出る | 出た（`seq` 18、23） |
| 2. 権限確認で `blocked` になり `kind` / `msg` が入る | 出た。`kind":"permission"`、`msg":"approve Bash: touch hello-m1.txt"` |
| 3. ターン終了で `done` / `idle` が出る | `done` が出た（`seq` 24）。続けて `turn` イベントが `{"state":"done","turn":2}` を出す |

付随して観測したこと:

- `msg` は `working` のときにも入ることがある。計測 2 の親では、ツール実行中に `{"app":"claude-code","msg":"Running sleep 40; echo slept","state":"working"}` が流れた（`seq` 34）。
- `hc ls --json` の各端末も `"status":"done"` のように状態を持つ。
- `blocked` の端末へは `hc send` が断られる（ダイアログに誤って答えないための挙動。`hc send --help` に「--force: Type even if the program is blocked」とある）:

  ```text
  $ HC_SESSION=oscm hc send m1 probe-while-blocked
  {"error":{"hint":"typing would answer its dialog; read its screen first (hc read t2), or --force","kind":"blocked","message":"t2 is blocked (permission)"}}
  [exit 9]
  ```

- `blocked` 中は `hc read --lines 80` もスクロールせず、画面だけを返した（出力末尾 `[hc] only its screen: it is blocked; history is not scrolled in this state`）。
- 計測を通じて、フォルダ信頼のダイアログは一度も出なかった（`/tmp/hc-oscm-cwd` / `/tmp/hc-oscm-cwd3` とも）。

### 5.2 計測 2: 子の `hc send` の届き方 — **場面 B・C は観測、場面 A（作業中の親）は不成立**

子は `--parent` で親の下に置いた素のシェル（`hc run --bg --name c2… --parent p2 --no-notify -- sh -c 'hc send p2 "…"'`）。子の環境には `HC_TERMINAL_ID=t4` が入っていた（子の出力）。

#### 場面 B: 親がターンを終えて待機中 → 次のプロンプトとしてすぐ処理された

子の出力:

```text
sent at turn 2 (done); it started working
send_exit=0
```

親の画面（`hc read p2 --lines 40`、抜粋）:

```text
❯ MSG-B from child: reply with the word PONG-B
● PONG-B
```

会話記録では普通のユーザー入力 1 件として入っていた: `{"type": "user", "content": "MSG-B from child: reply with the word PONG-B"}`。

画面にも会話記録にも `<hc from="…">` の付け札は無く、送った文がそのまま入っていた（`hc --skill` は子からの報告が `<hc from="t7 review">…</hc>` の形で届くと説明しているが、今回の条件では付かなかった。付く条件は本計測では確かめていない）。

#### 場面 C: 入力欄に打ちかけの文字がある → 打ちかけと連結されて 1 つのプロンプトになった

`hc keys p2 H A L F space T Y P E D` で入力欄に `half typed` を残した（キー名は小文字で入力された）。

```text
❯ half typed
```

この状態で子が `hc send p2 "MSG-C from child: reply with the word PONG-C"` を送ると、親の画面は次のようになった:

```text
❯ half typedMSG-C from child: reply with the word PONG-C
● PONG-C
```

会話記録も 1 件のユーザー入力だった: `{"type": "user", "content": "half typedMSG-C from child: reply with the word PONG-C"}`。区切り文字は入らない。

#### 場面 A: 作業中の親への送信 — 4 回試行し、作業中に届く場面は作れなかった

| 試行 | 親に与えた作業 | 何が起きたか |
| --- | --- | --- |
| 1 | `sleep 40; echo slept` | 子の send が起動直後のため断られた（下の exit 5）。加えて Claude Code が長い `sleep` を前景で走らせず、バックグラウンドに回してターンを閉じた（画面: `The foreground run was blocked, so I started the same command in the background instead.`） |
| 2 | `python3 -c 'import time; time.sleep(40); print(42)'`（acceptEdits） | 親が権限確認で `blocked` になり、子の send は exit 9 で断られた |
| 3 | 同上。権限確認に答えてから送る段取り | スクリプトの待ち合わせ条件が画面上の依頼文に一致してしまい、権限確認の前に子を起動した。結果は試行 2 と同じ |
| 4 | ツールを使わず長文を書かせる | 親は約 31 秒で書き終えた（会話記録の時刻 09:38:25 → 09:38:56）。スクリプトは生成中の検知に失敗して 60 秒待ってから子を起動したため、送った時点で親は既に `done` だった。届き方は場面 B と同じだった（子の出力 `sent at turn 1 (done); it started working`） |

場面 A は不成立だが、作業中・起動直後の親への送信について次の 3 点が確かめられた:

- **起動直後は断られる（exit 5）**。何も入力されない。試行 1〜3 では、子が親の起動から 3〜4 秒後に送り、hc は約 5 秒待ってからこの理由で断った。試行 2・3 では、親が既に `working` を申告していた時刻でも断られた:

  ```text
  error: t11 is still starting: its output has not settled
  hint: nothing was typed; check hc read t11, then retry
  send_exit=5
  ```

- **`blocked` の間は断られる（exit 9）**:

  ```text
  error: t11 is blocked (permission)
  hint: typing would answer its dialog; read its screen first (hc read t11), or --force
  send_exit=9
  ```

- **`--if idle` を付ければ、作業中の相手には打たない（exit 10）**:

  ```text
  $ HC_SESSION=oscm hc send p2 --if idle 'MSG-A2 should be refused'
  {"error":{"hint":"nothing was typed","kind":"status_mismatch","message":"t3 is working, not idle"}}
  [exit 10]
  ```

作業中の親に `--if` なしで送ったときに、ターン途中に割り込むのか、溜まって次のプロンプトになるのかは観測できていない。hc の help には、作業中の相手にも打つ前提の記述がある（`hc send --help` の `--if <STATUS>  Only type if the status is exactly this [possible values: idle, working]`、`hc wait --help` の「for the parent itself, also a message typed into it while it worked (reason `message`)」）。ただし、それが Claude Code の会話のどこに入るかは書かれていない。

### 5.3 計測 3: core 再起動後の `--continue` — **成功**

1. `hc run --bg --name w3 --keep --cwd /tmp/hc-oscm-cwd3 claude --model sonnet "Remember this codeword for later: PAPAYA-42. Reply only with OK."` → `● OK`、状態は `working` → `done`。

2. **`hc server restart`**（help: 「Restart the core (e.g. after an update); programs keep running」）: Claude Code は生き残った。再起動の前後で `hc get w3 --json` の `"pid":117324` も `"status":{"app":"claude-code","state":"done"}` も変わらず、`hc server status` の `holder` も同じ（`"pid":96704`）。core の `pid` だけが変わった（`"pid":117500,"uptime_ms":9`）。`hc events` の `seq` は 0 から振り直しになった（再起動前の最後は 74、再起動後の最初のスナップショットは `"seq":0`）。

3. **`hc server stop` → `hc server start`**（help: 「stop parks kept terminals; next core start gives them shells」）: Claude Code は終了し（停止後の `pgrep -af "claude --model sonnet Remember"` は 0 件）、`--keep` 付きの `w3` は同じアドレス・同じ作業場所のシェルとして戻った。状態は `null`:

   ```json
   {"address":"t7","argv":["/usr/bin/zsh"], …, "cwd":"/tmp/hc-oscm-cwd3", …, "foreground":{"argv":["/usr/bin/zsh"],"cwd":"/tmp/hc-oscm-cwd3","name":"zsh","pid":117619}, …, "keep":true, …, "reported":false, …, "state":"live","status":null, …, "turn":1}
   ```

4. **同じ作業場所で `--continue`**:

   ```sh
   HC_SESSION=oscm hc run --bg --json --name w3r --cwd /tmp/hc-oscm-cwd3 claude --model sonnet --continue \
     "What codeword did I ask you to remember earlier? Reply with only the codeword."
   ```

   画面: `● PAPAYA-42`。状態は `working`（`seq` 8）→ `done`（`seq` 10）。端末タイトルも前の会話のもの（`Codeword PAPAYA-42 memory`）で始まった。

   `/tmp/hc-oscm-cwd3` の会話記録は 1 ファイルだけで（`4a08f379-….jsonl`）、`sessionId` を持つ 47 行はすべて同じ値だった。再起動前の 2 件と再起動後の 2 件が同じセッションに続いている:

   ```json
   {"ts": "2026-11-08T09:25:44.654Z", "type": "user", "content": "Remember this codeword for later: PAPAYA-42. Reply only with OK."}
   {"ts": "2026-11-08T09:25:47.058Z", "type": "assistant", "content": "text:OK"}
   {"ts": "2026-11-08T09:26:10.623Z", "type": "user", "content": "What codeword did I ask you to remember earlier? Reply with only the codeword."}
   {"ts": "2026-11-08T09:26:12.536Z", "type": "assistant", "content": "text:PAPAYA-42"}
   ```

判定: 合言葉を答え、会話記録も同じセッションに続いた。判定基準の「成功」に当たる。

## 6. まとめ

| 計測 | 判定 | 要点 |
| --- | --- | --- |
| 1 | 成功 | `working` / `blocked`（`kind":"permission"`、`msg` 付き）/ `done` がすべて `hc get` と `hc events --json` に届いた。`turn` イベントがターンの区切りを別に出す |
| 2 | 一部不成立 | 待機中の親には次のプロンプトとして届く。打ちかけの文字とは区切りなしで連結される。付け札は付かなかった。作業中の親への届き方は未観測。hc は起動直後（exit 5）と `blocked`（exit 9）の相手には打たず、`--if idle` なら作業中の相手にも打たない（exit 10） |
| 3 | 成功 | `hc server restart` では Claude Code が生き残る。`stop` → `start` では終了し、`--keep` の端末はシェルで戻る。同じ作業場所で `hc run … claude --continue` を起動すると同じセッションが続き、会話が戻った |

この結果から言えること、言えないこと（判断の材料。決定ではない）:

- 計測 1 の範囲では、状態を画面から推し量らなくても、`hc events --json` を読めば `working` / `blocked` / `done` と権限待ちの中身が取れる。
- 計測 2 の場面 C から、子が `hc send` で親のプロンプトに文を流し込むと、入力欄に残っていた打ちかけの文字と混ざる。`hc keys` で残した打ちかけは、連結を防がなかった（`hc send --help` には「Paste text into a terminal and press Enter (waits for a person to pause)」とある。ただし、`hc keys` による入力がこの「人の入力」として扱われるかは確かめていない）。作業中の親への届き方が未観測のため、`hc send` を合図に使えるかどうかの判断材料はまだ揃っていない。
- 計測 3 から、core を `stop` すると Claude Code は止まる。ただし会話は Claude Code 側の記録に残っており、同じ作業場所で `--continue` を付けて起動し直せば戻る。この計測は会話が 1 本だけの作業場所で行った。同じ作業場所に会話が複数あるときの挙動は計っていない。
