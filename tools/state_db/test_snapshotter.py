"""Tests for tools.state_db.snapshotter and drift_check (Issue #267 M4)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.state_db import apply_schema, connect
from tools.state_db.drift_check import compute_diff
from tools.state_db.snapshotter import (
    post_commit_regenerate,
    regenerate_org_state_md,
    render_org_state_md,
    render_structured_markdown,
)
from tools.state_db.writer import StateWriter


def _seed_session(conn):
    w = StateWriter(conn)
    w.update_session(
        status="ACTIVE",
        started_at="2026-04-22",
        updated_at="2026-05-04 (session #11)",
        objective="finish M4",
        dispatcher_pane_id="2", dispatcher_peer_id="2",
        curator_pane_id="3", curator_peer_id="3",
        resume_instructions="Run `/org-resume` then briefing.",
    )
    w.register_worker_dir(
        abs_path="/x/wd/issue-267-m4-markdown-freeze", layout="flat",
        is_git_repo=True, is_worktree=True,
        current_branch="issue-267-m4-markdown-freeze",
    )
    w.upsert_run(
        task_id="issue-267-m4-markdown-freeze",
        project_slug="claude-org-ja",
        pattern="B",
        title="M4 freeze",
        status="in_use",
        worker_dir_abs_path="/x/wd/issue-267-m4-markdown-freeze",
        outcome_note="DB SoT migration",
    )
    w.upsert_run(
        task_id="prior-completed",
        project_slug="claude-org-ja",
        pattern="B",
        title="prior",
        status="completed",
        worker_dir_abs_path="/x/wd/issue-267-m4-markdown-freeze",
        outcome_note="merged",
    )
    w.append_event(kind="dispatch", actor="dispatcher",
                    payload={"task": "issue-267-m4-markdown-freeze"},
                    occurred_at="2026-05-04T01:00:00.000Z",
                    run_task_id="issue-267-m4-markdown-freeze")
    w.append_event(kind="suspend", actor="secretary",
                    payload={"reason": "user_requested"},
                    occurred_at="2026-05-04T02:00:00.000Z")
    w.commit()
    return w


class TestStructuredRender(unittest.TestCase):
    def test_renders_known_sections_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                md = render_structured_markdown(conn)
            finally:
                conn.close()
        self.assertIn("# Org State", md)
        self.assertIn("Status: ACTIVE", md)
        self.assertIn("Started: 2026-04-22", md)
        self.assertIn("Current Objective: finish M4", md)
        # Section order: Dispatcher → Curator → WDR → Active Work Items
        # → Resume Instructions. M4 (Issue #267) reinstates the
        # ``## Active Work Items`` block as a DB-rendered section
        # (sourced from runs with status in_use / review).
        idx_disp = md.index("## Dispatcher")
        idx_cur = md.index("## Curator")
        idx_wdr = md.index("## Worker Directory Registry")
        idx_active = md.index("## Active Work Items")
        idx_resume = md.index("## Resume Instructions")
        self.assertLess(idx_disp, idx_cur)
        self.assertLess(idx_cur, idx_wdr)
        self.assertLess(idx_wdr, idx_active)
        self.assertLess(idx_active, idx_resume)
        # WDR contains both runs (active + completed) so both task ids
        # are still rendered.
        self.assertIn("issue-267-m4-markdown-freeze", md)
        self.assertIn("prior-completed", md)

    def test_active_work_items_only_lists_in_use_and_review(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                md = render_structured_markdown(conn)
            finally:
                conn.close()
        # The completed run must NOT appear under Active Work Items
        # (it still appears in WDR).
        active_section = md.split("## Active Work Items", 1)[1].split("\n## ", 1)[0]
        self.assertIn("issue-267-m4-markdown-freeze", active_section)
        self.assertNotIn("prior-completed", active_section)
        # Status label uses the UI vocabulary.
        self.assertIn("[IN_PROGRESS]", active_section)


class TestRenderOrgStateMdHasNoPassthrough(unittest.TestCase):
    """M4 (Issue #267): the snapshotter no longer reads the on-disk
    markdown — every byte of the regenerated file comes from the DB."""

    def test_regenerate_drops_unknown_sections(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            md.write_text(
                "# Old header (will be discarded)\n\n"
                "Status: STALE\n\n"
                "## Dispatcher\n- Peer ID: 99\n\n"
                "## 2026-05-04 free-form note\nfreeform body\n",
                encoding="utf-8",
            )
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md)
                body = md.read_text(encoding="utf-8")
            finally:
                conn.close()
        # New header replaces old (DB-derived).
        self.assertIn("Status: ACTIVE", body)
        self.assertNotIn("Status: STALE", body)
        # Free-form section is no longer carried through. Operators must
        # extract such content to ``notes/`` before regenerate.
        self.assertNotIn("free-form note", body)
        self.assertNotIn("freeform body", body)
        # Old Dispatcher block is dropped (DB regenerates it).
        self.assertNotIn("Peer ID: 99", body)
        self.assertIn("- Peer ID: 2", body)


class TestIdempotency(unittest.TestCase):
    def test_regenerate_org_state_md_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md)
                first = md.read_bytes()
                regenerate_org_state_md(conn, md)
                second = md.read_bytes()
            finally:
                conn.close()
        self.assertEqual(first, second)


class TestPostCommitRegenerate(unittest.TestCase):
    def test_post_commit_regenerate_writes_markdown_only(self):
        # M4: jsonl side-output is decommissioned; the wrapper writes
        # only `.state/org-state.md`.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".state").mkdir()
            db = root / ".state" / "state.db"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                post_commit_regenerate(conn, root)
            finally:
                conn.close()
            self.assertTrue((root / ".state" / "org-state.md").exists())
            self.assertFalse((root / ".state" / "journal.jsonl").exists())


class TestDriftCheck(unittest.TestCase):
    def test_zero_diff_after_regenerate(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md)
                diff = compute_diff(conn, md)
            finally:
                conn.close()
        self.assertEqual(diff, "")

    def test_drift_detected_when_markdown_edited_in_known_section(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md)
                # Mutate a structured field by hand → must surface as drift.
                text = md.read_text(encoding="utf-8")
                tampered = text.replace("Status: ACTIVE", "Status: SUSPENDED")
                md.write_text(tampered, encoding="utf-8")
                diff = compute_diff(conn, md)
            finally:
                conn.close()
        self.assertNotEqual(diff, "")
        self.assertIn("Status:", diff)

    def test_freeform_section_appended_now_counts_as_drift(self):
        # M4 (Issue #267): the passthrough exception is gone. A
        # free-form ``## …`` section appended to the dump must surface
        # as drift — operators must put such content under ``notes/``.
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md)
                with md.open("a", encoding="utf-8") as fh:
                    fh.write("\n## 本セッションの学び\nlearned X\n")
                diff = compute_diff(conn, md)
            finally:
                conn.close()
        self.assertNotEqual(diff, "")
        self.assertIn("本セッションの学び", diff)


if __name__ == "__main__":
    unittest.main()
