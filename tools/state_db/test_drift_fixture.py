"""Fixture-based importer ↔ snapshotter round-trip test (M2.1, Issue #272).

Cross-review M2.1 Major 2: the prior CI ``drift_check`` step was
tautological — it ran ``importer`` (which calls into the structured
parsers in ``importer.py``) and then asked ``drift_check`` to compare
the freshly regenerated markdown against the same DB the snapshotter
produced. Both sides used the snapshotter, so a divergence between the
**importer**'s parse coverage and the **snapshotter**'s render coverage
would never surface.

This test replaces that with an actual round-trip:

1. Take a hand-curated ``.state/org-state.md`` fixture under
   ``tests/fixtures/m2_1_drift_sample/`` containing every structured
   section we own (Dispatcher, Curator, Worker Directory Registry,
   Resume Instructions) plus free-form sections that must pass through.
2. Copy the fixture into a worktree-isolated tempdir, import it into a
   fresh DB.
3. Regenerate the markdown from the DB via the snapshotter.
4. Run ``compute_diff`` on the regenerated file. A non-zero diff
   means: importer parsed something the snapshotter doesn't render,
   or vice versa, or the writer's field set has drifted from either.
   Failure mode is structural drift in the structured slice.

The free-form sections (the curated learning notes and the demoted
``## Active Work Items``) are excluded from comparison by ``drift_check``
itself; the assertion below additionally checks the regenerated file
still contains them, proving passthrough is preserved across the
round-trip.
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

            import_full_rebuild(db_path, work, strict=False)
            conn = connect(db_path)
            try:
                post_commit_regenerate(conn, work)
                diff = compute_diff(conn, md_path)
            finally:
                conn.close()

            self.assertEqual(
                diff, "",
                f"importer ↔ snapshotter round-trip drift detected:\n{diff}"
            )

            # Passthrough sections must survive the regenerate.
            regenerated = md_path.read_text(encoding="utf-8")
            self.assertIn("過去セッションの学び", regenerated)
            self.assertIn("## Active Work Items", regenerated)
            self.assertIn("prior-completed: COMPLETED", regenerated)


if __name__ == "__main__":
    unittest.main()
