#!/usr/bin/env bash
# Install git hooks for this repository (aainc-ops / Issue #69).
# Points git at .githooks/ so contributors share the same pre-commit
# secret scanner. Idempotent: safe to run any number of times.
set -euo pipefail

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

# chmod +x is a no-op on Windows filesystems but harmless; git-for-windows
# tracks the executable bit via the index.
chmod +x "$hook_file" 2>/dev/null || true

git config core.hooksPath "$hook_dir"

configured=$(git config --get core.hooksPath || true)
echo "install-hooks: core.hooksPath = ${configured:-<unset>}"
echo "install-hooks: pre-commit secret scanner enabled."
