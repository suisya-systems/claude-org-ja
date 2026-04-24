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
> ツール 14 種（`spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` /
> `list_panes` / `new_tab` / `send_message` / `list_peers` / `set_summary` /
> `check_messages` / `inspect_pane` / `poll_events` / `send_keys` /
> `set_pane_identity`）で同タブ内のペイン操作・ピア通信・画面スクレイプ・lifecycle
> event 購読・raw キー入力まですべてカバーできる（**ccmux 0.18.0+ 前提**）。

## Step 0: 初期化

1. `mcp__ccmux-peers__set_summary` で自分のサマリーを設定する: 「Secretary: 窓口」
   - ワーカー / フォアマン / キュレーターが `mcp__ccmux-peers__list_peers` で窓口を発見するために必須
2. `ccmux-peers` MCP の疎通確認: `mcp__ccmux-peers__list_panes` を呼び出す。
   - エラーなく応答が返れば MCP 有効。以降 ccmux-peers MCP ツールが使える前提で進む
   - エラーが返る / ツール未登録の場合はユーザーに `ccmux mcp install` の実行を促し、
     Skill の実行を一時停止する（MCP 導入後にやり直してもらう）。詳細は README の
     「インストール」セクション参照
3. **secretary ペイン identity の検証と自動リカバリ**（Issue #59 対応）:
   - `mcp__ccmux-peers__list_panes` の結果から `focused=true` のペイン（= 自分）を特定する
   - 期待値: `name == "secretary"` かつ `role == "secretary"`
   - **不一致の場合** — `ccmux --layout ops` 以外の経路で起動された / 旧ops.tomlで起動された既存セッションに attach 等:
     1. `mcp__ccmux-peers__set_pane_identity(target="focused", name="secretary", role="secretary")` を呼んで自動修復
     2. 成功すれば警告ログ（`journal.jsonl` に `{"event":"secretary_identity_restored"}`）を残して続行
     3. 失敗ケースの分岐:
        - `name_in_use` エラー: 既存の別ペインが `secretary` を占有している。ユーザーに状況を報告し、「現セッション継続なら全ワーカーに `to_id="{numeric_pane_id}"` で送信させる」「永続修復なら `/org-suspend` → 終了 → `ccmux --layout ops` で再起動」の選択肢を提示
        - `name_invalid` / その他: ユーザーに原因を報告
   - **一致している場合**: そのまま続行
4. `registry/org-config.md` の `workers_dir` を読み、ワーカーディレクトリの存在を確認する。
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

ccmux 0.18.0+ では `mcp__ccmux-peers__spawn_claude_pane` が役割別の構造化フィールド（`cwd` / `permission_mode` / `model` / `args[]`）を受け取り、`--dangerously-load-development-channels server:ccmux-peers` を自動付与する。旧方式の `cd X && claude ...` を `spawn_pane` に流し込むパターンは **禁止**（ccmux の bare-`claude` auto-upgrade が発動せず channel push が届かなくなる落とし穴を再導入するため）。

共通引数:
- `permission_mode`: registry/org-config.md の default_permission_mode の値を使用（フォアマン除く）
- `cwd`: 各ロール専用ディレクトリへの相対パス（caller pane の cwd 基準で解決される）

### フォアマン

- `cwd=".foreman"`
- `permission_mode="bypassPermissions"`（固定。`default_permission_mode` の影響を受けない）
- `model="sonnet"`

理由: フォアマンはワーカー起動時に `mcp__ccmux-peers__spawn_claude_pane` を発行する。auto モードの安全分類器はこの「子エージェント起動」を "Create Unsafe Agents" と判定してブロックするため、auto ではワーカー派遣が成立しない。

### キュレーター

- `cwd=".curator"`
- `permission_mode={default_permission_mode}`
- `model="opus"`

### ワーカー（org-delegate の Step 3 で使用）

**`model="opus"` は必須（sonnet 禁止）。**
理由: ワーカーの既定 permission_mode は `auto`（分類器ベース）。この safety classifier は Opus でのみ安定動作する。sonnet だと分類器が誤判定を多発し、承認フローが崩れて作業が詰まる。フォアマンだけは `bypassPermissions` 固定なので分類器を経由せず、sonnet 運用で問題ない（フォアマンを sonnet にしているのはコスト最適化のため、ワーカーには適用しない）。

Planモード不要の場合（通常）:
- `cwd="{workers_dir}/{task_id}"`（絶対パス推奨）
- `permission_mode={default_permission_mode}`
- `model="opus"`

Planモード要の場合（org-config の値を上書き）:
- `cwd="{workers_dir}/{task_id}"`
- `permission_mode="plan"`
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

## Step 2: フォアマンペイン起動

ペイン配置は org-delegate/references/pane-layout.md に従う (ccmux 版)。

1. `mcp__ccmux-peers__spawn_claude_pane` で窓口ペインを上下分割し、下半分にフォアマン用の Claude を起動する:
   ```
   mcp__ccmux-peers__spawn_claude_pane(
     target="focused",
     direction="horizontal",
     role="foreman",
     name="foreman",
     cwd=".foreman",
     permission_mode="bypassPermissions",
     model="sonnet"
   )
   ```
   - `target="focused"`: 現在フォーカスされている窓口ペインを分割（省略可。省略時は focused）
   - `direction="horizontal"` = 上下分割（窓口=上 / フォアマン=下）
   - `role="foreman"`: `mcp__ccmux-peers__list_panes` で役割識別できるようにラベル付与
   - `name="foreman"`: 後続の `mcp__ccmux-peers__send_message(to_id="foreman", ...)` や `close_pane(target="foreman")` で宛先指定するための安定名。**ccmux-peers は全桁数字の name を id として解釈するので、英字を含む名前を必ず付ける**
   - `cwd=".foreman"`: caller ペイン（= 窓口）の cwd を基点に `.foreman/` へ解決される。`cd X && claude ...` を `command` に埋める旧方式は禁止（auto-upgrade が発動せず channel push が失われる落とし穴）
   - `permission_mode="bypassPermissions"` / `model="sonnet"`: ccmux が `claude --permission-mode bypassPermissions --model sonnet --dangerously-load-development-channels server:ccmux-peers` を合成して実行する
   - `.foreman/CLAUDE.md` にフォアマン用の役割指示がある（Secretary の CLAUDE.md とは別）
   - 戻り値: `"Spawned pane id=N."` のテキスト。以降のペイン操作では `name="foreman"` で参照する
   - エラーは `[<code>] <msg>` 形式のテキストで返却される（例: `[split_refused]` / `[pane_not_found]` / `[cwd_invalid]`）。code 一覧と分岐は `.claude/skills/org-delegate/references/ccmux-error-codes.md` を参照
2. Claude Code 初回起動時に「Load development channel? (Y/n)」確認プロンプトが表示される。`mcp__ccmux-peers__send_keys` で Enter を送信して承認する:
   ```
   mcp__ccmux-peers__send_keys(target="foreman", enter=true)
   ```
   - Enter は CR (0x0D) として PTY に書き込まれる
   - 承認しないと `server:ccmux-peers` チャネルが有効化されず、`send_message` の channel push が届かない。Step 3 の `list_peers` 待ちもタイムアウトする
3. `mcp__ccmux-peers__list_peers` で新しいピアが現れるのを待つ
4. `mcp__ccmux-peers__send_message` でフォアマンに以下を送信する:
   「あなたはフォアマンです。窓口からの DELEGATE メッセージを受け取り、ワーカーのペイン起動・指示送信・状態記録を代行してください。CLOSE_PANE メッセージを受けたらペインを閉じてください。」
5. フォアマンのピアIDと ccmux ペイン名（`foreman`）を記録する（org-state.md の Foreman セクション）
6. JSON スナップショットを再生成する:
   `py -3 dashboard/org_state_converter.py`

## Step 3: キュレーターペイン起動

1. `mcp__ccmux-peers__spawn_claude_pane` でフォアマンペインの右半分をキュレーター用に立ち上げる:
   ```
   mcp__ccmux-peers__spawn_claude_pane(
     target="foreman",
     direction="vertical",
     role="curator",
     name="curator",
     cwd=".curator",
     permission_mode="{default_permission_mode}",
     model="opus"
   )
   ```
   - `target="foreman"`: Step 2 で命名したフォアマンペインを分割対象に指定
   - `direction="vertical"` = 左右分割（フォアマン=左 / キュレーター=右）
   - `name="curator"`: 安定名（英字を含む、全桁数字禁止）
   - `cwd=".curator"`: caller ペイン（= 窓口）の cwd 基点で `.curator/` 解決
   - `.curator/CLAUDE.md` にキュレーター用の役割指示がある
   - エラーは Step 2 と同様の `[<code>] <msg>` 形式
2. Step 2 と同様に「Load development channel?」確認プロンプトを Enter で承認する:
   ```
   mcp__ccmux-peers__send_keys(target="curator", enter=true)
   ```
3. `mcp__ccmux-peers__list_peers` で新しいピアが現れるのを待つ
4. `mcp__ccmux-peers__send_message` でキュレーターに以下を送信する:
   「あなたはキュレーターです。 /loop 30m /org-curate を実行してください。知見整理を30分ごとに行います。」
5. キュレーターのピアIDと ccmux ペイン名（`curator`）を記録する（org-state.md の Curator セクション）
6. JSON スナップショットを再生成する:
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
