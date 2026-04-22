# ccmux-peers MCP error codes — Foreman / Secretary reference

ccmux 0.14.0+ の `ccmux-peers` MCP サーバは、エラー応答に安定した machine-readable な code を載せる。フォアマン / キュレーター / 窓口は message 文字列の substring match ではなく **code で分岐する**のを推奨する。

## Wire format

MCP ツール（`mcp__ccmux-peers__*`）が失敗すると、JSON-RPC error の human-readable message 先頭に `[<code>] <human message>` が埋まる。ccmux 側の `fmt_code` 関数がこの形式を保証。

```
mcp__ccmux-peers__send_message(to_id="worker-nonexistent", message="hi")
→ ccmux refused send: [pane_not_found] pane not found: Name("worker-nonexistent")
```

抽出方法: tool result text を substring match（`[pane_not_found]` 等で case 分岐）。

## Known codes

| Code | 意味 | Foreman の推奨挙動 |
|---|---|---|
| `pane_not_found` | 指定した pane 名 / id / Focused が存在しない | そのワーカーは既に閉じた扱い。`.state/workers/worker-*.md` の status を `pane_closed` に遷移、`WORKER_PANE_EXITED` を窓口に通知。リトライしない。**注意**: `list_panes` / `focus_pane` / `send_message` / `inspect_pane` は現在フォーカス中のタブのペインしか見えない。別タブ (`new_tab` 由来) のワーカーは本 code で返るので、org-delegate では全ワーカーを同一タブ内 `spawn_pane` で起動する (happy-ryo/ccmux#71) |
| `pane_vanished` | resolve 成功後に消えたレース | `pane_not_found` と同等扱い |
| `last_pane` | `close_pane` で唯一のタブの唯一のペインを閉じようとした | 通常のワーカー停止では発生しない (窓口/フォアマン/キュレーターが同タブに同居するため)。`org-suspend` 末端で残った最後のペイン (通常は窓口) に対して発生した場合、そのペインは自分自身で `exit` して自然終了させる。強制再試行はしない |
| `split_refused` | `spawn_pane` が MAX_PANES / too small で拒否 | ワーカー起動 (`org-delegate` Step 3) で balanced split のいずれかのステップが 16 ペイン上限 / `MIN_PANE_WIDTH` / `MIN_PANE_HEIGHT` で拒否された場合、キュレーター → 窓口に escalate。典型シナリオは (a) 9 並列以上に到達、(b) ターミナル幅が balanced split の要件 (W ≥ 160) を満たさない、(c) ワーカー退役後の再派遣でレイアウト tree が想定と乖離。`new_tab` フォールバックは tab-scoped 制約のため不可 (happy-ryo/ccmux#71) |
| `io_error` | PTY write / spawn / OS レベル失敗 | 1 サイクル spin して再試行。2 連続で同じ worker に出たら窓口に `IO_ERROR_DETECTED` で escalate |
| `shutting_down` | ccmux 本体がシャットダウン中 | 監視ループを **即停止** する。窓口 (`secretary`) に ccmux-peers で `FOREMAN_STOPPING` を通知（best-effort — ccmux 自体が落ちる場合は届かない） |
| `app_timeout` | ccmux 内部 App スレッドが応答しなかった | 1 サイクル spin (ccmux 再起動は管理者判断)。連続発生なら窓口にログ |
| `parse` / `protocol` | 通常出ない (MCP が正しく組み立てる前提) | 発生時はバグ。journal に記録して窓口に `IPC_PROTOCOL_ERROR` で報告 |
| `internal` | ccmux 内部不変条件違反 (parser lock poison 等) | `app_timeout` と同じ扱い |

## MCP ツール特有の ok-return ルール

以下 2 つの MCP ツールは、ccmux 到達不可でも **JSON-RPC error にせず ok-text で返す** 例外扱い。

- `mcp__ccmux-peers__list_peers`: ccmux 本体未起動 / detached mode → `"(no peers — ccmux not reachable: <reason>)"`
- `mcp__ccmux-peers__send_message`: 同上 → `"(message dropped — ccmux not reachable: <reason>)"`

他の ccmux-peers ツール (`spawn_pane` / `close_pane` / `list_panes` / `focus_pane` / `new_tab` /
`check_messages` / `set_summary` / `poll_events` / `inspect_pane` / `send_keys`) は `require_connected` で非接続時に JSON-RPC error になる。この 2 つだけは**ハンドリング分岐を `[code]` パターンだけでなく `(no peers` / `(message dropped` 接頭辞**でも見るべき。

## シェル側のハンドリング例

MCP ツール呼び出し結果テキスト (`content[0].text` or JSON-RPC error message) に対する case 分岐:

```
# MCP ツール呼び出し後、返ってきたテキストを $out に入れた状態を想定
case "$out" in
  *"[pane_not_found]"*|*"[pane_vanished]"*)
    # worker 既に閉じた — lifecycle 処理に回す
    mark_worker_pane_closed worker-foo
    ;;
  *"[last_pane]"*)
    # org-suspend 末端で最後のペインを閉じようとした
    # 強制クローズしない。当該ペインは自分自身で exit
    echo "last pane — leave for self-exit"
    ;;
  *"[shutting_down]"*)
    echo "ccmux halting — foreman stopping"
    exit 0
    ;;
  *"[io_error]"*|*"[app_timeout]"*|*"[internal]"*)
    log_journal "transient ccmux error: $out"
    ;;
  *"(no peers"*|*"(message dropped"*)
    # list_peers / send_message の ccmux 非接続時の ok-text
    log_journal "ccmux peer unreachable: $out"
    ;;
  *)
    log_journal "unexpected ccmux error: $out"
    ;;
esac
```

## なぜ code か、substring ではなく

- メッセージ本文は human-facing。理由なしで変更される可能性がある
  (e.g. "pane not found: Id(3)" → "pane 3 does not exist")
- ccmux 側の契約については、以下を正本として参照する (このリポジトリ内では検証不能な前提なので **外部依存** として扱うこと):
  - `ccmux/src/ipc/mod.rs::err_code` の doc コメント — 公開 code の一覧と ABI 安定性 (rename は deprecation window 付き) の明文
  - `ccmux/src/mcp_peer/mod.rs::fmt_code` — MCP 経由の `[<code>] <message>` 成形ロジック
  - ccmux `Response::Err { message, code }` の wire schema — `code` は `Option<String>` で、`skip_serializing_if = "Option::is_none"`
- 未知の code は必ず非致命扱いにする — 将来 ccmux が新 code を追加してもフォアマンが落ちないようにデフォルトブランチ必須

## Event stream — `poll_events` MCP

pane lifecycle (`pane_started` / `pane_exited` / `events_dropped` / `heartbeat` / forward-compat variants) は `mcp__ccmux-peers__poll_events` で cursor-based long-poll する:

```
mcp__ccmux-peers__poll_events(
  since=<前回の next_since、初回は省略>,
  timeout_ms=5000,
  types=["pane_exited", "events_dropped"]
)
```

戻り値の `events[]` は `type` / `role` / `name` / `id` / `ts` を含む。フォアマンは `role == "worker"` で絞り込んで `WORKER_PANE_EXITED` 通知する。`next_since` を次回 `since` に流用して idempotent resume。

### type 別の扱い

| type | 扱い |
|---|---|
| `pane_started` | 現状 skip (将来必要になれば追加) |
| `pane_exited` | `role == "worker"` に絞って `WORKER_PANE_EXITED` 通知 |
| `events_dropped` | `.state/journal.jsonl` に drop 件数を記録 (監視が追いついていないシグナル) |
| `heartbeat` | 通常 `poll_events` のバッファに入らない (subscribe 内部で消化される) |

### `types` フィルタの挙動

`types` filter は cursor を全 type で advance させるので重複 scan なし。ただし **filter 不一致イベント到着で long-poll が early return** し、`events: []` + 進んだ cursor が返る (ccmux PR #120 参照)。Foreman 監視ループでは空応答時に spin せず、`next_since` を保持したまま次のサイクルで再呼び出しする。

### 初回呼び出しのセマンティクス

`since` 省略で「今以降のイベントだけ」を返す（過去の履歴を flood しない）。旧 `ccmux events --timeout` と同じ契約。

## Raw キー入力 — `send_keys` MCP

raw PTY キー送信は `mcp__ccmux-peers__send_keys` を使う。論理メッセージ配送の `send_message` とは**別物**（PTY に生バイトを書き込むので、そのペインで走っているアプリケーション側に見える）:

```
mcp__ccmux-peers__send_keys(
  target: string,           # pane name or id (list_panes と同じ解決規則)
  text?: string,            # 送信するテキスト（optional）
  keys?: string[],          # 特殊キー名の配列（optional、text と併用可、text の後に送られる）
  enter?: boolean           # 末尾に Enter (CR, 0x0D) を付ける（optional、keys の後に送られる）
)
```

### 対応キー語彙

- `Enter` / `Return` (CR, `\r` = 0x0D。`enter: true` と byte-identical)
- `Tab`
- `Shift+Tab` / `BackTab`
- `Esc` / `Escape`
- `Backspace`
- `Delete` / `Del`
- `Up` / `Down` / `Left` / `Right`
- `Home` / `End`
- `PageUp` / `PageDown`
- `Space`
- `Ctrl+<A-Z>`（例: `Ctrl+C`）

未知の key 名は `-32602 invalid-params` error が返る。

### 典型的な呼び出しパターン

| 用途 | 呼び出し |
|---|---|
| 空 Enter（プロンプトへの返答） | `send_keys(target="X", enter=true)` |
| "yes" + Enter（Plan 承認など） | `send_keys(target="X", text="yes", enter=true)` |
| Shift+Tab（permission mode 切替） | `send_keys(target="X", keys=["Shift+Tab"])` |
| Esc（モーダル escape） | `send_keys(target="X", keys=["Esc"])` |
| Ctrl+C（走行中プロセス中断） | `send_keys(target="X", keys=["Ctrl+C"])` |
