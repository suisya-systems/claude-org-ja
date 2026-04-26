---
name: skill-audit
description: >
  skill の棚卸し（廃止候補 / 重複統合 / owner 明記チェック）。
  状態ベースで発火する: 候補キュー knowledge/skill-candidates.md の pending が 5 件以上、
  または .claude/skills/ 配下の work-skill 数（org-* を除く）が 20 以上になった場合のみ実行。
  時間ベースの /loop では起動しない（変化の無い日に raw ログを汚す副作用を避けるため）。
---

# skill-audit: skill 棚卸し

skill 数の増加に伴って `org-delegate` の work-skill 検索にノイズが増えるのを防ぐため、
定期的ではなく **状態ベース**で棚卸しを行う。

skill 数増加そのものよりも「検索面のノイズ」が本丸。
本スキルは 3 つの観点（廃止 / 重複統合 / owner 明記）を機械的にチェックし、
変更提案を窓口 Claude にまとめて送る。自動で skill を削除・変更することはしない。

## Step 1: 発火条件チェック（状態ベース）

以下をいずれも満たさない場合は **即終了**（ログも残さない）。

```bash
# 候補キュー pending エントリ数
cand_count=$(grep -c '^- \*\*status\*\*: pending' knowledge/skill-candidates.md 2>/dev/null || echo 0)

# work-skill 数（org-* は除外。ノイズ源となる work-skill 検索対象に合わせる）
work_skill_count=$(find .claude/skills -maxdepth 2 -name SKILL.md \
  | grep -v '/org-' | wc -l)
```

- `cand_count >= 5` **または** `work_skill_count >= 20` なら続行
- どちらも満たさなければ終了（このとき報告は不要）

数値の根拠: N=5 / M=20 をデフォルトとする。実運用で重くなれば PR で調整。
`org-*` を除外する理由: ノイズ源は `org-delegate` の work-skill 検索であり、
`org-*` の増減は検索ノイズに直接影響しない。

## Step 2: 廃止候補の洗い出し

各 skill について以下を評価する。**現時点で観測可能な項目のみ**を機械判定に使い、
観測不能な項目は「要確認」として人間判定に委ねる。詳細は `references/audit-checklist.md` を参照。

観測可能（機械判定に使える）:
- description と `SKILL.md` 本文 Step 群の内容に明らかな乖離がある（本文内で完結）
- `knowledge/curated/` / `knowledge/raw/` / `.state/workers/` を `{skill-name}` で grep し、
  直近 90 日の言及が 0 件（本プロジェクト内の観測範囲に限る）

観測不能（「要確認」扱い、廃止判定には使えない）:
- `org-delegate` は work-skill を指示に埋め込むだけで「実際に採用されたか」を永続化しない
  → 言及検索は「検索で引っかかった」程度の情報にしかならない
- 既存 skill の多くが `origin.task_id` を持たず、再利用判定の起点がない
  → origin 付き skill のみ「再利用なし」判定に使い、origin 無しは除外

**廃止決定はしない。提案リストに載せるだけ**で、最終判断は人間に委ねる。
audit-checklist.md の 1.1 / 1.2 / 1.3 も同方針で詳細化済み。

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

`renga-peers` の `send_message(to_id="secretary", ...)` で窓口 Claude に送る。

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

1. `org-curate` Step 6（skill 棚卸しの発火チェック）で閾値を満たせば呼び出される（推奨経路）
2. 窓口が `skill-candidates.md` を見て手動で起動する
3. 人間が「棚卸しして」と依頼

`/loop` などの時間ベース起動はしない。

## 自動変更を避ける理由

- 廃止の誤判断は `org-delegate` 側で「使える skill が無い」状態を生む（委譲精度の低下）
- 統合は description・triggers・手順の擦り合わせを要し、機械的にはできない
- owner 追記は人間確認が最も軽い運用（ここだけ自動化しても効果小）

したがって本スキルは**提案までに留め**、変更は人間承認を経て窓口が手動で行う。
