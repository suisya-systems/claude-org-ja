# Codex design review trigger（窓口が実行、Issue #337）

> **一次参照元**: [`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) Step 1.7（トリガー判定の概要のみ）。本ドキュメントはトリガー表・実行コマンド・review 要約の組み込み手順の詳細 SoT。

`preview` 出力の `description` / `--target` 件数 / 参照ドキュメントを見て、以下の **いずれか 1 つ以上** に該当する場合は、`apply` の前に Codex design review を実行する。Curator session #18 retrospective (Issue #283 / session #12) で「事前 Codex design review が 1 ラウンドで Blocker 2 件 + Major 5 件を捕捉」した実績に基づくゲート。

## トリガー条件

| トリガー | 判定方法 |
|---|---|
| 推定工数 ≥ 3h | タスク説明から窓口が判断（ユーザー入力 / preview の規模感） |
| 新規 module / 新規 tool 導入 | description に「新規」「new tool」「新ツール」「新規導入」等、または preview の作成予定ファイルが新規パスのみ |
| ファイル変更 ≥ 3 件 | `--target` の数 + preview の brief に列挙される編集対象 |
| `docs/contracts/` 配下の契約ドキュメント参照 | description / brief / `--knowledge` で `docs/contracts/` を参照 |

## 実行手順

```bash
codex exec --skip-git-repo-check "<task-id> の design review。\
  タスク内容: <description>。\
  対象ファイル: <target paths>。\
  関連 contract / 参考: <docs paths>。\
  事前設計上の Blocker / Major / Minor / Nit を分類し、各指摘に対象ファイル:行番号と根拠を添えて日本語で簡潔に。"
```

`codex:rescue` skill は使わない（CLAUDE.local.md の禁止事項）。`codex exec` 直打ちのみ。

## review 要約の組み込み

- 要約を `tmp/codex-review-{task-id}.md` に保存
- `apply` 呼び出し時に **`--impl-guidance "<要約本文>"`** を渡す。これにより要約本文が brief の `[implementation].guidance` に展開され、ワーカーが直読できる
- 補足として `--knowledge tmp/codex-review-{task-id}.md` を追加すると brief の `[references].knowledge` にパスが列挙され、ワーカーが必要に応じて全文を参照できる（`gen_worker_brief.py` はパスを列挙するだけで本文は埋め込まない）。本文を確実にワーカーへ届けるのは `--impl-guidance` 側の責務
- Blocker / Major が指摘された場合は、ユーザーに上げて方針変更可否を確認してから apply に進む

## helper script

Issue #337 acceptance で optional とされており、本 PR では実装しない。Secretary が手動で上記表を判定する。
