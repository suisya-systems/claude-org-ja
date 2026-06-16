---
name: org-attach
description: >
  組織の生きているペイン（secretary / dispatcher / worker）へ tmux で**直接入る**
  ための **コマンドを出力するだけ** の read-only スキル。`mcp__org-broker__list_panes`（論理ペイン）
  と `/usr/bin/tmux -L claude-org-spike list-panes -a`（pane_id ↔ session マッピング）を
  pane_id（%N）で突き合わせ、各ペインに role + name(task_id) ラベル付きの attach コマンドを
  生成する（読取専用 `-r` / 書込は `-r` 無し / デタッチは `Ctrl-b d`）。
  「ワーカーのペインを見たい」「ワーカーのペインに入りたい」「dispatcher の様子を直接見たい」
  「ペインに入りたい」「あのペインを直接覗きたい」「attach コマンド教えて」
  「tmux でつなぎたい」「worker の画面に入る」等で発動。
  自分で attach はせず・ペインも一切変更しない（コマンド文字列とペイン一覧表を表示するのみ）。
  作業委譲（org-delegate）・ダッシュボードで状況一覧（org-dashboard）・watcher 停止（org-attention-stop）
  には発動しない。
effort: low
allowed-tools:
  - Read
  - Bash(printenv ORG_TRANSPORT)
  - Bash(/usr/bin/tmux -L claude-org-spike list-panes*)
  - Bash(/usr/bin/tmux -L claude-org-spike list-sessions*)
  - mcp__org-broker__list_panes
  - mcp__org-broker__list_peers
---

# org-attach: 組織ペインへの tmux attach コマンドを生成（read-only）

組織の生きているペイン（secretary / dispatcher / worker）へ人間が**自分のターミナルから**
tmux で直接 attach するための **attach コマンド文字列を生成して表示する** スキル。
このスキルは **何も attach しない・どのペインも変更しない**。論理ペイン一覧（broker）と
tmux 側の pane_id↔session マッピングを pane_id（`%N`）で join し、各ペインへの attach コマンドを
role + name(task_id) ラベル付きで印字するだけ。コピーした人間がコマンドを自分の端末に貼って入る。

> **輸送層 両系（frame C / 二フレーム） — このスキルは broker(tmux) フレーム専用**:
> このスキルは tmux backend（socket `claude-org-spike`）上の **detached tmux session への attach** を
> 前提とする。**broker フレーム**: 各 broker ペインは `spike-{pid}-{seq}` という独立した detached
> tmux session（1 ペイン = 1 session）として存在し、`tmux attach -t <session>` で入れる ―
> 本スキルの attach モデルが意味を持つ主フレームはこちら。**renga フレーム（opt-in,
> `ORG_TRANSPORT=renga`）は概念が異なる**: renga は**単一画面のタイリング**モデルで、ペインは 1 つの
> 生きたウィンドウ内の**タイル**であって、独立した detached session ではない。「detached session へ
> attach し直す」概念がそのまま写像せず、ペイン単位の `tmux attach -t <session>` は存在しない。
> よって renga 下では本スキルの attach 形は**適用外**で、画面そのものを直接見ればよい（attach 不要）。
> 本スキルは renga フレームでは「broker(tmux) 専用ツールです」と明示して停止する（下記 Step 0）。
>
> （**既定の二フレーム注記（Refs #604）**: org-attach は本質的に broker/tmux ツールなので header を
> **broker 主軸**で書く。輸送層全体の既定は二つのフレームで指す対象が異なる ― **運用既定**は renga
> （broker 実走 dogfood が Epic #6 Issue G まで未活性）だが、**コード既定**は `tools/transport.py:
> DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `renga`→`broker` にフリップ済み。broker tool を
> 使う手保守スキルのうち
> [`.claude/skills/org-attention-stop/SKILL.md`](../org-attention-stop/SKILL.md) は header を
> 「既定 `broker` / opt-in `renga`」のコード既定フレームで書く ― 本スキルはこの broker 主軸の先例に倣う。
> （[`.claude/skills/org-attention-start/SKILL.md`](../org-attention-start/SKILL.md) は逆に「既定 `renga`」
> の運用既定フレームで書かれており、両者は指す対象が異なるだけで矛盾しない。）総説は root
> [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節、契約面は
> [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
> Surface 8。）

> **権限フレーム**: `list_panes` は broker の **ops-tier ツール**（secretary / dispatcher のみ付与、
> worker には付与されない）。したがって org-attach は **窓口（Secretary）/ ディスパッチャー面のスキル**で、
> 人間は窓口セッションから実行する。

## なぜ `/usr/bin/tmux` 絶対パス必須か（最重要）

zsh では `tmux` は oh-my-zsh の tmux プラグインで **alias 化けする**（`tmux is an alias for
_zsh_tmux_plugin_run`）。素の `tmux ...` を出力すると、人間の zsh に貼った瞬間に alias 経由で
別物（プラグインのラッパ）が走り、`-L claude-org-spike` の socket 指定や `attach -r -t <session>` が
意図通り効かない。**生成する全コマンドで実体パス `/usr/bin/tmux` を使い**、出力にもその理由を 1 行添える。

## Step 0: 輸送フレームを確認する（renga なら停止）

まず `ORG_TRANSPORT` を read-only で読む（この 1 コマンドだけが Step 0 の許可対象）:

```bash
printenv ORG_TRANSPORT
```

renga フレームならこのスキルは適用外なので attach コマンドを生成せず停止する。

- **`ORG_TRANSPORT=renga`（明示）の場合** → 次の旨を伝えて停止する（attach コマンドは生成しない）:
  「現在は renga（単一画面タイリング）で稼働しています。renga ではペインは 1 つのライブウィンドウ内の
  タイルで、detached tmux session への attach という概念がそのまま適用されません。ペイン状況の一覧は
  [`/org-dashboard`](../org-dashboard/SKILL.md) を使ってください。」
- **それ以外（無設定 / `broker`）** → broker(tmux) フレームとして Step 1 へ進む。

## Step 1: 論理ペイン一覧を取得（broker）

```
mcp__org-broker__list_panes
```

戻り値は 1 論理ペイン = 1 レコードで、主に次のフィールドを持つ:

- `id`: tmux backend では **これが tmux の pane_id（`%N` 文字列）そのもの**。
  （runtime `broker_queue_event.schema.json`: "Backend-native pane id: int on WezTerm,
  string (e.g. `"%3"`) on tmux" / `dispatcher/runner.py`: "broker/tmux backend emits tmux
  pane_id strings of the form %N"。WezTerm backend では int になるが、本スキルが対象とする
  tmux socket `claude-org-spike` では `%N` 文字列で来る。）
- `name`: ペイン名（`worker-{task_id}` / `dispatcher` / `secretary` 等）
- `role`: `secretary` / `dispatcher` / `curator` / `worker`
- `focused`, geometry（`x` / `y` / `width` / `height`）, `cwd`

> **重要 — attach 可能なのは broker が spawn した子ペイン（dispatcher / worker）だけ**:
> 窓口（root secretary）は broker 起動時に **logical pane（bookkeeping entry、`register_logical_pane`）**
> として登録されるだけで、**adapter 実ペインを持たず `id` / pane_id が `null`（`%N` 無し）**。よって
> spike socket の detached session には**出現せず attach 対象外**である（窓口は org を起動した人間の
> 手元 terminal でそのまま動くので、そもそも attach 不要）。`id` が `%N` のレコードだけが attach 可能。
> 一次参照: [`docs/operations/broker-dogfood-runbook.md`](../../../docs/operations/broker-dogfood-runbook.md)
> §8（attach 導線の正準ガイド・「対象範囲（重要）」節）。

`list_panes` が空 / 取得不可なら「組織ペインが見つかりません。`/org-start` 済みか確認してください」と
報告して停止する。`list_panes` が `[tool_not_authorized]` を返したら ops-tier 権限が無い
（worker セッションから呼んでいる等）ので、窓口 / ディスパッチャー面で実行するよう案内する。

補足が要れば `mcp__org-broker__list_peers`（read-only）で peer 名と cwd を突き合わせてもよい
（join には必須ではない）。

## Step 2: tmux 側の pane_id ↔ session マッピングを取得

```bash
/usr/bin/tmux -L claude-org-spike list-panes -a -F '#{pane_id} #{session_name}'
```

- `-L claude-org-spike`: runtime の `SPIKE_SOCKET`。組織の tmux はこの socket 上にある
- `-a`: 全 session の全ペインを列挙
- `-F '#{pane_id} #{session_name}'`: 各行を `%N <session>` の固定 2 列に整形して**頑健にパース**する
  （tmux のデフォルト行は `session:window.pane: [geometry] [history] %N (active)` 形で、ここからも
  session 名と `%N` を取れるが、明示 `-F` の方が確実）

出力例（1 行 = 1 ペイン、各 broker ペインは独立 session `spike-{pid}-{seq}`）:

```
%0 spike-1912-1
%5 spike-1912-14
%6 spike-1912-17
```

socket 接続自体が落ちている場合は `error connecting to ... claude-org-spike` が返る。その時は
「tmux socket `claude-org-spike` に繋がりません（組織が起動していない可能性）。`/org-start` 済みか
確認してください」と報告して停止する。

## Step 3: pane_id（%N）で join

`list_panes` の `id`（`%N`）を key に、Step 2 の `%N → <session>` マッピングと突き合わせる:

```
list_panes.id (%N)  ⨝  tmux pane_id (%N)  →  各論理ペインの session 名
```

join 前に、まず `id` が `null`（`%N` 無し）の **logical pane を除外**する。これらは attach 対象外で、
join に回さず別枠で報告する（下記）。`id` が `%N` のレコードだけを tmux マッピングと突き合わせる。

両側で**マッチしないエントリは失敗にせず一覧で報告する**（実運用で起こる）:

- **`id` が `null` の logical pane（通常は窓口 = secretary）**: これは drift ではなく**正常**。
  adapter 実ペインを持たないので attach できない。「(logical pane / attach 対象外。窓口は人間の手元
  terminal)」と注記して別枠表示する（attach コマンドは出さない）。
- **list_panes に `%N` があるが tmux に同 `%N` が無い**: backend 再起動 / pane が死んだ直後の drift。
  「session 解決不可」として attach コマンドは出さず、行だけ「(tmux 側に %N 無し)」と注記して出す。
- **tmux にあるが list_panes に無い**: 組織外の手動 session / 孤児 session。「(broker 未登録 session)」
  として、role/name は不明のまま attach コマンドだけ参考表示する（人間判断に委ねる）。

## Step 4: attach コマンドを生成して出力

join できた各ペインについて、role + name(task_id) ラベル付きで次を出力する:

- **読取専用 attach（既定・安全）**: `/usr/bin/tmux -L claude-org-spike attach -r -t <session>`
  - `-r` = read-only attach。覗くだけで誤入力でペインを壊さない。**まずこちらを案内する**
- **読取書込 attach（自分で打ちたい時のみ）**: 同じコマンドから **`-r` を外す**
  → `/usr/bin/tmux -L claude-org-spike attach -t <session>`
- **デタッチ**: attach 中に `Ctrl-b d` を押すと、ペインを動かしたまま自分だけ抜ける
  （ペインは生き続ける ― close ではない）

出力末尾に「`tmux` は zsh で alias 化けするため、必ず実体パス `/usr/bin/tmux` を使うこと」を 1 行添える。

## 出力フォーマット（worked example）

Step 1 / Step 2 が次を返したとする:

`list_panes`（論理ペイン。窓口 = secretary は `id` が `null` の logical pane）:

| id | role | name |
|---|---|---|
| `null` | secretary | secretary |
| `%5` | dispatcher | dispatcher |
| `%6` | worker | worker-feat-org-attach-skill |

`/usr/bin/tmux -L claude-org-spike list-panes -a -F '#{pane_id} #{session_name}'`
（secretary は logical pane なので spike socket に**出ない**点に注意）:

```
%5 spike-1912-14
%6 spike-1912-17
```

logical pane 除外 → `%N` を持つ dispatcher / worker のみ join → 生成する出力:

```
組織ペインへの attach コマンド（broker/tmux フレーム・read-only スキル：表示のみ、自動 attach はしません）
※ これらを自分のターミナルに貼って入る

■ dispatcher (dispatcher)          session=spike-1912-14
  読取専用 : /usr/bin/tmux -L claude-org-spike attach -r -t spike-1912-14
  読取書込 : /usr/bin/tmux -L claude-org-spike attach    -t spike-1912-14

■ worker (worker-feat-org-attach-skill)   session=spike-1912-17
  読取専用 : /usr/bin/tmux -L claude-org-spike attach -r -t spike-1912-17
  読取書込 : /usr/bin/tmux -L claude-org-spike attach    -t spike-1912-17

(logical pane / attach 対象外)
- secretary (secretary): 窓口は logical pane（pane_id 無し・spike socket 非出現）。
  org を起動した人間の手元 terminal でそのまま見えるので attach 不要。

- まず読取専用（-r）で入るのを推奨。自分で打ちたい時だけ -r を外す。
- 抜けるとき: Ctrl-b d（デタッチ。ペインは動いたまま自分だけ抜ける）。
- 別セッションへ切替: Ctrl-b s（attach 中にセッション一覧から選ぶ。現状は per-session attach）。
- tmux は zsh で alias 化けするため、必ず実体パス /usr/bin/tmux を使うこと。
```

unmatched があった場合の追記例:

```
(参考) join できなかったエントリ:
- broker %3 (worker / worker-foo) : tmux 側に %3 無し（pane drift の可能性、attach 不可）
- tmux session spike-1912-9 (%9)  : broker 未登録 session（組織外/孤児の可能性）
    /usr/bin/tmux -L claude-org-spike attach -r -t spike-1912-9
```

## やらないこと（read-only 不変条件）

- 自分で `attach` しない（コマンド**文字列**を出すだけ）
- ペインを spawn / close / rename / send_keys / send_message しない
- `.state/` / registry / tmux のいずれも変更しない
- 書込み・spawn・送信系の broker / tmux ツールは `allowed-tools` に含めない
  （許可は `list_panes` / `list_peers` と read-only な tmux `list-panes` / `list-sessions` のみ）
- 状況の一覧だけが目的なら [`/org-dashboard`](../org-dashboard/SKILL.md)、作業委譲は
  [`/org-delegate`](../org-delegate/SKILL.md) を使う（本スキルは attach 用コマンド出力専用）
