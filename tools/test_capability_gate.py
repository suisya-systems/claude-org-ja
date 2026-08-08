"""Unit tests for tools/capability_gate.py (Issue #848).

Run with:
  python3 -m unittest tools.test_capability_gate
  (from repo root, or add claude-org-ja to PYTHONPATH)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capability_gate as mod  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_db(path: Path, events: "list[tuple[str, dict]]") -> None:
    """Create a minimal events table and insert (kind, payload) rows."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE events ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  kind TEXT NOT NULL,"
            "  payload_json TEXT NOT NULL DEFAULT '{}'"
            "    CHECK (json_valid(payload_json)))"
        )
        for kind, payload in events:
            conn.execute(
                "INSERT INTO events (kind, payload_json) VALUES (?, ?)",
                (kind, json.dumps(payload)),
            )
        conn.commit()
    finally:
        conn.close()


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "state.db"

    def test_recorded_when_matching_event_present(self) -> None:
        _make_db(self.db, [("notify_sent", {"kind": "capability_first_drive",
                                            "note": "renga 2.0"})])
        status, detail = mod.lookup("capability_first_drive", self.db)
        self.assertEqual(status, mod.RECORDED)
        self.assertEqual(detail, "")

    def test_not_recorded_on_empty_events_table(self) -> None:
        _make_db(self.db, [])
        status, _ = mod.lookup("capability_first_drive", self.db)
        self.assertEqual(status, mod.NOT_RECORDED)

    def test_note_mentioning_the_gate_name_does_not_satisfy_it(self) -> None:
        """Regression: a substring test over payload_json would pass here.

        The event below is a *different* notification whose free-text note
        happens to name the gate. `payload_json LIKE '%capability_first_drive%'`
        matches it and marks the gate passed forever; the exact `$.kind`
        match must not.
        """
        _make_db(self.db, [
            ("notify_sent", {"kind": "awaiting_user",
                             "note": "pending capability_first_drive report"}),
        ])
        status, _ = mod.lookup("capability_first_drive", self.db)
        self.assertEqual(status, mod.NOT_RECORDED)

        # ... and prove the substring form really would have said otherwise.
        conn = sqlite3.connect(str(self.db))
        try:
            hit = conn.execute(
                "SELECT count(*) FROM events WHERE kind='notify_sent' "
                "AND payload_json LIKE '%capability_first_drive%'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(hit, 1, "the LIKE form should over-match; that is the bug")

    def test_non_notify_sent_event_does_not_satisfy_the_gate(self) -> None:
        _make_db(self.db, [("anomaly_observed",
                            {"kind": "capability_first_drive"})])
        status, _ = mod.lookup("capability_first_drive", self.db)
        self.assertEqual(status, mod.NOT_RECORDED)

    def test_gates_are_independent(self) -> None:
        """Passing first_drive must not discharge production_activation.

        The contract keeps the three gates separate and says satisfying one
        does not satisfy another; collapsing them here would collapse them
        operationally.
        """
        _make_db(self.db, [("notify_sent", {"kind": "capability_first_drive"})])
        self.assertEqual(mod.lookup("capability_first_drive", self.db)[0],
                         mod.RECORDED)
        self.assertEqual(mod.lookup("capability_production_activation", self.db)[0],
                         mod.NOT_RECORDED)
        self.assertEqual(mod.lookup("capability_first_drive_pending", self.db)[0],
                         mod.NOT_RECORDED)

    def test_pending_report_does_not_satisfy_the_approval(self) -> None:
        """The dedup marker is not an approval record."""
        _make_db(self.db, [
            ("notify_sent", {"kind": "capability_first_drive_pending"}),
        ])
        self.assertEqual(mod.lookup("capability_first_drive", self.db)[0],
                         mod.NOT_RECORDED)

    def test_missing_db_is_undetermined_not_not_recorded(self) -> None:
        status, detail = mod.lookup("capability_first_drive",
                                    Path(self._tmp.name) / "absent.db")
        self.assertEqual(status, mod.UNDETERMINED)
        self.assertIn("not found", detail)

    def test_db_without_events_table_is_undetermined(self) -> None:
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()
        status, detail = mod.lookup("capability_first_drive", self.db)
        self.assertEqual(status, mod.UNDETERMINED)
        self.assertIn("query failed", detail)

    def test_lookup_does_not_create_a_db_file(self) -> None:
        absent = Path(self._tmp.name) / "absent.db"
        mod.lookup("capability_first_drive", absent)
        self.assertFalse(absent.exists(),
                         "a missing DB must not be materialised by a read")


class GateTableTests(unittest.TestCase):
    def test_every_gate_maps_to_a_distinct_kind(self) -> None:
        self.assertEqual(len(set(mod.GATES.values())), len(mod.GATES))

    def test_exit_codes_cover_every_status(self) -> None:
        self.assertEqual(
            set(mod.EXIT_CODES),
            {mod.RECORDED, mod.NOT_RECORDED, mod.UNDETERMINED},
        )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "state.db"

    def _run(self, argv: "list[str]") -> "tuple[int, str]":
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mod.main(argv)
        return code, buf.getvalue()

    def test_single_gate_exit_codes(self) -> None:
        _make_db(self.db, [("notify_sent", {"kind": "capability_first_drive"})])
        code, out = self._run(["--gate", "first_drive", "--db-path", str(self.db)])
        self.assertEqual((code, out.strip()), (0, mod.RECORDED))

        code, out = self._run(
            ["--gate", "production_activation", "--db-path", str(self.db)])
        self.assertEqual((code, out.strip()), (1, mod.NOT_RECORDED))

        code, out = self._run(
            ["--gate", "first_drive",
             "--db-path", str(Path(self._tmp.name) / "absent.db")])
        self.assertEqual((code, out.strip()), (2, mod.UNDETERMINED))

    def test_all_gates_report_exits_zero(self) -> None:
        _make_db(self.db, [("notify_sent", {"kind": "capability_first_drive"})])
        code, out = self._run(["--db-path", str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn("first_drive: recorded", out)
        self.assertIn("production_activation: not_recorded", out)

    def test_json_output_shape(self) -> None:
        _make_db(self.db, [("notify_sent", {"kind": "capability_first_drive"})])
        _, out = self._run(
            ["--gate", "first_drive", "--json", "--db-path", str(self.db)])
        payload = json.loads(out)
        self.assertEqual(payload["gate"], "first_drive")
        self.assertEqual(payload["kind"], "capability_first_drive")
        self.assertEqual(payload["status"], mod.RECORDED)

    def test_help_is_ascii_only(self) -> None:
        """A cp932 console raises UnicodeEncodeError on non-ASCII --help text."""
        text = mod._build_parser().format_help()
        text.encode("ascii")


class CwdIndependenceTests(unittest.TestCase):
    """The dispatcher runs with cwd `.dispatcher/`; the lookup must still work.

    This is the defect the script exists to fix: an inline
    `python3 -c "from tools.state_db import ..."` raises ModuleNotFoundError
    from any cwd that is not the repo root.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "state.db"
        _make_db(self.db, [("notify_sent", {"kind": "capability_first_drive"})])

    def _run_from(self, cwd: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        # Strip any inherited PYTHONPATH/STATE_DB_PATH so the run proves the
        # script's own repo-root anchoring rather than the caller's env.
        env.pop("PYTHONPATH", None)
        env.pop("STATE_DB_PATH", None)
        return subprocess.run(
            [sys.executable, str(_REPO_ROOT / "tools" / "capability_gate.py"),
             "--gate", "first_drive", "--db-path", str(self.db)],
            cwd=str(cwd), env=env, capture_output=True, text=True,
        )

    def test_runs_from_repo_root(self) -> None:
        proc = self._run_from(_REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), mod.RECORDED)

    def test_runs_from_dispatcher_cwd(self) -> None:
        dispatcher = _REPO_ROOT / ".dispatcher"
        if not dispatcher.is_dir():
            self.skipTest(".dispatcher/ not present in this checkout")
        proc = self._run_from(dispatcher)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), mod.RECORDED)

    def test_runs_from_an_unrelated_cwd(self) -> None:
        proc = self._run_from(Path(self._tmp.name))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), mod.RECORDED)


if __name__ == "__main__":
    unittest.main()
