# aainc-ops 自身を編集するタスクの特例

aainc-ops リポジトリのスキル / ドキュメント / 設定を編集するワーカーを派遣するとき、通常の worktree 準備のままでは以下の事故が発生する:

- `block-aainc-structure.sh` hook が `.claude/skills/` などへの Edit / Write を拒否する（`bypassPermissions` モードでも exit code 2 により確認プロンプトが出る）
- ルートの `CLAUDE.md` は Secretary（窓口）用の指示なので、ワーカーがこれを読んで「あなたは窓口」と誤認する

このため、aainc-ops 自己編集タスクでは **Step 1.5 のワーカーディレクトリ準備時に以下 3 点を通常手順に追加する**。

## 1. `block-aainc-structure.sh` hook を worktree の settings.local.json から除外する

worktree 直下の `.claude/settings.local.json` を配置する際、`hooks.PreToolUse` から `block-aainc-structure.sh` エントリを **除外**すること。`Edit|Write` matcher と `Bash` matcher の両方で除外する必要がある。

他の hook（例: `block-git-push.sh`, `block-workers-delete.sh`, `check-worker-boundary.sh` 等）は通常どおり残してよい。除外対象はあくまで aainc 構造のブロック hook のみ。

> Issue #11 の恒久対応（hook が aainc-ops worktree を自動検出して許可する）が merge されれば、この手順 1 は不要になる。手順 2・3 は恒久対応後も残す運用とする。

## 2. ワーカー指示は `CLAUDE.md` ではなく `CLAUDE.local.md` に書く

ルートの `CLAUDE.md` は Secretary 用の指示なので、ワーカー用 CLAUDE.md で上書きしてはならない（他ロールが壊れる）。
ワーカーへの指示は worktree 直下の `CLAUDE.local.md` に書く（git 管理外）。

Claude Code は同一ディレクトリの `CLAUDE.md` と `CLAUDE.local.md` の両方を読み込むため、ワーカーには両方が見える。

### 通常手順の読み替え（重要）

aainc-ops 自己編集タスクでは、SKILL.md Step 1.5 および `worker-claude-template.md` / `instruction-template.md` が「CLAUDE.md を生成 / 配置 / 確認」と指示している箇所を、すべて **`CLAUDE.local.md` に読み替える**こと:

- Step 1.5 共通手順の「CLAUDE.md を生成する（テンプレートの変数を置換）」 → 生成先を `CLAUDE.local.md` にする。`worker-claude-template.md` の本文をそのままテンプレートとして流用してよい
- Step 1.5 共通手順（配置後）の「生成した CLAUDE.md に『作業ディレクトリ（最重要制約）』セクションが含まれていることを確認」 → 生成した `CLAUDE.local.md` を対象に確認する
- `instruction-template.md` の「詳細な行動規範は CLAUDE.md に記載されています」「CLAUDE.md に記載された絶対パス」 → 該当箇所を `CLAUDE.local.md` に書き換えてワーカーへ送信する
- 参考 work-skill セクション追加先も `CLAUDE.local.md` に向けること

ルートの `CLAUDE.md`（Secretary 指示）はいかなる場合も上書きしない。

## 3. `CLAUDE.local.md` 冒頭で「ルート CLAUDE.md は無視」を明示する

`CLAUDE.local.md` の最初に以下の趣旨を必ず書く:

> このワーカーは aainc-ops リポジトリ自身の worktree で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。あなたは窓口ではなくワーカーである。

この明示がないと、ワーカーがルート CLAUDE.md を先に読んで Secretary として振る舞い始める（/org-start の実行を促す等）。

## 根拠

`knowledge/curated/delegation.md` の「aainc-ops 自身を編集するワーカーは worktree 内の設定を事前に調整する」セクション参照。
