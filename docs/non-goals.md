# 意図的に持たない機能（Non-goals）

claude-org-ja は **operational discipline framework** であり、Claude Code 上での組織運用に対象を絞っています。「あった方が便利そうに見えるが、design 哲学上あえて持たない」機能を以下に明示します。能動的な不在表明は、framework の境界と価値を読者に正しく伝えるための装置です。

> README には特に強い 5 項目だけ要約しています。本ドキュメントはその詳細版で、全 12 項目を扱います。

---

## 1. `--dangerously-skip-permissions` を既定 ON にしない

**やらないこと**: Claude Code の permission prompt を全面的にバイパスし、すべての Bash / Edit / Write を無制限に許可するモードを既定として配ること。

**理由**: claude-org-ja は **narrow allowlist + 多層防御**を core value として宣言しています。permission bypass を既定にすると、`git push --force` や `.env` 読取など、組織運用で最も致命的な事故クラスが事前に止まらなくなります。farm 系の fully-autonomous 思想とは方向が逆で、claude-org-ja は「window が見えないところでこっそり危険な操作が走る」状況を許容しません。

**代替手段**: 各ロール（Secretary / Foreman / Curator / Worker）ごとに `tools/role_configs_schema.json` を正典とした `settings.local.json` を `/org-setup` で配布します。allowlist は schema に登録し、CI で drift 検出します。一時的に制約を緩める必要があれば、対象スコープに限定した hook / `permissions.allow` 追記を schema 経由で行ってください。

---

## 2. 固定 role pool（Frontend / Backend / QA agent 等）を持たない

**やらないこと**: 「フロントエンド担当 agent」「バックエンド担当 agent」のような事前定義された role pool を提供すること。

**理由**: claude-org-ja は **per-task** で `WORKER_DIR` と `CLAUDE.md` を都度生成する設計です。事前 role pool は「タスクが来る前に role が決まっている」前提で動くため、per-task discipline（タスクごとに環境を作り直す）と矛盾します。同じ「フロントエンド作業」でも、対象リポジトリ / ブランチ / 検証深度ごとに必要な許可と context は異なるため、テンプレート化された role を再利用することで context drift が起きやすくなります。

**代替手段**: `/org-delegate` がタスクごとに worker を派生させ、その都度 `WORKER_DIR` 内に `CLAUDE.local.md`（タスク固有の指示書）を生成します。「定型タスク」を扱いたい場合は role ではなく **work-skill** として切り出してください（`/org-retro` → skill candidate キュー → `skill-creator` の流れ）。

---

## 3. 大規模並列（20+ agents）をしない

**やらないこと**: farm のように 20〜100 並列で agent を回し、各 agent が同一タスクを試行錯誤するスタイルを採用すること。

**理由**: claude-org-ja は **3〜5 worker / quality 重視** の positioning です。大規模並列は「数で殴って 1 つでも当たれば勝ち」のアプローチで、人間レビュアーが追えない量の PR / commit を生み出します。ロールバック・再現性・知見蓄積という運用 discipline の観点では、worker 数を絞って `/org-retro` で振り返るほうが自己成長ループが回ります。

**代替手段**: 現状の Foreman は同時に複数 worker を派生できますが、ピークでも 3〜5 を上限の目安としてください。「大量の similar タスクをまとめて処理したい」用途なら、worker を多重化するのではなく、タスクをバッチ化して 1 worker に渡し、進捗をジャーナルで追う方が適しています。

---

## 4. Auto-create app（自然言語 → scaffold）をしない

**やらないこと**: 「Twitter クローンを作って」のような自然言語からプロジェクト雛形を生成する機能。

**理由**: claude-org-ja は operational discipline framework であり、scaffold generator ではありません。scaffold は「最初の 5 分」を短くするツールで、claude-org-ja の主戦場である「長期運用での discipline 維持」とは関心領域が異なります。両方を 1 つのツールに混ぜると、どちらの責務もぼやけます。

**代替手段**: scaffold が必要なら `create-react-app` / `cargo new` / `npm init` 等の専用ツールを使い、その後 claude-org-ja で組織運用を始めてください。

---

## 5. Multi-provider（Aider / Codex / Gemini 切替）をしない

**やらないこと**: Claude Code 以外の LLM（OpenAI / Gemini / DeepSeek 等）を主役 worker として切り替え可能にすること。

**理由**: claude-org-ja は **Claude-only** で position を取っています。Multi-provider 化は魅力的に見えますが、provider ごとに permission モデル / hook 機構 / MCP 互換性 / context window 形状 / tool-use 仕様が異なり、「discipline を強制する」という framework の本質が provider 数だけ薄まります。Claude Code に深く張ることで、`renga-peers` MCP / hook / settings schema / sandbox 等の Claude Code-native な discipline を最大限活用できます。

**代替手段**: review / second-opinion 用途に限り、`codex:rescue` / `codex` セルフレビュー gate のような **optional な review hook** として他 provider を呼ぶことは想定範囲です（主役ではなく補助）。多様な provider を主役で使い分けたい場合は、汎用エージェントフレーム（Aider / LangGraph / CrewAI 等）の方が適合します。

---

## 6. ai-session crate 相当の PTY / multiplexer 層を持たない

**やらないこと**: PTY（pseudo terminal）操作・ペイン分割・キーストローク注入の low-level 実装をこのリポジトリに持つこと。

**理由**: PTY / multiplexer 層は **Layer 3 = `renga`**（`suisya-systems/renga`）に分離されています。claude-org-ja は Layer 4 = 「Claude Code CLI を素で叩く運用層」であり、low-level な terminal 制御は依存先に責務を譲ります。同一リポジトリで両方を抱えると、運用 discipline の改修と PTY バグ修正が干渉して release tempo が落ちます。

**代替手段**: ペイン操作・構造化 spawn・ピア通信は `renga-peers` MCP（Layer 3 提供）の 14 種ツールを通じて利用してください。

---

## 7. Benchmark suite（SWE-Bench スコア等）を持たない

**やらないこと**: agent 性能比較用のベンチマーク実行 / スコア公開機能。

**理由**: claude-org-ja は agent 性能比較フレームワークではありません。「窓口の指示が worker でどう実行されたか」「raw 知見が curated に正しく昇華したか」のような **運用ロジックの正しさ**は対象ですが、「Claude Code がベンチマークで何点取るか」は Claude Code 自体の評価であり、claude-org-ja の射程外です。

**代替手段**: SWE-Bench / HumanEval 等の標準ベンチマークは Anthropic 側 / 専用 OSS（`swe-bench` 等）を使ってください。

---

## 8. 34 stack × prompt template bundle を持たない

**やらないこと**: 「Next.js 用」「Rails 用」「Django 用」など、フレームワーク別の prompt テンプレート集を framework 同梱で配布すること。

**理由**: claude-org-ja は **per-project 文脈構築** を主役にする設計です。`CLAUDE.md` と `WORKER_DIR/CLAUDE.local.md` が project 固有の正典になり、stack 別 prompt は「最大公約数の汎用文」になりがちで、project 固有の context を希釈します。

**代替手段**: stack 別 prompt が必要なら、project の `CLAUDE.md` に各自記述するか、外部の prompt 集（Awesome Prompts 系）を別途参照してください。

---

## 9. `tools` frontmatter allowlist 形式を採らない

**やらないこと**: skill / agent 定義ファイルの frontmatter で `tools: [Read, Edit, Bash]` のように、ツール許可をファイル単位で宣言する公式形式。

**理由**: claude-org-ja は **`settings.local.json` + deny hook で per-task 制御**します。frontmatter ベースの allowlist は static で「いつ・どの worker が・どこの WORKER_DIR で」の動的境界を表現できません。同じ skill でも task によって許可境界が変わるため、role × task の 2 軸で動的に決定する設計を採用しています。

**代替手段**: ツール許可の追加が必要なら、`tools/role_configs_schema.json` を更新してください（ルール追加フロー: schema → docs → 実 settings.local.json の順、§README の「ロール別設定の source of truth」参照）。

---

## 10. `--add-dir` による横断アクセスを既定許可しない

**やらないこと**: worker が `WORKER_DIR` 外のディレクトリ（他 worker の作業領域、リポジトリ外のホームディレクトリ等）に自由にアクセスすること。

**理由**: claude-org-ja は **`WORKER_DIR` boundary を強制境界**としています。worker 間で work-tree や状態を共有すると、並列作業時の race condition や、誤って他 worker の commit を上書きする事故が起こります。境界を緩めるたびに「どの worker が何を見たか」を追跡するコストが増えます。

**代替手段**: 共有が必要な情報は `knowledge/curated/` や `registry/` 等の窓口管理領域に置き、Foreman / 窓口経由でのみ書き換えてください。

---

## 11. 公式 bundled skills（`/simplify` 等）を再実装しない

**やらないこと**: Claude Code 公式に bundled されている skill 群（`simplify` / `init` / `review` / `security-review` 等）の機能を claude-org-ja 側で再実装すること。

**理由**: 公式 skill に乗っかる方針です。再実装すると公式アップデートのたびに追従コストがかかり、しかもユーザーから見ると「公式版と何が違うのか」が分かりにくくなります。claude-org-ja のスコープは公式が提供しない運用 discipline 層に絞ります。

**代替手段**: 公式 skill はそのまま使ってください。組織運用文脈で公式 skill を呼び出すラッパーが必要な場合のみ、`/org-*` 系として薄く wrap します（例: `/org-retro` は振り返りの組織化 wrapper）。

---

## 12. MCP HTTP server 形式の external integration を持たない

**やらないこと**: MCP を HTTP で外部公開し、ブラウザ拡張や別マシンの IDE から接続できるようにすること。

**理由**: claude-org-ja の MCP は `renga-peers`（local stdio）に集約されており、**同一タブ内 P2P** が通信モデルの正本です。HTTP 公開は認証・rate limit・TLS・ネットワーク境界という別レイヤの問題を呼び込み、「local で完結する operational discipline」というシンプルな保証が崩れます。

**代替手段**: 別マシンや別タブからの監視は、状態ファイル（`.state/`）と dashboard（`/org-dashboard`）経由で行ってください。リアルタイム外部統合が必要なら、別途 MCP HTTP server を併設する設計も可能ですが、claude-org-ja 本体の責務外とします。

---

## 改訂履歴

- 2026-04-27: 初版（Issue #107 README 全面書き換えに伴い分離）
