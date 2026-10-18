# 各ロールの必要設定

> **正典**: 正典は [`tools/org_extension_schema.json`](../../../../tools/org_extension_schema.json)
> （org-extension allow / hooks）と `core_harness` の framework schema（型定義）をマージしたもの。
> 本ファイルの role 別 JSON はその schema を満たすテンプレートで、`/org-setup` と
> `tools/org_setup_prune.py` が settings ファイルへ書き出す。`tools/check_role_configs.py` が
> テンプレートと各 `settings.local.json` を schema と照合し、乖離があれば CI が fail する。
> ルールの追加や変更は schema → 本ファイルの順で反映すること。

org-setup が参照する、ロールごとの permissions allow と環境変数の定義。設計理由は [`.claude/skills/org-setup/references/rationale.md`](rationale.md)。

## 目次

- ユーザー共通（`~/.claude/settings.json`）
  - ユーザー共通の sandbox denyRead / denyWrite 補強（`--user-common-sandbox`）
- 窓口（`<repo>/.claude/settings.local.json`）
- ディスパッチャー（`<repo>/.dispatcher/.claude/settings.local.json`）
- キュレーター（`<repo>/.curator/.claude/settings.local.json`）
- ワーカー（動的生成）

`{claude_org_path}` と `{worker_dir}` は、settings.local.json の生成時に解決済みの絶対パスへ置換する。hook command 内のパスはスペース対策のためクォートしている。相対パス（`bash .hooks/...`）で書くと、cwd が org ルート以外のロール（ディスパッチャーの cwd は `.dispatcher/`）では hook が解決されず、ガードが**エラーも出さずに無効化**する。

## ユーザー共通 (`~/.claude/settings.json`)

全ロールが必要とする設定。ユーザーレベルに置くことで全サブディレクトリに適用される。

```json
{
  "permissions": {
    "allow": [
      "Bash(renga --version)",
      "Bash(renga --help)",
      "Bash(renga --layout:*)",
      "Bash(renga mcp install:*)",
      "Bash(renga mcp uninstall:*)",
      "Bash(renga mcp status:*)",
      "Bash(renga mcp --help)",
      "mcp__renga-peers__set_summary",
      "mcp__renga-peers__list_peers",
      "mcp__renga-peers__send_message",
      "mcp__renga-peers__check_messages",
      "mcp__renga-peers__list_panes",
      "mcp__renga-peers__spawn_pane",
      "mcp__renga-peers__close_pane",
      "mcp__renga-peers__focus_pane",
      "mcp__renga-peers__new_tab",
      "mcp__renga-peers__inspect_pane",
      "mcp__renga-peers__poll_events",
      "mcp__renga-peers__send_keys",
      "mcp__renga-peers__spawn_claude_pane",
      "mcp__renga-peers__set_pane_identity",
      "mcp__renga-peers__server_info"
    ]
  },
  "env": {
    "CLAUDE_CODE_NO_FLICKER": "1"
  }
}
```

**Bash permission 方針**: 旧 `Bash(renga:*)` glob は撤去済み（renga 0.14.0+ でペイン操作・ピア通信・event 購読・スクレイプ・raw キー送信がすべて MCP 化されたため）。残している `Bash(renga …)` は **運用コマンド限定**（現行 org の renga transport サポート下限は 2.0.0）:

- `renga --version` / `renga --help`: 環境確認
- `renga --layout ops` 相当 (`--layout:*`): 初回レイアウト起動（`renga-layouts/ops.toml` 参照）
- `renga mcp install` / `uninstall` / `status` / `--help`: MCP サーバー登録管理（`mcp__renga-peers__*` を使えるようにするための bootstrap）

ペイン操作（`renga split` / `close` / `list` / `send` / `events` / `inspect` / `new-tab` 等）は MCP ツール (`mcp__renga-peers__*`) 経由で実施する。該当 Bash permission は含めない。

**注意**: `renga-peers` MCP ツール 15 種は `renga mcp install` を一度実行して user-scope に MCP サーバーを登録した後に利用可能になる。登録手順は README「インストール」セクションを参照。

### ユーザー共通の sandbox denyRead / denyWrite 補強（`--user-common-sandbox`）

> **初回セットアップで 1 回実行する**: 本リポジトリを clone したら **`python3 tools/org_setup_prune.py --user-common-sandbox`**（Windows native は `py -3`）を 1 回実行する。冪等なので再実行しても害はない。未実行だと、共有 `.claude/settings.json` に置いていない `~/.ssh` / `~/.aws` 等の sandbox denyRead **および** `~/.claude/settings.json` の sandbox denyWrite が補完されず、**sandbox 防御が欠けたままになる**。書き先の `~/.claude/settings.json` は sandbox 内から書けないので、実行は [`.claude/skills/org-setup/SKILL.md`](../SKILL.md#sandbox-内からは書けない書き先窓口ユーザー共通) の「sandbox 内からは書けない書き先」節の 2 段手順で行う。

`tools/org_setup_prune.py --user-common-sandbox` は `~/.claude/settings.json` の `sandbox.filesystem.denyRead` / `denyWrite` 双方に対し、対象エントリを **idempotent に union-merge** する専用モード。共有 (= リポジトリ) 側の `.claude/settings.json` から個人 path（denyRead 群と denyWrite の `~/.claude/settings.json`）を除去した分の defense-in-depth を、個人環境ごとに復元する。**単一フラグで denyRead + denyWrite の両方を処理する**（`--user-common-sandbox-write` のような別フラグは設けない、UX 簡素化方針）。

**denyRead 対象ディレクトリ（候補）**: `~`-prefixed の literal で保存され、ユーザー間で共有されても portable:

- `~/.ssh`
- `~/.aws`
- `~/.kube`
- `~/.gnupg`
- `~/.docker`
- `~/.config/aws-vault`

**denyRead フィルタ規則**:

- **存在しないディレクトリは skip**。`~/.docker` が無い環境では entry を追加しない（bwrap launcher の case A 扱いを先回り）。
- **realpath が HOME を escape する symlink は skip**。WSL2 + DriveFS で `~/.aws → /mnt/c/Users/<name>/.aws` のようになっている場合、bwrap の bootstrap mount が失敗する（`bwrap: Can't create file at /home/<user>/.aws/config: No such file or directory`）。これは claude-org-runtime の Layer 3 generator (`role_configs_schema.json#$comment_sandbox_anchor` の `suppressOnSymlinkEscape=True`) と同じ判定を user_common 側でも先回りする。

**候補リストから恒久的に除外しているもの**（候補定数 `USER_COMMON_SANDBOX_DENYREAD_CANDIDATES` 自体に含めない）:

- `~/.config/gh`: gh CLI は窓口（Secretary）の業務動線（push / PR 作成 / CI 監視 / review feedback ループ / merge cleanup）で必須のため deny しない。defense-in-depth と運用継続性のトレードオフを評価し、後者を優先する判断とした。`~/.config/gh` は `USER_COMMON_SANDBOX_DENYREAD_REMOVE` の retire リストに登録されており、過去に `--user-common-sandbox` を実行して個人 `~/.claude/settings.json` の `sandbox.filesystem.denyRead` に当該 entry が残っているユーザーの環境では、次回 `--user-common-sandbox` 実行時に **自動的に除去** される（自動的な additive + prune セマンティクス）。ユーザーが手で追加した他の entry は touch しない。

**候補リストには残るが実行時に skip されるケース**（候補定数には残るが merge 時に弾かれる）:

- `~/.aws` の HOME-escape symlink ケース（WSL2 + DriveFS）: 上記「realpath が HOME を escape する symlink は skip」に従い実行時に自動 skip される。候補リストからは除外しないため、symlink を解消すれば次回実行で deny が効くようになる。

**denyWrite 対象ファイル（候補）**: `~`-prefixed の *file* literal:

- `~/.claude/settings.json`

**denyWrite フィルタ規則（denyRead と非対称）**:

- **存在チェック無し**: ファイルが未作成でも entry を merge する。書込防御は **ファイル未作成時点で意味があり**（fresh install では `~/.claude/settings.json` が Claude Code の初回起動時に作成される）、deny を先に置くことで初回作成時から bwrap subprocess の書き込みを構造的に止める。
- **symlink-escape skip も適用しない**: denyWrite の対象は単一ファイル literal で、bwrap が write 先 path を解釈する際に存在しない path を case A で skip するため bootstrap 失敗の risk が無い。
- directory 単位の denyWrite（`~/.ssh` 等）は配らない（理由は rationale.md）。

**動作（idempotent、denyRead/denyWrite 共通）**:

- 既存の top-level key (`theme`, `env`, `permissions`, etc.) は無触。
- 既存の `sandbox` / `sandbox.filesystem` siblings (`enabled`, `failIfUnavailable`, `additionalDirectories`, および merge 対象でない側の deny list) は保持。
- `denyRead` / `denyWrite` の既存順序を保ちつつ、未追加の candidate のみを後ろに append。重複は skip。
- **denyRead のみ additive + prune**: 上記の「retire リスト」(`USER_COMMON_SANDBOX_DENYREAD_REMOVE`) に列挙された entry が既存 `denyRead` に残っていれば、毎回の実行で **自動的に除去** する。retire 対象が現行候補リストにも入っている場合は除去せず保持（再追加ループを避けるため）。retire リストに無いユーザー追加 entry は一切 touch しない。denyWrite 側には retire リストを設けていない（現状 retire 対象が無いため）。
- malformed shape（`sandbox` が object でない、`denyRead` / `denyWrite` が array でない、entry が string でない、等）は **書込前に `ValueError` で abort**。ユーザーデータの黙示的破壊を防ぐ。denyRead 側で先に shape error が出れば denyWrite merge は実行されない（その逆も同様）。

コマンドは [`.claude/skills/org-setup/SKILL.md`](../SKILL.md) Step 6。

**`permissions.deny` の `Read(~/.aws/*)` 等も同じ失敗を踏む**: `Read(...)` deny は `sandbox.filesystem.denyRead` に合流するため（https://code.claude.com/docs/en/settings）、symlink-escape 環境では共有 / 個人 settings のどちらに書いても bwrap の bootstrap が失敗しうる。本モードが realpath で前さばきするのはこのため（詳細は rationale.md）。

**詳細**: 実装は `tools/org_setup_prune.py` の `merge_user_common_sandbox_denyread` / `merge_user_common_sandbox_denywrite` / `filter_existing_user_dirs` / `process_user_common_sandbox`（denyRead/denyWrite の merge は共通の `_merge_sandbox_deny` ヘルパー経由）、テストは `tools/test_org_setup_prune.py` の `MergeUserCommonSandboxTests` / `MergeUserCommonSandboxDenywriteTests` / `UserCommonSandboxEndToEndTests` クラス群。

## 窓口 (`<repo>/.claude/settings.local.json`)

窓口固有の設定。ユーザー共通分はユーザーレベルにあるため、ここには窓口だけが必要なものを書く。

**narrow 方針**: `gh:*` のような機能全体を許す wide allow は避け、`gh issue:*` `gh pr:*` のように**サブコマンドごとに narrow** にする。git も `Bash(git *)`（スペース形式 wildcard）ではなく `Bash(git add:*)` 等の `:*` コロン形式で narrow にする。

```json
{
  "permissions": {
    "allow": [
      "mcp__renga-peers__set_summary",
      "mcp__renga-peers__list_peers",
      "mcp__renga-peers__send_message",
      "mcp__renga-peers__check_messages",
      "mcp__renga-peers__list_panes",
      "mcp__renga-peers__spawn_pane",
      "mcp__renga-peers__spawn_claude_pane",
      "mcp__renga-peers__close_pane",
      "mcp__renga-peers__inspect_pane",
      "mcp__renga-peers__poll_events",
      "mcp__renga-peers__send_keys",
      "mcp__renga-peers__set_pane_identity",
      "mcp__renga-peers__server_info",

      "Bash(git add:*)",
      "Bash(git commit:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(git branch:*)",
      "Bash(git checkout:*)",
      "Bash(git switch:*)",
      "Bash(git push:*)",
      "Bash(git worktree:*)",
      "Bash(git fetch:*)",
      "Bash(git pull:*)",
      "Bash(git -C ../workers/claude-org status)",
      "Bash(git -C ../workers/claude-org remote -v)",

      "Bash(gh issue:*)",
      "Bash(gh pr:*)",
      "Bash(gh label:*)",
      "Bash(gh api:*)",
      "Bash(gh gist:*)",
      "Bash(gh run:*)",
      "Bash(gh auth status)",
      "Bash(gh auth login:*)",

      "Bash(python:*)",
      "Bash(python3:*)",
      "Bash(py -3 dashboard/:*)",
      "Bash(py -3 tools/:*)",
      "Bash(py dashboard/:*)",

      "Bash(renga --version)",
      "Bash(renga --help)",
      "Bash(renga --layout:*)",
      "Bash(renga mcp install:*)",
      "Bash(renga mcp uninstall:*)",
      "Bash(renga mcp status:*)",
      "Bash(renga mcp --help)",

      "Bash(sleep:*)",
      "Bash(codex exec:*)",
      "Bash(curl -s -o /dev/null -w \"%{http_code}\" http://localhost:8099/:*)",
      "Bash(curl -s http://localhost:8099/ -o /dev/null -w \"%{http_code}\\\\n\")",
      "PowerShell(Out-File *)"
    ],
    "deny": [
      "Edit(*/workers/*/.claude/settings.local.json)",
      "Edit(*/workers/*/.worktrees/*/.claude/settings.local.json)",
      "Edit(*/.worktrees/*/.claude/settings.local.json)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-workers-delete.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/gate-acceptance-before-merge.sh\"",
            "timeout": 900
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/check-dispatcher-loop.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/remind-verify-premise.sh\""
          }
        ]
      }
    ]
  },
  "sandbox": {
    "network": {
      "allowedDomains": ["api.anthropic.com"]
    }
  }
}
```

**`sandbox.network.allowedDomains`**: `/work-discovery` の判定段（`claude -p`）が `api.anthropic.com` に出るための許可。窓口の sandbox 本体（`enabled` / `filesystem`）はリポジトリ直下の追跡済み [`.claude/settings.json`](../../../settings.json) にあり、ここでは足さない（この local ファイルはサブディレクトリで起動したディスパッチャーにも読まれるため、`enabled` を足すとディスパッチャーにも sandbox が掛かる。https://code.claude.com/docs/en/settings 「Where Claude Code keeps the local file in a git repository」）。`allowedDomains` の配列は各スコープの値が合流する（https://code.claude.com/docs/en/settings-reference `sandbox.network.allowedDomains`）。[`tools/check_role_configs.py`](../../../../tools/check_role_configs.py) が schema（`roles.<role>.sandbox.network.allowedDomains`）との一致を検査する。

**ユーザー共通との重複**: `mcp__renga-peers__*` と renga bootstrap はユーザー共通と重複して列挙する（理由は rationale.md）。

**`permissions.deny`**: ワーカー設定ファイル（`workers/<project>/.claude/settings.local.json` および worktree パス `workers/<project>/.worktrees/<task>/.claude/settings.local.json`、加えて live-repo worktree の `.worktrees/<task>/.claude/settings.local.json`）への **Claude の `Edit` ツール経由の直接編集**を窓口に対して禁止する。窓口は通常モード起動（`bypassPermissions` ではない）なので、この `permissions.deny` は静的パターンマッチで常に効く。

**`Write(...)` を併記しない理由**: Claude Code の file permission check は `Edit(path)` のみを評価し、`Write(path)` の deny は実効しない。宣言すると 1 エントリにつき 1 行、起動のたびに `Permission deny rule (.claude/settings.local.json): Write(...) is not matched by file permission checks -- only Edit(path) rules are. Use Edit(...) instead` という警告が出るだけで、防御が死んでいるという誤読を招く。同一 glob の `Edit(...)` が防御を全て担っている。**ここに `Write(...)` を足し戻さないこと。**

この deny は `Edit` ツールだけのゲートで、Bash / PowerShell からの書き出しは塞がない（限界は rationale.md）。

**並び順**: (1) MCP ツール、(2) git、(3) gh、(4) python/dashboard、(5) renga bootstrap、(6) その他（sleep / codex / curl / PowerShell）。新規エントリ追加時はこの並び順を維持する。

**hooks の説明**: `block-workers-delete.sh` は workers ディレクトリへの再帰的削除（`rm -r`/`rm -rf`/`rm --recursive`）をブロックする。個別ファイルの `rm` は許可する。`renga` コマンドは除外する（ワーカー起動時の偽陽性防止）。`check-dispatcher-loop.sh` は窓口のターンごとに [`tools/dispatcher_loop_watch.py`](../../../../tools/dispatcher_loop_watch.py) を呼び、稼働中ワーカーがいるのにディスパッチャーの見回り（`relay_scan.py --cycle` の heartbeat）が 9 分を超えて止まっていれば `DISPATCHER_LOOP_UNARMED: ...` を 1 行だけ窓口の文脈に出す（同じ状態では 1 回きり）。ブロックはせず、判定できないときは何も出さない。`remind-verify-premise.sh` はユーザーの文に数値+単位・`key=value` の設定・挙動の主張の語（「はず」「既定」「閾値」等）があるときだけ、[`tools/premise_check.py`](../../../../tools/premise_check.py) が「同意・反論を書く前に 1 回読むか検索して出典を付ける」という `VERIFY_BEFORE_AGREE: ...` を 1 行窓口の文脈に出す。誤って出しても 1 行増えるだけなのでブロックはせず、判定の規則は単純にしてある（出る例・出ない例は [`tools/test_premise_check.py`](../../../../tools/test_premise_check.py) で固定）。`gate-acceptance-before-merge.sh` は窓口の `gh pr merge` の手前で [`tools/acceptance_merge_hook.py`](../../../../tools/acceptance_merge_hook.py) を呼び、その PR の head と base を fetch して受け入れテストのゲート [`tools/acceptance_gate.py`](../../../../tools/acceptance_gate.py) を走らせる。通すマージは `gh pr merge <番号> --repo <OWNER/REPO> --squash|--merge|--rebase [--delete-branch]` の形のコマンド単独だけで、ほかの書き方のマージは拒否する。rejected と判定できないときは拒否し、`Acceptance-Test` の宣言が無い PR は記録だけして通す。ゲートは pytest を bubblewrap の中で 2 回走らせるので `timeout` を 900 秒にしてある（タイムアウトしたフックはブロックしない扱いになるため、python 側は 840 秒で打ち切って拒否する）。`bwrap` の無い環境では、宣言のある PR のマージを拒否する。

**書いてはいけないもの**:
- wide allow (`Bash(git *)`, `Bash(git push *)`, `Bash(git fetch *)`, `Bash(git branch *)`, `Bash(git pull *)`, `Bash(gh:*)`, `Bash(gh *)`)
- 旧 `mcp__claude-peers__*`（2026 年に renga-peers へ移行済み）
- 旧 `renga list/split/send/events/close/inspect *` の Bash allow（renga 0.14.0+ で MCP 化。現行 org の renga transport サポート下限は 2.0.0）
- 過去の一発コマンド（特定 PR 番号・branch 名・PID を含むコマンド、`gh pr create --repo ... --head feat/xxx ...` 等）
- user-specific absolute path（`Read(//c/Users/<you>/Documents/work/**)` のような）

これらが蓄積すると drift となる。剪定は `--prune` モード（個人拡張は `settings.local.override.json` で保護）で行う。手順は [`.claude/skills/org-setup/SKILL.md`](../SKILL.md) Step 5。

## ディスパッチャー (`<repo>/.dispatcher/.claude/settings.local.json`)

ディスパッチャーはワーカーペインで claude を起動し、ペイン内容を取得する。

**重要**: ディスパッチャーは `permission_mode=bypassPermissions` で起動する。auto モードの安全分類器が、ワーカー起動に使う `spawn_claude_pane`（子エージェント起動）を止めるため（[`.claude/skills/org-start/SKILL.md`](../../org-start/SKILL.md) の 419 行目）。このため`permissions.allow` と `permissions.deny` は **両方とも bypass される**（Claude Code 公式仕様）。実効的な書き込み境界・git 制限は **PreToolUse フックでしか強制できない**。下記 `hooks.PreToolUse` がディスパッチャーの唯一の障壁であり、削除・無効化してはいけない。

```json
{
  "permissions": {
    "allow": [
      "Bash(claude :*)",
      "Bash(sleep:*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-dispatcher-out-of-scope.sh\""
          }
        ]
      },
      {
        "matcher": "CronCreate|ScheduleWakeup",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/check-loop-directive.sh\""
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-git-push.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-dangerous-git.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-workers-delete.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-no-verify.sh\""
          }
        ]
      },
      {
        "matcher": "mcp__.*__(close_pane|set_pane_identity)",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-relative-close-pane.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-close-before-retro.sh\""
          }
        ]
      },
      {
        "matcher": "mcp__.*__send_keys",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-blind-spawn-keys.sh\""
          }
        ]
      },
      {
        "matcher": "mcp__.*__spawn_claude_pane",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/check-worker-spawn-model.sh\""
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/context-record.sh\""
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/context-record.sh\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/context-record.sh\""
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/context-record.sh\""
          }
        ]
      }
    ]
  },
  "idleCompaction": false,
  "claudeMdExcludes": ["{claude_org_path}/CLAUDE.md"],
  "env": {
    "CLAUDE_ORG_PATH": "{claude_org_path}",
    "CLAUDE_ORG_HANDOVER_THRESHOLD_TOKENS": "600000"
  },
  "sandbox": {
    "network": {
      "allowedDomains": ["api.anthropic.com"]
    }
  }
}
```

**`claudeMdExcludes`**: ディスパッチャーの cwd は `.dispatcher/` で、その祖先にある窓口用の root `CLAUDE.md` が起動時に読み込まれる。これを外す（パターンは絶対パスに glob で照合される。https://code.claude.com/docs/en/memory.md 「Exclude specific CLAUDE.md files」）。外れたかは、ディスパッチャーで `/context` を実行し **Memory files** に root `CLAUDE.md` が無いことで確かめる。

**`sandbox.network.allowedDomains`**: worker クローズ時の work-discovery scan の判定段（`claude -p`）が `api.anthropic.com` に出るための許可。窓口と同じく `enabled` は足さない。ディスパッチャーの cwd（`.dispatcher/`）には追跡済みの `.claude/settings.json` が無く、窓口の local ファイルにも `enabled` が無いため、現状ディスパッチャーの Bash は sandbox 外で動く（共有ファイルは起動ディレクトリから読む: https://code.claude.com/docs/en/settings 「Where Claude Code keeps the local file in a git repository」）。permission mode は tool call を走らせるかを決め、sandbox は走った Bash の到達先を制限する別の層である（https://code.claude.com/docs/en/sandboxing 「Permission modes」）ため、将来ディスパッチャーの sandbox を有効にしても判定段が止まらないよう先に配っておく。

**hooks の役割分担**:
- `check-loop-directive.sh`: `/loop` 監視ループの武装 (`CronCreate` / `ScheduleWakeup`) を canonical 正文に固定する。正文の SoT は [`.claude/skills/dispatcher-resume/SKILL.md`](../../dispatcher-resume/SKILL.md) Step 5 の fenced code block（編集 SoT は `SKILL.md.in`）で、フックは実行時にそこから読み出すため正文の写経を持たない。自己流の短縮版は relay 実配送（`--list` → `send_message` → `--mark-delivered`）を落として relay 層を「滞留を報告するだけ」に退化させるため、空白を除いたうえで正文と完全一致しない prompt を deny する（正文への前置き・後置きも通さない。`CronCreate` は interval を剥がした本文のみ、`ScheduleWakeup` は `/loop 3m <本文>` の丸ごとも可）。**ディスパッチャー専用**であり、ワーカーの完了後 bounded `/loop`・キュレーター・窓口の `/loop` 用途を巻き込まないよう他ロールには配らない。判定の理由は [`.dispatcher/references/loop-directive-guard.md`](../../../../.dispatcher/references/loop-directive-guard.md)
- `block-dispatcher-out-of-scope.sh`: ディスパッチャーの Edit/Write 対象パスを `.dispatcher/`, `.state/`, `knowledge/raw/YYYY-MM-DD-{topic}.md` に限定。アプリケーションコード（`tools/`, `dashboard/`, `tests/`, `.claude/skills/`, `docs/`, `registry/` 等）の編集はワーカーへの委譲を強制する
- `block-git-push.sh`: ディスパッチャーからの直接 push を禁止（push は窓口経由）
- `block-dangerous-git.sh`: 素の `git push --force` / `-f` および protected branch (main / master / develop / release/* / production) への `git push --force-with-lease` をブロック。非保護 branch への `--force-with-lease` は許容（PR rebase / squash 後の安全な再 push 用、Issue #470）。ただし refspec 未指定 / `HEAD` / `@` / wildcard refspec / `--all` / `--mirror` / `--tags` / branch 以外の namespace (`refs/tags/*` / `refs/notes/*` / `refs/replace/*` 等) / `git push origin tag <name>` 形式の宛先が曖昧なケースは安全側で deny。加えて `git reset --hard` / `git branch -D` / `git clean -f` / `git checkout -- .` / `git restore --source` / `git tag -d` / `git update-ref -d` / `git reflog expire --all` 等の破壊的操作も引き続きブロック。さらに `git stash` の変更系（引数なしの bare 形 / `push` / `save` / `pop` / `apply` / `branch` / `drop` / `clear` / `store` / `create`）を allowlist 方式で deny する（調査用の `list` / `show` のみ許可）。`refs/stash` は base clone の共有 git dir に解決されるため、ある worktree の `pop` が別 worktree の退避を復元しうること、および未追跡の非通常ファイル（キャラクタデバイス）があると `-u` 形が途中失敗することが理由。下記 allow には `Bash(git stash:*)` を置かない（runtime の bundled schema と同期して削除済み）
- `block-workers-delete.sh`: workers ディレクトリの再帰削除をブロック（ワーカー成果物の保護）
- `block-no-verify.sh`: `--no-verify` 系の検証バイパスをブロック
- `block-relative-close-pane.sh`: `close_pane` / `set_pane_identity` の宛先が数値 pane id でないとき（`target` 省略 / `"focused"` / 裸の name）に deny する。裸 name の例外は `close_pane` の `pr-watch-<PR>` のみ（DD-2 stale-binding carve-out）。**ディスパッチャーは `bypassPermissions` で動き `permissions.deny` が評価されないため、このフックだけが障壁になる**。窓口はプロジェクトルート直下の [`.claude/settings.json`](../../../settings.json) から同じフックを継承するので、そちらには重複登録しない。設計と残余は [`docs/design/close-pane-guard.md`](../../../../docs/design/close-pane-guard.md)
- `block-close-before-retro.sh`: 振り返りの完了報告ゲートを通っていない worker ペインへの `close_pane` を deny する。数値 pane id を `worker_spawn_verified` で task に引き、`retro_gate_acked`（`tools/dispatcher_retro_gate.py` が acked 時に記録）・`retro_gate_confirmed_missing`（replied_no_ack で未着を確定したとき dispatcher が `tools/journal_append.sh` で記録）・`runs.status` が `completed` / `failed` / `abandoned`・`completion_reported_at` あり かつ `runs.status == 'review'` のどれかがあれば通す（`completed` と最後のものは [`.dispatcher/references/pane-close.md`](../../../../.dispatcher/references/pane-close.md)「0. 受領記録の確認」の ack 相当）。worker 以外のペインと `set_pane_identity` は素通しする。**ディスパッチャー専用**
- `block-blind-spawn-keys.sh`: キーを含む `send_keys`（`keys` が空でない、または `enter: true`）を、`claude-org-runtime dispatcher spawn-prompt-step --target <宛先>` が書いた判定記録（`<state-dir>/dispatcher/spawn-prompt/<宛先>.json`、runtime 0.1.50+）と照合し、記録が無い・期限切れ（30 秒）・宛先違い・キー不一致なら deny する。`action: "done"` の記録は Enter 単発だけを通す（[`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-5a の kickoff）。通した記録は消す（1 回限り）。`text` だけの送信は判定しない。folder-trust の初期カーソルは「No, exit」で、盲目 Enter はワーカーを終了させるため（3-3b）。**ディスパッチャー専用**（窓口の `/clear` や承認ハンドシェイクは止めない）
- `check-worker-spawn-model.sh`: `role: "worker"` の `spawn_claude_pane` で `model` が `"sonnet"` か `"opus"` と明示されていなければ deny する（ワーカーの既定は sonnet。runtime helper は `model` 省略時に opus を既定にするため、省略を通さない）。`"opus"` は、ペイン名から `worker-` を外した task_id の `.state/dispatcher/inbox/<task_id>.json` に `"model": "opus"` があるとき（DELEGATE 本文に `モデル: opus` 行があったとき）だけ通す。worker 以外の role は素通しする。**ディスパッチャー専用**
- `context-record.sh`（`SessionStart` / `UserPromptSubmit` / `Stop` / `PreCompact`）: 文脈の自動引き継ぎのために、会話記録の場所・ペイン id・開いているターンの有無・`Stop` が受け取る背景タスクと予約の一覧を `.state/context/dispatcher.json` に書く（[`tools/context_record.py`](../../../../tools/context_record.py)）。`PreCompact` では自動要約を止めずに `context_compacted_before_handover` を events に 1 行書く。何も出力せずブロックもしない。閾値の判定と `/dispatcher-handover` → `/clear` → `/dispatcher-resume` の打鍵は、`/org-start` が起動する [`tools/context_handover_watch.py`](../../../../tools/context_handover_watch.py) が外から行う（設計は [`docs/design/context-auto-handover.md`](../../../../docs/design/context-auto-handover.md)）。**ディスパッチャー専用**（窓口の自動引き継ぎは次段）

**`idleCompaction: false`**: idle 中の自動要約が自動引き継ぎより先に走らないようにする（設計 §2.2）。

**引き継ぎの閾値**: 上の `env` の `CLAUDE_ORG_HANDOVER_THRESHOLD_TOKENS`（トークン数。雛形は 600000 = window 1M の 60%）をフックが読んで記録に書き、watcher が使う。window が 1M でないときはこの値を window の 60% に合わせる。この変数が無ければ `CLAUDE_CODE_AUTO_COMPACT_WINDOW` の 60% を使い、どちらも無ければ閾値を記録せず、watcher は引き継ぎをせずに窓口へ `threshold_unset` を 1 回知らせる（window の大きさは仮定しない）。

## キュレーター (`<repo>/.curator/.claude/settings.local.json`)

キュレーターは知見整理のみ。追加の Bash 許可は不要。`close_pane` ガードだけは全ロール共通で掛ける（キュレーターの project dir は `.curator/` なので、リポジトリルートの `.claude/settings.json` を継承しない）。

```json
{
  "permissions": {
    "allow": []
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__.*__(close_pane|set_pane_identity)",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-relative-close-pane.sh\""
          }
        ]
      }
    ]
  },
  "env": {
    "CLAUDE_ORG_PATH": "{claude_org_path}"
  }
}
```

## ワーカー（動的生成）

ワーカーの設定は org-delegate の Step 1.5 で動的に作成される。

> ワーカーの `settings.local.json` は `claude-org-runtime settings generate` が同パッケージにバンドルされた merged role schema の `worker_roles[<role>]` から生成する（`default` / `claude-org-self-edit` / `doc-audit` の 3 role）。本セクションに掲載されている JSON はあくまでリファレンス用で、手書き編集は禁止（drift CI が fail する）。新しい permission パターンが必要な場合は schema に role を追加する PR を起こすこと。

```json
{
  "permissions": {
    "allow": [
      "Bash(git add:*)",
      "Bash(git commit:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(git branch:*)",
      "Bash(git checkout:*)",
      "Bash(git switch:*)",
      "Bash(git fetch:*)",
      "Bash(git merge:*)",
      "Bash(sleep:*)"
    ],
    "deny": [
      "Bash(git push *)",
      "Bash(git push)",
      "Bash(git worktree)",
      "Bash(git worktree *)",
      "Bash(git pull)",
      "Bash(git pull *)",
      "Bash(git submodule)",
      "Bash(git submodule *)",
      "Bash(git lfs)",
      "Bash(git lfs *)",
      "Bash(git gc)",
      "Bash(git gc *)",
      "Bash(git filter-branch)",
      "Bash(git filter-branch *)",
      "Bash(git filter-repo)",
      "Bash(git filter-repo *)",
      "Bash(git replace)",
      "Bash(git replace *)",
      "Bash(git update-ref)",
      "Bash(git update-ref *)",
      "Bash(git config --global *)",
      "Bash(git config --local *)",
      "Bash(git config --worktree *)",
      "Bash(rm -rf *)",
      "Bash(rm -r *)",
      "Read(.env)",
      "Read(.env.*)",
      "Read(!.env.example)",
      "Read(!.env.*.example)",
      "Read(!**/.env.example)",
      "Read(!**/.env.*.example)",
      "Read(**/credentials*)",
      "Read(**/*.pem)",
      "Read(~/.aws/*)",
      "Read(~/.ssh/*)",
      "mcp__renga-peers__close_pane",
      "mcp__org-broker__close_pane"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/check-worker-boundary.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-org-structure.sh\""
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-codex-over-cap.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-git-push.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-dangerous-git.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-no-verify.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-org-structure.sh\""
          }
        ]
      },
      {
        "matcher": "mcp__.*__close_pane",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-relative-close-pane.sh\""
          }
        ]
      },
      {
        "matcher": "mcp__.*__(spawn_pane|spawn_codex_pane|spawn_claude_pane|send_keys|new_tab)",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{claude_org_path}/.hooks/block-codex-over-cap.sh\""
          }
        ]
      }
    ]
  },
  "env": {
    "WORKER_DIR": "{worker_dir}",
    "CLAUDE_ORG_PATH": "{claude_org_path}"
  }
}
```

**`.env` テンプレートの除外（runtime 0.1.43+）**: `Read(!.env.example)` など `!` で始まる 4 行は `Read(.env)` / `Read(.env.*)` の**直後**に置く。`!` の deny パターンは同じ settings ファイルで**前に**並ぶルールからだけ一致分を除外する（https://code.claude.com/docs/en/permissions 「Read and Edit」）ので、順序を入れ替えると `.env.example` が再び読めなくなる。4 パターンは `claude_org_runtime.settings.generator.ENV_TEMPLATE_PATTERNS` で、sandbox の `filesystem.allowRead` と同じ集合（[`tools/check_role_configs.py`](../../../../tools/check_role_configs.py) が一致を検査する）。

**deny と hooks の役割分担**: `deny` は `echo foo && git push` のような埋め込みコマンドはカバーできないため、`block-git-push.sh` / `block-dangerous-git.sh` / `block-no-verify.sh` hook を副次防御として維持する（多層防御の説明は rationale.md）。

**ワーカー専用の hooks**:
- `block-codex-over-cap.sh`: Codex セルフレビューの往復上限をワーカーの外で強制する（判定本体は [`tools/codex_round_cap.py`](../../../../tools/codex_round_cap.py)）。引用符とバックスラッシュを除いて `codex` を含む Bash コマンドだけを見る。判定は既定 deny で、単純コマンドのどこかに `codex` / `codex.js` / `@openai/codex` の語があれば、引数を実行しない読み取り系コマンド（`grep` / `cat` / `echo` / `git` / `which` / `command -v` / `npm i` 等の許可リスト）以外は deny する。空白を含む引数（`bash -c` / `su -c` / `env -S` / `python -c` の文字列）は入れ子のコマンドとして再判定し、標準入力・ヒアドキュメント・`-c`・`eval` / `xargs` でテキストを実行するシェルに `codex` を含むテキストが渡る形と、`codex` を含む `$(...)` / バッククォートも deny する。MCP の `spawn_codex_pane` は常に deny、`spawn_pane` / `new_tab` の `command` と `send_keys` の `text` は Bash と同じ判定にかける（Codex ゲートは Bash からだけ通す）。Codex ゲートは `bash "$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" <base-ref>`（差分レビュー）と `bash "$CLAUDE_ORG_PATH/tools/codex_review_gate.sh" --design <file>`（設計レビュー。パスは空白やシェル記号を含まない 1 語）の単独の形だけ通す。正規のゲート呼び出しは 1 回ごとに events へ `codex_round_started` を記録し、タスクの許可回数（既定 3）を超えたら `codex_round_denied` を記録して `CODEX_ROUND_DENIED` で deny する。設計レビューは別枠でタスクごとに 1 回固定（payload の `mode=design`）で、差分の許可回数を消費も参照もしない。task はワーカーの `WORKER_DIR` と一致する最新の `delegate_sent` から引き、許可回数を変えられるのは窓口の `python3 tools/codex_round_cap.py set --task-id <id> --allowed <N|unlimited> --reason <理由>` だけ。フックは sandbox の外で動き、ワーカーから読み取り専用の ja root `.state/state.db` に書く。`$STATE_DB_PATH` は見ない。DB が無い・task を引けない・判定が異常終了したときは deny する（fail-closed）。変数展開・グロブ・ブレース展開・`$'...'`・別ファイルのスクリプト・他言語で組み立てた文字列・許可リストのコマンドの実行機能（`git` の alias / pager、`sed` の `e`）・`send_keys` の `keys` 1 文字ずつの送信を経由した `codex` 実行はコマンド文字列から判別できない（既知の限界）
- `check-worker-boundary.sh` は Worker ディレクトリ内でも `.claude/settings.json` / `.claude/settings.local.json` への Edit/Write を deny する。強制フックと env がここに載るため、変更は窓口へのエスカレーション経由にする

**worker git guardrails**: 上の JSON は `check_role_configs.py` が照合する監査下限で、実際の生成元 `worker_roles.default` の deny はこれより広い。生成元は素の `git fetch:*` を allow したうえで、`git -C * fetch` / `git -C * pull`、`git remote add|set-url|remove|rm`、`git reflog expire|delete` と、それぞれの `git -C *` 形を deny する。設計は [`docs/contracts/worker-git-guardrails-design.md`](../../../../docs/contracts/worker-git-guardrails-design.md)（§4.6 network、§5.2.4 template deny、§6 worktree）。

**sandbox の外で動くコマンド**: `default` / `claude-org-self-edit` の生成元は、docker の 4 形に加えて `git rebase origin/main` と差分レビューの Codex ゲート `bash "<claude_org_path>/tools/codex_review_gate.sh" origin/main`（パスの引用符あり・なしの 2 形）だけを `sandbox.excludedCommands` に載せ、同じ字面の allow を置く。sandbox 内の rebase は書込みが拒否される `.claude/skills` 等を書き換えられず、旧内容を残すため。どれもワイルドカードの無い完全一致なので、別の引数・`--design` 形・連結・リダイレクト・`cd`・`$(...)` を含む呼び出しは sandbox 内に残る。完全一致であることと allow の対応は `check_role_configs.py` の `check_excluded_commands_exact` が検査する。
