---
name: org-start
description: >
  組織を起動する。前回の状態を読み込んでブリーフィングし、
  ディスパッチャーとキュレーターペインを起動する。ClaudeCode起動直後に1回実行する。
  「起動して」「スタート」「始めて」等でも発動。
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
> **state DB 前提 (Issue #267 / M2.1, #272)**: `.state/state.db` が SoT。read 経路は
> DB primary + markdown fallback、構造化セクション (Status / Dispatcher / Curator /
> Worker Directory Registry / Active Work Items) の write 経路は `StateWriter.transaction()`
> 経由（post-commit hook が `.state/org-state.md` / `.state/journal.jsonl` を自動再生成）。
> DB が古い場合は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で再構築する。

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
     2. 成功すれば警告ログを `journal.jsonl` に残して続行（`bash tools/journal_append.sh secretary_identity_restored note=auto_recovered`）
     3. 失敗ケースの分岐:
        - `name_in_use` エラー: 既存の別ペインが `secretary` を占有している。ユーザーに状況を報告し、「現セッション継続なら全ワーカーに `to_id="{numeric_pane_id}"` で送信させる」「永続修復なら `/org-suspend` → 終了 → `renga --layout ops` で再起動」の選択肢を提示
        - `name_invalid` / その他: ユーザーに原因を報告
   - **一致している場合**: そのまま続行
4. `registry/org-config.md` の `workers_dir` を読み、ワーカーディレクトリの存在を確認する。
   存在するディレクトリがあれば一覧をユーザーに報告する（削除は絶対にしない）。
   **禁止事項**: ワーカーディレクトリは過去の作業成果や再利用可能なプロジェクトを含むため、
   org-start 時に削除してはならない。org-delegate のディレクトリ保持ポリシーに従うこと。

## Step 1: 前回の状態確認

read 経路は **DB primary + markdown fallback**（Issue #267 / M1）。

1. `.state/state.db` が存在するか確認する
   - 存在 + 新しい (= `.state/state.db` の mtime が `.state/org-state.md` /
     `.state/journal.jsonl` / `registry/projects.md` の最新 mtime 以上。
     WAL 利用時は `.state/state.db-wal` の mtime も含めて max を取る) → DB を read primary として使う:
     ```bash
     python -c "from tools.state_db import connect; from tools.state_db.queries import get_org_state_summary; import json; \
       conn = connect('.state/state.db'); \
       print(json.dumps(get_org_state_summary(conn), ensure_ascii=False, indent=2, default=str))"
     ```
     `active_runs` / `recent_events` / `run_status_counts` で前回状態を把握する
   - 存在するが上記いずれかの markdown SoT ファイルの mtime より古い（stale）→ 警告を表示し、Secretary に rebuild を促す:
     「state.db is stale (markdown is newer). Run: `python -m tools.state_db.importer --db .state/state.db --root . --rebuild`」
     rebuild するまでは markdown fallback で続行する
   - 存在しない → markdown fallback (下記 step 2 へ)
2. `.state/org-state.md` を読み、Status を確認する（DB は active runs を返すが Status / Current Objective は markdown SoT）
   - Status が `SUSPENDED` なら /org-resume の Phase 1〜3（ブリーフィング・照合・再開計画）を実行する。
     その後 Step 2 以降に進み、ディスパッチャー・キュレーターを起動してから、org-resume の Phase 4（ワーカー再派遣）を人間の承認に基づいて実行する
   - Status が `ACTIVE` なら、前回のセッションが突然終了した可能性がある。
     各ワーカーディレクトリの git 状態を確認し、現状を報告する
3. `.state/org-state.md` も `.state/state.db` も存在しない場合:
   - 初回起動と判断する

## ClaudeCode 起動コマンド（役割別）

renga 0.18.0+ では `mcp__renga-peers__spawn_claude_pane` が役割別の構造化フィールド（`cwd` / `permission_mode` / `model` / `args[]`）を受け取り、`--dangerously-load-development-channels server:renga-peers` を自動付与する。旧方式の `cd X && claude ...` を `spawn_pane` に流し込むパターンは **禁止**（renga の bare-`claude` auto-upgrade が発動せず channel push が届かなくなる落とし穴を再導入するため）。

共通引数:
- `permission_mode`: registry/org-config.md の default_permission_mode の値を使用（ディスパッチャー除く）
- `cwd`: 各ロール専用ディレクトリへの相対パス（caller pane の cwd 基準で解決される）

> **注**: Secretary は `renga --layout ops` で起動され、`--permission-mode` 未指定のまま動作する（人間判断窓口のため）。`registry/org-config.md` の「Role別の適用範囲」節を参照。

### ディスパッチャー

- `cwd=".dispatcher"`
- `permission_mode="bypassPermissions"`（固定。`default_permission_mode` の影響を受けない）
- `model="sonnet"`

理由: ディスパッチャーはワーカー起動時に `mcp__renga-peers__spawn_claude_pane` を発行する。auto モードの安全分類器はこの「子エージェント起動」を "Create Unsafe Agents" と判定してブロックするため、auto ではワーカー派遣が成立しない。

### キュレーター

- `cwd=".curator"`
- `permission_mode={default_permission_mode}`
- `model="opus"`

### ワーカー（org-delegate の Step 3 で使用）

**`model="opus"` は必須（sonnet 禁止）。**
理由: ワーカーの既定 permission_mode は `auto`（分類器ベース）。この safety classifier は Opus でのみ安定動作する。sonnet だと分類器が誤判定を多発し、承認フローが崩れて作業が詰まる。ディスパッチャーだけは `bypassPermissions` 固定なので分類器を経由せず、sonnet 運用で問題ない（ディスパッチャーを sonnet にしているのはコスト最適化のため、ワーカーには適用しない）。

通常:
- `cwd="{workers_dir}/{task_id}"`（絶対パス推奨）
- `permission_mode={default_permission_mode}`
- `model="opus"`

## Step 1.5: ダッシュボードサーバー起動

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

## Step 2: ディスパッチャーペイン起動

ペイン配置は org-delegate/references/pane-layout.md に従う (renga 版)。

1. `mcp__renga-peers__spawn_claude_pane` で窓口ペインを上下分割し、下半分にディスパッチャー用の Claude を起動する:
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
   - `target="focused"`: 現在フォーカスされている窓口ペインを分割（省略可。省略時は focused）
   - `direction="horizontal"` = 上下分割（窓口=上 / ディスパッチャー=下）
   - `role="dispatcher"`: `mcp__renga-peers__list_panes` で役割識別できるようにラベル付与
   - `name="dispatcher"`: 後続の `mcp__renga-peers__send_message(to_id="dispatcher", ...)` や `close_pane(target="dispatcher")` で宛先指定するための安定名。**renga-peers は全桁数字の name を id として解釈するので、英字を含む名前を必ず付ける**
   - `cwd=".dispatcher"`: caller ペイン（= 窓口）の cwd を基点に `.dispatcher/` へ解決される。`cd X && claude ...` を `command` に埋める旧方式は禁止（auto-upgrade が発動せず channel push が失われる落とし穴）
   - `permission_mode="bypassPermissions"` / `model="sonnet"`: renga が `claude --permission-mode bypassPermissions --model sonnet --dangerously-load-development-channels server:renga-peers` を合成して実行する
   - `.dispatcher/CLAUDE.md` にディスパッチャー用の役割指示がある（Secretary の CLAUDE.md とは別）
   - 戻り値: `"Spawned pane id=N."` のテキスト。以降のペイン操作では `name="dispatcher"` で参照する
   - エラーは `[<code>] <msg>` 形式のテキストで返却される（例: `[split_refused]` / `[pane_not_found]` / `[cwd_invalid]`）。code 一覧と分岐は `.claude/skills/org-delegate/references/renga-error-codes.md` を参照
2. Claude Code 初回起動時に「Load development channel? (Y/n)」確認プロンプトが表示される。`mcp__renga-peers__send_keys` で Enter を送信して承認する:
   ```
   mcp__renga-peers__send_keys(target="dispatcher", enter=true)
   ```
   - Enter は CR (0x0D) として PTY に書き込まれる
   - 承認しないと `server:renga-peers` チャネルが有効化されず、`send_message` の channel push が届かない。Step 3 の `list_peers` 待ちもタイムアウトする
3. `mcp__renga-peers__list_peers` で新しいピアが現れるのを待つ
4. `mcp__renga-peers__send_message` でディスパッチャーに以下を送信する:
   「あなたはディスパッチャーです。窓口からの DELEGATE メッセージを受け取り、ワーカーのペイン起動・指示送信・状態記録を代行してください。CLOSE_PANE メッセージを受けたらペインを閉じてください。」
5. **DB 経由で Dispatcher identity を記録する**（`.state/org-state.md` 直接編集禁止。post-commit hook が再生成）:
   ```bash
   python -c "from pathlib import Path; from tools.state_db import connect; from tools.state_db.writer import StateWriter; from tools.state_db.snapshotter import post_commit_regenerate; conn=connect('.state/state.db'); w=StateWriter(conn, claude_org_root=Path('.')); w.begin(); w.update_session(dispatcher_pane_id='<pane_id>', dispatcher_peer_id='<peer_id>'); w.commit(); post_commit_regenerate(w.conn, Path('.')); conn.close()"
   ```
6. JSON スナップショットを再生成する（dashboard 用、state-db cutover とは別経路）:
   `py -3 dashboard/org_state_converter.py`

## Step 3: キュレーターペイン起動

1. `mcp__renga-peers__spawn_claude_pane` でディスパッチャーペインの右半分をキュレーター用に立ち上げる:
   ```
   mcp__renga-peers__spawn_claude_pane(
     target="dispatcher",
     direction="vertical",
     role="curator",
     name="curator",
     cwd=".curator",
     permission_mode="{default_permission_mode}",
     model="opus"
   )
   ```
   - `target="dispatcher"`: Step 2 で命名したディスパッチャーペインを分割対象に指定
   - `direction="vertical"` = 左右分割（ディスパッチャー=左 / キュレーター=右）
   - `name="curator"`: 安定名（英字を含む、全桁数字禁止）
   - `cwd=".curator"`: caller ペイン（= 窓口）の cwd 基点で `.curator/` 解決
   - `.curator/CLAUDE.md` にキュレーター用の役割指示がある
   - エラーは Step 2 と同様の `[<code>] <msg>` 形式
2. Step 2 と同様に「Load development channel?」確認プロンプトを Enter で承認する:
   ```
   mcp__renga-peers__send_keys(target="curator", enter=true)
   ```
3. `mcp__renga-peers__list_peers` で新しいピアが現れるのを待つ
4. `mcp__renga-peers__send_message` でキュレーターに以下を送信する:
   「あなたはキュレーターです。 /loop 30m /org-curate を実行してください。知見整理を30分ごとに行います。」
5. **DB 経由で Curator identity を記録する**（直接編集禁止。post-commit hook が再生成）:
   ```bash
   python -c "from pathlib import Path; from tools.state_db import connect; from tools.state_db.writer import StateWriter; from tools.state_db.snapshotter import post_commit_regenerate; conn=connect('.state/state.db'); w=StateWriter(conn, claude_org_root=Path('.')); w.begin(); w.update_session(curator_pane_id='<pane_id>', curator_peer_id='<peer_id>'); w.commit(); post_commit_regenerate(w.conn, Path('.')); conn.close()"
   ```
6. JSON スナップショットを再生成する（dashboard 用、state-db cutover とは別経路）:
   `py -3 dashboard/org_state_converter.py`

## Step 4: 準備完了の報告

人間に簡潔に報告する:

**前回の状態がある場合**:
```
組織を起動しました。
前回の状態: {サマリー}
ディスパッチャーとキュレーターを起動しました。
何をしますか？
```

**初回起動の場合**:
```
組織を起動しました。
ディスパッチャーとキュレーターを起動しました。
プロジェクトはまだ登録されていません。何をしましょうか？
```
