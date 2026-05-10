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
# Phase 2a: eval / bash -c / sh -c explicit unwrap (Issue #79)
substitute_run "$NV_HOOK" 'eval "g_it commit --no-verify"' block 'eval-double-quoted'
substitute_run "$NV_HOOK" "eval 'g_it commit --no-verify'" block 'eval-single-quoted'
substitute_run "$NV_HOOK" 'bash -c "g_it commit --no-verify"' block 'bash-c-double-quoted'
substitute_run "$NV_HOOK" "sh -c 'g_it commit --no-verify'" block 'sh-c-single-quoted'
substitute_run "$NV_HOOK" "bash -c \"eval 'g_it commit --no-verify'\"" block 'nested-bash-eval'
# unquoted multi-token eval: segment-level regex already catches (tokens appear verbatim)
substitute_run "$NV_HOOK" 'eval g_it commit --no-verify' block 'eval-unquoted-multitoken'
# Phase 2 (Refs #379): merge / pull / am --no-verify
substitute_run "$NV_HOOK" 'g_it merge --no-verify topic-branch' block 'merge-no-verify'
substitute_run "$NV_HOOK" 'g_it merge topic-branch --no-verify' block 'merge-no-verify-flag-at-end'
substitute_run "$NV_HOOK" 'g_it pull --no-verify origin main' block 'pull-no-verify'
substitute_run "$NV_HOOK" 'g_it am --no-verify patch.mbox' block 'am-no-verify'
substitute_run "$NV_HOOK" 'g_it -C /tmp/repo merge --no-verify other' block 'merge-no-verify-with-C'
# Phase 2: env-var bypass (HUSKY / SKIP_SECRET_SCAN / NO_VERIFY)
substitute_run "$NV_HOOK" 'HUSKY=0 g_it commit -m feat' block 'husky-zero-prefix'
substitute_run "$NV_HOOK" 'HUSKY=false g_it commit -m feat' block 'husky-false-prefix'
substitute_run "$NV_HOOK" 'export HUSKY=0; g_it commit -m feat' block 'husky-export'
substitute_run "$NV_HOOK" 'SKIP_SECRET_SCAN=1 g_it commit -m feat' block 'skip-secret-scan-prefix'
substitute_run "$NV_HOOK" 'NO_VERIFY=1 g_it commit -m feat' block 'no-verify-env-prefix'
# Pure git plumbing without any of these env-vars or --no-verify must still pass
substitute_run "$NV_HOOK" 'g_it merge topic-branch' pass 'merge-clean-must-pass'
substitute_run "$NV_HOOK" 'g_it pull origin main' pass 'pull-clean-must-pass'
substitute_run "$NV_HOOK" 'g_it am patch.mbox' pass 'am-clean-must-pass'
# Phase 2 round 2 (Codex Major): git commit -n short form
substitute_run "$NV_HOOK" 'g_it commit -n -m feat' block 'commit-n-short-form'
substitute_run "$NV_HOOK" 'g_it commit -nm feat' block 'commit-bundled-nm'
substitute_run "$NV_HOOK" 'g_it commit -mn feat' block 'commit-bundled-mn'
substitute_run "$NV_HOOK" 'g_it -C /tmp/repo commit -n -m feat' block 'commit-n-with-C'
# But -n on push / merge / pull means --dry-run / --no-stat, not --no-verify;
# do not over-trigger. Note: hook still allows these forms.
substitute_run "$NV_HOOK" 'g_it push -n origin main' pass 'push-n-dry-run-must-pass'
substitute_run "$NV_HOOK" 'g_it merge -n topic' pass 'merge-n-no-stat-must-pass'
# Plain `-m` (with message arg) must still pass
substitute_run "$NV_HOOK" 'g_it commit -m feat' pass 'commit-m-must-pass-after-n-rule'
substitute_run "$NV_HOOK" 'g_it commit -am feat' pass 'commit-am-bundled-must-pass'
# Phase 2 round 3 (Codex Major): git -c core.hooksPath / hook.* override
substitute_run "$NV_HOOK" 'g_it -c core.hooksPath=/tmp/empty commit -m feat' block 'git-c-core-hookspath'
substitute_run "$NV_HOOK" 'g_it -c core.hooksPath=/dev/null commit -m feat' block 'git-c-core-hookspath-devnull'
substitute_run "$NV_HOOK" 'g_it -c hook.precommit.command=true commit -m feat' block 'git-c-hook-override'
substitute_run "$NV_HOOK" 'g_it -c core.hooksPath=/tmp/x p_ush origin main' block 'git-c-core-hookspath-push'
# But -c with unrelated keys must still pass (e.g. user.name override)
substitute_run "$NV_HOOK" 'g_it -c user.name=worker commit -m feat' pass 'git-c-user-name-must-pass'
substitute_run "$NV_HOOK" 'g_it -c color.ui=never status' pass 'git-c-status-must-pass'
# Phase 2 round 4 (Codex Major): variable-expansion bypass for -c / env-var
substitute_run "$NV_HOOK" 'cfg=core.hooksPath=/tmp/empty; g_it -c "$cfg" commit -m feat' block 'cfg-var-hookspath'
substitute_run "$NV_HOOK" 'cfg=core.hooksPath=/dev/null; g_it -c ${cfg} commit -m feat' block 'cfg-var-braces-hookspath'
substitute_run "$NV_HOOK" 'h=HUSKY=0; env "$h" g_it commit -m feat' block 'env-var-husky-via-var'
substitute_run "$NV_HOOK" 'k=SKIP_SECRET_SCAN=1; env "$k" g_it commit -m feat' block 'env-var-skip-via-var'

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
# Phase 2a: eval / bash -c / sh -c explicit unwrap (Issue #79)
substitute_run "$DG_HOOK" 'eval "g_it p_ush --force"' block 'eval-push-force'
substitute_run "$DG_HOOK" 'bash -c "g_it reset --hard HEAD~1"' block 'bash-c-reset-hard'
substitute_run "$DG_HOOK" "sh -c 'g_it branch -D some'" block 'sh-c-branch-D'
substitute_run "$DG_HOOK" "bash -c \"eval 'g_it p_ush --force'\"" block 'nested-bash-eval-push-force'
substitute_run "$DG_HOOK" 'eval g_it p_ush --force' block 'eval-unquoted-multitoken-dg'
# Phase 2 (Refs #379): clean -fd / -f / bundled
substitute_run "$DG_HOOK" 'g_it clean -fd' block 'clean-fd'
substitute_run "$DG_HOOK" 'g_it clean -f' block 'clean-f'
substitute_run "$DG_HOOK" 'g_it clean --force' block 'clean-force-long'
substitute_run "$DG_HOOK" 'g_it clean -fdx' block 'clean-bundled-fdx'
substitute_run "$DG_HOOK" 'g_it clean -dfx' block 'clean-bundled-dfx'
substitute_run "$DG_HOOK" 'g_it -C /tmp/repo clean -fd' block 'clean-fd-with-C'
# Phase 2: checkout -- <path>
substitute_run "$DG_HOOK" 'g_it checkout -- .' block 'checkout-discard-dot'
substitute_run "$DG_HOOK" 'g_it checkout -- some/file.py' block 'checkout-discard-path'
substitute_run "$DG_HOOK" 'g_it -C /tmp/repo checkout -- .' block 'checkout-discard-dot-with-C'
# Phase 2: restore --source --worktree
substitute_run "$DG_HOOK" 'g_it restore --source=HEAD~1 --worktree some/file.py' block 'restore-source-worktree'
substitute_run "$DG_HOOK" 'g_it restore --source HEAD~1 -W some/file.py' block 'restore-source-W'
# Phase 2 round 2 (Codex Major): restore --source default (no --worktree) is also destructive
substitute_run "$DG_HOOK" 'g_it restore --source=HEAD~1 some/file.py' block 'restore-source-default-worktree'
substitute_run "$DG_HOOK" 'g_it restore -s HEAD~1 some/file.py' block 'restore-s-short-form'
substitute_run "$DG_HOOK" 'g_it restore -s HEAD~1 -W some/file.py' block 'restore-s-W-short'
# Phase 2 round 2 codex: attached-arg short form must also block
substitute_run "$DG_HOOK" 'g_it restore -sHEAD~1 some/file.py' block 'restore-s-attached-arg'
substitute_run "$DG_HOOK" 'g_it restore -sHEAD some/file.py' block 'restore-s-attached-no-tilde'
substitute_run "$DG_HOOK" 'g_it -C /tmp/repo restore -sHEAD~1 some/file.py' block 'restore-s-attached-with-C'
# index-only restore via --staged is safe (only writes index, no worktree loss)
substitute_run "$DG_HOOK" 'g_it restore --staged --source=HEAD~1 some/file.py' pass 'restore-staged-source-must-pass'
substitute_run "$DG_HOOK" 'g_it restore -S -s HEAD~1 some/file.py' pass 'restore-S-s-must-pass'
# but --staged + --worktree (both) IS destructive on worktree
substitute_run "$DG_HOOK" 'g_it restore --staged --worktree --source=HEAD~1 some/file.py' block 'restore-staged-worktree-source-block'
# Phase 2: tag -d / --delete
substitute_run "$DG_HOOK" 'g_it tag -d v1.0.0' block 'tag-delete-short'
substitute_run "$DG_HOOK" 'g_it tag --delete v1.0.0' block 'tag-delete-long'
# Phase 2: update-ref -d / --stdin
substitute_run "$DG_HOOK" 'g_it update-ref -d refs/heads/topic' block 'update-ref-delete'
substitute_run "$DG_HOOK" 'g_it update-ref --stdin' block 'update-ref-stdin'
# Phase 2: reflog expire/delete --all / --expire=now
substitute_run "$DG_HOOK" 'g_it reflog expire --all --expire=now' block 'reflog-expire-all-now'
substitute_run "$DG_HOOK" 'g_it reflog expire --expire-unreachable=now --all' block 'reflog-expire-unreachable-now'
substitute_run "$DG_HOOK" 'g_it reflog delete --all' block 'reflog-delete-all'

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
substitute_run "$DG_HOOK" 'renga mcp install --force' pass 'non-git-with-force'
substitute_run "$DG_HOOK" 'bash scripts/install-hooks.sh --force' pass 'non-git-with-force'
# Phase 2 false-positive guard: legitimate forms must keep passing
substitute_run "$DG_HOOK" 'g_it tag v1.0.0' pass 'tag-create-must-pass'
substitute_run "$DG_HOOK" 'g_it tag -a v1.0.0 -m release' pass 'tag-annotate-must-pass'
substitute_run "$DG_HOOK" 'g_it restore some/file.py' pass 'restore-no-source-must-pass'
substitute_run "$DG_HOOK" 'g_it restore --staged some/file.py' pass 'restore-staged-must-pass'
substitute_run "$DG_HOOK" 'g_it checkout main' pass 'checkout-branch-must-pass'
substitute_run "$DG_HOOK" 'g_it checkout -b feat/new' pass 'checkout-new-branch-must-pass'
substitute_run "$DG_HOOK" 'g_it reflog' pass 'reflog-read-must-pass'
substitute_run "$DG_HOOK" 'g_it reflog show' pass 'reflog-show-must-pass'
substitute_run "$DG_HOOK" 'g_it clean -n' pass 'clean-dry-run-must-pass'
substitute_run "$DG_HOOK" 'g_it update-ref refs/heads/topic HEAD' pass 'update-ref-write-must-pass'

total=$((pass_count + fail_count))
echo ""
echo "=== Result: pass=$pass_count fail=$fail_count ==="
echo "# $pass_count passed, $fail_count failed out of $total tests"
[[ $fail_count -eq 0 ]] || exit 1
