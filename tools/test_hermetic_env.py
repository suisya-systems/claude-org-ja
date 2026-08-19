"""Issue #683: tests must never reach a live broker/renga daemon.

Covers the package-level hermetic env guard
(``tools/__init__.py`` + ``tools/_hermetic_env.py``) that scrubs
``ORG_BROKER_STATE_DIR`` / ``ORG_TRANSPORT`` / ``RENGA_SOCKET`` whenever
the process is a test runner, so a worker running the suite inside a live
org session cannot leak fixture peer messages (observed:
``CI_COMPLETED: PR #4242 ... repo octo/repo``) onto the live channel.

The integration test encodes the acceptance criterion directly: with a
live ``ORG_BROKER_STATE_DIR`` exported and a fake ``claude-org-runtime``
recording broker sends, a ``python -m unittest`` run of a peer-emitting
probe adds zero lines to the daemon queue — while a bypass control proves
the probe really would leak absent the guard.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import _hermetic_env  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


class ScrubUnitTests(unittest.TestCase):
    def test_scrub_removes_all_live_transport_env(self) -> None:
        with mock.patch.dict(os.environ, {
            "ORG_BROKER_STATE_DIR": "/live/.state/broker",
            "ORG_TRANSPORT": "broker",
            "RENGA_SOCKET": "/tmp/renga.sock",
            "UNRELATED_KEEP": "keep",
        }, clear=False):
            _hermetic_env.scrub_live_transport_env()
            self.assertNotIn("ORG_BROKER_STATE_DIR", os.environ)
            self.assertNotIn("RENGA_SOCKET", os.environ)
            # ORG_TRANSPORT is PINNED, not removed (Issue #941): peer_notify
            # resolves an unset value to DEFAULT_TRANSPORT == "broker", so
            # popping it would route tests at the live broker daemon.
            self.assertEqual(
                os.environ.get("ORG_TRANSPORT"),
                _hermetic_env.HERMETIC_TRANSPORT)
            self.assertEqual(os.environ.get("UNRELATED_KEEP"), "keep")

    def test_scrub_pins_a_transport_that_resolves_to_the_noop_branch(self) -> None:
        """The pinned value must be one peer_notify treats as renga.

        This is the property the hermetic guarantee rests on: with
        RENGA_SOCKET scrubbed, the renga branch short-circuits and no
        send is attempted. Asserting it through the real resolver keeps
        the guard honest if DEFAULT_TRANSPORT or the flag set changes
        again.
        """
        import peer_notify
        with mock.patch.dict(os.environ, {
            "ORG_BROKER_STATE_DIR": "/live/.state/broker",
            "ORG_TRANSPORT": "broker",
            "RENGA_SOCKET": "/tmp/renga.sock",
        }, clear=False):
            _hermetic_env.scrub_live_transport_env()
            self.assertEqual(peer_notify._resolve_transport(), "renga")
            self.assertNotIn("RENGA_SOCKET", os.environ)

    def test_both_guard_arms_leave_the_same_transport(self) -> None:
        """The ambient env must be the no-op transport under EITHER runner.

        There are two arms -- the package scrub in ``tools/__init__``
        (unittest / direct execution) and the autouse fixture in
        ``tools/conftest.py`` (pytest) -- and they must agree. They
        diverged in Issue #941: the fixture deleted ORG_TRANSPORT while
        the package scrub pinned it, and a deleted value now resolves to
        broker, so the pytest arm silently reopened the leak. This
        assertion runs under whichever runner is active and fails if
        either arm stops pinning.
        """
        self.assertEqual(os.environ.get("ORG_TRANSPORT"),
                         _hermetic_env.HERMETIC_TRANSPORT)
        self.assertNotIn("RENGA_SOCKET", os.environ)
        self.assertNotIn("ORG_BROKER_STATE_DIR", os.environ)

    def test_scrub_is_idempotent_when_unset(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in _hermetic_env.LIVE_TRANSPORT_ENV:
                os.environ.pop(name, None)
            _hermetic_env.scrub_live_transport_env()  # must not raise

    def test_running_under_test_detects_runners(self) -> None:
        argv0s = [
            "/usr/lib/python3.11/unittest/__main__.py",   # python -m unittest
            "/venv/bin/pytest",                            # pytest console script
            "/venv/lib/site-packages/pytest/__main__.py",  # python -m pytest
            "tools/test_state_db_discover.py",             # direct execution
        ]
        for argv0 in argv0s:
            with mock.patch.object(sys, "argv", [argv0]), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PYTEST_CURRENT_TEST", None)
                os.environ.pop("PYTEST_VERSION", None)
                self.assertTrue(_hermetic_env.running_under_test(), argv0)

    def test_running_under_test_true_via_pytest_env(self) -> None:
        with mock.patch.object(sys, "argv", ["tools/pr_watch.py"]), \
                mock.patch.dict(
                    os.environ,
                    {"PYTEST_CURRENT_TEST": "x::y (call)"},
                    clear=False):
            self.assertTrue(_hermetic_env.running_under_test())

    def test_running_under_test_false_for_production(self) -> None:
        for argv0 in [
            "tools/pr_watch.py",
            "tools/journal_append.py",
            "tools/check_role_configs.py",
            "tools/run_complete_on_merge.py",
            "",
        ]:
            with mock.patch.object(sys, "argv", [argv0]), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PYTEST_CURRENT_TEST", None)
                os.environ.pop("PYTEST_VERSION", None)
                self.assertFalse(_hermetic_env.running_under_test(), argv0)


class LiveDaemonHermeticIntegrationTests(unittest.TestCase):
    """Acceptance (Issue #683): a unittest run under a live
    ``ORG_BROKER_STATE_DIR`` must not enqueue anything to the daemon queue.
    """

    def setUp(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash claude-org-runtime shim is POSIX-only")
        self.tmp = Path(tempfile.mkdtemp(prefix="ja683-"))
        self.state_dir = self.tmp / "broker-live"
        self.state_dir.mkdir()
        self.queue = self.state_dir / "queue.jsonl"
        # Fake `claude-org-runtime`: record every `broker send` to the
        # queue so any leak is observable. Prepended to PATH so it wins
        # over a real runtime installed by `pip install -e .`.
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shim = self.bin / "claude-org-runtime"
        shim.write_text(textwrap.dedent(f'''
            #!/usr/bin/env bash
            if [[ "$1 $2" == "broker send" ]]; then
              echo "$*" >> "{self.queue}"
            fi
            exit 0
        ''').lstrip())
        shim.chmod(0o755)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # Env markers that make ``running_under_test()`` fire. The negative
    # control must NOT carry them: its premise is a *production*
    # invocation, and pytest exports PYTEST_CURRENT_TEST into every
    # child it spawns. Since Issue #941 ``peer_notify`` imports
    # ``tools.transport``, which runs ``tools/__init__`` and therefore
    # the guard -- so an inherited pytest marker would scrub the child
    # and the "control leaks" premise would silently stop holding.
    _TEST_RUNNER_MARKERS = ("PYTEST_CURRENT_TEST", "PYTEST_VERSION")

    def _live_env(self) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k not in self._TEST_RUNNER_MARKERS}
        env.update({
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "ORG_TRANSPORT": "broker",
            "ORG_BROKER_STATE_DIR": str(self.state_dir),
        })
        return env

    def _queue_lines(self) -> list:
        if not self.queue.exists():
            return []
        return [ln for ln in self.queue.read_text().splitlines() if ln.strip()]

    def test_negative_control_leaks_without_guard(self) -> None:
        """Sanity: bypassing the guard (bare ``peer_notify`` import, non-test
        argv) DOES leak — proves the probe + shim actually exercise the
        broker send path, so the hermetic assertion below is meaningful."""
        code = (
            "import sys; sys.path.insert(0, %r); import peer_notify; "
            "assert peer_notify.notify_peer('secretary', 'LEAKPROBE')"
            % str(REPO_ROOT / "tools")
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            env=self._live_env(), cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            len(self._queue_lines()), 1,
            f"control must leak exactly one send; queue={self._queue_lines()}",
        )

    def test_unittest_run_is_hermetic(self) -> None:
        """A ``python -m unittest`` run of a peer-emitting probe adds nothing
        to the live queue: ``tools/__init__`` scrubs the transport env at
        import, so ``notify_peer`` no-ops."""
        probe_dir = self.tmp / "probe"
        probe_dir.mkdir()
        (probe_dir / "test_leak_probe.py").write_text(textwrap.dedent(f'''
            import sys, unittest
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tools.peer_notify import notify_peer  # triggers tools/__init__

            class LeakProbe(unittest.TestCase):
                def test_emit(self):
                    # Env is scrubbed by the guard -> renga branch, no
                    # RENGA_SOCKET -> no-op -> returns False, no send.
                    notify_peer("secretary", "LEAKPROBE")
        '''))
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", str(probe_dir), "-p", "test_*.py"],
            env=self._live_env(), cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        )
        combined = proc.stdout + proc.stderr
        self.assertIn("OK", combined, f"probe did not run cleanly: {combined}")
        self.assertEqual(
            self._queue_lines(), [],
            f"LEAK: queue grew under unittest run: {self._queue_lines()!r}; "
            f"runner output={combined!r}",
        )


if __name__ == "__main__":
    unittest.main()
