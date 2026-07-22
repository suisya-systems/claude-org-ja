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
    def setUp(self):
        # Neutralise the PEP 610 local-install short-circuit for the
        # generic main() paths: whether the runtime happens to be
        # installed editable/local in the test venv must not change these
        # outcomes. The local-install branch has its own test class below.
        patcher = mock.patch.object(
            check_runtime_version, "_direct_url_local_reason", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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
            check_runtime_version, "_direct_url_local_reason", return_value=None
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


class _FakeDist:
    """Minimal stand-in for importlib.metadata.Distribution exposing
    only the read_text used by _direct_url_local_reason."""

    def __init__(self, direct_url_json: str | None):
        self._direct_url_json = direct_url_json

    def read_text(self, name: str):
        if name == "direct_url.json":
            return self._direct_url_json
        return None


class DirectUrlLocalInstallTest(unittest.TestCase):
    """PEP 610 direct_url.json detection (Issue #747): a file:// path /
    VCS / editable install advertises a version that need not match any
    PyPI release, so it must be surfaced as unverified rather than read
    as up-to-date. Background: on 2026-07-22 a file:// install was
    mis-reported as ``最新・drift なし``."""

    def _reason_for(self, direct_url_obj):
        raw = None if direct_url_obj is None else json.dumps(direct_url_obj)
        with mock.patch(
            "importlib.metadata.distribution",
            return_value=_FakeDist(raw),
        ):
            return check_runtime_version._direct_url_local_reason()

    def test_file_url_is_local(self):
        reason = self._reason_for(
            {"url": "file:///home/x/claude-org-runtime", "dir_info": {}}
        )
        self.assertIsNotNone(reason)
        self.assertIn("file://", reason)

    def test_vcs_install_is_local(self):
        reason = self._reason_for(
            {
                "url": "https://github.com/o/claude-org-runtime.git",
                "vcs_info": {"vcs": "git", "commit_id": "abc123"},
            }
        )
        self.assertIsNotNone(reason)
        self.assertIn("VCS", reason)
        self.assertIn("git", reason)

    def test_editable_install_is_local(self):
        reason = self._reason_for(
            {"url": "file:///home/x/src", "dir_info": {"editable": True}}
        )
        self.assertIsNotNone(reason)
        self.assertIn("editable", reason)

    def test_editable_takes_precedence_over_bare_file(self):
        """An editable install also has a file:// url; the label should
        name it editable (the more specific source), not classify it as
        a plain ``file:// install``."""
        reason = self._reason_for(
            {"url": "file:///home/x/src", "dir_info": {"editable": True}}
        )
        self.assertTrue(reason.startswith("editable install"))
        self.assertNotIn("file:// install", reason)

    def test_https_archive_direct_url_is_not_local(self):
        """A direct URL that is a plain https archive (not file://, not
        VCS, not editable) is left to the normal PyPI comparison path."""
        reason = self._reason_for(
            {
                "url": "https://example.com/claude-org-runtime-0.1.2.tar.gz",
                "archive_info": {},
            }
        )
        self.assertIsNone(reason)

    def test_no_direct_url_is_not_local(self):
        """A normal PyPI/index install writes no direct_url.json."""
        self.assertIsNone(self._reason_for(None))

    def test_malformed_direct_url_json_is_not_local(self):
        with mock.patch(
            "importlib.metadata.distribution",
            return_value=_FakeDist("not-json"),
        ):
            self.assertIsNone(
                check_runtime_version._direct_url_local_reason()
            )

    def test_package_not_found_is_not_local(self):
        from importlib.metadata import PackageNotFoundError

        with mock.patch(
            "importlib.metadata.distribution",
            side_effect=PackageNotFoundError(),
        ):
            self.assertIsNone(
                check_runtime_version._direct_url_local_reason()
            )


class MainLocalInstallTest(unittest.TestCase):
    """main() must short-circuit a local install to EXIT_UNVERIFIED with
    a stderr diagnostic BEFORE touching PyPI, keeping stdout empty so the
    spliceable drift-line contract is preserved."""

    def _run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(
            sys, "stderr", err
        ):
            code = check_runtime_version.main()
        return code, out.getvalue(), err.getvalue()

    def test_local_install_is_unverified_and_skips_pypi(self):
        with mock.patch.object(
            check_runtime_version, "_installed_version", return_value="0.1.2"
        ), mock.patch.object(
            check_runtime_version,
            "_direct_url_local_reason",
            return_value="file:// install (file:///home/x/rt)",
        ), mock.patch.object(
            check_runtime_version, "_latest_version_with_reason"
        ) as latest_mock:
            code, out, err = self._run_main()
        self.assertEqual(code, check_runtime_version.EXIT_UNVERIFIED)
        # stdout stays clean (no drift line); reason on stderr only.
        self.assertEqual(out, "")
        self.assertIn("local install", err)
        self.assertIn("照合不能", err)
        # The verdict doesn't depend on the network: PyPI is never hit.
        latest_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
