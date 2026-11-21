"""Acceptance tests for ``gen_delegate_payload.py revive`` (Issue 1295).

A delegation whose pane was closed while it only waited on a human (it was
parked) is brought back by spawning a new worker into the same worktree.
``revive`` refuses anything that is not a parked ``review`` run, respects
the conveyor slot limit, moves the previous worker seed and outbox
instruction aside (the runtime refuses to spawn over them), appends a
handover section to the existing brief, puts the run back to ``queued``
and writes a ``send_plan.json`` for the dispatcher.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.state_db import apply_schema, connect  # noqa: E402

TASK = "demo-task"
PR = "https://github.com/suisya-systems/claude-org-ja/pull/77"
ORIGINAL_MESSAGE = "DELEGATE: demo-task original body"


class ReviveBase(unittest.TestCase):
    def setUp(self) -> None:
        self.gdp = importlib.import_module("tools.gen_delegate_payload")
        if not hasattr(self.gdp, "revive_delegation"):
            self.fail("gen_delegate_payload.revive_delegation is missing")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "org"
        self.state = self.root / ".state"
        (self.state / "workers").mkdir(parents=True)
        (self.state / "dispatcher" / "outbox").mkdir(parents=True)
        self.db = self.state / "state.db"
        self.wdir = base / "worktree"
        self.wdir.mkdir()
        conn = connect(self.db)
        apply_schema(conn)
        conn.execute("INSERT INTO projects (id, slug, display_name) VALUES (1, 'ja', 'ja')")
        conn.execute(
            "INSERT INTO worker_dirs (id, abs_path, layout) VALUES (1, ?, 'flat')",
            (str(self.wdir),),
        )
        conn.commit()
        conn.close()

    # -- fixtures ---------------------------------------------------------
    def sql(self, q, args=()):
        conn = connect(self.db)
        try:
            rows = conn.execute(q, args).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def add_run(self, task=TASK, status="review", pr=PR, wdir_id=1):
        self.sql(
            "INSERT INTO runs (task_id, project_id, pattern, title, status, pr_url, "
            "pr_state, worker_dir_id) VALUES (?, 1, 'B', ?, ?, ?, ?, ?)",
            (task, task, status, pr, "open" if pr else None, wdir_id),
        )

    def ev(self, kind, payload, at):
        self.sql(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) VALUES (?, 'x', ?, ?)",
            (at, kind, json.dumps(payload)),
        )

    def parked_worker(self, brief="CLAUDE.md"):
        (self.wdir / brief).write_text("# Worker\nORIGINAL BRIEF\n", "utf-8")
        (self.wdir / "send_plan.json").write_text(
            json.dumps({"to_id": "dispatcher", "message": ORIGINAL_MESSAGE}), "utf-8"
        )
        (self.state / "workers" / f"worker-{TASK}.md").write_text(
            "# worker\n## Progress Log\n- done: PREVIOUS REPORT\n", "utf-8"
        )
        (self.state / "dispatcher" / "outbox" / f"{TASK}-instruction.md").write_text("old", "utf-8")
        self.add_run()
        self.ev("worker_spawned", {"worker": f"worker-{TASK}", "dir": str(self.wdir), "task": TASK},
                "2026-11-22T10:00:00.000Z")
        self.ev("worker_parked", {"task": TASK, "worker": f"worker-{TASK}", "reason": "merge"},
                "2026-11-22T10:10:00.000Z")
        self.ev("pane_closed", {"pane_id": "%5", "worker": f"worker-{TASK}"},
                "2026-11-22T10:11:00.000Z")

    def revive(self, **kw):
        kw.setdefault("reason", "review")
        kw.setdefault("feedback_text", "REVIEWER SAYS: rename foo")
        return self.gdp.revive_delegation(
            TASK, state_db_path=self.db, claude_org_root=self.root, **kw
        )

    def status(self, task=TASK):
        return self.sql("SELECT status FROM runs WHERE task_id = ?", (task,))[0][0]

    def resume_events(self):
        return [json.loads(r[0]) for r in self.sql(
            "SELECT payload_json FROM events WHERE kind = 'delegate_resume' ORDER BY id")]


class ReviveTests(ReviveBase):
    def test_revive_parked_review_run(self):
        self.parked_worker()
        out = self.revive()
        self.assertEqual(self.status(), "queued")
        # seed and outbox moved aside, not deleted
        self.assertFalse((self.state / "workers" / f"worker-{TASK}.md").exists())
        self.assertFalse((self.state / "dispatcher" / "outbox" / f"{TASK}-instruction.md").exists())
        archived = list((self.state / "workers" / "archive").glob(f"worker-{TASK}*gen1*"))
        self.assertEqual(len(archived), 1)
        self.assertIn("PREVIOUS REPORT", archived[0].read_text("utf-8"))
        # handover appended to the existing brief
        brief = (self.wdir / "CLAUDE.md").read_text("utf-8")
        self.assertIn("ORIGINAL BRIEF", brief)
        self.assertIn("引き継ぎ", brief)
        self.assertIn("REVIEWER SAYS: rename foo", brief)
        self.assertIn(PR, brief)
        self.assertIn("PREVIOUS REPORT", brief)
        # send plan for the dispatcher carries the original DELEGATE body
        plan = json.loads(Path(out["send_plan_path"]).read_text("utf-8"))
        self.assertEqual(plan["to_id"], "dispatcher")
        self.assertIn(ORIGINAL_MESSAGE, plan["message"])
        self.assertEqual(out["gen"], 1)
        ev = self.resume_events()
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["revived"])
        self.assertEqual(ev[0]["gen"], 1)
        self.assertEqual(ev[0]["reason"], "review")

    def test_brief_in_claude_local_md_is_used_and_project_file_untouched(self):
        self.parked_worker(brief="CLAUDE.local.md")
        (self.wdir / "CLAUDE.md").write_text("PROJECT FILE\n", "utf-8")
        self.revive()
        self.assertIn("引き継ぎ", (self.wdir / "CLAUDE.local.md").read_text("utf-8"))
        self.assertEqual((self.wdir / "CLAUDE.md").read_text("utf-8"), "PROJECT FILE\n")

    def test_refuses_run_that_is_not_review(self):
        self.parked_worker()
        self.sql("UPDATE runs SET status = 'in_use' WHERE task_id = ?", (TASK,))
        with self.assertRaises(self.gdp.ReviveRefused):
            self.revive()
        self.assertTrue((self.state / "workers" / f"worker-{TASK}.md").exists())

    def test_refuses_while_the_pane_is_still_open(self):
        self.parked_worker()
        self.ev("worker_spawned", {"worker": f"worker-{TASK}", "dir": str(self.wdir), "task": TASK},
                "2026-11-22T10:20:00.000Z")
        with self.assertRaises(self.gdp.ReviveRefused):
            self.revive()
        self.assertEqual(self.status(), "review")

    def test_refuses_when_no_slot_is_free(self):
        self.parked_worker()
        (self.state / "conveyor").mkdir()
        (self.state / "conveyor" / "scope-contract.md").write_text(
            "- max_parallel: 1\n- belt_state: running\n", "utf-8")
        self.add_run(task="busy-task", status="in_use", pr=None, wdir_id=None)
        with self.assertRaises(self.gdp.ReviveRefused):
            self.revive()
        self.assertEqual(self.status(), "review")
        self.assertNotIn("引き継ぎ", (self.wdir / "CLAUDE.md").read_text("utf-8"))

    def test_retry_before_spawn_is_idempotent(self):
        self.parked_worker()
        self.revive()
        out = self.revive()  # e.g. the DELEGATE send failed; run is still queued
        self.assertEqual(out["gen"], 1)
        self.assertEqual(self.status(), "queued")
        self.assertEqual((self.wdir / "CLAUDE.md").read_text("utf-8").count("REVIEWER SAYS"), 1)
        self.assertEqual(len(self.resume_events()), 1)

    def test_second_revive_is_a_new_generation(self):
        self.parked_worker()
        self.revive()
        # the revived worker ran, reported, and was parked again
        self.sql("UPDATE runs SET status = 'review' WHERE task_id = ?", (TASK,))
        (self.state / "workers" / f"worker-{TASK}.md").write_text("SECOND REPORT\n", "utf-8")
        self.ev("worker_spawned", {"worker": f"worker-{TASK}", "dir": str(self.wdir), "task": TASK},
                "2026-11-22T11:00:00.000Z")
        self.ev("worker_parked", {"task": TASK, "reason": "merge"}, "2026-11-22T11:10:00.000Z")
        self.ev("pane_closed", {"pane_id": "%6", "worker": f"worker-{TASK}"}, "2026-11-22T11:11:00.000Z")
        out = self.revive(reason="ci_failed", feedback_text="CI RED: test_x")
        self.assertEqual(out["gen"], 2)
        self.assertEqual(len(list((self.state / "workers" / "archive").glob(f"worker-{TASK}*gen2*"))), 1)
        brief = (self.wdir / "CLAUDE.md").read_text("utf-8")
        self.assertIn("CI RED: test_x", brief)
        self.assertIn("SECOND REPORT", brief)
        self.assertEqual([e["gen"] for e in self.resume_events()], [1, 2])

    def test_unknown_reason_is_refused(self):
        self.parked_worker()
        with self.assertRaises(self.gdp.ReviveRefused):
            self.revive(reason="whatever")


if __name__ == "__main__":
    unittest.main()
