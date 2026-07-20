# `block-org-structure.sh` が自 repo の `registry/` 編集に誤発火する

## 現象

`.hooks/block-org-structure.sh` は worker ディレクトリ直下の `registry/` / `dashboard/` / `knowledge/` への書き込みを拒否する（`ROOT_ONLY_BLOCKED`）。これは「claude-org の組織構造を worker dir に再現させない」ためのガードだが、**対象リポジトリ自身が正当に `registry/` を持つ場合**（EN ミラー repo、ja-upstream repo）にも誤発火する。

## 現在の緩和（Issue #736）

既に **tracked なファイルの編集**は例外として許可される（`is_tracked_in_worker_repo`、`git ls-files --error-unmatch` による index 照合）。out-of-org worker のみに適用され、in-org worker には適用されない。

## 残っているギャップ: 新規作成

`registry/` 配下への**新規ファイル作成**は例外に乗らない。tracked でないため当然だが、これは鶏卵状態を生む。

- 実例（2026-07-20, `ja-project-dossier-stage1`）: プロジェクト台帳 `registry/projects/<slug>/` を新規に作ろうとして全面ブロック。
- **回避した方法**: 窓口（`registry/` の所有者）が空ファイルを作成し `git add` して tracked 化 → 以降はワーカーが #736 の例外に乗って中身を記入できる。`git ls-files` は index を見るため commit までは不要。
- git は空ディレクトリを追跡しないため、**編集予定の全ファイルを実ファイルとして**用意する必要がある（ディレクトリだけでは不十分）。

## 配備ギャップの罠（同日、実測）

実行されるフックは `CLAUDE_ORG_PATH` 側（`/home/happy_ryo/work/dog/claude-org-ja/.hooks/`）であり、**ワークツリー内の `.hooks/` ではない**。ja 本体の作業コピーが main より遅れていると、main にマージ済みの #736 例外が実行系に存在せず、tracked 化しても例外が効かない。

- 切り分け方: `wc -l` と `grep -c 736` で実行系フックとワークツリー側を比較する（旧版 188 行 / #736 入り 208 行）。
- **フックをすり抜ける回避（python 直書き等）は行わないこと。** 実行系の版ずれとして報告し、ja 本体を main に追随させて解決する。
