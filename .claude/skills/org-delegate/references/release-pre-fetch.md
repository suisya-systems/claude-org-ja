# release-class タスクの pre-fetch（窓口が実行）

> **一次参照元**: [`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) Step 0.6。適用条件（3 条件）の正本は SKILL.md 側で、本ドキュメントは実行コマンドと Pattern ごとの適用範囲を書く。

`release/*` ブランチを切るタスクは、worker が **対象プロジェクトの最新 `main`** から branch することを前提とする。worker は素の `git fetch` はできるが、`git pull`（local main の fast-forward）と `git -C <path> fetch` は deny されている（[`tools/org_extension_schema.json`](../../../../tools/org_extension_schema.json) の `worker_roles.default.permissions` の allow / deny）。local main が古いまま派遣すると、worker は local main を進められず着手直後に BLOCKER を出し、窓口との往復でロスが出る。

このため **release-class タスクに限り**、`gen_delegate_payload.py preview` / `apply` の前に窓口側で local main を進める:

```bash
# 対象プロジェクト（release を切るリポジトリ）のローカルルート
cd <target project root>

# 最新 origin/main を取り込んで local main を ff 更新
git fetch origin
git pull --ff-only origin main
```

`--ff-only` が失敗した（local main が origin/main から分岐している）ときは派遣せず、人間に報告して判断を仰ぐ。

通常の feature / fix / docs タスクでは実行しない。

## Pattern ごとの適用範囲

- **Pattern B**（並走 run がベースクローンを占有しているため worktree を切るケース）の**新規 worktree 作成**は、`gen_delegate_payload.py apply`（内部の `_ensure_worktree`）が `git worktree add` の直前に `git fetch origin` を実行し、最新の `origin/HEAD` から分岐する。この fetch は fail-closed で、`origin` が設定済みで fetch に失敗すると apply は `WorktreeApplyError` で止まる（DB 予約前に止まるので queued 行も残らない）。
  - `origin` remote が未設定のローカル専用ベースは fetch をスキップする。
  - **既存登録済み worktree の再利用パス**（partial-retry）は fetch しない。コミット済みの branch tip は fetch では進まず、reset は worker の作業を壊しうるため。stale な再利用 worktree を最新化したいときは、窓口がその worktree を削除し、apply に作り直させる。
- **Pattern A**（worker がベースクローン自体で local `main` から `release/*` を切るケース）は、apply の自動 fetch では **local `main` ブランチ自体**が進まない（更新されるのは remote-tracking ref だけ）。上の窓口側 pre-fetch が必要なのはこのケース。

Pattern B の新規作成で Step 0.6 を重ねて実行しても害は無い（fetch が冪等なだけ）。

## 背景・経緯

[`.claude/skills/org-delegate/references/rationale.md`](rationale.md) §10。
