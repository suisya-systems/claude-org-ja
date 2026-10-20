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
| Codex round-cap hook (`.hooks/block-codex-over-cap.sh` → `tools/codex_round_cap.py hook`) | Writes `codex_round_started` / `codex_round_denied` on the worker's behalf. The hook runs outside the worker's Bash sandbox, so it can write `state.db` that the worker itself cannot |

The wrappers resolve their location via `${BASH_SOURCE[0]}` /
`__file__` and write into the `events` table of
`<repo_root>/.state/state.db` regardless of caller cwd (M4 routing).
Only the script *path* in the invocation depends on cwd (relative to
where the caller runs). Pre-M4 these wrappers wrote to
`<repo_root>/.state/journal.jsonl`; the file is retired and any
remnant is migration-only.

Workers do **not** write the journal directly; they report via
`send_message` and the dispatcher / secretary persists the event. The
Codex round-cap hook is the one writer that acts on a worker's tool call,
and it is a PreToolUse hook outside the worker's control, not the worker.

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
| `worker_spawned`         | `worker`, `dir`, `task`                                     | dispatcher   | dispatcher | T2           | After MCP `spawn_pane`. Records that a pane was created — **not** that the spawn ceremony (approval Enter / `list_peers` registration / instruction send) succeeded; that is `worker_spawn_verified`. |
| `worker_spawn_verified`  | `task`, `worker`, `pane_id`, `peer_id`, `peer_cwd`, `evidence`, `approval`, `instruction`, `transport` | dispatcher | `tools/spawn_gate.py verify` | — | Written by the Step 5 gate in `.dispatcher/references/spawn-flow.md`, immediately before `DELEGATE_COMPLETE`. `evidence` is `list_peers` (normal 3-4 enumeration; `peer_cwd` is then machine-checked against the immutable `delegate_sent` `dir`, **not** the rewritable `runs.worker_dir_id`) or `send_delivery` (documented 3-4 degraded path: enumeration discarded, `peer_*` null, no machine-checked half). `approval` / `instruction` are dispatcher attestations in both modes. A `worker_spawned` with no `worker_spawn_verified` at or after it is what `tools/spawn_gate.py audit` reports — the 2026-08-18 false-completion incidents left exactly that gap. |
| `worker_completed`       | `worker`, `task`                                            | secretary    | worker     | T4           | Worker reported done. |
| `worker_closed`          | `worker`, `pane_id`                                         | dispatcher   | dispatcher | T5, T7       | Pane closed, registry updated. |
| `worker_reported`        | `worker`, `task`, `summary`                                 | secretary    | worker     | T3           | Mid-task report received. |
| `worker_review`          | `worker`, `task`, `outcome`                                 | secretary    | secretary  | —            | Review verdict on a worker's report. Visible at §1 awaiting_review row but not on T4's mandatory-Journal line. |
| `worker_report_forwarded`| `worker`, `task`, `recipient`                               | secretary    | secretary  | —            | Forwarded to human / other. |
| `worktree_removed`       | `path`, `task`                                              | dispatcher   | dispatcher | T5 (Pattern B) | Worktree cleanup. |
| `retro_gate_acked`       | `task`                                                      | dispatcher   | `tools/dispatcher_retro_gate.py` | — | Appended when the retro completion-report gate returns `acked`. `.hooks/block-close-before-retro.sh` reads it (only rows after the task's latest `worker_spawn_verified`) to let the dispatcher's `close_pane` through; the other pass cases are `retro_gate_confirmed_missing`, `runs.status` in `completed` / `failed` / `abandoned`, and `completion_reported_at` with `runs.status == 'review'` (`.dispatcher/references/pane-close.md`). |
| `retro_gate_confirmed_missing` | `task` | dispatcher | dispatcher (`tools/journal_append.sh`) | — | Appended when the retro gate ends `replied_no_ack` and the secretary's reply confirms the completion report never arrived (`.dispatcher/references/pane-close.md`, exit 3 branch). `.hooks/block-close-before-retro.sh` treats it like `retro_gate_acked` (only rows after the task's latest `worker_spawn_verified`). |
| `retro_deferred`         | `worker`, `reason`                                          | dispatcher   | dispatcher | —            | Retro Steps 1–2 could not be completed before `close_pane` (e.g., secretary unreachable within 5 minutes); pane close skipped. Listed at Set B §1 aborted row as a visible journal event, but no §2 transition's mandatory-Journal line cites it. |

### Delegate flow

| Event                | Typical fields                                              | Writer    | Emitted by | Required for |
|----------------------|-------------------------------------------------------------|-----------|------------|--------------|
| `delegate_sent`      | `task`, `worker`, `dir`, `base_ref`                         | secretary | `gen_delegate_payload.py apply` | T1 |
| `delegate_resume`    | `task`, `worker`                                            | secretary | secretary  | —            |
| `delegate_resume_r2` | `task`, `worker`, `round`                                   | secretary | secretary  | —            |
| `self_edit_approval_sent` | `task`, `pane`, `files`, `parts`, `verified_at`, `backend`, `transport` | secretary | `tools/self_edit_approval.py send` | — |

`delegate_sent` is written by `tools/gen_delegate_payload.py apply` in the same transaction as the T1 reservation (`runs.status='queued'`), not by a hand-typed `journal_append` — see [`docs/contracts/delegation-lifecycle-contract.md`](./contracts/delegation-lifecycle-contract.md) §2 T1. It records the commitment to delegate, immediately before the `DELEGATE` message is sent; it is **not** proof of delivery, which is what T2's `worker_spawned` records. `base_ref` is the task's review base (`origin/<base branch>` when `--base-ref` or the registry sets one, else `origin/main`); the argument-less Codex gate reads it through `tools/codex_round_cap.py base-ref`, and a row written before the key existed reads as `origin/main`. It is emitted exactly once per run row: re-applying a still-`queued` delegation updates the reservation without appending a second event.

`self_edit_approval_sent` is written by `tools/self_edit_approval.py send`
after it has **verified both halves** of the `.claude/**` self-edit approval
handshake ([`.claude/skills/org-delegate/references/claude-org-self-edit.md`](../.claude/skills/org-delegate/references/claude-org-self-edit.md) §5):
that the approval text landed in the worker's composer, and that it then
*left* the composer when Enter was sent. The second half is the point —
text and Enter in one call makes the text a bracketed paste that swallows
the Enter, so the approval stays behind as an unsent draft that looks
exactly like a delivered one under `inspect_pane`, while the worker waits
for a user message it never received (observed 2026-07-31 and 2026-08-25;
Issue #956). The row is therefore **evidence of submission, not merely of
sending**, and it is written only on success: a stage that fails exits
non-zero and records nothing. A long approval is split by the pane width
into numbered parts (`parts`), each verified the same way; the one row is
written only after every part landed and was submitted, so a run that
stops part-way records nothing.

A `delegate_sent` whose worker dir lies inside the claude-org repo — the
durable trace of the `claude-org-self-edit` role, which is what routes the
worker dir there (`tools/resolve_worker_layout.py`) and is otherwise not
persisted — with no `self_edit_approval_sent` at or after it is what
`tools/self_edit_approval.py audit` reports. That test is a **superset**:
a self-edit-role dispatch that never touched `.claude/**` needs no
approval and shows up as a false positive, which is the safe direction for
a detector whose misses are silent.

The kind is deliberately **not** registered in `tools/relay_scan.py`'s
`TERMINAL_KINDS` (step 5 below): the relay exists to carry events *to* the
secretary, and the secretary is the actor that writes this one.

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
| `fix_pushed`    | `task`, `branch`, `head` (別名 `commit` / `sha`) | secretary | secretary  | —            |
| `pr_opened`     | `task`, `pr`, `url`                     | secretary | secretary  | —            |
| `prs_opened`    | `count`, `prs[]`                        | secretary | secretary  | —            |
| `pr_merged`     | `task`, `pr`, `repo`, `pr_url`, `merge_commit`, `head`, `merged_at`, `pattern`, `auto_completed` | run_complete_on_merge | `run_complete_on_merge.py` | —            |
| `prs_merged`    | `count`, `prs[]`                        | secretary | secretary  | —            |
| `prs_pushed`    | `count`, `branches[]`                   | secretary | secretary  | —            |

`fix_pushed` の sha フィールドは、この catalog が長らく `commit` と書いて
いた一方で、**実際の emitter は一貫して `head` を書いてきた**（本リポジトリ
の live DB では 69 行中 `head` が 47 行、`commit` は 0 行、残り 22 行は
sha を自由記述の `note` にしか持たない）。ここは表を実態側に合わせてある。
読み手（現状 [`tools/watcher_restart_guard.py`](../tools/watcher_restart_guard.py)）は
`commit` / `head` / `sha` の順に読むこと — 文書上の名前だけを読むと、その
比較は本番では常に空振りする dead code になる。新規に書く側は `head` を
使うのが望ましいが、既存 3 名のいずれも受理される。

`pr_merged` is written by `tools/run_complete_on_merge.py` in the **same transaction** as
`pr_state='merged'` / `commit` / `completed_at` (see the `append_event` call at
[`tools/run_complete_on_merge.py`](../tools/run_complete_on_merge.py) `kind="pr_merged"`), not by a
hand-typed `journal_append.sh`. **A hand-written `pr_merged` is deprecated**: the helper already
appends the row, so typing one adds a *second* events row for the same merge. Both rows are relayed
independently by [`tools/relay_scan.py`](../tools/relay_scan.py) (the outbox ledger keys on
`source_event_id`, so two rows are two deliveries), and the hand-written one carries no `head` —
which the secretary's watcher-cleanup freshness gate cannot match, so the watcher pane is left
running (Issue #954 / the Issue #751 re-entry path). The helper's idempotency covers **re-running
the helper**; it does not deduplicate a row a human appended separately.

### Codex review gate / worker model

| Event                     | Typical fields                                   | Writer    | Emitted by | Required for |
|---------------------------|--------------------------------------------------|-----------|------------|--------------|
| `codex_round_started`     | `task`, `mode`, `round`, `allowed`, `worker_dir` | `codex-round-cap-hook` | `tools/codex_round_cap.py hook` | — (round count = number of rows per `task`) |
| `codex_round_denied`      | `task`, `mode`, `used`, `allowed`, `worker_dir`  | `codex-round-cap-hook` | `tools/codex_round_cap.py hook` | — (relayed to the secretary) |
| `codex_round_cap_changed` | `task`, `allowed` (int or `"unlimited"`), `previous`, `reason` | secretary | `tools/codex_round_cap.py set` | — |
| `worker_model_set`        | `task`, `model` (`sonnet` / `opus`), `reason`    | dispatcher / secretary | dispatcher (spawn-flow Step 4), secretary (promotion) | — |

The Codex round cap is enforced outside the worker. `.hooks/block-codex-over-cap.sh`
sees every worker Bash call that mentions `codex`: a direct `codex` invocation is
refused, and the only permitted entry is `bash "<claude_org_path>/tools/codex_review_gate.sh" [<base-ref>]`
(workers use the argument-less form, which reads the base from `delegate_sent`'s `base_ref`).
Each permitted gate call appends one `codex_round_started` in the same transaction
that counts the earlier ones; once `used` reaches `allowed` the call is refused and
`codex_round_denied` is appended instead. The task is resolved from the newest
`delegate_sent` whose `dir` matches the worker's `WORKER_DIR`.

`mode` is `diff` for `bash "<claude_org_path>/tools/codex_review_gate.sh" [<base-ref>]`
and `design` for `... --design [<file>]`; a row without `mode` counts as `diff`. The two
are counted separately: `round` / `used` count rows of the same mode, diff rounds are
checked against `allowed`, and design is fixed at `allowed: 1` per task (not changed by
`set`), so a design review neither consumes nor is blocked by the diff allowance.
`codex_round_cap.py status` reports `used` (diff) and `design_used`.

A round is consumed when the hook authorises the gate call (PreToolUse), before the
gate runs. A call that then exits early (`CODEX_GATE: dirty-worktree` or
`CODEX_GATE: unavailable`) still counts, so before raising the allowance after a
`codex_round_denied` the secretary checks why the earlier rounds ended. Workers
also cannot reach Codex through a separate pane: `spawn_codex_pane` is denied for
workers.

`allowed` defaults to 3. Only the secretary raises it, with
`python3 tools/codex_round_cap.py set --task-id <id> --allowed <N|unlimited> --reason <text>`;
the newest `codex_round_cap_changed` for the task wins. A brief that names a
different cap does not change enforcement by itself — the secretary runs `set`
at delegate time.

`codex_round_denied` is in `tools/relay_scan.py`'s `TERMINAL_KINDS`, so the
dispatcher relays it to the secretary as
`CODEX_ROUND_DENIED: worker-<task> (task_id=<task>, used=<n>, allowed=<m>) [relay]`
(`mode=design` denials render as `(task_id=<task>, mode=design, used=<n>, allowed=1)`).
The dispatcher only relays; whether to promote the worker to Opus, ask the human,
or stop is the secretary's decision.

`worker_model_set` is the trial record of which model each worker ran on (the
state DB has no model column). `reason` is `default` or `delegate_line` when the
dispatcher spawns, and `promotion:<cap_denied|worker_request|human|enforcement>`
when the secretary promotes a running worker. Codex round counts per task come
from `codex_round_started`.

### Acceptance gate

| Event             | Typical fields                                                                 | Writer    | Emitted by | Required for |
|-------------------|--------------------------------------------------------------------------------|-----------|------------|--------------|
| `acceptance_gate` | `result` (`passed` / `rejected` / `no_manifest`), `reason`, `repo`, `base`, `head`, `manifest_commit`, `manifest_digest`, `paths[]`, `pr`, `task` | `tools/acceptance_gate.py` | secretary (pre-merge hook `.hooks/gate-acceptance-before-merge.sh`) | — |

`tools/acceptance_gate.py` writes one row per run. The gate passes only when the
protected tree entries at head equal those at the commit that declared the test
(`Acceptance-Test: <path>` trailers), the test fails on base and it passes on head.
`manifest_commit` / `paths` are absent when no manifest was found or the run was
rejected before reading it. Without `--pr` the row is a measurement record, not
the verdict: a failed write only warns on stderr and the exit code stays the
gate's verdict. With `--pr` (the pre-merge hook passes `OWNER/REPO#N`) the row
is also the pin: the first row of that PR carrying a `manifest_digest` (a hash
of the declared test files' paths, modes and blobs) is compared with every
later run, a changed or vanished manifest is rejected, and a failed write
rejects.

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
| `sandbox_deny_skipped` | `role`, `worker?`, `layer=layer_3`, `entry`, `reason`, `phase=case_a\|case_e`, `source=render_suppression\|bootstrap_retry\|bwrap_unavailable`, `attempt`, `fail_if_unavailable`, `bwrap_exit?`, `bwrap_stderr_excerpt?`, `severity`, `audience`, `dedupe_key`, `suppressed_by_default` | runtime / launcher | secretary, dispatcher, curator, worker | — |

`sandbox_deny_skipped` records that a Layer-3 sandbox deny entry was
skipped before or during bwrap startup. `phase=case_e` /
`source=render_suppression` covers the runtime's pre-launcher
realpath-escape suppression (steady-state on WSL); `phase=case_a` /
`source=bootstrap_retry` covers Claude Code's launcher dropping an
entry after a transient `bwrap` mount failure. The full payload schema
(field types, required vs conditional, allowed values) and the
filter contract (curator out-of-scope, dispatcher monitoring
`severity ≥ warning` only) are pinned in
[`docs/contracts/sandbox-launcher-contract.md`](contracts/sandbox-launcher-contract.md)
§3.3. Emit via the journal helper (e.g. `bash tools/journal_append.sh
sandbox_deny_skipped role=worker entry=/home/<user>/.aws/.env
phase=case_a source=bootstrap_retry attempt=1 fail_if_unavailable=false
severity=warning audience=operator suppressed_by_default=false
dedupe_key=<sha256>`); direct DB INSERTs are forbidden per
§"Adding a new event type" below.

### CI

| Event                  | Typical fields                                            | Writer    | Emitted by | Required for |
|------------------------|-----------------------------------------------------------|-----------|------------|--------------|
| `ci_completed`         | `pr`, `repo`, `status`, `duration_sec`, `head`, `fail_count`?, `pending_count`?, `total_checks`?, `retry_recommended`?, `retry_after_sec`?, `probe_attempts`? | secretary | secretary  | E4           |
| `pr_watch_pane_started`| `pr`, `repo`, `pane_id`                                   | secretary | secretary  | —            |
| `pr_conflict_detected` | `pr`, `repo`, `head`, `mergeable`, `merge_state_status`, `ci_settled` | secretary | secretary  | —            |

`status` ∈ `{passed, failed, incomplete, indeterminate, canceled}`.
`head` (Issue #636)
is the short (7-char) sha of the head whose CI verdict this event
records, or `null` when it could not be resolved; with `--merge-watch`
a new commit pushed to the PR branch makes `tools/pr_watch.py` loop back
to ci-watch and emit a fresh `ci_completed` (and `CI_COMPLETED` peer
message) for the new `head`, so the secretary never approves a merge
against a stale verdict. As of Issue #224
the value is derived from `gh pr checks <pr> --json bucket,state,name`
(per-check `bucket`, whose documented values are
`{pass, fail, pending, skipping, cancel}`) rather than the gh process'
exit code, so a transient watch-loop error is no longer conflated
with a real CI failure. `failed` requires at least one `fail` or
`cancel` bucket; `incomplete` is emitted when at least one check was
read but is still `pending` (or has an unrecognized bucket, or the
checks list came back empty). Issue #685 splits the old
overloaded `incomplete`: `indeterminate` is emitted when the
`gh pr checks --json` probe never returned a parseable response within
the retry budget (a gh outage — the verdict could not be read at all),
so a genuine pending CI is distinguishable from a fetch failure and a
real red is no longer degraded to a stalled `incomplete` when its probe
happens to blip. `canceled` is emitted only when the parent receives
SIGINT.

Issue #685 additive payload keys (base keys above are unchanged):

* When the verdict came from a parseable probe, `fail_count`,
  `pending_count`, and `total_checks` record the per-bucket tallies
  (`fail_count` counts `fail`+`cancel`; `pending_count` counts
  `pending`/empty/unrecognized) so a consumer can tell a single-check
  red from a broad failure without re-querying gh.
* When `status == "indeterminate"`, the event instead carries
  `retry_recommended: true`, `retry_after_sec` (the initial retry
  interval), and `probe_attempts` (how many `gh pr checks --json`
  calls were made). This makes the retry schedule explicit so the
  monitoring side reads it as "verdict not yet knowable — re-invoke
  pr_watch" rather than a stalled merge gate.

The probe is retried with exponential backoff (initial 5s, doubling to
a 30s cap) inside `tools/pr_watch.py`, so a transient gh failure
resolves to a definitive `passed`/`failed` before the budget is spent.

`pr_conflict_detected` (Issue #946) is written by `tools/pr_watch.py`
when its ci-watch poll observes `gh pr view --json mergeable` reporting
`CONFLICTING` for the PR's current head. A conflicting head cannot be
merged into base, so GitHub never builds the merge ref and **no
`pull_request` workflow fires at all** — the PR sits at zero checks,
which every check-arrival probe reads as "CI has not started yet". That
is what left kura PR #248 silent on 2026-08-19 until a human noticed.
`head` is the short sha the conflict was observed on (or `"unknown"`
when `headRefOid` was unreadable), `mergeable` is always `CONFLICTING`,
`merge_state_status` carries GitHub's `mergeStateStatus` (typically
`DIRTY`) for triage, and `ci_settled` says whether a CI verdict already
existed when the conflict was seen (`false`: CI never fired and the
watch continues; `true`: CI already reported and the conflict blocks the
merge, not the run) — the `PR_CONFLICT` message carries the matching
advice.

A `PR_CONFLICT: PR #<n> (head=<sha>, ...)` peer message is pushed
alongside the row on the same best-effort `_notify_or_record` path as
the other pr-watch signals, so a dropped push surfaces as `notify_failed`
with `failed_kind: "pr_conflict_detected"`. Unlike the other pr-watch
signals the kind is ALSO in `tools/relay_scan.py`'s `TERMINAL_KINDS`
despite being non-terminal: a pane with no transport configured at all
records no `notify_failed` by design, which would otherwise leave this
row as the only trace of the conflict with nothing to relay it.

The event is **not terminal**: a conflict is cleared by a re-push, and
the new head's CI is exactly what the watcher should go on to observe,
so `tools/pr_watch.py` keeps polling and emits the usual `ci_completed`
for the head that resolves it. It is emitted **at most once per head** —
a conflict persists across every poll until someone pushes, and the head
moving is precisely the event that makes it worth re-announcing.
`mergeable: UNKNOWN` (GitHub computes mergeability asynchronously, so a
freshly pushed head reports it for a few seconds) and an unreadable
probe are both silent: neither records an event nor pushes a message.

The probe runs at three points in a ci-watch round, because a conflict
reaches the watcher in three different shapes:

1. **Zero visible checks** (the kura #248 shape). A confirmed conflict
   here means the missing checks are explained rather than racing, so
   the self-poll loop keeps polling instead of handing off to the
   bounded resolver — which would otherwise spend its budget recording
   a misleading `incomplete`.
2. **During the resolver's retry budget.** Mergeability is computed
   asynchronously, so a freshly pushed conflicting head often reads
   `UNKNOWN` at the moment of the handoff and settles to `CONFLICTING`
   seconds later. The resolver keeps probing and bails out the moment it
   is confirmed, returning control to the self-poll loop.
3. **At verdict time, even on a green verdict.** A terminal check
   verdict does not imply the PR is mergeable: CI can finish green and
   the base branch then move underneath it, leaving `CONFLICTING` with a
   full set of passed checks. The probe there is report-only — the
   `ci_completed` event, its status, and the `CI_COMPLETED` message are
   unchanged, and `PR_CONFLICT` simply rides alongside so the secretary
   does not walk into a merge GitHub will refuse. It runs only against a
   head the verdict actually describes (after the phase's head-stability
   check, and pinned to that head), so a branch advancing mid-probe can
   never spend the new head's one-shot announcement on a claim about the
   old head. Because this can be the watcher's LAST observation (without
   `--merge-watch` it exits straight after), an `UNKNOWN` answer here is
   re-probed a couple of times; `MERGEABLE` and an unreadable answer
   return at once, so a healthy watch pays nothing.

Only a *confirmed* `CONFLICTING` changes the watcher's control flow, so
neither an unreadable probe nor a gh outage can wedge the watch.

`pr_watch_pane_started` is a best-effort audit row written by the
`/pr-watch-pane` skill (secretary) when it spawns a CI/merge-watch pane
(`name="pr-watch-<PR>"`, `role="watcher"`) running `tools/pr-watch.sh`.
It records that the watcher pane was launched; the actual CI verdict
still arrives later as `ci_completed` from `tools/pr_watch.py` inside
that pane. **This row used to be best-effort audit only** — nothing
read it back. [`tools/watcher_restart_guard.py`](../tools/watcher_restart_guard.py)
(Refs #978) makes it load-bearing: omitting the row, or a broker
transport that fails to record it, makes the guard report `missing`
even when a watcher pane genuinely exists on screen.

`fix_pushed`、`pr_watch_pane_started`、および上記の terminal 系イベント
（`ci_completed` / `pr_merged` / `pr_merged_no_run` /
`pr_merged_head_unconfirmed` / `pr_merge_watch_timeout` /
`pr_watch_aborted`）は、単体の監査ログとしてだけでなく、
`tools/watcher_restart_guard.py` によって `events` テーブルの時系列順に
読まれ、「最後の push より後に開始された watcher があるか」という
1 つの predicate へ合成される。判定基準は「`pr-watch-<N>` という名前の
pane が存在するか」では**ない**（herdr / renga transport では監視終了
後も pane が自己 close せず、終わった watch と生きている watch が外見
上区別できない — 2026-08-29 のインシデントはまさにこの誤判定で発生
した）。判定基準は常に「baseline push（対象 task の最新 `fix_pushed`）
より後の `(occurred_at, id)` を持つ `pr_watch_pane_started` があるか」
であり、それ以降に terminal イベントが来ている場合は続けて「その watch
は**答えを出して**終わったのか、単に**止まった**のか」を見る。

- `ci_completed` の `status` が `passed` / `failed` のときだけ「CI が答え
  を出した」と扱う。`incomplete`（checks が pending のまま retry budget
  切れ）/ `indeterminate`（probe を一度も読めなかった。この行は
  `retry_recommended` を伴う ＝ watcher 自身が再起動を要求している）/
  `canceled` / `status` 未記録、および `pr_watch_aborted` /
  `pr_merge_watch_timeout` は**答えのない終端**であり、現 head は無監視
  なので `ended_inconclusive` で trip する。live DB の `ci_completed` の
  約 7% がこの経路で終わっており、机上の分岐ではない。
- `head` は**降格材料であって必須条件ではない**。答えが出た watch につい
  て、baseline の sha と terminal の `head` が**両方読めて食い違うとき
  だけ** `ended_stale_head`（前 push 向けの判定＝現 head は無監視）に降格
  する。どちらかが欠けている場合は比較不能として `completed` に倒し、
  欠落は出力に残す。sha が読めないことを trip 条件にすると健全な watch で
  誤報が出て、「読み飛ばす習慣」というインシデントの原因そのものを再生産
  するため。

**watch はその起動が記録される前に終わりうる**: `/pr-watch-pane` はペインを
spawn し、起動確認を済ませてから `pr_watch_pane_started` を書くため、CI が
既に red の場合 watch は確認ステップの最中に `ci_completed` を出して終了し、
terminal イベントが自分の起動行より**前**に並ぶ。watcher の時刻だけを起点に
すると「起動行より後の terminal は無い ＝ live」と永久に答えることになる（ガード
自身が同じ誤りを一段下で再現する）。そこで watcher と terminal を時系列で
**貪欲にペアリング**する: 古い watcher から順に「自分の起動時刻以降で、まだ
誰にも取られていない最初の terminal」を 1 つ取り、baseline 以降で最後まで
取られなかった terminal を最新 watcher に帰属させる。貪欲であることが両方向の
誤りを同時に防ぐ — 前の watcher が terminal を「所有」できるのは**まだ消費して
いない場合だけ**なので、`push → watcher → indeterminate → 再起動`（`indeterminate`
への正規の対応手順）は `live` のまま、`watcher1 → failed → push →
watcher2 の terminal → watcher2 の起動行`は「watcher2 は終了済み」と正しく読む。
この帰属が働いたときは出力の `terminal_precedes_watcher_row` で可視化される。

なお terminal 側の repo 欠落行は「watch が終わった証拠」としては採用するが
**結論（非 trip の verdict）には使えない**（PR 番号は repo 間で衝突するので、
別 repo の同番号 PR の `pr_merged` / legacy `ci_completed` がこちらを
「監視済み」と証明してしまう）。この場合は `ended_inconclusive` になる。
live DB の `pr_merged` 363 行中 45 行が repo を持たないため、実在する行形である。

**既知の限界**: terminal 系イベントは**どのペインが書いたか**を持たない
（`--merge-watch` では 1 回の `pr_watch` が `ci_completed` と後続の
`pr_merged` / `pr_merge_watch_timeout` を書くので、1 watcher : 1 terminal
でもない）。このため旧ペインが残ったまま新 watcher を立てた状況では、旧ペイン
発の遅れて来た行と新 watcher 自身の行を区別できず、新しい watch を「終了した」
と読む。これは誤検知（余分な `/pr-watch-pane` 再起動が 1 回増えるだけで、
無監視の PR を見逃す方向には倒れない）として受け入れている。厳密化するには
terminal イベント側に pane 識別子を載せる必要があり、それは本ツールの範囲外。

baseline の sha は `fix_pushed` payload の `commit` / `head` / `sha` の
順で読む（catalog 上の名は `commit` だが、実際の emitter が書くのは
`head` である点は下記 `fix_pushed` 行の注記を参照）。詳細な非対称マッチ
ング規則（repo 欠落行の扱いが `pr_watch_pane_started` 側と terminal 側
で逆になる理由を含む）はツール本体の module docstring が一次情報源で
ある。

`tools/journal_append.py` は `fix_pushed` の DB commit 成功後、同じ
コネクション上でこの predicate を非致命的な post-check として実行し、
`stderr` に結果を出す（`journal_append` 自身の exit code は変えない
— 例外は握りつぶす）。push 直後は定義上まだ watcher が再起動されて
いないため、判定は `stale` / `missing` になるのが通常であり、これは
異常ではなく「次に必ずやるべき動作（`/pr-watch-pane` の再起動）」の
リマインドとして出力される。`$ORG_WATCHER_GUARD=off`（`0` / `false`
も可、大小文字無視）または非公開 CLI フラグ `--no-watcher-guard` で
抑止できる（既定は有効）。

`tools/watcher_restart_guard.py check --pr <N> --repo <owner/name>` /
`check --task <task_id>` は同じ predicate を単発 CLI として提供し、
exit code は `0`=`live`/`completed`（監視は説明がつく）、`3`=`stale`/
`missing`/`ended_stale_head`/`ended_inconclusive`（watcher の再起動が
必要 — 2026-08-29 のインシデントは `stale`）、`2`=`unresolved`（対象
task から PR が特定できない）、`1`=usage / DB / schema エラー。**両形式は
同じ baseline を見る**: `--pr` 形式は当該 PR を持つ run 行から task_id を
逆引きして `fix_pushed` baseline を復元する（復元しないと `--pr` 形式は
baseline を一切読まず、`stale` 分岐に到達できないまま exit 0 を返す ＝
インシデントそのものを見逃す）。`audit` サブコマンドは `pr_state` が非
terminal な run を全件評価し、1 件でも trip した verdict があれば exit 3、
trip は無いが `pr_url` を読めず**評価できなかった** run がある場合は exit 2
を返す（評価できなかった open PR を exit 0 に混ぜると「全件確認済み」と読めて
しまい、本ツールが消そうとしている silent miss そのものになる）。

### Session lifecycle

| Event                          | Typical fields                          | Writer    | Emitted by | Required for |
|--------------------------------|-----------------------------------------|-----------|------------|--------------|
| `suspend`                      | `reason`, `active_workers[]`, `pending_items[]` | secretary | secretary  | —            |
| `resume`                       | `restored_workers[]`, `note`            | secretary | secretary  | —            |
| `task_completed`               | `task`                                  | secretary | secretary  | —            |
| `secretary_identity_restored`  | `note`                                  | org-start | secretary  | —            |
| `dispatcher_handover`          | `active_workers`, `pending_decisions`, `note` | dispatcher | dispatcher | —      |
| `dispatcher_resumed`           | `pane_id`, `peer_id`, `active_workers`, `note` | dispatcher | dispatcher | —     |

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
5. If the dispatcher relay must deliver the event to the secretary (a
   terminal signal, or any kind that needs the same zero-miss
   guarantee), register the kind in `TERMINAL_KINDS` in
   `tools/relay_scan.py`. Kinds missing from that tuple are never
   relayed — the relay scan skips them and the row sits in state.db as
   the only trace (Issue #946: `pr_conflict_detected` needed exactly
   this registration to reach the secretary).

Do **not** hand-craft direct DB inserts (`sqlite3 .state/state.db
"INSERT INTO events ..."`) or the legacy `printf '%s\n' '{...}' >>
.state/journal.jsonl` pattern — the helper handles timestamp
generation, JSON escaping, schema validation, and reserved-key
checking. Direct INSERTs bypass these checks and direct jsonl writes
go to a retired sink (M4).
