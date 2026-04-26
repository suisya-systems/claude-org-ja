# claude-org-ja

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/happy-ryo/claude-org/actions/workflows/tests.yml/badge.svg)](https://github.com/happy-ryo/claude-org/actions/workflows/tests.yml)
[![Install](https://img.shields.io/badge/install-WIP%20%28%23106%29-lightgrey.svg)](https://github.com/happy-ryo/claude-org/issues/106)

> **claude-org-ja は日本語ファーストのリファレンス配布物です。**
> 英語版の兄弟リポジトリ `claude-org` は別途並走予定（日英 2 系統構成）。
>
> > **TODO**: 英語版兄弟リポジトリ (`claude-org`) へのクロスリンクは Issue #110 で当該リポジトリを作成後に追加します。
> > **TODO**: ワンライナー導入手順（`curl | bash` 形式）は Issue #106 で実装後にこの README に反映します。
> > **TODO**: 4 層アーキテクチャ図は Issue #108 で別途追加予定です（以下の節は現状テキストのみ）。

---

## 30 秒ピッチ

**問題**: Claude Code を「窓口 1 つ + ワーカー多数」の体制で長時間運用したい。しかし Claude Code は単独セッション前提で、複数インスタンスを安全に協調させるための運用層は公式には提供されていない。tmux 風の素朴な分割や、いわゆる farm 系の全自動並列では、許可境界・知見の蓄積・状態の復元・タスクごとの環境構築といった**運用上の規律**が抜け落ちる。

**解決策**: claude-org-ja は Claude Code 専用の**運用規律フレームワーク**である。1 つの窓口 Claude と対話するだけで、フォアマン・キュレーター・ワーカーが裏で自動的に派生し、許可エントリの絞り込み（narrow allowlist）+ タスクごとの作業ディレクトリ境界 + 30 分おきの自動的な知見整理 + 状態の中断・再開を**最初から強制する**。

**対象利用者**: Claude Code を業務で長時間回したい開発者・オペレーターのうち、「全自動より明示的な許可境界が欲しい」「3〜5 ワーカーを品質重視で動かしたい」「知見の自己成長ループを回したい」層。

---

## 4 層アーキテクチャ

claude-org-ja は 4 層スタックの **Layer 4** に位置するリファレンス配布物。Layer 3（端末多重化器 + MCP サーバー = `renga`）を依存先として持つ。

```
Layer 4: claude-org-ja  ← このリポジトリ（運用規律フレームワーク）
Layer 3: renga          ← suisya-systems/renga（ペイン管理 + MCP サーバー）
Layer 2: org-runtime    ← 抽出予定
Layer 1: core-harness   ← 抽出予定
```

> **TODO**: 上記の図解版（4 層アーキテクチャ図）は Issue #108 で別途追加予定です。

**Layer 3 と Layer 4 は同時公開**します。`renga` は単体で AI 開発以外の用途でも使える端末多重化器 + MCP サーバーで、claude-org-ja はその上に組織運用規律を載せたリファレンス配布物です。

---

## クイックスタート

> **OS について**: 以下は macOS / Linux（bash）前提。Windows でも `python3` を `python` に読み替えれば動作します（PowerShell 用ワンライナーは Issue #106 で対応予定）。
>
> **TODO**: ワンライナー導入手順（`curl -fsSL ... | bash` 形式）は Issue #106 で実装予定。現状は以下の手動手順で導入してください。

```bash
# 1. 依存ツールを導入
#    Claude Code (https://claude.ai/code), gh, Node.js v18+, Python 3.8+, jq を各公式手順で導入
#    renga (Layer 3) は 0.18.0 以上が必須:
npm install -g @suisya-systems/renga@0.18.0

# 2. 認証
gh auth login
claude                          # Claude Code の初回ログイン

# 3. このリポジトリを取得
git clone https://github.com/happy-ryo/claude-org.git
cd claude-org

# 4. renga の MCP サーバーを Claude Code に登録（初回のみ）
renga mcp install

# 5. 窓口（Secretary）ペインを起動
renga --layout ops
```

窓口ペインで Claude Code が立ち上がったら、**初回のみ** `/org-setup` を実行してロール別の許可・フックの設定を配置します:

```
/org-setup
```

続いて組織を起動します:

```
/org-start
```

これでフォアマンとキュレーターが派生し、以後は自然言語で依頼を投げるだけ。詳細は [docs/getting-started.md](docs/getting-started.md) を参照。

---

## なぜこれを使うか（既存ツールとの比較）

| 比較対象 | 立ち位置 | claude-org-ja との違い |
|---|---|---|
| **Claude Code Subagents / Agent Teams（公式）** | Anthropic 公式の「リード / チームメイト」階層 + 自動メモリ + フック | claude-org-ja は公式の上に乗る運用層。**競合せず共存**する。公式が提供しない「タスクごとの作業ディレクトリ境界の強制」「スキーマ駆動の設定 drift 検出」「生の知見 → 整理済み知見への昇華パイプライン」「30 分おきの自動整理ループ」を上乗せする |
| **ccswarm（Rust 製、多重化器なしの協調基盤）** | 固定ロールプール（フロントエンド / バックエンド / QA エージェント等）+ 大規模並列志向 | claude-org-ja は**タスクごとに作業ディレクトリと `CLAUDE.md` を都度生成**する（事前のロールプールは持たない）。3〜5 ワーカーで品質重視（farm 系とは方向が逆） |
| **Aider / aider-codex / Cursor のエージェント** | エディタ統合の単独エージェント、または複数モデル切替対応のコーディング支援ツール | claude-org-ja はコーディング支援ツールではなく**組織運用ランタイム**。Claude Code を素で叩き、組織運用規律を強制する |
| **tmux / zellij + 手動でのプロンプト分割** | 汎用の端末多重化器 + 人間によるペインの手動運用 | claude-org-ja は専用 MCP サーバー（`renga-peers`）で**ペイン間 P2P メッセージ + 構造化ペイン生成 + 状態の中断・再開**を提供する。手動運用には無い「役割契約」「自動知見整理」「ロール別の許可配布」が中核 |

→ より詳細な 16 軸の比較は [docs/oss-comparison.md](docs/oss-comparison.md) を参照。

---

## 仕組み

```
人間 <-> 窓口 Claude（司令塔）
              |
              +-> フォアマン（ワーカー起動・指示の代行）
              +-> キュレーター（知見整理、30 分ごとに自動実行）
              +-> ワーカー群（実作業、完了後に自動消滅）
```

- **窓口（Secretary）**: 人間との唯一の接点。タスク分解・委譲判断・結果報告を担う
- **フォアマン（Foreman）**: ペイン起動・指示送信を代行し、窓口がブロックされる時間を最小化する
- **キュレーター（Curator）**: 蓄積された生の知見を整理済みの知見に昇華し、スキルやプロセスの改善を提案する
- **ワーカー（Worker）**: 実作業を担当する。タスクごとの作業ディレクトリ境界の中で自律的にコミット・プルリクエスト作成を行い、完了後に生の知見を記録する

全ペインは同一タブ内で動作します（別タブを開く `new_tab` は組織運用では使いません）。

---

## 意図的に持たない機能（要約）

claude-org-ja の設計哲学を能動的に明示するため、**意図的に持たない 5 項目**:

1. **ワーカーに `--dangerously-skip-permissions` を既定で撒かない** — 許可エントリの絞り込み + 多層防御を中核価値とする。実作業ロールに許可境界の全面回避を一律で配ることはしない（フォアマンのみ Sonnet 運用上やむなく `bypassPermissions` を採用、詳細は [docs/non-goals.md](docs/non-goals.md) §1）
2. **固定ロールプール（フロントエンド / バックエンド / QA エージェント）を持たない** — タスクごとに作業ディレクトリと `CLAUDE.md` を都度生成する。事前のロールプールはタスクごとの規律と矛盾する
3. **大規模並列（20+ エージェント）はしない** — 3〜5 ワーカー想定。品質重視で farm 系とは方向が逆
4. **自然言語からのプロジェクト雛形生成（Auto-create app）はしない** — 運用規律フレームワークであり、雛形生成器ではない
5. **複数プロバイダー切替（Aider / Codex / Gemini 等）はしない** — Claude 専用。`codex` は任意のレビュー用途のみ想定

詳細・残り 7 項目（PTY 層 / `--add-dir` 横断 / MCP の HTTP 公開 等）と「なぜそうしないか」「代替手段は何か」は [docs/non-goals.md](docs/non-goals.md) を参照。

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
| [docs/oss-comparison.md](docs/oss-comparison.md) | 関連プロジェクトとの比較レポート（16 軸） |
| [docs/verification.md](docs/verification.md) | テスト手順・検証結果 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | コントリビュートガイド |

---

## セキュリティと許可境界

claude-org-ja は **4 層防御**（`permissions.deny` / PreToolUse フック / サンドボックス（sandbox）/ コミット直前の秘密情報スキャナ）を採用しています。ただし**ロールごとに各層の効き方は異なります**:

- **ワーカー / 窓口 / キュレーター（`auto` モード）**: `permissions.deny` と `permissions.allow` がいずれも有効。PreToolUse フックも有効。4 層防御がフル稼働する
- **フォアマン（`bypassPermissions` モード）**: `permissions.deny` と `permissions.allow` は **bypass される**（保護対象ディレクトリ `.git/`, `.claude/`, `.vscode/`, `.idea/`, `.husky/` への書き込み確認プロンプトのみ残る）。PreToolUse フックは引き続き有効だが、現状フォアマンには未配備。実効防御は限定的で、ロール契約と窓口監視に依存している（別 Issue で PreToolUse フック配備を追跡予定）

`git push --no-verify` 等の検証バイパス、`git push --force` 系の履歴上書き、`.env` や認証情報の読み取り、ステージ差分への秘密情報の混入は、`auto` モードのロールについては複数層で確実に止まります。フォアマン側のロール契約に基づく自主規律と、bypass モードでの挙動の正確な整理は [docs/non-goals.md §1](docs/non-goals.md#1-ワーカーに---dangerously-skip-permissions-を既定で撒かない) を参照。

各層の責任境界・既知の残存リスク（関数定義経由の回避手段など）・PreToolUse フックの検知範囲は [docs/overview-technical.md](docs/overview-technical.md) と `.hooks/` / `.githooks/` 配下を参照。

新しくクローンしたら、1 度だけ以下を実行してください:

```bash
bash scripts/install-hooks.sh
```

これで `core.hooksPath` が `.githooks/` に設定され、コミット直前の秘密情報スキャナが有効になります。

---

## 困ったとき

- **`/org-start` しても反応しない** → 窓口ペインの Claude Code がログイン済みか確認（`claude` を叩いて初回認証）。`claude mcp list` に `renga-peers` が出ているかも確認
- **`renga-peers` MCP サーバーが見えない** → `renga mcp status` で登録状態を確認し、未登録なら `renga mcp install` を再実行（ユーザースコープ登録なので全ペインに即時反映される）
- **`gh auth status` が Not logged in** → `gh auth login` で GitHub 認証を済ませる。未認証だとワーカーがプルリクエストを作れません
- **互換性の事前確認**: `tools/check_renga_compat.py` で `renga` のバージョンと MCP ツール群を一括確認できます

それでも解決しない場合は [Issues](https://github.com/happy-ryo/claude-org/issues) へ。

---

## ライセンス

[MIT License](LICENSE) © 2026 Ryo Iwama
