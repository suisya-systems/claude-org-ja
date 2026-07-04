#!/usr/bin/env python3
"""secretary 宛 broker メッセージの滞留 watcher（live-tail 版）。

broker transport では過去に「secretary 宛メッセージが claimed/delivered 記録付きで
silent 消失する」障害があった（channel sidecar の二重走行レースが根因。runtime 側の
observer lease で修正済み）。本 watcher はその再発・類似滞留に対する運用ガードとして、
broker セッション中の queue.jsonl を live-tail し、secretary 宛の新規 enqueue が
delivered されないまま閾値秒を超えたら 1 行報告して exit 0 で終了する。

設計上のポイント:
- **live-tail 方式**: 起動時点の queue.jsonl 末尾オフセットを起点に、それ以降の
  新規レコードのみを対象にする。過去ログの通算 gap を数えると、既知の過去消失分が
  混入して誤検知になる（実際に起きた）。
- **起動前 backlog の充当**: 起動時に既存ログを 1 回走査して owner 宛の未配達件数を
  スナップショットし、起動後に観測した配達はまずこの既存 backlog に充当する
  （broker の配達は enqueue 順 = FIFO 前提）。これをしないと、起動前から残っていた
  古いメッセージの drain が新規 pending を相殺し、真の滞留を発報し損ねる。
- **broker run 境界でのリセット**: broker の in-memory queue は再起動をまたいで
  復元されない（journal replay なし）ので、`broker_started` より前の未配達 enqueue は
  もう配達されえない残骸である。pre-scan は最後の `broker_started` 以降だけを数える。
  これをしないと、過去 run の消失分が既存 backlog に紛れ込み、新規メッセージの配達を
  横取りして「配達済みなのに滞留」の誤発報になる。
- **live 中の broker 再起動 = 確定消失として即発報**: 本セッション中に観測した
  owner 宛 pending が未配達のまま `broker_started` を見たら、その pending は
  もう配達されえない（閾値待ちは不要）。即 1 行 print して exit 0 する。
  pending 無しの再起動なら会計（既存 backlog / pending / delivered）をリセットして
  監視を続行する。FIFO リストに消失分を残すと、後続の新規配達が消失分を横取りして
  発報が無期限に遅延しうる。
- **検知したら exit 0 で終了する**: Claude Code の background Bash として起動される
  前提。常駐し続けて print しても窓口には届かないが、プロセス終了イベントで窓口が
  再起床し、出力の 1 行を読んで check_messages で drain できる。
- state dir はハードコードせず `ORG_BROKER_STATE_DIR` 環境変数から解決する
  （queue パスは `$ORG_BROKER_STATE_DIR/queue.jsonl`）。env 未設定なら exit 1
  （broker 専用ツール。renga セッションには queue.jsonl が存在しない）。

想定レコード形（1 行 1 JSON、parse 失敗行は skip）:
    {"ts": ..., "event": "message_enqueued", "from_id": "...", "to_id": "secretary", ...}
    {"ts": ..., "event": "claimed", "owner": "secretary", "ids": [...], ...}
    {"ts": ..., "event": "delivered", "id": "...", "owner": "secretary"}
    {"ts": ..., "event": "queue_drained", "agent_id": "secretary", "count": N}

配達の 2 経路を両方数える: push 一次（channel sidecar の claim → `delivered`）と
pull フォールバック（`check_messages` の drain → `queue_drained` に count=N）。
pull drain を数えないと、正常に drain 済みのメッセージを滞留と誤報する。

依存: Python 標準ライブラリのみ。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="broker queue.jsonl を live-tail し、owner 宛メッセージの滞留を検知したら exit する",
    )
    parser.add_argument(
        "--owner",
        default="secretary",
        help="監視対象の宛先 id（message_enqueued の to_id / delivered の owner。default: %(default)s）",
    )
    parser.add_argument(
        "--stale-sec",
        type=float,
        default=120,
        help="未配達の最古 enqueue がこの秒数を超えたら滞留と判定する（default: %(default)s）",
    )
    parser.add_argument(
        "--poll-sec",
        type=float,
        default=30,
        help="queue.jsonl のポーリング間隔秒（default: %(default)s）",
    )
    return parser.parse_args(argv)


def read_new_chunk(queue: Path, offset: int) -> tuple[str, int]:
    """offset 以降の新規バイトを読み、(テキスト, 新オフセット) を返す。

    truncation / rotation でファイルサイズが offset を下回ったら offset を 0 に
    リセットして先頭から読み直す。ファイル不在は「まだ何も来ていない」として扱う。
    """
    try:
        size = queue.stat().st_size
    except FileNotFoundError:
        return "", 0
    if size < offset:
        offset = 0
    with queue.open(encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        chunk = f.read()
        return chunk, f.tell()


def delivered_units(rec: dict, owner: str) -> int:
    """owner への配達として数えるべき件数を返す。

    push 一次経路の `delivered`（1 件ずつ）と、pull フォールバック経路の
    `queue_drained`（`agent_id` = drain した本人、`count` = まとめて取得した件数）
    の両方を配達扱いにする。該当しないレコードは 0。
    """
    ev = rec.get("event")
    if ev == "delivered" and rec.get("owner") == owner:
        return 1
    if ev == "queue_drained" and rec.get("agent_id") == owner:
        count = rec.get("count", 0)
        try:
            return max(0, int(count))
        except (TypeError, ValueError):
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    state_dir = os.environ.get("ORG_BROKER_STATE_DIR")
    if not state_dir:
        print(
            "[secretary-queue-watcher] ORG_BROKER_STATE_DIR が未設定です。"
            "本ツールは broker 専用（queue パスは $ORG_BROKER_STATE_DIR/queue.jsonl）。"
            "renga セッションでは起動しないでください。",
            file=sys.stderr,
        )
        return 1

    queue = Path(state_dir) / "queue.jsonl"

    # 起動前 backlog のスナップショット: 既存ログを 1 回走査し、owner 宛の
    # 未配達件数（enqueued - delivered、負なら 0）を数える。起動後に観測する
    # delivered は enqueue 順（FIFO）でまずこの既存 backlog に充当し、
    # 本セッション中の新規 pending を相殺させない。
    pre_chunk, offset = read_new_chunk(queue, 0)
    pre_enqueued = 0
    pre_delivered = 0
    for line in pre_chunk.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        ev = rec.get("event")
        if ev == "broker_started":
            # 新しい broker run の開始。in-memory queue は再起動で消えるので、
            # これより前の未配達分は「もう配達されえない過去の残骸」として捨てる
            pre_enqueued = 0
            pre_delivered = 0
        elif ev == "message_enqueued" and rec.get("to_id") == args.owner:
            pre_enqueued += 1
        else:
            pre_delivered += delivered_units(rec, args.owner)
    pre_backlog = max(0, pre_enqueued - pre_delivered)

    pending: list[float] = []  # 新規の owner 宛 enqueue の ts（enqueue 順）
    delivered = 0  # 新規の owner への delivered 件数（既存 backlog 充当後）

    while True:
        time.sleep(args.poll_sec)
        chunk, offset = read_new_chunk(queue, offset)
        for line in chunk.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            ev = rec.get("event")
            if ev == "broker_started":
                # broker が再起動した: in-memory queue は復元されないので、
                # この時点で未配達の pending は確定消失（閾値待ち不要で即発報）。
                lost = len(pending) - delivered
                if lost > 0:
                    print(
                        f"[secretary-queue-watcher] BROKER_RESTART_LOSS: broker 再起動により "
                        f"本セッション中の {args.owner} 宛 {lost} 件が未配達のまま消失。"
                        f"check_messages では回収できない（送信元への再送依頼が必要）。"
                    )
                    return 0
                # pending 無しなら会計をリセットして監視続行（消失分を FIFO に残すと
                # 後続の新規配達が横取りされ、発報が無期限に遅延しうる）
                pending.clear()
                delivered = 0
                pre_backlog = 0
            elif ev == "message_enqueued" and rec.get("to_id") == args.owner:
                ts = rec.get("ts")
                pending.append(ts if isinstance(ts, (int, float)) else time.time())
            else:
                units = delivered_units(rec, args.owner)
                if units:
                    # 起動前から残っていた古い分の drain に先に充当（FIFO 前提）
                    consumed = min(pre_backlog, units)
                    pre_backlog -= consumed
                    delivered += units - consumed
        backlog = len(pending) - delivered
        if backlog > 0:
            oldest_age = time.time() - pending[delivered]
            if oldest_age > args.stale_sec:
                print(
                    f"[secretary-queue-watcher] STAGNATION: 本セッション中の {args.owner} 宛 "
                    f"{backlog} 件が {int(oldest_age)}s 未配達。check_messages で drain 要。"
                )
                return 0


if __name__ == "__main__":
    sys.exit(main())
