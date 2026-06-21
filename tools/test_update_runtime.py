"""Unit tests for tools/update_runtime.py (Issue #626).

The updater layers a CLI (check + apply) over check_runtime_version's
pin-window resolution. It must:

* default to dry-run: report drift, never call pip
* with --apply: run pip, re-read installed, report old -> new, then
  print the integrity-check hint (suppressed by --quiet)
* be idempotent: when installed == in-window latest, no-op and NEVER
  call pip even under --apply
* skip non-fatally (exit 0, no traceback) for: package not installed,
  offline / PyPI unreachable, packaging absent, no in-window release
* respect ja's pin window (never target an out-of-window release)
* return non-zero (1) ONLY when an explicit --apply pip subprocess
  actually fails; 0 in every other case

Patches target ``check_runtime_version`` because update_runtime calls
its helpers through ``crv.<name>`` (late-bound module lookup).
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_runtime_version  # noqa: E402
import update_runtime  # noqa: E402

try:  # noqa: SIM105
    import packaging  # type: ignore  # noqa: F401

    _HAS_PACKAGING = True
except ImportError:
    _HAS_PACKAGING = False

requires_packaging = unittest.skipUnless(
    _HAS_PACKAGING,
    "packaging not installed -- pin-window resolution falls back to "
    "silent skip, so the pin-aware paths are untestable here",
)


def _fake_urlopen(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    return lambda *a, **kw: _Resp(body)


def _payload(*versions: str, yanked: tuple[str, ...] = ()) -> dict:
    """Build a minimally-PyPI-shaped JSON payload from the given
    release versions (newest last). Versions in ``yanked`` are emitted
    with every file marked yanked."""
    releases = {}
    for v in versions:
        releases[v] = [{"yanked": v in yanked}]
    return {
        "info": {"version": versions[-1] if versions else ""},
        "releases": releases,
    }


def _run_main(argv: list[str]) -> tuple[int, str]:
    """Drive update_runtime.main(argv) with stdout captured."""
    buf = io.StringIO()
    with mock.patch.object(sys, "stdout", buf):
        code = update_runtime.main(argv)
    return code, buf.getvalue()


class DryRunTest(unittest.TestCase):
    def test_dry_run_default_reports_drift_and_command_no_pip(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.29"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade"
        ) as pip_mock:
            code, out = _run_main([])
        self.assertEqual(code, 0)
        self.assertIn("[runtime update]", out)
        self.assertIn("installed=0.1.29", out)
        self.assertIn("-> 更新候補=0.1.30", out)
        self.assertIn(
            "python -m pip install --upgrade 'claude-org-runtime>=0.1.29,<0.2'",
            out,
        )
        pip_mock.assert_not_called()


class ApplyTest(unittest.TestCase):
    def test_apply_runs_pip_and_reports_old_to_new(self):
        # installed returns old before pip, new after the re-read.
        with mock.patch.object(
            check_runtime_version,
            "_installed_version",
            side_effect=["0.1.29", "0.1.30"],
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade", return_value=0
        ) as pip_mock:
            code, out = _run_main(["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("更新しました: 0.1.29 -> 0.1.30", out)
        self.assertIn("check_role_configs.py", out)
        self.assertIn("check_runtime_version.py", out)
        pip_mock.assert_called_once_with("claude-org-runtime>=0.1.29,<0.2")

    def test_apply_quiet_suppresses_integrity_hint(self):
        with mock.patch.object(
            check_runtime_version,
            "_installed_version",
            side_effect=["0.1.29", "0.1.30"],
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade", return_value=0
        ):
            code, out = _run_main(["--apply", "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("更新しました: 0.1.29 -> 0.1.30", out)
        self.assertNotIn("check_role_configs.py", out)

    def test_apply_pip_failure_returns_1(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.29"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade", return_value=5
        ):
            code, out = _run_main(["--apply"])
        self.assertEqual(code, 1)
        self.assertIn("pip install が失敗しました (exit=5)", out)
        self.assertNotIn("更新しました", out)

    def test_apply_reread_inconclusive_falls_back_to_latest(self):
        # pip returns 0 but the post-apply re-read is None: still a
        # success (tied to pip rc), reported via the resolved latest.
        with mock.patch.object(
            check_runtime_version,
            "_installed_version",
            side_effect=["0.1.29", None],
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade", return_value=0
        ):
            code, out = _run_main(["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("更新しました: 0.1.29 -> 0.1.30", out)


class IdempotenceTest(unittest.TestCase):
    def test_noop_dry_run(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.30"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ):
            code, out = _run_main([])
        self.assertEqual(code, 0)
        self.assertIn("no-op", out)
        self.assertIn("既にピン窓内最新", out)
        self.assertNotIn("pip install --upgrade", out)

    def test_noop_apply_does_not_call_pip(self):
        # The idempotence-under-apply guard: pip is NOT invoked.
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.30"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade"
        ) as pip_mock:
            code, out = _run_main(["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("no-op", out)
        pip_mock.assert_not_called()

    @requires_packaging
    def test_installed_ahead_of_latest_is_noop_no_phantom_upgrade(self):
        # In-window latest can sit BELOW installed (e.g. the newer
        # in-window release was yanked, or installed is a dev build).
        # That must be a no-op, never a "更新候補/更新しました" upgrade
        # claim, and never call pip even under --apply.
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.31"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.30"
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade"
        ) as pip_mock:
            code, out = _run_main(["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("no-op", out)
        self.assertNotIn("更新候補", out)
        self.assertNotIn("更新しました", out)
        pip_mock.assert_not_called()


class SkipTest(unittest.TestCase):
    def test_not_installed_skip_short_circuits_before_pypi(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value=None
        ), mock.patch.object(
            check_runtime_version, "_latest_version"
        ) as latest_mock:
            code, out = _run_main([])
        self.assertEqual(code, 0)
        self.assertIn("未インストール", out)
        self.assertIn("skip", out)
        latest_mock.assert_not_called()

    def test_offline_skip(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.29"
        ), mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            code, out = _run_main([])
        self.assertEqual(code, 0)
        self.assertIn("判定できませんでした", out)
        self.assertIn("skip", out)

    @requires_packaging
    def test_no_in_window_release_skip(self):
        payload = _payload("0.2.0", "0.2.1")
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.29"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            code, out = _run_main([])
        self.assertEqual(code, 0)
        self.assertIn("判定できませんでした", out)

    def test_apply_with_indeterminate_latest_skips_without_pip(self):
        # Regression guard: a skip path (latest is None) under --apply
        # must short-circuit BEFORE the pip branch -- real pip is never
        # shelled out while offline / PyPI-unreachable.
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.29"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value=None
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade"
        ) as pip_mock:
            code, out = _run_main(["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("判定できませんでした", out)
        pip_mock.assert_not_called()

    def test_apply_when_not_installed_skips_without_pip(self):
        # Not-installed skip under --apply must also precede the pip
        # branch (no upgrade attempt for an absent package).
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value=None
        ), mock.patch.object(
            update_runtime, "_run_pip_upgrade"
        ) as pip_mock:
            code, out = _run_main(["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("未インストール", out)
        pip_mock.assert_not_called()


class PinWindowTest(unittest.TestCase):
    @requires_packaging
    def test_pin_window_resolution_end_to_end(self):
        # Out-of-window 0.2.x exists on PyPI but must NOT be the target;
        # the in-window latest 0.1.31 must be reported instead.
        payload = _payload("0.1.29", "0.1.31", "0.2.0", "0.2.1")
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.29"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=">=0.1.29,<0.2"
        ), mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            code, out = _run_main([])
        self.assertEqual(code, 0)
        self.assertIn("-> 更新候補=0.1.31", out)
        self.assertNotIn("0.2.1", out)
        self.assertNotIn("0.2.0", out)


class InstallTargetTest(unittest.TestCase):
    def test_target_carries_pin(self):
        self.assertEqual(
            update_runtime._pip_install_target(">=0.1.29,<0.2"),
            "claude-org-runtime>=0.1.29,<0.2",
        )

    def test_target_bare_when_pin_none(self):
        self.assertEqual(
            update_runtime._pip_install_target(None),
            "claude-org-runtime",
        )


class ParserTest(unittest.TestCase):
    def test_flags_default_false(self):
        args = update_runtime.build_parser().parse_args([])
        self.assertFalse(args.apply)
        self.assertFalse(args.quiet)

    def test_flags_set(self):
        args = update_runtime.build_parser().parse_args(["--apply", "--quiet"])
        self.assertTrue(args.apply)
        self.assertTrue(args.quiet)


if __name__ == "__main__":
    unittest.main()
