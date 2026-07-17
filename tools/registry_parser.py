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

# The projects table grew from 5 columns (legacy) to 6 columns (Issue #374
# `mirror_of`) and, in header-mode registries (Issue #729), now carries a
# trailing `triage` opt-in column too. These positional constants are only
# consulted by the *positional fallback* path (headerless snippets); when a
# recognised header row is present the parser maps columns by name instead,
# so column order and extra columns no longer matter. We still parse 4-column
# hand-edited tables ('legacy' resolver leniency) and ignore extra trailing
# columns rather than rejecting the whole row, so a future addition doesn't
# silently drop registered projects.
#   positional layout: [0]=nickname [1]=name [2]=path [3]=description
#                       [4]=common_tasks [5]=mirror_of
# The live registry/projects.md places `triage` as its 6th visible column
# (there is no mirror_of column in the ja registry); positional mode never
# populates `triage` — only header mode does.
COLUMN_COUNT = 6
LEGACY_COLUMN_COUNT = 5

# Header rows (above the |---| separator) sometimes literally contain these
# words in the second column. Catch them when the separator is missing or
# malformed so we never mis-emit the table header as a Project row.
_HEADER_KEYWORDS = frozenset({"プロジェクト名", "name", "project"})

# Header-name -> canonical field aliases (Issue #729). Case-insensitive and
# trimmed. First matching column wins for each canonical field. A header row
# that matches at least one alias switches iter_rows into *header mode* (map
# columns by name); a header row that matches none falls back to positional
# parsing so headerless snippets and legacy fork tables keep working.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "nickname": ("通称",),
    "name": ("プロジェクト名", "name", "project"),
    "path": ("パス", "path"),
    "description": ("説明", "description"),
    "common_tasks": ("よくある作業例",),
    "mirror_of": ("mirror_of",),
    "triage": ("triage",),
}
_ALIAS_TO_FIELD: dict[str, str] = {
    alias.lower(): field
    for field, aliases in _FIELD_ALIASES.items()
    for alias in aliases
}

_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|\s*$")


@dataclass(frozen=True)
class Project:
    """One row of ``registry/projects.md``."""

    nickname: str       # 通称
    name: str           # プロジェクト名 (slug)
    path: str           # URL, local path, or '-'
    description: str
    common_tasks: str = ""   # 「よくある作業例」 raw column value (may be empty)
    # Issue #374: when non-empty, this row is a mirror of <slug>. Resolver
    # uses the presence of this signal — not just an active concurrent run —
    # to choose Pattern B from the first dispatch (per-task back-port style
    # workflow, distinct from accumulating self-edit work). 5-column legacy
    # tables (and fork registries that haven't adopted the column yet) leave
    # this empty and the resolver falls through to the conventional A/B/C
    # decision tree.
    mirror_of: str = ""
    # Issue #729: raw value of the `triage` opt-in column (header mode only;
    # positional fallback always leaves this empty). Kept verbatim — the
    # opt-in interpretation (`yes`/`true`/`on` after case-fold + trim) lives
    # in the work-discovery repo resolver, not here, so the parser stays a
    # dumb SoT and forks/tests can assert on the literal cell value.
    triage: str = ""


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


def _build_header_map(cells: list[str]) -> dict[str, int]:
    """Map canonical field name -> column index from a header row's cells.

    Case-insensitive, trimmed; first matching column wins per field. Returns
    an empty dict when the row matches no known header alias — the caller
    treats that as "not a real header" and stays in positional mode.
    """
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        field = _ALIAS_TO_FIELD.get(cell.strip().lower())
        if field is not None and field not in mapping:
            mapping[field] = idx
    return mapping


def _cell_by_field(
    cells: list[str], header_map: dict[str, int], field: str
) -> str:
    """Fetch a field's cell in header mode; '' when the column is absent or
    the data row is short of that index."""
    idx = header_map.get(field)
    if idx is None or idx >= len(cells):
        return ""
    return cells[idx]


def iter_rows(text: str) -> Iterator[ParsedRow]:
    """Yield a :class:`ParsedRow` for every line in ``text``.

    Lines outside any table are emitted as ``kind='non_table'`` so callers
    can iterate the file once and drive their own bookkeeping.

    Column resolution has two modes (Issue #729):

    - **header mode** — when the header row above the separator matches at
      least one known header alias, data-row cells are mapped by column
      *name*, so reordering columns or inserting new ones (e.g. `triage`)
      does not shift the meaning of existing columns.
    - **positional fallback** — headerless snippets and legacy/fork tables
      whose header matches no alias fall back to fixed positions
      (``cells[0]=nickname … cells[5]=mirror_of``). ``triage`` is never
      populated in this mode.
    """
    text = text.lstrip("﻿")
    in_table = False
    # None => positional fallback; a dict => header mode with that column map.
    header_map: Optional[dict[str, int]] = None
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.rstrip()
        if not stripped.startswith("|"):
            in_table = False
            header_map = None  # a new table may follow with its own header
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
            # Enter header mode only when the header unambiguously provides
            # BOTH identity columns (nickname + slug). A partial/ambiguous
            # header — e.g. a legacy English `| Name | Project | ... |` whose
            # `Name` alias would otherwise grab the slug field from column 0,
            # dropping the real nickname column — falls back to positional
            # parsing, which preserved the correct column meaning before the
            # Issue #729 header-mode addition.
            built = _build_header_map(cells)
            header_map = (
                built if ("nickname" in built and "name" in built) else None
            )
            yield ParsedRow(line_no, raw_line, "header")
            continue
        # Schema check: at least 4 cells (通称/slug/パス/説明) and the slug
        # column populated. The 5th 「よくある作業例」, 6th `mirror_of`, and
        # `triage` columns are optional — the legacy resolver accepted
        # 4-column tables, the pre-Issue-#374 registry was 5-column, and a
        # fork that hasn't adopted a new column yet must keep parsing. Extra
        # trailing cells are ignored rather than treated as schema breakage
        # so the next column addition doesn't silently drop every row.
        if header_map is not None:
            name = _cell_by_field(cells, header_map, "name")
            if len(cells) < 4 or not name:
                yield ParsedRow(line_no, raw_line, "mismatch")
                continue
            project = Project(
                nickname=_cell_by_field(cells, header_map, "nickname"),
                name=name,
                path=_cell_by_field(cells, header_map, "path"),
                description=_cell_by_field(cells, header_map, "description"),
                common_tasks=_cell_by_field(cells, header_map, "common_tasks"),
                mirror_of=_cell_by_field(cells, header_map, "mirror_of"),
                triage=_cell_by_field(cells, header_map, "triage"),
            )
            yield ParsedRow(line_no, raw_line, "data", project)
            continue
        if len(cells) < 4 or not cells[1]:
            yield ParsedRow(line_no, raw_line, "mismatch")
            continue
        project = Project(
            nickname=cells[0],
            name=cells[1],
            path=cells[2],
            description=cells[3],
            common_tasks=cells[4] if len(cells) >= LEGACY_COLUMN_COUNT else "",
            mirror_of=cells[5] if len(cells) >= COLUMN_COUNT else "",
        )
        yield ParsedRow(line_no, raw_line, "data", project)


def parse_projects(path: Union[str, Path]) -> list[Project]:
    """Return successfully parsed :class:`Project` rows from a file path.

    ``path`` is always treated as a filesystem path (``str | Path``).
    Callers that already have the markdown text in memory should use
    :func:`parse_projects_text` instead.
    """
    return parse_projects_text(Path(path).read_text(encoding="utf-8"))


def parse_projects_text(text: str) -> list[Project]:
    """Parse already-loaded markdown text. Malformed rows are skipped with
    a warning; this function never raises on parsing errors."""
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
