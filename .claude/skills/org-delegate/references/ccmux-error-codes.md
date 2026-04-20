# ccmux IPC error codes — Foreman / Secretary reference

ccmux 0.5.7+ は IPC エラー応答に `[code] human message` 形式で安定した
machine-readable コードを載せる。フォアマン / キュレーター / 窓口は
message の substring match ではなく **code で分岐する**のを推奨する。

Wire format (実挙動):

```
$ ccmux send --name worker-nonexistent hi
Error: [pane_not_found] pane not found: Name("worker-nonexistent")
```

stderr に上記 1 行、exit status は非ゼロ。

## Known codes

| Code | 意味 | Foreman の推奨挙動 |
|---|---|---|
| `pane_not_found` | 指定した pane 名 / id / Focused が存在しない | そのワーカーは既に閉じた扱い。`.state/workers/worker-*.md` の status を `pane_closed` に遷移、`WORKER_PANE_EXITED` を窓口に通知。リトライしない。**注意**: ccmux の `list` / `focus` / `send` / `inspect` は現在フォーカス中のタブのペインしか見えない。別タブ (`ccmux new-tab` 由来) のワーカーは本 code で返るので、org-delegate では全ワーカーを同一タブ内 `ccmux split` で起動する (happy-ryo/ccmux#71) |
| `pane_vanished` | resolve 成功後に消えたレース | `pane_not_found` と同等扱い |
| `last_pane` | `ccmux close` で唯一のタブの唯一のペインを閉じようとした | 通常のワーカー停止では発生しない (窓口/フォアマン/キュレーターが同タブに同居するため)。`org-suspend` 末端で残った最後のペイン (通常は窓口) に対して発生した場合、そのペインは自分自身で `exit` して自然終了させる。強制再試行はしない |
| `split_refused` | `ccmux split` が MAX_PANES / too small で拒否 | ワーカー起動 (`org-delegate` Step 3) で `--target-name foreman` への split が 16 ペイン上限等で拒否された場合、キュレーターに escalate。`new-tab` フォールバックは tab-scoped 制約のため不可 (happy-ryo/ccmux#71) |
| `io_error` | PTY write / spawn / OS レベル失敗 | 1 サイクル spin して再試行。2 連続で同じ worker に出たら窓口に `IO_ERROR_DETECTED` で escalate |
| `shutting_down` | ccmux 本体がシャットダウン中 | 監視ループを **即停止** する。claude-peers に `FOREMAN_STOPPING` を通知 |
| `app_timeout` | ccmux 内部 App スレッドが応答しなかった | 1 サイクル spin (ccmux 再起動は管理者判断)。連続発生なら窓口にログ |
| `parse` / `protocol` | 通常出ない (ccmux CLI が正しく組み立てる前提) | 発生時はバグ。stderr を journal に記録して窓口に `IPC_PROTOCOL_ERROR` で報告 |
| `internal` | ccmux 内部不変条件違反 (parser lock poison 等) | `app_timeout` と同じ扱い |

## シェル側のハンドリング例

```bash
out=$(ccmux send --name worker-foo --enter "ping" 2>&1)
status=$?
if [ $status -ne 0 ]; then
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
    *)
      log_journal "unexpected ccmux error: $out"
      ;;
  esac
fi
```

## なぜ code か、substring ではなく

- メッセージ本文は human-facing。リワードなしで変更される可能性がある
  (e.g. "pane not found: Id(3)" → "pane 3 does not exist")
- ccmux 側の契約については、以下を正本として参照する (このリポジトリ
  内では検証不能な前提なので **外部依存** として扱うこと):
  - `ccmux/src/ipc/mod.rs::err_code` の doc コメント — 公開 code の一覧
    と ABI 安定性 (rename は deprecation window 付き) の明文
  - ccmux `Response::Err { message, code }` の wire schema — `code`
    は `Option<String>` で、`skip_serializing_if = "Option::is_none"`
- 未知の code は必ず非致命扱いにする — 将来 ccmux が新 code を追加しても
  フォアマンが落ちないようにデフォルトブランチ必須

## 後方互換

- **想定**: pre-0.5.7 の ccmux では wire Response に `code` フィールド
  が載らず、CLI 側も `[code]` prefix なしでメッセージだけを stderr に
  吐く。この想定はこのリポジトリでは検証できないので ccmux 本体の
  リリースノート (v0.5.7) で裏取りしてから運用する。
- code 無しで受けた場合は従来 substring match にフォールバック。
  aainc-ops 側で code を扱う新しいコードは **両方** をサポートすべき
  (最低でも unknown code を無視しないロジック)。

## Event stream 側

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
