"""Tests for tools/resolve_run_repo.py and its two CLI consumers (Issue #828).

The headline case is the 2026-08-06 incident: recording renga PR #302 onto
``renga-296-focused-resolution`` with ``--repo`` omitted made ``gh pr view
302`` read **ja's** PR #302, and ja's branch / commit / mergedAt were written
onto the renga run while the helper still printed ``ok``. Whether that
corrupted silently or failed loudly depended only on whether ja happened to
own that number, so the regression tests below always stand up *both* repos
with the same PR number and assert the foreign run picked up the foreign
repo's metadata -- and that ja's own run was left alone.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_complete_on_merge  # noqa: E402  (tools/run_complete_on_merge.py)
import set_run_pr_open  # noqa: E402  (tools/set_run_pr_open.py)
from tools import resolve_run_repo  # noqa: E402
from tools.state_db import apply_schema, connect  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402


# --- the 2026-08-06 incident, verbatim -------------------------------------
PR_NUMBER = 302

JA_REPO = "suisya-systems/claude-org-ja"
JA_TASK = "issue-297-pending-register"
JA_BRANCH = "feat/issue-297-pending-register"
JA_URL = f"https://github.com/{JA_REPO}/pull/{PR_NUMBER}"
JA_MERGED_AT = "2026-05-05T07:17:15Z"
JA_MERGE_OID = "262b3939393939393939393939393939393939393"

RENGA_REPO = "suisya-systems/renga"
RENGA_TASK = "renga-296-focused-resolution"
RENGA_BRANCH = "feat/renga-296-focused-resolution"
RENGA_URL = f"https://github.com/{RENGA_REPO}/pull/{PR_NUMBER}"
RENGA_MERGED_AT = "2026-08-06T02:00:00Z"
RENGA_MERGE_OID = "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0"

REGISTRY_MD = (
    "# Projects\n\n"
    "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |\n"
    "|---|---|---|---|---|---|\n"
    f"| renga | renga | https://github.com/{RENGA_REPO} | TUI | 機能追加 | |\n"
    "| kura | kura-data-aggregator-trial | "
    "https://github.com/aainc/kura-data-aggregator-trial | 集約基盤 | 改修 "
    "| |\n"
    "| 時計アプリ | clock-app | - | Web 時計 | デザイン | no |\n"
)


class _Result:
    """Minimal ``subprocess.CompletedProcess`` stand-in."""

    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _pr_payload(repo: str, *, merged: bool) -> dict:
    """The PR #302 that ``repo`` owns. Both repos have one -- that is the
    trap: only the ``--repo`` argument distinguishes them."""
    if repo == JA_REPO:
        body = {
            "number": PR_NUMBER,
            "url": JA_URL,
            "state": "MERGED",
            "headRefName": JA_BRANCH,
            "headRefOid": JA_MERGE_OID,
            "title": "feat(claude): pending-decisions register",
        }
        if merged:
            body["mergedAt"] = JA_MERGED_AT
            body["mergeCommit"] = {"oid": JA_MERGE_OID}
        return body
    if repo == RENGA_REPO:
        body = {
            "number": PR_NUMBER,
            "url": RENGA_URL,
            "state": "MERGED",
            "headRefName": RENGA_BRANCH,
            "headRefOid": RENGA_MERGE_OID,
            "title": "Fix focused-pane resolution",
        }
        if merged:
            body["mergedAt"] = RENGA_MERGED_AT
            body["mergeCommit"] = {"oid": RENGA_MERGE_OID}
        return body
    raise AssertionError(f"unexpected repo queried: {repo!r}")


# Captured before any patching so the stand-in can hand non-gh commands
# (``git remote get-url``, used by the home-repo branch) to the real thing.
# ``mock.patch.object(<module>.subprocess, "run", ...)`` mutates the shared
# ``subprocess`` module object, so patching via one importer patches every
# importer -- including resolve_worker_layout's git calls.
_REAL_SUBPROCESS_RUN = subprocess.run


def _fake_gh(captured: list, *, merged: bool, cwd_repo: str = JA_REPO):
    """``subprocess.run`` stand-in that answers ``gh`` per ``--repo``.

    ``gh repo view`` returns ``cwd_repo`` (the secretary always runs from the
    ja checkout), so any code path that still falls back to cwd resolution
    ends up reading ja's PR -- exactly the failure mode under test.

    Only ``gh`` argv lists are recorded in ``captured``; everything else runs
    for real and stays out of the way of the assertions.
    """

    def _run(argv, *args, **kwargs):
        if list(argv[:1]) != ["gh"]:
            return _REAL_SUBPROCESS_RUN(argv, *args, **kwargs)
        captured.append(list(argv))
        if argv[:3] == ["gh", "repo", "view"]:
            return _Result(json.dumps({"nameWithOwner": cwd_repo}))
        if argv[:3] == ["gh", "pr", "view"]:
            repo = argv[argv.index("--repo") + 1]
            return _Result(json.dumps(_pr_payload(repo, merged=merged)))
        raise AssertionError(f"unexpected argv: {argv}")

    return _run


class _Root:
    """A claude-org checkout skeleton: registry + ``.state/state.db``."""

    def __init__(self, base: Path, *, registry: str = REGISTRY_MD):
        self.path = base / "claude-org"
        (self.path / ".state").mkdir(parents=True)
        (self.path / "registry").mkdir()
        (self.path / "registry" / "projects.md").write_text(
            registry, encoding="utf-8",
        )
        self.db = self.path / ".state" / "state.db"
        conn = connect(self.db)
        apply_schema(conn)
        conn.close()

    def add_run(self, task_id: str, project_slug: str, branch: str) -> None:
        conn = connect(self.db)
        try:
            with StateWriter(conn).transaction() as w:
                w.upsert_run(
                    task_id=task_id,
                    project_slug=project_slug,
                    pattern="B",
                    title=task_id,
                    status="review",
                    branch=branch,
                )
        finally:
            conn.close()

    def run_row(self, task_id: str) -> sqlite3.Row:
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT branch, pr_url, pr_state, commit_short, commit_full, "
                "completed_at FROM runs WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        finally:
            conn.close()

    def make_git_home(self, origin: str) -> None:
        """Give the root a git origin so ``is_claude_org_project`` can
        positively identify a self-edit run. A real repo (rather than a mock)
        keeps the home-repo branch exercising the same git call the delegation
        resolver uses."""
        subprocess.run(
            ["git", "init", "-q", str(self.path)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.path), "remote", "add", "origin", origin],
            check=True, capture_output=True,
        )


class TestOwnerRepoFromUrl(unittest.TestCase):
    def test_extracts_github_urls_and_preserves_case(self):
        # Case matters: the value is echoed into the pr_merged payload and
        # LIKE-compared against the real pr_url in _resolve_task_id.
        self.assertEqual(
            resolve_run_repo.owner_repo_from_url(
                "https://github.com/Suisya-Systems/Renga.git"
            ),
            "Suisya-Systems/Renga",
        )
        self.assertEqual(
            resolve_run_repo.owner_repo_from_url(
                "git@github.com:aainc/kura-data-aggregator-trial.git"
            ),
            "aainc/kura-data-aggregator-trial",
        )

    def test_rejects_non_github_values(self):
        for value in (None, "", "-", "C:/Users/me/existing-repo",
                      "/home/me/repo", "https://gitlab.com/o/r"):
            self.assertIsNone(resolve_run_repo.owner_repo_from_url(value))


class TestResolveRepoForTask(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = _Root(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resolve(self, task_id: str):
        return resolve_run_repo.resolve_repo_for_task_at(
            self.root.db, task_id, claude_org_root=self.root.path,
        )

    def test_resolves_via_registry_slug_column(self):
        self.root.add_run(RENGA_TASK, "renga", RENGA_BRANCH)
        res = self._resolve(RENGA_TASK)
        self.assertEqual(res.repo, RENGA_REPO)
        self.assertEqual(res.source, resolve_run_repo.SOURCE_REGISTRY)

    def test_resolves_via_registry_nickname_column(self):
        # Live data carries runs under both the slug
        # (`kura-data-aggregator-trial`) and the nickname (`kura`); only the
        # nickname fallback resolves the latter.
        self.root.add_run("kura-231-token-ttl", "kura", "feat/kura-231")
        res = self._resolve("kura-231-token-ttl")
        self.assertEqual(res.repo, "aainc/kura-data-aggregator-trial")
        self.assertEqual(res.source, resolve_run_repo.SOURCE_REGISTRY)

    def test_slug_match_outranks_a_colliding_nickname(self):
        registry = (
            "| 通称 | プロジェクト名 | パス | 説明 |\n"
            "|---|---|---|---|\n"
            "| alpha | beta | https://github.com/o/beta-repo | b |\n"
            "| beta | gamma | https://github.com/o/gamma-repo | g |\n"
        )
        (self.root.path / "registry" / "projects.md").write_text(
            registry, encoding="utf-8",
        )
        self.root.add_run("t-1", "beta", "feat/t-1")
        self.assertEqual(self._resolve("t-1").repo, "o/beta-repo")

    def test_falls_back_to_projects_origin_url_when_registry_is_silent(self):
        self.root.add_run("runtime-1", "claude-org-runtime", "feat/runtime-1")
        conn = connect(self.root.db)
        try:
            conn.execute(
                "UPDATE projects SET origin_url = ? WHERE slug = ?",
                ("https://github.com/suisya-systems/claude-org-runtime",
                 "claude-org-runtime"),
            )
            conn.commit()
        finally:
            conn.close()
        res = self._resolve("runtime-1")
        self.assertEqual(res.repo, "suisya-systems/claude-org-runtime")
        self.assertEqual(res.source, resolve_run_repo.SOURCE_DB_ORIGIN)

    def test_home_repo_is_used_for_a_claude_org_ja_self_edit_run(self):
        # ja's own project is absent from the registry by contract, so this
        # branch is what keeps `--repo`-less ja tasks working.
        self.root.make_git_home(f"https://github.com/{JA_REPO}.git")
        self.root.add_run("ja-828-crossrepo", "claude-org-ja", "feat/ja-828")
        res = self._resolve("ja-828-crossrepo")
        self.assertEqual(res.repo, JA_REPO)
        self.assertEqual(res.source, resolve_run_repo.SOURCE_HOME_REPO)

    def test_home_repo_is_not_a_catch_all_for_other_projects(self):
        # Same git origin as above, but a project that is not ja. The old
        # behaviour resolved this to ja; the fix must refuse instead.
        self.root.make_git_home(f"https://github.com/{JA_REPO}.git")
        self.root.add_run("clock-001", "clock-app", "feat/clock-001")
        with self.assertRaises(resolve_run_repo.RepoResolutionError) as ctx:
            self._resolve("clock-001")
        self.assertNotIsInstance(ctx.exception, resolve_run_repo.RunNotFound)
        self.assertIn("clock-app", str(ctx.exception))

    def test_missing_run_row_raises_run_not_found(self):
        with self.assertRaises(resolve_run_repo.RunNotFound):
            self._resolve("no-such-task")

    def test_noncanonical_db_path_still_finds_the_checkout(self):
        """Codex P2: ``--db-path`` / ``STATE_DB_PATH`` may point outside
        ``<root>/.state/``. The grandparent is then an arbitrary directory,
        so root discovery must fall back to the cwd walk instead of turning
        every self-edit run (registry-absent by contract) into an exit 2."""
        stray = Path(self._td.name) / "elsewhere"
        stray.mkdir()
        stray_db = stray / "state.db"
        conn = connect(stray_db)
        apply_schema(conn)
        conn.close()

        cwd_root = Path(self._td.name) / "cwd-checkout"
        (cwd_root / ".state").mkdir(parents=True)
        (cwd_root / "registry").mkdir()
        (cwd_root / "registry" / "projects.md").write_text(
            REGISTRY_MD, encoding="utf-8",
        )
        (cwd_root / "pyproject.toml").write_text(
            '[project]\nname = "claude-org-ja"\nversion = "0.0.1"\n',
            encoding="utf-8",
        )
        (cwd_root / ".git").mkdir()

        conn = connect(stray_db)
        try:
            with StateWriter(conn).transaction() as w:
                w.upsert_run(
                    task_id=RENGA_TASK, project_slug="renga", pattern="B",
                    title=RENGA_TASK, status="review", branch=RENGA_BRANCH,
                )
        finally:
            conn.close()

        self.assertEqual(
            resolve_run_repo.infer_claude_org_root(self.root.db),
            self.root.path,
        )
        prior = os.getcwd()
        try:
            os.chdir(str(cwd_root))
            self.assertEqual(
                resolve_run_repo.infer_claude_org_root(stray_db), cwd_root,
            )
            res = resolve_run_repo.resolve_repo_for_task_at(
                stray_db, RENGA_TASK,
            )
        finally:
            os.chdir(prior)
        self.assertEqual(res.repo, RENGA_REPO)
        self.assertEqual(res.source, resolve_run_repo.SOURCE_REGISTRY)

    def test_ambiguous_registry_rows_refuse_to_guess(self):
        registry = (
            "| 通称 | プロジェクト名 | パス | 説明 |\n"
            "|---|---|---|---|\n"
            "| renga | renga | https://github.com/o/renga-one | a |\n"
            "| renga2 | renga | https://github.com/o/renga-two | b |\n"
        )
        (self.root.path / "registry" / "projects.md").write_text(
            registry, encoding="utf-8",
        )
        self.root.add_run(RENGA_TASK, "renga", RENGA_BRANCH)
        with self.assertRaises(resolve_run_repo.RepoResolutionError) as ctx:
            self._resolve(RENGA_TASK)
        self.assertIn("more than one", str(ctx.exception))


class TestCrossRepoPrRecordingRegression(unittest.TestCase):
    """Reproduce the 2026-08-06 corruption and assert it cannot recur.

    Both repos own PR #302. The renga run is recorded with ``--repo``
    omitted; every assertion is that renga's metadata landed and none of ja's
    did.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = _Root(Path(self._td.name))
        self.root.make_git_home(f"https://github.com/{JA_REPO}.git")
        self.root.add_run(RENGA_TASK, "renga", RENGA_BRANCH)
        self.root.add_run(JA_TASK, "claude-org-ja", JA_BRANCH)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_set_run_pr_open_records_the_projects_own_pr(self):
        captured: list = []
        argv = [
            "--task-id", RENGA_TASK,
            "--pr", str(PR_NUMBER),
            "--db-path", str(self.root.db),
        ]
        with mock.patch.object(
            set_run_pr_open.subprocess, "run",
            side_effect=_fake_gh(captured, merged=False),
        ), mock.patch.object(
            set_run_pr_open.shutil, "which", return_value="/usr/bin/gh",
        ):
            rc = set_run_pr_open.main(argv)
        self.assertEqual(rc, 0)

        queried = [
            a[a.index("--repo") + 1]
            for a in captured if a[:3] == ["gh", "pr", "view"]
        ]
        self.assertEqual(queried, [RENGA_REPO])

        row = self.root.run_row(RENGA_TASK)
        self.assertEqual(row["pr_url"], RENGA_URL)
        self.assertEqual(row["branch"], RENGA_BRANCH)
        # The exact values that leaked in on 2026-08-06.
        self.assertNotEqual(row["branch"], JA_BRANCH)
        self.assertNotEqual(row["pr_url"], JA_URL)

        # ja's own run must be untouched by a renga recording.
        ja_row = self.root.run_row(JA_TASK)
        self.assertIsNone(ja_row["pr_url"])

    def test_run_complete_on_merge_records_the_projects_own_merge(self):
        captured: list = []
        argv = [
            "--task-id", RENGA_TASK,
            "--pr", str(PR_NUMBER),
            "--db-path", str(self.root.db),
        ]
        with mock.patch.object(
            run_complete_on_merge.subprocess, "run",
            side_effect=_fake_gh(captured, merged=True),
        ), mock.patch.object(
            run_complete_on_merge.shutil, "which", return_value="/usr/bin/gh",
        ):
            rc = run_complete_on_merge.main(argv)
        self.assertEqual(rc, 0)

        queried = [
            a[a.index("--repo") + 1]
            for a in captured if a[:3] == ["gh", "pr", "view"]
        ]
        self.assertEqual(queried, [RENGA_REPO])

        row = self.root.run_row(RENGA_TASK)
        self.assertEqual(row["pr_url"], RENGA_URL)
        self.assertEqual(row["pr_state"], "merged")
        self.assertEqual(row["commit_full"], RENGA_MERGE_OID)
        self.assertEqual(row["commit_short"], RENGA_MERGE_OID[:7])
        self.assertEqual(row["completed_at"], RENGA_MERGED_AT)
        # 262b393 / 2026-05-05 are the ja values that were written in the
        # real incident.
        self.assertNotEqual(row["commit_short"], JA_MERGE_OID[:7])
        self.assertNotEqual(row["completed_at"], JA_MERGED_AT)

        conn = sqlite3.connect(str(self.root.db))
        conn.row_factory = sqlite3.Row
        try:
            event = conn.execute(
                "SELECT e.payload_json FROM events e JOIN runs r "
                "ON r.id = e.run_id WHERE r.task_id = ? AND e.kind = ?",
                (RENGA_TASK, "pr_merged"),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(event["payload_json"])["repo"], RENGA_REPO)

    def test_ja_task_without_repo_flag_still_reads_ja(self):
        """The acceptance condition that the fix must not regress: an
        ordinary ja-internal task with ``--repo`` omitted keeps resolving to
        ja -- now by positive identification instead of by cwd accident."""
        captured: list = []
        argv = [
            "--task-id", JA_TASK,
            "--pr", str(PR_NUMBER),
            "--db-path", str(self.root.db),
        ]
        with mock.patch.object(
            set_run_pr_open.subprocess, "run",
            side_effect=_fake_gh(captured, merged=False),
        ), mock.patch.object(
            set_run_pr_open.shutil, "which", return_value="/usr/bin/gh",
        ):
            rc = set_run_pr_open.main(argv)
        self.assertEqual(rc, 0)

        queried = [
            a[a.index("--repo") + 1]
            for a in captured if a[:3] == ["gh", "pr", "view"]
        ]
        self.assertEqual(queried, [JA_REPO])
        self.assertEqual(self.root.run_row(JA_TASK)["pr_url"], JA_URL)

    def test_stdout_names_the_resolved_repo_and_pr_title(self):
        captured: list = []
        argv = [
            "--task-id", RENGA_TASK,
            "--pr", str(PR_NUMBER),
            "--db-path", str(self.root.db),
        ]
        with mock.patch.object(
            set_run_pr_open.subprocess, "run",
            side_effect=_fake_gh(captured, merged=False),
        ), mock.patch.object(
            set_run_pr_open.shutil, "which", return_value="/usr/bin/gh",
        ), mock.patch.object(set_run_pr_open.sys, "stdout") as out:
            set_run_pr_open.main(argv)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn(f"repo={RENGA_REPO}", printed)
        self.assertIn("Fix focused-pane resolution", printed)


class TestRunCompleteOnMergeRepoSelection(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = _Root(Path(self._td.name))
        self.root.add_run(RENGA_TASK, "renga", RENGA_BRANCH)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _main(self, argv: list, captured: list) -> int:
        with mock.patch.object(
            run_complete_on_merge.subprocess, "run",
            side_effect=_fake_gh(captured, merged=True),
        ), mock.patch.object(
            run_complete_on_merge.shutil, "which", return_value="/usr/bin/gh",
        ):
            return run_complete_on_merge.main(argv)

    def test_explicit_repo_still_wins(self):
        captured: list = []
        rc = self._main([
            "--task-id", RENGA_TASK, "--pr", str(PR_NUMBER),
            "--repo", RENGA_REPO, "--db-path", str(self.root.db),
        ], captured)
        self.assertEqual(rc, 0)
        self.assertEqual(
            [a for a in captured if a[:3] == ["gh", "repo", "view"]], [],
        )

    def test_without_task_id_the_cwd_repo_remains_the_default(self):
        """No task_id means the task is discovered *from* the PR, so there is
        no run to resolve a repo from and ``gh repo view`` still applies."""
        captured: list = []
        # cwd repo is renga here so the PR resolves to the seeded run.
        with mock.patch.object(
            run_complete_on_merge.subprocess, "run",
            side_effect=_fake_gh(captured, merged=True, cwd_repo=RENGA_REPO),
        ), mock.patch.object(
            run_complete_on_merge.shutil, "which", return_value="/usr/bin/gh",
        ):
            rc = run_complete_on_merge.main([
                "--pr", str(PR_NUMBER), "--db-path", str(self.root.db),
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(
            len([a for a in captured if a[:3] == ["gh", "repo", "view"]]), 1,
        )

    def test_unresolvable_project_exits_2_before_querying_gh(self):
        self.root.add_run("clock-001", "clock-app", "feat/clock-001")
        captured: list = []
        rc = self._main([
            "--task-id", "clock-001", "--pr", str(PR_NUMBER),
            "--db-path", str(self.root.db),
        ], captured)
        self.assertEqual(rc, 2)
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
