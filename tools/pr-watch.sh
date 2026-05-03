#!/usr/bin/env bash
# Thin POSIX wrapper around tools/pr_watch.py.
# Usage: tools/pr-watch.sh --pr <PR> [--repo OWNER/REPO] [--interval SEC]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/pr_watch.py" "$@"
