"""Regression tests for tools/inspect_anomaly_scan.py (Issue #492).

These lock the three ERROR-detection gaps fixed in
``.dispatcher/references/worker-monitoring.md`` §4(d):

* gap (1) — the scan must cover every visible row, not just bottom 10;
* gap (2) — ``529`` (and ``502`` / ``503`` / ``504``) must be detected;
* gap (3) — a spinner that has been counting for >= 5 minutes is an
  ERROR-equivalent.

The headline test reproduces the 2026-05-28 observation from
``worker-skill-worktree-remove-force-491``: an ``API Error: 529`` banner
parked at row 15 of a 43-row pane, a ``✻ ... for 9m ...`` spinner, and a
bottom-10 window that contains only blanks + prompt + status bar.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import inspect_anomaly_scan as ias  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "inspect_anomaly_scan.py"


def _observed_pane() -> list[dict]:
    """The 2026-05-28 pane: 529 banner at row 15, 9m spinner, empty bottom 10.

    Mirrors the grid quoted in Issue #492. Rows are 1-indexed to match how
    the Issue describes them; the detector is row-agnostic so the exact
    numbering only matters for the bottom-N slice below.
    """
    lines = [{"row": r, "text": ""} for r in range(1, 44)]

    def put(row: int, text: str) -> None:
        lines[row - 1] = {"row": row, "text": text}

    put(12, "Ran 1 shell command")
    put(13, "  ⎿  ok")
    put(14, "←renga-peers: ack from secretary")
    put(15, "API Error: 529 Overloaded. Retrying…")
    put(17, "✻ Sautéed for 9m 12s")
    put(39, "──────────────────────")
    put(40, "❯")
    put(41, "  ⏵⏵ accept edits on")
    put(42, "claude-opus-4-8")
    return lines


class ObservedRegressionTests(unittest.TestCase):
    """The headline Issue #492 case must fire ERROR; bottom-10 alone must not."""

    def test_full_scan_detects_529_and_stuck_spinner(self) -> None:
        detections = ias.scan_lines(_observed_pane())
        reasons = {d.reason for d in detections}

        # gap (2): the 529 banner is caught (by the bare 529 code and/or by
        # the "API Error" substring — at least one substring detection on
        # the banner row).
        banner = [d for d in detections if d.row == 15]
        self.assertTrue(banner, "529 banner at row 15 must be detected")
        self.assertTrue(
            any(r.startswith("substring:") for r in reasons),
            f"expected a substring detection, got {reasons}",
        )

        # gap (3): the 9m spinner is an ERROR-equivalent.
        self.assertIn("spinner_age:9m", reasons)

        # every detection maps onto the ERROR notification path.
        self.assertTrue(all(d.kind == "error" for d in detections))

    def test_bottom_10_window_misses_the_banner(self) -> None:
        """Demonstrates gap (1): the old bottom-10 scan is blind here."""
        pane = _observed_pane()
        bottom_10 = pane[-10:]  # rows 34-43: blanks + prompt + status bar
        self.assertEqual(ias.scan_lines(bottom_10), [])


class SubstringTests(unittest.TestCase):
    def test_api_error_case_insensitive(self) -> None:
        self.assertTrue(ias.scan_lines(["some api error happened"]))
        self.assertTrue(ias.scan_lines(["SOME API ERROR HAPPENED"]))

    def test_rate_limit_detected(self) -> None:
        self.assertTrue(ias.scan_lines(["rate limit reached, backing off"]))

    def test_clean_line_no_detection(self) -> None:
        self.assertEqual(ias.scan_lines(["Ran 1 shell command", "  ⎿  ok"]), [])

    def test_single_reason_per_line(self) -> None:
        # "API Error" (strong substring) wins; the gated 529 code does not add
        # a second detection on the same line.
        dets = ias.scan_lines(["API Error: 529 Overloaded"])
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].reason, "substring:API Error")


class StatusCodeTests(unittest.TestCase):
    """gap (2): status codes detected, but only as gated tokens.

    These guard the regression without leaning on the ``API Error``
    substring — removing a code from ``ERROR_STATUS_CODES`` makes the
    relevant subTest fail.
    """

    def test_each_code_detected_without_api_error_wording(self) -> None:
        # Lines carry an error-context keyword but NOT "API Error", so the
        # bare-code path is what fires.
        contexts = {
            "429": "HTTP 429 too many requests, retrying",
            "500": "Upstream 500 server error",
            "502": "got 502 from gateway",
            "503": "service unavailable (503)",
            "504": "504 gateway timeout, retry",
            "529": "Overloaded (529), retrying…",
        }
        for code, line in contexts.items():
            with self.subTest(code=code):
                dets = ias.scan_lines([line])
                self.assertEqual(
                    [d.reason for d in dets],
                    [f"status_code:{code}"],
                    f"{code} should be detected via the gated code path",
                )

    def test_bare_code_without_error_context_not_detected(self) -> None:
        # No error keyword on the line → benign, must not fire.
        for benign in ("500 passed, 0 failed", "see issue #529", "build 502 ok"):
            with self.subTest(line=benign):
                self.assertEqual(ias.scan_lines([benign]), [])

    def test_substring_of_larger_number_not_detected(self) -> None:
        # "5000" must not match \b500\b even with error context present.
        self.assertEqual(
            ias.scan_lines(["error connecting to localhost:5000"]), []
        )

    def test_issue_ref_with_error_context_not_detected(self) -> None:
        # A "#529" GitHub ref must not fire even when the same line mentions
        # "error" (e.g. an issue conversation in the pane). The (?<!#) guard
        # drops it; \b alone would let it through.
        for line in (
            "Issue #529: error detection",
            "error in issue #529 reproduction",
        ):
            with self.subTest(line=line):
                self.assertEqual(ias.scan_lines([line]), [])


class AnchoredRegexTests(unittest.TestCase):
    def test_error_prefix_detected(self) -> None:
        dets = ias.scan_lines(["Error: boom"])
        self.assertEqual(len(dets), 1)
        self.assertTrue(dets[0].reason.startswith("regex:"))

    def test_error_uppercase_prefix_detected(self) -> None:
        self.assertTrue(ias.scan_lines(["ERROR: boom"]))

    def test_error_not_at_line_start_not_matched_by_regex(self) -> None:
        # "  Error: " is not line-anchored, so the regex path does not fire.
        dets = ias.scan_lines(["  Error: indented"])
        self.assertFalse(any(d.reason.startswith("regex:") for d in dets))


class SpinnerAgeTests(unittest.TestCase):
    def test_spinner_below_threshold_not_detected(self) -> None:
        self.assertEqual(ias.scan_lines(["✻ Cogitating for 2m 30s"]), [])

    def test_spinner_at_threshold_detected(self) -> None:
        dets = ias.scan_lines(["✻ Cogitating for 5m 0s"])
        self.assertEqual([d.reason for d in dets], ["spinner_age:5m"])

    def test_spinner_above_threshold_detected(self) -> None:
        dets = ias.scan_lines(["✺ Pondering for 12m 8s"])
        self.assertEqual([d.reason for d in dets], ["spinner_age:12m"])

    def test_non_ascii_verb_matches(self) -> None:
        dets = ias.scan_lines(["✻ Sautéed for 6m 23s"])
        self.assertEqual([d.reason for d in dets], ["spinner_age:6m"])

    def test_custom_threshold(self) -> None:
        dets = ias.scan_lines(
            ["✻ Working for 3m 0s"], spinner_threshold_min=2
        )
        self.assertEqual([d.reason for d in dets], ["spinner_age:3m"])

    def test_not_a_spinner_line(self) -> None:
        self.assertEqual(ias.scan_lines(["working for 9m on the task"]), [])


class NormalizationTests(unittest.TestCase):
    def test_accepts_bare_strings(self) -> None:
        dets = ias.scan_lines(["API Error: 529"])
        self.assertEqual(dets[0].row, 0)

    def test_handles_none_text(self) -> None:
        self.assertEqual(ias.scan_lines([{"row": 1, "text": None}]), [])


class CliTests(unittest.TestCase):
    def _run(self, payload: dict, *extra: str):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *extra],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )

    def test_cli_structured_content_exit_3_on_anomaly(self) -> None:
        payload = {"structuredContent": {"lines": _observed_pane()}}
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 3)
        out = json.loads(proc.stdout)
        reasons = {d["reason"] for d in out["detections"]}
        self.assertIn("spinner_age:9m", reasons)

    def test_cli_clean_pane_exit_0(self) -> None:
        payload = {"lines": [{"row": 1, "text": "Ran 1 shell command"}]}
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["detections"], [])

    def test_cli_custom_threshold(self) -> None:
        payload = {"lines": ["✻ Working for 3m 0s"]}
        proc = self._run(payload, "--spinner-threshold-min", "2")
        self.assertEqual(proc.returncode, 3)


if __name__ == "__main__":
    unittest.main()
