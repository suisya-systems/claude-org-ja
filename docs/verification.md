# 検証方法

各機能の動作確認手順。問題が見つかったらスキルやCLAUDE.mdを修正し、再テストする。

**前提**: ccmux 0.18.0+ （`npm install -g ccmux-fork@0.18.0` 後、`ccmux mcp install --force` で `ccmux-peers` MCP サーバを user-scope 登録済み）。structured `cwd` (0.16.0) / `set_pane_identity` (0.17.0) / `spawn_claude_pane` (0.18.0) すべてを前提とする。

---

## 0. リグレッションチェック（起動テンプレートの退行防止）

**目的**: Issue #58 で撤去した `cd X && claude ...` auto-upgrade 迂回パターンがテンプレート / スキル / docs に再導入されていないか検出する。

**手順**:
```bash
# (1) spawn_pane / spawn_claude_pane の command 引数に `cd X && claude` を合成している箇所
#     (禁止: ccmux の bare-claude auto-upgrade が発動せず channel push が届かない)
grep -rEn 'command="cd [^"]*&&[[:space:]]*claude' --include="*.md" --include="*.toml" . \
  && { echo "FAIL: cd&&claude 合成が残っている"; exit 1; } \
  || echo "OK: no cd&&claude synthesis"

# (2) ops.toml / layout TOML の command 行に `claude --dangerously-load-development-channels` を手書き
#     (ccmux 0.16.0+ の bare-claude auto-upgrade / spawn_claude_pane の自動付与で不要)
grep -En '^[[:space:]]*command[[:space:]]*=.*dangerously-load-development-channels' ccmux-layouts/*.toml \
  && { echo "FAIL: layout で dangerously フラグ手書き"; exit 1; } \
  || echo "OK: no explicit flag in layout TOML"
```

**期待結果**:
- (1) は 0 件（prose 内での解説的な言及は引用符／記法が異なるためヒットしない）
- (2) は `ccmux-layouts/*.toml` で 0 件

**失敗時の対処**:
- ヒットした場合は `spawn_claude_pane` の構造化フィールド (`cwd` / `permission_mode` / `model`) に書き換える。詳細は `.claude/skills/org-start/SKILL.md`「ClaudeCode 起動コマンド（役割別）」セクションと `.claude/skills/org-delegate/references/pane-layout.md` を参照

## 0. 互換性プリフライト

**目的**: `/org-start` 実行前に ccmux バージョンと MCP ツール surface が aainc-ops の要件を満たすか確認する（Issue #61）。

**手順**:
```bash
py -3 tools/check_ccmux_compat.py            # Windows
python3 tools/check_ccmux_compat.py          # macOS / Linux
py -3 tools/check_ccmux_compat.py --json     # 機械可読出力
```

**期待結果**: `Result: OK` で終了コード 0。ccmux バージョン・`ccmux-peers` MCP 登録・必須 14 ツールすべてが揃っていれば合格。

**失敗パターン**:
- ccmux バージョン不足 → `npm update -g ccmux-fork`
- MCP 未登録 → `ccmux mcp install`
- ツール欠如 → `ccmux mcp install --force` で stale 登録を更新

このスクリプトは live ccmux セッションを必要としない（`ccmux mcp-peer` stdio 経由で tools/list を取得する静的 probe）。

---

## 1. 基本起動テスト

**目的**: clone先でClaudeCodeを起動し、CLAUDE.mdとスキルが正しく読み込まれるか確認。

**手順**:
1. 任意の場所に本リポジトリを `git clone`
2. clone先で `ccmux --layout ops` を実行 (窓口ペインが立ち上がる)
3. 窓口の Claude Code で `mcp__ccmux-peers__list_panes` が疎通するか確認（Step 0 の MCP 有効性 chk）
4. 窓口の Claude Code で `/org-start` を実行

**期待結果**:
- `.state/org-state.md` が存在しないので、初回起動と判断される
- `mcp__ccmux-peers__spawn_pane` でフォアマンペインが窓口の下に開き、その右にキュレーターペインが開く
- Foreman / Curator 起動直後の「開発チャネル確認プロンプト」を `mcp__ccmux-peers__send_keys(target=<pane>, enter=true)` で Enter 注入して通過している（`org-start` SKILL Step 2 / Step 3 の手順）
- キュレーターに `mcp__ccmux-peers__send_message` 経由で `/loop 30m /org-curate` の実行が指示される
- 「初回起動です。何をしましょうか？」と報告される

**失敗パターンと対処**:
- CLAUDE.mdが読み込まれない → `.claude/` ディレクトリ配置を確認
- スキルが認識されない → `.claude/skills/*/SKILL.md` のfrontmatter形式を確認
- `/org-start` が発動しない → スキル名の競合やdescriptionを確認
- `mcp__ccmux-peers__list_panes` で error → `ccmux mcp install --force` を再実行、`claude mcp list` で登録確認
- `send_keys(enter=true)` が Enter 注入に失敗 → Foreman / Curator ペインが「Load development channel?」プロンプトで止まっているか確認、手動で Enter

---

## 2. org-delegate テスト（ワーカー派遣）

**目的**: ワーカーが正しく派遣され、作業を完了し、結果が報告されるか確認。

**前提**: `ccmux --layout ops` で起動していること、`ccmux-peers` MCP が有効なこと（`claude mcp list` で Connected 確認）。テスト1で `/org-start` 済み。

**手順**:
1. 窓口Claudeにタスクを依頼する（例:「ブログに新しい記事を追加して」）
2. 新規プロジェクトの場合、通称・パス・説明の確認が入るので回答する
3. 窓口が `/org-delegate` でワーカーを派遣するのを確認

**期待結果**:
- プロジェクトが `registry/projects.md` に自動登録される
- 窓口がフォアマンに DELEGATE メッセージを送信し、すぐにユーザーとの対話に戻る
- フォアマンが `mcp__ccmux-peers__spawn_pane` で同一タブ内にワーカーペインを派生する（`name="worker-{task_id}"`、balanced split 戦略は `pane-layout.md` に従う）
- フォアマンが `mcp__ccmux-peers__poll_events(types=["pane_started"])` で起動完了を確認
- ワーカー起動直後の「開発チャネル確認プロンプト」を `mcp__ccmux-peers__send_keys(target="worker-{task_id}", enter=true)` で Enter 注入して通過（`org-delegate` SKILL Step 3-2）
- **Plan モード要の Worker 派遣時** (DELEGATE に「Plan承認後モード切替: 要」含む場合): Worker が Plan 作成 → APPROVAL_BLOCKED 通知 → 窓口側で **Plan 承認前に** `mcp__ccmux-peers__send_keys(target="worker-{task_id}", keys=["Shift+Tab"])` でモード切替 → `mcp__ccmux-peers__inspect_pane(lines=5, format="grid")` でステータスバーに「auto mode on」表示を確認 → `mcp__ccmux-peers__send_keys(target="worker-{task_id}", text="yes", enter=true)` で Plan 承認（`org-delegate` SKILL Step 3-7 / Step 5）
- フォアマンが `mcp__ccmux-peers__send_message` 経由でワーカーに作業指示を送信する
- フォアマンが `.state/workers/worker-{id}.md` を作成する
- `.state/org-state.md` が作成/更新される
- `.state/journal.jsonl` にイベントが記録される
- ワーカー完了後、`ccmux-peers` 経由で**窓口に**報告が届く（フォアマンではなく窓口）
- 窓口が結果を業務言語で人間に伝える（技術用語を避ける）
- 窓口がフォアマンにペインクローズを依頼する（フォアマンは `mcp__ccmux-peers__close_pane(target="worker-{task_id}")` で破棄）

**確認コマンド**:
```bash
cat .state/org-state.md
cat .state/journal.jsonl
ls .state/workers/
cat registry/projects.md
```

ペイン状態の確認は MCP ツールで:
```
mcp__ccmux-peers__list_panes    # 現在のペイン一覧
```

**失敗パターンと対処**:
- ペインが開かない → `mcp__ccmux-peers__list_panes` で現在のペイン状態を確認、tool result の `[split_refused]` / `[pane_not_found]` を `references/ccmux-error-codes.md` で分岐
- ccmux-peers で通信できない → `claude mcp list` で `ccmux-peers` が Connected か確認、`list_peers` で peer ID（`worker-{task_id}` / `foreman` / `curator` / `secretary`）を確認
- 状態ファイルが作成されない → org-delegateスキルの手順を見直し
- ワーカーが指示を理解しない → instruction-template.md の記述を改善
- プロジェクト名前解決が動かない → org-delegate Step 0 を見直し

### 2.1 balanced split スケール検証（4 並列 / 8 並列）

**目的**: org-delegate Step 3 の rect ベース balanced split が、4 並列・8 並列いずれも `[split_refused]` を発生させずに期待通りの tree を生成することを実機確認する。

**前提**: テスト 2 が通っていること。ターミナル幅 `W ≥ 160 cols`（`tput cols` で確認）。`pane-layout.md` の「ワーカーの balanced split 戦略」セクションを手元で開いておく。

**手順**:
1. `tput cols` を実行し W を記録。160 未満なら検証不能としてスキップ or ターミナルを広げる。
2. 窓口に互いに独立な 8 タスク（ダミーで良い。例: `echo-1` 〜 `echo-8` のような軽量タスク）を順次依頼。k=1〜8 それぞれが以下を満たすことを確認:
   - a. フォアマンの `mcp__ccmux-peers__spawn_pane` 呼び出し結果テキストに `[split_refused]` が含まれない
   - b. Step 3-1b のアルゴリズムが選出した `target` / `direction` が、`list_panes` の直前スナップショットから rect ベースで再現可能
   - c. 起動直後の `mcp__ccmux-peers__list_panes` を **別ログファイル (例: `.state/verification/balanced-split-{timestamp}.log`)** に保存するか、その場で `role == "worker"` の `name` / `id` を記録し、`.state/journal.jsonl` の `worker_spawned` イベントと事後照合する
3. 各 k 到達時点でペイン配置を `list_panes` で取得し、Step 3-1b のアルゴリズム（curator 特定 → role filter → foreman-curator 隣接判定 → direction 判定 → `new_w / new_h` 計算 → MIN_PANE 制約 → SECRETARY 保険条項 → metric sort）をスナップショットに対して手計算で再現できることを確認する。`pane-layout.md` の「ワーカーの balanced split 戦略」で述べている通り、rect ベース動的配置なので 2×2 や 2×4 のような固定グリッド形状は成功基準にしない。
4. 9 人目のダミータスクを試し、フォアマンが `ccmux-peers` で窓口に `SPLIT_CAPACITY_EXCEEDED` を送信することを確認。**当該 9 人目のワーカーのみ派遣を中止し、フォアマン本体の監視ループは継続稼働**すること（`spawn_pane` は発行されず、`exit` などでフォアマンが落ちない）。

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
mcp__ccmux-peers__list_panes             # 各 k 到達時のスナップショット
```

**失敗パターンと対処**:
- k=4 で `[split_refused]` → `tput cols` の値を確認。W < 160 なら balanced split の要件未満。ターミナル拡大で再試行
- k=3 で既に `[split_refused]` → foreman 直下に file-tree / preview が居座っていないか確認（これらが表示中だと `W_f` が 20〜40 cols 目減りする）
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
- `mcp__ccmux-peers__send_message` 経由でワーカーに SUSPEND メッセージが送信される
- ワーカーが `ccmux-peers` 経由で状態を報告する。未応答ワーカーは `mcp__ccmux-peers__inspect_pane(target="worker-{task_id}", format="text")` で画面内容を読み、git 状態と組み合わせて推定する
- `.state/org-state.md` の Status が `SUSPENDED` になる
- `.state/org-state.prev.md` にバックアップが作成される
- `mcp__ccmux-peers__send_message` で全ピアに SHUTDOWN が送信される
- `mcp__ccmux-peers__poll_events(types=["pane_exited"], timeout_ms=10000)` で pane_exited を待機、`role == "worker"` をまとめて消化
- 残留ワーカーは `mcp__ccmux-peers__close_pane(target="worker-{task_id}")` でフォールバッククローズ
- 全ワーカーペインが先に閉じられ、次にフォアマン、最後にキュレーターが閉じられる
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
2. clone先で再度 `ccmux --layout ops` で起動する
3. `/org-start` を実行する

**期待結果**:
- `/org-start` が `.state/org-state.md` を検出し、Status: SUSPENDED を確認
- `/org-resume` の手順に従い、前回の状態サマリーが表示される
- 各作業ディレクトリのgit状態との照合結果が報告される
- 再開計画が提案される
- 人間の承認を待つ（勝手にワーカーを派遣しない）
- フォアマンとキュレーターペインが `mcp__ccmux-peers__spawn_pane` 経由で再起動される

**確認ポイント**:
- ブリーフィング内容が `.state/org-state.md` と一致するか
- git状態の照合が正確か
- フォアマンとキュレーターペインが起動しているか（`mcp__ccmux-peers__list_panes` で確認）

**失敗パターンと対処**:
- `/org-start` が状態を読まない → org-start スキルの Step 1 を見直し
- 状態が不正確 → org-state.md のフォーマットまたはorg-suspendの書き込みを見直し
- キュレーターが起動しない → org-start Step 3 の `send_message` / `spawn_pane` を確認

---

## 5. 突然の終了テスト（クラッシュリカバリ）

**目的**: org-suspendを実行せずに端末を閉じた場合、どこまで復元できるか確認。

**手順**:
1. テスト2の状態（ワーカー稼働中）で、**suspendせずに**端末を閉じる
2. 再度 `ccmux --layout ops` で起動する
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
- Foreman の `poll_events` cursor (`.state/foreman-event-cursor.txt`) 消失時は過去 5 秒分のイベントを取りこぼす可能性があるが、`list_panes` 突き合わせで回復可能

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
2. 手動で `/org-curate` を実行（またはキュレーターの /loop を待つ）

**期待結果**:
- rawファイルがテーマ別に分類される
- `knowledge/curated/{theme}.md` にテーマ別ファイルが作成される
- 処理済みrawファイルの先頭に `<!-- curated -->` が追記される
- 改善提案がある場合、`ccmux-peers` で窓口に通知される

**確認コマンド**:
```bash
ls knowledge/curated/
cat knowledge/curated/*.md
head -1 knowledge/raw/*.md  # <!-- curated --> マーカー確認
```

**自己成長の確認**:
- 改善提案の内容が具体的か
- 提案が人間の承認なしに実行されていないか

---

## 8. フォアマン・キュレーターペインテスト

**目的**: フォアマンとキュレーターが専用ペインで正しく起動し機能するか確認。

**手順**:
1. `/org-start` を実行し、フォアマンとキュレーターのペインが起動されるか確認
2. フォアマンが `mcp__ccmux-peers__send_message` 経由で役割指示を受け取っているか確認
3. キュレーターが `mcp__ccmux-peers__send_message` 経由で `/loop 30m /org-curate` を実行しているか確認
4. `knowledge/raw/` に閾値未満のファイルを置き、キュレーターがスキップするか確認
5. 閾値以上に増やし、次の /loop サイクルで実行されるか確認

**期待結果**:
- `/org-start` 実行後に窓口の下にフォアマンとキュレーターが横並びで開く（`mcp__ccmux-peers__list_panes` で確認）
- フォアマンが DELEGATE メッセージを待ち受ける状態になる
- キュレーターが `/loop` を開始する
- 30分ごとに org-curate が発動する
- 閾値未満ではスキップ、閾値以上で実行

**失敗パターンと対処**:
- ペインは開くが指示を受け取らない → `ccmux-peers` のピア検出タイミングを調整（`list_peers` のリトライ、pane_started イベント待ちの延長）
- フォアマンが DELEGATE に反応しない → フォアマンへの初期メッセージの内容を見直し
- /loop が実行されない → キュレーターへのメッセージ内容を見直し

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
1. clone先でClaudeCodeを起動（`ccmux --layout ops`）
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

## 11. MCP 疎通テスト（環境確認）

**目的**: `ccmux-peers` MCP サーバが Claude Code に接続済みで、14 ツール全てが tool surface として登録されていることを確認し、副作用なしで呼び出せるツールについてはサンプル呼び出しで応答を検証する。副作用の大きいツール（`send_keys` / `spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` / `new_tab` / `set_pane_identity`）の実動作確認は Test 1-10 の E2E フローでカバーされるため、本テストでは登録確認のみに留める。

**手順**:

### 11-a. 登録確認（14 ツール）
1. `claude mcp list` で `ccmux-peers` が Connected を表示することを確認
2. `ccmux --version` で 0.18.0 以上であることを確認
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
`spawn_pane` / `close_pane` / `focus_pane` / `new_tab` は Test 1 / 2 / 3 / 4 の中で実動作確認される。`send_keys` は Test 1（開発チャネル確認 Enter 注入）と Test 2（Plan モード切替時の Shift+Tab / yes 送信）で確認される。

**期待結果**:
- 11-a: `claude mcp list` の出力に `ccmux-peers: … ✓ Connected` があり、12 ツールすべてが Claude Code の tool list に登録されている
- 11-b: 7 ツールがすべてエラーなく応答、エラー時は `[<code>] <msg>` 形式のテキストが得られる（例: `list_panes` が ccmux 未起動なら `[shutting_down]` 等）
- 11-c: 副作用大ツールは本テストでは実行せず、E2E テストでのカバレッジに委ねる

**失敗パターンと対処**:
- `claude mcp list` に `ccmux-peers` が出ない → `ccmux mcp install --force` 再実行
- `list_panes` が error → `ccmux --version` で 0.14.0 以上か確認、古ければ `npm install -g ccmux-fork@0.14.0`
- `poll_events` が JSON を返さない → `mcp_peer/mod.rs` の実装に不整合、ccmux バージョン確認

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
