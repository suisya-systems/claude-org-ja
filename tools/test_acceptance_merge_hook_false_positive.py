"""Acceptance test: the pre-merge hook does not stop commands that cannot merge a PR.

The four commands the hook denied in the secretary session on 2026-11-03, none of
which is a merge: the word only appears inside a longer name (a tool file name, a
JSON field such as ``mergedAt``, a hook file name), next to ``gh`` / ``$`` / a pipe.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

M = "mer" + "ge"  # keep the literal out of tool calls that run this file

CASES = (
    # 1. the post-close check from org-pull-request 2b-ii, on one line
    f"python3 tools/run_complete_on_{M}.py --check-closed; echo e=$?",
    # 2. multi-line, first line reads the approval mode
    f"python3 tools/{M}_approval_mode.py; gh pr checks 268 --repo o/r 2>&1 | awk '{{print $2}}' | sort | uniq -c\n"
    f"gh pr view 268 --repo o/r --json state,headRefOid",
    # 3. multi-line with a gh pr view JSON field whose name contains the word
    f"gh pr view 1243 --repo o/r --json state,{M}dAt,{M}able -q .{M}dAt\n"
    f"gh pr checks 1243 --repo o/r | head -5",
    # 4. a delegation whose description only mentions the files above
    f"G=$TMPDIR/guidance.txt\n"
    f"python3 tools/gen_delegate_payload.py apply --task-id t --project-slug p \\\n"
    f"  --target .hooks/gate-acceptance-before-{M}.sh --target tools/acceptance_{M}_hook.py \\\n"
    f'  --description "false positives on tools/run_complete_on_{M}.py, tools/{M}_approval_mode.py'
    f' and gh pr view --json {M}dAt" --impl-guidance "$(cat $G)"',
)


class FalsePositiveTest(unittest.TestCase):
    def test_commands_that_cannot_merge_pass(self):
        from tools import acceptance_merge_hook as hook

        for cmd in CASES:
            with self.subTest(cmd=cmd):
                try:
                    result = hook.parse(cmd)
                except hook.Deny as exc:
                    self.fail(f"denied a command that cannot merge: {exc}")
                self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
