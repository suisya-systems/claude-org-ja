---
name: pr-watch-pane
owner: secretary
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
- **自己 close の実装は tmux backend 前提**。Step 3 の `command` は `bash` 実行と、自己
  close の `tmux kill-pane -t "$TMUX_PANE"`（**自ペインを `$TMUX_PANE` で明示指定して** kill。
  socket は `$TMUX` 継承をそのまま使い `-L` で固定しない ＝ broker / renga / 非既定 socket
  いずれの transport でも正しい server に当たる。Issue #647 提案 1 の「明示 target 指定」を
  transport-neutral に実装した形）に依存する。
- **自己 close は tmux backend の低遅延経路であり、backend 依存で効き方が変わる（herdr
  ゾンビ残留の根治, Issue #751）**。broker は backend として tmux / herdr / wezterm を取りうる
  （解決済み backend は `daemon.json` sidecar が持つ。renga opt-in は常に tmux。契約は
  [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
  Surface 8）:
  - **tmux backend**: Step 3 末尾の `tmux kill-pane -t "$TMUX_PANE"` が実ペインを即座に消す
    ＝ **監視終端で watcher ペインが自動 close される低遅延経路**（従来挙動。温存する）。
  - **herdr / wezterm backend（Windows native broker の別 GUI ウィンドウ・tmux 非経由を含む）**:
    watcher ペインは tmux ペインではないため `tmux kill-pane` が **no-op**（`|| true` で握り潰され
    silent）。self-close が効かず、**監視終了後もペインがゾンビとして残留する**（実測: 2026-07-22
    に PR #154 / #749 / #750 の watcher 3 枚が herdr backend で残留し、`close_pane`(id 指定) で
    掃除した）。この backend では自己 close に頼れないため、**監視終端で窓口がイベント駆動で
    watcher ペインを close する経路が正路**になる
    （[`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md) の post-merge
    cleanup / `PR_MERGE_WATCH_TIMEOUT` / CI 失敗確定の各終端で窓口が発火。掃除手順は下記 Step 5 の
    (a)/(b) split に従う ＝ live pane は list_panes 確認済みの数値 pane_id で close、stale binding
    のみ broker 条件付き allowlist で name 指定）。`close_pane` は transport 抽象上 tmux / herdr 両対応で、tmux backend で既に
    self-close 済みなら `[pane_not_found]` が返る（自己クローズ済みで正常）。Windows native の手動起動経路
    （人間が `tools/pr-watch.ps1 <PR>` を `!` 経由で起動）も従来どおり遮断しない（既存経路は不変）。

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
  command="nocorrect true 2>/dev/null || true; export ORG_TRANSPORT='<ORG_TRANSPORT>'; export ORG_BROKER_STATE_DIR='<ORG_BROKER_STATE_DIR>'; export PATH='<PATH>'; mkdir -p .state; bash tools/pr-watch.sh <PR> --repo <OWNER/REPO> --merge-watch --no-detach 2>&1 | tee -a .state/pr-watch-<PR>.log; tmux kill-pane -t \"$TMUX_PANE\" 2>/dev/null || true"
)
```

- **`nocorrect true 2>/dev/null || true;` 前置（Issue #825 — zsh correctall 無言停止の根治。必須・置換禁止）**:
  `command` は対話シェルに打鍵される形で実行されるため、pane の login shell が zsh で
  スペル自動訂正（`setopt correctall`）が有効だと、残留 `.state/pr-watch-*.log` に類似した
  新規ログ名が `zsh: correct '...' to '...' [nyae]?` の確認プロンプトを発火させ、
  **パイプライン全体が一度も走らないまま無言停止**する（2026-08-06 の PR #824 監視で実発生・
  再現確認済み）。行頭の `nocorrect` は zsh の precommand modifier（reserved word）で、
  パース前に解釈され**その行全体の全 word のスペル訂正を無効化**する（`;` 連結の後続コマンド
  にも効く。zshmisc PRECOMMAND MODIFIERS「interpreted immediately, before any parsing is
  done」。tmux 上の対話 zsh 5.9 で `;` 越しの抑止を実機確認済み）。
  - **payload を持たない犠牲文として独立させる理由（非 zsh 互換）**: `nocorrect` は zsh 以外の
    シェルにコマンドとして存在しない。`nocorrect export ...` と payload に直結すると、bash 等が
    login shell の pane では `nocorrect: command not found` で **export 文ごと失敗**し、transport
    env 注入（次項）が silent に失われる（bash 実機で export 消失を確認済み）。単独文
    `nocorrect true 2>/dev/null || true;` なら zsh では行全体の訂正抑止・bash 等では犠牲文
    だけが無音で失敗し、`;` 以降の後続は全て実行される。非対話シェル実行（`zsh -c` 等）でも
    no-op で副作用はない（zshmisc「It has no effect in non-interactive shells」）。
  - **`2>/dev/null` は省略不可（Step 4 の誤殺防止）**: 非 zsh シェルでは犠牲文が
    `nocorrect: command not found` を stderr に出す。この行が画面に残ると Step 4 の
    negative-signal 判定（`command not found` = 起動失敗）に誤マッチし、**正常起動した
    watcher を健全なまま close する false positive** になるため、犠牲文の stderr は行内で
    捨てる（bash で無音 + 後続 export 生存、zsh 対話で correctall 抑止に影響なしを実機確認済み）。
  - **`|| true` も省略不可（errexit 耐性）**: 非 zsh シェルの rc で `set -e`（errexit）が
    有効だと、犠牲文の失敗（status 127）でシェルごと終了し **export / mkdir / watcher 本体が
    一切走らないまま pane が死ぬ**。`|| true` で犠牲文を非致命化する（errexit 有効 bash で
    後続実行の生存、zsh 対話で correctall 抑止に影響なしをともに実機確認済み）。
  - `<...>` placeholder ではない**固定文字列**であり、窓口は値に置換せずそのまま残す。下記の
    条件付き前置で export 文を省いた場合も、`nocorrect true 2>/dev/null || true;` は
    **常に行頭に残す**。

- **env 前置注入（Refs #653 #658 — 必須。PR #73 障害の根治）**: 犠牲文に続く
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
  - **`Ctrl-b s` は tmux prefix の既定値前提**: prefix を変更している場合は設定した prefix に
    読み替える。さらに、**覗く端末が renga の場合はこの打鍵が届かない** — renga の org
    サイドバーは既定で有効なあいだ `Ctrl+B`（tmux prefix `Ctrl-b` と同じ物理入力）を消費して
    PTY へ渡さないため、pane が生きていても `Ctrl-b s` でセッション切替できない。回避策
    （renga 設定 `[ui] org_sidebar = "off"` または tmux prefix の変更）は
    [`docs/operations/dispatcher-view.md`](../../../docs/operations/dispatcher-view.md) の
    「外側フレームが renga の場合」を参照。
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
    手動 fallback で行う。**そこで撃つ `mcp__org-broker__close_pane(target="pr-watch-<PR>")` は、
    数値 pane_id を列挙から取り直せないこの状態に限って裸 name を許可する transport 条件付きの
    allowlist 経路**であり、無条件の name 指定ではない（3 条件と broker 以外での扱いは Step 5 の
    (b) が SoT）。
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
     この状態は列挙にペインが出ないため **数値 pane_id を取得できず**、Group B の
     「数値 pane_id で撃つ」原則をそのまま適用できない。よってこの掃除だけは **transport 条件付きの
     allowlist** として裸 name の `close_pane` を許可する（契約
     [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
     T-§4.2 の Group B 台帳が stale-binding 行に求める「使った mechanism」がこれ。条件の並びと
     根拠の SoT は下記 Step 5 の (b)）。**以下 3 条件がすべて成立するときだけ許可される**:
     - **(1) いま Group B を駆動している backend が `close_pane` / `set_pane_identity` を自身の
       single-tab モデル内で解決する**（＝ `org-broker`）— 誤タブ hazard が構造的に生じない
       （契約 §8.1 / §8.10）。判定は本 skill の transport 解決（`ORG_TRANSPORT` の明示値 >
       既定 `DEFAULT_TRANSPORT`。[`tools/transport.py`](../../../tools/transport.py)）に従い、
       **Step 1 で `printenv` した raw env 文字列の有無では判定しない**（`DEFAULT_TRANSPORT` は
       runtime 0.1.28 で broker にフリップ済みなので、無設定は broker に解決する）。
       確定できない場合は不成立として扱う（判定規則と根拠の SoT は下記 Step 5 の (b)）
     - **(2) 再 spawn が `[name_in_use]` / `[name_taken]` で弾かれている** — stale binding の症状
       （本分岐の発火条件そのもの）
     - **(3) その name が `mcp__org-broker__list_panes` に現れない** — live pane 不在（上記 1. の再確認結果）
     - **3 条件が揃う場合**: `mcp__org-broker__close_pane(target="pr-watch-<PR>")` で name 解決させて登録簿を
       pop し（`ok closed=%N` が返る。`[pane_not_found]` は既に掃除済みで OK）、**Step 3 の spawn を
       1 度だけ再試行**する。再試行でも `[name_taken]` が続く場合はユーザーに報告して中断
       （想定外の登録簿状態）。
     - **broker 以外に解決する場合（`ORG_TRANSPORT=renga` の opt-in など）では close せず
       報告して中断する**: pre-capability
       renga の legacy 解決は active タブ（＝ユーザーが見ているタブ）を先に引き、miss したら
       他タブを index 順にフォールスルーして先勝ちする。「live pane が無い」という前提は
       `list_panes`（＝ユーザー可視タブ）からしか立てられないため、別タブに同名の live pane が
       居ると前提が偽のまま close が当たる（`close_pane` は不可逆）。自動再試行もせず、
       stale binding を検出したが transport が broker でないため自動掃除しない旨をユーザーに
       報告して指示を仰ぐ。
- broker 固有（`[no_backend]` / `[token_invalid]` / `[session_invalid]` /
  `[tool_not_authorized]` / `[peer_not_found]` 等）/ その他未知コード → 状況をユーザーに
  報告して中断（default-branch escalate）。

## Step 4: identity 登録と起動 health check

1. **identity 確定（role=watcher の登録）**: spawn_pane の `name` / `role` が反映されたか
   `mcp__org-broker__list_panes` で確認する。`name="pr-watch-<PR>"` かつ `role="watcher"` が付いて
   いれば登録済み。万一いずれかが欠ける backend では
   `mcp__org-broker__set_pane_identity(target=<N>, name="pr-watch-<PR>", role="watcher")` で補修する。
   - **補修も Group B なので自タブ確立が先（契約 T-§4.2「Fail-safe consequence for Group B」。
     [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)）**:
     `set_pane_identity` は `close_pane` と同じ Group B で、宛先は「**自タブと独立に確立済みの
     列挙**から得た数値 pane id」に限られる。数値であることは MUST の**片方**にすぎず、**id が
     spawn の戻り値として手元にあることも免除にならない**（未確立の列挙では、直前の `name` /
     `role` 確認の結果そのものを信用できないため）。確立手段は 2 つだけで、いずれか 1 つが成立
     すればよい: **(i) backend が Group B を自身の単一タブモデル内で解決する**（`org-broker`。
     契約 §8.1 / §8.10）/ **(ii) `caller_scope` を確立できている**（契約 T-§cap。
     `caller_scope_close_identity` から導出しない）。**どちらも成立しないなら補修を撃たず**、
     identity が未登録のまま監視が走っていること（Step 2 の冪等チェックと Step 5 の cleanup が
     `name` / `role` で引けなくなること）をユーザーに報告する。`target="focused"` や裸 name の
     相対セレクタへは**フォールバックしない** — `set_pane_identity` の `target` は既定が
     `"focused"` なので、人間がフォーカスを移していれば**別ペインを改名する**。
   - 本ペインは Claude セッションではなく CLI プロセスなので **Claude peer（peer_id）は
     持たない**。ここでの「peer 登録」= pane registry への name + role=watcher の登録
     （spawn_pane / set_pane_identity が行う）を指す。MCP peer / dev-channel 登録は発生しない。
2. **即時クラッシュ / 対話プロンプト停止の検出（negative-signal のみ）**:
   `mcp__org-broker__inspect_pane(target=<N>, format="text", lines=<ペイン高さ以上>, include_cursor=true)`
   で出力を**全画面**読む（Issue #825）。`lines` は `mcp__org-broker__list_panes` の geometry で分かる
   ペイン高さ以上、高さが取れなければ余裕を取って `lines=200` を指定する。末尾 15-40 行だけの
   読み取りは、画面**上部**に出た確認プロンプト + 下部空白を「静かな正常起動」と誤読する
   （2026-08-06 の correctall 停止はこの誤読で見逃された）。`include_cursor=true` は下記
   「行末 `?` + 入力待ち」判定に必要なカーソル位置を返させるため必須（既定 false では
   カーソル情報が返らず、ブロック中のプロンプトと通常出力を区別できない）。
   以下のいずれかを検出した場合のみ「起動失敗」と判定:
   - `command not found` / `is not recognized` / `No such file or directory`
   - `gh: ... not found` 等 gh 不在 / `Traceback (most recent call last)` / `ModuleNotFoundError`
   - 出力末尾に shell prompt（`$ ` / `% ` 末尾露出）= command が即時終了して shell に戻った
   - **対話プロンプト形跡（Issue #825 で追加）**: `[nyae]?`（zsh correctall の
     `zsh: correct '...' to '...' [nyae]?`）/ `(y/n)` / `[y/N]` のリテラル、または行末が `?` で
     終わる行に `cursor` が乗ったまま（= その行で入力待ち）後続出力が無い。いずれもコマンドが
     **一度も走らないまま**シェル層の確認プロンプトで停止している形跡で、放置すると監視ゼロの
     無言死になる（correctall 停止なら Step 3 の `nocorrect` 前置漏れを疑う）。
   - 上記いずれも無ければ（pr-watch の watch ループ出力 / 空 / 起動直後の静止）**起動成功扱い**
     とし、固定 sleep を入れて再 inspect する経路は持たない（健全な quiet start を誤殺しない）。
   - 起動失敗時は死んだペインを掃除し、原因（`tools/pr-watch.sh` / `gh` の導入、cwd、対話プロンプト
     停止）をユーザーに報告して中断する。掃除は **自タブ確立 → identity 再確認 → 数値 close** の
     順で行う:
     1. **自タブ確立**（契約 T-§4.2「Fail-safe consequence for Group B」。
        [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)）:
        **(i) backend が Group B を自身の単一タブモデル内で解決する**（`org-broker`。契約
        §8.1 / §8.10）/ **(ii) `caller_scope` を確立できている**（契約 T-§cap）のいずれか 1 つが
        成立すればよい。**どちらも成立しないなら close を撃たず**、死んだペイン (id=`<N>`) が
        残る旨を報告に含めて手動掃除に委ねる（相対セレクタへはフォールバックしない）。**id が
        spawn の戻り値として手元にあることは免除にならない** — 未確立の列挙では次の identity
        照合の結果そのものを信用できないため
     2. **identity 再確認（pane_id recycle 対策。他の全経路と揃える）**: `mcp__org-broker__list_panes` で
        `<N>` のレコードを引き、`name="pr-watch-<PR>"` かつ `role="watcher"` を**なお指している**
        ことを確かめる。別ペインへ再割当て済み / 列挙に現れない場合は **close せず**、その旨を
        報告に含める（本 Step の「identity 確定」で補修を撃てなかった backend ではこの照合が
        通らないので、同じく close せず報告に倒す）
     3. 両方を通ったときだけ `mcp__org-broker__close_pane(target=<N>)` する（`[pane_not_found]` /
        `[pane_vanished]` は直前に消えた扱いで skip）

3. **生存確認は副作用ベースで行う（`pgrep -f` 単独判定の禁止、Issue #825）**: 起動後に
   「監視が本当に走っているか」を確かめるときは、プロセス一覧ではなく**副作用**を見る:
   - **`pgrep -f "pr-watch"` 単独を生存判定に使わない**。spawn されたペインのラッパーシェルの
     argv にはコマンド全文（`pr-watch.sh` を含む）が乗るため、確認プロンプト停止中で
     パイプラインが一度も走っていなくても**シェル自身にマッチして偽陽性**になる（2026-08-06 に
     この誤認で「監視本体が稼働中」と誤読した）。
   - 一次の副作用は **`.state/pr-watch-<PR>.log` の生成**: Step 3 の `tee -a` はパイプライン
     開始と同時にログファイルを open/生成するため、fresh なログ名なら「ファイルが出来ている」＝
     パイプラインが実際に走った確証になる（プロンプト停止ではコマンド自体が実行されないので
     ログは生成されない）。同名の残留ログがある再監視では生成有無で判別できないため、上記 2 の
     全画面 inspect（プロンプト形跡なし）と組み合わせて判定する。
   - 判定確定の副作用は `.state/state.db` events テーブルの `ci_completed` 等の canonical
     event 行（本 skill 冒頭の二経路）。なお `pr_watch.py` は起動 banner を出力しないため、
     ログが空のまま静止していても quiet start でありうる（誤殺しない）。

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
   - 監視終了（マージ / CI 失敗確定 / timeout のいずれか）で、tmux backend ではペインが
     自動で閉じます。herdr / wezterm backend では自己 close が効かず残留するため、窓口が
     監視終端でイベント駆動 close します（org-pull-request の各終端処理）。
     確定した CI 判定は `.state/state.db` の `ci_completed` 行とログに残るので、
     ペインが閉じても判定は失われません（merge gate はそこを読む）。
   - tmux で直接見るには `/org-attach` のコマンドを使ってください。
   ```

3. **監視終端クローズ / 手動 close（Issue #647 提案 3 / Issue #751）**: self-close の効き方は
   backend 依存（前掲「前提」節）。**herdr / wezterm backend では self-close が no-op で watcher
   ペインが必ず残留する**ため、監視終端（PR_MERGED / PR_MERGE_WATCH_TIMEOUT / CI 失敗確定）で
   窓口がイベント駆動で watcher ペインを掃除するのが正路になる
   （[`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md) の各終端処理で発火）。
   tmux backend では self-close が tmux ペインを消すが broker 登録簿の name binding は残りうる
   （前掲「self-close は tmux 層だけを掃除する」）。掃除は下記 (a)/(b) の split に従う。**窓口の
   イベント駆動 close（監視終端）では、下記に加えて「spawn 時に控えた pane_id + 監視対象 head に束縛し、
   終端イベントの head が追跡中 instance と一致するときだけ close する」freshness gate を必ず併用する**
   （終端イベントが遅延 / 重複配送されて同一 PR の watcher が既に再起動済みの場合、`name` で live pane を
   再導出すると新 watcher を解決してその数値 pane_id を close しても replacement monitor を誤 close する。
   束縛の SoT は [`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md) の該当節）。
   人間が現ペインを手動 close する (a)/(b) は現 instance が対象で自明なので freshness gate は不要:

   - **(a) tmux ペインが live のまま残った / 監視を途中で止めたい**: **まず照合に使う
     `mcp__org-broker__list_panes` の列挙を自タブのものと確立する**（契約
     [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
     T-§4.2「Fail-safe consequence for Group B」）。確立手段は 2 つだけで、いずれか 1 つが成立
     すればよい: **(i) backend が Group B を自身の単一タブモデル内で解決する**（`org-broker`。
     契約 §8.1 / §8.10）/ **(ii) `caller_scope` を確立できている**（契約 T-§cap。
     `caller_scope_close_identity` から導出しない）。**どちらも成立しないなら close を撃たず**、
     watcher ペインが残る旨をユーザーに報告して停止する（相対セレクタへはフォールバックしない）。
     確立できたら `name="pr-watch-<PR>"` の live pane を確認し、その **数値 pane_id** で
     `mcp__org-broker__close_pane(target=<N>)` する（`[pane_not_found]` / `[pane_vanished]` は既に
     閉じた扱いで skip）。name 指定ではなく list_panes で identity を確認した数値 pane_id を
     使う（id recycle 時の誤 close を避ける）。
     - **(a) は「live pane が列挙に出るから安全」ではない**: (b) が carve-out を要するのは
       *列挙から数値 id を取り直せない*ためであって、(a) が数値 id を取れることは MUST の
       **片方**（相対セレクタでないこと）を満たすにすぎない。もう片方の「その列挙が自タブの
       ものか」は (a) でも別に確立しなければならず、未確立の列挙では `name` / `role` 照合の
       **結果そのもの**を信用できない（pre-capability の renga では `list_panes` が
       **フォーカス中**のタブに解決し、`pr-watch-<PR>` は 2 org 並走で構造的に衝突するので、
       照合を通っても**別 org の同名 watcher**を閉じうる）。

   - **(b) stale 登録簿 binding（`list_panes` には出ないのに再 spawn が `[name_taken]`）**:
     self-close で tmux ペインは消えたが broker 登録簿に name binding が残っている状態。
     ペインが列挙に出ないので **数値 pane_id を取得できず**、(a) の「list_panes で identity 照合
     した数値 pane_id で撃つ」形が原理的に取れない。この一点に限り **transport 条件付きの
     allowlist** として裸 name の `close_pane` を許可する（**本節が 3 条件と根拠の SoT**。
     契約 [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
     T-§4.2 の Group B 台帳は stale-binding 行を「数値化だけでは discharge できない」とし、
     follow-up が **使った mechanism を台帳に記録する**ことを求めている。本 skill が採る mechanism が
     この allowlist）。**以下 3 条件がすべて成立するときだけ許可される**:
     - **(1) いま Group B を駆動している backend が `close_pane` / `set_pane_identity` を
       自身の single-tab モデル内で解決する**（＝ `org-broker`）— そのモデル内で name も
       解決されるため誤タブ hazard が構造的に生じない（契約 §8.1 / §8.10。契約 T-§4.2 の
       carve-out 自体も条件を "the backend resolves Group B in a single-tab model" と
       **backend の性質**で書いており、env 変数の綴りでは書いていない）。**判定は本 skill が
       既に持つ transport 解決に従う**: `ORG_TRANSPORT` の明示値 > 既定 `DEFAULT_TRANSPORT`
       （[`tools/transport.py`](../../../tools/transport.py) の `resolve()` と冒頭 docstring。
       `DEFAULT_TRANSPORT` は runtime 0.1.28 で `renga` → `broker` にフリップ済みなので、
       **`ORG_TRANSPORT` 無設定のデプロイは broker に解決する**）。**raw env 文字列の有無で
       判定しない** — 「空 / 未設定だから renga」と読むと、いちばん一般的な無設定構成で
       この自己回復経路が原理的に発火せず毎回人手待ちになる。Step 1 が `printenv` の結果で
       `export ORG_TRANSPORT` を省くのは [`tools/peer_notify.py`](../../../tools/peer_notify.py)
       が raw env で分岐する helper だから（`ORG_TRANSPORT == "broker"` のときだけ broker 経路、
       それ以外は未設定を含めて renga 経路）で、判定基準の異なる別論点である
     - **確定できないときは carve-out を取らない**（fail-safe）: `ORG_TRANSPORT` に未知値が
       入っていて解決が `ValueError` になる等で「いま何が駆動しているか」を確定できない場合は、
       条件 (1) を**不成立**として扱い、下の「broker 以外」と同じくユーザーに報告して指示を仰ぐ
     - **(2) 再 spawn が `[name_in_use]` / `[name_taken]` で弾かれている** — stale binding の症状
     - **(3) その name が `mcp__org-broker__list_panes` に現れない** — live pane 不在（＝列挙から数値
       pane_id を取れない、この allowlist が必要になっている当の条件）
     - **3 条件が揃う場合**: `mcp__org-broker__close_pane(target="pr-watch-<PR>")` する（broker が name →
       stale pane_id を解決し登録簿を pop、`ok closed=%N` が返る。`[pane_not_found]` は既に
       掃除済みで OK）。掃除後は同名 spawn が通る。Step 3 の `[name_taken]` 分岐はこの (b) を
       自己回復するが、手動でも同手順で掃除できる。
     - **broker 以外に解決する場合（`ORG_TRANSPORT=renga` の opt-in など）では裸 name に
       フォールバックしない**: pre-capability
       renga の legacy 解決は active タブ（＝ユーザーが見ているタブ）を先に引き、miss したら
       他タブを index 順にフォールスルーして先勝ちする。したがって「live pane が無いので誤 close の
       余地が無い」という前提自体が `list_panes`（＝ユーザー可視タブ）からしか立てられず、別タブに
       同名の live pane が居れば前提は偽で、close はそのペインに当たる（`close_pane` は不可逆で
       エラーも出ない）。この経路では close せず、stale binding を検出した旨と `pr-watch-<PR>` の
       再 spawn が弾かれる状態であることをユーザーに報告して指示を仰ぐ。
     - **控えた pane_id での close にも倒さない**: `list_panes` で再確認できない retained id を
       close に使うのは (a) が防いでいる pane_id recycle hazard を素通りさせるため、本 skill は
       採らない（`[name_taken]` が続く場合と同じくユーザー報告に倒す）。
