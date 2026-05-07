# Journal Event Catalog (claude-org-ja)

> **Storage update (M4 / Issue [#267](https://github.com/suisya-systems/claude-org-ja/issues/267)).**
> Journal events are now written to the **`events` table inside `.state/state.db`**, not to a flat `.state/journal.jsonl` file. `.state/journal.jsonl` was retired at M4 and is no longer the SoT — any historical jsonl file present in a repo is migration-only and is **not** appended to.
> The writer wrappers (`tools/journal_append.sh` / `tools/journal_append.py`) keep their public CLI shape but route writes into state.db. Readers should query the DB (`tools/state_db/queries.py` or direct SQL) rather than tail the jsonl file.
> The canonical state surface — including journal events — is defined in [`docs/contracts/state-semantics-contract.md`](contracts/state-semantics-contract.md) § 1.1; this catalog documents the **event vocabulary and per-event payload shape**, which is unchanged across the M4 cutover.

> **Scope: org-specific.** This document catalogs the event types
> claude-org-ja emits via `tools/journal_append.{sh,py}` (DB-routed
> since M4). The wire-format and reader-tolerance contract live in
> core-harness
> ([`docs/journal-contract.md`](https://github.com/suisya-systems/core-harness/blob/v0.3.0/docs/journal-contract.md));
> this file documents the *what* (which events / which fields), which
> Layer 1 deliberately does not own (Q11 B, design PR #196 §4 Step D).

The journal is consumed informally (retros, dashboard readers, ad-hoc
SQL via `sqlite3 .state/state.db`). Field shapes here are descriptive
and may evolve; consumers should tolerate unknown fields gracefully.

## Reserved envelope

Each event row in the `.state/state.db` `events` table has the
following columns (see `tools/state_db/schema.sql`):

| Column          | Type                              | Purpose                                                     |
|-----------------|-----------------------------------|-------------------------------------------------------------|
| `id`            | INTEGER PK AUTOINCREMENT          | Row id                                                      |
| `occurred_at`   | TEXT (ISO-8601 UTC, sub-second)   | Append time. Default `strftime('%Y-%m-%dT%H:%M:%fZ','now')` (e.g. `2026-05-08T01:02:03.456Z`) |
| `actor`         | TEXT (nullable)                   | Originating role (`secretary` / `dispatcher` / …) when known |
| `kind`          | TEXT NOT NULL                     | Event name (one of the entries below)                       |
| `run_id` / `workstream_id` / `project_id` | INTEGER FK (nullable) | Optional join keys |
| `payload_json`  | TEXT NOT NULL (JSON object)       | Per-event typed fields documented below                     |

The CLI wrappers (`tools/journal_append.{sh,py}`) take the event name
as a positional argument and route it into the `kind` column,
synthesize `occurred_at`, and pack remaining `k=v` / `--json` fields
into `payload_json`. Pre-M4 jsonl rows used a flat `{ts, event, …}`
envelope; that shape is retained inside `payload_json` only when the
caller passes it through, but the **column-level reserved keys are
`occurred_at` / `kind`**.

## Writers

| Writer                     | Mechanism                                                             |
|----------------------------|-----------------------------------------------------------------------|
| Dispatcher (cwd=.dispatcher/) | `bash ../tools/journal_append.sh <event> ...` (Step D shim)        |
| Secretary skills (cwd=repo root) | `bash tools/journal_append.sh <event> ...` or `py -3 tools/journal_append.py` for typed payload |
| `org-start` identity recovery | `bash tools/journal_append.sh secretary_identity_restored ...`     |

The wrappers resolve their location via `${BASH_SOURCE[0]}` /
`__file__` and write into the `events` table of
`<repo_root>/.state/state.db` regardless of caller cwd (M4 routing).
Only the script *path* in the invocation depends on cwd (relative to
where the caller runs). Pre-M4 these wrappers wrote to
`<repo_root>/.state/journal.jsonl`; the file is retired and any
remnant is migration-only.

Workers do **not** write the journal directly; they report via
`send_message` and the dispatcher / secretary persists the event.

## Per-event annotations (Set A Q3 / Set B Q2)

Each event row below carries two contract-required annotations in
addition to its writer / payload shape:

- **Emitted by** — the role(s) whose action *originates* the event
  (one of `secretary`, `dispatcher`, `worker`, `curator`, or a
  comma-separated combination). This may differ from the **Writer**
  column: workers do not write the journal directly, so events
  originating from a worker action (e.g. `worker_completed`,
  `worker_reported`, `plan_delivered`) are emitted-by `worker` but
  written by the secretary on receipt of the corresponding peer
  message. Source: Set A Q3 ratification, role-contract §
  Authoritative journal events.
- **Required for** — the lifecycle transition (Set B
  `docs/contracts/delegation-lifecycle-contract.md` §2 T1–T8 *Journal*
  line, or §3 E1–E5 detection / de-dup ledger reference) for which
  emission of this event is contract-mandated. The scope is
  deliberately narrow: events that merely *appear* in §1's
  per-state "visible journal events" column, or that are referenced
  by §1.5 / §4 prose without a mandatory-emission requirement, are
  marked `—`. `—` therefore covers both informational /
  observability events and lifecycle-adjacent events whose emission
  is not contract-mandated. Source: Set B Q2 ratification.

## Event types

### Worker lifecycle

| Event                    | Typical fields                                              | Writer       | Emitted by | Required for | Notes |
|--------------------------|-------------------------------------------------------------|--------------|------------|--------------|-------|
| `worker_spawned`         | `worker`, `dir`, `task`                                     | dispatcher   | dispatcher | T2           | After MCP `spawn_pane`. |
| `worker_completed`       | `worker`, `task`                                            | secretary    | worker     | T4           | Worker reported done. |
| `worker_closed`          | `worker`, `pane_id`                                         | dispatcher   | dispatcher | T5, T7       | Pane closed, registry updated. |
| `worker_reported`        | `worker`, `task`, `summary`                                 | secretary    | worker     | T3           | Mid-task report received. |
| `worker_review`          | `worker`, `task`, `outcome`                                 | secretary    | secretary  | —            | Review verdict on a worker's report. Visible at §1 awaiting_review row but not on T4's mandatory-Journal line. |
| `worker_report_forwarded`| `worker`, `task`, `recipient`                               | secretary    | secretary  | —            | Forwarded to human / other. |
| `worktree_removed`       | `path`, `task`                                              | dispatcher   | dispatcher | T5 (Pattern B) | Worktree cleanup. |
| `retro_deferred`         | `worker`, `reason`                                          | dispatcher   | dispatcher | —            | Retro Steps 1–2 could not be completed before `close_pane` (e.g., secretary unreachable within 5 minutes); pane close skipped. Listed at Set B §1 aborted row as a visible journal event, but no §2 transition's mandatory-Journal line cites it. |

### Delegate flow

| Event                | Typical fields                                              | Writer    | Emitted by | Required for |
|----------------------|-------------------------------------------------------------|-----------|------------|--------------|
| `delegate_sent`      | `task`, `worker`, `dir`                                     | secretary | secretary  | T1           |
| `delegate_resume`    | `task`, `worker`                                            | secretary | secretary  | —            |
| `delegate_resume_r2` | `task`, `worker`, `round`                                   | secretary | secretary  | —            |

### Plan / design

| Event                                  | Typical fields                          | Writer    | Emitted by | Required for |
|----------------------------------------|-----------------------------------------|-----------|------------|--------------|
| `plan_delivered`                       | `task`, `worker`                        | secretary | worker     | —            |
| `plan_approved`                        | `task`                                  | secretary | secretary  | —            |
| `plan_approved_and_prep_dispatched`    | `task`, `prep_worker`                   | secretary | secretary  | —            |
| `prep_delivered`                       | `task`, `worker`                        | secretary | worker     | —            |
| `design_approved`                      | `task`, `pr`                            | secretary | secretary  | —            |
| `drift_reaudit`                        | `task`, `reason`                        | secretary | secretary  | —            |

### PR / push

| Event           | Typical fields                          | Writer    | Emitted by | Required for |
|-----------------|-----------------------------------------|-----------|------------|--------------|
| `fix_pushed`    | `task`, `branch`, `commit`              | secretary | secretary  | —            |
| `pr_opened`     | `task`, `pr`, `url`                     | secretary | secretary  | —            |
| `prs_opened`    | `count`, `prs[]`                        | secretary | secretary  | —            |
| `pr_merged`     | `pr`, `task`                            | secretary | secretary  | —            |
| `prs_merged`    | `count`, `prs[]`                        | secretary | secretary  | —            |
| `prs_pushed`    | `count`, `branches[]`                   | secretary | secretary  | —            |

### History / phase markers

| Event                          | Typical fields                          | Writer    | Emitted by | Required for |
|--------------------------------|-----------------------------------------|-----------|------------|--------------|
| `pre_history_reset_snapshot`   | `path`                                  | secretary | secretary  | —            |
| `phase_d_snapshot`             | `path`                                  | secretary | secretary  | —            |
| `phase_d_complete`             | `task`                                  | secretary | secretary  | —            |
| `phase_d_force_push`           | `branch`                                | secretary | secretary  | —            |
| `pane_closed`                  | `pane_id`, `worker`                     | dispatcher| dispatcher | —            |

### Issues

| Event             | Typical fields                          | Writer    | Emitted by | Required for |
|-------------------|-----------------------------------------|-----------|------------|--------------|
| `issue_filed`     | `issue`, `title`                        | secretary | secretary  | —            |
| `issues_filed`    | `count`, `issues[]`                     | secretary | secretary  | —            |
| `issues_swept`    | `count`                                 | secretary | secretary  | —            |
| `issue_closed`    | `issue`                                 | secretary | secretary  | —            |

### Observability

| Event              | Typical fields                          | Writer     | Emitted by | Required for |
|--------------------|-----------------------------------------|------------|------------|--------------|
| `anomaly_observed` | `worker`, `kind`, `confidence`, `note`  | dispatcher | dispatcher | E2 (conditional) |
| `notify_sent`      | `recipient`, `kind`, `summary`          | dispatcher | dispatcher | E2, E3 (de-dup ledger) |
| `events_dropped`   | `count`, `since_ts`                     | dispatcher | dispatcher | —            |

### CI

| Event          | Typical fields                                            | Writer    | Emitted by | Required for |
|----------------|-----------------------------------------------------------|-----------|------------|--------------|
| `ci_completed` | `pr`, `repo`, `status`, `duration_sec`                    | secretary | secretary  | E4           |

`status` ∈ `{passed, failed, incomplete, canceled}`. As of Issue #224
the value is derived from `gh pr checks <pr> --json bucket,state,name`
(per-check `bucket`, whose documented values are
`{pass, fail, pending, skipping, cancel}`) rather than the gh process'
exit code, so a transient watch-loop error is no longer conflated
with a real CI failure. `failed` requires at least one `fail` or
`cancel` bucket; `incomplete` is emitted when at least one check is
still `pending` (or has an unrecognized bucket, or the JSON probe
itself errored — see the fallback rules in `tools/pr_watch.py`);
`canceled` is emitted only when the parent receives SIGINT.

### Session lifecycle

| Event                          | Typical fields                          | Writer    | Emitted by | Required for |
|--------------------------------|-----------------------------------------|-----------|------------|--------------|
| `suspend`                      | `reason`, `active_workers[]`, `pending_items[]` | secretary | secretary  | —            |
| `resume`                       | `restored_workers[]`, `note`            | secretary | secretary  | —            |
| `task_completed`               | `task`                                  | secretary | secretary  | —            |
| `secretary_identity_restored`  | `note`                                  | org-start | secretary  | —            |

## Adding a new event type

1. Pick a snake_case name; check it does not collide with an existing
   one in this catalog.
2. Decide on the payload fields. Prefer flat string/number/bool keys
   for ergonomic `jq` queries; nested objects are allowed but require
   the Python entry point (`tools/journal_append.py --json '...'`).
3. Add a row to the relevant table above, including the **Emitted
   by** and **Required for** annotations (see "Per-event annotations"
   above for the value vocabulary).
4. Use the helper to write:
   - bash: `bash tools/journal_append.sh <event> k=v k2=v2`
   - python: `py -3 tools/journal_append.py <event> k=v --json '{"nested": {...}}'`

Do **not** hand-craft direct DB inserts (`sqlite3 .state/state.db
"INSERT INTO events ..."`) or the legacy `printf '%s\n' '{...}' >>
.state/journal.jsonl` pattern — the helper handles timestamp
generation, JSON escaping, schema validation, and reserved-key
checking. Direct INSERTs bypass these checks and direct jsonl writes
go to a retired sink (M4).
