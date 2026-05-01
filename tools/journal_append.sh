#!/usr/bin/env bash
# claude-org-ja journal append wrapper (Step D shim, refs ja#128 /
# core-harness 0.3.0).
#
# Wraps `core_harness.audit.journal_append` with the org-specific
# journal path (`.state/journal.jsonl`) baked in. Locates the bash
# companion library by asking the installed core-harness package where
# it lives, so this script keeps working through `pip install -e .`,
# `pip install` from the pinned git URL, and any future package
# layout change in core-harness.
#
# Usage:
#   bash tools/journal_append.sh <event> [k=v ...]
#
# Examples:
#   bash tools/journal_append.sh worker_spawned \
#       worker=worker-foo dir=workers/foo task=foo
#   bash tools/journal_append.sh suspend reason=user_requested
#
# For arbitrary payload shapes (nested objects, typed values, keys
# outside [A-Za-z_][A-Za-z0-9_]*), use the Python entry point:
#
#   py -3 tools/journal_append.py <event> --json '<payload>'
#
# The journal path is fixed at `<repo_root>/.state/journal.jsonl`
# regardless of the caller's cwd, where `<repo_root>` is the directory
# one level above this script (`tools/..`). This matters because the
# dispatcher pane runs with cwd=.dispatcher/, where a cwd-relative
# default would write to .dispatcher/.state/journal.jsonl by mistake.
#
# Audit boundary (refs cross-review M3): the legacy ``$JOURNAL_PATH``
# environment variable is rejected at this ja boundary so off-canon
# writes can't be silently redirected. If the variable is set we emit
# a stderr warning and proceed with the canonical path.

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
JOURNAL_PATH="$REPO_ROOT/.state/journal.jsonl"

# Ask the installed core-harness package where its bash companion
# lives. Going through Python keeps us robust to `pip install` layout
# differences (site-packages vs. editable installs vs. zipped wheels).
CORE_HARNESS_AUDIT_LIB="$(
    py -3 -c 'import core_harness.audit, pathlib, sys; sys.stdout.write(str(pathlib.Path(core_harness.audit.__file__).parent / "lib" / "journal_append.sh"))' \
        2>/dev/null \
    || python3 -c 'import core_harness.audit, pathlib, sys; sys.stdout.write(str(pathlib.Path(core_harness.audit.__file__).parent / "lib" / "journal_append.sh"))' \
        2>/dev/null \
    || python -c 'import core_harness.audit, pathlib, sys; sys.stdout.write(str(pathlib.Path(core_harness.audit.__file__).parent / "lib" / "journal_append.sh"))'
)"

if [ -z "$CORE_HARNESS_AUDIT_LIB" ] || [ ! -f "$CORE_HARNESS_AUDIT_LIB" ]; then
    printf 'tools/journal_append.sh: core_harness.audit lib not resolvable; check requirements.txt pin\n' >&2
    exit 1
fi

# shellcheck source=/dev/null
source "$CORE_HARNESS_AUDIT_LIB"

journal_append "$JOURNAL_PATH" "$@"
