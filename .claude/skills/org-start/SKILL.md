---
name: org-start
description: >
  組織を起動する。前回の状態を読み込んでブリーフィングし、
  ディスパッチャーペインを起動する。ClaudeCode起動直後に1回実行する。
  「起動して」「スタート」「始めて」等でも発動。
effort: low
allowed-tools:
  - Read
  - Bash(echo:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(python3 tools/journal_append.py:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python3 -m tools.state_db.importer:*)
  - Bash(py -3 -m tools.state_db.importer:*)
  - Bash(py -3 dashboard/org_state_converter.py:*)
  - Bash(python3 dashboard/org_state_converter.py:*)
  - Bash(py -3 dashboard/server.py:*)
  - Bash(python3 dashboard/server.py:*)
  - Bash(python3 tools/stop_dashboard.py:*)
  - Bash(py -3 tools/check_runtime_version.py:*)
  - Bash(python3 tools/check_runtime_version.py:*)
  - Bash(py -3 tools/ensure_projects_registry.py:*)
  - Bash(python3 tools/ensure_projects_registry.py:*)
  - Bash(py -3 tools/secretary_queue_watcher.py:*)
  - Bash(python3 tools/secretary_queue_watcher.py:*)
  - Bash(py -3 tools/check_role_configs.py:*)
  - Bash(python3 tools/check_role_configs.py:*)
  - Bash(py -3 tools/capability_gate.py:*)
  - Bash(python3 tools/capability_gate.py:*)
  - mcp__renga-peers__*
  - mcp__org-broker__* # ORG_TRANSPORT=broker（opt-in）時の機械置換先
---

# org-start: 組織の起動

ClaudeCode起動後に最初に実行するスキル。前回の状態復元とディスパッチャー起動を行う。

> **キュレーターは起動しない**: worker クローズ時の閾値チェックでディスパッチャーがオンデマンド起動する
> （[`.dispatcher/references/pane-close.md`](../../../.dispatcher/references/pane-close.md) Step 5）。
> org-start では curator の identity を `StateWriter.CLEAR` で明示クリアする（Block D-5）。
> **curator 不在（null）が正常系**である。設計メモ・経緯は [`.claude/skills/org-start/references/rationale.md`](references/rationale.md)。

> **前提**: この Claude は `renga --layout ops` で起動された窓口ペイン内で動作している。
> `RENGA_SOCKET` / `RENGA_PANE_ID` 環境変数が継承されているので、`mcp__renga-peers__*` MCP
> ツール 14 種（`spawn_pane` / `spawn_claude_pane` / `close_pane` / `focus_pane` /
> `list_panes` / `new_tab` / `send_message` / `list_peers` / `set_summary` /
> `check_messages` / `inspect_pane` / `poll_events` / `send_keys` /
> `set_pane_identity`）でペイン操作・ピア通信・画面スクレイプ・lifecycle
> event 購読・raw キー入力まですべてカバーできる（renga 0.18.0+ で導入された構造。**現行 org の renga transport サポート下限は 2.0.0**）。
> ただし**この 14 種はタブ scope が一様ではない**（pane 制御 / Group B / messaging / 例外）。分類の正本は
> [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) T-§cap / T-§4.2、要約は [`.claude/skills/org-start/references/rationale.md`](references/rationale.md)。
>
> **state DB 前提**: `.state/state.db` が唯一の SoT。
> read 経路は DB のみ、構造化セクション
> (Status / Dispatcher / Curator / Worker Directory Registry /
> Active Work Items / Resume Instructions) の write 経路は
> `StateWriter.transaction()` 経由（post-commit hook が
> `.state/org-state.md` を DB から自動再生成、markdown 直接編集禁止 —
> drift_check が検出する）。free-form ノート（学び / Pending Lead 等）
> は `notes/` 配下に保存する。
> DB が無い場合は `python3 -m tools.state_db.importer --db .state/state.db --root . --rebuild --no-strict` で構築する。

> **輸送層（transport）両系 — 既定 `renga` / opt-in `broker`**: 本スキルの `mcp__renga-peers__*` 呼び出しは **既定 `renga`**（`ORG_TRANSPORT` 無設定）で書いてあり、そのまま従えばよい（既定挙動は不変）。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では MCP サーバー名が `org-broker` になり、ツールの **完全修飾名が `mcp__renga-peers__*` → `mcp__org-broker__*`** に機械置換される（引数形・セマンティクスは同一なので手順の論理は変わらない）。輸送依存で手順が変わる点だけ broker 併記する:
>
> - **受信モデル（push 一次 = `claude/channel` / pull フォールバック）**: renga は dispatcher / worker のメッセージが `<channel source="renga-peers" …>` として in-band で push される。broker は **push 一次**に再設計されている（runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（renga の in-band push と同じ「受けたら即応答」契機）。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン / claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages`（broker では `mcp__org-broker__check_messages`）する（§9.6 読み替え表の役割別 cadence。ナッジが出れば契機になりうるが idle を起こさないため能動 poll が受信の正路。既存の「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む。ツール名以外の手順は同型）。契約面は Surface 8 で push 一次が **ratified 済み**（2026-06-15、S3。broker pull-only を supersede し pull は fallback として retain・renga 不変）。
> - **spawn 儀式（folder-trust 承認 + dev-channel sidecar 承認の再導入）**: renga の `spawn_claude_pane` は `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を画面判定で承認する（Block D-1）。broker は `--mcp-config <broker>` を注入し承認プロンプトが Claude Code の **folder-trust プロンプト**に変わる（画面判定で機械承認）**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を画面判定で **再導入**機械承認する（ratified §5/§8.5 の folder-trust フローへの加算であり置換ではない。設計は broker-native-roles.md §9.5）。**画面判定** = `inspect_pane` → `claude-org-runtime dispatcher spawn-prompt-step` が返した 1 手だけを `send_keys` する反復（runtime 0.1.43+）。盲目 Enter は禁止（folder-trust の初期カーソルは「No, exit」で、Enter 一発は起動したセッションを終了させる）。
> - **エラー分岐（broker 追加コード）**: renga コード（`[split_refused]` / `[pane_not_found]` / `[cwd_invalid]` 等）に加え broker は `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。一覧は [`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) の broker 節を参照。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外。本フローは元々使わない）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15、「Ratified amendment」節）・既存 ratified 本文不変更）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次再設計）/ `docs/design/ja-migration-plan.md` §5.2(ii)・§8。broker 実走（dogfood）は Epic #6 Issue G スコープで本スキルの既定経路ではない。
>
> （**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）

## Step 0: 初期化

0. **transport 判定（最初のサブステップ — どの MCP 呼び出しよりも前に行う）**: `ORG_TRANSPORT` 環境変数を確認し、以降の手順で使う MCP サーバー名を確定する:
   ```bash
   echo "${ORG_TRANSPORT:-renga}"
   ```
   - `renga`（無設定を含む既定）: 本スキル記載のとおり `mcp__renga-peers__*` をそのまま使う
   - `broker`: 以降の手順（Step 0 の残りサブステップ〜Block D・失敗モードを含むすべて）のツールを `mcp__org-broker__*` で呼ぶと**ここで決めてから** MCP 呼び出しに入る（冒頭注記の機械置換）。broker 環境で判定せずに `mcp__renga-peers__*` を先に叩くと「RENGA_PANE_ID not set — Claude Code was not launched by renga」エラーで初めて気づく無駄な往復になる
1. `mcp__renga-peers__set_summary`（broker では `mcp__org-broker__set_summary`）で自分のサマリーを設定する: 「Secretary: 窓口」
   - ワーカー / ディスパッチャー / キュレーターが `list_peers` で窓口を発見するために必須
2. messaging MCP の疎通確認: `mcp__renga-peers__list_panes`（broker では
   `mcp__org-broker__list_panes`）を呼び出す。
   - エラーなく応答が返れば MCP 有効。以降 messaging MCP ツールが使える前提で進む
   - エラーが返る / ツール未登録の場合は Skill の実行を一時停止し、transport 別の
     復旧をユーザーに促す（導入後にやり直してもらう）:
     - renga: `renga mcp install` の実行を促す。詳細は README の「インストール」
       セクション参照
     - broker: `renga mcp install` は renga 専用の復旧手順なので適用しない。
       broker daemon の起動状態と `--mcp-config` に渡す broker 設定をユーザーに
       確認してもらう
3. **secretary ペイン identity の検証と自動リカバリ**:
   - **caller pane id を先に確定する（`focused=true` を「自分」と読まない）**: `list_panes` の
     `focused` は「そのタブで今フォーカスされているペイン」であって caller 自身ではない。人間が
     フォーカスを移した瞬間 `focused` は別ペインを指すので、そこを起点にすると無関係のペインを
     `secretary` に改名しうる。identity の検証も修復も、契約
     [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md)
     T-§4.2 の **caller pane id 取得規則**に従って caller pane id 起点で行う:
     1. 環境変数 `RENGA_PANE_ID` を caller pane id の起点にする（renga が起動したペイン内でのみ
        継承される。冒頭「前提」で挙げた環境変数で、未設定は `detached` 相当）:
        ```bash
        echo "${RENGA_PANE_ID:-unset}"
        ```
     2. `mcp__renga-peers__list_panes`（broker では `mcp__org-broker__list_panes`）を列挙し、
        **同一 id のレコードが存在することを照合する**（id recycle / 列挙不能の検出）。照合できた
        そのレコードが caller 自身であり、以降の期待値判定と修復はこのレコードだけを対象にする
   - 期待値: `name == "secretary"` かつ `role == "secretary"`
   - **`caller-id 未確定` の場合** — `RENGA_PANE_ID` が未設定 / `list_panes` に同一 id のレコードが
     無い / broker 等そもそも同等の caller-id を入手できない transport: **自己修復（下の自動リカバリ）を
     実行せず、identity 未確認のまま人間に報告して続行判断を仰ぐ**（勝手に fatal にしない。相対セレクタは
     hook が deny する）。broker の縮退の具体は下の broker 分岐を参照
   - **不一致の場合** — `renga --layout ops` 以外の経路で起動された / 旧ops.tomlで起動された既存セッションに attach 等:
     1. `mcp__renga-peers__set_pane_identity(target="<RENGA_PANE_ID の値>", name="secretary", role="secretary")` を呼んで自動修復（照合済みの数値 pane id で撃つ）
     2. 成功すれば警告ログを events テーブルに残して続行（`bash tools/journal_append.sh secretary_identity_restored note=auto_recovered`）
     3. 失敗ケースの分岐:
        - `name_in_use`（broker では `name_taken`）エラー: 既存の別ペインが `secretary` を占有している。ユーザーに状況を報告し、「現セッション継続なら全ワーカーに `to_id="{numeric_pane_id}"` で送信させる」「永続修復なら `/org-suspend` → 終了 → `renga --layout ops` で再起動」の選択肢を提示
        - `name_invalid` / その他: ユーザーに原因を報告
   - **一致している場合**: そのまま続行
   - **broker（`ORG_TRANSPORT=broker`）の場合**: broker には `RENGA_PANE_ID` に相当する caller pane id が無く、secretary 自身の pane record が存在しないこともある。したがって broker では上の caller pane id 取得規則が最初から **`caller-id 未確定`** に落ち、`set_pane_identity` による自動修復は行わない（相対セレクタで撃てば `[pane_not_found] no pane for target 'focused'` になるが、そもそも撃たない）。**上記の不一致リカバリは再試行せず、この分岐に進む**。**この結論は下の「broker 論理エントリ注記」にも等しく及ぶ** — `list_panes` に数値 id のエントリが在って手元で掴めていても例外にならない。数値 id であることは契約 T-§4.2 Group B の MUST の**片方**（相対セレクタでないこと）しか満たさず、**もう片方**（自タブと確立済みの列挙から採った id であり、かつ対象が caller であること）を満たさないからである。 このとき **`mcp__org-broker__list_peers` を identity 検証の充足根拠にはしない**（下記のとおり、承認の有無によらずこの経路から得られるものは無い）。`list_peers` を引くのは **messaging 経路が生きていることの確認**までで、broker の `send_message` ルーティングは peer 登録で成立し secretary の pane record を要求しないので、その確認自体には意味がある — ただし**それは identity の検証ではない**（契約が MUST で禁じる「messaging 到達性から pane 制御到達性を推論する」に踏み込まないため、両者を明確に分ける）。**この `list_peers` の直前にも [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../org-delegate/references/capability-first-drive-operational-gate.md) を Read し、`monitoring-read-only` の分岐を適用する**（同 reference §6 の表 #2b）。capability 形かつ未承認なら列挙を identity 検証の充足根拠にせず破棄し、`name="secretary"` の一致で満たされたとみなさない（予約名 `secretary` は別 org の並走タブに同名で実在しうる。共有 reference §3-B-1）。その場合は identity 未確認のまま**人間に報告して続行判断を仰ぐ**（勝手に fatal にしない）。**承認済み（同 reference §2 の `first_drive` が `recorded`）のときも同じ結論になる**: §1-2-d のとおり `list_peers` は caller を除外するので、`name="secretary"` に一致したレコードは**同タブに 1 件だけ在っても自分ではありえず**、自 identity の検証根拠にできない（承認の有無によらず、この経路が列挙から得られるものは無い）。なお現行 `org-broker` は本 amendment の capability を 1 つも広告しないので、**この段落で言及した `monitoring-read-only` の縮退（capability 形かつ未承認のときの列挙破棄）**は broker 面では発火しない。**`caller-id 未確定` の方は別物で、broker では常に発火する** — capability の広告有無は first-drive gate を制御するだけで、欠けている out-of-band な caller pane id を供給しないからである。したがって broker 面でも上記の「自己修復せず人間へ報告して続行判断を仰ぐ」は**必ず通る**
     > **broker 論理エントリ注記**: broker の `list_panes` に geometry が全て 0（w=0 h=0）で `kind` が null のエントリが現れることがある。これは残骸とは限らず、**adapter 実体を持たない論理エントリ（human-driven logical pane）**でありうる。典型は root secretary（窓口）自身の bookkeeping entry。扱いは次のとおり:
     > - `inspect_pane` は adapter 不在のため失敗する（socket close を観測する）。調査目的で叩かない
     > - `close_pane` は `[logical_pane] cannot close a human-driven logical pane` で**拒否される**。残骸と決めつけて close を試みない
     > - このエントリの `name` / `role` が期待値（`secretary` / `secretary`）とずれたまま残っている場合（手動テストの名残等）でも、**`set_pane_identity` による改名修復はしない**。上の broker 分岐と同じ結論に落とし、**ずれを人間に報告して続行判断を仰ぐ**。理由は、そのエントリが caller である証明が**形状からは立たない**ためである — geometry 全 0 / `kind` が null / root secretary の bookkeeping entry という形が示すのは「adapter 実体を持たない論理エントリである」ことだけで、「それが自分である」ことではない。数値 id で撃てば相対セレクタは避けられるが、それは MUST の片方にすぎず、caller である裏付けを欠いたまま撃てば無関係のペインを `secretary` に改名しうる
4. **プロジェクトレジストリの配置（未配置時のみ生成・既存は上書きしない、Issue #811）**:
   `registry/projects.md` は operator-local な生成ファイル（`.gitignore` 済み）で、
   コミットされているのはテンプレート `registry/projects.example.md` の方である。
   fresh clone 直後は実体が無いので、ここで生成する:
   ```bash
   python3 tools/ensure_projects_registry.py     # Mac/Linux
   py -3 tools/ensure_projects_registry.py       # Windows native
   ```
   - **既存ファイルは絶対に上書きされない**（operator の登録行がここにあるため）。冪等なので毎回実行してよい
   - 出力が `created:` なら新規生成、`ok:` なら既存のまま
   - 出力が `header drift:` の場合、テンプレートに増えた列が手元ファイルに無い。
     **警告であり org-start は止めない**（exit 0）。表示された列名をユーザーに伝え、
     手元の `registry/projects.md` の表ヘッダー行とセパレーター行へ追記するよう案内する
     （既存データ行は編集不要。空セルは従来動作を保つ）
   - `error:` で exit 1 の場合はテンプレート自体が壊れている。ユーザーに報告して続行する
     （後続の Block B / D はレジストリを読まないので org-start 自体は継続できるが、
     ワーカー派遣前に解消が必要）
   - 既存 checkout の移行手順は [`docs/operations/registry-projects-migration.md`](../../../docs/operations/registry-projects-migration.md) を参照
5. `registry/org-config.md` の `workers_dir` を読み、ワーカーディレクトリの存在を確認する。
   存在するディレクトリがあれば一覧をユーザーに報告する。過去の作業成果や再利用可能なプロジェクトを含むため削除はせず、org-delegate のディレクトリ保持ポリシーに従う。

## Step 1〜3: 並列起動フェーズ

> **起動順**: Step 0 (transport 判定 / set_summary / MCP 疎通 / identity 検証 / projects レジストリ配置 / workers_dir 確認の 6 サブステップすべて) が完了した後に dispatcher の `spawn_claude_pane` を発火し、Claude 起動待ち (〜30〜60s) と並列に Block B (前回状態の DB 読み込み) / Block C (ダッシュボード server 起動) を進める（短縮効果は [`.claude/skills/org-start/references/rationale.md`](references/rationale.md)）。
>
> **実行モデル**: Secretary が以下の 3 ブロック (A/B/C) を発火し、最後に block D で合流する。block A は I/O bound（renga MCP の応答は数百 ms、その後は別プロセスである Claude の boot を待つだけ）なので B/C と wall-clock を完全に overlap できる。

### Block A: ディスパッチャーペイン spawn (発火のみ、boot 完了は待たない)

ペイン配置は org-delegate/references/pane-layout.md に従う (renga 版)。curator はここでは spawn しない（オンデマンド化、本ファイル冒頭注記）。

1. dispatcher 用 `spawn_claude_pane`:
   ```
   mcp__renga-peers__spawn_claude_pane(
     target="focused",
     direction="horizontal",
     role="dispatcher",
     name="dispatcher",
     cwd=".dispatcher",
     permission_mode="bypassPermissions",
     model="sonnet"
   )
   ```
   返り値の `"Spawned pane id=N."` から dispatcher の `pane_id` を控える。引数の意味と落とし穴は [`.claude/skills/org-start/references/rationale.md`](references/rationale.md)「spawn_claude_pane 引数の詳細」を参照。
2. **ここで spawn 結果のみブロックする**（Claude の boot 完了は待たない）。spawn が `[<code>] <msg>` で失敗していた場合は本ファイル末尾「### 失敗モード」へ。spawn が成功（pane_id 取得）なら、Block B / C と並列に進む。

### Block B: 前回の状態確認

read 経路は **DB only**（例外は手順 3 の pending-decisions register だけ）。Block A の spawn 発火と並列で実行する（Block A は MCP 経由でペイン作成を依頼するだけで Claude の boot は別プロセスのため、ここで CPU / I/O を競合しない）。

1. `.state/state.db` が存在するか確認し、存在すれば中身にテーブルがあるかも確かめる（Issue #1154: ファイルはあるがテーブルが 1 つも無い空の DB がありうる）:
   ```bash
   # Windows native は python3 を py -3 に読み替える
   python3 -c "import sqlite3,sys; t={r[0] for r in sqlite3.connect('.state/state.db').execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}; sys.exit(0 if {'runs','events'}<=t else 3 if not t else 4)"
   ```
   ファイルが無いときは実行しない（`sqlite3.connect` が空ファイルを作ってしまう）。終了コードで分岐する:
   - `0`（テーブルあり）→ DB をクエリ:
     ```bash
     # Mac/Linux
     python3 -c "from tools.state_db import connect; from tools.state_db.queries import get_org_state_summary; import json; \
       conn = connect('.state/state.db'); \
       print(json.dumps(get_org_state_summary(conn), ensure_ascii=False, indent=2, default=str))"
     # Windows native（Git Bash。PowerShell では行末の `\` を `` ` `` に替える）
     py -3 -c "from tools.state_db import connect; from tools.state_db.queries import get_org_state_summary; import json; \
       conn = connect('.state/state.db'); \
       print(json.dumps(get_org_state_summary(conn), ensure_ascii=False, indent=2, default=str))"
     ```
     `active_runs` / `recent_events` / `run_status_counts` / `session.status` / `session.objective` で前回状態を把握する
   - ファイルが存在しない、または `3`（テーブルが 1 つも無い空の DB）→ 初回起動と判断する。Secretary に importer 実行を促す:
     `python3 -m tools.state_db.importer --db .state/state.db --root . --rebuild --no-strict`
   - `4`（テーブルはあるが `runs` / `events` が欠けている）やそれ以外の失敗（壊れたファイル等）→ importer を回さない（`--rebuild` は全テーブルを消すので、残っている記録を失う）。エラー内容を添えて「state.db が不完全で前回状態を読めない」と人間に報告し、扱いを確認する
2. session.status を確認する:
   - `SUSPENDED` なら /org-resume の Phase 1〜3（ブリーフィング・照合・再開計画）を実行する。
     Block A の spawn は既に発火済みなのでブリーフィング中に裏で Claude が boot している。
     ブリーフィング完了後、Block D の合流で dispatcher が ready になるのを待ち、org-resume の Phase 4（ワーカー再派遣）を人間の承認に基づいて実行する
   - `ACTIVE` なら、前回のセッションが突然終了した可能性がある。
     各ワーカーディレクトリの git 状態を確認し、現状を報告する
3. **滞留中の判断仰ぎを棚卸しする**（Issue #1155）。read 経路「DB only」の唯一の例外として、pending-decisions register（JSON ファイル `.state/pending_decisions.json`）を読み取り専用で読む:
   ```bash
   python3 tools/pending_decisions.py stale --min-age-min 60   # Mac/Linux
   py -3 tools/pending_decisions.py stale --min-age-min 60     # Windows native
   ```
   - 受信（`received_at`）から 60 分以上経ち、まだ `resolved` になっていない項目（`pending` / `escalated`）が 1 行 1 件で出る。出たらブリーフィングに「要確認の判断仰ぎ」として task_id・状態・受信日時・要約を並べる
   - 一覧は **要確認** であって転送漏れの断定ではない（転送済みで記帳だけ失敗した項目や、人間がまだ考えている項目も含む）。ここから自動 resolve・判断の再送・ワーカーへの再連絡はしない。扱いは人間に確認してから決める
   - 出力が `{"status": "unreadable", ...}`（exit 2）のときは「該当なし」と書かず「判断仰ぎ register を確認できない」とエラー内容つきで報告し、起動は続ける
   - 役割分担: この棚卸しは **再起動時の 1 回だけ**。稼働中の中継漏れはディスパッチャーの SECRETARY_RELAY_GAP_SUSPECTED 監視（[`.dispatcher/references/worker-monitoring.md` Step 5.1](../../../.dispatcher/references/worker-monitoring.md#step-5-1)）が受け持つ。起動後に生じた滞留をこの一覧が何分以内に見つけるかは保証しない（次回起動まで見つからないこともある）

### Block C: ダッシュボードサーバー起動

Block A の spawn 発火と並列。ダッシュボード server は別プロセス（Python HTTP server）で Claude pane と独立。

サーバーは 8099 → 8100 → 8101 のうち空いているポートを使う（同じホストの別の組織が 8099 を使っていることがある）。案内する URL は必ず `--status` の出力から取り、8099 を決め打ちしない。

1. ダッシュボードサーバーが起動しているか確認する（Windows は `python3` を `py -3` に読み替える。以下同じ）:
   ```bash
   python3 dashboard/server.py --status
   ```
   `--status` は bind 後にサーバーが書く `.state/dashboard.json`（実際のポートと pid）を読み、そのポートで応答しているのがこの clone のサーバーかを HTTP で確かめる。終了コード `0` なら標準出力が URL なので手順 3 へ。`1`（停止中）なら手順 2 へ。`3`（ポート記録を持たない旧版サーバーがこの組織で稼働中）なら Mac/Linux は `python3 tools/stop_dashboard.py`、Windows native は [`.claude/skills/org-down/SKILL.md`](../org-down/SKILL.md) Step 2 の PowerShell 手順（`Get-CimInstance` で CommandLine を照合してから `Stop-Process`）で止めてから手順 2 へ。Windows native では `py -3 tools/stop_dashboard.py` を使わない（内部の `os.kill(pid, 0)` が Windows ではプロセスを終了させるため、照合前に無関係なプロセスを止めうる）。止められなければ「ダッシュボードの再起動が必要」と報告して本 Block を終える。`2`（記録が壊れている / 記録のポートで別のプロセスが応答している）なら標準エラーの理由を添えて報告し、URL は案内しない。
2. 起動し、最大 15 秒まで起動を待って確かめる:
   ```bash
   python3 dashboard/server.py &
   python3 dashboard/server.py --status --wait 15
   ```
   `0` 以外なら「ダッシュボードを起動できませんでした（<標準エラーの理由>）」と報告し、URL は案内しない（候補ポートが全部使用中、ポート記録が書けない等）。
3. ユーザーに案内する（`<url>` は `--status` の標準出力）:
   「ダッシュボードを起動しました → <url>」

> **sandbox 注記（false negative 回避）**: Claude Code の Bash sandbox はネットワーク / プロセス namespace が隔離されているため、本 Block の常駐起動・疎通確認を sandbox 内で行うと誤判定する。ホスト側で server が正常に listen していても、sandbox 内では `curl` が `000` を返し、`ss` に port が見えず、`pgrep` / `kill -0` はホストのプロセスを観測できない（「起動失敗」と誤判定して再起動ループに陥る）。また sandbox 内の `nohup ... &` で起動したプロセスはコマンド終了時に残らず常駐しない。**server の起動（手順 2）と疎通確認（手順 1・2 の `--status`、`tools/stop_dashboard.py` や `curl` / `ss` / `pgrep` / `kill -0` 等）は sandbox 外で実行する**（Claude Code の Bash tool なら `dangerouslyDisableSandbox: true` を付ける、または `run_in_background` によるホスト実行を使う）。

### Block C2: claude-org-runtime バージョン drift 検出

Block A の spawn 発火と並列。`claude-org-runtime` の installed バージョンと PyPI の latest を比較し、drift があれば Step 4 の起動完了報告に 1 行 warning を添える。auto-upgrade は行わず通知のみ。**バージョン番号は本ファイルにも script にも hard-code せず、すべて importlib.metadata と PyPI JSON API の動的取得値のみを使う**（runtime のリリースごとに記述が陳腐化することを避けるため）。

> **sandbox 内では PyPI に届かない → ホスト実行する**: sandbox 内でしか回せない場合は exit 2 =「PyPI 未確認」となり、ネットワーク到達可能なホストで再実行するまで drift 未検証と扱う（経緯と設計メモは [`.claude/skills/org-start/references/rationale.md`](references/rationale.md)）。

1. drift チェックを **ホスト実行**する（Claude Code の Bash tool では `dangerouslyDisableSandbox: true` を付ける）:
   ```bash
   py -3 tools/check_runtime_version.py   # Windows
   python3 tools/check_runtime_version.py # Mac/Linux
   ```
2. **exit code で分岐**する（stdout は drift 行専用、診断は stderr に出る）:
   - **exit 0**（up to date）: installed == pin 窓内 latest。PyPI 到達確認済・drift 無し。**warning 行なし**。
   - **exit 1**（drift）: stdout の `[runtime drift] ...` 1 行を **そのまま Step 4 起動完了報告の末尾に warning として転記する**。あわせて stderr の `測定 interpreter:` 行が示す Python パスを転記行に添える（下記「interpreter の併記」を参照）。
   - **exit 2**（未確認）: PyPI 到達不能 / 応答異常 / pin 窓外 / `packaging` 欠如、**または installed が local install（`file://` パス / VCS / editable。PEP 610 `direct_url.json` で判定）で PyPI 照合不能** の場合。stderr に理由診断が出る。**drift は未検証**なので Step 4 報告に「runtime drift: PyPI 未確認（要ホスト再実行）」を明示する（silent にしない）。**local install 起因の場合はホスト再実行では解消しない**（installed の実体がリリース版と限らないため）ので、stderr の診断が `local install -- PyPI 照合不能` を示すときは「runtime: local install のため PyPI 照合不能（要 PyPI 由来インストールで再確認）」と読み替えて報告する。それ以外（オフライン / sandbox 内）は `dangerouslyDisableSandbox: true` で再実行する。
   - **exit 3**（未インストール）: この Python から runtime を import できない（別 venv / 未インストール）。stderr に note。Step 4 報告に「runtime 未インストール（要確認）」を出す。

> **interpreter の併記（バージョン報告は測った Python に相対）**: 本 Block が報告する installed は「このコマンドを実行した Python が解決したバージョン」であって、ホストの状態ではない。ホストには runtime が複数入りうる（プロジェクトの `.venv` とシステム `python3` が別バージョンで同居し、CLI shim `claude-org-runtime` の shebang はそのどちらか片方に束ねられる）。そのため drift 行を interpreter 抜きで転記すると「更新が遅れている」と読めてしまうが、実際には「もう片方を測った」だけということが起こる。script は **毎回・全 outcome で** stderr の 1 行目に `[runtime drift-check] 測定 interpreter: <sys.executable> (installed=<version>)` を出すので、**Step 4 報告で installed / 未インストールに言及するときは必ずこの Python パスを併記する**（例:「system python3 で測って installed=X」）。上の実行例が `python3` / `py -3` である以上、測っているのは既定でシステム側であって ja の `.venv` ではない点に注意する。

### Block C3: secretary 滞留 watcher の常駐（broker のみ）

`ORG_TRANSPORT=broker` のときのみ実行する。renga では起動しない（broker の queue.jsonl が存在しないため。Step 0 の transport 判定が `renga` なら本 Block はまるごと skip する）。

broker transport では過去に「secretary 宛メッセージが claimed/delivered 記録付きで silent 消失する」障害があった（channel sidecar の二重走行レースが根因。runtime 側の observer lease で修正済み）。その再発・類似滞留に対する運用ガードとして、broker セッションでは org-start 直後に queue.jsonl の滞留 watcher を常駐させる。

1. Claude Code の Bash tool で **`run_in_background=true`** を付けて 1 回呼ぶだけ:
   ```bash
   python3 tools/secretary_queue_watcher.py   # Mac/Linux
   py -3 tools/secretary_queue_watcher.py     # Windows
   ```
2. 動作: watcher は起動時点の `$ORG_BROKER_STATE_DIR/queue.jsonl` 末尾を起点に live-tail し、それ以降の secretary 宛 `message_enqueued` が `delivered` されないまま閾値（default 120 秒）を超えたら、滞留件数と経過秒を 1 行 print して **exit する**。background Bash の終了イベントで窓口が再起床するので、その出力を見たら使用中 transport の `check_messages`（broker では `mcp__org-broker__check_messages`）で drain する。過去ログの通算 gap は数えない（既知の過去消失分が混入して誤検知になるため、本セッション中の新規レコードのみを対象にする）。
3. **停止経路**: watcher は起動時に `.state/secretary_queue_watcher.json`（pid / cwd / cmdline / broker_state_dir を記録した sidecar）を書き、`/org-suspend`（Phase 3.6）/ `/org-down` がこれを identity 照合して停止する（pid recycle / 別 broker 誤停止を避けるため pid 単独では kill しない）。滞留検知で自 exit した場合や SIGTERM で停止した場合は watcher 自身が sidecar を掃除する。

> **sandbox 注記**: 常駐プロセスなので Block C と同様 sandbox 内起動は不可、`run_in_background` によるホスト実行を使う。

> スクリプト本体: [`tools/secretary_queue_watcher.py`](../../../tools/secretary_queue_watcher.py)（`--owner` / `--stale-sec` / `--poll-sec` で調整可。`ORG_BROKER_STATE_DIR` 未設定なら exit 1 で即終了する broker 専用ツール）

> **Sidebar: attention watcher の起動案内（optional, 明示起動推奨）**
>
> 承認待ち / 判断待ち / CI 失敗 / silent stop / PR merged 等を OS notification + 音 + terminal bell で能動的に通知する watcher を別途常駐させられる。**`/org-start` からの自動起動はしない**（OS 通知 backend は環境依存が強く、勝手に音が鳴ると不快になりやすいため。設計 [`docs/design/attention-notification.md`](../../../docs/design/attention-notification.md) §11 Q1）。
>
> 有効化したいユーザーには Step 4 の起動完了報告と合わせて [`/org-attention-start`](../org-attention-start/SKILL.md) の実行を案内する。skill が以下を一括で行う:
>
> - `.state/attention.json` 未配置時は `tools/templates/attention.example.json` から自動コピー
> - dispatcher ペインの右側を vertical split し `claude-org-runtime attention watch ...` を常駐起動
> - pane_id を `.state/attention_pane.json` sidecar に記録（停止は [`/org-attention-stop`](../org-attention-stop/SKILL.md) で参照）
>
> 1 回限りの動作確認は `claude-org-runtime attention scan --state-dir .state --config .state/attention.json --dry-run --json`（`--config` を外すと runtime 中立の英語 default が出るので、ja テンプレートの導通確認には必ず付ける）。OS 別 backend 挙動・トラブルシューティング・別ターミナルからの素 CLI 起動手順は [`docs/operations/attention-watch.md`](../../../docs/operations/attention-watch.md) を参照。

### Block C4: role config drift 検出

Block A の spawn 発火と並列。各ロールの `settings.local.json` を schema に対して検証し、drift があれば Step 4 の起動完了報告に 1 行 warning を添える。自動修復は行わず通知のみ（修復は `/org-setup`）。Block C2 と同列の **非 fatal な warning ブロック**であり、drift を検出しても org-start は止めない。CI は `--include-local` を付けず gitignored の `settings.local.json` を検証しないので、起動時のこの検証が唯一の定期的な契機になる（理由と drift の種類は [`.claude/skills/org-start/references/rationale.md`](references/rationale.md)）。

1. drift チェックを実行する:
   ```bash
   py -3 tools/check_role_configs.py --include-local   # Windows
   python3 tools/check_role_configs.py --include-local # Mac/Linux
   ```
2. **exit code で分岐**する（stdout **最終行**が転記用の 1 行、診断は stderr）:
   - **exit 0**（clean）: schema と一致。**warning 行なし**。
   - **exit 1**（drift）: stdout 最終行の `[role config drift] ...` 1 行を **そのまま Step 4 起動完了報告の末尾に warning として転記する**。個別の `[ERROR] <path> :: <role> :: <理由>` 行は同じ stdout の上部に出ているので、ユーザーが詳細を求めたらそこから提示する（起動報告に全件は貼らない。実測で数十行になりうる）。
   - **exit 2**（検証不能）: checker 自体が完走できなかった（schema 不在 / JSON 構文エラー / `core_harness` 解決失敗等）。stderr に traceback と `role_configs: 検証不能 -- ...` 診断が出る。**silent にせず**「role config: 検証不能（guard 未検証・要確認）」の 1 行を報告に添える。drift の有無は **未測定**であって clean ではない、という区別が本 exit code の存在意義である。読み取りが sandbox の deny に掛かって exit 2 に落ちた場合は、`dangerouslyDisableSandbox: true` でホスト再実行する。

> **必ず窓口の cwd（ja live repo root）で実行する**: `--root` の既定はスクリプト位置から導出される repo root なので、worktree 内から叩くとその worktree の `.claude/settings.local.json`（= ワーカー用の設定）を **secretary の schema で**検証してしまい、数十件の false positive になる。ロール別に個別検証したい場合は当該 worktree で `--role worker` を付ける（`--role` は `--include-local` の意味を含む）。

### Block D: dispatcher の合流 (起動プロンプト承認 / list_peers poll / 挨拶 / DB write / snapshot)

Block A の spawn 成功後、dispatcher ペインで Claude が boot している。

1. **起動プロンプトを画面判定で承認する（盲目 Enter 禁止）** — Claude Code の起動時プロンプト（folder-trust「Do you trust the files in this folder?」相当 / 「Load development channel?」）を、画面を見て 1 手ずつ承認する。**folder-trust の初期カーソルは「No, exit」なので、表示を確かめずに送った Enter はそのまま dispatcher を終了させる**。判定は runtime の `spawn-prompt-step` に任せ、窓口は返された手だけを打つ:
   1. 開始時刻を ms で控える（`date +%s%3N`）。`previous` = null
   2. `mcp__renga-peers__inspect_pane(target="dispatcher", format="grid")` を呼ぶ（`lines` は付けない。背の高いペインでは下端 N 行の外にダイアログが残る）
   3. 判定を 1 回呼ぶ。stdin には JSON オブジェクト `{"screen": <2 の結果>, "previous_screen": <前回の inspect 結果か null>, "elapsed_ms": <開始からの経過 ms の整数>}` を 1 つ渡す（画面テキストはシェルの引用へ直に埋め込まず、JSON 文字列としてエンコードしてファイル経由で渡す）:
      ```bash
      claude-org-runtime dispatcher spawn-prompt-step --deadline-ms 120000 < "$TMPDIR/spawn-prompt-step.json"
      # PATH に無ければ: python3 -m claude_org_runtime.dispatcher.runner spawn-prompt-step --deadline-ms 120000
      #   （Windows native は py -3 -m claude_org_runtime.dispatcher.runner spawn-prompt-step --deadline-ms 120000）
      ```
   4. 結果で分岐する:
      - **exit 0 / `action: "send_keys"`**: 返された `send_keys` オブジェクトを**そのまま** `mcp__renga-peers__send_keys(target="dispatcher", ...)` に渡す（`enter` を自分で足さない）。`previous` = null にして 1 秒待ち 2 へ
      - **exit 0 / `action: "wait"`**: `previous` = 今回の inspect 結果にして 1 秒待ち 2 へ
      - **exit 0 / `action: "done"`**: 承認完了。手順 2（list_peers poll）へ
      - **exit 10（escalate）/ exit 2（入力不正）/ その他の非 0 / inspect・send_keys のエラー（`[key_unsupported]` 等）**: Enter を送らずに中止し、ペインの状態をユーザーに報告して判断を仰ぐ（下記「失敗モード」の起動失敗として扱う）
   - 承認しないと `server:renga-peers` チャネルが有効化されず、`send_message` の channel push が届かない。未表示段階では判定が `wait` を返すので、`done` まで反復すればよい（Enter を「念のため再送」しない）
   - **broker（`ORG_TRANSPORT=broker`）の場合 — 承認は 2 段（folder-trust + dev-channel sidecar の再導入）**: `spawn_claude_pane` は `--mcp-config <broker>`（daemon）を注入し、初回に Claude Code の **folder-trust プロンプト**が出る。**加えて push 一次のため** channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load するため「Load development channel?」プロンプトが**再出現**する（`--mcp-config`-only 設計で一旦消えた 3-3b 承認の再導入）。手順は上と同じループを `mcp__org-broker__inspect_pane` / `mcp__org-broker__send_keys` で回す（判定が 2 つのダイアログを順に扱う。順序は boot タイミング依存）。WezTerm adapter は Up / Down を送れず `[key_unsupported]` になるので、そのときも Enter に倒さず中止して報告する。承認しないと broker token のバインド／channel sidecar の登録が完了せず、次の `list_peers` 待ちが同様にタイムアウトする（ratified §5/§8.5 の folder-trust フローへの加算であり置換ではない。設計 broker-native-roles.md §9.5）
2. **list_peers を poll し dispatcher の peer 登録を確認**:
   **`list_peers` の直前に [`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../org-delegate/references/capability-first-drive-operational-gate.md) を Read し、`monitoring-read-only` の分岐を適用する**（同 reference §6 の表 #2）。
   ```
   mcp__renga-peers__list_peers
   # 結果に name="dispatcher" が現れるまで poll
   ```
   - 現れない場合、(a) Block D-1 の画面判定ループを 1 回回し直す（Enter を盲目で再送しない。判定が `done` なら承認は済んでいるので poll を続ける）、(b) `[pane_not_found]` 等 fatal なら「失敗モード」セクションへ
   - **capability 形かつ未承認のときの縮退（停止しない・待ち時間 0 分）**: 列挙を登録確認に使わず破棄する。**`name="dispatcher"` の一致だけで登録ゲートを開けない** — 予約名は別 org の並走タブに同名で実在しうるので、name 一致は「まだ登録していない自分の子」のゲートを他 org のピアで開けてしまう（契約 T-§2.2「MUST NOT key a lookup … on `name` alone」／共有 reference §3-B-1）
     - **代替の readiness 判定は「次の手順 3 の挨拶送信そのもの」で行う**。`list_panes` の生存と `inspect_pane` のプロンプト表示は Claude が起動したことしか示さず、**MCP の peer 登録が済んだことを示さない**。プロンプトが見えた時点で boot 完了とみなすと、手順 3 の**一度きりの挨拶が `[pane_not_found]` で消える**。したがって: (a) `list_panes` でペイン生存だけ確認し、(b) 手順 3 の `send_message` を送り、(c) `[pane_not_found]`（broker では `[peer_not_found]`）で失敗したら **2 秒間隔・最大 30 秒で再送**する（その間も Enter は再送せず、承認が未完了なら Block D-1 の画面判定ループを `done` まで回す）、(d) 送達成功をもって「起動・登録・挨拶送信」が同時に確定する
     - **この縮退中は「peer 未登録」を根拠に失敗モードの fatal 分岐へ進んではならない**（後述の「spawn 成功・boot 中に peer 登録されない」参照）。30 秒の再送予算を使い切っても送達できないときだけ、従来のタイムアウト処理に落とす
     - 報告は共有 reference §3-B の手順で 1 度だけ人間に上げる
   - **capability 形かつ承認済み（共有 reference §2 の `first_drive` が `recorded`）のとき**: 登録受理の判定を同 reference §1-2 の三値判定に委ね、`name="dispatcher"` が**「在」（同タブ・一意）のときだけ受理**する。poll の継続・再送予算・受理後の挨拶送信・予算切れの失敗処理は §1-2-c のとおり**この経路の従来手順のまま**で、ここに重ねて書かない
     - **この経路固有の適用結果は 1 つだけ**: 予算切れ時に `unknown` が残っていた場合は fatal に落とさない（`close_pane` + `StateWriter.CLEAR` へ進まない）。タブ数 / ピア総数 / 他タブ判定数 / unknown 件数を添えて**人間に報告し判断を仰ぐ**（共有 reference §1-2-b: unknown を「peer 未登録」に潰さない）。「不在」「他タブのみ」で予算切れになったときは従来どおり「失敗モード」の分岐に落とす
3. **挨拶メッセージを送信**:
   - dispatcher:
     「あなたはディスパッチャーです。窓口からの DELEGATE メッセージを受け取り、ワーカーのペイン起動・指示送信・状態記録を代行してください。CLOSE_PANE メッセージを受けたらペインを閉じてください。」
   - curator への挨拶は無い（常駐しない。オンデマンド起動時の指示送信は dispatcher が行う）
4. **Block B の DB 初期化完了を待つ** — 並列実行の合流点。Block B-1 で `.state/state.db` 不在 → `importer --rebuild` が走った場合、schema 構築完了までは `StateWriter.update_session()` が失敗するので、Block D-5 の DB write 前に Block B が完了している必要がある。Block B が SUSPENDED ブリーフィング待ちで未完了なら、ブリーフィング完了 → DB schema 健全性確認 → ここに進む
5. **DB transaction を 1 回にまとめて identity を記録する** (`StateWriter.transaction()` 経由、markdown 直接編集禁止。post-commit hook が `.state/org-state.md` を再生成)。dispatcher の identity を write し、**curator の identity は常に `StateWriter.CLEAR` で明示クリアする**（オンデマンド化により常駐 curator は存在しない。旧仕様の SUSPENDED 状態から繰り越した stale な `curator_pane_id` / `curator_peer_id` が残ると、ダッシュボードと balanced split target 選出が生きた curator がいる前提で誤判断する。`StateWriter.update_session()` は None 渡しを「未指定 = 保持」と解釈する契約なので、明示クリアが必須）:
   ```bash
   # Mac/Linux
   python3 -c "
   from pathlib import Path
   from tools.state_db import connect
   from tools.state_db.writer import StateWriter
   conn = connect('.state/state.db')
   with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
       w.update_session(
           dispatcher_pane_id='<d_pane>', dispatcher_peer_id='<d_peer>',
           curator_pane_id=StateWriter.CLEAR, curator_peer_id=StateWriter.CLEAR,
       )
   "
   # Windows native（Git Bash / PowerShell。`"..."` 内の改行はそのまま渡る）
   py -3 -c "
   from pathlib import Path
   from tools.state_db import connect
   from tools.state_db.writer import StateWriter
   conn = connect('.state/state.db')
   with StateWriter(conn, claude_org_root=Path('.')).transaction() as w:
       w.update_session(
           dispatcher_pane_id='<d_pane>', dispatcher_peer_id='<d_peer>',
           curator_pane_id=StateWriter.CLEAR, curator_peer_id=StateWriter.CLEAR,
       )
   "
   ```
   curator フィールドが null であることは**正常系**であり、suspend / handover / resume / dashboard はこれを前提に動く。
   - **capability gate の縮退中（Block D-2 の列挙を破棄した場合）は `dispatcher_peer_id` に `StateWriter.CLEAR` を渡す**。縮退中は `<d_peer>` を観測できていないので、推測値や他タブ由来の id を書くと以後の routing が壊れた宛先を掴む。**ここで「未指定 = 保持」に倒してはならない**: `/org-start` は dispatcher ペインを**新規 spawn** するので、DB に残っている値は前セッションの死んだペインのものであり、保持は stale の温存になる（生きたペインを引き継ぐ [`.claude/skills/dispatcher-resume/SKILL.md`](../dispatcher-resume/SKILL.md) Step 0 とはここが逆になる。あちらは同じペインが生きているので保持が正しい）。`dispatcher_pane_id` は `list_panes` から確定できるので通常どおり書く。peer_id が null の間、dispatcher への送信は安定名 `to_id="dispatcher"` で行う（root `CLAUDE.md` の既定の宛先指定と同じ）。この状態はユーザー報告に含める。
6. JSON スナップショットを 1 回だけ再生成する（dashboard 用、state-db cutover とは別経路）:
   ```bash
   python3 dashboard/org_state_converter.py   # Mac/Linux
   py -3 dashboard/org_state_converter.py     # Windows native
   ```

### 失敗モード

Block A の spawn 段階で分類する:

- **dispatcher spawn 失敗 (`[split_refused]` / `[cwd_invalid]` / その他 `[<code>]`)** — **失敗をユーザーに報告し、原因解消後 /org-start を再実行する**
- **spawn 成功・boot 中に peer 登録されない** — Block D-2 の poll が timeout する。Block D-1 の画面判定ループを回し直す → 再 poll。3 回 retry してダメなら fatal: dispatcher 無しでは org-delegate / SECRETARY_RELAY が機能しないため、ペインを破棄し、**dispatcher / curator 両 identity を `StateWriter.CLEAR` で消した上でユーザー報告**し /org-start 再実行を促す
  - **この破棄も Group B なので、下記 3 段を踏んでから撃つ**（契約 [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) T-§4.2「Fail-safe consequence for Group B」）。**spawn 戻り値で pane_id が手元にあることは免除にならない** — 未確立の列挙では、下の identity 照合の結果そのものを信用できないため:
    1. **自タブ確立**: (i) backend が Group B を自身の単一タブモデル内で解決する（`org-broker`。同契約 §8.1 / §8.10）/ (ii) `caller_scope` を確立できている（同 T-§cap）の**いずれか 1 つ**が成立すること。**どちらも不成立なら `close_pane` を撃たない** — その場合はペインを残したまま `StateWriter.CLEAR` とユーザー報告だけを行い、「dispatcher ペイン (id={N}) が残っているので手動で閉じてほしい」を報告に含める（相対セレクタへはフォールバックしない）
    2. **identity 照合**: `mcp__renga-peers__list_panes` で spawn 戻り値の pane_id が `name == "dispatcher"` かつ `role == "dispatcher"` を**なお指しているか**確認する。別ペインへ再割当て済み / 列挙に現れない場合は close せず、同じく `StateWriter.CLEAR` と報告に倒す（pane_id recycle 対策）
    3. 1 と 2 の両方を通ったときだけ `mcp__renga-peers__close_pane(target=<照合済みの数値 pane_id>)` で破棄する
  - **capability 形かつ未承認の縮退中は、fatal 分岐の判定材料を差し替える（分岐を無効化するのではない）**: 縮退中は Block D-2 の列挙を破棄しているので「`list_peers` に居ない = peer 未登録」という判定が成立せず（[共有 reference](../org-delegate/references/capability-first-drive-operational-gate.md) §1-1 / §3-B-1）、**それを根拠に** `close_pane` + `StateWriter.CLEAR` を撃つと健全な dispatcher ペインと 2 つの identity を破棄する。代わりに **Block D-2 の縮退手順で使う「挨拶 `send_message` の再送」が 30 秒の予算を使い切って一度も送達しなかったこと**を fatal の根拠にする（プロンプトが見えていても、送達できない dispatcher は org-delegate / SECRETARY_RELAY を担えないので起動失敗である）。その場合は従来どおり両 identity の `StateWriter.CLEAR` + ユーザー報告を行い、ペインの破棄は**上の 3 段（自タブ確立 → identity 照合 → 数値 close）を踏んでから**撃つ（踏めなければ close せず、ペインが残る旨を報告に含める）。**「ペインが boot していれば fatal にしない」ではない** — 到達不能な dispatcher を抱えたまま `/org-start` を成功として報告してはならない
- **起動プロンプトの承認が終わらない** — Block D-1 の判定が `done` を返す前に先へ進まない（プロンプト未表示の間は `wait` が返る）。判定が escalate（exit 10）/ 入力不正（exit 2）/ ツールエラーで止まったら Enter に倒さず、ペインの状態を添えてユーザーに報告する（folder-trust で Enter を打つと「No, exit」が選ばれて dispatcher が終了する）。Block D-2 の peer 登録 poll が登録の ground truth

curator の spawn / boot 失敗モードは org-start には存在しない（spawn しないため）。オンデマンド起動時の失敗ハンドリングは [`.dispatcher/references/pane-close.md`](../../../.dispatcher/references/pane-close.md) Step 5-3 / 5-4 を参照。

### 起動後の裏取り: dispatcher の /loop 監視宣言は実 invoke を打鍵で確認する

dispatcher は初回 DELEGATE 完了報告で「/loop 3m で監視します」と宣言したまま実 invoke せず idle に落ちることがある（宣言はターン終了と同時に失効し、次の契機がなければ実行されない）。窓口は初回 DELEGATE 完了報告や監視宣言を信用せず、使用中 transport の `inspect_pane(target="dispatcher")` で /loop の実行痕跡（loop 予約 / 監視サイクル出力）を確認し、無ければ `send_keys` で `/loop 3m <監視ディレクティブ>` を user turn として打鍵し再点火する。打鍵は text と Enter を**別呼び出し**に分ける 2 段（`send_keys(target="dispatcher", text="/loop 3m ...")` → `inspect_pane` で入力欄に乗ったことを確認 → `send_keys(target="dispatcher", enter=true)`。text+enter 同時送は draft 残りで未武装が再発しやすい。[`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-5a と同じ手順）で行う（いずれも Step 0 で判定した transport の完全修飾名で呼ぶ。監視ディレクティブは placeholder のまま打鍵せず、[`.claude/skills/dispatcher-resume/SKILL.md`](../dispatcher-resume/SKILL.md) Step 5 のコードブロック正文をそのまま使う。手順詳細は [`.dispatcher/references/worker-monitoring.md`](../../../.dispatcher/references/worker-monitoring.md)）。`send_message` / `check_messages` 経由の指示では武装しない — 打鍵による user turn 注入が確実な武装手段である（ultracode 武装と同型の構造）。

## Step 4: 準備完了の報告

人間に簡潔に報告する。起動するのはディスパッチャーのみ（キュレーターはオンデマンド）。

**Block C2 / C4 の warning 行の置き方**: 各 Block の exit code の項で決まった行（drift 行・「PyPI 未確認」・「検証不能」等）を、下記いずれのテンプレートでも **末尾に空行を 1 つ挟んだ上でそのまま転記する**。両方出る場合は 1 行ずつ並べる。exit 0 は何も付けない。

> **Sidebar: dispatcher の自己修復ビュー起動案内（broker フレーム時のみ、optional）**
>
> broker(tmux) backend では dispatcher ペインは detached tmux session として独立して存在する。窓口の隣のターミナルでこれを 1 回起動しておくと control plane を常に視界に保てる（restart / auto-compact fork でセッション名が変わっても自己修復で再 attach し直す）:
>
> ```bash
> tools/org-dispatcher-view.sh          # read-only attach（既定・安全）
> tools/org-dispatcher-view.sh --rw     # 読み書き attach（誤入力で dispatcher に直接打鍵されるので注意）
> ```
>
> read-only で十分だが、attach 中に **`Ctrl-b s`** で同 socket 上の他のセッション（= worker / curator のペイン）に切り替えて覗ける。終了は `Ctrl-b d` で detach してからプロンプトで `Ctrl-C`。renga フレームでは tmux session モデルが写像しないので案内しない（renga なら画面そのものを見ればよい）。詳細は [`tools/org-dispatcher-view.sh`](../../../tools/org-dispatcher-view.sh) の `--help`。
>
> **打鍵の読み替え**: 上記 `Ctrl-b` は tmux prefix の既定値なので、prefix を変更している場合は設定した prefix に読み替える。さらに、**この broker フレームのビューアを renga の画面の中で起動する場合はこの打鍵が届かない** — renga の org サイドバーは既定で有効なあいだ `Ctrl+B`（tmux prefix `Ctrl-b` と同じ物理入力）を消費して PTY へ渡さないため、`Ctrl-b s` での切替も `Ctrl-b d` での detach もできなくなる（抜けられなくなるので起動前に対処する）。回避策（renga 設定 `[ui] org_sidebar = "off"`、または内側 tmux prefix の変更）は [`docs/operations/dispatcher-view.md`](../../../docs/operations/dispatcher-view.md)「外側フレームが renga の場合」を参照。

**前回の状態がある場合**:
```
組織を起動しました。
前回の状態: {サマリー}
ディスパッチャーを起動しました（キュレーターは知見が溜まったときに自動で一時起動されます）。
何をしますか？
```

**初回起動の場合**:
```
組織を起動しました。
ディスパッチャーを起動しました（キュレーターは知見が溜まったときに自動で一時起動されます）。
プロジェクトはまだ登録されていません。何をしましょうか？
```

**drift 検出時の warning 添付例** (上記テンプレートの末尾に転記。`{installed}` / `{latest_in_window}` / `{pin}` は実行時にスクリプトが PyPI と `pyproject.toml` から動的に決定するもので、本ファイルには hard-code しない):
```
...
何をしますか？

[runtime drift] claude-org-runtime: installed={installed} latest={latest_in_window} -- `python3 -m pip install --upgrade 'claude-org-runtime{pin}'`（Windows は `py -3 -m pip ...`）で更新できます
```

**role config drift 検出時の warning 添付例** (Block C4 exit 1。`{errors}` / `{findings}` は実行時にスクリプトが決定するもので、本ファイルには hard-code しない。Block C2 の行と両方出るときは 1 行ずつ並べる):
```
...
何をしますか？

[role config drift] {errors} error(s) / {findings} finding(s) -- see the [ERROR] lines above
```

ユーザーが再実行コマンドを求めたら、上の手順 1 のコマンド（`py -3` / `python3` のプラットフォーム別）をそのまま案内する。**スクリプトは再実行コマンドを出力しない**: 貼って動く形は読み手のシェル（cmd.exe / PowerShell / Git Bash）ごとに quoting が非互換で、プロセス側からはどれで読まれるか知りようがないため、誤った形を出すより窓口が固定文で案内する方が確実。

## Appendix: ClaudeCode 起動コマンド（役割別）

Block A / org-delegate Step 3 で使う `spawn_claude_pane` の役割別パラメータ。
renga 0.18.0+ では `mcp__renga-peers__spawn_claude_pane` が役割別の構造化フィールド（`cwd` / `permission_mode` / `model` / `args[]`）を受け取り、`--dangerously-load-development-channels server:renga-peers` を自動付与する。旧方式の `cd X && claude ...` を `spawn_pane` に流し込むパターンは **禁止**（renga の bare-`claude` auto-upgrade が発動せず channel push が届かなくなる落とし穴を再導入するため）。

共通引数:
- `permission_mode`: `auto` リテラル直書き（ディスパッチャー除く）。CLAUDE.md には変数展開機構がないため `registry/org-config.md` の値を実行時に代入することはできない。値を変更する場合は `registry/org-config.md` 冒頭の同期注意セクションを参照
- `cwd`: 各ロール専用ディレクトリへの相対パス（caller pane の cwd 基準で解決される）

> **注**: Secretary は `renga --layout ops` で起動され、`--permission-mode` 未指定のまま動作する（人間判断窓口のため）。`registry/org-config.md` の「Role別の適用範囲」節を参照。

### ディスパッチャー

- `cwd=".dispatcher"`
- `permission_mode="bypassPermissions"`（固定。`default_permission_mode` の影響を受けない）
- `model="sonnet"`

理由: ディスパッチャーはワーカー起動時に `mcp__renga-peers__spawn_claude_pane` を発行する。auto モードの安全分類器はこの「子エージェント起動」を "Create Unsafe Agents" と判定してブロックするため、auto ではワーカー派遣が成立しない。

### キュレーター（オンデマンド起動専用 — org-start からは spawn しない）

ディスパッチャーが worker クローズ時の閾値超過で spawn する（[`.dispatcher/references/pane-close.md`](../../../.dispatcher/references/pane-close.md) Step 5-3）:

- `cwd="../.curator"`（caller は dispatcher、cwd=`.dispatcher/` 基準の相対解決）
- `permission_mode=auto`
- `model="sonnet"`

理由: auto モードの safety classifier はセッションモデルと独立した専用モデルで動作し、承認判定はセッションモデルに依存しない（公式: https://www.anthropic.com/engineering/claude-code-auto-mode）。キュレーターの知見整理ワークロードは軽量・機械的側に分類できるため Sonnet で十分（ワーカーの既定 opus 方針とは独立の判断）。

### ワーカー（org-delegate の Step 3 で使用）

**既定は `model="opus"`（品質優先）。ただし oracle が強いタスクは、窓口が明示指定したときに限り `model="sonnet"`（Sonnet メイン）を選んでよい。**
判定軸は工数ラベル (S/M/L) ではなくタスクの **oracle 強度と判断密度**（ultracode 許可基準と同じ思想）:
- **Sonnet メイン可**: 次を**両方**満たす帯 —(a) 機械検証可能な正解が実装の外に固定されている（fixture 駆動の期待値・テストスイートの assertion・DB の行など）、(b) 設計判断が brief で事前固定済みで実装中に決め直す判断が残らない。
- **opus 維持**: (a)(b) のどちらかを欠くもの（機構の再導出が要る retarget 型 / 契約設計を含む / 実測しないと決められない決定が実装中に残る帯）。

窓口の指定は DELEGATE 本文の `モデル: sonnet` 行で伝わり、この行が無ければディスパッチャーは `opus` で spawn する（[`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2）。

モデル選択は承認フローの安全性とは独立である。auto モードの分類器はワーカーのセッションモデルとは別の専用モデルで動作し、承認判定はワーカーが opus か sonnet かに依存しない（公式: https://www.anthropic.com/engineering/claude-code-auto-mode）。ディスパッチャーだけは `bypassPermissions` 固定なので分類器を経由せず、コスト最適化として sonnet 運用（この判断はワーカーには自動適用しない）。

通常:
- `cwd="{workers_dir}/{task_id}"`（絶対パス推奨）
- `permission_mode=auto`
- `model="opus"`（既定。oracle が強いタスクで窓口が明示指定した場合のみ `"sonnet"`）
