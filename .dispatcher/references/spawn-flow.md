# Spawn flow — ワーカー起動と状態記録（ディスパッチャーが実行）

`.dispatcher/CLAUDE.md` から参照される、`DELEGATE` 受信後のワーカー起動 / 指示送信 / 状態記録の詳細手順。**ディスパッチャー専属**で、窓口は触らない。判定ロジックは `claude-org-runtime dispatcher delegate-plan` helper に委譲済み。本ファイルは MCP 呼び出しと state 書き込みの具体手順をまとめる。

> **state-db cutover (M4, Issue #267)**: 構造化セクション
> (Worker Directory Registry / Active Work Items / Dispatcher / Curator / Status) の write は
> **必ず `StateWriter.transaction()` 経由**で行う。`transaction()` の post-commit hook が
> `.state/org-state.md` を DB から自動再生成するので、本 reference では markdown 直接編集の
> 手順を残さない (drift_check が検出する)。events は DB の `events` テーブルが SoT。

## Step 3: ワーカー起動と指示送信

### 3-1. balanced split で target / direction を決める

旧設計は序数 `k` ベースの lookup table で target を決めていたが、ワーカーが途中で閉じた後の再派遣や想定外の退役順でテーブル前提と実レイアウトが乖離し、`[split_refused]` を誘発しやすかった。renga-peers MCP の `mcp__renga-peers__list_panes` が各ペインの `id / name / role / focused / x / y / width / height` (cell 単位) を返すため、**現在のレイアウト (rect) から動的に target と direction を選ぶ方式**を取る。詳細ルールは [`../.claude/skills/org-delegate/references/pane-layout.md`](../../.claude/skills/org-delegate/references/pane-layout.md) の「ワーカーの balanced split 戦略」セクションを参照。

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

dispatcher が helper を経由しない degraded mode に入った場合、判定再現は `claude_org_runtime.dispatcher.runner` モジュール (インストール先は `python -c "import claude_org_runtime.dispatcher.runner; print(claude_org_runtime.dispatcher.runner.__file__)"` で解決可能) を一次参照する。

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
- ペイン配置ルールは [`../.claude/skills/org-delegate/references/pane-layout.md`](../../.claude/skills/org-delegate/references/pane-layout.md) を参照
- **同一タブ内 spawn で起動する理由**: renga の `list_panes` / `focus_pane` / `send_message` / `inspect`（CLI） は現在フォーカス中のタブのペインしか見えない。`new_tab` で別タブに置くとディスパッチャーからの監視・指示送信が不能になる（renga 側 issue: suisya-systems/renga#71）
- `name="worker-{task_id}"`: 後続の `mcp__renga-peers__send_message(to_id="worker-{task_id}", ...)` や `close_pane(target="worker-{task_id}")` で addressable にする安定名。**全桁数字は id 扱いになる** ので、`worker-` プレフィックス等で英字を必ず含める
- `role="worker"`: `list_panes` の結果で役割識別（次回以降の balanced split の target 選出にも使われる）
- `cwd` / `permission_mode` / `model` / `args[]` は `spawn_claude_pane` の構造化フィールド。renga が `claude --permission-mode {mode} --dangerously-load-development-channels server:renga-peers ...` を合成する。旧方式（`cd`-プレフィックス付き command 文字列を `spawn_pane` に渡す）は **禁止**
- 起動コマンドの仕様は `.claude/skills/org-start/SKILL.md` の「ClaudeCode 起動コマンド（役割別）」セクションを参照
- `spawn_claude_pane` が内部で `--dangerously-load-development-channels` を付与するため、`Load development channel?` 確認プロンプトが初回表示される。3-3b で `send_keys(enter=true)` による承認が必要
- **エラーハンドリング**: MCP 結果テキストに `[<code>] <msg>` 形式でエラーが埋まる。主な code:
  - `[split_refused]` (MAX_PANES / too small): [`../.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) の手順に従いキュレーター → 窓口に escalate
  - `[pane_not_found]`: `$target` に選んだ既存ペインが spawn 発行直前に閉じたレース。同じくエラーコード経路で escalate
  - `[cwd_invalid]`: 指定した cwd が存在しない / ディレクトリでない。窓口に escalate し、ワーカーディレクトリ準備（`.claude/skills/org-delegate/SKILL.md` Step 0.7 / 1 / 1.5 / 2）が完了しているか確認
  - `[invalid-params]`: `args[]` に `--permission-mode` / `--model` / `--dangerously-load-development-channels` を含めた場合の拒否。構造化フィールドで渡す
  - その他の code は同 reference 参照

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

[`../.claude/skills/org-delegate/references/instruction-template.md`](../../.claude/skills/org-delegate/references/instruction-template.md) のフォーマットに従う。`to_id="worker-{task_id}"` で pane name 指定。

### 3-6. 複数ワーカーの順次起動

複数ワーカーがある場合は 3-1〜3-5 を順次繰り返す。`list_panes` の結果が毎回変わるので、**都度再取得して** balanced split 判定をし直す（前ワーカーの起動が完了するのを 3-3 / 3-4 で待ってから次に進むこと）。

## Step 4: 状態記録

> **delegate-plan helper との分担**: `.dispatcher/CLAUDE.md` の delegate-plan helper が `.state/workers/worker-{task_id}.md` を `Status: planned` で生成し、`worker-instruction.md` も書き出す（ready_to_spawn 時）。本 Step 4 はその後 dispatcher が **MCP 呼び出し成功後に行う** active 遷移 + DB 登録 + events 追記をまとめる。worker file テンプレートを再生成するのではなく、helper 出力に Progress Log を追記して Status を `active` に flip する。

各ワーカーについて:

1. helper が生成した `.state/workers/worker-{task_id}.md` の Status を `planned` → `active` に更新し、Progress Log に「派遣完了、作業開始」を追記する（renga-peers では pane name `worker-{task_id}` が安定識別子。旧 peer-id は使わない）。helper 未経由 / 構造化 DELEGATE が無いフォールバック経路の場合のみ、以下のテンプレートで新規作成する:
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
   event 名と payload key の規約は [`docs/journal-events.md`](../../docs/journal-events.md) を参照。

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
- 窓口の `gen_delegate_payload.py apply` でディレクトリ準備時にエントリを追加する
- 窓口の `.claude/skills/org-pull-request/SKILL.md` 2b-ii でクローズ条件（PR マージ / 明示クローズ指示 / 長期 idle 判断）を満たした時にステータス更新・エントリ削除を行う（2b-i の PR 作成段階では触らない）
- `gen_delegate_payload.py` の Pattern 判定フローでこのテーブルを参照し、再利用可能なディレクトリや並行作業の有無を判定する

5. 窓口 (`secretary`) に renga-peers で派遣完了を報告:
   ```
   DELEGATE_COMPLETE: {task_id} のワーカーを派遣しました。
   Pane: worker-{task_id} (id={pane_id})
   ```
