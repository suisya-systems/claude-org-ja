#!/usr/bin/env bash
# Tests for scripts/install-hooks.sh (Issue #69).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_SRC="$REPO_ROOT/scripts/install-hooks.sh"
HOOK_SRC="$REPO_ROOT/.githooks/pre-commit"

PASS=0; FAIL=0; TEST_NUM=0
TMPDIRS=()
cleanup() {
  local d
  for d in "${TMPDIRS[@]}"; do
    [[ -d "$d" ]] && rm -rf "$d"
  done
}
trap cleanup EXIT

assert_exit() {
  local expected="$1" actual="$2" desc="$3"
  ((TEST_NUM++))
  if [[ "$actual" -eq "$expected" ]]; then
    echo "ok $TEST_NUM - $desc"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (expected exit $expected, got $actual)"
    ((FAIL++))
  fi
}

assert_config_eq() {
  local expected="$1" actual="$2" desc="$3"
  ((TEST_NUM++))
  if [[ "$actual" == "$expected" ]]; then
    echo "ok $TEST_NUM - $desc"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (expected '$expected', got '$actual')"
    ((FAIL++))
  fi
}

make_repo() {
  local d
  d=$(mktemp -d)
  TMPDIRS+=("$d")
  git -C "$d" init -q
  mkdir -p "$d/.githooks" "$d/scripts"
  cp "$HOOK_SRC" "$d/.githooks/pre-commit"
  cp "$INSTALL_SRC" "$d/scripts/install-hooks.sh"
  chmod +x "$d/.githooks/pre-commit" "$d/scripts/install-hooks.sh"
  echo "$d"
}

# 1. Fresh repo: installs cleanly, sets config.
repo=$(make_repo)
ec=0
( cd "$repo" && bash scripts/install-hooks.sh >/dev/null 2>&1 ) || ec=$?
assert_exit 0 "$ec" "install on fresh repo succeeds"
cfg=$(git -C "$repo" config --get core.hooksPath || true)
assert_config_eq ".githooks" "$cfg" "core.hooksPath is set to .githooks"

# 2. Re-run is idempotent when value already matches.
ec=0
( cd "$repo" && bash scripts/install-hooks.sh >/dev/null 2>&1 ) || ec=$?
assert_exit 0 "$ec" "re-install with matching config succeeds"

# 3. Pre-existing different value: refuses without --force.
repo=$(make_repo)
git -C "$repo" config core.hooksPath custom-hooks
ec=0
( cd "$repo" && bash scripts/install-hooks.sh >/dev/null 2>&1 ) || ec=$?
assert_exit 1 "$ec" "install refuses to overwrite existing non-.githooks path"
cfg=$(git -C "$repo" config --get core.hooksPath || true)
assert_config_eq "custom-hooks" "$cfg" "core.hooksPath was preserved"

# 4. --force overwrites.
ec=0
( cd "$repo" && bash scripts/install-hooks.sh --force >/dev/null 2>&1 ) || ec=$?
assert_exit 0 "$ec" "install --force overwrites existing value"
cfg=$(git -C "$repo" config --get core.hooksPath || true)
assert_config_eq ".githooks" "$cfg" "core.hooksPath updated to .githooks after --force"

# 5. Outside a git repo: exits non-zero.
d=$(mktemp -d); TMPDIRS+=("$d")
cp "$INSTALL_SRC" "$d/install-hooks.sh"
ec=0
( cd "$d" && bash install-hooks.sh >/dev/null 2>&1 ) || ec=$?
assert_exit 1 "$ec" "running outside a git repo fails"

# 6. Unknown argument is rejected.
repo=$(make_repo)
ec=0
( cd "$repo" && bash scripts/install-hooks.sh --bogus >/dev/null 2>&1 ) || ec=$?
assert_exit 2 "$ec" "unknown argument exits with code 2"

echo ""
echo "# $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
