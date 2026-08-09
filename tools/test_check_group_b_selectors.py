"""Unit + subprocess tests for tools/check_group_b_selectors.py.

The checker guards contract T-4.2 "Fail-safe consequence for Group B":
``close_pane`` / ``set_pane_identity`` address panes by a numeric
``pane_id``, never by a relative selector (literal ``"focused"`` or a
bare name). It must:

* flag the relative-selector forms, with or without the ``{{FQ}}`` /
  ``mcp__*__`` prefix, and exit 1
* find ``target=`` wherever it sits in the argument list, not only as
  the first argument (an order swap is the same relative selector)
* flag a ``set_pane_identity(...)`` that writes no ``target=`` at all,
  because that argument **defaults to** ``"focused"`` (T-4.2's caller
  pane id acquisition rule), so a caller reaches the hazard without
  ever spelling the literal
* leave numeric and ``<...>`` placeholder targets alone (exit 0)
* inspect the canonical source (``X.md.in``) and skip the generated
  ``X.md`` mirror, so one logical call is never counted twice
* honour the DD-2 stale-binding allowlist, which binds a carve-out by
  file path + context string (never by line number, which drifts)
* report an allowlist entry that stopped matching anything, so a
  carve-out cannot outlive the site it was written for
* come back clean on the real checkout (regression test against the
  live docs, not only synthetic fixtures)
* keep its own CLI output ASCII, so ``--help`` cannot crash a cp932
  console (CLAUDE.local.md Windows rule)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_group_b_selectors as cgs  # noqa: E402

SCRIPT = REPO_ROOT / "tools" / "check_group_b_selectors.py"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TmpRootTestCase(unittest.TestCase):
    """Gives each test an isolated fake checkout under ``self.root``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def scan(self, allowlist=()) -> cgs.ScanResult:
        return cgs.scan(self.root, allowlist=allowlist)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


class DetectionTest(TmpRootTestCase):
    def test_bare_name_close_pane_is_a_violation(self) -> None:
        _write(
            self.root,
            "docs/procedure.md",
            'ペインを `mcp__renga-peers__close_pane(target="curator")` で閉じる\n',
        )
        result = self.scan()
        self.assertEqual(len(result.violations), 1)
        finding = result.violations[0]
        self.assertEqual(finding.path, "docs/procedure.md")
        self.assertEqual(finding.lineno, 1)
        self.assertEqual(finding.tool, "close_pane")
        self.assertEqual(finding.value, "curator")

    def test_focused_set_pane_identity_is_a_violation(self) -> None:
        _write(
            self.root,
            "skill/SKILL.md",
            "1. 前置き\n"
            '2. `{{FQ}}set_pane_identity(target="focused", name="secretary",'
            ' role="secretary")` で修復\n',
        )
        result = self.scan()
        self.assertEqual([f.lineno for f in result.violations], [2])
        self.assertEqual(result.violations[0].tool, "set_pane_identity")
        self.assertEqual(result.violations[0].value, "focused")

    def test_template_and_unquoted_bare_names_are_violations(self) -> None:
        _write(
            self.root,
            "a.md",
            'close_pane(target="worker-{task_id}")\n'
            'close_pane(target="pr-watch-<PR>")\n'
            "close_pane(target=curator)\n"
            'close_pane(target="focused")\n',
        )
        result = self.scan()
        self.assertEqual(
            [f.value for f in result.violations],
            ["worker-{task_id}", "pr-watch-<PR>", "curator", "focused"],
        )

    def test_cli_exits_1_on_violation(self) -> None:
        _write(self.root, "a.md", 'close_pane(target="curator")\n')
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("a.md:1", proc.stdout)
        self.assertIn("violation", proc.stdout)


class ArgumentOrderTest(TmpRootTestCase):
    """``target=`` は第 1 引数とは限らない。"""

    def test_target_after_another_argument_is_still_a_violation(self) -> None:
        _write(
            self.root,
            "a.md",
            'close_pane(name="pr-watch-<PR>", target="curator")\n'
            '{{FQ}}set_pane_identity(name="secretary", role="secretary",'
            ' target="focused")\n',
        )
        result = self.scan()
        self.assertEqual(
            [(f.tool, f.value) for f in result.violations],
            [("close_pane", "curator"), ("set_pane_identity", "focused")],
        )

    def test_compliant_target_after_another_argument_is_clean(self) -> None:
        _write(
            self.root,
            "a.md",
            'close_pane(name="curator", target=<pane_id>)\n'
            'set_pane_identity(name="dispatcher", target=<N>)\n',
        )
        self.assertEqual(self.scan().violations, [])

    def test_an_identifier_ending_in_target_is_not_read_as_target(self) -> None:
        # `pane_target=` は別の引数名。これを target= と誤読すると、後ろに
        # 在る本物の適合形 target= を見ずに偽陽性を出す。
        _write(
            self.root,
            "a.md",
            'set_pane_identity(pane_target="curator", target=<N>)\n',
        )
        self.assertEqual(self.scan().violations, [])


class ToolNameBoundaryTest(TmpRootTestCase):
    """別の関数名の一部として現れた綴りを Group B 呼び出しと誤検出しない。"""

    def test_wrapper_named_functions_are_not_violations(self) -> None:
        # 境界が無いと、こういう例を書いただけの doc がリポジトリ回帰テスト経由で
        # CI を落とす（呼んでいるのは Group B ツールではない）。
        _write(
            self.root,
            "a.md",
            'safe_close_pane(target="curator")\n'
            'disclose_pane(target="curator")\n'
            'my_set_pane_identity(target="focused")\n',
        )
        self.assertEqual(self.scan().violations, [])

    def test_mcp_and_template_prefixes_are_still_detected(self) -> None:
        # 境界を厳しくしすぎて MCP プレフィックス形を取り逃さないこと。
        _write(
            self.root,
            "a.md",
            'mcp__renga-peers__close_pane(target="curator")\n'
            'mcp__org-broker__set_pane_identity(target="focused")\n'
            '{{FQ}}close_pane(target="curator")\n'
            'close_pane(target="curator")\n',
        )
        self.assertEqual(len(self.scan().violations), 4)


class ImplicitTargetTest(TmpRootTestCase):
    """``target=`` を書かない set_pane_identity は既定の "focused" に落ちる。"""

    def test_set_pane_identity_without_target_is_a_violation(self) -> None:
        _write(
            self.root,
            "a.md",
            '`{{FQ}}set_pane_identity(name="secretary", role="secretary")`'
            " で自 identity を修復する\n",
        )
        result = self.scan()
        self.assertEqual(len(result.violations), 1)
        finding = result.violations[0]
        self.assertEqual(finding.tool, "set_pane_identity")
        self.assertEqual(finding.value, "focused")
        self.assertTrue(finding.implicit)

    def test_close_pane_without_target_is_not_flagged(self) -> None:
        # close_pane には契約が書く既定値が無いので、省略形は違反にしない
        # (根拠の無い偽陽性を作らない)。
        _write(self.root, "a.md", "close_pane()\n")
        self.assertEqual(self.scan().violations, [])

    def test_multi_line_call_with_a_compliant_target_is_not_flagged(self) -> None:
        # 複数行に割った呼び出しも実引数を読み切ったうえで判定する。
        # ここは target= が適合形なので違反にしない (偽陽性を出さない)。
        _write(
            self.root,
            "a.md",
            "{{FQ}}set_pane_identity(\n"
            '  target="<RENGA_PANE_ID の値>", name="secretary")\n',
        )
        self.assertEqual(self.scan().violations, [])

    def test_report_names_the_defaulted_target(self) -> None:
        _write(self.root, "a.md", 'set_pane_identity(name="secretary")\n')
        report = cgs._format_report(self.scan())
        self.assertIn("has no target=", report)
        self.assertIn('defaults to "focused"', report)

    def test_cli_exits_1_on_the_omitted_target(self) -> None:
        _write(self.root, "a.md", 'set_pane_identity(name="secretary")\n')
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("a.md:1", proc.stdout)
        self.assertIn("has no target=", proc.stdout)


class MultiLineCallTest(TmpRootTestCase):
    """手順 doc は長い呼び出しを複数行に割る。行単位走査だと素通りする。"""

    def test_bare_name_split_across_lines_is_a_violation(self) -> None:
        _write(
            self.root,
            "docs/procedure.md",
            "ディスパッチャーは次で破棄する:\n"
            "\n"
            "close_pane(\n"
            '    target="curator",\n'
            ")\n",
        )
        result = self.scan()
        self.assertEqual(len(result.violations), 1)
        finding = result.violations[0]
        self.assertEqual(finding.tool, "close_pane")
        self.assertEqual(finding.value, "curator")
        # 行番号は呼び出しの先頭行を指す。
        self.assertEqual(finding.lineno, 3)

    def test_omitted_target_split_across_lines_is_a_violation(self) -> None:
        _write(
            self.root,
            "a.md",
            "set_pane_identity(\n"
            '    name="secretary",\n'
            '    role="secretary",\n'
            ")\n",
        )
        result = self.scan()
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].value, cgs.IMPLICIT_TARGET)
        self.assertEqual(result.violations[0].lineno, 1)

    def test_numeric_target_split_across_lines_is_clean(self) -> None:
        _write(
            self.root,
            "a.md",
            "close_pane(\n    target=<照合済みの数値 pane_id>,\n)\n",
        )
        self.assertEqual(self.scan().violations, [])

    def test_allowlist_context_matches_a_multi_line_call(self) -> None:
        # allowlist は「ファイルパス + 文脈文字列」で束縛する。文脈は呼び出しの
        # 前後の prose なので、報告テキストは一致が跨いだ行全体でなければならない。
        _write(
            self.root,
            "skill/SKILL.md.in",
            "stale binding のときだけ close_pane(\n"
            '    target="pr-watch-<PR>",\n'
            ") して登録簿を pop する\n",
        )
        allowlist = (
            cgs.AllowlistEntry(
                path="skill/SKILL.md.in",
                context="して登録簿を pop する",
                target="pr-watch-<PR>",
                reason="test",
            ),
        )
        result = self.scan(allowlist=allowlist)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.stale, [])
        self.assertEqual(len(result.allowed), 1)


class CompliantFormTest(TmpRootTestCase):
    def test_numeric_and_placeholder_targets_are_not_violations(self) -> None:
        _write(
            self.root,
            "a.md",
            "close_pane(target=3)\n"
            'close_pane(target="7")\n'
            "close_pane(target=%12)\n"
            "close_pane(target=<pane_id>)\n"
            'close_pane(target="<pane_id>")\n'
            'close_pane(target="<N>")\n'
            "close_pane(target=<照合済みの数値 pane_id>)\n"
            "close_pane(target=<控えた pane_id>)\n"
            '{{FQ}}set_pane_identity(target="<RENGA_PANE_ID の値>",'
            ' name="dispatcher", role="dispatcher")\n'
            "mcp__org-broker__set_pane_identity(target=<N>,"
            ' name="pr-watch-<PR>", role="watcher")\n',
        )
        result = self.scan()
        self.assertEqual(result.violations, [])

    def test_placeholder_naming_a_relative_selector_is_a_violation(self) -> None:
        # <...> で括られていても、中身が名前 / focus を指すなら相対セレクタ。
        # 中身を見ずに一律適合とすると Group B の不変条件が検査できなくなる。
        _write(
            self.root,
            "a.md",
            'close_pane(target="<worker name>")\n'
            "set_pane_identity(target=<focused pane>, name=\"secretary\")\n"
            "close_pane(target=<curator>)\n",
        )
        result = self.scan()
        self.assertEqual(
            [f.value for f in result.violations],
            ["<worker name>", "<focused pane>", "<curator>"],
        )

    def test_id_placeholder_wording_variants_stay_compliant(self) -> None:
        # 手順 doc が実際に使う「数値 pane id を指す」言い回しは通す。
        _write(
            self.root,
            "a.md",
            "close_pane(target=<spawn 戻り値の pane_id>)\n"
            'close_pane(target="<sidecar pane_id>")\n'
            '{{FQ}}set_pane_identity(target="<そのエントリの id>", name="x")\n',
        )
        self.assertEqual(self.scan().violations, [])

    def test_names_without_ascii_letters_are_still_violations(self) -> None:
        # backend の name 規則は英字を含まない安定 name も許す。「英字を含むもの
        # だけ違反」に絞ると、これらが裸 name のまま素通りする。
        _write(
            self.root,
            "a.md",
            'close_pane(target="---")\n'
            'close_pane(target="_")\n'
            'close_pane(target="123-4")\n',
        )
        result = self.scan()
        self.assertEqual(
            [f.value for f in result.violations], ["---", "_", "123-4"]
        )

    def test_prose_mentioning_the_form_without_a_call_is_ignored(self) -> None:
        # 「相対セレクタでは撃たない」と書くための引用形は call ではない。
        _write(
            self.root,
            "a.md",
            '数値 pane_id で撃つ（`target="focused"` は使わない。名前指定'
            ' `target="worker-{task_id}"` もしない）\n',
        )
        self.assertEqual(self.scan().violations, [])

    def test_report_says_clean(self) -> None:
        _write(self.root, "a.md", "close_pane(target=<pane_id>)\n")
        result = self.scan()
        self.assertTrue(result.ok())
        self.assertIn("clean", cgs._format_report(result))

    def test_cli_flags_the_shipped_allowlist_as_stale_off_repo(self) -> None:
        # 別ツリーを --root で指すと、同梱 allowlist は当然どこにも一致しない。
        # 違反ゼロでも stale として exit 1 になる (免罪符の失効を黙らせない)。
        _write(self.root, "a.md", "close_pane(target=<pane_id>)\n")
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Stale allowlist", proc.stdout)
        self.assertNotIn("violation(s)", proc.stdout)


class CanonicalSourceTest(TmpRootTestCase):
    def test_generated_md_is_skipped_when_the_in_sibling_exists(self) -> None:
        _write(self.root, "s/SKILL.md.in", "close_pane(target=<pane_id>)\n")
        # 生成物側だけに違反が残っていても、正本が適合なら検査しない
        # (再生成で消える差分を二重計上しない = 契約台帳の exclusion rule (1))。
        _write(self.root, "s/SKILL.md", 'close_pane(target="curator")\n')
        self.assertEqual(self.scan().violations, [])

    def test_the_in_source_itself_is_checked(self) -> None:
        _write(self.root, "s/SKILL.md.in", 'close_pane(target="curator")\n')
        _write(self.root, "s/SKILL.md", 'close_pane(target="curator")\n')
        result = self.scan()
        self.assertEqual([f.path for f in result.violations], ["s/SKILL.md.in"])

    def test_hand_maintained_md_without_an_in_sibling_is_checked(self) -> None:
        _write(self.root, "d/CLAUDE.md", 'close_pane(target="curator")\n')
        self.assertEqual(
            [f.path for f in self.scan().violations], ["d/CLAUDE.md"]
        )


class ExclusionTest(TmpRootTestCase):
    def test_ledger_history_and_scratch_paths_are_excluded(self) -> None:
        body = 'close_pane(target="curator")\n'
        for rel in (
            "docs/contracts/backend-interface-contract.md",
            "CHANGELOG.md",
            "notes/2026-08-05-renga2-org-audit.md",
            "knowledge/curated/codex.md",
            "tmp/scratch.md",
            "CLAUDE.local.md",
            "knowledge/curated/ops.local.md",
        ):
            _write(self.root, rel, body)
        self.assertEqual(self.scan().violations, [])

    def test_other_notes_files_stay_under_guard(self) -> None:
        # notes/ は丸ごと除外しない: 凍結された監査ノート 1 本だけが例外で、
        # 生きた設計 SoT (gen_skill_prose.py が参照する設計ノート等) は検査する。
        _write(self.root, "notes/design.md", 'close_pane(target="curator")\n')
        self.assertEqual(
            [f.path for f in self.scan().violations], ["notes/design.md"]
        )

    def test_git_directory_is_not_walked(self) -> None:
        _write(self.root, ".git/hooks/note.md", 'close_pane(target="curator")\n')
        self.assertEqual(self.scan().violations, [])


class AllowlistTest(TmpRootTestCase):
    def test_allowlisted_site_is_not_a_violation(self) -> None:
        _write(
            self.root,
            "s/SKILL.md.in",
            "- **3 条件が揃う場合**: `{{FQ}}close_pane(target=\"pr-watch-<PR>\")`"
            " で name 解決させて登録簿を pop する\n",
        )
        entry = cgs.AllowlistEntry(
            path="s/SKILL.md.in",
            context="で name 解決させて登録簿を",
            target="pr-watch-<PR>",
            reason="stale binding: no numeric id is derivable from list_panes",
        )
        result = self.scan(allowlist=(entry,))
        self.assertEqual(result.violations, [])
        self.assertEqual([e for _, e in result.allowed], [entry])
        self.assertEqual(result.stale, [])

    def test_allowlist_does_not_cover_other_lines_in_the_same_file(
        self,
    ) -> None:
        _write(
            self.root,
            "s/SKILL.md.in",
            "- **3 条件が揃う場合**: `close_pane(target=\"pr-watch-<PR>\")`"
            " で name 解決させて登録簿を pop する\n"
            '- 通常経路: `close_pane(target="curator")`\n',
        )
        entry = cgs.AllowlistEntry(
            path="s/SKILL.md.in",
            context="で name 解決させて登録簿を",
            target="pr-watch-<PR>",
            reason="stale binding carve-out",
        )
        result = self.scan(allowlist=(entry,))
        self.assertEqual([f.lineno for f in result.violations], [2])

    def test_a_second_call_on_the_allowlisted_line_is_still_a_violation(
        self,
    ) -> None:
        # 文脈文字列は行全体への部分一致なので、許可済み呼び出しの隣に危険な
        # 呼び出しを書き足すと巻き添えで免除されうる。entry を selector 値で
        # 束縛し 1 回だけ消費することで、混入した方を違反として拾う。
        _write(
            self.root,
            "s/SKILL.md.in",
            "- **3 条件が揃う場合**: `close_pane(target=\"pr-watch-<PR>\")`"
            ' で name 解決させて登録簿を pop する。ついでに'
            ' `close_pane(target="curator")` も閉じる\n',
        )
        entry = cgs.AllowlistEntry(
            path="s/SKILL.md.in",
            context="で name 解決させて登録簿を",
            target="pr-watch-<PR>",
            reason="stale binding carve-out",
        )
        result = self.scan(allowlist=(entry,))
        self.assertEqual([f.value for f in result.violations], ["curator"])
        self.assertEqual([e for _, e in result.allowed], [entry])

    def test_an_allowlist_entry_is_consumed_only_once(self) -> None:
        # 同じ selector を同じ文脈で 2 度書いても、entry 1 件で免除できるのは
        # 1 件だけ (免罪符の使い回しを塞ぐ)。
        line = (
            "`close_pane(target=\"pr-watch-<PR>\")`"
            " で name 解決させて登録簿を pop する\n"
        )
        _write(self.root, "s/SKILL.md.in", line + line)
        entry = cgs.AllowlistEntry(
            path="s/SKILL.md.in",
            context="で name 解決させて登録簿を",
            target="pr-watch-<PR>",
            reason="stale binding carve-out",
        )
        result = self.scan(allowlist=(entry,))
        self.assertEqual([f.lineno for f in result.violations], [2])
        self.assertEqual(len(result.allowed), 1)

    def test_stale_allowlist_entry_is_reported(self) -> None:
        # 当該箇所が数値化された (= 免罪符が実体を失った) 状態。
        _write(
            self.root,
            "s/SKILL.md.in",
            "- **3 条件が揃う場合**: `close_pane(target=<pane_id>)`"
            " で name 解決させて登録簿を pop する\n",
        )
        entry = cgs.AllowlistEntry(
            path="s/SKILL.md.in",
            context="で name 解決させて登録簿を",
            target="pr-watch-<PR>",
            reason="stale binding carve-out",
        )
        result = self.scan(allowlist=(entry,))
        self.assertEqual(result.violations, [])
        self.assertEqual([e for e, _ in result.stale], [entry])
        self.assertFalse(result.ok())

    def test_stale_allowlist_entry_for_a_missing_file_is_reported(self) -> None:
        entry = cgs.AllowlistEntry(
            path="gone/SKILL.md.in",
            context="で name 解決させて登録簿を",
            target="pr-watch-<PR>",
            reason="stale binding carve-out",
        )
        result = self.scan(allowlist=(entry,))
        self.assertEqual([e for e, _ in result.stale], [entry])

    def test_report_names_the_stale_entry(self) -> None:
        entry = cgs.AllowlistEntry(
            path="gone/SKILL.md.in", context="ctx", target="x", reason="r"
        )
        report = cgs._format_report(cgs.ScanResult(stale=[(entry, "missing")]))
        self.assertIn("Stale allowlist", report)
        self.assertIn("gone/SKILL.md.in", report)


class RepositoryRegressionTest(unittest.TestCase):
    """実物のチェックアウトに対する回帰テスト。"""

    def test_repository_is_clean(self) -> None:
        result = cgs.scan(REPO_ROOT)
        detail = "\n".join(
            f"{f.location()}: {f.tool}(target={f.value!r})"
            for f in result.violations
        )
        self.assertEqual(result.violations, [], f"relative selectors:\n{detail}")
        stale = "\n".join(f"{e.path}: {e.context} ({r})" for e, r in result.stale)
        self.assertEqual(result.stale, [], f"stale allowlist:\n{stale}")
        self.assertGreater(result.scanned_files, 0)

    def test_every_allowlist_entry_is_live(self) -> None:
        result = cgs.scan(REPO_ROOT)
        matched = {entry for _, entry in result.allowed}
        self.assertEqual(set(cgs.ALLOWLIST), matched)

    def test_cli_exits_0_on_the_repository(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class CliEncodingTest(unittest.TestCase):
    """cp932 コンソールで --help が落ちないこと (ASCII 出力の維持)。"""

    def test_help_output_is_ascii(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0)
        proc.stdout.encode("ascii")

    def test_allowlist_listing_is_cp932_encodable(self) -> None:
        # 文脈文字列は日本語 prose 由来なので ASCII ではないが、cp932 に
        # 無い文字 (em dash 等) を持ち込んでいないことを保証する。
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--list-allowlist"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0)
        proc.stdout.encode("cp932")


if __name__ == "__main__":
    unittest.main()
