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
