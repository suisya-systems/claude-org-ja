"""Tests for tools/watcher_restart_guard.py (Refs #978).

The centrepiece is :class:`TestIncident978`, which replays the incident
itself: push -> watcher -> red CI -> push again with no restart. Every
other case exists to pin one of the asymmetries the module docstring
argues for, because those are exactly the places where a well-meaning
simplification would turn the guard back into "a pane is present".
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import journal_append, watcher_restart_guard as guard  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402

REPO = "suisya-systems/claude-org-ja"
OTHER_REPO = "suisya-systems/renga"


def _ts(seconds: int) -> str:
    """A deterministic ISO-8601 UTC stamp, ``seconds`` past a fixed epoch."""
    return f"2026-08-29T10:{seconds // 60:02d}:{seconds % 60:02d}.000Z"


class DbCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "state.db"
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)
        apply_schema(self.conn)
        self.conn.commit()

    def _run(self, argv: list) -> "tuple[int, str, str]":
        """Invoke the CLI against this case's DB, capturing both streams."""
        out, err = io.StringIO(), io.StringIO()
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = guard.main(["--db-path", str(self.db_path)] + argv)
        finally:
            sys.stdout, sys.stderr = stdout, stderr
        return code, out.getvalue(), err.getvalue()

    def add_event(self, kind: str, payload: dict, occurred_at: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES (?, ?, ?, ?)",
            (occurred_at, "secretary", kind, json.dumps(payload)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_event_raw(self, kind: str, payload_json: str, occurred_at: str) -> None:
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES (?, 'secretary', ?, ?)",
            (occurred_at, kind, payload_json),
        )
        self.conn.commit()

    def add_run(
        self,
        task_id: str,
        pr_url: str | None = None,
        pr_state: str | None = "open",
        status: str = "in_use",
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO projects (id, slug, display_name) "
            "VALUES (1, 'claude-org-ja', 'claude-org-ja')"
        )
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status, "
            "pr_url, pr_state) VALUES (?, 1, 'A', ?, ?, ?, ?)",
            (task_id, task_id, status, pr_url, pr_state),
        )
        self.conn.commit()

    # convenience emitters -------------------------------------------------

    def push(self, task: str, commit: str, at: str) -> int:
        return self.add_event(
            "fix_pushed", {"task": task, "branch": "fix/x", "commit": commit}, at
        )

    def watcher(self, pr, at: str, repo=REPO, pane_id: str = "%9") -> int:
        payload: dict = {"pr": pr, "pane_id": pane_id}
        if repo is not None:
            payload["repo"] = repo
        return self.add_event("pr_watch_pane_started", payload, at)

    def ci(self, pr, status: str, head, at: str, repo=REPO) -> int:
        payload: dict = {"pr": pr, "status": status, "head": head}
        if repo is not None:
            payload["repo"] = repo
        return self.add_event("ci_completed", payload, at)


class TestIncident978(DbCase):
    """The exact 2026-08-29 sequence, replayed end to end."""

    TASK = "ja-978-watcher-restart-guard"

    def _replay_up_to_second_push(self) -> None:
        self.push(self.TASK, "c1c1c1c1c1c1", _ts(10))
        self.watcher(73, _ts(20))
        self.ci(73, "failed", "c1c1c1c", _ts(60))
        self.push(self.TASK, "c2c2c2c2c2c2", _ts(90))

    def test_second_push_without_restart_is_stale(self) -> None:
        self._replay_up_to_second_push()
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual(v.verdict, "stale")
        self.assertEqual(v.exit_code, 3)
        self.assertTrue(v.tripped)
        # The evidence, not just the label, must be in the output.
        self.assertEqual(v.baseline["commit"], "c2c2c2c2c2c2")
        self.assertEqual(v.watcher["occurred_at"], _ts(20))
        self.assertIn("/pr-watch-pane 73", v.remediation)

    def test_restarting_the_watcher_flips_it_to_live(self) -> None:
        self._replay_up_to_second_push()
        self.watcher(73, _ts(100), pane_id="%12")
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual(v.verdict, "live")
        self.assertEqual(v.exit_code, 0)
        self.assertFalse(v.tripped)
        self.assertIsNone(v.remediation)
        self.assertEqual(v.watcher["pane_id"], "%12")

    def test_a_live_pane_is_not_the_predicate(self) -> None:
        """No pane state is consulted at all -- only event ordering."""
        self._replay_up_to_second_push()
        stale = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        # The same watcher row, moved after the push, is the only difference
        # between the incident and a healthy run.
        self.conn.execute(
            "UPDATE events SET occurred_at = ? WHERE kind = 'pr_watch_pane_started'",
            (_ts(120),),
        )
        self.conn.commit()
        healthy = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((stale.verdict, healthy.verdict), ("stale", "live"))


class TestVerdicts(DbCase):
    TASK = "t"

    def test_missing_when_no_watcher_row_exists(self) -> None:
        self.push(self.TASK, "c1", _ts(10))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("missing", 3))
        self.assertEqual(v.watcher_count, 0)

    def test_completed_when_terminal_head_corroborates_the_push(self) -> None:
        self.push(self.TASK, "abcdef1234567890", _ts(10))
        self.watcher(73, _ts(20))
        self.ci(73, "passed", "abcdef1", _ts(60))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))
        self.assertEqual(v.terminal["kind"], "ci_completed")

    def test_completed_on_merge_without_head_corroboration(self) -> None:
        self.push(self.TASK, "abcdef1234567890", _ts(10))
        self.watcher(73, _ts(20))
        self.add_event("pr_merged", {"pr": 73, "repo": REPO}, _ts(60))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))

    def test_ended_stale_head_when_the_verdict_is_for_an_older_head(self) -> None:
        # The watcher restarted after the push, but the verdict it delivered
        # describes the previous head (a race the watcher can genuinely hit).
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
        self.watcher(73, _ts(20))
        self.ci(73, "failed", "aaaaaaa", _ts(60))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("ended_stale_head", 3))
        self.assertIn("/pr-watch-pane 73", v.remediation)

    def test_inconclusive_ci_status_trips_even_on_the_current_head(self) -> None:
        """The watch stopped without answering -- head agreement is irrelevant.

        ``pr_watch`` records ``head`` for these too, so a head-only rule
        reads them as "the verdict is in for the commit you pushed" and
        exits 0 while CI is still running and the pane is gone.
        ``indeterminate`` even carries ``retry_recommended``: the watcher
        itself is asking to be restarted.
        """
        for status in ("incomplete", "indeterminate", "canceled"):
            with self.subTest(status=status):
                self.setUp()
                self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
                self.watcher(73, _ts(20))
                self.ci(73, status, "bbbbbbb", _ts(60))
                v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
                self.assertEqual((v.verdict, v.exit_code), ("ended_inconclusive", 3))
                self.assertTrue(v.tripped)
                self.assertIn("/pr-watch-pane 73", v.remediation)

    def test_unrecorded_ci_status_trips(self) -> None:
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
        self.watcher(73, _ts(20))
        self.add_event(
            "ci_completed", {"pr": 73, "repo": REPO, "head": "bbbbbbb"}, _ts(60)
        )
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("ended_inconclusive", 3))

    def test_legacy_result_key_is_read_as_the_status(self) -> None:
        """A dozen rows in the live DB carry ``result`` instead of ``status``."""
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
        self.watcher(73, _ts(20))
        self.add_event(
            "ci_completed",
            {"pr": 73, "repo": REPO, "head": "bbbbbbb", "result": "passed"},
            _ts(60),
        )
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))

    def test_null_head_with_a_real_verdict_is_completed(self) -> None:
        """An unreadable head is an absent comparison, never a mismatch.

        About a third of ``ci_completed`` rows carry no ``head``. Tripping
        on that absence would fire on healthy watches, and an alarm that
        fires on the normal case is one the reader learns to skim past --
        the same reflex that produced the incident.
        """
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
        self.watcher(73, _ts(20))
        self.ci(73, "passed", None, _ts(60))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))
        self.assertIsNone(v.terminal["head"])

    def test_watch_abort_kinds_are_terminal_and_warn(self) -> None:
        for kind in ("pr_watch_aborted", "pr_merge_watch_timeout"):
            with self.subTest(kind=kind):
                self.setUp()
                self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
                self.watcher(73, _ts(20))
                self.add_event(kind, {"pr": 73, "repo": REPO}, _ts(60))
                v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
                self.assertEqual((v.verdict, v.exit_code), ("ended_inconclusive", 3))

    def test_no_baseline_edge_reports_null_baseline(self) -> None:
        self.watcher(73, _ts(20))
        v = guard.evaluate(self.conn, 73, REPO, task_id=None)
        self.assertEqual((v.verdict, v.exit_code), ("live", 0))
        self.assertIsNone(v.baseline)

    def test_no_baseline_with_terminal_is_completed(self) -> None:
        self.watcher(73, _ts(20))
        self.ci(73, "passed", "abcdef1", _ts(60))
        v = guard.evaluate(self.conn, 73, REPO, task_id=None)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))
        self.assertIsNone(v.baseline)

    def test_no_baseline_and_no_watcher_is_missing(self) -> None:
        v = guard.evaluate(self.conn, 73, REPO, task_id=None)
        self.assertEqual((v.verdict, v.exit_code), ("missing", 3))

    def test_unresolved_pr_number(self) -> None:
        v = guard.evaluate(self.conn, None, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("unresolved", 2))

    def test_terminal_before_the_watcher_needs_an_earlier_watcher_to_disown(self) -> None:
        """Who owns a terminal that precedes the only watcher row?

        With NO earlier ``pr_watch_pane_started`` on record there was no
        previous watch to attribute it to (the launch row is mandatory), so
        it belongs to this watcher, whose row simply landed late -- and the
        watch is over. Reading it as "someone else's old verdict, we are
        still live" is the race that lets a finished watch look alive.
        """
        self.push(self.TASK, "c2c2c2c", _ts(10))
        self.ci(73, "failed", "c1c1c1c", _ts(20))
        self.watcher(73, _ts(30))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("ended_stale_head", 3))
        self.assertTrue(v.terminal_precedes_watcher_row)

    def test_an_earlier_watcher_disowns_it_and_the_restart_is_live(self) -> None:
        # Same events, plus the previous watch's launch row: now the
        # terminal has an owner and the newest watcher is a real restart.
        self.push(self.TASK, "c2c2c2c", _ts(10))
        self.watcher(73, _ts(15), pane_id="%1")
        self.ci(73, "failed", "c1c1c1c", _ts(20))
        self.watcher(73, _ts(30), pane_id="%2")
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual(v.verdict, "live")
        self.assertIsNone(v.terminal)


class TestNormalisation(DbCase):
    TASK = "t"

    def test_str_and_int_pr_payloads_both_match(self) -> None:
        self.push(self.TASK, "c1", _ts(10))
        self.watcher("73", _ts(20))
        self.assertEqual(
            guard.evaluate(self.conn, 73, REPO, task_id=self.TASK).verdict, "live"
        )
        self.setUp()
        self.push(self.TASK, "c1", _ts(10))
        self.watcher(73, _ts(20))
        self.assertEqual(
            guard.evaluate(self.conn, "#73", REPO, task_id=self.TASK).verdict, "live"
        )

    def test_non_integer_pr_payload_never_matches(self) -> None:
        self.push(self.TASK, "c1", _ts(10))
        self.watcher("not-a-number", _ts(20))
        self.assertEqual(
            guard.evaluate(self.conn, 73, REPO, task_id=self.TASK).verdict, "missing"
        )

    def test_repo_case_and_git_suffix_are_normalised(self) -> None:
        self.push(self.TASK, "c1", _ts(10))
        self.watcher(73, _ts(20), repo="Suisya-Systems/Claude-Org-JA.git")
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual(v.verdict, "live")

    def test_watcher_without_repo_is_not_counted_and_is_reported(self) -> None:
        self.push(self.TASK, "c1", _ts(10))
        self.watcher(73, _ts(20), repo=None)
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual(v.verdict, "missing")
        self.assertEqual(len(v.ignored_unknown_repo), 1)
        self.assertEqual(v.watcher_count, 0)

    def test_terminal_without_repo_ends_the_watch_but_cannot_certify_it(self) -> None:
        """Admitted as evidence the watch is over; refused as a clean bill.

        Both halves matter. Dropping the row would leave a finished watch
        reading as ``live``; accepting it as a conclusion would let a
        same-numbered PR in another repository certify this one -- and 45
        live ``pr_merged`` rows carry no repo, so that row shape is real.
        """
        self.push(self.TASK, "abcdef1234", _ts(10))
        self.watcher(73, _ts(20))
        self.ci(73, "passed", "abcdef1", _ts(60), repo=None)
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("ended_inconclusive", 3))
        self.assertEqual(v.terminal["kind"], "ci_completed")
        self.assertIsNone(v.terminal["repo"])

    def test_a_repo_matched_terminal_still_certifies(self) -> None:
        self.push(self.TASK, "abcdef1234", _ts(10))
        self.watcher(73, _ts(20))
        self.ci(73, "passed", "abcdef1", _ts(60))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))

    def test_legacy_result_key_is_echoed_in_the_evidence(self) -> None:
        self.push(self.TASK, "abcdef1234", _ts(10))
        self.watcher(73, _ts(20))
        self.add_event(
            "ci_completed",
            {"pr": 73, "repo": REPO, "head": "abcdef1", "result": "passed"},
            _ts(60),
        )
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        # The evidence must not print null next to a verdict derived from it.
        self.assertEqual(v.terminal["status"], "passed")
        self.assertEqual(v.verdict, "completed")

    def test_same_millisecond_ordering_is_resolved_by_event_id(self) -> None:
        # Watcher row written AFTER the push in the same millisecond: the
        # autoincrement id is the only thing that distinguishes them.
        self.push(self.TASK, "c1", _ts(10))
        self.watcher(73, _ts(10))
        self.assertEqual(
            guard.evaluate(self.conn, 73, REPO, task_id=self.TASK).verdict, "live"
        )

        self.setUp()
        # Same millisecond, opposite insertion order: the push wins.
        self.watcher(73, _ts(10))
        self.push(self.TASK, "c1", _ts(10))
        self.assertEqual(
            guard.evaluate(self.conn, 73, REPO, task_id=self.TASK).verdict, "stale"
        )

    def test_cross_repo_pr_number_collision_does_not_leak(self) -> None:
        self.push("ja-task", "aaaaaaa", _ts(10))
        self.push("renga-task", "bbbbbbb", _ts(11))
        # Only the renga PR got a restarted watcher.
        self.watcher(73, _ts(20), repo=OTHER_REPO)
        ja = guard.evaluate(self.conn, 73, REPO, task_id="ja-task")
        renga = guard.evaluate(self.conn, 73, OTHER_REPO, task_id="renga-task")
        self.assertEqual(ja.verdict, "missing")
        self.assertEqual(renga.verdict, "live")

    def test_baseline_is_scoped_to_the_task(self) -> None:
        self.push("other-task", "zzzz", _ts(90))
        self.push(self.TASK, "c1", _ts(10))
        self.watcher(73, _ts(20))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual(v.verdict, "live")
        self.assertEqual(v.baseline["commit"], "c1")


class TestResolution(DbCase):
    def test_resolves_pr_and_repo_from_run_pr_url(self) -> None:
        self.add_run("t", pr_url=f"https://github.com/{REPO}/pull/73")
        res = guard.resolve_for_task(self.conn, "t")
        self.assertEqual((res.pr, res.repo), ("73", REPO))

    def test_falls_back_to_pr_opened_event(self) -> None:
        self.add_run("t", pr_url=None)
        self.add_event(
            "pr_opened",
            {"task": "t", "pr": 73, "url": f"https://github.com/{REPO}/pull/73"},
            _ts(5),
        )
        res = guard.resolve_for_task(self.conn, "t")
        self.assertEqual((res.pr, res.repo), ("73", REPO))

    def test_unresolved_when_nothing_names_a_pr(self) -> None:
        self.add_run("t", pr_url=None)
        res = guard.resolve_for_task(self.conn, "t")
        self.assertIsNone(res.pr)


class TestAudit(DbCase):
    def test_unreadable_pr_url_does_not_exit_0(self) -> None:
        """An open PR that could not be CHECKED is not a clean bill.

        `unresolved` is not a tripping verdict, so a plain "any tripped?"
        exit rule reports the whole audit healthy while that PR was never
        examined -- exactly the silent miss this tool removes.
        """
        self.add_run("t", pr_url="not-a-pr-url")
        code, out, _err = self._run(["audit"])
        self.assertEqual(code, guard.EXIT_UNRESOLVED)
        self.assertIn("unresolved", out)

    def test_a_trip_outranks_an_unresolved_run(self) -> None:
        self.add_run("bad-url", pr_url="not-a-pr-url")
        self.add_run("t", pr_url=f"https://github.com/{REPO}/pull/73")
        self.push("t", "c1", _ts(10))
        self.watcher(73, _ts(5))
        self.assertEqual(self._run(["audit"])[0], guard.EXIT_TRIPPED)

    def test_exits_3_when_any_run_trips(self) -> None:
        self.add_run("t", pr_url=f"https://github.com/{REPO}/pull/73")
        self.push("t", "c1", _ts(10))
        self.watcher(73, _ts(5))  # started BEFORE the push
        results = guard.audit(self.conn)
        self.assertEqual([v.verdict for v in results], ["stale"])
        self.assertTrue(any(v.tripped for v in results))
        self.assertEqual(self._cli(["audit"]), 3)

    def test_exits_0_when_none_trip(self) -> None:
        self.add_run("t", pr_url=f"https://github.com/{REPO}/pull/73")
        self.push("t", "c1", _ts(10))
        self.watcher(73, _ts(20))
        self.assertEqual(self._cli(["audit"]), 0)

    def test_terminal_and_merged_runs_are_excluded(self) -> None:
        self.add_run(
            "done",
            pr_url=f"https://github.com/{REPO}/pull/71",
            pr_state="merged",
            status="completed",
        )
        self.add_run(
            "abandoned",
            pr_url=f"https://github.com/{REPO}/pull/72",
            pr_state="open",
            status="abandoned",
        )
        self.assertEqual(guard.audit(self.conn), [])
        self.assertEqual(self._cli(["audit"]), 0)

    def test_null_pr_state_is_included(self) -> None:
        self.add_run(
            "t", pr_url=f"https://github.com/{REPO}/pull/73", pr_state=None
        )
        results = guard.audit(self.conn)
        self.assertEqual([v.verdict for v in results], ["missing"])

    def _cli(self, argv: list) -> int:
        buf = io.StringIO()
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = buf
        try:
            return guard.main(["--db-path", str(self.db_path)] + argv)
        finally:
            sys.stdout, sys.stderr = stdout, stderr


class TestCli(DbCase):
    TASK = "t"

    def test_check_by_pr_json_output_carries_the_evidence(self) -> None:
        self.push(self.TASK, "c1", _ts(10))
        self.watcher(73, _ts(20))
        code, out, _ = self._run(["check", "--pr", "73", "--repo", REPO, "--json"])
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["verdict"], "live")
        # No run row holds PR 73, so task_for_pr() recovers nothing and the
        # baseline is genuinely absent -- reported, not silently assumed.
        self.assertIsNone(payload["baseline"])
        self.assertEqual(payload["watcher"]["pane_id"], "%9")

    def test_check_by_task_resolves_pr_and_baseline(self) -> None:
        self.add_run(self.TASK, pr_url=f"https://github.com/{REPO}/pull/73")
        self.push(self.TASK, "c1", _ts(10))
        self.watcher(73, _ts(5))
        code, out, _ = self._run(["check", "--task", self.TASK, "--json"])
        payload = json.loads(out)
        self.assertEqual(code, 3)
        self.assertEqual(payload["verdict"], "stale")
        self.assertEqual(payload["baseline"]["commit"], "c1")

    def test_check_by_task_unresolved_exits_2(self) -> None:
        self.add_run(self.TASK, pr_url=None)
        code, _out, _err = self._run(["check", "--task", self.TASK, "--json"])
        self.assertEqual(code, 2)

    def test_check_requires_exactly_one_selector(self) -> None:
        self.assertEqual(self._run(["check"])[0], 1)
        self.assertEqual(
            self._run(["check", "--pr", "73", "--task", self.TASK])[0], 1
        )

    def test_text_output_is_ascii(self) -> None:
        self.push(self.TASK, "c1", _ts(10))
        _code, out, _err = self._run(["check", "--pr", "73", "--repo", REPO])
        out.encode("ascii")  # cp932 consoles: no em-dashes, no smart quotes
        self.assertIn("verdict: missing", out)


class TestJournalAppendWiring(DbCase):
    TASK = "t"

    def _append(self, argv: list, env: "dict | None" = None) -> "tuple[int, str]":
        err = io.StringIO()
        stderr = sys.stderr
        sys.stderr = err
        saved = {k: os.environ.get(k) for k in (env or {})}
        try:
            for k, v in (env or {}).items():
                os.environ[k] = v
            code = journal_append.main(["--db-path", str(self.db_path)] + argv)
        finally:
            sys.stderr = stderr
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return code, err.getvalue()

    def _rows(self, kind: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = ?", (kind,)
        ).fetchone()[0]

    def setUp(self) -> None:
        super().setUp()
        self.add_run(self.TASK, pr_url=f"https://github.com/{REPO}/pull/73")

    def test_fix_pushed_prints_the_next_action_block(self) -> None:
        code, err = self._append(
            ["fix_pushed", f"task={self.TASK}", "commit=c1"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self._rows("fix_pushed"), 1)
        self.assertIn("[watcher-guard] NEXT ACTION", err)
        self.assertIn("/pr-watch-pane 73", err)

    def test_guard_confirms_quietly_when_a_watcher_is_already_live(self) -> None:
        # A watcher started at the same timestamp but a later event id still
        # counts as after the push only if it is inserted after it, so this
        # one is dated later on purpose.
        code, err = self._append(["fix_pushed", f"task={self.TASK}", "commit=c1"])
        self.assertEqual(code, 0)
        self.watcher(73, "2099-01-01T00:00:00.000Z")
        code, err = self._append(["fix_pushed", f"task={self.TASK}", "commit=c2"])
        self.assertEqual(code, 0)
        self.assertIn("[watcher-guard]", err)
        self.assertNotIn("NEXT ACTION", err)

    def test_guard_failure_never_breaks_the_write(self) -> None:
        def boom(*a, **k):
            raise RuntimeError("guard exploded")

        original = guard.post_push_check
        guard.post_push_check = boom
        self.addCleanup(setattr, guard, "post_push_check", original)
        code, err = self._append(["fix_pushed", f"task={self.TASK}", "commit=c1"])
        self.assertEqual(code, 0)
        self.assertEqual(self._rows("fix_pushed"), 1)
        self.assertNotIn("watcher-guard", err)

    def test_guard_is_silent_for_other_event_kinds(self) -> None:
        code, err = self._append(["pr_opened", f"task={self.TASK}", "pr=73"])
        self.assertEqual(code, 0)
        self.assertNotIn("watcher-guard", err)

    def test_env_off_suppresses_the_guard(self) -> None:
        for value in ("off", "0", "FALSE"):
            with self.subTest(value=value):
                code, err = self._append(
                    ["fix_pushed", f"task={self.TASK}", "commit=c1"],
                    env={"ORG_WATCHER_GUARD": value},
                )
                self.assertEqual(code, 0)
                self.assertNotIn("watcher-guard", err)

    def test_hidden_flag_suppresses_the_guard(self) -> None:
        code, err = self._append(
            ["fix_pushed", f"task={self.TASK}", "commit=c1", "--no-watcher-guard"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self._rows("fix_pushed"), 1)
        self.assertNotIn("watcher-guard", err)


class TestPostPushCheckUnit(DbCase):
    def test_returns_none_without_a_task(self) -> None:
        self.assertIsNone(guard.post_push_check(self.conn, {}, stream=io.StringIO()))

    def test_returns_none_when_the_pr_cannot_be_resolved(self) -> None:
        self.add_run("t", pr_url=None)
        self.assertIsNone(
            guard.post_push_check(self.conn, {"task": "t"}, stream=io.StringIO())
        )

    def test_notice_is_ascii(self) -> None:
        self.add_run("t", pr_url=f"https://github.com/{REPO}/pull/73")
        buf = io.StringIO()
        v = guard.post_push_check(self.conn, {"task": "t"}, stream=buf)
        self.assertEqual(v.verdict, "missing")
        buf.getvalue().encode("ascii")


class TestHelpers(unittest.TestCase):
    def test_canonical_pr(self) -> None:
        for raw, want in (
            (73, "73"),
            ("73", "73"),
            (" #73 ", "73"),
            ("073", "73"),
            (None, None),
            (True, None),
            ("abc", None),
            ("", None),
            ({}, None),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(guard.canonical_pr(raw), want)

    def test_canonical_repo(self) -> None:
        self.assertEqual(guard.canonical_repo("A/B.git"), "a/b")
        self.assertEqual(guard.canonical_repo("  A/B  "), "a/b")
        self.assertIsNone(guard.canonical_repo(None))
        self.assertIsNone(guard.canonical_repo("  "))

    def test_head_corroborates_in_both_directions(self) -> None:
        self.assertTrue(guard.head_corroborates("abcdef1234", "abcdef1"))
        self.assertTrue(guard.head_corroborates("abcdef1", "abcdef1234"))
        self.assertFalse(guard.head_corroborates("abcdef1", "999999"))
        self.assertFalse(guard.head_corroborates(None, "abcdef1"))
        self.assertFalse(guard.head_corroborates("abcdef1", None))

    def test_guard_disabled(self) -> None:
        for value, want in (
            ("off", True),
            ("0", True),
            ("False", True),
            ("on", False),
            ("", False),
        ):
            with self.subTest(value=value):
                self.assertIs(
                    guard.guard_disabled({"ORG_WATCHER_GUARD": value}), want
                )
        self.assertFalse(guard.guard_disabled({}))

    def test_exit_code_table_is_total(self) -> None:
        for verdict in guard.TRIPPING_VERDICTS:
            self.assertEqual(guard.VERDICT_EXIT_CODES[verdict], 3)

    def test_remediation_names_only_the_canonical_route(self) -> None:
        text = guard.remediation_text("73", REPO)
        self.assertIn("/pr-watch-pane 73", text)
        self.assertIn("pr-ci-watch.md", text)
        text.encode("ascii")


class TestSchemaAssumptions(DbCase):
    def test_events_columns_used_by_the_guard_exist(self) -> None:
        cols = {
            r[1]
            for r in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        self.assertTrue({"id", "occurred_at", "kind", "payload_json"} <= cols)

    def test_non_object_payload_is_tolerated(self) -> None:
        # json_valid() is a CHECK on the column, so a payload can never be
        # broken JSON -- but it CAN be a valid non-object (a list, a bare
        # string). The loader must skip those rather than raise.
        self.add_event_raw("pr_watch_pane_started", "[1, 2]", _ts(20))
        self.add_event_raw("ci_completed", '"nope"', _ts(30))
        v = guard.evaluate(self.conn, 73, REPO)
        self.assertEqual(v.verdict, "missing")


class TestWatchEndingBeforeItsLaunchRow(DbCase):
    """The launch row is appended AFTER the pane is spawned and verified.

    A watch that finds CI already red can finish in that gap, so its
    terminal event sorts before its own ``pr_watch_pane_started``. A
    watcher-anchored window finds nothing after the launch row and would
    answer ``live`` forever for a watch that is already over.
    """

    TASK = "t"

    def test_terminal_recorded_before_the_launch_row_still_counts(self) -> None:
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
        self.ci(73, "failed", "bbbbbbb", _ts(20))     # watch finished first
        self.watcher(73, _ts(30))                     # launch row lands late
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))
        self.assertTrue(v.terminal_precedes_watcher_row)
        self.assertEqual(v.terminal["kind"], "ci_completed")

    def test_the_same_race_trips_when_the_watch_gave_no_verdict(self) -> None:
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
        self.ci(73, "indeterminate", "bbbbbbb", _ts(20))
        self.watcher(73, _ts(30))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("ended_inconclusive", 3))

    def test_a_restart_after_a_verdict_is_still_live(self) -> None:
        """The documented response to ``indeterminate`` must not false-alarm.

        Here an EARLIER watcher owns the terminal, so the newest one is a
        genuine restart rather than a late launch row.
        """
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(10))
        self.watcher(73, _ts(20), pane_id="%1")
        self.ci(73, "indeterminate", "bbbbbbb", _ts(30))
        self.watcher(73, _ts(40), pane_id="%2")
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("live", 0))
        self.assertFalse(v.terminal_precedes_watcher_row)
        self.assertEqual(v.watcher["pane_id"], "%2")

    def test_a_spent_earlier_watcher_cannot_disown_the_new_terminal(self) -> None:
        """An earlier watcher only disowns a terminal it has not consumed.

        watcher1 -> failed -> push -> watcher2's terminal -> watcher2's
        launch row. A rule that treats *any* earlier launch as a possible
        owner hands watcher2's terminal to watcher1 -- whose own verdict
        already came and went -- and reports the stopped watcher2 as live.
        """
        self.watcher(73, _ts(0), pane_id="%1")
        self.ci(73, "failed", "aaaaaaa", _ts(10))
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(20))
        self.ci(73, "failed", "bbbbbbb", _ts(30))     # watcher2's verdict
        self.watcher(73, _ts(40), pane_id="%2")       # watcher2's launch row
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))
        self.assertTrue(v.terminal_precedes_watcher_row)
        self.assertEqual(v.terminal["head"], "bbbbbbb")

    def test_the_same_shape_trips_when_the_new_verdict_is_inconclusive(self) -> None:
        self.watcher(73, _ts(0), pane_id="%1")
        self.ci(73, "failed", "aaaaaaa", _ts(10))
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(20))
        self.ci(73, "incomplete", "bbbbbbb", _ts(30))
        self.watcher(73, _ts(40), pane_id="%2")
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("ended_inconclusive", 3))

    def test_a_terminal_from_before_the_push_is_not_reattributed(self) -> None:
        self.push(self.TASK, "aaaaaaaaaaaa", _ts(0))
        self.ci(73, "failed", "aaaaaaa", _ts(10))
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(20))
        self.watcher(73, _ts(30))
        v = guard.evaluate(self.conn, 73, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("live", 0))
        self.assertFalse(v.terminal_precedes_watcher_row)


class TestEmittedPayloadShapes(DbCase):
    """Pin the guard to what the emitters actually write, not to the catalog.

    The event catalog documents ``fix_pushed`` as ``task, branch, commit``,
    but no row in the live DB has ever carried ``commit``: the emitters
    write ``head``. Reading only the documented key made the head
    comparison dead code and turned every healthy watch into
    ``ended_stale_head`` -- a guard that fires on the normal case, which is
    worse than no guard.
    """

    TASK = "cadenza-belt-clone-source"

    def push_with(self, key: str, sha: str, at: str) -> int:
        return self.add_event(
            "fix_pushed", {"task": self.TASK, "branch": "fix/x", key: sha}, at
        )

    def test_head_key_is_read_as_the_pushed_sha(self) -> None:
        self.push_with("head", "6ef6291", _ts(10))
        self.watcher(16, _ts(20))
        self.ci(16, "passed", "6ef6291", _ts(60))
        v = guard.evaluate(self.conn, 16, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))
        self.assertEqual(v.baseline["commit"], "6ef6291")
        self.assertEqual(v.baseline["commit_key"], "head")

    def test_head_key_still_catches_a_stale_verdict(self) -> None:
        self.push_with("head", "aaaaaaa", _ts(10))
        self.watcher(16, _ts(20))
        self.ci(16, "passed", "bbbbbbb", _ts(60))
        v = guard.evaluate(self.conn, 16, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("ended_stale_head", 3))

    def test_sha_only_in_free_text_note_is_absent_not_mismatched(self) -> None:
        # 22 of 69 live rows look exactly like this.
        self.add_event(
            "fix_pushed", {"task": self.TASK, "note": "f059c6f pushed"}, _ts(10)
        )
        self.watcher(16, _ts(20))
        self.ci(16, "passed", "6ef6291", _ts(60))
        v = guard.evaluate(self.conn, 16, REPO, task_id=self.TASK)
        self.assertEqual((v.verdict, v.exit_code), ("completed", 0))
        self.assertIsNone(v.baseline["commit"])

    def test_documented_commit_key_still_wins_when_present(self) -> None:
        self.add_event(
            "fix_pushed",
            {"task": self.TASK, "commit": "1111111", "head": "2222222"},
            _ts(10),
        )
        self.watcher(16, _ts(20))
        v = guard.evaluate(self.conn, 16, REPO, task_id=self.TASK)
        self.assertEqual(v.baseline["commit"], "1111111")
        self.assertEqual(v.baseline["commit_key"], "commit")


class TestPrFormRecoversTheBaseline(DbCase):
    """``check --pr N`` must answer the same question as ``check --task``.

    Without recovering the task from the run row the --pr form reads no
    ``fix_pushed`` at all, so the ``stale`` branch is unreachable and the
    documented shorthand exits 0 on the incident itself.
    """

    TASK = "ja-978-watcher-restart-guard"

    def _incident(self) -> None:
        self.add_run(self.TASK, pr_url=f"https://github.com/{REPO}/pull/73")
        self.watcher(73, _ts(0))
        self.ci(73, "failed", "aaaaaaa", _ts(30))
        self.push(self.TASK, "bbbbbbbbbbbb", _ts(60))

    def test_task_for_pr_finds_the_run(self) -> None:
        self._incident()
        self.assertEqual(guard.task_for_pr(self.conn, 73, REPO), self.TASK)

    def test_pr_form_reports_stale_like_the_task_form(self) -> None:
        self._incident()
        code, out, _err = self._run(["check", "--pr", "73", "--repo", REPO])
        self.assertEqual(code, 3)
        self.assertIn("verdict: stale", out)
        self.assertIn(self.TASK, out)

    def test_task_for_pr_does_not_cross_repos(self) -> None:
        self._incident()
        self.assertIsNone(guard.task_for_pr(self.conn, 73, OTHER_REPO))

    def test_unknown_pr_leaves_the_baseline_absent(self) -> None:
        self.watcher(999, _ts(20))
        v = guard.evaluate(
            self.conn, 999, REPO, task_id=guard.task_for_pr(self.conn, 999, REPO)
        )
        self.assertIsNone(v.baseline)
        self.assertEqual(v.verdict, "live")


class TestHomeRepoIsNotAWildcard(DbCase):
    """The origin fallback may only serve the one path its docstring names.

    PR numbers collide across repos. Substituting this checkout's origin on
    a path where the repo is already knowable lets a *different* repo's
    watcher certify this PR as watched.
    """

    TASK = "cross-repo-task"

    def test_post_push_check_warns_instead_of_guessing_the_repo(self) -> None:
        # A run whose PR is known only through a pr_opened event with no
        # url: the PR number resolves, the repo does not.
        self.add_run(self.TASK, pr_url=None)
        self.add_event("pr_opened", {"task": self.TASK, "pr": 73}, _ts(5))
        self.push(self.TASK, "cafebabe", _ts(10))
        # An unrelated watcher for THIS repo's PR #73.
        self.watcher(73, _ts(20), repo=REPO)

        out = io.StringIO()
        v = guard.post_push_check(
            self.conn, {"task": self.TASK, "commit": "cafebabe"}, stream=out
        )
        self.assertIsNone(v)
        self.assertIn("could not determine OWNER/REPO", out.getvalue())
        self.assertIn("/pr-watch-pane 73", out.getvalue())

    def test_task_path_does_not_fall_back_to_the_home_repo(self) -> None:
        self.add_run(self.TASK, pr_url=None)
        self.add_event("pr_opened", {"task": self.TASK, "pr": 73}, _ts(5))
        self.push(self.TASK, "cafebabe", _ts(10))
        self.watcher(73, _ts(20), repo=REPO)

        code, _out, err = self._run(["check", "--task", self.TASK])
        self.assertEqual(code, guard.EXIT_UNRESOLVED)
        self.assertIn("could not determine OWNER/REPO", err)

    def test_explicit_repo_still_works_on_the_task_path(self) -> None:
        self.add_run(self.TASK, pr_url=None)
        self.add_event("pr_opened", {"task": self.TASK, "pr": 73}, _ts(5))
        self.push(self.TASK, "cafebabe", _ts(10))
        self.watcher(73, _ts(20), repo=OTHER_REPO)

        code, out, _err = self._run(
            ["check", "--task", self.TASK, "--repo", OTHER_REPO]
        )
        self.assertEqual(code, 0)
        self.assertIn("verdict: live", out)


if __name__ == "__main__":
    unittest.main()
