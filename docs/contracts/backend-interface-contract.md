# Contract Set D — Backend Interface

> **Status**: Ratified (2026-05-03). Lead-confirmed decisions for all 12 open questions. This contract defines the abstract backend surface that the `claude-org` harness depends on; renga 0.18.0+ is the reference implementation, but any backend meeting the surface specified here is permitted.
>
> **Ratified amendment (2026-06-14 — Epic #6 broker dogfood #515 passed)**: a second reference backend — **`org-broker`** (the pure-backend broker extracted into `claude_org_runtime.broker`, driven by terminal adapters tmux/WezTerm) — is added as an **additive** amendment. The amendment introduces a new [Surface 8 (Broker auth & delivery)](#surface-8-broker-auth--delivery-ratified-amendment) and short per-surface broker notes on Surfaces 1–6; it does **not** modify any ratified normative text above. Renga (`renga-peers`) remains the **default** backend (`ORG_TRANSPORT` unset = `renga`); broker is **opt-in** (`ORG_TRANSPORT=broker`) and renga is never removed (opt-in fallback / rollback safety). Ratification was gated on the Epic #6 dogfood (transport-lab `docs/design/ja-migration-plan.md` §8 Issue G), which **passed** (unattended auto-delegation cycle completed end-to-end under renga/broker coexistence; runtime main 95855d3, re-verified 2026-06-14). Each broker note below is marked **“Broker amendment (ratified)”**. Two known limitations are ratified with the surface and recorded in [Surface 8](#surface-8-broker-auth--delivery-ratified-amendment) (folder-trust auto-approval wiring unverified — ja#566; host-reject silent-drop class — runtime#81); both affect the opt-in broker path only and renga stays the default + always-available fallback, so the risk is bounded.
>
> **Scope**: Phase 1 Contract Set D only. Sets A (roles), B (delegation lifecycle), C (state), and E (knowledge) are tracked in #121 / #122 / #124 / #125 and out of scope here.
>
> **Subject**: Set D defines the abstract API that any backend must provide for `claude-org` to operate. Today the only implementation is **renga** (`mcp__renga-peers__*` MCP server, renga 0.18.0+). This contract documents what the harness *requires*, not what renga *happens to provide* — alternative backends are permitted as long as they meet the surface specified here.
>
> **Frame note (additive, Refs #586 #604 — does not alter the ratified 2026-05-03 sentence above):** "the only implementation is renga" is the original 2026-05-03 wording. Per the ratified 2026-06-14 amendment above, **`org-broker`** is now a second reference backend (renga stays the default, broker is opt-in). The Phase 1 proposed amendment below additionally records a *future* contracted default flip to broker; see the two-frame note attached to it for how the contract-declaration / code-constant / operational frames relate.
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

> **Broker amendment (ratified)**: `org-broker` provides the generic `spawn_pane` plus `spawn_claude_pane` / `spawn_codex_pane` convenience helpers (§1.2/§1.3) and `close_pane` / `list_panes` / `inspect_pane` / `send_keys` / `set_pane_identity`; it intentionally **omits** `focus_pane` and `new_tab` (exercising the OPTIONAL latitude of §1.6/§4.3). Broker spawn helpers inject `--mcp-config <broker>` (not `--dangerously-load-development-channels`) and build the interactive-TUI argv internally behind a default-deny billing-neutral guard. `list_panes` output fields carry broker semantics (§1.5 note, [Surface 8](#surface-8-broker-auth--delivery-ratified-amendment) §8.5/§8.8/§8.9).

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

> **Broker amendment (ratified)**: under `org-broker` the `cwd`, `kind`, and `receive_mode` fields are populated with broker semantics — `cwd` is known at spawn-time token bind, `kind` reflects the broker client, and `receive_mode` is the constant `"poll"` (broker delivers all peers pull-style — there are no push peers; [Surface 8](#surface-8-broker-auth--delivery-ratified-amendment) §8.4/§8.8). Geometry, `id`, `focused`, and the rest of the record are unchanged. Where the ratified text says these fields are present "when known", broker makes them deterministic per §8.8.
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
- **Required for**: dev-channel approval (`org-start` Block D-1; `org-delegate` Step 3-3b), permission-mode toggle (`Shift+Tab`), interrupt (`Ctrl+C`), modal escape (`Esc`).
- **Required-vs-optional**: REQUIRED. Dev-channel approval (`send_keys(enter=true)` at every Claude spawn), over-validation `Esc` intervention (`org-delegate` Step 5), and `Shift+Tab` permission-mode toggle all depend on raw PTY input. The contract requires the documented key vocabulary; backends without `send_keys` cannot drive the harness's existing approval and intervention flows.

---

## Surface 2: Messaging

The backend MUST provide a logical peer-messaging channel separate from raw PTY input.

> **Broker amendment (ratified)**: `org-broker` keeps the same messaging tools (`send_message` / `list_peers` / `check_messages` / `set_summary`, same argument shapes), but delivery is **pull for every recipient** — `send_message` queues the body and emits a pane-local nudge, and the recipient drains it via `check_messages` (2.3). There is no push (in-band `<channel>`) delivery under broker. The channel source label becomes `source="org-broker"` (a transport tag — still non-normative per 2.1's HYBRID encoding rule; harnesses MUST NOT route on it). The `from_id` / `from_name` / `sent_at` semantic fields remain contracted and unchanged. `list_peers` output `cwd` / `kind` / `receive_mode` carry broker semantics ([Surface 8](#surface-8-broker-auth--delivery-ratified-amendment) §8.4/§8.8). On transport loss broker raises an error code per §8.7 (no ok-text shim — broker has no §6.3 carve-out).

### 2.1 send_message

> **Proposed amendment (capability-conditional cross-tab addressing)**: the "same tab" restriction in the Operation line below is **conditionally superseded** by T-§2.1 of the [Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing](#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing). The replacement rule — stable-name resolution stays sender-tab-local, numeric pane ids MAY cross tabs — applies **only** to backends that advertise the `cross_tab_peers` capability. Backends that do not advertise it, **including `org-broker`**, keep the ratified line below verbatim. Status: PROPOSED, pending human ratification; **nothing below is deleted or weakened**.

- **Operation**: deliver a text payload to another pane in the same tab.
- **Inputs**: `to_id` (recipient pane id or stable name), `message` (text).
- **Delivery semantics**:
  - For push-mode recipients (Claude Code): the message MUST appear in-band at the recipient as a channel notification carrying source, sender id, sender name, and send timestamp.
  - For pull-mode recipients (Codex): the backend MUST emit a pane-local nudge to the recipient and queue the actual body for retrieval via `check_messages` (2.3).
- **Encoding contract**: HYBRID normativity. The semantic content — `from_id`, `from_name`, and `sent_at` attributes on the delivered channel notification — is contracted; recipients MUST receive these fields and harnesses MAY depend on them. The literal source-string label (renga uses `source="renga-peers"`) is backend-defined and is a transport tag, NOT a contract-fixed name. Harnesses MUST NOT hard-code the source string for routing decisions; they MAY reference it in human-facing logs. Renga's wire form is therefore `<channel source="renga-peers" from_id="..." from_name="..." sent_at="...">…</channel>`, but only the `from_*` / `sent_at` attributes are normative across backends.
- **Failure modes**: backend MUST raise `[backend_unreachable]` when the peer-channel server / transport is unavailable (the contracted end state per Surface 6.3). All other failures use the `[<code>] <message>` form (Surface 6). Transitional renga note: current renga still returns ok-text `"(message dropped — renga not reachable: <reason>)"` for this case; that ok-text shape is a transitional shim tracked by Issue #242, NOT the contract surface.

### 2.2 list_peers

> **Proposed amendment (capability-conditional cross-tab addressing)**: the "in the current tab" scope in the Operation line below is **conditionally superseded** by T-§2.2 of the [Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing](#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing), which requires **all-tab enumeration** from backends that advertise `cross_tab_peers`, and adds the `same_tab` / `tab` / `tab_name` per-peer output fields (T-§2.2-fields, extending the §8.8 output-field amendment). Backends that do not advertise the capability, **including `org-broker`**, keep the ratified current-tab scope and emit none of the three fields. Status: PROPOSED, pending human ratification; **nothing below is deleted or weakened**.

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

> **Broker amendment (ratified)**: `org-broker` exposes the same `poll_events` long-poll API and the same minimum vocabulary (`pane_started` / `pane_exited` / `events_dropped`). Because terminal backends (tmux/WezTerm) have no native lifecycle push, broker **synthesizes** these events from `list_panes` diff reconciliation (transport-lab `docs/design/ja-migration-plan.md` §6 — backend-agnostic, exactly-once `pane_exited`, `events_dropped` on overflow with `list_panes` reconcile recovery). This satisfies the §3.1 best-effort + reconciliation contract (Q9) without change; the cursor/timeout semantics (initial-call = "now", 30 s hard cap) are identical.

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

> **Broker amendment (ratified)**: `org-broker` resolves `target` by the same three addressing kinds as renga — numeric/all-digit handle, stable `name`, and the literal `"focused"` — preserving the §4.1 all-digit-is-id disambiguation rule. Broker honours the SINGLE-TAB MUST scope (§4.2). `new_tab` (§4.3) is **not** provided by broker (OPTIONAL surface; harnesses already tolerate its absence). The reserved-name convention (§4.1) is unchanged.

### 4.1 Pane identifiers

- Two identifier kinds: numeric `id` (assigned by backend, opaque integer) and optional stable `name` (caller-supplied string).
- **Disambiguation rule**: when a string is passed where an identifier is expected, all-digit strings MUST be interpreted as `id`s. A pane literally named `"7"` is therefore unaddressable by name and MUST be addressed by id. This rule applies to `target` parameters across all pane-control and messaging calls.
- **Name validation**: see Surface 1.8 (`set_pane_identity`).
- **Reserved names** (harness convention, not enforced by backend): `secretary`, `dispatcher`, `curator`, `worker-{task_id}`. The backend MUST NOT reject these names but assigns them no special semantics.

### 4.2 Tab scope

> **Proposed amendment (capability-conditional cross-tab addressing)**: **both** the first bullet below (the operational "all pane-addressed operations … MUST resolve only against panes in the current tab" rule, which names `send_message` explicitly) **and** the SINGLE-TAB MUST decision bullet are **conditionally superseded** by T-§4.2 of the [Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing](#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing). Under a backend that advertises `cross_tab_peers`, the messaging scope and the pane-control scope **diverge**: messaging resolves names inside the sender's tab, accepts numeric peer ids across tabs, and enumerates peers from every tab, while pane-addressed **control** operations stay caller-tab-scoped. Backends that do not advertise the capability, **including `org-broker`** (whose §4 broker note above therefore continues to read exactly as written), keep the ratified SINGLE-TAB MUST verbatim. Status: PROPOSED, pending human ratification; **nothing below is deleted or weakened**.

- All pane-addressed operations — including but not limited to `list_panes`, `focus_pane`, `send_message`, `inspect_pane`, `close_pane`, `send_keys`, `set_pane_identity`, and the `target` parameter of every spawn variant in §1.1–§1.3 — MUST resolve only against panes in the current tab. Cross-tab addressing returns `pane_not_found`.
- The harness today launches every pane via single-tab `spawn_pane` to satisfy this. Per `references/renga-error-codes.md`, this is a hard constraint (suisya-systems/renga#71) — `new_tab` worker spawns would orphan dispatcher monitoring.
- **Tab-scope decision**: SINGLE-TAB MUST. All pane-addressed operations resolve only against the current tab. Cross-tab addressing returns `pane_not_found`. Multi-tab support is NOT in this contract revision; if added later it requires a contract amendment with explicit tab-id parameters on every addressed call. Until amended, harnesses MUST launch every orchestrator-spawned pane in the same tab.

### 4.3 new_tab

> **Proposed amendment (capability-conditional cross-tab addressing)**: the **Visibility consequence** bullet below ("once focus shifts, panes in the previous tab MAY become invisible to MCP calls until focus returns") is **conditionally superseded** by T-§4.2 of the [Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing](#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing). Under a backend that advertises `caller_scope`, addressability follows the **caller's** tab rather than the focused tab, so shifting focus neither hides the caller's panes nor reveals another tab's, and returning focus is not a recovery step. The OPTIONAL / human-driven positioning of `new_tab` itself is **not** superseded. Backends that do not advertise the capability, **including `org-broker`** (which omits `new_tab` entirely, §8.9), keep the bullet below verbatim. Status: PROPOSED, pending human ratification; **nothing below is deleted or weakened**.

- **Operation**: create a new tab with a fresh single pane and shift focus to it.
- **Inputs**: optional `command`, `cwd`, `name`, `role`, `label`.
- **Visibility consequence**: once focus shifts, panes in the previous tab MAY become invisible to MCP calls until focus returns (renga's per-tab limit; see 4.2). The harness uses `new_tab` only for human-driven workflows, never for orchestrator-spawned children.
- **Required-vs-optional**: backends SHOULD provide `new_tab` for human-driven workflows, but the contract does NOT make it strictly REQUIRED — no harness correctness flow depends on it (per Q10, all orchestrator panes live in a single tab). Harnesses MUST tolerate its absence.

---

## Surface 5: Authentication / channel

> **Broker amendment (ratified)**: under `org-broker` the dev-channel flow of §5.1 is replaced by a **bind-token + folder-trust** flow (detail in [Surface 8](#surface-8-broker-auth--delivery-ratified-amendment) §8.2/§8.5). The spawn helper injects `--mcp-config <broker>` instead of `--dangerously-load-development-channels server:<channel>`; the Claude-side approval prompt is the **folder-trust** prompt (a Claude Code feature, not a backend feature), machine-approved by the orchestrator via `send_keys(enter=true)` — structurally the same "spawn → approve prompt → peer joins" shape as §5.1, so §1.9 `send_keys` remains REQUIRED. Authentication itself (which renga leaves to the channel) is carried by a per-agent broker bind token with an immutable permission tier (§8.2/§8.3). The §5.2 transport-agnosticism statement already permits this: broker's channel transport is a localhost HTTP MCP server (§8.6).

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

> **Broker amendment (ratified)**: `org-broker` reuses the shared codes where semantics match (`pane_not_found`, `last_pane`, `invalid-params`) and **adds** the broker-specific codes catalogued in [Surface 8](#surface-8-broker-auth--delivery-ratified-amendment) §8.7 (`token_invalid`, `session_invalid`, `tool_not_authorized`, `peer_not_found`, `name_taken`, `no_backend`, `nudge_failed`, `unknown_tool`). These are additive per the 6.2 rule above; renga harnesses are unaffected and broker harnesses default-branch any unknown code.

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

## Surface 8: Broker auth & delivery (ratified amendment)

> **Status of this surface**: **Ratified 2026-06-14** (Epic #6 broker dogfood #515 passed). Surfaces 1–7 are ratified (2026-05-03) and describe the **renga** reference backend; this section is **additive** and introduces a **second** reference backend, **`org-broker`** (the pure-backend broker extracted into `claude_org_runtime.broker`, driven by terminal adapters `tmux` / `wezterm`). It modifies no ratified normative text. Renga (`renga-peers`) stays the **default** (`ORG_TRANSPORT` unset = `renga`); broker is **opt-in** (`ORG_TRANSPORT=broker`) and renga is never removed (opt-in fallback / rollback safety). Ratification was gated on the Epic #6 dogfood (transport-lab `docs/design/ja-migration-plan.md` §8 Issue G), which **passed**: the unattended auto-delegation cycle completed end-to-end under renga/broker coexistence (runtime main 95855d3, re-verified 2026-06-14). Design SoT for everything below: transport-lab `docs/design/ja-migration-plan.md` §3 (compat surface), §4 (runtime extraction), §5 (ja seam). Reference implementation: `claude_org_runtime.broker` (server / tokens / surface) 0.1.17.
>
> **Known limitations (ratified with the surface; opt-in `ORG_TRANSPORT=broker` path only):**
> - **(1) folder-trust auto-approval wiring is unverified** — the §8.5 spawn ritual's folder-trust machine-approval (the `send_keys(enter=true)` step at `.dispatcher/references/spawn-flow.md` 3-3b) is not yet verified end-to-end as auto-wired. Tracked as **ja#566**, which is an `org-start` operational item distinct from the delivery cycle that the dogfood exercised.
> - **(2) host-reject silent-drop class persists** — `/confirm-delivered` is an **emit gate**, not a host-accept gate, so a host-side reject of a delivery is not surfaced back to the sender (a silent-drop class remains on the host-reject path). Tracked as **runtime#81**.
>
> Both limitations are limited-risk: they affect only the opt-in broker path, and renga remains the **default** and an always-available fallback (rollback safety, §8.10).

### 8.1 Transport identity & coexistence

- The broker MCP server name is **`org-broker`** (deliberately distinct from `renga-peers`), so fully-qualified tool names become `mcp__org-broker__<tool>` (renga: `mcp__renga-peers__<tool>`). The distinct name lets both servers be registered in the same machine/session without collision, which is what makes opt-in / staged migration / rollback safe (design §3.4).
- "Drop-in compatibility" holds at the **argument-shape and semantics** level (so harness logic / retraining is minimized); the FQ tool-name prefix and the per-role MCP allowlist strings are mechanically rewritten by the generation seam, not by the contract. Renga remains the default and the byte-stable baseline.

### 8.2 Authentication — per-agent bind tokens

- The backend issues a **per-agent bind token** at spawn (reference impl: `secrets.token_urlsafe(32)`). Every subsequent MCP call from that agent is authenticated by its token; an unknown/invalid token raises `token_invalid` (§8.7).
- Each token carries an **immutable permission tier** (`auth_role`) decided at issue time. The display `role` (mutable via `set_pane_identity`, Surface 1.8) is **decoupled** from `auth_role` and CANNOT escalate the tier — `set_pane_identity` changes the label only. This is a structural strengthening over renga, where `role` is purely a display label and the allowlist is the only gate.
- A spawned child's tier is **capped at the caller's tier** (a caller cannot grant a child more authority than it holds). This bounds privilege flow through the spawn tree.

### 8.3 Tier-gated surface (structural authorization)

- The broker filters `tools/list` by the caller's `auth_role`, so out-of-tier tools are **not even visible** to a lower tier; invoking one anyway raises `tool_not_authorized` (§8.7). Tiers:
  - **messaging tier** (`worker` / `curator` / unknown role): `send_message`, `check_messages`, `list_peers`, `set_summary` (4 tools).
  - **ops tier** (`dispatcher` / `secretary`): the messaging four **plus** pane control (`list_panes`, `inspect_pane`, `send_keys`, `poll_events`, `close_pane`, `set_pane_identity`, `spawn_claude_pane`, `spawn_codex_pane`; `secretary` additionally gets generic `spawn_pane` for the attention-watcher).
- This is **defense-in-depth**: structural tier gating at the backend is the primary gate; the settings MCP allowlist (per-role) is a second, redundant gate. Under renga every role sees the same surface and the allowlist is the *only* gate — broker is strictly stronger here, not a regression.

### 8.4 Delivery model — pull for every recipient

- `send_message` **queues** the body and emits a **pane-local nudge** to the recipient; the recipient retrieves the body via `check_messages` (Surface 2.3). This applies to **all** recipients — there is no push (in-band `<channel>`) delivery under broker.
- Consequence for harness prose: the renga receive cue "when a `<channel>` notification arrives, ack" becomes "when a nudge line appears, run `check_messages`, then ack" under broker. The `from_id` / `from_name` / `sent_at` semantic fields (Surface 2.1 HYBRID rule) are unchanged; only the transport source tag differs (`source="org-broker"`).

### 8.5 Spawn ritual — `--mcp-config` injection + folder-trust approval

- Broker spawn helpers (`spawn_claude_pane` / `spawn_codex_pane`) inject **`--mcp-config <broker>`** (not `--dangerously-load-development-channels`) and **build the interactive-TUI argv internally** rather than accepting a caller-supplied `argv`.
- The Claude-side approval prompt under broker is the **folder-trust** prompt (a Claude Code feature), machine-approved by the orchestrator via `send_keys(enter=true)` — the same "approve the spawn prompt" shape as renga's dev-channel approval (Surface 5.1), so Surface 1.9 `send_keys` stays REQUIRED.
- **Billing-neutral guard (maintenance contract continues)**: because broker builds the argv, the interactive-TUI guard checks the **builder's own output** (structurally an interactive TUI) rather than caller argv. The guard is a **default-deny allowlist**: headless / non-interactive subcommands and flags (claude/codex `exec` / `review` / `*-server` / `apply` / `sandbox` / `completion` / unknown subcommands / bare positionals / `--` bypass) are rejected with `invalid-params`. New legitimate interactive flags require an allowlist extension; headless surfaces are never added (design §3.3-1/§3.3-6, §7.6 lineage).

### 8.6 Channel transport — localhost HTTP MCP

- The broker is a **localhost-only HTTP MCP server** (bound to `127.0.0.1`). Surface 5.2 already declares the channel transport backend-defined; this records the concrete broker choice.
- This is a **host-local exception** to `docs/non-goals.md` §12 ("no external HTTP MCP exposure"): the broker is not externally reachable, introduces no TLS / network-boundary / external-auth surface, and stays inside the "local-completeness" operating discipline. See the §12 amendment in `docs/non-goals.md`.

### 8.7 Broker error vocabulary (extends Surface 6)

Additive codes (the `[<code>]` prefix form of Surface 6 applies unchanged). Renga harnesses never see these; broker harnesses default-branch any unknown code (6.2).

| Code | Meaning | Issued by |
|---|---|---|
| `token_invalid` | Bind token is unknown / malformed / revoked. | All authenticated ops. |
| `session_invalid` | Broker session for this agent is gone (daemon restarted, bind dropped). | All authenticated ops. |
| `tool_not_authorized` | Caller's `auth_role` tier does not include the requested tool (§8.3). | Tier-gated ops. |
| `unknown_tool` | Tool name not in the broker catalogue. | All ops. |
| `peer_not_found` | `send_message` / messaging target id or name does not resolve. | messaging ops. |
| `name_taken` | Spawn / `set_pane_identity` name collision (broker's spelling of renga's `name_in_use`). | spawn family, `set_pane_identity`. |
| `no_backend` | The terminal adapter (tmux/WezTerm) is unavailable — the "adapter_unavailable" condition. | pane-control ops. |
| `nudge_failed` | The pull nudge could not be delivered to the recipient pane. | `send_message`. |

> Mapping note for prose (design §5.2 ii names the broker error additions as `token_*` / `nudge_failed` / `adapter_unavailable`): `token_*` = `token_invalid` + `session_invalid`; `adapter_unavailable` = `no_backend`; tier gating adds `tool_not_authorized`. `name_taken` is the broker spelling of the shared `name_in_use` semantics.

### 8.8 Output-field semantics (amends §1.5 / §2.2)

- Under broker, `list_panes` / `list_peers` records populate `cwd` (known at spawn-time token bind), `kind` (broker client kind), and `receive_mode` deterministically. `receive_mode` is the **constant `"poll"`** — broker has no push peers. Where the ratified §1.5/§2.2 wording says these fields appear "when known" / "optional", broker makes them deterministic; this is an *amendment to the output-field documentation*, not a change to the renga shape (renga keeps its existing optional/"when known" behavior).

### 8.9 Surfaces intentionally not provided

- `new_tab` (§4.3) and `focus_pane` (§1.6) are **omitted** from the broker surface — both are already OPTIONAL / SHOULD (no harness correctness flow depends on them), and harnesses already MUST tolerate their absence. This is a deliberate scope decision (design §3.1), not a gap.

### 8.10 Coexistence, default, and rollback

- `org-broker` and `renga-peers` use distinct MCP server names and MAY be registered simultaneously; the `ORG_TRANSPORT` flag selects which the harness drives (org-wide single value — not mixed per-pane).
- Renga is the **default** and is **never removed** — it is retained as the opt-in fallback / rollback target. A `transport=renga` rollback re-points the *next* spawned pane at renga; full rollback of an active broker deployment additionally requires settings regeneration, respawn of active broker panes, ordered broker-daemon stop, and token/queue-store teardown (design §5.5).

### 8.11 SemVer

- The broker surface follows the Surface 7 SemVer commitment. The runtime release that adds broker is **additive** (existing renga API unchanged), consumable within ja's `<0.2` pin window as a minor bump.

---

## Ratified amendment (2026-06-15): push-primary delivery via `claude/channel`

> **Status: RATIFIED 2026-06-15** (human ratification gate cleared; ja PR #583). This section is an **additive amendment** (S3) and **deletes no prior ratified normative text above**. It **supersedes the pull-only broker delivery semantics** of §2.1 / §2.3 / §5 / §8: under broker, **push-primary (`claude/channel`) is now the contracted delivery model, with pull retained as the structural fallback layer** — the prior pull-only wording is not removed but now reads as the fallback path. Surface 8 (and the per-surface broker notes on Surfaces 1–6) was ratified 2026-06-14 describing broker delivery as **pull for every recipient** (§8.4) with a `--mcp-config` + folder-trust spawn ritual (§8.5) and `receive_mode` constant `"poll"` (§8.8); the runtime then moved to **push-first** delivery (transport-lab PR #24, `claude_org_runtime` 0.1.24+), and this amendment brings the contract into line with that implementation.
>
> **Design SoT**: transport-lab `docs/design/broker-native-roles.md` §9 (push-primary redesign, β architecture: daemon authority + per-session channel sidecar) and `docs/design/ja-migration-plan.md` §8 (#18 追補). **Precondition — SATISFIED**: the §9.5 **K1 pre-ratification spike** (Claude Code harness loads a tool-less `claude/channel` server, idle-wakes on its notifications, and coexists with renga's channel) **PASSED** (transport-lab #22 CLOSED=PASS; spike `RESULTS.md`), so the amendment's load-bearing assumption was verified before ratification; the human ratification gate was subsequently cleared (2026-06-15). Had K1 failed, the fallback would have been the claude-peers-style "tools + channel co-resident in one sidecar" form (§9.5). All changes below are **broker-branch additive and flag-gated** (`ORG_TRANSPORT=broker`); **renga (`renga-peers`) is untouched** and remains the default + always-available fallback (§8.10).

### P-§1.2 spawn (Claude Code convenience) — additive dev-channel sidecar

The ratified §1.2 broker note has broker spawn helpers inject `--mcp-config <broker>` and *not* `--dangerously-load-development-channels`. **Ratified (additive)**: broker spawn ALSO loads a per-session **channel sidecar** via `--dangerously-load-development-channels server:org-broker-channel` **in addition to** `--mcp-config <broker>`. The two coexist: `--mcp-config` carries the daemon (all tools + per-agent token, unchanged); the dev-channel flag loads a thin stdio MCP sidecar (`org-broker-channel`) that declares `experimental: { "claude/channel": {} }` and holds only a delivery-scoped credential (P-§8.2). This re-introduces the dev-channel injection half of §5.1 for the **sidecar only** — it does not remove the ratified `--mcp-config` injection.

### P-§2.1 send_message — push-primary delivery for broker

The ratified §8.4 made broker delivery pull for every recipient; this amendment supersedes that for broker. **Ratified**: broker delivery becomes **push-primary**. `send_message` queues the body (unchanged); a per-session channel sidecar claims and pushes it as a `notifications/claude/channel` notification, which the harness injects in-band into the recipient's turn (idle sessions wake — the same "delivered → ack" cue renga's in-band push provides). This realigns broker with §2.1's existing **push-mode-recipient** clause (already contracted for Claude Code) rather than restricting broker to the pull-mode clause. **Pull remains the fallback** (see P-§2.3 / §8.4-fallback): when no healthy sidecar is registered the recipient drains via `check_messages` exactly as the ratified §8.4 prescribes. The `from_id` / `from_name` / `sent_at` HYBRID semantic fields and the `source="org-broker"` transport-tag rule are **unchanged**.

### P-§2.3 check_messages — three-state delivery lifecycle (additive)

The ratified §2.3 contracts AT-MOST-ONCE drain ("each call returns the queue and clears it; messages are not redelivered"). **Ratified (SemVer-additive, no change to the ratified drain guarantee)**: introduce a daemon-owned **three-state lifecycle** so push delivery cannot silently lose a message when a sidecar dies mid-delivery:

| State | Meaning |
|---|---|
| `UNDELIVERED` | queued, not yet claimed (initial; set by `send_message`) |
| `CLAIMED(lease, owner, epoch)` | a drainer holds a lease while attempting delivery |
| `DELIVERED` | delivery confirmed (harness-accepted); never redelivered |

- Push path = **claim-then-confirm**: the sidecar claims `UNDELIVERED` rows with a lease (`/poll-claims`), emits each as `claude/channel`, then marks only the rows whose notification resolved as `DELIVERED` (`/confirm-delivered`, idempotent by id). A lease that expires unconfirmed (sidecar died) is **reaped back to `UNDELIVERED`** (re-eligible).
- **Drain semantics restated additively**: `check_messages` drains the **`UNDELIVERED`-and-unclaimed** view (plus lease-expired rows it reclaims), taking the claim itself so it never double-delivers against a live sidecar claim or a concurrent `check_messages`. `DELIVERED` rows are never returned — preserving the ratified "not redelivered after a successful drain" guarantee. The overall lifecycle is **at-least-once + idempotent display** (duplicate display is benign; loss is fatal); the `DELIVERED` (confirmed) terminal is at-most-once, satisfying §2.3.
- `DELIVERED` means **harness-accepted** (the `claude/channel` notification resolved at the transport), not "visibly rendered to the model" — the residual accept→visible window is exactly what at-least-once + idempotent display covers, and is part of the K1 spike measurement (§9.3).

### P-§5.1 / §5.2 Authentication / channel — dev-channel approval re-introduced (additive)

The ratified §8.5 replaced the §5.1 dev-channel approval with a folder-trust prompt. **Ratified (additive)**: the §5.1 dev-channel approval prompt (`Load development channel? (Y/n)`) **re-appears for the `org-broker-channel` sidecar** and is machine-approved by the orchestrator via `send_keys(enter=true)` (spawn-flow 3-3b, re-introduced). This is **in addition to** the ratified folder-trust approval of §8.5, not a replacement — a broker spawn now machine-approves both prompts. §1.9 `send_keys` stays REQUIRED (it already was). §5.2 (channel transport is backend-defined) is unchanged and already permits the localhost-HTTP daemon + stdio-sidecar split.

### P-§8 Broker auth & delivery — ratified deltas

- **P-§8.2 (delivery-scoped token scope, additive)**: add a `scope` field to the per-agent bind token (`full` | `delivery`). A `scope=delivery` credential authorizes **only** `/poll-claims` and `/confirm-delivered`, and only for rows where `to_id == owner` — it grants **no tool/tier authority**. The sidecar receives a `delivery`-scoped credential (NOT the agent's `full` token), so the second process cannot exercise the agent's pane-control tier. This is the trust-boundary basis that also lets the daemon distinguish sidecar-drain from agent-drain (mutual exclusion). The ratified §8.2/§8.3 per-agent `full` token and tier model are unchanged.
- **P-§8.4 (delivery model → push-primary, fallback retained)**: amend "pull for every recipient — there is no push" to **"push-primary via the channel sidecar; pull (`check_messages`) is the structural fallback"**. Fallback triggers: sidecar absent / unhealthy (`delivery_mode=PULL` on heartbeat timeout), channel-incapable peers (codex), or claude.ai-login-absent environments. The ratified pull description becomes the documented fallback path, not the only path. **Delivery vs. reception (note for harness prose)**: the backend still emits the ratified §8.4 pane-local nudge on `send_message`, so this surface's *mechanism* is unchanged; but a nudge does NOT wake an idle session, so harness reception in fallback is the agent's **active role-cadence poll** of `check_messages` (worker turn-boundary / bounded `/loop`, dispatcher `/loop 3m`, secretary turn-prologue — design §9.6), with the nudge treated as a non-load-bearing accelerator. This keeps the contract mechanism aligned with ratified §8.4 while preventing the prose from re-introducing a nudge-dependent reception model.
- **P-§8.5 (spawn ritual, additive)**: broker spawn injects `--mcp-config <broker>` **and** `--dangerously-load-development-channels server:org-broker-channel`, and machine-approves **both** the folder-trust prompt and the re-introduced dev-channel prompt (P-§5.1). The billing-neutral default-deny argv guard is unchanged.
- **P-§8.8 (receive_mode → `push`, fallback `poll`)**: amend the `receive_mode` field from the constant `"poll"` to **`"push"`** for channel-capable broker peers (fallback value `"poll"` when degraded). This follows §8.4 above; it is a contract output-field amendment (the runtime descriptor that emits this field is tracked separately as transport-lab D2 and is out of ja scope). Renga's `receive_mode="push"` and its optional/"when known" shape are unchanged.
- **Rollback note (additive to §8.10)**: the push-primary path adds new live state — a per-pane channel sidecar process, per-agent `delivery_mode`, and a delivery-scoped credential. A `transport=renga` rollback therefore gains a sixth teardown sub-step: reap each per-pane channel sidecar (SIGTERM/unregister) + reset that agent's `delivery_mode` + revoke its delivery-scoped credential. Renga still spawns no second dev-channel; the rollback stays bounded and flag-gated (design §9.7).

### P-pre-ratification (K1 spike gate)

The §9.5 **K1 spike** (dependency-ordered *before* this amendment and before the matching prose land) has **PASSED** (transport-lab #22 CLOSED=PASS; spike `RESULTS.md`): the Claude Code harness (i) loads a tool-less `experimental{claude/channel}` stdio server under `--dangerously-load-development-channels`, (ii) idle-wakes on its `notifications/claude/channel`, and (iii) coexists with renga's channel; the `mcp.notification` resolve visibility/failure boundary was measured (§9.3). Had K1 failed, the fallback would have been the claude-peers-style co-resident sidecar (tools + channel in one server), re-scoping P-§8.2's token from `delivery` to messaging-tier (§9.5). The K1 verdict was upstream of this ratification, not a post-hoc dogfood criterion; with K1 PASSED and the human ratification gate cleared (2026-06-15), this amendment is ratified.

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

> **Amendment note on Q10 (additive — does not delete or weaken the ratified row above)**: the Q10 outcome **SINGLE-TAB MUST** is **conditionally superseded** by the [Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing](#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing). The condition is a backend capability, not a date: for backends that advertise `cross_tab_peers`, messaging addressing becomes capability-conditional per T-§2.1 / T-§2.2 / T-§4.2 while pane-**control** addressing stays caller-tab-scoped; for every backend that does not advertise it — which includes `org-broker` and every backend predating the capability — the Q10 row above stands verbatim and is also the fail-safe default when capability detection is unavailable. The amendment is **PROPOSED** and awaits human ratification; until then the row above is operative for all backends.

### Cluster summary

The 12 Lead-confirmed decisions above cluster as follows:

1. **Required-vs-optional surface boundaries** — `send_keys`, `inspect_pane`, and `list_panes` geometry are REQUIRED (the harness's approval flow, observation-based safety, and balanced-split scheduling cannot be replaced). The Claude-spawn / Codex-spawn convenience helpers and a graceful-exit operation are OPTIONAL — backends MAY provide them, but harnesses MUST be able to drive a generic spawn + flag injection and rely on pane self-`exit`.
2. **Channel-encoding normativity** — HYBRID. Semantic fields (`from_id`, `from_name`, `sent_at`) are contracted; the literal source-string label (`renga-peers`) is backend-defined and must not be hard-coded for routing.
3. **Event-stream guarantees** — `timeout_ms` hard-capped at 30 s; cursor-loss recovery is best-effort with `list_panes` reconciliation, no numeric event-loss SLA (consistent with Set A Q8).
4. **Identity / scope rules** — SINGLE-TAB MUST. Multi-tab addressing is deferred to a future contract amendment.
   - **Amendment note (additive — does not delete or weaken the sentence above)**: that "future contract amendment" is now drafted as the [Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing](#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing). It does **not** lift SINGLE-TAB MUST globally: the deferral is resolved **only for backends that advertise the `cross_tab_peers` capability**, and only for messaging addressing (pane-**control** addressing remains caller-tab-scoped). For every non-advertising backend, including `org-broker`, the sentence above remains the whole rule. The amendment is **PROPOSED** and awaits human ratification.
5. **Failure-mode normativity** — Message delivery is at-most-once (drain semantics). Backend-unreachable conditions normalize to error codes; the current renga ok-text carve-out is transitional and tracked as a follow-up Issue.
6. **Backwards-compatibility commitment** — Surface follows SemVer (Surface 7).

### Amendments log

- **2026-06-15 proposed → 2026-06-15 ratified (Epic #6 / ja push-first sync; Refs transport-lab#23, #18; ja PR #583)**: added the [Ratified amendment (2026-06-15): push-primary delivery via `claude/channel`](#ratified-amendment-2026-06-15-push-primary-delivery-via-claudechannel) section (S3). It realigns the previously pull-only broker delivery (§8.4) to **push-primary (`claude/channel`) + pull-fallback** to match the push-first runtime (0.1.24+), via per-surface additive deltas to §1.2 (dev-channel sidecar), §2.1 (push-primary send), §2.3 (three-state delivery lifecycle, additive to the at-most-once drain guarantee), §5.1/§8.5 (dev-channel approval re-introduced alongside folder-trust), §8.2 (delivery-scoped token scope), §8.4 (delivery model), and §8.8 (`receive_mode` → `push`). **Additive — no prior ratified normative text deleted**; it **supersedes** the broker pull-only semantics of §2.1/§2.3/§5/§8 (pull retained as the fallback layer). Dependency-gated on the §9.5 K1 spike, which **PASSED** (transport-lab #22 CLOSED=PASS) before ratification. Design SoT: transport-lab `docs/design/broker-native-roles.md` §9 / `docs/design/ja-migration-plan.md` §8.
- **2026-06-11 proposed (Epic #6 / ja#514) → 2026-06-14 ratified (Epic #6 broker dogfood #515 passed)**: added [Surface 8 (Broker auth & delivery)](#surface-8-broker-auth--delivery-ratified-amendment) and additive per-surface broker notes on Surfaces 1–6, introducing **`org-broker`** as a second reference backend (pure-backend, tmux/WezTerm adapters). Additive only — no ratified renga normative text changed. Renga stays the default; broker is opt-in (`ORG_TRANSPORT=broker`). Output-field amendments: `cwd` / `kind` / `receive_mode` semantics under broker (§8.8, amends §1.5/§2.2). Error vocabulary extended additively (§8.7). Ratification was gated on the Epic #6 dogfood (transport-lab `docs/design/ja-migration-plan.md` §8 Issue G); the dogfood **passed** (delivery cycle completed end-to-end, runtime main 95855d3, re-verified 2026-06-14). Ratified with two known limitations on the opt-in broker path — folder-trust auto-approval wiring unverified (ja#566) and host-reject silent-drop class (runtime#81) — bounded because renga remains the default + always-available fallback.
- **2026-06-15 proposed (Epic #586 Phase 1; Refs #586)**: added the [Proposed amendment (Epic #586 Phase 1): default transport flip to broker, renga as opt-in fallback](#proposed-amendment-epic-586-phase-1-default-transport-flip-to-broker-renga-as-opt-in-fallback) section. **Supersedes the renga-as-default declaration** of the 2026-06-14 top-header amendment, §8.1, and §8.10 to **broker default, renga opt-in fallback** (`ORG_TRANSPORT` unset = `broker`; `ORG_TRANSPORT=renga` = renga). **Additive — no prior ratified normative text deleted**; renga is **never removed**, rollback safety is preserved (§8.10), and the P-§8.4 pull-fallback triggers (channel-incapable codex peers / claude.ai-login-absent environments) are unchanged. The contract default *declaration* flips here; the runtime default value (`DEFAULT_TRANSPORT`) and the ja pin bump that actually change `resolve()`'s behaviour land separately in **Epic #586 Phase 2** — until then the operative default remains renga. **Status: proposed, pending human ratification** (Phase 1 gate per the Epic #586 promotion plan). Design SoT: `notes/broker-promotion-plan-586.md`.
- **2026-08-06 proposed (renga 2.0 multi-tab follow-through; Refs #823)**: added the [Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing](#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing) section. It introduces a **capability-advertisement mechanism** (T-§cap — previously undefined anywhere in this contract) and, gated on the `cross_tab_peers` capability, **supersedes** the single-tab addressing rules of **§2.1** (`send_message` same-tab delivery → sender-tab-local **name** resolution + cross-tab-capable **numeric id** delivery), **§2.2** (`list_peers` current-tab enumeration → all-tab enumeration, plus the new `same_tab` / `tab` / `tab_name` output fields amending §2.2 and extending §8.8), **§4.2** (both the operational pane-addressed-resolution bullet and SINGLE-TAB MUST → messaging scope and pane-control scope diverge; pane control stays caller-tab-scoped, and the literal `"focused"` resolves inside the caller's tab), and **§4.3**'s focus-dependent visibility consequence (visibility follows the caller's tab, not the focused tab), together with the **Q10** row and **cluster-summary item 4** of this digest. It also pins two previously-open surfaces that cross-tab operation makes load-bearing: cross-tab uniqueness of the numeric ids reported by `list_peers` (T-§2.1) and the tab scope + per-event identity of `poll_events` lifecycle events (T-§3.1, a new subsection). Error vocabulary extended additively under the §6.2 rule (`server_too_old`, `tab_not_found`, `tab_ambiguous`, `tab_limit_reached`, `target_tab_mismatch`; plus a refinement of `pane_not_found` semantics on messaging calls). **Additive — no prior ratified normative text deleted**; the supersession is **conditional on the backend capability, not on a date**, so backends that do not advertise `cross_tab_peers` — including `org-broker` and every backend predating the capability — keep the ratified rules verbatim, which is also the contracted fail-safe default when capability detection is unavailable. **Status: PROPOSED, pending human ratification.** Upstream behavioural source: suisya-systems/renga#287 and its child issues (#288 caller-scoped pane ops, #289 cross-tab peer messaging, #290 spawn tab placement, #291 org sidebar), 2026-08-05; harness-side error handling in [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md).

---

## Proposed amendment (Epic #586 Phase 1): default transport flip to broker, renga as opt-in fallback

> **Status: PROPOSED — pending human ratification** (Epic #586 Phase 1; Refs #586). This section is an **additive amendment** and **deletes no prior ratified normative text above**. It **supersedes the renga-as-default declaration** of the 2026-06-14 top-header amendment, §8.1, and §8.10 ("Coexistence, default, and rollback"): the contracted default transport flips to **`org-broker`** — `ORG_TRANSPORT` **unset = `broker`**; **renga (`renga-peers`) becomes opt-in** via `ORG_TRANSPORT=renga`. The prior renga-as-default wording is **not removed**; it now reads as the pre-#586 historical default and is retained as documentation of the rollback target. This aligns the contract with the Epic #586 promotion plan (`notes/broker-promotion-plan-586.md`) and the default-resolution SoT in `claude_org_runtime.transport` (`DEFAULT_TRANSPORT`).
>
> **This records the contracted decision; it does not assert that the default has already flipped.** The amendment changes only the contract's *default declaration*. The operative runtime default is still renga: the actual behavioural flip — `claude_org_runtime.transport.DEFAULT_TRANSPORT` renga→broker plus the ja runtime-pin bump that consumes it — lands separately in **Epic #586 Phase 2** (a contract amendment alone changes no behaviour; `resolve(env={})` keeps returning `renga` until Phase 2). The resolution order is unchanged (explicit arg > `ORG_TRANSPORT` env > built-in default); only the built-in default value moves, at the single SoT (ja consumes it via `tools/transport.py:resolve()` and does not hardcode it).
>
> **Ratification precondition (NOT yet satisfied at Phase 1)**: the Epic #586 ratification gate (proposed) — stable broker dogfood operation under a broker default, plus completion of the Phase 2 default-value flip (runtime `DEFAULT_TRANSPORT` + ja pin bump) and the Phase 3 prose inversion. The exact gate criteria are a human ratification decision (open point in the promotion plan §6). Until ratified, this section is informational and the ratified renga-as-default text above remains operative.
>
> **Two-frame note (additive, Refs #586 #604 — does not delete or weaken any ratified text):** this section is written in the **contract-declaration frame** and stays PROPOSED pending human ratification, so the *contracted* default remains renga (the ratified 2026-06-14 text above is operative). Two adjacent frames have since advanced and must not be conflated with the contract frame: (1) the **code-constant frame** — `claude_org_runtime.transport.DEFAULT_TRANSPORT` has flipped renga→broker (runtime 0.1.28, Epic #586 Phase 2), so `resolve(env={})` now returns `broker` at the code level; the wording above ("the actual behavioural flip … lands separately in **Epic #586 Phase 2**" / "`resolve(env={})` keeps returning `renga` until Phase 2") describes the Phase-1 authoring moment and is superseded by that landed flip. (2) the **operational frame** — the Issue G #515 coexistence dogfood already **passed** (2026-06-14, ratifying broker as the opt-in second backend, per the top-header and Amendments-log entries above), but the **production broker real-run (Epic #6 Issue G Track 3 — the `org-start` production activation)** is **not yet done**; until that Track-3 activation the operational default *route* stays renga (renga is never removed; rollback-safe). The three frames — contract declaration = renga (pending), code constant = broker (flipped), operational route = renga (until Issue G) — point at different objects and do not conflict; no flip or revert of ratified text is implied by this note.

### F-§8.1 / top-header (default declaration → broker)

The ratified 2026-06-14 top-header amendment and §8.1 state that "Renga … remains the **default** (`ORG_TRANSPORT` unset = `renga`); broker is **opt-in** (`ORG_TRANSPORT=broker`)". **Proposed (supersede, not delete)**: the contracted default flips — `ORG_TRANSPORT` **unset = `broker`**; **`ORG_TRANSPORT=renga` selects renga (opt-in)**. The ratified sentences are retained verbatim as the pre-#586 default of record. The resolution order is otherwise unchanged (explicit arg > `ORG_TRANSPORT` env > built-in default); the built-in default value changes renga → broker at the single SoT (`claude_org_runtime.transport`; ja consumes via `tools/transport.py:resolve()`, no hardcode), and that value change is the Phase 2 deliverable, not this amendment.

### F-§8.10 (Coexistence, default, and rollback → flipped, renga never removed)

§8.10 states "Renga is the **default** and is **never removed** — it is retained as the opt-in fallback / rollback target." **Proposed (supersede, not delete)**: **`org-broker` is now the contracted default**; **renga is never removed** and is **retained as the opt-in fallback / rollback target** (selected by `ORG_TRANSPORT=renga`). The never-removed / always-available / rollback-safety guarantee of §8.10 is **preserved and reaffirmed** — only the unset-default side flips. A `transport=renga` rollback re-points the *next* spawned pane at renga exactly as §8.10 (and the S3 "Rollback note (additive to §8.10)") prescribe; the full-rollback teardown sequence is unchanged.

### F-§rollback-safety (preservation statement)

Both transports continue to use distinct MCP server names and MAY be registered simultaneously (§8.10 unchanged). Flipping the unset default does **not** remove renga, weaken the rollback path, or change the per-pane org-wide single-value selection rule. The push-primary S3 amendment (2026-06-15) and its renga-untouched clause remain operative; this amendment only re-reads its "renga remains the default + always-available fallback" statement as "renga remains the opt-in fallback (no longer the unset default)". The pull-fallback triggers of P-§8.4 — sidecar absent / unhealthy, **channel-incapable peers (codex)**, and claude.ai-login-absent environments — are **unchanged**: those peers stay on pull regardless of which transport is the unset default.

---

## Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing

> **Status: PROPOSED — pending human ratification** (renga 2.0 multi-tab follow-through; Refs #823). This section is an **additive amendment** and **deletes no prior ratified normative text above**. It **supersedes**, *conditionally on a backend capability*, the single-tab addressing rules of **§2.1** (`send_message` — "another pane in the same tab"), **§2.2** (`list_peers` — "in the current tab"), **§4.2** (both the operational pane-addressed-resolution bullet and the SINGLE-TAB MUST tab-scope decision), and **§4.3**'s focus-dependent visibility consequence, together with the **Q10** row and **cluster-summary item 4** of the Decision rationale digest. It additionally **pins** two surfaces the ratified text left open where cross-tab operation makes them load-bearing: `list_peers` numeric-id uniqueness (T-§2.1) and `poll_events` tab scope / per-event identity (T-§3.1). The condition is `cross_tab_peers` (T-§cap): the new rules apply **only** to backends that advertise it. Backends that do **not** advertise it — **including the current `org-broker`** and every backend predating the capability — keep the ratified single-tab rules **verbatim**. The prior wording is not removed; it now reads as the rule for the non-advertising case, which is also the contracted **fail-safe default** whenever the capability cannot be determined.
>
> **Why now**: the renga reference backend gained multi-tab semantics — pane-control operations became caller-tab-scoped, peer messaging became cross-tab with sender-tab-local **name** resolution, `list_peers` began enumerating every tab, and spawn gained tab placement. Upstream source: suisya-systems/renga#287 and its child issues (#288 caller-scoped pane ops, #289 cross-tab peer messaging, #290 spawn tab placement, #291 org sidebar), landed 2026-08-05 and marked BREAKING in the renga CHANGELOG. This amendment records what the **harness contract** requires when such a backend is driven; it does **not** oblige any backend to implement multi-tab, and it does not change the harness's deployment rule that orchestrator-spawned panes live in one tab (T-§4.2).
>
> **Effect before ratification (three rules, to be read together)**: (1) **the ratified single-tab text above remains operative as the backend-conformance contract** — what a backend owes is unchanged by this section until ratification, and a harness assessing conformance measures against the ratified text. (2) **A harness MAY implement the capability-conditional branch ahead of ratification**, because the non-advertising branch — the branch every currently deployed backend takes — is byte-for-byte the ratified behaviour; merely implementing the branch changes no observable behaviour while every enumeration the harness sees is legacy-shaped. (3) **What is gated is not the implementation but the first live drive**: before a harness *acts on* the capability-advertising branch against a real `cross_tab_peers`-advertising backend, the **operational gate** of [T-§ratification](#t-ratification-gate) applies — the harness MUST NOT proceed silently and MUST report to a human first. These do not conflict: (1) fixes what backends owe, (2) licenses dormant harness code on the ratified path, (3) governs the moment that code first becomes load-bearing.
>
> **Harness-side error handling and recovery procedures** for the codes catalogued in T-§6 live in [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md).

### T-§cap Capability advertisement and detection (new mechanism)

The ratified contract defines no capability concept: backends are identified (by MCP server name, §8.1) but never asked what they support. This amendment introduces the mechanism, because every rule below is keyed to it.

- **Capability set**: a backend MAY advertise a set of named capabilities. A capability name is an opaque lowercase identifier; the set is open (new names MAY be added at any time, in the same spirit as the §6.2 error-code rule). This amendment defines the meaning of three names only:
  - **`cross_tab_peers`** — the messaging surface (§2.1 / §2.2) resolves and enumerates across tabs per T-§2.1 / T-§2.2. **This is the sole condition gating every normative rule in this amendment.**
  - **`caller_scope`** — pane-addressed **control** operations (§1.x, `set_pane_identity`, spawn `target`) resolve against the **caller's** tab rather than a single global tab, and `list_panes` returns the caller's tab only. Referenced by T-§4.2 to state the scope divergence; it gates no rule of its own here.
  - **spawn tab placement** (renga spells it `CAP_SPAWN_TAB`) — spawn variants accept a tab selector. Out of scope for this amendment except for its error codes (T-§6).
- **Advertisement channel**: the capability set MUST be discoverable through the backend's own contracted surface, by at least one of:
  1. an explicit capability query or handshake field, if the backend exposes one; or
  2. **contracted observable markers** on existing outputs. For `cross_tab_peers` the marker is normative on both sides, and the two sides are deliberately **asymmetric**. *Conformance requirement (binds the backend)*: a backend advertising `cross_tab_peers` **MUST** populate both `same_tab` and `tab` on **every** `list_peers` record (T-§2.2-fields). *Detection rule (binds the harness)*: the presence of **either** field on **any** record of an enumeration is sufficient to detect the capability. A backend that populates only one field is non-conformant, but the harness MUST still route it onto the capability path — reading a partially-populated record as legacy is the exact failure this amendment exists to prevent. This lets a harness with no capability query detect the capability from data it already fetches, without version sniffing. `caller_scope` and spawn tab placement have **no** observable marker defined here; they are detectable only through channel (1), and absent such a query a harness MUST treat them as not advertised.
- **Version sniffing is not advertisement**: a harness MUST NOT infer a capability from a backend version string, product name, or MCP server name. Capability presence is decided only by (1) or (2) above. (Server name still identifies *which* backend is in use, §8.1; it says nothing about what that backend supports.)
- **Fail-safe default (MUST)**: if the harness cannot determine whether a capability is advertised — no capability query exists, the observable markers are absent, detection errored, or the backend is unknown — it **MUST** treat the capability as **not advertised** and apply the ratified single-tab rules. **Detection failure MUST NOT be read as capability presence.** *Precedence over the marker definition*: this fail-safe applies only where **no** marker is observed at all. Where at least one of `same_tab` / `tab` is present on at least one record of the enumeration, T-§2.2-fields governs, the capability-advertising path applies, and the fail-safe MUST NOT be invoked to override it. The two rules do not compete: "no marker" and "an incomplete marker" are different observations with opposite conclusions.
- **A marker-less enumeration MUST be current-tab-only (conformance requirement, binds the backend)**: a backend whose `list_peers` returns any peer outside the caller's tab **MUST** advertise `cross_tab_peers` and populate the T-§2.2-fields markers. Equivalently: an enumeration in which **every** record omits both `same_tab` and `tab` **MUST** contain only peers of the caller's tab. This is the conformance counterpart of the harness-side detection rule above — without it, a backend could widen enumeration to other tabs while staying marker-less, the harness would correctly conclude "non-advertising" and then wrongly conclude "therefore single-tab", and a harness that treats its own tab as its own organisation would address peers of another organisation. A backend that enumerates cross-tab while omitting the markers is **non-conformant**, and its behaviour is outside what this contract supports.
- **What a legacy determination licenses — and what it does not**: concluding that a backend is non-advertising selects the *addressing rules* (name-based, current-tab) and, by the conformance requirement above, warrants that the enumeration is confined to the caller's tab. It licenses nothing beyond that. In particular it says nothing about whether a peer in the caller's tab **belongs to the harness's own organisation** where the harness's own deployment discipline does not already establish it; ownership remains harness policy, decided on its own terms rather than inferred from capability detection.
- **Independence**: capabilities are independent. Advertising `caller_scope` does not imply `cross_tab_peers`, and neither implies spawn tab placement. Each rule below names the exact capability it depends on.
- **Non-advertising backends are unaffected**: `org-broker` advertises none of these names. The ratified §2.1 / §2.2 / §4.2 rules, the §4 broker note ("Broker honours the SINGLE-TAB MUST scope (§4.2)"), and the §8.7 broker error vocabulary all continue to hold for it **without modification**, and no harness change is required on the broker path.
- **Backends predating the capability**: treated as non-advertising. Where a harness issues a call that a lacking backend cannot honour, the call fails closed with `server_too_old` (T-§6) rather than degrading silently. Note that this code is raised by the **client-side capability gate**, not by the legacy backend — a backend that predates a capability cannot name it (T-§6).

### T-§2.1 send_message — sender-tab-local name resolution, cross-tab numeric ids

§2.1 (ratified) reads: "**Operation**: deliver a text payload to another pane in the same tab."

**Proposed (supersede, not delete) — applies only when the backend advertises `cross_tab_peers`:**

- **Stable names resolve inside the sender's tab only.** A `to_id` that is a stable name MUST be resolved against peers in the **sender's** tab. A name that does not resolve there MUST fail with `pane_not_found`; the backend MUST NOT resolve it to a same-named pane in another tab, and MUST NOT report success for an undelivered message. Names are therefore **not** tab-stable addresses.
- **Numeric pane ids MAY cross tabs.** A `to_id` that is a numeric id (per the §4.1 all-digit disambiguation rule, unchanged) MUST NOT be refused solely because the recipient lives in another tab. Numeric ids obtained from `list_peers` are the **only** tab-stable address form.
- **Numeric ids MUST be unique across all tabs.** A backend advertising `cross_tab_peers` MUST assign the numeric ids it reports on `list_peers` so that they are unique across **every** tab for the lifetime of the backend session. Cross-tab numeric addressing is otherwise not well-defined: with a per-tab id space, a `to_id` copied from another tab's record would silently resolve to the same-numbered pane in the **sender's own** tab, delivering to the wrong recipient without any error. A backend that cannot guarantee cross-tab id uniqueness MUST NOT advertise `cross_tab_peers`.
- **`pane_not_found` on a messaging call is not, by itself, evidence that the recipient is gone.** Because a name can fail purely for tab reasons, harnesses MUST NOT treat this code on a messaging call as a pane-lifecycle signal. The contracted recovery order is: (1) re-run `list_peers`; (2) retry with the recipient's **numeric** peer id **once** — a loop cannot change the outcome and only delays genuine closure detection; (3) **only if the numeric-id send also fails** treat it as a lifecycle condition and confirm via the pane-control / event surfaces (§1.5, §3.1). This ordering is what makes the fail-loud change safe to adopt.
- **Step (3) is inconclusive for a peer known to be in another tab.** Where the enumeration reported `same_tab: false`, absence from §1.5 `list_panes` proves nothing: `list_panes` never shows other tabs and is explicitly **not** superseded (T-§2.2). For such a peer, lifecycle MUST be established either from an observed `pane_exited` (§3.1, as pinned by T-§3.1) or from the peer disappearing from a subsequent `list_peers`. If neither is available the harness MUST record the outcome as **indeterminate** and escalate it; it MUST NOT resolve it as "closed", which would reintroduce through step (3) precisely the mis-detection that steps (1)–(2) exist to prevent.
- **Unchanged**: the §2.1 HYBRID encoding contract (`from_id` / `from_name` / `sent_at` are normative, the source label is a transport tag), the push-mode / pull-mode delivery clauses, the P-§2.1 push-primary broker amendment, and the §6.3 / `backend_unreachable` failure normalization.
- **Non-advertising backends**: the ratified same-tab operation stands verbatim, including `org-broker`'s `peer_not_found` spelling (§8.7).

### T-§2.2 list_peers — all-tab enumeration

§2.2 (ratified) reads: "**Operation**: enumerate other peer-enabled panes in the current tab."

**Proposed (supersede, not delete) — applies only when the backend advertises `cross_tab_peers`:**

- `list_peers` **MUST enumerate peer-enabled panes across all tabs**, not only the caller's, and each record MUST carry the fields of T-§2.2-fields so the caller can partition the result by tab.
- **Deliberate asymmetry with `list_panes`**: §1.5's "**Visibility scope**: current tab only. Panes in other tabs MUST NOT appear." is **unchanged** and is **not** superseded by this amendment. `list_peers` (messaging discovery) widens to all tabs; `list_panes` (pane control and layout) does not. Harnesses MUST NOT use a `list_peers` record as evidence that the peer is visible to `list_panes` or addressable by pane-control operations (T-§4.2).
- **`name` is not unique across an all-tab enumeration.** §1.8 constrains name uniqueness to "another pane in **this tab**", so an all-tab `list_peers` MAY legitimately return several records sharing one `name`. The §4.1 reserved names (`secretary`, `dispatcher`, `curator`, `worker-{task_id}`) collide **by construction** as soon as two organisations run in parallel tabs. Harnesses MUST disambiguate by `same_tab` / `tab` or by the record `id`, and MUST NOT key a lookup, a set-membership test, or a reverse map on `name` alone.
- **Unchanged**: the §2.2 distinction from `list_panes` (excludes the caller, hides geometry) and the §6.3 / `backend_unreachable` failure normalization. The load-bearing "wait for the peer to register" gating use survives but acquires a filter: under all-tab enumeration the gate MUST require `same_tab: true` before accepting a record as the child the caller just spawned, because an unfiltered name match can be satisfied by a same-named peer in another tab and open the gate before the child has registered.
- **Non-advertising backends**: current-tab enumeration stands verbatim, and none of the T-§2.2-fields appear.

### T-§2.2-fields PeerInfo output fields — `same_tab` / `tab` / `tab_name` (amends §2.2; follows the §8.8 precedent)

§2.2's ratified `list_peers` record is "(`id`, optional `name`, optional `role`, `cwd`, optional client kind, optional receive mode (push | poll), optional `summary`)". **Proposed (additive — no existing field is removed, retyped, or made stricter):** three tab fields are added to the per-peer record. §8.8 already establishes the **precedent** that an amendment may pin output-field semantics for a named backend; this section follows that precedent for a named capability. It does **not** extend §8.8's broker-specific rules: `org-broker` advertises no capability here and therefore emits none of these three fields.

| Field | Type | Presence / nullability | Semantics |
|---|---|---|---|
| `same_tab` | boolean | OPTIONAL in general. **Backend conformance**: MUST be present and non-null on every record when the backend advertises `cross_tab_peers`. **Absent (or `null`)** when it does not. **Harness tolerance**: a harness MUST tolerate a non-conformant record that omits it, treating the value as **unknown** — never as a legacy marker (see detection rules). | `true` iff the peer resides in the same tab as the caller; `false` iff it resides in a different tab. Never used as an address. |
| `tab` | tab identifier — an **opaque scalar** (integer or string, backend-defined) | OPTIONAL in general. **Backend conformance**: MUST be present and non-null on every record when the backend advertises `cross_tab_peers`. **Absent (or `null`)** when it does not. **Harness tolerance**: as for `same_tab` — an omitted value is unknown, not legacy. | Identifies the tab the peer resides in. Opaque: harnesses MUST NOT parse it, order it, derive display text from it, or assume it is stable across backend restarts. It is meaningful **only** for equality comparison within a single enumeration. |
| `tab_name` | string | OPTIONAL, and **independently nullable**: it MAY be absent or `null` even on a `cross_tab_peers`-advertising backend — an unnamed tab, or one whose label the backend does not expose, produces no value. | Human-readable tab label, for display and human-facing logs only. **MUST NOT be used as an address** (see `tab_ambiguous`, T-§6). |

**Detection rules (normative — a harness's tab-aware addressing logic depends on exactly these):**

- **Detection is evaluated over the enumeration as a whole, not per record.** A single record carrying either field puts the **backend** on the capability-advertising path for **every** record of that enumeration. A harness MUST NOT apply the legacy rules to some peers of one `list_peers` result and the capability rules to others; the observation is about the backend, and the backend is one thing.
- A harness **MAY** treat "**every** record of the enumeration lacks **both** `same_tab` **and** `tab`" as evidence that the backend does not advertise `cross_tab_peers` — i.e. a non-advertising or capability-predating backend — and fall back to the ratified single-tab rules for that enumeration.
- A harness **MUST NOT** treat a record on which **only one** of `same_tab` / `tab` is absent/null as non-advertising. One field present is sufficient evidence that the backend is on the capability-advertising path; such a record MUST be handled by the capability-advertising rules, with the missing field treated as unknown rather than as a legacy marker.
- `same_tab: false` is a **positive** statement ("this peer is in another tab") and **MUST NOT** be conflated with `same_tab` being absent/null. The two carry opposite information: `false` proves the capability path, absence suggests the legacy path.
- `tab_name` carries **no** capability information in either direction. Its presence or absence MUST NOT influence the detection above.
- These rules are one-directional evidence about the **backend**, never about the **peer's liveness**. A record's field shape says nothing about whether the peer is reachable, alive, or pane-controllable (T-§4.2). It equally says nothing about whether the peer **belongs to the harness's own organisation**: a legacy determination does not license skipping an ownership check (T-§cap).

### T-§3.1 Lifecycle events — tab scope and per-event identity

§3.1 (ratified) contracts the `poll_events` cursor / long-poll semantics and the `pane_started` / `pane_exited` / `events_dropped` / `heartbeat` type vocabulary, but it fixes no per-event field set and says nothing about tabs. Cross-tab lifecycle confirmation (T-§2.1 step (3)) depends on both, so this amendment pins the minimum rather than leaving a load-bearing surface undefined.

**Proposed (additive) — applies only when the backend advertises `cross_tab_peers`:**

- **Tab scope**: `poll_events` MUST report `pane_exited` for **every** pane the caller can address for messaging, panes in other tabs included. Scoping the event stream to the caller's tab while `list_peers` enumerates all tabs would leave cross-tab peers with no observable lifecycle at all and make T-§2.1 step (3) unimplementable.
- **Per-event identity — canonical field names**: every `pane_exited` MUST carry an identifier that joins to an enumeration the harness already holds, **under a contracted field name**, so that a generic harness can perform the join without backend-specific knowledge:
  - **`id`** (REQUIRED) — the pane identifier, in the same id space as `list_panes`.`id`.
  - **`peer_id`** (REQUIRED for a peer-bearing pane, otherwise absent) — the peer identifier, in the same id space as `list_peers`.`id`. Required because under T-§2.2 the harness may be tracking a cross-tab peer that `list_panes` never showed, so the pane-side `id` alone would not join.
  - A backend MAY additionally emit its own aliases, but the two names above MUST be present with these semantics; a harness MUST NOT be required to know any other spelling. (An earlier draft of this amendment left the names backend-defined; that is insufficient — it makes the join unimplementable for a generic harness, and a conforming backend could hide the identifier under a name the harness never reads, leaving cross-tab exits permanently pending.)
  - What the join MUST NOT go through is `name` (next bullet).
- **`name` is not an event key**: because names are unique only within a tab (T-§2.2), a harness MUST NOT match a lifecycle event to a tracked pane by `name` alone unless it has independently established that the pane is in the caller's tab. Matching by name across an all-tab world can retire a live local pane on another tab's exit event.
- **Unchanged**: the §3.1 cursor semantics, the "no numeric event-loss SLA" BEST-EFFORT + reconciliation clause, and the `types`-filter behaviour. §3.1's reconciliation via `list_panes` stays current-tab-only (T-§2.2); for cross-tab peers the reconciliation surface is `list_peers`.

### T-§4.2 Tab scope — messaging scope and pane-control scope diverge

§4.2 (ratified) decides: "**Tab-scope decision**: SINGLE-TAB MUST. All pane-addressed operations resolve only against the current tab. Cross-tab addressing returns `pane_not_found`. Multi-tab support is NOT in this contract revision; if added later it requires a contract amendment with explicit tab-id parameters on every addressed call. Until amended, harnesses MUST launch every orchestrator-spawned pane in the same tab."

§4.2's **first** ratified bullet states the same scope operationally, naming the calls: "All pane-addressed operations — including but not limited to `list_panes`, `focus_pane`, `send_message`, `inspect_pane`, `close_pane`, `send_keys`, `set_pane_identity`, and the `target` parameter of every spawn variant in §1.1–§1.3 — MUST resolve only against panes in the current tab. Cross-tab addressing returns `pane_not_found`." **Both** bullets are in scope of the supersession below, and the split runs through that enumeration: its messaging members (`send_message`, and by §2.2 `list_peers`) move to the messaging scope; every remaining member stays in the pane-control scope, where the quoted sentence continues to hold word for word.

**Proposed (supersede, not delete) — applies only when the backend advertises `cross_tab_peers`:** the single "all pane-addressed operations" scope splits into two scopes that MUST be reasoned about separately.

- **Messaging scope** (`send_message`, `list_peers`): stable-name resolution is **sender-tab-local**; **numeric peer ids MAY cross tabs**; `list_peers` enumerates **all tabs**. (T-§2.1, T-§2.2.)
- **Pane-control scope** (`list_panes`, `close_pane`, `inspect_pane`, `send_keys`, `set_pane_identity`, `focus_pane`, and the `target` parameter of every spawn variant in §1.1–§1.3): **remains caller-tab-scoped**. Cross-tab pane-addressed control returns `pane_not_found`, exactly as the ratified §4.2 sentence says. On a backend advertising `caller_scope` (T-§cap) the resolving tab is the **caller's** tab rather than a single global tab; the *scope width* — one tab — is unchanged.
- **The two scopes are not interchangeable (MUST)**: a peer MAY be addressable for messaging and simultaneously **not** addressable for pane control. Harnesses **MUST NOT** infer pane-control reachability from messaging reachability, from a `list_peers` record existing, or from a successful `send_message`. Conversely, a `pane_not_found` from a pane-control call says nothing about messaging reachability. Where a harness must both message and control a pane, it MUST establish reachability on each surface separately (`list_peers` for messaging, `list_panes` for control).
- **The literal `"focused"` resolves inside the caller's tab.** Under `caller_scope`, the §1.1 default `target` value `"focused"` (and every other use of the literal, §6.1 included) MUST resolve to the focused pane **of the caller's tab**, never to a globally focused pane in another tab. Without this the default `target` would let a spawn land in whichever tab a human happened to be looking at, breaking the same-tab launch rule retained below through the default value alone.
- **Retained from ratified §4.2 (not superseded)**: "harnesses MUST launch every orchestrator-spawned pane in the same tab." Because pane control stays caller-tab-scoped, an orchestrator that placed a child in another tab would keep messaging it while losing `inspect_pane` / `close_pane` / `list_panes` observation of it — the precise failure the ratified sentence prevents. Multi-tab **spawn placement** is therefore **out of scope** for this amendment; only its error codes are catalogued (T-§6). §4.3's positioning of `new_tab` as OPTIONAL and human-driven is likewise unchanged — but see the next bullet for its visibility clause.
- **§4.3's visibility consequence is conditionally superseded.** §4.3 states that "once focus shifts, panes in the previous tab MAY become invisible to MCP calls **until focus returns**" — a consequence of the pre-capability model in which the addressable tab was the *focused* tab. Under a backend advertising `caller_scope`, visibility follows the **caller's** tab and is focus-independent: shifting focus neither hides the caller's own panes nor reveals another tab's, and "focus the tab again" is **not** a recovery step. The OPTIONAL / human-driven status of `new_tab` itself is unaffected. For non-advertising backends the ratified §4.3 sentence stands verbatim.
- **Explicit tab-id parameters**: ratified §4.2 anticipates that multi-tab support "requires a contract amendment with explicit tab-id parameters on every addressed call". This amendment deliberately does **not** add tab-id parameters to addressed calls. It achieves cross-tab messaging through the existing numeric-id address form plus the read-only `tab` / `same_tab` output fields, which keeps every ratified call signature unchanged. A future amendment introducing tab-id **input** parameters remains open.
- **Non-advertising backends**: SINGLE-TAB MUST stands verbatim. `org-broker` advertises neither capability, so the §4 broker note ("Broker honours the SINGLE-TAB MUST scope (§4.2)") remains accurate as written under this amendment, with no rewording needed.

### T-§6 Error vocabulary — capability-conditioned additions (additive under §6.2)

**Additive under the ratified §6.2 rule** ("New codes MAY be added at any time. Callers MUST treat unknown codes as non-fatal (default-branch in case analysis)"). The `[<code>] <human message>` wire form of Surface 6 applies unchanged. No code in §6.1 or §8.7 is renamed, removed, or re-scoped, so no §7 / §6.2 deprecation window is triggered and harnesses driving non-advertising backends never observe these codes.

| Code | Meaning | Issued by | Contracted recovery |
|---|---|---|---|
| `server_too_old` | The backend does not implement a capability the call requires; the newer client fails **closed** rather than silently degrading. | The **client-side capability gate** — the newer client refusing to issue the call, *not* the legacy backend answering it. This is the one row in this table whose issuer is not the backend, and necessarily so: a backend that predates a capability cannot name it. Surfaced on any op whose semantics depend on `cross_tab_peers` / `caller_scope` / spawn tab placement. | **Non-transient — MUST NOT be auto-retried.** The condition cannot clear without a backend upgrade or a client downgrade. Recovery is to fall back to the non-advertising path (T-§cap) for the remainder of the session, or to escalate an upgrade. Retry loops on this code are a harness bug. |
| `tab_not_found` | The referenced tab does not exist — typically a stale tab snapshot. | tab-selecting ops (spawn placement). | **Transient.** Refresh the tab snapshot, then retry the request once against the refreshed set. Do not retry against the stale identifier. |
| `tab_ambiguous` | A tab **name** matched more than one tab. | tab-selecting ops (spawn placement). | Stop addressing by name. Re-resolve to a **unique tab id** and retry with that. Retrying the same name is guaranteed to fail again. |
| `tab_limit_reached` | The backend's tab capacity is exhausted; no new tab can be created. | tab-creating ops (spawn placement, `new_tab`). | **MUST NOT auto-retry the same request.** This is a capacity condition, not a race: escalate it as a capacity decision (close tabs, or place the pane in an existing tab) rather than looping. |
| `target_tab_mismatch` | The resolved target is not in the tab the call asserted / requires. | tab-selecting and tab-asserting ops. | Treat as a **caller-side bug**, not a transient fault: the harness computed an inconsistent (target, tab) pair. Do not retry; escalate for correction. |

- **`pane_not_found` semantic refinement (messaging calls only)**: §6.1's ratified meaning ("Target pane id / name / `"focused"` does not resolve") is unchanged. What this amendment adds is that on a **messaging** call against a `cross_tab_peers`-advertising backend, the code additionally covers "a stable name that does not resolve **within the sender's tab**" — a condition that is **not** a pane-lifecycle signal. The recovery order of T-§2.1 (re-`list_peers` → retry by numeric id → only then treat as lifecycle) applies. On **pane-control** calls the ratified reading is unchanged.
- Harness-side branch tables and the operational recovery procedures for all of the above live in [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md).

### T-§ratification (gate)

This amendment is **PROPOSED**. Ratification is a human decision made on review of this contract change; no dogfood or spike gate is attached **to the ratification**, because at the time of drafting the amendment is behaviour-neutral for every deployed backend: `org-broker` advertises no capability named here, and the fail-safe default of T-§cap routes every undetermined case to the ratified rules. That neutrality is time-bound by construction — it holds exactly until a `cross_tab_peers`-advertising backend is driven, which is the situation the amendment exists for.

**Two gates apply, with different subjects, and they MUST NOT be conflated:**

- **Ratification gate — subject: the backend contract.** Until this amendment is ratified, the ratified §2.1 / §2.2 / §4.2 / §4.3 text, the Q10 row, and cluster-summary item 4 remain operative **as the conformance contract every backend is measured against**. This gate says nothing about harness code: per "Effect before ratification" above, a harness MAY carry the capability-conditional branch before ratification, because on every non-advertising backend that branch reproduces the ratified behaviour byte-for-byte.
- **Operational gate — subject: the harness, and NOT satisfied by ratification.** The **first drive of a capability-advertising backend** MUST be run and reported as a dogfood step before the harness relies on cross-tab addressing in production. Concretely: when a harness's T-§cap detection first puts an enumeration on the capability-advertising path, the harness **MUST halt before acting on cross-tab addressing and report to a human** — at minimum the number of tabs and peers observed and how many of those peers it judged to be in another tab — and proceed only on human confirmation. Ratifying the amendment does not discharge this gate, and satisfying it does not ratify the amendment.

The operational gate is **not** triggered on the non-advertising path. A harness whose enumerations are all legacy-shaped — which is every currently deployed backend, `org-broker` included — proceeds exactly as it does today; nothing about existing operation changes. What a harness MUST NOT do is cross into the capability-advertising path silently.
