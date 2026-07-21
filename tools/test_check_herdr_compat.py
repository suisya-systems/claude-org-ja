"""Unit tests for tools/check_herdr_compat.py (Issue #748).

Run with:
  python3 -m unittest tools.test_check_herdr_compat
  (from repo root, or add claude-org to PYTHONPATH)

Pure-function coverage only: no subprocess, no socket, no runtime import needed
(SUPPORTED_PROTOCOLS is injected into the report directly).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_herdr_compat as mod  # noqa: E402


class ParseVersionTests(unittest.TestCase):
    def test_parses_herdr_prefixed_output(self) -> None:
        self.assertEqual(mod.parse_version("herdr 0.7.5"), (0, 7, 5))

    def test_parses_bare_semver(self) -> None:
        self.assertEqual(mod.parse_version("0.7.4\n"), (0, 7, 4))

    def test_parses_with_dev_suffix(self) -> None:
        # herdr has a self-update path; suffix outputs must parse (Codex Minor).
        self.assertEqual(mod.parse_version("herdr 0.7.5-dev"), (0, 7, 5))

    def test_parses_with_build_metadata(self) -> None:
        self.assertEqual(mod.parse_version("0.7.2+build.9"), (0, 7, 2))

    def test_returns_none_when_absent(self) -> None:
        self.assertIsNone(mod.parse_version("no version here"))
        self.assertIsNone(mod.parse_version(""))


class DeriveProtocolTests(unittest.TestCase):
    """Local version->protocol map (SoT: herdr wire.rs / runtime herdr.py docstring)."""

    def test_070_071_map_to_14(self) -> None:
        self.assertEqual(mod._local_derive_protocol((0, 7, 0)), 14)
        self.assertEqual(mod._local_derive_protocol((0, 7, 1)), 14)

    def test_072_073_074_map_to_16(self) -> None:
        self.assertEqual(mod._local_derive_protocol((0, 7, 2)), 16)
        self.assertEqual(mod._local_derive_protocol((0, 7, 3)), 16)
        self.assertEqual(mod._local_derive_protocol((0, 7, 4)), 16)

    def test_075_and_later_patch_map_to_17(self) -> None:
        self.assertEqual(mod._local_derive_protocol((0, 7, 5)), 17)
        self.assertEqual(mod._local_derive_protocol((0, 7, 9)), 17)

    def test_outside_window_returns_none(self) -> None:
        # < 0.7 and >= 0.8 are outside the known map -> do not guess.
        self.assertIsNone(mod._local_derive_protocol((0, 6, 9)))
        self.assertIsNone(mod._local_derive_protocol((0, 8, 0)))
        self.assertIsNone(mod._local_derive_protocol((1, 0, 0)))


class ResolveStateDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get(mod.STATE_DIR_ENV)
        os.environ.pop(mod.STATE_DIR_ENV, None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(mod.STATE_DIR_ENV, None)
        else:
            os.environ[mod.STATE_DIR_ENV] = self._saved

    def test_explicit_wins(self) -> None:
        os.environ[mod.STATE_DIR_ENV] = "/env/broker"
        self.assertEqual(mod.resolve_state_dir("/flag/broker"), "/flag/broker")

    def test_env_over_default(self) -> None:
        os.environ[mod.STATE_DIR_ENV] = "/env/broker"
        self.assertEqual(mod.resolve_state_dir(None), "/env/broker")

    def test_default_when_unset(self) -> None:
        self.assertEqual(mod.resolve_state_dir(None), mod.DEFAULT_STATE_DIR)


class ReadDaemonBackendTests(unittest.TestCase):
    def test_absent_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            present, backend = mod.read_daemon_backend(d)
            self.assertFalse(present)
            self.assertIsNone(backend)

    def test_herdr_backend(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, mod.SIDECAR_NAME).write_text(
                json.dumps({"backend": "herdr", "pid": 1}), encoding="utf-8"
            )
            present, backend = mod.read_daemon_backend(d)
            self.assertTrue(present)
            self.assertEqual(backend, "herdr")

    def test_tmux_backend(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, mod.SIDECAR_NAME).write_text(
                json.dumps({"backend": "tmux"}), encoding="utf-8"
            )
            present, backend = mod.read_daemon_backend(d)
            self.assertTrue(present)
            self.assertEqual(backend, "tmux")

    def test_null_backend_no_nudge(self) -> None:
        # --no-nudge daemon records backend=null; present but not herdr.
        with tempfile.TemporaryDirectory() as d:
            Path(d, mod.SIDECAR_NAME).write_text(
                json.dumps({"backend": None}), encoding="utf-8"
            )
            present, backend = mod.read_daemon_backend(d)
            self.assertTrue(present)
            self.assertIsNone(backend)

    def test_corrupt_sidecar_treated_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, mod.SIDECAR_NAME).write_text("{not json", encoding="utf-8")
            present, backend = mod.read_daemon_backend(d)
            self.assertFalse(present)
            self.assertIsNone(backend)


def _report(**kw) -> mod.HerdrCompatReport:
    r = mod.HerdrCompatReport()
    for k, v in kw.items():
        setattr(r, k, v)
    return r


class DecideTests(unittest.TestCase):
    """decide() is pure: exercise every exit-code branch without I/O."""

    def test_ping_in_window_compatible(self) -> None:
        r = _report(
            daemon_backend="herdr", ping_protocol=17,
            derived_protocol=17, runtime_supported=[14, 16, 17],
        )
        mod.decide(r)
        self.assertEqual(r.exit_code, 0)
        self.assertEqual(r.effective_protocol, 17)
        self.assertEqual(r.effective_source, "socket ping (daemon)")

    def test_ping_outside_window_incompatible(self) -> None:
        r = _report(
            daemon_backend="herdr", ping_protocol=18,
            derived_protocol=17, runtime_supported=[14, 16, 17],
        )
        mod.decide(r)
        self.assertEqual(r.exit_code, 1)
        self.assertEqual(r.effective_protocol, 18)
        self.assertTrue(any("OUTSIDE" in f for f in r.failures))
        self.assertTrue(r.remediation)

    def test_ping_preferred_over_binary_and_warns_on_mismatch(self) -> None:
        # Authoritative daemon (18) disagrees with PATH binary (17): ping wins,
        # and the PATH!=daemon divergence is surfaced (Codex Major).
        r = _report(
            daemon_backend="herdr", ping_protocol=18,
            derived_protocol=17, runtime_supported=[14, 16, 17],
        )
        mod.decide(r)
        self.assertEqual(r.effective_protocol, 18)
        self.assertTrue(any("NOT the daemon" in w for w in r.warnings))

    def test_fallback_to_binary_when_no_ping(self) -> None:
        r = _report(
            daemon_backend="herdr", ping_protocol=None,
            derived_protocol=16, ping_error="socket unreachable",
            runtime_supported=[14, 16, 17],
        )
        mod.decide(r)
        self.assertEqual(r.exit_code, 0)
        self.assertEqual(r.effective_protocol, 16)
        self.assertIn("local version->protocol map", r.effective_source)
        self.assertTrue(any("socket ping unavailable" in w for w in r.warnings))

    def test_fallback_binary_outside_window_incompatible(self) -> None:
        r = _report(
            daemon_backend="herdr", ping_protocol=None,
            derived_protocol=14, runtime_supported=[16, 17],
        )
        mod.decide(r)
        self.assertEqual(r.exit_code, 1)

    def test_no_signal_unverified(self) -> None:
        # backend herdr, neither ping nor binary usable -> exit 2 fatal.
        r = _report(
            daemon_backend="herdr", ping_protocol=None,
            derived_protocol=None, ping_error="no ping",
            runtime_supported=[14, 16, 17],
        )
        mod.decide(r)
        self.assertEqual(r.exit_code, 2)
        self.assertIsNone(r.effective_protocol)

    def test_runtime_import_failure_unverified(self) -> None:
        r = _report(
            daemon_backend="herdr", ping_protocol=17,
            runtime_supported=None,
            runtime_import_error="ModuleNotFoundError: no runtime",
        )
        mod.decide(r)
        self.assertEqual(r.exit_code, 2)
        self.assertTrue(any("SUPPORTED_PROTOCOLS" in f for f in r.failures))


class RunGateSkipTests(unittest.TestCase):
    """Skip branches (exit 0) short-circuit before any herdr probing."""

    def test_no_sidecar_skips(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            r = mod.run_gate(d, mod.DEFAULT_PING_TIMEOUT, skip_ping=True)
            self.assertEqual(r.exit_code, 0)
            self.assertTrue(r.skipped)
            self.assertIn("not applicable", r.skip_reason)

    def test_non_herdr_backend_skips(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, mod.SIDECAR_NAME).write_text(
                json.dumps({"backend": "tmux"}), encoding="utf-8"
            )
            r = mod.run_gate(d, mod.DEFAULT_PING_TIMEOUT, skip_ping=True)
            self.assertEqual(r.exit_code, 0)
            self.assertTrue(r.skipped)
            self.assertEqual(r.daemon_backend, "tmux")

    def test_null_backend_skips(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, mod.SIDECAR_NAME).write_text(
                json.dumps({"backend": None}), encoding="utf-8"
            )
            r = mod.run_gate(d, mod.DEFAULT_PING_TIMEOUT, skip_ping=True)
            self.assertEqual(r.exit_code, 0)
            self.assertTrue(r.skipped)


class EmitTests(unittest.TestCase):
    def _capture(self, fn, report) -> str:
        buf = io.StringIO()
        saved = sys.stdout
        sys.stdout = buf
        try:
            fn(report)
        finally:
            sys.stdout = saved
        return buf.getvalue()

    def test_json_has_stable_shape(self) -> None:
        r = _report(
            daemon_backend="herdr", ping_ok=True, ping_protocol=17,
            ping_version="0.7.5", runtime_supported=[14, 16, 17],
            effective_protocol=17, effective_source="socket ping (daemon)",
        )
        doc = json.loads(self._capture(mod.emit_json, r))
        for key in (
            "exit_code", "skipped", "daemon_backend", "installed_herdr",
            "daemon_ping", "runtime_supported_protocols", "effective_protocol",
        ):
            self.assertIn(key, doc)
        self.assertIn("derived_protocol", doc["installed_herdr"])
        self.assertIn("protocol", doc["daemon_ping"])

    def test_text_skip_is_ascii(self) -> None:
        r = mod.HerdrCompatReport()
        r.skipped = True
        r.skip_reason = "no broker daemon"
        r.state_dir = ".state/broker"
        out = self._capture(mod.emit_text, r)
        self.assertIn("SKIP", out)
        # CLI output must stay cp932-safe (ASCII only) per repo Windows rule.
        out.encode("ascii")

    def test_text_incompatible_is_ascii(self) -> None:
        r = _report(
            daemon_backend="herdr", ping_ok=True, ping_protocol=18,
            ping_version="0.8.0", derived_protocol=17,
            runtime_supported=[14, 16, 17],
        )
        mod.decide(r)
        out = self._capture(mod.emit_text, r)
        self.assertIn("INCOMPATIBLE", out)
        out.encode("ascii")


if __name__ == "__main__":
    unittest.main()
