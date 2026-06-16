---
name: sample-wildcard
description: >
  ワイルドカード frontmatter を持つ synthetic skill (golden 固定用)。
  実在スキルではなく generator の render 面を両系で固定するためのフィクスチャ。
effort: low
allowed-tools:
  - Read
  - Bash(bash tools/journal_append.sh:*)
  - mcp__renga-peers__*
---

# sample-wildcard

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイルは **opt-in `renga` 面**（`ORG_TRANSPORT=renga` で render される面）で、ツール呼び出しは `mcp__renga-peers__*` で書いてある。**`ORG_TRANSPORT` 無設定の既定は `broker`** であり、無設定（既定）では MCP サーバー名が `org-broker`・完全修飾名が `mcp__renga-peers__*` → `mcp__org-broker__*` に機械置換される（引数形・セマンティクスは同一なので手順の論理は変わらない）。renga を選んだこの面で、輸送依存に手順が変わる点だけ無設定の既定 broker を併記する:
>
> - **受信モデル**: opt-in renga では worker → dispatcher の peer message が `<channel source="renga-peers" …>` として in-band で push される。**無設定の既定 broker は push 一次**（各ペイン同居の channel sidecar `server:org-broker-channel` が broker キューを ~1 秒間隔で claim→`notifications/claude/channel` で idle セッションへ本文注入。pull = ナッジ + `check_messages` は sidecar 不在 / unhealthy / channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時のフォールバック層）。
> - **spawn 儀式**: opt-in renga は `--dangerously-load-development-channels server:renga-peers` の「Load development channel?」を Enter 承認する 1 段。**無設定の既定 broker は 2 段**（`--mcp-config <broker>` の Claude Code **folder-trust プロンプト**の `send_keys(enter=true)` 機械承認に加え、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプトを `send_keys(enter=true)` で機械承認）。
> - **エラー分岐**: opt-in renga は shared codes（`pane_not_found` / `last_pane` / `invalid-params`）+ renga 固有コードのみ。**無設定の既定 broker は** shared codes に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で escalate）。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8 + push-primary amendment（broker push 一次が **既定の契約**、pull は fallback として retain）。**opt-in `renga` は削除せず常時有効な切戻しの安全装置**として維持する（本ファイルはその rollback 面）。broker 実走（dogfood）は Epic #6 Issue G スコープで無設定の既定運用経路ではない（**二フレーム注記（Refs #604）**: ヘッダの「既定 `broker`」は**コード既定**（`tools/transport.py: DEFAULT_TRANSPORT`、生成面はこれで render）。**運用既定**は broker dogfood が Epic #6 Issue G まで未活性のため `renga` で、本 rollback 面が現運用既定と一致する。両者は指す対象が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)。）

## 手順

1. ワーカーへ `mcp__renga-peers__send_message(to_id="worker-{task_id}", ...)` で ack を返す。
2. 受信は `<channel source="renga-peers" …>` で push される（MCP サーバーは `renga-peers`）。
3. 既定 transport は `broker`。

**spawn 儀式（opt-in renga = dev-channel 承認の 1 段。無設定の既定 broker は 2 段）**: 本ファイルは renga 面なので、`spawn_claude_pane` は `--dangerously-load-development-channels server:renga-peers` を注入し、初回に「Load development channel?」確認プロンプトが表示される。これを `mcp__renga-peers__send_keys(target="worker-{task_id}", enter=true)` で承認する 1 段（未表示段階の Enter は no-op になりうるため、`list_peers` 登録 poll を ground truth とし、未登録なら再送する）。承認しないと dev-channel の登録が完了せず、`list_peers` 待ち・`send_message`・`check_messages` が成立しない。**`ORG_TRANSPORT` 無設定の既定 broker は 2 段**: `--mcp-config <broker>` 注入による Claude Code の **folder-trust プロンプト**承認に**加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b の再導入）を `send_keys(enter=true)` で機械承認する（ratified §5/§8.5 への加算。設計 broker-native-roles.md §9.5）。

> **broker surface の意図的除外注記（transport 非依存）**: `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。renga 面で `mcp__renga-peers__new_tab` / `mcp__renga-peers__focus_pane` を使う手順は、broker では該当ツールが存在しないため adapter 側で代替（`spawn_claude_pane` の tab 生成・`inspect_pane` ベースの観測）に倒す。また **attention watcher は transport 非依存の CLI ペイン**であり、folder-trust / dev-channel いずれの spawn 承認（2 段承認）の**対象外**である（spawn 儀式の反転に巻き込まない）。
