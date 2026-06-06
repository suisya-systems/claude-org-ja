# 検証方法

各機能の動作確認手順。問題が見つかったらスキルやCLAUDE.mdを修正し、再テストする。

**前提**: renga 0.18.0+ （`npm install -g @suisya-systems/renga@0.18.0` 後、`renga mcp install --force` で `renga-peers` MCP サーバを user-scope 登録済み）。structured `cwd` (0.16.0) / `set_pane_identity` (0.17.0) / `spawn_claude_pane` (0.18.0) すべてを前提とする。

---

## 0. リグレッションチェック（起動テンプレートの退行防止）

**目的**: Issue #58 で撤去した `cd X && claude ...` auto-upgrade 迂回パターンがテンプレート / スキル / docs に再導入されていないか検出する。

**手順**:
```bash
# (1) spawn_pane / spawn_claude_pane の command 引数に `cd X && claude` を合成している箇所
#     (禁止: renga の bare-claude auto-upgrade が発動せず channel push が届かない)
grep -rEn 'command="cd [^"]*&&[[:space:]]*claude' --include="*.md" --include="*.toml" . \
  && { echo "FAIL: cd&&claude 合成が残っている"; exit 1; } \
  || echo "OK: no cd&&claude synthesis"

# (2) ops.toml / layout TOML の command 行に `claude --dangerously-load-development-channels` を手書き
#     (renga 0.16.0+ の bare-claude auto-upgrade / spawn_claude_pane の自動付与で不要)
grep -En '^[[:space:]]*command[[:space:]]*=.*dangerously-load-development-channels' renga-layouts/*.toml \
  && { echo "FAIL: layout で dangerously フラグ手書き"; exit 1; } \
  || echo "OK: no explicit flag in layout TOML"
```

**期待結果**:
- (1) は 0 件（prose 内での解説的な言及は引用符／記法が異なるためヒットしない）
- (2) は `renga-layouts/*.toml` で 0 件

**失敗時の対処**:
- ヒットした場合は `spawn_claude_pane` の構造化フィールド (`cwd` / `permission_mode` / `model`) に書き換える。詳細は `.claude/skills/org-start/SKILL.md`「ClaudeCode 起動コマンド（役割別）」セクションと `.claude/skills/org-delegate/references/pane-layout.md` を参照

## 0. 互換性プリフライト

**目的**: `/org-start` 実行前に renga バージョンと MCP ツール surface が claude-org の要件を満たすか確認する（Issue #61）。

**手順**:
```bash
py -3 tools/check_renga_compat.py            # Windows
python3 tools/check_renga_compat.py          # macOS / Linux
py -3 tools/check_renga_compat.py --json     # 機械可読出力
```

**期待結果**: `Result: OK` で終了コード 0。renga バージョン・`renga-peers` MCP 登録・必須 14 ツールすべてが揃っていれば合格。

**失敗パターン**:
- renga バージョン不足 → `npm update -g @suisya-systems/renga`
- MCP 未登録 → `renga mcp install`
- ツール欠如 → `renga mcp install --force` で stale 登録を更新

このスクリプトは live renga セッションを必要としない（`renga mcp-peer` stdio 経由で tools/list を取得する静的 probe）。

---

## 1. 基本起動テスト

**目的**: clone先でClaudeCodeを起動し、CLAUDE.mdとスキルが正しく読み込まれるか確認。

**手順**:
1. 任意の場所に本リポジトリを `git clone`
2. clone先で `renga --layout ops` を実行 (窓口ペインが立ち上がる)
3. 窓口の Claude Code で `mcp__renga-peers__list_panes` が疎通するか確認（Step 0 の MCP 有効性 chk）
4. 窓口の Claude Code で `/org-start` を実行

**期待結果**:
- `.state/org-state.md` が存在しないので、初回起動と判断される
- `mcp__renga-peers__spawn_claude_pane` でディスパッチャーペインが窓口の下に開く（キュレーターペインは開かない — オンデマンド化済み）
- Dispatcher 起動直後の「開発チャネル確認プロンプト」を `mcp__renga-peers__send_keys(target=<pane>, enter=true)` で Enter 注入して通過している（`org-start` SKILL Block D-1 の手順）
- state.db の `curator_pane_id` / `curator_peer_id` が `StateWriter.CLEAR` で null になっている（null が正常系）
- 「初回起動です。何をしましょうか？」と報告される

**失敗パターンと対処**:
- CLAUDE.mdが読み込まれない → `.claude/` ディレクトリ配置を確認
- スキルが認識されない → `.claude/skills/*/SKILL.md` のfrontmatter形式を確認
- `/org-start` が発動しない → スキル名の競合やdescriptionを確認
- `mcp__renga-peers__list_panes` で error → `renga mcp install --force` を再実行、`claude mcp list` で登録確認
- `send_keys(enter=true)` が Enter 注入に失敗 → Dispatcher ペインが「Load development channel?」プロンプトで止まっているか確認、手動で Enter

---

## 2. org-delegate テスト（ワーカー派遣）

**目的**: ワーカーが正しく派遣され、作業を完了し、結果が報告されるか確認。

**前提**: `renga --layout ops` で起動していること、`renga-peers` MCP が有効なこと（`claude mcp list` で Connected 確認）。テスト1で `/org-start` 済み。

**手順**:
1. 窓口Claudeにタスクを依頼する（例:「ブログに新しい記事を追加して」）
2. 新規プロジェクトの場合、通称・パス・説明の確認が入るので回答する
3. 窓口が `/org-delegate` でワーカーを派遣するのを確認

**期待結果**:
- プロジェクトが `registry/projects.md` に自動登録される
- 窓口がディスパッチャーに DELEGATE メッセージを送信し、すぐにユーザーとの対話に戻る
- ディスパッチャーが `mcp__renga-peers__spawn_claude_pane` で同一タブ内にワーカーペインを派生する（`name="worker-{task_id}"`、balanced split 戦略は `pane-layout.md` に従う）
- ディスパッチャーが `mcp__renga-peers__poll_events(types=["pane_started"])` で起動完了を確認
- ワーカー起動直後の「開発チャネル確認プロンプト」を `mcp__renga-peers__send_keys(target="worker-{task_id}", enter=true)` で Enter 注入して通過（`org-delegate` SKILL Step 3-2）
- ディスパッチャーが `mcp__renga-peers__send_message` 経由でワーカーに作業指示を送信する
- ディスパッチャーが `.state/workers/worker-{id}.md` を作成する
- `.state/org-state.md` が作成/更新される
- `.state/journal.jsonl` にイベントが記録される
- ワーカー完了後、`renga-peers` 経由で**窓口に**報告が届く（ディスパッチャーではなく窓口）
- 窓口が結果を業務言語で人間に伝える（技術用語を避ける）
- 窓口がディスパッチャーにペインクローズを依頼する（ディスパッチャーは `mcp__renga-peers__close_pane(target="worker-{task_id}")` で破棄）

**確認コマンド**:
```bash
cat .state/org-state.md
cat .state/journal.jsonl
ls .state/workers/
cat registry/projects.md
```

ペイン状態の確認は MCP ツールで:
```
mcp__renga-peers__list_panes    # 現在のペイン一覧
```

**失敗パターンと対処**:
- ペインが開かない → `mcp__renga-peers__list_panes` で現在のペイン状態を確認、tool result の `[split_refused]` / `[pane_not_found]` を `references/renga-error-codes.md` で分岐
- renga-peers で通信できない → `claude mcp list` で `renga-peers` が Connected か確認、`list_peers` で peer ID（`worker-{task_id}` / `dispatcher` / `curator` / `secretary`）を確認
- 状態ファイルが作成されない → org-delegateスキルの手順を見直し
- ワーカーが指示を理解しない → instruction-template.md の記述を改善
- プロジェクト名前解決が動かない → org-delegate Step 0 を見直し

### 2.1 balanced split スケール検証（4 並列 / 8 並列）

**目的**: org-delegate Step 3 の rect ベース balanced split が、4 並列・8 並列いずれも `[split_refused]` を発生させずに期待通りの tree を生成することを実機確認する。

**前提**: テスト 2 が通っていること。ターミナル幅 `W ≥ 160 cols`（`tput cols` で確認）。`pane-layout.md` の「ワーカーの balanced split 戦略」セクションを手元で開いておく。

**手順**:
1. `tput cols` を実行し W を記録。160 未満なら検証不能としてスキップ or ターミナルを広げる。
2. 窓口に互いに独立な 8 タスク（ダミーで良い。例: `echo-1` 〜 `echo-8` のような軽量タスク）を順次依頼。k=1〜8 それぞれが以下を満たすことを確認:
   - a. ディスパッチャーの `mcp__renga-peers__spawn_claude_pane` 呼び出し結果テキストに `[split_refused]` が含まれない
   - b. Step 3-1b のアルゴリズムが選出した `target` / `direction` が、`list_panes` の直前スナップショットから rect ベースで再現可能
   - c. 起動直後の `mcp__renga-peers__list_panes` を **別ログファイル (例: `.state/verification/balanced-split-{timestamp}.log`)** に保存するか、その場で `role == "worker"` の `name` / `id` を記録し、`.state/journal.jsonl` の `worker_spawned` イベントと事後照合する
3. 各 k 到達時点でペイン配置を `list_panes` で取得し、Step 3-1b のアルゴリズム（curator 特定 → role filter (4 役すべて候補) → dispatcher-curator 隣接判定 → direction 判定 → `new_w / new_h` 計算 → MIN_PANE 制約 → SECRETARY 保険条項 (`new_w >= 140` かつ `new_h >= 30`) → **(role priority desc, metric desc, id asc) sort** ※ role priority = secretary 4 > curator 3 > worker 2 > dispatcher 1）をスナップショットに対して手計算で再現できることを確認する。`pane-layout.md` の「ワーカーの balanced split 戦略」で述べている通り、rect ベース動的配置なので 2×2 や 2×4 のような固定グリッド形状は成功基準にしない。
4. 9 人目のダミータスクを試し、ディスパッチャーが `renga-peers` で窓口に `SPLIT_CAPACITY_EXCEEDED` を送信することを確認。**当該 9 人目のワーカーのみ派遣を中止し、ディスパッチャー本体の監視ループは継続稼働**すること（`spawn_claude_pane` は発行されず、`exit` などでディスパッチャーが落ちない）。

> **注**: `.state/verification/balanced-split-{timestamp}.log` 等の検証用ログは一時ファイルなのでコミット対象外。`.state/*` は既存の `.gitignore` で除外済み。

**期待結果**:
- k=1〜8 で `[split_refused]` ゼロ
- 各 k の配置が Step 3-1b の判定結果と一致（固定グリッド形状は要求しない、rect 動的配置が正しく動くこと）
- MIN_PANE 制約（`new_w ≥ 20` / `new_h ≥ 5`）に触れない範囲で候補が空にならない
- k=9 で明示的 escalate（silently fail しない）

**確認コマンド**:
```bash
tput cols                                # ターミナル幅の記録
cat .state/journal.jsonl | grep worker_spawned
```

ペイン状態は MCP で:
```
mcp__renga-peers__list_panes             # 各 k 到達時のスナップショット
```

**失敗パターンと対処**:
- k=4 で `[split_refused]` → `tput cols` の値を確認。W < 160 なら balanced split の要件未満。ターミナル拡大で再試行
- k=3 で既に `[split_refused]` → dispatcher 直下に file-tree / preview が居座っていないか確認（これらが表示中だと `W_f` が 20〜40 cols 目減りする）
- 配置が期待と乖離 → 前タスクで閉じ残ったワーカーの `close_pane` 忘れ。`list_panes` で role=worker の active が 0 からスタートしているか確認
- k=9 で silently 動く → Step 3-1c (`SPLIT_CAPACITY_EXCEEDED` escalate) の分岐が発火していない。Step 3-1b の判定ロジックが「候補空」を正しく返しているか確認

---

## 3. org-suspend テスト（中断）

**目的**: 組織の状態が正しく保存され、全ペインが停止するか確認。

**前提**: テスト2でワーカーが稼働中（または完了直後）の状態。

**手順**:
1. 窓口Claudeに「中断して」と伝える
2. `/org-suspend` が発動するのを確認

**期待結果**:
- `mcp__renga-peers__send_message` 経由でワーカーに SUSPEND メッセージが送信される
- ワーカーが `renga-peers` 経由で状態を報告する。未応答ワーカーは `mcp__renga-peers__inspect_pane(target="worker-{task_id}", format="text")` で画面内容を読み、git 状態と組み合わせて推定する
- `.state/org-state.md` の Status が `SUSPENDED` になる
- `.state/org-state.prev.md` にバックアップが作成される
- `mcp__renga-peers__send_message` で全ピアに SHUTDOWN が送信される
- `mcp__renga-peers__poll_events(types=["pane_exited"], timeout_ms=10000)` で pane_exited を待機、`role == "worker"` をまとめて消化
- 残留ワーカーは `mcp__renga-peers__close_pane(target="worker-{task_id}")` でフォールバッククローズ
- 全ワーカーペインが先に閉じられ、次にディスパッチャー、最後にキュレーターが閉じられる
- 窓口が中断完了を報告する

**確認コマンド**:
```bash
cat .state/org-state.md | head -5  # Status: SUSPENDED を確認
cat .state/journal.jsonl | tail -1  # suspend イベントを確認
```

**失敗パターンと対処**:
- ワーカーがSUSPENDに応答しない → `inspect_pane` で画面内容を確認、Phase 2 のスクレイプが機能するか確認
- ペインが閉じない → `close_pane(target="X")` の結果テキストをチェック。`[pane_not_found]` / `[pane_vanished]` は skip 扱い
- `[last_pane]` が出た → 最後の窓口ペインは自己 exit で自然終了させる（org-suspend は閉じない）
- 状態ファイルが不完全 → org-suspendの手順を見直し

---

## 4. org-resume テスト（再開）

**目的**: 中断後に再起動し、前回の状態が正しく復元されるか確認。

**前提**: テスト3で中断済み。

**手順**:
1. 窓口Claudeの端末を**完全に閉じる**
2. clone先で再度 `renga --layout ops` で起動する
3. `/org-start` を実行する

**期待結果**:
- `/org-start` の Block A が Step 0 直後に dispatcher の `spawn_claude_pane` を発火する（Issue #410 の並列化以降。boot 完了は待たず Block B / C を並行進行。curator はオンデマンド化により spawn されない）
- Block B が `.state/state.db` をクエリし、Status: SUSPENDED を確認
- `/org-resume` の Phase 1〜3（ブリーフィング・git 状態照合・再開計画提案）が裏で Claude boot 中に進行する
- 再開計画提示後、**Phase 4 のワーカー再派遣は人間の承認を待つ**（勝手に派遣しない）
- 承認前にすでに dispatcher ペインは `mcp__renga-peers__list_panes` 上に出現済みである（Block A で先行発火 → Block D で peer 登録合流。curator ペインが無いことは正常）

**確認ポイント**:
- ブリーフィング内容が `.state/org-state.md` と一致するか
- git状態の照合が正確か
- ディスパッチャーペインが起動しているか（`mcp__renga-peers__list_panes` で確認。curator ペインは存在しないのが正常）

**失敗パターンと対処**:
- `/org-start` が状態を読まない → org-start スキルの Block B (前回状態の DB 読み込み) を見直し
- 状態が不正確 → org-state.md のフォーマットまたはorg-suspendの書き込みを見直し
- ディスパッチャーが起動しない → org-start Block A (spawn_claude_pane) / Block D (送信・peer 登録) を確認

---

## 5. 突然の終了テスト（クラッシュリカバリ）

**目的**: org-suspendを実行せずに端末を閉じた場合、どこまで復元できるか確認。

**手順**:
1. テスト2の状態（ワーカー稼働中）で、**suspendせずに**端末を閉じる
2. 再度 `renga --layout ops` で起動する
3. `/org-start` を実行する

**期待結果**:
- `/org-start` が `.state/org-state.md` を検出し、Status が ACTIVE のままであることを確認
- 前回のセッションが突然終了したと判断し、各ワーカーディレクトリのgit状態を確認
- `.state/journal.jsonl` からスナップショット以降のイベントが補完される
- 現状を報告する

**許容される劣化**:
- ワーカーの自己申告による詳細な進捗情報は失われる
- journal.jsonl の最後のエントリ以降の情報は失われる
- git commitされていない作業は状態が不明確になる可能性がある
- Dispatcher の `poll_events` cursor (`.state/dispatcher-event-cursor.txt`) 消失時は過去 5 秒分のイベントを取りこぼす可能性があるが、`list_panes` 突き合わせで回復可能

**失敗パターンと対処**:
- org-state.md が古すぎる → 定期スナップショットの頻度を上げる（org-delegateの進捗管理を強化）
- journal.jsonl がない → ジャーナリングの実装を確認

---

## 6. org-retro テスト（振り返り）

**目的**: タスク完了後に学びが正しく記録されるか確認。

**手順**:
1. ワーカーが何らかのタスクを完了する
2. 窓口が `/org-retro` を実行するのを確認

**期待結果**:
- 再利用可能な知見があれば `knowledge/raw/YYYY-MM-DD-{topic}.md` が作成される
- フォーマットが「事実→判断→根拠→適用場面」に従っている
- 記録不要と判断された場合は何も作成されない（正しい判断）

**確認コマンド**:
```bash
ls knowledge/raw/
cat knowledge/raw/*.md  # フォーマット確認
```

---

## 7. org-curate テスト（知見整理 + 自己成長ループ）

**目的**: キュレーターが知見を整理し、改善提案ができるか確認。

**前提**: `knowledge/raw/` に5件以上の未整理ファイルがあること。

**手順**:
1. テスト用に `knowledge/raw/` に5件以上のダミー知見ファイルを作成
2. `py -3 tools/check_curate_threshold.py` を実行し、exit 10 + `reasons` に `raw_threshold` が立つことを確認
3. 手動で `/org-curate` を実行（または worker をクローズしてディスパッチャー経由のオンデマンド起動を待つ）

**期待結果**:
- rawファイルがテーマ別に分類される
- `knowledge/curated/{theme}.md` にテーマ別ファイルが作成される
- 処理済みrawファイルが `knowledge/raw/archive/` に move され、先頭に `<!-- curated -->` が追記される
- 改善提案がある場合、`renga-peers` で窓口に通知される
- 最後に dispatcher 宛て direct send で `CURATE_DONE` が送られる（dispatcher 起動中の場合）

**確認コマンド**:
```bash
# POSIX (bash)
python3 tools/check_curate_threshold.py; echo "exit=$?"
# Windows (PowerShell — $? は真偽値なので $LASTEXITCODE を使う)
py -3 tools/check_curate_threshold.py; echo "exit=$LASTEXITCODE"
ls knowledge/curated/
cat knowledge/curated/*.md
head -1 knowledge/raw/archive/*.md  # <!-- curated --> マーカー確認
```

**自己成長の確認**:
- 改善提案の内容が具体的か
- 提案が人間の承認なしに実行されていないか

---

## 8. ディスパッチャー・オンデマンドキュレーターテスト

**目的**: ディスパッチャーが正しく起動し、worker クローズ時の閾値超過でキュレーターがオンデマンド起動されるか確認。

**手順**:
1. `/org-start` を実行し、ディスパッチャーのペインが起動されるか確認（キュレーターは起動されない）
2. ディスパッチャーが `mcp__renga-peers__send_message` 経由で役割指示を受け取っているか確認
3. `knowledge/raw/` に閾値未満のファイルを置いた状態で worker をクローズし、curator が起動**されない**こと（`check_curate_threshold.py` exit 0）を確認
4. 閾値以上に増やして worker をクローズし、curator ペインが一時起動 → `/org-curate` 1 回実行 → `CURATE_DONE` 受領後にペインが閉じられることを確認

**期待結果**:
- `/org-start` 実行後に窓口の下にディスパッチャーが開く（`mcp__renga-peers__list_panes` で確認。curator ペインは無い）
- ディスパッチャーが DELEGATE メッセージを待ち受ける状態になる
- worker クローズ時のみ閾値チェックが走る（`.dispatcher/references/pane-close.md` Step 5）
- 閾値未満では curator は起動されず、閾値以上で一時起動 → 完了後クローズ

**失敗パターンと対処**:
- ペインは開くが指示を受け取らない → `renga-peers` のピア検出タイミングを調整（`list_peers` のリトライ、pane_started イベント待ちの延長）
- ディスパッチャーが DELEGATE に反応しない → ディスパッチャーへの初期メッセージの内容を見直し
- curator が起動されない → `py -3 tools/check_curate_threshold.py` を手動実行して exit code / reasons を確認
- curator ペインが残留する → `.state/dispatcher/curate-inflight.json` の有無と監視ループの Step 5.3（CURATE_* 受領 / 20 分 timeout 管理）が回っているか、CURATE_* direct send の到達を確認

---

## 9. org-dashboard テスト（ダッシュボード）

**目的**: ダッシュボードのライブサーバー起動とブラウザ表示が正しく動作するか確認。

**前提**: テスト2でワーカー派遣とプロジェクト登録が完了していること。

**手順**:
1. 窓口に「ダッシュボード見せて」と伝える
2. `/org-dashboard` が発動するのを確認

**期待結果**:
- `dashboard/server.py` が起動し `http://localhost:8099` でサーバーが立ち上がる
- ブラウザで `http://localhost:8099` が開かれる
- プロジェクト一覧、作業状況、アクティビティ、知見が表示される
- `/api/state` のレスポンスが実際の状態と一致する

**失敗パターンと対処**:
- サーバーが起動しない → `dashboard/server.py` のエラー出力を確認
- ブラウザでデータが表示されない → `http://localhost:8099` が応答しているか `curl` で確認
- データが更新されない → SSEの接続状態（`/api/events`）を確認

---

## 10. E2Eテスト（全サイクル）

**目的**: 起動→作業→中断→再開→知見整理の全サイクルが機能するか確認。

**手順**:
1. clone先でClaudeCodeを起動（`renga --layout ops`）
2. `/org-start` を実行（初回起動）
3. タスクを3つ依頼（ワーカー派遣が発生するもの）
4. 各タスク完了後に振り返りが記録されることを確認
5. 「ダッシュボード見せて」で全体像を確認
6. `/org-suspend` で中断
7. 端末を完全に閉じる
8. 再度起動 → `/org-start` → 前回の状態が報告される
9. 再開を承認 → ワーカーが再派遣される
10. `knowledge/raw/` が閾値に達したらキュレーションが動くか確認
11. curated知見をgit commit → push できるか確認

**成功基準**:
- 全ステップが人間の介入なし（指示と承認以外）で完了する
- 状態の損失がない
- 知見が蓄積・整理される
- ダッシュボードで全体像が確認できる

---

## 10.1. sandbox.denyRead / denyWrite 実機検証（Phase 2a, Issue #79）

**目的**: `.claude/settings.json` の `sandbox.filesystem.denyRead` / `denyWrite` が Windows + Git Bash 環境で期待通り機能し、Claude Code の Bash ツール経由で `.env` 等の秘密情報ファイルが読めないことを確認する。

**前提**:
- 本リポジトリを clone し Claude Code が起動できる状態
- 検証対象リポジトリ直下にダミー `.env`（例: `FAKE_TOKEN=dummy-not-a-real-secret`）を用意（`.gitignore` 対象のため commit されない）
- 既知バグ [anthropics/claude-code#32226](https://github.com/anthropics/claude-code/issues/32226) により denyRead が期待通り効かないケースが報告されているため、**必ず実機で挙動を確認**する
- **個人 sandbox 補強の事前適用（Issue #429 Task B/C + Issue #433）**: 手順 3 の `~/.ssh/id_rsa` 読出と手順 4 の `~/.claude/settings.json` 書込はいずれも **個人 `~/.claude/settings.json` 側に移管された** deny を経由する。検証前に `python tools/org_setup_prune.py --user-common-sandbox` を 1 回実行し、個人 `~/.claude/settings.json` の `sandbox.filesystem.denyRead` / `denyWrite` に当該 entry が merge 済みである状態にしてから手順 1〜4 を実施すること。実行しないまま手順 3 / 4 を確認すると、共有 settings 側に entry が無いため何も deny されず誤った "fail" 判定になる

**手順**:
1. 窓口 Claude に `cat .env` を実行するよう依頼する（Bash ツール経由）
2. `grep -r FAKE_TOKEN .` のように `.env` を読み出すコマンドを依頼する
3. `~/.ssh/id_rsa` を読み出そうとするコマンドを依頼する（存在する場合）
4. `~/.claude/settings.json` の書込を試みるコマンドを依頼する（例: `echo x >> ~/.claude/settings.json`）

**期待結果**:
- 手順 1〜2: 共有 `.claude/settings.json` の `sandbox.filesystem.denyRead` (`.env` / `**/credentials*` 等) により Bash サブプロセスで denied（`Permission denied` 相当）
- 手順 3: 個人 `~/.claude/settings.json` の `sandbox.filesystem.denyRead` (`~/.ssh` 等、上記前提で merge 済み) により Bash サブプロセスで denied。merge していない環境では Claude Code 組込の credential 保護層が拾うケースもあるが、ここでは Layer 3 (sandbox) の検証として individual 確認する
- 手順 4: 個人 `~/.claude/settings.json` の `sandbox.filesystem.denyWrite` (`~/.claude/settings.json`、上記前提で merge 済み、Issue #433) により write 失敗

**失敗パターンと対処**:
- `.env` の内容が読めてしまう → Claude Code 側のバグの可能性。バージョンと `claude --version` を記録し Issue #32226 のステータスを確認。暫定対応として `permissions.deny` の `Read(./.env)` 追加（Claude Code の Read ツール経路を塞ぐ）
- Windows で glob (`**/credentials*`) が効かない → forward/backward slash 差異の可能性。glob パターンを `./credentials*` 等に調整して再試行
- sandbox 自体が発動していない → Claude Code の `sandbox.enabled` デフォルトが OFF の可能性。公式 docs の現行デフォルトを確認

**注**: `sandbox.enabled` は **現行の共有 [`.claude/settings.json`](../.claude/settings.json) で `true` を明示指定済み**（v0.1.0 の初期コミット時点から）。本節の旧版では未明示としていたが、その後共有 settings 側で明示 `true` 化された。既知バグ #32226 の影響を受ける環境では `false` への一時退避を検討するための judgment lever として残しているが、現在のデフォルト方針は明示 `true` であることに留意。

### 実測結果（2026-04-25, Windows 11 + Git Bash, Claude Code Desktop）

| # | 操作 | 結果 |
|---|---|---|
| 1 | `cat .env` | 読めた（sandbox 未発火） |
| 2 | `grep -r FAKE_TOKEN .` | 読めた（sandbox 未発火） |
| 3 | `cat ~/.ssh/id_rsa` | deny（※ sandbox ではなく Claude Code 組込の credential 保護層による） |

`sandbox.enabled: true` を明示した状態でも #1 #2 は素通り。公式ドキュメントで Windows native の sandbox enforcement は "planned" 状態（未実装）と確認（https://docs.claude.com/en/docs/claude-code/iam#sandbox）。本設定は **macOS (Seatbelt) / Linux / WSL2 (bubblewrap)** のみで有効。

### WSL2 実測結果（2026-04-25）

| 操作 | 結果 |
|---|---|
| `cat .env`（PR branch checkout 上） | 読めた（sandbox disabled） |
| `grep -r FAKE_TOKEN .` | 読めた |
| `claude` 起動時の警告 | `⚠ Sandbox disabled: bubblewrap (bwrap) not installed, socat not installed` |

**原因**: Claude Code の sandbox は Linux / WSL2 で **`bubblewrap` と `socat`** を runtime dependency として要求するが、Ubuntu / Debian 系の WSL イメージにはデフォルトでは含まれない。

**対処**: sandbox を実際に発動させたい WSL 環境では以下を実施する:

```bash
sudo apt install -y bubblewrap socat
claude  # 警告が消えることを確認
```

これを入れた後、次節の検証手順で改めて `cat .env` 等が deny されることを確認する。

**検出手順**: `claude --version` 直後 / `/sandbox` 実行で sandbox の状態を確認できる（Claude Code 側で警告表示）。CI 環境や Docker コンテナで sandbox を期待する場合は Dockerfile / workflow に `apt install bubblewrap socat` を明示すること。

### WSL2 での検証手順（未実施、人間タスク）

1. WSL2 内に本リポジトリを clone もしくは `\\wsl$\...` 経由で Windows 側 worktree を共有
2. WSL 側で `claude` を起動（wsl 用 Claude Code または `npm install -g` で導入）
3. **個人 sandbox 補強を適用** (Issue #429 Task B/C + Issue #433 で必須): `python tools/org_setup_prune.py --user-common-sandbox` を 1 回実行し、個人 `~/.claude/settings.json` の `sandbox.filesystem.denyRead` / `denyWrite` に entry を merge する
4. **個人 `~/.claude/settings.json` を退避** (deny が万一不発した場合の corruption 回避): `cp ~/.claude/settings.json ~/.claude/settings.json.preflight-backup`
5. 以下を依頼し、それぞれ deny を確認:
   - `cat .env を実行して` → 共有 settings の sandbox denyRead で Permission denied 相当
   - `grep -r FAKE_TOKEN . を実行して` → 同上
   - `echo x >> ~/.claude/settings.json` → 個人 settings の denyWrite で書込失敗（Issue #433 で追加された exact-match の denyWrite）。**deny が効かなかった場合** はファイル末尾に `x` が追記されるので、`cp ~/.claude/settings.json.preflight-backup ~/.claude/settings.json` で復元する
6. 実測結果を本セクションの表に追記（OS 行を増やす形）

WSL で deny されなければ、 (a) Claude Code のバージョンが sandbox 未対応、(b) 設定 syntax の解釈差異、(c) #32226 の別症状、(d) `--user-common-sandbox` の merge が反映されていない、のいずれか。バージョンと Issue #32226 ステータス、`~/.claude/settings.json` の `sandbox.filesystem.denyWrite` 内容を記録。

### Phase 2a portability fix ([Issue #83](https://github.com/suisya-systems/claude-org-ja/issues/83))

WSL2 などで `bubblewrap` 未導入時に sandbox init が silent no-op fallback して `~/.aws/**` / `~/.ssh/**` の denyRead/denyWrite が無効化される問題への対処として、**ホーム dotfile（`~/.aws` / `~/.ssh`）は sandbox の対象範囲外**とし、`permissions.deny` の `Read(~/.ssh/*)` / `Read(~/.aws/*)` で防御する。for portability, home dotfiles are out of sandbox scope。sandbox 側の `denyRead` / `denyWrite` はリポジトリローカルの `.env` / 認証情報ファイルに集中させる。

#### Addendum: Phase 2a 前提の撤回（Issue #429 Task A 調査結論）

**前提撤回**: 上記「`permissions.deny` の `Read(...)` で防御する」前提は **現行 Claude Code の documented behavior と乖離している**。`permissions.deny` への移管は portability fix として成立しない。

**根拠（Claude Code 公式 docs）**: <https://code.claude.com/docs/en/settings> の `sandbox.filesystem.denyRead` 説明:

> Paths where sandboxed commands cannot read. Arrays are merged across all settings scopes. **Also merged with paths from `Read(...)` deny permission rules.**

つまり `permissions.deny` に `Read(~/.aws/*)` を書いた時点で、明示的に `sandbox.filesystem.denyRead` に書かなくても、Claude Code 側で sandbox の effective denyRead 集合に同パスが加算される。**共有 settings に書こうが個人 settings に書こうが merge 挙動は同じ**で、permissions.deny への移管は sandbox bootstrap 失敗（WSL2 + bwrap で `~/.aws` が `/mnt/c/...` への symlink になっている環境などで `bwrap: Can't create file at /home/<user>/.aws/config` が出る case）を回避できない。

**実用上の portability fix（二択）**:

1. **対象環境で当該 symlink を退避/削除し、real directory を `mkdir -p` で作り直す**。`mkdir -p` 単発では既存 symlink を real directory に置き換えないため、`rm <link> && mkdir -p <dir>` 等の明示的置換が必要。
2. **当該環境では `Read(~/.aws/*)` / `Read(~/.ssh/*)` を共有・個人いずれの settings からも除外する**。bwrap の bootstrap 失敗を避けるための回避策。**ただしこの選択は意図的に Claude 側の credential read 防御を弱める残存リスクを含む**: (a) Claude Code は同一ユーザー権限で動くため OS のファイルパーミッションは Claude プロセス自身を止めない、(b) claude-org-runtime の WSL Layer 3 suppression (§10.2 Phase 3 case E) は escape する Layer 3 entry を **emit 段階で落とす** 動作で、deny を強化するわけではない、(c) `--user-common-sandbox` も同 candidate を skip する。残るのは Claude Code 組込の credential 保護層 (`~/.ssh/id_*` 等の特定 path に対するもの) と Layer 4 hook / role 契約のみで、`~/.aws/credentials` の `cat` を完全に止める保証は無い。symlink-escape 環境では選択肢 1 (real directory 化) が望ましく、選択肢 2 はそれが運用上不可能な場合の妥協策と位置付ける。

claude-org-ja 本体は Issue #429 Task C（本 addendum と同 PR）で共有 `.claude/settings.json` から `Read(~/.ssh/*)` / `Read(~/.aws/*)` / `~/.config/gh/hosts.yml` を除去した（= 上記選択肢 2 を採用）。個人環境ごとに実在する（= symlink でない）機密ディレクトリを deny したい場合は、Issue #429 Task B で導入された `python tools/org_setup_prune.py --user-common-sandbox` を 1 回実行することで `~/.claude/settings.json` の `sandbox.filesystem.denyRead` に directory-level deny がマージされる（symlink-escape candidate は自動 skip）。詳細は [`.claude/skills/org-setup/references/permissions.md`](../.claude/skills/org-setup/references/permissions.md) の「ユーザー共通の sandbox denyRead / denyWrite 補強（`--user-common-sandbox`）」節を参照。

**upstream への enhancement request（任意）**: 「`Read(...)` / `Edit(...)` の `permissions.deny` を `sandbox.filesystem.denyRead` に merge する際、realpath が `sandbox_read_roots` を escape する path は除外する」改善が Claude Code 側に入れば、本 portability 問題は構造的に解消する。本タスクのスコープ外。

調査ログ全文は [Issue #429 のコメント](https://github.com/suisya-systems/claude-org-ja/issues/429#issuecomment-4419741705) を参照。

#### Addendum 2: denyWrite の merge セマンティクス確認と移管（Issue #433）

**前提確認**: Claude Code 公式 docs <https://code.claude.com/docs/en/settings> の `sandbox.filesystem.denyWrite` 説明（2026-05 時点）:

> Paths where sandboxed commands cannot write. Arrays are merged across all settings scopes. **Also merged with paths from `Edit(...)` deny permission rules.**

つまり denyWrite も denyRead と対称に、(a) 複数 settings scope（user / shared / project）の array が merge される、(b) Layer 2 (`permissions.deny` の `Edit(...)`) のパスが effective set に追加される。「`Edit(...)` のみで `Write(...)` には言及されていない」点に注意 — `Write(...)` も新規ファイル書き込み tool だが、公式 docs では明示的に挙げられていない（実機での `Write(...)` → `denyWrite` merge 検証は本 PR スコープ外、現状は doc 通り `Edit(...)` のみが Layer 3 へ自動 mirror すると解釈する）。

**移管の決定**: Issue #429 Task C と Issue #433 で **`~/.claude/settings.json` への denyWrite も共有 `.claude/settings.json` から個人 `~/.claude/settings.json` 側へ移管** した（Task C の denyRead 移管と対称、Task B の `--user-common-sandbox` フラグを単一のまま denyRead + denyWrite 双方を扱うよう拡張）。理由:

1. **個人ごとの opt-out 可能性**: 共有 settings に `denyWrite: ["~/.claude/settings.json"]` を置くと、repo を pull する全ユーザーの home path に対して deny が適用される。個人 settings 側へ移すと、各操作者が自分の `~/.claude/settings.json` を編集して撤回でき、ロール契約と個人運用の整合が取りやすい。
2. **保管場所と意図の一致**: 「個人の `~/.claude/settings.json` を sandbox subprocess 経由の書き込みから守る」という意図と、deny 自身が記述される場所（個人 `~/.claude/settings.json`）が物理的に一致する。共有 settings 側にあると「誰が誰の home dir を守っているのか」が不透明になりやすい。
3. **defense-in-depth の維持**: 公式 docs の merge ルールに従い、Layer 2 (`permissions.deny Edit(~/.claude/settings.json)`) と Layer 3 (`sandbox.filesystem.denyWrite`) は effective set で合流する。Layer 3 を個人側へ落としても、Layer 2 を共有 settings 側で必要に応じて宣言できるため、defense-in-depth は idempotent に再構成可能。

**preventive deny（=ファイル不在でも merge）の判断**: `~/.claude/settings.json` は fresh install の Claude Code が初回起動時に作成する。`--user-common-sandbox` を **`~/.claude/settings.json` 作成前に**実行した場合に entry を skip すると、初回 Claude Code 起動と次の `--user-common-sandbox` 実行の間に bwrap subprocess の write が素通りする時間窓ができる。これを避けるため、denyWrite candidate は **存在チェックを行わず常に merge** する（denyRead が directory 単位かつ bwrap bootstrap 失敗 risk があるため existence-check を行うのとは非対称）。

**残存検証 (実機、未実施)**: §10.1 の現状表は Windows native（sandbox 未実装、`cat .env` 等が素通り）と WSL2 (`bubblewrap` 未導入で sandbox disabled) の 2 行のみで、macOS / Linux / WSL2 + bubblewrap 導入後の denyWrite 実機確認は本 PR 時点で **未実施**。Issue #433 の正常系（`echo x >> ~/.claude/settings.json` が、`--user-common-sandbox` 適用後の個人 `~/.claude/settings.json` の `denyWrite` で deny される）の確認は §10.1 手順 4 の人間タスクとして残し、結果を本節の表に追記すること。Windows native での deny 不発は §10.1 が既に記録しており、本 addendum でも追加検証は不要（sandbox 自体が未実装のため）。

**実装**: `tools/org_setup_prune.py` の `merge_user_common_sandbox_denywrite` / `USER_COMMON_SANDBOX_DENYWRITE_CANDIDATES`、`tools/test_org_setup_prune.py` の `MergeUserCommonSandboxDenywriteTests` および `UserCommonSandboxEndToEndTests` の denyWrite 関連ケース群。

---

## 10.2. Phase 3 sandbox case E 実機検証 (WSL Layer 3 suppression, runtime 0.1.4+)

**目的**: runtime 0.1.4 で導入された WSL Layer 3 suppression が期待通り発火することを確認する。具体的には worker_dir の realpath が sandbox read roots を escape する場合（典型: WSL の `/home/user/...` が `/mnt/c/...` へ resolve）、`sandbox.filesystem.denyRead` / `denyWrite` の該当 entry が rendered `settings.local.json` から自動的に dropped されること。

**前提**: `claude-org-runtime>=0.1.4` が install されていること（`pyproject.toml` / `requirements.txt` 経由）。

**手順**:

1. WSL 環境で fresh worker dir を用意し、以下を実行:
   ```bash
   claude-org-runtime settings show \
       --explain --json \
       --role default \
       --worker-dir <worker_dir> \
       --claude-org-path <ja root>
   ```
2. 出力 JSON の `wsl_detected` が `true`、`sandbox_read_roots` が `<worker_dir>` + `additionalDirectories` を含むことを確認。
3. `suppressions` 配列に少なくとも 1 件、以下のフィールドを持つエントリが含まれることを確認:
   - `layer == 'sandbox.filesystem.denyRead'` または `'sandbox.filesystem.denyWrite'`
   - `reason == 'realpath escapes sandbox read roots'`
   - `realpath`, `sandbox_read_roots`

   WSL では typically `~/.aws/*` や `~/.ssh/*` 系の Layer 3 entries が realpath escape で suppressed される。
4. rendered `settings.local.json` を別途 generate し、`suppressions` に含まれた entries が `sandbox.filesystem.denyRead` / `denyWrite` から消えていることを確認。なお Issue #429 Task C 後の本リポジトリ共有 `.claude/settings.json` には `Read(~/.aws/*)` / `Read(~/.ssh/*)` が **そもそも存在しない**（§10.1 Addendum 参照、Phase 2a の Layer 2 mirror 前提を撤回）。Layer 2 fallback は worker_role のテンプレート (`tools/org_extension_schema.json` の `worker_roles.*`) や個人 `~/.claude/settings.json` でのみ評価する。runtime 側 contract「Layer 2 は never suppressed」自体は健在で、worker_role が emit する `permissions.deny` は §1.3 case E と独立して残る。
5. 非 WSL Linux 環境（GitHub Actions `ubuntu-latest` 等）で同様に `settings show --explain` を実行し、`wsl_detected=false` / `suppressions=[]` / Layer 3 entries が rendered settings に残ることを確認。

**期待結果と判断**:
- WSL では Layer 3 が adaptive に dropped、worker_role の Layer 2 mirror（emit 元側）は常に保持。
- 非 WSL では Layer 3 はそのまま残る。
- worker_role 側の `permissions.deny` は intact（共有 `.claude/settings.json` 側は §10.1 Addendum 後 `Read(~/.aws/*)` 等を持たない点に注意）。

**失敗時の切り分け**:
- (a) `wsl_detected` が `false` なのに `/mnt/c` が realpath に出る → `/proc/version` / `osrelease` 検出ロジックの不具合（runtime issue）。
- (b) `suppressions` が空でも rendered settings から entries が消えている → `render_role` と `show` のソースが乖離。
- (c) worker_role が emit するはずの Layer 2 entries が消えている → runtime regression、即座に runtime 側に escalation（共有 `.claude/settings.json` 側からの除去は Issue #429 Task C で意図的なもの。両者を取り違えない）。

**関連 §Phase 2a portability fix (§10.1) との関係**: §10.1 Addendum (Issue #429 Task A 調査結論) で「`permissions.deny Read(...)` は Claude Code 側で `sandbox.filesystem.denyRead` に merge される（共有 / 個人 settings 問わず）」事実が確認された結果、Phase 2a の「permissions.deny への移管が portability fix になる」前提は撤回された。共有 `.claude/settings.json` からは `Read(~/.ssh/*)` / `Read(~/.aws/*)` を除去し、個人環境ごとの directory-level deny は `python tools/org_setup_prune.py --user-common-sandbox`（symlink-escape 自動 skip）で個人 `~/.claude/settings.json` 側に補強する方針に切り替わっている。Phase 3 case E（runtime 側の WSL Layer 3 suppression）は worker_role の generator 動作として依然有効で、本 §10.2 は WSL 環境向けの runtime suppression 検証として独立して残す。

**Reference**:
- runtime 0.1.4 release notes: https://github.com/suisya-systems/claude-org-runtime/releases/tag/v0.1.4
- claude-org-runtime#10 (Phase 3 case E MVP)

---

## 11. MCP 疎通テスト（環境確認）

**目的**: `renga-peers` MCP サーバが Claude Code に接続済みで、14 ツール全てが tool surface として登録されていることを確認し、副作用なしで呼び出せるツールについてはサンプル呼び出しで応答を検証する。副作用の大きいツール（`send_keys` / `spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` / `new_tab` / `set_pane_identity`）の実動作確認は Test 1-10 の E2E フローでカバーされるため、本テストでは登録確認のみに留める。

**手順**:

### 11-a. 登録確認（14 ツール）
1. `claude mcp list` で `renga-peers` が Connected を表示することを確認
2. `renga --version` で 0.18.0 以上であることを確認
3. 以下 14 ツールが Claude Code の tool surface に出現するか確認（MCP サーバが tools/list で返すツール名と一致する）:
   - 副作用なし / 軽 side-effect: `list_panes` / `list_peers` / `set_summary` / `check_messages` / `send_message` / `poll_events` / `inspect_pane`
   - 副作用大（ペイン / PTY 操作）: `spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` / `new_tab` / `send_keys` / `set_pane_identity`

### 11-b. 副作用なしツールの応答確認（7 ツール）
以下の 7 ツールを順次呼び出し、エラーなく応答が返るか確認:

| ツール | 呼び出し例 | 期待応答 |
|---|---|---|
| `list_panes` | 引数なし | 現在のペイン一覧テキスト |
| `list_peers` | 引数なし | 同タブ内 peer 一覧 or `(no peers — …)` |
| `set_summary` | `summary="test"` | `Summary accepted (v1 stub: …)` |
| `check_messages` | 引数なし | `No queued messages.` |
| `send_message` | `to_id=<self の pane id or name>, message="ping"` | `Delivered to <target>.` or `(message dropped — …)` |
| `poll_events` | `timeout_ms=0`（非ブロッキング drain） | `{next_since, events}` の JSON |
| `inspect_pane` | `target="focused", lines=5, format="text"` | 画面末尾 5 行 + `structuredContent` |

### 11-c. 副作用大ツールは E2E テストに委譲
`spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` / `new_tab` / `set_pane_identity` は Test 1 / 2 / 3 / 4 の中で実動作確認される。`send_keys` は Test 1（開発チャネル確認 Enter 注入）で確認される。

**期待結果**:
- 11-a: `claude mcp list` の出力に `renga-peers: … ✓ Connected` があり、14 ツールすべてが Claude Code の tool list に登録されている
- 11-b: 7 ツールがすべてエラーなく応答、エラー時は `[<code>] <msg>` 形式のテキストが得られる（例: `list_panes` が renga 未起動なら `[shutting_down]` 等）
- 11-c: 副作用大ツールは本テストでは実行せず、E2E テストでのカバレッジに委ねる

**失敗パターンと対処**:
- `claude mcp list` に `renga-peers` が出ない → `renga mcp install --force` 再実行
- `list_panes` が error → `renga --version` で 0.14.0 以上か確認、古ければ `npm install -g @suisya-systems/renga@0.14.0`
- `poll_events` が JSON を返さない → `mcp_peer/mod.rs` の実装に不整合、renga バージョン確認

---

## 11.1. attention watcher 検証（scan --dry-run）

**目的**: `claude-org-runtime attention scan` が `.state/state.db` と `.state/pending_decisions.json` から attention event を抽出し、ja default の日本語テンプレート（[`tools/templates/attention.example.json`](../tools/templates/attention.example.json)）が runtime config として読み込まれることを確認する。`watch` は常駐コマンドで CI でも回しにくいため、verification は `scan --dry-run --json` で 1 回限りの抽出結果を JSON で取り、shape と severity 分類を見るのを正攻法とする。詳細は [`docs/operations/attention-watch.md`](operations/attention-watch.md) を参照。

**前提**:
- `claude-org-runtime` が install 済み（`pip install -e .` で `claude-org-runtime` CLI が `PATH` に乗っていること）
- 本リポジトリ直下で `/org-start` 済み、もしくは `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で `.state/state.db` が初期化済み

**手順**:

1. ja default config を `.state/` に配置する（`.state/` は gitignored なので tracked example をコピーして使う、fresh clone 直後は未作成のため `mkdir -p .state` を併発）:
   ```bash
   mkdir -p .state
   cp tools/templates/attention.example.json .state/attention.json
   ```
2. dry-run scan を JSON 出力で実行する:
   ```bash
   claude-org-runtime attention scan --state-dir .state --config .state/attention.json --dry-run --json
   ```
3. 出力 JSON を確認する。`events` 配列の各要素が以下のフィールドを持つこと:
   - `key`: dedup 用安定 ID（`event:<events.id>` または `pending:<task_id>:<kind>` のいずれか）
   - `kind`: runtime 0.1.x の分類 kind の集合（`approval_blocked` / `relay_gap_suspected` / `silent_worker_output` / `ci_failed` / `pending_decision` / `user_reply_not_forwarded` / `pane_silent` / `pane_crashed` / `worker_stalled` / `worker_not_reported` / `worker_error` / `worker_completed` / `pr_merged` / `secretary_awaiting_user`）
   - `severity`: `urgent` または `normal`
   - `title` / `body`: ja config のテンプレートが適用された文字列（日本語）
   - 必要に応じて `task_id` / `worker` / `created_at`

**期待結果**:

- exit code 0 で JSON が返る。`.state/state.db` に該当する `notify_sent kind=approval_blocked` event があれば `kind: "approval_blocked"`, `severity: "urgent"` で出る
- `ci_completed` で `status` が `failed` / `canceled` / `incomplete` のいずれかなら `kind: "ci_failed"`, `severity: "urgent"`
- `worker_completed` / `pr_merged` は `severity: "normal"`、それ以外の上記分類は ja default で `urgent`
- pending decision が `pending_decision_min`（既定 15 分）を超えていれば `kind: "pending_decision"`, `severity: "urgent"`
- `notify_sent kind=awaiting_user`（Secretary が user の判断待ちで停止する 4 ゲート）は `kind: "secretary_awaiting_user"`, `severity: "urgent"`
- title / body が ja default の日本語文字列で、`{worker}` / `{task_id}` / `{pr}` / `{status}` 等の placeholder が解決済み
- `--dry-run` 指定なので desktop notification subprocess が呼ばれない（macOS で notification center に何も出ない、Linux で `notify-send` が走らない）
- `--config .state/attention.json` を外して再実行すると runtime の中立的な英語 default が title / body に出ること（ja 上書きが effective であることの裏取り）

**失敗パターンと対処**:

- `command not found: claude-org-runtime` → `pip install -e .` 未実行。プロジェクト直下で `pip install -e .` を実行（`pyproject.toml` の dependency 経由で runtime が入る）
- `.state/state.db: no such file` → `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で初期化
- `events` 配列が空 → `.state/state.db` に分類対象 event が無い（クリーンな初期化直後など正常なケース）。手動で `tools/journal_append.sh notify_sent kind=approval_blocked task=test-1 worker=worker-test-1` 相当を投入して再 scan
- title / body の placeholder が `{worker}` のまま残る → `tools/templates/attention.example.json` の template に runtime 未対応の placeholder を足してしまった可能性。allowlist は `{task_id} {worker} {kind} {status} {pr} {summary}` の 6 種（[`docs/design/attention-notification.md`](design/attention-notification.md) §6）
- title / body が英語のまま → `.state/attention.json` が読まれていない。`--config` の path を絶対 path で渡し直すか、`.state/attention.json` の存在を確認
- `--json` 無しの human-readable 出力で「fallback to terminal bell」と出る → backend が落ちている。`--dry-run` 時は notification subprocess 自体を呼ばない仕様なので、本検証では fallback log は出ない想定。出る場合は runtime のバージョンを確認

**注**: `watch` の常駐動作確認は本リポジトリの自動 verification にはまだ含まれない。`tests/fixtures/attention/*` と統合テストは別 Issue（#445）で追加される。本節は `scan --dry-run` で ja templates が runtime に解決される導線が壊れていないかを最低限担保する。

---

<a id="security-matrix"></a>

## 12. 攻撃ベクトル × 防御層マトリクス

本リポジトリ自身の `.claude/settings.json`（窓口・キュレーター用、`auto` モード）と `.githooks/pre-commit` を基準にした、主要な攻撃ベクトルと各層の対応表。ワーカーロール用テンプレート（[`tools/org_extension_schema.json`](../tools/org_extension_schema.json) の `worker_roles.{default,claude-org-self-edit}` が SoT。`.claude/skills/org-setup/references/permissions.md` は同 SoT の参照ドキュメント）にも `check-worker-boundary.sh` / `block-org-structure.sh` / `block-git-push.sh` に加えて `block-no-verify.sh` / `block-dangerous-git.sh` が配備済み。`permissions.deny` も `git push` 系と `rm -r` / `rm -rf` に加えて `git fetch` / `git pull` / `git remote add|set-url|remove` / `git submodule` / `git lfs` / `git gc` / `git filter-branch` / `git filter-repo` / `git replace` / `git update-ref` / `git config --global|--local|--worktree` / `git reflog expire|delete` / `git worktree*` を `-C` バリアント込みで拒否する。`--no-verify` / `git reset --hard` / `git branch -D` 系の直接遮断は窓口・キュレーターに加えてワーカー側でも有効（ディスパッチャーは `.dispatcher/` 用の独立した hook 群で別途管理）。

凡例: ✅ ブロック / ⚠️ 部分・条件付き / — 対象外 / ➖ 未配備。

| 攻撃ベクトル | `permissions.deny` | PreToolUse フック | sandbox | pre-commit |
|---|---|---|---|---|
| `git commit --no-verify` 直書き（窓口・キュレーター） | ✅ | ✅ (`block-no-verify.sh`) | — | — |
| `eval "git commit --no-verify"` / `bash -c "..."` | — | ✅ Phase 2a [#79](https://github.com/suisya-systems/claude-org-ja/issues/79): `unwrap_eval_and_bashc` で明示パース | — | — |
| `VAR=$(printf -- '--no-verify'); git commit $VAR` | — | ✅ assignment 収集 + `flatten_substitutions` | — | — |
| `git push --force` / `git reset --hard` / `git branch -D`（窓口・キュレーター） | ✅ | ✅ (`block-dangerous-git.sh`) | — | — |
| `cat .env` / 認証情報読み取り（Bash 経由） | — | — | ⚠️ macOS (Seatbelt) / Linux / WSL2 (`bubblewrap`+`socat`) のみ。**Windows native は Claude Code 側未実装で素通り**（[§10.1](#101-sandboxdenyread--denywrite-実機検証phase-2a-issue-79)） | — |
| `cat ~/.ssh/<key>` / `cat ~/.aws/credentials`（Bash subprocess 経由のホーム dotfile 読み取り） | — (Issue #429 Task C で共有 settings から除去) | — | ⚠️ 個人 `~/.claude/settings.json` で `python tools/org_setup_prune.py --user-common-sandbox` を 1 回実行すると `sandbox.filesystem.denyRead` に directory-level deny がマージされる（symlink-escape は自動 skip。Issue #429 Task B）。**Bash subprocess (sandboxed commands) のみ防御**: `sandbox.filesystem.denyRead` は sandbox 経由 syscall を止めるが Read tool 自体は止めない | — |
| `echo x >> ~/.claude/settings.json`（Bash subprocess 経由の個人 Claude 設定上書き） | — (Issue #433 で共有 settings の `denyWrite` から除去・個人 settings 側へ移管) | — | ⚠️ 個人 `~/.claude/settings.json` で `python tools/org_setup_prune.py --user-common-sandbox` を 1 回実行すると `sandbox.filesystem.denyWrite` に `~/.claude/settings.json` がマージされる（preventive deny、ファイル未作成でも適用。Issue #433）。**Bash subprocess (sandboxed commands) のみ防御**: Read tool 経由の `Edit(...)` は別 layer (`permissions.deny`) で抑止する | — |
| `Read tool` 経由のホーム dotfile 読み取り（`Read(~/.ssh/<key>)` 等） | — (Issue #429 Task C で共有 settings から除去) | — | — (`sandbox.filesystem.denyRead` は Read tool に逆方向 merge されない。Claude Code 公式 docs は `Read(...)` deny → `denyRead` の一方向 merge のみ規定) | — |
| ステージ差分への秘密情報混入 | — | — | — | ✅ ([.githooks/pre-commit](../.githooks/pre-commit)) |
| シェル関数経由の bypass（`f(){ git commit --no-verify; }; f`） | — | ➖ 関数定義の静的解析は非対応 | — | — |

### 残存リスク (residual risk)

- **シェル関数定義経由のルーティング**: 関数本体内に隠された禁止コマンドは PreToolUse フックの静的解析では検出できない（Phase 2c で検討した shell-layer 静的解析は誤検知率と保守コストの観点から廃案）。sandbox の `denyWrite` も `git commit` などのリポジトリ副作用は止めない。ホーム dotfile の defense-in-depth は [Issue #429](https://github.com/suisya-systems/claude-org-ja/issues/429) Task B/C（`denyRead` 系候補）および [Issue #433](https://github.com/suisya-systems/claude-org-ja/issues/433)（`denyWrite` の `~/.claude/settings.json`）で **共有 `.claude/settings.json` から個人 `~/.claude/settings.json` 側へ移管** されている。`python tools/org_setup_prune.py --user-common-sandbox` を 1 回実行することで、個人環境ごとに directory-level の `sandbox.filesystem.denyRead`（symlink-escape は自動 skip）と file-level の `sandbox.filesystem.denyWrite`（preventive、ファイル未作成でも適用）が同時にマージされる。**この補強は sandboxed Bash サブプロセス経由の read / write のみを止め、Read tool 経由の `Read(~/.aws/<file>)` や Edit tool 経由の `Edit(~/.claude/settings.json)` は止めない**（Claude Code 公式 docs の merge は `Read(...)` deny → `denyRead` / `Edit(...)` deny → `denyWrite` の一方向のみ）。Read / Edit tool 経由は Claude Code 組込の credential 保護層と、worker_role schema (`tools/org_extension_schema.json` の `worker_roles.*`) が emit する `permissions.deny` で受ける残存リスクとして整理されている。WSL2 + DriveFS で `~/.aws` が `/mnt/c/...` への symlink になっている環境では denyRead 側が自動 skip され、bwrap bootstrap 失敗を構造的に避ける（denyWrite は単一ファイル literal のため symlink-escape の影響を受けない）。なお WSL 環境では `claude-org-runtime` が、realpath がサンドボックスの可視範囲から外れる Layer 3 `denyRead` / `denyWrite` エントリを出力時に抑止し、抑止対象を `$comment` フィールドに列挙して出力する。本ベクトルは現状ロール契約による自主規律と上記 user-level sandbox deny の併用で担保される。
- **Windows native の sandbox 不在**: 上記表のとおり `cat .env` 等は Windows native では素通りする。ワーカー実行環境としては macOS / Linux / WSL2 を推奨し、Windows native では別経路（OS 側のファイル権限・GitHub Secret Scanning 等）で補完する。

詳細と段階導入の意思決定は [Issue #79](https://github.com/suisya-systems/claude-org-ja/issues/79) と [§10.1〜§10.2](#101-sandboxdenyread--denywrite-実機検証phase-2a-issue-79) を参照。

---

## テスト結果の記録

各テストの結果は以下のフォーマットで記録する:

```markdown
## テスト{N}: {テスト名}
- 日時: YYYY-MM-DD HH:MM
- 結果: PASS / FAIL / PARTIAL
- 問題点: {あれば記述}
- 対処: {修正内容}
- 再テスト: 要 / 不要
```

`docs/test-results/` ディレクトリに保存する。
