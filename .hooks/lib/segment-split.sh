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
__core_harness_resolve_lib() {
  # Detection order aligned with tools/journal_append.sh (cross-review
  # Minor 5): try the Windows `py -3` launcher first, then `python3`,
  # then `python`, so Git-for-Windows shells without python3 on PATH
  # still resolve the lib.
  if command -v py >/dev/null 2>&1; then
    py -3 -c 'import core_harness.hooks, sys; sys.stdout.write(str(core_harness.hooks.lib_path()))' 2>/dev/null && return 0
  fi
  local py
  for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
      "$py" -c 'import core_harness.hooks, sys; sys.stdout.write(str(core_harness.hooks.lib_path()))' 2>/dev/null && return 0
    fi
  done
  return 1
}

__CORE_HARNESS_LIB_DIR="$(__core_harness_resolve_lib || true)"
if [[ -z "$__CORE_HARNESS_LIB_DIR" || ! -f "$__CORE_HARNESS_LIB_DIR/core_harness_hooks.sh" ]]; then
  echo "ブロック: core-harness パッケージが見つかりません (pip install -r requirements.txt が必要)。" >&2
  exit 2
fi

# shellcheck source=/dev/null
source "$__CORE_HARNESS_LIB_DIR/core_harness_hooks.sh"
