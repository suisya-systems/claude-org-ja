# Projects Registry (Fixture)

`tests/test_registry_parser.test_real_registry_fixture` 用の固定 fixture。
本物の `registry/projects.md` は ja / en / fork で divergence-allowed なので
（[`docs/sync-policy.md`](../../../docs/sync-policy.md) 参照）、
parser のスナップショットテストはこの固定ファイルに対して行う。

claude-org-ja 自身（self-edit）は live registry 同様このフィクスチャにも載せない。
`tools/resolve_worker_layout.py:is_claude_org_project()` が live repo の git origin
URL を見て判定する責務を負う。

| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |
|---|---|---|---|---|
| 時計アプリ | clock-app | - | Webブラウザで動くデジタル時計 | デザイン変更、機能追加 |
| renga | renga | https://github.com/suisya-systems/renga | Rust 製の Claude Code 用ターミナルマルチプレクサ（TUI） | 機能追加、バグ修正、Issue 対応 |
