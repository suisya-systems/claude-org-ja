"""Unit tests for tools/check_curate_threshold.py (curator on-demand).

The script is the single home of curate-threshold judgment (Codex
design review B1): the dispatcher branches on its exit code at worker
pane close, and org-curate consumes its ``reasons[]``. It must:

* fire ``raw_threshold`` at >= 5 active raw files, excluding
  ``archive/``, sentinels (``.gitkeep``), and legacy-marker remnants
* fire ``legacy_marker_sweep`` when any ``<!-- curated -->`` remnant
  sits directly under ``knowledge/raw/`` (B3)
* fire ``skill_candidates_pending`` on >= 5 ``- **status**: pending``
  lines **outside code fences** (the entry-format template example in
  the fenced block at the head of skill-candidates.md must never
  count), matching skill-audit Step 1's count command exactly (m9)
* fire ``work_skill_count`` with the same count skill-audit Step 1
  computes — parity asserted against the literal shell pipeline (M4)
* exit 0 / 10 so the dispatcher can branch without JSON parsing (m8)
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_curate_threshold as cct  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The pending-count command skill-audit Step 1 documents, verbatim
# (three-way sync: check_curate_threshold.count_pending / skill-audit
# Step 1 / the operational note in knowledge/skill-candidates.md).
_SKILL_AUDIT_PENDING_AWK = (
    "awk '/^(```|~~~)/ { fence = !fence; next }\n"
    "  !fence && /^- \\*\\*status\\*\\*: pending[ \\t]*$/ { n++ }\n"
    "  END { print n + 0 }' knowledge/skill-candidates.md 2>/dev/null"
    " || echo 0"
)


class _TreeCase(unittest.TestCase):
    """Base: builds a throwaway repo-shaped tree per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "knowledge" / "raw").mkdir(parents=True)
        (self.root / ".claude" / "skills").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def add_raw(self, name: str, body: str = "# note\n"):
        (self.root / "knowledge" / "raw" / name).write_text(
            body, encoding="utf-8"
        )

    def add_skill(self, name: str):
        d = self.root / ".claude" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")

    def add_candidates(self, pending: int, other: int = 0):
        lines = ["# queue", ""]
        for i in range(pending):
            lines.append(f"### 2026-06-07 pat-{i}")
            lines.append("- **status**: pending")
        for i in range(other):
            lines.append(f"### 2026-06-07 done-{i}")
            lines.append("- **status**: approved")
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def run_main(self, root: Path):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cct.main(["--root", str(root)])
        return code, json.loads(buf.getvalue())


class TestRawThreshold(_TreeCase):
    def test_below_threshold_is_silent_exit_0(self):
        for i in range(4):
            self.add_raw(f"2026-06-07-note-{i}.md")
        code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_BELOW_THRESHOLD)
        self.assertEqual(out["status"], "below_threshold")
        self.assertEqual(out["reasons"], [])
        self.assertEqual(out["counts"]["raw_active"], 4)

    def test_five_raw_files_fire_raw_threshold(self):
        for i in range(5):
            self.add_raw(f"2026-06-07-note-{i}.md")
        code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_CURATE_NEEDED)
        self.assertEqual(out["status"], "curate_needed")
        self.assertIn("raw_threshold", out["reasons"])

    def test_archive_and_sentinels_are_excluded(self):
        archive = self.root / "knowledge" / "raw" / "archive"
        archive.mkdir()
        for i in range(9):
            (archive / f"old-{i}.md").write_text(
                "<!-- curated -->\nx\n", encoding="utf-8"
            )
        self.add_raw(".gitkeep", "")
        self.add_raw(".hidden-note.md")
        code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_BELOW_THRESHOLD)
        self.assertEqual(out["counts"]["raw_active"], 0)
        self.assertEqual(out["counts"]["legacy_marker"], 0)

    def test_legacy_marker_files_do_not_count_as_active(self):
        for i in range(4):
            self.add_raw(f"new-{i}.md")
        self.add_raw("remnant.md", "<!-- curated -->\nold\n")
        code, out = self.run_main(self.root)
        # 4 active < 5, but the remnant fires legacy_marker_sweep.
        self.assertEqual(code, cct.EXIT_CURATE_NEEDED)
        self.assertEqual(out["counts"]["raw_active"], 4)
        self.assertEqual(out["counts"]["legacy_marker"], 1)
        self.assertNotIn("raw_threshold", out["reasons"])
        self.assertIn("legacy_marker_sweep", out["reasons"])

    def test_marker_after_bom_and_blank_line_is_detected(self):
        self.add_raw("bom.md", "﻿\n<!-- curated -->\nold\n")
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["legacy_marker"], 1)

    def test_missing_raw_dir_counts_zero(self):
        shutil.rmtree(self.root / "knowledge" / "raw")
        code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_BELOW_THRESHOLD)
        self.assertEqual(out["counts"]["raw_active"], 0)


class TestPendingCandidates(_TreeCase):
    def test_five_pending_fire(self):
        self.add_candidates(pending=5, other=3)
        code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_CURATE_NEEDED)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 5)
        self.assertEqual(out["reasons"], ["skill_candidates_pending"])

    def test_four_pending_do_not_fire(self):
        self.add_candidates(pending=4)
        code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_BELOW_THRESHOLD)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 4)

    def test_only_exact_pending_format_counts(self):
        # m9: the count matches `- **status**: pending` exactly; prose
        # mentions of the word "pending" must not inflate it.
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            "- **status**: pending\n"
            "- **status**: approved\n"
            "-  **status**: pending\n"  # double space — no match
            "the word pending in prose\n"
            "- **status**: pending\n",
            encoding="utf-8",
        )
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 2)

    def test_missing_file_counts_zero(self):
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 0)

    def test_deferred_entries_are_excluded(self):
        # Issue #753: a candidate the human shelved is marked
        # ``deferred`` — a non-terminal hold status that must NOT count
        # (only the literal ``pending`` token counts). Here 6 deferred
        # entries alongside 2 pending must yield a count of 2 (below the
        # threshold of 5), so shelved candidates never re-fire curator.
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            "# queue\n\n"
            + "".join(
                f"### 2026-07-22 shelved-{i}\n- **status**: deferred\n"
                for i in range(6)
            )
            + "".join(
                f"### 2026-07-22 live-{i}\n- **status**: pending\n"
                for i in range(2)
            ),
            encoding="utf-8",
        )
        code, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 2)
        self.assertEqual(code, cct.EXIT_BELOW_THRESHOLD)
        self.assertNotIn("skill_candidates_pending", out["reasons"])

    def test_fenced_template_is_not_counted(self):
        # The head of the real skill-candidates.md carries an
        # entry-format template inside a fenced block. A literal
        # `- **status**: pending` line in there (as the template read
        # before its defusal) must not inflate the count: 4 real
        # entries count as 4, not 5.
        real_entries = "".join(
            f"### 2026-07-03 pat-{i}\n- **status**: pending\n"
            for i in range(4)
        )
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            "# queue\n"
            "\n"
            "```markdown\n"
            "### {YYYY-MM-DD} {pattern-name}\n"
            "- **status**: pending\n"
            "```\n"
            "\n" + real_entries,
            encoding="utf-8",
        )
        code, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 4)
        self.assertEqual(code, cct.EXIT_BELOW_THRESHOLD)

    def test_tilde_fences_also_excluded(self):
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            "~~~\n"
            "- **status**: pending\n"
            "~~~\n"
            "- **status**: pending\n",
            encoding="utf-8",
        )
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 1)

    def test_pending_after_closed_fence_counts_again(self):
        # Fence state must toggle closed, not stick open forever.
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            "- **status**: pending\n"
            "```bash\n"
            "- **status**: pending\n"
            "```\n"
            "- **status**: pending\n",
            encoding="utf-8",
        )
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 2)

    def test_repo_template_head_has_no_countable_pending(self):
        """The committed skill-candidates.md head (format template +
        operational notes, i.e. everything above the entry list) must
        contribute 0 to the count — both template defusal (the example
        line no longer reads ``- **status**: pending``) and fence
        exclusion defend this."""
        real = (
            _REPO_ROOT / "knowledge" / "skill-candidates.md"
        ).read_text(encoding="utf-8")
        head = real.split("## エントリ一覧")[0]
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            head, encoding="utf-8"
        )
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["skill_candidates_pending"], 0)

    def test_parity_with_skill_audit_count_command(self):
        """Three-way sync (fence-excluding semantics): the literal awk
        command skill-audit Step 1 documents must (a) appear verbatim
        in the committed SKILL.md and (b) produce the same count as
        ``count_pending`` on a fixture exercising fences + real
        entries."""
        skill_md = (
            _REPO_ROOT / ".claude" / "skills" / "skill-audit" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            _SKILL_AUDIT_PENDING_AWK,
            skill_md,
            "skill-audit Step 1 count command drifted from the one "
            "this parity test runs — update both together",
        )
        fixture = (
            "```markdown\n"
            "- **status**: pending\n"
            "```\n"
            "- **status**: pending\n"
            "- **status**: pending  \n"  # trailing spaces still match
            "- **status**: approved\n"
            "~~~\n"
            "- **status**: pending\n"
            "~~~\n"
            "- **status**: pending\n"
        )
        (self.root / "knowledge" / "skill-candidates.md").write_text(
            fixture, encoding="utf-8"
        )
        if not shutil.which("bash"):
            self.skipTest("bash not on PATH — shell parity untestable")
        try:
            proc = subprocess.run(
                ["bash", "-c", _SKILL_AUDIT_PENDING_AWK],
                cwd=self.root,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"bash unusable here ({exc!r}) — parity skipped")
        self.assertEqual(cct.count_pending(self.root), int(proc.stdout.strip()))
        self.assertEqual(cct.count_pending(self.root), 3)


class TestWorkSkillCount(_TreeCase):
    def test_org_skills_are_excluded(self):
        for name in ("org-start", "org-curate", "org-delegate"):
            self.add_skill(name)
        for i in range(3):
            self.add_skill(f"work-{i}")
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["work_skill"], 3)

    def test_twenty_work_skills_fire(self):
        for i in range(20):
            self.add_skill(f"work-{i:02d}")
        code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_CURATE_NEEDED)
        self.assertIn("work_skill_count", out["reasons"])

    def test_nested_deeper_than_maxdepth_2_is_ignored(self):
        # find -maxdepth 2 admits .claude/skills/<dir>/SKILL.md only.
        deep = self.root / ".claude" / "skills" / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "SKILL.md").write_text("x\n", encoding="utf-8")
        _, out = self.run_main(self.root)
        self.assertEqual(out["counts"]["work_skill"], 0)

    def test_parity_with_skill_audit_pipeline_on_real_tree(self):
        """M4: the Python count must equal skill-audit Step 1's literal
        shell pipeline when run over the actual repository tree, so the
        two definitions cannot drift."""
        if not shutil.which("bash"):
            self.skipTest("bash not on PATH — shell parity untestable")
        pipeline = (
            "find .claude/skills -maxdepth 2 -name SKILL.md "
            "| grep -v '/org-' | wc -l"
        )
        try:
            # bash being on PATH does not guarantee it can run (e.g.
            # sandboxes where process creation fails with Win32 error
            # 5) — probe by running and skip on any launch/exec error.
            # encoding/errors explicit so a failed bash launch can't
            # leak a _readerthread UnicodeDecodeError into the test log
            # before the skip fires (Codex round 2 Minor).
            proc = subprocess.run(
                ["bash", "-c", pipeline],
                cwd=_REPO_ROOT,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"bash unusable here ({exc!r}) — parity skipped")
        self.assertEqual(cct.count_work_skills(_REPO_ROOT), int(proc.stdout.strip()))


class TestErrorPath(_TreeCase):
    def test_unexpected_failure_exits_2_with_error_json(self):
        buf = io.StringIO()
        with mock.patch.object(
            cct, "evaluate", side_effect=RuntimeError("boom")
        ):
            with redirect_stdout(buf):
                code = cct.main(["--root", str(self.root)])
        self.assertEqual(code, cct.EXIT_ERROR)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["status"], "error")
        self.assertIn("boom", out["error"])

    def test_unreadable_candidates_file_exits_2(self):
        # Contract: only a *missing* file counts as 0. Any other read
        # error must surface as status=error / exit 2, not silently
        # mask a real queue behind a false 0.
        self.add_candidates(pending=5)
        buf = io.StringIO()
        with mock.patch.object(
            Path,
            "read_text",
            side_effect=PermissionError("denied"),
        ):
            with redirect_stdout(buf):
                code = cct.main(["--root", str(self.root)])
        self.assertEqual(code, cct.EXIT_ERROR)
        self.assertEqual(json.loads(buf.getvalue())["status"], "error")

    def test_unreadable_raw_head_exits_2(self):
        # A raw file whose head can't be read (not merely vanished)
        # must not be silently counted as "no legacy marker".
        self.add_raw("note.md")
        buf = io.StringIO()
        with mock.patch.object(
            cct,
            "_has_legacy_marker",
            side_effect=PermissionError("denied"),
        ):
            with redirect_stdout(buf):
                code = cct.main(["--root", str(self.root)])
        self.assertEqual(code, cct.EXIT_ERROR)
        self.assertEqual(json.loads(buf.getvalue())["status"], "error")

    def test_raw_file_vanishing_mid_scan_is_skipped(self):
        # Race with a concurrent archive move: listing saw the file but
        # the head read finds it gone — skip, don't error.
        for i in range(2):
            self.add_raw(f"note-{i}.md")
        with mock.patch.object(
            cct,
            "_has_legacy_marker",
            side_effect=FileNotFoundError("gone"),
        ):
            code, out = self.run_main(self.root)
        self.assertEqual(code, cct.EXIT_BELOW_THRESHOLD)
        self.assertEqual(out["counts"]["raw_active"], 0)
        self.assertEqual(out["counts"]["legacy_marker"], 0)


if __name__ == "__main__":
    unittest.main()
