"""ja 統合シーム (Epic #6 D / ja#513) の golden / 不変条件テスト。

設計 SoT: transport-lab ``docs/design/ja-migration-plan.md`` §5.1 / §5.2(i) /
§5.4 / §8 Issue D。検証する性質は 3 つ:

1. **flag=renga (既定) で現行と bit 等価** — ja の生成物 (worker_brief /
   delegate body / send_plan) が 1 byte も変わらないこと (切戻し忠実性 /
   非破壊の絶対条件)。golden fixture は
   ``tests/fixtures/transport_seam/*.golden.md``。
2. **flag=broker で全生成物が broker 面を指す** — transport プレフィックスが
   ``mcp__org-broker__`` に振り替わり、renga 面が残らないこと。
3. **両生成器出力 == descriptor** — ja 側 ``tools.transport`` が runtime の
   ``claude_org_runtime.transport`` descriptor と一致し、生成器がハードコード
   ではなく descriptor を読んでいること (二重管理 = drift が無いこと)。
"""
from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from pathlib import Path

from claude_org_runtime import transport as rt_descriptor

from tools import gen_delegate_payload as gdp
from tools import gen_worker_brief as gwb
from tools import transport as t

FIXTURES = Path(__file__).parent / "fixtures" / "transport_seam"

# (fixture 名, self_edit, depth) — golden 生成時と同じ入力でなければ bit 等価
# 検証が成立しないので、_base_config と対で固定する。
_BRIEF_CASES = (
    ("worker_brief_normal_full.golden.md", False, "full"),
    ("worker_brief_normal_minimal.golden.md", False, "minimal"),
    ("worker_brief_self_edit_full.golden.md", True, "full"),
    ("worker_brief_self_edit_minimal.golden.md", True, "minimal"),
)


def _base_config(self_edit: bool, depth: str) -> dict:
    return {
        "task": {
            "id": "demo-task",
            "description": "デモタスク。X を Y に変更する。",
            "verification_depth": depth,
            "branch": "demo-task",
            "commit_prefix": "feat(tools):",
            "refs_issues": [121, 214],
        },
        "worker": {
            "dir": "/tmp/workers/demo-task",
            "pattern": "B" if self_edit else "A",
            "role": "claude-org-self-edit" if self_edit else "default",
            "self_edit": self_edit,
        },
        "project": {"name": "claude-org-ja", "description": "テスト用説明"},
        "paths": {"claude_org": "/home/user/work/claude-org"},
    }


@contextmanager
def _tmp_send_plan(plan):
    """``_write_send_plan`` を一時ファイルへ書き、生 byte 列を yield する。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "send_plan.json"
        gdp._write_send_plan(plan, out_path=out)
        yield out.read_text(encoding="utf-8")


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


class WorkerBriefBitEquivalence(unittest.TestCase):
    """flag=renga (opt-in fallback) で worker_brief が golden と byte 一致し、
    既定 (無設定) は broker 面を指す (Epic #586 既定フリップ)。

    renga golden は切戻し忠実性の不変条件として retain。明示 renga 経路で
    1 byte も変わらないことを担保しつつ、無設定の既定は broker にフリップした
    ことを併せて検証する。"""

    def test_explicit_renga_is_bit_equivalent(self):
        # renga は opt-in fallback として retain。明示時は golden と byte 一致
        # (切戻し忠実性 / 非破壊の絶対条件は renga 経路で不変)。
        with _env_transport("renga"):
            for name, self_edit, depth in _BRIEF_CASES:
                with self.subTest(fixture=name):
                    golden = (FIXTURES / name).read_text(encoding="utf-8")
                    out = gwb.render(_base_config(self_edit, depth))
                    self.assertEqual(out, golden)
                    self.assertIn(
                        "mcp__renga-peers__send_message", out
                    )

    def test_default_unset_is_broker_surface(self):
        # Epic #586: 既定 (ORG_TRANSPORT 無設定) は broker にフリップ。
        with _env_transport(None):
            for name, self_edit, depth in _BRIEF_CASES:
                with self.subTest(fixture=name):
                    out = gwb.render(_base_config(self_edit, depth))
                    self.assertIn("mcp__org-broker__send_message", out)
                    self.assertNotIn("mcp__renga-peers__", out)


class WorkerBriefBrokerSurface(unittest.TestCase):
    """flag=broker で worker_brief が broker 面のみを指す。"""

    def test_broker_points_at_org_broker(self):
        with _env_transport("broker"):
            for name, self_edit, depth in _BRIEF_CASES:
                with self.subTest(fixture=name):
                    out = gwb.render(_base_config(self_edit, depth))
                    self.assertIn("mcp__org-broker__send_message", out)
                    self.assertNotIn("mcp__renga-peers__", out)

    def test_broker_diff_is_only_the_prefix(self):
        # broker 出力は renga golden の prefix 置換のみで一致する
        # (生成物の差分は transport 面に閉じている = §5.2 単一シーム)。
        for name, self_edit, depth in _BRIEF_CASES:
            with self.subTest(fixture=name):
                golden = (FIXTURES / name).read_text(encoding="utf-8")
                with _env_transport("broker"):
                    out = gwb.render(_base_config(self_edit, depth))
                rehydrated = out.replace(
                    "mcp__org-broker__send_message",
                    "mcp__renga-peers__send_message",
                )
                self.assertEqual(rehydrated, golden)


class WorkerBriefMatchesDescriptor(unittest.TestCase):
    """worker_brief が render する transport 呼び出しが descriptor と一致。"""

    def test_rendered_call_equals_descriptor(self):
        for flag in t.TRANSPORTS:
            with self.subTest(flag=flag), _env_transport(flag):
                out = gwb.render(_base_config(False, "full"))
                expected = rt_descriptor.get_surface(flag).fq_prefix + "send_message"
                self.assertIn(f"{expected}(to_id=\"secretary\"", out)


class DelegatePayloadTransportNeutral(unittest.TestCase):
    """delegate body / send_plan は transport 中立 (本文 prose は E/#514 scope)。

    delegate payload で descriptor 駆動なのは「窓口がコピーする send_message の
    輸送先サーバー名」を示す operator next-step ヒント。本文 (DELEGATE body) と
    send_plan manifest は to_id/message のみで FQ ツール名を含まないため、flag に
    依らず byte 一致する。
    """

    def _body(self) -> str:
        layout = _StubLayout()
        return gdp._format_delegate_body(
            layout=layout,
            task_id="demo-task",
            description="デモタスク。X を Y に変更する。",
            project_path="/tmp/proj",
            permission_mode="auto",
            verification_depth="full",
            brief_filename="CLAUDE.md",
        )

    def test_delegate_body_is_transport_neutral(self):
        with _env_transport(None):
            renga_body = self._body()
        with _env_transport("broker"):
            broker_body = self._body()
        self.assertEqual(renga_body, broker_body)
        # 本文に FQ ツール名 (mcp__*) は載せない (中立)。
        self.assertNotIn("mcp__", renga_body)

    def test_delegate_body_byte_equivalent_to_golden(self):
        # delegate body は今回 descriptor 化していない (prose は §5.2(ii) =
        # D スコープ外、E/#514 へ分離)。生成器変更で本文が 1 byte もずれない
        # ことを golden で固定。
        golden = (FIXTURES / "delegate_body.golden.txt").read_text(encoding="utf-8")
        with _env_transport(None):
            self.assertEqual(self._body(), golden)
        with _env_transport("broker"):
            self.assertEqual(self._body(), golden)

    def test_send_plan_manifest_is_transport_neutral(self):
        import json

        plan = _StubPlan(delegate_body=self._body())
        with _tmp_send_plan(plan) as raw:
            payload = json.loads(raw)
        self.assertEqual(set(payload), {"to_id", "message", "summary"})
        self.assertEqual(payload["to_id"], "dispatcher")
        # manifest に FQ ツール名は載せない (窓口が descriptor から選ぶ)。
        self.assertNotIn("mcp__", raw)

    def test_send_plan_byte_equivalent_to_golden(self):
        # send_plan.json の byte 等価 (indent / 末尾改行 / summary を含む)。
        golden = (FIXTURES / "send_plan.golden.json").read_text(encoding="utf-8")
        plan = _StubPlan(delegate_body=self._body())
        with _env_transport(None):
            with _tmp_send_plan(plan) as raw_renga:
                pass
        with _env_transport("broker"):
            with _tmp_send_plan(plan) as raw_broker:
                pass
        self.assertEqual(raw_renga, golden)
        # transport 中立なので broker でも byte 同一。
        self.assertEqual(raw_broker, golden)


class DelegateNextStepHintMatchesDescriptor(unittest.TestCase):
    """operator next-step ヒントの輸送名が descriptor と一致 (== descriptor)。"""

    def test_hint_server_is_descriptor_driven(self):
        for flag in t.TRANSPORTS:
            with self.subTest(flag=flag), _env_transport(flag):
                hint = gdp._next_step_hint()
                server = rt_descriptor.get_surface(flag).server
                self.assertIn(f"{server} send_message call.", hint)

    def test_hint_default_is_broker_byte_for_byte(self):
        # Epic #586: 既定 (無設定) は broker。hint も broker server を指す。
        with _env_transport(None):
            self.assertEqual(
                gdp._next_step_hint(),
                "Next step: copy send_plan.json's `to_id`/`message` into a "
                "org-broker send_message call.",
            )

    def test_hint_explicit_renga_byte_for_byte(self):
        # renga (opt-in fallback) 明示時は renga-peers を指す (切戻し忠実性)。
        with _env_transport("renga"):
            self.assertEqual(
                gdp._next_step_hint(),
                "Next step: copy send_plan.json's `to_id`/`message` into a "
                "renga-peers send_message call.",
            )


class JaSeamMatchesRuntimeDescriptor(unittest.TestCase):
    """ja 側 ``tools.transport`` が runtime descriptor と一致 (単一 SoT, no drift)。"""

    def test_resolution_order(self):
        # Epic #586: 無設定の既定は broker にフリップ。
        self.assertEqual(t.resolve(env={}), "broker")
        self.assertEqual(t.resolve(env={t.ENV_KEY: "renga"}), "renga")
        self.assertEqual(t.resolve(env={t.ENV_KEY: "broker"}), "broker")
        # explicit > env (§5.1)
        self.assertEqual(t.resolve("renga", env={t.ENV_KEY: "broker"}), "renga")

    def test_default_is_broker(self):
        # Epic #586: DEFAULT_TRANSPORT が renga→broker にフリップ。
        self.assertEqual(t.DEFAULT_TRANSPORT, "broker")
        self.assertEqual(t.resolve(env={}), t.DEFAULT_TRANSPORT)

    def test_unknown_flag_rejected(self):
        with self.assertRaises(ValueError):
            t.resolve(env={t.ENV_KEY: "carrier-pigeon"})

    def test_send_message_is_in_descriptor_tool_set(self):
        # bare 動詞 send_message が descriptor の公開 tool 集合に実在すること
        # (ja 側ハードコード名が descriptor から drift していない anchor)。
        for flag in t.TRANSPORTS:
            with self.subTest(flag=flag):
                self.assertIn(
                    "send_message",
                    rt_descriptor.get_surface(flag).tools_for_role("worker"),
                )

    def test_send_message_call_matches_descriptor(self):
        for flag in t.TRANSPORTS:
            with self.subTest(flag=flag):
                expected = (
                    rt_descriptor.get_surface(flag).fq_prefix + "send_message"
                )
                self.assertEqual(t.send_message_call(env={t.ENV_KEY: flag}), expected)

    def test_server_and_prefix_match_descriptor(self):
        for flag in t.TRANSPORTS:
            with self.subTest(flag=flag):
                surf = rt_descriptor.get_surface(flag)
                self.assertEqual(t.server_name(env={t.ENV_KEY: flag}), surf.server)
                self.assertEqual(t.fq_prefix(env={t.ENV_KEY: flag}), surf.fq_prefix)

    def test_spawn_inject_matches_descriptor(self):
        for flag in t.TRANSPORTS:
            with self.subTest(flag=flag):
                surf = rt_descriptor.get_surface(flag)
                self.assertEqual(
                    t.spawn_inject(env={t.ENV_KEY: flag}),
                    surf.spawn_inject(),
                )
        # broker は mcp-config を具体化できる
        self.assertEqual(
            t.spawn_inject(env={t.ENV_KEY: "broker"}, broker_mcp_config="/x/b.json"),
            "--mcp-config /x/b.json",
        )

    def test_renga_surface_spawn_inject(self):
        # renga (opt-in fallback) の spawn 注入は dev-channel flag (非破壊)。
        self.assertEqual(
            t.spawn_inject(env={t.ENV_KEY: "renga"}),
            "--dangerously-load-development-channels server:renga-peers",
        )

    def test_default_unset_spawn_inject_is_broker(self):
        # Epic #586: 既定 (無設定) は broker の mcp-config 注入。
        self.assertEqual(t.spawn_inject(env={}), "--mcp-config <broker>")


# ---------------------------------------------------------------------------
# stubs (resolver / state.db を引かずに pure 関数だけを叩く)
# ---------------------------------------------------------------------------


class _StubLayout:
    pattern = "B"
    pattern_variant = "live_repo_worktree"
    role = "claude-org-self-edit"
    self_edit = True
    worker_dir = "/tmp/workers/demo-task"
    planned_branch = "feat/demo-task"


class _StubPlan:
    def __init__(self, delegate_body: str):
        self.delegate_body = delegate_body

    def to_summary_dict(self) -> dict:
        return {"task_id": "demo-task", "pattern": "B"}


if __name__ == "__main__":
    unittest.main()
