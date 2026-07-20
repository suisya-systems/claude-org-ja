# 翻訳パスの EN repo 固有の罠

2026-07-18 の `en-p2-translation-batch` 実測から。

## skill drift ゲートは EN では非活性、ただし `.in` 持ちは同時ステージが要る

- `tools/test_gen_skill_prose.py::test_production_manifest_no_drift` は `.dispatcher/**` 配下の manifest ソース（divergence-allowed・非ミラー）が欠落しているため EN では skip される。EN の `SKILL.md` / `SKILL.md.in` は既に散文が乖離しており手編集可。
- **ただし `.in` を持つスキルは pre-commit が `.md` と `.md.in` の同時ステージを要求する。** 片方だけ stage すると commit が落ちる。

## `audit_link_paths.py` は green を目指さない

EN のベースラインは約 110 違反（EN は CI 未接続）。翻訳時は green ではなく**違反数の前後差分**で確認すること。

## 同一ドキュメントに触る複数 Issue は「最終状態を 1 回」訳す

diff を順に再生してはならない。**後のマージ SHA の最終状態を 1 回だけ翻訳**すれば両 Issue をカバーできる。重なるドキュメントは 1 ワーカー / 1 ブランチ / 1 PR に束ねる（コンフリクトゼロ、`Closes` 一括で監査性も良い）。

## Codex はロケール混入を検出する

EN の運用コードブロックに ja slug がハードコードされていたり、ja にしかない成果物（`docker/` PoC 等）への doc 参照が残っていると P2 指摘される。修正パターン:

- 実行不能プレースホルダ（`OWNER/REPO`）へ置き換える
- ja-only の実体には「EN mirror note」callout を付ける
- **黙って ja パスを残さない**

## unknown-class ファイルは即エスカレーション

`docker/**`、`pyproject.toml`、`requirements.txt` などには解決ルールが無い（`docs/runbook/auto-mirror-runtime.md` は "manual triage" としか書いていない）。**即エスカレーションすること。** 判断待ちの間も翻訳クラスの作業は並行継続してよい。

## 検証深度の実測

- 2026-07-18 のバッチ: `verification_depth = full`（プロファイルはこちらを採用）
- 2026-07-16 の初期 catch-up: `verification_depth = minimal`

後発の `full` に収束したためプロファイルは `full`。なお「スコープ契約が翻訳作業に `/verify` 不要と書いていた」のとは**別軸**である（検証深度と `/verify` 実施要否を混同しないこと）。
