"""Unit tests for tools/dispatcher_runner.py (Issue #60)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatcher_runner as fr  # noqa: E402


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
    def test_zero_workers_picks_dispatcher_when_adjacent_to_curator(self) -> None:
        # Initial layout: secretary narrow enough to trigger its safety clause
        # (new_w = width/2 < 125), so dispatcher wins as the candidate.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=180, height=30,  # new_w=90 < 125, excluded
        )
        dispatcher = mk_pane(
            id=2, name="dispatcher", role="dispatcher",
            x=0, y=30, width=140, height=20,
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=140, y=30, width=60, height=20,
        )
        choice = fr.choose_split([secretary, dispatcher, curator])
        assert choice is not None
        self.assertEqual(choice.target_name, "dispatcher")
        self.assertEqual(choice.direction, "vertical")

    def test_dispatcher_excluded_when_not_adjacent_to_curator(self) -> None:
        dispatcher = mk_pane(
            id=2, name="dispatcher", role="dispatcher",
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
        choice = fr.choose_split([dispatcher, curator, secretary])
        # dispatcher filtered out by adjacency. secretary width 180 height 10:
        # 180 > 10*2 → vertical → new_w=90, 90 < SECRETARY_MIN_WIDTH(125)
        # so secretary also excluded. → None
        self.assertIsNone(choice)

    def test_returns_none_when_no_candidates(self) -> None:
        # Only a curator exists — no splittable panes
        curator = mk_pane(id=1, name="curator", role="curator")
        self.assertIsNone(fr.choose_split([curator]))

    def test_min_pane_enforced(self) -> None:
        # Tiny dispatcher adjacent to curator: split would produce <MIN dims
        dispatcher = mk_pane(
            id=2, name="dispatcher", role="dispatcher",
            x=0, y=0, width=30, height=8,
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=30, y=0, width=30, height=8,
        )
        # width 30 height 8: width>height*2 → vertical split →
        # new_w=15 < 20, excluded
        self.assertIsNone(fr.choose_split([dispatcher, curator]))

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

    def test_secretary_accepted_at_width_boundary(self) -> None:
        # width=250 → vertical split → new_w=125 exactly. Must be accepted (>= 125).
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=250, height=60,
        )
        choice = fr.choose_split([secretary])
        assert choice is not None
        self.assertEqual(choice.new_w, 125)
        self.assertEqual(choice.direction, "vertical")

    def test_secretary_accepted_at_height_boundary(self) -> None:
        # width=130 height=90 → horizontal (130 < 90*2=180) → new_h=45 exactly. Accept.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=130, height=90,
        )
        choice = fr.choose_split([secretary])
        assert choice is not None
        self.assertEqual(choice.new_h, 45)
        self.assertEqual(choice.direction, "horizontal")

    def test_pane_without_name_is_skipped(self) -> None:
        dispatcher = mk_pane(
            id=2, name=None, role="dispatcher",
            x=0, y=0, width=140, height=20,
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=140, y=0, width=60, height=20,
        )
        # dispatcher has no addressable name even if adjacent — skip
        self.assertIsNone(fr.choose_split([dispatcher, curator]))

    def test_multiple_existing_workers_picks_largest_metric(self) -> None:
        # Steady-state layout: secretary up top, dispatcher+curator row, two workers
        # already occupying bottom cells of different sizes. The next target must
        # be whichever existing pane has the largest post-split metric.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=260, height=30,       # new_w=130 ≥ 125, new_h=30 < 45 → horizontal fails height
        )                                          # width>height*2 (260>60) → vertical → new_w=130, new_h=30
                                                   # 30 ≥ MIN_PANE_HEIGHT(5) OK, secretary height guard 30<45 → rejected
        dispatcher = mk_pane(
            id=2, name="dispatcher", role="dispatcher",
            x=0, y=30, width=130, height=25,       # 130 > 25*2 → vertical split, new_w=65
        )
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=130, y=30, width=130, height=25,
        )
        worker_a = mk_pane(
            id=4, name="worker-a", role="worker",
            x=0, y=55, width=80, height=25,        # 80 > 25*2 → vertical, new_w=40
        )
        worker_b = mk_pane(
            id=5, name="worker-b", role="worker",
            x=80, y=55, width=180, height=25,      # 180 > 25*2 → vertical, new_w=90
        )
        choice = fr.choose_split([
            secretary, dispatcher, curator, worker_a, worker_b,
        ])
        assert choice is not None
        # worker_b has the largest split metric (new_w=90 > dispatcher 65 > worker_a 40).
        # Secretary is guard-rejected (new_h=30 < 45 on horizontal, new_w fine on
        # vertical but guard checks BOTH dims in the chosen direction).
        self.assertEqual(choice.target_name, "worker-b")
        self.assertEqual(choice.direction, "vertical")
        self.assertEqual(choice.new_w, 90)

    def test_irregular_layout_after_worker_closed(self) -> None:
        # After a worker closed, dispatcher and curator no longer share a rect edge
        # (another pane sits between them). Dispatcher must be filtered out by the
        # adjacency rule, otherwise it would tie with worker-left on metric and
        # win via id asc — which would be wrong.
        secretary = mk_pane(
            id=1, name="secretary", role="secretary",
            x=0, y=0, width=200, height=30,        # new_w=100 < 125 → rejected by secretary guard
        )
        dispatcher = mk_pane(
            id=2, name="dispatcher", role="dispatcher",
            x=0, y=30, width=60, height=40,
        )
        # Gap in the middle (where a worker was) — dispatcher no longer touches curator
        curator = mk_pane(
            id=3, name="curator", role="curator",
            x=140, y=30, width=60, height=40,
        )
        # A surviving worker sits in the gap and keeps the tab alive as a target
        worker_left = mk_pane(
            id=4, name="worker-left", role="worker",
            x=60, y=30, width=80, height=40,       # 80 < 40*2 → horizontal split → new_h=20
        )
        choice = fr.choose_split([secretary, dispatcher, curator, worker_left])
        assert choice is not None
        # dispatcher excluded by adjacency rule, secretary excluded by guard,
        # only worker_left is a valid candidate.
        self.assertEqual(choice.target_name, "worker-left")
        self.assertEqual(choice.direction, "horizontal")
        self.assertEqual(choice.new_h, 20)


class ValidationTests(unittest.TestCase):
    def test_task_id_accepts_valid(self) -> None:
        self.assertIsNone(fr.validate_task_id("login-fix"))
        self.assertIsNone(fr.validate_task_id("data_analysis"))

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
                id=2, name="dispatcher", role="dispatcher",
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
        # model defaults to opus when omitted (auto classifier stability)
        self.assertEqual(plan.spawn["model"], "opus")

    def test_model_defaults_to_opus_when_omitted(self) -> None:
        task = {
            "task_id": "model-default",
            "worker_dir": str(self.work_dir),
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.status, "ready_to_spawn")
        self.assertEqual(plan.spawn["model"], "opus")

    def test_explicit_model_is_respected(self) -> None:
        task = {
            "task_id": "model-override",
            "worker_dir": str(self.work_dir),
            "model": "sonnet",
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.spawn["model"], "sonnet")

    def test_cwd_is_file_rejected(self) -> None:
        # "exists but not a directory" used to silently pass as a warning.
        file_cwd = self.work_dir / "not-a-dir.txt"
        file_cwd.write_text("x", encoding="utf-8")
        plan = fr.build_plan(
            {"task_id": "login-fix", "worker_dir": str(file_cwd)},
            self.panes, self.state_dir,
        )
        self.assertEqual(plan.status, "input_invalid")
        self.assertTrue(any("not a directory" in e for e in plan.errors))

    def test_duplicate_state_file_rejected(self) -> None:
        # Simulate a prior task that left worker seed behind.
        seed = self.state_dir / "workers" / "worker-login-fix.md"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("# leftover\n", encoding="utf-8")
        task = {
            "task_id": "login-fix",
            "worker_dir": str(self.work_dir),
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.status, "input_invalid")
        self.assertTrue(any("already exists" in e for e in plan.errors))

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


class InstructionVarsValidationTests(unittest.TestCase):
    def _vars(self, **overrides):
        base = {
            "task_description": "Fix the login flow.",
            "dir_setup": "clone は不要です。",
            "branch_strategy": "feature/login",
            "verification_depth": "full",
        }
        base.update(overrides)
        return base

    def test_accepts_minimum_required(self) -> None:
        norm, err = fr.validate_instruction_vars(self._vars())
        self.assertIsNone(err)
        assert norm is not None
        self.assertEqual(norm["task_description"], "Fix the login flow.")
        self.assertEqual(norm["verification_depth"], "full")
        self.assertEqual(norm["branch_strategy"], "feature/login")
        # Defaults filled (only the truly-optional ones)
        self.assertEqual(norm["report_target"], "secretary")
        self.assertEqual(norm["constraints"], "(なし)")

    def test_rejects_non_dict(self) -> None:
        _, err = fr.validate_instruction_vars(["not", "a", "dict"])
        self.assertIsNotNone(err)

    def test_rejects_missing_verification_depth(self) -> None:
        _, err = fr.validate_instruction_vars(
            {"task_description": "x", "dir_setup": "y", "branch_strategy": "z"}
        )
        self.assertIsNotNone(err)
        self.assertIn("verification_depth", err)

    def test_rejects_missing_branch_strategy(self) -> None:
        # Defaulting branch_strategy would silently mis-instruct Pattern B
        # (worktree) workers — keep it required.
        _, err = fr.validate_instruction_vars(
            {"task_description": "x", "dir_setup": "y",
             "verification_depth": "full"}
        )
        self.assertIsNotNone(err)
        self.assertIn("branch_strategy", err)

    def test_rejects_blank_required(self) -> None:
        _, err = fr.validate_instruction_vars(self._vars(task_description="   "))
        self.assertIsNotNone(err)
        self.assertIn("task_description", err)

    def test_rejects_unknown_var(self) -> None:
        _, err = fr.validate_instruction_vars(self._vars(rogue_field="bad"))
        self.assertIsNotNone(err)
        self.assertIn("rogue_field", err)

    def test_rejects_invalid_verification_depth(self) -> None:
        _, err = fr.validate_instruction_vars(
            self._vars(verification_depth="medium")
        )
        self.assertIsNotNone(err)
        self.assertIn("verification_depth", err)

    def test_accepts_minimal_depth(self) -> None:
        norm, err = fr.validate_instruction_vars(
            self._vars(verification_depth="minimal")
        )
        self.assertIsNone(err)
        assert norm is not None
        self.assertEqual(norm["verification_depth"], "minimal")


class InstructionTemplateRenderTests(unittest.TestCase):
    def test_template_loads_and_renders(self) -> None:
        rendered = fr.render_instruction({
            "task_description": "Fix the login flow.",
            "dir_setup": "## setup\nworktree ready",
            "branch_strategy": "feature/login",
            "constraints": "no JS",
            "verification_depth": "full",
            "report_target": "secretary",
        })
        # Spot-check that key directives survive in the output
        self.assertIn("Fix the login flow.", rendered)
        self.assertIn("worktree ready", rendered)
        self.assertIn("feature/login", rendered)
        self.assertIn("no JS", rendered)
        self.assertIn('to_id="secretary"', rendered)
        self.assertIn("SUSPEND", rendered)
        self.assertIn("Codex", rendered)  # full-mode reviewer directive
        # Strict-format placeholders fully consumed
        for tok in (
            "{task_description}", "{dir_setup}", "{branch_strategy}",
            "{constraints}", "{verification_depth}", "{report_target}",
        ):
            self.assertNotIn(tok, rendered)


class BuildPlanInstructionVarsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / ".state"
        self.work_dir = Path(self.tmp.name) / "work"
        self.work_dir.mkdir()
        self.panes = [
            mk_pane(id=1, name="secretary", role="secretary",
                    x=0, y=0, width=180, height=30),
            mk_pane(id=2, name="dispatcher", role="dispatcher",
                    x=0, y=30, width=140, height=20),
            mk_pane(id=3, name="curator", role="curator",
                    x=140, y=30, width=60, height=20),
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _good_vars(self):
        return {
            "task_description": "Fix the login flow.",
            "dir_setup": "worktree ready, clone不要",
            "branch_strategy": "issue-71-template-expand",
            "verification_depth": "full",
        }

    def test_instruction_vars_renders_into_outbox(self) -> None:
        task = {
            "task_id": "vars-happy",
            "worker_dir": str(self.work_dir),
            "instruction_vars": self._good_vars(),
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.status, "ready_to_spawn")
        # Trigger side-effect writes (build_plan does not write)
        fr.write_instruction(self.state_dir, task, plan.task_id)
        outbox = (self.state_dir / "dispatcher" / "outbox"
                  / "vars-happy-instruction.md")
        body = outbox.read_text(encoding="utf-8")
        self.assertIn("Fix the login flow.", body)
        self.assertIn("worktree ready", body)
        self.assertIn('to_id="secretary"', body)
        self.assertIn("SUSPEND", body)
        # No dangling format placeholders leaked
        self.assertNotIn("{task_description}", body)

    def test_explicit_instruction_wins_over_vars(self) -> None:
        task = {
            "task_id": "explicit-wins",
            "worker_dir": str(self.work_dir),
            "instruction": "literal handoff text — do exactly this",
            "instruction_vars": self._good_vars(),
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.status, "ready_to_spawn")
        # Warning surfaced, but no error
        self.assertTrue(any(
            "instruction_vars" in w and "ignored" in w
            for w in plan.warnings
        ))
        fr.write_instruction(self.state_dir, task, plan.task_id)
        body = ((self.state_dir / "dispatcher" / "outbox"
                 / "explicit-wins-instruction.md")
                .read_text(encoding="utf-8"))
        self.assertIn("literal handoff text", body)
        # Template directives must NOT appear (explicit took over)
        self.assertNotIn("SUSPEND", body)

    def test_blank_instruction_falls_through_to_vars(self) -> None:
        # An empty/whitespace `instruction` must not silently produce an empty
        # outbox file; it should fall through to instruction_vars expansion.
        task = {
            "task_id": "blank-instr",
            "worker_dir": str(self.work_dir),
            "instruction": "   ",  # whitespace
            "instruction_vars": self._good_vars(),
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.status, "ready_to_spawn")
        self.assertEqual(plan.warnings, [])  # not "explicit wins"
        fr.write_instruction(self.state_dir, task, plan.task_id)
        body = ((self.state_dir / "dispatcher" / "outbox"
                 / "blank-instr-instruction.md")
                .read_text(encoding="utf-8"))
        self.assertIn("Fix the login flow.", body)
        self.assertIn("SUSPEND", body)

    def test_instruction_vars_missing_required_rejected(self) -> None:
        bad = {"task_description": "x", "dir_setup": "y",
               "branch_strategy": "z"}  # no verification_depth
        plan = fr.build_plan(
            {
                "task_id": "vars-missing",
                "worker_dir": str(self.work_dir),
                "instruction_vars": bad,
            },
            self.panes, self.state_dir,
        )
        self.assertEqual(plan.status, "input_invalid")
        self.assertTrue(any(
            "verification_depth" in e for e in plan.errors
        ))

    def test_instruction_vars_unknown_key_rejected(self) -> None:
        bad = dict(self._good_vars(), rogue_var="oops")
        plan = fr.build_plan(
            {
                "task_id": "vars-unknown",
                "worker_dir": str(self.work_dir),
                "instruction_vars": bad,
            },
            self.panes, self.state_dir,
        )
        self.assertEqual(plan.status, "input_invalid")
        self.assertTrue(any("rogue_var" in e for e in plan.errors))

    def test_legacy_task_description_only_still_works(self) -> None:
        # Backward-compat: existing callers that pass neither `instruction`
        # nor `instruction_vars` keep the old task_description fallback.
        task = {
            "task_id": "legacy-fallback",
            "worker_dir": str(self.work_dir),
            "task_description": "Old-style description.",
        }
        plan = fr.build_plan(task, self.panes, self.state_dir)
        self.assertEqual(plan.status, "ready_to_spawn")
        fr.write_instruction(self.state_dir, task, plan.task_id)
        body = ((self.state_dir / "dispatcher" / "outbox"
                 / "legacy-fallback-instruction.md")
                .read_text(encoding="utf-8"))
        self.assertIn("Old-style description.", body)


if __name__ == "__main__":
    unittest.main()
