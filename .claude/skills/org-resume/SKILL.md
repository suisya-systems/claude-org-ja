---
name: org-resume
description: >
  中断された組織を再開する。.state/org-state.md が存在し Status: SUSPENDED のとき、
  「再開」「続きから」「前回どこまでやった？」と言われたときに使う。
  起動時の自動ブリーフィングにも対応。
effort: low
allowed-tools:
  - Read
  - Bash(git status)
  - Bash(git log:*)
  - Bash(python3 tools/state_migrate.py)
  - Bash(python3 -c:*)
  - Bash(python3 dashboard/org_state_converter.py)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(python3 tools/journal_append.py:*)
  - Bash(python3 -m tools.state_db.importer:*)
  - mcp__renga-peers__list_panes
  - mcp__org-broker__list_panes
---

# org-resume: 組織の再開

中断された組織の状態を読み込み、人間にブリーフィングし、再開する。

> **state DB 前提**: `.state/state.db` が唯一の SoT。読み取りは DB のみ。構造化セクションの
> 書き込みは `StateWriter.transaction()` 経由（post-commit hook が `.state/org-state.md` を
> DB から自動再生成する。markdown の直接編集は禁止で、drift_check が検出する）。
> free-form な学び / Pending Lead 等は `notes/` 配下に保存する。
> DB が無い場合は `python3 -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で構築する。

## Phase 1: 状態読み込みとブリーフィング

0. **state schema migration を実行する**。`.state/` 配下の JSON 状態を最新スキーマへ揃えてから読み込みに入る:

   ```bash
   python3 tools/state_migrate.py
   ```

   exit 0 なら続行。exit 1（未対応 version 残存）/ exit 2（migration ループ異常）なら人間に報告して停止する。
1. **DB から前回状態を取得する**（軽量 briefing API）:
   - `.state/state.db` が存在 → DB をクエリ:
     ```bash
     python3 -c "from tools.state_db import connect; from tools.state_db.queries import get_resume_briefing_light; import json; \
       conn = connect('.state/state.db'); \
       print(json.dumps(get_resume_briefing_light(conn), ensure_ascii=False, indent=2, default=str))"
     ```
     `active_runs` / `reserved_runs` / `recent_events` / `last_suspend_summary` でブリーフィング素材を作る。
     `session` には Status / Current Objective / suspended_at / resumed_at / resume_summary が圧縮されて入る（raw `resume_instructions` 本文と raw event `payload_json` は含まれない）。
     `active_inventory_dirs` は `runs.status` から逆引きした worker_dir 集合（state-semantics-contract I7 — `worker_dirs.lifecycle='active'` ではない）。
     必要があれば（極稀ケース）`get_resume_briefing` で raw payload / 完全な resume_instructions を取得できるが、起動時 Phase 1 では使わない。
   - DB が無い → 人間に rebuild を促す:
     「state.db not found. Run: `python3 -m tools.state_db.importer --db .state/state.db --root . --rebuild`」
2. 人間に簡潔なサマリーを提示する:
   - 全体の目標 (briefing の `session.objective`)
   - 各作業アイテムの状態 (完了/進行中/保留/ブロック、briefing の `active_runs` / `reserved_runs`)
   - 中断時刻 (briefing の `last_suspend_summary.occurred_at`、reason / counts も同 dict)
3. `notes/` 配下にある自由記述 (学び / Pending Lead / セッションサマリー) を確認する

## Phase 2: 現実との照合

DB の active_runs を SoT として照合し、markdown は表示用補助とする。

1. DB から取得した active_runs（または markdown の Worker Directory Registry）を起点に、各 run の `worker_dir` を確認する
2. `.state/workers/` 内の各ワーカー状態ファイルも参照する
3. 各ワーカーの作業ディレクトリで以下を確認:
   - ディレクトリが存在するか
   - `git status` — 未コミットの変更があるか
   - `git log --oneline -5` — 最後のコミットは run の `commit_short` / 状態ファイルと一致するか
   - ブランチは run の `branch` / 状態ファイルの記述と一致するか
4. `knowledge/raw/` に未整理のファイルがあるか確認
5. **保留された振り返り（`retro_deferred`）を拾う**。ディスパッチャーは窓口に届かず振り返りを終えられなかったワーカーについて `retro_deferred` を記録し、ペインを閉じずに残す（[`.dispatcher/references/pane-close.md`](../../../.dispatcher/references/pane-close.md)「secretary unreachable 時の fallback」）。記録を、前回の `resume` より後かどうかの印付きで取り出す:
   ```bash
   python3 -c "from tools.state_db import connect; \
     conn = connect('.state/state.db'); \
     last = conn.execute(\"SELECT COALESCE(MAX(id), 0) FROM events WHERE kind = 'resume'\").fetchone()[0]; \
     [print(tuple(r)) for r in conn.execute(\"SELECT occurred_at, json_extract(payload_json, '$.worker'), json_extract(payload_json, '$.reason'), id > ? AS since_last_resume FROM events WHERE kind = 'retro_deferred' ORDER BY id\", (last,))]"
   ```
   ワーカーごとに、ペイン一覧（`list_panes`）と突き合わせて分ける:
   - ペインが残っている → 振り返りは未完了（終われば閉じられている）。記録の時期を問わず再試行の対象
   - ペインが無く、`since_last_resume` が 1 → 前回の再開以降に材料を失った。人間に報告する
   - ペインが無く、`since_last_resume` が 0 → 前回までに処理済みとして扱う

   対象があれば一覧を人間に示す。再試行は Phase 4-5 で行う。
6. 差異があれば人間に報告する（例: 「DB ではOAuth run が in_use ですが、ディレクトリが存在しません」）

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
3. **DB に Status / Resumed を書く**（`StateWriter.transaction()` 経由。post-commit hook が `.state/org-state.md` の Status 行を `ACTIVE` に更新する。regen 失敗時も DB は確定済みで stderr 警告のみ）:

   ```bash
   python3 -c "
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
   python3 dashboard/org_state_converter.py
   ```

5. ディスパッチャーの起動は /org-start が担当するため、ここでは行わない（キュレーターは常駐せず、ディスパッチャーが CLOSE_PANE の閾値超過時にオンデマンド起動する）。ディスパッチャーが ready になったら、Phase 2-5 で拾った `retro_deferred` のワーカーごとに、人間の承認を得て再試行する:
   - ペインが残っている → ディスパッチャーへ `CLOSE_PANE: {pane_id}` を再送する。ディスパッチャーは振り返りから CLOSE_PANE フローをやり直す
   - ペインが無い（suspend で閉じた等）→ 振り返りの材料が失われている旨を人間に報告し、`.state/workers/worker-{task_id}.md` だけで振り返るか見送るかを聞く
6. resume イベントを DB に追記する（`ts` は自動付与）:
   ```bash
   python3 tools/journal_append.py resume \
       --json '{"restored_workers": ["worker-blog-redesign", "worker-data-analysis"], "note": "<1 行の要約>"}'
   ```
   event 名と payload key の規約は [`docs/journal-events.md`](../../../docs/journal-events.md) を参照。
