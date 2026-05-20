"""Unit tests for tools/check_runtime_version.py (Issue #472).

The script is invoked by /org-start Block C2. It must:

* print one warning line when installed != latest-in-pin-window
* print nothing (and exit 0) when installed == latest
* print nothing on every "can't tell" branch — package missing,
  PyPI unreachable, JSON parse failure, no pin-compatible release
* respect ja's pin window declared in pyproject.toml so the warning
  never steers users to an out-of-window upgrade (Codex review,
  Issue #472)
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


def _payload(*versions: str) -> dict:
    """Build a minimally-PyPI-shaped JSON payload from the given
    release versions (newest last, as PyPI usually sorts)."""
    return {
        "info": {"version": versions[-1] if versions else ""},
        "releases": {v: [{"yanked": False}] for v in versions},
    }


class MainCliTest(unittest.TestCase):
    def _run_main(self) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            code = check_runtime_version.main()
        return code, buf.getvalue()

    def test_drift_prints_one_warning_line(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.11"
        ):
            code, out = self._run_main()
        self.assertEqual(code, 0)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("[runtime drift]", lines[0])
        self.assertIn("installed=0.1.2", lines[0])
        self.assertIn("latest=0.1.11", lines[0])

    def test_match_is_silent(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.11"
        ), mock.patch.object(
            check_runtime_version, "_latest_version", return_value="0.1.11"
        ):
            code, out = self._run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_package_not_installed_is_silent(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value=None
        ), mock.patch.object(
            check_runtime_version, "_latest_version"
        ) as latest_mock:
            code, out = self._run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        latest_mock.assert_not_called()

    def test_offline_is_silent(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            code, out = self._run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


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
    def test_invalid_pin_falls_back_to_global_max(self):
        payload = _payload("0.1.9", "0.1.11")
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertEqual(
                check_runtime_version._latest_version(pin="garbage"),
                "0.1.11",
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


class ReadPinSpecTest(unittest.TestCase):
    def test_reads_pin_from_real_pyproject(self):
        """The script lives next to ja's pyproject.toml; sanity-check
        the actual file has a pin we recognise."""
        spec = check_runtime_version._read_pin_spec()
        self.assertIsNotNone(spec)
        self.assertIn("claude-org-runtime", "claude-org-runtime")  # tautology
        self.assertTrue(
            spec.startswith(">=") or spec.startswith("=="),
            f"unexpected pin shape: {spec!r}",
        )


if __name__ == "__main__":
    unittest.main()
