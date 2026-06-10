"""Unit tests for tools/work_discovery_scan.py — Phase 1 (Issue #520).

These lock the deterministic computation-layer contract from
``docs/design/work-discovery-triage.md`` §3/§4/§5 and the §11 calibration:

* dependency extraction is high-precision (§11-3): blocking keywords with
  *immediate* ``#N`` refs only; ``Parent:`` / ``Design:`` / ``Refs`` /
  ``Closes`` / ``Discovered while working on`` / bare ``#N`` / prose-only
  ``Depends on:`` must NOT be read as blockers (avoid false exclusion);
* blocked Issues are excluded *with a visible reason*, never silently;
* the estimated axes carry ``*_estimated`` flags + ``signals[]`` (§4.4);
* ``truncated_count`` is always reported (§5.1, no silent truncation);
* ``scan()`` is a pure function — same input → same output — and never
  touches ``gh`` / git / state.db (INV-1/INV-3);
* exit codes are 0 / 10 / 2 (never 1), driven by status (§5.1).

The whole suite drives the pure core with synthetic Issue dicts; no
network, no subprocess. A final test execs the script through
``--from-file`` to confirm the stdout-JSON + exit-code wiring.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import work_discovery_scan as wds  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "work_discovery_scan.py"


def _issue(number, *, title="t", body="", labels=None, updated="2026-06-01T00:00:00Z"):
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": n} for n in (labels or [])],
        "updatedAt": updated,
    }


# ----------------------------------------------------------------------
# Dependency extraction (§4.1 / §11-3 calibration)
# ----------------------------------------------------------------------


class TestBlockingRefExtraction(unittest.TestCase):
    def test_blocked_by_immediate_ref(self):
        self.assertEqual(
            wds.extract_blocking_refs("Blocked by #482", is_epic=False), [482]
        )

    def test_depends_on_multiple_refs(self):
        self.assertEqual(
            wds.extract_blocking_refs(
                "Depends on #100, #101 and #102", is_epic=False
            ),
            [100, 101, 102],
        )

    def test_requires_ref(self):
        self.assertEqual(
            wds.extract_blocking_refs("Requires #7", is_epic=False), [7]
        )

    def test_pr_prefixed_ref(self):
        self.assertEqual(
            wds.extract_blocking_refs("Blocked by PR #55", is_epic=False), [55]
        )

    def test_prose_depends_on_yields_no_ref(self):
        # Real #177/#178: "Depends on:" describes unfiled sibling commits and
        # only mentions #80 deep in the prose ("filed under #80"). The #80 is
        # the parent umbrella, not an immediate dependency — must NOT extract.
        body = (
            "- Parent: #80\n"
            "- Design: PR #175\n"
            "- Depends on: Commit 1 (tokenizer) and Commit 2 follow-up "
            "Issues — see follow-up Issues to be filed under #80.\n"
        )
        self.assertEqual(wds.extract_blocking_refs(body, is_epic=False), [])

    def test_non_blocking_notations_ignored(self):
        # §11-3: none of these are blockers.
        body = (
            "Parent: #80\n"
            "Design: PR #175\n"
            "Refs #500\n"
            "Closes #501\n"
            "Discovered while working on #480\n"
            "See #999 for context.\n"
        )
        self.assertEqual(wds.extract_blocking_refs(body, is_epic=False), [])

    def test_task_list_ref_counts_for_non_epic(self):
        body = "## Subtasks\n- [ ] #11\n- [x] #12\n"
        self.assertEqual(
            wds.extract_blocking_refs(body, is_epic=False), [11]
        )

    def test_task_list_ignored_for_epic(self):
        # An epic's child checklist is tracking, not a blocker on the epic.
        body = "## Children\n- [ ] #11\n- [ ] #12\n"
        self.assertEqual(wds.extract_blocking_refs(body, is_epic=True), [])

    def test_empty_body(self):
        self.assertEqual(wds.extract_blocking_refs(None, is_epic=False), [])
        self.assertEqual(wds.extract_blocking_refs("", is_epic=False), [])


class TestClassifyDependency(unittest.TestCase):
    def test_resolved_when_refs_all_closed(self):
        issue = _issue(10, body="Blocked by #5")
        status, open_refs = wds.classify_dependency(issue, open_refs=set())
        self.assertEqual(status, "resolved")
        self.assertEqual(open_refs, [])

    def test_blocked_when_ref_open(self):
        issue = _issue(10, body="Blocked by #5")
        status, open_refs = wds.classify_dependency(issue, open_refs={5})
        self.assertEqual(status, "blocked")
        self.assertEqual(open_refs, [5])

    def test_block_label_forces_blocked(self):
        issue = _issue(10, body="no refs", labels=["blocked"])
        status, open_refs = wds.classify_dependency(issue, open_refs=set())
        self.assertEqual(status, "blocked")
        self.assertEqual(open_refs, [])

    def test_on_hold_label_forces_blocked(self):
        issue = _issue(10, labels=["on-hold"])
        status, _ = wds.classify_dependency(issue, open_refs=set())
        self.assertEqual(status, "blocked")

    def test_unknown_ref_treated_resolved(self):
        # A ref to a number not in open_refs (closed issue / merged PR /
        # nonexistent) is resolved — no over-exclusion (§11-3).
        issue = _issue(10, body="Depends on #9999")
        status, _ = wds.classify_dependency(issue, open_refs={1, 2, 3})
        self.assertEqual(status, "resolved")


# ----------------------------------------------------------------------
# Priority (§4.1, §11-2 degradation)
# ----------------------------------------------------------------------


class TestPriority(unittest.TestCase):
    def test_priority_high_label(self):
        level, _ = wds.compute_priority(_issue(1, labels=["priority:high"]))
        self.assertEqual(level, "high")

    def test_p0_label_high(self):
        level, _ = wds.compute_priority(_issue(1, labels=["p0"]))
        self.assertEqual(level, "high")

    def test_backlog_label_low(self):
        level, sig = wds.compute_priority(_issue(1, labels=["backlog"]))
        self.assertEqual(level, "low")
        self.assertTrue(any("backlog" in s for s in sig))

    def test_default_medium_without_labels(self):
        # §11-2: this repo has no priority labels / milestones → medium.
        level, sig = wds.compute_priority(_issue(1, labels=["enhancement"]))
        self.assertEqual(level, "medium")
        self.assertTrue(any("default medium" in s for s in sig))


# ----------------------------------------------------------------------
# Effort (§4.1 / §4.4)
# ----------------------------------------------------------------------


class TestEffort(unittest.TestCase):
    def test_size_label_not_estimated(self):
        size, estimated, sig = wds.estimate_effort(_issue(1, labels=["size:L"]))
        self.assertEqual(size, "L")
        self.assertFalse(estimated)
        self.assertTrue(any("label:size:l" in s for s in sig))

    def test_bare_size_label(self):
        size, estimated, _ = wds.estimate_effort(_issue(1, labels=["M"]))
        self.assertEqual(size, "M")
        self.assertFalse(estimated)

    def test_heuristic_small(self):
        size, estimated, sig = wds.estimate_effort(_issue(1, body="short"))
        self.assertEqual(size, "S")
        self.assertTrue(estimated)
        self.assertTrue(any("estimated effort" in s for s in sig))

    def test_heuristic_large_by_length(self):
        size, estimated, _ = wds.estimate_effort(_issue(1, body="x" * 2500))
        self.assertEqual(size, "L")
        self.assertTrue(estimated)

    def test_heuristic_large_by_criteria(self):
        body = "intro\n" + "\n".join("- [ ] item" for _ in range(9))
        size, estimated, _ = wds.estimate_effort(_issue(1, body=body))
        self.assertEqual(size, "L")
        self.assertTrue(estimated)


# ----------------------------------------------------------------------
# Estimated axes (§4.2 / §4.4)
# ----------------------------------------------------------------------


class TestEstimatedAxes(unittest.TestCase):
    def test_parallelizable_leaf(self):
        ok, sig = wds.estimate_parallelizable([])
        self.assertTrue(ok)
        self.assertTrue(any("leaf" in s for s in sig))

    def test_not_parallelizable_with_open_refs(self):
        ok, sig = wds.estimate_parallelizable([5, 6])
        self.assertFalse(ok)
        self.assertTrue(any("#5" in s for s in sig))

    def test_unblocked_by_recent_merge_via_blocking_ref(self):
        ok, sig = wds.estimate_unblocked_by_recent_merge(
            _issue(10), blocking_refs=[200], recent_merge_pr_numbers={200},
            recent_merge_linked_issues=set(),
        )
        self.assertTrue(ok)
        self.assertTrue(any("#200" in s for s in sig))

    def test_unblocked_by_recent_merge_via_pr_link(self):
        ok, _ = wds.estimate_unblocked_by_recent_merge(
            _issue(10), blocking_refs=[], recent_merge_pr_numbers=set(),
            recent_merge_linked_issues={10},
        )
        self.assertTrue(ok)

    def test_no_recent_merge_linkage(self):
        ok, sig = wds.estimate_unblocked_by_recent_merge(
            _issue(10), blocking_refs=[1], recent_merge_pr_numbers=set(),
            recent_merge_linked_issues=set(),
        )
        self.assertFalse(ok)
        self.assertTrue(any("no recent-merge" in s for s in sig))


class TestSummary(unittest.TestCase):
    def test_skips_headings_and_quotes(self):
        body = "# Title\n\n> quote\n\nThe real first line.\n"
        self.assertEqual(
            wds.extract_summary(body, "fallback"), "The real first line."
        )

    def test_falls_back_to_title(self):
        self.assertEqual(wds.extract_summary("# only heading\n", "T"), "T")

    def test_truncates_long_line(self):
        out = wds.extract_summary("w " * 200, "t")
        self.assertLessEqual(len(out), 121)
        self.assertTrue(out.endswith("…"))


# ----------------------------------------------------------------------
# Ranking + full scan (§4.3 / §5.1)
# ----------------------------------------------------------------------


class TestRankingAndScan(unittest.TestCase):
    def test_priority_beats_effort(self):
        issues = [
            _issue(1, labels=["backlog"], body="short"),  # low / S
            _issue(2, labels=["priority:high"], body="x" * 3000),  # high / L
        ]
        result = wds.scan(issues, set(), [], wds.ScanConfig())
        self.assertEqual(result["candidates"][0]["issue"], 2)

    def test_recent_merge_ranks_above_plain(self):
        issues = [
            _issue(1, body="plain"),
            _issue(2, body="Refs nothing"),
        ]
        merges = [{"number": 900, "title": "x", "body": "Closes #2"}]
        result = wds.scan(issues, set(), merges, wds.ScanConfig())
        self.assertEqual(result["candidates"][0]["issue"], 2)
        self.assertTrue(
            result["candidates"][0]["unblocked_by_recent_merge"]
        )

    def test_truncation_reported(self):
        issues = [_issue(i, body="b") for i in range(1, 8)]  # 7 resolved
        result = wds.scan(issues, set(), [], wds.ScanConfig(top_n=3))
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["truncated_count"], 4)

    def test_blocked_excluded_with_reason_not_silent(self):
        issues = [
            _issue(10, body="Blocked by #5"),  # blocked (#5 open)
            _issue(5, body="open dep"),
        ]
        result = wds.scan(issues, set(), [], wds.ScanConfig())
        excluded = {e["issue"]: e for e in result["excluded_blocked"]}
        self.assertIn(10, excluded)
        self.assertEqual(excluded[10]["blocking_refs"], [5])
        self.assertTrue(excluded[10]["note"])  # reason present, not silent

    def test_no_candidates_status(self):
        issues = [_issue(10, body="Blocked by #5", labels=["blocked"])]
        result = wds.scan(issues, set(), [], wds.ScanConfig())
        self.assertEqual(result["status"], "no_candidates")
        self.assertEqual(result["candidate_count"], 0)

    def test_recommendation_is_rank_1(self):
        issues = [
            _issue(1, body="plain"),
            _issue(2, labels=["priority:high"], body="b"),
        ]
        result = wds.scan(issues, set(), [], wds.ScanConfig())
        self.assertEqual(result["recommendation"]["issue"], 2)
        self.assertTrue(result["recommendation"]["reason"])

    def test_estimated_flags_always_present(self):
        result = wds.scan([_issue(1, body="b")], set(), [], wds.ScanConfig())
        cand = result["candidates"][0]
        for key in (
            "effort_estimated",
            "parallelizable_estimated",
            "unblocked_by_recent_merge_estimated",
        ):
            self.assertIn(key, cand)
        self.assertTrue(cand["parallelizable_estimated"])
        self.assertTrue(cand["signals"])  # auditable signals present

    def test_generated_for_propagates(self):
        result = wds.scan(
            [_issue(1, body="b")], set(), [], wds.ScanConfig(trigger="post_merge")
        )
        self.assertEqual(result["generated_for"], "post_merge")

    def test_internal_fields_stripped(self):
        result = wds.scan([_issue(1, body="b")], set(), [], wds.ScanConfig())
        self.assertNotIn("_updated_at", result["candidates"][0])

    def test_determinism_same_input_same_output(self):
        issues = [
            _issue(3, labels=["priority:high"], body="a"),
            _issue(1, body="b"),
            _issue(2, labels=["backlog"], body="c"),
        ]
        r1 = wds.scan(issues, set(), [], wds.ScanConfig())
        r2 = wds.scan(
            [dict(i, labels=list(i["labels"])) for i in issues],
            set(), [], wds.ScanConfig(),
        )
        self.assertEqual(
            json.dumps(r1, sort_keys=True, ensure_ascii=False),
            json.dumps(r2, sort_keys=True, ensure_ascii=False),
        )

    def _cand(self, number, *, priority="medium", parallelizable=True):
        # A hand-built candidate dict for ranking-mechanism tests. (In a real
        # scan every candidate is parallelizable by construction — see
        # estimate_parallelizable's docstring — so we synthesize one that is
        # not, purely to exercise the free_panes weighting.)
        return {
            "issue": number,
            "priority": priority,
            "effort": "M",
            "parallelizable": parallelizable,
            "unblocked_by_recent_merge": False,
            "_updated_at": "2026-06-01T00:00:00Z",
        }

    def test_free_panes_positive_boosts_parallelizable(self):
        cands = [
            self._cand(1, parallelizable=False),
            self._cand(2, parallelizable=True),
        ]
        top, _ = wds.rank_candidates(cands, top_n=3, free_panes=2)
        self.assertEqual(top[0]["issue"], 2)  # parallel boosted ahead

    def test_free_panes_zero_neutralizes_parallel_boost(self):
        # With no free pane to fill, parallelism carries no weight; the tie
        # falls through to recency, which is equal → stable original order.
        cands = [
            self._cand(1, parallelizable=False),
            self._cand(2, parallelizable=True),
        ]
        top, _ = wds.rank_candidates(cands, top_n=3, free_panes=0)
        self.assertEqual(top[0]["issue"], 1)


# ----------------------------------------------------------------------
# CLI wiring: stdout JSON + exit codes (§5.1)
# ----------------------------------------------------------------------


class TestCliWiring(unittest.TestCase):
    def _run(self, bundle):
        fd, name = tempfile.mkstemp(suffix=".json", prefix="wds_bundle_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(bundle, f)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--from-file", name],
                capture_output=True,
                text=True,
            )
        finally:
            os.unlink(name)
        return proc

    def test_exit_10_when_candidates(self):
        bundle = {
            "issues": [_issue(1, body="b")],
            "open_pr_numbers": [],
            "recent_merges": [],
        }
        proc = self._run(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_CANDIDATES_FOUND)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "candidates_found")

    def test_exit_0_when_no_candidates(self):
        bundle = {
            "issues": [_issue(1, body="x", labels=["blocked"])],
            "open_pr_numbers": [],
            "recent_merges": [],
        }
        proc = self._run(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_NO_CANDIDATES)
        self.assertEqual(json.loads(proc.stdout)["status"], "no_candidates")

    def test_exit_2_on_error(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--from-file", "/no/such/file.json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        self.assertEqual(json.loads(proc.stdout)["status"], "error")

    def test_exit_codes_never_collide_with_1(self):
        # Guard the §5.1 rationale: a Python crash exits 1, which must not be
        # reachable as a meaningful status code.
        self.assertNotIn(1, {wds.EXIT_NO_CANDIDATES, wds.EXIT_CANDIDATES_FOUND, wds.EXIT_ERROR})

    def test_error_json_keeps_fixed_schema(self):
        # Codex review (Major): the error branch must carry the same audit
        # fields as a normal result, not a bespoke shape.
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--from-file", "/no/such/file.json"],
            capture_output=True,
            text=True,
        )
        data = json.loads(proc.stdout)
        for key in (
            "status",
            "generated_for",
            "candidate_count",
            "truncated_count",
            "candidates",
            "recommendation",
            "excluded_blocked",
            "error",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["candidate_count"], 0)
        self.assertEqual(data["truncated_count"], 0)


class TestCommentsAndMilestone(unittest.TestCase):
    """Codex review (Major/Minor): blockers in comments + milestone tier."""

    def test_blocker_in_comment_detected(self):
        # §4.1: a blocker added later in a *comment* must still be detected.
        issue = {
            "number": 10,
            "title": "t",
            "body": "no blocker in body",
            "labels": [],
            "comments": [{"body": "Update: Blocked by #5 now."}],
            "updatedAt": "2026-06-01T00:00:00Z",
        }
        status, open_refs = wds.classify_dependency(issue, open_refs={5})
        self.assertEqual(status, "blocked")
        self.assertEqual(open_refs, [5])

    def test_comment_blocker_excluded_in_scan(self):
        issues = [
            {
                "number": 10,
                "title": "t",
                "body": "b",
                "labels": [],
                "comments": [{"body": "Depends on #5"}],
                "updatedAt": "2026-06-01T00:00:00Z",
            },
            _issue(5, body="open dep"),
        ]
        result = wds.scan(issues, set(), [], wds.ScanConfig())
        excluded = {e["issue"] for e in result["excluded_blocked"]}
        self.assertIn(10, excluded)

    def test_unblocked_via_ref_closed_by_recent_merge(self):
        # Codex review (Major): Depends on #100 + a recent PR that Closes
        # #100 → this issue is now unblocked-by-recent-merge.
        ok, sig = wds.estimate_unblocked_by_recent_merge(
            _issue(10),
            blocking_refs=[100],
            recent_merge_pr_numbers=set(),
            recent_merge_linked_issues={100},
        )
        self.assertTrue(ok)
        self.assertTrue(any("#100" in s for s in sig))

    def test_unblocked_via_closed_ref_in_full_scan(self):
        issues = [_issue(10, body="Depends on #100")]
        merges = [{"number": 900, "title": "x", "body": "Closes #100"}]
        result = wds.scan(issues, set(), merges, wds.ScanConfig())
        self.assertTrue(
            result["candidates"][0]["unblocked_by_recent_merge"]
        )

    def test_milestone_emitted_as_signal(self):
        issue = _issue(1, body="b")
        issue["milestone"] = {"title": "v1.0"}
        level, sig = wds.compute_priority(issue)
        self.assertEqual(level, "medium")
        self.assertTrue(any("milestone:v1.0" in s for s in sig))

    def test_milestone_breaks_tie_above_no_milestone(self):
        a = _issue(1, body="b")
        b = _issue(2, body="b")
        b["milestone"] = {"title": "v1.0"}
        result = wds.scan([a, b], set(), [], wds.ScanConfig())
        # Equal priority/effort/etc.; #2 (milestoned) ranks first.
        self.assertEqual(result["candidates"][0]["issue"], 2)

    def test_milestone_internal_field_stripped(self):
        issue = _issue(1, body="b")
        issue["milestone"] = {"title": "v1.0"}
        result = wds.scan([issue], set(), [], wds.ScanConfig())
        self.assertNotIn("_has_milestone", result["candidates"][0])


if __name__ == "__main__":
    unittest.main()
