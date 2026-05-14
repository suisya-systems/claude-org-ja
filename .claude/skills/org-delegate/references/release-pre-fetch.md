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

## 背景・経緯

詳細な経緯（worker 5 分以内 BLOCKER → 10 分追加ロスの実測、4 つの対応選択肢の比較、permissions 側根本原因）は [`knowledge/curated/release-process.md`](../../../../knowledge/curated/release-process.md) の「release ブランチ作成時は窓口側で `git fetch` を代行する」節を参照。
