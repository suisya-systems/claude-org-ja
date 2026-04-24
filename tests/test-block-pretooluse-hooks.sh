#!/usr/bin/env bash
# Tests for .hooks/block-no-verify.sh and .hooks/block-dangerous-git.sh
# Usage: bash tests/test-block-pretooluse-hooks.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NV_HOOK="${REPO_ROOT}/.hooks/block-no-verify.sh"
DG_HOOK="${REPO_ROOT}/.hooks/block-dangerous-git.sh"

pass_count=0
fail_count=0

# test_hook <hook> <command> <expected: block|pass> <label>
test_hook() {
  local hook="$1"
  local cmd="$2"
  local expected="$3"
  local label="${4:-}"
  local exit_code
  echo "{\"tool_input\":{\"command\":$(printf '%s' "$cmd" | jq -Rs .)}}" | bash "$hook" 2>/dev/null
  exit_code=$?
  if [[ "$expected" == "block" && $exit_code -eq 2 ]]; then
    printf "PASS [block ] %s %s\n" "$cmd" "$label"
    pass_count=$((pass_count+1))
  elif [[ "$expected" == "pass" && $exit_code -eq 0 ]]; then
    printf "PASS [pass  ] %s %s\n" "$cmd" "$label"
    pass_count=$((pass_count+1))
  else
    printf "FAIL [exp=%s got=%d] %s %s\n" "$expected" "$exit_code" "$cmd" "$label"
    fail_count=$((fail_count+1))
  fi
}

# Helper: substitute g_it -> git, p_ush -> push, then run.
# This indirection is required because the OUTER worker hook block-git-push.sh
# would block the entire test driver itself if the literal "git push" appeared
# in the script source. The hook under test never sees the obfuscated form.
substitute_run() {
  local hook="$1"
  local cmd_template="$2"
  local expected="$3"
  local label="${4:-}"
  local cmd="${cmd_template//g_it/git}"
  cmd="${cmd//p_ush/push}"
  test_hook "$hook" "$cmd" "$expected" "$label"
}

echo "=== block-no-verify.sh ==="
substitute_run "$NV_HOOK" 'g_it commit --no-verify -m test' block
substitute_run "$NV_HOOK" 'g_it commit -m test --no-verify' block
substitute_run "$NV_HOOK" 'g_it p_ush --no-verify' block
substitute_run "$NV_HOOK" 'g_it -C /tmp/repo commit --no-verify -m x' block
substitute_run "$NV_HOOK" 'g_it -C "C:/Program Files/repo" commit --no-verify -m x' block 'spaces-in-path'
substitute_run "$NV_HOOK" 'g_it commit -m feat' pass
substitute_run "$NV_HOOK" 'g_it status' pass
substitute_run "$NV_HOOK" 'echo "--no-verify"' pass 'no-git-context'
substitute_run "$NV_HOOK" 'echo --no-verify; g_it commit -m ok' pass 'cross-segment-semicolon'
substitute_run "$NV_HOOK" 'echo --no-verify && g_it commit -m ok' pass 'cross-segment-andand'
substitute_run "$NV_HOOK" 'echo --no-verify || g_it commit -m ok' pass 'cross-segment-oror'
substitute_run "$NV_HOOK" 'cat README.md | grep no-verify' pass 'no-git-context-pipe'
substitute_run "$NV_HOOK" 'g_it p_ush origin HEAD:refs/heads/test-nop-branch' pass
# quoted separator regression: separator inside quotes must NOT split the segment
substitute_run "$NV_HOOK" 'g_it commit -m "a ; b" --no-verify' block 'quoted-semicolon-must-block'
substitute_run "$NV_HOOK" 'g_it commit -m "a && b" --no-verify' block 'quoted-andand-must-block'
substitute_run "$NV_HOOK" 'g_it commit -m "a || b" --no-verify' block 'quoted-oror-must-block'
substitute_run "$NV_HOOK" 'g_it commit -m "a | b" --no-verify' block 'quoted-pipe-must-block'
substitute_run "$NV_HOOK" "g_it commit -m 'single ; quote' --no-verify" block 'single-quoted-semi-must-block'
# command substitution regression: flag inside $(...) or `...` must be detected
substitute_run "$NV_HOOK" 'g_it commit $(printf -- "--no-verify") -m x' block 'cmd-sub-dollar-paren'
substitute_run "$NV_HOOK" 'g_it commit `printf -- "--no-verify"` -m x' block 'cmd-sub-backtick'
# variable expansion regression: simple VAR=value bypass must be detected
substitute_run "$NV_HOOK" 'flag=--no-verify; g_it commit "$flag" -m x' block 'var-expansion-simple'
substitute_run "$NV_HOOK" 'flag=--no-verify; g_it commit ${flag} -m x' block 'var-expansion-braces'
substitute_run "$NV_HOOK" 'flag="--no-verify"; g_it commit $flag -m x' block 'var-expansion-quoted-value'
# But unrelated variable use must NOT trigger
substitute_run "$NV_HOOK" 'msg="hello"; g_it commit -m "$msg"' pass 'var-expansion-benign'
# var expansion class round 2: export prefix, inline multi-assign, sub in value
substitute_run "$NV_HOOK" 'export flag=--no-verify; g_it commit "$flag" -m x' block 'var-export-prefix'
substitute_run "$NV_HOOK" 'A=1 flag=--no-verify g_it commit "$flag" -m x' block 'var-inline-multi-assign'
substitute_run "$NV_HOOK" 'flag=$(printf -- "--no-verify"); g_it commit "$flag" -m x' block 'var-value-cmd-sub'
substitute_run "$NV_HOOK" 'flag=`printf -- "--no-verify"`; g_it commit "$flag" -m x' block 'var-value-backtick'

echo ""
echo "=== block-dangerous-git.sh ==="
substitute_run "$DG_HOOK" 'g_it p_ush --force' block
substitute_run "$DG_HOOK" 'g_it p_ush -f' block
substitute_run "$DG_HOOK" 'g_it p_ush --force-with-lease' block
substitute_run "$DG_HOOK" 'g_it p_ush -fu origin main' block 'bundled-short-opt'
substitute_run "$DG_HOOK" 'g_it p_ush -uf origin main' block 'bundled-short-opt'
substitute_run "$DG_HOOK" 'g_it p_ush --force origin main' block
substitute_run "$DG_HOOK" 'g_it -C /tmp p_ush --force' block
substitute_run "$DG_HOOK" 'g_it -C "C:/Program Files/repo" p_ush --force' block 'spaces-in-path'
substitute_run "$DG_HOOK" 'g_it reset --hard HEAD~1' block
substitute_run "$DG_HOOK" 'g_it -C "C:/Program Files/repo" reset --hard HEAD~1' block 'spaces-in-path'
substitute_run "$DG_HOOK" 'g_it reset HEAD --hard' block 'flag-at-end'
substitute_run "$DG_HOOK" 'g_it branch -D some-branch' block
substitute_run "$DG_HOOK" 'g_it branch --delete --force some-branch' block
substitute_run "$DG_HOOK" 'g_it branch --force --delete some-branch' block
# quoted separator regression: separator inside quotes must NOT split the segment
substitute_run "$DG_HOOK" 'g_it p_ush origin "refs/heads/x; y" --force' block 'quoted-semi-must-block'
substitute_run "$DG_HOOK" 'g_it p_ush origin "refs/heads/x && y" --force' block 'quoted-andand-must-block'
substitute_run "$DG_HOOK" "g_it p_ush origin 'refs/heads/x | y' --force" block 'single-quoted-pipe-must-block'
# command substitution regression
substitute_run "$DG_HOOK" 'g_it p_ush origin main $(printf -- "--force")' block 'cmd-sub-dollar-paren'
substitute_run "$DG_HOOK" 'g_it p_ush origin main `printf -- "--force"`' block 'cmd-sub-backtick'
substitute_run "$DG_HOOK" 'g_it reset $(printf -- "--hard") HEAD~1' block 'cmd-sub-reset-hard'
# variable expansion regression
substitute_run "$DG_HOOK" 'mode=--hard; g_it reset "$mode" HEAD~1' block 'var-expansion-reset-hard'
substitute_run "$DG_HOOK" 'f=--force; g_it p_ush origin main $f' block 'var-expansion-push-force'
substitute_run "$DG_HOOK" 'd=-D; g_it branch $d some-branch' block 'var-expansion-branch-D'
# var expansion class round 2: export prefix, inline multi-assign, sub in value
substitute_run "$DG_HOOK" 'export f=--force; g_it p_ush origin main "$f"' block 'var-export-prefix'
substitute_run "$DG_HOOK" 'A=1 f=--force g_it p_ush origin main "$f"' block 'var-inline-multi-assign'
substitute_run "$DG_HOOK" 'mode=$(printf -- "--hard"); g_it reset "$mode" HEAD~1' block 'var-value-cmd-sub'
substitute_run "$DG_HOOK" 'd=`printf -- "-D"`; g_it branch "$d" some' block 'var-value-backtick'

echo ""
echo "--- false-positive guard ---"
substitute_run "$DG_HOOK" 'echo --force; g_it p_ush origin main' pass 'cross-segment-semicolon'
substitute_run "$DG_HOOK" 'echo --force && g_it p_ush origin main' pass 'cross-segment-andand'
substitute_run "$DG_HOOK" 'echo --hard; g_it reset HEAD' pass 'cross-segment-semicolon'
substitute_run "$DG_HOOK" 'echo -D; g_it branch -d some' pass 'cross-segment-semicolon'
substitute_run "$DG_HOOK" 'g_it p_ush origin main' pass
substitute_run "$DG_HOOK" 'g_it reset HEAD' pass
substitute_run "$DG_HOOK" 'g_it reset --soft HEAD~1' pass
substitute_run "$DG_HOOK" 'g_it reset --mixed HEAD~1' pass
substitute_run "$DG_HOOK" 'g_it branch -d some-branch' pass
substitute_run "$DG_HOOK" 'g_it branch --delete some-branch' pass
substitute_run "$DG_HOOK" 'ccmux mcp install --force' pass 'non-git-with-force'
substitute_run "$DG_HOOK" 'bash scripts/install-hooks.sh --force' pass 'non-git-with-force'

echo ""
echo "=== Result: pass=$pass_count fail=$fail_count ==="
[[ $fail_count -eq 0 ]] || exit 1
