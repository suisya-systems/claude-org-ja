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
from unittest import mock

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

    def test_task_list_pure_ref_run_counts(self):
        # A task item that is *only* refs is a genuine sub-task dependency.
        body = "- [ ] #11\n- [ ] #12, #13 and #14\n"
        self.assertEqual(
            wds.extract_blocking_refs(body, is_epic=False), [11, 12, 13, 14]
        )

    def test_task_list_prose_after_ref_is_not_a_blocker(self):
        # §11-3: `- [ ] #123 を参考に確認する` merely *mentions* #123 — it is
        # not a dependency. Counting it would wrongly exclude the candidate.
        body = "- [ ] #123 を参考に確認する\n- [ ] #124 review notes\n"
        self.assertEqual(wds.extract_blocking_refs(body, is_epic=False), [])

    def test_task_list_prose_before_ref_is_not_a_blocker(self):
        # `- [ ] Fix #11` — prose before the ref → mention, not a blocker.
        self.assertEqual(
            wds.extract_blocking_refs("- [ ] Fix #11", is_epic=False), []
        )

    def test_task_list_mixed_pure_and_prose(self):
        # Only the pure-ref items count; the prose-annotated one is dropped.
        body = "- [ ] #11\n- [ ] #99 を参考に\n"
        self.assertEqual(wds.extract_blocking_refs(body, is_epic=False), [11])

    def test_task_list_ignored_for_epic(self):
        # An epic's child checklist is tracking, not a blocker on the epic.
        body = "## Children\n- [ ] #11\n- [ ] #12\n"
        self.assertEqual(wds.extract_blocking_refs(body, is_epic=True), [])

    def test_empty_body(self):
        self.assertEqual(wds.extract_blocking_refs(None, is_epic=False), [])
        self.assertEqual(wds.extract_blocking_refs("", is_epic=False), [])

    def test_negated_not_blocked_by_yields_no_ref(self):
        # §11-3: a negated keyword is NOT a blocker — must not exclude.
        self.assertEqual(
            wds.extract_blocking_refs("not blocked by #5", is_epic=False), []
        )

    def test_negated_no_longer_blocked_by_yields_no_ref(self):
        self.assertEqual(
            wds.extract_blocking_refs("no longer blocked by #5", is_epic=False),
            [],
        )

    def test_unblocked_by_yields_no_ref(self):
        # "unblocked" must not match the "blocked by" keyword mid-word.
        self.assertEqual(
            wds.extract_blocking_refs("unblocked by #5", is_epic=False), []
        )

    def test_negated_doesnt_depend_on_yields_no_ref(self):
        self.assertEqual(
            wds.extract_blocking_refs("This doesn't depend on #5", is_epic=False),
            [],
        )

    def test_negation_only_when_adjacent_to_keyword(self):
        # The "not" here negates "a blocker", not the later "blocked by" —
        # a comma breaks the negation run, so `blocked by #5` still counts.
        self.assertEqual(
            wds.extract_blocking_refs(
                "This is not a blocker, but blocked by #5", is_epic=False
            ),
            [5],
        )

    def test_negation_with_intervening_adverb(self):
        # "not currently blocked by", "not yet blocked by" are negations even
        # though an adverb sits between "not" and the keyword.
        for s in (
            "not currently blocked by #5",
            "not yet blocked by #5",
            "doesn't currently depend on #5",
        ):
            self.assertEqual(
                wds.extract_blocking_refs(s, is_epic=False), [], msg=s
            )

    def test_far_away_negation_with_punctuation_still_blocks(self):
        # A "not" separated from the keyword by punctuation does not suppress.
        self.assertEqual(
            wds.extract_blocking_refs(
                "We fixed it (not the UI). Now blocked by #5", is_epic=False
            ),
            [5],
        )

    def test_multiple_clauses_on_one_line(self):
        # Both clauses on a single line must be extracted, not just the first.
        self.assertEqual(
            wds.extract_blocking_refs(
                "Blocked by #1; depends on #2", is_epic=False
            ),
            [1, 2],
        )
        self.assertEqual(
            wds.extract_blocking_refs(
                "Requires #3 and Blocked by #4", is_epic=False
            ),
            [3, 4],
        )

    def test_ref_on_next_line_not_linked_to_keyword(self):
        # A keyword and a bare `#N` on the *next* line are not linked (§11-3
        # same-line precision: avoid spurious cross-line blockers).
        self.assertEqual(
            wds.extract_blocking_refs("Blocked by\n#5", is_epic=False), []
        )


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
            recent_merge_closed_issues=set(), recent_merge_referenced_issues=set(),
        )
        self.assertTrue(ok)
        self.assertTrue(any("#200" in s for s in sig))

    def test_unblocked_by_recent_merge_via_pr_link(self):
        ok, _ = wds.estimate_unblocked_by_recent_merge(
            _issue(10), blocking_refs=[], recent_merge_pr_numbers=set(),
            recent_merge_closed_issues=set(), recent_merge_referenced_issues={10},
        )
        self.assertTrue(ok)

    def test_no_recent_merge_linkage(self):
        ok, sig = wds.estimate_unblocked_by_recent_merge(
            _issue(10), blocking_refs=[1], recent_merge_pr_numbers=set(),
            recent_merge_closed_issues=set(), recent_merge_referenced_issues=set(),
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

    def test_free_panes_none_does_not_boost(self):
        # `--free-panes` unspecified (None) is *unknown*, not "panes free":
        # per the documented contract it must NOT boost parallelizable, so the
        # ranking is identical to free_panes=0 (stable original order here).
        cands = [
            self._cand(1, parallelizable=False),
            self._cand(2, parallelizable=True),
        ]
        top, _ = wds.rank_candidates(cands, top_n=3, free_panes=None)
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
        # The error branch must carry the same audit fields as a normal
        # result, not a bespoke shape.
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
            "input_truncated",
            "candidates",
            "recommendation",
            "excluded_blocked",
            "error",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["candidate_count"], 0)
        self.assertEqual(data["truncated_count"], 0)

    def test_argparse_error_emits_json_exit_2(self):
        # A CLI parse error must still print a single JSON object to stdout
        # and exit 2, not bare usage on stderr.
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--top-n", "not-an-int"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)  # stdout is valid JSON
        self.assertEqual(data["status"], "error")
        self.assertIn("argument error", data["error"])

    def test_input_truncated_present_in_normal_output(self):
        bundle = {
            "issues": [_issue(1, body="b")],
            "open_pr_numbers": [],
            "recent_merges": [],
        }
        proc = self._run(bundle)
        data = json.loads(proc.stdout)
        self.assertIn("input_truncated", data)
        self.assertEqual(
            data["input_truncated"], {"open_issues": False, "open_prs": False}
        )

    def test_top_n_zero_rejected_as_error(self):
        # `--top-n 0` would silently return an empty `top` (status
        # no_candidates / exit 0) even with candidates — a contract break.
        # It must be rejected as an argument error (exit 2, JSON envelope).
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--top-n", "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("--top-n", data["error"])

    def test_top_n_negative_rejected_as_error(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--top-n=-5"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")

    def test_top_n_error_envelope_keeps_trigger_context(self):
        # The error envelope must carry the real --trigger in generated_for,
        # not a hardcoded "manual" (delivery layer reads the context).
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--top-n", "0", "--trigger", "post_merge"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["generated_for"], "post_merge")

    def test_recent_merges_zero_rejected_as_error(self):
        # `--recent-merges 0` would request a nonsensical `gh --limit 0` and
        # break the 直近 K 件 contract — must be rejected (exit 2).
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--recent-merges", "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("--recent-merges", data["error"])

    def test_recent_merges_negative_rejected_as_error(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--recent-merges=-3"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")

    def test_free_panes_negative_rejected_as_error(self):
        # `--free-panes` is a non-negative count; 0 is valid, negative is not.
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--free-panes=-2"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("--free-panes", data["error"])


class TestCommentsAndMilestone(unittest.TestCase):
    """Blockers in comments + milestone tier."""

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
        # Depends on #100 + a recent PR that Closes #100 → this issue is now
        # unblocked-by-recent-merge.
        ok, sig = wds.estimate_unblocked_by_recent_merge(
            _issue(10),
            blocking_refs=[100],
            recent_merge_pr_numbers=set(),
            recent_merge_closed_issues={100},
            recent_merge_referenced_issues={100},
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

    def test_bare_refs_does_not_close_blocking_ref(self):
        # A recent PR that only `Refs #100`
        # (not Closes/Fixes/Resolves) must NOT mark #100 as resolved, so an
        # issue depending on #100 is not unblocked via the blocking-ref side.
        ok, _ = wds.estimate_unblocked_by_recent_merge(
            _issue(10),
            blocking_refs=[100],
            recent_merge_pr_numbers=set(),
            recent_merge_closed_issues=set(),  # #100 only referenced, not closed
            recent_merge_referenced_issues={100},
        )
        self.assertFalse(ok)

    def test_bare_refs_negative_in_full_scan(self):
        issues = [_issue(10, body="Depends on #100")]
        merges = [{"number": 900, "title": "x", "body": "Refs #100"}]
        result = wds.scan(issues, set(), merges, wds.ScanConfig())
        self.assertFalse(
            result["candidates"][0]["unblocked_by_recent_merge"]
        )

    def test_pr_close_refs_single(self):
        self.assertEqual(wds._pr_close_refs("Closes #100"), {100})

    def test_pr_close_refs_comma_separated(self):
        # `Closes #100, #101` must capture BOTH, not just the first.
        self.assertEqual(wds._pr_close_refs("Closes #100, #101"), {100, 101})

    def test_pr_close_refs_space_separated(self):
        self.assertEqual(wds._pr_close_refs("Fixes #100 #101 #102"), {100, 101, 102})

    def test_pr_close_refs_and_separated(self):
        self.assertEqual(
            wds._pr_close_refs("Resolves #100, #101 and #102"), {100, 101, 102}
        )

    def test_pr_close_refs_mixed_case_keywords(self):
        # Keyword casing is irrelevant; every following #N is captured.
        self.assertEqual(
            wds._pr_close_refs("CLOSED #100, #101\nfix #200 and #201"),
            {100, 101, 200, 201},
        )

    def test_pr_close_refs_bare_ref_not_captured(self):
        # Only close-keyword runs count; a bare `Refs #100` closes nothing.
        self.assertEqual(wds._pr_close_refs("Refs #100, #101"), set())

    def test_pr_close_refs_negated_does_not_close(self):
        # The GitHub auto-close false-positive that reopened #520: a PR body
        # saying it does NOT close #N must not mark #N closed.
        self.assertEqual(wds._pr_close_refs("This does not close #100"), set())
        self.assertEqual(wds._pr_close_refs("no longer closes #5"), set())

    def test_pr_close_refs_mixed_affirmed_and_negated(self):
        self.assertEqual(
            wds._pr_close_refs("Closes #100 but does not close #101"), {100}
        )

    def test_recent_merge_negated_close_not_unblocking(self):
        # Full-scan: a recent PR that disclaims closing #100 must NOT unblock
        # an issue depending on #100 via the closed-ref path.
        issues = [_issue(10, body="Depends on #100")]
        merges = [{"number": 900, "title": "x", "body": "This does not close #100"}]
        result = wds.scan(issues, set(), merges, wds.ScanConfig())
        self.assertFalse(result["candidates"][0]["unblocked_by_recent_merge"])

    def test_multi_issue_close_in_full_scan(self):
        # A single recent PR closing several issues must unblock dependents
        # on *each* listed issue, not just the first.
        issues = [
            _issue(10, body="Depends on #100"),
            _issue(11, body="Depends on #101"),
        ]
        merges = [{"number": 900, "title": "x", "body": "Closes #100, #101"}]
        result = wds.scan(issues, set(), merges, wds.ScanConfig())
        unblocked = {
            c["issue"]: c["unblocked_by_recent_merge"]
            for c in result["candidates"]
        }
        self.assertTrue(unblocked[10])
        self.assertTrue(unblocked[11])

    def test_pr_referenced_refs_comma_separated(self):
        # The reference side is symmetric with the close side: `Refs #10, #11`
        # must capture BOTH, not just the first.
        self.assertEqual(wds._pr_referenced_refs("Refs #10, #11"), {10, 11})

    def test_pr_referenced_refs_and_separated(self):
        self.assertEqual(
            wds._pr_referenced_refs("Ref #10 and #11 & #12"), {10, 11, 12}
        )

    def test_pr_close_refs_colon_form(self):
        # `Closes: #1` / `Fixes: #1, #2` (colon notation) must be captured.
        self.assertEqual(wds._pr_close_refs("Closes: #1"), {1})
        self.assertEqual(wds._pr_close_refs("Fixes: #1, #2"), {1, 2})

    def test_pr_referenced_refs_colon_form(self):
        self.assertEqual(wds._pr_referenced_refs("Refs: #9"), {9})

    def test_multi_ref_in_full_scan(self):
        # A recent PR that references several issues marks every one of them
        # as referenced-by-recent-merge (the natural-follow-up axis).
        issues = [_issue(10, body="x"), _issue(11, body="y")]
        merges = [{"number": 900, "title": "z", "body": "Refs #10, #11"}]
        result = wds.scan(issues, set(), merges, wds.ScanConfig())
        unblocked = {
            c["issue"]: c["unblocked_by_recent_merge"]
            for c in result["candidates"]
        }
        self.assertTrue(unblocked[10])
        self.assertTrue(unblocked[11])

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


class TestFetchRecentMerges(unittest.TestCase):
    """`gh pr list` defaults to createdAt-desc, NOT merge-time, so
    fetch_recent_merges must (a) ask the server to order by recency and
    (b) sort by mergedAt before taking the 直近 K 件 (§4.2)."""

    _UNSORTED = [
        {"number": 1, "title": "a", "body": "", "mergedAt": "2026-01-01T00:00:00Z"},
        {"number": 3, "title": "c", "body": "", "mergedAt": "2026-03-01T00:00:00Z"},
        {"number": 2, "title": "b", "body": "", "mergedAt": "2026-02-01T00:00:00Z"},
    ]

    def test_server_side_recency_ordering_requested(self):
        # The fix for createdAt-desc dropping recent merges is a server-side
        # recency sort + an over-fetched pool — assert both reach the gh call.
        with mock.patch.object(
            wds, "_run_gh_json", return_value=[]
        ) as gh:
            wds.fetch_recent_merges("owner/repo", 7)
        argv = gh.call_args[0][0]
        self.assertIn("--search", argv)
        self.assertEqual(argv[argv.index("--search") + 1], "sort:updated-desc")
        # Over-fetch K × factor so a freshly-commented old merge can't crowd
        # out a genuine recent merge before the client mergedAt top-K slice.
        self.assertEqual(
            argv[argv.index("--limit") + 1],
            str(7 * wds._RECENT_MERGE_OVERFETCH),
        )
        self.assertIn("merged", argv)

    def test_overfetched_pool_trimmed_to_requested_k(self):
        # Fetch returns the over-fetched pool; the result is the mergedAt
        # top-K for the *requested* K, regardless of the larger fetch.
        pool = [
            {"number": n, "mergedAt": f"2026-{n:02d}-01T00:00:00Z"}
            for n in range(1, 10)
        ]
        with mock.patch.object(wds, "_run_gh_json", return_value=pool):
            merges = wds.fetch_recent_merges(None, 3)
        self.assertEqual([m["number"] for m in merges], [9, 8, 7])

    def test_sorted_by_merged_at_desc(self):
        with mock.patch.object(wds, "_run_gh_json", return_value=list(self._UNSORTED)):
            merges = wds.fetch_recent_merges(None, 10)
        self.assertEqual([m["number"] for m in merges], [3, 2, 1])

    def test_defensive_limit_applied_after_sort(self):
        # Even if the fetch over-returns, the client side caps to the K
        # *newest* by mergedAt (not the first K in arrival order).
        with mock.patch.object(wds, "_run_gh_json", return_value=list(self._UNSORTED)):
            merges = wds.fetch_recent_merges(None, 2)
        self.assertEqual([m["number"] for m in merges], [3, 2])

    def test_missing_merged_at_sorts_last(self):
        data = [
            {"number": 1, "mergedAt": None},
            {"number": 2, "mergedAt": "2026-05-01T00:00:00Z"},
        ]
        with mock.patch.object(wds, "_run_gh_json", return_value=data):
            merges = wds.fetch_recent_merges(None, 10)
        self.assertEqual([m["number"] for m in merges], [2, 1])

    def test_non_list_payload_raises(self):
        # A non-array gh payload is an anomaly, not "no merges": it must raise
        # (→ exit 2), never silently degrade to [] (which reads as exit 0).
        with mock.patch.object(wds, "_run_gh_json", return_value=None):
            with self.assertRaises(wds.GhError):
                wds.fetch_recent_merges(None, 10)


class TestFetchRobustness(unittest.TestCase):
    """A non-array gh payload is an error (exit 2), never a silent empty."""

    def test_open_issues_non_list_raises(self):
        with mock.patch.object(wds, "_run_gh_json", return_value={"x": 1}):
            with self.assertRaises(wds.GhError):
                wds.fetch_open_issues(None)

    def test_open_pr_numbers_non_list_raises(self):
        with mock.patch.object(wds, "_run_gh_json", return_value=None):
            with self.assertRaises(wds.GhError):
                wds.fetch_open_pr_numbers(None)


class TestMalformedFieldNormalization(unittest.TestCase):
    """Non-list `comments` / `labels` shapes must normalize to empty, never
    crash the pure core (a bare comment *count* is a real gh-shape risk)."""

    def test_comments_as_count_does_not_crash(self):
        self.assertEqual(wds._comment_bodies({"comments": 5}), [])

    def test_labels_as_string_does_not_crash(self):
        self.assertEqual(wds._label_names({"labels": "blocked"}), [])

    def test_scan_survives_non_list_comments_and_labels(self):
        issue = {"number": 1, "title": "t", "body": "b", "comments": 3, "labels": 0}
        result = wds.scan([issue], set(), [], wds.ScanConfig())
        # No crash; the issue is a clean candidate (no blockers, no labels).
        self.assertEqual(result["candidates"][0]["issue"], 1)

    def test_candidate_title_always_a_string(self):
        # A non-string title (e.g. null from a malformed bundle) must be
        # coerced to "" so the candidate JSON schema holds, never crash.
        issue = {"number": 1, "title": None, "body": "body text here"}
        result = wds.scan([issue], set(), [], wds.ScanConfig())
        cand = result["candidates"][0]
        self.assertEqual(cand["title"], "")
        self.assertIsInstance(cand["summary"], str)


class TestBundleValidation(unittest.TestCase):
    """--from-file shape validation surfaces a clear error (exit 2)."""

    def _run_bundle_text(self, text):
        fd, name = tempfile.mkstemp(suffix=".json", prefix="wds_bad_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--from-file", name],
                capture_output=True,
                text=True,
            )
        finally:
            os.unlink(name)

    def test_bundle_not_object_errors(self):
        proc = self._run_bundle_text("[1, 2, 3]")
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("JSON object", data["error"])

    def test_bundle_issues_not_list_errors(self):
        proc = self._run_bundle_text('{"issues": {"not": "a list"}}')
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("issues", data["error"])

    def test_bundle_bad_open_pr_numbers_errors(self):
        proc = self._run_bundle_text(
            '{"issues": [], "open_pr_numbers": ["not-an-int"]}'
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("open_pr_numbers", data["error"])

    def test_bundle_open_pr_numbers_string_errors(self):
        # A string `"123"` must NOT be iterated into {1,2,3}; require a list.
        proc = self._run_bundle_text('{"issues": [], "open_pr_numbers": "123"}')
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("open_pr_numbers", data["error"])

    def test_bundle_issue_without_integer_number_errors(self):
        # An issue lacking an int `number` would emit `"issue": null` —
        # reject it so the candidate JSON schema stays consistent.
        proc = self._run_bundle_text('{"issues": [{"title": "no number"}]}')
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("number", data["error"])

    def test_bundle_falsey_non_list_field_errors(self):
        # A *present* falsey non-list (`{}`, `""`, `false`) is malformed and
        # must error — it must NOT be coalesced to [] (which would read as
        # no_candidates / exit 0, hiding the malformed input).
        for bad in ('{"issues": {}}', '{"issues": ""}', '{"issues": false}'):
            with self.subTest(bundle=bad):
                proc = self._run_bundle_text(bad)
                self.assertEqual(proc.returncode, wds.EXIT_ERROR)
                data = json.loads(proc.stdout)
                self.assertEqual(data["status"], "error")
                self.assertIn("issues", data["error"])

    def test_bundle_absent_or_null_fields_default_empty(self):
        # Absent / explicit null fields legitimately default to empty (and
        # yield a clean no_candidates / exit 0), unlike a present wrong type.
        proc = self._run_bundle_text('{"issues": null}')
        self.assertEqual(proc.returncode, wds.EXIT_NO_CANDIDATES)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "no_candidates")


if __name__ == "__main__":
    unittest.main()
