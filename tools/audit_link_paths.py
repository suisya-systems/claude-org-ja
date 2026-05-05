"""Audit markdown link path notation across in-scope docs.

Rule (Issue #322):
    In `[`DISPLAY`](TARGET)` links where DISPLAY is wrapped in backticks
    (treated as a path mention), DISPLAY must be the repo-root-relative
    path to the file resolved by TARGET, and TARGET is document-relative.

For each in-scope markdown file, this script:
  * extracts every link matching ``[`...`](...)`` whose DISPLAY looks like
    a filesystem path (contains '/' or '.md' or starts with '..'),
  * resolves TARGET (stripping any '#anchor') against the source file's
    directory and checks the resolved path exists in the repo,
  * checks that DISPLAY equals the repo-root-relative form of the resolved
    target (forward slashes, no leading './').

Exit code:
    0 -- all in-scope links are valid and conform to the rule.
    1 -- one or more violations or unresolved targets.

Run: ``py -3 tools/audit_link_paths.py`` from the repo root.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Glob patterns (relative to repo root) that define the in-scope markdown set.
IN_SCOPE_GLOBS = (
    "CLAUDE.md",
    ".curator/CLAUDE.md",
    ".dispatcher/CLAUDE.md",
    ".dispatcher/references/**/*.md",
    ".claude/skills/**/SKILL.md",
    ".claude/skills/**/references/**/*.md",
    "docs/contracts/**/*.md",
    "docs/journal-events.md",
)

LINK_RE = re.compile(r"\[`([^`\n]+)`\]\(([^)\s]+)\)")


def collect_in_scope() -> list[Path]:
    seen: set[Path] = set()
    for pattern in IN_SCOPE_GLOBS:
        for match in REPO_ROOT.glob(pattern):
            if match.is_file() and match.suffix == ".md":
                seen.add(match.resolve())
    return sorted(seen)


def is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:")) or target.startswith("#")


def looks_like_path(display: str) -> bool:
    return "/" in display or display.endswith(".md") or display.startswith("..")


def expected_display(resolved_abs: Path) -> str:
    rel = resolved_abs.relative_to(REPO_ROOT)
    return rel.as_posix()


def audit_file(md_path: Path) -> list[str]:
    violations: list[str] = []
    text = md_path.read_text(encoding="utf-8")
    file_rel = md_path.relative_to(REPO_ROOT).as_posix()
    for m in LINK_RE.finditer(text):
        display, target = m.group(1), m.group(2)
        if is_external(target):
            continue
        if not looks_like_path(display):
            continue
        # Strip in-doc anchor for filesystem resolution.
        target_path_part = target.split("#", 1)[0]
        if not target_path_part:
            continue
        resolved = (md_path.parent / target_path_part).resolve()
        try:
            resolved.relative_to(REPO_ROOT)
        except ValueError:
            violations.append(
                f"{file_rel}: target escapes repo root: [`{display}`]({target})"
            )
            continue
        if not resolved.exists():
            violations.append(
                f"{file_rel}: target does not exist: [`{display}`]({target}) -> {resolved}"
            )
            continue
        want = expected_display(resolved)
        if display != want:
            violations.append(
                f"{file_rel}: display mismatch: [`{display}`]({target}) "
                f"-> expected display `{want}`"
            )
    return violations


def main() -> int:
    files = collect_in_scope()
    all_violations: list[str] = []
    for md in files:
        all_violations.extend(audit_file(md))
    if all_violations:
        print(f"audit_link_paths: {len(all_violations)} violation(s) across {len(files)} file(s)")
        for v in all_violations:
            print(f"  - {v}")
        return 1
    print(f"audit_link_paths: OK ({len(files)} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
