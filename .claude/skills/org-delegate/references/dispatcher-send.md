# 窓口 → ディスパッチャーの送信（channel と user_turn の使い分け）

Claude Code 2.1.325 以降、channel で届いたメッセージには「ユーザー発ではない外部入力」の注記が付き、ディスパッチャーは spawn・close・打鍵を伴う指示を保留する（2026-11-16 / 11-17 に channel の DELEGATE が保留され、user_turn の 1 行で即着手した）。操作を伴う指示は broker では最初から `deliver="user_turn"` で送る。

## 送信の分類（正本）

窓口（と窓口が叩く tool）からディスパッチャーへ届くものを、受け手が**何をするか**で 3 つに分ける。判定軸は「ディスパッチャーに spawn / close / 打鍵をさせるか」と「受け手が `check_messages` で読む前提か」。

| 種別 | 送るもの | 送り方（broker） | renga |
|---|---|---|---|
| A. 操作系（spawn / close / 打鍵をさせる） | `DELEGATE`、`CLOSE_PANE: {pane_id}`（2b-ii のクローズも `/org-resume` の再送も）、`DISPATCHER_HANDOVER` | `mcp__org-broker__send_message(to_id="dispatcher", message=..., deliver="user_turn")` | 従来どおり `mcp__renga-peers__send_message`（channel）。renga の `peer_user_turn` の利用は別件で、本書は扱わない |
| B. 返事・記帳だけ（受け手が `check_messages` で読む） | `WORKER_COMPLETION_NOTED`、`WORKER_REOPENED`、retro gate ack | **channel のまま**（`deliver` を付けない） | 同左 |
| C. 役割の宣言（操作を伴わない） | `/org-start` 直後の挨拶（`org-start` Block D 手順 3） | channel のまま | 同左 |

- **B を `user_turn` にしてはいけない**: `user_turn` は本文を入力欄へ打ち込む経路でキューに積まれない。ディスパッチャーが `check_messages` で読む B は、`user_turn` にすると読まれず機能が止まる。retro gate ack は `tools/dispatcher_retro_gate.py` が `check_messages` で検出する（[`CLAUDE.md`](../../../../CLAUDE.md) の「retro gate ack の宛先」）。`WORKER_COMPLETION_NOTED` / `WORKER_REOPENED` は `/loop 3m` の通常の `check_messages` で拾われ、取りこぼしても DB の `runs.status` が補う（[`worker-monitoring.md`](../../../../.dispatcher/references/worker-monitoring.md) の監視フラグ節）。
- **C は channel のまま**: 本文は役割の説明だけで spawn / close / 打鍵を求めない。かつ `[peer_not_found]` を 2 秒間隔で再送することが「ディスパッチャーの peer 登録が済んだ」ことの判定を兼ねる（`org-start` Block D）。`user_turn` にすると起動直後の `[user_turn_not_ready]` が登録待ちの再送と混ざる。
- **`/loop` の再点火と `/clear` / `/dispatcher-resume`** はメッセージではなく `send_keys` の打鍵で、もとから user turn として入る。`send_message` で代替しない（[`CLAUDE.md`](../../../../CLAUDE.md) の打鍵規律）。
- **対象外**: ディスパッチャー → 窓口（`PANE_CLOSED`、`CODEX_ROUND_DENIED` 等）。キュレーター → ディスパッチャー（`CURATE_*`）は `check_messages` で読まれるので channel が正しい。
- **tool の自動送信**: ディスパッチャーに操作をさせるのは `gen_delegate_payload.py apply` が書く `send_plan.json`（DELEGATE）だけで、broker では `deliver: "user_turn"` 入り（次の DELEGATE 節）。`tools/peer_notify.py` 系の通知は窓口宛でディスパッチャーへは送らない。
- `DISPATCHER_HANDOVER` の後の `/clear` と `/dispatcher-resume` は従来どおり `send_keys` の 2 段（[`CLAUDE.md`](../../../../CLAUDE.md) の打鍵規律）。

## DELEGATE

`gen_delegate_payload.py apply` が書く `send_plan.json` は、broker のとき `deliver: "user_turn"` を含む。`to_id` / `message` / `deliver` をそのまま `send_message` に渡す（`summary` は渡さない）。

- 本文は UTF-8 で 4096 バイトまで（runtime の `MAX_BODY_BYTES`）。`apply` は超過を `ValueError` で止める。説明・受け入れ文・指示を短くして再実行する。
- 「モデル: opus」などを本文に手で足したときは、足した後の長さが 4096 バイト以内かを窓口が確かめる。

## 拒否されたとき

`send_message` は拒否も正常応答の JSON（`ok:false` と `[コード]`）で返すので、応答の中身を必ず読む。

| 応答 | 対処 |
|---|---|
| `[user_turn_busy]`（ディスパッチャーがターン中） | 10 秒おきに最大 3 回再送する |
| `[user_turn_not_ready]`（ダイアログ・下書き・スクロール中） | `inspect_pane` で原因を見る。人間の操作が要るなら人間へ。自動で打鍵しない |
| `[user_turn_stalled]`（本文は打ったが提出を確認できない） | 盲目再送しない。`inspect_pane` で入力欄を見て、自分の本文が残っていれば Enter だけ別送する。判断できなければ人間へ |
| `duplicate_suppressed` | 新しい送達は起きていない。成功と扱わず、`inspect_pane` で提出済みか下書き残りかを確かめる |
| `[user_turn_invalid_body]` / `[user_turn_unsupported_target]` / `[peer_not_found]` | 再送しても同じ。人間へ報告する |

3 回で通らないとき、および上の「人間へ」は、窓口が判断を仰ぐ（`/org-escalation`）。操作系を channel へ落として再送しない（保留されるため）。
