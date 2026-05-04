"""Unit tests for the M0 state DB schema and importer.

Run with:
    python -m unittest discover -s tools/state_db -p 'test_*.py'
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.state_db import apply_schema, connect
from tools.state_db.importer import (
    ImportSummary,
    MissingInputsError,
    _main,
    dump_signature,
    import_full_rebuild,
)


def _synthetic_inventory_entries() -> list[dict]:
    """Tier-diverse inventory entries with sanitised placeholder paths.

    Replaces the previous on-disk fixture (which leaked absolute developer
    paths into a public OSS repo). Covers project / run / scratch /
    archive_candidate / unknown tiers so the importer's classification
    branches stay exercised.
    """
    return [
        {"name": "demo-project", "abs_path": "/workers/demo-project",
         "git": {"is_repo": True, "is_worktree": False,
                 "origin_url": "https://example.invalid/demo.git",
                 "current_branch": "main"},
         "size_mb": None,
         "proposed_classification": {"tier": "project",
                                      "parent_project": "demo-project",
                                      "parent_workstream": None,
                                      "rationale": "long-lived clone"}},
        {"name": "run-alpha", "abs_path": "/workers/run-alpha",
         "git": {"is_repo": False, "is_worktree": False,
                 "origin_url": None, "current_branch": None},
         "proposed_classification": {"tier": "run",
                                      "parent_project": "demo-project",
                                      "parent_workstream": None,
                                      "rationale": "completed run"}},
        {"name": "run-beta", "abs_path": "/workers/run-beta",
         "git": {"is_repo": True, "is_worktree": True,
                 "origin_url": None, "current_branch": "feature/beta"},
         "proposed_classification": {"tier": "run",
                                      "parent_project": "demo-project",
                                      "parent_workstream": "epic-1",
                                      "rationale": "active worktree"}},
        {"name": "run-gamma", "abs_path": "/workers/run-gamma",
         "git": {"is_repo": False, "is_worktree": False,
                 "origin_url": None, "current_branch": None},
         "proposed_classification": {"tier": "run",
                                      "parent_project": "research",
                                      "parent_workstream": None,
                                      "rationale": "report dir"}},
        {"name": "fizzbuzz", "abs_path": "/workers/fizzbuzz",
         "git": {"is_repo": False, "is_worktree": False,
                 "origin_url": None, "current_branch": None},
         "proposed_classification": {"tier": "scratch",
                                      "parent_project": None,
                                      "parent_workstream": None,
                                      "rationale": "practice"}},
        {"name": "old-pile", "abs_path": "/workers/old-pile",
         "git": {"is_repo": False, "is_worktree": False,
                 "origin_url": None, "current_branch": None},
         "proposed_classification": {"tier": "archive_candidate",
                                      "parent_project": None,
                                      "parent_workstream": None,
                                      "rationale": "mtime > 90d"}},
        {"name": "mystery-dir", "abs_path": "/workers/mystery-dir",
         "git": {"is_repo": False, "is_worktree": False,
                 "origin_url": None, "current_branch": None},
         "proposed_classification": {"tier": "unknown",
                                      "parent_project": None,
                                      "parent_workstream": None,
                                      "rationale": "needs human triage"}},
    ]


def _seed_claude_org_root(root: Path) -> None:
    """Minimal but realistic input tree for the importer."""
    (root / "registry").mkdir(parents=True)
    (root / ".state").mkdir(parents=True)

    (root / "registry" / "projects.md").write_text(
        "# Projects Registry\n\n"
        "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |\n"
        "|---|---|---|---|---|\n"
        "| renga | renga | https://github.com/x/renga | TUI multiplexer | bug fixes |\n"
        "| 時計 | clock-app | - | demo clock | feature work |\n",
        encoding="utf-8",
    )

    (root / ".state" / "org-state.md").write_text(
        "## Worker Directory Registry\n\n"
        "| Task ID | Pattern | Directory | Project | Status |\n"
        "|---|---|---|---|---|\n"
        "| sample-task-1 | B | C:/tmp/workers/sample-task-1/ | clock-app | merged (PR #1) |\n"
        "| sample-task-2 | C | C:/tmp/workers/sample-task-2/ | renga | review |\n"
        "\n"
        "## Active Work Items\n\n"
        "- sample-task-2: in review\n"
        "\n"
        "## 直近完了 Work Items\n\n"
        "- sample-task-1: merged (PR #1)\n",
        encoding="utf-8",
    )

    (root / ".state" / "journal.jsonl").write_text(
        '{"ts":"2026-04-01T00:00:00Z","event":"dispatch","task":"sample-task-1"}\n'
        '{"ts":"2026-04-02T00:00:00Z","event":"complete","task":"sample-task-1"}\n'
        # ts-less line — exercises the sentinel branch (regression for M1).
        '{"event":"orphan","note":"no ts"}\n',
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema(unittest.TestCase):
    def test_apply_schema_creates_all_tables(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "schema.db"
            conn = connect(db)
            try:
                apply_schema(conn)
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                conn.close()
        for expected in (
            "projects", "workstreams", "runs", "worker_dirs", "events",
            "tags", "tag_assignments", "unparsed_legacy", "schema_migrations",
        ):
            self.assertIn(expected, tables)

    def test_workstream_composite_fk_rejects_mismatch(self):
        """runs(workstream_id, project_id) must point at the workstream's project."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "fk.db"
            conn = connect(db)
            try:
                apply_schema(conn)
                conn.execute(
                    "INSERT INTO projects (id, slug, display_name) VALUES (1, 'p1', 'p1')"
                )
                conn.execute(
                    "INSERT INTO projects (id, slug, display_name) VALUES (2, 'p2', 'p2')"
                )
                conn.execute(
                    "INSERT INTO workstreams (id, project_id, slug, display_name) "
                    "VALUES (10, 1, 'ws-a', 'ws-a')"
                )
                conn.commit()
                # workstream 10 belongs to project 1 — point a run at project 2 → must fail.
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO runs (task_id, project_id, workstream_id, "
                        "pattern, title) VALUES ('t', 2, 10, 'C', 't')"
                    )
                    conn.commit()
            finally:
                conn.close()

    def test_archived_generated_column(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "gen.db"
            conn = connect(db)
            try:
                apply_schema(conn)
                conn.execute(
                    "INSERT INTO worker_dirs (abs_path, layout, lifecycle) "
                    "VALUES ('/x/active', 'flat', 'active')"
                )
                conn.execute(
                    "INSERT INTO worker_dirs (abs_path, layout, lifecycle) "
                    "VALUES ('/x/archived', 'flat', 'archived')"
                )
                conn.execute(
                    "INSERT INTO worker_dirs (abs_path, layout, lifecycle) "
                    "VALUES ('/x/pending', 'flat', 'delete_pending')"
                )
                conn.commit()
                rows = dict(
                    conn.execute(
                        "SELECT lifecycle, archived FROM worker_dirs"
                    ).fetchall()
                )
                self.assertEqual(rows["active"], 0)
                self.assertEqual(rows["archived"], 1)
                self.assertEqual(rows["delete_pending"], 1)
            finally:
                conn.close()

    def test_payload_json_check_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "json.db"
            conn = connect(db)
            try:
                apply_schema(conn)
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO events (kind, payload_json) "
                        "VALUES ('x', 'not-json{')"
                    )
                    conn.commit()
                # valid JSON should pass
                conn.execute(
                    "INSERT INTO events (kind, payload_json) "
                    "VALUES ('x', '{\"k\":1}')"
                )
                conn.commit()
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# Importer tests
# ---------------------------------------------------------------------------


class TestImporter(unittest.TestCase):
    def test_import_full_rebuild_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            db = Path(td) / ".state" / "state.db"
            _seed_claude_org_root(root)

            s1 = import_full_rebuild(db, root)
            s2 = import_full_rebuild(db, root)

            self.assertEqual(s1.dump_sha256, s2.dump_sha256)
            self.assertEqual(s1.total_rows, s2.total_rows)
            self.assertEqual(s1.input_lines_total, s2.input_lines_total)

            # No drift after a 3rd run either.
            s3 = import_full_rebuild(db, root)
            self.assertEqual(s1.dump_sha256, s3.dump_sha256)

    def test_import_no_row_dropped_invariant(self):
        """input_lines_total == sum of accounted rows for that input class.

        Per migration-strategy.md M0 DoD, every input line must show up
        somewhere — either as a structured row or in unparsed_legacy.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            db = Path(td) / "state.db"
            _seed_claude_org_root(root)

            summary = import_full_rebuild(db, root)
            # All inputs are accounted for: WDR rows → runs, journal lines →
            # events, Active/recent bullets → events, anything else →
            # unparsed_legacy. projects.md rows account separately.
            accounted = (
                summary.runs_inserted
                + summary.events_inserted
                + summary.projects_inserted
                + summary.unparsed_inserted
            )
            self.assertEqual(accounted, summary.input_lines_total)

    def test_import_round_trip_inventory(self):
        """Synthetic tier-diverse inventory: row counts + sha256 stable across re-imports."""
        entries = _synthetic_inventory_entries()
        n_entries = len(entries)
        self.assertGreater(n_entries, 0)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            (root / "registry").mkdir(parents=True)
            (root / ".state").mkdir(parents=True)
            db = Path(td) / "inventory.db"
            inv = Path(td) / "inventory.json"
            inv.write_text(json.dumps(entries, ensure_ascii=False),
                           encoding="utf-8")

            # strict=False: this fixture intentionally lacks projects.md /
            # org-state.md / journal.jsonl; only inventory.json is provided.
            s1 = import_full_rebuild(db, root, inventory_json=inv, strict=False)
            s2 = import_full_rebuild(db, root, inventory_json=inv, strict=False)

            self.assertEqual(s1.dump_sha256, s2.dump_sha256)
            self.assertEqual(s1.worker_dirs_inserted, n_entries)
            self.assertEqual(s2.worker_dirs_inserted, n_entries)
            self.assertEqual(s1.input_lines_total, n_entries)


class TestInventoryLifecycleMapping(unittest.TestCase):
    """Inventory tier → worker_dirs.lifecycle must follow directory-layout.md
    §5 H5. archive_candidate in particular must land at delete_pending so
    `curator_archive --purge` can pick it up (round 1 review M2)."""

    def test_archive_candidate_maps_to_delete_pending(self):
        entries = _synthetic_inventory_entries()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            (root / "registry").mkdir(parents=True)
            (root / ".state").mkdir(parents=True)
            db = Path(td) / "inv.db"
            inv = Path(td) / "inventory.json"
            inv.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            import_full_rebuild(db, root, inventory_json=inv, strict=False)
            conn = connect(db)
            try:
                rows = {r["abs_path"]: r["lifecycle"]
                        for r in conn.execute("SELECT abs_path, lifecycle FROM worker_dirs")}
            finally:
                conn.close()
            self.assertEqual(rows["/workers/old-pile"], "delete_pending",
                             "archive_candidate → delete_pending (per H5)")
            self.assertEqual(rows["/workers/fizzbuzz"], "scratch")
            self.assertEqual(rows["/workers/run-alpha"], "active")
            self.assertEqual(rows["/workers/demo-project"], "active")
            self.assertEqual(rows["/workers/mystery-dir"], "active")  # unknown → active default


class TestStrictMode(unittest.TestCase):
    def test_strict_raises_on_missing_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            root.mkdir()  # no registry/, no .state/
            db = Path(td) / "strict.db"
            with self.assertRaises(MissingInputsError) as ctx:
                import_full_rebuild(db, root)
            self.assertIn("registry/projects.md", ctx.exception.missing)
            self.assertIn(".state/org-state.md", ctx.exception.missing)
            self.assertIn(".state/journal.jsonl", ctx.exception.missing)
            # DB file must not exist after a strict failure (no DB work done).
            self.assertFalse(db.exists())

    def test_strict_summary_records_inputs_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            db = Path(td) / "ok.db"
            _seed_claude_org_root(root)
            summary = import_full_rebuild(db, root)
            self.assertEqual(summary.inputs_missing, [])
            self.assertIn("registry/projects.md", summary.inputs_found)
            self.assertIn(".state/journal.jsonl", summary.inputs_found)

    def test_cli_strict_default_exits_nonzero_on_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            root.mkdir()
            db = Path(td) / "cli.db"
            rc = _main(["--db", str(db), "--root", str(root), "--rebuild"])
            self.assertEqual(rc, 3)
            self.assertFalse(db.exists())

    def test_cli_no_strict_tolerates_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "claude-org"
            root.mkdir()
            db = Path(td) / "cli.db"
            rc = _main(["--db", str(db), "--root", str(root), "--rebuild",
                         "--no-strict"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
