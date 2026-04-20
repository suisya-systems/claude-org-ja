# 検証方法

各機能の動作確認手順。問題が見つかったらスキルやCLAUDE.mdを修正し、再テストする。

---

## 1. 基本起動テスト

**目的**: clone先でClaudeCodeを起動し、CLAUDE.mdとスキルが正しく読み込まれるか確認。

**手順**:
1. 任意の場所に本リポジトリを `git clone`
2. clone先で `ccmux --layout ops` を実行 (窓口ペインが立ち上がる)
3. 窓口の Claude Code で `/org-start` を実行

**期待結果**:
- `.state/org-state.md` が存在しないので、初回起動と判断される
- フォアマンペインが窓口の下に開き、その右にキュレーターペインが開く
- キュレーターにclaude-peers経由で `/loop 30m /org-curate` の実行が指示される
- 「初回起動です。何をしましょうか？」と報告される

**失敗パターンと対処**:
- CLAUDE.mdが読み込まれない → `.claude/` ディレクトリ配置を確認
- スキルが認識されない → `.claude/skills/*/SKILL.md` のfrontmatter形式を確認
- `/org-start` が発動しない → スキル名の競合やdescriptionを確認

---

## 2. org-delegate テスト（ワーカー派遣）

**目的**: ワーカーが正しく派遣され、作業を完了し、結果が報告されるか確認。

**前提**: `ccmux --layout ops` で起動していること、claude-peers MCPが有効なこと。テスト1で `/org-start` 済み。

**手順**:
1. 窓口Claudeにタスクを依頼する（例:「ブログに新しい記事を追加して」）
2. 新規プロジェクトの場合、通称・パス・説明の確認が入るので回答する
3. 窓口が `/org-delegate` でワーカーを派遣するのを確認

**期待結果**:
- プロジェクトが `registry/projects.md` に自動登録される
- 窓口がフォアマンに DELEGATE メッセージを送信し、すぐにユーザーとの対話に戻る
- フォアマンが `ccmux split` で同一タブ内にワーカーペインを派生する（`worker-{task_id}` 名、balanced split 戦略は `pane-layout.md` に従う）
- フォアマンがclaude-peers経由でワーカーに作業指示を送信する
- フォアマンが `.state/workers/worker-{id}.md` を作成する
- `.state/org-state.md` が作成/更新される
- `.state/journal.jsonl` にイベントが記録される
- ワーカー完了後、claude-peers経由で**窓口に**報告が届く（フォアマンではなく窓口）
- 窓口が結果を業務言語で人間に伝える（技術用語を避ける）
- 窓口がフォアマンにペインクローズを依頼する

**確認コマンド**:
```bash
cat .state/org-state.md
cat .state/journal.jsonl
ls .state/workers/
cat registry/projects.md
```

**失敗パターンと対処**:
- ペインが開かない → `ccmux list` で現在のペイン状態を確認、`ccmux split --help` が実行できるか確認
- claude-peersで通信できない → MCPサーバーの起動確認、peer IDの確認
- 状態ファイルが作成されない → org-delegateスキルの手順を見直し
- ワーカーが指示を理解しない → instruction-template.md の記述を改善
- プロジェクト名前解決が動かない → org-delegate Step 0 を見直し

### 2.1 balanced split スケール検証（4 並列 / 8 並列）

**目的**: org-delegate Step 3 の balanced split lookup table が、4 並列・8 並列いずれも `[split_refused]` を発生させずに配置図通りの tree を生成することを実機確認する。

**前提**: テスト 2 が通っていること。ターミナル幅 `W ≥ 160 cols`（`tput cols` で確認）。`pane-layout.md` の 4 並列 / 8 並列 ASCII 図を手元で開いておく。

**手順**:
1. `tput cols` を実行し W を記録。160 未満なら検証不能としてスキップ or ターミナルを広げる。
2. 窓口に互いに独立な 8 タスク（ダミーで良い。例: `echo-1` 〜 `echo-8` のような軽量タスク）を順次依頼。k=1〜8 それぞれが以下を満たすことを確認:
   - a. フォアマンの `ccmux split` 呼び出しが `[split_refused]` を返さない
   - b. Step 3-1 のシェルスニペットが返す `$target` / `$direction` が `pane-layout.md` の lookup table 通り
   - c. 起動直後の `ccmux list --format json` を **別ログファイル (例: `.state/verification/balanced-split-{timestamp}.log`)** に保存するか、その場で `role == "worker"` の `name` / `id` を記録し、`.state/journal.jsonl` の `worker_spawned` イベントと事後照合する（`journal.jsonl` の schema に raw list スナップショットは含まれないので、verification 用途の一時ログとして分離する）
3. k=4 到達時点でペイン配置を目視し、`pane-layout.md` の 4 並列 ASCII 図と一致するか確認（`foreman` 幅 ≈ W_f/2、ワーカー 4 個が 2×2 のグリッド）。
4. k=8 到達時点で同じく 8 並列 ASCII 図（2×4 グリッド）と一致するか確認。
5. 9 人目のダミータスクを試し、フォアマンが claude-peers で窓口に `SPLIT_CAPACITY_EXCEEDED` を送信することを確認。**当該 9 人目のワーカーのみ派遣を中止し、フォアマン本体の監視ループは継続稼働**すること（`ccmux split` は発行されず、`exit` などでフォアマンが落ちない）。

> **注**: `.state/verification/balanced-split-{timestamp}.log` 等の検証用ログは一時ファイルなのでコミット対象外。`.state/*` は既存の `.gitignore` で除外済み。

**期待結果**:
- k=1〜8 で `[split_refused]` ゼロ
- 8 並列時のワーカー最小幅 ≈ W_f/4、最小高 ≈ H_f/4
- k=9 で明示的 escalate（silently fail しない）

**確認コマンド**:
```bash
# 各 k 到達時に記録
ccmux list --format json | jq '.panes | map(select(.role == "worker")) | sort_by(.id) | .[] | {id, name}'
tput cols  # ターミナル幅の記録
cat .state/journal.jsonl | grep worker_spawned
```

**失敗パターンと対処**:
- k=4 で `split_refused` → `tput cols` の値を確認。W < 160 なら balanced split table の要件未満。ターミナル拡大で再試行
- k=3 で既に `split_refused` → foreman 直下に file-tree / preview が居座っていないか確認（これらが表示中だと `W_f` が 20〜40 cols 目減りする）
- 配置が ASCII 図と乖離 → 前タスクで閉じ残ったワーカーの `ccmux close` 忘れ。`ccmux list` で role=worker の active が 0 からスタートしているか確認
- k=9 で silently 動く → Step 3-1 の case 文の `*)` ブランチが発火していない。jq スニペットが `.exited` 等存在しないフィールドで全件を抜いていないか確認

---

## 3. org-suspend テスト（中断）

**目的**: 組織の状態が正しく保存され、全ペインが停止するか確認。

**前提**: テスト2でワーカーが稼働中（または完了直後）の状態。

**手順**:
1. 窓口Claudeに「中断して」と伝える
2. `/org-suspend` が発動するのを確認

**期待結果**:
- claude-peers経由でワーカーに SUSPEND メッセージが送信される
- ワーカーが claude-peers 経由で状態を報告する (ccmux には pane text スクレイプ API が未実装なので、未応答ワーカーは git 状態ベースで推定する)
- `.state/org-state.md` の Status が `SUSPENDED` になる
- `.state/org-state.prev.md` にバックアップが作成される
- claude-peers経由で全ピアに SHUTDOWN が送信される
- 全ワーカーペインが先に閉じられ、次にフォアマン、最後にキュレーターが閉じられる
- 窓口が中断完了を報告する

**確認コマンド**:
```bash
cat .state/org-state.md | head -5  # Status: SUSPENDED を確認
cat .state/journal.jsonl | tail -1  # suspend イベントを確認
```

**失敗パターンと対処**:
- ワーカーがSUSPENDに応答しない → Phase 2のスクレイプが機能するか確認
- ペインが閉じない → `ccmux close --name X` でペインを明示破棄しているか確認 (ccmux v0.5.8+)
- 状態ファイルが不完全 → org-suspendの手順を見直し

---

## 4. org-resume テスト（再開）

**目的**: 中断後に再起動し、前回の状態が正しく復元されるか確認。

**前提**: テスト3で中断済み。

**手順**:
1. 窓口Claudeの端末を**完全に閉じる**
2. clone先で再度ClaudeCodeを起動する
3. `/org-start` を実行する

**期待結果**:
- `/org-start` が `.state/org-state.md` を検出し、Status: SUSPENDED を確認
- `/org-resume` の手順に従い、前回の状態サマリーが表示される
- 各作業ディレクトリのgit状態との照合結果が報告される
- 再開計画が提案される
- 人間の承認を待つ（勝手にワーカーを派遣しない）
- フォアマンとキュレーターペインが再起動される（claude-peers経由で指示）

**確認ポイント**:
- ブリーフィング内容が `.state/org-state.md` と一致するか
- git状態の照合が正確か
- フォアマンとキュレーターペインが起動しているか

**失敗パターンと対処**:
- `/org-start` が状態を読まない → org-start スキルの Step 1 を見直し
- 状態が不正確 → org-state.md のフォーマットまたはorg-suspendの書き込みを見直し
- キュレーターが起動しない → org-start Step 2 のclaude-peers送信を確認

---

## 5. 突然の終了テスト（クラッシュリカバリ）

**目的**: org-suspendを実行せずに端末を閉じた場合、どこまで復元できるか確認。

**手順**:
1. テスト2の状態（ワーカー稼働中）で、**suspendせずに**端末を閉じる
2. 再度ClaudeCodeを起動する
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
- 改善提案がある場合、claude-peersで窓口に通知される

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
2. フォアマンがclaude-peers経由で役割指示を受け取っているか確認
3. キュレーターがclaude-peers経由で `/loop 30m /org-curate` を実行しているか確認
4. `knowledge/raw/` に閾値未満のファイルを置き、キュレーターがスキップするか確認
5. 閾値以上に増やし、次の /loop サイクルで実行されるか確認

**期待結果**:
- `/org-start` 実行後に窓口の下にフォアマンとキュレーターが横並びで開く
- フォアマンが DELEGATE メッセージを待ち受ける状態になる
- キュレーターが `/loop` を開始する
- 30分ごとに org-curate が発動する
- 閾値未満ではスキップ、閾値以上で実行

**失敗パターンと対処**:
- ペインは開くが指示を受け取らない → claude-peersのピア検出タイミングを調整（list_peersのリトライ）
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
1. clone先でClaudeCodeを起動
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
