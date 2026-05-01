# Journal Event Catalog (claude-org-ja)

> **Scope: org-specific.** This document catalogs the event types
> claude-org-ja writes to `.state/journal.jsonl`. The wire-format and
> reader-tolerance contract live in core-harness
> ([`docs/journal-contract.md`](https://github.com/suisya-systems/core-harness/blob/v0.3.0/docs/journal-contract.md));
> this file documents the *what* (which events / which fields), which
> Layer 1 deliberately does not own (Q11 B, design PR #196 §4 Step D).

The journal is consumed informally (retros, ad-hoc `tail` / `jq`,
dashboard readers in the future). Field shapes here are descriptive
and may evolve; consumers should tolerate unknown fields gracefully.

## Reserved envelope (from core-harness)

Every line carries the two reserved keys defined by Layer 1:

| Key     | Type                              | Purpose                                  |
|---------|-----------------------------------|------------------------------------------|
| `ts`    | string, `YYYY-MM-DDTHH:MM:SSZ`    | Append time (ISO-8601 UTC, second prec.) |
| `event` | string                            | Event name (one of the entries below)    |

## Writers

| Writer                     | Mechanism                                                             |
|----------------------------|-----------------------------------------------------------------------|
| Dispatcher                 | `bash tools/journal_append.sh <event> ...` (Step D shim)              |
| Secretary skills           | Same wrapper (bash) or `py -3 tools/journal_append.py` for typed payload |
| `org-start` identity recovery | `bash tools/journal_append.sh secretary_identity_restored ...`     |

Workers do **not** write the journal directly; they report via
`send_message` and the dispatcher / secretary persists the event.

## Event types

### Worker lifecycle

| Event                    | Typical fields                                              | Writer       | Notes |
|--------------------------|-------------------------------------------------------------|--------------|-------|
| `worker_spawned`         | `worker`, `dir`, `task`                                     | dispatcher   | After MCP `spawn_pane`. |
| `worker_completed`       | `worker`, `task`                                            | secretary    | Worker reported done. |
| `worker_closed`          | `worker`, `pane_id`                                         | dispatcher   | Pane closed, registry updated. |
| `worker_reported`        | `worker`, `task`, `summary`                                 | secretary    | Mid-task report received. |
| `worker_review`          | `worker`, `task`, `outcome`                                 | secretary    | Review verdict on a worker's report. |
| `worker_report_forwarded`| `worker`, `task`, `recipient`                               | secretary    | Forwarded to human / other. |
| `worktree_removed`       | `path`, `task`                                              | dispatcher   | Worktree cleanup. |

### Delegate flow

| Event                | Typical fields                                              | Writer    |
|----------------------|-------------------------------------------------------------|-----------|
| `delegate_sent`      | `task`, `worker`, `dir`                                     | secretary |
| `delegate_resume`    | `task`, `worker`                                            | secretary |
| `delegate_resume_r2` | `task`, `worker`, `round`                                   | secretary |

### Plan / design

| Event                                  | Typical fields                          | Writer    |
|----------------------------------------|-----------------------------------------|-----------|
| `plan_delivered`                       | `task`, `worker`                        | secretary |
| `plan_approved`                        | `task`                                  | secretary |
| `plan_approved_and_prep_dispatched`    | `task`, `prep_worker`                   | secretary |
| `prep_delivered`                       | `task`, `worker`                        | secretary |
| `design_approved`                      | `task`, `pr`                            | secretary |
| `drift_reaudit`                        | `task`, `reason`                        | secretary |

### PR / push

| Event           | Typical fields                          | Writer    |
|-----------------|-----------------------------------------|-----------|
| `fix_pushed`    | `task`, `branch`, `commit`              | secretary |
| `pr_opened`     | `task`, `pr`, `url`                     | secretary |
| `prs_opened`    | `count`, `prs[]`                        | secretary |
| `pr_merged`     | `pr`, `task`                            | secretary |
| `prs_merged`    | `count`, `prs[]`                        | secretary |
| `prs_pushed`    | `count`, `branches[]`                   | secretary |

### History / phase markers

| Event                          | Typical fields                          | Writer    |
|--------------------------------|-----------------------------------------|-----------|
| `pre_history_reset_snapshot`   | `path`                                  | secretary |
| `phase_d_snapshot`             | `path`                                  | secretary |
| `phase_d_complete`             | `task`                                  | secretary |
| `phase_d_force_push`           | `branch`                                | secretary |
| `pane_closed`                  | `pane_id`, `worker`                     | dispatcher|

### Issues

| Event             | Typical fields                          | Writer    |
|-------------------|-----------------------------------------|-----------|
| `issue_filed`     | `issue`, `title`                        | secretary |
| `issues_filed`    | `count`, `issues[]`                     | secretary |
| `issues_swept`    | `count`                                 | secretary |
| `issue_closed`    | `issue`                                 | secretary |

### Observability

| Event              | Typical fields                          | Writer     |
|--------------------|-----------------------------------------|------------|
| `anomaly_observed` | `worker`, `kind`, `confidence`, `note`  | dispatcher |
| `notify_sent`      | `recipient`, `kind`, `summary`          | dispatcher |
| `events_dropped`   | `count`, `since_ts`                     | dispatcher |

### Session lifecycle

| Event                          | Typical fields                          | Writer    |
|--------------------------------|-----------------------------------------|-----------|
| `suspend`                      | `reason`, `active_workers[]`, `pending_items[]` | secretary |
| `resume`                       | `restored_workers[]`, `note`            | secretary |
| `task_completed`               | `task`                                  | secretary |
| `secretary_identity_restored`  | `note`                                  | org-start |

## Adding a new event type

1. Pick a snake_case name; check it does not collide with an existing
   one in this catalog.
2. Decide on the payload fields. Prefer flat string/number/bool keys
   for ergonomic `jq` queries; nested objects are allowed but require
   the Python entry point (`tools/journal_append.py --json '...'`).
3. Add a row to the relevant table above.
4. Use the helper to write:
   - bash: `bash tools/journal_append.sh <event> k=v k2=v2`
   - python: `py -3 tools/journal_append.py <event> k=v --json '{"nested": {...}}'`

Do **not** hand-craft `printf '%s\n' '{"ts": ..., "event": ...}' >>
.state/journal.jsonl` — the helper handles timestamp generation, JSON
escaping, file locking, and reserved-key validation. The raw `>>`
pattern is the legacy approach replaced in this PR.
