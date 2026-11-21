"""Acceptance tests for tools/belt_slots.py (Issue 1295).

The conveyor counts a slot as taken only while a worker is actually doing
work. A run that is only waiting on a human (merge approval after CI green,
an escalated decision, approval of a completion report) does not take a
slot, and a waiting run in ``review`` whose pane is still open is reported
as parkable.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.state_db import apply_schema, connect  # noqa: E402

REPO = "suisya-systems/claude-org-ja"
T0 = datetime(2026, 11, 22, 10, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.bs = importlib.import_module("tools.belt_slots")
        except ImportError as exc:  # before the tool exists: fail, not error
            self.fail(f"tools/belt_slots.py is missing: {exc}")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.conn = connect(self.dir / "state.db")
        self.addCleanup(self.conn.close)
        apply_schema(self.conn)
        self.conn.execute(
            "INSERT INTO projects (id, slug, display_name) VALUES (1, 'ja', 'ja')"
        )
        self.conn.commit()
        self.decisions = self.dir / "pending_decisions.json"
        self.contract = self.dir / "scope-contract.md"
        self.idle_state = self.dir / "belt-idle.json"

    # -- fixtures ---------------------------------------------------------
    def run_(self, task, status, pr=None):
        url = f"https://github.com/{REPO}/pull/{pr}" if pr else None
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status, "
            "pr_url, pr_state) VALUES (?, 1, 'B', ?, ?, ?, ?)",
            (task, task, status, url, "open" if pr else None),
        )
        self.conn.commit()

    def ev(self, kind, payload, at):
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES (?, 'secretary', ?, ?)",
            (at, kind, json.dumps(payload)),
        )
        self.conn.commit()

    def spawned(self, task, at, wdir="/nonexistent", pane="%5"):
        self.ev("worker_spawned", {"worker": f"worker-{task}", "dir": wdir, "task": task}, at)
        self.ev("worker_spawn_verified", {"task": task, "worker": f"worker-{task}", "pane_id": pane}, at)

    def green(self, task, pr, head="c1c1c1c1c1c1", start=1):
        self.ev("fix_pushed", {"task": task, "branch": "feat/x", "commit": head}, _ts(start))
        self.ev("pr_watch_pane_started", {"pr": pr, "repo": REPO, "pane_id": "%9"}, _ts(start + 1))
        self.ev("ci_completed", {"pr": pr, "repo": REPO, "status": "passed", "head": head[:7]}, _ts(start + 2))

    def decision(self, task, status="escalated", replied=None):
        entries = json.loads(self.decisions.read_text("utf-8")) if self.decisions.exists() else []
        entries.append({
            "task_id": task, "received_at": _ts(0), "raw_message": "q",
            "status": status, "resolved_at": None, "resolution_kind": None,
            "user_replied_at": replied,
        })
        self.decisions.write_text(json.dumps(entries), "utf-8")

    def count(self, max_parallel=3):
        return self.bs.count(self.conn, max_parallel=max_parallel, decisions_path=self.decisions)

    def waiting(self, result):
        return {w["task"]: w for w in result["waiting"]}


class CountTests(Base):
    def test_review_with_green_ci_on_current_head_does_not_take_a_slot(self):
        self.run_("a", "review", pr=10)
        self.spawned("a", _ts(0))
        self.green("a", 10)
        r = self.count()
        self.assertNotIn("a", r["busy"])
        self.assertEqual(self.waiting(r)["a"]["reason"], "merge")
        self.assertEqual(r["free_slots"], 3)
        self.assertEqual(r["parkable"], ["a"])

    def test_push_after_green_makes_it_busy_again(self):
        self.run_("a", "review", pr=10)
        self.green("a", 10)
        self.ev("fix_pushed", {"task": "a", "branch": "feat/x", "commit": "c2c2c2c2c2c2"}, _ts(10))
        r = self.count()
        self.assertIn("a", r["busy"])
        self.assertEqual(r["free_slots"], 2)

    def test_late_green_for_an_older_head_does_not_count_as_waiting(self):
        self.run_("a", "review", pr=10)
        self.ev("fix_pushed", {"task": "a", "branch": "feat/x", "commit": "c1c1c1c1c1c1"}, _ts(1))
        self.ev("fix_pushed", {"task": "a", "branch": "feat/x", "commit": "c2c2c2c2c2c2"}, _ts(2))
        self.ev("pr_watch_pane_started", {"pr": 10, "repo": REPO, "pane_id": "%9"}, _ts(3))
        self.ev("ci_completed", {"pr": 10, "repo": REPO, "status": "passed", "head": "c1c1c1c"}, _ts(4))
        r = self.count()
        self.assertIn("a", r["busy"])

    def test_ci_still_running_is_busy(self):
        self.run_("a", "review", pr=10)
        self.ev("fix_pushed", {"task": "a", "branch": "feat/x", "commit": "c1c1c1c1c1c1"}, _ts(1))
        self.ev("pr_watch_pane_started", {"pr": 10, "repo": REPO, "pane_id": "%9"}, _ts(2))
        self.assertIn("a", self.count()["busy"])

    def test_in_use_is_busy_unless_an_escalated_decision_waits_on_the_human(self):
        self.run_("a", "in_use")
        self.run_("b", "in_use")
        self.spawned("b", _ts(0))
        self.decision("b")
        r = self.count()
        self.assertIn("a", r["busy"])
        self.assertNotIn("b", r["busy"])
        self.assertEqual(self.waiting(r)["b"]["reason"], "decision")
        # mid-task decision: the pane stays, so it is never parkable
        self.assertTrue(self.waiting(r)["b"]["pane_open"])
        self.assertEqual(r["parkable"], [])

    def test_decision_the_human_already_answered_is_busy(self):
        self.run_("a", "in_use")
        self.decision("a", replied=_ts(5))
        self.assertIn("a", self.count()["busy"])

    def test_queued_is_busy(self):
        self.run_("a", "queued")
        self.assertIn("a", self.count()["busy"])

    def test_review_without_pr_waiting_for_report_approval(self):
        self.run_("a", "review")
        self.spawned("a", _ts(0))
        self.ev("worker_completed", {"worker": "worker-a", "task": "a"}, _ts(5))
        self.ev("notify_sent", {"kind": "awaiting_user", "task_id": "a", "gate": "worker_completed"}, _ts(6))
        r = self.count()
        self.assertEqual(self.waiting(r)["a"]["reason"], "approval")
        self.assertEqual(r["parkable"], ["a"])

    def test_review_without_pr_and_without_awaiting_user_is_busy(self):
        self.run_("a", "review")
        self.assertIn("a", self.count()["busy"])

    def test_closed_pane_is_not_parkable(self):
        self.run_("a", "review", pr=10)
        self.spawned("a", _ts(0))
        self.green("a", 10)
        self.ev("pane_closed", {"pane_id": "%5", "worker": "worker-a"}, _ts(20))
        r = self.count()
        self.assertFalse(self.waiting(r)["a"]["pane_open"])
        self.assertEqual(r["parkable"], [])

    def test_terminal_runs_are_ignored(self):
        for i, s in enumerate(("completed", "failed", "abandoned", "suspended")):
            self.run_(f"t{i}", s)
        r = self.count()
        self.assertEqual(r["busy"], [])
        self.assertEqual(r["free_slots"], 3)

    def test_free_slots_never_negative(self):
        for t in "abcd":
            self.run_(t, "in_use")
        r = self.count(max_parallel=3)
        self.assertEqual(r["free_slots"], 0)
        self.assertEqual(r["over_capacity"], 1)


class ParkTests(Base):
    def _git_dir(self, dirty=False):
        d = self.dir / "wt"
        d.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", str(d), *cmd], check=True, env=env)
        (d / "f.txt").write_text("x", "utf-8")
        subprocess.run(["git", "-C", str(d), "add", "f.txt"], check=True, env=env)
        if not dirty:
            subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", "f"], check=True, env=env)
        head = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        return d, head

    def test_park_refuses_a_busy_run(self):
        self.run_("a", "in_use")
        r = self.bs.park(self.conn, "a", decisions_path=self.decisions)
        self.assertFalse(r["ok"])

    def test_park_refuses_a_mid_task_decision(self):
        self.run_("a", "in_use")
        self.spawned("a", _ts(0))
        self.decision("a")
        self.assertFalse(self.bs.park(self.conn, "a", decisions_path=self.decisions)["ok"])

    def test_park_waiting_review_records_event_and_names_the_pane(self):
        d, head = self._git_dir()
        self.run_("a", "review", pr=10)
        self.spawned("a", _ts(0), wdir=str(d), pane="%7")
        self.green("a", 10, head=head)
        r = self.bs.park(self.conn, "a", decisions_path=self.decisions)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["pane_id"], "%7")
        row = self.conn.execute(
            "SELECT payload_json FROM events WHERE kind='worker_parked'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0])["task"], "a")
        status = self.conn.execute("SELECT status FROM runs WHERE task_id='a'").fetchone()[0]
        self.assertEqual(status, "review")

    def test_park_refuses_a_dirty_worktree(self):
        d, head = self._git_dir(dirty=True)
        self.run_("a", "review", pr=10)
        self.spawned("a", _ts(0), wdir=str(d))
        self.green("a", 10, head=head)
        self.assertFalse(self.bs.park(self.conn, "a", decisions_path=self.decisions)["ok"])

    def test_park_refuses_when_local_head_moved_past_green_head(self):
        d, head = self._git_dir()
        self.run_("a", "review", pr=10)
        self.spawned("a", _ts(0), wdir=str(d))
        self.green("a", 10, head="0123456789ab")
        self.assertFalse(self.bs.park(self.conn, "a", decisions_path=self.decisions)["ok"])

    def test_park_refuses_unknown_worktree(self):
        self.run_("a", "review", pr=10)
        self.spawned("a", _ts(0), wdir=str(self.dir / "missing"))
        self.green("a", 10)
        self.assertFalse(self.bs.park(self.conn, "a", decisions_path=self.decisions)["ok"])


class IdleCheckTests(Base):
    def setUp(self):
        super().setUp()
        self.contract.write_text(
            "# Conveyor scope contract\n- max_parallel: 3\n- belt_state: running\n", "utf-8"
        )
        self.ev("work_discovery_scanned", {"trigger": "worker_close", "candidate_count": 2}, _ts(0))

    def check(self, minutes):
        return self.bs.idle_check(
            self.conn, contract_path=self.contract, decisions_path=self.decisions,
            idle_state_path=self.idle_state, now=T0 + timedelta(minutes=minutes),
        )

    def test_free_slots_with_candidates_for_20_minutes_is_reported_once(self):
        self.assertEqual(self.check(1)[0], 0)
        self.assertEqual(self.check(10)[0], 0)
        code, msg = self.check(22)
        self.assertEqual(code, 10)
        self.assertIn("BELT_IDLE_SUSPECTED", msg)
        self.assertIn("free=3", msg)
        self.assertEqual(self.check(25)[0], 0)  # same episode: once

    def test_hold_suppresses_until_it_expires(self):
        self.check(1)
        self.bs.hold(self.conn, "candidates are all out of scope", minutes=60, now=T0 + timedelta(minutes=2))
        self.assertEqual(self.check(30)[0], 0)
        self.assertEqual(self.check(70)[0], 10)

    def test_dispatch_restarts_the_episode(self):
        self.check(1)
        self.run_("a", "queued")  # one slot filled
        self.assertEqual(self.check(15)[0], 0)
        self.assertEqual(self.check(30)[0], 0)  # only 15 min since the slot count fell
        self.assertEqual(self.check(36)[0], 10)

    def test_no_slots_or_no_candidates_or_halted_never_reports(self):
        for t in "abc":
            self.run_(t, "in_use")
        self.check(1)
        self.assertEqual(self.check(40)[0], 0)

    def test_no_candidates_never_reports(self):
        self.ev("work_discovery_scanned", {"trigger": "worker_close", "candidate_count": 0}, _ts(0.5))
        self.check(1)
        self.assertEqual(self.check(40)[0], 0)

    def test_halted_contract_never_reports(self):
        self.contract.write_text("- max_parallel: 3\n- belt_state: halted\n", "utf-8")
        self.check(1)
        self.assertEqual(self.check(40)[0], 0)

    def test_missing_contract_never_reports(self):
        self.contract.unlink()
        self.check(1)
        self.assertEqual(self.check(40)[0], 0)


if __name__ == "__main__":
    unittest.main()
