"""
org-state DB -> JSON converter (M4, Issue #267).

Reads .state/state.db and writes .state/org-state.json atomically.

Usage:
    py -3 dashboard/org_state_converter.py        (Windows)
    python3 dashboard/org_state_converter.py     (POSIX)

M4 (Issue #267): the DB is the only source. The pre-M4 ``--source
markdown`` mode has been removed; ``.state/org-state.md`` is now a
generator-owned dump and parsing it as input would re-introduce the
divergence the freeze was meant to retire.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
_JSON_PATH = BASE_DIR / ".state" / "org-state.json"
_DB_PATH = BASE_DIR / ".state" / "state.db"

# Make `tools.state_db.*` importable when running this script directly.
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SCHEMA_VERSION = 1


def parse_org_state_db(db_path):
    """Build the org-state.json shape from .state/state.db.

    All known fields come straight from the DB — workItems / Worker
    Directory Registry from runs + worker_dirs, Status / Updated /
    Suspended / Resumed / Current Objective / Dispatcher / Curator /
    Resume Instructions from org_sessions.
    """
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
    # so the JSON renders the same labels the markdown path used to.
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
            "progress": None,
            "worker": None,
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


def convert(json_path=None, db_path=None):
    """Read .state/state.db, build the org-state JSON dict, write it atomically."""
    if json_path is None:
        json_path = _JSON_PATH
    if db_path is None:
        db_path = _DB_PATH

    json_path = Path(json_path)
    db_path = Path(db_path)

    if not db_path.exists():
        print(f"[org_state_converter] state.db not found: {db_path}",
              file=sys.stderr)
        return False

    data = parse_org_state_db(db_path)

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
    p.add_argument("--json", default=str(_JSON_PATH),
                   help="Path to org-state.json (output)")
    p.add_argument("--db", default=str(_DB_PATH),
                   help="Path to state.db (DB source)")
    args = p.parse_args(argv)
    ok = convert(json_path=args.json, db_path=args.db)
    if ok:
        print(f"[org_state_converter] Written: {args.json} (source=db)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_main())
