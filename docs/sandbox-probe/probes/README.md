# probes/

Issue #376 Pre-Phase 0 spike (probe-worker pattern, Issue #376 issuecomment-4410401705) の probe checklist 置き場。

## ファイル構成

- `checklist.md` — probe の 5 列 checklist (category / 試行コマンド / 期待される allow or deny / 観測結果 / 結論)。本 iteration の計画分。
- `categories.md` — 各 category が何をなぜ確認するかの背景メモ。audit-issue-376-2026-05-09.md (B0/B1/B2/B3) と Issue #376/#377/#378/#379/#380 への対応関係を残す。

## probe-worker pattern の運用ルール (本 spike 時点での合意)

1. **probe は read-only 検証 + 既知の安全な書込のみ** に限定する。実環境破壊は窓口/dispatcher で人手 review 後に。
2. **「期待される allow or deny」列を埋めてから走らせる**。実測結果が期待と乖離した場合のみ「結論」列で深掘りする。
3. probe は **role × pattern × profile** の 3 軸で展開しうる。1 iteration では軸を増やしすぎず、検証深度 minimal を維持する。
4. 結果は probes/runs/{YYYY-MM-DD}-{topic}.md 形式で別途追記（本 iteration では未作成、次 iteration の最初で作る前提）。
5. 実機実行は **本 spike のスコープ外**。本 iteration では「期待値」と「観察ポイント」の確定までで止める。

## 本 iteration の優先 probe 群

`checklist.md` の category 列を以下の順で埋めてある。最低限 audit が指摘した B1-1/B2-1 を最初に消化し、付随する dangerous-git surface・network egress・secret denyRead を 1 周する:

1. B1-1 — dispatcher の `bypassPermissions` × sandbox profile 発火
2. B2-1 — worker への repo-shared `.claude/settings.json` 配備状況
3. fs-cwd — worker の cwd 内/外 read/write
4. fs-pattern-b — Pattern B 想定の base repo Git metadata 操作
5. git-surface — history-rewriting push / hard reset / forced worktree 削除
6. network — network egress (curl, gh, cargo fetch)
7. secrets — `.env` / credential / `*.pem` / `~/.config/gh/hosts.yml` denyRead

## 用語

- **allow** = sandbox or hook どちらか以上の防御層が通す状態
- **deny** = sandbox or hook いずれかが拒否し、Claude Code の Tool 結果としてエラーが返る
- **silent** = エラーは出ないが副作用が抑止 (例: sandbox が fail-open している, hook が exit 0)
- **observed**列 = 本 iteration では「未実測」と書く。実機 probe iteration が走り次第埋める。
