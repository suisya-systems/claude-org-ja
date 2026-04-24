#!/usr/bin/env bash
# Install git hooks for this repository (aainc-ops / Issue #69).
# Points git at .githooks/ so contributors share the same pre-commit
# secret scanner. Safe to run repeatedly when no conflicting hook path
# is configured; use --force to replace an existing non-.githooks path.
set -euo pipefail

force=0
for arg in "$@"; do
  case "$arg" in
    -f|--force) force=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/install-hooks.sh [--force]

Sets core.hooksPath to .githooks so git invokes the shared pre-commit
secret scanner. Refuses to overwrite an existing value that points
somewhere other than .githooks unless --force is given.
USAGE
      exit 0
      ;;
    *) echo "install-hooks: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

repo_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$repo_root" ]]; then
  echo "install-hooks: not inside a git repository." >&2
  exit 1
fi

cd "$repo_root"

hook_dir=".githooks"
hook_file="$hook_dir/pre-commit"

if [[ ! -f "$hook_file" ]]; then
  echo "install-hooks: $hook_file not found under $repo_root." >&2
  exit 1
fi

current=$(git config --get core.hooksPath || true)
if [[ -n "$current" && "$current" != "$hook_dir" ]]; then
  if [[ "$force" != "1" ]]; then
    echo "install-hooks: core.hooksPath is already set to '$current'." >&2
    echo "install-hooks: refusing to overwrite; pass --force to replace it." >&2
    exit 1
  fi
  echo "install-hooks: --force set; replacing core.hooksPath '$current' -> '$hook_dir'." >&2
fi

# chmod +x is a no-op on Windows filesystems but harmless; git-for-windows
# tracks the executable bit via the index.
chmod +x "$hook_file" 2>/dev/null || true

git config core.hooksPath "$hook_dir"

configured=$(git config --get core.hooksPath || true)
echo "install-hooks: core.hooksPath = ${configured:-<unset>}"
echo "install-hooks: pre-commit secret scanner enabled."
