"""Unit + subprocess tests for tools/state_db/discover.py (Issue #398).

Covers:

* :func:`discover_repo_root` — finds pyproject.toml with claude-org-ja
  marker; resolves a worktree-style ``.git`` file to the main checkout.
* :func:`resolve_state_db_path` — precedence (cli > env > discovery).
* :func:`verify_state_db_schema` — passes on a freshly-applied schema,
  fails on missing file / missing tables / corrupt file.
* End-to-end subprocess invocation of the three CLIs (pr_watch.py,
  journal_append.py, set_run_pr_open.py) from a tmp_path cwd plus a
  worktree-style cwd, asserting each writes to the canonical state.db
  resolved via discovery — not to a stray ``./.state/state.db``.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.discover import (  # noqa: E402
    StateDbSchemaError,
    discover_repo_root,
    resolve_state_db_path,
    verify_state_db_schema,
)


def _make_fake_repo(root: Path) -> Path:
    """Lay out a minimal fake claude-org-ja checkout under ``root``.

    Includes pyproject.toml with the recognised marker, a real ``.git``
    directory (so the worktree-redirect path doesn't trigger), and an
    empty ``.state/`` directory ready for state.db.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        textwrap.dedent('''
            [project]
            name = "claude-org-ja"
            version = "0.0.1"
        ''').strip() + "\n",
        encoding="utf-8",
    )
    (root / ".git").mkdir()
    (root / ".state").mkdir()
    return root


def _make_fake_worktree(main_root: Path, name: str) -> Path:
    """Return a worktree-style checkout pointing at ``main_root``.

    Lays out ``<main>/.git/worktrees/<name>`` (any contents — discover
    only reads parent paths) and a sibling worktree directory containing
    its own pyproject.toml + ``.git`` *file* with a ``gitdir:`` line.
    """
    worktree_internal = main_root / ".git" / "worktrees" / name
    worktree_internal.mkdir(parents=True, exist_ok=True)

    worktree_root = main_root.parent / f"worktree-{name}"
    worktree_root.mkdir(parents=True, exist_ok=True)
    (worktree_root / "pyproject.toml").write_text(
        textwrap.dedent('''
            [project]
            name = "claude-org-ja"
            version = "0.0.1"
        ''').strip() + "\n",
        encoding="utf-8",
    )
    (worktree_root / ".git").write_text(
        f"gitdir: {worktree_internal}\n", encoding="utf-8",
    )
    return worktree_root


class DiscoverRepoRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_finds_marker_via_walk_up(self) -> None:
        repo = _make_fake_repo(self.tmp / "fake-repo")
        nested = repo / "tools" / "state_db"
        nested.mkdir(parents=True)
        result = discover_repo_root(start=nested)
        self.assertEqual(result, repo)

    def test_redirects_worktree_to_main_checkout(self) -> None:
        main = _make_fake_repo(self.tmp / "main-repo")
        wt = _make_fake_worktree(main, "feat-x")
        nested = wt / "tools" / "state_db"
        nested.mkdir(parents=True)
        # Walking up from inside the worktree should resolve to the main
        # checkout, not the worktree root — that's the whole point of
        # Issue #398.
        result = discover_repo_root(start=nested)
        self.assertEqual(result, main)
        self.assertNotEqual(result, wt)

    def test_redirects_worktree_with_relative_gitdir(self) -> None:
        # Codex review Major 1: ``git worktree add --relative-paths``
        # writes a relative ``gitdir:`` line. Discovery has to resolve
        # it relative to the .git file's parent, not cwd, otherwise an
        # invocation from a different cwd would silently fall back to
        # the worktree's own .state/.
        main = _make_fake_repo(self.tmp / "main-rel-repo")
        wt = _make_fake_worktree(main, "feat-rel")
        # Replace the absolute gitdir with a relative one. The expected
        # main_root resolution is independent of cwd.
        worktree_internal = main / ".git" / "worktrees" / "feat-rel"
        rel = os.path.relpath(worktree_internal, wt)
        (wt / ".git").write_text(f"gitdir: {rel}\n", encoding="utf-8")
        nested = wt / "tools"
        nested.mkdir()
        # Switch cwd to an unrelated directory before discovery to prove
        # the relative resolution does not leak cwd.
        prior = os.getcwd()
        try:
            os.chdir(str(self.tmp))
            result = discover_repo_root(start=nested)
        finally:
            os.chdir(prior)
        self.assertEqual(result, main)

    def test_raises_when_no_marker(self) -> None:
        bare = self.tmp / "bare"
        bare.mkdir()
        with self.assertRaises(RuntimeError):
            discover_repo_root(start=bare)

    def test_ignores_pyproject_without_marker(self) -> None:
        # A nearby project with a different name must be skipped — we
        # only want to anchor to claude-org-ja.
        outer = self.tmp / "outer"
        outer.mkdir()
        (outer / "pyproject.toml").write_text(
            '[project]\nname = "other-thing"\n', encoding="utf-8",
        )
        repo = _make_fake_repo(outer / "claude-repo")
        nested = repo / "tools"
        nested.mkdir()
        self.assertEqual(discover_repo_root(start=nested), repo)


class ResolveStateDbPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self._saved_env = os.environ.pop("STATE_DB_PATH", None)

    def tearDown(self) -> None:
        if self._saved_env is not None:
            os.environ["STATE_DB_PATH"] = self._saved_env
        else:
            os.environ.pop("STATE_DB_PATH", None)
        self._td.cleanup()

    def test_explicit_override_wins_over_env(self) -> None:
        explicit = self.tmp / "explicit.db"
        os.environ["STATE_DB_PATH"] = str(self.tmp / "env.db")
        result = resolve_state_db_path(cli_override=explicit)
        self.assertEqual(result, explicit.resolve())

    def test_env_wins_over_discovery(self) -> None:
        env_path = self.tmp / "env.db"
        os.environ["STATE_DB_PATH"] = str(env_path)
        result = resolve_state_db_path()
        self.assertEqual(result, env_path.resolve())

    def test_discovery_fallback(self) -> None:
        # No override + no env → discovery walks up from discover.py and
        # lands on the real checkout's .state/state.db. We don't pin the
        # exact path (varies by checkout) but it must end with the
        # canonical tail.
        result = resolve_state_db_path()
        self.assertEqual(result.parts[-2:], (".state", "state.db"))


class VerifyStateDbSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_missing_file_raises(self) -> None:
        missing = self.tmp / "absent.db"
        with self.assertRaises(StateDbSchemaError) as ctx:
            verify_state_db_schema(missing)
        msg = str(ctx.exception)
        self.assertIn("not found", msg)
        self.assertIn(str(missing), msg)
        self.assertIn("STATE_DB_PATH", msg)

    def test_empty_file_raises(self) -> None:
        empty = self.tmp / "empty.db"
        empty.write_bytes(b"")
        with self.assertRaises(StateDbSchemaError) as ctx:
            verify_state_db_schema(empty)
        self.assertIn("missing required table", str(ctx.exception))

    def test_no_runs_table_raises(self) -> None:
        partial = self.tmp / "partial.db"
        conn = sqlite3.connect(str(partial))
        try:
            conn.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY)"
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(StateDbSchemaError) as ctx:
            verify_state_db_schema(partial)
        self.assertIn("runs", str(ctx.exception))

    def test_corrupt_sqlite_file_raises_schema_error(self) -> None:
        # Codex review Major 2: a non-sqlite file at the resolved path
        # makes ``conn.execute`` raise ``sqlite3.DatabaseError``. Without
        # explicit handling, that propagates as a bare traceback.
        # verify_state_db_schema must wrap it into StateDbSchemaError
        # so callers see a single, actionable failure type.
        corrupt = self.tmp / "corrupt.db"
        corrupt.write_bytes(b"this is not a sqlite database " + b"\x00" * 200)
        with self.assertRaises(StateDbSchemaError) as ctx:
            verify_state_db_schema(corrupt)
        self.assertIn("not a valid sqlite database", str(ctx.exception))

    def test_full_schema_passes(self) -> None:
        good = self.tmp / "good.db"
        conn = connect(good)
        apply_schema(conn)
        conn.close()
        verify_state_db_schema(good)  # should not raise


# ---------------------------------------------------------------------------
# Subprocess integration tests
# ---------------------------------------------------------------------------


class _SubprocessFixture:
    """Helper: spin up a fake repo with a populated state.db and run CLIs.

    Each test gets a fresh tmp containing:

        <tmp>/main-repo/pyproject.toml
        <tmp>/main-repo/.git/                          (real dir)
        <tmp>/main-repo/.state/state.db                (schema applied)
        <tmp>/main-repo/tools/                         (symlinked from real repo)
        <tmp>/main-repo/tools/state_db/                (real subpackage)

    The fake tools/ tree is a copy of the real repo's tools/ + state_db/
    so the python entry points (pr_watch.py, journal_append.py, etc.)
    can resolve their imports against the fake repo's pyproject anchor.
    """

    def __init__(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.main = self.tmp / "main-repo"
        self.main.mkdir()
        # Replicate the marker pyproject.toml so discovery walks land here.
        (self.main / "pyproject.toml").write_text(
            textwrap.dedent('''
                [project]
                name = "claude-org-ja"
                version = "0.0.1"
            ''').strip() + "\n",
            encoding="utf-8",
        )
        # Real .git directory (not a worktree pointer).
        (self.main / ".git").mkdir()
        # Copy the real tools/ tree so Python imports resolve relative to
        # the fake main-repo. We need: tools/__init__.py-or-equivalent,
        # state_db/, plus the four CLI scripts.
        src_tools = REPO_ROOT / "tools"
        dst_tools = self.main / "tools"
        dst_tools.mkdir()
        # Copy state_db package wholesale.
        shutil.copytree(src_tools / "state_db", dst_tools / "state_db")
        # Copy individual CLI scripts + their immediate helper modules.
        for name in (
            "pr_watch.py",
            "journal_append.py",
            "set_run_pr_open.py",
            "run_complete_on_merge.py",
            "peer_notify.py",
        ):
            shutil.copy2(src_tools / name, dst_tools / name)
        # Initialise an empty schema'd state.db at the canonical path.
        self.state_dir = self.main / ".state"
        self.state_dir.mkdir()
        self.state_db = self.state_dir / "state.db"
        conn = connect(self.state_db)
        apply_schema(conn)
        conn.close()

    def cleanup(self) -> None:
        self._td.cleanup()

    def make_worktree(self, name: str, *, mirror_tools: bool = True) -> Path:
        """Create a fake worktree pointing at the main checkout.

        When ``mirror_tools`` is True (default), copies the same tools/
        tree into the worktree — that's what real ``git worktree add``
        produces. Tests that exercise discovery from a worktree-relative
        ``__file__`` need this so the tool script's ``Path(__file__)``
        starts inside ``wt/tools/`` (the actual production scenario for
        Issue #398), not under ``main/tools/``.
        """
        worktree_internal = self.main / ".git" / "worktrees" / name
        worktree_internal.mkdir(parents=True)
        wt = self.tmp / f"worktree-{name}"
        wt.mkdir()
        (wt / "pyproject.toml").write_text(
            textwrap.dedent('''
                [project]
                name = "claude-org-ja"
                version = "0.0.1"
            ''').strip() + "\n",
            encoding="utf-8",
        )
        (wt / ".git").write_text(
            f"gitdir: {worktree_internal}\n", encoding="utf-8",
        )
        # Worktrees have an empty .state/ (modulo workers/ + .gitkeep).
        (wt / ".state").mkdir()
        if mirror_tools:
            shutil.copytree(self.main / "tools", wt / "tools")
        return wt

    def event_count(self, kind: str) -> int:
        conn = sqlite3.connect(str(self.state_db))
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?", (kind,),
            ).fetchone()[0])
        finally:
            conn.close()


class JournalAppendCwdTests(unittest.TestCase):
    """End-to-end: journal_append.py from various cwds writes to the
    canonical state.db."""

    def setUp(self) -> None:
        self.sb = _SubprocessFixture()

    def tearDown(self) -> None:
        self.sb.cleanup()

    def _invoke(self, cwd: Path, *extra: str) -> subprocess.CompletedProcess:
        env = {**os.environ}
        # Strip any STATE_DB_PATH leak from the parent environment so we
        # exercise the discovery path, not an ambient override.
        env.pop("STATE_DB_PATH", None)
        return subprocess.run(
            [
                sys.executable,
                str(self.sb.main / "tools" / "journal_append.py"),
                "worker_progress",
                "task=cwd_test",
                "note=cwd_smoke",
                *extra,
            ],
            cwd=str(cwd), capture_output=True, text=True, env=env, check=False,
        )

    def test_runs_from_main_checkout_cwd(self) -> None:
        proc = self._invoke(self.sb.main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.sb.event_count("worker_progress"), 1)

    def test_runs_from_unrelated_tmp_cwd(self) -> None:
        # cwd is a tmp directory unrelated to the repo. Discovery anchors
        # off __file__, not cwd, so the canonical state.db must still be
        # the write target.
        proc = self._invoke(self.sb.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.sb.event_count("worker_progress"), 1)

    def test_runs_from_worktree_cwd_writes_to_main_state_db(self) -> None:
        # Issue #398 canonical scenario: invocation from
        # `.worktrees/<task>/` must NOT create a stray state.db inside
        # the worktree's empty `.state/`.
        wt = self.sb.make_worktree("feat-cwd-fix")
        proc = self._invoke(wt)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.sb.event_count("worker_progress"), 1)
        worktree_state = wt / ".state" / "state.db"
        self.assertFalse(
            worktree_state.exists(),
            f"worktree state.db must NOT be auto-created; saw {worktree_state}",
        )

    def test_runs_worktree_owned_script_from_worktree_cwd(self) -> None:
        # Codex review Minor: the actual production scenario is
        # ``cd .worktrees/<task> && python tools/journal_append.py …``,
        # i.e. invoking the worktree's own copy of the script. That
        # makes Python's ``Path(__file__)`` start inside the worktree —
        # discovery has to walk up from there and successfully redirect
        # to the main checkout's state.db. Running ``main/tools/...``
        # from the worktree cwd doesn't exercise the same code path.
        wt = self.sb.make_worktree("feat-cwd-fix-owned")
        env = {**os.environ}
        env.pop("STATE_DB_PATH", None)
        env.pop("RENGA_SOCKET", None)
        proc = subprocess.run(
            [
                sys.executable,
                str(wt / "tools" / "journal_append.py"),
                "worker_progress",
                "task=cwd_test_owned",
                "note=worktree_owned_script",
            ],
            cwd=str(wt), capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Event landed in the main state.db — not the worktree's.
        self.assertEqual(self.sb.event_count("worker_progress"), 1)
        worktree_state = wt / ".state" / "state.db"
        self.assertFalse(
            worktree_state.exists(),
            f"worktree state.db must NOT be auto-created; saw {worktree_state}",
        )

    def test_state_db_path_env_override(self) -> None:
        # Explicit env var pointing at a custom DB wins over discovery.
        custom = self.sb.tmp / "custom.db"
        conn = connect(custom)
        apply_schema(conn)
        conn.close()
        env = {**os.environ, "STATE_DB_PATH": str(custom)}
        proc = subprocess.run(
            [
                sys.executable,
                str(self.sb.main / "tools" / "journal_append.py"),
                "worker_progress",
                "task=env_test",
            ],
            cwd=str(self.sb.tmp), capture_output=True, text=True,
            env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Event landed in the env-pointed DB, NOT the canonical one.
        custom_count = sqlite3.connect(str(custom)).execute(
            "SELECT COUNT(*) FROM events WHERE kind='worker_progress'"
        ).fetchone()[0]
        self.assertEqual(custom_count, 1)
        self.assertEqual(self.sb.event_count("worker_progress"), 0)

    def test_db_path_cli_flag_wins_over_env(self) -> None:
        cli_db = self.sb.tmp / "cli.db"
        conn = connect(cli_db)
        apply_schema(conn)
        conn.close()
        env_db = self.sb.tmp / "env.db"
        conn = connect(env_db)
        apply_schema(conn)
        conn.close()
        env = {**os.environ, "STATE_DB_PATH": str(env_db)}
        proc = subprocess.run(
            [
                sys.executable,
                str(self.sb.main / "tools" / "journal_append.py"),
                "worker_progress",
                "task=precedence_test",
                "--db-path", str(cli_db),
            ],
            cwd=str(self.sb.tmp), capture_output=True, text=True,
            env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            sqlite3.connect(str(cli_db)).execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0], 1,
        )
        self.assertEqual(
            sqlite3.connect(str(env_db)).execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0], 0,
        )


class JournalAppendSchemaMismatchTests(unittest.TestCase):
    """Hard-fail behaviour when the resolved DB exists but lacks schema."""

    def setUp(self) -> None:
        self.sb = _SubprocessFixture()

    def tearDown(self) -> None:
        self.sb.cleanup()

    def test_corrupt_state_db_via_env_override_exits_nonzero(self) -> None:
        # Plant an empty file at the env-overridden path so schema is
        # missing. The CLI must surface that with a clear error rather
        # than silently writing nothing or crashing on OperationalError.
        bogus = self.sb.tmp / "bogus.db"
        bogus.write_bytes(b"")
        env = {**os.environ, "STATE_DB_PATH": str(bogus)}
        proc = subprocess.run(
            [
                sys.executable,
                str(self.sb.main / "tools" / "journal_append.py"),
                "worker_progress",
                "task=schema_check",
            ],
            cwd=str(self.sb.tmp), capture_output=True, text=True,
            env=env, check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing required table", proc.stderr)
        self.assertIn("STATE_DB_PATH", proc.stderr)


class PrWatchCwdTests(unittest.TestCase):
    """pr_watch.py picks up the canonical state.db via discovery."""

    def setUp(self) -> None:
        self.sb = _SubprocessFixture()

    def tearDown(self) -> None:
        self.sb.cleanup()

    def _make_fake_gh_dir(self) -> Path:
        """Create a directory containing a fake `gh` shim; prepend to PATH."""
        bin_dir = self.sb.tmp / "fake-bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        # POSIX-only: a shell script that returns canned JSON. Tests are
        # skipped on Windows below.
        gh.write_text(textwrap.dedent('''
            #!/usr/bin/env bash
            # Minimal gh stub: emits canned JSON for pr view / pr checks
            # and exits 0 for pr checks --watch.
            case "$1 $2" in
              "pr view")
                # Fake "PR exists" and "checks" payloads for our test.
                if [[ "$*" == *"--json number"* ]]; then
                  echo '{"number": 4242}'
                fi
                ;;
              "pr checks")
                if [[ "$*" == *"--watch"* ]]; then
                  exit 0
                fi
                if [[ "$*" == *"--json bucket"* ]]; then
                  echo '[{"bucket":"pass","state":"SUCCESS","name":"ci"}]'
                fi
                ;;
              "repo view")
                echo '{"nameWithOwner": "octo/repo"}'
                ;;
            esac
            exit 0
        ''').lstrip())
        gh.chmod(0o755)
        return bin_dir

    def test_runs_from_worktree_cwd_writes_to_main_state_db(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash gh-stub is POSIX-only")
        wt = self.sb.make_worktree("pr-watch-cwd")
        bin_dir = self._make_fake_gh_dir()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        }
        env.pop("STATE_DB_PATH", None)
        # Scrub RENGA_SOCKET so peer_notify silently no-ops — otherwise
        # the test would emit a real "CI_COMPLETED PR #4242" message to
        # a live renga instance (the secretary running this test).
        env.pop("RENGA_SOCKET", None)
        proc = subprocess.run(
            [
                sys.executable,
                str(self.sb.main / "tools" / "pr_watch.py"),
                "--pr", "4242",
                "--repo", "octo/repo",
                "--interval", "1",
            ],
            cwd=str(wt), capture_output=True, text=True,
            env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.sb.event_count("ci_completed"), 1)
        worktree_state = wt / ".state" / "state.db"
        self.assertFalse(
            worktree_state.exists(),
            f"worktree state.db must NOT be auto-created; saw {worktree_state}",
        )


class SetRunPrOpenCwdTests(unittest.TestCase):
    """set_run_pr_open.py picks up the canonical state.db via discovery."""

    def setUp(self) -> None:
        self.sb = _SubprocessFixture()
        # Seed a runs row so the back-fill has something to update.
        from tools.state_db.writer import StateWriter
        conn = connect(self.sb.state_db)
        try:
            with StateWriter(conn).transaction() as w:
                w.upsert_run(
                    task_id="t-cwd",
                    project_slug="claude-org",
                    pattern="B",
                    title="t-cwd",
                    status="review",
                    branch="feat/t-cwd",
                )
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.sb.cleanup()

    def test_runs_from_worktree_cwd_back_fills_main_state_db(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash gh-stub is POSIX-only")
        wt = self.sb.make_worktree("setpr-cwd")
        bin_dir = self.sb.tmp / "fake-bin-setpr"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(textwrap.dedent('''
            #!/usr/bin/env bash
            case "$1 $2" in
              "pr view")
                echo '{"url":"https://github.com/octo/repo/pull/777","headRefName":"feat/t-cwd"}'
                ;;
              "repo view")
                echo '{"nameWithOwner": "octo/repo"}'
                ;;
            esac
            exit 0
        ''').lstrip())
        gh.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        }
        env.pop("STATE_DB_PATH", None)
        proc = subprocess.run(
            [
                sys.executable,
                str(self.sb.main / "tools" / "set_run_pr_open.py"),
                "--task-id", "t-cwd",
                "--pr", "777",
                "--repo", "octo/repo",
            ],
            cwd=str(wt), capture_output=True, text=True,
            env=env, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Verify the canonical state.db has the back-filled pr_url.
        conn = sqlite3.connect(str(self.sb.state_db))
        try:
            row = conn.execute(
                "SELECT pr_url FROM runs WHERE task_id = ?", ("t-cwd",),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "https://github.com/octo/repo/pull/777")
        worktree_state = wt / ".state" / "state.db"
        self.assertFalse(
            worktree_state.exists(),
            f"worktree state.db must NOT be auto-created; saw {worktree_state}",
        )


if __name__ == "__main__":
    unittest.main()
