"""
claude-org Organization Dashboard Server
Python standard library only — no pip install required.

Usage: python3 dashboard/server.py   (Mac/Linux)
       py -3 dashboard/server.py      (Windows)
       Then open http://localhost:8099
"""

import http.server
import json
import os
import queue
import re
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Make `tools.state_db.*` importable when running this script directly
# (e.g. `python dashboard/server.py`). Without this, the package lookup
# fails because dashboard/ is not itself a package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from tools.state_db import connect as _db_connect
    from tools.state_db.queries import (
        get_org_state_summary as _db_org_state_summary,
        list_recent_events as _db_recent_events,
    )
    _DB_AVAILABLE = True
except Exception as _exc:  # pragma: no cover — defensive against partial installs
    print(f"[server] state_db import failed: {_exc}", file=sys.stderr)
    _DB_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORTS = [8099, 8100, 8101]
POLL_INTERVAL = 1.5  # seconds
BASE_DIR = Path(__file__).parent.parent  # claude-org repo root
DASHBOARD_DIR = Path(__file__).parent
PID_FILE = BASE_DIR / ".state" / "dashboard.pid"
STATE_DB_PATH = BASE_DIR / ".state" / "state.db"

# ---------------------------------------------------------------------------
# State builder — parses .state/ and registry/ files
# ---------------------------------------------------------------------------

def _read(path, default=""):
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except Exception:
        return default


def _parse_org_state(text):
    status = "IDLE"
    objective = None
    work_items = []

    for line in text.splitlines():
        m = re.match(r"^Status:\s*(\S+)", line)
        if m:
            status = m.group(1).upper()

        m = re.match(r"^Current Objective:\s*(.+)", line)
        if m:
            objective = m.group(1).strip()

        # Work items: "- task-id: タイトル [STATUS]"
        m = re.match(r"^- ([\w-]+):\s*(.+?)\s*\[(\w+)\]", line)
        if m:
            work_items.append({
                "id": m.group(1),
                "title": m.group(2).strip(),
                "status": m.group(3).upper(),
                "progress": None,
                "worker": None,
            })

        # Sub-lines for last item
        if work_items:
            m = re.match(r"^\s+- 結果:\s*(.+)", line)
            if m:
                work_items[-1]["progress"] = m.group(1).strip()
            m = re.match(r"^\s+- ワーカー:\s*(\S+)", line)
            if m:
                work_items[-1]["worker"] = m.group(1).strip()

    return status, objective, work_items


def _load_org_state_from_json(state_dir):
    """Try to load org-state from JSON. Returns (status, objective, work_items) or None."""
    json_path = state_dir / "org-state.json"
    md_path = state_dir / "org-state.md"
    try:
        if not json_path.exists():
            return None
        # Only use JSON if it is at least as fresh as the Markdown
        if md_path.exists() and json_path.stat().st_mtime < md_path.stat().st_mtime:
            return None
        data = json.loads(json_path.read_text(encoding="utf-8"))
        status = data.get("status", "IDLE")
        objective = data.get("currentObjective")
        work_items = []
        for wi in data.get("workItems", []):
            work_items.append({
                "id": wi["id"],
                "title": wi["title"],
                "status": wi["status"],
                "progress": wi.get("progress"),
                "worker": wi.get("worker"),
            })
        return status, objective, work_items
    except Exception:
        return None


def _parse_journal(text):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            events.append(obj)
        except json.JSONDecodeError:
            pass

    EVENT_LABELS = {
        "worker_spawned": "ワーカー派遣",
        "worker_respawned": "ワーカー再派遣",
        "worker_closed": "ワーカー終了",
        "suspend": "組織を中断",
        "resume": "組織を再開",
    }

    result = []
    for e in reversed(events[-30:]):
        event = e.get("event", "")
        task = e.get("task", "")
        worker = e.get("worker", "")
        label = EVENT_LABELS.get(event, event)
        if task:
            summary = f"{label}: {task}"
            if worker:
                summary += f" ({worker[:8]})"
        else:
            summary = label
        result.append({"ts": e.get("ts"), "event": event, "summary": summary})

    return result


def _parse_projects(text):
    projects = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("|---") or line.startswith("| ---"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) >= 4:
            tasks = [t.strip() for t in cols[4].split("、")] if len(cols) >= 5 else []
            tasks = [t for t in tasks if t and t != "-"]
            projects.append({
                "name": cols[0],
                "path": cols[2] if len(cols) > 2 else "",
                "description": cols[3] if len(cols) > 3 else "",
                "tasks": tasks,
            })
    return projects


def _parse_workers(workers_dir):
    workers = []
    try:
        for md_file in sorted(Path(workers_dir).glob("worker-*.md")):
            text = _read(md_file)
            worker_id = md_file.stem.replace("worker-", "")
            task = None
            pane_id = None
            started = None
            progress_entries = []
            in_log = False

            for line in text.splitlines():
                m = re.match(r"^Task:\s*(.+)", line)
                if m:
                    task = m.group(1).strip()
                # `Pane ID:` is the header name kept for backwards compat
                # with existing worker state files. The value is the renga
                # pane name (e.g. `worker-<task_id>`) since the migration
                # from WezTerm; the dashboard treats it as an opaque string.
                m = re.match(r"^Pane ID:\s*(.+)", line)
                if m:
                    pane_id = m.group(1).strip()
                m = re.match(r"^Started:\s*(.+)", line)
                if m:
                    started = m.group(1).strip()
                if line.startswith("## Progress Log"):
                    in_log = True
                    continue
                if in_log and line.startswith("- ["):
                    # "- [timestamp] message"
                    m = re.match(r"^- \[([^\]]+)\]\s*(.+)", line)
                    if m:
                        progress_entries.append({
                            "ts": m.group(1).strip(),
                            "message": m.group(2).strip(),
                        })

            last_progress = progress_entries[-1] if progress_entries else None
            workers.append({
                "id": worker_id,
                "shortId": worker_id[:8],
                "task": task,
                "paneId": pane_id,
                "started": started,
                "lastProgress": last_progress["message"] if last_progress else None,
                "lastProgressTs": last_progress["ts"] if last_progress else None,
            })
    except Exception:
        pass
    return workers


def _parse_knowledge(curated_dir):
    result = []
    try:
        for md_file in sorted(Path(curated_dir).glob("*.md")):
            if md_file.name == ".gitkeep":
                continue
            text = _read(md_file)
            count = len(re.findall(r"^## ", text, re.MULTILINE))
            theme = md_file.stem.replace("-", " ").replace("_", " ")
            result.append({"theme": theme, "count": count})
    except Exception:
        pass
    return result


def _db_available():
    """M2 (Issue #267): DB is the SoT — no markdown freshness comparison.
    Just check the file exists and the imports succeeded."""
    return _DB_AVAILABLE and STATE_DB_PATH.exists()


_EVENT_LABELS_DB = {
    "worker_spawned": "ワーカー派遣",
    "worker_respawned": "ワーカー再派遣",
    "worker_closed": "ワーカー終了",
    "suspend": "組織を中断",
    "resume": "組織を再開",
}


# importer.import_org_state_md emits these synthetic events to keep the
# "no input row dropped" invariant; they carry no real timestamp and add
# noise to the activity feed. Skip them in DB-sourced activity.
_LEGACY_EVENT_KINDS = {"legacy_active_item", "legacy_recent_item"}


def _activity_from_db_events(events):
    """Render events rows (newest first) into the dashboard's activity shape."""
    out = []
    for e in events:
        kind = e.get("kind") or ""
        if kind in _LEGACY_EVENT_KINDS:
            continue
        label = _EVENT_LABELS_DB.get(kind, kind)
        task = None
        worker = None
        try:
            payload = json.loads(e.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            task = payload.get("task")
            worker = payload.get("worker")
        if task:
            summary = f"{label}: {task}"
            if worker:
                summary += f" ({worker[:8]})"
        else:
            summary = label
        out.append({"ts": e.get("occurred_at"), "event": kind, "summary": summary})
    return out


# Map DB run.status enum → the status vocabulary the dashboard frontend
# (dashboard/app.js) renders icons / labels for. Without this remap an
# `in_use` run would render as a `?` because the frontend has no entry for
# IN_USE. Keep this in sync with app.js's STATUS_* tables.
_DB_STATUS_TO_UI = {
    "in_use": "IN_PROGRESS",
    "queued": "PENDING",
    "review": "REVIEW",
    "completed": "COMPLETED",
    "failed": "BLOCKED",
    "suspended": "PENDING",
    "abandoned": "ABANDONED",
}


def _work_items_from_db_runs(active_runs):
    """Render active runs (in_use / review) into the workItems shape.

    Title/progress/worker are deliberately conservative: the M0 importer
    seeds title=task_id and outcome_note=raw markdown status string, and
    `worker_dir` is an absolute path (not a worker short id). Surfacing
    those verbatim would render duplicate titles, status-as-progress noise
    and full local paths in the worker column. Until the DB schema gains
    proper title / progress / worker_id fields, leave the optional columns
    as None and let the UI fall back to `id` only — matches the markdown
    path when those sub-lines are absent.
    """
    items = []
    for r in active_runs:
        raw = (r.get("status") or "in_use").lower()
        task_id = r.get("task_id")
        title = r.get("title")
        if title == task_id:
            title = task_id  # avoid `id - id` rendering, just keep the id
        items.append({
            "id": task_id,
            "title": title or task_id,
            "status": _DB_STATUS_TO_UI.get(raw, raw.upper()),
            "progress": None,
            "worker": None,
        })
    return items


def _load_state_from_db(state_dir):
    """Return (status, objective, work_items, activity) from state.db,
    or None on failure. M2: org_sessions carries Status / Current Objective
    so we no longer need the markdown overlay for those fields."""
    try:
        conn = _db_connect(STATE_DB_PATH)
        try:
            summary = _db_org_state_summary(conn)
            events = _db_recent_events(conn, limit=30)
        finally:
            conn.close()
    except Exception as exc:
        print(f"[server] DB read failed, falling back to markdown: {exc}",
              file=sys.stderr)
        return None
    session = summary.get("session") or {}
    db_status = session.get("status")
    db_objective = session.get("objective")
    return (
        db_status,
        db_objective,
        _work_items_from_db_runs(summary["active_runs"]),
        _activity_from_db_events(events),
    )


def build_state():
    state_dir = BASE_DIR / ".state"

    # M2: DB is the primary source for everything the schema models —
    # Status / Objective via org_sessions, workItems via active runs,
    # activity via the events table. Markdown / JSON snapshot is consulted
    # only when the DB itself is unavailable (fresh clone, missing import,
    # corrupt file). An *empty* DB is a legitimate state and must not
    # resurrect ghost tasks from a stale dump.
    status = "IDLE"
    objective = None
    work_items: list = []
    activity = None
    db_succeeded = False

    if _db_available():
        db_result = _load_state_from_db(state_dir)
        if db_result is not None:
            db_status, db_objective, work_items, activity = db_result
            if db_status:
                status = db_status
            if db_objective:
                objective = db_objective
            db_succeeded = True

    if not db_succeeded:
        _json_result = _load_org_state_from_json(state_dir)
        if _json_result is not None:
            md_status, md_objective, md_work_items = _json_result
        else:
            org_state_text = _read(state_dir / "org-state.md")
            md_status, md_objective, md_work_items = _parse_org_state(
                org_state_text
            )
        work_items = md_work_items
        if md_status:
            status = md_status
        if md_objective is not None:
            objective = md_objective
        journal_text = _read(state_dir / "journal.jsonl")
        activity = _parse_journal(journal_text)

    projects_text = _read(BASE_DIR / "registry" / "projects.md")
    projects = _parse_projects(projects_text)

    # Source of truth for "live worker" is presence directly under .state/workers/;
    # closing a worker moves its md file to .state/workers/archive/ (org-delegate Step 5).
    workers = _parse_workers(state_dir / "workers")

    knowledge = _parse_knowledge(BASE_DIR / "knowledge" / "curated")

    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "objective": objective,
        "projects": projects,
        "workItems": work_items,
        "workers": workers,
        "activity": activity,
        "knowledge": knowledge,
    }

# ---------------------------------------------------------------------------
# File watcher
# ---------------------------------------------------------------------------

_sse_clients = []
_sse_lock = threading.Lock()
_last_mtimes = {}


def _get_mtimes():
    paths = [
        BASE_DIR / ".state" / "org-state.md",
        BASE_DIR / ".state" / "org-state.json",
        BASE_DIR / ".state" / "journal.jsonl",
        BASE_DIR / "registry" / "projects.md",
        # Watch the state DB so importer rebuilds get pushed to SSE clients.
        # WAL files change on every commit even if state.db itself doesn't,
        # so include them as the writer-side change signal.
        STATE_DB_PATH,
        Path(str(STATE_DB_PATH) + "-wal"),
    ]
    # Glob workers and knowledge
    for p in (BASE_DIR / ".state" / "workers").glob("*.md"):
        paths.append(p)
    for p in (BASE_DIR / "knowledge" / "curated").glob("*.md"):
        paths.append(p)

    mtimes = {}
    for p in paths:
        try:
            mtimes[str(p)] = p.stat().st_mtime
        except OSError:
            pass
    return mtimes


def _watcher_thread():
    global _last_mtimes
    _last_mtimes = _get_mtimes()
    while True:
        time.sleep(POLL_INTERVAL)
        current = _get_mtimes()
        if current != _last_mtimes:
            _last_mtimes = current
            try:
                data = build_state()
                payload = json.dumps(data, ensure_ascii=False)
                with _sse_lock:
                    for q in _sse_clients:
                        try:
                            q.put_nowait(payload)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[watcher] error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Suppress default access log noise; print errors only
        if args and str(args[1]) not in ("200", "304"):
            super().log_message(fmt, *args)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/state":
            self._serve_json(build_state())
        elif path == "/api/events":
            self._serve_sse()
        elif path == "/" or path == "/index.html":
            self._serve_file(DASHBOARD_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/style.css":
            self._serve_file(DASHBOARD_DIR / "style.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self._serve_file(DASHBOARD_DIR / "app.js", "application/javascript; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, content_type):
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = queue.Queue()
        with _sse_lock:
            _sse_clients.append(q)

        try:
            # Send initial state
            initial = json.dumps(build_state(), ensure_ascii=False)
            self.wfile.write(f"data: {initial}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                try:
                    payload = q.get(timeout=25)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Keepalive comment
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[sse] {e}", file=sys.stderr)
        finally:
            with _sse_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Write PID file
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    # Start file watcher
    t = threading.Thread(target=_watcher_thread, daemon=True)
    t.start()

    # Try ports
    server = None
    port = None
    for p in PORTS:
        try:
            server = ThreadedHTTPServer(("localhost", p), Handler)
            port = p
            break
        except OSError:
            continue

    if server is None:
        print(f"ERROR: Could not bind to any of {PORTS}", file=sys.stderr)
        sys.exit(1)

    print(f"Dashboard: http://localhost:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        PID_FILE.unlink(missing_ok=True)
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
