# EN ミラー PR の CI red: まず切り分ける

auto-mirror PR の test / smoke fail は **1 系統ではない**。同時期に複数 PR が同じように 40-60 秒で落ちても共通原因 1 つとは限らない（2026-07-18, EN #516/#517/#518 で実証: 2 系統だった）。切り分けてから系統別の定型修正を当てること。

## 系統 1: stale-base × 範囲 pin runtime → schema drift ゲート落ち

- auto-mirror ブランチは ja PR マージ時点の**古い main** から切られる一方、runtime pin が範囲指定（`>=0.1.36,<0.2`）のため CI は常に最新 runtime を install する。main に「schema 更新 + runtime bump」が入った後は、それより古い base の全ブランチが `check_runtime_schema_drift.py` で DRIFT fail する。
- **修正はコード変更不要**: `origin/main` をブランチにマージするだけでよい（main が green である事実がそのまま保証になる）。
- schema / runtime 系ミラーが main に入った直後は、open 中の全 auto-mirror PR が同型で同時に落ち得る。

## 系統 2: ja-only リテラル × EN ローカル資産（live ファイルを読むテスト）

- ja 側で「コード + ja ローカル資産（registry / .state / docs 内テーブル）を同時に変える PR」は、ミラー時に **EN ローカル資産側の随伴変更が必要**になる（registry はミラー対象外）。
- 例（EN #518）: ja#731 が registry の列解決を位置→ヘッダ名に変更し、live registry を読むテストを追加した。EN 側は (1) `triage` 列が無い、(2) 列を足すだけでは直らない — alias map が ja ヘッダ（`通称` / `よくある作業例`）前提で EN ヘッダを解決できず positional fallback に落ちる。修正は EN alias 追加 + EN registry 列追加 + EN ヘッダでの回帰テスト。
- **ヘッダ文字列など「ja-only リテラル」が新規導入されたら EN alias の要否を必ず確認する。** live ファイルを読むテストはこの skew を検出する炭鉱のカナリアである。

## 系統 3: installer clone-URL 行の機械ミラーによる smoke red

- ja PR の diff が installer の clone-URL 行（`scripts/install.sh` の `REPO_URL=`、`scripts/install.ps1` の `$RepoUrl =`）を含むと、ja URL（`claude-org-ja.git`）が EN repo に持ち込まれ、EN #506 の EN 適応（`claude-org.git`）を毎回巻き戻す。EN smoke は正確な EN URL を grep するため 3 OS 全部で決定論的に red になる。
- **定型修正は 2 行のみ**: ミラーブランチ上で両ファイルの URL を
  `https://github.com/suisya-systems/claude-org.git` に復元する。ja PR の機能変更は保持すること。
- ローカル検証（CI 不要）: `claude` / `renga` を PATH にスタブし
  `bash scripts/install.sh --dry-run --skip-mcp --dir <tmp>` を実行 → CI と同じ 3 つの grep
  （clone 行 / `Skipping 'renga mcp install'` / `Done. Next steps:`）で確認する。
- 恒久対策候補（未実装）: ミラーパイプラインでの URL sed 戻し、または EN-owned のミラー除外ファイルから URL を導出する方式。

## 再発予告

系統 3 は構造的に再発する（ミラーが機械的である限り毎回巻き戻る）。修正時に「また来た」と驚かないこと。
