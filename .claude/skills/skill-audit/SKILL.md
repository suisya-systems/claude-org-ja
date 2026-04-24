---
name: skill-audit
description: >
  skill の棚卸し（廃止候補 / 重複統合 / owner 明記チェック）。
  状態ベースで発火する: 候補キュー knowledge/skill-candidates.md の pending が 5 件以上、
  または .claude/skills/ の skill 数が 20 以上になった場合のみ実行。
  時間ベースの /loop では起動しない（変化の無い日に raw ログを汚す副作用を避けるため）。
---

# skill-audit: skill 棚卸し

skill 数の増加に伴って `org-delegate` の work-skill 検索にノイズが増えるのを防ぐため、
定期的ではなく **状態ベース**で棚卸しを行う。

Issue #68 / Codex 指摘のとおり、skill 数増加そのものよりも「検索面のノイズ」が本丸。
本スキルは 3 つの観点（廃止 / 重複統合 / owner 明記）を機械的にチェックし、
変更提案を窓口 Claude にまとめて送る。自動で skill を削除・変更することはしない。

## Step 1: 発火条件チェック（状態ベース）

以下をいずれも満たさない場合は **即終了**（ログも残さない）。

```bash
# 候補キュー pending エントリ数
cand_count=$(grep -c '^- \*\*status\*\*: pending' knowledge/skill-candidates.md 2>/dev/null || echo 0)

# skill ディレクトリ数（SKILL.md を持つディレクトリ）
skill_count=$(find .claude/skills -maxdepth 2 -name SKILL.md | wc -l)
```

- `cand_count >= 5` **または** `skill_count >= 20` なら続行
- どちらも満たさなければ終了（このとき報告は不要）

数値の根拠: N=5 / M=20 は Issue #68 のデフォルト。実運用で重くなれば PR で調整。

## Step 2: 廃止候補の洗い出し

各 skill について以下を評価し、1 つ以上当てはまれば「廃止候補」とする。
詳細な手順は `references/audit-checklist.md` を参照。

- 直近 90 日で呼び出し履歴が無い（履歴が取れない場合はスキップ）
- origin.task_id が 1 件のみで、それ以降の類似タスクで再利用されていない
- description と `SKILL.md` 本文の実装に明らかな乖離がある

## Step 3: 重複統合候補の洗い出し

skill ペアを総当たりして以下を確認する:

- description の主題語（動詞・目的語）が重複している
- triggers（または description 中の発動条件）が重なる
- 片方がもう一方の特殊化であり、パラメータ差し替えで兼用できる

重複の疑いがあるペアは「統合候補」としてリストアップする。
実際の統合判断は人間が行うので、ここでは候補提示のみ。

## Step 4: owner 未明記の洗い出し

全 skill の SKILL.md frontmatter を読み、以下を確認する:

- `owner:` または `maintainer:` フィールドが無い skill
- あっても空文字列のもの

これらは「owner 未明記」としてリストアップ。
本プロジェクトの既存 skill 全てが現時点で owner 未記載である点は想定内で、
最初の監査実行では一括提案になる見込み。

## Step 5: 報告

`ccmux-peers` の `send_message(to_id="secretary", ...)` で窓口 Claude に送る。

```
[skill-audit] 棚卸し結果
- 廃止候補: {n} 件 ({skill-name} 一覧)
- 統合候補: {m} ペア ({skill-a} × {skill-b} 一覧)
- owner 未明記: {k} 件 ({skill-name} 一覧)

発火条件: cand_count={cand_count} / skill_count={skill_count}
詳細: 判定根拠は本メッセージ末尾の一覧を参照。

人間承認後に削除・統合・owner 追記を実施してください。自動変更はしていません。
```

候補が 0 件（クリーンな状態）だった場合も報告する: 「棚卸し実行、変更提案なし」。
次回は次の閾値超過まで実行されないので、0 件でも実行した事実を残す意味がある。

## トリガー経路

このスキルは自律的に走らない。以下のいずれかで起動する:

1. `org-curate` Step 5 の末尾で閾値を満たせば呼び出される（推奨経路）
2. 窓口が `skill-candidates.md` を見て手動で起動する
3. 人間が「棚卸しして」と依頼

`/loop` などの時間ベース起動はしない — Issue #68 の方針。

## 自動変更を避ける理由

- 廃止の誤判断は `org-delegate` 側で「使える skill が無い」状態を生む（委譲精度の低下）
- 統合は description・triggers・手順の擦り合わせを要し、機械的にはできない
- owner 追記は人間確認が最も軽い運用（ここだけ自動化しても効果小）

したがって本スキルは**提案までに留め**、変更は人間承認を経て窓口が手動で行う。
