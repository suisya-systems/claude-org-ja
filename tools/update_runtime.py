#!/usr/bin/env python3
"""Runtime updater for claude-org-runtime (check + apply, Issue #626).

Brings the installed ``claude-org-runtime`` up to the newest PyPI
release that still satisfies ja's pin window (declared in
``pyproject.toml`` dependencies). No version number is hard-coded
anywhere: installed comes from ``importlib.metadata``, latest from the
PyPI JSON API, and the pin spec from ``pyproject.toml`` at read time.

All of the version/PyPI/pin logic is REUSED from
``check_runtime_version`` (imported as ``crv``) rather than
reimplemented, so the pin-window resolution, prerelease/yanked
filtering, and packaging-absent fallback stay in one place.

Default is dry-run: it only reports what it would do. With ``--apply``
it runs ``python -m pip install --upgrade '<package><pin-spec>'`` so
pip resolves strictly inside the pin window (never crossing the
exclusive upper bound to an out-of-window major/minor). It is
idempotent: when the installed version already equals the in-window
latest, it is a no-op and never shells out to pip, even under
``--apply``.

Exit-code convention (consistent with check_runtime_version.py, which
returns 0 in every informational/skip/no-op/drift case):

    * 0  -- dry-run report, no-op (already up to date), and every
            non-fatal skip (offline / PyPI unreachable / package not
            installed / packaging import absent / pin parse failure).
    * 1  -- ONLY when an explicit ``--apply`` pip subprocess actually
            fails (non-zero return code). This is the single non-zero
            path.

Out of scope: this tool does not change /org-start, which never
auto-upgrades.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

# Reuse hinge: import the sibling module as a whole so every helper
# lookup is late-bound through ``crv.<name>``. This matters for tests:
# the mirror suite patches via ``mock.patch.object(check_runtime_version,
# "_latest_version", ...)``; going through the module object means those
# patches take effect here too (a ``from ... import _latest_version``
# would bind a local name the patch could not reach).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_runtime_version as crv  # noqa: E402

# Post-apply guidance. ASCII dashes only (cp932-safe console output).
INTEGRITY_HINT = (
    "次に整合チェックを実行してください: "
    "python tools/check_role_configs.py / "
    "python tools/check_runtime_version.py"
)


def _is_already_current(installed: str, latest: str) -> bool:
    """True when ``installed`` should be treated as already at (or ahead
    of) the in-window ``latest`` -- a no-op for pip's ``>=`` floor.

    The in-window latest can legitimately sit BELOW installed: a newer
    in-window release may be yanked (``crv._release_is_yanked`` drops it)
    or installed may be a local/dev build ahead of PyPI. Plain ``!=``
    would then mislabel a non-existent downgrade as an upgrade. Compare
    with ``packaging.Version`` when available; fall back to string
    equality when packaging is absent or a version fails to parse
    (mirrors crv's packaging-optional contract)."""
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return installed == latest
    try:
        return Version(installed) >= Version(latest)
    except InvalidVersion:
        return installed == latest


def _pip_install_target(pin: str | None) -> str:
    """Build the ``<package><pin-spec>`` install target, e.g.
    ``claude-org-runtime>=0.1.30,<0.2``. Single source of truth so the
    dry-run echo and the real pip argv use the identical string. This
    mirrors check_runtime_version.main()'s upgrade_target construction
    so pip resolves within the pin window."""
    return f"{crv.PACKAGE}{pin or ''}"


def _run_pip_upgrade(target: str) -> int:
    """Run ``python -m pip install --upgrade <target>`` and return the
    pip return code.

    ``sys.executable -m pip`` pins the upgrade to the SAME interpreter
    this script runs under (no PATH ambiguity). The target is passed as
    a SINGLE argv element (no shell=True), so the pin spec's ``<`` / ``>``
    are not shell redirects and pip receives the requirement verbatim.
    Wrapped in its own function so tests can patch it cleanly."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", target]
    result = subprocess.run(cmd)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_runtime.py",
        description=(
            "claude-org-runtime をピン窓内の最新へ更新する (check + apply)。"
            " 既定は dry-run で、--apply を付けたときだけ実際に更新する。"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "ピン窓内の最新が新しければ pip install --upgrade を実行する"
            " (既定は dry-run のみで実行しない)。"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "適用後の整合チェック案内"
            " (check_role_configs / check_runtime_version) を抑制する。"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 1. Installed version. Non-fatal skip if the package is absent.
    #    Short-circuit before any network call.
    installed = crv._installed_version()
    if installed is None:
        print(f"[runtime update] {crv.PACKAGE} は未インストールです -- skip")
        return 0

    # 2. Pin window from pyproject.toml (may legitimately be None).
    pin = crv._read_pin_spec()

    # 3. In-window latest from PyPI. crv._latest_version(pin) already
    #    collapses offline / PyPI-unreachable / JSON parse failure /
    #    packaging-absent-with-pin / invalid-pin / no-in-window-release
    #    all to None, so a single None check covers every non-fatal
    #    indeterminate case here.
    latest = crv._latest_version(pin)
    if latest is None:
        print(
            "[runtime update] 最新バージョンを判定できませんでした"
            " (オフライン / PyPI 到達不可 / packaging 不在 のいずれか)"
            " -- skip"
        )
        return 0

    # 4. Idempotence guard BEFORE the --apply branch: if already at (or
    #    ahead of) the in-window latest, never shell out to pip, even
    #    under --apply, and never report a phantom upgrade.
    if _is_already_current(installed, latest):
        print(
            f"[runtime update] {crv.PACKAGE}: installed={installed}"
            " は既にピン窓内最新です -- no-op"
        )
        return 0

    # 5. Drift exists. latest is in-window by construction (crv applied
    #    the SpecifierSet), so the target carries the pin window.
    target = _pip_install_target(pin)
    command = f"python -m pip install --upgrade '{target}'"

    if not args.apply:
        # Default: dry-run report only. Exit 0.
        print(
            f"[runtime update] {crv.PACKAGE}: installed={installed}"
            f" -> 更新候補={latest}"
        )
        print(f"[runtime update] 適用するには --apply: {command}")
        return 0

    # --apply: run pip. The ONLY path that can return non-zero.
    print(f"[runtime update] 適用中: {command}")
    rc = _run_pip_upgrade(target)
    if rc != 0:
        print(f"[runtime update] pip install が失敗しました (exit={rc})")
        return 1

    # Re-read installed AFTER pip (fresh on-disk metadata). Defensive:
    # success is tied to pip's return code, not to the re-read, so fall
    # back to the resolved latest if the re-read is inconclusive.
    new = crv._installed_version()
    reported_new = new or latest
    print(
        f"[runtime update] 更新しました: {installed} -> {reported_new}"
    )
    if not args.quiet:
        print(INTEGRITY_HINT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
