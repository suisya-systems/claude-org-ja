"""Fixture-based importer ↔ snapshotter round-trip test (M4, Issue #267).

The CI ``drift_check`` step must verify that the importer and the
snapshotter agree on every structured field. This test uses a hand-
curated fixture (post-M4 freeze shape — structured sections only) and
asserts:

1. Importer can rebuild a DB from the fixture without error.
2. ``compute_diff`` against the fixture (NOT a regenerated copy)
   returns 0 — the snapshotter's render of the DB the importer
   produced from the fixture matches the fixture byte-for-byte.
3. After ``post_commit_regenerate`` the file remains identical (no
   drift introduced by writing back what was already there).

M4 (Issue #267) removed the passthrough escape hatch, so the diff is
now whole-file: every byte of the fixture must come from the DB. Free-
form content lives under ``notes/`` and is not part of this round-trip.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.state_db import connect
from tools.state_db.drift_check import compute_diff
from tools.state_db.importer import import_full_rebuild
from tools.state_db.snapshotter import post_commit_regenerate


_FIXTURE_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "tests" / "fixtures" / "m2_1_drift_sample"
)


class TestImporterSnapshotterRoundtrip(unittest.TestCase):
    def test_curated_fixture_roundtrips_with_no_drift(self):
        self.assertTrue(_FIXTURE_ROOT.is_dir(),
                        f"fixture missing: {_FIXTURE_ROOT}")
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            shutil.copytree(_FIXTURE_ROOT, work, dirs_exist_ok=True)
            db_path = work / ".state" / "state.db"
            md_path = work / ".state" / "org-state.md"
            self.assertTrue(md_path.is_file())
            original = md_path.read_text(encoding="utf-8")

            import_full_rebuild(db_path, work, strict=False)
            conn = connect(db_path)
            try:
                # Diff *before* regenerate: compares snapshotter render
                # of the DB the importer produced against the
                # hand-written fixture. Diffing after regenerate would
                # be the snapshotter against itself.
                diff = compute_diff(conn, md_path)
                self.assertEqual(
                    diff, "",
                    "importer ↔ snapshotter round-trip drift detected — "
                    "the fixture's structured sections do not match what "
                    "the snapshotter renders from the DB the importer "
                    "produced. Either the importer is dropping a field "
                    "on parse, or the snapshotter is not rendering a "
                    f"field the importer captures. Diff:\n{diff}"
                )
                # Regenerate the file and confirm it stays byte-stable.
                post_commit_regenerate(conn, work)
            finally:
                conn.close()

            regenerated = md_path.read_text(encoding="utf-8")
            self.assertEqual(
                original.rstrip("\n") + "\n",
                regenerated.rstrip("\n") + "\n",
                "post_commit_regenerate produced bytes different from the "
                "fixture — the round-trip is no longer idempotent.",
            )
            # Codex M-r1-2: the importer must backfill `runs.title` from
            # the Active Work Items bullets. Without that, the regen
            # replaces the human-readable title (`M4 markdown freeze`)
            # with the bare task_id and the round-trip silently drops
            # operator-curated text.
            self.assertIn("M4 markdown freeze", regenerated)
            self.assertIn("prior task carry-over", regenerated)


if __name__ == "__main__":
    unittest.main()
