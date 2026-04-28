# 各ロールの必要設定

> **Source of truth**: このドキュメントは人間向け説明であり、機械可読な正典は
> [`tools/role_configs_schema.json`](../../../../tools/role_configs_schema.json)。
> 本ファイルの JSON ブロックと schema の間に drift があれば CI
> (`tools/check_role_configs.py`) が fail する。ルール追加や
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
      "Edit(*/workers/*/.claude/settings.local.json)"
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

**`permissions.deny` (Issue #99 Phase 2 で追加)**: ワーカー設定ファイル (`workers/*/.claude/settings.local.json`) への直接 Write/Edit を窓口に対して禁止する。ワーカー設定は `tools/generate_worker_settings.py` 経由でしか書き換えられないようにし、窓口の手書き編集による permission 過大付与を構造的に防ぐ。`bypassPermissions` モードでも常に有効。

**renga bootstrap の重複**: 同じ理由でユーザー共通と重複するが、窓口が初回レイアウト起動やペイン制御で即時使うため明示列挙。

**並び順**: (1) MCP ツール、(2) git、(3) gh、(4) python/dashboard、(5) renga bootstrap、(6) その他（sleep / codex / curl / PowerShell）。新規エントリ追加時はこの並び順を維持する。

**hooks の説明**: `block-workers-delete.sh` は workers ディレクトリへの再帰的削除（`rm -r`/`rm -rf`/`rm --recursive`）をブロックする。個別ファイルの `rm` は許可する。`renga` コマンドは除外する（ワーカー起動時の偽陽性防止）。

**書いてはいけないもの**:
- wide allow (`Bash(git *)`, `Bash(git push *)`, `Bash(git fetch *)`, `Bash(git branch *)`, `Bash(git pull *)`, `Bash(gh:*)`, `Bash(gh *)`)
- 旧 `mcp__claude-peers__*`（2025 年に renga-peers へ移行済み）
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

> **Phase 2 以降 (Issue #99)**: ワーカーの `settings.local.json` は `tools/generate_worker_settings.py` が `tools/role_configs_schema.json` の `worker_roles[<role>]` から生成する（`default` / `claude-org-self-edit` / `doc-audit` の 3 role）。本セクションに掲載されている JSON はあくまでリファレンス用で、手書き編集は禁止（drift CI が fail する）。新しい permission パターンが必要な場合は schema に role を追加する PR を起こすこと。

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
      "Bash(git worktree:*)",
      "Bash(git stash:*)",
      "Bash(sleep:*)"
    ],
    "deny": [
      "Bash(git push *)",
      "Bash(git push)",
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

**deny と hooks の役割分担**: `permissions.deny` は静的パターンマッチによるブロックで、`bypassPermissions` モードでも常に有効。外部コマンド（jq, bash）に依存しないため信頼性が高い。一方 hooks はワーカーディレクトリ境界チェック等の動的検証を担う。両者を併用することで多層防御を実現する。`deny` は `echo foo && git push` のような埋め込みコマンドはカバーできないため、`block-git-push.sh` hook は副次防御として維持する。
