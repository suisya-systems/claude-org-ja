# Contract Set D — Backend Interface

> **Status**: Ratified (2026-05-03). Lead-confirmed decisions for all 12 open questions. This contract defines the abstract backend surface that the `claude-org` harness depends on; renga 0.18.0+ is the reference implementation, but any backend meeting the surface specified here is permitted.
>
> **Scope**: Phase 1 Contract Set D only. Sets A (roles), B (delegation lifecycle), C (state), and E (knowledge) are tracked in #121 / #122 / #124 / #125 and out of scope here.
>
> **Subject**: Set D defines the abstract API that any backend must provide for `claude-org` to operate. Today the only implementation is **renga** (`mcp__renga-peers__*` MCP server, renga 0.18.0+). This contract documents what the harness *requires*, not what renga *happens to provide* — alternative backends are permitted as long as they meet the surface specified here.
>
> **Method**: Each surface section below is filled from empirical sources (see list below). Sentences sourced from current behavior are written as facts. Open design questions are marked inline.
>
> **Empirical sources consulted**:
> - `mcp__renga-peers__*` MCP tool list (live tool definitions in the current Claude Code session — the normative source of truth for the operations and parameters renga exposes today)
> - `.claude/skills/org-start/SKILL.md` (which backend calls are required for bootstrap; spawn / send_keys / list_peers / send_message sequence)
> - `.claude/skills/org-delegate/SKILL.md` Step 3 (worker spawn, balanced split, dev-channel approval, list_peers wait, instruction send)
> - `.claude/skills/org-delegate/references/renga-error-codes.md` (existing error-code vocabulary and event-stream semantics)
> - `.dispatcher/CLAUDE.md` (poll_events / check_messages / list_panes / inspect_pane / send_keys / close_pane usage in the watch loop and CLOSE_PANE handler)
> - `docs/contracts/role-contract-outline.md` (Set A — used as structural template)
> - renga README / public docs at https://github.com/suisya-systems/renga (for reference, not normative — Set D is the harness's REQUIREMENT, renga is one provider)
>
> **Refs**: #123 (this issue), parent epic #101.

---

## Surface 1: Pane control

The backend MUST expose primitives to spawn, enumerate, identify, and close "panes" (process-bearing rectangles in a tiling terminal). Per the Lead-ratified decisions: `inspect_pane`, `send_keys`, and `list_panes` geometry are REQUIRED; the per-runtime spawn helpers (`spawn_claude_pane`, `spawn_codex_pane`) and a graceful-exit operation are OPTIONAL (backends may provide them, but harnesses must be able to drive a generic spawn with flag injection); `focus_pane` and `new_tab` are user-affordance surfaces that backends SHOULD provide but the harness does not depend on for correctness. All operations are scoped to a single tab (see Surface 4 — Identity & addressing).

### 1.1 spawn (generic)

- **Operation**: split an existing pane to create a new one.
- **Inputs**:
  - `direction`: `"vertical"` (side-by-side) | `"horizontal"` (top/bottom). REQUIRED.
  - `target`: pane identifier (numeric id, stable name, or the literal `"focused"`). Default `"focused"`. All-digit strings are interpreted as numeric ids (see Surface 4).
  - `cwd`: optional working directory. Absolute paths used verbatim; relative paths resolved against the caller pane's cwd. Validated before any layout mutation (no half-mutated state on `cwd_invalid`).
  - `name`: optional stable id for later addressing. Subject to validation rules in Surface 4.
  - `role`: optional free-form label, surfaced on `list_panes` / `list_peers`.
  - `command`: optional shell command run once the shell is ready.
- **Outputs**: numeric id of the new pane.
- **Error codes** (see Surface 6): `split_refused`, `cwd_invalid`, `pane_not_found`, `name_in_use`, `name_invalid`, `invalid-params`.
- **Idempotency**: none. Re-issuing the call creates another pane.

### 1.2 spawn (Claude Code convenience)

- **Operation**: split + launch a Claude Code instance with the peer channel enabled.
- **Inputs**: `direction`, `target`, `cwd`, `name`, `role` as in 1.1, plus structured `permission_mode`, `model`, and `args[]`. The backend MUST reject `args[]` containing `--dangerously-load-development-channels`, `--permission-mode`, or `--model` with `invalid-params` (forces the structured fields).
- **Outputs**: numeric pane id.
- **Required behavior**: the backend MUST inject `--dangerously-load-development-channels server:<channel-name>` so the new Claude joins the peer network without the caller synthesizing the flag.
- **Required-vs-optional**: OPTIONAL. The contract requires the *end behavior* — a Claude pane spawned with the peer-channel flag injected — but does not require a backend-side spawn-Claude convenience operation. Backends MAY provide one (renga does); harnesses driving a backend without one MUST inject `--dangerously-load-development-channels server:<channel-name>` themselves via the generic Surface 1.1 spawn `command` parameter. The same logic applies to the Codex pull-peer helper in 1.3.
- **Implementation note**: the current harness skills (`org-start`, `org-delegate`) are written against renga and exclusively use `spawn_claude_pane` today. A backend without the helper would require the harness skills to be updated to take the generic-spawn path; that update is a harness-side change, not a contract violation.

### 1.3 spawn (Codex convenience)

- **Operation**: split + launch a Codex instance pre-registered as a pull-based peer.
- **Inputs**: `direction`, `target`, `cwd`, `name`, `role`, plus pass-through `args[]` (one logical token per array entry).
- **Outputs**: numeric pane id.
- **Required-vs-optional**: OPTIONAL, on the same basis as 1.2. Backends MAY provide a Codex-specific helper (renga does); harnesses driving a backend without one MUST drive Codex registration via the generic Surface 1.1 spawn `command` parameter, plus Surface 2.3 `check_messages`-style pull semantics. The harness's normal flow (`org-start`, `org-delegate`) does not currently spawn Codex peers; the surface is documented for completeness and for use in operator-driven workflows.
- **Error codes**: same as 1.1 / 1.2 (`split_refused`, `cwd_invalid`, `pane_not_found`, `name_in_use`, `name_invalid`, `invalid-params`).

### 1.4 close

- **Operation**: terminate the pane's process and remove it.
- **Inputs**: `target` (id, stable name, or `"focused"`).
- **Outputs**: confirmation text (renga: `"Closed pane id=N."`); a single lifecycle event (`pane_exited`) MUST be emitted exactly once per successful close.
- **Error codes**: `pane_not_found`, `pane_vanished`, `last_pane`.
- **Required-vs-optional**: the `close` operation itself is REQUIRED (the harness's `org-delegate` teardown and `org-suspend` flow both depend on `close_pane`). What is OPTIONAL is a *separate* "request-graceful-exit" backend operation; process-kill close (the current renga shape) plus pane-driven self-`exit` for graceful shutdown is sufficient. The contract does NOT require a dedicated graceful-exit op alongside `close`.

### 1.5 list_panes

- **Operation**: enumerate every pane in the current tab.
- **Outputs**: per-pane records containing `id`, optional `name`, optional `role`, `focused` flag, terminal geometry (`x`, `y`, `width`, `height` in cell units), `cwd`, optional `summary` (see 2.4), and when known the peer client kind / receive mode (push vs poll).
- **Required for**: balanced-split target selection (`org-delegate` Step 3-1), reconciliation of missed lifecycle events (`.dispatcher/references/worker-monitoring.md` watch-loop Step 3), bootstrap identity verification (`org-start` Step 0.3).
- **Visibility scope**: current tab only. Panes in other tabs MUST NOT appear.
- **Required-vs-optional**: REQUIRED. The backend MUST expose `x` / `y` / `width` / `height` in cell units on every `list_panes` record. The harness's balanced-split scheduling depends on it; a backend without geometry would require an entirely different scheduling strategy and the harness cannot operate against such a backend without contract amendment.

### 1.6 focus

- **Operation**: move keyboard focus to another pane in the current tab.
- **Inputs**: `target`.
- **Constraints**: focus changes are user-visible and disruptive — the harness uses this sparingly. Not load-bearing for correctness; available for human-affordance flows.
- **Required-vs-optional**: backends SHOULD provide `focus_pane` for human-affordance, but the contract does NOT make it strictly REQUIRED — no harness correctness flow depends on it. Harnesses MUST tolerate its absence by simply not invoking it.

### 1.7 inspect_pane

- **Operation**: snapshot the visible screen of another pane (grid scrape).
- **Inputs**: `target`, optional `lines` (trim to bottom N rows), optional `format` (`"text"` | `"grid"`), optional `include_cursor`.
- **Outputs**: rendered screen text and/or structured grid (`{lines: [{row, text}], cursor?: {visible, row, col}}`).
- **Used for**: independent observation of approval prompts and error banners by the dispatcher's watch loop, completion-state read-back during retro (`.dispatcher/references/worker-monitoring.md` Step 4 (a)–(g)).
- **Required-vs-optional**: REQUIRED. Independent grid-scrape observation is foundational to the dispatcher's confidence-graded notification model (cross-checking worker self-reports against observable screen state). A backend that exposes only logical messaging — forcing the harness onto self-report-only — is insufficient for the safety guarantees Set A and Set B depend on.

### 1.8 set_pane_identity

- **Operation**: rename or reassign `name` and/or `role` of an existing pane.
- **Inputs**: `target`, optional `name` (string | null | omit — three-state), optional `role` (same).
- **Outputs**: updated pane record.
- **Validation**: name MUST NOT be empty, all-digits, or collide with another pane in this tab; allowed characters `[A-Za-z0-9_-]`. Role has no uniqueness constraint.
- **Error codes**: `name_in_use`, `name_invalid`, `pane_not_found`.
- **Required for**: `/org-start` Step 0.3 secretary-identity auto-recovery when launched outside `renga --layout ops`.

### 1.9 send_keys (raw PTY input)

- **Operation**: write raw bytes / translated key sequences to a pane's PTY.
- **Inputs**: `target`, optional `text` (literal), optional `keys[]` (vocabulary: `Enter`/`Return`, `Tab`, `Shift+Tab`/`BackTab`, `Esc`/`Escape`, `Backspace`, `Delete`/`Del`, `Up`/`Down`/`Left`/`Right`, `Home`/`End`, `PageUp`/`PageDown`, `Space`, `Ctrl+<A-Z>`), optional `enter` (boolean — append CR).
- **Error codes**: `invalid-params` (unknown key name), `pane_not_found`.
- **Distinction**: NOT equivalent to messaging. `send_keys` writes bytes visible to whatever process is in the pane; `send_message` (Surface 2) delivers a logical peer message.
- **Required for**: dev-channel approval (`org-start` Step 2.2 / 3.2; `org-delegate` Step 3-3b), permission-mode toggle (`Shift+Tab`), interrupt (`Ctrl+C`), modal escape (`Esc`).
- **Required-vs-optional**: REQUIRED. Dev-channel approval (`send_keys(enter=true)` at every Claude spawn), over-validation `Esc` intervention (`org-delegate` Step 5), and `Shift+Tab` permission-mode toggle all depend on raw PTY input. The contract requires the documented key vocabulary; backends without `send_keys` cannot drive the harness's existing approval and intervention flows.

---

## Surface 2: Messaging

The backend MUST provide a logical peer-messaging channel separate from raw PTY input.

### 2.1 send_message

- **Operation**: deliver a text payload to another pane in the same tab.
- **Inputs**: `to_id` (recipient pane id or stable name), `message` (text).
- **Delivery semantics**:
  - For push-mode recipients (Claude Code): the message MUST appear in-band at the recipient as a channel notification carrying source, sender id, sender name, and send timestamp.
  - For pull-mode recipients (Codex): the backend MUST emit a pane-local nudge to the recipient and queue the actual body for retrieval via `check_messages` (2.3).
- **Encoding contract**: HYBRID normativity. The semantic content — `from_id`, `from_name`, and `sent_at` attributes on the delivered channel notification — is contracted; recipients MUST receive these fields and harnesses MAY depend on them. The literal source-string label (renga uses `source="renga-peers"`) is backend-defined and is a transport tag, NOT a contract-fixed name. Harnesses MUST NOT hard-code the source string for routing decisions; they MAY reference it in human-facing logs. Renga's wire form is therefore `<channel source="renga-peers" from_id="..." from_name="..." sent_at="...">…</channel>`, but only the `from_*` / `sent_at` attributes are normative across backends.
- **Failure modes**: backend MUST raise `[backend_unreachable]` when the peer-channel server / transport is unavailable (the contracted end state per Surface 6.3). All other failures use the `[<code>] <message>` form (Surface 6). Transitional renga note: current renga still returns ok-text `"(message dropped — renga not reachable: <reason>)"` for this case; that ok-text shape is a transitional shim tracked by Issue #242, NOT the contract surface.

### 2.2 list_peers

- **Operation**: enumerate other peer-enabled panes in the current tab.
- **Outputs**: per-peer records (`id`, optional `name`, optional `role`, `cwd`, optional client kind, optional receive mode (push | poll), optional `summary`).
- **Distinction from `list_panes`**: `list_panes` includes the caller and exposes geometry; `list_peers` excludes the caller and hides geometry. The harness today uses `list_panes` for layout decisions (balanced split) and `list_peers` for "wait for Claude to register" gating in `org-delegate` Step 3-4 — both are load-bearing.
- **Failure modes**: same as 2.1 — backend MUST raise `[backend_unreachable]` (Surface 6.3). Transitional renga ok-text `"(no peers — renga not reachable: <reason>)"` is a shim tracked by Issue #242 and NOT the contract surface.

### 2.3 check_messages

- **Operation**: drain queued peer messages waiting for this client.
- **Outputs**: array of pending messages (sender, body, timestamp).
- **Required for**: pull-mode peers' actual body retrieval after a nudge; push-mode clients use it to retroactively drain anything missed.
- **Drain semantics**: each call returns the queue and clears it; messages are not redelivered.
- **Delivery semantics**: AT-MOST-ONCE. Each `check_messages` call drains the queue; messages are NOT redelivered after a successful drain. Explicit ack is NOT required. The contract codifies the current renga shape; harnesses MUST treat a drained message as gone and persist anything they need to survive a restart themselves.

### 2.4 set_summary

- **Operation**: set a 1–2 sentence summary describing what this pane is doing.
- **Inputs**: `summary` (string, MAX 256 chars; empty string clears).
- **Surfaced**: in every `list_panes` and `list_peers` record.
- **Error codes**: `summary_too_long`.
- **Required for**: `org-start` Step 0.1 (secretary self-summary so peers can discover the human-facing pane).

---

## Surface 3: Events

The backend MUST expose pane lifecycle events via a long-poll API with cursor-based resume.

### 3.1 poll_events

- **Operation**: long-poll for pane lifecycle events.
- **Inputs**: optional `since` (opaque cursor from prior `next_since`; omit on first call), optional `timeout_ms` (default 2000, hard max 30000, `0` for non-blocking drain), optional `types[]` (filter — only return events whose `type` is in the list).
- **Outputs**: `{next_since, events[]}`.
- **Initial-call semantics**: when `since` is omitted, the cursor starts at "right now" — NO historical replay. (Matches `renga events --timeout` semantics; required so dispatcher startup doesn't flood on past lifecycle events.)
- **Minimum event vocabulary** (backends MUST emit these; backends MAY emit additional types, and callers MUST treat unknown types as non-fatal — default-branch them):
  - `pane_started` — pane created.
  - `pane_exited` — pane terminated. MUST be emitted exactly once per successful `close` and once per crash.
  - `events_dropped` — backpressure / overflow signal. Carries the count of dropped events since the last delivery. Recovery: caller MUST reconcile via `list_panes`.
- **Optional event types** (backends MAY emit; harness behavior tolerates absence):
  - `heartbeat` — periodic keep-alive (renga: 30 s). Clients MAY rely on `poll_events` long-poll behavior for liveness rather than this event.
- **Filter behavior**: `types[]` narrows the returned slice but the cursor MUST advance past filtered-out events (no duplicate scan on resubmit). Filter-mismatched events that arrive during a long-poll cause early return with `events: []` and an advanced cursor; callers MUST re-poll.
- **`timeout_ms` hard cap**: 30 seconds (30000 ms). Backends MUST clamp larger values to this cap. Harnesses MUST NOT rely on longer waits; long-running listeners MUST re-poll.
- **Cursor-loss recovery**: BEST-EFFORT + reconciliation. There is NO numeric event-loss SLA. Cursor-file disappearance during a poll cycle is permitted; the contract requires that recovery via `list_panes` reconciliation eventually restores consistent state. This is consistent with the dispatcher event-loss tolerance ratified in Set A Q8.

---

## Surface 4: Identity & addressing

### 4.1 Pane identifiers

- Two identifier kinds: numeric `id` (assigned by backend, opaque integer) and optional stable `name` (caller-supplied string).
- **Disambiguation rule**: when a string is passed where an identifier is expected, all-digit strings MUST be interpreted as `id`s. A pane literally named `"7"` is therefore unaddressable by name and MUST be addressed by id. This rule applies to `target` parameters across all pane-control and messaging calls.
- **Name validation**: see Surface 1.8 (`set_pane_identity`).
- **Reserved names** (harness convention, not enforced by backend): `secretary`, `dispatcher`, `curator`, `worker-{task_id}`. The backend MUST NOT reject these names but assigns them no special semantics.

### 4.2 Tab scope

- All pane-addressed operations — including but not limited to `list_panes`, `focus_pane`, `send_message`, `inspect_pane`, `close_pane`, `send_keys`, `set_pane_identity`, and the `target` parameter of every spawn variant in §1.1–§1.3 — MUST resolve only against panes in the current tab. Cross-tab addressing returns `pane_not_found`.
- The harness today launches every pane via single-tab `spawn_pane` to satisfy this. Per `references/renga-error-codes.md`, this is a hard constraint (suisya-systems/renga#71) — `new_tab` worker spawns would orphan dispatcher monitoring.
- **Tab-scope decision**: SINGLE-TAB MUST. All pane-addressed operations resolve only against the current tab. Cross-tab addressing returns `pane_not_found`. Multi-tab support is NOT in this contract revision; if added later it requires a contract amendment with explicit tab-id parameters on every addressed call. Until amended, harnesses MUST launch every orchestrator-spawned pane in the same tab.

### 4.3 new_tab

- **Operation**: create a new tab with a fresh single pane and shift focus to it.
- **Inputs**: optional `command`, `cwd`, `name`, `role`, `label`.
- **Visibility consequence**: once focus shifts, panes in the previous tab MAY become invisible to MCP calls until focus returns (renga's per-tab limit; see 4.2). The harness uses `new_tab` only for human-driven workflows, never for orchestrator-spawned children.
- **Required-vs-optional**: backends SHOULD provide `new_tab` for human-driven workflows, but the contract does NOT make it strictly REQUIRED — no harness correctness flow depends on it (per Q10, all orchestrator panes live in a single tab). Harnesses MUST tolerate its absence.

---

## Surface 5: Authentication / channel

### 5.1 Dev-channel injection

The dev-channel flow has two halves:

- **Flag injection**: IF the backend provides the Surface 1.2 spawn-Claude convenience (OPTIONAL per Q1), it MUST inject `--dangerously-load-development-channels server:<channel-name>` itself. Otherwise the harness MUST add the flag in its Surface 1.1 `command` payload. Today renga provides the helper.
- **Prompt approval**: the Claude-side approval prompt (`Load development channel? (Y/n)`) is a Claude Code feature, not a backend feature. Per Q5, Surface 1.9 (`send_keys`) is REQUIRED, so the orchestrator MUST approve the prompt via `send_keys(enter=true)`; the contract does not provide a `send_keys`-less alternative path.

### 5.2 Channel transport

- The backend MAY use any transport for the peer channel (renga: a server name like `renga-peers`). The harness does not depend on transport details beyond the contract here. The channel-name normativity question is folded into 2.1 (channel-encoding contract).

---

## Surface 6: Error code vocabulary

The backend MUST surface failures via a machine-readable code, not by message-string substring matching. Wire format: the JSON-RPC error message starts with `[<code>] <human message>`. The harness branches on `[<code>]` substring match. The vocabulary below is the minimum set; backends MAY extend it (see 6.2).

### 6.1 Minimum required codes

| Code | Meaning | Issued by |
|---|---|---|
| `pane_not_found` | Target pane id / name / `"focused"` does not resolve. | All pane-addressed ops. |
| `pane_vanished` | Resolve succeeded but the pane disappeared before the op completed (race). | All pane-addressed ops. |
| `split_refused` | `spawn_pane` / `spawn_claude_pane` rejected: tab pane cap reached or new pane below `MIN_PANE_WIDTH` / `MIN_PANE_HEIGHT`. | spawn family. |
| `cwd_invalid` | `cwd` does not exist or is not a directory. Layout MUST NOT be mutated. | spawn family, `new_tab`. |
| `last_pane` | `close_pane` would close the only pane of the only tab. | `close_pane`. |
| `name_in_use` | `set_pane_identity` (or spawn with `name`) would collide. | `set_pane_identity`, spawn family. |
| `name_invalid` | Name is empty, all-digits, or contains characters outside `[A-Za-z0-9_-]`. | `set_pane_identity`, spawn family. |
| `summary_too_long` | `set_summary` exceeded 256 chars. | `set_summary`. |
| `invalid-params` | JSON-RPC input validation failure (e.g. `args[]` containing forbidden flag, unknown key name in `send_keys`). | All ops. |
| `io_error` | PTY write / spawn / OS-level failure. | All ops touching PTY. |
| `shutting_down` | Backend is mid-shutdown. Caller MUST stop polling loops. | All ops. |
| `app_timeout` | Internal backend thread did not respond. Caller MAY retry once. | All ops. |
| `parse` / `protocol` / `internal` | Backend-internal invariant violations; treated as bugs by the harness. | All ops. |
| `backend_unreachable` | Backend session / transport is unavailable (peer-channel server down, MCP socket gone, etc). Replaces the legacy ok-text shape on `list_peers` / `send_message` (see 6.3). | `list_peers`, `send_message`; MAY be raised by any op on transport loss. |

### 6.2 Stability requirements

- Codes MUST be ABI-stable across backend minor versions. Renames MUST go through a deprecation window with both old and new codes emitted in parallel.
- New codes MAY be added at any time. Callers MUST treat unknown codes as non-fatal (default-branch in case analysis), so backends can extend the vocabulary without breaking conformance.
- The human-facing message MAY change freely; only the `[<code>]` prefix is contracted.

### 6.3 ok-text exceptions (today's renga behavior)

Today renga returns ok-text (NOT a JSON-RPC error) on backend-unreachable for two ops only:
- `list_peers` returns `"(no peers — renga not reachable: <reason>)"`.
- `send_message` returns `"(message dropped — renga not reachable: <reason>)"`.

All other ops raise on unreachable.

- **Decision**: NORMALIZE TO ERROR. The backend MUST raise the standard `backend_unreachable` code (defined in 6.1) instead of returning ok-text on `list_peers` / `send_message` unreachable. NOTE: current renga still returns ok-text; the migration to the normalized error code is tracked as follow-up Issue #242 ("feat(renga + harness): normalize backend-unreachable to error code instead of ok-text"). Until that migration lands, harnesses MAY continue to branch on the existing `(no peers` / `(message dropped` prefix as a transitional shim — but the contracted end state is the `backend_unreachable` error.

---

## Surface 7: Backwards-compatibility commitment

The backend surface MUST follow semantic versioning. Breaking changes — operation removal, parameter removal, error-code rename without a deprecation window — MUST bump the major version. New operations, new parameters with safe defaults, and new error codes MAY be added in minor versions. Renames of error codes MUST go through a deprecation window with both the old and new code emitted in parallel for at least one minor version (consistent with the existing 6.2 stability requirements).

---

## Decision rationale digest

### Per-question traceability (12 Lead-confirmed decisions)

| #   | Topic                                       | Surface | Outcome             |
| --- | ------------------------------------------- | ------- | ------------------- |
| Q1  | Claude-spawn / Codex-spawn helpers required? | §1.2, §1.3 | OPTIONAL         |
| Q2  | Separate graceful-exit path required?       | §1.4    | OPTIONAL            |
| Q3  | `list_panes` geometry fields required?      | §1.5    | REQUIRED            |
| Q4  | `inspect_pane` (grid scrape) required?      | §1.7    | REQUIRED            |
| Q5  | `send_keys` (raw PTY input) required?       | §1.9    | REQUIRED            |
| Q6  | Channel-encoding normativity                | §2.1    | HYBRID              |
| Q7  | Message delivery semantics                  | §2.3    | AT-MOST-ONCE        |
| Q8  | `poll_events` `timeout_ms` hard cap         | §3.1    | 30 s (30000 ms)     |
| Q9  | Cursor-loss recovery contract               | §3.1    | Best-effort + reconciliation |
| Q10 | Multi-tab vs single-tab addressing          | §4.2    | SINGLE-TAB MUST     |
| Q11 | ok-text-on-unreachable normalization        | §6.3    | Normalize to `backend_unreachable` |
| Q12 | Backend SemVer commitment                   | §7      | REQUIRED            |

### Cluster summary

The 12 Lead-confirmed decisions above cluster as follows:

1. **Required-vs-optional surface boundaries** — `send_keys`, `inspect_pane`, and `list_panes` geometry are REQUIRED (the harness's approval flow, observation-based safety, and balanced-split scheduling cannot be replaced). The Claude-spawn / Codex-spawn convenience helpers and a graceful-exit operation are OPTIONAL — backends MAY provide them, but harnesses MUST be able to drive a generic spawn + flag injection and rely on pane self-`exit`.
2. **Channel-encoding normativity** — HYBRID. Semantic fields (`from_id`, `from_name`, `sent_at`) are contracted; the literal source-string label (`renga-peers`) is backend-defined and must not be hard-coded for routing.
3. **Event-stream guarantees** — `timeout_ms` hard-capped at 30 s; cursor-loss recovery is best-effort with `list_panes` reconciliation, no numeric event-loss SLA (consistent with Set A Q8).
4. **Identity / scope rules** — SINGLE-TAB MUST. Multi-tab addressing is deferred to a future contract amendment.
5. **Failure-mode normativity** — Message delivery is at-most-once (drain semantics). Backend-unreachable conditions normalize to error codes; the current renga ok-text carve-out is transitional and tracked as a follow-up Issue.
6. **Backwards-compatibility commitment** — Surface follows SemVer (Surface 7).
