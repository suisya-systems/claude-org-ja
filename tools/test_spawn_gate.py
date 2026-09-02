"""Unit tests for tools/spawn_gate.py (dispatcher spawn-completion gate).

The gate exists because the spawn ceremony
(``.dispatcher/references/spawn-flow.md`` 3-3b approval Enter / 3-4
``list_peers`` wait / 3-5 instruction send) left no artifact, so a
dispatcher that skipped it produced a `.state/` trace identical to one
that performed it. The tests below pin the two properties that fix
depends on:

* ``verify`` refuses to emit a ``DELEGATE_COMPLETE`` body unless the
  supplied ``list_peers`` observation matches the worker dir the
  *secretary* recorded at T1 — the one input the dispatcher cannot
  produce by assertion.
* ``audit`` finds a ``worker_spawned`` with no ``worker_spawn_verified``,
  which is exactly the trace both 2026-08-18 incidents left behind.
"""

from __future__ import annotations

import json
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawn_gate  # noqa: E402

WORKER_DIR = "/tmp/org/workers/login-fix"
#: Seeded spawn time for the `verify` fixture. Must be in the past relative to the
#: real clock, because `verify` compares the verification it writes (stamped
#: `now` by SQLite) against the latest `worker_spawned`.
_PAST = "2000-01-01T00:00:00.000Z"
TASK = "login-fix"


def _make_db(path: Path) -> None:
    """A state.db with just enough schema for the gate's reads/writes."""
    from tools.state_db import apply_schema, connect

    conn = connect(path)
    try:
        apply_schema(conn)
    finally:
        conn.close()


def _seed_run(db: Path, task: str = TASK, worker_dir: str = WORKER_DIR) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO projects (slug, display_name) VALUES (?, ?)",
            ("proj", "Proj"),
        )
        project_id = conn.execute(
            "SELECT id FROM projects WHERE slug = 'proj'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO worker_dirs (abs_path, layout) VALUES (?, 'flat')",
            (worker_dir,),
        )
        dir_id = conn.execute(
            "SELECT id FROM worker_dirs WHERE abs_path = ?", (worker_dir,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO runs (task_id, project_id, pattern, title, status, "
            "worker_dir_id) VALUES (?, ?, 'A', ?, 'queued', ?)",
            (task, project_id, task, dir_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_event(db: Path, kind: str, payload: dict, occurred_at: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO events (occurred_at, actor, kind, payload_json) "
            "VALUES (?, 'dispatcher', ?, ?)",
            (occurred_at, kind, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def _run(
    argv: "list[str]", stderr: "io.StringIO | None" = None
) -> "tuple[int, dict]":
    """Run `main`, parse its stdout JSON. stderr is captured into `stderr`
    when given, otherwise swallowed (the worker-record side effect prints
    one line there whenever no `.state/workers/` record exists)."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(stderr or io.StringIO()):
        code = spawn_gate.main(argv)
    return code, json.loads(buf.getvalue())


class _VerifyFixture(unittest.TestCase):
    """Seeded state.db + `verify` argv shared by the `verify` test classes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "state.db"
        _make_db(self.db)
        _seed_run(self.db)
        _seed_event(
            self.db,
            "worker_spawned",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            _PAST,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _argv(self, **over) -> "list[str]":
        args = {
            "--task": TASK,
            "--pane-id": "5",
            "--peer-id": "5",
            "--peer-name": f"worker-{TASK}",
            "--peer-cwd": WORKER_DIR,
            "--approval": "sent",
            "--instruction": "send_message",
        }
        args.update(over)
        argv = ["--db-path", str(self.db), "verify"]
        for key, val in args.items():
            argv += [key, val]
        return argv


class VerifyTests(_VerifyFixture):
    def test_happy_path_records_event_and_emits_report(self) -> None:
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "verified")
        self.assertIn("DELEGATE_COMPLETE", out["delegate_complete"])
        self.assertIn("id=5", out["delegate_complete"])

        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM events WHERE kind = ?",
                (spawn_gate.VERIFIED_EVENT,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][0])
        self.assertEqual(payload["peer_id"], 5)
        self.assertEqual(payload["peer_cwd"], WORKER_DIR)

    def test_transport_comes_from_the_shared_resolver(self) -> None:
        """Not a literal: Epic #586 flipped DEFAULT_TRANSPORT to broker."""
        from tools.transport import resolve as _resolve

        _, out = _run(self._argv())
        self.assertEqual(out["recorded"]["transport"], str(_resolve()))

    def test_explicit_transport_wins(self) -> None:
        _, out = _run(self._argv(**{"--transport": "renga"}))
        self.assertEqual(out["recorded"]["transport"], "renga")

    def test_limitations_travel_with_the_evidence(self) -> None:
        """The attested/verified boundary must not live only in prose."""
        _, out = _run(self._argv())
        self.assertIn("機械検証", out["limitations"])

    def test_cwd_mismatch_blocks_the_report(self) -> None:
        """The one check the dispatcher cannot satisfy by asserting it."""
        code, out = _run(self._argv(**{"--peer-cwd": "/tmp/org/workers/other"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["status"], "gate_failed")
        self.assertIn("peer_cwd", out["failures"])
        self.assertNotIn("delegate_complete", out)

    def test_trailing_separator_is_not_a_mismatch(self) -> None:
        code, _ = _run(self._argv(**{"--peer-cwd": WORKER_DIR + "/"}))
        self.assertEqual(code, spawn_gate.EXIT_OK)

    def test_background_tab_requires_peer_id_to_equal_pane_id(self) -> None:
        """3-4b opens the background gate only on exact bound_pane_id match."""
        code, out = _run(
            self._argv(**{"--placement": "background_tab", "--peer-id": "9"})
        )
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("bound_pane_id", out["failures"])

    def test_background_tab_accepts_the_bound_pane_id(self) -> None:
        code, out = _run(self._argv(**{"--placement": "background_tab"}))
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["recorded"]["placement"], "background_tab")

    def test_same_tab_does_not_impose_id_equality(self) -> None:
        """broker peer ids need not equal pane ids on the default path."""
        code, _ = _run(self._argv(**{"--peer-id": "9"}))
        self.assertEqual(code, spawn_gate.EXIT_OK)

    def test_wrong_peer_name_blocks_the_report(self) -> None:
        code, out = _run(self._argv(**{"--peer-name": "worker-other"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("peer_name", out["failures"])

    def test_non_numeric_peer_id_blocks_the_report(self) -> None:
        """`list_peers` not yet showing the worker is the failure to catch."""
        code, out = _run(self._argv(**{"--peer-id": "unknown"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("peer_id", out["failures"])
        self.assertTrue(any("3-3b" in r for r in out["remedy"]))

    def test_gate_cannot_run_before_the_spawn_it_gates(self) -> None:
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("DELETE FROM events WHERE kind = 'worker_spawned'")
            conn.commit()
        finally:
            conn.close()
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("spawned_event", out["failures"])

    def test_unknown_task_is_blocked(self) -> None:
        code, out = _run(self._argv(**{"--task": "no-such-task"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("run_row", out["failures"])

    def test_runs_row_is_the_fallback_when_delegate_sent_is_absent(self) -> None:
        """No delegate_sent (older run): runs.worker_dir_id still serves."""
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["checks"]["expected_worker_dir_source"], "runs")

    def test_delegate_sent_is_preferred_over_the_mutable_runs_row(self) -> None:
        """Step 4's upsert_run can move runs.worker_dir_id; events cannot."""
        _seed_event(
            self.db,
            "delegate_sent",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            _PAST,
        )
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(
            out["checks"]["expected_worker_dir_source"], "delegate_sent"
        )

    def test_runs_row_moved_away_from_the_t1_value_is_a_failure(self) -> None:
        """The gate must not certify once its independent reference is gone."""
        _seed_event(
            self.db,
            "delegate_sent",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            _PAST,
        )
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO worker_dirs (abs_path, layout) VALUES (?, 'flat')",
                ("/tmp/org/workers/moved",),
            )
            conn.execute(
                "UPDATE runs SET worker_dir_id = "
                "(SELECT id FROM worker_dirs WHERE abs_path = ?)",
                ("/tmp/org/workers/moved",),
            )
            conn.commit()
        finally:
            conn.close()
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("worker_dir_divergence", out["failures"])

    def test_degraded_send_delivery_evidence_is_accepted(self) -> None:
        """spawn-flow 3-4 縮退: enumeration discarded, delivery is the probe."""
        code, out = _run(
            [
                "--db-path",
                str(self.db),
                "verify",
                "--task",
                TASK,
                "--pane-id",
                "5",
                "--evidence",
                "send_delivery",
                "--approval",
                "sent",
                "--instruction",
                "send_message",
            ]
        )
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertIn("DELEGATE_COMPLETE", out["delegate_complete"])
        self.assertIn("縮退", out["delegate_complete"])
        self.assertEqual(out["recorded"]["evidence"], "send_delivery")
        self.assertIsNone(out["recorded"]["peer_id"])

    def test_degraded_mode_rejects_a_quoted_peer_record(self) -> None:
        """A discarded enumeration must not ride in under the weaker mode."""
        code, out = _run(self._argv(**{"--evidence": "send_delivery"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertIn("evidence_mismatch", out["failures"])

    def test_redispatch_appends_a_second_verification(self) -> None:
        """A stale verification must not silently cover a later respawn."""
        self.assertEqual(_run(self._argv())[0], spawn_gate.EXIT_OK)
        _seed_event(
            self.db,
            "worker_spawned",
            {"task": TASK, "worker": f"worker-{TASK}", "dir": WORKER_DIR},
            "2099-01-01T00:00:00.000Z",
        )
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "verified")

        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?",
                (spawn_gate.VERIFIED_EVENT,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2)

    def test_rerun_is_idempotent(self) -> None:
        self.assertEqual(_run(self._argv())[0], spawn_gate.EXIT_OK)
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "already_verified")
        self.assertIn("DELEGATE_COMPLETE", out["delegate_complete"])

        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?",
                (spawn_gate.VERIFIED_EVENT,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


#: What the delegate-plan helper leaves in `.state/workers/` before spawn
#: (mirrors a real record: no Pane ID / Started, Status planned, and the
#: runner's "pane not yet spawned" bullet that the dashboard surfaces).
_PLANNED_MD = (
    f"# Worker: worker-{TASK}\n"
    f"Task: {TASK}\n"
    f"Directory: {WORKER_DIR}\n"
    f"Pane Name: worker-{TASK}\n"
    "Status: planned\n"
    "\n"
    "## Assignment\n"
    "Fix the login form.\n"
    "\n"
    "## Progress Log\n"
    "- [planned by dispatcher_runner] pane not yet spawned\n"
    "\n"
    "## Notes\n"
    "Status: keep this literal; it is prose, not the header field.\n"
)


class WorkerRecordSideEffectTests(_VerifyFixture):
    """spawn-flow Step 4 (b)/(c) are written by `verify`, not by hand.

    Observed 2026-09-03: the dispatcher flipped `Status` only, so the
    dashboard showed every live worker as "pane not yet spawned". The
    gate is the one step a dispatch cannot skip, so the record is written
    there. The exit-code / event contract of `verify` must not move.
    """

    def setUp(self) -> None:
        super().setUp()
        self.workers = self.db.parent / "workers"
        self.workers.mkdir()
        self.md = self.workers / f"worker-{TASK}.md"

    def _lines(self) -> "list[str]":
        return self.md.read_text(encoding="utf-8").split("\n")

    def test_planned_record_becomes_active_with_pane_and_started(self) -> None:
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "verified")
        self.assertEqual(out["worker_record"]["status"], "updated")
        self.assertEqual(
            out["worker_record"]["changes"],
            ["pane_id", "started", "status", "progress_log"],
        )

        lines = self._lines()
        # Header fields land right after `Pane Name:`; Started comes from
        # the seeded `worker_spawned.occurred_at`, not the wall clock.
        i = lines.index(f"Pane Name: worker-{TASK}")
        self.assertEqual(lines[i + 1], "Pane ID: 5")
        self.assertEqual(lines[i + 2], f"Started: {_PAST}")
        self.assertEqual(lines[i + 3], "Status: active")
        # The bullet is appended inside `## Progress Log`, before `## Notes`.
        log = lines.index("## Progress Log")
        notes = lines.index("## Notes")
        self.assertEqual(
            lines[log + 1 : notes],
            [
                "- [planned by dispatcher_runner] pane not yet spawned",
                f"- [{_PAST}] 派遣完了、作業開始（pane id=5）",
                "",
            ],
        )
        # The prose `Status:` under `## Notes` is not the header field.
        self.assertEqual(
            lines[notes + 1],
            "Status: keep this literal; it is prose, not the header field.",
        )
        # Nothing else moved.
        self.assertEqual(lines[0], f"# Worker: worker-{TASK}")
        self.assertIn("Fix the login form.", lines)
        self.assertEqual(lines[-1], "")

    def test_second_verify_is_idempotent(self) -> None:
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        _run(self._argv())
        first = self.md.read_text(encoding="utf-8")
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "already_verified")
        self.assertEqual(out["worker_record"]["status"], "unchanged")
        self.assertEqual(out["worker_record"]["changes"], [])
        self.assertEqual(self.md.read_text(encoding="utf-8"), first)
        self.assertEqual(first.count("派遣完了"), 1)
        self.assertEqual(first.count("Pane ID:"), 1)

    def test_existing_fields_are_kept_not_overwritten(self) -> None:
        text = _PLANNED_MD.replace(
            "Status: planned\n",
            "Pane ID: 77\nStarted: 1999-12-31T23:59:59.000Z\nStatus: active\n",
        ).replace(
            "- [planned by dispatcher_runner] pane not yet spawned\n",
            "- [1999-12-31T23:59:59.000Z] 派遣完了、作業開始\n",
        )
        self.md.write_text(text, encoding="utf-8")
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["worker_record"]["status"], "unchanged")
        self.assertEqual(self.md.read_text(encoding="utf-8"), text)

    def test_crlf_and_missing_trailing_newline_are_preserved(self) -> None:
        text = _PLANNED_MD.replace("\n", "\r\n").rstrip("\r\n")
        self.md.write_bytes(text.encode("utf-8"))
        code, out = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["worker_record"]["status"], "updated")
        raw = self.md.read_bytes().decode("utf-8")
        self.assertNotIn("\n", raw.replace("\r\n", ""))  # only CRLF endings
        self.assertFalse(raw.endswith("\n"))
        self.assertIn("\r\nPane ID: 5\r\nStarted: ", raw)
        self.assertIn("派遣完了", raw)

    def test_marker_in_prose_does_not_suppress_the_bullet(self) -> None:
        """Idempotency is keyed on a bullet inside `## Progress Log`, not
        on the phrase appearing anywhere in the file."""
        text = _PLANNED_MD.replace(
            "Fix the login form.", "Fix the login form. 派遣完了後に着手。"
        )
        self.md.write_text(text, encoding="utf-8")
        _, out = _run(self._argv())
        self.assertIn("progress_log", out["worker_record"]["changes"])
        lines = self._lines()
        log = lines.index("## Progress Log")
        self.assertIn("派遣完了、作業開始", lines[log + 2])

    def test_lookalike_and_indented_headings(self) -> None:
        """`## Progress Logger` is not the section; an indented `## ` still
        ends the header and the section."""
        text = (
            f"# Worker: worker-{TASK}\n"
            "Status: planned\n"
            "\n"
            "## Progress Logger\n"
            "Pane ID: not-a-header\n"
            "\n"
            "  ## Progress Log\n"
            "- [x] first\n"
            "\n"
            "  ## Notes\n"
            "n\n"
        )
        self.md.write_text(text, encoding="utf-8")
        code, _ = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(
            self._lines(),
            [
                f"# Worker: worker-{TASK}",
                "Pane ID: 5",
                f"Started: {_PAST}",
                "Status: active",
                "",
                "## Progress Logger",
                "Pane ID: not-a-header",
                "",
                "  ## Progress Log",
                "- [x] first",
                f"- [{_PAST}] 派遣完了、作業開始（pane id=5）",
                "",
                "  ## Notes",
                "n",
                "",
            ],
        )

    def test_indented_existing_headers_count_as_present(self) -> None:
        text = _PLANNED_MD.replace(
            "Status: planned\n",
            "  Pane ID: 77\n  Started: 1999-12-31T23:59:59.000Z\nStatus: planned\n",
        )
        self.md.write_text(text, encoding="utf-8")
        _, out = _run(self._argv())
        self.assertEqual(out["worker_record"]["changes"], ["status", "progress_log"])
        raw = self.md.read_text(encoding="utf-8")
        self.assertEqual(raw.count("Pane ID:"), 1)
        self.assertEqual(raw.count("Started:"), 1)

    def test_file_mode_survives_the_atomic_replace(self) -> None:
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        self.md.chmod(0o664)
        _run(self._argv())
        self.assertEqual(self.md.stat().st_mode & 0o777, 0o664)

    def test_fenced_headings_do_not_end_the_section(self) -> None:
        """A `## ` quoted inside a code fence is not a heading, so the
        bullet lands after the fence, not inside it."""
        text = _PLANNED_MD.replace(
            "- [planned by dispatcher_runner] pane not yet spawned\n",
            "- [planned by dispatcher_runner] pane not yet spawned\n"
            "  ```markdown\n"
            "  ## Notes\n"
            "  - 派遣完了 (example, not a bullet)\n"
            "  ```\n",
        )
        self.md.write_text(text, encoding="utf-8")
        _, out = _run(self._argv())
        self.assertIn("progress_log", out["worker_record"]["changes"])
        lines = self._lines()
        fence_close = lines.index("  ```")
        self.assertIn("派遣完了、作業開始", lines[fence_close + 1])
        self.assertEqual(lines[fence_close + 2], "")
        self.assertEqual(lines[fence_close + 3], "## Notes")
        # And a second run sees that bullet, not the fenced example.
        _, out = _run(self._argv())
        self.assertEqual(out["worker_record"]["status"], "unchanged")

    def test_fence_closing_follows_commonmark_lengths(self) -> None:
        """A ```` fence is not closed by ``` (nor by ```py), so everything
        up to the real closer stays fenced."""
        text = _PLANNED_MD.replace(
            "- [planned by dispatcher_runner] pane not yet spawned\n",
            "- [planned by dispatcher_runner] pane not yet spawned\n"
            "````\n"
            "```\n"
            "```md\n"
            "## Notes\n"
            "- 派遣完了 (still fenced)\n"
            "````\n",
        )
        self.md.write_text(text, encoding="utf-8")
        _, out = _run(self._argv())
        self.assertIn("progress_log", out["worker_record"]["changes"])
        lines = self._lines()
        closer = len(lines) - 1 - lines[::-1].index("````")
        self.assertIn("派遣完了、作業開始", lines[closer + 1])
        self.assertEqual(lines[closer + 3], "## Notes")

    def test_concurrent_append_is_not_lost(self) -> None:
        """A write that lands between read and replace is re-applied on
        top of the fresh content instead of being overwritten."""
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        original = spawn_gate._rewrite_worker_md
        calls = {"n": 0}

        def racing(text, pane_id, started_at):
            calls["n"] += 1
            if calls["n"] == 1:
                # Secretary appends while we hold the stale copy.
                with self.md.open("a", encoding="utf-8") as fh:
                    fh.write("- [x] secretary note\n")
            return original(text, pane_id, started_at)

        spawn_gate._rewrite_worker_md = racing
        try:
            code, out = _run(self._argv())
        finally:
            spawn_gate._rewrite_worker_md = original
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["worker_record"]["status"], "updated")
        self.assertEqual(calls["n"], 2)
        raw = self.md.read_text(encoding="utf-8")
        self.assertIn("- [x] secretary note", raw)
        self.assertEqual(raw.count("派遣完了"), 1)

    def test_record_that_keeps_moving_is_reported_not_clobbered(self) -> None:
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        original = spawn_gate._rewrite_worker_md
        calls = {"n": 0}

        def always_racing(text, pane_id, started_at):
            calls["n"] += 1
            with self.md.open("a", encoding="utf-8") as fh:
                fh.write(f"- [x] note {calls['n']}\n")
            return original(text, pane_id, started_at)

        spawn_gate._rewrite_worker_md = always_racing
        try:
            err = io.StringIO()
            code, out = _run(self._argv(), stderr=err)
        finally:
            spawn_gate._rewrite_worker_md = original
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["worker_record"]["status"], "error")
        self.assertIn("concurrently", err.getvalue())
        raw = self.md.read_text(encoding="utf-8")
        self.assertNotIn("派遣完了", raw)
        self.assertEqual(raw.count("- [x] note"), spawn_gate._WORKER_MD_ATTEMPTS)

    def test_status_flip_rewrites_only_the_value(self) -> None:
        text = _PLANNED_MD.replace("Status: planned\n", "  STATUS:  planned  \n")
        self.md.write_text(text, encoding="utf-8")
        _run(self._argv())
        self.assertIn("  STATUS:  active  ", self._lines())

    def test_failed_write_keeps_the_original_record(self) -> None:
        """Atomic replace: a write that dies mid-way must not truncate."""
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        original = spawn_gate._replace_file_atomically

        def boom(path, data):
            raise OSError("disk full")

        spawn_gate._replace_file_atomically = boom
        try:
            err = io.StringIO()
            code, out = _run(self._argv(), stderr=err)
        finally:
            spawn_gate._replace_file_atomically = original
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["worker_record"]["status"], "error")
        self.assertEqual(self.md.read_text(encoding="utf-8"), _PLANNED_MD)
        # No temp file left behind beside the record.
        self.assertEqual(sorted(p.name for p in self.workers.iterdir()), [self.md.name])

    def test_missing_record_does_not_fail_the_gate(self) -> None:
        err = io.StringIO()
        code, out = _run(self._argv(), stderr=err)
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "verified")
        self.assertIn("DELEGATE_COMPLETE", out["delegate_complete"])
        self.assertEqual(out["worker_record"]["status"], "missing")
        stderr_lines = [ln for ln in err.getvalue().splitlines() if ln]
        self.assertEqual(len(stderr_lines), 1)
        self.assertIn(str(self.md), stderr_lines[0])
        # The event was still recorded: the side effect is downstream of it.
        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?",
                (spawn_gate.VERIFIED_EVENT,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_write_error_does_not_fail_the_gate(self) -> None:
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        original = spawn_gate._record_dispatch_in_worker_md

        def boom(*_a, **_k):
            raise OSError("disk full")

        spawn_gate._record_dispatch_in_worker_md = boom
        try:
            err = io.StringIO()
            code, out = _run(self._argv(), stderr=err)
        finally:
            spawn_gate._record_dispatch_in_worker_md = original
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["worker_record"]["status"], "error")
        self.assertIn("disk full", err.getvalue())

    def test_gate_failure_leaves_the_record_untouched(self) -> None:
        """No event, no side effect: the md must not claim a dispatch that
        the gate refused to certify."""
        self.md.write_text(_PLANNED_MD, encoding="utf-8")
        code, out = _run(self._argv(**{"--peer-cwd": "/tmp/org/workers/other"}))
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertNotIn("worker_record", out)
        self.assertEqual(self.md.read_text(encoding="utf-8"), _PLANNED_MD)

    def test_record_without_progress_log_gets_one(self) -> None:
        self.md.write_text(
            f"# Worker: worker-{TASK}\nTask: {TASK}\nStatus: planned\n",
            encoding="utf-8",
        )
        code, _ = _run(self._argv())
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(
            self._lines(),
            [
                f"# Worker: worker-{TASK}",
                f"Task: {TASK}",
                "Pane ID: 5",
                f"Started: {_PAST}",
                "Status: active",
                "",
                "## Progress Log",
                f"- [{_PAST}] 派遣完了、作業開始（pane id=5）",
                "",
            ],
        )


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "state.db"
        _make_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _audit(self, *extra: str) -> "tuple[int, dict]":
        # Tests seed fixed timestamps around GATE_EPOCH, so the default cutoff
        # would filter some of them out; disable it unless a case sets it.
        argv = ["--db-path", str(self.db), "audit", *extra]
        if "--since" not in extra:
            argv += ["--since", ""]
        return _run(argv)

    def test_clean_when_every_spawn_is_verified(self) -> None:
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T00:00:00.000Z"
        )
        _seed_event(
            self.db,
            spawn_gate.VERIFIED_EVENT,
            {"task": TASK},
            "2026-08-18T00:00:30.000Z",
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["status"], "clean")

    def test_the_2026_08_18_trace_is_a_finding(self) -> None:
        """delegate_sent -> worker_spawned -> nothing: exactly the incident."""
        _seed_event(
            self.db, "delegate_sent", {"task": TASK}, "2026-08-17T17:05:43.236Z"
        )
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-17T17:07:33.126Z"
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["finding_count"], 1)
        self.assertEqual(out["findings"][0]["task"], TASK)
        self.assertIsNone(out["findings"][0]["last_verified_at"])

    def test_grace_window_suppresses_an_in_flight_ceremony(self) -> None:
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        _seed_event(
            self.db,
            "worker_spawned",
            {"task": TASK},
            now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        self.assertEqual(self._audit("--older-than-min", "5")[0], spawn_gate.EXIT_OK)
        self.assertEqual(self._audit("--older-than-min", "0")[0], spawn_gate.EXIT_FIRE)

    def test_redispatch_needs_a_fresh_verification(self) -> None:
        """A stale verification must not cover a later re-spawn."""
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T00:00:00.000Z"
        )
        _seed_event(
            self.db,
            spawn_gate.VERIFIED_EVENT,
            {"task": TASK},
            "2026-08-18T00:00:30.000Z",
        )
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T02:00:00.000Z"
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["findings"][0]["last_verified_at"], "2026-08-18T00:00:30.000Z")

    def test_gate_epoch_excludes_spawns_that_predate_the_gate(self) -> None:
        """383 pre-deployment spawns must not drown the live signal."""
        _seed_event(
            self.db, "worker_spawned", {"task": "ancient"}, "2026-05-11T07:40:30.206Z"
        )
        code, out = self._audit(
            "--older-than-min", "0", "--since", spawn_gate.GATE_EPOCH
        )
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["skipped"]["before_gate_epoch"], 1)

    def test_an_old_unverified_spawn_is_never_forgotten(self) -> None:
        """A rolling window would drop this once it aged past 24h."""
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-19T00:00:00.000Z"
        )
        code, out = self._audit("--older-than-min", "0", "--since", spawn_gate.GATE_EPOCH)
        self.assertEqual(code, spawn_gate.EXIT_FIRE)
        self.assertEqual(out["finding_count"], 1)

    def test_completed_run_is_not_a_finding(self) -> None:
        """A run that finished is no longer actionable, and is counted."""
        _seed_run(self.db)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE runs SET status = 'completed'")
            conn.commit()
        finally:
            conn.close()
        _seed_event(
            self.db, "worker_spawned", {"task": TASK}, "2026-08-18T00:00:00.000Z"
        )
        code, out = self._audit("--older-than-min", "0")
        self.assertEqual(code, spawn_gate.EXIT_OK)
        self.assertEqual(out["skipped"]["terminal_run"], 1)


if __name__ == "__main__":
    unittest.main()
