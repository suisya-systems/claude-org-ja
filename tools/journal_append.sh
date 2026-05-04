#!/usr/bin/env bash
# claude-org-ja journal append wrapper (M4 DB-only, Issue #267).
#
# Pre-M2 this script appended directly to .state/journal.jsonl. M2
# routed writes through SQLite (`.state/state.db`) while still
# regenerating the jsonl side-output. M4 decommissions the jsonl
# entirely — the ``events`` table is the SoT and this wrapper just
# defers to the Python entry point. Keeping the .sh shim because
# CLAUDE.md / SKILL.md / hook configs still reference it by name.
#
# Usage:
#   bash tools/journal_append.sh <event> [k=v ...]
#
# Examples:
#   bash tools/journal_append.sh worker_spawned \
#       worker=worker-foo dir=workers/foo task=foo
#   bash tools/journal_append.sh suspend reason=user_requested
#
# Audit boundary: the legacy ``$JOURNAL_PATH`` env var is rejected at the
# ja boundary (preserved from the pre-M2 wrapper) so off-canon writes
# can't be silently redirected.

set -euo pipefail

if [ "$#" -lt 1 ]; then
    printf 'usage: %s <event> [k=v ...]\n' "$0" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -n "${JOURNAL_PATH-}" ]; then
    printf 'tools/journal_append.sh: warning: $JOURNAL_PATH override rejected at ja boundary; M4 writes go to <repo_root>/.state/state.db only\n' >&2
fi

EVENT="$1"
shift

# Resolve a Python interpreter the same way the rest of the repo does:
# `py -3` on Windows / `python3` on POSIX / `python` as last resort.
if command -v py >/dev/null 2>&1; then
    PY="py -3"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

cd "$REPO_ROOT"
exec $PY "$SCRIPT_DIR/journal_append.py" "$EVENT" "$@"
