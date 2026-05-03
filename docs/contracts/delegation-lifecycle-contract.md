# Contract Set B — Delegation Lifecycle (Outline)

> **Status**: Outline / skeleton — pending Lead Q&A (2026-05). This document is the structural extraction of the delegation lifecycle (from secretary's `DELEGATE` decision through worker spawn, in-progress reporting, completion review, and pane teardown) as it exists in the current `claude-org-ja` implementation, with inline fill-in markers left for design decisions that the Lead must ratify before this contract is finalized. The Lead fill-in pass is a separate follow-up PR.
>
> **Scope**: Phase 1 Contract Set B only. Covers delegation begin / in_progress / complete / abort transitions, error propagation, and SUSPEND handling. Role-level responsibilities and boundaries are covered by Set A (`docs/contracts/role-contract-outline.md`). State-file schemas, message-channel contracts, and knowledge flow are tracked in #123–#125 and out of scope here.
>
> **Method**: Each lifecycle state and transition below is filled from empirical sources (current `org-delegate` skill, dispatcher CLAUDE.md, worker template, journal helper). Sentences sourced from current behavior are written as facts. Open design questions are flagged inline with the standard fill-in marker (see Set A) so a Lead Q&A pass can resolve them.
>
> **Empirical sources consulted**:
> - `.claude/skills/org-delegate/SKILL.md` (Step 1.5 worker-dir prep, Step 3 spawn / instruction send, Step 4 state record, Step 5 progress / close)
> - `.claude/skills/org-delegate/references/instruction-template.md` (validation depth, completion-report format, SUSPEND clause)
> - `.claude/skills/org-delegate/references/worker-claude-template.md` (worker steady-state behavior, completion / SUSPEND obligations)
> - `.dispatcher/CLAUDE.md` (anomaly forwarding, watch loop, completion-report retro gate, CLOSE_PANE flow)
> - `docs/journal-events.md` (event vocabulary)
> - `tools/journal_append.sh` / `tools/journal_append.py` (accepted event-write schema)
> - `docs/contracts/role-contract-outline.md` — Set A (per-role lifecycle / boundary sections, for cross-reference)
>
> **Refs**: #122 (this issue), parent epic #101.

---

## 1. Lifecycle states

A single delegation moves through the following finite set of states. State labels are normative — they appear (or should appear) in `.state/workers/worker-{task_id}.md` `Status:` and in `.state/org-state.md` Active Work Items.

| # | State | Owner of transition in | Persisted at | Visible journal events |
|---|---|---|---|---|
| 1 | `pending` | secretary (drafts `DELEGATE`) | `.state/dispatcher/inbox/{task_id}.json` (task spec written for `delegate-plan`) | `delegate_sent` |
| 2 | `dispatched` | dispatcher (after `spawn_claude_pane` succeeds) | `.state/workers/worker-{task_id}.md` `Status: planned → active`; `.state/org-state.md` Worker Directory Registry row added | `worker_spawned` |
| 3 | `in_progress` | worker (begins acting on its instruction) | `.state/workers/worker-{task_id}.md` Progress Log appended on each report | `worker_reported` (per progress message), `anomaly_observed` (if applicable) |
| 4 | `awaiting_review` (a.k.a. `REVIEW`) | secretary (on receipt of completion report from worker) | `.state/org-state.md` Active Work Item set to `REVIEW` | `worker_completed`, `worker_review` |
| 5 | `complete` (a.k.a. `COMPLETED`) | secretary (after close-condition met — see §1.5) | `.state/org-state.md` Active Work Item set to `COMPLETED`; Worker Directory Registry updated per pattern; `.state/workers/worker-{task_id}.md` final-update | `worker_closed`, `worktree_removed` (Pattern B), pattern-specific registry updates |
| 6 | `aborted` | dispatcher or secretary (per §2 error propagation) | `.state/workers/worker-{task_id}.md` `Status: aborted` (or `pane_closed` if pane-exit-without-completion) | `worker_closed` with abort reason, `retro_deferred` (if retro could not run) |

- **[TBD by Lead]** — State granularity: whether `dispatched` and `awaiting_review` are each distinct contract states or sub-states of `in_progress`. Today the implementation half-distinguishes them (`.state/workers/*.md` flips `planned → active` at spawn but does not retitle on completion-report; `.state/org-state.md` writes `REVIEW` but the worker pane keeps running). The contract should pick one boundary per state.
- **[TBD by Lead]** — Authoritative list of journal event names permitted (or mandatory) for each lifecycle transition. Today the helper accepts arbitrary event strings; `docs/journal-events.md` documents a vocabulary but does not pin which subset is required per transition — same shape as Set A's "allowed journal events per role" cluster.

### 1.5 Close-condition (transition into `complete`)

The secretary moves a delegation from `awaiting_review` to `complete` when at least one of the following is met (per `org-delegate` Step 5 § 2b-ii):

- The PR has been merged (verified via `gh pr view {n} --json mergedAt` or via merge notification).
- The user has explicitly instructed close ("閉じてよい" / "クローズして" / "マージ済み").
- The PR has been idle for 24–48 hours with no review activity (operator judgment; not automated).

- **[TBD by Lead]** — Whether the 24–48h idle threshold is a hard contract (with an exact bound) or a default that the operator may tune per project. Currently it is an operator judgment range, not a contract.
- **[TBD by Lead]** — Whether a delegation that closes without a PR (e.g., investigation-only Pattern C tasks that produce only a report message) follows the same close-condition gate, or has a separate path.

---

## 2. Transitions and triggering events

Each transition below names: **(a)** the event that triggers it, **(b)** which actor executes the transition, **(c)** the state-file write the actor must perform, and **(d)** the journal event the helper must record.

### T1 — `(none) → pending`
- **Trigger**: Secretary completes `org-delegate` Steps 0–1.5 for a task and is about to send the `DELEGATE` message to the dispatcher.
- **Actor**: secretary.
- **State write**: `.state/dispatcher/inbox/{task_id}.json` is written with the task spec consumed by `claude-org-runtime dispatcher delegate-plan`. CLAUDE.md / settings.local.json are placed in the worker dir (Step 1.5). Worker Directory Registry row added with Status `in_use`.
- **Journal**: `delegate_sent` (`task`, `worker`, `dir`).

### T2 — `pending → dispatched`
- **Trigger**: Dispatcher receives `DELEGATE` from secretary and successfully completes Step 3 (balanced-split target / direction → `spawn_claude_pane` → dev-channel Enter approval → `list_peers` confirms the new peer → `send_message` delivers the worker instruction).
- **Actor**: dispatcher.
- **State write**: `.state/workers/worker-{task_id}.md` flips `Status: planned → active`; `.state/dispatcher-event-cursor.txt` is advanced as `poll_events` consumes the `pane_started` event; `.state/org-state.md` Active Work Items row added.
- **Journal**: `worker_spawned` (`worker`, `dir`, `task`). Followed by `DELEGATE_COMPLETE` `send_message` to secretary (out-of-band notification; not a journal event today).
- **[TBD by Lead]** — Whether `DELEGATE_COMPLETE` must also be journaled (today it is only sent as a peer message; there is no journal record of dispatcher-side handoff completion).

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
- **Pane discipline**: Worker pane MUST remain open; secretary must NOT instruct dispatcher to `CLOSE_PANE` at this stage (per `org-delegate` Step 5 § 2b-i and `worker-claude-template.md` § 2).

### T5 — `awaiting_review → complete` (close-condition met)
- **Trigger**: §1.5 close-condition met AND user has approved (or condition is auto-satisfied via merge / idle).
- **Actor**: secretary executes the close, then sends `CLOSE_PANE: {pane_id}` to dispatcher; dispatcher executes the close per `.dispatcher/CLAUDE.md` § ペインクローズ (retro Steps 1–2 first, then `mcp__renga-peers__close_pane`).
- **State write**:
  - secretary: `.state/org-state.md` Active Work Item → `COMPLETED`; pattern-specific Worker Directory Registry update (Pattern A → `available`; Pattern B → row removed and worktree removed via `git worktree remove`; Pattern C → row removed); JSON snapshot regenerated.
  - dispatcher: `.state/workers/worker-{task_id}.md` final update; pane closed via `close_pane`.
- **Journal**: `worker_closed` (`worker`, `pane_id`). Pattern B additionally writes `worktree_removed` (`path`, `task`).

### T6 — `awaiting_review → in_progress` (review feedback / depth switch)
- **Trigger**: User issues feedback / change request on the completion report or PR, OR secretary intervenes (per `org-delegate` Step 5 ワーカー監視と介入判定) and re-instructs in the same pane.
- **Actor**: secretary `send_message`s the same `worker-{task_id}` pane with the additional instruction.
- **State write**: `.state/org-state.md` Active Work Item back to `IN_PROGRESS`; `.state/workers/worker-{task_id}.md` Progress Log appended.
- **Pane discipline**: New worker MUST NOT be re-spawned for in-scope review feedback (re-spawn is rejected by the contract because Issue/diff/judgment context would be lost).

### T7 — `* → aborted` (worker pane exits without completion)
- **Trigger**: Dispatcher's `poll_events` sees `pane_exited` for `name == "worker-{task_id}"` whose corresponding `.state/workers/worker-{task_id}.md` does not record a `worker_completed` event, OR `list_panes` reconciliation finds the pane gone.
- **Actor**: dispatcher (forwards to secretary as `WORKER_PANE_EXITED`); secretary decides whether to re-delegate or abandon (per current `.dispatcher/CLAUDE.md` § (1) `pane_exited` handling).
- **State write**: dispatcher writes `.state/workers/worker-{task_id}.md` `Status: pane_closed`; secretary may flip Active Work Item to `aborted` after user judgment.
- **Journal**: `worker_closed` (with reason hint); separately, `WORKER_PANE_EXITED` is a peer-message channel only (not journaled today).
- **[TBD by Lead]** — Maximum number of automatic re-delegation retries for an unexpectedly-exited worker before the contract requires user escalation. Today: pure secretary judgment, no retry counter contracted.

### T8 — `* → aborted` (`SPLIT_CAPACITY_EXCEEDED`)
- **Trigger**: Dispatcher's balanced-split filter returns zero candidates (per `org-delegate` Step 3-1c).
- **Actor**: dispatcher.
- **State write**: No worker pane is spawned; `.state/dispatcher/inbox/{task_id}.json` may remain on disk for re-attempt; `.state/workers/worker-{task_id}.md` is NOT written (no pane existed).
- **Journal**: `delegate_failed` or equivalent — see the §1 fill-in on event vocabulary; today this case is signalled only via the `SPLIT_CAPACITY_EXCEEDED` peer message to secretary, with no journal record.
- **Liveness**: Dispatcher watch loop continues; only this one delegation is aborted (`exit` / `return` of dispatcher pane is forbidden).

---

## 3. Error propagation

Five error / anomaly classes are recognized. Each lists: who detects, who is notified, retry semantics, and abort conditions.

### E1 — Worker pane exits unexpectedly
- **Detection**: dispatcher's `poll_events` (`pane_exited` for `role=="worker"` without prior `worker_completed`); fallback via `list_panes` reconciliation each watch-loop cycle.
- **Notification path**: dispatcher → secretary via `mcp__renga-peers__send_message(to_id="secretary")` with body `WORKER_PANE_EXITED: {name} (id={id}) のペインが閉じました。リコンサイル要。`
- **Retry**: Not automatic. Secretary asks user whether to re-delegate or abandon.
- **Abort condition**: User explicitly declines re-delegation, OR secretary determines task is no longer relevant. (Retry-bound is the same open question as §2 T7.)

### E2 — `APPROVAL_BLOCKED` / `ERROR_DETECTED` from dispatcher inspect
- **Detection**: dispatcher `inspect_pane` matches one of the anchored regexes in `.dispatcher/CLAUDE.md` § (b) (approval prompt) or substring set in § (d) (error banner).
- **Notification path**: dispatcher → secretary; tagged with `source=inspect` and `confidence=high|n/a`. De-duplication: 30-second window keyed on `(worker, kind)` against `event=notify_sent` ledger; `anomaly_observed` rows do NOT count toward de-dup.
- **Retry**: Notification is at-least-once. The underlying anomaly is human-resolved (secretary asks user how to proceed and forwards `send_keys` instructions to the worker pane via the dispatcher / directly).
- **Abort condition**: None automatic; only human decision aborts.

### E3 — Worker self-reports `ERROR` / `APPROVAL_BLOCKED` via `to_id="secretary"`
- **Detection**: dispatcher receives via `check_messages` (and forwards), OR secretary receives directly. Both channels are independent (per `.dispatcher/CLAUDE.md` § (g) "両チャネル独立稼働で OK").
- **Notification path**: as in E2; tagged `source=self_report`, `confidence=n/a`.
- **De-dup**: same 30-second `(worker, kind)` window applies, so inspect (E2) and self-report (E3) are not double-notified.
- **[TBD by Lead]** — Whether `ERROR_DETECTED` with `confidence=n/a` (self-report only, no inspect corroboration within window) is sufficient to halt the worker (e.g., automatic `Esc`-send) or requires inspect corroboration before halting. Today: notification only; halting is human-decided.

### E4 — CI fails on PR
- **Detection**: `tools/pr-watch.{ps1,sh}` writes a `ci_completed` event to `.state/journal.jsonl` on completion (per Secretary CLAUDE.md § PR 後の CI 監視). Failure is signalled within the event payload.
- **Notification path**: secretary inspects the journal entry (or is notified out-of-band by `gh pr checks --watch` exit) and decides whether to send fix instructions back to the same worker pane (T6 review-feedback path).
- **Retry**: Same-pane fix is the default (per `worker-claude-template.md` § 2 "ペインを保持してレビュー指摘待機"). Re-spawn of a fresh worker is forbidden.
- **Abort condition**: User declines further work, OR worker fix loop exceeds intervention triggers in `org-delegate` Step 5 (30 min same-phase / 1 h silent / Codex round-4).

### E5 — Codex Blocker / Major (worker self-review, full mode)
- **Detection**: Worker's own `codex exec` review.
- **Handling rule**: 3-round cap on same-category Blocker/Major findings; on 4th round the worker MUST stop and report to secretary "design issue — request scope reduction" (per `worker-claude-template.md` § Codex セルフレビュー手順).
- **Notification path**: worker → secretary direct.
- **Retry / abort**: Retry is bounded by the 3-round cap; abort condition is the round-4 declaration.
- **[TBD by Lead]** — Whether the 3-round cap is a contract obligation across all `full`-mode delegations or only when `codex` is available in the worker environment (today the rule is conditional on `codex` availability — `unavailable` env skips the entire round discipline).

### Error-class summary table

| Class | Detector | Notifier | De-dup | Auto-abort? |
|---|---|---|---|---|
| E1 pane-exited | dispatcher poll_events | dispatcher → secretary | n/a | no (human decides) |
| E2 inspect anomaly | dispatcher inspect_pane | dispatcher → secretary | 30s `(worker, kind)` | no |
| E3 worker self-report | worker → secretary (also dispatcher.check_messages) | secretary direct (or dispatcher forward) | 30s `(worker, kind)`, shared with E2 | no |
| E4 CI failure | `pr-watch` script (journal `ci_completed`) | secretary | n/a | no |
| E5 Codex 4th-round | worker (self) | worker → secretary | n/a | yes — worker stops at 4th round |

- **[TBD by Lead]** — Authoritative list of inspect-detected approval-prompt regexes (currently maintained as a growing list in `.dispatcher/CLAUDE.md` § (b); should it be promoted to a contract artifact and versioned? — same TBD as Set A dispatcher constraint).

---

## 4. SUSPEND handling

`SUSPEND:` is a peer message that triggers an in-flight delegation to halt and report. The current contract surface is small but informal.

### 4.1 Who may issue
- Only the secretary may issue `SUSPEND:` to a worker (per `worker-claude-template.md` § SUSPEND対応 and `instruction-template.md` § SUSPEND 対応).
- **[TBD by Lead]** — Whether the dispatcher may relay a `SUSPEND:` originally authored by the secretary (today the secretary `send_message`s the worker directly), or whether dispatcher-originated SUSPEND is permitted under any condition.

### 4.2 Worker obligations on receipt
On receiving a message whose body begins with `SUSPEND:`, the worker MUST immediately (i.e., before continuing the in-flight tool call where safe) report the following four items to `to_id="secretary"`:
1. Work completed up to this point.
2. Modified files (committed vs. uncommitted, listed separately).
3. Planned next step (the action the worker would have taken next).
4. Blockers / unresolved issues.

- **[TBD by Lead]** — Authoritative SUSPEND-report schema, including (a) required-vs-optional field split for the four prose items above, and (b) whether the worker MUST `git add` / `git commit` uncommitted changes before reporting (so the worktree is clean for resume), or whether reporting them as "uncommitted" is sufficient and resume re-evaluates.

### 4.3 State transition under SUSPEND
- The implementation today does NOT introduce a distinct `suspended` state in `.state/workers/worker-{task_id}.md` or `.state/org-state.md`. The Active Work Item remains `IN_PROGRESS` (or whatever its prior label was) and the worker pane stays open.
- **[TBD by Lead]** — Whether the contract should introduce a distinct `suspended` state (sub-state of `in_progress`, or peer state alongside it) so that `org-resume` can disambiguate "worker is silently mid-work" from "worker has been told to halt and is awaiting resume instruction".

### 4.4 Resume contract
- Today, on `/org-resume`, the secretary inspects `.state/workers/worker-*.md` and decides per worker whether to send a resume instruction to the **same pane** (default) or to abandon and re-delegate to a **fresh pane**. Same-pane resume is the documented norm because re-spawn loses Issue / diff / judgment context (same rationale as T6 review-feedback path).
- **[TBD by Lead]** — Resume contract: (a) whether SUSPEND-then-resume MUST reuse the same pane or may at secretary's discretion fall back to a fresh pane (today: same pane preferred but not contracted), and (b) which persisted artifact is the canonical resume input — today only `.state/workers/worker-{task_id}.md` Progress Log plus the most recent SUSPEND report message.

### 4.5 SUSPEND vs `/org-suspend`
- `/org-suspend` (org-wide shutdown) is distinct from per-worker `SUSPEND:`. `/org-suspend` flushes secretary / dispatcher / curator state and graceful-closes panes; per-worker `SUSPEND:` is a single-worker pause that keeps panes alive.
- **[TBD by Lead]** — Whether `/org-suspend` MUST first issue per-worker `SUSPEND:` to every active worker (today implicit; the contract should state whether worker reports are guaranteed before org-state is flushed).

---

## Open questions consolidated (for Lead fill-in)

The inline fill-in markers above are the explicit decision points. They cluster into:

1. **State granularity** — whether `dispatched` / `awaiting_review` / `suspended` are distinct contract states or sub-states (§1, §1, §4.3).
2. **Closed-set enumerations** — allowed journal events per transition, approval-prompt regex set, SUSPEND-report schema (§1, §2, §3, §4.2).
3. **Retry bounds** — automatic re-delegation cap on pane-exit, and Codex round-cap applicability (§2 T7, §3 E5).
4. **Close-condition formalization** — whether the 24–48h idle threshold is a hard bound, and whether no-PR delegations follow the same close path (§1.5).
5. **SUSPEND semantics** — same-pane vs. fresh-pane on resume, commit obligations on suspend, resume input artifact, `/org-suspend` ordering (§4.1, §4.2, §4.4, §4.5).
6. **Notification halting** — whether self-report-only (no inspect corroboration) suffices to auto-halt a worker (§3 E3).
7. **Handoff journaling** — whether `DELEGATE_COMPLETE` must be journaled in addition to its peer message (§2 T2).

These are the design decisions that must be settled before Contract Set B is ratified; the structural skeleton is fixed.
