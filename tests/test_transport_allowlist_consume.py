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
    """flag=renga (テンプレ著述面 / opt-in fallback) では rewrite_allow_entries が
    恒等 = bit 等価の核。恒等の基準はテンプレ著述面 (renga) であって既定 transport
    ではない (Epic #586 で既定が broker にフリップしても renga 恒等は不変)。"""

    def test_explicit_renga_is_identity(self):
        allow = _secretary_template_allow()
        out = t.rewrite_allow_entries(allow, "secretary", flag="renga")
        self.assertEqual(out, allow)

    def test_default_unset_env_is_broker_swap(self):
        # Epic #586: 無設定の既定が broker にフリップしたため、テンプレ (renga 著述)
        # は broker tier へ付け替えられる (もはや恒等ではない)。恒等は明示 renga
        # (test_explicit_renga_is_identity) が担保する。
        allow = _secretary_template_allow()
        with _env_transport(None):  # ORG_TRANSPORT 無設定 = 既定 broker
            out = t.rewrite_allow_entries(allow, "secretary")
        self.assertFalse(any(e.startswith(RENGA_PREFIX) for e in out))
        broker = [e for e in out if e.startswith(BROKER_PREFIX)]
        self.assertEqual(broker, t.allow_entries("secretary", flag="broker"))

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

    def test_renga_explicit_generates_template_allow_byte_equivalent(self):
        # ORG_TRANSPORT=renga (opt-in fallback) → 生成 allow == permissions.md
        # テンプレ (renga 著述面で byte 等価 = 切戻し忠実性)。
        template_allow = _secretary_template_allow()
        generated = self._write_secretary("renga")
        self.assertEqual(generated, template_allow)

    def test_default_unset_equals_explicit_broker(self):
        # Epic #586: 無設定の既定が broker にフリップ。明示 broker と生成物が一致。
        self.assertEqual(
            self._write_secretary(None), self._write_secretary("broker")
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
        with _env_transport("renga"):  # renga (opt-in fallback): schema を触らない
            out = crc._transport_aware_role_schema("secretary", rs)
        self.assertIs(out, rs)  # renga 恒等: schema オブジェクトを一切触らない

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
        with _env_transport("renga"):  # renga 期待 (明示 fallback; 既定は broker)
            findings = crc.check_on_disk(
                schema, root, include_untracked=True, role_override="secretary"
            )
        errors = [f for f in findings if getattr(f, "severity", "") == "ERROR"]
        self.assertTrue(errors, "broker settings should NOT pass renga validation")


class UserCommonAllowlistProjection(unittest.TestCase):
    """user_common (~/.claude/settings.json) の broker 射影 (Option A, §5.3)。

    curator / worker / dispatcher が継承する messaging MCP floor を broker 化する
    経路。**renga (明示) は strict no-op (file を 1 byte も書かない)**。Epic #586 で
    無設定の既定は broker にフリップしたため、無設定では broker floor を射影する。
    """

    _RENGA_SETTINGS = {
        "permissions": {
            "allow": [
                "Bash(renga --version)",
                "mcp__renga-peers__set_summary",
                "mcp__renga-peers__send_message",
                "mcp__renga-peers__check_messages",
                "mcp__renga-peers__list_peers",
                "mcp__renga-peers__spawn_pane",
            ]
        },
        "env": {"CLAUDE_CODE_NO_FLICKER": "1"},
    }

    def _write_tmp(self, data) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / "settings.json"
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return p

    def test_default_unset_projects_broker_floor(self):
        # Epic #586: 無設定の既定が broker にフリップしたため、user_common は
        # broker messaging floor へ射影される (renga 時の strict no-op は
        # test_renga_explicit_is_no_op が担保)。
        p = self._write_tmp(self._RENGA_SETTINGS)
        with _env_transport(None):  # ORG_TRANSPORT 無設定 = 既定 broker
            rc = osp.process_user_common_allowlist(
                settings_path=p, dry_run=False, no_backup=True
            )
        self.assertEqual(rc, 0)
        allow = json.loads(p.read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertFalse(any(e.startswith(RENGA_PREFIX) for e in allow))
        broker = [e for e in allow if e.startswith(BROKER_PREFIX)]
        self.assertEqual(
            sorted(broker), sorted(t.allow_entries("user_common", flag="broker"))
        )

    def test_renga_explicit_is_no_op(self):
        p = self._write_tmp(self._RENGA_SETTINGS)
        before = p.read_bytes()
        with _env_transport("renga"):
            osp.process_user_common_allowlist(
                settings_path=p, dry_run=False, no_backup=True
            )
        self.assertEqual(p.read_bytes(), before)

    def test_broker_dry_run_does_not_write(self):
        p = self._write_tmp(self._RENGA_SETTINGS)
        before = p.read_bytes()
        with _env_transport("broker"):
            osp.process_user_common_allowlist(
                settings_path=p, dry_run=True, no_backup=True
            )
        self.assertEqual(p.read_bytes(), before)

    def test_broker_projects_messaging_floor(self):
        p = self._write_tmp(self._RENGA_SETTINGS)
        with _env_transport("broker"):
            rc = osp.process_user_common_allowlist(
                settings_path=p, dry_run=False, no_backup=True
            )
        self.assertEqual(rc, 0)
        allow = json.loads(p.read_text(encoding="utf-8"))["permissions"]["allow"]
        # renga MCP は消え、broker messaging tier が入る
        self.assertFalse(any(e.startswith(RENGA_PREFIX) for e in allow))
        broker = [e for e in allow if e.startswith(BROKER_PREFIX)]
        self.assertEqual(sorted(broker), sorted(t.allow_entries("user_common", flag="broker")))
        self.assertEqual(len(broker), 4)  # messaging tier
        # 非 MCP エントリ・env は保持
        self.assertIn("Bash(renga --version)", allow)
        self.assertEqual(
            json.loads(p.read_text(encoding="utf-8")).get("env"),
            {"CLAUDE_CODE_NO_FLICKER": "1"},
        )

    def test_broker_idempotent(self):
        p = self._write_tmp(self._RENGA_SETTINGS)
        with _env_transport("broker"):
            osp.process_user_common_allowlist(settings_path=p, dry_run=False, no_backup=True)
            after_first = p.read_bytes()
            osp.process_user_common_allowlist(settings_path=p, dry_run=False, no_backup=True)
            after_second = p.read_bytes()
        self.assertEqual(after_first, after_second)

    def test_merge_pure_drops_renga_and_ensures_tier(self):
        tier = t.allow_entries("user_common", flag="broker")
        out = osp.merge_user_common_allowlist(self._RENGA_SETTINGS, tier)
        allow = out["permissions"]["allow"]
        self.assertFalse(any(e.startswith(RENGA_PREFIX) for e in allow))
        for e in tier:
            self.assertIn(e, allow)
        # 元 dict は破壊されない
        self.assertTrue(
            any(e.startswith(RENGA_PREFIX) for e in self._RENGA_SETTINGS["permissions"]["allow"])
        )

    def test_merge_drops_stale_broker_ops_entries(self):
        # codex round-2 Major: 既存の上位 tier broker (ops) が残留して継承漏れ
        # しないこと。射影後は messaging tier ぴったりになる。
        tier = t.allow_entries("user_common", flag="broker")
        stale = {
            "permissions": {
                "allow": [
                    "Bash(git add:*)",
                    "mcp__org-broker__send_message",   # messaging (残る)
                    "mcp__org-broker__spawn_pane",     # ops tier の stale (除去対象)
                    "mcp__org-broker__close_pane",     # ops tier の stale (除去対象)
                    "mcp__renga-peers__list_panes",    # 反対 transport の stale
                ]
            }
        }
        out = osp.merge_user_common_allowlist(stale, tier)
        broker = [e for e in out["permissions"]["allow"] if e.startswith(BROKER_PREFIX)]
        # ぴったり messaging tier に射影される (ops の残骸なし)
        self.assertEqual(sorted(broker), sorted(tier))
        self.assertNotIn("mcp__org-broker__spawn_pane", broker)
        self.assertNotIn("mcp__org-broker__close_pane", broker)
        # renga 残骸も消える / 非 transport は残る
        self.assertFalse(any(e.startswith(RENGA_PREFIX) for e in out["permissions"]["allow"]))
        self.assertIn("Bash(git add:*)", out["permissions"]["allow"])

    def test_merge_creates_allow_when_absent(self):
        tier = t.allow_entries("user_common", flag="broker")
        out = osp.merge_user_common_allowlist({"env": {}}, tier)
        self.assertEqual(out["permissions"]["allow"], list(tier))
        self.assertEqual(out["env"], {})  # 他 key は invent しない/保持

    def test_merge_malformed_shape_raises(self):
        tier = t.allow_entries("user_common", flag="broker")
        with self.assertRaises(ValueError):
            osp.merge_user_common_allowlist(
                {"permissions": {"allow": "not-a-list"}}, tier
            )
        with self.assertRaises(ValueError):
            osp.merge_user_common_allowlist({"permissions": [1, 2]}, tier)

    def test_unknown_transport_aborts_rc2_without_write(self):
        # codex round-2 Minor: 未知 ORG_TRANSPORT は traceback でなく rc=2 abort。
        p = self._write_tmp(self._RENGA_SETTINGS)
        before = p.read_bytes()
        with _env_transport("bogus"):
            rc = osp.process_user_common_allowlist(
                settings_path=p, dry_run=False, no_backup=True
            )
        self.assertEqual(rc, 2)
        self.assertEqual(p.read_bytes(), before)  # 書込なし

    def test_broker_idempotent_after_stale_cleanup(self):
        # stale ops が混ざった初期状態でも 1 回目で射影、2 回目で no-op。
        stale = {
            "permissions": {
                "allow": [
                    "mcp__org-broker__spawn_pane",
                    "mcp__renga-peers__list_panes",
                    "Bash(git add:*)",
                ]
            }
        }
        p = self._write_tmp(stale)
        with _env_transport("broker"):
            osp.process_user_common_allowlist(settings_path=p, dry_run=False, no_backup=True)
            first = p.read_bytes()
            osp.process_user_common_allowlist(settings_path=p, dry_run=False, no_backup=True)
            second = p.read_bytes()
        self.assertEqual(first, second)
        allow = json.loads(first.decode("utf-8"))["permissions"]["allow"]
        self.assertNotIn("mcp__org-broker__spawn_pane", allow)
        self.assertEqual(
            sorted(e for e in allow if e.startswith(BROKER_PREFIX)),
            sorted(t.allow_entries("user_common", flag="broker")),
        )


if __name__ == "__main__":
    unittest.main()
