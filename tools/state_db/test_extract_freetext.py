"""Tests for tools.state_db.extract_freetext (Issue #267 M4)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.state_db.extract_freetext import (
    _slugify,
    _target_for_heading,
    apply_extraction,
    plan_extraction,
)


_LIVE_SHAPE = (
    "# Org State\n"
    "\n"
    "Status: ACTIVE\n"
    "Started: 2026-04-22\n"
    "Updated: 2026-05-04\n"
    "Current Objective: M4 freeze\n"
    "\n"
    "## Dispatcher\n"
    "- Peer ID: 2\n"
    "- Pane ID: 2\n"
    "\n"
    "## Curator\n"
    "- Peer ID: 3\n"
    "- Pane ID: 3\n"
    "\n"
    "## Worker Directory Registry\n"
    "\n"
    "| Task ID | Pattern | Directory | Project | Status |\n"
    "|---|---|---|---|---|\n"
    "| issue-267-m4 | B | /tmp/wd/issue-267 | claude-org-ja | in_use |\n"
    "\n"
    "## 2026-05-04 セッション #11 主要成果\n"
    "- ratified M4 plan\n"
    "- shipped extract_freetext\n"
    "\n"
    "## 本セッションの学び\n"
    "lesson: don't mock the DB.\n"
    "\n"
    "## Pending Lead アクション\n"
    "- follow up with secretary\n"
    "\n"
    "## Resume Instructions\n"
    "\n"
    "Run /org-resume.\n"
)


class TestPlan(unittest.TestCase):
    def test_structured_headings_kept(self):
        plan = plan_extraction(_LIVE_SHAPE, today_iso="2026-05-05")
        kept = [r for r in plan if r["structured"]]
        moved = [r for r in plan if not r["structured"]]
        kept_headings = {r["heading"].lower() for r in kept}
        moved_headings = {r["heading"].lower() for r in moved}
        # Structured snapshotter-owned headings remain.
        self.assertIn("dispatcher", kept_headings)
        self.assertIn("curator", kept_headings)
        self.assertIn("worker directory registry", kept_headings)
        self.assertIn("resume instructions", kept_headings)
        # Free-form headings flagged for move.
        self.assertTrue(
            any("セッション" in h for h in moved_headings),
            f"session heading not flagged: {moved_headings!r}",
        )
        self.assertTrue(any("学び" in h for h in moved_headings))
        self.assertTrue(any("pending lead" in h for h in moved_headings))

    def test_session_heading_routes_to_sessions_dir(self):
        target = _target_for_heading(
            "2026-05-04 セッション #11 主要成果",
            today_iso="2026-05-05",
        )
        self.assertEqual(str(target).replace("\\", "/"),
                          "sessions/2026-05-04-session-11.md")

    def test_learnings_heading_uses_today_when_no_date(self):
        target = _target_for_heading("本セッションの学び",
                                       today_iso="2026-05-05")
        self.assertEqual(str(target).replace("\\", "/"),
                          "learnings/2026-05-05.md")

    def test_pending_lead_routes_to_pending_leads_md(self):
        target = _target_for_heading("Pending Lead アクション",
                                       today_iso="2026-05-05")
        self.assertEqual(str(target), "pending-leads.md")

    def test_misc_heading_falls_back_to_misc_dir(self):
        target = _target_for_heading("Operational Notes",
                                       today_iso="2026-05-05")
        self.assertEqual(str(target).replace("\\", "/"),
                          "misc/operational-notes.md")

    def test_slugify_handles_invalid_filename_chars(self):
        self.assertEqual(_slugify("foo/bar:baz"), "foo-bar-baz")
        self.assertEqual(_slugify("   "), "section")
        # Unicode preserved.
        self.assertIn("学", _slugify("学び"))


class TestApply(unittest.TestCase):
    def _run(self, today="2026-05-05"):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        org_state = root / ".state" / "org-state.md"
        org_state.parent.mkdir(parents=True)
        org_state.write_text(_LIVE_SHAPE, encoding="utf-8")
        notes_dir = root / "notes"
        summary = apply_extraction(org_state, notes_dir, today_iso=today)
        return root, org_state, notes_dir, summary

    def test_apply_moves_freeform_blocks_to_notes(self):
        _, org_state, notes_dir, summary = self._run()
        self.assertEqual(summary["moved"], 3)
        rewritten = org_state.read_text(encoding="utf-8")
        # Free-form sections gone from org-state.md.
        self.assertNotIn("セッション #11", rewritten)
        self.assertNotIn("本セッションの学び", rewritten)
        self.assertNotIn("Pending Lead", rewritten)
        # Structured sections retained.
        self.assertIn("## Dispatcher", rewritten)
        self.assertIn("## Worker Directory Registry", rewritten)
        self.assertIn("## Resume Instructions", rewritten)
        # Files written.
        self.assertTrue(
            (notes_dir / "sessions" / "2026-05-04-session-11.md").exists()
        )
        self.assertTrue(
            (notes_dir / "learnings" / "2026-05-05.md").exists()
        )
        self.assertTrue((notes_dir / "pending-leads.md").exists())
        # Manifest written.
        manifest = json.loads(
            (notes_dir / ".extraction-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["entries"]), 3)
        # Marker comment added.
        self.assertIn("See notes/", rewritten)

    def test_apply_is_idempotent(self):
        _, org_state, notes_dir, summary1 = self._run()
        first_md = org_state.read_text(encoding="utf-8")
        summary2 = apply_extraction(org_state, notes_dir,
                                      today_iso="2026-05-05")
        second_md = org_state.read_text(encoding="utf-8")
        self.assertTrue(summary2["unchanged"])
        self.assertEqual(first_md, second_md)

    def test_plan_does_not_apply(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        org_state = root / ".state" / "org-state.md"
        org_state.parent.mkdir(parents=True)
        org_state.write_text(_LIVE_SHAPE, encoding="utf-8")
        plan = plan_extraction(_LIVE_SHAPE, today_iso="2026-05-05")
        # plan_extraction is pure: no notes/ dir created.
        self.assertFalse((root / "notes").exists())
        self.assertTrue(any(not r["structured"] for r in plan))

    def test_session_block_body_preserved(self):
        _, _, notes_dir, _ = self._run()
        body = (notes_dir / "sessions" / "2026-05-04-session-11.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("ratified M4 plan", body)
        self.assertIn("shipped extract_freetext", body)
        # Heading line itself is preserved.
        self.assertIn("## 2026-05-04 セッション #11 主要成果", body)

    def test_same_heading_different_body_appends(self):
        """Codex r3 B-1: a re-add of the same heading with a NEW body
        must land in notes/, not be silently dropped by the dedup
        check. The pre-fix code keyed dedup on (heading, target) only
        so the second body was filtered out and then deleted by the
        org-state rewrite — permanent data loss."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        org_state = root / ".state" / "org-state.md"
        org_state.parent.mkdir(parents=True)
        # First run: one Pending Lead bullet.
        org_state.write_text(
            "# Org State\n\nStatus: ACTIVE\n\n"
            "## Dispatcher\n- Peer ID: 2\n\n"
            "## Pending Lead アクション\n- 初回: スコープ確定\n",
            encoding="utf-8",
        )
        notes_dir = root / "notes"
        apply_extraction(org_state, notes_dir, today_iso="2026-05-05")
        # Second run: operator added a *new* bullet under the same
        # heading — different body, same (heading, target) key.
        org_state.write_text(
            org_state.read_text(encoding="utf-8")
            + "\n## Pending Lead アクション\n- 追加: リリースノート起草\n",
            encoding="utf-8",
        )
        summary = apply_extraction(org_state, notes_dir,
                                    today_iso="2026-05-05")
        self.assertGreaterEqual(summary["moved"], 1)
        pl = (notes_dir / "pending-leads.md").read_text(encoding="utf-8")
        # Both bullets must survive — no silent loss.
        self.assertIn("初回: スコープ確定", pl)
        self.assertIn("追加: リリースノート起草", pl)

    def test_partial_failure_recovery_no_manifest_no_double_append(self):
        """Codex r3 M-1: if the prior run wrote notes/ but crashed
        before reaching org-state.md, the manifest IS already on disk
        (we now write it before the rewrite). A rerun reads it, sees
        the body_sha256, and skips the notes/ append even though the
        same blocks are still present in org-state.md."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        org_state = root / ".state" / "org-state.md"
        org_state.parent.mkdir(parents=True)
        org_state.write_text(_LIVE_SHAPE, encoding="utf-8")
        notes_dir = root / "notes"
        # First run completes (manifest written).
        apply_extraction(org_state, notes_dir, today_iso="2026-05-05")
        first = (notes_dir / "sessions" /
                 "2026-05-04-session-11.md").read_text(encoding="utf-8")
        # Simulate "crash before rewrite": put the free-form blocks
        # back into org-state.md.
        org_state.write_text(_LIVE_SHAPE, encoding="utf-8")
        apply_extraction(org_state, notes_dir, today_iso="2026-05-05")
        second = (notes_dir / "sessions" /
                  "2026-05-04-session-11.md").read_text(encoding="utf-8")
        self.assertEqual(first, second, "rerun double-appended notes/ body")

    def test_manifest_entries_preserve_source_order(self):
        """Codex r3 M-2: manifest entries must follow the order
        ``## …`` blocks appeared in org-state.md so a future restore
        tool can interleave them back at their original positions."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        org_state = root / ".state" / "org-state.md"
        org_state.parent.mkdir(parents=True)
        # Interleaved free-form sections with the same target file
        # type (Pending Lead appears twice, sandwiching a session
        # block — the pre-fix grouping-by-target order would have
        # listed both Pending Leads first.)
        org_state.write_text(
            "# Org State\n\nStatus: ACTIVE\n\n"
            "## Dispatcher\n- Peer ID: 2\n\n"
            "## Pending Lead 第一弾\n- A\n\n"
            "## 2026-05-04 セッション #11 主要成果\n- M4 done\n\n"
            "## Pending Lead 第二弾\n- B\n",
            encoding="utf-8",
        )
        notes_dir = root / "notes"
        apply_extraction(org_state, notes_dir, today_iso="2026-05-05")
        manifest = json.loads(
            (notes_dir / ".extraction-manifest.json").read_text(encoding="utf-8")
        )
        indices = [e["source_index"] for e in manifest["entries"]]
        self.assertEqual(indices, sorted(indices),
                          f"manifest not in source order: {indices}")
        headings = [e["heading"] for e in manifest["entries"]]
        # Pending Lead 第一弾 must precede the session, which must
        # precede Pending Lead 第二弾.
        self.assertLess(headings.index("Pending Lead 第一弾"),
                          headings.index("2026-05-04 セッション #11 主要成果"))
        self.assertLess(headings.index("2026-05-04 セッション #11 主要成果"),
                          headings.index("Pending Lead 第二弾"))

    def test_partial_failure_recovery_no_double_append(self):
        """Codex M-r1-3: if a previous run wrote notes/ but crashed
        before rewriting org-state.md, the second run must not append
        the same blocks again. The manifest records prior (heading,
        target) pairs; matching blocks are skipped on the notes/ side
        even though the org-state rewrite still strips them."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        org_state = root / ".state" / "org-state.md"
        org_state.parent.mkdir(parents=True)
        org_state.write_text(_LIVE_SHAPE, encoding="utf-8")
        notes_dir = root / "notes"
        # First run completes normally.
        apply_extraction(org_state, notes_dir, today_iso="2026-05-05")
        first_size = (notes_dir / "sessions" /
                      "2026-05-04-session-11.md").stat().st_size
        # Simulate the crashed-before-rewrite state by re-injecting the
        # free-form blocks back into the dump.
        org_state.write_text(_LIVE_SHAPE, encoding="utf-8")
        # Run again with the manifest already on disk.
        apply_extraction(org_state, notes_dir, today_iso="2026-05-05")
        second_size = (notes_dir / "sessions" /
                       "2026-05-04-session-11.md").stat().st_size
        # Same notes file — no double-append.
        self.assertEqual(first_size, second_size)

    def test_windows_reserved_name_is_prefixed(self):
        from tools.state_db.extract_freetext import _slugify
        self.assertTrue(_slugify("CON").startswith("_"))
        self.assertTrue(_slugify("Prn").startswith("_"))
        # Stem-with-extension forms (Codex r2): Windows treats
        # `CON.foo`, `COM1.log`, `PRN.v2` as the device too.
        self.assertTrue(_slugify("CON.foo").startswith("_"))
        self.assertTrue(_slugify("COM1.log").startswith("_"))
        self.assertTrue(_slugify("PRN.v2").startswith("_"))
        # Non-reserved names untouched.
        self.assertEqual(_slugify("notes"), "notes")
        self.assertEqual(_slugify("contact-list"), "contact-list")

    def test_no_freeform_means_noop(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        org_state = root / ".state" / "org-state.md"
        org_state.parent.mkdir(parents=True)
        clean = (
            "# Org State\n\nStatus: IDLE\n\n"
            "## Dispatcher\n- Peer ID: 2\n\n"
            "## Resume Instructions\n\nNothing pending.\n"
        )
        org_state.write_text(clean, encoding="utf-8")
        summary = apply_extraction(org_state, root / "notes",
                                    today_iso="2026-05-05")
        self.assertTrue(summary["unchanged"])
        self.assertEqual(org_state.read_text(encoding="utf-8"), clean)
        self.assertFalse((root / "notes").exists())


if __name__ == "__main__":
    unittest.main()
