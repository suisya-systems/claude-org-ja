"""Tests for the shared registry/projects.md parser (Issue #286)."""

import logging
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.registry_parser import (  # noqa: E402
    Project,
    iter_rows,
    parse_projects,
    parse_projects_text,
)


def parse(text: str):  # convenience for in-memory tests
    return parse_projects_text(text)


HEADER = "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |"
SEPARATOR = "|---|---|---|---|---|"


def _build(*body_lines: str, intro: str = "# Projects Registry\n\n") -> str:
    return intro + HEADER + "\n" + SEPARATOR + "\n" + "\n".join(body_lines) + "\n"


class TestParseProjects(unittest.TestCase):

    def test_real_registry_fixture(self):
        # Parses a checked-in fixture (not the live registry/projects.md), since
        # the live registry is divergence-allowed across ja / en / forks per
        # docs/sync-policy.md. The fixture pins a known project list so this
        # test stays deterministic regardless of the host repo's roster.
        path = PROJECT_ROOT / "tests" / "fixtures" / "registry" / "projects.md"
        projects = parse_projects(path)
        names = [p.name for p in projects]
        self.assertIn("clock-app", names)
        self.assertIn("renga", names)
        self.assertNotIn("claude-org-ja", names)
        # All rows are fully populated Project instances.
        for p in projects:
            self.assertIsInstance(p, Project)
            self.assertTrue(p.nickname)
            self.assertTrue(p.name)

    def test_live_registry_smoke(self):
        # Smoke check: the live registry/projects.md (which may diverge per
        # repo/fork) must remain parseable and yield at least one well-formed
        # row. We do NOT assert specific project names here — that is the
        # fixture-based test's job.
        path = PROJECT_ROOT / "registry" / "projects.md"
        if not path.exists():  # pragma: no cover - safety for fork checkouts
            self.skipTest("live registry/projects.md not present")
        # parse_projects must not raise and must not emit malformed-row
        # warnings against the live file. An empty registry is valid (fresh
        # checkout / fork) — but if the file *contains* table data rows, at
        # least one must parse cleanly, otherwise the registry is silently
        # corrupt (e.g. separator dropped → every row classified as
        # non_table and parser returns []).
        text = path.read_text(encoding="utf-8")
        with self.assertNoLogs("tools.registry_parser", level="WARNING"):
            projects = parse_projects(path)
        for p in projects:
            self.assertIsInstance(p, Project)
            self.assertTrue(p.nickname)
            self.assertTrue(p.name)
        # Detect "looks like a markdown table" by counting pipe-prefixed
        # lines, not by iter_rows() kinds: when the separator row is missing
        # iter_rows classifies every `| ... |` line (header AND would-be
        # data rows) as "header", so a kind-based check would silently miss
        # exactly the corruption mode we want to catch.
        pipe_lines = sum(
            1 for ln in text.splitlines() if ln.lstrip().startswith("|")
        )
        if pipe_lines >= 2:
            self.assertGreater(
                len(projects), 0,
                "live registry has pipe-table lines but none parsed — "
                "likely separator/header corruption",
            )

    def test_happy_path(self):
        text = _build(
            "| 時計アプリ | clock-app | apps/clock | demo clock | a、b |",
            "| ブログ | blog-site | sites/blog | blog | c |",
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[0].nickname, "時計アプリ")
        self.assertEqual(projects[0].name, "clock-app")
        self.assertEqual(projects[0].path, "apps/clock")
        self.assertEqual(projects[0].common_tasks, "a、b")
        self.assertEqual(projects[1].name, "blog-site")

    def test_no_trailing_newline(self):
        text = _build("| n | slug | / | d | t |").rstrip("\n")
        self.assertEqual(parse_projects_text(text)[0].name, "slug")

    def test_crlf_line_endings(self):
        text = _build("| n | slug | / | d | t |").replace("\n", "\r\n")
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "slug")

    def test_bom_prefix(self):
        text = "﻿" + _build("| n | slug | / | d | t |")
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "slug")

    def test_malformed_row_graceful_skip(self):
        text = _build(
            "| n | slug | / | d | t |",
            "| only | three | cols |",  # too few cells
            "| n2 | slug2 | / | d2 | t2 |",
        )
        with self.assertLogs("tools.registry_parser", level="WARNING") as cm:
            projects = parse_projects_text(text)
        self.assertEqual([p.name for p in projects], ["slug", "slug2"])
        self.assertTrue(any("malformed row" in m for m in cm.output))

    def test_empty_text(self):
        self.assertEqual(parse_projects_text(""), [])

    def test_header_only_no_data(self):
        text = HEADER + "\n" + SEPARATOR + "\n"
        self.assertEqual(parse_projects_text(text), [])

    def test_four_column_table_still_parses(self):
        # Regression for Codex review M: the legacy resolver accepted 4-col
        # tables (no 「よくある作業例」 column). Hand-edited registries in the
        # wild may omit the 5th column; we must not silently drop them.
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 |\n"
            "|---|---|---|---|\n"
            "| 時計 | clock-app | - | Demo |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "clock-app")
        self.assertEqual(projects[0].common_tasks, "")

    def test_empty_optional_fifth_column(self):
        text = _build("| n | slug | / | d |  |")
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].common_tasks, "")

    def test_accepts_path_input(self):
        path = PROJECT_ROOT / "tests" / "fixtures" / "projects-sample.md"
        projects = parse_projects(path)
        names = [p.name for p in projects]
        self.assertIn("clock-app", names)
        self.assertIn("blog-site", names)


class TestMirrorOfColumn(unittest.TestCase):
    """Issue #374: optional 6th column ``mirror_of`` carries a project slug
    reference for back-port style mirrors. Backwards compatibility must
    hold for fork registries that haven't adopted the column yet."""

    def test_six_column_table_populates_mirror_of(self):
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | mirror_of |\n"
            "|---|---|---|---|---|---|\n"
            "| EN ミラー | my-mirror | "
            "https://github.com/example/mirror | mirror | - | upstream-slug |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "my-mirror")
        self.assertEqual(projects[0].mirror_of, "upstream-slug")

    def test_legacy_five_column_table_leaves_mirror_of_empty(self):
        text = _build("| n | slug | / | d | t |")
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].mirror_of, "")

    def test_extra_trailing_columns_are_ignored_not_rejected(self):
        # Future column additions or fork-side experiments must not silently
        # drop every row. iter_rows accepts trailing cells beyond the schema.
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | mirror_of | future_col |\n"
            "|---|---|---|---|---|---|---|\n"
            "| n | slug | / | d | t | upstream | extra-value |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "slug")
        # The mirror_of column is still populated from cell index 5.
        self.assertEqual(projects[0].mirror_of, "upstream")

    def test_six_column_empty_mirror_of_cell_normalizes_to_empty(self):
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | mirror_of |\n"
            "|---|---|---|---|---|---|\n"
            "| n | slug | / | d | t |  |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(projects[0].mirror_of, "")


class TestHeaderNameParsing(unittest.TestCase):
    """Issue #729: columns are resolved by header *name*, not position, so
    reordering columns or inserting a new `triage` column does not shift the
    meaning of existing columns. Headerless / alias-less tables still fall
    back to positional parsing for backwards compatibility."""

    def test_triage_column_populates_raw_value(self):
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |\n"
            "|---|---|---|---|---|---|\n"
            "| 時計 | clock-app | - | Demo | tasks | yes |\n"
            "| ブログ | blog | https://github.com/x/blog | B | t | no |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 2)
        # Raw cell value is preserved verbatim; the parser does NOT interpret
        # opt-in semantics (that lives in the resolver).
        self.assertEqual(projects[0].triage, "yes")
        self.assertEqual(projects[1].triage, "no")

    def test_triage_absent_defaults_to_empty(self):
        # A 5-column header with no `triage` column leaves triage="".
        text = _build("| 時計 | clock-app | - | Demo | tasks |")
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].triage, "")

    def test_triage_raw_values_preserved_various(self):
        # The parser keeps the literal cell — case, `-`, and empty are all
        # passed through unchanged; interpretation is the resolver's job.
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |\n"
            "|---|---|---|---|---|---|\n"
            "| a | slug-a | - | d | t | TRUE |\n"
            "| b | slug-b | - | d | t | On |\n"
            "| c | slug-c | - | d | t | - |\n"
            "| d | slug-d | - | d | t |  |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(
            [p.triage for p in projects], ["TRUE", "On", "-", ""]
        )

    def test_column_reorder_resilience(self):
        # triage first, description before path — header mode must map each
        # column by name regardless of order.
        text = (
            "| triage | 通称 | 説明 | プロジェクト名 | パス | よくある作業例 |\n"
            "|---|---|---|---|---|---|\n"
            "| yes | 時計 | Demo | clock-app | https://github.com/x/c | t |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        p = projects[0]
        self.assertEqual(p.nickname, "時計")
        self.assertEqual(p.name, "clock-app")
        self.assertEqual(p.path, "https://github.com/x/c")
        self.assertEqual(p.description, "Demo")
        self.assertEqual(p.common_tasks, "t")
        self.assertEqual(p.triage, "yes")

    def test_english_header_aliases(self):
        text = (
            "| 通称 | name | path | description | よくある作業例 | triage |\n"
            "|---|---|---|---|---|---|\n"
            "| nn | slug | /p | desc | t | on |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "slug")
        self.assertEqual(projects[0].path, "/p")
        self.assertEqual(projects[0].description, "desc")
        self.assertEqual(projects[0].triage, "on")

    def test_fully_english_header_falls_back_to_positional(self):
        # Regression (Codex P2): a legacy/fork English header with no 通称
        # column must NOT enter header mode — its `Name` alias would grab the
        # slug field from column 0 and drop the real nickname. The header map
        # lacks the nickname identity column, so we fall back to positional
        # parsing, preserving the pre-#729 column meaning (col1 = slug).
        text = (
            "| Name | Project | Path | Description | Tasks |\n"
            "|---|---|---|---|---|\n"
            "| Clock App | clock-app | /tmp/clock | Demo | task |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        p = projects[0]
        self.assertEqual(p.nickname, "Clock App")
        self.assertEqual(p.name, "clock-app")  # slug from col1, not "Clock App"
        self.assertEqual(p.path, "/tmp/clock")
        self.assertEqual(p.description, "Demo")

    def test_header_alias_case_insensitive(self):
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | TRIAGE |\n"
            "|---|---|---|---|---|---|\n"
            "| nn | slug | - | d | t | yes |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(projects[0].triage, "yes")

    def test_positional_fallback_when_header_matches_no_alias(self):
        # Header row uses no known alias -> positional mode. triage is never
        # populated positionally, even for a 6th column.
        text = (
            "| a | b | c | d | e | f |\n"
            "|---|---|---|---|---|---|\n"
            "| 時計 | clock-app | - | Demo | tasks | ignored |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(len(projects), 1)
        p = projects[0]
        self.assertEqual(p.nickname, "時計")
        self.assertEqual(p.name, "clock-app")
        self.assertEqual(p.path, "-")
        self.assertEqual(p.description, "Demo")
        self.assertEqual(p.common_tasks, "tasks")
        # 6th positional column is mirror_of, NOT triage.
        self.assertEqual(p.mirror_of, "ignored")
        self.assertEqual(p.triage, "")

    def test_triage_and_mirror_of_coexist_by_name(self):
        # When both columns are present, each is mapped by its own header,
        # so triage does not leak into mirror_of (the position-collision bug
        # that motivated header-name parsing).
        text = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | mirror_of | triage |\n"
            "|---|---|---|---|---|---|---|\n"
            "| nn | slug | - | d | t | upstream | yes |\n"
        )
        projects = parse_projects_text(text)
        self.assertEqual(projects[0].mirror_of, "upstream")
        self.assertEqual(projects[0].triage, "yes")

    def test_live_registry_triage_column_parses(self):
        # The live registry now carries a triage column; every data row must
        # parse and expose a triage value (default "no").
        path = PROJECT_ROOT / "registry" / "projects.md"
        if not path.exists():  # pragma: no cover
            self.skipTest("live registry/projects.md not present")
        projects = parse_projects(path)
        for p in projects:
            # Every live row is currently opted-out.
            self.assertEqual(p.triage.strip().lower(), "no")


class TestIterRows(unittest.TestCase):

    def test_classifies_lines(self):
        text = _build(
            "| n | slug | / | d | t |",
            "| only | three |",
        )
        kinds = [r.kind for r in iter_rows(text)]
        # intro + blank + header + separator + data + mismatch
        self.assertIn("header", kinds)
        self.assertIn("separator", kinds)
        self.assertIn("data", kinds)
        self.assertIn("mismatch", kinds)
        self.assertIn("non_table", kinds)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.WARNING)
    unittest.main()
