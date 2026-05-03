# claude-org 自身を編集するタスクの特例

> **前提（Pattern 判定）**: 対象ファイルが gitignored（例: `docs/internal/`, `notes/`, `tmp/` 配下の内部メモ）の場合は、SKILL.md **Step 0.7「ターゲットファイル gitignore 事前チェック」**により **Pattern C 強制**となる（Step 1 のディレクトリパターン判定基準には到達しない）。本ドキュメントの特例（hook 除外・`CLAUDE.local.md`）は Pattern B / C いずれでも適用するが、Pattern C の場合 worktree は作らないため WORKER_DIR は対象ファイルにアクセスできる既存リポジトリ root を指定する。以下は Pattern B（tracked ファイル編集）を主に想定した手順。

claude-org リポジトリのスキル / ドキュメント / 設定を編集するワーカーを派遣するとき、通常の worktree 準備のままでは以下の事故が発生する:

- `block-org-structure.sh` hook が `.claude/skills/` などへの Edit / Write を拒否する（`bypassPermissions` モードでも exit code 2 により確認プロンプトが出る）
- ルートの `CLAUDE.md` は Secretary（窓口）用の指示なので、ワーカーがこれを読んで「あなたは窓口」と誤認する

このため、claude-org 自己編集タスクでは **Step 1.5 のワーカーディレクトリ準備時に以下 3 点を通常手順に追加する**。

## 1. `claude-org-self-edit` ロールで settings.local.json を生成する

Phase 2 (Issue #99) 以降、ワーカー `.claude/settings.local.json` は `claude-org-runtime settings generate` で **schema-driven に生成**する（Phase 4 で in-tree `tools/generate_worker_settings.py` から PyPI パッケージに移行済み。手書き編集は窓口の `permissions.deny` で禁止されている）。claude-org 自己編集タスクでは `--role claude-org-self-edit` を指定すること:

```bash
claude-org-runtime settings generate \
  --role claude-org-self-edit \
  --worker-dir {worker_dir} \
  --claude-org-path {claude_org_path} \
  --out {worker_dir}/.claude/settings.local.json
```

`claude-org-self-edit` ロールは schema 上で `block-org-structure.sh` hook が **既に除外**された状態で定義されている（`Edit|Write` / `Bash` matcher 双方）。`check-worker-boundary.sh` / `block-git-push.sh` などその他の hook は通常どおり残る。生成済み JSON を手で再編集してはならない（drift CI が fail する。新パターンが必要なら `claude-org-runtime` リポジトリの `settings/role_configs_schema.json` の `worker_roles` に role を追加する PR を起こす）。

## 2. ワーカー指示は `CLAUDE.md` ではなく `CLAUDE.local.md` に書く

ルートの `CLAUDE.md` は Secretary 用の指示なので、ワーカー用 CLAUDE.md で上書きしてはならない（他ロールが壊れる）。
ワーカーへの指示は `{worker_dir}` 直下の `CLAUDE.local.md` に書く（git 管理外）。Pattern B（tracked ファイル編集）なら `{worker_dir}` は worktree 直下、Pattern C 強制（gitignored サブモード）なら `{worker_dir}` は対象ファイルにアクセスできる既存リポジトリ root を指す。

Claude Code は同一ディレクトリの `CLAUDE.md` と `CLAUDE.local.md` の両方を読み込むため、ワーカーには両方が見える。

### 通常手順の読み替え（重要）

claude-org 自己編集タスクでは、SKILL.md Step 1.5 および `worker-claude-template.md` / `instruction-template.md` が「CLAUDE.md を生成 / 配置 / 確認」と指示している箇所を、すべて **`CLAUDE.local.md` に読み替える**こと:

- Step 1.5 共通手順の「CLAUDE.md を生成する（テンプレートの変数を置換）」 → 生成先を `CLAUDE.local.md` にする。`worker-claude-template.md` の本文をそのままテンプレートとして流用してよい
- Step 1.5 共通手順（配置後）の「生成した CLAUDE.md に『作業ディレクトリ（最重要制約）』セクションが含まれていることを確認」 → 生成した `CLAUDE.local.md` を対象に確認する
- `instruction-template.md` の「詳細な行動規範は CLAUDE.md に記載されています」「CLAUDE.md に記載された絶対パス」 → 該当箇所を `CLAUDE.local.md` に書き換えてワーカーへ送信する
- 参考 work-skill セクション追加先も `CLAUDE.local.md` に向けること

ルートの `CLAUDE.md`（Secretary 指示）はいかなる場合も上書きしない。

## 3. `CLAUDE.local.md` 冒頭で「ルート CLAUDE.md は無視」を明示する

`CLAUDE.local.md` の最初に以下の趣旨を必ず書く:

> このワーカーは claude-org リポジトリ自身の `{worker_dir}`（Pattern B なら worktree 直下、Pattern C 強制なら repo root 直下）で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。あなたは窓口ではなくワーカーである。

この明示がないと、ワーカーがルート CLAUDE.md を先に読んで Secretary として振る舞い始める（/org-start の実行を促す等）。

## 根拠

`knowledge/curated/delegation.md` の「claude-org 自身を編集するワーカーは worktree 内の設定を事前に調整する」セクション参照。
