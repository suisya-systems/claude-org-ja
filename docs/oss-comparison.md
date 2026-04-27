# マルチエージェントAI協調フレームワーク — OSS比較レポート

> 調査日: 2026-04-06
> 目的: claude-orgの設計思想（複数AIインスタンスの協調・役割分担・常駐ロール・自己成長ループ・スキルによるプログレッシブディスクロージャー）と類似するOSSを網羅的に調査し、比較する

---

## 1. claude-orgの設計特徴（比較の基準）

claude-orgは以下の特徴を持つ:

| 特徴 | 内容 |
|---|---|
| **マルチインスタンス協調** | 窓口・ディスパッチャー・キュレーター・ワーカーの4種類のClaude Codeインスタンスが協調動作 |
| **役割分担** | Secretary（対話）、Dispatcher（ペイン管理）、Curator（知見整理）、Worker（実作業）の明確な分業 |
| **常駐ロール** | Secretary/Dispatcher/Curatorは常駐、Workerはオンデマンド起動 |
| **状態管理** | ジャーナル（JSONL）＋スナップショット（Markdown）＋サスペンドの三層構造 |
| **自己成長ループ** | Worker→raw知見→Curator整理→改善提案→ユーザー承認→スキル/CLAUDE.md更新 |
| **通信方式** | `renga-peers` MCP（同タブ内 P2P プッシュ型）＋ CLAUDE.md（永続ベースライン） |
| **プログレッシブディスクロージャー** | スキルシステムにより必要時のみ詳細手順をロード |

---

## 2. 比較対象OSS一覧

### 2.1 汎用マルチエージェントフレームワーク

| # | プロジェクト | 開発元 | GitHub Stars | ライセンス |
|---|---|---|---|---|
| 1 | [CrewAI](https://github.com/crewaiinc/crewai) | CrewAI Inc. | 44,300+ | MIT |
| 2 | [LangGraph](https://github.com/langchain-ai/langgraph) | LangChain | 24,800+ | MIT |
| 3 | [Microsoft Agent Framework (AutoGen)](https://github.com/microsoft/autogen) | Microsoft | 40,000+ | MIT |
| 4 | [OpenAI Swarm](https://github.com/openai/swarm) | OpenAI | — | MIT |
| 5 | [Google ADK](https://github.com/google/adk-python) | Google | — | Apache 2.0 |
| 6 | [AWS Agent Squad](https://github.com/awslabs/agent-squad) | AWS Labs | — | Apache 2.0 |

### 2.2 Claude Code特化型マルチエージェント

| # | プロジェクト | 開発元 | 特徴 |
|---|---|---|---|
| 7 | [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) | Anthropic（公式） | 公式のマルチセッション協調機能（実験的） |
| 8 | [Ruflo](https://github.com/ruvnet/ruflo) | ruvnet | Claude Code向けスウォーム型エージェントプラットフォーム |
| 9 | [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode) | Yeachan Heo | チーム指向のマルチエージェントオーケストレーション |
| 10 | [claude-code-by-agents](https://github.com/baryhuang/claude-code-by-agents) | baryhuang | @mentionベースのマルチエージェント協調 |

### 2.3 自己成長・自己改善系

| # | プロジェクト | 開発元 | 特徴 |
|---|---|---|---|
| 11 | [Agent Zero](https://github.com/agent0ai/agent-zero) | agent0ai | 自律的ツール作成と永続メモリによる学習 |
| 12 | [OpenSpace](https://github.com/HKUDS/OpenSpace) | HKUDS（香港大学） | 自己進化型スキルエンジン |
| 13 | [AutoAgent](https://github.com/kevinrgu/autoagent) | Kevin Gu / thirdlayer | エージェントハーネスの自律的最適化 |
| 14 | [SuperAGI](https://github.com/TransformerOptimus/SuperAGI) | TransformerOptimus | 自律エージェントフレームワーク（パフォーマンス自動改善） |

---

## 3. 比較軸ごとの詳細分析

### 3.1 マルチエージェント協調方式

| プロジェクト | 協調方式 | 詳細 |
|---|---|---|
| **claude-org** | **階層型＋P2P** | Secretary→Dispatcher→Worker の階層的委譲 ＋ `renga-peers` MCP でのP2P通信（同タブスコープ） |
| CrewAI | ロールベース協調 | Agent に role/backstory/goal を定義し、crew として協調。Sequential / Hierarchical プロセス |
| LangGraph | グラフベース | ノード（エージェント）とエッジ（遷移）でワークフローを定義。条件分岐・ループ対応 |
| AutoGen | 会話ベース | エージェント間のメッセージパッシングによる協調。GroupChat で複数エージェント会話 |
| Swarm | ハンドオフ型 | Agent 間の明示的なハンドオフ（引き継ぎ）でタスクを委譲。ステートレス |
| Google ADK | 階層型 | エージェントを階層的に構成。Sequential / Parallel / Loop ワークフロー ＋ LLM動的ルーティング |
| Agent Squad | スーパーバイザー型 | SupervisorAgent が agent-as-tools パターンで専門エージェントを並列調整 |
| Agent Teams | チームリード型 | 1つのリードセッション＋最大15のチームメイト。共有タスクリスト＋P2Pメッセージング |
| Ruflo | スウォーム型 | 最大100エージェントが並列動作。6種の協調パターン。自己学習ルーティング |
| oh-my-claudecode | オートパイロット型 | 32専門エージェントの自動委譲。最大5並行ワーカー |
| Agent Zero | 階層型 | 上位エージェントが下位エージェントを生成・委譲。再帰的な構造 |
| OpenSpace | スタンドアロン＋共有 | 単体Agent ＋ MCPサーバー経由での統合。スキル共有コミュニティ |

**claude-orgとの類似度**: Agent Teams が最も近い（P2P通信 ＋ 共有タスクリスト）。CrewAI のロールベース設計も概念的に近い。

### 3.2 役割分担

| プロジェクト | 役割モデル | 常駐ロール | 動的ロール |
|---|---|---|---|
| **claude-org** | **Secretary / Dispatcher / Curator / Worker** | **3（Sec/Fore/Cur）** | **Worker（オンデマンド）** |
| CrewAI | ユーザー定義ロール（Manager / Researcher 等） | なし（実行時のみ） | 全エージェント |
| LangGraph | ノードとして定義（固定名なし） | なし | 全ノード |
| AutoGen | UserProxy / Assistant / GroupChatManager 等 | なし | 全エージェント |
| Swarm | ユーザー定義（Triage / Sales 等） | なし | 全エージェント |
| Google ADK | 階層的エージェント定義 | なし | 全エージェント |
| Agent Squad | Supervisor ＋ 専門エージェント | なし | 全エージェント |
| Agent Teams | Team Lead ＋ Teammates | Team Lead（1） | Teammates |
| Ruflo | Orchestrator ＋ Specialist Swarm | なし（オンデマンド起動） | 全エージェント |
| oh-my-claudecode | Architect ＋ 32専門エージェント | なし | 全エージェント |
| Agent Zero | 親エージェント＋子エージェント | 親（1） | 子エージェント |
| OpenSpace | 単体エージェント（役割分担なし） | — | — |

**claude-orgの独自性**: **常駐ロールの多さ**（3種）と**明確な組織構造**（Secretary-Dispatcher-Curator-Worker）は他に類を見ない。特にCurator（知見整理専門の常駐プロセス）はclaude-org固有の設計。

### 3.3 状態管理

| プロジェクト | 状態永続化 | 形式 | クラッシュリカバリ |
|---|---|---|---|
| **claude-org** | **ジャーナル＋スナップショット＋サスペンド（三層）** | **JSONL / Markdown** | **ジャーナルからの復元 ＋ org-resume** |
| CrewAI | メモリ（短期/長期/エンティティ） | 内部DB | 限定的 |
| LangGraph | チェックポイント（永続化） | カスタムストレージ | タイムトラベルデバッグ対応 |
| AutoGen | セッションベース状態管理 | メモリ / シリアライズ | v0.4で改善 |
| Swarm | **なし**（ステートレス設計） | — | なし |
| Google ADK | セッション状態 | カスタム | Vertex AI連携 |
| Agent Squad | コンテキスト管理 | メモリ | 限定的 |
| Agent Teams | 共有タスクリスト（ファイルベース） | JSON / ファイル | タスクリストから復元可能 |
| Ruflo | ニューラルメモリ（v3） | 内部DB | パターン保持（catastrophic forgetting防止） |
| Agent Zero | 永続メモリ | ファイルベース | メモリから復元 |
| OpenSpace | スキルDB | ファイルベース | スキルの自動修復（FIXモード） |

**claude-orgの特徴**: **Markdown形式での状態管理**は、新規インスタンスが読むだけで状況を把握できる点がユニーク。LangGraphのチェックポイント機能が機能面では最も充実。

### 3.4 自己改善メカニズム

| プロジェクト | 自己改善 | メカニズム | 人間の承認 |
|---|---|---|---|
| **claude-org** | **あり（構造化ループ）** | **Worker→raw知見→Curator整理→提案→承認→スキル更新** | **必須（安全弁）** |
| CrewAI | 限定的 | タスク間でのメモリ蓄積 | なし |
| LangGraph | なし（外部実装は可能） | — | — |
| AutoGen | 計画中 | エージェントの長期学習（ロードマップ） | — |
| Swarm | なし | — | — |
| Google ADK | なし | — | — |
| Agent Squad | なし | — | — |
| Agent Teams | なし | — | — |
| Ruflo | あり | タスク実行からの自動学習、パターン保持 | なし（自動） |
| oh-my-claudecode | 限定的 | 実行結果のフィードバック | なし |
| Agent Zero | あり | 動的ツール作成、永続メモリによる学習 | なし（自律的） |
| OpenSpace | **あり（最も高度）** | **FIX / DERIVED / CAPTURED の3モード進化。スキルの自動修復・派生・獲得** | **なし（自律的）** |
| AutoAgent | あり（メタ最適化） | ハーネス自体を自律的に最適化（プロンプト・ツール・ルーティング） | なし（自律的） |
| SuperAGI | あり | 実行ごとのパフォーマンス改善 | なし |

**claude-orgの独自性**: **人間の承認を挟む自己改善ループ**はclaude-org固有。他の自己改善系は自律的（人間介入なし）。OpenSpaceのスキル進化メカニズムはclaude-orgのスキルシステムと概念的に近いが、人間の承認プロセスがない。

### 3.5 通信方式

| プロジェクト | 通信方式 | 特徴 |
|---|---|---|
| **claude-org** | **`renga-peers` MCP（同タブ内 P2P プッシュ型）＋ CLAUDE.md（永続ベースライン）** | **二重化による信頼性。揮発的通信 ＋ 永続的指示の組み合わせ** |
| CrewAI | コンテキスト共有・委譲 | エージェント間で context / delegation |
| LangGraph | 共有State経由 | グラフのState オブジェクトを通じたデータ共有 |
| AutoGen | メッセージパッシング | エージェント間の直接メッセージ。GroupChatでブロードキャスト |
| Swarm | ハンドオフ関数 | 会話コンテキストを丸ごと引き継ぎ |
| Google ADK | 階層的メッセージ ＋ 転送 | 親子間のメッセージ ＋ LLM動的ルーティング |
| Agent Squad | インテントルーティング | ユーザー入力を動的に適切なエージェントへルーティング |
| Agent Teams | P2Pメールボックス ＋ 共有タスクリスト | ファイルベースのメールボックスシステム |
| Ruflo | スウォーム通信 | 階層的協調 ＋ コンセンサスメカニズム |
| Agent Zero | 親子間メッセージ | 階層的なメッセージパッシング |

**claude-orgの独自性**: **指示の二重化**（CLAUDE.md永続指示 ＋ `renga-peers` リアルタイム通信）は他に類を見ない設計。Agent Teams のメールボックスシステムが最も近い。

---

## 4. 総合比較表

| 比較軸 | claude-org | CrewAI | LangGraph | AutoGen | Agent Teams | Ruflo | OpenSpace | Agent Zero |
|---|---|---|---|---|---|---|---|---|
| 協調方式 | 階層＋P2P | ロール型 | グラフ型 | 会話型 | チーム型 | スウォーム型 | 単体＋共有 | 階層型 |
| 役割の固定度 | ◎ 4役固定 | △ 自由定義 | △ 自由定義 | △ 自由定義 | ○ Lead＋Members | △ 自由定義 | × なし | ○ 親子 |
| 常駐ロール | ◎ 3種 | × なし | × なし | × なし | ○ 1種 | × なし | × なし | ○ 1種 |
| 状態永続化 | ◎ 三層 | ○ メモリ | ◎ チェックポイント | ○ セッション | ○ タスクリスト | ○ ニューラルDB | ○ スキルDB | ○ メモリ |
| 自己改善 | ◎ 構造化 | △ 限定的 | × なし | × 計画中 | × なし | ○ 自動学習 | ◎ 3モード進化 | ○ ツール生成 |
| 人間承認 | ◎ 必須 | × なし | × なし | × なし | × なし | × なし | × なし | × なし |
| P2P通信 | ◎ | × | × | ○ | ◎ | △ | × | × |
| 指示の永続化 | ◎ 二重化 | × | × | × | △ | × | × | × |

凡例: ◎ 高度に実装 / ○ 実装あり / △ 限定的 / × なし

---

## 5. 特筆すべき類似プロジェクト（Top 3）

### 5.1 Claude Code Agent Teams（最も構造的に近い）

- **類似点**: P2P通信、共有タスクリスト、チームリード＋メンバーの構造
- **相違点**: 常駐ロールは1種（Lead）のみ、Curator相当なし、自己成長ループなし、指示の二重化なし
- **評価**: インフラ層（通信・タスク管理）は近いが、組織設計と自己改善の層が欠けている

### 5.2 OpenSpace（自己改善の思想が最も近い）

- **類似点**: スキルの自動進化（FIX/DERIVED/CAPTUREDはclaude-orgのraw→curated→skill更新に類似）、スキルの再利用
- **相違点**: マルチエージェント協調ではない（単体エージェント＋MCP連携）、人間承認プロセスなし、役割分担なし
- **評価**: 自己改善メカニズムの成熟度はclaude-orgより高い可能性があるが、組織としての協調機能がない

### 5.3 CrewAI（役割ベース設計が近い）

- **類似点**: エージェントに明確なロール（role/backstory/goal）を定義、階層的プロセス、委譲（delegation）の概念
- **相違点**: 常駐ロールなし、状態管理は限定的、自己成長ループなし、Claude Code非依存
- **評価**: 役割ベースの協調パターンは概念的に近いが、永続的な組織としての設計思想は異なる

---

## 6. claude-orgの差別化ポイント

調査の結果、claude-orgには以下の差別化要素が確認された:

### 6.1 既存OSSにない特徴

1. **常駐マルチロール組織**: Secretary/Dispatcher/Curator の3種の常駐ロールを持つ組織構造は他に例がない
2. **人間承認付き自己改善ループ**: 自己改善を持つフレームワークは複数あるが、人間の承認を安全弁として組み込んでいるのはclaude-orgのみ
3. **指示の二重化**: CLAUDE.md（永続ベースライン）＋ `renga-peers` メッセージ（リアルタイム補足）の組み合わせは他に類を見ない
4. **プログレッシブディスクロージャー**: スキルシステムによるコンテキスト消費の最小化戦略
5. **Markdown状態管理**: 新インスタンスが読むだけで状況把握可能な設計（機械可読かつ人間可読）

### 6.2 既存OSSから学べる点

1. **LangGraph のチェックポイント**: タイムトラベルデバッグは状態管理の強化に有用
2. **OpenSpace のスキル進化3モード**: FIX/DERIVED/CAPTURED の分類はclaude-orgの知見整理に適用可能
3. **Ruflo の自己学習ルーティング**: タスクの自動振り分けの改善に参考になる
4. **Agent Teams のファイルロック**: 複数ワーカーの同時編集時の衝突防止メカニズム
5. **AutoAgent のメタ最適化**: ハーネス自体の自動改善は、claude-orgのスキル自動更新の高度化に応用可能

---

## 7. まとめ

claude-orgは「複数AIインスタンスによる永続的な組織運営」という独自のポジションを持つ。既存OSSの多くは「タスク実行時のエージェント協調」に焦点を当てているのに対し、claude-orgは**組織そのものの継続的運営と自己改善**を目指している。

最も近いプロジェクトであるClaude Code Agent Teamsでさえ、常駐キュレーターや自己成長ループを持たない。claude-orgの設計思想は、現時点のOSSランドスケープにおいて明確なギャップを埋めるものである。

---

## Sources

- [CrewAI](https://crewai.com/open-source)
- [LangGraph](https://www.langchain.com/langgraph)
- [Microsoft AutoGen / Agent Framework](https://github.com/microsoft/autogen)
- [OpenAI Swarm](https://github.com/openai/swarm)
- [Google ADK](https://google.github.io/adk-docs/)
- [AWS Agent Squad](https://github.com/awslabs/agent-squad)
- [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams)
- [Ruflo](https://github.com/ruvnet/ruflo)
- [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode)
- [claude-code-by-agents](https://github.com/baryhuang/claude-code-by-agents)
- [Agent Zero](https://github.com/agent0ai/agent-zero)
- [OpenSpace](https://github.com/HKUDS/OpenSpace)
- [AutoAgent](https://github.com/kevinrgu/autoagent)
- [SuperAGI](https://github.com/TransformerOptimus/SuperAGI)
- [The Best Open Source Frameworks For Building AI Agents in 2026](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks)
- [Self-Evolving Agents: Open-Source Projects Redefining AI in 2026](https://evoailabs.medium.com/self-evolving-agents-open-source-projects-redefining-ai-in-2026-be2c60513e97)
