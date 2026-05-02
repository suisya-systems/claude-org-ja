# Phase 4 (org-runtime) — completion note

**Date**: 2026-05-02
**Issue**: #129 (Phase 4 / Layer 2 = `claude-org-runtime` extraction)
**DoD**: Q11=b — in-tree `tools/` helpers replaced by the published
`claude-org-runtime` package.

## What landed

- `requirements.txt` now pins
  `claude-org-runtime @ git+https://github.com/suisya-systems/claude-org-runtime@v0.1.0`
  alongside the existing `core-harness` pin (the package is not on
  PyPI yet, so we install from the GitHub release tag, matching the
  core-harness pattern).
- `tools/ja_locale.json` carries the Japanese `LocaleConfig`
  override (`(なし)` constraints filler, the `dispatcher_runner.py`
  outbox-instruction header, etc.) consumed by
  `claude-org-runtime dispatcher delegate-plan --locale-json`.
- `tools/dispatcher_runner.py`, `tools/test_dispatcher_runner.py`,
  `tools/generate_worker_settings.py`, and
  `tests/test_generate_worker_settings.py` are removed; the
  runtime owns these state-machine/generator implementations and
  their tests.
- Skill / dispatcher / setup-doc references that previously invoked
  `python tools/dispatcher_runner.py ...` or
  `python tools/generate_worker_settings.py ...` now invoke
  `claude-org-runtime dispatcher delegate-plan ... --locale-json
  tools/ja_locale.json` and `claude-org-runtime settings generate
  ...` respectively.
- The dispatcher hook test (`tests/test-block-dispatcher-out-of-scope.sh`)
  was retargeted from the now-removed `tools/dispatcher_runner.py`
  path to `tools/check_role_configs.py` (still in-tree, still
  out-of-scope for dispatcher edits).

## What stayed

- `tools/org_extension_schema.json` (org-specific `roles.*` audit
  constraints + `worker_roles` templates) remains the in-tree SoT
  consumed by `tools/check_role_configs.py` and
  `tools/org_setup_prune.py`. These are ja-specific drift tools
  and were out of scope for the runtime extraction.
- `tools/check_role_configs.py`, `tools/check_renga_compat.py`,
  `tools/org_setup_prune.py`, `tools/journal_*`, `tools/pr_watch.*`
  remain in-tree per design Q5/Q11 (ja-specific tooling).

## Design decisions referenced

- Q1=c — wide extraction of dispatcher + generator at once.
- Q3=a — Python (no shell port).
- Q5=b — runtime ships an English `LocaleConfig`; `ja_locale.json`
  is the consumer-side override.
- Q8=a — runtime is the single source of truth for the helper
  implementations and the bundled `role_configs_schema.json`.
- Q11=b — DoD = in-tree replacement complete (this PR).

See `docs/internal/phase4-decisions-2026-05-02.md` for the full
Q&A record.

## Schema-equivalence check

Before deleting `tools/generate_worker_settings.py` we verified
that the in-tree (Step B `core_harness`-merged) generator and the
runtime's bundled `role_configs_schema.json` emit byte-identical
`settings.local.json` for every defined role (`default`,
`claude-org-self-edit`, `doc-audit`). The runtime's bundled
schema is the merged result, so adoption does not change worker
permissions.

## Known limitations / follow-ups

- The runtime ships its own bundled `role_configs_schema.json`,
  while `tools/check_role_configs.py` audits against the in-tree
  `tools/org_extension_schema.json`. Drift between the two is
  possible in principle. A follow-up PR (out of scope here) should
  pick one schema as the SoT or wire a CI check that diffs them on
  every runtime bump.
- `tools/ja_locale.json` is a flat `LocaleConfig` snapshot. If the
  runtime later adds locale-sensitive keys, ja must add them here
  as part of the bump verification.
- Real-fixture coverage for `delegate-plan` (situation matrices)
  remains a Lead-led follow-up per the design Q&A.
- The auto-mirror runtime work (#171) is unaffected by this PR but
  needs a re-scope decision now that the helper code lives in the
  runtime package.
