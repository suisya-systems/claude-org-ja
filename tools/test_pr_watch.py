"""Unit tests for tools/pr_watch.py (Issue #204, Issue #224, M4 Issue #267).

Mocks the gh CLI subprocess via monkey-patching so the suite stays
hermetic. M4 (Issue #267) routes ``ci_completed`` to the DB events
table; the test helper ``_read_ci_event`` reads back via sqlite3.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_watch  # noqa: E402


# Issue #398 / pr-watch-race-fix: tools/conftest.py auto-scrubs
# ``RENGA_SOCKET`` when this suite runs under pytest. Direct
# ``python tools/test_pr_watch.py`` / ``python -m unittest`` invocations
# bypass conftest entirely, so we re-assert isolation at the unittest
# layer too: setUpModule deletes the env var unconditionally so
# ``tools.peer_notify.notify_peer`` short-circuits before reaching the
# real ``renga mcp-peer`` subprocess. Without this guard,
# ``test_watch_for_merge_timeout_records_event`` and similar tests that
# invoke ``pr_watch.main`` / ``_watch_for_merge`` without an explicit
# ``_notify_peer`` mock could leak fake CI / merge messages onto the
# live peer channel (observed against PR #555 from a parallel worker).
_RENGA_SOCKET_BEFORE: "str | None" = None


def setUpModule() -> None:  # noqa: N802 (unittest hook)
    global _RENGA_SOCKET_BEFORE
    _RENGA_SOCKET_BEFORE = os.environ.pop("RENGA_SOCKET", None)


def tearDownModule() -> None:  # noqa: N802 (unittest hook)
    if _RENGA_SOCKET_BEFORE is not None:
        os.environ["RENGA_SOCKET"] = _RENGA_SOCKET_BEFORE


def _assert_peer_isolation() -> None:
    """Defense-in-depth: reject the test if the runtime would spawn renga.

    Even with ``RENGA_SOCKET`` cleared, a regression in
    ``peer_notify.notify_peer`` that bypasses the env-guard could still
    spawn a subprocess. Tests that exercise the unmocked ``_notify_peer``
    path call this helper to fail fast instead of leaking.
    """
    assert "RENGA_SOCKET" not in os.environ, (
        "test isolation breach: RENGA_SOCKET is set during a test that "
        "does not mock _notify_peer; peer messages would leak onto the "
        "live channel"
    )


def _read_ci_event(db_path: Path) -> dict:
    """Return the (single) ci_completed event from the DB as a payload dict
    flattened with the ``ts`` / ``event`` keys the tests expect."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT occurred_at, kind, payload_json FROM events "
            "WHERE kind = 'ci_completed' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        raise AssertionError(f"expected 1 ci_completed row, got {len(rows)}")
    row = rows[0]
    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    out = dict(payload)
    out["event"] = row["kind"]
    out["ts"] = row["occurred_at"]
    return out


def _count_ci_events(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'ci_completed'"
        ).fetchone()[0])
    finally:
        conn.close()


class ClassifyTests(unittest.TestCase):
    """Fallback classifier (used only when JSON probe is unavailable).

    Per `gh help exit-codes`, exit 8 is "Checks pending" — NOT failure.
    Issue #224 corrects the prior mapping.
    """

    def test_zero_is_passed(self) -> None:
        self.assertEqual(pr_watch._classify(0), "passed")

    def test_eight_is_incomplete(self) -> None:
        # gh exit 8 = "Checks pending", per gh's help text.
        self.assertEqual(pr_watch._classify(8), "incomplete")

    def test_two_is_canceled(self) -> None:
        self.assertEqual(pr_watch._classify(2), "canceled")

    def test_other_nonzero_is_incomplete(self) -> None:
        # Conservative: treat unknown gh errors as incomplete rather
        # than libelling the PR as failed.
        self.assertEqual(pr_watch._classify(1), "incomplete")
        self.assertEqual(pr_watch._classify(127), "incomplete")


def _make_fake_run(
    watch_exit: int = 0,
    checks_json: "list[dict] | None" = None,
    checks_raises: "Exception | None" = None,
    checks_json_exit: "int | None" = None,
):
    """Build a `subprocess.run` stub matching the call sites in pr_watch.

    Recognized commands:

    * ``gh pr view ... --json number`` → success (PR exists probe)
    * ``gh pr checks <pr> --json ...`` → ``checks_json`` (or raise)
    * ``gh pr checks <pr> --watch ...`` → returncode = ``watch_exit``
    """
    if checks_json is None:
        checks_json = [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}]

    # gh exits non-zero (1) when at least one check is failing, and 8
    # when checks are still pending — but in both cases it still writes
    # the requested JSON. Mirror that here so the tests exercise the
    # real protocol, not an idealized one.
    def _gh_exit_for_checks(payload):
        if checks_json_exit is not None:
            return checks_json_exit
        for chk in payload:
            b = (chk.get("bucket") or "").lower()
            if b in ("fail", "cancel"):
                return 1
            if b == "pending":
                return 8
        return 0

    def fake_run(cmd, *args, **kwargs):
        if "view" in cmd and "--json" in cmd and "number" in cmd:
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        if "checks" in cmd and "--json" in cmd:
            if checks_raises is not None:
                raise checks_raises
            return mock.Mock(
                returncode=_gh_exit_for_checks(checks_json),
                stdout=json.dumps(checks_json),
                stderr="",
            )
        # The watched run.
        return mock.Mock(returncode=watch_exit)

    return fake_run


def _gh_exit_for_payload(payload):
    """Mirror gh's exit-code protocol for `gh pr checks --json`.

    ``1`` if any bucket is fail/cancel, ``8`` if any bucket is pending,
    ``0`` otherwise. Used by the stateful stub below to keep retry-loop
    fixtures aligned with real gh behavior.
    """
    for chk in payload or []:
        b = (chk.get("bucket") or "").lower()
        if b in ("fail", "cancel"):
            return 1
        if b == "pending":
            return 8
    return 0


def _make_stateful_fake_run(
    watch_exit: int,
    checks_sequence: "list[list[dict]]",
):
    """Like :func:`_make_fake_run` but consumes one entry of
    ``checks_sequence`` per ``gh pr checks --json`` call.

    Designed for Issue #413 retry-loop regression fixtures: the first
    fetch can return ``[]`` (transient empty) while a later fetch
    returns the actual verdict. The last entry is reused if the
    resolver fetches more than ``len(checks_sequence)`` times (a
    convenient cap so callers don't have to over-pad the sequence).
    """
    idx = {"i": 0}

    def fake_run(cmd, *args, **kwargs):
        if "view" in cmd and "--json" in cmd and "number" in cmd:
            return mock.Mock(returncode=0, stdout="{}", stderr="")
        if "checks" in cmd and "--json" in cmd:
            i = idx["i"]
            if i < len(checks_sequence) - 1:
                idx["i"] = i + 1
            payload = checks_sequence[i] if i < len(checks_sequence) else checks_sequence[-1]
            return mock.Mock(
                returncode=_gh_exit_for_payload(payload),
                stdout=json.dumps(payload),
                stderr="",
            )
        return mock.Mock(returncode=watch_exit)

    return fake_run


class ArgFormTests(unittest.TestCase):
    """Both `--pr <n>` and the legacy positional form must parse identically."""

    def setUp(self) -> None:
        # Belt-and-braces: the module-level setUp already cleared
        # RENGA_SOCKET, but if a pytest plugin or an earlier test
        # restored it we want to fail loudly rather than leak.
        _assert_peer_isolation()

    def _run(self, argv: "list[str]") -> "tuple[int, dict]":
        fake_run = _make_fake_run(watch_exit=0)

        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                # Issue #317: existing CI-only tests opt out of the
                # post-CI merge-watch loop; that path is exercised
                # separately in MergeWatchTests below.
                # Issue #317 round 3: merge-watch is now off by default,
                # so existing CI-only tests don't need an opt-out flag.
                rc = pr_watch.main(argv)
            rec = _read_ci_event(journal)
            return rc, rec

    def test_long_form(self) -> None:
        rc, rec = self._run(["--pr", "42", "--repo", "octo/repo"])
        self.assertEqual(rc, 0)
        self.assertEqual(rec["pr"], 42)

    def test_positional_form(self) -> None:
        rc, rec = self._run(["42", "--repo", "octo/repo"])
        self.assertEqual(rc, 0)
        self.assertEqual(rec["pr"], 42)

    def test_both_forms_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            pr_watch.main(["7", "--pr", "9", "--repo", "octo/repo"])


class JournalEmitTests(unittest.TestCase):
    def setUp(self) -> None:
        _assert_peer_isolation()

    def _run(
        self,
        tmp_journal: Path,
        gh_exit: int,
        checks_json: "list[dict] | None" = None,
        checks_raises: "Exception | None" = None,
    ) -> int:
        fake_run = _make_fake_run(
            watch_exit=gh_exit,
            checks_json=checks_json,
            checks_raises=checks_raises,
        )
        # `_notify_peer` mocked at the seam so even a regression in
        # peer_notify's env-guard cannot leak onto the live channel.
        with mock.patch.object(pr_watch, "JOURNAL_PATH", tmp_journal), \
             mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
             mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(pr_watch.time, "monotonic", side_effect=[100.0, 142.0]):
            return pr_watch.main([
                "--pr", "205", "--repo", "octo/repo", "--interval", "5",
            ])

    def test_passed_emits_ci_completed(self) -> None:
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            self._run(journal, gh_exit=0)
            self.assertEqual(_count_ci_events(journal), 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["event"], "ci_completed")
            self.assertEqual(rec["pr"], 205)
            self.assertEqual(rec["repo"], "octo/repo")
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(rec["duration_sec"], 42)
            self.assertIn("ts", rec)

    def test_failed_status_from_check_bucket(self) -> None:
        """A `fail` bucket in the JSON probe → status=failed."""
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            self._run(
                journal,
                gh_exit=8,
                checks_json=[
                    {"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                    {"name": "test", "state": "COMPLETED", "bucket": "fail"},
                ],
            )
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "failed")

    def test_transient_gh_error_is_not_failed(self) -> None:
        """Issue #224: gh exit 1 with all checks `pass` must classify as passed.

        Regression: the old code conflated any non-zero exit with CI
        failure, so a transient error in `gh pr checks --watch` (e.g.
        a brief network blip) would be journaled as ``status=failed``
        even when CI itself was green.
        """
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            self._run(
                journal,
                gh_exit=1,
                checks_json=[
                    {"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                    {"name": "test", "state": "COMPLETED", "bucket": "pass"},
                ],
            )
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")

    def test_pending_check_after_retry_exhaustion_is_incomplete(self) -> None:
        """Issue #413: a still-running check now drives the retry
        loop; only after the retry budget is exhausted do we record
        a final ``incomplete`` event.

        Pre-#413, this test asserted ``status=incomplete`` on the
        first ``pending`` observation. That path is exactly the
        race the fix addresses — a pending bucket must be retried,
        not journaled immediately. We keep the bucket→status
        coverage by exercising the exhaustion path here (the
        per-bucket mapping itself is unit-tested in
        :class:`ClassifyFromChecksTests`).
        """
        # Always-pending stub: every fetch returns the same payload.
        fake_run = _make_fake_run(
            watch_exit=8,  # gh exits 8 ("Checks pending") with this payload
            checks_json=[
                {"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                {"name": "deploy", "state": "IN_PROGRESS", "bucket": "pending"},
            ],
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[100.0, 100.5, 9999.0, 9999.5]):
                pr_watch.main([
                    "--pr", "205", "--repo", "octo/repo", "--interval", "5",
                ])
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "incomplete")

    def test_transient_empty_then_passes_emits_one_final_event(self) -> None:
        """Issue #413 regression-prevention fixture.

        Repro: ``gh pr checks --watch`` returns immediately on a
        freshly created PR (no check rows have propagated yet), so
        the first ``gh pr checks --json`` call returns ``[]``. The
        legacy code wrote ``ci_completed(status=incomplete,
        duration_sec=1)`` as the FINAL event — observed in a single
        session against PRs #411 / #14 / #15 / #416. The retry loop
        must absorb the transient empty and emit exactly one
        ``ci_completed`` event whose status is the actually-final
        verdict (``passed`` here).
        """
        fake_run = _make_stateful_fake_run(
            watch_exit=0,
            checks_sequence=[
                [],  # transient empty (first fetch after watch returns)
                [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}],
            ],
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[100.0, 100.5, 101.0, 142.0]):
                rc = pr_watch.main([
                    "--pr", "413", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 0)
            # Exactly one final event — not one per observation.
            self.assertEqual(_count_ci_events(journal), 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            # Duration is measured from watch start to final verdict
            # (post-retry), not to the first transient observation.
            self.assertEqual(rec["duration_sec"], 42)

    def test_transient_pending_then_passes_returns_zero(self) -> None:
        """Codex round-1 Major: the script's exit code must follow the
        final verdict, not gh's initial ``--watch`` exit code.

        Repro: gh exits 8 (pending) on the first observation, then a
        retry resolves to passed. The caller checks ``$?`` and must
        see 0 — otherwise a CI-passed PR is mistaken for
        ``incomplete`` by every shell consumer.
        """
        fake_run = _make_stateful_fake_run(
            watch_exit=8,
            checks_sequence=[
                [{"name": "ci", "state": "IN_PROGRESS", "bucket": "pending"}],
                [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}],
            ],
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 0.1, 0.2, 1.0]):
                rc = pr_watch.main([
                    "--pr", "417", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 0)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")

    def test_transient_pending_then_fails_emits_one_final_event(self) -> None:
        """Symmetric coverage: a real CI failure that arrives after a
        pending observation must still record exactly one final
        ``ci_completed(status=failed)`` event.
        """
        fake_run = _make_stateful_fake_run(
            watch_exit=8,  # gh exits 8 on the initial pending observation
            checks_sequence=[
                [{"name": "ci", "state": "IN_PROGRESS", "bucket": "pending"}],
                [{"name": "ci", "state": "COMPLETED", "bucket": "fail"}],
            ],
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 0.1, 0.2, 7.0]):
                pr_watch.main([
                    "--pr", "414", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(_count_ci_events(journal), 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "failed")

    def test_retry_budget_exhausted_records_incomplete_once(self) -> None:
        """When every retry observation is still empty/pending, the
        budget eventually runs out. We then write a SINGLE final
        ``ci_completed(status=incomplete)`` event whose
        ``duration_sec`` reflects the full observation window — not
        a misleading 1s.
        """
        fake_run = _make_stateful_fake_run(
            watch_exit=0,
            checks_sequence=[[]],  # always empty
        )
        # monotonic side_effect:
        #   1st: started = 0.0
        #   2nd: deadline = monotonic() + budget at top of retry loop
        #   3rd: while-loop check (returns past deadline → exit loop)
        #   4th: duration = monotonic() - started
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 0.5, 9999.0, 9999.5]):
                pr_watch.main([
                    "--pr", "415", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(_count_ci_events(journal), 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "incomplete")

    def test_failed_check_with_gh_json_exit_1(self) -> None:
        """gh exits 1 when a check failed but still emits JSON.

        Regression: the previous implementation only trusted the JSON
        when gh exited 0 or 8, so a real CI failure (gh exit 1 + valid
        JSON listing a `fail` bucket) would be discarded and fall
        through to the exit-code classifier — defeating Issue #224(a).
        """
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            self._run(
                journal,
                gh_exit=1,  # watch loop saw the failure too
                checks_json=[
                    {"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                    {"name": "test", "state": "COMPLETED", "bucket": "fail"},
                ],
                # `_gh_exit_for_checks` will emit 1 for this payload
                # (failure present), matching real gh behavior.
            )
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "failed")

    def test_gh_exit_2_is_canceled(self) -> None:
        """gh exit 2 = cancellation. Must NOT be overwritten by JSON probe.

        Regression: an earlier draft only honored Python-side
        KeyboardInterrupt, so a Ctrl-C delivered to gh itself (exit 2)
        would have been re-classified as passed/failed/incomplete by
        the JSON probe. The journal must reflect cancellation so the
        secretary can distinguish "user aborted" from "CI verdict".
        """
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            self._run(
                journal,
                gh_exit=2,
                checks_json=[{"name": "ci", "state": "COMPLETED", "bucket": "pass"}],
            )
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "canceled")

    def test_json_probe_failure_falls_back_to_exit_code(self) -> None:
        """If every `gh pr checks --json` probe fails (binary missing /
        malformed stdout), the resolver retries within its budget and
        only after exhaustion does it fall back to the exit-code
        classifier.

        Codex round-2 Major: a transient JSON parse failure used to
        short-circuit the retry budget and bypass the resolver
        entirely; the fix unifies the retry path so probe failures
        and ``incomplete`` observations are both retryable.
        """
        # Always-FileNotFoundError stub: `_fetch_checks` returns None
        # for every call.
        fake_run = _make_fake_run(
            watch_exit=0,
            checks_raises=FileNotFoundError("gh missing"),
        )
        # monotonic side_effect:
        #   1st: started = 100.0
        #   2nd: deadline = monotonic() + budget (set on iter 1)
        #   3rd: deadline check (already past → exit loop)
        #   4th: duration = monotonic() - started
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[100.0, 100.5, 9999.0, 9999.5]):
                pr_watch.main([
                    "--pr", "205", "--repo", "octo/repo", "--interval", "5",
                ])
            rec = _read_ci_event(journal)
            # exit 0 → passed via _classify fallback (post-exhaustion).
            self.assertEqual(rec["status"], "passed")

    def test_transient_probe_failure_then_passes(self) -> None:
        """Codex round-2 Major regression test: a single transient
        unparseable JSON response (e.g. ``gh`` returned empty stdout
        for one observation) must NOT short-circuit the retry budget.

        We simulate one ``FileNotFoundError`` (so ``_fetch_checks``
        returns ``None``) then a passing observation. The pre-fix
        code would have called ``_classify(exit_code=8)`` →
        ``incomplete`` and recorded that as the final event,
        re-introducing the Issue #413 race when a transient probe
        failure coincided with ``gh exit 8``.
        """
        call_state = {"i": 0}

        def fake_run(cmd, *args, **kwargs):
            if "view" in cmd and "--json" in cmd and "number" in cmd:
                return mock.Mock(returncode=0, stdout="{}", stderr="")
            if "checks" in cmd and "--json" in cmd:
                i = call_state["i"]
                call_state["i"] = i + 1
                if i == 0:
                    raise FileNotFoundError("transient")
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        [{"name": "ci", "state": "COMPLETED",
                          "bucket": "pass"}]
                    ),
                    stderr="",
                )
            return mock.Mock(returncode=8)  # gh --watch exit 8 (pending)

        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 0.1, 0.2, 1.0]):
                rc = pr_watch.main([
                    "--pr", "418", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 0)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(_count_ci_events(journal), 1)


class ClassifyFromChecksTests(unittest.TestCase):
    def test_all_pass(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "pass"}]
            ),
            "passed",
        )

    def test_skipping_counts_as_passed(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "skipping"}]
            ),
            "passed",
        )

    def test_fail_bucket_is_failed(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "fail"}]
            ),
            "failed",
        )

    def test_cancel_bucket_is_failed(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "cancel"}]
            ),
            "failed",
        )

    def test_pending_is_incomplete(self) -> None:
        self.assertEqual(
            pr_watch._classify_from_checks(
                [{"bucket": "pass"}, {"bucket": "pending"}]
            ),
            "incomplete",
        )

    def test_empty_is_incomplete(self) -> None:
        self.assertEqual(pr_watch._classify_from_checks([]), "incomplete")

    def test_unknown_bucket_is_incomplete(self) -> None:
        # Conservative: unrecognized bucket → don't claim "passed".
        self.assertEqual(
            pr_watch._classify_from_checks([{"bucket": "mystery"}]),
            "incomplete",
        )

    def test_case_insensitive_bucket(self) -> None:
        # gh emits lowercase, but be defensive.
        self.assertEqual(
            pr_watch._classify_from_checks([{"bucket": "PASS"}]),
            "passed",
        )


class PowerShellInterpreterProbeTests(unittest.TestCase):
    """Issue #224 (b) / pr-watch-race-fix: pr-watch.ps1 must reject
    Pythons that fail the combined ``Python 3 + stdlib sqlite3``
    probe.

    Originally the probe required ``core_harness.audit``; that import
    was retired during M4 (the events table replaced
    ``.state/journal.jsonl``) and the probe in ``tools/pr-watch.ps1``
    now imports stdlib ``sqlite3`` instead — the actual external
    dependency of ``tools.state_db``.

    These tests exercise the actual PowerShell script via pwsh; they
    are skipped on hosts where pwsh isn't available (e.g. minimal CI
    images). The probe logic itself is small and self-contained, so
    we extract just the ``Test-Interpreter`` function and call it
    with synthetic shim interpreters.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not cls.pwsh:
            raise unittest.SkipTest("pwsh/powershell not available")
        cls.script = (
            Path(__file__).resolve().parent / "pr-watch.ps1"
        )
        if not cls.script.exists():
            raise unittest.SkipTest("pr-watch.ps1 not found")

    def _run_probe(self, shim_body: str) -> bool:
        """Write a fake interpreter shim, source Test-Interpreter, return result."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            if os.name == "nt":
                shim = tdp / "fakepy.cmd"
                shim.write_text(shim_body, encoding="ascii")
                exe_arg = str(shim)
            else:
                shim = tdp / "fakepy"
                shim.write_text("#!/usr/bin/env bash\n" + shim_body, encoding="ascii")
                shim.chmod(0o755)
                exe_arg = str(shim)
            # We can't dot-source pr-watch.ps1 directly (it has a mandatory
            # -PR param that would error out), so extract just the
            # Test-Interpreter function block via regex and Invoke-Expression.
            extract = (
                "$src = Get-Content -Raw '" + str(self.script).replace("'", "''") + "'; "
                "if ($src -match '(?ms)function Test-Interpreter \\{.*?^\\}') "
                "{ Invoke-Expression $Matches[0] } "
                "else { Write-Error 'function not found'; exit 99 }; "
                "if (Test-Interpreter -Exe '" + exe_arg.replace("'", "''") + "' -Prefix @()) "
                "{ exit 0 } else { exit 1 }"
            )
            res = subprocess.run(
                [self.pwsh, "-NoProfile", "-Command", extract],
                capture_output=True,
                text=True,
            )
            return res.returncode == 0

    def test_version_ok_import_fail_is_rejected(self) -> None:
        if os.name == "nt":
            shim_body = (
                "@echo off\r\n"
                "if \"%~1\"==\"--version\" ( echo Python 3.10.0 & exit /b 0 )\r\n"
                "if \"%~1\"==\"-c\" ( echo ImportError 1>&2 & exit /b 1 )\r\n"
                "exit /b 2\r\n"
            )
        else:
            shim_body = (
                "if [ \"$1\" = \"--version\" ]; then echo 'Python 3.10.0'; exit 0; fi\n"
                "if [ \"$1\" = \"-c\" ]; then echo 'ImportError' 1>&2; exit 1; fi\n"
                "exit 2\n"
            )
        self.assertFalse(
            self._run_probe(shim_body),
            "Test-Interpreter should reject a Python that fails the import check",
        )

    def test_python_2_is_rejected(self) -> None:
        """A `--version`-passing Python 2 must not be accepted.

        The combined `-c` probe asserts sys.version_info[0]==3, so a
        shim that simulates Py2 by exiting nonzero on the `-c` call
        should be rejected even though `--version` succeeds.
        """
        if os.name == "nt":
            shim_body = (
                "@echo off\r\n"
                "if \"%~1\"==\"--version\" ( echo Python 2.7.18 & exit /b 0 )\r\n"
                "if \"%~1\"==\"-c\" ( exit /b 1 )\r\n"
                "exit /b 2\r\n"
            )
        else:
            shim_body = (
                "if [ \"$1\" = \"--version\" ]; then echo 'Python 2.7.18'; exit 0; fi\n"
                "if [ \"$1\" = \"-c\" ]; then exit 1; fi\n"
                "exit 2\n"
            )
        self.assertFalse(
            self._run_probe(shim_body),
            "Test-Interpreter must reject Python 2 even if --version exits 0",
        )

    def test_version_ok_import_ok_is_accepted(self) -> None:
        if os.name == "nt":
            shim_body = (
                "@echo off\r\n"
                "if \"%~1\"==\"--version\" ( echo Python 3.10.0 & exit /b 0 )\r\n"
                "if \"%~1\"==\"-c\" ( exit /b 0 )\r\n"
                "exit /b 2\r\n"
            )
        else:
            shim_body = (
                "if [ \"$1\" = \"--version\" ]; then echo 'Python 3.10.0'; exit 0; fi\n"
                "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
                "exit 2\n"
            )
        self.assertTrue(
            self._run_probe(shim_body),
            "Test-Interpreter should accept a Python that passes both probes",
        )


class TempDir:
    def __enter__(self) -> Path:
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        return Path(self._dir.name)

    def __exit__(self, *exc) -> None:
        self._dir.cleanup()


class MergeWatchTests(unittest.TestCase):
    """Issue #317: post-CI merge-watch loop in pr_watch.main."""

    def setUp(self) -> None:
        # Hard-isolate from the live renga peer channel — see the
        # module-level setUp comment. The previously-observed leak
        # (PR #555 PR_MERGE_WATCH_TIMEOUT visible to a parallel
        # worker) originated from `test_watch_for_merge_timeout_*`,
        # which calls `_watch_for_merge` without mocking
        # `_notify_peer`.
        _assert_peer_isolation()

    def _seed_run_for_merge(self, db: Path, *, pr_url: str,
                            pattern: str = "A") -> None:
        """Seed a run pointing at the PR. Default pattern='A' so the
        helper performs the full status transition; pass 'B' to test
        the pending-cleanup path."""
        from tools.state_db import apply_schema, connect
        from tools.state_db.writer import StateWriter

        apply_schema(connect(db))
        conn = connect(db)
        try:
            with StateWriter(conn).transaction() as w:
                w.upsert_run(
                    task_id="t-merge-watch",
                    project_slug="claude-org",
                    pattern=pattern,
                    title="merge-watch test",
                    status="review",
                    branch="feat/merge-watch",
                    pr_url=pr_url,
                    pr_state="open",
                )
        finally:
            conn.close()

    def _build_run_with_view_sequence(
        self,
        watch_exit: int,
        view_sequence: "list[dict | None]",
    ):
        """Like _make_fake_run but threads a sequence of `gh pr view --json`
        responses for the merge-watch loop. The first `gh pr view --json
        number` (PR-exists probe) returns success; the *second* and
        subsequent `view --json` calls cycle through ``view_sequence``."""
        # Default check JSON for the CI-watch portion (status=passed).
        checks_json = [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}]

        # Mutable index threaded across closures — track which entry of
        # view_sequence the next merge-watch poll should consume.
        view_idx = {"i": 0}
        seen_existence_probe = {"v": False}

        def fake_run(cmd, *args, **kwargs):
            # PR-exists probe: `gh pr view <pr> --repo <r> --json number`.
            if (cmd[:3] == ["gh", "pr", "view"]
                    and "--json" in cmd
                    and cmd[cmd.index("--json") + 1] == "number"):
                seen_existence_probe["v"] = True
                return mock.Mock(returncode=0, stdout="{}", stderr="")
            # Issue #636 head probe: `gh pr view <pr> --json headRefOid`.
            # Return an empty object so `_fetch_head_oid` resolves to None
            # (no head-change detection) WITHOUT consuming a view_sequence
            # entry — these legacy fixtures retain mergedAt-only behavior.
            if (cmd[:3] == ["gh", "pr", "view"]
                    and "--json" in cmd
                    and cmd[cmd.index("--json") + 1] == "headRefOid"):
                return mock.Mock(returncode=0, stdout="{}", stderr="")
            # Merge-watch probe: `gh pr view <pr> --json number,url,...`
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                idx = view_idx["i"]
                if idx >= len(view_sequence):
                    payload = view_sequence[-1]
                else:
                    payload = view_sequence[idx]
                    view_idx["i"] += 1
                if payload is None:
                    return mock.Mock(returncode=1, stdout="", stderr="boom")
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(payload),
                    stderr="",
                )
            # `gh pr checks --json` for CI classification.
            if "checks" in cmd and "--json" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(checks_json),
                    stderr="",
                )
            # The watched `gh pr checks --watch` run.
            return mock.Mock(returncode=watch_exit)

        return fake_run

    def test_ci_pass_then_merged_records_metadata(self) -> None:
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            pr_url = "https://github.com/octo/repo/pull/777"
            self._seed_run_for_merge(db, pr_url=pr_url)

            view_merged = {
                "number": 777, "url": pr_url, "state": "MERGED",
                "mergedAt": "2026-05-06T03:21:00Z",
                "mergeCommit": {"oid": "f" * 40},
                "headRefName": "feat/merge-watch",
            }
            fake_run = self._build_run_with_view_sequence(
                watch_exit=0, view_sequence=[view_merged],
            )
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                rc = pr_watch.main([
                    "--pr", "777", "--repo", "octo/repo", "--interval", "1",
                    "--merge-watch",
                ])
            self.assertEqual(rc, 0)

            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status, pr_state, completed_at "
                    "FROM runs WHERE task_id = 't-merge-watch'"
                ).fetchone()
                # status stays in 'review' — secretary owns the flip.
                self.assertEqual(row["status"], "review")
                self.assertEqual(row["pr_state"], "merged")
                self.assertEqual(row["completed_at"], "2026-05-06T03:21:00Z")
                merged_count = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE kind = 'pr_merged'"
                ).fetchone()[0]
                self.assertEqual(merged_count, 1)
            finally:
                conn.close()

    def test_default_skips_merge_watch(self) -> None:
        """Issue #317 round 3: merge-watch is opt-in, off by default.

        Without `--merge-watch`, even on CI pass pr_watch must NOT
        poll `gh pr view --json mergedAt`.
        """
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            self._seed_run_for_merge(
                db, pr_url="https://github.com/octo/repo/pull/111",
            )

            calls: list[list[str]] = []

            def fake_run(cmd, *args, **kwargs):
                calls.append(list(cmd))
                if (cmd[:3] == ["gh", "pr", "view"]
                        and "number" in cmd):
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if "checks" in cmd and "--json" in cmd:
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps(
                            [{"name": "ci", "state": "COMPLETED",
                              "bucket": "pass"}]
                        ),
                        stderr="",
                    )
                return mock.Mock(returncode=0)

            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                pr_watch.main([
                    "--pr", "111", "--repo", "octo/repo", "--interval", "1",
                ])

            view_calls = [c for c in calls
                          if c[:3] == ["gh", "pr", "view"]
                          and "url" in str(c)]
            self.assertEqual(view_calls, [])

    def test_ci_fail_skips_merge_watch(self) -> None:
        """When CI did not pass, pr_watch must not poll for merge."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)

            # Fail the CI; assert no further `view --json` call is made.
            calls: list[list[str]] = []

            def fake_run(cmd, *args, **kwargs):
                calls.append(list(cmd))
                if (cmd[:3] == ["gh", "pr", "view"]
                        and "number" in cmd):
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if "checks" in cmd and "--json" in cmd:
                    return mock.Mock(
                        returncode=1,
                        stdout=json.dumps([
                            {"name": "ci", "state": "COMPLETED",
                             "bucket": "fail"}
                        ]),
                        stderr="",
                    )
                return mock.Mock(returncode=1)

            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                pr_watch.main([
                    "--pr", "888", "--repo", "octo/repo", "--interval", "1",
                    "--merge-watch",
                ])

            # CI failed → no merge-watch even with --merge-watch on.
            view_calls = [c for c in calls
                          if c[:3] == ["gh", "pr", "view"]
                          and "url" in str(c)]
            self.assertEqual(view_calls, [])

    def test_no_merge_watch_flag_skips_loop(self) -> None:
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            self._seed_run_for_merge(
                db, pr_url="https://github.com/octo/repo/pull/999",
            )

            calls: list[list[str]] = []

            def fake_run(cmd, *args, **kwargs):
                calls.append(list(cmd))
                if (cmd[:3] == ["gh", "pr", "view"]
                        and "number" in cmd):
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if "checks" in cmd and "--json" in cmd:
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps(
                            [{"name": "ci", "state": "COMPLETED",
                              "bucket": "pass"}]
                        ),
                        stderr="",
                    )
                return mock.Mock(returncode=0)

            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                pr_watch.main([
                    "--pr", "999", "--repo", "octo/repo", "--interval", "1",
                    "--no-merge-watch",
                ])

            # Run row must NOT have been completed.
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status FROM runs WHERE task_id = 't-merge-watch'"
                ).fetchone()
                self.assertEqual(row["status"], "review")
            finally:
                conn.close()

    def test_watch_for_merge_timeout_records_event(self) -> None:
        """Bound exhaustion appends pr_merge_watch_timeout."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            view_pending = {
                "number": 555, "url": "https://github.com/octo/repo/pull/555",
                "state": "OPEN", "mergedAt": None,
                "mergeCommit": None, "headRefName": "feat/x",
            }

            from tools.state_db import apply_schema, connect
            apply_schema(connect(db))

            # Patch view fetch + monotonic so the loop exhausts after
            # one iteration. monotonic side_effect: start=0.0, then
            # 99999.0 to immediately exceed deadline.
            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(view_pending),
                        stderr="",
                    )
                raise AssertionError(f"unexpected cmd: {cmd}")

            with mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
                # Hardened against the PR #555 leak: even though the
                # module-level setUp scrubs RENGA_SOCKET, a regression
                # in `peer_notify`'s env-guard would still spawn the
                # `renga mcp-peer` binary if `_notify_peer` were
                # unmocked. Mocking the seam directly closes the door.
                result = pr_watch._watch_for_merge(
                    pr=555, repo="octo/repo", interval=0,
                    db_path=db, max_seconds=60,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 100.0, 100.0]),
                )
            self.assertEqual(result, "timeout")

            conn = sqlite3.connect(str(db))
            try:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM events "
                    "WHERE kind = 'pr_merge_watch_timeout'"
                ).fetchone()[0]
                self.assertEqual(cnt, 1)
            finally:
                conn.close()


class PeerNotifyTests(unittest.TestCase):
    """Issue #326: pr_watch dispatches peer messages to secretary on
    CI completion / merge detection / merge-watch timeout. Mocks the
    `_notify_peer` seam so the test suite doesn't spawn renga."""

    def test_ci_completed_dispatches_peer_message(self) -> None:
        fake_run = _make_fake_run(watch_exit=0)
        captured: list[str] = []

        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 7.0]):
                rc = pr_watch.main(["--pr", "326", "--repo", "octo/repo"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(captured), 1)
            self.assertIn("CI_COMPLETED", captured[0])
            self.assertIn("PR #326", captured[0])
            self.assertIn("passed", captured[0])
            self.assertIn("octo/repo", captured[0])

    def test_pr_merged_dispatches_peer_message(self) -> None:
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            pr_url = "https://github.com/octo/repo/pull/777"
            mw_tests = MergeWatchTests()
            mw_tests._seed_run_for_merge(db, pr_url=pr_url)

            view_merged = {
                "number": 777, "url": pr_url, "state": "MERGED",
                "mergedAt": "2026-05-06T03:21:00Z",
                "mergeCommit": {"oid": "f" * 40},
                "headRefName": "feat/merge-watch",
                "headRefOid": "c" * 40,
            }
            fake_run = mw_tests._build_run_with_view_sequence(
                watch_exit=0, view_sequence=[view_merged],
            )
            captured: list[str] = []
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                pr_watch.main([
                    "--pr", "777", "--repo", "octo/repo", "--interval", "1",
                    "--merge-watch",
                ])
            # Expect both CI_COMPLETED and PR_MERGED in captured.
            self.assertTrue(any("CI_COMPLETED" in m for m in captured),
                            f"missing CI_COMPLETED: {captured}")
            # Issue #636: PR_MERGED carries the merged head's short sha.
            self.assertTrue(
                any("PR_MERGED: PR #777 (head=ccccccc)" in m for m in captured),
                f"missing PR_MERGED w/ head: {captured}")

    def test_merge_watch_timeout_dispatches_peer_message(self) -> None:
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            view_pending = {
                "number": 555, "url": "https://github.com/octo/repo/pull/555",
                "state": "OPEN", "mergedAt": None,
                "mergeCommit": None, "headRefName": "feat/x",
                "headRefOid": "d" * 40,
            }
            from tools.state_db import apply_schema, connect
            apply_schema(connect(db))

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(view_pending),
                        stderr="",
                    )
                raise AssertionError(f"unexpected cmd: {cmd}")

            captured: list[str] = []
            with mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
                # Issue #636: baseline_head matches the polled headRefOid
                # (no head change), so the loop times out and the message
                # carries the short head sha.
                result = pr_watch._watch_for_merge(
                    pr=555, repo="octo/repo", interval=0,
                    db_path=db, max_seconds=60,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 100.0, 100.0]),
                    baseline_head="d" * 40,
                )
            self.assertEqual(result, "timeout")
            self.assertEqual(
                captured, ["PR_MERGE_WATCH_TIMEOUT: PR #555 (head=ddddddd)"]
            )

    def test_no_run_dispatches_distinct_message(self) -> None:
        """When complete_on_merge returns no_run, pr-watch must NOT
        send PR_MERGED (which would mislead secretary into starting
        post-merge cleanup). It surfaces a PR_MERGED_NO_RUN variant."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            from tools.state_db import apply_schema, connect
            apply_schema(connect(db))
            view_merged = {
                "number": 444,
                "url": "https://github.com/octo/repo/pull/444",
                "state": "MERGED",
                "mergedAt": "2026-05-06T03:21:00Z",
                "mergeCommit": {"oid": "a" * 40},
                "headRefName": "feat/x",
                "headRefOid": "e" * 40,
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(view_merged),
                        stderr="",
                    )
                raise AssertionError(f"unexpected cmd: {cmd}")

            captured: list[str] = []
            with mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
                # No seeded run for PR #444 → complete_on_merge → no_run.
                # Issue #636: the head sha comes from the merged view.
                result = pr_watch._watch_for_merge(
                    pr=444, repo="octo/repo", interval=0,
                    db_path=db, max_seconds=60,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 0.0, 100.0]),
                )
            self.assertEqual(result, "no_run")
            self.assertEqual(
                captured, ["PR_MERGED_NO_RUN: PR #444 (head=eeeeeee)"]
            )

    def test_no_renga_socket_silent_fallback(self) -> None:
        """With RENGA_SOCKET unset, _notify_peer must return False
        without raising or spawning anything, and pr_watch.main must
        complete normally."""
        from tools import peer_notify

        env = {k: v for k, v in os.environ.items() if k != "RENGA_SOCKET"}

        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(peer_notify.notify_peer("secretary", "x"))
            self.assertFalse(pr_watch._notify_peer("x"))

        # And run pr_watch.main end-to-end with the helper unmocked but
        # RENGA_SOCKET cleared — no exception, ci_completed event still
        # written.
        fake_run = _make_fake_run(watch_exit=0)
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                rc = pr_watch.main(["--pr", "10", "--repo", "octo/repo"])
            self.assertEqual(rc, 0)
            self.assertEqual(_count_ci_events(db), 1)


def _read_ci_events(db_path: Path) -> "list[dict]":
    """Return all ci_completed events (payload dicts, ordered by id)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE kind = 'ci_completed' "
            "ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r["payload_json"]) if r["payload_json"] else {}
            for r in rows]


class HeadPollLoopbackTests(unittest.TestCase):
    """Issue #636: merge-watch polls headRefOid and loops back to ci-watch
    when the PR head moves, re-emitting CI_COMPLETED for the new head."""

    HEAD_A = "a" * 40
    HEAD_B = "b" * 40
    PR_URL = "https://github.com/octo/repo/pull/636"

    def setUp(self) -> None:
        _assert_peer_isolation()

    # -- direct _watch_for_merge unit coverage -----------------------------

    def test_head_change_returns_head_changed(self) -> None:
        """A new headRefOid (≠ baseline) makes _watch_for_merge return
        ``head_changed`` without merging, timing out, or notifying."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            from tools.state_db import apply_schema, connect
            apply_schema(connect(db))

            view_a = {
                "number": 636, "url": self.PR_URL, "state": "OPEN",
                "mergedAt": None, "mergeCommit": None,
                "headRefName": "feat/hp", "headRefOid": self.HEAD_A,
            }
            view_b = dict(view_a, headRefOid=self.HEAD_B)
            views = [view_a, view_b]
            idx = {"i": 0}

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    i = idx["i"]
                    payload = views[i] if i < len(views) else views[-1]
                    if i < len(views) - 1:
                        idx["i"] = i + 1
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(payload), stderr="",
                    )
                raise AssertionError(f"unexpected cmd: {cmd}")

            captured: list[str] = []
            with mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
                # poll0 = HEAD_A (no change), poll1 = HEAD_B (change).
                result = pr_watch._watch_for_merge(
                    pr=636, repo="octo/repo", interval=0,
                    db_path=db, max_seconds=60,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 1.0, 2.0]),
                    baseline_head=self.HEAD_A,
                )
            self.assertEqual(result, "head_changed")
            # No peer notification on a head change (the ci-watch loopback
            # re-emits CI_COMPLETED instead).
            self.assertEqual(captured, [])
            # And no timeout event was written.
            conn = sqlite3.connect(str(db))
            try:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM events "
                    "WHERE kind = 'pr_merge_watch_timeout'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(cnt, 0)

    def test_none_baseline_disables_head_change_detection(self) -> None:
        """Compat (Issue #636 #7): when baseline_head is None, a moving
        headRefOid must NOT trigger head_changed — the loop falls through
        to its pre-#636 mergedAt/timeout behavior (None-safe)."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            from tools.state_db import apply_schema, connect
            apply_schema(connect(db))

            view = {
                "number": 636, "url": self.PR_URL, "state": "OPEN",
                "mergedAt": None, "mergeCommit": None,
                "headRefName": "feat/hp", "headRefOid": self.HEAD_B,
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(view), stderr="",
                    )
                raise AssertionError(f"unexpected cmd: {cmd}")

            with mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
                result = pr_watch._watch_for_merge(
                    pr=636, repo="octo/repo", interval=0,
                    db_path=db, max_seconds=60,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 100.0]),
                    baseline_head=None,  # detection disabled
                )
            # Falls through to timeout rather than head_changed.
            self.assertEqual(result, "timeout")

    # -- end-to-end main() loopback ----------------------------------------

    def test_main_loops_back_to_ci_watch_on_head_change(self) -> None:
        """ci-watch → merge-watch → head moves → ci-watch loopback → second
        CI_COMPLETED → merge. Proves the one-shot trap is fixed."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            # Seed a run so the final complete_on_merge resolves to MERGED.
            MergeWatchTests()._seed_run_for_merge(db, pr_url=self.PR_URL)

            head_calls = {"i": 0}
            mw_calls = {"i": 0}
            mw_seq = [
                # round-1 merge-watch poll: head already moved to HEAD_B.
                {"number": 636, "url": self.PR_URL, "state": "OPEN",
                 "mergedAt": None, "mergeCommit": None,
                 "headRefName": "feat/hp", "headRefOid": self.HEAD_B},
                # round-2 merge-watch poll: merged at HEAD_B.
                {"number": 636, "url": self.PR_URL, "state": "MERGED",
                 "mergedAt": "2026-06-23T00:00:00Z",
                 "mergeCommit": {"oid": "c" * 40},
                 "headRefName": "feat/hp", "headRefOid": self.HEAD_B},
            ]

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                    jval = cmd[cmd.index("--json") + 1]
                    if jval == "number":
                        return mock.Mock(returncode=0, stdout="{}", stderr="")
                    if jval == "headRefOid":
                        # Two head probes per ci-watch round (pre/post
                        # resolution). Head sits at HEAD_A through round 1
                        # (probes 0,1) and at HEAD_B through round 2
                        # (probes 2,3) — stable within each round so no
                        # spurious mid-resolution-advance is logged.
                        i = head_calls["i"]
                        head_calls["i"] = i + 1
                        oid = self.HEAD_A if i < 2 else self.HEAD_B
                        return mock.Mock(
                            returncode=0,
                            stdout=json.dumps({"headRefOid": oid}),
                            stderr="",
                        )
                    # merge-watch poll (long --json field list).
                    i = mw_calls["i"]
                    payload = mw_seq[i] if i < len(mw_seq) else mw_seq[-1]
                    if i < len(mw_seq) - 1:
                        mw_calls["i"] = i + 1
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(payload), stderr="",
                    )
                if "checks" in cmd and "--json" in cmd:
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps(
                            [{"name": "ci", "state": "COMPLETED",
                              "bucket": "pass"}]
                        ),
                        stderr="",
                    )
                # The watched `gh pr checks --watch` run (both rounds pass).
                return mock.Mock(returncode=0)

            captured: list[str] = []
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 1.0, 2.0, 3.0]):
                rc = pr_watch.main([
                    "--pr", "636", "--repo", "octo/repo", "--interval", "1",
                    "--merge-watch",
                ])
            self.assertEqual(rc, 0)

            # Two CI_COMPLETED messages — one per head — re-emitted across
            # the loopback. This is the core of the Issue #636 fix.
            ci_msgs = [m for m in captured if m.startswith("CI_COMPLETED")]
            self.assertEqual(len(ci_msgs), 2, f"captured={captured}")
            self.assertIn("head=aaaaaaa", ci_msgs[0])
            self.assertIn("head=bbbbbbb", ci_msgs[1])
            # The merge is announced against the new head.
            self.assertTrue(
                any("PR_MERGED: PR #636 (head=bbbbbbb)" in m for m in captured),
                f"captured={captured}")

            # Two ci_completed events, each tagged with its head sha.
            events = _read_ci_events(db)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["head"], "aaaaaaa")
            self.assertEqual(events[1]["head"], "bbbbbbb")
            self.assertEqual(events[0]["status"], "passed")
            self.assertEqual(events[1]["status"], "passed")

    def test_ci_completed_message_and_event_carry_head(self) -> None:
        """Acceptance: the CI_COMPLETED peer message and the ci_completed
        event both carry the short head sha (CI-only, no merge-watch)."""
        def fake_run(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                jval = cmd[cmd.index("--json") + 1]
                if jval == "number":
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if jval == "headRefOid":
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps({"headRefOid": self.HEAD_A}),
                        stderr="",
                    )
            if "checks" in cmd and "--json" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}]
                    ),
                    stderr="",
                )
            return mock.Mock(returncode=0)

        captured: list[str] = []
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 5.0]):
                rc = pr_watch.main(["--pr", "636", "--repo", "octo/repo"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(captured), 1)
            self.assertIn("CI_COMPLETED: PR #636 passed", captured[0])
            self.assertIn("head=aaaaaaa", captured[0])
            rec = _read_ci_event(db)
            self.assertEqual(rec["head"], "aaaaaaa")
            self.assertEqual(rec["status"], "passed")

    def test_merge_at_unconfirmed_head_is_distinct_signal_not_pr_merged(self) -> None:
        """Codex review: if a PR merges at a head different from the last
        CI-confirmed head (a push + merge slipped in before the next
        merge-watch poll), pr_watch must NOT emit a clean PR_MERGED. The
        merge is terminal (no loopback possible), so it emits a DISTINCT
        prefix (PR_MERGED_HEAD_UNCONFIRMED — fails closed so the secretary
        escalates instead of auto-advancing) and returns a sentinel main
        maps to a non-zero exit."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            MergeWatchTests()._seed_run_for_merge(db, pr_url=self.PR_URL)

            # Merged at HEAD_B while the CI baseline was HEAD_A.
            view_merged = {
                "number": 636, "url": self.PR_URL, "state": "MERGED",
                "mergedAt": "2026-06-23T00:00:00Z",
                "mergeCommit": {"oid": "c" * 40},
                "headRefName": "feat/hp", "headRefOid": self.HEAD_B,
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(view_merged), stderr="",
                    )
                raise AssertionError(f"unexpected cmd: {cmd}")

            captured: list[str] = []
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
                result = pr_watch._watch_for_merge(
                    pr=636, repo="octo/repo", interval=0,
                    db_path=db, max_seconds=60,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 0.0, 100.0]),
                    baseline_head=self.HEAD_A,  # CI was confirmed for HEAD_A
                )
            self.assertEqual(result, pr_watch.MERGE_RESULT_HEAD_UNCONFIRMED)
            self.assertEqual(len(captured), 1)
            msg = captured[0]
            # Distinct prefix — NOT the clean "PR_MERGED: " consumers act on.
            self.assertTrue(msg.startswith("PR_MERGED_HEAD_UNCONFIRMED: PR #636"))
            self.assertIn("head=bbbbbbb", msg)
            # Names the stale CI-confirmed head for the human.
            self.assertIn("aaaaaaa", msg)

    def test_unconfirmed_head_merge_exits_nonzero_through_main(self) -> None:
        """End-to-end: a merge at an unconfirmed head makes main exit 9 (not
        0), so shell callers don't treat it as a confirmed-CI success."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            MergeWatchTests()._seed_run_for_merge(db, pr_url=self.PR_URL)

            head_calls = {"i": 0}
            # ci-watch round confirms HEAD_A; merge-watch then observes a
            # merge already at HEAD_B (push+merge slipped in).
            view_merged = {
                "number": 636, "url": self.PR_URL, "state": "MERGED",
                "mergedAt": "2026-06-23T00:00:00Z",
                "mergeCommit": {"oid": "c" * 40},
                "headRefName": "feat/hp", "headRefOid": self.HEAD_B,
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                    jval = cmd[cmd.index("--json") + 1]
                    if jval == "number":
                        return mock.Mock(returncode=0, stdout="{}", stderr="")
                    if jval == "headRefOid":
                        # Both ci-watch head probes (pre/post) see HEAD_A.
                        head_calls["i"] += 1
                        return mock.Mock(
                            returncode=0,
                            stdout=json.dumps({"headRefOid": self.HEAD_A}),
                            stderr="",
                        )
                    # merge-watch poll: merged at HEAD_B.
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(view_merged), stderr="",
                    )
                if "checks" in cmd and "--json" in cmd:
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps(
                            [{"name": "ci", "state": "COMPLETED",
                              "bucket": "pass"}]
                        ),
                        stderr="",
                    )
                return mock.Mock(returncode=0)

            captured: list[str] = []
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0]):
                rc = pr_watch.main([
                    "--pr", "636", "--repo", "octo/repo", "--interval", "1",
                    "--merge-watch",
                ])
            self.assertEqual(rc, 9)
            self.assertTrue(
                any(m.startswith("PR_MERGED_HEAD_UNCONFIRMED") for m in captured),
                f"captured={captured}")
            self.assertFalse(
                any(m.startswith("PR_MERGED:") for m in captured),
                f"clean PR_MERGED must not be sent: {captured}")

    def test_merge_at_confirmed_head_has_no_warning(self) -> None:
        """The flag fires ONLY on a head mismatch: merging at the same head
        the CI baseline confirmed is a clean PR_MERGED with no warning."""
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            MergeWatchTests()._seed_run_for_merge(db, pr_url=self.PR_URL)

            view_merged = {
                "number": 636, "url": self.PR_URL, "state": "MERGED",
                "mergedAt": "2026-06-23T00:00:00Z",
                "mergeCommit": {"oid": "c" * 40},
                "headRefName": "feat/hp", "headRefOid": self.HEAD_A,
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(
                        returncode=0, stdout=json.dumps(view_merged), stderr="",
                    )
                raise AssertionError(f"unexpected cmd: {cmd}")

            captured: list[str] = []
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
                result = pr_watch._watch_for_merge(
                    pr=636, repo="octo/repo", interval=0,
                    db_path=db, max_seconds=60,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 0.0, 100.0]),
                    baseline_head=self.HEAD_A,
                )
            self.assertEqual(result, "merged")
            self.assertEqual(captured, ["PR_MERGED: PR #636 (head=aaaaaaa)"])

    def test_head_advance_during_resolution_restarts_ci_watch(self) -> None:
        """Codex review: if the branch advances during verdict resolution
        (pre-resolution head != post-resolution head), pr_watch records NO
        verdict for that round and restarts the full ci-watch for the new
        head — so a new head whose CI is still pending is properly waited
        on via `gh pr checks --watch`, not short-circuited to incomplete.
        The recorded verdict/head is the stabilized HEAD_B."""
        head_calls = {"i": 0}

        def fake_run(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                jval = cmd[cmd.index("--json") + 1]
                if jval == "number":
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if jval == "headRefOid":
                    # Round 1: pre=HEAD_A (probe 0), post=HEAD_B (probe 1)
                    # → advanced mid-resolution → head_changed → restart.
                    # Round 2: pre=HEAD_B (probe 2), post=HEAD_B (probe 3)
                    # → stable → record HEAD_B.
                    i = head_calls["i"]
                    head_calls["i"] = i + 1
                    oid = self.HEAD_A if i == 0 else self.HEAD_B
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps({"headRefOid": oid}),
                        stderr="",
                    )
            if "checks" in cmd and "--json" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}]
                    ),
                    stderr="",
                )
            return mock.Mock(returncode=0)

        captured: list[str] = []
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            # CI-only (no --merge-watch): even here a mid-resolution advance
            # restarts ci-watch, so the recorded verdict is for a stable head.
            # monotonic: round-1 consumes only `started` (head_changed returns
            # before the duration calc); round-2 consumes started + duration.
            with mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   side_effect=lambda msg, *a, **kw: captured.append(msg) or True), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic", side_effect=[0.0, 1.0, 5.0]):
                rc = pr_watch.main(["--pr", "636", "--repo", "octo/repo"])
            self.assertEqual(rc, 0)
            # Exactly one ci_completed event — the head_changed round records
            # nothing — tagged with the stabilized HEAD_B (never HEAD_A).
            rec = _read_ci_event(db)
            self.assertEqual(rec["head"], "bbbbbbb")
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(len(captured), 1)
            self.assertIn("head=bbbbbbb", captured[0])
            self.assertNotIn("aaaaaaa", captured[0])


if __name__ == "__main__":
    unittest.main()
