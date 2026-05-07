# Contract Set F — State Semantics

> **Status**: Drafted (2026-05-07). Codifies the canonical meaning of run/session states across the harness so subsequent implementation cleanups can point back to this document as the semantic baseline.
>
> **Scope**: State semantics only. This contract defines (a) the source of truth for each state surface, (b) the closed vocabulary of run statuses, (c) the conceptual groupings that operators and tooling are entitled to rely on (active reservation vs. active execution vs. user-visible vs. terminal), (d) which role/tool may originate or advance each transition, (e) the cross-state invariants, and (f) the allowed transition table. Per-role boundaries are Set A; on-disk file shapes are Set C; delegation lifecycle prose is Set B. This contract reconciles the run-status vocabulary used by all three.
>
> **Method**: Most statements below are sourced from the implementation as it exists today (post-M4 state-db cutover, Issue #267 / #284). A small number of run-status transitions (T7 `→ abandoned`, T8 `→ abandoned`, T9 `→ failed`) are **prescribed but not yet implemented**: the DB enum reserves the values and Set B describes the abstract lifecycle exit, but no production callsite writes them today. Such transitions are flagged inline and serve as the semantic baseline for the subsequent implementation Issues #352–#356 that build on this contract (per the Issue #351 directive: "Treat this document as the reference for subsequent implementation cleanups."). Where the established prose contracts (Set B § 1, Set C § 1.1) or `docs/org-state-schema.md` carry an older view, the divergence is named explicitly so the reconciliation work tracked under Epic #357 can refer to the gap by name.
>
> **Empirical sources consulted**:
> - `tools/state_db/schema.sql` (`runs.status` CHECK enumeration; `org_sessions.status` enumeration; `worker_dirs.lifecycle` enumeration)
> - `tools/state_db/queries.py` (`_ACTIVE_STATUSES`, `list_active_runs`, `list_runs_with_dirs`, `get_session`, M2 cutover docstring)
> - `tools/state_db/writer.py` (`update_run_status`, post-commit worker-state-file archive on `completed`)
> - `tools/resolve_worker_layout.py` § `_ACTIVE_RUN_STATUSES` (`('queued','in_use','review')` — active reservation set used by Pattern judgment)
> - `tools/gen_delegate_payload.py` (T1 reservation writes `runs.status='queued'`)
> - `tools/run_complete_on_merge.py` (terminal-status filter `NOT IN ('completed','failed','abandoned')`)
> - `dashboard/server.py` § `_DB_STATUS_TO_UI` (DB-status → frontend-status remap; UNINITIALIZED handling)
> - `.claude/skills/org-delegate/SKILL.md` (T1 reservation as queued; T4 review transition `update_run_status('<task>', 'review')`)
> - `.claude/skills/org-pull-request/SKILL.md` (T5 close transition `update_run_status('<task>', 'completed')`; T6 review-feedback re-entry `update_run_status('<task>', 'in_use')`)
> - `.dispatcher/references/spawn-flow.md` Step 4 (T2 upsert `status='in_use'`)
> - `docs/contracts/delegation-lifecycle-contract.md` (Set B — abstract lifecycle states)
> - `docs/contracts/state-schema-contract.md` § 1.1 (Set C — file-level schemas)
> - `docs/contracts/role-contract.md` (Set A — role boundaries)
> - `docs/journal-events.md` (event vocabulary, writer attribution, Required-for transition annotation)
> - `docs/org-state-schema.md` (legacy markdown-canonical view; reconciliation gap noted in §1.1 below)
>
> **Refs**: Closes #351. Parent epic #357. Subsequent Issues #352–#356 build on the vocabulary fixed here.

---

## 1. Source of truth

The harness maintains a layered state surface. Each layer is either **authoritative** (a writer must update it directly) or **derived** (regenerated from an authoritative layer; manual edits are drift). Reading from a derived layer is permitted; writing to one is contract violation.

### 1.1 Authoritative layers

| Surface | Path | Authoritative for | Writers |
|---|---|---|---|
| **state.db** (`runs` table) | `.state/state.db` | Run-level status / pattern / branch / PR / commit / outcome / worker-dir association | `tools/gen_delegate_payload.py` (T1 reservation insert with `status='queued'`), `.dispatcher` `delegate-plan` helper (T2 upsert with `status='in_use'`), `.claude/skills/org-delegate` Step 5 (T4 `update_run_status('<task>', 'review')`), `.claude/skills/org-pull-request` § 2c (T6 `update_run_status('<task>', 'in_use')`) and § 2b-ii (T5 `update_run_status('<task>', 'completed')`). The two sanctioned mutation entry points are `StateWriter.update_run_status` (`tools/state_db/writer.py:574`, the canonical lifecycle-status writer) and `StateWriter.upsert_run` (`tools/state_db/writer.py:433`, used at T1 / T2 to insert or upsert the row including its initial `status`). Direct SQL UPDATE is forbidden because the post-commit hooks (snapshotter regen, completed-archive move) would not fire. |
| **state.db** (`org_sessions` row) | `.state/state.db` (singleton `id=1`) | Org-wide session: `Status` ∈ `{ACTIVE, SUSPENDED, IDLE}`, `Updated`, `Suspended`, `Resumed`, `Current Objective`, dispatcher / curator pane+peer ids, resume instructions | `/org-start`, `/org-suspend`, `/org-resume` skills (via `StateWriter`). |
| **state.db** (`events` table) | `.state/state.db` | Append-only journal of cross-role events | `tools/journal_append.sh` / `tools/journal_append.py` (DB-routed since M4). |
| **state.db** (`worker_dirs` table) | `.state/state.db` | Worker directory inventory + `lifecycle` ∈ `{active, scratch, archived, delete_pending}` | `tools/gen_delegate_payload.py` (T1 worker-dir reservation), `StateWriter` worker-dir mutators. |
| **`.state/workers/worker-{task_id}.md`** | per-worker file | Pane-liveness Status (`planned` / `active` / `pane_closed` / `completed`) + Progress Log | Created by `delegate-plan` helper at T2; appended to by secretary on each peer message; auto-archived to `.state/workers/archive/` by the post-commit hook on `update_run_status('<task>', 'completed')` (`tools/state_db/writer.py:597`). The Progress Log content is authoritative; the `Status:` field is a coarse pane-liveness mirror. |

The post-M4 reality (Issue #267 / #284) is that **`state.db` is the canonical write target** for runs, sessions, events, and worker-directory inventory. `tools/state_db/queries.py:8–12` documents this explicitly: the M1 markdown overlay is gone, `org_sessions` carries the org-wide session fields, and `.state/org-state.md` is regenerated from `state.db` by `tools/state_db.snapshotter`.

### 1.2 Derived layers

| Surface | Path | Derived from | Regenerator |
|---|---|---|---|
| `.state/org-state.md` | repo-relative | state.db (`org_sessions`, `runs`, `worker_dirs`) | `tools/state_db.snapshotter` (called automatically as a `StateWriter.transaction()` post-commit hook). Direct manual edits are detected by `tools/state_db.drift_check`. |
| `.state/org-state.json` | repo-relative | state.db (DB-only since M4) | `dashboard/org_state_converter.py` (the pre-M4 `--source markdown` mode was removed; `dashboard/org_state_converter.py:10–13`); the dashboard server reads state.db directly via `dashboard.server.build_state` (`dashboard/server.py:226–245`) without consulting the JSON. |
| Dashboard payload (`/api/state`) | HTTP | state.db | `dashboard.server.build_state` (`dashboard/server.py:248–289`). |

### 1.3 Reconciliation gaps with prior prose

Two earlier documents describe an older "markdown is canonical" view that predates the M4 cutover:

- `docs/org-state-schema.md` § "Source of truth ルール" states "Markdown が正本、JSON は派生です。"
- `docs/contracts/state-schema-contract.md` § 1.1 declares `org-state.md` canonical with a derived JSON projection.

Both are stale relative to the running implementation, which writes to `state.db` first and regenerates the markdown. Reconciling those documents to the post-M4 reality is **out of scope for this contract**; this contract pins the current behavior so subsequent doc cleanups (tracked under Epic #357 follow-ups #352–#356) can refer to the canonical reading by name.

When the prior docs and this contract conflict, **this contract governs**: state.db is the single source of truth for run status, session status, worker-directory inventory, and journal events.

---

## 2. Run status vocabulary

The DB CHECK constraint on `runs.status` enumerates exactly seven values (`tools/state_db/schema.sql:75`):

```
queued, in_use, review, completed, failed, suspended, abandoned
```

This contract treats that enumeration as **closed**. Any new lifecycle concept MUST either map onto an existing value or extend the CHECK clause (and this contract) explicitly; ad-hoc string values are forbidden.

| Status | Definition | Contract-level role | Set B mapping |
|---|---|---|---|
| `queued` | T1 reservation: secretary has reserved the run row, written CLAUDE.md/CLAUDE.local.md and `send_plan.json` into the worker dir, and is about to send `DELEGATE` to the dispatcher. The worker pane has not yet been spawned. Introduced by Issue #283 to make the reservation atomic (avoid the lost-row race when `gen_delegate_payload` succeeds but `spawn_claude_pane` fails). | Active reservation (occupies the project's base-clone slot); no live execution yet. | `pending` |
| `in_use` | T2 onward: dispatcher has spawned the worker pane and the worker is acting on its instruction (or in T6 re-entry from review). The worker pane is open, the worker is the live executor. | Active execution. | `dispatched` / `in_progress` |
| `review` | T4: worker has sent a structured completion report to the secretary. The pane is still open; the secretary may issue T6 review feedback or T5 close. | Active execution **paused for human review** — pane retained, no worker-side work in flight. | `awaiting_review` |
| `completed` | T5: close-condition met (PR merged, user-explicit close, or 24–48h idle per Set B § 1.5). Terminal success. The post-commit hook moves `.state/workers/worker-{task_id}.md` to `.state/workers/archive/` (`tools/state_db/writer.py:597`). | Terminal — success. | `complete` |
| `failed` | Terminal failure recorded explicitly. Reserved for cases where the run reached a definite negative outcome that should not be conflated with `abandoned` (e.g., CI failure ratified as the final outcome, or a worker error path the operator wants distinguishable in retros). Currently used only by `tools/test_pr_watch.py` fixtures and the `run_complete_on_merge` filter (`tools/run_complete_on_merge.py:161`); no production write path sets `failed` at the time of writing. | Terminal — failure. | `aborted` (subset) |
| `suspended` | Reserved at the run level for future use (e.g., long-pause delegations whose pane has been closed but whose work is intended to resume). **Not currently written by any production path.** Set B § 4.3 explicitly does NOT introduce a per-run `suspended` lifecycle state — `SUSPEND:` to a worker keeps `runs.status='in_use'` and recovers discrimination from the SUSPEND report + Progress Log. The DB-level enum value is retained as a reservation slot for the deferred design; treating it as live state is contract violation until a future contract amendment activates it. | Reserved (not active). | (none today) |
| `abandoned` | Terminal abandonment: pane exited without completion AND user declined re-delegation, OR secretary judged the task no longer relevant (Set B T7). | Terminal — abandoned. | `aborted` (subset) |

The `org_sessions.status` field uses an independent three-value enum `{ACTIVE, SUSPENDED, IDLE}` covering the org-wide session, **not** an individual run. `org-state.md` Active Work Item display labels (`IN_PROGRESS` / `REVIEW` / `COMPLETED` / `ABANDONED`) are a derived UI projection mapped from `runs.status` by `dashboard/server.py:196–204`; they are not independent state.

---

## 3. Conceptual groupings

This contract distinguishes four orthogonal concepts that the run-status vocabulary expresses simultaneously. The bug pattern Issue #351 calls out — "`queued` is treated as an active reservation in some code paths" while "active, visible, and reserved are not clearly separated" — is resolved by pinning each concept to an explicit predicate over `runs.status`.

### 3.1 Active reservation
A run **occupies a project's base-clone slot** (i.e., a back-to-back delegation on the same project must use Pattern B worktree, not the base clone). Predicate: `runs.status IN ('queued','in_use','review')` per `tools/resolve_worker_layout.py:91`. This is the broadest "live" predicate; it includes `queued` because the T1 reservation's whole purpose is to claim the slot before T2 spawns the pane.

### 3.2 Active execution
A run **has a live worker pane that is acting on its instruction**. Predicate: `runs.status = 'in_use'`. `review` is excluded because the worker is awaiting human review with no work in flight; `queued` is excluded because the pane has not been spawned yet.

### 3.3 User-visible work item
A run **appears on the dashboard / `org-state.md` Active Work Items list as something the human is currently shepherding**. Predicate: `runs.status IN ('in_use','review')` per `tools/state_db/queries.py:20` (`_ACTIVE_STATUSES`) and `:68`. `queued` is intentionally excluded from the user-visible projection: the reservation has not yet produced a pane, so surfacing it in the dashboard would conflate "the secretary has reserved a slot" with "the org has an in-flight delegation". Once T2 flips the row to `in_use` (typically within seconds), the dashboard surfaces it.

### 3.4 Terminal state
A run **has reached an outcome that cannot transition further without operator override**. Predicate: `runs.status IN ('completed','failed','abandoned')` per `tools/run_complete_on_merge.py:161`. `suspended` is **not** terminal under this contract (it is reserved-for-future per § 2); future contract amendments may move it.

### 3.5 Predicate summary

| Concept | Predicate (`runs.status IN …`) | Source |
|---|---|---|
| Active reservation | `('queued','in_use','review')` | `tools/resolve_worker_layout.py:91` |
| Active execution | `('in_use')` | This contract |
| User-visible (dashboard) | `('in_use','review')` | `tools/state_db/queries.py:20`, `:68` |
| Terminal | `('completed','failed','abandoned')` | `tools/run_complete_on_merge.py:161` |

The `queued` ⊂ active-reservation but ⊄ user-visible asymmetry is deliberate. Resolver code that gates pattern selection MUST use the active-reservation predicate (otherwise concurrent secretary delegations corrupt the base clone). Dashboard / org-state.md rendering MUST use the user-visible predicate (otherwise the operator UI flickers with sub-second reservations). Conflating the two — which Issue #351's example case names — is the failure mode this distinction prevents.

---

## 4. Transition ownership

Each transition lists the actor authorized to originate it, the writing tool, and the resulting `runs.status` value. The sanctioned writers are `StateWriter.upsert_run` (T1 insert, T2 upsert — initial / promoted status) and `StateWriter.update_run_status` (all subsequent lifecycle transitions). Direct SQL UPDATE is forbidden: it bypasses the post-commit snapshotter regen (I6) and the `update_run_status('<task>', 'completed')` worker-state-file archive hook (`tools/state_db/writer.py:597`).

| # | From → To | Actor | Writer | Transition (Set B) |
|---|---|---|---|---|
| T1 | `(none) → queued` | secretary | `tools/gen_delegate_payload.py` (`apply` mode) inserts the run row with `status='queued'` | T1 (`pending`) |
| T2 | `queued → in_use` | dispatcher | `delegate-plan` helper upsert with `status='in_use'` (`.dispatcher/references/spawn-flow.md:160`); fires after `spawn_claude_pane` confirms the new peer | T2 (`dispatched`) |
| T3 | `in_use → in_use` (no-op) | secretary records progress | Progress Log append in `.state/workers/worker-{task_id}.md`; `runs.status` unchanged | T3 (`in_progress`) |
| T4 | `in_use → review` | secretary | `StateWriter.update_run_status('<task>', 'review')` (`.claude/skills/org-delegate/SKILL.md:179`) | T4 (`awaiting_review`) |
| T5 | `review → completed` | secretary | `StateWriter.update_run_status('<task>', 'completed')` (`.claude/skills/org-pull-request/SKILL.md:99`). `tools/run_complete_on_merge.py` is **not** a T5 writer: on PR-merge sweep it records only `pr_state='merged'` and `completed_at`, leaving `runs.status` untouched and emitting a stderr notice that the secretary must perform the worktree remove / `CLOSE_PANE` / `update_run_status('<task>', 'completed')` cleanup (`tools/run_complete_on_merge.py:281–311`). The status flip remains a secretary act. | T5 (`complete`) |
| T6 | `review → in_use` | secretary | `StateWriter.update_run_status('<task>', 'in_use')` (`.claude/skills/org-pull-request/SKILL.md:55`) | T6 (review feedback / depth switch) |
| T7 | `in_use → abandoned` | secretary | **Prescribed (not yet implemented)**: `StateWriter.update_run_status('<task>', 'abandoned')` after dispatcher reports `WORKER_PANE_EXITED` and user declines re-delegation. Today only Set B § 2 T7 is in effect (dispatcher writes worker-state-file `Status: pane_closed`; secretary updates `.state/org-state.md` Active Work Items to `ABANDONED` via the M1-era markdown path). The `runs.status='abandoned'` write call site does not yet exist in production — wiring it is part of the subsequent Issues #352–#356 that build on this contract. | T7 |
| T8 | `queued → abandoned` | secretary | **Prescribed (not yet implemented)**: on `SPLIT_CAPACITY_EXCEEDED` from dispatcher, secretary calls `StateWriter.update_run_status('<task>', 'abandoned')` and reverts the Worker Directory Registry row per Set B § 2 T8. No `remove_run` API exists in `StateWriter` and physical deletion of run rows is not a sanctioned operation, so the queued reservation transitions to the `abandoned` terminal status rather than being deleted. The production callsite is pending. | T8 |
| T9 | `in_use → failed` | secretary | **Prescribed (not yet implemented)**: `StateWriter.update_run_status('<task>', 'failed')` — reserved entry point. The `failed` enum value is currently used only by the terminal-status filter in `tools/run_complete_on_merge.py:161`, by `dashboard/server.py:201` (UI remap), and by `tools/state_db.snapshotter` for read-side rendering; no production write path sets it. Activating T9 is a follow-up. | E4 / E5 deferred |

Transitions T7 / T8 / T9 all share the abstract `aborted` mapping in Set B; the run-status vocabulary distinguishes them so retros and dashboards can tell "user abandoned" from "split capacity prevented spawn" from "explicit failure recorded".

The dispatcher does **not** write `runs.status` for terminal transitions. Even when `poll_events` reports `pane_exited`, the dispatcher only writes the worker-state-file `Status: pane_closed` and notifies the secretary; the secretary classifies the outcome and writes the terminal `runs.status` (Set B § 2 T7).

---

## 5. Invariants

The following invariants hold on every committed state. Violations are bugs.

### I1 — Run-status closed enumeration
`runs.status ∈ {queued, in_use, review, completed, failed, suspended, abandoned}`. Enforced by SQL CHECK; this contract additionally fixes the meaning of each value (§ 2).

### I2 — Single active reservation per (project, base clone)
For Pattern A delegations, at most one run row per project may have `status IN ('queued','in_use','review')`. A second concurrent delegation on the same project MUST select Pattern B (worktree). Enforced by `tools/resolve_worker_layout.project_has_active_run` (`:213`); the resolver flips Pattern A → B when the predicate matches.

### I3 — Pane-liveness vs. run-status coherence (ACTIVE-session only)
While `org_sessions.status='ACTIVE'`: if `runs.status='in_use'`, the worker pane named `worker-{task_id}` MUST be open; if `runs.status='completed'`, the worker-state file MUST be in `.state/workers/archive/` (post-commit hook in `tools/state_db/writer.py:597`). A `completed` run with a live pane, or an `in_use` run with no pane, indicates drift.

The pane-open clause is **suspended while `org_sessions.status='SUSPENDED'`**: `/org-suspend` graceful-closes worker panes (`.claude/skills/org-suspend/SKILL.md:10, 134`) but deliberately leaves the in-flight runs at `runs.status='in_use'` so that `/org-resume` can re-open panes against them. Re-opening panes on resume is what the resume skill iterates on; treating the closed-pane-during-SUSPEND interval as drift would force every suspend cycle to flip status into the reserved-and-unused `runs.status='suspended'` slot (§ 2), which no production path supports today. The carve-out is therefore explicit and bounded: it applies only between the `org_sessions` `SUSPENDED → ACTIVE` transition.

### I4 — Session vs. run independence
`org_sessions.status='SUSPENDED'` does NOT propagate to `runs.status`. Per Set B § 4.3, individual runs remain `in_use` across `/org-suspend` even when the worker pane has been graceful-closed; the discrimination of "paused" vs. "active" is recovered from the worker's most recent SUSPEND report + Progress Log, not from a run-level status flip. The reserved-but-unused `runs.status='suspended'` value (§ 2) is **not** the persistence target for `/org-suspend` checkpoints today, and `/org-resume` is responsible for re-opening worker panes against still-`in_use` rows.

### I5 — Terminal status is final
Once `runs.status ∈ {completed, failed, abandoned}`, the row MUST NOT transition back to a non-terminal status. Re-running a task with the same `task_id` is forbidden by the `runs.task_id UNIQUE` constraint; a fresh delegation requires a fresh `task_id`. The `update_run_status` writer does not enforce this in code today — the invariant is operator-level, and a follow-up Issue may add a CHECK trigger.

### I6 — Derived-layer freshness
After any `runs` / `org_sessions` / `worker_dirs` / `events` write, the snapshotter post-commit hook regenerates `.state/org-state.md`. Direct manual edits to `.state/org-state.md` are forbidden and detectable by `tools/state_db.drift_check`. The derived JSON snapshot (`.state/org-state.json`) and the dashboard payload are downstream of the same write.

### I7 — Worker directory lifecycle is independent of run status
`worker_dirs.lifecycle ∈ {active, scratch, archived, delete_pending}` is **not** a projection of `runs.status`. A Pattern A worker_dir typically remains `lifecycle='active'` across many runs; Pattern B worktrees move from `active → archived` on T5; ephemeral Pattern C dirs may move to `delete_pending`. The `StateWriter` does not couple these mutations — each is its own call site.

### I8 — `queued` MUST be invisible to the user-visible projection
The dashboard / `org-state.md` Active Work Items list MUST exclude `queued` rows (§ 3.3). A row that lingers in `queued` for more than a few seconds is itself a signal of a failed T2 transition (likely `SPLIT_CAPACITY_EXCEEDED` without secretary cleanup); it should be surfaced via dispatcher anomaly notification, not dashboard rendering.

---

## 6. Transition table

This table is the contract-level allow-list. Any transition not listed is forbidden.

| From\To | queued | in_use | review | completed | failed | suspended | abandoned |
|---|---|---|---|---|---|---|---|
| **(none)** | T1 (secretary) | — | — | — | — | — | — |
| **queued** | — | T2 (dispatcher) | — | — | — | — | T8 (secretary, capacity-exceeded) |
| **in_use** | — | T3 (no-op, progress) | T4 (secretary) | — | T9 (secretary, deferred) | — | T7 (secretary, pane-exit) |
| **review** | — | T6 (secretary, review feedback) | — | T5 (secretary, close-condition) | — | — | — |
| **completed** | — | — | — | — | — | — | — |
| **failed** | — | — | — | — | — | — | — |
| **suspended** | — | — | — | — | — | — | — |
| **abandoned** | — | — | — | — | — | — | — |

Notes on the table:

- `T3` is listed as a `in_use → in_use` self-loop because progress reports do not change `runs.status`; the row exists to emphasize that `worker_reported` events (§ Set B T3) require no run-status write.
- `suspended` has no incoming transition because it is reserved-for-future (§ 2). The empty row is intentional.
- `completed` / `failed` / `abandoned` rows have no outgoing transitions per I5. A re-attempt requires a new `task_id` and re-enters at T1.
- `queued → completed` / `queued → review` are forbidden: a queued reservation that never spawned has no worker output to review, so the only legal terminal exit is T8 (`abandoned`).

---

## Decision rationale digest

1. **State.db as canonical, not org-state.md (§ 1)** — Codifies the post-M4 reality (Issue #267 / #284). The two prior contract docs (Set C § 1.1, `docs/org-state-schema.md`) describe the pre-M4 markdown-canonical view; their reconciliation is tracked under Epic #357 as a doc-only follow-up. This contract governs the conflict.
2. **Closed seven-value vocabulary (§ 2)** — Fixes the DB CHECK enum as the contract enum. `suspended` is retained as a reserved slot to avoid breaking-change risk, with explicit prose stating no production path writes it today.
3. **Four orthogonal predicates (§ 3)** — Active-reservation / active-execution / user-visible / terminal are pinned to explicit `runs.status IN …` predicates so resolver, dashboard, and sweep code stop conflating them. The `queued` ⊂ active-reservation but ⊄ user-visible asymmetry is the central correctness fact this contract names.
4. **Dispatcher does not write terminal status (§ 4)** — Pane-exit observation belongs to the dispatcher; outcome classification belongs to the secretary. The split mirrors Set B § 2 T7 and is restated here so future tooling does not regress.
5. **`/org-suspend` does not propagate to `runs.status` (I4)** — Aligned with Set B § 4.3. The reserved `runs.status='suspended'` value remains unused; activating it is a future contract amendment, not a silent code change.
6. **Derived-layer drift is detectable (I6)** — `tools/state_db.drift_check` is the existing detector; this contract pins the freshness invariant so adding new derived surfaces (e.g., a future search index) inherits the same regenerator obligation.
