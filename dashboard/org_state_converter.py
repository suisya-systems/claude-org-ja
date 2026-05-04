"""
org-state Markdown -> JSON converter
Reads .state/org-state.md and writes .state/org-state.json atomically.

Usage:
    py -3 dashboard/org_state_converter.py                   (Windows, default = auto)
    python3 dashboard/org_state_converter.py --source db
    python3 dashboard/org_state_converter.py --source markdown

Source of truth rule:
    org-state.md is canonical (human/AI-readable).
    org-state.json is derived (machine-readable for dashboard etc.).

M1 (Issue #267) adds an optional DB read path:
    --source markdown  : parse .state/org-state.md (default — non-lossy SoT).
    --source db        : query .state/state.db via tools.state_db.queries.
                         Falls back to a markdown overlay for fields the DB
                         does not yet model (Status / Objective / Updated /
                         Dispatcher / Curator / Resume Instructions).
    --source auto      : try DB first; fall back to markdown if DB is missing
                         or stale.

The default stays `markdown` because the DB only models *active* runs and
worker directories; emitting a JSON without completed/queued items or the
full Worker Directory Registry would be lossy for downstream readers
(dashboard JSON-first read, etc.). Use `--source db` explicitly when you
want a DB-derived snapshot.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
_MD_PATH = BASE_DIR / ".state" / "org-state.md"
_JSON_PATH = BASE_DIR / ".state" / "org-state.json"
_DB_PATH = BASE_DIR / ".state" / "state.db"

# Make `tools.state_db.*` importable when running this script directly.
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SCHEMA_VERSION = 1


def _read(path):
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except Exception:
        return ""


def parse_org_state_md(text):
    """
    Parse org-state.md and return a dict matching the org-state.json schema.

    Sections handled:
    - Status: <value>
    - Updated: <value>
    - Current Objective: <value>
    - Work items: "- task-id: title [STATUS]" with sub-items
    - ## Worker Directory Registry (Markdown table)
    - ## Dispatcher / ## Curator sections (Peer ID:, Pane ID: lines)
    - ## Resume Instructions (free text until next ## heading)
    """
    status = "IDLE"
    updated = None
    current_objective = None
    work_items = []
    worker_dir_registry = []
    dispatcher = None
    curator = None
    resume_instructions = None

    section = None
    resume_lines = []
    in_registry_table = False
    registry_header_seen = False
    current_role = None
    dispatcher_data = {}
    curator_data = {}

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # --- Detect ## headings (highest priority) ---
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if section == "resume instructions" and resume_lines:
                resume_instructions = "\n".join(resume_lines).strip() or None
                resume_lines = []
            section = heading
            in_registry_table = False
            registry_header_seen = False
            current_role = None
            if "dispatcher" in heading:
                current_role = "dispatcher"
            elif "curator" in heading:
                current_role = "curator"
            i += 1
            continue

        # --- Top-level key: value lines ---
        if line.startswith("Status:"):
            val = line[len("Status:"):].strip()
            if val:
                status = val.upper()
            i += 1
            continue

        if line.startswith("Updated:"):
            val = line[len("Updated:"):].strip()
            if val:
                updated = val
            i += 1
            continue

        if line.startswith("Current Objective:"):
            val = line[len("Current Objective:"):].strip()
            if val:
                current_objective = val
            i += 1
            continue

        # --- Dispatcher / Curator sections (check BEFORE work items to avoid "- Peer ID:" clash) ---
        if current_role in ("dispatcher", "curator"):
            m = re.match(r"^-?\s*Peer\s+ID:\s*(\S+)", line, re.IGNORECASE)
            if m:
                if current_role == "dispatcher":
                    dispatcher_data["peerId"] = m.group(1)
                else:
                    curator_data["peerId"] = m.group(1)
                i += 1
                continue
            m = re.match(r"^-?\s*Pane\s+ID:\s*(\S+)", line, re.IGNORECASE)
            if m:
                if current_role == "dispatcher":
                    dispatcher_data["paneId"] = m.group(1)
                else:
                    curator_data["paneId"] = m.group(1)
                i += 1
                continue

        # --- Worker Directory Registry table ---
        if section == "worker directory registry":
            stripped = line.strip()
            if stripped.startswith("|") and not registry_header_seen:
                cols = [c.strip() for c in stripped.strip("|").split("|")]
                if cols and cols[0].lower() in ("task id", "task_id"):
                    registry_header_seen = True
                    i += 1
                    continue
            if stripped.startswith("|---") or stripped.startswith("| ---"):
                in_registry_table = True
                i += 1
                continue
            if in_registry_table and stripped.startswith("|"):
                cols = [c.strip() for c in stripped.strip("|").split("|")]
                if len(cols) >= 5:
                    worker_dir_registry.append({
                        "taskId": cols[0],
                        "pattern": cols[1],
                        "directory": cols[2],
                        "project": cols[3],
                        "status": cols[4],
                    })
            i += 1
            continue

        # --- Resume Instructions section ---
        if section == "resume instructions":
            resume_lines.append(line)
            i += 1
            continue

        # --- Work items (top-level "- id: title [STATUS]") ---
        if line.startswith("- ") and section not in ("worker directory registry",):
            m = re.match(r"^- ([\w-]+):\s*(.+?)\s*\[(\w+)\]", line)
            if m:
                work_items.append({
                    "id": m.group(1),
                    "title": m.group(2).strip(),
                    "status": m.group(3).upper(),
                    "progress": None,
                    "worker": None,
                })
            i += 1
            continue

        # --- Sub-items of work items (indented) ---
        if line.startswith("  ") and work_items and section not in ("worker directory registry",):
            m = re.match(r"^\s+- 結果:\s*(.+)", line)
            if m:
                work_items[-1]["progress"] = m.group(1).strip()
            m = re.match(r"^\s+- ワーカー:\s*(\S+)", line)
            if m:
                work_items[-1]["worker"] = m.group(1).strip()
            i += 1
            continue

        i += 1

    # Finalize resume instructions
    if section == "resume instructions" and resume_lines:
        resume_instructions = "\n".join(resume_lines).strip() or None

    # Finalize dispatcher/curator
    if dispatcher_data:
        dispatcher = dispatcher_data
    if curator_data:
        curator = curator_data

    return {
        "version": SCHEMA_VERSION,
        "updated": updated,
        "status": status,
        "currentObjective": current_objective,
        "workItems": work_items,
        "workerDirectoryRegistry": worker_dir_registry,
        "dispatcher": dispatcher,
        "curator": curator,
        "resumeInstructions": resume_instructions,
    }


def parse_org_state_db(db_path, md_path=None):
    """Build the org-state.json shape from .state/state.db.

    The DB does not yet model Status / Current Objective / Updated /
    Dispatcher / Curator / Resume Instructions (M1 scope keeps markdown as
    SoT for those). To stay non-lossy, this function ALSO parses the markdown
    when present and merges the markdown-only fields into the output. The
    workItems / workerDirectoryRegistry come from the DB.
    """
    from tools.state_db import connect
    from tools.state_db.queries import get_org_state_summary

    conn = connect(db_path)
    try:
        summary = get_org_state_summary(conn)
        registry = []
        for r in summary["active_runs"]:
            if not r.get("worker_dir"):
                continue
            registry.append({
                "taskId": r["task_id"],
                "pattern": r["pattern"],
                "directory": r["worker_dir"],
                "project": r.get("project_slug") or "",
                "status": r["status"],
            })
    finally:
        conn.close()

    work_items = []
    for r in summary["active_runs"]:
        work_items.append({
            "id": r["task_id"],
            "title": r["title"] or r["task_id"],
            "status": (r["status"] or "").upper(),
            "progress": r.get("outcome_note"),
            "worker": r.get("worker_dir"),
        })

    # Merge markdown-only fields (Status, Objective, Updated, Dispatcher,
    # Curator, Resume Instructions). Without this, an `auto`/`db` CLI run
    # would overwrite .state/org-state.json with a lossy snapshot and the
    # dashboard's JSON-first reader would lose those fields.
    md_overlay = {}
    md_path = Path(md_path) if md_path else _MD_PATH
    if md_path.exists():
        try:
            md_overlay = parse_org_state_md(md_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[org_state_converter] markdown overlay failed: {exc}",
                  file=sys.stderr)
            md_overlay = {}

    def _pick(key, default=None):
        return md_overlay.get(key, default) if md_overlay else default

    return {
        "version": SCHEMA_VERSION,
        "updated": _pick("updated"),
        "status": _pick("status",
                        "ACTIVE" if summary["active_runs"] else "IDLE"),
        "currentObjective": _pick("currentObjective"),
        "workItems": work_items,
        "workerDirectoryRegistry": registry,
        "dispatcher": _pick("dispatcher"),
        "curator": _pick("curator"),
        "resumeInstructions": _pick("resumeInstructions"),
        "_source": "db",
    }


def _db_is_fresh(db_path, md_path):
    """True if db_path is at least as new as md_path. Missing files = not fresh."""
    db_path = Path(db_path)
    md_path = Path(md_path)
    if not db_path.exists():
        return False
    try:
        db_mtime = db_path.stat().st_mtime
    except OSError:
        return False
    try:
        md_mtime = md_path.stat().st_mtime
    except OSError:
        # No markdown to compare against → trust the DB.
        return True
    return db_mtime >= md_mtime


def convert(md_path=None, json_path=None, source="markdown", db_path=None):
    """Read state, build the org-state JSON dict, write it atomically.

    `source`:
      - 'markdown': always parse the markdown file.
      - 'db'      : always query the DB (raises if absent).
      - 'auto'    : DB if present and not stale, otherwise markdown.
    """
    if md_path is None:
        md_path = _MD_PATH
    if json_path is None:
        json_path = _JSON_PATH
    if db_path is None:
        db_path = _DB_PATH

    md_path = Path(md_path)
    json_path = Path(json_path)
    db_path = Path(db_path)

    chosen = source
    if chosen == "auto":
        chosen = "db" if _db_is_fresh(db_path, md_path) else "markdown"

    if chosen == "db":
        if not db_path.exists():
            print(f"[org_state_converter] state.db not found: {db_path}",
                  file=sys.stderr)
            return False
        data = parse_org_state_db(db_path, md_path=md_path)
    elif chosen == "markdown":
        if not md_path.exists():
            print(f"[org_state_converter] org-state.md not found: {md_path}",
                  file=sys.stderr)
            return False
        text = md_path.read_text(encoding="utf-8")
        data = parse_org_state_md(text)
    else:
        print(f"[org_state_converter] unknown --source {source}", file=sys.stderr)
        return False

    json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=json_path.parent, prefix=".org-state-", suffix=".json.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        Path(tmp_path).replace(json_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return True


def _main(argv=None):
    p = argparse.ArgumentParser(prog="org_state_converter")
    p.add_argument(
        "--source",
        choices=("auto", "db", "markdown"),
        default="markdown",
        help="Where to read org-state from. 'markdown' (default) preserves "
             "all fields including completed/queued items. 'db' uses the "
             "state DB and overlays markdown for fields the DB does not "
             "model. 'auto' picks DB if it is fresh, else markdown.",
    )
    p.add_argument("--md", default=str(_MD_PATH),
                   help="Path to org-state.md (markdown SoT)")
    p.add_argument("--json", default=str(_JSON_PATH),
                   help="Path to org-state.json (output)")
    p.add_argument("--db", default=str(_DB_PATH),
                   help="Path to state.db (DB source)")
    args = p.parse_args(argv)
    ok = convert(md_path=args.md, json_path=args.json,
                 source=args.source, db_path=args.db)
    if ok:
        print(f"[org_state_converter] Written: {args.json} (source={args.source})")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_main())
