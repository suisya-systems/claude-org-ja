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
    "role": "...",                // required, key inside schema_fragment.worker_roles or schema_fragment.roles
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
    "schema_fragment": { ... }    // a minimal schema dict the renderer can load
  },
  "expected_explain": { ... }     // SandboxMetadata.to_jsonable() output
}
```

`realpath_map` rules apply to a path `p` when `p == prefix` or
`p.startswith(prefix + "/")`. The matched prefix is replaced and the
result is returned. Paths that match no rule pass through unchanged.

## Out-of-scope intentionally

- **`verification_depth`**: this is a delegate-payload / brief
  convention surfaced via `tools/gen_delegate_payload.py`, not a
  sandbox enforcement dimension. The renderer's
  `render_role_with_metadata()` does not branch on it and the explain
  JSON does not include it. Fixtures here MUST NOT add a
  `verification_depth` field to either `inputs` or
  `expected_explain` — depth is enforcement-out-of-scope by design.
- **`anchor: "home"`**: skipped to keep fixtures host-independent.
  `os.path.expanduser("~")` resolves against the running user's
  `HOME` env var and would make goldens differ across machines.
- **Concrete sandbox bodies for shipped roles**: the `schema_fragment`
  in each fixture is a self-contained minimal example; it does not
  need to match the in-tree
  [`tools/org_extension_schema.json`](../../../../tools/org_extension_schema.json)
  contents. Concrete bodies for `secretary` / `dispatcher` / `curator`
  / worker `default` (A/B/C) / `claude-org-self-edit` / `doc-audit`
  are added in a later changeset.

## Adding a new fixture

1. Drop the JSON file into this directory.
2. Run `python tools/check_runtime_schema_drift.py --semantic` and
   confirm it prints OK. Mismatches print a unified diff between the
   committed `expected_explain` and the runtime's actual output.
3. The pytest companion at
   [`tests/test_runtime_schema_drift_semantic.py`](../../../test_runtime_schema_drift_semantic.py)
   discovers fixtures by glob, so no test wiring is needed.
