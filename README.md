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
git clone https://github.com/happy-ryo/aainc-wezterm.git
cd aainc
# Claude Code を起動して /org-start を実行
```

詳しくは [docs/getting-started.md](docs/getting-started.md) を参照。

## 前提条件

### アプリケーション

| 名前 | 用途 | 入手先 |
|---|---|---|
| Claude Code | AIエージェント本体 | https://claude.ai/code |
| WezTerm | ターミナル（ペイン分割でマルチエージェント管理） | https://wezfurlong.org/wezterm/ |
| Git | バージョン管理 | https://git-scm.com/ |
| GitHub CLI (`gh`) | GitHub操作（PR作成等） | https://cli.github.com/ |
| Node.js (v18+) | Claude Code CLI の実行環境 | https://nodejs.org/ |
| Python 3.8+ | ダッシュボードサーバー、スクリプト実行 | https://www.python.org/ |
| jq | Hooks スクリプトのJSON解析（ワーカー境界チェック等） | https://jqlang.github.io/jq/ |

### Claude Code プラグイン

| 名前 | 用途 | インストール |
|---|---|---|
| wezterm-panes | WezTermのペイン開閉・管理 | https://github.com/happy-ryo/wezterm-panes-plugin |

### MCP サーバー

| 名前 | 用途 | リポジトリ |
|---|---|---|
| claude-peers | Claude Code インスタンス間のメッセージング | https://github.com/happy-ryo/claude-peers-mcp |

### セットアップ確認

全て導入後、以下で確認:

```bash
claude --version          # Claude Code がインストールされている
wezterm --version         # WezTerm がインストールされている
gh auth status            # GitHub CLI が認証済み
node --version            # Node.js v18 以上
python3 --version         # Python 3.8 以上
jq --version              # jq がインストールされている
```

Claude Code 起動後、以下が利用可能であること:
- `/wezterm-panes` スキルが認識される
- `claude-peers` MCP の `list_peers` が実行できる

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
