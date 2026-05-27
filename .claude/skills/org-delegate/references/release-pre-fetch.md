# release-class タスクの pre-fetch（窓口が実行）

> **一次参照元**: [`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) Step 0.6（トリガー判定のみ）。本ドキュメントは適用条件・実行コマンド・背景経緯の詳細 SoT。

`release/*` ブランチを切るタスクは worker が **対象プロジェクトの最新 `main`** から branch することを前提とする。Phase 2 worker git guardrails 以降、worker 側 `.claude/settings.json` の `permissions.deny` に `Bash(git fetch)` / `Bash(git pull)` / `Bash(git remote update)` が含まれており、ローカル `origin/main` が古いまま worker をディスパッチすると着手 5 分以内に「git fetch deny」BLOCKER が発火し、窓口往復で 10 分以上のロスになる（claude-org-runtime v0.1.10 事例）。

このため **release-class タスクに限り**、`gen_delegate_payload.py preview` / `apply` の前に窓口側で fetch を代行する:

```bash
# 対象プロジェクト（release を切るリポジトリ）のローカルルート
cd <target project root>

# 最新 origin/main を取り込んで local main を ff 更新
git fetch origin
git pull --ff-only origin main
```

## 適用条件

以下のいずれかに該当する場合のみ発動:

- task description / commit-prefix / planned branch に `release`, `release/`, `vX.Y.Z` 等のリリース昇格を示す語が含まれる
- 対象ファイルに `CHANGELOG.md` の昇格、`__about__.__version__` / `pyproject.toml` の `version` bump 等のリリース昇格作業が含まれる
- task_id に `release` を含む（例: `runtime-0-1-10-release`）

通常の feature / fix / docs タスクでは実行しない。worker permissions deny は「worker は本流履歴を引き寄せず sandbox 内で完結する」意図的設計であり、release だけが「最新 main からの branch」を必須とする例外フローである。

## Issue #480 との関係（Pattern B は apply が自動 fetch するため重複しない）

Issue #480 以降、**Pattern B**（並走 run がベースクローンを占有しているため worktree を切るケース）の **新規 worktree 作成** は `gen_delegate_payload.py apply`（内部の `_ensure_worktree`）が **`git worktree add` の直前に自動で `git fetch origin` を実行し、最新の `origin/HEAD`（= `origin/main`）から分岐する**。この fetch は **fail-closed**: `origin` remote が設定済みで fetch に失敗した場合は apply が `WorktreeApplyError` で abort する（stale な `origin/main` 起点で worktree を切ることはない／DB 予約前に abort するので queued 行も残さない）。

スコープと例外（実装どおりの正確な表現）:

- **新規作成時** かつ `origin` remote 設定済みなら、worktree 起点は最新 trunk になる（fail-closed で保証）。
- `origin` remote が未設定のローカル専用ベースは fetch をスキップする（取り込む remote が無いため）。
- **既存登録済み worktree の再利用パス**（Issue #309 の partial-retry）は fetch しない。既にコミット済みの branch tip は fetch では進められず、reset は worker の作業を破壊し得るため。stale な再利用 worktree を最新化したい場合は窓口が当該 worktree を削除し、apply に再作成させる（再作成は上記のとおり fetch を通る）。

したがって **新規作成パスでは Step 0.6 の pre-fetch を実行していなくても worktree の起点は最新 trunk** になり、Step 0.6 の「最新 main から branch させる」目的とは重複する（二重に実行しても害はない: fetch が冪等なだけ）。

一方 Step 0.6 は引き続き必須である。理由は対象が **Pattern A**（並走 run が無く worker がベースクローン自体で直接 `release/*` を local main から切るケース）だから:

- Pattern A の worker はベースクローン内の **local `main`** から branch する。worker 側 permissions が `git fetch` / `git pull` を deny しているため、ローカル main が古いままだと着手直後に BLOCKER になる。apply の自動 fetch は Pattern B の worktree 起点（`origin/main` remote-tracking ref）を更新するだけで、Pattern A が使う **local main ブランチ自体** は ff 更新しない。
- よって「local main を最新へ ff する」窓口側 pre-fetch（`git pull --ff-only origin main`）は Pattern A release タスクで依然必要。

要約: **worktree 起点の鮮度は Issue #480 が apply 内で担保（Pattern B）。local main の鮮度は Step 0.6 が窓口側で担保（Pattern A release）。** 両者は対象が異なり、片方が他方を不要にはしない。

## 背景・経緯

詳細な経緯（worker 5 分以内 BLOCKER → 10 分追加ロスの実測、4 つの対応選択肢の比較、permissions 側根本原因）は [`knowledge/curated/release-process.md`](../../../../knowledge/curated/release-process.md) の「release ブランチ作成時は窓口側で `git fetch` を代行する」節を参照。
