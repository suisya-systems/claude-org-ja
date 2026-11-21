"""Acceptance tests: scan emits design candidates (Issue 1295).

With ``ScanConfig(design_candidates=True)`` an Issue that is blocked by an
open Issue/PR is still built as a candidate and passed through the same
ranking (the goal ranker when given), and reported under
``design_candidates`` with its blocking refs: work the conveyor may take up
to the design stage while the blocker lands. It stays out of ``candidates``
and stays listed in ``excluded_blocked``. Without the flag the key is
present and empty.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import work_discovery_scan as wds  # noqa: E402

JA = "suisya-systems/claude-org-ja"


def _issue(number, body=""):
    return {"number": number, "title": f"t{number}", "body": body,
            "labels": [], "updatedAt": "2026-11-01T00:00:00Z"}


def _bundle(issues):
    return {"repo": JA, "issues": issues, "open_pr_numbers": [], "recent_merges": []}


def _config(**kw):
    try:
        return wds.ScanConfig(**kw)
    except TypeError as exc:  # before the field exists: fail, not error
        raise AssertionError(f"ScanConfig does not accept {sorted(kw)}: {exc}")


class DesignCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.bundles = [_bundle([
            _issue(60, "the blocker"),
            _issue(61, "Blocked by #60"),
            _issue(62, "independent work"),
        ])]

    def _numbers(self, entries):
        return sorted(e["issue"] for e in entries)

    def test_blocked_issue_is_a_design_candidate(self):
        out = wds.scan_repos(self.bundles, _config(design_candidates=True))
        self.assertIn("design_candidates", out)
        self.assertEqual(self._numbers(out["design_candidates"]), [61])
        dc = out["design_candidates"][0]
        self.assertEqual([str(r) for r in dc["blocking_refs"]], [f"{JA}#60"])
        # unchanged: not a normal candidate, still reported as excluded
        self.assertNotIn(61, self._numbers(out["candidates"]))
        self.assertIn(61, self._numbers(out["excluded_blocked"]))
        self.assertEqual(self._numbers(out["candidates"]), [60, 62])

    def test_without_flag_the_key_is_present_and_empty(self):
        out = wds.scan_repos(self.bundles, wds.ScanConfig())
        self.assertIn("design_candidates", out)
        self.assertEqual(out["design_candidates"], [])

    def test_goal_ranker_filters_design_candidates_too(self):
        calls = []

        def ranker(pool):
            calls.append(sorted(c["issue"] for c in pool))
            kept = [c for c in pool if c["issue"] != 61]
            return {"candidates": kept, "truncated_count": 0,
                    "recommendation": None, "excluded_goal": [], "goal_rank": {}}

        bundles = [_bundle([_issue(60), _issue(61, "Blocked by #60"),
                            _issue(63, "Blocked by #60")])]
        out = wds.scan_repos(bundles, _config(design_candidates=True), goal_ranker=ranker)
        self.assertIn([61, 63], calls, "design pool must go through the goal ranker")
        self.assertEqual(self._numbers(out["design_candidates"]), [63])

    def test_internal_fields_do_not_leak(self):
        out = wds.scan_repos(self.bundles, _config(design_candidates=True))
        for dc in out["design_candidates"]:
            self.assertFalse([k for k in dc if k.startswith("_")])

    def test_cli_accepts_the_flag(self):
        src = Path(wds.__file__).read_text(encoding="utf-8")
        self.assertIn('"--design-candidates"', src)


if __name__ == "__main__":
    unittest.main()
