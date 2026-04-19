"""
org-state Markdown -> JSON converter
Reads .state/org-state.md and writes .state/org-state.json atomically.

Usage:
    py -3 dashboard/org_state_converter.py      (Windows)
    python3 dashboard/org_state_converter.py     (Mac/Linux)

Source of truth rule:
    org-state.md is canonical (human/AI-readable).
    org-state.json is derived (machine-readable for dashboard etc.).
    Re-run this script whenever org-state.md is updated manually.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
_MD_PATH = BASE_DIR / ".state" / "org-state.md"
_JSON_PATH = BASE_DIR / ".state" / "org-state.json"

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
    - ## Foreman / ## Curator sections (Peer ID:, Pane ID: lines)
    - ## Resume Instructions (free text until next ## heading)
    """
    status = "IDLE"
    updated = None
    current_objective = None
    work_items = []
    worker_dir_registry = []
    foreman = None
    curator = None
    resume_instructions = None

    section = None
    resume_lines = []
    in_registry_table = False
    registry_header_seen = False
    current_role = None
    foreman_data = {}
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
            if "foreman" in heading:
                current_role = "foreman"
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

        # --- Foreman / Curator sections (check BEFORE work items to avoid "- Peer ID:" clash) ---
        if current_role in ("foreman", "curator"):
            m = re.match(r"^-?\s*Peer\s+ID:\s*(\S+)", line, re.IGNORECASE)
            if m:
                if current_role == "foreman":
                    foreman_data["peerId"] = m.group(1)
                else:
                    curator_data["peerId"] = m.group(1)
                i += 1
                continue
            m = re.match(r"^-?\s*Pane\s+ID:\s*(\S+)", line, re.IGNORECASE)
            if m:
                if current_role == "foreman":
                    foreman_data["paneId"] = m.group(1)
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

    # Finalize foreman/curator
    if foreman_data:
        foreman = foreman_data
    if curator_data:
        curator = curator_data

    return {
        "version": SCHEMA_VERSION,
        "updated": updated,
        "status": status,
        "currentObjective": current_objective,
        "workItems": work_items,
        "workerDirectoryRegistry": worker_dir_registry,
        "foreman": foreman,
        "curator": curator,
        "resumeInstructions": resume_instructions,
    }


def convert(md_path=None, json_path=None):
    """Read org-state.md, parse it, and write org-state.json atomically."""
    if md_path is None:
        md_path = _MD_PATH
    if json_path is None:
        json_path = _JSON_PATH

    md_path = Path(md_path)
    json_path = Path(json_path)

    if not md_path.exists():
        print(f"[org_state_converter] org-state.md not found: {md_path}", file=sys.stderr)
        return False

    text = md_path.read_text(encoding="utf-8")
    data = parse_org_state_md(text)

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


if __name__ == "__main__":
    ok = convert()
    if ok:
        print(f"[org_state_converter] Written: {_JSON_PATH}")
        sys.exit(0)
    else:
        sys.exit(1)
