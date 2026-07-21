"""herdr / runtime compatibility preflight for claude-org (Issue #748).

org-start が dispatcher を spawn する **前** に、broker daemon の解決済み backend が
``herdr`` のとき **だけ**、接続先 herdr の wire protocol が現在インストール済みの
``claude-org-runtime`` の対応範囲 (``claude_org_runtime.terminal.herdr.
SUPPORTED_PROTOCOLS``) に収まっているかを検査する fail-loud ゲート。

背景 (事故モデル): herdr は self-update 経路を持つため、runtime が追随する前に
daemon が窓外の protocol へ黙って昇格しうる。その状態で dispatcher を spawn すると
``agent.start`` が protocol 不一致で wedge し、原因が「daemon 停止」と誤診断される
(Issue #151 の主症状)。本ゲートは spawn 前に **fail loud** で止め、運用者に
「runtime を上げる」か「herdr を下げる」かを 1 画面で提示する。

判定レイヤ (権威度の高い順):
  1. **socket ping (一次・権威的)**: daemon の herdr socket へ ``ping`` を 1 往復し、
     daemon が **実際に話している** protocol 番号を得る。PATH 上の ``herdr`` binary が
     daemon の接続先と同一とは限らないため、可能ならこれを一次判定にする
     (runtime adapter も同じ ping で protocol を確定する: herdr.py ``_probe_protocol``)。
  2. **``herdr --version`` + ローカル写像 (fallback)**: socket が不通のときの代替。
     PATH binary の version から protocol をローカル写像で導く。**この経路は version
     系列内の protocol bump を検出できない**ため、fallback である旨を stderr に明示する。

backend 判定は ``ORG_TRANSPORT`` だけでは不足 (renga/broker のフレーム差があり、かつ
broker でも backend は herdr とは限らない)。``$ORG_BROKER_STATE_DIR`` (未設定時
``.state/broker``) の ``daemon.json`` sidecar に runtime が書く **解決済み backend**
(broker/sidecar.py ``write_sidecar``) を読み、``herdr`` のときだけゲートを発動する。

**ホスト実行必須**: ``herdr --version`` / socket ping / daemon sidecar はホスト実体
(PATH / プロセス namespace / Unix socket) に依存する。Claude Code の Bash sandbox 内で
実行すると PATH・namespace 差で false negative/positive が出るため、org-start からは
``dangerouslyDisableSandbox: true`` を付けてホスト実行する (Block C2 と同じ制約)。

Usage:
  python3 tools/check_herdr_compat.py            # Mac/Linux
  py -3 tools/check_herdr_compat.py              # Windows
  python3 tools/check_herdr_compat.py --json
  python3 tools/check_herdr_compat.py --state-dir .state/broker

Exit codes (C2 の drift warning とは **混ぜない**: これは spawn 前 fatal gate):
  0 - compatible、または skip (非 broker / 非 herdr backend)
  1 - incompatible: 解決した protocol が runtime の対応範囲外 (fatal)
  2 - unverified: backend は herdr だが protocol を確定できない (fatal)
      未確認を warning 継続にすると今回の事故を再導入するので fatal 側に倒す。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# daemon.json (broker/sidecar.py SIDECAR_NAME) の探索既定。ORG_BROKER_STATE_DIR は
# broker が pane へ注入する state dir 絶対パス (broker/server.py / launcher.py)。
STATE_DIR_ENV = "ORG_BROKER_STATE_DIR"
DEFAULT_STATE_DIR = ".state/broker"
SIDECAR_NAME = "daemon.json"

# broker が herdr backend のときの sidecar backend 値。
HERDR_BACKEND = "herdr"

# socket ping のタイムアウト既定 (adapter の 15s より短く: preflight は即答を期待)。
DEFAULT_PING_TIMEOUT = 5.0


def parse_version(s: str) -> Optional[tuple[int, int, int]]:
    """'herdr 0.7.5' / '0.7.5' / '0.7.5-dev' を (0, 7, 5) に parse する。

    herdr は self-update 経路があり ``0.7.5-dev`` 等の suffix 付き出力もありうるので
    先頭の semver triple のみを拾う (check_renga_compat.parse_version と同型)。
    triple が無ければ None。
    """
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _local_derive_protocol(v: tuple[int, int, int]) -> Optional[int]:
    """herdr version -> wire protocol のローカル写像 (fallback)。

    SoT は herdr src wire.rs / runtime herdr.py の docstring:
      0.7.0-0.7.1 -> 14 / 0.7.2-0.7.4 -> 16 / 0.7.5 以降 -> 17。

    写像窓の外 (0.7.0 未満 / 0.8 以降等) は None を返す。**version 系列内の protocol
    bump は検出できない** (例: 将来 0.7.9 が protocol 18 を話しても本写像は 17 と誤導出
    する) のが本経路の限界であり、その検出は socket ping (一次判定) が担う。
    """
    major, minor, patch = v
    if (major, minor) == (0, 7):
        if patch <= 1:
            return 14
        if patch <= 4:
            return 16
        return 17  # 0.7.5 以降
    return None  # 写像窓の外: 導出不能 (推測しない)


def _runtime_derive_protocol(v: tuple[int, int, int]) -> Optional[int]:
    """runtime が version->protocol の写像 API を将来公開したら使用する (無ければ None)。

    現行 runtime (0.1.37/0.1.38) に該当 API は無い。将来 ``claude_org_runtime.
    terminal.herdr`` が ``protocol_for_version`` のような callable を持つようになったら、
    ローカル写像より優先して使う (SoT を runtime 側へ寄せるため)。
    """
    try:
        from claude_org_runtime.terminal import herdr as rt_herdr
    except Exception:  # noqa: BLE001 - best-effort: API 探索は import 失敗を握り潰す
        return None
    fn: Optional[Callable[..., Any]] = getattr(
        rt_herdr, "protocol_for_version", None
    )
    if not callable(fn):
        return None
    try:
        result = fn(v)
    except Exception:  # noqa: BLE001 - 未知 API のシグネチャ差異は fallback に倒す
        return None
    return result if isinstance(result, int) and not isinstance(result, bool) else None


def derive_protocol(v: tuple[int, int, int]) -> Optional[int]:
    """version -> protocol を導出する。runtime API があれば使用、無ければローカル写像。"""
    api = _runtime_derive_protocol(v)
    if api is not None:
        return api
    return _local_derive_protocol(v)


def runtime_supported_protocols() -> tuple[Optional[list[int]], Optional[str]]:
    """runtime の ``SUPPORTED_PROTOCOLS`` を import して sorted list で返す。

    対応窓の SoT は ja 側 hard-code ではなく runtime。import 不能 (別 venv / 未
    インストール) なら (None, 診断文字列) を返し、呼出側は exit 2 (unverified) に倒す。
    """
    try:
        from claude_org_runtime.terminal.herdr import SUPPORTED_PROTOCOLS
    except Exception as e:  # noqa: BLE001 - import 失敗は unverified として顕在化
        return None, f"{type(e).__name__}: {e}"
    try:
        return sorted(int(p) for p in SUPPORTED_PROTOCOLS), None
    except Exception as e:  # noqa: BLE001 - 想定外の型は unverified 扱い
        return None, f"unexpected SUPPORTED_PROTOCOLS shape: {e}"


def run_cmd(args: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """subprocess を回し (returncode, stdout, stderr) を返す。

    FileNotFoundError は returncode=127 (POSIX 慣習) に写像し、'binary 不在' と
    'binary は走ったが失敗' を呼出側が区別できるようにする。
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", f"{args[0]}: not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"{args[0]}: timed out after {timeout}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def resolve_state_dir(explicit: Optional[str]) -> str:
    """daemon.json を探す state dir を解決する: flag > env > 既定 .state/broker。"""
    if explicit:
        return explicit
    env = os.environ.get(STATE_DIR_ENV)
    if env:
        return env
    return DEFAULT_STATE_DIR


def read_daemon_backend(state_dir: str) -> tuple[bool, Optional[str]]:
    """daemon.json を読み (存在フラグ, 解決済み backend) を返す。

    存在しない / 壊れている場合は (False, None)。broker daemon が discoverable でない
    (= 非 broker transport か daemon 未起動) ことを意味し、herdr ゲートは非該当。
    """
    path = os.path.join(state_dir, SIDECAR_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, None
    except (json.JSONDecodeError, OSError):
        # 壊れた sidecar は「発見できない」扱い (torn write / 破損を herdr と誤断しない)
        return False, None
    if not isinstance(data, dict):
        return False, None
    return True, data.get("backend")


@dataclass
class _PingResult:
    ok: bool = False
    version: Optional[str] = None
    protocol: Optional[int] = None
    socket_path: Optional[str] = None
    error: Optional[str] = None


def probe_daemon_ping(timeout: float = DEFAULT_PING_TIMEOUT) -> _PingResult:
    """daemon の herdr socket へ ``ping`` を 1 往復し protocol/version を得る (best-effort)。

    socket 解決は runtime の ``resolve_socket_path`` (SoT。HERDR_SOCKET_PATH env /
    HERDR_SESSION / 既定 config dir の順) を借りる。wire は newline-delimited JSON で
    ``{"id","method":"ping","params":{}}`` を送り ``result`` に ``{type,version,
    protocol}`` が載る (herdr.py ``_probe_protocol``)。14-17 で ping 応答形は不変。

    どの段で失敗しても例外を投げず :class:`_PingResult` (ok=False) を返す。socket が
    使えない環境 (Windows: AF_UNIX 不在) や daemon 不通は fallback (version 写像) に倒す。
    """
    res = _PingResult()
    if not hasattr(socket, "AF_UNIX"):
        res.error = "AF_UNIX unavailable on this platform (herdr is POSIX/WSL only)"
        return res
    try:
        from claude_org_runtime.terminal.herdr import resolve_socket_path
    except Exception as e:  # noqa: BLE001 - runtime 不在は fallback に倒す
        res.error = f"cannot import resolve_socket_path: {type(e).__name__}: {e}"
        return res
    try:
        sock_path = resolve_socket_path()
    except Exception as e:  # noqa: BLE001
        res.error = f"resolve_socket_path failed: {type(e).__name__}: {e}"
        return res
    res.socket_path = sock_path
    payload = (
        json.dumps({"id": "check-herdr-compat", "method": "ping", "params": {}})
        + "\n"
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(sock_path)
            sock.sendall(payload.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                data = sock.recv(65536)
                if not data:
                    break
                buf += data
    except (OSError, socket.timeout) as e:
        res.error = f"socket unreachable at {sock_path!r}: {e}"
        return res
    line = buf.split(b"\n", 1)[0]
    if not line:
        res.error = "connection closed before a response line"
        return res
    try:
        resp = json.loads(line.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError) as e:
        res.error = f"unparseable ping response frame: {e}"
        return res
    result = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(result, dict):
        res.error = f"ping response missing 'result': {resp!r}"
        return res
    protocol = result.get("protocol")
    if not isinstance(protocol, int) or isinstance(protocol, bool):
        res.error = f"ping response has no usable 'protocol': {result!r}"
        return res
    res.ok = True
    res.protocol = protocol
    version = result.get("version")
    res.version = version if isinstance(version, str) else None
    return res


@dataclass
class HerdrCompatReport:
    exit_code: int = 0
    skipped: bool = False
    skip_reason: Optional[str] = None
    state_dir: Optional[str] = None
    daemon_backend: Optional[str] = None
    # PATH `herdr --version` 経路
    installed_herdr_version: Optional[str] = None
    installed_herdr_path: Optional[str] = None
    derived_protocol: Optional[int] = None
    # socket ping (権威的) 経路
    ping_ok: bool = False
    ping_version: Optional[str] = None
    ping_protocol: Optional[int] = None
    ping_socket_path: Optional[str] = None
    ping_error: Optional[str] = None
    # runtime 対応窓
    runtime_supported: Optional[list[int]] = None
    runtime_import_error: Optional[str] = None
    # 判定結果
    effective_protocol: Optional[int] = None
    effective_source: Optional[str] = None
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # stderr 行 (診断・限界注記)
    remediation: list[str] = field(default_factory=list)


def gather_installed_herdr(report: HerdrCompatReport) -> None:
    """PATH の ``herdr --version`` を取り、version と derived protocol を埋める。"""
    report.installed_herdr_path = shutil.which("herdr")
    rc, out, err = run_cmd(["herdr", "--version"])
    if rc == 127:
        report.warnings.append(
            "herdr not found on PATH; cannot derive protocol from binary version "
            "(socket ping is the authoritative fallback)"
        )
        return
    if rc != 0:
        report.warnings.append(
            f"`herdr --version` exited {rc}: {err.strip()[:160]}"
        )
        return
    v = parse_version(out)
    if v is None:
        report.warnings.append(
            f"could not parse herdr version from output: {out.strip()!r}"
        )
        return
    report.installed_herdr_version = ".".join(str(x) for x in v)
    report.derived_protocol = derive_protocol(v)
    if report.derived_protocol is None:
        report.warnings.append(
            f"herdr {report.installed_herdr_version} is outside the known "
            "version->protocol map window; cannot derive protocol from binary "
            "(rely on socket ping)"
        )


def decide(report: HerdrCompatReport) -> None:
    """収集済みシグナルから effective protocol と exit code を確定する (pure)。

    権威度: socket ping (daemon 実体) > herdr --version + ローカル写像。ping が使える
    ときは常に ping を採る (PATH binary が daemon 接続先と同一とは限らないため)。
    """
    supported = report.runtime_supported
    if supported is None:
        report.exit_code = 2
        report.failures.append(
            "cannot import claude_org_runtime.terminal.herdr SUPPORTED_PROTOCOLS "
            f"({report.runtime_import_error}); runtime supported window unknown "
            "-> protocol compatibility unverified"
        )
        report.remediation.append(
            "ensure claude-org-runtime is installed in this Python "
            "(run this preflight on the host, not inside the Bash sandbox)"
        )
        return

    # 権威的シグナル選択
    if report.ping_protocol is not None:
        report.effective_protocol = report.ping_protocol
        report.effective_source = "socket ping (daemon)"
        # PATH binary と daemon が食い違うなら明示 (Codex Major: PATH != daemon)
        if (
            report.derived_protocol is not None
            and report.derived_protocol != report.ping_protocol
        ):
            report.warnings.append(
                "PATH `herdr --version` derives protocol "
                f"{report.derived_protocol} but the daemon socket speaks "
                f"{report.ping_protocol}; the PATH binary is NOT the daemon's "
                "herdr. Using the daemon protocol as authoritative."
            )
    elif report.derived_protocol is not None:
        report.effective_protocol = report.derived_protocol
        report.effective_source = "herdr --version (local version->protocol map)"
        report.warnings.append(
            "socket ping unavailable "
            f"({report.ping_error or 'no ping'}); fell back to PATH "
            "`herdr --version`. The PATH binary may differ from the daemon's "
            "connected herdr, and the local map cannot detect a protocol bump "
            "within a version series. Re-run on the host with the daemon "
            "reachable for an authoritative check."
        )

    if report.effective_protocol is None:
        report.exit_code = 2
        report.failures.append(
            "backend is herdr but the wire protocol could not be determined "
            "by either socket ping or `herdr --version` -> unverified"
        )
        report.remediation.append(
            "run this preflight on the host (not the Bash sandbox) with the "
            "herdr daemon running, or ensure `herdr` is on PATH"
        )
        return

    if report.effective_protocol in supported:
        report.exit_code = 0
        return

    report.exit_code = 1
    report.failures.append(
        f"herdr wire protocol {report.effective_protocol} "
        f"(source: {report.effective_source}) is OUTSIDE the runtime supported "
        f"set {supported}. Spawning dispatcher now would wedge on protocol "
        "mismatch (Issue #151 symptom)."
    )
    report.remediation.append(
        "resolve the mismatch before spawning, either: (a) upgrade "
        "claude-org-runtime to a version whose SUPPORTED_PROTOCOLS includes "
        f"protocol {report.effective_protocol}, or (b) pin/downgrade herdr to a "
        f"version whose protocol is in {supported}."
    )


def run_gate(
    state_dir_arg: Optional[str], ping_timeout: float, skip_ping: bool
) -> HerdrCompatReport:
    """ゲート本体。skip / verify を判定し :class:`HerdrCompatReport` を返す。"""
    report = HerdrCompatReport()
    report.state_dir = resolve_state_dir(state_dir_arg)

    present, backend = read_daemon_backend(report.state_dir)
    if not present:
        report.skipped = True
        report.skip_reason = (
            f"no broker daemon sidecar ({SIDECAR_NAME}) under "
            f"{report.state_dir!r}: non-broker transport or daemon not running; "
            "herdr compatibility gate not applicable"
        )
        report.exit_code = 0
        return report

    report.daemon_backend = backend
    if backend != HERDR_BACKEND:
        report.skipped = True
        report.skip_reason = (
            f"broker backend is {backend!r} (not {HERDR_BACKEND!r}); "
            "herdr compatibility gate not applicable"
        )
        report.exit_code = 0
        return report

    # backend is herdr -> 実検査
    gather_installed_herdr(report)

    if not skip_ping:
        ping = probe_daemon_ping(ping_timeout)
        report.ping_ok = ping.ok
        report.ping_version = ping.version
        report.ping_protocol = ping.protocol
        report.ping_socket_path = ping.socket_path
        report.ping_error = ping.error

    report.runtime_supported, report.runtime_import_error = (
        runtime_supported_protocols()
    )

    decide(report)
    return report


# Reporting -------------------------------------------------------------------


def emit_text(report: HerdrCompatReport) -> None:
    print("herdr / runtime compatibility preflight")
    print("=" * 56)

    if report.skipped:
        print(f"state_dir:            {report.state_dir}")
        print(f"daemon backend:       {report.daemon_backend or '(none)'}")
        print(f"SKIP: {report.skip_reason}")
        print()
        print("Result: SKIP (exit 0)")
        return

    def show(label: str, value: Any) -> None:
        print(f"{label:<22} {value}")

    show("state_dir:", report.state_dir)
    show("daemon backend:", report.daemon_backend)

    installed = report.installed_herdr_version or "(unknown)"
    if report.installed_herdr_path:
        installed = f"{installed}  ({report.installed_herdr_path})"
    show("installed herdr:", installed)
    show(
        "derived protocol:",
        f"{report.derived_protocol if report.derived_protocol is not None else '(none)'}"
        "  [via local version->protocol map]",
    )
    if report.ping_ok:
        show(
            "daemon ping:",
            f"herdr {report.ping_version or '(?)'}, protocol {report.ping_protocol}",
        )
    else:
        show("daemon ping:", f"unavailable ({report.ping_error or 'skipped'})")
    show(
        "runtime supported:",
        report.runtime_supported
        if report.runtime_supported is not None
        else f"(unknown: {report.runtime_import_error})",
    )
    show(
        "effective protocol:",
        f"{report.effective_protocol if report.effective_protocol is not None else '(undetermined)'}"
        + (f"  [source: {report.effective_source}]" if report.effective_source else ""),
    )

    if report.failures:
        print()
        print("Failures:")
        for f in report.failures:
            print(f"  - {f}")

    if report.remediation:
        print()
        print("Remediation:")
        for r in report.remediation:
            print(f"  - {r}")

    print()
    verdict = {0: "COMPATIBLE", 1: "INCOMPATIBLE", 2: "UNVERIFIED"}.get(
        report.exit_code, "UNKNOWN"
    )
    print(f"Result: {verdict} (exit {report.exit_code})")


def emit_json(report: HerdrCompatReport) -> None:
    doc = {
        "exit_code": report.exit_code,
        "skipped": report.skipped,
        "skip_reason": report.skip_reason,
        "state_dir": report.state_dir,
        "daemon_backend": report.daemon_backend,
        "installed_herdr": {
            "version": report.installed_herdr_version,
            "path": report.installed_herdr_path,
            "derived_protocol": report.derived_protocol,
        },
        "daemon_ping": {
            "ok": report.ping_ok,
            "version": report.ping_version,
            "protocol": report.ping_protocol,
            "socket_path": report.ping_socket_path,
            "error": report.ping_error,
        },
        "runtime_supported_protocols": report.runtime_supported,
        "runtime_import_error": report.runtime_import_error,
        "effective_protocol": report.effective_protocol,
        "effective_source": report.effective_source,
        "failures": report.failures,
        "warnings": report.warnings,
        "remediation": report.remediation,
    }
    print(json.dumps(doc, indent=2, ensure_ascii=False))


def _reconfigure_stdout() -> None:
    # Windows の cp932 コンソールが非 ASCII を吐けずにクラッシュしないよう UTF-8 に
    # 再ラップする (check_renga_compat と同型。出力自体は ASCII に寄せている)。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv: Optional[list[str]] = None) -> int:
    _reconfigure_stdout()

    p = argparse.ArgumentParser(
        description="herdr/runtime protocol compatibility preflight for claude-org"
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of console text",
    )
    p.add_argument(
        "--state-dir", default=None,
        help="broker state dir holding daemon.json "
             "(default: $ORG_BROKER_STATE_DIR or .state/broker)",
    )
    p.add_argument(
        "--ping-timeout", type=float, default=DEFAULT_PING_TIMEOUT,
        help=f"herdr socket ping timeout in seconds (default {DEFAULT_PING_TIMEOUT})",
    )
    p.add_argument(
        "--skip-ping", action="store_true",
        help="skip the authoritative socket ping (fall back to `herdr --version`); "
             "for testing only - degrades protocol detection accuracy",
    )
    args = p.parse_args(argv)

    report = run_gate(args.state_dir, args.ping_timeout, args.skip_ping)

    if args.json:
        emit_json(report)
    else:
        emit_text(report)

    # 限界注記・診断は stderr に出す (stdout は診断ブロック/JSON 専用に保つ)。
    for w in report.warnings:
        print(f"[warn] {w}", file=sys.stderr)

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
