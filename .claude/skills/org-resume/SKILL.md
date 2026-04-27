---
name: org-resume
description: >
  中断された組織を再開する。.state/org-state.md が存在し Status: SUSPENDED のとき、
  「再開」「続きから」「前回どこまでやった？」と言われたときに使う。
  起動時の自動ブリーフィングにも対応。
---

# org-resume: 組織の再開

中断された組織の状態を読み込み、人間にブリーフィングし、再開する。

## Phase 1: 状態読み込みとブリーフィング

1. `.state/org-state.md` を読む
2. 人間に簡潔なサマリーを提示する:
   - 全体の目標
   - 各作業アイテムの状態（完了/進行中/保留/ブロック）
   - 中断時刻
3. `.state/journal.jsonl` が存在すれば、org-state.md の Updated 以降のエントリを確認し、
   スナップショット後に起きたイベントがあれば補足する

## Phase 2: 現実との照合

各作業アイテムについて、実際のファイルシステムの状態を確認する:

1. `.state/workers/` 内の各ワーカー状態ファイルを読む
2. 各ワーカーの作業ディレクトリで以下を確認:
   - ディレクトリが存在するか
   - `git status` — 未コミットの変更があるか
   - `git log --oneline -5` — 最後のコミットは状態ファイルと一致するか
   - ブランチは状態ファイルの記述と一致するか
3. `knowledge/raw/` に未整理のファイルがあるか確認
4. 差異があれば人間に報告する（例: 「状態ファイルではOAuth 60%完了とありますが、実際にはファイルが存在しません」）

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
3. `org-state.md` の Status を `ACTIVE` に更新
4. JSON スナップショットを再生成する:

   ```bash
   py -3 dashboard/org_state_converter.py    # Windows
   python3 dashboard/org_state_converter.py   # Mac/Linux
   ```

5. ディスパッチャー・キュレーターペインの起動は /org-start が担当するため、ここでは行わない
6. `journal.jsonl` に resume イベントを追記:
   ```json
   {"ts":"<ISO timestamp>","event":"resume","resumed_items":["blog-redesign","data-analysis"]}
   ```
