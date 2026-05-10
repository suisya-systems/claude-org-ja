# Sandbox-intent semantic drift fixtures

Inputs for the semantic golden drift check in
[`tools/check_runtime_schema_drift.py`](../../../../tools/check_runtime_schema_drift.py).
Each fixture pairs a small input schema fragment with the expected
`SandboxMetadata.to_jsonable()` (the "explain JSON") output of
`claude_org_runtime.settings.generator.render_role_with_metadata()`.
The byte-identical schema check (existing path) catches structural
divergence; this semantic check catches behavioural divergence in the
suppression evaluator on top of identical schema bytes.

## Fixture format

```jsonc
{
  "description": "what this fixture exercises",
  "inputs": {
    "role": "...",                // required, key inside schema's worker_roles or roles
    "role_kind": "worker"|"org",  // optional, defaults to "worker"
    "worker_dir": "...",          // required absolute path
    "claude_org_path": "...",     // required absolute path
    "base_clone": "...",          // optional, Pattern B placeholder
    "task_id": "...",             // optional, Pattern B placeholder
    "branch_ref": "...",          // optional, Pattern B placeholder
    "pattern": "A"|"B"|"C"|null,  // optional, informational only
    "wsl_detected": true|false,   // stub for runtime's wsl_detector
    "realpath_map": [             // fake realpath rules; first match wins
      {"prefix": "/some/path", "replacement": "/another/path"}
    ],

    // Choose exactly one of the next two — the loader rejects setting
    // both, and rejects setting neither.
    "schema_fragment": { ... },   // (a) inline mini-schema dict
    "schema_source": "shipped",   // (b) load tools/org_extension_schema.json

    "home_dir": "/H"              // optional; required if any deny entry uses
                                  //   anchor:"home". Swaps $HOME/$USERPROFILE
                                  //   for the duration of the render so
                                  //   os.path.expanduser('~') is deterministic.
  },
  "expected_explain": { ... }     // SandboxMetadata.to_jsonable() output
}
```

`realpath_map` rules apply to a path `p` when `p == prefix` or
`p.startswith(prefix + "/")`. The matched prefix is replaced and the
result is returned. Paths that match no rule pass through unchanged.

### `schema_source: "shipped"` vs inline `schema_fragment`

- Use `schema_fragment` when the fixture's purpose is to exercise a
  specific evaluator path with a minimal, hand-rolled mini-schema —
  the original sandbox_default / sandbox_doc_audit /
  sandbox_pattern_b_self_edit fixtures use this form.
- Use `schema_source: "shipped"` when the fixture's purpose is to
  pin behaviour against the *actual concrete sandbox body* that ships
  in [`tools/org_extension_schema.json`](../../../../tools/org_extension_schema.json).
  The Phase 1 PR3 `role_secretary.json` / `role_dispatcher.json` /
  `role_curator.json` fixtures use this form so the concrete bodies
  for the three org roles are verified, not just the evaluator. A
  hand-rolled mini-schema cannot detect a regression in the shipped
  body; the shipped form can.

### `home_dir` and `anchor: "home"`

The runtime resolves `anchor: "home"` via `os.path.expanduser("~")`,
which on POSIX reads `$HOME` (Windows: `$USERPROFILE`) — host-dependent
by default. Setting `inputs.home_dir = "/H"` (or any stable path) makes
the loader temporarily swap those env vars for the duration of the
render so home-anchored entries produce a byte-portable
`expected_explain`. Restore-on-finally is unconditional: a stray
exception cannot leak the fake `$HOME` into other tests.

Fixtures that do not use `anchor: "home"` should omit `home_dir`.

## Out-of-scope intentionally

- **`verification_depth`**: this is a delegate-payload / brief
  convention surfaced via `tools/gen_delegate_payload.py`, not a
  sandbox enforcement dimension. The renderer's
  `render_role_with_metadata()` does not branch on it and the explain
  JSON does not include it. Fixtures here MUST NOT add a
  `verification_depth` field to either `inputs` or
  `expected_explain` — depth is enforcement-out-of-scope by design.
- **Concrete sandbox bodies for `worker_roles.*`**: not landed in the
  Phase 1 PR3 changeset; the worker_roles A/B/C pattern variation
  needs a `sandbox_by_pattern` schema decision (deferred to PR4 per
  the Codex pre-design review). The three `role_secretary` /
  `role_dispatcher` / `role_curator` fixtures pin the org-side bodies
  added in PR3. `worker_roles.default` / `claude-org-self-edit` /
  `doc-audit` shipped-body fixtures are tracked for a later changeset.

## Adding a new fixture

1. Drop the JSON file into this directory.
2. Run `python tools/check_runtime_schema_drift.py --semantic` and
   confirm it prints OK. Mismatches print a unified diff between the
   committed `expected_explain` and the runtime's actual output.
3. The pytest companion at
   [`tests/test_runtime_schema_drift_semantic.py`](../../../test_runtime_schema_drift_semantic.py)
   discovers fixtures by glob, so no test wiring is needed.
