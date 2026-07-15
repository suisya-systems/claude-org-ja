#!/usr/bin/env python3
"""dashboard/server.py を identity 照合して停止する (pid recycle 誤 kill 防止).

/org-down が呼ぶ「stale-pid-safe なダッシュボード停止」。`.state/dashboard.pid` は
bare pid のみを持つため、recycle された pid を無検証で kill すると無関係プロセスを撃つ。
本ツールは pid の生存だけでなく、その live argv が `dashboard/server.py` を含むことを
照合できたときだけ SIGTERM し、外れたら kill せず pid file を stale として削除する。

live argv の取得は Linux/WSL では `/proc/<pid>/cmdline`、macOS/BSD では `ps -p <pid>
-o args=` フォールバックで行う (照合ロジックは tools/secretary_queue_watcher.py の
queue-watcher 停止と共通のプリミティブを再利用する)。`/proc` も `ps` も無い環境
(Windows native) では argv を確認できないため exit 2 を返し、pid file を残す
(org-down は Windows では PowerShell の Get-CimInstance 手順を使う)。

exit code: 0 = 停止 / 既停止 / stale 掃除 (正常系)、1 = kill 権限不足、
2 = identity 未確認で kill せず保留 (Windows native)。

依存: Python 標準ライブラリのみ。
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

# 直接実行 (``python tools/stop_dashboard.py``) では Python が ``tools/`` を sys.path に
# 載せるため ``from tools.secretary_queue_watcher import ...`` が失敗する。repo root を
# 先頭へ挿入し、-m 起動・直接実行のどちらでも import を成立させる (gen_skill_prose.py 等と同型)。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 同じ「live プロセス identity で誤 kill を避ける」プリミティブを再利用する (重複回避)。
from tools.secretary_queue_watcher import live_cmdline, pid_alive, wait_gone  # noqa: E402

DEFAULT_PID_FILE = ".state/dashboard.pid"
SERVER_IDENT = "dashboard/server.py"


def run_stop(pid_file: Path) -> int:
    if not pid_file.exists():
        print(
            f"[stop-dashboard] {pid_file} が無い（dashboard は起動していない）。no-op。"
        )
        return 0
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"[stop-dashboard] pid file を読めない（{exc}）。stale として削除。")
        _safe_unlink(pid_file)
        return 0
    try:
        pid = int(raw)
    except ValueError:
        print(f"[stop-dashboard] pid file の中身が pid でない（{raw!r}）。stale 削除。")
        _safe_unlink(pid_file)
        return 0

    if not pid_alive(pid):
        print(
            f"[stop-dashboard] dashboard pid={pid} は既に消滅（stale）。"
            "kill せず pid file を削除。"
        )
        _safe_unlink(pid_file)
        return 0

    cmd = live_cmdline(pid)
    if cmd is None:
        print(
            f"[stop-dashboard] identity 未確認: pid={pid} の argv を確認できない"
            "（/proc も ps も無い = Windows native）。誤 kill を避けるため kill せず "
            "pid file も残す。Windows native は PowerShell の Get-CimInstance 手順で停止すること。"
        )
        return 2
    if not any(SERVER_IDENT in part for part in cmd):
        print(
            f"[stop-dashboard] identity 不一致: pid={pid} は dashboard/server.py でない"
            "（recycle で別プロセスに再利用済み）。誤 kill を避けるため kill せず stale 削除。"
        )
        _safe_unlink(pid_file)
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"[stop-dashboard] pid={pid} は kill 直前に消滅。pid file を削除。")
        _safe_unlink(pid_file)
        return 0
    except PermissionError:
        print(
            f"[stop-dashboard] pid={pid} を kill する権限が無い。手動確認が必要"
            "（pid file は残す）。",
            file=sys.stderr,
        )
        return 1
    wait_gone(pid)
    _safe_unlink(pid_file)
    print(f"[stop-dashboard] dashboard (pid={pid}) stopped")
    return 0


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stop dashboard/server.py identified by .state/dashboard.pid, only "
            "when the live process identity still matches (mis-kill safe)."
        ),
    )
    parser.add_argument(
        "--pid-file",
        default=DEFAULT_PID_FILE,
        help="dashboard pid file path (cwd-relative, default: %(default)s)",
    )
    args = parser.parse_args(argv)
    return run_stop(Path(args.pid_file))


if __name__ == "__main__":
    sys.exit(main())
