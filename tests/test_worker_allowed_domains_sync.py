"""worker の sandbox allowedDomains が ja schema と runtime 定数で一致することを検査する。

claude-org-runtime 0.1.46 (runtime#191) は worker spawn に ``--settings`` で
``WORKER_SANDBOX_SETTINGS`` を渡す。その ``strictAllowlist`` があると Claude Code は
project 側 (ja の ``worker_roles.*.sandbox_by_pattern.*.network.allowedDomains``) を
無視するため、同じ一覧が 2 か所に重複する。片方だけ直すと worker の通信先が黙って
食い違うので、installed runtime の定数と比較する。定数の無い runtime (< 0.1.46) では skip。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "tools" / "org_extension_schema.json"

try:
    from claude_org_runtime.dispatcher.runner import WORKER_SANDBOX_SETTINGS
except ImportError:  # runtime 未インストール、または 0.1.46 未満
    WORKER_SANDBOX_SETTINGS = None


@unittest.skipIf(
    WORKER_SANDBOX_SETTINGS is None,
    "claude-org-runtime >= 0.1.46 (WORKER_SANDBOX_SETTINGS) not installed",
)
class WorkerAllowedDomainsSyncTest(unittest.TestCase):
    def test_every_worker_pattern_matches_runtime_overlay(self) -> None:
        expected = sorted(WORKER_SANDBOX_SETTINGS["sandbox"]["network"]["allowedDomains"])
        worker_roles = json.loads(SCHEMA.read_text(encoding="utf-8"))["worker_roles"]
        checked = 0
        for role, body in worker_roles.items():
            if not isinstance(body, dict):
                continue  # $comment_* entries
            for pattern, sandbox in (body.get("sandbox_by_pattern") or {}).items():
                with self.subTest(role=role, pattern=pattern):
                    self.assertEqual(
                        sorted(sandbox["network"]["allowedDomains"]), expected
                    )
                checked += 1
        self.assertGreater(checked, 0, "no worker sandbox_by_pattern bodies found")


if __name__ == "__main__":
    unittest.main()
