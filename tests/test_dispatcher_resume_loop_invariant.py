"""dispatcher-resume の /loop 再帰防止 invariant をピンするテスト。

背景: ``knowledge/raw/2026-06-19-dispatcher-resume-loop-recursion.md``

``/dispatcher-resume`` の Step 5 が ``/loop 3m`` を prompt 省略のまま skill 実行
ターン内で起動すると、loop の反復対象としてアクティブな slash command
(= ``/dispatcher-resume`` 自身) が捕捉され、skill が 3 分ごとに自己再帰する。
本テストはその再発を機械的に防ぐ:

- ``/loop`` の反復対象 (prompt) は slash command であってはならない。
- ``/loop`` のコマンド行に skill 自身 (``/dispatcher-resume``) を名指ししない。
- ``/loop`` は worker-monitoring を指す monitoring 専用ディレクティブで arm する。
- invariant が SKILL.md(.in) にコメントとして明文化されている。

SoT は ``SKILL.md.in``、``SKILL.md`` は ``tools/gen_skill_prose.py`` の生成物。
両方を検査して source と rendered が同じ不変条件を満たすことを保証する。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "dispatcher-resume"
SKILL_SRC = SKILL_DIR / "SKILL.md.in"
SKILL_OUT = SKILL_DIR / "SKILL.md"

INVARIANT_MARKER = "INVARIANT(loop-prompt)"
_INTERVAL_RE = re.compile(r"^\d+[smh]$")


def _fenced_loop_command_lines(text: str) -> list[str]:
    """fenced code block 内で ``/loop`` から始まる行 (= リテラルの起動コマンド) を返す。

    prose や HTML コメント中の ``/loop`` 言及は対象外 (実際に dispatcher が打つ
    コマンドだけを検査する)。
    """
    out: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and raw.strip().startswith("/loop"):
            out.append(raw.strip())
    return out


def _loop_prompt(line: str) -> str:
    """``/loop [interval] <prompt>`` の <prompt> 部分を取り出す。"""
    rest = line[len("/loop"):].strip()
    parts = rest.split(None, 1)
    if parts and _INTERVAL_RE.match(parts[0]):
        return parts[1].strip() if len(parts) > 1 else ""
    return rest


class DispatcherResumeLoopInvariant(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SKILL_SRC.exists(), f"missing {SKILL_SRC}")
        self.assertTrue(SKILL_OUT.exists(), f"missing {SKILL_OUT}")
        self.files = {
            "SKILL.md.in": SKILL_SRC.read_text(encoding="utf-8"),
            "SKILL.md": SKILL_OUT.read_text(encoding="utf-8"),
        }

    def test_loop_arms_monitoring_directive_not_a_slash_command(self) -> None:
        for label, text in self.files.items():
            loops = _fenced_loop_command_lines(text)
            self.assertTrue(loops, f"{label}: fenced な /loop コマンド行が見つからない")
            armed = False
            for line in loops:
                prompt = _loop_prompt(line)
                # 反復対象が空 (prompt 省略) だと、skill 実行ターン内では
                # アクティブな slash command が捕捉され再帰する。
                self.assertTrue(
                    prompt,
                    f"{label}: /loop の反復対象 (prompt) が空 (省略禁止): {line!r}",
                )
                # 反復対象が slash command であってはならない (= 再帰の直接原因)。
                self.assertFalse(
                    prompt.startswith("/"),
                    f"{label}: /loop の反復対象が slash command: {line!r}",
                )
                # コマンド行に skill 自身を名指ししない。
                self.assertNotIn(
                    "/dispatcher-resume",
                    line,
                    f"{label}: /loop コマンド行が /dispatcher-resume を含む (再帰): {line!r}",
                )
                if "worker-monitoring" in prompt:
                    armed = True
            self.assertTrue(
                armed,
                f"{label}: /loop に worker-monitoring を指す monitoring "
                f"ディレクティブが無い",
            )

    def test_invariant_documented(self) -> None:
        for label, text in self.files.items():
            self.assertIn(
                INVARIANT_MARKER,
                text,
                f"{label}: {INVARIANT_MARKER} コメントが無い (invariant 明文化を維持すること)",
            )


if __name__ == "__main__":
    unittest.main()
