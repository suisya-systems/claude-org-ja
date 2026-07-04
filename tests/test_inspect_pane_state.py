"""Regression tests for tools/inspect_pane_state.py (Issues #680 / #671).

These lock the deterministic pane-state extraction that replaces the prose
single-point screen-change compare and the stale spinner-age regex in
``.dispatcher/references/worker-monitoring.md`` Step 5:

* #680 — the content hash covers all normalized visible rows, so a rotating
  spinner / advancing timer alone keeps the hash stable (idle) while a real
  scrollback change flips it (active);
* #671 — the new-form ``Verb… (Xm Ys · ...)`` spinner is parsed and drives a
  ``suppress_stall`` signal that is released once the elapsed counter reaches
  ``SPINNER_ACTIVE_SUPPRESS_CAP_MIN`` (frozen / dead-API spinner cannot mask a
  stall forever);
* Blocker 2 — a record missing ``last_visible_content_hash`` is a first
  observation (reset streak, no migration from the deprecated target line).

The five brief pins: (1) both new spinner patterns parse; (2) timer-only change
keeps the hash invariant; (3) a middle-row change flips the hash; (4) old-state
missing hash resets on first observation; (5) cap-exceeded releases suppression.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import inspect_pane_state as ips  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "inspect_pane_state.py"


def _pane(*texts: str) -> list[dict]:
    return [{"row": i + 1, "text": t} for i, t in enumerate(texts)]


# The two new-form spinner patterns the brief pins (minutes+seconds, hours+min+sec).
NEW_SPINNER_MS = "✻ Puttering… (2m 5s · ↑ 1.2k tokens · esc to interrupt)"
NEW_SPINNER_HMS = "✻ Gesticulating… (1h 1m 42s · ↓ 121.5k tokens)"


class NewSpinnerParseTests(unittest.TestCase):
    """Pin (1): both new-form spinner patterns parse to verb + elapsed seconds."""

    def test_minutes_seconds_form(self) -> None:
        info = ips.parse_new_spinner(NEW_SPINNER_MS)
        self.assertIsNotNone(info)
        self.assertEqual(info.signature, "Puttering")
        self.assertEqual(info.elapsed_sec, 2 * 60 + 5)

    def test_hours_minutes_seconds_form(self) -> None:
        info = ips.parse_new_spinner(NEW_SPINNER_HMS)
        self.assertIsNotNone(info)
        self.assertEqual(info.signature, "Gesticulating")
        self.assertEqual(info.elapsed_sec, 3600 + 60 + 42)

    def test_seconds_only_form(self) -> None:
        info = ips.parse_new_spinner("✶ Noodling… (23s · ↑ 400 tokens)")
        self.assertIsNotNone(info)
        self.assertEqual(info.elapsed_sec, 23)

    def test_ascii_ellipsis_and_no_glyph(self) -> None:
        info = ips.parse_new_spinner("Thinking... (5m 0s)")
        self.assertIsNotNone(info)
        self.assertEqual(info.signature, "Thinking")
        self.assertEqual(info.elapsed_sec, 300)

    def test_non_ascii_verb(self) -> None:
        info = ips.parse_new_spinner("✻ Sautéing… (1m 0s · ↓ 2k tokens)")
        self.assertIsNotNone(info)
        self.assertEqual(info.signature, "Sautéing")

    def test_old_form_is_not_a_new_spinner(self) -> None:
        # Old "for Xm Ys" form is owned by inspect_anomaly_scan.py (ERROR path).
        self.assertIsNone(ips.parse_new_spinner("✻ Sautéed for 9m 12s"))

    def test_plain_prose_not_a_spinner(self) -> None:
        self.assertIsNone(ips.parse_new_spinner("Working on the task (almost done)"))


class HashInvarianceTests(unittest.TestCase):
    """Pins (2)/(3): timer-only change is invariant; a content change flips the hash."""

    def _mid_change_panes(self):
        base = _pane(
            "Ran 1 shell command",
            "  ⎿  ok",
            "←renga-peers: ack from secretary",
            NEW_SPINNER_MS,
            "❯",
        )
        return base

    def test_timer_only_change_keeps_hash_invariant(self) -> None:
        p1 = self._mid_change_panes()
        p2 = _pane(
            "Ran 1 shell command",
            "  ⎿  ok",
            "←renga-peers: ack from secretary",
            NEW_SPINNER_HMS,  # different verb + elapsed + tokens, same everything else
            "❯",
        )
        h1 = ips.content_hash(ips.normalize_visible_lines(p1))
        h2 = ips.content_hash(ips.normalize_visible_lines(p2))
        self.assertEqual(h1, h2, "spinner glyph/verb/elapsed churn must not flip hash")

    def test_rotating_glyph_only_keeps_hash_invariant(self) -> None:
        p1 = _pane("✻ Puttering… (2m 5s · ↑ 1k tokens)")
        p2 = _pane("✺ Puttering… (2m 8s · ↑ 1k tokens)")
        self.assertEqual(
            ips.content_hash(ips.normalize_visible_lines(p1)),
            ips.content_hash(ips.normalize_visible_lines(p2)),
        )

    def test_old_form_spinner_timer_also_excluded(self) -> None:
        p1 = _pane("body line", "✻ Sautéed for 3m 0s")
        p2 = _pane("body line", "✻ Sautéed for 8m 40s")
        self.assertEqual(
            ips.content_hash(ips.normalize_visible_lines(p1)),
            ips.content_hash(ips.normalize_visible_lines(p2)),
        )

    def test_non_timer_parenthetical_flips_hash(self) -> None:
        # A churning prose line "Word… (attempt N)" has no elapsed timer, so it must NOT
        # be collapsed to the spinner placeholder — otherwise a real screen change hashes
        # stable and re-introduces the #680 STALL false positive. Hash must flip.
        p1 = _pane("Fetching… (attempt 1)", "❯")
        p2 = _pane("Fetching… (attempt 2)", "❯")
        self.assertNotEqual(
            ips.content_hash(ips.normalize_visible_lines(p1)),
            ips.content_hash(ips.normalize_visible_lines(p2)),
        )
        # and such a line does not parse as an active spinner (no elapsed).
        self.assertIsNone(ips.parse_new_spinner("Fetching… (attempt 1)"))

    def test_ansi_residue_does_not_affect_hash(self) -> None:
        # Major(d): ANSI escape residue must be stripped so it cannot churn the hash.
        p_ansi = _pane("\x1b[31mbody\x1b[0m", "❯")
        p_plain = _pane("body", "❯")
        self.assertEqual(
            ips.content_hash(ips.normalize_visible_lines(p_ansi)),
            ips.content_hash(ips.normalize_visible_lines(p_plain)),
        )

    def test_middle_row_change_flips_hash(self) -> None:
        p1 = self._mid_change_panes()
        p2 = _pane(
            "Ran 1 shell command",
            "  ⎿  ok",
            "Edited tools/foo.py (+12 -3)",  # scrollback moved: real activity
            NEW_SPINNER_MS,
            "❯",
        )
        self.assertNotEqual(
            ips.content_hash(ips.normalize_visible_lines(p1)),
            ips.content_hash(ips.normalize_visible_lines(p2)),
        )

    def test_trailing_blank_rows_ignored(self) -> None:
        p1 = _pane("body", "❯")
        p2 = _pane("body", "❯", "", "", "")
        self.assertEqual(
            ips.content_hash(ips.normalize_visible_lines(p1)),
            ips.content_hash(ips.normalize_visible_lines(p2)),
        )


class SuppressionTests(unittest.TestCase):
    """#671 suppression signal, including pin (5): cap-exceeded releases suppression."""

    def test_first_observation_suppresses(self) -> None:
        st = ips.extract_pane_state(_pane("body", NEW_SPINNER_MS))
        self.assertTrue(st.spinner_present)
        self.assertTrue(st.spinner_elapsed_increased)
        self.assertTrue(st.suppress_stall)
        self.assertFalse(st.cap_exceeded)

    def test_increasing_elapsed_suppresses(self) -> None:
        st = ips.extract_pane_state(
            _pane("body", "✻ Puttering… (5m 0s)"),
            prev_spinner_signature="Puttering",
            prev_spinner_elapsed_sec=120,
        )
        self.assertTrue(st.suppress_stall)

    def test_frozen_elapsed_does_not_suppress(self) -> None:
        # Same elapsed across a ~3 min cycle → spinner frozen → do not suppress.
        st = ips.extract_pane_state(
            _pane("body", "✻ Puttering… (5m 0s)"),
            prev_spinner_signature="Puttering",
            prev_spinner_elapsed_sec=300,
        )
        self.assertFalse(st.spinner_elapsed_increased)
        self.assertFalse(st.suppress_stall)

    def test_signature_change_counts_as_new_turn(self) -> None:
        # Different verb = new turn, elapsed reset lower — still active, suppress.
        st = ips.extract_pane_state(
            _pane("body", "✻ Puttering… (0m 3s)"),
            prev_spinner_signature="Gesticulating",
            prev_spinner_elapsed_sec=3600,
        )
        self.assertTrue(st.spinner_elapsed_increased)
        self.assertTrue(st.suppress_stall)

    def test_cap_exceeded_releases_suppression(self) -> None:
        # Pin (5): elapsed at / past the 90 min cap → no suppression, anomaly resumes.
        cap_line = "✻ Gesticulating… (1h 30m 0s · ↓ 200k tokens)"
        st = ips.extract_pane_state(
            _pane("body", cap_line),
            prev_spinner_signature="Gesticulating",
            prev_spinner_elapsed_sec=5000,
        )
        self.assertTrue(st.cap_exceeded)
        self.assertFalse(st.suppress_stall)

    def test_no_spinner_no_suppression(self) -> None:
        st = ips.extract_pane_state(_pane("body", "❯"))
        self.assertFalse(st.spinner_present)
        self.assertFalse(st.suppress_stall)
        self.assertIsNone(st.spinner_elapsed_sec)


class IdleTransitionTests(unittest.TestCase):
    """Deterministic Step 5 (b) idle-state transition, incl. Blocker 2 (pin 4)."""

    def _obs(self, *texts, **kw):
        return ips.extract_pane_state(_pane(*texts), **kw)

    def test_missing_hash_is_first_observation_reset(self) -> None:
        # Pin (4): a pre-#680 record (idle_streak accumulated on target-line logic,
        # no content hash) must reset — NOT carry the streak into the new logic.
        prev = {
            "last_target_line_text": "❯",
            "idle_streak_cycles": 13,
            "last_check_ts": "2026-07-04T00:00:00Z",
            "last_content_change_ts": "2026-07-03T23:40:00Z",
            "completion_reported_at": None,
        }
        obs = self._obs("body", "❯")
        new, decision = ips.compute_idle_transition(
            prev, obs, "2026-07-04T00:03:00Z"
        )
        self.assertEqual(decision["transition"], "first_observation")
        self.assertEqual(new["idle_streak_cycles"], 0)
        self.assertIsNone(new["last_content_change_ts"])
        self.assertEqual(new["last_visible_content_hash"], obs.content_hash)
        # deprecated field preserved but not consulted
        self.assertEqual(new["last_target_line_text"], "❯")

    def test_brand_new_worker_none_record(self) -> None:
        obs = self._obs("body")
        new, decision = ips.compute_idle_transition(None, obs, "2026-07-04T00:03:00Z")
        self.assertEqual(decision["transition"], "first_observation")
        self.assertEqual(new["idle_streak_cycles"], 0)

    def test_unchanged_hash_increments_streak(self) -> None:
        obs1 = self._obs("body", "❯")
        r1, _ = ips.compute_idle_transition(None, obs1, "2026-07-04T00:03:00Z")
        obs2 = self._obs("body", "❯")
        r2, d2 = ips.compute_idle_transition(r1, obs2, "2026-07-04T00:06:00Z")
        self.assertEqual(d2["transition"], "idle")
        self.assertEqual(r2["idle_streak_cycles"], 1)
        # held (still null from first observation)
        self.assertIsNone(r2["last_content_change_ts"])

    def test_idle_to_active_uses_previous_check_ts(self) -> None:
        # Build an idle streak, then a real content change.
        obs1 = self._obs("body", "❯")
        r1, _ = ips.compute_idle_transition(None, obs1, "2026-07-04T00:03:00Z")
        obs2 = self._obs("body", "❯")
        r2, _ = ips.compute_idle_transition(r1, obs2, "2026-07-04T00:06:00Z")
        self.assertEqual(r2["idle_streak_cycles"], 1)
        # cycle 3: scrollback moved
        obs3 = self._obs("Edited foo.py", "❯")
        r3, d3 = ips.compute_idle_transition(r2, obs3, "2026-07-04T00:09:00Z")
        self.assertEqual(d3["transition"], "active")
        self.assertEqual(r3["idle_streak_cycles"], 0)
        # last_content_change_ts = PREVIOUS last_check_ts (cycle 2), not now
        self.assertEqual(r3["last_content_change_ts"], "2026-07-04T00:06:00Z")

    def test_active_continuation_holds_start_ts(self) -> None:
        obs1 = self._obs("a", "❯")
        r1, _ = ips.compute_idle_transition(None, obs1, "2026-07-04T00:03:00Z")
        obs2 = self._obs("a", "❯")
        r2, _ = ips.compute_idle_transition(r1, obs2, "2026-07-04T00:06:00Z")  # idle, streak 1
        obs3 = self._obs("b", "❯")
        r3, d3 = ips.compute_idle_transition(r2, obs3, "2026-07-04T00:09:00Z")  # active
        self.assertEqual(d3["transition"], "active")
        self.assertEqual(r3["last_content_change_ts"], "2026-07-04T00:06:00Z")
        obs4 = self._obs("c", "❯")
        r4, d4 = ips.compute_idle_transition(r3, obs4, "2026-07-04T00:12:00Z")  # continuation
        self.assertEqual(d4["transition"], "active_continuation")
        # held from the active transition — START of the active span, not reset to now
        self.assertEqual(r4["last_content_change_ts"], r3["last_content_change_ts"])

    def test_spinner_fields_persisted_into_record(self) -> None:
        # m2 gap: the spinner-present branch (last_spinner_seen_ts = now, signature/elapsed
        # written) is what the dispatcher reads back next cycle as prev_spinner_*. Pin it.
        obs = ips.extract_pane_state(_pane("body", NEW_SPINNER_MS))  # Puttering, 125s
        new, _ = ips.compute_idle_transition(None, obs, "2026-07-04T00:03:00Z")
        self.assertEqual(new["last_spinner_signature"], "Puttering")
        self.assertEqual(new["last_spinner_elapsed_sec"], 2 * 60 + 5)
        self.assertEqual(new["last_spinner_seen_ts"], "2026-07-04T00:03:00Z")

    def test_spinner_absent_clears_prev_spinner_fields(self) -> None:
        prev = {
            "last_visible_content_hash": "deadbeef",
            "idle_streak_cycles": 1,
            "last_check_ts": "2026-07-04T00:00:00Z",
            "last_content_change_ts": "2026-07-04T00:00:00Z",
            "last_spinner_signature": "Puttering",
            "last_spinner_elapsed_sec": 300,
            "last_spinner_seen_ts": "2026-07-04T00:00:00Z",
        }
        obs = ips.extract_pane_state(_pane("body", "❯"))  # no spinner
        new, _ = ips.compute_idle_transition(prev, obs, "2026-07-04T00:03:00Z")
        self.assertIsNone(new["last_spinner_signature"])
        self.assertIsNone(new["last_spinner_elapsed_sec"])
        # seen_ts is retained (historical marker), not overwritten when spinner absent
        self.assertEqual(new["last_spinner_seen_ts"], "2026-07-04T00:00:00Z")

    def test_anomaly_fired_resets_streak_regardless_of_hash(self) -> None:
        # rule (3): an APPROVAL_BLOCKED/ERROR cycle resets the streak even when the hash
        # is unchanged, and re-baselines last_content_change_ts to the previous check ts.
        obs1 = self._obs("Allow this tool use? (y/n)", "❯")
        r1, _ = ips.compute_idle_transition(None, obs1, "2026-07-04T00:03:00Z")
        obs2 = self._obs("Allow this tool use? (y/n)", "❯")  # identical → hash unchanged
        r2, _ = ips.compute_idle_transition(r1, obs2, "2026-07-04T00:06:00Z")
        self.assertEqual(r2["idle_streak_cycles"], 1)  # would climb without anomaly
        obs3 = self._obs("Allow this tool use? (y/n)", "❯")  # still identical
        r3, d3 = ips.compute_idle_transition(
            r2, obs3, "2026-07-04T00:09:00Z", anomaly_fired=True
        )
        self.assertEqual(d3["transition"], "anomaly_reset")
        self.assertEqual(r3["idle_streak_cycles"], 0)
        self.assertEqual(r3["last_content_change_ts"], "2026-07-04T00:06:00Z")

    def test_completion_reported_at_preserved(self) -> None:
        prev = {
            "last_visible_content_hash": "deadbeef",
            "idle_streak_cycles": 1,
            "last_check_ts": "t1",
            "last_content_change_ts": "t0",
            "completion_reported_at": "2026-07-04T00:00:00Z",
        }
        obs = self._obs("body", "❯")  # hash differs from 'deadbeef' → active
        new, _ = ips.compute_idle_transition(prev, obs, "t2")
        self.assertEqual(new["completion_reported_at"], "2026-07-04T00:00:00Z")


class CliTests(unittest.TestCase):
    def _run(self, payload: dict, *extra: str):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *extra],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )

    def test_cli_emits_required_fields(self) -> None:
        payload = {"structuredContent": {"lines": _pane("body", NEW_SPINNER_MS)}}
        proc = self._run(payload)
        out = json.loads(proc.stdout)
        for key in (
            "content_hash",
            "normalized_lines",
            "spinner_present",
            "spinner_signature",
            "spinner_elapsed_sec",
            "suppress_stall",
            "cap_exceeded",
        ):
            self.assertIn(key, out)
        self.assertEqual(out["spinner_signature"], "Puttering")

    def test_cli_suppress_exit_4(self) -> None:
        payload = {"lines": _pane("body", NEW_SPINNER_MS)}
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 4)

    def test_cli_clean_pane_exit_0(self) -> None:
        payload = {"lines": _pane("body", "❯")}
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 0)

    def test_cli_prev_elapsed_frozen_exit_0(self) -> None:
        payload = {"lines": _pane("body", "✻ Puttering… (5m 0s)")}
        proc = self._run(
            payload,
            "--prev-spinner-signature",
            "Puttering",
            "--prev-spinner-elapsed-sec",
            "300",
        )
        self.assertEqual(proc.returncode, 0)  # frozen → no suppress

    def test_cli_null_prev_spinner_does_not_crash(self) -> None:
        # M1: worker-idle-state.json stores last_spinner_* as null on the common
        # no-spinner cycle; the prose substitutes the literal "null" / "". Must not abort.
        payload = {"lines": _pane("body", "❯")}
        for elapsed in ("null", "", "None"):
            with self.subTest(elapsed=elapsed):
                proc = self._run(
                    payload,
                    "--prev-spinner-signature",
                    "null",
                    "--prev-spinner-elapsed-sec",
                    elapsed,
                )
                self.assertEqual(proc.returncode, 0)
                self.assertIn("content_hash", json.loads(proc.stdout))

    def test_cli_record_mode_emits_record(self) -> None:
        # M2: --now-ts + --prev-record makes the CLI emit the record the prose writes
        # as-is (compute_idle_transition output), not just the observation.
        payload = {"lines": _pane("body", "❯")}
        prev = {
            "last_visible_content_hash": "not-the-current-hash",
            "idle_streak_cycles": 2,
            "last_check_ts": "2026-07-04T00:00:00Z",
            "last_content_change_ts": "2026-07-03T23:57:00Z",
            "completion_reported_at": None,
        }
        proc = self._run(
            payload, "--now-ts", "2026-07-04T00:03:00Z", "--prev-record", json.dumps(prev)
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertIn("observation", out)
        self.assertIn("record", out)
        self.assertIn("decision", out)
        # hash differs from prev → active transition, streak reset, cc_ts = prev check ts
        self.assertEqual(out["decision"]["transition"], "active")
        self.assertEqual(out["record"]["idle_streak_cycles"], 0)
        self.assertEqual(out["record"]["last_content_change_ts"], "2026-07-04T00:00:00Z")

    def test_cli_record_mode_missing_hash_first_observation(self) -> None:
        # Blocker 2 through the CLI: a prev record without last_visible_content_hash
        # resets rather than carrying the old streak.
        payload = {"lines": _pane("body", "❯")}
        prev = {"last_target_line_text": "❯", "idle_streak_cycles": 13}
        proc = self._run(
            payload, "--now-ts", "2026-07-04T00:03:00Z", "--prev-record", json.dumps(prev)
        )
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"]["transition"], "first_observation")
        self.assertEqual(out["record"]["idle_streak_cycles"], 0)
        self.assertIsNone(out["record"]["last_content_change_ts"])


if __name__ == "__main__":
    unittest.main()
