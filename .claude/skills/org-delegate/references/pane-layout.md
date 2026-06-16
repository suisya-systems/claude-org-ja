# Pane Layout Specification (org-broker MCP)

ペイン / タブ配置ルール。`org-start` と `org-delegate` が参照する。
ペイン制御は `mcp__org-broker__*` MCP ツール経由で行う（`spawn_claude_pane` / `set_pane_identity` を含む pane 制御ツール群が MCP で完結する）。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイルの `mcp__org-broker__*` 呼び出しは **既定 `broker`**（`ORG_TRANSPORT` 無設定）で書いてあり、そのまま従えばよい（既定挙動）。`ORG_TRANSPORT=renga`（opt-in・切戻し可）では MCP サーバー名が `renga-peers` になり、ツールの **完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので balanced split の判定・操作の論理は変わらない）。輸送依存で手順が変わる点だけ renga 併記する:
>
> - **spawn 儀式（folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 下記「`spawn_claude_pane` を使う理由」のとおり既定 broker は `--mcp-config <broker>` を注入し起動時に Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。ratified §5/§8.5 の folder-trust フローへの加算であり置換ではない。設計 transport-lab `docs/design/broker-native-roles.md` §9.5）。なお broker spawn helper は内部で interactive-TUI argv を default-deny billing-neutral guard の背後で組み立てる。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。
> - **受信モデル（push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計済（runtime push-first 0.1.24+、broker-native-roles.md §9）で、各ペイン同居の channel sidecar（`server:org-broker-channel`）が `notifications/claude/channel` で本文を idle セッションへ注入する。pull（pane-local ナッジ + `check_messages`）はフォールバック層で、その受信は各役割の能動 cadence poll が正路（ナッジは idle を起こさないため非依存。§9.6 読み替え表）。`ORG_TRANSPORT=renga`（opt-in）では `send_message` が `<channel source="renga-peers" …>` の in-band push になる。レイアウト操作自体（`list_panes` / `spawn_claude_pane` / `close_pane` 等）はどちらも同じ論理でツール名のみ `mcp__org-broker__*` ⇄ `mcp__renga-peers__*` に変わる。
> - **エラー分岐（既定 = broker 拡張コード込み）**: shared codes（`split_capacity_exceeded` / `[split_refused]` / `[cwd_invalid]` 等）に加え既定 broker は `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる。一覧は [`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の broker 節を参照。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）— ただし本ドキュメントの balanced split は同一タブ内 split のみで `new_tab` を使わない設計なので、broker でも欠落の影響は無い。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次再設計）/ `docs/design/ja-migration-plan.md` §5.2(ii)。broker 実走（dogfood）は Epic #6 Issue G スコープで本ファイルの既定経路ではない。

## 初期レイアウト (`renga --layout ops` の結果 + ディスパッチャー・キュレーター起動後)

窓口 (`secretary`) / ディスパッチャー / キュレーターが同一タブに立ち上がり、ワーカーも同一タブ内に split で積んでいく方針。

```
Tab 1: ops (ワーカー 0 人)
┌────────────────────┬────────────────────┐
│                    │                    │
│                    │     Secretary      │
│                    │     (上半分)       │
│                    │                    │
│                    ├──────────┬─────────┤
│                    │ Dispatcher  │ Curator │
│                    │          │         │
└────────────────────┴──────────┴─────────┘
```

> ※ 実際には `secretary` が左で `dispatcher/curator` が下半分を占める構成もあり、初期レイアウト詳細は org-start に委ねる。本ドキュメントで重要なのは「`secretary / curator / worker / dispatcher` の 4 役を候補とする role-priority 付き balanced split で動的にワーカー zone を作っていく」という点（詳細は下記アルゴリズム節）。

## 配置ルール

| 対象 | 操作 | 備考 |
|---|---|---|
| ディスパッチャー | 窓口ペインを水平分割して下半分 | `mcp__org-broker__spawn_claude_pane(target="focused", direction="horizontal", role="dispatcher", name="dispatcher", cwd=".dispatcher", permission_mode="bypassPermissions", model="sonnet")` (org-start Block A-1) |
| キュレーター（オンデマンド時のみ） | ディスパッチャーペインを垂直分割して右半分 | `mcp__org-broker__spawn_claude_pane(target="dispatcher", direction="vertical", role="curator", name="curator", cwd="../.curator", permission_mode="auto")` (pane-close.md Step 5-3。常駐廃止につき org-start からは spawn しない) |
| 各ワーカー | **balanced split**: `list_panes` が返す現在の rect から target と direction を動的に選び、同一タブ内に積む | 詳細は下記「ワーカーの balanced split 戦略」セクション。`mcp__org-broker__spawn_claude_pane(target={target}, direction={direction}, role="worker", name="worker-{task_id}", cwd="{workers_dir}/{task_id}", permission_mode="auto")` (org-delegate Step 3) |

> **`spawn_claude_pane` を使う理由**: 構造化 launch ツール（renga 0.18.0+ で追加、broker adapter も同 API を提供）。`cwd` / `permission_mode` / `model` / `args[]` を構造化フィールドで渡すと、既定 broker では `claude --permission-mode {mode} --mcp-config <broker>`（daemon・全ツール + agent token）+ push 一次のための channel sidecar `--dangerously-load-development-channels server:org-broker-channel` を**併用**する flag が合成され、初回承認は **folder-trust プロンプト + dev-channel sidecar 承認の 2 段（spawn-flow 3-3b）**になる。旧方式（`cd`-プレフィックス付き command 文字列を `spawn_pane` に流し込む）は **禁止**（cwd 変更プレフィックスがあると bare-`claude` auto-upgrade が発動せず、`send_message` の channel push が届かなくなる。窓口→ディスパッチャー / ディスパッチャー→ワーカーの指示が一切通らなくなる）。Secretary のみ `ops.toml` から bare `claude` で起動され auto-upgrade に任せる。`ORG_TRANSPORT=renga`（opt-in）では合成される flag が `--mcp-config <broker>` ではなく `--dangerously-load-development-channels server:renga-peers` の 1 段になり、初回承認も dev-channel 承認の 1 段になる（手順形は同型、本ファイル冒頭の両系注記を参照）。

## ワーカーの balanced split 戦略

### なぜ balanced split が必要か

各 split は対象ペインを 50/50 に分ける。`MIN_PANE_WIDTH = 20` / `MIN_PANE_HEIGHT = 5` の下限を割り込むと `[split_refused]` で拒否される (調査: `<workers_dir>/renga-split-inv/findings.md`)。

固定 target や序数 `k` ベースの lookup table では、dispatcher 幅の累積半減や、ワーカーが途中で閉じた後の再派遣で想定レイアウトと実レイアウトが乖離し、早期に `split_refused` を誘発していた。

現設計は `mcp__org-broker__list_panes` が返す各ペインの **rect 情報 (`x / y / width / height`, cell 単位)** を使い、**現状のレイアウトから動的に target と direction を選ぶ**。ワーカー退役順の揺れや途中クローズに強く、固定的な「N 並列上限」は持たず、ターミナルサイズと MIN_PANE 制約が許す限り分割し続け、限界に達したら自動 escalate する。

### アルゴリズム

balanced split の判定ロジック (target / direction 選択、MIN_PANE 制約、secretary 保険、role priority ソート、rect 隣接判定、`split_capacity_exceeded` 検出) は **`claude-org-runtime` の helper が SoT**。dispatcher は `mcp__org-broker__list_panes` のスナップショットと task JSON を入力にして以下のいずれかを呼び、返却された action plan (`spawn` / `after_spawn` / `escalate` / `state_writes` / `status`) に従って `spawn_claude_pane` / escalate を実行する:

- CLI (運用上の標準呼び出し): `claude-org-runtime dispatcher delegate-plan --task-json ... --panes-json ... --state-dir ... [--template-repo ...] [--locale-json ...]`。dispatcher 側の手順は `.dispatcher/CLAUDE.md` の delegate-plan helper 節を一次参照
- ライブラリ: `claude_org_runtime.dispatcher.runner` モジュールの `build_plan(...)` (action plan 全体) と `choose_split(panes)` (target / direction だけ欲しい場合の low-level helper)

定数値 (MIN_PANE_WIDTH / MIN_PANE_HEIGHT / SECRETARY_MIN_WIDTH / SECRETARY_MIN_HEIGHT / role priority マップ) と判定順序、rect 隣接の厳密な定義は **`claude_org_runtime.dispatcher.runner` モジュール本体** (`_ROLE_PRIORITY` / `MIN_PANE_*` / `SECRETARY_MIN_*` / `choose_split()` / `rect_adjacent()`) を一次参照とする。本ドキュメントから定数値の prose を削除したのは、runtime と doc の drift が `[split_refused]` 等の不可解な失敗を生む原因になるため (Issue #307 cleanup)。

候補が空のときは helper が `status="split_capacity_exceeded"` と `escalate.send_message(to_id="secretary", ...)` を返す。dispatcher は `spawn_claude_pane` を発行せず、該当ワーカー 1 件だけ派遣を中止、本体監視ループは継続する (`SKILL.md` Step 3-1c 参照)。

### Verification trace (Issue #307 シナリオ、参考)

`secretary 280×43 / dispatcher 140×43 / curator 140×43` の直後レイアウト (ターミナル ≈ 280×86、org-start で secretary horizontal split → dispatcher vertical split 直後を想定) を入力にした場合の `choose_split` の挙動を手動 trace した参考表。**正準値は runtime SoT**。doc 上の値と runtime の挙動が食い違ったら runtime を信じる。

| spawn | 選出 role | direction | 直感的な根拠 |
|---|---|---|---|
| 1st | secretary | vertical | secretary が分割可能サイズを満たすうちは role priority で最優先 |
| 2nd | curator | vertical | secretary が SECRETARY_MIN_WIDTH ガードで脱落、次点 priority の curator が選ばれる |
| 3rd | curator | horizontal | role priority が strict primary なので curator が MIN_PANE を割るまで curator 連続 |

curator が MIN_PANE を割って脱落した後は priority 2 の worker 群に流れる。dispatcher を最後に置く設計意図 (active 監視ペインの viewport を頻繁に半減させない、curator はオンデマンド起動中のみ存在し通常は不在) は runner.py の `_ROLE_PRIORITY` コメントを参照。

### Edge cases / 運用時の注意

- **ワーカーが途中で閉じた後の再派遣**: 旧 k-table 方式で問題になった「閉じた slot を詰めるとテーブル前提と乖離」は rect ベースでは発生しない。常に実レイアウトから target を選ぶため、実際のレイアウト tree と判断が一致する
- **`spawn_claude_pane` エラー**: `[split_refused]` / `[pane_not_found]` が MCP 結果テキストで返る。`references/renga-error-codes.md` の手順でキュレーター → 窓口にエスカレーション (方針は旧設計と同じ)
- **レース**: `list_panes` 実行から `spawn_claude_pane` 実行までに他ワーカーが増減した場合、target 不整合は `[pane_not_found]` として顕在化する。既存のエラーハンドリング経路で吸収する
- **target 選出の責務**: 計算はディスパッチャーが `list_panes` の rect ベースで行う。窓口は DELEGATE メッセージに task_id だけを渡せばよく、target は指定しない

## 運用メモ

- **全ペインを同一タブ内に配置する**: broker surface には `new_tab` / `focus_pane` が無く（意図的除外）、pane 操作（`mcp__org-broker__list_panes` / `send_message` / `inspect_pane` 等）は同一タブ前提で運用するため、ディスパッチャー・キュレーター・全ワーカーを同一タブ内に split で積む。（`ORG_TRANSPORT=renga` では `list_panes` / `focus_pane` / `send_message` / `inspect`（CLI）が現在フォーカス中のタブのペインしか扱えず、`new_tab` でワーカーを別タブに置くとディスパッチャー側から addressable でなくなる制約 — 2026-04-20 判明、renga 本体での解決は suisya-systems/renga#71 — が同じ同一タブ要件を導く）
- **命名規約**:
  - 窓口 → `secretary`
  - ディスパッチャー → `dispatcher`
  - キュレーター → `curator`
  - ワーカー → `worker-{task_id}` (task_id は kebab-case の一意識別子)
  - **org-broker の target 解決ルール**: 全桁数字の name は id として解釈されるため、name には英字を必ず含める (`worker-1` は OK、`1` は id 扱いになるので NG)
- **役割ラベル (`role`)**: `secretary` / `dispatcher` / `curator` / `worker` の 4 種
  - `list_panes` の出力で `role` フィールドが取得でき、組織状態の集計や balanced split の target 選出に使える
- **ワーカー完了時**:
  1. 窓口がディスパッチャーに `CLOSE_PANE` を依頼
  2. ディスパッチャーは `mcp__org-broker__close_pane(target="worker-{task_id}")` でペインを明示破棄する
     (pane が撤去される → `pane_exited` を 1 回 emit → `list_panes` からも消える。
     `[pane_not_found]` / `[pane_vanished]` は「既に閉じた扱い」として skip する)
- **org-suspend 時の停止順**: ワーカー → ディスパッチャー → キュレーター (いずれも `mcp__org-broker__close_pane` で破棄。最後の 1 ペインを閉じるときだけ `[last_pane]` が返るので、そのペインは自分自身で `exit` させる)

## split direction 慣習

分割方向は以下の定義（`spawn_pane` / `spawn_claude_pane` 共通）:
- `direction="vertical"` = 左右分割 (既存ペイン=左、新ペイン=右)
- `direction="horizontal"` = 上下分割 (既存ペイン=上、新ペイン=下)

## 将来機能 / upstream 追跡

- `spawn_pane` / `spawn_claude_pane` の `--ratio 0.2` 等の比率指定 (現状は 50/50 固定)
- `--target-largest` / `--direction auto` 等の renga 側自動 target 選出 (現状はディスパッチャー側で `list_panes` rect から算出。upstream に移譲できれば balanced split ロジックを MCP 側に畳める)
