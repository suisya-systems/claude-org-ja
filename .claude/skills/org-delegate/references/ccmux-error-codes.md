# ccmux error codes — Foreman / Secretary reference (MCP + CLI fallback)

ccmux 0.5.7+ および ccmux-peers MCP 0.12.0+ は、エラー応答に安定した
machine-readable な code を載せる。フォアマン / キュレーター / 窓口は
message 文字列の substring match ではなく **code で分岐する**のを推奨する。

伝達経路は呼び出し手段で 2 通り:

| 呼び出し手段 | エラー表現 | 抽出方法 |
|---|---|---|
| MCP (`mcp__ccmux-peers__*`) | JSON-RPC error の result テキストに `[<code>] <message>` を埋め込み | tool result text を正規表現 / substring で `[<code>]` 抽出 |
| CLI (`ccmux send / inspect / events`, upstream #116/#117/#118 merge まで併用) | stderr に `Error: [<code>] <message>` 形式、exit status 非ゼロ | `grep '\[<code>\]'` or case 分岐 |

code 語彙は両経路共通なので、抽出後のハンドリング分岐は同じテーブルを使える。

## Wire format

### MCP (`mcp__ccmux-peers__*`) 失敗例

```
mcp__ccmux-peers__send_message(to_id="worker-nonexistent", message="hi")
→ ccmux refused send: [pane_not_found] pane not found: Name("worker-nonexistent")
```

MCP ツールの result テキスト（JSON-RPC error の human-readable message）の先頭に
`[<code>] <human message>` が埋まる。ccmux 側の `fmt_code` 関数がこの形式を保証。

### CLI 失敗例（併用中の経路）

```
$ ccmux send --name worker-nonexistent hi
Error: [pane_not_found] pane not found: Name("worker-nonexistent")
```

stderr に上記 1 行、exit status 非ゼロ。

## Known codes

| Code | 意味 | Foreman の推奨挙動 |
|---|---|---|
| `pane_not_found` | 指定した pane 名 / id / Focused が存在しない | そのワーカーは既に閉じた扱い。`.state/workers/worker-*.md` の status を `pane_closed` に遷移、`WORKER_PANE_EXITED` を窓口に通知。リトライしない。**注意**: ccmux の `list_panes` / `focus_pane` / `send_message` / `inspect`（CLI） は現在フォーカス中のタブのペインしか見えない。別タブ (`new_tab` 由来) のワーカーは本 code で返るので、org-delegate では全ワーカーを同一タブ内 `spawn_pane` で起動する (happy-ryo/ccmux#71) |
| `pane_vanished` | resolve 成功後に消えたレース | `pane_not_found` と同等扱い |
| `last_pane` | `close_pane` / `ccmux close` で唯一のタブの唯一のペインを閉じようとした | 通常のワーカー停止では発生しない (窓口/フォアマン/キュレーターが同タブに同居するため)。`org-suspend` 末端で残った最後のペイン (通常は窓口) に対して発生した場合、そのペインは自分自身で `exit` して自然終了させる。強制再試行はしない |
| `split_refused` | `spawn_pane` / `ccmux split` が MAX_PANES / too small で拒否 | ワーカー起動 (`org-delegate` Step 3) で balanced split のいずれかのステップが 16 ペイン上限 / `MIN_PANE_WIDTH` / `MIN_PANE_HEIGHT` で拒否された場合、キュレーター → 窓口に escalate。典型シナリオは (a) 9 並列以上に到達、(b) ターミナル幅が balanced split の要件 (W ≥ 160) を満たさない、(c) ワーカー退役後の再派遣でレイアウト tree が想定と乖離。`new_tab` フォールバックは tab-scoped 制約のため不可 (happy-ryo/ccmux#71) |
| `io_error` | PTY write / spawn / OS レベル失敗 | 1 サイクル spin して再試行。2 連続で同じ worker に出たら窓口に `IO_ERROR_DETECTED` で escalate |
| `shutting_down` | ccmux 本体がシャットダウン中 | 監視ループを **即停止** する。claude-peers に `FOREMAN_STOPPING` を通知 |
| `app_timeout` | ccmux 内部 App スレッドが応答しなかった | 1 サイクル spin (ccmux 再起動は管理者判断)。連続発生なら窓口にログ |
| `parse` / `protocol` | 通常出ない (MCP / CLI が正しく組み立てる前提) | 発生時はバグ。journal に記録して窓口に `IPC_PROTOCOL_ERROR` で報告 |
| `internal` | ccmux 内部不変条件違反 (parser lock poison 等) | `app_timeout` と同じ扱い |

## MCP ツール特有の ok-return ルール

以下 2 つの MCP ツールは、ccmux 到達不可でも **JSON-RPC error にせず ok-text で返す** 例外扱い。

- `mcp__ccmux-peers__list_peers`: ccmux 本体未起動 / detached mode → `"(no peers — ccmux not reachable: <reason>)"`
- `mcp__ccmux-peers__send_message`: 同上 → `"(message dropped — ccmux not reachable: <reason>)"`

他の ccmux-peers ツール (`spawn_pane` / `close_pane` / `list_panes` / `focus_pane` / `new_tab` /
`check_messages` / `set_summary` / `poll_events` / `inspect_pane`) は `require_connected` で
非接続時に JSON-RPC error になる。この 2 つだけは**ハンドリング分岐を `[code]` パターンだけでなく
`(no peers` / `(message dropped` 接頭辞**でも見るべき。

## シェル側のハンドリング例

### MCP ツール結果のパターンマッチ

Claude Code 内で MCP ツール呼び出しを行い、結果テキストを受け取ったあと。典型的には tool result
の `content[0].text` または JSON-RPC error message:

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

### CLI 経路のハンドリング例（upstream merge まで使う）

```bash
out=$(ccmux send --name worker-foo --enter "ping" 2>&1)
status=$?
if [ $status -ne 0 ]; then
  case "$out" in
    *"[pane_not_found]"*|*"[pane_vanished]"*) mark_worker_pane_closed worker-foo ;;
    *"[last_pane]"*) echo "last pane — leave for self-exit" ;;
    *"[shutting_down]"*) echo "ccmux halting — foreman stopping"; exit 0 ;;
    *"[io_error]"*|*"[app_timeout]"*|*"[internal]"*) log_journal "transient ccmux error: $out" ;;
    *) log_journal "unexpected ccmux error: $out" ;;
  esac
fi
```

MCP / CLI で分岐テーブルは実質同一。違いは入力の取り方のみ。

## なぜ code か、substring ではなく

- メッセージ本文は human-facing。理由なしで変更される可能性がある
  (e.g. "pane not found: Id(3)" → "pane 3 does not exist")
- ccmux 側の契約については、以下を正本として参照する (このリポジトリ
  内では検証不能な前提なので **外部依存** として扱うこと):
  - `ccmux/src/ipc/mod.rs::err_code` の doc コメント — 公開 code の一覧
    と ABI 安定性 (rename は deprecation window 付き) の明文
  - `ccmux/src/mcp_peer/mod.rs::fmt_code` — MCP 経由の `[<code>] <message>` 成形ロジック
  - ccmux `Response::Err { message, code }` の wire schema — `code` は
    `Option<String>` で、`skip_serializing_if = "Option::is_none"`
- 未知の code は必ず非致命扱いにする — 将来 ccmux が新 code を追加しても
  フォアマンが落ちないようにデフォルトブランチ必須

## 後方互換

MCP 経路 (`ccmux-peers` 0.12.0+) では常に `[<code>] <message>` が取れる前提で良い。
CLI 経路の pre-0.5.7 後方互換は以下の通り:

- **想定**: pre-0.5.7 の ccmux では wire Response に `code` フィールドが載らず、CLI 側も
  `[<code>]` prefix なしでメッセージだけを stderr に吐く。この想定はこのリポジトリでは検証
  できないので ccmux 本体のリリースノート (v0.5.7) で裏取りしてから運用する
- code 無しで受けた場合は従来 substring match にフォールバック。aainc-ops 側で code を
  扱う新しいコードは **両方** をサポートすべき (最低でも unknown code を無視しないロジック)

## Event stream 側（CLI 併用、upstream #117 merge まで）

MCP に `poll_events` tool が追加された (upstream happy-ryo/ccmux#117 / PR #120)。
以降のスキル改修で `ccmux events` CLI から `mcp__ccmux-peers__poll_events` に切替予定
（下流 Issue #24 / #25 で対応）。cleanup までは CLI 併用経路で扱う:

`ccmux events --timeout 5s` が返す JSON 行のうち、フォアマンが扱う `type`:

| type | 扱い |
|---|---|
| `pane_started` | 現状 skip (将来必要になれば追加) |
| `pane_exited` | `role == "worker"` に絞って `WORKER_PANE_EXITED` 通知 |
| `events_dropped` | `.state/journal.jsonl` に drop 件数を記録 |
| `heartbeat` | skip (30 秒おきの keep-alive。ccmux 0.5.7+ が emit) |

`jq -c 'select(.type == "pane_exited" and .role == "worker")'` は
heartbeat / pane_started / events_dropped を暗黙に落とすので、
**既存のフィルタ式は 0.5.7 以降も無修正で動く**。ただし
`events_dropped` を journal に記録したいなら select 式を拡張する:

```bash
ccmux events --timeout 5s \
  | jq -c 'select(
      (.type == "pane_exited" and .role == "worker")
      or .type == "events_dropped"
    )'
```

### MCP 化後の等価ハンドリング（参考、#24 / #25 で実装）

`poll_events` の types フィルタで同じ選別ができる:

```
mcp__ccmux-peers__poll_events(
  since=<cursor>,
  timeout_ms=5000,
  types=["pane_exited", "events_dropped"]
)
```

戻り値の `events[]` は role フィールドを含むので、さらに `role == "worker"` で絞る。
`next_since` は次回呼び出しの `since` に流用する（idempotent resume）。

注: `poll_events` は **filter 不一致イベントが到着しても long-poll を中断して empty 返却する**
挙動（ccmux PR #120 のドキュメント参照）。Foreman 監視ループでは空応答時に spin せず、
`next_since` を保持したまま次のサイクルで再呼び出しする。
