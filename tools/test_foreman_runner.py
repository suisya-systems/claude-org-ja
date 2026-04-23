"""Unit tests for tools/foreman_runner.py (Issue #60)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import foreman_runner as fr  # noqa: E402


def mk_pane(**kw):
    defaults = dict(
        id=1, name=None, role=None, focused=False,
        x=0, y=0, width=100, height=40,
    )
    defaults.update(kw)
    return fr.Pane(**defaults)


class RectAdjacencyTests(unittest.TestCase):
    def test_shared_vertical_edge_with_overlap(self) -> None:
        a = mk_pane(x=0, y=0, width=40, height=20)
        b = mk_pane(x=40, y=5, width=30, height=10)
        self.assertTrue(fr.rect_adjacent(a, b))

    def test_shared_horizontal_edge_with_overlap(self) -> None:
        a = mk_pane(x=0, y=0, width=40, height=20)
        b = mk_pane(x=10, y=20, width=20, height=10)
        self.assertTrue(fr.rect_adjacent(a, b))

    def test_no_overlap_same_edge(self) -> None:
        a = mk_pane(x=0, y=0, width=40, height=20)
        b = mk_pane(x=40, y=30, width=20, height=10)  # y-disjoint
        self.assertFalse(fr.rect_adjacent(a, b))

    def test_not_touching(self) -> None:
        a = mk_pane(x=0, y=0, width=10, height=10)
        b = mk_pane(x=20, y=0, width=10, height=10)
        self.assertFalse(fr.rect_adjacent(a, b))


class ChooseSplitTests(unittest.TestCase):
    def test_zero_workers_picks_foreman_when_adjacent_to_curator(self) -> None:
        # Initial layout: secretary narrow enough to trigger its safety clause
        # (new_w = width/2 < 125), so foreman wins as the candidate.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=180, height=30,  # new_w=90 < 125, excluded
        )
        foreman = mk_pane(
            id=2, name="foreman", role="foreman",
            x=0, y=30, width=140, height=20,
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=140, y=30, width=60, height=20,
        )
        choice = fr.choose_split([secretary, foreman, curator])
        assert choice is not None
        self.assertEqual(choice.target_name, "foreman")
        self.assertEqual(choice.direction, "vertical")

    def test_foreman_excluded_when_not_adjacent_to_curator(self) -> None:
        foreman = mk_pane(
            id=2, name="foreman", role="foreman",
            x=0, y=0, width=60, height=20,
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=100, y=30, width=60, height=20,  # not adjacent
        )
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=40, width=180, height=10,
        )
        choice = fr.choose_split([foreman, curator, secretary])
        # foreman filtered out by adjacency. secretary width 180 height 10:
        # 180 > 10*2 → vertical → new_w=90, 90 < SECRETARY_MIN_WIDTH(125)
        # so secretary also excluded. → None
        self.assertIsNone(choice)

    def test_returns_none_when_no_candidates(self) -> None:
        # Only a curator exists — no splittable panes
        curator = mk_pane(id=1, name="curator", role="curator")
        self.assertIsNone(fr.choose_split([curator]))

    def test_min_pane_enforced(self) -> None:
        # Tiny foreman adjacent to curator: split would produce <MIN dims
        foreman = mk_pane(
            id=2, name="foreman", role="foreman",
            x=0, y=0, width=30, height=8,
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=30, y=0, width=30, height=8,
        )
        # width 30 height 8: width>height*2 → vertical split →
        # new_w=15 < 20, excluded
        self.assertIsNone(fr.choose_split([foreman, curator]))

    def test_secretary_rejected_when_split_width_below_min(self) -> None:
        # secretary width=248 → vertical split (248 > 60*2) → new_w=124 < 125.
        # Height 60 ≥ SECRETARY_MIN_HEIGHT(45). Only width fails.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=248, height=60,
        )
        self.assertIsNone(fr.choose_split([secretary]))

    def test_secretary_rejected_when_split_height_below_min(self) -> None:
        # secretary width=130 height=80: 130 > 80*2? no → horizontal split
        # new_w=130 ≥ 125 OK, new_h=40 < SECRETARY_MIN_HEIGHT(45). Only height fails.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=130, height=80,
        )
        self.assertIsNone(fr.choose_split([secretary]))

    def test_secretary_accepted_when_both_dims_ok(self) -> None:
        # secretary width=260 height=100 → vertical (260 > 100*2? no → horizontal)
        # Actually 260 > 200 → vertical → new_w=130 ≥ 125, new_h=100 ≥ 45. Both pass.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=260, height=100,
        )
        choice = fr.choose_split([secretary])
        assert choice is not None
        self.assertEqual(choice.target_name, "secretary")
        self.assertEqual(choice.direction, "vertical")
        self.assertEqual(choice.new_w, 130)
        self.assertEqual(choice.new_h, 100)

    def test_pane_without_name_is_skipped(self) -> None:
        foreman = mk_pane(
            id=2, name=None, role="foreman",
            x=0, y=0, width=140, height=20,
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=140, y=0, width=60, height=20,
        )
        # foreman has no addressable name even if adjacent — skip
        self.assertIsNone(fr.choose_split([foreman, curator]))


class ValidationTests(unittest.TestCase):
    def test_task_id_accepts_valid(self) -> None:
        self.assertIsNone(fr.validate_task_id("login-fix"))
        self.assertIsNone(fr.validate_task_id("ceps_analysis"))

    def test_task_id_rejects_empty(self) -> None:
        self.assertIsNotNone(fr.validate_task_id(""))

    def test_task_id_rejects_bad_chars(self) -> None:
        self.assertIsNotNone(fr.validate_task_id("login fix"))
        self.assertIsNotNone(fr.validate_task_id("login/fix"))
        self.assertIsNotNone(fr.validate_task_id("login.fix"))


class BuildPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / ".state"
        # cwd must exist
        self.work_dir = Path(self.tmp.name) / "work"
        self.work_dir.mkdir()
        self.panes = [
            mk_pane(
                id=1, name="secretary", role="secretary",
                x=0, y=0, width=180, height=30,
            ),
            mk_pane(
                id=2, name="foreman", role="foreman",
                x=0, y=30, width=140, height=20,
            ),
            mk_pane(
                id=3, name="curator", role="curator",
                x=140, y=30, width=60, height=20,
            ),
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_happy_path(self) -> None:
        task = {
            "task_id": "login-fix",
            "worker_dir": str(self.work_dir),
            "permission_mode": "auto",
            "task_description": "Fix the login flow.",
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.status, "ready_to_spawn")
        self.assertIsNotNone(plan.spawn)
        self.assertEqual(plan.spawn["tool"], "spawn_claude_pane")
        self.assertEqual(plan.spawn["name"], "worker-login-fix")
        self.assertEqual(plan.spawn["role"], "worker")
        self.assertEqual(plan.spawn["permission_mode"], "auto")
        self.assertEqual(plan.spawn["cwd"], str(self.work_dir))
        # 4 after_spawn steps: poll_events, send_keys, list_peers, send_message
        self.assertEqual(len(plan.after_spawn), 4)
        self.assertEqual(plan.after_spawn[0]["tool"], "poll_events")
        self.assertEqual(plan.after_spawn[1]["tool"], "send_keys")
        self.assertTrue(plan.after_spawn[1]["enter"])

    def test_duplicate_worker_name_rejected(self) -> None:
        panes = list(self.panes) + [
            mk_pane(
                id=4, name="worker-login-fix", role="worker",
                x=60, y=50, width=140, height=10,
            ),
        ]
        task = {
            "task_id": "login-fix",
            "worker_dir": str(self.work_dir),
        }
        plan = fr.build_plan(task, panes, self.state_dir)
        self.assertEqual(plan.status, "input_invalid")
        self.assertTrue(
            any("already exists" in e for e in plan.errors)
        )

    def test_missing_cwd_rejected(self) -> None:
        plan = fr.build_plan(
            {"task_id": "login-fix"}, self.panes, self.state_dir
        )
        self.assertEqual(plan.status, "input_invalid")

    def test_nonexistent_cwd_rejected(self) -> None:
        plan = fr.build_plan(
            {
                "task_id": "login-fix",
                "worker_dir": "/definitely/does/not/exist/abc123",
            },
            self.panes, self.state_dir,
        )
        self.assertEqual(plan.status, "input_invalid")

    def test_split_capacity_exceeded_emits_escalation(self) -> None:
        # No splittable panes
        curator_only = [
            mk_pane(id=1, name="curator", role="curator",
                    x=0, y=0, width=40, height=10),
        ]
        task = {
            "task_id": "login-fix",
            "worker_dir": str(self.work_dir),
        }
        plan = fr.build_plan(task, curator_only, self.state_dir)
        self.assertEqual(plan.status, "split_capacity_exceeded")
        self.assertIsNotNone(plan.escalate)
        self.assertEqual(plan.escalate["to_id"], "secretary")
        self.assertIn("SPLIT_CAPACITY_EXCEEDED", plan.escalate["message"])


class PaneParserTests(unittest.TestCase):
    def test_accepts_list_form(self) -> None:
        panes = fr._parse_panes([
            {"id": 1, "x": 0, "y": 0, "width": 80, "height": 20},
        ])
        self.assertEqual(len(panes), 1)

    def test_accepts_object_form(self) -> None:
        panes = fr._parse_panes({
            "panes": [{"id": 1, "x": 0, "y": 0, "width": 80, "height": 20}],
        })
        self.assertEqual(len(panes), 1)


if __name__ == "__main__":
    unittest.main()
