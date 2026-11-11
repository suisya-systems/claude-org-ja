# CI 監視の終端信号を多層で受ける理由（CI-watch zero-miss）

`/org-pull-request` は CI 完了・マージ検出・24h タイムアウトの終端信号（`CI_COMPLETED` / `PR_MERGED` / `PR_MERGE_WATCH_TIMEOUT` / `PR_MERGED_NO_RUN` / `PR_MERGED_HEAD_UNCONFIRMED` / `PR_WATCH_ABORTED`）を、`events` テーブルの canonical event を正本として次の 2 層で受ける（Refs #653 #658）。受け方の手順はスキル側にあり、この文書は構成の理由だけを書く。運用上の規律の理由は [`docs/operations/pr-ci-watch.md`](../operations/pr-ci-watch.md)。

- **(A) pr-watch からの直 push**: 遅延は小さいが best-effort。
- **(B) ディスパッチャーの relay**: `/loop 3m` の relay scan が `events` を直接読み、`event_deliveries` 台帳と突き合わせて未配送分を窓口へ送る。見逃しゼロの主保証。

## (B) を足した理由

(A) だけだった頃、汎用 spawn ペインでは env が欠けて直 push が黙って no-op になり、broker queue に `CI_COMPLETED` が 1 件も入らず、窓口が idle のまま CI green を取りこぼした（PR #73）。(B) は `events` を直接読むので、(A) が落ちても届く。

## 両層を残す理由と、2 通届くのが正常である理由

2026-07-30〜08-19 には relay 層が死んで (A) だけが動く期間があり、その直後には (A) が失敗して (B) だけが動く期間があった。片方だけが生きている期間が繰り返し起きたので、どちらも冗長層として残す。台帳 `event_deliveries` が記録するのは (B) だけで、(A) は台帳に触らない（`tools/pr_watch.py` は push に成功するとすぐ return する）。したがって直 push が成功しても relay は抑止されず、両層が健全な通常運転では同じ終端信号が 2 通届く（Issue #954）。2 通目を障害と読んで片方を止めてはいけない。

## 直 push の transport 解決

`tools/peer_notify.py` は raw env 判定をやめて `tools/transport.py` の `resolve()` 経由で transport を決める（Refs #941）。以前は `ORG_TRANSPORT` 無設定を renga と読んでいたが、無設定のペインは実際には broker に解決されるので、実際の解決先と違う宛先へ push していた。これも CI green 取りこぼしの一因だった。
