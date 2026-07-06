# Changelog

本プロジェクト (claude-org-ja) の注目すべき変更をこのファイルに記録する。

書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
本プロジェクトは [セマンティック バージョニング](https://semver.org/lang/ja/) に従う。

## [Unreleased]

## [1.0.0] - 2026-07-06

v0.1.0 (2026-04-30) 公開以降の 301 コミットを集約した最初の安定版リリース。
この期間の主要な達成は次の 5 点である。(1) 通信基盤を renga から org-broker へ移行して既定化、
(2) 組織状態を Markdown から SQLite (state DB) へ移管、(3) role × dispatch pattern に基づく
sandbox / ワーカー Git ガードレールの整備、(4) 横断 work-discovery (Issue triage) の導入、
(5) attention 通知層の構築。

### Added

- **org-broker トランスポート層と生成基盤** (Epic #586 / #515 / #6): renga に依存しない
  transport-neutral なスキル source と生成器 (generator) を新設し、標準スキル 11 件と surgical
  スキル 4 件を source 化。broker 面 (pane) 生成、`ORG_TRANSPORT` フラグ、runtime transport
  descriptor 駆動の生成器、Broker auth & delivery 契約 (Surface 8) の批准、broker dogfood 運用
  runbook を追加。配信モデルを push-first へ移行した。
- **state DB (SQLite) 基盤** (Issue #267): `journal.jsonl` ベースの状態管理を SQLite `state.db`
  へ移行。M0 スキーマ + rebuild importer、M1 read 切替 (dashboard / org-start / org-resume が
  DB を参照)、M2 write API、M2.1 cutover (StateWriter + post-commit hook)、M3 移行ツール、
  M4 markdown freeze (自由記述を `notes/` へ抽出し `journal.jsonl` を廃止) を段階的に実施。
  snapshot 再生成とワーカー状態アーカイブを StateWriter の post-commit hook に統合した。
- **横断 work-discovery / Issue triage** (Issue #520 / #528 / #529): read-only スキャンによる
  triage compute layer、`/work-discovery` スキル (手動 triage エントリ)、ワーカークローズ契機の
  triage 配線、クロスリポジトリ横断依存解決とランク付け、過去マージ PR の実工数から repo を
  較正し相関ゲートで上書き判定する effort 学習フレームワークを追加。
- **attention 通知層** (#28 / #26 / #444): attention watcher の ja 配布 (config / docs / README /
  org-start ガイダンス)、secretary の 3 停止ゲートでの `awaiting_user` emit、`secretary_awaiting_user`
  種別、severity デモートと TTL ladder、WSL/Windows backend の実態記述と ja テンプレートを追加。
- **dispatcher 監視・自己修復** (#295 / #296 / #298 / #382 / #464 / #619): `STALL_SUSPECTED` /
  `SECRETARY_RELAY_GAP_SUSPECTED` 検知、retro 完了報告ポーリングの `dispatcher_retro_gate.py`
  抽出、stale queued run と DB/worker-file drift の検知、peer-msg なしワーカー出力 (silent
  dead-lock) の検知、broker/tmux で control plane を常時可視化する read-only 自己修復ビュー、
  handover/resume プロトコルを追加。
- **on-demand curator** (#503): ワーカークローズ時の threshold-triggered spawn を導入し、
  常駐 `/loop 30m` curator を退役。
- **スキル群**: `org-delegate` をフォーカスされたスキル群へ carve し、`/org-conveyor`
  (approved-scope 完了駆動ループ)、`/pr-watch-pane` (broker tmux 面で pr-watch)、`/org-attach`
  (組織ペインへの read-only attach コマンド生成)、ワーカーへのスキル昇格委譲、transport-neutral
  skill generator を追加。
- **secretary 自動化** (#288 / #302 / #303): DELEGATE 経路の end-to-end スクリプト化
  (`resolve_worker_layout` + `gen_worker_brief` + `gen_delegate_payload`)、pending-decisions
  レジスタと `user_replied_at` マーカーによる relay-gap 検知、ワーカー承認要求の人間への
  エスカレーション、非保護ブランチへの `--force-with-lease` 許可、handover/resume スキルを追加。
- **sandbox / ワーカー Git ガードレール** (Phase 0/2, #377 / #378 / #379): role × dispatch pattern
  に基づく sandbox filesystem 契約と `sandbox_by_pattern` body、Phase 2 ワーカー Git ガードレール
  (hook attach + Layer 2 deny family + スクリプト拡張)、bwrap consumer protocol の sandbox
  launcher 契約、前景 (同期) subagent 起動を一律ブロックする PreToolUse フックを追加。
- **契約文書** (Contract Sets A–F): Role Contract (A)、Delegation Lifecycle (B)、State Schema (C)、
  Backend Interface (D)、Knowledge & Curation Boundaries (E)、canonical state semantics (Set F)、
  および Broker auth & delivery (Surface 8) を批准。
- **CLI / tooling**: チーム導入向け実績レポート CLI (`tools/org_metrics_report.py`)、ワーカー brief
  生成器 (CLAUDE.md / CLAUDE.local.md)、runtime updater (`tools/update_runtime.py`)、CI 監視用
  `pr_watch` ヘルパー、`state_migrate.py` 中央移行エントリポイントを追加。
- **PR / マージ自動化** (org-pull-request): PR マージ時の run 自動完了、PR open 時の `runs.pr_url`
  back-fill (MergeWatch)、pr-watch の Secretary 通知 (renga-peers)、CI 完了検知の events DB
  poll 主導化を追加。

### Changed

- 起動の主経路を broker (`claude-org-runtime org up`) へ刷新し、renga を切り戻しフォールバック
  (opt-in) に集約。renga-decoupling を再導出設計へ追従させ renga-free 完全移行を完了した。
- core-harness の抽出 (Phase 3 / Layer 1) と claude-org-runtime の採用。in-tree の `tools/` を
  claude-org-runtime へ委譲し、permission/audit primitives を core-harness の shim 経由に切替。
- claude-org-runtime の pin 下限を継続的に bump (0.1.1 → 最終 `>=0.1.36,<0.2`)。各 runtime
  リリースの paired 同期 (broker delivery / herdr placement / send_keys raw-key 等) を追従。
- packaging を `pyproject.toml` canonical へ移行 (Phase 5c)。`requirements.txt` は thin pointer
  として維持し、両者の pin 同期を drift CI で厳格化。
- README を公開向けに全面リビルド (簡潔な LP 化、起動コマンドを `org up` へ、課金中立 / Loop
  Engineering / 判断供給 の 3 柱を前面化、prerequisites テーブルと用語集、ペインレイアウト図)。
- ワーカーモデル規約を「既定 opus・軽量機械的タスクは Sonnet 5 許可」に改訂し、タスクルーティングを
  2 レーン制 (軽量 subagent レーン + 重量 ultracode レーン) に明文化。キュレーターの spawn モデルを
  sonnet に切替。
- Codex 差分セルフレビューの SoT を codex review surface (Method A) へ切替。ワーカー brief に
  Codex round 上限 (既定 3) と上限超え時の判断ガイドラインを焼き込み。
- 組織状態の主管を Markdown から DB へ移し、`journal.jsonl` を廃止 (state-db M4)。
- 「1 worker = 1 task = 1 scope」を規約として明文化。

### Fixed

- dispatcher stall 検知の false positive を抑止 (全可視行ハッシュ / active-spinner suppress /
  完了報告済みワーカーの正常 idle 除外 / escalation 誤分類の decision register 参照修正)。
- Windows / WSL2 / cp932 対応: install.sh の WSL2/Ubuntu PEP 668 と node/npm 前提対応、
  work-discovery スキャンの ASCII 安全化と cp932 `UnicodeDecodeError` 連鎖の解消、pr-watch の
  Windows 堅牢化、ワーカー brief の Windows Python 起動コマンド指定の統一。
- テスト実行時のライブ broker/renga 送信漏れを、全 transport サニタイズで構造的に遮断。
- `peer_notify` / `notify_peer` の transport-neutral 化と、broker send への `ORG_BROKER_STATE_DIR`
  配線。
- retro-gate の ack 判定 edge-case (丁寧否定「ございません」/ 疑問終端限定 / 「マージ済み・完了」
  パターンの追加)。
- pr_watch の CI 完了判定を final verdict のみ `ci_completed` + bounded retry に是正し、gh probe
  取得失敗を indeterminate に分離してリトライ backoff を追加。
- cross-repo の `closingIssuesReferences` を `(repo, number)` で正しく join。
- Pattern A/B/C の worker_dir 統一、worktree 生成・削除の統一、self-edit boilerplate 誤適用の
  解消、cleanup 順序の非依存化。
- installer の jq fail-close、renga (および node/npm) を required から optional へデモート。

### Security

- pre-commit secret スキャナ (Issue #69) と、`--no-verify` / `HUSKY=0` / `SKIP_SECRET_SCAN=1` /
  `git -c core.hooksPath` 等の verify-bypass 経路を構造的に遮断するフックを整備。
- ワーカーからの `git push` および破壊的 git 操作 (`reset --hard` / `branch -D` / `clean -f` /
  `tag -d` / `update-ref -d` 等) をブロック。
- 共有 `settings.json` からの personal-path leak を停止し、registry のローカルパスを redaction。
  sandbox の `denyRead` に credential セットを設定。

[Unreleased]: https://github.com/suisya-systems/claude-org-ja/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/suisya-systems/claude-org-ja/releases/tag/v1.0.0
