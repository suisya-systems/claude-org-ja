---
name: org-down
description: >
  組織を中断し、broker daemon まで含めて完全に停止する。
  「完全に止める」「daemon ごと落とす」「org down して」「全部終了」と言われたときに使う。
  suspend（状態保存 + ja 管理プロセス / ペイン停止）を完走してから
  `claude-org-runtime org down` を呼ぶ統合終了。単に「中断」「今日は終わり」なら
  daemon を残す /org-suspend を使う。
effort: low
allowed-tools:
  - Read
  - Bash(bash tools/journal_append.sh:*)
  - Bash(python3 tools/journal_append.py:*)
  - Bash(py -3 tools/journal_append.py:*)
  - Bash(python3 -m tools.state_db.importer:*)
  - Bash(py -3 -m tools.state_db.importer:*)
  - Bash(python3 tools/secretary_queue_watcher.py:*)
  - Bash(py -3 tools/secretary_queue_watcher.py:*)
  - Bash(python3 tools/context_handover_watch.py:*)
  - Bash(py -3 tools/context_handover_watch.py:*)
  - Bash(python3 tools/stop_dashboard.py:*)
  - Bash(py -3 tools/stop_dashboard.py:*)
  - Bash(echo:*)
  - Bash(rm -f .state/attention_pane.json)
  - Bash(del .state\attention_pane.json)
  - Bash(claude-org-runtime org down:*)
  - Bash(python3 -m claude_org_runtime.cli org down:*)
  - Bash(py -3 -m claude_org_runtime.cli org down:*)
  - mcp__org-broker__check_messages
  - mcp__org-broker__close_pane
  - mcp__org-broker__inspect_pane
  - mcp__org-broker__list_panes
  - mcp__org-broker__list_peers
  - mcp__org-broker__poll_events
  - mcp__org-broker__send_keys
  - mcp__org-broker__send_message
  - mcp__org-broker__set_summary
---

# org-down: 組織の完全停止（broker daemon まで）

`/org-down` は **`/org-suspend` を完走してから** `claude-org-runtime org down` を呼び、broker
daemon まで含めて組織を完全に停止する統合終了スキルである。

> **責務境界（最重要）**: `claude-org-runtime org down`（broker daemon 停止）を呼ぶのは
> **本スキルだけ**である。[`/org-suspend`](../org-suspend/SKILL.md) は「状態保存 + ja 管理下の補助
> プロセス（dashboard / secretary_queue_watcher / context-handover watcher / attention watcher）とペインの停止」までで
> 止まり、daemon は走らせたまま（`/org-start` で再開する前提の中断）にする。/org-down は
> **suspend の成功を確認できたときにのみ** daemon 停止へ進む。順序を守るのは、未保存の状態を
> 残したまま daemon を落とすと復旧経路（`check_messages` での drain 等）ごと失われるため。
> 本スキルは runtime の `org down` を **呼ぶだけ**（runtime 側 pre-flight との分担は
> [`.claude/skills/org-down/references/rationale.md`](references/rationale.md)）。

> **輸送層の前提**: `claude-org-runtime org down` は broker daemon を落とすコマンドで、broker
> transport（`ORG_TRANSPORT` 無設定＝コード既定 broker、または明示 broker）でのみ意味を持つ。
> renga フォールバック（`ORG_TRANSPORT=renga` で `renga --layout ops` 起動）では daemon の概念が
> 無いので、Step 3 は skip して「renga では daemon 停止は不要」と案内する。判断は Step 3 の
> transport 確認で行う。

## Step 1: /org-suspend を完走する

[`/org-suspend`](../org-suspend/SKILL.md) の全 Phase を順に実行する（Phase 1 状態収集 → Phase 2
未応答スクレイプ → Phase 3 状態書き込み → **Phase 3.6 secretary_queue_watcher 停止** →
**Phase 3.65 context-handover watcher 停止** → **Phase 3.7 attention watcher 停止**（ペイン teardown 前）→ Phase 4 全ペイン停止）。

**ただし Phase 3.5 の dashboard blind kill は本スキル Step 2 の stale-pid-safe 停止に差し替える**
（org down 直前の完全停止では、pid recycle された stale な `dashboard.pid` を無検証 kill すると
無関係プロセスを撃つため。誤 kill 防止）。他の Phase はそのまま実行する。

**suspend 成功の確認（gate）** — 次を満たしたときのみ Step 2 / Step 3 へ進む:

- `.state/state.db` の `session.status` が `SUSPENDED` になっている（Phase 3 の DB write が成立）
- [`.claude/skills/org-suspend/SKILL.md`](../org-suspend/SKILL.md) Phase 4 **手順 7** の最終報告が、
  **`pending` と `unresolved` が両方空で「組織を中断しました」と言い切れた形**になっている。生死の判定は
  Phase 4 が **F-1**（同 Phase 1 手順 1 の「フォーカス前提（F）」）に従って出した結論だけを読み、
  本 gate のために org-down 側で `list_panes` を引き直して判定し直さない（Phase 4 自身の再列挙は
  org-suspend の手順としてそのまま実行する）
- 最後の窓口ペインが残っているのは gate の妨げにならない（窓口は自分自身を close できない。同 Phase 4
  「最後のペイン (窓口) の扱い」）

**gate 不成立なら org down に進まず STOP** する。suspend が abort / 失敗した、Phase 4 が「組織を部分的に
中断しました」を出した（停止できなかったワーカー / `role` が判定できず対象外にしたピア / 他タブに見えたが
自組織と確認できず対象外にしたピアが残る）、生死を判定できないピアが `pending` に残った、のいずれも該当する。状況を人間に報告し、「状態が保存し切れていない
/ ペインの停止を確認し切れていないため daemon は落とさなかった。原因解消後に /org-down を再実行するか、
/org-suspend でいったん中断してください」と案内する（生死を判定できなかったピアが残っている場合は、
Phase 4 手順 7 が列挙したその一覧もそのまま渡す）。判定不能を worker 監視と逆向きに STOP へ倒す
理由は [`.claude/skills/org-down/references/rationale.md`](references/rationale.md)。

## Step 2: ダッシュボードの停止（stale-pid-safe）

`dashboard.pid` は bare pid のみを持つため、recycle された pid を無検証 kill すると無関係プロセスを
撃つ。**pid の生存だけでなく、その cmdline が `dashboard/server.py` であることを照合してから** kill し、
照合が外れたら kill せず stale pid file を削除する。

> **sandbox 注記**: ホストのプロセス確認・kill は Claude Code の Bash sandbox 内では観測できない
> （process namespace 隔離）。本 Step は `dangerouslyDisableSandbox: true` を付けてホスト実行する
> （org-start Block C のダッシュボード起動と同じ理由）。

identity 照合ロジックは helper（`tools/stop_dashboard.py`）に入っているので、POSIX では 1 行呼ぶ
だけでよい。helper は pid の生存に加え、その live argv（Linux/WSL は `/proc`、macOS/BSD は `ps`
フォールバック）が `dashboard/server.py` を含むことを照合できたときだけ SIGTERM し、外れたら kill
せず pid file を stale として削除する。

**Mac / Linux / WSL**:
```bash
python3 tools/stop_dashboard.py   # Windows native は py -3 tools/stop_dashboard.py
```
標準出力の 1 行（`[stop-dashboard] dashboard (pid=N) stopped` / `... 消滅（stale）...` /
`... 起動していない ...`）を Step 4 の報告にそのまま転記する（実際に停止したか stale だったかを
人間に正しく伝えるため）。exit 0 が正常系。exit 2（identity 未確認）は `/proc` も `ps` も無い環境
（Windows native）でのみ出るシグナルで、その場合は次の PowerShell 手順を使う。

**Windows native（PowerShell）** — `Get-CimInstance Win32_Process` の CommandLine で identity を
照合してから `Stop-Process` する:
```powershell
$pf = ".state\dashboard.pid"
if (Test-Path $pf) {
  $dpid = [int]((Get-Content $pf -Raw).Trim())
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$dpid" -ErrorAction SilentlyContinue
  if ($proc -and ($proc.CommandLine -match 'dashboard[\\/]server\.py')) {
    Stop-Process -Id $dpid -Force
    Write-Output "dashboard (pid=$dpid) stopped"
  } else {
    Write-Output "dashboard pid stale or not running; removing stale pidfile (no kill)"
  }
  Remove-Item $pf -ErrorAction SilentlyContinue
}
```

## Step 3: broker daemon の停止（org down）

**Step 1 の suspend gate を満たしたときのみ**実行する。

1. **transport を確認**する:
   ```bash
   echo "${ORG_TRANSPORT:-broker}"
   ```
   - `renga`: daemon の概念が無いので **本 Step は skip**。Step 4 で「renga フォールバックのため
     daemon 停止は不要」と案内する
   - `broker`（無設定を含む）: 次へ

2. broker daemon を停止する（この呼び出しで現在の broker セッション＝窓口ペインを含む tmux も
   終了する。**必ず最後に実行する**）:
   ```bash
   claude-org-runtime org down          # Mac / Linux / WSL
   python3 -m claude_org_runtime.cli org down # Mac / Linux / WSL で console script が PATH に無い場合
   py -3 -m claude_org_runtime.cli org down   # Windows で console script が PATH に無い場合
   ```
   - 正常終了で broker daemon が停止し、以後 `claude-org-runtime org up` → `/org-start` で再起動する
   - 失敗（non-zero exit / エラー出力）した場合は、そのまま人間に報告する。状態保存（Step 1）は
     既に完了しているので、daemon が残っていても `/org-start` 再開には支障しない

> **注**: `org down` 実行後は broker daemon と窓口ペインが落ちるため、本スキルの続き（Step 4 の
> 報告）は `org down` の**前に**準備し、`org down` を最後の操作にする。あるいは `org down` の
> stdout/exit を受けてから最終報告する場合は、端末が閉じる前に人間が読めるよう 1 行で簡潔に出す。

## Step 4: 報告

**broker で daemon まで停止した場合**（`org down` 実行直前に提示。dashboard の行は Step 2 の
標準出力を転記し、`stopped` か `stale`（元々動いていなかった）かを実態どおりに書く — 未確認で
「停止済み」と断定しない）:
```
組織を完全に停止します。
- 状態保存: 完了（.state/state.db = SUSPENDED）
- dashboard: {Step 2 の出力（stopped / stale）}
- 停止済み: secretary_queue_watcher / context-handover watcher / attention watcher / 全ワーカー・ディスパッチャーペイン
- これから broker daemon を停止します（claude-org-runtime org down）。
再開するときは `claude-org-runtime org up` → `/org-start` を実行してください。
```

**renga フォールバックで daemon 停止を skip した場合**:
```
組織を中断しました（renga フォールバックのため broker daemon 停止は不要です）。
- 状態保存: 完了（.state/state.db = SUSPENDED）
- 停止済み: dashboard / context-handover watcher / attention watcher / 全ワーカー・ディスパッチャーペイン
  （secretary_queue_watcher は broker 専用のため renga では非該当）
再開するときは `renga --layout ops` → `/org-start` を実行してください。
```

**suspend gate を満たせず STOP した場合**（Step 1 で分岐済み）: org down は実行せず、未保存の原因、
または停止を確定できなかった（`indeterminate` の）ピアの一覧と、復旧手順（再実行 or /org-suspend）を
人間に報告する。
