"""Generate / verify the operator-local ``registry/projects.md`` (Issue #811).

``registry/projects.md`` used to be a tracked file, which meant every
operator's project roster -- customer names, private repo URLs -- was one
implicit ``git add`` away from entering public history. It is now
operator-local (gitignored) and generated from the tracked template
``registry/projects.example.md``.

This module owns two jobs:

1. **Generation** -- create ``registry/projects.md`` from the template when
   it is absent. An existing file is NEVER overwritten: it holds the
   operator's live roster and losing it is the failure mode this whole
   migration exists to prevent.

2. **Schema-evolution warning** -- because (1) never overwrites, a template
   whose column schema grew (e.g. Issue #808 added ``base_branch``) would
   otherwise never reach operators who already have a local file. So on
   every run we compare the local table header against the template's and
   warn (non-fatal) when the local one is missing columns. ``/org-start``
   calls this on each startup, so a schema change surfaces the next time
   the operator boots the org rather than silently never.

Why a tool instead of the plain ``cp`` used for ``.state/attention.json``:
``cp`` can do (1) but not (2), and (2) is the part that keeps a
generated-file design from rotting.

Exit codes (the only branch key for callers):
  0  ok / created / header drift (drift is a warning by default)
  1  error (template missing, unreadable, or malformed)
  3  header drift, and --strict was passed
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# The template's live-registry body starts after this marker line. Everything
# above it is template-only prose (how to edit the schema, what the file is)
# and must not leak into the generated file.
LIVE_MARKER = "<!-- BEGIN-LIVE-REGISTRY -->"

TEMPLATE_RELPATH = Path("registry") / "projects.example.md"
LIVE_RELPATH = Path("registry") / "projects.md"

# Status values reported to callers / --json consumers.
STATUS_CREATED = "created"
STATUS_OK = "ok"
STATUS_HEADER_DRIFT = "header_drift"
STATUS_MISSING = "missing"


class EnsureError(Exception):
    """Fatal condition: the template is absent or malformed."""


@dataclass
class EnsureResult:
    status: str
    live_path: Path
    template_path: Path
    # Columns present in the template header but absent from the local file.
    missing_columns: list[str] = field(default_factory=list)
    # Columns present locally but not in the template (operator additions or
    # a template that dropped a column). Reported, never acted on.
    extra_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "live_path": str(self.live_path),
            "template_path": str(self.template_path),
            "missing_columns": self.missing_columns,
            "extra_columns": self.extra_columns,
        }


def extract_live_body(template_text: str) -> str:
    """Return the part of the template that becomes ``registry/projects.md``.

    Raises :class:`EnsureError` when the marker is absent. Copying the whole
    template as a fallback would embed template-only prose ("this is a
    template, edit the other file") into the live registry, so we fail loud
    instead.
    """
    lines = template_text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if line.strip() == LIVE_MARKER:
            body = "".join(lines[idx + 1:]).lstrip("\n")
            if not body.strip():
                raise EnsureError(
                    "template marker found but the body after it is empty: "
                    + LIVE_MARKER
                )
            return body
    raise EnsureError(
        "template is missing its live-body marker line " + LIVE_MARKER
    )


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def extract_header_columns(text: str) -> Optional[list[str]]:
    """Return the column labels of the first markdown table in ``text``.

    A table header is the ``| a | b |`` line immediately preceding a
    ``|---|---|`` separator. Returns ``None`` when no table is found -- an
    empty or table-less registry is a legitimate state (fresh fork), so
    callers treat it as "nothing to compare" rather than an error.
    """
    lines = [ln.rstrip() for ln in text.lstrip("﻿").splitlines()]
    for idx, line in enumerate(lines[:-1] if lines else []):
        if not line.lstrip().startswith("|"):
            continue
        nxt = lines[idx + 1].strip()
        if not nxt.startswith("|"):
            continue
        # Separator: only pipes, dashes, colons and whitespace.
        body = nxt.strip("|")
        if body and all(ch in "-: |" for ch in body) and "-" in body:
            return _split_cells(line)
    return None


def compare_headers(
    template_body: str, live_text: str
) -> tuple[list[str], list[str]]:
    """Return ``(missing_columns, extra_columns)`` for the local file.

    ``template_body`` must be the marked live body (the output of
    :func:`extract_live_body`), NOT the whole template file: the
    template-only prose above the marker may itself contain a markdown table
    (a column summary, say), and comparing against that would both invent
    drift and hide real schema additions.

    Comparison is case-insensitive on trimmed labels but reports the
    template's original spelling, so the warning tells the operator exactly
    what to paste in. Order is not compared: the parser maps columns by name
    (``tools/registry_parser.py`` header mode), so a reordered local header
    is not drift.
    """
    tmpl_cols = extract_header_columns(template_body)
    live_cols = extract_header_columns(live_text)
    if tmpl_cols is None:
        return [], []
    if live_cols is None:
        # The local file has no table at all -- a truncated write, a botched
        # hand-edit, or a stray restore. Reporting "ok" here would leave every
        # reader parsing zero projects with nothing said, so surface it as
        # drift on all columns; the warning already tells the operator to add
        # the header and separator rows.
        return list(tmpl_cols), []
    live_lower = {c.lower() for c in live_cols}
    tmpl_lower = {c.lower() for c in tmpl_cols}
    missing = [c for c in tmpl_cols if c.lower() not in live_lower]
    extra = [c for c in live_cols if c.lower() not in tmpl_lower]
    return missing, extra


def ensure_projects_registry(
    root: Path, *, check_only: bool = False
) -> EnsureResult:
    """Create ``registry/projects.md`` if absent; report header drift.

    ``check_only`` inspects without writing (used by tests and by anyone who
    wants to know the state without side effects).
    """
    root = Path(root)
    template_path = root / TEMPLATE_RELPATH
    live_path = root / LIVE_RELPATH

    if not template_path.is_file():
        raise EnsureError("template not found: " + str(template_path))
    template_text = template_path.read_text(encoding="utf-8")
    # Validate the marker even on the create path we may not take, so a
    # malformed template is reported the first time anyone runs this rather
    # than only on a fresh checkout.
    body = extract_live_body(template_text)
    # A body without a table header would generate a registry that every
    # reader parses as zero projects -- broken, but silently so. Fail loud
    # instead of shipping an unusable file and reporting "created".
    if extract_header_columns(body) is None:
        raise EnsureError(
            "template body after " + LIVE_MARKER
            + " has no markdown table header: " + str(template_path)
        )

    if not live_path.is_file():
        if check_only:
            return EnsureResult(STATUS_MISSING, live_path, template_path)
        live_path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create (O_EXCL), not write_text(): the is_file() check
        # above and the write are not atomic together, and /org-start and
        # /org-setup both call this. A truncating open would let the loser of
        # that race overwrite rows the winner already has -- exactly the
        # never-overwrite guarantee this tool exists to hold. Losing the race
        # is not an error: fall through and treat the file as pre-existing.
        try:
            with live_path.open("x", encoding="utf-8") as fh:
                fh.write(body)
        except FileExistsError:
            pass  # lost the race; fall through to the existing-file path
        except BaseException:
            # A failed or interrupted write (disk full, SIGINT) would leave a
            # partial file that the never-overwrite rule then preserves
            # forever. Remove our own partial file before re-raising --
            # nothing else can have written it, since we created it
            # exclusively a moment ago.
            live_path.unlink(missing_ok=True)
            raise
        else:
            return EnsureResult(STATUS_CREATED, live_path, template_path)

    live_text = live_path.read_text(encoding="utf-8")
    missing, extra = compare_headers(body, live_text)
    status = STATUS_HEADER_DRIFT if missing else STATUS_OK
    return EnsureResult(status, live_path, template_path, missing, extra)


def _render_human(result: EnsureResult) -> str:
    lines: list[str] = []
    if result.status == STATUS_CREATED:
        lines.append("created: " + str(result.live_path))
        lines.append(
            "  generated from " + str(result.template_path)
            + " (operator-local, gitignored)"
        )
    elif result.status == STATUS_MISSING:
        lines.append("missing: " + str(result.live_path))
        lines.append(
            "  run without --check to generate it from "
            + str(result.template_path)
        )
    elif result.status == STATUS_HEADER_DRIFT:
        lines.append("header drift: " + str(result.live_path))
        lines.append(
            "  the template declares columns your local registry lacks: "
            + ", ".join(result.missing_columns)
        )
        lines.append(
            "  add them to the table header (and separator row) of "
            + str(result.live_path) + "."
        )
        lines.append(
            "  Existing rows may leave the new cells empty - an empty cell "
            "keeps the previous behaviour."
        )
        lines.append("  Column semantics: " + str(result.template_path))
    else:
        lines.append("ok: " + str(result.live_path))
    if result.extra_columns:
        lines.append(
            "  note: local-only columns (not in the template): "
            + ", ".join(result.extra_columns)
        )
    return "\n".join(lines)


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 tools/ensure_projects_registry.py",
        description=(
            "Generate registry/projects.md from registry/projects.example.md "
            "when absent, and warn when the local table header is missing "
            "columns the template declares. Never overwrites an existing "
            "registry."
        ),
    )
    p.add_argument(
        "--root", type=Path, default=Path.cwd(),
        help="claude-org repo root (defaults to CWD).",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Report state without creating anything.",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="Exit 3 on header drift instead of warning (exit 0).",
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit the result as JSON on stdout.",
    )
    args = p.parse_args(argv)

    try:
        result = ensure_projects_registry(args.root, check_only=args.check)
    except EnsureError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_human(result))

    if result.status == STATUS_HEADER_DRIFT and args.strict:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
