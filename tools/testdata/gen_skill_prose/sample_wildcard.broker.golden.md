---
name: sample-wildcard
description: >
  ワイルドカード frontmatter を持つ synthetic skill (golden 固定用)。
  実在スキルではなく generator の render 面を両系で固定するためのフィクスチャ。
effort: low
allowed-tools:
  - Read
  - Bash(bash tools/journal_append.sh:*)
  - mcp__org-broker__check_messages
  - mcp__org-broker__close_pane
  - mcp__org-broker__inspect_pane
  - mcp__org-broker__list_panes
  - mcp__org-broker__list_peers
  - mcp__org-broker__poll_events
  - mcp__org-broker__send_keys
  - mcp__org-broker__send_message
  - mcp__org-broker__set_pane_identity
  - mcp__org-broker__set_summary
  - mcp__org-broker__spawn_claude_pane
  - mcp__org-broker__spawn_pane
---

# sample-wildcard

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の `mcp__org-broker__*` 呼び出しは **既定 `broker`**（`ORG_TRANSPORT` 無設定）で書いてあり、そのまま従えばよい（既定挙動）。`ORG_TRANSPORT=renga`（opt-in・切戻し可）では MCP サーバー名が `renga-peers` になり、ツールの **完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので手順の論理は変わらない）。輸送依存で手順が変わる点だけ renga 併記する:
>
> - **受信モデル**: 既定 broker は **push 一次**（各ペイン同居の channel sidecar `server:org-broker-channel` が broker キューを ~1 秒間隔で claim→`notifications/claude/channel` で idle セッションへ本文注入。pull = ナッジ + `check_messages` は sidecar 不在 / unhealthy / channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時のフォールバック層）。`ORG_TRANSPORT=renga` 時は dispatcher / worker メッセージが `<channel source="renga-peers" …>` として in-band で push される。
> - **spawn 儀式**: 既定 broker は `--mcp-config <broker>` 注入による Claude Code **folder-trust プロンプト**の `send_keys(enter=true)` 機械承認に加え、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプトを `send_keys(enter=true)` で機械承認する（2 段承認）。`ORG_TRANSPORT=renga` 時は `--dangerously-load-development-channels server:renga-peers` の「Load development channel?」を Enter 承認する 1 段。
> - **エラー分岐**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生しない。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8 + push-primary amendment（broker push 一次が **既定の契約**、pull は fallback として retain）。**opt-in `renga` は削除せず常時有効な切戻しの安全装置**として維持する。broker 実走（dogfood）は Epic #6 Issue G スコープで本ファイルの既定運用経路ではない（**二フレーム注記（Refs #604）**: ここの「既定 `broker`」は**コード既定**（`tools/transport.py: DEFAULT_TRANSPORT`、生成面はこれで render）。**運用既定**は broker dogfood が Epic #6 Issue G まで未活性のため `renga` で、両者は指す対象が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)。）

## 手順

1. ワーカーへ `mcp__org-broker__send_message(to_id="worker-{task_id}", ...)` で ack を返す。
2. 受信は `<channel source="org-broker" …>` で push される（MCP サーバーは `org-broker`）。
3. 既定 transport は `broker`。

**spawn 儀式（既定 broker = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: `spawn_claude_pane` は `--mcp-config <broker>`（daemon）を注入し、初回に Claude Code の **folder-trust プロンプト**（「Do you trust the files in this folder?」相当）が出る。これを `mcp__org-broker__send_keys(target="worker-{task_id}", enter=true)` で機械承認する。**加えて push 一次のため**、`spawn_claude_pane` は channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load するため、**「Load development channel?」プロンプトが再出現**する（`--mcp-config`-only 設計で一旦消えた 3-3b 承認の broker 枝での再導入）。これも `mcp__org-broker__send_keys(target="worker-{task_id}", enter=true)` で機械承認する。両プロンプトの順序は boot タイミング依存なので、`inspect_pane` で各プロンプトの表示を確認しつつ順に Enter 承認する（未表示段階の Enter は no-op になりうるため、`list_peers` 登録 poll を ground truth とし、未登録なら再送する）。承認しないと broker token のバインド／channel sidecar の登録が完了せず、`list_peers` 待ち・`send_message`（push 配送）・フォールバックの `check_messages` が成立しない。これは ratified §5/§8.5 の folder-trust フローへの **加算であり置換ではない**（設計 transport-lab `docs/design/broker-native-roles.md` §9.5。S3 で contract §5.1/§8.5 を amend 済み・2026-06-15 ratified）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。

> **broker surface の意図的除外注記（transport 非依存）**: `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。renga 面で `mcp__renga-peers__new_tab` / `mcp__renga-peers__focus_pane` を使う手順は、broker では該当ツールが存在しないため adapter 側で代替（`spawn_claude_pane` の tab 生成・`inspect_pane` ベースの観測）に倒す。また **attention watcher は transport 非依存の CLI ペイン**であり、folder-trust / dev-channel いずれの spawn 承認（2 段承認）の**対象外**である（spawn 儀式の反転に巻き込まない）。
