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
3. Run ``compute_diff`` against the **fixture** (NOT a regenerated
   copy). This compares the hand-written markdown against the
   snapshotter's render of the DB the importer produced from that same
   markdown. A non-zero diff means: importer dropped a field on parse,
   or the snapshotter doesn't render a field the importer captured, or
   the writer / schema columns the snapshotter relies on have drifted.
4. THEN regenerate from the DB and assert the free-form sections are
   preserved verbatim by passthrough. (Only after the diff check —
   if we regenerated first, both compare sides would be the same
   snapshotter render and the diff would always be zero.)

The free-form sections (the curated learning notes and the demoted
``## Active Work Items``) are excluded from the structured-slice
comparison by ``drift_check`` itself; step 4 additionally checks the
regenerated file still contains them, proving passthrough is preserved
across the round-trip.
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
                # CRITICAL ORDER: diff *before* regenerate. Comparing
                # the snapshotter render of the DB against the
                # hand-written fixture catches importer ↔ snapshotter
                # divergence. Diffing after regenerate would just be
                # the snapshotter against itself.
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
                # Now regenerate and confirm passthrough preservation
                # (free-form sections survive the snapshotter pass).
                post_commit_regenerate(conn, work)
            finally:
                conn.close()

            regenerated = md_path.read_text(encoding="utf-8")
            self.assertIn("過去セッションの学び", regenerated)
            self.assertIn("## Active Work Items", regenerated)
            self.assertIn("prior-completed: COMPLETED", regenerated)


if __name__ == "__main__":
    unittest.main()
