"""Unit tests for tools/check_runtime_version.py (Issue #472 + the
#119 follow-up that surfaces the sandbox/offline silent skip).

The script is invoked by /org-start Block C2. Its outcome contract:

* stdout carries the single ``[runtime drift]`` line ONLY on drift
  (exit 1); every other non-OK outcome puts its diagnostic on stderr,
  keeping stdout empty and spliceable.
* exit codes distinguish the outcomes so a sandboxed/offline run is no
  longer read as "up to date": 0 up-to-date, 1 drift, 2 could-not-
  verify (offline / PyPI error / JSON parse / no in-window release /
  packaging missing / pin parse failure), 3 package not installed.
* ja's pin window declared in pyproject.toml is respected so the
  warning never steers users to an out-of-window upgrade.
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

try:  # noqa: SIM105
    import packaging  # type: ignore  # noqa: F401

    _HAS_PACKAGING = True
except ImportError:
    _HAS_PACKAGING = False

requires_packaging = unittest.skipUnless(
    _HAS_PACKAGING,
    "packaging not installed — pin-window resolution falls back to "
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
    release versions (newest last, as PyPI usually sorts). Versions
    listed in ``yanked`` are emitted with every file marked yanked."""
    releases = {}
    for v in versions:
        releases[v] = [{"yanked": v in yanked}]
    return {
        "info": {"version": versions[-1] if versions else ""},
        "releases": releases,
    }


class MainCliTest(unittest.TestCase):
    def _run_main(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(
            sys, "stderr", err
        ):
            code = check_runtime_version.main()
        return code, out.getvalue(), err.getvalue()

    def test_drift_prints_one_warning_line_to_stdout(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch.object(
            check_runtime_version,
            "_latest_version_with_reason",
            return_value=("0.1.11", None),
        ):
            code, out, err = self._run_main()
        self.assertEqual(code, check_runtime_version.EXIT_DRIFT)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("[runtime drift]", lines[0])
        self.assertIn("installed=0.1.2", lines[0])
        self.assertIn("latest=0.1.11", lines[0])
        # drift is a clean result: nothing on stderr.
        self.assertEqual(err, "")

    def test_match_is_ok_and_silent(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.11"
        ), mock.patch.object(
            check_runtime_version,
            "_latest_version_with_reason",
            return_value=("0.1.11", None),
        ):
            code, out, err = self._run_main()
        self.assertEqual(code, check_runtime_version.EXIT_OK)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_package_not_installed_reports_on_stderr(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value=None
        ), mock.patch.object(
            check_runtime_version, "_latest_version_with_reason"
        ) as latest_mock:
            code, out, err = self._run_main()
        self.assertEqual(code, check_runtime_version.EXIT_NOT_INSTALLED)
        # stdout stays clean; the reason is surfaced on stderr, not swallowed.
        self.assertEqual(out, "")
        self.assertNotEqual(err.strip(), "")
        latest_mock.assert_not_called()

    def test_offline_is_reported_not_silent(self):
        """#119 regression guard: an unreachable PyPI must NOT be a
        silent exit 0. It surfaces as EXIT_UNVERIFIED with a stderr
        diagnostic, while stdout stays empty (no drift line)."""
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            code, out, err = self._run_main()
        self.assertEqual(code, check_runtime_version.EXIT_UNVERIFIED)
        self.assertEqual(out, "")
        self.assertIn("PyPI", err)

    def test_unverified_reason_goes_to_stderr(self):
        """Every non-offline "could not determine latest" reason also
        yields EXIT_UNVERIFIED + a stderr diagnostic, stdout empty."""
        for reason in (
            check_runtime_version.REASON_PYPI_ERROR,
            check_runtime_version.REASON_NO_IN_WINDOW_RELEASE,
            check_runtime_version.REASON_PACKAGING_MISSING,
            check_runtime_version.REASON_PIN_PARSE_FAILED,
        ):
            with self.subTest(reason=reason):
                with mock.patch.object(
                    check_runtime_version,
                    "_installed_version",
                    return_value="0.1.2",
                ), mock.patch.object(
                    check_runtime_version,
                    "_latest_version_with_reason",
                    return_value=(None, reason),
                ):
                    code, out, err = self._run_main()
                self.assertEqual(code, check_runtime_version.EXIT_UNVERIFIED)
                self.assertEqual(out, "")
                self.assertNotEqual(err.strip(), "")


class LatestVersionTest(unittest.TestCase):
    """Direct tests for _latest_version() and the pin-window logic."""

    @requires_packaging
    def test_picks_latest_within_pin_window(self):
        payload = _payload("0.1.9", "0.1.11", "0.2.0", "0.2.1")
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertEqual(
                check_runtime_version._latest_version(pin=">=0.1.9,<0.2"),
                "0.1.11",
            )

    @requires_packaging
    def test_no_pin_picks_global_max(self):
        payload = _payload("0.1.9", "0.1.11", "0.2.0", "0.2.1")
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertEqual(
                check_runtime_version._latest_version(pin=None),
                "0.2.1",
            )

    @requires_packaging
    def test_skips_prereleases(self):
        payload = _payload("0.1.9", "0.1.11", "0.1.12a1")
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertEqual(
                check_runtime_version._latest_version(pin=">=0.1.9,<0.2"),
                "0.1.11",
            )

    @requires_packaging
    def test_no_pin_compatible_release_is_silent(self):
        payload = _payload("0.2.0", "0.2.1")
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertIsNone(
                check_runtime_version._latest_version(pin=">=0.1.9,<0.2")
            )

    @requires_packaging
    def test_invalid_pin_is_silent(self):
        """When the pin string fails to parse we can no longer enforce
        the window, so prefer silence over recommending an
        out-of-window upgrade (Codex round 2 Major)."""
        payload = _payload("0.1.9", "0.1.11", "0.2.1")
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertIsNone(
                check_runtime_version._latest_version(pin="garbage")
            )

    @requires_packaging
    def test_yanked_release_is_excluded(self):
        """If the only in-window release newer than installed is
        yanked, _latest_version must not surface it as ``latest`` —
        pip wouldn't pick it either (Codex round 2 Minor)."""
        payload = _payload(
            "0.1.9", "0.1.10", "0.1.11", yanked=("0.1.11",)
        )
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertEqual(
                check_runtime_version._latest_version(pin=">=0.1.9,<0.2"),
                "0.1.10",
            )

    def test_pypi_returns_empty_payload_is_silent(self):
        with mock.patch(
            "urllib.request.urlopen", _fake_urlopen({"info": {"version": ""}})
        ):
            self.assertIsNone(check_runtime_version._latest_version(pin=None))

    def test_pypi_json_parse_failure_is_silent(self):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()
                return False

        with mock.patch(
            "urllib.request.urlopen", lambda *a, **kw: _Resp(b"not-json")
        ):
            self.assertIsNone(check_runtime_version._latest_version(pin=None))

    def test_fallback_to_info_version_when_releases_missing_and_no_pin(self):
        payload = {"info": {"version": "1.2.3"}}
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertEqual(
                check_runtime_version._latest_version(pin=None),
                "1.2.3",
            )

    def test_fallback_is_silent_when_pin_present_but_releases_missing(self):
        """Without a releases dict we can't enforce a pin window, so
        prefer silence over recommending an out-of-window upgrade."""
        payload = {"info": {"version": "1.2.3"}}
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertIsNone(
                check_runtime_version._latest_version(pin=">=0.1.9,<0.2")
            )

    def test_with_reason_reports_offline(self):
        """_latest_version_with_reason surfaces REASON_OFFLINE when the
        host can't reach PyPI -- the channel that drives main()'s
        EXIT_UNVERIFIED + stderr diagnostic instead of a silent skip."""
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            version, reason = (
                check_runtime_version._latest_version_with_reason(pin=None)
            )
        self.assertIsNone(version)
        self.assertEqual(reason, check_runtime_version.REASON_OFFLINE)

    @requires_packaging
    def test_with_reason_success_has_no_reason(self):
        """On success the reason channel is None and the version is the
        pin-window latest (keeps the wrapper and the reason function in
        agreement)."""
        payload = _payload("0.1.9", "0.1.11", "0.2.0")
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            version, reason = (
                check_runtime_version._latest_version_with_reason(
                    pin=">=0.1.9,<0.2"
                )
            )
        self.assertEqual(version, "0.1.11")
        self.assertIsNone(reason)


class ReadPinSpecTest(unittest.TestCase):
    def test_reads_pin_from_real_pyproject(self):
        """The script lives next to ja's pyproject.toml; sanity-check
        the actual file has a pin we recognise."""
        spec = check_runtime_version._read_pin_spec()
        self.assertIsNotNone(spec)
        self.assertTrue(
            spec.startswith(">=") or spec.startswith("=="),
            f"unexpected pin shape: {spec!r}",
        )


class UpgradeCommandShapeTest(unittest.TestCase):
    """Warning text must bake the pin spec into the recommended
    upgrade command so users never get steered to an out-of-window
    release (Codex round 2 Major)."""

    def _drive_main_with_pin(self, pin):
        buf = io.StringIO()
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch.object(
            check_runtime_version, "_read_pin_spec", return_value=pin
        ), mock.patch.object(
            check_runtime_version,
            "_latest_version_with_reason",
            return_value=("0.1.11", None),
        ), mock.patch.object(sys, "stdout", buf):
            check_runtime_version.main()
        return buf.getvalue()

    def test_command_includes_pin_when_pin_known(self):
        out = self._drive_main_with_pin(">=0.1.9,<0.2")
        self.assertIn(
            "'claude-org-runtime>=0.1.9,<0.2'",
            out,
            "upgrade command must carry the pin spec",
        )

    def test_command_is_bare_when_pin_missing(self):
        out = self._drive_main_with_pin(None)
        self.assertIn("'claude-org-runtime'", out)
        self.assertNotIn("<", out.split("install --upgrade")[-1])


if __name__ == "__main__":
    unittest.main()
