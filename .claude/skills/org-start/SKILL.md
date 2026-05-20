---
name: org-start
description: >
  組織を起動する。前回の状態を読み込んでブリーフィングし、
  ディスパッチャーとキュレーターペインを起動する。ClaudeCode起動直後に1回実行する。
  「起動して」「スタート」「始めて」等でも発動。
effort: low
allowed-tools:
  - Read
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python -m tools.state_db.importer:*)
  - Bash(py -3 dashboard/org_state_converter.py:*)
  - Bash(python3 dashboard/org_state_converter.py:*)
  - Bash(py -3 tools/check_runtime_version.py:*)
  - Bash(python3 tools/check_runtime_version.py:*)
  - mcp__renga-peers__*
---

# org-start: 組織の起動

ClaudeCode起動後に最初に実行するスキル。前回の状態復元、ディスパッチャー起動、キュレーター起動を行う。

> **前提**: この Claude は `renga --layout ops` で起動された窓口ペイン内で動作している。
> `RENGA_SOCKET` / `RENGA_PANE_ID` 環境変数が継承されているので、`mcp__renga-peers__*` MCP
> ツール 14 種（`spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` /
> `list_panes` / `new_tab` / `send_message` / `list_peers` / `set_summary` /
> `check_messages` / `inspect_pane` / `poll_events` / `send_keys` /
> `set_pane_identity`）で同タブ内のペイン操作・ピア通信・画面スクレイプ・lifecycle
> event 購読・raw キー入力まですべてカバーできる（**renga 0.18.0+ 前提**）。
>
> **state DB 前提 (Issue #267 / M4)**: `.state/state.db` が唯一の SoT。
> read 経路は DB のみ (markdown fallback は M4 で撤去)、構造化セクション
> (Status / Dispatcher / Curator / Worker Directory Registry /
> Active Work Items / Resume Instructions) の write 経路は
> `StateWriter.transaction()` 経由（post-commit hook が
> `.state/org-state.md` を DB から自動再生成、markdown 直接編集禁止 —
> drift_check が検出する）。free-form ノート（学び / Pending Lead 等）
> は `notes/` 配下に保存する。`.state/journal.jsonl` は M4 で廃止。
> DB が無い場合は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で構築する。

## Step 0: 初期化

1. `mcp__renga-peers__set_summary` で自分のサマリーを設定する: 「Secretary: 窓口」
   - ワーカー / ディスパッチャー / キュレーターが `mcp__renga-peers__list_peers` で窓口を発見するために必須
2. `renga-peers` MCP の疎通確認: `mcp__renga-peers__list_panes` を呼び出す。
   - エラーなく応答が返れば MCP 有効。以降 renga-peers MCP ツールが使える前提で進む
   - エラーが返る / ツール未登録の場合はユーザーに `renga mcp install` の実行を促し、
     Skill の実行を一時停止する（MCP 導入後にやり直してもらう）。詳細は README の
     「インストール」セクション参照
3. **secretary ペイン identity の検証と自動リカバリ**:
   - `mcp__renga-peers__list_panes` の結果から `focused=true` のペイン（= 自分）を特定する
   - 期待値: `name == "secretary"` かつ `role == "secretary"`
   - **不一致の場合** — `renga --layout ops` 以外の経路で起動された / 旧ops.tomlで起動された既存セッションに attach 等:
     1. `mcp__renga-peers__set_pane_identity(target="focused", name="secretary", role="secretary")` を呼んで自動修復
     2. 成功すれば警告ログを events テーブルに残して続行（`bash tools/journal_append.sh secretary_identity_restored note=auto_recovered`）
     3. 失敗ケースの分岐:
        - `name_in_use` エラー: 既存の別ペインが `secretary` を占有している。ユーザーに状況を報告し、「現セッション継続なら全ワーカーに `to_id="{numeric_pane_id}"` で送信させる」「永続修復なら `/org-suspend` → 終了 → `renga --layout ops` で再起動」の選択肢を提示
        - `name_invalid` / その他: ユーザーに原因を報告
   - **一致している場合**: そのまま続行
4. `registry/org-config.md` の `workers_dir` を読み、ワーカーディレクトリの存在を確認する。
   存在するディレクトリがあれば一覧をユーザーに報告する（削除は絶対にしない）。
   **禁止事項**: ワーカーディレクトリは過去の作業成果や再利用可能なプロジェクトを含むため、
   org-start 時に削除してはならない。org-delegate のディレクトリ保持ポリシーに従うこと。

## Step 1〜3: 並列起動フェーズ

> **Issue #410 / Stage B**: Step 0 (set_summary / MCP 疎通 / identity 検証 / workers_dir 確認の 4 サブステップすべて) が完了した時点で dispatcher / curator の `spawn_claude_pane` を発火し、Claude 起動待ち (〜30〜60s) と並列に Block B (前回状態の DB 読み込み) / Block C (ダッシュボード server 起動) を進める。serial 実行時の wall-clock 〜3 分を ~35s まで短縮する目的。
>
> **実行モデル**: Secretary が以下の 3 ブロック (A/B/C) を発火し、最後に block D で合流する。block A は I/O bound（renga MCP の応答は数百 ms、その後は別プロセスである Claude の boot を待つだけ）なので B/C と wall-clock を完全に overlap できる。

### Block A: ディスパッチャー / キュレーターペイン spawn (発火のみ、boot 完了は待たない)

ペイン配置は org-delegate/references/pane-layout.md に従う (renga 版)。spawn 自体は MCP のレスポンスが数百 ms オーダーなので連続発行で済む（並列発行する必要はなく、curator は target="dispatcher" で dispatcher の name 解決を必要とする）。

1. dispatcher 用 `spawn_claude_pane`:
   ```
   mcp__renga-peers__spawn_claude_pane(
     target="focused",
     direction="horizontal",
     role="dispatcher",
     name="dispatcher",
     cwd=".dispatcher",
     permission_mode="bypassPermissions",
     model="sonnet"
   )
   ```
   返り値の `"Spawned pane id=N."` から dispatcher の `pane_id` を控える。引数の意味と落とし穴は本ファイル末尾「### 補足: spawn_claude_pane 引数の詳細」を参照。
2. ↑が返却された直後に curator 用 `spawn_claude_pane`:
   ```
   mcp__renga-peers__spawn_claude_pane(
     target="dispatcher",
     direction="vertical",
     role="curator",
     name="curator",
     cwd=".curator",
     permission_mode="auto",
     model="opus"
   )
   ```
   target="dispatcher" は (1) で確立した安定名を解決する。curator の `pane_id` も控える。
3. **ここで spawn 結果のみブロックする**（Claude の boot 完了は待たない）。両 spawn が `[<code>] <msg>` で失敗していた場合は本ファイル末尾「### 失敗モード」へ。両 spawn が成功（pane_id 取得）なら、Block B / C と並列に進む。

### Block B: 前回の状態確認

read 経路は **DB only**（Issue #267 / M4）。Block A の spawn 発火と並列で実行する（Block A は MCP 経由でペイン作成を依頼するだけで Claude の boot は別プロセスのため、ここで CPU / I/O を競合しない）。

1. `.state/state.db` が存在するか確認する
   - 存在 → DB をクエリ:
     ```bash
     python -c "from tools.state_db import connect; from tools.state_db.queries import get_org_state_summary; import json; \
       conn = connect('.state/state.db'); \
       print(json.dumps(get_org_state_summary(conn), ensure_ascii=False, indent=2, default=str))"
     ```
     `active_runs` / `recent_events` / `run_status_counts` / `session.status` / `session.objective` で前回状態を把握する
   - 存在しない → 初回起動と判断する。Secretary に importer 実行を促す:
     `python -m tools.state_db.importer --db .state/state.db --root . --rebuild --no-strict`
2. session.status を確認する:
   - `SUSPENDED` なら /org-resume の Phase 1〜3（ブリーフィング・照合・再開計画）を実行する。
     Block A の spawn は既に発火済みなのでブリーフィング中に裏で Claude が boot している。
     ブリーフィング完了後、Block D の合流で dispatcher / curator が ready になるのを待ち、org-resume の Phase 4（ワーカー再派遣）を人間の承認に基づいて実行する
   - `ACTIVE` なら、前回のセッションが突然終了した可能性がある。
     各ワーカーディレクトリの git 状態を確認し、現状を報告する

### Block C: ダッシュボードサーバー起動

Block A の spawn 発火と並列。ダッシュボード server は別プロセス（Python HTTP server）で Claude pane と独立。

1. ダッシュボードサーバーが起動しているか確認する:
   ```bash
   cat .state/dashboard.pid 2>/dev/null && kill -0 $(cat .state/dashboard.pid) 2>/dev/null && echo "running" || echo "stopped"
   ```
2. 停止中であれば起動する:
   ```bash
   python3 dashboard/server.py &   # Mac/Linux
   py -3 dashboard/server.py &     # Windows
   ```
3. ユーザーに案内する:
   「ダッシュボードを起動しました → http://localhost:8099」

### Block C2: claude-org-runtime バージョン drift 検出 (Issue #472)

Block A の spawn 発火と並列。`claude-org-runtime` の installed バージョンと PyPI の latest を比較し、drift があれば Step 4 の起動完了報告に 1 行 warning を添える。auto-upgrade は行わず通知のみ。

1. drift チェックを実行する:
   ```bash
   py -3 tools/check_runtime_version.py   # Windows
   python3 tools/check_runtime_version.py # Mac/Linux
   ```
2. 出力分岐:
   - stdout が空 (exit 0): INSTALLED == LATEST、未インストール、オフライン、PyPI レスポンス parse 失敗 のいずれか。**Step 4 報告に warning 行を出さない**（silent）
   - stdout に `[runtime drift] ...` の 1 行: drift 検出。**この 1 行をそのまま Step 4 起動完了報告の末尾に warning として転記する**

> 設計メモ:
> - latest 取得は PyPI JSON API (`https://pypi.org/pypi/claude-org-runtime/json`) を urllib.request で叩く (timeout 3s)。`pip index versions` は experimental で stderr に warning を吐くため採用しない
> - **pin window**: ja の `pyproject.toml` 依存に書かれた制約 (例 `>=0.1.9,<0.2`) を読み取り、PyPI releases から制約を満たす最新だけを latest として比較する。これにより `0.2.x` が PyPI にリリースされても窓外への upgrade を促さない（`packaging` モジュール利用）。`packaging` 未インストール環境では silent skip
> - 「drift = 古い」も「drift = preview 入り (installed > latest の release channel ずれ)」も同じく 1 行で通知する。auto-upgrade はせず、対応はユーザー判断に委ねる
> - スクリプト本体: [`tools/check_runtime_version.py`](../../../tools/check_runtime_version.py)

> **Sidebar: attention watcher の起動案内（optional, 明示起動推奨）**
>
> 承認待ち / 判断待ち / CI 失敗 / silent stop / PR merged 等を OS notification + 音 + terminal bell で能動的に通知する watcher を別途常駐させられる。**`/org-start` からの自動起動はしない**（OS 通知 backend は環境依存が強く、勝手に音が鳴ると不快になりやすいため。設計 [`docs/design/attention-notification.md`](../../../docs/design/attention-notification.md) §11 Q1）。
>
> 有効化したいユーザーには Step 4 の起動完了報告と合わせて [`/org-attention-start`](../org-attention-start/SKILL.md) の実行を案内する。skill が以下を一括で行う:
>
> - `.state/attention.json` 未配置時は `tools/templates/attention.example.json` から自動コピー
> - dispatcher ペインの右側を vertical split し `claude-org-runtime attention watch ...` を常駐起動
> - pane_id を `.state/attention_pane.json` sidecar に記録（停止は [`/org-attention-stop`](../org-attention-stop/SKILL.md) で参照）
>
> 1 回限りの動作確認は `claude-org-runtime attention scan --state-dir .state --config .state/attention.json --dry-run --json`（`--config` を外すと runtime 中立の英語 default が出るので、ja テンプレートの導通確認には必ず付ける）。OS 別 backend 挙動・トラブルシューティング・別ターミナルからの素 CLI 起動手順は [`docs/operations/attention-watch.md`](../../../docs/operations/attention-watch.md) を参照。

### Block D: 両ペインの合流 (Enter / list_peers poll / 挨拶 / DB write / snapshot)

Block A の spawn が両方成功した後、両ペインで Claude が並列に boot している。**boot 完了を 1 回の wait で済ませ、Enter / list_peers poll / 挨拶送信 / DB write を両方まとめて発行する**ことで Stage A の wall-clock 短縮 (180s→90s) を得る。

1. **両ペインに Enter を送信** — Claude Code 初回起動時の「Load development channel? (Y/n)」プロンプトを承認する。両者並列に発行（renga MCP は逐次処理だが、レスポンスは数百 ms オーダーなので体感同時）:
   ```
   mcp__renga-peers__send_keys(target="dispatcher", enter=true)
   mcp__renga-peers__send_keys(target="curator", enter=true)
   ```
   - Enter は CR (0x0D) として PTY に書き込まれる
   - 承認しないと `server:renga-peers` チャネルが有効化されず、`send_message` の channel push が届かない
   - Claude の boot 速度に差があるためプロンプト未表示の段階で Enter を送信すると no-op になる場合がある。次の list_peers poll で peer 登録が確認できなければ Enter を再送する
2. **list_peers を poll し dispatcher / curator 両方の peer 登録を 1 回で確認** — 両ペインが並列に boot しているので、role ごとに別々の poll を回す必要はなく、1 つの poll loop で両方の登録を同時に待てる:
   ```
   mcp__renga-peers__list_peers
   # 結果から name="dispatcher" / "curator" が両方揃うまで poll
   ```
   - 両方揃わない場合、(a) Enter 未送信のペインに再送、(b) `[pane_not_found]` 等 fatal なら「失敗モード」セクションへ
3. **挨拶メッセージを両方に並列送信**:
   - dispatcher:
     「あなたはディスパッチャーです。窓口からの DELEGATE メッセージを受け取り、ワーカーのペイン起動・指示送信・状態記録を代行してください。CLOSE_PANE メッセージを受けたらペインを閉じてください。」
   - curator:
     「あなたはキュレーターです。 /loop 30m /org-curate を実行してください。知見整理を30分ごとに行います。」
4. **Block B の DB 初期化完了を待つ** — 並列実行の合流点。Block B-1 で `.state/state.db` 不在 → `importer --rebuild` が走った場合、schema 構築完了までは `StateWriter.update_session()` が失敗するので、Block D-5 の DB write 前に Block B が完了している必要がある。Block B が SUSPENDED ブリーフィング待ちで未完了なら、ブリーフィング完了 → DB schema 健全性確認 → ここに進む
5. **DB transaction を 1 回にまとめて identity を記録する** (`StateWriter.transaction()` 経由、markdown 直接編集禁止。post-commit hook が `.state/org-state.md` を再生成)。**両 role 成功なら 4 つすべて書き込む**。**片方が D-2 / D-3 で失敗 (boot 不能で `close_pane` 済み等) なら、成功 role のみ write、失敗 role は `StateWriter.CLEAR` で前回 SUSPENDED から繰り越した stale な `*_pane_id` / `*_peer_id` を明示クリアする** (`StateWriter.update_session()` は None 渡しを「未指定 = 保持」と解釈する契約なので、明示クリアが必須):
   ```bash
   python -c "
   from pathlib import Path
   from tools.state_db import connect
   from tools.state_db.writer import StateWriter
   conn = connect('.state/state.db')
   with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
       # 両者成功時の例（失敗 role は値の代わりに StateWriter.CLEAR を渡す）
       w.update_session(
           dispatcher_pane_id='<d_pane>', dispatcher_peer_id='<d_peer>',
           curator_pane_id='<c_pane>', curator_peer_id='<c_peer>',
       )
   "
   ```
6. JSON スナップショットを 1 回だけ再生成する（dashboard 用、state-db cutover とは別経路。両 identity をまとめて反映できるので 2 回呼ぶ必要はない）:
   `py -3 dashboard/org_state_converter.py`

### 補足: spawn_claude_pane 引数の詳細

両 spawn で共通する引数の意味と落とし穴:

- `target`: 分割対象のペイン。dispatcher は `target="focused"`（窓口ペインを分割）。curator は `target="dispatcher"`（Block A-1 で確立した安定名を解決して dispatcher pane の右半分を取る）
- `direction`: `"horizontal"` = 上下分割（既存ペイン=上 / 新ペイン=下）、`"vertical"` = 左右分割（既存ペイン=左 / 新ペイン=右）
- `role`: `mcp__renga-peers__list_panes` で役割識別できるラベル
- `name`: 後続の `send_message(to_id="dispatcher", ...)` / `close_pane(target="curator")` 等で参照する安定名。**renga-peers は全桁数字の name を id として解釈するので、英字を含む名前を必ず付ける**
- `cwd`: caller ペイン（= 窓口）の cwd を基点に相対解決される。`cd X && claude ...` を `command` に埋める旧方式は禁止（auto-upgrade が発動せず channel push が失われる落とし穴）
- `permission_mode` / `model`: renga が `claude --permission-mode {mode} --model {model} --dangerously-load-development-channels server:renga-peers` を合成して実行する
- 戻り値: `"Spawned pane id=N."` テキスト。エラーは `[<code>] <msg>` 形式（例: `[split_refused]` / `[pane_not_found]` / `[cwd_invalid]`）。code 一覧と分岐は `.claude/skills/org-delegate/references/renga-error-codes.md` を参照
- `.dispatcher/CLAUDE.md` / `.curator/CLAUDE.md` にそれぞれのロール用の指示が入っている（Secretary の CLAUDE.md とは別）

### 失敗モード

並列発火に伴い従来の serial 実行とは異なる失敗パターンが発生する。Block A の spawn 段階で分類する:

- **dispatcher spawn 失敗 (`[split_refused]` / `[cwd_invalid]` / その他 `[<code>]`)** — curator spawn は `target="dispatcher"` の name 解決に失敗するので `[pane_not_found]` で続けて失敗する。**両者の失敗をユーザーに報告し、原因解消後 /org-start を再実行する**。中途半端な状態（curator のみ起動）は発生しない
- **curator spawn 失敗 / dispatcher spawn 成功** — dispatcher は残してユーザーに報告する。組織機能の中核（ワーカー派遣・状態書き込み）は dispatcher 単独で維持されるため、**ユーザーには「dispatcher のみ起動済み。curator を再 spawn するか、curator 無しで暫定継続するか」を選択肢として提示**する。Block B / C / D の dispatcher 関連手順は curator 失敗とは独立に完走させる。**DB write は dispatcher 分のみ書き込み、curator 分は `StateWriter.CLEAR` で明示的にクリアする**（前回 SUSPENDED から繰り越した stale な `curator_pane_id` / `curator_peer_id` が残ると、ダッシュボードと balanced split target 選出が生きた curator がいる前提で誤判断するため。`StateWriter.update_session()` は None 渡しを「未指定 = 保持」と解釈する契約なので、明示クリアが必須）:
  ```python
  from tools.state_db.writer import StateWriter
  ...
  with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
      w.update_session(
          dispatcher_pane_id='<d_pane>', dispatcher_peer_id='<d_peer>',
          curator_pane_id=StateWriter.CLEAR, curator_peer_id=StateWriter.CLEAR,
      )
  ```
- **両 spawn 成功・boot 中に片方が peer 登録されない** — Block D-2 の poll で片方が timeout する。該当ペインに Enter を再送 → 再 poll。3 回 retry してダメなら該当ペインを `close_pane` で破棄して以下のいずれかに分岐する:
  - **curator のみ peer 登録失敗**: 上記「curator spawn 失敗 / dispatcher spawn 成功」と同等の暫定継続を許容する（dispatcher 単独で組織機能の中核は維持される）。Block D-5 の DB write は dispatcher のみ書き込み、curator は `StateWriter.CLEAR`
  - **dispatcher のみ peer 登録失敗**: 暫定継続を許容しない (fatal)。dispatcher 無しでは org-delegate / SECRETARY_RELAY が機能せず、curator 単独で残しても役に立たないため、curator も `close_pane` で閉じ、**両 identity を `StateWriter.CLEAR` で消した上でユーザー報告**し /org-start 再実行を促す
  - **両者とも timeout**: 両ペインを `close_pane`、両 identity を `StateWriter.CLEAR`、ユーザー報告 + 再実行
- **Enter 送信タイミングのずれ** — 「Load development channel?」プロンプト未表示の段階で Enter を送ると no-op になる。Block D-1 で両者並列に送るので片方が早すぎる可能性があるが、Block D-2 の peer 登録 poll が ground truth。peer 未登録なら Block D-1 に戻って再送する

### Stage A / Stage B の wall-clock 効果

| stage | 変更内容 | wall-clock |
|---|---|---|
| 改修前 | 状態復元 → ダッシュボード起動 → dispatcher 起動 (spawn+Enter+poll+greet+DB+snapshot) → curator 起動 (同様) を serial | 〜180s |
| Stage A 後 | dispatcher / curator 起動を 1 つの並列 block にまとめ、両 spawn / Enter / poll / greet / DB write / snapshot を一括化 | 〜90s |
| Stage A+B 後 | 上に加え、Step 0 完了直後に Block A の spawn を発火、Block B (state 復元) / Block C (dashboard 起動) と並列に Claude の boot wait を overlap | 〜35s |

## Step 4: 準備完了の報告

人間に簡潔に報告する。Block D-5 の DB write 内容に応じて、起動済み role を正確に列挙する（curator 失敗時に「キュレーターを起動しました」と虚偽報告しないため）。

**Block C2 の runtime drift 出力の扱い**: Block C2 で `tools/check_runtime_version.py` の stdout に `[runtime drift] ...` の 1 行が出ていれば、下記いずれのテンプレートでも **末尾に空行を 1 つ挟んだ上でその 1 行をそのまま転記する**。stdout が空であれば warning 行は付けない（INSTALLED == LATEST / 未インストール / オフライン / parse 失敗 はすべて silent）。

**前回の状態がある場合 (両 role 成功)**:
```
組織を起動しました。
前回の状態: {サマリー}
ディスパッチャーとキュレーターを起動しました。
何をしますか？
```

**初回起動の場合 (両 role 成功)**:
```
組織を起動しました。
ディスパッチャーとキュレーターを起動しました。
プロジェクトはまだ登録されていません。何をしましょうか？
```

**curator spawn / boot に失敗・dispatcher のみ稼働の場合**:
```
組織を起動しました（一部のみ）。
ディスパッチャーは稼働中ですが、キュレーター起動に失敗しました（理由: {[<code>] / peer 未登録 timeout 等}）。
curator を再 spawn して復旧するか、curator 無しで暫定継続するかを選んでください。
（curator 無しでもワーカー派遣・状態書き込みは可能ですが、知見の自動 curate (/loop 30m /org-curate) は停止します）
```

**drift 検出時の warning 添付例** (上記テンプレートの末尾に転記):
```
...
何をしますか？

[runtime drift] claude-org-runtime: installed=0.1.2 latest=0.1.11 -- ja の pin 内最新です。`python -m pip install --upgrade 'claude-org-runtime'` で更新できます
```

## Appendix: ClaudeCode 起動コマンド（役割別）

Block A / org-delegate Step 3 で使う `spawn_claude_pane` の役割別パラメータ。
renga 0.18.0+ では `mcp__renga-peers__spawn_claude_pane` が役割別の構造化フィールド（`cwd` / `permission_mode` / `model` / `args[]`）を受け取り、`--dangerously-load-development-channels server:renga-peers` を自動付与する。旧方式の `cd X && claude ...` を `spawn_pane` に流し込むパターンは **禁止**（renga の bare-`claude` auto-upgrade が発動せず channel push が届かなくなる落とし穴を再導入するため）。

共通引数:
- `permission_mode`: `auto` リテラル直書き（ディスパッチャー除く）。CLAUDE.md には変数展開機構がないため `registry/org-config.md` の値を実行時に代入することはできない。値を変更する場合は `registry/org-config.md` 冒頭の同期注意セクションを参照
- `cwd`: 各ロール専用ディレクトリへの相対パス（caller pane の cwd 基準で解決される）

> **注**: Secretary は `renga --layout ops` で起動され、`--permission-mode` 未指定のまま動作する（人間判断窓口のため）。`registry/org-config.md` の「Role別の適用範囲」節を参照。

### ディスパッチャー

- `cwd=".dispatcher"`
- `permission_mode="bypassPermissions"`（固定。`default_permission_mode` の影響を受けない）
- `model="sonnet"`

理由: ディスパッチャーはワーカー起動時に `mcp__renga-peers__spawn_claude_pane` を発行する。auto モードの安全分類器はこの「子エージェント起動」を "Create Unsafe Agents" と判定してブロックするため、auto ではワーカー派遣が成立しない。

### キュレーター

- `cwd=".curator"`
- `permission_mode=auto`
- `model="opus"`

### ワーカー（org-delegate の Step 3 で使用）

**`model="opus"` は必須（sonnet 禁止）。**
理由: ワーカーの既定 permission_mode は `auto`（分類器ベース）。この safety classifier は Opus でのみ安定動作する。sonnet だと分類器が誤判定を多発し、承認フローが崩れて作業が詰まる。ディスパッチャーだけは `bypassPermissions` 固定なので分類器を経由せず、sonnet 運用で問題ない（ディスパッチャーを sonnet にしているのはコスト最適化のため、ワーカーには適用しない）。

通常:
- `cwd="{workers_dir}/{task_id}"`（絶対パス推奨）
- `permission_mode=auto`
- `model="opus"`
