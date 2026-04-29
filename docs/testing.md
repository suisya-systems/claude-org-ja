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
  test_parsers.py          # dashboard/server.py のパーサーテスト
  test_org_state_converter.py
  run-all.sh               # shell hook テストランナー
  test-block-git-push.sh
  test-block-org-structure.sh
  test-check-worker-boundary.sh
  fixtures/
    org-state-sample.md    # org-state パーサー用サンプル
    journal-sample.jsonl   # journal パーサー用サンプル
    projects-sample.md     # projects パーサー用サンプル
    workers/
      worker-abc12345.md   # workers パーサー用サンプル
    curated/
      .gitkeep             # スキップ対象の確認用
      sample-topic.md      # knowledge パーサー用サンプル
```

## テスト結果の保存

テスト結果を記録する場合は `docs/test-results/` に保存してください。

## CI

GitHub Actions でも同じ 2 系統のテストを実行します。ローカルで再現できない failure を減らすため、PR 前に両方を通してください。
