# skill 化候補キュー

`skill-eligibility-check` が `skill_recommend` と判定した候補を蓄積する。
窓口は pending エントリが **5 件（N=5）** 以上になった時点で、人間にバッチで問い合わせる。

都度問い合わせよりバッチの方が意思決定コストが低い（Issue #68 方針）。

## エントリフォーマット

各候補は 3 レベル見出し `### {YYYY-MM-DD} {pattern-name}` で始まるブロックとする。

```markdown
### {YYYY-MM-DD} {pattern-name}
- **判定スコア**: {score}/5
- **該当シグナル**: {matched_signals の配列を "[a, b, c]" 形式}
- **根拠**: {1-2 行}
- **関連タスク**: {task_ids、curation 文脈では空 "[]" 可}
- **関連 raw ファイル**: {raw_files のパス列}
- **呼び出し元**: {post_retro | curation}
- **提案 skill 名**: {kebab-case 名}
- **status**: pending
- **決定日**: 未定
- **却下理由**: （status が `rejected` に遷移したとき記入、それ以外は省略）
- **統合先**: （status が `merged-into-*` のとき記入、それ以外は省略）
```

## status の遷移

- `pending`: 人間に未問い合わせ。`skill-audit` の発火条件 N=5 でカウントされる
- `approved`: 人間が skill 化を承認。対応する `.claude/skills/{name}/SKILL.md` を作成
- `rejected`: 人間が却下。却下理由を「却下理由」フィールドで追記
- `merged-into-{existing-skill}`: 既存 skill に統合された。新規作成はしない

`approved` 以降のエントリは履歴として**削除せず保持**する。
同じ `pattern_name` が再び上がってきた時の参考になる。

## 運用メモ

- `skill-eligibility-check` は判定時にこのファイルを自動追記する（同スキル Step 4）
- 同 `pattern_name` で既に `pending` エントリがある場合は新規追加せずマージ（関連タスク・raw ファイルの追記のみ）
- 既に `approved` / `rejected` / `merged-into-*` のエントリがある場合は、新しい日付で別エントリを作る（過去の決定を履歴として残すため）

## エントリ一覧

<!-- 以下にエントリが自動追記される -->
