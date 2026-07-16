"""Repo-root + state.db path discovery for cwd-independent tools (Issue #398).

Several tools (`pr_watch.py`, `journal_append.py`, `set_run_pr_open.py`,
`run_complete_on_merge.py`) need to open ``<repo_root>/.state/state.db``
regardless of the cwd they were invoked from. Pre-#398 they anchored the
path off ``Path(__file__).resolve().parent.parent``, which works when the
script lives in the main checkout, but resolves to the **worktree** root
when the script is invoked through a `.worktrees/<task>/` checkout. The
worktree's ``.state/`` is empty (only ``.gitkeep`` + ``workers/``), so
opens silently created an empty DB at the wrong path and downstream
SELECT/INSERT crashed with "no such table: runs/events".

Discovery contract:

1. Walk up from this file's directory until we find a ``pyproject.toml``
   whose ``[project] name = "claude-org-ja"``. ``.git`` alone is not
   sufficient — it exists in worktrees too (as a file pointer).
2. If the found root is a worktree (``.git`` is a file containing
   ``gitdir: ...``), resolve back to the main checkout that owns the
   real ``.git`` directory. The canonical state.db lives in the main
   checkout, not in worktree-private ``.state/`` directories.
3. Honor the ``STATE_DB_PATH`` environment variable as an override.
4. Callers may pass an explicit path (e.g. CLI ``--db-path``) which
   trumps both env and discovery — used by tests and for debugging
   against a custom DB.

Precedence for :func:`resolve_state_db_path`:
    explicit (``cli_override``)  >  ``$STATE_DB_PATH``  >  discovered

Schema verification (:func:`verify_state_db_schema`) is the loud-failure
mode for the bug class above: when the resolved path points at a file
that exists but is missing the ``runs`` / ``events`` tables, raise with
an actionable message instead of letting the next SELECT/INSERT crash
with the bare sqlite error.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

# Package names that identify a claude-org repo root. ``claude-org-ja`` is
# this (ja) upstream's own name; ``claude-org`` is the EN mirror's name after
# the package rename (en#489 / en#506). Accepting both keeps discovery working
# in the auto-mirrored EN checkout without changing ja's behavior — same
# two-name shape as ``resolve_worker_layout._CLAUDE_ORG_REPO_NAMES`` (ja#717).
_CLAUDE_ORG_REPO_NAMES: tuple[str, ...] = ("claude-org-ja", "claude-org")

# String markers we look for in pyproject.toml. Doing a substring search is
# intentional — it avoids pulling tomllib (3.11+) / tomli (3.10) into every
# tool just to read one field, and the marker is a stable canary string in
# this repo's own pyproject. The closing quote is part of each marker so the
# ``claude-org`` marker does NOT spuriously match ``name = "claude-org-ja"``.
_PROJECT_NAME_MARKERS: tuple[str, ...] = tuple(
    f'name = "{name}"' for name in _CLAUDE_ORG_REPO_NAMES
)

# Required tables for state.db to be usable by event-recording tools.
# The schema has more (projects, workstreams, …) but `runs` + `events`
# are the two that pr_watch / journal_append / set_run_pr_open touch.
_REQUIRED_TABLES = ("runs", "events")


def _pyproject_has_marker(pyproject: Path) -> bool:
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in text for marker in _PROJECT_NAME_MARKERS)


def _resolve_main_checkout_from_worktree(git_file: Path) -> Optional[Path]:
    """Given a worktree's ``.git`` file, return the main checkout's root.

    Worktree ``.git`` files contain a single ``gitdir: <path>`` line
    pointing at ``<main_git>/worktrees/<name>``. The main checkout is
    three parents up from that gitdir (gitdir → worktrees → .git → main).

    The gitdir path may be absolute or relative. Per gitrepository-layout
    (and confirmed by ``git worktree add --relative-paths``), a relative
    gitdir is resolved against the directory holding the ``.git`` file —
    NOT the cwd. We must honour that here, otherwise invocations from a
    different cwd would resolve the wrong target and silently fall back
    to the worktree's own ``.state/`` (the bug we're fixing).

    Returns None if the file is not a valid worktree pointer or the
    inferred main checkout doesn't itself look like the right project
    (sanity-check via :data:`_PROJECT_NAME_MARKERS`).
    """
    try:
        text = git_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "gitdir:"
    if not text.startswith(prefix):
        return None
    gitdir = Path(text[len(prefix):].strip())
    if not gitdir.is_absolute():
        gitdir = (git_file.parent / gitdir).resolve()
    # gitdir = <main>/.git/worktrees/<name>
    # parents: gitdir.parent = .git/worktrees, .parent.parent = .git,
    # .parent.parent.parent = main checkout root.
    main_root = gitdir.parent.parent.parent
    if not main_root.is_dir():
        return None
    main_pyproject = main_root / "pyproject.toml"
    if not main_pyproject.is_file() or not _pyproject_has_marker(main_pyproject):
        return None
    return main_root


def discover_repo_root(start: Optional[Path] = None) -> Path:
    """Walk up from ``start`` (default: this file's directory) to find the
    claude-org-ja main checkout.

    Returns the absolute path of the directory whose ``pyproject.toml``
    declares ``name = "claude-org-ja"``. If that directory is itself a
    worktree (``.git`` is a file pointer), redirects to the main checkout.

    Raises :class:`RuntimeError` if no candidate is found.
    """
    if start is None:
        start = Path(__file__).resolve().parent
    else:
        start = Path(start).resolve()

    for candidate in [start, *start.parents]:
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        if not _pyproject_has_marker(pyproject):
            continue
        git_path = candidate / ".git"
        if git_path.is_file():
            main_root = _resolve_main_checkout_from_worktree(git_path)
            if main_root is not None:
                return main_root
            # Worktree pointer was malformed; fall through to candidate
            # rather than failing — better to return the worktree root
            # than to raise, callers will get a schema-mismatch error
            # downstream that points to the same fix.
        return candidate

    raise RuntimeError(
        "could not locate claude-org repo root (no pyproject.toml with "
        f'any of {list(_PROJECT_NAME_MARKERS)!r} found walking up from {start})'
    )


def resolve_state_db_path(cli_override: Optional[Path] = None) -> Path:
    """Resolve ``state.db`` path with precedence: explicit > env > discovery.

    * ``cli_override`` — typically the value of a CLI ``--db-path`` flag.
    * ``$STATE_DB_PATH`` — environment variable override.
    * discovery — walks up from this file to the main checkout.

    The returned path is absolute. The file may or may not exist;
    callers can pair this with :func:`verify_state_db_schema` to assert
    schema validity before reads/writes.
    """
    if cli_override is not None:
        return Path(cli_override).resolve()
    env_override = os.environ.get("STATE_DB_PATH")
    if env_override:
        return Path(env_override).resolve()
    return discover_repo_root() / ".state" / "state.db"


class StateDbSchemaError(RuntimeError):
    """Raised when state.db is missing or its schema is incomplete.

    Carries an actionable message including the resolved path and the
    invocation cwd so the operator can immediately see whether they are
    pointed at the wrong file.
    """


def _format_missing_message(db_path: Path, reason: str) -> str:
    cwd = os.getcwd()
    return (
        f"state.db {reason} at {db_path}; run cwd was {cwd}. "
        "Try --db-path <path> or set STATE_DB_PATH to the canonical "
        "<repo_root>/.state/state.db. (Issue #398: tools must run from "
        "the main checkout's state.db, not a worktree's empty .state/.)"
    )


def verify_state_db_schema(
    db_path: Path,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Assert ``db_path`` exists and contains the required tables.

    Opens a short-lived sqlite connection if ``conn`` is not supplied
    (callers that already hold an open connection can pass it to avoid
    re-opening). Raises :class:`StateDbSchemaError` with an actionable
    message on failure — callers translate that into a non-zero exit
    code (see :func:`verify_or_exit`).

    A corrupt sqlite file (e.g. random bytes planted at the resolved
    path) raises ``sqlite3.DatabaseError`` from the schema-introspection
    query; we wrap that into :class:`StateDbSchemaError` too so callers
    see a single, actionable error type instead of a bare traceback.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise StateDbSchemaError(_format_missing_message(db_path, "not found"))

    own_conn = False
    if conn is None:
        conn = sqlite3.connect(str(db_path))
        own_conn = True
    try:
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StateDbSchemaError(
                _format_missing_message(
                    db_path,
                    f"is not a valid sqlite database ({exc})",
                )
            ) from exc
        present = {row[0] for row in rows}
        missing = [t for t in _REQUIRED_TABLES if t not in present]
        if missing:
            raise StateDbSchemaError(
                _format_missing_message(
                    db_path,
                    f"is missing required table(s) {missing!r}",
                )
            )
    finally:
        if own_conn:
            conn.close()


def verify_or_exit(
    db_path: Path,
    conn: Optional[sqlite3.Connection] = None,
    *,
    prog: str = "tool",
    exit_code: int = 2,
) -> None:
    """Convenience wrapper: verify schema and ``sys.exit`` on failure.

    Tools call this right after opening the connection (and before any
    SELECT/INSERT) so a wrong-path / corrupted-DB invocation surfaces as
    a clean stderr message + non-zero exit instead of a stack trace from
    sqlite3.OperationalError. ``prog`` is the script name used in the
    stderr prefix; ``exit_code`` defaults to 2 (usage / configuration
    error, matching the rest of the affected CLIs).
    """
    import sys
    try:
        verify_state_db_schema(db_path, conn=conn)
    except StateDbSchemaError as exc:
        sys.stderr.write(f"{prog}: error: {exc}\n")
        sys.exit(exit_code)


__all__ = [
    "StateDbSchemaError",
    "discover_repo_root",
    "resolve_state_db_path",
    "verify_or_exit",
    "verify_state_db_schema",
]
