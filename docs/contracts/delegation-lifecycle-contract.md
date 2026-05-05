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
| 1 | `pending` | secretary (drafts `DELEGATE`) | `.state/dispatcher/inbox/{task_id}.json` (task spec written for `delegate-plan`); `.state/org-state.md` Worker Directory Registry row added with Status `in_use` (per `org-delegate` Step 1.5). Worker state file does NOT yet exist — it is created by the dispatcher in T2. | `delegate_sent` |
| 2 | `dispatched` | dispatcher (after `spawn_claude_pane` succeeds and `send_message` of the instruction) | `.state/workers/worker-{task_id}.md` created with `Status: planned`, then flipped to `active` once the worker is spawned and instructed (per `.dispatcher/references/spawn-flow.md` Step 4 / dispatcher `delegate-plan` helper). `.state/org-state.md` Active Work Items row added by dispatcher. | `worker_spawned` |
| 3 | `in_progress` | worker (begins acting on its instruction) | `.state/workers/worker-{task_id}.md` Progress Log appended on each report (Status remains `active`). | `worker_reported` (per progress message), `anomaly_observed` (if applicable) |
| 4 | `awaiting_review` (a.k.a. `REVIEW`) | secretary (on receipt of completion report from worker) | `.state/org-state.md` Active Work Item set to `REVIEW`. Worker state file Status is NOT retitled today (remains `active`); the worker pane stays open. | `worker_completed`, `worker_review` |
| 5 | `complete` (a.k.a. `COMPLETED`) | secretary (after close-condition met — see §1.5) | `.state/org-state.md` Active Work Item set to `COMPLETED`; Worker Directory Registry updated per pattern; `.state/workers/worker-{task_id}.md` final-update (dispatcher writes `Status: completed` or `pane_closed` per close path). | `worker_closed`, `worktree_removed` (Pattern B), pattern-specific registry updates |
| 6 | `aborted` (a.k.a. `ABANDONED` in `org-state.md`) | dispatcher reports lifecycle exit; secretary classifies and decides | Worker state file: dispatcher writes `Status: pane_closed` (the only literal worker-state-file label for terminal failure today). Active Work Item: secretary, after judging the delegation is abandoned, sets it to `ABANDONED` per `docs/org-state-schema.md` §50 terminal vocabulary. There is no literal worker-state-file `Status: aborted` — `aborted` is the contract-level abstract label for "delegation reached a terminal failure path". For T8 (`SPLIT_CAPACITY_EXCEEDED`) no worker state file is written, since the pane was never spawned. |  `worker_closed` with reason hint, `retro_deferred` (if retro could not run) |

The contract codifies a deliberate two-level state model. The `.state/org-state.md` Active Work Item view is the canonical lifecycle vocabulary — it carries the full `IN_PROGRESS` / `REVIEW` / `COMPLETED` / `ABANDONED` set, so `awaiting_review` IS a distinct contract state at the org-state.md level. The `.state/workers/*.md` Status field uses a coarser subset (`planned` / `active` / `pane_closed` / `completed`) because the worker pane stays open across `awaiting_review`; at the worker-state-file level, `dispatched` and `awaiting_review` are sub-states of `active`. The two views are intentionally not symmetric: org-state.md tracks delegation-from-the-secretary's-POV state, while the worker state file tracks pane-liveness state.

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
- **Trigger**: Secretary completes `org-delegate` Steps 0–1.5 for a task and is about to send the `DELEGATE` message to the dispatcher.
- **Actor**: secretary.
- **State write**: `.state/dispatcher/inbox/{task_id}.json` is written with the task spec consumed by `claude-org-runtime dispatcher delegate-plan`. CLAUDE.md / settings.local.json are placed in the worker dir (Step 1.5). Worker Directory Registry row added with Status `in_use`.
- **Journal**: `delegate_sent` (`task`, `worker`, `dir`).

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

### T7 — `* → aborted` (worker pane exits without completion)
- **Trigger**: Dispatcher's `poll_events` sees `pane_exited` for `name == "worker-{task_id}"`, OR `list_panes` reconciliation finds the pane gone. The dispatcher does NOT itself decide whether the delegation was completed — it reports the lifecycle fact only (per `.dispatcher/references/worker-monitoring.md` § (1) and § list_panes reconciliation).
- **Actor**: dispatcher writes the pane-closed fact and notifies; secretary then determines completion vs. unexpected-exit by inspecting the renga-peers message history (last `COMPLETED` report present? if not, treat as worker accident).
- **State write**: dispatcher writes `.state/workers/worker-{task_id}.md` `Status: pane_closed`. Secretary, after judging the task is abandoned (no completion report and user does not re-delegate), sets the Active Work Item terminal status to `ABANDONED` (per `docs/org-state-schema.md` §50 vocabulary).
- **Journal**: `worker_closed` (with reason hint); separately, `WORKER_PANE_EXITED` is a peer-message channel only (not journaled today).
- **Re-delegation**: Automatic re-delegation is not contracted. After an unexpected pane exit, the secretary determines per-task whether to abandon, ask the user, or re-delegate; the decision is not bounded by an automatic retry counter.

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
- **Detection**: `tools/pr-watch.{ps1,sh}` writes a `ci_completed` event to `.state/journal.jsonl` on completion (per Secretary CLAUDE.md § PR 後の CI 監視). Failure is signalled within the event payload.
- **Notification path**: secretary inspects the journal entry (or is notified out-of-band by `gh pr checks --watch` exit) and decides whether to send fix instructions back to the same worker pane (T6 review-feedback path).
- **Retry**: Same-pane fix is the default (per `worker-claude-template.md` § 2 "ペインを保持してレビュー指摘待機"). Re-spawn of a fresh worker is forbidden.
- **Abort condition**: User declines further work, OR worker fix loop exceeds intervention triggers in `.claude/skills/org-delegate/SKILL.md` ワーカー監視と介入判定 (30 min same-phase / 1 h silent / Codex round-4).

### E5 — Codex Blocker / Major (worker self-review, full mode)
- **Detection**: Worker's own `codex exec` review.
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
| E4 CI failure | `pr-watch` script (journal `ci_completed`) | secretary | n/a | no |
| E5 Codex 4th-round | worker (self) | worker → secretary | n/a | yes — worker stops at 4th round |

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
