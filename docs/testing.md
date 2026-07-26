# Automated Tests

Python ベースのパーサー/コンバーター回帰検知テストと、Bash ベースの hook 回帰検知テストをまとめた実行ガイド。

## 対象

| 関数 | 内容 |
|------|------|
| `_parse_org_state` | org-state.md のステータス・目的・作業項目を解析 |
| `_parse_journal` | journal.jsonl のイベントログを解析 |
| `_parse_projects` | projects.md のマークダウンテーブルを解析 |
| `_parse_workers` | worker-*.md ファイル群を解析 |
| `_parse_knowledge` | curated/*.md の H2 セクション数をカウント |
| `org_state_converter.py` | org-state Markdown -> JSON 変換とダッシュボード JSON 読み込み |
| `.hooks/*.sh` | worker boundary / claude-org structure / git push block の回帰検知 |

## 実行方法

```bash
# Python テスト
# Windows (py -3 が使えない場合は python でも可)
python -m unittest discover -s tests -v

# Mac / Linux
python3 -m unittest discover -s tests -v

# Shell hook テスト
bash tests/run-all.sh
```

プロジェクトルートで実行してください。外部ライブラリは不要ですが、shell hook テストには `bash` と `jq` が必要です。

日常運用では、Python テストだけでなく `bash tests/run-all.sh` まで含めて成功として扱ってください。

## テスト構成

```
tests/
  __init__.py              # パッケージ初期化（空）
  run-all.sh               # shell hook テストランナー（収集の SoT）
  test_*.py                # Python テスト（unittest discover が収集）
  test-*.sh                # shell hook テスト（run-all.sh が収集）
  sandbox/
    test_*.sh              # sandbox smoke テスト（run-all.sh が収集）
  fixtures/                # 各テストのサンプルデータ
```

### shell テストの置き場所ルール

**shell テストは必ず `tests/` 直下（または `tests/sandbox/`）に置く。** `.hooks/` 配下に
テストを置くと `tests/run-all.sh` の収集対象から外れ、CI で実行されないまま気付かれない
（Issue #787 の実体）。

この置き場所のズレは `tests/run-all.sh` の収集漏れ検出ガードが検出する。ガードは git 追跡
ファイルから「テストに見える命名」（`test-*.sh` / `test_*.sh` / `*-test.sh` / `*_test.sh`）を
列挙し、ランナーが実際に実行した集合と突き合わせて、未収集があれば fail する。

- テストでないファイル（hook の fixture 等）は、`test` に見えない名前を付けて収集対象から
  外す。例: `.hooks/fixture-always-block.sh` は hook 配線の手動確認用 fixture であって
  テストスイートではないため `fixture-` を名乗る。
- どうしても例外が必要な場合のみ、`run-all.sh` の `COVERAGE_EXEMPT` に repo root 相対パスを
  理由コメント付きで追加する。ガードごと削除しないこと。

検出を CI の workflow ではなくランナー側に置いているのは、手元実行と CI の判定基準を
1 つに保つため。

なお shell テストは実行時の cwd に依存しない（スクリプト自身の位置から repo root を導出する）。

## テスト結果の保存

テスト結果を記録する場合は `docs/test-results/` に保存してください。

## CI

GitHub Actions でも同じ 2 系統のテストを実行します。ローカルで再現できない failure を減らすため、PR 前に両方を通してください。
