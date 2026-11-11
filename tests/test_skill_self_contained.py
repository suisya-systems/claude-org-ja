"""Skills carry no Issue/PR references and link out of their folder only to a source of truth.

A skill is read on every run. An Issue number in it is history, not a step, and a
relative link that leaves the skill folder breaks when the skill is used outside
this repository. The why behind a step lives in the skill's own
``references/rationale.md``; anything a step needs to run lives in the skill folder.

Links that leave the skill folder are allowed only to the single sources of truth
listed in ``ALLOWED_OUTSIDE`` (copying them would drift).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

# Repo-root relative. An entry ending in "/" allows every file under that directory.
ALLOWED_OUTSIDE = (
    "docs/contracts/",  # contracts
    ".dispatcher/references/",  # references shared with the dispatcher
    "CLAUDE.md",  # secretary role instructions
    ".dispatcher/CLAUDE.md",  # dispatcher role instructions
    "docs/journal-events.md",  # journal event vocabulary
    "registry/org-config.md",  # organisation settings
)

ISSUE_REF_PATTERNS = (
    re.compile(r"github\.com/[^\s)>\]]+/(?:issues|pull)/\d+"),
    re.compile(r"(?<![\w&/#])#\d+\b"),  # bare #123
    re.compile(r"\b[\w.-]+/?[\w.-]*#\d+\b"),  # ja#590, runtime#93, owner/repo#12
    re.compile(r"\b(?:Issue|issue|PR|Epic)\s+\d+\b"),  # "Issue 787"
)

INLINE_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REF_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", re.MULTILINE)


def skill_files() -> list[Path]:
    files = sorted(SKILLS_DIR.rglob("*.md")) + sorted(SKILLS_DIR.rglob("*.md.in"))
    return files


def strip_code_fences(text: str) -> str:
    # Links inside fenced code blocks are not rendered as links.
    return re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)


def issue_refs(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        for pat in ISSUE_REF_PATTERNS:
            hits.extend(m.group(0) for m in pat.finditer(line))
    return hits


def is_allowed(rel: str) -> bool:
    return any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in ALLOWED_OUTSIDE)


def outside_links(path: Path) -> list[str]:
    text = strip_code_fences(path.read_text(encoding="utf-8"))
    skill_root = SKILLS_DIR / path.relative_to(SKILLS_DIR).parts[0]
    bad = []
    for target in INLINE_LINK.findall(text) + REF_LINK.findall(text):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
            continue  # external URL (Issue URLs are caught by issue_refs) or in-page anchor
        target = target.split("#", 1)[0]
        if not target:
            continue
        base = REPO_ROOT if target.startswith("/") else path.parent
        resolved = (base / target.lstrip("/")).resolve()
        try:
            resolved.relative_to(skill_root)
            continue
        except ValueError:
            pass
        try:
            rel = resolved.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            bad.append(target)
            continue
        if not is_allowed(rel):
            bad.append(rel)
    return bad


class SkillSelfContainedTest(unittest.TestCase):
    def test_skill_files_exist(self):
        self.assertTrue(skill_files(), "no skill files found")

    def test_no_issue_or_pr_references(self):
        found = {}
        for f in skill_files():
            hits = issue_refs(f.read_text(encoding="utf-8"))
            if hits:
                found[f.relative_to(REPO_ROOT).as_posix()] = sorted(set(hits))
        self.assertEqual(
            found,
            {},
            "Issue/PR references in skill files (move the why to the skill's "
            "references/rationale.md without numbers, or leave it to the commit message)",
        )

    def test_links_leave_skill_folder_only_to_allowlist(self):
        found = {}
        for f in skill_files():
            bad = outside_links(f)
            if bad:
                found[f.relative_to(REPO_ROOT).as_posix()] = sorted(set(bad))
        self.assertEqual(
            found,
            {},
            "links out of the skill folder that are not in ALLOWED_OUTSIDE (bring the "
            "content into the skill folder, or mention the path without linking)",
        )

    def test_detectors(self):
        self.assertTrue(issue_refs("see #1271 and ja#590"))
        self.assertTrue(issue_refs("https://github.com/o/r/pull/12"))
        self.assertTrue(issue_refs("runtime#93"))
        self.assertFalse(issue_refs("## Step 5 / `%3` / [a](#step-5)"))
        self.assertTrue(is_allowed("docs/contracts/x.md"))
        self.assertFalse(is_allowed("tools/x.py"))


if __name__ == "__main__":
    unittest.main()
