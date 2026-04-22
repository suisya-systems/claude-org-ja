---
name: org-start
description: >
  組織を起動する。前回の状態を読み込んでブリーフィングし、
  フォアマンとキュレーターペインを起動する。ClaudeCode起動直後に1回実行する。
  「起動して」「スタート」「始めて」等でも発動。
---

# org-start: 組織の起動

ClaudeCode起動後に最初に実行するスキル。前回の状態復元、フォアマン起動、キュレーター起動を行う。

> **前提**: この Claude は `ccmux --layout ops` で起動された窓口ペイン内で動作している。
> `CCMUX_SOCKET` / `CCMUX_PANE_ID` 環境変数が継承されているので、`mcp__ccmux-peers__*` MCP
> ツール 12 種（`spawn_pane` / `close_pane` / `focus_pane` / `list_panes` / `new_tab` /
> `send_message` / `list_peers` / `set_summary` / `check_messages` / `inspect_pane` /
> `poll_events` / `send_keys`）で同タブ内のペイン操作・ピア通信・画面スクレイプ・lifecycle
> event 購読・raw キー入力まですべてカバーできる（ccmux 0.14.0+ 前提）。

## Step 0: 初期化

1. `mcp__ccmux-peers__set_summary` で自分のサマリーを設定する: 「Secretary: 窓口」
   - ワーカー / フォアマン / キュレーターが `mcp__ccmux-peers__list_peers` で窓口を発見するために必須
2. `ccmux-peers` MCP の疎通確認: `mcp__ccmux-peers__list_panes` を呼び出す。
   - エラーなく応答が返れば MCP 有効。以降 ccmux-peers MCP ツールが使える前提で進む
   - エラーが返る / ツール未登録の場合はユーザーに `ccmux mcp install` の実行を促し、
     Skill の実行を一時停止する（MCP 導入後にやり直してもらう）。詳細は README の
     「ccmux MCP サーバーの登録」セクション参照
3. `registry/org-config.md` の `workers_dir` を読み、ワーカーディレクトリの存在を確認する。
   存在するディレクトリがあれば一覧をユーザーに報告する（削除は絶対にしない）。
   **禁止事項**: ワーカーディレクトリは過去の作業成果や再利用可能なプロジェクトを含むため、
   org-start 時に削除してはならない。org-delegate のディレクトリ保持ポリシーに従うこと。

## Step 1: 前回の状態確認

1. `.state/org-state.md` が存在するか確認する
2. 存在する場合:
   - ファイルを読み、Status を確認する
   - Status が `SUSPENDED` なら /org-resume の Phase 1〜3（ブリーフィング・照合・再開計画）を実行する。
     その後 Step 2 以降に進み、フォアマン・キュレーターを起動してから、org-resume の Phase 4（ワーカー再派遣）を人間の承認に基づいて実行する
   - Status が `ACTIVE` なら、前回のセッションが突然終了した可能性がある。
     各ワーカーディレクトリの git 状態を確認し、現状を報告する
3. 存在しない場合:
   - 初回起動と判断する

## ClaudeCode 起動コマンド（役割別）

全インスタンス共通:
- `--permission-mode {mode}`: registry/org-config.md の default_permission_mode の値を使用（フォアマン除く）

ccmux 0.14.0+ は `mcp__ccmux-peers__spawn_pane` / `new_tab` 経由で起動した bare `claude`（および `claude <args>`）を自動的に Alt+P 相当へ upgrade し、`server:ccmux-peers` チャネルを注入する。したがって起動コマンドに `--dangerously-load-development-channels` を明示する必要はない。ccmux 側の auto-bootstrap に委ねる（#46）。

### フォアマン
```
claude --permission-mode bypassPermissions --model sonnet
```

フォアマンは常に `bypassPermissions` 固定。`default_permission_mode` の影響を受けない。
理由: フォアマンはワーカー起動時に `mcp__ccmux-peers__spawn_pane` の `command` 引数へ `"claude ..."` を渡す。auto モードの安全分類器はこの「子エージェント起動コマンド列」を "Create Unsafe Agents" と判定してブロックするため、auto ではワーカー派遣が成立しない（この挙動は旧 `ccmux split --command` でも同じ）。

### キュレーター
```
claude --permission-mode {default_permission_mode}
```

### ワーカー（org-delegate の Step 3 で使用）

Planモード不要の場合（通常）:
```
claude --permission-mode {default_permission_mode}
```

Planモード要の場合（org-config の値を上書き）:
```
claude --permission-mode plan
```

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

## Step 2: フォアマンペイン起動

ペイン配置は org-delegate/references/pane-layout.md に従う (ccmux 版)。

1. `mcp__ccmux-peers__spawn_pane` で窓口ペインを上下分割し、下半分にフォアマン用の Claude を起動する:
   ```
   mcp__ccmux-peers__spawn_pane(
     target="focused",
     direction="horizontal",
     role="foreman",
     name="foreman",
     command="cd .foreman && claude --permission-mode bypassPermissions --model sonnet"
   )
   ```
   - `target="focused"`: 現在フォーカスされている窓口ペインを分割（省略可。省略時は focused）
   - `direction="horizontal"` = 上下分割（窓口=上 / フォアマン=下）
   - `role="foreman"`: `mcp__ccmux-peers__list_panes` で役割識別できるようにラベル付与
   - `name="foreman"`: 後続の `mcp__ccmux-peers__send_message(to_id="foreman", ...)` や `close_pane(target="foreman")` で宛先指定するための安定名。**ccmux-peers は全桁数字の name を id として解釈するので、英字を含む名前を必ず付ける**
   - `command` は新しいペインでシェルプロンプトが立ち上がった直後に注入される。bare `claude` は ccmux が Alt+P 形式に auto-upgrade するため `--dangerously-load-development-channels` を明示しなくて良い（#46）
   - `.foreman/CLAUDE.md` にフォアマン用の役割指示がある（Secretary の CLAUDE.md とは別）
   - 戻り値: `"Spawned pane id=N."` のテキスト。以降のペイン操作では `name="foreman"` で参照する
   - エラーは `[<code>] <msg>` 形式のテキストで返却される（例: `[split_refused]` / `[pane_not_found]`）。code 一覧と分岐は `.claude/skills/org-delegate/references/ccmux-error-codes.md` を参照
2. `mcp__ccmux-peers__list_peers` で新しいピアが現れるのを待つ
3. `mcp__ccmux-peers__send_message` でフォアマンに以下を送信する:
   「あなたはフォアマンです。窓口からの DELEGATE メッセージを受け取り、ワーカーのペイン起動・指示送信・状態記録を代行してください。CLOSE_PANE メッセージを受けたらペインを閉じてください。」
4. フォアマンのピアIDと ccmux ペイン名（`foreman`）を記録する（org-state.md の Foreman セクション）
5. JSON スナップショットを再生成する:
   `py -3 dashboard/org_state_converter.py`

## Step 3: キュレーターペイン起動

1. `mcp__ccmux-peers__spawn_pane` でフォアマンペインの右半分をキュレーター用に立ち上げる:
   ```
   mcp__ccmux-peers__spawn_pane(
     target="foreman",
     direction="vertical",
     role="curator",
     name="curator",
     command="cd .curator && claude --permission-mode {default_permission_mode}"
   )
   ```
   - `target="foreman"`: Step 2 で命名したフォアマンペインを分割対象に指定
   - `direction="vertical"` = 左右分割（フォアマン=左 / キュレーター=右）
   - `name="curator"`: 安定名（英字を含む、全桁数字禁止）
   - bare `claude` の auto-upgrade で `server:ccmux-peers` が注入される（#46）
   - `.curator/CLAUDE.md` にキュレーター用の役割指示がある
   - エラーは Step 2 と同様の `[<code>] <msg>` 形式
2. `mcp__ccmux-peers__list_peers` で新しいピアが現れるのを待つ
3. `mcp__ccmux-peers__send_message` でキュレーターに以下を送信する:
   「あなたはキュレーターです。 /loop 30m /org-curate を実行してください。知見整理を30分ごとに行います。」
4. キュレーターのピアIDと ccmux ペイン名（`curator`）を記録する（org-state.md の Curator セクション）
5. JSON スナップショットを再生成する:
   `py -3 dashboard/org_state_converter.py`

## Step 4: 準備完了の報告

人間に簡潔に報告する:

**前回の状態がある場合**:
```
組織を起動しました。
前回の状態: {サマリー}
フォアマンとキュレーターを起動しました。
何をしますか？
```

**初回起動の場合**:
```
組織を起動しました。
フォアマンとキュレーターを起動しました。
プロジェクトはまだ登録されていません。何をしましょうか？
```
