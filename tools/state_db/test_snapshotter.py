"""Tests for tools.state_db.snapshotter and drift_check (Issue #267 M2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.state_db import apply_schema, connect
from tools.state_db.drift_check import compute_diff
from tools.state_db.snapshotter import (
    extract_unknown_sections,
    post_commit_regenerate,
    regenerate_journal_jsonl,
    regenerate_org_state_md,
    render_journal_jsonl,
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
        objective="finish M2",
        dispatcher_pane_id="2", dispatcher_peer_id="2",
        curator_pane_id="3", curator_peer_id="3",
        resume_instructions="Run `/org-resume` then briefing.",
    )
    wid = w.register_worker_dir(
        abs_path="/x/wd/issue-267-m2-write-switch", layout="flat",
        is_git_repo=True, is_worktree=True, current_branch="issue-267-m2",
    )
    w.upsert_run(
        task_id="issue-267-m2-write-switch",
        project_slug="claude-org-ja",
        pattern="B",
        title="M2 write switch",
        status="in_use",
        worker_dir_abs_path="/x/wd/issue-267-m2-write-switch",
        outcome_note="DB SoT migration",
    )
    w.upsert_run(
        task_id="prior-completed",
        project_slug="claude-org-ja",
        pattern="B",
        title="prior",
        status="completed",
        worker_dir_abs_path="/x/wd/issue-267-m2-write-switch",
        outcome_note="merged",
    )
    w.append_event(kind="dispatch", actor="dispatcher",
                    payload={"task": "issue-267-m2-write-switch"},
                    occurred_at="2026-05-04T01:00:00.000Z",
                    run_task_id="issue-267-m2-write-switch")
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
        self.assertIn("Current Objective: finish M2", md)
        # Section headings appear in canonical order. ``## Active Work
        # Items`` was demoted to passthrough in M2.1 (Issue #272) — see
        # ``_STRUCTURED_HEADINGS`` for the rationale — so the structured
        # render does not emit it any more.
        idx_disp = md.index("## Dispatcher")
        idx_cur = md.index("## Curator")
        idx_wdr = md.index("## Worker Directory Registry")
        idx_resume = md.index("## Resume Instructions")
        self.assertLess(idx_disp, idx_cur)
        self.assertLess(idx_cur, idx_wdr)
        self.assertLess(idx_wdr, idx_resume)
        self.assertNotIn("## Active Work Items", md)
        # WDR contains both runs (active + completed) so both task ids
        # are still rendered.
        self.assertIn("issue-267-m2-write-switch", md)
        self.assertIn("prior-completed", md)


class TestStructuredHeadingExactMatch(unittest.TestCase):
    """Cross-review M1: substring match on heading names was eating
    free-form headings whose name happened to *contain* a structured
    keyword. Switched to exact match (lower-cased)."""

    def test_dispatcher_notes_passes_through(self):
        src = (
            "## Dispatcher\n- Peer ID: 2\n\n"
            "## Dispatcher Notes\nfree-form ops log\n\n"
            "## Curator メモ\n人間の覚え書き\n\n"
            "## Curator\n- Peer ID: 3\n"
        )
        passthrough = extract_unknown_sections(src)
        self.assertIn("Dispatcher Notes", passthrough)
        self.assertIn("free-form ops log", passthrough)
        self.assertIn("Curator メモ", passthrough)
        self.assertIn("人間の覚え書き", passthrough)
        # The exact-match structured headings are excluded.
        self.assertNotIn("Peer ID: 2", passthrough)
        self.assertNotIn("Peer ID: 3", passthrough)

    def test_drift_check_detects_loss_of_dispatcher_notes(self):
        # Sanity: when the on-disk markdown has a "## Dispatcher Notes"
        # section, drift_check must NOT report drift just because the
        # snapshotter's structured slice doesn't render that section.
        # The previous (substring) implementation classified it as
        # structured, dropped it from the passthrough output, and then
        # the strip-passthrough step in drift_check left it behind in
        # the actual-side text — which surfaced as drift.
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md)
                with md.open("a", encoding="utf-8") as fh:
                    fh.write(
                        "\n## Dispatcher Notes\nfree-form note\n"
                        "\n## Curator メモ\n別の自由記述\n"
                    )
                diff = compute_diff(conn, md)
            finally:
                conn.close()
        self.assertEqual(diff, "")


class TestActiveWorkItemsPassthrough(unittest.TestCase):
    """M2.1 (Issue #272): the live ``.state/org-state.md`` keeps a
    free-form ``## Active Work Items`` list that the importer routes
    to ``events`` (kind ``legacy_active_item``), not to ``runs``. The
    snapshotter must therefore preserve the existing block via
    passthrough — otherwise the first post_commit_regenerate after the
    cutover silently erases every COMPLETED / ABANDONED entry the
    operator curated by hand. This test reproduces the live shape and
    asserts the regen round-trip preserves the list."""

    _LIVE_SHAPE = (
        "# Org State\n\nStatus: ACTIVE\n\n"
        "## Dispatcher\n- Peer ID: 2\n\n"
        "## Worker Directory Registry\n\n"
        "## Active Work Items\n\n"
        "- worker-brief-generator: COMPLETED (PR #228 merged)\n"
        "- contract-set-a-outline: COMPLETED (PR #227 merged)\n"
        "- dogfooding-smoke-rerun-session8: REVIEW 待ち\n"
        "- session-current: IN_PROGRESS\n"
        "\n"
        "## Pending Lead アクション\n- 何かのフォローアップ\n"
    )

    def test_completed_items_survive_regen(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            md.write_text(self._LIVE_SHAPE, encoding="utf-8")
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md, source_md=md)
                regenerated = md.read_text(encoding="utf-8")
            finally:
                conn.close()
        # Operator-curated COMPLETED / REVIEW entries must survive.
        self.assertIn("worker-brief-generator: COMPLETED", regenerated)
        self.assertIn("contract-set-a-outline: COMPLETED", regenerated)
        self.assertIn("dogfooding-smoke-rerun-session8: REVIEW", regenerated)
        # Free-form follow-up section also survives.
        self.assertIn("Pending Lead", regenerated)
        # And the structured render still updates DB-owned blocks.
        self.assertIn("Status: ACTIVE", regenerated)


class TestPassthrough(unittest.TestCase):
    def test_extract_unknown_sections_keeps_freeform(self):
        src = (
            "# Org State\n\nStatus: ACTIVE\n\n"
            "## 2026-05-04 セッション #11 主要成果\n"
            "- ratified Phase 1\n\n"
            "## Dispatcher\n- Peer ID: 2\n\n"
            "## 本セッションの学び\nlessons here\n"
        )
        passthrough = extract_unknown_sections(src)
        self.assertIn("セッション #11", passthrough)
        self.assertIn("本セッションの学び", passthrough)
        self.assertNotIn("Dispatcher", passthrough)


class TestMergeRender(unittest.TestCase):
    def test_render_org_state_md_merges_passthrough(self):
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
                body = render_org_state_md(conn, source_md=md)
            finally:
                conn.close()
        # New header replaces old (DB-derived).
        self.assertIn("Status: ACTIVE", body)
        self.assertNotIn("Status: STALE", body)
        # Free-form passthrough preserved.
        self.assertIn("free-form note", body)
        self.assertIn("freeform body", body)
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

    def test_regenerate_journal_jsonl_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            jl = Path(td) / "journal.jsonl"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_journal_jsonl(conn, jl)
                first = jl.read_bytes()
                regenerate_journal_jsonl(conn, jl)
                second = jl.read_bytes()
            finally:
                conn.close()
        self.assertEqual(first, second)
        # Body has both events in chronological order.
        text = first.decode("utf-8")
        self.assertIn('"event": "dispatch"', text)
        self.assertIn('"event": "suspend"', text)
        self.assertLess(text.index("dispatch"), text.index("suspend"))


class TestPostCommitRegenerate(unittest.TestCase):
    def test_post_commit_regenerate_writes_both(self):
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
            self.assertTrue((root / ".state" / "journal.jsonl").exists())


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

    def test_freeform_section_not_treated_as_drift(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "s.db"
            md = Path(td) / "org-state.md"
            conn = connect(db)
            try:
                apply_schema(conn)
                _seed_session(conn)
                regenerate_org_state_md(conn, md)
                # Append a free-form section directly to the file. Drift
                # check ignores unknown sections; expected diff = empty.
                with md.open("a", encoding="utf-8") as fh:
                    fh.write("\n## 本セッションの学び\nlearned X\n")
                diff = compute_diff(conn, md)
            finally:
                conn.close()
        self.assertEqual(diff, "")


if __name__ == "__main__":
    unittest.main()
