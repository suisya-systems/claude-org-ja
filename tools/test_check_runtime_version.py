"""Unit tests for tools/check_runtime_version.py (Issue #472).

The script is invoked by /org-start Block C2. It must:

* print one warning line when installed != latest
* print nothing (and exit 0) when installed == latest
* print nothing on every "can't tell" branch — package missing,
  PyPI unreachable, JSON parse failure — because /org-start treats
  silence as "no drift to report"
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


def _fake_urlopen(payload: dict):
    """Return a context-manager-compatible stand-in for urlopen()."""
    body = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    return lambda *a, **kw: _Resp(body)


class CheckRuntimeVersionTest(unittest.TestCase):
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

    def test_pypi_returns_garbage_is_silent(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch(
            "urllib.request.urlopen", _fake_urlopen({"info": {"version": ""}})
        ):
            code, out = self._run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_pypi_json_parse_failure_is_silent(self):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()
                return False

        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch(
            "urllib.request.urlopen",
            lambda *a, **kw: _Resp(b"not-json"),
        ):
            code, out = self._run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_latest_version_extracted_from_pypi_payload(self):
        """End-to-end smoke: _latest_version returns the payload's
        info.version when urlopen succeeds."""
        payload = {"info": {"version": "9.9.9"}, "releases": {}}
        with mock.patch("urllib.request.urlopen", _fake_urlopen(payload)):
            self.assertEqual(check_runtime_version._latest_version(), "9.9.9")


if __name__ == "__main__":
    unittest.main()
