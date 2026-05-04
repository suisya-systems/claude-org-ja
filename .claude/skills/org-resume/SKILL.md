---
name: org-resume
description: >
  中断された組織を再開する。.state/org-state.md が存在し Status: SUSPENDED のとき、
  「再開」「続きから」「前回どこまでやった？」と言われたときに使う。
  起動時の自動ブリーフィングにも対応。
---

# org-resume: 組織の再開

中断された組織の状態を読み込み、人間にブリーフィングし、再開する。

> **state DB 前提 (Issue #267 / M2.1, #272)**: `.state/state.db` が SoT。read 経路は
> DB primary + markdown fallback、構造化セクションの write 経路は `StateWriter.transaction()`
> 経由（post-commit hook が `.state/org-state.md` / `.state/journal.jsonl` を自動再生成）。
> DB が古い場合は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で再構築する。

## Phase 1: 状態読み込みとブリーフィング

0. **state schema migration を実行する**（Set C §4.4 契約）。`.state/` 配下の JSON 状態を最新スキーマへ揃えてから読み込みに入る:

   ```bash
   py -3 tools/state_migrate.py    # Windows
   python3 tools/state_migrate.py   # Mac/Linux
   ```

   exit 0 なら続行。exit 1（未対応 version 残存）/ exit 2（migration ループ異常）なら人間に報告して停止する。
1. **DB primary で前回状態を取得する**:
   - `.state/state.db` が存在し、新しい (= `.state/state.db` の mtime が
     `.state/org-state.md` / `.state/journal.jsonl` / `registry/projects.md`
     の最新 mtime 以上。WAL 利用時は `.state/state.db-wal` の mtime も含めて max を取る) → DB をクエリ:
     ```bash
     python -c "from tools.state_db import connect; from tools.state_db.queries import get_resume_briefing; import json; \
       conn = connect('.state/state.db'); \
       print(json.dumps(get_resume_briefing(conn), ensure_ascii=False, indent=2, default=str))"
     ```
     `active_runs` / `recent_events` / `last_suspend_at` でブリーフィング素材を作る
   - DB が上記いずれかの markdown SoT ファイルの mtime より古い（stale）→ 人間に rebuild を促す:
     「state.db is stale (markdown is newer). Run: `python -m tools.state_db.importer --db .state/state.db --root . --rebuild`」
     必要なら markdown fallback で続行
   - DB が無い → markdown fallback
2. `.state/org-state.md` も読む（Status / Current Objective / Resume Instructions は DB に未収録のため markdown が SoT）
3. 人間に簡潔なサマリーを提示する:
   - 全体の目標（markdown）
   - 各作業アイテムの状態（完了/進行中/保留/ブロック、DB の active_runs + 補完）
   - 中断時刻（DB の `last_suspend_at` または markdown の Updated）
4. `.state/journal.jsonl` も DB の `recent_events` で補えるが、生 JSONL を確認する場合は org-state.md の Updated 以降を見る

## Phase 2: 現実との照合

DB の active_runs を SoT として照合し、markdown は表示用補助とする。

1. DB から取得した active_runs（または markdown の Worker Directory Registry）を起点に、各 run の `worker_dir` を確認する
2. `.state/workers/` 内の各ワーカー状態ファイルも参照する（旧経路）
3. 各ワーカーの作業ディレクトリで以下を確認:
   - ディレクトリが存在するか
   - `git status` — 未コミットの変更があるか
   - `git log --oneline -5` — 最後のコミットは run の `commit_short` / 状態ファイルと一致するか
   - ブランチは run の `branch` / 状態ファイルの記述と一致するか
4. `knowledge/raw/` に未整理のファイルがあるか確認
5. 差異があれば人間に報告する（例: 「DB ではOAuth run が in_use ですが、ディレクトリが存在しません」）

## Phase 3: 再開計画の提案

状態に応じて提案を分ける:

- **COMPLETED**: 結果を報告するのみ
- **IN_PROGRESS（中断済み）**: 「未コミットの変更があります。ワーカーを派遣して続行しますか？」
- **PENDING**: ブロッカーの状態を確認し、実行可能か判断
- **BLOCKED**: ブロッカーの解消状況を確認

**重要: 人間の確認を待ってから行動すること。勝手にワーカーを派遣しない。**

## Phase 4: 組織の再構築

人間が承認した作業について:

1. `/org-delegate` スキルでワーカーを派遣
2. 新ワーカーには前回のワーカー状態ファイル（`.state/workers/worker-{id}.md`）の内容をコンテキストとして渡す
3. **DB に Status / Resumed を書く**（`StateWriter.transaction()` 経由。post-commit hook が `.state/org-state.md` の Status 行を `ACTIVE` に更新し `.state/journal.jsonl` も再生成する。regen 失敗時も DB は確定済みで stderr 警告のみ）:

   ```bash
   python -c "
   from datetime import datetime, timezone
   from pathlib import Path
   from tools.state_db import connect
   from tools.state_db.writer import StateWriter
   ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%fZ')
   conn = connect('.state/state.db')
   with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
       w.update_session(status='ACTIVE', resumed_at=ts, updated_at=ts)
   "
   ```
4. JSON スナップショットを再生成する（dashboard 用。state-db cutover とは別経路）:

   ```bash
   py -3 dashboard/org_state_converter.py    # Windows
   python3 dashboard/org_state_converter.py   # Mac/Linux
   ```

5. ディスパッチャー・キュレーターペインの起動は /org-start が担当するため、ここでは行わない
6. `journal.jsonl` に resume イベントを追記（DB 経由。`tools/journal_append.py` は M2 で DB ルーティング済み。`ts` は自動付与）:
   ```bash
   py -3 tools/journal_append.py resume \
       --json '{"resumed_items": ["blog-redesign", "data-analysis"]}'
   ```
   event 名と payload key の規約は [`docs/journal-events.md`](../../../docs/journal-events.md) を参照。
