#!/usr/bin/env python3
"""Print how the secretary stops before a PR merge (Issue #1167).

Reads ``merge_approval_mode:`` (column 0) from ``registry/org-config.md`` and
prints ``human`` or ``acceptance`` as one line:

- human: after CI green the secretary asks the human before merging.
- acceptance (the value set in this repo's org-config.md): the secretary
  merges without asking when the acceptance gate and CI pass and no stop line
  is hit (the skills list the stop lines).

A missing file, a missing key or an invalid value prints ``human``: a config
that cannot be read must not skip the human. An invalid value also writes one
warning to stderr. Exit code is always 0.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MODES = ("human", "acceptance")
_LINE = re.compile(r"^merge_approval_mode[ \t]*:(.*)$", re.MULTILINE)


def read_mode(root: Path) -> tuple[str, str | None]:
    """(mode, warning or None)."""
    try:
        text = (root / "registry" / "org-config.md").read_text(encoding="utf-8")
    except OSError:
        return "human", None
    m = _LINE.search(text)
    if not m:
        return "human", None
    value = m.group(1).strip().lower()
    if value in MODES:
        return value, None
    return "human", f"merge_approval_mode: invalid value {m.group(1).strip()!r}, using human"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/merge_approval_mode.py",
                                 description="Print the merge approval mode (human or acceptance).")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                    help="claude-org root (default: this repo)")
    mode, warning = read_mode(Path(ap.parse_args(argv).root))
    if warning:
        print(warning, file=sys.stderr)
    print(mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
