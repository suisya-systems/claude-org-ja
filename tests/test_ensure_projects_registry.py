"""Tests for the registry/projects.md generator (Issue #811).

The load-bearing properties, in order of how much damage their violation
would cause:

1. An existing ``registry/projects.md`` is NEVER overwritten. It holds the
   operator's live roster; clobbering it is the exact failure this migration
   exists to prevent.
2. Template-only prose never leaks into the generated file (marker split).
3. Header drift is detected, so a column added to the template after an
   operator already has a local file still reaches them.
"""

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ensure_projects_registry import (  # noqa: E402
    LIVE_MARKER,
    STATUS_CREATED,
    STATUS_HEADER_DRIFT,
    STATUS_MISSING,
    STATUS_OK,
    EnsureError,
    compare_headers,
    ensure_projects_registry,
    extract_header_columns,
    extract_live_body,
)
from tools.registry_parser import parse_projects  # noqa: E402

TOOL = PROJECT_ROOT / "tools" / "ensure_projects_registry.py"

_TEMPLATE = (
    "# Projects Registry (Template)\n"
    "\n"
    "テンプレート専用の説明。実体ファイルに漏れてはならない。\n"
    "\n"
    + LIVE_MARKER + "\n"
    "# Projects Registry\n"
    "\n"
    "実体ファイルの仕様説明。\n"
    "\n"
    "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage "
    "| base_branch |\n"
    "|---|---|---|---|---|---|---|\n"
    "| 時計アプリ | clock-app | - | デジタル時計 | デザイン変更 | no | |\n"
)

# A pre-#808 local registry: same table, no base_branch column.
_OLD_LIVE = (
    "# Projects Registry\n"
    "\n"
    "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |\n"
    "|---|---|---|---|---|---|\n"
    "| 社内案件 | operator-only | - | operator 固有 | 改修 | no |\n"
)


class _Sandbox:
    """A throwaway repo root with registry/ laid out."""

    def __init__(self, stack: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory()
        stack.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "registry").mkdir()

    def write_template(self, text: str = _TEMPLATE) -> Path:
        p = self.root / "registry" / "projects.example.md"
        p.write_text(text, encoding="utf-8")
        return p

    def write_live(self, text: str) -> Path:
        p = self.root / "registry" / "projects.md"
        p.write_text(text, encoding="utf-8")
        return p

    @property
    def live(self) -> Path:
        return self.root / "registry" / "projects.md"


class TestExtractLiveBody(unittest.TestCase):

    def test_splits_at_marker(self):
        body = extract_live_body(_TEMPLATE)
        self.assertTrue(body.startswith("# Projects Registry\n"))
        self.assertNotIn("テンプレート専用の説明", body)
        self.assertNotIn(LIVE_MARKER, body)

    def test_missing_marker_is_fatal(self):
        # Falling back to "copy the whole template" would embed template-only
        # prose into the live registry, so this must fail loud.
        with self.assertRaises(EnsureError) as ctx:
            extract_live_body("# No marker here\n\n| a |\n|---|\n")
        self.assertIn("marker", str(ctx.exception))

    def test_empty_body_after_marker_is_fatal(self):
        with self.assertRaises(EnsureError):
            extract_live_body("# T\n" + LIVE_MARKER + "\n\n   \n")


class TestExtractHeaderColumns(unittest.TestCase):

    def test_reads_first_table_header(self):
        cols = extract_header_columns(_TEMPLATE)
        self.assertEqual(
            cols,
            ["通称", "プロジェクト名", "パス", "説明", "よくある作業例",
             "triage", "base_branch"],
        )

    def test_no_table_returns_none(self):
        # A table-less registry is legitimate (fresh fork), not an error.
        self.assertIsNone(extract_header_columns("# Projects\n\nprose only\n"))

    def test_prose_pipes_do_not_fake_a_header(self):
        # A line starting with '|' but not followed by a separator row must
        # not be mistaken for a header.
        text = "| not a table\nstill prose\n"
        self.assertIsNone(extract_header_columns(text))

    def test_bom_and_crlf(self):
        text = "﻿| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n"
        self.assertEqual(extract_header_columns(text), ["a", "b"])


class TestCompareHeaders(unittest.TestCase):
    """compare_headers takes the marked live BODY, never the whole template."""

    def setUp(self):
        self.body = extract_live_body(_TEMPLATE)

    def test_detects_column_added_to_template(self):
        missing, extra = compare_headers(self.body, _OLD_LIVE)
        self.assertEqual(missing, ["base_branch"])
        self.assertEqual(extra, [])

    def test_identical_headers_have_no_drift(self):
        missing, extra = compare_headers(self.body, self.body)
        self.assertEqual((missing, extra), ([], []))

    def test_ignores_a_table_in_the_template_only_preamble(self):
        # A column-summary table above the marker must not be mistaken for
        # the registry schema: comparing against it would both invent drift
        # and hide real additions. ensure_projects_registry is the contract
        # under test here, since it owns the body extraction.
        sb = _Sandbox(self)
        sb.write_template(
            "# Projects Registry (Template)\n"
            "\n"
            "列の早見表:\n"
            "\n"
            "| 列 | 意味 |\n"
            "|---|---|\n"
            "| triage | scan 対象か |\n"
            "\n"
            + _TEMPLATE[_TEMPLATE.index(LIVE_MARKER):]
        )
        sb.write_live(extract_live_body(_TEMPLATE))
        result = ensure_projects_registry(sb.root)
        self.assertEqual(
            result.status, STATUS_OK,
            "the preamble table must not be compared against the registry",
        )

    def test_reordered_local_header_is_not_drift(self):
        # The parser maps columns by name, so order carries no meaning.
        reordered = (
            "| base_branch | triage | よくある作業例 | 説明 | パス "
            "| プロジェクト名 | 通称 |\n"
            "|---|---|---|---|---|---|---|\n"
        )
        missing, extra = compare_headers(self.body, reordered)
        self.assertEqual((missing, extra), ([], []))

    def test_case_insensitive(self):
        live = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | TRIAGE "
            "| Base_Branch |\n|---|---|---|---|---|---|---|\n"
        )
        missing, _ = compare_headers(self.body, live)
        self.assertEqual(missing, [])

    def test_local_only_column_reported_as_extra(self):
        live = (
            "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage "
            "| base_branch | owner |\n|---|---|---|---|---|---|---|---|\n"
        )
        missing, extra = compare_headers(self.body, live)
        self.assertEqual(missing, [])
        self.assertEqual(extra, ["owner"])


class TestEnsure(unittest.TestCase):

    def test_creates_when_absent(self):
        sb = _Sandbox(self)
        sb.write_template()
        result = ensure_projects_registry(sb.root)
        self.assertEqual(result.status, STATUS_CREATED)
        self.assertTrue(sb.live.is_file())
        text = sb.live.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Projects Registry\n"))
        self.assertNotIn("テンプレート専用の説明", text)

    def test_generated_file_is_parseable(self):
        # The whole point is that readers keep working unchanged.
        sb = _Sandbox(self)
        sb.write_template()
        ensure_projects_registry(sb.root)
        projects = parse_projects(sb.live)
        self.assertEqual([p.name for p in projects], ["clock-app"])

    def test_never_overwrites_existing(self):
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(_OLD_LIVE)
        ensure_projects_registry(sb.root)
        self.assertIn(
            "operator-only", sb.live.read_text(encoding="utf-8"),
            "an existing registry must survive verbatim",
        )

    def test_reports_header_drift_on_existing(self):
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(_OLD_LIVE)
        result = ensure_projects_registry(sb.root)
        self.assertEqual(result.status, STATUS_HEADER_DRIFT)
        self.assertEqual(result.missing_columns, ["base_branch"])

    def test_up_to_date_existing_is_ok(self):
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(extract_live_body(_TEMPLATE))
        self.assertEqual(ensure_projects_registry(sb.root).status, STATUS_OK)

    def test_idempotent(self):
        sb = _Sandbox(self)
        sb.write_template()
        first = ensure_projects_registry(sb.root)
        content = sb.live.read_text(encoding="utf-8")
        second = ensure_projects_registry(sb.root)
        self.assertEqual(first.status, STATUS_CREATED)
        self.assertEqual(second.status, STATUS_OK)
        self.assertEqual(content, sb.live.read_text(encoding="utf-8"))

    def test_does_not_clobber_a_file_that_appears_mid_run(self):
        # The is_file() probe and the write are not atomic, and /org-start and
        # /org-setup both call this. Simulate losing that race by making the
        # probe report "absent" for a file that actually exists: an exclusive
        # create must refuse rather than truncate the winner's rows.
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(_OLD_LIVE)

        real_is_file = Path.is_file

        def lying_is_file(self):
            if self.name == "projects.md":
                return False
            return real_is_file(self)

        with unittest.mock.patch.object(Path, "is_file", lying_is_file):
            result = ensure_projects_registry(sb.root)

        self.assertIn(
            "operator-only", sb.live.read_text(encoding="utf-8"),
            "a concurrent create must not truncate the existing registry",
        )
        # Having lost the race, the run reports on the file that won.
        self.assertEqual(result.status, STATUS_HEADER_DRIFT)

    def test_failed_write_leaves_no_partial_file(self):
        # A partial file would be preserved forever by the never-overwrite
        # rule, so a failed create must not leave one behind.
        sb = _Sandbox(self)
        sb.write_template()

        real_open = Path.open

        def failing_open(self, *args, **kwargs):
            fh = real_open(self, *args, **kwargs)
            if self.name == "projects.md" and args and args[0] == "x":
                fh.close()
                # Reopened for write, then blown up mid-write.
                with real_open(self, "w", encoding="utf-8") as partial:
                    partial.write("partial")
                raise OSError(28, "No space left on device")
            return fh

        with unittest.mock.patch.object(Path, "open", failing_open):
            with self.assertRaises(OSError):
                ensure_projects_registry(sb.root)

        self.assertFalse(
            sb.live.exists(),
            "a failed create must not leave a partial registry behind",
        )

    def test_tableless_local_file_is_reported_not_silently_ok(self):
        # Defence in depth for the same failure: if a table-less file does
        # end up in place, the tool must say so rather than report "ok" and
        # leave every reader parsing zero projects.
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live("# Projects Registry\n\n")
        result = ensure_projects_registry(sb.root)
        self.assertEqual(result.status, STATUS_HEADER_DRIFT)
        self.assertIn("base_branch", result.missing_columns)
        self.assertIn("通称", result.missing_columns)

    def test_check_only_does_not_create(self):
        sb = _Sandbox(self)
        sb.write_template()
        result = ensure_projects_registry(sb.root, check_only=True)
        self.assertEqual(result.status, STATUS_MISSING)
        self.assertFalse(sb.live.exists())

    def test_missing_template_is_fatal(self):
        sb = _Sandbox(self)
        with self.assertRaises(EnsureError):
            ensure_projects_registry(sb.root)

    def test_body_without_a_table_header_is_fatal(self):
        # Generating a registry every reader parses as zero projects would be
        # broken but silent, and would still report "created". Fail loud.
        sb = _Sandbox(self)
        sb.write_template(
            "# T\n" + LIVE_MARKER + "\n# Projects Registry\n\n説明だけ。\n"
        )
        with self.assertRaises(EnsureError) as ctx:
            ensure_projects_registry(sb.root)
        self.assertIn("table header", str(ctx.exception))
        self.assertFalse(
            sb.live.exists(), "no unusable registry may be left behind"
        )

    def test_malformed_body_reported_even_when_live_file_exists(self):
        # Validation runs before the create/compare split, so a broken
        # template is caught on every machine, not only on a fresh checkout.
        sb = _Sandbox(self)
        sb.write_template("# T\n" + LIVE_MARKER + "\n# Projects Registry\n\nx\n")
        sb.write_live(_OLD_LIVE)
        with self.assertRaises(EnsureError):
            ensure_projects_registry(sb.root)
        self.assertIn("operator-only", sb.live.read_text(encoding="utf-8"))

    def test_creates_registry_dir_when_absent(self):
        # Fresh clone where registry/ somehow doesn't exist yet.
        sb = _Sandbox(self)
        sb.write_template()
        nested = sb.root / "nested"
        (nested / "registry").mkdir(parents=True)
        (nested / "registry" / "projects.example.md").write_text(
            _TEMPLATE, encoding="utf-8"
        )
        result = ensure_projects_registry(nested)
        self.assertEqual(result.status, STATUS_CREATED)


class TestShippedTemplate(unittest.TestCase):
    """The template actually committed to this repo must satisfy the
    contract -- otherwise a fresh clone generates a broken registry."""

    def setUp(self):
        self.path = PROJECT_ROOT / "registry" / "projects.example.md"
        self.text = self.path.read_text(encoding="utf-8")

    def test_has_marker_and_body(self):
        body = extract_live_body(self.text)
        self.assertTrue(body.lstrip().startswith("# Projects Registry"))

    def test_body_parses_into_projects(self):
        projects = parse_projects(self.path)
        self.assertTrue(projects, "shipped template must yield sample rows")
        for p in projects:
            self.assertTrue(p.nickname)
            self.assertTrue(p.name)

    def test_declares_the_current_column_schema(self):
        cols = [c.lower() for c in extract_header_columns(self.text) or []]
        for required in ("通称", "プロジェクト名", "パス", "説明",
                         "よくある作業例", "triage", "base_branch"):
            self.assertIn(required.lower(), cols)

    # Sample slugs the template is allowed to ship. Deliberately an
    # ALLOWLIST, not a denylist of real operator project names: spelling out
    # "do not commit customer X" in a tracked test would itself leak the
    # identifiers this issue exists to keep out of history.
    _ALLOWED_SAMPLE_SLUGS = {"clock-app", "renga", "sample-two-track"}

    def test_carries_only_generic_sample_rows(self):
        # Guards the regression this issue was filed for: an operator's real
        # roster must never reach the tracked template. Any row whose slug is
        # not a declared sample fails — so adding a real project here is
        # caught even though the test never names one.
        slugs = {p.name for p in parse_projects(self.path)}
        unexpected = slugs - self._ALLOWED_SAMPLE_SLUGS
        self.assertEqual(
            unexpected, set(),
            "the tracked template must carry only generic sample rows; "
            "found non-sample slug(s). If you are adding a new SAMPLE row, "
            "add its slug to _ALLOWED_SAMPLE_SLUGS. If this is a real "
            "project, it belongs in your operator-local registry/projects.md "
            "instead.",
        )

    def test_sample_rows_use_no_real_repo_host_paths(self):
        # Sample GitHub URLs must point at the project's own public repos or
        # the reserved example.com-style placeholder org, never at an
        # operator's organisation.
        allowed_owners = {"suisya-systems", "example"}
        for p in parse_projects(self.path):
            path = p.path.strip()
            if not path.startswith("https://github.com/"):
                continue
            owner = path[len("https://github.com/"):].split("/")[0]
            self.assertIn(
                owner, allowed_owners,
                f"sample row {p.name!r} points at GitHub owner {owner!r}; "
                "template rows must not reference an operator's org",
            )


class TestCli(unittest.TestCase):

    def _run(self, sb, *args):
        return subprocess.run(
            [sys.executable, str(TOOL), "--root", str(sb.root), *args],
            capture_output=True, text=True,
        )

    def test_json_output_on_create(self):
        sb = _Sandbox(self)
        sb.write_template()
        proc = self._run(sb, "--json")
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], STATUS_CREATED)

    def test_drift_is_a_warning_by_default(self):
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(_OLD_LIVE)
        proc = self._run(sb, "--json")
        self.assertEqual(proc.returncode, 0, "drift must not block /org-start")
        self.assertEqual(json.loads(proc.stdout)["status"], STATUS_HEADER_DRIFT)

    def test_strict_exits_3_on_drift(self):
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(_OLD_LIVE)
        self.assertEqual(self._run(sb, "--strict").returncode, 3)

    def test_strict_exits_0_when_clean(self):
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(extract_live_body(_TEMPLATE))
        self.assertEqual(self._run(sb, "--strict").returncode, 0)

    def test_missing_template_exits_1(self):
        sb = _Sandbox(self)
        proc = self._run(sb)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("error", proc.stderr)

    def test_human_output_names_the_missing_columns(self):
        sb = _Sandbox(self)
        sb.write_template()
        sb.write_live(_OLD_LIVE)
        proc = self._run(sb)
        self.assertIn("base_branch", proc.stdout)


if __name__ == "__main__":
    unittest.main()
