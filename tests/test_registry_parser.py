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
)


HEADER = "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |"
SEPARATOR = "|---|---|---|---|---|"


def _build(*body_lines: str, intro: str = "# Projects Registry\n\n") -> str:
    return intro + HEADER + "\n" + SEPARATOR + "\n" + "\n".join(body_lines) + "\n"


class TestParseProjects(unittest.TestCase):

    def test_real_registry_fixture(self):
        path = PROJECT_ROOT / "registry" / "projects.md"
        projects = parse_projects(path)
        # The actual checked-in registry has at least the 3 well-known
        # projects (clock-app, renga, claude-org-ja).
        names = [p.name for p in projects]
        self.assertIn("clock-app", names)
        self.assertIn("renga", names)
        self.assertIn("claude-org-ja", names)
        # All rows are fully populated Project instances.
        for p in projects:
            self.assertIsInstance(p, Project)
            self.assertTrue(p.nickname)
            self.assertTrue(p.name)

    def test_happy_path(self):
        text = _build(
            "| 時計アプリ | clock-app | apps/clock | demo clock | a、b |",
            "| ブログ | blog-site | sites/blog | blog | c |",
        )
        projects = parse_projects(text)
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[0].nickname, "時計アプリ")
        self.assertEqual(projects[0].name, "clock-app")
        self.assertEqual(projects[0].path, "apps/clock")
        self.assertEqual(projects[0].common_tasks, "a、b")
        self.assertEqual(projects[1].name, "blog-site")

    def test_no_trailing_newline(self):
        text = _build("| n | slug | / | d | t |").rstrip("\n")
        self.assertEqual(parse_projects(text)[0].name, "slug")

    def test_crlf_line_endings(self):
        text = _build("| n | slug | / | d | t |").replace("\n", "\r\n")
        projects = parse_projects(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "slug")

    def test_bom_prefix(self):
        text = "﻿" + _build("| n | slug | / | d | t |")
        projects = parse_projects(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "slug")

    def test_malformed_row_graceful_skip(self):
        text = _build(
            "| n | slug | / | d | t |",
            "| only | three | cols |",  # too few cells
            "| n2 | slug2 | / | d2 | t2 |",
        )
        with self.assertLogs("tools.registry_parser", level="WARNING") as cm:
            projects = parse_projects(text)
        self.assertEqual([p.name for p in projects], ["slug", "slug2"])
        self.assertTrue(any("malformed row" in m for m in cm.output))

    def test_empty_text(self):
        self.assertEqual(parse_projects(""), [])

    def test_header_only_no_data(self):
        text = HEADER + "\n" + SEPARATOR + "\n"
        self.assertEqual(parse_projects(text), [])

    def test_empty_optional_fifth_column(self):
        text = _build("| n | slug | / | d |  |")
        projects = parse_projects(text)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].common_tasks, "")

    def test_accepts_path_input(self):
        path = PROJECT_ROOT / "tests" / "fixtures" / "projects-sample.md"
        projects = parse_projects(path)
        names = [p.name for p in projects]
        self.assertIn("clock-app", names)
        self.assertIn("blog-site", names)


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
