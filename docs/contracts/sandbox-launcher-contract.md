# Sandbox Launcher Contract (Phase 3 prerequisite)

> **Status**: **Design contract**, doc-only. This contract fixes the
> protocol surface around the bubblewrap (`bwrap`) launcher that consumes
> Claude Code's `sandbox.filesystem.*` block, so that subsequent
> implementation Issues can land case A (bootstrap fallback),
> `failIfUnavailable` re-definition, and the `sandbox_deny_skipped`
> observability event without dead code.
>
> **Scope**: This contract is a **settings-consumer protocol + observable
> behavior** specification. It is **not** a launcher *implementation*
> document. Phase 3 case E (`render_role_with_metadata` symlink-escape
> suppression) is already implemented in `claude-org-runtime` ≥0.1.4 and
> is treated here as upstream-given; this contract describes only the
> contract Claude Code's bwrap launcher must honor when it consumes the
> runtime's emitted profile, plus the feedback channel from the launcher
> back into the journal. Concretely, this contract pins:
>
> 1. Where the launcher *lives* and what the contract surface
>    encompasses ([`§1`](#1-launcher-placement-and-invocation-point)).
> 2. The schemas that flow across the runtime → launcher boundary and
>    the launcher → journal boundary ([`§2`](#2-launcher--runtime-boundary)).
> 3. The bootstrap-fallback algorithm (case A) — bwrap stderr classifier,
>    retry decision table, and the `sandbox_deny_skipped` event payload
>    ([`§3`](#3-bootstrap-fallback-specification-case-a)).
> 4. The re-defined semantics of `failIfUnavailable` and a role-by-role
>    fall-open allow table ([`§4`](#4-failifunavailable-re-semantics)).
> 5. How the launcher composes with Layer 2 (`permissions.deny`),
>    Layer 3 (sandbox), and Layer 4 (hooks) — including the case E vs
>    case A boundary ([`§5`](#5-interaction-with-existing-layers)).
> 6. The recommended split between a claude-org-ja PR and a
>    claude-org-runtime PR for the actual implementation
>    ([`§6`](#6-recommended-implementation-split)).
>
> **Method**: Empirical-first. Every claim is sourced either from a file
> in this repository (path + line range), the released
> `claude-org-runtime` API surface (function name + version), the
> `bubblewrap(1)` man page (option + observed stderr string), or a prior
> contract / design document that has already been ratified. Forward-
> looking statements (Phase 1 / case A wiring) are explicitly tagged
> **Prescribed (not yet implemented)**.
>
> **Empirical sources consulted**:
>
> - [`docs/sandbox-probe/phase3-bootstrap-policy-design.md`](../sandbox-probe/phase3-bootstrap-policy-design.md)
>   §4 (5-case option matrix), §5.1 (case E + case A采用), §5.2 (policy
>   requirements), §6 (residual risk).
> - [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
>   §1 (Layer 2 / 3 / 4 model), §1.3 (case E adaptive suppression), §3.1
>   (secretary), §3.2 (dispatcher), §3.3 (curator), §4 (worker rows).
> - [`docs/contracts/role-contract.md`](./role-contract.md) — role
>   boundary definitions; cited by §4.2 fall-open allow table.
> - [`docs/contracts/state-semantics-contract.md`](./state-semantics-contract.md)
>   §1.1 (state.db `events` table is the post-M4 SoT for journal
>   events).
> - [`docs/journal-events.md`](../journal-events.md) §"Adding a new
>   event type" (registration recipe), §"Observability" (table the new
>   event row joins), §"Reserved envelope" (`occurred_at` / `kind` /
>   `payload_json`).
> - [`docs/contracts/worker-git-guardrails-design.md`](./worker-git-guardrails-design.md)
>   — used as size precedent for a Phase-N design contract.
> - `claude-org-runtime` v0.1.4 / v0.1.6 (pin bump): public
>   `render_role_with_metadata()` returns a `RenderResult` describing
>   the post-suppression deny set. Version stream and pin:
>   [`requirements.txt`](../../requirements.txt) (≥0.1.6 at
>   time of writing).
> - `bubblewrap(1)` (Debian/Ubuntu package `bubblewrap`, version range
>   `0.5.x`–`0.10.x` in scope). Observed stderr strings cited in
>   [`docs/sandbox-probe/phase3-bootstrap-policy-design.md`](../sandbox-probe/phase3-bootstrap-policy-design.md)
>   §1, §2.
>
> **Refs**: Closes claude-org-ja#414. Parent epic: claude-org-ja#376.
> Phase 3 implementation parent (deferred items consume this contract):
> claude-org-ja#392. Phase 0 contract context:
> [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md).
> Phase 2 contract context (worker Git guardrails, similar in size and
> shape): [`docs/contracts/worker-git-guardrails-design.md`](./worker-git-guardrails-design.md).
> Runtime pin tracking: [`requirements.txt`](../../requirements.txt).
> Codex design review pre-applied (Blocker / Major findings folded into
> §1.2, §2.3, §3.3, §4, §5.3 below); see Issue #414 worker brief for the
> review summary.

---

## 1. Launcher placement and invocation point

### 1.1 Premise: Claude Code core is the launcher

The bwrap launcher is **inside Claude Code core**. Specifically:

- The released claude-org-ja repository does not ship a `tools/bwrap-launch.sh`
  or any in-tree program that takes a sandbox profile and executes
  `bwrap --bind ... -- <claude-binary>`. There is no such file in
  [`tools`](../../tools/), [`.dispatcher`](../../.dispatcher/), or
  [`.curator`](../../.curator/) at the time of this contract.
- The released `claude-org-runtime` package emits the
  `.claude/settings.local.json` `sandbox` block (Phase 1 schema with
  the per-role `sandbox` field) but does **not** itself fork-exec bwrap.
  Its public API
  ends at `render_role_with_metadata()`, which returns a Python
  `RenderResult` describing the post-suppression deny set. That return
  value is consumed by the settings-generator helper and serialized into
  JSON; the runtime never holds a bwrap process handle.
- Claude Code itself is the only process that *does* invoke bwrap: when
  it finds a `sandbox.filesystem.*` block in `.claude/settings.local.json`,
  it spawns the sandboxed work process under bwrap with mount/bind
  arguments derived from that block. This is part of Claude Code's
  built-in sandbox feature, not an in-org tool.

Therefore "the launcher" in this contract refers to the bwrap-invoking
component **inside Claude Code core**, treated here as a black-box
consumer of the `sandbox.filesystem.*` block. The contract is what that
consumer must do; it is not an instruction for an in-tree script.

### 1.2 Scope of this contract

Because the launcher lives in Claude Code core, this contract is
**necessarily a protocol contract, not an implementation contract**. It
fixes:

| Surface | Owner | What this contract pins |
|---|---|---|
| The shape of the `sandbox` block read by the launcher | claude-org-runtime emits it; Claude Code consumes it | Fields that the launcher MUST honor (`failIfUnavailable`, `denyRead`, `denyWrite`, `additionalDirectories`); fields that are advisory; case-A retry obligations on the consumer side. |
| The shape of feedback from the launcher | Claude Code core | The `sandbox_deny_skipped` journal event payload, severity / audience / dedupe-key fields, and the `/sandbox` status surface obligations. |
| The case-E vs case-A boundary | claude-org-runtime owns case E; Claude Code core owns case A | Which side suppresses which entries, and how each side records what it suppressed. |
| The role-by-role fall-open expectation | This contract | Per-role `failIfUnavailable` defaults and operator-warning thresholds. |

What this contract does **NOT** specify:

- Internal data structures the launcher uses to represent its retry
  state (those are private to Claude Code core).
- The exact bwrap argv it constructs. The contract binds the launcher
  to *behavior* (e.g., "after a transient bootstrap error the launcher
  MUST retry once with the offending entry suppressed") not to a
  specific argv recipe. Different bwrap versions may require different
  recipes; the recipe is implementation, the behavior is contract.
- Operating-system-level fallbacks (e.g. user namespaces availability,
  kernel-level seccomp). Those are bwrap concerns and out of scope here.
- Layer 1 (Claude Code's safety classifier). That layer is upstream
  and not configurable per role; see [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
  §1 for the explicit out-of-scope statement.

### 1.3 Per-role invocation chain

Every role in the org is started by spawning a Claude Code process under
a directory that has its own `.claude/settings.local.json`. The chain is
the same shape across roles; only the cwd, role name, and emitted
sandbox block differ. The path from a `claude` invocation to a
sandboxed work loop is:

```
operator (or dispatcher spawn_claude_pane)
  → claude CLI
    → reads <cwd>/.claude/settings.local.json
      → if `sandbox.filesystem` block present and bwrap available:
          → constructs bwrap argv from `additionalDirectories` /
            `denyRead` / `denyWrite`
          → exec bwrap → bwrap mounts namespaces → exec claude work loop
            inside the sandbox
      → else (no sandbox block, or bwrap unavailable + failIfUnavailable=false):
          → exec claude work loop directly (Layer 3 not enforced)
      → else (bwrap unavailable + failIfUnavailable=true):
          → fail-closed; claude exits with sandbox-required error
```

Per role:

| Role | cwd | Spawned by | settings template emitted by |
|---|---|---|---|
| Secretary | `<claude_org_path>/` | operator (manually `claude`) | `claude-org-runtime settings generate` (`roles.secretary` template) |
| Dispatcher | `<claude_org_path>/.dispatcher/` | operator at `/org-start`, then auto by `dispatcher_retro_gate` if pane lost | `roles.dispatcher` template |
| Curator | `<claude_org_path>/.curator/` | operator at `/org-start` | `roles.curator` template |
| Worker (any variant) | `<workers_dir>/<task_id>/` (Pattern A/B/C) or `<claude_org_path>/.worktrees/<task_id>/` (B-`live_repo_worktree`) | dispatcher via `delegate-plan` + `mcp__renga-peers__spawn_claude_pane` | `worker_roles[*]` template (default / claude-org-self-edit / doc-audit) |

The Phase 0 contract ([`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
§1.3) already records that the runtime emits the sandbox block per
role; this contract does not change which role gets a block, only what
the launcher does once it reads one.

### 1.4 Environment branches

The launcher behavior splits by host-environment capability. This
contract lists the three branches the launcher MUST handle distinctly;
each appears later in §3 (case A) and §4 (`failIfUnavailable` table):

| Environment | bwrap availability | Symlink-escape risk (case E) | Launcher action |
|---|---|---|---|
| Linux native (no WSL) | Available | Low (no `/mnt/c/...` style escape paths in default config) | Normal sandbox bootstrap; `denyRead` / `denyWrite` from runtime are honored as-is. |
| WSL2 | Usually available | High (`~/.aws`, `~/.ssh`, occasionally `~/.config/X` symlinked to `/mnt/c/Users/...`) | Runtime case-E suppresses some Layer 3 entries before the launcher sees them; launcher may also hit case A on residual entries. |
| Sandbox-absent (e.g., container without `bwrap` binary, or kernel without user namespaces) | Unavailable | n/a | Behavior depends on `failIfUnavailable`; see §4.1. |

Detection rules for case E (WSL + `realpath` escape) live in the
runtime; case A's environment detection is bwrap-runtime (the launcher
distinguishes "binary missing" from "binary failed at mount time" by
the bwrap exit shape, not by static probing).

---

## 2. Launcher ↔ runtime boundary

This section pins the schemas that cross the boundary in each
direction. It is deliberately written so that the runtime's
`RenderResult` and the launcher's `LauncherResult` are **independent
schemas** — to avoid pretending the boundary is bidirectional when it
is not.

### 2.1 Runtime → launcher: `sandbox` block in `.claude/settings.local.json`

The runtime writes a JSON object whose shape is fixed by Claude Code's
sandbox feature. The runtime-side derivation pipeline is:

```
worker_roles[role].sandbox  (per-role schema input)
  ↓  claude-org-runtime render_role_with_metadata(role, env)
RenderResult {
  emitted_settings: { ..., "sandbox": { "filesystem": {...}, "failIfUnavailable": bool } },
  suppressed_entries: [SuppressedEntry],   # case E (symlink escape)
  platform_metadata: { "platform": "wsl"|"linux", ... }
}
  ↓  settings generator helper writes emitted_settings to disk
.claude/settings.local.json   ← this is what the launcher reads
```

The serialized `sandbox` block uses the public Claude Code sandbox
schema and contains exactly the fields the launcher consumes:

```jsonc
{
  "sandbox": {
    "filesystem": {
      "additionalDirectories": [
        "<claude_org_path>",
        "<workers_dir>/<task_id>"
      ],
      "denyRead": [
        "/home/<user>/.aws/.env",
        "/home/<user>/.aws/credentials",
        "/home/<user>/.ssh/id_*"
      ],
      "denyWrite": [
        "/etc/**"
      ]
    },
    "failIfUnavailable": false
  },
  // optional advisory metadata produced by the runtime; the launcher MAY
  // read these for /sandbox status display but MUST NOT alter execution
  // behavior based on them
  "$comment": "platform=wsl, layer-3 entries suppressed: [~/.aws/**, ~/.ssh/**]"
}
```

**Required fields**:

- `sandbox.filesystem.additionalDirectories` — list of absolute paths
  the launcher MUST mount writable into the sandbox.
- `sandbox.filesystem.denyRead` — list of absolute path patterns the
  launcher MUST attempt to mount as deny-read entries (typically via
  `--bind /dev/null <target>`). Order does **not** convey priority.
- `sandbox.filesystem.denyWrite` — same shape, deny-write semantics
  (typically a read-only bind).
- `sandbox.failIfUnavailable` — boolean, default `false`. Re-defined in §4.1.

**Optional fields** the runtime may emit; the launcher MAY surface them
in `/sandbox` status but MUST NOT change retry behavior based on them:

- `$comment` — human-readable platform note. Format is fixed at
  `platform=<linux|wsl>, layer-3 entries suppressed: [<list>]` per
  case E §5.2(b) of [`docs/sandbox-probe/phase3-bootstrap-policy-design.md`](../sandbox-probe/phase3-bootstrap-policy-design.md).

**Forbidden in the consumer direction**: the runtime MUST NOT include
entry-level retry hints, attempt counters, or any field that asks the
launcher to do per-entry conditional logic beyond the standard bwrap
deny semantics. Case A fallback is the launcher's responsibility, not
the runtime's; pushing retry hints across this boundary would conflate
case E and case A (see §5.3).

### 2.2 Launcher → journal: `sandbox_deny_skipped` event

The launcher reports any entries it dropped (case A) by appending a
`sandbox_deny_skipped` event row to `.state/state.db`'s `events` table
via the writer wrappers ([`tools/journal_append.sh`](../../tools/journal_append.sh)
or [`tools/journal_append.py`](../../tools/journal_append.py)). The
state.db `events` table is the post-M4 single source of truth per
[`docs/contracts/state-semantics-contract.md`](./state-semantics-contract.md)
§1.1; the legacy `.state/journal.jsonl` file is migration-only and is
not appended to. The runtime case-E suppression also emits the same
`kind=sandbox_deny_skipped` event but with a distinct `phase` /
`source` field — the two emitters share an event vocabulary, not an
implementation.

The full payload schema is fixed in §3.3.

### 2.3 Schema separation: `RenderResult` ≠ `LauncherResult`

The runtime's `RenderResult` (in-memory Python value, internal to the
generator pipeline) and the launcher's `LauncherResult` (in-process
state inside Claude Code core, published only via journal events) are
**distinct schemas with no field-level coupling**. This separation is
the contract-level separation we adopt:

| Aspect | `RenderResult` (runtime) | `LauncherResult` (launcher, in-core) |
|---|---|---|
| Lifetime | One settings-generation cycle | One claude-process bootstrap |
| Visibility | Internal Python object; the only externalized projection is the `.claude/settings.local.json` file | Internal to Claude Code core; the only externalized projection is the `sandbox_deny_skipped` event(s) emitted to state.db |
| Suppression source it represents | case E (symlink-escape, `realpath`-based) | case A (bwrap bootstrap failure, stderr-based) |
| Mutability across attempts | Single-shot — emitted once and frozen | Updated across retry attempts within a single bootstrap |
| Fed back into the other side | **No.** The runtime never reads launcher state. The launcher never re-asks the runtime to re-render. | **No.** Runtime decisions are committed to disk before the launcher sees them. |

The launcher result schema (defined here purely as the contract surface
for what gets logged; the in-core representation is implementation):

```jsonc
LauncherResult := {
  "bootstrap_outcome": "success" | "partial_success" | "total_failure" | "skipped_no_bwrap",
  "attempts": [
    {
      "attempt_no": 1,
      "exit_code": 0,                        // bwrap exit; -1 if not yet exec'd
      "failed_entries": ["/home/<user>/.aws/.env"],
      "stderr_excerpt": "bwrap: Can't create file at /home/<user>/.aws/.env: ..."
    },
    {
      "attempt_no": 2,
      "exit_code": 0,
      "failed_entries": [],
      "stderr_excerpt": ""
    }
  ],
  "effective_deny_set": ["/home/<user>/.aws/credentials", "/home/<user>/.ssh/id_*"],
  "suppressed_entries": [
    {
      "entry": "/home/<user>/.aws/.env",
      "reason": "bwrap_bootstrap_failure",
      "bwrap_stderr_excerpt": "bwrap: Can't create file at ..."
    }
  ],
  "fail_if_unavailable": false,
  "fall_open": false   // true iff bootstrap_outcome ∈ {total_failure, skipped_no_bwrap} and failIfUnavailable=false
}
```

`bootstrap_outcome` values:

- `success` — All denyRead / denyWrite entries from the runtime were
  successfully mounted on attempt #1 (or attempt #2 after one retry).
- `partial_success` — One or more entries were dropped via case A after
  retries; the sandbox is up with the surviving deny set. The dropped
  entries appear in `suppressed_entries`.
- `total_failure` — bwrap could not start at all (e.g., `--unshare-pid`
  refused by the kernel). Subsequent fall-open behavior is governed by
  `failIfUnavailable` per §4.1.
- `skipped_no_bwrap` — bwrap binary not found on `$PATH`. Distinct from
  `total_failure` because the launcher never invoked bwrap.

### 2.4 What the runtime does NOT do (negative space)

To prevent contract drift, this list pins the things the runtime MUST
NOT do across this boundary. Each is grounded in a Codex Major
finding or §5.3's case E / case A boundary:

- The runtime MUST NOT write `sandbox_deny_skipped` events with
  `phase=case_a` / `source=bootstrap_retry`. Those are launcher-only.
  The runtime emits only `phase=case_e` / `source=render_suppression`
  events at settings-generation time (or during a startup probe — but
  not from inside a bootstrap retry loop).
- The runtime MUST NOT attempt to predict which entries bwrap will
  fail on at run time. case-E suppression operates only on
  realpath-detectable escape; everything else is left to the launcher.
- The runtime MUST NOT re-render the settings file in response to a
  launcher failure. If a bootstrap failure occurs, the operator (or a
  follow-up run of `claude-org-runtime settings generate`) updates the
  profile; the launcher does not feed back into the runtime in-process.

---

## 3. Bootstrap fallback specification (case A)

This section pins the algorithm Claude Code's launcher MUST follow when
bwrap returns a non-zero exit during sandbox setup. The algorithm is
written as a state machine plus two reference tables (stderr classifier
and retry decision). It is the contract surface for case A's
**post-launcher** behavior; case E's pre-launcher behavior lives in the
runtime and is referenced via §5.3.

### 3.1 bwrap stderr classifier

The launcher classifies bwrap stderr into one of three buckets. The
classifier is **content-based** (substring match on documented bwrap
error strings), not version-pinned, so that minor bwrap version drift
does not silently regress the matcher.

| Bucket | bwrap stderr pattern (substring, case-sensitive) | Origin |
|---|---|---|
| `transient_mount_failure` | `Can't create file at <path>` | bwrap fails to materialize a deny-target file when the parent path resolves outside the sandbox view (typical case: `~/.aws/.env` whose `~/.aws` is a symlink to `/mnt/c/Users/<user>/.aws` on WSL). Per [`docs/sandbox-probe/phase3-bootstrap-policy-design.md`](../sandbox-probe/phase3-bootstrap-policy-design.md) §1, §2. |
| `transient_mount_failure` | `Can't mount tmpfs on <path>` | bwrap fails to mount tmpfs over a wildcard-style deny target (typical case: `~/.aws/**` denyRead with WSL symlink). Per [`docs/sandbox-probe/phase3-bootstrap-policy-design.md`](../sandbox-probe/phase3-bootstrap-policy-design.md) §1. |
| `permanent_setup_failure` | `Failed to create new namespace` / `setting up uid map: Permission denied` / `clone failed: Operation not permitted` | Kernel / capability problem — retrying without an entry will not help. |
| `unknown_failure` | (no match) | Neither pattern matched. Treated as `permanent_setup_failure` for safety but logged with the full stderr excerpt so the catalog can grow. |

The classifier is the **only** state that depends on bwrap's stderr
shape. Adding a new bwrap version means adding new substring entries
to this table; the rest of the algorithm is shape-stable.

### 3.2 Retry decision table

```
For each bwrap attempt (1, 2, ...):
  → run bwrap with effective_deny_set
  → if exit == 0: bootstrap_outcome = success (or partial_success if
    attempt_no > 1 and entries were dropped on prior attempts), exit loop
  → else classify stderr:
      transient_mount_failure
        → identify offending entry from stderr (the absolute path
          mentioned after `Can't create file at` / `Can't mount tmpfs on`)
        → if offending entry not in effective_deny_set: log unknown_failure
          and treat as permanent_setup_failure
        → else: append to suppressed_entries with reason="bwrap_bootstrap_failure",
                drop entry from effective_deny_set
        → if attempt_no >= MAX_ATTEMPTS (=2): bootstrap_outcome =
          total_failure if effective_deny_set is empty AND original was
          non-empty, else partial_success
        → else: continue loop
      permanent_setup_failure / unknown_failure
        → bootstrap_outcome = total_failure (or skipped_no_bwrap if
          bwrap was not exec'd)
        → exit loop
```

`MAX_ATTEMPTS = 2`. Rationale: the only known transient failure mode
is per-entry path-resolution failure, and one round of pruning is
sufficient to drop all symlink-escape entries the runtime missed.
Higher retry budgets (the `5` proposed in [`docs/sandbox-probe/phase3-bootstrap-policy-design.md`](../sandbox-probe/phase3-bootstrap-policy-design.md)
§4.2 case B) were considered and rejected because the symptom set is
small and bounded; an unbounded retry would mask configuration errors.

After exiting the loop:

- If `bootstrap_outcome ∈ {total_failure, skipped_no_bwrap}`, consult
  `failIfUnavailable` per §4.1 to decide whether the claude work loop
  starts at all.
- If `bootstrap_outcome ∈ {success, partial_success}`, the claude work
  loop starts inside the now-running bwrap.

### 3.3 `sandbox_deny_skipped` event payload

Every entry that ends up in `LauncherResult.suppressed_entries` (case A)
or in the runtime's case-E suppression list MUST be emitted as a
`sandbox_deny_skipped` event row. The event is added to the
[`docs/journal-events.md`](../journal-events.md) "Observability" table
per the §"Adding a new event type" recipe; this contract pins its
payload shape.

**Event row** (registered in [`docs/journal-events.md`](../journal-events.md)
under §"Observability"):

```
| sandbox_deny_skipped | role, worker?, layer=layer_3, entry, reason,
phase=case_a|case_e, source=render_suppression|bootstrap_retry,
attempt, fail_if_unavailable, bwrap_exit, bwrap_stderr_excerpt,
severity, audience, dedupe_key, suppressed_by_default |
secretary | secretary, runtime, launcher | — |
A Layer-3 deny entry was skipped before or during bwrap startup. |
```

**Payload field schema**:

| Field | Type | Required? | Allowed values | Notes |
|---|---|---|---|---|
| `role` | string | yes | `secretary` / `dispatcher` / `curator` / `worker` | The role under which the launcher (or runtime) is operating. |
| `worker` | string | only when `role=worker` | `worker-<task_id>` | Identifies the per-worker task; absent for org roles. |
| `layer` | string | yes | `layer_3` | Always `layer_3` for now (the only layer this event covers). Future-reserved. |
| `entry` | string | yes | absolute path or glob | The denyRead / denyWrite entry that was skipped. |
| `reason` | string | yes | `symlink_escape` / `bwrap_bootstrap_failure` / `bwrap_unavailable` | Why the entry was skipped. `symlink_escape` is case-E only; `bwrap_bootstrap_failure` is case-A only; `bwrap_unavailable` is the no-bwrap branch. |
| `phase` | string | yes | `case_a` / `case_e` | Boundary marker; mirrors `source`. Must be present so consumers can filter without parsing `reason`. |
| `source` | string | yes | `render_suppression` (= case_e) / `bootstrap_retry` (= case_a) | Synonym of `phase` aimed at downstream consumers preferring the noun-style key. Both `phase` and `source` are required so that filtering remains unambiguous regardless of consumer preference. |
| `attempt` | int | yes | `0` (case_e, no bwrap attempt yet) / `1` / `2` | bwrap attempt number that produced the suppression, or `0` for case E. |
| `fail_if_unavailable` | bool | yes | — | The in-effect `sandbox.failIfUnavailable` setting at suppression time. |
| `bwrap_exit` | int | only when `phase=case_a` | bwrap exit code | Absent for `phase=case_e`. |
| `bwrap_stderr_excerpt` | string | only when `phase=case_a` | first 256 chars of bwrap stderr | Truncated to keep payload bounded; absent for `phase=case_e`. |
| `severity` | string | yes | `info` / `warning` / `error` | `info` = expected case-E suppression on a known WSL-style symlink. `warning` = case-A suppression that altered the deny set. `error` = bootstrap totally failed and fall-open occurred (one event per drop, plus one summary event with `entry="*"` `severity=error`). |
| `audience` | string | yes | `operator` / `debug` | `operator` = should surface in dashboards / `/sandbox` status. `debug` = noisy detail (e.g., per-entry case-E events on every spawn) intended for retro / curator scope. |
| `dedupe_key` | string | yes | `sha256(role + entry + reason + phase)` (lowercase hex, full digest) | Stable across spawns of the same role with the same suppression; allows dispatcher monitoring (§4.3) to count *unique* drops, not raw event lines. |
| `suppressed_by_default` | bool | yes | — | `true` = consumers SHOULD aggregate-count rather than display per-event (set on case-E events under steady state). `false` = consumers SHOULD surface to the operator. Mirrors `audience` but is consumer-facing, not source-facing. |

**Filter contract** (which consumers process which events):

- **Curator** (knowledge curation): `sandbox_deny_skipped` is **out of
  scope**. The curator's input is `knowledge/raw/`, not journal events
  in general; this event is observability state, not raw learning.
  [`docs/contracts/knowledge-curation-contract.md`](./knowledge-curation-contract.md)
  governs that scope and is not amended by this contract.
- **Dispatcher monitoring**: aggregate by `dedupe_key`, surface only
  when `severity ∈ {warning, error}`. Per-spawn case-E `info` events
  with `suppressed_by_default=true` MUST NOT be surfaced as anomalies.
  Detection of a new `dedupe_key` not seen in the previous 7 days
  qualifies as an `anomaly_observed` candidate ([`docs/journal-events.md`](../journal-events.md)
  §"Observability"); recurrence does not.
- **`/sandbox` status output**: list every entry currently in
  `LauncherResult.suppressed_entries` for the live process, plus the
  runtime's case-E suppression list (read from the runtime's
  `$comment` metadata in the live `.claude/settings.local.json`). The
  display is per-entry, not aggregated; aggregation is for monitoring
  only.

### 3.4 `/sandbox` status surface

When the operator runs `/sandbox` (Claude Code built-in command), the
launcher MUST display:

- Whether bwrap is in use for the current process.
- The full `additionalDirectories` set actually mounted.
- The `effective_deny_set` (post-case-A pruning) actually enforced.
- The `suppressed_entries` set (case A only; case E is suppressed
  before the launcher saw it but should still appear in a separate
  block read from the live settings file's `$comment`).
- The `failIfUnavailable` setting in effect.

The runtime MUST NOT participate in `/sandbox` rendering at run time;
its only contribution is the static `$comment` metadata it wrote at
generation time.

---

## 4. `failIfUnavailable` re-semantics

`failIfUnavailable` is a single boolean field on the `sandbox` block.
The Phase 0 contract treated it as "fail-closed if bwrap missing"; this
contract refines it to cover bootstrap failure and mount failure
distinctly, and adds a per-role expectations table.

### 4.1 Re-defined semantics

| `failIfUnavailable` | bwrap missing | bwrap exec'd → `permanent_setup_failure` | bwrap exec'd → `transient_mount_failure` (case A) |
|---|---|---|---|
| `true` | fail-closed (claude exits with sandbox-required error) | fail-closed | First retry per §3.2; if retry budget exhausts and `effective_deny_set` is empty (whole input pruned), fail-closed. If retry budget exhausts but partial success (some entries kept), warn + continue. |
| `false` (default) | fall-open: claude starts with no Layer 3 enforcement | fall-open | First retry per §3.2; on any outcome (success / partial / total_failure) the claude work loop starts; total_failure → fall-open with `severity=error` event. |

This preserves backward compatibility with the existing schema field
(`failIfUnavailable=true`/`false` still parses and still distinguishes
fail-closed from fall-open at the binary-missing point) while
clarifying the previously-undefined behavior at the bootstrap-failure
point. The Phase 0 contract referenced this as "round 3 §8 残課題 3"
in [`docs/sandbox-probe/phase3-bootstrap-policy-design.md`](../sandbox-probe/phase3-bootstrap-policy-design.md)
§5.2(d); this contract ratifies it.

### 4.2 Per-role fall-open allow table

The default for `failIfUnavailable` is `false`. Each role's emitted
profile MAY override that default. The expectations below are normative
for the runtime's role templates; deviations require a contract
amendment.

| Role | Default `failIfUnavailable` | Fall-open allowed? | Rationale |
|---|---|---|---|
| Secretary | `false` | Yes | Secretary runs in normal Claude Code permission mode (per-tool prompts); Layer 2 `permissions.deny` and operator judgment cover credentials even without Layer 3. Sandbox absence does not silently broaden the role's surface. |
| Dispatcher | **`true`** (override) | **No** | Dispatcher runs with `permission_mode=bypassPermissions` per [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md) §3.2, which makes Layer 2 a no-op. Sandbox absence + bypassPermissions = only Layer 4 hooks remain, and the hook chain has the §3.2.4 Bash-redirect carve-out. Fall-open here would mean credentials are reachable via `Bash(cat ~/.aws/...)`. The contract therefore overrides the default and requires `failIfUnavailable=true` so that the dispatcher refuses to start without bwrap. |
| Curator | `false` | Yes | Curator runs at `permission_mode=auto` with a near-empty allow list and a read-mostly task surface (knowledge/curated). Sandbox absence does not enable a new attack surface that Layer 2 + role discipline does not already cover. |
| Worker `default` | `false` | Yes | Worker has Layer 2 `permissions.deny` for credentials and Layer 4 hooks (`block-org-structure.sh`, `check-worker-boundary.sh`). Sandbox absence keeps Layer 2 + Layer 4 active. |
| Worker `claude-org-self-edit` | `false` (with operator-warning) | Yes-with-caveat | Self-edit role writes to `<claude_org_path>/.worktrees/<task_id>/`, so the blast radius is broader than a project worker. The default remains `false` for parity, but the runtime SHOULD emit an operator-visible advisory in `$comment` when it detects sandbox-absent + self-edit role; the dispatcher monitoring (§4.3) should treat the resulting fall-open `severity=error` event as a high-attention anomaly. |
| Worker `doc-audit` | `false` | Yes | doc-audit is read-only by role contract; sandbox absence does not change its writable surface (which is empty). |

### 4.3 Operator-visible warning + drift detection

Beyond the per-event journal, the contract requires three
operator-visible surfaces:

- **`/sandbox` status block** — see §3.4. Lists fall-open state and
  suppressed entries explicitly.
- **Dispatcher monitoring** — when an `anomaly_observed` candidate is
  identified per §3.3 filter contract (severity ≥ warning, new
  `dedupe_key` not seen in 7 days), the dispatcher emits the standard
  `anomaly_observed` event and notifies the secretary per
  [`docs/journal-events.md`](../journal-events.md) §"Observability".
  This is the **only** surface where new sandbox suppressions get
  surfaced in real time; in steady state, repeated case-E suppressions
  with the same `dedupe_key` are intentionally silent.
- **Drift CI hook** — a contract-level expectation, **prescribed (not
  yet implemented)**: a CI job running on PRs that touch
  [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json)
  or [`requirements.txt`](../../requirements.txt) MUST run
  `claude-org-runtime settings generate` against a synthetic WSL
  fixture and assert that the case-E suppression set is non-empty for
  worker-role profiles that include `~/.aws/**` denyRead. This catches
  the regression where the runtime stops emitting `$comment`
  metadata. The CI wiring is part of the case-A implementation work,
  not part of this contract.

---

## 5. Interaction with existing layers

This section pins how the launcher composes with Layer 2
(`permissions.deny`), the rest of Layer 3 (sandbox), and Layer 4
(hooks). The composition rules already exist in
[`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
§1; this contract adds the launcher-specific edges.

### 5.1 Layer 2 — out of scope for the launcher

`permissions.allow` / `permissions.deny` are enforced inside Claude
Code core's tool-invocation classifier, **before** any bwrap process is
considered. They never appear in the bwrap argv and are not consumed by
the launcher. This contract therefore makes no claim about Layer 2
behavior; the canonical reference is
[`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
§1, and any change to Layer 2 is governed there.

The one cross-layer guarantee this contract assumes is the §1.3 "always
emit Layer 2 fallback" rule from [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md):
when the runtime suppresses a Layer 3 entry (case E) or the launcher
drops one (case A), the corresponding Layer 2 deny remains in effect
because Layer 2 is unaffected by sandbox bootstrap. This is what makes
the per-role fall-open allowance in §4.2 acceptable for all roles
*except* the dispatcher (whose `bypassPermissions` mode disables
Layer 2 entirely).

### 5.2 Layer 3 — the launcher's domain

The launcher consumes `sandbox.filesystem.*` and is the sole surface
that turns those fields into bwrap mounts. The contract surface here is
covered by §2 (input schema) and §3 (case-A behavior).

### 5.3 case E vs case A boundary

This is the single most important boundary in this contract. Both cases
suppress Layer 3 entries; they differ in **when** and **who**:

```
                    .claude/settings.local.json
                    (post-case-E suppression)
                              │
runtime side  ───────────────┤├─────────────── launcher side
(case E)                     ││                (case A)
                             ││
realpath-based               ││                bwrap-stderr-based
resolution at                ││                resolution at run time
generation time              ││                inside Claude Code core
                             ││
RenderResult                 ││                LauncherResult
suppressed_entries           ││                suppressed_entries
                             ││
sandbox_deny_skipped         ││                sandbox_deny_skipped
phase=case_e                 ││                phase=case_a
source=render_suppression    ││                source=bootstrap_retry
attempt=0                    ││                attempt ∈ {1, 2}
severity=info                ││                severity ∈ {warning, error}
audience=debug               ││                audience=operator
```

**Pre-launcher (case E)**:

- The runtime's `render_role_with_metadata()` calls `os.path.realpath()`
  on each Layer 3 entry. If the resolved path escapes the sandbox view
  (typically `/mnt/c/...` on WSL2), the entry is suppressed from the
  emitted JSON.
- The runtime emits the case-E `sandbox_deny_skipped` event with
  `phase=case_e`, `source=render_suppression`, `attempt=0`,
  `severity=info`, `audience=debug`, `suppressed_by_default=true`.
- This is implemented and shipping in `claude-org-runtime` ≥0.1.4.
- Case E is a *steady-state* property of the WSL host; the suppressed
  set is stable across spawns of the same role and is therefore
  emitted as `info` / `audience=debug`. Dispatcher monitoring counts
  it once per `dedupe_key` and goes quiet.

**Post-launcher (case A)**:

- Claude Code core invokes bwrap with the post-case-E deny set. If
  bwrap returns a `transient_mount_failure` (§3.1), the launcher
  classifies the offending entry, drops it, and retries once.
- The launcher emits a case-A `sandbox_deny_skipped` event per dropped
  entry, with `phase=case_a`, `source=bootstrap_retry`,
  `attempt={1,2}`, `severity=warning` (or `error` if `effective_deny_set`
  ended empty), `audience=operator`, `suppressed_by_default=false`.
- This is the **prescribed behavior** that this contract authorizes;
  the case-A implementation work realizes the launcher side (or, if
  Claude Code core already implements bwrap retry semantically
  equivalent to §3.2, this contract documents the consumer expectation
  that the `sandbox_deny_skipped` events are emitted in the shape §3.3
  pins).

**Forbidden cross-coupling**:

- The runtime MUST NOT produce `phase=case_a` events. The launcher
  MUST NOT produce `phase=case_e` events.
- The launcher MUST NOT extend `RenderResult` (the runtime's in-process
  data structure). Its only handle on the runtime side is the on-disk
  settings file plus the optional `$comment` metadata.
- `RenderResult.suppressed_entries` and
  `LauncherResult.suppressed_entries` are not the same set. They are
  disjoint by construction: case E suppresses entries the launcher
  never sees; case A suppresses entries the launcher saw and bwrap
  rejected. Aggregating them at the dashboard MUST distinguish by
  `phase`.

### 5.4 Layer 4 — out of scope for the launcher

Layer 4 hooks are PreToolUse hook scripts that run inside the live
claude work loop. They run *after* the sandbox is already up and
therefore have no contract surface on the launcher. The launcher's
output (`bootstrap_outcome`, `effective_deny_set`) is not visible to
hooks.

The dispatcher's case for `failIfUnavailable=true` (§4.2) rests on the
fact that Layer 4 hooks alone are an insufficient defense when Layer 2
is no-op. This is a consequence of [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
§3.2.4 (Bash-redirect carve-out), not a Layer 4 fact this contract
introduces.

---

## 6. Recommended implementation split

This contract is doc-only. The implementation work it unblocks is
expected to land as 1–2 follow-up PRs. The split below is a
recommendation, not a contract requirement; the actual scoping decision
belongs to the implementing worker(s) on the case-A follow-up.

### 6.1 claude-org-ja PR (primary)

Scope, in order of dependency:

1. Add `sandbox_deny_skipped` to [`docs/journal-events.md`](../journal-events.md)
   Observability table per the §"Adding a new event type" recipe, with
   the §3.3 payload schema rendered into the table row. (Done in this
   contract's PR; see §3.3 above.)
2. Implement (or wire into Claude Code core's existing wiring, if
   already present) the case-A retry algorithm per §3.2. This is the
   only side that requires touching Claude Code core; if Claude Code
   already retries semantically equivalently, this step degenerates
   into "verify the existing behavior matches §3.2 and document the
   match".
3. Wire the `sandbox_deny_skipped` emit path through
   [`tools/journal_append.sh`](../../tools/journal_append.sh) /
   [`tools/journal_append.py`](../../tools/journal_append.py). Per
   [`docs/journal-events.md`](../journal-events.md) §"Adding a new
   event type", direct INSERTs are forbidden; the helper is the only
   sanctioned writer. The launcher invokes the helper as a post-bwrap
   shell call (one event per suppressed entry).
4. Update [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
   §1.3 prose to cite this contract's §5.3 boundary explicitly (the
   current language already names case E; case A becomes a sibling
   subsection).
5. Add a smoke test under [`docs/sandbox-probe`](../sandbox-probe/)
   that exercises the case-A retry on a synthetic dangling-symlink
   fixture. Pure-unit tests should cover stderr classification, retry
   decision, payload validation, and the `failIfUnavailable` matrix
   (§3.1, §3.2, §3.3, §4.1); WSL/bwrap smoke tests cover only end-to-end
   integration. (See §6.3 for the full test strategy.)

### 6.2 claude-org-runtime PR (companion, optional)

Scope:

1. **Verify only**: confirm that `render_role_with_metadata()` already
   emits the case-E suppression metadata in the form §2.1 expects. If
   it does (released in 0.1.4 / pinned at 0.1.6), no runtime change is
   needed.
2. If a change is needed: tighten `RenderResult.suppressed_entries`
   into the schema this contract expects, and add the
   `sandbox_deny_skipped` `phase=case_e` emit at settings-generation
   time. This must NOT extend `RenderResult` to carry launcher-side
   state — see §2.4.

### 6.3 Test strategy

Per the Codex review (Minor / Nit findings folded in):

- **Pure unit tests** are the primary surface. Coverage targets:
  - bwrap stderr classifier (§3.1) — feed each documented stderr
    string + a corpus of unknown strings, assert bucket assignment.
  - Retry decision table (§3.2) — table-driven test over (attempt_no,
    bucket, effective_deny_set_size) combinations; assert
    `bootstrap_outcome` + `suppressed_entries` shape.
  - Payload validation (§3.3) — emit one of every required field
    combination, assert it parses against the journal helper's schema
    check.
  - `failIfUnavailable` matrix (§4.1) — table over (`failIfUnavailable`,
    bwrap state, retry outcome) with expected fall-open / fail-closed.
- **WSL / bwrap smoke tests** are the secondary surface. One end-to-end
  test per environment (Linux native + WSL with `~/.aws → /mnt/c/...`)
  asserting that the case-A path actually fires on WSL and does not
  fire on Linux native. These are slow and host-dependent; keep them
  out of the per-commit CI loop.

### 6.4 Backward compatibility

This contract preserves all on-disk schema fields:

- `sandbox.failIfUnavailable` keeps its boolean shape and its existing
  binary-missing semantics (§4.1 column 1).
- `sandbox.filesystem.{additionalDirectories,denyRead,denyWrite}` keep
  their list-of-strings shape and their bwrap-mount semantics.
- The runtime's `RenderResult` is unchanged in this contract; case-E
  is already implemented.

The only on-disk addition is the new `sandbox_deny_skipped` event row
in the `events` table, which is a strictly additive change to the
journal vocabulary per [`docs/journal-events.md`](../journal-events.md)
§"Adding a new event type" (existing readers tolerate unknown event
kinds gracefully).

---

## 7. Decision rationale digest

1. **Launcher is in Claude Code core, not in-tree (§1.1)** — Searched
   the repo (`tools/`, `.dispatcher/`, `.curator/`) and found no bwrap
   invoker. The `claude-org-runtime` API ends at `RenderResult`. The
   only candidate is Claude Code's built-in sandbox feature, so the
   contract is a consumer-side protocol, not an in-tree script spec.
2. **Schema separation `RenderResult` ≠ `LauncherResult` (§2.3)** —
   Pretending the boundary is bidirectional would smuggle launcher
   state into the runtime and would invite re-render-on-failure logic,
   which is the wrong layer.
3. **`MAX_ATTEMPTS = 2` (§3.2)** — Phase 3 design considered up to 5
   retries; this contract picks 2 because the only known transient
   case is symlink-escape and one prune is sufficient. Higher budgets
   mask config errors and inflate cold-start latency.
4. **Dispatcher fall-open NOT allowed (§4.2)** —
   `bypassPermissions` makes Layer 2 a no-op; without Layer 3 the
   dispatcher has only Layer 4 hooks, which have the Bash-redirect
   carve-out. The default override is the contract-level fix.
5. **`sandbox_deny_skipped` payload severity / audience / dedupe_key
   (§3.3)** — Without `severity` / `audience` filtering, dispatcher
   monitoring would alert on every spawn of every WSL machine; without
   `dedupe_key`, monitoring would over-count.
6. **case E pre-launcher / case A post-launcher (§5.3)** —
   The two suppression sources are disjoint by construction;
   co-mingling them in monitoring or in `RenderResult` would lose the
   ability to tell "expected WSL steady-state" from "new bootstrap
   regression".
7. **state.db `events` table is SoT, not `.state/journal.jsonl`** —
   Per [`docs/contracts/state-semantics-contract.md`](./state-semantics-contract.md)
   §1.1 + [`docs/journal-events.md`](../journal-events.md) header.
   The legacy jsonl file was retired at M4. This contract therefore
   wires `sandbox_deny_skipped` only through the journal-helper CLI
   (`bash tools/journal_append.sh sandbox_deny_skipped ...`), never
   through direct file appends or direct DB INSERTs.
