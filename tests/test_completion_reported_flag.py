"""Issue #658 completion_reported_at フラグの契約をピンする統合テスト。

背景: dispatcher の ``pane_output_without_peer_msg`` 検知 (worker-monitoring.md
Step 5.2) が「worker が secretary へ完了報告済みで review 待ちの正常 idle」を
silent dead-lock と誤判定する false positive (実運用で 4 回再現) を、Issue #658
案 1 (``completion_reported_at`` フラグ) で抑止する。

本テストはその契約が prose (SoT = ``.in`` / rendered = ``.md``) と hand-written
contract に一貫して埋まっていることを機械的に固定する。Step 5.2 の idle 検知は
決定的 helper を持たない prose 契約 (worker-monitoring.md 「本 PR では JSON ファイル
経由の prose 契約に留め、helper script 化は将来課題」) のため、既存の
``test_dispatcher_resume_loop_invariant.py`` と同じ prose-invariant 方式で検証する。

設計 review の Blocker / Major / Minor / Nit を各々別ケースにマップする:

- Blocker: T6 再指示の ``WORKER_REOPENED`` clear 契約 (無いとレビュー修正中の本物の
  silent dead-lock を永久に見逃す)。
- Major: (1) ``WORKER_COMPLETION_NOTED`` は non-blocking、(2) skip は
  ``pane_output_without_peer_msg`` に限定 (ERROR / APPROVAL_BLOCKED / pane exit /
  STALL は完了後も有効)、(3) timeout ではなく lifecycle event で解除。
- Minor: schema に ``completion_reported_at: null | ISO-8601 UTC`` を明示 /
  contract への additive 追記。
- Nit: message 本文に ``task_id`` と ``received_at`` を含める。

``.in`` (SoT) と ``.md`` (rendered) の両方を検査し、生成 drift で契約が片系統だけに
残る事故を防ぐ (rendered の byte 一致自体は ``tools/test_gen_skill_prose.py``
``test_production_manifest_no_drift`` が別途担保する)。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

WORKER_MONITORING_IN = REPO_ROOT / ".dispatcher" / "references" / "worker-monitoring.md.in"
WORKER_MONITORING_MD = REPO_ROOT / ".dispatcher" / "references" / "worker-monitoring.md"
ORG_DELEGATE_IN = REPO_ROOT / ".claude" / "skills" / "org-delegate" / "SKILL.md.in"
ORG_DELEGATE_MD = REPO_ROOT / ".claude" / "skills" / "org-delegate" / "SKILL.md"
ORG_PR_IN = REPO_ROOT / ".claude" / "skills" / "org-pull-request" / "SKILL.md.in"
ORG_PR_MD = REPO_ROOT / ".claude" / "skills" / "org-pull-request" / "SKILL.md"
ROLE_CONTRACT = REPO_ROOT / "docs" / "contracts" / "role-contract.md"
LIFECYCLE_CONTRACT = REPO_ROOT / "docs" / "contracts" / "delegation-lifecycle-contract.md"
DISPATCHER_CLAUDE_MD = REPO_ROOT / ".dispatcher" / "CLAUDE.md"


def _read(path: Path) -> str:
    assert path.exists(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


class WorkerMonitoringContract(unittest.TestCase):
    """worker-monitoring.md(.in) 側 (dispatcher) の Step 5.2 skip 契約。"""

    def _both(self):
        # (source label, text) を .in / .md 双方について返す。
        return (
            ("worker-monitoring.md.in", _read(WORKER_MONITORING_IN)),
            ("worker-monitoring.md", _read(WORKER_MONITORING_MD)),
        )

    def test_schema_example_has_completion_reported_at_null(self):
        """Minor: schema 例に completion_reported_at: null を明示 (migration 不要)。"""
        for label, text in self._both():
            with self.subTest(source=label):
                self.assertIn('"completion_reported_at": null', text,
                              f"{label}: schema JSON 例に completion_reported_at: null が無い")
                self.assertIn("null | ISO-8601 UTC", text,
                              f"{label}: completion_reported_at の型注記が無い")
                self.assertIn("migration", text,
                              f"{label}: migration 不要の明記が無い")

    def test_step2_sets_on_completion_noted_and_clears_on_reopened(self):
        """Step 2 が WORKER_COMPLETION_NOTED で set / WORKER_REOPENED で clear する。"""
        for label, text in self._both():
            with self.subTest(source=label):
                self.assertIn("WORKER_COMPLETION_NOTED", text)
                self.assertIn("WORKER_REOPENED", text)
                # lifecycle-control として扱い anomaly ledger に乗せない。
                self.assertIn("lifecycle-control", text,
                              f"{label}: 監視制御メッセージが lifecycle-control 扱いと明記されていない")

    def test_step52_gate_condition_on_completion_reported_at(self):
        """(b) の fire 条件に completion_reported_at == null gate がある。"""
        for label, text in self._both():
            with self.subTest(source=label):
                self.assertIn("completion-review-skip", text,
                              f"{label}: (d) に completion-review-skip 分岐が無い")
                self.assertIn("pane_output_completion_review_skip", text,
                              f"{label}: skip 時の soft-note kind が無い")

    def test_skip_scoped_to_pane_output_only(self):
        """Major: skip は pane_output_without_peer_msg のみ。他監視は完了後も有効。"""
        for label, text in self._both():
            with self.subTest(source=label):
                # ERROR / APPROVAL_BLOCKED / STALL が完了後も有効なまま残ると明記。
                self.assertIn("完了報告後も有効なまま", text,
                              f"{label}: 完了後も他監視が有効である旨の明記が無い")
                for keyword in ("APPROVAL_BLOCKED", "ERROR", "STALL"):
                    self.assertIn(keyword, text,
                                  f"{label}: skip 限定の説明に {keyword} が無い")

    def test_no_timeout_lifecycle_release_only(self):
        """Major: timeout による自然失効を持たず lifecycle event でのみ解除。"""
        for label, text in self._both():
            with self.subTest(source=label):
                self.assertIn("timeout による自然失効は持たない", text,
                              f"{label}: timeout 無しの明記が無い")
                # 3 解除経路: WORKER_REOPENED (T6) / CLOSE_PANE・pane 消失 / 再完了。
                self.assertIn("WORKER_REOPENED", text)
                self.assertIn("CLOSE_PANE", text)

    def test_not_a_completion_determination(self):
        """Minor: 完了判定ではなく監視抑止用の受領通知だと明文化。"""
        for label, text in self._both():
            with self.subTest(source=label):
                self.assertIn("監視抑止用の受領通知", text,
                              f"{label}: 「監視抑止用の受領通知」の明文化が無い")
                self.assertIn("完了を判定しない", text,
                              f"{label}: dispatcher が完了判定しない旨の明記が無い")


class SecretaryEmissionContract(unittest.TestCase):
    """org-delegate §2a (完了受領 → NOTED) / org-pull-request 2c (再指示 → REOPENED)。"""

    def test_delegate_2a_emits_completion_noted_nonblocking(self):
        """Major(1) + Nit: §2a が non-blocking で NOTED を送り本文に task_id/received_at。"""
        for label, path in (("SKILL.md.in", ORG_DELEGATE_IN), ("SKILL.md", ORG_DELEGATE_MD)):
            text = _read(path)
            with self.subTest(source=f"org-delegate/{label}"):
                self.assertIn("WORKER_COMPLETION_NOTED", text)
                self.assertIn("非 blocking", text,
                              "§2a: non-blocking の明記が無い (blocking wait 禁止)")
                self.assertIn("待たない", text,
                              "§2a: dispatcher 応答を待たない旨が無い")
                # Nit: 本文に task_id と received_at を含める。
                self.assertIn("task_id=", text)
                self.assertIn("received_at=", text)

    def test_pr_2c_emits_reopened_before_reinstruction(self):
        """Blocker: 2c が再指示の前に WORKER_REOPENED を送り completion_reported_at を clear。"""
        for label, path in (("SKILL.md.in", ORG_PR_IN), ("SKILL.md", ORG_PR_MD)):
            text = _read(path)
            with self.subTest(source=f"org-pull-request/{label}"):
                self.assertIn("WORKER_REOPENED", text)
                self.assertIn("completion_reported_at", text)
                self.assertIn("reopened_at=", text)
                # clear が無いと sticky skip で silent dead-lock を見逃す旨の根拠。
                self.assertIn("silent dead-lock", text,
                              "2c: clear 契約の根拠 (見逃しリスク) が無い")

    def test_delegate_2c_crossrefs_reopened(self):
        """§2c (org-delegate) が再指示時の WORKER_REOPENED 解除を cross-ref する。"""
        for label, path in (("SKILL.md.in", ORG_DELEGATE_IN), ("SKILL.md", ORG_DELEGATE_MD)):
            text = _read(path)
            with self.subTest(source=f"org-delegate/{label}"):
                self.assertIn("WORKER_REOPENED", text,
                              "org-delegate 2c が WORKER_REOPENED を参照していない")


class ContractAdditiveNotes(unittest.TestCase):
    """Minor: role-contract / delegation-lifecycle への additive 追記。"""

    def test_role_contract_dispatcher_input_note(self):
        text = _read(ROLE_CONTRACT)
        self.assertIn("WORKER_COMPLETION_NOTED", text)
        self.assertIn("WORKER_REOPENED", text)
        self.assertIn("NOT completion determinations", text,
                      "role-contract: 完了判定ではない旨の明記が無い")

    def test_lifecycle_contract_t4_t6_notes(self):
        text = _read(LIFECYCLE_CONTRACT)
        # T4 = completion handoff, T6 = release。additive を明記。
        self.assertIn("Monitoring-suppression handoff", text)
        self.assertIn("Monitoring-suppression release", text)
        self.assertIn("WORKER_COMPLETION_NOTED", text)
        self.assertIn("WORKER_REOPENED", text)

    def test_dispatcher_claude_entrypoint_mentions_gate(self):
        text = _read(DISPATCHER_CLAUDE_MD)
        self.assertIn("completion_reported_at", text,
                      ".dispatcher/CLAUDE.md entry-point summary に gate の言及が無い")


if __name__ == "__main__":
    unittest.main()
