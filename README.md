# claude-org-ja

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/suisya-systems/claude-org-ja/actions/workflows/tests.yml/badge.svg)](https://github.com/suisya-systems/claude-org-ja/actions/workflows/tests.yml)

> 日本語で使うことを前提にしたバージョンです。英語版は [suisya-systems/claude-org](https://github.com/suisya-systems/claude-org)。

## これは何か
claude-org-ja は、Claude Code を 1 つの窓口から複数のワーカーへ安全に仕事を振れるようにするための、マルチワーカー運用環境です。

複数の Claude Code を小さな開発チームのように連携させ、タスクの分解、作業場所の準備、ワーカーへの割り当て、状態保存、知見整理をまとめて扱います。

「ハーネスエンジニアリングやループエンジニアリングに興味はあるけれど、いきなり自分で構築するのは難しい」という人が、最初の一歩として使うのに向いています。

利用者は窓口の Claude Code と会話するだけでよく、タスクの分解、ワーカーへの作業割り当て、作業状態の保存と再開、たまった知見の整理は、すべて裏で自動的に進みます。
たまった知見を基に、スキルの修正や新たなスキルの作成を提案する機能も備えています。

人間が担うのは、タスクの選択、push・PR 作成、重要な設計判断などに絞ります。それ以外はできるかぎりエージェントに任せ、作業を進められるようにする設計です。

これらの機能を提供するために最低限必要なセキュリティ設定も事前に組み込まれています。

## すぐ試す

前提ツール（git / claude / gh / jq / Node.js / Python）が入っていれば、1 行のコマンドでクローンとセットアップができます。

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/suisya-systems/claude-org-ja/main/scripts/install.sh | bash
```

```powershell
# Windows (PowerShell 7+)
iwr -useb https://raw.githubusercontent.com/suisya-systems/claude-org-ja/main/scripts/install.ps1 | iex
```

クローン後、初回だけ次を実行します。

```bash
cd claude-org-ja
source .venv/bin/activate                                # Linux / macOS
bash scripts/install-hooks.sh                            # コミット直前の秘密情報スキャナ
python tools/org_setup_prune.py --user-common-sandbox    # 個人設定の安全強化（1 回だけ）
claude-org-runtime org up                                # 窓口（Secretary）を起動
```

窓口が立ち上がったら、初回だけ `/org-setup`（許可設定の配置）→ `/org-start`（組織の起動）の順に実行します。2 回目以降は `claude-org-runtime org up` → `/org-start` だけで再開できます。前提条件・手動手順・困ったときは [`docs/getting-started.md`](docs/getting-started.md) を参照してください。

### renga で起動する場合

通常は `claude-org-runtime org up` で起動します。renga は、claude-org-ja に合わせて開発された、Windows・Linux・macOS 対応のターミナル作業環境です（[GitHub](https://github.com/suisya-systems/renga) / [npm](https://www.npmjs.com/package/@suisya-systems/renga)）。複数の Claude Code ペインを 1 つの画面に並べ、窓口・ディスパッチャー・ワーカーのペイン管理と、ペイン同士の連絡を扱います。画面全体でペインの動きを見ながら起動したい場合は、renga を使って起動できます。クローン後のディレクトリで次を実行します。

```bash
# macOS / Linux
export ORG_TRANSPORT=renga
python tools/org_setup_prune.py --all
renga --layout ops
```

```powershell
# Windows (PowerShell 7+)
$env:ORG_TRANSPORT = 'renga'
python tools/org_setup_prune.py --all
renga --layout ops
```

renga で窓口を開いた場合も、Claude Code 上で実行する手順は同じです。初回は `/org-setup` → `/org-start`、2 回目以降は `/org-start` で再開します。

## 仕組み

```
人 ⇄ 窓口（司令塔）
        ├─ ディスパッチャー（ワーカーの起動・指示を代行）
        ├─ キュレーター（知見の整理。必要なときだけ起動）
        └─ ワーカー群（実作業。終わると自動で片付く）
```

- **窓口（Secretary）** — 人が話す相手。タスクを分解し、判断し、結果を報告する。
- **ディスパッチャー（Dispatcher）** — ワーカーの起動と指示出しを肩代わりし、待ち時間を減らす。
- **キュレーター（Curator）** — たまった学びを整理して知見として残す。必要なときだけ立ち上がる。
- **ワーカー（Worker）** — タスクごとの作業領域で実作業し、終わると自動で片付く。

依頼された作業は、ワーカーごとの作業ディレクトリで進みます。通常の作業先は `registry/org-config.md` の `workers_dir`（初期値は `../workers`）配下です。登録済みプロジェクトなら `../workers/{project_slug}/` を使い、同じプロジェクトで複数の作業が並ぶと `../workers/{project_slug}/.worktrees/{task_id}/` に git worktree を切ります。こうした作業場所の確保や設定ファイルの配置まで自動化することも、この組織の特徴です。

<table>
  <tr>
    <td width="50%"><img src="docs/assets/org-start-fresh.png" alt="/org-start 直後: 窓口とディスパッチャーが起動した状態"></td>
    <td width="50%"><img src="docs/assets/org-start-pane-layout.png" alt="稼働中: 窓口・ディスパッチャーに加え並列ワーカーが動いている状態"></td>
  </tr>
</table>

上の画像は、renga で組織を起動したときの画面例です。左は起動直後、右は窓口・ディスパッチャー・ワーカーが並んで動いている状態です。

## 主な特長

- **人は判断に集中** — 起動・分配・状態管理は窓口に任せ、人は呼ばれたときに判断を返します。人の確認が必要な場面は必ず止まって知らせるので、見落としを防げます。
- **明示的な許可範囲と多層防御** — 権限を一律に広く渡さず、タスクごとに作業領域を分けます。サンドボックス・フック・許可範囲が全タスクに効きます。
- **品質重視の少数並列** — 大量のワーカーを並べるのではなく、3〜5 ワーカー程度で回します。実装とは別のモデルによる独立レビュー（`codex`、任意）も検証に組み込めます。
- **中断・再開と知見の自動整理** — 長く回しても、作業状態と学びが失われません。

設計の背景（安全モデル / Loop Engineering の実装例 / 既存ツールとの比較）は [`docs/overview-business.md`](docs/overview-business.md) と [`docs/overview-technical.md`](docs/overview-technical.md) にまとめています。

## よく使うコマンドとスキル

まず使うコマンドとスキルは少数です。多くの `/org-*` スキルは、窓口が状況に応じて裏で使います。

| いつ | 人間が入力するもの | 何が起きるか |
|---|---|---|
| 起動する | `claude-org-runtime org up` | 窓口（Secretary）の Claude Code を開く |
| 初回セットアップ | `/org-setup` | ロール別の許可設定とフックを配置する |
| 組織を開始・再開する | `/org-start` | 前回状態を読み込み、ディスパッチャーを起動する |
| ディスパッチャーを横で見る | `tools/org-dispatcher-view.sh` | broker/tmux のディスパッチャーペインを読み取り専用で表示し続ける |
| 完全に止める | `claude-org-runtime org down` | broker デーモンまで停止する |

`/org-delegate`・`/org-pull-request`・`/org-escalation`・`/org-retro`・`/org-curate` は、通常は手で打つ必要はありません。依頼、完了報告、人の確認が必要な場面、ワーカー終了などの流れで、窓口やディスパッチャーが裏で使うスキルです。

窓口には、コマンドではなく普通の言葉で依頼できます。

| したいこと | 窓口への入力例 | 何が起きるか |
|---|---|---|
| 作業を頼む | `ブログの記事を直して` | 窓口が必要に応じてワーカーへ割り当てる |
| ワーカーの実際のペインを見る | `ワーカーのペインを見たい` / `attach コマンド教えて` | broker/tmux の読み取り専用 attach コマンドを表示する |
| 次の候補を見る | `次の仕事候補出して` | 未解決 Issue から着手候補を提示する（着手判断は人間） |
| 中断する | `今日は終わり` / `中断して` | 状態を保存して中断する |

長く使うときに便利なコマンドとスキルもあります。

| いつ | 人間が入力するもの | 何が起きるか |
|---|---|---|
| 窓口の会話を作り直す | `/secretary-handover` → `/clear` → `/secretary-resume` | ディスパッチャーやワーカーを止めず、窓口だけ新しいセッションへ引き継ぐ |
| ディスパッチャーの会話を作り直す | `ディスパッチャーを引き継いで` | 窓口がディスパッチャーに引き継ぎを依頼し、`/clear` → `/dispatcher-resume` を入力する |
| 通知監視を使う | `/org-attention-start` / `/org-attention-stop` | 承認待ちや停止を別ペインで監視し、必要に応じて通知音を鳴らす |
| スキル構成を点検する | `/skill-eligibility-check` / `/skill-audit` | 蓄積した知見からスキル化候補や棚卸しを確認する |

`tools/org-dispatcher-view.sh` と `/org-attach` は broker/tmux 用の読み取り専用の補助ツールです。renga を代替手段として使う場合は 1 つの画面に各ペインが並ぶため、attach ではなくその画面を直接見ます。ディスパッチャーを表示し続けたい場合の詳しい手順は [`docs/operations/dispatcher-view.md`](docs/operations/dispatcher-view.md) を参照してください。

## もっと知る

- [`docs/getting-started.md`](docs/getting-started.md) — 導入・前提条件・手動手順・トラブルシューティング
- [`docs/overview-business.md`](docs/overview-business.md) — 業務目線のやさしい概要
- [`docs/overview-technical.md`](docs/overview-technical.md) — アーキテクチャ・4 層スタック・MCP ツール詳細
- [`docs/non-goals.md`](docs/non-goals.md) — 意図的に持たない機能
- [`docs/oss-comparison.md`](docs/oss-comparison.md) — 関連プロジェクトとの比較
- [`docs/verification.md`](docs/verification.md) — テストと安全性（攻撃ベクトル × 防御層）
- [`docs/operations/dispatcher-view.md`](docs/operations/dispatcher-view.md) — 窓口の隣でディスパッチャーを表示し続ける手順（WezTerm / tmux）
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — コントリビュートガイド

困ったときは [Issues](https://github.com/suisya-systems/claude-org-ja/issues) へ。

## ライセンス

[MIT License](LICENSE) © 2026 Ryo Iwama
