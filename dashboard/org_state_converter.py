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

M2 (Issue #267) makes the DB the canonical source:
    --source db        : (default) query .state/state.db via
                         tools.state_db.queries. The org_sessions table
                         now carries Status / Objective / Updated /
                         Dispatcher / Curator / Resume Instructions —
                         no markdown overlay required.
    --source markdown  : parse .state/org-state.md instead. Kept for
                         debugging / pre-M2 DBs / disaster recovery.
    --source auto      : DB if state.db exists, otherwise markdown.
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

    M2 (Issue #267): all known fields come straight from the DB —
    workItems / Worker Directory Registry from runs + worker_dirs,
    Status / Updated / Suspended / Resumed / Current Objective /
    Dispatcher / Curator / Resume Instructions from org_sessions.
    The ``md_path`` parameter is accepted for backwards compatibility
    but no longer consulted (the M1 markdown overlay is gone).
    """
    del md_path  # M2: no markdown overlay; arg kept for caller compat.
    from tools.state_db import connect
    from tools.state_db.queries import (
        get_org_state_summary,
        list_runs_with_dirs,
    )

    conn = connect(db_path)
    try:
        summary = get_org_state_summary(conn)
        session = summary.get("session") or {}
        # WDR historically lists *every* run with a worker_dir
        # (active + completed + failed + abandoned), so use the full
        # listing rather than just the active runs from `summary`.
        registry = []
        for r in list_runs_with_dirs(conn):
            registry.append({
                "taskId": r["task_id"],
                "pattern": r["pattern"],
                "directory": r["worker_dir"],
                "project": r.get("project_slug") or "",
                "status": r.get("outcome_note") or r.get("status") or "",
            })
    finally:
        conn.close()

    # Frontend (dashboard/app.js) renders icons keyed off IN_PROGRESS /
    # REVIEW / PENDING / COMPLETED / BLOCKED / ABANDONED. Map the DB enum
    # so a `_source: db` JSON renders the same as the markdown path.
    _STATUS_MAP = {
        "in_use": "IN_PROGRESS",
        "review": "REVIEW",
        "queued": "PENDING",
        "completed": "COMPLETED",
        "failed": "BLOCKED",
        "suspended": "PENDING",
        "abandoned": "ABANDONED",
    }
    work_items = []
    for r in summary["active_runs"]:
        raw = (r["status"] or "").lower()
        work_items.append({
            "id": r["task_id"],
            "title": r["title"] or r["task_id"],
            "status": _STATUS_MAP.get(raw, raw.upper()),
            "progress": r.get("outcome_note"),
            "worker": r.get("worker_dir"),
        })

    dispatcher = None
    if session.get("dispatcher_pane_id") or session.get("dispatcher_peer_id"):
        dispatcher = {
            "peerId": session.get("dispatcher_peer_id"),
            "paneId": session.get("dispatcher_pane_id"),
        }
    curator = None
    if session.get("curator_pane_id") or session.get("curator_peer_id"):
        curator = {
            "peerId": session.get("curator_peer_id"),
            "paneId": session.get("curator_pane_id"),
        }

    status = (session.get("status")
              or ("ACTIVE" if summary["active_runs"] else "IDLE"))

    return {
        "version": SCHEMA_VERSION,
        "updated": session.get("updated_at"),
        "status": status,
        "currentObjective": session.get("objective"),
        "workItems": work_items,
        "workerDirectoryRegistry": registry,
        "dispatcher": dispatcher,
        "curator": curator,
        "resumeInstructions": session.get("resume_instructions"),
        "_source": "db",
    }


def _db_available(db_path, md_path=None):
    """True iff `.state/state.db` is on disk. M2: DB is the SoT, no
    markdown-vs-DB freshness comparison required."""
    del md_path  # kept for legacy call sites
    return Path(db_path).exists()


def convert(md_path=None, json_path=None, source="db", db_path=None):
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
        chosen = "db" if _db_available(db_path) else "markdown"

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
        default="db",
        help="Where to read org-state from. 'db' (default, M2 SoT) reads "
             ".state/state.db. 'markdown' parses the legacy file. 'auto' "
             "picks DB when the file is present, else markdown.",
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
