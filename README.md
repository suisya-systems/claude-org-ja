# aainc

Allied Architects, Inc. 向けの自己成長する Claude Code 組織。

人間は1つの Claude Code（窓口）と対話するだけで、裏側でワーカーが自動的に派遣・管理され、組織が知見を蓄積し改善していく。

## 仕組み

```
人間 <-> 窓口Claude（司令塔）
              |
              +-> フォアマン（ワーカー起動・指示の代行）
              +-> キュレーター（知見整理、30分ごとに自動実行）
              +-> ワーカー群（実作業、完了後に自動消滅）
```

- **窓口**: 人間との唯一の接点。タスク分解・委譲判断・結果報告を担当
- **フォアマン**: ペイン起動・指示送信を代行し、窓口のロックを回避
- **キュレーター**: 蓄積された知見を整理し、スキルやプロセスの改善を提案
- **ワーカー**: 実作業を担当。自律的にcommit/PR作成が可能。完了後に知見を記録

## クイックスタート

```bash
cd ~/path/to/aainc-ops
ccmux --layout ops
```

`ccmux-layouts/ops.toml` で定義された窓口 (Secretary) ペインが立ち上がります。
フォアマン・キュレーター・ワーカーは `/org-start` などのスキル内で
`mcp__ccmux-peers__spawn_claude_pane` を介して同一タブ内に動的に派生します
（別タブに置くと監視・指示送信が通らないため、`new_tab` は使いません。詳細は happy-ryo/ccmux#71）。

詳しくは [docs/getting-started.md](docs/getting-started.md) を参照。

## 前提条件

### アプリケーション

| 名前 | 用途 | 入手先 |
|---|---|---|
| Claude Code | AIエージェント本体 | https://claude.ai/code |
| ccmux | ターミナルマルチプレクサ (ペイン管理) | ローカルビルド または npm (prerelease タグ) |
| Git | バージョン管理 | https://git-scm.com/ |
| GitHub CLI (`gh`) | GitHub操作（PR作成等） | https://cli.github.com/ |
| Node.js (v18+) | Claude Code CLI の実行環境 | https://nodejs.org/ |
| Python 3.8+ | ダッシュボードサーバー、スクリプト実行 | https://www.python.org/ |
| jq | Hooks スクリプトのJSON解析（ワーカー境界チェック等） | https://jqlang.github.io/jq/ |

### MCP サーバー

| 名前 | 用途 | リポジトリ |
|---|---|---|
| ccmux-peers | ccmux ペイン操作 (`spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` / `list_panes` / `new_tab` / `set_pane_identity` 等) と同タブ内 Claude 間双方向メッセージング（組織通信の正本） | ccmux に同梱 (`ccmux mcp install` で登録) |

#### ccmux MCP サーバーの登録

**前提**: ccmux 0.18.0+ （structured `cwd` / `set_pane_identity` / `spawn_claude_pane` を使用）。旧 CLI (`ccmux split` / `close` / `send` / `list` / `focus` / `new-tab`) の大部分は `ccmux-peers` MCP サーバー経由で呼び出せるようになっており、本リポジトリの組織運用 Skill は MCP の利用を前提としています（親 Epic: #20）。

初回セットアップで以下を一度だけ実行してください:

```bash
npm install -g ccmux-fork@0.18.0    # または npm update -g ccmux-fork
ccmux mcp install
```

これにより Claude Code の user-scope 設定に `ccmux-peers` MCP サーバーが登録され、以下 14 種のツールが利用可能になります:

- ペイン制御: `mcp__ccmux-peers__spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` / `list_panes` / `new_tab` / `set_pane_identity`
- PTY / 画面: `inspect_pane` / `send_keys` / `poll_events`
- ピア通信: `send_message` / `list_peers` / `set_summary` / `check_messages`

登録状態は `claude mcp list` で確認できます。

### セットアップ確認

全て導入後、以下で確認:

```bash
claude --version          # Claude Code がインストールされている
ccmux --version           # ccmux がインストールされている
gh auth status            # GitHub CLI が認証済み
node --version            # Node.js v18 以上
python3 --version         # Python 3.8 以上
jq --version              # jq がインストールされている
```

Claude Code 起動後、以下が利用可能であること:
- `ccmux-peers` MCP の `list_panes` が実行できる（空応答でも可。エラーなく返れば疎通成功）
- `ccmux-peers` MCP の `list_peers` が実行できる（同タブ内の他 Claude Code インスタンスが検出できる）

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [getting-started.md](docs/getting-started.md) | 使い方ガイド |
| [verification.md](docs/verification.md) | テスト手順 |
| [design-journal.md](docs/design-journal.md) | 設計経緯と意思決定の記録 |

## スキル一覧

| スキル | 用途 |
|---|---|
| `/org-start` | 組織の起動（起動直後に1回実行） |
| `/org-delegate` | 作業の割り当て（自動発動） |
| `/org-suspend` | 作業の中断 |
| `/org-resume` | 作業の再開 |
| `/org-retro` | 委譲プロセスの振り返り |
| `/org-curate` | 知見の整理（自動実行） |
| `/org-dashboard` | ダッシュボード表示 |

## 旧 ccmux CLI 依存の撤去方針

組織運用 Skill 群は歴史的に旧 ccmux CLI (`ccmux split` / `close` / `send` / `list` / `events` / `inspect`) を Bash 経由で叩いていました。現在 MCP サーバー `ccmux-peers` へ段階的に移行中です（親 Epic: #20）。

| 旧 Bash permission | 撤去タイミング |
|---|---|
| `Bash(ccmux split *)` / `Bash(ccmux close *)` / `Bash(ccmux list *)` | 全 Skill の MCP 化完了後（Issue #30） |
| `Bash(ccmux send *)` | upstream (happy-ryo/ccmux#118) の `send_keys` MCP 対応完了後（Issue #30 内で管理） |
| `Bash(ccmux events *)` | upstream (happy-ryo/ccmux#117) の events MCP 露出完了後 |
| `Bash(ccmux inspect *)` | upstream (happy-ryo/ccmux#116) の `inspect_pane` MCP 追加完了後 |

それまでは CLI と MCP の併用運用になります。新しく書く Skill / ドキュメントは MCP ツールを優先して使ってください。
