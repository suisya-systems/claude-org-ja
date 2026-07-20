"""Unit tests for tools/project_dossier.py (Issue #744 Stage 1).

Covers the invariants docs/design/project-dossier.md section 7.1 asks for:
profile inheritance (base -> class), undefined-class behaviour, the four-way
key classification (wired / deferred / forbidden / unknown), branch_style
rendering, the charter+notes embedding budget, and the contracts/
references-only guard.

CLI-level precedence (profile < --from-toml < flags) lives in
tests/test_gen_delegate_payload.py, where the sandbox helper already exists.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import project_dossier as pdoss  # noqa: E402


class _DossierSandbox:
    """A claude-org root carrying one dossier at registry/projects/<slug>/."""

    def __init__(self, root: Path, slug: str = "demo-project"):
        self.root = root
        self.slug = slug
        self.dir = root / "registry" / "projects" / slug
        (self.dir / "profiles").mkdir(parents=True)
        (self.dir / "notes").mkdir()

    def profile(self, name: str, body: str) -> None:
        (self.dir / "profiles" / f"{name}.toml").write_text(body, encoding="utf-8")

    def charter(self, body: str) -> None:
        (self.dir / "charter.md").write_text(body, encoding="utf-8")

    def note(self, name: str, body: str) -> None:
        (self.dir / "notes" / name).write_text(body, encoding="utf-8")

    def contract_file(self, name: str, body: str = "x") -> None:
        contracts = self.dir / "contracts"
        contracts.mkdir(exist_ok=True)
        (contracts / name).write_text(body, encoding="utf-8")

    def resolve(self, ref: str) -> pdoss.ProfileResolution:
        return pdoss.resolve_profile(claude_org_root=self.root, ref=ref)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.sb = _DossierSandbox(Path(self._td.name))

    def tearDown(self) -> None:
        self._td.cleanup()


# ---------------------------------------------------------------------------
# Reference parsing
# ---------------------------------------------------------------------------


class TestParseProfileRef(unittest.TestCase):
    def test_slug_only(self):
        self.assertEqual(pdoss.parse_profile_ref("en"), ("en", None))

    def test_slug_and_class(self):
        self.assertEqual(pdoss.parse_profile_ref("en/ci-fix"), ("en", "ci-fix"))

    def test_rejects_empty(self):
        with self.assertRaises(pdoss.DossierError):
            pdoss.parse_profile_ref("")

    def test_rejects_too_many_components(self):
        with self.assertRaises(pdoss.DossierError):
            pdoss.parse_profile_ref("en/ci/extra")

    def test_rejects_traversal_component(self):
        with self.assertRaises(pdoss.DossierError):
            pdoss.parse_profile_ref("../secrets")

    def test_rejects_empty_class(self):
        with self.assertRaises(pdoss.DossierError):
            pdoss.parse_profile_ref("en/")


# ---------------------------------------------------------------------------
# Inheritance and fallback
# ---------------------------------------------------------------------------


class TestResolution(_Base):
    def test_missing_dossier_is_an_error(self):
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve("nope")
        self.assertIn("no dossier for project 'nope'", str(ctx.exception))

    def test_base_only_when_no_class_requested(self):
        self.sb.profile("base", '[task]\nverification_depth = "minimal"\n')
        res = self.sb.resolve(self.sb.slug)
        self.assertIsNone(res.class_name)
        self.assertEqual(res.plan_kwargs["verification_depth"], "minimal")

    def test_class_overrides_base_key_by_key(self):
        self.sb.profile(
            "base",
            '[task]\nverification_depth = "minimal"\ncommit_prefix = "chore:"\n',
        )
        self.sb.profile("ci-fix", '[task]\nverification_depth = "full"\n')
        res = self.sb.resolve(f"{self.sb.slug}/ci-fix")
        # class wins on the key it sets ...
        self.assertEqual(res.plan_kwargs["verification_depth"], "full")
        # ... and base survives on the key it does not.
        self.assertEqual(res.plan_kwargs["commit_prefix"], "chore:")

    def test_undefined_class_errors_and_lists_available(self):
        self.sb.profile("base", '[task]\nverification_depth = "full"\n')
        self.sb.profile("ci-fix", "")
        self.sb.profile("translation-pass", "")
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve(f"{self.sb.slug}/ci-fx")
        msg = str(ctx.exception)
        # An undefined class must NOT silently degrade to base.toml: a brief
        # that looks profiled but is not is the failure mode INV-4 forbids.
        self.assertIn("no profile class 'ci-fx'", msg)
        self.assertIn("ci-fix", msg)
        self.assertIn("translation-pass", msg)

    def test_class_without_base_is_allowed(self):
        self.sb.profile("ci-fix", '[task]\ncommit_prefix = "fix(mirror):"\n')
        res = self.sb.resolve(f"{self.sb.slug}/ci-fix")
        self.assertEqual(res.plan_kwargs["commit_prefix"], "fix(mirror):")

    def test_base_only_request_without_base_file_errors(self):
        self.sb.profile("ci-fix", "")
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve(self.sb.slug)
        self.assertIn("has no", str(ctx.exception))

    def test_project_slug_defaults_from_the_profile(self):
        self.sb.profile("base", "")
        res = self.sb.resolve(self.sb.slug)
        self.assertEqual(res.plan_kwargs["project_slug"], self.sb.slug)

    def test_all_wired_axes_reach_plan_kwargs(self):
        self.sb.profile(
            "base",
            "\n".join(
                [
                    '[project]',
                    'description = "EN mirror"',
                    '[task]',
                    'verification_depth = "full"',
                    'commit_prefix = "fix(mirror):"',
                    '[implementation]',
                    'guidance = "triage the failure family first"',
                    'target_files = ["scripts/install.sh"]',
                    '[references]',
                    'knowledge = ["registry/projects/demo-project/notes/a.md"]',
                    '[parallel]',
                    'notes = "one worker per failing job type"',
                    "",
                ]
            ),
        )
        kw = self.sb.resolve(self.sb.slug).plan_kwargs
        self.assertEqual(kw["project_description_override"], "EN mirror")
        self.assertEqual(kw["verification_depth"], "full")
        self.assertEqual(kw["commit_prefix"], "fix(mirror):")
        self.assertEqual(kw["implementation_guidance"], "triage the failure family first")
        self.assertEqual(kw["implementation_target_files"], ["scripts/install.sh"])
        self.assertEqual(
            kw["references_knowledge"], ["registry/projects/demo-project/notes/a.md"]
        )
        self.assertEqual(kw["parallel_notes"], "one worker per failing job type")


# ---------------------------------------------------------------------------
# Key classification (design section 4.3)
# ---------------------------------------------------------------------------


class TestKeyClassification(_Base):
    def test_unknown_key_is_an_error(self):
        self.sb.profile("base", '[task]\nverificaton_depth = "full"\n')
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve(self.sb.slug)
        self.assertIn("unknown key [task].verificaton_depth", str(ctx.exception))

    def test_unknown_section_is_an_error(self):
        self.sb.profile("base", '[merge]\nstrategy = "squash"\n')
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve(self.sb.slug)
        self.assertIn("unknown section [merge]", str(ctx.exception))

    def test_deferred_axis_warns_and_does_nothing(self):
        self.sb.profile("base", '[profile]\nmodel = "opus"\n')
        res = self.sb.resolve(self.sb.slug)
        self.assertTrue(
            any("not wired in Stage 1" in w for w in res.warnings), res.warnings
        )
        # It must not leak into the plan under any name.
        self.assertNotIn("model", res.plan_kwargs)

    def test_every_deferred_axis_is_accepted_in_the_profile_table(self):
        body = "[profile]\n" + "\n".join(
            f'{k} = {"true" if k == "codex_review" else "1" if k == "codex_round_max" else chr(34) + "x" + chr(34)}'
            for k in sorted(pdoss.DEFERRED_KEYS)
        )
        self.sb.profile("base", body + "\n")
        res = self.sb.resolve(self.sb.slug)
        for key in pdoss.DEFERRED_KEYS:
            self.assertTrue(
                any(f"axis '{key}'" in w for w in res.warnings),
                f"{key} did not warn: {res.warnings}",
            )

    def test_deferred_axis_in_a_wired_section_errors_with_a_hint(self):
        self.sb.profile("base", '[task]\nmodel = "opus"\n')
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve(self.sb.slug)
        self.assertIn("[profile]", str(ctx.exception))

    def test_permission_mode_is_forbidden(self):
        self.sb.profile("base", '[task]\npermission_mode = "bypassPermissions"\n')
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve(self.sb.slug)
        self.assertIn("permission_mode is not allowed", str(ctx.exception))

    def test_merge_preapproval_is_forbidden(self):
        # INV-1: a profile configures execution, never approval.
        self.sb.profile("base", "[task]\nmerge_preapproved = true\n")
        with self.assertRaises(pdoss.DossierError) as ctx:
            self.sb.resolve(self.sb.slug)
        self.assertIn("never approval", str(ctx.exception))

    def test_forbidden_key_rejected_even_inside_the_profile_table(self):
        self.sb.profile("base", "[profile]\nauto_merge = true\n")
        with self.assertRaises(pdoss.DossierError):
            self.sb.resolve(self.sb.slug)

    def test_bad_verification_depth_value_errors(self):
        self.sb.profile("base", '[task]\nverification_depth = "deep"\n')
        with self.assertRaises(pdoss.DossierError):
            self.sb.resolve(self.sb.slug)

    def test_scalar_at_top_level_errors(self):
        self.sb.profile("base", 'model = "opus"\n')
        with self.assertRaises(pdoss.DossierError):
            self.sb.resolve(self.sb.slug)


# ---------------------------------------------------------------------------
# branch_style
# ---------------------------------------------------------------------------


class TestBranchStyle(_Base):
    def test_render_expands_known_placeholders(self):
        out = pdoss.render_branch_style(
            "docs/{task_id}", task_id="en-batch", project_slug="en"
        )
        self.assertEqual(out, "docs/en-batch")

    def test_render_expands_project_slug(self):
        out = pdoss.render_branch_style(
            "{project_slug}/{task_id}", task_id="t", project_slug="en"
        )
        self.assertEqual(out, "en/t")

    def test_literal_style_passes_through(self):
        self.assertEqual(
            pdoss.render_branch_style("main", task_id="t", project_slug="p"), "main"
        )

    def test_unknown_placeholder_rejected(self):
        with self.assertRaises(pdoss.DossierError):
            pdoss.render_branch_style("x/{taskid}", task_id="t", project_slug="p")

    def test_unknown_placeholder_rejected_at_resolve_time(self):
        self.sb.profile("base", '[task]\nbranch_style = "x/{nope}"\n')
        with self.assertRaises(pdoss.DossierError):
            self.sb.resolve(self.sb.slug)

    def test_branch_style_is_held_out_of_plan_kwargs(self):
        # It needs the FINAL task_id, so the caller renders it post-merge.
        self.sb.profile("base", '[task]\nbranch_style = "docs/{task_id}"\n')
        res = self.sb.resolve(self.sb.slug)
        self.assertEqual(res.branch_style, "docs/{task_id}")
        self.assertNotIn("branch_override", res.plan_kwargs)


# ---------------------------------------------------------------------------
# charter / notes embedding (design section 5)
# ---------------------------------------------------------------------------


class TestEmbedding(_Base):
    def test_charter_embedded_by_default(self):
        self.sb.charter("# 憲章\n\nEN ミラーの憲章である。\n")
        self.sb.profile("base", "")
        res = self.sb.resolve(self.sb.slug)
        self.assertIsNotNone(res.dossier_block)
        self.assertIn("EN ミラーの憲章である。", res.dossier_block)

    def test_charter_can_be_switched_off(self):
        self.sb.charter("# 憲章\n")
        self.sb.profile("base", "[dossier]\nembed_charter = false\n")
        res = self.sb.resolve(self.sb.slug)
        self.assertIsNone(res.dossier_block)

    def test_notes_are_opt_in_never_globbed(self):
        self.sb.charter("charter body\n")
        self.sb.note("picked.md", "PICKED NOTE\n")
        self.sb.note("ignored.md", "IGNORED NOTE\n")
        self.sb.profile("base", '[dossier]\nembed_notes = ["picked.md"]\n')
        block = self.sb.resolve(self.sb.slug).dossier_block
        self.assertIn("PICKED NOTE", block)
        # Not listed -> not embedded. Globbing notes/ would make every brief
        # grow monotonically as the project accumulates knowledge.
        self.assertNotIn("IGNORED NOTE", block)

    def test_order_is_charter_then_declared_note_order(self):
        self.sb.charter("CHARTER\n")
        self.sb.note("a.md", "NOTE_A\n")
        self.sb.note("b.md", "NOTE_B\n")
        self.sb.profile("base", '[dossier]\nembed_notes = ["b.md", "a.md"]\n')
        block = self.sb.resolve(self.sb.slug).dossier_block
        self.assertLess(block.index("CHARTER"), block.index("NOTE_B"))
        self.assertLess(block.index("NOTE_B"), block.index("NOTE_A"))

    def test_missing_note_warns_rather_than_silently_dropping(self):
        self.sb.profile("base", '[dossier]\nembed_notes = ["ghost.md"]\n')
        res = self.sb.resolve(self.sb.slug)
        self.assertTrue(any("ghost.md" in w for w in res.warnings), res.warnings)

    def test_missing_charter_warns_when_embedding_requested(self):
        self.sb.profile("base", "")
        res = self.sb.resolve(self.sb.slug)
        self.assertTrue(any("charter.md" in w for w in res.warnings), res.warnings)

    def test_per_file_truncation_marks_and_warns(self):
        long_charter = "\n".join(f"line {i}" for i in range(2000))
        self.assertGreater(len(long_charter), pdoss.PER_FILE_CHAR_LIMIT)
        self.sb.charter(long_charter)
        self.sb.profile("base", "")
        res = self.sb.resolve(self.sb.slug)
        block = res.dossier_block
        self.assertIn("以下省略", block)
        self.assertIn("registry/projects/demo-project/charter.md", block)
        self.assertTrue(any("truncated" in w for w in res.warnings), res.warnings)
        self.assertLess(len(block), len(long_charter))

    def test_total_budget_exhaustion_omits_and_warns(self):
        filler = "\n".join(f"line {i}" for i in range(2000))  # > per-file cap
        for name in ("a.md", "b.md", "c.md", "d.md"):
            self.sb.note(name, filler)
        self.sb.profile(
            "base",
            '[dossier]\nembed_charter = false\n'
            'embed_notes = ["a.md", "b.md", "c.md", "d.md"]\n',
        )
        res = self.sb.resolve(self.sb.slug)
        self.assertLessEqual(
            len(res.dossier_block), pdoss.TOTAL_CHAR_LIMIT + 2000
        )
        self.assertTrue(
            any("budget" in w and "d.md" in w for w in res.warnings), res.warnings
        )

    def test_note_path_traversal_is_rejected(self):
        (self.sb.root / "secret.md").write_text("TOP SECRET\n", encoding="utf-8")
        self.sb.profile(
            "base", '[dossier]\nembed_notes = ["../../../secret.md"]\n'
        )
        with self.assertRaises(pdoss.DossierError):
            self.sb.resolve(self.sb.slug)

    def test_absolute_note_path_is_rejected(self):
        self.sb.profile("base", '[dossier]\nembed_notes = ["/etc/passwd"]\n')
        with self.assertRaises(pdoss.DossierError):
            self.sb.resolve(self.sb.slug)

    def test_no_dossier_content_yields_no_block(self):
        self.sb.profile("base", "[dossier]\nembed_charter = false\n")
        self.assertIsNone(self.sb.resolve(self.sb.slug).dossier_block)


# ---------------------------------------------------------------------------
# contracts/ references-only guard (design section 3)
# ---------------------------------------------------------------------------


class TestContractsGuard(_Base):
    def test_readme_only_is_clean(self):
        self.sb.profile("base", "")
        self.sb.charter("c\n")
        self.sb.contract_file("README.md", "- [old belt contract](...) (historical)\n")
        res = self.sb.resolve(self.sb.slug)
        self.assertFalse([w for w in res.warnings if "contracts/" in w], res.warnings)

    def test_contract_body_warns(self):
        # Copying a session-scoped human merge-preapproval into a standing
        # dossier is how "an exception for this session" becomes policy.
        self.sb.profile("base", "")
        self.sb.charter("c\n")
        self.sb.contract_file("scope-contract.md", "merge pre-approved: yes\n")
        res = self.sb.resolve(self.sb.slug)
        self.assertTrue(
            any("references only" in w for w in res.warnings), res.warnings
        )

    def test_absent_contracts_dir_is_clean(self):
        self.sb.profile("base", "")
        self.sb.charter("c\n")
        res = self.sb.resolve(self.sb.slug)
        self.assertFalse([w for w in res.warnings if "contracts/" in w], res.warnings)


if __name__ == "__main__":
    unittest.main()
