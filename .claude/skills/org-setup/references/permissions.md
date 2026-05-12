# 各ロールの必要設定

> **Source of truth**: このドキュメントは人間向け説明であり、機械可読な正典は
> ja の [`tools/org_extension_schema.json`](../../../../tools/org_extension_schema.json)
> （org-extension allow / hooks）と `core_harness` の framework schema（型定義）を
> マージしたもの。drift validator `tools/check_role_configs.py` はこの merged schema
> に対して `settings.local.json` を検証し、乖離があれば CI が fail する。ルール追加や
> 文面変更は schema → docs の順で反映すること。

org-setup が参照する、ロールごとの permissions allow と環境変数の定義。

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
      "mcp__renga-peers__set_pane_identity"
    ]
  },
  "env": {
    "CLAUDE_CODE_NO_FLICKER": "1"
  }
}
```

**Bash permission 方針**: 旧 `Bash(renga:*)` glob は撤去済み（renga 0.14.0+ でペイン操作・ピア通信・event 購読・スクレイプ・raw キー送信がすべて MCP 化されたため）。残している `Bash(renga …)` は **運用コマンド限定**:

- `renga --version` / `renga --help`: 環境確認
- `renga --layout ops` 相当 (`--layout:*`): 初回レイアウト起動（`renga-layouts/ops.toml` 参照）
- `renga mcp install` / `uninstall` / `status` / `--help`: MCP サーバー登録管理（`mcp__renga-peers__*` を使えるようにするための bootstrap）

ペイン操作（`renga split` / `close` / `list` / `send` / `events` / `inspect` / `new-tab` 等）は MCP ツール (`mcp__renga-peers__*`) 経由で実施する。該当 Bash permission は含めない。

**注意**: `renga-peers` MCP ツール 14 種は `renga mcp install` を一度実行して user-scope に MCP サーバーを登録した後に利用可能になる。登録手順は README「インストール」セクションを参照。

### ユーザー共通の sandbox denyRead / denyWrite 補強（`--user-common-sandbox`、Issue #429 Task B + Issue #433）

> **⚠️ main pull 後の 1 回必須**: 本リポジトリを clone / pull した後に **`python tools/org_setup_prune.py --user-common-sandbox` を 1 回実行する**。未実行だと共有 `.claude/settings.json` から除去された `~/.ssh` / `~/.aws` 等の sandbox denyRead **および** `~/.claude/settings.json` の sandbox denyWrite が補完されず、**sandbox 防御が一時的に弱くなる**。

`tools/org_setup_prune.py --user-common-sandbox` は `~/.claude/settings.json` の `sandbox.filesystem.denyRead` / `denyWrite` 双方に対し、対象エントリを **idempotent に union-merge** する専用モード。共有 (= リポジトリ) 側の `.claude/settings.json` から個人 path を除去（Issue #429 Task C で denyRead 群、Issue #433 で denyWrite の `~/.claude/settings.json`）した分の defense-in-depth を、個人環境ごとに復元する。**単一フラグで denyRead + denyWrite の両方を処理する**（`--user-common-sandbox-write` のような別フラグは設けない、UX 簡素化方針）。

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

**denyWrite フィルタ規則（denyRead と非対称、Issue #433）**:

- **存在チェック無し**: ファイルが未作成でも entry を merge する。書込防御は **ファイル未作成時点で意味があり**（fresh install では `~/.claude/settings.json` が Claude Code の初回起動時に作成される）、deny を先に置くことで初回作成時から bwrap subprocess の書き込みを構造的に止める。
- **symlink-escape skip も適用しない**: denyWrite の対象は単一ファイル literal で、bwrap が write 先 path を解釈する際に存在しない path を case A で skip するため bootstrap 失敗の risk が無い。symmetric directory denyWrite (`~/.ssh` 等) は **Issue #433 のスコープ外**として deferred（`ssh-keygen` / `aws configure` 等の正規 write を巻き込む副作用が大きく、明確な threat model も無いため）。

**動作（idempotent、denyRead/denyWrite 共通）**:

- 既存の top-level key (`theme`, `env`, `permissions`, etc.) は無触。
- 既存の `sandbox` / `sandbox.filesystem` siblings (`enabled`, `failIfUnavailable`, `additionalDirectories`, および merge 対象でない側の deny list) は保持。
- `denyRead` / `denyWrite` の既存順序を保ちつつ、未追加の candidate のみを後ろに append。重複は skip。
- **denyRead のみ additive + prune**: 上記の「retire リスト」(`USER_COMMON_SANDBOX_DENYREAD_REMOVE`) に列挙された entry が既存 `denyRead` に残っていれば、毎回の実行で **自動的に除去** する。retire 対象が現行候補リストにも入っている場合は除去せず保持（再追加ループを避けるため）。retire リストに無いユーザー追加 entry は一切 touch しない。denyWrite 側には retire リストを設けていない（現状 retire 対象が無いため）。
- malformed shape（`sandbox` が object でない、`denyRead` / `denyWrite` が array でない、entry が string でない、等）は **書込前に `ValueError` で abort**。ユーザーデータの黙示的破壊を防ぐ。denyRead 側で先に shape error が出れば denyWrite merge は実行されない（その逆も同様）。

**使用方法**:

```bash
# diff プレビュー（書き込まない）
python tools/org_setup_prune.py --user-common-sandbox --dry-run

# 実行（既存があれば .bak 自動生成）
python tools/org_setup_prune.py --user-common-sandbox

# 既に全候補が入っていれば no-op
python tools/org_setup_prune.py --user-common-sandbox  # → "no changes"
```

**前提 1（Issue #429 Task A 調査結論を踏まえた含意）**: Claude Code は `permissions.deny` の `Read(...)` を `sandbox.filesystem.denyRead` に **merge する** ([公式 docs](https://code.claude.com/docs/en/settings))。したがって `permissions.deny Read(~/.aws/*)` を共有 / 個人 settings いずれかに書くと、symlink-escape 環境では bwrap bootstrap 失敗が起きうる。本モードが directory-level deny を採用し symlink-escape を skip するのは、Layer 2 (`Read(...)`) と Layer 3 (`sandbox.filesystem.denyRead`) のどちらに書いても同じ failure を踏むためで、Layer 3 側で realpath ベースに前さばきして root-cause を構造的に避ける設計。

**前提 2（denyWrite の merge セマンティクス、Issue #433 で確認）**: 公式 docs の `sandbox.filesystem.denyWrite` 説明にも「Arrays are merged across all settings scopes. Also merged with paths from `Edit(...)` deny permission rules.」と明記されており、denyRead と同じく Layer 2 (`Edit(...)`) と Layer 3 (`sandbox.filesystem.denyWrite`) が effective set として合流する。本モードが Layer 3 側に直接書く理由は、共有 `.claude/settings.json` の `permissions.deny Edit(~/.claude/settings.json)` 経由でも同じ effective deny を作れるが、共有 settings に書くと repo を pull する全ユーザーの home path に対して deny が適用される副作用（個人ごとの opt-out が不可能）が出るため。個人 settings 側に書くと個人環境ごとに撤回可能で、また「個人の `~/.claude/settings.json` を守る」という意図と保管場所が一致する。

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
      "Bash(git stash:*)",
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
      "Write(*/workers/*/.claude/settings.local.json)",
      "Edit(*/workers/*/.claude/settings.local.json)",
      "Write(*/workers/*/.worktrees/*/.claude/settings.local.json)",
      "Edit(*/workers/*/.worktrees/*/.claude/settings.local.json)",
      "Write(*/.worktrees/*/.claude/settings.local.json)",
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
            "command": "bash .hooks/block-workers-delete.sh"
          }
        ]
      }
    ]
  }
}
```

**mcp__renga-peers__\* の重複**: ユーザー共通 settings.json と重複するが、窓口は run 直後に renga-peers MCP を必ず使うため、窓口スコープでも明示的に列挙して source-of-truth として固定する（user settings の drift でも窓口が動くことを保証）。

**`permissions.deny` (Issue #99 Phase 2 で追加)**: ワーカー設定ファイル（`workers/<project>/.claude/settings.local.json` および worktree パス `workers/<project>/.worktrees/<task>/.claude/settings.local.json`）への **Claude の `Write` / `Edit` ツール経由の直接編集**を窓口に対して禁止する。窓口は通常モード起動（`bypassPermissions` ではない）なので、この `permissions.deny` は静的パターンマッチで常に効く。

ただしこの deny は Claude のファイル編集ツール（Write/Edit）系のゲートに限定される。窓口は引き続き `Bash(python:*)` / `Bash(python3:*)` / `PowerShell(Out-File *)` を allow しているため、Bash/PowerShell から `cat > settings.local.json` のように書き出すことは技術的に可能。本 deny は **「窓口が手作業で `Edit` ツールを開いて settings を書き換える」** という主要な誤付与経路を塞ぐためのもので、`claude-org-runtime settings generate` 以外の経路を完全に遮断するものではない。完全な generator-only 化（Bash 側の遮断を含む）は Phase 3 の課題（drift CI 拡張・escape hatch と併走）。

**renga bootstrap の重複**: 同じ理由でユーザー共通と重複するが、窓口が初回レイアウト起動やペイン制御で即時使うため明示列挙。

**並び順**: (1) MCP ツール、(2) git、(3) gh、(4) python/dashboard、(5) renga bootstrap、(6) その他（sleep / codex / curl / PowerShell）。新規エントリ追加時はこの並び順を維持する。

**hooks の説明**: `block-workers-delete.sh` は workers ディレクトリへの再帰的削除（`rm -r`/`rm -rf`/`rm --recursive`）をブロックする。個別ファイルの `rm` は許可する。`renga` コマンドは除外する（ワーカー起動時の偽陽性防止）。

**書いてはいけないもの**:
- wide allow (`Bash(git *)`, `Bash(git push *)`, `Bash(git fetch *)`, `Bash(git branch *)`, `Bash(git pull *)`, `Bash(gh:*)`, `Bash(gh *)`)
- 旧 `mcp__claude-peers__*`（2026 年に renga-peers へ移行済み）
- 旧 `renga list/split/send/events/close/inspect *` の Bash allow（renga 0.14.0+ で MCP 化）
- 過去の一発コマンド（特定 PR 番号・branch 名・PID を含むコマンド、`gh pr create --repo ... --head feat/xxx ...` 等）
- user-specific absolute path（`Read(//c/Users/<you>/Documents/work/**)` のような）

これらが蓄積すると drift となる。定期的に `permissions.md` と突き合わせて剪定する。

**剪定（drift 解消）は `--prune` モードで自動化済み**: 上記「書いてはいけないもの」のエントリが `settings.local.json` に蓄積した場合、`tools/org_setup_prune.py` で本ドキュメントの role 別サンプルを SOT として丸ごと書き換えられる。

```bash
python tools/org_setup_prune.py --role secretary --dry-run   # diff プレビュー
python tools/org_setup_prune.py --role secretary             # 実行（.bak を自動生成）
python tools/org_setup_prune.py --all                        # secretary / dispatcher / curator まとめて
```

**user 拡張の保護**: 個人で追加した allow / env / hook を残すには、各 settings ファイルと**同じディレクトリ**に `settings.local.override.json` を置く。prune 時に deep-merge され、ツールはこの override ファイルを書き換えない。詳細は `.claude/skills/org-setup/SKILL.md` の Step 5 参照。

## ディスパッチャー (`<repo>/.dispatcher/.claude/settings.local.json`)

ディスパッチャーはワーカーペインで claude を起動し、ペイン内容を取得する。

**重要**: ディスパッチャーは Sonnet 制約により `permission_mode=bypassPermissions` で起動するため、`permissions.allow` と `permissions.deny` は **両方とも bypass される**（Claude Code 公式仕様）。実効的な書き込み境界・git 制限は **PreToolUse フックでしか強制できない**。下記 `hooks.PreToolUse` がディスパッチャーの唯一の障壁であり、削除・無効化してはいけない。

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
      }
    ]
  },
  "env": {
    "CLAUDE_ORG_PATH": "{claude_org_path}"
  }
}
```

**注意**: `{claude_org_path}` は settings.local.json 生成時に解決済みの絶対パスに置換すること。Hook command 内のパスはスペース対策のためクォートされている。

**hooks の役割分担**:
- `block-dispatcher-out-of-scope.sh`: ディスパッチャーの Edit/Write 対象パスを `.dispatcher/`, `.state/`, `knowledge/raw/YYYY-MM-DD-{topic}.md` に限定。アプリケーションコード（`tools/`, `dashboard/`, `tests/`, `.claude/skills/`, `docs/`, `registry/` 等）の編集はワーカーへの委譲を強制する
- `block-git-push.sh`: ディスパッチャーからの直接 push を禁止（push は窓口経由）
- `block-dangerous-git.sh`: `git push --force` / `git reset --hard` / `git branch -D` をブロック
- `block-workers-delete.sh`: workers ディレクトリの再帰削除をブロック（ワーカー成果物の保護）
- `block-no-verify.sh`: `--no-verify` 系の検証バイパスをブロック

## キュレーター (`<repo>/.curator/.claude/settings.local.json`)

キュレーターは知見整理のみ。追加の Bash 許可は不要。

```json
{
  "permissions": {
    "allow": []
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
      "Bash(git stash:*)",
      "Bash(sleep:*)"
    ],
    "deny": [
      "Bash(git push *)",
      "Bash(git push)",
      "Bash(git worktree)",
      "Bash(git worktree *)",
      "Bash(git fetch)",
      "Bash(git fetch *)",
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
      "Bash(rm -r *)"
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
      }
    ]
  },
  "env": {
    "WORKER_DIR": "{worker_dir}",
    "CLAUDE_ORG_PATH": "{claude_org_path}"
  }
}
```

**注意**: `{claude_org_path}` と `{worker_dir}` は settings.local.json 生成時に解決済みの絶対パスに置換すること。Hook command 内のパスはスペース対策のためクォートされている。

**deny と hooks の役割分担**: ワーカーは通常モード（`bypassPermissions` ではない）で起動するため、`permissions.deny` は静的パターンマッチで常に効く。外部コマンド（jq, bash）に依存しないので信頼性が高い。一方 hooks はワーカーディレクトリ境界チェック等の動的検証を担う。両者を併用することで多層防御を実現する。`deny` は `echo foo && git push` のような埋め込みコマンドはカバーできないため、`block-git-push.sh` / `block-dangerous-git.sh` / `block-no-verify.sh` hook は副次防御として維持する。なお `bypassPermissions` で起動するロール（ディスパッチャー）では `permissions.deny` は bypass されるので、そちらは hook のみが障壁になる（前述 `重要` 節参照）。

**Phase 2 worker git guardrails (Refs #379)**: 上記 `deny` / `hooks` は worker-git-guardrails-design.md §5 / §6 / §11 cheat sheet の即時項目を反映する。`Bash(git worktree*)` deny family は Pattern B 共有 base clone の `.git/worktrees/<other>` への副作用（remove / prune / lock 等）を Layer 2 で全 deny する（§6）。`block-dangerous-git.sh` / `block-no-verify.sh` の attach はワーカー作業ディレクトリが claude-org repo 外の場合に発生していた cwd-tree non-inheritance gap を埋める（§5.1 / §5.2）。`Bash(git fetch / pull / remote / submodule / lfs / gc / filter-branch / replace / update-ref / config --{global,local,worktree} / reflog {expire,delete})` の deny family は §4.6 (N) network deny + §5.2.4 history-rewrite / config write deny を Layer 2 で固定する。
