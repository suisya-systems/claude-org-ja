"""Layer 4 integration test for ``claude-org-runtime attention scan``.

Feeds a hand-curated fixture (``tests/fixtures/attention/``) into the
runtime CLI and asserts that the ``--dry-run --json`` output matches a
checked-in golden file. This is the drift-detection seam between ja's
``.state`` shape and the runtime classifier: if the runtime adds or
renames an urgent kind ja could plausibly emit, this test fails and
forces a deliberate vocabulary reconciliation on the ja side.

Design source: ``docs/design/attention-notification.md`` §8 (companion
to runtime PRs #19 + #20 — attention scan/watch CLI and template
overrides). The runtime CLI ships in ``claude-org-runtime>=0.1.10``;
on older runtimes that don't expose the ``attention`` subcommand the
test skips with a clear message instead of failing.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "attention"
STATE_EVENTS_JSON = FIXTURE_DIR / "state_events.json"
PENDING_JSON = FIXTURE_DIR / "pending_decisions.json"
GOLDEN_JSON = FIXTURE_DIR / "expected_scan.json"

# Mirrors the attention-relevant columns of tools/state_db/schema.sql.
# Projecting only what the runtime reader touches keeps a non-attention
# schema migration from breaking this test.
_EVENTS_SCHEMA = """
CREATE TABLE events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at  TEXT NOT NULL DEFAULT '2026-05-01T00:00:00Z',
  actor        TEXT,
  kind         TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
"""

# Dispatch metadata depends on the host OS notification backend
# (notify-send vs stdout vs PowerShell). The integration contract under
# test is the classifier output, not the delivery channel, so these
# fields are stripped before comparing against the golden.
_DISPATCH_ONLY_KEYS = frozenset({
    "desktop_dispatched", "bell_dispatched", "delivered",
})

# Drift canary: the set of urgent attention kinds the fixture is
# expected to surface. If the runtime stops classifying any of these
# as urgent, the assertion in
# test_all_expected_urgent_kinds_are_recognized fails and points at
# the gap.
_EXPECTED_URGENT_KINDS = frozenset({
    "approval_blocked",
    "ci_failed",
    "pending_decision",
    "user_reply_not_forwarded",
})


def _build_state_db(db_path: Path, events: list[dict]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_EVENTS_SCHEMA)
        for ev in events:
            conn.execute(
                "INSERT INTO events (occurred_at, actor, kind, payload_json)"
                " VALUES (?, ?, ?, ?)",
                (
                    ev["occurred_at"],
                    ev.get("actor"),
                    ev["kind"],
                    json.dumps(ev.get("payload", {})),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _normalize(events: list[dict]) -> list[dict]:
    """Strip env-dependent dispatch keys and sort by stable ``key``.

    The CLI emits events in a deterministic order (DB events by id ASC,
    then pending decisions in file order), but sorting both sides keeps
    the assertion message readable when only one event drifts.
    """
    return sorted(
        ({k: v for k, v in ev.items() if k not in _DISPATCH_ONLY_KEYS}
         for ev in events),
        key=lambda ev: ev["key"],
    )


def _runtime_has_attention() -> bool:
    """Probe for the ``attention`` subcommand on the installed runtime."""
    exe = shutil.which("claude-org-runtime")
    if exe is None:
        return False
    proc = subprocess.run(
        [exe, "attention", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode == 0


class AttentionRuntimeIntegrationTests(unittest.TestCase):
    """End-to-end check: ja fixture → runtime CLI → golden output."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _runtime_has_attention():
            raise unittest.SkipTest(
                "claude-org-runtime CLI without 'attention' subcommand; "
                "install claude-org-runtime>=0.1.10 to run this test"
            )
        cls.events_spec = json.loads(
            STATE_EVENTS_JSON.read_text(encoding="utf-8"),
        )
        cls.pending = json.loads(
            PENDING_JSON.read_text(encoding="utf-8"),
        )
        cls.golden = json.loads(
            GOLDEN_JSON.read_text(encoding="utf-8"),
        )

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.state_dir = Path(self._tmpdir.name) / ".state"
        self.state_dir.mkdir()
        _build_state_db(self.state_dir / "state.db", self.events_spec)
        (self.state_dir / "pending_decisions.json").write_text(
            json.dumps(self.pending, indent=2), encoding="utf-8",
        )

    def _run_scan(self) -> list[dict]:
        result = subprocess.run(
            [
                "claude-org-runtime", "attention", "scan",
                "--state-dir", str(self.state_dir),
                "--dry-run", "--json",
            ],
            check=False, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"attention scan exited with {result.returncode}; "
            f"stderr={result.stderr!r}",
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"attention scan stdout was not JSON: {exc}; "
                f"stdout={result.stdout!r}"
            )

    # ------------------------------------------------------------------
    # (1) Whole-fixture comparison against the golden.
    # ------------------------------------------------------------------
    def test_scan_output_matches_golden(self) -> None:
        events = self._run_scan()
        self.assertEqual(_normalize(events), _normalize(self.golden))

    # ------------------------------------------------------------------
    # (2) Each individual acceptance case from design §8 is present.
    # ------------------------------------------------------------------
    def test_notify_sent_approval_blocked_is_urgent(self) -> None:
        ev = self._find_event(self._run_scan(), key="event:2")
        self.assertEqual(ev["kind"], "approval_blocked")
        self.assertEqual(ev["severity"], "urgent")
        self.assertEqual(ev["task_id"], "T-approval")

    def test_ci_completed_failed_is_urgent(self) -> None:
        ev = self._find_event(self._run_scan(), key="event:4")
        self.assertEqual(ev["kind"], "ci_failed")
        self.assertEqual(ev["severity"], "urgent")
        self.assertEqual(ev["pr"], 42)
        self.assertEqual(ev["status"], "failed")

    def test_stale_pending_decision_is_urgent(self) -> None:
        ev = self._find_event(
            self._run_scan(),
            key="pending:T-pending-stale:pending_decision",
        )
        self.assertEqual(ev["kind"], "pending_decision")
        self.assertEqual(ev["severity"], "urgent")
        self.assertEqual(ev["task_id"], "T-pending-stale")

    def test_user_reply_not_forwarded_is_urgent(self) -> None:
        ev = self._find_event(
            self._run_scan(),
            key="pending:T-relay-gap:user_reply_not_forwarded",
        )
        self.assertEqual(ev["kind"], "user_reply_not_forwarded")
        self.assertEqual(ev["severity"], "urgent")

    # ------------------------------------------------------------------
    # (3) Progress-only and below-threshold cases are dropped.
    # ------------------------------------------------------------------
    def test_progress_only_events_are_filtered(self) -> None:
        keys = {ev["key"] for ev in self._run_scan()}
        # heartbeat (filtered by reader)
        self.assertNotIn("event:1", keys)
        # notify_sent with unrecognized subkind (filtered by classifier)
        self.assertNotIn("event:3", keys)
        # ci_completed status=success (filtered by classifier)
        self.assertNotIn("event:5", keys)
        # pending decision still inside the freshness window
        self.assertNotIn("pending:T-fresh:pending_decision", keys)

    # ------------------------------------------------------------------
    # (4) Drift canary — the runtime must still classify every kind the
    # ja fixture is built around as urgent.
    # ------------------------------------------------------------------
    def test_all_expected_urgent_kinds_are_recognized(self) -> None:
        urgent_kinds = {
            ev["kind"] for ev in self._run_scan()
            if ev.get("severity") == "urgent"
        }
        missing = _EXPECTED_URGENT_KINDS - urgent_kinds
        self.assertEqual(
            missing, set(),
            "runtime is no longer classifying "
            f"{sorted(missing)} as urgent; either the runtime vocabulary "
            "drifted or the ja fixture is stale — reconcile before merging."
        )

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------
    def _find_event(self, events: list[dict], *, key: str) -> dict:
        for ev in events:
            if ev.get("key") == key:
                return ev
        self.fail(
            f"event with key={key!r} not found in scan output; "
            f"got keys={[ev.get('key') for ev in events]!r}"
        )


if __name__ == "__main__":
    unittest.main()
