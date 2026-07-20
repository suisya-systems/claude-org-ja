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
    Issue #224 corrects the prior mapping. Issue #685: this fallback is
    reached only when the JSON probe never parsed, so exit 8 / other
    non-zero codes (we could not read the checks at all) map to
    ``indeterminate`` rather than ``incomplete`` — keeping "checks
    parseable but pending" distinct from "verdict unreadable".
    """

    def test_zero_is_passed(self) -> None:
        # gh --watch exit 0 is a definitive "all passed", honoured even
        # when the JSON probe was unavailable.
        self.assertEqual(pr_watch._classify(0), "passed")

    def test_eight_is_indeterminate(self) -> None:
        # gh exit 8 = "Checks pending"; reached here only when we could
        # not read the checks, so the verdict is undetermined (#685).
        self.assertEqual(pr_watch._classify(8), "indeterminate")

    def test_two_is_canceled(self) -> None:
        self.assertEqual(pr_watch._classify(2), "canceled")

    def test_other_nonzero_is_indeterminate(self) -> None:
        # Unknown gh errors with no readable checks → indeterminate
        # (verdict undetermined), never a libellous "failed".
        self.assertEqual(pr_watch._classify(1), "indeterminate")
        self.assertEqual(pr_watch._classify(127), "indeterminate")


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

    def test_pass_plus_skipping_mix_emits_ci_completed(self) -> None:
        """Issue #695 end-to-end repro: kura PR #38 had 4 passed + 2
        skipping + 0 pending checks. `gh pr checks --watch` never
        treats `skipping` as terminal, so it never returned and
        `ci_completed` was never journaled -- the secretary's
        auto-merge gate never fired and a human had to merge manually.
        `main()` must record a `passed` `ci_completed` event for this
        shape without hanging.
        """
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            self._run(
                journal,
                gh_exit=0,
                checks_json=(
                    [{"name": f"pass-{i}", "state": "COMPLETED", "bucket": "pass"}
                     for i in range(4)]
                    + [{"name": f"skip-{i}", "state": "COMPLETED", "bucket": "skipping"}
                       for i in range(2)]
                ),
            )
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(rec["fail_count"], 0)
            self.assertEqual(rec["pending_count"], 0)
            self.assertEqual(rec["total_checks"], 6)

    def test_pending_then_passes_via_self_poll(self) -> None:
        """Issue #695: a still-running (``pending`` bucket) check must
        keep the self-poll loop going, unbounded, at ``--interval``
        cadence -- mirroring the indefinite block ``gh pr checks
        --watch`` used to perform while CI was genuinely still running.
        Once the check transitions to a decided bucket, the self-poll
        loop returns that verdict directly (no bounded-retry timeout
        applies to a genuinely-running check).

        Pre-#695, `gh pr checks --watch` handled this blocking itself,
        and a stray ``pending`` observation reaching the JSON-probe
        resolver after `--watch` returned was retried only within a
        60s budget before giving up as ``incomplete`` (see
        ``test_retry_budget_exhausted_records_incomplete_once`` for
        that still-valid empty-list race). With `--watch` removed, a
        genuinely pending check must never be prematurely declared
        ``incomplete`` -- self-poll keeps polling until it is actually
        decided, however long that takes.
        """
        fake_run = _make_stateful_fake_run(
            watch_exit=8,
            checks_sequence=[
                [{"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                 {"name": "deploy", "state": "IN_PROGRESS", "bucket": "pending"}],
                [{"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                 {"name": "deploy", "state": "IN_PROGRESS", "bucket": "pending"}],
                [{"name": "lint", "state": "COMPLETED", "bucket": "pass"},
                 {"name": "deploy", "state": "COMPLETED", "bucket": "pass"}],
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
                                   side_effect=[100.0, 142.0]):
                rc = pr_watch.main([
                    "--pr", "205", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 0)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(_count_ci_events(journal), 1)

    def test_empty_then_real_pending_outlasting_budget_still_resolves(self) -> None:
        """Codex review (Issue #695 round 1, P1): the empty-list handoff
        from `_self_poll_watch` to `_resolve_final_status` must NOT
        inherit the resolver's 60s budget for a REAL pending check that
        shows up afterward.

        Repro: the first probe is `[]` (no check rows yet -- the
        Issue #413 race), so self-poll hands off to the bounded
        resolver. The resolver's own probe then sees a genuine
        `pending` check, but the retry budget is exhausted before it
        decides (simulated here by jumping `time.monotonic()` straight
        past the deadline). Without the fix, this would be recorded as
        a final `incomplete` even though CI was still legitimately
        running. With the fix, `_run_ci_watch_phase` recognizes the
        resolver's `incomplete` verdict carries a non-zero
        `total_checks` (a real check row, not an empty-race artifact)
        and hands control back to the unbounded self-poll loop, which
        keeps polling until the check actually decides.
        """
        fake_run = _make_stateful_fake_run(
            watch_exit=8,
            checks_sequence=[
                [],  # self-poll's first fetch: empty-race -> hand off
                [{"name": "deploy", "state": "IN_PROGRESS", "bucket": "pending"}],
                [{"name": "deploy", "state": "COMPLETED", "bucket": "pass"}],
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
                                   # started, resolver set_deadline,
                                   # resolver deadline-check (jumps past
                                   # budget -> exhausted), duration.
                                   side_effect=[0.0, 0.5, 9999.0, 9999.5]):
                rc = pr_watch.main([
                    "--pr", "205", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 0)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(_count_ci_events(journal), 1)

    def test_empty_then_failed_with_pending_sibling_waits_for_sibling(self) -> None:
        """Codex review (Issue #695 round 2, P2), exercised through the
        `_resolve_final_status` handoff composition: the resolver's own
        probe can observe a check that already failed while a sibling
        is still pending. `_summarize_checks` reports `status="failed"`
        immediately in that shape, but CI is not actually done -- the
        composition loop in `_run_ci_watch_phase` must key off
        `pending_count` (not `status`) to decide whether to resume
        self-poll, so the pending sibling is watched to completion
        rather than an early `failed` verdict being recorded while it
        is still running.
        """
        fake_run = _make_stateful_fake_run(
            watch_exit=8,
            checks_sequence=[
                [],  # self-poll's first fetch: empty-race -> hand off
                [{"name": "unit", "state": "COMPLETED", "bucket": "fail"},
                 {"name": "deploy", "state": "IN_PROGRESS", "bucket": "pending"}],
                [{"name": "unit", "state": "COMPLETED", "bucket": "fail"},
                 {"name": "deploy", "state": "COMPLETED", "bucket": "pass"}],
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
                                   side_effect=[0.0, 0.5, 9999.0, 9999.5]):
                rc = pr_watch.main([
                    "--pr", "205", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "failed")
            self.assertEqual(rec["fail_count"], 1)
            self.assertEqual(rec["pending_count"], 0)
            self.assertEqual(_count_ci_events(journal), 1)

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
                [],  # transient empty (self-poll hands off on this one)
                [{"name": "ci", "state": "COMPLETED", "bucket": "pass"}],
            ],
        )
        # Issue #695: self-poll's own first fetch consumes the `[]` entry
        # and hands off immediately (no monotonic call); the resolver's
        # first fetch then sees the `pass` entry and returns immediately
        # too (also no monotonic call) — so only `started` / `duration`
        # are consumed here, unlike the pre-#695 4-value sequence that
        # accounted for the resolver's own retry-budget bookkeeping.
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[100.0, 142.0]):
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

    def test_keyboard_interrupt_during_self_poll_is_canceled(self) -> None:
        """SIGINT (Python ``KeyboardInterrupt``) during the self-poll loop
        must be recorded as ``canceled``, not re-classified by whatever
        the JSON probe would otherwise say.

        Issue #695: since the self-poll loop replaced the blocking
        ``gh pr checks --watch`` subprocess, there is no longer a gh
        process exit code that can itself signal cancellation (the old
        "gh exit 2" regression fixture no longer applies) -- the only
        cancellation source is a KeyboardInterrupt raised inside Python
        (e.g. from ``time.sleep`` on Ctrl-C), which
        :func:`_run_ci_watch_phase` must still convert to a ``canceled``
        verdict without ever consulting ``_fetch_checks`` again after
        the interrupt.
        """
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch, "_pr_exists", return_value=True), \
                 mock.patch.object(pr_watch, "_fetch_head_oid", return_value=None), \
                 mock.patch.object(pr_watch, "_self_poll_watch",
                                   side_effect=KeyboardInterrupt), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[100.0, 101.0]):
                rc = pr_watch.main([
                    "--pr", "42", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 2)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "canceled")

    def test_json_probe_failure_falls_back_to_indeterminate(self) -> None:
        """If every `gh pr checks --json` probe fails (binary missing /
        malformed stdout), the resolver retries within its budget and
        only after exhaustion does it fall back to the exit-code
        classifier.

        Codex round-2 Major: a transient JSON parse failure used to
        short-circuit the retry budget and bypass the resolver
        entirely; the fix unifies the retry path so probe failures
        and ``incomplete`` observations are both retryable.

        Issue #695: pre-fix, this fixture's ``watch_exit=0`` fed a real
        `gh pr checks --watch` exit code into the fallback classifier,
        so total probe failure degraded to an (arguably too-optimistic)
        ``passed``. With `--watch` removed there is no gh exit code to
        consult at all -- the fallback is always ``indeterminate``
        (verdict undetermined), never a guessed ``passed``.
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
            self.assertEqual(rec["status"], "indeterminate")

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


class SummarizeChecksTests(unittest.TestCase):
    """Issue #685: _summarize_checks returns per-bucket counts alongside
    the status word (both feed the enriched ci_completed payload)."""

    def test_all_pass_counts(self) -> None:
        self.assertEqual(
            pr_watch._summarize_checks(
                [{"bucket": "pass"}, {"bucket": "skipping"}]
            ),
            ("passed", 0, 0, 2),
        )

    def test_fail_and_cancel_counted_as_failures(self) -> None:
        self.assertEqual(
            pr_watch._summarize_checks(
                [{"bucket": "pass"}, {"bucket": "fail"}, {"bucket": "cancel"}]
            ),
            ("failed", 2, 0, 3),
        )

    def test_pending_and_unknown_counted_as_pending(self) -> None:
        self.assertEqual(
            pr_watch._summarize_checks(
                [{"bucket": "pass"}, {"bucket": "pending"}, {"bucket": "mystery"}]
            ),
            ("incomplete", 0, 2, 3),
        )

    def test_empty_list(self) -> None:
        self.assertEqual(pr_watch._summarize_checks([]), ("incomplete", 0, 0, 0))

    def test_status_matches_classify_from_checks(self) -> None:
        # The status element must stay in lockstep with the legacy
        # single-value classifier across every fixture shape.
        for checks in (
            [],
            [{"bucket": "pass"}],
            [{"bucket": "pass"}, {"bucket": "fail"}],
            [{"bucket": "pass"}, {"bucket": "pending"}],
            [{"bucket": "cancel"}],
            [{"bucket": "mystery"}],
        ):
            self.assertEqual(
                pr_watch._summarize_checks(checks)[0],
                pr_watch._classify_from_checks(checks),
                f"mismatch for {checks!r}",
            )


class Issue685Tests(unittest.TestCase):
    """Issue #685: transient `gh pr checks --json` failure must not
    degrade a real verdict to a stalled `incomplete`.

    * A persistent probe failure records the NEW `indeterminate`
      status (verdict undetermined / fetch failure), distinct from
      `incomplete` (checks parseable but still pending).
    * A real red survives a transient probe failure as `failed`.
    * The payload gains per-bucket counts, and `indeterminate` gains
      an explicit retry schedule.
    * Retries are spaced with exponential backoff.
    """

    def setUp(self) -> None:
        _assert_peer_isolation()

    def _run(self, argv, fake_run, monotonic, *, sleep=True):
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            patches = [
                mock.patch.object(pr_watch, "JOURNAL_PATH", journal),
                mock.patch.object(pr_watch, "_notify_peer", return_value=False),
                mock.patch.object(pr_watch.shutil, "which",
                                  return_value="/usr/bin/gh"),
                mock.patch.object(pr_watch.subprocess, "run",
                                  side_effect=fake_run),
                mock.patch.object(pr_watch.time, "monotonic",
                                  side_effect=monotonic),
            ]
            if sleep:
                patches.append(
                    mock.patch.object(pr_watch.time, "sleep", return_value=None)
                )
            for p in patches:
                p.start()
            try:
                rc = pr_watch.main(argv)
            finally:
                for p in reversed(patches):
                    p.stop()
            return rc, _read_ci_event(journal)

    def test_persistent_probe_failure_records_indeterminate(self) -> None:
        """gh --watch exits 8 (pending) and every `--json` probe raises
        (binary hiccup): after the retry budget we record the NEW
        `indeterminate` status with an explicit retry schedule, and
        main exits 8 (not a clean pass/fail)."""
        fake_run = _make_fake_run(
            watch_exit=8,
            checks_raises=FileNotFoundError("gh transient outage"),
        )
        # started, set_deadline, deadline-check (past → break), duration.
        rc, rec = self._run(
            ["--pr", "685", "--repo", "octo/repo", "--interval", "5"],
            fake_run,
            monotonic=[0.0, 0.5, 9999.0, 9999.5],
        )
        self.assertEqual(rec["status"], "indeterminate")
        self.assertEqual(rc, 8)
        # Retry schedule is explicit so the monitoring side can tell an
        # undetermined verdict from a stalled merge gate.
        self.assertTrue(rec["retry_recommended"])
        self.assertEqual(rec["retry_after_sec"], pr_watch.RETRY_INTERVAL_SEC)
        self.assertGreaterEqual(rec["probe_attempts"], 1)
        # No per-bucket counts on the fetch-failure path (nothing read).
        self.assertNotIn("total_checks", rec)

    def test_persistent_probe_failure_exit1_is_indeterminate_not_failed(self) -> None:
        """gh --watch exit 1 with an unreadable probe must NOT be
        guessed as `failed` — we could not read which (if any) check
        is red, so the honest verdict is `indeterminate`."""
        fake_run = _make_fake_run(
            watch_exit=1,
            checks_raises=FileNotFoundError("gh transient outage"),
        )
        rc, rec = self._run(
            ["--pr", "685", "--repo", "octo/repo", "--interval", "5"],
            fake_run,
            monotonic=[0.0, 0.5, 9999.0, 9999.5],
        )
        self.assertEqual(rec["status"], "indeterminate")

    def test_real_red_survives_transient_probe_failure(self) -> None:
        """Acceptance: a real red (fail >=1) that follows a single
        transient probe failure is recorded as `failed`, not degraded
        to `incomplete`/`indeterminate`."""
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
                    returncode=1,
                    stdout=json.dumps(
                        [{"name": "lint", "bucket": "pass"},
                         {"name": "test", "bucket": "fail"}]
                    ),
                    stderr="",
                )
            return mock.Mock(returncode=8)  # gh --watch exit 8 (pending)

        # started, probe-fail set_deadline, check(<deadline)->sleep,
        # probe-fail-then-fail returns, duration.
        rc, rec = self._run(
            ["--pr", "685", "--repo", "octo/repo", "--interval", "5"],
            fake_run,
            monotonic=[0.0, 0.1, 0.2, 3.0],
        )
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rc, 1)
        self.assertEqual(rec["fail_count"], 1)
        self.assertEqual(rec["total_checks"], 2)

    def test_failed_payload_carries_counts(self) -> None:
        fake_run = _make_fake_run(
            watch_exit=1,
            checks_json=[
                {"name": "lint", "bucket": "pass"},
                {"name": "unit", "bucket": "fail"},
                {"name": "e2e", "bucket": "cancel"},
            ],
        )
        rc, rec = self._run(
            ["--pr", "685", "--repo", "octo/repo"],
            fake_run,
            monotonic=[0.0, 5.0],
        )
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["fail_count"], 2)
        self.assertEqual(rec["pending_count"], 0)
        self.assertEqual(rec["total_checks"], 3)

    def test_passed_payload_carries_counts(self) -> None:
        fake_run = _make_fake_run(
            watch_exit=0,
            checks_json=[
                {"name": "lint", "bucket": "pass"},
                {"name": "unit", "bucket": "skipping"},
            ],
        )
        rc, rec = self._run(
            ["--pr", "685", "--repo", "octo/repo"],
            fake_run,
            monotonic=[0.0, 5.0],
        )
        self.assertEqual(rec["status"], "passed")
        self.assertEqual(rec["fail_count"], 0)
        self.assertEqual(rec["pending_count"], 0)
        self.assertEqual(rec["total_checks"], 2)
        # `passed` is not an indeterminate verdict — no retry schedule.
        self.assertNotIn("retry_recommended", rec)

    def test_incomplete_is_distinct_from_indeterminate(self) -> None:
        """A parseable-but-empty verdict stays `incomplete` (with
        counts, no retry schedule) — the fetch-failure `indeterminate`
        is a different word. This is the core 3-value split.

        Issue #695: a *non-empty* persistently-pending checks list is
        no longer reachable as a final `incomplete` verdict — the
        self-poll loop now polls it unbounded until it decides (see
        ``test_pending_then_passes_via_self_poll``), matching
        `gh --watch`'s own indefinite block while CI is genuinely
        running. The empty-list race (no check rows visible at all)
        is the remaining path that legitimately exhausts the retry
        budget into `incomplete`.
        """
        fake_run = _make_fake_run(
            watch_exit=8,
            checks_json=[],
        )
        # Always-empty: retry budget exhausts, then record incomplete.
        rc, rec = self._run(
            ["--pr", "685", "--repo", "octo/repo", "--interval", "5"],
            fake_run,
            monotonic=[0.0, 0.5, 9999.0, 9999.5],
        )
        self.assertEqual(rec["status"], "incomplete")
        self.assertEqual(rec["pending_count"], 0)
        self.assertEqual(rec["total_checks"], 0)
        # `incomplete` (checks read, still pending) is NOT the
        # fetch-failure path, so it carries no retry schedule.
        self.assertNotIn("retry_recommended", rec)

    def test_exponential_backoff_between_probes(self) -> None:
        """Retry sleeps grow geometrically (5 -> 10 -> 20) and cap at
        RETRY_MAX_INTERVAL_SEC (30)."""
        sleeps: list = []
        with mock.patch.object(pr_watch, "_fetch_checks", return_value=[]), \
             mock.patch.object(pr_watch.time, "sleep",
                               side_effect=lambda s: sleeps.append(s)), \
             mock.patch.object(pr_watch.time, "monotonic",
                               side_effect=[0.0, 1.0, 2.0, 3.0, 4.0, 100.0]):
            verdict = pr_watch._resolve_final_status(1, "octo/repo", 8)
        self.assertEqual(verdict["status"], "incomplete")
        self.assertEqual(sleeps, [5, 10, 20, 30])

    def test_ci_watch_phase_widens_budget_for_empty_race_handoff(self) -> None:
        """Codex review (Issue #695 round 3, P2): the self-poll handoff
        into `_resolve_final_status` (on an empty/unparseable first
        observation) must use the wider
        `CI_WATCH_EMPTY_RACE_BUDGET_SEC`, not the narrow
        `RETRY_BUDGET_SEC` the post-`--watch` race was originally
        built for -- otherwise a CI system that takes longer than
        `RETRY_BUDGET_SEC` (but well under
        `CI_WATCH_EMPTY_RACE_BUDGET_SEC`) to publish its first check
        row would be wrongly finalized as `incomplete` before it ever
        got a chance to run."""
        captured_kwargs: list = []
        real_resolve = pr_watch._resolve_final_status

        def spy(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return real_resolve(*args, **kwargs)

        with mock.patch.object(pr_watch, "_fetch_head_oid", return_value=None), \
             mock.patch.object(pr_watch, "_evaluate_startup_state", return_value=None), \
             mock.patch.object(pr_watch, "_self_poll_watch", return_value=None), \
             mock.patch.object(pr_watch, "_resolve_final_status", side_effect=spy), \
             mock.patch.object(pr_watch, "_fetch_checks", return_value=[]), \
             mock.patch.object(pr_watch, "_record_ci_completed"), \
             mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
             mock.patch.object(pr_watch.time, "sleep", return_value=None), \
             mock.patch.object(pr_watch.time, "monotonic",
                               side_effect=[0.0, 0.5, 999999.0, 999999.5]):
            pr_watch._run_ci_watch_phase(
                pr=1, repo="octo/repo", interval=5, db_path=Path("/dev/null"),
            )
        self.assertEqual(len(captured_kwargs), 1)
        self.assertEqual(
            captured_kwargs[0].get("budget_sec"),
            pr_watch.CI_WATCH_EMPTY_RACE_BUDGET_SEC,
        )
        self.assertGreater(pr_watch.CI_WATCH_EMPTY_RACE_BUDGET_SEC,
                           pr_watch.RETRY_BUDGET_SEC)


class SelfPollWatchTests(unittest.TestCase):
    """Issue #695: unit coverage for `_self_poll_watch`, the self-poll
    loop that replaced the blocking `gh pr checks --watch` subprocess.

    Root-cause repro (kura PR #38): a PR with 4 passed + 2 skipping + 0
    pending checks never made `gh pr checks --watch` return, because gh
    does not treat `skipping` as terminal even though our own
    classifier does -- so `CI_COMPLETED` was never recorded and the
    secretary's auto-merge gate never fired.
    """

    def test_all_pass_resolves_immediately(self) -> None:
        checks = [{"bucket": "pass"}, {"bucket": "pass"}]
        with mock.patch.object(pr_watch, "_fetch_checks", return_value=checks), \
             mock.patch.object(pr_watch.time, "sleep") as sleep_mock:
            verdict = pr_watch._self_poll_watch(1, "octo/repo", 30)
        self.assertEqual(verdict["status"], "passed")
        self.assertEqual(verdict["total_checks"], 2)
        self.assertEqual(verdict["probe_attempts"], 1)
        sleep_mock.assert_not_called()

    def test_fail_resolves_immediately(self) -> None:
        checks = [{"bucket": "pass"}, {"bucket": "fail"}]
        with mock.patch.object(pr_watch, "_fetch_checks", return_value=checks), \
             mock.patch.object(pr_watch.time, "sleep") as sleep_mock:
            verdict = pr_watch._self_poll_watch(1, "octo/repo", 30)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["fail_count"], 1)
        sleep_mock.assert_not_called()

    def test_pass_plus_skipping_mix_resolves_immediately(self) -> None:
        """The literal Issue #695 repro fixture: 4 pass + 2 skipping, 0
        pending. `gh --watch` never returned for this shape; the
        self-poll loop must resolve on the very first observation
        (no sleep / extra poll needed) since every bucket is already
        decided."""
        checks = (
            [{"bucket": "pass"}] * 4 + [{"bucket": "skipping"}] * 2
        )
        with mock.patch.object(pr_watch, "_fetch_checks", return_value=checks), \
             mock.patch.object(pr_watch.time, "sleep") as sleep_mock:
            verdict = pr_watch._self_poll_watch(1, "octo/repo", 30)
        self.assertEqual(verdict["status"], "passed")
        self.assertEqual(verdict["fail_count"], 0)
        self.assertEqual(verdict["pending_count"], 0)
        self.assertEqual(verdict["total_checks"], 6)
        self.assertEqual(verdict["probe_attempts"], 1)
        sleep_mock.assert_not_called()

    def test_pending_then_passed_polls_at_interval(self) -> None:
        """A genuinely pending check keeps the loop going, sleeping
        `interval` seconds between polls, until it decides."""
        sequence = [
            [{"bucket": "pending"}],
            [{"bucket": "pending"}],
            [{"bucket": "pass"}],
        ]
        with mock.patch.object(pr_watch, "_fetch_checks",
                               side_effect=sequence), \
             mock.patch.object(pr_watch.time, "sleep") as sleep_mock:
            verdict = pr_watch._self_poll_watch(1, "octo/repo", 15)
        self.assertEqual(verdict["status"], "passed")
        self.assertEqual(verdict["probe_attempts"], 3)
        self.assertEqual(sleep_mock.call_args_list, [mock.call(15), mock.call(15)])

    def test_failed_check_with_sibling_still_pending_keeps_polling(self) -> None:
        """Codex review (Issue #695 round 2, P2): a check that has
        already failed must NOT short-circuit the loop while a sibling
        check is still pending -- `_summarize_checks` reports
        `status="failed"` the moment any bucket is a failure, regardless
        of `pending_count`, so the termination gate must key off
        `pending_count == 0` rather than `status != "incomplete"`.
        `gh pr checks --watch` itself waits for every check to leave
        pending before returning, even after one has already gone red.
        """
        sequence = [
            [{"bucket": "fail"}, {"bucket": "pending"}],
            [{"bucket": "fail"}, {"bucket": "pending"}],
            [{"bucket": "fail"}, {"bucket": "pass"}],
        ]
        with mock.patch.object(pr_watch, "_fetch_checks",
                               side_effect=sequence), \
             mock.patch.object(pr_watch.time, "sleep") as sleep_mock:
            verdict = pr_watch._self_poll_watch(1, "octo/repo", 15)
        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["fail_count"], 1)
        self.assertEqual(verdict["pending_count"], 0)
        self.assertEqual(verdict["probe_attempts"], 3)
        self.assertEqual(sleep_mock.call_args_list, [mock.call(15), mock.call(15)])

    def test_empty_list_hands_off_without_sleeping_in_loop(self) -> None:
        """An empty check list (no rows visible yet) is inconclusive,
        not "still running" -- returns None immediately so the caller's
        bounded `_resolve_final_status` retry/backoff handles it,
        rather than polling unbounded here."""
        with mock.patch.object(pr_watch, "_fetch_checks", return_value=[]), \
             mock.patch.object(pr_watch.time, "sleep") as sleep_mock:
            verdict = pr_watch._self_poll_watch(1, "octo/repo", 30)
        self.assertIsNone(verdict)
        sleep_mock.assert_not_called()

    def test_unparseable_probe_hands_off_without_sleeping_in_loop(self) -> None:
        with mock.patch.object(pr_watch, "_fetch_checks", return_value=None), \
             mock.patch.object(pr_watch.time, "sleep") as sleep_mock:
            verdict = pr_watch._self_poll_watch(1, "octo/repo", 30)
        self.assertIsNone(verdict)
        sleep_mock.assert_not_called()


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
            # Issue #719 startup probe: `gh pr view <pr> --json
            # statusCheckRollup`. Return an empty rollup so
            # `_evaluate_startup_state` defers to the self-poll path
            # WITHOUT consuming a view_sequence entry (these fixtures drive
            # the CI verdict through `gh pr checks`, not the rollup).
            if (cmd[:3] == ["gh", "pr", "view"]
                    and "--json" in cmd
                    and cmd[cmd.index("--json") + 1] == "statusCheckRollup"):
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"statusCheckRollup": []}),
                    stderr="",
                )
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
                    if jval == "statusCheckRollup":
                        # Issue #719 startup probe: empty rollup so
                        # `_evaluate_startup_state` defers to the self-poll
                        # path WITHOUT consuming an mw_seq entry (this
                        # fixture drives the verdict + head loopback through
                        # `gh pr checks` + headRefOid, not the rollup).
                        return mock.Mock(
                            returncode=0,
                            stdout=json.dumps({"statusCheckRollup": []}),
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


def _read_events_of_kind(db_path: Path, kind: str) -> "list[dict]":
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE kind = ? ORDER BY id",
            (kind,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r["payload_json"]) if r["payload_json"] else {}
            for r in rows]


class TransportDetectionTests(unittest.TestCase):
    """_configured_transport: raw-env dispatch matching peer_notify."""

    def test_broker_when_org_transport_broker(self) -> None:
        with mock.patch.dict(os.environ, {"ORG_TRANSPORT": "broker"},
                             clear=True):
            self.assertEqual(pr_watch._configured_transport(), "broker")

    def test_renga_when_socket_set(self) -> None:
        with mock.patch.dict(os.environ, {"RENGA_SOCKET": "/tmp/s"},
                             clear=True):
            self.assertEqual(pr_watch._configured_transport(), "renga")

    def test_none_when_unset(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if k not in ("ORG_TRANSPORT", "RENGA_SOCKET")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(pr_watch._configured_transport())


class FailLoudNotifyTests(unittest.TestCase):
    """Refs #653 #658: _notify_or_record records notify_failed only when a
    transport is configured and the push failed (silent no-op elimination)."""

    def _db(self, tmp: Path) -> Path:
        db = tmp / ".state" / "state.db"
        db.parent.mkdir(parents=True)
        from tools.state_db import apply_schema, connect
        apply_schema(connect(db))
        return db

    def test_records_notify_failed_when_configured_and_push_fails(self) -> None:
        with TempDir() as tmp:
            db = self._db(tmp)
            with mock.patch.dict(os.environ, {"ORG_TRANSPORT": "broker"},
                                 clear=True), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   return_value=False):
                ok = pr_watch._notify_or_record(
                    "CI_COMPLETED: PR #7 passed", db_path=db,
                    failed_kind="ci_completed", pr=7)
            self.assertFalse(ok)
            rows = _read_events_of_kind(db, "notify_failed")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pr"], 7)
            self.assertEqual(rows[0]["failed_kind"], "ci_completed")
            self.assertEqual(rows[0]["transport"], "broker")
            self.assertIn("env_present", rows[0])

    def test_no_record_when_no_transport(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if k not in ("ORG_TRANSPORT", "RENGA_SOCKET")}
        with TempDir() as tmp:
            db = self._db(tmp)
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   return_value=False):
                ok = pr_watch._notify_or_record(
                    "CI_COMPLETED: PR #7 passed", db_path=db,
                    failed_kind="ci_completed", pr=7)
            self.assertFalse(ok)
            self.assertEqual(_read_events_of_kind(db, "notify_failed"), [])

    def test_no_record_when_push_succeeds(self) -> None:
        with TempDir() as tmp:
            db = self._db(tmp)
            with mock.patch.dict(os.environ, {"ORG_TRANSPORT": "broker"},
                                 clear=True), \
                 mock.patch.object(pr_watch, "_notify_peer",
                                   return_value=True):
                ok = pr_watch._notify_or_record(
                    "CI_COMPLETED: PR #7 passed", db_path=db,
                    failed_kind="ci_completed", pr=7)
            self.assertTrue(ok)
            self.assertEqual(_read_events_of_kind(db, "notify_failed"), [])


class CanonicalTerminalEventTests(unittest.TestCase):
    """Refs #653 #658: peer-only terminal signals now write a canonical DB
    event BEFORE the push, so a lost push can't strand them."""

    def _db(self, tmp: Path) -> Path:
        db = tmp / ".state" / "state.db"
        db.parent.mkdir(parents=True)
        from tools.state_db import apply_schema, connect
        apply_schema(connect(db))
        return db

    def test_no_run_writes_canonical_event(self) -> None:
        with TempDir() as tmp:
            db = self._db(tmp)
            view_merged = {
                "number": 444, "state": "MERGED",
                "mergedAt": "2026-05-06T03:21:00Z",
                "mergeCommit": {"oid": "a" * 40},
                "headRefName": "feat/x", "headRefOid": "e" * 40,
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(returncode=0,
                                     stdout=json.dumps(view_merged), stderr="")
                raise AssertionError(f"unexpected cmd: {cmd}")

            with mock.patch.object(pr_watch, "_notify_peer", return_value=True), \
                 mock.patch.object(pr_watch.subprocess, "run",
                                   side_effect=fake_run):
                result = pr_watch._watch_for_merge(
                    pr=444, repo="octo/repo", interval=0, db_path=db,
                    max_seconds=60, sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 0.0, 100.0]))
            self.assertEqual(result, "no_run")
            rows = _read_events_of_kind(db, "pr_merged_no_run")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pr"], 444)

    def test_head_unconfirmed_writes_canonical_event(self) -> None:
        with TempDir() as tmp:
            db = self._db(tmp)
            # PR merged at a head that differs from the CI-confirmed baseline.
            view_merged = {
                "number": 636, "state": "MERGED",
                "mergedAt": "2026-05-06T03:21:00Z",
                "mergeCommit": {"oid": "f" * 40},
                "headRefName": "feat/x", "headRefOid": "b" * 40,
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd[:3] == ["gh", "pr", "view"]:
                    return mock.Mock(returncode=0,
                                     stdout=json.dumps(view_merged), stderr="")
                raise AssertionError(f"unexpected cmd: {cmd}")

            with mock.patch.object(pr_watch, "_notify_peer", return_value=True), \
                 mock.patch.object(pr_watch.subprocess, "run",
                                   side_effect=fake_run):
                result = pr_watch._watch_for_merge(
                    pr=636, repo="octo/repo", interval=0, db_path=db,
                    max_seconds=60, baseline_head="a" * 40,
                    sleeper=lambda _s: None,
                    monotonic=mock.Mock(side_effect=[0.0, 0.0, 100.0]))
            self.assertEqual(result, pr_watch.MERGE_RESULT_HEAD_UNCONFIRMED)
            rows = _read_events_of_kind(db, "pr_merged_head_unconfirmed")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pr"], 636)
            self.assertEqual(rows[0]["baseline_head"], "aaaaaaa")


class WatchAbortedTests(unittest.TestCase):
    """Refs #653 #658: a watcher that dies on an unexpected exception
    records a canonical pr_watch_aborted event and re-raises."""

    def test_records_pr_watch_aborted_and_reraises(self) -> None:
        with TempDir() as tmp:
            db = tmp / ".state" / "state.db"
            db.parent.mkdir(parents=True)
            from tools.state_db import apply_schema, connect
            apply_schema(connect(db))
            env = {k: v for k, v in os.environ.items()
                   if k not in ("ORG_TRANSPORT", "RENGA_SOCKET")}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(pr_watch, "JOURNAL_PATH", db), \
                 mock.patch.object(pr_watch, "_ensure_gh_installed",
                                   return_value=None), \
                 mock.patch.object(pr_watch, "_resolve_repo",
                                   return_value="octo/repo"), \
                 mock.patch.object(pr_watch, "_pr_exists", return_value=True), \
                 mock.patch.object(pr_watch, "_watch_loop",
                                   side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    pr_watch.main(["--pr", "5", "--repo", "octo/repo"])
            rows = _read_events_of_kind(db, "pr_watch_aborted")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pr"], 5)
            self.assertIn("RuntimeError", rows[0]["error"])


class StartupStateEvalTests(unittest.TestCase):
    """Issue #719: the startup absolute-state evaluation.

    When the watcher spawns *after* CI already finished, there is no
    running→completed transition left to observe. Before entering the
    self-poll loop, `_evaluate_startup_state` reads the PR's aggregate
    `gh pr view --json statusCheckRollup` once; if every check is already
    terminal it records `ci_completed` (passed/failed) immediately instead
    of degrading to a ~306s `indeterminate` via the empty-race resolver
    handoff (5x reproduced 2026-07-16: en#504/505/506, ja#718, en#509).

    Four systems: startup already-passed / already-failed /
    running→completed / continued fetch-failure.
    """

    def setUp(self) -> None:
        _assert_peer_isolation()

    @staticmethod
    def _fake_run(
        *,
        rollup_stdout: "str | None",
        rollup_returncode: int = 0,
        checks_sequence: "list | None" = None,
        checks_raises: "Exception | None" = None,
        checks_calls: "list | None" = None,
    ):
        """Build a `subprocess.run` stub for the startup-eval tests.

        * `gh pr view --json number` → PR exists.
        * `gh pr view --json headRefOid` → `{}` (head detection off).
        * `gh pr view --json statusCheckRollup` → `rollup_stdout`
          (``None`` simulates an unparseable / no-checks probe).
        * `gh pr checks --json ...` → next entry of `checks_sequence`
          (last entry reused), or raises `checks_raises`. Each such call
          is appended to `checks_calls` so a test can assert whether the
          polling path ran at all.
        """
        idx = {"i": 0}

        def fake_run(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                jval = cmd[cmd.index("--json") + 1]
                if jval == "number":
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if jval == "headRefOid":
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if jval == "statusCheckRollup":
                    return mock.Mock(
                        returncode=rollup_returncode,
                        stdout="" if rollup_stdout is None else rollup_stdout,
                        stderr="",
                    )
            if "checks" in cmd and "--json" in cmd:
                if checks_calls is not None:
                    checks_calls.append(list(cmd))
                if checks_raises is not None:
                    raise checks_raises
                seq = checks_sequence or [[]]
                i = idx["i"]
                payload = seq[i] if i < len(seq) else seq[-1]
                if i < len(seq) - 1:
                    idx["i"] = i + 1
                return mock.Mock(
                    returncode=_gh_exit_for_payload(payload),
                    stdout=json.dumps(payload),
                    stderr="",
                )
            return mock.Mock(returncode=0)

        return fake_run

    def test_startup_already_passed_records_immediately(self) -> None:
        """A rollup that is entirely terminal + green at spawn records
        `passed` immediately, WITHOUT ever polling `gh pr checks` (proving
        the transition-observation path was short-circuited)."""
        rollup = {"statusCheckRollup": [
            {"__typename": "CheckRun", "name": "build",
             "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "name": "lint",
             "status": "COMPLETED", "conclusion": "SKIPPED"},
            {"__typename": "StatusContext", "context": "ci/legacy",
             "state": "SUCCESS"},
        ]}
        checks_calls: list = []
        fake_run = self._fake_run(
            rollup_stdout=json.dumps(rollup), checks_calls=checks_calls,
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 3.0]):
                rc = pr_watch.main(["--pr", "719", "--repo", "octo/repo"])
            self.assertEqual(rc, 0)
            self.assertEqual(_count_ci_events(journal), 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(rec["fail_count"], 0)
            self.assertEqual(rec["pending_count"], 0)
            self.assertEqual(rec["total_checks"], 3)
            self.assertEqual(rec["duration_sec"], 3)
            # The whole point of #719: no transition polling was needed.
            self.assertEqual(checks_calls, [])

    def test_startup_already_failed_records_immediately(self) -> None:
        """A rollup with a terminal failure at spawn records `failed`
        immediately (exit 1), again without polling `gh pr checks`."""
        rollup = {"statusCheckRollup": [
            {"__typename": "CheckRun", "name": "build",
             "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "name": "test",
             "status": "COMPLETED", "conclusion": "FAILURE"},
        ]}
        checks_calls: list = []
        fake_run = self._fake_run(
            rollup_stdout=json.dumps(rollup), checks_calls=checks_calls,
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 2.0]):
                rc = pr_watch.main(["--pr", "719", "--repo", "octo/repo"])
            self.assertEqual(rc, 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "failed")
            self.assertEqual(rec["fail_count"], 1)
            self.assertEqual(rec["total_checks"], 2)
            self.assertEqual(checks_calls, [])

    def test_startup_running_then_completed_uses_polling(self) -> None:
        """A rollup that still has a pending check at spawn is NON-terminal:
        `_evaluate_startup_state` returns None and the existing self-poll
        path observes the running→completed transition via `gh pr checks`
        and records `passed`."""
        rollup = {"statusCheckRollup": [
            {"__typename": "CheckRun", "name": "build",
             "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "name": "deploy",
             "status": "IN_PROGRESS", "conclusion": None},
        ]}
        checks_calls: list = []
        fake_run = self._fake_run(
            rollup_stdout=json.dumps(rollup),
            checks_sequence=[
                [{"name": "build", "state": "COMPLETED", "bucket": "pass"},
                 {"name": "deploy", "state": "IN_PROGRESS", "bucket": "pending"}],
                [{"name": "build", "state": "COMPLETED", "bucket": "pass"},
                 {"name": "deploy", "state": "COMPLETED", "bucket": "pass"}],
            ],
            checks_calls=checks_calls,
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 4.0]):
                rc = pr_watch.main([
                    "--pr", "719", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 0)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(_count_ci_events(journal), 1)
            # Non-terminal startup → the polling path DID run.
            self.assertGreaterEqual(len(checks_calls), 1)

    def test_continued_fetch_failure_is_indeterminate(self) -> None:
        """When the startup rollup is unreadable AND every `gh pr checks`
        probe also fails, the verdict is `indeterminate` (continued fetch
        failure) — distinct from a completed observation. This is the ONLY
        route to `indeterminate` after #719."""
        checks_calls: list = []
        fake_run = self._fake_run(
            # Rollup probe returns empty stdout → unparseable → None.
            rollup_stdout=None, rollup_returncode=1,
            checks_raises=FileNotFoundError("gh transient outage"),
            checks_calls=checks_calls,
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 0.5, 9999.0, 9999.5]):
                rc = pr_watch.main([
                    "--pr", "719", "--repo", "octo/repo", "--interval", "5",
                ])
            self.assertEqual(rc, 8)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "indeterminate")
            self.assertTrue(rec["retry_recommended"])
            # No per-bucket counts on the fetch-failure path (nothing read).
            self.assertNotIn("total_checks", rec)


class ChecksJsonFallbackTests(unittest.TestCase):
    """Issue #739: gh < 2.50.0 has no `gh pr checks --json` flag.

    Real-world shape (gh 2.45.0, Ubuntu 24.04 archive): the probe exits
    1 with "unknown flag: --json" on stderr and EMPTY stdout — an
    unparseable probe on every attempt. Pre-#739 that made every
    ci-watch round burn the full CI_WATCH_EMPTY_RACE_BUDGET_SEC
    resolver budget (13 probes, ~306s) and record `indeterminate` with
    `retry_recommended`, even though CI was green (reproduced on all 8
    PRs of 2026-07-18/19 + ja#743, each with a single green check
    named 'test'). #719's startup evaluation didn't cover it because
    at spawn time — right after PR creation — the rollup is still
    pending, so the whole watch rode on the broken probe.

    The fix: `_fetch_checks` detects the unknown-flag failure, caches
    the incapability, and serves this and all later probes from
    `gh pr view --json statusCheckRollup` (supported on old gh),
    normalized through the #719 rollup→bucket mapping.
    """

    # Verbatim gh 2.45.0 behavior for `gh pr checks ... --json ...`.
    _UNKNOWN_FLAG_STDERR = (
        "unknown flag: --json\n"
        "\n"
        "Usage:  gh pr checks [<number> | <url> | <branch>] [flags]\n"
    )

    def setUp(self) -> None:
        _assert_peer_isolation()

    def _fake_run(
        self,
        *,
        rollup_sequence: "list | None",
        checks_calls: "list | None" = None,
    ):
        """`subprocess.run` stub for an old-gh environment.

        * `gh pr checks ... --json ...` → exit 1, empty stdout,
          "unknown flag: --json" on stderr (recorded in `checks_calls`).
        * `gh pr view --json statusCheckRollup` → next entry of
          `rollup_sequence` (last entry reused); an entry of ``None``
          simulates an unreadable rollup (empty stdout, exit 1).
        * `gh pr view --json number` / `headRefOid` → benign defaults.
        """
        idx = {"i": 0}

        def fake_run(cmd, *args, **kwargs):
            if "checks" in cmd and "--json" in cmd:
                if checks_calls is not None:
                    checks_calls.append(list(cmd))
                return mock.Mock(
                    returncode=1,
                    stdout="",
                    stderr=self._UNKNOWN_FLAG_STDERR,
                )
            if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
                jval = cmd[cmd.index("--json") + 1]
                if jval in ("number", "headRefOid"):
                    return mock.Mock(returncode=0, stdout="{}", stderr="")
                if jval == "statusCheckRollup":
                    seq = rollup_sequence or [None]
                    i = idx["i"]
                    entry = seq[i] if i < len(seq) else seq[-1]
                    if i < len(seq) - 1:
                        idx["i"] = i + 1
                    if entry is None:
                        return mock.Mock(returncode=1, stdout="", stderr="")
                    return mock.Mock(
                        returncode=0,
                        stdout=json.dumps({"statusCheckRollup": entry}),
                        stderr="",
                    )
            return mock.Mock(returncode=0)

        return fake_run

    # -- unit level: _fetch_checks ------------------------------------

    def test_unknown_json_flag_falls_back_to_rollup(self) -> None:
        """The unknown-flag probe is answered from the rollup, in the
        same bucket vocabulary the native probe would have used."""
        rollup = [{"__typename": "CheckRun", "name": "test",
                   "status": "COMPLETED", "conclusion": "SUCCESS"}]
        fake_run = self._fake_run(rollup_sequence=[rollup])
        with mock.patch.object(pr_watch, "_CHECKS_JSON_SUPPORTED", None), \
             mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
            checks = pr_watch._fetch_checks(739, "octo/repo")
            self.assertEqual(
                checks,
                [{"bucket": "pass", "state": "COMPLETED", "name": "test"}],
            )
            self.assertIs(pr_watch._CHECKS_JSON_SUPPORTED, False)

    def test_fallback_is_cached_after_first_unknown_flag(self) -> None:
        """After the first unknown-flag failure the broken subprocess is
        never spawned again — later probes go straight to the rollup."""
        rollup = [{"__typename": "CheckRun", "name": "test",
                   "status": "COMPLETED", "conclusion": "SUCCESS"}]
        checks_calls: list = []
        fake_run = self._fake_run(
            rollup_sequence=[rollup], checks_calls=checks_calls,
        )
        with mock.patch.object(pr_watch, "_CHECKS_JSON_SUPPORTED", None), \
             mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
            pr_watch._fetch_checks(739, "octo/repo")
            pr_watch._fetch_checks(739, "octo/repo")
            pr_watch._fetch_checks(739, "octo/repo")
        self.assertEqual(len(checks_calls), 1)

    def test_unknown_flag_with_unreadable_rollup_is_none(self) -> None:
        """Fallback active but the rollup is ALSO unreadable → ``None``
        (the continued-fetch-failure → `indeterminate` path is kept for
        genuine outages)."""
        fake_run = self._fake_run(rollup_sequence=[None])
        with mock.patch.object(pr_watch, "_CHECKS_JSON_SUPPORTED", None), \
             mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
            self.assertIsNone(pr_watch._fetch_checks(739, "octo/repo"))

    def test_transient_unparseable_probe_does_not_flip_cache(self) -> None:
        """An unparseable probe WITHOUT the unknown-flag signature (a
        network blip / malformed stdout) keeps the existing transient
        semantics: ``None`` now, native probe retried next time."""
        def fake_run(cmd, *args, **kwargs):
            if "checks" in cmd and "--json" in cmd:
                return mock.Mock(returncode=1, stdout="", stderr="HTTP 502")
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        with mock.patch.object(pr_watch, "_CHECKS_JSON_SUPPORTED", None), \
             mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run):
            self.assertIsNone(pr_watch._fetch_checks(739, "octo/repo"))
            self.assertIsNone(pr_watch._CHECKS_JSON_SUPPORTED)

    # -- end to end: the pinned 306s reproduction, now green ----------

    def test_old_gh_green_ci_resolves_passed_not_indeterminate(self) -> None:
        """The exact field failure, pinned: single check 'test', CI goes
        green, gh has no `pr checks --json`. Pre-#739 this recorded
        `indeterminate` after burning the full 300s resolver budget
        (duration_sec=306 / probe_attempts=13 / retry_recommended on
        all observed PRs). It must now resolve `passed` via the rollup
        fallback, with exactly ONE doomed native-probe spawn."""
        pending = [{"__typename": "CheckRun", "name": "test",
                    "status": "IN_PROGRESS", "conclusion": None}]
        green = [{"__typename": "CheckRun", "name": "test",
                  "status": "COMPLETED", "conclusion": "SUCCESS"}]
        checks_calls: list = []
        sleeps: list = []
        # Startup eval sees pending (watcher spawns right after PR
        # creation — the reason #719 alone could not cover this bug),
        # one fallback poll still pending, then green.
        fake_run = self._fake_run(
            rollup_sequence=[pending, pending, green],
            checks_calls=checks_calls,
        )
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "_CHECKS_JSON_SUPPORTED", None), \
                 mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", sleeps.append), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 40.0]):
                rc = pr_watch.main([
                    "--pr", "739", "--repo", "octo/repo", "--interval", "30",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(_count_ci_events(journal), 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(rec["total_checks"], 1)
            self.assertEqual(rec["fail_count"], 0)
            self.assertEqual(rec["pending_count"], 0)
            self.assertNotIn("retry_recommended", rec)
            # One unknown-flag spawn, then cached: never retried.
            self.assertEqual(len(checks_calls), 1)
            # Still-pending fallback observation polls at --interval
            # cadence (the self-poll loop, not the bounded resolver).
            self.assertEqual(sleeps, [30])

    def test_old_gh_red_ci_resolves_failed(self) -> None:
        """Same environment, red CI: the fallback must report an honest
        `failed`, not `indeterminate`."""
        pending = [{"__typename": "CheckRun", "name": "test",
                    "status": "IN_PROGRESS", "conclusion": None}]
        red = [{"__typename": "CheckRun", "name": "test",
                "status": "COMPLETED", "conclusion": "FAILURE"}]
        fake_run = self._fake_run(rollup_sequence=[pending, red])
        with TempDir() as tmp:
            journal = tmp / ".state" / "state.db"
            with mock.patch.object(pr_watch, "_CHECKS_JSON_SUPPORTED", None), \
                 mock.patch.object(pr_watch, "JOURNAL_PATH", journal), \
                 mock.patch.object(pr_watch, "_notify_peer", return_value=False), \
                 mock.patch.object(pr_watch.shutil, "which", return_value="/usr/bin/gh"), \
                 mock.patch.object(pr_watch.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(pr_watch.time, "sleep", return_value=None), \
                 mock.patch.object(pr_watch.time, "monotonic",
                                   side_effect=[0.0, 35.0]):
                rc = pr_watch.main([
                    "--pr", "739", "--repo", "octo/repo", "--interval", "30",
                ])
            self.assertEqual(rc, 1)
            rec = _read_ci_event(journal)
            self.assertEqual(rec["status"], "failed")
            self.assertEqual(rec["fail_count"], 1)


if __name__ == "__main__":
    unittest.main()
