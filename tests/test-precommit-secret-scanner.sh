#!/usr/bin/env bash
# Tests for .githooks/pre-commit (Issue #69 secret scanner).
# Creates throwaway git repositories in $TMPDIR, stages concrete inputs, and
# invokes the hook exactly how git would during `git commit`.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
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

assert_stderr_contains() {
  local pattern="$1" file="$2" desc="$3"
  ((TEST_NUM++))
  if grep -qF "$pattern" "$file" 2>/dev/null; then
    echo "ok $TEST_NUM - $desc"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - $desc (stderr did not contain '$pattern')"
    ((FAIL++))
  fi
}

# Build a fresh git repo, wire the hook in, and echo its path.
make_repo() {
  local d
  d=$(mktemp -d)
  TMPDIRS+=("$d")
  git -C "$d" init -q
  git -C "$d" config user.email "test@example.com"
  git -C "$d" config user.name "tester"
  git -C "$d" config commit.gpgsign false
  mkdir -p "$d/.githooks"
  cp "$HOOK_SRC" "$d/.githooks/pre-commit"
  chmod +x "$d/.githooks/pre-commit"
  git -C "$d" config core.hooksPath .githooks
  echo "$d"
}

# Stage $2 as $3 inside repo $1 and attempt a commit.  Writes stderr to $4
# and returns the exit code via stdout.
run_commit() {
  local repo="$1" content="$2" path="$3" stderr_file="$4"
  local file="$repo/$path"
  mkdir -p "$(dirname "$file")"
  printf '%s\n' "$content" > "$file"
  git -C "$repo" add -- "$path"
  local ec=0
  git -C "$repo" commit -m "test" >/dev/null 2>"$stderr_file" || ec=$?
  echo "$ec"
}

# ---- Block cases ----

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'aws_key = "AKIAABCDEFGHIJKLMNOP"' "src/aws.txt" "$stderr")
assert_exit 1 "$ec" "AWS access key id is blocked"
assert_stderr_contains "AWS access key ID" "$stderr" "AWS stderr names the pattern"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123' "src/gh.txt" "$stderr")
assert_exit 1 "$ec" "GitHub classic PAT is blocked"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'token: github_pat_11ABCDEFG0abcdefghijkl_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz012345678' "src/gh2.txt" "$stderr")
assert_exit 1 "$ec" "GitHub fine-grained PAT is blocked"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'ANTHROPIC=sk-ant-abcdefghijklmnopqrstuvwxyz01234567' "src/an.txt" "$stderr")
assert_exit 1 "$ec" "Anthropic API key is blocked"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'OPENAI=sk-abcdefghijklmnopqrstuvwxyz012345' "src/oa.txt" "$stderr")
assert_exit 1 "$ec" "OpenAI API key is blocked"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" '-----BEGIN RSA PRIVATE KEY-----' "src/key.pem" "$stderr")
assert_exit 1 "$ec" "PEM private key header is blocked"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'API_KEY = "supersecretvalue1234"' "src/assign.py" "$stderr")
assert_exit 1 "$ec" "Generic API_KEY assignment is blocked"

# ---- Allow cases ----

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'print("hello world")' "src/app.py" "$stderr")
assert_exit 0 "$ec" "Plain code passes"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'Example: AKIAIOSFODNN7EXAMPLE  <!-- allow-secret -->' "docs/readme.md" "$stderr")
assert_exit 0 "$ec" "allow-secret marker lets docs through"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123' "tests/fixtures/secret.txt" "$stderr")
assert_exit 0 "$ec" "Files under tests/ are excluded"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'AKIAABCDEFGHIJKLMNOP' ".hooks/sample.sh" "$stderr")
assert_exit 0 "$ec" "Files under .hooks/ are excluded"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'API_KEY = "short"' "src/short.py" "$stderr")
assert_exit 0 "$ec" "Short generic assignment below length threshold passes"

# ---- knowledge/ is now scanned (no longer excluded) ----

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'aws_key = "AKIAABCDEFGHIJKLMNOP"' "knowledge/raw/note.md" "$stderr")
assert_exit 1 "$ec" "Secrets inside knowledge/raw/ are still blocked"

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
ec=$(run_commit "$repo" 'aws_key = "AKIAABCDEFGHIJKLMNOP"' "knowledge/curated/note.md" "$stderr")
assert_exit 1 "$ec" "Secrets inside knowledge/curated/ are still blocked"

# ---- Bypass env var ----

repo=$(make_repo); stderr=$(mktemp); TMPDIRS+=("$stderr")
file="$repo/src/bypass.txt"
mkdir -p "$(dirname "$file")"
printf '%s\n' 'AKIAABCDEFGHIJKLMNOP' > "$file"
git -C "$repo" add -- "src/bypass.txt"
ec=0
SKIP_SECRET_SCAN=1 git -C "$repo" commit -m "bypass" >/dev/null 2>"$stderr" || ec=$?
assert_exit 0 "$ec" "SKIP_SECRET_SCAN=1 bypasses the scan"
assert_stderr_contains "SKIP_SECRET_SCAN=1" "$stderr" "Bypass logs a warning to stderr"

echo ""
echo "# $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
