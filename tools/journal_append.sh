#!/usr/bin/env bash
# claude-org-ja journal append wrapper (M2 DB-write switch, Issue #267).
#
# Pre-M2 this script sourced core_harness.audit's bash companion and
# appended directly to journal.jsonl. M2 routes writes through SQLite
# (`.state/state.db`) and regenerates the jsonl from the events table;
# the Python wrapper does both. Keeping the .sh entry point because
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
    printf 'tools/journal_append.sh: warning: $JOURNAL_PATH override rejected at ja boundary; writing to canonical <repo_root>/.state/journal.jsonl\n' >&2
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
