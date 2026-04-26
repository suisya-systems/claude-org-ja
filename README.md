# claude-org-ja

<!-- TODO(#110): cross-language link to English sibling `suisya-systems/claude-org` once that repo exists. -->

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/happy-ryo/claude-org/actions/workflows/tests.yml/badge.svg)](https://github.com/happy-ryo/claude-org/actions/workflows/tests.yml)
<!-- TODO(#106): install one-liner badge once installer URL is decided. -->

> **claude-org-ja は日本語ファースト reference distribution。**
> 英語版 sibling `claude-org` は別リポジトリで並走予定（dual-track、`suisya-systems` Org 配下）。

---

## 30 秒ピッチ

**問題**: Claude Code を「窓口 1 つ + ワーカー多数」で運用したい。だが Claude Code は単独セッション前提で、複数インスタンスを安全に協調させる運用層は公式には用意されていない。tmux 風の素朴な分割や farm 系のフルオート並列では、許可境界・知見蓄積・状態復元・per-task 環境構築といった**運用 discipline** が抜け落ちる。

**ソリューション**: claude-org-ja は Claude Code 専用の **operational discipline framework**。1 つの窓口 Claude と対話するだけで、フォアマン・キュレーター・ワーカーが裏で自動的に派生し、permission narrow allow + per-task `WORKER_DIR` 境界 + 30 分おきの自動知見整理 + 状態の suspend/resume を**最初から強制**する。

**対象読者**: Claude Code を業務で長時間回したい開発者・オペレーターのうち、「fully autonomous より explicit な許可境界が欲しい」「3〜5 worker を quality 重視で動かしたい」「知見の自己成長ループを回したい」層。

---

## 4-Layer Architecture

claude-org-ja は 4-Layer スタックの **Layer 4** に位置する reference distribution。Layer 3 (terminal multiplexer = `renga`) を依存先として持つ。

```
Layer 4: claude-org-ja  ← このリポジトリ（運用 discipline FW）
Layer 3: renga          ← suisya-systems/renga（ペイン管理 + MCP）
Layer 2: org-runtime    ← 抽出予定
Layer 1: core-harness   ← 抽出予定
```

<!-- TODO(#108): 4-Layer architecture diagram (diagram: 実装中). -->

**Layer 3 + Layer 4 は同時公開**します（B-3 決定）。Layer 3 (`renga`) は単体で AI 開発以外でも使える terminal multiplexer + MCP サーバーで、Layer 4 (`claude-org-ja`) はその上に組織運用 discipline を載せた reference distribution。

---

## Quickstart

<!-- TODO(#106): install one-liner. 現状は git clone + 手動 bootstrap。one-liner 実装は #106 で対応予定。 -->

> **OS 注記**: 以下は macOS / Linux (bash) 前提。Windows でも `python3` を `python` に読み替えれば動作します（PowerShell 版 one-liner も #106 で対応予定）。

```bash
# 1. 依存ツールを導入
#    Claude Code (https://claude.ai/code), gh, Node.js v18+, Python 3.8+, jq を各公式手順で
#    renga (Layer 3) は 0.18.0 以上が必須:
npm install -g @suisya-systems/renga@0.18.0

# 2. 認証
gh auth login
claude                          # Claude Code 初回ログイン

# 3. このリポジトリを取得
git clone https://github.com/happy-ryo/claude-org.git
cd claude-org

# 4. renga MCP サーバーを Claude Code に登録（初回のみ）
renga mcp install

# 5. Secretary ペインを起動
renga --layout ops
```

Secretary ペインで Claude Code が立ち上がったら、**初回のみ** `/org-setup` を実行してロール別の許可・hook 設定を配置:

```
/org-setup
```

続いて組織を起動:

```
/org-start
```

これでフォアマンとキュレーターが派生し、以後は自然言語で依頼するだけ。詳細は [docs/getting-started.md](docs/getting-started.md) を参照。

---

## なぜこれを使うか（vs 既存ツール）

| 比較対象 | 立ち位置 | claude-org-ja との違い |
|---|---|---|
| **Claude Code Subagents / Agent Teams（公式）** | Anthropic 公式の lead/teammate 階層 + auto memory + hooks | claude-org-ja は公式の上に乗る運用層。**競合せず共存**。Subagents が提供しない「per-task `WORKER_DIR` 強制」「schema-driven 設定 drift CI」「raw → curated knowledge pipeline」「30 分自動 curation loop」を足す |
| **ccswarm**（Rust 製 multiplexer-free coordinator） | 固定 role pool（Frontend/Backend/QA agent 等）+ 大規模並列指向 | claude-org-ja は **per-task で worker_dir / CLAUDE.md を都度生成**（固定 role pool は採用しない）。3〜5 worker / quality 重視（farm 思想と方向が逆） |
| **Aider / aider-codex / Cursor agents** | エディタ統合の単独エージェント or LLM 切替対応の coding assistant | claude-org-ja は coding assistant ではなく **organization runtime**。Claude Code を素で叩き、組織運用 discipline を強制する |
| **tmux / zellij + 手動 prompt 分割** | 汎用 multiplexer + 人間によるペイン手動運用 | claude-org-ja は専用 MCP（`renga-peers`）で**ペイン間 P2P メッセージング + 構造化 spawn + 状態 suspend/resume** を提供。手動ペイン運用には無い「役割契約」「自動 curation」「permission ロール別配布」がコア |

→ 詳細な 16 軸比較は [docs/oss-comparison.md](docs/oss-comparison.md) を参照。

---

## 仕組み

```
人間 <-> 窓口 Claude（司令塔）
              |
              +-> フォアマン（ワーカー起動・指示の代行）
              +-> キュレーター（知見整理、30 分ごとに自動実行）
              +-> ワーカー群（実作業、完了後に自動消滅）
```

- **窓口 (Secretary)**: 人間との唯一の接点。タスク分解・委譲判断・結果報告
- **フォアマン (Foreman)**: ペイン起動・指示送信を代行し、窓口のロックを回避
- **キュレーター (Curator)**: 蓄積された raw 知見を curated に昇華し、スキル / プロセス改善を提案
- **ワーカー (Worker)**: 実作業を担当。per-task `WORKER_DIR` 境界内で自律的に commit / PR 作成。完了後に raw 知見を記録

全ペインは同一タブ内で動作します（`new_tab` は組織運用では使いません）。

---

## 意図的に持たない（Non-goals 要約）

claude-org-ja の design 哲学を能動的に明示するため、**意図的に持たない 5 項目**:

1. **`--dangerously-skip-permissions` 既定 ON はしない** — narrow allowlist + 多層防御を core value とする。permission bypass は受け入れない
2. **固定 role pool（Frontend/Backend/QA agent）は持たない** — per-task で worker_dir / CLAUDE.md を都度生成する。事前 role pool は per-task discipline と矛盾する
3. **大規模並列（20+ agents）はしない** — 3〜5 worker 想定。quality 重視で farm 思想と方向が逆
4. **Auto-create app（自然言語 → scaffold 生成）はしない** — operational discipline FW、scaffold generator ではない
5. **Multi-provider（Aider/Codex/Gemini 切替）はしない** — Claude-only。codex は review hook の optional 用途のみ

詳細・残り項目（PTY 層 / `--add-dir` 横断 / MCP HTTP 公開 等）と「なぜそうしないか」「代替手段は何か」は [docs/non-goals.md](docs/non-goals.md) を参照。

---

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

---

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | 使い方ガイド |
| [docs/overview-technical.md](docs/overview-technical.md) | アーキテクチャ・MCP ツール詳細 |
| [docs/non-goals.md](docs/non-goals.md) | 意図的に持たない機能の詳細 |
| [docs/oss-comparison.md](docs/oss-comparison.md) | OSS 比較レポート（16 軸） |
| [docs/verification.md](docs/verification.md) | テスト手順・検証結果 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | コントリビュートガイド |

---

## セキュリティ・許可境界

claude-org-ja は **4 層防御**（`permissions.deny` / PreToolUse hooks / sandbox / pre-commit secret スキャナ）を全ロールに強制適用します。`git push --no-verify` 等の verify-bypass、`git push --force` 系の履歴破壊、`.env` / 認証情報の読取、ステージ差分への secret 混入は、Claude Code 経由の操作については複数層で確実に止まります。

詳細・各層の責任境界・既知の残存リスク（関数定義経由 bypass 等）・PreToolUse hook の検知範囲は [docs/overview-technical.md](docs/overview-technical.md) と `.hooks/` / `.githooks/` 配下を参照。

新しくクローンしたら、1 度だけ:

```bash
bash scripts/install-hooks.sh
```

で `core.hooksPath` を `.githooks/` に設定し、commit 直前の secret スキャナを有効化してください。

---

## 困ったとき

- **`/org-start` しても反応しない** → Secretary ペインの Claude Code がログイン済みか確認（`claude` を叩いて初回認証）。`claude mcp list` に `renga-peers` が出ているかも確認
- **`renga-peers` MCP が見えない** → `renga mcp status` で登録状態を確認し、未登録なら `renga mcp install` を再実行（user-scope 登録なので全ペインに即時反映）
- **`gh auth status` が Not logged in** → `gh auth login` で GitHub 認証を済ませる。未認証だとワーカーが PR を作れません
- **互換性プリフライト**: `tools/check_renga_compat.py` で renga バージョンと MCP ツール surface を一括チェック

それでも解決しない場合は [Issues](https://github.com/happy-ryo/claude-org/issues) へ。

---

## ライセンス

[MIT License](LICENSE) © 2026 Ryo Iwama
