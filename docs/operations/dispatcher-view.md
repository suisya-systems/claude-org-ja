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

> **ただし、その WezTerm をさらに renga の中で開いている場合はこの限りでない。** renga の org サイドバーは既定で有効で `Ctrl+B` を消費するため、内側 dispatcher へ `Ctrl-b` が届かない。§4 の回避策を先に適用すること。

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

WezTerm の既定キーバインドが上記である前提。`.wezterm.lua` 等でカスタム設定をしている場合は当該キーに読み替えること。表中の `Ctrl-b` も内側 tmux prefix の既定値なので、prefix を変更している場合は **設定した prefix** に読み替える。`cd` のパス例は本リポジトリの clone 先に応じて読み替える。

## 4. 外側フレームが renga の場合（`Ctrl+B` 衝突）

本ビューアや broker セッションへの attach を **renga の画面の中で** 行う場合、renga の org サイドバーが `Ctrl+B` を消費するため内側 tmux の prefix が届かない。§3 / §5 の手順に入る前に、下記いずれかの回避策を適用すること。

renga 側 UI キーの `Ctrl+B`（org サイドバーのトグル）と tmux prefix の `Ctrl-b` は、**表記が違うだけで同じ物理入力**（`Ctrl` + `b`）である。以下、renga の機能として押す場合を `Ctrl+B`、tmux の prefix として押す場合を `Ctrl-b` と書き分ける。

### 4.1 何が起きるか

renga 2.0 の org サイドバーは **既定で有効**（`OrgSidebarMode::Coexist` が `Default`。renga `src/config.rs:58`）で、有効な間は `Ctrl+B` を消費して PTY へ渡さない（renga `src/app/keyboard_input.rs:339-345` が `toggle_org_sidebar()` して `return Ok(true)` するため、renga `src/main.rs:492-493` の `if !consumed` に入らない）。エラーも出ずサイドバーがトグルするだけなので、人間からは「押しても効かない」ようにしか見えない。

**§5 の「`Ctrl-b` を 2 回押す」とは原因が別物である。** 外側が tmux の場合は、1 回目を外側 tmux が prefix として処理したうえで 2 回目を内側セッションへ送れる（だから連打が回避策になる）。renga は最初の `Ctrl+B` の時点で consume して PTY へ 1 バイトも流さないため、**何回押しても内側 tmux には 1 文字も届かない**。§5 の 2 回押し手順が成立するのは、下記の回避策を適用して `Ctrl+B` が PTY へ落ちるようになってからである。

**`Ctrl+B` でサイドバーを非表示にトグルしても解決しない。** 消費するかどうかの判定は表示状態ではなく機能の enable 状態（`org_sidebar_enabled()` = モードが `off` 以外。renga `src/app/org_sidebar.rs:67-69`）で行われるため、非表示にしてもキーは消費され続ける（renga `src/app/tests/org_sidebar.rs:514-524` が「非表示状態でも consumed」を挙動として固定している）。`coexist` / `replace` はいずれも「有効」であり、消費を止められるのは `off` だけである（同 `:494-512` が `off` のときだけ PTY へ通ることを固定している）。

### 4.2 回避策 (i): renga の org サイドバーを無効化する

renga の設定ファイルに次を書く。パスは Unix が `$XDG_CONFIG_HOME/renga/config.toml`（`XDG_CONFIG_HOME` 未設定なら `$HOME/.config/renga/config.toml`）、Windows が `%APPDATA%\renga\config.toml`（renga `src/config.rs:2-3`, `:329-330`）。**値は引用符付きの文字列**である:

```toml
[ui]
org_sidebar = "off"
```

これは renga 側が「shell / tmux / readline で `Ctrl+B` が要る利用者向けの documented escape hatch」として明示的に用意しているもの（renga `src/app/keyboard_input.rs:334-338` のコメント）。設定は renga 起動時に一度だけ読まれる（renga `src/main.rs:146`）ため、**変更後は renga を起動し直す**。同等の CLI フラグは無く（CLI override が受け取るのは ime / lang / fps 系のみ。renga `src/main.rs:147-153`）、経路は設定ファイルのみである。

トレードオフ: org サイドバーは全タブとその中のペインを一覧する cross-tab パネル（renga `src/app/org_sidebar.rs:1-3`）なので、`off` にするとその可視性を丸ごと失う。サイドバーを残したい場合は 4.3 を選ぶ。

### 4.3 回避策 (ii): 内側 tmux の prefix を変更する

サイドバーを残したい場合は、内側 tmux（= attach 先の broker socket 上の tmux server）の prefix を `Ctrl-b` 以外へ動かして物理キーの衝突自体を無くす。

prefix は **tmux server ごとの設定**なので、2 段階で入れる。

1. 今後起動する server 向けに `~/.tmux.conf` へ永続化する:

   ```tmux
   set -g prefix C-a
   bind C-a send-prefix
   ```

2. **既に走っている broker server には上記の編集は反映されない。** 衝突に気付くのは大抵 server が走っている最中なので、その場で効かせるには socket を指定して直接設定する（attach していない別のペイン / 端末から実行してよい）:

   ```bash
   /usr/bin/tmux -L claude-org-broker set -g prefix C-a
   /usr/bin/tmux -L claude-org-broker bind C-a send-prefix
   ```

   `ORG_BROKER_SOCKET` で socket 名を変えている場合はその名前に読み替える。既に attach して抜けられなくなっている場合も、この経路なら別端末から prefix を差し替えて detach できる。

読み替えの範囲: 以後 **内側 tmux（broker socket 側）を指す** `Ctrl-b` — 本ドキュメントの detach / セッション切替、および `tools/org-dispatcher-view.sh` の出力 — を、設定した prefix（上例なら `Ctrl-a`）に読み替える。**§5 の外側 tmux 用のキー**（ペイン分割、および 2 回押しの 1 回目）は別 server の prefix なので、外側を変更していない限り `Ctrl-b` のままである。

### 4.4 runtime の capacity escalation が案内する `Ctrl+B` について

runtime のディスパッチャーは容量不足時の escalate 文面に「Reclaim them with Ctrl+B or `[ui] org_sidebar = "off"` and re-run.」を含める（runtime `src/claude_org_runtime/dispatcher/runner.py:1506-1513`）。この `Ctrl+B` は **renga のサイドバー操作の案内であり、内側 tmux prefix の送信案内ではない**。内側 tmux を触っている最中にこの文面を読んでも、その打鍵で prefix が届くようになるわけではない。

## 5. tmux 手順（衝突注意）

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

内側 prefix を `Ctrl-b` 2 回で送る点が WezTerm 経路との最大の違い。外側 tmux の prefix を別キー（例: `Ctrl-a`）に再設定している場合は、そちらと `Ctrl-b` の組み合わせに読み替える。**この 2 回押しが成立するのは、外側が tmux の場合に限る**。外側フレームが renga の場合は 1 回目の時点で消費されるため 2 回押しでも届かない（§4）。

## 6. オプション

### 6.1 `--rw`（読み書き attach）

既定は read-only（`-r`）で安全だが、dispatcher のペインに **直接打鍵したい** ときだけ `--rw` を付ける:

```bash
tools/org-dispatcher-view.sh --rw
```

dispatcher ペインへの誤入力は control plane を壊しうる（worker 監視ループや handover フローを破る可能性がある）。常時可視化の用途では `--rw` は付けず、書き込みが本当に必要なときだけスポットで起動するのが望ましい。

### 6.2 環境変数 `ORG_BROKER_SOCKET`

broker の tmux socket 名（既定 `claude-org-broker`）。runtime 側で socket 名を変えている場合のみ設定する:

```bash
ORG_BROKER_SOCKET=my-broker tools/org-dispatcher-view.sh
```

通常運用では設定不要。

## 7. 自己修復の挙動

- **dispatcher 不在時の再探索**: socket は繋がるが `.dispatcher` cwd のペインが無い場合、「dispatcher の tmux ペインが見つかりません（degraded / 未起動）。再探索中…」と出て 2 秒ごとに再探索する
- **socket 不通時の再試行**: broker daemon が未起動などで tmux socket に繋がらない場合、「broker tmux socket (...) に繋がりません」と出て 2 秒ごとに再試行する
- **attach 後の自動復帰**: dispatcher が restart / auto-compact fork して session 名が変わると、tmux 側で attach が切れる。本ビューアはそれを検知してループ先頭に戻り、新しい session 名を再解決して再 attach する
- **複数候補警告**: 同一 broker socket 上に複数 org / 複数 `.dispatcher` ペインが居る稀ケースでは、「dispatcher 候補が N 件見つかりました」と警告し 1 件目を採用する。意図しない dispatcher に attach しうるので broker daemon の状態を確認すること
- **終了動作の注意**: attach 中の `Ctrl-C` は tmux クライアント / dispatcher ペイン側に渡るので、本ビューアの SIGINT trap には届かない（`--rw` では dispatcher へ `^C` を送ってしまう）。終了は必ず **detach（`Ctrl-b d` または `Ctrl-b Ctrl-b d`）→ 再探索プロンプト → `Ctrl-C`** の順で行う。ここの `Ctrl-b` は各 tmux server の prefix の既定値であり、変更している場合は §4.3 の読み替え範囲に従う（入れ子形 `Ctrl-b Ctrl-b d` は 1 回目が外側 server、2 回目以降が内側 server の prefix）。外側フレームが renga の場合は、この detach 打鍵自体が届かないので先に §4 の回避策を適用すること（適用しないとビューアから抜ける手段が無くなる）

## 8. トラブルシューティング

### 8.1 起動しても何も映らない / すぐ「見つかりません」になる

broker socket にセッションが居るかを直接確認する:

```bash
/usr/bin/tmux -L claude-org-broker list-panes -a
```

- 何も出ない → broker daemon が起動していない / dispatcher がまだ立ち上がっていない。`/org-start` 直後で broker が ready になる前のタイミングや、`/org-suspend` 後の状態
- セッションは出るが `.dispatcher` cwd のペインが無い → dispatcher が degraded（bg-pty フォールバック）か未起動。dispatcher 復元を別経路で確認する

### 8.2 `sessions should be nested with care` が出る

外側 tmux の中から起動しているのに `TMUX=` を付け忘れている。tmux 手順（§5）の起動コマンドどおり、先頭に `TMUX=` を付け直す。

### 8.3 `Ctrl-b` を押しても内側 dispatcher に何も起きない / detach できない

外側フレームが renga で、org サイドバー（既定で有効）が `Ctrl+B` を消費している可能性が高い。サイドバーの表示がトグルするだけでエラーは出ない。§4 の回避策（renga 設定 `[ui] org_sidebar = "off"`、または内側 tmux の prefix 変更）を適用する。外側が tmux の場合は原因が別で、`Ctrl-b` を 2 回押す（§5）。

### 8.4 「dispatcher 候補が N 件見つかりました」と警告が出る

同一 broker socket に `.dispatcher` cwd のペインが複数居る状態。本ビューアは 1 件目を採用するが、意図したものか確認する:

```bash
/usr/bin/tmux -L claude-org-broker list-panes -a \
  -F '#{session_name}\t#{pane_current_path}' | grep '\.dispatcher$'
```

`ORG_BROKER_SOCKET` を分けるか、不要な dispatcher セッションを片付けることで解消できる。

### 8.5 `tmux` コマンドが alias 化けする

本スクリプトは内部で `/usr/bin/tmux` を実体パスで叩くため、zsh + oh-my-zsh の tmux プラグインによる alias は無視される（影響を受けない）。手動で `tmux -L ... list-panes` を実行する場合のみ alias 化けに注意（実体パスを使うか `command tmux ...` で剥がす）。

## 9. 関連

- スクリプト本体: [`tools/org-dispatcher-view.sh`](../../tools/org-dispatcher-view.sh)
- broker 運用全般: [`docs/operations/broker-dogfood-runbook.md`](broker-dogfood-runbook.md)
- attention 通知（人間が応答すべきイベントの能動通知）: [`docs/operations/attention-watch.md`](attention-watch.md)
