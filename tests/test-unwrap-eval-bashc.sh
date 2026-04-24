#!/usr/bin/env bash
# Unit tests for segment-split.sh:unwrap_eval_and_bashc
# 目的: Phase 2a (#79) で追加した unwrap 関数が、flatten_substitutions の
# 副作用に依存せず独立して eval / bash -c / sh -c の引数を取り出せることを
# 明示的に検証する。

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../.hooks/lib/segment-split.sh
source "${REPO_ROOT}/.hooks/lib/segment-split.sh"

pass_count=0
fail_count=0

# expect_contains <label> <input> <expected_substring>
expect_contains() {
  local label="$1" input="$2" expected="$3"
  local out
  out=$(printf '%s\n' "$input" | unwrap_eval_and_bashc)
  if printf '%s' "$out" | grep -qF -- "$expected"; then
    printf "PASS %s\n" "$label"
    pass_count=$((pass_count+1))
  else
    printf "FAIL %s\n  input: %s\n  expected to contain: %s\n  got:\n%s\n" \
      "$label" "$input" "$expected" "$out"
    fail_count=$((fail_count+1))
  fi
}

# expect_empty <label> <input>
expect_empty() {
  local label="$1" input="$2"
  local out
  out=$(printf '%s\n' "$input" | unwrap_eval_and_bashc)
  if [[ -z "$out" ]]; then
    printf "PASS %s\n" "$label"
    pass_count=$((pass_count+1))
  else
    printf "FAIL %s (expected empty, got: %s)\n" "$label" "$out"
    fail_count=$((fail_count+1))
  fi
}

echo "=== unwrap_eval_and_bashc ==="
expect_contains 'eval double-quoted' 'eval "foo bar"' 'foo bar'
expect_contains 'eval single-quoted' "eval 'foo bar'" 'foo bar'
expect_contains 'bash -c double' 'bash -c "foo bar"' 'foo bar'
expect_contains 'sh -c double' 'sh -c "foo bar"' 'foo bar'
expect_contains 'bash -c single' "bash -c 'foo bar'" 'foo bar'
expect_contains 'nested bash + eval' "bash -c \"eval 'inner cmd'\"" 'inner cmd'
expect_contains 'eval unquoted single token' 'eval foo' 'foo'
expect_contains 'multiple eval in one line 1' 'eval "a"; eval "b"' 'a'
expect_contains 'multiple eval in one line 2' 'eval "a"; eval "b"' 'b'

# 非 eval / bash-c は空を返すべき
expect_empty 'plain echo' 'echo hello'
expect_empty 'non-eval command' 'bash foo.sh'
expect_empty 'evaluator is substring of identifier' 'evaluator --help'
expect_empty 'bashrc (bash substring)' 'cat ~/.bashrc'

# 引用符が閉じていない（malformed）ときは取り出さない（FP 回避）
expect_empty 'unclosed double quote' 'eval "unterminated'

total=$((pass_count + fail_count))
echo ""
echo "# $pass_count passed, $fail_count failed out of $total tests"
[[ $fail_count -eq 0 ]] || exit 1
