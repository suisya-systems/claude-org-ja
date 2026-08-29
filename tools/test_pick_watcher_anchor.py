"""Unit tests for tools/pick_watcher_anchor.py (Refs #335).

The tool's contract is an *ordering* over anchors, so the tests are fixed
geometry fixtures with an asserted candidate order rather than spot checks
on individual predicates:

* the 2026-08-30 incident layout (narrow dispatcher + free secretary) must
  not yield "no capacity" -- the secretary must be offered;
* the healthy layout must keep the dispatcher first, so this change does
  not silently move every watcher off its historical anchor;
* the genuinely-full layout must yield zero candidates and exit 2 under
  renga -- the only state the skill may report as "out of capacity" -- while
  the same layout under broker keeps the anchors, since broker panes are
  independent sessions with no rect ceiling;
* the floor mirror must equal the runtime's constants.

**The pane sizes in these fixtures are observed examples, not spec
values.** 397x53 / 24 wide are what the 2026-08-30 incident happened to
measure; 280x43 and 80x24 stand in for a laptop and a small terminal.
Terminal geometry is per-environment, so nothing in ``pick()`` keys on a
particular size -- the only absolute numbers in the tool are the runtime
floor mirror (pinned by ``TestFloorDrift``) and prose in its docstring.
A fixture size may be changed freely as long as its relation to the
floors is preserved.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pick_watcher_anchor as pwa  # noqa: E402


def pane(id_, name, role, x, y, w, h) -> dict:
    return {"id": id_, "name": name, "role": role, "x": x, "y": y, "width": w, "height": h}


def names(result: dict) -> list:
    return [c["target"] for c in result["candidates"]]


# The 2026-08-30 incident: the dispatcher had been halved down to 24 cols
# (below MIN_PANE_WIDTH once halved again) while the secretary sat free at
# 397x53. The old skill aimed at the dispatcher, got [split_refused], and
# concluded the tab was full.
INCIDENT_LAYOUT = [
    pane("%1", "secretary", "secretary", 0, 0, 397, 53),
    pane("%2", "dispatcher", "dispatcher", 0, 54, 24, 30),
    pane("%3", "worker-alpha", "worker", 25, 54, 180, 30),
]

# Steady state right after /org-start: a wide dispatcher that can still
# absorb a watcher without dropping below its comfort floor.
HEALTHY_LAYOUT = [
    pane("%1", "secretary", "secretary", 0, 0, 280, 43),
    pane("%2", "dispatcher", "dispatcher", 0, 44, 280, 43),
]

# A small terminal (80x24 class), tiled the usual way. Nothing here is
# splittable *comfortably*, which is the point: the tool should degrade
# through its tiers rather than jump to "no capacity".
SMALL_TERMINAL_LAYOUT = [
    pane("%1", "secretary", "secretary", 0, 0, 80, 12),
    pane("%2", "dispatcher", "dispatcher", 0, 12, 40, 12),
    pane("%3", "worker-a", "worker", 40, 12, 40, 12),
]

# Everything is at or under the floors: no anchor can be halved at all.
FULL_LAYOUT = [
    pane("%1", "secretary", "secretary", 0, 0, 100, 20),
    pane("%2", "dispatcher", "dispatcher", 0, 21, 30, 6),
    pane("%3", "pr-watch-900", "watcher", 31, 21, 30, 6),
]


class TestOrdering(unittest.TestCase):
    def test_incident_layout_falls_back_to_secretary(self):
        result = pwa.build_result(pwa.parse_panes(INCIDENT_LAYOUT))
        self.assertFalse(result["capacity_exhausted"])
        # The dispatcher is 24 cols: a vertical split leaves 12 < MIN_PANE_WIDTH
        # and a horizontal one leaves 24x15, which clears the floors -- so it
        # is still offered, but demoted below the secretary because it is
        # under DISPATCHER_MIN_WIDTH.
        self.assertEqual(names(result), ["secretary", "dispatcher"])
        self.assertEqual(result["candidates"][0]["tier"], "secretary")
        self.assertEqual(result["candidates"][1]["tier"], "dispatcher-narrow")

    def test_healthy_layout_keeps_dispatcher_first(self):
        result = pwa.build_result(pwa.parse_panes(HEALTHY_LAYOUT))
        self.assertEqual(names(result), ["dispatcher", "secretary"])
        first = result["candidates"][0]
        self.assertEqual(first["tier"], "dispatcher")
        self.assertEqual(first["direction"], "vertical")
        self.assertEqual(first["new_w"], 140)

    def test_resident_watcher_ranks_between_dispatcher_and_secretary(self):
        layout = [
            pane("%1", "secretary", "secretary", 0, 0, 280, 43),
            pane("%2", "dispatcher", "dispatcher", 0, 44, 200, 43),
            pane("%3", "pr-watch-900", "watcher", 200, 44, 180, 43),
        ]
        result = pwa.build_result(pwa.parse_panes(layout))
        self.assertEqual(
            names(result), ["dispatcher", "pr-watch-900", "secretary"]
        )

    def test_watcher_adjacent_to_dispatcher_wins_over_a_roomier_detached_one(self):
        # The detached watcher is wider (metric 90 vs 60), so only the
        # adjacency key can put the adjacent one first.
        layout = [
            pane("%1", "secretary", "secretary", 0, 0, 400, 40),
            pane("%2", "dispatcher", "dispatcher", 0, 40, 60, 40),
            pane("%3", "pr-watch-adj", "watcher", 60, 40, 120, 40),
            pane("%4", "pr-watch-far", "watcher", 220, 40, 180, 40),
        ]
        result = pwa.build_result(pwa.parse_panes(layout))
        watchers = [c["target"] for c in result["candidates"] if c["role"] == "watcher"]
        self.assertEqual(watchers, ["pr-watch-adj", "pr-watch-far"])

    def test_attention_pane_ranks_in_the_watcher_tier(self):
        # /org-attention-start registers its pane as role="attention", not
        # "watcher"; excluding it would report capacity exhaustion in a
        # layout where it is the only splittable pane.
        layout = [
            pane("%1", "secretary", "secretary", 0, 0, 200, 40),
            pane("%2", "dispatcher", "dispatcher", 0, 40, 30, 40),
            pane("%3", "attention", "attention", 30, 40, 170, 40),
        ]
        result = pwa.build_result(pwa.parse_panes(layout))
        self.assertFalse(result["capacity_exhausted"])
        self.assertEqual(result["candidates"][0]["target"], "attention")
        self.assertEqual(result["candidates"][0]["tier"], "watcher")

    def test_working_panes_are_never_offered(self):
        result = pwa.build_result(pwa.parse_panes(INCIDENT_LAYOUT))
        self.assertNotIn("worker-alpha", names(result))
        rejected = {r["target"]: r["reason"] for r in result["rejected"]}
        self.assertIn("worker-alpha", rejected)
        self.assertIn("not an anchor role", rejected["worker-alpha"])

    def test_unnamed_pane_is_not_targetable(self):
        layout = [pane("%9", None, "dispatcher", 0, 0, 280, 43)]
        result = pwa.build_result(pwa.parse_panes(layout))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejected"][0]["reason"], "pane has no name to target")

    def test_ordering_is_independent_of_input_order(self):
        forward = pwa.build_result(pwa.parse_panes(INCIDENT_LAYOUT))
        reverse = pwa.build_result(pwa.parse_panes(list(reversed(INCIDENT_LAYOUT))))
        self.assertEqual(names(forward), names(reverse))


class TestDirection(unittest.TestCase):
    """Direction is chosen by the tool, never assumed by the caller.

    2026-08-30: a watcher was hand-placed on the 397x53 secretary with
    direction="horizontal", cutting the human's interactive pane down to
    397x13 -- giving a log tail 397 columns while taking the height off the
    pane a person reads and types in. Both directions are evaluated and the
    one leaving the roomier remaining child wins, which on a wide-and-short
    rect is the vertical split.
    """

    def test_wide_secretary_splits_vertically(self):
        # 397x53 is the size the incident was measured at, not a spec value.
        layout = [pane("%1", "secretary", "secretary", 0, 0, 397, 53)]
        result = pwa.build_result(pwa.parse_panes(layout))
        first = result["candidates"][0]
        self.assertEqual(first["target"], "secretary")
        self.assertEqual(first["direction"], "vertical")
        self.assertEqual((first["new_w"], first["new_h"]), (198, 53))

    def test_direction_rule_holds_across_terminal_sizes(self):
        # Sizes are examples spanning small / medium / large; the assertion
        # is the *rule* (halve the longer side -> the roomier child), not a
        # per-size constant. Watcher role keeps SECRETARY_MIN out of it.
        cases = [
            (40, 10, "vertical"),    # small, wide
            (24, 40, "horizontal"),  # small, tall
            (160, 48, "vertical"),   # medium, wide
            (60, 120, "horizontal"), # medium, tall
            (397, 53, "vertical"),   # the 2026-08-30 measurement
            (200, 300, "horizontal"),
        ]
        for w, h, expected in cases:
            with self.subTest(size=f"{w}x{h}"):
                layout = [pane("%1", "pr-watch-900", "watcher", 0, 0, w, h)]
                result = pwa.build_result(pwa.parse_panes(layout))
                self.assertEqual(
                    result["candidates"][0]["direction"], expected
                )

    def test_tall_narrow_pane_splits_horizontally(self):
        # 200 wide halves to 100 (still >= MIN_PANE_WIDTH) with metric 100,
        # while halving the 300 height gives metric 150 -- the roomier child.
        layout = [pane("%1", "pr-watch-900", "watcher", 0, 0, 200, 300)]
        result = pwa.build_result(pwa.parse_panes(layout))
        self.assertEqual(result["candidates"][0]["direction"], "horizontal")

    def test_only_the_fitting_direction_is_offered(self):
        # 40 wide halves to 20 (== MIN_PANE_WIDTH, fits); 8 tall halves to 4
        # < MIN_PANE_HEIGHT, so horizontal is not an option at all.
        layout = [pane("%1", "pr-watch-900", "watcher", 0, 0, 40, 8)]
        result = pwa.build_result(pwa.parse_panes(layout))
        self.assertEqual(result["candidates"][0]["direction"], "vertical")

    def test_split_options_match_the_runtime_algorithm(self):
        """Drift check: our split_options must equal the runtime's.

        The floors are imported, but the direction rule itself is
        re-implemented here rather than calling the runtime's private
        ``_split_options``. This pins the two together over a grid so the
        re-implementation cannot drift into a different direction choice.
        """
        try:
            from claude_org_runtime.dispatcher import runner
        except ImportError:  # pragma: no cover
            self.skipTest("claude-org-runtime not installed")
        for role in ("secretary", "dispatcher", "worker"):
            for w in (0, 19, 40, 100, 200, 241, 397):
                for h in (0, 4, 8, 30, 53, 61, 300):
                    ours = pwa.split_options(
                        pwa.Pane("%1", "p", role, 0, 0, w, h)
                    )
                    theirs = runner._split_options(
                        runner.Pane(
                            id=1, name="p", role=role, focused=False,
                            x=0, y=0, width=w, height=h,
                        )
                    )
                    self.assertEqual(ours, theirs, f"{role} {w}x{h}")


class TestSmallTerminalDegradation(unittest.TestCase):
    """Degrade through the tiers on a small terminal, then report zero.

    The failure this guards against is the mirror image of the incident:
    concluding "no capacity" while something is still splittable, and
    conversely offering a candidate when nothing is.
    """

    def test_small_terminal_still_offers_the_narrow_dispatcher(self):
        # secretary 80x12 fails SECRETARY_MIN in both directions; the
        # 40x12 dispatcher halves to 20x12, clearing MIN_PANE but sitting
        # under DISPATCHER_MIN_WIDTH -- so it is offered, demoted.
        result = pwa.build_result(pwa.parse_panes(SMALL_TERMINAL_LAYOUT), "renga")
        self.assertFalse(result["capacity_exhausted"])
        self.assertEqual(names(result), ["dispatcher"])
        self.assertEqual(result["candidates"][0]["tier"], "dispatcher-narrow")
        secretary_reason = next(
            r["reason"] for r in result["rejected"] if r["target"] == "secretary"
        )
        self.assertIn("secretary floor", secretary_reason)

    def test_one_notch_smaller_yields_zero_candidates(self):
        # One notch tighter: 19 wide halves to 9 (< MIN_PANE_WIDTH 20) and
        # 8 tall halves to 4 (< MIN_PANE_HEIGHT 5), so neither direction
        # fits for any anchor and the secretary still fails its own floor.
        layout = [
            pane("%1", "secretary", "secretary", 0, 0, 80, 8),
            pane("%2", "dispatcher", "dispatcher", 0, 8, 19, 8),
            pane("%3", "pr-watch-900", "watcher", 19, 8, 19, 8),
        ]
        result = pwa.build_result(pwa.parse_panes(layout), "renga")
        self.assertTrue(result["capacity_exhausted"])
        self.assertEqual(result["candidates"], [])

    def test_zero_candidates_exits_two_for_the_skill_to_report(self):
        layout = [
            pane("%1", "secretary", "secretary", 0, 0, 80, 8),
            pane("%2", "dispatcher", "dispatcher", 0, 8, 19, 8),
        ]
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(layout))):
            with redirect_stdout(buf):
                rc = pwa.main(["--transport", "renga"])
        self.assertEqual(rc, 2)
        self.assertTrue(json.loads(buf.getvalue())["capacity_exhausted"])


class TestSecretaryFloor(unittest.TestCase):
    def test_secretary_below_its_floor_is_rejected_with_its_own_reason(self):
        # 200 wide halves to 100 < SECRETARY_MIN_WIDTH (120); 40 tall halves
        # to 20 < SECRETARY_MIN_HEIGHT (30). Both directions fail.
        layout = [pane("%1", "secretary", "secretary", 0, 0, 200, 40)]
        result = pwa.build_result(pwa.parse_panes(layout), "renga")
        self.assertTrue(result["capacity_exhausted"])
        self.assertIn("secretary floor", result["rejected"][0]["reason"])


class TestCapacityExhausted(unittest.TestCase):
    def test_full_layout_yields_no_candidate_under_renga(self):
        result = pwa.build_result(pwa.parse_panes(FULL_LAYOUT), "renga")
        self.assertTrue(result["capacity_exhausted"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["rejected"]), 3)

    def test_broker_keeps_floor_failing_anchors_as_a_last_resort_tail(self):
        # Broker panes are independent detached sessions: a small rect is
        # not a refusal, so the same layout must NOT read as "tab full".
        result = pwa.build_result(pwa.parse_panes(FULL_LAYOUT), "broker")
        self.assertFalse(result["capacity_exhausted"])
        self.assertEqual(
            [c["tier"] for c in result["candidates"]],
            ["non-geometric", "non-geometric", "non-geometric"],
        )
        # Role preference survives into the tail.
        self.assertEqual(
            names(result), ["dispatcher", "pr-watch-900", "secretary"]
        )

    def test_broker_zero_geometry_logical_pane_is_still_offered(self):
        # The broker secretary is a logical bookkeeping pane; its rect can
        # be reported as 0x0 and must not read as capacity exhaustion.
        layout = [pane("%1", "secretary", "secretary", 0, 0, 0, 0)]
        result = pwa.build_result(pwa.parse_panes(layout), "broker")
        self.assertFalse(result["capacity_exhausted"])
        self.assertEqual(result["candidates"][0]["target"], "secretary")

    def test_broker_exhaustion_means_no_anchor_role_pane_at_all(self):
        layout = [pane("%1", "worker-a", "worker", 0, 0, 280, 43)]
        result = pwa.build_result(pwa.parse_panes(layout), "broker")
        self.assertTrue(result["capacity_exhausted"])

    def test_geometric_tiers_still_outrank_the_broker_tail(self):
        layout = [
            pane("%1", "secretary", "secretary", 0, 0, 280, 43),
            pane("%2", "dispatcher", "dispatcher", 0, 44, 10, 3),
        ]
        result = pwa.build_result(pwa.parse_panes(layout), "broker")
        self.assertEqual(names(result), ["secretary", "dispatcher"])
        self.assertEqual(result["candidates"][1]["tier"], "non-geometric")


class TestPaneCap(unittest.TestCase):
    """The renga per-tab cap is reported, never turned into a verdict."""

    def _capped_layout(self):
        layout = [pane("%1", "secretary", "secretary", 0, 0, 280, 43)]
        # Fill the tab up to the cap with panes that are not anchor roles,
        # so only the cap itself distinguishes this from HEALTHY_LAYOUT.
        for i in range(2, pwa.RENGA_MAX_PANES + 1):
            layout.append(pane(f"%{i}", f"worker-{i}", "worker", 0, 44, 20, 5))
        return layout

    def test_cap_is_advisory_under_broker(self):
        result = pwa.build_result(pwa.parse_panes(self._capped_layout()), "broker")
        self.assertTrue(result["pane_cap_reached"])
        self.assertFalse(result["pane_cap_is_a_ceiling"])
        self.assertEqual(result["pane_count"], pwa.RENGA_MAX_PANES)
        # The secretary is still splittable and broker has no per-tab cap,
        # so the geometry verdict stands.
        self.assertFalse(result["capacity_exhausted"])
        self.assertEqual(names(result), ["secretary"])

    def test_cap_is_a_ceiling_under_renga(self):
        result = pwa.build_result(pwa.parse_panes(self._capped_layout()), "renga")
        self.assertTrue(result["pane_cap_is_a_ceiling"])
        # renga refuses every further spawn at the cap, so walking the
        # candidate list could only collect [split_refused] responses.
        self.assertTrue(result["capacity_exhausted"])

    def test_cap_is_not_reported_below_the_cap(self):
        result = pwa.build_result(pwa.parse_panes(HEALTHY_LAYOUT), "renga")
        self.assertFalse(result["pane_cap_reached"])

    def test_cap_note_is_rendered_in_text_output(self):
        buf = io.StringIO()
        payload = json.dumps({"panes": self._capped_layout()})
        with mock.patch.object(sys, "stdin", io.StringIO(payload)):
            with redirect_stdout(buf):
                rc = pwa.main(["--format", "text", "--transport", "broker"])
        self.assertEqual(rc, 0)
        self.assertIn("per-tab cap", buf.getvalue())

    def test_cap_exhaustion_exits_two_under_renga(self):
        buf = io.StringIO()
        payload = json.dumps({"panes": self._capped_layout()})
        with mock.patch.object(sys, "stdin", io.StringIO(payload)):
            with redirect_stdout(buf):
                rc = pwa.main(["--format", "text", "--transport", "renga"])
        self.assertEqual(rc, 2)
        self.assertIn("SUPPRESSED", buf.getvalue())

    def test_cap_mirror_matches_runtime(self):
        try:
            from claude_org_runtime.dispatcher import runner
        except ImportError:  # pragma: no cover
            self.skipTest("claude-org-runtime not installed")
        self.assertEqual(pwa._FALLBACK_RENGA_MAX_PANES, runner.RENGA_MAX_PANES)


class TestCli(unittest.TestCase):
    def _run(self, payload, extra=None):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            with redirect_stdout(buf):
                rc = pwa.main(extra or [])
        return rc, buf.getvalue()

    def test_exit_zero_and_json_on_candidates(self):
        rc, out = self._run({"panes": HEALTHY_LAYOUT})
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["candidates"][0]["target"], "dispatcher")

    def test_exit_two_when_capacity_exhausted(self):
        rc, _ = self._run({"panes": FULL_LAYOUT}, ["--transport", "renga"])
        self.assertEqual(rc, 2)

    def test_transport_flag_selects_the_floor_regime(self):
        rc, out = self._run({"panes": FULL_LAYOUT}, ["--transport", "broker"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["transport"], "broker")

    def test_bare_list_payload_is_accepted(self):
        rc, out = self._run(HEALTHY_LAYOUT)
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)["candidates"]), 2)

    def test_text_format_is_ascii_only(self):
        # CLI output must survive a cp932 console (worker Windows rule).
        rc, out = self._run({"panes": INCIDENT_LAYOUT}, ["--format", "text"])
        self.assertEqual(rc, 0)
        out.encode("ascii")
        self.assertIn("candidates (try in order):", out)

    def test_help_is_ascii_only(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                pwa.main(["--help"])
        buf.getvalue().encode("ascii")

    def test_malformed_payload_exits_one(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO('{"panes": [{"id": "%1"}]}')):
            with redirect_stdout(buf):
                rc = pwa.main([])
        self.assertEqual(rc, 1)

    def test_non_json_input_exits_one(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("not json")):
            with redirect_stdout(buf):
                rc = pwa.main([])
        self.assertEqual(rc, 1)


class TestFloorDrift(unittest.TestCase):
    """The fallback mirror must not drift from the runtime constants."""

    def test_mirror_matches_runtime(self):
        try:
            from claude_org_runtime.dispatcher import runner
        except ImportError:  # pragma: no cover - runtime is a hard dep in CI
            self.skipTest("claude-org-runtime not installed")
        self.assertEqual(
            pwa._FALLBACK_FLOORS,
            {
                "min_pane_width": runner.MIN_PANE_WIDTH,
                "min_pane_height": runner.MIN_PANE_HEIGHT,
                "secretary_min_width": runner.SECRETARY_MIN_WIDTH,
                "secretary_min_height": runner.SECRETARY_MIN_HEIGHT,
                "dispatcher_min_width": runner.DISPATCHER_MIN_WIDTH,
            },
        )

    def test_live_floors_come_from_the_runtime_when_it_is_installed(self):
        try:
            import claude_org_runtime.dispatcher.runner  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("claude-org-runtime not installed")
        self.assertEqual(
            pwa.CONSTANTS_SOURCE, "claude_org_runtime.dispatcher.runner"
        )


if __name__ == "__main__":
    unittest.main()
