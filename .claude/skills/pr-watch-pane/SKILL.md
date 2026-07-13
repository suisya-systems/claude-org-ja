---
name: pr-watch-pane
description: >
  PR の CI / マージ監視 (tools/pr-watch.sh) を broker tmux セッション内の専用ペイン
  pr-watch-<PR> で回す。窓口が PR 作成直後に `/pr-watch-pane <PR>` で起動すると、
  ja-root cwd・sandbox 外で監視が走り、/clear や窓口セッション寿命と無関係に継続する。
  pane name で冪等起動 (二重監視しない)、role=watcher で identity 登録、監視終了
  (CI green / PR merged / timeout) でペイン自動 close。Bash tool の background は
  session 寿命依存で長時間 watcher には不適なので本 skill が推奨経路。
  「CI 監視をペインで」「pr-watch をペインで回して」「PR <N> の CI を見張って」等で発動。
effort: low
allowed-tools:
  - Read
  - Bash(git rev-parse:*)
  - Bash(gh repo view:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - mcp__org-broker__list_panes
  - mcp__org-broker__spawn_pane
  - mcp__org-broker__set_pane_identity
  - mcp__org-broker__inspect_pane
  - mcp__org-broker__close_pane
---

# pr-watch-pane: CI / マージ監視を専用ペインで回す

`tools/pr-watch.sh <PR> --repo <owner/repo> --merge-watch` を broker tmux セッション
内の専用ペイン (`name="pr-watch-<PR>"`) で起動する。Bash tool の background 起動は
session 寿命依存で、CI 監視のような長時間 watcher には不適（公式設計の対象外）。broker
ペイン spawn を経由することで **sandbox 外・窓口セッション非依存**（`/clear` や窓口の
context リセットと無関係）に監視が継続し、人間が tmux ペインで直接見えるので可視性も高い
（feedback-dispatcher-visibility 整合）。

監視結果は二経路で残る（どちらも `pr_watch.py` の既存挙動。本 skill は形を変えない）:

- **`.state/state.db` events テーブル** … `ci_completed` / `pr_merge_watch_timeout` の
  canonical event 行（payload 形・`CI_COMPLETED` / `PR_MERGED` / `PR_MERGE_WATCH_TIMEOUT`
  のメッセージ形は不変）。**これが判定の canonical 記録**。
- **`.state/pr-watch-<PR>.log` + tmux スクロールバッファ** … 人間可読の生ログ二段。

> **peer push は best-effort**: `pr_watch.py` は CI 確定・マージ時に窓口へ `CI_COMPLETED` /
> `PR_MERGED` の peer message を送ろうとするが、これは `tools/peer_notify.py` 経由の
> best-effort（broker send CLI 不在 / `ORG_TRANSPORT`・`RENGA_SOCKET` 未設定の pane では
> no-op）。daemon が非既定 state dir（herdr dogfood 等）で動く環境では、pane env に
> `ORG_BROKER_STATE_DIR` が無いと broker send が既定 `.state/broker` を掴んで push が欠落する
> （欠落しても canonical の events DB 行には影響しない）。**待つべき正路は上記 events DB 行と
> 可視ペイン**であり、push の到達を merge gate の
> 前提にしない（org-pull-request の CI/merge gate は events DB を一次ソースにする）。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節。）

> **本 skill の spawn 対象は「汎用 CLI ペイン」**: 起動するのは `tools/pr-watch.sh`
> という shell コマンドであり、Claude セッション（`spawn_claude_pane`）ではなく
> **汎用 `mcp__org-broker__spawn_pane`** を使う。したがって上記ヘッダの **spawn 儀式（broker:
> folder-trust + channel sidecar の 2 段承認 / renga: dev-channel の 1 段承認）は本
> skill では一切発生しない**（`--mcp-config` / `--dangerously-load-development-channels`
> の注入が無く、承認プロンプトが出ない）。attention watcher と同じ CLI ペイン扱いである
> （共有ヘッダ末尾の「attention watcher は ... 2 段承認の対象外」注記と同義）。両系で
> 切り替わるのは **ツール完全修飾名だけ**:
>
> - 既定 broker: `mcp__org-broker__spawn_pane`（`list_panes` / `set_pane_identity` /
>   `inspect_pane` / `close_pane` も同様に `mcp__org-broker__*`）
> - opt-in renga: `mcp__renga-peers__spawn_pane`（同様に `mcp__renga-peers__*`）
>
> 以下の手順は `mcp__org-broker__` を active transport の完全修飾名に読み替えれば両系で論理同一。
> spawn 直後の identity 登録・起動コマンドも両系共通（CLI ペインなので Enter 承認は不要）。

## 前提

- 本 skill は **窓口（secretary）専用**。汎用 `spawn_pane` は broker の auth tier 上
  secretary だけが持つ（dispatcher は `spawn_claude_pane` のみ。contract
  [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
  Surface 8 の ops tier 定義）。
- 窓口の cwd は ja-root（リポジトリ root）。spawn する pr-watch は `.state/` パスや
  `tools/pr-watch.sh` を ja-root 基点で resolve する必要があるため、**cwd trap を skill
  側で吸収する**（Step 1 で `git rev-parse --show-toplevel` の絶対パスを spawn_pane の
  `cwd` に明示。窓口 cwd が何らかの理由で ja-root を外れていても正しく解決される）。
- **前提環境は POSIX/tmux broker（WSL2 / Linux / macOS）**。Step 3 の `command` は `bash`
  実行と、自己 close の `tmux kill-pane -t "$TMUX_PANE"`（**自ペインを `$TMUX_PANE` で明示
  指定して** kill。socket は `$TMUX` 継承をそのまま使い `-L` で固定しない ＝ broker /
  renga / 非既定 socket いずれの transport でも正しい server に当たる。Issue #647 提案 1 の
  「明示 target 指定」を transport-neutral に実装した形）に依存する。Windows native broker（WezTerm の別 GUI
  ウィンドウ・tmux 非経由）では本 skill の自己 close は効かないため、その環境では従来どおり
  人間が `tools/pr-watch.ps1 <PR>` を `!` 経由で起動する手動経路を使う（本 skill はその経路を
  遮断しない＝既存の手動起動経路は不変）。

## Step 1: 引数解決と cwd / repo の確定

1. PR 番号 `<PR>` を引数から取る（必須・正の整数）。省略時はユーザーに PR 番号を尋ねて中断。
2. ja-root の絶対パスと owner/repo を決定的に解決する（cwd trap 吸収 + repo 明示）:

   ```bash
   git rev-parse --show-toplevel                          # -> JA_ROOT（絶対パス）
   gh repo view --json nameWithOwner -q .nameWithOwner    # -> OWNER/REPO
   printenv ORG_TRANSPORT ORG_BROKER_STATE_DIR PATH       # -> transport env（Refs #653 #658）
   ```

   - `gh repo view` が失敗する場合はユーザーに「リポジトリを自動解決できません。
     `--repo OWNER/REPO` を指定してください」と報告して中断。pr-watch.sh 自身も `--repo`
     省略時は auto-resolve するが、ここで明示することで **pane の cwd に依らず repo を固定**
     する（cwd trap が repo 解決にも波及しないようにする）。
   - **transport env の捕捉（Refs #653 #658 — env trap 吸収）**: `printenv` で窓口
     （secretary）ペインの `ORG_TRANSPORT` / `ORG_BROKER_STATE_DIR` / `PATH` を確定させる。
     `mcp__org-broker__spawn_pane` は **汎用 CLI ペイン**を env 注入なしで立てるため（本 skill 上部の
     注記）、これらを Step 3 の `command` に**前置注入しないと** spawn されたペインの
     `tools/peer_notify.py` が `ORG_TRANSPORT` 未設定で silent no-op に落ちる（PR #73 障害の
     直接原因: broker queue に `CI_COMPLETED` が 1 件も入らず窓口が idle のまま気付かなかった）。
     `cwd` が cwd trap を吸収するのと同じ理屈で、この env 捕捉が **env trap** を吸収する。
     `ORG_TRANSPORT` が空 / 未設定（renga 運用既定 or 無設定）なら Step 3 で `export
     ORG_TRANSPORT` を**省く**（未設定のまま → `peer_notify` が renga にフォールバック ＝
     renga opt-in 経路を壊さない）。`ORG_BROKER_STATE_DIR` も set のときだけ前置する。

## Step 2: 冪等チェック（同一 PR の二重監視を防ぐ）

`mcp__org-broker__list_panes` を呼び、`name="pr-watch-<PR>"` の live pane が既に存在するか確認する。

- **存在する** → 既に監視中。**spawn せず**「PR #<PR> の CI 監視ペイン
  (`pr-watch-<PR>`, id={N}) は既に稼働中です」と報告して終了。再起動したい場合は先に
  Step 5 の手順でそのペインを close してから再実行するよう案内する。
- **存在しない** → Step 3 へ。

> pane name `pr-watch-<PR>` が冪等キー。`<PR>` は数字だが `pr-watch-` プレフィックスで
> 英字を含むため許可文字 `[A-Za-z0-9_-]` を満たし、全桁数字（= id 扱いで曖昧化）にもならない。

## Step 3: 監視ペインの spawn

`mcp__org-broker__spawn_pane` で CLI watcher ペインを起動する（`<...>` は Step 1 / 引数で確定した値に置換）:

```
mcp__org-broker__spawn_pane(
  target="dispatcher",
  direction="vertical",
  role="watcher",
  name="pr-watch-<PR>",
  cwd="<JA_ROOT 絶対パス>",
  command="export ORG_TRANSPORT='<ORG_TRANSPORT>'; export ORG_BROKER_STATE_DIR='<ORG_BROKER_STATE_DIR>'; export PATH='<PATH>'; mkdir -p .state; bash tools/pr-watch.sh <PR> --repo <OWNER/REPO> --merge-watch --no-detach 2>&1 | tee -a .state/pr-watch-<PR>.log; tmux kill-pane -t \"$TMUX_PANE\" 2>/dev/null || true"
)
```

- **env 前置注入（Refs #653 #658 — 必須。PR #73 障害の根治）**: `command` 先頭の
  `export ORG_TRANSPORT=...; export ORG_BROKER_STATE_DIR=...; export PATH=...;` は Step 1 で
  `printenv` 捕捉した窓口ペインの実値に置換する。これが無いと汎用 spawn ペインは transport
  env を継承せず、`peer_notify` の broker/renga 経路がどちらも未設定分岐に落ちて **push が
  silent no-op** になる（events テーブルへの `ci_completed` 書き込みは成功するのに窓口へ届かない
  ＝ まさに PR #73 の障害）。`PATH` 注入は broker 経路が shell out する `claude-org-runtime
  broker send` CLI（venv 内）を pane の PATH で解決可能にするため。
  - **条件付き前置（renga opt-in を壊さない）**: `ORG_TRANSPORT` が空 / 未設定なら
    `export ORG_TRANSPORT=...` を**丸ごと省く**（未設定のまま渡す ＝ `peer_notify` が renga に
    フォールバック）。`ORG_BROKER_STATE_DIR` も set のときだけ前置する。空値を `''` で明示
    export すると broker 経路が既定 state dir を掴む誤動作を招くため、**空なら export 文自体を
    書かない**。
  - **多層防御の位置づけ**: この env 注入は **path A（低遅延 push）の修理**であって単独の保証では
    ない。仮に注入漏れ / push 失敗が起きても、(B) ディスパッチャーの `event_deliveries` outbox
    relay が `ci_completed` 等の canonical event を直接 scan して窓口へ確実に relay するため
    「見逃しゼロ」は成立する（[`.dispatcher/references/worker-monitoring.md`](../../../.dispatcher/references/worker-monitoring.md) の relay scan ステップ参照）。
    さらに push が失敗した場合は `pr_watch` が `notify_failed` イベントを fail-loud で記録する
    （silent no-op の全廃）。

- `target="dispatcher"`: 同一タブ scope の安定アンカー（attention watcher と同じく
  dispatcher を split 起点にする）。broker では各ペインが detached 独立セッションだが、
  addressing scope（同一タブ MUST、contract Surface 4.2）を満たすため既存ペインを起点に取る。
- `role="watcher"`: list_panes で監視ペインを識別する表示ラベル（attention watcher の
  `role="attention"` と同じく canonical 4 role 以外のラベル。broker では token の auth tier
  は spawn 時に固定で、role ラベルは tier を変えない — Surface 8）。
- `cwd`: **Step 1 で解決した JA_ROOT 絶対パス**。これにより pane 内の `tools/pr-watch.sh` /
  `.state/pr-watch-<PR>.log` が ja-root 基点で resolve される（cwd trap 吸収）。
- **`--no-detach` 必須（Issue #650）**: `tools/pr-watch.sh` は Issue #641 対策で既定
  setsid + nohup の自己 detach をするため、`--no-detach` を付けないと spawn 直後に親 bash が
  exit して broker pane が即時掃除され、watcher が孤児化する（pane が無いので `/org-attach` /
  `Ctrl-b s` でも覗けない）。`--no-detach` で前景動作させ、`tee` と末尾 self-close の
  自己終了サイクルを成立させる。
- `command`: pr-watch を前景実行し、stdout/stderr を `.state/pr-watch-<PR>.log` に `tee -a`
  （tmux スクロールバッファにも出る二段）。先頭 `mkdir -p .state` で fresh clone でも tee の
  出力先を確保する。pr-watch 終了後に `tmux kill-pane -t "$TMUX_PANE"`
  で **ペインを自己 close**（監視終了で自動 close。`|| true` で tmux 不在環境でも握り潰す）。
  - **`$TMUX_PANE` は placeholder ではなく実行時 env**: `<PR>` / `<OWNER/REPO>` と違い
    `$TMUX_PANE` は窓口が値に置換してはならない。spawn されたペインの shell に自動露出する
    自ペイン id（例 `%16`）で、self-close 時に **その pane 自身**を明示 target 解決する。
    旧形 `tmux kill-pane`（target 無指定）は現在ペインを暗黙推定するが、`-t "$TMUX_PANE"` で
    明示することで曖昧さを排す（Issue #647 提案 1 の「明示 target 指定」）。
  - **socket は `$TMUX` 継承をそのまま使う（`-L` で固定しない）**: `tmux` を `-L` 無しで
    起動すると、ペインが属する tmux server の socket を `$TMUX` から自動解決する。broker では
    それが `claude-org-broker` socket、opt-in renga や非既定 socket 構成では別 socket になる
    が、いずれも `$TMUX` が正しい server を指すので kill が当たる。ここで `-L claude-org-broker`
    と固定すると renga / 非既定 socket 下で **別 server に当たって self-close が無言で失敗**
    するため、socket は固定せず transport-neutral に保つ。
  - **self-close は tmux 層だけを掃除する**: `kill-pane` は broker socket 上の実 tmux ペイン
    を消すので、直後から `list_panes` には現れなくなる。ただし broker daemon の pane 登録簿
    （name binding）は self-close では pop されず **stale に残りうる**（daemon は自 pane の外部
    kill を検知しないため）。stale binding が残ると同名 `pr-watch-<PR>` の再 spawn が
    `[name_taken]` で弾かれる（`list_panes` には出ないのに、が症状）。この掃除は Step 5 の
    手動 fallback（`mcp__org-broker__close_pane(target="pr-watch-<PR>")` が登録簿を pop）で行う。
  - `--merge-watch`: CI green で `CI_COMPLETED` を出した後もマージまで poll し続け（最大
    24h）、マージで `PR_MERGED`、timeout で `PR_MERGE_WATCH_TIMEOUT` を出してから自己 close
    する。CI 確定だけで止めたい場合は呼び出し時に `--merge-watch` を外す（その場合は CI green
    / failed 確定で自己 close）。
  - pr-watch.sh の既存メッセージ形・event payload 形は **本 skill では一切変更しない**（不変条件）。
    既存の手動 `tools/pr-watch.sh` 起動経路（人間の `!` 経由など）も従来どおり動作する。

戻り値 `"Spawned pane id=N."` の N を控える。

**spawn 失敗時の分岐**（MCP 結果テキストの `[<code>]` で判定。詳細は
[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md)）:

- `[split_refused]`（broker free-pane なし / MIN_PANE 割れ）→「監視ペインを作る空きが
  ありません（ターミナルが狭い / pane 上限）。ターミナルを広げるか不要ペインを閉じてから
  再実行してください」と報告して中断。
- `[pane_not_found]`（`target="dispatcher"` 不在）→「dispatcher ペインが見つかりません。
  `/org-start` を先に実行してください」と報告して中断。
- `[name_in_use]` / `[name_taken]` → **live pane と stale 登録簿を切り分ける**（前 watcher の
  self-close で tmux ペインは消えたが broker 登録簿に name binding が残る「二層の不整合」を
  自己回復する）:
  1. `mcp__org-broker__list_panes` で `name="pr-watch-<PR>"` の **live pane が実在するか**再確認する。
  2. **live pane が在る** → 冪等チェックを取りこぼした真の race。既に監視中として Step 2 の
     「既に稼働中」報告に倒す（新規 spawn しない）。
  3. **live pane が無い**（`list_panes` に出ない）→ self-close 済みの **stale 登録簿 binding**。
     `mcp__org-broker__close_pane(target="pr-watch-<PR>")` で name 解決させて登録簿を pop し
     （`ok closed=%N` が返る。`[pane_not_found]` は既に掃除済みで OK）、**Step 3 の spawn を
     1 度だけ再試行**する。再試行でも `[name_taken]` が続く場合はユーザーに報告して中断
     （想定外の登録簿状態）。
- broker 固有（`[no_backend]` / `[token_invalid]` / `[session_invalid]` /
  `[tool_not_authorized]` / `[peer_not_found]` 等）/ その他未知コード → 状況をユーザーに
  報告して中断（default-branch escalate）。

## Step 4: identity 登録と起動 health check

1. **identity 確定（role=watcher の登録）**: spawn_pane の `name` / `role` が反映されたか
   `mcp__org-broker__list_panes` で確認する。`name="pr-watch-<PR>"` かつ `role="watcher"` が付いて
   いれば登録済み。万一いずれかが欠ける backend では
   `mcp__org-broker__set_pane_identity(target=<N>, name="pr-watch-<PR>", role="watcher")` で補修する。
   - 本ペインは Claude セッションではなく CLI プロセスなので **Claude peer（peer_id）は
     持たない**。ここでの「peer 登録」= pane registry への name + role=watcher の登録
     （spawn_pane / set_pane_identity が行う）を指す。MCP peer / dev-channel 登録は発生しない。
2. **即時クラッシュ検出（negative-signal のみ）**: `mcp__org-broker__inspect_pane(target=<N>,
   format="text", lines=40)` で出力を覗き、以下のいずれかを検出した場合のみ「起動失敗」と判定:
   - `command not found` / `is not recognized` / `No such file or directory`
   - `gh: ... not found` 等 gh 不在 / `Traceback (most recent call last)` / `ModuleNotFoundError`
   - 出力末尾に shell prompt（`$ ` / `% ` 末尾露出）= command が即時終了して shell に戻った
   - 上記いずれも無ければ（pr-watch の watch ループ出力 / 空 / 起動直後の静止）**起動成功扱い**
     とし、固定 sleep を入れて再 inspect する経路は持たない（健全な quiet start を誤殺しない）。
   - 起動失敗時は `mcp__org-broker__close_pane(target=<N>)` で死んだペインを掃除し、原因（`tools/pr-watch.sh`
     / `gh` の導入、cwd）をユーザーに報告して中断。

## Step 5: 監査記録と報告 / 手動 close

1. journal に起動を best-effort で記録する:

   ```bash
   bash tools/journal_append.sh pr_watch_pane_started pr=<PR> repo=<OWNER/REPO> pane_id=<N>
   ```

   Windows native では `py -3 tools/journal_append.py pr_watch_pane_started pr=<PR> repo=<OWNER/REPO> pane_id=<N>`。

2. ユーザーに報告する:

   ```
   PR #<PR> の CI / マージ監視ペイン pr-watch-<PR> (id={N}) を起動しました。
   - ログ: .state/pr-watch-<PR>.log（tmux スクロールバッファにも出力）
   - 監視終了（マージ / CI 失敗確定 / timeout のいずれか）でペインは自動で閉じます。
     確定した CI 判定は `.state/state.db` の `ci_completed` 行とログに残るので、
     ペインが閉じても判定は失われません（merge gate はそこを読む）。
   - tmux で直接見るには `/org-attach` のコマンドを使ってください。
   ```

3. **手動 close（自動 close されないケースの掃除、Issue #647 提案 3）**: self-close は tmux
   ペインを消すだけで broker 登録簿の name binding は残りうる（前掲「self-close は tmux 層
   だけを掃除する」）。二つのケースに分けて掃除する:

   - **(a) tmux ペインが live のまま残った / 監視を途中で止めたい**: `mcp__org-broker__list_panes` で
     `name="pr-watch-<PR>"` の live pane を確認し、その **数値 pane_id** で
     `mcp__org-broker__close_pane(target=<N>)` する（`[pane_not_found]` / `[pane_vanished]` は既に
     閉じた扱いで skip）。live pane が在るこのケースでは、name 指定ではなく list_panes で
     identity を確認した数値 pane_id を使う（id recycle 時の誤 close を避ける）。

   - **(b) stale 登録簿 binding（`list_panes` には出ないのに再 spawn が `[name_taken]`）**:
     self-close で tmux ペインは消えたが broker 登録簿に name binding が残っている状態。
     数値 pane_id は list_panes に出ないので取得できないため、**name 指定**で
     `mcp__org-broker__close_pane(target="pr-watch-<PR>")` する（broker が name → stale pane_id を解決し
     登録簿を pop、`ok closed=%N` が返る。`[pane_not_found]` は既に掃除済みで OK）。掃除後は
     同名 spawn が通る。Step 3 の `[name_taken]` 分岐はこの (b) を自己回復するが、手動でも
     同手順で掃除できる。
