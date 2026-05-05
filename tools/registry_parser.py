"""Shared parser for ``registry/projects.md``.

Single source of truth for the markdown-table parsing previously
duplicated across:

- ``dashboard/server.py:_parse_projects``
- ``tools/state_db/importer.py``
- ``tools/resolve_worker_layout.py`` (Issue #283 minimal parser)

The parser handles BOM, CRLF, missing trailing newline, leading prose,
and gracefully skips malformed rows (logs a warning instead of raising).

Two entry points:

- :func:`parse_projects` — convenience API returning only the successfully
  parsed :class:`Project` rows. Use this from regular consumers.
- :func:`iter_rows` — per-line classification for callers (importer) that
  need to record unparsed rows in their own bookkeeping.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Union

logger = logging.getLogger(__name__)

# The projects table is a fixed-width 5-column shape.
COLUMN_COUNT = 5

# Header rows (above the |---| separator) sometimes literally contain these
# words in the second column. Catch them when the separator is missing or
# malformed so we never mis-emit the table header as a Project row.
_HEADER_KEYWORDS = frozenset({"プロジェクト名", "name", "project"})

_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|\s*$")


@dataclass(frozen=True)
class Project:
    """One row of ``registry/projects.md``."""

    nickname: str       # 通称
    name: str           # プロジェクト名 (slug)
    path: str           # URL, local path, or '-'
    description: str
    common_tasks: str   # 「よくある作業例」 raw column value (may be empty)


# Per-line classification. ``kind`` values:
#   'data'      — a successfully parsed Project row (``project`` populated)
#   'separator' — Markdown table separator (``|---|---|...|``)
#   'header'    — Markdown table header row (above the separator)
#   'mismatch'  — line starts with '|' but doesn't conform to the schema
#   'non_table' — line outside any table (prose, blank, etc.)
@dataclass(frozen=True)
class ParsedRow:
    line_no: int
    raw: str
    kind: str
    project: Optional[Project] = None


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def iter_rows(text: str) -> Iterator[ParsedRow]:
    """Yield a :class:`ParsedRow` for every line in ``text``.

    Lines outside any table are emitted as ``kind='non_table'`` so callers
    can iterate the file once and drive their own bookkeeping.
    """
    text = text.lstrip("﻿")
    in_table = False
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.rstrip()
        if not stripped.startswith("|"):
            in_table = False
            yield ParsedRow(line_no, raw_line, "non_table")
            continue
        if _SEPARATOR_RE.match(stripped):
            in_table = True
            yield ParsedRow(line_no, raw_line, "separator")
            continue
        cells = _split_cells(stripped)
        is_header_keyword = (
            len(cells) >= 2 and cells[1].lower() in _HEADER_KEYWORDS
        )
        if not in_table or is_header_keyword:
            # Header row (above the separator, or a stray repeat header).
            yield ParsedRow(line_no, raw_line, "header")
            continue
        # Schema check: exactly 5 cells, and the machine slug column must be
        # populated. Other columns are allowed to be empty so authors can omit
        # description / common_tasks for early-stage entries (matches the
        # legacy resolver's tolerant parse).
        if len(cells) != COLUMN_COUNT or not cells[1]:
            yield ParsedRow(line_no, raw_line, "mismatch")
            continue
        project = Project(
            nickname=cells[0],
            name=cells[1],
            path=cells[2],
            description=cells[3],
            common_tasks=cells[4],
        )
        yield ParsedRow(line_no, raw_line, "data", project)


def parse_projects(source: Union[str, Path]) -> list[Project]:
    """Return successfully parsed :class:`Project` rows from ``source``.

    ``source`` may be either the raw markdown text or a path-like object
    pointing at ``registry/projects.md``. Malformed rows are skipped with a
    warning log; this function never raises on parsing errors.
    """
    if isinstance(source, (str, bytes)):
        text = source.decode("utf-8") if isinstance(source, bytes) else source
    else:
        text = Path(source).read_text(encoding="utf-8")
    out: list[Project] = []
    for row in iter_rows(text):
        if row.kind == "data" and row.project is not None:
            out.append(row.project)
        elif row.kind == "mismatch":
            logger.warning(
                "registry_parser: skipping malformed row at line %d: %r",
                row.line_no, row.raw,
            )
    return out
