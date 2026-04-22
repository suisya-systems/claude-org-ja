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
      "mcp__ccmux-peers__send_keys"
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

**注意**: `ccmux-peers` MCP ツール 12 種は `ccmux mcp install` を一度実行して user-scope に MCP サーバーを登録した後に利用可能になる。登録手順は README「ccmux MCP サーバーの登録」を参照。

## 窓口 (`<repo>/.claude/settings.local.json`)

窓口固有の設定。ユーザー共通分はユーザーレベルにあるため、ここには窓口だけが必要なものを書く。

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
      "Bash(git push:*)",
      "Bash(git worktree:*)",
      "Bash(gh:*)",
      "Bash(start:*)",
      "Bash(python:*)",
      "Bash(sleep:*)"
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

**hooks の説明**: `block-workers-delete.sh` は workers ディレクトリへの再帰的削除（`rm -r`/`rm -rf`/`rm --recursive`）をブロックする。個別ファイルの `rm` は許可する。`ccmux` コマンドは除外する（ワーカー起動時の偽陽性防止）。

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
