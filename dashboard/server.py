"""
claude-org Organization Dashboard Server
Python standard library only — no pip install required.

Usage: python3 dashboard/server.py   (Mac/Linux)
       py -3 dashboard/server.py      (Windows)
       Then open http://localhost:8099

M4 (Issue #267): the dashboard reads exclusively from
``.state/state.db``. There is no markdown / JSON fallback — fresh
clones must run::

    python -m tools.state_db.importer \\
        --db .state/state.db --root . --rebuild --no-strict

once before ``server.py`` will return useful data.
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

from tools.registry_parser import parse_projects_text as _parse_projects_shared
from tools.state_db import connect as _db_connect
from tools.state_db.queries import (
    get_org_state_summary as _db_org_state_summary,
    list_live_worker_task_ids as _db_live_worker_task_ids,
    list_recent_events as _db_recent_events,
)

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
# State builder — DB-only after M4
# ---------------------------------------------------------------------------

def _read(path, default=""):
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except Exception:
        return default


def _parse_projects(text):
    return [
        {
            "name": p.nickname,
            "path": p.path,
            "description": p.description,
            "tasks": [
                t for t in (s.strip() for s in p.common_tasks.split("、"))
                if t and t != "-"
            ],
        }
        for p in _parse_projects_shared(text)
    ]


def _parse_workers(workers_dir, eligible_task_ids):
    """Build worker cards for the *live* workers only.

    ``eligible_task_ids`` is the display-eligibility set computed from the
    DB (``runs.status IN ('in_use', 'review')``). It is a required argument
    so no caller can accidentally fall back to "every md file under
    .state/workers/", which is what made the panel report dozens of
    finished workers as active; md files never grant eligibility of their
    own.

    A card is emitted for the *intersection* of that set with the md files
    sitting directly under ``workers_dir`` (a run whose file has been
    archived is no longer live — Issue #264). Within that intersection the
    md is a detail source only: if it cannot be parsed the card is still
    emitted, with null details, so a corrupt file degrades one card instead
    of hiding a running worker.
    """
    eligible = set(eligible_task_ids)
    if not eligible:
        return []

    workers = []
    try:
        md_files = sorted(Path(workers_dir).glob("worker-*.md"))
    except OSError as exc:
        print(
            f"[dashboard] workers: cannot list {workers_dir}: {exc}",
            file=sys.stderr,
        )
        return []

    for md_file in md_files:
        worker_id = md_file.stem[len("worker-"):]
        if worker_id not in eligible:
            continue
        try:
            # Read directly (not via _read, which swallows errors and
            # returns "") so an unreadable / undecodable file reaches the
            # per-file handler below instead of rendering a blank card.
            text = md_file.read_text(encoding="utf-8").replace("\r\n", "\n")
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
        except Exception as exc:
            # Per-file containment: one corrupt md must not blank the whole
            # panel (the previous blanket try/except turned a single parse
            # error into "0 workers", indistinguishable from an idle org).
            # The DB says this worker is running, so the card stays — only
            # its md-sourced detail is dropped.
            print(
                f"[dashboard] workers: unparsable {md_file.name}: {exc}; "
                "rendering card without detail",
                file=sys.stderr,
            )
            workers.append({
                "id": worker_id,
                "shortId": worker_id[:8],
                "task": None,
                "paneId": None,
                "started": None,
                "lastProgress": None,
                "lastProgressTs": None,
            })
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
# Set F state-semantics-contract §3 pins four orthogonal phase predicates
# over runs.status. The dashboard renders three of them as distinct UI
# groups so operators can tell reserved / running / review / terminal apart:
#
# * Reserved (§3.1 \\ §3.3, queued only) — rendered as ``reservedItems``,
#   a separate group above Active Work Items. I8 says queued MUST be
#   invisible to the user-visible projection; surfacing it as a distinct
#   anomaly group preserves that while making a stuck T1→T2 transition
#   visible to the operator.
# * User-visible (§3.3, in_use / review) — Active Work Items.
# * Terminal (§3.4) — filtered out of ``list_active_runs`` and not
#   normally rendered. The terminal entries in the map below stay as
#   defense-in-depth so a leaked terminal row renders with the right
#   phase icon instead of "?". ``suspended`` is reserved-for-future
#   per §2 / I4 — no production path writes it today.
#
# Keep this in sync with app.js's STATUS_ICON table.
_DB_STATUS_TO_UI = {
    "queued": "RESERVED",
    "in_use": "IN_PROGRESS",
    "review": "REVIEW",
    "completed": "COMPLETED",
    "failed": "BLOCKED",
    "suspended": "PENDING",
    "abandoned": "ABANDONED",
}


def _work_items_from_db_runs(runs, default_status="in_use"):
    """Render run rows into the app.js workItems shape.

    Used for both the Set F §3.3 user-visible projection (in_use / review)
    and the §3.1 \\ §3.3 reserved-only projection (queued); callers pass the
    appropriate default_status for the rare row whose status field is empty.
    """
    items = []
    for r in runs:
        raw = (r.get("status") or default_status).lower()
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


def _load_state_from_db():
    """Return (status, objective, work_items, reserved_items, activity).

    M4 (Issue #267): the DB is required. Callers must check
    ``STATE_DB_PATH.exists()`` first; this function raises on a missing
    file rather than degrading silently.

    ``work_items`` is the Set F §3.3 user-visible projection (in_use /
    review); ``reserved_items`` is the §3.1 \\ §3.3 reserved-only group
    (queued). They are returned separately so app.js can keep the I8
    anomaly surface visually distinct from Active Work Items.
    """
    conn = _db_connect(STATE_DB_PATH)
    try:
        summary = _db_org_state_summary(conn)
        events = _db_recent_events(conn, limit=30)
    finally:
        conn.close()
    session = summary.get("session") or {}
    return (
        session.get("status"),
        session.get("objective"),
        _work_items_from_db_runs(summary["active_runs"]),
        _work_items_from_db_runs(
            summary.get("reserved_runs", []), default_status="queued"
        ),
        _activity_from_db_events(events),
    )


def _live_worker_task_ids():
    """Return the DB-side display-eligibility set for the workers panel.

    Never raises: on a missing DB, or if the eligibility query itself
    fails, it returns an empty set and logs the reason, so the panel
    degrades to "no live workers" rather than to "show everything on
    disk".

    Scope note: this only covers the workers panel. If the DB file exists
    but is corrupt, ``_load_state_from_db`` raises first and the whole
    ``/api/state`` response fails — that is the deliberate M4 (Issue #267)
    "DB is required" behaviour, and this helper does not soften it. The
    guard here is defense-in-depth for the cases the M4 path survives (a
    transient lock, a schema the org-state queries tolerate but this one
    does not).
    """
    if not STATE_DB_PATH.exists():
        print(
            "[dashboard] workers: state.db not found at "
            f"{STATE_DB_PATH}; rendering 0 workers",
            file=sys.stderr,
        )
        return set()
    try:
        conn = _db_connect(STATE_DB_PATH)
        try:
            return set(_db_live_worker_task_ids(conn))
        finally:
            conn.close()
    except Exception as exc:
        print(
            "[dashboard] workers: live-worker query failed "
            f"({exc.__class__.__name__}: {exc}); rendering 0 workers",
            file=sys.stderr,
        )
        return set()


def build_state():
    state_dir = BASE_DIR / ".state"

    # M4: the DB is required. If it isn't on disk we still serve a
    # minimal "IDLE / no data" payload so the dashboard renders an
    # empty shell with a guidance message; the operator should then
    # run the importer.
    if not STATE_DB_PATH.exists():
        # Codex r3 m-1: distinguish "no DB exists yet" from "DB present
        # and idle". The pre-fix label "IDLE" looked like a normal
        # operational state and could mask an unconfigured environment;
        # UNINITIALIZED makes the operator action obvious.
        status = "UNINITIALIZED"
        objective = (
            "state.db not found — run `python -m tools.state_db.importer "
            "--db .state/state.db --root . --rebuild --no-strict` to seed "
            "the dashboard."
        )
        work_items: list = []
        reserved_items: list = []
        activity: list = []
    else:
        (
            status,
            objective,
            work_items,
            reserved_items,
            activity,
        ) = _load_state_from_db()
        if not status:
            status = "IDLE"

    projects_text = _read(BASE_DIR / "registry" / "projects.md")
    projects = _parse_projects(projects_text)

    # The workers panel renders the intersection of two liveness signals:
    # the DB phase (the Set F §3.3 user-visible projection — in_use /
    # review; a review pane is still open awaiting human approval, so it
    # stays on the panel per Issue #264) and an md file sitting directly
    # under .state/workers/ (archival means the worker is closed —
    # Issue #264). The DB is the admitting predicate: md presence alone is
    # NOT evidence of a live worker, because archival lags and completed /
    # abandoned / suspended runs keep their file in place. Within the
    # intersection the md contributes card detail only.
    #
    # Fail-safe: if the DB is missing or the eligibility query fails we
    # render zero workers and log why. Falling back to "every md file"
    # would silently reinstate the bug this guards against. (A corrupt DB
    # file never reaches here — the M4 org-state read above raises first,
    # by design.)
    workers = _parse_workers(state_dir / "workers", _live_worker_task_ids())

    knowledge = _parse_knowledge(BASE_DIR / "knowledge" / "curated")

    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "objective": objective,
        "projects": projects,
        "workItems": work_items,
        "reservedItems": reserved_items,
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
