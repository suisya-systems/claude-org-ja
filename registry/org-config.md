# Organization Config

## Permission Mode
default_permission_mode: auto

選択肢:
- bypassPermissions: 全許可、確認なし（デフォルト）
- auto: 分類器による安全チェック付き（Team/Enterprise/API プランのみ）
- default: 都度確認
- acceptEdits: ファイル編集のみ自動許可
- dontAsk: 明示許可のみ

### Role別の適用範囲

`default_permission_mode` は Curator / Worker に適用される。他のロールは以下のように扱う:

- **Secretary**: 対象外。`--permission-mode` 未指定の Claude Code デフォルト挙動（ツール実行前に確認プロンプトを表示）を維持する。Secretary は人間との接点であり、人間判断を要する操作の自動承認を避けるため。詳細は Issue #10 を参照。
- **Dispatcher**: `default_permission_mode` の値にかかわらず、固定で `bypassPermissions` を使用する。理由は `.claude/skills/org-start/SKILL.md` の「ディスパッチャー」節を参照。

## Workers Directory
workers_dir: ../workers

ワーカー専用ディレクトリの配置先。claude-org リポジトリからの相対パス。
リポジトリ外に配置することで、ワーカーの新規プロジェクト作成時に親リポジトリの git コンテキストが干渉しない。
