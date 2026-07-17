"""Unit tests for tools/work_discovery_repos.py (Issue #729).

Covers the SPEC D3 resolver cases:
- home repo always included (and first)
- triage opt-in row with a GitHub URL path is adopted (owner/repo)
- local-path / ``-`` opt-in rows are skipped and left in a signal
- duplicate repos are de-duped, order preserved
- ``--format flags`` output (pure stdout, signals to stderr)
- ``--format json`` output shape
- home-resolution failure leaves a loud signal (non-fatal)
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import work_discovery_repos as wdr  # noqa: E402


HOME_URL = "https://github.com/suisya-systems/claude-org-ja.git"
HOME_REPO = "suisya-systems/claude-org-ja"


def _init_git_with_origin(repo: Path, origin_url: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", origin_url],
        check=True,
    )


def _write_registry(root: Path, data_rows: list[str]) -> Path:
    """Write a header-mode registry with the triage column.

    Each entry in ``data_rows`` is the full pipe-delimited data row body
    without the leading/trailing pipes, e.g.
    ``"foo | foo | https://github.com/o/r | d | t | yes"``.
    """
    reg_dir = root / "registry"
    reg_dir.mkdir(exist_ok=True)
    lines = [
        "# Projects Registry",
        "",
        "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |",
        "|---|---|---|---|---|---|",
    ]
    for body in data_rows:
        lines.append(f"| {body} |")
    path = reg_dir / "projects.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class ResolveReposTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        _init_git_with_origin(self.root, HOME_URL)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_home_always_included_first_no_optin(self) -> None:
        reg = _write_registry(
            self.root,
            ["clock | clock-app | - | clock | design | no"],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["home_repo"], HOME_REPO)
        self.assertEqual(result["repos"], [HOME_REPO])
        self.assertEqual(result["opted_in"], [])

    def test_optin_url_adopted(self) -> None:
        reg = _write_registry(
            self.root,
            [
                "tt | token-tracking | https://github.com/aainc/token-tracking | t | x | yes",
                "renga | renga | https://github.com/suisya-systems/renga | r | y | no",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(
            result["repos"], [HOME_REPO, "aainc/token-tracking"]
        )
        self.assertEqual(len(result["opted_in"]), 1)
        self.assertEqual(result["opted_in"][0]["repo"], "aainc/token-tracking")
        self.assertEqual(result["skipped"], [])

    def test_optin_values_case_insensitive(self) -> None:
        reg = _write_registry(
            self.root,
            [
                "a | a | https://github.com/o/a | d | x | YES",
                "b | b | https://github.com/o/b | d | x |  True ",
                "c | c | https://github.com/o/c | d | x | on",
                "d | d | https://github.com/o/d | d | x | maybe",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(
            result["repos"], [HOME_REPO, "o/a", "o/b", "o/c"]
        )

    def test_localpath_and_dash_optin_skipped_with_signal(self) -> None:
        reg = _write_registry(
            self.root,
            [
                "local | localproj | C:/Users/me/repo | d | x | yes",
                "dash | dashproj | - | d | x | true",
                "ok | okproj | https://github.com/o/ok | d | x | yes",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], [HOME_REPO, "o/ok"])
        self.assertEqual(len(result["skipped"]), 2)
        skipped_nicks = {row["nickname"] for row in result["skipped"]}
        self.assertEqual(skipped_nicks, {"local", "dash"})
        # each skipped row carries its original path and a reason.
        paths = {row["path"] for row in result["skipped"]}
        self.assertEqual(paths, {"C:/Users/me/repo", "-"})
        # skip reasons are surfaced as audit signals too.
        self.assertTrue(any("skipped" in s for s in result["signals"]))

    def test_dedup_preserves_order_home_first(self) -> None:
        # An opt-in row that is the home repo itself + a duplicate opt-in row.
        reg = _write_registry(
            self.root,
            [
                "home-dup | hd | https://github.com/suisya-systems/claude-org-ja | d | x | yes",
                "tt1 | tt1 | https://github.com/aainc/token-tracking | d | x | yes",
                "tt2 | tt2 | https://github.com/aainc/token-tracking.git | d | x | yes",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        # home first, no duplicate of home, token-tracking once.
        self.assertEqual(
            result["repos"], [HOME_REPO, "aainc/token-tracking"]
        )

    def test_home_resolution_failure_signal(self) -> None:
        # A root with no git origin and gh fallback stubbed to fail.
        with tempfile.TemporaryDirectory() as td2:
            root2 = Path(td2)
            subprocess.run(["git", "init", "-q"], cwd=str(root2), check=True)
            reg = _write_registry(
                root2,
                ["ok | okproj | https://github.com/o/ok | d | x | yes"],
            )
            with mock.patch.object(wdr, "_gh_home_repo", return_value=None):
                result = wdr.resolve_repos(
                    registry_path=reg, claude_org_root=root2
                )
        self.assertIsNone(result["home_repo"])
        self.assertTrue(
            any("could not resolve home repo" in s for s in result["signals"])
        )
        # Non-fatal: opt-in repos still resolved, no home prepended.
        self.assertEqual(result["repos"], ["o/ok"])

    def test_gh_fallback_used_when_no_origin(self) -> None:
        with tempfile.TemporaryDirectory() as td2:
            root2 = Path(td2)
            subprocess.run(["git", "init", "-q"], cwd=str(root2), check=True)
            reg = _write_registry(root2, [])
            with mock.patch.object(
                wdr, "_gh_home_repo", return_value="owner/fallback-repo"
            ):
                result = wdr.resolve_repos(
                    registry_path=reg, claude_org_root=root2
                )
        self.assertEqual(result["home_repo"], "owner/fallback-repo")
        self.assertEqual(result["repos"], ["owner/fallback-repo"])
        self.assertTrue(any("fallback" in s for s in result["signals"]))

    def test_owner_repo_lowercased(self) -> None:
        reg = _write_registry(
            self.root,
            ["mixed | mixed | https://github.com/AAInc/Token-Tracking | d | x | yes"],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertIn("aainc/token-tracking", result["repos"])

    def test_owner_repo_url_helper_cases(self) -> None:
        # bare clone URL, mixed-case host, .git suffix -> lowercased owner/repo;
        # local paths / '-' / empty -> None (skipped by callers).
        self.assertEqual(
            wdr._owner_repo_from_url("https://github.com/AAInc/Token-Tracking"),
            "aainc/token-tracking",
        )
        self.assertEqual(
            wdr._owner_repo_from_url("https://GitHub.com/OWNER/Repo.git"),
            "owner/repo",
        )
        for bad in ("/tmp/local", "-", "", None):
            self.assertIsNone(wdr._owner_repo_from_url(bad))

    def test_registry_missing(self) -> None:
        missing = self.root / "registry" / "does-not-exist.md"
        result = wdr.resolve_repos(
            registry_path=missing, claude_org_root=self.root
        )
        self.assertEqual(result["repos"], [HOME_REPO])
        self.assertTrue(any("registry not found" in s for s in result["signals"]))


class FormatOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        _init_git_with_origin(self.root, HOME_URL)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_json_format_shape(self) -> None:
        reg = _write_registry(
            self.root,
            ["tt | tt | https://github.com/aainc/token-tracking | d | x | yes"],
        )
        out, err, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "json"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["home_repo"], HOME_REPO)
        self.assertEqual(data["repos"], [HOME_REPO, "aainc/token-tracking"])
        self.assertIn("opted_in", data)
        self.assertIn("skipped", data)
        self.assertIn("signals", data)

    def test_flags_format_pure_stdout(self) -> None:
        reg = _write_registry(
            self.root,
            [
                "tt | tt | https://github.com/aainc/token-tracking | d | x | yes",
                "bad | bad | - | d | x | yes",
            ],
        )
        out, err, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "flags"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.strip(),
            f"--repo {HOME_REPO} --repo aainc/token-tracking",
        )
        # skipped row detail is on stderr, not stdout.
        self.assertNotIn("skipped", out)
        self.assertIn("skipped", err)

    def test_flags_splice_shape(self) -> None:
        reg = _write_registry(self.root, [])
        out, _, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "flags"]
        )
        self.assertEqual(rc, 0)
        # exactly the home repo as a single --repo flag pair.
        self.assertEqual(out.strip().split(), ["--repo", HOME_REPO])

    def test_error_exit_code_when_no_repos(self) -> None:
        # Home unresolvable (no origin + gh fallback None) and the only
        # opt-in row is a non-URL path => repos empty => error + exit 2.
        with tempfile.TemporaryDirectory() as td2:
            root2 = Path(td2)
            subprocess.run(["git", "init", "-q"], cwd=str(root2), check=True)
            reg = _write_registry(root2, ["bad | bad | - | d | x | yes"])
            with mock.patch.object(wdr, "_gh_home_repo", return_value=None):
                out, err, rc = self._run_cli(
                    ["--registry", str(reg), "--claude-org-root", str(root2),
                     "--format", "json"]
                )
        self.assertEqual(rc, 2)
        data = json.loads(out)
        self.assertEqual(data["repos"], [])
        self.assertIn("error", data)
        self.assertIn("error:", err)

    def test_error_flags_mode_empty_stdout(self) -> None:
        # Same failure in flags mode: stdout stays empty, error to stderr.
        with tempfile.TemporaryDirectory() as td2:
            root2 = Path(td2)
            subprocess.run(["git", "init", "-q"], cwd=str(root2), check=True)
            reg = _write_registry(root2, [])
            with mock.patch.object(wdr, "_gh_home_repo", return_value=None):
                out, err, rc = self._run_cli(
                    ["--registry", str(reg), "--claude-org-root", str(root2),
                     "--format", "flags"]
                )
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")
        self.assertIn("error:", err)

    def _run_cli(self, argv: list[str]) -> tuple[str, str, int]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = wdr.main(argv)
        return out.getvalue(), err.getvalue(), rc


if __name__ == "__main__":
    unittest.main()
