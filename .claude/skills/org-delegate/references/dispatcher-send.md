# 窓口 → ディスパッチャーの送信（channel と user_turn の使い分け）

Claude Code 2.1.325 以降、channel で届いたメッセージには「ユーザー発ではない外部入力」の注記が付き、ディスパッチャーは spawn・close・打鍵を伴う指示を保留する（2026-11-16 / 11-17 に channel の DELEGATE が保留され、user_turn の 1 行で即着手した）。操作を伴う指示は broker では最初から `deliver="user_turn"` で送る。

## 送信の分類

| 種別 | 例 | broker | renga |
|---|---|---|---|
| 操作系（ディスパッチャーに spawn / close / 打鍵をさせる） | DELEGATE、`CLOSE_PANE:`、`DISPATCHER_HANDOVER`、派遣承認 | `mcp__org-broker__send_message(to_id="dispatcher", message=..., deliver="user_turn")` | 従来どおり `mcp__renga-peers__send_message`（channel）。renga の `peer_user_turn` の利用は別件で、本書は扱わない |
| 返事・記帳だけ | `WORKER_COMPLETION_NOTED`、`WORKER_REOPENED` | channel のまま（`deliver` を付けない） | 同左 |

- 返事・記帳だけの 2 種は、取りこぼしても DB の `runs.status` が補うので channel で足りる（[`worker-monitoring.md`](../../../../.dispatcher/references/worker-monitoring.md) の監視フラグ節）。
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
