"""Unit tests for tools/work_discovery_repos.py (Issue #729, Issue #801).

Issue #801 inverted the resolver's defaults, so every case below is written
against the *new* contract:

- registry rows are **included by default**; only ``no`` / ``off`` / ``false``
  (case-insensitive, trimmed) opt a row out, and unknown values are still
  included but leave an audit signal
- the home repo (claude-org-ja itself) is **opt-in** via ``triage_home`` in
  ``registry/org-config.md`` (missing file / missing key / unrecognised value
  all fall back to off, never fatal)
- when ``triage_home`` is off the resolver does not even attempt the git /
  ``gh`` home resolution
- result keys are ``repos`` / ``home_repo`` / ``triage_home`` / ``included`` /
  ``opted_out`` / ``skipped`` / ``signals`` (``opted_in`` is gone)

Each condition lives in its own test method so a regression names itself.
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

# Canonical org-config bodies. The setting line always starts at column 0 —
# the resolver reads ``triage_home`` anchored there so prose mentions inside
# bullets / quotes are not picked up as configuration.
ORG_CONFIG_HOME_ON = "# Org Config\n\n## Triage Home\ntriage_home: on\n"
ORG_CONFIG_HOME_OFF = "# Org Config\n\n## Triage Home\ntriage_home: off\n"
ORG_CONFIG_NO_KEY = (
    "# Org Config\n\n## Max Concurrent Workers\nmax_concurrent_workers: 3\n"
)


def _init_git_with_origin(repo: Path, origin_url: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", origin_url],
        check=True,
    )


def _write_registry(
    root: Path, data_rows: list[str], *, with_triage_column: bool = True
) -> Path:
    """Write a header-mode registry under ``root/registry/projects.md``.

    Each entry in ``data_rows`` is the full pipe-delimited data row body
    without the leading/trailing pipes, e.g.
    ``"foo | foo | https://github.com/o/r | d | t | no"``. Pass a body that
    ends with ``|`` (i.e. one trailing empty field) for a blank triage cell.

    ``with_triage_column=False`` writes the 5-column legacy table (no
    ``triage`` header at all) so the "legacy tables are included by default"
    contract can be exercised.
    """
    reg_dir = root / "registry"
    reg_dir.mkdir(exist_ok=True)
    if with_triage_column:
        header = "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage |"
        separator = "|---|---|---|---|---|---|"
    else:
        header = "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 |"
        separator = "|---|---|---|---|---|"
    lines = ["# Projects Registry", "", header, separator]
    for body in data_rows:
        lines.append(f"| {body} |")
    path = reg_dir / "projects.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_base_branch_registry(root: Path, data_rows: list[str]) -> Path:
    """Write a registry whose header carries the Issue #808 ``base_branch``
    column (7 columns), for the Issue #830 base-branch tests."""
    reg_dir = root / "registry"
    reg_dir.mkdir(exist_ok=True)
    lines = [
        "# Projects Registry",
        "",
        "| 通称 | プロジェクト名 | パス | 説明 | よくある作業例 | triage "
        "| base_branch |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {body} |" for body in data_rows]
    path = reg_dir / "projects.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_org_config(root: Path, body: str) -> Path:
    """Write ``root/registry/org-config.md`` with ``body`` verbatim."""
    reg_dir = root / "registry"
    reg_dir.mkdir(exist_ok=True)
    path = reg_dir / "org-config.md"
    path.write_text(body, encoding="utf-8")
    return path


class ResolveReposTest(unittest.TestCase):
    """resolve_repos() behaviour. No git repo is created by setUp — only the
    cases that genuinely need an origin build one, so "home is off" cases
    cannot pass by accident."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    # --- registry row semantics (included by default) ---------------------

    def test_legacy_table_without_triage_column_is_included(self) -> None:
        # SPEC case 1: a table that predates the triage column has every URL
        # row included (the old contract treated the whole table as opt-out).
        reg = _write_registry(
            self.root,
            ["ok | okproj | https://github.com/o/ok | d | x"],
            with_triage_column=False,
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["o/ok"])
        self.assertEqual(result["opted_out"], [])
        self.assertEqual(result["skipped"], [])

    def test_blank_triage_cell_is_included(self) -> None:
        # SPEC case 2: an empty cell is the default, i.e. scanned.
        url = "https://github.com/o/ok"
        reg = _write_registry(self.root, [f"ok | okproj | {url} | d | x |"])
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["o/ok"])
        self.assertEqual(
            result["included"],
            # `base_branch: None` = the row declares no base branch (Issue
            # #830); the key is always present so the shape is fixed.
            [{"nickname": "ok", "repo": "o/ok", "path": url, "base_branch": None}],
        )

    def test_explicit_no_is_opted_out(self) -> None:
        # SPEC case 3: only an explicit opt-out keeps a URL row out, and the
        # row stays auditable (owner/repo is still recorded).
        url = "https://github.com/aainc/token-tracking"
        reg = _write_registry(
            self.root,
            [
                f"tt | token-tracking | {url} | d | x | no",
                "ok | okproj | https://github.com/o/ok | d | x |",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["o/ok"])
        self.assertEqual(
            result["opted_out"],
            [
                {
                    "nickname": "tt",
                    "path": url,
                    "repo": "aainc/token-tracking",
                    "value": "no",
                }
            ],
        )

    def test_opt_out_values_are_case_insensitive_and_trimmed(self) -> None:
        # SPEC case 4. The table parser already trims cells, so the resolver's
        # own strip() is defence in depth; the case-folding is what this row
        # set really exercises.
        reg = _write_registry(
            self.root,
            [
                "a | a | https://github.com/o/a | d | x | off",
                "b | b | https://github.com/o/b | d | x | FALSE",
                "c | c | https://github.com/o/c | d | x |  No ",
                "keep | keep | https://github.com/o/keep | d | x |",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["o/keep"])
        self.assertEqual(
            [row["nickname"] for row in result["opted_out"]], ["a", "b", "c"]
        )
        # raw cell values are preserved verbatim for the audit trail.
        self.assertEqual(
            [row["value"] for row in result["opted_out"]], ["off", "FALSE", "No"]
        )

    def test_unknown_triage_value_is_included_with_signal(self) -> None:
        # SPEC case 5: unknown values fail *open* (included) but are loud.
        reg = _write_registry(
            self.root,
            ["d | dproj | https://github.com/o/d | d | x | maybe"],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["o/d"])
        self.assertEqual([row["nickname"] for row in result["included"]], ["d"])
        self.assertTrue(
            any(
                "is not recognised -- treated as included" in s
                for s in result["signals"]
            ),
            result["signals"],
        )

    def test_non_url_row_is_skipped_with_signal(self) -> None:
        # SPEC case 6: local paths / '-' cannot back a --repo slug.
        reg = _write_registry(
            self.root,
            [
                "local | localproj | C:/Users/me/repo | d | x |",
                "dash | dashproj | - | d | x |",
                "ok | okproj | https://github.com/o/ok | d | x |",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["o/ok"])
        self.assertEqual(
            {row["nickname"] for row in result["skipped"]}, {"local", "dash"}
        )
        self.assertEqual(
            {row["path"] for row in result["skipped"]}, {"C:/Users/me/repo", "-"}
        )
        self.assertTrue(any("skipped" in s for s in result["signals"]))

    def test_opted_out_non_url_row_emits_no_skip_signal(self) -> None:
        # SPEC case 7: opt-out is checked *before* the URL derivation, so a
        # row that is deliberately out of scope stays quiet.
        reg = _write_registry(
            self.root,
            [
                "clock | clock-app | - | d | x | no",
                "ok | okproj | https://github.com/o/ok | d | x |",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["o/ok"])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(
            result["opted_out"],
            [{"nickname": "clock", "path": "-", "repo": None, "value": "no"}],
        )
        self.assertFalse(
            any("skipped" in s for s in result["signals"]), result["signals"]
        )

    # --- triage_home (home repo is opt-in) --------------------------------

    def test_home_defaults_off_when_org_config_missing(self) -> None:
        # SPEC case 8: no org-config at all => home off, with a signal.
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertIsNone(result["home_repo"])
        self.assertEqual(result["repos"], ["o/a"])
        self.assertTrue(
            any("org-config not found" in s for s in result["signals"]),
            result["signals"],
        )

    def test_triage_home_on_puts_home_first(self) -> None:
        # SPEC case 9: opt-in resolves the home repo from git origin and puts
        # it at the head of the set.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(self.root, ORG_CONFIG_HOME_ON)
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        with mock.patch.object(
            wdr, "_gh_home_repo", return_value=None
        ) as gh_mock:
            result = wdr.resolve_repos(
                registry_path=reg, claude_org_root=self.root
            )
        self.assertTrue(result["triage_home"])
        self.assertEqual(result["home_repo"], HOME_REPO)
        self.assertEqual(result["repos"], [HOME_REPO, "o/a"])
        # git origin succeeded, so the gh fallback is never reached.
        gh_mock.assert_not_called()

    def test_triage_home_on_but_home_unresolvable_is_non_fatal(self) -> None:
        # SPEC case 10: opt-in + both resolution stages failing is loud but
        # never fatal; registry rows still make the set.
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=True)
        _write_org_config(self.root, ORG_CONFIG_HOME_ON)
        reg = _write_registry(
            self.root, ["ok | okproj | https://github.com/o/ok | d | x |"]
        )
        with mock.patch.object(wdr, "_gh_home_repo", return_value=None):
            result = wdr.resolve_repos(
                registry_path=reg, claude_org_root=self.root
            )
        self.assertTrue(result["triage_home"])
        self.assertIsNone(result["home_repo"])
        self.assertEqual(result["repos"], ["o/ok"])
        self.assertNotIn("error", result)
        self.assertTrue(
            any(
                "could not resolve home repo" in s
                and "triage_home is on" in s
                for s in result["signals"]
            ),
            result["signals"],
        )

    def test_triage_home_unrecognised_value_is_off_with_signal(self) -> None:
        # SPEC case 11: an unrecognised triage_home value falls back to off.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(
            self.root, "# Org Config\n\ntriage_home: sometimes\n"
        )
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertIsNone(result["home_repo"])
        self.assertEqual(result["repos"], ["o/a"])
        self.assertTrue(
            any(
                "triage_home value 'sometimes' is not recognised" in s
                for s in result["signals"]
            ),
            result["signals"],
        )

    def test_triage_home_empty_value_is_off_with_signal(self) -> None:
        # A bare `triage_home:` takes the unrecognised-value path (the empty
        # string is explicitly covered there). The value must not be read
        # across the line break -- otherwise the following prose line becomes
        # the value.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(
            self.root,
            "# Org Config\n\n## Triage Home\ntriage_home:\n\n"
            "work-discovery setting\n",
        )
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertIsNone(result["home_repo"])
        self.assertEqual(result["repos"], ["o/a"])
        self.assertTrue(
            any(
                "triage_home value '' is not recognised" in s
                for s in result["signals"]
            ),
            result["signals"],
        )

    def test_triage_home_empty_value_does_not_absorb_next_line(self) -> None:
        # The dangerous variant of the case above: the next non-blank line
        # spells a truthy value. It is prose, not configuration, so home must
        # stay off.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(
            self.root, "# Org Config\n\ntriage_home:\n\non\n"
        )
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertIsNone(result["home_repo"])
        self.assertEqual(result["repos"], ["o/a"])

    def test_triage_home_key_absent_is_off_without_signal(self) -> None:
        # SPEC case 12: a missing key is the documented default, not an
        # anomaly -- it must not produce a triage_home signal.
        _write_org_config(self.root, ORG_CONFIG_NO_KEY)
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertIsNone(result["home_repo"])
        self.assertFalse(
            any("triage_home" in s for s in result["signals"]),
            result["signals"],
        )

    def test_triage_home_explicit_off_is_off(self) -> None:
        # The explicit off spelling is recognised (no unrecognised-value
        # signal) and behaves like the default.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(self.root, ORG_CONFIG_HOME_OFF)
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertEqual(result["repos"], ["o/a"])
        self.assertFalse(
            any("is not recognised" in s for s in result["signals"]),
            result["signals"],
        )

    def test_triage_home_only_matches_column_zero(self) -> None:
        # The setting is read anchored at column 0 so prose that quotes
        # `triage_home: on` inside a bullet (or indents it) is not config.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(
            self.root,
            "# Org Config\n\n## Triage Home\n"
            "- `triage_home: on` to opt the home repo in\n"
            "  triage_home: on\n",
        )
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertEqual(result["repos"], ["o/a"])

    def test_triage_home_first_match_wins(self) -> None:
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(
            self.root, "# Org Config\n\ntriage_home: on\ntriage_home: off\n"
        )
        reg = _write_registry(self.root, [])
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertTrue(result["triage_home"])
        self.assertEqual(result["repos"], [HOME_REPO])

    def test_org_config_unreadable_is_off_with_signal(self) -> None:
        # An org-config path that exists but cannot be read (here: a
        # directory) degrades to off with a signal instead of raising. The
        # assertion is deliberately loose about which message fires -- both
        # the not-found and the read-failure wording state the off fallback.
        (self.root / "registry").mkdir(exist_ok=True)
        (self.root / "registry" / "org-config.md").mkdir()
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertFalse(result["triage_home"])
        self.assertEqual(result["repos"], ["o/a"])
        self.assertTrue(
            any("org-config" in s and "off" in s for s in result["signals"]),
            result["signals"],
        )

    def test_home_off_skips_git_and_gh_resolution(self) -> None:
        # SPEC case 14: with home off the resolver must not shell out at all
        # -- neither `git remote get-url origin` nor `gh repo view`.
        _init_git_with_origin(self.root, HOME_URL)
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        with mock.patch.object(
            wdr, "_git_origin_url", return_value=None
        ) as git_mock, mock.patch.object(
            wdr, "_gh_home_repo", return_value=None
        ) as gh_mock:
            result = wdr.resolve_repos(
                registry_path=reg, claude_org_root=self.root
            )
        git_mock.assert_not_called()
        gh_mock.assert_not_called()
        self.assertIsNone(result["home_repo"])
        self.assertEqual(result["repos"], ["o/a"])

    def test_gh_fallback_used_when_no_origin(self) -> None:
        # triage_home on + no git origin => the gh fallback resolves the home
        # repo and says so in a signal.
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=True)
        _write_org_config(self.root, ORG_CONFIG_HOME_ON)
        reg = _write_registry(self.root, [])
        with mock.patch.object(
            wdr, "_gh_home_repo", return_value="owner/fallback-repo"
        ):
            result = wdr.resolve_repos(
                registry_path=reg, claude_org_root=self.root
            )
        self.assertEqual(result["home_repo"], "owner/fallback-repo")
        self.assertEqual(result["repos"], ["owner/fallback-repo"])
        self.assertTrue(any("fallback" in s for s in result["signals"]))

    # --- set assembly ------------------------------------------------------

    def test_dedup_preserves_order_with_home_first(self) -> None:
        # SPEC case 15: home leads the set, a registry row naming the home
        # repo does not duplicate it, and '.git' variants collapse.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(self.root, ORG_CONFIG_HOME_ON)
        reg = _write_registry(
            self.root,
            [
                "home-dup | hd | https://github.com/suisya-systems/claude-org-ja | d | x |",
                "tt1 | tt1 | https://github.com/aainc/token-tracking | d | x |",
                "tt2 | tt2 | https://github.com/aainc/token-tracking.git | d | x |",
            ],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], [HOME_REPO, "aainc/token-tracking"])
        # de-duplication happens on the repo set only; the audit list keeps
        # every included row.
        self.assertEqual(len(result["included"]), 3)

    def test_owner_repo_lowercased(self) -> None:
        # SPEC case 16.
        reg = _write_registry(
            self.root,
            ["mixed | mixed | https://github.com/AAInc/Token-Tracking | d | x |"],
        )
        result = wdr.resolve_repos(registry_path=reg, claude_org_root=self.root)
        self.assertEqual(result["repos"], ["aainc/token-tracking"])

    def test_registry_missing_signal_is_non_fatal(self) -> None:
        # SPEC case 20: a missing registry leaves a signal; with the home repo
        # opted in the scan still has something to run against.
        _init_git_with_origin(self.root, HOME_URL)
        _write_org_config(self.root, ORG_CONFIG_HOME_ON)
        missing = self.root / "registry" / "does-not-exist.md"
        result = wdr.resolve_repos(
            registry_path=missing, claude_org_root=self.root
        )
        self.assertEqual(result["repos"], [HOME_REPO])
        self.assertEqual(result["included"], [])
        self.assertNotIn("error", result)
        self.assertTrue(
            any("registry not found" in s for s in result["signals"]),
            result["signals"],
        )

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


class FormatOutputTest(unittest.TestCase):
    """CLI surface: exit codes, JSON shape, flags-mode stdout purity."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_json_format_shape(self) -> None:
        # SPEC case 17: the renamed keys are present and `opted_in` is gone.
        reg = _write_registry(
            self.root,
            [
                "tt | tt | https://github.com/aainc/token-tracking | d | x | no",
                "ok | ok | https://github.com/o/ok | d | x |",
            ],
        )
        out, _, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "json"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertNotIn("opted_in", data)
        for key in (
            "repos", "home_repo", "triage_home", "included", "opted_out",
            "skipped", "signals",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["repos"], ["o/ok"])
        self.assertIs(data["triage_home"], False)
        self.assertIsNone(data["home_repo"])
        self.assertEqual(
            [row["nickname"] for row in data["opted_out"]], ["tt"]
        )

    def test_flags_format_pure_stdout(self) -> None:
        # SPEC case 18: stdout is spliceable flags only; skip/signal detail
        # goes to stderr.
        reg = _write_registry(
            self.root,
            [
                "ok | ok | https://github.com/o/ok | d | x |",
                "bad | bad | - | d | x |",
            ],
        )
        out, err, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "flags"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "--repo o/ok")
        self.assertNotIn("skipped", out)
        self.assertNotIn("signal", out)
        self.assertIn("skipped", err)

    def test_flags_format_multi_repo_is_one_spliceable_line(self) -> None:
        # Multi-repo is the *default* shape now, and both delivery consumers
        # splice this stdout with `$(...)`. Pin the exact single-line join so a
        # regression to newline-separated output cannot pass unnoticed.
        reg = _write_registry(
            self.root,
            [
                "a | a | https://github.com/o/a | d | x |",
                "b | b | https://github.com/o/b | d | x |",
            ],
        )
        out, _, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "flags"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "--repo o/a --repo o/b")
        # exactly one trailing newline, no internal line breaks.
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(
            out.strip().split(), ["--repo", "o/a", "--repo", "o/b"]
        )

    def test_org_config_cli_argument_is_honoured(self) -> None:
        # SPEC case 19: --org-config reads a non-default path (the default
        # registry/org-config.md is deliberately absent here).
        _init_git_with_origin(self.root, HOME_URL)
        cfg = self.root / "custom-org-config.md"
        cfg.write_text(ORG_CONFIG_HOME_ON, encoding="utf-8")
        reg = _write_registry(
            self.root, ["a | a | https://github.com/o/a | d | x |"]
        )
        out, _, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--org-config", str(cfg), "--format", "json"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIs(data["triage_home"], True)
        self.assertEqual(data["repos"], [HOME_REPO, "o/a"])

    def test_exit_2_when_no_url_rows_and_home_off_despite_git_origin(self) -> None:
        # SPEC case 13: a perfectly resolvable git origin must not rescue the
        # set while triage_home is off -- no URL rows means exit 2.
        _init_git_with_origin(self.root, HOME_URL)
        reg = _write_registry(
            self.root,
            [
                "clock | clock-app | - | d | x | no",
                "local | localproj | C:/Users/me/repo | d | x |",
            ],
        )
        out, err, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "json"]
        )
        self.assertEqual(rc, 2)
        data = json.loads(out)
        self.assertEqual(data["repos"], [])
        self.assertIsNone(data["home_repo"])
        self.assertIs(data["triage_home"], False)
        self.assertIn("triage_home", data["error"])
        self.assertIn("error:", err)

    def test_error_flags_mode_empty_stdout(self) -> None:
        # Same empty-set failure in flags mode: stdout stays empty.
        reg = _write_registry(self.root, ["bad | bad | - | d | x |"])
        out, err, rc = self._run_cli(
            ["--registry", str(reg), "--claude-org-root", str(self.root),
             "--format", "flags"]
        )
        self.assertEqual(rc, 2)
        self.assertEqual(out.strip(), "")
        self.assertIn("error:", err)

    def test_oserror_fallback_shape_matches_result_keys(self) -> None:
        # main()'s OSError fallback object must carry the same keys as a real
        # result so consumers can parse one shape.
        reg = _write_registry(self.root, [])
        with mock.patch.object(
            wdr, "resolve_repos", side_effect=OSError("boom")
        ):
            out, err, rc = self._run_cli(
                ["--registry", str(reg), "--claude-org-root", str(self.root),
                 "--format", "json"]
            )
        self.assertEqual(rc, 2)
        data = json.loads(out)
        self.assertNotIn("opted_in", data)
        for key in (
            "repos", "home_repo", "triage_home", "included", "opted_out",
            "skipped", "signals", "error",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["repos"], [])
        self.assertIn("error:", err)

    def test_undecodable_registry_exits_2_with_envelope(self) -> None:
        # Regression (Issue #829 review): `UnicodeDecodeError` is a
        # ValueError, NOT an OSError, so a registry with undecodable bytes
        # escaped main()'s handler and exited 1 with a traceback — breaking
        # the documented "exit 0 / 2" contract the delivery layer branches on
        # (and exit 1 is reserved for crashes elsewhere in this contract).
        reg = self.root / "registry"
        reg.mkdir(exist_ok=True)
        path = reg / "projects.md"
        path.write_bytes(bytes([255]))
        out, err, rc = self._run_cli(
            ["--registry", str(path), "--claude-org-root", str(self.root),
             "--format", "json"]
        )
        self.assertEqual(rc, 2)
        data = json.loads(out)
        self.assertEqual(data["repos"], [])
        self.assertIn("codec", data["error"])
        self.assertIn("error:", err)

    def _run_cli(self, argv: list[str]) -> tuple[str, str, int]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = wdr.main(argv)
        return out.getvalue(), err.getvalue(), rc


class BaseBranchesTest(unittest.TestCase):
    """The ``base_branch`` column feeds the triage scan's two-track
    completion check (Issue #830, over the Issue #808 column)."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_declared_base_branch_is_mapped(self) -> None:
        reg = _write_base_branch_registry(
            self.root,
            [
                "kura | kura | https://github.com/aainc/kura | d | x |  "
                "| develop |",
                "ja | ja | https://github.com/o/ja | d | x |  |  |",
            ],
        )
        self.assertEqual(
            wdr.resolve_base_branches(reg), {"aainc/kura": "develop"}
        )
        result = wdr.resolve_repos(
            registry_path=reg, claude_org_root=self.root
        )
        self.assertEqual(result["base_branches"], {"aainc/kura": "develop"})
        rows = {row["repo"]: row["base_branch"] for row in result["included"]}
        self.assertEqual(rows, {"aainc/kura": "develop", "o/ja": None})

    def test_origin_prefix_and_placeholder_are_normalized(self) -> None:
        # Same normalization as the delegation pipeline (Issue #808): the ref
        # may be written the way git prints it, and `-` means "unset".
        reg = _write_base_branch_registry(
            self.root,
            [
                "a | a | https://github.com/o/a | d | x |  | origin/develop |",
                "b | b | https://github.com/o/b | d | x |  | - |",
                "c | c | https://github.com/o/c | d | x |  |   main   |",
            ],
        )
        self.assertEqual(
            wdr.resolve_base_branches(reg),
            {"o/a": "develop", "o/c": "main"},
        )

    def test_opted_out_row_still_reports_its_base_branch(self) -> None:
        # `triage` governs auto-scanning, not what the branch *is*: an
        # explicit `--repo` scan of an opted-out repo must still get it.
        reg = _write_base_branch_registry(
            self.root,
            ["k | k | https://github.com/o/k | d | x | no | develop |"],
        )
        result = wdr.resolve_repos(
            registry_path=reg, claude_org_root=self.root
        )
        self.assertEqual(result["repos"], [])
        self.assertEqual(result["base_branches"], {"o/k": "develop"})

    def test_legacy_table_has_no_base_branches(self) -> None:
        # Positional fallback never populates base_branch, so pre-#808 forks
        # keep the historical behaviour with zero edits.
        reg = _write_registry(
            self.root,
            ["ok | okproj | https://github.com/o/ok | d | x"],
            with_triage_column=False,
        )
        self.assertEqual(wdr.resolve_base_branches(reg), {})
        result = wdr.resolve_repos(
            registry_path=reg, claude_org_root=self.root
        )
        self.assertEqual(result["base_branches"], {})

    def test_missing_registry_yields_empty_map(self) -> None:
        self.assertEqual(
            wdr.resolve_base_branches(self.root / "registry" / "projects.md"),
            {},
        )


if __name__ == "__main__":
    unittest.main()
