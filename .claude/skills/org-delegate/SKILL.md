---
name: org-delegate
description: >
  ワーカーClaudeを派遣して作業を委譲する。窓口は司令塔であり、
  手を動かす実作業は原則としてワーカーに任せる。
  ユーザーから作業の依頼を受けたとき、ファイル編集・実装・調査等の
  実作業が発生する場合に発動する。
---

# org-delegate: ワーカー派遣

作業をワーカーClaudeに委譲する。窓口はタスク分解と準備だけ行い、
ペイン起動・指示送信はディスパッチャーに委託する。これにより窓口のロック時間を最小化する。

## 窓口とディスパッチャーの役割分担

| 工程 | 担当 |
|---|---|
| プロジェクト名前解決 | **窓口** |
| work-skill 検索 | **窓口**（新規追加） |
| タスク分解 | **窓口** |
| CLAUDE.md 生成 | **窓口** |
| ディスパッチャーへの依頼 | **窓口**（ここで窓口は解放される） |
| ペイン起動 | **ディスパッチャー** |
| ピア待ち・指示送信 | **ディスパッチャー** |
| 状態記録 | **ディスパッチャー** |
| 窓口への派遣完了報告 | **ディスパッチャー** |
| ワーカーからの進捗/完了報告の受信 | **窓口** |
| ワーカー完了時のペインクローズ | **ディスパッチャー**（窓口から依頼） |

## 委譲前チェックリスト（窓口が実行）

タスク分解に入る前に、依頼内容を以下の観点で確認する。該当する場合はユーザーに聞き返す。

| チェック項目 | 確認すべき状況 | 例 |
|---|---|---|
| **曖昧な用語・略語** | ツール名・サービス名・略語が複数の意味を持ちうる場合 | 「gog」→ Google OAuth? gog CLI? |
| **OS固有の前提条件** | OS別の成果物を作る場合、デフォルト設定の明示が必要 | Mac=zsh、Windows=py -3、パス区切り |

- 曖昧な用語がある場合: 「○○は△△のことですか？」とユーザーに確認してから進める
- OS別タスクの場合: Step 1 のタスク分解時に、OS固有の前提条件をワーカーへの指示に含める

## Step 0: プロジェクト名前解決（窓口が実行）

ユーザーの依頼からプロジェクトを特定する:

1. `registry/projects.md` を読む
2. 依頼に含まれるキーワードから該当プロジェクトを特定する（通称・プロジェクト名・説明から照合）
3. 特定できた場合はそのパスを使う
4. 特定できない場合は登録済みプロジェクトの通称一覧を提示し、選ばせる
5. 新規プロジェクトの場合:
   - パスをユーザーに確認する
   - 通称・説明・よくある作業例を推定し、ユーザーに確認してから `registry/projects.md` に追記する

## Step 0.5: work-skill 検索（窓口が実行）（新規追加）

タスク分解の前に、関連する既存の work-skill がないか検索する。
マッチした work-skill はワーカーへの指示に参考情報として含める。

### 検索手順

1. `.claude/skills/` 配下の全 SKILL.md ファイルを列挙する
2. 各 SKILL.md の frontmatter を読み取る:
   - `type` フィールドで work-skill を識別する（org- プレフィックスでないもの）
   - `description` と `triggers` をタスク内容と照合する
3. マッチング判定:
   - ユーザーの依頼内容と `description` のキーワードを比較する
   - `triggers` リストに依頼内容と合致する記述があるか確認する
   - 完全一致は不要。関連性があれば候補に含める

### マッチ結果の活用

**マッチした場合:**
- 人間に通知する:
  ```
  関連するwork-skillが見つかりました:
  - {skill-name}: {description の1行目}
  ワーカーへの指示に参考情報として含めます。
  ```
- Step 1 のタスク分解時に、work-skill の手順を参考にする
- Step 1.5 の CLAUDE.md 生成時に、以下のセクションを追加する:
  ```markdown
  ## 参考 work-skill
  以下の work-skill が参考になる可能性があります。手順や判断基準を参照してください。
  ただし、タスクの要件に合わない部分は適宜調整すること。
  
  - スキル名: {skill-name}
  - パス: .claude/skills/{skill-name}/SKILL.md
  - 概要: {description}
  ```
- ワーカーへの指示（instruction-template）にも参考スキルの存在を明記する

**マッチしなかった場合:**
- 通知不要。そのまま Step 1 に進む

### 検索の注意点

- work-skill の手順をそのままコピーしない。参考情報として提示し、ワーカーが判断する
- 複数マッチした場合は関連度順に全て含める
- org- プレフィックスのスキル（org-retro, org-delegate 等）は組織運営スキルなので検索対象外

## Step 0.7: ターゲットファイル gitignore 事前チェック（窓口が実行）

> **この Step は Step 1 のディレクトリパターン判定に先行する最上位判定である**。Step 1 の A / B / C ヒューリスティックに入る前に、編集対象ファイルが `.gitignore` で除外されていないかを確認し、ignored なら **Pattern C 強制（gitignored サブモード）** に分岐させる。operator が見落とすと tracked 用の Pattern B / A 経路に落ちて対象ファイルに届かないワーカーを派遣することになるため、必ずここで判定する。

**「対象ファイル」は窓口がタスク説明から抽出する**（依頼文・Issue 本文・ユーザー発話の中で明示されたパス。機械的判定はしない）。対象ファイルが特定できないタスク（純粋な調査、対象パス未定の新規作成など）はこのチェックをスキップし、Step 1 の通常判定に進む。

### 適用条件

このチェックは **ローカルに git repo が既に存在するプロジェクトでのみ実行する**。具体的には:

- registry/projects.md の「パス」がローカル絶対パスで、かつ `.git/` を持つディレクトリ（または worktree）として解決できる場合のみ実行
- パスが URL（未 clone）/ `-` / 解決不能なら **チェック自体をスキップ**して Step 1 の通常判定へ（初回 clone 後の状態は git の通常挙動に従うため、tracked 既存ファイルが gitignored になっているレアケースは別途レビューで拾う）

### 判定コマンド

ローカル repo root（=「パス」が指す絶対パス）で:

```
git -C {project_path} check-ignore -q -- <target>
```

- 終了コード 1（=ignored ではない）→ tracked または「単に未存在の新規ファイル」。**Step 1 の通常 A / B / C 判定に進む**
- 終了コード 0（=ignored）→ **Pattern C 強制（gitignored サブモード）**。下記参照。Step 1 の通常判定はスキップする
- 終了コード 128 等（コマンド失敗、repo 未初期化など）→ 適用条件外。スキップして Step 1 の通常判定へ

> `git check-ignore` は「現在の `.gitignore` ルールにマッチするか」だけを判定し、ファイルが実在しなくても評価できる。`ls-files --error-unmatch` を使うと「単に未作成の新規ファイル」まで untracked 扱いで Pattern C に落としてしまうため、こちらを使わない。

### Pattern C 強制（gitignored サブモード）

通常の Pattern C は `{workers_dir}/{task_id}/` のエフェメラル空ディレクトリだが、gitignored 対象を編集する場合はそれでは対象ファイルに届かない。次の特例運用とする:

- **WORKER_DIR**: 既存ローカル clone の **repo root を直接指定**する（registry の「パス」値そのもの）
- **CLAUDE.md / settings.local.json の配置先**: その repo root 直下。既に他用途の CLAUDE.md がある場合は `CLAUDE.local.md` に書く（`references/claude-org-self-edit.md` の特例参照）
- **Worker Directory Registry**: Pattern を `C` として登録、Directory に repo root の絶対パス、Status を `in_use`。完了時はエントリ削除（ディレクトリ自体は元プロジェクトなので保持）
- **並行作業との競合**: repo root を直接掴むため、同 repo に対する Pattern A / B のワーカーと同時起動はしない（窓口側で順次化する）
- **窓口メモ**: 「Pattern B 不可: 対象 `<target>` が gitignored。WORKER_DIR=既存 repo root 運用」と一文残す

ここで Pattern C 強制が確定した場合、**Step 1 のディレクトリパターン判定基準テーブルおよび判定フローはスキップ**し、そのまま Step 1.5 のワーカーディレクトリ準備に進む（パターンは C 確定）。

### claude-org 自己編集との関係

通常のスキル / ドキュメント編集（`.claude/skills/...`, `references/...`）は tracked なので従来どおり Pattern B が選べる。`docs/internal/`, `notes/`, `tmp/` 等の gitignored 内部メモを編集する場合のみ本事前チェックで Pattern C 強制（gitignored サブモード）となる（`references/claude-org-self-edit.md` 参照）。

## Step 1: タスク分解（窓口が実行）

人間の依頼を分析し、ワーカーに委譲するタスクを定義する:

- 各タスクに一意のIDを振る（依頼内容から連想しやすい英語 kebab-case。例: `data-analysis`, `login-fix`, `dashboard-redesign`）
  - 既存のIDと重複しないよう `.state/org-state.md` を確認する（重複時はサフィックスで区別: `login-fix-2`）
- タスクごとに以下を明確にする:
  - 目的（何を達成するか）
  - 成果物（何ができあがるか）
  - 作業ディレクトリ（どのプロジェクトで作業するか）
  - 制約（ブランチ名、コーディング規約、依存関係等）
  - **検証深度（`full` / `minimal`）** — 派遣指示には必ずどちらか 1 値を明示する。既定は `full`（コード・挙動の変更を伴うタスクはすべてこちら）。`full` では **codex の有無に関わらず** リポジトリ通常検証（テスト / lint / type-check 等）を green まで実行し通常の完了報告フォーマットで報告するのが必須ゲート。**追加ゲート（任意）** として、codex CLI が available なら commit 完了後に Codex セルフレビュー・同一指摘 3 ラウンド上限のルールが走る（未導入環境では skip）。trivial fix（CI 出力整形 / typo / コメント修正 / 既存テスト形式合わせ等）のみ `minimal` を選択し、ワーカーは `git add` → `git commit` → `done` 報告のみで終わる。詳細は `references/instruction-template.md` の「検証深度」節と `references/worker-claude-template.md` の「Codex セルフレビュー手順」節参照。値の決定は窓口の責任で、ワーカーには判断させない
  - **ディレクトリパターン（A / B / C）** — 以下の判定基準で決定する（**Step 0.7 の事前チェックで Pattern C 強制が確定している場合はこの判定をスキップ**）
  - **参考 work-skill**（Step 0.5 でマッチしたもの）
- 注意: タスク説明にファイルパスを含める場合、それがワーカー作業ディレクトリからの相対パスであることを明記する。registry/projects.md の「パス」列の値をそのまま成果物パスとして指示しない（ワーカーが別の場所にパスを作成する原因になる）

### ディレクトリパターン判定基準

| パターン | 名称 | 条件 | ディレクトリ |
|---|---|---|---|
| A | プロジェクトディレクトリ | プロジェクトの clone が必要（初回は clone、2回目以降は再利用） | `{workers_dir}/{project_slug}/` |
| B | worktree | 同一プロジェクトで並行作業が必要（既に別ワーカーが同じプロジェクトディレクトリを使用中） | `{workers_dir}/{project_slug}/.worktrees/{task_id}/` |
| C | エフェメラル | 成果物を残す必要がない一時作業（調査・検証等） | `{workers_dir}/{task_id}/` |

**判定フロー:**

> **前提**: Step 0.7 のターゲットファイル gitignore 事前チェックが先行する。Step 0.7 で Pattern C 強制（gitignored サブモード）が確定している場合は本判定フローには入らない。Step 0.7 が「ignored ではない」または「適用条件外（URL のみ・対象未特定など）」で抜けた場合のみ、以下の通常判定を行う。

1. プロジェクトの clone が必要な場合（registry/projects.md にパスが登録されているプロジェクト）:
   a. Worker Directory Registry で同プロジェクトに `in_use` のエントリがある場合 → **パターン B**（worktree で並行作業）
   b. 同プロジェクトに `available` のエントリがある場合 → **パターン A**（既存ディレクトリを再利用）
   c. エントリがない場合 → **パターン A**（新規 clone）
2. 上記に該当しない場合 → **パターン C**
   - clone 不要の一時作業、成果物不要の調査タスク等

## Step 1.5: ワーカーディレクトリ準備（窓口が実行）

各タスクのワーカー専用ディレクトリを準備し、CLAUDE.md と設定を配置する。
テンプレートは references/worker-claude-template.md を使用する。
**パターン（A/B/C）によって手順が異なる。**

> **claude-org 自身を編集するタスクの場合**: 通常手順に加えて `references/claude-org-self-edit.md` の特例手順（block-org-structure.sh hook の除外、CLAUDE.local.md への指示記述、ルート CLAUDE.md を無視する旨の明示）を必ず適用すること。**本セクション以下で「CLAUDE.md を生成 / 配置 / 確認」と書かれている箇所はすべて `CLAUDE.local.md` に読み替える**（ルート CLAUDE.md は Secretary 用なので上書き禁止）。

### 共通手順（全パターン）

1. `registry/org-config.md` の `workers_dir` を読み、リポジトリルートからの相対パスを絶対パスに解決する

### ワーカーロール (`<ROLE>`) の選び方

`.claude/settings.local.json` の生成は schema-driven generator (`claude-org-runtime settings generate`、Phase 4 で in-tree `tools/generate_worker_settings.py` から PyPI パッケージ `claude-org-runtime` に移行済み) に委ねる。窓口は **タスク特性に応じて 1 つの role を選ぶだけ** で、permission の手書き編集は禁止（schema → settings の drift は CI で fail する）。

#### 事前判定: self-edit タスクか？（必須・最優先）

下記 role 表に進む前に、まずこの判定を行う。self-edit タスクなら role は `claude-org-self-edit` に **固定** され、`default` / `doc-audit` の検討に入らない。

> **Q.** これは self-edit タスクか？（`worker_dir` が claude-org リポジトリ本体、またはその worktree（例: `.worktrees/{task_id}/`）を指すか）
> - **Yes** → role を `claude-org-self-edit` に固定する（`block-org-structure.sh` hook を外す特例が必要なため）。`settings.local.json` 生成前にこの role を確定させ、加えて `references/claude-org-self-edit.md` の特例手順（CLAUDE.local.md への指示記述、ルート CLAUDE.md を無視する旨の明示など）を併せて適用する
> - **No** → 通常タスク。下記 role 表に従って `default` / `doc-audit` から選ぶ

判定根拠: claude-org 自身を編集する場合、Pattern A / B / C いずれであっても `worker_dir` は claude-org repo（または worktree）配下となり、`block-org-structure.sh` がワーカーの編集を拒否してしまう。これを安全に外せるのは `claude-org-self-edit` role だけなので、operator が role を取り違えると Pattern 判定が正しくてもワーカーが立ち上がらない。

| Role | 用途 |
|---|---|
| `default` | 通常の実装・修正タスク（git commit / branch 操作あり、push 不可、recursive delete 不可）。**self-edit タスクには使わない** |
| `claude-org-self-edit` | **claude-org リポジトリ自身を編集するタスク（self-edit task）**。`worker_dir` が claude-org repo or its worktree（`tools/`, `.claude/skills/`, `docs/` 等の編集を含む）。`block-org-structure.sh` を外す代わりに `check-worker-boundary.sh` で境界を担保。詳細は `references/claude-org-self-edit.md` |
| `doc-audit` | 読み取り中心の調査・監査・レポート（Edit/Write/MultiEdit/NotebookEdit を deny。commit / branch も禁止） |

各 role の具体的な allow/deny/hooks は `claude-org-runtime の settings/role_configs_schema.json` の `worker_roles[<role>]` を参照（schema が SOT）。新しいパターンが必要な場合は schema に role を追加する PR を起こすこと（窓口の手書き拡張は不可）。

### パターン A: プロジェクトディレクトリ

プロジェクト専用ディレクトリ（`{workers_dir}/{project_slug}/`）を使う。初回は clone、2回目以降は再利用。

**初回（ディレクトリが存在しない場合）:**

1. `git clone {project_path} {workers_dir}/{project_slug}/` を実行
2. ディレクトリ直下に CLAUDE.md を生成する（テンプレートの変数を置換）
3. ディレクトリ直下に `.claude/settings.local.json` を **generator で生成する**（schema が SOT。詳細は `claude-org-runtime の settings/role_configs_schema.json` の `worker_roles` を参照）:
   ```bash
   claude-org-runtime settings generate \
     --role <ROLE> \
     --worker-dir {worker_dir} \
     --claude-org-path {claude_org_path} \
     --out {worker_dir}/.claude/settings.local.json
   ```
   `<ROLE>` の選び方は本 Step 冒頭の「ワーカーロール (`<ROLE>`) の選び方」表を参照。手書き JSON は禁止（drift CI が fail する）。
4. `.state/org-state.md` の Worker Directory Registry に登録する

**再利用（ディレクトリが存在し、ステータスが `available` の場合）:**

1. `git -C {workers_dir}/{project_slug}/ fetch origin` で最新化
2. CLAUDE.md **のみ**を再生成する（新しいタスクID・タスク説明で上書き）
   - settings.local.json はそのまま流用（`{worker_dir}` が変わらないため再生成不要）
3. `.state/org-state.md` の Worker Directory Registry を更新する（新タスクIDを紐付け、ステータスを `in_use` に変更）

### パターン B: worktree

同一プロジェクトで並行作業が必要な場合、プロジェクトディレクトリを base clone として worktree を作成する。

1. base clone（`{workers_dir}/{project_slug}/`）の存在確認:
   - 存在しない場合 → `git clone {project_path} {workers_dir}/{project_slug}/` を実行
   - 既に存在する場合 → `git -C {workers_dir}/{project_slug}/ fetch origin` で最新化
2. worktree を作成:
   - `git -C {workers_dir}/{project_slug}/ worktree add .worktrees/{task_id} -b {branch_name}` を実行
   - `{branch_name}` は Step 1 で決定したブランチ名（指定がなければ `{task_id}` をブランチ名に使う）
   - ワーカーディレクトリ: `{workers_dir}/{project_slug}/.worktrees/{task_id}/`
3. worktree 直下に CLAUDE.md を生成する（テンプレートの変数を置換）
4. worktree 直下に `.claude/settings.local.json` を **generator で生成する**（schema-driven。詳細は `claude-org-runtime の settings/role_configs_schema.json` の `worker_roles` 参照）:
   ```bash
   claude-org-runtime settings generate \
     --role <ROLE> \
     --worker-dir {worker_dir} \
     --claude-org-path {claude_org_path} \
     --out {worker_dir}/.claude/settings.local.json
   ```
   `<ROLE>` の選び方は本 Step 冒頭の「ワーカーロール (`<ROLE>`) の選び方」表を参照。手書き JSON は禁止。
5. `.state/org-state.md` の Worker Directory Registry に登録する

### パターン C: エフェメラル

成果物を残す必要がない一時作業（調査・検証等）で使用する。

1. `{workers_dir}/{task_id}/` ディレクトリを作成する（例: `../workers/data-analysis/`）
2. テンプレートから `{workers_dir}/{task_id}/CLAUDE.md` を生成する
3. `{workers_dir}/{task_id}/.claude/settings.local.json` を **generator で生成する**（schema-driven。詳細は `claude-org-runtime の settings/role_configs_schema.json` の `worker_roles` 参照）:
   ```bash
   claude-org-runtime settings generate \
     --role <ROLE> \
     --worker-dir {worker_dir} \
     --claude-org-path {claude_org_path} \
     --out {worker_dir}/.claude/settings.local.json
   ```
   `<ROLE>` の選び方は本 Step 冒頭の「ワーカーロール (`<ROLE>`) の選び方」表を参照。手書き JSON は禁止。
4. `.state/org-state.md` の Worker Directory Registry に登録する

### 共通手順（全パターン・配置後）

CLAUDE.md テンプレートの変数を実際の値で置換する（settings.local.json の置換は generator が自動で行うため対象外）:
- `{project_name}` → registry の通称
- `{project_description}` → registry の説明
- `{task_id}` → タスクID（例: `data-analysis`）
- `{task_description}` → タスクの目的と成果物
- `{claude_org_path}` → claude-org リポジトリの絶対パス
- `{worker_dir}` → ワーカーディレクトリの絶対パス（パターンにより異なる、上記参照）

生成した CLAUDE.md に「作業ディレクトリ（最重要制約）」セクションが含まれていることを確認する。含まれていない場合はテンプレート適用ミスのため再生成する

**参考 work-skill がある場合（Step 0.5 でマッチ）:**

CLAUDE.md に以下のセクションを追加する（「参照すべきファイル」セクションの後に配置）:

```markdown
## 参考 work-skill
以下の work-skill が参考になる可能性があります。手順や判断基準を参照してください。
ただし、タスクの要件に合わない部分は適宜調整すること。

- スキル名: {skill-name}
- パス: {claude_org_path}/.claude/skills/{skill-name}/SKILL.md
- 概要: {description}
```

## Step 2: ディスパッチャーへの委託（窓口が実行 → ここで窓口は解放）

renga-peers の `send_message` でディスパッチャー（pane name=`dispatcher`）に以下を送信する:

```
DELEGATE: 以下のワーカーを派遣してください。

タスク一覧:
- {task_id}: {タスク説明}
  - ワーカーディレクトリ: {ワーカーディレクトリの絶対パス}（CLAUDE.md・設定配置済み）
  - ディレクトリパターン: {A: プロジェクトディレクトリ / B: worktree / C: エフェメラル}
  - プロジェクト: {clone先URL or ローカルパス or 新規作成 or worktree済み or 前タスク引継ぎ}
  - Permission Mode: {org-config から読んだ default_permission_mode の値}
  - 検証深度: {full | minimal}（instruction-template の同名行と同じ値を必ず記入。ディスパッチャーはこの値をワーカーへの指示にそのまま転記する）
  - 指示内容: {instruction-template に基づく指示の要約。「検証深度」行は必ず残したまま転送する}

窓口ペイン名: `secretary`（renga layout で登録済み。新規タブ作成時の基準となる）
```

**窓口はこの送信後すぐにユーザーとの対話に戻れる。**
ユーザーには「ディスパッチャーに派遣を依頼しました。準備ができ次第報告します。」と伝える。

> renga では窓口・ディスパッチャー・キュレーター等の「長寿命ペイン」は安定名 (`--id`) で addressable。
> 窓口 (`secretary`) / ディスパッチャー (`dispatcher`) / キュレーター (`curator`) は `/org-start` で命名済み。

## Step 3: ワーカー起動と指示送信（ディスパッチャーが実行）

ディスパッチャーが以下を実行する:

### 3-1. balanced split で target / direction を決める

旧設計は序数 `k` ベースの lookup table で target を決めていたが、ワーカーが途中で閉じた後の再派遣や想定外の退役順でテーブル前提と実レイアウトが乖離し、`[split_refused]` を誘発しやすかった。renga-peers MCP の `mcp__renga-peers__list_panes` が各ペインの `id / name / role / focused / x / y / width / height` (cell 単位) を返すため、**現在のレイアウト (rect) から動的に target と direction を選ぶ方式**を取る。詳細ルールは `references/pane-layout.md` の「ワーカーの balanced split 戦略」セクションを参照。

#### 3-1a. レイアウト取得

`mcp__renga-peers__list_panes` を呼び、返却テキストから全ペインの属性を抽出する。各ペインは以下のフィールドを持つ:

- `id`: 整数
- `name`: 文字列（`spawn_pane` / `new_tab` で明示指定されたペインのみ、未設定なら省略）
- `role`: 文字列 ("secretary" / "dispatcher" / "curator" / "worker" のいずれか。未設定なら省略)
- `focused`: bool（出力行に `(focused)` が付くかで判断）
- `x / y / width / height`: cell 単位の整数

#### 3-1b. balanced split アルゴリズム（Claude が判定ロジックを実行）

**定数**:
- `MIN_PANE_WIDTH = 20` / `MIN_PANE_HEIGHT = 5`: renga 側の分割下限（findings: renga-split-inv）
- `SECRETARY_MIN_WIDTH = 125` / `SECRETARY_MIN_HEIGHT = 45`: secretary を分割候補にしてよい最小幅・最小高さ（保険条項、実運用ではほぼ不発動）

**Step 1. curator を特定**: `role == "curator"` のペインを 1 つ選ぶ（複数あれば先頭）。以降 `$curator` と呼ぶ。存在しなければ `$curator = null`。

**Step 2. 候補を絞り込む**:
- `role ∈ {"secretary", "dispatcher", "worker"}` のペインのみ候補
- `role == "dispatcher"` のペインは、**`$curator` と rect 隣接している場合のみ**残す（`$curator = null` なら dispatcher も除外）
  - rect 隣接の定義（どちらかを満たす）:
    - **縦辺共有 + y 区間重なり**: `a.x + a.width == b.x` または `b.x + b.width == a.x`、かつ `max(a.y, b.y) < min(a.y + a.height, b.y + b.height)`
    - **横辺共有 + x 区間重なり**: `a.y + a.height == b.y` または `b.y + b.height == a.y`、かつ `max(a.x, b.x) < min(a.x + a.width, b.x + b.width)`

**Step 3. 各候補に direction / new_w / new_h / metric を付与**:
- `direction = (width > height * 2) ? "vertical" : "horizontal"`
  - ターミナル cell は縦:横 ≈ 2:1（文字が縦長）。`width > height*2` は物理的に横長 → vertical（左右分割）で綺麗に割れる
  - それ以外は horizontal（上下分割）
- `new_w = (direction == "vertical") ? floor(width / 2) : width`
- `new_h = (direction == "horizontal") ? floor(height / 2) : height`
- `metric = (direction == "vertical") ? new_w : new_h`（分割軸方向の新サイズ）

**Step 4. MIN_PANE 制約**:
- `new_w >= MIN_PANE_WIDTH` かつ `new_h >= MIN_PANE_HEIGHT` のペインのみ残す

**Step 5. secretary 保険条項**:
- `role == "secretary"` のペインは `new_w >= SECRETARY_MIN_WIDTH` **かつ** `new_h >= SECRETARY_MIN_HEIGHT` のときだけ残す（width だけ通っても height が足りなければ除外）

**Step 6. ソート & 選択**:
- `metric` の降順、tie-break は `id` の昇順
- 先頭要素の `name` を `$target`、`direction` を `$direction` として使用

初回（ワーカー 0 人）は dispatcher が唯一の候補として残り、direction は dispatcher の aspect ratio から決まる（典型的に横長なので vertical）。

#### 3-1c. 候補が空だった場合

`$target` が空（候補セットが空）の場合、ディスパッチャー Claude は **`spawn_pane` を発行せず**、代わりに renga-peers で窓口 (`secretary`) に escalate メッセージを送信する:

1. `mcp__renga-peers__send_message(to_id="secretary", message=...)` を呼び、本文を以下にする:
   ```
   SPLIT_CAPACITY_EXCEEDED: {task_id} のワーカー分割対象が見つからない。
   rect ベース balanced split の MIN_PANE / 隣接条件を満たす候補が 0。
   ターミナルサイズ不足または想定外のレイアウトが疑われる。人間判断が必要です。
   ```
2. 3-2 以降（`spawn_pane` / 起動確認 / `list_peers` 待ち / instruction 送信）は **skip** する。該当ワーカー 1 件だけ派遣を中止し、ディスパッチャー本体の監視ループは **継続**させる。`exit` / `return` などでディスパッチャーを落とさないこと

### 3-2. ワーカーペインを起動する

3-1 で算出した `$target` / `$direction` を使って `mcp__renga-peers__spawn_claude_pane` を呼ぶ。**`$target` が空なら spawn せず 3-1c の escalate 手順に従う**:

```
mcp__renga-peers__spawn_claude_pane(
  target=$target,                         # 3-1 で算出した既存ペイン名
  direction=$direction,                   # "vertical" or "horizontal"
  role="worker",
  name="worker-{task_id}",                # 後続操作で参照する安定名。英字含む前提
  cwd="{workers_dir}/{task_id}",          # 絶対パス推奨。相対は caller pane の cwd 基点
  permission_mode="{default_permission_mode}",
  model="opus"                            # 必須。sonnet 禁止（auto classifier が不安定）
)
```

- **`model="opus"` は必須（sonnet 禁止）。** ワーカーの permission_mode `auto` の safety classifier は Opus でのみ安定動作するため、sonnet だと分類器が誤判定を多発し承認フローが崩れる。ディスパッチャーだけは `bypassPermissions` 固定で分類器非経由のため sonnet 運用で問題ない
- ペイン配置ルールは `references/pane-layout.md` を参照。rect ベースの target / direction 選出ルールはそちらに集約
- **同一タブ内 spawn で起動する理由**: renga の `list_panes` / `focus_pane` / `send_message` / `inspect`（CLI） は現在フォーカス中のタブのペインしか見えない。`new_tab` で別タブに置くとディスパッチャーからの監視・指示送信が不能になる（renga 側 issue: suisya-systems/renga#71）
- `name="worker-{task_id}"`: 後続の `mcp__renga-peers__send_message(to_id="worker-{task_id}", ...)` や `close_pane(target="worker-{task_id}")` で addressable にする安定名。**全桁数字は id 扱いになる** ので、`worker-` プレフィックス等で英字を必ず含める
- `role="worker"`: `list_panes` の結果で役割識別（次回以降の balanced split の target 選出にも使われる）
- `cwd` / `permission_mode` / `model` / `args[]` は `spawn_claude_pane` の構造化フィールド。renga が `claude --permission-mode {mode} --dangerously-load-development-channels server:renga-peers ...` を合成する。旧方式（`cd`-プレフィックス付き command 文字列を `spawn_pane` に渡す）は **禁止** — cwd 変更プレフィックスがあると renga の bare-`claude` auto-upgrade が発動せず、channel push が失われる
- 起動コマンドの仕様は `.claude/skills/org-start/SKILL.md` の「ClaudeCode 起動コマンド（役割別）」セクションを参照
- `spawn_claude_pane` が内部で `--dangerously-load-development-channels` を付与するため、`Load development channel?` 確認プロンプトが初回表示される。Step 3-3b で `send_keys(enter=true)` による承認が必要
- **エラーハンドリング**: MCP 結果テキストに `[<code>] <msg>` 形式でエラーが埋まる。主な code:
  - `[split_refused]` (MAX_PANES / too small): `references/renga-error-codes.md` の手順に従いキュレーター → 窓口に escalate。balanced split は best-effort の配置ヒントであり、想定外のレイアウト（途中でワーカーが閉じた後の再派遣など）では拒否され得る
  - `[pane_not_found]`: `$target` に選んだ既存ペインが spawn 発行直前に閉じたレース。同じくエラーコード経路で escalate
  - `[cwd_invalid]`: 指定した cwd が存在しない / ディレクトリでない。ペイン作成前に reject されるので half-mutated layout にはならない。窓口に escalate し、ワーカーディレクトリ準備（org-delegate Step 1.5）が完了しているか確認
  - `[invalid-params]`: `args[]` に `--permission-mode` / `--model` / `--dangerously-load-development-channels` を含めた場合の拒否。構造化フィールドで渡す
  - その他の code は `references/renga-error-codes.md` 参照

### 3-3. ペインが起動したことを確認

`mcp__renga-peers__poll_events` で `pane_started` イベントを最大 3 秒待つ。target 以外の worker の同時 spawn や filter 不一致イベント到着による early return に備え、**3 秒 deadline 内で再 poll するループ**として書く:

```
cursor = None                    # 初回は since 省略（「今以降のイベントだけ」セマンティクス）
deadline = now + 3 秒
while now < deadline:
    remaining_ms = (deadline - now) ミリ秒
    result = mcp__renga-peers__poll_events(
        since=cursor,                                  # 2 回目以降は前回の next_since
        timeout_ms=min(remaining_ms, 3000),
        types=["pane_started"]
    )
    cursor = result.next_since                          # 次呼び出しで使う
    for ev in result.events:
        if ev.name == "worker-{task_id}":
            return OK                                   # 起動確認完了
# deadline 超過 → 起動イベント未検出
# mcp__renga-peers__list_panes でペイン存在を再確認、未存在なら窓口にエスカレーション
```

- 初回 `since` 省略 = `renga events --timeout` と同じ「今以降」セマンティクス（過去の起動イベントを replay しない）
- `types=["pane_started"]` で他 type（`pane_exited` 等）を除外しつつ、cursor は全 type で advance（重複 scan なし）
- **filter 不一致イベントが到着すると long-poll が早期終了し `events:[]` + 進んだ cursor が返る**ので、空応答のままループ継続（cursor 保持で重複なし）
- `name == "worker-{task_id}"` の `pane_started` で break。deadline 超過で未検出なら `list_panes` で pane 存在を再確認

### 3-3b. 「Load development channel?」プロンプトを Enter で承認

`spawn_claude_pane` は内部で `--dangerously-load-development-channels server:renga-peers` を付与するため、初回起動で Y/n 確認プロンプトが出る。Enter で承認する:

```
mcp__renga-peers__send_keys(target="worker-{task_id}", enter=true)
```

承認しないと `server:renga-peers` チャネルが有効化されず、3-4 の `list_peers` 待ちがタイムアウトし、3-5 の `send_message` も届かない。Enter は CR (0x0D) として PTY に書き込まれる（byte-identical to renga `append_enter`）。

### 3-4. `mcp__renga-peers__list_peers` で新ピア出現を待機

pane は live でも Claude がまだ起動中の場合があるため二重確認。`mcp__renga-peers__list_peers` を呼び、`worker-{task_id}` が peer 一覧に現れるまで短い間隔（例: 2 秒）でリトライする（最大 30 秒程度）。タイムアウトした場合は `list_panes` でペイン状態を再確認し、必要なら窓口に escalate する。

### 3-5. `mcp__renga-peers__send_message` でワーカーに指示を送信

`references/instruction-template.md` のフォーマットに従う。`to_id="worker-{task_id}"` で pane name 指定。

### 3-6. 複数ワーカーの順次起動

複数ワーカーがある場合は 3-1〜3-5 を順次繰り返す。`list_panes` の結果が毎回変わるので、**都度再取得して** balanced split 判定をし直す（前ワーカーの起動が完了するのを 3-3 / 3-4 で待ってから次に進むこと）。

## Step 4: 状態記録（ディスパッチャーが実行）

各ワーカーについて:

1. `.state/workers/worker-{task_id}.md` を作成（renga-peers では pane name `worker-{task_id}` が安定識別子。旧 peer-id は使わない）:
   ```markdown
   # Worker: worker-{task_id}
   Task: {task_id}
   Directory: {作業ディレクトリ}
   Pane ID: {pane_id}
   Started: {ISO timestamp}

   ## Assignment
   {タスクの説明}

   ## Progress Log
   - [{time}] 派遣完了、作業開始
   ```

2. `.state/org-state.md` を更新（なければ新規作成）:
   - Current Objective に人間の依頼を記載
   - Active Work Items にタスクを追加

3. `.state/journal.jsonl` にイベント追記（helper 経由。`ts` は自動付与。Bash で生 JSON を `>>` で append しないこと）:
   ```bash
   bash tools/journal_append.sh worker_spawned worker=worker-{task_id} dir=<dir> task={task_id}
   ```
   event 名と payload key の規約は [`docs/journal-events.md`](../../../docs/journal-events.md) を参照。

4. `.state/org-state.md` を更新した後、JSON スナップショットを再生成する:

   ```bash
   py -3 dashboard/org_state_converter.py    # Windows
   python3 dashboard/org_state_converter.py   # Mac/Linux
   ```

5. ワーカーペインを監視対象として登録する:
   - 派遣後、そのペインを監視対象として記録し、`.dispatcher/CLAUDE.md` の「ワーカーペイン監視」に従って定期的に承認待ちを確認する

### Worker Directory Registry（org-state.md 内のセクション定義）

`.state/org-state.md` に以下のセクションを追加・管理する。ワーカーディレクトリの再利用状態を追跡する。

```markdown
## Worker Directory Registry

| Task ID | Pattern | Directory | Project | Status |
|---|---|---|---|---|
| blog-redesign | A | /path/to/workers/blog/ | blog | in_use |
| blog-auth-fix | B | /path/to/workers/blog/.worktrees/blog-auth-fix/ | blog | in_use |
| data-analysis | C | /path/to/workers/data-analysis/ | - | in_use |
```

**フィールド説明:**
- **Task ID**: 現在そのディレクトリを使用しているタスクID
- **Pattern**: A（プロジェクトディレクトリ）/ B（worktree）/ C（エフェメラル）
- **Directory**: ワーカーディレクトリの絶対パス
- **Project**: registry/projects.md の通称（エフェメラルで無関係なら `-`）
- **Status**: `in_use`（作業中）/ `available`（完了済み・再利用可能）

**運用ルール:**
- Step 1.5 でディレクトリ準備時にエントリを追加する
- Step 5 (2b-ii) でクローズ条件（PR マージ / 明示クローズ指示 / 長期 idle 判断）を満たした時にステータス更新・エントリ削除を行う（2b-i の PR 作成段階では触らない）
- Step 1 の判定フローでこのテーブルを参照し、再利用可能なディレクトリや並行作業の有無を判定する

5. 窓口 (`secretary`) に renga-peers で派遣完了を報告:
   ```
   DELEGATE_COMPLETE: {task_id} のワーカーを派遣しました。
   Pane: worker-{task_id} (id={pane_id})
   ```

## Step 5: 進捗管理（窓口が実行）

### DELEGATE_COMPLETE 受信時

ディスパッチャーから派遣完了報告を受け取ったら、各ワーカーに挨拶メッセージを送る:
```
mcp__renga-peers__send_message(
  to_id="worker-{task_id}",
  message="窓口です。{task_id} の作業をお願いしています。完了・進捗・ブロック、全ての報告は `to_id=\"secretary\"` で renga-peers 送信してください。"
)
```
ワーカー側は worker-claude-template の方針通り pane name `secretary` に固定で送るため、窓口の peer-id を履歴に残す必要はない（この挨拶はあくまで作業開始確認）。

### ワーカーからのメッセージ受信時

ワーカーから renga-peers でメッセージを受け取ったら:

1. 進捗報告の場合:
   - `.state/workers/worker-{task_id}.md` の Progress Log に追記
   - `journal.jsonl` にイベント追記
2a. ワーカーから完了報告を受け取ったら:
   - `org-state.md` の該当Work Itemを **REVIEW** に更新
   - `journal.jsonl` にイベント追記
   - JSON スナップショットを再生成する: `py -3 dashboard/org_state_converter.py`
   - 結果を人間に報告する
   - **ペインはまだ閉じない**

2b. 人間が承認した場合（「OK」「確認した」「問題ない」等）:

   2b-i. **PR 作成段階（即時実行）**:
   - 必要に応じて窓口がプッシュ・PR作成を行う（ワーカーには権限がないため）
   - `journal.jsonl` にイベント追記（push / PR open など）
   - PR 番号が確定したら `tools/pr-watch.ps1 <PR>` (Windows) / `tools/pr-watch.sh <PR>` (POSIX) で CI を監視する。完了時に `ci_completed` が自動で journal に記録される
   - `org-state.md` の該当Work Item は **REVIEW のまま据え置く**（GitHub 側 PR レビュー指摘が来たら同ペインで対応するため。COMPLETED への遷移は 2b-ii で行う）
   - **ペインはまだ閉じない**: PR 作成直後に `CLOSE_PANE` を送らない。worktree 除去・Worker Directory Registry 更新も 2b-ii まで遅延する
   - PR レビューで指摘が来た場合は 2c のフローで同ワーカーに `send_message` 追指示を送り、同ペインで修正コミットを積ませる（新ワーカー再派遣は避ける — Issue / diff / 判断境界の再構築コストを払うことになる）

   2b-ii. **最終クローズ段階（クローズ条件を満たしたら実行）**:

   クローズ条件（少なくとも 1 つ満たすこと）:
   - PR がマージされた（`gh pr view {n} --json mergedAt` 等で確認、または窓口がマージ通知を受ける）
   - ユーザーが明示的に「閉じてよい」「クローズして」「マージ済み」等の指示を出した
   - 24-48 時間レビュー音沙汰なしの長期 idle（窓口の運用判断で随時。自動化はしない）

   実施内容:
   - `org-state.md` の該当Work Itemを **COMPLETED** に更新
   - ワーカーの状態ファイルを最終更新
   - `journal.jsonl` にイベント追記
   - ディスパッチャーにペインクローズを依頼:
     `CLOSE_PANE: {pane_id} のペインを閉じてください。`
   - **ディレクトリパターンに応じた後処理**（同タイミングで実施）:
     - パターン A（プロジェクトディレクトリ）: ディレクトリは保持する（次タスクで再利用）
     - パターン B（worktree）: `git -C {workers_dir}/{project_slug}/ worktree remove .worktrees/{task_id}` を実行。ブランチは残す（マージ済みでもブランチ削除はしない、PR 履歴用）
     - パターン C（エフェメラル）: ディレクトリは保持する（容量が問題になった場合のみ手動削除を検討）
   - `.state/org-state.md` の Worker Directory Registry を更新（同タイミングで実施）:
     - パターン A: ステータスを `available` に更新（次タスクで再利用可能）
     - パターン B: エントリを削除（worktree は除去済み）
     - パターン C: エントリを削除
   - JSON スナップショットを再生成する: `py -3 dashboard/org_state_converter.py`

2c. 人間がフィードバック・修正指示を出した場合:
   - ワーカーに renga-peers で追加指示を送る (`to_id="worker-{task_id}"`)
   - 追加指示が trivial fix（CI 出力整形 / typo / コメント修正等）なら **検証深度 `minimal`** を明示し、完了報告は `done: {commit SHA 短縮形} {変更ファイル名}` の 1 行だけで返すよう伝える（フォーマットは `references/instruction-template.md` / `references/worker-claude-template.md` に従う）
   - `org-state.md` の該当Work Itemを **IN_PROGRESS** に戻す
   - `journal.jsonl` にイベント追記
   - JSON スナップショットを再生成する: `py -3 dashboard/org_state_converter.py`
   - （ペインが生きているのでワーカーはそのまま作業続行）

### ワーカー監視と介入判定（窓口が実行）

派遣後、ワーカーが深掘り・過剰検証ループに入っていないか定期的に確認する:

**介入トリガー**（いずれか 1 つ以上該当したら `mcp__renga-peers__inspect_pane` で状況確認する）:
- 同一タスクで 30 分超経過、かつ同じフェーズ（実装 / レビュー / 検証）に 3 回目以降入っている
- 1 時間以上進捗報告なしで静穏（入力待ちでもなく、progress ログも出ない）
- (codex を使っている場合) Codex セルフレビューが 4 ラウンド目以降に入っている（3 ラウンド上限はワーカー側指示だが、window 側でも確認）。codex 未導入環境ではこのトリガーは無関係

**介入手順**:
1. `inspect_pane` で画面を確認（Running / Codex 実行中 / 入力待ちのいずれか判定）
2. 深掘りと判断したら `send_keys(target="worker-{task_id}", keys=["Escape"])` で中断
3. `send_message` で tight な修正指示を送る。例:
   - 「検証深度 minimal に切り替えます。Codex レビュー・追加テスト禁止。いま書いた変更を commit して `done: {commit SHA 短縮形} {変更ファイル名}` の 1 行だけ返してください」
   - 「Minor は残置で OK。README に既知制限として 1 行追記したら完了報告してください（検証深度 full のままなので通常の完了報告フォーマット）」

**注意**: 窓口が自らワーカーの worktree で commit を代行することは auto-mode classifier によりブロックされる（スコープ逸脱）。介入はあくまで「指示の再送」で行うこと。
3. ブロック報告の場合:
   - 人間に判断を仰ぐ
