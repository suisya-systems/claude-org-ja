# dispatcher-view 運用ガイド

`tools/org-dispatcher-view.sh` は、窓口（secretary）の隣のペインに置いておくと、broker(tmux) backend で動いている dispatcher のペインを **常に視界に保ち続ける** ための「自己修復する read-only ビューア」である。dispatcher が restart したり auto-compact fork で broker tmux のセッション名が変わっても、本ビューアが自動で再探索・再 attach するため、手動で attach し直す手間が要らなくなる。

スクリプト本体のヘッダコメント（[`tools/org-dispatcher-view.sh`](../../tools/org-dispatcher-view.sh)）が一次仕様で、本ドキュメントは「窓口の隣に常時表示として置くまでの実運用手順」をまとめる。

## 1. 何が見えるか

- broker(tmux) backend では各ペインが別々の detached tmux session として broker の専用 socket（既定 `claude-org-broker`）に居る。dispatcher のペインもその socket 上の detached session として存在する
- 本ビューアは「pane の cwd basename が `.dispatcher` の session」を純 tmux で役割解決し、見つかったセッションへ `-r`（read-only）で attach する
- detach（あるいは dispatcher の restart / auto-compact fork による session 名変化）で attach から抜けると、自動で再探索ループに戻り、見つかれば再 attach する
- broker daemon の HTTP / MCP API は一切叩かない（純 tmux 役割解決）。control plane に余計な負荷をかけない

## 2. 適用範囲

| 範囲 | 適用可否 | 備考 |
|---|---|---|
| broker の **tmux backend**（Linux / macOS / WSL） | 適用 | 本スクリプトの想定環境 |
| broker の **Windows backend (wezterm)** | 非適用 | broker の Windows backend は tmux ではなく wezterm のため、本スクリプトは動かない。同等品は follow-up |
| **renga** フレーム | 不要 | renga は単一画面タイリングで、各ペインが別 tmux session に分かれず「detached session へ attach し直す」概念が写像しないため不要 |

「見る側の端末」が WezTerm / tmux のどちらでも本ビューアは動く。スコープ外なのは **broker backend 自体が wezterm のケース** のみ。

## 3. WezTerm 手順（推奨）

WezTerm 側でペインを分割し、新ペインで本ビューアを起動する。WezTerm の split キーと内側 dispatcher 側の `Ctrl-b` プレフィックスは別系統なので **キー衝突が起きない**（後述の tmux 手順と比較した最大の利点）。

1. 窓口セッションの WezTerm ペインにフォーカスがある状態で、ペインを分割する:
   - 左右に分割: `Ctrl+Shift+Alt+%`
   - 上下に分割: `Ctrl+Shift+Alt+"`
2. 開いた新ペインで本ビューアを起動する:
   ```bash
   cd /path/to/claude-org-ja
   tools/org-dispatcher-view.sh
   ```
   起動メッセージに `socket=claude-org-broker, mode=read-only` と出れば想定どおり。
3. dispatcher が見つかれば自動で attach する。見つからなければ「dispatcher の tmux ペインが見つかりません」と出て再探索ループに入る（dispatcher が起動すれば自動で attach する）

### 操作キー（WezTerm）

| 操作 | キー |
|---|---|
| ペイン間移動 | `Ctrl+Shift+←/→/↑/↓` |
| 内側 dispatcher を detach する（自分だけ抜ける） | `Ctrl-b d` |
| ビューア自体を終了する | `Ctrl-b d` で detach → 再探索プロンプトに戻ったところで `Ctrl-C` → `exit` |

WezTerm の既定キーバインドが上記である前提。`.wezterm.lua` 等でカスタム設定をしている場合は当該キーに読み替えること。`cd` のパス例は本リポジトリの clone 先に応じて読み替える。

## 4. tmux 手順（衝突注意）

外側 tmux のペインから入れ子 attach する形になるため、**2 つの注意点** がある。

1. 起動コマンドの先頭に `TMUX=` を付ける（環境変数 unset）。理由: 外側 tmux の中から別 tmux サーバー（broker socket）へ入れ子 attach するため、`TMUX=` を付けないと tmux が `sessions should be nested with care` で attach を拒否する
2. 内側 dispatcher に prefix を送るには `Ctrl-b` を **2 回** 押す（外側 tmux が 1 回目を横取りするため）

### 手順

1. 窓口セッションの tmux ペインにフォーカスがある状態で、ペインを分割する:
   - 左右に分割: `Ctrl-b %`
   - 上下に分割: `Ctrl-b "`
2. 開いた新ペインで本ビューアを起動する:
   ```bash
   cd /path/to/claude-org-ja
   TMUX= tools/org-dispatcher-view.sh
   ```
3. dispatcher が見つかれば自動で attach する

### 操作キー（tmux 入れ子）

| 操作 | キー |
|---|---|
| 外側ペイン間移動 | `Ctrl-b ←/→` / `Ctrl-b o` |
| 内側 dispatcher を detach する（自分だけ抜ける） | `Ctrl-b Ctrl-b d` |
| ビューア自体を終了する | `Ctrl-b Ctrl-b d` で detach → 再探索プロンプトに戻ったところで `Ctrl-C` → `exit` |

内側 prefix を `Ctrl-b` 2 回で送る点が WezTerm 経路との最大の違い。外側 tmux の prefix を別キー（例: `Ctrl-a`）に再設定している場合は、そちらと `Ctrl-b` の組み合わせに読み替える。

## 5. オプション

### 5.1 `--rw`（読み書き attach）

既定は read-only（`-r`）で安全だが、dispatcher のペインに **直接打鍵したい** ときだけ `--rw` を付ける:

```bash
tools/org-dispatcher-view.sh --rw
```

dispatcher ペインへの誤入力は control plane を壊しうる（worker 監視ループや handover フローを破る可能性がある）。常時可視化の用途では `--rw` は付けず、書き込みが本当に必要なときだけスポットで起動するのが望ましい。

### 5.2 環境変数 `ORG_BROKER_SOCKET`

broker の tmux socket 名（既定 `claude-org-broker`）。runtime 側で socket 名を変えている場合のみ設定する:

```bash
ORG_BROKER_SOCKET=my-broker tools/org-dispatcher-view.sh
```

通常運用では設定不要。

## 6. 自己修復の挙動

- **dispatcher 不在時の再探索**: socket は繋がるが `.dispatcher` cwd のペインが無い場合、「dispatcher の tmux ペインが見つかりません（degraded / 未起動）。再探索中…」と出て 2 秒ごとに再探索する
- **socket 不通時の再試行**: broker daemon が未起動などで tmux socket に繋がらない場合、「broker tmux socket (...) に繋がりません」と出て 2 秒ごとに再試行する
- **attach 後の自動復帰**: dispatcher が restart / auto-compact fork して session 名が変わると、tmux 側で attach が切れる。本ビューアはそれを検知してループ先頭に戻り、新しい session 名を再解決して再 attach する
- **複数候補警告**: 同一 broker socket 上に複数 org / 複数 `.dispatcher` ペインが居る稀ケースでは、「dispatcher 候補が N 件見つかりました」と警告し 1 件目を採用する。意図しない dispatcher に attach しうるので broker daemon の状態を確認すること
- **終了動作の注意**: attach 中の `Ctrl-C` は tmux クライアント / dispatcher ペイン側に渡るので、本ビューアの SIGINT trap には届かない（`--rw` では dispatcher へ `^C` を送ってしまう）。終了は必ず **detach（`Ctrl-b d` または `Ctrl-b Ctrl-b d`）→ 再探索プロンプト → `Ctrl-C`** の順で行う

## 7. トラブルシューティング

### 7.1 起動しても何も映らない / すぐ「見つかりません」になる

broker socket にセッションが居るかを直接確認する:

```bash
/usr/bin/tmux -L claude-org-broker list-panes -a
```

- 何も出ない → broker daemon が起動していない / dispatcher がまだ立ち上がっていない。`/org-start` 直後で broker が ready になる前のタイミングや、`/org-suspend` 後の状態
- セッションは出るが `.dispatcher` cwd のペインが無い → dispatcher が degraded（bg-pty フォールバック）か未起動。dispatcher 復元を別経路で確認する

### 7.2 `sessions should be nested with care` が出る

外側 tmux の中から起動しているのに `TMUX=` を付け忘れている。tmux 手順（§4）の起動コマンドどおり、先頭に `TMUX=` を付け直す。

### 7.3 「dispatcher 候補が N 件見つかりました」と警告が出る

同一 broker socket に `.dispatcher` cwd のペインが複数居る状態。本ビューアは 1 件目を採用するが、意図したものか確認する:

```bash
/usr/bin/tmux -L claude-org-broker list-panes -a \
  -F '#{session_name}\t#{pane_current_path}' | grep '\.dispatcher$'
```

`ORG_BROKER_SOCKET` を分けるか、不要な dispatcher セッションを片付けることで解消できる。

### 7.4 `tmux` コマンドが alias 化けする

本スクリプトは内部で `/usr/bin/tmux` を実体パスで叩くため、zsh + oh-my-zsh の tmux プラグインによる alias は無視される（影響を受けない）。手動で `tmux -L ... list-panes` を実行する場合のみ alias 化けに注意（実体パスを使うか `command tmux ...` で剥がす）。

## 8. 関連

- スクリプト本体: [`tools/org-dispatcher-view.sh`](../../tools/org-dispatcher-view.sh)
- broker 運用全般: [`docs/operations/broker-dogfood-runbook.md`](broker-dogfood-runbook.md)
- attention 通知（人間が応答すべきイベントの能動通知）: [`docs/operations/attention-watch.md`](attention-watch.md)
