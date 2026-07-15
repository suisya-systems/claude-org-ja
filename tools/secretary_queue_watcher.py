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
  （broker の drain 対象行は enqueue 順 = FIFO 前提）。これをしないと、起動前から
  残っていた古いメッセージの drain が新規 pending を相殺し、真の滞留を発報し損ねる。
- **claim 済み in-flight の id 追跡**: 起動時点で sidecar に CLAIMED（lease 中）の
  旧行は drain（`check_messages`）にスキップされるため、単純な FIFO 充当が崩れる。
  `claimed` イベントの `ids` で起動前 claim 済み・未配達の id を集合追跡し、
  id 一致の `delivered` は旧 in-flight の完了として新規会計に触れない。
  `queue_drained` は claim 済み行をスキップする仕様なので、旧 backlog のうち
  unclaimed 分のみに充当する。`lease_reaped` で reclaim された id は unclaimed 側へ
  戻す。これでも live claim の対象行までは journal から判別できず微小な近似は残る
  （検知は閾値ベースの運用トリップワイヤであり、厳密会計を要求しない）。
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

PID file（`--stop` による識別付き停止のための小改修）:
- watch モード起動時に `.state/secretary_queue_watcher.json` へ自分の識別情報
  （pid / cwd / cmdline / started_at / broker_state_dir / owner）を記録し、
  検知 exit / SIGTERM 等の graceful 終了で自削除する。
- `--stop` モードはこの PID file を読み、(a) ownership 照合（記録された
  broker_state_dir が現在の `ORG_BROKER_STATE_DIR` と一致するか。別 org / 別 broker の
  watcher を誤停止しないためのガード）と (b) identity 照合（記録 pid が生存し、かつ
  `/proc/<pid>/cmdline` に `secretary_queue_watcher.py` を含むか。pid recycle された
  無関係プロセスを掴まないためのガード）の両方が通ったときだけ SIGTERM を送る。
  どちらかが外れたら **kill せず** PID file を stale として削除する（誤 kill 防止を
  最優先し、迷ったら殺さない側へ倒す）。identity 照合の argv 取得は Linux / WSL では
  `/proc/<pid>/cmdline`、macOS / BSD では `ps -p <pid> -o args=` フォールバックで
  行う。`/proc` も `ps` も無い環境（Windows native）では argv を確認できず「未確認」
  (exit 2) を返す（Windows は org-suspend / org-down の PowerShell 手順で
  `Get-CimInstance Win32_Process` の CommandLine 照合を使う）。

依存: Python 標準ライブラリのみ。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# .state/ 直下に置く補助プロセス追跡 sidecar（dashboard.pid / attention_pane.json と
# 同じ「.state 相対」パターン）。broker の queue.jsonl が住む $ORG_BROKER_STATE_DIR とは
# 別物であることに注意（PID file は cwd 相対の .state/、queue は $ORG_BROKER_STATE_DIR）。
DEFAULT_PID_FILE = ".state/secretary_queue_watcher.json"

# /proc/<pid>/cmdline に本 watcher プロセスが含むはずの識別子（recycle された無関係
# プロセスを掴んで誤 kill しないための identity 照合キー）。
SCRIPT_IDENT = "secretary_queue_watcher.py"


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
    parser.add_argument(
        "--pid-file",
        default=DEFAULT_PID_FILE,
        help="watch 中に識別情報を記録する sidecar path（cwd 相対。default: %(default)s）",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help=(
            "watch せず、--pid-file を読み ownership+identity 照合の上で "
            "watcher を停止する（照合失敗時は kill せず stale sidecar を削除）"
        ),
    )
    return parser.parse_args(argv)


def read_new_chunk(queue: Path, offset: int) -> tuple[str, int]:
    """offset 以降の完結行（末尾が改行の行）だけを読み、(テキスト, 新オフセット) を返す。

    - broker が追記中の未完行（末尾に改行が無い断片）は消費せず、オフセットも
      進めない（次回 poll で完結してから読む）。断片を parse-skip して offset を
      進めると、そのレコードを恒久的に取りこぼす。
    - truncation / rotation でファイルサイズが offset を下回ったら offset を 0 に
      リセットして先頭から読み直す。ファイル不在は「まだ何も来ていない」として扱う。
    - バイト単位で読み、オフセットもバイト位置で管理する（text モードの tell() に
      依存しない）。
    """
    try:
        size = queue.stat().st_size
    except FileNotFoundError:
        return "", 0
    if size < offset:
        offset = 0
    with queue.open("rb") as f:
        f.seek(offset)
        data = f.read()
    nl = data.rfind(b"\n")
    if nl < 0:
        return "", offset  # 完結行なし（未完断片のみ）: 持ち越し
    complete = data[: nl + 1]
    return complete.decode("utf-8", errors="replace"), offset + len(complete)


def drained_count(rec: dict) -> int:
    """`queue_drained` レコードの count を非負 int で返す（不正値は 0）。"""
    count = rec.get("count", 0)
    try:
        return max(0, int(count))
    except (TypeError, ValueError):
        return 0


def _real(path: str | os.PathLike[str]) -> str:
    """絶対・シンボリックリンク解決したパス文字列。解決不能でも原文字列を返す。"""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_pid_file(pid_file: Path, state_dir: str, args: argparse.Namespace) -> None:
    """watch 起動時に識別情報を sidecar へ atomic 書き込みする。

    記録項目は pid 単独でなく cwd / cmdline / started_at / broker_state_dir も含める
    （--stop / PowerShell 側が pid recycle・別 org を識別して誤 kill を避けるため）。
    """
    record = {
        "pid": os.getpid(),
        "cwd": _real(Path.cwd()),
        "cmdline": list(sys.argv),
        "script": _real(Path(__file__)),
        "started_at": _now_iso(),
        "broker_state_dir": _real(state_dir),
        "owner": args.owner,
        "stale_sec": args.stale_sec,
        "poll_sec": args.poll_sec,
    }
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = pid_file.with_name(pid_file.name + ".tmp")
    tmp.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(pid_file)  # 同一 dir 内 rename は atomic（部分書き込みを晒さない）


def remove_pid_file(pid_file: Path, expected_pid: int | None = None) -> None:
    """graceful 終了時の sidecar 掃除。

    expected_pid 指定時は、いま sidecar が指す pid が自分と一致するときだけ削除する
    （二重起動で新しい watcher が sidecar を上書きした後に、古い watcher の finally が
    新しい方の sidecar を消してしまう事故を防ぐ）。
    """
    try:
        if expected_pid is not None:
            rec = json.loads(pid_file.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or rec.get("pid") != expected_pid:
                return
        pid_file.unlink()
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError):
        pass


def _pid_alive(pid: int) -> bool:
    """signal 0 でプロセスの生存を確認する（POSIX）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在するが別ユーザー所有
    except OSError:
        return False
    return True


def _proc_cmdline(pid: int) -> list[str] | None:
    """`/proc/<pid>/cmdline` から live な argv を返す。読めない環境は None。

    None は「/proc からは argv を確認できない」（macOS / BSD / Windows など /proc
    非搭載環境、または読取不可）。空 argv（kernel thread 等）も None 扱い。呼び出し側
    (:func:`_live_cmdline`) が None のとき `ps` フォールバックへ進む。
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, OSError):
        return None
    if not raw:
        return None
    return [p for p in raw.decode("utf-8", errors="replace").split("\0") if p]


def _ps_cmdline(pid: int) -> list[str] | None:
    """`ps -p <pid> -o args=` で live な command line を返す（POSIX フォールバック）。

    /proc の無い POSIX（macOS / BSD）で identity 照合を成立させるための経路。
    `ps` が無い / 失敗 / 空出力なら None（Windows native はここも None になり、
    呼び出し側は identity 未確認として PowerShell 手順へ倒す）。
    """
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    line = proc.stdout.strip()
    if not line:
        return None
    return line.split()


def _live_cmdline(pid: int) -> list[str] | None:
    """live プロセスの argv を返す。/proc 優先、無ければ `ps` フォールバック。

    Linux / WSL は `/proc/<pid>/cmdline`、macOS / BSD は `ps` で解決する。どちらも
    使えない環境（Windows native）は None を返し、identity 未確認として扱わせる。
    """
    cmd = _proc_cmdline(pid)
    if cmd is not None:
        return cmd
    return _ps_cmdline(pid)


def _ownership_ok(record: dict, env_state_dir: str | None) -> tuple[bool, str]:
    """記録された watcher が「いま停止してよい対象（同一 org/broker）」か判定する。

    ORG_BROKER_STATE_DIR が設定されていれば broker_state_dir の一致を要求する
    （別 org / 別 broker の watcher 誤停止防止＝設計制約 #2）。env 未設定時は
    フォールバックとして記録 cwd と現在 cwd の一致（同一 checkout）で代替する。
    """
    rec_bsd = record.get("broker_state_dir")
    if env_state_dir:
        if isinstance(rec_bsd, str) and _real(rec_bsd) == _real(env_state_dir):
            return True, "broker_state_dir_match"
        return False, "broker_state_dir_mismatch"
    rec_cwd = record.get("cwd")
    if isinstance(rec_cwd, str) and _real(rec_cwd) == _real(Path.cwd()):
        return True, "cwd_match_env_unset"
    return False, "ownership_unconfirmed_env_unset"


def _identity_ok(pid: int, record: dict) -> tuple[bool, str]:
    """記録 pid が「いまも本 watcher」か identity 照合する。

    生存 + live argv（/proc → ps フォールバック）に SCRIPT_IDENT 包含で verified。
    argv を読めない環境（/proc も ps も無い Windows native）は "cmdline_unreadable"
    （未確認）を返し、caller は誤 kill を避けて kill しない側へ倒す。
    """
    if not _pid_alive(pid):
        return False, "pid_not_alive"
    cmd = _live_cmdline(pid)
    if cmd is None:
        return False, "cmdline_unreadable"
    if any(SCRIPT_IDENT in part for part in cmd):
        return True, "verified"
    return False, "cmdline_mismatch"


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    """SIGTERM 後、プロセス消滅を最大 timeout 秒待つ。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


# 兄弟 teardown CLI（tools/stop_dashboard.py）が同じ「live プロセス identity 照合で
# 誤 kill を避ける」プリミティブを再利用するための public 別名（underscore シンボルを
# import させないため）。実体は上の identity 照合ヘルパ。
pid_alive = _pid_alive
live_cmdline = _live_cmdline
wait_gone = _wait_gone


def run_stop(pid_file: Path) -> int:
    """PID file を ownership+identity 照合して watcher を停止する（誤 kill 防止）。

    返り値: 0 = 停止 / 既に停止 / stale 掃除（正常系）、1 = kill 権限不足、
    2 = identity 未確認で kill せず保留（非 Linux。Windows は PowerShell 手順へ）。
    """
    if not pid_file.exists():
        print(
            f"[secretary-queue-watcher] STOP: PID file {pid_file} が無い"
            "（既に停止済み / 未起動）。no-op。"
        )
        return 0
    try:
        record = json.loads(pid_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[secretary-queue-watcher] STOP: PID file を parse できない（{exc}）。"
            "stale として削除。"
        )
        remove_pid_file(pid_file)
        return 0
    pid = record.get("pid") if isinstance(record, dict) else None
    if not isinstance(pid, int):
        print(
            "[secretary-queue-watcher] STOP: PID file の pid フィールドが不正。"
            "stale として削除。"
        )
        remove_pid_file(pid_file)
        return 0

    env_state_dir = os.environ.get("ORG_BROKER_STATE_DIR")
    own_ok, own_reason = _ownership_ok(record, env_state_dir)
    if not own_ok:
        print(
            f"[secretary-queue-watcher] STOP: ownership 不一致（{own_reason}）。"
            f"別 org / 別 broker の watcher 誤停止を避けるため pid={pid} を kill せず、"
            "stale sidecar のみ削除。"
        )
        remove_pid_file(pid_file)
        return 0

    id_ok, id_reason = _identity_ok(pid, record)
    if not id_ok:
        if id_reason == "cmdline_unreadable":
            print(
                f"[secretary-queue-watcher] STOP: identity 未確認（{id_reason}）。"
                "この環境では argv を確認できない（/proc も ps も無い = Windows native）。"
                f"誤 kill を避けるため pid={pid} は kill せず sidecar も残す。"
                "Windows native は PowerShell 手順（Get-CimInstance の CommandLine 照合）"
                "で停止すること。"
            )
            return 2
        print(
            f"[secretary-queue-watcher] STOP: identity 不一致（{id_reason}）。"
            f"pid={pid} は既に消滅 / recycle で別プロセスに再利用済み。"
            "誤 kill を避けるため kill せず stale sidecar を削除。"
        )
        remove_pid_file(pid_file)
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(
            f"[secretary-queue-watcher] STOP: pid={pid} は kill 直前に消滅。"
            "sidecar を削除。"
        )
        remove_pid_file(pid_file)
        return 0
    except PermissionError:
        print(
            f"[secretary-queue-watcher] STOP: pid={pid} を kill する権限が無い。"
            "手動確認が必要（sidecar は残す）。",
            file=sys.stderr,
        )
        return 1
    _wait_gone(pid)
    # expected_pid=pid で「いま sidecar が指すのがこの pid のとき」だけ削除する。
    # kill 後〜削除の窓で新 watcher が別 pid の sidecar を書いていたら、それは
    # 消さない（watcher の finally と同じ clobber ガード）。
    remove_pid_file(pid_file, expected_pid=pid)
    print(
        f"[secretary-queue-watcher] STOP: pid={pid} に SIGTERM を送信し停止しました。"
        "sidecar を削除。"
    )
    return 0


def _install_sigterm_handler() -> None:
    """SIGTERM で SystemExit を投げ、watch の finally（sidecar 掃除）を通す。"""

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass  # メインスレッド以外 / 未対応 platform


def run_watch(queue: Path, args: argparse.Namespace) -> int:
    """queue.jsonl を live-tail し、owner 宛の滞留を検知したら 1 行 print して 0 を返す。"""
    # 起動前 backlog のスナップショット: 既存ログを 1 回走査し、owner 宛の
    # 未配達件数（enqueued - delivered、負なら 0）を数える。起動後に観測する
    # delivered は enqueue 順（FIFO）でまずこの既存 backlog に充当し、
    # 本セッション中の新規 pending を相殺させない。
    pre_chunk, offset = read_new_chunk(queue, 0)
    pre_enqueued = 0
    pre_delivered = 0
    # 起動前に claim され未配達のまま lease 中の id（挿入順を保持する dict-as-set。
    # 逆順窓トリムで「古い claim から pre_backlog 件」を残すために順序が要る）
    old_claimed: dict[str, None] = {}
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
            old_claimed.clear()
        elif ev == "message_enqueued" and rec.get("to_id") == args.owner:
            pre_enqueued += 1
        elif ev == "claimed" and rec.get("owner") == args.owner:
            ids = rec.get("ids")
            if isinstance(ids, list):
                for i in ids:
                    old_claimed[str(i)] = None
        elif ev == "lease_reaped":
            old_claimed.pop(str(rec.get("id")), None)
        elif ev == "delivered" and rec.get("owner") == args.owner:
            old_claimed.pop(str(rec.get("id")), None)
            pre_delivered += 1
        elif ev == "queue_drained" and rec.get("agent_id") == args.owner:
            pre_delivered += drained_count(rec)
    pre_backlog = max(0, pre_enqueued - pre_delivered)
    # 逆順窓トリム: claimed が対応する message_enqueued より先に journal に落ちる
    # 小窓を pre-scan が跨ぐと、live で現れる enqueue の分の claim が旧扱いに紛れる。
    # 旧 in-flight は高々 pre_backlog 件しか存在しえないので、claim の古い順に
    # pre_backlog 件までへ絞る（あふれた新しい claim は live 側の会計に委ねる）
    if len(old_claimed) > pre_backlog:
        old_claimed = dict.fromkeys(list(old_claimed)[:pre_backlog])
    # 旧 backlog を「claim 済み in-flight（id 追跡、drain にスキップされる）」と
    # 「unclaimed（drain / 新規配達の FIFO 充当対象）」に分ける
    old_unclaimed = max(0, pre_backlog - len(old_claimed))

    pending: list[float] = []  # 新規の owner 宛 enqueue の ts（enqueue 順）
    # broker は queue 反映後にロック外で journal するため、配達記録
    # (delivered / queue_drained) が対応する message_enqueued より先に journal に
    # 落ちる小窓がある。その窓を跨いで起動した場合の余剰配達分
    # (pre_delivered > pre_enqueued) を live 会計に引き継ぎ、直後に現れる
    # enqueue 記録を「配達済み」として相殺できるようにする（捨てると誤発報になる）
    delivered = max(0, pre_delivered - pre_enqueued)

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
                old_unclaimed = 0
                old_claimed.clear()
            elif ev == "message_enqueued" and rec.get("to_id") == args.owner:
                ts = rec.get("ts")
                pending.append(ts if isinstance(ts, (int, float)) else time.time())
            elif ev == "claimed" and rec.get("owner") == args.owner:
                # live claim は FIFO で最古の UNDELIVERED 行から掴む。旧 unclaimed が
                # 残っていればそれが対象なので old_claimed（id 追跡）へ移す。
                # これをしないと claim された旧行は drain にスキップされるのに
                # old_unclaimed に残り、新規メッセージの drain を横取りして誤発報する。
                # 旧 unclaimed が尽きていれば新規 pending への claim であり、その
                # delivered は通常の新規配達として数えられるため何もしない
                ids = rec.get("ids")
                if isinstance(ids, list):
                    for i in ids:
                        if old_unclaimed > 0:
                            old_unclaimed -= 1
                            old_claimed[str(i)] = None
            elif ev == "lease_reaped":
                rid = str(rec.get("id"))
                if rid in old_claimed:
                    # 旧 in-flight の lease が失効し UNDELIVERED に戻った:
                    # 以後は drain / 配達の FIFO 充当対象（unclaimed 側）になる
                    old_claimed.pop(rid, None)
                    old_unclaimed += 1
            elif ev == "delivered" and rec.get("owner") == args.owner:
                rid = str(rec.get("id"))
                if rid in old_claimed:
                    # 起動前から lease 中だった旧 in-flight の完了。新規会計に触れない
                    old_claimed.pop(rid, None)
                elif old_unclaimed > 0:
                    old_unclaimed -= 1  # 旧 unclaimed 分の配達に充当（FIFO 前提）
                else:
                    delivered += 1
            elif ev == "queue_drained" and rec.get("agent_id") == args.owner:
                # drain は claim 済み行をスキップするので、旧 backlog のうち
                # unclaimed 分にのみ充当し、残りを新規配達として数える
                units = drained_count(rec)
                consumed = min(old_unclaimed, units)
                old_unclaimed -= consumed
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pid_file = Path(args.pid_file)

    if args.stop:
        return run_stop(pid_file)

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

    _install_sigterm_handler()
    write_pid_file(pid_file, state_dir, args)
    try:
        return run_watch(queue, args)
    finally:
        # graceful 終了（検知 exit / SIGTERM）で自分の sidecar を掃除する。
        # kill -9 やクラッシュ時は残るが、その場合は --stop 側が
        # identity 照合で stale と判定して掃除する。
        remove_pid_file(pid_file, expected_pid=os.getpid())


if __name__ == "__main__":
    sys.exit(main())
