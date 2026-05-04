"""Free-text extractor for M4 markdown freeze (Issue #267).

M0–M3 demoted ``.state/org-state.md`` to a DB-derived dump but kept a
passthrough escape hatch in :mod:`tools.state_db.snapshotter` so the
hand-curated free-form sections in the live file (Pending Lead /
session 学び / 過去セッション成果) survived each regenerate. M4 retires
that escape hatch: every free-form ``## …`` block in the live file is
moved out to ``notes/`` so the snapshotter can render the whole file
deterministically from the DB.

CLI shape::

    python -m tools.state_db.extract_freetext \
        --org-state .state/org-state.md \
        --notes-dir notes/ \
        --plan      # dry-run, prints what would be written
    python -m tools.state_db.extract_freetext \
        --org-state .state/org-state.md \
        --notes-dir notes/ \
        --apply     # actually write notes/ and rewrite org-state.md

Behaviour:

* A free-form ``## …`` block is anything whose heading is **not** in
  :data:`tools.state_db.snapshotter._STRUCTURED_HEADINGS` (matched
  case-insensitively, exact-string after lower() — same predicate the
  snapshotter uses, so the two sides stay in lockstep).
* Each free-form block is routed to a target file under ``notes/``:

  * ``## YYYY-MM-DD …セッション #N…`` →
    ``notes/sessions/<YYYY-MM-DD>-session-<NN>.md``
  * ``## …学び`` (with optional date prefix) →
    ``notes/learnings/<YYYY-MM-DD>.md`` (date defaults to today's UTC
    date when the heading carries none)
  * ``## Pending Lead …`` → appended to ``notes/pending-leads.md``
  * Anything else → ``notes/misc/<slug>.md``

* Blocks with the same target file are concatenated in source order.
* The original ``.state/org-state.md`` is rewritten with the free-form
  blocks removed (plus a top-of-file ``<!-- See notes/ for moved
  free-text sections -->`` marker if at least one block moved).
* An extraction manifest is written to
  ``notes/.extraction-manifest.json`` recording each (heading,
  target_path, byte_length) so a future "restore" tool can reverse the
  operation, and so re-runs can short-circuit when nothing changed.
* Idempotent: running ``--apply`` twice in a row leaves both
  ``.state/org-state.md`` and ``notes/`` unchanged after the second
  call.
* ``--plan`` writes nothing — just prints the planned (heading →
  target) routing to stdout.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from .snapshotter import _is_structured_heading


_MANIFEST_FILENAME = ".extraction-manifest.json"
_PENDING_LEADS_FILENAME = "pending-leads.md"
_HEADER_MARKER = (
    "<!-- See notes/ for moved free-text sections "
    "(extracted by tools/state_db/extract_freetext.py). -->\n"
)


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------


def _split_blocks(markdown_text: str) -> "tuple[str, list[tuple[str, str]]]":
    """Return (preamble, blocks).

    `preamble` is everything before the first ``## …`` heading (kept
    verbatim so the rewrite round-trips the structured top-of-file
    block byte-identically). `blocks` is a list of ``(heading, body)``
    pairs where ``heading`` is the raw heading text after ``## ``
    (rstripped) and ``body`` is the section text **including** the
    heading line itself.
    """
    lines = markdown_text.splitlines(keepends=True)
    pre: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    cur_heading: Optional[str] = None
    cur_lines: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if cur_heading is not None:
                blocks.append((cur_heading, cur_lines))
            else:
                # Lines accumulated before the first ## belong to preamble.
                pre = cur_lines
            cur_heading = line[3:].rstrip("\n").strip()
            cur_lines = [line]
        else:
            cur_lines.append(line)
    if cur_heading is not None:
        blocks.append((cur_heading, cur_lines))
    else:
        pre = cur_lines
    return ("".join(pre), [(h, "".join(b)) for h, b in blocks])


# ---------------------------------------------------------------------------
# Slug / target-path routing
# ---------------------------------------------------------------------------


_DATE_RE = re.compile(r"(\d{4}-\d{1,2}-\d{1,2})")
_SESSION_RE = re.compile(r"セッション\s*#?\s*(\d+)|session\s*#?\s*(\d+)", re.IGNORECASE)


# Windows reserved device names (case-insensitive). A file called
# ``CON.md`` resolves to the console device on Windows and the open()
# call hangs or fails. Prefix the slug with ``_`` when the bare stem
# matches one of these to keep notes/ portable.
_WIN_RESERVED: frozenset[str] = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
})


def _slugify(text: str, *, fallback: str = "section") -> str:
    """ASCII-or-unicode-letter slug suitable for a filename.

    Drops characters that confuse Windows / POSIX filesystems
    (``<>:"/\\|?*`` plus control chars), collapses runs of whitespace
    and ``-`` / ``_`` into a single ``-``, and side-steps Windows
    reserved device names (``CON``, ``PRN``, ``AUX``, ``NUL``,
    ``COM1``-``COM9``, ``LPT1``-``LPT9``) by prefixing ``_``. Returns
    a lowercased result; empty inputs collapse to ``fallback``.
    """
    cleaned: list[str] = []
    for ch in text.strip():
        if ch in '<>:"/\\|?*':
            cleaned.append("-")
        elif ord(ch) < 0x20:
            cleaned.append("-")
        else:
            cleaned.append(ch)
    s = "".join(cleaned).lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-.")
    if not s:
        return fallback
    if s in _WIN_RESERVED:
        s = "_" + s
    return s


def _target_for_heading(heading: str, *, today_iso: str) -> Path:
    """Return the relative path under ``notes/`` for `heading`.

    `today_iso` is YYYY-MM-DD; used as the date when the heading
    carries no date prefix (only matters for 学び / sessions where we
    want a stable filename across runs).
    """
    h = heading.strip()
    h_low = h.lower()
    date_match = _DATE_RE.search(h)
    date = date_match.group(1) if date_match else today_iso

    sess_match = _SESSION_RE.search(h)
    if sess_match:
        num = sess_match.group(1) or sess_match.group(2) or "0"
        return Path("sessions") / f"{date}-session-{int(num):02d}.md"

    if "学び" in h or "lesson" in h_low or "lessons" in h_low:
        return Path("learnings") / f"{date}.md"

    if h_low.startswith("pending lead") or "pending lead" in h_low:
        return Path(_PENDING_LEADS_FILENAME)

    # Default: misc/<slug>.md. Strip any leading date so we don't
    # re-encode the date in the filename for ad-hoc sections.
    base = h
    if date_match:
        base = (h[:date_match.start()] + h[date_match.end():]).strip()
    slug = _slugify(base or h, fallback="section")
    if date_match:
        return Path("misc") / f"{date}-{slug}.md"
    return Path("misc") / f"{slug}.md"


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _read_manifest(notes_dir: Path) -> dict:
    p = notes_dir / _MANIFEST_FILENAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Plan / apply
# ---------------------------------------------------------------------------


def _normalize_block(body: str) -> str:
    """Strip trailing blank lines but keep the heading and one EOL.

    The snapshotter emits each section followed by exactly one blank
    line; matching that here keeps round-trip diffs clean when blocks
    are concatenated under the same target file.
    """
    body = body.rstrip("\n")
    return body + "\n"


def plan_extraction(
    org_state_text: str, *, today_iso: Optional[str] = None,
) -> "list[dict]":
    """Compute the extraction plan without touching disk.

    Returns a list of plan rows: ``{heading, target, body, structured}``.
    ``structured=True`` blocks are kept in org-state.md (passthrough
    rewrite drops only ``structured=False`` blocks).
    """
    if today_iso is None:
        today_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    _, blocks = _split_blocks(org_state_text)
    plan: list[dict] = []
    for heading, body in blocks:
        if _is_structured_heading(heading):
            plan.append({
                "heading": heading,
                "target": None,
                "body": body,
                "structured": True,
            })
            continue
        target = _target_for_heading(heading, today_iso=today_iso)
        plan.append({
            "heading": heading,
            "target": str(target).replace("\\", "/"),
            "body": body,
            "structured": False,
        })
    return plan


def apply_extraction(
    org_state_path: Path,
    notes_dir: Path,
    *,
    today_iso: Optional[str] = None,
) -> dict:
    """Write notes/ files and rewrite ``org_state_path``.

    Returns a summary dict: ``{moved: int, files: list[str], unchanged: bool}``.

    Safety: the operation is split into "write notes/" then "rewrite
    org-state.md", which means a process crash between the two leaves
    blocks both in the notes file *and* still in the org-state dump. To
    keep the second run idempotent in that case we consult the
    extraction manifest before appending: any (heading, target) pair
    already present in the manifest is treated as already-extracted
    and skipped on the notes/ side. The org-state rewrite still strips
    those headings on the second run, so the partial-failure recovery
    path converges on the same end state without duplicating bytes
    inside the notes/ file (Codex M-r1-3).
    """
    org_state_path = Path(org_state_path)
    notes_dir = Path(notes_dir)
    if not org_state_path.exists():
        raise FileNotFoundError(f"org-state.md not found: {org_state_path}")
    text = org_state_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    plan = plan_extraction(text, today_iso=today_iso)
    free = [row for row in plan if not row["structured"]]
    if not free:
        # Idempotency: nothing to do. Don't churn the manifest mtime.
        return {"moved": 0, "files": [], "unchanged": True}

    prior_manifest = _read_manifest(notes_dir)
    prior_pairs: set[tuple[str, str]] = {
        (e.get("heading", ""), e.get("target", ""))
        for e in (prior_manifest.get("entries") or [])
    }

    # Group by target file in source order.
    grouped: "dict[str, list[dict]]" = {}
    order: list[str] = []
    for row in free:
        key = row["target"]
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)

    written: list[str] = []
    manifest_entries: list[dict] = list(prior_manifest.get("entries") or [])
    for target in order:
        target_path = notes_dir / target
        existing = ""
        if target_path.exists():
            existing = target_path.read_text(encoding="utf-8")
        # Filter out blocks the manifest already records for this target —
        # those bytes are already in the file from a prior partial run.
        new_blocks = [r for r in grouped[target]
                      if (r["heading"], target) not in prior_pairs]
        if not new_blocks:
            written.append(target)
            continue
        body_parts = [_normalize_block(r["body"]) for r in new_blocks]
        appended = "\n".join(body_parts).rstrip("\n") + "\n"
        if existing:
            new_body = existing.rstrip("\n") + "\n\n" + appended
        else:
            new_body = appended
        if new_body != existing:
            _atomic_write(target_path, new_body)
        written.append(target)
        for r in new_blocks:
            manifest_entries.append({
                "heading": r["heading"],
                "target": target,
                "byte_length": len(r["body"].encode("utf-8")),
            })

    # Rewrite org-state.md without the free-form blocks. Preamble +
    # structured blocks only, in source order.
    pre, blocks = _split_blocks(text)
    keep: list[str] = [pre] if pre.strip() else [pre]
    for heading, body in blocks:
        if _is_structured_heading(heading):
            keep.append(body)
    new_md = "".join(keep)
    if not new_md.lstrip().startswith(_HEADER_MARKER.strip()):
        # Insert marker right after the first blank line of the preamble
        # so the dump's structure (# Org State \n\n Status: …) stays
        # intact. If preamble is empty, prepend.
        lines = new_md.splitlines(keepends=True)
        inserted = False
        for i, line in enumerate(lines):
            if line.strip() == "":
                lines.insert(i + 1, _HEADER_MARKER)
                inserted = True
                break
        if not inserted:
            lines.insert(0, _HEADER_MARKER)
        new_md = "".join(lines)
    _atomic_write(org_state_path, new_md)

    manifest = {
        "schema": 1,
        "generated_by": "tools.state_db.extract_freetext",
        "entries": manifest_entries,
    }
    _atomic_write(
        notes_dir / _MANIFEST_FILENAME,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )

    moved_this_run = len(manifest_entries) - len(prior_manifest.get("entries") or [])
    return {
        "moved": moved_this_run,
        "files": written,
        "unchanged": False,
    }


def format_plan(plan: Iterable[dict]) -> str:
    out: list[str] = []
    for row in plan:
        if row["structured"]:
            out.append(f"  KEEP   ## {row['heading']}")
        else:
            out.append(f"  MOVE   ## {row['heading']}  →  notes/{row['target']}")
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tools.state_db.extract_freetext",
        description=(
            "Move free-form ## … sections out of .state/org-state.md "
            "into notes/ (M4 markdown freeze, Issue #267)."
        ),
    )
    p.add_argument("--org-state", required=True, type=Path,
                   help="Path to .state/org-state.md")
    p.add_argument("--notes-dir", required=True, type=Path,
                   help="Path to notes/ output directory")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true",
                      help="Dry-run: print the planned routing only")
    mode.add_argument("--apply", action="store_true",
                      help="Write notes/ files and rewrite org-state.md")
    p.add_argument("--today", default=None,
                   help="Override today's UTC date (YYYY-MM-DD); used as the "
                        "default date prefix for headings without one")
    args = p.parse_args(argv)

    if not args.org_state.exists():
        print(f"error: {args.org_state} does not exist", file=sys.stderr)
        return 2
    text = args.org_state.read_text(encoding="utf-8").replace("\r\n", "\n")
    if args.plan:
        plan = plan_extraction(text, today_iso=args.today)
        sys.stdout.write(format_plan(plan))
        return 0
    summary = apply_extraction(args.org_state, args.notes_dir,
                                today_iso=args.today)
    if summary["unchanged"]:
        print("extract_freetext: no free-form sections to move (no-op)")
    else:
        print(f"extract_freetext: moved {summary['moved']} block(s) "
              f"into {len(summary['files'])} notes file(s)")
        for f in summary["files"]:
            print(f"  - notes/{f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
