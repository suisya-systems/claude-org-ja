#!/usr/bin/env bash
# Test runner: executes all test-*.sh and reports summary
# Detects both test failures and abnormal exits (syntax errors, crashes)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
total_pass=0
total_fail=0
total_errors=0

for test_file in "$SCRIPT_DIR"/test-*.sh; do
  [[ -f "$test_file" ]] || continue
  echo ""
  echo "=== $(basename "$test_file") ==="
  output=$(bash "$test_file" 2>&1)
  file_exit=$?
  echo "$output"

  # Extract pass/fail counts from "# N passed, M failed" line
  summary_line=$(echo "$output" | grep '# .* passed, .* failed' || true)

  if [[ -z "$summary_line" ]]; then
    # No summary line: script crashed or had a syntax error
    echo "ERROR: $(basename "$test_file") did not produce a summary line (exit code: $file_exit)"
    ((total_errors++))
    continue
  fi

  pass=$(echo "$summary_line" | sed -n 's/^# \([0-9]*\) passed.*/\1/p')
  fail=$(echo "$summary_line" | sed -n 's/.*passed, \([0-9]*\) failed.*/\1/p')
  pass=${pass:-0}
  fail=${fail:-0}

  # Also treat non-zero exit with 0 reported failures as an error
  if [[ $file_exit -ne 0 && $fail -eq 0 ]]; then
    echo "ERROR: $(basename "$test_file") exited with code $file_exit but reported 0 failures"
    ((total_errors++))
  fi

  total_pass=$((total_pass + pass))
  total_fail=$((total_fail + fail))
done

total_tests=$((total_pass + total_fail))
echo ""
echo "==============================="
echo "Total: $total_tests tests, $total_pass passed, $total_fail failed, $total_errors errors"
echo "==============================="

[[ $total_fail -eq 0 && $total_errors -eq 0 ]]
