# 各ロールの必要設定

org-setup が参照する、ロールごとの permissions allow と環境変数の定義。

## ユーザー共通 (`~/.claude/settings.json`)

全ロールが必要とする設定。ユーザーレベルに置くことで全サブディレクトリに適用される。

```json
{
  "permissions": {
    "allow": [
      "Bash(ccmux --version)",
      "Bash(ccmux --help)",
      "Bash(ccmux --layout:*)",
      "Bash(ccmux mcp install:*)",
      "Bash(ccmux mcp uninstall:*)",
      "Bash(ccmux mcp status:*)",
      "Bash(ccmux mcp --help)",
      "mcp__ccmux-peers__set_summary",
      "mcp__ccmux-peers__list_peers",
      "mcp__ccmux-peers__send_message",
      "mcp__ccmux-peers__check_messages",
      "mcp__ccmux-peers__list_panes",
      "mcp__ccmux-peers__spawn_pane",
      "mcp__ccmux-peers__close_pane",
      "mcp__ccmux-peers__focus_pane",
      "mcp__ccmux-peers__new_tab",
      "mcp__ccmux-peers__inspect_pane",
      "mcp__ccmux-peers__poll_events",
      "mcp__ccmux-peers__send_keys",
      "mcp__ccmux-peers__spawn_claude_pane",
      "mcp__ccmux-peers__set_pane_identity"
    ]
  },
  "env": {
    "CLAUDE_CODE_NO_FLICKER": "1"
  }
}
```

**Bash permission 方針**: 旧 `Bash(ccmux:*)` glob は撤去済み（ccmux 0.14.0+ でペイン操作・ピア通信・event 購読・スクレイプ・raw キー送信がすべて MCP 化されたため）。残している `Bash(ccmux …)` は **運用コマンド限定**:

- `ccmux --version` / `ccmux --help`: 環境確認
- `ccmux --layout ops` 相当 (`--layout:*`): 初回レイアウト起動（`ccmux-layouts/ops.toml` 参照）
- `ccmux mcp install` / `uninstall` / `status` / `--help`: MCP サーバー登録管理（`mcp__ccmux-peers__*` を使えるようにするための bootstrap）

ペイン操作（`ccmux split` / `close` / `list` / `send` / `events` / `inspect` / `new-tab` 等）は MCP ツール (`mcp__ccmux-peers__*`) 経由で実施する。該当 Bash permission は含めない。

**注意**: `ccmux-peers` MCP ツール 14 種は `ccmux mcp install` を一度実行して user-scope に MCP サーバーを登録した後に利用可能になる。登録手順は README「インストール」セクションを参照。

## 窓口 (`<repo>/.claude/settings.local.json`)

窓口固有の設定。ユーザー共通分はユーザーレベルにあるため、ここには窓口だけが必要なものを書く。

**narrow 方針**: `gh:*` のような機能全体を許す wide allow は避け、`gh issue:*` `gh pr:*` のように**サブコマンドごとに narrow** にする。git も `Bash(git *)`（スペース形式 wildcard）ではなく `Bash(git add:*)` 等の `:*` コロン形式で narrow にする。

```json
{
  "permissions": {
    "allow": [
      "mcp__ccmux-peers__set_summary",
      "mcp__ccmux-peers__list_peers",
      "mcp__ccmux-peers__send_message",
      "mcp__ccmux-peers__check_messages",
      "mcp__ccmux-peers__list_panes",
      "mcp__ccmux-peers__spawn_pane",
      "mcp__ccmux-peers__spawn_claude_pane",
      "mcp__ccmux-peers__close_pane",
      "mcp__ccmux-peers__inspect_pane",
      "mcp__ccmux-peers__poll_events",
      "mcp__ccmux-peers__send_keys",
      "mcp__ccmux-peers__set_pane_identity",

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
      "Bash(git -C ../workers/aainc-ops status)",
      "Bash(git -C ../workers/aainc-ops remote -v)",

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

      "Bash(ccmux --version)",
      "Bash(ccmux --help)",
      "Bash(ccmux --layout:*)",
      "Bash(ccmux mcp install:*)",
      "Bash(ccmux mcp uninstall:*)",
      "Bash(ccmux mcp status:*)",
      "Bash(ccmux mcp --help)",

      "Bash(sleep:*)",
      "Bash(codex exec:*)",
      "Bash(curl -s -o /dev/null -w \"%{http_code}\" http://localhost:8099/:*)",
      "Bash(curl -s http://localhost:8099/ -o /dev/null -w \"%{http_code}\\\\n\")",
      "PowerShell(Out-File *)"
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

**mcp__ccmux-peers__\* の重複**: ユーザー共通 settings.json と重複するが、窓口は run 直後に ccmux-peers MCP を必ず使うため、窓口スコープでも明示的に列挙して source-of-truth として固定する（user settings の drift でも窓口が動くことを保証）。

**ccmux bootstrap の重複**: 同じ理由でユーザー共通と重複するが、窓口が初回レイアウト起動やペイン制御で即時使うため明示列挙。

**並び順**: (1) MCP ツール、(2) git、(3) gh、(4) python/dashboard、(5) ccmux bootstrap、(6) その他（sleep / codex / curl / PowerShell）。新規エントリ追加時はこの並び順を維持する。

**hooks の説明**: `block-workers-delete.sh` は workers ディレクトリへの再帰的削除（`rm -r`/`rm -rf`/`rm --recursive`）をブロックする。個別ファイルの `rm` は許可する。`ccmux` コマンドは除外する（ワーカー起動時の偽陽性防止）。

**書いてはいけないもの**:
- wide allow (`Bash(git *)`, `Bash(git push *)`, `Bash(git fetch *)`, `Bash(git branch *)`, `Bash(git pull *)`, `Bash(gh:*)`, `Bash(gh *)`)
- 旧 `mcp__claude-peers__*`（2025 年に ccmux-peers へ移行済み）
- 旧 `ccmux list/split/send/events/close/inspect *` の Bash allow（ccmux 0.14.0+ で MCP 化）
- 過去の一発コマンド（特定 PR 番号・branch 名・PID を含むコマンド、`gh pr create --repo ... --head feat/xxx ...` 等）
- user-specific absolute path（`Read(//c/Users/iwama/Documents/work/**)` のような）

これらが蓄積すると drift となる。定期的に `permissions.md` と突き合わせて剪定する（Issue #84 参照）。

**重要 — 剪定は手動**: 現行の `org-setup` スキルは additive-only（不足分を追加するだけで既存を削除しない）のため、上記「書いてはいけないもの」のエントリが一度 `settings.local.json` に入ると自動では消えない。`/org-setup` を再実行しても drift は解消されない点に注意。Secretary は定期（例: 月次 / Issue 起票時）に `.claude/settings.local.json` を本ドキュメントの窓口サンプルで丸ごと置き換える剪定運用を行う。自動化する場合は `org-setup` スキル側に「permissions.md サンプルを baseline とし、差分は警告ログに出した上で削除」する mode を追加する必要がある（別 Issue 化を推奨）。

## フォアマン (`<repo>/.foreman/.claude/settings.local.json`)

フォアマンはワーカーペインで claude を起動し、ペイン内容を取得する。

```json
{
  "permissions": {
    "allow": [
      "Bash(claude :*)",
      "Bash(sleep:*)"
    ]
  }
}
```

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

ワーカーの設定は org-delegate の Step 3 で動的に作成される。

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
            "command": "bash \"{aainc_path}/.hooks/check-worker-boundary.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{aainc_path}/.hooks/block-aainc-structure.sh\""
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{aainc_path}/.hooks/block-git-push.sh\""
          },
          {
            "type": "command",
            "command": "bash \"{aainc_path}/.hooks/block-aainc-structure.sh\""
          }
        ]
      }
    ]
  },
  "env": {
    "WORKER_DIR": "{worker_dir}",
    "AAINC_PATH": "{aainc_path}"
  }
}
```

**注意**: `{aainc_path}` と `{worker_dir}` は settings.local.json 生成時に解決済みの絶対パスに置換すること。Hook command 内のパスはスペース対策のためクォートされている。

**deny と hooks の役割分担**: `permissions.deny` は静的パターンマッチによるブロックで、`bypassPermissions` モードでも常に有効。外部コマンド（jq, bash）に依存しないため信頼性が高い。一方 hooks はワーカーディレクトリ境界チェック等の動的検証を担う。両者を併用することで多層防御を実現する。`deny` は `echo foo && git push` のような埋め込みコマンドはカバーできないため、`block-git-push.sh` hook は副次防御として維持する。
