# Organization Config

> **同期注意**: CLAUDE.md には変数展開機構がないため、`.claude/skills/**` および `docs/contracts/role-contract.md` 内の `permission_mode` は `auto` リテラル直書きでハードコードされている（session #15 で Secretary が `acceptEdits` を誤代入した回帰を機に固定化）。このファイルの値を変更しただけでは skill / docs 側に反映されない。`default_permission_mode` を変更する場合は、以下も併せて手で書き換えること（`tools/gen_delegate_payload.py` だけはこのファイルを実行時に読むので追従する。`grep -rn '"auto"' .claude/skills docs/contracts` で取りこぼし確認）:
>
> - `.claude/skills/org-start/SKILL.md`
> - `.claude/skills/org-delegate/SKILL.md`
> - `.claude/skills/org-delegate/references/pane-layout.md`
> - `docs/contracts/role-contract.md`

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

## Max Concurrent Workers
max_concurrent_workers: 8

broker 面（`ORG_TRANSPORT=broker` / コード既定）でのワーカー同時並列上限。runtime 0.1.31 / #104（backend-aware worker capacity）で導入。dispatcher が `claude-org-runtime dispatcher delegate-plan` helper に `--transport broker --max-concurrent-workers <値>` として明示で渡し、helper は rect geometry の balanced split（`choose_split`）を**バイパス**して「アクティブ worker 数 < 上限なら固定 spawn target で spawn / 到達で `split_capacity_exceeded`」を返す（runtime は panes snapshot から transport を推定しないため dispatcher が `ORG_TRANSPORT` を解決して明示で渡す契約）。

- **既定 8**。`unlimited`（上限なし）は opt-in。
- **renga 面（`ORG_TRANSPORT=renga`, opt-in）では効かない**: renga はターミナルサイズと MIN_PANE 制約が許す限り分割し続ける rect ベース balanced split が律速するため、`max_concurrent_workers` は参照されない。詳細は [`.claude/skills/org-delegate/references/pane-layout.md`](../.claude/skills/org-delegate/references/pane-layout.md)「ワーカーの balanced split 戦略」と [`.dispatcher/CLAUDE.md`](../.dispatcher/CLAUDE.md) の delegate-plan helper 節を参照。
- 判定ロジック・定数の正準 SoT は `claude_org_runtime.dispatcher.runner`（runtime 側）。本ファイルの値は dispatcher が helper へ渡す運用値の導線。
