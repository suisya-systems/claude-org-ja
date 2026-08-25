"""Unit tests for tools/self_edit_approval.py.

The tool exists because the manual three-call approval handshake failed
*silently* when the final Enter was dropped: the approval text sat in the
worker's composer, which looks identical to "delivered" under
``inspect_pane``, the worker waited without having received anything, and
nothing was written anywhere. The tests below pin the properties that fix
depends on:

* the approval text is assembled by the tool and carries all three
  mandatory elements, so it cannot be composed short;
* each stage that cannot be verified exits non-zero rather than
  reporting success - in particular the "text never landed" and "Enter
  did not submit" paths, the latter being the exact 2026-07-31 /
  2026-08-25 failure;
* ``audit`` finds a self-edit dispatch with no ``self_edit_approval_sent``,
  which is the trace an omitted handshake leaves behind.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import self_edit_approval as sea  # noqa: E402

TASK = "self-edit-demo"
ROOT = "/home/u/work/org/claude-org-ja"
SELF_EDIT_DIR = f"{ROOT}/.worktrees/{TASK}"
OTHER_DIR = "/home/u/work/org/workers/other-project/.worktrees/other"
_PAST = "2026-08-25T01:00:00.000Z"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fence() -> dict:
    return {"row": 0, "text": "─" * 60}


def _screen(composer: str) -> "list[dict]":
    """A screen whose composer holds ``composer`` (wrapped over 2 rows).

    Mirrors what a live ``renga inspect`` returned: the prompt is
    followed by NBSP and interior spaces are dropped by the renderer.
    """
    rendered = composer.replace(" ", "")
    head, tail = rendered[:40], rendered[40:]
    rows = [
        {"row": 1, "text": "● an earlier assistant line"},
        {"row": 2, "text": "> an echoed transcript line, not the composer"},
        _fence(),
        {"row": 4, "text": "❯ " + head},
    ]
    if tail:
        rows.append({"row": 5, "text": tail})
    rows.append(_fence())
    rows.append({"row": 7, "text": "  auto mode on (shift+tab to cycle)"})
    return rows


class FakeBackend:
    """Scripted pane: each inspect pops the next screen in ``screens``."""

    name = "renga"

    def __init__(self, screens, fail_on=None):
        self.screens = list(screens)
        self.fail_on = fail_on
        self.calls = []

    def describe(self):
        return "fake"

    def send_text(self, text):
        self.calls.append(("text", text))
        if self.fail_on == "text":
            raise sea.BackendFailure("boom")

    def send_enter(self):
        self.calls.append(("enter", None))
        if self.fail_on == "enter":
            raise sea.BackendFailure("boom")

    def inspect(self, lines):
        self.calls.append(("inspect", lines))
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]


def _make_db(path: Path) -> None:
    from tools.state_db import apply_schema, connect

    conn = connect(path)
    try:
        apply_schema(conn)
    finally:
        conn.close()


def _seed_run(db: Path, task: str = TASK, status: str = "in_use") -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT OR IGNORE INTO projects (slug, display_name) "
                     "VALUES ('proj', 'Proj')")
        pid = conn.execute("SELECT id FROM projects WHERE slug='proj'").fetchone()[0]
        conn.execute("INSERT INTO runs (task_id, project_id, pattern, title, status) "
                     "VALUES (?, ?, 'B', ?, ?)", (task, pid, task, status))
        conn.commit()
    finally:
        conn.close()


def _seed_event(db: Path, kind: str, payload: dict, occurred_at: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO events (kind, actor, occurred_at, payload_json) "
                     "VALUES (?, 'secretary', ?, ?)",
                     (kind, occurred_at, json.dumps(payload)))
        conn.commit()
    finally:
        conn.close()


def _run(argv) -> "tuple[int, dict]":
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = sea.main(argv)
    return code, json.loads(buf.getvalue())


# ---------------------------------------------------------------------------
# approval text
# ---------------------------------------------------------------------------

class ApprovalTextTests(unittest.TestCase):
    def test_carries_all_three_mandatory_elements(self):
        text = sea.build_approval_text(TASK, [".claude/a.md", ".claude/b.md"])
        self.assertIn(TASK, text)                      # task_id
        self.assertIn(".claude/a.md", text)            # file enumeration
        self.assertIn(".claude/b.md", text)
        self.assertIn("窓口経由のユーザー承認", text)   # relayed-user-approval wording

    def test_is_single_line(self):
        # A literal newline is a mid-string submit in the composer, which
        # would submit a truncated approval.
        text = sea.build_approval_text(TASK, [".claude/a.md"])
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)


# ---------------------------------------------------------------------------
# composer parsing
# ---------------------------------------------------------------------------

class ComposerTests(unittest.TestCase):
    def test_reads_wrapped_text_and_ignores_transcript_echo(self):
        msg = sea.build_approval_text(TASK, [".claude/skills/x/SKILL.md.in"])
        got = sea.extract_composer(_screen(msg))
        self.assertIn(sea._norm(msg), sea._norm(got))

    def test_empty_composer_reads_empty(self):
        rows = [_fence(), {"row": 2, "text": "❯"}, _fence()]
        self.assertEqual(sea._norm(sea.extract_composer(rows) or ""), "")

    def test_norm_is_whitespace_and_nbsp_insensitive(self):
        # The renderer drops interior spaces and inserts NBSP after the
        # prompt, so comparison must survive both.
        self.assertEqual(sea._norm("a b c"), sea._norm("abc"))

    def test_no_prompt_row_returns_none(self):
        self.assertIsNone(sea.extract_composer([{"row": 1, "text": "no box here"}]))


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

class SendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "state.db"
        _make_db(self.db)
        self._real = sea.make_backend
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: setattr(sea, "make_backend", self._real))

    def _install(self, backend):
        sea.make_backend = lambda args, transport: backend

    def _argv(self, *extra):
        return ["--db-path", str(self.db), "send", "--task", TASK,
                "--file", ".claude/skills/x/SKILL.md.in",
                "--verify-timeout", "0", "--poll-interval", "0", *extra]

    def _approval(self):
        return sea.build_approval_text(TASK, [".claude/skills/x/SKILL.md.in"])

    def _events(self):
        conn = sqlite3.connect(self.db)
        try:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM events WHERE kind = ?",
                (sea.APPROVAL_EVENT,))]
        finally:
            conn.close()

    def test_happy_path_records_event(self):
        empty = [_fence(), {"row": 2, "text": "❯"}, _fence()]
        backend = FakeBackend([empty, _screen(self._approval()), empty])
        self._install(backend)
        code, out = _run(self._argv())
        self.assertEqual(code, sea.EXIT_OK, out)
        self.assertEqual(out["status"], "approved")
        self.assertTrue(out["approval_delivered"])
        # text and Enter were separate writes, in that order.
        writes = [c[0] for c in backend.calls if c[0] in ("text", "enter")]
        self.assertEqual(writes, ["text", "enter"])
        rows = self._events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task"], TASK)
        self.assertEqual(rows[0]["files"], [".claude/skills/x/SKILL.md.in"])
        self.assertIn("verified_at", rows[0])
        self.assertIn("pane", rows[0])

    def test_text_that_never_lands_fails_loud(self):
        empty = [_fence(), {"row": 2, "text": "❯"}, _fence()]
        # Composer stays empty after the write: nothing arrived.
        self._install(FakeBackend([empty, empty, empty]))
        code, out = _run(self._argv())
        self.assertEqual(code, sea.EXIT_FIRE)
        self.assertEqual(out["failures"], ["text_not_landed"])
        self.assertFalse(out["approval_delivered"])
        self.assertEqual(self._events(), [])

    def test_enter_that_does_not_submit_fails_loud(self):
        # THE regression: the text stays in the composer after Enter.
        # Under the old manual procedure this is what "success" looked
        # like; here it must be a non-zero exit and no record.
        landed = _screen(self._approval())
        empty = [_fence(), {"row": 2, "text": "❯"}, _fence()]
        backend = FakeBackend([empty, landed, landed])
        self._install(backend)
        code, out = _run(self._argv())
        self.assertEqual(code, sea.EXIT_FIRE)
        self.assertEqual(out["failures"], ["not_submitted"])
        self.assertFalse(out["approval_delivered"])
        self.assertEqual(self._events(), [])
        self.assertIn("届いていない", out["remedy"][0])

    def test_prior_draft_refuses_before_writing(self):
        dirty = _screen("leftover draft text")
        backend = FakeBackend([dirty])
        self._install(backend)
        code, out = _run(self._argv())
        self.assertEqual(code, sea.EXIT_FIRE)
        self.assertEqual(out["failures"], ["prior_draft"])
        # Nothing was typed into a composer we would have corrupted.
        self.assertNotIn("text", [c[0] for c in backend.calls])

    def test_backend_failure_is_a_gate_failure(self):
        empty = [_fence(), {"row": 2, "text": "❯"}, _fence()]
        self._install(FakeBackend([empty], fail_on="text"))
        code, out = _run(self._argv())
        self.assertEqual(code, sea.EXIT_FIRE)
        self.assertEqual(out["failures"], ["backend_failed"])
        self.assertEqual(self._events(), [])

    def test_no_files_is_refused(self):
        code, out = _run(["--db-path", str(self.db), "send", "--task", TASK])
        self.assertEqual(code, sea.EXIT_ERROR)
        self.assertEqual(out["status"], "error")

    def test_dry_run_sends_nothing(self):
        backend = FakeBackend([[]])
        self._install(backend)
        code, out = _run(self._argv("--dry-run"))
        self.assertEqual(code, sea.EXIT_OK)
        self.assertEqual(out["status"], "dry_run")
        self.assertEqual(backend.calls, [])
        self.assertEqual(self._events(), [])

    def test_default_target_is_worker_task(self):
        empty = [_fence(), {"row": 2, "text": "❯"}, _fence()]
        self._install(FakeBackend([empty, _screen(self._approval()), empty]))
        code, out = _run(self._argv())
        self.assertEqual(code, sea.EXIT_OK)
        self.assertEqual(out["pane"], f"worker-{TASK}")


# ---------------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------------

class BackendTests(unittest.TestCase):
    def test_renga_never_sends_text_and_enter_together(self):
        seen = []

        def fake_run(cmd, timeout):
            seen.append(cmd)
            class P:
                returncode = 0
                stdout = '{"lines": []}'
                stderr = ""
            return P()

        real, sea._run = sea._run, fake_run
        try:
            b = sea.RengaBackend("worker-x", None, 5.0)
            b.send_text("hello")
            b.send_enter()
        finally:
            sea._run = real
        text_cmd, enter_cmd = seen
        self.assertNotIn("--enter", text_cmd)      # the paste-absorption trap
        self.assertIn("--enter", enter_cmd)
        self.assertIn("hello", text_cmd)
        # Enter carries no text of its own: empty positional + --enter.
        self.assertEqual(enter_cmd[2], "")

    def test_tmux_argv_and_capture_parsing(self):
        seen = []

        def fake_run(cmd, timeout):
            seen.append(cmd)
            class P:
                returncode = 0
                stdout = "line one\n❯ draft\n"
                stderr = ""
            return P()

        real, sea._run = sea._run, fake_run
        try:
            b = sea.TmuxBackend("%3", 5.0)
            b.send_text("hi")
            b.send_enter()
            rows = b.inspect(10)
        finally:
            sea._run = real
        self.assertEqual(seen[0][:3], [sea._TMUX_BIN, "-L", sea._TMUX_SOCKET])
        self.assertIn("-l", seen[0])               # literal: no key-name parsing
        self.assertNotIn("Enter", seen[0])
        self.assertIn("Enter", seen[1])
        self.assertEqual([r["text"] for r in rows], ["line one", "❯ draft"])

    def test_backend_follows_resolved_transport(self):
        args = sea.build_parser().parse_args(
            ["send", "--task", "t", "--file", "f"])
        self.assertIsInstance(sea.make_backend(args, "renga"), sea.RengaBackend)
        self.assertIsInstance(sea.make_backend(args, "broker"), sea.TmuxBackend)

    def test_explicit_backend_overrides_transport(self):
        args = sea.build_parser().parse_args(
            ["send", "--task", "t", "--file", "f", "--backend", "renga"])
        self.assertIsInstance(sea.make_backend(args, "broker"), sea.RengaBackend)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "state.db"
        _make_db(self.db)
        self.addCleanup(self.tmp.cleanup)

    def _audit(self, *extra):
        return _run(["--db-path", str(self.db), "audit",
                     "--claude-org-root", ROOT, "--older-than-min", "0", *extra])

    def test_self_edit_dispatch_without_approval_is_reported(self):
        _seed_run(self.db)
        _seed_event(self.db, sea.DELEGATE_SENT_EVENT,
                    {"task": TASK, "worker": f"worker-{TASK}", "dir": SELF_EDIT_DIR},
                    _PAST)
        code, out = self._audit()
        self.assertEqual(code, sea.EXIT_FIRE)
        self.assertEqual(out["finding_count"], 1)
        self.assertEqual(out["findings"][0]["task"], TASK)

    def test_approval_after_dispatch_clears_it(self):
        _seed_run(self.db)
        _seed_event(self.db, sea.DELEGATE_SENT_EVENT,
                    {"task": TASK, "dir": SELF_EDIT_DIR}, _PAST)
        _seed_event(self.db, sea.APPROVAL_EVENT,
                    {"task": TASK, "files": ["x"]}, "2026-08-25T02:00:00.000Z")
        code, out = self._audit()
        self.assertEqual(code, sea.EXIT_OK)
        self.assertEqual(out["status"], "clean")

    def test_approval_predating_a_redispatch_does_not_cover_it(self):
        _seed_run(self.db)
        _seed_event(self.db, sea.APPROVAL_EVENT, {"task": TASK},
                    "2026-08-25T00:30:00.000Z")
        _seed_event(self.db, sea.DELEGATE_SENT_EVENT,
                    {"task": TASK, "dir": SELF_EDIT_DIR}, _PAST)
        code, out = self._audit()
        self.assertEqual(code, sea.EXIT_FIRE)

    def test_non_self_edit_dispatch_is_skipped(self):
        _seed_run(self.db, task="other")
        _seed_event(self.db, sea.DELEGATE_SENT_EVENT,
                    {"task": "other", "dir": OTHER_DIR}, _PAST)
        code, out = self._audit()
        self.assertEqual(code, sea.EXIT_OK)
        self.assertEqual(out["skipped"]["not_self_edit"], 1)

    def test_terminal_run_is_skipped(self):
        _seed_run(self.db, status="completed")
        _seed_event(self.db, sea.DELEGATE_SENT_EVENT,
                    {"task": TASK, "dir": SELF_EDIT_DIR}, _PAST)
        code, out = self._audit()
        self.assertEqual(code, sea.EXIT_OK)
        self.assertEqual(out["skipped"]["terminal_run"], 1)

    def test_dispatch_before_gate_epoch_is_skipped(self):
        _seed_run(self.db)
        _seed_event(self.db, sea.DELEGATE_SENT_EVENT,
                    {"task": TASK, "dir": SELF_EDIT_DIR},
                    "2026-01-01T00:00:00.000Z")
        code, out = self._audit()
        self.assertEqual(code, sea.EXIT_OK)
        self.assertEqual(out["skipped"]["before_gate_epoch"], 1)

    def test_repo_root_itself_counts_as_self_edit(self):
        # Pattern C forced self-edit works in the repo root directly.
        self.assertTrue(sea._is_self_edit_dir(ROOT, ROOT))
        self.assertTrue(sea._is_self_edit_dir(SELF_EDIT_DIR, ROOT))
        self.assertFalse(sea._is_self_edit_dir(OTHER_DIR, ROOT))
        # A sibling whose path merely shares the prefix is not inside it.
        self.assertFalse(sea._is_self_edit_dir(ROOT + "-mirror/x", ROOT))


if __name__ == "__main__":
    unittest.main()
