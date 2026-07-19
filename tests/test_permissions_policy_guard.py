"""repo-shared 設定 (.claude/settings.json) の permissions ポリシーをピンするテスト。

背景: 2026-07-05 に混入した ``Bash(gh pr merge:*)`` allow が 15 日間残留した。
merge は人間ゲート（窓口経由の承認）専用の不可逆操作であり、allow への残留は
「設定はポリシーだが、それを守るテストがない」ことの実証になった。
本テストは ``test_dispatcher_resume_loop_invariant.py`` と同じ pin 方式で
3 つの不変条件を機械的に固定し、再発を防ぐ:

1. 負の不変条件 — allow に不可逆操作（gh pr merge / gh pr create / gh pr close /
   git push / gh api / gh repo）へ許可を与えるパターンが存在したら、該当行を
   名指しして fail する。
2. no-verify 系・force-push 系の deny 行が実在する。
3. PreToolUse に block-no-verify.sh / block-dangerous-git.sh の配線が実在する。

対象は git tracked な repo-shared 設定のみ（各ロールの settings.local.json は
``tools/check_role_configs.py`` + ``tools/org_extension_schema.json`` が担当）。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"

# 不可逆・高影響のため allow が許可を与えてはならない操作の代表インスタンス。
# allow ルールを glob として展開し、これらのプローブのいずれかにマッチしたら
# 「そのルールは不可逆操作を包含している」と判定する。`Bash(gh pr merge:*)` の
# ような明示形だけでなく、`Bash(gh:*)` / `Bash(gh *)` / `Bash(git *)` /
# `Bash(*)` のような広域パターン（Codex round 1 指摘のギャップ）も検出できる。
IRREVERSIBLE_COMMAND_PROBES = [
    ("gh pr merge", ["gh pr merge", "gh pr merge 123 --admin"]),
    ("gh pr create", ["gh pr create", "gh pr create -f"]),
    ("gh pr close", ["gh pr close", "gh pr close 123"]),
    ("git push", ["git push", "git push origin main", "git -C /x push origin main"]),
    ("gh api", ["gh api", "gh api repos/o/r -X DELETE"]),
    ("gh repo", ["gh repo", "gh repo delete o/r --yes"]),
]


def _rule_pattern_regex(cmd_pattern: str) -> re.Pattern[str]:
    """Bash allow ルールの中身（例 ``gh issue create:*``）を包含判定用 regex にする。

    Claude Code の permission ルールで使われる末尾 ``:*`` / ``*`` を「任意の
    続き」として展開し、それ以外はリテラル一致として扱う。
    """
    normalized = cmd_pattern.replace(":*", "*")
    parts = [re.escape(p) for p in normalized.split("*")]
    return re.compile("^" + ".*".join(parts) + "$")

# deny 行の実在を確認する族。settings.json の網羅バリアント全部ではなく、
# 各族の正準形（素の git commit / git push 形）を pin する。
REQUIRED_DENY_NO_VERIFY = [
    "Bash(git commit --no-verify*)",
    "Bash(git push --no-verify*)",
]
REQUIRED_DENY_FORCE_PUSH = [
    "Bash(git push --force*)",
    "Bash(git push -f*)",
]

REQUIRED_PRETOOLUSE_SCRIPTS = [
    "block-no-verify.sh",
    "block-dangerous-git.sh",
]

_BASH_RULE_RE = re.compile(r"^Bash\((.*)\)$")


def _bash_command_of(rule: str) -> str | None:
    """``Bash(<cmd>)`` 形の allow ルールから <cmd> を取り出す（非 Bash は None）。"""
    m = _BASH_RULE_RE.match(rule.strip())
    return m.group(1).strip() if m else None


class PermissionsPolicyGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SETTINGS_PATH.exists(), f"missing {SETTINGS_PATH}")
        self.settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        self.permissions = self.settings.get("permissions", {})

    def test_allow_has_no_irreversible_operations(self) -> None:
        """不変条件 1: allow に不可逆操作を包含するパターンが存在したら該当行名で fail。"""
        allow = self.permissions.get("allow", [])
        self.assertIsInstance(allow, list, "permissions.allow が list でない")
        violations: list[str] = []
        for rule in allow:
            cmd = _bash_command_of(rule)
            if cmd is None:
                continue
            rule_re = _rule_pattern_regex(cmd)
            for label, probes in IRREVERSIBLE_COMMAND_PROBES:
                if any(rule_re.match(probe) for probe in probes):
                    violations.append(f"{rule!r} (covers irreversible op: {label})")
                    break
        self.assertEqual(
            violations,
            [],
            ".claude/settings.json permissions.allow に不可逆操作の許可が混入:\n  "
            + "\n  ".join(violations)
            + "\nこれらは人間ゲート（窓口経由の承認）専用であり allow してはならない",
        )

    def test_deny_pins_no_verify_family(self) -> None:
        """不変条件 2a: no-verify 系 deny 行の実在。"""
        deny = self.permissions.get("deny", [])
        for entry in REQUIRED_DENY_NO_VERIFY:
            self.assertIn(
                entry,
                deny,
                f"permissions.deny から no-verify 系の正準行が消えている: {entry!r}",
            )

    def test_deny_pins_force_push_family(self) -> None:
        """不変条件 2b: force-push 系 deny 行の実在。"""
        deny = self.permissions.get("deny", [])
        for entry in REQUIRED_DENY_FORCE_PUSH:
            self.assertIn(
                entry,
                deny,
                f"permissions.deny から force-push 系の正準行が消えている: {entry!r}",
            )

    def test_pretooluse_hooks_wired(self) -> None:
        """不変条件 3: PreToolUse に安全 hook 2 本の配線が実在する。"""
        pre_tool_use = self.settings.get("hooks", {}).get("PreToolUse", [])
        self.assertTrue(pre_tool_use, "hooks.PreToolUse が空または欠落")
        bash_commands: list[str] = []
        for entry in pre_tool_use:
            if "Bash" not in entry.get("matcher", ""):
                continue
            for hook in entry.get("hooks", []):
                if hook.get("type") == "command":
                    bash_commands.append(hook.get("command", ""))
        for script in REQUIRED_PRETOOLUSE_SCRIPTS:
            self.assertTrue(
                any(script in cmd for cmd in bash_commands),
                f"PreToolUse (matcher=Bash) に {script} の配線が無い",
            )


if __name__ == "__main__":
    unittest.main()
