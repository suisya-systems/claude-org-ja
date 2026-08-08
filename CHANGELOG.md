# Changelog

本プロジェクト (claude-org-ja) の注目すべき変更をこのファイルに記録する。

書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
本プロジェクトは [セマンティック バージョニング](https://semver.org/lang/ja/) に従う。

## [Unreleased]

### Added

- attention watcher の `duplicate_sidecar` kind に ja 側を追随 (#868)。
  `tools/templates/attention.example.json` に `notify.duplicate_sidecar: "urgent"` と日本語文面を追加した
  (未追加だとこの kind だけ runtime 中立の英語 default が出る)。この通知は「同じ owner 宛のメッセージを
  2 つの channel sidecar が取り合っている」状態を指し、放置すると報告が読まれない側のセッションへ配送されて
  沈黙する。runtime 側は自力で復旧できず、余分なセッションを終了できるのは人間だけなので severity は
  `urgent` で、文面もその行動 (余分なセッションを探して終了する) が読み取れる形にした。
  `docs/operations/attention-watch.md` には severity 表の 1 行に加えて §4.3 を新設し、この kind だけが
  `.state/state.db` ではなく org-broker の journal (`<state-dir>/broker/queue.jsonl`) を入力にすること・
  `--broker-state-dir` (既定 `<state-dir>/broker`、非既定 state dir の daemon でのみ明示が要る)・
  `duplicate_sidecar_window_sec` (既定 300s、継続中の incident だけを鳴らす freshness 判定) を説明した。
  broker journal reader は runtime 0.1.40 で入った経路なので、**同 PR で runtime の下限 pin を
  0.1.39 → 0.1.40 へ引き上げた** (`pyproject.toml` / `requirements.txt` / `docker/Dockerfile` /
  `docker/compose.yaml`)。pin を満たす環境ではこの kind は実際に発火する。手元の runtime に
  経路があるかは `claude-org-runtime attention scan --help` に `--broker-state-dir` が出るかで
  判別できる (出ない場合は pin より古い runtime が入っている。設定は正しいのに黙る形になる)。
  `/org-attention-start` は watch 起動時に `ORG_BROKER_STATE_DIR` を確認し、値があれば
  `--broker-state-dir` をリテラルで渡すようになった (watcher 自身はこの env を読まないため、
  非既定 state dir では誰かが渡す必要がある)。`tests/test_attention_runtime_integration.py` は
  broker journal の fixture 行を追加して golden に載せ、`duplicate_sidecar` を drift canary
  (`_EXPECTED_URGENT_KINDS`) に加えた。

### Changed

- `registry/projects.md` を operator-local な生成ファイルへ移行 (#811)。コミット対象は列スキーマ・
  仕様説明・一般サンプル行だけを持つテンプレート `registry/projects.example.md` になり、実体は
  `.gitignore` 対象。`/org-start` (Step 0) と `/org-setup` (Step 3.5) が
  `tools/ensure_projects_registry.py` で未配置時のみ生成する (既存ファイルは絶対に上書きしない)。
  読み手ツール (registry_parser / gen_delegate_payload / work_discovery_repos /
  resolve_worker_layout / state_db importer / dashboard) は従来どおり `registry/projects.md` を
  読むためインターフェース変更は無い。テンプレートの列が増えた場合は起動時に header drift 警告
  (非 fatal) が出る。

  > **既存 checkout の移行が必要**: gitignore はローカルファイルを守らない。移行コミットを跨いで
  > HEAD が動く操作 (pull / rebase / ブランチ切替 / merge) を行うと、`registry/projects.md` が
  > clean な checkout では**ファイルが削除される** (dirty な checkout では git が操作自体を拒否する
  > のでファイルは無傷)。取り込み前に `cp registry/projects.md registry/projects.md.bak` で
  > 退避すること (`registry/projects.md.bak*` も gitignore 済み)。手順の詳細は
  > [`docs/operations/registry-projects-migration.md`](docs/operations/registry-projects-migration.md)。

- work-discovery triage の repo セット解決を反転 (#801)。`registry/projects.md` の GitHub URL 行を
  既定 scan 対象とし (`no` / `off` / `false` の明示 opt-out のみ除外)、home repo (claude-org-ja 自身) の
  常時包含を廃して `registry/org-config.md` の `triage_home` (既定 off) による opt-in へ移行した。
  resolver JSON の `opted_in` は `included` / `opted_out` へ改称し、`recommendation_ref` の補完元を
  `home_repo` から `repos[0]` へ変更した。

### Fixed

- ディスパッチャーの監視判定が「観測不能」を「対象の異常」と解釈して誤検知を出していた問題を、
  3 箇所の個別修正ではなく 1 つの原則として根治 (#869)。
  [`.dispatcher/references/worker-monitoring.md`](.dispatcher/references/worker-monitoring.md) の
  監視ループ手順の**手前**に「観測の原則」節を新設し、(P1) 観測できないことは起きていないことの
  証拠にならない / (P2) 異常の申告は独立した複数の観測面が一致したときに限る / (P3) 観測面ごとの
  証拠能力の表 / (P4) 観測不能 (`OBSERVATION_UNAVAILABLE`) の報告語彙 / (P5) 精度が上がっても行動は
  増えない、を置いた。各判定 (Step 3 のペイン消失 / Step 4 のエラー分岐 / Step 5 の STALL /
  Step 5.1 の relay gap / Step 5.2 の PANE_OUTPUT / Step 5.3 の curator 消失) はこの節を参照する形に
  なり、今後追加される判定にも同じ規律が効く。具体的な誤読の是正は 3 点:
  **(1)** `list_panes` からのペイン消失を単独根拠に `WORKER_PANE_EXITED` を出さない (Step 3 (3-a) の
  裏取りゲートを新設。`list_panes` の可視範囲は current tab のみで、フォーカスが別タブに移ると
  前タブのペインは見えなくなりうるため、**自分以外の org ペインが一斉に消えた形は異常ではなく観測不能**。
  `[pane_not_found]` も cross-tab addressing で返る契約コードなので「閉じた」と読まない)。
  **(2)** STALL_SUSPECTED を報告痕跡の不在だけで発火しない (Step 5 (b-4) を新設。worker の報告は
  ターン境界で出るため長い 1 ターン中に痕跡が無いのは正常で、画面側の稼働痕跡と併せて判定する。
  **「入力欄が空 = idle」は成立しない**ため busy はフッタの active spinner で判定し、画面を観測
  できないサイクルは推測せず観測不能として扱う)。
  **(3)** `completion_reported_at` に受領記録がある task へ完了確認を再送しない。判定材料を維持する
  監視ループ側 (worker-monitoring.md Step 2) と、実際に問い合わせを発行する CLOSE_PANE の
  完了報告ゲート側 ([`.dispatcher/references/pane-close.md`](.dispatcher/references/pane-close.md) 1 の
  「0. 受領記録の確認」を新設) の両方に同じ skip 条件を置いた。**記録の不在は「未着」の証拠に
  ならない**ので、記録が無い / 読めない場合は従来どおりゲートを回す (初回送信の有無だけが変わり、
  polling ループ・secretary unreachable fallback・exit code 分岐は不変)。
  **抑止は一切緩めていない**: (P5) で「異常ではないと判定できたときの正しい行動は通知を出さない
  ことであって自分で直すことではない」を否定形で明記し、ペイン再 spawn / 再構築 / 監視判定を
  結論とした `close_pane` の禁止を維持した (判定条件の変更であって権限の変更ではない)。

- runtime drift 報告が「どの interpreter で測ったか」を伏せていたため、片側だけの測定が
  「更新が遅れている」と誤読された問題を根治 (#863)。`tools/check_runtime_version.py` は
  **毎回・全 outcome で** stderr の 1 行目に
  `[runtime drift-check] 測定 interpreter: <sys.executable> (installed=<version>)` を出すようになった。
  この script が報告する installed は常に「実行した Python が解決したバージョン」であって
  ホストの状態ではなく、ホストにはプロジェクトの `.venv` とシステム `python3` が別バージョンで
  同居しうる (CLI shim の shebang はそのどちらか片方に束ねられる)。exit 0 (up to date) でも
  出すのは、「最新」判定も drift 判定と同じく測った Python に相対で、片方だけ silent にすると
  同じ取り違えが exit 0 側で起きるため。**開示専用の変更**であり、stdout は drift 行専用のまま
  (spliceable)、exit code 契約 (0/1/2/3) も不変。`/org-start` Block C2 には、installed / 未インストールに
  言及する際に interpreter を併記する手順を追記した。

- worker クローズ時の triage scan が zsh で常に失敗し、候補提示が黙ってスキップされていた問題を根治 (#829)。
  旧手順は `REPO_FLAGS=$(tools/work_discovery_repos.py --format flags)` の結果を未クォートで
  `tools/work_discovery_scan.py` へ渡していたが、フラグ列が複数引数になるかは呼び出し元シェルの単語分割次第で、
  ペインの login shell である zsh は既定 `SH_WORD_SPLIT` off のため 1 引数として argparse に届いていた
  (bash では 4 引数に分割されるため bash では再現しない)。`tools/work_discovery_scan.py` に
  **`--all-registry-repos`** を追加し、scan 自身が `work_discovery_repos.resolve_repos()` を
  プロセス内で呼んで repo セットを解決するようにした (呼び出しは 1 コマンド・シェル非依存)。
  repo セット解決の失敗は **exit 2** で、`--repo` 無し = gh カレントリポジトリの暗黙 scan へ
  フォールバックしない (解決失敗が「候補ゼロ」に化けるのを機構で塞ぐ)。resolver の監査情報
  (`repos` / `included` / `opted_out` / `skipped` / `signals`) は scan 出力の新キー
  **`repo_resolution`** に載る (未使用時 `null`、error envelope にも載る)。
  `--format flags` は対話用に残したうえで、docstring / `--help` / 設計書にシェル依存 (zsh では
  `${=VAR}` が必要) である旨を明記した。窓口 skill `/work-discovery`・dispatcher の worker_close 手順
  (`.dispatcher/references/pane-close.md` Step 6) も 1 コマンド形へ更新し、exit 2 時の窓口通知 +
  journal 記帳を「省略不可」として明文化した。

## [1.1.0] - 2026-07-15

窓口の CI 監視・完了報告経路と、worker 委譲まわりの取りこぼしを塞ぐ運用改善リリース。

### Added

- worker brief テンプレートに「完了報告前 rebase」を焼き込み (#700)。並列 dispatch で
  複数 worker が同じ integration point (registry / CLI routing / pyproject / README / docs) を
  編集した際の CONFLICTING 連鎖 (先勝ち残りが conflict で CI すら起動しない) を、worker 段階の
  `git fetch origin` → rebase → clean push で予防する (full タスク限定・Codex review 前)。

### Changed

- CI 監視パイプラインを見逃しゼロに多層再設計 (#703, Refs #653 #658)。events テーブルを正本と
  する outbox 型 relay (`event_deliveries` 配送台帳 + dispatcher の `/loop 3m` relay scan) を導入し、
  pr-watch の secretary push が env 欠如で silent no-op に陥っても終端信号を取りこぼさないようにした。
  relay scan の floor を配送台帳エポックに固定し、長時間 outage 中の終端イベントも取りこぼさない。
- `pr_watch` の CI watch を `gh pr checks --watch` 非依存の自前ポーリングに置換 (#701, Closes #695)。
  skipping バケットを terminal と認識せず watch loop が抜けない不具合を解消し、終了条件を
  `pending_count == 0` に統一 (fail が pending 共存時の早期終了も是正)。

### Fixed

- 中間ハンドオフ報告受信時にも `worker_reported` を journal するよう修正 (#704, Closes #699)。
  dispatcher の PANE_OUTPUT_WITHOUT_PEER_MSG 誤検知を防止する。
- runtime drift check の sandbox silent-skip を根治 (#696, #119)。`check_runtime_version.py` に
  exit code 契約 (0=up-to-date / 1=drift / 2=unverified / 3=not-installed) を導入し、offline / PyPI
  不達を silent skip でなく exit 2 + stderr で顕在化。pin ラグを「最新」と誤読して既修正バグを
  委譲する phantom dispatch を防ぐ。

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

[Unreleased]: https://github.com/suisya-systems/claude-org-ja/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/suisya-systems/claude-org-ja/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/suisya-systems/claude-org-ja/releases/tag/v1.0.0
