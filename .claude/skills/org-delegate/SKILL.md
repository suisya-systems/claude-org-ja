---
name: org-delegate
description: >
  ワーカーClaudeを派遣して作業を委譲する。窓口は司令塔であり、
  手を動かす実作業は原則としてワーカーに任せる。
  ユーザーから作業の依頼を受けたとき、ファイル編集・実装・調査等の
  実作業が発生する場合に発動する。
effort: medium
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash(python tools/gen_delegate_payload.py:*)
  - Bash(py -3 tools/gen_delegate_payload.py:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python -m tools.state_db.importer:*)
  - Bash(git fetch:*)
  - Bash(git log:*)
  - Bash(gh issue create:*)
  - mcp__renga-peers__send_message
  - mcp__renga-peers__inspect_pane
  - mcp__renga-peers__list_peers
  - mcp__renga-peers__list_panes
---

# org-delegate: ワーカー派遣

作業をワーカーClaudeに委譲する。窓口はタスク分解と派遣ペイロード生成だけ行い、
ペイン起動・指示送信はディスパッチャーに委託する。これにより窓口のロック時間を最小化する。

> **本 SKILL のスコープ**: 派遣の「初動」(タスク特定 → 派遣ペイロード生成 → ディスパッチャーへの DELEGATE 受け渡し → ワーカー起動後の挨拶 → 進捗・完了報告受信時の ack と REVIEW 遷移) のみ。以下は別スキル / reference に分離している:
> - **ワーカー起動・指示送信・状態記録の手順** → [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) (ディスパッチャー専属)
> - **ユーザー承認後の push / PR / CI 監視 / レビュー指摘ループ / マージ後クローズ** → [`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md)
> - **ワーカーからの判断仰ぎ / スコープ拡張 / ブロッカーのエスカレーション** → [`.claude/skills/org-escalation/SKILL.md`](../org-escalation/SKILL.md)
> - **ack 文面の最低 3 要素・種別ごとの例文** → [`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) (single SoT)

> **state-db cutover (M4, Issue #267 / #284)**: 構造化セクションの write は **必ず `StateWriter.transaction()` 経由**で行う。post-commit hook が `.state/org-state.md` / `.state/org-state.json` を DB から自動再生成し、`update_run_status('<task_id>', 'completed')` 呼び出しは `.state/workers/worker-<task_id>.md` を `.state/workers/archive/` へ自動 move する。markdown 直接編集は drift_check が検出する。events は DB の `events` テーブルが SoT (`tools/journal_append.sh` / `.py` は DB ルーティング済み)。DB 不在時は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で構築する。

> **輸送層（transport）両系 — 既定 `renga` / opt-in `broker`**: 本スキルの `mcp__renga-peers__*` 呼び出しは **既定 `renga`**（`ORG_TRANSPORT` 無設定）で書いてあり、そのまま従えばよい（既定挙動は不変）。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では MCP サーバー名が `org-broker` になり、ツールの **完全修飾名が `mcp__renga-peers__*` → `mcp__org-broker__*`** に機械置換される（引数形・セマンティクスは同一なので手順の論理は変わらない）。輸送依存で手順が変わる点だけ broker 併記する:
>
> - **受信モデル（push → pull）**: renga は worker からの進捗 / 完了 / 判断仰ぎが `<channel source="renga-peers" …>` として in-band で push される。broker は **pane-local なナッジが出るだけ**で、本文は `check_messages`（broker では `mcp__org-broker__check_messages`）で pull 取得する（broker は全 peer が pull 配送 = `receive_mode` 定数 `"poll"`。Step 5 の「ワーカーからのメッセージ受信時」が「ナッジを見たら `check_messages`」に変わるだけで、ack の `send_message` 等の手順は同型）。
> - **spawn 儀式（dev-channel 承認 → folder-trust 承認）**: ワーカー起動はディスパッチャー専属（[`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)）だが、broker では `--dangerously-load-development-channels` 注入に代えて `--mcp-config <broker>` 注入となり、承認プロンプトが Claude Code の **folder-trust プロンプト**に変わる（`send_keys(enter=true)` での機械承認は同型）。root `.claude/**` self-edit の `send_keys` 事前承認（下記 Step 5）も broker では `mcp__org-broker__send_keys` で同じ手順を踏む。
> - **エラー分岐（broker 追加コード）**: renga コードに加え broker は `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。一覧は [`.claude/skills/org-delegate/references/renga-error-codes.md`](references/renga-error-codes.md) の broker 節を参照。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外。本フローは元々使わない）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、提案・批准待ち）、設計 SoT は transport-lab `docs/design/ja-migration-plan.md` §5.2(ii)。broker 実走（dogfood）は Epic #6 Issue G スコープで本スキルの既定経路ではない。

## 窓口とディスパッチャーの役割分担

| 工程 | 担当 |
|---|---|
| プロジェクト名前解決 | **窓口** |
| work-skill 検索 | **窓口** |
| タスク分解 / 派遣ペイロード生成 | **窓口** (`gen_delegate_payload.py`) |
| DELEGATE 送信 | **窓口**（ここで窓口は解放される） |
| ペイン起動・ピア待ち・指示送信・状態記録 | **ディスパッチャー** ([`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)) |
| 窓口への派遣完了報告 | **ディスパッチャー** |
| ワーカーからの進捗/完了/escalation 報告の受信 | **窓口** |
| ワーカー完了時のペインクローズ | **ディスパッチャー**（窓口から `CLOSE_PANE` 依頼） |

## 委譲前チェックリスト（窓口が実行）

タスク分解に入る前に、依頼内容を以下の観点で確認する。該当する場合はユーザーに聞き返す。

| チェック項目 | 確認すべき状況 | 例 |
|---|---|---|
| **曖昧な用語・略語** | ツール名・サービス名・略語が複数の意味を持ちうる場合 | 「gog」→ Google OAuth? gog CLI? |
| **OS固有の前提条件** | OS別の成果物を作る場合、デフォルト設定の明示が必要 | Mac=zsh、Windows=py -3、パス区切り |

- 曖昧な用語がある場合: 「○○は△△のことですか？」とユーザーに確認してから進める
- OS別タスクの場合: ペイロード生成時に、OS固有の前提条件をワーカーへの指示に含める
  - **Windows worker + CLI / 標準出力を持つツール実装の場合**: CLI へ出力される文字列（argparse `help=` / `print()`）は ASCII の `-` を使い em-dash 等 cp932 非対応文字を避けること、`--help` を実端末で 1 回スモークすること、の 2 点を `--impl-guidance` 等で brief に載せる（rendered brief の Windows 注意事項にも常時記載済みだが、CLI ツール委譲時は窓口が明示的に意識する）。背景: cp932 コンソールが em-dash(U+2014) を encode できず `--help` がクラッシュする型が 2 回発火（ja#537 / runtime#63）。pytest は `redirect_stdout` で UTF-8 キャプチャするため通り、実端末でのみ落ちる

### incorporation / sync 系タスクの初手チェックリスト

ソース（review 結果 / 別ブランチ / 別リポジトリの状態）を destination に取り込む incorporation / sync 系タスクでは、**ソース commit が destination の現状から N コミット以上進んでいる場合、selective merge（cherry-pick / 必要 hunk のみ apply）を初手として検討する**。byte 一致 cp は Codex iterative review fix を機械的に上書きするリスクがある。

| 観点 | チェック | アクション |
|---|---|---|
| ソースと destination の乖離 | `git log <source>..<destination>` / `git log <destination>..<source>` で双方向に確認 | 双方向に diverge があれば cp 禁止、selective merge を採用 |
| destination 側の追加修正 | destination ブランチで Codex review fix / Blocker fix が積まれていないか | 積まれている場合は cp で機械的に上書きしないこと（cherry-pick or hunk 単位の apply） |

背景: cp で destination の修正を機械的に巻き戻す事故が過去に発生（destination 側の credential 露出対策 Blocker fix を revert 寸前まで進んだ）。ワーカーへの brief で「初手 cp 禁止 / 取り込み戦略を明示」を要求する。

## Step 0: プロジェクト名前解決（窓口が実行）

ユーザーの依頼からプロジェクトを特定する:

1. `registry/projects.md` を読む
2. 依頼に含まれるキーワードから該当プロジェクトを特定する（通称・プロジェクト名・説明から照合）
3. 特定できた場合はそのパスを使う
4. 特定できない場合は登録済みプロジェクトの通称一覧を提示し、選ばせる
5. 新規プロジェクトの場合:
   - パスをユーザーに確認する
   - 通称・説明・よくある作業例を推定し、ユーザーに確認してから `registry/projects.md` に追記する

## Step 0.5: work-skill 検索（窓口が実行）

タスク分解の前に、関連する既存の work-skill がないか検索する。マッチした work-skill はワーカーへの指示に参考情報として含める。

1. `.claude/skills/` 配下の全 SKILL.md ファイルを列挙する
2. 各 SKILL.md の frontmatter (`type` / `description` / `triggers`) をタスク内容と照合する。`org-` プレフィックスは組織運営スキルなので検索対象外
3. 関連性があれば候補に含める（完全一致は不要、複数マッチは関連度順に全て）

**マッチした場合:**
- 人間に「関連 work-skill を見つけました: `{skill-name}` — 参考情報として含めます」と通知する
- `gen_delegate_payload.py` 呼び出しの `--knowledge` フラグに work-skill の SKILL.md パスを渡す。Stage 2 brief renderer がそのパスを `[references].knowledge` として CLAUDE.md / CLAUDE.local.md に埋め込む。複数マッチは `--knowledge <path1> --knowledge <path2>` のように繰り返す
- ワーカーへの指示（instruction-template）にも参考スキルの存在を明記する

work-skill の手順をそのままコピーしない。参考情報として提示し、ワーカーが判断する。

## Step 0.6: release-class タスクの pre-fetch（窓口が実行）

以下 4 条件のいずれかに該当する場合のみ、`gen_delegate_payload.py apply` の **前に** 対象プロジェクトの local main を `git fetch origin` + `git pull --ff-only origin main` で更新する:

- task description / commit-prefix / planned branch に `release`, `release/`, `vX.Y.Z` 等のリリース昇格語を含む
- 対象ファイルに `CHANGELOG.md` 昇格 / `__about__.__version__` / `pyproject.toml` の `version` bump 等を含む
- task_id に `release` を含む（例: `runtime-0-1-10-release`）

詳細条件・実行コマンド・worker permissions deny の根拠（fetch 漏れ → 着手 5 分以内に worker BLOCKER → 10 分以上ロスの背景）は [`references/release-pre-fetch.md`](references/release-pre-fetch.md) を一次参照。**トリガー見逃しが BLOCKER 直結のため本体に残す 4 条件は省略禁止。**

> **Pattern B との切り分け（Issue #480）**: Pattern B の worktree 作成は apply 自身が `git fetch origin` してから `origin/HEAD` 起点で切るため、worktree の起点鮮度はこの Step 0.6 に依存しない。Step 0.6 が担保するのは Pattern A（worker が local main から `release/*` を切る）の **local main の鮮度** であり、両者は対象が異なる。詳細は [`.claude/skills/org-delegate/references/release-pre-fetch.md`](references/release-pre-fetch.md) の「Issue #480 との関係」節。

## Step 0.7 / 1 / 1.5 / 2: 1 コマンドで派遣ペイロードを生成（Issue #283）

Step 0.7 (gitignore 事前チェック) / Step 1 (Pattern 判定) / Step 1.5 (ワーカーディレクトリ準備 + role 決定 + settings 生成) / Step 2 (DELEGATE 本文組み立て) は **`tools/gen_delegate_payload.py` が一括で行う**。窓口の責務はタスク特定 (Step 0)・work-skill 検索 (Step 0.5)・対象ファイルの抽出・depth 判断のみ。

### dispatch 前検証チェック（Step 0.7 付随・窓口が手動で実行）

以下 2 点は `gen_delegate_payload.py` が検証**しない**ため、`preview` の前に窓口が手動で確認する。**満たせない場合はその委譲を dispatch 不成立とし、`apply` に進まない**（原因を窓口側で解消するかユーザーに上げてから Step 0 から回し直す）:

1. **コミット済みベースの存在確認**: `--target` の **file existence は常に確認**する。**line existence は、行番号付きレビュー指摘 / パッチを入力に持つ委譲のみ**検証する。live tree の未コミット変更を編集ベースとする委譲は不成立（worker の worktree / clone はコミット済みベースから切られるため対象が見えない）— commit してから委譲し直す
2. **org 挙動変更時の契約 grep**: org の挙動（cadence / lifecycle / 責務境界）を変える委譲では `docs/contracts/` を挙動キーワード（loop / cadence / curator / close 等）で grep し、ヒットした契約の cited source（`.dispatcher/CLAUDE.md`, `.dispatcher/references/worker-monitoring.md` 等）まで辿る。ヒットは **`--target` に入れず**（edit scope 汚染）、`--knowledge` / `--impl-guidance` で brief に運ぶ

判定基準・コマンド例・grep キーワードの詳細は [`.claude/skills/org-delegate/references/delegate-flow-details.md`](references/delegate-flow-details.md) §1.5 を一次参照。

### 標準フロー (推奨)

```bash
# 1. preview: 完全に非破壊。DELEGATE 本文と作成予定ファイル一覧だけを確認する
python tools/gen_delegate_payload.py preview \
    --task-id <task-id> --project-slug <slug> \
    --target <path>... --description "<desc>" \
    --verification-depth full

# 1.5. Step 1.7 gate: preview 出力で Codex design review トリガー条件を評価
#      該当する場合のみ codex exec で design review を実行し、要約を
#      --impl-guidance または --knowledge で apply に渡す（下節 Step 1.7 参照）

# 2. apply: state.db に runs.status='queued' で予約 + CLAUDE.md/CLAUDE.local.md 配置
#    + claude-org-runtime settings generate 実行 + send_plan.json 出力
python tools/gen_delegate_payload.py apply \
    --task-id <task-id> --project-slug <slug> \
    --target <path>... --description "<desc>" \
    --verification-depth full

# 3. apply 出力の send_plan.json を MCP 呼び出しにコピペ
#    cat <worker_dir>/send_plan.json
#    → mcp__renga-peers__send_message(to_id="dispatcher", message=<message>)
```

`apply` は **T1 reservation のみ** (`runs.status='queued'`) を行う。Active Work Items への active 化はディスパッチャー T2 ([`docs/contracts/delegation-lifecycle-contract.md`](../../../docs/contracts/delegation-lifecycle-contract.md)) なので本 skill では触らない。失敗時はキューを残したまま Secretary に判断を仰ぐこと。

### よく使うフラグ

- `--mode edit|audit` (default `edit`): claude-org 上の **読み取り専用** 監査タスクは `--mode audit` を明示する
- `--branch <name>`: planned_branch を上書き。default は `feat/<task-id>` (description に "fix"/"bug"/"修正" を含むと `fix/<task-id>`)
- `--commit-prefix "<prefix>"`: 省略時は project_slug の頭部から推論 (例: `claude-org-ja` → `feat(claude):`)
- `--closes-issue N` / `--refs-issues N1 N2`: 「Closes #N」「Refs #N1 #N2」を brief に埋め込む
- `--impl-target <path>` / `--impl-guidance "<text>"` / `--knowledge <path>`: optional な `[implementation]` / `[references]` セクション
- `--skip-settings`: `claude-org-runtime settings generate` をスキップ (CLI 未導入環境向け)
- `--from-toml <path>`: 既存 `worker_brief.toml` を入力にする。CLI フラグは TOML を上書きする

### Pattern / role / branch の判定詳細

判定ロジック (Pattern A vs B vs C / gitignored サブモード / role 表 / planned_branch / DELEGATE 本文の必須行) は [`.claude/skills/org-delegate/references/delegate-flow-details.md`](references/delegate-flow-details.md) 参照。self-edit タスクの特例（Issue #289、`pattern_variant='live_repo_worktree'`）は [`.claude/skills/org-delegate/references/claude-org-self-edit.md`](references/claude-org-self-edit.md) §3 参照。

### 対象ファイル抽出

「対象ファイル」は窓口がタスク説明から抽出する（依頼文・Issue 本文・ユーザー発話の中で明示されたパス。機械的判定はしない）。対象ファイルが特定できないタスク（純粋な調査、対象パス未定の新規作成など）は `--target` を渡さなくてよい。

### 標準経路が想定外の出力を返した場合

標準経路 (`gen_delegate_payload.py apply`) が想定外の出力 (Pattern 誤判定 / resolver エラー / brief 不整合 等) を返した場合、Secretary は **手動で同じ作業を再現してはならない**。resolver のバグとして Issue を切り、当該タスクの delegation は resolver が直るまで pause する。手作業 fallback は skill のスコープ外。CLI 未導入環境では `--skip-settings` フラグに限定する。歴史的な手書き経路の museum copy は `docs/legacy/hand-typed-delegate-path.md` にあるが標準オペレーションでは参照禁止。

## Step 1.7: Codex design review trigger（窓口が実行、Issue #337）

`apply` の前に Codex design review を実施するか判定する。以下のいずれかに該当する場合のみ実行:

- 推定工数 ≥ 3h
- 新規 module / 新規 tool 導入
- ファイル変更 ≥ 3 件
- `docs/contracts/` 配下の契約ドキュメント参照

トリガー判定の詳細表・`codex exec` コマンド・review 要約の `--impl-guidance` / `--knowledge` への組み込み手順は [`references/codex-design-review.md`](references/codex-design-review.md) を一次参照。

## Step 1.8: dogfood follow-up issue protocol（窓口 + org-pull-request 連携、Issue #338）

新規 CLI tool / 新規 runtime / 新規 workflow / 新規 protocol の導入、または既存 tool の break-change 再設計に該当するタスクは **dogfood 対象**。実装 delegation と paired で follow-up issue を作成し、後続の実使用 delegation を dogfood pass として earmark する。

dogfood 対象判定 / 窓口責務 (A) 実装起票時の `registry/dogfood_pending.md` append / (B) dogfood pass earmark の手順 / org-pull-request 連携 / register フォーマット / hygiene チェック (consumed→closed) は [`references/dogfood-protocol.md`](references/dogfood-protocol.md) を一次参照。

状態遷移: `pending → open → consumed → closed`

## Step 3 / 4: ワーカー起動・指示送信・状態記録（ディスパッチャーが実行）

詳細手順 (3-1 balanced split / 3-1c SPLIT_CAPACITY_EXCEEDED escalate / 3-2 spawn / 3-3 pane_started / 3-3b channel approve / 3-4 list_peers / 3-5 instruction send / 3-6 順次起動 / Step 4 状態記録 / Worker Directory Registry) は **[`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)** を一次参照する。窓口は触らない。

ディスパッチャーは派遣完了時に窓口へ `DELEGATE_COMPLETE` を返す。

## Step 5: 進捗管理（窓口が実行）

### ⚠️ cwd 注意: state.db touching tools

`tools/journal_append.sh` / `tools/journal_append.py` / `tools/set_run_pr_open.py` / `python -c "... StateWriter ..."` 等、`state.db` を相対パスで開く tool は ja root 相対前提。worker / worktree cwd から起動すると `no such table: runs` / `no such table: events` でサイレント or クラッシュ失敗し、後段の post-commit hook や snapshot 再生成も走らない。必ず `cd <ja-root>` してから実行すること。Issue #398 で根本対応中。

### 窓口 → worker のメッセージング規約（Issue #475: 1 worker = 1 task = 1 scope）

窓口から既存 worker へ送る全 message は「1 worker = 1 task = 1 scope」の原則に従う。canonical な 3 rule は CLAUDE.md「役割の境界 > worker への追加依頼の境界」を SoT 参照:

1. **追加依頼は元タスクのスコープ内に限る**: brief で示した範囲内の補足・修正指示のみ追送する。スコープ外の別件は同 worker に投入せず、Step 0 から本 SKILL を回し直してディスパッチャー経由で別 worker を派遣する
2. **worker のスコープ拡張提案は escalation 経由**: 窓口は一次承認せず [`/org-escalation`](../org-escalation/SKILL.md) を発動する
3. **窓口は worker 作業を代行しない**: ファイル編集・commit・テスト等を窓口側 worktree で手を出さず、追加依頼として worker に戻すか別 worker を派遣する

違反事例: 2026-05-21 voice-v2-independent ペインへの別件混入投入（スコープ外作業を同 worker に追送し、本来別 worker を立てるべき別件を 1 worker に集約してしまった）。本節の guard / CI 実装は別 Issue。

### DELEGATE_COMPLETE 受信時

ディスパッチャーから派遣完了報告を受け取ったら、各ワーカーに挨拶メッセージを送る:
```
mcp__renga-peers__send_message(
  to_id="worker-{task_id}",
  message="窓口です。{task_id} の作業をお願いしています。完了・進捗・ブロック、全ての報告は `to_id=\"secretary\"` で renga-peers 送信してください。"
)
```

**`.claude/` 編集タスクの send_keys 事前承認（root `.claude/**` self-edit のみ）**: 委譲対象に claude-org root の `.claude/**` が含まれる場合（`.dispatcher/` / `.curator/`、worker dir 生成物の `.claude/settings.local.json` は対象外）、窓口は上記挨拶の送信に**続けて** `mcp__renga-peers__send_keys` で承認文（対象ファイル列挙 + task_id + 「窓口経由のユーザー承認」の明記）を worker ペインへ入力しておく。worker 側は編集前にこの承認入力の存在を確認し、無ければ編集せず窓口に要求する（ハンドシェイク固定で deadlock / 空打ちを防止）。スコープ境界・背景（2 層ガード）・承認文テンプレート・worker brief 必須文言は [`.claude/skills/org-delegate/references/claude-org-self-edit.md`](references/claude-org-self-edit.md) §5 を一次参照。

### ワーカーからのメッセージ受信時

**Canonical event flow**（途中段階を飛ばしてはならない）:

```
worker → Secretary peer message
  1. ack to worker (全 message 共通で必須。dead-lock 防止)
  2. update Progress Log + DB (run.status / events / pending-decisions register)
  3. report to user           (完了 / escalation / blocker のみ。進捗報告は不要)
  4. wait for user approval before push/PR
  5. CI watch / next instruction → [`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md)
```

- ack の最低内容と種別ごとの例文は [`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) を参照。**ack ≠ user 承認**: `git push` / `gh pr create` / `tools/pr-watch.*` は user の明示的 OK 後にのみ発行
- 2 → 3 の順序は「内部状態を先に整合させてから user に報告する」原則

#### 0. 判断仰ぎ・スコープ拡張・ブロッカー（最優先で識別）

→ [`.claude/skills/org-escalation/SKILL.md`](../org-escalation/SKILL.md) を発動する。Secretary は一次承認しない。

#### 1. 進捗報告

- worker へ ack を返す（[`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) の「進捗報告 ack」節。Progress Log 追記より前）。**進捗報告は user に上げない・承認待ちもしない**
- `.state/workers/worker-{task_id}.md` の Progress Log に追記
- DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)

#### 2a. 完了報告

- worker へ ack を返す（[`.claude/skills/org-delegate/references/ack-template.md`](references/ack-template.md) の「完了報告 ack」節。受信直後・dead-lock 防止で他の状態更新より前に）
- **DB 経由で run を REVIEW に遷移**（markdown 直接編集禁止）:
  ```bash
  python -c "
  from pathlib import Path
  from tools.state_db import connect
  from tools.state_db.writer import StateWriter
  conn = connect('.state/state.db')
  with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
      w.update_run_status('<task_id>', 'review')
  "
  ```
- DB の events テーブルにイベント追記 (`bash tools/journal_append.sh ...`)
- **dogfood pass 完了時の register 更新（Issue #338）**: 完了したタスクが `registry/dogfood_pending.md` の `dogfood_run_task_id` 列に earmark されていた場合、該当行の `status` を `open → consumed` に遷移する。defect は paired follow-up issue (`dogfood_issue` 列) に既に集約されている前提（dogfood pass worker の brief で format 指定済）。protocol 全体は本 SKILL Step 1.8 を SoT
- **人間向け理解サマリを承認提示の土台にし永続化する（検証深度 `full` 限定）**: full モード完了報告には worker が「人間向け理解サマリ」（(1) 最重要の変更点 N 個、(2) 要確認ファイル / hunk、(3) 設計判断と理由）を含める（スキーマ SoT は [`.claude/skills/org-delegate/references/worker-claude-template.md`](references/worker-claude-template.md)）。窓口は自分でコードを精読せず、このサマリをユーザーへの承認提示の土台にする（必要なら業務言語に整える）。受領したサマリは `.state/workers/worker-{task_id}.md` の Progress Log に `Human Understanding Summary:` 見出し + 直下の fenced code block でそのまま追記する（merge 承認時に再掲する元。full 完了報告が複数回ある場合は最新ブロックを正とする）。PR 作成時は PR 本文にも要約を載せてよい。**full でサマリが欠落していたら通常の review feedback として同ペインの worker に補完を依頼する**（[`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md) 2c の review-feedback 手順で扱う）。これは手順レイヤの完了報告フォーマット拡張であり、contract（T4 `worker_completed`）の遷移条件は変えない。minimal の 1 行 `done:` 報告にはサマリは付かない
- **awaiting_user 通知の emit（Issue #28）**: 人間への報告 → 承認待ち停止に入る直前で、attention watcher に「Secretary が user の判断待ちで停止する」ことを知らせる:
  ```bash
  bash tools/journal_append.sh notify_sent kind=awaiting_user task_id=<task_id> gate=worker_completed note="<PR/Issue 等の短い文脈>"
  ```
  並走 runtime PR の classifier がこの 1 行を `secretary_awaiting_user` (default severity `urgent`) として拾い、画面前にユーザーが居ない場合でもビープで気付ける。CLAUDE.md「secretary が user の判断を待っている状態を通知する」節を参照
- 結果を人間に報告し、**ペインを閉じず承認待ちで停止**。承認なしで push/PR を発行すると worker / user 双方への protocol 違反

#### 2b / 2c. ユーザー承認後・レビュー指摘・マージ後クローズ

→ [`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md) を発動する。

### ワーカー監視と介入判定（窓口が実行）

派遣後、ワーカーが深掘り・過剰検証ループに入っていないか定期的に確認する。**介入トリガー**（いずれか 1 つ以上該当したら `mcp__renga-peers__inspect_pane` で状況確認）:

- 同一タスクで 30 分超経過、かつ同じフェーズ（実装 / レビュー / 検証）に 3 回目以降入っている
- 1 時間以上進捗報告なしで静穏（入力待ちでもなく、progress ログも出ない）
- (codex を使っている場合) Codex セルフレビューが 4 ラウンド目以降に入っている

**介入手順**: `inspect_pane` で画面確認 → 深掘りと判断したら `send_keys(target="worker-{task_id}", keys=["Escape"])` で中断 → `send_message` で tight な修正指示を送る (例「検証深度 minimal に切り替え。Codex レビュー・追加テスト禁止。`done: {commit SHA} {ファイル名}` の 1 行だけ返してください」)。

窓口が自らワーカーの worktree で commit を代行することは auto-mode classifier によりブロックされる（スコープ逸脱）。介入はあくまで「指示の再送」で行うこと。
