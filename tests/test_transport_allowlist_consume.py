"""settings allowlist の broker consume (Epic #6 E / ja#514, §5.3) の
golden / 不変条件テスト。

D (ja#513) から defer した「user_common / per-role settings の mcp allowlist が
renga 固定」問題の解消を検証する。設計 SoT: transport-lab
``docs/design/ja-migration-plan.md`` §5.3 / §8 Issue E。

**絶対条件 = 既定 renga で生成物が bit 等価 (byte 一致)**。検証する性質:

1. **flag=renga (既定) で恒等 / bit 等価** — ``tools.transport.rewrite_allow_entries``
   が入力をそのまま返し、``org_setup_prune`` の生成 allow が permissions.md の
   テンプレートと 1 byte も変わらないこと (renga アンカー不変)。
2. **flag=broker で broker tier に置換** — renga の MCP ブロック
   (``mcp__renga-peers__*``) がロールの broker auth tier
   (``mcp__org-broker__*``) に置換され、``Bash(...)`` 等の非 MCP エントリは
   順序保持されること。
3. **byte-drift 非波及** — ``org_extension_schema.json`` / permissions.md は
   touch せず、``check_role_configs`` の renga 既定検証が不変であること。
   broker 期待面の付け替えは in-memory のみ。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from claude_org_runtime import transport as rt_descriptor

from tools import transport as t
from tools import org_setup_prune as osp
from tools import check_role_configs as crc

REPO_ROOT = Path(__file__).resolve().parent.parent
PERMISSIONS_MD = (
    REPO_ROOT / ".claude" / "skills" / "org-setup" / "references" / "permissions.md"
)
SCHEMA = REPO_ROOT / "tools" / "org_extension_schema.json"

RENGA_PREFIX = "mcp__renga-peers__"
BROKER_PREFIX = "mcp__org-broker__"


@contextmanager
def _env_transport(value):
    """``ORG_TRANSPORT`` を一時設定 (None で明示削除) し、確実に復元する。"""
    sentinel = object()
    prev = os.environ.get(t.ENV_KEY, sentinel)
    try:
        if value is None:
            os.environ.pop(t.ENV_KEY, None)
        else:
            os.environ[t.ENV_KEY] = value
        yield
    finally:
        if prev is sentinel:
            os.environ.pop(t.ENV_KEY, None)
        else:
            os.environ[t.ENV_KEY] = prev


def _secretary_template_allow() -> list:
    """実 permissions.md の窓口テンプレートの permissions.allow を取り出す。"""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    blocks = osp.extract_role_blocks(
        PERMISSIONS_MD.read_text(encoding="utf-8"), schema["roles"]
    )
    return list(blocks["secretary"]["permissions"]["allow"])


class RewriteIdentityUnderRenga(unittest.TestCase):
    """flag=renga (既定) では rewrite_allow_entries が恒等 = bit 等価の核。"""

    def test_explicit_renga_is_identity(self):
        allow = _secretary_template_allow()
        out = t.rewrite_allow_entries(allow, "secretary", flag="renga")
        self.assertEqual(out, allow)

    def test_default_unset_env_is_renga_identity(self):
        allow = _secretary_template_allow()
        with _env_transport(None):  # ORG_TRANSPORT 無設定 = 既定 renga
            out = t.rewrite_allow_entries(allow, "secretary")
        self.assertEqual(out, allow)

    def test_identity_for_all_org_roles(self):
        # renga ではどのロールでも入力をそのまま返す (per-role 手書きサブセットを
        # descriptor の renga-14 で勝手に上書きしない = byte 等価)。
        sample = ["mcp__renga-peers__send_message", "Bash(git add:*)"]
        for role in ("user_common", "secretary", "dispatcher", "curator", "worker"):
            with self.subTest(role=role):
                self.assertEqual(
                    t.rewrite_allow_entries(sample, role, flag="renga"), sample
                )

    def test_returns_new_list_not_same_object(self):
        # 恒等でも呼び出し側の入力を共有・破壊しないよう新リストを返す。
        allow = _secretary_template_allow()
        out = t.rewrite_allow_entries(allow, "secretary", flag="renga")
        self.assertIsNot(out, allow)


class RewriteBrokerSwap(unittest.TestCase):
    """flag=broker で renga MCP ブロックを broker tier に置換する。"""

    def test_secretary_block_swapped_to_broker_tier(self):
        allow = _secretary_template_allow()
        renga_count = sum(1 for e in allow if e.startswith(RENGA_PREFIX))
        self.assertGreater(renga_count, 0, "前提: 窓口テンプレに renga MCP がある")
        out = t.rewrite_allow_entries(allow, "secretary", flag="broker")
        # renga エントリは 1 つも残らない
        self.assertFalse(any(e.startswith(RENGA_PREFIX) for e in out))
        # broker エントリは descriptor の secretary tier と一致
        broker = [e for e in out if e.startswith(BROKER_PREFIX)]
        self.assertEqual(broker, t.allow_entries("secretary", flag="broker"))
        # 非 MCP エントリ (Bash 等) は順序を保って残る
        non_mcp_in = [
            e for e in allow if not e.startswith(RENGA_PREFIX)
        ]
        non_mcp_out = [e for e in out if not e.startswith(BROKER_PREFIX)]
        self.assertEqual(non_mcp_out, non_mcp_in)

    def test_broker_block_inserted_at_first_renga_position(self):
        allow = ["Bash(a)", "mcp__renga-peers__x", "Bash(b)", "mcp__renga-peers__y"]
        out = t.rewrite_allow_entries(allow, "worker", flag="broker")
        # 最初の renga 位置 (index 1) に broker tier がまとめて入り、Bash 順序は不変
        self.assertEqual(out[0], "Bash(a)")
        tier = t.allow_entries("worker", flag="broker")
        self.assertEqual(out[1 : 1 + len(tier)], tier)
        self.assertEqual(out[1 + len(tier):], ["Bash(b)"])

    def test_no_renga_block_passthrough(self):
        # renga MCP を持たないロールのテンプレ (dispatcher/curator) は broker でも
        # 素通し — 無いものに tier を勝手に注入しない (「ブロックの swap」に限定)。
        allow = ["Bash(git add:*)", "Bash(git commit:*)"]
        self.assertEqual(
            t.rewrite_allow_entries(allow, "dispatcher", flag="broker"), allow
        )

    def test_broker_tiers_match_descriptor(self):
        # messaging tier (4) vs ops tier、descriptor が唯一の SoT。
        self.assertEqual(len(t.allow_entries("worker", flag="broker")), 4)
        self.assertEqual(len(t.allow_entries("curator", flag="broker")), 4)
        self.assertEqual(len(t.allow_entries("user_common", flag="broker")), 4)
        self.assertEqual(len(t.allow_entries("dispatcher", flag="broker")), 12)
        self.assertEqual(len(t.allow_entries("secretary", flag="broker")), 13)
        # 全 broker エントリは org-broker プレフィックス
        for role in ("worker", "dispatcher", "secretary"):
            for e in t.allow_entries(role, flag="broker"):
                self.assertTrue(e.startswith(BROKER_PREFIX))


class AllowEntriesConsumesDescriptor(unittest.TestCase):
    """ja は allowlist をハードコードせず runtime descriptor を consume する。"""

    def test_renga_matches_descriptor(self):
        for role in ("user_common", "secretary", "dispatcher", "curator", "worker"):
            with self.subTest(role=role):
                self.assertEqual(
                    t.allow_entries(role, flag="renga"),
                    list(rt_descriptor.allow_entries_for_role(role, flag="renga")),
                )

    def test_broker_matches_descriptor(self):
        for role in ("user_common", "secretary", "dispatcher", "curator", "worker"):
            with self.subTest(role=role):
                self.assertEqual(
                    t.allow_entries(role, flag="broker"),
                    list(rt_descriptor.allow_entries_for_role(role, flag="broker")),
                )


class OrgSetupPruneBitEquivalence(unittest.TestCase):
    """org_setup_prune の生成 settings が renga で byte 等価 / broker で置換。"""

    def _write_secretary(self, transport_value):
        schema = osp.load_schema(SCHEMA)
        md = PERMISSIONS_MD.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".claude").mkdir(parents=True)
            with _env_transport(transport_value):
                rc = osp.process_role(
                    "secretary", schema, md,
                    root=root, dry_run=False,
                    claude_org_path_arg=str(root), no_backup=True,
                )
            self.assertEqual(rc, 0)
            written = json.loads(
                (root / ".claude" / "settings.local.json").read_text(encoding="utf-8")
            )
        return written["permissions"]["allow"]

    def test_renga_default_generates_template_allow_byte_equivalent(self):
        # ORG_TRANSPORT 無設定 (既定 renga) → 生成 allow == permissions.md テンプレ。
        template_allow = _secretary_template_allow()
        generated = self._write_secretary(None)
        self.assertEqual(generated, template_allow)

    def test_renga_explicit_equals_default(self):
        self.assertEqual(
            self._write_secretary("renga"), self._write_secretary(None)
        )

    def test_broker_generates_org_broker_allow(self):
        generated = self._write_secretary("broker")
        self.assertFalse(any(e.startswith(RENGA_PREFIX) for e in generated))
        broker = [e for e in generated if e.startswith(BROKER_PREFIX)]
        self.assertEqual(broker, t.allow_entries("secretary", flag="broker"))


class CheckRoleConfigsTransportAware(unittest.TestCase):
    """check_role_configs の broker 検証付け替え (in-memory のみ)。"""

    def test_renga_returns_same_object_identity(self):
        rs = {"required_allow": [RENGA_PREFIX + "send_message", RENGA_PREFIX + "list_panes"]}
        with _env_transport(None):
            out = crc._transport_aware_role_schema("secretary", rs)
        self.assertIs(out, rs)  # 既定 renga: schema オブジェクトを一切触らない

    def test_no_required_allow_returns_same_object(self):
        rs = {"settings_paths": ["x"]}
        with _env_transport("broker"):
            self.assertIs(crc._transport_aware_role_schema("dispatcher", rs), rs)

    def test_broker_rewrites_required_allow_without_mutating_original(self):
        rs = {"required_allow": [RENGA_PREFIX + "send_message", RENGA_PREFIX + "list_panes"]}
        original = list(rs["required_allow"])
        with _env_transport("broker"):
            out = crc._transport_aware_role_schema("secretary", rs)
        # 元 schema は不変 (byte-drift される org_extension_schema.json を守る)
        self.assertEqual(rs["required_allow"], original)
        self.assertIsNot(out, rs)
        self.assertEqual(out["required_allow"], t.allow_entries("secretary", flag="broker"))


class CheckRoleConfigsOnDiskBrokerValidation(unittest.TestCase):
    """broker で生成した on-disk settings が broker 検証を通過する。"""

    def _settings_for(self, transport_value):
        schema = osp.load_schema(SCHEMA)
        md = PERMISSIONS_MD.read_text(encoding="utf-8")
        d = tempfile.mkdtemp()
        root = Path(d)
        (root / ".claude").mkdir(parents=True)
        with _env_transport(transport_value):
            osp.process_role(
                "secretary", schema, md, root=root, dry_run=False,
                claude_org_path_arg=str(root), no_backup=True,
            )
        return schema, root

    def test_broker_on_disk_passes_broker_validation(self):
        schema, root = self._settings_for("broker")
        # settings_paths を secretary のテンプレ生成先に向けて on-disk 検証する。
        sec = schema["roles"]["secretary"]
        sec = {**sec, "settings_paths": [".claude/settings.local.json"]}
        schema = {**schema, "roles": {**schema["roles"], "secretary": sec}}
        with _env_transport("broker"):
            findings = crc.check_on_disk(
                schema, root, include_untracked=True, role_override="secretary"
            )
        errors = [f for f in findings if getattr(f, "severity", "") == "ERROR"]
        self.assertEqual(errors, [], msg=f"broker settings should pass broker validation: {errors}")

    def test_broker_on_disk_fails_renga_validation(self):
        # 取り違え検出: broker 設定を renga 期待で検証すると不一致が出る。
        schema, root = self._settings_for("broker")
        sec = {**schema["roles"]["secretary"], "settings_paths": [".claude/settings.local.json"]}
        schema = {**schema, "roles": {**schema["roles"], "secretary": sec}}
        with _env_transport(None):  # renga 期待
            findings = crc.check_on_disk(
                schema, root, include_untracked=True, role_override="secretary"
            )
        errors = [f for f in findings if getattr(f, "severity", "") == "ERROR"]
        self.assertTrue(errors, "broker settings should NOT pass renga validation")


if __name__ == "__main__":
    unittest.main()
