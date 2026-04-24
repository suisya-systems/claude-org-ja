# aainc

Allied Architects, Inc. 向けの自己成長する Claude Code 組織。

- **これは何か**: 1つの窓口 Claude と対話するだけで、裏でフォアマン・キュレーター・ワーカーが自動で動き、知見を蓄積しながら改善していくマルチエージェント運用基盤。
- **対象読者**: ccmux と Claude Code でチーム作業を動かしたい開発者・オペレーター。
- **できること**: タスクの自動分解と並列実行、状態の永続化と復元、30 分おきの知見整理、ダッシュボードでの俯瞰。

## 前提条件

### アプリケーション

| 名前 | 用途 | 入手先 |
|---|---|---|
| Claude Code | AI エージェント本体 | https://claude.ai/code |
| ccmux (**0.18.0 以上**) | ターミナルマルチプレクサ (ペイン管理) | `npm install -g ccmux-fork@0.18.0` |
| Git | バージョン管理 | https://git-scm.com/ |
| GitHub CLI (`gh`) | GitHub 操作（PR 作成等） | https://cli.github.com/ |
| Node.js (v18+) | Claude Code CLI の実行環境 | https://nodejs.org/ |
| Python 3.8+ | ダッシュボードサーバー、スクリプト実行 | https://www.python.org/ |
| jq | Hooks スクリプトの JSON 解析 | https://jqlang.github.io/jq/ |

> **OS 注記**: 以下のコマンド例は macOS / Linux (bash) 前提です。Windows では `python3` を `python` に、`~/...` を各自のパスに読み替えてください。

### MCP サーバー

| 名前 | 用途 | 備考 |
|---|---|---|
| ccmux-peers | ccmux のペイン操作・構造化 Claude ペイン起動・ピア通信・event 購読・raw キー送信・スクレイプを提供する 14 種の MCP ツール一式（組織通信の正本） | ccmux に同梱。ツール詳細は [docs/overview-technical.md](docs/overview-technical.md) |

## インストール

```bash
# 1. 依存ツールを導入
#    - Claude Code / gh / Node.js / Python / jq は各公式サイトの手順で導入
#    - ccmux は 0.18.0 以上が必須:
#        npm install -g ccmux-fork@0.18.0

# 2. 認証
gh auth login                   # 未認証なら
claude                          # Claude Code の初回ログインを済ませる

# 3. リポジトリを取得
git clone <このリポジトリの URL>
cd <クローン先>

# 4. ccmux MCP サーバーを Claude Code に登録（初回のみ）
ccmux mcp install
```

`ccmux mcp install` で `mcp__ccmux-peers__*` 系の 14 種のツール（`spawn_claude_pane` / `set_pane_identity` 等を含む）が user-scope 設定に登録され、全ロールから利用可能になります。組織運用 Skill 群はこの MCP サーバーを前提に動作するため、未登録だとフォアマン・ワーカーの起動や相互通信が行えません。

## 起動

窓口 (Secretary) ペインを立ち上げ、組織を起動します:

```bash
ccmux --layout ops              # ccmux-layouts/ops.toml の定義で Secretary ペインが起動
```

Secretary ペインで Claude Code が立ち上がったら、最初に以下を実行します:

```
/org-start
```

これでフォアマンとキュレーターが同一タブ内に派生し、以後は自然言語の依頼を投げるだけで、ワーカーへの派遣と知見整理が自動で回ります。

詳しい使い方は [docs/getting-started.md](docs/getting-started.md) を参照。

## 動作確認

### コマンドライン

```bash
claude --version                # Claude Code のバージョンが出る
ccmux --version                 # 0.18.0 以上
gh auth status                  # "Logged in to github.com" が出る
node --version                  # v18 以上
python3 --version               # 3.8 以上
jq --version                    # 任意のバージョン
claude mcp list                 # 一覧に ccmux-peers が含まれる
```

### 窓口ペイン上の Claude Code

- `mcp__ccmux-peers__list_panes` がエラーなく返る（空応答でも疎通 OK）
- `mcp__ccmux-peers__list_peers` で同タブ内の Claude Code が検出できる
- `/org-start` 実行後、`ccmux` 画面にフォアマンとキュレーターのペインが増えている

互換性プリフライト（ccmux バージョンと 14 種 MCP ツール surface の一括チェック）は `tools/check_ccmux_compat.py` を参照。

## Git Hooks（secret 漏洩防止）

新しくクローンしたら、1 度だけ以下を実行してください:

```bash
bash scripts/install-hooks.sh
```

`core.hooksPath` が `.githooks/` に設定され、以降 `git commit` 直前に `.githooks/pre-commit` が走ります。AWS / GitHub（classic / fine-grained PAT 両対応）/ OpenAI / Anthropic / Google / GitLab / Slack 等の API キー、PEM 秘密鍵、典型的な `API_KEY=...` 代入がステージ差分に含まれると commit は拒否されます（詳細パターンは `.githooks/pre-commit` を参照）。

- **既存の `core.hooksPath` がある環境**: 別パスが設定済みの場合、`scripts/install-hooks.sh` は黙って上書きせずエラー終了します。置き換えて良い場合は `--force` を付けて再実行してください。
- **誤検出の回避**: 該当行に `allow-secret` の文字列を含めて再 stage すると、その行は無視されます（Markdown なら HTML コメント `<!-- allow-secret -->` が読みやすい）。文字列は行頭・行中・行末のどこにあっても有効です。
- **緊急バイパス**: `SKIP_SECRET_SCAN=1 git commit ...`（stderr に警告が出ます）。最後の手段として `git commit --no-verify` も有効ですが通常は使わないでください。
- **ワーカー向け注記**: ワーカー Claude が commit しようとした際、secret を含むと hook がブロックします。対処は人間と同じく `allow-secret` マーカー or `SKIP_SECRET_SCAN=1` です。
- **`.hooks/` との責任境界**: この `.githooks/pre-commit` は **git が `git commit` 直前に起動する** レイヤ。`.hooks/*.sh`（`block-git-push.sh` 等）は **Claude Code が Edit/Write/Bash ツールを呼ぶ前に起動する PreToolUse レイヤ**。対象タイミングが異なるため両者は直交し、併用を前提としています。

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
- **ワーカー**: 実作業を担当。自律的に commit / PR 作成が可能。完了後に知見を記録

全ペインは同一タブ内で動作します（別タブでは監視・指示送信が届かないため、`new_tab` は組織運用では使いません[^newtab]）。

[^newtab]: 背景は upstream の happy-ryo/ccmux#71 を参照。

## スキル一覧

| スキル | 用途 |
|---|---|
| `/org-start` | 組織の起動（起動直後に 1 回実行） |
| `/org-delegate` | 作業の割り当て（自動発動） |
| `/org-suspend` | 作業の中断 |
| `/org-resume` | 作業の再開 |
| `/org-retro` | 委譲プロセスの振り返り |
| `/org-curate` | 知見の整理（自動実行） |
| `/org-dashboard` | ダッシュボード表示 |

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [getting-started.md](docs/getting-started.md) | 使い方ガイド |
| [overview-technical.md](docs/overview-technical.md) | アーキテクチャ・MCP ツール詳細 |
| [verification.md](docs/verification.md) | テスト手順 |
| [design-journal.md](docs/design-journal.md) | 設計経緯と意思決定の記録 |

## 困ったとき

- **`/org-start` しても反応しない** → Secretary ペインの Claude Code がログイン済みか確認（`claude` を叩いて初回認証）。`claude mcp list` に `ccmux-peers` が出ているかも確認。
- **`ccmux-peers` MCP が見えない** → `ccmux mcp status` で現在の登録状態を確認し、未登録なら `ccmux mcp install` を再実行（user-scope 登録なので全ペインに即時反映）。
- **`gh auth status` が Not logged in** → `gh auth login` で GitHub 認証を済ませる。未認証だとワーカーが PR を作れません。
