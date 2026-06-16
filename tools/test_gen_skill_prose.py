"""Tests for tools/gen_skill_prose.py (Epic #586 Phase 3' G0).

G0 のゴール (設計 §7.1): generator + フラグメント SoT + manifest スキーマの
unit test。**render 面 broker / renga 両方を golden 固定**し、設計 §5 の不変条件
(subset 保存・broker 省略ツール drop・renga byte 安定・auth 迂回なし) を機械 assert
する。実在スキルは触らない (rendered SKILL.md 0 件) ため、testdata/ の synthetic
フィクスチャで render パイプラインを両系固定する。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools import gen_skill_prose as g
from tools import transport

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI = _REPO_ROOT / "tools" / "gen_skill_prose.py"

_TESTDATA = Path(__file__).resolve().parent / "testdata" / "gen_skill_prose"
_FRAGMENTS = Path(__file__).resolve().parent / "skill_src" / "fragments"

# render 面が DEFAULT_TRANSPORT (broker) であることを前提にした assert があるので、
# runtime の既定が想定どおりかを最初に確認しておく (フリップ前後の取り違え防止)。
_DEFAULT = transport.DEFAULT_TRANSPORT


# ---------------------------------------------------------------------------
# トークン render (設計 §1.3 (a)/(b))
# ---------------------------------------------------------------------------


class TokenRenderTest(unittest.TestCase):
    def test_render_face_tokens_broker(self):
        text = "{{FQ}}send_message / {{SERVER}} / {{CHANNEL_SRC}}"
        out = g.render_tokens(text, "broker")
        self.assertEqual(out, "mcp__org-broker__send_message / org-broker / org-broker")

    def test_render_face_tokens_renga(self):
        text = "{{FQ}}send_message / {{SERVER}} / {{CHANNEL_SRC}}"
        out = g.render_tokens(text, "renga")
        self.assertEqual(out, "mcp__renga-peers__send_message / renga-peers / renga-peers")

    def test_channel_src_is_server_name_not_sidecar(self):
        # 設計 §1.3 の最重要区別: <channel source="..."> のタグ値は server 名
        # (org-broker) であって channel sidecar 名 (org-broker-channel) ではない。
        out = g.render_tokens('source="{{CHANNEL_SRC}}"', "broker")
        self.assertIn('source="org-broker"', out)
        self.assertNotIn("org-broker-channel", out)

    def test_default_transport_token_follows_default_not_render_face(self):
        # {{DEFAULT_TRANSPORT}} は render 面ではなく DEFAULT_TRANSPORT に追従する。
        # 両 render 面で同じ値 (= DEFAULT_TRANSPORT) になる (§1.3 (c))。
        self.assertEqual(g.render_tokens("{{DEFAULT_TRANSPORT}}", "broker"), _DEFAULT)
        self.assertEqual(g.render_tokens("{{DEFAULT_TRANSPORT}}", "renga"), _DEFAULT)

    def test_unknown_token_raises(self):
        with self.assertRaises(g.GenError):
            g.render_tokens("{{BOGUS}}", "broker")


# ---------------------------------------------------------------------------
# フラグメント注入 (設計 §1.3 (c))
# ---------------------------------------------------------------------------


class FragmentInjectionTest(unittest.TestCase):
    def test_per_transport_fragment_selected_by_flag(self):
        broker = g.inject_fragments("{{> dual-system-header-short }}", "broker", _FRAGMENTS)
        renga = g.inject_fragments("{{> dual-system-header-short }}", "renga", _FRAGMENTS)
        self.assertIn("既定 `broker` / opt-in `renga`", broker)
        self.assertIn("既定 `renga` / opt-in `broker`", renga)
        self.assertNotEqual(broker, renga)

    def test_neutral_fragment_same_on_both_faces(self):
        # surface-omissions は transport 非依存 = 両面同一 (設計 §5)。
        broker = g.inject_fragments("{{> surface-omissions }}", "broker", _FRAGMENTS)
        renga = g.inject_fragments("{{> surface-omissions }}", "renga", _FRAGMENTS)
        self.assertEqual(broker, renga)
        self.assertIn("attention watcher", broker)

    def test_missing_fragment_raises(self):
        with self.assertRaises(g.GenError):
            g.inject_fragments("{{> no-such-fragment }}", "broker", _FRAGMENTS)

    def test_all_four_fragments_present_both_faces(self):
        names = [
            "dual-system-header-short",
            "dual-system-header-long",
            "spawn-ritual",
            "surface-omissions",
        ]
        for name in names:
            for flag in transport.TRANSPORTS:
                # 例外なくロードできること (両面 render 健全性, §7.2-4)。
                self.assertTrue(g.load_fragment(name, flag, _FRAGMENTS).strip())


# ---------------------------------------------------------------------------
# frontmatter per-entry render (設計 §0.4 / §2.2 ※3-※5)
# ---------------------------------------------------------------------------


class FrontmatterRenderTest(unittest.TestCase):
    def test_per_tool_rename_and_drop_without_role(self):
        # 明示 per-tool: focus_pane / new_tab は broker surface 不在で drop、
        # 他は接頭辞リネーム + 順序保存 (role 不要, ※4)。
        entries = [
            "Read",
            "mcp__renga-peers__send_message",
            "mcp__renga-peers__focus_pane",
            "mcp__renga-peers__new_tab",
            "mcp__renga-peers__inspect_pane",
        ]
        r = g.render_frontmatter_allowlist(entries, "broker", role=None, allowlist="per-entry-rename")
        self.assertEqual(
            r.entries,
            [
                "Read",
                "mcp__org-broker__send_message",
                "mcp__org-broker__inspect_pane",
            ],
        )
        dropped = {t for t, _ in r.dropped}
        self.assertEqual(dropped, {"focus_pane", "new_tab"})

    def test_wildcard_expands_to_subset_no_leak(self):
        # ワイルドカードは renga source surface へ展開し broker 像を明示リスト化。
        # broker `*` を出さず、broker 固有ツール (spawn_codex_pane) も漏らさない (※5)。
        entries = ["Read", "mcp__renga-peers__*"]
        r = g.render_frontmatter_allowlist(
            entries, "broker", role="secretary", allowlist="per-entry-rename"
        )
        joined = "\n".join(r.entries)
        self.assertNotIn("mcp__org-broker__*", joined)
        self.assertNotIn("spawn_codex_pane", joined)  # broker 固有 = subset 違反になる
        self.assertNotIn("focus_pane", joined)  # broker 省略 = drop
        self.assertNotIn("new_tab", joined)
        self.assertIn("mcp__org-broker__send_message", joined)
        self.assertIn("mcp__org-broker__spawn_pane", joined)  # secretary は保持

    def test_wildcard_broker_subset_of_source_image(self):
        # 不変条件 §7.2-2(iv): broker 集合 ⊆ source 集合の像。
        role = "secretary"
        renga_tools = set(transport.surface("renga").tools_for_role(role))
        r = g.render_frontmatter_allowlist(
            ["mcp__renga-peers__*"], "broker", role=role, allowlist="per-entry-rename"
        )
        broker_prefix = transport.surface("broker").fq_prefix
        broker_tools = {e[len(broker_prefix):] for e in r.entries if e.startswith(broker_prefix)}
        self.assertTrue(broker_tools.issubset(renga_tools), broker_tools - renga_tools)

    def test_wildcard_requires_role(self):
        with self.assertRaises(g.GenError):
            g.render_frontmatter_allowlist(
                ["mcp__renga-peers__*"], "broker", role=None, allowlist="per-entry-rename"
            )

    def test_explicit_entry_kept_against_universe_not_role_tier(self):
        # Codex P2 修正: 明示 per-tool は role tier ではなく broker universe で判定。
        # dispatcher skill が明示する secretary 限定 spawn_pane は broker に存在する
        # ので保存する (source の明示認可 = subset 保存)。role tier で判定すると誤 drop。
        entries = ["mcp__renga-peers__spawn_pane", "mcp__renga-peers__send_message"]
        r = g.render_frontmatter_allowlist(
            entries, "broker", role="dispatcher", allowlist="per-entry-rename"
        )
        self.assertIn("mcp__org-broker__spawn_pane", r.entries)  # 明示 → 保存
        self.assertIn("mcp__org-broker__send_message", r.entries)
        self.assertEqual(r.dropped, [])

    def test_wildcard_follows_role_tier_not_universe(self):
        # ワイルドカードは role tier に従う: worker の `*` は broker messaging 4 のみ
        # (pane 制御は付かない = broker auth tiering を尊重)。
        r = g.render_frontmatter_allowlist(
            ["mcp__renga-peers__*"], "broker", role="worker", allowlist="per-entry-rename"
        )
        broker_worker = set(transport.surface("broker").tools_for_role("worker"))
        prefix = transport.surface("broker").fq_prefix
        got = {e[len(prefix):] for e in r.entries if e.startswith(prefix)}
        self.assertEqual(got, broker_worker)
        self.assertNotIn("mcp__org-broker__spawn_pane", r.entries)  # worker tier に無い

    def test_renga_face_is_identity(self):
        # renga (TEMPLATE_TRANSPORT) は恒等 = rollback byte 安定 (§3.2)。
        entries = ["Read", "mcp__renga-peers__*", "mcp__renga-peers__send_message"]
        r = g.render_frontmatter_allowlist(entries, "renga", role="secretary", allowlist="per-entry-rename")
        self.assertEqual(r.entries, entries)
        self.assertEqual(r.dropped, [])

    def test_renga_identity_preserves_inline_comment(self):
        # コメント付きエントリも renga 恒等で byte 保存される (Codex P2 修正)。
        entries = ["Read", "mcp__renga-peers__* # 機械置換先"]
        r = g.render_frontmatter_allowlist(entries, "renga", role="secretary", allowlist="per-entry-rename")
        self.assertEqual(r.entries, entries)

    def test_broker_rename_strips_comment_from_tool_name(self):
        # broker リネーム時はコメントを除去して bare ツール名でリネーム/展開する。
        entries = ["mcp__renga-peers__send_message # 報告用"]
        r = g.render_frontmatter_allowlist(entries, "broker", allowlist="per-entry-rename")
        self.assertEqual(r.entries, ["mcp__org-broker__send_message"])

    def test_no_role_tier_expansion_for_skill_frontmatter(self):
        # ※3: send_message のみのスキルに ops tier が付かない (過剰認可なし)。
        r = g.render_frontmatter_allowlist(
            ["mcp__renga-peers__send_message"], "broker", role="secretary", allowlist="per-entry-rename"
        )
        self.assertEqual(r.entries, ["mcp__org-broker__send_message"])

    def test_source_normalization_rejects_broker_prefix(self):
        # ※1: source は renga/template 面で著述。broker プレフィックス混入は拒否。
        with self.assertRaises(g.GenError):
            g.render_frontmatter_allowlist(
                ["mcp__org-broker__send_message"], "broker", allowlist="per-entry-rename"
            )

    def test_role_tier_uses_rewrite_allow_entries(self):
        # permissions.md (identity-anchor) のみ role-tier 置換 (§4.2(2))。
        entries = ["Read", "mcp__renga-peers__*"]
        r = g.render_frontmatter_allowlist(entries, "broker", role="worker", allowlist="role-tier")
        expected = transport.rewrite_allow_entries(entries, "worker", flag="broker")
        self.assertEqual(r.entries, list(expected))

    def test_role_tier_requires_role(self):
        with self.assertRaises(g.GenError):
            g.render_frontmatter_allowlist(["mcp__renga-peers__*"], "broker", allowlist="role-tier")


# ---------------------------------------------------------------------------
# render_source オーケストレーション + golden 固定 (G0 の核)
# ---------------------------------------------------------------------------


def _render_fixture(stem: str, flag: str, **kw) -> str:
    src = (_TESTDATA / f"{stem}.md.in").read_text(encoding="utf-8")
    return g.render_source(src, flag, fragments_dir=_FRAGMENTS, **kw).text


class GoldenRenderTest(unittest.TestCase):
    """render 面 broker / renga 両方を golden 固定する (設計 §7.1 G0 検証)。"""

    CASES = [
        ("sample_wildcard", dict(mode="template", role="secretary", allowlist="per-entry-rename")),
        ("sample_pertool", dict(mode="template", role=None, allowlist="per-entry-rename")),
        ("sample_codeliteral", dict(mode="code-literal", role=None, allowlist="none")),
    ]

    def test_golden_byte_fixed_both_faces(self):
        for stem, kw in self.CASES:
            for flag in transport.TRANSPORTS:
                with self.subTest(stem=stem, flag=flag):
                    rendered = _render_fixture(stem, flag, **kw)
                    golden = (_TESTDATA / f"{stem}.{flag}.golden.md").read_text(encoding="utf-8")
                    self.assertEqual(rendered, golden, f"{stem}.{flag} drift")

    def test_no_unresolved_tokens_in_any_golden(self):
        # 生成物に未解決トークン / フラグメント参照が残らない。
        for golden in _TESTDATA.glob("*.golden.md"):
            text = golden.read_text(encoding="utf-8")
            self.assertNotIn("{{", text, f"unresolved token in {golden.name}")

    def test_both_faces_render_without_error(self):
        # 両面 render 健全性 (§7.2-4): renga でも例外なく成立する。
        for stem, kw in self.CASES:
            for flag in transport.TRANSPORTS:
                _render_fixture(stem, flag, **kw)  # raises -> test fails

    def test_code_literal_uses_default_not_render_face(self):
        # code-literal は render 面に関わらず DEFAULT_TRANSPORT 値を出す。
        for flag in transport.TRANSPORTS:
            out = _render_fixture("sample_codeliteral", flag, mode="code-literal", allowlist="none")
            self.assertIn(f"${{ORG_TRANSPORT:-{_DEFAULT}}}", out)

    def test_unimplemented_generating_modes_rejected_in_g0(self):
        # Codex P2 修正: スキーマ定義済みだが G0 未実装の生成モード (template+fragment
        # = G2 / surgical-fragment = G3) は full パイプラインへ fall-through せず明示
        # 拒否する (silent な不完全生成を防ぐ, 設計 §4.1 / §7.1)。
        src = "# x\n\n本文に {{> surface-omissions }} と {{FQ}}send_message。\n"
        for mode in ("template+fragment", "surgical-fragment"):
            with self.subTest(mode=mode):
                with self.assertRaises(g.GenError):
                    g.render_source(src, "broker", fragments_dir=_FRAGMENTS, mode=mode, allowlist="none")

    def test_g0_implemented_modes_render(self):
        # G0 で実装済みの生成モードは render が成立する (網羅性の対称確認)。
        src = "# x\n\n{{FQ}}send_message / 既定 {{DEFAULT_TRANSPORT}}\n"
        for mode in ("template", "code-literal"):
            with self.subTest(mode=mode):
                out = g.render_source(src, "broker", fragments_dir=_FRAGMENTS, mode=mode, allowlist="none").text
                self.assertIn("mcp__org-broker__send_message", out)

    def test_mode_partition_is_exhaustive(self):
        # 全生成モードが「実装済み」か「G0 未実装(拒否)」のどちらかに分類され、
        # 取りこぼし (fall-through) が無いこと。
        self.assertEqual(
            set(g.G0_IMPLEMENTED_MODES) | set(g.G0_UNIMPLEMENTED_MODES),
            set(g.GENERATING_MODES),
        )
        self.assertEqual(
            set(g.G0_IMPLEMENTED_MODES) & set(g.G0_UNIMPLEMENTED_MODES),
            set(),
        )

    def test_identity_anchor_returns_source_unchanged(self):
        # identity-anchor は render 対象外 = 入力をそのまま返す (§4.2(2))。
        src = (_TESTDATA / "sample_pertool.md.in").read_text(encoding="utf-8")
        r = g.render_source(src, "broker", fragments_dir=_FRAGMENTS, mode="identity-anchor", allowlist="none")
        self.assertEqual(r.text, src)

    def test_renga_frontmatter_byte_stable_vs_source(self):
        # rollback byte 安定: renga render の frontmatter allowed-tools は source と一致。
        src = (_TESTDATA / "sample_wildcard.md.in").read_text(encoding="utf-8")
        src_fm = g.split_frontmatter(src)
        rendered = g.render_source(
            src, "renga", fragments_dir=_FRAGMENTS, mode="template", role="secretary", allowlist="per-entry-rename"
        ).text
        out_fm = g.split_frontmatter(rendered)
        self.assertEqual(out_fm.allowed_tools, src_fm.allowed_tools)


# ---------------------------------------------------------------------------
# frontmatter パーサ
# ---------------------------------------------------------------------------


class FrontmatterParserTest(unittest.TestCase):
    def test_split_and_reassemble_roundtrip(self):
        text = "---\nname: x\nallowed-tools:\n  - Read\n  - mcp__renga-peers__*\n---\n\n# body\n"
        fm = g.split_frontmatter(text)
        self.assertEqual(fm.allowed_tools, ["Read", "mcp__renga-peers__*"])
        self.assertEqual(g.reassemble_frontmatter(fm, fm.allowed_tools), text)

    def test_allowlist_entries_kept_raw_with_comments(self):
        # raw 保持 (コメント除去しない)。コメント除去は broker リネーム時のみ
        # (renga 恒等の byte 安定性を守る, Codex P2 修正)。
        text = (
            "---\nallowed-tools:\n  - mcp__renga-peers__* # 機械置換先\n"
            "  - Bash(echo # not a comment:*)\n---\nbody\n"
        )
        fm = g.split_frontmatter(text)
        self.assertEqual(
            fm.allowed_tools,
            ["mcp__renga-peers__* # 機械置換先", "Bash(echo # not a comment:*)"],
        )

    def test_strip_inline_comment_respects_brackets(self):
        # 括弧外の # のみコメント。Bash(...) 内の # は誤除去しない。
        self.assertEqual(g._strip_inline_comment("mcp__renga-peers__* # note"), "mcp__renga-peers__*")
        self.assertEqual(g._strip_inline_comment("Bash(echo # x:*)"), "Bash(echo # x:*)")

    def test_no_frontmatter(self):
        fm = g.split_frontmatter("# just body\n")
        self.assertFalse(fm.has_frontmatter)
        self.assertIsNone(fm.allowed_tools)


# ---------------------------------------------------------------------------
# manifest スキーマ / 検証 (設計 §4.1)
# ---------------------------------------------------------------------------


class ManifestTest(unittest.TestCase):
    def test_schema_loads_and_is_object(self):
        schema = g.load_manifest_schema()
        self.assertEqual(schema["type"], "object")
        self.assertIn("entries", schema["properties"])

    def test_example_manifest_validates(self):
        path = Path(__file__).resolve().parent / "skill_src" / "manifest.example.json"
        manifest = g.load_manifest(path)
        self.assertTrue(manifest.entries)
        self.assertIn("README.md", manifest.exclude)
        modes = {e.mode for e in manifest.entries}
        self.assertIn("identity-anchor", modes)

    def test_valid_manifest_obj(self):
        obj = {
            "entries": [
                {"source": "a.in", "output": "a.md", "mode": "template", "allowlist": "per-entry-rename"},
            ]
        }
        m = g.validate_manifest_obj(obj)
        self.assertEqual(m.entries[0].source, "a.in")

    def test_missing_required_field_raises(self):
        with self.assertRaises(g.GenError):
            g.validate_manifest_obj({"entries": [{"source": "a.in", "mode": "template"}]})

    def test_invalid_mode_raises(self):
        with self.assertRaises(g.GenError):
            g.validate_manifest_obj(
                {"entries": [{"source": "a", "mode": "bogus", "allowlist": "none"}]}
            )

    def test_invalid_allowlist_raises(self):
        with self.assertRaises(g.GenError):
            g.validate_manifest_obj(
                {"entries": [{"source": "a", "mode": "template", "allowlist": "bogus"}]}
            )

    def test_invalid_role_raises(self):
        with self.assertRaises(g.GenError):
            g.validate_manifest_obj(
                {"entries": [{"source": "a", "mode": "template", "allowlist": "none", "role": "boss"}]}
            )

    def test_role_tier_requires_role(self):
        # §2.2 ※2: identity-anchor (role-tier) は role 必須。
        with self.assertRaises(g.GenError):
            g.validate_manifest_obj(
                {"entries": [{"source": "p.md", "mode": "identity-anchor", "allowlist": "role-tier"}]}
            )

    def test_role_tier_rejected_on_generating_mode(self):
        # §2.2 ※3: role-tier を生成モードに当てると frontmatter が役割の全 tier へ
        # 拡大する過剰認可。組み合わせ自体を拒否する (Codex P2 修正)。
        for mode in ("template", "template+fragment", "surgical-fragment", "code-literal"):
            with self.assertRaises(g.GenError):
                g.validate_manifest_obj(
                    {"entries": [{"source": "s", "output": "o", "mode": mode,
                                  "allowlist": "role-tier", "role": "secretary"}]}
                )

    def test_generating_mode_requires_output(self):
        # §7.2-1: 生成モードで output を省くと drift CI カバレッジから漏れる。
        # 検証時に拒否する (Codex P2 修正)。
        for mode in ("template", "template+fragment", "surgical-fragment", "code-literal"):
            with self.assertRaises(g.GenError):
                g.validate_manifest_obj(
                    {"entries": [{"source": "s", "mode": mode, "allowlist": "none"}]}
                )

    def test_identity_anchor_output_optional(self):
        # identity-anchor は非生成アンカーなので output 省略可。
        m = g.validate_manifest_obj(
            {"entries": [{"source": "p.md", "mode": "identity-anchor",
                          "allowlist": "role-tier", "role": "secretary"}]}
        )
        self.assertIsNone(m.entries[0].output)

    def test_schema_enums_match_module_constants(self):
        # スキーマの enum と module 定数の drift 防止。
        schema = g.load_manifest_schema()
        entry = schema["definitions"]["entry"]["properties"]
        self.assertEqual(set(entry["mode"]["enum"]), set(g.ALL_MODES))
        self.assertEqual(set(entry["allowlist"]["enum"]), set(g.ALLOWLIST_KINDS))
        self.assertEqual(set(entry["role"]["enum"]), set(g.ROLES))


class CliInvocationTest(unittest.TestCase):
    """直接スクリプト実行で import が成立すること (Codex P2 修正の回帰固定)。

    既存 tool CLI と同じく ``python tools/gen_skill_prose.py`` 形でも動くこと。
    repo root が ``sys.path`` に無い状態 (= ``tools/`` だけが載る直接実行) を
    再現するため ``cwd`` を repo root 外に置き、引数なしのスクリプトパスで起動する。
    """

    def _run(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(_CLI), *args],
            cwd=str(cwd or _REPO_ROOT),
            capture_output=True,
            text=True,
        )

    def test_direct_script_print_schema(self):
        proc = self._run("--print-schema", cwd=_REPO_ROOT.parent)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"title": "skill-prose-generator manifest"', proc.stdout)

    def test_direct_script_no_manifest_is_noop(self):
        proc = self._run(cwd=_REPO_ROOT.parent)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to do", proc.stderr)

    def test_help_is_ascii_only(self):
        # cp932 コンソールでの --help クラッシュ防止 (CLAUDE.local.md Windows 注意)。
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc.stdout.encode("cp932")  # raises UnicodeEncodeError if non-cp932 char present


if __name__ == "__main__":
    unittest.main()
