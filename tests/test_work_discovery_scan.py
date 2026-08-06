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
import shutil
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


class TestEffortLearning(unittest.TestCase):
    """Learned effort model (design §10): learn realized-effort scale from
    merged PRs, override the static heuristic ONLY when the issue-body
    predictor actually correlates with realized effort (data-driven gate),
    else retain the static estimate and disclose why + the realized context."""

    @staticmethod
    def _sample(body_len, lines, *, files=1, reviews=0, hours=0.1, criteria=0):
        return {
            "body_len": body_len,
            "criteria": criteria,
            "changed_lines": lines,
            "changed_files": files,
            "review_rounds": reviews,
            "hours_to_merge": hours,
        }

    def _correlated(self, n=9):
        # body_len rises with changed_lines → Spearman ≈ 1.0 (gate fires).
        return [self._sample(100 * i, 30 * i) for i in range(1, n + 1)]

    def _uncorrelated(self):
        # body_len and changed_lines unrelated → Spearman ≈ 0 (gate declines).
        # The repo's real shape: long specs, small diffs and vice versa.
        bodies = [100, 200, 300, 400, 500, 600, 700, 800, 900]
        lines = [900, 100, 700, 200, 500, 300, 800, 50, 400]
        return [self._sample(b, l) for b, l in zip(bodies, lines)]

    # --- helpers ------------------------------------------------------
    def test_spearman_perfect_positive(self):
        self.assertAlmostEqual(wds._spearman([1, 2, 3], [10, 20, 30]), 1.0)

    def test_spearman_perfect_negative(self):
        self.assertAlmostEqual(wds._spearman([1, 2, 3], [30, 20, 10]), -1.0)

    def test_spearman_zero_variance_is_zero(self):
        self.assertEqual(wds._spearman([5, 5, 5], [1, 2, 3]), 0.0)

    def test_spearman_degenerate_lengths(self):
        self.assertEqual(wds._spearman([1], [1]), 0.0)
        self.assertEqual(wds._spearman([1, 2], [1]), 0.0)

    def test_spearman_handles_ties(self):
        # Tie-aware average ranks: monotone-with-ties stays strongly positive.
        self.assertGreater(wds._spearman([1, 1, 2, 3], [1, 1, 2, 3]), 0.9)

    def test_tertile_cutpoints_none_below_three(self):
        self.assertIsNone(wds._tertile_cutpoints([1, 2]))

    def test_tertile_cutpoints_two_values(self):
        t = wds._tertile_cutpoints([0, 30, 60, 90])
        self.assertEqual(len(t), 2)
        self.assertLess(t[0], t[1])

    # --- learn_effort_model ------------------------------------------
    def test_empty_samples_not_applied(self):
        m = wds.learn_effort_model([])
        self.assertEqual(m["sample_size"], 0)
        self.assertFalse(m["applies"])
        self.assertIn("no linked", m["reason"])

    def test_correlated_above_gate_applies(self):
        m = wds.learn_effort_model(self._correlated(9))
        self.assertTrue(m["applies"])
        self.assertEqual(m["sample_size"], 9)
        self.assertIsNotNone(m["predictor_cutpoints"])
        self.assertGreaterEqual(m["predictor_correlation"], 0.3)
        self.assertIn("tracks realized effort", m["reason"])

    def test_uncorrelated_declines_even_with_samples(self):
        m = wds.learn_effort_model(self._uncorrelated())
        self.assertGreaterEqual(m["sample_size"], wds.MIN_EFFORT_SAMPLES)
        self.assertFalse(m["applies"])  # the crux: N is fine, signal is not
        self.assertIn("does not predict", m["reason"])

    def test_insufficient_samples_declines(self):
        # Strongly correlated but too few pairs → gate declines on N.
        m = wds.learn_effort_model(self._correlated(4))
        self.assertFalse(m["applies"])
        self.assertIn("insufficient", m["reason"])

    def test_realized_context_always_reported(self):
        # Even when not applied, the realized-effort context is present so a
        # human sees the empirical basis (anti-cognitive-surrender).
        m = wds.learn_effort_model(self._uncorrelated())
        self.assertIsNotNone(m["realized_median_lines"])
        self.assertIsNotNone(m["realized_cutpoints"])
        self.assertTrue(m["realized_metric"].startswith("changed_lines"))

    def test_degenerate_review_rounds_noted(self):
        m = wds.learn_effort_model(self._correlated(9))  # all reviews=0
        self.assertTrue(
            any("review_rounds" in d for d in m["degenerate_signals"])
        )

    def test_review_rounds_present_not_flagged_degenerate(self):
        samples = [
            self._sample(100 * i, 30 * i, reviews=(i % 2)) for i in range(1, 10)
        ]
        m = wds.learn_effort_model(samples)
        self.assertFalse(
            any("review_rounds" in d for d in m["degenerate_signals"])
        )

    def test_model_order_independent(self):
        # Determinism: learning is invariant to sample order (§4 再現性).
        s = self._correlated(9)
        m1 = wds.learn_effort_model(s)
        m2 = wds.learn_effort_model(list(reversed(s)))
        self.assertEqual(
            json.dumps(m1, sort_keys=True), json.dumps(m2, sort_keys=True)
        )

    # --- estimate_effort with a model --------------------------------
    def test_applied_model_uses_learned_cutpoints(self):
        m = wds.learn_effort_model(self._correlated(9))
        t1, t2 = m["predictor_cutpoints"]
        small = wds.estimate_effort(_issue(1, body="x" * int(t1 - 1)), m)
        large = wds.estimate_effort(_issue(1, body="x" * int(t2 + 50)), m)
        self.assertEqual(small[0], "S")
        self.assertEqual(large[0], "L")
        self.assertTrue(small[1])  # still estimated=True
        self.assertTrue(any("learned effort" in s for s in small[2]))

    def test_label_authoritative_even_with_model(self):
        # An explicit size label still wins over the learned model.
        m = wds.learn_effort_model(self._correlated(9))
        size, estimated, _ = wds.estimate_effort(_issue(1, labels=["size:L"]), m)
        self.assertEqual(size, "L")
        self.assertFalse(estimated)

    def test_declined_model_keeps_static_and_discloses(self):
        m = wds.learn_effort_model(self._uncorrelated())
        size, estimated, sig = wds.estimate_effort(_issue(1, body="short"), m)
        self.assertEqual(size, "S")  # unchanged static result
        self.assertTrue(estimated)
        self.assertTrue(any("estimated effort from body_len" in s for s in sig))
        self.assertTrue(any("effort model not applied" in s for s in sig))
        self.assertTrue(any("realized context" in s for s in sig))

    def test_no_model_is_pure_static(self):
        # model=None must reproduce the pre-learning behaviour exactly.
        a = wds.estimate_effort(_issue(1, body="short"))
        b = wds.estimate_effort(_issue(1, body="short"), None)
        self.assertEqual(a, b)

    def test_learning_signals_ascii_only(self):
        # Issue #546: the learning path's *generated* strings (reason /
        # degenerate_signals / effort signals) must be ASCII-only so the
        # stdout JSON (ensure_ascii=False for Japanese GitHub titles) never
        # crashes a cp932 console on typographic chars like ≤ / ρ / — / →.
        models = [
            wds.learn_effort_model([]),  # zero training pairs
            wds.learn_effort_model(self._correlated(9)),  # gate fires
            wds.learn_effort_model(self._correlated(4)),  # insufficient n
            wds.learn_effort_model(self._uncorrelated()),  # gate declines
        ]
        for m in models:
            for s in [m["reason"], *m["degenerate_signals"]]:
                s.encode("ascii")  # raises UnicodeEncodeError on regression
            for body in ("short", "x" * 5000):
                _, _, sig = wds.estimate_effort(_issue(1, body=body), m)
                for s in sig:
                    s.encode("ascii")

    # --- sample building ---------------------------------------------
    def test_build_samples_single_issue_join(self):
        prs = [
            {
                "closingIssuesReferences": [{"number": 5}],
                "additions": 10,
                "deletions": 5,
                "changedFiles": 2,
                "reviews": [],
                "createdAt": "2026-06-01T00:00:00Z",
                "mergedAt": "2026-06-01T02:00:00Z",
            }
        ]
        samples = wds._build_effort_samples(prs, {5: "issue body text"})
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["changed_lines"], 15)
        self.assertEqual(samples[0]["changed_files"], 2)
        self.assertEqual(samples[0]["hours_to_merge"], 2.0)
        self.assertEqual(samples[0]["body_len"], len("issue body text"))

    def test_build_samples_skips_multi_issue_pr(self):
        prs = [{"closingIssuesReferences": [{"number": 5}, {"number": 6}]}]
        self.assertEqual(wds._build_effort_samples(prs, {5: "b", 6: "b"}), [])

    def test_build_samples_skips_unlinked_and_empty_body(self):
        prs = [
            {"closingIssuesReferences": []},  # unlinked
            {"closingIssuesReferences": [{"number": 9}]},  # body not fetched
            {"closingIssuesReferences": [{"number": 7}]},  # empty body
        ]
        self.assertEqual(wds._build_effort_samples(prs, {7: ""}), [])

    # --- cross-repo closing join (Issue #545) -------------------------
    @staticmethod
    def _pr_closing(num, owner=None, name=None):
        ref = {"number": num}
        if owner is not None:
            ref["repository"] = {"name": name, "owner": {"login": owner}}
        return {
            "closingIssuesReferences": [ref],
            "additions": 10, "deletions": 5, "changedFiles": 2,
            "reviews": [], "createdAt": "2026-06-01T00:00:00Z",
            "mergedAt": "2026-06-01T01:00:00Z",
        }

    def test_build_samples_excludes_cross_repo_closing(self):
        # A PR in repo A closing an issue in repo B must NOT become one of
        # repo A's training pairs — even when the issue NUMBER collides with
        # a closed issue in repo A (the join keys by (repo, number)).
        prs = [self._pr_closing(5, "suisya-systems", "claude-org-runtime")]
        samples = wds._build_effort_samples(
            prs, {5: "home issue body"}, "suisya-systems/claude-org-ja"
        )
        self.assertEqual(samples, [])

    def test_build_samples_same_number_two_repos(self):
        # ja#5 and runtime#5: only the home-repo closing joins; the
        # cross-repo one with the colliding number is excluded.
        prs = [
            self._pr_closing(5, "suisya-systems", "claude-org-ja"),
            self._pr_closing(5, "suisya-systems", "claude-org-runtime"),
        ]
        samples = wds._build_effort_samples(
            prs, {5: "home issue body"}, "suisya-systems/claude-org-ja"
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["issue"], 5)
        self.assertEqual(samples[0]["body_len"], len("home issue body"))

    def test_build_samples_same_repo_closing_unchanged(self):
        # Normal same-repo closings (with the repository field gh always
        # returns) keep joining exactly as before; the repo match is
        # case-insensitive (GitHub owner/repo names are).
        prs = [self._pr_closing(5, "Owner", "Repo")]
        samples = wds._build_effort_samples(
            prs, {5: "issue body text"}, "owner/repo"
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["changed_lines"], 15)

    def test_build_samples_missing_repository_field_treated_as_home(self):
        # Back-compat: a ref without repository info (hand-built fixtures;
        # gh always provides it) is treated as home, not dropped.
        prs = [self._pr_closing(5)]
        samples = wds._build_effort_samples(prs, {5: "b"}, "owner/repo")
        self.assertEqual(len(samples), 1)

    def test_single_closing_issue_cross_repo_returns_none(self):
        pr = self._pr_closing(7, "other", "repo")
        self.assertIsNone(wds._single_closing_issue(pr, "owner/repo"))
        # without a home repo (pure-fixture mode) the number still extracts
        self.assertEqual(wds._single_closing_issue(pr), 7)

    def test_hours_between_basic_and_bad(self):
        self.assertEqual(
            wds._hours_between("2026-06-01T00:00:00Z", "2026-06-01T03:30:00Z"),
            3.5,
        )
        self.assertIsNone(wds._hours_between(None, "2026-06-01T03:30:00Z"))
        self.assertIsNone(wds._hours_between("garbage", "also-bad"))

    # --- scan integration --------------------------------------------
    def test_scan_echoes_effort_model(self):
        m = wds.learn_effort_model(self._uncorrelated())
        result = wds.scan([_issue(1, body="b")], set(), [], wds.ScanConfig(), None, m)
        self.assertEqual(result["effort_model"]["sample_size"], m["sample_size"])

    def test_scan_effort_model_none_by_default(self):
        result = wds.scan([_issue(1, body="b")], set(), [], wds.ScanConfig())
        self.assertIsNone(result["effort_model"])

    def test_scan_applied_model_changes_candidate_effort(self):
        m = wds.learn_effort_model(self._correlated(9))
        t2 = m["predictor_cutpoints"][1]
        big = _issue(1, body="x" * int(t2 + 100))
        result = wds.scan([big], set(), [], wds.ScanConfig(), None, m)
        self.assertEqual(result["candidates"][0]["effort"], "L")


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
                encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)  # stdout is valid JSON
        self.assertEqual(data["status"], "error")
        self.assertIn("argument error", data["error"])

    def test_argparse_type_error_keeps_trigger_context(self):
        # Even an argparse type error raised mid-parse must carry the real
        # --trigger in generated_for (best-effort probe), not "manual".
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--top-n", "nope", "--trigger", "post_merge"],
            capture_output=True,
            text=True,
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["generated_for"], "post_merge")

    def test_malformed_arg_keeps_stderr_clean(self):
        # The trigger-probe pre-parse must stay silent: a malformed CLI emits
        # the JSON envelope to stdout and nothing to stderr (single-channel
        # machine contract, §5.1).
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--trigger"],  # missing value
            capture_output=True,
            text=True,
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        self.assertEqual(proc.stderr, "")
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")

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
            data["input_truncated"],
            # `base_merges` joined the coverage flags with Issue #830 (the
            # base-branch completion window can cut off older merges).
            {"open_issues": False, "open_prs": False, "base_merges": False},
        )

    def test_top_n_zero_rejected_as_error(self):
        # `--top-n 0` would silently return an empty `top` (status
        # no_candidates / exit 0) even with candidates — a contract break.
        # It must be rejected as an argument error (exit 2, JSON envelope).
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--top-n", "0"],
            capture_output=True,
            text=True,
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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
            encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("--free-panes", data["error"])


class TestEffortLearningCliAndWiring(unittest.TestCase):
    """CLI/bundle wiring for effort learning + the NON-FATAL guarantee:
    a learning-fetch failure must degrade to the static heuristic, never
    abort the triage (design §10 / effort model is an enhancement input)."""

    def _run_bundle(self, bundle):
        fd, name = tempfile.mkstemp(suffix=".json", prefix="wds_effort_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(bundle, f)
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--from-file", name],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        finally:
            os.unlink(name)

    def _correlated_samples(self, n=9):
        return [
            {
                "body_len": 100 * i,
                "criteria": 0,
                "changed_lines": 30 * i,
                "changed_files": 1,
                "review_rounds": 0,
                "hours_to_merge": 0.1,
            }
            for i in range(1, n + 1)
        ]

    def test_effort_model_in_output_schema(self):
        bundle = {"issues": [_issue(1, body="b")], "open_pr_numbers": [], "recent_merges": []}
        proc = self._run_bundle(bundle)
        data = json.loads(proc.stdout)
        self.assertIn("effort_model", data)
        self.assertIsNone(data["effort_model"])  # no effort_samples → None

    def test_bundle_effort_samples_learned_and_applied(self):
        bundle = {
            "issues": [_issue(1, body="x" * 5000)],  # very long body
            "open_pr_numbers": [],
            "recent_merges": [],
            "effort_samples": self._correlated_samples(9),
        }
        proc = self._run_bundle(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_CANDIDATES_FOUND)
        data = json.loads(proc.stdout)
        self.assertTrue(data["effort_model"]["applies"])
        # body 5000 ≫ top cutpoint → learned 'L'; signal names the learned route
        cand = data["candidates"][0]
        self.assertEqual(cand["effort"], "L")
        self.assertTrue(any("learned effort" in s for s in cand["signals"]))

    def test_bundle_effort_samples_not_a_list_errors(self):
        bundle = {"issues": [], "effort_samples": {"not": "a list"}}
        proc = self._run_bundle(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertIn("effort_samples", data["error"])

    def test_bundle_effort_sample_missing_int_field_errors(self):
        bundle = {
            "issues": [],
            "effort_samples": [{"body_len": 10, "changed_files": 1}],  # no changed_lines
        }
        proc = self._run_bundle(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertIn("changed_lines", data["error"])

    def test_effort_history_negative_rejected(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--effort-history=-1"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        self.assertIn("--effort-history", json.loads(proc.stdout)["error"])

    def _run_main_capture(self, argv):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = wds.main(argv)
        return rc, buf.getvalue()

    def test_effort_history_fetch_failure_is_non_fatal(self):
        # The crux: a GhError on the effort-history *fetch* must still let the
        # triage succeed on the static heuristic, with the failure disclosed in
        # the echoed effort_model (NOT exit 2).
        with mock.patch.object(wds, "fetch_open_issues", return_value=[_issue(1, body="b")]), \
             mock.patch.object(wds, "fetch_open_pr_numbers", return_value=set()), \
             mock.patch.object(wds, "fetch_recent_merges", return_value=[]), \
             mock.patch.object(
                 wds, "build_effort_model", side_effect=wds.GhError("boom")
             ):
            rc, out = self._run_main_capture(["--effort-history", "60"])
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)  # NOT EXIT_ERROR
        data = json.loads(out)
        self.assertEqual(data["status"], "candidates_found")
        self.assertFalse(data["effort_model"]["applies"])
        self.assertIn("fetch failed", data["effort_model"]["reason"])
        # the failure stub is full-shape, not a partial dict (no KeyError risk)
        self.assertIn("predictor_correlation", data["effort_model"])
        self.assertIn(data["candidates"][0]["effort"], {"S", "M", "L"})

    def test_effort_learning_bug_is_not_swallowed(self):
        # Codex Blocker: a NON-GhError exception (a genuine bug / unexpected
        # schema in the pure learning code) must NOT be masked as "fetch
        # failed" / exit 0|10 — it must propagate to exit 2 (the §5.1
        # `error` contract), like any other unexpected failure.
        with mock.patch.object(wds, "fetch_open_issues", return_value=[_issue(1, body="b")]), \
             mock.patch.object(wds, "fetch_open_pr_numbers", return_value=set()), \
             mock.patch.object(wds, "fetch_recent_merges", return_value=[]), \
             mock.patch.object(
                 wds, "build_effort_model", side_effect=KeyError("body_len")
             ):
            rc, out = self._run_main_capture(["--effort-history", "60"])
        self.assertEqual(rc, wds.EXIT_ERROR)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_build_effort_model_surfaces_coverage(self):
        # build_effort_model must report learning-data coverage (no silent
        # drop): one single-issue PR yields a sample, one is dropped (no body).
        prs = [
            {
                "closingIssuesReferences": [{"number": 5}],
                "additions": 10, "deletions": 5, "changedFiles": 2,
                "reviews": [], "createdAt": "2026-06-01T00:00:00Z",
                "mergedAt": "2026-06-01T01:00:00Z",
            },
            {  # single-issue linked but its body is absent from the fetch
                "closingIssuesReferences": [{"number": 99}],
                "additions": 1, "deletions": 1, "changedFiles": 1,
                "reviews": [], "createdAt": "2026-06-01T00:00:00Z",
                "mergedAt": "2026-06-01T01:00:00Z",
            },
        ]
        with mock.patch.object(wds, "fetch_effort_history", return_value=prs), \
             mock.patch.object(
                 wds, "fetch_closed_issue_bodies", return_value={5: "issue body"}
             ), \
             mock.patch.object(
                 wds, "_resolve_home_repo", return_value="owner/repo"
             ):
            model = wds.build_effort_model(None, 60)
        self.assertEqual(model["coverage"]["single_issue_linked_prs"], 2)
        self.assertEqual(model["coverage"]["usable_samples"], 1)
        self.assertEqual(model["coverage"]["dropped_missing_body"], 1)
        self.assertEqual(model["coverage"]["dropped_cross_repo"], 0)

    def test_build_effort_model_excludes_cross_repo_closings(self):
        # Issue #545: a PR closing an issue in ANOTHER repo must not feed
        # this repo's training samples, and the drop must be disclosed in
        # coverage (not silent, not misattributed).
        def _ref(num, owner, name):
            return {
                "number": num,
                "repository": {"name": name, "owner": {"login": owner}},
            }

        prs = [
            {  # home-repo closing: stays a sample
                "closingIssuesReferences": [_ref(5, "owner", "repo")],
                "additions": 10, "deletions": 5, "changedFiles": 2,
                "reviews": [], "createdAt": "2026-06-01T00:00:00Z",
                "mergedAt": "2026-06-01T01:00:00Z",
            },
            {  # cross-repo closing (same number as a home closed issue!)
                "closingIssuesReferences": [_ref(5, "owner", "other-repo")],
                "additions": 900, "deletions": 900, "changedFiles": 40,
                "reviews": [], "createdAt": "2026-06-01T00:00:00Z",
                "mergedAt": "2026-06-01T01:00:00Z",
            },
        ]
        with mock.patch.object(wds, "fetch_effort_history", return_value=prs), \
             mock.patch.object(
                 wds, "fetch_closed_issue_bodies", return_value={5: "issue body"}
             ), \
             mock.patch.object(
                 wds, "_resolve_home_repo", return_value="owner/repo"
             ):
            model = wds.build_effort_model("owner/repo", 60)
        self.assertEqual(model["coverage"]["single_issue_linked_prs"], 1)
        self.assertEqual(model["coverage"]["usable_samples"], 1)
        self.assertEqual(model["coverage"]["dropped_missing_body"], 0)
        self.assertEqual(model["coverage"]["dropped_cross_repo"], 1)

    def test_resolve_home_repo_passthrough_and_gh_default(self):
        # Explicit --repo: no gh call. None: one read-only `gh repo view`.
        with mock.patch.object(wds, "_run_gh_json") as gh:
            self.assertEqual(
                wds._resolve_home_repo("owner/repo"), "owner/repo"
            )
        gh.assert_not_called()
        with mock.patch.object(
            wds, "_run_gh_json", return_value={"nameWithOwner": "o/r"}
        ) as gh:
            self.assertEqual(wds._resolve_home_repo(None), "o/r")
        self.assertEqual(gh.call_args[0][0][:2], ["repo", "view"])

    def test_resolve_home_repo_normalizes_host_qualified_forms(self):
        # gh --repo also accepts HOST/OWNER/REPO and full URLs; the join key
        # must still be bare owner/repo (what closingIssuesReferences
        # carries), or same-repo closings would misclassify as cross-repo.
        for given in (
            "github.com/owner/repo",
            "https://github.com/owner/repo",
            "https://github.com/owner/repo/",
        ):
            self.assertEqual(wds._resolve_home_repo(given), "owner/repo")

    def test_resolve_home_repo_bad_payload_raises(self):
        # An unusable `gh repo view` payload must raise GhError (degrades
        # learning to the static heuristic non-fatally in main), never
        # silently fall back to a number-only join.
        for payload in ({}, {"nameWithOwner": ""}, ["x"], None):
            with mock.patch.object(wds, "_run_gh_json", return_value=payload):
                with self.assertRaises(wds.GhError):
                    wds._resolve_home_repo(None)

    def test_effort_history_zero_disables_learning(self):
        with mock.patch.object(wds, "fetch_open_issues", return_value=[_issue(1, body="b")]), \
             mock.patch.object(wds, "fetch_open_pr_numbers", return_value=set()), \
             mock.patch.object(wds, "fetch_recent_merges", return_value=[]), \
             mock.patch.object(wds, "build_effort_model") as bem:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = wds.main(["--effort-history", "0"])
        bem.assert_not_called()  # learning skipped entirely
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)
        self.assertIsNone(json.loads(buf.getvalue())["effort_model"])


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


class TestFetchEffortHistory(unittest.TestCase):
    """fetch_effort_history mirrors fetch_recent_merges' two-layer ordering:
    over-fetch in recency-biased order, then take the exact mergedAt top-K
    (so a freshly-commented old merge can't displace a recent one)."""

    def test_overfetch_and_recency_ordering_requested(self):
        with mock.patch.object(wds, "_run_gh_json", return_value=[]) as gh:
            wds.fetch_effort_history("owner/repo", 7)
        argv = gh.call_args[0][0]
        self.assertIn("merged", argv)
        self.assertEqual(argv[argv.index("--search") + 1], "sort:updated-desc")
        self.assertEqual(
            argv[argv.index("--limit") + 1], str(7 * wds._RECENT_MERGE_OVERFETCH)
        )
        # realized-effort fields + the PR↔issue bridge are requested
        json_fields = argv[argv.index("--json") + 1]
        for f in ("additions", "deletions", "changedFiles", "closingIssuesReferences"):
            self.assertIn(f, json_fields)

    def test_trimmed_to_merged_at_top_k(self):
        pool = [
            {"number": n, "mergedAt": f"2026-{n:02d}-01T00:00:00Z"}
            for n in range(1, 10)
        ]
        with mock.patch.object(wds, "_run_gh_json", return_value=pool):
            merges = wds.fetch_effort_history(None, 3)
        self.assertEqual([m["number"] for m in merges], [9, 8, 7])

    def test_closed_issue_fetch_recency_ordered(self):
        # The closed-issue body fetch must be recency-ordered so recently-closed
        # (incl. long-lived) issues are not silently dropped (Codex Major).
        with mock.patch.object(wds, "_run_gh_json", return_value=[]) as gh:
            wds.fetch_closed_issue_bodies("owner/repo", 200)
        argv = gh.call_args[0][0]
        self.assertIn("closed", argv)
        self.assertEqual(argv[argv.index("--search") + 1], "sort:updated-desc")


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


class TestGhUtf8Decoding(unittest.TestCase):
    """#537 regression: gh stdout is decoded as UTF-8 in the caller's thread,
    so a cp932 locale can neither corrupt Japanese output nor swallow a decode
    error into a NoneType cascade."""

    def test_decode_japanese_stdout(self):
        # gh emits UTF-8; the bytes below are invalid under cp932 (the exact
        # failure mode of #537) but must decode cleanly here.
        raw = "ログイン機能 — 並列可".encode("utf-8")
        self.assertEqual(wds._decode_gh_stdout(raw, ["x"]), "ログイン機能 — 並列可")

    def test_invalid_utf8_raises_clear_gherror(self):
        # A genuinely non-UTF-8 byte must surface as a GhError naming the byte
        # and position — NOT be swallowed into proc.stdout=None / a NoneType
        # 'JSON object must be str/bytes' cascade (the #537 symptom).
        with self.assertRaises(wds.GhError) as ctx:
            wds._decode_gh_stdout(b"\x96\x96", ["issue", "list"])
        msg = str(ctx.exception)
        self.assertIn("not valid UTF-8", msg)
        self.assertIn("0x96", msg)
        self.assertNotIn("NoneType", msg)

    def test_run_gh_json_parses_japanese_via_bytes(self):
        # End-to-end: subprocess returns *bytes* stdout (as it does without
        # text=True); _run_gh_json must decode + parse it regardless of locale.
        payload = json.dumps([{"number": 1, "title": "日本語"}]).encode("utf-8")
        completed = subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout=payload, stderr=b""
        )
        with mock.patch.object(wds.shutil, "which", return_value="/usr/bin/gh"):
            with mock.patch.object(wds.subprocess, "run", return_value=completed):
                data = wds._run_gh_json(["issue", "list"])
        self.assertEqual(data, [{"number": 1, "title": "日本語"}])


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

    def test_is_int_excludes_bool(self):
        self.assertTrue(wds._is_int(5))
        self.assertFalse(wds._is_int(True))
        self.assertFalse(wds._is_int(False))
        self.assertFalse(wds._is_int("5"))
        self.assertFalse(wds._is_int(None))

    def test_bool_pr_number_not_treated_as_issue_one(self):
        # `number: true` must NOT be read as PR #1 (bool is an int subclass):
        # an issue depending on #1 must stay blocked-agnostic, not "unblocked".
        issues = [{"number": 10, "title": "t", "body": "Depends on #1"}]
        merges = [{"number": True, "title": "x", "body": "no refs"}]
        result = wds.scan(issues, set(), merges, wds.ScanConfig())
        self.assertFalse(result["candidates"][0]["unblocked_by_recent_merge"])

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
                encoding="utf-8",  # script emits UTF-8; don't decode via cp932 locale (#537)
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


# ----------------------------------------------------------------------
# Cross-repo triage (Issue #528, design §10)
# ----------------------------------------------------------------------

JA = "suisya-systems/claude-org-ja"
RT = "suisya-systems/claude-org-runtime"


class TestCrossRepoExtraction(unittest.TestCase):
    """The cross-repo blocking extractor is keyword-gated and leading-run
    anchored exactly like the home one (§11-3): real cross-repo refs live in
    non-blocking notations (Epic:/Refs:/Found by) and must NOT be excluded."""

    def test_owner_repo_blocked_by(self):
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                f"Blocked by {RT}#60", is_epic=False
            ),
            [(RT, 60)],
        )

    def test_github_url_blocked_by(self):
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                f"Depends on https://github.com/{RT}/issues/42", is_epic=False
            ),
            [(RT, 42)],
        )

    def test_github_pull_url(self):
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                f"Requires https://github.com/{JA}/pull/7", is_epic=False
            ),
            [(JA, 7)],
        )

    def test_mixed_home_and_cross_run(self):
        # A run mixing home `#1` and cross `RT#2`: the cross extractor pulls
        # only the cross ref; the home one is left to extract_blocking_refs.
        text = f"Blocked by #1, {RT}#2"
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(text, is_epic=False), [(RT, 2)]
        )
        self.assertEqual(wds.extract_blocking_refs(text, is_epic=False), [1])

    def test_epic_notation_not_a_blocker(self):
        # §11-3 cross-repo: `Epic: owner/repo#6` is tracking, not a blocker.
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                "Epic: suisya-systems/claude-org-transport-lab#6", is_epic=False
            ),
            [],
        )

    def test_refs_and_found_by_not_blockers(self):
        body = (
            f"Refs {JA}#467, suisya-systems/claude-org#264\n"
            f"Found by Codex during {JA}#467\n"
            f"Design source: {JA}#443\n"
        )
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(body, is_epic=False), []
        )

    def test_negated_cross_ref_not_a_blocker(self):
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                f"no longer blocked by {RT}#60", is_epic=False
            ),
            [],
        )

    def test_prose_before_cross_ref_not_extracted(self):
        # Leading-run anchored: prose before the ref → not the immediate run.
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                f"Blocked by the runtime work, see {RT}#60", is_epic=False
            ),
            [],
        )

    def test_bare_repo_shorthand_not_resolved(self):
        # `ja#467` (no owner) is ambiguous — deliberately not a cross ref.
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                "Blocked by ja#467", is_epic=False
            ),
            [],
        )

    def test_cross_task_list_pure_ref(self):
        body = f"- [ ] {RT}#11\n- [ ] {RT}#12 を参考に\n"
        # Only the pure-ref item counts; the prose-annotated one is dropped.
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(body, is_epic=False), [(RT, 11)]
        )

    def test_cross_task_list_ignored_for_epic(self):
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(
                f"- [ ] {RT}#11", is_epic=True
            ),
            [],
        )


class TestQualifiedRefHelpers(unittest.TestCase):
    def test_ref_to_json_home_is_bare_int(self):
        self.assertEqual(wds._ref_to_json((None, 5)), 5)

    def test_ref_to_json_cross_is_string(self):
        self.assertEqual(wds._ref_to_json((RT, 5)), f"{RT}#5")

    def test_issue_blocking_refs_q_unifies_self_ref(self):
        # A cross ref naming the issue's own repo dedups with home keying.
        issue = {"number": 1, "title": "t", "body": f"Blocked by #2 and {JA}#2"}
        self.assertEqual(
            wds.issue_blocking_refs_q(issue, JA), [(JA, 2)]
        )


class TestCrossRepoScan(unittest.TestCase):
    """End-to-end cross-repo behaviour through ``scan_repos``."""

    def _bundle(self, repo, issues, prs=None, merges=None):
        return {
            "repo": repo,
            "issues": issues,
            "open_pr_numbers": prs or [],
            "recent_merges": merges or [],
        }

    def test_blocked_by_open_cross_repo_issue_excluded(self):
        ja = self._bundle(
            JA, [_issue(60, body=f"Blocked by {RT}#60")]
        )
        rt = self._bundle(RT, [_issue(60, body="open runtime work")])
        result = wds.scan_repos([ja, rt], wds.ScanConfig())
        cand_keys = {(c["repo"], c["issue"]) for c in result["candidates"]}
        self.assertNotIn((JA, 60), cand_keys)  # ja#60 excluded
        self.assertIn((RT, 60), cand_keys)  # runtime#60 is itself a candidate
        excluded = {(e["repo"], e["issue"]): e for e in result["excluded_blocked"]}
        self.assertIn((JA, 60), excluded)
        self.assertEqual(excluded[(JA, 60)]["blocking_refs"], [f"{RT}#60"])

    def test_blocked_by_closed_cross_repo_issue_resolved(self):
        # runtime#60 is NOT open (not in runtime's issue list) → resolved.
        ja = self._bundle(JA, [_issue(60, body=f"Blocked by {RT}#60")])
        rt = self._bundle(RT, [])
        result = wds.scan_repos([ja, rt], wds.ScanConfig())
        cand_keys = {(c["repo"], c["issue"]) for c in result["candidates"]}
        self.assertIn((JA, 60), cand_keys)

    def test_issue_number_collision_across_repos(self):
        # ja#60 and runtime#60 are distinct candidates — no collision.
        ja = self._bundle(JA, [_issue(60, body="ja work")])
        rt = self._bundle(RT, [_issue(60, body="runtime work")])
        result = wds.scan_repos([ja, rt], wds.ScanConfig())
        keys = sorted((c["repo"], c["issue"]) for c in result["candidates"])
        self.assertEqual(keys, [(JA, 60), (RT, 60)])

    def test_candidates_carry_repo_field(self):
        result = wds.scan_repos(
            [self._bundle(JA, [_issue(1, body="b")])], wds.ScanConfig()
        )
        self.assertEqual(result["candidates"][0]["repo"], JA)
        self.assertEqual(result["recommendation"]["repo"], JA)

    def test_unblocked_by_recent_cross_repo_merge(self):
        # ja#72 Depends on runtime#80; a runtime merge Closes #80 → unblocked.
        ja = self._bundle(JA, [_issue(72, body=f"Depends on {RT}#80")])
        rt = self._bundle(
            RT, [], merges=[{"number": 900, "title": "x", "body": "Closes #80"}]
        )
        result = wds.scan_repos([ja, rt], wds.ScanConfig())
        cand = next(c for c in result["candidates"] if c["issue"] == 72)
        self.assertTrue(cand["unblocked_by_recent_merge"])
        self.assertTrue(
            any(f"{RT}#80" in s for s in cand["signals"])
        )

    def test_recent_merge_qualified_by_repo(self):
        # A *ja* merge `Closes #80` must NOT unblock a dep on runtime#80.
        ja = self._bundle(
            JA,
            [_issue(72, body=f"Depends on {RT}#80")],
            merges=[{"number": 900, "title": "x", "body": "Closes #80"}],
        )
        rt = self._bundle(RT, [])
        result = wds.scan_repos([ja, rt], wds.ScanConfig())
        cand = next(c for c in result["candidates"] if c["issue"] == 72)
        self.assertFalse(cand["unblocked_by_recent_merge"])

    def test_unscanned_repo_ref_resolved_with_signal(self):
        # Blocked by a repo not in the scan set → resolved (誤除外<誤包含) but
        # the silent resolution is surfaced as an auditable signal (§10).
        ja = self._bundle(JA, [_issue(71, body="Blocked by Shin-sibainu/ccmux#4")])
        result = wds.scan_repos([ja], wds.ScanConfig())
        cand = result["candidates"][0]
        self.assertTrue(
            any("un-scanned repo" in s for s in cand["signals"])
        )

    def test_cross_repo_merge_closes_other_repo_issue(self):
        # A runtime PR that explicitly Closes ja#5 unblocks a dep on ja#5.
        ja = self._bundle(JA, [_issue(5, body="dep target")])
        ja2 = self._bundle(JA, [_issue(6, body=f"Depends on {JA}#5")])
        # Note: #5 open → #6 would be blocked. Use a separate scenario where #5
        # is closed but a recent cross-repo merge referenced it.
        rt = self._bundle(
            RT, [], merges=[{"number": 901, "title": "x", "body": f"Closes {JA}#5"}]
        )
        # #5 is open in ja, so #6 is blocked regardless; assert the cross-close
        # was parsed by checking an issue depending on a *closed* ja ref.
        ja3 = self._bundle(JA, [_issue(7, body=f"Depends on {JA}#999")])
        rt2 = self._bundle(
            RT, [], merges=[{"number": 902, "title": "x", "body": f"Closes {JA}#999"}]
        )
        result = wds.scan_repos([ja3, rt2], wds.ScanConfig())
        cand = next(c for c in result["candidates"] if c["issue"] == 7)
        self.assertTrue(cand["unblocked_by_recent_merge"])

    def test_deterministic_order_on_collision(self):
        # Full tie across axes: order is (repo, issue) ascending, stable.
        ja = self._bundle(JA, [_issue(60, body="b")])
        rt = self._bundle(RT, [_issue(60, body="b")])
        r1 = wds.scan_repos([ja, rt], wds.ScanConfig())
        r2 = wds.scan_repos([rt, ja], wds.ScanConfig())  # reversed input order
        self.assertEqual(
            [(c["repo"], c["issue"]) for c in r1["candidates"]],
            [(c["repo"], c["issue"]) for c in r2["candidates"]],
        )


class TestCrossRepoCli(unittest.TestCase):
    """`--from-file` multi-repo shape + `--repo` repeatable wiring."""

    def _run_text(self, text):
        fd, name = tempfile.mkstemp(suffix=".json", prefix="wds_xrepo_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--from-file", name],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        finally:
            os.unlink(name)

    def test_multi_repo_bundle_end_to_end(self):
        bundle = json.dumps(
            {
                "repos": [
                    {
                        "repo": JA,
                        "issues": [
                            {"number": 60, "title": "t", "body": f"Blocked by {RT}#60"},
                            {"number": 61, "title": "free", "body": "indep"},
                        ],
                        "open_pr_numbers": [],
                        "recent_merges": [],
                    },
                    {
                        "repo": RT,
                        "issues": [{"number": 60, "title": "rt", "body": "w"}],
                        "open_pr_numbers": [],
                        "recent_merges": [],
                    },
                ]
            }
        )
        proc = self._run_text(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_CANDIDATES_FOUND)
        data = json.loads(proc.stdout)
        excluded = {(e["repo"], e["issue"]) for e in data["excluded_blocked"]}
        self.assertIn((JA, 60), excluded)
        cand_keys = {(c["repo"], c["issue"]) for c in data["candidates"]}
        self.assertEqual(cand_keys, {(JA, 61), (RT, 60)})

    def test_multi_repo_bad_repo_type_errors(self):
        proc = self._run_text('{"repos": [{"repo": 5, "issues": []}]}')
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "error")
        self.assertIn("repos[0].repo", data["error"])

    def test_multi_repo_repos_not_list_errors(self):
        proc = self._run_text('{"repos": {"not": "a list"}}')
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertIn("repos", data["error"])

    def test_multi_repo_bad_issue_number_errors(self):
        proc = self._run_text(
            '{"repos": [{"repo": "o/r", "issues": [{"title": "no num"}]}]}'
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertIn("repos[0].issues[0]", data["error"])

    def test_effort_samples_not_applied_in_multi_repo_offline(self):
        # Effort learning is repo-calibrated: a top-level effort_samples must
        # NOT be applied across a genuine multi-repo (2+) offline scan (uniform
        # with the gh-path single-repo guard) — effort_model is null, visible.
        samples = [
            {"body_len": 100 * i, "changed_lines": 50 * i, "changed_files": i,
             "criteria": 0, "review_rounds": 0, "hours_to_merge": None}
            for i in range(1, 12)
        ]
        bundle = json.dumps({
            "effort_samples": samples,
            "repos": [
                {"repo": JA, "issues": [{"number": 1, "title": "a", "body": "b"}],
                 "open_pr_numbers": [], "recent_merges": []},
                {"repo": RT, "issues": [{"number": 1, "title": "c", "body": "d"}],
                 "open_pr_numbers": [], "recent_merges": []},
            ],
        })
        proc = self._run_text(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_CANDIDATES_FOUND)
        self.assertIsNone(json.loads(proc.stdout)["effort_model"])  # not applied

    def test_effort_samples_applied_in_single_repo_offline(self):
        # The single-repo offline shape keeps the learned model (back-compat
        # with #529's offline effort path).
        samples = [
            {"body_len": 100 * i, "changed_lines": 50 * i, "changed_files": i,
             "criteria": 0, "review_rounds": 0, "hours_to_merge": None}
            for i in range(1, 12)
        ]
        bundle = json.dumps({
            "effort_samples": samples,
            "issues": [{"number": 1, "title": "a", "body": "b"}],
            "open_pr_numbers": [],
            "recent_merges": [],
        })
        proc = self._run_text(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_CANDIDATES_FOUND)
        self.assertIsNotNone(json.loads(proc.stdout)["effort_model"])  # applied

    def test_malformed_effort_samples_errors_even_in_multi_repo(self):
        # The samples are validated regardless of repo shape — a malformed one
        # errors (exit 2) even when it would not be applied.
        proc = self._run_text(
            '{"effort_samples": [{"body_len": "x"}], "repos": ['
            '{"repo": "o/r", "issues": [], "open_pr_numbers": [], '
            '"recent_merges": []}]}'
        )
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        self.assertIn("effort_samples", json.loads(proc.stdout)["error"])

    def test_repo_arg_is_repeatable(self):
        # `--repo` uses action=append; two of them must parse to a 2-list and
        # reach the (mocked) fetchers once each, then scan_repos over both.
        calls = []

        def _fake_issues(repo, limit=wds.DEFAULT_OPEN_LIMIT):
            calls.append(repo)
            return []

        with mock.patch.object(wds, "fetch_open_issues", _fake_issues), \
             mock.patch.object(wds, "fetch_open_pr_numbers", lambda r, limit=wds.DEFAULT_OPEN_LIMIT: set()), \
             mock.patch.object(wds, "fetch_recent_merges", lambda r, k: []):
            rc = wds.main(["--repo", JA, "--repo", RT])
        self.assertEqual(calls, [JA, RT])
        self.assertEqual(rc, wds.EXIT_NO_CANDIDATES)


class TestCrossRepoOrderIndependence(unittest.TestCase):
    """Regression: a leading cross ref must not hide a trailing home `#N`
    (and vice versa). One shared run isolator feeds both extractors, so order
    inside a mixed run is irrelevant."""

    def test_home_ref_after_cross_ref_blocking(self):
        # cross-first: `Blocked by RT#2, #1` — home `#1` must still be found.
        text = f"Blocked by {RT}#2, #1"
        self.assertEqual(wds.extract_blocking_refs(text, is_epic=False), [1])
        self.assertEqual(
            wds.extract_cross_repo_blocking_refs(text, is_epic=False), [(RT, 2)]
        )

    def test_home_ref_after_cross_ref_in_scan_excludes(self):
        # ja#9 `Blocked by RT#2, #1` with home #1 open → must be excluded.
        ja = {
            "repo": JA,
            "issues": [
                _issue(9, body=f"Blocked by {RT}#2, #1"),
                _issue(1, body="open home dep"),
            ],
            "open_pr_numbers": [],
            "recent_merges": [],
        }
        result = wds.scan_repos([ja], wds.ScanConfig())
        excluded = {e["issue"] for e in result["excluded_blocked"]}
        self.assertIn(9, excluded)

    def test_recent_merge_home_close_after_cross_close(self):
        # cross-first close: `Closes RT#81, #80` must still capture home #80.
        self.assertEqual(wds._pr_close_refs(f"Closes {RT}#81, #80"), {80})

    def test_recent_merge_home_close_after_cross_in_scan(self):
        # A home merge `Closes RT#81, #80` unblocks a dep on home #80.
        bundle = {
            "repo": JA,
            "issues": [_issue(7, body="Depends on #80")],
            "open_pr_numbers": [],
            "recent_merges": [
                {"number": 900, "title": "x", "body": f"Closes {RT}#81, #80"}
            ],
        }
        result = wds.scan_repos([bundle], wds.ScanConfig())
        cand = next(c for c in result["candidates"] if c["issue"] == 7)
        self.assertTrue(cand["unblocked_by_recent_merge"])


class TestSingleRepoBackCompat(unittest.TestCase):
    """A single-repo scan keys candidates as the home repo (`repo: null`,
    home blocking_refs as bare ints) even when `--repo` names it explicitly —
    the original `scan()` contract (design §5.1 / §10)."""

    def _run_main_capture(self, argv, issue):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.object(
            wds, "fetch_open_issues", lambda r, limit=wds.DEFAULT_OPEN_LIMIT: [issue]
        ), mock.patch.object(
            wds, "fetch_open_pr_numbers", lambda r, limit=wds.DEFAULT_OPEN_LIMIT: set()
        ), mock.patch.object(
            wds, "fetch_recent_merges", lambda r, k: []
        ):
            with redirect_stdout(buf):
                rc = wds.main(argv)
        return rc, json.loads(buf.getvalue())

    def test_single_explicit_repo_keys_as_null(self):
        rc, data = self._run_main_capture(["--repo", JA], _issue(1, body="b"))
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)
        self.assertIsNone(data["candidates"][0]["repo"])
        self.assertIsNone(data["recommendation"]["repo"])

    def test_single_explicit_repo_home_blocking_refs_are_ints(self):
        # home blocker `#5` open → excluded with bare int [5], not "JA#5".
        issue = _issue(10, body="Blocked by #5")
        dep = _issue(5, body="open")
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.object(
            wds, "fetch_open_issues", lambda r, limit=wds.DEFAULT_OPEN_LIMIT: [issue, dep]
        ), mock.patch.object(
            wds, "fetch_open_pr_numbers", lambda r, limit=wds.DEFAULT_OPEN_LIMIT: set()
        ), mock.patch.object(
            wds, "fetch_recent_merges", lambda r, k: []
        ):
            with redirect_stdout(buf):
                wds.main(["--repo", JA])
        data = json.loads(buf.getvalue())
        excl = {e["issue"]: e for e in data["excluded_blocked"]}
        self.assertEqual(excl[10]["blocking_refs"], [5])

    def test_duplicate_repo_arg_fetched_once(self):
        # `--repo JA --repo JA` de-dups → JA fetched once, single-repo (null).
        calls = []
        with mock.patch.object(
            wds, "fetch_open_issues",
            lambda r, limit=wds.DEFAULT_OPEN_LIMIT: (calls.append(r) or [_issue(1, body="b")]),
        ), mock.patch.object(
            wds, "fetch_open_pr_numbers", lambda r, limit=wds.DEFAULT_OPEN_LIMIT: set()
        ), mock.patch.object(
            wds, "fetch_recent_merges", lambda r, k: []
        ):
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                wds.main(["--repo", JA, "--repo", JA])
            data = json.loads(buf.getvalue())
        self.assertEqual(calls, [JA])  # fetched once, not twice
        self.assertEqual(data["candidate_count"], 1)
        self.assertIsNone(data["candidates"][0]["repo"])


class TestSingleRepoSelfReferenceKeying(unittest.TestCase):
    """Round-2 regression: a single-repo scan keys by the *real* repo (so a
    self-reference by full name resolves) while still *displaying* as home
    (`repo: null`, int blocking_refs) via collapse_repo (design §10)."""

    def _bundle(self, repo, issues, merges=None):
        return {
            "repo": repo,
            "issues": issues,
            "open_pr_numbers": [],
            "recent_merges": merges or [],
        }

    def test_self_ref_full_name_excludes_when_open(self):
        # `Blocked by JA#5` in a JA scan, #5 open → blocked (keyed by real JA),
        # displayed as home: repo null, blocking_refs int, note `#5 ...`.
        b = self._bundle(
            JA,
            [_issue(10, body=f"Blocked by {JA}#5"), _issue(5, body="open")],
        )
        result = wds.scan_repos([b], wds.ScanConfig(), collapse_repo=JA)
        excl = {e["issue"]: e for e in result["excluded_blocked"]}
        self.assertIn(10, excl)
        self.assertIsNone(excl[10]["repo"])  # displayed as home
        self.assertEqual(excl[10]["blocking_refs"], [5])  # int, not "JA#5"
        self.assertNotIn(10, {c["issue"] for c in result["candidates"]})

    def test_self_ref_recent_merge_unblocks(self):
        # `Depends on JA#80` + a JA merge `Closes JA#80` → unblocked (keyed
        # real), displayed home.
        b = self._bundle(
            JA,
            [_issue(7, body=f"Depends on {JA}#80")],
            merges=[{"number": 900, "title": "x", "body": f"Closes {JA}#80"}],
        )
        result = wds.scan_repos([b], wds.ScanConfig(), collapse_repo=JA)
        cand = next(c for c in result["candidates"] if c["issue"] == 7)
        self.assertTrue(cand["unblocked_by_recent_merge"])
        self.assertIsNone(cand["repo"])  # displayed home

    def test_collapse_does_not_affect_other_repo_ref(self):
        # A cross ref to a *different* repo in a collapsed single scan still
        # renders as "owner/repo#N" and carries the un-scanned signal.
        b = self._bundle(JA, [_issue(71, body=f"Blocked by {RT}#9")])
        result = wds.scan_repos([b], wds.ScanConfig(), collapse_repo=JA)
        cand = result["candidates"][0]
        self.assertIsNone(cand["repo"])
        self.assertEqual(cand["blocking_refs"], [f"{RT}#9"])  # not collapsed
        self.assertTrue(any("un-scanned" in s for s in cand["signals"]))

    def test_multi_repo_not_collapsed(self):
        # collapse_repo is None for a genuine multi-repo scan → real repos kept.
        ja = self._bundle(JA, [_issue(1, body="b")])
        rt = self._bundle(RT, [_issue(1, body="b")])
        result = wds.scan_repos([ja, rt], wds.ScanConfig(), collapse_repo=None)
        repos = {c["repo"] for c in result["candidates"]}
        self.assertEqual(repos, {JA, RT})


class TestDuplicateRepoBundleDedup(unittest.TestCase):
    """A repo bundle appearing twice (offline `repos:[A, A]`) must not
    double-count candidates / excluded entries."""

    def test_duplicate_bundle_dedups_candidates(self):
        b = {
            "repo": JA,
            "issues": [_issue(1, body="b"), _issue(2, body="Blocked by #3")],
            "open_pr_numbers": [],
            "recent_merges": [],
        }
        dep = {"repo": JA, "issues": [_issue(3, body="open")], "open_pr_numbers": [], "recent_merges": []}
        result = wds.scan_repos([b, dict(b), dep], wds.ScanConfig())
        keys = [(c["repo"], c["issue"]) for c in result["candidates"]]
        self.assertEqual(keys.count((JA, 1)), 1)  # not duplicated
        self.assertEqual(result["candidate_count"], len(set(keys)))
        excl_keys = [(e["repo"], e["issue"]) for e in result["excluded_blocked"]]
        self.assertEqual(excl_keys.count((JA, 2)), 1)  # excluded not duplicated


class TestAllRegistryRepos(unittest.TestCase):
    """`--all-registry-repos` resolves the repo set in-process (Issue #829).

    The flag exists so no caller has to word-split a `--format flags`
    string: zsh (the org panes' login shell) leaves an unquoted expansion
    intact, so the old `scan $REPO_FLAGS` reached argparse as ONE argument
    and every worker-close scan died with exit 2. These lock the three
    properties that make the new path shell-proof and non-silent: the repos
    come from the registry, a resolution failure is fatal (never a
    fall-through to the implicit current-repo scan), and the resolver's
    audit lands in the output on both the success and the failure path."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "registry").mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _write_registry(self, rows):
        lines = [
            "# Projects Registry",
            "",
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |",
            "|---|---|---|---|---|---|",
        ]
        lines += [f"| {row} |" for row in rows]
        (self.root / "registry" / "projects.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _run(self, argv, issues_by_repo=None):
        """Run main() with the fetchers mocked; return (rc, json, fetched)."""
        fetched = []

        def _issues(repo, limit=wds.DEFAULT_OPEN_LIMIT):
            fetched.append(repo)
            return (issues_by_repo or {}).get(repo, [])

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.object(wds, "fetch_open_issues", _issues), \
             mock.patch.object(
                 wds, "fetch_open_pr_numbers",
                 lambda r, limit=wds.DEFAULT_OPEN_LIMIT: set()), \
             mock.patch.object(wds, "fetch_recent_merges", lambda r, k: []):
            with redirect_stdout(buf):
                rc = wds.main(argv)
        return rc, json.loads(buf.getvalue()), fetched

    def test_registry_repos_are_scanned(self):
        self._write_registry(
            [
                f"ja | ja | https://github.com/{JA} | d | x |",
                f"rt | rt | https://github.com/{RT} | d | x |",
            ]
        )
        rc, data, fetched = self._run(
            ["--all-registry-repos", "--claude-org-root", str(self.root)],
            {JA: [_issue(1, body="b")]},
        )
        self.assertEqual(fetched, [JA, RT])  # one fetch per resolved repo
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)
        self.assertEqual(data["repo_resolution"]["repos"], [JA, RT])
        # 2+ repos → a genuine cross-repo scan, so candidates keep real names.
        self.assertEqual(data["candidates"][0]["repo"], JA)

    def test_opted_out_row_is_not_scanned_but_is_audited(self):
        self._write_registry(
            [
                f"ja | ja | https://github.com/{JA} | d | x |",
                f"rt | rt | https://github.com/{RT} | d | x | no",
            ]
        )
        rc, data, fetched = self._run(
            ["--all-registry-repos", "--claude-org-root", str(self.root)]
        )
        self.assertEqual(fetched, [JA])
        self.assertEqual(
            [r["repo"] for r in data["repo_resolution"]["opted_out"]], [RT]
        )

    def test_resolution_failure_is_fatal_and_never_falls_back(self):
        # Empty registry → resolver error. The scan must NOT quietly degrade
        # to the implicit gh-current-repo scan (that is exactly the silent
        # skip Issue #829 is about): no fetch, exit 2, error explains why.
        self._write_registry([])
        rc, data, fetched = self._run(
            ["--all-registry-repos", "--claude-org-root", str(self.root)]
        )
        self.assertEqual(fetched, [])
        self.assertEqual(rc, wds.EXIT_ERROR)
        self.assertEqual(data["status"], "error")
        self.assertIn("repo resolution failed", data["error"])
        # The audit survives onto the error envelope.
        self.assertEqual(data["repo_resolution"]["repos"], [])
        self.assertIn("error", data["repo_resolution"])

    def test_trigger_is_preserved_on_resolution_failure(self):
        # The delivery layer records `generated_for`; a resolution failure
        # must not relabel a worker_close scan as "manual".
        self._write_registry([])
        rc, data, _ = self._run(
            [
                "--all-registry-repos",
                "--claude-org-root", str(self.root),
                "--trigger", "worker_close",
            ]
        )
        self.assertEqual(rc, wds.EXIT_ERROR)
        self.assertEqual(data["generated_for"], "worker_close")

    def test_conflicts_with_explicit_repo(self):
        self._write_registry([f"ja | ja | https://github.com/{JA} | d | x |"])
        rc, data, fetched = self._run(
            [
                "--all-registry-repos",
                "--claude-org-root", str(self.root),
                "--repo", RT,
            ]
        )
        self.assertEqual(rc, wds.EXIT_ERROR)
        self.assertEqual(fetched, [])
        self.assertIn("mutually exclusive", data["error"])

    def test_conflicts_with_from_file(self):
        self._write_registry([f"ja | ja | https://github.com/{JA} | d | x |"])
        fd, name = tempfile.mkstemp(suffix=".json", prefix="wds_reg_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write('{"issues": [], "open_pr_numbers": [], "recent_merges": []}')
            rc, data, _ = self._run(
                [
                    "--all-registry-repos",
                    "--claude-org-root", str(self.root),
                    "--from-file", name,
                ]
            )
        finally:
            os.unlink(name)
        self.assertEqual(rc, wds.EXIT_ERROR)
        self.assertIn("--from-file", data["error"])

    def test_unreadable_registry_keeps_the_audited_shape(self):
        # `resolve_repos()` *raises* when the registry exists but cannot be
        # read (permission denied / undecodable bytes / a directory in its
        # place). Propagating that would land in main's generic handler with
        # repo_resolution still None — the fixed schema broken on exactly the
        # failure that needs explaining. A directory reproduces it portably
        # (read_text -> IsADirectoryError, an OSError) without chmod, which
        # is a no-op for root and on Windows.
        (self.root / "registry" / "projects.md").mkdir()
        rc, data, fetched = self._run(
            ["--all-registry-repos", "--claude-org-root", str(self.root)]
        )
        self.assertEqual(rc, wds.EXIT_ERROR)
        self.assertEqual(fetched, [])
        self.assertIsNotNone(data["repo_resolution"])
        self.assertEqual(data["repo_resolution"]["repos"], [])
        self.assertIn("failed to resolve repos", data["repo_resolution"]["error"])
        self.assertIn("failed to resolve repos", data["error"])

    def test_undecodable_registry_keeps_the_audited_shape(self):
        # `UnicodeDecodeError` is a ValueError, NOT an OSError — so a catch
        # listing only OSError still let this one through and produced
        # `repo_resolution: null`. The wrapper's catch is class-wide for
        # exactly this reason; this pins the case that proved it.
        (self.root / "registry" / "projects.md").write_bytes(bytes([255]))
        rc, data, fetched = self._run(
            ["--all-registry-repos", "--claude-org-root", str(self.root)]
        )
        self.assertEqual(rc, wds.EXIT_ERROR)
        self.assertEqual(fetched, [])
        self.assertIsNotNone(data["repo_resolution"])
        self.assertIn("UnicodeDecodeError", data["repo_resolution"]["error"])

    def test_repo_resolution_is_null_without_the_flag(self):
        # One shape for the delivery layer: the key is always present.
        rc, data, _ = self._run(["--repo", JA])
        self.assertIsNone(data["repo_resolution"])

    def test_flag_is_shell_independent_end_to_end(self):
        # The regression itself: the same one-line command must resolve the
        # same repo set under zsh and bash. Only the *argv shape* is under
        # test, so the fetchers are skipped by asserting on the resolver
        # (an empty registry → deterministic exit 2 with no network).
        self._write_registry([])
        cmd = (
            f'{sys.executable} {SCRIPT} --all-registry-repos '
            f'--claude-org-root {self.root} --trigger worker_close'
        )
        seen = {}
        for shell in ("zsh", "bash"):
            if shutil.which(shell) is None:
                self.skipTest(f"{shell} not available")
            proc = subprocess.run(
                [shell, "-c", cmd], capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, wds.EXIT_ERROR, shell)
            seen[shell] = json.loads(proc.stdout)
        self.assertEqual(seen["zsh"], seen["bash"])
        self.assertEqual(seen["zsh"]["generated_for"], "worker_close")


# ----------------------------------------------------------------------
# Two-track base_branch completion (Issue #830)
# ----------------------------------------------------------------------

KURA = "aainc/kura-data-aggregator-trial"

# The real shapes behind Issue #830, captured from GitHub on 2026-08-06
# (gh 2.74.0). Kept verbatim-ish so the regression reproduces the reported
# case rather than an idealized version of it: PR #232 targets `develop`,
# carries `Closes #231` in its body, and reports NO closingIssuesReferences
# (GitHub only links closing issues for default-branch PRs) — which is why
# Issue #231 was still `open` and got triaged as untouched.
KURA_ISSUE_231 = {
    "number": 231,
    "title": "MCP トークン TTL 暫定値 (8h) を既定 900s へ戻す (refresh rotation の本番確認後)",
    "body": "refresh token が本番で回ることを確認してから暫定 TTL を外す。",
    "labels": [],
    "createdAt": "2026-08-05T01:20:00Z",
    "updatedAt": "2026-08-06T02:33:45Z",
}
KURA_PR_232 = {
    "number": 232,
    "title": "fix(kura): MCP アクセストークン TTL の暫定 8 時間を外し既定 900s へ戻す",
    "body": (
        "## 概要\n\n"
        "#226 の後始末。refresh token (#228) が本番稼働していることを確認できたので、"
        "#227 で入れた暫定 TTL を削除し、アプリ側の既定 900s へ戻す。\n\n"
        "Closes #231\n"
    ),
    "baseRefName": "develop",
    "mergedAt": "2026-08-06T02:33:45Z",
}


def _kura_bundle(**over):
    bundle = {
        "repo": KURA,
        "issues": [KURA_ISSUE_231],
        "open_pr_numbers": set(),
        # The merge that finished #231 is also in the recent-merge window —
        # that is what made `unblocked_by_recent_merge` fire and pushed the
        # finished Issue to rank 1 (the reported symptom).
        "recent_merges": [KURA_PR_232],
        "base_branch": "develop",
        "base_merges": [KURA_PR_232],
    }
    bundle.update(over)
    return bundle


class TestBaseBranchCompletion(unittest.TestCase):
    """Issues finished by a merge into a declared non-default base branch are
    excluded with a reason, not ranked first (Issue #830).

    On a `base_branch=develop` repo GitHub's auto-close never fires, so the
    Issue stays `open`; the pre-#830 scan read that as untouched AND lit up
    `unblocked_by_recent_merge` off the very merge that completed it. Every
    test here drives the pure core with fixture data — no `gh`, no network.
    """

    def _scan(self, bundles, **cfg):
        config = wds.ScanConfig(
            top_n=cfg.pop("top_n", 3),
            free_panes=cfg.pop("free_panes", None),
            trigger=cfg.pop("trigger", "post_merge"),
        )
        return wds.scan_repos(bundles, config, **cfg)

    def test_pre_fix_symptom_without_base_branch(self):
        # Pins the bug being fixed: with no base_branch configured, the
        # completed Issue is not just present — it is rank 1, boosted by the
        # merge that finished it. This is the state kura#231 was reported in.
        result = self._scan(
            [_kura_bundle(base_branch=None, base_merges=[])],
            collapse_repo=KURA,
        )
        self.assertEqual(result["status"], "candidates_found")
        top = result["candidates"][0]
        self.assertEqual(top["issue"], 231)
        self.assertEqual(top["rank"], 1)
        self.assertTrue(top["unblocked_by_recent_merge"])
        self.assertEqual(result["excluded_merged"], [])

    def test_kura_231_is_excluded_with_a_reason(self):
        # The fix: same input + `base_branch=develop` → not a candidate, and
        # the exclusion names PR #232 and the branch it landed on.
        result = self._scan([_kura_bundle()], collapse_repo=KURA)
        self.assertEqual([c["issue"] for c in result["candidates"]], [])
        self.assertEqual(result["status"], "no_candidates")
        self.assertEqual(len(result["excluded_merged"]), 1)
        entry = result["excluded_merged"][0]
        self.assertEqual(entry["issue"], 231)
        self.assertIsNone(entry["repo"])  # collapsed single-repo display
        self.assertEqual(entry["base_branch"], "develop")
        self.assertEqual(entry["closed_by_pr"], 232)
        self.assertEqual(entry["merged_at"], "2026-08-06T02:33:45Z")
        self.assertIn("#232", entry["note"])
        self.assertIn("develop", entry["note"])
        # Not silent, and not conflated with the dependency exclusion枠.
        self.assertEqual(result["excluded_blocked"], [])

    def test_excluded_issue_is_not_recommended(self):
        # The recommendation is derived from the ranked list, so a completed
        # Issue must not reappear there either.
        result = self._scan([_kura_bundle()], collapse_repo=KURA)
        self.assertIsNone(result["recommendation"])

    def test_base_branch_scan_audit_is_emitted(self):
        result = self._scan([_kura_bundle()], collapse_repo=KURA)
        self.assertEqual(
            result["base_branch_scan"],
            [
                {
                    "repo": None,
                    "base_branch": "develop",
                    "merged_prs_scanned": 1,
                    "closed_issue_count": 1,
                }
            ],
        )
        self.assertEqual(result["base_branch_signals"], [])

    def test_repo_without_base_branch_is_unchanged(self):
        # Acceptance criterion: projects with no base_branch behave exactly
        # as before, even when a merged PR closing the Issue is in the data.
        result = self._scan(
            [
                {
                    "repo": "o/plain",
                    "issues": [_issue(7, body="b")],
                    "open_pr_numbers": set(),
                    "recent_merges": [{"number": 8, "body": "Closes #7"}],
                }
            ],
            collapse_repo="o/plain",
        )
        self.assertEqual([c["issue"] for c in result["candidates"]], [7])
        self.assertEqual(result["excluded_merged"], [])
        self.assertEqual(result["base_branch_scan"], [])

    def test_mere_reference_does_not_exclude(self):
        # `Refs #N` is a mention, not a completion — excluding on it would
        # drop live work (the §11-3 over-matching risk, applied to merges).
        pr = dict(KURA_PR_232, body="Refs #231\n")
        result = self._scan(
            [_kura_bundle(base_merges=[pr], recent_merges=[pr])],
            collapse_repo=KURA,
        )
        self.assertEqual([c["issue"] for c in result["candidates"]], [231])
        self.assertEqual(result["excluded_merged"], [])

    def test_negated_close_keyword_does_not_exclude(self):
        pr = dict(KURA_PR_232, body="This does not close #231 yet.\n")
        result = self._scan(
            [_kura_bundle(base_merges=[pr], recent_merges=[])],
            collapse_repo=KURA,
        )
        self.assertEqual([c["issue"] for c in result["candidates"]], [231])
        self.assertEqual(result["excluded_merged"], [])

    def test_merge_targeting_another_branch_is_not_counted(self):
        # An offline bundle can carry unfiltered merges; a baseRefName that
        # contradicts the configured branch must not widen the exclusion, and
        # the mismatch is reported rather than dropped.
        pr = dict(KURA_PR_232, baseRefName="feature/x")
        result = self._scan(
            [_kura_bundle(base_merges=[pr], recent_merges=[])],
            collapse_repo=KURA,
        )
        self.assertEqual([c["issue"] for c in result["candidates"]], [231])
        self.assertEqual(result["excluded_merged"], [])
        self.assertTrue(
            any("target a different branch" in s
                for s in result["base_branch_signals"]),
            result["base_branch_signals"],
        )

    def test_issue_created_after_the_merge_is_not_excluded(self):
        # Sanity guard: a merge cannot have completed an Issue that did not
        # exist yet (number reuse / a hand-written `Closes` typo).
        issue = dict(KURA_ISSUE_231, createdAt="2026-08-07T00:00:00Z")
        result = self._scan(
            [_kura_bundle(issues=[issue], recent_merges=[])],
            collapse_repo=KURA,
        )
        self.assertEqual([c["issue"] for c in result["candidates"]], [231])
        self.assertEqual(result["excluded_merged"], [])
        self.assertTrue(
            any("closing link ignored" in s
                for s in result["base_branch_signals"]),
            result["base_branch_signals"],
        )

    def test_missing_created_at_still_excludes(self):
        # The guard abstains when it cannot compare, rather than inventing an
        # ordering that would resurrect the bug.
        issue = {k: v for k, v in KURA_ISSUE_231.items() if k != "createdAt"}
        result = self._scan(
            [_kura_bundle(issues=[issue], recent_merges=[])],
            collapse_repo=KURA,
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["excluded_merged"][0]["issue"], 231)

    def test_newest_closing_merge_is_the_one_cited(self):
        older = {
            "number": 100,
            "title": "t",
            "body": "Closes #231",
            "baseRefName": "develop",
            "mergedAt": "2026-07-01T00:00:00Z",
        }
        result = self._scan(
            [_kura_bundle(base_merges=[older, KURA_PR_232], recent_merges=[])],
            collapse_repo=KURA,
        )
        self.assertEqual(result["excluded_merged"][0]["closed_by_pr"], 232)

    def test_dependent_unblocks_when_its_blocker_was_merged(self):
        # A completed Issue is still `open` on GitHub, so leaving it in the
        # open-blocker set would keep its dependents excluded as blocked by
        # finished work. It unblocks — and says why.
        dependent = _issue(240, body="Blocked by #231")
        result = self._scan(
            [_kura_bundle(issues=[KURA_ISSUE_231, dependent])],
            collapse_repo=KURA,
        )
        self.assertEqual([c["issue"] for c in result["candidates"]], [240])
        self.assertEqual(result["excluded_blocked"], [])
        signals = result["candidates"][0]["signals"]
        self.assertTrue(
            any("#231" in s and "#232" in s and "develop" in s
                for s in signals),
            signals,
        )

    def test_cross_repo_close_from_a_base_branch_merge(self):
        # A develop PR that closes another scanned repo's Issue keeps that
        # Issue's repo, and the exclusion cites the merging repo's PR.
        pr = dict(
            KURA_PR_232, body=f"Closes suisya-systems/claude-org-ja#77\n"
        )
        other = {
            "repo": "suisya-systems/claude-org-ja",
            "issues": [_issue(77, body="b")],
            "open_pr_numbers": set(),
            "recent_merges": [],
        }
        result = self._scan(
            [_kura_bundle(issues=[], base_merges=[pr], recent_merges=[]), other]
        )
        self.assertEqual(result["candidates"], [])
        entry = result["excluded_merged"][0]
        self.assertEqual(entry["repo"], "suisya-systems/claude-org-ja")
        self.assertEqual(entry["issue"], 77)
        self.assertEqual(entry["closed_by_pr"], f"{KURA}#232")

    def test_cross_repo_close_matches_case_insensitively(self):
        # GitHub slugs are case-insensitive and a PR author writes whatever
        # casing they like, while the registry-driven scan set is lowercased.
        # Missing on case alone would leave finished work in the list.
        pr = dict(KURA_PR_232, body="Closes Suisya-Systems/Claude-Org-JA#77\n")
        other = {
            "repo": "suisya-systems/claude-org-ja",
            "issues": [_issue(77, body="b")],
            "open_pr_numbers": set(),
            "recent_merges": [],
        }
        result = self._scan(
            [_kura_bundle(issues=[], base_merges=[pr], recent_merges=[]), other]
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            result["excluded_merged"][0]["repo"], "suisya-systems/claude-org-ja"
        )

    def test_origin_prefixed_and_padded_base_branch_cell(self):
        # Registry cells are padded and an operator may write the ref the way
        # git prints it; the scan compares against baseRefName either way.
        result = self._scan(
            [_kura_bundle(base_branch="  develop  ")], collapse_repo=KURA
        )
        self.assertEqual(result["excluded_merged"][0]["issue"], 231)

    def test_scan_shim_accepts_base_branch_inputs(self):
        result = wds.scan(
            [KURA_ISSUE_231],
            set(),
            [KURA_PR_232],
            wds.ScanConfig(top_n=3, free_panes=None, trigger="manual"),
            base_branch="develop",
            base_merges=[KURA_PR_232],
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["excluded_merged"][0]["closed_by_pr"], 232)

    def test_scan_is_still_pure(self):
        # Same input → same output (design §4 再現性契約).
        a = self._scan([_kura_bundle()], collapse_repo=KURA)
        b = self._scan([_kura_bundle()], collapse_repo=KURA)
        self.assertEqual(a, b)


class TestBaseBranchCompletionCli(unittest.TestCase):
    """The `--from-file` / CLI surface of Issue #830."""

    def _run(self, bundle, extra_argv=()):
        fd, name = tempfile.mkstemp(suffix=".json", prefix="wds_bb_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(bundle, f)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--from-file", name,
                 *extra_argv],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        finally:
            os.unlink(name)
        return proc

    def test_from_file_bundle_excludes_and_exits_0(self):
        bundle = {
            "repos": [
                {
                    "repo": KURA,
                    "issues": [KURA_ISSUE_231],
                    "open_pr_numbers": [],
                    "recent_merges": [KURA_PR_232],
                    "base_branch": "develop",
                    "base_merges": [KURA_PR_232],
                }
            ]
        }
        proc = self._run(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_NO_CANDIDATES)
        data = json.loads(proc.stdout)
        self.assertEqual(data["candidates"], [])
        self.assertEqual(data["excluded_merged"][0]["issue"], 231)

    def test_base_merges_zero_also_disables_the_offline_path(self):
        # "0 disables the check" has to mean the same thing offline: a bundle
        # carrying its own base inputs must stop excluding once the caller
        # switched the check off, or the flag lies about what it does.
        bundle = {
            "repos": [
                {
                    "repo": KURA,
                    "issues": [KURA_ISSUE_231],
                    "open_pr_numbers": [],
                    "recent_merges": [],
                    "base_branch": "develop",
                    "base_merges": [KURA_PR_232],
                }
            ]
        }
        proc = self._run(bundle, ["--base-merges", "0"])
        self.assertEqual(proc.returncode, wds.EXIT_CANDIDATES_FOUND)
        data = json.loads(proc.stdout)
        self.assertEqual([c["issue"] for c in data["candidates"]], [231])
        self.assertEqual(data["excluded_merged"], [])
        self.assertEqual(data["base_branch_scan"], [])

    def test_non_string_base_branch_is_a_pinpointed_error(self):
        bundle = {"issues": [], "base_branch": 5}
        proc = self._run(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertIn("base_branch", data["error"])

    def test_non_object_base_merge_row_is_a_pinpointed_error(self):
        bundle = {"issues": [], "base_branch": "develop", "base_merges": ["x"]}
        proc = self._run(bundle)
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        data = json.loads(proc.stdout)
        self.assertIn("base_merges[0]", data["error"])

    def test_negative_base_merges_rejected(self):
        proc = self._run({"issues": []}, ["--base-merges", "-1"])
        self.assertEqual(proc.returncode, wds.EXIT_ERROR)
        self.assertIn("--base-merges must be >= 0", json.loads(proc.stdout)["error"])

    def test_error_envelope_carries_the_new_keys(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--from-file", "/no/such/file.json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        data = json.loads(proc.stdout)
        for key in ("excluded_merged", "base_branch_scan", "base_branch_signals"):
            self.assertIn(key, data)
        self.assertIn("base_merges", data["input_truncated"])


class TestFetchBaseBranchMerges(unittest.TestCase):
    """The fetch window is merge-time-exact, not update-time-approximate."""

    def test_overfetches_then_slices_by_merged_at(self):
        # `gh pr list --limit` pages under `sort:updated-desc`, so an old
        # merged PR with a fresh comment can crowd a just-merged closing PR
        # off the page — and that is exactly the PR this check needs. Mirror
        # fetch_recent_merges: over-fetch, then take the mergedAt top-K.
        page = [
            {"number": 1, "mergedAt": "2026-01-01T00:00:00Z"},
            {"number": 2, "mergedAt": "2026-08-06T00:00:00Z"},
            {"number": 3, "mergedAt": "2026-05-01T00:00:00Z"},
        ]
        seen = {}

        def _fake(args):
            seen["args"] = args
            return list(page)

        with mock.patch.object(wds, "_run_gh_json_list", _fake):
            out = wds.fetch_base_branch_merges(KURA, "develop", 2)
        self.assertEqual([p["number"] for p in out], [2, 3])
        args = seen["args"]
        self.assertIn("--base", args)
        self.assertEqual(args[args.index("--base") + 1], "develop")
        # The requested page is larger than the window it returns.
        self.assertEqual(
            args[args.index("--limit") + 1],
            str(2 * wds._RECENT_MERGE_OVERFETCH),
        )
        # closingIssuesReferences is deliberately not requested (empty for
        # non-default-branch PRs — measured; the closing keyword is the SoT).
        self.assertNotIn(
            "closingIssuesReferences", args[args.index("--json") + 1]
        )


class TestBaseBranchFromRegistry(unittest.TestCase):
    """The registry (`base_branch` column) drives which repos get the check."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "registry").mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _write_registry(self, rows):
        lines = [
            "# Projects Registry",
            "",
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage "
            "| base_branch |",
            "|---|---|---|---|---|---|---|",
        ]
        lines += [f"| {row} |" for row in rows]
        (self.root / "registry" / "projects.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _run(self, argv, issues_by_repo=None):
        """main() with every fetcher mocked; returns (rc, json, base_calls)."""
        base_calls = []

        def _base(repo, branch, limit):
            base_calls.append((repo, branch, limit))
            return [KURA_PR_232] if branch == "develop" else []

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.object(
                 wds, "fetch_open_issues",
                 lambda r, limit=wds.DEFAULT_OPEN_LIMIT:
                     (issues_by_repo or {}).get(r, [])), \
             mock.patch.object(
                 wds, "fetch_open_pr_numbers",
                 lambda r, limit=wds.DEFAULT_OPEN_LIMIT: set()), \
             mock.patch.object(wds, "fetch_recent_merges", lambda r, k: []), \
             mock.patch.object(wds, "fetch_base_branch_merges", _base), \
             mock.patch.object(wds, "build_effort_model", lambda r, n: None):
            with redirect_stdout(buf):
                rc = wds.main(argv)
        return rc, json.loads(buf.getvalue()), base_calls

    def test_explicit_repo_still_gets_the_registry_base_branch(self):
        # Naming the repo by hand must not reintroduce the bug: the registry
        # is the SoT for base_branch regardless of how the repo set was built.
        self._write_registry(
            [f"kura | kura | https://github.com/{KURA} | d | x |  | develop |"]
        )
        rc, data, calls = self._run(
            ["--repo", KURA, "--claude-org-root", str(self.root)],
            {KURA: [KURA_ISSUE_231]},
        )
        self.assertEqual(rc, wds.EXIT_NO_CANDIDATES)
        self.assertEqual(calls, [(KURA, "develop", wds.DEFAULT_BASE_MERGE_LIMIT)])
        self.assertEqual(data["excluded_merged"][0]["issue"], 231)

    def test_all_registry_repos_uses_the_resolver_map(self):
        self._write_registry(
            [
                f"kura | kura | https://github.com/{KURA} | d | x |  | develop |",
                f"ja | ja | https://github.com/{JA} | d | x |  |  |",
            ]
        )
        rc, data, calls = self._run(
            ["--all-registry-repos", "--claude-org-root", str(self.root)],
            {KURA: [KURA_ISSUE_231]},
        )
        # Only the base_branch row is fetched; the unset row is untouched.
        self.assertEqual(calls, [(KURA, "develop", wds.DEFAULT_BASE_MERGE_LIMIT)])
        self.assertEqual(rc, wds.EXIT_NO_CANDIDATES)
        self.assertEqual(data["excluded_merged"][0]["repo"], KURA)
        self.assertEqual(
            data["repo_resolution"]["base_branches"], {KURA: "develop"}
        )

    def test_origin_prefixed_registry_cell_is_normalized(self):
        self._write_registry(
            [f"kura | kura | https://github.com/{KURA} | d | x |  | origin/develop |"]
        )
        _, _, calls = self._run(
            ["--repo", KURA, "--claude-org-root", str(self.root)],
            {KURA: [KURA_ISSUE_231]},
        )
        self.assertEqual(calls, [(KURA, "develop", wds.DEFAULT_BASE_MERGE_LIMIT)])

    def test_base_merges_zero_disables_the_check(self):
        self._write_registry(
            [f"kura | kura | https://github.com/{KURA} | d | x |  | develop |"]
        )
        rc, data, calls = self._run(
            ["--repo", KURA, "--claude-org-root", str(self.root),
             "--base-merges", "0"],
            {KURA: [KURA_ISSUE_231]},
        )
        self.assertEqual(calls, [])
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)
        self.assertEqual(data["excluded_merged"], [])

    def test_unset_base_branch_column_scans_nothing(self):
        self._write_registry(
            [f"kura | kura | https://github.com/{KURA} | d | x |  | - |"]
        )
        rc, data, calls = self._run(
            ["--repo", KURA, "--claude-org-root", str(self.root)],
            {KURA: [KURA_ISSUE_231]},
        )
        self.assertEqual(calls, [])
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)

    def test_missing_registry_degrades_without_failing(self):
        # No registry at all: the scan still runs (pre-#830 behaviour), it
        # just cannot exclude anything this way — and says so, because
        # "nothing to exclude" and "never looked" must not read the same.
        rc, data, calls = self._run(
            ["--repo", KURA, "--claude-org-root", str(self.root)],
            {KURA: [KURA_ISSUE_231]},
        )
        self.assertEqual(calls, [])
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)
        self.assertTrue(
            any("registry not found" in s for s in data["base_branch_signals"]),
            data["base_branch_signals"],
        )

    def test_unreadable_registry_is_reported_not_swallowed(self):
        (self.root / "registry" / "projects.md").write_bytes(bytes([255]))
        rc, data, calls = self._run(
            ["--repo", KURA, "--claude-org-root", str(self.root)],
            {KURA: [KURA_ISSUE_231]},
        )
        self.assertEqual(calls, [])
        self.assertEqual(rc, wds.EXIT_CANDIDATES_FOUND)
        self.assertTrue(
            any("base-branch completion check skipped" in s
                for s in data["base_branch_signals"]),
            data["base_branch_signals"],
        )

    def test_truncated_base_merge_window_is_flagged(self):
        self._write_registry(
            [f"kura | kura | https://github.com/{KURA} | d | x |  | develop |"]
        )
        rc, data, _ = self._run(
            ["--repo", KURA, "--claude-org-root", str(self.root),
             "--base-merges", "1"],
            {KURA: [KURA_ISSUE_231]},
        )
        self.assertTrue(data["input_truncated"]["base_merges"])


if __name__ == "__main__":
    unittest.main()
