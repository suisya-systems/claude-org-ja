# Contract Set D — Backend Interface (Outline)

> **Status**: Outline / skeleton — pending Lead Q&A (2026-05). Structural extraction of the backend surface that the `claude-org` harness depends on, with placeholders left for design decisions the Lead must fill in before this contract is ratified.
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

The backend MUST expose primitives to spawn, enumerate, identify, and close "panes" (process-bearing rectangles in a tiling terminal). Additional operations — `focus_pane`, `inspect_pane`, `send_keys`, `new_tab`, and the per-runtime spawn helpers (`spawn_claude_pane`, `spawn_codex_pane`) — are surfaced today by renga but their required-vs-optional status is open (see the per-section `[TBD by Lead]` markers and the consolidated list at the end of this document). All operations are scoped to a single tab unless explicitly noted (see Surface 4 — Identity & addressing).

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
- **`[TBD by Lead]`** — Whether the harness contract requires a Claude-specific spawn helper at all, or whether the harness is content to drive a generic spawn + flag injection itself. Today every spawn path in `org-start` Step 2/3 and `org-delegate` Step 3 uses the Claude helper, but a backend without one could theoretically be wrapped at the harness layer.

### 1.3 spawn (Codex convenience)

- **Operation**: split + launch a Codex instance pre-registered as a pull-based peer.
- **Inputs**: `direction`, `target`, `cwd`, `name`, `role`, plus pass-through `args[]` (one logical token per array entry).
- The harness's normal flow (`org-start`, `org-delegate`) does not spawn Codex peers. Whether Codex-style pull peers are a required category of the contract or an optional second tier is subsumed by the broader spawn-helper question in 1.2.

### 1.4 close

- **Operation**: terminate the pane's process and remove it.
- **Inputs**: `target` (id, stable name, or `"focused"`).
- **Outputs**: confirmation text (renga: `"Closed pane id=N."`); a single lifecycle event (`pane_exited`) MUST be emitted exactly once per successful close.
- **Error codes**: `pane_not_found`, `pane_vanished`, `last_pane`.
- **`[TBD by Lead]`** — Whether the contract requires a separate "request-graceful-exit" path in addition to process-kill close (renga today only offers process-kill; `org-suspend` flows currently rely on the closing pane to self-`exit` for graceful shutdown).

### 1.5 list_panes

- **Operation**: enumerate every pane in the current tab.
- **Outputs**: per-pane records containing `id`, optional `name`, optional `role`, `focused` flag, terminal geometry (`x`, `y`, `width`, `height` in cell units), `cwd`, optional `summary` (see 2.4), and when known the peer client kind / receive mode (push vs poll).
- **Required for**: balanced-split target selection (`org-delegate` Step 3-1), reconciliation of missed lifecycle events (`.dispatcher/CLAUDE.md` watch-loop Step 3), bootstrap identity verification (`org-start` Step 0.3).
- **Visibility scope**: current tab only. Panes in other tabs MUST NOT appear.
- **`[TBD by Lead]`** — Whether the geometry fields (`x` / `y` / `width` / `height` in cells) are required to be exposed. The harness's balanced-split algorithm depends on them; a backend without geometry would force a different scheduling strategy.

### 1.6 focus

- **Operation**: move keyboard focus to another pane in the current tab.
- **Inputs**: `target`.
- **Constraints**: focus changes are user-visible and disruptive — the harness uses this sparingly. Not load-bearing for correctness; available for human-affordance flows.

### 1.7 inspect_pane

- **Operation**: snapshot the visible screen of another pane (grid scrape).
- **Inputs**: `target`, optional `lines` (trim to bottom N rows), optional `format` (`"text"` | `"grid"`), optional `include_cursor`.
- **Outputs**: rendered screen text and/or structured grid (`{lines: [{row, text}], cursor?: {visible, row, col}}`).
- **Used for**: independent observation of approval prompts and error banners by the dispatcher's watch loop, completion-state read-back during retro (`.dispatcher/CLAUDE.md` Step 4 (a)–(g)).
- **`[TBD by Lead]`** — Whether grid scrape is a REQUIRED surface or OPTIONAL. Some backends may only provide logical messaging; without `inspect_pane`, the dispatcher loses the independent approval/error-detection channel and must rely solely on worker self-reports. The harness today relies on it for confidence-graded notifications.

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
- **`[TBD by Lead]`** — Whether `send_keys` is a REQUIRED surface or whether the contract permits a backend that exposes only logical messaging. The harness today uses `send_keys(enter=true)` to approve the dev-channel prompt at every Claude spawn, and uses `Esc` for over-validation intervention (`org-delegate` Step 5). Removing it forces an alternative dev-channel approval mechanism.

---

## Surface 2: Messaging

The backend MUST provide a logical peer-messaging channel separate from raw PTY input.

### 2.1 send_message

- **Operation**: deliver a text payload to another pane in the same tab.
- **Inputs**: `to_id` (recipient pane id or stable name), `message` (text).
- **Delivery semantics**:
  - For push-mode recipients (Claude Code): the message MUST appear in-band at the recipient as a channel notification carrying source, sender id, sender name, and send timestamp.
  - For pull-mode recipients (Codex): the backend MUST emit a pane-local nudge to the recipient and queue the actual body for retrieval via `check_messages` (2.3).
- **Encoding contract** (renga today): Claude recipients see `<channel source="renga-peers" from_id="..." from_name="..." sent_at="...">…</channel>`. **`[TBD by Lead]`** — Channel-encoding normativity: whether the channel-source string (`renga-peers`) and the `from_id` / `from_name` / `sent_at` attributes are part of the contract or backend-defined. The harness's prompt strings reference `renga-peers` directly today, so a generic backend would force a rename or a contract-fixed name.
- **Failure modes**: returns ok-text `"(message dropped — renga not reachable: <reason>)"` when the backend is unreachable (does NOT raise a JSON-RPC error; see Surface 6.3). All other failures use the `[<code>] <message>` form (Surface 6).

### 2.2 list_peers

- **Operation**: enumerate other peer-enabled panes in the current tab.
- **Outputs**: per-peer records (`id`, optional `name`, optional `role`, `cwd`, optional client kind, optional receive mode (push | poll), optional `summary`).
- **Distinction from `list_panes`**: `list_panes` includes the caller and exposes geometry; `list_peers` excludes the caller and hides geometry. The harness today uses `list_panes` for layout decisions (balanced split) and `list_peers` for "wait for Claude to register" gating in `org-delegate` Step 3-4 — both are load-bearing.
- **Failure modes**: same ok-text exception as 2.1 (`"(no peers — renga not reachable: <reason>)"`).

### 2.3 check_messages

- **Operation**: drain queued peer messages waiting for this client.
- **Outputs**: array of pending messages (sender, body, timestamp).
- **Required for**: pull-mode peers' actual body retrieval after a nudge; push-mode clients use it to retroactively drain anything missed.
- **Drain semantics**: each call returns the queue and clears it; messages are not redelivered.
- **`[TBD by Lead]`** — Whether the contract requires at-least-once delivery (today renga: queued until drained, but no replay after drain) vs at-most-once vs explicit ack.

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
- **`[TBD by Lead]`** — Maximum hard cap for `timeout_ms` (renga clamps to 30000; harness today uses 5000 in the dispatcher loop).
- **`[TBD by Lead]`** — Recovery contract on cursor loss (today: cursor file disappears → up-to-5-seconds of events may be missed; reconciliation falls back to `list_panes`). Should the contract guarantee at-most-N-seconds of missed lifecycle events?

---

## Surface 4: Identity & addressing

### 4.1 Pane identifiers

- Two identifier kinds: numeric `id` (assigned by backend, opaque integer) and optional stable `name` (caller-supplied string).
- **Disambiguation rule**: when a string is passed where an identifier is expected, all-digit strings MUST be interpreted as `id`s. A pane literally named `"7"` is therefore unaddressable by name and MUST be addressed by id. This rule applies to `target` parameters across all pane-control and messaging calls.
- **Name validation**: see Surface 1.8 (`set_pane_identity`).
- **Reserved names** (harness convention, not enforced by backend): `secretary`, `dispatcher`, `curator`, `worker-{task_id}`. The backend MUST NOT reject these names but assigns them no special semantics.

### 4.2 Tab scope

- All pane-addressed operations (`list_panes`, `focus_pane`, `send_message`, `inspect_pane`, `close_pane`, `send_keys`) MUST resolve only against panes in the current tab. Cross-tab addressing returns `pane_not_found`.
- The harness today launches every pane via single-tab `spawn_pane` to satisfy this. Per `references/renga-error-codes.md`, this is a hard constraint (suisya-systems/renga#71) — `new_tab` worker spawns would orphan dispatcher monitoring.
- **`[TBD by Lead]`** — Whether the contract permits multi-tab pane addressing (with a tab-id parameter on every addressed call) or REQUIRES single-tab scope. The harness today depends on single-tab; this also subsumes the question of when a tab ceases to be visible to MCP calls.

### 4.3 new_tab

- **Operation**: create a new tab with a fresh single pane and shift focus to it.
- **Inputs**: optional `command`, `cwd`, `name`, `role`, `label`.
- **Visibility consequence**: once focus shifts, panes in the previous tab MAY become invisible to MCP calls until focus returns (renga's per-tab limit; see 4.2). The harness uses `new_tab` only for human-driven workflows, never for orchestrator-spawned children. Required-vs-optional status of this surface is part of the broader per-surface question carried in the consolidated list.

---

## Surface 5: Authentication / channel

### 5.1 Dev-channel injection

The dev-channel flow has two halves; whether each half is the backend's responsibility depends on the resolution of 1.2 / 1.9:

- **Flag injection**: IF the backend provides the Surface 1.2 spawn-Claude convenience, it MUST inject `--dangerously-load-development-channels server:<channel-name>` itself. IF Surface 1.2 collapses into the generic Surface 1.1 spawn (deferring to the harness/CLI), the harness MUST add the flag in its `command` payload. Today renga implements the former.
- **Prompt approval**: the Claude-side approval prompt (`Load development channel? (Y/n)`) is a Claude Code feature, not a backend feature. IF the backend provides Surface 1.9 (`send_keys`), the orchestrator MAY approve the prompt via `send_keys(enter=true)`. IF Surface 1.9 is omitted, the contract requires an alternative approval path (e.g. a logical-message variant the Claude-side hook can intercept). Today the harness depends on `send_keys`.

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

### 6.2 Stability requirements

- Codes MUST be ABI-stable across backend minor versions. Renames MUST go through a deprecation window with both old and new codes emitted in parallel.
- New codes MAY be added at any time. Callers MUST treat unknown codes as non-fatal (default-branch in case analysis), so backends can extend the vocabulary without breaking conformance.
- The human-facing message MAY change freely; only the `[<code>]` prefix is contracted.

### 6.3 ok-text exceptions (today's renga behavior)

Today renga returns ok-text (NOT a JSON-RPC error) on backend-unreachable for two ops only:
- `list_peers` returns `"(no peers — renga not reachable: <reason>)"`.
- `send_message` returns `"(message dropped — renga not reachable: <reason>)"`.

All other ops raise on unreachable.

- **`[TBD by Lead]`** — Whether this carve-out is contracted (callers branch on the `(no peers` / `(message dropped` prefix today) or whether the contract should normalize all unreachable conditions to errors with a dedicated code (e.g. `backend_unreachable`). Until decided, the harness treats the current renga shape as authoritative.

---

## Open questions consolidated (for Lead fill-in)

The `[TBD by Lead]` markers above are the explicit fill-in points. They cluster into:

1. **Required-vs-optional surface boundaries** (raw PTY `send_keys`, grid-scrape `inspect_pane`, `focus_pane`, `new_tab`, Codex-style pull peers, the Claude-spawn convenience itself).
2. **Channel-encoding normativity** (channel-source string, `from_id` / `from_name` / `sent_at` attributes, channel name `renga-peers`, dev-channel flag injection responsibility).
3. **Event-stream guarantees** (max `timeout_ms`, heartbeat optionality, cursor-loss recovery bound, forward-compat event-type policy).
4. **Identity / scope rules** (single-tab vs multi-tab visibility, reserved-name handling, exact tab-visibility rule).
5. **Failure-mode normativity** (graceful-vs-forced close, `last_pane` semantics, ok-text-on-unreachable exception, at-least-once vs at-most-once message delivery, cwd pre-validation guarantee).
6. **Backwards-compatibility commitment** — Should the contract require semantic versioning of the surface? Today renga's `err_code` ABI promises a deprecation window for code renames, but the broader op-level surface (added params, removed ops) has no formal commitment. **`[TBD by Lead]`**.

These are the design decisions that must be settled before Contract Set D is ratified; the structural skeleton is fixed.
