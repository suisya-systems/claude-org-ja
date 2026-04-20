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
ペイン起動・指示送信はフォアマンに委託する。これにより窓口のロック時間を最小化する。

## 窓口とフォアマンの役割分担

| 工程 | 担当 |
|---|---|
| プロジェクト名前解決 | **窓口** |
| work-skill 検索 | **窓口**（新規追加） |
| タスク分解 | **窓口** |
| CLAUDE.md 生成 | **窓口** |
| フォアマンへの依頼 | **窓口**（ここで窓口は解放される） |
| ペイン起動 | **フォアマン** |
| ピア待ち・指示送信 | **フォアマン** |
| 状態記録 | **フォアマン** |
| 窓口への派遣完了報告 | **フォアマン** |
| ワーカーからの進捗/完了報告の受信 | **窓口** |
| ワーカー完了時のペインクローズ | **フォアマン**（窓口から依頼） |

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

## Step 1: タスク分解（窓口が実行）

人間の依頼を分析し、ワーカーに委譲するタスクを定義する:

- 各タスクに一意のIDを振る（依頼内容から連想しやすい英語 kebab-case。例: `ceps-analysis`, `login-fix`, `dashboard-redesign`）
  - 既存のIDと重複しないよう `.state/org-state.md` を確認する（重複時はサフィックスで区別: `login-fix-2`）
- タスクごとに以下を明確にする:
  - 目的（何を達成するか）
  - 成果物（何ができあがるか）
  - 作業ディレクトリ（どのプロジェクトで作業するか）
  - 制約（ブランチ名、コーディング規約、依存関係等）
  - Planモードの要否（プログラミングタスクは推奨、定型作業は不要）
  - **ディレクトリパターン（A / B / C）** — 以下の判定基準で決定する
  - **参考 work-skill**（Step 0.5 でマッチしたもの）
- 注意: タスク説明にファイルパスを含める場合、それがワーカー作業ディレクトリからの相対パスであることを明記する。registry/projects.md の「パス」列の値をそのまま成果物パスとして指示しない（ワーカーが別の場所にパスを作成する原因になる）

### ディレクトリパターン判定基準

| パターン | 名称 | 条件 | ディレクトリ |
|---|---|---|---|
| A | プロジェクトディレクトリ | プロジェクトの clone が必要（初回は clone、2回目以降は再利用） | `{workers_dir}/{project_slug}/` |
| B | worktree | 同一プロジェクトで並行作業が必要（既に別ワーカーが同じプロジェクトディレクトリを使用中） | `{workers_dir}/{project_slug}/.worktrees/{task_id}/` |
| C | エフェメラル | 成果物を残す必要がない一時作業（調査・検証等） | `{workers_dir}/{task_id}/` |

**判定フロー:**

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

### 共通手順（全パターン）

1. `registry/org-config.md` の `workers_dir` を読み、リポジトリルートからの相対パスを絶対パスに解決する

### パターン A: プロジェクトディレクトリ

プロジェクト専用ディレクトリ（`{workers_dir}/{project_slug}/`）を使う。初回は clone、2回目以降は再利用。

**初回（ディレクトリが存在しない場合）:**

1. `git clone {project_path} {workers_dir}/{project_slug}/` を実行
2. ディレクトリ直下に CLAUDE.md を生成する（テンプレートの変数を置換）
3. ディレクトリ直下に `.claude/settings.local.json` を配置する
   （設定内容は org-setup/references/permissions.md の「ワーカー」セクション参照）
   - `permissions.deny` を含めること（`git push` / `rm -rf` 等の静的ブロック。`bypassPermissions` モードでも常に有効）
   - hooks の command パスと env の値には `{aainc_path}` と `{worker_dir}` を解決済みの絶対パスで埋め込むこと
   - command パス内のクォート（`"bash \"{aainc_path}/...\""`）はそのまま維持すること（スペース対策）
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
4. worktree 直下に `.claude/settings.local.json` を配置する
   （設定内容は org-setup/references/permissions.md の「ワーカー」セクション参照）
   - `permissions.deny` を含めること（`git push` / `rm -rf` 等の静的ブロック。`bypassPermissions` モードでも常に有効）
   - hooks の command パスと env の値には `{aainc_path}` と `{worker_dir}` を解決済みの絶対パスで埋め込むこと
   - command パス内のクォート（`"bash \"{aainc_path}/...\""`）はそのまま維持すること（スペース対策）
5. `.state/org-state.md` の Worker Directory Registry に登録する

### パターン C: エフェメラル

成果物を残す必要がない一時作業（調査・検証等）で使用する。

1. `{workers_dir}/{task_id}/` ディレクトリを作成する（例: `../workers/ceps-analysis/`）
2. テンプレートから `{workers_dir}/{task_id}/CLAUDE.md` を生成する
3. `{workers_dir}/{task_id}/.claude/settings.local.json` にワーカー用の設定を配置する
   （設定内容は org-setup/references/permissions.md の「ワーカー」セクション参照）
   - `permissions.deny` を含めること（`git push` / `rm -rf` 等の静的ブロック。`bypassPermissions` モードでも常に有効）
   - hooks の command パスと env の値には `{aainc_path}` と `{worker_dir}` を解決済みの絶対パスで埋め込むこと
   - command パス内のクォート（`"bash \"{aainc_path}/...\""`）はそのまま維持すること（スペース対策）
4. `.state/org-state.md` の Worker Directory Registry に登録する

### 共通手順（全パターン・配置後）

テンプレートの変数を実際の値で置換する:
- `{project_name}` → registry の通称
- `{project_description}` → registry の説明
- `{task_id}` → タスクID（例: `ceps-analysis`）
- `{task_description}` → タスクの目的と成果物
- `{aainc_path}` → aainc リポジトリの絶対パス
- `{worker_dir}` → ワーカーディレクトリの絶対パス（パターンにより異なる、上記参照）

生成した CLAUDE.md に「作業ディレクトリ（最重要制約）」セクションが含まれていることを確認する。含まれていない場合はテンプレート適用ミスのため再生成する

**参考 work-skill がある場合（Step 0.5 でマッチ）:**

CLAUDE.md に以下のセクションを追加する（「参照すべきファイル」セクションの後に配置）:

```markdown
## 参考 work-skill
以下の work-skill が参考になる可能性があります。手順や判断基準を参照してください。
ただし、タスクの要件に合わない部分は適宜調整すること。

- スキル名: {skill-name}
- パス: {aainc_path}/.claude/skills/{skill-name}/SKILL.md
- 概要: {description}
```

## Step 2: フォアマンへの委託（窓口が実行 → ここで窓口は解放）

claude-peers の `send_message` でフォアマンに以下を送信する:

```
DELEGATE: 以下のワーカーを派遣してください。

タスク一覧:
- {task_id}: {タスク説明}
  - ワーカーディレクトリ: {ワーカーディレクトリの絶対パス}（CLAUDE.md・設定配置済み）
  - ディレクトリパターン: {A: プロジェクトディレクトリ / B: worktree / C: エフェメラル}
  - プロジェクト: {clone先URL or ローカルパス or 新規作成 or worktree済み or 前タスク引継ぎ}
  - Planモード: 要 / 不要
  - Permission Mode: {org-config から読んだ default_permission_mode の値、またはPlanモード要なら plan}
  - Plan承認後モード切替: 要 / 不要（Planモード要の場合は「要」。Plan承認後に Shift+Tab で acceptEdits に切り替える）
  - 指示内容: {instruction-template に基づく指示の要約}

窓口ペイン名: `secretary`（ccmux layout で登録済み。新規タブ作成時の基準となる）
```

**窓口はこの送信後すぐにユーザーとの対話に戻れる。**
ユーザーには「フォアマンに派遣を依頼しました。準備ができ次第報告します。」と伝える。

> ccmux では窓口・フォアマン・キュレーター等の「長寿命ペイン」は安定名 (`--id`) で addressable。
> 窓口 (`secretary`) / フォアマン (`foreman`) / キュレーター (`curator`) は `/org-start` で命名済み。

## Step 3: ワーカー起動と指示送信（フォアマンが実行）

フォアマンが以下を実行する:

### 3-1. balanced split で target / direction を決める

旧設計は `--target-name foreman --direction vertical` 固定だったが、これは foreman 幅を毎回半減させるため、典型的なターミナル幅 (W≈200〜300 cols) で 4 人目以降 `[split_refused]` が発生していた (`MIN_PANE_WIDTH=20` 制約、調査: `C:/Users/iwama/working/workers/ccmux-split-inv/findings.md`)。

現設計では **balanced split** により、新規ワーカーの序数 `k` に応じて target と direction を動的に選ぶ。詳細ルールは `references/pane-layout.md` の「ワーカーの balanced split 戦略」セクションを参照。

フォアマンは `ccmux split` を呼ぶ前に、以下のシェルスニペットで target と direction を算出する:

```bash
# 生きている worker ペインを作成順 (pane id 昇順) に並べた name 配列を取得
readarray -t active_workers < <(
  ccmux list --format json \
    | jq -r '.panes | map(select(.role == "worker" and .exited == false)) | sort_by(.id) | .[].name'
)

k=$(( ${#active_workers[@]} + 1 ))  # この新規ワーカーの序数 (1-indexed)

case "$k" in
  1) target="foreman";                direction="vertical"   ;;
  2) target="${active_workers[0]}";   direction="horizontal" ;;
  3) target="${active_workers[0]}";   direction="vertical"   ;;
  4) target="${active_workers[1]}";   direction="vertical"   ;;
  5) target="${active_workers[0]}";   direction="horizontal" ;;
  6) target="${active_workers[2]}";   direction="horizontal" ;;
  7) target="${active_workers[1]}";   direction="horizontal" ;;
  8) target="${active_workers[3]}";   direction="horizontal" ;;
  *)
    # 9 人以上は balanced split table 未定義。窓口へ escalate
    echo "ERROR: ${k} 人目のワーカーは balanced split table 未定義。窓口へエスカレーションが必要" >&2
    exit 1
    ;;
esac
```

- active_workers が空 (最初のワーカー) なら k=1 に落ちて target=foreman / direction=vertical が選ばれる
- target / direction が計算できない場合 (9 人以上、もしくは想定外の退役順で `${active_workers[i]}` が空) は、窓口に escalate して人間判断を仰ぐ

### 3-2. ワーカーペインを起動する

3-1 で算出した `$target` / `$direction` を使って `ccmux split` を呼ぶ:

```bash
ccmux split \
  --target-name "$target" \
  --direction "$direction" \
  --role worker \
  --id worker-{task_id} \
  --command "cd '{workers_dir}/{task_id}' && claude --dangerously-load-development-channels server:claude-peers --permission-mode {default_permission_mode}"
```

   - ペイン配置ルールは `references/pane-layout.md` (ccmux 版) を参照。`k` に対する target / direction のマッピングと 4 並列 / 8 並列の ASCII 図もそちらに集約
   - **同一タブ内 split で起動する理由**: ccmux の `list` / `focus` / `send` / `inspect` は現在フォーカス中のタブのペインしか見えない。`new-tab` で別タブに置くとフォアマンからの監視・指示送信が不能になる (2026-04-20 判明。ccmux 側 issue: happy-ryo/ccmux#71)
   - `--target-name "$target"`: balanced split で算出した既存ペイン名 (`foreman` もしくは `worker-*`) を分割対象にする
   - `--direction "$direction"`: balanced split で算出した `vertical` / `horizontal`
   - `--id worker-{task_id}`: 後続の `ccmux send --name worker-{task_id} ...` で addressable にする安定名
   - `--role worker`: `ccmux list` の JSON で役割識別 (balanced split の target 選出にも使われる)
   - 起動コマンドは `.claude/skills/org-start/SKILL.md` の「ClaudeCode 起動コマンド（役割別）」セクションを参照
   - Planモード要の場合は `--permission-mode plan` を使用する（org-config の値を上書き）
   - 開発チャネルの確認プロンプトが表示されるので、`ccmux send --name worker-{task_id} --enter ""` で Enter を送信する
   - `[split_refused]` (MAX_PANES / too small) が返った場合は `references/ccmux-error-codes.md` の手順に従いキュレーター → 窓口に escalate する。balanced split は best-effort の配置ヒントであり、想定外のレイアウト (途中でワーカーが閉じた後の再派遣など) では拒否され得る
   - `[pane_not_found]` が返った場合は `$target` に選んだワーカーが split 発行直前に閉じたレース。同じく既存エラーコード経路で escalate
### 3-3. ペインが起動したことを確認 (`ccmux events`、推奨)

```bash
ccmux events --timeout 3s \
  | jq -c --arg want "worker-{task_id}" \
    'select(.type == "pane_started" and .name == $want)' \
  | head -n 1
```

- `ccmux events` は 3 秒経過で勝手に exit するので全体で最大 3 秒待機
- `jq` で `pane_started` かつ `name == "worker-{task_id}"` の行だけに絞る (別タスクの同時 spawn 由来イベントを取りこぼさない、誤マッチしない)
- `head -n 1` で最初の該当行で pipeline を終了させる
- 出力が 1 行あれば OK。空なら 3 秒以内に起動イベントが来なかったということなので、`ccmux list` で状態を確認して窓口にエスカレーションする
- **注意**: 直接 `--count 1` にすると、別ワーカーの起動イベント等の無関係な 1 件で exit してしまい、target ワーカーの起動確認ができない

### 3-4. claude-peers の `list_peers` で新ピア出現を待機

pane は live でも Claude がまだ起動中の場合があるため二重確認。

### 3-5. claude-peers の `send_message` でワーカーに指示を送信

`references/instruction-template.md` のフォーマットに従う。

### 3-6. 複数ワーカーの順次起動

複数ワーカーがある場合は 3-1〜3-5 を順次繰り返す。`ccmux list` の結果が毎回変わるので、都度 `active_workers` を再取得して `k` を計算し直す (前ワーカーの起動が完了するのを 3-3 / 3-4 で待ってから次に進むこと)。

### 3-7. Planモード要の場合 — Plan承認前のモード切替（重要: 順序厳守）

Plan承認待ち（APPROVAL_BLOCKED）を検知したら、**Plan を承認する前に**モード切替を完了させる:

(a) `ccmux send --name worker-{task_id} $'\x1b[Z'` で Shift+Tab を送信する（`--enter` を付けない）
(b) 目視または claude-peers 側のサインでモード切替完了（「accept edits」表示）を確認する — 最大5回リトライ
(c) モード切替完了を確認してから、Plan を承認する（`ccmux send --name worker-{task_id} --enter "yes"`）

**理由**: Plan承認後にモード切替しようとすると、planモードのままワーカーが動き出し、
コマンド実行のたびに承認プロンプトが連続発生する。承認プロンプト表示中は Shift+Tab が
効かないため、手動介入が必要になる。先にモード切替することでこの問題を回避する。

**TODO (Phase 3)**: pane 内容スクレイプ API が入れば、ステータスバー
テキスト取得で「mode change」を確認できる。それまでは目視 or リトライで運用。

## Step 4: 状態記録（フォアマンが実行）

各ワーカーについて:

1. `.state/workers/worker-{peer_id}.md` を作成:
   ```markdown
   # Worker: {peer_id}
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

3. `.state/journal.jsonl` にイベント追記:
   ```json
   {"ts":"<ISO timestamp>","event":"worker_spawned","worker":"<peer_id>","dir":"<dir>","task":"{task_id}"}
   ```

4. `.state/org-state.md` を更新した後、JSON スナップショットを再生成する:

   ```bash
   py -3 dashboard/org_state_converter.py    # Windows
   python3 dashboard/org_state_converter.py   # Mac/Linux
   ```

5. ワーカーペインを監視対象として登録する:
   - 派遣後、そのペインを監視対象として記録し、`.foreman/CLAUDE.md` の「ワーカーペイン監視」に従って定期的に承認待ちを確認する

### Worker Directory Registry（org-state.md 内のセクション定義）

`.state/org-state.md` に以下のセクションを追加・管理する。ワーカーディレクトリの再利用状態を追跡する。

```markdown
## Worker Directory Registry

| Task ID | Pattern | Directory | Project | Status |
|---|---|---|---|---|
| blog-redesign | A | /path/to/workers/blog/ | blog | in_use |
| blog-auth-fix | B | /path/to/workers/blog/.worktrees/blog-auth-fix/ | blog | in_use |
| ceps-analysis | C | /path/to/workers/ceps-analysis/ | - | in_use |
```

**フィールド説明:**
- **Task ID**: 現在そのディレクトリを使用しているタスクID
- **Pattern**: A（プロジェクトディレクトリ）/ B（worktree）/ C（エフェメラル）
- **Directory**: ワーカーディレクトリの絶対パス
- **Project**: registry/projects.md の通称（エフェメラルで無関係なら `-`）
- **Status**: `in_use`（作業中）/ `available`（完了済み・再利用可能）

**運用ルール:**
- Step 1.5 でディレクトリ準備時にエントリを追加する
- Step 5 (2b) で人間が承認した際にステータスを更新する（ディレクトリは削除しない）
- Step 1 の判定フローでこのテーブルを参照し、再利用可能なディレクトリや並行作業の有無を判定する

5. 窓口に claude-peers で派遣完了を報告:
   ```
   DELEGATE_COMPLETE: {task_id} のワーカーを派遣しました。
   ピアID: {peer_id}, ペインID: {pane_id}
   ```

## Step 5: 進捗管理（窓口が実行）

### DELEGATE_COMPLETE 受信時

フォアマンから派遣完了報告を受け取ったら、各ワーカーに挨拶メッセージを送る:
```
窓口です。{task_id} の作業をお願いしています。完了・進捗・ブロック、全ての報告はこのIDに送ってください。
```
これによりワーカーのメッセージ履歴に窓口のピアIDが残り、報告先を見失わない。

### フォアマンから APPROVAL_BLOCKED（Plan承認待ち）受信時

Planモード要のワーカーがPlan作成を完了し承認待ちになった場合:

1. **先にモード切替**: ワーカーペインに Shift+Tab を送信する
   ```bash
   ccmux send --name worker-{task_id} $'\x1b[Z'
   ```
2. 目視 or claude-peers 側のメッセージで「accept edits on」表示を確認する（最大5回）
3. **モード切替完了後に承認**: ワーカーペインに `yes` + Enter を送信してPlanを承認する
   ```bash
   ccmux send --name worker-{task_id} --enter "yes"
   ```
4. フォアマンにモード切替完了を通知する

**TODO (Phase 3)**: ccmux に pane 内容スクレイプ API または "mode change" 相当のイベントが追加されれば、ステータスバー確認を自動化できる。Phase 2 の \`ccmux events\` は現状 pane lifecycle (起動/終了) のみで mode 変更は含まれない。

**順序を間違えない**: 先に承認するとplanモードのままワーカーが動き出し、コマンド実行のたびに承認プロンプトが連続発生して作業が止まる。

### ワーカーからのメッセージ受信時

ワーカーからclaude-peersでメッセージを受け取ったら:

1. 進捗報告の場合:
   - `.state/workers/worker-{id}.md` の Progress Log に追記
   - `journal.jsonl` にイベント追記
2a. ワーカーから完了報告を受け取ったら:
   - `org-state.md` の該当Work Itemを **REVIEW** に更新
   - `journal.jsonl` にイベント追記
   - JSON スナップショットを再生成する: `py -3 dashboard/org_state_converter.py`
   - 結果を人間に報告する
   - **ペインはまだ閉じない**

2b. 人間が承認した場合（「OK」「確認した」「問題ない」等）:
   - `org-state.md` の該当Work Itemを **COMPLETED** に更新
   - ワーカーの状態ファイルを最終更新
   - `journal.jsonl` にイベント追記
   - 必要に応じて窓口がプッシュ・PR作成を行う（ワーカーには権限がないため）
   - フォアマンにペインクローズを依頼:
     `CLOSE_PANE: {pane_id} のペインを閉じてください。`
   - **ディレクトリパターンに応じた後処理**:
     - パターン A（プロジェクトディレクトリ）: ディレクトリは保持する（次タスクで再利用）
     - パターン B（worktree）: `git -C {workers_dir}/{project_slug}/ worktree remove .worktrees/{task_id}` を実行。ブランチは残す（PR/マージ用）
     - パターン C（エフェメラル）: ディレクトリは保持する（容量が問題になった場合のみ手動削除を検討）
   - `.state/org-state.md` の Worker Directory Registry を更新:
     - パターン A: ステータスを `available` に更新（次タスクで再利用可能）
     - パターン B: エントリを削除（worktree は除去済み）
     - パターン C: エントリを削除
   - JSON スナップショットを再生成する: `py -3 dashboard/org_state_converter.py`

2c. 人間がフィードバック・修正指示を出した場合:
   - ワーカーにclaude-peersで追加指示を送る
   - `org-state.md` の該当Work Itemを **IN_PROGRESS** に戻す
   - `journal.jsonl` にイベント追記
   - JSON スナップショットを再生成する: `py -3 dashboard/org_state_converter.py`
   - （ペインが生きているのでワーカーはそのまま作業続行）
3. ブロック報告の場合:
   - 人間に判断を仰ぐ
