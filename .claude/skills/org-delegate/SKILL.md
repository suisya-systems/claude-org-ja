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

> **state-db cutover (M4, Issue #267)**: 構造化セクション
> (Worker Directory Registry / Active Work Items / Dispatcher / Curator / Status) の write は
> **必ず `StateWriter.transaction()` 経由**で行う。`transaction()` の post-commit hook が
> `.state/org-state.md` を DB から自動再生成するので、本 skill では
> markdown を直接編集する手順を残さない (drift_check が検出する)。
> events は DB の `events` テーブルが SoT (`.state/journal.jsonl` は M4 で廃止)。
> `tools/journal_append.py` (および `journal_append.sh`) は DB ルーティング済み。
> DB が無い場合は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で構築する。
>
> **Issue #284 cutover**: `transaction()` の post-commit hook は markdown 再生成に加えて
> `.state/org-state.json` (dashboard 用 JSON snapshot) も DB から自動再生成する。
> さらに `update_run_status('<task_id>', 'completed')` 呼び出しは commit 後に
> `.state/workers/worker-<task_id>.md` を `.state/workers/archive/` へ自動 move する
> (Issue #264: dead worker live 表示の根絶)。本 skill 内では JSON 再生成・archive 移動の
> 手動コマンドを残さない (StateWriter が自動実行)。

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
- タスク分解時に、work-skill の手順を参考にする
- `gen_delegate_payload.py` 呼び出しの `--knowledge` フラグに work-skill の SKILL.md パスを渡す。Stage 2 の brief renderer がそのパスを `[references].knowledge` として CLAUDE.md / CLAUDE.local.md に埋め込む。複数マッチがあれば `--knowledge <path1> --knowledge <path2>` のように繰り返す
- ワーカーへの指示（instruction-template）にも参考スキルの存在を明記する

**マッチしなかった場合:**
- 通知不要。そのまま Step 1 に進む

### 検索の注意点

- work-skill の手順をそのままコピーしない。参考情報として提示し、ワーカーが判断する
- 複数マッチした場合は関連度順に全て含める
- org- プレフィックスのスキル（org-retro, org-delegate 等）は組織運営スキルなので検索対象外

## Step 0.7 / 1 / 1.5 / 2: 1 コマンドで派遣ペイロードを生成（Issue #283）

Step 0.7 (gitignore 事前チェック) / Step 1 (Pattern 判定) / Step 1.5 (ワーカーディレクトリ準備 + role 決定 + settings 生成) / Step 2 (DELEGATE 本文組み立て) は **`tools/gen_delegate_payload.py` が一括で行う**。窓口の責務はタスク特定 (Step 0)・work-skill 検索 (Step 0.5)・対象ファイルの抽出・depth 判断のみ。

### 標準フロー (推奨)

```bash
# 1. preview: 完全に非破壊。DELEGATE 本文と作成予定ファイル一覧だけを確認する
python tools/gen_delegate_payload.py preview \
    --task-id <task-id> --project-slug <slug> \
    --target <path>... --description "<desc>" \
    --verification-depth full

# 2. apply: state.db に runs.status='queued' で予約 + CLAUDE.md/CLAUDE.local.md 配置
#    + claude-org-runtime settings generate 実行 + send_plan.json 出力
python tools/gen_delegate_payload.py apply \
    --task-id <task-id> --project-slug <slug> \
    --target <path>... --description "<desc>" \
    --verification-depth full

# 3. apply 出力の send_plan.json を MCP 呼び出しにコピペ
#    cat <worker_dir>/send_plan.json
#    → mcp__renga-peers__send_message(to_id="dispatcher", message=<message>)
```

`apply` は **T1 reservation のみ** (`runs.status='queued'`) を行う。Active Work Items への active 化はディスパッチャー T2 (`docs/contracts/delegation-lifecycle-contract.md`) なので本 skill では触らない。失敗時はキューを残したまま Secretary に判断を仰ぐこと。

### よく使うフラグ

- `--mode edit|audit` (default `edit`): claude-org 上の **読み取り専用** 監査タスクは `--mode audit` を明示する。デフォルトの `edit` だと self-edit role が選ばれて余計な書き込み権限が付く。
- `--branch <name>`: planned_branch を上書き。default は `feat/<task-id>` (description に "fix"/"bug"/"修正" を含むと `fix/<task-id>`)。
- `--commit-prefix "<prefix>"`: TOML の `task.commit_prefix` を明示。省略時は project_slug の頭部から推論 (例: `claude-org-ja` → `feat(claude):`)。
- `--closes-issue N` / `--refs-issues N1 N2`: 「Closes #N」「Refs #N1 #N2」を brief に埋め込む。
- `--impl-target <path>` / `--impl-guidance "<text>"` / `--knowledge <path>`: optional な `[implementation]` / `[references]` セクション。
- `--skip-settings`: `claude-org-runtime settings generate` をスキップ (CLI 未導入環境向け)。
- `--from-toml <path>`: 既存 `worker_brief.toml` を入力にする。CLI フラグは TOML を上書きする。

### Pattern / role / branch の判定詳細

判定ロジック (Pattern A vs B vs C / gitignored サブモード / role 表 / planned_branch / DELEGATE 本文の必須行) は `references/delegate-flow-details.md` 参照。本 SKILL.md からは抜き出してある。

**self-edit タスクの特例（Issue #289）**: claude-org 自己編集タスクが Pattern B に乗る場合、worktree base は通常の `{workers_dir}/{project_slug}/.worktrees/` ではなく **Secretary の live repo (`{claude_org_path}/.worktrees/`)** を使う（`pattern_variant='live_repo_worktree'`）。resolver が role=claude-org-self-edit のとき自動選択する。詳細・根拠は `references/claude-org-self-edit.md` §3 参照。

### Step 0.7 の対象ファイル抽出

「対象ファイル」は窓口がタスク説明から抽出する（依頼文・Issue 本文・ユーザー発話の中で明示されたパス。機械的判定はしない）。対象ファイルが特定できないタスク（純粋な調査、対象パス未定の新規作成など）は `--target` を渡さなくてよい — `gen_delegate_payload` は target 0 件のときだけ check-ignore をスキップして通常判定に進む。

### 標準経路が想定外の出力を返した場合

標準経路 (`gen_delegate_payload.py apply`) が想定外の出力 (Pattern 誤判定 / resolver エラー / brief 不整合 等) を返した場合、Secretary は **手動で同じ作業を再現してはならない**。`gen_delegate_payload.py` (またはその resolver) のバグとして Issue を切り、当該タスクの delegation は **resolver が直るまで pause** する。例外的に手作業を行うかどうかはユーザー判断に委ね、Secretary が自走で fallback に入らない。手作業 fallback は skill のスコープ外。

runtime CLI 自体が壊れている場合 (`claude-org-runtime` / `gen_delegate_payload.py` が import / exec 不能) も同じく runtime 側の修復が前提で、手書き経路は代替にならない (旧手書き手順自体が `claude-org-runtime settings generate` に依存する)。標準経路の degraded mode が必要な場合 (CLI 未導入環境など) は `--skip-settings` フラグの利用に限定する。

歴史的な手書き経路の museum copy は `docs/legacy/hand-typed-delegate-path.md` にある (settings env mismatch / drift_check breakage / T1 reservation 欠落などの失敗事例つき)。標準オペレーションでは参照禁止。

<!-- 旧 Step 0.7 / Step 1 / Step 1.5 / Step 2 の詳細 prose は references/delegate-flow-details.md に移設済み (Issue #283 Stage 4)。
     判定コマンド・Pattern C 強制サブモード・パターン別 worktree/clone 手順・DELEGATE 本文の必須行は同 reference を SoT とする。 -->

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

#### 3-1b. balanced split アルゴリズム（runtime helper が判定ロジックを実行）

balanced split の判定 (target / direction の選択、MIN_PANE / secretary 保険 / role priority によるソート、rect 隣接判定) は **dispatcher が `claude-org-runtime` の `delegate-plan` helper 経由で実行する**。Claude 側で再実装するロジックではない。helper は `mcp__renga-peers__list_panes` の rect スナップショットと task JSON を入力に取り、`spawn` ターゲット名と direction を含む action plan を返す（候補が空なら `split_capacity_exceeded` で escalate を指示）。

仕様詳細・定数値・ソートキー・rect 隣接の正確な定義は **runtime SoT** を参照する:
- CLI (運用上の標準呼び出し): `claude-org-runtime dispatcher delegate-plan --task-json ... --panes-json ... --state-dir ... [--template-repo ...] [--locale-json ...]`。`.dispatcher/CLAUDE.md` の delegate-plan helper 節が一次手順
- ライブラリ: `claude_org_runtime.dispatcher.runner` モジュールの `build_plan()` (action plan 全体: `spawn` / `after_spawn` / `escalate` / `state_writes` / `status`) と、その内部で呼ばれる `choose_split()` (target / direction 選択) / `rect_adjacent()` / `_ROLE_PRIORITY` / `MIN_PANE_*` / `SECRETARY_MIN_*` 定数

本 SKILL から定数値や Step 1-6 の prose を消したのは、runtime と doc が drift する原因になるため (Issue #307 cleanup)。dispatcher が helper を経由しない degraded mode に入った場合、判定再現は `claude_org_runtime.dispatcher.runner` モジュール (インストール先は `python -c "import claude_org_runtime.dispatcher.runner; print(claude_org_runtime.dispatcher.runner.__file__)"` で解決可能) を一次参照する。

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
  permission_mode="auto",
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

2. **DB 経由で run と Active Work Items を登録する**（`.state/org-state.md` 直接編集は禁止。`StateWriter.transaction()` 経由、post-commit hook が再生成）:

   ```bash
   python -c "
   from pathlib import Path
   from tools.state_db import connect
   from tools.state_db.writer import StateWriter
   conn = connect('.state/state.db')
   with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
       w.register_worker_dir(abs_path='<worker_dir_abs>', layout='<flat|worktree|ephemeral>', is_worktree=<True|False>)
       w.upsert_run(task_id='<task_id>', project_slug='<slug>', pattern='<A|B|C>', title='<title>', status='in_use', worker_dir_abs_path='<worker_dir_abs>')
       w.update_session(objective='<人間の依頼>')
   "
   ```

   - free-form な「Current Objective の補足説明」は **`notes/` に保存する**（`notes/README.md` 参照）。markdown 直接編集は drift_check で検出される。`update_session(objective=...)` は構造化 header フィールドとして DB に書く
   - `pattern` は A / B / C のいずれか

3. DB の events テーブルにイベント追記（helper 経由。`ts` は自動付与。Bash で生 JSON を `>>` で append しないこと）:
   ```bash
   bash tools/journal_append.sh worker_spawned worker=worker-{task_id} dir=<dir> task={task_id}
   ```
   event 名と payload key の規約は [`docs/journal-events.md`](../../../docs/journal-events.md) を参照。

4. JSON スナップショット (dashboard 用 `.state/org-state.json`) は **StateWriter の post-commit hook が自動再生成**する (Issue #284)。手動で `org_state_converter.py` を呼ぶ必要はない。

5. ワーカーペインを監視対象として登録する:
   - 派遣後、そのペインを監視対象として記録し、`.dispatcher/CLAUDE.md` の「ワーカーペイン監視」に従って定期的に承認待ちを確認する

### Worker Directory Registry（DB 由来のセクション定義）

> **M2.1 cutover (Issue #272)**: このセクションは DB の `worker_dirs` × `runs` から
> snapshotter が自動生成する。窓口・ディスパッチャーは `register_worker_dir(...)` /
> `update_worker_dir_lifecycle(...)` / `upsert_run(...)` を `transaction()` 経由で呼ぶ。
> markdown を直接編集するのは厳禁（次の post-commit 再生成で消える）。

`.state/org-state.md` の以下のセクションは DB 由来。ワーカーディレクトリの再利用状態を追跡する。

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

**Canonical event flow**（ワーカーからの完了 / 進捗 / Codex round / 判断仰ぎ いずれの peer message にも共通する正準順序。途中段階を飛ばしてはならない）:

```
worker → Secretary peer message
  1. ack to worker (mcp__renga-peers__send_message, to_id="worker-{task_id}")
  2. update Progress Log + DB (run.status / events / pending-decisions register)
  3. report to user
  4. wait for user approval before push/PR (2b-i)
  5. CI watch / next instruction
```

- **適用範囲**:
  - **step 1 (ack) と step 2 (state 更新) は全 message 共通で必須**。完了 / 進捗 / Codex round / 判断仰ぎ いずれを受けても worker 宛 ack を最初に発行する（dead-lock 防止）
  - **step 3 (user 報告) と step 4 (承認待ち)** は完了報告・判断仰ぎ・ブロッカー・スコープ拡張提案 等 user の判断を要する種別に限る。**純粋な進捗報告（subsection 1）は ack + Progress Log + events 追記で完了**し、user 報告・承認待ちは行わない（worker を不要に止めない）
  - **step 5 (CI watch / 次の指示)** は push / PR 後または追加指示が必要な種別のみ
- ack の最低内容と種別ごとの例文は [`references/ack-template.md`](references/ack-template.md) を参照
- 2 → 3 の順序は「内部状態を先に整合させてから user に報告する」原則。逆にすると user 承認後に DB 不整合が残るリスクがある
- step 4 の `git push` / `gh pr create` / `tools/pr-watch.*` は user の明示的 OK 後にのみ発行する。**ack ≠ user 承認** — ack は worker dead-lock 解除のための受領確認で、push/PR 権限を生まない

ワーカーから renga-peers でメッセージを受け取ったら:

0. 判断仰ぎ・スコープ拡張提案・承認要求・ブロッカーの場合（**最優先で識別**）:
   - ワーカーが「承認を仰ぎます」「判断仰ぎます」「続行可否」「スコープ拡張」「提案」「想定外」「runbook 逸脱」「ブロック」「ブロッカー」「block」等を含むメッセージを送ってきた場合
   - **最初に worker へ ack を返す**（Canonical event flow step 1。状態保存・user 伝達より前に発行する）:
     ```
     mcp__renga-peers__send_message(
       to_id="worker-{task_id}",
       message="判断仰ぎ受領しました。Secretary では一次承認しません。人間に確認します。返答が来るまでペイン保持で待機してください（自動続行しないこと）。"
     )
     ```
     文面例は [`references/ack-template.md`](references/ack-template.md) の「判断仰ぎ ack」節
   - **Secretary は一次承認しない**。worker への返答も「受領しました、人間に確認します」のみ
   - **状態を保存する**（窓口再起動・引き継ぎで pending 判断を失わないため、進捗報告と同等の永続化を行う）:
     - `.state/workers/worker-{task_id}.md` の Progress Log に「判断仰ぎ受信」内容と要点を追記
     - DB の events テーブルに追記: `bash tools/journal_append.sh worker_escalation worker=worker-{task_id} task={task_id} reason="<要約>"`
     - **pending-decisions register に追加** (Issue #297): `python tools/pending_decisions.py append --task-id {task_id} --message "<本文要約>"`。同 task_id の pending が既存なら idempotent (no-op)。register はディスパッチャーの SECRETARY_RELAY_GAP_SUSPECTED 検出 ([`.dispatcher/references/worker-monitoring.md` Step 5.1](../../../.dispatcher/references/worker-monitoring.md#step-5-1)) の primary lookup source
   - 人間に内容と選択肢を整理して提示する。提示直後に **register を escalated に更新**: `python tools/pending_decisions.py resolve --task-id {task_id} --kind to_user`
   - **ユーザーから返答（decision／フィードバック／修正指示）を受領した時点** — ワーカーへ転送する **前に** `user_replied_at` marker を register に記録する (Issue #301): `python tools/pending_decisions.py mark-user-replied --task-id {task_id}`。escalated entry が無ければ no-op、既に設定済みでも idempotent。これにより [`.dispatcher/references/worker-monitoring.md` Step 5.1 (a-2)](../../../.dispatcher/references/worker-monitoring.md#step-5-1) で「ユーザー返答済みなのに Secretary が転送忘れ」を deterministic に検知できる
   - 人間の判断後にワーカーに伝達する（伝達時は `to_id="worker-{task_id}"` で `send_message`）。伝達直後に **register を resolved に更新**: `python tools/pending_decisions.py resolve --task-id {task_id} --kind to_worker`
   - 「ユーザーは選択肢 X を選んだから自動的に含意される」等の自己解釈で承認してはならない
   - register の append / resolve のどちらかが欠落するとディスパッチャー側で SECRETARY_RELAY_GAP_SUSPECTED が誤発火または見逃しになる。Progress Log / journal は重複保険として維持し、register 更新とは独立に行うこと
   - （注）ブロッカー報告も本分岐で扱うため、下段「3. ブロック報告の場合」と重複した場合は本分岐を優先する

1. 進捗報告の場合:
   - **最初に worker へ ack を返す**（Canonical event flow step 1。Progress Log 追記より前）:
     ```
     mcp__renga-peers__send_message(
       to_id="worker-{task_id}",
       message="進捗受領しました。続行 OK。完了したら同じ to_id=\"secretary\" で報告してください。ペイン保持で。"
     )
     ```
     文面例は [`references/ack-template.md`](references/ack-template.md) の「進捗報告 ack」節。**進捗報告は user に上げない・承認待ちもしない**（Canonical event flow の適用範囲注記参照）
   - `.state/workers/worker-{task_id}.md` の Progress Log に追記
   - DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)
2a. ワーカーから完了報告を受け取ったら:
   - **最初に worker へ ack を返す**（Canonical event flow の step 1。run.status 更新・user 報告・後続作業より前に発行する）:
     ```
     mcp__renga-peers__send_message(
       to_id="worker-{task_id}",
       message="<受領確認> + <次の予定: PR 作成は user 承認後 / CI 結果待ち / 追加レビュー要否> + <ペイン状態: 保持 or クローズ予定>"
     )
     ```
     ack 文面の例（種別別）は [`references/ack-template.md`](references/ack-template.md)。worker は ack を受けるまで「次の指示待ち」で idle になり、dead-lock の原因になる
   - **DB 経由で run を REVIEW に遷移する**（markdown 直接編集禁止。post-commit hook が `.state/org-state.md` を再生成）:
     ```bash
     python -c "
     from pathlib import Path
     from tools.state_db import connect
     from tools.state_db.writer import StateWriter
     conn = connect('.state/state.db')
     with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
         w.update_run_status('<task_id>', 'review')
     "
     ```
   - DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)（`tools/journal_append.py` が DB ルーティング済み）
   - JSON snapshot は StateWriter post-commit hook が自動再生成 (Issue #284)
   - 結果を人間に報告する
   - **ペインはまだ閉じない**
   - **承認待ちで停止する**: 人間から「OK」「確認した」「問題ない」「進めて」等の **明示的承認** を受けるまで、`git push` / `gh pr create` / `tools/pr-watch.ps1` / `tools/pr-watch.sh` を発行してはならない。「報告して即続行」ではなく「報告して承認待ち」が正準フロー。ack（worker 宛）は user 承認とは別物 — ack は worker dead-lock 解除のためで、push/PR 権限を生まない。承認なしで push/PR を発行すると worker / user 双方への protocol 違反

2b. 人間が承認した場合（「OK」「確認した」「問題ない」等）:

   2b-i. **PR 作成段階（即時実行）**:
   - 必要に応じて窓口がプッシュ・PR作成を行う（ワーカーには権限がないため）
   - DB の events テーブルにイベント追記 (push / PR open など)
   - PR 番号が確定したら `tools/pr-watch.ps1 <PR>` (Windows) / `tools/pr-watch.sh <PR>` (POSIX) で CI を監視する。完了時に `ci_completed` が自動で journal に記録される
   - run.status は **REVIEW のまま据え置く**（GitHub 側 PR レビュー指摘が来たら同ペインで対応するため。COMPLETED への遷移は 2b-ii で `update_run_status('<task_id>', 'completed')` を呼ぶ）。markdown 直接編集はしない
   - **ペインはまだ閉じない**: PR 作成直後に `CLOSE_PANE` を送らない。worktree 除去・Worker Directory Registry 更新も 2b-ii まで遅延する
   - PR レビューで指摘が来た場合は 2c のフローで同ワーカーに `send_message` 追指示を送り、同ペインで修正コミットを積ませる（新ワーカー再派遣は避ける — Issue / diff / 判断境界の再構築コストを払うことになる）

   2b-ii. **最終クローズ段階（クローズ条件を満たしたら実行）**:

   クローズ条件（少なくとも 1 つ満たすこと）:
   - PR がマージされた（`gh pr view {n} --json mergedAt` 等で確認、または窓口がマージ通知を受ける）
   - ユーザーが明示的に「閉じてよい」「クローズして」「マージ済み」等の指示を出した
   - 24-48 時間レビュー音沙汰なしの長期 idle（窓口の運用判断で随時。自動化はしない）

   実施内容:
   - 該当 run を **COMPLETED** に DB 更新（直下の `update_run_status('<task_id>', 'completed')` ブロックで実施）。markdown 直接編集はしない
   - ワーカーの状態ファイルを最終更新（最後の Progress Log 追記など）
   - **ワーカー状態ファイル (`.state/workers/worker-{task_id}.md`) は StateWriter が `update_run_status('<task_id>', 'completed')` の post-commit で自動的に `.state/workers/archive/` へ移動する** (Issue #284。`archive/` 不在時は lazy 作成、再呼び出しは idempotent。dashboard はこのディレクトリ内のファイルを live ワーカーとして扱わない (Issue #264)。journal / retro が履歴参照する可能性に備えて削除はしない)
   - DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)
   - ディスパッチャーにペインクローズを依頼:
     `CLOSE_PANE: {pane_id} のペインを閉じてください。`
   - **ディレクトリパターンに応じた後処理**（同タイミングで実施）:
     - パターン A（プロジェクトディレクトリ）: ディレクトリは保持する（次タスクで再利用）
     - パターン B（worktree）: `git -C {workers_dir}/{project_slug}/ worktree remove .worktrees/{task_id}` を実行。ブランチは残す（マージ済みでもブランチ削除はしない、PR 履歴用）
       - **self-edit (`pattern_variant='live_repo_worktree'`) の場合**: worktree base が `{claude_org_path}` なので `git -C {claude_org_path} worktree remove .worktrees/{task_id}` を実行する（Issue #289）。ブランチは同様に残す
     - パターン C（エフェメラル）: ディレクトリは保持する（容量が問題になった場合のみ手動削除を検討）
   - **DB 経由で Worker Directory Registry を更新する**（`StateWriter.transaction()` 経由、markdown 直接編集禁止）:
     ```bash
     python -c "
     from pathlib import Path
     from tools.state_db import connect
     from tools.state_db.writer import StateWriter
     conn = connect('.state/state.db')
     with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
         w.update_run_status('<task_id>', 'completed')
         # パターン B / C のエントリ削除はここで w.remove_worker_dir('<abs>') を追加
     "
     ```
     - パターン A: lifecycle='active' のまま、run.status='completed' で snapshotter が available 相当の表示にする
     - パターン B / C: 物理 dir は別途処理（worktree remove / dir 保持）。レジストリエントリ削除は上記 with ブロック内に `w.remove_worker_dir('<abs>')` を追加
   - JSON snapshot は StateWriter post-commit hook が自動再生成 (Issue #284)

2c. 人間がフィードバック・修正指示を出した場合:
   - ワーカーに renga-peers で追加指示を送る (`to_id="worker-{task_id}"`)
   - 追加指示が trivial fix（CI 出力整形 / typo / コメント修正等）なら **検証深度 `minimal`** を明示し、完了報告は `done: {commit SHA 短縮形} {変更ファイル名}` の 1 行だけで返すよう伝える（フォーマットは `references/instruction-template.md` / `references/worker-claude-template.md` に従う）
   - **DB 経由で run を IN_PROGRESS に戻す**（`run.status='in_use'`、markdown 直接編集禁止。post-commit hook が `.state/org-state.md` を再生成）:
     ```bash
     python -c "
     from pathlib import Path
     from tools.state_db import connect
     from tools.state_db.writer import StateWriter
     conn = connect('.state/state.db')
     with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
         w.update_run_status('<task_id>', 'in_use')
     "
     ```
   - DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)（`tools/journal_append.py` が DB ルーティング済み）
   - JSON snapshot は StateWriter post-commit hook が自動再生成 (Issue #284)
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
