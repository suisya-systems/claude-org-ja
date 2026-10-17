# org-setup の設計理由

手順には要らないが、変更するときに知っておくべき理由をまとめる。手順は [`.claude/skills/org-setup/SKILL.md`](../SKILL.md)、各ロールのテンプレートは [`.claude/skills/org-setup/references/permissions.md`](permissions.md)。

## `{claude_org_path}` の解決で root を採る条件

audit root を `{claude_org_path}` に採るのは、`<root>/.hooks/` に schema の `required_hook_scripts` が全て実在する場合だけにしている。`.hooks/` ディレクトリの実在だけを条件にすると、たまたま同名ディレクトリを持つ無関係なプロジェクトを org root として受理し、存在しないスクリプトを指す hook（＝生成時点で死んでいるガード）を書いてしまうため。実装は `tools/org_setup_prune.py` の `_root_as_claude_org_path`。

## ユーザー共通の sandbox 補強

### directory 単位の denyWrite を見送った理由

`~/.ssh` 等の directory 単位の denyWrite は見送っている。`ssh-keygen` / `aws configure` 等の正規 write を巻き込む副作用が大きく、明確な threat model も無いため。

### Layer 2 と Layer 3 の merge（denyRead）

Claude Code は `permissions.deny` の `Read(...)` を `sandbox.filesystem.denyRead` に **merge する** ([公式 docs](https://code.claude.com/docs/en/settings))。したがって `permissions.deny Read(~/.aws/*)` を共有 / 個人 settings いずれかに書くと、symlink-escape 環境では bwrap bootstrap 失敗が起きうる。本モードが directory-level deny を採用し symlink-escape を skip するのは、Layer 2 (`Read(...)`) と Layer 3 (`sandbox.filesystem.denyRead`) のどちらに書いても同じ failure を踏むためで、Layer 3 側で realpath ベースに前さばきして root-cause を構造的に避ける設計。

### Layer 2 と Layer 3 の merge（denyWrite）

公式 docs の `sandbox.filesystem.denyWrite` 説明にも「Arrays are merged across all settings scopes. Also merged with paths from `Edit(...)` deny permission rules.」と明記されており、denyRead と同じく Layer 2 (`Edit(...)`) と Layer 3 (`sandbox.filesystem.denyWrite`) が effective set として合流する。本モードが Layer 3 側に直接書く理由は、共有 `.claude/settings.json` の `permissions.deny Edit(~/.claude/settings.json)` 経由でも同じ effective deny を作れるが、共有 settings に書くと repo を pull する全ユーザーの home path に対して deny が適用される副作用（個人ごとの opt-out が不可能）が出るため。個人 settings 側に書くと個人環境ごとに撤回可能で、また「個人の `~/.claude/settings.json` を守る」という意図と保管場所が一致する。

## 窓口テンプレート

### ユーザー共通と重複して列挙するもの

**mcp__renga-peers__\* の重複**: ユーザー共通 settings.json と重複するが、窓口は run 直後に renga-peers MCP を必ず使うため、窓口スコープでも明示的に列挙して source-of-truth として固定する（user settings の drift でも窓口が動くことを保証）。

**renga bootstrap の重複**: 同じ理由でユーザー共通と重複するが、窓口が初回レイアウト起動やペイン制御で即時使うため明示列挙。

### ワーカー設定への Edit deny の限界

この deny は Claude のファイル編集ツール（`Edit`）のゲートに限定される。窓口は引き続き `Bash(python:*)` / `Bash(python3:*)` / `PowerShell(Out-File *)` を allow しているため、Bash/PowerShell から `cat > settings.local.json` のように書き出すことは技術的に可能。本 deny は **「窓口が手作業で `Edit` ツールを開いて settings を書き換える」** という主要な誤付与経路を塞ぐためのもので、`claude-org-runtime settings generate` 以外の経路を完全に遮断するものではない。完全な generator-only 化（Bash 側の遮断を含む）は Phase 3 の課題（drift CI 拡張・escape hatch と併走）。

## ワーカーテンプレート: deny と hooks の役割分担

ワーカーは通常モード（`bypassPermissions` ではない）で起動するため、`permissions.deny` は静的パターンマッチで常に効く。外部コマンド（jq, bash）に依存しないので信頼性が高い。一方 hooks はワーカーディレクトリ境界チェック等の動的検証を担う。両者を併用することで多層防御を実現する。

## ディスパッチャーから root `CLAUDE.md` を外す理由

Claude Code は起動ディレクトリから上の階層にある CLAUDE.md を起動時に読み込む（https://code.claude.com/docs/en/memory.md）。ディスパッチャーの cwd は `<repo>/.dispatcher/` なので、何もしなければ窓口用の root `CLAUDE.md` も読み込まれ、読む量が増えるうえ役割を取り違える原因になる。`.dispatcher/CLAUDE.md` に打ち消し文を書くのではなく、ディスパッチャーの settings の `claudeMdExcludes` で読み込み自体を外す。同ページ「Exclude specific CLAUDE.md files」によれば、パターンは絶対パスに glob で照合され、local を含むどの settings 層にも書ける。外れたかは、ディスパッチャーで `/context` を実行し **Memory files** に root `CLAUDE.md` が無いことで確かめる。
