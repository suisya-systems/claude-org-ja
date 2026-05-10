"""Unit tests for the M4 state-DB query layer.

Run with:
    python -m unittest discover -s tools/state_db -p 'test_*.py'

Strategy: most tests use the M0 importer with the same synthetic fixture
that test_importer.py uses, so we exercise the real markdown → DB pipeline
that dashboard / org-* skills will read from in production. A couple of
tests bypass the importer to seed exact rows and pin down edge cases
(suspend lookup, lifecycle filter, null workstream).

Briefing-light coverage (``TestBriefingLight``) seeds intentionally heavy
rows — long ``resume_instructions``, multi-KB ``payload_json`` blobs,
both runs-attached and runs-orphaned ``archived``/``delete_pending``
worker_dirs — and asserts the light briefing API returns *none* of those
raw blobs. The size assertion locks in Issue #412's intent so a future
refactor that re-introduces a raw payload to the briefing fails loudly
instead of silently regressing the secretary's startup context cost.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.state_db import apply_schema, connect
from tools.state_db.importer import import_full_rebuild
from tools.state_db.queries import (
    ACTIVE_RESERVATION_STATUSES,
    BRIEFING_EVENT_KINDS_NOISE,
    TERMINAL_STATUSES,
    USER_VISIBLE_STATUSES,
    format_last_suspend_summary,
    format_session_brief,
    get_org_state_summary,
    get_resume_briefing,
    get_resume_briefing_light,
    get_run_by_task_id,
    list_active_runs,
    list_briefing_worker_dirs,
    list_recent_events,
    list_recent_events_for_briefing,
    list_reserved_runs,
    list_worker_dirs,
)
from tools.state_db.test_importer import _seed_claude_org_root


# ---------------------------------------------------------------------------
# Empty DB behaviour
# ---------------------------------------------------------------------------


class TestEmptyDB(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "empty.db"
        self.conn = connect(self.db_path)
        apply_schema(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_list_active_runs_empty(self):
        self.assertEqual(list_active_runs(self.conn), [])

    def test_list_reserved_runs_empty(self):
        self.assertEqual(list_reserved_runs(self.conn), [])

    def test_list_worker_dirs_empty(self):
        self.assertEqual(list_worker_dirs(self.conn), [])
        self.assertEqual(list_worker_dirs(self.conn, lifecycle="active"), [])

    def test_list_recent_events_empty(self):
        self.assertEqual(list_recent_events(self.conn), [])

    def test_get_run_by_task_id_missing(self):
        self.assertIsNone(get_run_by_task_id(self.conn, "no-such-task"))

    def test_get_org_state_summary_empty(self):
        s = get_org_state_summary(self.conn)
        self.assertEqual(s["active_runs"], [])
        self.assertEqual(s["reserved_runs"], [])
        self.assertEqual(s["active_worker_dirs"], [])
        self.assertEqual(s["recent_events"], [])
        self.assertEqual(s["run_status_counts"], {})
        self.assertEqual(s["totals"]["runs"], 0)
        self.assertEqual(s["totals"]["projects"], 0)
        self.assertEqual(s["totals"]["worker_dirs"], 0)

    def test_get_resume_briefing_empty(self):
        b = get_resume_briefing(self.conn)
        self.assertEqual(b["active_runs"], [])
        self.assertIsNone(b["last_event_at"])
        self.assertIsNone(b["last_suspend_at"])

    def test_get_resume_briefing_light_empty(self):
        b = get_resume_briefing_light(self.conn)
        self.assertIsNone(b["session"])
        self.assertEqual(b["active_runs"], [])
        self.assertEqual(b["reserved_runs"], [])
        self.assertEqual(b["active_inventory_dirs"], [])
        self.assertEqual(b["recent_events"], [])
        self.assertEqual(b["run_status_counts"], {})
        self.assertIsNone(b["last_event_at"])
        self.assertIsNone(b["last_event_kind"])
        self.assertIsNone(b["last_suspend_summary"])


# ---------------------------------------------------------------------------
# Imported-fixture behaviour
# ---------------------------------------------------------------------------


class TestSeededDB(unittest.TestCase):
    """End-to-end check: importer fixture → queries return expected shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name) / "claude-org"
        cls.db_path = Path(cls._td.name) / "state.db"
        _seed_claude_org_root(root)
        cls.summary = import_full_rebuild(cls.db_path, root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def setUp(self) -> None:
        self.conn = connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()

    def test_active_runs_match_in_use_and_review(self):
        # Fixture: sample-task-1 → completed (merged), sample-task-2 → review.
        active = list_active_runs(self.conn)
        task_ids = {r["task_id"] for r in active}
        self.assertIn("sample-task-2", task_ids)
        self.assertNotIn("sample-task-1", task_ids)
        # Joined project info populated.
        row = next(r for r in active if r["task_id"] == "sample-task-2")
        self.assertEqual(row["project_slug"], "renga")
        self.assertEqual(row["status"], "review")

    def test_get_run_by_task_id_returns_completed_too(self):
        # Lookup is not status-filtered.
        row = get_run_by_task_id(self.conn, "sample-task-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["project_slug"], "clock-app")

    def test_recent_events_ordered_desc_with_limit(self):
        all_events = list_recent_events(self.conn, limit=100)
        self.assertGreater(len(all_events), 0)
        ids = [e["id"] for e in all_events]
        self.assertEqual(ids, sorted(ids, reverse=True))

        capped = list_recent_events(self.conn, limit=2)
        self.assertEqual(len(capped), 2)
        # Same prefix as the un-capped result.
        self.assertEqual([e["id"] for e in capped], ids[:2])

    def test_summary_aggregates(self):
        s = get_org_state_summary(self.conn)
        self.assertGreater(s["totals"]["runs"], 0)
        self.assertGreater(s["totals"]["projects"], 0)
        self.assertGreater(len(s["recent_events"]), 0)
        # Status counts should sum to total runs.
        self.assertEqual(
            sum(s["run_status_counts"].values()), s["totals"]["runs"]
        )

    def test_resume_briefing_has_last_event(self):
        b = get_resume_briefing(self.conn)
        self.assertIsNotNone(b["last_event_at"])
        self.assertIsNotNone(b["last_event_kind"])


# ---------------------------------------------------------------------------
# Targeted edge cases (hand-seeded rows)
# ---------------------------------------------------------------------------


class TestEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "edge.db"
        self.conn = connect(self.db_path)
        apply_schema(self.conn)
        # One project, two worker_dirs (active + archived), one run with NULL
        # workstream_id, plus a 'suspend' event.
        self.conn.execute(
            "INSERT INTO projects (id, slug, display_name) VALUES (1, 'pj', 'pj')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (abs_path, layout, lifecycle) "
            "VALUES ('/w/active', 'flat', 'active')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (abs_path, layout, lifecycle) "
            "VALUES ('/w/old', 'flat', 'archived')"
        )
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status) "
            "VALUES ('t-null-ws', 1, 'B', 't-null-ws', 'in_use')"
        )
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES ('2026-04-01T00:00:00Z', 'secretary', 'suspend', "
            "'{\"reason\":\"end of day\"}')"
        )
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES ('2026-04-02T00:00:00Z', 'secretary', 'resume', '{}')"
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_lifecycle_filter(self):
        active = list_worker_dirs(self.conn, lifecycle="active")
        archived = list_worker_dirs(self.conn, lifecycle="archived")
        self.assertEqual([w["abs_path"] for w in active], ["/w/active"])
        self.assertEqual([w["abs_path"] for w in archived], ["/w/old"])
        self.assertEqual(len(list_worker_dirs(self.conn)), 2)

    def test_active_run_with_null_workstream(self):
        rows = list_active_runs(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_id"], "t-null-ws")
        self.assertIsNone(rows[0]["workstream_slug"])

    def test_resume_briefing_picks_latest_suspend(self):
        b = get_resume_briefing(self.conn)
        self.assertEqual(b["last_suspend_at"], "2026-04-01T00:00:00Z")
        self.assertEqual(b["last_suspend_actor"], "secretary")
        # Most recent event overall is the resume, not the suspend.
        self.assertEqual(b["last_event_kind"], "resume")
        # Payload survives as raw JSON text (caller decodes).
        self.assertEqual(json.loads(b["last_suspend_payload"])["reason"],
                         "end of day")

    def test_status_set_constants_match_contract(self):
        # Set F §3.5 — pin the four predicates so a future edit cannot
        # silently drift the resolver / dashboard / snapshotter contract.
        self.assertEqual(
            ACTIVE_RESERVATION_STATUSES, ("queued", "in_use", "review")
        )
        self.assertEqual(USER_VISIBLE_STATUSES, ("in_use", "review"))
        self.assertEqual(
            TERMINAL_STATUSES, ("completed", "failed", "abandoned")
        )

    def test_reserved_runs_returns_only_queued(self):
        # Seed one queued + one in_use + one completed and verify the
        # reserved query returns only the queued row (Set F §3.1 \\ §3.3).
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status) "
            "VALUES ('t-queued', 1, 'A', 't-queued', 'queued')"
        )
        self.conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status) "
            "VALUES ('t-done', 1, 'A', 't-done', 'completed')"
        )
        self.conn.commit()
        reserved = list_reserved_runs(self.conn)
        self.assertEqual([r["task_id"] for r in reserved], ["t-queued"])
        self.assertEqual(reserved[0]["status"], "queued")
        # And the user-visible projection (in_use / review only) excludes it.
        active_ids = {r["task_id"] for r in list_active_runs(self.conn)}
        self.assertNotIn("t-queued", active_ids)
        self.assertIn("t-null-ws", active_ids)  # the in_use seed row

    def test_recent_events_negative_limit_is_safe(self):
        # Defensive: a caller passing limit=-1 should not blow up.
        self.assertEqual(list_recent_events(self.conn, limit=-1), [])


# ---------------------------------------------------------------------------
# Briefing-light shape & weight (Issue #412)
# ---------------------------------------------------------------------------


# Sentinel substrings the briefing-light path MUST NOT echo back. The
# tail-padding length (320 chars) is chosen to comfortably exceed the
# briefing's per-field truncation cap (~120 chars) so a value that gets
# trimmed instead of dropped still fails the ``assertNotIn`` substring
# check. Picked to be unlikely to occur naturally so an inadvertent
# ``payload_json`` re-leak shows up as a single ``in`` hit.
_HUGE_PAYLOAD_SENTINEL = "RAW_PAYLOAD_SHOULD_NOT_LEAK_" + "X" * 320
_HUGE_INSTRUCTIONS_SENTINEL = (
    "RAW_RESUME_INSTRUCTIONS_SHOULD_NOT_LEAK_" + "Y" * 320
)


class TestBriefingLight(unittest.TestCase):
    """Pin Issue #412's invariants: no raw blobs, runs-scoped dirs, short tail."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "briefing.db"
        self.conn = connect(self.db_path)
        apply_schema(self.conn)

        # ---- session row with a multi-KB resume_instructions body ----
        long_resume_instructions = (
            "Resume guidance line 1\n"
            + (_HUGE_INSTRUCTIONS_SENTINEL + " " * 16) * 50
        )
        self.conn.execute(
            "INSERT INTO org_sessions "
            "(id, status, started_at, updated_at, suspended_at, resumed_at, "
            " objective, resume_instructions, last_writer_at) "
            "VALUES (1, 'SUSPENDED', '2026-05-01T00:00:00Z', "
            "'2026-05-09T00:00:00Z', '2026-05-09T00:00:00Z', NULL, "
            "'Ship the briefing-context-trim work', ?, "
            "'2026-05-09T00:00:00Z')",
            (long_resume_instructions,),
        )

        # ---- one project + four worker_dirs across lifecycles ----
        self.conn.execute(
            "INSERT INTO projects (id, slug, display_name) "
            "VALUES (1, 'pj', 'pj')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (id, abs_path, layout, lifecycle) "
            "VALUES (10, '/w/active-with-run', 'flat', 'active')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (id, abs_path, layout, lifecycle) "
            "VALUES (11, '/w/active-orphan', 'flat', 'active')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (id, abs_path, layout, lifecycle) "
            "VALUES (12, '/w/archived-with-completed-run', 'flat', "
            "'archived')"
        )
        self.conn.execute(
            "INSERT INTO worker_dirs (id, abs_path, layout, lifecycle) "
            "VALUES (13, '/w/delete-pending-with-run', 'flat', "
            "'delete_pending')"
        )

        # ---- runs spanning the live + terminal status set ----
        # run 1 → in_use, dir=10 (active). Should appear in
        # active_inventory_dirs.
        self.conn.execute(
            "INSERT INTO runs (id, task_id, project_id, pattern, title, "
            "status, worker_dir_id, dispatched_at) "
            "VALUES (1, 't-live', 1, 'B', 't-live', 'in_use', 10, "
            "'2026-05-09T01:00:00Z')"
        )
        # run 2 → completed, dir=12 (archived). Even though dir is
        # archived, the join is gated by runs.status NOT IN
        # ACTIVE_RESERVATION → must NOT appear in active_inventory_dirs.
        self.conn.execute(
            "INSERT INTO runs (id, task_id, project_id, pattern, title, "
            "status, worker_dir_id, dispatched_at) "
            "VALUES (2, 't-done', 1, 'A', 't-done', 'completed', 12, "
            "'2026-05-08T00:00:00Z')"
        )
        # run 3 → queued (reservation), dir=13 (delete_pending).
        # state-semantics-contract I7: lifecycle != runs.status — the
        # run is live so the dir MUST appear in active_inventory_dirs
        # despite delete_pending lifecycle.
        self.conn.execute(
            "INSERT INTO runs (id, task_id, project_id, pattern, title, "
            "status, worker_dir_id, dispatched_at) "
            "VALUES (3, 't-queued', 1, 'A', 't-queued', 'queued', 13, "
            "'2026-05-09T02:00:00Z')"
        )
        # dir 11 has no run pointing to it → should be excluded by the
        # runs-scoped briefing query (and the ``lifecycle='active'``
        # filter would have wrongly included it).

        # ---- ten heavy payload events (each ~3-4 KB of payload_json) ----
        big_summary = _HUGE_PAYLOAD_SENTINEL + ("Z" * 4096)
        for i in range(10):
            payload = json.dumps({
                "worker": f"worker-{i}",
                "task": f"t-{i}",
                "summary": big_summary,
            })
            self.conn.execute(
                "INSERT INTO events (occurred_at, actor, kind, payload_json) "
                "VALUES (?, 'secretary', 'worker_reported', ?)",
                (f"2026-05-09T00:00:{i:02d}Z", payload),
            )
        # noise event right before suspend → must be filtered out of the
        # briefing tail even though it would otherwise win on recency.
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES ('2026-05-09T00:00:50Z', 'dispatcher', "
            "'events_dropped', '{\"count\":12}')"
        )
        # latest suspend with a payload that names ~50 active workers
        # and ~80 pending items so we can assert counts pass through but
        # the raw lists do not.
        suspend_payload = json.dumps({
            "reason": _HUGE_PAYLOAD_SENTINEL + " end of day",
            "active_workers": [
                {"id": f"w-{i}", "task": f"t-{i}"} for i in range(50)
            ],
            "pending_items": [
                {"id": f"p-{i}", "note": "x" * 200} for i in range(80)
            ],
        })
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES ('2026-05-09T01:00:00Z', 'secretary', 'suspend', ?)",
            (suspend_payload,),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    # ---- raw-blob suppression -------------------------------------------------

    def test_briefing_light_excludes_raw_payload_and_resume_instructions(self):
        b = get_resume_briefing_light(self.conn)
        serialized = json.dumps(b, ensure_ascii=False, default=str)
        self.assertNotIn(_HUGE_PAYLOAD_SENTINEL, serialized)
        self.assertNotIn(_HUGE_INSTRUCTIONS_SENTINEL, serialized)
        # And the heavyweight API still does leak them — the contrast
        # documents *why* /org-resume Phase 1 picks the light variant.
        heavy_serialized = json.dumps(
            get_resume_briefing(self.conn), ensure_ascii=False, default=str
        )
        self.assertIn(_HUGE_PAYLOAD_SENTINEL, heavy_serialized)
        self.assertIn(_HUGE_INSTRUCTIONS_SENTINEL, heavy_serialized)

    def test_briefing_light_serialized_size_below_budget(self):
        # Sanity ceiling — not a token count, but a strong proxy. With 10
        # events at ~4 KB raw payload each + a ~5 KB resume_instructions
        # blob, the heavy payload is well over 50 KB. The light path must
        # come in under 8 KB even on this fixture.
        size = len(json.dumps(
            get_resume_briefing_light(self.conn),
            ensure_ascii=False, default=str,
        ))
        self.assertLess(size, 8_000)

    # ---- session_brief shape --------------------------------------------------

    def test_session_brief_omits_raw_resume_instructions(self):
        sb = format_session_brief({
            "status": "SUSPENDED",
            "objective": "ship it",
            "started_at": "2026-05-01T00:00:00Z",
            "updated_at": "2026-05-09T00:00:00Z",
            "suspended_at": "2026-05-09T00:00:00Z",
            "resumed_at": None,
            "resume_instructions": _HUGE_INSTRUCTIONS_SENTINEL * 10,
            "dispatcher_pane_id": "%1.0",
        })
        self.assertNotIn("resume_instructions", sb)
        self.assertNotIn("dispatcher_pane_id", sb)
        self.assertEqual(sb["status"], "SUSPENDED")
        self.assertEqual(sb["objective"], "ship it")
        self.assertTrue(sb["has_resume_instructions"])
        self.assertNotIn(_HUGE_INSTRUCTIONS_SENTINEL, sb["resume_summary"])

    def test_session_brief_none_passthrough(self):
        self.assertIsNone(format_session_brief(None))
        self.assertIsNone(format_session_brief({}))

    # ---- runs-scoped worker_dirs ---------------------------------------------

    def test_active_inventory_dirs_uses_runs_status_not_lifecycle(self):
        dirs = list_briefing_worker_dirs(self.conn)
        paths = sorted(d["abs_path"] for d in dirs)
        # live in_use run + queued run → both included regardless of
        # lifecycle (delete_pending pinned by I7).
        self.assertEqual(
            paths,
            ["/w/active-with-run", "/w/delete-pending-with-run"],
        )
        # Importantly, the lifecycle='active' shortcut would have
        # returned the orphan dir AND skipped the delete_pending live
        # one — which is exactly the bug Codex Major #4 flags.
        lifecycle_view = sorted(
            d["abs_path"]
            for d in list_worker_dirs(self.conn, lifecycle="active")
        )
        self.assertEqual(
            lifecycle_view,
            ["/w/active-orphan", "/w/active-with-run"],
        )

    # ---- recent_events tail ---------------------------------------------------

    def test_recent_events_for_briefing_filters_noise_and_caps_limit(self):
        events = list_recent_events_for_briefing(self.conn, limit=5)
        self.assertEqual(len(events), 5)
        kinds = {e["kind"] for e in events}
        self.assertNotIn("events_dropped", kinds)
        # Each row is event_summary shape — never raw payload_json.
        for e in events:
            self.assertNotIn("payload_json", e)
            self.assertIn("fields", e)
            for v in e["fields"].values():
                if isinstance(v, str):
                    self.assertNotIn(_HUGE_PAYLOAD_SENTINEL, v)

    def test_recent_events_for_briefing_unknown_kind_falls_back(self):
        self.conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES ('2026-05-09T03:00:00Z', 'secretary', "
            "'made_up_unknown_kind', '{\"foo\":\"bar\",\"big\":\"" + "x" * 4096
            + "\"}')"
        )
        self.conn.commit()
        events = list_recent_events_for_briefing(self.conn, limit=1)
        self.assertEqual(events[0]["kind"], "made_up_unknown_kind")
        # Fallback shape: kind/actor/occurred_at present, fields empty —
        # no raw blob accidentally extracted.
        self.assertEqual(events[0]["fields"], {})
        self.assertEqual(events[0]["actor"], "secretary")

    def test_recent_events_for_briefing_zero_limit_returns_empty(self):
        self.assertEqual(
            list_recent_events_for_briefing(self.conn, limit=0), []
        )
        self.assertEqual(
            list_recent_events_for_briefing(self.conn, limit=-3), []
        )

    # ---- last_suspend_summary -------------------------------------------------

    def test_last_suspend_summary_compresses_lists_to_counts(self):
        b = get_resume_briefing_light(self.conn)
        s = b["last_suspend_summary"]
        self.assertEqual(s["actor"], "secretary")
        self.assertEqual(s["active_workers_count"], 50)
        self.assertEqual(s["pending_items_count"], 80)
        # Reason is truncated, not a raw multi-KB blob.
        self.assertNotIn(_HUGE_PAYLOAD_SENTINEL, s["reason"])

    def test_format_last_suspend_summary_handles_missing_payload(self):
        s = format_last_suspend_summary({
            "occurred_at": "2026-05-09T01:00:00Z",
            "actor": "secretary",
            "payload_json": None,
        })
        self.assertEqual(s["occurred_at"], "2026-05-09T01:00:00Z")
        self.assertIsNone(s["reason"])
        self.assertIsNone(s["active_workers_count"])
        self.assertIsNone(s["pending_items_count"])

    def test_format_last_suspend_summary_handles_malformed_payload(self):
        s = format_last_suspend_summary({
            "occurred_at": "2026-05-09T01:00:00Z",
            "actor": "secretary",
            "payload_json": "{not valid json",
        })
        self.assertIsNone(s["reason"])
        self.assertIsNone(s["active_workers_count"])

    # ---- briefing-noise allowlist constant ------------------------------------

    def test_briefing_noise_constant_pins_known_noise_kinds(self):
        # Lock in the current noise list — silently extending it changes
        # what the secretary sees on cold-start, so a future drift should
        # land in this test as a deliberate diff.
        self.assertEqual(
            BRIEFING_EVENT_KINDS_NOISE,
            frozenset({"events_dropped", "secretary_identity_restored"}),
        )


if __name__ == "__main__":
    unittest.main()
