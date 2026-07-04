#!/usr/bin/env bash
# Phase 5 shim audit: confirmed minimal as of 2026-05-04 (#130)
# Compatibility shim — Step C / ja#128.
#
# The 325-line implementation that previously lived here moved up to
# core-harness 0.2.0 as `core_harness/hooks/lib/core_harness_hooks.sh`
# (design PR #196 §4 Step C, Q4 one-way dependency). This file is now
# a thin org-boundary shim: it sets the claude-org-ja deny-line locale
# and forwards to the framework lib.
#
# Function names exposed (split_segments, flatten_substitutions,
# collect_assignments, expand_known_vars, unwrap_eval_and_bashc) are
# unchanged so existing hooks (`block-no-verify.sh`,
# `block-dangerous-git.sh`) and tests (`tests/test-unwrap-eval-bashc.sh`)
# work without modification.
#
# This file is the *single* place that injects the legacy Japanese
# "ブロック: " prefix; core-harness ships only the neutral English
# default, so the org-specific locale stays inside ja.

# Org-specific locale injection (Layer 1 stays unaware of locale).
# Existing hook/test stderr assertions rely on this exact prefix.
export CORE_HARNESS_BLOCK_PREFIX="${CORE_HARNESS_BLOCK_PREFIX:-ブロック: }"

# Resolve the framework lib path through the pinned core-harness
# package. requirements.txt pins an exact tag; if the package is not
# importable we fail closed (echo + exit 2) — same pattern as the
# pre-extraction "missing jq/awk" guard, so behaviour is unchanged
# for misconfigured environments.
#
# Sets two globals rather than printing to stdout so the accumulated
# interpreter-attempt list survives into the rejection message (a
# command-substitution subshell would discard it):
#   __CORE_HARNESS_LIB_DIR  — resolved lib dir on success, "" otherwise
#   __CORE_HARNESS_TRIED    — interpreters attempted, for diagnostics
__core_harness_resolve_lib() {
  __CORE_HARNESS_LIB_DIR=""
  __CORE_HARNESS_TRIED=()
  local prog='import core_harness.hooks, sys; sys.stdout.write(str(core_harness.hooks.lib_path()))'

  # Repo-local virtualenv first (Issue #679). requirements.txt exact-pins
  # core-harness into <repo_root>/.venv, and that install is reachable from
  # this shim's own location even in panes that did NOT inherit VIRTUAL_ENV
  # or .venv/bin on PATH — runtime-spawned panes rebuild PATH through a
  # login shell and drop the venv, so the on-PATH `python3` cannot import
  # core_harness and every Bash call fails closed. Deriving <repo_root> from
  # $BASH_SOURCE keeps this independent of the caller's CWD. Trying the venv
  # first also keeps the healthy path at a single interpreter spawn (no perf
  # regression vs. the previous python3-first order, since the venv resolves
  # on the first attempt when present).
  local script_dir repo_root venv_py
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || script_dir=""
  if [[ -n "$script_dir" ]]; then
    repo_root="$(cd "$script_dir/../.." 2>/dev/null && pwd)" || repo_root=""
    if [[ -n "$repo_root" ]]; then
      for venv_py in "$repo_root/.venv/bin/python3" "$repo_root/.venv/Scripts/python.exe"; do
        if [[ -x "$venv_py" ]]; then
          __CORE_HARNESS_TRIED+=("$venv_py")
          # Accept only on a clean exit (assignment status == interpreter
          # status) AND non-empty output, so a noisy interpreter that writes
          # to stdout then fails does not masquerade as a resolved lib path.
          if __CORE_HARNESS_LIB_DIR="$("$venv_py" -c "$prog" 2>/dev/null)" && [[ -n "$__CORE_HARNESS_LIB_DIR" ]]; then
            return 0
          fi
        fi
      done
    fi
  fi

  # Detection order aligned with tools/journal_append.sh (cross-review
  # Minor 5): try the Windows `py -3` launcher first, then `python3`,
  # then `python`, so Git-for-Windows shells without python3 on PATH
  # still resolve the lib.
  if command -v py >/dev/null 2>&1; then
    __CORE_HARNESS_TRIED+=("py -3")
    if __CORE_HARNESS_LIB_DIR="$(py -3 -c "$prog" 2>/dev/null)" && [[ -n "$__CORE_HARNESS_LIB_DIR" ]]; then
      return 0
    fi
  fi
  local py
  for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
      __CORE_HARNESS_TRIED+=("$py")
      if __CORE_HARNESS_LIB_DIR="$("$py" -c "$prog" 2>/dev/null)" && [[ -n "$__CORE_HARNESS_LIB_DIR" ]]; then
        return 0
      fi
    fi
  done
  __CORE_HARNESS_LIB_DIR=""
  return 1
}

__core_harness_resolve_lib || true
if [[ -z "$__CORE_HARNESS_LIB_DIR" || ! -f "$__CORE_HARNESS_LIB_DIR/core_harness_hooks.sh" ]]; then
  echo "ブロック: core-harness パッケージが見つかりません (pip install -e . または <repo>/.venv の作成が必要)。試行した interpreter: ${__CORE_HARNESS_TRIED[*]:-none}" >&2
  exit 2
fi

# shellcheck source=/dev/null
source "$__CORE_HARNESS_LIB_DIR/core_harness_hooks.sh"
