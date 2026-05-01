# 同期ポリシー

このリポジトリ (`suisya-systems/claude-org-ja`, ja) と兄弟リポジトリ `suisya-systems/claude-org` (en) の間で、編集をどう伝播させるかのルール。

各成果物カテゴリの正本がどちら側かは en repo の `docs/canonical-ownership.md` を参照すること。

## リリース連動 SLA

ja 側は en 側に対して遅延が許容されるが、リリースケイデンスで上限が決まる。

- **リリースウィンドウ中**（en `vX.Y.0` タグから対応する ja リリースタグまでの間）: ja は ja リリース出荷前に追従しきること。リリースを阻害する翻訳ギャップはリリースを阻害する。
- **リリースウィンドウ外**（en 側にアクティブなリリース進行がない期間）: ja は **マイナーリリース 1 本** までの遅延を許容する。en `main` から 2 マイナー以上遅れた場合、ja 側に `translation-pending` トラッキング Issue を起票する。
- **ホットフィックス**（en `vX.Y.Z` で Z>0 かつセキュリティ・正しさ修正）: ja は 14 日以内、悪用可能な修正の場合はそれより早く反映する。

「遅延」は en-canonical 成果物（`docs/canonical-ownership.md` 参照）への en 側コミット数で計測する。誤字修正のみのコミットはカウントしない。

## バックポート制限

ja 側で直接行った編集を en 側へバックポートできるのは、以下の **3 カテゴリのみ**。

1. **用語** — グロッサリ修正（例: `フォアマン` → `ディスパッチャー`）。ja 側でより明確な用語が見つかり、en 側 glossary を追従させたい場合。
2. **概念定義** — 役割・ライフサイクル・不変条件等の説明文で、ja 側の文言が結果として en より鋭くなったもの。バックポート対象は **定義そのもの** であり、周辺の散文ではない。
3. **API 契約** — スキーマ・フック・CLI 表面の変更で、実装議論が ja で先行したため ja 側ドキュメントが偶発的に正本化したもの。

それ以外（散文の磨き上げ、例の追加、ja 側での構造再編）は ja-local に留める。canonical が en 側のコンテンツを変更したい場合は en 側で先に PR を立てる。

## 乖離許容セクション

以下は意図的に乖離してよい。翻訳パリティの対象 **外**。

- `registry/projects.md` — ローカル運用状態（`docs/canonical-ownership.md` で ja-canonical 指定。en 側は無関係な en 用 projects リストを保持）。
- `knowledge/curated/*.md` — キュレーション知見は ja-canonical。en 翻訳はベストエフォートで、リリースをブロックしない。
- `.state/`, `.curator/`, `.dispatcher/` — ランタイム/オペレータ状態。リポジトリ毎にスコープされる。
- en-only: `bootstrap-cherry-picks.md`, `docs/translation-manifest.md` — メタ/プロセス成果物。
- README の第一印象コピー（トーン、スクリーンショット、バッジ選択）は技術主張が一致する限り両側で異なってよい。

## `docs/getting-started.md` 例外（B3）

plan-110 §8 Wave C Minor 振り返りの通り、`docs/getting-started.md` は en repo の `docs/canonical-ownership.md` 上では **ja-canonical** に分類されているが、en 側は **B3 並列 SOT** コピーを保持する。オンボーディングはプラットフォーム依存のインストール手順や en/ja で乖離するパスに敏感で、純粋な翻訳では不自然になるため、両側がそれぞれの該当ファイルを編集し、構造的変更（セクション追加・削除）はバックポート制限の枠内でバックポート PR にて整合させる。

## クロスリポジトリ通知 CI

ja 側で PR が `main` に merge されると、`.github/workflows/notify-en-changes.yml`（このリポジトリ）が `repository_dispatch` イベント `ja_pr_merged` を en repo へ発火する。受信側の `.github/workflows/notify-ja-changes.yml`（en repo）が ja PR タイトルと URL を載せた `TRANSLATION-PENDING` Issue を起票する。窓口/キュレーターがトリアージし、Issue を close（対象外あるいは canonical-en と判定）するか、翻訳作業をスケジュールする。

逆方向（en → ja）は対称: en 側 merge で `en_pr_merged` を ja repo へ発火し、ja 側に翻訳ペンディング Issue を起票する。

dispatch ステップには受信先 repo に対する `repo` スコープの PAT が必要で、ja 側は `secrets.NOTIFY_EN_PAT`、en 側は `secrets.NOTIFY_JA_PAT`（en→ja 送信側は本 PR では未実装、後続で対応）として保存する。PAT 未設定の間は workflow は休止状態で、受信側は dispatch が来ないため誤起票を起こさない（fail-closed）。

## Auto-mirror runtime

Lead は 2026-04-30 に **Option A (ja = SoT, en = auto-mirror runtime)** を確定した（Issue #171 / Issue #189）。これに基づき、ja `main` への PR merge をトリガとして en 側のランタイムコードを自動同期する CI パイプラインを段階導入する。

### スコープ（mirror 対象 path globs）

以下のパスは ja-canonical かつ auto-mirror 対象。en 側は en repo の `auto-mirror-runtime.yml` 経由で同期される。

- `tools/**/*.py`, `tools/**/*.json`
- `dashboard/app.js`, `dashboard/server.py`, `dashboard/index.html`
- `.claude/settings.json`, `.claude/hooks/**`
- `tests/**`, `tools/test_*.py`

スコープ **外**（既存の翻訳 / 乖離許容ルールを継続）:

- `.claude/skills/**`, `docs/**`, `README.md`, `CLAUDE.md` — 翻訳パイプライン経由
- `knowledge/curated/**`, `registry/projects.md`, `.state/`, `.curator/`, `.dispatcher/` — 乖離許容

分類の正本は en 側 `tools/sync_classifier.py`（pytest 完備）。

### en 側 workflow

- 実体: `suisya-systems/claude-org` の `.github/workflows/auto-mirror-runtime.yml`
- 既存の `repository_dispatch` `ja_pr_merged`（`.github/workflows/notify-en-changes.yml` から発火）を再利用するため、ja 側で新規 PAT は不要
- 運用ドキュメント: en repo `docs/runbook/auto-mirror-runtime.md`（一時無効化、過去 merge の手動再実行、各フェーズの定義）

### 現在のフェーズ: P1 warn-only

ja PR が merge されると、en 側 workflow は分類結果を ja PR にコメントするだけで、**自動 mirror PR は開かない**。観測期間として最低 1 週間 / ja merge 5 件以上の分類精度確認後に P2 へ進む。

ロードマップ:

| フェーズ | 振る舞い | 移行条件 |
|---|---|---|
| P1（現在） | 分類して ja PR にコメント。mirror PR は開かない | 1 週間 / 5 merges 以上の分類確認 |
| P2 | en 側に mirror PR を開く（手動マージ） | 10 PR 以上のマージ実績、コンフリクト・docstring 影響の把握 |
| P3 | runtime-only の mirror PR を auto-merge（gate は Lead 決定待ち） | P2 の安定運用 4 週間以上 |
| P4 | reverse drift 検出（ja 親なしの en 側 runtime 編集を警告） | 追加機能、ブロッカーなし |

### en 側 runtime 直接編集をしないポリシー（reverse drift 防止）

ja-canonical な runtime コード（上記スコープ）を en 側で直接編集しない。en 側で気付いた修正は ja で先に PR を出し、auto-mirror 経由で en に反映する。

例外（緊急 hotfix で en にしか触れない場合）: 後追いで ja に back-port PR を立て、en の runbook に missed-mirror として記録する。P4 の reverse drift detector は、こうしたケースを検出して可視化することを目的とする。

### Lead 判断ポイント（P1 中はデフォルト適用）

- **docstring overwrite policy**（Issue #189 §Open #1）: デフォルトは「ja の docstrings をそのまま en に乗せる」。overlay 翻訳は採用しない。Lead が覆す場合は本セクションと en `docs/canonical-ownership.md` の該当行を更新するのみで方針切替が完了する
- **ja-only doc コミット**（#163, #168）: P1 では classifier が `translation` クラスとして既存 TRANSLATION-PENDING フローに流すため status quo 維持。新規の警告シグナルは追加しない

P3 段階の gating（#2, #3）は今回スコープ外。

## 通知 CI smoke-test ログ

| 日時 (UTC) | 確認内容 |
|---|---|
| 2026-04-28 | `notify-en-changes.yml` 初発火・en 側 TRANSLATION-PENDING issue 起票確認用の trivial PR |
