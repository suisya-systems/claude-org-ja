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
    "docs/contributing/**/*.md",
    "docs/journal-events.md",
)

# Match `[`DISPLAY`](TARGET)` where TARGET is either a bare destination (no
# whitespace, no parens) or an angle-bracket destination `<...>` allowing
# spaces -- both forms are valid CommonMark.
LINK_RE = re.compile(
    r"\[`([^`\n]+)`\]\("
    r"(?:<([^>\n]+)>|([^)\s]+))"
    r"\)"
)


def collect_in_scope() -> list[Path]:
    seen: set[Path] = set()
    for pattern in IN_SCOPE_GLOBS:
        for match in REPO_ROOT.glob(pattern):
            if match.is_file() and match.suffix == ".md":
                seen.add(match.resolve())
    return sorted(seen)


def is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:")) or target.startswith("#")


def expected_display(resolved_abs: Path) -> str:
    rel = resolved_abs.relative_to(REPO_ROOT)
    return rel.as_posix()


def strip_fenced_code(text: str) -> str:
    """Replace fenced code blocks with blank lines so example links inside
    ```` ```markdown ```` fences are not audited as live links."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append("\n")
            continue
        out.append("\n" if in_fence else line)
    return "".join(out)


def audit_file(md_path: Path) -> list[str]:
    violations: list[str] = []
    text = strip_fenced_code(md_path.read_text(encoding="utf-8"))
    file_rel = md_path.relative_to(REPO_ROOT).as_posix()
    for m in LINK_RE.finditer(text):
        # Skip matches wrapped in an inline code span -- e.g. the literal
        # example `[`...`](...)` shown as documentation, not a live link.
        if m.start() > 0 and text[m.start() - 1] == "`":
            continue
        display = m.group(1)
        target = m.group(2) or m.group(3)
        if is_external(target):
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
