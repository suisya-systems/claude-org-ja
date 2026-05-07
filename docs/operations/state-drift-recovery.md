# State drift detection & recovery

> **Status**: Drafted (2026-05-08, Issue #356, Epic #357). Documents the
> drift classes detected by [`tools/check_state_drift.py`](../../tools/check_state_drift.py)
> and the recommended operator response for each. Pairs with
> [`docs/contracts/state-semantics-contract.md`](../contracts/state-semantics-contract.md)
> (Set F invariants I3 / I7 / I8 and § 4 transition ownership).

## What this covers

`tools/check_state_drift.py` is a **detection-only** maintenance command.
It walks the canonical state DB and the `.state/workers/` directory and
reports inconsistencies between `runs.status` and the on-disk worker-state
files. It NEVER mutates state.db, NEVER moves worker files, NEVER touches
panes. The contract is deliberate: classification of run outcomes belongs
to the Secretary (Set F § 4 T5 / T7 / T8 / T9); the tool only surfaces
evidence so an operator can decide.

Distinct from:

- [`tools/state_db/drift_check.py`](../../tools/state_db/drift_check.py) —
  DB → `.state/org-state.md` markdown round-trip checker (Issue #267).
- [`tools/sweep_stale_workers.py`](../../tools/sweep_stale_workers.py) —
  bulk archival of orphan worker-state files whose `task_id` is absent
  from `org-state.md` (Issue #264). `check_state_drift` does **not**
  classify orphans; that surface is owned by `sweep_stale_workers`.

## Exit-code contract

| Code | Meaning |
|---|---|
| `0` | No drift detected. |
| `1` | Drift detected. **Warn-only signal** — no remediation applied. |
| `2` | Tool failure (DB missing, IO error, malformed schema). |

Runbooks and CI gates can rely on `rc==1` to surface "investigate" without
triggering automated state mutation.

## Drift classes

Each class names a contract surface and the documented operator action.
"Operator-ambiguous" means the drift class requires human confirmation
before any state.db write; "mechanical" means the recovery is a
file-system move with no state.db transition.

### D1 — `queued_stale`

**What it means.** A `runs.status='queued'` row whose `dispatched_at` is
older than `--queued-stale-seconds` (default `300`). Per Set F § 2 / I8 a
queued reservation that lingers more than a few seconds is itself a
signal of a failed T2 transition — most likely `SPLIT_CAPACITY_EXCEEDED`
without Secretary cleanup, or a `spawn_claude_pane` failure that the
dispatcher did not report back.

**Why warn-only.** `runs.status='queued' → 'abandoned'` (T8) is a
**Secretary-owned** transition that the contract currently flags as
*prescribed but not yet implemented* (§ 4 T8). Auto-healing here would
both pre-empt the Secretary's outcome judgment and write a transition
that has no production callsite yet.

**Operator action.**
1. Inspect dispatcher state: was `SPLIT_CAPACITY_EXCEEDED` reported and
   missed? Did `spawn_claude_pane` fail without a peer notification?
2. If the reservation should be released, confirm with the Secretary
   and apply T8 once the prescribed write path lands. Until then, the
   queued row is documented stale state and the operator may close it
   manually via the Secretary skill flow.

**Threshold guidance.** The default `300s` is a Windows-tolerant safety
margin. On hot caches the typical `queued → in_use` window is < 5s; raise
the threshold (e.g., `--queued-stale-seconds 600`) on slow base-clone
fan-outs, lower it to `60` only on instrumented test rigs.

### D2 — `live_run_missing_worker_file`

**What it means.** A `runs.status IN ('in_use','review')` row whose
`.state/workers/worker-{task_id}.md` file is absent. This is a
steady-state breach of Set F I3 (pane-liveness vs. run-status coherence).

**Why this fires under `/org-suspend` is **not** an exception.** I3
permits the *pane* to be closed across `/org-suspend`, but the
worker-state .md file persists on disk for the resume hand-off
([`/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md)).
A missing .md therefore signals genuine drift even when
`org_sessions.status='SUSPENDED'`; this tool does not suppress it.

**Why warn-only.** The contract recovery is T7 (`in_use → abandoned`),
which Set F § 4 T7 also flags as prescribed-but-not-yet-implemented. The
Secretary classifies and writes the terminal status (Set B § 2 T7); the
dispatcher only writes the worker-state-file `Status: pane_closed`.

**Operator action.**
1. Confirm with the Dispatcher whether `WORKER_PANE_EXITED` was missed.
2. If the worker is genuinely gone, apply T7 once the prescribed write
   path lands. Today the run row remains `in_use` until manually
   reconciled.

### D3 — `completed_run_worker_file_present`

**What it means.** A `runs.status='completed'` row whose
`.state/workers/worker-{task_id}.md` is still in the live workers
directory (not under `.state/workers/archive/`). The post-commit hook
at [`tools/state_db/writer.py:597`](../../tools/state_db/writer.py)
should have moved it on the T5 transition.

**Why this happens.** Two realistic causes:
- A direct SQL `UPDATE runs SET status='completed' …` was issued,
  bypassing `StateWriter.update_run_status` and therefore the
  post-commit hook (Set F § 4 calls this out as forbidden).
- A transient IO error during the post-commit move.

**Why mechanical recovery is safe.** The DB is already at the terminal
state — no Secretary classification is owed. The remediation is a pure
file-system move that completes the work the hook intended to do.

**Operator action.** Move the file:

```bash
mv .state/workers/worker-{task_id}.md \
   .state/workers/archive/worker-{task_id}.md
```

Then re-run `tools/check_state_drift.py` to confirm the record clears.

### D4 — `terminal_nonarchived_worker_file` *(future-covered)*

**What it means.** A `runs.status IN ('failed','abandoned')` row whose
.md is still in the live workers directory.

**Why this is currently empty in practice.** Per Set F § 4 the
`failed` / `abandoned` write paths are *prescribed but not yet
implemented*; no production callsite emits them today. Detection is
included so the tool covers the contract surface — when T7 / T8 / T9
activate, this class will start firing and the same mechanical recovery
as D3 applies (file move; DB row is already terminal).

## What this tool does NOT detect

- **Orphan worker-state files** (no DB row at all). Owned by
  [`tools/sweep_stale_workers.py`](../../tools/sweep_stale_workers.py).
- **Markdown drift** between state.db and `.state/org-state.md`. Owned
  by [`tools/state_db/drift_check.py`](../../tools/state_db/drift_check.py).
- **`worker_dirs.lifecycle` drift** vs. on-disk worktree presence.
  Out of scope for this iteration; Set F I7 explicitly decouples
  `worker_dirs.lifecycle` from `runs.status`.

## Cadence

Run `tools/check_state_drift.py` whenever the operator suspects state
drift (e.g., after a dispatcher crash, after manual SQL, before
`/org-suspend`). It is safe to run against a live DB — WAL allows
concurrent readers and the tool opens read-only.

## See also

- [`docs/contracts/state-semantics-contract.md`](../contracts/state-semantics-contract.md) — Set F state semantics (I3, I7, I8, § 4).
- [`docs/contracts/delegation-lifecycle-contract.md`](../contracts/delegation-lifecycle-contract.md) — Set B abstract lifecycle (T5, T7, T8).
- [`tools/check_state_drift.py`](../../tools/check_state_drift.py) — implementation.
- [`tests/test_check_state_drift.py`](../../tests/test_check_state_drift.py) — coverage per drift class.
