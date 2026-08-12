# Contract Set B — Delegation Lifecycle

> **Status**: Ratified (2026-05-03). Lead-confirmed decisions for all 14 open questions.
>
> **Scope**: Phase 1 Contract Set B only. Covers delegation begin / in_progress / complete / abort transitions, error propagation, and SUSPEND handling. Role-level responsibilities and boundaries are covered by Set A (`docs/contracts/role-contract.md`). State-file schemas, message-channel contracts, and knowledge flow are tracked in #123–#125 and out of scope here.
>
> **Method**: Each lifecycle state and transition below is filled from empirical sources (current `org-delegate` skill, dispatcher CLAUDE.md, worker template, journal helper). Sentences sourced from current behavior are written as facts. Design decisions ratified by the Lead on 2026-05-03 are stated as contract obligations.
>
> **Empirical sources consulted**:
> - `.claude/skills/org-delegate/SKILL.md` (Step 0–2 worker-dir prep / payload generation, Step 5 progress + completion ack), `.dispatcher/references/spawn-flow.md` (Step 3 spawn / instruction send, Step 4 state record), `.claude/skills/org-pull-request/SKILL.md` (§ 2b-i / 2b-ii / 2c push / PR / merge close), `.claude/skills/org-escalation/SKILL.md` (judgment-escalation register protocol) — carved out of monolithic `org-delegate` per Issue #320
> - `.claude/skills/org-delegate/references/instruction-template.md` (validation depth, completion-report format, SUSPEND clause)
> - `.claude/skills/org-delegate/references/worker-claude-template.md` (worker steady-state behavior, completion / SUSPEND obligations)
> - `.dispatcher/CLAUDE.md` (anomaly forwarding, watch loop, completion-report retro gate, CLOSE_PANE flow)
> - `docs/journal-events.md` (event vocabulary, writer-attribution table)
> - `docs/org-state-schema.md` (Active Work Item terminal-status vocabulary, Worker Directory Registry shape)
> - `docs/internal/phase4-inventory-2026-05-02.md` §2.7 (worker-status state-machine inventory)
> - `tools/journal_append.sh` / `tools/journal_append.py` (accepted event-write schema)
> - `docs/contracts/role-contract.md` — Set A (per-role lifecycle / boundary sections, for cross-reference)
>
> **Refs**: #122 (this issue), parent epic #101.

---

## 1. Lifecycle states

A single delegation moves through the following finite set of contract-level states. The state labels are this contract's vocabulary; they do not all map 1:1 to a literal `Status:` string in the implementation today. The implementation's worker-state-file vocabulary is the smaller set `planned` / `active` / `pane_closed` / `completed` (per `docs/internal/phase4-inventory-2026-05-02.md` §2.7), and `.state/org-state.md` Active Work Items uses `IN_PROGRESS` / `REVIEW` / `COMPLETED` / `ABANDONED`. Some contract states (`pending`, `aborted`) have no dedicated worker-state-file Status today — see the per-row notes.

| # | State | Owner of transition in | Persisted at | Visible journal events |
|---|---|---|---|---|
| 1 | `pending` | secretary (`gen_delegate_payload.py apply`) | `runs.status='queued'` + the `worker_dirs` reservation, committed together with the `delegate_sent` event by `apply` (see §2 T1); the brief / `settings.local.json` / `send_plan.json` are already written in the worker dir. `.state/org-state.md` Worker Directory Registry is regenerated from the DB. Worker state file does NOT yet exist — it is created by the dispatcher in T2. | `delegate_sent` |
| 2 | `dispatched` | dispatcher (after `spawn_claude_pane` succeeds and `send_message` of the instruction) | `.state/workers/worker-{task_id}.md` created with `Status: planned`, then flipped to `active` once the worker is spawned and instructed (per `.dispatcher/references/spawn-flow.md` Step 4 / dispatcher `delegate-plan` helper). `.state/org-state.md` Active Work Items row added by dispatcher. | `worker_spawned` |
| 3 | `in_progress` | worker (begins acting on its instruction) | `.state/workers/worker-{task_id}.md` Progress Log appended on each report (Status remains `active`). | `worker_reported` (per progress message), `anomaly_observed` (if applicable) |
| 4 | `awaiting_review` (a.k.a. `REVIEW`) | secretary (on receipt of completion report from worker) | `.state/org-state.md` Active Work Item set to `REVIEW`. Worker state file Status is NOT retitled today (remains `active`); the worker pane stays open. | `worker_completed`, `worker_review` |
| 5 | `complete` (a.k.a. `COMPLETED`) | secretary (after close-condition met — see §1.5) | `.state/org-state.md` Active Work Item set to `COMPLETED`; Worker Directory Registry updated per pattern; `.state/workers/worker-{task_id}.md` final-update (dispatcher writes `Status: completed` or `pane_closed` per close path). | `worker_closed`, `worktree_removed` (Pattern B), pattern-specific registry updates |
| 6 | `aborted` (a.k.a. `ABANDONED` in `org-state.md`) | dispatcher reports lifecycle exit; secretary classifies and decides | Worker state file: dispatcher writes `Status: pane_closed` (the only literal worker-state-file label for terminal failure today). Active Work Item: secretary, after judging the delegation is abandoned, sets it to `ABANDONED` per `docs/org-state-schema.md` §50 terminal vocabulary. There is no literal worker-state-file `Status: aborted` — `aborted` is the contract-level abstract label for "delegation reached a terminal failure path". For T8 (`SPLIT_CAPACITY_EXCEEDED`) no worker state file is written, since the pane was never spawned. |  `worker_closed` with reason hint, `retro_deferred` (if retro could not run) |

The contract codifies a deliberate two-level state model. The `.state/org-state.md` Active Work Item view is the canonical lifecycle vocabulary — it carries the full `IN_PROGRESS` / `REVIEW` / `COMPLETED` / `ABANDONED` set, so `awaiting_review` IS a distinct contract state at the org-state.md level. The `.state/workers/*.md` Status field uses a coarser subset (`planned` / `active` / `pane_closed` / `completed`) because the worker pane stays open across `awaiting_review`; at the worker-state-file level, `dispatched` and `awaiting_review` are sub-states of `active`. The two views are intentionally not symmetric: org-state.md tracks delegation-from-the-secretary's-POV state, while the worker state file tracks pane-liveness state.

This contract governs *who* flips the worker-state-file `Status` and *when*; the **byte-level shape** of that `Status:` header line is governed by Set C §7 ([`docs/contracts/state-schema-contract.md`](./state-schema-contract.md)), because `claude-org-runtime` machine-reads it as an overflow reservation ledger. `planned` is the only value the runtime distinguishes — every transition out of it (T2 onward) reads identically to the runtime (Refs #835).

The authoritative list of journal events permitted (and required) per lifecycle transition is delegated to `docs/journal-events.md`, consistent with Set A's treatment of the role event registry. Each event entry in that document MUST carry a `required-for-transition` annotation (in addition to the `emitted-by` annotation already tracked by #236), so this contract's per-transition `Journal:` lines can be evaluated mechanically against the registry. A follow-up Issue tracks adding the `required-for-transition` annotation work.

### 1.5 Close-condition (transition into `complete`)

The secretary moves a delegation from `awaiting_review` to `complete` when at least one of the following is met (per `.claude/skills/org-pull-request/SKILL.md` § 2b-ii):

- The PR has been merged (verified via `gh pr view {n} --json mergedAt` or via merge notification).
- The user has explicitly instructed close ("閉じてよい" / "クローズして" / "マージ済み").
- The PR has been idle for 24–48 hours with no review activity (operator judgment; not automated).

The 24–48 hour idle window is a default operator guideline, not a hard contract bound. The secretary may close earlier upon explicit user instruction or extend in the absence of one. No automated timer enforces this bound.

Delegations that do not produce a PR (e.g., investigation-only Pattern C tasks that produce only a report message) follow the same §1.5 close-condition gate. The PR-merged condition is trivially false for such delegations; the user-explicit and 24–48h-idle conditions still apply.

---

## 2. Transitions and triggering events

Each transition below names: **(a)** the event that triggers it, **(b)** which actor executes the transition, **(c)** the state-file write the actor must perform, and **(d)** the journal event the helper must record.

### T1 — `(none) → pending`
- **Trigger**: `tools/gen_delegate_payload.py apply` completes. Apply first materializes every delivery artifact (worktree, brief, `settings.local.json`, `send_plan.json`), then commits the reservation and the journal event in one transaction; that commit **is** the T1 boundary. The Secretary sends the `DELEGATE` message from `send_plan.json` immediately afterwards.
- **Actor**: secretary (via `apply`).
- **State write**: `runs` row upserted with `status='queued'` and the `worker_dirs` reservation registered, both inside the same transaction as the journal event (`tools/gen_delegate_payload.py: _reserve_in_db`). The brief (`CLAUDE.md` / `CLAUDE.local.md`), `.claude/settings.local.json` and `send_plan.json` are already on disk in the worker dir at this point — apply orders them **before** the transaction so a committed `delegate_sent` always has a sendable payload behind it (Issue #928). Active Work Items is NOT touched here; that is T2. `.state/org-state.md` is regenerated from the DB by the post-commit hook.
- **Journal**: `delegate_sent` (`task`, `worker`, `dir`; actor `secretary`, linked to the reserved run).
- **Semantics**: `delegate_sent` records the *commitment* to delegate — "the payload is complete and the `DELEGATE` message is about to be sent". It is **not** a transport-success notification and does not prove the message was delivered or the worker started; actual delivery is proved by T2's `worker_spawned`. A `delegate_sent` with no following `worker_spawned` is therefore a legitimate, and detectable, "committed but never dispatched" state.
- **Re-apply**: `queued` is the only status apply may re-apply onto (a correction of a not-yet-sent delegation); it updates the reservation and does **not** append a second `delegate_sent`, so the event is exactly-once per run row. Apply refuses a `task_id` whose run is in any other status — a new delegation needs a new `task_id`. A failure before the final transaction reserves nothing and journals nothing, so the same `task_id` stays re-appliable.

### T2 — `pending → dispatched`
- **Trigger**: Dispatcher receives `DELEGATE` from secretary and successfully completes the spawn flow (`.dispatcher/references/spawn-flow.md` Step 3: balanced-split target / direction → `spawn_claude_pane` → dev-channel Enter approval → `list_peers` confirms the new peer → `send_message` delivers the worker instruction).
- **Actor**: dispatcher.
- **State write**: `.state/workers/worker-{task_id}.md` is created with `Status: planned` (by `delegate-plan` helper), then flipped to `active` after spawn succeeds (per `.dispatcher/CLAUDE.md` § delegate-plan helper). `.state/org-state.md` Active Work Items row added by dispatcher. (Note: `.state/dispatcher-event-cursor.txt` is the dispatcher's watch-loop cursor for `poll_events(types=["pane_exited","events_dropped"])`; the spawn-time `pane_started` confirmation in Step 3-3 uses a local in-memory cursor, not this file.)
- **Journal**: `worker_spawned` (`worker`, `dir`, `task`). `DELEGATE_COMPLETE` is a peer-message channel only and is NOT journaled — the `worker_spawned` event written by the dispatcher in this step already records the handoff completion, so a separate `delegate_complete` event would be redundant.

### T3 — `dispatched → in_progress`
- **Trigger**: Worker performs `pwd` / reads CLAUDE.md / starts its instruction and emits its first progress message (or first `APPROVAL_BLOCKED` / `ERROR` self-report).
- **Actor**: worker emits report; secretary records it on receipt.
- **State write**: secretary appends to `.state/workers/worker-{task_id}.md` Progress Log on each progress message.
- **Journal**: `worker_reported` (`worker`, `task`, `summary`) per progress event.

### T4 — `in_progress → awaiting_review`
- **Trigger**: Worker sends a structured completion report to `to_id="secretary"` (full mode: completion report with deliverables / outstanding / draft PR text; minimal mode: single-line `done: {sha} {files}`).
- **Actor**: secretary.
- **State write**: `.state/org-state.md` Active Work Item set to `REVIEW`. JSON snapshot regenerated via `dashboard/org_state_converter.py`. `.state/workers/worker-{task_id}.md` Progress Log appended.
- **Journal**: `worker_completed` (`worker`, `task`).
- **Pane discipline**: Worker pane MUST remain open; secretary must NOT instruct dispatcher to `CLOSE_PANE` at this stage (per `.claude/skills/org-pull-request/SKILL.md` § 2b-i and `worker-claude-template.md` § 2).
- **Monitoring-suppression handoff (additive, Issue #658)**: After the ack + REVIEW write, the secretary additionally sends a best-effort, non-blocking `WORKER_COMPLETION_NOTED: worker-{task_id}` to the dispatcher (per `.claude/skills/org-delegate/SKILL.md` Step 5 § 2a). This is a monitoring-suppression receipt notice, NOT a completion determination — it does not alter this transition's ownership (the secretary alone owns `awaiting_review`) and the dispatcher does not consume it to decide completion. Its sole effect is to set `completion_reported_at` in the dispatcher's `worker-idle-state.json` so the `pane_output_without_peer_msg` detector skips the completed worker's normal review-idle. The secretary does NOT wait for a dispatcher reply; if the message is dropped the detector merely stays active (safe side). This is an additive handoff and does not change the ratified `worker_completed` journal obligation above.

### T5 — `awaiting_review → complete` (close-condition met)
- **Trigger**: §1.5 close-condition met AND user has approved (or condition is auto-satisfied via merge / idle).
- **Actor**: secretary executes the close, then sends `CLOSE_PANE: {pane_id}` to dispatcher; dispatcher executes the close per `.dispatcher/references/pane-close.md` (retro Steps 1–2 first, then `mcp__renga-peers__close_pane`).
- **State write**:
  - secretary: `.state/org-state.md` Active Work Item → `COMPLETED`; pattern-specific Worker Directory Registry update (Pattern A → `available`; Pattern B → row removed and worktree removed via `git worktree remove`; Pattern C → row removed); JSON snapshot regenerated.
  - dispatcher: `.state/workers/worker-{task_id}.md` final update; pane closed via `close_pane`.
- **Journal**: `worker_closed` (`worker`, `pane_id`). Pattern B additionally writes `worktree_removed` (`path`, `task`).

### T6 — `awaiting_review → in_progress` (review feedback / depth switch)
- **Trigger**: User issues feedback / change request on the completion report or PR (handled per `.claude/skills/org-pull-request/SKILL.md` § 2c), OR secretary intervenes (per `.claude/skills/org-delegate/SKILL.md` ワーカー監視と介入判定) and re-instructs in the same pane.
- **Actor**: secretary `send_message`s the same `worker-{task_id}` pane with the additional instruction.
- **State write**: `.state/org-state.md` Active Work Item back to `IN_PROGRESS`; `.state/workers/worker-{task_id}.md` Progress Log appended.
- **Pane discipline**: New worker MUST NOT be re-spawned for in-scope review feedback (re-spawn is rejected by the contract because Issue/diff/judgment context would be lost).
- **Monitoring-suppression release (additive, Issue #658)**: If the worker had reported completion (T4 sent `WORKER_COMPLETION_NOTED`), the secretary MUST send a best-effort, non-blocking `WORKER_REOPENED: worker-{task_id}` to the dispatcher BEFORE the re-instruction (per `.claude/skills/org-pull-request/SKILL.md` § 2c), clearing `completion_reported_at` so the `pane_output_without_peer_msg` detector resumes for the review-fix work. Because the re-instruction is a direct secretary→worker send with the dispatcher off the path, an explicit release is needed to re-arm monitoring. `WORKER_REOPENED` is only best-effort, and unlike `WORKER_COMPLETION_NOTED` a dropped release fails UNSAFE (monitoring stays suppressed through review-fix). The reliable backstop is the deterministic `runs.status` `review → in_use` transition this transition already performs via `StateWriter` (peer-message-independent): the dispatcher's Step 5.2 gate skips only while `completion_reported_at != null` AND `runs.status == 'review'`, and self-heal-clears the flag when it observes `runs.status == 'in_use'` with the flag still set. So `WORKER_REOPENED` is the fast-path and the DB status transition is the fail-safe. The release is lifecycle-event-based (no timeout), matching the CLOSE_PANE / pane-gone record deletion; it does not change the ratified state-write / pane-discipline obligations above.

### T7 — `* → aborted` (worker pane exits without completion)
- **Trigger**: Dispatcher's `poll_events` sees `pane_exited` for `name == "worker-{task_id}"`, OR `list_panes` reconciliation finds the pane gone. The dispatcher does NOT itself decide whether the delegation was completed — it reports the lifecycle fact only (per `.dispatcher/references/worker-monitoring.md` § (1) and § list_panes reconciliation).
- **Actor**: dispatcher writes the pane-closed fact and notifies; secretary then determines completion vs. unexpected-exit by inspecting the renga-peers message history (last `COMPLETED` report present? if not, treat as worker accident).
- **State write**: dispatcher writes `.state/workers/worker-{task_id}.md` `Status: pane_closed`. Secretary, after judging the task is abandoned (no completion report and user does not re-delegate), sets the Active Work Item terminal status to `ABANDONED` (per `docs/org-state-schema.md` §50 vocabulary).
- **Journal**: `worker_closed` (with reason hint); separately, `WORKER_PANE_EXITED` is a peer-message channel only (not journaled today).
- **Re-delegation**: Automatic re-delegation is not contracted. After an unexpected pane exit, the secretary determines per-task whether to abandon, ask the user, or re-delegate; the decision is not bounded by an automatic retry counter.
- **Two-stage absence determination (additive, 2026-08-09)**: The `list_panes` reconciliation disjunct of the Trigger above is a first stage only. A pane's disappearance from `list_panes` is NOT by itself an effective trigger for this transition, because `list_panes` is one tab wide and — wherever `caller_scope` is not established — the tab it resolves against is the **focused** one, so a focus change alone produces the same observation. That general fact rests on ratified text (`docs/contracts/backend-interface-contract.md` §1.5 "Visibility scope" and §4.3 "Visibility consequence"), which is why the two-stage reading is not itself a capability-era novelty; the still-PROPOSED T-§4.2 is cited only for the narrower capability-era prohibition it adds, forbidding absence from such an enumeration, or a `pane_not_found` from such a call, from being read as evidence that a pane has exited. The corroboration requirement stated next is not itself conditioned on that capability: it comes from §1-2-c of the reference below. That disjunct becomes effective only when the three-valued same-tab determination in `.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md` §1-2 resolves the tracked worker to **absent** and one of the two pane-side corroborations §1-2-c requires is available: (i) an observed same-tab `pane_exited`, or (ii) the disappearance of the previously recorded numeric id from a subsequent `list_peers`. Because (i) coincides with the `pane_exited` disjunct, (ii) is what remains on the reconciliation-only path; the dispatcher-side procedure that applies all of this is `.dispatcher/references/worker-monitoring.md` (3-a). **The `pane_exited` disjunct itself is not narrowed by the determination**: receipt of the event remains sufficient on its own, whatever §1-2 reports (`docs/contracts/backend-interface-contract.md` §3.1 — emitted exactly once per successful close and once per crash). What this amendment narrows there is attribution only: binding a lifecycle event to the tracked worker is done by tracked pane / peer numeric id, never by `name` alone (T-§3.1: "a harness MUST NOT match a lifecycle event to a tracked pane by `name` alone unless it has independently established that the pane is in the caller's tab" — the tracked numeric id is how this amendment discharges that condition), and the `name == "worker-{task_id}"` form in the ratified Trigger above stands as the display and candidate-gathering form. While the outcome is `unknown` or `indeterminate` — the determination unresolved on the reconciliation path, or a `pane_exited` whose attribution cannot be resolved — **this transition does not occur**: no `Status: pane_closed` write, no `WORKER_PANE_EXITED`, no Active Work Item change, and no `worker_closed` journal event. The only output is the observation-unavailable report of `.dispatcher/references/worker-monitoring.md` (P4), and the next watch-loop cycle re-evaluates (T-§2.1: "If neither is available the harness MUST record the outcome as **indeterminate** and escalate it; it MUST NOT resolve it as 'closed'"). **Narrowing scope**: this amendment binds only capability-shaped enumerations (a backend advertising `same_tab` / `tab`) whose first-drive operational gate is recorded (§2 of the same reference); legacy-shaped enumerations — every currently deployed backend, `org-broker` included — proceed exactly as the ratified text above describes, under the corroboration procedure they already follow today (`.dispatcher/references/worker-monitoring.md` (3-a)). A capability-shaped enumeration whose first-drive gate is **not** recorded is a third case, and it does not fall back to an un-narrowed reading either: §1-2-a of the same reference discards such an enumeration before the determination runs, and the dispatcher procedure degrades so that the reconciliation-only path yields an observation-unavailable rather than an effective trigger (same (3-a)). No capability-shaped configuration, recorded or not, reads a bare `list_panes` absence as an effective trigger. Actor, state-write, Journal and Re-delegation obligations are otherwise unchanged.

### T8 — `* → aborted` (`SPLIT_CAPACITY_EXCEEDED`)
- **Trigger**: Dispatcher's balanced-split filter returns zero candidates (per `.dispatcher/references/spawn-flow.md` § 3-1c).
- **Actor**: dispatcher.
- **State write**: No worker pane is spawned; `.state/dispatcher/inbox/{task_id}.json` may remain on disk for re-attempt; `.state/workers/worker-{task_id}.md` is NOT written (no pane existed). On receipt of `SPLIT_CAPACITY_EXCEEDED`, the secretary MUST release the Worker Directory Registry row reserved in T1 (set Status back to `available` for Pattern A, or remove the row for Pattern B/C) so the `in_use` reservation does not leak; no Active Work Item row need be reverted because T2 has not yet added one.
- **Journal**: Today this case is signalled ONLY via the `SPLIT_CAPACITY_EXCEEDED` peer message to secretary; there is no corresponding journal event in `docs/journal-events.md`. The follow-up `required-for-transition` annotation work on the registry (see §1) will decide whether to introduce a `delegate_failed` (or equivalent) event for this transition; until then, the peer message is the sole record.
- **Liveness**: Dispatcher watch loop continues; only this one delegation is aborted (`exit` / `return` of dispatcher pane is forbidden).

---

## 3. Error propagation

Five error / anomaly classes are recognized. Each lists: who detects, who is notified, retry semantics, and abort conditions.

### E1 — Worker pane exits unexpectedly
- **Detection**: dispatcher's `poll_events` (`pane_exited` for `role=="worker"`); fallback via `list_panes` reconciliation each watch-loop cycle. The dispatcher does NOT consult journal `worker_completed` (which is a secretary-written event per `docs/journal-events.md`); it forwards the raw lifecycle fact and lets the secretary classify expected-vs-unexpected exit.
- **Notification path**: dispatcher → secretary via `mcp__renga-peers__send_message(to_id="secretary")` with body `WORKER_PANE_EXITED: {name} (id={id}) のペインが閉じました。リコンサイル要。`
- **Retry**: Not automatic. Secretary asks user whether to re-delegate or abandon.
- **Abort condition**: User explicitly declines re-delegation, OR secretary determines task is no longer relevant. (Per §2 T7, no automatic retry counter is contracted.)
- **Two-stage absence determination (additive, 2026-08-09)**: The `list_panes` reconciliation fallback in the Detection line above is a first stage only; a worker pane missing from a single watch-loop `list_panes` is not by itself a detection of this error class. It becomes one only when the three-valued same-tab determination in `.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md` §1-2 resolves the tracked worker to **absent** and one of its §1-2-c pane-side corroborations holds — (i) an observed same-tab `pane_exited`, or (ii) the disappearance of the previously recorded numeric id from a subsequent `list_peers` — per the dispatcher procedure in `.dispatcher/references/worker-monitoring.md` (3-a). The `poll_events` half of the Detection line is not narrowed by the determination: receipt of `pane_exited` remains sufficient terminal evidence on its own, whatever §1-2 reports (`docs/contracts/backend-interface-contract.md` §3.1: emitted exactly once per successful close and once per crash) — which is why (i) is the corroboration that coincides with it, leaving (ii) for the reconciliation-only path. What this amendment narrows instead is *which tracked pane an event or an absence is attributed to*, decided by the tracked pane / peer numeric id and never by `name` alone (T-§3.1). Under `unknown` / `indeterminate` no E1 detection is declared and the notification path above therefore does not fire: no `WORKER_PANE_EXITED`, no `Status: pane_closed` write, no Active Work Item change, no `worker_closed` event. What the dispatcher emits instead is the `OBSERVATION_UNAVAILABLE` report of `.dispatcher/references/worker-monitoring.md` (P4) — de-duplicated per degradation interval on `(worker, source, kind=observation_unavailable)` and released by an `observation_recovered` on the same `source` — while the worker stays under monitoring and active for the next cycle. **Who resolves a persistent `indeterminate`**: an `unknown` clears on its own once the same-tab field becomes readable, but an `indeterminate` arising from the absence of a recorded numeric id does not — the corroboration (ii) can never become available afterwards, so the dispatcher would re-report the same cycle forever. Resolving it is the secretary's, per the same division of labour the ratified Detection line states (the dispatcher forwards lifecycle facts; the secretary classifies): the dispatcher's report says so explicitly and the secretary, on judging the delegation terminal, performs the T7 state write itself. The dispatcher MUST NOT write the terminal state in its place. **Narrowing scope**: capability-shaped enumerations whose first-drive operational gate is recorded, only; legacy-shaped enumerations (every currently deployed backend, `org-broker` included) detect exactly as the ratified text above describes, under the corroboration procedure they already follow today (`.dispatcher/references/worker-monitoring.md` (3-a)). A capability-shaped enumeration whose first-drive gate is not recorded is a third case that likewise yields no detection from a bare absence: the enumeration is discarded before the determination runs (§1-2-a of the same reference) and the reconciliation-only path degrades to the same observation-unavailable report. Retry and abort-condition semantics are unchanged.

### E2 — `APPROVAL_BLOCKED` / `ERROR_DETECTED` from dispatcher inspect
- **Detection**: dispatcher `inspect_pane` matches one of the anchored regexes in `.dispatcher/references/worker-monitoring.md` § (b) (approval prompt) or substring set in § (d) (error banner).
- **Notification path**: dispatcher → secretary; tagged with `source=inspect` and `confidence=high|n/a`. De-duplication: 30-second window keyed on `(worker, kind)` against `event=notify_sent` ledger; `anomaly_observed` rows do NOT count toward de-dup.
- **Retry**: Notification is at-least-once. The underlying anomaly is human-resolved (secretary asks user how to proceed and forwards `send_keys` instructions to the worker pane via the dispatcher / directly).
- **Abort condition**: None automatic; only human decision aborts.

### E3 — Worker self-reports `ERROR` / `APPROVAL_BLOCKED` via `to_id="secretary"`
- **Detection**: dispatcher receives via `check_messages` (and forwards), OR secretary receives directly. Both channels are independent (per `.dispatcher/references/worker-monitoring.md` § (g) "両チャネル独立稼働で OK").
- **Notification path**: as in E2; tagged `source=self_report`, `confidence=n/a`.
- **De-dup**: same 30-second `(worker, kind)` window applies, so inspect (E2) and self-report (E3) are not double-notified.
- **Halting**: A self-report `ERROR` / `APPROVAL_BLOCKED` (`source=self_report`, `confidence=n/a`) without inspect corroboration produces a notification only. Halting the worker (e.g., via `Esc` send) is a human decision; the secretary may issue it but it is not automated by the harness.

### E4 — CI fails on PR
- **Detection**: `tools/pr-watch.{ps1,sh}` writes a `ci_completed` event to the `events` table in `.state/state.db` on completion (M4 cutover, Issue #267 — `.state/journal.jsonl` is decommissioned). Per Secretary CLAUDE.md § PR 後の CI 監視. Failure is signalled within the event payload (`status` ∈ `{passed, failed, incomplete, indeterminate, canceled}`). Issue #413: a single `ci_completed` event is emitted per pr_watch invocation, only after the *final* verdict — the resolver retries transient empty / pending / `gh exit 8` observations AND transient JSON-probe failures (unparseable stdout / malformed JSON) with exponential backoff until a deterministic verdict (`passed` / `failed`) appears or the retry budget is exhausted. On exhaustion the final event records the full elapsed `duration_sec` and one of two distinct words (Issue #685): `incomplete` when the checks were read but stayed pending, or `indeterminate` when NO parseable probe response was ever observed (a gh outage — the verdict could not be read). `indeterminate` carries an explicit retry schedule (`retry_recommended` / `retry_after_sec` / `probe_attempts`) so the secretary re-invokes pr_watch rather than treating the merge gate as stalled; a real red therefore never degrades to a stalled `incomplete` on a transient probe blip.
- **Notification path**: secretary inspects the events table (or is notified out-of-band by `gh pr checks --watch` exit / the `CI_COMPLETED` peer message) and decides whether to send fix instructions back to the same worker pane (T6 review-feedback path).
- **Retry**: Same-pane fix is the default (per `worker-claude-template.md` § 2 "ペインを保持してレビュー指摘待機"). Re-spawn of a fresh worker is forbidden.
- **Abort condition**: User declines further work, OR worker fix loop exceeds intervention triggers in `.claude/skills/org-delegate/SKILL.md` ワーカー監視と介入判定 (30 min same-phase / 1 h silent / Codex round-4).

### E5 — Codex Blocker / Major (worker self-review, full mode)
- **Detection**: Worker's own `codex exec review` (review surface) diff self-review.
- **Handling rule**: 3-round cap on same-category Blocker/Major findings; on 4th round the worker MUST stop and report to secretary "design issue — request scope reduction" (per `worker-claude-template.md` § Codex セルフレビュー手順).
- **Notification path**: worker → secretary direct.
- **Retry / abort**: Retry is bounded by the 3-round cap; abort condition is the round-4 declaration.
- **Applicability**: The 3-round same-category Blocker/Major cap on Codex self-review is contracted only when `codex` is available in the worker environment. Workers in a `codex`-unavailable environment skip the round-discipline entirely (per `worker-claude-template.md`).

### Error-class summary table

| Class | Detector | Notifier | De-dup | Auto-abort? |
|---|---|---|---|---|
| E1 pane-exited | dispatcher poll_events | dispatcher → secretary | n/a | no (human decides) |
| E2 inspect anomaly | dispatcher inspect_pane | dispatcher → secretary | 30s `(worker, kind)` | no |
| E3 worker self-report | worker → secretary (also dispatcher.check_messages) | secretary direct (or dispatcher forward) | 30s `(worker, kind)`, shared with E2 | no |
| E4 CI failure | `pr-watch` script (DB event `ci_completed`) | secretary | n/a | no |
| E5 Codex 4th-round | worker (self) | worker → secretary | n/a | yes — worker stops at 4th round |

**Note on the E1 row (additive, 2026-08-09)**: its Detector / Notifier / De-dup cells describe the case where the pane exit has actually been established. Per the two-stage determination added to §2 T7 and §3 E1 above, an observation that merely resembles a pane exit — a pane missing from one `list_panes` cycle, or a `pane_exited` whose attribution to a tracked worker is unresolved — is not an E1 detection, produces no dispatcher → secretary `WORKER_PANE_EXITED`, and is reported instead as the `OBSERVATION_UNAVAILABLE` of `.dispatcher/references/worker-monitoring.md` (P4), which carries its own `(worker, source, kind=observation_unavailable)` de-dup key rather than this row's `n/a`. The row is unchanged and remains correct for detections that reach the effective trigger.

The authoritative list of inspect-detected approval-prompt regexes is maintained in `.dispatcher/references/worker-monitoring.md` § (b), which is the single source of truth for this registry (consistent with Set A's treatment of dispatcher constraint surfaces). Updates to the regex set are made there; this contract refers to it rather than duplicating the list.

---

## 4. SUSPEND handling

`SUSPEND:` is a peer message that triggers an in-flight delegation to halt and report. The contract surface is small but precisely scoped.

### 4.1 Who may issue
- Only the secretary may issue `SUSPEND:` to a worker (per `worker-claude-template.md` § SUSPEND対応 and `instruction-template.md` § SUSPEND 対応). The dispatcher MUST NOT originate or relay `SUSPEND:` messages — the secretary's identity as the SUSPEND-issuer is part of the human-judgment chain of authority, and routing SUSPEND through the dispatcher would dilute that boundary.

### 4.2 Worker obligations on receipt
On receiving a message whose body begins with `SUSPEND:`, the worker MUST immediately (i.e., before continuing the in-flight tool call where safe) report the following four-item prose schema to `to_id="secretary"`:
1. Work completed up to this point.
2. Modified files (committed vs. uncommitted, listed separately).
3. Planned next step (the action the worker would have taken next).
4. Blockers / unresolved issues.

The four-item prose schema is authoritative. The worker MUST NOT auto-`git add` / `git commit` uncommitted changes before reporting; uncommitted changes are reported as-is under item 2, and resume re-evaluates them in the same pane (see §4.4). Auto-committing on SUSPEND would risk producing unreviewed commits and would conflict with same-pane resume semantics.

### 4.3 State transition under SUSPEND
SUSPEND does not introduce a distinct `suspended` lifecycle state. The Active Work Item remains `IN_PROGRESS`; the worker pane stays open. Discrimination between "worker is silently mid-work" and "worker has been told to halt and is awaiting resume instruction" is recovered from the worker's most recent SUSPEND report message and Progress Log, not from a state-file label. This keeps the state vocabulary compact and avoids requiring `org-resume` to reason about a fourth org-state.md status value.

### 4.4 Resume contract
On `/org-resume`, the secretary inspects `.state/workers/worker-*.md` and decides per worker whether to send a resume instruction. Same-pane resume is the default; fresh-pane resume is permitted only at the secretary's discretion as a documented exception. Fresh-pane resume loses Issue / diff / judgment context (same rationale as the T6 review-feedback path), so it is reserved for cases where the original pane is no longer recoverable.

The canonical resume input is `.state/workers/worker-{task_id}.md` Progress Log together with the worker's most recent SUSPEND report message. No additional persisted artifact is required; the SUSPEND report and Progress Log together carry sufficient context for the worker to resume without re-reading the original Issue or task spec from scratch.

### 4.5 SUSPEND vs `/org-suspend`
- `/org-suspend` (org-wide shutdown) is distinct from per-worker `SUSPEND:`. `/org-suspend` flushes secretary / dispatcher / curator state and graceful-closes panes; per-worker `SUSPEND:` is a single-worker pause that keeps panes alive.
- During `/org-suspend`, the secretary MUST issue `SUSPEND:` to every active worker and collect each worker's checkpoint BEFORE flushing org-state and graceful-closing panes. The checkpoint is satisfied either by (a) the worker's SUSPEND report received within the skill's response-wait window, or (b) the Phase 2 fallback (`inspect_pane` screen-scrape plus `git status` / `git diff --stat` / `git log` from the worker dir) for workers that did not respond. This guarantees state-flush integrity at resume time — without this ordering, in-flight worker progress could be lost or `.state/workers/*.md` Progress Logs could be desynchronized from the worker's actual checkpoint. The `/org-suspend` skill (`.claude/skills/org-suspend/SKILL.md`) is the operational source of truth for the wait-window length and Phase 2 fallback procedure; this contract pins only the ordering invariant.

---

## Decision rationale digest

The 14 decisions ratified on 2026-05-03 cluster as follows:

1. **State model (§1, §1, §4.3)** — A two-level state model is codified: the org-state.md Active Work Item view is canonical (`IN_PROGRESS` / `REVIEW` / `COMPLETED` / `ABANDONED`), and the worker-state-file view is a deliberately coarser pane-liveness subset. SUSPEND does NOT introduce a distinct `suspended` state — the SUSPEND report and Progress Log carry the discrimination.
2. **Closed-set enumerations (§1, §3)** — Journal events and approval-prompt regexes are delegated to their existing single-source-of-truth files (`docs/journal-events.md`, `.dispatcher/references/worker-monitoring.md` § (b)) rather than duplicated here. A follow-up Issue tracks adding the `required-for-transition` annotation to `docs/journal-events.md`.
3. **Retry bounds (§2 T7, §3 E5)** — Neither the post-pane-exit re-delegation cap nor the Codex round-cap is broadened: re-delegation is per-task secretary judgment with no counter, and the Codex 3-round cap applies only when `codex` is available in the worker environment.
4. **Close-condition (§1.5)** — The 24–48h idle threshold remains an operator guideline, not a hard bound. No-PR delegations follow the same close gate (PR-merged condition is trivially false for them).
5. **SUSPEND semantics (§4.1, §4.2, §4.4, §4.5)** — Only the secretary may issue SUSPEND (the dispatcher MUST NOT relay). The four-item prose report is authoritative with no auto-commit obligation. Same-pane resume is the default; the canonical resume input is the Progress Log plus the most recent SUSPEND report. `/org-suspend` MUST first issue per-worker SUSPEND and collect reports before flushing state.
6. **Notification halting (§3 E3)** — Self-report-only `ERROR` / `APPROVAL_BLOCKED` produces a notification only; halting the worker is a human decision, not automated.
7. **Handoff journaling (§2 T2)** — `DELEGATE_COMPLETE` is NOT journaled; the existing `worker_spawned` event already records dispatcher-side handoff completion.
