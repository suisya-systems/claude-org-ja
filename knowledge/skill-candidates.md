# skill 化候補キュー

`skill-eligibility-check` が `skill_recommend` と判定した候補を蓄積する。
窓口は pending エントリが **5 件（N=5）** 以上になった時点で、人間にバッチで問い合わせる。

都度問い合わせよりバッチの方が意思決定コストが低い（Issue #68 方針）。

> **このファイルはフォーマット定義のみ・エントリ常に空（Issue #755）**: 実エントリは
> operator-private な作業知見であり OSS リポジトリに載せない。実エントリは machine-local な
> **`knowledge/skill-candidates.local.md`**（.gitignore 済み）に追記する。本公開ファイルは
> エントリフォーマット・status 語彙・運用ルールの**定義**を保持し、`## エントリ一覧` は空のまま保つ。
> `skill-eligibility-check` Step 4 の追記先・status 遷移の編集先はすべて `.local.md` 側。
> 閾値カウント（`tools/check_curate_threshold.py` / `skill-audit`）は公開 + local の**両ファイルを合算**して読む
> （読む 2 ファイルと順序は `check_curate_threshold.py` の `CANDIDATE_ENTRY_PATHS` が SoT）。

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
- **status**: {pending | deferred | approved | rejected | merged-into-*}
- **決定日**: 未定
- **却下理由**: （status が `rejected` に遷移したとき記入、それ以外は省略）
- **統合先**: （status が `merged-into-*` のとき記入、それ以外は省略）
```

> **status 行の形式は厳守**: pending のカウントは `- **status**: pending` の**行形式
> 完全一致**で行われる（`skill-audit` Step 1 / `tools/check_curate_threshold.py`）。
> インデント・スペース数・大文字小文字を変えるとカウントから漏れ、skill-audit /
> オンデマンド curate が発火しなくなる。
>
> **コードフェンス内は数えない**: カウントはコードフェンス（行頭 `` ``` `` /
> `~~~` で開閉するブロック）の**外側の行のみ**が対象。上の「エントリフォーマット」の
> テンプレ例文が誤カウントされない防御であり、このセマンティクスは
> `tools/check_curate_threshold.py` の `count_pending` / `skill-audit` Step 1 の
> カウントコマンド / 本節の 3 者で完全一致させること（変更時は 3 箇所同時更新。
> `tools/test_check_curate_threshold.py` の parity テストが drift を検出する）。
> **カウントは本公開ファイル + `knowledge/skill-candidates.local.md` の 2 ファイルを合算**
> （Issue #755。読む 2 ファイルと順序は `check_curate_threshold.py` の `CANDIDATE_ENTRY_PATHS` が SoT。
> 各ファイルは独立してフェンス状態を持つ）。

## status の遷移

- `pending`: 人間に未問い合わせ。`skill-audit` の発火条件 N=5 でカウントされる
- `deferred`: **人間に提示済みで、人間が「今は見送り（保留）」と判断した**状態。terminal（承認 / 却下）ではないが、**閾値カウント対象外**であり**再問い合わせもしない**。`pending` と違い `- **status**: pending` の行形式に一致しないため、`tools/check_curate_threshold.py` の `count_pending` / `skill-audit` Step 1 の pending カウントから自動的に除外される（見送り済み候補が worker クローズのたびに閾値を再発火させて curator を無駄起動する問題への対策。Issue #753）
- `approved`: 人間が skill 化を承認。対応する `.claude/skills/{name}/SKILL.md` を作成
- `rejected`: 人間が却下。却下理由を「却下理由」フィールドで追記
- `merged-into-{existing-skill}`: 既存 skill に統合された。新規作成はしない

**`deferred` は `pending` に戻さない**（再問い合わせ対象外）。見送りは一度で確定し、同じ候補を蒸し返さないための状態である。人間が後日あらためて skill 化したくなった場合は、`deferred` を書き換えるのではなく**新しい日付で別エントリ**を起こす（`approved` / `rejected` と同じく履歴を残す運用）。

`deferred` / `approved` 以降のエントリは履歴として**削除せず保持**する。
同じ `pattern_name` が再び上がってきた時の参考になる。

## 運用メモ

- `skill-eligibility-check` は判定時にこのファイルを自動追記する（同スキル Step 4）
- 同 `pattern_name` で既に `pending` エントリがある場合は新規追加せずマージ（関連タスク・raw ファイルの追記のみ）
- 同 `pattern_name` で既に `deferred` エントリがある場合は**再追加しない**（`deferred` を `pending` に戻さない・新規 `pending` も起こさない）。見送り済み候補は蒸し返さないのが原則。人間が明示的に再検討したいときのみ新しい日付で別エントリを起こす
- 既に `approved` / `rejected` / `merged-into-*` のエントリがある場合は、新しい日付で別エントリを作る（過去の決定を履歴として残すため）

## エントリ一覧

<!-- 以下にエントリが自動追記される -->
