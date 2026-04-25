# Issue #48 回帰テスト結果レポート（claude-peers 撤去後の窓口→フォアマン→ワーカー通信検証）

> **本レポートの位置づけと保存先について**: `docs/verification.md` §「テスト結果の記録」および `docs/testing.md` では、反復的な機能検証の結果を `docs/test-results/` 配下に `## テスト{N}: {テスト名}` 形式で記録する運用が定義されている。一方で本ドキュメントは、Epic #43 の完遂判定に紐づく **特定 Issue（#48）向けの一回性の回帰テスト報告書** であり、受け入れ基準 (1)〜(4) の根拠を一つの PR としてレビューに通す目的で作成される。そのため、Issue 単位のレポート群を時系列で並べる新規ディレクトリ `docs/verification/` に `YYYY-MM-DD-{issue}-{topic}.md` 命名で保存する。個別機能テストの `docs/test-results/` 規則には意図的に従っていない。

## 1. サマリー

- **実施日**: 2026-04-22
- **対象**: Epic #43（claude-peers → ccmux-peers 一本化）の子 Issue #48 「窓口 → フォアマン → ワーカー派遣を実際に実行し、claude-peers 無しで全通信が成立することを検証」
- **結論**: **全 4 シナリオ PASS**。窓口・フォアマン・キュレーター・ワーカーの全通信が ccmux-peers MCP のみで成立し、`.state/journal.jsonl` を含むランタイム成果物に `claude-peers` 参照は一切残っていない。
- **関連**:
  - Close 対象: Issue #48
  - 親 Epic: #43（残子 Issue なし）
  - 先行 PR: #56（org-delegate テンプレートの Codex exec 直打ち化／自己編集特例の明文化）

## 2. テスト環境

| 項目 | 値 |
|---|---|
| ccmux | 0.14.0+（auto-upgrade 経路、MCP 12 ツール公開） |
| OS / シェル | Windows 11 / Git Bash / WezTerm |
| レイアウト | `ccmux --layout ops` で起動した 3 ペイン構成（secretary / foreman / curator） |
| MCP サーバ | `ccmux-peers`（user scope, `claude mcp list` で Connected） |
| 起動フラグ | フォアマン／キュレーター／ワーカー全て `--dangerously-load-development-channels server:ccmux-peers` 明示付与 |

## 3. シナリオ別結果

### サマリー表

| # | シナリオ | 判定 | メモ |
|---|---|---|---|
| 1 | `/org-start` fresh | PASS | foreman/curator を ccmux-peers のみで spawn。auto-upgrade の代替として `--dangerously-load-development-channels` 明示フラグで成立 |
| 2 | 単発 dummy タスク（todo-cli: Python TODO CLI） | PASS | 派遣 → 完了 → クローズまで ccmux-peers only |
| 3 | SUSPEND → RESUME | PASS | foreman/curator を一度閉じ、状態を `org-state.md` に永続化、`/org-start` で復元成功 |
| 4 | 並列 3 人（palindrome / wordcount / leapyear） | PASS | balanced split で 3 ペイン spawn、3 人並列稼働、3 人とも自走完了 |

### シナリオ 1 — `/org-start` fresh

- **手順**:
  1. 窓口 Claude Code で `/org-start` を実行
  2. `mcp__ccmux-peers__spawn_pane` でフォアマン（id=2）／キュレーター（id=3）を起動（起動コマンドに `--dangerously-load-development-channels server:ccmux-peers` を明示）
  3. 両ペインの "Load development channel?" プロンプトに対し `mcp__ccmux-peers__send_keys(enter=true)` で承認
- **観測事実**:
  - `mcp__ccmux-peers__list_peers` で foreman / curator の双方が認識された
  - 窓口 → フォアマン宛の DELEGATE テンプレ送信、窓口 → キュレーター宛の `/loop 30m /org-curate` 指示送信ともに `mcp__ccmux-peers__send_message` 単独で完結
  - claude-peers 系ツール呼び出しはゼロ
- **判定**: PASS

### シナリオ 2 — 単発 dummy タスク（todo-cli）

- **手順**:
  1. 窓口にて「Python 標準ライブラリのみで TODO CLI を作る」タスクを依頼
  2. `/org-delegate` でワーカー `worker-todo-cli`（id=5、パターン C エフェメラル）を派遣
  3. ワーカー完了報告 → 窓口経由で CLOSE_PANE 依頼 → フォアマンが pane close
- **観測事実**:
  - 成果物: `todo.py`（add / list / done / rm、UTF-8 JSON ストア、Python 標準ライブラリのみ）/ `README.md`
  - 窓口 ⇄ フォアマン ⇄ ワーカーの全往復が `mcp__ccmux-peers__send_message` のみで成立
  - 完了通知後の `mcp__ccmux-peers__close_pane` でワーカーペインが閉じることを確認
- **判定**: PASS

### シナリオ 3 — SUSPEND → RESUME

- **手順**:
  1. `/org-suspend` 実行 → foreman / curator に SHUTDOWN メッセージ送信
  2. `mcp__ccmux-peers__poll_events` が空になったのち、`mcp__ccmux-peers__close_pane` を明示呼び出ししてペイン完全閉鎖
  3. `.state/org-state.md` の `Status: SUSPENDED` を確認後、`/org-start` で再開
- **観測事実**:
  - SUSPEND 時: `org-state.md` Status=SUSPENDED、dashboard 停止、journal に suspend イベント記録
  - RESUME 時: 新 foreman（id=9）／curator（id=10）が spawn、`org-state.md` Status=ACTIVE、journal に resume イベント記録
  - 抜粋（`C:/Users/iwama/Documents/work/claude-org/.state/journal.jsonl` 末尾）:
    ```json
    {"ts":"2026-04-22T14:10:00Z","event":"suspend","reason":"issue_48_scenario_3_test","active_workers":[],"pending_items":["issue_48_scenario_5"]}
    {"ts":"2026-04-22T14:14:00Z","event":"resume","reason":"issue_48_scenario_3_test","foreman":"9","curator":"10"}
    ```
- **判定**: PASS

### シナリオ 4 — 並列 3 人派遣

- **手順**:
  1. 同時に 3 タスク（palindrome / wordcount / leapyear）を窓口に依頼
  2. フォアマンが balanced split 戦略で `mcp__ccmux-peers__spawn_pane` を 3 回連続実行
  3. ワーカー 3 人（id=6 / 7 / 8）が並列稼働 → 個別に完了報告 → 一括 CLOSE_PANE
- **観測事実**:
  - 3 回の spawn いずれも MIN_PANE 制約をクリア（画面サイズ 327×81 で十分な余裕）
  - 3 ワーカーが互いの送受信に干渉せず、各々のタスクを自走完了
  - 完了報告の送受信、CLOSE_PANE の一括処理ともに ccmux-peers のみで成立
- **判定**: PASS

## 4. 受け入れ基準の確認

### (1) 手動 1 周完走

上記シナリオ 1〜4 をすべて手動実行し、いずれも PASS。

### (2) `.state/journal.jsonl` に claude-peers 参照なし

レポート作成時点でのコマンドと結果（※ 本ドキュメントを生成した worktree には `.state/` が存在しないため、メインチェックアウトの絶対パスで実行）:

```
$ grep -c "claude-peers" C:/Users/iwama/Documents/work/claude-org/.state/journal.jsonl
0
```

journal は今回のセッション分を含む 48 行（`wc -l` 確認）が記録済みで、その全体に対して `claude-peers` の文字列マッチは 0 件。

### (3) 個別 Issue 起票の要否

シナリオ実行中に Blocker / Major レベルの不具合は観測されなかったため、個別 Issue の起票は不要と判断（軽微な運用上の注意は §5 に記載）。

### (4) Codex レビュー Clean

`codex exec --skip-git-repo-check` 直打ち（CLAUDE.local.md の指定プロンプト）で反復レビューを実施。

- **Round 1**: Major 2 件を受領。
  - (a) AC(4) の根拠が未来形で、実行済みの証跡になっていない → 本節に Round 1／Round 2 の結果を追記することで対応。
  - (b) 本レポートの保存先と形式が `docs/verification.md` / `docs/testing.md` の `docs/test-results/` 規則と不整合 → 冒頭の「本レポートの位置づけと保存先について」の注記で、Issue 単位の回帰テスト報告書として意図的に `docs/verification/` 配下に置く旨を明示。
- **Round 2**: 上記 2 件を反映して再実行 → Clean。
- 備考: `codex exec` 実行環境は `sandbox: read-only` のため `git diff main...HEAD` が safe.directory 設定エラーで取得不能だった。レビュアーは代替としてファイルシステムから直接 `docs/verification/` と関連 docs を読み比較しており、観点 (1)〜(4) のすべてがカバーされている。

## 5. 観察された運用上の注意（既知問題・副次発見）

- **`py -3` ランチャの起動失敗**: Git Bash 上の一部環境で `py -3` が "Unable to create process" で失敗するケースを確認。Worker 系 CLAUDE.md の Python 実行指示は、`py -3` と `python` のフォールバックを両方許容する書き方が望ましい（将来 Worker template の更新候補）。
- **SUSPEND 時のプロセス終了挙動**: SHUTDOWN メッセージの送信のみでは Claude Code プロセスが終了せず、`mcp__ccmux-peers__close_pane` の明示呼び出しが必要。これは既存 `org-suspend` Pass 2 の想定通りであり仕様。
- **balanced split の MIN_PANE 制約**: 並列 3 人派遣時、画面サイズ 327×81 では問題なく 3 ペインを確保できた。より狭いターミナル幅の環境では `[split_refused]` が返る可能性があるため、運用ドキュメントへの注記が将来検討課題。

## 6. クローズ判断

- 本レポートが Codex Clean で merge されたら **Issue #48 は close 可能**。
- Epic #43 は残り子 Issue が存在しないため、本 Issue クローズと同時に Epic 起票者側の判断で close 可能。

## 7. 補足: ドッグフード

本セッションは PR #56（同日 merge）で更新された org-delegate テンプレートをそのまま運用しており、

- ワーカー指示は `CLAUDE.local.md` に集約（Secretary 誤認防止のため、ルート `CLAUDE.md` を明示無視）
- Codex レビューは `codex:rescue` ではなく `codex exec` を直打ち
- 自己編集（claude-org 自身の編集）特例として `block-org-structure.sh` hook を `settings.local.json` から除外

を Worker 起動直後から適用した。本 PR 自体が PR #56 の運用効果を確認するドッグフード事例である。
